"""바탕화면에서 고른 진짜 아이콘을 읽고, 풀고, 옮긴다. 키보드가 바탕화면에 있는지도 본다.

메모 아이콘과 메모 폴더 아이콘은 진짜 바탕화면 아이콘 사이에 놓이고 함께 골라진다.
둘을 한 묶음처럼 다루려면 탐색기 쪽에 고른 것이 무엇인지 알아야 하고, 메모
아이콘만 눌렀을 때는 진짜 아이콘의 선택을 풀어야 한다(그러지 않으면 Delete 가
남아 있던 진짜 파일까지 지운다). 메모 아이콘을 잡고 끌 때는 함께 고른 진짜
아이콘도 같은 만큼 옮긴다.

하는 일은 사용자가 바탕화면에서 손으로 하는 것과 같은 것뿐이다: 고른 것 풀기,
고르기, 아이콘 자리 옮기기, 바탕화면을 앞으로 부르기. 파일을 스스로 지우거나 열지
않는다. Delete 는 탐색기가 사용자의 글쇠를 받아 스스로 한다. 하나만 다르다: 사용자가
메모 아이콘을 잡고 함께 고른 파일까지 Windows 휴지통 아이콘에 끌어다 놓았을 때는
탐색기에 '고른 것을 지워 달라'고 청한다(``delete_selected``). 지우는 것은 탐색기이고,
바탕화면에서 Delete 를 누른 것과 같다(휴지통으로 가고, 탐색기가 묻도록 해 두었으면
묻는다).

읽는 것은 아이콘 목록 창에 짧은 기다림을 두고 묻는다(탐색기가 바쁘면 곧 그만둔다).
바꾸는 것은 탐색기의 공식 인터페이스(IShellView, IFolderView)로 한다. 탐색기가
마우스를 쥐고 있는 동안(끄는 중)에는 부르지 않는다. 값은 물리 픽셀이다."""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from . import desktop_icons as di

_LVM_FIRST = 0x1000
_LVM_GETITEMCOUNT = _LVM_FIRST + 4
_LVM_GETNEXTITEM = _LVM_FIRST + 12
_LVM_GETSELECTEDCOUNT = _LVM_FIRST + 50
_LVM_GETSELECTIONMARK = _LVM_FIRST + 66
_LVNI_SELECTED = 0x0002
_SMTO_ABORTIFHUNG = 0x2
_ASK_TIMEOUT_MS = 80
# 고른 것이 이보다 많으면 번호를 다 세지 않는다(끌어서 함께 옮기지 않는다).
MAX_SELECTED = 200

_GA_ROOT = 2
_GUI_BUSY = 0x2 | 0x4 | 0x8 | 0x10      # 창을 옮기는 중이다, 메뉴가 떠 있다

_SVSI_SELECT = 0x1
_SVSI_DESELECTOTHERS = 0x4
_SVSI_POSITIONITEM = 0x80
_IID_SHELL_VIEW = di._GUID.of("000214E3-0000-0000-C000-000000000046")
_SV_SELECT_ITEM = 14                    # IShellView.SelectItem
_FV_SELECT_ITEM = 15                    # IFolderView.SelectItem
_FV_SELECT_AND_POSITION = 16            # IFolderView.SelectAndPositionItems
_FV_INVOKE_VERB = 34                    # IFolderView2.InvokeVerbOnSelection

_SHGFI_PIDL, _SHGFI_DISPLAYNAME, _SHGFI_ATTRIBUTES = 0x8, 0x200, 0x800
_SHGFI_SYSICONINDEX, _SHGFI_ATTR_SPECIFIED = 0x4000, 0x20000
_SFGAO_FOLDER = 0x20000000
_ILD_TRANSPARENT = 0x1
# 시스템 아이콘 묶음: 보통(32), 큰(48), 아주 큰(256). 배율에 따라 커진다.
_IMAGE_LISTS = (0, 2, 4)
_IID_IMAGE_LIST = di._GUID.of("46EB5926-582E-4017-9FDF-E8998DAA0950")
# 따라오는 그림을 만들 아이콘 수의 한도.
MAX_FACES = 40

VK_RETURN, VK_ESCAPE, VK_DELETE, VK_F2 = 0x0D, 0x1B, 0x2E, 0x71
VK_CONTROL, VK_SHIFT, VK_MENU = 0x11, 0x10, 0x12
VK_A = 0x41
VK_MOVES = (0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28)    # PgUp ~ 아래 화살표


