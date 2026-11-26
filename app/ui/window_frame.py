"""모든 창의 상단바를 앱이 직접 그린다.

Windows 기본 상단바(회색 띠, 작은 제목, 프로그램 아이콘) 대신 앱 모양의 상단바를
둔다. 창은 프레임 없는 창이지만 Windows 의 창 동작은 그대로 둔다.

* 상단바의 빈 곳을 끌면 옮긴다(화면 위로 끌면 최대화, 옆으로 끌면 반쪽 맞춤).
* 두 번 누르면 최대화와 되돌리기(최대화가 있는 창만).
* 크기를 바꿀 수 있는 창은 가장자리 6px 에서 크기를 바꾼다.
* Windows 11 은 최대화 단추에 마우스를 올리면 맞춤 레이아웃이 뜬다.
* 그림자와 둥근 모서리(Windows 11)는 Windows 가 그린다.

Windows 에서는 창 스타일에 캡션과 두꺼운 테두리를 되돌려 주고(맞춤과 애니메이션이
살아난다), 그 테두리 자리는 WM_NCCALCSIZE 로 없애며, 가장자리와 최대화 단추는
WM_NCHITTEST 로 알려 준다. 메시지는 앱 전체의 네이티브 필터 하나가 받아, 등록한
창의 것만 다룬다. 다른 운영체제와 시험(offscreen)에서는 상단바만 그린다.

상단바의 종류:

* ``WINDOW``: 큰 창(메인 창, 폴더 창, 그림 보기, 수정 이력). 높이 48, 왼쪽에 창
  아이콘과 이름, 오른쪽에 최소화, 최대화, 닫기(46x48, 닫기는 빨강).
* ``DIALOG``: 대화 상자. 높이 52, 본문과 같은 왼쪽 선(20px)에 창 아이콘과 제목,
  닫기는 둥근 단추 하나.
* ``BARE``: 확인 창. 제목을 그리지 않고 둥근 닫기만(묻는 말이 곧 제목이고, 그 옆에
  묻는 아이콘이 있다).

창 아이콘과 이름은 같은 크기다(``MARK_SIZE``, ``TITLE_PX``). 메인 창만 조금 크다
(``TitleBar.set_lead_size``).
"""
from __future__ import annotations

import ctypes
import platform
import sys
import weakref
from ctypes import wintypes

import shiboken6

from PySide6.QtCore import (
    QAbstractNativeEventFilter,
    QEvent,
    QObject,
    QPoint,
    QPointF,
    QRectF,
    QSize,
    Qt,
)
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPlatformSurfaceEvent
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QWidget,
)

from .. import i18n

WINDOW, DIALOG, BARE = "window", "dialog", "bare"
HEIGHTS = {WINDOW: 48, DIALOG: 52, BARE: 40}
MARK_SIZE = 22               # 상단바의 창 아이콘
TITLE_PX = 15                # 상단바의 창 이름
_MARK_GAP = 8                # 아이콘과 이름 사이
EDGE = 6                     # 크기를 바꾸는 가장자리(논리 픽셀)
_CAPTION_W = 46


def _is_windows() -> bool:
    return platform.system() == "Windows"


def _is_win11() -> bool:
    try:
        return _is_windows() and sys.getwindowsversion().build >= 22000
    except AttributeError:
        return False


# ── 상단바의 단추 ──

