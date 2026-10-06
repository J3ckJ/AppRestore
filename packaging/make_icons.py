"""Render the AppRestore app icon (macOS .icns, Windows .ico, PNG previews).

The mark follows the v5 sidebar logo: a terracotta tile with a white phone
outline and an arrow returning into the phone.  Everything is drawn from
geometry (supersampled, then downscaled), so the icon is reproducible:

    python packaging/make_icons.py
"""

from __future__ import annotations

import io
import math
import struct
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "icons"
GUI_RES = ROOT.parent / "apprestore_gui" / "resources" / "icons"

TOP = (0xD7, 0x6E, 0x3D)     # lit top of the terracotta
BOTTOM = (0xB3, 0x4F, 0x20)  # deeper bottom (theme ACCENT #C45C2A in between)
WHITE = (255, 255, 255, 255)


def superellipse(cx: float, cy: float, half: float, n: float = 5.0, steps: int = 720):
    pts = []
    for i in range(steps):
        t = 2 * math.pi * i / steps
        c, s = math.cos(t), math.sin(t)
        x = half * math.copysign(abs(c) ** (2 / n), c)
        y = half * math.copysign(abs(s) ** (2 / n), s)
        pts.append((cx + x, cy + y))
    return pts


def squircle(cx: float, cy: float, half: float, corner: float, n: float = 3.4, steps: int = 90):
    """Rounded square with continuous (superellipse) corners, like Apple's tile.

    Straight sides, each corner is a quarter superellipse spanning ``corner``.
    """

    pts = []
    centers = [(1, 1), (-1, 1), (-1, -1), (1, -1)]
    for q, (sx, sy) in enumerate(centers):
        ccx = cx + sx * (half - corner)
        ccy = cy + sy * (half - corner)
        for i in range(steps + 1):
            t = (q + i / steps) * math.pi / 2
            c, s = math.cos(t), math.sin(t)
            x = corner * math.copysign(abs(c) ** (2 / n), c)
            y = corner * math.copysign(abs(s) ** (2 / n), s)
            pts.append((ccx + x, ccy + y))
    return pts


def _gradient(size: int) -> Image.Image:
    grad = Image.new("RGBA", (1, 256))
    for y in range(256):
        k = y / 255
        grad.putpixel((0, y), tuple(round(a + (b - a) * k) for a, b in zip(TOP, BOTTOM)) + (255,))
    return grad.resize((size, size), Image.BICUBIC)


def _round_line(draw: ImageDraw.ImageDraw, p0, p1, width: float) -> None:
    draw.line([p0, p1], fill=WHITE, width=round(width))
    r = width / 2
    for x, y in (p0, p1):
        draw.ellipse([x - r, y - r, x + r, y + r], fill=WHITE)


def _glyph(size: int, scale: float, small: bool, dy: float = 0) -> Image.Image:
    """White phone + arrow returning into it; geometry in 1024 units."""

    s = size / 1024
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    stroke = (64 if small else 44) * scale
    w, h = (360 if small else 344) * scale, (520 if small else 512) * scale
    cx, cy = 512, 560 + dy
    x0, y0, x1, y1 = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
    radius = (78 if small else 74) * scale
    # PIL strokes inward, so the stroke centre line sits stroke/2 inside.
    d.rounded_rectangle([x0 * s, y0 * s, x1 * s, y1 * s], radius=radius * s, outline=WHITE, width=round(stroke * s))
    edge_y = y0 + stroke / 2

    # Opening in the top edge where the arrow goes back into the phone.
    gap = stroke / 2 + (stroke * 0.5 + (52 if small else 46) * scale)
    cut = Image.new("L", (size, size), 0)
    ImageDraw.Draw(cut).rectangle(
        [(cx - gap) * s, (y0 - 4) * s, (cx + gap) * s, (y0 + stroke + 4) * s], fill=255
    )
    layer.putalpha(ImageChops.subtract(layer.getchannel("A"), cut))
    r = stroke / 2
    for ex in (cx - gap, cx + gap):
        d.ellipse([(ex - r) * s, (edge_y - r) * s, (ex + r) * s, (edge_y + r) * s], fill=WHITE)

    arrow = stroke
    top_y = y0 - (96 if small else 118) * scale
    tip_y = cy + (30 if small else 22) * scale
    head = (68 if small else 80) * scale
    _round_line(d, (cx * s, top_y * s), (cx * s, (tip_y - arrow * 0.3) * s), arrow * s)
    _round_line(d, ((cx - head) * s, (tip_y - head) * s), (cx * s, tip_y * s), arrow * s)
    _round_line(d, ((cx + head) * s, (tip_y - head) * s), (cx * s, tip_y * s), arrow * s)

    if not small:
        bar = 40 * scale
        by = y1 - stroke - 44 * scale
        _round_line(d, ((cx - bar) * s, by * s), ((cx + bar) * s, by * s), 20 * scale * s)
    return layer


