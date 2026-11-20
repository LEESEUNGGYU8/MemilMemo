"""메모를 쪽지처럼 접고 펴는 그림과, 접힌 쪽지·가장자리 탭의 모양.

접기는 종이를 반으로 두 번 접는 순서를 그대로 따른다. 아래 절반을 뒤로 넘겨
위 절반 밑에 넣고, 남은 위 절반을 다시 좌우로 반 접은 뒤, 그 사분면이 쪽지
크기로 줄어들며 쪽지 얼굴로 바뀐다. 펴기는 같은 그림을 거꾸로 돌린다.

여기에는 그리기와 자리 계산만 있고 창 상태는 없다. 프레임은 진행도(0 = 펼침,
1 = 접힘)의 순수 함수라, 화면 없이도 그려서 확인할 수 있다."""
from __future__ import annotations

import math
import platform
import re
from collections import OrderedDict

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QTextLayout,
    QTextOption,
    QTransform,
)

from .icons import svg_icon
from .rich_text import data_url_image, is_marker_url
from .theme import text_color_for_bg

# 떠 있는 쪽지와 가장자리 탭의 크기(논리 픽셀).
SLIP_SIZE = QSize(184, 40)
TAB_SIZE = QSize(46, 40)
TAB_MAX_WIDTH = 240
TAB_GAP = 6
# 위아래 벽의 탭은 벽을 따라 길고 납작하다. 마우스를 올리면 조금 더 나온다.
HTAB_SIZE = QSize(132, 32)
HTAB_GROW = 8
# 접힌 메모를 끌 때 그 끝이 화면 벽에서 이 거리 안에 들면 붙는다. 아이콘 격자의
# 첫 칸(벽에서 10)보다 작아야 벽 옆 칸에도 아이콘을 둘 수 있다.
SNAP_DISTANCE = 6
# 펼친 메모는 화면 끝 가까이에 두는 일이 흔하다. 커서를 벽까지 밀어야 붙는다.
EDGE_PUSH = 4
# 모니터끼리 맞닿은 끝에는 커서가 멈추지 않고 지나간다. 그 끝에 붙이려면 커서를
# 끝에서 이 거리 안에 두고 놓는다.
SEAM_DISTANCE = 14

DOCK_NONE = ""
DOCK_LEFT = "left"
DOCK_RIGHT = "right"
DOCK_TOP = "top"
DOCK_BOTTOM = "bottom"
DOCK_SIDES = (DOCK_LEFT, DOCK_RIGHT, DOCK_TOP, DOCK_BOTTOM)
_SIDE_WALLS = (DOCK_LEFT, DOCK_RIGHT)
# 모서리는 좌우 탭에 내주고, 위아래 탭은 그 안쪽에만 놓는다(서로 겹치지 않게).
_CORNER_KEEP = TAB_SIZE.width() + 6
# 화면 끝 너머 이만큼 떨어진 점에 다른 화면이 있으면 그 끝은 벽이 아니다.
_WALL_PROBE = 2

# 접힌 메모의 모양: 쪽지(제목이 적힌 납작한 종이) 또는 바탕화면 아이콘(그림 아래
# 이름). 가장자리에 붙인 메모는 모양과 무관하게 탭이다.
FOLD_SLIP = "slip"
FOLD_ICON = "icon"
FOLD_STYLES = (FOLD_SLIP, FOLD_ICON)

# 아이콘 한 칸의 크기와, 끌어다 놓을 때 맞춰지는 격자 간격.
ICON_SIZE = QSize(88, 98)
ICON_GRID = QSize(96, 106)
ICON_GRID_ORIGIN = QPoint(10, 10)
_GLYPH_W, _GLYPH_H, _GLYPH_TOP = 46.0, 52.0, 6.0

FOLD_MS = 460
UNFOLD_MS = 400
SLIDE_MS = 200
TAB_HOVER_MS = 140

_A_END = 0.36
_B_END = 0.68
# 접히는 면에 드리우는 그늘의 가장 짙은 값(0~255).
_FLAP_SHADE = 84


def system_animations_enabled() -> bool:
    """Windows '애니메이션 효과' 설정을 따른다. 끈 사용자에게는 움직임을
    보이지 않는다(접고 펴는 결과는 그대로다)."""
    if platform.system() != "Windows":
        return True
    try:
        import ctypes
        SPI_GETCLIENTAREAANIMATION = 0x1042
        value = ctypes.c_int(1)
        ok = ctypes.windll.user32.SystemParametersInfoW(
            SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(value), 0)
        return bool(value.value) if ok else True
    except Exception:
        return True


def _ease(u: float) -> float:
    """구간마다 부드럽게 시작해 부드럽게 멈춘다."""
    u = max(0.0, min(1.0, u))
    return 0.5 - 0.5 * math.cos(math.pi * u)


def _lerp(a: float, b: float, u: float) -> float:
    return a + (b - a) * u


# ── 쪽지와 탭의 얼굴 ────────────────────────────────────────────────────

def slip_path(rect: QRectF, dock: str) -> QPainterPath:
    """쪽지의 윤곽. 떠 있는 쪽지는 오른쪽 위 모서리를 접어 넣은 종이 모양이고,
    가장자리 탭은 화면 끝에 닿는 쪽이 반듯하다."""
    r = 10.0
    x0, y0, x1, y1 = rect.left(), rect.top(), rect.right(), rect.bottom()
    path = QPainterPath()
    if dock == DOCK_LEFT:
        path.moveTo(x0, y0)
        path.lineTo(x1 - r, y0)
        path.quadTo(x1, y0, x1, y0 + r)
        path.lineTo(x1, y1 - r)
        path.quadTo(x1, y1, x1 - r, y1)
        path.lineTo(x0, y1)
        path.closeSubpath()
    elif dock == DOCK_RIGHT:
        path.moveTo(x1, y0)
        path.lineTo(x0 + r, y0)
        path.quadTo(x0, y0, x0, y0 + r)
        path.lineTo(x0, y1 - r)
        path.quadTo(x0, y1, x0 + r, y1)
        path.lineTo(x1, y1)
        path.closeSubpath()
    elif dock == DOCK_TOP:
        path.moveTo(x0, y0)
        path.lineTo(x1, y0)
        path.lineTo(x1, y1 - r)
        path.quadTo(x1, y1, x1 - r, y1)
        path.lineTo(x0 + r, y1)
        path.quadTo(x0, y1, x0, y1 - r)
        path.closeSubpath()
    elif dock == DOCK_BOTTOM:
        path.moveTo(x0, y1)
        path.lineTo(x0, y0 + r)
        path.quadTo(x0, y0, x0 + r, y0)
        path.lineTo(x1 - r, y0)
        path.quadTo(x1, y0, x1, y0 + r)
        path.lineTo(x1, y1)
        path.closeSubpath()
    else:
        ear = _ear_size(rect)
        path.moveTo(x0 + r, y0)
        path.lineTo(x1 - ear, y0)
        path.lineTo(x1, y0 + ear)
        path.lineTo(x1, y1 - r)
        path.quadTo(x1, y1, x1 - r, y1)
        path.lineTo(x0 + r, y1)
        path.quadTo(x0, y1, x0, y1 - r)
        path.lineTo(x0, y0 + r)
        path.quadTo(x0, y0, x0 + r, y0)
        path.closeSubpath()
    return path


