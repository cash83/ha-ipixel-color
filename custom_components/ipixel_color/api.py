"""iPIXEL Color Bluetooth API client - Refactored version."""
from __future__ import annotations

import base64
import logging
import time
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

from .bluetooth.client import BluetoothClient
from .device.commands import (
    make_power_command,
    make_brightness_command,
)
from .device.clock import make_clock_mode_command, make_time_command
from .device.text import make_text_command
from .device.image import make_image_command
from .device.info import build_device_info_command, parse_device_response
from .display.text_renderer import render_text_to_png
from .exceptions import iPIXELError, iPIXELConnectionError, iPIXELTimeoutError

_LOGGER = logging.getLogger(__name__)

# Molte GIF scaricate da internet (Giphy/Tenor) risultano "truncated"
# per PIL pur essendo visualizzabili: tolleriamole.
try:
    from PIL import ImageFile as _PILImageFile

    _PILImageFile.LOAD_TRUNCATED_IMAGES = True
except ImportError:  # pragma: no cover
    pass

# La toppa che rimpicciolisce le emoji nei blocchi 16x16 vive in
# device/text.py (EMOJI_OPTS), insieme all'assemblatore dei comandi.

# Default values used only while the device has not answered the
# info query yet. They are NOT cached: the query is retried on the
# next call until the device answers.
_FALLBACK_DEVICE_INFO: dict[str, Any] = {
    "width": 64,
    "height": 16,
    "device_type": 0,
    "device_type_str": "Unknown",
    "led_type": 0,
    "mcu_version": "Unknown",
    "wifi_version": "Unknown",
    "has_wifi": False,
    "password_flag": 255,
}

_FALLBACK_FONT = "7x5.ttf"


def _resolve_font(font_name: str | None) -> str:
    """Map a font entity value to a real font file path.

    Falls back to a bundled pixel font when the requested font does not
    exist (e.g. firmware names like 'CUSONG' that have no local file).
    """
    fonts_dir = Path(__file__).parent / "fonts"
    if font_name:
        candidate = fonts_dir / font_name
        if candidate.exists():
            return str(candidate)
        # Alcuni font (es. unifont.otf) vivono nella cartella di
        # pypixelcolor, non nella nostra
        try:
            import pypixelcolor

            pp_candidate = Path(pypixelcolor.__file__).parent / "fonts" / font_name
            if pp_candidate.exists():
                return str(pp_candidate)
        except ImportError:
            pass
        # Font names coming from the device firmware (CUSONG, SIMSUN,
        # VCR_OSD_MONO) are understood by pypixelcolor directly.
        if font_name.upper() in ("CUSONG", "SIMSUN", "VCR_OSD_MONO"):
            return font_name.upper()
        _LOGGER.warning(
            "Font '%s' not found, falling back to %s", font_name, _FALLBACK_FONT
        )
    return str(fonts_dir / _FALLBACK_FONT)


