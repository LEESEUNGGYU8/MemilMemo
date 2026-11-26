"""프로그램 창에 붙인 메모를 그 창에 따라 옮기고 숨기고 앞으로 세운다(실험).

붙을 창은 몇 초마다 다시 찾고(새로 연 창, 다시 연 프로그램), 붙은 창의 움직임은 그
프로그램에만 건 WinEvent 훅으로 받는다. 이벤트가 몰려도 한 프레임에 한 번만 옮긴다.
메모는 Win32 로 물리 픽셀 자리에 옮긴다(모니터마다 배율이 달라도 맞는다)."""
from __future__ import annotations

import ctypes
import os
from typing import Callable

from PySide6.QtCore import QObject, QTimer

from .. import window_attach as wa

_EVENT_SYSTEM_FOREGROUND = 0x0003
_EVENT_SYSTEM_MINIMIZEEND = 0x0017
_EVENT_OBJECT_DESTROY = 0x8001
_EVENT_OBJECT_HIDE = 0x8003
_EVENT_OBJECT_NAMECHANGE = 0x800C
_EVENT_OBJECT_UNCLOAKED = 0x8018
_WINEVENT_OUTOFCONTEXT = 0x0000
_SWP_NOSIZE, _SWP_NOZORDER, _SWP_NOACTIVATE = 0x0001, 0x0004, 0x0010
_GW_HWNDPREV = 3


_USER32 = None


def _private_user32():
    global _USER32
    if _USER32 is None:
        _USER32 = ctypes.WinDLL("user32")
    return _USER32


