"""Sensor platform for iPIXEL Color device information."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorDeviceClass,
)
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo, EntityCategory

from .api import iPIXELAPI, iPIXELConnectionError
from .bluetooth.client import scanner_label
from .const import DOMAIN, CONF_ADDRESS, CONF_NAME

_LOGGER = logging.getLogger(__name__)

SENSOR_DESCRIPTIONS = [
    SensorEntityDescription(
        key="width",
        name="Display Width",
        icon="mdi:monitor",
        native_unit_of_measurement="px",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="height", 
        name="Display Height",
        icon="mdi:monitor",
        native_unit_of_measurement="px",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="device_type_str",
        name="Device Type",
        icon="mdi:chip",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="mcu_version",
        name="MCU Version",
        icon="mdi:memory",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="wifi_version",
        name="WiFi Version", 
        icon="mdi:wifi",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the iPIXEL Color sensors."""
    address = entry.data[CONF_ADDRESS]
    name = entry.data[CONF_NAME]
    
    api = hass.data[DOMAIN][entry.entry_id]
    
    # Create sensor entities
    sensors = []
    for description in SENSOR_DESCRIPTIONS:
        sensors.append(iPIXELSensor(api, entry, address, name, description))
    sensors.append(iPIXELSignalSensor(hass, entry, address, name))

    async_add_entities(sensors)


class iPIXELSensor(SensorEntity):
    """Representation of an iPIXEL Color sensor."""

    def __init__(
        self, 
        api: iPIXELAPI, 
        entry: ConfigEntry, 
        address: str, 
        name: str,
        description: SensorEntityDescription
    ) -> None:
        """Initialize the sensor."""
        self._api = api
        self._entry = entry
        self._address = address
        self._device_name = name
        self.entity_description = description
        
        self._attr_name = description.name
        self._attr_unique_id = f"{address}_{description.key}"
        self._attr_native_value = None
        self._available = True

        # Device info for grouping in device registry
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, address)},
            name=name,
            manufacturer="iPIXEL",
            model="LED Matrix Display",
            sw_version="1.0",
        )

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._available

    async def async_update(self) -> None:
        """Update the sensor state."""
        try:
            if not self._api.is_connected:
                self._available = False
                return
                
            device_info = await self._api.get_device_info()
            if device_info:
                self._attr_native_value = device_info.get(self.entity_description.key)
                self._available = True
            else:
                self._available = False
                
        except iPIXELConnectionError:
            _LOGGER.debug("Device not connected, marking sensor as unavailable")
            self._available = False
        except Exception as err:
            _LOGGER.error("Error updating sensor %s: %s", self.entity_description.key, err)
            self._available = False


class iPIXELSignalSensor(SensorEntity):
    """Segnale BLE (RSSI) del display e quali adattatori lo vedono.

    Serve a decidere quale adattatore scegliere nel select "Adattatore BLE":
    mostra il segnale più forte come stato e, negli attributi, l'elenco di
    tutti gli adattatori/proxy che vedono il display con il loro RSSI.
    """

    _attr_has_entity_name = False

    def __init__(self, hass, entry, address, name) -> None:
        self.hass = hass
        self._entry = entry
        self._address = address
        self._attr_name = "Segnale BLE"
        self._attr_unique_id = f"{address}_ble_signal"
        self._attr_native_unit_of_measurement = "dBm"
        self._attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
        self._attr_icon = "mdi:bluetooth-audio"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_native_value = None
        self._attr_extra_state_attributes = {}
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, address)},
            name=name,
            manufacturer="iPIXEL",
            model="LED Matrix Display",
        )

    async def async_update(self) -> None:
        best = None
        adattatori: dict[str, int] = {}
        try:
            devs = bluetooth.async_scanner_devices_by_address(
                self.hass, self._address, connectable=True
            )
        except Exception:  # pragma: no cover - best effort
            devs = []
        for d in devs:
            rssi = getattr(d.advertisement, "rssi", None)
            lbl = scanner_label(d.scanner)
            if rssi is not None:
                # tiene il migliore per adattatore
                if lbl not in adattatori or rssi > adattatori[lbl]:
                    adattatori[lbl] = rssi
                if best is None or rssi > best:
                    best = rssi
        self._attr_native_value = best
        pref = self._entry.options.get("preferred_adapter")
        self._attr_extra_state_attributes = {
            "adattatore_scelto": pref or "Automatico",
            "adattatori": adattatori,  # {nome: rssi dBm}
            "adattatori_in_vista": len(adattatori),
        }