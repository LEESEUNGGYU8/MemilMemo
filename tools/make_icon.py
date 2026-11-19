"""메밀메모 아이콘을 그려서 SVG·PNG·ICO 로 내보낸다.

    python tools/make_icon.py                    # etc/brand/memilmemo 에 만든다(확인용 그림 포함)
    python tools/make_icon.py --squares solid    # 네모를 속이 찬 것으로
    python tools/make_icon.py --install          # Resources/Image 의 Icon.png·Icon.ico 를 바꾼다

그림: 살짝 기운 붙임쪽지 한가운데에 붉은 도장을 찍고, 도장 안에 정사각형 네모
넷을 2x2 로 놓았다. 네 글자를 2x2 로 새기는 인장에서 왔고, 네모 넷은 메밀메모의
초성 ㅁㅁㅁㅁ 이다. 도장은 쪽지의 위아래·좌우 한가운데에 있다.

24px 이하는 따로 그린 그림을 쓴다. 기운 그림을 그대로 줄이면 가장자리가
뭉개지므로 바로 세우고, 모든 변을 화소 경계에 맞췄다."""
from __future__ import annotations

import argparse
import io
import os
import shutil
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QByteArray, QBuffer, QRect, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QColor, QFont, QFontDatabase, QGuiApplication, QImage, QPainter,
)
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

from app import config  # noqa: E402

# ── 색 ──────────────────────────────────────────────────────────────────
PAPER = "#FFDA5C"
FLAP = "#E7B62E"           # 접힌 귀
FLAP_SHADE = "#B98B12"
SEAL = "#D8412D"           # 인주
SEAL_INK = "#FFF3D6"       # 도장 안의 네모

TILT = -5.0                # 쪽지가 기운 각도(도)
HOLLOW, SOLID = "hollow", "solid"

ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
# 작은 그림(한 변의 화소 수 → 쪽지 여백, 모서리, 귀, 도장 한 변, 네모 한 변, 네모 사이)
_SMALL = {
    16: dict(inset=1, radius=2.5, fold=3, seal=8, square=2, gap=2),
    20: dict(inset=1, radius=3.0, fold=4, seal=10, square=3, gap=2),
    24: dict(inset=1, radius=3.5, fold=5, seal=12, square=4, gap=2),
}


def _rrect(x: float, y: float, w: float, h: float, r: float) -> str:
    r = max(0.0, min(r, w / 2, h / 2))
    if r == 0:
        return f"M{x:.2f},{y:.2f} h{w:.2f} v{h:.2f} h{-w:.2f} Z"
    return (f"M{x + r:.2f},{y:.2f} H{x + w - r:.2f} "
            f"A{r:.2f},{r:.2f} 0 0 1 {x + w:.2f},{y + r:.2f} V{y + h - r:.2f} "
            f"A{r:.2f},{r:.2f} 0 0 1 {x + w - r:.2f},{y + h:.2f} H{x + r:.2f} "
            f"A{r:.2f},{r:.2f} 0 0 1 {x:.2f},{y + h - r:.2f} V{y + r:.2f} "
            f"A{r:.2f},{r:.2f} 0 0 1 {x + r:.2f},{y:.2f} Z")


def _note(x0: float, y0: float, x1: float, y1: float, r: float,
          fold: float, shade: float) -> str:
    """오른쪽 아래 귀가 접힌 붙임쪽지."""
    body = (f"M{x0 + r},{y0} H{x1 - r} A{r},{r} 0 0 1 {x1},{y0 + r} "
            f"V{y1 - fold} L{x1 - fold},{y1} H{x0 + r} "
            f"A{r},{r} 0 0 1 {x0},{y1 - r} V{y0 + r} "
            f"A{r},{r} 0 0 1 {x0 + r},{y0} Z")
    ax, ay, cx, cy, fr = x1 - fold, y1, x1, y1 - fold, fold * 0.3
    out = f'<path fill="{PAPER}" d="{body}"/>'
    if shade > 0:
        out += (f'<path fill="{FLAP_SHADE}" opacity="0.30" '
                f'd="M{ax},{ay} L{cx},{cy} L{cx},{cy + shade} L{ax + shade},{ay} Z"/>')
    out += (f'<path fill="{FLAP}" d="M{ax},{ay} V{cy + fr} '
            f'A{fr},{fr} 0 0 1 {ax + fr},{cy} H{cx} Z"/>')
    return out