def _ear_size(rect: QRectF) -> float:
    return min(13.0, rect.height() * 0.34)


def paint_slip(p: QPainter, rect: QRectF, color: str, title: str, *,
               dock: str = DOCK_NONE, private: bool = False,
               hover: bool = False, font: QFont | None = None) -> None:
    """접힌 쪽지(또는 가장자리 탭)를 ``rect`` 에 그린다."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    base = QColor(color)
    fg = text_color_for_bg(color)
    dark = fg == "#FFFFFF"
    body = QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5)
    path = slip_path(body, dock)

    p.setPen(Qt.NoPen)
    p.setBrush(base)
    p.drawPath(path)
    if hover:
        p.setBrush(QColor(255, 255, 255, 34) if dark else QColor(255, 255, 255, 90))
        p.drawPath(path)

    edge = QColor(255, 255, 255, 60) if dark else QColor(0, 0, 0, 46)
    if dock in DOCK_SIDES:
        # 화면 끝에 물린 쪽: 살짝 어두운 띠로 '끼워 둔' 느낌을 준다.
        strip = QRectF(body)
        if dock in _SIDE_WALLS:
            strip.setWidth(4.0)
            if dock == DOCK_RIGHT:
                strip.moveRight(body.right())
        else:
            strip.setHeight(4.0)
            if dock == DOCK_BOTTOM:
                strip.moveBottom(body.bottom())
        p.save()
        p.setClipPath(path)
        p.fillRect(strip, QColor(255, 255, 255, 50) if dark
                   else QColor(0, 0, 0, 40))
        p.restore()
    else:
        # 접어 넣은 모서리: 종이 뒷면이 삼각형으로 보인다.
        ear = _ear_size(body)
        tri = QPolygonF([
            QPointF(body.right() - ear, body.top()),
            QPointF(body.right(), body.top() + ear),
            QPointF(body.right() - ear, body.top() + ear),
        ])
        back = QColor(base)
        h, s, v, a = back.getHsv()
        back.setHsv(h, max(0, s - 18), max(0, v - 34) if not dark
                    else min(255, v + 46), a)
        p.setBrush(back)
        p.setPen(Qt.NoPen)
        p.drawPolygon(tri)
        p.setPen(QPen(edge, 1.0))
        p.drawLine(tri[0], tri[2])
        p.drawLine(tri[2], tri[1])

    p.setPen(QPen(edge, 1.0))
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)

    f = QFont(font) if font is not None else QFont(p.font())
    f.setPixelSize(13)
    f.setBold(True)
    p.setFont(f)
    fm = QFontMetricsF(f)
    pad_l = 12.0 + (4.0 if dock == DOCK_LEFT else 0.0)
    pad_r = 10.0 + (4.0 if dock == DOCK_RIGHT else 0.0)
    if dock == DOCK_NONE:
        pad_r += _ear_size(body) * 0.6
    # 위아래 탭은 벽에 물린 띠를 뺀 자리의 가운데에 제목을 둔다.
    pad_t = 4.0 if dock == DOCK_TOP else 0.0
    pad_b = 4.0 if dock == DOCK_BOTTOM else 0.0
    text_rect = QRectF(body.left() + pad_l, body.top() + pad_t,
                       body.width() - pad_l - pad_r,
                       body.height() - pad_t - pad_b)
    if private and text_rect.width() > 60:
        icon = svg_icon("lock", color=fg).pixmap(13, 13)
        p.drawPixmap(QPointF(text_rect.left(),
                             text_rect.center().y() - 6.5), icon)
        text_rect.setLeft(text_rect.left() + 18)
    p.setPen(QColor(fg))
    # 탭은 마우스를 올리면 폭이 늘어난다. 그 사이에 줄임표가 생겼다 사라지면
    # 글자가 튀어 보이므로, 가장 넓어졌을 때만 줄임표를 쓰고 그 전에는 들어가는
    # 글자만 보인다.
    clip_only = (dock in _SIDE_WALLS and rect.width() < TAB_MAX_WIDTH - 0.5) \
        or text_rect.width() < fm.horizontalAdvance("가…")
    if clip_only:
        p.save()
        p.setClipRect(text_rect)
        p.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft | Qt.TextDontClip,
                   title)
        p.restore()
    else:
        text = fm.elidedText(title, Qt.ElideRight, text_rect.width())
        p.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, text)
    p.restore()


def slip_pixmap(size: QSize, dpr: float, color: str, title: str, *,
                dock: str = DOCK_NONE, private: bool = False,
                font: QFont | None = None) -> QPixmap:
    pix = QPixmap(int(math.ceil(size.width() * dpr)),
                  int(math.ceil(size.height() * dpr)))
    pix.setDevicePixelRatio(dpr)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    paint_slip(p, QRectF(0, 0, size.width(), size.height()), color, title,
               dock=dock, private=private, font=font)
    p.end()
    return pix


def tab_width_for(title: str, font: QFont | None = None,
                  private: bool = False) -> int:
    """탭 위에 마우스를 올렸을 때 제목이 다 보이도록 늘릴 폭."""
    f = QFont(font) if font is not None else QFont()
    f.setPixelSize(13)
    f.setBold(True)
    text_w = QFontMetricsF(f).horizontalAdvance(title)
    want = int(math.ceil(text_w)) + 16 + 14 + (18 if private else 0)
    return max(TAB_SIZE.width(), min(TAB_MAX_WIDTH, want))


# ── 바탕화면 아이콘 모양 ────────────────────────────────────────────────

def icon_glyph_rect(cell: QRectF) -> QRectF:
    """아이콘 칸 안에서 그림(종이·폴더)이 놓이는 자리."""
    return QRectF(cell.left() + (cell.width() - _GLYPH_W) / 2.0,
                  cell.top() + _GLYPH_TOP, _GLYPH_W, _GLYPH_H)


def icon_paper_path(rect: QRectF) -> QPainterPath:
    """오른쪽 위 모서리를 접어 넣은 작은 종이의 윤곽."""
    r = min(5.0, rect.width() * 0.12, rect.height() * 0.12)
    ear = min(rect.width() * 0.28, rect.height() * 0.28)
    x0, y0, x1, y1 = rect.left(), rect.top(), rect.right(), rect.bottom()
    path = QPainterPath()
    path.moveTo(x0 + r, y0)
    path.lineTo(x1 - ear, y0)
    path.lineTo(x1, y0 + ear)
    path.lineTo(x1, y1 - r)
    path.quadTo(x1, y1, x1 - r, y1)
    path.lineTo(x0 + r, y1)
    path.quadTo(x0, y1, x0, y1 - r)
    path.lineTo(x0, y0 + r)
    path.quadTo(x0, y0, x0 + r, y0)
    path.closeSubpath()
    return path


def _shade(color: QColor, *, lighter: bool, amount: int) -> QColor:
    out = QColor(color)
    h, s, v, a = out.getHsv()
    if lighter:
        out.setHsv(h, max(0, s - 20), min(255, v + amount), a)
    else:
        out.setHsv(h, min(255, s + 18), max(0, v - amount), a)
    return out


_IMG_SRC = re.compile(r'<img[^>]*src=["\']([^"\']+)["\']', re.IGNORECASE)
# 종이 그림 안에 넣는 첫 이미지의 축소판(논리 픽셀). 두 배로 만들어 둔다.
_THUMB_W, _THUMB_H, _THUMB_SCALE = 36, 32, 2
_THUMBS: "OrderedDict[tuple, QPixmap]" = OrderedDict()
_THUMBS_MAX = 48


def first_image_url(html: str) -> str:
    """본문의 첫 이미지(글머리와 체크 상자 그림은 뺀다)."""
    if not html or "<img" not in html.lower():
        return ""
    for m in _IMG_SRC.finditer(html):
        if not is_marker_url(m.group(1)):
            return m.group(1)
    return ""


def note_thumbnail(html: str) -> QPixmap | None:
    """본문의 첫 이미지를 아이콘 속 칸에 꽉 차게(가운데를 잘라) 줄인 그림.

    이미지가 없으면 None. 같은 이미지를 거듭 풀지 않도록 최근 것을 기억한다."""
    url = first_image_url(html)
    if not url:
        return None
    key = (len(url), url[:96], url[-96:])
    hit = _THUMBS.get(key)
    if hit is not None:
        _THUMBS.move_to_end(key)
        return hit
    img = data_url_image(url)
    if img.isNull():
        return None
    tw, th = _THUMB_W * _THUMB_SCALE, _THUMB_H * _THUMB_SCALE
    scaled = img.scaled(tw, th, Qt.KeepAspectRatioByExpanding,
                        Qt.SmoothTransformation)
    pix = QPixmap.fromImage(scaled.copy(max(0, (scaled.width() - tw) // 2),
                                        max(0, (scaled.height() - th) // 2),
                                        tw, th))
    pix.setDevicePixelRatio(_THUMB_SCALE)
    _THUMBS[key] = pix
    while len(_THUMBS) > _THUMBS_MAX:
        _THUMBS.popitem(last=False)
    return pix


def paint_note_glyph(p: QPainter, rect: QRectF, color: str, *,
                     private: bool = False,
                     thumb: QPixmap | None = None) -> None:
    """메모 색의 작은 종이. 위쪽 띠와 글줄 몇 가닥으로 메모임을 알린다.

    ``thumb`` 을 주면 글줄 대신 그 그림을 종이 안에 작게 넣는다(이미지가 든 메모)."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    base = QColor(color)
    dark = text_color_for_bg(color) == "#FFFFFF"
    body = QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5)
    path = icon_paper_path(body)
    p.setPen(Qt.NoPen)
    p.setBrush(base)
    p.drawPath(path)
    p.save()
    p.setClipPath(path)
    band = QRectF(body.left(), body.top(), body.width(), body.height() * 0.2)
    p.fillRect(band, _shade(base, lighter=True, amount=20))
    ink = QColor(255, 255, 255, 150) if dark else QColor(0, 0, 0, 92)
    if thumb is not None and not thumb.isNull():
        # 종이 안쪽에 사진을 붙인 모양. 종이가 줄어드는 접기 그림에서도 비율을 지킨다.
        inset = body.width() * 0.11
        top = band.bottom() + body.height() * 0.07
        box = QRectF(body.left() + inset, top, body.width() - 2 * inset,
                     body.bottom() - inset - top)
        if box.width() > 6 and box.height() > 6:
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            clip = QPainterPath()
            clip.addRoundedRect(box, 2.0, 2.0)
            p.save()
            p.setClipPath(clip, Qt.IntersectClip)
            p.drawPixmap(box, thumb, QRectF(thumb.rect()))
            p.restore()
            p.setPen(QPen(QColor(255, 255, 255, 120) if dark
                          else QColor(0, 0, 0, 70), 1.0))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(box, 2.0, 2.0)
    else:
        pen = QPen(ink, max(1.2, body.height() * 0.045))
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        left = body.left() + body.width() * 0.2
        for frac, width in ((0.42, 0.6), (0.58, 0.6), (0.74, 0.38)):
            y = body.top() + body.height() * frac
            p.drawLine(QPointF(left, y),
                       QPointF(left + body.width() * width, y))
    p.restore()
    ear = min(body.width() * 0.28, body.height() * 0.28)
    tri = QPolygonF([
        QPointF(body.right() - ear, body.top()),
        QPointF(body.right(), body.top() + ear),
        QPointF(body.right() - ear, body.top() + ear),
    ])
    edge = QColor(255, 255, 255, 110) if dark else QColor(0, 0, 0, 70)
    p.setPen(Qt.NoPen)
    p.setBrush(_shade(base, lighter=dark, amount=46 if dark else 34))
    p.drawPolygon(tri)
    p.setPen(QPen(edge, 1.0))
    p.setBrush(Qt.NoBrush)
    p.drawLine(tri[0], tri[2])
    p.drawLine(tri[2], tri[1])
    p.drawPath(path)
    if private and body.width() >= 30:
        side = 13.0
        spot = QPointF(body.left() + 4.0, body.bottom() - side - 4.0)
        if thumb is not None and not thumb.isNull():
            # 사진 위에서도 보이도록 종이 색 바탕을 깐다.
            p.setPen(Qt.NoPen)
            p.setBrush(base)
            p.drawRoundedRect(QRectF(spot.x() - 2.0, spot.y() - 2.0,
                                     side + 4.0, side + 4.0), 4.0, 4.0)
        icon = svg_icon("lock", color="#FFFFFF" if dark else "#1F2937")
        p.drawPixmap(spot, icon.pixmap(int(side), int(side)))
    p.restore()


