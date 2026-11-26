"""메인 창 메모 목록의 카드와 그 배치.

보기 방식은 두 축이다. 배치(목록·격자)는 카드를 한 줄에 몇 장 놓을지를, 크기
(작게·보통·크게)는 카드 한 장에 얼마나 담을지를 정한다.

* 목록: 흰 줄. 왼쪽의 작은 네모가 메모 색이고, 제목과 첫 줄, 오른쪽에 상태 그림과
  고친 때, 고친 사람. 표시·숨기기와 더 보기는 마우스를 올리거나 글쇠로 왔을 때만.
* 격자: 바탕화면의 메모와 같은 종이(메모 색, 머리 줄, 제목과 본문). 메인 창은 모든
  칸을 같은 크기의 네모로 놓는다(``CardFlowLayout.set_square``). 넘치는 내용은 칸
  아래에서 흐려진다. 바탕화면에서 숨긴 메모는 옅게.

카드는 한 번 만들어 두고 보기 방식이 바뀌면 같은 위젯의 줄 수·여백·보이는
칸만 바꾼다(선택 상태와 시그널 연결이 그대로 남는다)."""
from __future__ import annotations

import math
import re
from collections import OrderedDict

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QIcon,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QTextLayout,
    QTextOption,
)
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QMenu,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetItem,
)

from .. import config, datetime_fmt, i18n
from ..settings import (
    VIEW_COMPACT,
    VIEW_DETAILED,
    VIEW_GRID,
    VIEW_LIST,
    VIEW_STANDARD,
)
from .icons import svg_icon
from .rich_text import data_url_image, has_non_checkbox_image, html_to_plain, is_marker_url, is_widget_url, with_widgets_as_text
from .theme import LIGHT_MENU_QSS, text_color_for_bg

# 메인 창 격자 칸 한 변의 기준 길이(작게, 보통, 크게). 칸은 줄 폭을 채우려 이보다
# 조금 크거나 작아지고, 글자도 같은 비율로 커지거나 작아진다.
GRID_CELL = {VIEW_COMPACT: 132, VIEW_STANDARD: 168, VIEW_DETAILED: 212}
# 칸이 기준보다 이만큼 작아지면 한 줄의 칸 수를 줄인다.
GRID_SHRINK = 0.82

# 그리드에서 타일 한 장이 가져야 하는 최소 폭. 창을 넓히면 열이 늘어난다.
GRID_MIN_COLUMN = 148

_IMG_SRC_RE = re.compile(r'<img[^>]*src=["\']([^"\']+)["\']', re.IGNORECASE)
_LINE_SEP = " "

# 자세히 보기의 첫 이미지 축소판. 메모가 갱신될 때마다 큰 data URL 을 다시
# 디코드하지 않도록 최근 것만 기억한다.
_THUMB_CACHE: "OrderedDict[tuple, QPixmap]" = OrderedDict()
_THUMB_CACHE_MAX = 64
_THUMB_SIZE = QSize(84, 64)


def _first_image_url(html: str) -> str:
    if not html or "<img" not in html.lower():
        return ""
    for m in _IMG_SRC_RE.finditer(html):
        if not is_marker_url(m.group(1)) and not is_widget_url(m.group(1)):
            return m.group(1)
    return ""


def _thumbnail(url: str) -> QPixmap | None:
    """첫 이미지를 축소판 칸에 꽉 차게(가운데 잘라) 그린 둥근 그림."""
    if not url:
        return None
    key = (len(url), url[:96], url[-96:])
    hit = _THUMB_CACHE.get(key)
    if hit is not None:
        _THUMB_CACHE.move_to_end(key)
        return hit
    img = data_url_image(url)
    if img.isNull():
        return None
    scale = 2  # 고해상도 화면에서도 뭉개지지 않게 두 배로 만든다.
    tw, th = _THUMB_SIZE.width() * scale, _THUMB_SIZE.height() * scale
    scaled = img.scaled(tw, th, Qt.KeepAspectRatioByExpanding,
                        Qt.SmoothTransformation)
    x = max(0, (scaled.width() - tw) // 2)
    y = max(0, (scaled.height() - th) // 2)
    cropped = scaled.copy(x, y, tw, th)
    pix = QPixmap(tw, th)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, tw, th), 8 * scale, 8 * scale)
    p.setClipPath(path)
    p.drawImage(0, 0, cropped)
    p.setClipping(False)
    p.setPen(QPen(QColor(0, 0, 0, 40), scale))
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)
    p.end()
    pix.setDevicePixelRatio(scale)
    _THUMB_CACHE[key] = pix
    while len(_THUMB_CACHE) > _THUMB_CACHE_MAX:
        _THUMB_CACHE.popitem(last=False)
    return pix


def note_title_and_lines(raw: str) -> tuple[str, list[str], bool]:
    """본문에서 ``(제목, 나머지 줄, 이미지 유무)`` 를 뽑는다. 제목은 첫 번째
    비어 있지 않은 줄이고, 글이 없으면 이미지 메모·빈 메모 문구다."""
    # 위젯 그림은 그림이 아니라 그 이름 글로 읽는다('투표: 오늘 점심').
    content = html_to_plain(with_widgets_as_text(raw or ""))
    lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
    # 체크박스 마커도 <img> 이므로 진짜 이미지 첨부만 가려낸다.
    has_image = has_non_checkbox_image(raw or "")
    if lines:
        return lines[0], lines[1:], has_image
    if has_image:
        return i18n.t("main.note_title_image"), [], True
    return i18n.t("main.note_title_empty"), [], False


