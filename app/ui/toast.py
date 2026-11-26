"""화면 오른쪽 아래에 잠깐 뜨는 알림.

Windows 알림(트레이 풍선)에 기대지 않는다. Windows 설정에서 알림을 꺼 둔 PC 나
'방해 금지'를 켠 PC 에서는 풍선이 아예 뜨지 않아, 누가 새 메모를 만들었다는
알림이 조용히 사라졌다. 여기서는 앱이 직접 작은 창을 띄운다.

하던 일을 끊지 않는 것이 먼저다. 창은 초점을 가져가지 않고, 몇 초 뒤 스스로
사라지며, 전체 화면 발표나 게임 중에는 띄우지 않는다."""
from __future__ import annotations

import platform

from PySide6.QtCore import QObject, QPoint, QPropertyAnimation, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QStyle,
    QStyleOptionToolButton,
    QStylePainter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import i18n
from . import dwm
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


class SplitButton(QToolButton):
    """누르면 제 일을 하고, 글 바로 옆 화살표를 누르면 펼침 메뉴를 연다.

    Qt 의 나눔 단추는 화살표를 단추 끝에 붙여 글과 멀어진다. 여기서는 글과
    화살표를 한 덩이로 가운데 두고, 화살표부터 오른쪽 끝까지를 메뉴 자리로 본다."""

    ARROW = 10
    GAP = 6

    def __init__(self, parent: QWidget | None, menu: QMenu, ink: str) -> None:
        super().__init__(parent)
        self._menu = menu
        self._ink = QColor(ink)
        self._arrow = svg_icon("chevron_down", color=ink)

    def menu(self) -> QMenu:
        return self._menu

    def arrow_rect(self) -> QRect:
        text = self.fontMetrics().horizontalAdvance(self.text())
        left = (self.width() - (text + self.GAP + self.ARROW)) // 2 + text + self.GAP
        return QRect(left, (self.height() - self.ARROW) // 2, self.ARROW, self.ARROW)

    def in_menu_zone(self, x: float) -> bool:
        return x >= self.arrow_rect().left() - 6

    def open_menu(self) -> None:
        self.setDown(False)
        self._menu.exec(self.mapToGlobal(QPoint(0, self.height() + 4)))

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.in_menu_zone(event.position().x()):
            event.accept()
            QTimer.singleShot(0, self.open_menu)
            return
        super().mousePressEvent(event)

    def paintEvent(self, _event) -> None:
        # 바탕과 테두리는 스타일 시트대로 그리고, 글과 화살표는 한 덩이로 둔다.
        p = QStylePainter(self)
        opt = QStyleOptionToolButton()
        self.initStyleOption(opt)
        opt.text = ""
        opt.icon = QIcon()
        p.drawComplexControl(QStyle.CC_ToolButton, opt)
        arrow = self.arrow_rect()
        text_rect = QRect(0, 0, arrow.left() - self.GAP, self.height())
        p.setPen(self._ink)
        p.setFont(self.font())
        p.drawText(text_rect, Qt.AlignRight | Qt.AlignVCenter, self.text())
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        self._arrow.paint(p, arrow)


class Toast(QWidget):
    """알림 한 장. 누르면 ``clicked``, 닫히면 ``closed`` 를 낸다.

    ``actions`` 를 주면 카드 폭 전체에 같은 너비의 단추 줄을 둔다((글, 열쇠, 짙게)).
    단추를 누르면 ``action(열쇠)`` 를 내고 닫힌다. ``menus`` 는 단추 열쇠마다 펼침
    메뉴((글, 열쇠) 목록)다. ``persistent`` 면 스스로 사라지지 않는다(리마인더)."""

    clicked = Signal()
    closed = Signal()
    action = Signal(str)

    def __init__(self, text: str, icon: QIcon | None = None, *,
                 warn: bool = False, animate: bool = True, sub: str = "",
                 actions: list | None = None, menus: dict | None = None,
                 persistent: bool = False):
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
        self._persistent = bool(persistent)
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

        box = QVBoxLayout(card)
        # 단추 줄이 있으면 좌우 여백을 맞춰 닫기 단추와 단추 줄의 끝을 나란히 둔다.
        box.setContentsMargins(*((12, 12, 12, 12) if actions else (14, 12, 8, 12)))
        box.setSpacing(10)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        box.addLayout(row)
        mark = QLabel(card)
        mark.setFixedSize(22, 22)
        mark.setStyleSheet("background: transparent; border: none;")
        if warn:
            mark.setPixmap(svg_icon("warning", color="#FBBF24").pixmap(20, 20))
        elif icon is not None and not icon.isNull():
            mark.setPixmap(icon.pixmap(22, 22))
        row.addWidget(mark, 0, Qt.AlignTop)

        # 알림 글에는 남의 닉네임과 메모 제목이 들어간다. 글자 그대로 보인다.
        self._label = QLabel(card)
        self._label.setTextFormat(Qt.PlainText)
        self._label.setText(text)
        self._label.setWordWrap(True)
        self._label.setStyleSheet(
            "color: #FFFFFF; font-size: 13px; font-weight: 600; "
            "background: transparent; border: none;")
        if sub:
            texts = QVBoxLayout()
            texts.setContentsMargins(0, 0, 0, 0)
            texts.setSpacing(2)
            texts.addWidget(self._label)
            self._sub = QLabel(card)
            self._sub.setTextFormat(Qt.PlainText)
            self._sub.setText(sub)
            self._sub.setWordWrap(True)
            self._sub.setStyleSheet(
                "color: #B8B8B8; font-size: 12px; background: transparent; border: none;")
            texts.addWidget(self._sub)
            row.addLayout(texts, 1)
        else:
            self._sub = None
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

        self._buttons: dict[str, QToolButton] = {}
        if actions:
            acts = QHBoxLayout()
            acts.setContentsMargins(0, 0, 0, 0)
            acts.setSpacing(6)
            for label, key, main in actions:
                more = (menus or {}).get(key)
                if more:
                    menu = QMenu(card)
                    for text_, key_ in more:
                        menu.addAction(text_).triggered.connect(
                            lambda _c=False, k=key_: self._act(k))
                    b = SplitButton(card, menu, "#1F1F1F" if main else "#FFFFFF")
                else:
                    b = QToolButton(card)
                b.setText(label)
                b.setCursor(Qt.PointingHandCursor)
                b.setFocusPolicy(Qt.NoFocus)
                b.setFixedHeight(30)
                b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
                b.setToolButtonStyle(Qt.ToolButtonTextOnly)
                b.setStyleSheet(
                    "QToolButton { border: 1px solid %s; border-radius: 7px; background: %s; "
                    "color: %s; font-size: 12px; font-weight: 600; padding: 0 6px; }"
                    "QToolButton:hover { background: %s; }"
                    % (("#FFFFFF", "#FFFFFF", "#1F1F1F", "#E5E5E5") if main else
                       ("rgba(255,255,255,0.22)", "transparent", "#FFFFFF",
                        "rgba(255,255,255,0.12)")))
                b.clicked.connect(lambda _c=False, k=key: self._act(k))
                self._buttons[key] = b
                acts.addWidget(b, 1)
            box.addLayout(acts)

        self.setFixedWidth(TOAST_WIDTH + 2 * _SHADOW)
        self.adjustSize()

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(SHOW_MS)
        self._timer.timeout.connect(self.dismiss)

    def text(self) -> str:
        return self._label.text()

    def sub_text(self) -> str:
        return self._sub.text() if self._sub is not None else ""

    def buttons(self) -> dict:
        return dict(self._buttons)

    @property
    def persistent(self) -> bool:
        return self._persistent

    def _act(self, key: str) -> None:
        if self._closing:
            return
        self.action.emit(key)
        self.dismiss()

    def present(self) -> None:
        if self._animate:
            self.setWindowOpacity(0.0)
            self.show()
            self._run_fade(1.0, None)
        else:
            self.show()
        if not self._persistent:
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

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        # 그림자 여백까지 창이라, Windows 가 두르는 그림자와 테두리가 알림을
        # 감싼 판처럼 보인다.
        dwm.set_bare(int(self.winId()))

    def enterEvent(self, event) -> None:  # noqa: N802
        # 읽는 동안에는 사라지지 않는다.
        self._timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if not self._closing and not self._persistent:
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
        # 리마인더가 이미 셋 떠 있어 기다리는 리마인더(앞의 것을 닫으면 차례로 뜬다).
        self._waiting: list[Toast] = []

    def toasts(self) -> list[Toast]:
        return list(self._toasts)

    def show_text(self, text: str, *, warn: bool = False) -> bool:
        """알림을 띄운다. 발표나 전체 화면 중이라 띄우지 않았으면 False."""
        if not text or user_is_busy():
            return False
        from . import note_fold
        toast = Toast(text, self._icon, warn=warn,
                      animate=note_fold.system_animations_enabled())
        self._add(toast)
        return True

    def show_actions(self, text: str, sub: str, actions: list, *,
                     menus: dict | None = None, persistent: bool = True):
        """단추가 달린 알림(리마인더). 띄운 알림을 돌려준다(띄우지 않았으면 None)."""
        if not text or user_is_busy():
            return None
        from . import note_fold
        toast = Toast(text, self._icon, sub=sub, actions=actions, menus=menus,
                      persistent=persistent,
                      animate=note_fold.system_animations_enabled())
        self._add(toast)
        return toast

    def _add(self, toast: Toast) -> None:
        if not toast.persistent:
            toast.clicked.connect(self.clicked.emit)
        toast.closed.connect(lambda t=toast: self._forget(t))
        if len(self._toasts) >= MAX_VISIBLE:
            # 스스로 사라지는 것부터 내린다. 리마인더는 내리지 않는다: 다 리마인더면
            # 새 리마인더는 기다렸다가 뜨고, 스스로 사라지는 알림은 띄우지 않는다.
            transient = next((t for t in self._toasts if not t.persistent), None)
            if transient is not None:
                self._toasts.remove(transient)
                transient._animate = False
                transient.dismiss()
            elif toast.persistent:
                self._waiting.append(toast)
                return
            else:
                toast.deleteLater()
                return
        self._toasts.append(toast)
        self._place()
        toast.present()

    def close_all(self) -> None:
        waiting, self._waiting = self._waiting, []
        for toast in waiting:
            toast.deleteLater()
        for toast in list(self._toasts):
            toast._animate = False
            toast.dismiss()
        self._toasts.clear()

    def _forget(self, toast: Toast) -> None:
        if toast in self._waiting:
            self._waiting.remove(toast)
        if toast in self._toasts:
            self._toasts.remove(toast)
            shown = []
            while self._waiting and len(self._toasts) < MAX_VISIBLE:
                nxt = self._waiting.pop(0)
                self._toasts.append(nxt)
                shown.append(nxt)
            self._place()
            for nxt in shown:
                nxt.present()

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
