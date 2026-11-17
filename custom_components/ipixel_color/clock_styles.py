"""Stili dell'orologio italiano, disegnati lato server a 64x16.

Ogni stile produce un PNG (una schermata) o una GIF (schermata animata)
che l'integrazione manda al display come una qualsiasi immagine. I font
sono bitmap disegnati a mano: a queste dimensioni un TTF ricampionato
perde i pixel, qui ogni LED e' deciso in partenza.
"""
from __future__ import annotations

import datetime
from io import BytesIO

W, H = 64, 16

GIORNI2 = ("LU", "MA", "ME", "GI", "VE", "SA", "DO")
GIORNI3 = ("LUN", "MAR", "MER", "GIO", "VEN", "SAB", "DOM")

# ---------------------------------------------------------------- font bitmap
# Ogni glifo e' una tupla di righe; "1" = LED acceso.

F5 = {  # 5x7 (cifre, due punti, barra)
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11111", "00010", "00100", "00010", "00001", "10001", "01110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "11110", "00001", "00001", "10001", "01110"),
    "6": ("00110", "01000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00010", "01100"),
    ":": ("00", "11", "11", "00", "11", "11", "00"),
    "/": ("00001", "00010", "00010", "00100", "01000", "01000", "10000"),
    " ": ("000", "000", "000", "000", "000", "000", "000"),
    "~": ("00", "00", "00", "00", "00", "00", "00"),  # vuoto largo 2 = colon spento
}

F3 = {  # 3x5 (testi piccoli: giorni, data, temperatura)
    "0": ("111", "101", "101", "101", "111"),
    "1": ("010", "110", "010", "010", "111"),
    "2": ("111", "001", "111", "100", "111"),
    "3": ("111", "001", "011", "001", "111"),
    "4": ("101", "101", "111", "001", "001"),
    "5": ("111", "100", "111", "001", "111"),
    "6": ("111", "100", "111", "101", "111"),
    "7": ("111", "001", "010", "010", "010"),
    "8": ("111", "101", "111", "101", "111"),
    "9": ("111", "101", "111", "001", "111"),
    "A": ("111", "101", "111", "101", "101"),
    "B": ("110", "101", "110", "101", "110"),
    "D": ("110", "101", "101", "101", "110"),
    "E": ("111", "100", "111", "100", "111"),
    "G": ("111", "100", "101", "101", "111"),
    "I": ("111", "010", "010", "010", "111"),
    "L": ("100", "100", "100", "100", "111"),
    "M": ("101", "111", "111", "101", "101"),
    "N": ("110", "101", "101", "101", "101"),
    "O": ("111", "101", "101", "101", "111"),
    "R": ("110", "101", "110", "101", "101"),
    "S": ("111", "100", "111", "001", "111"),
    "U": ("101", "101", "101", "101", "111"),
    "V": ("101", "101", "101", "101", "010"),
    "/": ("001", "010", "010", "010", "100"),
    "-": ("000", "000", "111", "000", "000"),
    ",": ("0", "0", "0", "1", "1"),
    "°": ("11", "11", "00", "00", "00"),
    ":": ("0", "1", "0", "1", "0"),   # due punti stretti (1 largo)
    "~": ("0", "0", "0", "0", "0"),   # vuoto largo 1 = colon spento
    " ": ("00", "00", "00", "00", "00"),
}


def _text_width(text: str, font: dict, sx: int = 1) -> int:
    w = 0
    for ch in text:
        glyph = font.get(ch)
        if not glyph:
            continue
        w += (len(glyph[0]) + 1) * sx
    return max(0, w - sx)  # senza l'ultimo spazio


def _new_buf() -> list[list[tuple[int, int, int] | None]]:
    return [[None] * W for _ in range(H)]


def _draw(buf, text, font, x, y, color, sx=1, sy=1, align=None):
    """Disegna `text` nel buffer; align center/right ignora x (right = margine)."""
    tw = _text_width(text, font, sx)
    if align == "center":
        x = (W - tw) // 2
    elif align == "right":
        x = W - tw - x
    for ch in text:
        glyph = font.get(ch)
        if not glyph:
            continue
        gw = len(glyph[0])
        for gy, row in enumerate(glyph):
            for gx, bit in enumerate(row):
                if bit != "1":
                    continue
                for dy in range(sy):
                    for dx in range(sx):
                        px, py = x + gx * sx + dx, y + gy * sy + dy
                        if 0 <= px < W and 0 <= py < H:
                            buf[py][px] = color
        x += (gw + 1) * sx
    return buf


