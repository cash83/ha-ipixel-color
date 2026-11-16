"""Text display commands using pypixelcolor.

Oltre al comando "tutto uguale" di pypixelcolor (make_text_command),
qui c'è anche make_text_command_mixed: il protocollo del testo
scorrevole è un flusso di blocchi-carattere INDIPENDENTI (ognuno con i
suoi byte RGB e la sua bitmap a 16 righe), quindi ogni segmento può
avere grandezza e posizione verticale sue. pypixelcolor non lo espone
(un solo font/colore per chiamata): i blocchi li assembliamo noi con
le sue stesse funzioni di codifica.
"""
from __future__ import annotations

import binascii
from typing import Any, Optional

try:
    from pypixelcolor.commands.send_text import send_text
    from pypixelcolor.lib.transport.send_plan import SendPlan
except ImportError:
    send_text = None
    SendPlan = None

# --- Emoji più piccole (e posizionabili) nel flusso ------------------
# I blocchi emoji sono JPEG a misura fissa (16x16): l'unico modo di
# ridurle/spostarle è disegnarle più piccole DENTRO il quadrato.
# EMOJI_OPTS viene impostato attorno a ogni codifica.
EMOJI_OPTS: dict[str, Any] = {"px": None, "valign": "center"}

try:
    from pypixelcolor.commands.send_text import image_processing as _pp_ip

    _ORIG_EMOJI_IMAGE = _pp_ip.get_emoji_image

    def _emoji_image_scaled(char, size):
        px = EMOJI_OPTS.get("px")
        if not px or px >= size:
            return _ORIG_EMOJI_IMAGE(char, size)
        img = _ORIG_EMOJI_IMAGE(char, px)
        if img is None:
            return None
        from PIL import Image as _Img

        canvas = _Img.new("RGB", (size, size), (0, 0, 0))
        x = (size - img.width) // 2
        valign = EMOJI_OPTS.get("valign") or "center"
        if valign == "top":
            y = 0
        elif valign == "bottom":
            y = size - img.height
        else:
            y = (size - img.height) // 2
        if img.mode == "RGBA":
            canvas.paste(img, (x, y), img)
        else:
            canvas.paste(img, (x, y))
        return canvas

    _pp_ip.get_emoji_image = _emoji_image_scaled
except ImportError:  # pragma: no cover
    pass


def make_text_command(
    text: str,
    color: str = "ffffff",
    bg_color: Optional[str] = None,
    font="CUSONG",
    animation: int = 0,
    speed: int = 80,
    rainbow_mode: int = 0,
    save_slot: int = 0,
    device_height: Optional[int] = None
) -> list[bytes]:
    """Build text display command using pypixelcolor.

    Args:
        text: The text to display
        color: Text color in hex format (e.g., 'ffffff')
        bg_color: Background color in hex format (e.g., '000000'), or None for transparent
        font: Font name ('CUSONG', 'SIMSUN', 'VCR_OSD_MONO'), file path, or FontConfig
        animation: Animation type (0-7, excluding 3&4 on non-32x32 devices)
        speed: Animation speed (0-100)
        rainbow_mode: Rainbow mode (0-9)
        save_slot: Save slot (0-255)
        device_height: Device height in pixels (auto-detected if None)

    Returns:
        List of command bytes (one per window/frame)

    Raises:
        ImportError: If pypixelcolor is not available
        ValueError: If parameters are out of valid ranges
    """
    if send_text is None:
        raise ImportError("pypixelcolor library is not installed")

    # Call pypixelcolor's send_text function
    send_plan = send_text(
        text=text,
        color=color,
        bg_color=bg_color,
        font=font,
        animation=animation,
        speed=speed,
        rainbow_mode=rainbow_mode,
        save_slot=save_slot,
        char_height=device_height
    )

    # Extract command bytes from all windows
    # send_text may return multiple windows for large text
    commands = []
    for window in send_plan.windows:
        commands.append(window.data)

    return commands


def _valign_offset(block_height: int, size_px: int, valign: str) -> int:
    if valign == "top":
        return 0
    if valign == "bottom":
        return block_height - size_px
    return (block_height - size_px) // 2