class CaptionButton(QAbstractButton):
    """최소화, 최대화(되돌리기), 닫기. Windows 11 과 같은 폭(46)에
    1px 선으로 그린 그림. 닫기는 마우스를 올리면 빨갛다."""

    MIN, MAX, CLOSE = "min", "max", "close"

    def __init__(self, kind: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.kind = kind
        self.maximized = False
        self.active = True
        # Windows 11 의 맞춤 레이아웃 때문에 최대화 단추 위의 마우스는 Qt 가 아니라
        # 네이티브 필터가 알려 준다.
        self.native_hover = False
        self.native_down = False
        self.setFixedSize(_CAPTION_W, HEIGHTS[WINDOW])
        self.setFocusPolicy(Qt.NoFocus)
        self.setAttribute(Qt.WA_Hover)
        self._retip()

    def _retip(self) -> None:
        key = {self.MIN: "frame.minimize", self.CLOSE: "frame.close"}.get(
            self.kind, "frame.restore" if self.maximized else "frame.maximize")
        self.setToolTip(i18n.t(key))
        self.setAccessibleName(i18n.t(key))

    def set_maximized(self, on: bool) -> None:
        if self.maximized != on:
            self.maximized = on
            self._retip()
            self.update()

    def hovered(self) -> bool:
        return self.native_hover or self.underMouse()

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        down = self.isDown() or self.native_down
        if self.kind == self.CLOSE and (self.hovered() or down):
            p.fillRect(self.rect(), QColor("#C42B1C" if not down else "#B22A1B"))
            ink = QColor("#FFFFFF")
        else:
            if self.hovered() or down:
                p.fillRect(self.rect(), QColor(0, 0, 0, 24 if down else 14))
            ink = QColor("#1F1F1F" if self.active else "#9A9A9A")
        p.setRenderHint(QPainter.Antialiasing, self.kind == self.CLOSE)
        pen = QPen(ink, 1)
        pen.setCosmetic(True)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        c = QPointF(self.width() / 2, self.height() / 2)
        s = 5.0
        if self.kind == self.MIN:
            p.drawLine(QPointF(c.x() - s, c.y() + 0.5), QPointF(c.x() + s, c.y() + 0.5))
        elif self.kind == self.MAX and not self.maximized:
            p.drawRect(QRectF(c.x() - s + 0.5, c.y() - s + 0.5, 2 * s - 1, 2 * s - 1))
        elif self.kind == self.MAX:
            p.drawRect(QRectF(c.x() - s + 0.5, c.y() - s + 2.5, 2 * s - 3, 2 * s - 3))
            p.drawLine(QPointF(c.x() - s + 2.5, c.y() - s + 0.5),
                       QPointF(c.x() + s - 0.5, c.y() - s + 0.5))
            p.drawLine(QPointF(c.x() + s - 0.5, c.y() - s + 0.5),
                       QPointF(c.x() + s - 0.5, c.y() + s - 2.5))
        else:
            p.drawLine(QPointF(c.x() - s, c.y() - s), QPointF(c.x() + s, c.y() + s))
            p.drawLine(QPointF(c.x() + s, c.y() - s), QPointF(c.x() - s, c.y() + s))


class RoundCloseButton(QAbstractButton):
    """대화 상자의 닫기: 32px 둥근 단추에 X."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(32, 32)
        self.setFocusPolicy(Qt.NoFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover)
        self.active = True
        self.setToolTip(i18n.t("frame.close"))
        self.setAccessibleName(i18n.t("frame.close"))

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self.underMouse() or self.isDown():
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#E4E4E4" if self.isDown() else "#F0F0F0"))
            p.drawEllipse(QRectF(self.rect()))
        pen = QPen(QColor("#3A3A3A" if self.active else "#9A9A9A"), 1.6)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        c = QPointF(self.width() / 2, self.height() / 2)
        s = 5.0
        p.drawLine(QPointF(c.x() - s, c.y() - s), QPointF(c.x() + s, c.y() + s))
        p.drawLine(QPointF(c.x() + s, c.y() - s), QPointF(c.x() - s, c.y() + s))


# ── 상단바 ──

class _Mark(QWidget):
    """창 아이콘(창이 정한 것, 없으면 앱 아이콘). 화면 배율에 맞게 직접 그린다."""

    def __init__(self, window: QWidget, parent: QWidget) -> None:
        super().__init__(parent)
        self._window = window
        self.setFixedSize(MARK_SIZE, MARK_SIZE)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def paintEvent(self, _event) -> None:
        if not shiboken6.isValid(self._window):
            return
        icon = self._window.windowIcon()
        if icon.isNull():
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        icon.paint(p, self.rect(), Qt.AlignCenter)


class TitleBar(QWidget):
    """창 맨 위의 상단바. ``add_widget`` 으로 가운데 자리(검색칸, 본인 아이콘 등)에
    위젯을 더한다. 빈 곳을 끌면 창이 옮겨지고, 두 번 누르면 최대화가 바뀐다."""

    def __init__(self, window: QWidget, kind: str = DIALOG, *,
                 minimize: bool = True, maximize: bool = True,
                 mark: QWidget | None = None) -> None:
        super().__init__(window)
        self._window = window
        self._active: bool | None = None
        self.kind = kind
        self.setObjectName("TitleBar")
        self.setFixedHeight(HEIGHTS[kind])
        self.setAttribute(Qt.WA_StyledBackground, False)
        row = QHBoxLayout(self)
        self._row = row
        self.min_button: CaptionButton | None = None
        self.max_button: CaptionButton | None = None
        self.close_button: QAbstractButton
        self._title = QLabel(self)
        self._title.setTextFormat(Qt.PlainText)
        self._title.setAttribute(Qt.WA_TransparentForMouseEvents)
        font = QFont(self._title.font())
        font.setPixelSize(TITLE_PX)
        font.setWeight(QFont.DemiBold)
        if kind == WINDOW:
            row.setContentsMargins(14, 0, 0, 0)
            row.setSpacing(10)
        else:
            row.setContentsMargins(20, 0, 12, 0)
            row.setSpacing(8)
        self._title.setFont(font)
        self._left_margin = row.contentsMargins().left()
        # 창 아이콘과 이름은 한 묶음이다(검색칸 같은 가운데 자리는 그 오른쪽).
        self._lead = QWidget(self)
        self._lead.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._lead_row = QHBoxLayout(self._lead)
        self._lead_row.setContentsMargins(0, 0, 0, 0)
        self._lead_row.setSpacing(_MARK_GAP)
        if mark is None and kind != BARE:
            mark = _Mark(window, self._lead)
        self._mark = mark
        if mark is not None:
            mark.setParent(self._lead)
            mark.setAttribute(Qt.WA_TransparentForMouseEvents)
            self._lead_row.addWidget(mark, 0, Qt.AlignVCenter)
        self._title.setParent(self._lead)
        self._lead_row.addWidget(self._title, 0, Qt.AlignVCenter)
        self._lead_row.addStretch(1)
        row.addWidget(self._lead, 0, Qt.AlignVCenter)
        self._lead.setVisible(kind != BARE)
        self._title.setVisible(kind != BARE)
        self._middle = QHBoxLayout()
        self._middle.setContentsMargins(0, 0, 0, 0)
        self._middle.setSpacing(8)
        row.addLayout(self._middle, 1)
        if kind == WINDOW:
            self._middle.addStretch(1)
            self._caption = QHBoxLayout()
            self._caption.setContentsMargins(0, 0, 0, 0)
            self._caption.setSpacing(0)
            if minimize:
                self.min_button = CaptionButton(CaptionButton.MIN, self)
                self.min_button.clicked.connect(window.showMinimized)
                self._caption.addWidget(self.min_button)
            if maximize:
                self.max_button = CaptionButton(CaptionButton.MAX, self)
                self.max_button.clicked.connect(self.toggle_maximized)
                self._caption.addWidget(self.max_button)
            self.close_button = CaptionButton(CaptionButton.CLOSE, self)
            self._caption.addWidget(self.close_button)
            row.addLayout(self._caption)
        else:
            self._middle.addStretch(1)
            self.close_button = RoundCloseButton(self)
            row.addWidget(self.close_button, 0, Qt.AlignVCenter)
        self.close_button.clicked.connect(window.close)
        self.set_title(window.windowTitle())
        window.windowTitleChanged.connect(self.set_title)

    # ── 바깥에서 쓰는 것 ──

    def set_title(self, text: str) -> None:
        self._title.setText(text or "")

    def title(self) -> str:
        return self._title.text()

    def set_lead_width(self, px: int) -> None:
        """가운데 자리(검색칸 등)가 상단바 왼쪽 끝에서 ``px`` 떨어져 시작하게 창
        아이콘과 이름 묶음의 폭을 맞춘다. 0 이면 이름 길이대로 둔다."""
        if px <= 0:
            self._lead.setMinimumWidth(0)
            self._lead.setMaximumWidth(16777215)
            return
        used = self._left_margin + self._row.spacing()
        self._lead.setFixedWidth(max(0, px - used))

    def set_lead_size(self, mark: int, title_px: int) -> None:
        """창 아이콘과 이름의 크기를 바꾼다(메인 창)."""
        if self._mark is not None:
            self._mark.setFixedSize(mark, mark)
        font = QFont(self._title.font())
        font.setPixelSize(title_px)
        self._title.setFont(font)

    def add_widget(self, widget: QWidget, stretch: int = 0) -> None:
        """가운데 자리(제목 오른쪽, 단추 왼쪽)에 위젯을 더한다. 더한 순서대로 오른쪽에
        붙는다(맨 앞의 늘어나는 빈칸 뒤)."""
        widget.setParent(self)
        self._middle.addWidget(widget, stretch, Qt.AlignVCenter)

    def insert_widget(self, index: int, widget: QWidget, stretch: int = 0) -> None:
        widget.setParent(self)
        self._middle.insertWidget(index, widget, stretch, Qt.AlignVCenter)

    def can_maximize(self) -> bool:
        return self.max_button is not None and self.max_button.isVisible()

    def toggle_maximized(self) -> None:
        w = self._window
        if w.isMaximized():
            w.showNormal()
        else:
            w.showMaximized()

    def refresh_state(self) -> None:
        """창이 앞에 있는가와 최대화 여부를 그림에 입힌다(바뀐 것만)."""
        if not shiboken6.isValid(self._window):
            return
        active = self._window.isActiveWindow()
        if self.max_button is not None:
            self.max_button.set_maximized(self._window.isMaximized())
        if active == self._active:
            return
        self._active = active
        for b in (self.min_button, self.max_button, self.close_button):
            if b is not None:
                b.active = active
                b.update()
        palette = self._title.palette()
        palette.setColor(self._title.foregroundRole(),
                         QColor("#1F1F1F" if active else "#8C8C8C"))
        self._title.setPalette(palette)

    def is_drag_area(self, pos: QPoint) -> bool:
        """상단바 안의 이 자리를 끌면 창이 옮겨지는가(단추나 입력칸 위가 아닌가)."""
        if not self.rect().contains(pos):
            return False
        child = self.childAt(pos)
        while child is not None and child is not self:
            if child is self._title or isinstance(child, QLabel) and child.testAttribute(
                    Qt.WA_TransparentForMouseEvents):
                return True
            if child.focusPolicy() != Qt.NoFocus or isinstance(child, QAbstractButton):
                return False
            child = child.parentWidget()
        return True

    # ── 마우스 ──

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.is_drag_area(event.position().toPoint()):
            handle = self._window.windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if (event.button() == Qt.LeftButton and self.can_maximize()
                and self.is_drag_area(event.position().toPoint())):
            self.toggle_maximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#FFFFFF"))


# ── 창에 입히기 ──

_bars: "weakref.WeakKeyDictionary[QWidget, TitleBar]" = weakref.WeakKeyDictionary()


def bar_of(window: QWidget) -> TitleBar | None:
    return _bars.get(window)


class _Keeper(QObject):
    """창의 크기, 상태, 활성이 바뀔 때 상단바를 맞추고 네이티브 설정을 건다."""

    def __init__(self, window: QWidget, bar: TitleBar, overlay: bool,
                 resizable) -> None:
        super().__init__(window)
        self._window = window
        self._bar = bar
        self._overlay = overlay
        self._resizable = resizable
        self._hwnd = 0
        self._handle = None

    def resizable(self) -> bool:
        if self._resizable is not None:
            return bool(self._resizable)
        w = self._window
        if w.minimumSize() == w.maximumSize():
            return False
        lay = w.layout()
        return not (lay is not None and lay.sizeConstraint() == lay.SizeConstraint.SetFixedSize)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if not shiboken6.isValid(self._window):
            return False
        if obj is self._handle and obj is not None:
            # 창을 닫으면 Qt 가 창 핸들을 없애고 다시 열 때 새로 만든다. 새 핸들에도
            # 테두리 설정을 건다(안 그러면 각진 기본 창이 되고 가장자리가 안 먹는다).
            if event.type() == QEvent.PlatformSurface:
                if event.surfaceEventType() == QPlatformSurfaceEvent.SurfaceCreated:
                    self._attach_native()
                else:
                    _native.forget(self._hwnd)
                    self._hwnd = 0
            return False
        if obj is not self._window:
            return False
        t = event.type()
        if t == QEvent.Resize and self._overlay:
            self._bar.setGeometry(0, 0, self._window.width(), self._bar.height())
            self._bar.raise_()
        elif t == QEvent.WindowIconChange and self._bar._mark is not None:
            self._bar._mark.update()
        elif t in (QEvent.WindowStateChange, QEvent.ActivationChange):
            self._bar.refresh_state()
        elif t in (QEvent.Show, QEvent.WinIdChange):
            self._bar.refresh_state()
            self._attach_native()
        return False

    def _attach_native(self) -> None:
        if not _is_windows() or QApplication.platformName() != "windows":
            return
        handle = self._window.windowHandle()
        if handle is not None and handle is not self._handle:
            if self._handle is not None and shiboken6.isValid(self._handle):
                self._handle.removeEventFilter(self)
            self._handle = handle
            handle.installEventFilter(self)
        # 창의 지금 핸들(없으면 지금 만든다). 위젯의 winId() 는 옛 값일 수 있다.
        hwnd = int(handle.winId()) if handle is not None else int(self._window.winId())
        if not hwnd:
            return
        if hwnd != self._hwnd:
            _native.forget(self._hwnd)
            self._hwnd = hwnd
        _native.register(hwnd, self._window, self._bar, self.resizable())


def install(window: QWidget, kind: str = DIALOG, *, minimize: bool | None = None,
            maximize: bool | None = None, resizable: bool | None = None,
            mark: QWidget | None = None) -> TitleBar:
    """창에 상단바를 입힌다. 창의 레이아웃을 다 만든 뒤, 보이기 전에 부른다.

    대화 상자는 상단바를 맨 위에 겹쳐 두고 레이아웃 위 여백을 상단바 높이만큼
    늘린다(창 코드를 고치지 않는다). 메인 창(QMainWindow)은 메뉴 자리에 둔다.
    ``minimize``/``maximize`` 를 주지 않으면 큰 창(WINDOW)만 둘 다 있다.
    ``resizable`` 을 주지 않으면 크기가 정해진 창인지로 가린다."""
    existing = _bars.get(window)
    if existing is not None:
        return existing
    big = kind == WINDOW
    window.setWindowFlags(window.windowFlags() | Qt.FramelessWindowHint)
    bar = TitleBar(window, kind,
                   minimize=big if minimize is None else minimize,
                   maximize=big if maximize is None else maximize, mark=mark)
    overlay = not isinstance(window, QMainWindow)
    if overlay:
        lay = window.layout()
        if lay is not None:
            m = lay.contentsMargins()
            lay.setContentsMargins(m.left(), m.top() + bar.height(), m.right(), m.bottom())
        bar.setGeometry(0, 0, max(window.width(), 1), bar.height())
        bar.raise_()
    else:
        window.setMenuWidget(bar)
    keeper = _Keeper(window, bar, overlay, resizable)
    window.installEventFilter(keeper)
    _bars[window] = bar
    _native.ensure_installed()
    return bar


# ── Windows: 테두리 없애기, 가장자리와 최대화 단추 알리기 ──

_GWL_STYLE = -16
_WS_CAPTION = 0x00C00000
_WS_SYSMENU = 0x00080000
_WS_THICKFRAME = 0x00040000
_WS_MINIMIZEBOX = 0x00020000
_WS_MAXIMIZEBOX = 0x00010000
_SWP_FLAGS = 0x0002 | 0x0001 | 0x0004 | 0x0010 | 0x0020   # NOMOVE NOSIZE NOZORDER NOACTIVATE FRAMECHANGED

_WM_NCCALCSIZE = 0x0083
_WM_NCHITTEST = 0x0084
_WM_NCMOUSEMOVE = 0x00A0
_WM_NCLBUTTONDOWN = 0x00A1
_WM_NCLBUTTONUP = 0x00A2
_WM_NCLBUTTONDBLCLK = 0x00A3
_WM_NCMOUSELEAVE = 0x02A2
_WM_MOUSEMOVE = 0x0200

HTTRANSPARENT, HTCLIENT, HTCAPTION, HTMAXBUTTON = -1, 1, 2, 9
HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT, HTTOPRIGHT = 10, 11, 12, 13, 14
HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = 15, 16, 17


class _MARGINS(ctypes.Structure):
    _fields_ = [("left", ctypes.c_int), ("right", ctypes.c_int),
                ("top", ctypes.c_int), ("bottom", ctypes.c_int)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


class _NCCALCSIZE_PARAMS(ctypes.Structure):
    _fields_ = [("rgrc", wintypes.RECT * 3), ("lppos", ctypes.c_void_p)]


def hit_test(window: QWidget, bar: TitleBar, local: QPointF, *,
             resizable: bool, snap: bool) -> int:
    """창 안의 이 자리(창 왼쪽 위에서 잰 논리 픽셀)가 무엇인가: 가장자리, 최대화
    단추, 그 밖(HTCLIENT, Qt 가 다룬다)."""
    x, y = local.x(), local.y()
    w, h = window.width(), window.height()
    if resizable and not window.isMaximized() and not window.isFullScreen():
        left, right = x < EDGE, x >= w - EDGE
        top, bottom = y < EDGE, y >= h - EDGE
        if top and left:
            return HTTOPLEFT
        if top and right:
            return HTTOPRIGHT
        if bottom and left:
            return HTBOTTOMLEFT
        if bottom and right:
            return HTBOTTOMRIGHT
        if left:
            return HTLEFT
        if right:
            return HTRIGHT
        if top:
            return HTTOP
        if bottom:
            return HTBOTTOM
    button = bar.max_button
    if snap and button is not None and button.isVisible():
        corner = button.mapTo(window, QPoint(0, 0))
        if (corner.x() <= x < corner.x() + button.width()
                and corner.y() <= y < corner.y() + button.height()):
            return HTMAXBUTTON
    return HTCLIENT


class _NativeFrames(QAbstractNativeEventFilter):
    """앱 전체의 Windows 메시지에서 상단바를 입힌 창의 것만 다룬다."""

    def __init__(self) -> None:
        super().__init__()
        self._installed = False
        self._frames: dict[int, tuple] = {}

    def ensure_installed(self) -> None:
        app = QApplication.instance()
        if self._installed or app is None or not _is_windows():
            return
        app.installNativeEventFilter(self)
        self._installed = True

    def register(self, hwnd: int, window: QWidget, bar: TitleBar, resizable: bool) -> None:
        if hwnd not in self._frames:
            window.destroyed.connect(lambda *_a, h=hwnd: self.forget(h))
        self._frames[hwnd] = (weakref.ref(window), weakref.ref(bar), resizable)
        try:
            user32 = ctypes.windll.user32
            get = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
            put = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
            get.restype = ctypes.c_ssize_t
            get.argtypes = [wintypes.HWND, ctypes.c_int]
            put.restype = ctypes.c_ssize_t
            put.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
            style = get(hwnd, _GWL_STYLE)
            style |= _WS_CAPTION | _WS_SYSMENU
            if bar.min_button is not None:
                style |= _WS_MINIMIZEBOX
            if resizable:
                style |= _WS_THICKFRAME
            if bar.max_button is not None:
                style |= _WS_MAXIMIZEBOX
            put(hwnd, _GWL_STYLE, style)
            user32.SetWindowPos(wintypes.HWND(hwnd), None, 0, 0, 0, 0, _SWP_FLAGS)
            margins = _MARGINS(0, 0, 1, 0)
            ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(
                wintypes.HWND(hwnd), ctypes.byref(margins))
        except Exception:
            pass

    def forget(self, hwnd: int) -> None:
        self._frames.pop(hwnd, None)

    def nativeEventFilter(self, event_type, message):  # noqa: N802
        try:
            if bytes(event_type) != b"windows_generic_MSG":
                return False, 0
            msg = wintypes.MSG.from_address(int(message))
        except (TypeError, ValueError, OSError):
            return False, 0
        entry = self._frames.get(int(msg.hWnd or 0))
        if entry is None:
            if msg.message == _WM_NCHITTEST:
                return self._child_hit(msg)
            return False, 0
        window, bar = entry[0](), entry[1]()
        if window is None or bar is None:
            return False, 0
        kind = msg.message
        if kind == _WM_NCCALCSIZE:
            return self._calc_size(msg, window)
        if kind == _WM_NCHITTEST:
            code = hit_test(window, bar, self._client_point(msg, window),
                            resizable=entry[2], snap=_is_win11())
            self._hover_max(bar, code == HTMAXBUTTON)
            if code == HTCLIENT:
                return False, 0
            return True, code
        snap_button = bar.max_button
        if snap_button is None:
            return False, 0
        if kind == _WM_NCMOUSEMOVE:
            self._hover_max(bar, msg.wParam == HTMAXBUTTON)
        elif kind in (_WM_NCMOUSELEAVE, _WM_MOUSEMOVE):
            self._hover_max(bar, False)
            snap_button.native_down = False
        elif kind in (_WM_NCLBUTTONDOWN, _WM_NCLBUTTONDBLCLK) and msg.wParam == HTMAXBUTTON:
            snap_button.native_down = True
            snap_button.update()
            return True, 0
        elif kind == _WM_NCLBUTTONUP and msg.wParam == HTMAXBUTTON:
            pressed, snap_button.native_down = snap_button.native_down, False
            snap_button.update()
            if pressed:
                bar.toggle_maximized()
            return True, 0
        return False, 0

    def _child_hit(self, msg):
        """창 안의 자식 창(창 핸들을 가진 위젯)이 받은 판정. 가장자리나 최대화 단추
        위면 '투명'이라 답해 바깥 창이 판정하게 한다(그래야 크기를 바꾼다)."""
        try:
            root = int(ctypes.windll.user32.GetAncestor(msg.hWnd, 2) or 0)   # GA_ROOT
        except Exception:
            return False, 0
        entry = self._frames.get(root)
        if entry is None or root == int(msg.hWnd or 0):
            return False, 0
        window, bar = entry[0](), entry[1]()
        if window is None or bar is None:
            return False, 0
        probe = wintypes.MSG()
        probe.hWnd, probe.lParam = root, msg.lParam
        code = hit_test(window, bar, self._client_point(probe, window),
                        resizable=entry[2], snap=_is_win11())
        return (True, HTTRANSPARENT) if code != HTCLIENT else (False, 0)

    @staticmethod
    def _client_point(msg, window: QWidget) -> QPointF:
        """메시지에 실린 화면 자리(물리 픽셀)를 창 안의 논리 픽셀로."""
        lp = int(msg.lParam)
        point = wintypes.POINT(ctypes.c_short(lp & 0xFFFF).value,
                               ctypes.c_short((lp >> 16) & 0xFFFF).value)
        ctypes.windll.user32.ScreenToClient(msg.hWnd, ctypes.byref(point))
        dpr = window.devicePixelRatioF() or 1.0
        return QPointF(point.x / dpr, point.y / dpr)

    @staticmethod
    def _hover_max(bar: TitleBar, on: bool) -> None:
        b = bar.max_button
        if b is not None and b.native_hover != on:
            b.native_hover = on
            b.update()

    @staticmethod
    def _calc_size(msg, window: QWidget):
        """테두리 자리를 없애 창 전체를 내용으로 쓴다. 최대화하면 창이 화면의 쓸 수
        있는 곳(작업 표시줄 뺀 곳) 밖으로 넘친 만큼 안으로 들인다(내용이 잘리지
        않게). Qt 가 이미 쓸 수 있는 곳에 맞춰 두었으면 그대로다."""
        if not msg.wParam:
            return True, 0
        try:
            if window.isMaximized() and not window.isFullScreen():
                params = _NCCALCSIZE_PARAMS.from_address(msg.lParam)
                user32 = ctypes.windll.user32
                monitor = user32.MonitorFromWindow(msg.hWnd, 2)     # MONITOR_DEFAULTTONEAREST
                info = _MONITORINFO()
                info.cbSize = ctypes.sizeof(_MONITORINFO)
                if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                    rect, work = params.rgrc[0], info.rcWork
                    rect.left = max(rect.left, work.left)
                    rect.top = max(rect.top, work.top)
                    rect.right = min(rect.right, work.right)
                    rect.bottom = min(rect.bottom, work.bottom)
        except Exception:
            pass
        return True, 0


_native = _NativeFrames()


__all__ = ["BARE", "DIALOG", "WINDOW", "CaptionButton", "RoundCloseButton", "TitleBar",
           "bar_of", "hit_test", "install"]
