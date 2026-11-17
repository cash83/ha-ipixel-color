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
EMOJI_OPTS: dict[str, Any] = {"px": None, "valign": "center", "y": None}

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
        yopt = EMOJI_OPTS.get("y")
        if yopt is not None:
            y = int(yopt)                       # posizione esatta (voff): segue il testo
        else:
            valign = EMOJI_OPTS.get("valign") or "center"
            if valign == "top":
                y = 0
            elif valign == "bottom":
                y = size - img.height
            else:
                y = (size - img.height) // 2
        y = max(0, min(y, size - img.height))
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


def _hcat(a, b):
    """Affianca due immagini L (stessa altezza)."""
    from PIL import Image
    c = Image.new("L", (a.width + b.width, a.height), 0)
    c.paste(a, (0, 0))
    c.paste(b, (a.width, 0))
    return c


def _jpeg_tile_block(tile, block_height=16):
    """Una tile RGB 16x16 → blocco emoji JPEG, STESSO formato di
    pypixelcolor (q95, subsampling 0, senza header JFIF) così il firmware
    la rende come una qualsiasi emoji."""
    from io import BytesIO
    from pypixelcolor.commands.send_text.encoding import encode_emoji_block
    buf = BytesIO()
    tile.convert("RGB").save(buf, format="JPEG", quality=95,
                             subsampling=0, optimize=True)
    jb = buf.getvalue()
    if jb[2:4] == b"\xff\xe0":                 # togli l'header JFIF
        dqt = jb.find(b"\xff\xdb")
        if dqt > 0:
            jb = b"\xff\xd8" + jb[dqt:]
    return encode_emoji_block(jb, block_height)


def _render_line_rgb(canvas, text, color_hex, font_name, lh, y0):
    """Disegna una riga (icone emoji a colori + testo colorato) sul canvas
    RGB a quota y0. Ritorna la larghezza occupata."""
    from pypixelcolor.lib.emoji_manager import is_emoji, get_emoji_image
    from ..display.text_renderer import render_text_sprite

    sp = max(2, round(lh * 0.5))
    x = 0
    runs, cur = [], ""
    for ch in text:
        if is_emoji(ch):
            if cur:
                runs.append(("t", cur))
                cur = ""
            runs.append(("e", ch))
        else:
            cur += ch
    if cur:
        runs.append(("t", cur))

    for kind, val in runs:
        if kind == "e":
            im = get_emoji_image(val, size=lh)
            if im is not None:
                im = im.convert("RGBA")
                canvas.paste(im, (x, y0), im)
                x += im.width + 1
            continue
        lead = len(val) - len(val.lstrip(" "))
        trail = len(val) - len(val.rstrip(" "))
        core = val.strip(" ")
        x += lead * sp
        if core:
            spr = render_text_sprite(core, lh, color_hex, font_name)  # RGBA
            canvas.paste(spr, (x, y0), spr)
            x += spr.width
        x += trail * sp
    return x


def _encode_stacked_rgb(top, top_color, bottom, bottom_color, font_name,
                        gap_after=0, block_height=16):
    """Due sensori IMPILATI, a COLORI: la riga di sopra (top_color) e quella
    di sotto (bottom_color), ciascuna ~7px con le sue icone, rese come
    immagine e affettate in tile 16x16 mandate come blocchi emoji JPEG
    (colore vero pixel-per-pixel). Ritorna (bytes, numero_blocchi)."""
    from PIL import Image

    lh = 7
    wide = Image.new("RGB", (1024, block_height), (0, 0, 0))
    wt = _render_line_rgb(wide, (top or "").strip(), top_color, font_name, lh, 0)
    wb = _render_line_rgb(wide, (bottom or "").strip(), bottom_color,
                          font_name, lh, block_height - lh)
    w = max(wt, wb, 1) + max(0, int(gap_after)) * 2
    w = min(w, 1024)
    canvas = wide.crop((0, 0, w, block_height))

    out = bytearray()
    n = 0
    x = 0
    while x < canvas.width:
        tile = canvas.crop((x, 0, min(x + 16, canvas.width), block_height))
        if tile.width < 16:
            pad = Image.new("RGB", (16, block_height), (0, 0, 0))
            pad.paste(tile, (0, 0))
            tile = pad
        out.extend(_jpeg_tile_block(tile, block_height))
        n += 1
        x += 16
    return bytes(out), n


