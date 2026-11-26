"""어디서나 쓰는 단축키: 새 메모, 메모 찾기, 클립보드로 새 메모.

일마다 키보드 조합 하나와 마우스 단추 하나를 걸 수 있다.

* 키보드는 Windows 의 ``RegisterHotKey`` 로 건다. 다른 프로그램이 이미 쓰는 조합이면
  걸리지 않는다(그 일은 ``apply`` 가 돌려주는 '사용 중' 목록에 든다).
* 마우스 단추는 Windows 가 단축키로 받아 주지 않아 저수준 마우스 훅(WH_MOUSE_LL)을
  쓴다. 훅은 따로 도는 스레드에 걸어 바로 답한다(늦게 답하면 Windows 가 훅을
  빼 버린다). 건 단추만 막고 나머지는 그대로 흘려보낸다. 마우스 단추를 하나도
  걸지 않으면 훅을 걸지 않는다.
* 고를 수 있는 마우스 단추는 뒤로, 앞으로, 가운데다. 가운데는 Ctrl, Alt, Shift
  가운데 하나와 함께일 때만 받는다(평소 누르기를 막지 않게). 왼쪽, 오른쪽 단추는
  고를 수 없다.

조합은 글로 적는다: ``"Ctrl+Alt+N"``, ``"Ctrl+XButton1"``(뒤로 단추).
"""
from __future__ import annotations

import ctypes
import sys
import threading
from dataclasses import dataclass
from typing import Callable

ACT_NEW_NOTE = "new_note"
ACT_FIND = "find"
ACT_CLIP_NOTE = "clip_note"
ACTIONS = (ACT_NEW_NOTE, ACT_FIND, ACT_CLIP_NOTE)

# 처음 깔았을 때. 마우스 단추는 비워 둔다(다른 프로그램의 뒤로 가기를 막지 않게).
DEFAULTS: dict[str, dict[str, str]] = {
    ACT_NEW_NOTE: {"key": "Ctrl+Alt+N", "mouse": ""},
    ACT_FIND: {"key": "Ctrl+Alt+F", "mouse": ""},
    ACT_CLIP_NOTE: {"key": "", "mouse": ""},
}

MODS = ("Ctrl", "Alt", "Shift", "Win")
MOUSE_BACK, MOUSE_FORWARD, MOUSE_MIDDLE = "XButton1", "XButton2", "MButton"
MOUSE_BUTTONS = (MOUSE_BACK, MOUSE_FORWARD, MOUSE_MIDDLE)

_MOD_BITS = {"Alt": 0x0001, "Ctrl": 0x0002, "Shift": 0x0004, "Win": 0x0008}
_MOD_NOREPEAT = 0x4000

# 키 이름과 가상 키 번호. 글자, 숫자, F1~F24 는 아래에서 더한다.
_NAMED_KEYS = {
    "Space": 0x20, "PageUp": 0x21, "PageDown": 0x22, "End": 0x23, "Home": 0x24,
    "Left": 0x25, "Up": 0x26, "Right": 0x27, "Down": 0x28, "Insert": 0x2D,
    "Delete": 0x2E, "Pause": 0x13, "ScrollLock": 0x91,
    ";": 0xBA, "=": 0xBB, ",": 0xBC, "-": 0xBD, ".": 0xBE, "/": 0xBF, "`": 0xC0,
    "[": 0xDB, "\\": 0xDC, "]": 0xDD, "'": 0xDE,
}
_VK: dict[str, int] = dict(_NAMED_KEYS)
_VK.update({chr(c): c for c in range(ord("A"), ord("Z") + 1)})
_VK.update({chr(c): c for c in range(ord("0"), ord("9") + 1)})
_VK.update({f"F{n}": 0x6F + n for n in range(1, 25)})


