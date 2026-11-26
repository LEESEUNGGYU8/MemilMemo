"""프로그램 창에 붙이기(실험): 메모를 다른 프로그램의 창에 붙여 그 창을 따라가게 한다.

* 붙은 메모는 창을 옮기면 따라가고, 창을 내리거나 닫으면 숨고, 창이 다시 보이면
  나타난다. 창이 앞에 오면 메모는 그 창 바로 위에 선다(항상 위가 아니다).
* 어느 창에 붙는가는 '실행 파일 경로 + 제목에 든 글'(비우면 그 프로그램의 창 전체)로
  맞춘다. 창 제목과 프로그램 이름은 이 PC 안에서 맞추는 데만 쓴다.
* 자리는 창의 가장 가까운 모서리에서 잰 거리로 기억한다(창 크기가 바뀌어도 그 모서리를
  따라간다).
* 창의 움직임은 SetWinEventHook(주입 없는 WINEVENT_OUTOFCONTEXT)으로 받는다. 다른
  프로그램의 창을 메모의 주인 창으로 삼지 않는다(그 프로그램이 멈추면 메밀메모도 멈출
  수 있다).

이 모듈의 계산(맞추기, 모서리, 자리)은 순수 함수라 시험한다. Windows 를 부르는 것은
``WindowInfo`` 를 만드는 쪽뿐이다."""
from __future__ import annotations

import ctypes
import os
import sys
from dataclasses import dataclass, field

CORNERS = ("tl", "tr", "bl", "br")


@dataclass(frozen=True)
class WindowInfo:
    """보이는 창 하나. ``rect`` 는 화면에 보이는 테두리(물리 픽셀, 그림자 제외)."""
    hwnd: int
    title: str
    exe: str
    rect: tuple[int, int, int, int]          # 왼, 위, 오른, 아래
    minimized: bool = False
    cloaked: bool = False

    @property
    def program(self) -> str:
        """프로그램 이름(실행 파일 이름에서 .exe 를 뺀 것)."""
        name = os.path.basename(self.exe or "")
        return name[:-4] if name.lower().endswith(".exe") else name


@dataclass
class Attachment:
    """메모 하나가 붙은 곳. ``title`` 이 비면 그 프로그램의 창 전체."""
    exe: str
    title: str = ""
    corner: str = "tr"
    dx: int = 0
    dy: int = 0
    label: str = ""                          # 머리 줄에 적을 이름(창 제목이나 프로그램)
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"exe": self.exe, "title": self.title, "corner": self.corner,
                "dx": int(self.dx), "dy": int(self.dy), "label": self.label}

    @classmethod
    def from_dict(cls, raw) -> "Attachment | None":
        if not isinstance(raw, dict) or not isinstance(raw.get("exe"), str) or not raw["exe"]:
            return None
        corner = raw.get("corner") if raw.get("corner") in CORNERS else "tr"
        try:
            dx, dy = int(raw.get("dx", 0)), int(raw.get("dy", 0))
        except (TypeError, ValueError):
            return None
        title = raw.get("title") if isinstance(raw.get("title"), str) else ""
        label = raw.get("label") if isinstance(raw.get("label"), str) else ""
        return cls(raw["exe"], title[:200], corner, dx, dy, label[:80])


def matches(att: Attachment, win: WindowInfo) -> bool:
    """이 창이 붙을 곳인가: 같은 실행 파일이고, 제목 조건이 있으면 제목에 그 글이 든다."""
    if not win.exe or os.path.normcase(win.exe) != os.path.normcase(att.exe):
        return False
    return not att.title or att.title.lower() in (win.title or "").lower()


def pick_window(att: Attachment, windows: list[WindowInfo]) -> WindowInfo | None:
    """붙을 창: 맞는 창 가운데 Windows 의 차례(앞에 있는 것부터)로 첫 창.
    내려 둔 창과 다른 가상 데스크톱의 창은 고르지 않는다."""
    for win in windows:
        if not win.minimized and not win.cloaked and matches(att, win):
            return win
    return None


