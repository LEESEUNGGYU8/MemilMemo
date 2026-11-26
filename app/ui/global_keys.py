"""어디서나 쓰는 단축키의 Qt 쪽: 키보드 조합 메시지를 받고, 마우스 훅 스레드의 알림을
메인 스레드로 넘긴다. 설정 창의 조합 칸(``HotkeyField``)도 여기 있다."""
from __future__ import annotations

import weakref

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QApplication, QWidget

from .. import hotkeys, i18n
from .theme import COLOR_FOCUS


class _Filter(QAbstractNativeEventFilter):
    def __init__(self, owner: "GlobalKeys") -> None:
        super().__init__()
        self._owner = owner

    def nativeEventFilter(self, event_type, message):  # noqa: N802
        try:
            if bytes(event_type) != b"windows_generic_MSG":
                return False, 0
            from ctypes import wintypes
            msg = wintypes.MSG.from_address(int(message))
        except (TypeError, ValueError, OSError):
            return False, 0
        action = self._owner.keyboard.handle_message(msg.message, int(msg.wParam or 0))
        if action is None:
            return False, 0
        self._owner.triggered.emit(action)
        return True, 0


class GlobalKeys(QObject):
    """설정의 단축키를 걸고, 눌리면 ``triggered(일)`` 을 낸다."""

    triggered = Signal(str)
    _from_hook = Signal(str)

    # 떠 있는 단축키들. 설정의 조합 칸이 조합을 받는 동안 잠시 내려 둔다(걸어 둔 조합은
    # Windows 가 먼저 가로채 칸에 오지 않는다).
    _live: "weakref.WeakSet[GlobalKeys]" = weakref.WeakSet()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        GlobalKeys._live.add(self)
        self._bindings: dict | None = None
        self._held = 0
        self.keyboard = hotkeys.KeyboardHotkeys()
        self.mouse = hotkeys.MouseHook(self._from_hook.emit)
        if QApplication.platformName() == "offscreen":
            # 화면 없이 도는 시험: Windows 전체에 단축키와 훅을 걸지 않는다.
            self.keyboard.register = lambda *_a: 0
            self.keyboard.unregister = lambda *_a: None
            self.mouse.apply = lambda _bindings: None
        self._from_hook.connect(self.triggered.emit, Qt.QueuedConnection)
        self._filter = _Filter(self)
        self._installed = False
        self.busy: list[str] = []

    def apply(self, bindings: dict) -> list[str]:
        """설정 값(``{일: {"key", "mouse"}}``)을 건다. 다른 프로그램이 써서 못 건
        키보드 조합을 돌려준다."""
        clean = hotkeys.clean_bindings(bindings)
        self._bindings = clean
        if self._held:
            return list(self.busy)          # 조합 칸이 받는 중이다. 다 받은 뒤에 건다.
        app = QApplication.instance()
        if app is not None and not self._installed and hotkeys.is_windows():
            app.installNativeEventFilter(self._filter)
            self._installed = True
        self.busy = self.keyboard.apply({a: clean[a]["key"] for a in hotkeys.ACTIONS})
        self.mouse.apply({a: clean[a]["mouse"] for a in hotkeys.ACTIONS})
        return list(self.busy)

    def hold(self) -> None:
        """잠시 내려 둔다(설정의 조합 칸이 받는 동안)."""
        self._held += 1
        if self._held == 1:
            self.keyboard.clear()
            self.mouse.stop()

    def resume(self) -> None:
        if not self._held:
            return
        self._held -= 1
        if self._held == 0 and self._bindings is not None and self._installed:
            self.apply(self._bindings)

    def stop(self) -> None:
        self._bindings = None
        self.keyboard.clear()
        self.mouse.stop()
        app = QApplication.instance()
        if app is not None and self._installed:
            app.removeNativeEventFilter(self._filter)
            self._installed = False


# ── 보이는 글 ──

