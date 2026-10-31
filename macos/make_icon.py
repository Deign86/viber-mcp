"""Draws the "Viber MCP" app icon (purple squircle, white chat bubble) as macos/AppIcon.icns.

    uv run python macos/make_icon.py

Only needed to change the icon; setup_macos.sh uses the committed .icns.
"""
import os
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
S = 4  # supersampling for smooth edges
N = 1024 * S


def _gradient(top, bottom):
    img = Image.new("RGB", (1, N))
    for y in range(N):
        t = y / (N - 1)
        img.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)))
    return img.resize((N, N))


def draw():
    # macOS icon grid: 824 pt tile centred on a 1024 canvas, with a soft drop shadow
    m, r = 100 * S, 185 * S
    tile = Image.new("L", (N, N), 0)
    ImageDraw.Draw(tile).rounded_rectangle((m, m, N - m, N - m), r, fill=255)

    icon = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    shadow = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    shadow.putalpha(tile.point(lambda a: a * 70 // 255))
    shadow = shadow.transform((N, N), Image.AFFINE, (1, 0, 0, 0, 1, -12 * S))
    icon.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(14 * S)))
    bg = _gradient((0x8E, 0x6B, 0xF7), (0x5B, 0x3F, 0xC9)).convert("RGBA")
    bg.putalpha(tile)
    icon.alpha_composite(bg)

    # chat bubble with a tail at the bottom left
    d = ImageDraw.Draw(icon)
    white = (255, 255, 255, 255)
    bx0, by0, bx1, by1 = 250 * S, 285 * S, 774 * S, 665 * S
    d.rounded_rectangle((bx0, by0, bx1, by1), 150 * S, fill=white)
    d.polygon([(330 * S, 600 * S), (300 * S, 790 * S), (470 * S, 640 * S)], fill=white)

    # three dots: a conversation being typed
    purple = (0x6E, 0x4F, 0xDE, 255)
    cy, rad = (by0 + by1) // 2, 48 * S
    for cx in (382, 512, 642):
        d.ellipse((cx * S - rad, cy - rad, cx * S + rad, cy + rad), fill=purple)
    return icon.resize((1024, 1024), Image.LANCZOS)


def main(out=os.path.join(HERE, "AppIcon.icns")):
    big = draw()
    with tempfile.TemporaryDirectory() as tmp:
        iconset = os.path.join(tmp, "AppIcon.iconset")
        os.mkdir(iconset)
        for size in (16, 32, 128, 256, 512):
            big.resize((size, size), Image.LANCZOS).save(f"{iconset}/icon_{size}x{size}.png")
            big.resize((size * 2, size * 2), Image.LANCZOS).save(f"{iconset}/icon_{size}x{size}@2x.png")
        subprocess.run(["iconutil", "-c", "icns", iconset, "-o", out], check=True)
    print(out)


if __name__ == "__main__":
    main(*sys.argv[1:])
