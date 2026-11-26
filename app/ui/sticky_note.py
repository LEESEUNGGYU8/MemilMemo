from __future__ import annotations

import ctypes
import math
import platform
import re
from typing import Any

from PySide6.QtCore import (
    QCoreApplication, QEasingCurve, QEvent, QMimeData, QObject, QPoint, QPointF,
    QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal, QUrl,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QIcon,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QShortcut,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextFormat,
    QTextFrameFormat,
    QTextImageFormat,
    QTextLength,
    QTextTableFormat,
    QImage,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizeGrip,
    QSizePolicy,
    QSlider,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from .. import config, desktop_icons, html_guard, i18n
from .. import file_links as fl
from ..image_export import (
    open_data_url_externally, parse_data_url, sniff_extension,
)
from ..settings import IMAGE_VIEWER_SYSTEM
from . import dwm, edit_menu, font_colors
from . import note_fold as nf
from .icons import color_swatch_icon, svg_icon, text_color_icon
from .image_viewer import ImageViewerWindow
from .link_dialog import LinkDialog, kind_label
from .link_icons import link_icon
from .link_open import open_link, reveal_in_folder
from .menu_item import add_centered_menu_action
from .note_card import note_title_and_lines
from .rich_text import (
    BULLET_CODES,
    CHECKBOX_HEIGHT,
    CHECKBOX_WIDTH,
    LEVEL_PROP,
    LINK_COLOR_DARK,
    LINK_COLOR_LIGHT,
    MARKER_ARROW,
    MARKER_CHECK0,
    MARKER_CHECK1,
    MARKER_CIRCLE,
    MARKER_CODES,
    MARKER_INFO,
    MARKER_NONE,
    MARKER_NUMBER,
    MARKER_PROP,
    MARKER_SQUARE,
    MAX_LEVEL,
    NUMBER_ANCHOR,
    SPACING_PROP,
    RichTextEdit,
    copied_format,
    data_url_image,
    list_menu_icon,
    html_to_plain,
    marker_code_for_url,
    marker_url_for_code,
    set_copied_format,
    strip_link_icons,
)
from .theme import COLOR_FOCUS, RoundedScrollBar, color_name
from . import widget_object as wobj
from .widget_block import WidgetBlock, wants_setup


# 바탕 화면과 앱 창 사이 레이어는 WindowStaysOnTopHint 로 만들 수 없어
# Windows Z-order 최하단에 핀한다.
def _pin_window_to_bottom(hwnd: int) -> None:
    if platform.system() != "Windows" or hwnd == 0:
        return
    HWND_BOTTOM = 1
    SWP_NOMOVE = 0x0002
    SWP_NOSIZE = 0x0001
    SWP_NOACTIVATE = 0x0010
    try:
        ctypes.windll.user32.SetWindowPos(
            hwnd, HWND_BOTTOM, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        )
    except Exception:
        pass


def _set_window_topmost(hwnd: int, on: bool) -> None:
    """가장자리에 붙인 탭은 다른 창을 쓰는 중에도 꺼내 볼 수 있어야 하므로
    항상 위에 둔다. 펼치거나 떼면 일반 창으로 되돌린다(그때 보통 창들 가운데
    맨 앞에 서고, 다른 창을 누르면 뒤로 간다)."""
    if platform.system() != "Windows" or hwnd == 0:
        return
    HWND_TOPMOST = -1
    HWND_NOTOPMOST = -2
    SWP_NOMOVE = 0x0002
    SWP_NOSIZE = 0x0001
    SWP_NOACTIVATE = 0x0010
    try:
        ctypes.windll.user32.SetWindowPos(
            ctypes.c_void_p(hwnd),
            ctypes.c_void_p(HWND_TOPMOST if on else HWND_NOTOPMOST),
            0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        )
    except Exception:
        pass


class _AttachGhost(QWidget):
    """메모를 다른 창의 가장자리로 끄는 동안, 놓으면 붙을 그 창의 테두리를 보인다."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint
                         | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus
                         | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

    def outline(self, rect: QRect, *, leaving: bool = False) -> None:
        """``leaving`` 이면 놓으면 떨어진다는 뜻의 회색 점선."""
        self._leaving = leaving
        if self.geometry() != rect:
            self.setGeometry(rect)
        if not self.isVisible():
            self.show()
        self.update()

    _leaving = False

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        dwm.set_bare(int(self.winId()))

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self._leaving:
            p.setPen(QPen(QColor("#8A8A8A"), 2, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
        else:
            p.setPen(QPen(QColor(COLOR_FOCUS), 3))
            fill = QColor(COLOR_FOCUS)
            fill.setAlphaF(0.06)
            p.setBrush(fill)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5), 8, 8)
        p.end()


class _DockGhost(QWidget):
    """펼친 메모를 화면 벽으로 끌었을 때, 놓으면 생길 탭을 미리 보인다."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint
                         | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus
                         | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self._face: tuple | None = None

    def preview(self, rect: QRect, color: str, title: str, side: str,
                private: bool, font: QFont) -> None:
        self._face = (color, title, side, private, QFont(font))
        self.setGeometry(rect)
        if not self.isVisible():
            self.show()
        self.update()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        dwm.set_bare(int(self.winId()))

    def paintEvent(self, event) -> None:  # noqa: N802
        if self._face is None:
            return
        color, title, side, private, font = self._face
        painter = QPainter(self)
        painter.setOpacity(0.78)
        nf.paint_slip(painter, QRectF(self.rect()), color, title, dock=side,
                      private=private, font=font)
        painter.end()


def _make_format_button(icon: QIcon, tip: str, checkable: bool = False) -> QToolButton:
    b = QToolButton()
    b.setIcon(icon)
    b.setIconSize(QSize(16, 16))
    b.setToolTip(tip)
    b.setCursor(Qt.PointingHandCursor)
    b.setFocusPolicy(Qt.NoFocus)
    b.setAutoRaise(True)
    b.setCheckable(checkable)
    b.setStyleSheet(
        "QToolButton { border: none; padding: 5px; border-radius: 5px; background: transparent; }"
        "QToolButton:hover { background: rgba(0,0,0,0.08); }"
        "QToolButton:checked { background: rgba(0,0,0,0.14); }"
        "QToolButton::menu-indicator { image: none; }"
    )
    return b


class _ScheduleChip(QAbstractButton):
    """머리 줄의 다음 알림(체크 해제) 때. 그림 옆에 때를 적는다.

    때는 긴 꼴부터 여러 가지('내일 오전 9:00', '내일 9:00', '내일')를 받아 두고,
    머리 줄이 좁으면 짧은 꼴로, 더 좁으면 그림만 둔다(``set_level``). 단계는 0 이
    가장 긴 꼴, ``levels() - 1`` 이 그림만, ``levels()`` 는 감춤이다."""

    _PAD = 4
    _GAP = 5
    _ICON = 13
    _HEIGHT = 22
    _INK = "#3A3A3A"

    def __init__(self, icon: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._icon_name = icon
        self._ink = self._INK
        self._dark = False
        self._icon = svg_icon(icon, color=self._INK)
        self._forms: list[str] = []
        self._level = 0
        font = QFont(self.font())
        font.setPixelSize(11)
        self.setFont(font)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAttribute(Qt.WA_Hover)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def set_ink(self, color: str, dark: bool) -> None:
        """글자와 그림의 색(어두운 메모에서는 밝게)."""
        if (color, dark) == (self._ink, self._dark):
            return
        self._ink, self._dark = color, dark
        self._icon = svg_icon(self._icon_name, color=color)
        self.update()

    def set_forms(self, forms) -> None:
        self._forms = [f for f in forms if f]
        self._level = 0
        self.setText(self._forms[0] if self._forms else "")
        self.updateGeometry()
        self.update()

    def full_text(self) -> str:
        return self._forms[0] if self._forms else ""

    def levels(self) -> int:
        """보이는 꼴의 수(마지막은 그림만)."""
        return len(self._forms) + 1

    def level(self) -> int:
        return self._level

    def shown_text(self) -> str:
        return self._forms[self._level] if self._level < len(self._forms) else ""

    def width_at(self, level: int) -> int:
        """그 단계의 폭. 감추는 단계는 0."""
        if level >= self.levels():
            return 0
        text = self._forms[level] if level < len(self._forms) else ""
        width = 2 * self._PAD + self._ICON
        if text:
            width += self._GAP + self.fontMetrics().horizontalAdvance(text)
        return width

    def set_level(self, level: int) -> None:
        level = max(0, min(level, self.levels()))
        if level != self._level:
            self._level = level
            self.updateGeometry()
            self.update()
        self.setVisible(bool(self._forms) and level < self.levels())

    def sizeHint(self) -> QSize:
        return QSize(self.width_at(self._level), self._HEIGHT)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self.isDown() or self.underMouse():
            p.setPen(Qt.NoPen)
            if self._dark:
                p.setBrush(QColor(255, 255, 255, 52 if self.isDown() else 40))
            else:
                p.setBrush(QColor(0, 0, 0, 34 if self.isDown() else 20))
            p.drawRoundedRect(QRectF(self.rect()), 5, 5)
        top = (self.height() - self._ICON) // 2
        self._icon.paint(p, QRect(self._PAD, top, self._ICON, self._ICON))
        text = self.shown_text()
        if text:
            left = self._PAD + self._ICON + self._GAP
            p.setPen(QColor(self._ink))
            p.setFont(self.font())
            p.drawText(QRect(left, 0, self.width() - left, self.height()),
                       Qt.AlignLeft | Qt.AlignVCenter, text)


HIGHLIGHT_COLORS = [
    "#FFF59D",
    "#C5E1A5",
    "#B3E5FC",
    "#F8BBD0",
    "#FFCC80",
    "#D1C4E9",
]
DEFAULT_HIGHLIGHT_COLOR = HIGHLIGHT_COLORS[0]


class _NoteGrip(QSizeGrip):
    """메모 오른쪽 아래의 크기 조절 손잡이.

    서식 줄 안에 있던 것을 메모 위에 띄웠다. 읽는 동안에는 서식 줄이 자리를
    차지하지 않아 본문이 메모 끝까지 내려오는데, 손잡이는 그 위에 늘 있다.
    무늬는 메모 글자색으로 그려 어떤 메모 색에서도 보이고, 잡을 자리를
    넉넉하게 했다."""

    SIZE = 22

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self._tone = QColor(0, 0, 0, 125)

    def set_tone(self, color: QColor) -> None:
        self._tone = QColor(color)
        self.update()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        # 두 번 누르면 메모 높이를 내용에 맞춘다.
        note = self.parentWidget()
        if event.button() == Qt.LeftButton and hasattr(note, "fit_to_content"):
            note.fit_to_content()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(self._tone, 1.6, Qt.SolidLine, Qt.RoundCap))
        edge = self.SIZE - 5.0
        for reach in (4.0, 8.5, 13.0):
            p.drawLine(QPointF(edge, edge - reach), QPointF(edge - reach, edge))
        p.end()


class _SwatchKeys(QObject):
    """색 견본 묶음을 방향키로 옮겨 다니고 Enter·Space 로 고른다."""

    def __init__(self, buttons: list, columns: int, parent: QObject):
        super().__init__(parent)
        self._buttons = buttons
        self._columns = max(1, columns)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if event.type() != QEvent.KeyPress or obj not in self._buttons:
            return False
        key = event.key()
        if key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            obj.click()
            return True
        step = {Qt.Key_Left: -1, Qt.Key_Right: 1,
                Qt.Key_Up: -self._columns, Qt.Key_Down: self._columns}.get(key)
        if step is None:
            return False
        i = self._buttons.index(obj)
        j = i + step
        if key in (Qt.Key_Up, Qt.Key_Down):
            # 마지막 줄 아래(또는 맨 아래 단추 위)로는 남은 단추로 간다.
            j = max(0, min(j, len(self._buttons) - 1))
        if 0 <= j < len(self._buttons):
            self._buttons[j].setFocus(Qt.TabFocusReason)
        return True


def _wire_swatch_keys(menu, container, columns: int) -> None:
    buttons = [b for b in container.findChildren(QToolButton)]
    if not buttons:
        return
    keys = _SwatchKeys(buttons, columns, menu)
    for b in buttons:
        b.setFocusPolicy(Qt.StrongFocus)
        b.setStyleSheet(b.styleSheet() + (
            "QToolButton:focus { background: rgba(37, 99, 235, 0.16); "
            "outline: none; }"))
        b.installEventFilter(keys)
    # 메뉴가 뜨면 첫 견본에 초점을 둔다.
    menu.aboutToShow.connect(
        lambda b=buttons[0]: QTimer.singleShot(0, b.setFocus))


def _highlight_swatch_icon(color: str | None, size: int = 18) -> QIcon:
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    rect = QRect(2, 2, size - 4, size - 4)
    if color is None:
        p.setPen(QPen(QColor("#9A9A9A"), 1.2))
        p.setBrush(QColor("#FFFFFF"))
        p.drawRoundedRect(rect, 3, 3)
        p.setPen(QPen(QColor("#D64545"), 1.6))
        p.drawLine(rect.topRight(), rect.bottomLeft())
    else:
        p.setPen(QPen(QColor(0, 0, 0, 60), 1))
        p.setBrush(QColor(color))
        p.drawRoundedRect(rect, 3, 3)
    p.end()
    return QIcon(pix)


def _text_color_for_bg(bg_hex: str) -> str:
    try:
        r = int(bg_hex[1:3], 16)
        g = int(bg_hex[3:5], 16)
        b = int(bg_hex[5:7], 16)
    except (ValueError, IndexError):
        return "#1F1F1F"
    lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
    return "#FFFFFF" if lum < 0.55 else "#1F1F1F"


class _GalleryTile(QAbstractButton):
    """고르는 판의 칸 하나: 가운데 그림과 그 아래 이름. 마우스를 올리면 옅은 판."""

    def __init__(self, icon: QIcon, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._icon = icon
        self.setText(text)
        self.setAccessibleName(text)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self.underMouse() or self.isDown():
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#E9E9E9" if self.isDown() else "#F2F2F2"))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 8, 8)
        side = 26
        top = 12
        self._icon.paint(p, QRect((self.width() - side) // 2, top, side, side))
        f = QFont(self.font())
        f.setPixelSize(12)
        p.setFont(f)
        p.setPen(QColor("#1F1F1F"))
        p.drawText(QRect(4, top + side + 6, self.width() - 8, 18),
                   Qt.AlignHCenter | Qt.AlignTop,
                   p.fontMetrics().elidedText(self.text(), Qt.ElideRight, self.width() - 8))

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)


class WidgetGallery(QWidget):
    """위젯을 고르는 판: 그림과 이름의 칸(네 칸씩). 누르면 ``picked(종류)``."""

    picked = Signal(str)
    COLS = 4
    TILE = QSize(84, 70)

    def __init__(self, kinds: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from ..widgets import texts as wtexts
        grid = QGridLayout(self)
        grid.setContentsMargins(8, 8, 8, 8)
        grid.setSpacing(4)
        self.tiles: dict[str, QToolButton] = {}
        for i, name in enumerate(kinds):
            b = _GalleryTile(svg_icon("w_" + name, size=26, color="#2B2B2B"),
                             wtexts.tr("kind." + name), self)
            b.setFixedSize(self.TILE)
            b.clicked.connect(lambda _c=False, k=name: self.picked.emit(k))
            grid.addWidget(b, i // self.COLS, i % self.COLS)
            self.tiles[name] = b


class _FlashRing(QWidget):
    """목록에서 꺼낸 메모를 잠깐 파란 테두리로 알린다(어느 메모가 나왔는지 바로
    보이게). 누름은 그대로 메모로 간다."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.level = 0.0
        self.hide()

    def paintEvent(self, _event) -> None:
        if self.level <= 0.0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        color = QColor(COLOR_FOCUS)
        color.setAlphaF(min(1.0, self.level))
        p.setPen(QPen(color, 3))
        p.setBrush(Qt.NoBrush)
        r = config.CORNER_RADIUS
        p.drawRoundedRect(QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5), r, r)


class StickyNoteWidget(QWidget):
    requestLock = Signal(str)
    releaseLock = Signal(str)
    commitEdit = Signal(str, str, str, int, int, int)  # id, html, color, w, h, version
    deleteRequested = Signal(str)
    hideRequested = Signal(str)
    historyRequested = Signal(str)
    privateToggleRequested = Signal(str, bool)  # note_id, new_private
    positionChanged = Signal(str, int, int)
    sizeChanged = Signal(str, int, int)  # note_id, w, h
    # note_id, 접힘 상태(folded, dock, dock_y, fold_style, fold_x, fold_y, fold_at)
    foldChanged = Signal(str, object)
    # note_id, 바탕의 불투명도(%). 이 PC 에만 적는다.
    opacityChanged = Signal(str, int)
    # 알림, 체크 해제 예약을 정하는 창을 열어 달라(머리 줄 표시나 더 보기 메뉴).
    reminderRequested = Signal(str)
    checkResetRequested = Signal(str)
    # 1.2: 공지로 만들기/풀기(메모 id, 켤까), 이 메모를 봤다(펼침, 2초 머묾), 내 공지를
    # 본 사람 명단을 보여 달라.
    noticeRequested = Signal(str, bool)
    # 본문의 날짜를 눌렀다(메모 id, 때, 시각까지 적혔는가). 그 때로 알림을 만든다.
    dateReminderRequested = Signal(str, object, bool)
    # '같은 줄 모아 세기' 를 켜거나 껐다(메모 id, 켬).
    tallyToggled = Signal(str, bool)
    wrapToggled = Signal(str, bool)
    # 위젯: 새로 넣었다(메모 id, 위젯 id, 데이터), 눌렀다(메모 id, 위젯 id, 동작).
    widgetCreated = Signal(str, str, dict)
    widgetOp = Signal(str, str, dict)
    # 프로그램 창에 붙이기(실험): 붙여 달라, 떼어 달라.
    attachRequested = Signal(str)
    # 창 끝으로 끌어다 놓았다: (메모 id, 붙을 창의 핸들).
    attachDropped = Signal(str, int)
    detachRequested = Signal(str)
    # 인쇄(메모 id, "print" 또는 "pdf").
    printRequested = Signal(str, str)
    viewed = Signal(str)
    readListRequested = Signal(str)

    _DEFAULT_FONT_SIZE = 11
    _AUTOSAVE_INTERVAL_MS = 30_000
    # 앞으로 올린 메모가 실제로 활성 창이 됐는지 확인하기까지 기다리는 시간.
    _FRONT_EDIT_DELAY_MS = 150
    # 편집이 거절됐다는 알림이 떠 있는 시간.
    _NOTICE_MS = 2600
    # 입력이 멈춘 뒤 이만큼 지나면 내용을 보낸다. 다른 참가자가 편집 중인 글을
    # 거의 바로 볼 수 있고, 갑자기 꺼져도 잃는 양이 적다.
    _IDLE_SAVE_MS = 2500
    # 이보다 큰 내용(그림이 든 메모)은 입력이 멈출 때마다 보내지 않고 30초
    # 주기에 맡긴다. 매번 몇 MB 를 보내게 되기 때문이다.
    _IDLE_SAVE_MAX_CHARS = 300_000

    def __init__(self, note: dict[str, Any], my_nickname: str,
                 parent: QWidget | None = None,
                 default_font_size: int = 11,
                 settings=None):
        # WindowStaysOnTopHint 대신 showEvent 에서 Z-order 최하단에 핀한다.
        super().__init__(
            parent,
            Qt.FramelessWindowHint | Qt.Tool,
        )
        # 제목 줄은 없지만 화면 낭독기와 창 목록이 읽는 이름이다.
        self.setWindowTitle(i18n.t("app.name"))
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setMinimumSize(config.MIN_NOTE_WIDTH, config.MIN_NOTE_HEIGHT)

        self.note_id: str = note["id"]
        self.version: int = int(note.get("version", 1))
        self.color: str = note.get("color", config.DEFAULT_COLOR)
        self.private_owner: str | None = note.get("private_owner")
        self._content: str = note.get("content", "") or ""
        self._pending_commit: str | None = None
        # 보내 놓고 답을 받지 못한 글이 어느 판을 보고 쓴 것인가. 다시 붙었을 때
        # 그 사이 다른 사람이 고쳤는지 가리는 기준이다.
        self._pending_commit_version = 0
        # 답이 오기 전에 한 번 더 보냈을 때, 먼저 보낸 글.
        self._earlier_commit: str | None = None
        self._edit_baseline: tuple[str, str, int, int] | None = None
        self._my_nickname = my_nickname
        self._lock_holder: str | None = None
        self._edit_mode = False
        self.update()
        self._pending_lock = False
        self._first_show_done = False
        # 이 PC 에서 크기를 정해 둔 메모인가(apply_local_state, 크기 조절).
        self._local_size_set = False
        # 호스트와 끊겨 있는가. 끊긴 동안에는 새로 편집을 시작하지 못한다.
        self._offline = False
        # 읽기 상태에서 누른 체크박스의 줄 번호. 잠금을 받는 대로 그 상자만 바꾼다.
        self._pending_check: int | None = None
        # 앱 종료 중 MainWindow 가 설정한다. closeEvent 가 "내 바탕
        # 화면에서 숨기기" 부수 효과를 건너뛰게 한다.
        self._teardown = False
        self._default_font_size = int(default_font_size
                                       or self._DEFAULT_FONT_SIZE)
        # 참조로 보관해 설정 토글이 기존 메모 위젯에 즉시 반영되게 한다.
        self._settings = settings
        # 바탕(종이와 머리 줄)의 불투명도(%). 글자, 그림, 체크 상자는 늘 또렷하다.
        self._opacity = 100
        self._opacity_hover = False
        # 더 보기 메뉴에서 투명도를 고르는 동안은 편집 중이어도 고른 대로 보인다.
        self._opacity_preview = False
        # 체크 해제 예약: 잠금을 받는 대로 체크한 상자를 모두 해제하고 바로 놓는다.
        self._pending_uncheck = False

        root = QVBoxLayout(self)
        # paintEvent 의 둥근 사각형 안쪽(2 px 인셋)에 콘텐츠를 맞춰
        # 핸들 바 버튼이 상단 틴트 스트립에 정확히 가운데 정렬되게 한다.
        root.setContentsMargins(2, 2, 2, 2)
        root.setSpacing(0)

        self._handle = QWidget(self)
        self._handle.setFixedHeight(config.HANDLE_BAR_HEIGHT)
        self._handle.setAttribute(Qt.WA_TranslucentBackground)
        handle_row = QHBoxLayout(self._handle)
        handle_row.setContentsMargins(12, 0, 6, 0)
        handle_row.setSpacing(2)
        handle_row.setAlignment(Qt.AlignVCenter)

        self._private_icon = QLabel(self._handle)
        self._private_icon.setFixedSize(16, 16)
        self._private_icon.setPixmap(
            svg_icon("lock", color="#3A3A3A").pixmap(14, 14))
        self._private_icon.setToolTip(
            i18n.t("sticky.private_icon_tip"))
        self._private_icon.setStyleSheet("background: transparent;")
        self._private_icon.setVisible(bool(self.private_owner))
        handle_row.addWidget(self._private_icon, 0, Qt.AlignVCenter)

        # 공지: 만든 사람만 고치고 지운다. 머리 줄 왼쪽에 '공지'.
        self._notice_owner: str | None = None
        self._notice_can_make = False
        self._notice_can_lift = False
        self._notice_label = QLabel(i18n.t("notice.label"), self._handle)
        self._notice_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._notice_label.setToolTip(i18n.t("notice.label"))
        self._notice_label.hide()
        self._notice_icon = False      # 아주 좁으면 글 대신 그림
        handle_row.addWidget(self._notice_label, 0, Qt.AlignVCenter)

        # 붙은 프로그램 창의 이름. 누르면 '창에서 떼기'.
        self._attached = False
        self._can_attach = False
        # 창 끝으로 끌어 붙이기: 끌기를 시작할 때 읽은 창 목록, 지금 붙을 창, 테두리 그림.
        self._attach_windows: list | None = None
        self._attach_target = None              # (창, 면, 맞출 자리) 또는 None
        self._detach_pending = False            # 놓으면 붙은 창에서 떨어진다
        self._attach_ghost: _AttachGhost | None = None
        # 메인 창이 정한다: 지금 붙은 창의 핸들(없으면 0).
        self.attached_hwnd = lambda: 0
        self._attach_chip = QToolButton(self._handle)
        self._attach_chip.setIcon(svg_icon("window", color="#3A3A3A"))
        self._attach_chip.setIconSize(QSize(13, 13))
        self._attach_chip.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._attach_chip.setCursor(Qt.PointingHandCursor)
        self._attach_chip.setFocusPolicy(Qt.NoFocus)
        self._attach_chip.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._attach_chip.clicked.connect(self._show_window_menu)
        self._attach_chip.hide()
        self._attach_full = ""         # 붙은 창의 이름(줄이기 전)
        self._head_ink = "#3A3A3A"
        handle_row.addWidget(self._attach_chip, 0, Qt.AlignVCenter)

        # 다음 알림과 다음 체크 해제 때. 누르면 정하는 창을 연다.
        self._remind_chip = self._make_chip(
            "bell", lambda: self.reminderRequested.emit(self.note_id))
        self._reset_chip = self._make_chip(
            "check_reset", lambda: self.checkResetRequested.emit(self.note_id))
        handle_row.addWidget(self._remind_chip, 0, Qt.AlignVCenter)
        handle_row.addWidget(self._reset_chip, 0, Qt.AlignVCenter)
        # 내 공지를 본 사람 수('읽음 3/5'). 누르면 명단.
        self._read_chip = self._make_chip(
            "eye_on", lambda: self.readListRequested.emit(self.note_id))
        handle_row.addWidget(self._read_chip, 0, Qt.AlignVCenter)

        self._lock_label = QLabel("", self._handle)
        self._lock_label.setTextFormat(Qt.PlainText)   # 편집 중인 사람의 닉네임
        # 남는 자리만 쓴다(비어 있을 때 자리를 잡아 머리 줄 표시를 밀어내지 않는다).
        self._lock_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._lock_label.setStyleSheet(
            "color: #3A3A3A; font-size: 11px; background: transparent;"
        )
        handle_row.addWidget(self._lock_label, 1, Qt.AlignVCenter)

        self._color_btn = _make_format_button(
            color_swatch_icon(self.color), i18n.t("sticky.tip_color"))
        self._color_btn.clicked.connect(self._show_color_menu)
        self._history_btn = _make_format_button(
            svg_icon("clock"), i18n.t("sticky.tip_history"))
        self._history_btn.clicked.connect(
            lambda: self.historyRequested.emit(self.note_id))
        self._fold_btn = _make_format_button(
            svg_icon("fold"), i18n.t("sticky.tip_fold"))
        self._fold_btn.clicked.connect(lambda: self.fold())
        self._more_btn = _make_format_button(
            svg_icon("more"), i18n.t("sticky.tip_more"))
        self._more_btn.clicked.connect(self._show_more_menu)
        self._close_btn = _make_format_button(
            svg_icon("close"), i18n.t("sticky.tip_close"))
        self._close_btn.clicked.connect(self._close_sticky)

        for b in (self._color_btn, self._history_btn, self._fold_btn,
                  self._more_btn, self._close_btn):
            handle_row.addWidget(b, 0, Qt.AlignVCenter)
        root.addWidget(self._handle)
        # 남의 공지를 고치려 할 때, 연락처를 복사했을 때 잠깐 알리는 글은 머리 줄의 상태
        # 글 자리에 둔다(본문을 밀었다 당기지 않는다).
        self._flash_text = ""
        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self._end_flash_status)
        # 2초 머물면 '봤다'.
        self._view_timer = QTimer(self)
        self._view_timer.setSingleShot(True)
        self._view_timer.setInterval(2000)
        self._view_timer.timeout.connect(self._on_view_timer)

        self._text = RichTextEdit(self)
        self._text.set_attach_modes(getattr(settings, "attach_modes", None))
        self._text.attachedRange.connect(self._on_attached_range)
        # 보기 상태의 본문에서 날짜와 연락처를 찾는다(글이 바뀐 뒤 잠깐 있다가).
        self._smart_timer = QTimer(self)
        self._smart_timer.setSingleShot(True)
        self._smart_timer.setInterval(250)
        self._smart_timer.timeout.connect(self._refresh_smart)
        self._text.document().contentsChanged.connect(self._smart_timer.start)
        self._text.document().contentsChanged.connect(
            lambda: self._widget_timer.start() if hasattr(self, "_widget_timer") else None)
        self._smart_press = None
        # 고른 글의 숫자 합계(끌어 고르는 동안에는 세지 않고 멈춘 뒤에).
        self._sum_timer = QTimer(self)
        self._sum_timer.setSingleShot(True)
        self._sum_timer.setInterval(120)
        self._sum_timer.timeout.connect(self._on_selection_changed)
        self._text.selectionChanged.connect(self._sum_timer.start)
        # @이름 자동 완성(고를 수 있는 이름은 메인 창이 넣어 준다).
        self._people: list[str] = []
        self._mention_list = None
        self._text.textChanged.connect(self._update_mention_list)
        # 같은 줄 모아 세기.
        self._tally_on = False
        self._wrap_on = True
        self._text.textChanged.connect(self._refresh_tally)
        self._text.setReadOnly(True)
        self._text.setFrameStyle(QFrame.NoFrame)
        self._text.setAttribute(Qt.WA_TranslucentBackground)
        self._text.viewport().setAutoFillBackground(False)
        self._scrollbar = RoundedScrollBar()
        self._text.setVerticalScrollBar(self._scrollbar)
        # 넓은 표나 그림이 폭을 넘을 때의 가로 막대도 둥글게 그린다(Windows 기본 그림은 각지다).
        self._hscrollbar = RoundedScrollBar(orientation=Qt.Horizontal)
        self._text.setHorizontalScrollBar(self._hscrollbar)
        self._text.setStyleSheet("QTextEdit { background: transparent; }")
        self._text.setViewportMargins(*config.TEXT_MARGINS)
        default_font = self._text.font()
        default_font.setPointSize(self._default_font_size)
        self._text.setFont(default_font)
        self._text.document().setDefaultFont(default_font)
        self._apply_content_to_view(self._content)
        self._text.viewport().installEventFilter(self)
        self._text.installEventFilter(self)
        self._text.cursorPositionChanged.connect(self._refresh_format_buttons)
        self._text.selectionChanged.connect(self._refresh_format_buttons)
        root.addWidget(self._text, 1)

        self._format_bar = QWidget(self)
        self._format_bar.setFixedHeight(config.FORMAT_BAR_HEIGHT)
        self._format_bar.setAttribute(Qt.WA_TranslucentBackground)
        fb = QHBoxLayout(self._format_bar)
        fb.setContentsMargins(10, 4, 4, 4)
        fb.setSpacing(2)

        self._bold_btn = _make_format_button(
            svg_icon("bold"), i18n.t("sticky.tip_bold"), True)
        self._italic_btn = _make_format_button(
            svg_icon("italic"), i18n.t("sticky.tip_italic"), True)
        self._underline_btn = _make_format_button(
            svg_icon("underline"), i18n.t("sticky.tip_underline"), True)
        self._strike_btn = _make_format_button(
            svg_icon("strike"), i18n.t("sticky.tip_strike"), True)

        self._font_color_btn = _make_format_button(
            svg_icon("text_color"), i18n.t("sticky.tip_font_color"))
        # 단추에 그려 둔 (글자색, 띠의 색). 같으면 다시 그리지 않는다.
        self._font_icon_state: tuple | None = None
        self._font_color_btn.clicked.connect(self._show_font_color_menu)

        self._highlight_btn = _make_format_button(
            svg_icon("highlight"), i18n.t("sticky.tip_highlight"))
        self._highlight_btn.clicked.connect(self._show_highlight_menu)

        self._list_btn = _make_format_button(
            svg_icon("list"), i18n.t("sticky.tip_list"), True)
        # 메뉴의 항목은 처음 열 때 만든다(메모 창마다 미리 만들면 켤 때 느리다).
        self._list_menu = QMenu(self._list_btn)
        self._list_menu.aboutToShow.connect(self._fill_list_menu)
        self._list_btn.setMenu(self._list_menu)
        self._list_btn.setPopupMode(QToolButton.InstantPopup)

        # 넣기: 이미지와 파일·폴더 링크. 버튼 줄이 좁아 한 버튼의 메뉴로 묶는다.
        self._image_btn = _make_format_button(
            svg_icon("attach"), i18n.t("sticky.tip_attach"))
        self._image_btn.setToolTip(i18n.t("sticky.tip_attach_full"))
        self._attach_menu = QMenu(self._image_btn)
        self._attach_menu.aboutToShow.connect(self._fill_attach_menu)
        # 위젯은 서식 막대의 따로 된 단추에서 고른다(공유 PC 가 위젯을 알 때만 누른다).
        self._widget_btn = _make_format_button(
            svg_icon("widgets"), i18n.t("widget.menu"))
        # clicked 는 눌림 값(bool)을 넘긴다. 고르는 판의 자리로 받지 않게 감싼다.
        self._widget_btn.clicked.connect(lambda _c=False: self._show_widget_gallery())
        self._widgets_ok = False
        self._widget_games = True
        self._widget_data: dict[str, dict] = {}
        self._widget_views: dict = {}
        self._widget_fixing = False
        self._widget_delete_pending: list[str] = []
        # 본문의 위젯 칸 크기(위젯 id: (폭, 높이))와 저장용 그림(같은 모습이면 다시 그리지 않는다).
        self._widget_sizes: dict[str, tuple[int, int]] = {}
        self._widget_shots: dict[str, tuple] = {}
        self._widget_room: set[str] = set()
        # 부모는 문서(본문 칸에 자식을 붙이면 메모의 이벤트 거르개가 만들기 전에 불린다).
        self._widget_handler = wobj.WidgetObjectHandler(
            self._text.document(), self._widget_sizes.get,
            lambda wid: wid in self._widget_views)
        wobj.install(self._text.document(), self._widget_handler)
        # 옮겨 붙인 위젯은 이 메모의 위젯이고 본문에 아직 없을 때만 다시 위젯이 된다.
        self._text.widget_ok = lambda wid: (
            wid in self._widget_data
            and wid not in {w for w, _p, _f in wobj.find_objects(self._text.document())})
        self._text.widget_picture = self._widget_copy_picture
        self._text.widget_mine = lambda wid: wid in self._widget_data
        self._widget_timer = QTimer(self)
        self._widget_timer.setSingleShot(True)
        self._widget_timer.setInterval(40)
        self._widget_timer.timeout.connect(self._sync_widget_views)
        # 메모 폭이 바뀌어 글이 다시 놓이면 위젯 폭('메모 폭에 맞춤')과 자리도 다시 맞춘다.
        layout = self._text.document().documentLayout()
        layout.documentSizeChanged.connect(
            lambda _size: self._widget_timer.start() if self._widget_views else None)
        # 자리는 글이 다시 놓이는 그때 바로 옮긴다(위젯이 글보다 늦게 따라오지 않게).
        self._placing_widgets = False
        layout.update.connect(lambda _rect: self._place_widget_views())
        self._text.verticalScrollBar().valueChanged.connect(
            lambda _v: self._place_widget_views())
        self._text.horizontalScrollBar().valueChanged.connect(
            lambda _v: self._place_widget_views())

        self._bold_btn.clicked.connect(self._toggle_bold)
        self._italic_btn.clicked.connect(self._toggle_italic)
        self._underline_btn.clicked.connect(self._toggle_underline)
        self._strike_btn.clicked.connect(self._toggle_strike)
        self._image_btn.clicked.connect(self._show_attach_menu)

        for b in (self._bold_btn, self._italic_btn, self._underline_btn,
                  self._strike_btn, self._font_color_btn, self._highlight_btn,
                  self._list_btn, self._image_btn, self._widget_btn):
            fb.addWidget(b)
        fb.addStretch(1)
        fb.addSpacing(_NoteGrip.SIZE)        # 손잡이가 올라앉는 자리
        # 본문 아래 칸: 가는 선, 고른 글의 합계, 같은 줄 모아 세기.
        self._extra_rule = QFrame(self)
        self._extra_rule.setFixedHeight(1)
        self._extra_rule.hide()
        root.addWidget(self._extra_rule)
        self._sum_line = QLabel("", self)
        self._sum_line.setTextFormat(Qt.PlainText)
        self._sum_line.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._sum_line.hide()
        root.addWidget(self._sum_line)
        self._tally_line = QLabel("", self)
        self._tally_line.setTextFormat(Qt.RichText)
        self._tally_line.hide()
        root.addWidget(self._tally_line)
        self._extra_ink = ("#3A3A3A", "#6B6B6B")
        root.addWidget(self._format_bar)

        self._grip = _NoteGrip(self)
        self._grip.installEventFilter(self)
        # 스크롤바가 손잡이 밑으로 들어가지 않게 아래를 그만큼 비운다.
        self._scrollbar.setStyleSheet(
            f"QScrollBar:vertical {{ margin: 4px 2px {_NoteGrip.SIZE}px 0; }}")

        self._set_format_bar_enabled(False)

        self._shortcuts = [
            QShortcut(QKeySequence.Bold, self, self._toggle_bold),
            QShortcut(QKeySequence.Italic, self, self._toggle_italic),
            QShortcut(QKeySequence.Underline, self, self._toggle_underline),
            QShortcut(QKeySequence("Ctrl+Shift+S"), self, self._toggle_strike),
            QShortcut(QKeySequence("Ctrl+Shift+L"), self, self._toggle_list),
            QShortcut(QKeySequence("Ctrl+Shift+H"), self, self._toggle_highlight),
            QShortcut(QKeySequence("Ctrl+K"), self, self._insert_link),
        ]
        # 링크 위에서 손가락 커서와 말풍선을 보이려면 누르지 않은 움직임도 받아야 한다.
        self._text.viewport().setMouseTracking(True)
        self._link_press: str | None = None
        # 방금 저절로 목록으로 바뀐 줄의 자리, 그때 쳤던 글자, 바뀐 직후의 문서 판.
        # 바로 Backspace 를 누르면 그 글자로 되돌린다. 다른 키를 누르거나 그 뒤로
        # 글이 바뀌었으면(한글은 키 이벤트 없이 들어온다) 되돌리지 않는다.
        self._auto_list_undo: tuple[int, str, int] | None = None

        # 다른 사람이 편집 중인 메모를 고치려다 거절됐을 때 잠깐 뜨는 알림.
        # 예전에는 남이 편집하는 내내 본문을 덮어 읽지도 못하게 했다.
        self._overlay = QLabel("", self)
        self._overlay.setTextFormat(Qt.PlainText)
        self._overlay.setAlignment(Qt.AlignCenter)
        self._overlay.setWordWrap(True)
        self._overlay.setStyleSheet(
            "QLabel { background: rgba(20,20,20,0.86); color: white; "
            "font-size: 13px; font-weight: 600; border-radius: 10px; "
            "padding: 10px 14px; }"
        )
        self._overlay.hide()
        self._overlay_timer = QTimer(self)
        self._overlay_timer.setSingleShot(True)
        self._overlay_timer.setInterval(self._NOTICE_MS)
        self._overlay_timer.timeout.connect(self._overlay.hide)

        self._drag_offset: QPoint | None = None
        # 파일 대화상자가 열리면 창이 비활성화되는데, 그때 changeEvent 가
        # 편집을 조기 커밋해 이후 삽입된 이미지가 세션 밖에서 처리되지
        # 않도록 막는 플래그.
        self._opening_dialog = False
        # 이미지 더블 클릭으로 연 자체 뷰어. 메모당 하나만 두고 재사용한다.
        self._image_viewer: ImageViewerWindow | None = None
        # 더블 클릭의 첫 클릭이 낸 잠금 요청이 아직 응답 전(게스트의 왕복 지연)에
        # 뷰어가 열렸으면, 뒤늦게 온 허용은 편집으로 들어가지 않고 바로 돌려준다.
        self._abandon_grant = False

        # 접기 · 가장자리 붙이기. 접힌 동안에는 이 창 자체가 쪽지(붙였으면 탭)가
        # 된다. 창을 따로 두지 않아 잠금·저장·닫기 흐름이 그대로다.
        self._folded = False
        # 붙여 둔 벽과, 벽을 따라 잰 탭의 자리(좌우 벽은 y, 위아래 벽은 x).
        self._dock = nf.DOCK_NONE
        self._dock_y = 0
        self._open_pos = QPoint()            # 펼쳤을 때의 왼쪽 위(저장되는 위치)
        self._open_size = QSize()            # 접힌 동안 기억해 두는 펼친 크기
        # 접힌 메모(쪽지·아이콘·탭)를 끌어다 둔 자리. 펼친 자리와 따로 기억한다.
        # 정해 둔 적이 없으면 펼친 메모의 왼쪽 위에서 그대로 접힌다.
        self._fold_pos = QPoint()
        self._fold_at = False
        self._open_pix: QPixmap | None = None  # 접을 때 찍어 둔 펼친 모습
        self._fold_frames: nf.FoldFrames | None = None
        self._fold_t = 0.0
        self._fold_anim: QVariantAnimation | None = None
        self._fold_done = None
        self._fold_target = QRect()
        self._geo_anim: QVariantAnimation | None = None
        self._geo_done = None
        self._pre_unfold = False             # 펼칠 자리로 옮겨 가는 중
        self._slip_press: QPoint | None = None
        self._slip_grab = QPoint()
        self._slip_dragging = False
        self._drag_dock = nf.DOCK_NONE       # 끄는 동안 미리 보이는 붙을 쪽
        self._drag_avail = QRect()           # 그 벽이 있는 화면
        self._drag_cursor = QPoint()
        self._drag_from = QPoint()           # 펼친 메모를 끌기 시작한 자리
        self._ghost: _DockGhost | None = None
        self._slip_hover = False
        self._topmost = False
        self._dock_resolver = None
        # 이 메모를 접었을 때의 모양. 비어 있으면 설정의 기본 모양을 따른다.
        self._fold_style = ""
        self._icon_resolver = None
        # 아이콘으로 접힌 메모를 끌 때, 그 아래에 받아 줄 것(메모 폴더 아이콘)이
        # 있는지 묻는 함수와 끌기 시작한 자리.
        self._drop_probe = None
        # 아이콘으로 접혀 있는 동안 바탕화면 아이콘처럼 고르고 함께 끄는 일을
        # 맡는 곳(ui/icon_select.py 의 IconController). 없으면 혼자 움직인다.
        self._icon_ctl = None
        self._group_drag = False
        self._icon_selected = False
        # 바탕화면에서 끌어 고르는 사각형이 이 아이콘 위를 지나는 부분.
        self._marquee_tint: QRectF | None = None
        self._drag_origin: QPoint | None = None
        # 끄는 동안 벽에 닿아 있는가(붙어 있던 탭을 끌기 시작했으면 처음부터 참).
        self._drag_at_wall = False
        self._fade_anim: QVariantAnimation | None = None
        self._title_src: str | None = None
        self._title_text = ""

        # 미완료 편집이 크래시나 강제 종료에서 살아남도록 주기적으로 저장한다.
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(self._AUTOSAVE_INTERVAL_MS)
        self._autosave_timer.timeout.connect(self._autosave_while_editing)
        self._idle_save_timer = QTimer(self)
        self._idle_save_timer.setSingleShot(True)
        self._idle_save_timer.setInterval(self._IDLE_SAVE_MS)
        self._idle_save_timer.timeout.connect(self._idle_save)
        self._text.textChanged.connect(self._on_body_changed)
        # 이미지를 넣으면 크기와 상관없이 곧바로 한 번 보낸다. 큰 메모는 입력이
        # 멈출 때마다 보내지 않으므로(_IDLE_SAVE_MAX_CHARS), 그대로 두면 이미지만
        # 넣은 메모가 다른 사람과 목록에는 한참 빈 메모로 보인다.
        self._image_save_timer = QTimer(self)
        self._image_save_timer.setSingleShot(True)
        self._image_save_timer.setInterval(400)
        self._image_save_timer.timeout.connect(self._save_after_image)
        self._text.imageInserted.connect(self._image_save_timer.start)
        # 편집 중이 아닐 때 끌어다 놓은 것. 잠금을 받은 뒤에 넣는다.
        self._pending_drop: QMimeData | None = None
        self._pending_drop_pos = -1
        # 편집을 시작하면 본문 끝에 링크로 넣을 파일·폴더 경로.
        self._pending_links: list[str] = []
        # 클립보드로 만든 새 메모에 넣을 내용(편집을 얻으면 넣는다).
        self._pending_mime: QMimeData | None = None
        # 접힌 아이콘에 넣는 첫 이미지의 작은 그림(내용이 바뀌면 다시 만든다).
        self._thumb_src: str | None = None
        self._thumb_pix: QPixmap | None = None

        self.resize(int(note.get("width", config.DEFAULT_NOTE_WIDTH)),
                    int(note.get("height", config.DEFAULT_NOTE_HEIGHT)))

        self._apply_theme_colors()

    def _apply_content_to_view(self, content: str) -> None:
        if not content:
            self._text.clear()
        elif html_guard.is_markup(content):
            # 본문이 가리키는 바깥 자원(네트워크 경로의 그림 등)은 걷어 내고 그린다.
            self._text.setHtml(html_guard.clean(content))
        else:
            # 서식 없이 저장된 글. 꺾쇠가 들어 있어도 글자 그대로 보여 준다.
            self._text.setPlainText(content)
        self._convert_loaded_markers()
        # 저장 형식의 위젯 그림을 본문의 위젯 칸으로(그림으로 다루지 않는다).
        wobj.images_to_objects(self._text.document())
        # 링크마다 종류 아이콘 자리를 끼우고 글자색을 지금 메모 색에 맞춘다.
        self._text.sync_link_icons()
        self._keep_text_readable()
        # 아직 표시 전이면 뷰포트 폭이 확정되지 않아 fit 이 이미지를 과하게
        # 줄인다. 표시되면 RichTextEdit.resizeEvent 가 알맞은 폭으로 맞춘다.
        if self.isVisible():
            self._text.fit_images_to_viewport()
        # load·fit 을 undo 스택에서 제거해 첫 Ctrl+Z 가 fit 이전
        # 이미지 크기로 되돌아가지 않게 한다.
        self._text.document().clearUndoRedoStacks()
        # 접힌 모양의 할 일 진행률이 바뀌었을 수 있다.
        if getattr(self, "_folded", False):
            self.update()

    def _leading_marker(self, block) -> tuple[int, int]:
        """블록 맨 앞에 인라인 마커 이미지가 있으면 ``(code, 제거할 글자수)``
        를 반환한다. 마커 이미지 + 뒤 공백 0~2 개를 함께 센다."""
        it = block.begin()
        if it.atEnd():
            return (MARKER_NONE, 0)
        fmt = it.fragment().charFormat()
        if not fmt.isImageFormat():
            return (MARKER_NONE, 0)
        code = marker_code_for_url(fmt.toImageFormat().name())
        if code == MARKER_NONE:
            return (MARKER_NONE, 0)
        text = block.text()
        length = 1
        while length < 3 and len(text) > length and text[length] == " ":
            length += 1
        return (code, length)

    @staticmethod
    def _leading_number(block) -> int:
        """블록이 번호 표시가 붙은 '1.' 로 시작하면 지울 글자 수(번호와 뒤의 빈칸
        하나), 아니면 0."""
        it = block.begin()
        if it.atEnd():
            return 0
        # Qt 는 이름 붙은 앵커를 읽을 때 그 이름을 뒤따르는 글의 첫 글자에만 붙인다.
        # 그래서 표시는 첫 조각에서 보고, 번호는 줄의 글자에서 읽는다.
        if NUMBER_ANCHOR not in it.fragment().charFormat().anchorNames():
            return 0
        text = block.text()
        found = re.match(r"\d{1,4}\.", text)
        if not found:
            return 0
        return found.end() + (1 if text[found.end():found.end() + 1] == " " else 0)

    def _convert_loaded_markers(self, start: int | None = None,
                                end: int | None = None) -> None:
        """저장본을 화면용으로 바꾼다: 줄 앞의 마커 그림(+뒤 공백)과 번호 글자를
        블록 마커 속성으로, 블록의 들여쓰기 값을 단계로. 구버전 인라인 이미지
        저장본도 이렇게 현재 모델로 흡수한다. ``start``/``end`` 를 주면 그 범위의
        줄만(첨부한 글. 원래 있던 줄의 '1.' 글자를 번호로 바꾸지 않는다)."""
        doc = self._text.document()
        positions = []
        block = doc.findBlock(start) if start is not None else doc.begin()
        while block.isValid():
            if end is not None and block.position() > end:
                break
            positions.append(block.position())
            block = block.next()
        for pos in reversed(positions):
            block = doc.findBlock(pos)
            saved_level = block.blockFormat().indent()
            code, length = self._leading_marker(block)
            if code == MARKER_NONE:
                length = self._leading_number(block)
                if length:
                    code = MARKER_NUMBER
            bc = QTextCursor(doc)
            if code != MARKER_NONE:
                bc.setPosition(pos)
                bc.setPosition(pos + length, QTextCursor.KeepAnchor)
                bc.removeSelectedText()
                self._text.set_block_marker(doc.findBlock(pos), code, saved_level)
            if saved_level:
                # 단계는 따로 적어 두고 Qt 의 들여쓰기(한 단계 40픽셀)는 쓰지 않는다.
                # 목록이 아닌 줄에 남은 값은 옛 버전에서 목록을 풀며 남긴 것이다.
                flat = QTextBlockFormat()
                flat.setIndent(0)
                bc.setPosition(pos)
                bc.mergeBlockFormat(flat)
        self._text.restyle_lists()

    def _marker_save_html(self) -> str:
        """블록 마커를 인라인 이미지로 되살린 HTML 을 만든다. 저장 형식은
        예전과 같아(인라인 이미지 + 공백) 호환되고, 다시 불러올 때 블록
        마커로 변환된다. 라이브 문서는 그대로 두고 복제본에서 작업한다."""
        clone = self._text.document().clone()
        # 링크 아이콘 자리는 화면용이라 저장하지 않는다(위치 계산 전에 뺀다).
        strip_link_icons(clone)
        # 위젯 칸은 지금 모습의 그림으로 적는다(옛 버전은 그림을 본다). 글자 하나가
        # 글자 하나로 바뀌어 아래의 위치 계산은 그대로다.
        wobj.objects_to_images(clone, self._widget_picture)
        numbers = self._text.list_numbers(clone)
        positions = []
        block = clone.begin()
        while block.isValid():
            bf = block.blockFormat()
            code = bf.intProperty(MARKER_PROP)
            level = (max(0, min(MAX_LEVEL, bf.intProperty(LEVEL_PROP)))
                     if code in MARKER_CODES else 0)
            positions.append((block.position(), code, level,
                              numbers.get(block.blockNumber(), 1),
                              bf.hasProperty(SPACING_PROP)))
            block = block.next()
        for pos, code, level, number, spaced in reversed(positions):
            cur = QTextCursor(clone)
            cur.setPosition(pos)
            # 저장 형식엔 여백 대신 인라인 이미지를 두고, 단계는 블록의 들여쓰기
            # 값으로 적는다(옛 버전이 고쳐 저장해도 그 값은 남는다). 서식 사이에
            # 넣은 위 간격은 화면용이라 뺀다.
            saved = QTextBlockFormat()
            saved.setLeftMargin(0.0)
            saved.setIndent(level)
            if spaced:
                saved.setTopMargin(0.0)
            cur.mergeBlockFormat(saved)
            if code == MARKER_NUMBER:
                # 번호는 글자로 적는다. 옛 버전에서는 '1. 내용'으로 보인다.
                lead = QTextCursor(clone)
                lead.setPosition(pos)
                if clone.findBlock(pos).length() > 1:
                    lead.setPosition(pos + 1)
                    plain = RichTextEdit._plain_format(lead.charFormat())
                else:
                    plain = RichTextEdit._plain_format(lead.blockCharFormat())
                label = QTextCharFormat(plain)
                label.setAnchor(True)
                label.setAnchorNames([NUMBER_ANCHOR])
                label.setFontUnderline(False)
                cur.insertText(f"{number}.", label)
                cur.insertText(" ", plain)
            elif code in MARKER_CODES:
                cur.insertImage(self._make_marker_image_format(code))
                cur.insertText("  ")
        return clone.toHtml()

    def _current_content(self) -> str:
        """서식이 있으면 HTML, 없으면 이력 diff 가독성을 위해 평문을 반환한다.

        '기본과 다른 폰트 크기' 감지가 핵심이다. 그러지 않으면 Ctrl+Wheel
        로 크기만 바꾼 메모가 평문으로 저장돼, 서버 상태에서 다시 렌더할 때
        크기 정보가 사라진다."""
        doc = self._text.document()
        plain = doc.toPlainText()
        default_size = doc.defaultFont().pointSizeF()
        # 표(글상자)가 있으면 서식이다. 글자만 든 표를 평문으로 저장하면 표가 사라진다.
        has_rich = bool(doc.rootFrame().childFrames())
        block = doc.begin()
        while block.isValid() and not has_rich:
            if self._marker_code(block) in MARKER_CODES:
                has_rich = True
                break
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                if frag.isValid():
                    fmt = frag.charFormat()
                    size = fmt.fontPointSize()
                    if (fmt.fontWeight() > QFont.Normal
                            or fmt.isAnchor()
                            or fmt.fontItalic()
                            or fmt.fontUnderline()
                            or fmt.fontStrikeOut()
                            or fmt.isImageFormat()
                            or wobj.is_widget_format(fmt)
                            or (size > 0
                                and abs(size - default_size) > 0.1)
                            or fmt.hasProperty(QTextFormat.ForegroundBrush)
                            or fmt.hasProperty(QTextFormat.BackgroundBrush)):
                        has_rich = True
                        break
                it += 1
            block = block.next()
        if has_rich or "<" in plain:
            # 꺾쇠가 든 글은 서식이 없어도 HTML 로 저장한다. 글자 그대로 두면 읽는
            # 쪽(옛 버전 포함)이 꺾쇠 사이를 태그로 읽어 그 글이 사라진다.
            return self._marker_save_html()
        return plain

    def apply_server_state(self, note: dict[str, Any]) -> None:
        self.version = int(note.get("version", self.version))
        new_private = note.get("private_owner")
        if new_private != self.private_owner:
            self._open_pix = None
        self.private_owner = new_private
        if hasattr(self, "_private_icon"):
            self._private_icon.setVisible(bool(self.private_owner))
        new_color = note.get("color", self.color)
        if new_color != self.color:
            self._open_pix = None
            self.color = new_color
            self._color_btn.setIcon(color_swatch_icon(new_color))
            self._apply_theme_colors()
            self.update()
        new_content = note.get("content", self._content) or ""
        if new_content != self._content:
            # 접을 때 찍어 둔 펼친 모습은 더는 맞지 않는다.
            self._open_pix = None

        # 방금 전송한 내용이 그대로 돌아온 경우 undo 스택을 지키려고 뷰를 유지한다.
        own_echo = (self._pending_commit is not None
                    and new_content == self._pending_commit)
        if (not own_echo and self._earlier_commit is not None
                and new_content == self._earlier_commit):
            # 잇달아 두 번 보낸 저장 가운데 먼저 것의 답이다. 화면에는 그 뒤에 쓴
            # 글이 있으므로 건드리지 않고, 나중 것의 답을 기다린다.
            self._earlier_commit = None
            self._content = new_content
        elif own_echo:
            self._earlier_commit = None
            self._pending_commit = None
            self._content = new_content
        elif not self._edit_mode and new_content != self._content:
            self._content = new_content
            self._apply_content_to_view(new_content)
        else:
            self._content = new_content

        cur = self._current_open_size()
        w = int(note.get("width", cur.width()))
        h = int(note.get("height", cur.height()))
        if self._local_size_set:
            # 크기는 PC 마다 따로다. 다른 사람이 고칠 때 딸려 온 크기를 따르면
            # 이 PC 에서 맞춰 둔 크기가 남의 화면 크기로 바뀐다.
            w, h = cur.width(), cur.height()
        if (w, h) != (cur.width(), cur.height()) and not self._edit_mode:
            if self._folded or self._fold_busy():
                # 접힌 창은 쪽지 크기다. 펼칠 때 쓸 크기만 바꿔 둔다.
                self._open_size = QSize(w, h)
                self._open_pix = None
            else:
                self.resize(w, h)
        if self._folded:
            # 제목이나 색이 바뀌었을 수 있다. 탭은 폭도 제목을 따른다.
            if (self._dock and self._slip_hover and not self._slip_dragging
                    and not self._fold_busy()):
                self._slide_to(self._tab_rect(True), nf.TAB_HOVER_MS)
            self._refresh_face_tip()
            self.update()

    def set_lock_holder(self, holder: str | None) -> None:
        if holder != self._lock_holder:
            self._open_pix = None
        self._lock_holder = holder
        self._refresh_lock_ui()

    def is_locked_by_other(self) -> bool:
        return self._lock_holder is not None and self._lock_holder != self._my_nickname

    def enter_edit_mode(self) -> None:
        self._pending_lock = False
        pending_check, self._pending_check = self._pending_check, None
        uncheck, self._pending_uncheck = self._pending_uncheck, False
        if (uncheck and pending_check is None and self._pending_drop is None
                and not self._pending_links and self._pending_mime is None):
            # 체크 해제 예약: 편집 화면으로 바꾸지 않고 고친 뒤 바로 놓는다.
            # 접힌 메모도 그대로 접힌 채로 둔다.
            self._abandon_grant = False
            self._uncheck_and_release()
            return
        if self._abandon_grant or self._folded or self._fold_busy():
            # 이미지 뷰어를 열거나 메모를 접는 사이에 허용이 도착했다. 변경 없이
            # 잠금만 돌려주므로 서버는 이력 행을 남기지 않는다(변경 없는 편집
            # 종료와 같은 경로). 접힌 메모가 보이지 않는 채로 잠금을 쥐면 안 된다.
            self._abandon_grant = False
            self._pending_drop = None
            self._pending_links = []
            self._pending_mime = None
            self.releaseLock.emit(self.note_id)
            return
        self._edit_mode = True
        self.update()
        self._earlier_commit = None
        self._text.setReadOnly(False)
        self._text.setFocus(Qt.OtherFocusReason)
        self._overlay.hide()
        self._set_lock_text(i18n.t("sticky.lock_editing_self"))
        self._set_format_bar_enabled(True)
        self._fit_chips()
        self._refresh_format_buttons()
        self._autosave_timer.start()
        # 변화 없는 focus→blur 가 updated_at 을 건드리거나 no-op 이력
        # 행을 남기지 않도록 비교 기준 스냅샷을 떠 둔다.
        self._edit_baseline = self._edit_snapshot()
        self._apply_pending_drop()
        self._apply_pending_links()
        self._apply_pending_widget_deletes()
        if uncheck:
            self._uncheck_blocks()
        if pending_check is not None:
            # 체크박스 한 번 누르기: 그 상자만 바꾸고 곧바로 저장하고 잠금을 푼다.
            block = self._text.document().findBlockByNumber(pending_check)
            if block.isValid() and self._marker_code(block) in (
                    MARKER_CHECK0, MARKER_CHECK1):
                self._toggle_checkbox_block(block)
            self._commit_and_release()

    def _edit_snapshot(self) -> tuple[str, str, int, int]:
        return (self._current_content(), self.color,
                self.width(), self.height())

    def _queue_drop(self, event) -> None:
        """편집 중이 아닌 메모에 이미지나 파일을 끌어다 놓았다.

        그대로 넣으면 화면에만 들어가고 호스트로는 가지 않는다(잠금 없이는
        보낼 수 없다). 놓은 것을 들고 있다가 잠금을 받은 뒤에 넣는다."""
        source = event.mimeData()
        if not self._text.canInsertFromMimeData(source):
            return
        kept = QMimeData()
        if source.hasUrls():
            kept.setUrls(source.urls())
        if source.hasImage():
            kept.setImageData(source.imageData())
        if source.hasText():
            kept.setText(source.text())
        self._pending_drop = kept
        self._pending_drop_pos = self._text.cursorForPosition(
            event.position().toPoint()).position()
        self._pending_check = None
        if self.is_locked_by_other():
            self._pending_drop = None
            self._show_overlay(i18n.t("sticky.lock_overlay",
                                      holder=self._lock_holder))
            return
        self._request_edit()
        if not self._pending_lock and not self._edit_mode:
            # 편집을 청하지 못했다(다른 사람이 편집 중이다).
            self._pending_drop = None

    def _apply_pending_drop(self) -> None:
        source, self._pending_drop = self._pending_drop, None
        if source is None:
            return
        cursor = self._text.textCursor()
        cursor.setPosition(max(0, min(self._pending_drop_pos,
                                      self._text.document().characterCount() - 1)))
        self._text.setTextCursor(cursor)
        self._text.insertFromMimeData(source)
        if not self.isActiveWindow():
            # 다른 창을 쓰던 채로 끌어다 놓았다. 앞에 있지 않은 메모가 잠금을 쥔
            # 채 남지 않게, 넣은 것만 보내고 편집을 끝낸다(링크를 넣을 때와 같다).
            self._commit_and_release()

    def add_file_links(self, paths: list[str]) -> None:
        """파일·폴더를 가리키는 링크를 본문 끝에 한 줄에 하나씩 넣는다.

        잠금 없이는 호스트로 보낼 수 없으므로, 편집 중이 아니면 편집을 먼저
        시작하고 잠금을 받은 뒤에 넣는다."""
        self._pending_links += [p for p in paths if p]
        if not self._pending_links:
            return
        if self._edit_mode:
            self._apply_pending_links()
            return
        if self.is_locked_by_other() or self._folded or self._fold_busy():
            self._pending_links = []
            return
        if not self._pending_lock:
            self._request_edit()
        if not self._pending_lock and not self._edit_mode:
            # 편집을 청하지 못했다(다른 사람이 편집 중이다).
            self._pending_links = []

    # ── 본문 자동 인식 ──

    def _refresh_smart(self) -> None:
        if self._edit_mode:
            return
        if not self.isVisible():
            self._smart_stale = True        # 보일 때 한다(숨긴 메모가 많으면 켤 때 느리다)
            return
        self._smart_stale = False
        from datetime import datetime
        self._text.refresh_smart(datetime.now())

    def _on_selection_changed(self) -> None:
        from .. import text_detect as td
        text = self._text.textCursor().selectedText().replace("\u2029", "\n")
        # 아주 긴 고르기(메모 전체 등)는 세지 않는다. 합계를 보려는 고르기가 아니다.
        summary = td.summarize_numbers(text) if text and len(text) <= 20000 else None
        if summary is None:
            self._sum_line.hide()
            self._refresh_extra_rule()
            return
        from decimal import ROUND_HALF_UP, Decimal
        # 평균은 소수 둘째 자리까지(1.333333 처럼 길게 늘어놓지 않는다).
        avg = summary.format(summary.average.quantize(Decimal("0.01"), ROUND_HALF_UP))
        self._sum_line.setText(i18n.t("smart.sum", total=summary.total_text,
                                      avg=avg, count=summary.count))
        self._sum_line.show()
        self._refresh_extra_rule()

    def _smart_event(self, event) -> bool:
        """보기 상태에서 날짜를 누르면 알림, 연락처를 누르면 복사."""
        et = event.type()
        if self._edit_mode:
            return False
        if et == QEvent.MouseMove and event.buttons() == Qt.NoButton:
            if self._text.smart_at(event.position().toPoint()) is not None:
                self._text.viewport().setCursor(Qt.PointingHandCursor)
                return True
            return False
        if et == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            hit = self._text.smart_at(event.position().toPoint())
            self._smart_press = hit
            if hit is not None:
                event.accept()
                return True
            return False
        if et == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            pressed, self._smart_press = self._smart_press, None
            if pressed is None:
                return False
            hit = self._text.smart_at(event.position().toPoint())
            if hit is not None and hit.start == pressed.start:
                if hit.kind == "date":
                    self.dateReminderRequested.emit(self.note_id, hit.value, hit.has_time)
                else:
                    QGuiApplication.clipboard().setText(str(hit.value))
                    self._flash_status(i18n.t("smart.copied", text=str(hit.value)), 1500)
            event.accept()
            return True
        return False

    # ── @이름 자동 완성 ──

    def set_people(self, names: list[str]) -> None:
        """'@' 뒤에 고를 수 있는 이름(참가자). 비어 있으면 자동 완성을 하지 않는다."""
        self._people = [n for n in names if n]

    def _mention_prefix(self) -> str | None:
        """커서 바로 앞이 '@이름의 앞부분' 이면 그 앞부분(빈 글일 수 있다)."""
        cursor = self._text.textCursor()
        if cursor.hasSelection():
            return None
        block = cursor.block()
        before = block.text()[:cursor.position() - block.position()]
        found = re.search(r"(?:^|[^\w.@])@([^\s@]{0,12})$", before)
        return found.group(1) if found else None

    def _update_mention_list(self) -> None:
        if not getattr(self, "_edit_mode", False) or not self._people:
            self._hide_mention_list()
            return
        prefix = self._mention_prefix()
        names = ([n for n in self._people if n.lower().startswith(prefix.lower())]
                 if prefix is not None else [])
        if not names or (len(names) == 1 and names[0] == prefix):
            self._hide_mention_list()
            return
        from PySide6.QtWidgets import QListWidget
        box = self._mention_list
        if box is None:
            box = self._mention_list = QListWidget(self)
            box.setFocusPolicy(Qt.NoFocus)
            box.setStyleSheet(
                "QListWidget { background: #FFFFFF; border: 1px solid #D4D4D4;"
                " border-radius: 6px; padding: 2px; font-size: 12px; outline: none; }"
                "QListWidget::item { padding: 3px 8px; border-radius: 4px; color: #1F1F1F; }"
                "QListWidget::item:selected { background: #F0F0F0; }")
            box.itemClicked.connect(lambda item: self._accept_mention(item.text()))
        box.clear()
        box.addItems(names[:6])
        box.setCurrentRow(0)
        at = self._text.textCursor()
        at.setPosition(max(0, at.position() - len(prefix) - 1))
        rect = self._text.cursorRect(at)
        top_left = self._text.viewport().mapTo(self, rect.bottomLeft())
        row_h = box.sizeHintForRow(0) if box.count() else 22
        box.resize(140, min(6, box.count()) * row_h + 6)
        box.move(max(4, min(top_left.x(), self.width() - box.width() - 4)), top_left.y() + 2)
        box.show()
        box.raise_()

    def _hide_mention_list(self) -> None:
        if self._mention_list is not None:
            self._mention_list.hide()

    def mention_choices(self) -> list[str]:
        """지금 떠 있는 이름 목록(시험용)."""
        box = self._mention_list
        if box is None or box.isHidden():
            return []
        return [box.item(i).text() for i in range(box.count())]

    def _accept_mention(self, name: str) -> None:
        prefix = self._mention_prefix()
        if prefix is None:
            return
        cursor = self._text.textCursor()
        cursor.movePosition(QTextCursor.Left, QTextCursor.KeepAnchor, len(prefix))
        cursor.insertText(name + " ")
        self._text.setTextCursor(cursor)
        self._hide_mention_list()

    def _mention_key(self, event) -> bool:
        """이름 목록이 떠 있을 때의 키(위아래, Enter, Tab, Esc). 처리했으면 True."""
        box = self._mention_list
        if box is None or box.isHidden():
            return False
        key = event.key()
        if key in (Qt.Key_Down, Qt.Key_Up):
            row = box.currentRow() + (1 if key == Qt.Key_Down else -1)
            box.setCurrentRow(max(0, min(box.count() - 1, row)))
            return True
        if key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Tab):
            item = box.currentItem()
            if item is not None:
                self._accept_mention(item.text())
            return True
        if key == Qt.Key_Escape:
            self._hide_mention_list()
            return True
        return False

    # ── 같은 줄 모아 세기 ──

    def set_word_wrap(self, on: bool) -> None:
        """자동 줄바꿈. 끄면 줄이 메모 폭에서 꺾이지 않고 가로로 굴러간다."""
        self._wrap_on = bool(on)
        mode = self._text.LineWrapMode
        self._text.setLineWrapMode(mode.WidgetWidth if on else mode.NoWrap)
        self._text.setHorizontalScrollBarPolicy(
            Qt.ScrollBarAlwaysOff if on else Qt.ScrollBarAsNeeded)
        if hasattr(self, "_widget_timer"):
            self._widget_timer.start()

    def word_wrap(self) -> bool:
        return self._wrap_on

    def set_tally(self, on: bool) -> None:
        self._tally_on = bool(on)
        self._refresh_tally()

    def _refresh_tally(self) -> None:
        if getattr(self, "_tally_line", None) is None:
            return          # 아직 만드는 중이다
        if not self._tally_on:
            self._tally_line.hide()
            self._refresh_extra_rule()
            return
        import html as _html
        from .. import text_detect as td
        rows = td.tally_lines(self._text.toPlainText().splitlines())
        if not rows:
            self._tally_line.hide()
            self._refresh_extra_rule()
            return
        ink, soft = self._extra_ink
        lines = [f'<span style="color:{soft}">{_html.escape(i18n.t("smart.tally"))}</span>']
        for row in rows:
            text = _html.escape(f"{row.item} {row.quantity}{row.unit}")
            if row.names:
                names = _html.escape(", ".join(row.names))
                text += f'&nbsp;&nbsp;<span style="color:{soft}">{names}</span>'
            lines.append(text)
        self._tally_line.setText("<br>".join(lines))
        self._tally_line.show()
        self._refresh_extra_rule()

    def _on_attached_range(self, start: int, end: int) -> None:
        """파일을 글이나 표로 첨부했다. 그 범위의 저장용 목록 표시를 화면용으로."""
        self._convert_loaded_markers(start, end)
        self._text.sync_link_icons()

    def set_attach_modes(self, overrides: dict | None) -> None:
        self._text.set_attach_modes(overrides)

    def add_clipboard(self, mime: QMimeData) -> None:
        """클립보드에서 가져온 내용(글, 서식 있는 글, 그림, 파일)을 넣는다. 편집 중이
        아니면 편집을 먼저 시작하고 잠금을 받은 뒤에 넣는다."""
        self._pending_mime = mime
        if self._edit_mode:
            self._apply_pending_links()
            return
        if self.is_locked_by_other() or self._folded or self._fold_busy():
            self._pending_mime = None
            return
        if not self._pending_lock:
            self._request_edit()
        if not self._pending_lock and not self._edit_mode:
            self._pending_mime = None

    def _apply_pending_links(self) -> None:
        mime, self._pending_mime = self._pending_mime, None
        if mime is not None:
            cursor = self._text.textCursor()
            cursor.movePosition(QTextCursor.End)
            self._text.setTextCursor(cursor)
            self._text.insertFromMimeData(mime)
            if not self._pending_links:
                if self._pending_commit is None:
                    self._autosave_while_editing()
                return
        paths, self._pending_links = self._pending_links, []
        if not paths:
            return
        cursor = self._text.textCursor()
        cursor.movePosition(QTextCursor.End)
        self._text.setTextCursor(cursor)
        for path in paths:
            cursor = self._text.textCursor()
            if cursor.block().length() > 1:
                cursor.insertBlock()
                self._text.setTextCursor(cursor)
            self._text.insert_file_links([path])
        if not self.isActiveWindow():
            # Windows 가 앞에 세우기를 막아 뒤에 남은 메모다. 보이지 않는 창이
            # 잠금을 쥐고 있지 않게, 넣은 것만 보내고 편집을 끝낸다.
            self._commit_and_release()
        elif self._pending_commit is None:
            self._autosave_while_editing()

    def _save_after_image(self) -> None:
        """이미지를 넣은 직후에 한 번 보낸다(크기 제한을 따지지 않는다)."""
        if not self._edit_mode:
            return
        if self._pending_commit is not None:
            # 앞서 보낸 것이 아직 돌아오지 않았다. 판 번호가 어긋나지 않게 기다린다.
            self._image_save_timer.start()
            return
        self._autosave_while_editing()

    def show_lock_denied(self, holder: str) -> None:
        self._pending_lock = False
        self._widget_delete_pending = []
        self._pending_uncheck = False
        self._pending_check = None
        self._pending_drop = None
        self._pending_links = []
        self._abandon_grant = False
        self._text.setReadOnly(True)
        self._set_lock_text(i18n.t("sticky.lock_editing_by", holder=holder))
        # 손잡이 줄의 글자는 그대로 두고, 왜 편집이 안 되는지만 잠깐 알린다.
        self._show_overlay(i18n.t("sticky.lock_overlay", holder=holder))

    def _close_sticky(self) -> None:
        if self._edit_mode:
            self._commit_and_release()
        self.hideRequested.emit(self.note_id)

    def _set_format_bar_enabled(self, enabled: bool) -> None:
        # 서식 줄은 편집 중에만 자리를 차지한다. 읽는 동안에도 빈 줄을 남겨 두면
        # 본문이 그 위에서 잘려, 메모 아래쪽이 빈 띠에 가린 것처럼 보인다.
        for b in (self._bold_btn, self._italic_btn, self._underline_btn,
                  self._strike_btn, self._font_color_btn, self._highlight_btn,
                  self._list_btn, self._image_btn):
            b.setEnabled(enabled)
        self._widget_btn.setEnabled(enabled and self._widgets_ok)
        self._format_bar.setVisible(
            enabled and not getattr(self, "_folded", False))
        self._fit_edit_size(enabled)
        self._place_grip()

    def _fit_edit_size(self, editing: bool) -> None:
        """편집하는 동안은 서식 줄과 글 두어 줄이 들어가게 작은 메모를 잠시 넓힌다.
        편집을 마치면, 그동안 크기를 손대지 않았을 때 원래 크기로 돌린다."""
        if getattr(self, "_folded", False) or not hasattr(self, "_grip"):
            return
        if editing:
            # 최소 크기를 올리면 Qt 가 창을 먼저 키우므로 원래 크기를 그 전에 잡는다.
            before = QSize(self.size())
            want = QSize(max(before.width(), config.EDIT_MIN_WIDTH),
                         max(before.height(), config.EDIT_MIN_HEIGHT))
            self.setMinimumSize(config.EDIT_MIN_WIDTH, config.EDIT_MIN_HEIGHT)
            if want != before:
                self._edit_restore = (before, QSize(want))
                self.resize(want)
            return
        self.setMinimumSize(config.MIN_NOTE_WIDTH, config.MIN_NOTE_HEIGHT)
        restore = getattr(self, "_edit_restore", None)
        self._edit_restore = None
        if restore is not None and self.size() == restore[1]:
            self.resize(restore[0])

    def _shared_size(self) -> tuple[int, int]:
        """공유 PC 에 적어 둘 크기. 편집하느라 잠시 넓힌 크기는 보내지 않는다."""
        restore = getattr(self, "_edit_restore", None)
        if restore is not None and self.size() == restore[1]:
            return restore[0].width(), restore[0].height()
        return self.width(), self.height()

    def fit_to_content(self) -> None:
        """본문 높이에 맞춰 메모 높이를 줄이거나 늘린다(크기 조절 손잡이를 두 번 누르면).
        한 줄짜리 메모는 그 한 줄만큼으로 줄고, 긴 메모는 화면 높이까지만 늘어난다."""
        if self._folded or self._fold_busy():
            return
        doc = self._text.document()
        margins = self._text.viewportMargins()
        bar = self._format_bar.height() if self._format_bar.isVisible() else 0
        want = math.ceil(4 + config.HANDLE_BAR_HEIGHT + margins.top() + margins.bottom()
                         + doc.size().height() + bar)
        avail = self._avail()
        want = max(self.minimumHeight(), min(int(want), avail.height()))
        self.resize(self.width(), want)
        pos = self._pull_on_screen(self.pos(), self.size())
        if pos != self.pos():
            self.move(pos)
            self._open_pos = QPoint(pos)
            self.positionChanged.emit(self.note_id, pos.x(), pos.y())
        self._edit_restore = None
        self._local_size_set = True
        self.sizeChanged.emit(self.note_id, self.width(), self.height())

    def _widen_for_table(self) -> None:
        """방금 넣은 표가 본문 폭을 넘으면 메모를 그만큼 넓힌다(화면 폭까지)."""
        doc = self._text.document()
        over = math.ceil(doc.size().width() - self._text.viewport().width())
        if over <= 0:
            return
        avail = self._avail()
        width = min(self.width() + over + 2, avail.width())
        if width <= self.width():
            return
        self.resize(width, self.height())
        pos = self._pull_on_screen(self.pos(), self.size())
        if pos != self.pos():
            self.move(pos)
            self._open_pos = QPoint(pos)
            self.positionChanged.emit(self.note_id, pos.x(), pos.y())
        restore = getattr(self, "_edit_restore", None)
        if restore is not None:
            # 표 때문에 넓힌 폭은 편집을 마쳐도 그대로 둔다.
            self._edit_restore = None
        self._local_size_set = True
        self.sizeChanged.emit(self.note_id, self.width(), self.height())

    def _place_grip(self) -> None:
        g = self._grip
        g.move(self.width() - g.width() - 1, self.height() - g.height() - 1)
        g.raise_()

    def _refresh_lock_ui(self) -> None:
        if self._edit_mode:
            return
        if self._lock_holder is None:
            self._set_lock_text(
                i18n.t("sticky.offline") if self._offline else "")
            self._overlay.hide()
            self._text.setReadOnly(True)
            self._set_format_bar_enabled(False)
        elif self._lock_holder == self._my_nickname:
            self._set_lock_text(i18n.t("sticky.lock_editing_self"))
        else:
            # 누가 편집 중인지는 손잡이 줄에 적는다. 본문은 가리지 않는다.
            self._set_lock_text(
                i18n.t("sticky.lock_editing_by", holder=self._lock_holder))
            self._set_format_bar_enabled(False)
        self._fit_chips()

    def _show_overlay(self, text: str) -> None:
        """본문 가운데에 알림을 잠깐 띄운다."""
        self._overlay.setText(text)
        if self._folded or self._fold_busy():
            return
        self._reposition_overlay()
        self._overlay.show()
        self._overlay.raise_()
        self._overlay_timer.start()

    def _reposition_overlay(self) -> None:
        m = 16
        body_top = config.HANDLE_BAR_HEIGHT
        body_h = max(60, self._text.height())
        width = max(80, min(self.width() - 2 * m, 260))
        height = min(max(44, self._overlay.heightForWidth(width)),
                     max(44, body_h - 2 * m))
        self._overlay.setGeometry(
            (self.width() - width) // 2,
            body_top + max(m, (body_h - height) // 2),
            width, height)

    def set_offline(self, offline: bool) -> None:
        """호스트와 끊겼는지 알린다(손잡이 줄에 '오프라인'을 적는다).

        끊긴 동안 고친 글은 메인 창이 이 PC 에 적어 두었다가 다시 붙으면 올릴지
        묻는다. 연결돼 있을 때부터 편집하던 글은 그대로 이어 쓸 수 있고, 다시
        붙으면 메인 창이 unsent_edit() 로 가져가 보낸다."""
        offline = bool(offline)
        if offline == self._offline:
            return
        self._offline = offline
        self._pending_lock = False
        self._refresh_lock_ui()

    def _version_to_send(self) -> int:
        """지금 보내는 저장에 적을 판 번호.

        앞서 보낸 저장의 답이 아직 오지 않았으면(글을 쓰다 멈춘 직후 곧바로 다른
        곳을 누른 경우), 공유 PC 는 온 차례대로 처리하므로 이 저장이 닿을 때는
        판 번호가 하나 올라가 있다. 그대로 보내면 거절돼 글이 사라진다."""
        if self._pending_commit is None:
            self._pending_commit_version = self.version
            return self.version
        self._earlier_commit = self._pending_commit
        return self.version + 1

    def unsent_base_version(self) -> int:
        """보내지 못한 글(unsent_edit)이 보고 쓴 판 번호."""
        return self.version if self._edit_mode else self._pending_commit_version

    def is_editing(self) -> bool:
        return self._edit_mode

    def lock_lost(self) -> str | None:
        """편집 중인데 공유 PC 가 잠금을 풀었다(오래 손대지 않았다).

        편집을 끝낸다. 그대로 두면 창에는 옛 글이 남은 채 다른 사람이 고친 판
        번호만 따라가, 나중에 한 글자만 쳐도 그 사람의 글을 덮어쓴다. 아직
        보내지 못한 글이 있으면 돌려준다(메인 창이 잠금을 다시 받아 보낸다)."""
        if not self._edit_mode:
            return None
        self._autosave_timer.stop()
        self._idle_save_timer.stop()
        self._edit_mode = False
        self.update()
        self._text.setReadOnly(True)
        snapshot = self._edit_snapshot()
        unsent = snapshot[0] if snapshot != self._edit_baseline else None
        self._edit_baseline = None
        if unsent is not None:
            self._pending_commit = unsent
            self._pending_commit_version = self.version
            self._content = unsent
        self._set_lock_text(i18n.t("sticky.offline") if self._offline else "")
        self._set_format_bar_enabled(False)
        return unsent

    def cancel_pending_lock(self) -> None:
        """편집 요청을 들어줄 수 없다. 기다리는 상태를 푼다."""
        self._pending_lock = False
        self._widget_delete_pending = []
        self._pending_uncheck = False
        self._pending_check = None
        self._pending_drop = None
        self._pending_links = []

    def unsent_edit(self) -> str | None:
        """호스트에 닿았는지 확인되지 않은 편집 내용. 없으면 None."""
        if self._edit_mode:
            if (self._pending_commit is not None
                    or self._edit_snapshot() != self._edit_baseline):
                return self._current_content()
            return None
        return self._pending_commit

    # ── 메모 안 위젯 ──

    def set_widget_env(self, ok: bool, games: bool, *, offline: bool = False) -> None:
        """공유 PC 가 위젯을 아는가(모르면 그림으로만 보인다), 미니게임을 넣을 수 있는가.
        끊긴 동안(``offline``)에도 그림으로만 보인다."""
        self._widgets_ok = bool(ok)
        self._widget_games = bool(games)
        self._widget_btn.setEnabled(self._edit_mode and self._widgets_ok)
        self._widget_btn.setToolTip(i18n.t("widget.menu") if self._widgets_ok
                                    else i18n.t("sticky.offline") if offline
                                    else i18n.t("widget.host_old"))
        for block in self._widget_views.values():
            block.set_readonly(not self._widgets_ok)
        self._widget_timer.start()

    def set_widget_data(self, widget_id: str, data: dict) -> None:
        self._widget_data[widget_id] = data
        block = self._widget_views.get(widget_id)
        if block is not None:
            block.set_data(data)
            if not self._widgets_ok:
                block.set_readonly(True)
        self._widget_timer.start()

    def widget_views(self) -> dict:
        """본문 위에 놓인 위젯(시험용)."""
        return dict(self._widget_views)

    def widget_kinds(self) -> list[str]:
        """넣을 수 있는 위젯 종류(미니게임은 설정에서 켰을 때만)."""
        from .. import widgets
        out = []
        for name in widgets.kinds():
            kind = widgets.get(name)
            if kind is not None and (not kind.is_game or self._widget_games):
                out.append(name)
        return out

    def _show_widget_gallery(self, at: QPoint | None = None) -> str | None:
        """위젯을 고르는 판(그림과 이름의 칸). 고르면 넣고 그 종류를 돌려준다."""
        if not self._edit_mode:
            self._request_edit()
            return None
        if not self._widgets_ok:
            return None
        menu = edit_menu.new_menu(self)
        gallery = WidgetGallery(self.widget_kinds(), menu)
        chosen: dict = {}
        gallery.picked.connect(lambda k: (chosen.update(kind=k), menu.close()))
        action = QWidgetAction(menu)
        action.setDefaultWidget(gallery)
        menu.addAction(action)
        self._widget_gallery = gallery          # 시험이 쓴다
        if at is None:
            at = self._widget_btn.mapToGlobal(QPoint(0, -gallery.sizeHint().height() - 12))
        edit_menu.run_menu(menu, at)
        menu.deleteLater()
        kind = chosen.get("kind")
        if kind and self._edit_mode:
            self.insert_widget(kind)
        return kind

    def insert_widget(self, name: str) -> str | None:
        """커서가 있는 줄 아래(빈 줄이면 그 줄)에 위젯 칸을 넣는다. 정할 것이 있는 종류는
        바로 설정 칸이 열린다."""
        from .. import widgets
        kind = widgets.get(name)
        if kind is None or not self._edit_mode or not self._widgets_ok:
            return None
        data = kind.new_data(self._my_nickname)
        wid = widgets.new_id()
        cursor = self._text.textCursor()
        cursor.clearSelection()
        plain = RichTextEdit._plain_format(cursor.charFormat())
        own = QTextBlockFormat(cursor.blockFormat())
        for prop in (MARKER_PROP, LEVEL_PROP, SPACING_PROP):
            own.clearProperty(prop)
        own.setIndent(0)
        own.setLeftMargin(0.0)
        cursor.beginEditBlock()
        try:
            if cursor.block().length() > 1:
                # 글을 가르지 않고 그 줄 끝에서 새 줄을 연다.
                cursor.movePosition(QTextCursor.EndOfBlock)
                cursor.insertBlock(own, plain)
            else:
                cursor.setBlockFormat(own)
            cursor.insertText(wobj.CHAR, wobj.object_format(wid))
            cursor.insertBlock(own, plain)
        finally:
            cursor.endEditBlock()
        self._text.setTextCursor(cursor)
        self._text.setCurrentCharFormat(plain)
        self._widget_data[wid] = data
        self.widgetCreated.emit(self.note_id, wid, data)
        self._sync_widget_views()
        block = self._widget_views.get(wid)
        if block is not None and wants_setup(name):
            block.open_setup(first=True)
            self._sync_widget_views()
        # 기본 크기 메모에서도 위젯이 잘리지 않게 메모를 키운다.
        self._make_room_for_widget(wid)
        return wid

    def delete_widget(self, wid: str) -> None:
        """본문에서 위젯을 뺀다(본문을 고치므로 편집을 받은 뒤에)."""
        if not self._edit_mode:
            if self.is_locked_by_other() or self._notice_blocks_me():
                return              # 편집을 받을 수 없다. 나중에 몰래 지우지 않는다.
            if wid not in self._widget_delete_pending:
                self._widget_delete_pending.append(wid)
            self._request_edit()
            return
        for found, pos, _fmt in wobj.find_objects(self._text.document()):
            if found != wid:
                continue
            cursor = QTextCursor(self._text.document())
            cursor.beginEditBlock()
            cursor.setPosition(pos)
            cursor.setPosition(pos + 1, QTextCursor.KeepAnchor)
            cursor.removeSelectedText()
            # 위젯만 있던 빈 줄도 함께 뺀다.
            if cursor.block().length() <= 1 and cursor.block().next().isValid():
                cursor.deleteChar()
            cursor.endEditBlock()
            break
        block = self._widget_views.pop(wid, None)
        if block is not None:
            block.hide()
            block.deleteLater()
        self._widget_sizes.pop(wid, None)
        if self._pending_commit is None:
            self._autosave_while_editing()

    def _widget_copy_picture(self, wid: str) -> str:
        """복사하는 위젯의 그림(data URL). 다른 메모에 붙이면 이 그림이 들어간다."""
        for found, _pos, fmt in wobj.find_objects(self._text.document()):
            if found == wid:
                try:
                    return self._widget_picture(wid, fmt)[0] or ""
                except Exception:       # 그림을 못 만들면 그림 없이 복사한다
                    return ""
        return ""

    def _apply_pending_widget_deletes(self) -> None:
        pending, self._widget_delete_pending = self._widget_delete_pending, []
        for wid in pending:
            self.delete_widget(wid)

    def _widget_column(self) -> int:
        """본문 글 칸의 폭. 굴리는 막대가 보이든 말든 같은 값이다(막대가 나타났다
        사라지며 위젯 폭이 흔들리지 않게 막대 자리를 늘 뺀다)."""
        doc = self._text.document()
        width = self._text.viewport().width() - 2 * int(doc.documentMargin())
        bar = self._text.verticalScrollBar()
        if bar.isVisible():
            width += bar.width()
        return width - max(8, bar.sizeHint().width())

    # 위젯이 그려지는 가장 좁은 폭. 메모가 더 좁으면 위젯을 넣을 때 메모를 넓힌다.
    _WIDGET_MIN = 160
    # 위젯을 넣을 때 메모 글 칸이 이만큼은 되게 넓힌다.
    _WIDGET_ROOM = 220

    def _widget_fit_width(self) -> int:
        """'메모 폭에 맞춤' 의 폭(글 칸 폭, 560 까지)."""
        return max(self._WIDGET_MIN, min(560, self._widget_column()))

    def _widget_max_width(self) -> int:
        """위젯은 글 칸보다 넓어지지 않는다(넓게 정해 두어도 메모 폭까지)."""
        return max(self._WIDGET_MIN, self._widget_column())

    def _make_room_for_widget(self, wid: str) -> None:
        """위젯이 다 보이도록 메모를 키우고(화면 안에서) 그 자리로 굴린다. 줄이지는 않는다."""
        size = self._widget_sizes.get(wid)
        if size is None or self.is_folded():
            return
        geo = self.geometry()
        screen = self.screen()
        area = screen.availableGeometry() if screen is not None else geo
        grow_w = max(0, self._WIDGET_ROOM - self._widget_column())
        grow_h = max(0, size[1] + 16 - self._text.viewport().height())
        new_w = max(geo.width(), min(geo.width() + grow_w, area.right() - geo.left() + 1))
        new_h = max(geo.height(), min(geo.height() + grow_h, area.bottom() - geo.top() + 1))
        if (new_w, new_h) != (geo.width(), geo.height()):
            self.resize(new_w, new_h)
            self._sync_widget_views()
        self._scroll_to_widget(wid)

    def _scroll_to_widget(self, wid: str) -> None:
        block = self._widget_views.get(wid)
        if block is None:
            return
        bar = self._text.verticalScrollBar()
        rect = block.geometry()
        view_h = self._text.viewport().height()
        if rect.bottom() > view_h:
            bar.setValue(bar.value() + min(rect.bottom() - view_h + 8, rect.top()))
        elif rect.top() < 0:
            bar.setValue(bar.value() + rect.top() - 8)

    def _widget_png(self, kind, data: dict, wid: str, width: int) -> bytes:
        """저장용 위젯 그림(위젯 id 와 이름을 품은 PNG). 보는 사람과 상관없는 모습이다."""
        from datetime import date
        from .. import widgets
        img = kind.render(data, width, viewer="", today=date.today(), background=self.color)
        return widgets.to_png(img, wid, label=widgets.label_of(data))

    def _widget_picture(self, wid: str, fmt) -> tuple[str, int, int]:
        """저장할 때 위젯 칸 대신 적을 그림: (data URL, 폭, 높이). 같은 모습이면 앞서
        그린 것을 쓴다(저장 내용이 괜히 바뀌지 않게)."""
        import json
        from datetime import date
        from .. import widgets
        data = self._widget_data.get(wid)
        kind = widgets.usable_kind(data) if data is not None else None
        size = self._widget_sizes.get(wid)
        if kind is not None:
            width = size[0] if size else self._widget_fit_width()
            try:
                key = (json.dumps(data, sort_keys=True, ensure_ascii=False), width,
                       self.color, date.today().isoformat())
            except (TypeError, ValueError):
                key = None
            cached = self._widget_shots.get(wid)
            if key is not None and cached is not None and cached[0] == key:
                return cached[1]
            png = self._widget_png(kind, data, wid, width)
            image = QImage.fromData(png)
            if not image.isNull():
                out = (widgets.to_data_url(png), image.width(), image.height())
                if key is not None:
                    self._widget_shots[wid] = (key, out)
                return out
        # 그릴 수 있는 데이터가 없다(받기 전, 새 판의 데이터). 마지막으로 그린 그림이나
        # 저장돼 있던 그림을 그대로 둔다.
        cached = self._widget_shots.get(wid)
        if cached is not None:
            return cached[1]
        url = fmt.property(wobj.P_URL) if fmt is not None else None
        if isinstance(url, str) and url:
            w, h = wobj.stored_size(fmt)
            return url, w, h
        # 데이터도 그림도 없다(받기 전). 빈 틀이라도 id 를 품은 그림으로 남긴다.
        from ..widgets.base import Ctx
        from ..widgets.draw import blank
        img = blank(240, i18n.t("widget.menu"), Ctx())
        png = widgets.to_png(img, wid)
        return widgets.to_data_url(png), img.width(), img.height()

    def _widget_object_rect(self, pos: int, width: int, height: int) -> QRect:
        """본문 위치 ``pos`` 의 위젯 칸이 뷰포트에서 차지하는 자리."""
        doc = self._text.document()
        tb = doc.findBlock(pos)
        layout_rect = doc.documentLayout().blockBoundingRect(tb)    # 배치를 마치게 한다
        layout = tb.layout()
        rel = pos - tb.position()
        line = layout.lineForTextPosition(rel) if layout is not None else None
        if line is None or not line.isValid():
            cursor = QTextCursor(doc)
            cursor.setPosition(pos)
            rect = self._text.cursorRect(cursor)
            return QRect(rect.left(), rect.bottom() - height + 1, width, height)
        x = line.cursorToX(rel)
        if isinstance(x, tuple):
            x = x[0]
        # 위젯 칸은 줄의 기준선 위에 선다(칸의 바닥이 기준선).
        top = layout_rect.y() + line.y() + line.ascent() - height
        left = layout_rect.x() + x
        dx = self._text.horizontalScrollBar().value()
        dy = self._text.verticalScrollBar().value()
        return QRect(int(round(left - dx)), int(round(top - dy)), width, height)

    def _place_widget_views(self) -> None:
        """위젯 칸이 있는 자리에 위젯을 옮긴다(크기는 그대로). 가벼워서 본문이 다시 놓일
        때마다 부른다."""
        if not self._widget_views or self._widget_fixing or self._placing_widgets:
            return
        self._placing_widgets = True
        try:
            seen: set[str] = set()
            for wid, pos, _fmt in wobj.find_objects(self._text.document()):
                if wid in seen:
                    continue
                seen.add(wid)
                block = self._widget_views.get(wid)
                size = self._widget_sizes.get(wid)
                if block is None or size is None:
                    continue
                rect = self._widget_object_rect(pos, *size)
                if block.geometry() != rect:
                    block.setGeometry(rect)
        finally:
            self._placing_widgets = False

    def _sync_widget_views(self) -> None:
        """본문의 위젯 칸마다 그 자리에 위젯을 놓는다. 위젯의 폭이나 높이가 바뀌면 칸의
        크기만 고친다(본문은 그대로다). 데이터를 모르는 위젯은 저장된 그림이 보인다.
        같은 위젯이 두 번 들어 있으면(복사) 앞의 것만 위젯이다."""
        from datetime import date
        from .. import widgets
        if self._widget_fixing:
            return
        doc = self._text.document()
        viewport = self._text.viewport()
        fit = self._widget_fit_width()
        most = self._widget_max_width()
        placed: list[tuple[str, int]] = []
        dirty: list[int] = []
        seen: set[str] = set()
        for wid, pos, _fmt in wobj.find_objects(doc):
            if wid in seen:
                continue
            data = self._widget_data.get(wid)
            kind = widgets.usable_kind(data) if data is not None else None
            if kind is None:
                continue
            seen.add(wid)
            block = self._widget_views.get(wid)
            if block is None:
                block = WidgetBlock(kind, data, self._my_nickname, date.today(),
                                    background=self.color,
                                    can_delete=not self._notice_blocks_me(), parent=viewport)
                block.opRequested.connect(
                    lambda op, w=wid: self.widgetOp.emit(self.note_id, w, op))
                block.sizeChanged.connect(self._widget_timer.start)
                block.roomWanted.connect(lambda w=wid: self._widget_room.add(w))
                block.deleteRequested.connect(lambda w=wid: self.delete_widget(w))
                self._widget_views[wid] = block
                block.set_readonly(not self._widgets_ok)
                dirty.append(pos)        # 저장된 그림 대신 위젯이 보이게 다시 그린다
            width = block.wanted_width() or fit
            width = max(self._WIDGET_MIN, min(width, most))
            need = block.content_height(width)
            if self._widget_sizes.get(wid) != (width, need):
                self._widget_sizes[wid] = (width, need)
                dirty.append(pos)
            placed.append((wid, pos))
        for wid in list(self._widget_sizes):
            if wid not in seen:
                self._widget_sizes.pop(wid, None)
        if dirty:
            self._widget_fixing = True
            try:
                for pos in sorted(set(dirty), reverse=True):
                    doc.markContentsDirty(pos, 1)
            finally:
                self._widget_fixing = False
        for wid, pos in placed:
            block = self._widget_views[wid]
            width, need = self._widget_sizes[wid]
            block.setGeometry(self._widget_object_rect(pos, width, need))
            block.show()
        # 설정 칸이 열리거나 커진 위젯은 다 보이게 메모를 키운다.
        room, self._widget_room = self._widget_room, set()
        for wid in room:
            if wid in self._widget_views:
                self._make_room_for_widget(wid)
        for wid in list(self._widget_views):
            if wid not in seen:
                view = self._widget_views.pop(wid)
                view.hide()
                view.deleteLater()

    # ── 프로그램 창에 붙이기(실험) ──

    def set_attached(self, label: str | None, *, can_attach: bool = False) -> None:
        """붙은 창의 이름(없으면 붙지 않음). ``can_attach`` 는 설정에서 켰는가."""
        self._attached = bool(label)
        self._can_attach = bool(can_attach)
        self._attach_full = label or ""
        self._attach_chip.setText(self._attach_full)
        self._attach_chip.setToolTip(i18n.t("attach_win.tip", name=label or ""))
        self._attach_chip.setVisible(self._attached)
        self._fit_chips()
        if not self._attached:
            self._apply_z_order(initial=True)

    def is_attached(self) -> bool:
        return self._attached

    def _show_window_menu(self) -> None:
        """붙은 창 이름표의 메뉴: 창에서 떼기."""
        menu = edit_menu.new_menu(self)
        add_centered_menu_action(menu, svg_icon("close", color="#3A3A3A"),
                                 i18n.t("attach_win.detach"),
                                 lambda: self.detachRequested.emit(self.note_id))
        edit_menu.run_menu(menu, self._attach_chip.mapToGlobal(
            QPoint(0, self._attach_chip.height())))
        menu.deleteLater()

    def _apply_head_ink(self, ink: str, dark: bool) -> None:
        """머리 줄과 본문 아래 칸의 새 요소 색(어두운 메모에서는 밝게)."""
        self._head_ink = ink
        soft = "#B8B8B8" if dark else "#6B6B6B"
        hover = "rgba(255, 255, 255, 0.16)" if dark else "rgba(0, 0, 0, 0.06)"
        for chip in (self._remind_chip, self._reset_chip, self._read_chip):
            chip.set_ink(ink, dark)
        self._notice_label.setStyleSheet(
            f"color: {ink}; font-size: 11px; font-weight: 700; background: transparent;"
            " padding: 0px;")
        self._attach_chip.setIcon(svg_icon("window", color=ink))
        self._attach_chip.setStyleSheet(
            f"QToolButton {{ border: none; background: transparent; color: {ink};"
            " font-size: 11px; padding: 0px 3px; }"
            f"QToolButton:hover {{ background: {hover}; border-radius: 4px; }}")
        line = "rgba(255, 255, 255, 0.16)" if dark else "rgba(0, 0, 0, 0.08)"
        self._extra_rule.setStyleSheet(f"background: {line}; border: none;")
        self._extra_ink = (ink, soft)
        style = (f"color: {ink}; font-size: 11px; background: transparent;"
                 " padding: 3px 12px 3px 23px;")
        self._sum_line.setStyleSheet(style)
        self._tally_line.setStyleSheet(style)
        if self._notice_icon:
            self._notice_label.setPixmap(svg_icon("info", color=ink).pixmap(13, 13))
        if self._tally_on:
            self._refresh_tally()

    def _refresh_extra_rule(self) -> None:
        self._extra_rule.setVisible(not self._sum_line.isHidden()
                                    or not self._tally_line.isHidden())

    def _flash_status(self, text: str, ms: int) -> None:
        """머리 줄의 상태 글 자리에 잠깐 알린다(편집 중 같은 원래 글은 뒤에 되돌린다)."""
        if not self._flash_text:
            self._flash_saved = self._lock_label.text()
        self._flash_text = text
        self._set_lock_text(text)
        self._flash_timer.start(ms)

    def _end_flash_status(self) -> None:
        if self._flash_text and self._lock_label.text() == self._flash_text:
            self._set_lock_text(getattr(self, "_flash_saved", ""))
        self._flash_text = ""

    # ── 공지 ──

    def set_notice(self, owner: str | None, *, can_make: bool = False,
                   can_lift: bool = False, read_text: str = "", read_short: str = "") -> None:
        """공지인가(만든 사람의 이름), 공지로 만들 수 있는가, 풀 수 있는가, 내 공지면
        본 사람 수 글(긴 꼴과 짧은 꼴)."""
        self._notice_owner = owner or None
        self._notice_can_make = bool(can_make)
        self._notice_can_lift = bool(can_lift)
        self._notice_label.setVisible(bool(owner))
        self._read_chip.set_forms([read_text, read_short] if read_text else [])
        self._read_chip.setVisible(bool(read_text))
        self._read_chip.setToolTip(read_text)
        self._fit_chips()

    def notice_owner(self) -> str | None:
        return self._notice_owner

    def _notice_blocks_me(self) -> bool:
        return bool(self._notice_owner) and self._notice_owner != self._my_nickname

    def _show_notice_line(self) -> None:
        self._flash_status(i18n.t("notice.only_owner", name=self._notice_owner or ""), 3000)

    def _on_view_timer(self) -> None:
        if (self.isVisible() and not self._folded
                and self.rect().contains(self.mapFromGlobal(QCursor.pos()))):
            self.viewed.emit(self.note_id)

    def _request_edit(self) -> None:
        if self._notice_blocks_me():
            # 남의 공지다. 고치지 않고 까닭을 한 줄로.
            self._show_notice_line()
            return
        if self._edit_mode or self._pending_lock:
            # 뷰어를 연 뒤 응답이 오기 전에 다시 눌렀다면 편집하려는 뜻이다.
            self._abandon_grant = False
            return
        if self._lock_holder and self._lock_holder != self._my_nickname:
            return
        # 끊긴 동안에도 요청은 보낸다. 메인 창이 이 PC 에서만 고치게 해 준다.
        self._pending_lock = True
        self.requestLock.emit(self.note_id)

    def _commit_and_release(self) -> None:
        if not self._edit_mode:
            return
        self._autosave_timer.stop()
        self._idle_save_timer.stop()
        self._edit_mode = False
        self.update()
        self._text.setReadOnly(True)
        snapshot = self._edit_snapshot()
        # baseline 과 같으면 updated_at·이력 행을 건드리지 않도록 전송을 건너뛴다.
        if snapshot != self._edit_baseline:
            new_content = snapshot[0]
            version = self._version_to_send()
            self._pending_commit = new_content
            self._content = new_content
            self.commitEdit.emit(
                self.note_id, new_content, self.color,
                *self._shared_size(), version,
            )
        self._edit_baseline = None
        self.releaseLock.emit(self.note_id)
        self._set_lock_text(i18n.t("sticky.offline") if self._offline else "")
        self._set_format_bar_enabled(False)

    def _on_body_changed(self) -> None:
        if self._edit_mode:
            self._idle_save_timer.start()

    def _idle_save(self) -> None:
        """입력이 멈췄다. 잠금은 쥔 채로 지금까지 쓴 것을 보낸다."""
        if not self._edit_mode:
            return
        if self._pending_commit is not None:
            # 앞서 보낸 것이 아직 돌아오지 않았다. 판 번호가 어긋나지 않게 기다린다.
            self._idle_save_timer.start()
            return
        if len(self._current_content()) > self._IDLE_SAVE_MAX_CHARS:
            return
        self._autosave_while_editing()

    def _autosave_while_editing(self) -> None:
        """잠금을 유지한 채 flush 하되, baseline 이후 변화가 없으면 no-op
        이라 손대지 않은 편집기가 updated_at 을 올리지 않는다."""
        if not self._edit_mode:
            return
        snapshot = self._edit_snapshot()
        if snapshot == self._edit_baseline:
            return
        new_content = snapshot[0]
        version = self._version_to_send()
        self._pending_commit = new_content
        self._content = new_content
        self.commitEdit.emit(
            self.note_id, new_content, self.color,
            *self._shared_size(), version,
        )
        # baseline 을 굴려 이후 idle 자동 저장 tick 도 no-op 이 되게 한다.
        self._edit_baseline = snapshot

    def flush_pending_edit(self) -> None:
        """앱 종료 전 메인 창이 호출해 저장되지 않은 편집을 잃지 않게 한다."""
        if self._edit_mode:
            self._commit_and_release()

    def _recent_note_colors(self) -> list[str]:
        return font_colors.clean_recents(
            getattr(self._settings, "recent_note_colors", []))

    def _remember_color(self, name: str, color: QColor) -> None:
        """'다른 색'에서 고른 색을 최근 색으로 남긴다(``name`` 은 설정의 이름)."""
        settings = self._settings
        if settings is None or not hasattr(settings, name):
            return
        setattr(settings, name, font_colors.remember(
            font_colors.clean_recents(getattr(settings, name)), color))
        try:
            settings.save()
        except OSError:
            pass

    def _ask_color(self, title: str, initial: QColor,
                   recents: list[str]) -> QColor | None:
        """색 고르는 창을 띄운다. 창이 뜨면 메모가 비활성화된다. 그때 편집이 끝나
        버리지 않게 막는다."""
        self._opening_dialog = True
        try:
            return font_colors.ask_color(self, title, initial, recents)
        finally:
            QTimer.singleShot(0, self._end_opening_dialog)

    def _show_color_menu(self) -> None:
        if not self._edit_mode:
            self._request_edit()
            return
        choice: dict = {}
        menu = QMenu(self)
        container = QWidget(menu)
        outer = QVBoxLayout(container)
        outer.setContentsMargins(6, 6, 6, 4)
        outer.setSpacing(4)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)
        current = (self.color or "").upper()

        def swatch(hex_color: str, tip: str) -> QToolButton:
            btn = QToolButton(container)
            btn.setFixedSize(28, 28)
            btn.setIcon(color_swatch_icon(hex_color, 22))
            btn.setIconSize(QSize(22, 22))
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setToolTip(tip)
            btn.setAccessibleName(tip)
            # 지금 메모의 색에는 테두리를 두른다.
            btn.setCheckable(True)
            btn.setChecked(hex_color.upper() == current)
            btn.setStyleSheet(
                "QToolButton { border: 2px solid transparent; padding: 1px; "
                "border-radius: 7px; background: transparent; }"
                "QToolButton:hover { background: rgba(0,0,0,0.08); }"
                "QToolButton:checked { border: 2px solid #1F1F1F; }"
            )
            btn.clicked.connect(
                lambda _checked=False, c=hex_color, m=menu:
                    (choice.update(color=c), m.close()))
            return btn

        for index, hex_color in enumerate(config.COLORS):
            grid.addWidget(swatch(hex_color, color_name(hex_color)),
                           *divmod(index, config.COLOR_COLUMNS))
        outer.addLayout(grid)
        # '다른 색'에서 고른 색들.
        recents = [c for c in self._recent_note_colors()
                   if c not in config.COLORS][:config.COLOR_COLUMNS]
        if recents:
            outer.addWidget(font_colors.caption(i18n.t("sticky.font_color_recent")))
            line = QHBoxLayout()
            line.setContentsMargins(0, 0, 0, 0)
            line.setSpacing(6)
            for hex_color in recents:
                line.addWidget(swatch(hex_color, hex_color))
            line.addStretch(1)
            outer.addLayout(line)
        outer.addWidget(font_colors.rule(container))
        foot = QHBoxLayout()
        foot.setContentsMargins(0, 0, 0, 0)
        foot.addStretch(1)
        more = font_colors.more_button(container)
        more.clicked.connect(
            lambda _checked=False, m=menu: (choice.update(more=True), m.close()))
        foot.addWidget(more)
        outer.addLayout(foot)
        action = QWidgetAction(menu)
        action.setDefaultWidget(container)
        menu.addAction(action)
        _wire_swatch_keys(menu, container, columns=config.COLOR_COLUMNS)
        edit_menu.run_menu(
            menu, self._color_btn.mapToGlobal(QPoint(0, self._color_btn.height())))
        menu.deleteLater()
        if choice.get("more"):
            self._pick_note_color()
        elif "color" in choice:
            self._set_color(choice["color"])

    def _pick_note_color(self) -> None:
        """견본에 없는 메모 색을 고른다. 색상 코드를 쳐 넣을 수도 있다."""
        if not self._edit_mode:
            return
        color = self._ask_color(i18n.t("sticky.tip_color"), QColor(self.color),
                                self._recent_note_colors())
        if color is None or not self._edit_mode:
            return
        self._set_color(font_colors.code_of(color))
        if font_colors.code_of(color) not in config.COLORS:
            self._remember_color("recent_note_colors", color)

    def _show_highlight_menu(self) -> None:
        if not self._edit_mode:
            self._request_edit()
            return
        menu = QMenu(self)
        container = QWidget(menu)
        outer = QVBoxLayout(container)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(4)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)
        for index, hex_color in enumerate(HIGHLIGHT_COLORS):
            r, c = divmod(index, 3)
            btn = QToolButton(container)
            btn.setFixedSize(28, 28)
            btn.setIcon(_highlight_swatch_icon(hex_color, 22))
            btn.setIconSize(QSize(22, 22))
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setStyleSheet(
                "QToolButton { border: none; padding: 2px; "
                "border-radius: 6px; background: transparent; }"
                "QToolButton:hover { background: rgba(0,0,0,0.08); }"
            )
            btn.clicked.connect(
                lambda _checked=False, c=hex_color, m=menu:
                    (self._apply_highlight(c), m.close()))
            grid.addWidget(btn, r, c)
        outer.addLayout(grid)

        divider = QFrame(container)
        divider.setFrameShape(QFrame.HLine)
        divider.setStyleSheet("color: rgba(0,0,0,0.08);")
        outer.addWidget(divider)

        clear_btn = QToolButton(container)
        clear_btn.setText(i18n.t("sticky.bg_none"))
        clear_btn.setIcon(_highlight_swatch_icon(None, 18))
        clear_btn.setIconSize(QSize(18, 18))
        clear_btn.setCursor(Qt.PointingHandCursor)
        clear_btn.setFocusPolicy(Qt.NoFocus)
        clear_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        clear_btn.setFixedHeight(26)
        clear_btn.setStyleSheet(
            "QToolButton { border: none; padding: 2px 6px; "
            "border-radius: 6px; background: transparent; "
            "color: #1F1F1F; font-size: 12px; text-align: left; }"
            "QToolButton:hover { background: rgba(0,0,0,0.08); }"
        )
        clear_btn.clicked.connect(
            lambda _checked=False, m=menu:
                (self._apply_highlight(None), m.close()))
        outer.addWidget(clear_btn)

        action = QWidgetAction(menu)
        action.setDefaultWidget(container)
        menu.addAction(action)
        _wire_swatch_keys(menu, container, columns=3)
        menu.exec(self._highlight_btn.mapToGlobal(
            QPoint(0, self._highlight_btn.height())))

    def _show_more_menu(self) -> None:
        is_private = bool(self.private_owner)
        menu = edit_menu.new_menu(self)
        ink = "#3A3A3A"

        def add(icon: str | None, text: str, handler) -> None:
            add_centered_menu_action(
                menu, svg_icon(icon, color=ink) if icon else None, text, handler)

        # 보이는 모양: 투명도와 자동 줄바꿈.
        self._add_opacity_row(menu)
        add_centered_menu_action(
            menu, svg_icon("wrap", color=ink), i18n.t("sticky.more_wrap"),
            lambda: self.wrapToggled.emit(self.note_id, not self._wrap_on),
            checked=self._wrap_on)
        menu.addSeparator()
        add("eye_off", i18n.t("sticky.more_hide"),
            lambda: self.hideRequested.emit(self.note_id))
        add("clock", i18n.t("sticky.tip_history"),
            lambda: self.historyRequested.emit(self.note_id))
        menu.addSeparator()
        # 놓는 자리: 접기와 프로그램 창에 붙이기.
        if self._dock:
            # 벽에 붙여 둔 메모는 접으면 늘 그 자리의 탭으로 돌아간다.
            add("fold", i18n.t("sticky.tip_fold"), self.fold)
        else:
            add("fold", i18n.t("sticky.more_fold"),
                lambda: self.fold(style=nf.FOLD_SLIP))
            add("note", i18n.t("sticky.more_fold_icon"),
                lambda: self.fold(style=nf.FOLD_ICON))
        if self._attached:
            add("window", i18n.t("attach_win.detach"),
                lambda: self.detachRequested.emit(self.note_id))
        elif self._can_attach:
            add("window", i18n.t("attach_win.menu"),
                lambda: self.attachRequested.emit(self.note_id))
        menu.addSeparator()
        add("bell", i18n.t("sticky.more_reminder"),
            lambda: self.reminderRequested.emit(self.note_id))
        if self.todo_progress() or not self._reset_chip.isHidden():
            # 할 일(체크 상자)이 있는 메모에만 둔다. 예약을 지울 수 있게 걸어 둔 것은 남긴다.
            add("check_reset", i18n.t("sticky.more_check_reset"),
                lambda: self.checkResetRequested.emit(self.note_id))
        add_centered_menu_action(
            menu, svg_icon("tally", color=ink), i18n.t("smart.tally_toggle"),
            lambda: self.tallyToggled.emit(self.note_id, not self._tally_on),
            checked=self._tally_on)
        menu.addSeparator()
        # 누가 보는가: 나만 보기와 공지.
        add("lock", i18n.t("sticky.more_private_off") if is_private
            else i18n.t("sticky.more_private_on"),
            lambda: self.privateToggleRequested.emit(self.note_id, not is_private))
        if self._notice_owner and (self._notice_can_lift
                                   or self._notice_owner == self._my_nickname):
            add("info", i18n.t("notice.lift"),
                lambda: self.noticeRequested.emit(self.note_id, False))
        elif not self._notice_owner and self._notice_can_make:
            add("info", i18n.t("notice.make"),
                lambda: self.noticeRequested.emit(self.note_id, True))
        menu.addSeparator()
        add("printer", i18n.t("print.menu_print"),
            lambda: self.printRequested.emit(self.note_id, "print"))
        add("download", i18n.t("print.menu_pdf"),
            lambda: self.printRequested.emit(self.note_id, "pdf"))
        if not self._notice_blocks_me():
            menu.addSeparator()
            add("trash", i18n.t("sticky.more_delete"),
                lambda: self.deleteRequested.emit(self.note_id))
        start = self._opacity
        self._opacity_preview = True
        try:
            edit_menu.run_menu(menu, self._more_btn.mapToGlobal(
                QPoint(0, self._more_btn.height())))
        finally:
            self._opacity_preview = False
            self.update()
        if self._opacity != start:
            self.opacityChanged.emit(self.note_id, self._opacity)

    def _add_opacity_row(self, menu: QMenu) -> None:
        """투명도(0 ~ 80%, 5 단위)를 고르는 한 줄. 고르는 대로 메모에 보인다."""
        box = QWidget(menu)
        box.setStyleSheet("background: transparent;")
        row = QHBoxLayout(box)
        # 그림 칸(40)과 글의 세로줄을 다른 메뉴 항목과 맞춘다.
        row.setContentsMargins(4, 5, 12, 5)
        row.setSpacing(0)
        mark = QLabel(box)
        mark.setFixedWidth(40)
        mark.setAlignment(Qt.AlignCenter)
        mark.setPixmap(svg_icon("opacity", color="#3A3A3A").pixmap(16, 16))
        row.addWidget(mark)
        label = QLabel(i18n.t("sticky.more_transparency"), box)
        slider = QSlider(Qt.Horizontal, box)
        slider.setRange(0, (100 - config.MIN_NOTE_OPACITY) // 5)
        slider.setPageStep(2)
        slider.setFixedWidth(120)
        slider.setAccessibleName(i18n.t("sticky.more_transparency"))
        slider.setValue((100 - self._opacity) // 5)
        value = QLabel(box)
        value.setMinimumWidth(34)
        value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        def show(step: int) -> None:
            value.setText(f"{step * 5}%")
            self.set_opacity(100 - step * 5)

        slider.valueChanged.connect(show)
        show(slider.value())
        row.addWidget(label)
        row.addSpacing(10)
        row.addWidget(slider)
        row.addSpacing(10)
        row.addWidget(value)
        action = QWidgetAction(menu)
        action.setDefaultWidget(box)
        menu.addAction(action)

    # ── 알림과 체크 해제 예약 ──

    def _set_lock_text(self, text: str) -> None:
        """머리 줄의 상태 글(편집 중, 연결 끊김). 자리가 바뀌니 표시를 다시 맞춘다."""
        self._lock_label.setText(text)
        self._fit_chips()

    def _make_chip(self, icon: str, handler) -> _ScheduleChip:
        chip = _ScheduleChip(icon, self._handle)
        chip.clicked.connect(handler)
        chip.hide()
        return chip

    def set_schedule(self, reminder="", reminder_tip: str = "",
                     reset="", reset_tip: str = "") -> None:
        """머리 줄에 다음 알림과 다음 체크 해제 때를 적는다. 때는 글 하나나 긴
        것부터 짧은 것까지의 꼴 목록이다. 비어 있으면 감춘다."""
        for chip, forms, tip in ((self._remind_chip, reminder, reminder_tip),
                                 (self._reset_chip, reset, reset_tip)):
            chip.set_forms([forms] if isinstance(forms, str) else list(forms or ()))
            text = chip.full_text()
            chip.setToolTip(tip or text)
            chip.setAccessibleName(tip or text)
            chip.setVisible(bool(text))
        self._fit_chips()
        if self._folded:
            self.update()              # 접힌 모양의 종

    def has_reminder(self) -> bool:
        """다음 알림이 걸려 있는가(접힌 모양에 종을 그린다)."""
        return bool(self._remind_chip.full_text())

    _SHAKE_MS = 460

    def shake(self) -> None:
        """알림이 왔을 때 접힌 메모를 좌우로 살짝 흔든다(어느 메모인지 보이게).
        움직임을 끈 PC에서는 흔들지 않는다."""
        if (not self.isVisible() or not self._folded or self._fold_busy()
                or getattr(self, "_shake_anim", None) is not None
                or not nf.system_animations_enabled()):
            return
        home = QPoint(self.pos())
        anim = QVariantAnimation(self)
        anim.setDuration(self._SHAKE_MS)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)

        def step(t) -> None:
            # 세 번 오가며 줄어드는 흔들림(5px 에서 0).
            dx = round(5 * (1.0 - float(t)) * math.sin(float(t) * math.pi * 6))
            self.move(home.x() + dx, home.y())

        def done() -> None:
            self.move(home)
            self._shake_anim = None
        anim.valueChanged.connect(step)
        anim.finished.connect(done)
        self._shake_anim = anim
        anim.start()

    def schedule_texts(self) -> tuple[str, str]:
        """머리 줄의 (다음 알림, 다음 체크 해제) 때(긴 꼴). 시험용."""
        return (self._remind_chip.full_text() if not self._remind_chip.isHidden() else "",
                self._reset_chip.full_text() if not self._reset_chip.isHidden() else "")

    def schedule_levels(self) -> tuple[int, int]:
        """머리 줄 표시가 얼마나 줄었는가(0 이 가장 긴 꼴). 시험용."""
        return self._remind_chip.level(), self._reset_chip.level()

    # 붙은 창 이름을 줄여 둘 폭(처음 96, 그림으로 바꾸기 전 40 까지).
    _ATTACH_MAX = 96
    _ATTACH_MIN = 40

    def _attach_text_pad(self) -> int:
        """붙은 창 칩에서 글을 뺀 폭(그림, 사이, 여백). 단추에서 한 번 잰다."""
        pad = getattr(self, "_attach_pad", None)
        if pad is None:
            chip = self._attach_chip
            text, style = chip.text(), chip.toolButtonStyle()
            chip.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            chip.setText("M")
            pad = chip.sizeHint().width() - chip.fontMetrics().horizontalAdvance("M")
            chip.setText(text)
            chip.setToolButtonStyle(style)
            self._attach_pad = pad
        return pad

    def _fit_chips(self) -> None:
        """머리 줄이 좁으면 차례로 줄인다. 단추를 밀어내거나 눌러 찌그러뜨리지 않는다.

        1. 붙은 창 이름을 줄임표로(96px 까지)
        2. 체크 해제, 알림 때를 차례로 짧은 꼴로
        3. 읽음을 짧은 꼴로('3/5')
        4. 붙은 창 이름을 남은 자리만큼(40px 까지), 그다음 그림만으로
        5. 더 보기 메뉴에도 있는 기록 단추, 그다음 접기 단추를 감춘다
        6. 체크 해제, 알림을 그림만으로
        7. 체크 해제, 알림, 읽음, 붙은 창을 차례로 감춘다
        8. '공지' 를 그림으로(말풍선에 글)
        좁힐수록 줄기만 한다(뒤 단계에서 자리가 남아도 앞 단계를 되돌리지 않는다).
        '공지', 색, 더 보기, 닫기는 줄이거나 감추지 않는다."""
        if getattr(self, "_close_btn", None) is None:
            return      # 아직 만드는 중이다
        row = self._handle.layout()
        m = row.contentsMargins()
        gap = row.spacing()
        fixed = m.left() + m.right()
        for w in (self._private_icon, self._color_btn, self._more_btn, self._close_btn):
            if not w.isHidden():
                fixed += w.sizeHint().width() + gap
        if self._lock_label.text():
            fixed += self._lock_label.sizeHint().width()
        room = self._handle.width() - fixed
        chips = [c for c in (self._remind_chip, self._reset_chip) if c.full_text()]
        read = self._read_chip if self._read_chip.full_text() else None
        spare = (self._fold_btn, self._history_btn)     # 기록부터 감춘다
        fm = QFontMetrics(self._attach_chip.font())
        attach_text = self._attach_full if self._attached else ""
        notice = bool(self._notice_owner)
        nfm = QFontMetrics(self._notice_label.font())
        notice_text = i18n.t("notice.label")

        st = {"attach": "full" if attach_text else "none", "levels": [0] * len(chips),
              "read": 0, "keep": len(spare), "notice": "text"}

        pad = self._attach_text_pad() if attach_text else 0

        def attach_w() -> int:
            mode = st["attach"]
            if mode in ("none", "hidden"):
                return 0
            if mode == "icon":
                return 13 + 6 + 4             # 그림, 여백
            text_w = fm.horizontalAdvance(attach_text)
            if mode == "elided":
                text_w = min(text_w, st.get("attach_max", self._ATTACH_MAX))
            elif mode == "flex":
                text_w = min(text_w, self._ATTACH_MIN)
            return pad + text_w

        def need() -> int:
            n = 0
            if notice:
                n += (13 if st["notice"] == "icon" else nfm.horizontalAdvance(notice_text)) + gap
            w = attach_w()
            if w:
                n += w + gap
            for c, lv in zip(chips, st["levels"]):
                cw = c.width_at(lv)
                if cw:
                    n += cw + gap
            if read is not None:
                rw = read.width_at(st["read"])
                if rw:
                    n += rw + gap
            n += sum(b.sizeHint().width() + gap for b in spare[:st["keep"]])
            return n

        def steps():
            if st["attach"] == "full":
                st["attach"] = "elided"
                yield
            # 짧은 꼴로(그림만 앞까지). 체크 해제 쪽부터.
            for i in reversed(range(len(chips))):
                c = chips[i]
                while st["levels"][i] < c.levels() - 2:
                    st["levels"][i] += 1
                    yield
            if read is not None and read.levels() > 2:
                st["read"] = 1
                yield
            if st["attach"] == "elided":
                st["attach"] = "flex"                  # 남은 자리만큼(아래에서 정한다)
                yield
                st["attach"] = "icon"
                yield
            while st["keep"] > 0:
                st["keep"] -= 1
                yield
            for i in reversed(range(len(chips))):     # 그림만(체크 해제 쪽부터)
                st["levels"][i] = chips[i].levels() - 1
                yield
            for i in reversed(range(len(chips))):     # 체크 해제, 알림 차례로 감춤
                st["levels"][i] = chips[i].levels()
                yield
            if read is not None:
                st["read"] = read.levels()
                yield
            if st["attach"] == "icon":
                st["attach"] = "hidden"
                yield
            if notice:
                st["notice"] = "icon"
                yield

        if need() > room:
            for _ in steps():
                if need() <= room:
                    break
            if st["attach"] == "flex":
                # 붙은 창 이름은 남은 자리만큼(40 에서 96 사이) 줄임표로.
                st["attach_max"] = min(self._ATTACH_MAX,
                                       self._ATTACH_MIN + max(0, room - need()))
                st["attach"] = "elided"
        for c, lv in zip(chips, st["levels"]):
            c.set_level(lv)
        if read is not None:
            read.set_level(st["read"])
        for i, b in enumerate(spare):
            b.setVisible(i < st["keep"])
        mode = st["attach"]
        if mode in ("full", "elided"):
            shown = (attach_text if mode == "full"
                     else fm.elidedText(attach_text, Qt.ElideRight,
                                        st.get("attach_max", self._ATTACH_MAX)))
            self._attach_chip.setText(shown)
            self._attach_chip.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        else:
            self._attach_chip.setText("")
            self._attach_chip.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self._attach_chip.setVisible(self._attached and mode not in ("none", "hidden"))
        if self._attach_chip.isVisible():
            self._attach_chip.setFixedWidth(attach_w())
        icon_mode = notice and st["notice"] == "icon"
        if icon_mode != self._notice_icon:
            self._notice_icon = icon_mode
            if icon_mode:
                self._notice_label.setPixmap(
                    svg_icon("info", color=self._head_ink).pixmap(13, 13))
            else:
                self._notice_label.setText(notice_text)
        self._notice_label.setFixedWidth(
            13 if icon_mode else nfm.horizontalAdvance(notice_text) + 1)

    def has_checked(self) -> bool:
        block = self._text.document().begin()
        while block.isValid():
            if self._marker_code(block) == MARKER_CHECK1:
                return True
            block = block.next()
        return False

    def uncheck_all(self) -> bool:
        """체크한 상자를 모두 해제한다(체크 해제 예약). 글과 차례는 그대로다.

        잠금을 받아 고치고 바로 놓는다(보통 편집과 같은 길이라 공유 PC 는 옛
        버전이어도 된다). 고칠 것이 없거나 지금 고칠 수 없으면 False."""
        if not self.has_checked():
            return False
        if self._edit_mode or self._pending_lock or self.is_locked_by_other():
            return False
        self._pending_uncheck = True
        self._request_edit()
        if not self._pending_lock and not self._edit_mode:
            self._pending_uncheck = False
            return False
        return True

    def _uncheck_blocks(self) -> bool:
        doc = self._text.document()
        blocks = []
        block = doc.begin()
        while block.isValid():
            if self._marker_code(block) == MARKER_CHECK1:
                blocks.append(block.blockNumber())
            block = block.next()
        if not blocks:
            return False
        cursor = QTextCursor(doc)
        cursor.beginEditBlock()
        try:
            for number in blocks:
                self._set_block_marker(doc.findBlockByNumber(number), MARKER_CHECK0)
        finally:
            cursor.endEditBlock()
        return True

    def _uncheck_and_release(self) -> None:
        if self._uncheck_blocks():
            content = self._current_content()
            version = self._version_to_send()
            self._pending_commit = content
            self._content = content
            size = self._current_open_size()
            self.commitEdit.emit(self.note_id, content, self.color,
                                 size.width(), size.height(), version)
            if self._folded:
                self._refresh_face_tip()
            self.update()
        self.releaseLock.emit(self.note_id)

    # ── 할 일 진행률 ──

    def todo_progress(self) -> str:
        """접힌 모양에 적는 할 일 진행률('끝낸 것/모두'). 할 일이 없으면 빈 글.
        본문이 바뀔 때만 다시 센다(문서의 판 번호로 안다)."""
        doc = self._text.document()
        revision = doc.revision()
        cached = getattr(self, "_progress_cache", None)
        if cached is not None and cached[0] == revision and cached[1] is doc:
            return cached[2]
        total = done = 0
        block = doc.begin()
        while block.isValid():
            code = block.blockFormat().intProperty(MARKER_PROP)
            if code in (MARKER_CHECK0, MARKER_CHECK1):
                total += 1
                done += code == MARKER_CHECK1
            block = block.next()
        text = f"{done}/{total}" if total else ""
        self._progress_cache = (revision, doc, text)
        return text

    # ── 투명도 ──

    def set_opacity(self, percent: int) -> None:
        """바탕의 불투명도(%)를 정한다. 접힌 모양은 늘 진하다."""
        percent = max(config.MIN_NOTE_OPACITY, min(100, int(percent)))
        if percent != self._opacity:
            self._opacity = percent
            self.update()

    def opacity(self) -> int:
        return self._opacity

    def _paper_alpha(self) -> float:
        """지금 바탕을 칠할 알파. 편집 중이거나 마우스를 올리면 진하게."""
        if self._opacity >= 100 or self._folded:
            return 1.0
        if self._opacity_preview:
            return self._opacity / 100.0
        if self._edit_mode:
            return 1.0
        solid = (self._settings is None
                 or getattr(self._settings, "opacity_hover_solid", True))
        if solid and self._opacity_hover:
            return 1.0
        return self._opacity / 100.0

    def _set_opacity_hover(self, on: bool) -> None:
        if on != self._opacity_hover:
            self._opacity_hover = on
            if self._opacity < 100:
                self.update()

    def _show_text_menu(self, pos: QPoint, global_pos: QPoint) -> None:
        """본문을 우클릭했을 때의 메뉴.

        그림 위면 그림에 할 수 있는 일을 먼저 둔다. 편집 중이면 실행 취소부터
        모두 선택까지, 보기 상태면 편집과 전체 복사를 둔다."""
        menu = edit_menu.new_menu(self)
        ink = "#3A3A3A"
        hit = self._text.content_image_at(pos)
        if hit is not None:
            url, ordinal = hit
            add_centered_menu_action(
                menu, svg_icon("image", color=ink), i18n.t("sticky.menu_image_view"),
                lambda: self._open_image_viewer(url, ordinal))
            add_centered_menu_action(
                menu, svg_icon("copy", color=ink), i18n.t("sticky.menu_image_copy"),
                lambda: QApplication.clipboard().setImage(data_url_image(url)))
            add_centered_menu_action(
                menu, svg_icon("download", color=ink),
                i18n.t("sticky.menu_image_save"), lambda: self._save_image(url))
            menu.addSeparator()
        if self._edit_mode:
            edit_menu.add_edit_actions(menu, self._text)
            self._add_table_actions(menu, pos)
        else:
            can_edit = not self.is_locked_by_other()
            add_centered_menu_action(
                menu, svg_icon("edit", color=ink if can_edit else "#B0B0B0"),
                i18n.t("sticky.menu_edit"), self._request_edit, enabled=can_edit)
            plain = html_to_plain(self._content or "").strip()
            add_centered_menu_action(
                menu, svg_icon("copy", color=ink if plain else "#B0B0B0"),
                i18n.t("sticky.menu_copy_all"),
                lambda: QApplication.clipboard().setText(plain),
                enabled=bool(plain))
        # 서식 복사: 글자 서식 전체와 글머리 기호를 떠서 다른 메모에도 입힌다.
        # 뜨는 것은 보기 상태에서도 되고, 입히는 것은 편집 중일 때만 된다.
        menu.addSeparator()
        add_centered_menu_action(
            menu, svg_icon("brush", color=ink), i18n.t("sticky.menu_format_copy"),
            lambda: self._copy_format(pos))
        if self._edit_mode:
            ready = copied_format() is not None
            add_centered_menu_action(
                menu, svg_icon("brush", color=ink if ready else "#B0B0B0"),
                i18n.t("sticky.menu_format_paste"),
                lambda: self._paste_format(pos), enabled=ready)
        # 접기: 벽에 붙인 메모는 그 자리의 탭으로, 아니면 쪽지와 아이콘.
        menu.addSeparator()
        if self._dock:
            add_centered_menu_action(menu, svg_icon("fold", color=ink),
                                     i18n.t("sticky.tip_fold"), self.fold)
        else:
            add_centered_menu_action(menu, svg_icon("fold", color=ink),
                                     i18n.t("sticky.more_fold"),
                                     lambda: self.fold(style=nf.FOLD_SLIP))
            add_centered_menu_action(menu, svg_icon("note", color=ink),
                                     i18n.t("sticky.more_fold_icon"),
                                     lambda: self.fold(style=nf.FOLD_ICON))
        edit_menu.run_menu(menu, global_pos)
        menu.deleteLater()

    def _format_cursor(self, pos: QPoint | None) -> QTextCursor:
        """서식을 뜨거나 입힐 자리. 고른 글이 있으면 그것, 없으면 누른 자리다."""
        cursor = self._text.textCursor()
        if not cursor.hasSelection() and pos is not None:
            cursor = self._text.cursorForPosition(pos)
        return cursor

    def _copy_format(self, pos: QPoint | None = None) -> None:
        set_copied_format(self._text.format_at(self._format_cursor(pos)))

    def _paste_format(self, pos: QPoint | None = None) -> None:
        clip = copied_format()
        if clip is None or not self._edit_mode:
            return
        self._text.paste_format(clip, self._format_cursor(pos))
        # 이 메모의 바탕에서 보이지 않는 색이면 읽히는 밝기로 옮기고, 링크의
        # 밑줄과 색은 링크의 것으로 되돌린다.
        self._keep_text_readable(join_previous=True)
        self._text.sync_link_icons(join_previous=True)
        self._refresh_format_buttons()

    def _save_image(self, url: str) -> None:
        """본문의 이미지를 파일로 저장한다."""
        parsed = parse_data_url(url)
        if parsed is None:
            self._show_overlay(i18n.t("sticky.image_save_failed"))
            return
        raw, subtype = parsed
        ext = sniff_extension(raw, subtype)
        # 저장 창이 뜨면 메모가 비활성화된다. 그때 편집이 끝나 버리지 않게 막는다.
        self._opening_dialog = True
        try:
            path, _ = QFileDialog.getSaveFileName(
                self, i18n.t("sticky.menu_image_save"),
                i18n.t("sticky.image_save_name") + "." + ext,
                f"*.{ext}")
        finally:
            QTimer.singleShot(0, self._end_opening_dialog)
        if not path:
            return
        try:
            with open(path, "wb") as f:
                f.write(raw)
        except OSError:
            self._show_overlay(i18n.t("sticky.image_save_failed"))

    def _set_color(self, color: str) -> None:
        self._open_pix = None
        self.color = color
        self._color_btn.setIcon(color_swatch_icon(color))
        self._apply_theme_colors()
        self.update()

    def _apply_theme_colors(self) -> None:
        """어두운 색 메모에서도 글리프가 보이도록 전경을 배경에 맞춰 다시 칠한다."""
        fg = _text_color_for_bg(self.color)
        self._text.setStyleSheet(
            f"QTextEdit {{ background: transparent; color: {fg}; }}")
        # 링크 글자색도 바탕에 맞춘다. 보기 상태에서 바꾼 것은 사용자의 편집이
        # 아니므로 되돌리기 이력에 남기지 않는다.
        self._text.set_link_color(QColor(
            LINK_COLOR_DARK if fg == "#FFFFFF" else LINK_COLOR_LIGHT))
        if not self._edit_mode:
            self._text.document().clearUndoRedoStacks()
        # 메모 배경색이 제각각이라 스크롤바 핸들 색을 배경 대비에 맞춘다.
        if fg == "#FFFFFF":
            self._scrollbar.set_colors(QColor(255, 255, 255, 90),
                                       QColor(255, 255, 255, 140))
            self._hscrollbar.set_colors(QColor(255, 255, 255, 90),
                                        QColor(255, 255, 255, 140))
        else:
            self._scrollbar.set_colors(QColor(0, 0, 0, 60),
                                       QColor(0, 0, 0, 105))
            self._hscrollbar.set_colors(QColor(0, 0, 0, 60),
                                        QColor(0, 0, 0, 105))
        # 글머리·체크박스와 형광펜 글자도 바탕에 맞춘다.
        self._text.set_marker_tone(fg == "#FFFFFF")
        self._keep_text_readable()
        self._grip.set_tone(QColor(255, 255, 255, 170) if fg == "#FFFFFF"
                            else QColor(0, 0, 0, 125))
        lock_color = "#E5E5E5" if fg == "#FFFFFF" else "#3A3A3A"
        self._lock_label.setStyleSheet(
            f"color: {lock_color}; font-size: 11px; background: transparent;")
        self._apply_head_ink(lock_color, fg == "#FFFFFF")
        self._private_icon.setPixmap(
            svg_icon("lock", color=lock_color).pixmap(14, 14))
        self._history_btn.setIcon(svg_icon("clock", color=fg))
        self._fold_btn.setIcon(svg_icon("fold", color=fg))
        self._more_btn.setIcon(svg_icon("more", color=fg))
        self._close_btn.setIcon(svg_icon("close", color=fg))
        self._bold_btn.setIcon(svg_icon("bold", color=fg))
        self._italic_btn.setIcon(svg_icon("italic", color=fg))
        self._underline_btn.setIcon(svg_icon("underline", color=fg))
        self._strike_btn.setIcon(svg_icon("strike", color=fg))
        self._refresh_font_color_icon()
        self._highlight_btn.setIcon(svg_icon("highlight", color=fg))
        self._list_btn.setIcon(svg_icon("list", color=fg))
        self._image_btn.setIcon(svg_icon("attach", color=fg))

    def _keep_text_readable(self, join_previous: bool = False) -> None:
        """형광펜이나 지정한 글자색이 이 메모 바탕에서 묻히지 않게 한다."""
        changed = self._text.ensure_readable(
            QColor(self.color), QColor(_text_color_for_bg(self.color)),
            join_previous=join_previous)
        # 보기 상태에서 바꾼 것은 사용자의 편집이 아니므로 되돌리기에 남기지 않는다.
        if changed and not self._edit_mode:
            self._text.document().clearUndoRedoStacks()

    def _merge_char_format(self, fmt: QTextCharFormat) -> None:
        cursor = self._text.textCursor()
        if not cursor.hasSelection():
            cursor.select(QTextCursor.WordUnderCursor)
        cursor.mergeCharFormat(fmt)
        self._text.mergeCurrentCharFormat(fmt)

    def _toggle_bold(self) -> None:
        if not self._edit_mode:
            return
        current_weight = self._text.fontWeight()
        fmt = QTextCharFormat()
        fmt.setFontWeight(QFont.Normal if current_weight > QFont.Normal else QFont.Bold)
        self._merge_char_format(fmt)
        self._refresh_format_buttons()

    def _toggle_italic(self) -> None:
        if not self._edit_mode:
            return
        fmt = QTextCharFormat()
        fmt.setFontItalic(not self._text.fontItalic())
        self._merge_char_format(fmt)
        self._refresh_format_buttons()

    def _toggle_underline(self) -> None:
        if not self._edit_mode:
            return
        fmt = QTextCharFormat()
        fmt.setFontUnderline(not self._text.fontUnderline())
        self._merge_char_format(fmt)
        self._refresh_format_buttons()

    def _toggle_strike(self) -> None:
        if not self._edit_mode:
            return
        current = self._text.currentCharFormat()
        fmt = QTextCharFormat()
        fmt.setFontStrikeOut(not current.fontStrikeOut())
        self._merge_char_format(fmt)
        self._refresh_format_buttons()

    def _apply_highlight(self, color: str | None) -> None:
        """``None`` 은 no-brush 배경을 설정해 강조를 지운다. Qt 는 저장
        HTML 에서 이를 통째로 생략한다."""
        if not self._edit_mode:
            return
        fmt = QTextCharFormat()
        if color is None:
            fmt.setBackground(QBrush(Qt.NoBrush))
        else:
            fmt.setBackground(QBrush(QColor(color)))
        self._merge_char_format(fmt)
        # 어두운 메모에서 흰 글자에 밝은 형광펜을 칠하면 글자가 묻힌다.
        self._keep_text_readable(join_previous=True)
        self._refresh_format_buttons()

    def _toggle_highlight(self) -> None:
        if not self._edit_mode:
            return
        current = self._text.currentCharFormat()
        bg = current.background()
        already_highlighted = (
            bg.style() != Qt.NoBrush
            and bg.color().alpha() > 0
            and bg.color() != QColor(self.color)
        )
        self._apply_highlight(None if already_highlighted
                              else DEFAULT_HIGHLIGHT_COLOR)

    def _recent_inks(self) -> list[str]:
        return font_colors.clean_recents(
            getattr(self._settings, "recent_font_colors", []))

    def _font_color_panel(self) -> "font_colors.FontColorPanel":
        return font_colors.FontColorPanel(
            QColor(_text_color_for_bg(self.color)), self._current_ink(),
            self._recent_inks())

    def _show_font_color_menu(self) -> None:
        if not self._edit_mode:
            self._request_edit()
            return
        where = self._font_color_btn.mapToGlobal(
            QPoint(0, self._font_color_btn.height()))
        choice: dict = {}
        menu = QMenu(self)
        panel = self._font_color_panel()
        panel.picked.connect(
            lambda ink, m=menu: (choice.update(ink=ink), m.close()))
        panel.moreRequested.connect(
            lambda m=menu: (choice.update(more=True), m.close()))
        action = QWidgetAction(menu)
        action.setDefaultWidget(panel)
        menu.addAction(action)
        menu.aboutToShow.connect(
            lambda w=panel.first_focus(): QTimer.singleShot(0, w.setFocus))
        edit_menu.run_menu(menu, where)
        menu.deleteLater()
        if choice.get("more"):
            self._pick_font_color()
        elif "ink" in choice:
            self._apply_font_color(choice["ink"])

    def _pick_font_color(self) -> None:
        """견본에 없는 글자 색을 고른다. 색상 코드를 쳐 넣을 수도 있다. 고른 색은
        최근 색으로 남는다."""
        if not self._edit_mode:
            return
        start = self._current_ink() or QColor(_text_color_for_bg(self.color))
        ink = self._ask_color(i18n.t("sticky.tip_font_color"), start,
                              self._recent_inks())
        if ink is None or not self._edit_mode:
            return
        self._text.setFocus(Qt.OtherFocusReason)
        self._apply_font_color(ink)
        if not font_colors.in_palette(ink):
            self._remember_color("recent_font_colors", ink)

    def _apply_font_color(self, color) -> None:
        """``None`` 은 기본 색이다. 색을 정해 두지 않은 글자로 돌려, 메모 색을
        바꾸면 글자도 따라가게 한다(Qt 는 저장 HTML 에서 이를 통째로 생략한다)."""
        if not self._edit_mode:
            return
        fmt = QTextCharFormat()
        if color is None:
            fmt.setForeground(QBrush(Qt.NoBrush))
        else:
            fmt.setForeground(QBrush(QColor(color)))
        self._merge_char_format(fmt)
        # 이 바탕에서 보이지 않는 색을 골랐으면 고른 자리에서 바로 읽히는 밝기로
        # 옮긴다(나중에 메모를 다시 그릴 때 갑자기 바뀌지 않게).
        self._keep_text_readable(join_previous=True)
        self._refresh_format_buttons()

    def _toggle_list(self) -> None:
        if not self._edit_mode:
            return
        self._apply_bullet_marker(MARKER_SQUARE)

    def _marker_code(self, block) -> int:
        return block.blockFormat().intProperty(MARKER_PROP)

    def _set_block_marker(self, block, code: int,
                          level: int | None = None) -> None:
        """블록 마커를 설정/해제한다. 마커가 있으면 본문을 기호 칸만큼 들여
        paintEvent 가 여백에 기호를 그릴 자리를 비운다. 블록 포맷에 두므로
        undo 로 되돌릴 수 있고, 줄 분할 시 여백·단계와 함께 상속돼 리스트가
        자연히 이어진다. ``level`` 을 주면 단계도 함께 정한다."""
        if not block.isValid():
            return
        self._text.set_block_marker(block, code, level)
        self._text.restyle_lists(join_previous=True)
        self._text.viewport().update()

    def _strip_block_marker(self, block) -> None:
        self._set_block_marker(block, MARKER_NONE)

    def _make_marker_image_format(self, code: int) -> QTextImageFormat:
        fmt = QTextImageFormat()
        fmt.setName(marker_url_for_code(code) or "")
        fmt.setWidth(float(CHECKBOX_WIDTH))
        fmt.setHeight(float(CHECKBOX_HEIGHT))
        fmt.setVerticalAlignment(QTextCharFormat.AlignMiddle)
        return fmt

    def _selected_block_positions(self) -> list[int]:
        cursor = self._text.textCursor()
        doc = self._text.document()
        start_block = doc.findBlock(cursor.selectionStart())
        end_block = doc.findBlock(cursor.selectionEnd())
        positions = []
        block = start_block
        while block.isValid():
            positions.append(block.position())
            if block.position() >= end_block.position():
                break
            block = block.next()
        return positions

    def _apply_marker_to_selection(self, code: int, *,
                                    is_member) -> None:
        doc = self._text.document()
        positions = self._selected_block_positions()
        if not positions:
            return
        all_same = all(is_member(self._marker_code(doc.findBlock(pos)))
                       for pos in positions)
        target = MARKER_NONE if all_same else code
        cursor = self._text.textCursor()
        cursor.beginEditBlock()
        try:
            for pos in positions:
                self._set_block_marker(doc.findBlock(pos), target)
        finally:
            cursor.endEditBlock()
        self._refresh_format_buttons()

    def _apply_bullet_marker(self, code: int) -> None:
        """선택한 줄이 모두 같은 글머리면 제거하고, 아니면 설정한다."""
        if not self._edit_mode or code not in BULLET_CODES:
            return
        self._apply_marker_to_selection(code, is_member=lambda c: c == code)

    def _apply_checkbox_marker(self) -> None:
        """선택한 줄이 모두 체크박스면 제거하고, 아니면 설정한다."""
        if not self._edit_mode:
            return
        self._apply_marker_to_selection(
            MARKER_CHECK0,
            is_member=lambda c: c in (MARKER_CHECK0, MARKER_CHECK1))

    def _apply_number_marker(self) -> None:
        """선택한 줄이 모두 번호 줄이면 제거하고, 아니면 번호 목록으로 만든다."""
        if not self._edit_mode:
            return
        self._apply_marker_to_selection(
            MARKER_NUMBER, is_member=lambda c: c == MARKER_NUMBER)

    def _change_level(self, delta: int) -> bool:
        """선택한 목록 줄들을 한 단계 안으로(+1) 또는 밖으로(-1) 옮긴다.
        목록 줄이 하나도 없으면 False(키를 평소대로 처리하게 한다)."""
        doc = self._text.document()
        blocks = [doc.findBlock(pos) for pos in self._selected_block_positions()]
        items = [b for b in blocks if self._marker_code(b) in MARKER_CODES]
        if not items:
            return False
        cursor = self._text.textCursor()
        cursor.beginEditBlock()
        try:
            for block in items:
                level = self._text.block_level(block)
                target = max(0, min(MAX_LEVEL, level + delta))
                if target != level:
                    self._text.set_block_marker(
                        doc.findBlock(block.position()),
                        self._marker_code(block), target)
            self._text.restyle_lists()
        finally:
            cursor.endEditBlock()
        self._text.viewport().update()
        return True

    def _remove_list_markers(self) -> None:
        if not self._edit_mode:
            return
        doc = self._text.document()
        positions = self._selected_block_positions()
        cursor = self._text.textCursor()
        cursor.beginEditBlock()
        try:
            for pos in positions:
                self._set_block_marker(doc.findBlock(pos), MARKER_NONE)
        finally:
            cursor.endEditBlock()
        self._refresh_format_buttons()

    def _checkbox_block_at(self, pos: QPoint):
        """``pos`` 가 체크박스 줄의 왼쪽 여백(상자 자리)이면 그 줄을, 아니면 None."""
        block = self._text.cursorForPosition(pos).block()
        if self._marker_code(block) not in (MARKER_CHECK0, MARKER_CHECK1):
            return None
        start = QTextCursor(block)
        start.setPosition(block.position())
        if pos.x() >= self._text.cursorRect(start).left():
            return None
        return block

    def _try_toggle_checkbox_at(self, pos: QPoint) -> bool:
        """체크박스 줄의 왼쪽 여백(마커 영역)을 클릭하면 토글하고 True 를
        반환한다."""
        if not self._edit_mode:
            return False
        block = self._checkbox_block_at(pos)
        if block is None:
            return False
        self._toggle_checkbox_block(block)
        return True

    def _toggle_checkbox_block(self, block) -> None:
        code = self._marker_code(block)
        will_check = code == MARKER_CHECK0
        new_code = MARKER_CHECK1 if will_check else MARKER_CHECK0
        doc = self._text.document()
        bc = QTextCursor(doc)
        # Ctrl+Z 가 토글과 이동을 함께 되돌리도록 한 undo 단계로 묶는다.
        bc.beginEditBlock()
        try:
            self._set_block_marker(block, new_code)
            if will_check and self._move_checked_to_bottom_enabled():
                self._move_block_to_end(doc.findBlock(block.position()))
        finally:
            bc.endEditBlock()

    def _move_checked_to_bottom_enabled(self) -> bool:
        s = self._settings
        return bool(s is not None and getattr(s, "move_checked_to_bottom", False))

    def _move_block_to_end(self, block) -> None:
        """``block`` 이 이미 마지막이면 no-op."""
        doc = self._text.document()
        if not block.isValid() or not block.next().isValid():
            return
        code = self._marker_code(block)
        level = self._text.block_level(block)
        snap = QTextCursor(doc)
        snap.setPosition(block.position())
        snap.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
        fragment = snap.selection()
        # 뒤따르는 블록 구분자까지 제거해야 원래 위치에 빈 줄이 남지 않는다.
        cut = QTextCursor(doc)
        cut.setPosition(block.position())
        cut.setPosition(block.position() + block.length(),
                        QTextCursor.KeepAnchor)
        cut.removeSelectedText()
        end = QTextCursor(doc)
        end.movePosition(QTextCursor.End)
        end.insertBlock()
        end.insertFragment(fragment)
        self._set_block_marker(end.block(), code, level)
        # 다음 키 입력이 방금 체크한 줄의 편집을 이어가도록 커서를 옮긴다.
        self._text.setTextCursor(end)

    def _refresh_format_buttons(self) -> None:
        if not self._edit_mode:
            return
        current = self._text.currentCharFormat()
        self._bold_btn.setChecked(current.fontWeight() > QFont.Normal)
        self._italic_btn.setChecked(current.fontItalic())
        self._underline_btn.setChecked(current.fontUnderline())
        self._strike_btn.setChecked(current.fontStrikeOut())
        self._list_btn.setChecked(
            self._marker_code(self._text.textCursor().block()) in MARKER_CODES)
        self._refresh_font_color_icon()

    def _current_ink(self) -> QColor | None:
        """커서가 있는 글자에 따로 정해 둔 색. 기본 색이면 None."""
        fmt = self._text.currentCharFormat()
        if not fmt.hasProperty(QTextFormat.ForegroundBrush):
            return None
        return font_colors.explicit_ink(fmt)

    def _refresh_font_color_icon(self) -> None:
        """글자 색 단추 아래의 띠를 지금 글자의 색으로 칠한다."""
        fg = _text_color_for_bg(self.color)
        ink = self._current_ink() if self._edit_mode else None
        bar = ink.name() if ink is not None else fg
        if (fg, bar) != self._font_icon_state:
            self._font_icon_state = (fg, bar)
            self._font_color_btn.setIcon(text_color_icon(fg, bar))

    def _fill_list_menu(self) -> None:
        if self._list_menu.actions():
            return
        # 네모는 단계에 따라 기호가 바뀌는 기본 목록이다(■ ○ – ·).
        for code, key in ((MARKER_SQUARE, "sticky.menu_square"),
                          (MARKER_CIRCLE, "sticky.menu_circle"),
                          (MARKER_ARROW, "sticky.menu_arrow"),
                          (MARKER_INFO, "sticky.menu_info")):
            add_centered_menu_action(
                self._list_menu, list_menu_icon(code), i18n.t(key),
                lambda _checked=False, c=code: self._apply_bullet_marker(c))
        self._list_menu.addSeparator()
        add_centered_menu_action(
            self._list_menu, list_menu_icon(MARKER_NUMBER),
            i18n.t("sticky.menu_number"), self._apply_number_marker)
        add_centered_menu_action(
            self._list_menu, list_menu_icon(MARKER_CHECK0),
            i18n.t("sticky.menu_checkbox"),
            self._apply_checkbox_marker)
        self._list_menu.addSeparator()
        add_centered_menu_action(
            self._list_menu, None, i18n.t("sticky.menu_list_off"),
            self._remove_list_markers)

    def _fill_attach_menu(self) -> None:
        if self._attach_menu.actions():
            return
        add_centered_menu_action(
            self._attach_menu, svg_icon("image"),
            i18n.t("sticky.menu_attach_image"), self._attach_image)
        add_centered_menu_action(
            self._attach_menu, svg_icon("link"),
            i18n.t("sticky.menu_attach_link"), self._insert_link)
        add_centered_menu_action(
            self._attach_menu, svg_icon("table"),
            i18n.t("sticky.menu_attach_table"), self._show_table_picker)

    def _show_attach_menu(self) -> None:
        if not self._edit_mode:
            self._request_edit()
            return
        self._fill_attach_menu()
        self._attach_menu.exec(self._image_btn.mapToGlobal(
            QPoint(0, self._image_btn.height())))

    # ── 표 ──

    def _show_table_picker(self) -> None:
        """표의 크기를 고르는 칸. 마우스를 올린 만큼 칠해지고, 누르면 넣는다."""
        if not self._edit_mode:
            self._request_edit()
            return
        menu = edit_menu.new_menu(self)
        picker = TableSizePicker(menu)
        chosen: dict = {}
        picker.picked.connect(lambda r, c: (chosen.update(size=(r, c)), menu.close()))
        action = QWidgetAction(menu)
        action.setDefaultWidget(picker)
        menu.addAction(action)
        self._table_picker = picker      # 시험이 쓴다
        edit_menu.run_menu(menu, self._image_btn.mapToGlobal(
            QPoint(0, self._image_btn.height())))
        menu.deleteLater()
        if "size" in chosen and self._edit_mode:
            self.insert_table(*chosen["size"])

    def insert_table(self, rows: int, cols: int) -> None:
        """커서 자리에 표를 넣는다. 글이 있는 줄이면 다음 줄에 넣는다."""
        rows = max(1, min(TABLE_MAX_ROWS, int(rows)))
        cols = max(1, min(TABLE_MAX_COLS, int(cols)))
        fmt = QTextTableFormat()
        fmt.setBorder(1)
        fmt.setBorderStyle(QTextFrameFormat.BorderStyle_Solid)
        fmt.setBorderBrush(QBrush(QColor(TABLE_BORDER)))
        fmt.setBorderCollapse(True)
        fmt.setCellPadding(4)
        fmt.setCellSpacing(0)
        fmt.setWidth(QTextLength(QTextLength.PercentageLength, 100))
        cursor = self._text.textCursor()
        cursor.beginEditBlock()
        try:
            inside = cursor.currentTable()
            if inside is not None:
                # 표 안의 표는 만들지 않는다. 그 표의 바로 다음 줄에 넣는다.
                while inside.parentFrame() is not None and isinstance(
                        inside.parentFrame(), type(inside)):
                    inside = inside.parentFrame()
                cursor.setPosition(inside.lastPosition() + 1)
            if cursor.block().length() > 1:
                cursor.movePosition(QTextCursor.EndOfBlock)
                cursor.insertBlock()
            # 목록 줄의 기호가 칸으로 따라 들어가지 않게 한다.
            plain = QTextBlockFormat()
            cursor.setBlockFormat(plain)
            table = cursor.insertTable(rows, cols, fmt)
        finally:
            cursor.endEditBlock()
        self._text.setTextCursor(table.cellAt(0, 0).firstCursorPosition())
        self._widen_for_table()

    def _move_table_cell(self, step: int) -> bool:
        """Tab 으로 다음 칸, Shift+Tab 으로 앞 칸. 마지막 칸에서 Tab 은 줄을 더한다."""
        cursor = self._text.textCursor()
        table = cursor.currentTable()
        if table is None:
            return False
        cell = table.cellAt(cursor)
        index = cell.row() * table.columns() + cell.column() + step
        if index < 0:
            return True
        if index >= table.rows() * table.columns():
            if table.rows() >= TABLE_MAX_ROWS:
                return True
            table.appendRows(1)
        row, col = divmod(index, table.columns())
        self._text.setTextCursor(table.cellAt(row, col).firstCursorPosition())
        return True

    def _add_table_actions(self, menu: QMenu, pos: QPoint) -> None:
        """본문을 우클릭한 자리가 표 안이면 줄과 칸을 더하고 지우는 항목."""
        cursor = self._text.cursorForPosition(pos)
        table = cursor.currentTable()
        if table is None:
            return
        cell = table.cellAt(cursor)
        row, col = cell.row(), cell.column()
        ink = "#3A3A3A"
        menu.addSeparator()
        sub = edit_menu.new_menu(menu)
        sub.setTitle(i18n.t("sticky.menu_table"))
        sub.setIcon(svg_icon("table", color=ink))
        more_rows = table.rows() < TABLE_MAX_ROWS
        more_cols = table.columns() < TABLE_MAX_COLS
        for key, run, ok in (
                ("sticky.table_row_above", lambda: table.insertRows(row, 1), more_rows),
                ("sticky.table_row_below", lambda: table.insertRows(row + 1, 1), more_rows),
                ("sticky.table_col_left", lambda: table.insertColumns(col, 1), more_cols),
                ("sticky.table_col_right", lambda: table.insertColumns(col + 1, 1), more_cols)):
            add_centered_menu_action(sub, None, i18n.t(key), run, enabled=ok)
        sub.addSeparator()
        add_centered_menu_action(
            sub, None, i18n.t("sticky.table_row_delete"),
            lambda: self._remove_table_part(table, row=row))
        add_centered_menu_action(
            sub, None, i18n.t("sticky.table_col_delete"),
            lambda: self._remove_table_part(table, col=col))
        add_centered_menu_action(
            sub, None, i18n.t("sticky.table_delete"),
            lambda: self._remove_table_part(table))
        menu.addMenu(sub)
        self._table_menu = sub           # 시험이 쓴다

    def _remove_table_part(self, table, *, row: int | None = None,
                           col: int | None = None) -> None:
        """줄이나 칸 하나를 지운다. 마지막 하나를 지우거나 둘 다 없으면 표째 지운다."""
        if row is not None and table.rows() > 1:
            table.removeRows(row, 1)
            return
        if col is not None and table.columns() > 1:
            table.removeColumns(col, 1)
            return
        cursor = QTextCursor(self._text.document())
        cursor.beginEditBlock()
        cursor.setPosition(table.firstPosition() - 1)
        cursor.setPosition(table.lastPosition() + 1, QTextCursor.KeepAnchor)
        cursor.removeSelectedText()
        cursor.endEditBlock()

    def _insert_link(self) -> None:
        """파일·폴더·웹 링크를 넣는다. 커서가 링크 위에 있으면 그 링크를 고친다."""
        if not self._edit_mode:
            self._request_edit()
            return
        cur = self._text.textCursor()
        hit = self._text.link_at_cursor()
        if hit is not None:
            start, end, href = hit
            text = self._text.link_text(start, end)
        else:
            href = ""
            text = " ".join(cur.selectedText().split()) if cur.hasSelection() else ""
        # 창이 뜨면 메모가 비활성화된다. 그때 편집이 끝나 버리지 않게 막는다.
        self._opening_dialog = True
        try:
            dlg = LinkDialog(text=text, href=href, editing=hit is not None,
                             parent=self)
            accepted = dlg.exec() == QDialog.Accepted
        finally:
            QTimer.singleShot(0, self._end_opening_dialog)
        if not accepted or not self._edit_mode:
            return
        self._text.setFocus(Qt.OtherFocusReason)
        if hit is not None:
            start, end, _old = hit
            if dlg.remove_requested:
                self._text.remove_link(start, end)
            else:
                self._text.replace_link(start, end, dlg.text, dlg.href)
        elif not dlg.remove_requested:
            self._text.insert_link(dlg.text, dlg.href)

    def _link_tooltip(self, href: str) -> str:
        lines = [fl.describe_target(href), kind_label(fl.kind_of(href))]
        path = fl.href_to_path(href)
        if path and fl.is_local_drive(path):
            lines.append(i18n.t("link.tip_local_only"))
        lines.append(i18n.t("link.tip_ctrl_click") if self._edit_mode
                     else i18n.t("link.tip_click"))
        return "\n".join(lines)

    def _open_link(self, href: str) -> None:
        if self._edit_mode:
            # 다른 창이 뜨면 어차피 편집이 끝난다. 먼저 저장하고 연다.
            self._commit_and_release()
        elif self._pending_lock:
            self._abandon_grant = True
        open_link(self, href)

    def _show_link_menu(self, href: str, global_pos: QPoint) -> None:
        menu = edit_menu.new_menu(self)
        ink = "#3A3A3A"
        add_centered_menu_action(
            menu, link_icon(fl.kind_of(href)), i18n.t("link.menu_open"),
            lambda: self._open_link(href))
        if fl.is_file_href(href) and not fl.is_folder_href(href):
            add_centered_menu_action(
                menu, svg_icon("folder", color=ink), i18n.t("link.menu_reveal"),
                lambda: reveal_in_folder(self, href))
        add_centered_menu_action(
            menu, svg_icon("copy", color=ink), i18n.t("link.menu_copy"),
            lambda: QApplication.clipboard().setText(fl.describe_target(href)))
        if self._edit_mode:
            menu.addSeparator()
            add_centered_menu_action(
                menu, svg_icon("edit", color=ink), i18n.t("link.menu_edit"),
                self._insert_link)
        edit_menu.run_menu(menu, global_pos)
        menu.deleteLater()

    def _attach_image(self) -> None:
        if not self._edit_mode:
            self._request_edit()
            return
        self._opening_dialog = True
        try:
            path, _ = QFileDialog.getOpenFileName(
                self, i18n.t("sticky.img_dialog_title"), "",
                i18n.t("sticky.img_filter"))
            if path:
                self._text.setFocus(Qt.OtherFocusReason)
                # QImage 경로는 첫 프레임만 캡처하므로, 애니메이션 보존을
                # 위해 경로 기반 inserter 로 라우팅한다.
                self._text._insert_image_from_path(path)
        finally:
            # 네이티브 대화상자가 닫히며 늦게 전달되는 비활성화 이벤트까지
            # 가드가 살아 있도록 이벤트 루프를 한 번 돌린 뒤 해제한다.
            QTimer.singleShot(0, self._end_opening_dialog)

    def _end_opening_dialog(self) -> None:
        self._opening_dialog = False

    def eventFilter(self, obj, event) -> bool:
        if obj is self._grip and event.type() == QEvent.MouseButtonRelease:
            self._local_size_set = True
            self.sizeChanged.emit(self.note_id, self.width(), self.height())
        # 본문 이미지 더블 클릭은 보기/편집 상태와 무관하게 뷰어를 연다.
        # 첫 클릭이 편집 요청을 냈더라도 뷰어 창이 초점을 가져가면 changeEvent
        # 가 편집을 커밋하고 잠금을 풀어 메모는 곧 보기 상태로 돌아간다.
        if (obj is self._text.viewport()
                and event.type() == QEvent.MouseButtonDblClick
                and event.button() == Qt.LeftButton):
            hit = self._text.content_image_at(event.pos())
            if hit is not None:
                url, ordinal = hit
                self._open_image_viewer(url, ordinal)
                event.accept()
                return True
        if (obj is self._text.viewport() and event.type() == QEvent.Drop
                and not self._edit_mode):
            self._queue_drop(event)
            event.acceptProposedAction()
            return True
        if obj is self._text.viewport():
            handled = self._link_event(event)
            if handled:
                return True
            if event.type() == QEvent.ContextMenu:
                self._show_text_menu(event.pos(), event.globalPos())
                return True
        if obj is self._text.viewport() and event.type() == QEvent.MouseButtonPress:
            if not self._edit_mode:
                if event.button() == Qt.LeftButton:
                    # 할 일의 상자를 눌렀다. 편집으로 들어가지 않고 그 상자만 바꾼다
                    # (잠금을 받으면 enter_edit_mode 가 바꾸고 곧바로 돌려준다).
                    block = self._checkbox_block_at(event.position().toPoint())
                    self._pending_check = (block.blockNumber()
                                           if block is not None else None)
                self._request_edit()
                if not self._pending_lock:
                    self._pending_check = None
                return True
            if event.button() == Qt.LeftButton:
                if self._try_toggle_checkbox_at(event.position().toPoint()):
                    event.accept()
                    return True
        if obj is self._text and event.type() == QEvent.KeyPress:
            if self._edit_mode and self._mention_key(event):
                return True
            if event.key() == Qt.Key_Escape and self._edit_mode:
                self._commit_and_release()
                return True
            if self._edit_mode and self._handle_list_key(event):
                return True
            # Shift/Ctrl+Enter 는 자동 글머리 없이 soft break·명시적
            # 커밋을 할 수 있도록 기본 동작에 맡긴다.
            if (event.key() in (Qt.Key_Return, Qt.Key_Enter)
                    and self._edit_mode
                    and not (event.modifiers() &
                              (Qt.ShiftModifier | Qt.ControlModifier))):
                # 방금 친 주소는 줄을 바꾸기 전에 링크로 만든다.
                self._text.linkify_before_cursor()
                if self._handle_marker_enter():
                    return True
        return super().eventFilter(obj, event)

    def _link_event(self, event) -> bool:
        """본문 위의 마우스 이벤트 가운데 링크(와 날짜, 연락처)에 해당하는 것을 처리한다.

        보기 상태에서는 링크를 누르면 열고(편집으로 들어가지 않는다), 편집
        중에는 Ctrl 을 누른 채 눌러야 연다. 그냥 누르면 글자를 고친다."""
        et = event.type()
        viewport = self._text.viewport()
        if (et in (QEvent.MouseMove, QEvent.MouseButtonPress, QEvent.MouseButtonRelease)
                and not self._text.anchorAt(event.position().toPoint())
                and self._smart_event(event)):
            return True
        if et == QEvent.MouseMove:
            if event.buttons() == Qt.NoButton:
                href = self._text.anchorAt(event.position().toPoint())
                ctrl = bool(event.modifiers() & Qt.ControlModifier)
                if href and (not self._edit_mode or ctrl):
                    viewport.setCursor(Qt.PointingHandCursor)
                else:
                    viewport.setCursor(Qt.IBeamCursor)
            return False
        if et == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            href = self._text.anchorAt(event.position().toPoint())
            ctrl = bool(event.modifiers() & Qt.ControlModifier)
            if href and (not self._edit_mode or ctrl):
                self._link_press = href
                event.accept()
                return True
            self._link_press = None
            return False
        if et == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            pressed, self._link_press = self._link_press, None
            if pressed is None:
                return False
            if self._text.anchorAt(event.position().toPoint()) == pressed:
                self._open_link(pressed)
            event.accept()
            return True
        if et == QEvent.ToolTip:
            href = self._text.anchorAt(event.pos())
            if href:
                QToolTip.showText(event.globalPos(), self._link_tooltip(href),
                                  viewport)
            else:
                QToolTip.hideText()
            return bool(href)
        if et == QEvent.ContextMenu:
            href = self._text.anchorAt(event.pos())
            if href:
                if self._edit_mode:
                    # 우클릭한 링크에 커서를 두어 '링크 편집'이 그 링크를 고치게 한다.
                    self._text.setTextCursor(
                        self._text.cursorForPosition(event.pos()))
                self._show_link_menu(href, event.globalPos())
                return True
        return False

    def _open_image_viewer(self, url: str, ordinal: int) -> None:
        """설정에 따라 OS 기본 뷰어(임시 파일 경유) 또는 자체 뷰어로 연다.
        OS 뷰어를 열지 못하면 자체 뷰어로 물러선다."""
        # 뷰어(외부 앱 포함)가 초점을 가져가면 어차피 changeEvent 가 편집을
        # 커밋하지만, 활성화 순서에 기대지 않고 여기서 먼저 끝내 잠금을 오래
        # 붙들지 않는다. 변경이 없으면 서버에 아무것도 보내지 않는다.
        if self._edit_mode:
            self._commit_and_release()
        elif self._pending_lock:
            self._abandon_grant = True
        mode = (getattr(self._settings, "image_viewer", "")
                if self._settings is not None else "")
        if mode == IMAGE_VIEWER_SYSTEM and open_data_url_externally(url):
            return
        urls = self._text.content_image_urls()
        if not urls:
            urls = [url]
        if not (0 <= ordinal < len(urls)) or urls[ordinal] != url:
            ordinal = urls.index(url) if url in urls else 0
        viewer = self._image_viewer
        if viewer is None:
            viewer = ImageViewerWindow()
            viewer.destroyed.connect(self._on_image_viewer_destroyed)
            # 메모가 삭제·재구성되어 파괴되면 그 이미지를 보던 창도 닫는다.
            self.destroyed.connect(viewer.close)
            self._image_viewer = viewer
        viewer.set_images(urls, ordinal)
        viewer.present(self.screen())

    def _on_image_viewer_destroyed(self, *_args) -> None:
        self._image_viewer = None

    # 줄 맨 앞에서 치고 빈칸을 띄우면 목록으로 바뀌는 글자.
    _AUTO_LISTS = {"-": MARKER_SQUARE, "[]": MARKER_CHECK0, "[ ]": MARKER_CHECK0}

    def _handle_list_key(self, event) -> bool:
        """편집 중의 키 가운데 목록과 링크에 딸린 것을 처리한다. 처리했으면 True."""
        key = event.key()
        mods = event.modifiers() & (Qt.ShiftModifier | Qt.ControlModifier
                                    | Qt.AltModifier | Qt.MetaModifier)
        undo, self._auto_list_undo = self._auto_list_undo, None
        cursor = self._text.textCursor()
        if key == Qt.Key_Tab and not mods and self._text.accept_calc():
            return True
        if key in (Qt.Key_Tab, Qt.Key_Backtab) and cursor.currentTable() is not None:
            back = key == Qt.Key_Backtab or bool(mods & Qt.ShiftModifier)
            return self._move_table_cell(-1 if back else +1)
        if key == Qt.Key_Tab and not mods:
            return self._change_level(+1)
        if key == Qt.Key_Backtab and mods in (Qt.ShiftModifier, Qt.NoModifier):
            return self._change_level(-1)
        if key == Qt.Key_Backspace and not mods and not cursor.hasSelection():
            block = cursor.block()
            if cursor.position() != block.position():
                return False
            code = self._marker_code(block)
            if code not in MARKER_CODES:
                return False
            if (undo is not None and undo[0] == block.position()
                    and undo[2] == self._text.document().revision()):
                # 방금 저절로 바뀐 목록: 쳤던 글자로 되돌린다.
                cursor.beginEditBlock()
                try:
                    self._set_block_marker(block, MARKER_NONE)
                    cursor.insertText(undo[1])
                finally:
                    cursor.endEditBlock()
                self._text.setTextCursor(cursor)
            elif self._text.block_level(block) > 0:
                self._set_block_marker(block, code,
                                       self._text.block_level(block) - 1)
            else:
                self._set_block_marker(block, MARKER_NONE)
            self._refresh_format_buttons()
            return True
        if key == Qt.Key_Space and not mods and not cursor.hasSelection():
            if self._auto_list(cursor):
                return True
            # 방금 친 주소를 링크로 만들고, 빈칸은 평소대로 들어가게 둔다.
            self._text.linkify_before_cursor()
        return False

    def _auto_list(self, cursor: QTextCursor) -> bool:
        """글 줄의 맨 앞에서 '-', '[]', '1.' 을 치고 빈칸을 누르면 목록으로 바꾼다."""
        block = cursor.block()
        if self._marker_code(block) in MARKER_CODES:
            return False
        typed = block.text()[:cursor.position() - block.position()]
        code = self._AUTO_LISTS.get(typed)
        if code is None:
            found = re.fullmatch(r"(\d{1,3})\.", typed)
            # 이 줄이 받게 될 번호를 쳤을 때만 바꾼다('1.', 또는 앞 번호의 다음 수).
            if not found or int(found.group(1)) != self._text.next_number_for(block):
                return False
            code = MARKER_NUMBER
        cursor.beginEditBlock()
        try:
            cursor.setPosition(block.position())
            cursor.setPosition(block.position() + len(typed), QTextCursor.KeepAnchor)
            cursor.removeSelectedText()
            self._set_block_marker(cursor.block(), code, 0)
        finally:
            cursor.endEditBlock()
        self._text.setTextCursor(cursor)
        # 바로 Backspace 를 누르면 쳤던 글자(빈칸까지)로 돌아간다.
        self._auto_list_undo = (cursor.block().position(), typed + " ",
                                self._text.document().revision())
        self._refresh_format_buttons()
        return True

    def _handle_marker_enter(self) -> bool:
        """마커만 있는 빈 줄에서 Enter 면 마커를 없애 리스트를 끝낸다.
        체크박스는 다음 줄을 '미체크'로 잇는다. 글머리는 기본 Enter 가 새 블록
        에 마커 서식을 상속해 자연히 이어지므로 False 를 돌려준다."""
        cursor = self._text.textCursor()
        if cursor.hasSelection():
            return False
        block = cursor.block()
        code = self._marker_code(block)
        if code not in MARKER_CODES:
            return False
        if not block.text().strip():
            # 딸린 줄이면 한 단계 밖으로, 1단계 줄이면 목록을 끝낸다.
            level = self._text.block_level(block)
            if level > 0:
                self._set_block_marker(block, code, level - 1)
            else:
                self._set_block_marker(block, MARKER_NONE)
            self._refresh_format_buttons()
            return True
        if code in (MARKER_CHECK0, MARKER_CHECK1):
            cursor.beginEditBlock()
            try:
                cursor.insertBlock()
                self._set_block_marker(cursor.block(), MARKER_CHECK0)
            finally:
                cursor.endEditBlock()
            self._text.setTextCursor(cursor)
            self._refresh_format_buttons()
            return True
        return False

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._fold_busy():
            event.accept()
            return
        if self._folded:
            if event.button() == Qt.LeftButton:
                self._stop_geo_anim()
                self._slip_press = event.globalPosition().toPoint()
                self._slip_grab = (self._slip_press
                                   - self.frameGeometry().topLeft())
                self._slip_dragging = False
                if self._icon_ctl is not None and self.is_desktop_icon():
                    self._icon_ctl.pressed(self.icon_key(), event.modifiers())
            event.accept()
            return
        if (event.button() == Qt.LeftButton
                and self._on_handle_bar(event.position().toPoint())):
            self._drag_offset = (event.globalPosition().toPoint()
                                  - self.frameGeometry().topLeft())
            self._drag_from = self.pos()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._folded:
            if (self._slip_press is not None
                    and (event.buttons() & Qt.LeftButton)):
                gp = event.globalPosition().toPoint()
                if not self._slip_dragging:
                    moved = (gp - self._slip_press).manhattanLength()
                    if moved < QApplication.startDragDistance():
                        event.accept()
                        return
                    self._begin_slip_drag()
                self._drag_slip_to(gp)
            event.accept()
            return
        if self._drag_offset is not None and (event.buttons() & Qt.LeftButton):
            gp = event.globalPosition().toPoint()
            self.move(gp - self._drag_offset)
            self._preview_wall(gp)
            self._preview_window_edge(gp)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._folded:
            if event.button() == Qt.LeftButton and self._slip_press is not None:
                dragged = self._slip_dragging
                self._slip_press = None
                self._slip_dragging = False
                if dragged:
                    self._end_slip_drag()
                elif not self._icon_mode():
                    # 쪽지와 탭은 한 번 누르면 펴진다. 아이콘 모양은 바탕화면
                    # 아이콘처럼 한 번 누르면 골라지고 두 번 눌러야 펴진다.
                    self.unfold()
                else:
                    if self._icon_ctl is not None:
                        self._icon_ctl.clicked(self.icon_key())
                    plain = not (event.modifiers()
                                 & (Qt.ControlModifier | Qt.ShiftModifier))
                    if plain and desktop_icons.single_click_opens():
                        # Windows 를 '한 번 클릭해서 열기'로 쓰는 사용자다.
                        self.unfold()
            event.accept()
            return
        if self._drag_offset is not None:
            self._drag_offset = None
            side = self._drag_dock
            avail, cursor = QRect(self._drag_avail), QPoint(self._drag_cursor)
            target = self._attach_target
            leaving = self._detach_pending
            self._clear_wall_preview()
            self._clear_window_edge()
            if target is not None and not side:
                # 창의 끝면에 놓았다. 그 면에 딱 맞추고 그 창에 붙인다.
                from .. import window_attach as wa
                win, _face, (x, y) = target
                wa.move_window(int(self.winId()), x, y)
                self._open_pos = self.pos()
                self.positionChanged.emit(self.note_id, self.x(), self.y())
                if int(win.hwnd) != int(self.attached_hwnd() or 0):
                    self.attachDropped.emit(self.note_id, int(win.hwnd))
                event.accept()
                return
            if leaving and not side:
                # 붙은 창에서 멀리 떼어 놓았다. 떨어진다(자리는 놓은 그대로).
                self._open_pos = self.pos()
                self.positionChanged.emit(self.note_id, self.x(), self.y())
                self.detachRequested.emit(self.note_id)
                event.accept()
                return
            if side and not self._fold_busy():
                # 벽까지 끌어다 놓았다. 그 벽의 탭으로 접는다. 펼친 자리는 끌기
                # 전 그대로 둬서, 다시 펴면 원래 있던 곳으로 돌아온다.
                self._open_pos = QPoint(self._drag_from)
                self._dock_to(side, self._tab_along(side, cursor), avail)
                self._fold_now()
            else:
                self._open_pos = self.pos()
                self.positionChanged.emit(self.note_id, self.x(), self.y())
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if (self._folded and self._icon_mode() and not self._fold_busy()
                and event.button() == Qt.LeftButton):
            self._slip_press = None
            self.unfold()
            # 아이콘은 눌려도 앞으로 나오지 않는다. 펴는 메모는 앞으로 부른다.
            self.raise_()
            self.activateWindow()
            event.accept()
            return
        # 메모 위쪽 줄을 두 번 누르면 접는다(창 제목 줄을 두 번 누르는 것과 같은 자리).
        if (not self._folded and not self._fold_busy()
                and event.button() == Qt.LeftButton
                and self._on_handle_bar(event.position().toPoint())):
            self._drag_offset = None
            self._clear_wall_preview()
            self.fold()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def nativeEvent(self, event_type, message):  # noqa: N802
        # 바탕화면 아이콘으로 놓인 동안에는 눌려도 키보드를 가져가지 않는다. 진짜
        # 아이콘과 함께 고를 수 있게 키보드는 바탕화면이 쥐고 있어야 한다.
        if self._icon_ctl is not None and self.is_desktop_icon() and nf.is_mouse_activate(message):
            return True, nf.MA_NOACTIVATE
        return super().nativeEvent(event_type, message)

    def keyPressEvent(self, event) -> None:
        if (self._icon_ctl is not None and self.is_desktop_icon()
                and self._icon_ctl.key(self.icon_key(), event.key(),
                                       event.modifiers())):
            event.accept()
            return
        if (self._folded and not self._fold_busy()
                and event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space)):
            self.unfold()
            event.accept()
            return
        if not self._folded and not self._fold_busy():
            # 글쇠만으로도 편집에 들어가고 나올 수 있게 한다. 편집 중의 Enter 는
            # 본문이 먼저 받아 줄을 바꾸므로 여기까지 오지 않는다.
            if (not self._edit_mode
                    and event.key() in (Qt.Key_F2, Qt.Key_Return, Qt.Key_Enter)):
                self._request_edit()
                event.accept()
                return
            if self._edit_mode and event.key() == Qt.Key_Escape:
                self._commit_and_release()
                event.accept()
                return
        super().keyPressEvent(event)

    def enterEvent(self, event) -> None:
        if not self._folded:
            self._set_opacity_hover(True)
            if self._notice_owner:
                self._view_timer.start()
        if self._folded and not self._fold_busy():
            self._slip_hover = True
            if self._dock and not self._slip_dragging:
                self._slide_to(self._tab_rect(True), nf.TAB_HOVER_MS)
            self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        # 안쪽 위젯으로 옮겨 간 것은 나간 것이 아니다.
        if not self.rect().contains(self.mapFromGlobal(QCursor.pos())):
            self._set_opacity_hover(False)
            self._view_timer.stop()
        if self._slip_hover:
            self._slip_hover = False
            if (self._folded and self._dock and not self._slip_dragging
                    and self._slip_press is None and not self._fold_busy()):
                self._slide_to(self._tab_rect(False), nf.TAB_HOVER_MS)
            self.update()
        super().leaveEvent(event)

    def contextMenuEvent(self, event) -> None:
        if not self._folded or self._fold_busy():
            super().contextMenuEvent(event)
            return
        if self._icon_ctl is not None and self.is_desktop_icon():
            # 메뉴는 앞에 나온 창에서 띄워야 다른 곳을 눌렀을 때 닫힌다.
            self.activateWindow()
        if (self._icon_ctl is not None and self.is_desktop_icon()
                and self._icon_ctl.menu(self.icon_key(), event.globalPos())):
            # 여러 개를 골라 둔 상태다. 고른 것 전체에 쓰는 메뉴가 떴다.
            event.accept()
            return
        menu = edit_menu.new_menu(self)
        ink = "#3A3A3A"
        add_centered_menu_action(
            menu, svg_icon("unfold", color=ink), i18n.t("sticky.slip_unfold"),
            self.unfold)
        if not self._dock:
            # 접힌 모양 고르기: 쪽지 또는 바탕화면 아이콘. 벽에 붙인 메모는 늘 탭이다.
            if self._icon_mode():
                add_centered_menu_action(
                    menu, svg_icon("fold", color=ink),
                    i18n.t("sticky.slip_style_slip"),
                    lambda: self.set_fold_style(nf.FOLD_SLIP))
            else:
                add_centered_menu_action(
                    menu, svg_icon("note", color=ink),
                    i18n.t("sticky.slip_style_icon"),
                    lambda: self.set_fold_style(nf.FOLD_ICON))
        menu.addSeparator()
        add_centered_menu_action(
            menu, svg_icon("eye_off", color=ink), i18n.t("sticky.more_hide"),
            lambda: self.hideRequested.emit(self.note_id))
        add_centered_menu_action(
            menu, svg_icon("trash", color=ink), i18n.t("common.btn_delete"),
            lambda: self.deleteRequested.emit(self.note_id))
        edit_menu.run_menu(menu, event.globalPos())
        menu.deleteLater()
        event.accept()

    def _on_handle_bar(self, pos: QPoint) -> bool:
        return self._handle.geometry().contains(pos)

    # ── 접기 · 가장자리 붙이기 ──────────────────────────────────────────

    def is_folded(self) -> bool:
        return self._folded

    def dock_side(self) -> str:
        return self._dock

    def dock_span(self) -> tuple[int, int]:
        """붙어 있는 탭이 벽을 따라 차지하는 구간 ``(시작, 끝)``."""
        return (self._dock_y, self._dock_y + nf.tab_length(self._dock))

    def dock_screen_rect(self) -> QRect:
        return self._dock_avail()

    def set_dock_resolver(self, resolver) -> None:
        """``resolver(note_id, side, y, height, screen_rect) -> y`` 는 같은
        벽의 다른 탭과 겹치지 않는 자리를 돌려준다. ``y`` 와 ``height`` 는 벽을
        따라 잰 자리와 길이다."""
        self._dock_resolver = resolver

    def refresh_dock_layer(self) -> None:
        """'붙인 메모를 다른 창 위에' 설정이 바뀌었을 때 다시 맞춘다."""
        self._apply_z_order()

    @staticmethod
    def _reachable(rect: QRect) -> bool:
        """손잡이 줄이 어느 화면에든 잡을 수 있을 만큼 걸쳐 있는가."""
        grip = QRect(rect.x(), rect.y(), rect.width(),
                     max(1, min(rect.height(), config.HANDLE_BAR_HEIGHT)))
        for screen in QGuiApplication.screens():
            hit = screen.availableGeometry().intersected(grip)
            if hit.width() >= 48 and hit.height() >= 12:
                return True
        return False

    def _pull_on_screen(self, pos: QPoint, size: QSize) -> QPoint:
        """화면 밖에 놓인 자리를 주 화면 안으로 옮긴 자리를 돌려준다."""
        if self._reachable(QRect(pos, size)):
            return pos
        screen = QGuiApplication.primaryScreen()
        avail = (screen.availableGeometry() if screen is not None
                 else QRect(0, 0, 1280, 720))
        return QPoint(
            max(avail.left(), min(pos.x(), avail.right() - size.width() + 1)),
            max(avail.top(), min(pos.y(), avail.bottom() - size.height() + 1)))

    def reveal(self) -> None:
        """목록·트레이에서 고른 메모를 펼쳐 앞으로 가져온다."""
        if not self._folded and not self._fold_busy():
            # 그 사이 모니터 구성이 바뀌어 화면 밖에 있으면 데려온다.
            pos = self._pull_on_screen(self.pos(), self.size())
            if pos != self.pos():
                self.move(pos)
                self._open_pos = QPoint(pos)
                self.positionChanged.emit(self.note_id, pos.x(), pos.y())
        if self._folded and not self._fold_busy():
            self.unfold()
        self.raise_()
        self.activateWindow()

    # 꺼낸 메모 알리기: 두 번 깜빡이는 테두리(움직임을 끈 PC는 잠깐 켰다 끔).
    _FLASH_MS = 760
    _FLASH_STEPS = ((0.0, 0.0), (0.14, 1.0), (0.36, 0.25), (0.55, 1.0), (1.0, 0.0))

    def flash(self) -> None:
        """목록에서 꺼낸 메모임을 잠깐 테두리로 알린다. 펴는 중이면 다 편 뒤에."""
        if not self.isVisible():
            return
        if self._folded or self._fold_busy():
            tries = getattr(self, "_flash_wait", 0)
            if tries < 30:
                self._flash_wait = tries + 1
                QTimer.singleShot(60, self.flash)
            return
        self._flash_wait = 0
        ring = getattr(self, "_flash_ring", None)
        if ring is None:
            ring = self._flash_ring = _FlashRing(self)
        ring.setGeometry(self.rect())
        ring.show()
        ring.raise_()
        anim = getattr(self, "_flash_anim", None)
        if anim is not None:
            anim.stop()
        if not nf.system_animations_enabled():
            ring.level = 0.9
            ring.update()
            QTimer.singleShot(600, self._end_flash)
            return
        anim = QVariantAnimation(self)
        anim.setDuration(self._FLASH_MS)
        for at, level in self._FLASH_STEPS:
            anim.setKeyValueAt(at, float(level))

        def step(value) -> None:
            ring.level = float(value)
            ring.update()
        anim.valueChanged.connect(step)
        anim.finished.connect(self._end_flash)
        self._flash_anim = anim
        anim.start()

    def _end_flash(self) -> None:
        ring = getattr(self, "_flash_ring", None)
        if ring is not None:
            ring.level = 0.0
            ring.hide()

    def fold_busy(self) -> bool:
        """접거나 펴는 중인가."""
        return self._fold_busy()

    def in_view(self, share: float = 0.6) -> bool:
        """펼쳐진 채 눈앞에 보이는가: 다른 창에 가려지거나 화면 밖에 나간 부분이
        적은가. 메모 안의 고른 점들에서 맨 위에 있는 창이 이 메모인지 본다."""
        if (not self.isVisible() or self._folded or self._fold_busy()
                or self.windowOpacity() < 0.5):
            return False
        if platform.system() != "Windows" or QGuiApplication.platformName() != "windows":
            return True
        handle = self.windowHandle()
        hwnd = int(handle.winId()) if handle is not None else int(self.winId())
        try:
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            user32.WindowFromPoint.restype = wintypes.HWND
            user32.WindowFromPoint.argtypes = [wintypes.POINT]
            user32.GetAncestor.restype = wintypes.HWND
            user32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
            user32.MonitorFromPoint.restype = wintypes.HANDLE
            user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
            rect = wintypes.RECT()
            if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
                return True
            w, h = rect.right - rect.left, rect.bottom - rect.top
            seen = total = 0
            for i in range(5):
                for j in range(5):
                    pt = wintypes.POINT(rect.left + int(w * (0.1 + 0.2 * i)),
                                        rect.top + int(h * (0.1 + 0.2 * j)))
                    total += 1
                    if not user32.MonitorFromPoint(pt, 0):     # 화면 밖
                        continue
                    top = user32.WindowFromPoint(pt)
                    root = user32.GetAncestor(top, 2) if top else None   # GA_ROOT
                    if root and int(root) == hwnd:
                        seen += 1
            return seen >= share * total
        except Exception:
            return True

    def bring_to_front(self, *, start_editing: bool = False) -> None:
        """방금 만든 메모처럼 지금 바로 봐야 하는 메모를 다른 창들 위로 올린다.

        메모는 평소 바탕 화면 쪽(맨 아래)에 깔리므로 다른 프로그램을 쓰는 중에
        만들면 그 창들에 가려 보이지 않는다. '항상 위'로 올렸다가 곧바로 풀면
        다른 프로그램이 앞에 있어도 맨 위로 올라오고, 그 뒤로는 보통 창처럼
        다른 창을 누르면 뒤로 간다.

        ``start_editing`` 이면 앞에 서는 데 성공했을 때 바로 편집으로 들어간다."""
        if not self.isVisible():
            return
        if not self._topmost:
            hwnd = int(self.winId())
            _set_window_topmost(hwnd, True)
            _set_window_topmost(hwnd, False)
        self.raise_()
        self.activateWindow()
        if start_editing:
            # 활성화는 다음 이벤트 처리에서 끝난다. 잠깐 뒤에 확인한다.
            QTimer.singleShot(self._FRONT_EDIT_DELAY_MS, self._edit_if_active)

    def _edit_if_active(self) -> None:
        # Windows 가 앞에 세우기를 막아 뒤에 남은 메모는 편집으로 넣지 않는다.
        # 보이지 않는 창이 잠금을 쥐고 있으면 안 된다.
        if (self.isVisible() and self.isActiveWindow()
                and not self._folded and not self._fold_busy()):
            self._request_edit()

    def fold_style(self) -> str:
        """지금 접으면(또는 접혀 있는) 모양. 가장자리에 붙인 메모는 늘 탭이다."""
        return self._style_now()

    def set_icon_resolver(self, resolver) -> None:
        """``resolver(owner_key, pos, screen_rect) -> QPoint`` 는 다른 아이콘과
        겹치지 않는 격자 자리를 돌려준다."""
        self._icon_resolver = resolver

    def set_drop_probe(self, probe) -> None:
        """``probe(note_id, 커서 자리, 놓았는가) -> bool``. 아이콘으로 접힌 메모를
        끄는 동안 부른다. 놓았을 때 참을 돌려주면 받아 준 것이다(메모 폴더
        아이콘 위에 놓았다). 그러면 아이콘은 끌기 전의 자리로 돌아간다."""
        self._drop_probe = probe

    def _probe_drop(self, gp: QPoint, dropped: bool) -> bool:
        if self._drop_probe is None:
            return False
        try:
            return bool(self._drop_probe(self.note_id, QPoint(gp), dropped))
        except Exception:
            return False

    # ── 아이콘 격자 ──

    def _icon_grid(self, at: QPoint | None = None) -> nf.IconGrid:
        """이 메모의 아이콘이 놓이는(``at`` 을 주면 그 점이 있는) 화면의 격자."""
        if at is None:
            if self._folded and not self._dock:
                at = self.geometry().center()
            else:
                at = (self._fold_pos if self._fold_at
                      else self._open_pos) + QPoint(8, 8)
        return nf.icon_grid(self._screen_rect_at(at))

    # ── 바탕화면 아이콘처럼 고르기(ui/icon_select.py) ──

    def set_icon_controller(self, controller) -> None:
        self._icon_ctl = controller

    # ── 모니터 구성별 자리 ──

    def layout_spot(self) -> list[int] | None:
        """모니터 구성마다 기억할 자리 [x, y, 접힌 x, 접힌 y, 접힌 자리를 둠].
        벽에 붙인 탭과 바탕화면 아이콘 모양은 각자의 틀(벽, 아이콘 칸)을 따르므로 None."""
        if self._dock or self._fold_busy() or (self._folded and self._icon_mode()):
            return None
        open_pos = self._open_pos if self._folded else self.pos()
        fold = self.pos() if self._folded else self._fold_pos
        fold_at = self._folded or self._fold_at
        return [open_pos.x(), open_pos.y(), fold.x(), fold.y(), int(bool(fold_at))]

    def apply_layout_spot(self, spot) -> bool:
        """다른 때 이 모니터 구성에서 쓰던 자리로 옮긴다. 옮겼으면 True."""
        if (self._dock or self._fold_busy() or (self._folded and self._icon_mode())
                or not isinstance(spot, (list, tuple)) or len(spot) != 5):
            return False
        x, y, fx, fy, at = (int(v) for v in spot)
        self._open_pos = self._pull_on_screen(QPoint(x, y), self._current_open_size())
        if at:
            self._fold_at = True
            self._fold_pos = QPoint(fx, fy)
        if self._folded:
            self.setGeometry(self._folded_rect())
            self._emit_fold_state()
        else:
            self.move(self._open_pos)
        self.positionChanged.emit(self.note_id, self._open_pos.x(), self._open_pos.y())
        return True

    def icon_key(self) -> str:
        return "note:" + self.note_id

    def is_desktop_icon(self) -> bool:
        """지금 바탕화면 아이콘 모양으로 놓여 있는가."""
        return (self._folded and not self._dock and not self._fold_busy()
                and self._icon_mode())

    def set_icon_selected(self, on: bool) -> None:
        if bool(on) != self._icon_selected:
            self._icon_selected = bool(on)
            self.update()

    def set_marquee(self, rect: QRect | None) -> None:
        """바탕화면에서 끌어 고르는 사각형(화면 좌표). 끝나면 None."""
        tint = None
        if rect is not None and self.isVisible() and self.is_desktop_icon():
            hit = rect.intersected(self.geometry())
            if not hit.isEmpty():
                tint = QRectF(hit.translated(-self.pos()))
        if tint != self._marquee_tint:
            self._marquee_tint = tint
            self.update()

    def icon_hit(self, rect: QRect) -> bool:
        """그 사각형(화면 좌표)이 이 아이콘의 그림이나 이름에 닿는가."""
        if not self.isVisible() or not self.is_desktop_icon():
            return False
        local = QRectF(rect.translated(-self.pos()))
        return any(local.intersects(r) for r in nf.icon_hit_rects(
            QRectF(self.rect()), self._title(), self.font(),
            self._icon_grid().icon, self.devicePixelRatioF()))

    def place_icon(self, pos: QPoint) -> None:
        self.move_icon(QPoint(pos))

    def snap_icon(self) -> None:
        """지금 있는 곳에서 가장 가까운 빈 칸에 놓는다."""
        self.move_icon(self._resolve_icon_pos(self.pos()))

    def wants_icon_spot(self) -> bool:
        """아이콘 모양으로 접혀 있거나, 접으면 아이콘이 될 자리를 가진 메모인가."""
        return (not self._dock and self._style_pref() == nf.FOLD_ICON
                and (self._fold_at or self._folded))

    def icon_spot(self) -> QPoint:
        """아이콘이 놓인(놓일) 자리."""
        if self._folded and not self._dock and not self._fold_busy():
            if self._geo_anim is not None and self._fold_at:
                return QPoint(self._fold_pos)       # 그리로 미끄러져 가는 중이다
            return self.pos()
        return QPoint(self._fold_pos if self._fold_at else self._open_pos)

    def move_icon(self, pos: QPoint) -> None:
        """아이콘의 자리와 크기를 다시 맞춘다(바탕화면 아이콘의 크기가 바뀌었다,
        격자에 맞춰 다시 놓는다)."""
        self._fold_pos = QPoint(pos)
        self._fold_at = True
        if (self._folded and not self._dock and not self._fold_busy()
                and self._icon_mode()):
            self._stop_geo_anim()
            grid = nf.icon_grid(self._screen_rect_at(pos + QPoint(8, 8)))
            self.setGeometry(QRect(pos, grid.cell))
            self._refresh_face_tip()
            self.update()
        self._emit_fold_state()

    def set_fold_style(self, style: str) -> None:
        """접힌 모양을 쪽지나 아이콘으로 바꾼다. 접혀 있으면 그 자리에서 바뀐다."""
        if style not in nf.FOLD_STYLES or self._fold_busy():
            return
        self._fold_style = style
        if self._folded and not self._dock:
            self._settle_face()
        self._emit_fold_state()

    def _settle_face(self) -> None:
        """접힌 모양이 바뀐 뒤 창을 그 모양의 크기로 맞춘다. 아이콘이 됐으면
        바탕화면 아이콘의 칸에 맞춰 놓는다."""
        self._stop_geo_anim()
        pos = self.pos()
        if self._icon_mode():
            pos = self._resolve_icon_pos(pos)
            self._fold_pos = QPoint(pos)
            self._fold_at = True
        self.setGeometry(QRect(pos, self._face_size(pos + QPoint(8, 8))))
        self._refresh_face_tip()
        self.update()

    def refresh_fold_face(self) -> None:
        """설정의 접힌 모양이 바뀌었을 때 다시 맞춘다. 모양을 따로 고르지 않은
        접힌 메모만 따라간다(그리는 모양은 설정을 바로 따르는데 창 크기가 옛
        모양에 남으면 쪽지 크기의 창에 아이콘이 잘려 그려진다)."""
        if (not self._folded or self._dock or self._fold_busy()
                or self._fold_style in nf.FOLD_STYLES):
            return
        if self.size() != self._face_size():
            self._settle_face()
            self._emit_fold_state()
        self._refresh_face_tip()
        self.update()

    def fade_to(self, visible: bool) -> None:
        """폴더 아이콘을 열고 닫을 때 메모를 부드럽게 나타내거나 감춘다."""
        anim, self._fade_anim = self._fade_anim, None
        if anim is not None:
            anim.stop()
            anim.deleteLater()
        animate = self._fade_enabled()
        if visible:
            if self.isVisible() and self.windowOpacity() >= 0.99:
                return
            if not animate:
                self.setWindowOpacity(1.0)
                self.show()
                return
            start = self.windowOpacity() if self.isVisible() else 0.0
            self.setWindowOpacity(start)
            self.show()
            self._run_fade(start, 1.0, None)
        else:
            if not self.isVisible():
                return
            if not animate:
                self.hide()
                return
            self._run_fade(self.windowOpacity(), 0.0, self._fade_hidden)

    def _fade_enabled(self) -> bool:
        s = self._settings
        if s is not None and not getattr(s, "fold_animation", True):
            return False
        return nf.system_animations_enabled()

    def _run_fade(self, start: float, end: float, done) -> None:
        anim = QVariantAnimation(self)
        anim.setStartValue(float(start))
        anim.setEndValue(float(end))
        anim.setDuration(nf.anim_ms(nf.FADE_MS))
        anim.valueChanged.connect(lambda v: self.setWindowOpacity(float(v)))

        def finished() -> None:
            if self._fade_anim is anim:
                self._fade_anim = None
            anim.deleteLater()
            if done is not None:
                done()

        anim.finished.connect(finished)
        self._fade_anim = anim
        anim.start()

    def _fade_hidden(self) -> None:
        self.hide()
        self.setWindowOpacity(1.0)

    def apply_local_state(self, x: int, y: int, w: int = 0, h: int = 0, *,
                          folded: bool = False, dock: str = "",
                          dock_y: int = 0, fold_style: str = "",
                          fold_x: int = 0, fold_y: int = 0,
                          fold_at: bool = False) -> None:
        """이 PC 에 저장해 둔 위치·크기·접힘 상태로 창을 놓는다(처음 한 번,
        보이기 전에 부른다). ``x``·``y`` 는 펼친 자리, ``fold_x``·``fold_y`` 는
        접힌 메모를 둔 자리다(``fold_at`` 이 참일 때만 뜻이 있다)."""
        if w and h:
            self.resize(int(w), int(h))
            self._local_size_set = True
        # 모니터를 떼거나 해상도를 바꾸면 저장해 둔 자리가 화면 밖이 된다.
        pos = self._pull_on_screen(QPoint(int(x), int(y)), self.size())
        self.move(pos)
        self._open_pos = QPoint(pos)
        self._open_size = QSize(self.size())
        self._fold_style = fold_style if fold_style in nf.FOLD_STYLES else ""
        self._dock = dock if dock in nf.DOCK_SIDES else nf.DOCK_NONE
        self._fold_at = bool(fold_at)
        self._fold_pos = QPoint(int(fold_x), int(fold_y))
        if self._dock:
            if self._fold_at:
                tab = nf.tab_size(self._dock)
                avail = self._screen_rect_at(self._fold_pos + QPoint(
                    tab.width() // 2, tab.height() // 2))
                along = self._along(self._dock, self._fold_pos)
            else:
                # 접힌 자리를 따로 적지 않던 버전의 상태. 그때는 펼친 자리가
                # 붙인 벽 바로 옆이었으므로 그 자리로 화면을 가린다.
                avail = self._screen_rect_at(self._open_pos + QPoint(8, 8))
                along = int(dock_y)
            self._dock_y = self._resolve_slot(self._dock, along, avail)
            self._fold_pos = nf.tab_rect(self._dock, self._dock_y,
                                         avail).topLeft()
            self._fold_at = True
        elif self._fold_at:
            self._fold_pos = self._pull_on_screen(self._fold_pos,
                                                  self._face_size())
        if folded:
            self._folded = True
            self._set_body_visible(False)
            self.setMinimumSize(0, 0)
            self.setCursor(Qt.PointingHandCursor)
            self.setGeometry(self._folded_rect())
            self._refresh_face_tip()

    def fold(self, dock: str | None = None, style: str | None = None) -> None:
        """메모를 접는다. ``style`` 을 주면 이 메모의 접힌 모양(쪽지·아이콘)을
        그것으로 정하고, ``dock`` 을 주면 지금 화면의 그 벽에 탭으로 붙인다.

        접힌 메모를 둔 자리가 있으면 접힌 뒤 그리로 간다. 펼친 자리는 따로
        기억해 두었다가 다시 펼 때 쓴다."""
        if self._fold_busy():
            return
        if style in nf.FOLD_STYLES:
            self._fold_style = style
        if self._folded:
            if dock is not None:
                self.dock(dock)
            elif style in nf.FOLD_STYLES:
                self.set_fold_style(style)
            return
        self._open_pos = self.pos()
        if dock in nf.DOCK_SIDES:
            self._dock_to(dock, self._along(dock, self.pos()), self._avail())
        elif not self._dock and self._icon_mode():
            # 아이콘은 바탕화면 아이콘과 같은 칸에 놓인다. 둔 자리가 없으면 펼쳐
            # 있던 곳에서 가까운 빈 칸을 잡는다. 둔 자리가 있어도 펼쳐 둔 사이에
            # 다른 아이콘이 그 칸에 놓였으면 옆의 빈 칸으로 간다.
            self._fold_pos = self._resolve_icon_pos(
                self._fold_pos if self._fold_at else self._open_pos)
            self._fold_at = True
        self._fold_now()

    def _fold_now(self) -> None:
        """펼친 메모를 그 자리에서 접는다(펼친 자리는 이미 정해져 있다)."""
        if self._edit_mode:
            self._commit_and_release()
        self._drag_offset = None
        self._clear_wall_preview()
        self._open_size = QSize(self.size())
        self._fold_target = self._folded_rect()

        pix = self.grab()
        self._open_pix = pix
        self._set_body_visible(False)
        self._set_bare(True)
        # 벽에 붙인 메모는 접힌 모양 설정과 무관하게 탭으로 접힌다.
        self._fold_frames = nf.FoldFrames(
            pix, self._fold_target.size(),
            color=self.color, title=self._title(),
            anchor=self._fold_anchor(), dock=self._dock,
            private=bool(self.private_owner), font=self.font(),
            style=self._style_now(), thumb=self._fold_thumb(),
            icon=self._icon_grid(self._fold_target.center()).icon)
        self._fold_t = 0.0
        self.update()
        self._run_fold(0.0, 1.0, nf.FOLD_MS, self._finish_fold)

    def _fold_anchor(self) -> str:
        """접는 그림에서 쪽지가 남는 쪽. 오른쪽 벽의 탭은 오른쪽으로 접는다."""
        return nf.DOCK_RIGHT if self._dock == nf.DOCK_RIGHT else nf.DOCK_LEFT

    def _finish_fold(self) -> None:
        frames = self._fold_frames
        end = QRect(self._fold_target)
        if frames is not None:
            end = frames.slip_rect().toRect().translated(
                self.geometry().topLeft())
        self._fold_frames = None
        self._fold_t = 0.0
        self._folded = True
        self._slip_hover = False
        self.setMinimumSize(0, 0)
        self.setCursor(Qt.PointingHandCursor)
        self.setGeometry(end)
        self._refresh_face_tip()
        self._apply_z_order()
        self.update()
        self._slide_to(self._fold_target)
        self._emit_fold_state()

    def unfold(self) -> None:
        """쪽지(탭)를 다시 메모로 편다. 직전에 펼쳐 있던 자리에서 펴진다."""
        if not self._folded or self._fold_busy():
            return
        self._stop_geo_anim()
        self._slip_press = None
        self._slip_dragging = False
        open_rect = self._open_rect()
        self._open_pos = open_rect.topLeft()
        # 펴는 그림은 쪽지가 메모의 위쪽 모서리에 놓인 데서 시작한다. 접힌
        # 메모가 다른 곳에 있으면 먼저 그리로 옮긴다.
        face = self.size()
        if self._fold_anchor() == nf.DOCK_RIGHT:
            start = QRect(open_rect.right() - face.width() + 1, open_rect.top(),
                          face.width(), face.height())
        else:
            start = QRect(open_rect.topLeft(), face)
        if start != self.geometry() and self._animations_on():
            self._pre_unfold = True
            self._slip_hover = False
            self._slide_to(start, nf.SLIDE_MS, done=self._unfold_now)
            return
        self._unfold_now()

    def _unfold_now(self) -> None:
        self._pre_unfold = False
        cur = self.geometry()
        size = self._current_open_size()
        open_rect = QRect(self._open_pos, size)
        anchor = self._fold_anchor()
        face_dock = self._dock

        animate = self._animations_on()
        pix = self._open_pix if animate else None
        if pix is not None:
            dpr = pix.devicePixelRatio() or 1.0
            same = (abs(dpr - self.devicePixelRatioF()) < 0.01
                    and round(pix.width() / dpr) == size.width()
                    and round(pix.height() / dpr) == size.height())
            if not same:
                pix = None
        self._folded = False
        self._slip_hover = False
        self.unsetCursor()
        self.setToolTip("")
        self.setMinimumSize(config.MIN_NOTE_WIDTH, config.MIN_NOTE_HEIGHT)
        if not animate:
            self.setGeometry(open_rect)
            self._finish_unfold()
            return
        if pix is not None:
            # 접을 때 찍어 둔 모습이 그대로면, 본문을 잠깐 내보였다 감추지 않고
            # 바로 펴는 그림으로 들어간다.
            self._fold_frames = self._frames_for(pix, cur.size(), anchor,
                                                 face_dock)
            self._fold_t = 1.0
            self.setGeometry(open_rect)
        else:
            self.setGeometry(open_rect)
            self._set_body_visible(True)
            if self.layout() is not None:
                self.layout().activate()
            pix = self.grab()
            self._set_body_visible(False)
            self._fold_frames = self._frames_for(pix, cur.size(), anchor,
                                                 face_dock)
            self._fold_t = 1.0
        self.update()
        self._run_fold(1.0, 0.0, nf.UNFOLD_MS, self._finish_unfold)

    def _frames_for(self, pix: QPixmap, face: QSize, anchor: str,
                    face_dock: str) -> nf.FoldFrames:
        return nf.FoldFrames(
            pix, face, color=self.color, title=self._title(), anchor=anchor,
            dock=face_dock, private=bool(self.private_owner), font=self.font(),
            style=nf.FOLD_SLIP if face_dock else self._style_now(),
            thumb=self._fold_thumb())

    def _finish_unfold(self) -> None:
        self._fold_frames = None
        self._fold_t = 0.0
        self._open_pix = None
        self._set_body_visible(True)
        self._set_bare(False)
        self._refresh_lock_ui()
        self._apply_z_order()
        self.update()
        self._emit_fold_state()
        if self._notice_owner:
            self.viewed.emit(self.note_id)

    def _open_rect(self) -> QRect:
        """다시 펼 자리. 직전에 펼쳐 있던 자리 그대로다."""
        rect = QRect(self._open_pos, self._current_open_size())
        if self._fold_at and not self._dock and self._fold_pos == self._open_pos:
            # 접힌 자리를 따로 적지 않던 버전은 쪽지를 둔 자리를 펼친 자리로
            # 적었다. 화면 끝에 둔 쪽지를 그대로 펴면 메모가 화면 밖으로 나간다.
            return nf.keep_on_screen(rect, self._screen_rect_at(rect.topLeft()))
        return QRect(self._pull_on_screen(rect.topLeft(), rect.size()),
                     rect.size())

    def dock(self, side: str) -> None:
        """메모를 지금 화면의 ``side`` 벽에 탭으로 붙인다. 펼쳐 있으면 접어서
        붙인다. 사용자는 끌어다 붙이고, 이 함수는 그 밖의 경로가 쓴다."""
        if side not in nf.DOCK_SIDES or self._fold_busy():
            return
        if not self._folded:
            self.fold(dock=side)
            return
        self._dock_to(side, self._along(side, self.pos()), self._avail())
        self._refresh_face_tip()
        self._slide_to(self._tab_rect(self._slip_hover))
        self.update()
        self._emit_fold_state()

    def undock(self) -> None:
        """벽에서 뗀다. 접혀 있으면 벽 옆에 쪽지(아이콘)로 남는다."""
        if not self._dock or self._fold_busy():
            return
        side = self._dock
        avail = self._dock_avail()
        tab = self._tab_rect(False)
        self._dock = nf.DOCK_NONE
        face = self._face_size()
        if side == nf.DOCK_LEFT:
            pos = QPoint(avail.left() + 24, tab.top())
        elif side == nf.DOCK_RIGHT:
            pos = QPoint(avail.right() - face.width() - 23, tab.top())
        elif side == nf.DOCK_TOP:
            pos = QPoint(tab.left(), avail.top() + 24)
        else:
            pos = QPoint(tab.left(), avail.bottom() - face.height() - 23)
        rect = nf.keep_on_screen(QRect(pos, face), avail)
        if self._icon_mode():
            # 아이콘은 바탕화면 아이콘의 칸에 맞춰 놓는다.
            at = self._resolve_icon_pos(rect.topLeft())
            rect = QRect(at, self._face_size(at + QPoint(8, 8)))
        self._fold_pos = rect.topLeft()
        self._fold_at = True
        if self._folded:
            self._refresh_face_tip()
            self._slide_to(rect)
        self._apply_z_order()
        self.update()
        self._emit_fold_state()

    def _dock_to(self, side: str, along: int, avail: QRect) -> None:
        """``avail`` 화면의 ``side`` 벽, 벽을 따라 ``along`` 근처에 탭 자리를 잡는다."""
        self._dock = side
        self._dock_y = self._resolve_slot(side, int(along), avail)
        self._fold_pos = nf.tab_rect(side, self._dock_y, avail).topLeft()
        self._fold_at = True
        self._apply_z_order()

    @staticmethod
    def _along(side: str, point: QPoint) -> int:
        """벽을 따라 잰 자리. 좌우 벽은 위에서부터, 위아래 벽은 왼쪽에서부터다."""
        if side in (nf.DOCK_LEFT, nf.DOCK_RIGHT):
            return point.y()
        return point.x()

    def _tab_along(self, side: str, cursor: QPoint) -> int:
        """커서가 탭의 가운데에 오는 자리."""
        return self._along(side, cursor) - nf.tab_length(side) // 2

    def _preview_wall(self, gp: QPoint) -> None:
        """펼친 메모를 끄는 동안 커서가 화면 벽에 닿으면, 놓았을 때 생길 탭을
        그 자리에 미리 보인다."""
        screens = self._screens()
        side, index = nf.wall_for_drag(QRect(gp, QSize(1, 1)), gp, screens,
                                       nf.EDGE_PUSH)
        if not side:
            self._clear_wall_preview()
            return
        avail = QRect(screens[index][1])
        self._drag_dock = side
        self._drag_avail = avail
        self._drag_cursor = QPoint(gp)
        if not self.isVisible():
            return
        if self._ghost is None:
            self._ghost = _DockGhost(self)
        along = self._resolve_slot(side, self._tab_along(side, gp), avail)
        self._ghost.preview(nf.tab_rect(side, along, avail), self.color,
                            self._title(), side, bool(self.private_owner),
                            self.font())

    def _preview_window_edge(self, gp: QPoint) -> None:
        """창에 붙이기를 켜 두었으면 끄는 동안 메모의 가장자리가 다른 창의 끝면에 닿는지
        본다. 닿으면 그 창의 테두리를 파랗게, 붙은 메모를 그 창에서 멀리 떼면 회색 점선으로
        보인다. 화면 벽에 닿아 있으면(벽에 붙는다) 보지 않는다."""
        from .. import window_attach as wa
        if not self._can_attach or self._drag_dock:
            self._clear_window_edge()
            return
        note = wa.window_rect(int(self.winId()))
        if note is None:
            return
        if self._attach_windows is None:
            import os
            self._attach_windows = wa.list_windows(skip_pid=os.getpid())
        screen = QGuiApplication.screenAt(gp) or self.screen()
        ratio = screen.devicePixelRatio() if screen is not None else 1.0
        snap = wa.edge_snap(self._attach_windows, note, int(round(wa.EDGE_MARGIN * ratio)))
        own_hwnd = int(self.attached_hwnd() or 0) if self._attached else 0
        own = None
        if own_hwnd:
            own = next((w for w in self._attach_windows if int(w.hwnd) == own_hwnd), None) \
                or wa.window_info(own_hwnd)
        self._attach_target = snap
        self._detach_pending = False
        show, leaving = None, False
        if snap is not None:
            show = snap[0].rect
        elif own is not None:
            far = wa.rect_gap(note, own.rect) > int(round(wa.DETACH_GAP * ratio))
            self._detach_pending = far
            show, leaving = own.rect, far
        if show is None:
            if self._attach_ghost is not None:
                self._attach_ghost.hide()
            return
        if self._attach_ghost is None:
            self._attach_ghost = _AttachGhost(self)
        left, top, right, bottom = show
        self._attach_ghost.outline(QRect(int(left / ratio), int(top / ratio),
                                         int((right - left) / ratio),
                                         int((bottom - top) / ratio)), leaving=leaving)

    def _clear_window_edge(self) -> None:
        self._attach_target = None
        self._detach_pending = False
        self._attach_windows = None
        if self._attach_ghost is not None:
            self._attach_ghost.hide()

    def _clear_wall_preview(self) -> None:
        self._drag_dock = nf.DOCK_NONE
        if self._ghost is not None:
            self._ghost.hide()

    def _begin_slip_drag(self) -> None:
        self._slip_dragging = True
        self._drag_origin = None if self._dock else QPoint(self.pos())
        self._drag_at_wall = bool(self._dock)
        # 고른 아이콘 여럿 가운데 하나를 끌면 나머지도 함께 움직인다.
        self._group_drag = (self._icon_ctl is not None and not self._dock
                            and self._icon_mode())
        if self._group_drag:
            self._icon_ctl.drag_began(self.icon_key())
        cur = self.size()
        target = self._face_size()
        if cur != target:
            # 탭을 집어 들면 쪽지(아이콘) 크기로 돌아온다. 잡은 자리가 그 안에
            # 오게 한다.
            gx, gy = self._slip_grab.x(), self._slip_grab.y()
            if self._dock == nf.DOCK_RIGHT:
                gx = target.width() - (cur.width() - gx)
            if self._dock == nf.DOCK_BOTTOM:
                gy = target.height() - (cur.height() - gy)
            gx = max(12, min(target.width() - 12, gx))
            gy = max(8, min(target.height() - 8, gy))
            self._slip_grab = QPoint(gx, gy)

    def _drag_slip_to(self, gp: QPoint) -> None:
        screens = self._screens()
        rect = QRect(gp - self._slip_grab, self._face_size(gp))
        probe = rect
        if self._style_pref() == nf.FOLD_ICON and not self._drag_at_wall:
            # 아이콘은 벽에 닿아 있는 첫 칸에도 놓인다. 그림이 벽을 넘도록 밀어야
            # 탭으로 붙는다. 붙어 있던 탭은 전과 같이 벽에서 떼어 낼 때까지 탭이다.
            probe = nf.icon_wall_probe(rect, self._icon_grid(gp).icon)
        side, index = nf.wall_for_drag(probe, gp, screens)
        self._drag_at_wall = bool(side)
        self._drag_cursor = QPoint(gp)
        if self._style_pref() == nf.FOLD_ICON:
            # 메모 폴더 아이콘 위를 지나면 그 폴더가 받을 채비를 한다.
            self._probe_drop(gp, False)
        if side:
            # 화면 벽에 닿으면 접힌 모양과 무관하게 쪽지로 붙는다.
            avail = QRect(screens[index][1])
            self._drag_avail = avail
            slip = QSize(nf.SLIP_SIZE)
            grab = QPoint(min(self._slip_grab.x(), slip.width() - 12),
                          min(self._slip_grab.y(), slip.height() - 8))
            rect = nf.snap_to_wall(QRect(gp - grab, slip), side, avail)
        self._drag_dock = side
        self.setGeometry(rect)
        self.update()
        if self._group_drag and self._drag_origin is not None:
            self._icon_ctl.drag_moved(self.icon_key(),
                                      rect.topLeft() - self._drag_origin, gp)

    def _end_group_drag(self, moved: bool) -> None:
        if self._group_drag:
            self._group_drag = False
            delta = None
            if moved and self._drag_origin is not None and self._fold_at:
                delta = self._fold_pos - self._drag_origin
            self._icon_ctl.drag_ended(self.icon_key(), moved, delta)

    def _end_slip_drag(self) -> None:
        side = self._drag_dock
        self._drag_dock = nf.DOCK_NONE
        if side:
            if side in (nf.DOCK_LEFT, nf.DOCK_RIGHT):
                along = self.y()
            else:
                along = self._tab_along(side, self._drag_cursor)
            self._dock_to(side, along, QRect(self._drag_avail))
            self._slide_to(self._tab_rect(self._slip_hover))
            # 벽에 탭으로 붙었다. 함께 끌던 아이콘은 제자리로 돌려보낸다.
            self._end_group_drag(False)
        else:
            was_docked, self._dock = bool(self._dock), nf.DOCK_NONE
            pos = self.pos()
            if self._icon_mode():
                took = self._probe_drop(self._drag_cursor, True)
                if not took and self._group_drag:
                    # 진짜 바탕화면의 휴지통이나 폴더 아이콘 위에 놓았는가.
                    took = self._icon_ctl.dropped_on_real(
                        self.icon_key(), self._drag_cursor)
                if took:
                    # 메모 폴더 아이콘 위에 놓았다(그 폴더로 들어갔다). 아이콘은
                    # 끌기 전의 자리로 돌아간다. Windows 휴지통에 놓았을 때도
                    # 같다(메모는 곧 메모 휴지통으로 간다).
                    back = self._drag_origin
                    if back is None or was_docked:
                        back = self._resolve_icon_pos(pos)
                    self._fold_pos = QPoint(back)
                    self._fold_at = True
                    self._slide_to(QRect(back, self._face_size(back + QPoint(8, 8))))
                    self._apply_z_order()
                    self._refresh_face_tip()
                    self.update()
                    self._emit_fold_state()
                    self._end_group_drag(True)
                    return
                # 바탕화면 아이콘처럼 격자에 맞추고, 다른 아이콘과 겹치면 비켜 놓는다.
                pos = self._resolve_icon_pos(pos)
                self._slide_to(QRect(pos, self._face_size(pos + QPoint(8, 8))))
            self._fold_pos = QPoint(pos)
            self._fold_at = True
            self._apply_z_order()
        self._refresh_face_tip()
        self.update()
        self._emit_fold_state()
        self._end_group_drag(True)

    def _refresh_face_tip(self) -> None:
        """아이콘과 위아래 탭은 제목이 잘릴 수 있어 말풍선으로 전체를 보인다."""
        cut = self._folded and (self._icon_mode() or self._dock in (
            nf.DOCK_TOP, nf.DOCK_BOTTOM))
        self.setToolTip(self._title() if cut else "")

    def _set_bare(self, bare: bool) -> None:
        """접힌 메모는 창보다 작게 그려진다. Windows 가 창에 두르는 그림자가
        그 둘레에 판처럼 보이지 않게 끈다."""
        if self.isVisible():
            dwm.set_bare(int(self.winId()), bare)

    def _style_pref(self) -> str:
        """이 메모의 접힌 모양(붙였는지와 무관한 선호). 비어 있으면 설정을 따른다."""
        if self._fold_style in nf.FOLD_STYLES:
            return self._fold_style
        s = self._settings
        style = getattr(s, "fold_style", nf.FOLD_SLIP) if s is not None else ""
        return style if style in nf.FOLD_STYLES else nf.FOLD_SLIP

    def _style_now(self) -> str:
        return nf.FOLD_SLIP if self._dock else self._style_pref()

    def _icon_mode(self) -> bool:
        return self._style_now() == nf.FOLD_ICON

    def _face_size(self, at: QPoint | None = None) -> QSize:
        """떠 있는 접힌 메모의 크기(쪽지 또는 아이콘). 아이콘은 그 화면의
        바탕화면 아이콘 한 칸과 같다."""
        if self._style_pref() == nf.FOLD_ICON:
            return QSize(self._icon_grid(at).cell)
        return QSize(nf.SLIP_SIZE)

    def _resolve_icon_pos(self, pos: QPoint) -> QPoint:
        avail = self._screen_rect_at(pos + QPoint(38, 50))
        if self._icon_resolver is not None:
            try:
                return QPoint(self._icon_resolver(self.note_id, pos, avail))
            except Exception:
                pass
        return nf.snap_icon_pos(pos, avail, [])

    def _fold_busy(self) -> bool:
        return self._fold_frames is not None or self._pre_unfold

    def _animations_on(self) -> bool:
        s = self._settings
        if s is not None and not getattr(s, "fold_animation", True):
            return False
        return self.isVisible() and nf.system_animations_enabled()

    def _run_fold(self, start: float, end: float, ms: int, done) -> None:
        self._fold_done = done
        if not self._animations_on():
            self._fold_t = float(end)
            self._fold_done = None
            done()
            return
        anim = QVariantAnimation(self)
        anim.setStartValue(float(start))
        anim.setEndValue(float(end))
        anim.setDuration(nf.anim_ms(ms))
        anim.valueChanged.connect(self._on_fold_tick)
        anim.finished.connect(self._on_fold_anim_finished)
        self._fold_anim = anim
        anim.start()

    def _on_fold_tick(self, value) -> None:
        self._fold_t = float(value)
        self.update()

    def _on_fold_anim_finished(self) -> None:
        anim, self._fold_anim = self._fold_anim, None
        if anim is not None:
            anim.deleteLater()
        done, self._fold_done = self._fold_done, None
        if done is not None:
            done()

    def _finish_pending_fold(self) -> None:
        """접거나 펴는 도중에 창이 숨겨지면 끝 상태로 바로 넘긴다. 중간에서
        멈추면 저장된 접힘 상태와 창 모양이 어긋난다."""
        anim = self._fold_anim
        if anim is not None:
            anim.stop()
            self._on_fold_anim_finished()
        self._finish_geo_anim()
        if self._pre_unfold:
            self._unfold_now()

    def _slide_to(self, rect: QRect, ms: int = nf.SLIDE_MS, done=None) -> None:
        """창을 ``rect`` 로 부드럽게 옮긴다. ``done`` 은 다 옮긴 뒤에 부른다."""
        self._stop_geo_anim()
        rect = QRect(rect)
        if rect == self.geometry() or not self._animations_on():
            self.setGeometry(rect)
            if done is not None:
                done()
            return
        anim = QVariantAnimation(self)
        anim.setStartValue(self.geometry())
        anim.setEndValue(rect)
        anim.setDuration(nf.anim_ms(ms))
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.valueChanged.connect(self._on_geo_tick)
        anim.finished.connect(self._on_geo_finished)
        self._geo_anim = anim
        self._geo_done = done
        anim.start()

    def _on_geo_tick(self, value) -> None:
        if isinstance(value, QRect):
            self.setGeometry(value)

    def _on_geo_finished(self) -> None:
        anim, self._geo_anim = self._geo_anim, None
        if anim is not None:
            anim.deleteLater()
        done, self._geo_done = self._geo_done, None
        if done is not None:
            done()

    def _stop_geo_anim(self) -> None:
        anim, self._geo_anim = self._geo_anim, None
        self._geo_done = None
        if anim is not None:
            anim.stop()
            anim.deleteLater()

    def _finish_geo_anim(self) -> None:
        anim = self._geo_anim
        if anim is None:
            return
        end = anim.endValue()
        done = self._geo_done
        self._stop_geo_anim()
        if isinstance(end, QRect):
            self.setGeometry(end)
        if done is not None:
            done()

    def _set_body_visible(self, visible: bool) -> None:
        for wdg in (self._handle, self._text, self._grip):
            wdg.setVisible(visible)
        self._format_bar.setVisible(visible and self._edit_mode)
        if not visible:
            self._overlay.hide()

    def _title(self) -> str:
        if self._title_src is not self._content:
            self._title_src = self._content
            self._title_text = note_title_and_lines(self._content)[0]
        return self._title_text

    def _fold_thumb(self) -> QPixmap | None:
        """접힌 아이콘 안에 넣을 첫 이미지의 작은 그림. 이미지가 없으면 None."""
        if self._thumb_src is not self._content:
            self._thumb_src = self._content
            self._thumb_pix = nf.note_thumbnail(self._content or "")
        return self._thumb_pix

    def _current_open_size(self) -> QSize:
        if (self._folded or self._fold_busy()) and self._open_size.isValid() \
                and not self._open_size.isEmpty():
            return QSize(self._open_size)
        if self._folded:
            return QSize(config.DEFAULT_NOTE_WIDTH, config.DEFAULT_NOTE_HEIGHT)
        return QSize(self.size())

    @staticmethod
    def _screens() -> list[tuple[QRect, QRect]]:
        """연결된 화면마다 ``(전체 영역, 작업 표시줄을 뺀 영역)``."""
        return [(s.geometry(), s.availableGeometry())
                for s in QGuiApplication.screens()]

    def _screen_rect_at(self, point: QPoint) -> QRect:
        """``point`` 가 놓인(없으면 가장 가까운) 화면에서 작업 표시줄을 뺀 영역."""
        screens = self._screens()
        if not screens:
            return QRect(0, 0, 1920, 1080)
        return QRect(screens[nf.screen_index_at(point, screens)][1])

    def _avail(self) -> QRect:
        """지금 창이 놓인 화면에서 작업 표시줄을 뺀 영역."""
        return self._screen_rect_at(self.geometry().center())

    def _dock_avail(self) -> QRect:
        """탭이 붙어 있는 화면. 탭을 둔 자리로 가린다."""
        tab = nf.tab_size(self._dock)
        return self._screen_rect_at(self._fold_pos + QPoint(
            tab.width() // 2, tab.height() // 2))

    def _resolve_slot(self, side: str, y: int, avail: QRect) -> int:
        length = nf.tab_length(side)
        y = nf.clamp_dock_y(int(y), length, avail, side)
        if self._dock_resolver is not None:
            try:
                y = int(self._dock_resolver(self.note_id, side, y, length,
                                            avail))
            except Exception:
                pass
        return y

    def _tab_rect(self, expanded: bool) -> QRect:
        wide = 0
        if expanded and self._dock in (nf.DOCK_LEFT, nf.DOCK_RIGHT):
            wide = nf.tab_width_for(self._title(), self.font(),
                                    bool(self.private_owner))
        return nf.tab_rect(self._dock, self._dock_y, self._dock_avail(),
                           expanded=expanded, wide=wide)

    def _folded_rect(self) -> QRect:
        """접힌 메모가 놓일 자리. 둔 자리가 없으면 펼친 메모의 왼쪽 위다."""
        if self._dock:
            return self._tab_rect(False)
        pos = self._fold_pos if self._fold_at else self._open_pos
        # 접어 둔 자리의 모니터가 뽑혔으면 보이는 화면 안으로 들인다. 그러지
        # 않으면 접을 때마다 메모가 화면 밖으로 사라진다.
        size = self._face_size()
        return QRect(self._pull_on_screen(pos, size), size)

    def _dock_on_top(self) -> bool:
        s = self._settings
        return bool(getattr(s, "dock_on_top", True)) if s is not None else True

    def _wants_top(self) -> bool:
        """다른 창 위에 둘 것인가. 벽에 탭으로 접혀 있는 동안만이다.

        붙였던 메모도 펼치면 보통 창이다. 펼친 메모까지 위에 두면 쓰고 있는
        다른 창을 계속 가린다."""
        return self._folded and bool(self._dock) and self._dock_on_top()

    def _apply_z_order(self, initial: bool = False) -> None:
        if not self.isVisible():
            return
        hwnd = int(self.winId())
        want_top = self._wants_top()
        if want_top:
            _set_window_topmost(hwnd, True)
            self._topmost = True
            return
        if self._topmost:
            _set_window_topmost(hwnd, False)
            self._topmost = False
        if initial and not getattr(self, "_attached", False):
            # 프로그램 창에 붙은 메모는 그 창 바로 위에 선다(따라가는 쪽이 정한다).
            _pin_window_to_bottom(hwnd)

    def fold_state(self) -> dict[str, Any]:
        """이 PC 에 저장할 접힘 상태."""
        return {
            "folded": self._folded, "dock": self._dock,
            "dock_y": int(self._dock_y), "fold_style": self._fold_style,
            "fold_x": self._fold_pos.x(), "fold_y": self._fold_pos.y(),
            "fold_at": self._fold_at,
        }

    def _emit_fold_state(self) -> None:
        self.foldChanged.emit(self.note_id, self.fold_state())
        self.positionChanged.emit(self.note_id, self._open_pos.x(),
                                  self._open_pos.y())

    def changeEvent(self, event) -> None:
        if (event.type() == QEvent.ActivationChange
                and self._edit_mode and not self.isActiveWindow()
                and not self._opening_dialog):
            self._commit_and_release()
        super().changeEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._reposition_overlay()
        self._place_grip()
        self._fit_chips()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if getattr(self, "_smart_stale", False):
            self._smart_timer.start()
        hwnd = int(self.winId())
        if not self._first_show_done:
            self._first_show_done = True
            dwm.hide_border(hwnd)
        dwm.set_bare(hwnd, self._folded or self._fold_busy())
        if self._folded and self._dock:
            # 숨겨 둔 사이에 다른 탭이 이 자리를 차지했을 수 있다.
            avail = self._dock_avail()
            y = self._resolve_slot(self._dock, self._dock_y, avail)
            if y != self._dock_y:
                self._dock_y = y
                rect = nf.tab_rect(self._dock, y, avail)
                self._fold_pos = rect.topLeft()
                self.setGeometry(rect)
                self._emit_fold_state()
        elif (self._folded and self._fold_at and self._icon_mode()
              and not self._fold_busy() and self.pos() != self._fold_pos):
            # 감춰진 동안 옮긴 자리(폴더에 넣느라 끌려갔다가 돌아온 아이콘)는
            # 창에 닿지 않았을 수 있다. 다시 나타날 때 적어 둔 자리에 놓는다.
            self.move(self._fold_pos)
        # 가장자리에 붙은 탭은 다른 창 위에, 나머지는 바탕 화면 쪽(맨 아래)에 둔다.
        self._apply_z_order(initial=True)

    def hideEvent(self, event) -> None:
        self._finish_pending_fold()
        self._drag_offset = None
        self._clear_wall_preview()
        self._slip_press = None
        self._slip_dragging = False
        self._slip_hover = False
        super().hideEvent(event)

    def closeEvent(self, event) -> None:
        # 종료 중에는 hideRequested 를 emit 하지 않는다. emit 하면 로컬
        # 'hidden' 플래그가 켜져 다음 실행 시 메모가 숨겨진 채 뜬다.
        # closingDown() 은 aboutToQuit 의 teardown 보다 closeEvent 가
        # 먼저 오는 Windows 세션 종료 경로의 안전망이다.
        if self._teardown or QCoreApplication.closingDown():
            event.accept()
            return
        if self._edit_mode:
            self._commit_and_release()
        self.hideRequested.emit(self.note_id)
        event.ignore()
        self.hide()

    # 핸들 바를 메모 색의 옅은 톤으로 칠해 두 영역 경계에 선이 보이지 않게 한다.
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if self._fold_frames is not None:
            self._fold_frames.paint(painter, self._fold_t)
            painter.end()
            return
        if self._folded:
            dock = self._drag_dock if self._slip_dragging else self._dock
            if not dock and self._style_pref() == nf.FOLD_ICON:
                nf.paint_icon(painter, QRectF(self.rect()), self.color,
                              self._title(), private=bool(self.private_owner),
                              hover=self._slip_hover, font=self.font(),
                              thumb=self._fold_thumb(),
                              icon=self._icon_grid().icon,
                              selected=self._icon_selected,
                              tint=self._marquee_tint,
                              progress=self.todo_progress(),
                              bell=self.has_reminder())
            else:
                nf.paint_slip(painter, QRectF(self.rect()), self.color,
                              self._title(), dock=dock,
                              private=bool(self.private_owner),
                              hover=self._slip_hover, font=self.font(),
                              progress=self.todo_progress(),
                              bell=self.has_reminder())
            painter.end()
            return
        rect = self.rect()
        bar = QColor(self.color)
        h, s, v, a = bar.getHsv()
        v = min(255, v + 18)
        s = max(0, s - 20)
        bar.setHsv(h, s, v, a)
        hb = QRect(rect.x(), rect.y(), rect.width(), config.HANDLE_BAR_HEIGHT)
        alpha = self._paper_alpha()
        if alpha < 0.999:
            # 옅게: 머리 줄과 종이가 겹쳐 칠해지면 머리 줄만 진해지므로 나눠 칠한다.
            paper = QColor(self.color)
            paper.setAlphaF(alpha)
            bar.setAlphaF(alpha)
            whole = QPainterPath()
            whole.addRoundedRect(QRectF(rect), config.CORNER_RADIUS, config.CORNER_RADIUS)
            top = QPainterPath()
            top.addRect(QRectF(hb))
            painter.fillPath(whole.subtracted(top), paper)
            painter.fillPath(whole.intersected(top), bar)
            painter.end()
            return
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(self.color))
        painter.drawRoundedRect(rect, config.CORNER_RADIUS, config.CORNER_RADIUS)
        path = QPainterPath()
        path.addRoundedRect(hb, config.CORNER_RADIUS, config.CORNER_RADIUS)
        painter.fillPath(path, bar)
        painter.end()


# 표 한 개의 줄과 칸 한도(남이 보낸 본문도 이보다 크면 잘라 그리지 않는다. 고를 때의 한도).
TABLE_MAX_ROWS = 50
TABLE_MAX_COLS = 12
TABLE_BORDER = "#9A9A9A"


class TableSizePicker(QWidget):
    """표의 줄과 칸 수를 고르는 격자. 마우스를 올린 칸까지 칠하고 아래에 크기를 적는다.
    화살표로 옮기고 Enter 로 고를 수도 있다."""

    picked = Signal(int, int)
    ROWS, COLS, CELL, GAP = 8, 8, 18, 3

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName(i18n.t("sticky.menu_attach_table"))
        self._at = (1, 1)
        side = self.COLS * (self.CELL + self.GAP) - self.GAP
        self.setFixedSize(side + 16, self.ROWS * (self.CELL + self.GAP) - self.GAP + 40)

    def size_text(self) -> str:
        return i18n.t("sticky.table_size", rows=self._at[0], cols=self._at[1])

    def _cell_at(self, pos) -> tuple[int, int] | None:
        x, y = pos.x() - 8, pos.y() - 8
        if x < 0 or y < 0:
            return None
        col, row = x // (self.CELL + self.GAP) + 1, y // (self.CELL + self.GAP) + 1
        if row > self.ROWS or col > self.COLS:
            return None
        return int(row), int(col)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        hit = self._cell_at(event.position().toPoint())
        if hit is not None and hit != self._at:
            self._at = hit
            self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        hit = self._cell_at(event.position().toPoint())
        if hit is not None and event.button() == Qt.LeftButton:
            self._at = hit
            self.picked.emit(*hit)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        row, col = self._at
        moves = {Qt.Key_Left: (0, -1), Qt.Key_Right: (0, 1),
                 Qt.Key_Up: (-1, 0), Qt.Key_Down: (1, 0)}
        if event.key() in moves:
            dr, dc = moves[event.key()]
            self._at = (max(1, min(self.ROWS, row + dr)), max(1, min(self.COLS, col + dc)))
            self.update()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.picked.emit(row, col)
            return
        super().keyPressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        for r in range(self.ROWS):
            for c in range(self.COLS):
                on = r < self._at[0] and c < self._at[1]
                rect = QRectF(8 + c * (self.CELL + self.GAP), 8 + r * (self.CELL + self.GAP),
                              self.CELL, self.CELL)
                p.setPen(QPen(QColor("#1F2937") if on else QColor("#C9CDD3"), 1.0))
                p.setBrush(QColor("#E8EEF6") if on else QColor("#FFFFFF"))
                p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 3, 3)
        p.setPen(QColor("#374151"))
        p.drawText(QRectF(0, self.height() - 28, self.width(), 22), Qt.AlignCenter,
                   self.size_text())
        p.end()
