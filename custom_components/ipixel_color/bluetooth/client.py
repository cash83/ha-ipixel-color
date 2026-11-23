"""Bluetooth client management for iPIXEL Color devices.

Rewritten for reliability:
- Notifications are started ONCE at connect time with a dispatcher,
  instead of being stopped/restarted around every command (which left
  stale notify sessions behind, especially via ESPHome BLE proxies).
- Payloads are written in MTU-sized chunks with response, so large
  frames (text/images, 1-3 kB) survive the trip through a BLE proxy.
- A command is only considered successful when the device ACKs it
  (configurable, since a few commands never answer).
- Automatic reconnection when the link dropped in the meantime.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, TYPE_CHECKING

from bleak.exc import BleakError
from bleak_retry_connector import (
    BleakClientWithServiceCache,
    establish_connection,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

from homeassistant.components import bluetooth

from ..const import WRITE_UUID, NOTIFY_UUID, DOMAIN
from ..exceptions import iPIXELConnectionError

_LOGGER = logging.getLogger(__name__)


def scanner_label(scanner: Any) -> str:
    """Nome leggibile di un adattatore/proxy BLE (per il select e il sensore)."""
    return (
        getattr(scanner, "name", None)
        or getattr(scanner, "source", None)
        or "?"
    )

# Fallback chunk payload when the MTU is unknown (BLE minimum is 23).
_MIN_CHUNK = 20
# Never exceed this per write even when the MTU allows more; keeps the
# ESPHome proxy write queue happy.
_MAX_CHUNK = 244


class BluetoothClient:
    """Manages Bluetooth connection and communication."""

    def __init__(self, hass: HomeAssistant, address: str) -> None:
        self._hass = hass
        self._address = address
        self._client: BleakClientWithServiceCache | None = None
        self._connected = False
        self._notification_handler: Callable | None = None
        self._response_event: asyncio.Event = asyncio.Event()
        self._last_response: bytes | None = None
        self._lock = asyncio.Lock()
        # "Liberato per l'app": finche' il momento non passa, niente
        # connessioni ne' comandi, cosi' l'app del telefono puo' agganciarsi
        self._released_until: float = 0.0

    @property
    def is_released(self) -> bool:
        """True while the link is intentionally left free for the phone app."""
        return self._released_until > self._hass.loop.time()

    @property
    def released_seconds_left(self) -> int:
        """Seconds before the link is taken back automatically."""
        return max(0, int(self._released_until - self._hass.loop.time()))

    async def release_for_app(self, seconds: int) -> None:
        """Drop the link and keep it free so the phone app can connect."""
        self._released_until = self._hass.loop.time() + seconds
        await self.disconnect()
        _LOGGER.info(
            "Collegamento liberato per l'app per %d secondi", seconds
        )

    def end_release(self) -> None:
        """Take the link back: the next command reconnects by itself."""
        self._released_until = 0.0

    # ------------------------------------------------------------------
    # Connection handling
    # ------------------------------------------------------------------

    def _disconnected_callback(self, client: BleakClientWithServiceCache) -> None:
        """Called when device disconnects."""
        # Un client rimpiazzato da una riconnessione puo' segnalare il suo
        # disconnect in ritardo: non deve marcare DOWN la connessione nuova
        if self._client is not None and client is not self._client:
            _LOGGER.debug("Ignoro il disconnect di un client gia' rimpiazzato")
            return
        _LOGGER.warning("iPIXEL device %s disconnected", self._address)
        self._connected = False

    def _dispatch_notification(self, sender: Any, data: bytearray) -> None:
        """Single permanent notification callback.

        Stores the payload for whoever is waiting for a response and
        forwards it to the externally registered handler.
        """
        self._last_response = bytes(data)
        self._response_event.set()
        _LOGGER.debug("Notification from %s: %s", sender, self._last_response.hex())
        if self._notification_handler:
            try:
                self._notification_handler(sender, data)
            except Exception:  # pragma: no cover - defensive
                _LOGGER.exception("Error in notification handler")

    async def connect(self, notification_handler: Callable[[Any, bytearray], None]) -> bool:
        """Connect to the iPIXEL device and start notifications once."""
        if self.is_released:
            raise iPIXELConnectionError(
                "Collegamento liberato per l'app del telefono: premi "
                "Ricollega (o aspetta la scadenza) per riprenderlo"
            )
        _LOGGER.debug("Connecting to iPIXEL device at %s", self._address)
        self._notification_handler = notification_handler

        try:
            ble_device = None
            # Adattatore scelto dall'utente (select "Adattatore BLE"):
            # se impostato, forza il collegamento via QUELLO (es. il
            # Bluetooth del box invece di un proxy ESPHome che perde i
            # pacchetti grandi). Se non è più in vista, ripiega su auto.
            pref = (
                self._hass.data.get(DOMAIN, {})
                .get("pref_adapter", {})
                .get(self._address)
            )
            if pref:
                try:
                    for d in bluetooth.async_scanner_devices_by_address(
                        self._hass, self._address, connectable=True
                    ):
                        if scanner_label(d.scanner) == pref:
                            ble_device = d.ble_device
                            _LOGGER.info(
                                "Collegamento forzato via adattatore '%s'", pref
                            )
                            break
                    else:
                        _LOGGER.warning(
                            "Adattatore scelto '%s' non in vista: uso automatico",
                            pref,
                        )
                except Exception as err:  # pragma: no cover - best effort
                    _LOGGER.debug("Scelta adattatore fallita (%s): auto", err)

            if ble_device is None:
                ble_device = bluetooth.async_ble_device_from_address(
                    self._hass, self._address, connectable=True
                )

            if not ble_device:
                raise iPIXELConnectionError(
                    f"Device {self._address} not found. "
                    "Ensure the device is powered on and in range."
                )

            # Chiudi un client stantio prima di crearne uno nuovo: un
            # handle BLE lasciato appeso puo' esaurire gli slot del box
            if self._client is not None:
                stale, self._client = self._client, None
                try:
                    await stale.disconnect()
                except Exception:  # pragma: no cover - best effort
                    pass

            self._client = await establish_connection(
                BleakClientWithServiceCache,
                ble_device,
                ble_device.name or "iPIXEL Display",
                disconnected_callback=self._disconnected_callback,
                max_attempts=3,
            )

            self._connected = True

            # Start notifications exactly once per connection. If a stale
            # session is still around (e.g. after an unclean disconnect
            # through a proxy), stop it first and retry.
            # bluez use_start_notify: da bleak 2.x su BlueZ il default
            # AcquireNotify non e' supportato dal firmware iPIXEL
            # (NotPermitted/UnknownObject e riconnessioni continue);
            # sui bleak piu' vecchi la chiave viene ignorata
            async def _start_notify() -> None:
                try:
                    await self._client.start_notify(
                        NOTIFY_UUID, self._dispatch_notification,
                        bluez={"use_start_notify": True},
                    )
                except TypeError:
                    # bleak troppo vecchio per il parametro: va bene cosi'
                    await self._client.start_notify(
                        NOTIFY_UUID, self._dispatch_notification
                    )

            try:
                await _start_notify()
            except BleakError as err:
                _LOGGER.debug("start_notify failed (%s), retrying after stop_notify", err)
                try:
                    await self._client.stop_notify(NOTIFY_UUID)
                except BleakError:
                    pass
                await _start_notify()

            _LOGGER.info(
                "Successfully connected to iPIXEL device (MTU %s)",
                getattr(self._client, "mtu_size", "unknown"),
            )
            return True

        except BleakError as err:
            _LOGGER.error("Failed to connect to %s: %s", self._address, err)
            raise iPIXELConnectionError(f"Connection failed: {err}") from err
        except iPIXELConnectionError:
            raise
        except Exception as err:
            _LOGGER.error("Unexpected error connecting to %s: %s", self._address, err)
            raise iPIXELConnectionError(f"Connection failed: {err}") from err

    async def disconnect(self) -> None:
        """Disconnect from the device."""
        if self._client and self._connected:
            try:
                await self._client.stop_notify(NOTIFY_UUID)
            except BleakError as err:
                _LOGGER.debug("stop_notify during disconnect: %s", err)
            try:
                await self._client.disconnect()
                _LOGGER.debug("Disconnected from iPIXEL device")
            except BleakError as err:
                _LOGGER.error("Error during disconnect: %s", err)
            finally:
                self._connected = False
                self._client = None

    async def _ensure_connected(self) -> None:
        """Reconnect if the link dropped."""
        if self.is_connected:
            return
        _LOGGER.debug("Link to %s is down, reconnecting", self._address)
        if self._notification_handler is None:
            raise iPIXELConnectionError("Device not connected")
        await self.connect(self._notification_handler)

    # ------------------------------------------------------------------
    # Command sending
    # ------------------------------------------------------------------

    def _chunk_size(self) -> int:
        """Usable payload per GATT write."""
        mtu = getattr(self._client, "mtu_size", None) or 23
        return max(_MIN_CHUNK, min(mtu - 3, _MAX_CHUNK))

    async def send_command(
        self,
        command: bytes,
        expect_response: bool = True,
        response_timeout: float = 5.0,
        return_response: bool = False,
    ) -> bool | tuple[bool, bytes | None]:
        """Send a command, chunked to the MTU, and wait for the ACK.

        Args:
            command: Full command frame to send.
            expect_response: Whether the device is expected to ACK. When
                True, missing ACK makes the command fail.
            response_timeout: How long to wait for the ACK.

        Returns:
            True on success (ACK received, or sent when no ACK expected).
        """
        if self.is_released:
            _LOGGER.debug("Comando saltato: collegamento liberato per l'app")
            return (False, None) if return_response else False
        async with self._lock:
            total = len(command)
            # Frame GRANDE (composizione sensori ~12 KB): parte già in
            # modalità gentile — conferma OGNI pacchetto (l'ATT fa da
            # freno) + pausa tra i pacchetti, perché se gli si sparano i
            # ~50 pezzi a raffica il buffer del firmware del display va in
            # overflow e si pianta. UN SOLO tentativo per i frame grandi:
            # niente seconda raffica da 12 KB (il colpo di grazia a un
            # display già in affanno) — si rimanda al giro dopo. I frame
            # piccoli (orologio) restano veloci: conferma ogni 4, con
            # ripiego lento se il collegamento vacilla.
            big = total > 1500
            attempts = (1,) if big else (4, 1)
            pace = 0.012 if big else 0.0
            for attempt, confirm_every in enumerate(attempts):
                if attempt:
                    # respiro prima di riprovare: non incalzare la radio
                    await asyncio.sleep(0.5)
                try:
                    # Timeout sulla riconnessione: il backoff di
                    # establish_connection, tenuto dentro il lock, può
                    # bloccare ogni invio per minuti se il device è muto.
                    await asyncio.wait_for(self._ensure_connected(), timeout=20.0)
                except (iPIXELConnectionError, asyncio.TimeoutError) as err:
                    _LOGGER.warning(
                        "Riconnessione fallita: %s", err or "timeout"
                    )
                    self._connected = False
                    continue
                if not self._client:
                    continue

                self._response_event.clear()
                self._last_response = None

                chunk = self._chunk_size()
                chunks = [command[o : o + chunk] for o in range(0, total, chunk)]
                _LOGGER.debug(
                    "Sending command: %d bytes, chunks of %d, confirm every %d",
                    total, chunk, confirm_every,
                )
                try:
                    for i, part in enumerate(chunks):
                        confirmed = (
                            i % confirm_every == confirm_every - 1
                            or i == len(chunks) - 1
                        )
                        # Timeout sulla singola scrittura: se la radio/il
                        # proxy si impicca, write_gatt_char NON ritorna e
                        # terrebbe il lock all'infinito (display congelato,
                        # switch ancora "on"). Col timeout diventa un
                        # fallimento recuperabile.
                        await asyncio.wait_for(
                            self._client.write_gatt_char(
                                WRITE_UUID, part, response=confirmed
                            ),
                            timeout=8.0,
                        )
                        # Pacing sui frame grandi: dà al display il tempo
                        # di svuotare il buffer tra un pezzo e l'altro
                        if pace and i != len(chunks) - 1:
                            await asyncio.sleep(pace)
                except (BleakError, asyncio.TimeoutError) as err:
                    _LOGGER.warning(
                        "Invio interrotto (%s)%s",
                        err or "timeout scrittura",
                        ", ritrasmetto in modalità lenta" if confirm_every != 1 else "",
                    )
                    self._connected = False
                    continue

                if not expect_response:
                    return True

                try:
                    await asyncio.wait_for(
                        self._response_event.wait(), timeout=response_timeout
                    )
                except asyncio.TimeoutError:
                    _LOGGER.warning(
                        "Nessuna conferma entro %.1fs (%d byte)%s",
                        response_timeout, total,
                        ", ritrasmetto in modalità lenta" if confirm_every != 1 else "",
                    )
                    self._connected = False
                    continue

                if self._last_response is not None:
                    _LOGGER.debug("Command ACK: %s", self._last_response.hex())
                # La copia della risposta avviene qui, ancora dentro il
                # lock: un altro comando non può sovrascriverla
                if return_response:
                    return True, self._last_response
                return True

            _LOGGER.error(
                "Invio fallito%s (%d byte)",
                "" if big else " anche in modalità lenta", total,
            )
            if return_response:
                return False, None
            return False

    async def request(
        self, command: bytes, response_timeout: float = 5.0
    ) -> bytes | None:
        """Send a command and return the raw response payload (or None)."""
        ok, response = await self.send_command(
            command,
            expect_response=True,
            response_timeout=response_timeout,
            return_response=True,
        )
        return response if ok else None

    @property
    def last_response(self) -> bytes | None:
        """Raw payload of the last notification received."""
        return self._last_response

    @property
    def is_connected(self) -> bool:
        """Return True if connected to device."""
        return bool(self._connected and self._client and self._client.is_connected)

    @property
    def address(self) -> str:
        """Return device address."""
        return self._address

    @property
    def mtu_size(self) -> int | None:
        """Negotiated MTU, when known."""
        return getattr(self._client, "mtu_size", None)
