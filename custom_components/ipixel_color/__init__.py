"""The iPIXEL Color integration."""
from __future__ import annotations

import asyncio
import base64
import functools
import logging
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event, async_call_later
from homeassistant.components import panel_custom, frontend, websocket_api
from homeassistant.components.http import StaticPathConfig, HomeAssistantView

from .api import iPIXELAPI, iPIXELConnectionError, iPIXELTimeoutError
from .composition import OPTION_KEY, composition_entity_ids
from .const import DOMAIN, CONF_ADDRESS, CONF_NAME

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SWITCH,
    Platform.TEXT,
    Platform.SENSOR,
    Platform.SELECT,
    Platform.NUMBER,
    Platform.BUTTON,
    Platform.LIGHT,
]

PANEL_URL_PATH = "ipixel"
PANEL_STATIC_PATH = "/ipixel_color_files"

SERVICE_DISPLAY_IMAGE = "display_image"
SERVICE_DISPLAY_IMAGE_SCHEMA = vol.Schema(
    {
        vol.Optional("address"): cv.string,
        vol.Required("image"): cv.string,
        vol.Optional("resize_method", default="crop"): vol.In(["crop", "fit"]),
        vol.Optional("file_extension", default=".png"): cv.string,
    }
)


def _device_slug(hass: HomeAssistant, address: str) -> str | None:
    """Derive the entity-id slug for a device from its text entity."""
    registry = er.async_get(hass)
    unique_id = f"{address}_text_display"
    for entity_id, entry in registry.entities.items():
        if entry.platform == DOMAIN and entry.unique_id == unique_id:
            object_id = entity_id.split(".", 1)[1]
            if object_id.endswith("_display"):
                return object_id[: -len("_display")]
            return object_id
    return None


async def _async_update_panel(hass: HomeAssistant) -> None:
    """(Re)register the sidebar panel with the current device list."""
    devices = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.entry_id not in hass.data.get(DOMAIN, {}):
            continue
        address = entry.data[CONF_ADDRESS]
        slug = _device_slug(hass, address)
        if not slug:
            continue
        devices.append(
            {
                "name": entry.data.get(CONF_NAME, entry.title),
                "address": address,
                "slug": slug,
            }
        )

    # Remove a previously registered panel before re-adding it
    if PANEL_URL_PATH in hass.data.get("frontend_panels", {}):
        frontend.async_remove_panel(hass, PANEL_URL_PATH)

    if not devices:
        return

    # ?v=<versione>: senza, il browser tiene il JS vecchio in cache
    # anche dopo un deploy e le novità del pannello "non si vedono"
    from homeassistant.loader import async_get_integration

    try:
        integration = await async_get_integration(hass, DOMAIN)
        version = str(integration.version)
    except Exception:  # pragma: no cover
        version = "0"

    await panel_custom.async_register_panel(
        hass,
        webcomponent_name="ipixel-panel",
        frontend_url_path=PANEL_URL_PATH,
        module_url=f"{PANEL_STATIC_PATH}/ipixel-panel.js?v={version}",
        sidebar_title="iPixel",
        sidebar_icon="mdi:dots-grid",
        config={"devices": devices},
        require_admin=False,
    )
    _LOGGER.debug("iPixel panel registered with %d device(s)", len(devices))


def _find_api(hass: HomeAssistant, address: str | None) -> iPIXELAPI:
    """Find the API instance for an address (or the only one)."""
    apis: dict[str, iPIXELAPI] = hass.data.get(DOMAIN, {})
    if not apis:
        raise HomeAssistantError("No iPIXEL device is configured")
    if address:
        for api in apis.values():
            if api.address.lower() == address.lower():
                return api
        raise HomeAssistantError(f"No iPIXEL device with address {address}")
    return next(iter(apis.values()))


def _find_entry(hass: HomeAssistant, address: str | None) -> ConfigEntry:
    """Find the config entry for an address (or the only one)."""
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        raise HomeAssistantError("No iPIXEL device is configured")
    if address:
        for entry in entries:
            if entry.data.get(CONF_ADDRESS, "").lower() == address.lower():
                return entry
        raise HomeAssistantError(f"No iPIXEL device with address {address}")
    return entries[0]


def _setup_composition_tracking(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """(Re)subscribe to the entities used by the saved composition.

    When one of them changes, the display is refreshed (debounced),
    provided the auto-update switch is on.
    """
    from .common import get_entity_id_by_unique_id, update_ipixel_display

    store = hass.data.setdefault(f"{DOMAIN}_tracking", {})

    # Cancel previous subscription
    if unsub := store.pop(entry.entry_id, None):
        unsub()

    items = entry.options.get(OPTION_KEY) or []
    entity_ids = composition_entity_ids(items)
    if not entity_ids:
        return

    address = entry.data[CONF_ADDRESS]
    name = entry.data.get(CONF_NAME, entry.title)
    pending: dict[str, Any] = {"timer": None}
    last: dict[str, Any] = {"text": None, "ts": 0.0}

    async def _do_update(_now) -> None:
        import time as _t

        pending["timer"] = None
        api = hass.data.get(DOMAIN, {}).get(entry.entry_id)
        if not api:
            return
        # Un contenuto una-tantum (immagine, animazione, timer) resta sul
        # display: niente aggiornamenti automatici finché l'utente non
        # rimanda la composizione.
        if _override_active(hass, entry.entry_id):
            return
        auto_eid = get_entity_id_by_unique_id(hass, address, "auto_update", "switch")
        auto_state = hass.states.get(auto_eid) if auto_eid else None
        if auto_state and auto_state.state != "on":
            return

        # Ogni ri-invio fa RIPARTIRE lo scorrimento da capo: quindi
        # 1) testo identico a quello già sul display = non toccare nulla
        # 2) minimo 30 s tra due ri-invii automatici (i sensori di
        #    potenza cambiano ogni pochi secondi e il testo diventava
        #    illeggibile); la modifica arrivata prima non si perde,
        #    si rimanda alla scadenza del respiro
        from .composition import build_text

        e2 = hass.config_entries.async_get_entry(entry.entry_id)
        items_now = (e2.options.get(OPTION_KEY) if e2 else None) or []
        sep = " " * int((e2.options.get("comp_sep", 2) if e2 else 2) or 2)
        text_now = build_text(hass, items_now, sep)
        if text_now == last["text"]:
            return
        wait = 30.0 - (_t.monotonic() - last["ts"])
        if wait > 0:
            pending["timer"] = async_call_later(hass, wait, _do_update)
            return

        if await update_ipixel_display(hass, name, api):
            last["text"] = text_now
            last["ts"] = _t.monotonic()
        else:
            # Invio fallito (BLE): riprova tra mezzo minuto
            pending["timer"] = async_call_later(hass, 30.0, _do_update)

    @callback
    def _on_change(event) -> None:
        # Debounce bursts of sensor updates
        if pending["timer"]:
            pending["timer"]()
        pending["timer"] = async_call_later(hass, 2.0, _do_update)

    store[entry.entry_id] = async_track_state_change_event(
        hass, entity_ids, _on_change
    )
    _LOGGER.debug(
        "Composition tracking active for %s on %d entities", name, len(entity_ids)
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/get_composition",
        vol.Optional("address"): str,
    }
)
@websocket_api.async_response
async def _ws_get_composition(hass, connection, msg) -> None:
    """Return the saved composition for a device."""
    try:
        entry = _find_entry(hass, msg.get("address"))
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "not_found", str(err))
        return
    connection.send_result(
        msg["id"],
        {
            "items": entry.options.get(OPTION_KEY) or [],
            "panel_width": entry.options.get("panel_width"),
            "panel_height": entry.options.get("panel_height"),
            "separator": entry.options.get("comp_sep", 2),
        },
    )


def _library_dir(hass: HomeAssistant) -> Path:
    """Cartella della libreria personale di immagini/animazioni."""
    return Path(hass.config.path(".storage/ipixel_color_library"))