class ClampLabel(QWidget):
    """정해 둔 줄 수까지만 그리고 넘치면 마지막 줄 끝을 줄이는 라벨.

    QLabel 의 wordWrap 은 줄 수를 제한하지 못해 카드 높이가 내용마다 달라지고,
    공백 없는 긴 글은 폭을 넘겨 버린다. 여기서는 글자 단위로도 접는다."""

    def __init__(self, max_lines: int = 1, parent: QWidget | None = None):
        super().__init__(parent)
        self._text = ""
        self._max_lines = max(1, int(max_lines))
        self._color = QColor("#1F1F1F")
        # 세로를 Fixed 로 두면 QWidgetItem 이 높이 상한을 sizeHint 로 묶어,
        # 배치가 heightForWidth 로 준 자리보다 작게(한 줄로) 그려진다.
        sp = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)
        # 카드 어디를 눌러도 카드가 눌린 것으로 받도록 마우스를 흘려보낸다.
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def text(self) -> str:
        return self._text

    def setText(self, text: str) -> None:  # noqa: N802
        text = text or ""
        if text != self._text:
            self._text = text
            self.updateGeometry()
            self.update()

    def setMaxLines(self, n: int) -> None:  # noqa: N802
        n = max(1, int(n))
        if n != self._max_lines:
            self._max_lines = n
            self.updateGeometry()
            self.update()

    def setColor(self, color: QColor) -> None:  # noqa: N802
        self._color = QColor(color)
        self.update()

    def setTextFont(self, pixel_size: int, bold: bool = False) -> None:  # noqa: N802
        f = QFont(self.font())
        f.setPixelSize(pixel_size)
        f.setBold(bold)
        # 정한 글꼴을 따로 들고 그린다. 처음 화면에 뜰 때 앱 꾸밈(QSS)이 위젯의 글꼴을
        # 13px 로 덮어써도 카드 제목과 본문의 크기는 그대로다.
        self._text_font = QFont(f)
        self.setFont(f)
        self.updateGeometry()
        self.update()

    def font(self) -> QFont:  # noqa: D401  그리는 글꼴(정해 둔 것이 있으면 그것)
        own = getattr(self, "_text_font", None)
        return QFont(own) if own is not None else super().font()

    def setLineHeight(self, factor: float | None) -> None:  # noqa: N802
        """줄 높이를 글자 크기의 배수로 정한다(None 이면 글꼴의 줄 간격). 글자가
        커질수록 줄 사이도 같은 비율로 벌어져 갑갑하지 않다."""
        if factor != getattr(self, "_line_factor", None):
            self._line_factor = factor
            self.updateGeometry()
            self.update()

    def _line_height(self) -> float:
        factor = getattr(self, "_line_factor", None)
        fm = QFontMetricsF(self.font())
        if factor:
            return max(fm.height(), self.font().pixelSize() * factor)
        return fm.lineSpacing()

    def lines_for(self, width: int) -> list[str]:
        if not self._text or width <= 0:
            return []
        font = self.font()
        key = (int(width), self._max_lines, self._text, font.key())
        cache = self.__dict__.setdefault("_lines_cache", {})
        hit = cache.get(key)
        if hit is not None:
            return list(hit)
        lines = self._measure_lines(width, font)
        if len(cache) >= 16:
            cache.clear()
        cache[key] = tuple(lines)
        return lines

    def _measure_lines(self, width: int, font: QFont) -> list[str]:
        text = self._text.replace("\n", _LINE_SEP)
        layout = QTextLayout(text, font)
        opt = QTextOption()
        opt.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(opt)
        spans: list[tuple[int, int]] = []
        layout.beginLayout()
        while len(spans) <= self._max_lines:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(width)
            spans.append((line.textStart(), line.textLength()))
        layout.endLayout()
        if len(spans) <= self._max_lines:
            return [text[s:s + n].replace(_LINE_SEP, "").rstrip()
                    for s, n in spans]
        keep = spans[:self._max_lines]
        out = [text[s:s + n].replace(_LINE_SEP, "").rstrip()
               for s, n in keep[:-1]]
        rest = text[keep[-1][0]:].replace(_LINE_SEP, " ")
        out.append(QFontMetricsF(font).elidedText(
            rest, Qt.ElideRight, width))
        return out

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        n = max(1, len(self.lines_for(width)))
        return int(math.ceil(n * self._line_height()))

    def sizeHint(self) -> QSize:  # noqa: N802
        # 실제 높이는 배치가 heightForWidth 로 정한다. 여기서는 폭에 기대지
        # 않는 한 줄 높이만 알려 준다(낡은 폭으로 잰 값이 굳지 않게).
        return QSize(160, int(math.ceil(self._line_height())))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(24, int(math.ceil(self._line_height())))

    def paintEvent(self, event) -> None:  # noqa: N802
        lines = self.lines_for(self.width())
        if not lines:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.setFont(self.font())
        p.setPen(self._color)
        fm = QFontMetricsF(self.font())
        step = self._line_height()
        # 가로 줄 안에서는 줄 높이보다 큰 자리를 받으므로 세로 가운데에 그린다.
        block = len(lines) * step
        y = (max(0.0, (self.height() - block) / 2.0)
             + (step - fm.height()) / 2.0 + fm.ascent())
        for line in lines:
            p.drawText(QPointF(0.0, y), line)
            y += step
        p.end()


