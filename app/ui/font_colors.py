"""색 고르기: 색조별로 늘어놓은 견본, 글자 색을 고르는 판, 색상 코드를 넣는 창.

글자 색과 메모 색이 함께 쓴다.

- 견본(``PALETTE``)은 열이 색조, 줄이 밝기다. 맨 왼쪽 열은 흰색에서 검정까지의
  무채색이다. 칸은 색만 칠한 네모다.
- 글자 색 단추를 누르면 견본과 최근 색, '기본 색', '다른 색'이 든 판이 뜬다
  (``FontColorPanel``).
- '다른 색'은 창이다(``ColorPicker``). 견본, 채도와 밝기를 고르는 판, 색조 띠,
  색상 코드 칸이 있다. 코드를 직접 쳐 넣을 수 있다. 여기서 고른 색은 최근 색으로
  남는다. 메모 색의 '다른 색'도 같은 창이다.

어떤 색이든 고를 수 있다. 다만 바탕에서 사실상 보이지 않는 글자 색(명암비 2 미만)은
같은 색조에서 읽히는 밝기로 옮긴다(``rescue``, app/ui/rich_text.py 의
``ensure_readable`` 이 부른다). 메모 색을 밝은 것에서 어두운 것으로 바꿨을 때
검게 써 둔 글자가 사라지지 않게 하려는 것이다."""
from __future__ import annotations

import re
import unicodedata

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QIcon,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
    QValidator,
)
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from .. import i18n
from .theme import refresh_style

# 열마다의 색조. 화면에 읽히는 이름은 ``ink.<이름>`` 이다.
HUES = ("gray", "red", "orange", "yellow", "green", "teal", "sky", "blue",
        "purple", "pink")
# 색조마다 밝은 것에서 어두운 것까지 열 단계.
_RAMPS = {
    "gray": ("#FFFFFF", "#E5E5E5", "#D4D4D4", "#A3A3A3", "#737373", "#525252",
             "#404040", "#262626", "#171717", "#000000"),
    "red": ("#FEE2E2", "#FECACA", "#FCA5A5", "#F87171", "#EF4444", "#DC2626",
            "#B91C1C", "#991B1B", "#7F1D1D", "#450A0A"),
    "orange": ("#FFEDD5", "#FED7AA", "#FDBA74", "#FB923C", "#F97316", "#EA580C",
               "#C2410C", "#9A3412", "#7C2D12", "#431407"),
    "yellow": ("#FEF9C3", "#FEF08A", "#FDE047", "#FACC15", "#EAB308", "#CA8A04",
               "#A16207", "#854D0E", "#713F12", "#422006"),
    "green": ("#DCFCE7", "#BBF7D0", "#86EFAC", "#4ADE80", "#22C55E", "#16A34A",
              "#15803D", "#166534", "#14532D", "#052E16"),
    "teal": ("#CCFBF1", "#99F6E4", "#5EEAD4", "#2DD4BF", "#14B8A6", "#0D9488",
             "#0F766E", "#115E59", "#134E4A", "#042F2E"),
    "sky": ("#E0F2FE", "#BAE6FD", "#7DD3FC", "#38BDF8", "#0EA5E9", "#0284C7",
            "#0369A1", "#075985", "#0C4A6E", "#082F49"),
    "blue": ("#DBEAFE", "#BFDBFE", "#93C5FD", "#60A5FA", "#3B82F6", "#2563EB",
             "#1D4ED8", "#1E40AF", "#1E3A8A", "#172554"),
    "purple": ("#F3E8FF", "#E9D5FF", "#D8B4FE", "#C084FC", "#A855F7", "#9333EA",
               "#7E22CE", "#6B21A8", "#581C87", "#3B0764"),
    "pink": ("#FCE7F3", "#FBCFE8", "#F9A8D4", "#F472B6", "#EC4899", "#DB2777",
             "#BE185D", "#9D174D", "#831843", "#500724"),
}
TONES = len(_RAMPS["gray"])
# 화면에 놓이는 차례: 줄이 밝기, 열이 색조.
PALETTE: tuple[tuple[str, ...], ...] = tuple(
    tuple(_RAMPS[hue][tone] for hue in HUES) for tone in range(TONES))