def _save_to_library(hass: HomeAssistant, data: bytes, ext: str) -> None:
    """Salva (in executor) un contenuto nella libreria, con dedup e tetto."""
    import hashlib

    lib = _library_dir(hass)
    lib.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha1(data).hexdigest()[:12] + ext
    target = lib / name
    if target.exists():
        target.touch()  # aggiorna l'ordinamento "recenti"
    else:
        target.write_bytes(data)
    files = sorted(lib.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[50:]:
        try:
            old.unlink()
        except OSError:
            pass


def _last_image_path(hass: HomeAssistant, entry_id: str) -> str:
    """File that keeps the last sent image, to restore it after restarts."""
    return hass.config.path(f".storage/ipixel_color_last_{entry_id}")


def _remember_owner(hass: HomeAssistant, entry: ConfigEntry, owner: str, **extra) -> None:
    """Persist who owns the display (composition/image/text) across restarts."""
    options = dict(entry.options)
    options["display_owner"] = owner
    for key, value in extra.items():
        options[key] = value
    hass.config_entries.async_update_entry(entry, options=options)


def _set_override(hass: HomeAssistant, entry_id: str) -> None:
    """Mark that one-off content (image/text/timer) owns the display.

    While set, sensor-driven composition refreshes are suppressed, so
    the sent content stays until the user re-sends the composition.
    """
    hass.data.setdefault(f"{DOMAIN}_override", set()).add(entry_id)


def _clear_override(hass: HomeAssistant, entry_id: str) -> None:
    hass.data.setdefault(f"{DOMAIN}_override", set()).discard(entry_id)


def _override_active(hass: HomeAssistant, entry_id: str) -> bool:
    return entry_id in hass.data.get(f"{DOMAIN}_override", set())


def _cancel_timer(hass: HomeAssistant, entry_id: str) -> None:
    """Stop a running countdown/stopwatch task for a device."""
    tasks = hass.data.setdefault(f"{DOMAIN}_timers", {})
    hass.data.setdefault(f"{DOMAIN}_timer_mode", {}).pop(entry_id, None)
    if task := tasks.pop(entry_id, None):
        task.cancel()


async def _timer_loop(hass: HomeAssistant, entry_id: str, mode: str, seconds: int) -> None:
    """Drive a countdown/stopwatch/clock on the display."""
    import time as _time

    from .common import get_color_from_light_entity, get_entity_id_by_unique_id

    api = hass.data.get(DOMAIN, {}).get(entry_id)
    entry = hass.config_entries.async_get_entry(entry_id)
    address = entry.data.get(CONF_ADDRESS) if entry else None

    def _current_font() -> str | None:
        if not address:
            return None
        eid = get_entity_id_by_unique_id(hass, address, "font_select", "select")
        state = hass.states.get(eid) if eid else None
        return state.state if state else None

    async def show(txt: str, blink: bool = False) -> None:
        # Segue il colore del testo scelto nella pagina Colori
        color = "ffffff"
        if address:
            color = get_color_from_light_entity(
                hass, address, "text_color", default="ffffff"
            ) or "ffffff"
        await api.display_text_pypixelcolor(
            text=txt, color=color, font=_current_font(),
            animation=5 if blink else 0, speed=50, rainbow_mode=0,
        )

    # ----- orologio italiano: usato sia da solo sia nella rotazione -----
    from . import clock_styles as cs

    # Due lettere: "LU 02:09" sono 8 caratteri ed entrano nei 64 px
    giorni = ("LU", "MA", "ME", "GI", "VE", "SA", "DO")

    def _show_date_on() -> bool:
        if not address:
            return False
        eid = get_entity_id_by_unique_id(hass, address, "clock_show_date", "switch")
        st = hass.states.get(eid) if eid else None
        return bool(st and st.state == "on")

    def _style() -> str:
        e2 = hass.config_entries.async_get_entry(entry_id)
        return str((e2.options.get("clock_it_style") if e2 else "a") or "a")

    def _clock_color_hex() -> str:
        """Colore dell'orologio italiano: la scelta dedicata del pannello
        se c'e', altrimenti la luce globale 'Colore testo'."""
        e2 = hass.config_entries.async_get_entry(entry_id)
        chosen = str((e2.options.get("clock_color") if e2 else "") or "")
        if chosen:
            return chosen.lstrip("#")
        if address:
            return get_color_from_light_entity(
                hass, address, "text_color", default="ffffff"
            ) or "ffffff"
        return "ffffff"

    def _color_rgb() -> tuple[int, int, int]:
        hexc = _clock_color_hex()
        try:
            return tuple(int(hexc[i:i + 2], 16) for i in (0, 2, 4))
        except (ValueError, IndexError):
            return (255, 255, 255)

    def _temp_text() -> str | None:
        # Stile F: la prima casella accesa che sia una TEMPERATURA
        # (non la prima qualunque: i watt non sono gradi)
        e2 = hass.config_entries.async_get_entry(entry_id)
        items = (e2.options.get(OPTION_KEY) if e2 else None) or []
        for item in items:
            if (
                item.get("type", "entity") != "entity"
                or not item.get("entity_id")
                or not item.get("enabled", True)
            ):
                continue
            st = hass.states.get(item["entity_id"])
            if not st or st.state in ("unknown", "unavailable", ""):
                continue
            unit = st.attributes.get("unit_of_measurement") or ""
            if (
                st.attributes.get("device_class") != "temperature"
                and "°" not in unit
            ):
                continue
            try:
                val = f"{float(st.state):.1f}".replace(".", ",")
            except (ValueError, TypeError):
                val = st.state
            return f"{val}°"
        return None

    async def _clock_icon():
        """Icona MDI dello stile O, scaricata una volta e tenuta in memoria."""
        e2 = hass.config_entries.async_get_entry(entry_id)
        name = str((e2.options.get("clock_icon") if e2 else "") or "heart")
        colr = str((e2.options.get("clock_icon_color") if e2 else "") or "ff0000")
        cache = hass.data.setdefault(f"{DOMAIN}_iconcache", {})
        key = f"{name}|{colr}"
        if key not in cache:
            from homeassistant.helpers.aiohttp_client import async_get_clientsession

            from .device.mdi_icon import (
                fetch_emoji_svg,
                fetch_mdi_svg,
                is_emoji_icon,
                render_mdi_icon,
            )

            try:
                session = async_get_clientsession(hass)
                if is_emoji_icon(name):
                    svg = await fetch_emoji_svg(name, session)
                    colr_arg = None  # l'emoji tiene i suoi colori
                else:
                    svg = await fetch_mdi_svg(name, session)
                    colr_arg = colr
                cache[key] = await hass.async_add_executor_job(
                    render_mdi_icon, svg, 14, colr_arg
                )
            except (ValueError, ImportError) as err:
                _LOGGER.warning("Icona '%s' non disponibile: %s", name, err)
                cache[key] = None
        return cache[key]

    async def _frame(style: str, now, show_date: bool) -> None:
        temp = _temp_text() if style == "f" else None
        icon = await _clock_icon() if style == "o" else None
        e2 = hass.config_entries.async_get_entry(entry_id)
        size = str((e2.options.get("clock_size") if e2 else "") or "m")
        try:
            gap = int((e2.options.get("clock_gap") if e2 else 2))
        except (TypeError, ValueError):
            gap = 2
        day_size = (e2.options.get("clock_day_size") if e2 else None) or None
        data, ext = await hass.async_add_executor_job(
            cs.render, style, now, _color_rgb(), temp, show_date, icon,
            size, gap, day_size
        )
        # Restituisce l'esito: se False il link e' giu' e la fase
        # orologio esce subito invece di restare a martellare frame
        return bool(await api.display_image(data, ext, "crop"))

    async def _clock_tick(deadline: float | None) -> bool:
        """Una schermata dell'orologio italiano, poi dorme fino alla
        prossima (o fino alla scadenza della fase, se arriva prima).
        Ritorna False se il frame non e' partito (link giu'): chi chiama
        puo' smettere subito invece di restare a martellare la radio."""
        from homeassistant.util import dt as dt_util

        def _nap(secs: float) -> float:
            if deadline is not None:
                secs = min(secs, deadline - _time.monotonic())
            return max(0.05, secs)

        now = dt_util.now()
        style = _style()
        if style == "a":
            # Stile A: ora lo disegna clock_styles (giorno a lettere + ora
            # in font bitmap COMPATTO), non piu' il testo a tutta altezza
            # di pypixelcolor — cosi' i numeri sono piu' piccoli e il
            # giorno ("MA", "GI"...) si vede.
            ok = await _frame("a", now, False)
            if not ok:
                return False
            if _show_date_on():
                # ultimi ~10 secondi del minuto: data
                await asyncio.sleep(_nap(max(1, 51 - now.second)))
                if deadline is not None and _time.monotonic() >= deadline:
                    return True
                await _frame("a", dt_util.now(), True)
                await asyncio.sleep(_nap(max(1, 61 - dt_util.now().second)))
            else:
                await asyncio.sleep(_nap(max(1, 61 - now.second)))
            return True
        elif style == "i":
            # L'ora a parole scorre col motore del display
            color = _clock_color_hex()
            ok = await api.display_text_pypixelcolor(
                text=cs.ora_a_parole(now, with_date=_show_date_on()),
                color=color,
                font=_current_font(), animation=1, speed=50,
                rainbow_mode=0,
            )
            if not ok:
                return False
            await asyncio.sleep(_nap(max(5, 61 - dt_util.now().second)))
            return True
        elif style in ("e", "g", "h"):
            # Stili vivi: una schermata al secondo; la data compare
            # negli ultimi ~9 secondi del minuto come negli altri
            date_now = now.second >= 51 and _show_date_on()
            ok = await _frame(style, now, date_now)
            if not ok:
                return False
            frac = dt_util.now().microsecond / 1_000_000
            await asyncio.sleep(_nap(1 - frac))
            return True
        else:  # b, c, d, f, l, m, n: una schermata al minuto
            # B e D hanno gia' la data in vista: niente alternanza
            date_on = _show_date_on() and style not in ("b", "d")
            ok = await _frame(style, now, False)
            if not ok:
                return False
            if date_on:
                await asyncio.sleep(_nap(max(1, 51 - now.second)))
                if deadline is not None and _time.monotonic() >= deadline:
                    return True
                await _frame(style, dt_util.now(), True)
                await asyncio.sleep(_nap(max(1, 61 - dt_util.now().second)))
            else:
                await asyncio.sleep(_nap(max(1, 61 - now.second)))
            return True

    start = _time.monotonic()
    try:
        if not api:
            return
        if mode == "down":
            end = start + seconds
            while True:
                remaining = max(0, round(end - _time.monotonic()))
                await show(f"{remaining // 60:02d}:{remaining % 60:02d}")
                if remaining <= 0:
                    break
                await asyncio.sleep(max(0.2, min(1.0, end - _time.monotonic() - remaining + 1)))
            await show("00:00", blink=True)
        elif mode == "rotation":
            from .common import update_ipixel_display

            opts = entry.options if entry else {}
            use_sensors = bool(opts.get("rot_use_sensors", True))
            use_anim = bool(opts.get("rot_use_anim", True))
            use_clock = bool(opts.get("rot_use_clock", False))
            per_ent = bool(opts.get("rot_comp_per_ent", False))
            comp_s_raw = int(opts.get("rot_comp_s", 300))
            # In modo "a entità" il numero sono i secondi PER casella;
            # altrimenti e' la durata assoluta della fase
            comp_s = comp_s_raw if per_ent else max(30, comp_s_raw)
            anim_s = max(10, int(opts.get("rot_anim_s", 60)))
            clock_s = max(10, int(opts.get("rot_clock_s", 60)))
            anim_file = Path(str(opts.get("rot_anim") or "")).name
            gallery = Path(__file__).parent / "panel" / "gallery"
            anim_bytes = None
            anim_ext = ".gif"
            if use_anim and anim_file:
                # Cerca sia nella galleria di fabbrica sia nella libreria
                for base in (gallery, _library_dir(hass)):
                    candidate = base / anim_file
                    if candidate.exists():
                        anim_bytes = await hass.async_add_executor_job(
                            candidate.read_bytes
                        )
                        anim_ext = candidate.suffix or ".gif"
                        break
            name_ = entry.data.get(CONF_NAME, entry.title) if entry else ""

            async def _force_text_mode() -> None:
                # Se il display e' rimasto in modalita' orologio (quello
                # di fabbrica), riportalo al testo: la fase sensori
                # rispetta il modo e altrimenti ridisegnerebbe lo stock
                if not address:
                    return
                mode_eid = get_entity_id_by_unique_id(
                    hass, address, "mode_select", "select"
                )
                mode_state = hass.states.get(mode_eid) if mode_eid else None
                if mode_state and mode_state.state == "clock":
                    await hass.services.async_call(
                        "select", "select_option",
                        {"entity_id": mode_eid, "option": "text"},
                        blocking=True,
                    )

            async def _phase_sensors(secs: int, per_ent_flag: bool) -> None:
                await _force_text_mode()
                # Fase sensori: composizione coi valori attuali
                await update_ipixel_display(hass, name_, api)
                # Il blocco protegge la rotazione dagli aggiornamenti
                # automatici al cambio dei sensori
                _set_override(hass, entry_id)
                if per_ent_flag:
                    # Conta le caselle accese ADESSO: spegnendone
                    # qualcuna il giro si accorcia da solo
                    e2 = hass.config_entries.async_get_entry(entry_id)
                    items = (e2.options.get(OPTION_KEY) if e2 else None) or []
                    n = sum(1 for it in items if it.get("enabled", True))
                    await asyncio.sleep(max(10, secs * max(1, n)))
                else:
                    await asyncio.sleep(max(5, secs))

            _img_cache: dict[str, tuple[bytes, str]] = {}

            async def _phase_image(fname: str, secs: int) -> None:
                fname = Path(str(fname or "")).name
                if not fname:
                    return
                if fname not in _img_cache:
                    for base in (gallery, _library_dir(hass)):
                        candidate = base / fname
                        if candidate.exists():
                            data = await hass.async_add_executor_job(
                                candidate.read_bytes
                            )
                            _img_cache[fname] = (data, candidate.suffix or ".gif")
                            break
                    else:
                        _LOGGER.warning("Scaletta: immagine %s non trovata", fname)
                        _img_cache[fname] = (b"", "")
                data, ext = _img_cache[fname]
                if not data:
                    return
                await api.display_image(data, ext, "crop")
                await asyncio.sleep(max(5, secs))

            async def _phase_clock(secs: int) -> None:
                # Orologio ITALIANO con lo stile scelto: non cambia la
                # modalita' del display, cosi' dopo la fase i sensori
                # tornano regolarmente (l'orologio di fabbrica invece
                # metteva il display in modo clock e ci restava).
                # Rispetta la durata scelta (minimo 1 s): il tempo vero
                # non scende sotto quello che ci mette il BLE a mandare
                # l'immagine dell'orologio, ma niente piu' attesa inutile.
                end_clock = _time.monotonic() + max(1, secs)
                while _time.monotonic() < end_clock - 0.05:
                    ok = await _clock_tick(end_clock)
                    if not ok:
                        # Link assente: non restare a martellare frame e
                        # riconnessioni dentro la fase orologio. Esce e
                        # lascia proseguire la rotazione (con il respiro
                        # di 0.4s a fine fase), cosi' non sembra bloccata.
                        break

            def _playlist() -> list:
                e2 = hass.config_entries.async_get_entry(entry_id)
                return list((e2.options.get("rot_playlist") if e2 else None) or [])

            if _playlist():
                # SCALETTA: decide lui cosa e quando, nell'ordine scelto.
                # Riletta a ogni giro: le modifiche entrano da sole.
                while True:
                    steps = _playlist()
                    if not steps:
                        return
                    for step in steps:
                        typ = step.get("type")
                        secs = int(step.get("seconds", 60))
                        if typ == "sensors":
                            await _phase_sensors(secs, bool(step.get("per_ent")))
                        elif typ == "image":
                            await _phase_image(step.get("file"), secs)
                        elif typ == "clock":
                            await _phase_clock(secs)
                        # Un filo di respiro tra una fase e l'altra: con
                        # durate corte (es. orologio 1s) evita di sparare
                        # immagini BLE una attaccata all'altra e stressare
                        # la radio (è lì che il firmware si impunta).
                        await asyncio.sleep(0.4)
            else:
                if not (use_sensors or anim_bytes or use_clock):
                    return  # niente da ruotare
                while True:
                    if use_sensors:
                        await _phase_sensors(comp_s, per_ent)
                    if anim_bytes:
                        await api.display_image(anim_bytes, anim_ext, "crop")
                        await asyncio.sleep(anim_s)
                    if use_clock:
                        await _phase_clock(clock_s)
        elif mode == "clock_it":
            while True:
                ok = await _clock_tick(None)
                if not ok:
                    # Link giu': aspetta un filo prima di riprovare, cosi'
                    # non spinge frame e riconnessioni a raffica
                    await asyncio.sleep(3)
        elif mode == "rhythm":
            # Equalizzatore di bordo: le barre le muoviamo noi con
            # un'onda morbida + un po' di caso (manca un microfono)
            import math
            import random

            style = hass.data.get(f"{DOMAIN}_rhythm_style", {}).get(entry_id, 0)
            t0 = _time.monotonic()
            phases = [random.uniform(0, 6.28) for _ in range(11)]
            speeds = [random.uniform(1.2, 3.2) for _ in range(11)]
            while True:
                t = _time.monotonic() - t0
                levels = [
                    max(0, min(15, int(
                        8 + 6.5 * math.sin(speeds[i] * t + phases[i])
                        + random.uniform(-2.5, 2.5)
                    )))
                    for i in range(11)
                ]
                await api.set_rhythm(style, levels)
                await asyncio.sleep(0.15)
        else:  # cronometro
            while True:
                elapsed = round(_time.monotonic() - start)
                await show(f"{elapsed // 60:02d}:{elapsed % 60:02d}")
                await asyncio.sleep(max(0.2, 1 - ((_time.monotonic() - start) % 1)))
    except asyncio.CancelledError:
        pass
    except Exception as err:  # pragma: no cover
        _LOGGER.error("Timer loop error: %s", err)
    finally:
        # Sganciati SOLO se il registrato sei ancora tu: dopo un restart
        # rapido nel dict c'e' gia' il task nuovo, e il finally del vecchio
        # (che arriva dopo) non deve portarselo via
        timers = hass.data.get(f"{DOMAIN}_timers", {})
        if timers.get(entry_id) is asyncio.current_task():
            timers.pop(entry_id, None)
            hass.data.get(f"{DOMAIN}_timer_mode", {}).pop(entry_id, None)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/timer",
        vol.Optional("address"): str,
        vol.Required("action"): vol.In(["start", "stop"]),
        vol.Optional("mode", default="down"): vol.In(
            ["down", "up", "clock_it", "rotation"]
        ),
        vol.Optional("seconds", default=60): vol.All(int, vol.Range(min=1, max=86400)),
        vol.Optional("anim"): cv.string,
        vol.Optional("comp_seconds"): vol.All(int, vol.Range(min=1, max=3600)),
        vol.Optional("comp_per_entity"): bool,
        vol.Optional("anim_seconds"): vol.All(int, vol.Range(min=10, max=600)),
        vol.Optional("clock_seconds"): vol.All(int, vol.Range(min=10, max=3600)),
        vol.Optional("use_sensors"): bool,
        vol.Optional("use_anim"): bool,
        vol.Optional("use_clock"): bool,
        vol.Optional("clock_style"): vol.In(
            ["a", "b", "c", "d", "e", "f", "g", "h", "i", "l", "m", "n", "o"]
        ),
    }
)
@websocket_api.async_response
async def _ws_timer(hass, connection, msg) -> None:
    """Start or stop a countdown/stopwatch/clock/rotation on the display."""
    try:
        entry = _find_entry(hass, msg.get("address"))
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "not_found", str(err))
        return

    _cancel_timer(hass, entry.entry_id)
    if msg["action"] == "start":
        if msg["mode"] == "rotation":
            # Salva la configurazione della rotazione (persiste ai riavvii)
            extra = {}
            if "anim" in msg:
                extra["rot_anim"] = Path(msg["anim"]).name
            if "comp_seconds" in msg:
                extra["rot_comp_s"] = msg["comp_seconds"]
            if "comp_per_entity" in msg:
                extra["rot_comp_per_ent"] = msg["comp_per_entity"]
            if "anim_seconds" in msg:
                extra["rot_anim_s"] = msg["anim_seconds"]
            if "clock_seconds" in msg:
                extra["rot_clock_s"] = msg["clock_seconds"]
            for flag in ("use_sensors", "use_anim", "use_clock"):
                if flag in msg:
                    extra[f"rot_{flag}"] = msg[flag]
            _remember_owner(hass, entry, "rotation", **extra)
            entry = _find_entry(hass, msg.get("address"))  # opzioni aggiornate
        elif msg["mode"] == "clock_it":
            # Anche l'orologio italiano (e il suo stile) sopravvive ai riavvii
            extra = {}
            if "clock_style" in msg:
                extra["clock_it_style"] = msg["clock_style"]
            _remember_owner(hass, entry, "clock_it", **extra)
            entry = _find_entry(hass, msg.get("address"))
        _set_override(hass, entry.entry_id)
        # eager_start=False: il task va registrato PRIMA che inizi a
        # girare, altrimenti non si riconosce e si auto-cancella
        task = hass.async_create_background_task(
            _timer_loop(hass, entry.entry_id, msg["mode"], msg["seconds"]),
            "ipixel rotazione",
            eager_start=False,
        )
        hass.data.setdefault(f"{DOMAIN}_timers", {})[entry.entry_id] = task
        hass.data.setdefault(f"{DOMAIN}_timer_mode", {})[entry.entry_id] = msg["mode"]
    elif entry.options.get("display_owner") in ("rotation", "clock_it"):
        # Fermato a mano: al prossimo riavvio niente ripartenza
        _remember_owner(hass, entry, "composition")
        _clear_override(hass, entry.entry_id)

    connection.send_result(msg["id"], {"running": msg["action"] == "start"})