class CardFlowLayout(QLayout):
    """카드를 같은 폭의 열로 흘려 놓는다. 열이 하나면 리스트, 여럿이면 그리드다.

    숨긴 카드(검색·폴더로 걸러진 것)는 자리를 차지하지 않는다. 같은 줄의 카드는
    그 줄에서 가장 큰 카드 높이에 맞춘다."""

    def __init__(self, parent: QWidget | None = None, spacing: int = 8):
        super().__init__(parent)
        self._items: list[QWidgetItem] = []
        self._min_col = 0  # 0 이면 한 열(리스트).
        self._square = 0   # 0 이 아니면 모든 칸이 같은 크기의 네모(한 변의 최소)
        self.setSpacing(spacing)

    def set_square(self, size: int) -> None:
        """모든 칸을 같은 크기의 네모로 놓는다(격자). ``size`` 는 한 변의 기준 길이다.
        칸들은 줄 폭을 고르게 채우고(오른쪽에 큰 빈자리가 생기지 않는다), 한 줄의 칸
        수는 칸이 기준에 가장 가깝게 고른다. 카드는 칸 크기에 맞춰 글자를 키우고
        줄인다(글자와 칸의 비율이 늘 같다). 0 이면 끈다."""
        size = max(0, int(size))
        if size != self._square:
            self._square = size
            self.invalidate()

    def square(self) -> int:
        return self._square

    def set_min_column_width(self, width: int) -> None:
        width = max(0, int(width))
        if width != self._min_col:
            self._min_col = width
            self.invalidate()

    # QLayout 구현
    def addItem(self, item) -> None:  # noqa: N802
        self._items.append(item)
        self.invalidate()

    def insertWidget(self, index: int, widget: QWidget) -> None:  # noqa: N802
        self.addChildWidget(widget)
        index = max(0, min(int(index), len(self._items)))
        self._items.insert(index, QWidgetItem(widget))
        self.invalidate()

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int):  # noqa: N802
        if 0 <= index < len(self._items):
            item = self._items.pop(index)
            self.invalidate()
            return item
        return None

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        m = self.contentsMargins()
        w = (self._square or self._min_col or 120) + m.left() + m.right()
        return QSize(w, m.top() + m.bottom())

    def column_count(self, width: int) -> int:
        """안쪽 폭 ``width`` 에 놓일 열 수."""
        sp = self.spacing()
        if self._square:
            ref = self._square
            cols = max(1, int((width + sp) / (ref + sp) + 0.5))
            while cols > 1 and (width - (cols - 1) * sp) / cols < ref * GRID_SHRINK:
                cols -= 1
            return cols
        if not self._min_col:
            return 1
        return max(1, (width + sp) // (self._min_col + sp))

    def _arrange(self, rect: QRect, *, apply: bool) -> int:
        m = self.contentsMargins()
        inner = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        sp = self.spacing()
        items = [it for it in self._items
                 if it.widget() is not None and not it.widget().isHidden()]
        if not items or inner.width() <= 0:
            return m.top() + m.bottom()
        cols = self.column_count(inner.width())
        col_w = (inner.width() - (cols - 1) * sp) // cols
        y = inner.y()
        for start in range(0, len(items), cols):
            row = items[start:start + cols]
            if self._square:
                row_h = col_w
            else:
                heights = []
                for it in row:
                    w = it.widget()
                    h = (w.heightForWidth(col_w) if w.hasHeightForWidth()
                         else w.sizeHint().height())
                    h = max(h, w.minimumHeight())
                    h = min(h, w.maximumHeight())
                    heights.append(h)
                row_h = max(heights)
            if apply:
                x = inner.x()
                for it in row:
                    w = it.widget()
                    if self._square and hasattr(w, "set_cell"):
                        # 글자 배율은 실제 칸 폭으로 정한다. 자리가 그대로라 크기 바뀜
                        # 알림이 오지 않아도 늘 맞는다(앞의 폭으로 잰 배율이 남지 않는다).
                        w.set_cell(col_w)
                    it.setGeometry(QRect(x, y, col_w, row_h))
                    x += col_w + sp
            y += row_h + sp
        return (y - sp) - rect.y() + m.bottom()


class _CheckMark(QWidget):
    """고르는 카드(휴지통)의 왼쪽 체크. 누르는 것은 카드가 받는다."""

    SIDE = 20

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(self.SIDE, self.SIDE)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._checked = False
        self._dark = False

    def set_state(self, checked: bool, dark: bool) -> None:
        if (checked, dark) != (self._checked, self._dark):
            self._checked, self._dark = checked, dark
            self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        center = QPointF(self.SIDE / 2, self.SIDE / 2)
        if self._checked:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#93C5FD" if self._dark else "#1D4ED8"))
            p.drawEllipse(center, 8.5, 8.5)
            p.setPen(QPen(QColor("#1F2937" if self._dark else "#FFFFFF"), 2.0,
                          Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            cx, cy = center.x(), center.y()
            p.drawLine(QPointF(cx - 4.0, cy + 0.2), QPointF(cx - 1.2, cy + 3.0))
            p.drawLine(QPointF(cx - 1.2, cy + 3.0), QPointF(cx + 4.2, cy - 2.8))
        else:
            ring = QColor(255, 255, 255, 170) if self._dark else QColor(0, 0, 0, 120)
            p.setPen(QPen(ring, 1.4))
            p.setBrush(QColor(255, 255, 255, 40 if self._dark else 150))
            p.drawEllipse(center, 7.8, 7.8)
        p.end()


def _fit_label_width(label: QLabel) -> None:
    """글 폭만큼만 줄어들지 않게 한다. QLabel 은 정해 둔 가장 작은 폭을 제 폭으로 여겨
    한 번 넓게 정하면 글이 짧아져도 줄지 않으므로, 풀고 나서 다시 잰다."""
    label.ensurePolished()              # 꾸밈(11px 글꼴)을 입힌 뒤의 폭으로 잰다
    label.setMinimumWidth(0)
    label.setMinimumWidth(label.sizeHint().width())


class NoteCard(QFrame):
    clicked = Signal(str)
    rangeClickRequested = Signal(str)
    toggleVisibility = Signal(str)
    deleteRequested = Signal(str)
    historyRequested = Signal(str)
    privateToggleRequested = Signal(str, bool)   # note_id, new_private
    moveToFolderRequested = Signal(str, object)  # note_id, folder_id|None
    doubleClicked = Signal(str)
    # 단추 없는 카드(휴지통)를 오른쪽 단추로 눌렀다: note_id, 화면에서의 자리.
    menuRequested = Signal(str, QPoint)

    _CARD_RADIUS = 12
    _ROW_RADIUS = 8
    _PAPER_RADIUS = 10
    # 여백과 간격은 글자 크기를 따라간다. 글자가 큰 단계일수록 여백도 넓다(같은
    # 여백이라도 글자가 크면 좁게 느껴진다).
    # 목록 줄: 색 네모 크기, 왼쪽 여백, 네모와 글 사이, 위아래 여백, 제목과 본문 사이,
    # 윗줄 높이, (제목, 본문) 글자.
    _LIST_METRICS = {
        VIEW_COMPACT: dict(box=10, pad=14, gap=12, v=8, tb=0, line=22, fonts=(13, 12)),
        VIEW_STANDARD: dict(box=12, pad=14, gap=14, v=10, tb=2, line=22, fonts=(13, 12)),
        VIEW_DETAILED: dict(box=14, pad=16, gap=17, v=13, tb=4, line=24, fonts=(15, 13)),
    }
    # 격자 칸이 기준 크기일 때: 옆 여백, 아래 여백, 머리 줄, 머리 줄과 제목 사이, 제목과
    # 본문 사이, (제목, 본문) 글자. 칸이 커지고 작아지면 모두 같은 비율로 바뀐다.
    _GRID_METRICS = {
        VIEW_COMPACT: dict(pad=11, bottom=10, bar=26, after=8, tb=5, fonts=(13, 11)),
        VIEW_STANDARD: dict(pad=14, bottom=12, bar=28, after=10, tb=7, fonts=(14, 12)),
        VIEW_DETAILED: dict(pad=17, bottom=14, bar=30, after=12, tb=9, fonts=(15, 13)),
    }
    _BODY_LINE = 1.42      # 본문 줄 높이(글자 크기의 배수)

    def __init__(self, note: dict, visible: bool, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setCursor(Qt.PointingHandCursor)
        # 글쇠로도 고르고 열고 지울 수 있게 Tab 순서에 넣는다.
        self.setFocusPolicy(Qt.StrongFocus)
        self._kb_focus = False
        sp = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)

        self._note: dict = dict(note)
        self._note_id: str = note["id"]
        self._color: str = note.get("color", config.DEFAULT_COLOR)
        self._visible = visible
        self._updated_at: int = int(note.get("updated_at", 0))
        self._private: bool = bool(note.get("private_owner"))
        self._folder_context: list[tuple[str, str]] = []  # (id, name)
        self._selection_mode: bool = False
        self._selected: bool = False
        # 눈과 메뉴 단추가 없는 카드인가(휴지통). 누르고 고르는 것만 된다.
        self._plain: bool = False
        # 시각과 아랫줄에 따로 적을 글(휴지통: 남은 날, 지운 사람). None 이면 평소대로.
        self._stamp_text: str | None = None
        self._meta_text: str | None = None
        self._density: str = VIEW_STANDARD
        self._layout_mode: str = VIEW_LIST
        # 메모 색 위의 글자색(격자). 목록은 흰 바탕이라 늘 짙은 글자다.
        self._fg: str = "#1F1F1F"
        # 메인 창의 격자는 같은 크기의 네모 칸이다(높이를 칸이 정한다).
        self._square = False
        self._hover = False
        self._pressed = False
        self._has_thumb = False
        # 격자 칸이 기준 크기보다 얼마나 큰가(글자와 여백을 같은 비율로).
        self._scale = 1.0
        self._cell_w = 0            # 격자 칸의 실제 폭(배치가 알린다)
        self._bar_h = 28
        self._box = 12
        self._box_x = 14
        # 상태 그림: 알림, 체크 해제 예약, 편집 중인 사람.
        self._remind = False
        self._reset = False
        self._editing = ""
        self._folded = False
        self._eye_icon = QIcon()
        self._menu_icon = QIcon()
        self.setAttribute(Qt.WA_Hover)

        self._root = QVBoxLayout(self)
        self._root.setSpacing(6)

        # 윗줄은 높이가 정해진 상자 안에 둔다. 그림이 있든 없든, 마우스를 올려
        # 단추가 보이든 아니든 제목이 같은 높이에서 시작한다.
        self._top_box = QWidget(self)
        self._top_box.setAttribute(Qt.WA_TranslucentBackground)
        self._top = QHBoxLayout(self._top_box)
        self._top.setContentsMargins(0, 0, 0, 0)
        self._top.setSpacing(4)

        self._eye_btn = QToolButton()
        self._eye_btn.setIconSize(QSize(16, 16))
        self._eye_btn.setProperty("iconOnly", True)
        self._eye_btn.setCursor(Qt.PointingHandCursor)
        self._eye_btn.setFocusPolicy(Qt.NoFocus)
        self._eye_btn.clicked.connect(
            lambda: self.toggleVisibility.emit(self._note_id))

        self._image_indicator = QLabel()
        self._image_indicator.setFixedSize(20, 20)
        self._image_indicator.setAlignment(Qt.AlignCenter)
        self._image_indicator.setToolTip(i18n.t("main.tooltip_image_note"))
        self._image_indicator.setStyleSheet("background: transparent;")
        self._image_indicator.hide()

        # 시스템 emoji 폰트 영향을 받지 않도록 SVG 자물쇠로 배지를 만든다.
        self._private_indicator = QLabel()
        self._private_indicator.setFixedSize(22, 20)
        self._private_indicator.setAlignment(Qt.AlignCenter)
        self._private_indicator.setToolTip(
            i18n.t("main.tooltip_private_note"))
        self._private_indicator.hide()

        # 그 상태일 때만 보이는 작은 그림: 알림, 체크 해제 예약, 숨김, 편집 중.
        self._remind_indicator = self._indicator("main.tooltip_remind_note")
        self._reset_indicator = self._indicator("main.tooltip_reset_note")
        self._hidden_indicator = self._indicator("main.tooltip_hidden_note")
        self._fold_indicator = self._indicator("main.tooltip_folded_note")
        self._notice = False
        self._notice_indicator = QLabel(i18n.t("notice.label"))
        self._notice_indicator.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._notice_indicator.hide()
        self._edit_indicator = self._indicator("")
        self._edit_name = QLabel("")
        self._edit_name.setTextFormat(Qt.PlainText)
        self._edit_name.hide()

        # 목록에서는 제목이 윗줄 안에 들어간다.
        self._inline_title = ClampLabel(1)
        self._inline_title.setTextFont(13, True)
        self._inline_title.hide()

        self._timestamp = QLabel("")

        self._menu_btn = QToolButton()
        self._menu_btn.setIconSize(QSize(16, 16))
        self._menu_btn.setFixedSize(22, 22)
        self._menu_btn.setProperty("iconOnly", True)
        self._menu_btn.setCursor(Qt.PointingHandCursor)
        self._menu_btn.setFocusPolicy(Qt.NoFocus)
        self._menu_btn.setToolTip(i18n.t("main.tooltip_menu"))
        self._menu_btn.setAccessibleName(i18n.t("main.tooltip_menu"))
        self._menu_btn.clicked.connect(self._open_menu)

        # 고르는 카드(휴지통)에서는 눈 단추 자리에 체크가 온다.
        self._check = _CheckMark()
        self._check.hide()

        self._top.addWidget(self._check)
        self._top.addWidget(self._inline_title, 1)
        for w in (self._notice_indicator, self._remind_indicator, self._reset_indicator,
                  self._hidden_indicator, self._fold_indicator, self._private_indicator,
                  self._image_indicator, self._edit_indicator, self._edit_name):
            self._top.addWidget(w)
        # 격자에서는 이 빈칸이 늘어나 그림은 왼쪽, 시각과 단추는 오른쪽에 간다.
        self._stretch_at = self._top.count()
        self._top.addStretch(0)
        self._top.addWidget(self._timestamp)
        self._top.addWidget(self._eye_btn)
        self._top.addWidget(self._menu_btn)
        self._root.addWidget(self._top_box)

        # 리스트에서는 글 옆에, 그리드에서는 글 위에 축소판을 둔다(방향만 바꾼다).
        self._mid = QBoxLayout(QBoxLayout.LeftToRight)
        self._mid.setContentsMargins(0, 0, 0, 0)
        self._mid.setSpacing(10)
        # 격자의 제목은 머리 줄 바로 아래(그림보다 위)에 온다.
        self._title = ClampLabel(2)
        self._title.setTextFont(14, True)
        self._root.addWidget(self._title)
        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(4)
        self._body = ClampLabel(3)
        self._body.setTextFont(12)
        text_col.addWidget(self._body)
        text_col.addStretch(1)
        self._mid.addLayout(text_col, 1)
        self._thumb = QLabel()
        self._thumb.setFixedSize(_THUMB_SIZE)
        self._thumb.setStyleSheet("background: transparent;")
        self._thumb.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._thumb.hide()
        self._mid.addWidget(self._thumb, 0, Qt.AlignTop)
        # 목록의 둘째 줄 오른쪽: 고친 사람.
        self._author = QLabel("")
        self._author.setTextFormat(Qt.PlainText)
        self._author.hide()
        self._mid.addWidget(self._author, 0, Qt.AlignTop | Qt.AlignRight)
        self._root.addLayout(self._mid)

        self._meta = ClampLabel(1)
        self._meta.setTextFont(11)
        self._meta.hide()
        self._root.addWidget(self._meta)
        self._root.addStretch(1)

        # 격자 칸의 아래(넘친 글이 흐려지는 곳)와 숨긴 메모의 옅은 막.
        self._fade = _Fade(self)
        self._veil = _Veil(self)

        self._apply_view_metrics()
        self.update_from(note, visible)

    def _indicator(self, tip_key: str) -> QLabel:
        label = QLabel()
        label.setFixedSize(18, 20)
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet("background: transparent;")
        if tip_key:
            label.setToolTip(i18n.t(tip_key))
        label.hide()
        return label

    # ── 상태 그림 ──

    def set_notice(self, on: bool) -> None:
        """공지 메모다(머리 줄에 '공지')."""
        if bool(on) != self._notice:
            self._notice = bool(on)
            self._render()

    def is_notice(self) -> bool:
        return self._notice

    def set_marks(self, *, remind: bool = False, reset: bool = False,
                  editing: str = "", folded: bool = False) -> None:
        """알림, 체크 해제 예약, 지금 편집 중인 사람(다른 사람), 접혀 있음을 그림으로
        보인다."""
        new = (remind, reset, editing, folded)
        if new == (self._remind, self._reset, self._editing, self._folded):
            return
        self._remind, self._reset, self._editing, self._folded = new
        self._render()

    def _grid_fonts(self) -> tuple[int, int]:
        return self._grid_metrics()["fonts"]

    def _grid_metrics(self) -> dict:
        """격자 칸의 치수(칸 배율을 곱한 값)."""
        base = self._GRID_METRICS[self._density]
        s = self._scale if self._square else 1.0
        out = {k: max(1, round(v * s)) for k, v in base.items() if k != "fonts"}
        out["bar"] = max(24, out["bar"])
        out["fonts"] = tuple(max(9, round(px * s)) for px in base["fonts"])
        return out

    def set_square(self, on: bool) -> None:
        """같은 크기의 네모 칸에 놓인다(높이를 칸이 정한다)."""
        if self._square != bool(on):
            self._square = bool(on)
            self._rescale(from_width=False)
            self._apply_view_metrics()
            self._render()

    # ── 보기 방식 ──

    def set_view_all(self, square: bool, density: str, layout_mode: str) -> None:
        """네모 칸과 보기 방식을 한 번에 바꾼다(카드를 한 번만 다시 그린다)."""
        changed = False
        if self._square != bool(square):
            self._square = bool(square)
            changed = True
        if (density, layout_mode) != (self._density, self._layout_mode):
            if layout_mode != VIEW_GRID:
                self._cell_w = 0            # 목록의 폭은 칸 폭이 아니다
            self._density = density
            self._layout_mode = layout_mode
            changed = True
        if changed:
            self._rescale(from_width=False)
            self._apply_view_metrics()
            self._render()

    def set_view(self, density: str, layout_mode: str) -> None:
        if (density, layout_mode) == (self._density, self._layout_mode):
            return
        if layout_mode != VIEW_GRID:
            self._cell_w = 0            # 목록의 폭은 칸 폭이 아니다
        self._density = density
        self._layout_mode = layout_mode
        self._rescale(from_width=False)
        self._apply_view_metrics()
        self._render()

    def set_cell(self, width: int) -> None:
        """격자 배치가 정한 칸 폭. 칸을 놓을 때마다 불린다(자리가 그대로여도)."""
        self._cell_w = int(width)
        before = self._grid_metrics()
        if self._rescale() and self._grid_metrics() != before:
            self._apply_view_metrics()

    def _rescale(self, *, from_width: bool = True) -> bool:
        """네모 칸의 배율(칸 폭 / 그 크기의 기준 칸). 바뀌었으면 True. 칸 폭은 배치가
        알린 폭이고, 아직 모르면 카드의 폭이다(``from_width`` 가 False 면 모를 때 1).
        목록에서 격자로 바꾸는 때처럼 카드가 아직 칸에 놓이지 않았으면 그 폭은 칸이
        아니므로 쓰지 않는다(목록 폭으로 재면 글자가 세 배가 된다)."""
        width = self._cell_w or (self.width() if from_width else 0)
        if not (self._layout_mode == VIEW_GRID and self._square and width > 0):
            scale = 1.0
        else:
            scale = width / GRID_CELL.get(self._density, GRID_CELL[VIEW_STANDARD])
        if abs(scale - self._scale) > 0.005:
            self._scale = scale
            return True
        return False

    def _apply_view_metrics(self) -> None:
        """보기 크기와 칸 배율에 맞춘 여백과 글자. 적용하는 동안 배치가 끼어들어 배율이
        바뀌면(set_cell) 끝난 뒤 한 번 더 적용한다(앞서 잰 큰 글자로 덮어쓰지 않게)."""
        if self._applying:
            self._reapply = True
            return
        self._applying = True
        try:
            for _ in range(3):
                self._reapply = False
                self._apply_view_metrics_once()
                if not self._reapply:
                    break
        finally:
            self._applying = False

    _applying = False
    _reapply = False

    def _apply_view_metrics_once(self) -> None:
        grid = self._layout_mode == VIEW_GRID
        compact = self._density == VIEW_COMPACT
        # 고르는 카드(휴지통)의 목록은 체크가 색 네모 자리에 온다.
        plain_list = self._plain and not grid

        if grid:
            g = self._grid_metrics()
            self._bar_h = g["bar"]
            # 윗줄은 머리 줄 안에 오고, 그 아래 여백만큼 띄워 제목이 온다.
            extra = max(0, g["after"] - g["tb"])
            # 오른쪽 시각 글자 끝과 카드 끝 사이를 왼쪽 그림과 카드 끝 사이와 같게 둔다.
            self._root.setContentsMargins(g["pad"], 1, g["pad"], g["bottom"])
            self._top_box.setFixedHeight(g["bar"] - 2 + extra)
            self._top.setContentsMargins(0, 0, 0, extra)
            self._root.setSpacing(g["tb"])
            title_px, body_px = g["fonts"]
            self._mid.setSpacing(max(4, round(6 * self._scale)))
        else:
            m = self._LIST_METRICS[self._density]
            self._box, self._box_x = m["box"], m["pad"]
            text_x = m["pad"] + m["box"] + m["gap"]
            if plain_list:
                self._root.setContentsMargins(12, m["v"], 12, m["v"])
            else:
                self._root.setContentsMargins(text_x, m["v"], 12, m["v"])
            self._top_box.setFixedHeight(m["line"])
            self._top.setContentsMargins(0, 0, 0, 0)
            self._root.setSpacing(m["tb"])
            title_px, body_px = m["fonts"]
            self._mid.setSpacing(12)
        self._top.setSpacing(6 if self._plain else 4)
        self._timestamp.setContentsMargins(0 if grid else 6, 0, 0, 0)
        self._timestamp.setAlignment(
            (Qt.AlignRight if grid else Qt.AlignLeft) | Qt.AlignVCenter)
        self._top.setStretch(self._stretch_at, 1 if grid else 0)
        # 격자에서는 머리 줄의 그림이 제목과 같은 세로줄에서 시작한다.
        mark_align = (Qt.AlignLeft | Qt.AlignVCenter) if grid else Qt.AlignCenter
        for w in (self._remind_indicator, self._reset_indicator, self._hidden_indicator,
                  self._fold_indicator, self._private_indicator, self._image_indicator,
                  self._edit_indicator):
            w.setAlignment(mark_align)
        # 글은 제목과 줄을 맞춘다(체크의 폭만큼 들인다).
        self._mid.setContentsMargins(
            _CheckMark.SIDE + self._top.spacing() if plain_list else 0, 0, 0, 0)

        self._inline_title.setVisible(not grid)
        self._title.setVisible(grid)
        self._title.setMaxLines(2)
        self._title.setTextFont(title_px, True)
        self._title.setLineHeight(1.3)
        if not grid:
            self._body.setMaxLines(2 if self._density == VIEW_DETAILED else 1)
        elif not self._square:
            self._body.setMaxLines({VIEW_COMPACT: 1, VIEW_STANDARD: 4,
                                    VIEW_DETAILED: 8}[self._density])
        self._body.setTextFont(body_px)
        self._body.setLineHeight(self._BODY_LINE)
        self._inline_title.setTextFont(title_px, True)
        self._mid.setDirection(QBoxLayout.BottomToTop if grid
                               else QBoxLayout.LeftToRight)
        self._mid.setAlignment(
            self._thumb, (Qt.AlignLeft if grid else Qt.AlignTop))
        self._author.setVisible(not grid and not compact and not self._plain)
        self._fade.setVisible(grid)

        if grid and self._square:
            self.setMinimumHeight(0)
            self.setMaximumHeight(16777215)
        elif grid:
            self.setFixedHeight({VIEW_COMPACT: 84, VIEW_STANDARD: 148,
                                 VIEW_DETAILED: 236}[self._density])
        elif plain_list:
            self.setMinimumHeight({VIEW_COMPACT: 38, VIEW_STANDARD: 44,
                                   VIEW_DETAILED: 44}[self._density])
            self.setMaximumHeight(16777215)
        else:
            # 줄 높이는 글 덩이와 위아래 여백으로 정해진다. 그래야 글 덩이가 줄의
            # 위아래 가운데에 오고, 가운데에 그리는 색 네모와 맞는다.
            self.setMinimumHeight(0)
            self.setMaximumHeight(16777215)
        self._refresh_actions()
        self.updateGeometry()

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        h = self._root.heightForWidth(width)
        if h < 0:
            h = self._root.sizeHint().height()
        return max(self.minimumHeight(), min(h, self.maximumHeight()))

    # ── 내용 ──

    def update_from(self, note: dict, visible: bool) -> None:
        self._note = dict(note)
        self._note_id = note["id"]
        self._color = note.get("color", config.DEFAULT_COLOR)
        self._visible = visible
        self._updated_at = int(note.get("updated_at", 0))
        # 서버가 청중을 이미 필터링하므로, 이 메모가 도달했다면 본인이 곧 소유자다.
        self._private = bool(note.get("private_owner"))
        self._render()

    def _folder_name(self) -> str:
        fid = self._note.get("folder_id")
        if not fid:
            return ""
        for cid, name in self._folder_context:
            if cid == fid:
                return name
        return ""

    def _render(self) -> None:
        raw = self._note.get("content") or ""
        title, rest, has_image = note_title_and_lines(raw)
        if title:
            self.setAccessibleName(title)
        grid = self._layout_mode == VIEW_GRID
        compact = self._density == VIEW_COMPACT
        detailed = self._density == VIEW_DETAILED

        if len(title) > 120:
            title = title[:120] + "…"
        self._title.setText(title)
        self._inline_title.setText(title)

        if grid:
            # 종이처럼 줄바꿈을 살린다. 칸이 정한 높이까지만 보이고 아래는 흐려진다.
            body = "\n".join(rest[:16])
            if len(body) > 800:
                body = body[:800] + "…"
        elif compact:
            body = ""
        else:
            body = " ".join(rest[:6 if detailed else 3])
            if len(body) > 240:
                body = body[:240] + "…"
        self._body.setText(body)
        self._body.setVisible(bool(body))

        thumb = None
        if has_image and (detailed or (grid and not compact)):
            thumb = _thumbnail(_first_image_url(raw))
        if thumb is not None:
            self._thumb.setPixmap(thumb)
        self._has_thumb = thumb is not None
        self._thumb.setVisible(thumb is not None)
        if grid and not self._square:
            # 높이가 정해진 타일은 축소판이 들어가면 글 줄을 덜어 낸다.
            self._body.setMaxLines(
                {VIEW_COMPACT: 1, VIEW_STANDARD: 2 if thumb is not None else 4,
                 VIEW_DETAILED: 4 if thumb is not None else 8}[self._density])

        stamp = (self._stamp_text if self._stamp_text is not None
                 else datetime_fmt.fmt_card_timestamp(self._updated_at))
        self._timestamp.setText(stamp)
        self._stamp_full = stamp
        meta_parts: list[str] = []
        if self._meta_text is not None:
            # 격자의 칸은 좁다. 시각 자리의 글만 적는다.
            if detailed and not grid:
                meta_parts.append(self._meta_text)
        meta = "    ".join(p for p in meta_parts if p)
        self._meta.setText(meta)
        self._meta.setVisible(bool(meta))
        self._author.setText((self._note.get("updated_by") or "").strip())

        plain = self._plain
        self._image_indicator.setVisible(has_image and not grid)
        self._private_indicator.setVisible(self._private)
        self._remind_indicator.setVisible(self._remind and not plain)
        self._reset_indicator.setVisible(self._reset and not self._remind and not plain)
        self._hidden_indicator.setVisible(not self._visible and not plain)
        self._fold_indicator.setVisible(self._folded and self._visible and not plain)
        self._notice_indicator.setVisible(self._notice and not plain)
        self._edit_indicator.setVisible(bool(self._editing) and not plain)
        self._edit_name.setVisible(bool(self._editing) and not plain and not grid)
        self._edit_name.setText(self._editing)
        self._edit_indicator.setToolTip(
            i18n.t("main.tooltip_editing", name=self._editing) if self._editing else "")
        self._veil.setVisible(grid and not self._visible and not plain)
        self._fit_head()
        if grid and self._square and self.height() > 0:
            self._fit_body_to_cell()
        self._apply_colors()
        self.updateGeometry()
        self.update()

    def _apply_colors(self) -> None:
        """글자와 그림의 색. 격자는 메모 색 위라 메모에 맞추고(어두운 메모에서도
        읽힌다), 목록은 흰 바탕이라 짙은 글자다. 숨긴 메모의 목록 제목은 옅다."""
        grid = self._layout_mode == VIEW_GRID
        # 같은 상태면 다시 입히지 않는다(그림과 꾸밈을 만드는 데 카드마다 몇 ms 가 든다).
        key = (grid, self._color, bool(self._visible), bool(self._plain), bool(self._selected))
        if key == getattr(self, "_colors_key", None):
            return
        self._colors_key = key
        fg = text_color_for_bg(self._color)
        self._fg = fg
        dark = grid and fg == "#FFFFFF"
        if grid:
            # 글자색은 메모 색과 미리 섞은 불투명한 색이다. 반투명한 글자는 메모 색
            # 위에서 Windows 글꼴 다듬기(ClearType)가 붉고 푸른 번짐을 만든다.
            paper = QColor(self._color)
            strong = QColor(fg)
            soft = _mix(QColor(fg), paper, 0.84 if dark else 0.80)
            # 11px 시각 글자도 가장 옅은 메모 색 위에서 4.5:1 을 넘게 한다.
            faint = _mix(QColor(fg), self._bar_color(), 0.80 if dark else 0.72)
            icon = "#F2F2F2" if dark else "#4A4A4A"
        else:
            strong = QColor("#1F1F1F" if self._visible or self._plain else "#8C8C8C")
            soft = QColor("#5B5B5B")
            faint = QColor("#6B6B6B")
            icon = "#5B5B5B"
        self._title.setColor(strong)
        self._notice_indicator.setStyleSheet(
            f"color: {strong.name()}; font-size: 11px; font-weight: 700;"
            " background: transparent; padding: 0px 2px 0px 0px;")
        self._inline_title.setColor(strong)
        self._body.setColor(soft)
        self._meta.setColor(faint)
        small = f"color: {faint.name()}; font-size: 11px; background: transparent;"
        self._timestamp.setStyleSheet(small)
        self._author.setStyleSheet(small)
        self._edit_name.setStyleSheet(
            "color: #B45309; font-size: 11px; font-weight: 600; background: transparent;")
        self._menu_icon = svg_icon("more", color=icon)
        self._image_indicator.setPixmap(svg_icon("image", color=icon).pixmap(14, 14))
        self._remind_indicator.setPixmap(svg_icon("bell", color=icon).pixmap(13, 13))
        self._reset_indicator.setPixmap(svg_icon("check_reset", color=icon).pixmap(13, 13))
        self._hidden_indicator.setPixmap(svg_icon("eye_off", color=icon).pixmap(13, 13))
        self._fold_indicator.setPixmap(svg_icon("fold", color=icon).pixmap(13, 13))
        self._edit_indicator.setPixmap(
            svg_icon("edit", color="#FBBF24" if dark else "#B45309").pixmap(13, 13))
        self._private_indicator.setPixmap(svg_icon("lock", color=icon).pixmap(13, 13))
        self._private_indicator.setFixedSize(18, 20)
        self._private_indicator.setStyleSheet("background: transparent;")
        hover = ("rgba(255, 255, 255, 0.16)" if dark
                 else "rgba(0, 0, 0, 0.07)")
        for b in (self._eye_btn, self._menu_btn):
            b.setStyleSheet(
                "QToolButton { background: transparent; border: none; "
                "padding: 0; border-radius: 6px; }"
                f"QToolButton:hover {{ background: {hover}; }}")
        self._check.set_state(self._selected, dark)
        self._fade.set_color(QColor(self._color))
        self._refresh_eye_icon()

    def set_visible(self, visible: bool) -> None:
        self._visible = visible
        self._render()

    def _refresh_eye_icon(self) -> None:
        name = "eye_on" if self._visible else "eye_off"
        dark = self._layout_mode == VIEW_GRID and self._fg == "#FFFFFF"
        self._eye_icon = svg_icon(name, color="#F2F2F2" if dark else "#4A4A4A")
        tip = i18n.t("main.tooltip_hide_from_desktop" if self._visible
                     else "main.tooltip_show_on_desktop")
        self._eye_btn.setToolTip(tip)
        self._eye_btn.setAccessibleName(tip)
        self._refresh_actions()

    def _short_stamp(self) -> str:
        """오늘 고친 시각의 짧은 꼴('오전 12:04' 를 '12:04')."""
        if self._stamp_text is not None or not self._updated_at:
            return ""
        from datetime import datetime
        dt = datetime.fromtimestamp(self._updated_at / 1000)
        if dt.date() != datetime.now().date():
            return ""
        return dt.strftime("%H:%M")

    def _fit_head(self) -> None:
        """격자 머리 줄이 좁으면 눌러 자르지 않고 덜어 낸다: 접힘, 체크 해제, 알림 표시를
        차례로 감추고, 그래도 좁으면 시각을 짧은 꼴로. '공지' 와 시각은 줄이지 않는다."""
        stamp = getattr(self, "_stamp_full", self._timestamp.text())
        if self._timestamp.text() != stamp:
            self._timestamp.setText(stamp)
        for w in (self._notice_indicator, self._timestamp):
            _fit_label_width(w)
        if self._layout_mode != VIEW_GRID or self._top_box.width() <= 0:
            return
        gap = self._top.spacing()
        room = self._top_box.width() - self._menu_btn.sizeHint().width() - gap

        def need() -> int:
            n = 0
            for w in (self._check, self._notice_indicator, self._remind_indicator,
                      self._reset_indicator, self._hidden_indicator, self._fold_indicator,
                      self._private_indicator, self._image_indicator, self._edit_indicator,
                      self._timestamp):
                if not w.isHidden():
                    n += w.sizeHint().width() + gap
            return n

        for w in (self._fold_indicator, self._reset_indicator, self._remind_indicator):
            if need() <= room:
                return
            w.hide()
        short = self._short_stamp()
        if need() > room and short:
            self._timestamp.setText(short)
            _fit_label_width(self._timestamp)

    def _refresh_actions(self) -> None:
        """마우스를 올렸거나 글쇠로 왔을 때 시각 오른쪽에 더 보기 단추가 나온다(시각은
        그만큼 왼쪽으로 간다. 윗줄의 높이는 그대로라 아래 내용은 움직이지 않는다).
        표시·숨기기 단추는 두지 않는다(카드를 누르면 메모가 뜨고, 숨기기는 더 보기
        메뉴에 있다)."""
        show = (self._hover or self._kb_focus) and not self._selection_mode
        self._menu_btn.setIcon(self._menu_icon)
        self._menu_btn.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self._eye_btn.setVisible(False)
        if not self._plain:
            self._menu_btn.setVisible(show)
            self._timestamp.setVisible(True)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self._refresh_actions()
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self._refresh_actions()
        self.update()
        super().leaveEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        # 테두리(마우스를 올리면 짙어진다)를 덮지 않게 안쪽에 둔다.
        fade = max(18, round(24 * self._scale))
        self._fade.setGeometry(2, max(0, self.height() - fade - 2), max(0, self.width() - 4), fade)
        self._fade.raise_()
        self._veil.setGeometry(self.rect())
        self._veil.raise_()
        self._fit_head()
        if self._layout_mode == VIEW_GRID and self._square:
            # 칸이 기준보다 크거나 작으면 글자도 같은 비율로(한 px 단위로 바뀐다).
            self.set_cell(self.width())
            self._fit_body_to_cell()

    def _fit_body_to_cell(self) -> None:
        """네모 칸에 들어갈 만큼만 그림과 본문 줄을 둔다. 둘이 서로 겹치지 않게, 남는
        높이를 위에서부터 나눠 준다(그림이 들어갈 자리가 없으면 그림을 뺀다)."""
        m = self._root.contentsMargins()
        sp = self._root.spacing()
        inner_w = max(1, self.width() - m.left() - m.right())
        room = self.height() - m.top() - self._top_box.height() - sp - m.bottom()
        if not self._title.isHidden():
            room -= self._title.heightForWidth(inner_w) + sp
        line = self._body._line_height()
        show_thumb = (self._has_thumb
                      and room >= self._thumb.height() + self._mid.spacing() + line)
        self._thumb.setVisible(show_thumb)
        if show_thumb:
            room -= self._thumb.height() + self._mid.spacing()
        lines = int(room // line) if line > 0 else 0
        self._body.setVisible(bool(self._body.text()) and lines >= 1)
        if lines >= 1 and lines != self._body._max_lines:
            self._body.setMaxLines(lines)

    def set_folder_context(self, folders: list[tuple[str, str]]) -> None:
        self._folder_context = list(folders)
        if self._density == VIEW_DETAILED:
            self._render()

    def set_plain(self, on: bool) -> None:
        """고르기만 하는 카드로 바꾼다. 눈과 메뉴 단추를 없애고 왼쪽에 체크를
        둔다. 오른쪽 단추는 ``menuRequested`` 로, Enter 는 ``doubleClicked`` 로
        알린다."""
        if self._plain == bool(on):
            return
        self._plain = bool(on)
        self._eye_btn.setVisible(not on)
        self._menu_btn.setVisible(not on)
        self._timestamp.setVisible(True)
        self._check.setVisible(bool(on))
        self._apply_view_metrics()
        self._render()

    def set_caption(self, stamp: str | None, meta: str | None) -> None:
        """시각 자리와 아랫줄에 적을 글을 따로 정한다."""
        if (stamp, meta) != (self._stamp_text, self._meta_text):
            self._stamp_text, self._meta_text = stamp, meta
            self._render()

    def set_selection_mode(self, on: bool) -> None:
        # 일괄 편집 중 카드별 동작이 실행되지 않도록 눈/메뉴 버튼을 비활성화한다.
        if self._selection_mode == on:
            return
        self._selection_mode = on
        if not on:
            self._selected = False
        self._eye_btn.setEnabled(not on)
        self._menu_btn.setEnabled(not on)
        self._refresh_actions()
        self.update()

    def mouseDoubleClickEvent(self, ev: QMouseEvent) -> None:  # noqa: N802
        if ev.button() == Qt.LeftButton:
            self.doubleClicked.emit(self._note_id)
        super().mouseDoubleClickEvent(ev)

    def set_selected(self, selected: bool) -> None:
        if self._selected == selected:
            return
        self._selected = selected
        self._check.set_state(selected, self._fg == "#FFFFFF")
        self.update()

    def is_selected(self) -> bool:
        return self._selected

    def build_menu(self) -> QMenu:
        """더 보기 메뉴. 글 길이에 맞는 폭이고, 하위 메뉴 화살표는 오른쪽 끝에서 조금
        들인다."""
        menu = QMenu(self)
        menu.setAttribute(Qt.WA_NoMouseReplay)
        menu.setStyleSheet(_CARD_MENU_QSS)
        act_eye = menu.addAction(i18n.t("main.tooltip_hide_from_desktop" if self._visible
                                        else "main.tooltip_show_on_desktop"))
        act_eye.triggered.connect(lambda: self.toggleVisibility.emit(self._note_id))
        act_hist = menu.addAction(i18n.t("main.menu_edit_history"))
        act_hist.triggered.connect(
            lambda: self.historyRequested.emit(self._note_id))
        # 부모를 주어 만든다(파이썬 쪽이 가지면 build_menu 를 벗어날 때 지워진다).
        move_menu = QMenu(i18n.t("main.menu_move_to_folder"), menu)
        menu.addMenu(move_menu)
        move_menu.setStyleSheet(_CARD_MENU_QSS)
        act_to_none = move_menu.addAction(i18n.t("main.folder_uncategorized"))
        act_to_none.triggered.connect(
            lambda: self.moveToFolderRequested.emit(self._note_id, None))
        if self._folder_context:
            move_menu.addSeparator()
            for fid, fname in self._folder_context:
                act = move_menu.addAction(fname)
                act.triggered.connect(
                    lambda _checked=False, _fid=fid:
                        self.moveToFolderRequested.emit(self._note_id, _fid))
        act_priv = menu.addAction(
            i18n.t("main.menu_private_off") if self._private
            else i18n.t("main.menu_private_on"))
        act_priv.triggered.connect(
            lambda: self.privateToggleRequested.emit(
                self._note_id, not self._private))
        menu.addSeparator()
        act_del = menu.addAction(i18n.t("main.menu_delete"))
        act_del.triggered.connect(
            lambda: self.deleteRequested.emit(self._note_id))
        return menu

    def _open_menu(self) -> None:
        menu = self.build_menu()
        menu.exec(self._menu_btn.mapToGlobal(
            QPoint(0, self._menu_btn.height())))

    def mousePressEvent(self, ev: QMouseEvent) -> None:  # noqa: N802
        if ev.button() == Qt.LeftButton:
            self._press_pos = ev.position().toPoint()
            self._pressed = True
            self.update()
        super().mousePressEvent(ev)

    def mouseReleaseEvent(self, ev: QMouseEvent) -> None:  # noqa: N802
        if self._pressed:
            self._pressed = False
            self.update()
        if ev.button() == Qt.LeftButton and hasattr(self, "_press_pos"):
            at = ev.position().toPoint()
            if (at - self._press_pos).manhattanLength() < 5:
                # 눈/메뉴 미니 버튼 위 클릭은 카드 클릭으로 처리하지 않는다.
                child = self.childAt(at)
                if child not in (self._eye_btn, self._menu_btn):
                    if (self._selection_mode
                            and ev.modifiers() & Qt.ShiftModifier):
                        self.rangeClickRequested.emit(self._note_id)
                    else:
                        self.clicked.emit(self._note_id)
        super().mouseReleaseEvent(ev)

    def contextMenuEvent(self, ev) -> None:  # noqa: N802
        if self._plain:
            self.menuRequested.emit(self._note_id, ev.globalPos())
            return
        self._open_menu()

    def focusInEvent(self, ev) -> None:  # noqa: N802
        # 초점 테두리는 글쇠로 옮겨 왔을 때만 그린다(마우스로 누른 카드에는 안 그린다).
        self._kb_focus = ev.reason() in (
            Qt.TabFocusReason, Qt.BacktabFocusReason, Qt.ShortcutFocusReason)
        if self._kb_focus:
            area = self.parent()
            while area is not None and not hasattr(area, "ensureWidgetVisible"):
                area = area.parent()
            if area is not None:
                area.ensureWidgetVisible(self, 0, 12)
        self._refresh_actions()
        self.update()
        super().focusInEvent(ev)

    def focusOutEvent(self, ev) -> None:  # noqa: N802
        self._kb_focus = False
        self._refresh_actions()
        self.update()
        super().focusOutEvent(ev)

    def keyPressEvent(self, ev) -> None:  # noqa: N802
        key = ev.key()
        if self._plain and key in (Qt.Key_Return, Qt.Key_Enter):
            self.doubleClicked.emit(self._note_id)  # 두 번 누른 것과 같다
        elif key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.clicked.emit(self._note_id)        # 누른 것과 같다: 메모를 꺼낸다
        elif key == Qt.Key_Delete:
            self.deleteRequested.emit(self._note_id)
        elif key == Qt.Key_Menu or (key == Qt.Key_F10
                                    and ev.modifiers() & Qt.ShiftModifier):
            if self._plain:
                self.menuRequested.emit(
                    self._note_id, self.mapToGlobal(self.rect().center()))
            else:
                self._open_menu()
        elif key in (Qt.Key_Down, Qt.Key_Up):
            # 방향키로 위아래 카드를 오간다. 글쇠로 옮긴 것이므로 초점을 보인다.
            target = (self.nextInFocusChain() if key == Qt.Key_Down
                      else self.previousInFocusChain())
            while target is not None and target is not self                     and not isinstance(target, NoteCard):
                target = (target.nextInFocusChain() if key == Qt.Key_Down
                          else target.previousInFocusChain())
            if isinstance(target, NoteCard) and target is not self                     and not target.isHidden():
                target.setFocus(Qt.TabFocusReason)
        else:
            super().keyPressEvent(ev)
            return
        ev.accept()

    def _bar_color(self) -> QColor:
        base = QColor(self._color)
        return base.lighter(125) if base.lightness() < 110 else base.darker(107)

    def paintEvent(self, ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        grid = self._layout_mode == VIEW_GRID
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        dark_card = QColor(self._color).lightness() < 110
        mark = QColor("#93C5FD" if (grid and dark_card) else "#1D4ED8")
        if grid:
            radius = self._PAPER_RADIUS
            path = QPainterPath()
            path.addRoundedRect(rect, radius, radius)
            p.fillPath(path, QColor(self._color))
            p.save()
            p.setClipPath(path)
            p.fillRect(QRectF(rect.left(), rect.top(), rect.width(), self._bar_h),
                       self._bar_color())
            p.restore()
            if self._hover or self._pressed:
                edge = QColor(0, 0, 0, 90 if self._pressed else 60)
                if dark_card:
                    edge = QColor(255, 255, 255, 120 if self._pressed else 90)
                p.setPen(QPen(edge, 2))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius - 0.5, radius - 0.5)
            else:
                p.setPen(QPen(QColor(0, 0, 0, 22), 1))
                p.setBrush(Qt.NoBrush)
                p.drawPath(path)
        else:
            radius = self._ROW_RADIUS
            fill = None
            if self._selected:
                fill = QColor("#DCE5FB" if self._pressed else "#E8EEFD")
            elif self._pressed:
                fill = QColor("#E9E9E9")
            elif self._hover or self._kb_focus:
                fill = QColor("#F3F3F3")
            if fill is not None:
                p.setPen(Qt.NoPen)
                p.setBrush(fill)
                p.drawRoundedRect(rect, radius, radius)
            if not self._plain:
                # 색 네모(고르면 그 자리에 체크).
                # 줄의 위아래 가운데(글 덩이도 줄 가운데에 있다).
                cy = rect.center().y()
                side = self._box
                box = QRectF(self._box_x, cy - side / 2, side, side)
                if self._selected:
                    p.setPen(Qt.NoPen)
                    p.setBrush(mark)
                    p.drawEllipse(box.center(), side * 0.67, side * 0.67)
                    p.setPen(QPen(QColor("#FFFFFF"), 1.8, Qt.SolidLine, Qt.RoundCap,
                                  Qt.RoundJoin))
                    c = box.center()
                    p.drawLine(QPointF(c.x() - 3.6, c.y() + 0.2), QPointF(c.x() - 1.0, c.y() + 2.8))
                    p.drawLine(QPointF(c.x() - 1.0, c.y() + 2.8), QPointF(c.x() + 3.8, c.y() - 2.6))
                else:
                    light = QColor(self._color).lightness() > 225
                    p.setPen(QPen(QColor(0, 0, 0, 60), 1) if light else Qt.NoPen)
                    p.setBrush(QColor(self._color))
                    p.drawRoundedRect(box, 3, 3)
        if grid and self._selected:
            # 둥근 모서리가 깔끔하게 보이도록 선택 테두리는 약간 안쪽에 그린다.
            p.setPen(QPen(mark, 2.5))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(rect.adjusted(1, 1, -1, -1), radius - 1, radius - 1)
            if not self._plain:
                # 색에만 기대지 않도록 오른쪽 아래에 체크 표식을 둔다.
                d = 18
                cx, cy = rect.right() - 8 - d / 2, rect.bottom() - 8 - d / 2
                p.setPen(Qt.NoPen)
                p.setBrush(mark)
                p.drawEllipse(QPointF(cx, cy), d / 2, d / 2)
                p.setPen(QPen(QColor("#1F2937" if dark_card else "#FFFFFF"), 2.0,
                              Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                p.drawLine(QPointF(cx - 4.2, cy + 0.2), QPointF(cx - 1.2, cy + 3.2))
                p.drawLine(QPointF(cx - 1.2, cy + 3.2), QPointF(cx + 4.4, cy - 3.0))
        if self._kb_focus:
            ring = QColor("#FFFFFF" if (grid and dark_card) else "#111827")
            p.setPen(QPen(ring, 2.0))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(rect.adjusted(2, 2, -2, -2), radius - 2, radius - 2)
        p.end()


# 카드의 더 보기 메뉴: 앱의 메뉴 모양에서 최소 폭을 빼(글 길이만큼) 좁히고, 하위 메뉴
# 화살표가 오른쪽 끝에 붙지 않게 안쪽으로 들인다.
_CARD_MENU_QSS = (
    "QMenu { background-color: #FFFFFF; border: 1px solid #EAEAEA; "
    "border-radius: 8px; padding: 4px; color: #1F1F1F; }"
    "QMenu::item { background-color: transparent; padding: 6px 28px 6px 12px; "
    "border-radius: 6px; color: #1F1F1F; min-width: 0px; }"
    "QMenu::item:selected { background-color: #F0F0F0; color: #1F1F1F; }"
    "QMenu::item:disabled { color: #A0A0A0; background-color: transparent; }"
    "QMenu::separator { height: 1px; background: #EAEAEA; margin: 4px 6px; }"
    "QMenu::right-arrow { right: 12px; }"
)


def _mix(fg: QColor, bg: QColor, amount: float) -> QColor:
    """``fg`` 를 ``amount`` 만큼 ``bg`` 위에 얹은 불투명한 색."""
    a = max(0.0, min(1.0, amount))
    return QColor(round(fg.red() * a + bg.red() * (1 - a)),
                  round(fg.green() * a + bg.green() * (1 - a)),
                  round(fg.blue() * a + bg.blue() * (1 - a)))


class _Fade(QWidget):
    """격자 칸 아래에서 넘친 글이 메모 색으로 흐려진다."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._color = QColor("#FFFFFF")
        self.hide()

    def set_color(self, color: QColor) -> None:
        self._color = QColor(color)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        grad = QLinearGradient(0, 0, 0, self.height())
        clear = QColor(self._color)
        clear.setAlpha(0)
        grad.setColorAt(0.0, clear)
        grad.setColorAt(0.75, self._color)
        grad.setColorAt(1.0, self._color)
        # 둥근 모서리 밖은 칠하지 않는다.
        path = QPainterPath()
        r = NoteCard._PAPER_RADIUS
        path.addRoundedRect(QRectF(0.5, -r, self.width() - 1, self.height() + r - 0.5), r, r)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillPath(path, grad)


class _Veil(QWidget):
    """바탕화면에서 숨긴 메모의 격자 칸을 옅게 덮는다."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.hide()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 120))
        r = NoteCard._PAPER_RADIUS
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), r, r)


__all__ = [
    "CardFlowLayout",
    "ClampLabel",
    "GRID_MIN_COLUMN",
    "NoteCard",
    "note_title_and_lines",
]
