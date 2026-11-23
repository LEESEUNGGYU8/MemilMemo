"""Windows 바탕화면의 아이콘 격자를 읽는다(화면 코드가 없는 순수 Windows 호출).

접힌 메모와 메모 폴더를 바탕화면 아이콘 모양으로 둘 때, 진짜 바탕화면 아이콘과
같은 크기, 같은 칸에 놓이게 하려는 것이다. 사용자가 아이콘 크기를 바꾸면(바탕
화면에서 Ctrl + 휠) 칸의 크기도 함께 달라지므로 그때마다 다시 읽는다.

탐색기의 바탕화면 보기에 공식 인터페이스(IFolderView2)로 묻는다. 항목 하나가
차지하는 칸은 접근성 인터페이스(IAccessible)로 묻는다. 다른 프로세스의 메모리를
들여다보지 않는다. 값은 모두 물리 픽셀이고 화면 좌표다.

읽지 못하면(탐색기가 떠 있지 않다, 바탕화면 아이콘을 숨겼다) None 을 돌려주고,
부르는 쪽은 기본 격자를 쓴다."""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from dataclasses import dataclass

_S_OK = 0
_COINIT_APARTMENT = 0x2
_CLSCTX_LOCAL_SERVER = 0x4
_VT_I4, _VT_EMPTY = 3, 0
_CSIDL_DESKTOP = 0
_SWC_DESKTOP = 0x8
_SWFO_NEEDDISPATCH = 0x1
_SVGIO_ALLVIEW = 0x2
_FWF_AUTOARRANGE, _FWF_SNAPTOGRID = 0x1, 0x4
_FVM_ICON = 1
_LVM_GETITEMSPACING = 0x1000 + 51
_OBJID_CLIENT = 0xFFFFFFFC
# 칸의 자리를 잴 때 물어보는 항목 수(모두 같아야 정상이다. 가장 흔한 값을 쓴다).
_OFFSET_SAMPLES = 5
_SMTO_ABORTIFHUNG = 0x2
# 탐색기가 답하지 않을 때 화면이 멈추지 않도록 짧게만 기다린다.
_ASK_TIMEOUT_MS = 80
# 항목의 자리는 이만큼까지만 읽는다(바탕화면에 파일이 수천 개인 경우).
_MAX_ITEMS = 600


class _GUID(ctypes.Structure):
    _fields_ = [("a", ctypes.c_uint32), ("b", ctypes.c_uint16),
                ("c", ctypes.c_uint16), ("d", ctypes.c_ubyte * 8)]

    @classmethod
    def of(cls, text: str) -> "_GUID":
        p = text.strip("{}").split("-")
        tail = bytes.fromhex(p[3] + p[4])
        return cls(int(p[0], 16), int(p[1], 16), int(p[2], 16),
                   (ctypes.c_ubyte * 8)(*tail))


class _VARIANT(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort),
                ("r2", ctypes.c_ushort), ("r3", ctypes.c_ushort),
                ("value", ctypes.c_longlong), ("more", ctypes.c_void_p)]


# 식별값과 가상 함수표의 자리는 Windows SDK 의 ExDisp.h, ShObjIdl_core.h 에서 옮겼다.
_CLSID_SHELL_WINDOWS = _GUID.of("9BA05972-F6A8-11CF-A442-00A0C90A8F39")
_IID_SHELL_WINDOWS = _GUID.of("85CB6900-4D95-11CF-960C-0080C7F4EE85")
_IID_SERVICE_PROVIDER = _GUID.of("6D5140C1-7436-11CE-8034-00AA006009FA")
_SID_TOP_LEVEL_BROWSER = _GUID.of("4C96BE40-915C-11CF-99D3-00AA004AE837")
_IID_SHELL_BROWSER = _GUID.of("000214E2-0000-0000-C000-000000000046")
_IID_FOLDER_VIEW2 = _GUID.of("1AF3A467-214F-4298-908E-06B03E0B39F9")
_IID_ACCESSIBLE = _GUID.of("618736E0-3C3D-11CF-810C-00AA00389B71")

