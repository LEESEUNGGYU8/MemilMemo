"""어디서나 쓰는 단축키: 조합 읽기, 겹침, 키보드 단축키 걸기(가짜로), 마우스 단추 맞추기,
설정의 조합 칸, 찾기 창, 클립보드로 새 메모. 시험은 Windows 전체에 단축키나 훅을
걸지 않는다."""
from __future__ import annotations

import ctypes
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QKeyEvent, QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app import hotkeys as hk  # noqa: E402
from app import i18n, shell_menu  # noqa: E402
from app.local_state import LocalStateStore  # noqa: E402
from app.network.client_base import ClientBase  # noqa: E402
from app.settings import AppSettings  # noqa: E402
from app.ui import settings_dialog, theme  # noqa: E402
from app.ui.global_keys import GlobalKeys, HotkeyField, _Filter, display  # noqa: E402
from app.ui.main_window import MainWindow, copy_mime  # noqa: E402
from app.ui.quick_find import QuickFind, find_notes  # noqa: E402
from app.ui.settings_dialog import SettingsDialog  # noqa: E402
from app.ui.sticky_note import StickyNoteWidget  # noqa: E402

ME = "지우"


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    i18n.set_language("ko")
    theme.apply_theme(app, "")
    yield app


# ── 조합 ──

def test_combos_are_read_and_written_the_same_way():
    assert hk.parse("ctrl+alt+n").text() == "Ctrl+Alt+N"
    assert hk.parse("Alt+Ctrl+N").text() == "Ctrl+Alt+N"
    assert hk.parse("Ctrl+XButton1").is_mouse
    assert hk.parse("F5").text() == "F5"                    # F 키는 혼자도 된다
    assert hk.parse("N") is None and hk.parse("Shift+N") is None   # 평소 입력을 막는다
    assert hk.parse("MButton") is None                      # 가운데 단추는 조합으로만
    assert hk.parse("Shift+MButton").text() == "Shift+MButton"
    assert hk.parse("XButton2").text() == "XButton2"
    assert hk.parse("Ctrl+Ctrl+N") is None and hk.parse("Hyper+N") is None
    assert hk.normalize("") == "" and hk.normalize("??") == ""


def test_saved_bindings_are_cleaned_and_duplicates_found():
    clean = hk.clean_bindings({"find": {"key": "Ctrl+XButton1", "mouse": "XButton2"},
                               "junk": {"key": "Ctrl+J"}})
    assert clean["find"] == {"key": "", "mouse": "XButton2"}   # 칸마다 맞는 종류만
    assert clean["new_note"] == {"key": "Ctrl+Alt+N", "mouse": ""}
    assert set(clean) == set(hk.ACTIONS)
    assert hk.clean_bindings(None) == hk.DEFAULTS
    dup = hk.duplicates({"new_note": {"key": "Ctrl+Alt+N"}, "find": {"key": "ctrl+alt+n"}})
    assert dup == {"Ctrl+Alt+N"}


def test_mouse_buttons_match_only_with_the_same_modifiers():
    table = {hk.parse("Ctrl+XButton1"): "new_note", hk.parse("XButton2"): "find"}
    assert hk.match_mouse("XButton1", frozenset({"Ctrl"}), table) == "new_note"
    assert hk.match_mouse("XButton1", frozenset(), table) is None
    assert hk.match_mouse("XButton2", frozenset(), table) == "find"
    assert hk.match_mouse("XButton2", frozenset({"Shift"}), table) is None


def test_keyboard_hotkeys_report_combos_another_program_holds():
    calls = []
    kb = hk.KeyboardHotkeys()
    kb.register = lambda hid, mods, vk: (calls.append((hid, mods, vk)), 1409 if vk == ord("F") else 0)[1]
    kb.unregister = lambda hid: calls.append(("off", hid))
    busy = kb.apply({"new_note": "Ctrl+Alt+N", "find": "Ctrl+Alt+F", "clip_note": ""})
    assert busy == ["Ctrl+Alt+F"]
    nid = next(c[0] for c in calls if c[2] == ord("N"))
    assert calls[0][1] == 0x0002 | 0x0001                    # Ctrl | Alt
    assert kb.handle_message(0x0312, nid) == "new_note"
    assert kb.handle_message(0x0312, nid + 1) is None
    assert kb.handle_message(0x0100, nid) is None
    assert kb.holds("ctrl+alt+n") and kb.probe("Ctrl+Alt+N")
    assert not kb.probe("Ctrl+Alt+F")
    kb.clear()
    assert ("off", nid) in calls and kb.action_for(nid) is None


