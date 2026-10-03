"""
Willy's logo, drawn with Pillow.

The window/taskbar icon, the tray icon (with its live status dot) and the WillyPC.exe icon
all come from here, so they always match.
"""

import os
from pathlib import Path
from typing import Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont

CYAN = "#00F2FE"
PURPLE = "#7C3AED"
BG = "#070913"
STATUS_COLORS = {"online": "#10B981", "connecting": "#F59E0B", "offline": "#EF4444"}
BOLT = 0xE945  # "lightning bolt" in Segoe Fluent Icons / Segoe MDL2 Assets
ICON_FONT_FILES = ("SegoeIcons.ttf", "segmdl2.ttf")
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def _rgb(color: str) -> Tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def icon_font_path() -> Optional[str]:
    fonts_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    return next((str(fonts_dir / f) for f in ICON_FONT_FILES if (fonts_dir / f).exists()), None)


def glyph(code: int, px: int, color: str) -> Optional[Image.Image]:
    """One icon-font glyph centred on a transparent px × px square (None without the font)."""
    path = icon_font_path()
    if not path:
        return None
    try:
        font = ImageFont.truetype(path, px)
        img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
        ch = chr(code)
        left, top, right, bottom = font.getbbox(ch)
        x = (px - (right - left)) / 2 - left
        y = (px - (bottom - top)) / 2 - top
        ImageDraw.Draw(img).text((x, y), ch, font=font, fill=color)
        return img
    except Exception:
        return None


def logo_image(px: int) -> Image.Image:
    """Rounded square with a cyan→purple gradient and a white bolt (4× supersampled)."""
    ss = 4
    big = px * ss
    ramp = Image.linear_gradient("L").rotate(-45, expand=True).resize((big, big))
    fill = Image.composite(Image.new("RGBA", (big, big), _rgb(CYAN) + (255,)),
                           Image.new("RGBA", (big, big), _rgb(PURPLE) + (255,)), ramp)
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, big - 1, big - 1), radius=big * 0.26, fill=255)
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    img.paste(fill, (0, 0), mask)
    bolt = glyph(BOLT, int(big * 0.62), "#FFFFFF")
    if bolt is not None:
        img.alpha_composite(bolt, ((big - bolt.width) // 2, (big - bolt.height) // 2))
    else:  # no icon font: a plain drawn bolt
        points = ((0.58, 0.14), (0.30, 0.56), (0.48, 0.56), (0.42, 0.86), (0.70, 0.44), (0.52, 0.44))
        ImageDraw.Draw(img).polygon([(x * big, y * big) for x, y in points], fill=(255, 255, 255, 255))
    return img.resize((px, px), Image.LANCZOS)


def status_icon(state: str, px: int = 64, logo: Optional[Image.Image] = None) -> Image.Image:
    """The logo with a status dot in the bottom-right corner (green online, amber
    connecting, red offline), sized to stay readable at 16 px in the tray."""
    base = logo.copy() if logo is not None and logo.size == (px, px) else logo_image(px)
    base = base.convert("RGBA")
    ss = 4
    big = px * ss
    layer = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    radius, ring = big * 0.22, big * 0.06
    c = big - radius - ring - 1
    draw.ellipse((c - radius - ring, c - radius - ring, c + radius + ring, c + radius + ring),
                 fill=_rgb(BG) + (255,))
    draw.ellipse((c - radius, c - radius, c + radius, c + radius),
                 fill=_rgb(STATUS_COLORS.get(state, STATUS_COLORS["offline"])) + (255,))
    base.alpha_composite(layer.resize((px, px), Image.LANCZOS))
    return base


def save_ico(path: Path, sizes: Sequence[int] = ICO_SIZES) -> Path:
    """Writes a multi-size .ico of the logo (used for WillyPC.exe and its shortcuts)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    logo_image(max(sizes)).save(path, format="ICO", sizes=[(s, s) for s in sizes])
    return path