_QUERY_INTERFACE, _RELEASE = 0, 2
_WINDOWS_FIND = 15                  # IShellWindows.FindWindowSW
_PROVIDER_QUERY_SERVICE = 3         # IServiceProvider.QueryService
_BROWSER_ACTIVE_VIEW = 15           # IShellBrowser.QueryActiveShellView
_VIEW_GET_WINDOW = 3                # IShellView(IOleWindow).GetWindow
_FV_ITEM, _FV_ITEM_COUNT = 6, 7     # IFolderView.Item, ItemCount
_FV_ITEM_POSITION, _FV_SPACING = 11, 12
_FV_FOLDER_FLAGS = 25               # IFolderView2.GetCurrentFolderFlags
_FV_MODE_AND_ICON = 36              # IFolderView2.GetViewModeAndIconSize
_ACC_LOCATION = 22                  # IAccessible.accLocation (oleacc.h)


@dataclass(frozen=True)
class DesktopView:
    """바탕화면 아이콘 보기의 지금 상태(물리 픽셀, 화면 좌표)."""
    icon: int                             # 아이콘 그림의 한 변
    spacing: tuple[int, int]              # 격자 한 칸의 폭과 높이
    items: tuple[tuple[int, int], ...]    # 놓여 있는 항목들의 자리(칸의 왼쪽 위)
    snap: bool                            # '아이콘을 그리드에 맞춤'
    window: int = 0                       # 아이콘 목록 창(가벼운 물음에 쓴다)
    dpi: int = 96                         # 그 창의 DPI(96 이 100% 배율)
    # 항목의 자리에서 그 항목이 차지하는 칸의 왼쪽 위까지. 바탕화면에 직접 물어 잰
    # 값이다. 잴 수 없었으면(항목이 없다) None 이고, 쓰는 쪽이 어림한다.
    offset: tuple[int, int] | None = None


class _Failed(Exception):
    pass


def _method(ptr, index: int, *argtypes):
    vtable = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))
    proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)
    func = proto(vtable.contents[index])
    return lambda *args: func(ptr, *args)


def _check(hr: int, what: str) -> None:
    if hr != _S_OK:
        raise _Failed(f"{what}: {hr & 0xFFFFFFFF:#010x}")


def _release(ptr) -> None:
    if ptr:
        try:
            _method(ptr, _RELEASE)()
        except Exception:
            pass


def _query(ptr, iid: _GUID, what: str) -> ctypes.c_void_p:
    out = ctypes.c_void_p()
    _check(_method(ptr, _QUERY_INTERFACE, ctypes.POINTER(_GUID),
                   ctypes.POINTER(ctypes.c_void_p))(
        ctypes.byref(iid), ctypes.byref(out)), what)
    return out


def _list_window(view_window: int) -> int:
    """보기 창(SHELLDLL_DefView) 안의 아이콘 목록 창."""
    user32 = ctypes.windll.user32
    user32.FindWindowExW.restype = wintypes.HWND
    user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND,
                                     wintypes.LPCWSTR, wintypes.LPCWSTR]
    return int(user32.FindWindowExW(view_window, None, "SysListView32", None) or 0)


def _window_origin(hwnd: int) -> tuple[int, int]:
    rect = wintypes.RECT()
    if hwnd and ctypes.windll.user32.GetWindowRect(wintypes.HWND(hwnd),
                                                   ctypes.byref(rect)):
        return rect.left, rect.top
    return 0, 0


def _dpi_of(hwnd: int) -> int:
    try:
        dpi = int(ctypes.windll.user32.GetDpiForWindow(wintypes.HWND(hwnd)))
    except (AttributeError, OSError):
        dpi = 0
    return dpi if dpi > 0 else 96


