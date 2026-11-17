"""Text rendering for iPIXEL Color displays."""
from __future__ import annotations

import io
import logging
import os
from pathlib import Path
from typing import Any, Tuple

from PIL import Image, ImageDraw, ImageFont

from ..color import hex_to_rgb
from ..fonts import get_font_path

_LOGGER = logging.getLogger(__name__)

# Minimum font size to try
MIN_FONT_SIZE = 4
MARGIN_THRESHOLD = 64  # Pixel brightness threshold for margin detection


def render_text_to_png(text: str, width: int, height: int, antialias: bool = True, font_size: float | None = None, font: str | None = None, line_spacing: int = 0, text_color: str = "ffffff", bg_color: str = "000000") -> bytes:
    """Render text to PNG image data with color gradient mapping.

    Args:
        text: Text to render (supports multiline with \n)
        width: Display width in pixels
        height: Display height in pixels
        antialias: Enable antialiasing for smoother text (default: True)
        font_size: Fixed font size in pixels (can be fractional), or None for auto-sizing (default: None)
        font: Font name from fonts/ folder, or None for default (default: None)
        line_spacing: Additional spacing between lines in pixels (default: 0)
        text_color: Foreground/text color in hex format (e.g., 'ffffff') (default: white)
        bg_color: Background color in hex format (e.g., '000000') (default: black)

    Returns:
        PNG image data as bytes

    Note:
        Uses linear interpolation to map grayscale values to colors:
        - 0 (black) → bg_color
        - 255 (white) → text_color
        - Intermediate values → interpolated colors
    """
    # Create image with device dimensions
    # Use 'L' mode (grayscale) for non-antialiased to get sharper pixels
    image_mode = "RGB" if antialias else "1"
    gray_mode = "L" if antialias else "1"
    init_zero = (0,0,0) if antialias else 0
    gray_zero = 0

    if font_size == 0:
        font_size = None

    if not antialias:
        img = Image.new(image_mode, (width, height), init_zero)  # 1-bit pixels, black background
    else:
        img = Image.new(image_mode, (width, height), init_zero)
    draw = ImageDraw.Draw(img)
    
    # Process multiline text
    lines = text.split('\n') if '\n' in text else [text]
    
    # Get font - either fixed size or auto-optimized
    if font_size is not None:
        font_obj = get_fixed_font(font_size, font)
    else:
        font_obj = get_optimal_font(draw, lines, width, height, font, line_spacing)
    
    # Create temporary image to measure actual content bounds
    temp_img = Image.new(gray_mode, (width*2, height*2), gray_zero)  # Grayscale for easier analysis
    temp_draw = ImageDraw.Draw(temp_img)
    
    # Draw all text to measure actual content area and calculate per-line bounds
    temp_y = 0
    line_data = []
    for line in lines:
        bbox = temp_draw.textbbox((0, 0), line, font=font_obj)
        line_width = bbox[2] - bbox[0]
        line_height = bbox[3] - bbox[1]
        
        # Draw line on temporary image
        temp_draw.text((0, temp_y), line, font=font_obj, fill=255)

        l_left, l_top, l_right, l_bottom = (bbox[0], bbox[1], bbox[2], bbox[3])
        line_data.append({
            'text': line,
            'y_pos': temp_y,
            'content_left': l_left,
            'content_top': l_top,
            'content_right': l_right,
            'content_bottom': l_bottom,
            'content_width': line_width,
            'content_height': line_height
        })
        temp_y += line_height + line_spacing  # Add line spacing between lines

    total_height = sum(data['content_height'] for data in line_data)
    if len(lines) > 1:
        total_height += line_spacing * (len(lines) - 1)
    y_offset = (height - total_height) // 2

    print(line_data)
    # Draw each line with corrected positioning
    current_y = y_offset
    for i, (line, data) in enumerate(zip(lines, line_data)):
        # Calculate horizontal position for this specific line using pre-calculated bounds
        x = (width - data['content_width']) // 2 - data['content_left']
        adjusted_y = current_y - data["content_top"]

        
        # Draw the line with appropriate fill color
        if not antialias:
            draw.text((x, adjusted_y), line, font=font_obj, fill=1)  # 1 for white in 1-bit mode
        else:
            draw.text((x, adjusted_y), line, font=font_obj, fill=(255, 255, 255))
        
        # Move to next line position
        current_y += data['content_height'] + line_spacing  # Add line spacing between lines
    
    # Parse hex colors to RGB tuples using utility function
    try:
        bg_r, bg_g, bg_b = hex_to_rgb(bg_color)
        text_r, text_g, text_b = hex_to_rgb(text_color)
    except (ValueError, IndexError):
        _LOGGER.error("Invalid color format. Using defaults (white text, black background)")
        bg_r, bg_g, bg_b = 0, 0, 0
        text_r, text_g, text_b = 255, 255, 255

    # Apply color gradient mapping using linear interpolation
    # Convert grayscale image to RGB with gradient mapping
    if not antialias:
        # For 1-bit images, convert to grayscale first
        grayscale_img = img.point(lambda x: 255 if x else 0).convert('L')
    else:
        # For RGB images, convert to grayscale
        grayscale_img = img.convert('L')

    # Create RGB image with interpolated colors
    rgb_img = Image.new('RGB', (width, height))
    pixels_gray = grayscale_img.load()
    pixels_rgb = rgb_img.load()

    for y in range(height):
        for x in range(width):
            # Get grayscale value (0-255)
            gray_value = pixels_gray[x, y]

            # Linear interpolation: t = gray_value / 255.0
            # color = bg_color * (1-t) + text_color * t
            t = gray_value / 255.0

            r = int(bg_r * (1 - t) + text_r * t)
            g = int(bg_g * (1 - t) + text_g * t)
            b = int(bg_b * (1 - t) + text_b * t)

            pixels_rgb[x, y] = (r, g, b)

    # Convert to PNG bytes
    png_buffer = io.BytesIO()
    rgb_img.save(png_buffer, format='PNG')
    return png_buffer.getvalue()


