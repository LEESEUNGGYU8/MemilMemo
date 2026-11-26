"""메모 투명도: 바탕(종이와 머리 줄)만 옅게 하고 글자는 또렷하게 둔다. 이 PC 에만 적는다."""
from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication, QSlider  # noqa: E402

from app import config, i18n  # noqa: E402
from app.local_state import LocalStateStore  # noqa: E402
from app.network.client_base import ClientBase  # noqa: E402
from app.settings import AppSettings, clamp_transparency  # noqa: E402
from app.ui import edit_menu, theme  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.sticky_note import StickyNoteWidget  # noqa: E402

ME = "지우"


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    i18n.set_language("ko")
    theme.apply_theme(app, "")
    yield app


def _note(qapp, settings=None, note_id="o1"):
    w = StickyNoteWidget({"id": note_id, "version": 1, "color": "#FFE066",
                          "width": 300, "height": 260, "content": ""},
                         ME, settings=settings or AppSettings())
    w.apply_local_state(200, 160)
    w.show()
    qapp.processEvents()
    return w


def _paper_alpha_at(w, x, y) -> int:
    img = QImage(w.size(), QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    w.render(img)
    return img.pixelColor(x, y).alpha()


# ── 값 ──

def test_transparency_is_clamped_to_five_percent_steps():
    assert clamp_transparency(0) == 0
    assert clamp_transparency(33) == 35
    assert clamp_transparency(95) == 100 - config.MIN_NOTE_OPACITY
    assert clamp_transparency(-10) == 0
    assert clamp_transparency("??") == 0


def test_settings_file_with_a_wrong_value_falls_back(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"note_transparency": 97, "opacity_hover_solid": False}),
                    encoding="utf-8")
    s = AppSettings.load(path)
    assert s.note_transparency == 80 and s.opacity_hover_solid is False


def test_local_state_keeps_the_opacity_outside_the_note_entries(tmp_path):
    path = tmp_path / "local_state.json"
    store = LocalStateStore(path)
    store.state.set_note_state("a", x=10, y=20)
    store.state.note_opacity["a"] = 60
    store.save()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["note_opacity"] == {"a": 60}
    # 1.0.6 이하는 메모 항목에 모르는 키가 있으면 파일 전체를 버린다.
    assert set(raw["notes"]["a"]) <= {"x", "y", "hidden", "w", "h"}
    again = LocalStateStore(path)
    assert again.state.note_opacity == {"a": 60}
    # 잘못된 값은 읽지 않는다. 이 PC 가 모르는 메모(e)의 것도 버린다.
    for nid in "bcd":
        raw["notes"][nid] = {"x": 0, "y": 0}
    raw["note_opacity"] = {"a": 5, "b": 100, "c": "x", "d": 45, "e": 50}
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert LocalStateStore(path).state.note_opacity == {"d": 45}
    again.state.remove_note("a")
    assert "a" not in again.state.note_opacity


# ── 메모 창 ──

def test_only_the_paper_turns_see_through(qapp):
    w = _note(qapp)
    x, y = 30, w.height() - 30            # 글이 없는 종이
    assert _paper_alpha_at(w, x, y) == 255
    w.set_opacity(50)
    assert w._paper_alpha() == pytest.approx(0.5)
    assert 110 <= _paper_alpha_at(w, x, y) <= 145
    # 머리 줄도 같은 만큼(겹쳐 칠해 진해지지 않는다).
    bar = _paper_alpha_at(w, 8, config.HANDLE_BAR_HEIGHT // 2)
    assert 110 <= bar <= 145
    w.set_opacity(5)                     # 너무 옅으면 누름이 아래로 빠진다
    assert w.opacity() == config.MIN_NOTE_OPACITY
    w.close()


def test_editing_and_mouse_over_show_it_solid(qapp):
    settings = AppSettings()
    w = _note(qapp, settings)
    w.set_opacity(40)
    assert w._paper_alpha() == pytest.approx(0.4)
    w._set_opacity_hover(True)
    assert w._paper_alpha() == 1.0
    settings.opacity_hover_solid = False
    assert w._paper_alpha() == pytest.approx(0.4)
    w._set_opacity_hover(False)
    w.enter_edit_mode()
    assert w._paper_alpha() == 1.0
    # 투명도를 고르는 동안은 편집 중이어도 고른 대로 보인다.
    w._opacity_preview = True
    assert w._paper_alpha() == pytest.approx(0.4)
    w._opacity_preview = False
    w.close()


def test_the_more_menu_picks_the_transparency(qapp, monkeypatch):
    w = _note(qapp)
    seen: list = []
    w.opacityChanged.connect(lambda nid, pct: seen.append((nid, pct)))

    def run(menu, pos):
        sliders = menu.findChildren(QSlider)
        assert len(sliders) == 1
        assert sliders[0].value() == 0                 # 투명도 0%
        assert w._opacity_preview
        sliders[0].setValue(7)                         # 35%
        assert w.opacity() == 65
        return None

    monkeypatch.setattr(edit_menu, "run_menu", run)
    w._show_more_menu()
    assert seen == [("o1", 65)] and not w._opacity_preview
    seen.clear()
    monkeypatch.setattr(edit_menu, "run_menu", lambda menu, pos: None)
    w._show_more_menu()                                # 바꾸지 않으면 알리지 않는다
    assert seen == []
    w.close()


def test_a_folded_note_stays_solid(qapp):
    w = _note(qapp)
    w.set_opacity(30)
    w._folded = True
    assert w._paper_alpha() == 1.0
    w._folded = False
    w.close()


# ── 메인 창 ──

class FakeClient(ClientBase):
    def __init__(self):
        super().__init__(ME)

    def release_lock(self, note_id):
        pass


def test_the_main_window_remembers_it_per_note(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(StickyNoteWidget, "bring_to_front",
                        lambda self, *, start_editing=False: None)
    settings = AppSettings(note_transparency=20)
    client = FakeClient()
    win = MainWindow(client=client, local_state=LocalStateStore(tmp_path / "ls.json"),
                     role="host", settings=settings)
    client.connectionChanged.emit(True)
    note = {"id": "n1", "version": 1, "color": "#FFE066", "content": "",
            "width": 280, "height": 240, "created_by": ME, "updated_by": ME}
    client._dispatch("welcome", {"users": [ME], "host": ME, "locks": {},
                                 "heartbeat_ms": 3000, "trash_days": 30,
                                 "folders": [], "notes": [note]})
    qapp.processEvents()
    w = win._stickies["n1"]
    assert w.opacity() == 80                           # 설정의 기본값(투명도 20%)
    w.opacityChanged.emit("n1", 55)
    assert win._local.state.note_opacity == {"n1": 55}
    w.opacityChanged.emit("n1", 80)                    # 기본값과 같으면 지운다
    assert win._local.state.note_opacity == {}
    win._force_quit = True
    win._teardown_stickies()
    win.close()
    win.deleteLater()
    qapp.processEvents()


def test_every_language_has_the_words():
    from app.locales import en, ja, ko
    for module in (ko, en, ja):
        for key in ("sticky.more_transparency", "settings.label_transparency",
                    "settings.check_opacity_hover"):
            assert module.STRINGS.get(key), (module.__name__, key)