class _GuiThreadInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("hwndActive", wintypes.HWND), ("hwndFocus", wintypes.HWND),
                ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND),
                ("rcCaret", wintypes.RECT)]


def _user32():
    user32 = ctypes.windll.user32
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD,
                                        ctypes.POINTER(_GuiThreadInfo)]
    user32.SendMessageTimeoutW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
        wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
    return user32


def _ask(window: int, message: int, wparam: int = 0, lparam: int = 0) -> int | None:
    """아이콘 목록 창에 짧게 묻는다. 답이 없으면 None."""
    result = ctypes.c_size_t(0)
    if not _user32().SendMessageTimeoutW(
            wintypes.HWND(window), message, ctypes.c_size_t(wparam).value, lparam,
            _SMTO_ABORTIFHUNG, _ASK_TIMEOUT_MS, ctypes.byref(result)):
        return None
    return int(ctypes.c_ssize_t(result.value).value)


def root_of(window: int) -> int:
    """아이콘 목록 창이 든 바탕화면 창."""
    if sys.platform != "win32" or not window:
        return 0
    return int(_user32().GetAncestor(wintypes.HWND(window), _GA_ROOT) or 0)


def is_foreground(window: int) -> bool:
    """바탕화면이 맨 앞에 있는가(키보드가 그쪽으로 간다)."""
    if sys.platform != "win32" or not window:
        return False
    root = root_of(window)
    return bool(root) and int(_user32().GetForegroundWindow() or 0) == root


def is_focused(window: int) -> bool:
    """글쇠가 바탕화면의 아이콘 목록으로 가는가. 이름을 고치는 칸이 떠 있거나
    메뉴가 떠 있으면 거짓이다(그때의 글쇠는 아이콘에 하는 일이 아니다)."""
    if not is_foreground(window):
        return False
    user32 = _user32()
    thread = user32.GetWindowThreadProcessId(wintypes.HWND(window), None)
    info = _GuiThreadInfo(cbSize=ctypes.sizeof(_GuiThreadInfo))
    if not thread or not user32.GetGUIThreadInfo(thread, ctypes.byref(info)):
        return False
    return int(info.hwndFocus or 0) == int(window) and not info.flags & _GUI_BUSY


def focus(window: int) -> bool:
    """바탕화면을 앞으로 부른다(바탕화면의 아이콘을 누른 것과 같다). 메모 아이콘은
    눌려도 키보드를 가져가지 않으므로, 대신 바탕화면이 받게 한다."""
    if sys.platform != "win32" or not window:
        return False
    root = root_of(window)
    if not root:
        return False
    user32 = _user32()
    if int(user32.GetForegroundWindow() or 0) == root:
        return True
    try:
        return bool(user32.SetForegroundWindow(wintypes.HWND(root)))
    except OSError:
        return False


def key_down(vk: int) -> bool:
    if sys.platform != "win32":
        return False
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)


def selected(window: int) -> list[int] | None:
    """고른 진짜 아이콘의 번호들. 물을 수 없으면 None. 너무 많으면 앞의
    ``MAX_SELECTED`` 개만 준다."""
    if sys.platform != "win32" or not window:
        return None
    count = _ask(window, _LVM_GETSELECTEDCOUNT)
    if count is None:
        return None
    out: list[int] = []
    index = -1
    for _ in range(min(count, MAX_SELECTED)):
        index = _ask(window, _LVM_GETNEXTITEM, index, _LVNI_SELECTED)
        if index is None or index < 0:
            break
        out.append(index)
    return out


def item_count(window: int) -> int | None:
    """바탕화면에 놓인 진짜 아이콘의 수. 물을 수 없으면 None."""
    if sys.platform != "win32" or not window:
        return None
    return _ask(window, _LVM_GETITEMCOUNT)


def selection_mark(window: int) -> int:
    """Shift 로 범위를 고를 때 탐색기가 기준으로 삼는 아이콘의 번호(없으면 -1)."""
    if sys.platform != "win32" or not window:
        return -1
    index = _ask(window, _LVM_GETSELECTIONMARK)
    return index if index is not None else -1