def _encode_small_segment(text, color_hex, size_px, valign, font_name,
                          block_height=16, y0=None):
    """Casella rimpicciolita resa NITIDA.

    Le parti di testo vengono disegnate con render_text_sprite
    (sovracampionato 4x + soglia: piccole ma leggibili, niente glifi
    impastati), appoggiate su un'immagine alta `block_height` alla
    posizione verticale voluta e affettate in strisce da 8px, ognuna un
    blocco-carattere 8x16 come quello che il firmware vede da sempre
    (verificato: byte identici). Le emoji restano blocchi emoji, gia'
    scalati via EMOJI_OPTS dal chiamante. Gli spazi (anche la "distanza
    icona") diventano colonne vuote. Ritorna (bytes, numero_blocchi).
    """
    from PIL import Image
    from pypixelcolor.lib.emoji_manager import is_emoji
    from pypixelcolor.commands.send_text.image_processing import (
        encode_char_img, emoji_to_hex)
    from pypixelcolor.commands.send_text.encoding import (
        encode_character_block, encode_emoji_block,
        _logic_reverse_bits_order_bytes)
    from ..display.text_renderer import render_text_sprite

    color_bytes = bytes.fromhex(color_hex)
    out = bytearray()
    n = 0

    if y0 is None:
        if valign == "top":
            y0 = 0
        elif valign == "bottom":
            y0 = max(0, block_height - size_px)
        else:
            y0 = max(0, (block_height - size_px) // 2)
    y0 = max(0, min(int(y0), max(0, block_height - size_px)))
    space_px = max(2, round(size_px * 0.45))

    runs = []
    cur = ""
    for ch in text:
        if is_emoji(ch):
            if cur:
                runs.append(("text", cur))
                cur = ""
            runs.append(("emoji", ch))
        else:
            cur += ch
    if cur:
        runs.append(("text", cur))

    strip = {"img": None}  # colonne di testo accumulate (non spezzo agli spazi)

    def _emit(canvas):
        nonlocal n
        x = 0
        while x < canvas.width:
            col = canvas.crop((x, 0, min(x + 8, canvas.width), block_height))
            if col.width < 8:
                pad = Image.new("L", (8, block_height), 0)
                pad.paste(col, (0, 0))
                col = pad
            cb = _logic_reverse_bits_order_bytes(encode_char_img(col))
            out.extend(encode_character_block(cb, block_height, color_bytes))
            n += 1
            x += 8

    def _flush():
        if strip["img"] is not None and strip["img"].width:
            _emit(strip["img"])
        strip["img"] = None

    def _append(seg_img):
        strip["img"] = seg_img if strip["img"] is None else _hcat(strip["img"], seg_img)

    for kind, val in runs:
        if kind == "emoji":
            _flush()
            eb = emoji_to_hex(val, block_height)
            if eb:
                out.extend(encode_emoji_block(eb, block_height))
                n += 1
            continue
        lead = len(val) - len(val.lstrip(" "))
        trail = len(val) - len(val.rstrip(" "))
        core = val.strip(" ")
        if lead:
            _append(Image.new("L", (lead * space_px, block_height), 0))
        if core:
            spr = render_text_sprite(core, size_px, color_hex, font_name)
            mask = spr.split()[3]
            canvas = Image.new("L", (mask.width, block_height), 0)
            canvas.paste(255, (0, y0), mask)
            _append(canvas)
        if trail:
            _append(Image.new("L", (trail * space_px, block_height), 0))
    _flush()
    return bytes(out), n


def render_segments_image(segments, font="CUSONG", block_height=16):
    """Rende la composizione come IMMAGINE RGB (la striscia intera, 16 alta),
    con gli STESSI criteri del display: caselle piene per-carattere col font
    scelto, caselle piccole con lo sprite 7x5 alla posizione/colore giusti,
    coppie impilate a due righe colorate con icone. Serve all'anteprima del
    pannello per rispecchiare davvero lo schermo. Ritorna una PIL.Image."""
    from PIL import Image
    from pypixelcolor.commands.send_text.font_utils import resolve_font_config
    from pypixelcolor.commands.send_text.image_processing import char_to_hex
    from pypixelcolor.lib.emoji_manager import is_emoji, get_emoji_image

    base_cfg = resolve_font_config(font)
    base_m = dict(base_cfg.get_metrics(block_height))

    def _full_image(text, color_hex):
        r, g, b = (int(color_hex[0:2], 16), int(color_hex[2:4], 16),
                   int(color_hex[4:6], 16))
        parts = []
        for ch in text:
            if is_emoji(ch):
                im = get_emoji_image(ch, block_height)
                parts.append(im.convert("RGB") if im
                             else Image.new("RGB", (block_height, block_height), (0, 0, 0)))
                continue
            raw = char_to_hex(ch, block_height, base_cfg.path, base_m["offset"],
                              base_m["font_size"], base_m["pixel_threshold"])
            cell = Image.new("RGB", (8, block_height), (0, 0, 0))
            if raw:
                px = cell.load()
                for row in range(block_height):
                    byte = raw[row] if row < len(raw) else 0
                    for c in range(8):
                        if (byte >> (7 - c)) & 1:
                            px[c, row] = (r, g, b)
            parts.append(cell)
        if not parts:
            return Image.new("RGB", (1, block_height), (0, 0, 0))
        w = sum(p.width for p in parts)
        img = Image.new("RGB", (w, block_height), (0, 0, 0))
        x = 0
        for p in parts:
            img.paste(p, (x, 0))
            x += p.width
        return img

    strips = []
    for seg in segments:
        text = str(seg.get("text") or "")
        stack = seg.get("stack_text")
        color = str(seg.get("color") or "ffffff").lstrip("#")
        if stack is not None:
            lh = 7
            wide = Image.new("RGB", (1024, block_height), (0, 0, 0))
            wt = _render_line_rgb(wide, text.strip(), color, "7x5.ttf", lh, 0)
            scol = str(seg.get("stack_color") or color).lstrip("#")
            wb = _render_line_rgb(wide, stack.strip(), scol, "7x5.ttf", lh,
                                  block_height - lh)
            w = max(wt, wb, 1) + max(0, int(seg.get("gap_after") or 0)) * 2
            strips.append(wide.crop((0, 0, min(w, 1024), block_height)))
            continue
        if not text:
            continue
        size_px = int(seg.get("size") or block_height)
        size_px = max(5, min(size_px, block_height))
        if size_px >= block_height:
            strips.append(_full_image(text, color))
        else:
            voff = seg.get("voff")
            if voff is not None:
                y0 = max(0, min(int(voff), block_height - size_px))
            else:
                y0 = max(0, min(_valign_offset(block_height, size_px,
                                               seg.get("valign") or "center"),
                                block_height - size_px))
            wide = Image.new("RGB", (1024, block_height), (0, 0, 0))
            w = _render_line_rgb(wide, text, color, "7x5.ttf", size_px, y0)
            strips.append(wide.crop((0, 0, min(max(w, 1), 1024), block_height)))

    if not strips:
        return Image.new("RGB", (1, block_height), (0, 0, 0))
    total = sum(s.width for s in strips)
    img = Image.new("RGB", (total, block_height), (0, 0, 0))
    x = 0
    for s in strips:
        img.paste(s, (x, 0))
        x += s.width
    return img


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
        stack_text = seg.get("stack_text")
        if not text and not stack_text:
            continue
        color = str(seg.get("color") or "ffffff").lstrip("#")
        if first_color is None:
            first_color = color

        if stack_text is not None:
            # Due sensori IMPILATI, a colori (riga sopra e riga sotto con
            # colori propri + icone): resa come immagine → tile JPEG.
            stack_color = str(seg.get("stack_color") or color).lstrip("#")
            seg_bytes, seg_items = _encode_stacked_rgb(
                text, color, stack_text, stack_color, "7x5.ttf",
                int(seg.get("gap_after") or 0), block_height,
            )
            items_bytes += seg_bytes
            num_items += seg_items
            continue

        size_px = int(seg.get("size") or block_height)
        size_px = max(5, min(size_px, block_height))
        valign = str(seg.get("valign") or "center")
        voff = seg.get("voff")

        full = size_px >= block_height
        if full:
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
            # posizione verticale effettiva (voff esatto, o dalla valign):
            # la usa ANCHE l'icona, così segue il testo quando sposti
            if voff is not None:
                y0 = max(0, min(int(voff), block_height - size_px))
            else:
                y0 = max(0, min(_valign_offset(block_height, size_px, valign),
                                block_height - size_px))

        EMOJI_OPTS["px"] = emoji_px
        EMOJI_OPTS["valign"] = valign
        EMOJI_OPTS["y"] = None if full else y0
        try:
            if full:
                # Misura piena: percorso per-carattere, IDENTICO a send_text.
                seg_bytes = encode_text(
                    text, block_height, color, base_cfg.path,
                    offset, font_size, base_m["pixel_threshold"], reverse=False,
                )
                seg_items = len(text)
            else:
                # Rimpicciolita: resa NITIDA con sprite sovracampionato 4x
                # (font a pixel 7x5), appoggiata alla posizione voluta e
                # affettata in strisce da 8px. L'emoji segue la stessa y.
                seg_bytes, seg_items = _encode_small_segment(
                    text, color, size_px, valign, "7x5.ttf",
                    block_height=block_height, y0=y0,
                )
        finally:
            EMOJI_OPTS["px"] = None
            EMOJI_OPTS["valign"] = "center"
            EMOJI_OPTS["y"] = None
        items_bytes += seg_bytes
        num_items += seg_items

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
