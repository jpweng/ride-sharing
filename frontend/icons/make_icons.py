"""
Generates the logo and the app icons from one description of the logo: a
route in two halves, white from the top right dot and orange from the bottom
left dot, on a dark navy rounded square.

Run from the repo root, then commit the files:

    uv run --with pillow python frontend/icons/make_icons.py

To change the logo, change the geometry or colours below and regenerate; keep
the file names and sizes, which the manifest, the HTML, style.css and
mobile/android/twa-manifest.json refer to:

    logo.svg                    the logo as a vector image (the header's mark)
    icon-192.png, icon-512.png  "any" purpose: rounded square, transparent corners
    maskable-512.png            "maskable" purpose: full bleed; the route is
                                inside the safe zone (the centre circle of 80%
                                diameter), since Android crops it to its own shape
    apple-touch-icon.png        180x180, full bleed and opaque (iOS rounds the
                                corners itself and turns transparency black)
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent

NAVY = "#1e2235"
WHITE = "#ffffff"
ORANGE = "#f06b45"

# The logo on a 180x180 grid. The orange half is the white half turned by
# 180 degrees around the centre, where the two meet.
GRID = 180
CENTRE = GRID / 2
CORNER = 40  # radius of the square's corners
STROKE = 14  # width of the route
DOT = 12.5  # radius of the dots at the two ends
BEND = 15  # radius of the route's bends, on its centre line
WHITE_DOT = (129.5, 61)

SUPERSAMPLE = 4  # draw large, then downscale, for smooth edges


def _turned(point):
    """The point turned by 180 degrees around the centre."""
    return (GRID - point[0], GRID - point[1])


def _fmt(number):
    return f"{number:g}"


def logo_svg():
    x, y = WHITE_DOT
    ox, oy = _turned(WHITE_DOT)
    straight = CENTRE - y - BEND  # the vertical part of each half
    white = f"M{_fmt(x)} {_fmt(y)}H{_fmt(CENTRE + BEND)}a{BEND} {BEND} 0 0 0-{BEND} {BEND}v{_fmt(straight)}"
    orange = f"M{_fmt(CENTRE)} {_fmt(CENTRE)}v{_fmt(straight)}a{BEND} {BEND} 0 0 1-{BEND} {BEND}H{_fmt(ox)}"
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {GRID} {GRID}" role="img" aria-label="Ride and Rail">\n'
        f'  <rect width="{GRID}" height="{GRID}" rx="{CORNER}" fill="{NAVY}"/>\n'
        f'  <path d="{white}" fill="none" stroke="{WHITE}" stroke-width="{STROKE}"/>\n'
        f'  <path d="{orange}" fill="none" stroke="{ORANGE}" stroke-width="{STROKE}"/>\n'
        f'  <circle cx="{_fmt(x)}" cy="{_fmt(y)}" r="{_fmt(DOT)}" fill="{WHITE}"/>\n'
        f'  <circle cx="{_fmt(ox)}" cy="{_fmt(oy)}" r="{_fmt(DOT)}" fill="{ORANGE}"/>\n'
        "</svg>\n"
    )


def _arc(centre, radius, start, end, steps=24):
    """Points on a circle from one angle to another, in degrees (0 = right, 90 = down)."""
    angles = (math.radians(start + (end - start) * step / steps) for step in range(steps + 1))
    return [(centre[0] + radius * math.cos(angle), centre[1] + radius * math.sin(angle)) for angle in angles]


def _white_outline():
    """The white half of the route as a polygon: from its dot, left, round the bend, down to the centre."""
    x, y = WHITE_DOT
    half = STROKE / 2
    bend_centre = (CENTRE + BEND, y + BEND)
    return (
        [(x, y - half)]
        + _arc(bend_centre, BEND + half, -90, -180)
        + [(CENTRE - half, CENTRE), (CENTRE + half, CENTRE)]
        + _arc(bend_centre, BEND - half, -180, -90)
        + [(x, y + half)]
    )


def draw_icon(size, *, rounded):
    big = size * SUPERSAMPLE
    scale = big / GRID
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    if rounded:
        draw.rounded_rectangle((0, 0, big - 1, big - 1), radius=round(CORNER * scale), fill=NAVY)
    else:
        draw.rectangle((0, 0, big, big), fill=NAVY)

    halves = (
        (WHITE_DOT, _white_outline(), WHITE),
        (_turned(WHITE_DOT), [_turned(point) for point in _white_outline()], ORANGE),
    )
    for (cx, cy), outline, colour in halves:
        draw.polygon([(px * scale, py * scale) for px, py in outline], fill=colour)
        radius = DOT * scale
        draw.ellipse(((cx * scale - radius, cy * scale - radius), (cx * scale + radius, cy * scale + radius)), fill=colour)

    img = img.resize((size, size), Image.LANCZOS)
    return img if rounded else img.convert("RGB")


def main():
    (HERE / "logo.svg").write_text(logo_svg())
    print(f"wrote {HERE / 'logo.svg'}")

    icons = {
        "icon-192.png": dict(size=192, rounded=True),
        "icon-512.png": dict(size=512, rounded=True),
        # Safe zone: a circle of 80% diameter; the route's dots reach 68%.
        "maskable-512.png": dict(size=512, rounded=False),
        "apple-touch-icon.png": dict(size=180, rounded=False),
    }
    for name, spec in icons.items():
        draw_icon(spec["size"], rounded=spec["rounded"]).save(HERE / name, optimize=True)
        print(f"wrote {HERE / name}")


if __name__ == "__main__":
    main()