def render_text_sprite(
    text: str,
    height_px: int,
    color_hex: str = "ffffff",
    font_name: str | None = None,
) -> Image.Image:
    """Scritta ritagliata ai suoi bordi, come "sprite" RGBA trasparente.

    Serve alla scritta libera del pannello: l'immagine viene poi
    appoggiata sul canvas del display (posizionata o fatta scorrere)
    con compose_icon_media, esattamente come le icone.

    Args:
        text: La scritta (una riga).
        height_px: Altezza voluta dei caratteri in pixel.
        color_hex: Colore della scritta ('ffffff' o '#ffffff').
        font_name: Font della cartella fonts/, o None per il default.

    Returns:
        PIL Image RGBA larga quanto la scritta, trasparente intorno.
    """
    # Sovracampionato: disegnato 4 volte piu' grande e poi ridotto con
    # LANCZOS + soglia. Disegnare il TTF direttamente a 8 px impastava
    # le lettere (la B non si distingueva dalla A).
    ss = 4
    font_obj = get_fixed_font(float(height_px * ss), font_name or "7x5.ttf")

    # Canvas provvisorio abbondante: poi si ritaglia al contenuto
    est_w = max(8, len(text) * height_px * ss * 2 + 16)
    tmp = Image.new("L", (est_w, height_px * ss * 3 + 16), 0)
    d = ImageDraw.Draw(tmp)
    d.text((4, 4), text, font=font_obj, fill=255)

    bbox = tmp.getbbox()
    if not bbox:
        return Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    tmp = tmp.crop(bbox)

    try:
        r, g, b = hex_to_rgb(color_hex.lstrip("#"))
    except (ValueError, IndexError):
        r, g, b = 255, 255, 255

    # Riduzione all'altezza esatta + soglia netta: i LED o accesi o spenti
    new_w = max(1, round(tmp.width * height_px / tmp.height))
    small = tmp.resize((new_w, height_px), Image.LANCZOS)
    mask = small.point(lambda v: 255 if v >= 110 else 0)
    sprite = Image.new("RGBA", mask.size, (0, 0, 0, 0))
    sprite.paste(Image.new("RGBA", mask.size, (r, g, b, 255)), (0, 0), mask)
    return sprite