def test_the_native_filter_turns_a_hotkey_message_into_an_action(qapp):
    keys = GlobalKeys()
    keys.apply({"new_note": {"key": "Ctrl+Alt+N"}})
    nid = next(iter(keys.keyboard._ids))
    got = []
    keys.triggered.connect(got.append)
    from ctypes import wintypes
    msg = wintypes.MSG()
    msg.message = 0x0312
    msg.wParam = nid
    handled, _ = _Filter(keys).nativeEventFilter(b"windows_generic_MSG",
                                                 ctypes.addressof(msg))
    assert handled and got == ["new_note"]
    keys.stop()


def test_mouse_combos_read_in_words(qapp):
    assert display("Ctrl+XButton1") == "Ctrl+뒤로 단추"
    assert display("XButton2") == "앞으로 단추"
    assert display("Ctrl+Alt+N") == "Ctrl+Alt+N"


# ── 설정의 조합 칸 ──

def _key(field, key, mods=Qt.NoModifier, text=""):
    field.keyPressEvent(QKeyEvent(QEvent.KeyPress, key, mods, text))


def test_a_key_field_takes_a_combo_and_esc_or_backspace(qapp):
    field = HotkeyField(False, "Ctrl+Alt+N")
    got = []
    field.changed.connect(got.append)
    field.click()
    assert field.capturing()
    _key(field, Qt.Key_Control, Qt.ControlModifier)          # 조합 키만으로는 안 들어간다
    assert field.capturing()
    _key(field, Qt.Key_M, Qt.ControlModifier | Qt.AltModifier, "m")
    assert field.value() == "Ctrl+Alt+M" and not field.capturing()
    field.click()
    _key(field, Qt.Key_Escape)
    assert field.value() == "Ctrl+Alt+M" and not field.capturing()
    field.click()
    _key(field, Qt.Key_Backspace)
    assert field.value() == "" and got == ["Ctrl+Alt+M", ""]
    field.click()
    _key(field, Qt.Key_Q, Qt.ShiftModifier, "Q")            # Shift+글자는 받지 않는다
    assert field.value() == "" and field.capturing()


def test_a_mouse_field_takes_side_buttons(qapp):
    field = HotkeyField(True, "")
    field.click()
    press = QMouseEvent(QEvent.MouseButtonPress, QPointF(5, 5), QPointF(5, 5),
                        Qt.BackButton, Qt.BackButton, Qt.ControlModifier)
    field.mousePressEvent(press)
    assert field.value() == "Ctrl+XButton1"
    field.click()
    middle = QMouseEvent(QEvent.MouseButtonPress, QPointF(5, 5), QPointF(5, 5),
                         Qt.MiddleButton, Qt.MiddleButton, Qt.NoModifier)
    field.mousePressEvent(middle)                            # 가운데 단추는 조합으로만
    assert field.value() == "Ctrl+XButton1"


def test_the_settings_page_flags_busy_and_duplicate_combos_and_saves(qapp, monkeypatch):
    monkeypatch.setattr(settings_dialog, "set_windows_autostart", lambda v: True)
    monkeypatch.setattr(shell_menu, "set_registered", lambda *a, **k: True)
    settings = AppSettings()
    settings.save = lambda path=None: None
    dlg = SettingsDialog(settings, role="host")
    dlg.set_hotkey_probe(lambda text: text != "Ctrl+Alt+F")
    key, mouse, row = dlg._hotkey_fields["find"]
    assert row.hint.text() == "다른 프로그램이 사용 중" and not row.hint.isHidden()
    key.set_value("Ctrl+Alt+N")                               # 새 메모와 겹친다
    assert row.hint.text() == "다른 단축키와 겹침"
    key.set_value("Ctrl+Alt+K")
    mouse.set_value("Ctrl+XButton2")
    assert row.hint.isHidden()
    dlg._on_accept()
    assert settings.hotkeys["find"] == {"key": "Ctrl+Alt+K", "mouse": "Ctrl+XButton2"}
    dlg.deleteLater()


# ── 찾기 창 ──

NOTES = [
    {"id": "a", "title": "주간 회의 메모", "body": "주간 회의 메모\n견적 회신", "folder": "업무",
     "updated_at": 3},
    {"id": "b", "title": "거래처 연락처", "body": "거래처 연락처\n다온유통 견적 담당", "folder": "",
     "updated_at": 5},
    {"id": "c", "title": "견적서 양식", "body": "견적서 양식", "folder": "", "updated_at": 1},
]


def test_find_puts_title_matches_first_then_recent_body_matches():
    found = find_notes(NOTES, "견적")
    assert [n["id"] for n in found] == ["c", "b", "a"]
    assert found[1]["snippet"] == "다온유통 견적 담당"
    assert [n["id"] for n in find_notes(NOTES, "")] == ["b", "a", "c"]   # 최근 순
    assert find_notes(NOTES, "없는 말") == []


