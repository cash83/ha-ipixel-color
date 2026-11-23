"""Select entity for iPIXEL Color font selection."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from homeassistant.components import bluetooth
from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.restore_state import RestoreEntity

from .api import iPIXELAPI
from .bluetooth.client import scanner_label
from .const import DOMAIN, CONF_ADDRESS, CONF_NAME, AVAILABLE_MODES, DEFAULT_MODE
from .common import get_entity_id_by_unique_id
from .common import update_ipixel_display
from .fonts import get_available_fonts

ADAPTER_AUTO = "Automatico"

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the iPIXEL Color select entities."""
    address = entry.data[CONF_ADDRESS]
    name = entry.data[CONF_NAME]
    
    api = hass.data[DOMAIN][entry.entry_id]

    # La scansione delle cartelle dei font e' I/O: fuori dal loop eventi
    fonts = await hass.async_add_executor_job(get_available_fonts)

    async_add_entities([
        iPIXELFontSelect(hass, api, entry, address, name, fonts),
        iPIXELModeSelect(hass, api, entry, address, name),
        iPIXELClockStyleSelect(hass, api, entry, address, name),
        iPIXELAdapterSelect(hass, entry, address, name),
    ])


class iPIXELFontSelect(SelectEntity, RestoreEntity):
    """Representation of an iPIXEL Color font selection."""

    def __init__(
        self, 
        hass: HomeAssistant,
        api: iPIXELAPI, 
        entry: ConfigEntry,
        address: str,
        name: str,
        fonts: list[str] | None = None,
    ) -> None:
        """Initialize the font select."""
        self.hass = hass
        self._api = api
        self._entry = entry
        self._address = address
        self._name = name
        self._attr_name = "Font"
        self._attr_unique_id = f"{address}_font_select"
        self._attr_entity_description = "Select font for text display"

        # Elenco font passato dal setup (scansione fatta in executor)
        self._attr_options = fonts or get_available_fonts()
        self._attr_current_option = "OpenSans-Light.ttf" if "OpenSans-Light.ttf" in self._attr_options else self._attr_options[0]
        
        # Device info for grouping in device registry
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, address)},
            name=name,
            manufacturer="iPIXEL",
            model="LED Matrix Display",
            sw_version="1.0",
        )

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""
        await super().async_added_to_hass()
        
        # Restore last state if available
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state in self._attr_options:
            self._attr_current_option = last_state.state
            _LOGGER.debug("Restored font selection: %s", self._attr_current_option)

    @property
    def current_option(self) -> str | None:
        """Return the current selected font."""
        return self._attr_current_option

    async def async_select_option(self, option: str) -> None:
        """Select a font option."""
        if option in self._attr_options:
            self._attr_current_option = option
            _LOGGER.debug("Font changed to: %s", option)
            
            # Trigger display update if auto-update is enabled
            await self._trigger_auto_update()
        else:
            _LOGGER.error("Invalid font option: %s", option)

    async def _trigger_auto_update(self) -> None:
        """Trigger display update if auto-update is enabled."""
        try:
            # Check auto-update setting
            auto_update_entity_id = get_entity_id_by_unique_id(self.hass, self._address, "auto_update", "switch")
            auto_update_state = self.hass.states.get(auto_update_entity_id) if auto_update_entity_id else None
            
            if auto_update_state and auto_update_state.state == "on":
                # Use common update function directly
                await update_ipixel_display(self.hass, self._name, self._api)
                _LOGGER.debug("Auto-update triggered display refresh due to font change")
        except Exception as err:
            _LOGGER.debug("Could not trigger auto-update: %s", err)

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return True


