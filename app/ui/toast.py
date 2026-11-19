"""화면 오른쪽 아래에 잠깐 뜨는 알림.

Windows 알림(트레이 풍선)에 기대지 않는다. Windows 설정에서 알림을 꺼 둔 PC 나
'방해 금지'를 켠 PC 에서는 풍선이 아예 뜨지 않아, 누가 새 메모를 만들었다는
알림이 조용히 사라졌다. 여기서는 앱이 직접 작은 창을 띄운다.

하던 일을 끊지 않는 것이 먼저다. 창은 초점을 가져가지 않고, 몇 초 뒤 스스로
사라지며, 전체 화면 발표나 게임 중에는 띄우지 않는다."""
from __future__ import annotations

import platform

from PySide6.QtCore import QObject, QPropertyAnimation, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import i18n
from .icons import svg_icon

TOAST_WIDTH = 328
SHOW_MS = 6000
MAX_VISIBLE = 3
_EDGE = 12          # 화면 가장자리에서 띄우는 거리
_GAP = 2            # 알림 사이 간격(그림자 여백은 창 안에 있다)
_SHADOW = 10        # 그림자가 번질 자리

# SHQueryUserNotificationState 의 값 가운데 알림을 띄우면 안 되는 상태:
# 전체 화면 프로그램 사용 중, Direct3D 전체 화면, 발표 모드.
_BUSY_STATES = (2, 3, 4)


def user_is_busy() -> bool:
    """전체 화면으로 발표하거나 게임하는 중인가. 알 수 없으면 아니라고 본다."""
    if platform.system() != "Windows":
        return False
    try:
        import ctypes
        state = ctypes.c_int(0)
        if ctypes.windll.shell32.SHQueryUserNotificationState(
                ctypes.byref(state)) != 0:
            return False
        return state.value in _BUSY_STATES
    except Exception:
        return False


class Toast(QWidget):
    """알림 한 장. 누르면 ``clicked``, 닫히면 ``closed`` 를 낸다."""

    clicked = Signal()
    closed = Signal()

    def __init__(self, text: str, icon: QIcon | None = None, *,
                 warn: bool = False, animate: bool = True):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint
                         | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setFocusPolicy(Qt.NoFocus)
        self.setWindowTitle(i18n.t("app.name"))
        self.setAccessibleName(text)
        self.setCursor(Qt.PointingHandCursor)
        self._animate = animate
        self._closing = False
        self._fade: QPropertyAnimation | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(_SHADOW, _SHADOW, _SHADOW, _SHADOW)
        card = QFrame(self)
        card.setObjectName("ToastCard")
        card.setStyleSheet(
            "QFrame#ToastCard { background: #1F1F1F; border-radius: 12px; "
            "border: 1px solid rgba(255,255,255,0.10); }")
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(18)
        shadow.setOffset(0, 3)
        shadow.setColor(QColor(0, 0, 0, 90))
        card.setGraphicsEffect(shadow)
        outer.addWidget(card)

        row = QHBoxLayout(card)
        row.setContentsMargins(14, 12, 8, 12)
        row.setSpacing(10)
        mark = QLabel(card)
        mark.setFixedSize(22, 22)
        mark.setStyleSheet("background: transparent; border: none;")
        if warn:
            mark.setPixmap(svg_icon("warning", color="#FBBF24").pixmap(20, 20))
        elif icon is not None and not icon.isNull():
            mark.setPixmap(icon.pixmap(22, 22))
        row.addWidget(mark, 0, Qt.AlignTop)

        self._label = QLabel(text, card)
        self._label.setWordWrap(True)
        self._label.setStyleSheet(
            "color: #FFFFFF; font-size: 13px; font-weight: 600; "
            "background: transparent; border: none;")
        row.addWidget(self._label, 1)

        close = QToolButton(card)
        close.setIcon(svg_icon("close", color="#B8B8B8"))
        close.setToolTip(i18n.t("toast.close"))
        close.setAccessibleName(i18n.t("toast.close"))
        close.setFocusPolicy(Qt.NoFocus)
        close.setCursor(Qt.PointingHandCursor)
        close.setFixedSize(24, 24)
        close.setStyleSheet(
            "QToolButton { border: none; border-radius: 6px; "
            "background: transparent; padding: 4px; }"
            "QToolButton:hover { background: rgba(255,255,255,0.14); }")
        close.clicked.connect(self.dismiss)
        row.addWidget(close, 0, Qt.AlignTop)

        self.setFixedWidth(TOAST_WIDTH + 2 * _SHADOW)
        self.adjustSize()

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(SHOW_MS)
        self._timer.timeout.connect(self.dismiss)

    def text(self) -> str:
        return self._label.text()

    def present(self) -> None:
        if self._animate:
            self.setWindowOpacity(0.0)
            self.show()
            self._run_fade(1.0, None)
        else:
            self.show()
        self._timer.start()

    def dismiss(self) -> None:
        """사라진다. 움직임을 쓰면 옅어진 뒤에 닫힌다."""
        if self._closing:
            return
        self._closing = True
        self._timer.stop()
        if self._animate and self.isVisible():
            self._run_fade(0.0, self.close)
        else:
            self.close()

    def _run_fade(self, target: float, then) -> None:
        fade = QPropertyAnimation(self, b"windowOpacity", self)
        fade.setDuration(160)
        fade.setStartValue(self.windowOpacity())
        fade.setEndValue(target)
        if then is not None:
            fade.finished.connect(then)
        self._fade = fade
        fade.start()

    def enterEvent(self, event) -> None:  # noqa: N802
        # 읽는 동안에는 사라지지 않는다.
        self._timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if not self._closing:
            self._timer.start(2000)
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and not self._closing:
            self.clicked.emit()
            self.dismiss()
        super().mouseReleaseEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        self.closed.emit()
        super().closeEvent(event)