def test_the_find_window_picks_with_enter_and_closes_with_esc(qapp):
    win = QuickFind()
    got = []
    win.picked.connect(got.append)
    win.open_at(NOTES)
    assert win.results() == ["b", "a", "c"]
    win._input.setText("견적")
    assert win.results() == ["c", "b", "a"]
    QApplication.sendEvent(win._input, QKeyEvent(QEvent.KeyPress, Qt.Key_Down, Qt.NoModifier))
    QApplication.sendEvent(win._input, QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))
    assert got == ["b"] and not win.isVisible()
    win.open_at(NOTES)
    win._input.setText("없는 말")
    assert win._empty.isVisibleTo(win._card) and win.results() == []
    QApplication.sendEvent(win._input, QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    assert not win.isVisible() and got == ["b"]
    win.deleteLater()


# ── 메인 창 ──

def note(nid, text="<p>메모</p>", **more):
    return {"id": nid, "version": 1, "color": "#FFE066", "width": 280, "height": 220,
            "content": text, "created_by": ME, "updated_by": ME,
            "updated_at": int(time.time() * 1000), **more}


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(StickyNoteWidget, "bring_to_front",
                        lambda self, *, start_editing=False: None)
    client = ClientBase(ME)
    win = MainWindow(client=client, local_state=LocalStateStore(tmp_path / "ls.json"),
                     role="guest", settings=AppSettings(fold_animation=False))
    client.connectionChanged.emit(True)
    client._dispatch("welcome", {
        "users": [ME], "host": ME, "locks": {}, "heartbeat_ms": 3000,
        "trash_days": 30, "reminders": {}, "folders": [],
        "notes": [note("a", "<p>주간 회의</p><p>견적 회신</p>"), note("b", "<p>점심</p>")]})
    yield win
    win._force_quit = True
    win._teardown_stickies()
    win.close()
    win.deleteLater()
    qapp.processEvents()


def test_the_find_shortcut_opens_the_window_and_brings_the_note_out(window, qapp, monkeypatch):
    taken = []
    monkeypatch.setattr(window, "_take_note_out", taken.append)
    window._on_global_key("find")
    finder = window._quick_find
    assert finder is not None and finder.isVisible()
    finder._input.setText("견적")
    assert finder.results() == ["a"]
    QApplication.sendEvent(finder._input, QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))
    assert taken == ["a"]


def test_the_new_note_shortcut_asks_for_a_note_on_the_mouse_screen(window, monkeypatch):
    made = []
    monkeypatch.setattr(window._client, "create_note", lambda *a, **k: made.append(a))
    screen = QGuiApplication.primaryScreen().availableGeometry()
    window._on_global_key("new_note")
    assert len(made) == 1
    spot, hint = window._take_new_note_spot()
    assert spot is None and hint is not None
    pos = window._new_note_position(280, 220, None, screen_hint=hint)
    assert screen.contains(pos)


def test_the_clipboard_shortcut_makes_a_note_with_the_clipboard(window, qapp, monkeypatch):
    made = []
    monkeypatch.setattr(window._client, "create_note", lambda *a, **k: made.append(a))
    clip = QMimeData()
    clip.setText("복사해 둔 글")
    monkeypatch.setattr(QGuiApplication, "clipboard",
                        staticmethod(lambda: type("C", (), {"mimeData": lambda self: clip})()))
    window._on_global_key("clip_note")
    assert len(made) == 1 and window._clip_note_queue[0].text() == "복사해 둔 글"
    empty = QMimeData()
    shown = []
    monkeypatch.setattr(window._toasts, "show_text", lambda text, *a, **k: shown.append(text))
    monkeypatch.setattr(QGuiApplication, "clipboard",
                        staticmethod(lambda: type("C", (), {"mimeData": lambda self: empty})()))
    window._on_global_key("clip_note")
    assert len(made) == 1 and shown == ["클립보드가 비어 있습니다"]


def test_copy_mime_keeps_text_html_and_images():
    from PySide6.QtGui import QImage
    src = QMimeData()
    src.setHtml("<b>굵게</b>")
    src.setText("굵게")
    out = copy_mime(src)
    assert out is not src and out.html() == "<b>굵게</b>" and out.text() == "굵게"
    img = QMimeData()
    img.setImageData(QImage(4, 4, QImage.Format_ARGB32))
    assert copy_mime(img).hasImage()
    blank = QMimeData()
    blank.setText("   ")
    assert copy_mime(blank) is None and copy_mime(None) is None


def test_a_sticky_note_inserts_clipboard_content_once_it_may_edit(window, qapp):
    sticky = window._stickies["a"]
    sticky.show()
    clip = QMimeData()
    clip.setText("붙여 넣을 글")
    sticky._pending_mime = clip
    sticky.enter_edit_mode()
    assert "붙여 넣을 글" in sticky._text.toPlainText() and sticky._pending_mime is None