def _folder_view() -> tuple[ctypes.c_void_p, int]:
    """바탕화면의 IFolderView2 와 그 아이콘 목록 창. 쓰고 나면 놓아 준다."""
    ole32 = ctypes.windll.ole32
    ole32.CoCreateInstance.argtypes = [
        ctypes.POINTER(_GUID), ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)]
    windows = ctypes.c_void_p()
    dispatch = ctypes.c_void_p()
    provider = ctypes.c_void_p()
    browser = ctypes.c_void_p()
    view = ctypes.c_void_p()
    try:
        _check(ole32.CoCreateInstance(
            ctypes.byref(_CLSID_SHELL_WINDOWS), None, _CLSCTX_LOCAL_SERVER,
            ctypes.byref(_IID_SHELL_WINDOWS), ctypes.byref(windows)),
            "ShellWindows")
        where = _VARIANT(vt=_VT_I4, value=_CSIDL_DESKTOP)
        empty = _VARIANT(vt=_VT_EMPTY)
        hwnd = ctypes.c_long(0)
        _check(_method(windows, _WINDOWS_FIND, ctypes.POINTER(_VARIANT),
                       ctypes.POINTER(_VARIANT), ctypes.c_int,
                       ctypes.POINTER(ctypes.c_long), ctypes.c_int,
                       ctypes.POINTER(ctypes.c_void_p))(
            ctypes.byref(where), ctypes.byref(empty), _SWC_DESKTOP,
            ctypes.byref(hwnd), _SWFO_NEEDDISPATCH, ctypes.byref(dispatch)),
            "FindWindowSW")
        if not dispatch:
            raise _Failed("no desktop window")
        provider = _query(dispatch, _IID_SERVICE_PROVIDER, "IServiceProvider")
        _check(_method(provider, _PROVIDER_QUERY_SERVICE, ctypes.POINTER(_GUID),
                       ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p))(
            ctypes.byref(_SID_TOP_LEVEL_BROWSER), ctypes.byref(_IID_SHELL_BROWSER),
            ctypes.byref(browser)), "IShellBrowser")
        _check(_method(browser, _BROWSER_ACTIVE_VIEW,
                       ctypes.POINTER(ctypes.c_void_p))(ctypes.byref(view)),
               "QueryActiveShellView")
        view_window = wintypes.HWND()
        _method(view, _VIEW_GET_WINDOW, ctypes.POINTER(wintypes.HWND))(
            ctypes.byref(view_window))
        folder = _query(view, _IID_FOLDER_VIEW2, "IFolderView2")
        return folder, _list_window(view_window.value or 0)
    finally:
        for ptr in (view, browser, provider, dispatch, windows):
            _release(ptr)


def read() -> DesktopView | None:
    """바탕화면 아이콘 보기에 지금 상태를 묻는다. 물을 수 없으면 None."""
    if sys.platform != "win32":
        return None
    folder = None
    # 화면 프로그램에서는 Qt 가 이미 COM 을 쓰게 해 두었다. 그렇지 않은 곳(테스트,
    # 다른 스레드)에서 불러도 되게 여기서도 짝을 맞춰 부른다.
    started = ctypes.windll.ole32.CoInitializeEx(None, _COINIT_APARTMENT)
    try:
        folder, window = _folder_view()
        mode, icon = ctypes.c_int(0), ctypes.c_int(0)
        _check(_method(folder, _FV_MODE_AND_ICON, ctypes.POINTER(ctypes.c_int),
                       ctypes.POINTER(ctypes.c_int))(
            ctypes.byref(mode), ctypes.byref(icon)), "GetViewModeAndIconSize")
        spacing = wintypes.POINT()
        _check(_method(folder, _FV_SPACING, ctypes.POINTER(wintypes.POINT))(
            ctypes.byref(spacing)), "GetSpacing")
        if icon.value <= 0 or spacing.x <= 0 or spacing.y <= 0:
            return None
        flags = wintypes.DWORD(0)
        _method(folder, _FV_FOLDER_FLAGS, ctypes.POINTER(wintypes.DWORD))(
            ctypes.byref(flags))
        left, top = _window_origin(window)
        found = [(index, x + left, y + top) for index, x, y in _positions(folder)]
        return DesktopView(
            icon=int(icon.value), spacing=(int(spacing.x), int(spacing.y)),
            items=tuple((x, y) for _index, x, y in found),
            snap=bool(flags.value & (_FWF_SNAPTOGRID | _FWF_AUTOARRANGE)),
            window=window, dpi=_dpi_of(window),
            offset=_cell_offset(window, found[:_OFFSET_SAMPLES],
                                (int(spacing.x), int(spacing.y))))
    except (_Failed, OSError, ValueError):
        return None
    finally:
        _release(folder)
        if started in (0, 1):               # S_OK, S_FALSE: 부른 만큼 돌려준다
            ctypes.windll.ole32.CoUninitialize()