async def _maybe_video_to_gif(
    hass: HomeAssistant, api, data: bytes, ext: str
) -> tuple[bytes, str]:
    """Convert a short video (mp4/mov/webm) to a panel-sized GIF via ffmpeg."""
    is_mp4 = len(data) > 8 and data[4:8] == b"ftyp"
    is_webm = data[:4] == b"\x1aE\xdf\xa3"
    if ext not in (".mp4", ".mov", ".webm") and not is_mp4 and not is_webm:
        return data, ext

    info = await api.get_device_info()
    w, h = info["width"], info["height"]

    import os
    import tempfile

    suffix = ext if ext in (".mp4", ".mov", ".webm") else (".webm" if is_webm else ".mp4")

    def _write_src() -> str:
        src = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        src.write(data)
        src.close()
        return src.name

    src_name = await hass.async_add_executor_job(_write_src)
    dst = src_name + ".gif"

    def _read_dst() -> bytes:
        with open(dst, "rb") as f:
            return f.read()

    def _cleanup() -> None:
        for p in (src_name, dst):
            try:
                os.unlink(p)
            except OSError:
                pass

    try:
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-i", src_name, "-t", "4",
                "-vf",
                f"fps=6,scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}",
                dst,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise HomeAssistantError(
                "ffmpeg non disponibile: impossibile convertire il video"
            ) from exc
        _, err = await proc.communicate()
        if proc.returncode != 0:
            raise HomeAssistantError(
                "Conversione video fallita: " + err.decode(errors="replace")[-200:]
            )
        try:
            return await hass.async_add_executor_job(_read_dst), ".gif"
        except OSError as oserr:
            raise HomeAssistantError(f"Conversione video fallita: {oserr}") from oserr
    finally:
        await hass.async_add_executor_job(_cleanup)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/display_url",
        vol.Optional("address"): str,
        vol.Required("url"): cv.url,
        vol.Optional("resize_method", default="crop"): vol.In(["crop", "fit"]),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def _ws_display_url(hass, connection, msg) -> None:
    """Download an image/GIF from a URL and send it to the display."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    try:
        entry = _find_entry(hass, msg.get("address"))
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "not_found", str(err))
        return

    url = msg["url"]
    try:
        session = async_get_clientsession(hass)
        async with asyncio.timeout(25):
            resp = await session.get(url)
            resp.raise_for_status()
            # Scarica con tetto DURANTE il download, non dopo
            parts: list[bytes] = []
            size = 0
            async for part in resp.content.iter_chunked(64 * 1024):
                size += len(part)
                if size > 8 * 1024 * 1024:
                    raise ValueError("file oltre 8 MB")
                parts.append(part)
            data = b"".join(parts)
    except Exception as err:
        connection.send_error(msg["id"], "download_failed", f"Download fallito: {err}")
        return

    # Estensione dal contenuto (magic bytes), con ripiego sull'URL
    if data[:6] in (b"GIF87a", b"GIF89a"):
        ext = ".gif"
    elif data[:8] == b"\x89PNG\r\n\x1a\n":
        ext = ".png"
    elif data[:3] == b"\xff\xd8\xff":
        ext = ".jpg"
    elif len(data) > 8 and data[4:8] == b"ftyp":
        ext = ".mp4"
    elif data[:4] == b"\x1aE\xdf\xa3":
        ext = ".webm"
    else:
        ext = ".gif" if ".gif" in url.lower() else ".png"

    _cancel_timer(hass, entry.entry_id)
    api = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    ok = False
    if api:
        try:
            data, ext = await _maybe_video_to_gif(hass, api, data, ext)
        except HomeAssistantError as err:
            connection.send_error(msg["id"], "convert_failed", str(err))
            return
        ok = await api.display_image(
            image_bytes=data, file_extension=ext,
            resize_method=msg["resize_method"],
        )
        if ok:
            _set_override(hass, entry.entry_id)
            await hass.async_add_executor_job(
                Path(_last_image_path(hass, entry.entry_id)).write_bytes, data
            )
            await hass.async_add_executor_job(_save_to_library, hass, data, ext)
            _remember_owner(
                hass, entry, "image",
                last_ext=ext, last_resize=msg["resize_method"],
            )
    result = {"displayed": ok, "bytes": len(data), "ext": ext}
    # Anteprima per il pannello (solo file piccoli)
    if ok and len(data) <= 400 * 1024:
        result["preview"] = base64.b64encode(data).decode()
        result["preview_ext"] = ext.lstrip(".")
    connection.send_result(msg["id"], result)


class IpixelUploadView(HomeAssistantView):
    """Upload HTTP di un'immagine/GIF/video dal pannello al display.

    Il WebSocket di HA accetta messaggi fino a 4 MiB: troppo poco per una
    GIF del telefono mandata in base64 (che la gonfia di 4/3). Il body HTTP
    arriva invece a 16 MiB, e qui passano i byte GREZZI del file, senza
    gonfiarli. Stessa logica del canale "da URL", ma con i byte caricati.
    """

    url = "/api/ipixel_color/upload"
    name = "api:ipixel_color:upload"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        """Tiene l'istanza hass: su HA recenti request.app non e' piu'
        indicizzato dalla stringa 'hass' ma da un AppKey."""
        self.hass = hass

    async def post(self, request):
        from aiohttp import web

        hass = self.hass
        data = await request.read()
        if not data:
            return web.json_response({"error": "corpo vuoto"}, status=400)
        if len(data) > 15 * 1024 * 1024:
            return web.json_response({"error": "file oltre 15 MB"}, status=413)

        address = request.query.get("address")
        resize_method = request.query.get("resize_method", "crop")
        if resize_method not in ("crop", "fit"):
            resize_method = "crop"
        try:
            slot = int(request.query.get("save_slot", "0"))
        except (TypeError, ValueError):
            slot = 0

        try:
            entry = _find_entry(hass, address)
        except HomeAssistantError as err:
            return web.json_response({"error": str(err)}, status=404)

        # Estensione dai magic bytes (come il canale da URL)
        if data[:6] in (b"GIF87a", b"GIF89a"):
            ext = ".gif"
        elif data[:8] == b"\x89PNG\r\n\x1a\n":
            ext = ".png"
        elif data[:3] == b"\xff\xd8\xff":
            ext = ".jpg"
        elif len(data) > 8 and data[4:8] == b"ftyp":
            ext = ".mp4"
        elif data[:4] == b"\x1aE\xdf\xa3":
            ext = ".webm"
        else:
            return web.json_response(
                {"error": "formato non riconosciuto (serve PNG/JPG/GIF/MP4/WEBM)"},
                status=415,
            )

        api = hass.data.get(DOMAIN, {}).get(entry.entry_id)
        if not api:
            return web.json_response({"error": "dispositivo non pronto"}, status=503)

        _cancel_timer(hass, entry.entry_id)
        try:
            data, ext = await _maybe_video_to_gif(hass, api, data, ext)
        except HomeAssistantError as err:
            return web.json_response({"error": str(err)}, status=422)

        ok = await api.display_image(
            image_bytes=data, file_extension=ext,
            resize_method=resize_method, save_slot=slot,
        )
        if ok:
            _set_override(hass, entry.entry_id)
            await hass.async_add_executor_job(
                Path(_last_image_path(hass, entry.entry_id)).write_bytes, data
            )
            await hass.async_add_executor_job(_save_to_library, hass, data, ext)
            _remember_owner(
                hass, entry, "image",
                last_ext=ext, last_resize=resize_method,
            )

        result = {"displayed": ok, "bytes": len(data), "ext": ext}
        if ok and len(data) <= 400 * 1024:
            result["preview"] = base64.b64encode(data).decode()
            result["preview_ext"] = ext.lstrip(".")
        return web.json_response(result)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/release",
        vol.Optional("address"): str,
        vol.Required("action"): vol.In(["release", "reconnect"]),
        vol.Optional("seconds", default=600): vol.All(
            int, vol.Range(min=60, max=3600)
        ),
    }
)
@websocket_api.async_response
async def _ws_release(hass, connection, msg) -> None:
    """Libera il BLE per l'app del telefono, o lo riprende.

    Il display accetta UNA connessione alla volta: finche' HA lo tiene,
    l'app resta fuori. Portato dal fork CzarofAK, in versione pigra.
    """
    try:
        entry = _find_entry(hass, msg.get("address"))
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "not_found", str(err))
        return
    api = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not api:
        connection.send_error(msg["id"], "not_ready", "Integrazione non pronta")
        return

    if msg["action"] == "release":
        await api.release_for_app(msg["seconds"])
    else:
        api.end_app_release()
    connection.send_result(
        msg["id"], {"released_s": api.released_seconds_left}
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/show_icon",
        vol.Optional("address"): str,
        vol.Required("icon"): cv.string,
        vol.Optional("color", default="ffffff"): cv.string,
        vol.Optional("bg_color", default="000000"): cv.string,
        vol.Optional("size", default=16): vol.All(int, vol.Range(min=4, max=64)),
        vol.Optional("pos_x", default="center"): vol.In(["left", "center", "right"]),
        vol.Optional("pos_y", default="center"): vol.In(["top", "center", "bottom"]),
        vol.Optional("motion", default="none"): vol.In(["none", "left", "right"]),
    }
)
@websocket_api.async_response
async def _ws_show_icon(hass, connection, msg) -> None:
    """Icona MDI sul display: scaricata, colorata e centrata dal server.

    Portato dal fork tigers75 (jsDelivr @mdi/svg + resvg_py).
    """
    try:
        entry = _find_entry(hass, msg.get("address"))
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "not_found", str(err))
        return
    api = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not api:
        connection.send_error(msg["id"], "not_ready", "Integrazione non pronta")
        return

    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    from .device.mdi_icon import (
        compose_icon_media,
        fetch_emoji_svg,
        fetch_mdi_svg,
        render_mdi_icon,
        is_emoji_icon,
    )

    info = await api.get_device_info()
    try:
        session = async_get_clientsession(hass)
        if is_emoji_icon(msg["icon"]):
            # Un'emoji al posto del nome MDI: Twemoji, coi suoi colori
            svg = await fetch_emoji_svg(msg["icon"], session)
            color = None
        else:
            svg = await fetch_mdi_svg(msg["icon"], session)
            color = msg["color"]
        size = min(msg["size"], info["height"])
        icon_img = await hass.async_add_executor_job(
            render_mdi_icon, svg, size, color
        )
        data, ext = await hass.async_add_executor_job(
            compose_icon_media, icon_img, info["width"], info["height"],
            msg["pos_x"], msg["pos_y"], msg["motion"], msg["bg_color"],
        )
    except (ValueError, ImportError) as err:
        connection.send_error(msg["id"], "icon_failed", str(err))
        return

    _cancel_timer(hass, entry.entry_id)
    ok = await api.display_image(data, ext, "crop")
    if ok:
        _set_override(hass, entry.entry_id)
        await hass.async_add_executor_job(
            Path(_last_image_path(hass, entry.entry_id)).write_bytes, data
        )
        # Anche nella libreria "Le mie immagini": riusabile a mano
        # e come fase della rotazione
        await hass.async_add_executor_job(_save_to_library, hass, data, ext)
        _remember_owner(hass, entry, "image", last_ext=ext, last_resize="crop")

    import base64 as _b64

    connection.send_result(msg["id"], {
        "displayed": ok,
        "preview": _b64.b64encode(data).decode(),
        "preview_ext": ext.lstrip("."),
    })


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/show_free_text",
        vol.Optional("address"): str,
        vol.Optional("text", default=""): cv.string,
        vol.Optional("entity_id", default=""): cv.string,
        vol.Optional("color", default="ffffff"): cv.string,
        vol.Optional("bg_color", default="000000"): cv.string,
        vol.Optional("size", default=10): vol.All(int, vol.Range(min=5, max=16)),
        vol.Optional("font"): cv.string,
        vol.Optional("pos_x", default="center"): vol.In(["left", "center", "right"]),
        vol.Optional("pos_y", default="center"): vol.In(["top", "center", "bottom"]),
        vol.Optional("motion", default="none"): vol.In(["none", "left", "right"]),
        vol.Optional("speed", default=50): vol.All(int, vol.Range(min=0, max=100)),
    }
)
@websocket_api.async_response
async def _ws_show_free_text(hass, connection, msg) -> None:
    """Scritta disegnata dal server: grandezza, posizione e scorrimento
    a piacere, come le icone. Il testo e' quello scritto, il valore di
    un'entità, o tutti e due ("testo valore")."""
    try:
        entry = _find_entry(hass, msg.get("address"))
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "not_found", str(err))
        return
    api = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not api:
        connection.send_error(msg["id"], "not_ready", "Integrazione non pronta")
        return

    text = msg["text"].strip()
    if msg["entity_id"]:
        st = hass.states.get(msg["entity_id"])
        if not st or st.state in ("unknown", "unavailable", ""):
            connection.send_error(
                msg["id"], "no_state",
                f"L'entità {msg['entity_id']} non ha un valore adesso",
            )
            return
        value = st.state
        try:
            value = (f"{float(value):.1f}".rstrip("0").rstrip(".") or "0"
                     ).replace(".", ",")
        except (ValueError, TypeError):
            pass
        unit = (st.attributes.get("unit_of_measurement") or "").replace("°", "")
        if unit:
            value = f"{value}{unit}" if unit == "%" else f"{value} {unit}"
        text = f"{text} {value}".strip()
    if not text:
        connection.send_error(
            msg["id"], "empty", "Scrivi qualcosa o scegli un'entità"
        )
        return

    from .device.mdi_icon import compose_icon_media
    from .display.text_renderer import render_text_sprite

    info = await api.get_device_info()
    size = min(msg["size"], info["height"])
    try:
        sprite = await hass.async_add_executor_job(
            render_text_sprite, text, size, msg["color"], msg.get("font")
        )
        # Velocità: la GIF la disegniamo noi, quindi può andare anche
        # più lenta del minimo del firmware. Stessa formula
        # dell'emulatore: 500-(v-1)*4.7 ms per pixel.
        ms_per_px = max(20, round(500 - (msg["speed"] - 1) * 4.7))
        step = 1 if ms_per_px > 120 else 2
        data, ext = await hass.async_add_executor_job(
            functools.partial(
                compose_icon_media, sprite, info["width"], info["height"],
                msg["pos_x"], msg["pos_y"], msg["motion"], msg["bg_color"],
                frame_ms=ms_per_px * step, step_px=step,
            )
        )
    except (ValueError, OSError) as err:
        connection.send_error(msg["id"], "render_failed", str(err))
        return

    _cancel_timer(hass, entry.entry_id)
    ok = await api.display_image(data, ext, "crop")
    if ok:
        _set_override(hass, entry.entry_id)
        await hass.async_add_executor_job(
            Path(_last_image_path(hass, entry.entry_id)).write_bytes, data
        )
        # Anche in "Le mie immagini": riusabile a mano e come fase
        # della rotazione (come le icone)
        await hass.async_add_executor_job(_save_to_library, hass, data, ext)
        _remember_owner(hass, entry, "image", last_ext=ext, last_resize="crop")

    connection.send_result(msg["id"], {
        "displayed": ok,
        "preview": base64.b64encode(data).decode(),
        "preview_ext": ext.lstrip("."),
    })


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/set_playlist",
        vol.Optional("address"): str,
        vol.Required("playlist"): list,
    }
)
@websocket_api.async_response
async def _ws_set_playlist(hass, connection, msg) -> None:
    """Salva la scaletta della rotazione: decide l'utente cosa e quando.

    La rotazione la rilegge a ogni giro, quindi le modifiche entrano
    da sole anche mentre gira."""
    try:
        entry = _find_entry(hass, msg.get("address"))
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "not_found", str(err))
        return

    steps = []
    for raw in msg["playlist"][:20]:
        if not isinstance(raw, dict):
            continue
        typ = raw.get("type")
        if typ not in ("sensors", "image", "clock"):
            continue
        step = {
            "type": typ,
            "seconds": max(5, min(86400, int(raw.get("seconds", 60) or 60))),
        }
        if typ == "sensors" and raw.get("per_ent"):
            step["per_ent"] = True
        if typ == "image":
            fname = Path(str(raw.get("file") or "")).name
            if not fname:
                continue
            step["file"] = fname
        steps.append(step)

    options = dict(entry.options)
    options["rot_playlist"] = steps
    hass.config_entries.async_update_entry(entry, options=options)
    connection.send_result(msg["id"], {"saved": True, "steps": len(steps)})