class iPIXELAPI:
    """iPIXEL Color device API client - simplified facade."""

    def __init__(self, hass: HomeAssistant, address: str) -> None:
        self._address = address
        self._bluetooth = BluetoothClient(hass, address)
        self._power_state = False
        self._device_info: dict[str, Any] | None = None
        self._size_override: tuple[int, int] | None = None
        # Ultima cosa davvero mandata al pannello: l'anteprima del
        # pannello web la usa per rispecchiare il display fisico.
        self._last_media: dict[str, Any] | None = None

    @property
    def last_media(self) -> dict[str, Any] | None:
        """L'ultimo contenuto inviato al display (immagine/testo/clock)."""
        return self._last_media

    def set_size_override(self, width: int | None, height: int | None) -> None:
        """Force panel dimensions (some firmwares report wrong ones)."""
        if width and height:
            self._size_override = (int(width), int(height))
        else:
            self._size_override = None

    def _apply_size(self, info: dict[str, Any]) -> dict[str, Any]:
        if self._size_override:
            info = dict(info)
            info["width"], info["height"] = self._size_override
        return info

    async def connect(self) -> bool:
        """Connect to the iPIXEL device."""
        return await self._bluetooth.connect(self._notification_handler)

    async def disconnect(self) -> None:
        """Disconnect from the device."""
        await self._bluetooth.disconnect()

    async def set_power(self, on: bool) -> bool:
        """Set device power state."""
        command = make_power_command(on)
        success = await self._bluetooth.send_command(command)

        if success:
            self._power_state = on
            _LOGGER.debug("Power set to %s", "ON" if on else "OFF")
        return success

    async def set_brightness(self, brightness: int) -> bool:
        """Set device brightness level (1-100)."""
        try:
            command = make_brightness_command(brightness)
            success = await self._bluetooth.send_command(command)

            if success:
                _LOGGER.debug("Brightness set to %d", brightness)
            else:
                _LOGGER.error("Failed to set brightness to %d", brightness)
            return success

        except ValueError as err:
            _LOGGER.error("Invalid brightness value: %s", err)
            return False
        except Exception as err:
            _LOGGER.error("Error setting brightness: %s", err)
            return False

    async def release_for_app(self, seconds: int = 600) -> None:
        """Libera il BLE cosi' l'app del telefono puo' collegarsi."""
        await self._bluetooth.release_for_app(seconds)

    def end_app_release(self) -> None:
        """Riprende il collegamento: il prossimo comando riaggancia."""
        self._bluetooth.end_release()

    @property
    def released_seconds_left(self) -> int:
        """Secondi rimasti di collegamento liberato per l'app."""
        return self._bluetooth.released_seconds_left

    async def sync_time(self) -> bool:
        """Sync current time to the device."""
        try:
            time_command = make_time_command()
            success = await self._bluetooth.send_command(time_command)

            if success:
                _LOGGER.debug("Time synchronized to device")
                # L'opcode 0x8001 ACCENDE il pannello come effetto
                # collaterale: se era spento, rispegnilo subito
                if not self._power_state:
                    await self.set_power(False)
                    self._power_state = False
            else:
                _LOGGER.error("Failed to sync time")
            return success

        except Exception as err:
            _LOGGER.error("Error syncing time: %s", err)
            return False

    async def set_clock_mode(
        self,
        style: int = 1,
        date: str = "",
        show_date: bool = True,
        format_24: bool = True,
    ) -> bool:
        """Set device to clock display mode."""
        try:
            command = make_clock_mode_command(style, date, show_date, format_24)
            success = await self._bluetooth.send_command(command)

            if not success:
                _LOGGER.error("Failed to set clock mode")
                return False

            _LOGGER.info(
                "Clock mode set: style=%d, 24h=%s, show_date=%s",
                style,
                format_24,
                show_date,
            )

            if not await self.sync_time():
                _LOGGER.warning("Clock mode set but time sync failed")

            self._last_media = {"kind": "clock", "ts": time.time()}
            return success

        except ValueError as err:
            _LOGGER.error("Invalid clock mode parameters: %s", err)
            return False
        except Exception as err:
            _LOGGER.error("Error setting clock mode: %s", err)
            return False

    async def get_device_info(self) -> dict[str, Any]:
        """Query device information.

        The result is cached only on success; on failure, fallback
        defaults are returned for this call and the query is retried
        the next time.
        """
        if self._device_info is not None:
            return self._apply_size(self._device_info)

        try:
            command = build_device_info_command()
            response = await self._bluetooth.request(command, response_timeout=5.0)

            if not response:
                raise iPIXELConnectionError("No response to device info query")

            info = parse_device_response(response)
            self._device_info = info
            _LOGGER.info("Device info retrieved: %s", info)
            return self._apply_size(info)

        except Exception as err:
            _LOGGER.warning(
                "Failed to get device info (%s); using defaults until the "
                "device answers",
                err,
            )
            return self._apply_size(dict(_FALLBACK_DEVICE_INFO))

    @property
    def device_info_cached(self) -> dict[str, Any] | None:
        """Device info if already retrieved, else None."""
        return self._device_info

    def invalidate_device_info(self) -> None:
        """Force a re-query of device info on next access."""
        self._device_info = None

    async def display_text(
        self,
        text: str,
        antialias: bool = True,
        font_size: float | None = None,
        font: str | None = None,
        line_spacing: int = 0,
        text_color: str = "ffffff",
        bg_color: str = "000000",
    ) -> bool:
        """Display text rendered as an image (textimage mode)."""
        try:
            device_info = await self.get_device_info()
            width = device_info["width"]
            height = device_info["height"]

            png_data = render_text_to_png(
                text, width, height, antialias, font_size, font,
                line_spacing, text_color, bg_color,
            )

            commands = make_image_command(
                image_bytes=png_data,
                file_extension=".png",
                resize_method="crop",
                device_info_dict=device_info,
            )

            for i, command in enumerate(commands):
                _LOGGER.debug(
                    "Sending image frame %d/%d: %d bytes",
                    i + 1, len(commands), len(command),
                )
                if not await self._bluetooth.send_command(command):
                    _LOGGER.error(
                        "Failed to send image frame %d/%d", i + 1, len(commands)
                    )
                    return False

            _LOGGER.info(
                "Text rendered as image: '%s' (%dx%d, %d bytes PNG, %d frames)",
                text, width, height, len(png_data), len(commands),
            )
            return True

        except Exception as err:
            _LOGGER.error("Error displaying text: %s", err)
            return False

    async def display_image(
        self,
        image_bytes: bytes,
        file_extension: str = ".png",
        resize_method: str = "crop",
        save_slot: int = 0,
    ) -> bool:
        """Display a raw image (PNG/JPEG/GIF) on the device.

        save_slot >= 1: oltre a mostrarla, la salva nella memoria del
        display (richiamabile poi con show_slot, quasi zero BLE).
        """
        try:
            device_info = await self.get_device_info()

            commands = make_image_command(
                image_bytes=image_bytes,
                file_extension=file_extension,
                resize_method=resize_method,
                device_info_dict=device_info,
                save_slot=save_slot,
            )

            for i, command in enumerate(commands):
                _LOGGER.debug(
                    "Sending image frame %d/%d: %d bytes",
                    i + 1, len(commands), len(command),
                )
                if not await self._bluetooth.send_command(command):
                    _LOGGER.error(
                        "Failed to send image frame %d/%d", i + 1, len(commands)
                    )
                    return False

            _LOGGER.info(
                "Image sent to display (%d bytes, %d frames)",
                len(image_bytes), len(commands),
            )
            self._last_media = {
                "kind": "image",
                "b64": base64.b64encode(image_bytes).decode(),
                "ext": file_extension.lstrip(".").lower() or "png",
                "ts": time.time(),
            }
            return True

        except Exception as err:
            _LOGGER.error("Error displaying image: %s", err)
            return False

    async def display_text_pypixelcolor(
        self,
        text: str,
        color: str = "ffffff",
        bg_color: str | None = None,
        font: str | None = None,
        animation: int = 0,
        speed: int = 80,
        rainbow_mode: int = 0,
        char_height: int | None = None,
    ) -> bool:
        """Display text using the device's native text mode.

        char_height controls the glyph rendering height: None uses the
        panel height; larger values stretch the glyphs (cropping the
        font's own margins), smaller values shrink them.
        """
        try:
            # I selettori di variante (FE0F) e i legami (ZWJ) delle emoji
            # sono codici invisibili: lasciati nel testo finiscono nel
            # font, che non li ha, e sul display compare un "?"
            text = text.replace("️", "").replace("‍", "")

            device_info = await self.get_device_info()
            # Il protocollo vuole blocchi ad altezza FISSA (16 sui
            # pannelli fino a 20 px): un char_height diverso produce
            # payload sballati. La "Grandezza" si ottiene disegnando i
            # glifi piu' piccoli DENTRO il blocco, centrati; le emoji
            # idem (vedi _emoji_image_scaled in cima al file).
            panel_height = device_info["height"]
            block_height = 16 if panel_height <= 20 else panel_height
            render_px = None
            if char_height:
                render_px = max(5, min(int(char_height), panel_height))
                if render_px >= block_height:
                    render_px = None  # piena altezza: niente da scalare

            # Animazioni 3/4 (scorri su/giu'): su pannelli non 32x32 il
            # firmware salva un payload che manda il display in BOOTLOOP
            # (documentato da ahzs645 e bloccato da tutti gli upstream).
            if animation in (3, 4) and (
                device_info["width"], device_info["height"]
            ) != (32, 32):
                _LOGGER.warning(
                    "Animazione %d vietata su %dx%d (bootloop): uso 0 (fisso)",
                    animation, device_info["width"], device_info["height"],
                )
                animation = 0

            resolved_font = _resolve_font(font)

            # Tutto in executor: la costruzione del FontConfig legge i
            # JSON dei font, il rendering apre i TTF e il PRIMO uso di
            # un'emoji la scarica da Twemoji (poi resta in cache)
            import asyncio as _asyncio

            from .device.text import EMOJI_OPTS

            def _build() -> list[bytes]:
                font_arg = resolved_font
                px = render_px
                if px:
                    try:
                        from pypixelcolor.commands.send_text.font_utils import (
                            resolve_font_config,
                        )
                        from pypixelcolor.lib.font_config import FontConfig

                        base_cfg = resolve_font_config(resolved_font)
                        m = dict(base_cfg.get_metrics(block_height))
                        ratio = px / block_height
                        scaled = {
                            "font_size": max(5, round(m["font_size"] * ratio)),
                            "offset": (
                                m["offset"][0],
                                round(m["offset"][1] * ratio)
                                + (block_height - px) // 2,
                            ),
                            "pixel_threshold": m["pixel_threshold"],
                            "var_width": m.get("var_width", False),
                        }
                        font_arg = FontConfig(
                            name=base_cfg.name,
                            path=base_cfg.path,
                            metrics={block_height: scaled},
                        )
                    except ImportError:
                        px = None
                EMOJI_OPTS["px"] = px  # None = emoji a misura piena
                try:
                    return make_text_command(
                        text=text,
                        color=color,
                        bg_color=bg_color,
                        font=font_arg,
                        animation=animation,
                        speed=speed,
                        rainbow_mode=rainbow_mode,
                        save_slot=0,
                        device_height=block_height,
                    )
                finally:
                    EMOJI_OPTS["px"] = None

            commands = await _asyncio.get_running_loop().run_in_executor(
                None, _build
            )

            for i, command in enumerate(commands):
                _LOGGER.debug(
                    "Sending text frame %d/%d: %d bytes",
                    i + 1, len(commands), len(command),
                )
                if not await self._bluetooth.send_command(command):
                    _LOGGER.error(
                        "Failed to send text frame %d/%d", i + 1, len(commands)
                    )
                    return False

            _LOGGER.info(
                "Text sent: '%s' (color=%s, bg=%s, font=%s, anim=%d, speed=%d, frames=%d)",
                text, color, bg_color or "none", resolved_font,
                animation, speed, len(commands),
            )
            self._last_media = {
                "kind": "text",
                "text": text,
                "color": color,
                "scroll": animation in (1, 2),
                "ts": time.time(),
            }
            return True

        except Exception as err:
            _LOGGER.error("Error displaying pypixelcolor text: %s", err)
            return False

    async def display_text_segments(
        self,
        segments: list[dict[str, Any]],
        font: str | None = None,
        bg_color: str | None = None,
        animation: int = 1,
        speed: int = 80,
        rainbow_mode: int = 0,
    ) -> bool:
        """Testo scorrevole con grandezza/posizione per segmento (casella).

        Ogni segmento: {"text", "color", "size" (px), "valign"}.
        """
        try:
            for seg in segments:
                if seg.get("text"):
                    seg["text"] = (
                        str(seg["text"]).replace("️", "").replace("‍", "")
                    )
            device_info = await self.get_device_info()
            panel_height = device_info["height"]
            block_height = 16 if panel_height <= 20 else panel_height

            if animation in (3, 4) and (
                device_info["width"], device_info["height"]
            ) != (32, 32):
                _LOGGER.warning(
                    "Animazione %d vietata su %dx%d (bootloop): uso 1",
                    animation, device_info["width"], device_info["height"],
                )
                animation = 1

            from .device.text import make_text_command_mixed

            resolved_font = _resolve_font(font)

            import asyncio as _asyncio
            import functools as _functools

            commands = await _asyncio.get_running_loop().run_in_executor(
                None,
                _functools.partial(
                    make_text_command_mixed,
                    segments,
                    font=resolved_font,
                    bg_color=bg_color,
                    animation=animation,
                    speed=speed,
                    rainbow_mode=rainbow_mode,
                    save_slot=0,
                    block_height=block_height,
                ),
            )

            for i, command in enumerate(commands):
                if not await self._bluetooth.send_command(command):
                    _LOGGER.error(
                        "Failed to send mixed text frame %d/%d",
                        i + 1, len(commands),
                    )
                    return False

            joined = "  ".join(
                str(s.get("text") or "") for s in segments if s.get("text")
            )
            _LOGGER.info(
                "Mixed text sent: '%s' (%d segmenti, anim=%d, speed=%d)",
                joined, len(segments), animation, speed,
            )
            self._last_media = {
                "kind": "text",
                "text": joined,
                "color": str(segments[0].get("color") or "ffffff") if segments else "ffffff",
                "scroll": animation in (1, 2),
                "ts": time.time(),
            }
            return True

        except Exception as err:
            _LOGGER.error("Error displaying mixed text: %s", err)
            return False

    # ----- comandi nativi del firmware (device/native.py + pypixelcolor) -----

    async def native_timer(self, action: int) -> bool:
        """Cronometro DI BORDO: 0=stop, 1=start, 2=pausa. Conta da solo."""
        from .device.native import make_native_timer_command

        ok = await self._bluetooth.send_command(make_native_timer_command(action))
        if ok:
            self._last_media = {"kind": "native_timer", "ts": time.time()}
        return ok

    async def native_scores(self, p1: int, p2: int) -> bool:
        """Tabellone segnapunti di bordo (0-99)."""
        from .device.native import make_scores_command

        ok = await self._bluetooth.send_command(make_scores_command(p1, p2))
        if ok:
            self._last_media = {
                "kind": "text", "text": f"{p1:02d} : {p2:02d}",
                "scroll": False, "ts": time.time(),
            }
        return ok

    async def native_schedule(
        self, on: bool, slot: int, mask: int, hour: int, minute: int
    ) -> bool:
        """Accensione/spegnimento programmati nel firmware del display."""
        from .device.native import make_schedule_command

        return await self._bluetooth.send_command(
            make_schedule_command(on, slot, mask, hour, minute)
        )

    async def set_fun_mode(self, enable: bool) -> bool:
        """Modo disegno-diretto del firmware (per set_pixel)."""
        from pypixelcolor.commands.set_fun_mode import set_fun_mode as _sfm

        plan = _sfm(enable)
        return await self._bluetooth.send_command(plan.windows[0].data)

    async def set_pixel(self, x: int, y: int, color: str) -> bool:
        """Accende UN pixel (fun mode attivo). Senza conferma: veloce."""
        from pypixelcolor.commands.set_fun_mode import set_pixel as _sp

        plan = _sp(int(x), int(y), color.lstrip("#"))
        return await self._bluetooth.send_command(plan.windows[0].data)

    async def set_rhythm(self, style: int, levels: list[int]) -> bool:
        """Equalizzatore di bordo: 11 barre (0-15), stile 0-4."""
        from pypixelcolor.commands.set_rhythm_mode import set_rhythm_mode as _srm

        lv = (list(levels) + [0] * 11)[:11]
        plan = _srm(int(style), *lv)
        return await self._bluetooth.send_command(plan.windows[0].data)

    async def show_slot(self, number: int) -> bool:
        """Richiama uno slot salvato nella memoria del display."""
        from pypixelcolor.commands.show_slot import show_slot as _ss

        ok = await self._bluetooth.send_command(_ss(int(number)).windows[0].data)
        if ok:
            self._last_media = {"kind": "slot", "slot": int(number),
                                "ts": time.time()}
        return ok

    async def delete_slot(self, number: int) -> bool:
        """Svuota uno slot della memoria del display."""
        from pypixelcolor.commands.delete import delete as _del

        return await self._bluetooth.send_command(
            _del(int(number)).windows[0].data
        )

    def _notification_handler(self, sender: Any, data: bytearray) -> None:
        """Handle unsolicited notifications from the device."""
        _LOGGER.debug("Notification from %s: %s", sender, data.hex())

    @property
    def is_connected(self) -> bool:
        """Return True if connected to device."""
        return self._bluetooth.is_connected

    @property
    def power_state(self) -> bool:
        """Return current power state."""
        return self._power_state

    @property
    def address(self) -> str:
        """Return device address."""
        return self._address

    @property
    def mtu_size(self) -> int | None:
        """Negotiated BLE MTU, when known."""
        return self._bluetooth.mtu_size


# Export at module level for convenience
__all__ = ["iPIXELAPI", "iPIXELError", "iPIXELConnectionError", "iPIXELTimeoutError"]