def _squares(cx: float, cy: float, side: float, gap: float, *, kind: str,
             radius: float, stroke: float) -> str:
    """가운데가 (cx, cy) 인 2x2 정사각형 네모."""
    first = -(side + gap / 2)
    out = ""
    for dy in (first, gap / 2):
        for dx in (first, gap / 2):
            x, y = cx + dx, cy + dy
            d = _rrect(x, y, side, side, radius)
            if kind == HOLLOW:
                inner = side - 2 * stroke
                d += " " + _rrect(x + stroke, y + stroke, inner, inner,
                                  radius * 0.3)
            out += f'<path fill="{SEAL_INK}" fill-rule="evenodd" d="{d}"/>'
    return out


def svg_large(kind: str = HOLLOW) -> str:
    """32px 이상에서 쓰는 그림(256 눈금)."""
    c = 128.0                               # 쪽지도 도장도 네모 묶음도 이 점이 한가운데다
    half_note, half_seal = 106.0, 62.0
    note = _note(c - half_note, c - half_note, c + half_note, c + half_note,
                 20, 40, 8)
    seal = (f'<path fill="{SEAL}" d="'
            f'{_rrect(c - half_seal, c - half_seal, 2 * half_seal, 2 * half_seal, 16)}"/>')
    squares = _squares(c, c, 40, 14, kind=kind, radius=5, stroke=10)
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" '
            'width="256" height="256">\n'
            f'<g transform="rotate({TILT} {c} {c})">{note}{seal}{squares}</g>\n</svg>\n')


def svg_small(px: int, kind: str = HOLLOW) -> str:
    """24px 이하에서 쓰는 그림. 눈금 하나가 화소 하나다."""
    g = _SMALL[px]
    c = px / 2
    note = _note(g["inset"], g["inset"], px - g["inset"], px - g["inset"],
                 g["radius"], g["fold"], 0)
    half = g["seal"] / 2
    seal = (f'<path fill="{SEAL}" d="'
            f'{_rrect(c - half, c - half, g["seal"], g["seal"], g["seal"] * 0.14)}"/>')
    # 속을 비울 만큼 큰 것은 24px 뿐이다(네모 4화소, 획 1화소).
    hollow = kind == HOLLOW and g["square"] >= 4
    squares = _squares(c, c, g["square"], g["gap"],
                       kind=HOLLOW if hollow else SOLID,
                       radius=0.5 if g["square"] >= 4 else 0, stroke=1)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {px} {px}" '
            f'width="{px}" height="{px}">\n{note}{seal}{squares}\n</svg>\n')


def svg_for(size: int, kind: str = HOLLOW) -> str:
    return svg_small(size, kind) if size in _SMALL else svg_large(kind)


def render(svg: str, size: int) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    QSvgRenderer(QByteArray(svg.encode("utf-8"))).render(p, QRectF(0, 0, size, size))
    p.end()
    return img


def frame(size: int, kind: str = HOLLOW) -> QImage:
    return render(svg_for(size, kind), size)