def paint_folder_glyph(p: QPainter, rect: QRectF, color: str, *,
                       opened: bool = False) -> None:
    """폴더 색의 폴더 그림. 열려 있으면 앞 덮개가 젖혀진 모양이다."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    base = QColor(color)
    line = _shade(base, lighter=False, amount=96)
    w, h = rect.width(), rect.height()
    body = QRectF(rect.left() + 0.5, rect.top() + h * 0.2,
                  w - 1.0, h * 0.68)
    r = 4.0
    back = QPainterPath()
    tab_w = body.width() * 0.42
    back.moveTo(body.left() + r, body.top() - h * 0.1)
    back.lineTo(body.left() + tab_w - 3.0, body.top() - h * 0.1)
    back.lineTo(body.left() + tab_w + 4.0, body.top())
    back.lineTo(body.right() - r, body.top())
    back.quadTo(body.right(), body.top(), body.right(), body.top() + r)
    back.lineTo(body.right(), body.bottom() - r)
    back.quadTo(body.right(), body.bottom(), body.right() - r, body.bottom())
    back.lineTo(body.left() + r, body.bottom())
    back.quadTo(body.left(), body.bottom(), body.left(), body.bottom() - r)
    back.lineTo(body.left(), body.top() - h * 0.1 + r)
    back.quadTo(body.left(), body.top() - h * 0.1,
                body.left() + r, body.top() - h * 0.1)
    back.closeSubpath()
    p.setPen(QPen(line, 1.1))
    p.setBrush(_shade(base, lighter=False, amount=26))
    p.drawPath(back)
    front = QPainterPath()
    if opened:
        # 앞 덮개를 앞으로 젖힌 모양: 위쪽 변이 오른쪽으로 기울어 벌어진다.
        top = body.top() + body.height() * 0.34
        lean = body.width() * 0.13
        front.moveTo(body.left() + lean, top)
        front.lineTo(body.right() + lean * 0.5 - 0.5, top)
        front.lineTo(body.right() - r, body.bottom())
        front.lineTo(body.left() + r, body.bottom())
        front.quadTo(body.left(), body.bottom(),
                     body.left() + 1.5, body.bottom() - r)
        front.closeSubpath()
    else:
        top = body.top() + body.height() * 0.2
        front.addRoundedRect(QRectF(body.left(), top, body.width(),
                                    body.bottom() - top), r, r)
    p.setBrush(base)
    p.drawPath(front)
    p.restore()


def _wrap_label(text: str, font: QFont, width: float,
                max_lines: int = 2) -> list[str]:
    """이름을 ``width`` 안에서 최대 ``max_lines`` 줄로 접는다. 넘치면 마지막 줄
    끝을 줄인다."""
    text = " ".join((text or "").split())
    if not text:
        return []
    layout = QTextLayout(text, font)
    option = QTextOption()
    option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
    layout.setTextOption(option)
    lines: list[str] = []
    layout.beginLayout()
    while True:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(width)
        lines.append(text[line.textStart():line.textStart() + line.textLength()])
    layout.endLayout()
    if len(lines) > max_lines:
        rest = "".join(lines[max_lines - 1:])
        lines = lines[:max_lines - 1] + [
            QFontMetricsF(font).elidedText(rest, Qt.ElideRight, width)]
    return [ln.strip() for ln in lines]


def paint_icon_label(p: QPainter, cell: QRectF, text: str,
                     font: QFont | None = None) -> None:
    """아이콘 아래의 이름. 어떤 바탕 화면 위에서도 읽히도록 흰 글자에 어두운
    테두리 그림자를 두른다(Windows 바탕화면 아이콘 이름과 같은 방식)."""
    f = QFont(font) if font is not None else QFont(p.font())
    f.setPixelSize(12)
    f.setBold(False)
    f.setWeight(QFont.Medium)
    fm = QFontMetricsF(f)
    width = cell.width() - 6.0
    lines = _wrap_label(text, f, width)
    if not lines:
        return
    p.save()
    p.setFont(f)
    top = cell.top() + _GLYPH_TOP + _GLYPH_H + 5.0
    for i, line in enumerate(lines):
        rect = QRectF(cell.left() + 3.0, top + i * fm.lineSpacing(),
                      width, fm.lineSpacing())
        p.setPen(QColor(0, 0, 0, 120))
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (0, 2)):
            p.drawText(rect.translated(dx, dy),
                       Qt.AlignHCenter | Qt.AlignVCenter, line)
        p.setPen(QColor("#FFFFFF"))
        p.drawText(rect, Qt.AlignHCenter | Qt.AlignVCenter, line)
    p.restore()


def _paint_icon_hit_area(p: QPainter, cell: QRectF) -> None:
    # 반투명 창은 완전히 투명한 화소에서 마우스가 뒤 창으로 빠진다. 칸 전체를
    # 눈에 안 보이는 옅은 색으로 깔아 아이콘 둘레에서도 잡히게 한다.
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255, 1))
    p.drawRoundedRect(cell, 7, 7)


def _paint_glyph(p: QPainter, rect: QRectF, hover: bool, draw) -> None:
    """``draw(painter)`` 로 그림을 그린다. 마우스가 올라와 있으면 그림 모양대로만
    밝힌다(둘레에 판을 깔지 않는다)."""
    if not hover:
        draw(p)
        return
    area = rect.adjusted(-2.0, -2.0, 2.0, 2.0)
    device = p.device()
    dpr = device.devicePixelRatioF() if device is not None else 1.0
    layer = QPixmap(int(math.ceil(area.width() * dpr)),
                    int(math.ceil(area.height() * dpr)))
    layer.setDevicePixelRatio(dpr)
    layer.fill(Qt.transparent)
    q = QPainter(layer)
    q.setRenderHint(QPainter.Antialiasing)
    q.translate(-area.left(), -area.top())
    draw(q)
    q.setCompositionMode(QPainter.CompositionMode_SourceAtop)
    q.fillRect(area, QColor(255, 255, 255, 62))
    q.end()
    p.drawPixmap(area.topLeft(), layer)


def paint_icon(p: QPainter, cell: QRectF, color: str, title: str, *,
               private: bool = False, hover: bool = False,
               font: QFont | None = None,
               thumb: QPixmap | None = None) -> None:
    """접힌 메모를 바탕화면 아이콘 모양으로 그린다."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    _paint_icon_hit_area(p, cell)
    glyph = icon_glyph_rect(cell)
    _paint_glyph(p, glyph, hover, lambda q: paint_note_glyph(
        q, glyph, color, private=private, thumb=thumb))
    paint_icon_label(p, cell, title, font)
    p.restore()