@dataclass(frozen=True)
class Combo:
    """조합 하나. ``key`` 는 키 이름(``"N"``, ``"F5"``)이나 마우스 단추 이름이다."""
    mods: frozenset
    key: str

    @property
    def is_mouse(self) -> bool:
        return self.key in MOUSE_BUTTONS

    def text(self) -> str:
        return "+".join([m for m in MODS if m in self.mods] + [self.key])

    def mod_bits(self) -> int:
        bits = 0
        for m in self.mods:
            bits |= _MOD_BITS[m]
        return bits


def parse(text: str) -> Combo | None:
    """적어 둔 조합을 읽는다. 쓸 수 없는 조합이면 None."""
    parts = [p.strip() for p in (text or "").split("+")]
    if not parts or not parts[-1]:
        # '+' 키 자체는 다루지 않는다.
        return None
    key = parts[-1]
    mods = []
    for p in parts[:-1]:
        name = {"control": "Ctrl", "ctrl": "Ctrl", "alt": "Alt", "shift": "Shift",
                "win": "Win", "meta": "Win"}.get(p.lower())
        if name is None or name in mods:
            return None
        mods.append(name)
    if len(key) == 1:
        key = key.upper()
    elif key.upper().startswith("F") and key[1:].isdigit():
        key = key.upper()
    combo = Combo(frozenset(mods), key)
    return combo if valid(combo) else None


def valid(combo: Combo) -> bool:
    """걸 수 있는 조합인가. 키보드는 Ctrl, Alt, Win 가운데 하나가 있어야 한다(글자
    하나만으로는 평소 입력을 막는다). F 키는 혼자도 된다. 가운데 단추는 조합으로만."""
    if combo.is_mouse:
        if combo.key == MOUSE_MIDDLE:
            return bool(combo.mods - {"Win"})
        return True
    if combo.key not in _VK:
        return False
    if combo.key.startswith("F") and combo.key[1:].isdigit():
        return True
    return bool(combo.mods & {"Ctrl", "Alt", "Win"})


def normalize(text: str) -> str:
    """조합 글을 늘 같은 꼴로(읽을 수 없으면 빈 글)."""
    combo = parse(text)
    return combo.text() if combo else ""


def clean_bindings(raw) -> dict[str, dict[str, str]]:
    """설정 파일에서 읽은 값을 고친다. 모르는 일은 버리고, 칸마다 맞는 종류만 남긴다."""
    out = {a: dict(DEFAULTS[a]) for a in ACTIONS}
    if not isinstance(raw, dict):
        return out
    for action in ACTIONS:
        entry = raw.get(action)
        if not isinstance(entry, dict):
            continue
        for slot in ("key", "mouse"):
            combo = parse(str(entry.get(slot) or ""))
            ok = combo is not None and combo.is_mouse == (slot == "mouse")
            out[action][slot] = combo.text() if ok else ""
    return out


def duplicates(bindings: dict) -> set[str]:
    """두 일에 같은 조합이 걸려 있으면 그 조합들."""
    seen: dict[str, str] = {}
    dup: set[str] = set()
    for action in ACTIONS:
        for slot in ("key", "mouse"):
            text = normalize((bindings.get(action) or {}).get(slot, ""))
            if not text:
                continue
            if text in seen and seen[text] != action:
                dup.add(text)
            seen[text] = action
    return dup


def vk_of(combo: Combo) -> int | None:
    return None if combo.is_mouse else _VK.get(combo.key)


# ── 마우스 단추 맞추기(훅 스레드가 부른다. 순수 함수라 시험한다) ──

def match_mouse(button: str, mods: frozenset, bindings: dict[Combo, str]) -> str | None:
    """눌린 단추와 그때 눌려 있던 조합 키로 걸린 일을 찾는다. 조합 키는 정확히
    같아야 한다(Ctrl+뒤로 를 걸었으면 뒤로 단추만으로는 일이 되지 않는다)."""
    return bindings.get(Combo(frozenset(mods), button))


# ── Windows ──