RECENT_MAX = 10
# 이보다 낮은 명암비의 글자 색은 바탕에서 사실상 보이지 않는다.
MIN_CONTRAST = 2.0
# 보이지 않는 색을 옮길 때 맞추는 명암비.
RESCUE_CONTRAST = 3.0
CELL, GAP = 18, 2
_HEX = re.compile(r"#?([0-9A-Fa-f]{6}|[0-9A-Fa-f]{3})")
_HEX_DIGITS = "0123456789ABCDEF"
# 색 고르는 창의 오른쪽: 판과 띠 사이의 틈, 띠의 높이.
_SIDE_GAP, _HUE_HEIGHT = 8, 16


def code_digits(text: str) -> str:
    """친 글에서 색상 코드에 쓰는 글자만 남긴다(대문자, 여섯 자까지). 전각 영문자와
    숫자는 반각으로 바꿔 받는다. 그 밖의 글자는 버린다."""
    plain = unicodedata.normalize("NFKC", text or "").upper()
    return "".join(ch for ch in plain if ch in _HEX_DIGITS)[:6]


class CodeValidator(QValidator):
    """색상 코드 칸에 들어가는 글을 '#' 과 코드 글자 여섯 자까지로 고쳐 둔다.
    '#' 은 지워도 다시 붙는다."""

    def validate(self, text: str, pos: int):
        digits = code_digits(text)
        before = len(code_digits(text[:pos]))
        state = (QValidator.Acceptable if len(digits) == 6
                 else QValidator.Intermediate)
        return state, "#" + digits, 1 + min(before, len(digits))


class CodeEdit(QLineEdit):
    """색상 코드를 적는 칸."""

    entered = Signal()              # Enter 를 눌렀다

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setValidator(CodeValidator(self))
        self.setFixedWidth(104)
        # 색상 코드는 영문자와 숫자뿐이다. 한글 입력 상태여도 그대로 쳐지게 한다.
        self.setAttribute(Qt.WA_InputMethodEnabled, False)
        self.cursorPositionChanged.connect(self._keep_after_hash)

    def _keep_after_hash(self, _old: int, new: int) -> None:
        # 커서가 '#' 앞으로 가지 않게 한다(고른 범위가 있을 때는 둔다).
        if new == 0 and not self.hasSelectedText() and self.text().startswith("#"):
            self.setCursorPosition(1)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            # 다 치지 않은 코드로 창이 닫히지 않게, 여기서 받고 넘기지 않는다.
            self.entered.emit()
            event.accept()
            return
        super().keyPressEvent(event)


def _lum(c: QColor) -> float:
    def ch(v: float) -> float:
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(c.redF()) + 0.7152 * ch(c.greenF()) + 0.0722 * ch(c.blueF())


def contrast(a: QColor, b: QColor) -> float:
    la, lb = _lum(a), _lum(b)
    if la < lb:
        la, lb = lb, la
    return (la + 0.05) / (lb + 0.05)


def is_dark_ground(ground: QColor) -> bool:
    """글자가 놓이는 바탕이 어두운가(흰 글자를 올리는 메모 색과 같은 기준)."""
    return (0.299 * ground.red() + 0.587 * ground.green()
            + 0.114 * ground.blue()) / 255.0 < 0.55


def parse_code(text: str) -> QColor | None:
    """색상 코드(#RRGGBB, RRGGBB, #RGB)를 색으로. 코드가 아니면 None."""
    match = _HEX.fullmatch((text or "").strip())
    if match is None:
        return None
    digits = match.group(1)
    if len(digits) == 3:
        digits = "".join(ch * 2 for ch in digits)
    return QColor("#" + digits)


def code_of(color: QColor) -> str:
    return color.name().upper()


def hue_name(column: int) -> str:
    return i18n.t(f"ink.{HUES[column]}")


def explicit_ink(fmt) -> QColor | None:
    """그 글자에 따로 정해 둔 색. 기본 색이면 None."""
    brush = fmt.foreground()
    if brush.style() == Qt.NoBrush:
        return None
    return QColor(brush.color())