class ToastManager(QObject):
    """알림을 화면 오른쪽 아래에 아래부터 쌓는다."""

    clicked = Signal()

    def __init__(self, parent: QObject | None = None, icon: QIcon | None = None):
        super().__init__(parent)
        self._icon = icon
        self._toasts: list[Toast] = []

    def toasts(self) -> list[Toast]:
        return list(self._toasts)

    def show_text(self, text: str, *, warn: bool = False) -> bool:
        """알림을 띄운다. 발표나 전체 화면 중이라 띄우지 않았으면 False."""
        if not text or user_is_busy():
            return False
        from . import note_fold
        toast = Toast(text, self._icon, warn=warn,
                      animate=note_fold.system_animations_enabled())
        toast.clicked.connect(self.clicked.emit)
        toast.closed.connect(lambda t=toast: self._forget(t))
        self._toasts.append(toast)
        while len(self._toasts) > MAX_VISIBLE:
            oldest = self._toasts.pop(0)
            oldest._animate = False
            oldest.dismiss()
        self._place()
        toast.present()
        return True

    def close_all(self) -> None:
        for toast in list(self._toasts):
            toast._animate = False
            toast.dismiss()
        self._toasts.clear()

    def _forget(self, toast: Toast) -> None:
        if toast in self._toasts:
            self._toasts.remove(toast)
            self._place()

    def _area(self) -> QRect:
        screen = QGuiApplication.primaryScreen()
        return (screen.availableGeometry() if screen is not None
                else QRect(0, 0, 1280, 720))

    def _place(self) -> None:
        area = self._area()
        bottom = area.bottom() - _EDGE + _SHADOW
        for toast in self._toasts:          # 먼저 뜬 것이 아래, 새것이 위
            h = toast.sizeHint().height()
            x = area.right() - _EDGE + _SHADOW - toast.width() + 1
            toast.move(x, bottom - h + 1)
            bottom -= h - 2 * _SHADOW + _GAP + 6
