"""
Generates the placeholder app icons (there is no logo yet): "R&R" in white on
the app's teal, in the style of the text wordmark ("&" lighter and italic).

Run from the repo root, then commit the PNGs:

    uv run --with pillow python frontend/icons/make_icons.py

When a real logo exists, replace this script's drawing (or the PNGs) and keep
the same file names and sizes, which the manifest, the HTML and
mobile/android/twa-manifest.json refer to:

    icon-192.png, icon-512.png  "any" purpose: rounded square, transparent corners
    maskable-512.png            "maskable" purpose: full bleed, the text inside the
                                safe zone (the centre circle of 80% diameter),
                                since Android crops it to its own shape
    apple-touch-icon.png        180x180, full bleed and opaque (iOS rounds the
                                corners itself and turns transparency black)

Fonts: Ubuntu Bold / Italic if installed, else DejaVu Sans; pass --bold-font
and --italic-font to use others.
"""
import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent

# From frontend/style.css (:root).
TEAL = "#0f6e5f"  # --accent
WHITE = "#ffffff"
SOFT = "#a9d8cf"  # a lighter --accent-soft, readable on teal

BOLD_FONTS = [
    "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]
ITALIC_FONTS = [
    "/usr/share/fonts/truetype/ubuntu/Ubuntu-RI.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/Library/Fonts/Arial Italic.ttf",
    "C:/Windows/Fonts/ariali.ttf",
]

SUPERSAMPLE = 4  # draw large, then downscale, for smooth edges


def _font(path, size, weight):
    """Loads a font; picks the named weight of a variable font (e.g. Ubuntu's)."""
    font = ImageFont.truetype(path, size)
    try:
        font.set_variation_by_name(weight)
    except (OSError, ValueError):
        pass  # not a variable font, or no such style: use it as is
    return font


def _first_existing(paths):
    for path in paths:
        if Path(path).is_file():
            return path
    raise SystemExit(f"No font found among {paths}; pass --bold-font / --italic-font")


def draw_icon(size, bold_path, italic_path, *, text_width, rounded):
    """`text_width` is the width of "R&R" as a fraction of the icon's width."""
    big = size * SUPERSAMPLE
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    if rounded:
        draw.rounded_rectangle((0, 0, big - 1, big - 1), radius=round(big * 0.22), fill=TEAL)
    else:
        draw.rectangle((0, 0, big, big), fill=TEAL)

    # Find the font size at which "R&R" spans text_width of the icon.
    def layout(font_size):
        bold = _font(bold_path, font_size, "Bold")
        italic = _font(italic_path, round(font_size * 0.8), "Regular")
        parts = [("R", bold, WHITE), ("&", italic, SOFT), ("R", bold, WHITE)]
        gap = font_size * 0.04
        widths = [draw.textlength(text, font=font) for text, font, _ in parts]
        return parts, widths, gap, sum(widths) + gap * 2

    font_size = big
    parts, widths, gap, total = layout(font_size)
    font_size = round(font_size * text_width * big / total)
    parts, widths, gap, total = layout(font_size)

    # Centre vertically on the capital letters' height (baseline anchors).
    bold = parts[0][1]
    top, bottom = bold.getbbox("R", anchor="ls")[1], 0
    baseline = big / 2 + (bottom - top) / 2
    x = (big - total) / 2
    for (text, font, colour), width in zip(parts, widths):
        draw.text((x, baseline), text, font=font, fill=colour, anchor="ls")
        x += width + gap

    img = img.resize((size, size), Image.LANCZOS)
    return img if rounded else img.convert("RGB")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bold-font")
    parser.add_argument("--italic-font")
    args = parser.parse_args()
    bold = args.bold_font or _first_existing(BOLD_FONTS)
    italic = args.italic_font or _first_existing(ITALIC_FONTS)

    icons = {
        "icon-192.png": dict(size=192, text_width=0.66, rounded=True),
        "icon-512.png": dict(size=512, text_width=0.66, rounded=True),
        # Safe zone: a circle of 80% diameter; "R&R" at 56% width stays in it.
        "maskable-512.png": dict(size=512, text_width=0.56, rounded=False),
        "apple-touch-icon.png": dict(size=180, text_width=0.62, rounded=False),
    }
    for name, spec in icons.items():
        img = draw_icon(spec["size"], bold, italic, text_width=spec["text_width"], rounded=spec["rounded"])
        img.save(HERE / name, optimize=True)
        print(f"wrote {HERE / name}")


if __name__ == "__main__":
    main()