def clean_recents(values) -> list[str]:
    """설정 파일에서 읽은 최근 색. 색이 아닌 것은 버린다."""
    out: list[str] = []
    for value in values if isinstance(values, (list, tuple)) else ():
        color = parse_code(value) if isinstance(value, str) else None
        if color is not None and len(value.strip()) == 7:
            name = code_of(color)
            if name not in out:
                out.append(name)
    return out[:RECENT_MAX]


def remember(recents: list[str], color: QColor) -> list[str]:
    """방금 고른 색을 맨 앞에 둔 최근 색."""
    name = code_of(color)
    return ([name] + [c for c in recents if c != name])[:RECENT_MAX]


def in_palette(color: QColor) -> bool:
    name = code_of(color)
    return any(name in row for row in PALETTE)


def rescue(ink: QColor, ground: QColor) -> QColor:
    """바탕에서 보이지 않는 글자 색을 같은 색조에서 읽히는 색으로 옮긴다.

    견본의 색이면 같은 열에서 가장 가까운 읽히는 칸으로, 아니면 색조와 채도는 두고
    밝기만 옮긴다. 무채색은 검정이나 흰색이 된다."""
    name = code_of(ink)
    for hue in HUES[1:]:
        ramp = _RAMPS[hue]
        if name in ramp:
            at = ramp.index(name)
            for tone in sorted(range(TONES), key=lambda t: abs(t - at)):
                if contrast(QColor(ramp[tone]), ground) >= RESCUE_CONTRAST:
                    return QColor(ramp[tone])
            break
    dark = is_dark_ground(ground)
    hue_f, saturation, light, _alpha = ink.getHslF()
    if hue_f < 0 or saturation < 0.08:
        return QColor("#FFFFFF" if dark else "#1F1F1F")
    near, far = light, (1.0 if dark else 0.0)
    for _ in range(12):
        middle = (near + far) / 2.0
        if contrast(QColor.fromHslF(hue_f, saturation, middle),
                    ground) >= RESCUE_CONTRAST:
            far = middle
        else:
            near = middle
    return QColor.fromHslF(hue_f, saturation, far)


def swatch_icon(color: QColor, side: int = 18) -> QIcon:
    """색만 칠한 네모(단추에 붙이는 그림)."""
    icon = QIcon()
    for scale in (1, 2):
        pix = QPixmap(side * scale, side * scale)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.scale(scale, scale)
        _paint_cell(p, QRectF(1.0, 1.0, side - 2.0, side - 2.0), color)
        p.end()
        pix.setDevicePixelRatio(scale)
        icon.addPixmap(pix)
    return icon


def _paint_cell(p: QPainter, rect: QRectF, color: QColor) -> None:
    p.setRenderHint(QPainter.Antialiasing)
    # 흰색에 가까운 칸은 흰 바탕과 섞여 보이지 않는다. 옅은 테두리를 두른다.
    edge = QColor(0, 0, 0, 46) if _lum(color) > 0.72 else QColor(0, 0, 0, 0)
    p.setPen(QPen(edge, 1.0) if edge.alpha() else Qt.NoPen)
    p.setBrush(color)
    p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 3.0, 3.0)


