"""Comandi NATIVI del firmware che pypixelcolor 0.4 non ha.

I payload sono ricostruiti dai sorgenti di pypixelcolor 0.5.0 (che non
possiamo installare: toglie i font CUSONG/SIMSUN). Sono comandi brevi a
finestra singola: il primo byte e' la lunghezza, come make_power_command.

- set_timer:    il CRONOMETRO lo fa girare il display da solo
                (0=stop, 1=start, 2=pausa) — zero traffico BLE
- set_scores:   tabellone segnapunti del firmware (P1/P2, 0-99)
- set_schedule: accensione/spegnimento programmati DI BORDO
                (funzionano anche con Home Assistant spento)
"""
from __future__ import annotations

TIMER_STOP = 0
TIMER_START = 1
TIMER_PAUSE = 2

# Bitmask dei giorni (dal sorgente 0.5: il bit 0 e' sempre acceso)
SCHEDULE_DAYS = {
    "mon": 0x02,
    "tue": 0x04,
    "wed": 0x08,
    "thu": 0x10,
    "fri": 0x20,
    "sun": 0x40,
    "sat": 0x80,
}


def make_native_timer_command(action: int) -> bytes:
    """Cronometro di bordo: 0=stop, 1=start, 2=pausa."""
    if action not in (0, 1, 2):
        raise ValueError(f"Azione timer non valida: {action}")
    return bytes([5, 0, 9, 0x80, action])


def make_scores_command(p1: int, p2: int) -> bytes:
    """Tabellone segnapunti di bordo (0-99 per giocatore)."""
    p1, p2 = int(p1), int(p2)
    if not (0 <= p1 <= 99 and 0 <= p2 <= 99):
        raise ValueError("I punteggi vanno da 0 a 99")
    return bytes([8, 0, 0x0A, 0x80, p1, 0, p2, 0])


def days_mask(days: list[str] | None) -> int:
    """Converte ['mon','sat'] nella bitmask del firmware (None = tutti)."""
    if not days:
        return 0xFF
    mask = 1  # il bit 0 va sempre acceso
    for d in days:
        key = str(d).strip().lower()
        if key not in SCHEDULE_DAYS:
            raise ValueError(f"Giorno sconosciuto: {d}")
        mask |= SCHEDULE_DAYS[key]
    return mask


def make_schedule_command(
    on: bool, slot: int, mask: int, hour: int, minute: int
) -> bytes:
    """Programma di bordo: alle hour:minute nei giorni della mask il
    display si accende (on=True) o si spegne (on=False)."""
    hour, minute, slot = int(hour), int(minute), int(slot)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("Orario non valido")
    if not (0 <= slot <= 255):
        raise ValueError("Slot non valido")
    return bytes([
        9, 0, 0x11, 0x80,
        1 if on else 0,
        slot,
        mask & 0xFF,
        hour,
        minute,
    ])