def make_text_command_mixed(
    segments: list[dict[str, Any]],
    font: str = "CUSONG",
    bg_color: Optional[str] = None,
    animation: int = 1,
    speed: int = 50,
    rainbow_mode: int = 0,
    save_slot: int = 0,
    block_height: int = 16,
) -> list[bytes]:
    """Testo scorrevole con grandezza/posizione/colore PER SEGMENTO.

    segments: [{"text": str, "color": "rrggbb", "size": 5..block,
                "valign": "top"|"center"|"bottom"}, ...]
    Il font è unico (quello scelto nel pannello); ogni segmento viene
    codificato con le sue metriche e i blocchi si concatenano. Replica
    l'assemblaggio del payload di pypixelcolor.send_text.
    """
    if send_text is None:
        raise ImportError("pypixelcolor library is not installed")

    # encode_text (per carattere) e non encode_text_chunked: e' il
    # percorso che il display riceve da sempre (i nostri font hanno
    # tutti var_width=false), quindi niente esperimenti sul firmware.
    from pypixelcolor.commands.send_text.encoding import encode_text
    from pypixelcolor.commands.send_text.font_utils import resolve_font_config

    if animation in (3, 4):
        raise ValueError("Animazioni 3/4 vietate (bootloop sui non-32x32)")

    base_cfg = resolve_font_config(font)
    base_m = dict(base_cfg.get_metrics(block_height))

    items_bytes = bytearray()
    num_items = 0
    first_color = None

    for seg in segments:
        text = str(seg.get("text") or "")
        if not text:
            continue
        color = str(seg.get("color") or "ffffff").lstrip("#")
        if first_color is None:
            first_color = color
        size_px = int(seg.get("size") or block_height)
        size_px = max(5, min(size_px, block_height))
        valign = str(seg.get("valign") or "center")

        if size_px >= block_height:
            font_size = base_m["font_size"]
            offset = tuple(base_m["offset"])
            emoji_px = None
        else:
            ratio = size_px / block_height
            font_size = max(5, round(base_m["font_size"] * ratio))
            offset = (
                base_m["offset"][0],
                round(base_m["offset"][1] * ratio)
                + _valign_offset(block_height, size_px, valign),
            )
            emoji_px = size_px

        EMOJI_OPTS["px"] = emoji_px
        EMOJI_OPTS["valign"] = valign
        try:
            seg_bytes = encode_text(
                text,
                block_height,
                color,
                base_cfg.path,
                offset,
                font_size,
                base_m["pixel_threshold"],
                reverse=False,
            )
        finally:
            EMOJI_OPTS["px"] = None
            EMOJI_OPTS["valign"] = "center"
        items_bytes += seg_bytes
        # Come l'originale: il contatore e' il numero di caratteri
        num_items += len(text)

    if not num_items:
        raise ValueError("Niente da mostrare")
    if num_items > 255:
        raise ValueError(f"Troppi blocchi ({num_items}): accorcia il testo")

    # --- properties: come in send_text ---
    color_bytes = bytes.fromhex(first_color or "ffffff")
    properties = bytearray()
    properties += bytes([0x00, 0x01, 0x01])
    properties += bytes([
        int(animation) & 0xFF, int(speed) & 0xFF, int(rainbow_mode) & 0xFF,
    ])
    properties += color_bytes
    if bg_color:
        properties += bytes([0x01]) + bytes.fromhex(bg_color.lstrip("#"))
    else:
        properties += bytes([0x00, 0x00, 0x00, 0x00])

    data_payload = bytes([num_items]) + bytes(properties) + bytes(items_bytes)

    # --- multi-frame: come in send_text ---
    crc = binascii.crc32(data_payload) & 0xFFFFFFFF
    payload_size = len(data_payload)
    commands: list[bytes] = []
    window_size = 12 * 1024
    pos = 0
    window_index = 0
    while pos < payload_size:
        window_end = min(pos + window_size, payload_size)
        chunk_payload = data_payload[pos:window_end]
        option = 0x00 if window_index == 0 else 0x02
        frame_header = bytearray()
        frame_header += bytes([0x00, 0x01, option])
        frame_header += payload_size.to_bytes(4, byteorder="little")
        frame_header += crc.to_bytes(4, byteorder="little")
        frame_header += bytes([0x00, int(save_slot) & 0xFF])
        frame_content = bytes(frame_header) + chunk_payload
        prefix = (len(frame_content) + 2).to_bytes(2, byteorder="little")
        commands.append(prefix + frame_content)
        window_index += 1
        pos = window_end

    return commands