class _View:
    """바탕화면 보기의 인터페이스를 잡았다가 놓는다."""

    def __enter__(self):
        self._started = ctypes.windll.ole32.CoInitializeEx(None, di._COINIT_APARTMENT)
        self.folder = None
        try:
            self.folder, self.window = di._folder_view()
        except Exception:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *_exc) -> None:
        di._release(self.folder)
        self.folder = None
        if self._started in (0, 1):
            ctypes.windll.ole32.CoUninitialize()


def deselect_all() -> bool:
    """진짜 아이콘의 선택을 모두 푼다(바탕화면 빈 곳을 누른 것과 같다)."""
    if sys.platform != "win32":
        return False
    shell_view = None
    try:
        with _View() as view:
            shell_view = di._query(view.folder, _IID_SHELL_VIEW, "IShellView")
            hr = di._method(shell_view, _SV_SELECT_ITEM, ctypes.c_void_p,
                            ctypes.c_uint)(None, _SVSI_DESELECTOTHERS)
            return hr == 0
    except (di._Failed, OSError, ValueError):
        return False
    finally:
        di._release(shell_view)


def select(indices: list[int], *, replace: bool) -> bool:
    """그 번호의 진짜 아이콘들을 고른다. ``replace`` 면 다른 것은 푼다."""
    if sys.platform != "win32":
        return False
    if not indices:
        return deselect_all() if replace else True
    try:
        with _View() as view:
            pick = di._method(view.folder, _FV_SELECT_ITEM, ctypes.c_int,
                              ctypes.c_uint)
            ok = True
            for position, index in enumerate(indices):
                flags = _SVSI_SELECT
                if replace and position == 0:
                    flags |= _SVSI_DESELECTOTHERS
                ok = pick(int(index), flags) == 0 and ok
            return ok
    except (di._Failed, OSError, ValueError):
        return False


def _reposition(folder, wanted: dict[int, tuple[int, int] | None],
                delta: tuple[int, int]) -> dict[int, tuple[int, int]] | None:
    """그 번호의 아이콘들을 옮긴다. ``wanted`` 의 값이 자리면 거기로, None 이면
    지금 자리에서 ``delta`` 만큼. 옮기기 전의 자리를 돌려준다."""
    free = ctypes.windll.ole32.CoTaskMemFree
    free.argtypes = [ctypes.c_void_p]
    pidls: list[ctypes.c_void_p] = []
    try:
        item = di._method(folder, di._FV_ITEM, ctypes.c_int,
                          ctypes.POINTER(ctypes.c_void_p))
        position = di._method(folder, di._FV_ITEM_POSITION,
                              ctypes.c_void_p, ctypes.POINTER(wintypes.POINT))
        before: dict[int, tuple[int, int]] = {}
        targets: list[tuple[int, int]] = []
        for index, spot in wanted.items():
            pidl = ctypes.c_void_p()
            if item(int(index), ctypes.byref(pidl)) != 0 or not pidl:
                continue
            point = wintypes.POINT()
            if position(pidl, ctypes.byref(point)) != 0:
                free(pidl)
                continue
            pidls.append(pidl)
            before[int(index)] = (int(point.x), int(point.y))
            targets.append(spot if spot is not None else
                           (int(point.x) + delta[0], int(point.y) + delta[1]))
        if not pidls:
            return None
        count = len(pidls)
        array = (ctypes.c_void_p * count)(*[p.value for p in pidls])
        points = (wintypes.POINT * count)(
            *[wintypes.POINT(int(x), int(y)) for x, y in targets])
        hr = di._method(
            folder, _FV_SELECT_AND_POSITION, ctypes.c_uint,
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.POINT),
            ctypes.c_uint)(count, array, points,
                           _SVSI_SELECT | _SVSI_POSITIONITEM)
        return before if hr == 0 else None
    finally:
        for pidl in pidls:
            free(pidl)


def move_by(indices: list[int], dx: int, dy: int) -> dict[int, tuple[int, int]] | None:
    """그 번호의 진짜 아이콘들을 (dx, dy) 만큼 옮긴다(사용자가 그 아이콘들을 끌어다
    놓은 것과 같다). 옮기기 전의 자리(아이콘 목록 안의 좌표)를 돌려준다. 하지
    못했으면 None."""
    if sys.platform != "win32" or not indices or (not dx and not dy):
        return None
    try:
        with _View() as view:
            flags = wintypes.DWORD(0)
            di._method(view.folder, di._FV_FOLDER_FLAGS,
                       ctypes.POINTER(wintypes.DWORD))(ctypes.byref(flags))
            # '자동 정렬'이 켜져 있으면 놓일 자리는 탐색기가 정한다(부르는 쪽이
            # 옮긴 뒤의 자리를 뜻한 자리와 견주지 않는다).
            return _reposition(view.folder, {int(i): None for i in indices},
                               (int(dx), int(dy)))
    except (di._Failed, OSError, ValueError):
        return None


