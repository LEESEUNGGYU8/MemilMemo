from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import (
    QBuffer,
    QByteArray,
    QIODevice,
    QObject,
    QPointF,
    QRect,
    QRectF,
    QTimer,
    QUrl,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontInfo,
    QFontMetricsF,
    QIcon,
    QImage,
    QImageReader,
    QMovie,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QSyntaxHighlighter,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextFormat,
    QTextImageFormat,
)
from PySide6.QtWidgets import QLabel, QTextEdit

from .. import config, html_guard
from .. import file_links as fl
from . import font_colors
from .link_icons import link_icon_pixmap
from .safe_text import SafeTextDocument, is_plain_raster


_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".apng"}


def _is_image_file(path: str) -> bool:
    return Path(path).suffix.lower() in _IMAGE_EXTS


def _data_url_payload(data_url: str) -> QByteArray:
    if not data_url or not data_url.startswith("data:"):
        return QByteArray()
    try:
        _, payload = data_url.split(",", 1)
        raw = QByteArray.fromBase64(payload.encode("ascii"))
    except ValueError:
        return QByteArray()
    # 본문의 그림은 남이 보낸 바이트다. 이 앱이 넣는 형식만 풀어 본다.
    if not is_plain_raster(raw.left(16).data()):
        return QByteArray()
    return raw


def _data_url_format_hint(data_url: str) -> str:
    try:
        mime = data_url.split(";", 1)[0].split(":", 1)[1]
        subtype = mime.split("/", 1)[1].lower()
    except (IndexError, ValueError):
        return ""
    if subtype in ("jpeg", "jpg"):
        return "JPG"
    if subtype == "apng":
        return "PNG"
    return subtype.upper()


def data_url_payload(data_url: str) -> QByteArray:
    """``data:`` URL 의 base64 본문을 바이트로 돌려준다. 아니면 빈 배열."""
    return _data_url_payload(data_url)


def data_url_image(data_url: str) -> QImage:
    """data URL 을 원본 해상도 QImage 로 디코드한다(애니메이션은 첫 프레임).
    실패하면 null 이미지."""
    img = QImage()
    ba = _data_url_payload(data_url)
    if not ba.isEmpty():
        img.loadFromData(ba)
    return img


def is_animated_data_url(data_url: str) -> bool:
    ba = _data_url_payload(data_url)
    if ba.isEmpty():
        return False
    buf = QBuffer(ba)
    buf.open(QIODevice.ReadOnly)
    reader = QImageReader()
    reader.setDecideFormatFromContent(True)
    reader.setDevice(buf)
    try:
        if not reader.canRead():
            return False
        return reader.supportsAnimation() and reader.imageCount() > 1
    finally:
        buf.close()


def image_to_data_url(image: QImage, max_width: int = config.MAX_IMAGE_WIDTH) -> str:
    if image.isNull():
        return ""
    if image.width() > max_width:
        image = image.scaledToWidth(max_width, Qt.SmoothTransformation)
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    image.save(buf, "PNG")
    b64 = bytes(buf.data().toBase64()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def bytes_to_data_url(raw: bytes, subtype: str) -> str:
    if not raw:
        return ""
    ba = QByteArray(raw)
    b64 = bytes(ba.toBase64()).decode("ascii")
    return f"data:image/{subtype.lower()};base64,{b64}"


_CHECKBOX_URL_CACHE: dict[bool, str] = {}

# 글머리 마커와 같은 x 좌표에 정렬되도록 체크박스 글리프 왼쪽에 투명
# 패딩을 더한다.
CHECKBOX_GLYPH_SIZE = 16
CHECKBOX_LEFT_PAD = 3
CHECKBOX_WIDTH = CHECKBOX_GLYPH_SIZE + CHECKBOX_LEFT_PAD
CHECKBOX_HEIGHT = CHECKBOX_GLYPH_SIZE


def _render_checkbox_png(checked: bool) -> QByteArray:
    pix = QPixmap(CHECKBOX_WIDTH, CHECKBOX_HEIGHT)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    margin = 1.5
    size = CHECKBOX_GLYPH_SIZE
    x_off = CHECKBOX_LEFT_PAD
    rect = QRectF(x_off + margin, margin, size - 2 * margin, size - 2 * margin)
    radius = 3.5
    if checked:
        p.setBrush(QColor("#2F2F2F"))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(rect, radius, radius)
        p.setPen(QPen(QColor("#FFFFFF"), 1.8, Qt.SolidLine,
                       Qt.RoundCap, Qt.RoundJoin))
        p.drawLine(QPointF(x_off + size * 0.28, size * 0.52),
                    QPointF(x_off + size * 0.45, size * 0.70))
        p.drawLine(QPointF(x_off + size * 0.45, size * 0.70),
                    QPointF(x_off + size * 0.74, size * 0.33))
    else:
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor("#9A9A9A"), 1.4))
        p.drawRoundedRect(rect, radius, radius)
    p.end()
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    pix.save(buf, "PNG")
    return buf.data()


def checkbox_url(checked: bool) -> str:
    cached = _CHECKBOX_URL_CACHE.get(checked)
    if cached is not None:
        return cached
    ba = _render_checkbox_png(checked)
    b64 = bytes(ba.toBase64()).decode("ascii")
    url = f"data:image/png;base64,{b64}"
    _CHECKBOX_URL_CACHE[checked] = url
    return url


def is_checkbox_url(url: str) -> bool:
    if not url:
        return False
    return url == checkbox_url(False) or url == checkbox_url(True)


BULLET_CIRCLE = "circle"
BULLET_SQUARE = "square"
BULLET_ARROW = "arrow"
BULLET_INFO = "info"

_BULLET_NAMES = (BULLET_CIRCLE, BULLET_SQUARE, BULLET_ARROW, BULLET_INFO)
_BULLET_URL_PREFIX = "x-sticky-bullet:"
_BULLET_IMAGE_CACHE: dict[str, QImage] = {}
_BULLET_FILL = QColor("#2F2F2F")


def bullet_markers() -> tuple[str, ...]:
    return _BULLET_NAMES


def bullet_url_for(name: str) -> str | None:
    if name not in _BULLET_NAMES:
        return None
    return _BULLET_URL_PREFIX + name


def url_to_bullet_name(url: str) -> str | None:
    if not url or not url.startswith(_BULLET_URL_PREFIX):
        return None
    name = url[len(_BULLET_URL_PREFIX):]
    return name if name in _BULLET_NAMES else None


def is_bullet_url(url: str) -> bool:
    return url_to_bullet_name(url) is not None


def is_marker_url(url: str) -> bool:
    return is_checkbox_url(url) or is_bullet_url(url)