class SwatchGrid(QWidget):
    """색만 칠한 네모들. ``rows`` 는 줄마다의 색상 코드다."""

    picked = Signal(object)         # QColor

    def __init__(self, rows, parent: QWidget | None = None, *,
                 names: bool = False):
        super().__init__(parent)
        self._rows = [list(row) for row in rows]
        self._names = names         # 열의 이름(색조)을 말풍선에 붙이는가
        self._current = ""
        self._hover: tuple[int, int] | None = None
        self._at: tuple[int, int] = (0, 0)
        columns = max((len(row) for row in self._rows), default=0)
        self.setFixedSize(columns * (CELL + GAP) + GAP,
                          len(self._rows) * (CELL + GAP) + GAP)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)

    def colors(self) -> list[str]:
        return [code for row in self._rows for code in row]

    def set_current(self, color: QColor | None) -> None:
        """지금 골라져 있는 색(테두리를 두른다). 견본에 없는 색이면 아무 칸도 아니다."""
        name = code_of(color) if color is not None else ""
        if name != self._current:
            self._current = name
            self.update()
        for r, row in enumerate(self._rows):
            if name in row:
                self._at = (r, row.index(name))

    def current(self) -> str:
        return self._current

    def _rect(self, row: int, column: int) -> QRectF:
        return QRectF(GAP + column * (CELL + GAP), GAP + row * (CELL + GAP),
                      CELL, CELL)

    def cell_center(self, row: int, column: int) -> QPointF:
        return self._rect(row, column).center()

    def _cell_at(self, pos) -> tuple[int, int] | None:
        column = int((pos.x() - GAP / 2) // (CELL + GAP))
        row = int((pos.y() - GAP / 2) // (CELL + GAP))
        if 0 <= row < len(self._rows) and 0 <= column < len(self._rows[row]):
            return row, column
        return None

    def _pick(self, cell: tuple[int, int]) -> None:
        row, column = cell
        self._at = cell
        self.picked.emit(QColor(self._rows[row][column]))

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        cell = self._cell_at(event.position())
        if cell != self._hover:
            self._hover = cell
            self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = None
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        cell = self._cell_at(event.position())
        if event.button() == Qt.LeftButton and cell is not None:
            self._pick(cell)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        row, column = self._at
        step = {Qt.Key_Left: (0, -1), Qt.Key_Right: (0, 1), Qt.Key_Up: (-1, 0),
                Qt.Key_Down: (1, 0)}.get(event.key())
        if step is not None and self._rows:
            row = max(0, min(len(self._rows) - 1, row + step[0]))
            column = max(0, min(len(self._rows[row]) - 1, column + step[1]))
            self._at = (row, column)
            self.update()
        elif event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space) and self._rows:
            self._pick(self._at)
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def focusInEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().focusOutEvent(event)

    def tip_at(self, pos) -> str:
        cell = self._cell_at(pos)
        if cell is None:
            return ""
        code = self._rows[cell[0]][cell[1]]
        return f"{hue_name(cell[1])} {code}" if self._names else code

    def event(self, event) -> bool:
        if event.type() == QEvent.ToolTip:
            tip = self.tip_at(event.pos())
            if tip:
                QToolTip.showText(event.globalPos(), tip, self)
            else:
                QToolTip.hideText()
            return True
        return super().event(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        marks: list[tuple[QRectF, QColor, float]] = []
        for r, row in enumerate(self._rows):
            for c, code in enumerate(row):
                rect = self._rect(r, c)
                _paint_cell(p, rect, QColor(code))
                if code == self._current:
                    marks.append((rect, QColor("#1F1F1F"), 2.0))
                elif self.hasFocus() and (r, c) == self._at:
                    marks.append((rect, QColor("#2563EB"), 2.0))
                elif (r, c) == self._hover:
                    marks.append((rect, QColor(0, 0, 0, 120), 1.5))
        # 테두리는 칸 밖으로 두른다(색을 가리지 않게). 칸 위에 흰 띠를 먼저 깐다.
        for rect, color, width in marks:
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor("#FFFFFF"), width + 2.0))
            p.drawRoundedRect(rect, 3.5, 3.5)
            p.setPen(QPen(color, width))
            p.drawRoundedRect(rect.adjusted(-1.0, -1.0, 1.0, 1.0), 4.5, 4.5)
        p.end()


def _text_button(text: str, parent: QWidget) -> QToolButton:
    button = QToolButton(parent)
    button.setText(text)
    button.setCursor(Qt.PointingHandCursor)
    button.setFocusPolicy(Qt.StrongFocus)
    button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
    button.setFixedHeight(28)
    button.setStyleSheet(
        "QToolButton { border: none; padding: 2px 8px 2px 4px; border-radius: 6px; "
        "background: transparent; color: #1F1F1F; font-size: 12px; }"
        "QToolButton:hover { background: rgba(0,0,0,0.07); }"
        "QToolButton:checked { background: rgba(0,0,0,0.10); }"
        "QToolButton:focus { background: rgba(37, 99, 235, 0.16); outline: none; }")
    return button


def more_button(parent: QWidget) -> QToolButton:
    """'다른 색' 단추(글자 색 판과 메모 색 메뉴가 함께 쓴다)."""
    return _text_button(i18n.t("sticky.font_color_more"), parent)


def rule(parent: QWidget) -> QFrame:
    line = QFrame(parent)
    line.setFixedHeight(1)
    line.setStyleSheet("background: rgba(0,0,0,0.08);")
    return line