class iPIXELModeSelect(SelectEntity, RestoreEntity):
    """Representation of an iPIXEL Color mode selection."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: iPIXELAPI,
        entry: ConfigEntry,
        address: str,
        name: str
    ) -> None:
        """Initialize the mode select."""
        self.hass = hass
        self._api = api
        self._entry = entry
        self._address = address
        self._name = name
        self._attr_name = "Mode"
        self._attr_unique_id = f"{address}_mode_select"
        self._attr_entity_description = "Select display mode (textimage, clock, rhythm, fun)"

        # Set available mode options
        self._attr_options = AVAILABLE_MODES
        self._attr_current_option = DEFAULT_MODE

        # Device info for grouping in device registry
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, address)},
            name=name,
            manufacturer="iPIXEL",
            model="LED Matrix Display",
            sw_version="1.0",
        )

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""
        await super().async_added_to_hass()

        # Restore last state if available
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state in self._attr_options:
            self._attr_current_option = last_state.state
            _LOGGER.debug("Restored mode selection: %s", self._attr_current_option)

    @property
    def current_option(self) -> str | None:
        """Return the current selected mode."""
        return self._attr_current_option

    async def async_select_option(self, option: str) -> None:
        """Select a mode option."""
        if option in self._attr_options:
            self._attr_current_option = option
            _LOGGER.info("Mode changed to: %s", option)

            # Trigger display update if auto-update is enabled
            await self._trigger_auto_update()
        else:
            _LOGGER.error("Invalid mode option: %s", option)

    async def _trigger_auto_update(self) -> None:
        """Trigger display update if auto-update is enabled."""
        try:
            # Check auto-update setting
            auto_update_entity_id = get_entity_id_by_unique_id(self.hass, self._address, "auto_update", "switch")
            auto_update_state = self.hass.states.get(auto_update_entity_id) if auto_update_entity_id else None

            if auto_update_state and auto_update_state.state == "on":
                # Use common update function directly
                await update_ipixel_display(self.hass, self._name, self._api)
                _LOGGER.debug("Auto-update triggered display refresh due to mode change")
        except Exception as err:
            _LOGGER.debug("Could not trigger auto-update: %s", err)

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return True


class iPIXELClockStyleSelect(SelectEntity, RestoreEntity):
    """Representation of an iPIXEL Color clock style selection."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: iPIXELAPI,
        entry: ConfigEntry,
        address: str,
        name: str
    ) -> None:
        """Initialize the clock style select."""
        self.hass = hass
        self._api = api
        self._entry = entry
        self._address = address
        self._name = name
        self._attr_name = "Clock Style"
        self._attr_unique_id = f"{address}_clock_style_select"
        self._attr_entity_description = "Select clock display style (0-8)"

        # Clock styles 0-8
        self._attr_options = ["0", "1", "2", "3", "4", "5", "6", "7", "8"]
        self._attr_current_option = "1"  # Default style

        # Device info for grouping in device registry
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, address)},
            name=name,
            manufacturer="iPIXEL",
            model="LED Matrix Display",
            sw_version="1.0",
        )

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""
        await super().async_added_to_hass()

        # Restore last state if available
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state in self._attr_options:
            self._attr_current_option = last_state.state
            _LOGGER.debug("Restored clock style selection: %s", self._attr_current_option)

    @property
    def current_option(self) -> str | None:
        """Return the current selected clock style."""
        return self._attr_current_option

    async def async_select_option(self, option: str) -> None:
        """Select a clock style option."""
        if option in self._attr_options:
            self._attr_current_option = option
            _LOGGER.info("Clock style changed to: %s", option)

            # Trigger display update if auto-update is enabled and in clock mode
            await self._trigger_auto_update()
        else:
            _LOGGER.error("Invalid clock style option: %s", option)

    async def _trigger_auto_update(self) -> None:
        """Trigger display update if auto-update is enabled and in clock mode."""
        try:
            # Check if we're in clock mode
            mode_entity_id = get_entity_id_by_unique_id(self.hass, self._address, "mode_select", "select")
            mode_state = self.hass.states.get(mode_entity_id) if mode_entity_id else None

            if mode_state and mode_state.state == "clock":
                # Check auto-update setting
                auto_update_entity_id = get_entity_id_by_unique_id(self.hass, self._address, "auto_update", "switch")
                auto_update_state = self.hass.states.get(auto_update_entity_id) if auto_update_entity_id else None

                if auto_update_state and auto_update_state.state == "on":
                    # Use common update function directly
                    await update_ipixel_display(self.hass, self._name, self._api)
                    _LOGGER.debug("Auto-update triggered display refresh due to clock style change")
        except Exception as err:
            _LOGGER.debug("Could not trigger auto-update: %s", err)

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return True


