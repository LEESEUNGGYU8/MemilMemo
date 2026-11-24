"""Windows 바탕화면에서 사용자가 마우스로 누르고 끄는 것을 알아챈다.

접힌 메모와 메모 폴더의 아이콘은 진짜 바탕화면 아이콘 사이에 놓인다. 사용자가
바탕화면 빈 곳을 끌어 여러 아이콘을 고를 때 우리 아이콘도 함께 고르려면, 그
사각형이 어디에서 시작됐는지 알아야 한다.

탐색기의 바탕화면 아이콘 목록 창은 눌릴 때마다 마우스를 잡는다. Windows 는 창이
마우스를 잡았다는 것을 접근성 이벤트(EVENT_SYSTEM_CAPTURESTART)로 알려 준다.
그 이벤트만 듣는다. 프로세스 밖에서 듣는 방식이라 탐색기에 아무것도 끼워 넣지
않고, 마우스나 키보드 입력을 가로채지도 않는다. 누른 뒤 1~10ms 안에 오고, 그때의
마우스 자리가 곧 누른 자리다(2026-11-23 에 이 PC 에서 쟀다).

한 번 누를 때 이벤트가 두세 번 잇달아 온다(누름, 끌기 시작 판정, 사각형). 쓰는
쪽은 단추가 눌린 동안의 첫 이벤트만 누름으로 본다. 값은 모두 물리 픽셀이다."""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Callable

_EVENT_SYSTEM_CAPTURESTART = 0x0008
_WINEVENT_OUTOFCONTEXT = 0x0000
_VK_LBUTTON, _VK_RBUTTON = 0x01, 0x02
_VK_SHIFT, _VK_CONTROL = 0x10, 0x11
_SM_SWAPBUTTON, _SM_CXDRAG, _SM_CYDRAG = 23, 68, 69

if sys.platform == "win32":
    _WinEventProc = ctypes.WINFUNCTYPE(
        None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND, wintypes.LONG,
        wintypes.LONG, wintypes.DWORD, wintypes.DWORD)
else:                                   # 다른 곳에서는 아무 일도 하지 않는다
    _WinEventProc = None


def _user32():
    return ctypes.windll.user32


def cursor_pos() -> tuple[int, int]:
    if sys.platform != "win32":
        return 0, 0
    point = wintypes.POINT()
    _user32().GetCursorPos(ctypes.byref(point))
    return int(point.x), int(point.y)


def _held(vk: int) -> bool:
    return bool(_user32().GetAsyncKeyState(vk) & 0x8000)


def _swapped() -> bool:
    return bool(_user32().GetSystemMetrics(_SM_SWAPBUTTON))


def primary_down() -> bool:
    """고르는 단추(보통 왼쪽)가 눌려 있는가."""
    if sys.platform != "win32":
        return False
    return _held(_VK_RBUTTON if _swapped() else _VK_LBUTTON)


def secondary_down() -> bool:
    if sys.platform != "win32":
        return False
    return _held(_VK_LBUTTON if _swapped() else _VK_RBUTTON)


def adding_keys() -> bool:
    """고른 것에 더하는 글쇠(Ctrl, Shift)가 눌려 있는가."""
    if sys.platform != "win32":
        return False
    return _held(_VK_CONTROL) or _held(_VK_SHIFT)


def shift_down() -> bool:
    if sys.platform != "win32":
        return False
    return _held(_VK_SHIFT)


def drag_threshold() -> int:
    """이만큼 움직여야 끄는 것으로 본다(Windows 의 설정)."""
    if sys.platform != "win32":
        return 4
    user32 = _user32()
    return max(1, int(user32.GetSystemMetrics(_SM_CXDRAG)),
               int(user32.GetSystemMetrics(_SM_CYDRAG)))


class CaptureWatch:
    """창 하나가 마우스를 잡을 때마다 ``on_capture()`` 를 부른다."""

    def __init__(self, on_capture: Callable[[], None]):
        self._on_capture = on_capture
        self._window = 0
        self._hook = None
        self._proc = None       # 걸어 둔 동안 살아 있어야 한다

    def window(self) -> int:
        return self._window

    def start(self, window: int) -> bool:
        """``window`` (바탕화면 아이콘 목록 창)를 듣기 시작한다."""
        self.stop()
        if sys.platform != "win32" or not window:
            return False
        user32 = _user32()
        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        pid = wintypes.DWORD(0)
        thread = user32.GetWindowThreadProcessId(wintypes.HWND(window),
                                                 ctypes.byref(pid))
        if not thread or not pid.value:
            return False

        def proc(_hook, event, hwnd, _obj, _child, _thread, _time):
            if event == _EVENT_SYSTEM_CAPTURESTART and int(hwnd or 0) == self._window:
                try:
                    self._on_capture()
                except Exception:
                    pass        # 이벤트를 받는 자리에서 예외를 밖으로 내지 않는다

        self._proc = _WinEventProc(proc)
        user32.SetWinEventHook.argtypes = [
            wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE, _WinEventProc,
            wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]
        user32.SetWinEventHook.restype = wintypes.HANDLE
        hook = user32.SetWinEventHook(
            _EVENT_SYSTEM_CAPTURESTART, _EVENT_SYSTEM_CAPTURESTART, None,
            self._proc, pid.value, thread, _WINEVENT_OUTOFCONTEXT)
        if not hook:
            self._proc = None
            return False
        self._hook, self._window = hook, int(window)
        return True

    def stop(self) -> None:
        if self._hook:
            try:
                user32 = _user32()
                user32.UnhookWinEvent.argtypes = [wintypes.HANDLE]
                user32.UnhookWinEvent(self._hook)
            except (OSError, AttributeError):
                pass
        self._hook, self._proc, self._window = None, None, 0


__all__ = ["CaptureWatch", "adding_keys", "cursor_pos", "drag_threshold",
           "primary_down", "secondary_down", "shift_down"]