def caption(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet("color: #6B7280; font-size: 11px;")
    return label


class FontColorPanel(QWidget):
    """글자 색을 고르는 판: 견본, 최근 색, 기본 색과 '다른 색'."""

    picked = Signal(object)         # QColor. 기본 색이면 None
    moreRequested = Signal()

    def __init__(self, default_ink: QColor, current: QColor | None,
                 recents: list[str], parent: QWidget | None = None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 6)
        root.setSpacing(6)
        self.grid = SwatchGrid(PALETTE, self, names=True)
        self.grid.set_current(current)
        self.grid.picked.connect(self.picked.emit)
        root.addWidget(self.grid)

        self.recent: SwatchGrid | None = None
        if recents:
            root.addWidget(caption(i18n.t("sticky.font_color_recent")))
            self.recent = SwatchGrid([recents[:RECENT_MAX]], self)
            self.recent.set_current(current)
            self.recent.picked.connect(self.picked.emit)
            root.addWidget(self.recent)

        root.addWidget(rule(self))
        foot = QHBoxLayout()
        foot.setContentsMargins(0, 0, 0, 0)
        foot.setSpacing(4)
        self.default_button = _text_button(i18n.t("sticky.font_color_default"), self)
        self.default_button.setIcon(swatch_icon(QColor(default_ink)))
        self.default_button.setIconSize(QSize(18, 18))
        self.default_button.setCheckable(True)
        self.default_button.setChecked(current is None)
        self.default_button.clicked.connect(lambda: self.picked.emit(None))
        foot.addWidget(self.default_button)
        foot.addStretch(1)
        self.more_button = more_button(self)
        self.more_button.clicked.connect(self.moreRequested.emit)
        foot.addWidget(self.more_button)
        root.addLayout(foot)

    def first_focus(self) -> QWidget:
        return self.grid


class _SatVal(QWidget):
    """한 색조 안에서 채도(가로)와 밝기(세로)를 고르는 판. 왼쪽 위가 흰색, 아래가
    검정이다."""

    changed = Signal(float, float)

    def __init__(self, parent: QWidget | None = None, height: int = 150):
        super().__init__(parent)
        self.setFixedSize(208, height)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.CrossCursor)
        self._hue, self._sat, self._val = 0.0, 1.0, 1.0

    def set_hsv(self, hue: float, sat: float, val: float) -> None:
        self._hue, self._sat, self._val = hue, sat, val
        self.update()

    def _take(self, pos) -> None:
        self._sat = min(1.0, max(0.0, pos.x() / (self.width() - 1)))
        self._val = 1.0 - min(1.0, max(0.0, pos.y() / (self.height() - 1)))
        self.update()
        self.changed.emit(self._sat, self._val)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._take(event.position())

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if event.buttons() & Qt.LeftButton:
            self._take(event.position())

    def keyPressEvent(self, event) -> None:  # noqa: N802
        step = {Qt.Key_Left: (-0.02, 0.0), Qt.Key_Right: (0.02, 0.0),
                Qt.Key_Up: (0.0, 0.02), Qt.Key_Down: (0.0, -0.02)}.get(event.key())
        if step is None:
            super().keyPressEvent(event)
            return
        self._sat = min(1.0, max(0.0, self._sat + step[0]))
        self._val = min(1.0, max(0.0, self._val + step[1]))
        self.update()
        self.changed.emit(self._sat, self._val)
        event.accept()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect())
        across = QLinearGradient(rect.topLeft(), rect.topRight())
        across.setColorAt(0.0, QColor("#FFFFFF"))
        across.setColorAt(1.0, QColor.fromHsvF(self._hue, 1.0, 1.0))
        down = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        down.setColorAt(0.0, QColor(0, 0, 0, 0))
        down.setColorAt(1.0, QColor(0, 0, 0, 255))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(across))
        p.drawRoundedRect(rect, 6.0, 6.0)
        p.setBrush(QBrush(down))
        p.drawRoundedRect(rect, 6.0, 6.0)
        at = QPointF(self._sat * (self.width() - 1),
                     (1.0 - self._val) * (self.height() - 1))
        p.setBrush(QColor.fromHsvF(self._hue, self._sat, self._val))
        p.setPen(QPen(QColor("#FFFFFF"), 2.0))
        p.drawEllipse(at, 6.0, 6.0)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(0, 0, 0, 130), 1.0))
        p.drawEllipse(at, 7.2, 7.2)
        if self.hasFocus():
            p.setPen(QPen(QColor("#2563EB"), 2.0))
            p.drawRoundedRect(rect.adjusted(1.0, 1.0, -1.0, -1.0), 5.0, 5.0)
        p.end()