class iPIXELAdapterSelect(SelectEntity):
    """Scelta dell'adattatore BLE con cui collegare il display.

    "Automatico" = lo decide HA (segnale migliore). Scegliendo un
    adattatore/proxy preciso (es. il Bluetooth del box) il collegamento
    viene forzato su quello: utile perché via proxy ESPHome i pacchetti
    grandi si perdono. Vedi il sensore "Segnale BLE" per capire quale ha
    il segnale migliore.
    """

    _attr_icon = "mdi:bluetooth-connect"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_should_poll = True  # ricalcola i dBm nella tendina ogni giro

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, address: str, name: str) -> None:
        self.hass = hass
        self._entry = entry
        self._address = address
        self._name = name
        self._attr_name = "Adattatore BLE"
        self._attr_unique_id = f"{address}_ble_adapter"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, address)},
            name=name,
            manufacturer="iPIXEL",
            model="LED Matrix Display",
        )
        # Snapshot (aggiornato a ogni poll): tiene stabili le etichette
        # mentre l'utente apre la tendina e sceglie, così HA non rifiuta
        # l'opzione perché nel frattempo il dBm è cambiato.
        self._cached_options: list[str] = [ADAPTER_AUTO]
        self._cached_current: str = ADAPTER_AUTO

    def _scanner_rssi(self) -> dict[str, int | None]:
        """{nome adattatore: RSSI migliore} di chi vede ora il display."""
        out: dict[str, int | None] = {}
        try:
            for d in bluetooth.async_scanner_devices_by_address(
                self.hass, self._address, connectable=True
            ):
                lbl = scanner_label(d.scanner)
                r = getattr(d.advertisement, "rssi", None)
                if lbl not in out or (
                    r is not None and (out[lbl] is None or r > out[lbl])
                ):
                    out[lbl] = r
        except Exception:  # pragma: no cover - best effort
            pass
        return out

    @staticmethod
    def _label(name: str, rssi: int | None) -> str:
        # etichetta MOSTRATA (col segnale); il valore SALVATO resta il nome
        return f"{name} · {rssi} dBm" if rssi is not None else name

    @staticmethod
    def _stable_name(option: str) -> str:
        # recupera il nome stabile dall'etichetta col segnale
        if option != ADAPTER_AUTO and option.endswith(" dBm") and " · " in option:
            return option.rsplit(" · ", 1)[0]
        return option

    def _recompute(self) -> None:
        """Rigenera lo snapshot di opzioni/selezione (coi dBm freschi)."""
        seen = self._scanner_rssi()
        labels = [self._label(n, r) for n, r in seen.items()]
        pref = self._entry.options.get("preferred_adapter")
        if pref and pref not in seen:
            labels.append(pref)  # scegliibile anche se ora non in vista
        self._cached_options = [ADAPTER_AUTO] + labels
        if not pref:
            self._cached_current = ADAPTER_AUTO
        elif pref in seen:
            self._cached_current = self._label(pref, seen[pref])
        else:
            self._cached_current = pref

    @property
    def options(self) -> list[str]:
        return self._cached_options

    @property
    def current_option(self) -> str:
        return self._cached_current

    @property
    def available(self) -> bool:
        return True

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._recompute()  # pronto subito, senza aspettare il primo poll

    async def async_update(self) -> None:
        self._recompute()

    async def async_select_option(self, option: str) -> None:
        name = self._stable_name(option)
        options = dict(self._entry.options)
        if name == ADAPTER_AUTO:
            options.pop("preferred_adapter", None)
            chosen = None
        else:
            options["preferred_adapter"] = name
            chosen = name
        self.hass.config_entries.async_update_entry(self._entry, options=options)
        # subito disponibile al client, poi reload per riconnettere via quello
        self.hass.data.setdefault(DOMAIN, {}).setdefault("pref_adapter", {})[
            self._address
        ] = chosen
        self.async_write_ha_state()
        _LOGGER.info("Adattatore BLE scelto: %s", name)
        await self.hass.config_entries.async_reload(self._entry.entry_id)