def render(size: int, style: str) -> Image.Image:
    """style: 'mac' (squircle on Apple grid with shadow), 'win' or 'win-small'."""

    ss = 8 if size <= 64 else 4 if size <= 256 else 2
    big = size * ss
    canvas = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    k = big / 1024
    if style == "mac":
        half, cy, glyph_scale, corner = 412, 512 - 6, 0.86, 300
    else:
        half, cy, glyph_scale, corner = 496, 512, 1.0 if style == "win" else 1.12, 330
    outline = squircle(512, cy, half, corner)
    shape = Image.new("L", (big, big), 0)
    ImageDraw.Draw(shape).polygon([(x * k, y * k) for x, y in outline], fill=255)
    if style == "mac":
        shadow = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        offset = Image.new("L", (big, big), 0)
        offset.paste(shape, (0, round(12 * k)))
        shadow.putalpha(offset.point(lambda a: a * 0.32).filter(ImageFilter.GaussianBlur(14 * k)))
        canvas.alpha_composite(shadow)
    tile = _gradient(big)
    tile.putalpha(shape)
    canvas.alpha_composite(tile)
    if style == "mac":
        # Hairline highlight along the top inner edge, very subtle.
        inner = Image.new("L", (big, big), 0)
        ImageDraw.Draw(inner).polygon(
            [(x * k, (y + 5) * k) for x, y in squircle(512, cy, half - 3, corner - 3)], fill=255
        )
        rim = ImageChops.subtract(shape, inner).point(lambda a: a * 0.22)
        light = Image.new("RGBA", (big, big), (255, 255, 255, 0))
        light.putalpha(rim)
        canvas.alpha_composite(light)
    glyph = _glyph(big, glyph_scale, small=(style == "win-small"), dy=-6 if style == "mac" else 0)
    canvas.alpha_composite(glyph)
    return canvas.resize((size, size), Image.LANCZOS)


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def write_icns(path: Path) -> None:
    types = [
        ("icp4", 16), ("icp5", 32), ("icp6", 64), ("ic07", 128), ("ic08", 256),
        ("ic09", 512), ("ic10", 1024), ("ic11", 32), ("ic12", 64), ("ic13", 256), ("ic14", 512),
    ]
    cache: dict[int, bytes] = {}
    body = b""
    for code, px in types:
        data = cache.setdefault(px, _png(render(px, "mac")))
        body += code.encode() + struct.pack(">I", len(data) + 8) + data
    path.write_bytes(b"icns" + struct.pack(">I", len(body) + 8) + body)


def write_ico(path: Path) -> None:
    sizes = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]
    images = [_png(render(px, "win-small" if px <= 32 else "win")) for px in sizes]
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries = b""
    for px, data in zip(sizes, images):
        dim = 0 if px >= 256 else px
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    path.write_bytes(header + entries + b"".join(images))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    render(1024, "mac").save(OUT / "AppRestore-1024.png")
    render(512, "win").save(OUT / "AppRestore-windows-512.png")
    write_icns(OUT / "AppRestore.icns")
    write_ico(OUT / "AppRestore.ico")
    # Window icon used by Qt at runtime (title bar, Dock/taskbar when unbundled).
    render(256, "mac").save(GUI_RES / "app-icon-256.png")
    render(64, "win").save(GUI_RES / "app-icon-64.png")
    # Contact sheet for review.
    sheet_sizes = [16, 32, 48, 64, 128, 256]
    sheet = Image.new("RGBA", (40 + sum(sheet_sizes) + 30 * len(sheet_sizes), 320), (236, 236, 240, 255))
    x = 20
    for px in sheet_sizes:
        img = render(px, "win-small" if px <= 32 else "win")
        sheet.alpha_composite(img, (x, 160 - px // 2))
        x += px + 30
    sheet.save(OUT / "AppRestore-sizes-preview.png")
    for p in sorted(OUT.iterdir()):
        print(p, p.stat().st_size)


if __name__ == "__main__":
    main()
