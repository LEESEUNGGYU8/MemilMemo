"""프로그램 창에 붙이기(실험): 맞추기, 모서리와 거리, 따라가기(가짜 창으로), 메뉴로 붙이고 떼기."""
from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from app import i18n  # noqa: E402
from app import window_attach as wa  # noqa: E402
from app.local_state import LocalStateStore  # noqa: E402
from app.network.client_base import ClientBase  # noqa: E402
from app.settings import AppSettings  # noqa: E402
from app.ui import theme, window_pick  # noqa: E402
from app.ui import window_follow as wf  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.sticky_note import StickyNoteWidget  # noqa: E402

EXCEL = r"C:\Program Files\Office\EXCEL.EXE"


def win(hwnd, title, exe=EXCEL, rect=(100, 100, 900, 700), **kw):
    return wa.WindowInfo(hwnd, title, exe, rect, **kw)


def test_windows_match_by_program_and_title():
    att = wa.Attachment(EXCEL.lower(), "단가표")
    assert wa.matches(att, win(1, "2026 단가표.xlsx - Excel"))
    assert not wa.matches(att, win(2, "매출 보고서.xlsx - Excel"))
    assert not wa.matches(att, win(3, "단가표", exe=r"C:\Windows\notepad.exe"))
    every = wa.Attachment(EXCEL)
    assert wa.matches(every, win(2, "매출 보고서.xlsx - Excel"))
    picked = wa.pick_window(att, [win(9, "단가표", minimized=True), win(8, "단가표 2")])
    assert picked.hwnd == 8                       # 내려 둔 창은 고르지 않는다


def test_the_nearest_corner_keeps_the_offset():
    rect = (100, 100, 900, 700)
    corner, dx, dy = wa.offset_for(rect, (880, 120))
    assert (corner, dx, dy) == ("tr", -20, 20)
    att = wa.Attachment(EXCEL, corner=corner, dx=dx, dy=dy)
    assert wa.note_pos_for(att, (300, 300, 1000, 900)) == (980, 320)   # 창이 커지고 옮겨도
    assert wa.offset_for(rect, (110, 690))[0] == "bl"


def test_attachments_survive_a_round_trip_and_bad_data_is_dropped():
    att = wa.Attachment(EXCEL, "단가표", "br", -5, -7, "단가표")
    assert wa.Attachment.from_dict(att.to_dict()) == att
    assert wa.Attachment.from_dict({"exe": ""}) is None
    assert wa.Attachment.from_dict({"exe": "a.exe", "dx": "x"}) is None
    assert wa.Attachment.from_dict({"exe": "a.exe", "corner": "zz"}).corner == "tr"


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    i18n.set_language("ko")
    theme.apply_theme(app, "")
    yield app


def test_the_follower_hides_notes_while_their_window_is_gone(qapp, monkeypatch):
    note = QWidget()
    note.show()
    windows = [win(5, "단가표")]
    infos = {5: windows[0]}
    monkeypatch.setattr(wa, "window_info", lambda hwnd: infos.get(hwnd))
    placed = []
    follower = wf.WindowFollower(lambda: {"n": note}, list_windows=lambda: list(windows))
    monkeypatch.setattr(follower, "_place", lambda w, att, info: placed.append(info.rect))
    monkeypatch.setattr(follower, "_rehook", lambda: None)
    follower.set_attachments({"n": wa.Attachment(EXCEL, "단가표")})
    assert follower.targets == {"n": 5} and placed == [(100, 100, 900, 700)]
    infos[5] = win(5, "단가표", minimized=True)              # 창을 내렸다
    follower.update_all()
    assert not note.isVisible() and "n" in follower.hidden
    infos[5] = win(5, "단가표", rect=(0, 0, 500, 400))       # 다시 올렸다
    follower.update_all()
    assert note.isVisible() and placed[-1] == (0, 0, 500, 400)
    windows.clear()                                         # 닫았다
    infos.clear()
    follower.rescan()
    assert not note.isVisible()
    follower.detach("n")
    assert note.isVisible() and follower.attachments == {}
    follower.stop()
    note.deleteLater()


def note(nid):
    return {"id": nid, "version": 1, "color": "#FFE066", "width": 280, "height": 220,
            "content": "<p>단가 메모</p>", "created_by": "지우", "updated_by": "지우",
            "updated_at": int(time.time() * 1000)}


