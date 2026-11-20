from __future__ import annotations

import ctypes
import platform
from typing import Any

from PySide6.QtCore import (
    QCoreApplication, QEasingCurve, QEvent, QMimeData, QObject, QPoint, QPointF,
    QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
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
    QTextImageFormat,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizeGrip,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from .. import config, html_guard, i18n
from .. import file_links as fl
from ..image_export import (
    open_data_url_externally, parse_data_url, sniff_extension,
)
from ..settings import IMAGE_VIEWER_SYSTEM
from . import dwm, edit_menu
from . import note_fold as nf
from .icons import color_swatch_icon, svg_icon
from .image_viewer import ImageViewerWindow
from .link_dialog import LinkDialog, kind_label
from .link_icons import link_icon
from .link_open import open_link, reveal_in_folder
from .menu_item import add_centered_menu_action
from .note_card import note_title_and_lines
from .rich_text import (
    BULLET_ARROW,
    BULLET_CIRCLE,
    BULLET_CODES,
    BULLET_INFO,
    BULLET_SQUARE,
    CHECKBOX_HEIGHT,
    CHECKBOX_WIDTH,
    LINK_COLOR_DARK,
    LINK_COLOR_LIGHT,
    MARKER_ARROW,
    MARKER_CHECK0,
    MARKER_CHECK1,
    MARKER_CIRCLE,
    MARKER_CODES,
    MARKER_INFO,
    MARKER_MARGIN,
    MARKER_NONE,
    MARKER_PROP,
    MARKER_SQUARE,
    RichTextEdit,
    bullet_qicon,
    checkbox_qicon,
    data_url_image,
    html_to_plain,
    marker_code_for_url,
    marker_url_for_code,
    strip_link_icons,
)
from .theme import RoundedScrollBar, color_name


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


HIGHLIGHT_COLORS = [
    "#FFF59D",
    "#C5E1A5",
    "#B3E5FC",
    "#F8BBD0",
    "#FFCC80",
    "#D1C4E9",
]
DEFAULT_HIGHLIGHT_COLOR = HIGHLIGHT_COLORS[0]

FONT_COLORS = [
    "#1F1F1F",
    "#D64545",
    "#E8830C",
    "#1F8A4C",
    "#1D6FD6",
    "#7A3FCB",
    "#C2185B",
]


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
        self._edit_baseline: tuple[str, str, int, int] | None = None
        self._my_nickname = my_nickname
        self._lock_holder: str | None = None
        self._edit_mode = False
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

        self._lock_label = QLabel("", self._handle)
        self._lock_label.setTextFormat(Qt.PlainText)   # 편집 중인 사람의 닉네임
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

        self._text = RichTextEdit(self)
        self._text.setReadOnly(True)
        self._text.setFrameStyle(QFrame.NoFrame)
        self._text.setAttribute(Qt.WA_TranslucentBackground)
        self._text.viewport().setAutoFillBackground(False)
        self._scrollbar = RoundedScrollBar()
        self._text.setVerticalScrollBar(self._scrollbar)
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
        self._font_color_btn.clicked.connect(self._show_font_color_menu)

        self._highlight_btn = _make_format_button(
            svg_icon("highlight"), i18n.t("sticky.tip_highlight"))
        self._highlight_btn.clicked.connect(self._show_highlight_menu)

        self._list_btn = _make_format_button(
            svg_icon("list"), i18n.t("sticky.tip_list"), True)
        self._list_menu = QMenu(self._list_btn)
        add_centered_menu_action(
            self._list_menu, bullet_qicon(BULLET_CIRCLE),
            i18n.t("sticky.menu_circle"),
            lambda: self._apply_bullet_marker(MARKER_CIRCLE))
        add_centered_menu_action(
            self._list_menu, bullet_qicon(BULLET_SQUARE),
            i18n.t("sticky.menu_square"),
            lambda: self._apply_bullet_marker(MARKER_SQUARE))
        add_centered_menu_action(
            self._list_menu, bullet_qicon(BULLET_ARROW),
            i18n.t("sticky.menu_arrow"),
            lambda: self._apply_bullet_marker(MARKER_ARROW))
        add_centered_menu_action(
            self._list_menu, bullet_qicon(BULLET_INFO),
            i18n.t("sticky.menu_info"),
            lambda: self._apply_bullet_marker(MARKER_INFO))
        self._list_menu.addSeparator()
        add_centered_menu_action(
            self._list_menu, checkbox_qicon(False),
            i18n.t("sticky.menu_checkbox"),
            self._apply_checkbox_marker)
        self._list_menu.addSeparator()
        add_centered_menu_action(
            self._list_menu, None, i18n.t("sticky.menu_list_off"),
            self._remove_list_markers)
        self._list_btn.setMenu(self._list_menu)
        self._list_btn.setPopupMode(QToolButton.InstantPopup)

        # 넣기: 이미지와 파일·폴더 링크. 버튼 줄이 좁아 한 버튼의 메뉴로 묶는다.
        self._image_btn = _make_format_button(
            svg_icon("attach"), i18n.t("sticky.tip_attach"))
        self._image_btn.setToolTip(i18n.t("sticky.tip_attach_full"))
        self._attach_menu = QMenu(self._image_btn)
        add_centered_menu_action(
            self._attach_menu, svg_icon("image"),
            i18n.t("sticky.menu_attach_image"), self._attach_image)
        add_centered_menu_action(
            self._attach_menu, svg_icon("link"),
            i18n.t("sticky.menu_attach_link"), self._insert_link)

        self._bold_btn.clicked.connect(self._toggle_bold)
        self._italic_btn.clicked.connect(self._toggle_italic)
        self._underline_btn.clicked.connect(self._toggle_underline)
        self._strike_btn.clicked.connect(self._toggle_strike)
        self._image_btn.clicked.connect(self._show_attach_menu)

        for b in (self._bold_btn, self._italic_btn, self._underline_btn,
                  self._strike_btn, self._font_color_btn, self._highlight_btn,
                  self._list_btn, self._image_btn):
            fb.addWidget(b)
        fb.addStretch(1)
        fb.addSpacing(_NoteGrip.SIZE)        # 손잡이가 올라앉는 자리
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
        # 접힌 아이콘에 넣는 첫 이미지의 작은 그림(내용이 바뀌면 다시 만든다).
        self._thumb_src: str | None = None
        self._thumb_pix: QPixmap | None = None

        self.resize(int(note.get("width", config.DEFAULT_NOTE_WIDTH)),
                    int(note.get("height", config.DEFAULT_NOTE_HEIGHT)))

        self._apply_theme_colors()

    def _apply_content_to_view(self, content: str) -> None:
        if not content:
            self._text.clear()
        elif "<" in content and ">" in content:
            # 본문이 가리키는 바깥 자원(네트워크 경로의 그림 등)은 걷어 내고 그린다.
            self._text.setHtml(html_guard.clean(content))
        else:
            self._text.setPlainText(content)
        self._convert_loaded_markers()
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

    def _convert_loaded_markers(self) -> None:
        """저장본의 인라인 마커 이미지(+뒤 공백)를 블록 마커 속성으로 바꾼다.
        구버전 인라인 이미지 저장본도 이렇게 현재 모델로 흡수한다."""
        doc = self._text.document()
        positions = []
        block = doc.begin()
        while block.isValid():
            positions.append(block.position())
            block = block.next()
        for pos in reversed(positions):
            code, length = self._leading_marker(doc.findBlock(pos))
            if code == MARKER_NONE:
                continue
            bc = QTextCursor(doc)
            bc.setPosition(pos)
            bc.setPosition(pos + length, QTextCursor.KeepAnchor)
            bc.removeSelectedText()
            self._set_block_marker(doc.findBlock(pos), code)

    def _marker_save_html(self) -> str:
        """블록 마커를 인라인 이미지로 되살린 HTML 을 만든다. 저장 형식은
        예전과 같아(인라인 이미지 + 공백) 호환되고, 다시 불러올 때 블록
        마커로 변환된다. 라이브 문서는 그대로 두고 복제본에서 작업한다."""
        clone = self._text.document().clone()
        # 링크 아이콘 자리는 화면용이라 저장하지 않는다(위치 계산 전에 뺀다).
        strip_link_icons(clone)
        positions = []
        block = clone.begin()
        while block.isValid():
            positions.append(
                (block.position(),
                 block.blockFormat().intProperty(MARKER_PROP)))
            block = block.next()
        for pos, code in reversed(positions):
            cur = QTextCursor(clone)
            cur.setPosition(pos)
            # 저장 형식엔 여백 대신 인라인 이미지를 둔다.
            no_margin = QTextBlockFormat()
            no_margin.setLeftMargin(0.0)
            cur.mergeBlockFormat(no_margin)
            if code in MARKER_CODES:
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
        has_rich = False
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
                            or (size > 0
                                and abs(size - default_size) > 0.1)
                            or fmt.hasProperty(QTextFormat.ForegroundBrush)
                            or fmt.hasProperty(QTextFormat.BackgroundBrush)):
                        has_rich = True
                        break
                it += 1
            block = block.next()
        if has_rich:
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
        if own_echo:
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
        if self._abandon_grant or self._folded or self._fold_busy():
            # 이미지 뷰어를 열거나 메모를 접는 사이에 허용이 도착했다. 변경 없이
            # 잠금만 돌려주므로 서버는 이력 행을 남기지 않는다(변경 없는 편집
            # 종료와 같은 경로). 접힌 메모가 보이지 않는 채로 잠금을 쥐면 안 된다.
            self._abandon_grant = False
            self._pending_drop = None
            self._pending_links = []
            self.releaseLock.emit(self.note_id)
            return
        self._edit_mode = True
        self._text.setReadOnly(False)
        self._text.setFocus(Qt.OtherFocusReason)
        self._overlay.hide()
        self._lock_label.setText(i18n.t("sticky.lock_editing_self"))
        self._set_format_bar_enabled(True)
        self._refresh_format_buttons()
        self._autosave_timer.start()
        # 변화 없는 focus→blur 가 updated_at 을 건드리거나 no-op 이력
        # 행을 남기지 않도록 비교 기준 스냅샷을 떠 둔다.
        self._edit_baseline = self._edit_snapshot()
        self._apply_pending_drop()
        self._apply_pending_links()
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
            # 끊겨 있어 편집을 시작하지 못했다(안내는 _request_edit 이 띄웠다).
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
            # 끊겨 있어 편집을 시작하지 못했다(안내는 _request_edit 이 띄웠다).
            self._pending_links = []

    def _apply_pending_links(self) -> None:
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
        self._pending_check = None
        self._pending_drop = None
        self._pending_links = []
        self._abandon_grant = False
        self._text.setReadOnly(True)
        self._lock_label.setText(i18n.t("sticky.lock_editing_by", holder=holder))
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
        self._format_bar.setVisible(
            enabled and not getattr(self, "_folded", False))
        self._place_grip()

    def _place_grip(self) -> None:
        g = self._grip
        g.move(self.width() - g.width() - 1, self.height() - g.height() - 1)
        g.raise_()

    def _refresh_lock_ui(self) -> None:
        if self._edit_mode:
            return
        if self._lock_holder is None:
            self._lock_label.setText(
                i18n.t("sticky.offline") if self._offline else "")
            self._overlay.hide()
            self._text.setReadOnly(True)
            self._set_format_bar_enabled(False)
        elif self._lock_holder == self._my_nickname:
            self._lock_label.setText(i18n.t("sticky.lock_editing_self"))
        else:
            # 누가 편집 중인지는 손잡이 줄에 적는다. 본문은 가리지 않는다.
            self._lock_label.setText(
                i18n.t("sticky.lock_editing_by", holder=self._lock_holder))
            self._set_format_bar_enabled(False)

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
        """호스트와 끊겼는지 알린다.

        끊긴 동안에는 새로 편집을 시작하지 못한다(잠금을 받을 길이 없다).
        이미 편집하던 글은 그대로 이어 쓸 수 있고, 다시 붙으면 메인 창이
        unsent_edit() 로 가져가 보낸다."""
        offline = bool(offline)
        if offline == self._offline:
            return
        self._offline = offline
        self._pending_lock = False
        self._refresh_lock_ui()

    def unsent_edit(self) -> str | None:
        """호스트에 닿았는지 확인되지 않은 편집 내용. 없으면 None."""
        if self._edit_mode:
            if (self._pending_commit is not None
                    or self._edit_snapshot() != self._edit_baseline):
                return self._current_content()
            return None
        return self._pending_commit

    def _request_edit(self) -> None:
        if self._edit_mode or self._pending_lock:
            # 뷰어를 연 뒤 응답이 오기 전에 다시 눌렀다면 편집하려는 뜻이다.
            self._abandon_grant = False
            return
        if self._offline:
            # 요청을 보내도 닿지 않는다. 아무 반응이 없는 것처럼 두지 않는다.
            self._show_overlay(i18n.t("sticky.offline_notice"))
            return
        if self._lock_holder and self._lock_holder != self._my_nickname:
            return
        self._pending_lock = True
        self.requestLock.emit(self.note_id)

    def _commit_and_release(self) -> None:
        if not self._edit_mode:
            return
        self._autosave_timer.stop()
        self._idle_save_timer.stop()
        self._edit_mode = False
        self._text.setReadOnly(True)
        snapshot = self._edit_snapshot()
        # baseline 과 같으면 updated_at·이력 행을 건드리지 않도록 전송을 건너뛴다.
        if snapshot != self._edit_baseline:
            new_content = snapshot[0]
            self._pending_commit = new_content
            self._content = new_content
            self.commitEdit.emit(
                self.note_id, new_content, self.color,
                self.width(), self.height(), self.version,
            )
        self._edit_baseline = None
        self.releaseLock.emit(self.note_id)
        self._lock_label.setText("")
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
        self._pending_commit = new_content
        self._content = new_content
        self.commitEdit.emit(
            self.note_id, new_content, self.color,
            self.width(), self.height(), self.version,
        )
        # baseline 을 굴려 이후 idle 자동 저장 tick 도 no-op 이 되게 한다.
        self._edit_baseline = snapshot

    def flush_pending_edit(self) -> None:
        """앱 종료 전 메인 창이 호출해 저장되지 않은 편집을 잃지 않게 한다."""
        if self._edit_mode:
            self._commit_and_release()

    def _show_color_menu(self) -> None:
        if not self._edit_mode:
            self._request_edit()
            return
        menu = QMenu(self)
        container = QWidget(menu)
        grid = QGridLayout(container)
        grid.setContentsMargins(6, 6, 6, 6)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)
        for index, hex_color in enumerate(config.COLORS):
            r, c = divmod(index, 4)
            btn = QToolButton(container)
            btn.setFixedSize(28, 28)
            btn.setIcon(color_swatch_icon(hex_color, 22))
            btn.setIconSize(QSize(22, 22))
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setToolTip(color_name(hex_color))
            btn.setStyleSheet(
                "QToolButton { border: none; padding: 2px; border-radius: 6px; background: transparent; }"
                "QToolButton:hover { background: rgba(0,0,0,0.08); }"
            )
            btn.clicked.connect(
                lambda _checked=False, c=hex_color, m=menu: (self._set_color(c), m.close()))
            grid.addWidget(btn, r, c)
        action = QWidgetAction(menu)
        action.setDefaultWidget(container)
        menu.addAction(action)
        _wire_swatch_keys(menu, container, columns=4)
        menu.exec(self._color_btn.mapToGlobal(QPoint(0, self._color_btn.height())))

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

        add("eye_off", i18n.t("sticky.more_hide"),
            lambda: self.hideRequested.emit(self.note_id))
        add("clock", i18n.t("sticky.tip_history"),
            lambda: self.historyRequested.emit(self.note_id))
        menu.addSeparator()
        if self._dock:
            # 벽에 붙여 둔 메모는 접으면 늘 그 자리의 탭으로 돌아간다.
            add("fold", i18n.t("sticky.tip_fold"), self.fold)
        else:
            add("fold", i18n.t("sticky.more_fold"),
                lambda: self.fold(style=nf.FOLD_SLIP))
            add("note", i18n.t("sticky.more_fold_icon"),
                lambda: self.fold(style=nf.FOLD_ICON))
        menu.addSeparator()
        add("lock", i18n.t("sticky.more_private_off") if is_private
            else i18n.t("sticky.more_private_on"),
            lambda: self.privateToggleRequested.emit(self.note_id, not is_private))
        menu.addSeparator()
        add("trash", i18n.t("sticky.more_delete"),
            lambda: self.deleteRequested.emit(self.note_id))
        edit_menu.run_menu(menu, self._more_btn.mapToGlobal(
            QPoint(0, self._more_btn.height())))

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
        else:
            can_edit = not self._offline and not self.is_locked_by_other()
            add_centered_menu_action(
                menu, svg_icon("edit", color=ink if can_edit else "#B0B0B0"),
                i18n.t("sticky.menu_edit"), self._request_edit, enabled=can_edit)
            plain = html_to_plain(self._content or "").strip()
            add_centered_menu_action(
                menu, svg_icon("copy", color=ink if plain else "#B0B0B0"),
                i18n.t("sticky.menu_copy_all"),
                lambda: QApplication.clipboard().setText(plain),
                enabled=bool(plain))
        edit_menu.run_menu(menu, global_pos)
        menu.deleteLater()

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
        else:
            self._scrollbar.set_colors(QColor(0, 0, 0, 60),
                                       QColor(0, 0, 0, 105))
        # 글머리·체크박스와 형광펜 글자도 바탕에 맞춘다.
        self._text.set_marker_tone(fg == "#FFFFFF")
        self._keep_text_readable()
        self._grip.set_tone(QColor(255, 255, 255, 170) if fg == "#FFFFFF"
                            else QColor(0, 0, 0, 125))
        lock_color = "#E5E5E5" if fg == "#FFFFFF" else "#3A3A3A"
        self._lock_label.setStyleSheet(
            f"color: {lock_color}; font-size: 11px; background: transparent;")
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
        self._font_color_btn.setIcon(svg_icon("text_color", color=fg))
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

    def _show_font_color_menu(self) -> None:
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
        for index, hex_color in enumerate(FONT_COLORS):
            r, c = divmod(index, 4)
            btn = QToolButton(container)
            btn.setFixedSize(28, 28)
            btn.setIcon(color_swatch_icon(hex_color, 22))
            btn.setIconSize(QSize(22, 22))
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setStyleSheet(
                "QToolButton { border: none; padding: 2px; "
                "border-radius: 6px; background: transparent; }"
                "QToolButton:hover { background: rgba(0,0,0,0.08); }")
            btn.clicked.connect(
                lambda _checked=False, c=hex_color, m=menu:
                    (self._apply_font_color(c), m.close()))
            grid.addWidget(btn, r, c)
        outer.addLayout(grid)

        divider = QFrame(container)
        divider.setFrameShape(QFrame.HLine)
        divider.setStyleSheet("color: rgba(0,0,0,0.08);")
        outer.addWidget(divider)

        default_btn = QToolButton(container)
        default_btn.setText(i18n.t("sticky.font_color_default"))
        default_btn.setIcon(color_swatch_icon(_text_color_for_bg(self.color), 18))
        default_btn.setIconSize(QSize(18, 18))
        default_btn.setCursor(Qt.PointingHandCursor)
        default_btn.setFocusPolicy(Qt.NoFocus)
        default_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        default_btn.setFixedHeight(26)
        default_btn.setStyleSheet(
            "QToolButton { border: none; padding: 2px 6px; "
            "border-radius: 6px; background: transparent; "
            "color: #1F1F1F; font-size: 12px; text-align: left; }"
            "QToolButton:hover { background: rgba(0,0,0,0.08); }")
        default_btn.clicked.connect(
            lambda _checked=False, m=menu:
                (self._apply_font_color(None), m.close()))
        outer.addWidget(default_btn)

        action = QWidgetAction(menu)
        action.setDefaultWidget(container)
        menu.addAction(action)
        _wire_swatch_keys(menu, container, columns=4)
        menu.exec(self._font_color_btn.mapToGlobal(
            QPoint(0, self._font_color_btn.height())))

    def _apply_font_color(self, color: str | None) -> None:
        if not self._edit_mode:
            return
        fmt = QTextCharFormat()
        if color is None:
            fmt.setForeground(QBrush(QColor(_text_color_for_bg(self.color))))
        else:
            fmt.setForeground(QBrush(QColor(color)))
        self._merge_char_format(fmt)
        self._refresh_format_buttons()

    def _toggle_list(self) -> None:
        if not self._edit_mode:
            return
        self._apply_bullet_marker(MARKER_CIRCLE)

    def _marker_code(self, block) -> int:
        return block.blockFormat().intProperty(MARKER_PROP)

    def _set_block_marker(self, block, code: int) -> None:
        """블록 마커를 설정/해제한다. 마커가 있으면 본문을 마커 폭만큼 들여
        paintEvent 가 여백에 글리프를 그릴 자리를 비운다. 블록 포맷에 두므로
        undo 로 되돌릴 수 있고, 줄 분할 시 여백과 함께 상속돼 리스트가
        자연히 이어진다."""
        if not block.isValid():
            return
        new_bf = QTextBlockFormat()
        if code in MARKER_CODES:
            new_bf.setProperty(MARKER_PROP, code)
            new_bf.setLeftMargin(MARKER_MARGIN)
        else:
            new_bf.setProperty(MARKER_PROP, MARKER_NONE)
            new_bf.setLeftMargin(0.0)
        QTextCursor(block).mergeBlockFormat(new_bf)
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
        self._set_block_marker(end.block(), code)
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

    def _show_attach_menu(self) -> None:
        if not self._edit_mode:
            self._request_edit()
            return
        self._attach_menu.exec(self._image_btn.mapToGlobal(
            QPoint(0, self._image_btn.height())))

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
            if event.key() == Qt.Key_Escape and self._edit_mode:
                self._commit_and_release()
                return True
            # Shift/Ctrl+Enter 는 자동 글머리 없이 soft break·명시적
            # 커밋을 할 수 있도록 기본 동작에 맡긴다.
            if (event.key() in (Qt.Key_Return, Qt.Key_Enter)
                    and self._edit_mode
                    and not (event.modifiers() &
                              (Qt.ShiftModifier | Qt.ControlModifier))):
                if self._handle_marker_enter():
                    return True
        return super().eventFilter(obj, event)

    def _link_event(self, event) -> bool:
        """본문 위의 마우스 이벤트 가운데 링크에 해당하는 것을 처리한다.

        보기 상태에서는 링크를 누르면 열고(편집으로 들어가지 않는다), 편집
        중에는 Ctrl 을 누른 채 눌러야 연다. 그냥 누르면 글자를 고친다."""
        et = event.type()
        viewport = self._text.viewport()
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
                    # 아이콘처럼 두 번 눌러야 펴진다.
                    self.unfold()
            event.accept()
            return
        if self._drag_offset is not None:
            self._drag_offset = None
            side = self._drag_dock
            avail, cursor = QRect(self._drag_avail), QPoint(self._drag_cursor)
            self._clear_wall_preview()
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

    def keyPressEvent(self, event) -> None:
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
        if self._folded and not self._fold_busy():
            self._slip_hover = True
            if self._dock and not self._slip_dragging:
                self._slide_to(self._tab_rect(True), nf.TAB_HOVER_MS)
            self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
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

    def set_fold_style(self, style: str) -> None:
        """접힌 모양을 쪽지나 아이콘으로 바꾼다. 접혀 있으면 그 자리에서 바뀐다."""
        if style not in nf.FOLD_STYLES or self._fold_busy():
            return
        self._fold_style = style
        if self._folded and not self._dock:
            self._stop_geo_anim()
            self.setGeometry(QRect(self.pos(), self._face_size()))
            self._refresh_face_tip()
            self.update()
        self._emit_fold_state()

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
        anim.setDuration(170)
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
            style=self._style_now(), thumb=self._fold_thumb())
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

    def _clear_wall_preview(self) -> None:
        self._drag_dock = nf.DOCK_NONE
        if self._ghost is not None:
            self._ghost.hide()

    def _begin_slip_drag(self) -> None:
        self._slip_dragging = True
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
        rect = QRect(gp - self._slip_grab, self._face_size())
        side, index = nf.wall_for_drag(rect, gp, screens)
        self._drag_cursor = QPoint(gp)
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
        else:
            self._dock = nf.DOCK_NONE
            pos = self.pos()
            if self._icon_mode():
                # 바탕화면 아이콘처럼 격자에 맞추고, 다른 아이콘과 겹치면 비켜 놓는다.
                pos = self._resolve_icon_pos(pos)
                self._slide_to(QRect(pos, nf.ICON_SIZE))
            self._fold_pos = QPoint(pos)
            self._fold_at = True
            self._apply_z_order()
        self._refresh_face_tip()
        self.update()
        self._emit_fold_state()

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

    def _face_size(self) -> QSize:
        """떠 있는 접힌 메모의 크기(쪽지 또는 아이콘)."""
        return (QSize(nf.ICON_SIZE) if self._style_pref() == nf.FOLD_ICON
                else QSize(nf.SLIP_SIZE))

    def _resolve_icon_pos(self, pos: QPoint) -> QPoint:
        avail = self._screen_rect_at(pos + QPoint(nf.ICON_SIZE.width() // 2,
                                                  nf.ICON_SIZE.height() // 2))
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
        anim.setDuration(int(ms))
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
        anim.setDuration(int(ms))
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
        return QRect(pos, self._face_size())

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
        if initial:
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

    def showEvent(self, event) -> None:
        super().showEvent(event)
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
                              thumb=self._fold_thumb())
            else:
                nf.paint_slip(painter, QRectF(self.rect()), self.color,
                              self._title(), dock=dock,
                              private=bool(self.private_owner),
                              hover=self._slip_hover, font=self.font())
            painter.end()
            return
        rect = self.rect()
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(self.color))
        painter.drawRoundedRect(rect, config.CORNER_RADIUS, config.CORNER_RADIUS)
        bar = QColor(self.color)
        h, s, v, a = bar.getHsv()
        v = min(255, v + 18)
        s = max(0, s - 20)
        bar.setHsv(h, s, v, a)
        hb = QRect(rect.x(), rect.y(), rect.width(), config.HANDLE_BAR_HEIGHT)
        path = QPainterPath()
        path.addRoundedRect(hb, config.CORNER_RADIUS, config.CORNER_RADIUS)
        painter.fillPath(path, bar)
        painter.end()