def _get_entry_api(hass, connection, msg):
    """Entry+api per i comandi nativi, o None (errore già mandato)."""
    try:
        entry = _find_entry(hass, msg.get("address"))
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "not_found", str(err))
        return None, None
    api = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not api:
        connection.send_error(msg["id"], "not_ready", "Integrazione non pronta")
        return None, None
    return entry, api


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/native_timer",
        vol.Optional("address"): str,
        vol.Required("action"): vol.In(["start", "pause", "stop"]),
    }
)
@websocket_api.async_response
async def _ws_native_timer(hass, connection, msg) -> None:
    """Cronometro DI BORDO del display: conta da solo, zero BLE."""
    entry, api = _get_entry_api(hass, connection, msg)
    if not api:
        return
    act = {"stop": 0, "start": 1, "pause": 2}[msg["action"]]
    if act == 1:
        _cancel_timer(hass, entry.entry_id)
        _set_override(hass, entry.entry_id)
    ok = await api.native_timer(act)
    connection.send_result(msg["id"], {"ok": ok})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/scores",
        vol.Optional("address"): str,
        vol.Required("p1"): vol.All(int, vol.Range(min=0, max=99)),
        vol.Required("p2"): vol.All(int, vol.Range(min=0, max=99)),
    }
)
@websocket_api.async_response
async def _ws_scores(hass, connection, msg) -> None:
    """Tabellone segnapunti NATIVO del firmware."""
    entry, api = _get_entry_api(hass, connection, msg)
    if not api:
        return
    _cancel_timer(hass, entry.entry_id)
    _set_override(hass, entry.entry_id)
    ok = await api.native_scores(msg["p1"], msg["p2"])
    connection.send_result(msg["id"], {"ok": ok})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/schedule",
        vol.Optional("address"): str,
        vol.Required("enabled"): bool,
        vol.Optional("on_hour", default=8): vol.All(int, vol.Range(min=0, max=23)),
        vol.Optional("on_minute", default=0): vol.All(int, vol.Range(min=0, max=59)),
        vol.Optional("off_hour", default=23): vol.All(int, vol.Range(min=0, max=23)),
        vol.Optional("off_minute", default=0): vol.All(int, vol.Range(min=0, max=59)),
        vol.Optional("days", default=[]): [str],
    }
)
@websocket_api.async_response
async def _ws_schedule(hass, connection, msg) -> None:
    """Accensione/spegnimento programmati NEL display (vivono nel
    firmware: funzionano anche con Home Assistant spento)."""
    from .device.native import days_mask

    entry, api = _get_entry_api(hass, connection, msg)
    if not api:
        return
    try:
        mask = days_mask(msg["days"] or None) if msg["enabled"] else 0x01
    except ValueError as err:
        connection.send_error(msg["id"], "bad_days", str(err))
        return
    # L'orologio di bordo deve essere giusto, sennò gli orari sballano
    await api.sync_time()
    ok1 = await api.native_schedule(
        True, 0, mask, msg["on_hour"], msg["on_minute"]
    )
    ok2 = await api.native_schedule(
        False, 1, mask, msg["off_hour"], msg["off_minute"]
    )
    if ok1 and ok2:
        options = dict(entry.options)
        options["schedule_cfg"] = {
            "enabled": msg["enabled"],
            "on_hour": msg["on_hour"], "on_minute": msg["on_minute"],
            "off_hour": msg["off_hour"], "off_minute": msg["off_minute"],
            "days": msg["days"],
        }
        hass.config_entries.async_update_entry(entry, options=options)
    connection.send_result(msg["id"], {"ok": ok1 and ok2})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/rhythm",
        vol.Optional("address"): str,
        vol.Required("action"): vol.In(["start", "stop"]),
        vol.Optional("style", default=0): vol.All(int, vol.Range(min=0, max=4)),
    }
)
@websocket_api.async_response
async def _ws_rhythm(hass, connection, msg) -> None:
    """Equalizzatore di bordo, animato dal server finché non si ferma."""
    entry, api = _get_entry_api(hass, connection, msg)
    if not api:
        return
    _cancel_timer(hass, entry.entry_id)
    if msg["action"] == "start":
        _set_override(hass, entry.entry_id)
        hass.data.setdefault(f"{DOMAIN}_rhythm_style", {})[entry.entry_id] = msg["style"]
        task = hass.async_create_background_task(
            _timer_loop(hass, entry.entry_id, "rhythm", 0),
            "ipixel rotazione",
            eager_start=False,
        )
        hass.data.setdefault(f"{DOMAIN}_timers", {})[entry.entry_id] = task
        hass.data.setdefault(f"{DOMAIN}_timer_mode", {})[entry.entry_id] = "rhythm"
    connection.send_result(msg["id"], {"running": msg["action"] == "start"})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/fun_mode",
        vol.Optional("address"): str,
        vol.Required("enable"): bool,
    }
)
@websocket_api.async_response
async def _ws_fun_mode(hass, connection, msg) -> None:
    """Disegno in diretta: accende/spegne il fun mode del firmware."""
    entry, api = _get_entry_api(hass, connection, msg)
    if not api:
        return
    if msg["enable"]:
        _cancel_timer(hass, entry.entry_id)
        _set_override(hass, entry.entry_id)
    ok = await api.set_fun_mode(msg["enable"])
    connection.send_result(msg["id"], {"ok": ok})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/pixel",
        vol.Optional("address"): str,
        vol.Required("x"): vol.All(int, vol.Range(min=0, max=255)),
        vol.Required("y"): vol.All(int, vol.Range(min=0, max=255)),
        vol.Required("color"): cv.string,
    }
)
@websocket_api.async_response
async def _ws_pixel(hass, connection, msg) -> None:
    """Un pixel in diretta (fun mode attivo)."""
    entry, api = _get_entry_api(hass, connection, msg)
    if not api:
        return
    ok = await api.set_pixel(msg["x"], msg["y"], msg["color"])
    connection.send_result(msg["id"], {"ok": ok})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/slot",
        vol.Optional("address"): str,
        vol.Required("action"): vol.In(["save", "show", "delete"]),
        vol.Required("slot"): vol.All(int, vol.Range(min=1, max=8)),
    }
)
@websocket_api.async_response
async def _ws_slot(hass, connection, msg) -> None:
    """Memoria del display: salva/richiama/svuota gli slot di bordo."""
    entry, api = _get_entry_api(hass, connection, msg)
    if not api:
        return
    slot = msg["slot"]
    options = dict(entry.options)
    registry = dict(options.get("device_slots") or {})

    if msg["action"] == "save":
        # Salva nello slot L'ULTIMA immagine mandata al display
        path = Path(_last_image_path(hass, entry.entry_id))
        if not path.exists():
            connection.send_error(
                msg["id"], "no_image",
                "Prima manda un'immagine (o scritta/icona) al display",
            )
            return
        data = await hass.async_add_executor_job(path.read_bytes)
        ext = options.get("last_ext", ".png")
        ok = await api.display_image(data, ext, "crop", save_slot=slot)
        if ok:
            from homeassistant.util import dt as dt_util

            registry[str(slot)] = f"salvata {dt_util.now():%d/%m %H:%M}"
            options["device_slots"] = registry
            hass.config_entries.async_update_entry(entry, options=options)
        connection.send_result(msg["id"], {"ok": ok})
        return

    if msg["action"] == "show":
        _cancel_timer(hass, entry.entry_id)
        _set_override(hass, entry.entry_id)
        ok = await api.show_slot(slot)
        connection.send_result(msg["id"], {"ok": ok})
        return

    ok = await api.delete_slot(slot)
    if ok:
        registry.pop(str(slot), None)
        options["device_slots"] = registry
        hass.config_entries.async_update_entry(entry, options=options)
    connection.send_result(msg["id"], {"ok": ok})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/preview_segment",
        vol.Optional("address"): str,
        vol.Required("item"): dict,
        vol.Optional("font"): vol.Any(str, None),
    }
)
@websocket_api.async_response
async def _ws_preview_segment(hass, connection, msg) -> None:
    """Rende UNA sola casella come immagine statica: l'anteprima dentro
    l'editor mostra solo la casella che si sta modificando, ferma, così si
    capisce (invece dell'intera composizione che scorre)."""
    import base64
    import io as _io

    from .composition import build_segments
    from .device.text import render_segments_image
    from .api import _resolve_font

    item = msg["item"]
    font = _resolve_font(msg.get("font"))

    def _render():
        segs = build_segments(hass, [item])
        if not segs:
            return None, 0, 0
        img = render_segments_image(segs, font=font, block_height=16)
        buf = _io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii"), img.width, img.height

    try:
        b64, w, h = await hass.async_add_executor_job(_render)
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "render_failed", str(err))
        return
    connection.send_result(msg["id"], {"b64": b64, "width": w, "height": h})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/frame",
        vol.Optional("address"): str,
    }
)
@websocket_api.async_response
async def _ws_frame(hass, connection, msg) -> None:
    """L'ultima cosa davvero mandata al display: l'anteprima del
    pannello la usa per rispecchiare il display fisico."""
    try:
        entry = _find_entry(hass, msg.get("address"))
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "not_found", str(err))
        return
    api = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    media = (api.last_media if api else None) or {"kind": None}
    running = entry.entry_id in hass.data.get(f"{DOMAIN}_timers", {})
    connection.send_result(msg["id"], {"running": running, **media})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/set_clock_style",
        vol.Optional("address"): str,
        vol.Required("clock_style"): vol.In(
            ["a", "b", "c", "d", "e", "f", "g", "h", "i", "l", "m", "n", "o"]
        ),
        vol.Optional("icon"): cv.string,
        vol.Optional("icon_color"): cv.string,
        vol.Optional("color"): cv.string,
        vol.Optional("size"): vol.Any(
            vol.All(vol.Coerce(int), vol.Range(min=5, max=16)),
            vol.In(["s", "m", "g"]),
        ),
        vol.Optional("gap"): vol.All(int, vol.Range(min=0, max=12)),
        vol.Optional("day_size"): vol.In(["auto", "1", "2"]),
    }
)
@websocket_api.async_response
async def _ws_set_clock_style(hass, connection, msg) -> None:
    """Ricorda lo stile dell'orologio italiano senza avviare nulla.

    Serve alla tendina del pannello: la scelta vale anche per la fase
    orologio della rotazione, che legge lo stile salvato a ogni giro.
    """
    try:
        entry = _find_entry(hass, msg.get("address"))
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "not_found", str(err))
        return

    options = dict(entry.options)
    options["clock_it_style"] = msg["clock_style"]
    if "icon" in msg:
        options["clock_icon"] = msg["icon"].strip().removeprefix("mdi:")
    if "icon_color" in msg:
        options["clock_icon_color"] = msg["icon_color"].lstrip("#")
    if "color" in msg:
        options["clock_color"] = msg["color"].lstrip("#")
    if "size" in msg:
        options["clock_size"] = msg["size"]
    if "gap" in msg:
        options["clock_gap"] = int(msg["gap"])
    if "day_size" in msg:
        options["clock_day_size"] = "" if msg["day_size"] == "auto" else msg["day_size"]
    hass.config_entries.async_update_entry(entry, options=options)
    connection.send_result(msg["id"], {"saved": True})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/status",
        vol.Optional("address"): str,
    }
)
@websocket_api.async_response
async def _ws_status(hass, connection, msg) -> None:
    """Stato corrente: timer/rotazione in corso e configurazione."""
    try:
        entry = _find_entry(hass, msg.get("address"))
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "not_found", str(err))
        return

    mode = hass.data.get(f"{DOMAIN}_timer_mode", {}).get(entry.entry_id)
    running = entry.entry_id in hass.data.get(f"{DOMAIN}_timers", {})
    connection.send_result(
        msg["id"],
        {
            "running": running,
            "mode": mode if running else None,
            "owner": entry.options.get("display_owner"),
            "rot_anim": entry.options.get("rot_anim"),
            "rot_comp_s": entry.options.get("rot_comp_s", 300),
            "rot_comp_per_ent": entry.options.get("rot_comp_per_ent", False),
            "rot_anim_s": entry.options.get("rot_anim_s", 60),
            "rot_clock_s": entry.options.get("rot_clock_s", 60),
            "rot_use_sensors": entry.options.get("rot_use_sensors", True),
            "rot_use_anim": entry.options.get("rot_use_anim", True),
            "rot_use_clock": entry.options.get("rot_use_clock", False),
            "rot_playlist": entry.options.get("rot_playlist") or [],
            "schedule_cfg": entry.options.get("schedule_cfg") or {},
            "device_slots": entry.options.get("device_slots") or {},
            "clock_style": entry.options.get("clock_it_style", "a"),
            "clock_size": entry.options.get("clock_size", "m"),
            "clock_gap": entry.options.get("clock_gap", 2),
            "clock_day_size": entry.options.get("clock_day_size", ""),
            "clock_icon": entry.options.get("clock_icon", "heart"),
            "clock_icon_color": entry.options.get("clock_icon_color", "ff0000"),
            "clock_color": entry.options.get("clock_color", ""),
            "released_s": (
                api.released_seconds_left
                if (api := hass.data.get(DOMAIN, {}).get(entry.entry_id))
                else 0
            ),
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/library",
        vol.Required("action"): vol.In(["list", "show", "delete"]),
        vol.Optional("file"): cv.string,
        vol.Optional("address"): str,
    }
)
@websocket_api.async_response
async def _ws_library(hass, connection, msg) -> None:
    """Gestisce la libreria personale di immagini."""
    lib = _library_dir(hass)
    action = msg["action"]

    if action == "list":
        def _list() -> list[dict]:
            if not lib.exists():
                return []
            files = sorted(
                (p for p in lib.iterdir() if p.is_file()),
                key=lambda p: p.stat().st_mtime, reverse=True,
            )
            return [{"file": p.name} for p in files]

        connection.send_result(
            msg["id"], {"items": await hass.async_add_executor_job(_list)}
        )
        return

    fname = Path(msg.get("file") or "").name
    target = lib / fname
    if not fname or not target.exists():
        connection.send_error(msg["id"], "not_found", "File non trovato")
        return

    if action == "delete":
        await hass.async_add_executor_job(target.unlink)
        connection.send_result(msg["id"], {"deleted": True})
        return

    # action == "show": rimanda il contenuto al display
    try:
        entry = _find_entry(hass, msg.get("address"))
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "not_found", str(err))
        return

    data = await hass.async_add_executor_job(target.read_bytes)
    ext = target.suffix or ".png"
    _cancel_timer(hass, entry.entry_id)
    api = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    ok = False
    if api:
        ok = await api.display_image(
            image_bytes=data, file_extension=ext, resize_method="crop"
        )
        if ok:
            _set_override(hass, entry.entry_id)
            await hass.async_add_executor_job(
                Path(_last_image_path(hass, entry.entry_id)).write_bytes, data
            )
            await hass.async_add_executor_job(target.touch)  # in cima ai recenti
            _remember_owner(hass, entry, "image", last_ext=ext, last_resize="crop")
    connection.send_result(msg["id"], {"displayed": ok})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/show_text",
        vol.Optional("address"): str,
        vol.Required("text"): str,
        vol.Optional("animation", default=0): vol.All(int, vol.Range(min=0, max=7)),
        vol.Optional("speed", default=50): vol.All(int, vol.Range(min=0, max=100)),
    }
)
@websocket_api.async_response
async def _ws_show_text(hass, connection, msg) -> None:
    """Show a one-off text on the display (does not touch the composition)."""
    try:
        entry = _find_entry(hass, msg.get("address"))
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "not_found", str(err))
        return

    _cancel_timer(hass, entry.entry_id)
    api = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    ok = False
    if api:
        # Rispetta font e grandezza scelti nella pagina Testo
        from .common import get_entity_id_by_unique_id

        address = entry.data[CONF_ADDRESS]
        font = None
        char_height = None
        eid = get_entity_id_by_unique_id(hass, address, "font_select", "select")
        st = hass.states.get(eid) if eid else None
        if st and st.state not in ("unknown", "unavailable", ""):
            font = st.state
        eid = get_entity_id_by_unique_id(hass, address, "font_size", "number")
        st = hass.states.get(eid) if eid else None
        if st:
            try:
                v = int(float(st.state))
                char_height = v if v > 0 else None
            except (ValueError, TypeError):
                pass
        ok = await api.display_text_pypixelcolor(
            text=msg["text"], color="ffffff", font=font,
            animation=msg["animation"], speed=msg["speed"],
            char_height=char_height,
        )
        if ok:
            _set_override(hass, entry.entry_id)
            _remember_owner(
                hass, entry, "text",
                last_text=msg["text"],
                last_anim=msg["animation"], last_speed=msg["speed"],
            )
    connection.send_result(msg["id"], {"displayed": ok})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/set_panel_size",
        vol.Optional("address"): str,
        vol.Required("width"): vol.All(int, vol.Range(min=8, max=256)),
        vol.Required("height"): vol.All(int, vol.Range(min=8, max=64)),
        vol.Optional("reset", default=False): bool,
    }
)
@websocket_api.async_response
async def _ws_set_panel_size(hass, connection, msg) -> None:
    """Override (or reset) the panel dimensions used for rendering."""
    try:
        entry = _find_entry(hass, msg.get("address"))
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "not_found", str(err))
        return

    options = dict(entry.options)
    if msg["reset"]:
        options.pop("panel_width", None)
        options.pop("panel_height", None)
    else:
        options["panel_width"] = msg["width"]
        options["panel_height"] = msg["height"]
    hass.config_entries.async_update_entry(entry, options=options)

    api = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if api:
        if msg["reset"]:
            api.set_size_override(None, None)
        else:
            api.set_size_override(msg["width"], msg["height"])

    connection.send_result(msg["id"], {"saved": True})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/set_composition",
        vol.Optional("address"): str,
        vol.Required("items"): list,
        vol.Optional("separator"): vol.All(int, vol.Range(min=1, max=4)),
    }
)
@websocket_api.async_response
async def _ws_set_composition(hass, connection, msg) -> None:
    """Save the composition, refresh tracking and update the display."""
    from .common import update_ipixel_display

    try:
        entry = _find_entry(hass, msg.get("address"))
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "not_found", str(err))
        return

    _cancel_timer(hass, entry.entry_id)
    _clear_override(hass, entry.entry_id)
    items = msg["items"]
    options = dict(entry.options)
    options[OPTION_KEY] = items
    options["display_owner"] = "composition"
    if "separator" in msg:
        options["comp_sep"] = msg["separator"]
    hass.config_entries.async_update_entry(entry, options=options)

    _setup_composition_tracking(hass, entry)

    # Se il display era in modalità orologio, riportalo al testo:
    # altrimenti "Invia al display" riattiverebbe l'orologio
    from .common import get_entity_id_by_unique_id

    mode_eid = get_entity_id_by_unique_id(
        hass, entry.data[CONF_ADDRESS], "mode_select", "select"
    )
    mode_state = hass.states.get(mode_eid) if mode_eid else None
    if mode_state and mode_state.state == "clock":
        await hass.services.async_call(
            "select", "select_option",
            {"entity_id": mode_eid, "option": "text"},
            blocking=True,
        )

    api = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    success = False
    if api:
        name = entry.data.get(CONF_NAME, entry.title)
        success = await update_ipixel_display(hass, name, api)

    connection.send_result(msg["id"], {"saved": True, "displayed": success})