class _HueBar(QWidget):
    """색조를 고르는 띠."""

    changed = Signal(float)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(208, _HUE_HEIGHT)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self._hue = 0.0

    def set_hue(self, hue: float) -> None:
        self._hue = min(0.999, max(0.0, hue))
        self.update()

    def _take(self, pos) -> None:
        self.set_hue(pos.x() / (self.width() - 1) * 0.999)
        self.changed.emit(self._hue)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._take(event.position())

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if event.buttons() & Qt.LeftButton:
            self._take(event.position())

    def keyPressEvent(self, event) -> None:  # noqa: N802
        step = {Qt.Key_Left: -0.01, Qt.Key_Right: 0.01}.get(event.key())
        if step is None:
            super().keyPressEvent(event)
            return
        self.set_hue(self._hue + step)
        self.changed.emit(self._hue)
        event.accept()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.0, 3.0, 0.0, -3.0)
        band = QLinearGradient(rect.topLeft(), rect.topRight())
        for stop in range(13):
            band.setColorAt(stop / 12.0,
                            QColor.fromHsvF(min(0.999, stop / 12.0), 1.0, 1.0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(band))
        p.drawRoundedRect(rect, 5.0, 5.0)
        x = min(self.width() - 8.0,
                max(8.0, self._hue / 0.999 * (self.width() - 1)))
        knob = QPointF(x, self.height() / 2.0)
        p.setBrush(QColor.fromHsvF(self._hue, 1.0, 1.0))
        p.setPen(QPen(QColor("#FFFFFF"), 2.0))
        p.drawEllipse(knob, 7.0, 7.0)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor("#2563EB") if self.hasFocus() else QColor(0, 0, 0, 130),
                      1.0))
        p.drawEllipse(knob, 8.0, 8.0)
        p.end()


