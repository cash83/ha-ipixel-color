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


def _format_item(hass: HomeAssistant, item: dict[str, Any],
                 with_icon: bool = True) -> str:
    """Testo di una casella (o di un sotto-sensore 'below'): etichetta +
    valore + unità, con l'eventuale icona davanti. Vale sia per le caselle
    'entity' sia per quelle 'text'."""
    if item.get("type", "entity") == "text":
        return (item.get("text") or "").strip()

    entity_id = item.get("entity_id")
    if not entity_id:
        return ""
    state = hass.states.get(entity_id)

    value = "?"
    if state and state.state not in ("unknown", "unavailable", ""):
        value = state.state
        decimals = item.get("decimals", 1)
        try:
            value = f"{float(value):.{int(decimals)}f}".replace(".", ",")
        except (ValueError, TypeError):
            pass

    unit = ""
    if item.get("show_unit") and state:
        unit = state.attributes.get("unit_of_measurement") or ""
        unit = unit.replace("°", "").strip()  # i gradi resi male dal display

    label = (item.get("label") or "").strip()
    text = f"{label} {value}" if label else value
    if unit:
        text = f"{text}{unit}"

    if with_icon:
        # chiave assente = icona automatica dal tipo; "" = tolta apposta
        icon = item.get("icon")
        if icon is None:
            icon = default_icon_for(entity_id, state)
        icon = (icon or "").strip()
        if icon:
            gap = item.get("icon_gap")
            gap = 1 if gap is None else max(0, int(gap))
            text = f"{icon}{' ' * gap}{text}"
    return text


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
        text = _format_item(hass, item, with_icon=True)
        if text:
            parts.append(text)
    return separator.join(parts)


def has_custom_segments(items: list[dict[str, Any]]) -> bool:
    """True se qualche casella ha grandezza, posizione, distanza o un
    secondo sensore impilato (serve il percorso a segmenti, non il testo
    unico)."""
    for item in items:
        if not item.get("enabled", True):
            continue
        if item.get("size") or (item.get("valign") or "center") != "center":
            return True
        if item.get("gap") is not None or item.get("voff") is not None:
            return True
        below = item.get("below")
        if isinstance(below, dict) and below.get("entity_id"):
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
    grandezza ("size", px), posizione ("voff" px dall'alto, o "valign") e,
    se presente, un SECONDO sensore ("below") impilato sotto nello stesso
    spazio (due righe da ~7px)."""
    segments: list[dict[str, Any]] = []
    enabled = [it for it in items if it.get("enabled", True)]
    for idx, item in enumerate(enabled):
        text = _format_item(hass, item, with_icon=True)

        stack_text = None
        below = item.get("below")
        if isinstance(below, dict) and below.get("entity_id"):
            stack_text = _format_item(hass, below, with_icon=True)

        if not text and not stack_text:
            continue

        gap = item.get("gap")
        last = idx >= len(enabled) - 1
        seg: dict[str, Any] = {
            "text": text,
            "color": item.get("color") or default_color,
            "size": int(item.get("size") or default_size or 0) or None,
            "valign": item.get("valign") or "center",
        }
        voff = item.get("voff")
        if voff is not None:
            try:
                seg["voff"] = int(voff)
            except (ValueError, TypeError):
                pass

        if stack_text is not None:
            # coppia impilata: riga sotto con colore proprio; la distanza
            # dopo è gestita in rendering
            seg["stack_text"] = stack_text
            seg["stack_color"] = below.get("color") or default_color
            seg["gap_after"] = 0 if last else (
                int(gap) if gap is not None else len(separator))
        elif not last:
            # segmento singolo: la distanza è negli spazi in coda al testo
            seg["text"] = text + (
                " " * max(0, int(gap)) if gap is not None else separator)

        segments.append(seg)
    return segments