def display(text: str) -> str:
    """조합을 사람이 읽는 꼴로: 'Ctrl+Alt+N', 'Ctrl+뒤로 단추'."""
    combo = hotkeys.parse(text)
    if combo is None:
        return ""
    names = {hotkeys.MOUSE_BACK: i18n.t("hotkey.mouse_back"),
             hotkeys.MOUSE_FORWARD: i18n.t("hotkey.mouse_forward"),
             hotkeys.MOUSE_MIDDLE: i18n.t("hotkey.mouse_middle")}
    parts = [m for m in hotkeys.MODS if m in combo.mods]
    parts.append(names.get(combo.key, combo.key))
    return "+".join(parts)


_QT_KEYS = {
    Qt.Key_Space: "Space", Qt.Key_PageUp: "PageUp", Qt.Key_PageDown: "PageDown",
    Qt.Key_End: "End", Qt.Key_Home: "Home", Qt.Key_Left: "Left", Qt.Key_Up: "Up",
    Qt.Key_Right: "Right", Qt.Key_Down: "Down", Qt.Key_Insert: "Insert",
    Qt.Key_Delete: "Delete", Qt.Key_Pause: "Pause", Qt.Key_ScrollLock: "ScrollLock",
    Qt.Key_Semicolon: ";", Qt.Key_Equal: "=", Qt.Key_Comma: ",", Qt.Key_Minus: "-",
    Qt.Key_Period: ".", Qt.Key_Slash: "/", Qt.Key_QuoteLeft: "`",
    Qt.Key_BracketLeft: "[", Qt.Key_Backslash: "\\", Qt.Key_BracketRight: "]",
    Qt.Key_Apostrophe: "'",
}


def key_name(key: int, native_vk: int = 0) -> str:
    """Qt 키를 조합 글의 키 이름으로. 한글 자판에서 글자 키가 한글로 오면 가상 키
    번호로 영문 글자를 찾는다."""
    if Qt.Key_A <= key <= Qt.Key_Z:
        return chr(key)
    if Qt.Key_0 <= key <= Qt.Key_9:
        return chr(key)
    if Qt.Key_F1 <= key <= Qt.Key_F24:
        return f"F{key - Qt.Key_F1 + 1}"
    if key in _QT_KEYS:
        return _QT_KEYS[key]
    if 0x41 <= native_vk <= 0x5A or 0x30 <= native_vk <= 0x39:
        return chr(native_vk)
    return ""


def modifier_names(mods) -> list[str]:
    out = []
    if mods & Qt.ControlModifier:
        out.append("Ctrl")
    if mods & Qt.AltModifier:
        out.append("Alt")
    if mods & Qt.ShiftModifier:
        out.append("Shift")
    if mods & Qt.MetaModifier:
        out.append("Win")
    return out


