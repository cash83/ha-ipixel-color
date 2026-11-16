"""Fetch, color and rasterize Home Assistant MDI icons for iPIXEL Color devices.

Icons are downloaded on demand from the jsDelivr CDN mirror of the @mdi/svg
npm package (https://www.jsdelivr.com/package/npm/@mdi/svg), pinned to a
fixed version so results are reproducible. They are rasterized with
`resvg_py` (https://pypi.org/project/resvg_py/), a self-contained Rust
binding that ships as a precompiled wheel with no system-level SVG
dependencies (unlike cairosvg, which needs libcairo installed at the OS
level - often unavailable/painful on Home Assistant OS or containers).
"""
from __future__ import annotations

import io
import logging

import aiohttp

try:
    import resvg_py
except ImportError:
    resvg_py = None

from PIL import Image

_LOGGER = logging.getLogger(__name__)

# Pinned @mdi/svg version for reproducibility. Bump manually if you need
# icons added in a newer Material Design Icons release.
# Browse available icons/versions at https://pictogrammers.com/library/mdi/
MDI_SVG_VERSION = "7.4.47"
MDI_CDN_URL_TEMPLATE = (
    f"https://cdn.jsdelivr.net/npm/@mdi/svg@{MDI_SVG_VERSION}/svg/{{slug}}.svg"
)

# Twemoji (le emoji di Twitter/X, mantenute dal fork jdecked): stessa
# fonte usata da pypixelcolor per le emoji nel testo scorrevole.
TWEMOJI_VERSION = "15.1.0"
TWEMOJI_URL_TEMPLATE = (
    f"https://cdn.jsdelivr.net/gh/jdecked/twemoji@{TWEMOJI_VERSION}"
    "/assets/svg/{slug}.svg"
)


def is_emoji_icon(icon: str) -> bool:
    """True se il campo icona contiene un'emoji invece di un nome MDI.

    I nomi MDI sono solo ASCII (lettere, cifre, trattini): qualunque
    carattere fuori dall'ASCII viene trattato come emoji.
    """
    return any(ord(c) > 127 for c in icon.strip())


def _emoji_slugs(emoji: str) -> list[str]:
    """Nomi file Twemoji possibili per un'emoji (senza e con FE0F)."""
    cps = [ord(c) for c in emoji.strip()]
    without = "-".join(f"{c:x}" for c in cps if c != 0xFE0F)
    full = "-".join(f"{c:x}" for c in cps)
    slugs = [without] if without else []
    if full and full != without:
        slugs.append(full)
    return slugs


async def fetch_emoji_svg(emoji: str, session: aiohttp.ClientSession) -> str:
    """Scarica l'SVG Twemoji di un'emoji (coi suoi colori originali)."""
    last_err = "emoji vuota"
    for slug in _emoji_slugs(emoji):
        url = TWEMOJI_URL_TEMPLATE.format(slug=slug)
        try:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    return await resp.text()
                last_err = f"HTTP {resp.status}"
        except aiohttp.ClientError as err:
            last_err = str(err)
    raise ValueError(
        f"Emoji '{emoji.strip()}' non trovata su Twemoji ({last_err})"
    )


def normalize_icon_slug(icon: str) -> str:
    """Turn 'mdi:battery-outline' or 'battery-outline' into 'battery-outline'.

    Args:
        icon: Icon name, with or without the 'mdi:' prefix.

    Returns:
        The bare icon slug as used by the @mdi/svg package file names.
    """
    icon = icon.strip()
    if icon.startswith("mdi:"):
        icon = icon[len("mdi:"):]
    return icon


def _hex_to_rgb(color_hex: str) -> tuple[int, int, int]:
    """Convert a 6-digit hex color string (with or without '#') to an RGB tuple."""
    color_hex = color_hex.lstrip("#")
    if len(color_hex) != 6:
        raise ValueError(f"Color must be 6 hex chars, e.g. 'ffffff' (got '{color_hex}')")
    return tuple(int(color_hex[i:i + 2], 16) for i in (0, 2, 4))


async def fetch_mdi_svg(icon: str, session: aiohttp.ClientSession) -> str:
    """Download the raw SVG markup for an MDI icon from the jsDelivr CDN.

    Args:
        icon: Icon name, with or without the 'mdi:' prefix.
        session: aiohttp session to use for the request (reuse HA's shared
            session via homeassistant.helpers.aiohttp_client.async_get_clientsession).

    Returns:
        Raw SVG markup as text.

    Raises:
        ValueError: If the icon cannot be found or downloaded.
    """
    slug = normalize_icon_slug(icon)
    url = MDI_CDN_URL_TEMPLATE.format(slug=slug)

    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                raise ValueError(
                    f"MDI icon '{slug}' not found (HTTP {resp.status}) at {url}"
                )
            return await resp.text()
    except aiohttp.ClientError as err:
        raise ValueError(f"Failed to download MDI icon '{slug}' from {url}: {err}") from err