def _to_image(buf):
    from PIL import Image

    img = Image.new("RGB", (W, H), (0, 0, 0))
    put = img.putpixel
    for y in range(H):
        row = buf[y]
        for x in range(W):
            if row[x]:
                put((x, y), row[x])
    return img


def _png(buf) -> tuple[bytes, str]:
    out = BytesIO()
    _to_image(buf).save(out, format="PNG")
    return out.getvalue(), ".png"


def _gif(bufs, duration_ms: int) -> tuple[bytes, str]:
    frames = [_to_image(b) for b in bufs]
    out = BytesIO()
    frames[0].save(
        out, format="GIF", save_all=True, append_images=frames[1:],
        duration=duration_ms, loop=0, disposal=2,
    )
    return out.getvalue(), ".gif"


# ------------------------------------------------------------------- stili

GIORNI_PAROLE = (
    "LUNEDI", "MARTEDI", "MERCOLEDI", "GIOVEDI", "VENERDI", "SABATO", "DOMENICA",
)
MESI_PAROLE = (
    "GENNAIO", "FEBBRAIO", "MARZO", "APRILE", "MAGGIO", "GIUGNO", "LUGLIO",
    "AGOSTO", "SETTEMBRE", "OTTOBRE", "NOVEMBRE", "DICEMBRE",
)


def ora_a_parole(now: datetime.datetime, with_date: bool = False) -> str:
    """Stile I: 'LE DUE E MEZZA' (testo scorrevole nativo del display)."""
    ore = (
        "MEZZANOTTE", "L'UNA", "LE DUE", "LE TRE", "LE QUATTRO", "LE CINQUE",
        "LE SEI", "LE SETTE", "LE OTTO", "LE NOVE", "LE DIECI", "LE UNDICI",
        "MEZZOGIORNO",
    )
    h, m = now.hour, now.minute
    if m >= 43:  # 'meno ...' guarda all'ora che arriva
        h = (h + 1) % 24
    h12 = h % 12
    word = ore[0] if h == 0 else ore[12] if h == 12 else ore[h12 if h12 else 12]
    if m == 0 or m >= 58 or m <= 2:
        suff = " IN PUNTO" if m == 0 else ""
    elif 13 <= m <= 17:
        suff = " E UN QUARTO"
    elif 28 <= m <= 32:
        suff = " E MEZZA"
    elif 43 <= m <= 47:
        suff = " MENO UN QUARTO"
    elif m >= 48:
        suff = f" MENO {60 - m}"
    else:
        suff = f" E {m}"
    frase = word + suff
    if with_date:
        # "Mostra data" accesa: anche la data, in parole
        frase += (
            f"  {GIORNI_PAROLE[now.weekday()]}"
            f" {now.day} {MESI_PAROLE[now.month - 1]} {now.year}"
        )
    return frase


def _blit(buf, img, x0: int, y0: int) -> None:
    """Appoggia un'immagine PIL RGBA sul buffer (alfa > 128 = acceso)."""
    px = img.load()
    for y in range(img.height):
        for x in range(img.width):
            r, g, b, a = px[x, y]
            xx, yy = x0 + x, y0 + y
            if a > 128 and 0 <= xx < W and 0 <= yy < H:
                buf[yy][xx] = (r, g, b)


def _a_metrics(size) -> tuple[dict, int, int]:
    """Stile A: dato l'altezza voluta dell'ora (in pixel, 5..16) restituisce
    (font, sx, sy). I font bitmap scalano a interi, quindi le altezze vere
    sono 5 (F3), 7 (F5), 10 (F3x2) e 14 (F5x2): si aggancia alla piu' vicina.
    Accetta anche le vecchie etichette s/m/g."""
    aliases = {"s": 5, "m": 7, "g": 14}
    h = size
    if isinstance(h, str):
        h = aliases.get(h) or (int(h) if h.strip().lstrip("-").isdigit() else 7)
    try:
        h = int(h)
    except (TypeError, ValueError):
        h = 7
    cands = ((5, F3, 1, 1), (7, F5, 1, 1), (10, F3, 2, 2), (14, F5, 2, 2))
    best = min(cands, key=lambda c: abs(c[0] - h))
    return best[1], best[2], best[3]