def paint_folder_icon(p: QPainter, cell: QRectF, color: str, name: str, *,
                      opened: bool = False, count: int = 0,
                      hover: bool = False, font: QFont | None = None) -> None:
    """메모 폴더를 바탕화면 아이콘 모양으로 그린다. 오른쪽 아래 숫자는 그
    폴더에 든 메모 수다."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    _paint_icon_hit_area(p, cell)
    glyph = icon_glyph_rect(cell)
    glyph = QRectF(glyph.left() - 3.0, glyph.top() + 2.0,
                   glyph.width() + 6.0, glyph.height() - 6.0)
    _paint_glyph(p, glyph, hover, lambda q: paint_folder_glyph(
        q, glyph, color, opened=opened))
    if count > 0:
        f = QFont(font) if font is not None else QFont(p.font())
        f.setPixelSize(10)
        f.setBold(True)
        label = str(count) if count < 100 else "99+"
        fm = QFontMetricsF(f)
        w = max(17.0, fm.horizontalAdvance(label) + 9.0)
        badge = QRectF(glyph.right() - w + 5.0, glyph.bottom() - 13.0, w, 17.0)
        p.setPen(QPen(QColor("#FFFFFF"), 1.4))
        p.setBrush(QColor("#374151"))
        p.drawRoundedRect(badge, 8.5, 8.5)
        p.setFont(f)
        p.setPen(QColor("#FFFFFF"))
        p.drawText(badge, Qt.AlignCenter, label)
    paint_icon_label(p, cell, name, font)
    p.restore()


def snap_icon_pos(pos: QPoint, screen: QRect,
                  taken: list[QPoint] | None = None) -> QPoint:
    """아이콘을 놓은 자리에서 가장 가까운 빈 격자 칸의 왼쪽 위.

    ``taken`` 은 이미 다른 아이콘이 놓인 자리다. 같은 칸이면 가까운 빈 칸으로
    비켜 놓는다(바탕화면 아이콘이 겹쳐 쌓이지 않게)."""
    gw, gh = ICON_GRID.width(), ICON_GRID.height()
    ox = screen.left() + ICON_GRID_ORIGIN.x()
    oy = screen.top() + ICON_GRID_ORIGIN.y()
    cols = max(1, (screen.width() - ICON_GRID_ORIGIN.x()) // gw)
    rows = max(1, (screen.height() - ICON_GRID_ORIGIN.y()) // gh)

    def cell_of(pt: QPoint) -> tuple[int, int]:
        col = int(round((pt.x() - ox) / gw))
        row = int(round((pt.y() - oy) / gh))
        return (max(0, min(cols - 1, col)), max(0, min(rows - 1, row)))

    used = {cell_of(pt) for pt in (taken or [])}
    want = cell_of(pos)
    best = want
    if want in used:
        found = None
        for radius in range(1, max(cols, rows) + 1):
            ring = []
            for dc in range(-radius, radius + 1):
                for dr in range(-radius, radius + 1):
                    if max(abs(dc), abs(dr)) != radius:
                        continue
                    c, r = want[0] + dc, want[1] + dr
                    if 0 <= c < cols and 0 <= r < rows and (c, r) not in used:
                        ring.append((dc * dc + dr * dr, r, c))
            if ring:
                _dist, r, c = min(ring)
                found = (c, r)
                break
        if found is not None:
            best = found
    return QPoint(ox + best[0] * gw, oy + best[1] * gh)


# ── 접는 그림 ───────────────────────────────────────────────────────────

class FoldFrames:
    """펼친 메모의 그림을 받아 쪽지로 접히는 과정의 프레임을 그린다.

    ``anchor`` 는 쪽지가 남는 쪽이다. 왼쪽이면 메모의 왼쪽 위로, 오른쪽이면
    오른쪽 위로 접혀 들어간다(오른쪽 가장자리에 붙인 메모는 오른쪽으로 접는다).
    ``slip_size`` 와 ``dock`` 은 다 접혔을 때의 쪽지 크기와 모양이다."""

    def __init__(self, open_pix: QPixmap, slip_size: QSize, *,
                 color: str, title: str, anchor: str = DOCK_LEFT,
                 dock: str = DOCK_NONE, private: bool = False,
                 font: QFont | None = None, style: str = FOLD_SLIP,
                 thumb: QPixmap | None = None):
        self.thumb = thumb
        self.dpr = open_pix.devicePixelRatio() or 1.0
        self.full = open_pix
        self.W = int(round(open_pix.width() / self.dpr))
        self.H = int(round(open_pix.height() / self.dpr))
        self.anchor = DOCK_RIGHT if anchor == DOCK_RIGHT else DOCK_LEFT
        self.color = color
        self.title = title
        self.dock = dock
        self.private = private
        self.font = font
        # 아이콘 모양은 떠 있는 메모에만 쓴다(붙인 메모는 탭이다).
        self.style = FOLD_ICON if (style == FOLD_ICON and not dock) else FOLD_SLIP
        self.sw = float(slip_size.width())
        self.sh = float(slip_size.height())

        self.hh = self.H // 2          # 위 절반 높이
        self.hw = self.W // 2          # 왼쪽 절반 폭
        self.top = self._piece(0, 0, self.W, self.hh)
        self.bottom = self._piece(0, self.hh, self.W, self.H - self.hh)
        if self.anchor == DOCK_LEFT:
            self.keep_x = 0
            self.keep_w = self.hw
            self.keep = self._piece(0, 0, self.hw, self.hh)
            self.flap = self._piece(self.hw, 0, self.W - self.hw, self.hh)
        else:
            self.keep_x = self.hw
            self.keep_w = self.W - self.hw
            self.keep = self._piece(self.hw, 0, self.W - self.hw, self.hh)
            self.flap = self._piece(0, 0, self.hw, self.hh)

    def _piece(self, x: int, y: int, w: int, h: int) -> QPixmap:
        d = self.dpr
        piece = self.full.copy(QRect(int(round(x * d)), int(round(y * d)),
                                     int(round(w * d)), int(round(h * d))))
        piece.setDevicePixelRatio(d)
        return piece

    def slip_rect(self) -> QRectF:
        """다 접힌 쪽지가 놓이는 자리(펼친 메모의 왼쪽 위가 원점)."""
        x = 0.0 if self.anchor == DOCK_LEFT else self.W - self.sw
        return QRectF(x, 0.0, self.sw, self.sh)

    def _paper_target(self) -> QRectF:
        """접힌 종이가 마지막에 놓이는 자리. 쪽지는 쪽지 전체, 아이콘은 그림."""
        cell = self.slip_rect()
        return icon_glyph_rect(cell) if self.style == FOLD_ICON else cell

    def _paper_path(self, rect: QRectF) -> QPainterPath:
        body = rect.adjusted(0.5, 0.5, -0.5, -0.5)
        if self.style == FOLD_ICON:
            return icon_paper_path(body)
        return slip_path(body, self.dock)

    def _paint_face(self, p: QPainter, paper: QRectF) -> None:
        """다 접힌 모습. ``paper`` 는 줄어드는 중인 종이의 자리다."""
        if self.style == FOLD_ICON:
            paint_note_glyph(p, paper, self.color, private=self.private,
                             thumb=self.thumb)
            paint_icon_label(p, self.slip_rect(), self.title, self.font)
        else:
            paint_slip(p, paper, self.color, self.title, dock=self.dock,
                       private=self.private, font=self.font)

    def _shaded(self, piece: QPixmap, alpha: int) -> QPixmap:
        """종이 모양(투명한 둥근 모서리)은 지키면서 면만 어둡게 한다."""
        if alpha <= 0:
            return piece
        out = QPixmap(piece.size())
        out.setDevicePixelRatio(self.dpr)
        out.fill(Qt.transparent)
        q = QPainter(out)
        q.drawPixmap(0, 0, piece)
        q.setCompositionMode(QPainter.CompositionMode_SourceAtop)
        q.fillRect(QRectF(0, 0, piece.width() / self.dpr,
                          piece.height() / self.dpr),
                   QColor(0, 0, 0, min(255, alpha)))
        q.end()
        return out

    def _draw_quad(self, p: QPainter, piece: QPixmap, dst: QPolygonF) -> None:
        w = piece.width() / self.dpr
        h = piece.height() / self.dpr
        src = QPolygonF([QPointF(0, 0), QPointF(w, 0),
                         QPointF(w, h), QPointF(0, h)])
        t = QTransform()
        if not QTransform.quadToQuad(src, dst, t):
            return
        p.save()
        p.setTransform(t, True)
        p.drawPixmap(QPointF(0, 0), piece)
        p.restore()

    def paint(self, p: QPainter, progress: float) -> None:
        t = max(0.0, min(1.0, float(progress)))
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        if t <= 0.0:
            p.drawPixmap(QPointF(0, 0), self.full)
        elif t >= 1.0:
            # 끝 프레임은 접힌 창이 그리는 모습과 한 화소도 다르지 않게 한다.
            if self.style == FOLD_ICON:
                paint_icon(p, self.slip_rect(), self.color, self.title,
                           private=self.private, font=self.font,
                           thumb=self.thumb)
            else:
                paint_slip(p, self.slip_rect(), self.color, self.title,
                           dock=self.dock, private=self.private,
                           font=self.font)
        elif t < _A_END:
            self._paint_fold_up(p, _ease(t / _A_END))
        elif t < _B_END:
            self._paint_fold_side(p, _ease((t - _A_END) / (_B_END - _A_END)))
        else:
            self._paint_settle(p, _ease((t - _B_END) / (1.0 - _B_END)))
        p.restore()

    # 1단계: 아래 절반을 뒤로 넘긴다. 면이 옆에서 보이는 순간(90도)부터는
    # 위 절반에 가려지므로 거기까지만 돌린다.
    def _paint_fold_up(self, p: QPainter, u: float) -> None:
        phi = u * math.pi / 2.0
        s, c = math.sin(phi), math.cos(phi)
        p.drawPixmap(QPointF(0, 0), self.top)
        vis = (self.H - self.hh) * c
        if vis >= 0.6:
            inset = min(self.W * 0.06, 14.0) * s
            y0 = float(self.hh)
            dst = QPolygonF([
                QPointF(0, y0), QPointF(self.W, y0),
                QPointF(self.W - inset, y0 + vis),
                QPointF(inset, y0 + vis),
            ])
            self._draw_quad(
                p, self._shaded(self.bottom, int(_FLAP_SHADE * s)), dst)
        # 접힌 금 위의 그늘: 접는 도중에 짙어졌다가 다 넘어가면 사라진다.
        k = math.sin(2.0 * phi)
        band = min(22.0, self.hh * 0.5)
        grad = QLinearGradient(0, self.hh - band, 0, self.hh)
        grad.setColorAt(0.0, QColor(0, 0, 0, 0))
        grad.setColorAt(1.0, QColor(0, 0, 0, int(46 * k)))
        p.save()
        p.setCompositionMode(QPainter.CompositionMode_SourceAtop)
        p.fillRect(QRectF(0, self.hh - band, self.W, band), grad)
        p.restore()

    # 2단계: 남은 위 절반을 좌우로 반 접는다.
    def _paint_fold_side(self, p: QPainter, u: float) -> None:
        phi = u * math.pi / 2.0
        s, c = math.sin(phi), math.cos(phi)
        p.drawPixmap(QPointF(self.keep_x, 0), self.keep)
        vis = (self.W - self.keep_w) * c
        if vis >= 0.6:
            inset = min(self.hh * 0.08, 10.0) * s
            shaded = self._shaded(self.flap, int(_FLAP_SHADE * s))
            if self.anchor == DOCK_LEFT:
                x0 = float(self.keep_w)
                dst = QPolygonF([
                    QPointF(x0, 0), QPointF(x0 + vis, inset),
                    QPointF(x0 + vis, self.hh - inset),
                    QPointF(x0, self.hh),
                ])
            else:
                x0 = float(self.keep_x)
                dst = QPolygonF([
                    QPointF(x0 - vis, inset), QPointF(x0, 0),
                    QPointF(x0, self.hh),
                    QPointF(x0 - vis, self.hh - inset),
                ])
            self._draw_quad(p, shaded, dst)
        k = math.sin(2.0 * phi)
        band = min(22.0, self.keep_w * 0.5)
        if self.anchor == DOCK_LEFT:
            x1 = float(self.keep_w)
            grad = QLinearGradient(x1 - band, 0, x1, 0)
            area = QRectF(x1 - band, 0, band, self.hh)
        else:
            x1 = float(self.keep_x)
            grad = QLinearGradient(x1 + band, 0, x1, 0)
            area = QRectF(x1, 0, band, self.hh)
        grad.setColorAt(0.0, QColor(0, 0, 0, 0))
        grad.setColorAt(1.0, QColor(0, 0, 0, int(46 * k)))
        p.save()
        p.setCompositionMode(QPainter.CompositionMode_SourceAtop)
        p.fillRect(area, grad)
        p.restore()

    # 3단계: 남은 사분면이 쪽지 크기로 줄어든다. 본문은 줄이지 않고 종이
    # 윤곽으로 잘라 내며 지우고, 제목은 제 크기로 나타난다(글자를 늘이거나
    # 겹쳐 그리면 번져 보인다).
    def _paint_settle(self, p: QPainter, u: float) -> None:
        q = QRectF(self.keep_x, 0, self.keep_w, self.hh)
        s = self._paper_target()
        r = QRectF(_lerp(q.x(), s.x(), u), _lerp(q.y(), s.y(), u),
                   _lerp(q.width(), s.width(), u),
                   _lerp(q.height(), s.height(), u))
        a_keep = max(0.0, 1.0 - u / 0.55)
        a_slip = min(1.0, max(0.0, (u - 0.30) / 0.60))
        path = self._paper_path(r)
        p.save()
        p.setClipPath(path)
        p.fillPath(path, QColor(self.color))
        if a_keep > 0.0:
            p.setOpacity(a_keep)
            if self.anchor == DOCK_LEFT:
                origin = QPointF(r.left(), r.top())
            else:
                origin = QPointF(r.right() - self.keep_w, r.top())
            p.drawPixmap(origin, self.keep)
        p.restore()
        if a_slip > 0.0:
            p.setOpacity(a_slip)
            self._paint_face(p, r)
            p.setOpacity(1.0)


# ── 자리 계산 ───────────────────────────────────────────────────────────

# 탭의 자리는 벽을 따라 잰 값 하나로 적는다. 좌우 벽이면 위에서부터(y), 위아래
# 벽이면 왼쪽에서부터(x)다. 아래 함수들의 ``y``·``height`` 도 그 뜻으로 쓴다.

def tab_size(side: str) -> QSize:
    """다 접혀 벽에 붙은 탭의 크기."""
    return QSize(TAB_SIZE) if side in _SIDE_WALLS else QSize(HTAB_SIZE)


def tab_length(side: str) -> int:
    """탭이 벽을 따라 차지하는 길이."""
    return TAB_SIZE.height() if side in _SIDE_WALLS else HTAB_SIZE.width()


def tab_rect(side: str, pos: int, screen: QRect, *, expanded: bool = False,
             wide: int = 0) -> QRect:
    """``screen`` 의 ``side`` 벽, 벽을 따라 ``pos`` 자리에 붙은 탭.

    마우스를 올리면(``expanded``) 좌우 탭은 제목이 보이도록 ``wide`` 까지 길어지고,
    위아래 탭은 조금 더 나온다."""
    if side in _SIDE_WALLS:
        w = max(TAB_SIZE.width(), int(wide)) if expanded else TAB_SIZE.width()
        x = screen.left() if side == DOCK_LEFT else screen.right() - w + 1
        return QRect(x, int(pos), w, TAB_SIZE.height())
    h = HTAB_SIZE.height() + (HTAB_GROW if expanded else 0)
    y = screen.top() if side == DOCK_TOP else screen.bottom() - h + 1
    return QRect(int(pos), y, HTAB_SIZE.width(), h)


def _dock_range(side: str, length: int, screen: QRect) -> tuple[int, int]:
    if side in _SIDE_WALLS:
        lo = screen.top() + 8
        hi = screen.bottom() - length - 8
    else:
        lo = screen.left() + _CORNER_KEEP
        hi = screen.right() - length - _CORNER_KEEP
    return lo, max(lo, hi)


def clamp_dock_y(y: int, height: int, screen: QRect,
                 side: str = DOCK_LEFT) -> int:
    lo, hi = _dock_range(side, height, screen)
    return max(lo, min(int(y), hi))


def free_dock_slot(y: int, height: int, screen: QRect,
                   occupied: list[tuple[int, int]],
                   side: str = DOCK_LEFT) -> int:
    """같은 벽의 다른 탭과 겹치지 않는, ``y`` 에 가장 가까운 자리.

    ``occupied`` 는 이미 놓인 탭들의 ``(시작, 끝)`` 이다. 뒤로 밀어 보고 화면을
    벗어나면 앞으로 당겨 찾는다."""
    lo, hi = _dock_range(side, height, screen)
    y = max(lo, min(int(y), hi))
    spans = sorted(occupied)

    def clash(start: int) -> tuple[int, int] | None:
        for a, b in spans:
            if start < b + TAB_GAP and a < start + height + TAB_GAP:
                return a, b
        return None

    down = y
    while down <= hi:
        hit = clash(down)
        if hit is None:
            return down
        down = hit[1] + TAB_GAP
    up = y
    while up >= lo:
        hit = clash(up)
        if hit is None:
            return up
        up = hit[0] - TAB_GAP - height
    return y


# ── 화면의 바깥벽 ───────────────────────────────────────────────────────
# ``screens`` 는 화면마다 ``(전체 영역, 작업 표시줄을 뺀 영역)`` 을 적은 목록이다.
# 창에 기대지 않는 계산이라 모니터가 여럿인 배치도 숫자만으로 확인할 수 있다.

def screen_index_at(point: QPoint,
                    screens: list[tuple[QRect, QRect]]) -> int:
    """``point`` 가 놓인 화면의 번호. 어느 화면에도 없으면 가장 가까운 화면."""
    best, best_d = -1, -1
    for i, (geo, _avail) in enumerate(screens):
        if geo.contains(point):
            return i
        dx = max(geo.left() - point.x(), 0, point.x() - geo.right())
        dy = max(geo.top() - point.y(), 0, point.y() - geo.bottom())
        d = dx * dx + dy * dy
        if best < 0 or d < best_d:
            best, best_d = i, d
    return best


def is_outer_wall(side: str, index: int, screens: list[tuple[QRect, QRect]],
                  along: int) -> bool:
    """``index`` 화면의 ``side`` 끝이 바깥벽인가. 그 너머에 다른 화면이 이어져
    있으면 바깥벽이 아니라 모니터 사이의 경계다. 크기가 다른 모니터는 끝의
    일부만 맞닿으므로, 벽을 따라 잰 자리 ``along`` 에서 본다."""
    geo = screens[index][0]
    if side in _SIDE_WALLS:
        along = max(geo.top(), min(int(along), geo.bottom()))
        x = (geo.left() - _WALL_PROBE if side == DOCK_LEFT
             else geo.right() + _WALL_PROBE)
        probe = QPoint(x, along)
    else:
        along = max(geo.left(), min(int(along), geo.right()))
        y = (geo.top() - _WALL_PROBE if side == DOCK_TOP
             else geo.bottom() + _WALL_PROBE)
        probe = QPoint(along, y)
    return not any(other.contains(probe)
                   for i, (other, _avail) in enumerate(screens) if i != index)


def wall_for_drag(rect: QRect, cursor: QPoint,
                  screens: list[tuple[QRect, QRect]],
                  distance: int = SNAP_DISTANCE,
                  seam_distance: int = SEAM_DISTANCE) -> tuple[str, int]:
    """끌고 있는 ``rect`` 가 닿은 벽을 ``(쪽, 화면 번호)`` 로 돌려준다.
    닿지 않았으면 쪽이 빈 문자열이다.

    화면은 커서가 있는 곳으로 고른다. 벽은 작업 표시줄을 뺀 영역의 끝이다.

    바깥벽은 ``rect`` 의 끝이 ``distance`` 안에 들면 닿은 것이다. 모니터끼리
    맞닿은 경계에도 붙일 수 있지만, 그곳은 메모를 옆 화면으로 넘길 때 지나는
    길이기도 하다. 끌려가는 쪽지가 경계에서 길게 걸리지 않도록, 경계는 커서가
    ``seam_distance`` 안에 있을 때만 닿은 것으로 본다. 붙는 곳은 커서가 있는
    쪽 화면의 끝이다.

    모서리처럼 두 벽에 함께 닿으면 더 가까운 쪽을 고른다."""
    if not screens:
        return DOCK_NONE, -1
    index = screen_index_at(cursor, screens)
    avail = screens[index][1]
    # (쪽, 메모 끝과 벽의 거리, 커서와 벽의 거리, 벽을 따라 잰 커서 자리)
    gaps = (
        (DOCK_LEFT, rect.left() - avail.left(),
         cursor.x() - avail.left(), cursor.y()),
        (DOCK_RIGHT, avail.right() - rect.right(),
         avail.right() - cursor.x(), cursor.y()),
        (DOCK_TOP, rect.top() - avail.top(),
         cursor.y() - avail.top(), cursor.x()),
        (DOCK_BOTTOM, avail.bottom() - rect.bottom(),
         avail.bottom() - cursor.y(), cursor.x()),
    )
    found = DOCK_NONE
    depth = 0
    for side, rect_gap, cursor_gap, along in gaps:
        if is_outer_wall(side, index, screens, along):
            gap, limit = rect_gap, distance
        else:
            gap, limit = cursor_gap, seam_distance
        if gap > limit:
            continue
        if not found or gap < depth:
            found, depth = side, gap
    return found, index


def snap_to_wall(rect: QRect, side: str, screen: QRect) -> QRect:
    """끌고 있는 쪽지를 벽에 댄 자리. 벽을 따라서는 화면 안에 둔다."""
    r = QRect(rect)
    if side == DOCK_LEFT:
        r.moveLeft(screen.left())
    elif side == DOCK_RIGHT:
        r.moveRight(screen.right())
    elif side == DOCK_TOP:
        r.moveTop(screen.top())
    elif side == DOCK_BOTTOM:
        r.moveBottom(screen.bottom())
    if side in _SIDE_WALLS:
        r.moveTop(max(screen.top(), min(r.top(), screen.bottom() - r.height() + 1)))
    elif side:
        r.moveLeft(max(screen.left(), min(r.left(), screen.right() - r.width() + 1)))
    return r


def keep_on_screen(rect: QRect, screen: QRect) -> QRect:
    """``rect`` 가 화면 밖으로 나가 있으면 안으로 들인다."""
    r = QRect(rect)
    if r.right() > screen.right():
        r.moveRight(screen.right())
    if r.bottom() > screen.bottom():
        r.moveBottom(screen.bottom())
    if r.left() < screen.left():
        r.moveLeft(screen.left())
    if r.top() < screen.top():
        r.moveTop(screen.top())
    return r