def _cell_offset(window: int, samples: list[tuple[int, int, int]],
                 spacing: tuple[int, int]) -> tuple[int, int] | None:
    """항목의 자리에서 그 항목이 차지하는 칸의 왼쪽 위까지의 거리.

    목록 창이 알려 주는 '자리'는 그림의 왼쪽 위 언저리이고, 칸은 거기서 조금 떨어져
    있다. 그 거리는 아이콘 크기와 배율에 따라 달라질 수 있어 어림하지 않고 잰다:
    화면 낭독기가 쓰는 접근성 인터페이스가 항목마다의 사각형을 화면 좌표로 준다.
    ``samples`` 는 (항목 번호, 화면 x, 화면 y) 다."""
    if not window or not samples:
        return None
    acc = ctypes.c_void_p()
    try:
        oleacc = ctypes.windll.oleacc
        oleacc.AccessibleObjectFromWindow.argtypes = [
            wintypes.HWND, wintypes.DWORD, ctypes.POINTER(_GUID),
            ctypes.POINTER(ctypes.c_void_p)]
        if oleacc.AccessibleObjectFromWindow(
                wintypes.HWND(window), _OBJID_CLIENT,
                ctypes.byref(_IID_ACCESSIBLE), ctypes.byref(acc)) != _S_OK or not acc:
            return None
        location = _method(
            acc, _ACC_LOCATION, ctypes.POINTER(ctypes.c_long),
            ctypes.POINTER(ctypes.c_long), ctypes.POINTER(ctypes.c_long),
            ctypes.POINTER(ctypes.c_long), _VARIANT)
        cx, cy = spacing
        seen: dict[tuple[int, int], int] = {}
        for index, x, y in samples:
            left, top, width, height = (ctypes.c_long(0) for _ in range(4))
            if location(ctypes.byref(left), ctypes.byref(top), ctypes.byref(width),
                        ctypes.byref(height),
                        _VARIANT(vt=_VT_I4, value=index + 1)) != _S_OK:
                continue
            dx, dy = left.value - x, top.value - y
            # 칸 하나의 폭이고, 자리에서 한 칸 안쪽에 있어야 믿는다(이름을 고르는
            # 중이거나 번호가 어긋났으면 다른 값이 나온다).
            if width.value == cx and -cx < dx <= 0 and -cy < dy < cy:
                seen[(dx, dy)] = seen.get((dx, dy), 0) + 1
        if not seen:
            return None
        return max(seen.items(), key=lambda kv: kv[1])[0]
    except (OSError, AttributeError, ValueError):
        return None
    finally:
        _release(acc)


def _positions(folder) -> list[tuple[int, int, int]]:
    """놓여 있는 항목들의 번호와 자리(목록 창 안의 좌표)."""
    count = ctypes.c_int(0)
    if _method(folder, _FV_ITEM_COUNT, ctypes.c_uint,
               ctypes.POINTER(ctypes.c_int))(
            _SVGIO_ALLVIEW, ctypes.byref(count)) != _S_OK:
        return []
    item = _method(folder, _FV_ITEM, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))
    position = _method(folder, _FV_ITEM_POSITION, ctypes.c_void_p,
                       ctypes.POINTER(wintypes.POINT))
    free = ctypes.windll.ole32.CoTaskMemFree
    free.argtypes = [ctypes.c_void_p]
    out: list[tuple[int, int, int]] = []
    for index in range(min(count.value, _MAX_ITEMS)):
        pidl = ctypes.c_void_p()
        if item(index, ctypes.byref(pidl)) != _S_OK or not pidl:
            continue
        try:
            point = wintypes.POINT()
            if position(pidl, ctypes.byref(point)) == _S_OK:
                out.append((index, int(point.x), int(point.y)))
        finally:
            free(pidl)
    return out


def spacing_of(window: int) -> tuple[int, int] | None:
    """아이콘 목록 창에 칸의 크기만 가볍게 묻는다. 크기가 바뀌었는지 자주 볼 때
    쓴다(탐색기가 답하지 않으면 곧 그만둔다)."""
    if sys.platform != "win32" or not window:
        return None
    user32 = ctypes.windll.user32
    user32.SendMessageTimeoutW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
        wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
    result = ctypes.c_size_t(0)
    if not user32.SendMessageTimeoutW(
            wintypes.HWND(window), _LVM_GETITEMSPACING, 0, 0,
            _SMTO_ABORTIFHUNG, _ASK_TIMEOUT_MS, ctypes.byref(result)):
        return None
    value = int(result.value)
    cx, cy = value & 0xFFFF, (value >> 16) & 0xFFFF
    return (cx, cy) if cx > 0 and cy > 0 else None


__all__ = ["DesktopView", "read", "spacing_of"]
