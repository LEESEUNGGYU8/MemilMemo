"""Windows 가 창 둘레에 덧그리는 테두리와 그림자를 다룬다.

Windows 11 은 프레임 없는 창도 모서리를 둥글리고 그림자와 테두리를 두른다.
내용보다 큰 투명한 창(바탕화면 아이콘, 알림)에서는 그것이 내용을 감싼 반투명한
판으로 보인다. 다른 운영체제에서는 아무 일도 하지 않는다."""
from __future__ import annotations

import ctypes
import platform

_CORNER_PREFERENCE = 33      # DWMWA_WINDOW_CORNER_PREFERENCE
_CORNER_DEFAULT = 0          # DWMWCP_DEFAULT
_CORNER_SQUARE = 1           # DWMWCP_DONOTROUND
_BORDER_COLOR = 34           # DWMWA_BORDER_COLOR
_COLOR_NONE = 0xFFFFFFFE     # DWMWA_COLOR_NONE


def _set(hwnd: int, attribute: int, value: int) -> None:
    if platform.system() != "Windows" or not hwnd:
        return
    try:
        data = ctypes.c_uint32(value)
        ctypes.WinDLL("dwmapi").DwmSetWindowAttribute(
            ctypes.c_void_p(hwnd), attribute,
            ctypes.byref(data), ctypes.sizeof(data))
    except Exception:
        pass


def hide_border(hwnd: int) -> None:
    """창 둘레의 한 줄 테두리를 없앤다. 그림자는 그대로 둔다."""
    _set(hwnd, _BORDER_COLOR, _COLOR_NONE)


def set_bare(hwnd: int, bare: bool = True) -> None:
    """``bare`` 면 Windows 가 창에 두르는 그림자와 테두리를 모두 없앤다(모서리를
    둥글리지 않게 하면 함께 사라진다). 아니면 기본으로 되돌린다."""
    _set(hwnd, _CORNER_PREFERENCE, _CORNER_SQUARE if bare else _CORNER_DEFAULT)
    if bare:
        hide_border(hwnd)