# ---------------------------------------------------------------------------
# Font bitmap 5x7 disegnato a mano: a queste dimensioni (righe impilate da
# 7px, caselle rimpicciolite) un TTF ricampionato impasta i glifi in modo
# incoerente (la A sembrava una R, T/O/2 a volte sbagliati). Qui ogni glifo
# e' definito pixel per pixel, quindi tutte le lettere e i numeri escono
# netti e uniformi. Larghezza variabile (i simboli stretti sono piu' corti).
BITMAP_FONT: dict[str, tuple[str, ...]] = {
    "A": ("01110","10001","10001","11111","10001","10001","10001"),
    "B": ("11110","10001","10001","11110","10001","10001","11110"),
    "C": ("01110","10001","10000","10000","10000","10001","01110"),
    "D": ("11110","10001","10001","10001","10001","10001","11110"),
    "E": ("11111","10000","10000","11110","10000","10000","11111"),
    "F": ("11111","10000","10000","11110","10000","10000","10000"),
    "G": ("01110","10001","10000","10000","10011","10001","01110"),
    "H": ("10001","10001","10001","11111","10001","10001","10001"),
    "I": ("01110","00100","00100","00100","00100","00100","01110"),
    "J": ("00001","00001","00001","00001","00001","10001","01110"),
    "K": ("10001","10010","10100","11000","10100","10010","10001"),
    "L": ("10000","10000","10000","10000","10000","10000","11111"),
    "M": ("10001","11011","10101","10101","10001","10001","10001"),
    "N": ("10001","10001","11001","10101","10011","10001","10001"),
    "O": ("01110","10001","10001","10001","10001","10001","01110"),
    "P": ("11110","10001","10001","11110","10000","10000","10000"),
    "Q": ("01110","10001","10001","10001","10101","10010","01101"),
    "R": ("11110","10001","10001","11110","10100","10010","10001"),
    "S": ("01111","10000","10000","01110","00001","00001","11110"),
    "T": ("11111","00100","00100","00100","00100","00100","00100"),
    "U": ("10001","10001","10001","10001","10001","10001","01110"),
    "V": ("10001","10001","10001","10001","01010","01010","00100"),
    "W": ("10001","10001","10001","10101","10101","11011","10001"),
    "X": ("10001","10001","01010","00100","01010","10001","10001"),
    "Y": ("10001","10001","01010","00100","00100","00100","00100"),
    "Z": ("11111","00001","00010","00100","01000","10000","11111"),
    "0": ("01110","10001","10011","10101","11001","10001","01110"),
    "1": ("00100","01100","00100","00100","00100","00100","01110"),
    "2": ("01110","10001","00001","00010","00100","01000","11111"),
    "3": ("11111","00010","00100","00010","00001","10001","01110"),
    "4": ("00010","00110","01010","10010","11111","00010","00010"),
    "5": ("11111","10000","11110","00001","00001","10001","01110"),
    "6": ("00110","01000","10000","11110","10001","10001","01110"),
    "7": ("11111","00001","00010","00100","01000","01000","01000"),
    "8": ("01110","10001","10001","01110","10001","10001","01110"),
    "9": ("01110","10001","10001","01111","00001","00010","01100"),
    " ": ("000","000","000","000","000","000","000"),
    ".": ("00","00","00","00","00","11","11"),
    ",": ("00","00","00","00","00","01","10"),
    ":": ("00","11","11","00","11","11","00"),
    "/": ("00001","00010","00010","00100","01000","01000","10000"),
    "-": ("00000","00000","00000","11111","00000","00000","00000"),
    "+": ("00000","00000","00100","11111","00100","00000","00000"),
    "%": ("11001","11010","00100","00100","01000","10011","10011"),
    "°": ("111","101","111","000","000","000","000"),
    "a": ("00000","00000","01110","00001","01111","10001","01111"),
    "b": ("10000","10000","11110","10001","10001","10001","11110"),
    "c": ("00000","00000","01110","10001","10000","10001","01110"),
    "d": ("00001","00001","01111","10001","10001","10001","01111"),
    "e": ("00000","00000","01110","10001","11111","10000","01110"),
    "f": ("00110","01000","01000","11110","01000","01000","01000"),
    "g": ("00000","00000","01111","10001","01111","00001","01110"),
    "h": ("10000","10000","11110","10001","10001","10001","10001"),
    "i": ("00000","00100","00000","00100","00100","00100","00100"),
    "j": ("00000","00010","00000","00010","00010","10010","01100"),
    "k": ("10000","10000","10010","10100","11000","10100","10010"),
    "l": ("01100","00100","00100","00100","00100","00100","01110"),
    "m": ("00000","00000","11011","10101","10101","10101","10101"),
    "n": ("00000","00000","11110","10001","10001","10001","10001"),
    "o": ("00000","00000","01110","10001","10001","10001","01110"),
    "p": ("00000","00000","11110","10001","11110","10000","10000"),
    "q": ("00000","00000","01111","10001","01111","00001","00001"),
    "r": ("00000","00000","10110","11001","10000","10000","10000"),
    "s": ("00000","00000","01111","10000","01110","00001","11110"),
    "t": ("01000","01000","11110","01000","01000","01001","00110"),
    "u": ("00000","00000","10001","10001","10001","10011","01101"),
    "v": ("00000","00000","10001","10001","10001","01010","00100"),
    "w": ("00000","00000","10001","10001","10101","10101","01010"),
    "x": ("00000","00000","10001","01010","00100","01010","10001"),
    "y": ("00000","00000","10001","10001","01111","00001","01110"),
    "z": ("00000","00000","11111","00010","00100","01000","11111"),
}
_FONT_H = 7