def place(spots: dict[int, tuple[int, int]]) -> bool:
    """그 번호의 진짜 아이콘들을 적어 둔 자리로 돌려놓는다(옮긴 것이 뜻한 대로
    되지 않았을 때)."""
    if sys.platform != "win32" or not spots:
        return False
    try:
        with _View() as view:
            return _reposition(view.folder, dict(spots), (0, 0)) is not None
    except (di._Failed, OSError, ValueError):
        return False


def positions(indices: list[int]) -> dict[int, tuple[int, int]]:
    """그 번호의 진짜 아이콘들이 지금 있는 자리(아이콘 목록 안의 좌표)."""
    out: dict[int, tuple[int, int]] = {}
    if sys.platform != "win32" or not indices:
        return out
    free = ctypes.windll.ole32.CoTaskMemFree
    free.argtypes = [ctypes.c_void_p]
    try:
        with _View() as view:
            item = di._method(view.folder, di._FV_ITEM, ctypes.c_int,
                              ctypes.POINTER(ctypes.c_void_p))
            position = di._method(view.folder, di._FV_ITEM_POSITION,
                                  ctypes.c_void_p, ctypes.POINTER(wintypes.POINT))
            for index in indices:
                pidl = ctypes.c_void_p()
                if item(int(index), ctypes.byref(pidl)) != 0 or not pidl:
                    continue
                try:
                    point = wintypes.POINT()
                    if position(pidl, ctypes.byref(point)) == 0:
                        out[int(index)] = (int(point.x), int(point.y))
                finally:
                    free(pidl)
    except (di._Failed, OSError, ValueError):
        pass
    return out


class _FileInfo(ctypes.Structure):
    _fields_ = [("hIcon", wintypes.HANDLE), ("iIcon", ctypes.c_int),
                ("dwAttributes", wintypes.DWORD),
                ("szDisplayName", wintypes.WCHAR * 260),
                ("szTypeName", wintypes.WCHAR * 80)]


_DLLS: dict[str, object] = {}


def _dll(name: str):
    """이 모듈만 쓰는 DLL 손잡이(인자 모양을 여기서 정해도 다른 곳과 섞이지 않는다)."""
    dll = _DLLS.get(name)
    if dll is not None:
        return dll
    dll = ctypes.WinDLL(name)
    if name == "shell32":
        dll.SHGetFileInfoW.argtypes = [
            ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_FileInfo),
            wintypes.UINT, wintypes.UINT]
        dll.SHGetFileInfoW.restype = ctypes.c_void_p
        dll.SHGetImageList.argtypes = [
            ctypes.c_int, ctypes.POINTER(di._GUID), ctypes.POINTER(ctypes.c_void_p)]
        dll.SHGetImageList.restype = ctypes.c_long
    elif name == "comctl32":
        dll.ImageList_GetIcon.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                          wintypes.UINT]
        dll.ImageList_GetIcon.restype = ctypes.c_void_p
        dll.ImageList_GetIconSize.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int)]
    elif name == "user32":
        dll.DestroyIcon.argtypes = [ctypes.c_void_p]
    _DLLS[name] = dll
    return dll


def _info_of(pidl, flags: int, attributes: int = 0) -> _FileInfo | None:
    info = _FileInfo()
    info.dwAttributes = attributes
    if not _dll("shell32").SHGetFileInfoW(
            pidl, 0, ctypes.byref(info), ctypes.sizeof(info),
            _SHGFI_PIDL | flags):
        return None
    return info