class WindowFollower(QObject):
    """``notes()`` 는 메모 id → 메모 창. ``attachments`` 는 메모 id → Attachment."""

    def __init__(self, notes: Callable[[], dict], parent: QObject | None = None,
                 list_windows: Callable[[], list] | None = None,
                 restore: Callable[[str], None] | None = None) -> None:
        super().__init__(parent)
        self._notes = notes
        # 붙은 창 때문에 숨겼던 메모를 다시 보일 때. 보일지는 메인 창이 정한다(사용자가
        # 숨겼거나 폴더가 닫혀 있으면 그대로 둔다). 없으면 그냥 보인다.
        self._restore = restore
        self._list = list_windows or (lambda: wa.list_windows(skip_pid=os.getpid()))
        self.attachments: dict[str, wa.Attachment] = {}
        self.targets: dict[str, int] = {}        # 메모 id → 붙은 창
        self.hidden: set[str] = set()            # 창이 없거나 내려 둬서 숨긴 메모
        self._hooks: dict[int, tuple] = {}       # 프로그램 번호 → (훅, 콜백)
        self._tick = QTimer(self)
        self._tick.setSingleShot(True)
        self._tick.setInterval(16)
        self._tick.timeout.connect(self.update_all)
        self._scan = QTimer(self)
        self._scan.setInterval(3000)
        self._scan.timeout.connect(self.rescan)

    # ── 붙이기와 떼기 ──

    def _unhide(self, note_id: str) -> None:
        self.hidden.discard(note_id)
        if self._restore is not None:
            self._restore(note_id)
            return
        w = self._notes().get(note_id)
        if w is not None:
            w.show()

    def set_attachments(self, attachments: dict) -> None:
        self.attachments = dict(attachments)
        # 붙지 않게 된 메모는 붙은 창 때문에 숨겨 둔 것을 푼다.
        for nid in [n for n in self.hidden if n not in self.attachments]:
            self.targets.pop(nid, None)
            self._unhide(nid)
        self.rescan()
        if self.attachments:
            self._scan.start()
        else:
            self._scan.stop()

    def attach(self, note_id: str, att: wa.Attachment) -> None:
        self.attachments[note_id] = att
        self.set_attachments(self.attachments)

    def detach(self, note_id: str) -> None:
        self.attachments.pop(note_id, None)
        self.targets.pop(note_id, None)
        if note_id in self.hidden:
            self._unhide(note_id)
        self.set_attachments(self.attachments)

    def stop(self) -> None:
        self._scan.stop()
        self._tick.stop()
        self._unhook_all()

    # ── 창 찾기 ──

    def rescan(self) -> None:
        """붙을 창을 다시 찾고, 그 프로그램들에 훅을 건다."""
        windows = self._list() if self.attachments else []
        self.targets = {}
        for nid, att in self.attachments.items():
            win = wa.pick_window(att, windows)
            if win is not None:
                self.targets[nid] = win.hwnd
        self._rehook()
        self.update_all()

    def update_all(self) -> None:
        notes = self._notes()
        for nid in list(self.attachments):
            w = notes.get(nid)
            if w is None:
                continue
            hwnd = self.targets.get(nid)
            info = wa.window_info(hwnd) if hwnd else None
            if info is None or info.minimized or info.cloaked:
                if nid not in self.hidden and w.isVisible():
                    self.hidden.add(nid)
                    w.hide()
                continue
            if nid in self.hidden:
                self._unhide(nid)
            self._place(w, self.attachments[nid], info)

    def _place(self, w, att: wa.Attachment, info: wa.WindowInfo) -> None:
        """메모를 창의 그 모서리에서 그 거리만큼 떨어진 곳에 둔다(물리 픽셀). 사용자가
        끌고 있는 메모는 옮기지 않는다(놓으면 그 자리로 다시 잰다)."""
        if not wa.is_windows() or getattr(w, "_drag_offset", None) is not None:
            return
        from ctypes import wintypes
        x, y = wa.note_pos_for(att, info.rect)
        hwnd = int(w.winId())
        ctypes.windll.user32.SetWindowPos(
            wintypes.HWND(hwnd), None, int(x), int(y), 0, 0,
            _SWP_NOSIZE | _SWP_NOZORDER | _SWP_NOACTIVATE)

    def raise_above(self, target: int) -> None:
        """그 창이 앞에 왔다. 거기 붙은 메모를 그 창 바로 위에 세운다(항상 위가 아니다)."""
        if not wa.is_windows():
            return
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        notes = self._notes()
        for nid, hwnd in self.targets.items():
            if hwnd != target or nid in self.hidden:
                continue
            w = notes.get(nid)
            if w is None or not w.isVisible():
                continue
            above = user32.GetWindow(wintypes.HWND(target), _GW_HWNDPREV)
            user32.SetWindowPos(wintypes.HWND(int(w.winId())), above or None, 0, 0, 0, 0,
                                0x0002 | _SWP_NOSIZE | _SWP_NOACTIVATE)

    # ── 훅 ──

    def _rehook(self) -> None:
        if not wa.is_windows():
            return
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        pids: set[int] = set()
        for hwnd in self.targets.values():
            pid = wintypes.DWORD(0)
            user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
            if pid.value:
                pids.add(int(pid.value))
        for pid in list(self._hooks):
            if pid not in pids:
                self._unhook(pid)
        for pid in pids - set(self._hooks):
            self._hook(pid)

    def _hook(self, pid: int) -> None:
        from ctypes import wintypes
        # 따로 연 user32: 함수의 인자 형식을 다른 모듈(desktop_watch)과 나눠 쓰지 않는다.
        user32 = _private_user32()
        proc_type = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND,
                                       ctypes.c_long, ctypes.c_long, wintypes.DWORD,
                                       wintypes.DWORD)

        def on_event(_hook, event, hwnd, obj, _child, _thread, _time):
            try:
                h = int(hwnd or 0)
                if obj != 0 or h not in self.targets.values():
                    return
                if event == _EVENT_SYSTEM_FOREGROUND:
                    self.raise_above(h)
                if event in (_EVENT_OBJECT_DESTROY, _EVENT_OBJECT_HIDE, _EVENT_OBJECT_NAMECHANGE):
                    # 닫히거나 숨었거나 제목이 바뀌었다(다른 문서). 붙을 창을 다시 찾는다.
                    QTimer.singleShot(0, self.rescan)
                elif not self._tick.isActive():
                    self._tick.start()
            except Exception:
                pass

        proc = proc_type(on_event)
        user32.SetWinEventHook.restype = wintypes.HANDLE
        user32.SetWinEventHook.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE,
                                           proc_type, wintypes.DWORD, wintypes.DWORD,
                                           wintypes.DWORD]
        hooks = []
        # 앞으로 옴, 내림과 올림(0x16~0x17), 없어짐과 보임과 숨음(0x8001~0x8003), 자리와 제목
        # 바뀜(0x800B~0x800C), 다른 데스크톱으로 감과 돌아옴(0x8017~0x8018)만 받는다. 그 사이의
        # 초점, 커서, 값 바뀜은 글을 칠 때마다 수백 번 와서 받지 않는다.
        for first, last in ((_EVENT_SYSTEM_FOREGROUND, _EVENT_SYSTEM_FOREGROUND),
                            (0x0016, _EVENT_SYSTEM_MINIMIZEEND),
                            (_EVENT_OBJECT_DESTROY, 0x8003),
                            (0x800B, 0x800C),
                            (0x8017, _EVENT_OBJECT_UNCLOAKED)):
            h = user32.SetWinEventHook(first, last, None, proc, pid, 0, _WINEVENT_OUTOFCONTEXT)
            if h:
                hooks.append(h)
        self._hooks[pid] = (hooks, proc)

    def _unhook(self, pid: int) -> None:
        hooks, _proc = self._hooks.pop(pid, ([], None))
        for h in hooks:
            try:
                _private_user32().UnhookWinEvent(h)
            except OSError:
                pass

    def _unhook_all(self) -> None:
        for pid in list(self._hooks):
            self._unhook(pid)
