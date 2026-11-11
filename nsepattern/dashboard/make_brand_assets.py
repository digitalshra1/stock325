"""One-off helper: turn the brand logo into the files the dashboard uses.

    python -m nsepattern.dashboard.make_brand_assets path/to/logo.png

Creates nsepattern/dashboard/assets/:
  logo.webp        full logo for light mode
  logo-dark.webp   same, with the navy lettering switched to light (readable on dark backgrounds)
  icon-192.png / icon-512.png   shield on a navy tile (phone home-screen icon)
  favicon.png      shield only (browser tab)
To change the logo later, run this again with the new image and rebuild the dashboard.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ASSETS = Path(__file__).with_name("assets")
NAVY_TILE = (8, 20, 34, 255)
LIGHT_INK = (238, 242, 247)


def trim(im: Image.Image) -> Image.Image:
    box = im.getbbox()
    return im.crop(box) if box else im


def make(src: str):
    ASSETS.mkdir(exist_ok=True)
    im = trim(Image.open(src).convert("RGBA"))
    w, h = im.size
    # where the lettering starts: the emptiest column between the shield and the text
    # (only the thin divider line crosses it)
    filled = [sum(1 for y in range(0, h, 2) if im.getpixel((x, y))[3] > 40) for x in range(w)]
    lo, hi = int(w * 0.25), int(w * 0.45)
    shield_end = min(range(lo, hi), key=lambda x: filled[x])

    def save_scaled(img, name, height):
        r = img.resize((round(img.width * height / img.height), height), Image.LANCZOS)
        r.save(ASSETS / name, "WEBP", quality=92, method=6)

    save_scaled(im, "logo.webp", 220)
    dark = im.copy()
    px = dark.load()
    for x in range(shield_end, w):
        for y in range(h):
            r, g, b, a = px[x, y]
            if a and r < 90 and g < 100 and b < 120 and not (g > r + 40 and g > b + 40):
                px[x, y] = (*LIGHT_INK, a)
    save_scaled(dark, "logo-dark.webp", 220)

    shield = trim(im.crop((0, 0, shield_end, h)))
    side = max(shield.size)
    sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    sq.paste(shield, ((side - shield.width) // 2, (side - shield.height) // 2), shield)
    sq.resize((64, 64), Image.LANCZOS).save(ASSETS / "favicon.png")
    for size in (192, 512):
        tile = Image.new("RGBA", (size, size), NAVY_TILE)
        inner = sq.resize((int(size * 0.78), int(size * 0.78)), Image.LANCZOS)
        off = (size - inner.width) // 2
        tile.paste(inner, (off, off), inner)
        tile.save(ASSETS / f"icon-{size}.png")
    print("brand assets written to", ASSETS)


if __name__ == "__main__":
    make(sys.argv[1])