def is_container(index: int) -> bool:
    """그 번호의 진짜 아이콘이 무엇을 받아 담는 것인가(폴더, 압축 파일, 내 PC 같은
    것). 메모 아이콘은 그 안으로 들어갈 수 없다."""
    if sys.platform != "win32" or index < 0:
        return False
    free = ctypes.windll.ole32.CoTaskMemFree
    free.argtypes = [ctypes.c_void_p]
    try:
        with _View() as view:
            pidl = ctypes.c_void_p()
            if di._method(view.folder, di._FV_ITEM, ctypes.c_int,
                          ctypes.POINTER(ctypes.c_void_p))(
                    int(index), ctypes.byref(pidl)) != 0 or not pidl:
                return False
            try:
                info = _info_of(pidl, _SHGFI_ATTRIBUTES | _SHGFI_ATTR_SPECIFIED,
                                _SFGAO_FOLDER)
                return info is not None and bool(info.dwAttributes & _SFGAO_FOLDER)
            finally:
                free(pidl)
    except (di._Failed, OSError, ValueError, AttributeError):
        return False


def delete_selected() -> bool:
    """탐색기에 지금 고른 진짜 아이콘을 지워 달라고 청한다. 바탕화면에서 Delete 를
    누른 것과 같다: 탐색기가 휴지통으로 보내고, 묻도록 해 두었으면 묻는다.
    사용자가 고른 것을 Windows 휴지통 아이콘에 끌어다 놓았을 때만 부른다."""
    if sys.platform != "win32":
        return False
    try:
        with _View() as view:
            return di._method(view.folder, _FV_INVOKE_VERB,
                              ctypes.c_char_p)(b"delete") == 0
    except (di._Failed, OSError, ValueError):
        return False


def _image_list(side: int) -> ctypes.c_void_p | None:
    """한 변이 ``side`` 이상인 가장 작은 시스템 아이콘 묶음."""
    shell32, comctl32 = _dll("shell32"), _dll("comctl32")
    best = None
    for kind in _IMAGE_LISTS:
        images = ctypes.c_void_p()
        if shell32.SHGetImageList(kind, ctypes.byref(_IID_IMAGE_LIST),
                                  ctypes.byref(images)) != 0 or not images:
            continue
        di._release(best)
        best = images
        cx, cy = ctypes.c_int(0), ctypes.c_int(0)
        if comctl32.ImageList_GetIconSize(images, ctypes.byref(cx),
                                          ctypes.byref(cy)) and cx.value >= side:
            break
    return best


def faces(indices: list[int], side: int) -> list[tuple[int, int, str]]:
    """그 번호의 진짜 아이콘마다 (번호, 아이콘 그림의 손잡이, 이름). 메모 아이콘을
    잡고 끄는 동안 함께 고른 진짜 아이콘이 따라오는 모습을 그리는 데 쓴다. 손잡이는
    받은 쪽이 ``destroy_icon`` 으로 없앤다. ``side`` 는 그림의 한 변(물리 픽셀)."""
    out: list[tuple[int, int, str]] = []
    if sys.platform != "win32" or not indices:
        return out
    free = ctypes.windll.ole32.CoTaskMemFree
    free.argtypes = [ctypes.c_void_p]
    images = None
    try:
        with _View() as view:
            images = _image_list(int(side))
            if not images:
                return out
            item = di._method(view.folder, di._FV_ITEM, ctypes.c_int,
                              ctypes.POINTER(ctypes.c_void_p))
            for index in list(indices)[:MAX_FACES]:
                pidl = ctypes.c_void_p()
                if item(int(index), ctypes.byref(pidl)) != 0 or not pidl:
                    continue
                try:
                    info = _info_of(pidl, _SHGFI_SYSICONINDEX | _SHGFI_DISPLAYNAME)
                finally:
                    free(pidl)
                if info is None:
                    continue
                handle = _dll("comctl32").ImageList_GetIcon(
                    images, info.iIcon, _ILD_TRANSPARENT)
                if handle:
                    out.append((int(index), int(handle), str(info.szDisplayName)))
    except (di._Failed, OSError, ValueError, AttributeError):
        pass
    finally:
        di._release(images)
    return out


def destroy_icon(handle: int) -> None:
    if sys.platform == "win32" and handle:
        try:
            _dll("user32").DestroyIcon(handle)
        except (OSError, AttributeError):
            pass


__all__ = ["MAX_FACES", "MAX_SELECTED", "delete_selected", "deselect_all",
           "destroy_icon", "faces", "focus", "is_container", "is_focused",
           "is_foreground", "item_count", "key_down", "move_by", "place",
           "positions", "root_of", "select", "selected", "selection_mark"]