def render(
    style: str,
    now: datetime.datetime,
    color: tuple[int, int, int] = (255, 255, 255),
    temp: str | None = None,
    show_date: bool = False,
    icon=None,
    size: str = "m",
    gap: int = 2,
) -> tuple[bytes, str]:
    """Una schermata dello stile scelto: (bytes, estensione)."""
    hh, mm = f"{now:%H}", f"{now:%M}"
    tempo = f"{hh}:{mm}"
    data = f"{now:%d/%m/%y}"  # con l'anno: "16/11/26"
    main = data if show_date else tempo  # alternanza data di fine minuto
    day3 = GIORNI3[now.weekday()]

    if style == "b":  # due righe: giorno+data sopra, ora sotto
        buf = _new_buf()
        _draw(buf, f"{day3} {data}", F3, 0, 1, color, align="center")
        _draw(buf, tempo, F5, 0, 8, color, align="center")
        return _png(buf)

    if style in ("c", "g"):  # ora grande (g: due punti a lampeggio)
        buf = _new_buf()
        if show_date:
            # la data con l'anno e' lunga: cifre strette ma a tutta altezza
            _draw(buf, data, F5, 0, 1, color, sx=1, sy=2, align="center")
        else:
            txt = main
            if style == "g" and now.second % 2:
                txt = main.replace(":", "~")  # colon spento ma stessa larghezza
            _draw(buf, txt, F5, 0, 1, color, sx=2, sy=2, align="center")
        return _png(buf)

    if style == "d":  # ora stretta e alta a sinistra, giorno e data a destra
        buf = _new_buf()
        _draw(buf, main, F5, 2, 1, color, sy=2)
        _draw(buf, day3, F3, 1, 2, color, align="right")
        _draw(buf, f"{now:%d/%m/%y}", F3, 1, 9, color, align="right")
        return _png(buf)

    if style == "e":  # con i secondi
        buf = _new_buf()
        txt = data if show_date else f"{tempo}:{now:%S}"
        _draw(buf, txt, F5, 0, 4, color, align="center")
        return _png(buf)

    if style == "f":  # ora + temperatura
        buf = _new_buf()
        _draw(buf, main, F5, 1, 4, color)
        if temp and not show_date:  # la data con l'anno occupa tutta la riga
            _draw(buf, temp, F3, 1, 5, color, align="right")
        return _png(buf)

    if style == "h":  # binario: colonne di bit per hh mm ss
        if show_date:  # a fine minuto anche i nerd leggono la data in chiaro
            buf = _new_buf()
            _draw(buf, data, F5, 0, 4, color, align="center")
            return _png(buf)
        buf = _new_buf()
        cols = (
            now.hour // 10, now.hour % 10,
            now.minute // 10, now.minute % 10,
            now.second // 10, now.second % 10,
        )
        for i, v in enumerate(cols):
            x0 = 8 + i * 9 + (2 if i >= 2 else 0) + (2 if i >= 4 else 0)
            for bit in range(4):
                if (v >> bit) & 1:
                    y0 = 13 - bit * 4
                    for dy in (-1, 0, 1):
                        for dx in (0, 1, 2):
                            yy, xx = y0 + dy, x0 + dx
                            if 0 <= yy < H and xx < W:
                                buf[yy][xx] = color
        return _png(buf)

    if style == "o":  # ora a sinistra, icona MDI scelta a destra
        buf = _new_buf()
        _draw(buf, main, F5, 2, 4, color)
        if icon is not None:
            _blit(buf, icon, W - icon.width - 1, (H - icon.height) // 2)
        return _png(buf)

    if style == "l":  # sole di giorno, luna e stelle di notte
        is_day = 7 <= now.hour < 20
        cx, cy = 56, 8

        def base():
            b = _new_buf()
            _draw(b, main, F5, 2, 4, color)
            return b

        if is_day:
            buf = base()
            for y in range(-2, 3):
                for x in range(-2, 3):
                    if x * x + y * y <= 4:
                        buf[cy + y][cx + x] = (255, 212, 0)
            for dx, dy in ((-4, 0), (4, 0), (0, -4), (0, 4),
                           (-3, -3), (3, 3), (-3, 3), (3, -3)):
                if 0 <= cy + dy < H and 0 <= cx + dx < W:
                    buf[cy + dy][cx + dx] = (255, 140, 0)
            return _png(buf)

        stars = ((50, 3), (61, 4), (52, 13), (62, 12))
        bufs = []
        for frame in range(3):  # le stelline brillano a turno
            buf = base()
            for y in range(-3, 4):
                for x in range(-3, 4):
                    in_big = x * x + y * y <= 9
                    in_cut = (x - 2) * (x - 2) + y * y <= 7
                    if in_big and not in_cut and 0 <= cy + y < H and 0 <= cx + x < W:
                        buf[cy + y][cx + x] = (255, 212, 127)
            for i, (sx_, sy_) in enumerate(stars):
                if (frame + i) % 3 != 0:
                    buf[sy_][sx_] = (255, 255, 255)
            bufs.append(buf)
        return _gif(bufs, 500)

    if style == "m":  # fiamme sotto l'ora
        import random

        rng = random.Random(now.minute * 60 + now.hour)
        fire = [[0] * W for _ in range(H)]
        pal = {1: (122, 27, 0), 2: (255, 90, 0), 3: (255, 196, 0)}

        def step():
            for x in range(W):
                fire[H - 1][x] = 3 if rng.random() < 0.75 else 0
            for y in range(H - 2, 8, -1):
                for x in range(W):
                    below = fire[y + 1][(x + rng.choice((-1, 1))) % W]
                    fire[y][x] = max(0, below - (1 if rng.random() < 0.6 else 0))

        for _ in range(6):  # il fuoco prende prima della prima foto
            step()
        bufs = []
        for _ in range(10):
            step()
            buf = _new_buf()
            _draw(buf, main, F5, 0, 1, color, align="center")
            for y in range(9, H):
                for x in range(W):
                    if fire[y][x] and buf[y][x] is None:
                        buf[y][x] = pal[fire[y][x]]
            bufs.append(buf)
        return _gif(bufs, 140)

    if style == "n":  # cielo stellato attorno all'ora
        bufs = []
        for t in range(8):
            buf = _new_buf()
            _draw(buf, main, F5, 0, 4, color, align="center")
            for i in range(14):
                sx_, sy_ = (i * 37 + 11) % W, (i * 23 + 3) % H
                if buf[sy_][sx_] is None and (t + i) % 4 != 0:
                    bright = (t + i) % 7 == 0
                    buf[sy_][sx_] = (255, 255, 255) if bright else (120, 128, 150)
            bufs.append(buf)
        return _gif(bufs, 400)

    # fallback (stile A): giorno a lettere (F3) + ora, con GRANDEZZA (in
    # pixel, slider 5..16) e DISTANZA scelte dall'utente. Il giorno si
    # ingrandisce da solo (x2) quando resta spazio in larghezza.
    buf = _new_buf()
    if show_date:
        # a fine minuto la data (16/11/26), centrata, dimensione fissa
        _draw(buf, main, F5, 0, (H - 7) // 2, color, align="center")
        return _png(buf)

    day = GIORNI2[now.weekday()]
    tf, tsx, tsy = _a_metrics(size)
    gap = max(0, int(gap))
    time_h = len(tf["0"]) * tsy
    tw = _text_width(main, tf, tsx)
    ty = max(0, (H - time_h) // 2)
    dh3 = len(F3["0"])                      # altezza base del giorno (5)
    dsx = 1
    if tsy == 2 and (_text_width(day, F3, 2) + gap + tw + 2) <= W \
            and (ty + time_h - dh3 * 2) >= 0:
        dsx = 2                             # il giorno raddoppia con l'ora alta
    dw = _text_width(day, F3, dsx)
    day_h = dh3 * dsx
    x0 = max(0, (W - (dw + gap + tw)) // 2)
    day_y = max(0, (ty + time_h) - day_h)   # base del giorno = base dell'ora
    _draw(buf, day, F3, x0, day_y, color, sx=dsx, sy=dsx)
    _draw(buf, main, tf, x0 + dw + gap, ty, color, sx=tsx, sy=tsy)
    return _png(buf)