def nearest_corner(rect: tuple[int, int, int, int], point: tuple[int, int]) -> str:
    """메모의 왼쪽 위(``point``)에서 가장 가까운 창 모서리."""
    left, top, right, bottom = rect
    x, y = point
    horiz = "l" if abs(x - left) <= abs(x - right) else "r"
    vert = "t" if abs(y - top) <= abs(y - bottom) else "b"
    return vert + horiz


def corner_point(rect: tuple[int, int, int, int], corner: str) -> tuple[int, int]:
    left, top, right, bottom = rect
    return (left if corner[1] == "l" else right, top if corner[0] == "t" else bottom)


def offset_for(rect: tuple[int, int, int, int], note_pos: tuple[int, int]) -> tuple[str, int, int]:
    """메모 자리를 창의 가장 가까운 모서리에서 잰 거리로(모서리, dx, dy)."""
    corner = nearest_corner(rect, note_pos)
    cx, cy = corner_point(rect, corner)
    return corner, note_pos[0] - cx, note_pos[1] - cy


# 끌어 붙이기(물리 픽셀, 100% 기준. 화면 배율을 곱해 쓴다).
EDGE_MARGIN = 24        # 메모의 가장자리가 창의 끝면에서 이만큼 안이면 붙을 곳
DETACH_GAP = 40         # 붙은 메모를 그 창에서 이만큼 넘게 떼어 놓으면 떨어진다
_OVERLAP = 12           # 붙는 면을 따라 이만큼은 겹쳐야 한다(모서리끼리 스치는 것은 빼고)

# 붙는 면: 창의 바깥에 나란히(오른쪽, 왼쪽, 아래, 위)와 창 안에서 끝면에 맞춘 것.
SIDES = ("right", "left", "below", "above",
         "in_right", "in_left", "in_bottom", "in_top")


def edge_snap(windows: list[WindowInfo], note: tuple[int, int, int, int],
              margin: int = EDGE_MARGIN):
    """메모(왼, 위, 오른, 아래)가 붙을 창과 면, 딱 맞출 메모의 왼쪽 위. 없으면 None.

    메모의 한 가장자리가 창의 한 끝면에서 ``margin`` 안에 있고 그 면을 따라 겹치면 붙을
    곳이다. 창 바깥에 나란히 둔 것(오른쪽 면에 왼쪽 가장자리)과 창 안에서 끝면에 맞춘
    것(오른쪽 면에 오른쪽 가장자리) 둘 다. 여럿이면 위에 있는 창, 같은 창이면 더 가까운
    면. 최소화했거나 숨은 창은 보지 않는다."""
    nl, nt, nr, nb = note
    w, h = nr - nl, nb - nt
    best = None
    for z, win in enumerate(windows):
        if win.minimized or win.cloaked:
            continue
        L, T, R, B = win.rect
        along_y = min(nb, B) - max(nt, T) >= _OVERLAP
        along_x = min(nr, R) - max(nl, L) >= _OVERLAP
        tries = []
        if along_y:
            tries += [("right", nl - R, (R, nt)), ("left", nr - L, (L - w, nt)),
                      ("in_right", nr - R, (R - w, nt)), ("in_left", nl - L, (L, nt))]
        if along_x:
            tries += [("below", nt - B, (nl, B)), ("above", nb - T, (nl, T - h)),
                      ("in_bottom", nb - B, (nl, B - h)), ("in_top", nt - T, (nl, T))]
        for side, gap, pos in tries:
            if abs(gap) <= margin:
                key = (z, abs(gap))
                if best is None or key < best[0]:
                    best = (key, win, side, pos)
        if best is not None and best[0][0] == z:
            break                     # 위에 있는 창이 먼저다
    if best is None:
        return None
    return best[1], best[2], best[3]


def rect_gap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> int:
    """두 네모 사이의 거리(겹치면 0)."""
    dx = max(b[0] - a[2], a[0] - b[2], 0)
    dy = max(b[1] - a[3], a[1] - b[3], 0)
    return max(dx, dy)


def note_pos_for(att: Attachment, rect: tuple[int, int, int, int]) -> tuple[int, int]:
    """창이 ``rect`` 에 있을 때 메모의 왼쪽 위(물리 픽셀)."""
    cx, cy = corner_point(rect, att.corner)
    return cx + att.dx, cy + att.dy