def bitmap_text_mask(text: str, height_px: int) -> Tuple[Image.Image, int]:
    """Maschera 'L' (255 = LED acceso) della scritta col font bitmap 5x7.

    Scala a interi (nitida): scale = height_px // 7, min 1. Ritorna
    (maschera, larghezza). I glifi mancanti diventano spazio.
    """
    scale = max(1, int(height_px) // _FONT_H)
    gap = scale
    glyphs = []
    for ch in text:
        g = BITMAP_FONT.get(ch) or BITMAP_FONT.get(ch.upper()) or BITMAP_FONT.get(" ")
        glyphs.append(g)
    h = _FONT_H * scale
    total = sum(len(g[0]) * scale for g in glyphs) + gap * max(0, len(glyphs) - 1)
    if total <= 0:
        return Image.new("L", (1, h), 0), 0
    img = Image.new("L", (total, h), 0)
    px = img.load()
    x = 0
    for g in glyphs:
        gw = len(g[0])
        for gy, row in enumerate(g):
            for gx, bit in enumerate(row):
                if bit == "1":
                    for dy in range(scale):
                        yy = gy * scale + dy
                        for dx in range(scale):
                            px[x + gx * scale + dx, yy] = 255
        x += gw * scale + gap
    return img, total


def bitmap_text_sprite(text: str, height_px: int, color_hex: str = "ffffff") -> Image.Image:
    """Sprite RGBA della scritta col font bitmap 5x7, nel colore dato."""
    try:
        r, g, b = hex_to_rgb(color_hex.lstrip("#"))
    except (ValueError, IndexError):
        r, g, b = 255, 255, 255
    mask, _w = bitmap_text_mask(text, height_px)
    spr = Image.new("RGBA", mask.size, (0, 0, 0, 0))
    spr.paste(Image.new("RGBA", mask.size, (r, g, b, 255)), (0, 0), mask)
    return spr


def _calculate_content_bounds(img: Image.Image) -> tuple[int, int, int, int] | None:
    """Calculate actual content bounds by analyzing pixels.
    
    Args:
        img: Grayscale image with text content
        
    Returns:
        Tuple of (left, top, right, bottom) bounds or None if no content
    """
    width, height = img.size
    pixels = img.load()
    
    # Find top boundary (first row with bright pixels)
    top = None
    for y in range(height):
        for x in range(width):
            if pixels[x, y] > MARGIN_THRESHOLD:
                top = y
                break
        if top is not None:
            break
    
    if top is None:
        return None  # No content found
    
    # Find bottom boundary (last row with bright pixels)
    bottom = None
    for y in range(height - 1, -1, -1):
        for x in range(width):
            if pixels[x, y] > MARGIN_THRESHOLD:
                bottom = y + 1  # +1 because we want inclusive bounds
                break
        if bottom is not None:
            break
    
    # Find left boundary (first column with bright pixels)
    left = None
    for x in range(width):
        for y in range(top, bottom):
            if pixels[x, y] > MARGIN_THRESHOLD:
                left = x
                break
        if left is not None:
            break
    
    # Find right boundary (last column with bright pixels)
    right = None
    for x in range(width - 1, -1, -1):
        for y in range(top, bottom):
            if pixels[x, y] > MARGIN_THRESHOLD:
                right = x + 1  # +1 because we want inclusive bounds
                break
        if right is not None:
            break
    
    if left is None or right is None:
        return None
    
    _LOGGER.debug("Content bounds: left=%d, top=%d, right=%d, bottom=%d (content: %dx%d)", 
                 left, top, right, bottom, right - left, bottom - top)
    
    return left, top, right, bottom


def get_fixed_font(size: float, font_name: str | None = None) -> ImageFont.FreeTypeFont:
    """Get font with fixed size.
    
    Args:
        size: Font size in pixels (can be fractional)
        font_name: Optional font name from fonts/ folder
        
    Returns:
        Font object with the specified size
    """
    try:
        # Try to load custom font from fonts/ folder first
        if font_name:
            font_path = get_font_path(font_name)
            if font_path:
                try:
                    return ImageFont.truetype(str(font_path), size)
                except Exception as e:
                    _LOGGER.warning("Could not load custom font %s: %s", font_name, e)

        # Use default font if custom font failed or not specified
        return ImageFont.load_default()
    except Exception as e:
        _LOGGER.warning("Error loading font size %.1f: %s, using default", size, e)
        return ImageFont.load_default()


def get_optimal_font(draw: ImageDraw.Draw, lines: list[str], 
                     max_width: int, max_height: int, font_name: str | None = None, line_spacing: int = 0) -> ImageFont.FreeTypeFont:
    """Find the largest font size that fits all text within dimensions.
    
    Args:
        draw: ImageDraw object for text measurement
        lines: List of text lines to render
        max_width: Maximum width in pixels
        max_height: Maximum height in pixels
        font_name: Optional font name from fonts/ folder
        line_spacing: Additional spacing between lines in pixels
        
    Returns:
        Optimal font for the text
    """
    # Compute theoretical optimal size based on text proportions
    # Start with a baseline font to measure proportions
    baseline_size = 100.0
    baseline_font = get_fixed_font(baseline_size, font_name)
    
    # Find the longest line to determine width constraint
    longest_line = max(lines, key=len) if lines else ""
    
    # Measure baseline dimensions
    bbox = draw.textbbox((0, 0), longest_line, font=baseline_font)
    baseline_width = bbox[2] - bbox[0]
    baseline_line_height = bbox[3] - bbox[1]
    
    # Calculate total height with line spacing
    total_baseline_height = baseline_line_height * len(lines)
    if len(lines) > 1:
        total_baseline_height += line_spacing * (len(lines) - 1)
    
    # Calculate theoretical optimal size based on proportions
    width_ratio = max_width / baseline_width if baseline_width > 0 else 1.0
    height_ratio = max_height / total_baseline_height if total_baseline_height > 0 else 1.0
    theoretical_size = baseline_size * min(width_ratio, height_ratio)
    
    # Clamp to reasonable range
    theoretical_size = max(1.0, min(theoretical_size, min(max_height, max_width)))
    
    _LOGGER.debug("Theoretical optimal size: %.1f (width_ratio: %.2f, height_ratio: %.2f)",
                 theoretical_size, width_ratio, height_ratio)
    
    # Try sizes around the theoretical optimal with fine increments
    best_font = None
    best_size = 0.0
    
    # Test 3 iterations with refinement
    test_ranges = [
        (theoretical_size * 0.7, theoretical_size * 1.3, 2.0),  # Coarse: ±30% with 2.0 step
        (best_size - 2.0, best_size + 2.0, 0.5),                # Medium: ±2 with 0.5 step  
        (best_size - 0.5, best_size + 0.5, 0.1),                # Fine: ±0.5 with 0.1 step
    ]
    
    for iteration, (start, end, step) in enumerate(test_ranges):
        if iteration > 0 and best_size == 0:
            break  # Skip refinement if no valid size found
            
        current_start = max(1.0, start if iteration == 0 else start)
        current_end = min(min(max_height, max_width), end if iteration == 0 else end)
        
        size = current_start
        while size <= current_end:
            try:
                # Try to load font at this size
                font = None
                if font_name:
                    font_path = get_font_path(font_name)
                    if font_path:
                        try:
                            font = ImageFont.truetype(str(font_path), size)
                        except Exception as e:
                            _LOGGER.debug("Custom font %s failed at size %.1f: %s", font_name, size, e)
                
                # Use default font if custom font failed or not specified
                if font is None:
                    font = ImageFont.load_default()
                
                # Check if all lines fit within dimensions
                fits = True
                total_height = 0
                
                for line in lines:
                    bbox = draw.textbbox((0, 0), line, font=font)
                    text_width = bbox[2] - bbox[0]
                    text_height = bbox[3] - bbox[1]
                    
                    # Check if line fits horizontally
                    if text_width > max_width:
                        fits = False
                        break
                        
                    total_height += text_height
                
                # Add line spacing to total height
                if len(lines) > 1:
                    total_height += line_spacing * (len(lines) - 1)
                
                # Check if all lines fit vertically
                if total_height > max_height:
                    fits = False
                
                if fits and size > best_size:
                    best_size = size
                    best_font = font
                    _LOGGER.debug("Iteration %d: Found better size: %.1f (total height: %d/%d)", 
                                iteration + 1, size, total_height, max_height)
                    
            except Exception as e:
                _LOGGER.debug("Font size %.1f failed: %s", size, e)
            
            size += step
    
    # Return best font found
    if best_font:
        _LOGGER.debug("Using optimal font size: %.1f", best_size)
        return best_font
    
    # Fallback to minimum font size
    _LOGGER.warning("Using fallback font - text may not fit optimally")
    return get_fixed_font(1.0, font_name)