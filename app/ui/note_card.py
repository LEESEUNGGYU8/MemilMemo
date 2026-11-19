"""메인 창 '최근 메모' 목록의 카드와 그 배치.

보기 방식은 두 축이다. 표시 밀도(간략히·표준·자세히)는 카드 한 장에 얼마나
담을지를, 배치(리스트·그리드)는 카드를 한 줄에 몇 장 놓을지를 정한다.
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
from .rich_text import data_url_image, has_non_checkbox_image, html_to_plain, is_marker_url
from .theme import LIGHT_MENU_QSS, text_color_for_bg

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
        if not is_marker_url(m.group(1)):
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
    content = html_to_plain(raw or "")
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
        self.setFont(f)
        self.updateGeometry()
        self.update()

    def _line_height(self) -> float:
        return QFontMetricsF(self.font()).lineSpacing()

    def lines_for(self, width: int) -> list[str]:
        if not self._text or width <= 0:
            return []
        text = self._text.replace("\n", _LINE_SEP)
        layout = QTextLayout(text, self.font())
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
        out.append(QFontMetricsF(self.font()).elidedText(
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
        # 가로 줄 안에서는 줄 높이보다 큰 자리를 받으므로 세로 가운데에 그린다.
        block = len(lines) * fm.lineSpacing()
        y = max(0.0, (self.height() - block) / 2.0) + fm.ascent()
        for line in lines:
            p.drawText(QPointF(0.0, y), line)
            y += fm.lineSpacing()
        p.end()


class CardFlowLayout(QLayout):
    """카드를 같은 폭의 열로 흘려 놓는다. 열이 하나면 리스트, 여럿이면 그리드다.

    숨긴 카드(검색·폴더로 걸러진 것)는 자리를 차지하지 않는다. 같은 줄의 카드는
    그 줄에서 가장 큰 카드 높이에 맞춘다."""

    def __init__(self, parent: QWidget | None = None, spacing: int = 8):
        super().__init__(parent)
        self._items: list[QWidgetItem] = []
        self._min_col = 0  # 0 이면 한 열(리스트).
        self.setSpacing(spacing)

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
        w = (self._min_col or 120) + m.left() + m.right()
        return QSize(w, m.top() + m.bottom())

    def column_count(self, width: int) -> int:
        """안쪽 폭 ``width`` 에 놓일 열 수."""
        if not self._min_col:
            return 1
        sp = self.spacing()
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
                    it.setGeometry(QRect(x, y, col_w, row_h))
                    x += col_w + sp
            y += row_h + sp
        return (y - sp) - rect.y() + m.bottom()


class NoteCard(QFrame):
    clicked = Signal(str)
    rangeClickRequested = Signal(str)
    toggleVisibility = Signal(str)
    deleteRequested = Signal(str)
    historyRequested = Signal(str)
    privateToggleRequested = Signal(str, bool)   # note_id, new_private
    moveToFolderRequested = Signal(str, object)  # note_id, folder_id|None

    _CARD_RADIUS = 12

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
        self._density: str = VIEW_STANDARD
        self._layout_mode: str = VIEW_LIST
        self._fg: str = "#1F1F1F"

        self._root = QVBoxLayout(self)
        self._root.setSpacing(6)

        self._top = QHBoxLayout()
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

        # 간략히 보기에서는 제목이 윗줄 안에 들어간다.
        self._inline_title = ClampLabel(1)
        self._inline_title.setTextFont(13, True)
        self._inline_title.hide()

        self._timestamp = QLabel("")

        self._menu_btn = QToolButton()
        self._menu_btn.setIconSize(QSize(16, 16))
        self._menu_btn.setProperty("iconOnly", True)
        self._menu_btn.setCursor(Qt.PointingHandCursor)
        self._menu_btn.setFocusPolicy(Qt.NoFocus)
        self._menu_btn.setToolTip(i18n.t("main.tooltip_menu"))
        self._menu_btn.setAccessibleName(i18n.t("main.tooltip_menu"))
        self._menu_btn.clicked.connect(self._open_menu)

        self._top.addWidget(self._eye_btn)
        self._top.addWidget(self._image_indicator)
        self._top.addWidget(self._private_indicator)
        self._top.addWidget(self._inline_title, 1)
        self._top.addStretch(0)
        self._top.addWidget(self._timestamp)
        self._top.addWidget(self._menu_btn)
        self._root.addLayout(self._top)

        # 리스트에서는 글 옆에, 그리드에서는 글 위에 축소판을 둔다(방향만 바꾼다).
        self._mid = QBoxLayout(QBoxLayout.LeftToRight)
        self._mid.setContentsMargins(0, 0, 0, 0)
        self._mid.setSpacing(10)
        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(4)
        self._title = ClampLabel(2)
        self._title.setTextFont(14, True)
        text_col.addWidget(self._title)
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
        self._root.addLayout(self._mid)

        self._meta = ClampLabel(1)
        self._meta.setTextFont(11)
        self._meta.hide()
        self._root.addWidget(self._meta)
        self._root.addStretch(1)

        self._apply_view_metrics()
        self.update_from(note, visible)

    # ── 보기 방식 ──

    def set_view(self, density: str, layout_mode: str) -> None:
        if (density, layout_mode) == (self._density, self._layout_mode):
            return
        self._density = density
        self._layout_mode = layout_mode
        self._apply_view_metrics()
        self._render()

    def _apply_view_metrics(self) -> None:
        grid = self._layout_mode == VIEW_GRID
        compact = self._density == VIEW_COMPACT
        detailed = self._density == VIEW_DETAILED
        inline = compact and not grid

        if inline:
            self._root.setContentsMargins(10, 3, 8, 3)
        elif grid:
            self._root.setContentsMargins(12, 8, 8, 10)
        else:
            self._root.setContentsMargins(16, 12, 14, 14)
        self._root.setSpacing(4 if (grid or compact) else 6)

        self._inline_title.setVisible(inline)
        self._title.setVisible(not inline)
        self._title.setMaxLines(2)
        self._title.setTextFont(13 if grid else 14, True)
        self._body.setMaxLines(6 if detailed else 3)
        self._mid.setDirection(QBoxLayout.BottomToTop if grid
                               else QBoxLayout.LeftToRight)
        self._mid.setSpacing(6 if grid else 10)
        self._mid.setAlignment(
            self._thumb, (Qt.AlignLeft if grid else Qt.AlignTop))
        # 그리드는 폭이 좁아 시각을 윗줄이 아니라 아랫줄에 적는다.
        self._timestamp.setVisible(not grid)

        if grid:
            self.setFixedHeight({VIEW_COMPACT: 84, VIEW_STANDARD: 148,
                                 VIEW_DETAILED: 236}[self._density])
        else:
            self.setMinimumHeight({VIEW_COMPACT: 38, VIEW_STANDARD: 92,
                                   VIEW_DETAILED: 112}[self._density])
            self.setMaximumHeight(16777215)
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

        if compact:
            body = ""
        elif detailed:
            # 자세히 보기는 줄바꿈을 살려 메모의 생김새를 그대로 보인다.
            body = "\n".join(rest[:8])
            if len(body) > 600:
                body = body[:600] + "…"
        else:
            body = " ".join(rest[:3])
            if len(body) > 180:
                body = body[:180] + "…"
        self._body.setText(body)
        self._body.setVisible(bool(body))

        thumb = None
        if detailed and has_image:
            thumb = _thumbnail(_first_image_url(raw))
        if thumb is not None:
            self._thumb.setPixmap(thumb)
        self._thumb.setVisible(thumb is not None)
        if detailed:
            # 그리드 타일은 높이가 정해져 있어, 축소판이 들어가면 글 줄을 덜어 낸다.
            self._body.setMaxLines(
                (4 if thumb is not None else 8) if grid else 6)

        stamp = datetime_fmt.fmt_card_timestamp(self._updated_at)
        self._timestamp.setText(stamp)
        meta_parts: list[str] = []
        if grid and not compact:
            meta_parts.append(stamp)
        if detailed:
            user = (self._note.get("updated_by") or "").strip()
            if user:
                meta_parts.append(i18n.t("main.card_meta_edited_by", user=user))
            folder = self._folder_name()
            if folder and not grid:
                meta_parts.append(folder)
        meta = "    ".join(p for p in meta_parts if p)
        self._meta.setText(meta)
        self._meta.setVisible(bool(meta))

        self._image_indicator.setVisible(has_image)
        self._private_indicator.setVisible(self._private)
        self._apply_colors()
        self.updateGeometry()
        self.update()

    def _apply_colors(self) -> None:
        """글자와 아이콘을 메모 색에 맞춘다. 어두운 메모에서도 읽혀야 한다."""
        fg = text_color_for_bg(self._color)
        self._fg = fg
        dark = fg == "#FFFFFF"
        strong = QColor(fg)
        soft = QColor(255, 255, 255, 215) if dark else QColor(0, 0, 0, 178)
        # 11px 시각·출처 글자도 가장 옅은 메모 색 위에서 4.5:1 을 넘게 한다
        # (투명도 0.55 는 여덟 색 중 여섯에서 미달이었다).
        faint = QColor(255, 255, 255, 200) if dark else QColor(0, 0, 0, 170)
        icon = "#F2F2F2" if dark else "#4A4A4A"
        self._title.setColor(strong)
        self._inline_title.setColor(strong)
        self._body.setColor(soft)
        self._meta.setColor(faint)
        self._timestamp.setStyleSheet(
            f"color: rgba({faint.red()}, {faint.green()}, {faint.blue()}, "
            f"{faint.alpha()}); font-size: 11px; background: transparent;")
        self._menu_btn.setIcon(svg_icon("more", color=icon))
        self._image_indicator.setPixmap(
            svg_icon("image", color=icon).pixmap(14, 14))
        self._private_indicator.setPixmap(
            svg_icon("lock", color="#FFFFFF" if dark else "#1F2937")
            .pixmap(14, 14))
        self._private_indicator.setStyleSheet(
            "background: rgba(255, 255, 255, 0.18); border-radius: 6px;"
            if dark else
            "background: rgba(0, 0, 0, 0.08); border-radius: 6px;")
        hover = ("rgba(255, 255, 255, 0.16)" if dark
                 else "rgba(0, 0, 0, 0.07)")
        for b in (self._eye_btn, self._menu_btn):
            b.setStyleSheet(
                "QToolButton { background: transparent; border: none; "
                "padding: 4px; border-radius: 6px; }"
                f"QToolButton:hover {{ background: {hover}; }}")
        self._refresh_eye_icon()

    def set_visible(self, visible: bool) -> None:
        self._visible = visible
        self._refresh_eye_icon()

    def _refresh_eye_icon(self) -> None:
        name = "eye_on" if self._visible else "eye_off"
        icon = "#F2F2F2" if self._fg == "#FFFFFF" else "#4A4A4A"
        self._eye_btn.setIcon(svg_icon(name, color=icon))
        tip = i18n.t("main.tooltip_hide_from_desktop" if self._visible
                     else "main.tooltip_show_on_desktop")
        self._eye_btn.setToolTip(tip)
        self._eye_btn.setAccessibleName(tip)

    def set_folder_context(self, folders: list[tuple[str, str]]) -> None:
        self._folder_context = list(folders)
        if self._density == VIEW_DETAILED:
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
        self.update()

    def set_selected(self, selected: bool) -> None:
        if self._selected == selected:
            return
        self._selected = selected
        self.update()

    def is_selected(self) -> bool:
        return self._selected

    def _open_menu(self) -> None:
        menu = QMenu(self)
        menu.setStyleSheet(LIGHT_MENU_QSS)
        act_hist = menu.addAction(i18n.t("main.menu_edit_history"))
        act_hist.triggered.connect(
            lambda: self.historyRequested.emit(self._note_id))
        move_menu = menu.addMenu(i18n.t("main.menu_move_to_folder"))
        move_menu.setStyleSheet(LIGHT_MENU_QSS)
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
        menu.exec(self._menu_btn.mapToGlobal(
            QPoint(0, self._menu_btn.height())))

    def mousePressEvent(self, ev: QMouseEvent) -> None:  # noqa: N802
        if ev.button() == Qt.LeftButton:
            self._press_pos = ev.pos()
        super().mousePressEvent(ev)

    def mouseReleaseEvent(self, ev: QMouseEvent) -> None:  # noqa: N802
        if ev.button() == Qt.LeftButton and hasattr(self, "_press_pos"):
            if (ev.pos() - self._press_pos).manhattanLength() < 5:
                # 눈/메뉴 미니 버튼 위 클릭은 카드 클릭으로 처리하지 않는다.
                child = self.childAt(ev.pos())
                if child not in (self._eye_btn, self._menu_btn):
                    if (self._selection_mode
                            and ev.modifiers() & Qt.ShiftModifier):
                        self.rangeClickRequested.emit(self._note_id)
                    else:
                        self.clicked.emit(self._note_id)
        super().mouseReleaseEvent(ev)

    def contextMenuEvent(self, ev) -> None:  # noqa: N802
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
        self.update()
        super().focusInEvent(ev)

    def focusOutEvent(self, ev) -> None:  # noqa: N802
        self._kb_focus = False
        self.update()
        super().focusOutEvent(ev)

    def keyPressEvent(self, ev) -> None:  # noqa: N802
        key = ev.key()
        if key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.clicked.emit(self._note_id)        # 누른 것과 같다: 메모를 꺼낸다
        elif key == Qt.Key_Delete:
            self.deleteRequested.emit(self._note_id)
        elif key == Qt.Key_Menu or (key == Qt.Key_F10
                                    and ev.modifiers() & Qt.ShiftModifier):
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

    def paintEvent(self, ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(0, 0, -1, -1)
        radius = 9 if (self._density == VIEW_COMPACT
                       and self._layout_mode == VIEW_LIST) else self._CARD_RADIUS
        p.setPen(QPen(QColor(0, 0, 0, 25), 1))
        p.setBrush(QColor(self._color))
        p.drawRoundedRect(rect, radius, radius)
        # 둥근 모서리가 깔끔하게 보이도록 선택 테두리는 약간 inset 으로 그린다.
        if self._selected:
            dark_card = QColor(self._color).lightness() < 110
            mark = QColor("#93C5FD" if dark_card else "#1D4ED8")
            p.setPen(QPen(mark, 2.5))
            p.setBrush(Qt.NoBrush)
            inset = rect.adjusted(1, 1, -1, -1)
            p.drawRoundedRect(inset, radius - 1, radius - 1)
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
            dark_card = QColor(self._color).lightness() < 110
            p.setPen(QPen(QColor("#FFFFFF" if dark_card else "#111827"), 2.0))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(rect.adjusted(3, 3, -3, -3), radius - 3, radius - 3)
        p.end()


__all__ = [
    "CardFlowLayout",
    "ClampLabel",
    "GRID_MIN_COLUMN",
    "NoteCard",
    "note_title_and_lines",
]