class HotkeyField(QAbstractButton):
    """조합 하나를 받는 칸. 누르면 '누를 키'(마우스 칸은 '누를 단추')로 바뀌고, 그때
    누른 키나 단추가 들어간다. Esc 는 취소, Backspace 는 지우기."""

    changed = Signal(str)
    WIDTH = 132
    HEIGHT = 38           # 설정 창의 다른 고르는 칸과 같은 높이

    def __init__(self, mouse: bool, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.mouse = bool(mouse)
        self._value = hotkeys.normalize(text)
        self._capturing = False
        self._problem = False
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setAttribute(Qt.WA_Hover)
        self.clicked.connect(self._start)
        self._sync_name()

    def value(self) -> str:
        return self._value

    def set_value(self, text: str) -> None:
        text = hotkeys.normalize(text)
        if text != self._value:
            self._value = text
            self._sync_name()
            self.update()
            self.changed.emit(text)

    def set_problem(self, on: bool) -> None:
        """다른 프로그램이 쓰거나 겹치는 조합이면 테두리를 빨갛게."""
        if self._problem != bool(on):
            self._problem = bool(on)
            self.update()

    def capturing(self) -> bool:
        return self._capturing

    def _sync_name(self) -> None:
        self.setAccessibleName(display(self._value) or i18n.t("hotkey.none"))

    def _start(self) -> None:
        if not self._capturing:
            for keys in list(GlobalKeys._live):
                keys.hold()
        self._capturing = True
        self.setFocus(Qt.MouseFocusReason)
        self.update()

    def _stop(self) -> None:
        if self._capturing:
            for keys in list(GlobalKeys._live):
                keys.resume()
        self._capturing = False
        self.update()

    def hideEvent(self, event) -> None:
        # 받는 중에 창이 닫혀도 내려 둔 단축키를 다시 건다.
        self._stop()
        super().hideEvent(event)

    def sizeHint(self) -> QSize:
        return QSize(self.WIDTH, self.HEIGHT)

    def focusOutEvent(self, event) -> None:
        self._stop()
        super().focusOutEvent(event)

    def keyPressEvent(self, event) -> None:
        if not self._capturing:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
                self._start()
                return
            super().keyPressEvent(event)
            return
        key = event.key()
        mods = event.modifiers()
        if key == Qt.Key_Escape and not mods:
            self._stop()
            return
        if key in (Qt.Key_Backspace, Qt.Key_Delete) and not mods:
            self.set_value("")
            self._stop()
            return
        if self.mouse or key in (Qt.Key_Control, Qt.Key_Alt, Qt.Key_Shift,
                                 Qt.Key_Meta, Qt.Key_AltGr):
            return
        name = key_name(key, event.nativeVirtualKey())
        if not name:
            return
        text = "+".join(modifier_names(mods) + [name])
        if hotkeys.parse(text) is not None:
            self.set_value(text)
            self._stop()

    def mousePressEvent(self, event) -> None:
        if self._capturing and self.mouse:
            button = {Qt.BackButton: hotkeys.MOUSE_BACK,
                      Qt.ForwardButton: hotkeys.MOUSE_FORWARD,
                      Qt.MiddleButton: hotkeys.MOUSE_MIDDLE}.get(event.button())
            if button is not None:
                text = "+".join(modifier_names(event.modifiers()) + [button])
                if hotkeys.parse(text) is not None:
                    self.set_value(text)
                self._stop()
                event.accept()
                return
        super().mousePressEvent(event)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect().adjusted(1, 1, -1, -1)
        if self._capturing or self.hasFocus():
            border = QColor(COLOR_FOCUS)
        elif self._problem:
            border = QColor("#DC2626")
        else:
            border = QColor("#D4D4D4")
        p.setPen(QPen(border, 1.4 if (self._capturing or self._problem) else 1.0))
        p.setBrush(QColor("#F7F9FF") if self._capturing else QColor("#FFFFFF"))
        p.drawRoundedRect(r, 6, 6)
        if self._capturing:
            text = i18n.t("hotkey.press_button" if self.mouse else "hotkey.press_key")
            color = QColor(COLOR_FOCUS)
        elif self._value:
            text, color = display(self._value), QColor("#1F1F1F")
        else:
            text, color = i18n.t("hotkey.none"), QColor("#9A9A9A")
        p.setPen(color)
        f = p.font()
        f.setPixelSize(13)
        p.setFont(f)
        p.drawText(r.adjusted(10, 0, -8, 0), Qt.AlignVCenter | Qt.AlignLeft,
                   p.fontMetrics().elidedText(text, Qt.ElideRight, r.width() - 18))


def probe_free(keys: GlobalKeys | None, text: str) -> bool:
    """키보드 조합을 걸 수 있는가(다른 프로그램이 쓰지 않는가)."""
    if keys is None or not text:
        return True
    combo = hotkeys.parse(text)
    if combo is None or combo.is_mouse:
        return True
    return keys.keyboard.probe(text)


__all__ = ["GlobalKeys", "HotkeyField", "display", "key_name", "probe_free"]