# ── Windows ──

_DWMWA_EXTENDED_FRAME_BOUNDS = 9
_DWMWA_CLOAKED = 14
_GWL_EXSTYLE = -20
_WS_EX_TOOLWINDOW = 0x00000080
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def is_windows() -> bool:
    return sys.platform == "win32"


def window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """창의 자리(물리 픽셀, 왼, 위, 오른, 아래). 메모 창처럼 테두리가 없는 창에 쓴다."""
    if not is_windows() or not hwnd:
        return None
    from ctypes import wintypes
    rect = wintypes.RECT()
    if not ctypes.windll.user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        return None
    return rect.left, rect.top, rect.right, rect.bottom


def move_window(hwnd: int, x: int, y: int) -> None:
    """창을 그 자리(물리 픽셀)로 옮긴다(크기와 앞뒤는 그대로, 앞으로 부르지 않는다)."""
    if not is_windows() or not hwnd:
        return
    from ctypes import wintypes
    ctypes.windll.user32.SetWindowPos(wintypes.HWND(hwnd), None, int(x), int(y), 0, 0,
                                      0x0001 | 0x0004 | 0x0010)


def cursor_pos() -> tuple[int, int] | None:
    """마우스 커서 자리(물리 픽셀). Windows 가 아니면 None."""
    if not is_windows():
        return None
    from ctypes import wintypes
    point = wintypes.POINT()
    if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
        return None
    return point.x, point.y


# 붙일 창으로 보이지 않는 셸 창(바탕 화면, 작업 표시줄).
_SHELL_CLASSES = ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd")


def _frame_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    from ctypes import wintypes
    rect = wintypes.RECT()
    try:
        ok = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd), _DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect),
            ctypes.sizeof(rect)) == 0
    except OSError:
        ok = False
    if not ok and not ctypes.windll.user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        return None
    return rect.left, rect.top, rect.right, rect.bottom


def _cloaked(hwnd: int) -> bool:
    from ctypes import wintypes
    value = ctypes.c_int(0)
    try:
        ctypes.windll.dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd), _DWMWA_CLOAKED, ctypes.byref(value), ctypes.sizeof(value))
    except OSError:
        return False
    return bool(value.value)


def _exe_of(hwnd: int) -> str:
    from ctypes import wintypes
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
    if not pid.value:
        return ""
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(handle)


def window_info(hwnd: int) -> WindowInfo | None:
    if not is_windows() or not hwnd:
        return None
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    rect = _frame_rect(hwnd)
    if rect is None:
        return None
    length = user32.GetWindowTextLengthW(wintypes.HWND(hwnd))
    buf = ctypes.create_unicode_buffer(max(1, length + 1))
    user32.GetWindowTextW(wintypes.HWND(hwnd), buf, len(buf))
    return WindowInfo(int(hwnd), buf.value, _exe_of(hwnd), rect,
                      bool(user32.IsIconic(wintypes.HWND(hwnd))), _cloaked(hwnd))


def list_windows(skip_pid: int | None = None) -> list[WindowInfo]:
    """보이는 위쪽 창들(제목이 있는 것, 도구 창이 아닌 것)을 Windows 의 차례로.
    ``skip_pid`` 의 창(이 앱)은 뺀다."""
    if not is_windows():
        return []
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _lparam):
        h = int(hwnd or 0)
        if not h or not user32.IsWindowVisible(hwnd):
            return True
        if user32.GetWindowLongW(hwnd, _GWL_EXSTYLE) & _WS_EX_TOOLWINDOW:
            return True
        if user32.GetWindowTextLengthW(hwnd) <= 0:
            return True
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        if cls.value in _SHELL_CLASSES:
            return True
        if skip_pid is not None:
            pid = wintypes.DWORD(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == skip_pid:
                return True
        found.append(h)
        return True

    user32.EnumWindows(each, 0)
    out = []
    for h in found:
        info = window_info(h)
        if info is not None and info.exe:
            out.append(info)
    return out