def render_mdi_icon(
    svg_markup: str, icon_size_px: int, color_hex: str | None = "ffffff"
) -> Image.Image:
    """Rasterize an MDI SVG into a colored RGBA PIL image.

    MDI's raw per-icon SVG files contain a single <path> with no explicit
    fill attribute (defaulting to black per the SVG spec). We override the
    fill via an injected CSS stylesheet rule, which takes precedence over
    the (absent) default.

    Args:
        svg_markup: Raw SVG text for the icon.
        icon_size_px: Target width/height in pixels (icons are square).
        color_hex: Fill color in hex, with or without '#' (e.g. 'ffffff').

    Returns:
        RGBA PIL Image of size (icon_size_px, icon_size_px).

    Raises:
        ImportError: If resvg_py is not installed.
    """
    if resvg_py is None:
        raise ImportError("resvg_py library is not installed")

    kwargs = {}
    if color_hex:
        # None = lascia i colori dell'SVG (le emoji Twemoji sono già colorate)
        kwargs["style_sheet"] = f"path {{ fill: #{color_hex.lstrip('#')}; }}"

    png_bytes = resvg_py.svg_to_bytes(
        svg_string=svg_markup,
        width=icon_size_px,
        height=icon_size_px,
        **kwargs,
    )
    return Image.open(io.BytesIO(bytes(png_bytes))).convert("RGBA")


def compose_icon_canvas(
    icon_img: Image.Image,
    canvas_width: int,
    canvas_height: int,
    bg_color_hex: str = "000000",
) -> bytes:
    """Paste a colored icon, centered, onto a background-filled canvas.

    Args:
        icon_img: RGBA icon image (square), as returned by render_mdi_icon.
        canvas_width: Target canvas width in pixels (device width).
        canvas_height: Target canvas height in pixels (device height).
        bg_color_hex: Background fill color in hex, with or without '#'.

    Returns:
        PNG-encoded bytes of the composed canvas, ready for
        pypixelcolor's send_image_hex.
    """
    bg_rgb = _hex_to_rgb(bg_color_hex)
    canvas = Image.new("RGB", (canvas_width, canvas_height), bg_rgb)

    x = (canvas_width - icon_img.width) // 2
    y = (canvas_height - icon_img.height) // 2
    canvas.paste(icon_img, (x, y), mask=icon_img)  # icon's own alpha channel as mask

    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()


def compose_icon_media(
    icon_img: Image.Image,
    canvas_width: int,
    canvas_height: int,
    pos_x: str = "center",
    pos_y: str = "center",
    motion: str = "none",
    bg_color_hex: str = "000000",
    frame_ms: int = 80,
    step_px: int = 2,
) -> tuple[bytes, str]:
    """Icona posizionata (PNG) o che scorre da un lato all'altro (GIF).

    pos_x: left/center/right - pos_y: top/center/bottom -
    motion: none/left/right (con lo scorrimento pos_x viene ignorata).
    frame_ms/step_px: millisecondi a fotogramma e pixel a passo — qui
    la velocità è nostra, può essere lenta quanto si vuole (a
    differenza dello scorrimento del firmware, che ha il suo minimo).
    """
    bg = _hex_to_rgb(bg_color_hex)

    if pos_y == "top":
        y = 0
    elif pos_y == "bottom":
        y = canvas_height - icon_img.height
    else:
        y = (canvas_height - icon_img.height) // 2

    if motion in ("left", "right"):
        step_px = max(1, int(step_px))
        xs = list(range(canvas_width, -icon_img.width - 1, -step_px))
        if motion == "right":
            xs = list(reversed(xs))
        frames = []
        for x in xs:
            c = Image.new("RGB", (canvas_width, canvas_height), bg)
            c.paste(icon_img, (x, y), mask=icon_img)
            frames.append(c)
        out = io.BytesIO()
        frames[0].save(
            out, format="GIF", save_all=True, append_images=frames[1:],
            duration=max(20, int(frame_ms)), loop=0, disposal=2,
        )
        return out.getvalue(), ".gif"

    if pos_x == "left":
        x = 0
    elif pos_x == "right":
        x = canvas_width - icon_img.width
    else:
        x = (canvas_width - icon_img.width) // 2
    c = Image.new("RGB", (canvas_width, canvas_height), bg)
    c.paste(icon_img, (x, y), mask=icon_img)
    out = io.BytesIO()
    c.save(out, format="PNG")
    return out.getvalue(), ".png"


async def build_mdi_icon_png(
    icon: str,
    session: aiohttp.ClientSession,
    canvas_width: int,
    canvas_height: int,
    color_hex: str = "ffffff",
    bg_color_hex: str = "000000",
    scale_percent: int = 100,
) -> bytes:
    """Fetch, color and rasterize an MDI icon into device-canvas-sized PNG bytes.

    Args:
        icon: MDI icon name, with or without 'mdi:' prefix.
        session: aiohttp session for the CDN request.
        canvas_width: Device width in pixels (as reported by the device itself).
        canvas_height: Device height in pixels (as reported by the device itself).
        color_hex: Icon fill color in hex, with or without '#'.
        bg_color_hex: Canvas background color in hex, with or without '#'.
        scale_percent: Icon size as a percentage of the smaller canvas
            dimension (1-100). 100 fills the panel's short side edge to edge.

    Returns:
        PNG-encoded bytes ready to pass to device.image.make_image_command.
    """
    scale_percent = max(1, min(100, int(scale_percent)))
    icon_size_px = max(1, round(min(canvas_width, canvas_height) * scale_percent / 100))

    svg_markup = await fetch_mdi_svg(icon, session)
    icon_img = render_mdi_icon(svg_markup, icon_size_px, color_hex)
    return compose_icon_canvas(icon_img, canvas_width, canvas_height, bg_color_hex)