class ColorPicker(QDialog):
    """어떤 색이든 고르는 창: 견본, 채도와 밝기의 판, 색조 띠, 색상 코드."""

    def __init__(self, title: str, initial: QColor, recents: list[str] | None = None,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self._color = QColor(initial) if initial.isValid() else QColor("#1F1F1F")
        self._hue = 0.0

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 12)
        root.setSpacing(12)
        body = QHBoxLayout()
        body.setSpacing(14)

        left = QVBoxLayout()
        left.setSpacing(6)
        self.grid = SwatchGrid(PALETTE, self, names=True)
        self.grid.picked.connect(self.set_color)
        left.addWidget(self.grid)
        self.recent: SwatchGrid | None = None
        if recents:
            left.addWidget(caption(i18n.t("sticky.font_color_recent")))
            self.recent = SwatchGrid([recents[:RECENT_MAX]], self)
            self.recent.picked.connect(self.set_color)
            left.addWidget(self.recent)
        left.addStretch(1)
        body.addLayout(left)

        # 오른쪽의 위아래를 왼쪽 견본의 첫 줄 위와 끝 줄 아래에 맞춘다(견본의
        # 칸은 제 틀에서 GAP 만큼 안쪽에 있다). 최근 색은 그 아래에 따로 온다.
        right = QVBoxLayout()
        right.setContentsMargins(0, GAP, 0, 0)
        right.setSpacing(_SIDE_GAP)
        # 코드 칸의 높이는 앱의 입력 칸 모양이 정한다. 그 높이를 재서 판의 높이를
        # 거기에 맞춘다.
        self.code = CodeEdit(self)
        self.code.ensurePolished()
        code_height = max(self.code.sizeHint().height(),
                          self.code.minimumSizeHint().height())
        self.code.setFixedHeight(code_height)
        self.field = _SatVal(self, self.grid.height() - 2 * GAP - 2 * _SIDE_GAP
                             - _HUE_HEIGHT - code_height)
        self.field.changed.connect(self._from_field)
        right.addWidget(self.field)
        self.hue = _HueBar(self)
        self.hue.changed.connect(self._from_hue)
        right.addWidget(self.hue)
        line = QHBoxLayout()
        line.setSpacing(8)
        self.preview = QLabel()
        self.preview.setFixedSize(code_height, code_height)
        line.addWidget(self.preview)
        self.code.setAccessibleName(i18n.t("color.code"))
        self.code.setToolTip(i18n.t("color.code"))
        self.code.textEdited.connect(self._from_code)
        self.code.entered.connect(self._accept_code)
        line.addWidget(self.code)
        line.addStretch(1)
        right.addLayout(line)
        right.addStretch(1)
        body.addLayout(right)
        root.addLayout(body)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        self.apply_button = QPushButton(i18n.t("color.btn_apply"))
        self.apply_button.setMinimumWidth(96)
        self.apply_button.setCursor(Qt.PointingHandCursor)
        self.apply_button.setDefault(True)
        self.apply_button.clicked.connect(self.accept)
        cancel = QPushButton(i18n.t("common.btn_cancel"))
        cancel.setProperty("secondary", True)
        refresh_style(cancel)
        cancel.setMinimumWidth(96)
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(self.apply_button)
        buttons.addWidget(cancel)
        root.addLayout(buttons)

        self.set_color(self._color)
        self.setFixedSize(self.sizeHint())

    def color(self) -> QColor:
        return QColor(self._color)

    def set_color(self, color: QColor, *, keep_code: bool = False) -> None:
        self._color = QColor(color)
        hue, sat, val, _alpha = self._color.getHsvF()
        if hue >= 0:
            self._hue = min(0.999, hue)     # 무채색은 색조가 없다. 보던 것을 둔다
        self.hue.set_hue(self._hue)
        self.field.set_hsv(self._hue, max(0.0, sat), val)
        self._show(keep_code=keep_code)

    def _show(self, *, keep_code: bool = False) -> None:
        if not keep_code:
            self.code.setText(code_of(self._color))
        self.preview.setStyleSheet(
            f"background: {self._color.name()}; border: 1px solid rgba(0,0,0,0.18); "
            "border-radius: 6px;")
        self.grid.set_current(self._color)
        if self.recent is not None:
            self.recent.set_current(self._color)

    def _from_field(self, sat: float, val: float) -> None:
        self._color = QColor.fromHsvF(self._hue, sat, val)
        self._show()

    def _from_hue(self, hue: float) -> None:
        self._hue = hue
        _h, sat, val, _alpha = self._color.getHsvF()
        self.field.set_hsv(hue, max(0.0, sat), val)
        self._color = QColor.fromHsvF(hue, max(0.0, sat), val)
        self._show()

    def _from_code(self, text: str) -> None:
        # 여섯 자를 다 쳤을 때만 색이 바뀐다. 치고 있는 글자는 건드리지 않는다.
        if len(code_digits(text)) == 6:
            self.set_color(QColor("#" + code_digits(text)), keep_code=True)

    def _accept_code(self) -> None:
        # 다 치지 않은 코드에서 Enter 를 누르면 아무 일도 없다.
        if len(code_digits(self.code.text())) == 6:
            self.set_color(QColor("#" + code_digits(self.code.text())))
            self.accept()


def ask_color(parent: QWidget | None, title: str, initial: QColor,
              recents: list[str] | None = None) -> QColor | None:
    """색 고르는 창을 띄운다. 고르지 않고 닫으면 None(시험에서는 이 함수를 바꿔
    끼운다)."""
    dialog = ColorPicker(title, initial, recents, parent)
    try:
        return dialog.color() if dialog.exec() == QDialog.Accepted else None
    finally:
        dialog.deleteLater()


__all__ = [
    "CodeEdit", "CodeValidator", "ColorPicker", "FontColorPanel", "HUES",
    "MIN_CONTRAST", "PALETTE", "code_digits",
    "RECENT_MAX", "SwatchGrid", "TONES", "ask_color", "caption", "clean_recents",
    "code_of", "contrast", "explicit_ink", "hue_name", "in_palette",
    "is_dark_ground", "more_button", "parse_code", "remember", "rescue", "rule",
    "swatch_icon",
]