async def _async_register_services(hass: HomeAssistant) -> None:
    """Register integration-level services (once)."""
    if hass.services.has_service(DOMAIN, SERVICE_DISPLAY_IMAGE):
        return

    async def handle_display_image(call: ServiceCall) -> None:
        api = _find_api(hass, call.data.get("address"))
        matched = None
        for entry in hass.config_entries.async_entries(DOMAIN):
            if entry.data.get(CONF_ADDRESS, "").lower() == api.address.lower():
                matched = entry
                _cancel_timer(hass, entry.entry_id)
                break
        try:
            image_bytes = base64.b64decode(call.data["image"])
        except Exception as err:
            raise HomeAssistantError(f"Invalid base64 image: {err}") from err

        ext = call.data.get("file_extension", ".png")
        resize = call.data.get("resize_method", "crop")
        success = await api.display_image(
            image_bytes=image_bytes,
            file_extension=ext,
            resize_method=resize,
        )
        if not success:
            raise HomeAssistantError("Failed to send image to the display")

        # Ricorda l'immagine per ripristinarla dopo un riavvio + libreria
        if matched:
            _set_override(hass, matched.entry_id)
            await hass.async_add_executor_job(
                Path(_last_image_path(hass, matched.entry_id)).write_bytes,
                image_bytes,
            )
            await hass.async_add_executor_job(
                _save_to_library, hass, image_bytes, ext
            )
            _remember_owner(hass, matched, "image", last_ext=ext, last_resize=resize)

    hass.services.async_register(
        DOMAIN,
        SERVICE_DISPLAY_IMAGE,
        handle_display_image,
        schema=SERVICE_DISPLAY_IMAGE_SCHEMA,
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up iPIXEL Color from a config entry."""
    address = entry.data[CONF_ADDRESS]
    name = entry.data[CONF_NAME]

    _LOGGER.debug("Setting up iPIXEL Color for %s (%s)", name, address)

    # Adattatore BLE preferito (select "Adattatore BLE"): va in hass.data
    # PRIMA della connessione, così anche il primo collegamento lo usa.
    hass.data.setdefault(DOMAIN, {}).setdefault("pref_adapter", {})[address] = (
        entry.options.get("preferred_adapter")
    )

    api = iPIXELAPI(hass, address)

    # Override dimensioni pannello salvato dall'utente (firmware bugiardi)
    if entry.options.get("panel_width") and entry.options.get("panel_height"):
        api.set_size_override(
            entry.options["panel_width"], entry.options["panel_height"]
        )

    try:
        if not await api.connect():
            raise ConfigEntryNotReady(
                f"Failed to connect to iPIXEL device at {address}"
            )

        _LOGGER.info("Successfully connected to iPIXEL device %s", address)

        # Get device info for sensors (retried internally if it fails)
        await api.get_device_info()

    except iPIXELTimeoutError as err:
        _LOGGER.error("Connection timeout to iPIXEL device %s: %s", address, err)
        raise ConfigEntryNotReady(f"Connection timeout: {err}") from err

    except iPIXELConnectionError as err:
        _LOGGER.error("Failed to connect to iPIXEL device %s: %s", address, err)
        raise ConfigEntryNotReady(f"Connection failed: {err}") from err

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = api
    entry.runtime_data = api

    # Chiusura pulita: ferma subito timer/orologio e scollega il display,
    # così la scrittura finale degli stati non viene ritardata e al
    # riavvio non si ripristinano istantanee vecchie delle impostazioni
    async def _on_ha_stop(event) -> None:
        _cancel_timer(hass, entry.entry_id)
        try:
            await api.disconnect()
        except Exception:  # pragma: no cover
            pass

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _on_ha_stop)
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Static files for the sidebar panel (idempotent)
    if not hass.data.get(f"{DOMAIN}_static_registered"):
        panel_dir = str(Path(__file__).parent / "panel")
        fonts_dir = str(Path(__file__).parent / "fonts")
        lib_dir = _library_dir(hass)
        await hass.async_add_executor_job(
            lambda: lib_dir.mkdir(parents=True, exist_ok=True)
        )
        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(PANEL_STATIC_PATH, panel_dir, cache_headers=False),
                StaticPathConfig("/ipixel_color_fonts", fonts_dir, cache_headers=True),
                StaticPathConfig("/ipixel_color_library", str(lib_dir), cache_headers=False),
            ]
        )
        hass.data[f"{DOMAIN}_static_registered"] = True

    # Endpoint HTTP di upload (byte grezzi, oltre il limite 4 MiB del WS)
    if not hass.data.get(f"{DOMAIN}_view_registered"):
        hass.http.register_view(IpixelUploadView(hass))
        hass.data[f"{DOMAIN}_view_registered"] = True

    await _async_register_services(hass)

    # WebSocket API for the panel (idempotent)
    if not hass.data.get(f"{DOMAIN}_ws_registered"):
        websocket_api.async_register_command(hass, _ws_get_composition)
        websocket_api.async_register_command(hass, _ws_set_composition)
        websocket_api.async_register_command(hass, _ws_set_panel_size)
        websocket_api.async_register_command(hass, _ws_timer)
        websocket_api.async_register_command(hass, _ws_show_text)
        websocket_api.async_register_command(hass, _ws_display_url)
        websocket_api.async_register_command(hass, _ws_library)
        websocket_api.async_register_command(hass, _ws_status)
        websocket_api.async_register_command(hass, _ws_set_clock_style)
        websocket_api.async_register_command(hass, _ws_show_icon)
        websocket_api.async_register_command(hass, _ws_show_free_text)
        websocket_api.async_register_command(hass, _ws_set_playlist)
        websocket_api.async_register_command(hass, _ws_native_timer)
        websocket_api.async_register_command(hass, _ws_scores)
        websocket_api.async_register_command(hass, _ws_schedule)
        websocket_api.async_register_command(hass, _ws_rhythm)
        websocket_api.async_register_command(hass, _ws_fun_mode)
        websocket_api.async_register_command(hass, _ws_pixel)
        websocket_api.async_register_command(hass, _ws_slot)
        websocket_api.async_register_command(hass, _ws_frame)
        websocket_api.async_register_command(hass, _ws_preview_segment)
        websocket_api.async_register_command(hass, _ws_release)
        hass.data[f"{DOMAIN}_ws_registered"] = True

    _setup_composition_tracking(hass, entry)
    await _async_update_panel(hass)

    # Ripristina sul display l'ultimo contenuto scelto, dopo un riavvio
    async def _restore_last_content() -> None:
        await asyncio.sleep(8)  # lascia assestare la connessione BLE
        # Se l'utente aveva spento il display, rispetta la scelta
        from .common import get_entity_id_by_unique_id

        power_eid = get_entity_id_by_unique_id(hass, address, "power", "switch")
        power_state = hass.states.get(power_eid) if power_eid else None
        if power_state and power_state.state == "off":
            _LOGGER.debug("Display spento: nessun ripristino del contenuto")
            return
        owner = entry.options.get("display_owner")
        try:
            if owner == "image":
                path = Path(_last_image_path(hass, entry.entry_id))
                if path.exists():
                    _set_override(hass, entry.entry_id)
                    data = await hass.async_add_executor_job(path.read_bytes)
                    await api.display_image(
                        image_bytes=data,
                        file_extension=entry.options.get("last_ext", ".gif"),
                        resize_method=entry.options.get("last_resize", "crop"),
                    )
                    _LOGGER.info("Ultima immagine ripristinata sul display")
            elif owner == "text":
                _set_override(hass, entry.entry_id)
                await api.display_text_pypixelcolor(
                    text=entry.options.get("last_text", ""),
                    color="ffffff", font=None,
                    animation=entry.options.get("last_anim", 0),
                    speed=entry.options.get("last_speed", 50),
                )
                _LOGGER.info("Ultimo testo ripristinato sul display")
            elif owner in ("rotation", "clock_it"):
                if entry.entry_id in hass.data.get(f"{DOMAIN}_timers", {}):
                    # L'utente ha gia' avviato qualcosa dal pannello in
                    # questi 8 secondi: vince lui, niente ripristino
                    return
                _set_override(hass, entry.entry_id)
                task = hass.async_create_background_task(
                    _timer_loop(hass, entry.entry_id, owner, 0),
                    "ipixel rotazione",
                    eager_start=False,
                )
                hass.data.setdefault(f"{DOMAIN}_timers", {})[entry.entry_id] = task
                hass.data.setdefault(f"{DOMAIN}_timer_mode", {})[
                    entry.entry_id
                ] = owner
                _LOGGER.info(
                    "%s ripreso dopo il riavvio",
                    "Rotazione" if owner == "rotation" else "Orologio italiano",
                )
            elif owner == "composition" and (entry.options.get(OPTION_KEY) or []):
                from .common import update_ipixel_display

                await update_ipixel_display(
                    hass, entry.data.get(CONF_NAME, entry.title), api
                )
                _LOGGER.info("Composizione ripristinata sul display")
        except Exception as err:  # pragma: no cover
            _LOGGER.warning("Ripristino contenuto dopo riavvio fallito: %s", err)

    entry.async_create_background_task(
        hass, _restore_last_content(), "ipixel_restore_last_content"
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug("Unloading iPIXEL Color integration")

    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        _cancel_timer(hass, entry.entry_id)
        if unsub := hass.data.get(f"{DOMAIN}_tracking", {}).pop(entry.entry_id, None):
            unsub()
        api: iPIXELAPI = hass.data[DOMAIN].pop(entry.entry_id)
        try:
            await api.disconnect()
            _LOGGER.debug("Disconnected from iPIXEL device")
        except Exception as err:
            _LOGGER.error("Error disconnecting from device: %s", err)

        await _async_update_panel(hass)

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
