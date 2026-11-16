"""Build display text from a user-defined composition of entities.

A composition is a list of items saved in the config entry options:
  {"type": "entity", "entity_id": "...", "label": "BAGNO",
   "decimals": 1, "show_unit": false}
  {"type": "text", "text": "CIAO"}

The panel edits this list; no Jinja knowledge required.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

OPTION_KEY = "composition"
DEFAULT_SEPARATOR = "  "

# Emoji per tipo di sensore: sul display viaggiano come blocchi
# immagine Twemoji (gia' supportati dal testo scorrevole), cosi' ogni
# casella si porta dietro la sua icona come nel pannello.
_CLASS_EMOJI = {
    "temperature": "🌡",
    "humidity": "💧",
    "power": "⚡",
    "energy": "⚡",
    "voltage": "⚡",
    "current": "⚡",
    "battery": "🔋",
    "illuminance": "☀",
    "pm25": "🌫",
    "co2": "🌫",
    "carbon_dioxide": "🌫",
}

_DOMAIN_EMOJI = {
    "light": "💡",
    "switch": "🔌",
    "person": "🧍",
    "device_tracker": "🧍",
    "climate": "🌡",
    "fan": "🌀",
    "media_player": "🎵",
    "cover": "🪟",
    "lock": "🔒",
}


def default_icon_for(entity_id: str, state) -> str:
    """L'emoji 'corrispondente' di un'entità (come l'icona della casella)."""
    if state is not None:
        device_class = state.attributes.get("device_class")
        if device_class in _CLASS_EMOJI:
            return _CLASS_EMOJI[device_class]
        unit = state.attributes.get("unit_of_measurement") or ""
        if "°" in unit:
            return "🌡"
        if unit == "%":
            return "💧"
        if unit.upper() in ("W", "KW", "WH", "KWH", "V", "A"):
            return "⚡"
    domain = entity_id.split(".", 1)[0] if "." in entity_id else ""
    return _DOMAIN_EMOJI.get(domain, "")


def composition_entity_ids(items: list[dict[str, Any]]) -> list[str]:
    """Entity ids referenced by the ENABLED items of a composition."""
    return [
        item["entity_id"]
        for item in items
        if item.get("type", "entity") == "entity"
        and item.get("entity_id")
        and item.get("enabled", True)
    ]


def build_text(
    hass: HomeAssistant,
    items: list[dict[str, Any]],
    separator: str = DEFAULT_SEPARATOR,
) -> str:
    """Render a composition to the final display string.

    Numeric values are rounded and use the Italian decimal comma.
    """
    parts: list[str] = []
    for item in items:
        if not item.get("enabled", True):
            continue
        kind = item.get("type", "entity")

        if kind == "text":
            text = (item.get("text") or "").strip()
            if text:
                parts.append(text)
            continue

        entity_id = item.get("entity_id")
        if not entity_id:
            continue
        state = hass.states.get(entity_id)

        value = "?"
        if state and state.state not in ("unknown", "unavailable", ""):
            value = state.state
            decimals = item.get("decimals", 1)
            try:
                number = float(value)
                value = f"{number:.{int(decimals)}f}".replace(".", ",")
            except (ValueError, TypeError):
                pass

        unit = ""
        if item.get("show_unit") and state:
            unit = state.attributes.get("unit_of_measurement") or ""
            # Il simbolo dei gradi viene reso male dal display
            unit = unit.replace("°", "").strip()

        label = (item.get("label") or "").strip()
        segment = f"{label} {value}" if label else value
        if unit:
            segment = f"{segment}{unit}"

        # Icona della casella: chiave assente = automatica dal tipo di
        # sensore; stringa vuota = l'utente l'ha tolta apposta
        icon = item.get("icon")
        if icon is None:
            icon = default_icon_for(entity_id, state)
        icon = (icon or "").strip()
        if icon:
            segment = f"{icon} {segment}"
        parts.append(segment)

    return separator.join(parts)


def has_custom_segments(items: list[dict[str, Any]]) -> bool:
    """True se qualche casella ha grandezza o posizione proprie."""
    for item in items:
        if not item.get("enabled", True):
            continue
        if item.get("size") or (item.get("valign") or "center") != "center":
            return True
    return False


def build_segments(
    hass: HomeAssistant,
    items: list[dict[str, Any]],
    separator: str = DEFAULT_SEPARATOR,
    default_color: str = "ffffff",
    default_size: int | None = None,
) -> list[dict[str, Any]]:
    """Come build_text, ma un segmento per casella: ognuno con la sua
    grandezza ("size", px) e posizione verticale ("valign")."""
    segments: list[dict[str, Any]] = []
    enabled = [it for it in items if it.get("enabled", True)]
    for idx, item in enumerate(enabled):
        kind = item.get("type", "entity")

        if kind == "text":
            text = (item.get("text") or "").strip()
        else:
            entity_id = item.get("entity_id")
            if not entity_id:
                continue
            state = hass.states.get(entity_id)
            value = "?"
            if state and state.state not in ("unknown", "unavailable", ""):
                value = state.state
                decimals = item.get("decimals", 1)
                try:
                    number = float(value)
                    value = f"{number:.{int(decimals)}f}".replace(".", ",")
                except (ValueError, TypeError):
                    pass
            unit = ""
            if item.get("show_unit") and state:
                unit = (state.attributes.get("unit_of_measurement") or "")
                unit = unit.replace("°", "").strip()
            label = (item.get("label") or "").strip()
            text = f"{label} {value}" if label else value
            if unit:
                text = f"{text}{unit}"
            icon = item.get("icon")
            if icon is None:
                icon = default_icon_for(entity_id, state)
            icon = (icon or "").strip()
            if icon:
                text = f"{icon} {text}"

        if not text:
            continue
        if idx < len(enabled) - 1:
            text += separator
        segments.append({
            "text": text,
            "color": item.get("color") or default_color,
            "size": int(item.get("size") or default_size or 0) or None,
            "valign": item.get("valign") or "center",
        })
    return segments