def _render_bullet_icon(name: str, fill: QColor | None = None) -> QImage:
    if fill is None:
        fill = _BULLET_FILL
    canvas_w = CHECKBOX_WIDTH
    canvas_h = CHECKBOX_HEIGHT
    pix = QPixmap(canvas_w, canvas_h)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)

    glyph = CHECKBOX_GLYPH_SIZE
    cx = CHECKBOX_LEFT_PAD + glyph / 2.0
    cy = glyph / 2.0

    if name == BULLET_CIRCLE:
        p.setPen(Qt.NoPen)
        p.setBrush(fill)
        r = 3.6
        p.drawEllipse(QPointF(cx, cy), r, r)
    elif name == BULLET_SQUARE:
        p.setPen(Qt.NoPen)
        p.setBrush(fill)
        side = 7.4
        p.drawRoundedRect(
            QRectF(cx - side / 2, cy - side / 2, side, side), 2.1, 2.1)
    elif name == BULLET_ARROW:
        pen = QPen(fill, 1.85,
                    Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        hw = 2.6
        hh = 4.0
        path = QPainterPath(QPointF(cx - hw, cy - hh))
        path.lineTo(QPointF(cx + hw, cy))
        path.lineTo(QPointF(cx - hw, cy + hh))
        p.drawPath(path)
    elif name == BULLET_INFO:
        pen = QPen(fill, 1.4,
                    Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        r = 5.6
        p.drawEllipse(QPointF(cx, cy), r, r)
        p.setPen(Qt.NoPen)
        p.setBrush(fill)
        dot_r = 0.85
        p.drawEllipse(QPointF(cx, cy - 2.3), dot_r, dot_r)
        stem_w = 1.3
        stem_h = 3.2
        stem_top = cy - 0.6
        p.drawRoundedRect(
            QRectF(cx - stem_w / 2, stem_top, stem_w, stem_h), 0.6, 0.6)
    p.end()
    return pix.toImage()


def bullet_image(name: str) -> QImage | None:
    if name not in _BULLET_NAMES:
        return None
    cached = _BULLET_IMAGE_CACHE.get(name)
    if cached is not None:
        return cached
    img = _render_bullet_icon(name)
    _BULLET_IMAGE_CACHE[name] = img
    return img


def bullet_qicon(name: str) -> QIcon:
    """인라인 정렬용 왼쪽 패딩을 잘라내지 않으면 메뉴 아이콘 칼럼에서
    오른쪽으로 밀려 보인다."""
    img = bullet_image(name)
    if img is None or img.isNull():
        return QIcon()
    cropped = img.copy(CHECKBOX_LEFT_PAD, 0,
                       img.width() - CHECKBOX_LEFT_PAD, img.height())
    return QIcon(QPixmap.fromImage(cropped))


def register_bullet_resources(document: QTextDocument) -> None:
    for name in _BULLET_NAMES:
        url = _BULLET_URL_PREFIX + name
        img = bullet_image(name)
        if img is None or img.isNull():
            continue
        document.addResource(
            QTextDocument.ImageResource, QUrl(url), img)


_CHECKBOX_IMAGE_CACHE: dict[bool, QImage] = {}


def checkbox_image(checked: bool) -> QImage:
    cached = _CHECKBOX_IMAGE_CACHE.get(checked)
    if cached is not None:
        return cached
    raw = _render_checkbox_png(checked)
    img = QImage()
    img.loadFromData(raw)
    _CHECKBOX_IMAGE_CACHE[checked] = img
    return img


def checkbox_qicon(checked: bool) -> QIcon:
    img = checkbox_image(checked)
    cropped = img.copy(CHECKBOX_LEFT_PAD, 0,
                       img.width() - CHECKBOX_LEFT_PAD, img.height())
    return QIcon(QPixmap.fromImage(cropped))


# 마커는 블록 포맷의 커스텀 속성에 코드로 저장하고, 글리프는 paintEvent 가
# 왼쪽 여백에 직접 그린다. 텍스트에 마커 문자를 넣지 않으므로 공백 없는 CJK
# 줄도 글머리 뒤에서 끊기지 않고, 줄바꿈된 줄이 본문에 정렬된다. 블록 포맷에
# 두면 undo 로 되돌릴 수 있고, 줄 분할 시 여백과 함께 상속돼 리스트가 자연히
# 이어진다.
MARKER_PROP = QTextFormat.UserProperty + 1
MARKER_NONE = 0
MARKER_CIRCLE = 1
MARKER_SQUARE = 2
MARKER_ARROW = 3
MARKER_INFO = 4
MARKER_CHECK0 = 5
MARKER_CHECK1 = 6
MARKER_NUMBER = 7
MARKER_CODES = frozenset({1, 2, 3, 4, 5, 6, 7})
BULLET_CODES = frozenset({1, 2, 3, 4})
CHECK_CODES = frozenset({MARKER_CHECK0, MARKER_CHECK1})
# 옛 버전이 쓰던 고정 여백. 지금은 글자 크기에 맞춰 잰다(marker_column).
MARKER_MARGIN = float(CHECKBOX_WIDTH + 7)

# 목록의 단계(0~3). 블록 포맷의 커스텀 속성에 두고, 저장할 때는 블록의
# 들여쓰기 값(-qt-block-indent)으로 적는다. 옛 버전은 그 값을 그대로 물려 저장하므로
# 옛 버전이 고쳐 저장해도 단계가 남는다(왼쪽 여백으로 적으면 옛 버전이 읽는 순간 지운다).
LEVEL_PROP = QTextFormat.UserProperty + 3
MAX_LEVEL = 3
# Tab 한 번에 들어가는 폭(글자 크기에 대한 비율). 한글 한 글자의 절반이다.
LEVEL_STEP = 0.5
# 이 편집기가 서식 사이에 넣은 위 간격(px). 화면에만 쓰고 저장할 때 뺀다.
SPACING_PROP = QTextFormat.UserProperty + 4

# 번호 목록은 줄 앞에 '1. ' 을 글자로 적어 저장하고, 번호 글자에 이 이름의
# 앵커를 붙여 번호 목록임을 표시한다. 옛 버전에는 보통 글자로 보이고, 앵커가 있으면
# 옛 버전이 늘 HTML 로 저장하므로 번호와 단계가 함께 남는다.
NUMBER_ANCHOR = "memil-num"

# 서식 사이의 간격(기본 글자 크기에 대한 비율). 앞 줄과 이 줄의 종류로 정한다.
# 글과 글 사이, 빈 줄의 앞뒤에는 넣지 않는다.
GAP_IMAGE = 0.45        # 그림의 위와 아래
GAP_LIST_START = 0.32   # 글 다음에 목록이 시작될 때
GAP_LIST_END = 0.45     # 목록 다음에 글이 올 때
GAP_ITEM = 0.14         # 목록의 줄과 줄
GAP_GROUP = 0.34        # 딸린 줄들 다음에 오는 1단계 줄

(SHAPE_DOT, SHAPE_RING, SHAPE_DASH, SHAPE_MIDDOT, SHAPE_SQUARE, SHAPE_ARROW,
 SHAPE_INFO) = range(1, 8)
# 네모 목록은 단계에 따라 기호가 바뀐다(■ ○ – ·). 나머지 기호는 그대로다.
OUTLINE_SHAPES = (SHAPE_SQUARE, SHAPE_RING, SHAPE_DASH, SHAPE_MIDDOT)


def marker_column(em: float) -> float:
    """기호가 들어가는 칸의 폭(px). 글자 크기를 따른다."""
    return float(max(18, round(em * 1.62)))


def marker_shape(code: int, level: int) -> int:
    if code == MARKER_SQUARE:
        return OUTLINE_SHAPES[max(0, min(level, len(OUTLINE_SHAPES) - 1))]
    return {MARKER_CIRCLE: SHAPE_DOT, MARKER_ARROW: SHAPE_ARROW,
            MARKER_INFO: SHAPE_INFO}.get(code, 0)


def marker_ink(light: bool) -> QColor:
    return QColor("#F2F2F2") if light else QColor("#2F2F2F")


def draw_marker_shape(p: QPainter, shape: int, cx: float, cy: float, em: float,
                      ink: QColor) -> None:
    """글머리 기호를 도형으로 그린다. 그림을 늘려 그리지 않으므로 글자 크기와
    화면 배율이 달라도 또렷하다."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing, True)
    if shape == SHAPE_DOT:
        r = max(2.4, em * 0.205)
        p.setPen(Qt.NoPen)
        p.setBrush(ink)
        p.drawEllipse(QPointF(cx, cy), r, r)
    elif shape == SHAPE_RING:
        w = max(1.2, em * 0.088)
        r = max(2.4, em * 0.205) - w / 2
        p.setPen(QPen(ink, w))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(cx, cy), r, r)
    elif shape == SHAPE_DASH:
        w = max(1.3, em * 0.095)
        half = max(2.6, em * 0.215)
        p.setPen(QPen(ink, w, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(cx - half, cy), QPointF(cx + half, cy))
    elif shape == SHAPE_MIDDOT:
        r = max(1.5, em * 0.115)
        p.setPen(Qt.NoPen)
        p.setBrush(ink)
        p.drawEllipse(QPointF(cx, cy), r, r)
    elif shape == SHAPE_SQUARE:
        s = max(5.0, em * 0.42)
        p.setPen(Qt.NoPen)
        p.setBrush(ink)
        p.drawRoundedRect(QRectF(cx - s / 2, cy - s / 2, s, s),
                          em * 0.07, em * 0.07)
    elif shape == SHAPE_ARROW:
        w = max(1.4, em * 0.115)
        hw, hh = em * 0.17, em * 0.27
        p.setPen(QPen(ink, w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        path = QPainterPath(QPointF(cx - hw, cy - hh))
        path.lineTo(QPointF(cx + hw, cy))
        path.lineTo(QPointF(cx - hw, cy + hh))
        p.drawPath(path)
    elif shape == SHAPE_INFO:
        r = em * 0.37
        p.setPen(QPen(ink, max(1.2, em * 0.093)))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(cx, cy), r, r)
        p.setPen(Qt.NoPen)
        p.setBrush(ink)
        dot = max(0.8, em * 0.058)
        p.drawEllipse(QPointF(cx, cy - em * 0.155), dot, dot)
        stem_w, stem_h = max(1.2, em * 0.087), em * 0.215
        p.drawRoundedRect(QRectF(cx - stem_w / 2, cy - em * 0.04, stem_w, stem_h),
                          em * 0.04, em * 0.04)
    p.restore()


def draw_checkbox(p: QPainter, checked: bool, cx: float, cy: float, em: float,
                  light: bool) -> None:
    """할 일의 상자를 글자 크기에 맞춰 그린다."""
    side = max(11.0, em * 0.88)
    rect = QRectF(cx - side / 2, cy - side / 2, side, side)
    radius = side * 0.27
    ink = marker_ink(light)
    p.save()
    p.setRenderHint(QPainter.Antialiasing, True)
    if checked:
        p.setPen(Qt.NoPen)
        p.setBrush(ink)
        p.drawRoundedRect(rect, radius, radius)
        p.setPen(QPen(QColor("#2F2F2F") if light else QColor("#FFFFFF"),
                      max(1.6, side * 0.135), Qt.SolidLine, Qt.RoundCap,
                      Qt.RoundJoin))
        x0, y0 = rect.left(), rect.top()
        path = QPainterPath(QPointF(x0 + side * 0.25, y0 + side * 0.53))
        path.lineTo(QPointF(x0 + side * 0.43, y0 + side * 0.71))
        path.lineTo(QPointF(x0 + side * 0.76, y0 + side * 0.31))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
    else:
        w = max(1.4, side * 0.11)
        p.setPen(QPen(QColor("#D0D0D0") if light else QColor("#5C5C5C"), w))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(rect.adjusted(w / 2, w / 2, -w / 2, -w / 2),
                          radius, radius)
    p.restore()


def list_menu_icon(code: int) -> QIcon:
    """목록 메뉴에 넣을 16픽셀 아이콘. 배율이 높은 화면용 그림도 함께 넣는다."""
    icon = QIcon()
    for dpr in (1.0, 2.0):
        pix = QPixmap(int(16 * dpr), int(16 * dpr))
        pix.setDevicePixelRatio(dpr)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        if code == MARKER_NUMBER:
            font = QFont(p.font())
            font.setPixelSize(12)
            font.setBold(True)
            p.setFont(font)
            p.setPen(marker_ink(False))
            p.drawText(QRectF(0, 0, 16, 16), Qt.AlignCenter, "1.")
        elif code in CHECK_CODES:
            draw_checkbox(p, code == MARKER_CHECK1, 8, 8, 15.0, False)
        else:
            draw_marker_shape(p, marker_shape(code, 0), 8, 8, 17.0,
                              marker_ink(False))
        p.end()
        icon.addPixmap(pix)
    return icon

_CODE_TO_BULLET = {
    MARKER_CIRCLE: BULLET_CIRCLE, MARKER_SQUARE: BULLET_SQUARE,
    MARKER_ARROW: BULLET_ARROW, MARKER_INFO: BULLET_INFO,
}
_BULLET_TO_CODE = {v: k for k, v in _CODE_TO_BULLET.items()}


def marker_glyph(code: int):
    if code in _CODE_TO_BULLET:
        return bullet_image(_CODE_TO_BULLET[code])
    if code == MARKER_CHECK0:
        return checkbox_image(False)
    if code == MARKER_CHECK1:
        return checkbox_image(True)
    return None


# 화면에 그리는 글머리와 체크박스. 위의 그림들은 옛 HTML 에 박힌 그림과 바이트가
# 같아야 해서 손댈 수 없으므로, 그리기 전용을 따로 둔다. 메모 바탕이 어두우면
# 밝은 벌을 쓴다(어두운 메모에서 글머리와 체크 표시가 1.2:1 로 묻혔다). 빈 상자의
# 테두리도 색 있는 메모에서 보이도록 진하게 했다.
_DISPLAY_GLYPHS: dict[tuple[int, bool], QImage] = {}


def _render_display_checkbox(checked: bool, light: bool) -> QImage:
    pix = QPixmap(CHECKBOX_WIDTH, CHECKBOX_HEIGHT)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    margin = 1.5
    size = CHECKBOX_GLYPH_SIZE
    x_off = CHECKBOX_LEFT_PAD
    rect = QRectF(x_off + margin, margin, size - 2 * margin, size - 2 * margin)
    ink = QColor("#F2F2F2") if light else QColor("#2F2F2F")
    if checked:
        p.setBrush(ink)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(rect, 3.5, 3.5)
        p.setPen(QPen(QColor("#2F2F2F") if light else QColor("#FFFFFF"), 1.8,
                      Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawLine(QPointF(x_off + size * 0.28, size * 0.52),
                   QPointF(x_off + size * 0.45, size * 0.70))
        p.drawLine(QPointF(x_off + size * 0.45, size * 0.70),
                   QPointF(x_off + size * 0.74, size * 0.33))
    else:
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor("#D0D0D0") if light else QColor("#5C5C5C"), 1.5))
        p.drawRoundedRect(rect, 3.5, 3.5)
    p.end()
    return pix.toImage()


def marker_display_glyph(code: int, light: bool = False) -> QImage | None:
    """본문 왼쪽 여백에 그릴 글머리·체크박스. ``light`` 는 어두운 메모용."""
    key = (code, bool(light))
    cached = _DISPLAY_GLYPHS.get(key)
    if cached is not None:
        return cached
    if code in _CODE_TO_BULLET:
        img = _render_bullet_icon(
            _CODE_TO_BULLET[code],
            QColor("#F2F2F2") if light else QColor("#2F2F2F"))
    elif code in (MARKER_CHECK0, MARKER_CHECK1):
        img = _render_display_checkbox(code == MARKER_CHECK1, light)
    else:
        return None
    _DISPLAY_GLYPHS[key] = img
    return img


def contrast_ratio(a: QColor, b: QColor) -> float:
    """WCAG 명암비(1~21)."""
    def lum(c: QColor) -> float:
        def ch(v: float) -> float:
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        return (0.2126 * ch(c.redF()) + 0.7152 * ch(c.greenF())
                + 0.0722 * ch(c.blueF()))
    la, lb = lum(a), lum(b)
    if la < lb:
        la, lb = lb, la
    return (la + 0.05) / (lb + 0.05)


# 이보다 낮으면 글자가 사실상 보이지 않는다. 사용자가 고른 옅은 색(2~4:1)까지
# 바꾸지는 않는다.
MIN_READABLE_CONTRAST = 2.0


@dataclass(frozen=True)
class CopiedFormat:
    """'서식 복사'로 떠 둔 것: 글자 서식 전체와 그 줄의 글머리 기호."""
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strike: bool = False
    size: float = 0.0           # 0 이면 기본 크기
    ink: str = ""               # 빈 글이면 기본 색
    highlight: str = ""         # 빈 글이면 형광펜 없음
    marker: int = 0             # 목록이 아니면 MARKER_NONE
    level: int = 0


# 떠 둔 서식은 메모끼리 옮길 수 있게 프로그램 하나에 한 벌만 둔다(끄면 사라진다).
_copied_format: CopiedFormat | None = None


def copied_format() -> CopiedFormat | None:
    return _copied_format


def set_copied_format(value: CopiedFormat | None) -> None:
    global _copied_format
    _copied_format = value


def marker_url_for_code(code: int) -> str | None:
    if code in _CODE_TO_BULLET:
        return bullet_url_for(_CODE_TO_BULLET[code])
    if code == MARKER_CHECK0:
        return checkbox_url(False)
    if code == MARKER_CHECK1:
        return checkbox_url(True)
    return None


def marker_code_for_url(url: str) -> int:
    name = url_to_bullet_name(url)
    if name is not None:
        return _BULLET_TO_CODE.get(name, MARKER_NONE)
    if url == checkbox_url(False):
        return MARKER_CHECK0
    if url == checkbox_url(True):
        return MARKER_CHECK1
    return MARKER_NONE


# 링크는 본문에 ``<a href>`` 로만 저장한다. 종류 아이콘은 저장하지 않고,
# 불러올 때 링크 앞에 폭만 차지하는 글자(줄바꿈 없는 공백) 하나를 끼운 뒤
# paintEvent 가 그 자리에 그린다. 저장할 때는 그 글자를 다시 뺀다. 그래서
# 아이콘은 항상 실제 연결 대상에서 새로 정해지고, 옛 버전이 연 메모에도
# 깨진 그림이 남지 않는다(밑줄 친 글자로만 보인다).
LINK_ICON_PROP = QTextFormat.UserProperty + 2
LINK_SPACER = "\u00a0"
LINK_COLOR_LIGHT = "#1746A8"   # 밝은 메모 위의 링크 글자색
LINK_COLOR_DARK = "#9CC4FF"    # 어두운 메모 위


def strip_link_icons(doc: QTextDocument) -> None:
    """저장용 사본에서 아이콘 자리 글자를 뺀다."""
    spots: list[tuple[int, int]] = []
    block = doc.begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            if frag.isValid() and frag.charFormat().hasProperty(LINK_ICON_PROP):
                spots.append((frag.position(), frag.length()))
            it += 1
        block = block.next()
    cur = QTextCursor(doc)
    for pos, length in reversed(spots):
        cur.setPosition(pos)
        cur.setPosition(pos + length, QTextCursor.KeepAnchor)
        cur.removeSelectedText()


def has_non_marker_image(html: str) -> bool:
    if not html or "<img" not in html.lower():
        return False
    import re
    pattern = re.compile(r'<img[^>]*src=["\']([^"\']+)["\']', re.IGNORECASE)
    for match in pattern.finditer(html):
        if not is_marker_url(match.group(1)):
            return True
    return False


has_non_checkbox_image = has_non_marker_image


def html_to_plain(html: str) -> str:
    if not html:
        return ""
    if not html_guard.is_markup(html):
        # 서식 없이 저장된 글이다. 꺾쇠가 들어 있어도 글자 그대로다.
        return html
    # 읽기만 해도 본문이 가리키는 스타일시트를 파일에서 열려고 한다.
    doc = SafeTextDocument()
    doc.setHtml(html)
    return doc.toPlainText().replace("￼", "")


# PySide6 는 QObject 서브클래스를 통해 QTextObjectInterface 가상
# 메서드를 디스패치하지 않아 커스텀 text-object 핸들러로 애니메이션
# 프레임을 그릴 수 없다. 그래서 애니메이션 이미지를 표준 ``<img
# src="data:...">`` 조각에 두고, 매 QMovie 프레임마다 QTextDocument
# 의 이미지 리소스를 교체해 구동한다.

class AnimationDriver(QObject):
    def __init__(self, host_edit: QTextEdit):
        super().__init__(host_edit)
        self._host = host_edit
        # QMovie 가 버퍼를 스트리밍하므로, 살려두지 않으면 Python 이 GC
        # 해서 재생이 끊긴다.
        self._movies: dict[str, tuple[QMovie, QBuffer]] = {}

    def known_urls(self) -> set[str]:
        return set(self._movies)

    def register(self, data_url: str) -> bool:
        """이번 호출에서 새 QMovie 를 만들었으면 True. 호출자는 Qt 가
        그리기 전에 첫 프레임을 배치하는 데 쓸 수 있다."""
        if not data_url or data_url in self._movies:
            return False
        ba = _data_url_payload(data_url)
        if ba.isEmpty():
            return False
        buf = QBuffer()
        buf.setData(ba)
        if not buf.open(QIODevice.ReadOnly):
            return False
        fmt_hint = _data_url_format_hint(data_url)
        movie = QMovie()
        movie.setDevice(buf)
        if fmt_hint:
            movie.setFormat(QByteArray(fmt_hint.encode("ascii")))
        movie.setCacheMode(QMovie.CacheAll)
        if not movie.isValid():
            buf.close()
            return False
        movie.frameChanged.connect(
            lambda _frame, u=data_url: self._on_frame_changed(u))
        self._movies[data_url] = (movie, buf)
        movie.start()
        # 최초 paint 가 원시 data URL 의 기본 디코드로 폴백하지 않도록
        # 첫 프레임을 동기적으로 넣어 둔다.
        self._swap_resource(data_url)
        return True

    def _on_frame_changed(self, data_url: str) -> None:
        self._swap_resource(data_url)

    def _swap_resource(self, data_url: str) -> None:
        entry = self._movies.get(data_url)
        if entry is None:
            return
        movie, _buf = entry
        pix = movie.currentPixmap()
        if pix.isNull():
            return
        img = pix.toImage()
        doc = self._host.document()
        doc.addResource(QTextDocument.ImageResource, QUrl(data_url), img)
        vp = self._host.viewport()
        if vp is not None:
            vp.update()

    def unregister_missing(self, current_urls: set[str]) -> None:
        stale = [u for u in self._movies if u not in current_urls]
        for url in stale:
            movie, buf = self._movies.pop(url)
            movie.stop()
            buf.close()

    def stop_all(self) -> None:
        for movie, buf in self._movies.values():
            movie.stop()
            buf.close()
        self._movies.clear()


class _MarkerAwareDocument(SafeTextDocument):
    """``setHtml`` 이 렌더링 전에 리소스 캐시를 비우는데, 그 사이 Qt 가
    그린 깨진 이미지 placeholder 는 리소스를 재등록해도 남는다. 글머리
    URL 을 ``loadResource`` 에서 직접 해결하면 캐시가 비어 있어도 매
    paint 가 이 메서드로 처리되어 그 문제가 사라진다.

    그 밖의 주소는 ``SafeTextDocument`` 가 거른다: 본문에 든 ``data:`` 그림만
    읽고, 파일이나 네트워크 경로는 열지 않는다."""

    def loadResource(self, resource_type, name):  # noqa: N802
        if resource_type == QTextDocument.ImageResource:
            url_str = (name.toString() if isinstance(name, QUrl)
                       else str(name))
            if is_bullet_url(url_str):
                bname = url_to_bullet_name(url_str)
                if bname is not None:
                    img = bullet_image(bname)
                    if img is not None and not img.isNull():
                        return img
        return super().loadResource(resource_type, name)


class _DoneHighlighter(QSyntaxHighlighter):
    """끝낸 할 일의 글자를 흐리게 하고 줄을 긋는다. 화면에만 입히는 서식이라
    문서(저장 내용, 되돌리기)는 바뀌지 않는다."""

    def __init__(self, document: QTextDocument):
        super().__init__(document)
        self._light = False

    def set_light(self, light: bool) -> None:
        if bool(light) != self._light:
            self._light = bool(light)
            self.rehighlight()

    def highlightBlock(self, text: str) -> None:  # noqa: N802
        if not text:
            return
        block = self.currentBlock()
        if block.blockFormat().intProperty(MARKER_PROP) != MARKER_CHECK1:
            return
        ink = QColor("#FFFFFF") if self._light else QColor("#1F1F1F")
        ink.setAlphaF(0.46)
        fmt = QTextCharFormat()
        fmt.setForeground(ink)
        fmt.setFontStrikeOut(True)
        self.setFormat(0, len(text), fmt)


# 치거나 붙여 넣으면 링크로 바꾸는 주소의 머리, 주소 끝에서 떼어 내는 문장 부호.
_AUTO_LINK_HEADS = ("http://", "https://", "www.", "file:")
_AUTO_LINK_TAIL = ".,;:!?)]}>'\"」』）"
_WORD = re.compile(r"\S+")


def auto_link_target(word: str) -> tuple[str, str] | None:
    """``word`` 가 웹 주소나 file: 주소면 ``(링크로 삼을 글자, href)``."""
    if not word.lower().startswith(_AUTO_LINK_HEADS):
        return None
    text = word.rstrip(_AUTO_LINK_TAIL)
    href = fl.parse_target(text)
    if not href or not fl.is_supported(href):
        return None
    if fl.is_web(href):
        from urllib.parse import urlsplit
        if not urlsplit(href).netloc.strip("."):
            return None                     # 'http://' 처럼 머리만 있는 것
    elif len(fl.href_to_path(href) or "") < 3:
        return None
    return text, href


class RichTextEdit(QTextEdit):
    imageInserted = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDocument(_MarkerAwareDocument(self))
        self.setAcceptRichText(True)
        self.setAcceptDrops(True)
        self._anim = AnimationDriver(self)
        self.document().contentsChange.connect(self._on_contents_change)
        register_bullet_resources(self.document())

        # 파일 링크. 아이콘 자리 맞추기(sync_link_icons)는 편집 직후 한 번 미뤄
        # 돌리고, 되돌리기(undo)로 생긴 변화에는 돌리지 않는다.
        self._link_color = QColor(LINK_COLOR_LIGHT)
        self._has_links = False
        # 메모 바탕이 어두우면 글머리와 체크박스를 밝은 벌로 그린다.
        self._marker_light = False
        self._link_syncing = False
        self._link_sync_pending = False
        self._undo_cmd_added = False
        # 목록의 여백과 서식 사이 간격을 맞추는 중인가(restyle_lists).
        self._restyling = False
        self._done = _DoneHighlighter(self.document())
        self.document().undoCommandAdded.connect(self._on_undo_command_added)
        self.cursorPositionChanged.connect(self._leave_link_format_at_edges)

        # 본문 이미지의 원본 크기와 애니메이션 여부(URL → (w, h, animated)).
        # 헤더만 읽어 채우므로 전체 디코드가 없다.
        self._img_meta: dict[str, tuple[int, int, bool]] = {}
        # 문서에 등록해 둔 표시용 리소스의 픽셀 크기(URL → (w, h)). Qt 는 문서
        # 안 이미지를 최근접 방식으로 줄여 그려 큰 원본을 그대로 두면 계단이
        # 지므로, 표시 크기로 부드럽게 줄인 사본을 리소스로 넣는다. 저장 HTML
        # 에는 원본 data URL 이 그대로 남아 데이터는 바뀌지 않는다.
        self._display_res: dict[str, tuple[int, int]] = {}
        # 표시용 사본을 만들 때 쓰는 원본 디코드 캐시. 크기 조절 드래그 동안만
        # 살려 두고 곧 비워, 큰 원본이 메모(숨은 메모 포함)마다 상주하지 않게 한다.
        self._full_cache: dict[str, QImage] = {}
        self._full_cache_timer = QTimer(self)
        self._full_cache_timer.setSingleShot(True)
        self._full_cache_timer.setInterval(1500)
        self._full_cache_timer.timeout.connect(self._full_cache.clear)

        self._size_popup = QLabel(self)
        self._size_popup.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._size_popup.setAlignment(Qt.AlignCenter)
        self._size_popup.setStyleSheet(
            "background: rgba(28, 28, 28, 0.88); color: #FFFFFF; "
            "border-radius: 9px; padding: 6px 14px; "
            "font-size: 13px; font-weight: 700;"
        )
        self._size_popup.hide()
        self._size_popup_timer = QTimer(self)
        self._size_popup_timer.setSingleShot(True)
        self._size_popup_timer.timeout.connect(self._size_popup.hide)

    def _show_size_popup(self, size: int) -> None:
        self._size_popup.setText(f"{size} pt")
        self._size_popup.adjustSize()
        x = (self.width() - self._size_popup.width()) // 2
        y = (self.height() - self._size_popup.height()) // 2
        self._size_popup.move(max(4, x), max(4, y))
        self._size_popup.show()
        self._size_popup.raise_()
        self._size_popup_timer.start(900)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        """Qt 는 글머리 마커를 끌 수 있는 image-object 로 다룬다. 글머리
        좌클릭을 redirect 해 커서를 마커 바로 뒤로 보내면, 선택 가능한
        이미지가 아니라 접두 글리프처럼 동작한다. 체크박스는 redirect
        하지 않는다. StickyNoteWidget 이벤트 필터가 토글을 위해 클릭을
        가로채므로 이 메서드까지 오지 않는다."""
        if event.button() == Qt.LeftButton:
            local_pos = (event.position().toPoint()
                         if hasattr(event, "position") else event.pos())
            target = self._snap_past_bullet(local_pos)
            if target is not None:
                cursor = self.textCursor()
                anchor_mode = (
                    QTextCursor.KeepAnchor
                    if event.modifiers() & Qt.ShiftModifier
                    else QTextCursor.MoveAnchor
                )
                cursor.setPosition(target, anchor_mode)
                self.setTextCursor(cursor)
                event.accept()
                return
        super().mousePressEvent(event)

    def _snap_past_bullet(self, pos) -> int | None:
        """``pos`` 가 글머리 마커 위라면 마커와 뒤따르는 공백 다음의
        문서 위치를, 아니면 None 을 반환한다."""
        cursor = self.cursorForPosition(pos)
        block = cursor.block()
        if not block.isValid():
            return None
        # 마커 조각은 블록 맨 앞에 있다. 커서가 그보다 뒤면 이미 마커를
        # 지나온 상태라 redirect 할 필요가 없다.
        col = cursor.position() - block.position()
        if col > 1:
            return None
        it = block.begin()
        if it.atEnd():
            return None
        frag = it.fragment()
        fmt = frag.charFormat()
        if not fmt.isImageFormat():
            return None
        url = fmt.toImageFormat().name()
        if not is_bullet_url(url):
            return None
        text = block.text()
        advance = 2 if len(text) > 1 and text[1] == " " else 1
        return block.position() + advance

    def canInsertFromMimeData(self, source) -> bool:  # noqa: N802
        if source.hasImage():
            return True
        if source.hasUrls():
            for url in source.urls():
                if url.isLocalFile():
                    return True
        return super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source) -> None:  # noqa: N802
        # 로컬 이미지 파일 URL 을 먼저 처리해야 원본 바이트와 애니메이션
        # 프레임을 그대로 얻는다.
        if source.hasUrls():
            inserted = False
            others: list[str] = []
            for url in source.urls():
                if not url.isLocalFile():
                    continue
                path = url.toLocalFile()
                if _is_image_file(path):
                    if self._insert_image_from_path(path):
                        inserted = True
                else:
                    others.append(path)
            # 그림이 아닌 파일과 폴더는 본문에 넣지 않고 바로 가기 링크로 건다.
            if others:
                self.insert_file_links(others)
                inserted = True
            if inserted:
                return

        text = source.text() if source.hasText() else ""
        raw_img = source.imageData() if source.hasImage() else None
        has_text = bool(text)
        has_image = isinstance(raw_img, QImage) and not raw_img.isNull()

        # 복사한 HTML 의 배경·폰트가 메모로 따라오지 않도록 항상 평문으로
        # 붙여넣는다. 텍스트와 이미지를 한 edit-block 에 묶어 undo/redo
        # 가 전체를 한 번에 되돌리게 한다.
        if has_text or has_image:
            cursor = self.textCursor()
            cursor.beginEditBlock()
            try:
                if has_text:
                    start = cursor.selectionStart()
                    cursor.insertText(text)
                    end = cursor.position()
                    added = self.linkify_range(start, end)
                    if added:
                        cursor.setPosition(end + added)
                        self.setTextCursor(cursor)
                if has_image:
                    if has_text:
                        cursor.insertText("\n")
                    self.insert_image(raw_img)
            finally:
                cursor.endEditBlock()
            return

        super().insertFromMimeData(source)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if self.canInsertFromMimeData(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if self.canInsertFromMimeData(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        if self.canInsertFromMimeData(event.mimeData()):
            self.insertFromMimeData(event.mimeData())
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    def insert_image(self, image: QImage) -> None:
        data_url = image_to_data_url(image)
        if not data_url:
            return
        self.document().addResource(
            QTextDocument.ImageResource, QUrl(data_url), image)
        cursor = self.textCursor()
        cursor.beginEditBlock()
        try:
            cursor.insertHtml(f'<img src="{data_url}" />')
            self.fit_images_to_viewport()
        finally:
            cursor.endEditBlock()
        self.imageInserted.emit()

    def _insert_image_from_path(self, path: str) -> bool:
        p = Path(path)
        try:
            raw = p.read_bytes()
        except OSError:
            return False
        if not raw:
            return False
        ext = p.suffix.lstrip(".").lower()
        if ext in ("jpg", "jpeg"):
            ext = "jpeg"
        ba = QByteArray(raw)
        buf = QBuffer(ba)
        buf.open(QIODevice.ReadOnly)
        reader = QImageReader()
        reader.setDecideFormatFromContent(True)
        reader.setDevice(buf)
        is_animated = reader.supportsAnimation() and reader.imageCount() > 1
        buf.close()
        if is_animated:
            data_url = bytes_to_data_url(raw, ext)
            self._insert_animated(data_url)
        else:
            img = QImage()
            img.loadFromData(ba)
            if img.isNull():
                return False
            self.insert_image(img)
        return True

    def _insert_animated(self, data_url: str) -> None:
        if not data_url:
            return
        # Qt 가 조각을 레이아웃하기 전에 URL 에 그릴 내용이 있도록 먼저
        # 첫 프레임을 채운다.
        self._anim.register(data_url)
        cursor = self.textCursor()
        cursor.beginEditBlock()
        try:
            cursor.insertHtml(f'<img src="{data_url}" />')
            self.fit_images_to_viewport()
        finally:
            cursor.endEditBlock()
        self.imageInserted.emit()

    # ── 파일 링크 ──────────────────────────────────────────────────────

    def _effective_font(self, fmt: QTextCharFormat) -> QFont:
        font = QFont(self.document().defaultFont())
        size = fmt.fontPointSize()
        if size > 0:
            font.setPointSizeF(size)
        if fmt.fontWeight() > QFont.Normal:
            font.setBold(True)
        return font

    @staticmethod
    def _icon_px(fm: QFontMetricsF) -> float:
        return float(max(12, min(30, round(fm.height() * 0.92))))

    @staticmethod
    def _plain_format(fmt: QTextCharFormat) -> QTextCharFormat:
        """링크에 딸린 속성(연결 주소·밑줄·색·아이콘 자리)을 걷어 낸 서식."""
        out = QTextCharFormat(fmt)
        out.setAnchor(False)
        out.clearProperty(QTextFormat.AnchorHref)
        out.clearProperty(QTextFormat.AnchorName)
        out.clearProperty(LINK_ICON_PROP)
        out.clearProperty(QTextFormat.FontLetterSpacing)
        out.clearProperty(QTextFormat.FontLetterSpacingType)
        out.setFontUnderline(False)
        out.clearForeground()
        return out

    def _link_formats(self, href: str, base: QTextCharFormat | None = None
                      ) -> tuple[QTextCharFormat, QTextCharFormat]:
        """``(아이콘 자리 서식, 링크 글자 서식)``."""
        text = self._plain_format(base) if base is not None else QTextCharFormat()
        text.setAnchor(True)
        text.setAnchorHref(href)
        text.setFontUnderline(True)
        text.setForeground(QBrush(self._link_color))
        spacer = QTextCharFormat(text)
        spacer.setFontUnderline(False)
        spacer.setProperty(LINK_ICON_PROP, fl.kind_of(href))
        fm = QFontMetricsF(self._effective_font(text))
        gap = self._icon_px(fm) + 4.0 - fm.horizontalAdvance(LINK_SPACER)
        spacer.setFontLetterSpacingType(QFont.AbsoluteSpacing)
        spacer.setFontLetterSpacing(float(max(0, round(gap))))
        return spacer, text

    def has_links(self) -> bool:
        return self._has_links

    def insert_link(self, text: str, href: str) -> None:
        """커서 자리에(선택이 있으면 그 자리를 바꿔) 링크를 넣는다."""
        label = " ".join((text or "").split()) or fl.display_name(href)
        cur = self.textCursor()
        base = self._plain_format(cur.charFormat())
        spacer_fmt, text_fmt = self._link_formats(href, base)
        cur.beginEditBlock()
        try:
            if cur.hasSelection():
                cur.removeSelectedText()
            cur.insertText(LINK_SPACER, spacer_fmt)
            cur.insertText(label, text_fmt)
        finally:
            cur.endEditBlock()
        self.setTextCursor(cur)
        # 이어서 치는 글자는 링크에 딸려 가지 않게 한다.
        self.setCurrentCharFormat(base)
        self._has_links = True
        self.viewport().update()

    def insert_file_links(self, paths: list[str]) -> None:
        """끌어다 놓거나 붙여 넣은 파일·폴더를 링크로 넣는다.

        네트워크 드라이브(Z:\\…)의 파일은 드라이브 글자가 PC 마다 달라
        ``\\\\서버\\공유`` 경로로 바꿔 건다. 이 PC 의 공유 폴더 안에 있는
        파일은 바꾸지 않는다. 공유 폴더 안이라고 남이 열 수 있다는 보장이 없고
        (Windows 기본 Users 공유가 그렇다), 서버 서비스가 꺼져 있으면 넣은 사람도
        못 열게 된다. 그 변환은 링크 창에서 사용자가 직접 고른다."""
        import os
        cur = self.textCursor()
        cur.beginEditBlock()
        try:
            first = True
            for raw in paths:
                path = os.path.normpath(raw)
                try:
                    is_dir = os.path.isdir(path)
                except OSError:
                    is_dir = False
                target = fl.mapped_drive_unc(path) or path
                href = fl.path_to_href(target, is_dir=is_dir)
                if not first:
                    self.textCursor().insertText(" ")
                self.insert_link(fl.display_name(href), href)
                first = False
        finally:
            cur.endEditBlock()

    def _link_runs(self, block) -> list[tuple[int, int, str]]:
        """블록 안의 링크 구간 ``(시작, 끝, href)``. 아이콘 자리 글자를 포함한다."""
        runs: list[tuple[int, int, str]] = []
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            it += 1
            if not frag.isValid():
                continue
            fmt = frag.charFormat()
            href = fmt.anchorHref() if fmt.isAnchor() else ""
            if not href:
                continue
            start, end = frag.position(), frag.position() + frag.length()
            if runs and runs[-1][1] == start and runs[-1][2] == href:
                runs[-1] = (runs[-1][0], end, href)
            else:
                runs.append((start, end, href))
        return runs

    def link_at(self, position: int) -> tuple[int, int, str] | None:
        """문서 위치 ``position`` 이 닿아 있는 링크 구간."""
        block = self.document().findBlock(position)
        if not block.isValid():
            return None
        for start, end, href in self._link_runs(block):
            if start <= position <= end:
                return start, end, href
        return None

    def link_at_cursor(self) -> tuple[int, int, str] | None:
        cur = self.textCursor()
        if cur.hasSelection():
            hit = self.link_at(cur.selectionStart() + 1)
            if hit is not None and cur.selectionEnd() <= hit[1]:
                return hit
            return None
        return self.link_at(cur.position())

    def link_text(self, start: int, end: int) -> str:
        cur = QTextCursor(self.document())
        cur.setPosition(start)
        cur.setPosition(end, QTextCursor.KeepAnchor)
        return cur.selectedText().replace(LINK_SPACER, " ").strip()

    def replace_link(self, start: int, end: int, text: str, href: str) -> None:
        cur = self.textCursor()
        cur.beginEditBlock()
        try:
            cur.setPosition(start)
            cur.setPosition(end, QTextCursor.KeepAnchor)
            self.setTextCursor(cur)
            self.insert_link(text, href)
        finally:
            cur.endEditBlock()

    def remove_link(self, start: int, end: int) -> None:
        """링크만 풀고 글자는 남긴다."""
        doc = self.document()
        spots: list[tuple[int, int, QTextCharFormat]] = []
        block = doc.findBlock(start)
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            it += 1
            if frag.isValid() and start <= frag.position() < end:
                spots.append((frag.position(), frag.length(), frag.charFormat()))
        cur = QTextCursor(doc)
        cur.beginEditBlock()
        try:
            for pos, length, fmt in reversed(spots):
                cur.setPosition(pos)
                cur.setPosition(pos + length, QTextCursor.KeepAnchor)
                if fmt.hasProperty(LINK_ICON_PROP):
                    cur.removeSelectedText()
                else:
                    cur.setCharFormat(self._plain_format(fmt))
        finally:
            cur.endEditBlock()
        self.sync_link_icons(join_previous=True)

    def set_marker_tone(self, light: bool) -> None:
        """메모 바탕이 어두우면 ``light=True`` 로 글머리·체크박스를 밝게 그린다."""
        light = bool(light)
        if light != self._marker_light:
            self._marker_light = light
            self.viewport().update()
        self._done.set_light(light)

    # ── 서식 복사 ────────────────────────────────────────────────────────

    def format_at(self, cursor: QTextCursor) -> CopiedFormat:
        """그 자리 글자의 서식과 그 줄의 글머리 기호. 고른 글이 있으면 그 첫 글자,
        없으면 커서 바로 뒤의 글자를 본다. 링크의 밑줄과 색은 링크의 것이라
        서식으로 치지 않는다."""
        doc = self.document()
        probe = QTextCursor(cursor)
        if probe.hasSelection():
            start, end = probe.selectionStart(), probe.selectionEnd()
            probe.setPosition(min(start + 1, end))
        else:
            block = probe.block()
            if probe.position() < block.position() + block.length() - 1:
                probe.setPosition(probe.position() + 1)
        fmt = probe.charFormat()
        if fmt.isImageFormat() or fmt.hasProperty(LINK_ICON_PROP):
            fmt = QTextCharFormat()
        link = fmt.isAnchor()
        ink = fmt.foreground()
        mark = fmt.background()
        block = doc.findBlock(probe.position())
        code = block.blockFormat().intProperty(MARKER_PROP)
        if code == MARKER_CHECK1:
            code = MARKER_CHECK0            # 끝낸 표시는 서식이 아니다
        return CopiedFormat(
            bold=fmt.fontWeight() > QFont.Normal,
            italic=fmt.fontItalic(),
            underline=fmt.fontUnderline() and not link,
            strike=fmt.fontStrikeOut(),
            size=float(fmt.fontPointSize() or 0.0),
            ink=(ink.color().name() if not link and ink.style() != Qt.NoBrush
                 and fmt.hasProperty(QTextFormat.ForegroundBrush) else ""),
            highlight=(mark.color().name() if mark.style() != Qt.NoBrush
                       and mark.color().alpha() > 0 else ""),
            marker=code if code in MARKER_CODES else MARKER_NONE,
            level=self.block_level(block))

    @staticmethod
    def _dressed(fmt: QTextCharFormat, clip: CopiedFormat) -> QTextCharFormat:
        """``fmt`` 의 글자 서식을 떠 둔 서식으로 바꾼 것. 링크는 링크로 남는다."""
        out = QTextCharFormat(fmt)
        out.setFontWeight(QFont.Bold if clip.bold else QFont.Normal)
        out.setFontItalic(clip.italic)
        out.setFontStrikeOut(clip.strike)
        if clip.size > 0:
            out.setFontPointSize(clip.size)
        else:
            out.clearProperty(QTextFormat.FontPointSize)
            out.clearProperty(QTextFormat.FontPixelSize)
        if clip.highlight:
            out.setBackground(QBrush(QColor(clip.highlight)))
        else:
            out.clearBackground()
        if not fmt.isAnchor():
            out.setFontUnderline(clip.underline)
            if clip.ink:
                out.setForeground(QBrush(QColor(clip.ink)))
            else:
                out.clearForeground()
        return out

    def paste_format(self, clip: CopiedFormat, cursor: QTextCursor) -> None:
        """떠 둔 서식을 고른 글에 입힌다. 고른 글이 없으면 커서가 놓인 낱말에
        입힌다. 글머리 기호는 떠 둔 줄이 목록이었을 때만 그 줄들에 입힌다(목록이
        아닌 줄에서 뜬 서식이 있던 글머리 기호를 지우지 않는다). 되돌리기 한 번에
        함께 돌아간다."""
        doc = self.document()
        target = QTextCursor(cursor)
        if not target.hasSelection():
            target.select(QTextCursor.WordUnderCursor)
        start, end = target.selectionStart(), target.selectionEnd()
        spots: list[tuple[int, int, QTextCharFormat]] = []
        block = doc.findBlock(start)
        while block.isValid() and block.position() <= end:
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                it += 1
                if not frag.isValid():
                    continue
                a = max(start, frag.position())
                b = min(end, frag.position() + frag.length())
                fmt = frag.charFormat()
                if a < b and not fmt.isImageFormat() \
                        and not fmt.hasProperty(LINK_ICON_PROP):
                    spots.append((a, b, fmt))
            block = block.next()
        edit = QTextCursor(doc)
        edit.beginEditBlock()
        try:
            for a, b, fmt in spots:
                edit.setPosition(a)
                edit.setPosition(b, QTextCursor.KeepAnchor)
                edit.setCharFormat(self._dressed(fmt, clip))
            if clip.marker in MARKER_CODES:
                first, last = doc.findBlock(start), doc.findBlock(end)
                positions = []
                block = first
                while block.isValid():
                    positions.append(block.position())
                    if block.position() >= last.position():
                        break
                    block = block.next()
                for position in positions:
                    self.set_block_marker(doc.findBlock(position), clip.marker,
                                          clip.level)
                self.restyle_lists(join_previous=False)
        finally:
            edit.endEditBlock()
        if not spots:
            # 빈 줄이다. 이제부터 치는 글자가 그 서식을 갖게 한다.
            self.setCurrentCharFormat(self._dressed(self.currentCharFormat(), clip))
        self.viewport().update()

    def ensure_readable(self, note_bg: QColor, default_fg: QColor,
                        join_previous: bool = False) -> bool:
        """놓인 바탕에서 사실상 보이지 않는 글자의 색을 읽히는 색으로 바꾼다.

        어두운 메모는 글자가 희고 형광펜은 밝은 색이라 형광펜을 칠한 글자가
        묻힌다(1.1~1.6:1). 메모 색을 어둡게 바꿨을 때 검게 지정해 둔 글자도
        같다. 형광펜이 있거나 글자색을 따로 지정한 구간만 보고, 바꾼 것이
        있으면 True. 링크는 sync_link_icons 가 색을 맡으므로 건드리지 않는다."""
        doc = self.document()
        dark, white = QColor("#1F1F1F"), QColor("#FFFFFF")
        fixes: list[tuple[int, int, QColor]] = []
        block = doc.begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                it += 1
                if not frag.isValid() or frag.length() <= 0:
                    continue
                fmt = frag.charFormat()
                if (fmt.isImageFormat() or fmt.isAnchor()
                        or fmt.hasProperty(LINK_ICON_PROP)):
                    continue
                brush = fmt.background()
                marked = (brush.style() != Qt.NoBrush
                          and brush.color().alpha() > 0)
                # 글자 색을 '기본 색'으로 돌려 둔 것은 색을 정한 것이 아니다.
                explicit = (fmt.hasProperty(QTextFormat.ForegroundBrush)
                            and fmt.foreground().style() != Qt.NoBrush)
                if not (marked or explicit):
                    continue
                bg = brush.color() if marked else note_bg
                fg = fmt.foreground().color() if explicit else default_fg
                if contrast_ratio(fg, bg) >= MIN_READABLE_CONTRAST:
                    continue
                if explicit:
                    # 색을 정해 둔 글자는 같은 색조에서 읽히는 밝기로 옮긴다.
                    better = font_colors.rescue(fg, bg)
                else:
                    better = (dark if contrast_ratio(dark, bg)
                              >= contrast_ratio(white, bg) else white)
                fixes.append((frag.position(), frag.length(), better))
            block = block.next()
        if not fixes:
            return False
        cur = QTextCursor(doc)
        if join_previous:
            cur.joinPreviousEditBlock()
        else:
            cur.beginEditBlock()
        try:
            for pos, length, color in fixes:
                cur.setPosition(pos)
                cur.setPosition(pos + length, QTextCursor.KeepAnchor)
                fmt = QTextCharFormat()
                fmt.setForeground(color)
                cur.mergeCharFormat(fmt)
        finally:
            cur.endEditBlock()
        return True

    def set_link_color(self, color: QColor) -> None:
        """메모 색이 바뀌면 링크 글자색도 그 바탕에서 읽히는 색으로 맞춘다."""
        color = QColor(color)
        if color == self._link_color:
            return
        self._link_color = color
        self.sync_link_icons()

    def sync_link_icons(self, join_previous: bool = False) -> bool:
        """링크마다 아이콘 자리 글자가 정확히 하나, 맨 앞에 있도록 맞추고 링크
        글자의 밑줄과 색을 지금 메모 색에 맞춘다. 바꾼 것이 있으면 True.

        ``join_previous`` 이면 바꾼 내용을 직전 편집과 한 묶음으로 넣어,
        되돌리기 한 번에 함께 돌아가게 한다(따로 두면 되돌릴 때마다 다시
        맞춰 넣어 되돌리기가 그 자리에서 맴돈다)."""
        if self._link_syncing:
            return False
        doc = self.document()
        ops: list[tuple[int, int, str, int, QTextCharFormat | None]] = []
        any_link = False
        block = doc.begin()
        while block.isValid():
            frags = []
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                it += 1
                if frag.isValid():
                    frags.append((frag.position(), frag.length(),
                                  frag.charFormat(), frag.text()))
            i = 0
            while i < len(frags):
                pos, length, fmt, _text = frags[i]
                href = fmt.anchorHref() if fmt.isAnchor() else ""
                if not href:
                    if fmt.hasProperty(LINK_ICON_PROP):
                        # 링크를 잃은 아이콘 자리.
                        ops.append((pos, 0, "del", length, None))
                    i += 1
                    continue
                j = i + 1
                while (j < len(frags) and frags[j][2].isAnchor()
                       and frags[j][2].anchorHref() == href):
                    j += 1
                if self._plan_link_run(frags[i:j], href, ops):
                    any_link = True
                i = j
            block = block.next()
        self._has_links = any_link
        if not ops:
            return False
        # 뒤에서부터 고쳐야 앞쪽 위치가 밀리지 않는다. 같은 자리에서는 끼워
        # 넣기를 마지막에 한다.
        ops.sort(key=lambda op: (-op[0], op[1]))
        self._link_syncing = True
        cur = QTextCursor(doc)
        if join_previous:
            cur.joinPreviousEditBlock()
        else:
            cur.beginEditBlock()
        try:
            for pos, _order, what, length, fmt in ops:
                cur.setPosition(pos)
                if what == "ins":
                    cur.insertText(LINK_SPACER, fmt)
                    continue
                cur.setPosition(pos + length, QTextCursor.KeepAnchor)
                if what == "del":
                    cur.removeSelectedText()
                elif what == "fmt":
                    cur.setCharFormat(fmt)
                elif what == "merge":
                    cur.mergeCharFormat(fmt)
        finally:
            cur.endEditBlock()
            self._link_syncing = False
            self._undo_cmd_added = False
        self.viewport().update()
        return True

    def _plan_link_run(self, run: list, href: str, ops: list) -> bool:
        """링크 한 구간을 바로잡는 데 필요한 손질을 ``ops`` 에 보탠다. 글자가
        남아 있는 링크이면 True."""
        text_frags = [f for f in run if not f[2].hasProperty(LINK_ICON_PROP)]
        icon_frags = [f for f in run if f[2].hasProperty(LINK_ICON_PROP)]
        if not text_frags:
            # 글자를 다 지워 아이콘 자리만 남았다.
            for pos, length, _fmt, _text in icon_frags:
                ops.append((pos, 0, "del", length, None))
            return False
        spacer_fmt, text_fmt = self._link_formats(href, text_frags[0][2])
        first = run[0]
        keep_first = (first[2].hasProperty(LINK_ICON_PROP)
                      and first[3][:1] == LINK_SPACER)
        for frag in icon_frags:
            pos, length, fmt, text = frag
            if frag is first and keep_first:
                if length > 1:
                    if text.strip(LINK_SPACER):
                        ops.append((pos + 1, 0, "fmt", length - 1, text_fmt))
                    else:
                        ops.append((pos + 1, 0, "del", length - 1, None))
                stale = (fmt.property(LINK_ICON_PROP) != fl.kind_of(href)
                         or fmt.fontUnderline()
                         or abs(fmt.fontLetterSpacing()
                                - spacer_fmt.fontLetterSpacing()) > 0.5)
                if stale:
                    ops.append((pos, 0, "fmt", 1, spacer_fmt))
            elif text.strip(LINK_SPACER):
                # 아이콘 자리 서식을 물려받은 글자(자리 바로 뒤에서 친 글자).
                ops.append((pos, 0, "fmt", length, text_fmt))
            else:
                ops.append((pos, 0, "del", length, None))
        if not keep_first:
            ops.append((first[0], 1, "ins", 0, spacer_fmt))
        for pos, length, fmt, _text in text_frags:
            brush = fmt.foreground()
            if (not fmt.fontUnderline() or brush.style() == Qt.NoBrush
                    or brush.color() != self._link_color):
                fix = QTextCharFormat()
                fix.setFontUnderline(True)
                fix.setForeground(QBrush(self._link_color))
                ops.append((pos, 0, "merge", length, fix))
        return True

    def _on_undo_command_added(self) -> None:
        self._undo_cmd_added = True

    def _deferred_link_sync(self) -> None:
        self._link_sync_pending = False
        if not self._undo_cmd_added:
            # 되돌리기·다시 실행이 낸 변화다. 이미 맞춰 둔 상태로 돌아간 것이라
            # 손대지 않는다.
            return
        self._undo_cmd_added = False
        self.sync_link_icons(join_previous=True)
        # 줄이 생기거나 글자 크기가 바뀌면 목록의 여백과 간격도 달라진다.
        self.restyle_lists(join_previous=True)

    def _leave_link_format_at_edges(self) -> None:
        """링크 끝에서 이어 치는 글자가 링크에 딸려 가지 않게 하고, 아이콘 자리
        바로 뒤에서 치는 글자는 링크 글자 서식을 쓰게 한다."""
        if self.isReadOnly() or self._link_syncing:
            return
        cur = self.textCursor()
        if cur.hasSelection():
            return
        fmt = cur.charFormat()
        if not fmt.isAnchor():
            return
        href = fmt.anchorHref()
        if fmt.hasProperty(LINK_ICON_PROP):
            self.setCurrentCharFormat(self._link_formats(href, fmt)[1])
            return
        pos = cur.position()
        block = cur.block()
        at_block_end = pos >= block.position() + block.length() - 1
        if not at_block_end:
            nxt = QTextCursor(self.document())
            nxt.setPosition(pos + 1)
            after = nxt.charFormat()
            if after.isAnchor() and after.anchorHref() == href:
                return
        self.setCurrentCharFormat(self._plain_format(fmt))

    def _paint_link_icons(self, painter: QPainter | None) -> QPainter | None:
        doc = self.document()
        dpr = self.devicePixelRatioF()
        view = self.viewport().rect()
        block = doc.begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                it += 1
                if not frag.isValid():
                    continue
                fmt = frag.charFormat()
                if not fmt.hasProperty(LINK_ICON_PROP):
                    continue
                cur = QTextCursor(doc)
                cur.setPosition(frag.position())
                r0 = self.cursorRect(cur)
                if r0.bottom() < view.top() or r0.top() > view.bottom():
                    continue
                cur.setPosition(frag.position() + 1)
                r1 = self.cursorRect(cur)
                fm = QFontMetricsF(self._effective_font(fmt))
                size = self._icon_px(fm)
                slot = float(r1.left() - r0.left())
                if r1.top() != r0.top() or slot <= 0:
                    slot = size + 4.0
                size = max(8.0, min(size, slot - 2.0))
                x = r0.left() + (slot - size) / 2.0 - 1.0
                y = r0.top() + (r0.height() - size) / 2.0
                if painter is None:
                    painter = QPainter(self.viewport())
                    painter.setRenderHint(QPainter.SmoothPixmapTransform)
                kind = str(fmt.property(LINK_ICON_PROP) or fl.KIND_FILE)
                painter.drawPixmap(QPointF(x, y),
                                   link_icon_pixmap(kind, size, dpr))
            block = block.next()
        return painter

    # ── 목록: 단계, 번호, 여백과 간격 ────────────────────────────────────

    def _em_of(self, fmt: QTextCharFormat) -> float:
        """이 서식의 글자 크기(px)."""
        pt = fmt.fontPointSize()
        if pt <= 0:
            pt = self.document().defaultFont().pointSizeF()
        if pt <= 0:
            return float(QFontInfo(self.document().defaultFont()).pixelSize())
        return pt * self.logicalDpiY() / 72.0

    def base_em(self) -> float:
        return self._em_of(QTextCharFormat())

    def _lead_format(self, block) -> QTextCharFormat:
        """그 줄의 첫 글자 서식(그림과 링크 아이콘 자리는 건너뛴다)."""
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            it += 1
            if not frag.isValid():
                continue
            fmt = frag.charFormat()
            if not fmt.isImageFormat() and not fmt.hasProperty(LINK_ICON_PROP):
                return fmt
        return block.charFormat()

    def block_em(self, block) -> float:
        return self._em_of(self._lead_format(block))

    @staticmethod
    def block_marker(block) -> int:
        return block.blockFormat().intProperty(MARKER_PROP)

    @staticmethod
    def block_level(block) -> int:
        bf = block.blockFormat()
        if bf.intProperty(MARKER_PROP) not in MARKER_CODES:
            return 0
        return max(0, min(MAX_LEVEL, bf.intProperty(LEVEL_PROP)))

    def _number_layout(self, doc: QTextDocument | None = None
                       ) -> tuple[dict[int, int], dict[int, int]]:
        """번호 목록의 줄마다 ``(번호, 그 줄이 든 목록에서 가장 긴 번호의 자릿수)``.

        같은 단계에서 이어지는 번호 줄이 한 목록이다. 더 깊은 줄은 끊지 않고,
        글 줄이나 같은 단계의 다른 기호가 끼면 다음 줄은 1 부터 다시 센다."""
        doc = doc or self.document()
        numbers: dict[int, int] = {}
        runs: dict[int, list[int]] = {}         # 단계 → 지금 이어지는 목록의 블록들
        digits: dict[int, int] = {}

        def close(level: int) -> None:
            run = runs.pop(level, None)
            if run:
                width = len(str(numbers[run[-1]]))
                for number in run:
                    digits[number] = width

        block = doc.begin()
        while block.isValid():
            bf = block.blockFormat()
            code = bf.intProperty(MARKER_PROP)
            if code not in MARKER_CODES:
                for level in list(runs):
                    close(level)
            else:
                level = max(0, min(MAX_LEVEL, bf.intProperty(LEVEL_PROP)))
                for deeper in [k for k in runs if k > level]:
                    close(deeper)
                if code == MARKER_NUMBER:
                    run = runs.setdefault(level, [])
                    numbers[block.blockNumber()] = len(run) + 1
                    run.append(block.blockNumber())
                else:
                    close(level)
            block = block.next()
        for level in list(runs):
            close(level)
        return numbers, digits

    def list_numbers(self, doc: QTextDocument | None = None) -> dict[int, int]:
        """번호 목록의 줄 번호(블록 번호 → 1, 2, 3 …)."""
        return self._number_layout(doc)[0]

    def next_number_for(self, block) -> int:
        """이 줄(지금은 글 줄)을 번호 줄로 바꾸면 받게 될 번호."""
        numbers = self.list_numbers()
        prev = block.previous()
        while prev.isValid():
            code = self.block_marker(prev)
            if code not in MARKER_CODES:
                return 1
            if self.block_level(prev) == 0:
                return numbers[prev.blockNumber()] + 1 if code == MARKER_NUMBER else 1
            prev = prev.previous()
        return 1

    def _list_margin(self, block, code: int, level: int, digits: int) -> float:
        """목록 줄의 왼쪽 여백: 기호 칸 + 단계마다 글자 반 칸."""
        fmt = self._lead_format(block)
        em = self._em_of(fmt)
        column = marker_column(em)
        if code == MARKER_NUMBER and digits > 1:
            fm = QFontMetricsF(self._effective_font(self._plain_format(fmt)))
            label = fm.horizontalAdvance("0") * digits + fm.horizontalAdvance(".")
            column = max(column, float(round(label + em * 0.58)))
        return column + level * LEVEL_STEP * em

    @staticmethod
    def _block_kind(block, code: int) -> tuple[str, bool]:
        """간격 규칙에서 보는 줄의 종류 ``(text | list | image, 빈 줄인가)``."""
        if code in MARKER_CODES:
            return "list", False
        has_image = has_text = False
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            it += 1
            if not frag.isValid():
                continue
            fmt = frag.charFormat()
            if fmt.isImageFormat():
                has_image = True
            elif frag.text().replace(LINK_SPACER, "").strip():
                has_text = True
        if has_image and not has_text:
            return "image", False
        return "text", not (has_image or has_text)

    @staticmethod
    def _gap_before(prev: tuple[str, bool, int] | None,
                    cur: tuple[str, bool, int]) -> float:
        if prev is None:
            return 0.0
        (pk, p_empty, p_level), (ck, c_empty, c_level) = prev, cur
        if p_empty or c_empty:
            return 0.0                      # 빈 줄로 이미 띄워 놓았다
        if pk == "image" or ck == "image":
            return GAP_IMAGE
        if ck == "list":
            if pk != "list":
                return GAP_LIST_START
            return GAP_GROUP if (c_level == 0 and p_level > 0) else GAP_ITEM
        return GAP_LIST_END if pk == "list" else 0.0

    def restyle_lists(self, join_previous: bool = False) -> bool:
        """목록 줄의 왼쪽 여백과 서식 사이의 위 간격을 지금 내용에 맞춘다.
        바꾼 것이 있으면 True.

        둘 다 화면에만 쓰는 값이다(저장할 때 뺀다). 위 간격은 이 편집기가 넣은
        줄(``SPACING_PROP``)만 다시 잰다. 붙여 넣은 글이 원래 가진 여백은 두고,
        그런 줄에는 간격을 더하지 않는다."""
        if self._restyling:
            return False
        doc = self.document()
        base_em = self.base_em()
        _numbers, digits = self._number_layout()
        ops: list[tuple[int, QTextBlockFormat]] = []
        prev: tuple[str, bool, int] | None = None
        block = doc.begin()
        while block.isValid():
            bf = block.blockFormat()
            code = bf.intProperty(MARKER_PROP)
            level = self.block_level(block)
            kind, empty = self._block_kind(block, code)
            merge = QTextBlockFormat()
            dirty = False
            if code in MARKER_CODES:
                want = self._list_margin(block, code, level,
                                         digits.get(block.blockNumber(), 1))
                if abs(bf.leftMargin() - want) > 0.5:
                    merge.setLeftMargin(want)
                    dirty = True
            cur = (kind, empty, level)
            gap = float(round(self._gap_before(prev, cur) * base_em))
            managed = bf.hasProperty(SPACING_PROP)
            if managed or (gap > 0 and bf.topMargin() == 0):
                if (not managed or abs(bf.topMargin() - gap) > 0.5
                        or abs(bf.doubleProperty(SPACING_PROP) - gap) > 0.5):
                    merge.setTopMargin(gap)
                    merge.setProperty(SPACING_PROP, gap)
                    dirty = True
            if dirty:
                ops.append((block.position(), merge))
            prev = cur
            block = block.next()
        if not ops:
            return False
        self._restyling = True
        cur = QTextCursor(doc)
        if join_previous:
            cur.joinPreviousEditBlock()
        else:
            cur.beginEditBlock()
        try:
            for pos, merge in ops:
                cur.setPosition(pos)
                cur.mergeBlockFormat(merge)
        finally:
            cur.endEditBlock()
            self._restyling = False
            self._undo_cmd_added = False
        self.viewport().update()
        return True

    def set_block_marker(self, block, code: int, level: int | None = None) -> None:
        """줄의 목록 종류(와 단계)를 정한다. 목록이 아니게 하면 여백과 단계를 지운다.
        여백은 ``restyle_lists`` 가 잰다."""
        if not block.isValid():
            return
        bf = QTextBlockFormat()
        if code in MARKER_CODES:
            bf.setProperty(MARKER_PROP, code)
            if level is not None:
                bf.setProperty(LEVEL_PROP, max(0, min(MAX_LEVEL, int(level))))
        else:
            bf.setProperty(MARKER_PROP, MARKER_NONE)
            bf.setProperty(LEVEL_PROP, 0)
            bf.setLeftMargin(0.0)
        QTextCursor(block).mergeBlockFormat(bf)
        self._done.rehighlightBlock(block)

    # ── 주소를 링크로 ────────────────────────────────────────────────────

    def _linkify_word(self, start: int, word: str) -> bool:
        """문서 위치 ``start`` 에서 시작하는 낱말이 주소면 링크로 바꾼다."""
        hit = auto_link_target(word)
        if hit is None:
            return False
        text, href = hit
        probe = QTextCursor(self.document())
        probe.setPosition(start + 1)
        if probe.charFormat().isAnchor():
            return False                    # 이미 링크다
        self.replace_link(start, start + len(text), text, href)
        return True

    def linkify_before_cursor(self) -> bool:
        """커서 바로 앞의 낱말이 웹 주소나 file: 주소면 링크로 바꾼다
        (빈칸이나 Enter 를 치기 직전에 부른다). 커서는 제자리에 둔다."""
        cur = self.textCursor()
        if cur.hasSelection():
            return False
        block = cur.block()
        head = block.text()[:cur.position() - block.position()]
        words = list(_WORD.finditer(head))
        if not words or words[-1].end() != len(head):
            return False
        word = words[-1]
        if not self._linkify_word(block.position() + word.start(), word.group()):
            return False
        # 링크 앞에 아이콘 자리 글자 하나가 들어갔다.
        cur = self.textCursor()
        cur.setPosition(block.position() + len(head) + 1)
        self.setTextCursor(cur)
        return True

    def linkify_range(self, start: int, end: int) -> int:
        """``start``~``end`` 에 든 주소를 링크로 바꾸고 바꾼 개수를 돌려준다
        (붙여 넣은 글에 쓴다)."""
        doc = self.document()
        found: list[tuple[int, str]] = []
        block = doc.findBlock(start)
        while block.isValid() and block.position() < end:
            for word in _WORD.finditer(block.text()):
                pos = block.position() + word.start()
                if start <= pos and pos + len(word.group()) <= end:
                    found.append((pos, word.group()))
            block = block.next()
        count = 0
        for pos, word in reversed(found):   # 뒤에서부터 바꿔야 앞의 자리가 안 밀린다
            if self._linkify_word(pos, word):
                count += 1
        return count

    def setHtml(self, html: str) -> None:  # noqa: N802
        super().setHtml(html)
        # setHtml 이 리소스 캐시를 비우므로 글머리 PNG 를 다시 등록한다.
        register_bullet_resources(self.document())
        # 표시용 리소스도 함께 사라졌으므로 등록 기록을 지운다.
        self._display_res.clear()
        # setHtml 은 data: 이미지를 리소스로 디코드하지 않아, 디코드를 미리
        # 해 두지 않으면 첫 paint 전까지 빈 칸으로 보인다.
        self._restore_image_resources()
        self._register_animated_in_document()

    def _restore_image_resources(self) -> None:
        """setHtml 뒤 첫 paint 전에 이미지 리소스를 채운다.

        본문 이미지는 저장 HTML 에 적힌 표시 크기(width/height 속성)로 줄인
        사본을 넣는다. 아직 표시 전이라 뷰포트 폭을 모르지만 저장된 표시 크기가
        곧 마지막 표시 크기라 대체로 그대로 쓰이고, 표시되면 resizeEvent 의
        fit 이 필요할 때만 다시 맞춘다."""
        doc = self.document()
        if doc.isEmpty():
            return
        block = doc.begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                if frag.isValid() and frag.charFormat().isImageFormat():
                    img_fmt = frag.charFormat().toImageFormat()
                    name = img_fmt.name()
                    if name.startswith("data:"):
                        if is_marker_url(name):
                            self._restore_marker_resource(doc, name)
                        else:
                            meta = self._image_meta(name)
                            if meta is not None:
                                w, h = img_fmt.width(), img_fmt.height()
                                if w <= 0 or h <= 0:
                                    w, h = meta[0], meta[1]
                                self._set_display_resource(
                                    name, int(round(w)), int(round(h)))
                it += 1
            block = block.next()

    def _restore_marker_resource(self, doc: QTextDocument, name: str) -> None:
        existing = doc.resource(QTextDocument.ImageResource, QUrl(name))
        if isinstance(existing, QImage) and not existing.isNull():
            return
        ba = _data_url_payload(name)
        if ba.isEmpty():
            return
        img = QImage()
        img.loadFromData(ba)
        if not img.isNull():
            doc.addResource(QTextDocument.ImageResource, QUrl(name), img)

    def _image_meta(self, data_url: str) -> tuple[int, int, bool] | None:
        """``(원본 폭, 원본 높이, 애니메이션 여부)``. 헤더만 읽어 캐시한다."""
        cached = self._img_meta.get(data_url)
        if cached is not None:
            return cached
        ba = _data_url_payload(data_url)
        if ba.isEmpty():
            return None
        buf = QBuffer(ba)
        buf.open(QIODevice.ReadOnly)
        reader = QImageReader()
        reader.setDecideFormatFromContent(True)
        reader.setDevice(buf)
        try:
            if not reader.canRead():
                return None
            size = reader.size()
            animated = reader.supportsAnimation() and reader.imageCount() > 1
        finally:
            buf.close()
        if not size.isValid() or size.width() <= 0 or size.height() <= 0:
            # 크기를 헤더에서 못 주는 형식이면 한 번 디코드한다.
            img = QImage()
            img.loadFromData(ba)
            if img.isNull():
                return None
            size = img.size()
        meta = (size.width(), size.height(), bool(animated))
        self._img_meta[data_url] = meta
        return meta

    def _full_image(self, data_url: str) -> QImage | None:
        img = self._full_cache.get(data_url)
        if img is None:
            img = data_url_image(data_url)
            if img.isNull():
                return None
            self._full_cache[data_url] = img
        self._full_cache_timer.start()
        return img

    def _set_display_resource(self, data_url: str, w: int, h: int) -> None:
        """``data_url`` 이미지를 논리 크기 ``w x h`` 로 그릴 때 쓸 리소스를
        등록한다. 원본보다 키우지 않고, 고해상도 화면에서는 배율만큼 더 큰
        사본을 둔다. 애니메이션은 드라이버가 프레임을 넣으므로 건드리지 않는다."""
        meta = self._image_meta(data_url)
        if meta is None or w <= 0 or h <= 0:
            return
        ow, oh, animated = meta
        if animated:
            return
        dpr = max(1.0, float(self.devicePixelRatioF()))
        tw = max(1, min(ow, int(round(w * dpr))))
        th = max(1, min(oh, int(round(h * dpr))))
        if self._display_res.get(data_url) == (tw, th):
            return
        full = self._full_image(data_url)
        if full is None:
            return
        if (tw, th) == (ow, oh):
            img = full
        else:
            img = full.scaled(tw, th, Qt.IgnoreAspectRatio,
                              Qt.SmoothTransformation)
        self.document().addResource(
            QTextDocument.ImageResource, QUrl(data_url), img)
        self._display_res[data_url] = (tw, th)

    def _register_animated_in_document(self) -> None:
        doc = self.document()
        if doc.isEmpty():
            return
        current_urls: set[str] = set()
        block = doc.begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                if frag.isValid():
                    fmt = frag.charFormat()
                    if fmt.isImageFormat():
                        url = fmt.toImageFormat().name()
                        current_urls.add(url)
                        if is_animated_data_url(url):
                            self._anim.register(url)
                it += 1
            block = block.next()
        self._anim.unregister_missing(current_urls)

    def content_image_urls(self) -> list[str]:
        """본문 이미지(글머리·체크박스 마커 제외)의 URL 을 문서 순서대로 돌려준다.

        같은 이미지를 나란히 두 번 넣으면 Qt 가 한 조각으로 합치므로, 조각
        하나가 아니라 조각 안의 글자 수만큼 센다."""
        urls: list[str] = []
        doc = self.document()
        block = doc.begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                if frag.isValid():
                    fmt = frag.charFormat()
                    if fmt.isImageFormat():
                        name = fmt.toImageFormat().name()
                        if name and not is_marker_url(name):
                            urls.extend([name] * max(1, frag.length()))
                it += 1
            block = block.next()
        return urls

    def content_image_at(self, pos) -> tuple[str, int] | None:
        """뷰포트 좌표 ``pos`` 위에 그려진 본문 이미지를 찾아 ``(URL, 본문
        이미지 순번)`` 을 돌려준다. 이미지가 아니면 None.

        ``cursorForPosition`` 은 클릭 지점에서 가장 가까운 문서 위치를 주므로
        이미지 왼쪽 절반을 누르면 이미지 앞, 오른쪽 절반이면 뒤 위치가 온다.
        두 이웃 글자를 모두 살피되, 그림이 실제로 차지하는 사각형 안일 때만
        맞은 것으로 본다(이미지 옆 글자를 더블 클릭한 경우를 걸러 낸다)."""
        doc = self.document()
        if doc.isEmpty():
            return None
        anchor = self.cursorForPosition(pos).position()
        for start in (anchor - 1, anchor):
            if start < 0:
                continue
            block = doc.findBlock(start)
            if not block.isValid():
                continue
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                it += 1
                if not frag.isValid() or not frag.contains(start):
                    continue
                fmt = frag.charFormat()
                if not fmt.isImageFormat():
                    break
                img_fmt = fmt.toImageFormat()
                name = img_fmt.name()
                if not name or is_marker_url(name):
                    break
                rect = self._image_rect_at(start, img_fmt)
                if rect is not None and rect.contains(pos):
                    return name, self._image_ordinal(start)
                break
        return None

    def _image_rect_at(self, position: int, img_fmt: QTextImageFormat):
        """문서 위치 ``position`` 의 이미지 한 장이 뷰포트에서 차지하는 사각형."""
        width = img_fmt.width()
        if width <= 0:
            meta = self._image_meta(img_fmt.name())
            if meta is not None:
                width = float(meta[0])
        if width <= 0:
            return None
        cur = QTextCursor(self.document())
        cur.setPosition(position)
        line = self.cursorRect(cur)
        return QRect(line.left(), line.top(),
                     max(1, int(round(width))), max(1, line.height()))

    def _image_ordinal(self, position: int) -> int:
        """``position`` 의 이미지가 본문 이미지 목록에서 몇 번째인지(0 기준)."""
        ordinal = 0
        doc = self.document()
        block = doc.begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                it += 1
                if not frag.isValid():
                    continue
                fmt = frag.charFormat()
                if not fmt.isImageFormat():
                    continue
                name = fmt.toImageFormat().name()
                if not name or is_marker_url(name):
                    continue
                if frag.contains(position):
                    return ordinal + (position - frag.position())
                ordinal += max(1, frag.length())
            block = block.next()
        return 0

    def _available_image_width(self) -> int:
        ml, _, mr, _ = config.TEXT_MARGINS
        return max(60, self.viewport().width() - ml - mr - 4)

    def fit_images_to_viewport(self) -> None:
        """``beginEditBlock`` 으로 묶어 기존 undo 이력을 보존한다. 스택을
        비우는 ``setUndoRedoEnabled(False)`` 방식과 의도적으로 다르다."""
        doc = self.document()
        if doc.isEmpty():
            return
        avail = self._available_image_width()
        changes: list[tuple[int, int, QTextImageFormat]] = []
        block = doc.begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                if frag.isValid():
                    fmt = frag.charFormat()
                    if fmt.isImageFormat():
                        img_fmt = fmt.toImageFormat()
                        target = self._fit_target(img_fmt, avail)
                        if target is not None:
                            new_w, new_h = target
                            # 표시 크기가 그대로여도 리소스가 아직 원본이면
                            # (방금 삽입) 표시용 사본으로 바꾼다.
                            self._set_display_resource(
                                img_fmt.name(), new_w, new_h)
                            new_fmt = self._fit_image_format(
                                img_fmt, new_w, new_h)
                            if new_fmt is not None:
                                changes.append(
                                    (frag.position(), frag.length(), new_fmt))
                it += 1
            block = block.next()
        if not changes:
            return
        cur = QTextCursor(doc)
        cur.beginEditBlock()
        try:
            for pos, length, new_fmt in changes:
                cur.setPosition(pos)
                cur.setPosition(pos + length, QTextCursor.KeepAnchor)
                cur.setCharFormat(new_fmt)
        finally:
            cur.endEditBlock()

    def _fit_target(self, img_fmt: QTextImageFormat,
                    avail: int) -> tuple[int, int] | None:
        """뷰포트 폭 ``avail`` 에 맞춘 표시 크기. 원본보다 키우지 않는다."""
        name = img_fmt.name()
        # 마커 아이콘은 본래 캔버스 크기를 유지해야 하며 사진처럼 뷰포트에
        # 맞춰 스케일되면 안 된다.
        if not name or is_marker_url(name):
            return None
        meta = self._image_meta(name)
        if meta is None:
            return None
        orig_w, orig_h, _animated = meta
        if orig_w <= 0:
            return None
        new_w = min(orig_w, avail)
        scale = new_w / orig_w
        new_h = max(1, int(round(orig_h * scale)))
        return new_w, new_h

    @staticmethod
    def _fit_image_format(img_fmt: QTextImageFormat, new_w: int,
                          new_h: int) -> QTextImageFormat | None:
        if (abs(img_fmt.width() - new_w) < 0.5
                and abs(img_fmt.height() - new_h) < 0.5):
            return None
        out = QTextImageFormat(img_fmt)
        out.setWidth(float(new_w))
        out.setHeight(float(new_h))
        return out

    def _on_contents_change(self, position: int, removed: int, added: int) -> None:
        if (not self._link_syncing and not self._restyling
                and not self._link_sync_pending
                and (self._has_links or added > 0 or removed > 0)):
            self._link_sync_pending = True
            QTimer.singleShot(0, self._deferred_link_sync)
        if removed > 0 and self._anim.known_urls():
            current: set[str] = set()
            doc = self.document()
            block = doc.begin()
            while block.isValid():
                it = block.begin()
                while not it.atEnd():
                    frag = it.fragment()
                    if frag.isValid() and frag.charFormat().isImageFormat():
                        current.add(frag.charFormat().toImageFormat().name())
                    it += 1
                block = block.next()
            self._anim.unregister_missing(current)
        if added <= 0:
            return
        doc = self.document()
        cur = QTextCursor(doc)
        cur.setPosition(position)
        cur.setPosition(position + added, QTextCursor.KeepAnchor)
        fmt = cur.charFormat()
        # 표시 전 setHtml 로 이미지가 들어오면 뷰포트 폭이 작아 과하게
        # 줄어들므로, 표시된 동안의 삽입에서만 맞춘다.
        if ((fmt.isImageFormat() or "￼" in cur.selection().toPlainText())
                and self.isVisible()):
            self.fit_images_to_viewport()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        # 표시 전에는 뷰포트 폭이 확정되지 않아 이미지를 과하게 줄인다.
        # 표시된 뒤의 resize 에서만 맞춘다.
        if self.isVisible():
            self.fit_images_to_viewport()

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        doc = self.document()
        view = self.viewport().rect()
        ink = marker_ink(self._marker_light)
        numbers: dict[int, int] | None = None
        block = doc.begin()
        painter = None
        while block.isValid():
            code = block.blockFormat().intProperty(MARKER_PROP)
            if code in MARKER_CODES:
                cursor = QTextCursor(block)
                cursor.setPosition(block.position())
                r = QRectF(self.cursorRect(cursor))
                if r.bottom() >= view.top() and r.top() <= view.bottom():
                    if painter is None:
                        painter = QPainter(self.viewport())
                    fmt = self._lead_format(block)
                    em = self._em_of(fmt)
                    column = marker_column(em)
                    # 기호는 글자 바로 앞의 칸에, 칸 안에서 조금 왼쪽에 놓는다.
                    cx = r.left() - column + column * 0.42
                    cy = r.top() + r.height() / 2.0
                    if code == MARKER_NUMBER:
                        if numbers is None:
                            numbers = self.list_numbers()
                        painter.setFont(self._effective_font(self._plain_format(fmt)))
                        painter.setPen(ink)
                        right = r.left() - em * 0.36
                        painter.drawText(
                            QRectF(right - em * 4, r.top(), em * 4, r.height()),
                            int(Qt.AlignRight | Qt.AlignVCenter),
                            f"{numbers.get(block.blockNumber(), 1)}.")
                    elif code in CHECK_CODES:
                        draw_checkbox(painter, code == MARKER_CHECK1, cx, cy, em,
                                      self._marker_light)
                    else:
                        draw_marker_shape(
                            painter, marker_shape(code, self.block_level(block)),
                            cx, cy, em, ink)
            block = block.next()
        if self._has_links:
            painter = self._paint_link_icons(painter)
        if painter is not None:
            painter.end()

    def wheelEvent(self, event) -> None:  # noqa: N802
        if self.isReadOnly():
            super().wheelEvent(event)
            return
        if event.modifiers() & Qt.ControlModifier:
            delta = event.angleDelta().y()
            if delta == 0:
                event.accept()
                return
            current = self.fontPointSize()
            if current <= 0:
                current = self.document().defaultFont().pointSize() or 11
            step = 1 if delta > 0 else -1
            new_size = max(6, min(96, int(round(current)) + step))
            self.setFontPointSize(float(new_size))
            self._show_size_popup(new_size)
            event.accept()
            return
        super().wheelEvent(event)
