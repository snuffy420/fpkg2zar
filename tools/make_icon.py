"""Builds the app icon: fpkg2zar/assets/icon.svg, icon.png and icon.ico (16-256 px), plus assets/icon.png for the README.

Run from the project folder:  py -3 tools\\make_icon.py
Needs only PyQt6. The design is the blue-tint disc with data rings and a Z with a curved arrow in the hub.
"""

import os
import struct
import sys

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PyQt6.QtGui import QGuiApplication, QImage, QPainter
from PyQt6.QtSvg import QSvgRenderer

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(HERE, "fpkg2zar", "assets")
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)

N, L, W = "#0b1a36", "#4da3ff", "#ffffff"


def svg() -> str:
    rings = "".join(
        f'<circle cx="64" cy="64" r="{30 + i * 1.4:.1f}" fill="none" stroke="#9cc8ff" stroke-width="0.6" opacity="0.45"/>'
        for i in range(12)
    )
    return f"""<svg viewBox="0 0 128 128" width="128" height="128" xmlns="http://www.w3.org/2000/svg">
<rect x="4" y="4" width="120" height="120" rx="28" fill="{N}"/>
<circle cx="64" cy="64" r="47" fill="#1f4f94"/>{rings}
<path d="M64 64 L22 40 A47 47 0 0 1 46 20 Z" fill="{W}" opacity="0.35"/>
<path d="M64 64 L106 88 A47 47 0 0 1 82 108 Z" fill="{W}" opacity="0.25"/>
<path d="M64 64 L30 96 A47 47 0 0 1 22 86 Z" fill="{L}" opacity="0.5"/>
<circle cx="64" cy="64" r="47" fill="none" stroke="{L}" stroke-width="3"/>
<circle cx="64" cy="64" r="28" fill="#08142b"/>
<circle cx="64" cy="64" r="28" fill="none" stroke="{L}" stroke-width="1.5" opacity="0.8"/>
<path d="M54 54 h18 l-18 20 h18" stroke="{W}" stroke-width="5.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
<path d="M44 50 A22 22 0 0 1 80 46" stroke="{L}" stroke-width="3" fill="none" stroke-linecap="round"/>
<path d="M76 40 l5 6 l-7 3" stroke="{L}" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
</svg>
"""


def render(renderer: QSvgRenderer, size: int) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(p)
    p.end()
    return img


def png_bytes(img: QImage) -> bytes:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba)


def main():
    app = QGuiApplication(sys.argv)  # noqa: F841 (needed for painting)
    text = svg()
    with open(os.path.join(ASSETS, "icon.svg"), "w", encoding="utf-8") as f:
        f.write(text)
    r = QSvgRenderer(QByteArray(text.encode()))
    assert r.isValid()
    pngs = [(s, png_bytes(render(r, s))) for s in SIZES]
    render(r, 256).save(os.path.join(ASSETS, "icon.png"))
    os.makedirs(os.path.join(HERE, "assets"), exist_ok=True)  # README images
    render(r, 256).save(os.path.join(HERE, "assets", "icon.png"))
    # ICO with PNG-compressed entries (supported since Windows Vista).
    head = struct.pack("<HHH", 0, 1, len(pngs))
    offset = 6 + 16 * len(pngs)
    dir_, data = b"", b""
    for s, b in pngs:
        dim = 0 if s >= 256 else s
        dir_ += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(b), offset + len(data))
        data += b
    with open(os.path.join(ASSETS, "icon.ico"), "wb") as f:
        f.write(head + dir_ + data)
    print("wrote icon.svg, icon.png, icon.ico", SIZES)


if __name__ == "__main__":
    main()
