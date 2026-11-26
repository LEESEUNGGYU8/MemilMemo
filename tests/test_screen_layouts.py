"""모니터 구성마다 메모 자리를 따로 기억하고, 구성이 바뀌면 그 구성의 자리로 옮긴다.
실제 모니터를 바꾸지 않고 모니터 목록을 흉내 낸다."""
from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app import i18n  # noqa: E402
from app.local_state import LocalStateStore  # noqa: E402
from app.network.client_base import ClientBase  # noqa: E402
from app.settings import AppSettings  # noqa: E402
from app.ui import theme  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.screen_layouts import MAX_LAYOUTS, ScreenLayouts, keep_recent, screens_key  # noqa: E402
from app.ui.sticky_note import StickyNoteWidget  # noqa: E402

ME = "지우"
LAPTOP = [("DISPLAY1", 0, 0, 1536, 864, 1.25)]
DOCKED = [("DISPLAY1", 0, 0, 1536, 864, 1.25), ("DISPLAY2", 1536, 0, 1920, 1080, 1.0)]


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    i18n.set_language("ko")
    theme.apply_theme(app, "")
    yield app


def test_the_key_does_not_care_about_order():
    assert screens_key(DOCKED) == screens_key(list(reversed(DOCKED)))
    assert screens_key(LAPTOP) != screens_key(DOCKED)
    assert "@1.25" in screens_key(LAPTOP)


def test_old_layouts_are_dropped_first():
    layouts = {f"k{i}": {} for i in range(MAX_LAYOUTS + 2)}
    keep_recent(layouts, "k0")
    assert len(layouts) == MAX_LAYOUTS and "k0" in layouts and "k1" not in layouts


def test_a_change_is_announced_once(qapp):
    current = {"s": LAPTOP}
    watch = ScreenLayouts(screens=lambda: current["s"], watch=False)
    seen = []
    watch.switched.connect(lambda old, new: seen.append((old, new)))
    watch.check()
    assert seen == []
    current["s"] = DOCKED
    watch.check()
    watch.check()
    assert seen == [(screens_key(LAPTOP), screens_key(DOCKED))]


def test_local_state_keeps_layouts(tmp_path):
    path = tmp_path / "ls.json"
    store = LocalStateStore(path)
    for nid in "abc":
        store.state.set_note_state(nid, x=0, y=0)
    store.state.screen_layouts = {"k": {"a": [1, 2, 3, 4, 1]}}
    store.save()
    assert LocalStateStore(path).state.screen_layouts == {"k": {"a": [1, 2, 3, 4, 1]}}
    raw = json.loads(path.read_text(encoding="utf-8"))
    # 잘못된 값과 이 PC 가 모르는 메모(z)의 자리는 버린다.
    raw["screen_layouts"] = {"k": {"a": [1, 2], "b": [1, 2, 3, 4, True], "c": [5, 6, 7, 8, 0],
                                   "z": [5, 6, 7, 8, 0]}}
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert LocalStateStore(path).state.screen_layouts == {"k": {"c": [5, 6, 7, 8, 0]}}


def _note(qapp, nid="n", x=100, y=100):
    w = StickyNoteWidget({"id": nid, "version": 1, "color": "#FFE066",
                          "width": 240, "height": 180, "content": ""},
                         ME, settings=AppSettings(fold_animation=False))
    w.apply_local_state(x, y, 240, 180)
    w.show()
    qapp.processEvents()
    return w


def test_a_note_reports_and_takes_its_spot(qapp):
    w = _note(qapp)
    assert w.layout_spot() == [100, 100, 0, 0, 0]     # 접힌 자리를 둔 적이 없다
    assert w.apply_layout_spot([300, 120, 0, 0, 0])
    assert w.pos() == QPoint(300, 120)
    w.fold()
    qapp.processEvents()
    spot = w.layout_spot()
    assert spot[:2] == [300, 120] and spot[4] == 1
    assert w.apply_layout_spot([300, 120, 420, 260, 1])
    assert w.pos() == QPoint(420, 260)                 # 접힌 쪽지도 그 자리로
    w.unfold()
    qapp.processEvents()
    assert w.pos() == QPoint(300, 120)
    w._dock = "left"
    assert w.layout_spot() is None and not w.apply_layout_spot([1, 1, 1, 1, 0])
    w._dock = ""
    w.close()


class FakeClient(ClientBase):
    def __init__(self):
        super().__init__(ME)

    def release_lock(self, note_id):
        pass


def test_the_main_window_moves_notes_back_for_a_known_setup(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(StickyNoteWidget, "bring_to_front",
                        lambda self, *, start_editing=False: None)
    client = FakeClient()
    win = MainWindow(client=client, local_state=LocalStateStore(tmp_path / "ls.json"),
                     role="host", settings=AppSettings(fold_animation=False))
    current = {"s": DOCKED}
    win._screen_layouts._screens = lambda: current["s"]
    win._screen_layouts._key = screens_key(DOCKED)
    told = []
    monkeypatch.setattr(win, "notify", told.append)
    client.connectionChanged.emit(True)
    note = {"id": "n1", "version": 1, "color": "#FFE066", "content": "",
            "width": 240, "height": 180, "created_by": ME, "updated_by": ME}
    client._dispatch("welcome", {"users": [ME], "host": ME, "locks": {},
                                 "heartbeat_ms": 3000, "trash_days": 30,
                                 "folders": [], "notes": [note]})
    qapp.processEvents()
    w = win._stickies["n1"]
    w.move(400, 300)
    w.positionChanged.emit("n1", 400, 300)             # 모니터 두 대에서 둔 자리
    assert win._local.state.screen_layouts[screens_key(DOCKED)]["n1"][:2] == [400, 300]
    # 노트북만 남았다. 처음 보는 구성이라 옮기지 않고 지금 자리를 적는다.
    current["s"] = LAPTOP
    win._screen_layouts.check()
    win._remember_all_spots()
    assert told == []
    w.move(150, 120)
    w.positionChanged.emit("n1", 150, 120)
    assert win._local.state.screen_layouts[screens_key(LAPTOP)]["n1"][:2] == [150, 120]
    # 다시 모니터를 꽂으면 그 구성에서 쓰던 자리로 돌아간다.
    current["s"] = DOCKED
    win._screen_layouts.check()
    assert w.pos() == QPoint(400, 300) and told == [i18n.t("main.layout_restored")]
    win._force_quit = True
    win._teardown_stickies()
    win.close()
    win.deleteLater()
    qapp.processEvents()
