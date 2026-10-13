"""Draw the app icon (a red map pin on a teal tile) as a multi-size .ico.

    python app/make_icon.py app/web/icon.ico
"""

from __future__ import annotations

import sys

from PIL import Image, ImageDraw

S = 256


def draw() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((8, 8, S - 8, S - 8), radius=48, fill=(31, 111, 120, 255))      # tile
    # two roads crossing the tile
    d.line((8, 190, S - 8, 120), fill=(236, 240, 241, 255), width=18)
    d.line((70, S - 8, 150, 8), fill=(236, 240, 241, 160), width=12)
    # pin: circle head + triangle tail, white hole
    cx, cy, r = 128, 100, 58
    d.polygon([(cx - 44, cy + 38), (cx + 44, cy + 38), (cx, 222)], fill=(209, 73, 91, 255))
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(209, 73, 91, 255))
    d.ellipse((cx - 22, cy - 22, cx + 22, cy + 22), fill=(255, 255, 255, 255))
    return img


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "icon.ico"
    draw().save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"wrote {out}")