def test_a_note_is_attached_from_its_menu_and_detached(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(StickyNoteWidget, "bring_to_front",
                        lambda self, *, start_editing=False: None)
    monkeypatch.setattr(wf.WindowFollower, "_rehook", lambda self: None)
    monkeypatch.setattr(wf.WindowFollower, "_place", lambda self, w, att, info: None)
    target = win(7, "2026 단가표.xlsx - Excel")
    monkeypatch.setattr(wa, "list_windows", lambda skip_pid=None: [target])
    monkeypatch.setattr(wa, "window_info", lambda hwnd: target if hwnd == 7 else None)
    monkeypatch.setattr(window_pick, "pick_window", lambda parent, windows: (windows[0], "단가표"))
    settings = AppSettings(fold_animation=False, window_attach=True)
    settings.save = lambda path=None: None
    client = ClientBase("지우")
    window = MainWindow(client=client, local_state=LocalStateStore(tmp_path / "ls.json"),
                        role="guest", settings=settings)
    window._follower._list = lambda: [target]
    client.connectionChanged.emit(True)
    client._dispatch("welcome", {"users": ["지우"], "host": "지우", "locks": {},
                                 "heartbeat_ms": 3000, "trash_days": 30, "reminders": {},
                                 "folders": [], "notes": [note("a")]})
    sticky = window._stickies["a"]
    sticky.show()
    sticky.attachRequested.emit("a")
    saved = window._local.state.attachments["a"]
    assert saved["exe"] == EXCEL and saved["title"] == "단가표" and saved["label"] == "단가표"
    assert sticky.is_attached() and sticky._attach_chip.text() == "단가표"
    assert window._follower.targets == {"a": 7}
    sticky.detachRequested.emit("a")
    assert "a" not in window._local.state.attachments and not sticky.is_attached()
    window._force_quit = True
    window._teardown_stickies()
    window.close()
    window.deleteLater()


def test_a_note_edge_touching_a_window_face_is_a_place_to_attach():
    """메모의 가장자리가 창의 끝면에 닿으면(겹치지 않아도) 붙을 곳이고, 놓을 자리는 그 면에
    딱 맞는다. 창 안에서 끝면에 맞춘 것도 된다. 멀거나 모서리끼리 스치면 아니다."""
    front = win(1, "앞 창", rect=(100, 100, 600, 500))
    back = win(2, "뒤 창", rect=(0, 0, 1000, 800))
    hidden = win(3, "숨은 창", rect=(0, 0, 2000, 2000), cloaked=True)
    stack = [hidden, front, back]
    # 앞 창의 오른쪽 면 바깥 10px(겹치지 않음): 오른쪽 면에 딱 붙는다.
    got = wa.edge_snap(stack, (610, 200, 890, 460))
    assert got[0] is front and got[1] == "right" and got[2] == (600, 200)
    # 왼쪽 면 바깥, 아래 면 바깥.
    assert wa.edge_snap(stack, (-185, 200, 95, 460))[1:] == ("left", (-180, 200))
    assert wa.edge_snap(stack, (200, 510, 480, 770))[1:] == ("below", (200, 500))
    # 창 안에서 오른쪽 끝면에 맞춘 것.
    assert wa.edge_snap(stack, (315, 200, 595, 460))[1:] == ("in_right", (320, 200))
    # 멀리 떨어졌거나 모서리끼리 스치는 것은 아니다.
    assert wa.edge_snap([front], (700, 200, 980, 460)) is None
    assert wa.edge_snap([front], (605, 495, 885, 755)) is None
    # 두 창이 겹치는 자리면 위에 있는 창.
    assert wa.edge_snap(stack, (590, 200, 870, 460))[0] is front
    assert wa.rect_gap((0, 0, 10, 10), (50, 0, 60, 10)) == 40
    assert wa.rect_gap((0, 0, 10, 10), (5, 5, 20, 20)) == 0


def test_dropping_a_note_on_a_window_edge_attaches_it_and_far_away_detaches(
        qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(StickyNoteWidget, "bring_to_front",
                        lambda self, *, start_editing=False: None)
    monkeypatch.setattr(wf.WindowFollower, "_rehook", lambda self: None)
    monkeypatch.setattr(wf.WindowFollower, "_place", lambda self, w, att, info: None)
    target = win(7, "2026 단가표.xlsx - Excel", rect=(100, 100, 900, 700))
    monkeypatch.setattr(wa, "list_windows", lambda skip_pid=None: [target])
    monkeypatch.setattr(wa, "window_info", lambda hwnd: target if hwnd == 7 else None)
    moved = []
    monkeypatch.setattr(wa, "move_window", lambda hwnd, x, y: moved.append((x, y)))
    settings = AppSettings(fold_animation=False, window_attach=True)
    settings.save = lambda path=None: None
    client = ClientBase("지우")
    window = MainWindow(client=client, local_state=LocalStateStore(tmp_path / "ls.json"),
                        role="guest", settings=settings)
    window._follower._list = lambda: [target]
    client.connectionChanged.emit(True)
    client._dispatch("welcome", {"users": ["지우"], "host": "지우", "locks": {},
                                 "heartbeat_ms": 3000, "trash_days": 30, "reminders": {},
                                 "folders": [], "notes": [note("a")]})
    sticky = window._stickies["a"]
    sticky.show()
    from PySide6.QtCore import QPoint
    # 메모의 왼쪽 가장자리가 창의 오른쪽 면 바깥 12px: 파란 테두리, 놓을 곳은 그 면.
    monkeypatch.setattr(wa, "window_rect", lambda hwnd: (912, 200, 1192, 460))
    sticky._drag_offset = QPoint(10, 10)
    sticky._preview_window_edge(QPoint(915, 205))
    assert sticky._attach_target[0] is target and sticky._attach_target[2] == (900, 200)
    assert sticky._attach_ghost.isVisible() and not sticky._attach_ghost._leaving
    sticky.attachDropped.emit("a", 7)
    saved = window._local.state.attachments["a"]
    assert saved["exe"] == EXCEL and saved["title"] == "2026 단가표.xlsx - Excel"
    assert sticky.is_attached() and window._follower.targets == {"a": 7}
    # 붙은 메모를 창에서 멀리 끌면 '떨어진다' 는 회색 점선, 놓으면 떨어진다.
    monkeypatch.setattr(wa, "window_rect", lambda hwnd: (1000, 200, 1280, 460))
    sticky._drag_offset = QPoint(10, 10)
    sticky._preview_window_edge(QPoint(1005, 205))
    assert sticky._attach_target is None and sticky._detach_pending
    assert sticky._attach_ghost._leaving
    # 가까이(40px 안) 두면 붙은 채로 남는다.
    monkeypatch.setattr(wa, "window_rect", lambda hwnd: (930, 200, 1210, 460))
    sticky._preview_window_edge(QPoint(935, 205))
    assert not sticky._detach_pending
    sticky.detachRequested.emit("a")
    assert "a" not in window._local.state.attachments and not sticky.is_attached()
    window._force_quit = True
    window._teardown_stickies()
    window.close()
    window.deleteLater()


def test_releasing_on_a_face_snaps_flush_and_releasing_far_detaches(qapp, monkeypatch):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    moved = []
    monkeypatch.setattr(wa, "move_window", lambda hwnd, x, y: moved.append((x, y)))
    sticky = StickyNoteWidget(note("r"), "지우", settings=AppSettings(fold_animation=False))
    sticky.show()
    attached, detached = [], []
    sticky.attachDropped.connect(lambda nid, hwnd: attached.append((nid, hwnd)))
    sticky.detachRequested.connect(detached.append)

    def release():
        ev = QMouseEvent(QMouseEvent.MouseButtonRelease, QPointF(10, 10), QPointF(10, 10),
                         Qt.LeftButton, Qt.NoButton, Qt.NoModifier)
        sticky.mouseReleaseEvent(ev)

    target = win(7, "단가표", rect=(100, 100, 900, 700))
    sticky._drag_offset = QPoint(10, 10)
    sticky._attach_target = (target, "right", (900, 200))
    release()
    assert moved == [(900, 200)] and attached == [("r", 7)] and detached == []
    # 이미 그 창에 붙어 있으면 다시 붙이지 않고 자리만 맞춘다.
    sticky.attached_hwnd = lambda: 7
    sticky._drag_offset = QPoint(10, 10)
    sticky._attach_target = (target, "below", (300, 700))
    release()
    assert moved[-1] == (300, 700) and attached == [("r", 7)]
    # 멀리 떼어 놓았으면 떨어진다.
    sticky._drag_offset = QPoint(10, 10)
    sticky._attach_target = None
    sticky._detach_pending = True
    release()
    assert detached == ["r"]
    sticky._teardown = True
    sticky.close()