def _png_bytes(img: QImage) -> bytes:
    buf = QBuffer()
    buf.open(QBuffer.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


def write_ico(path: Path, kind: str = HOLLOW) -> None:
    from PIL import Image

    frames = [Image.open(io.BytesIO(_png_bytes(frame(s, kind)))).convert("RGBA")
              for s in ICO_SIZES]
    frames[-1].save(path, format="ICO", sizes=[(s, s) for s in ICO_SIZES],
                    append_images=frames[:-1])


def preview(path: Path) -> None:
    """확인용 그림: 속 빈 네모와 속 찬 네모를 나란히, 큰 그림과 크기별 모습."""
    fid = QFontDatabase.addApplicationFont(str(config.FONT_PATH))
    family = (QFontDatabase.applicationFontFamilies(fid) or ["sans-serif"])[0]
    kinds = ((HOLLOW, "속 빈 네모"), (SOLID, "속 찬 네모"))
    sizes = (64, 48, 32, 24, 20, 16)
    zoom = (16, 20, 24)                      # 작은 크기를 화소가 보이게 키운 것
    pad, big, strip_h, zoom_px = 24, 256, 88, 5
    col_w = big + 2 * pad + 40
    w = pad + len(kinds) * (col_w + pad)
    zoom_h = max(zoom) * zoom_px
    h = pad + 36 + big + 2 * pad + 10 + 2 * (strip_h + 10) + zoom_h + 2 * pad
    sheet = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    sheet.fill(QColor("#EEF0F3"))
    p = QPainter(sheet)
    p.setRenderHint(QPainter.TextAntialiasing, True)
    title = QFont(family)
    title.setPixelSize(19)
    title.setWeight(QFont.Weight.Bold)
    for i, (kind, label) in enumerate(kinds):
        x = pad + i * (col_w + pad)
        p.setFont(title)
        p.setPen(QColor("#1F2937"))
        p.drawText(QRect(x, pad, col_w, 28), Qt.AlignLeft | Qt.AlignVCenter, label)
        y = pad + 36
        p.fillRect(x, y, col_w, big + 2 * pad, QColor("#FFFFFF"))
        p.drawImage(x + (col_w - big) // 2, y + pad, frame(big, kind))
        y += big + 2 * pad + 10
        for ground in ("#F7F8FA", "#1B2330"):
            p.fillRect(x, y, col_w, strip_h, QColor(ground))
            sx = x + 14
            for s in sizes:
                p.drawImage(sx, y + (strip_h - s) // 2, frame(s, kind))
                sx += s + 14
            y += strip_h + 10
        p.fillRect(x, y, col_w, zoom_h + 2 * pad - 10, QColor("#FFFFFF"))
        sx = x + 14
        for s in zoom:
            scaled = frame(s, kind).scaled(s * zoom_px, s * zoom_px,
                                           Qt.KeepAspectRatio, Qt.FastTransformation)
            p.drawImage(sx, y + 10 + (zoom_h - s * zoom_px) // 2, scaled)
            sx += s * zoom_px + 14
    p.end()
    sheet.save(str(path))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(ROOT / "etc" / "brand" / "memilmemo"))
    ap.add_argument("--squares", choices=(HOLLOW, SOLID), default=HOLLOW,
                    help="도장 안 네모: 속 빈 것(기본) 또는 속 찬 것")
    ap.add_argument("--install", action="store_true",
                    help="Resources/Image 의 Icon.png·Icon.ico 를 새 아이콘으로 바꾼다")
    args = ap.parse_args()

    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    kind = args.squares
    (out / "icon.svg").write_text(svg_large(kind), encoding="utf-8")
    for px in _SMALL:
        (out / f"icon-{px}.svg").write_text(svg_small(px, kind), encoding="utf-8")
    frame(1024, kind).save(str(out / "icon-1024.png"))
    write_ico(out / "icon.ico", kind)
    preview(out / "preview.png")
    print("만든 곳:", out)

    if args.install:
        dest = ROOT / "Resources" / "Image"
        shutil.copyfile(out / "icon-1024.png", dest / "Icon.png")
        shutil.copyfile(out / "icon.ico", dest / "Icon.ico")
        print("바꿈:", dest / "Icon.png", dest / "Icon.ico")
    return 0


if __name__ == "__main__":
    sys.exit(main())