_WM_HOTKEY = 0x0312
_WM_QUIT = 0x0012
_WH_MOUSE_LL = 14
_WM_MBUTTONDOWN, _WM_MBUTTONUP = 0x0207, 0x0208
_WM_XBUTTONDOWN, _WM_XBUTTONUP = 0x020B, 0x020C
_LLMHF_INJECTED = 0x00000001
_ERROR_HOTKEY_ALREADY_REGISTERED = 1409
_HOTKEY_BASE = 0x5A10         # 이 앱이 쓰는 단축키 번호의 시작


def is_windows() -> bool:
    return sys.platform == "win32"


class KeyboardHotkeys:
    """키보드 조합을 Windows 에 건다. 눌리면 메시지(WM_HOTKEY)가 이 스레드로 오고,
    ``handle_message`` 가 일을 찾는다. ``register``/``unregister`` 는 시험에서
    바꿔 끼운다."""

    def __init__(self) -> None:
        self._ids: dict[int, str] = {}         # 번호 → 일
        self._texts: dict[str, int] = {}       # 조합 글 → 번호

    @staticmethod
    def register(hotkey_id: int, mods: int, vk: int) -> int:
        """걸었으면 0, 못 걸었으면 Windows 의 오류 번호."""
        if not is_windows():
            return 0
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        ok = user32.RegisterHotKey(None, hotkey_id, mods | _MOD_NOREPEAT, vk)
        return 0 if ok else (ctypes.get_last_error() or _ERROR_HOTKEY_ALREADY_REGISTERED)

    @staticmethod
    def unregister(hotkey_id: int) -> None:
        if is_windows():
            ctypes.windll.user32.UnregisterHotKey(None, hotkey_id)

    def apply(self, bindings: dict[str, str]) -> list[str]:
        """``{일: 조합 글}`` 을 건다. 다른 프로그램이 써서 못 건 조합 글을 돌려준다."""
        self.clear()
        busy: list[str] = []
        for n, action in enumerate(ACTIONS):
            combo = parse(bindings.get(action, ""))
            if combo is None or combo.is_mouse:
                continue
            hotkey_id = _HOTKEY_BASE + n
            if self.register(hotkey_id, combo.mod_bits(), vk_of(combo)) != 0:
                busy.append(combo.text())
                continue
            self._ids[hotkey_id] = action
            self._texts[combo.text()] = hotkey_id
        return busy

    def holds(self, text: str) -> bool:
        """이 조합을 이미 이 앱이 걸어 두었는가."""
        return normalize(text) in self._texts

    def probe(self, text: str) -> bool:
        """이 조합을 걸 수 있는가(다른 프로그램이 쓰지 않는가). 이 앱이 이미 건
        조합이면 참. 잠깐 걸어 보고 바로 푼다."""
        combo = parse(text)
        if combo is None or combo.is_mouse:
            return True
        if combo.text() in self._texts:
            return True
        probe_id = _HOTKEY_BASE + 0x40
        if self.register(probe_id, combo.mod_bits(), vk_of(combo)) != 0:
            return False
        self.unregister(probe_id)
        return True

    def clear(self) -> None:
        for hotkey_id in list(self._ids):
            self.unregister(hotkey_id)
        self._ids.clear()
        self._texts.clear()

    def action_for(self, hotkey_id: int) -> str | None:
        return self._ids.get(int(hotkey_id))

    def handle_message(self, message: int, wparam: int) -> str | None:
        """Windows 메시지 하나. 이 앱의 단축키면 그 일."""
        if message != _WM_HOTKEY:
            return None
        return self.action_for(wparam)


class MouseHook:
    """마우스 보조 단추를 받는 저수준 훅. 따로 도는 스레드에 걸고 그 스레드가
    메시지를 돌린다. ``notify(일)`` 은 훅 스레드에서 불리므로 Qt 쪽으로는 신호로
    넘겨야 한다(바로 답해야 해서 그 밖의 일은 하지 않는다)."""

    def __init__(self, notify: Callable[[str], None]) -> None:
        self._notify = notify
        self._bindings: dict[Combo, str] = {}
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._swallowed: set[str] = set()     # 누름을 막은 단추(뗌도 막는다)
        self._proc = None
        self._hook = None

    def apply(self, bindings: dict[str, str]) -> None:
        """``{일: 마우스 조합 글}``. 하나도 없으면 훅을 뗀다."""
        table: dict[Combo, str] = {}
        for action, text in bindings.items():
            combo = parse(text)
            if combo is not None and combo.is_mouse:
                table[combo] = action
        self._bindings = table
        if table and self._thread is None and is_windows():
            self._start()
        elif not table:
            self.stop()

    def active(self) -> bool:
        return self._thread is not None

    def stop(self) -> None:
        thread, self._thread = self._thread, None
        if thread is None:
            return
        if self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, _WM_QUIT, 0, 0)
        thread.join(timeout=2.0)
        self._thread_id = 0
        self._ready.clear()

    def _start(self) -> None:
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="memil-mouse-hook",
                                        daemon=True)
        self._thread.start()
        self._ready.wait(timeout=2.0)

    def _current_mods(self) -> frozenset:
        state = ctypes.windll.user32.GetAsyncKeyState
        mods = set()
        if state(0x11) & 0x8000:
            mods.add("Ctrl")
        if state(0x12) & 0x8000:
            mods.add("Alt")
        if state(0x10) & 0x8000:
            mods.add("Shift")
        if (state(0x5B) | state(0x5C)) & 0x8000:
            mods.add("Win")
        return frozenset(mods)

    def _run(self) -> None:
        from ctypes import wintypes
        # 따로 연 user32: 인자 형식을 앱의 다른 곳과 나눠 쓰지 않는다.
        user32 = ctypes.WinDLL("user32")
        kernel32 = ctypes.windll.kernel32
        self._thread_id = kernel32.GetCurrentThreadId()

        class MSLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD),
                        ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("dwExtraInfo", ctypes.c_size_t)]

        LRESULT = ctypes.c_ssize_t
        proc_type = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM,
                                       wintypes.LPARAM)
        user32.CallNextHookEx.restype = LRESULT
        user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int,
                                          wintypes.WPARAM, wintypes.LPARAM]
        user32.SetWindowsHookExW.restype = wintypes.HHOOK
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, proc_type,
                                             wintypes.HINSTANCE, wintypes.DWORD]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE

        def callback(code, wparam, lparam):
            try:
                if code == 0 and wparam in (_WM_XBUTTONDOWN, _WM_XBUTTONUP,
                                            _WM_MBUTTONDOWN, _WM_MBUTTONUP):
                    info = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                    if not info.flags & _LLMHF_INJECTED:
                        if wparam in (_WM_MBUTTONDOWN, _WM_MBUTTONUP):
                            button = MOUSE_MIDDLE
                        else:
                            button = MOUSE_BACK if (info.mouseData >> 16) == 1 else MOUSE_FORWARD
                        down = wparam in (_WM_XBUTTONDOWN, _WM_MBUTTONDOWN)
                        if down:
                            action = match_mouse(button, self._current_mods(), self._bindings)
                            if action is not None:
                                self._swallowed.add(button)
                                self._notify(action)
                                return 1
                        elif button in self._swallowed:
                            self._swallowed.discard(button)
                            return 1
            except Exception:
                pass
            return user32.CallNextHookEx(None, code, wparam, lparam)

        self._proc = proc_type(callback)
        self._hook = user32.SetWindowsHookExW(_WH_MOUSE_LL, self._proc,
                                              kernel32.GetModuleHandleW(None), 0)
        self._ready.set()
        if not self._hook:
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        user32.UnhookWindowsHookEx(self._hook)
        self._hook = None
