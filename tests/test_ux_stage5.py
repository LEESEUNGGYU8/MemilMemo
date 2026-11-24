"""2026-11-19 추가 기능(offscreen): 로컬 사본 읽기 전용 표시, 끊긴 동안 변경 막기,
새 메모 알림, 새 메모 기본 공개 범위, 설정 창 항목, 글자 다듬기."""
from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app import i18n  # noqa: E402
from app.local_state import LocalStateStore  # noqa: E402
from app.network.client_base import ClientBase  # noqa: E402
from app.replica import ReplicaStore  # noqa: E402
from app.settings import AppSettings  # noqa: E402
from app.ui import confirm, theme  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.settings_dialog import SettingsDialog  # noqa: E402
from app.ui.sticky_note import StickyNoteWidget  # noqa: E402

ME = "지우"
OTHER = "민수"


class FakeClient(ClientBase):
    """보낸 요청을 적어 두기만 한다. 응답은 테스트가 직접 낸다."""

    def __init__(self, nickname: str = ME):
        super().__init__(nickname)
        self.calls: list[tuple] = []

    def create_note(self, color, width, height, folder_id=None,
                    private_owner=None, quiet=False):
        self.calls.append(("create", folder_id, private_owner, quiet))

    def delete_note(self, note_id):
        self.calls.append(("delete", note_id))

    def set_note_privacy(self, note_id, private):
        self.calls.append(("privacy", note_id, private))

    def set_note_folder(self, note_id, folder_id):
        self.calls.append(("folder", note_id, folder_id))

    def acquire_lock(self, note_id):
        self.calls.append(("lock", note_id))

    def release_lock(self, note_id):
        self.calls.append(("release", note_id))

    def update_note(self, note_id, content, color, width, height, expected_version):
        self.calls.append(("update", note_id, content))

    def of(self, kind):
        return [c for c in self.calls if c[0] == kind]


def note(nid: str, text: str, by: str = OTHER, **more) -> dict:
    return {"id": nid, "version": 1, "color": "#FFE066", "width": 300, "height": 240,
            "content": text, "created_by": by, "updated_at": time.time(), **more}


def welcome(*notes: dict, folders=()) -> dict:
    return {"users": [ME, OTHER], "host": OTHER, "folders": list(folders),
            "locks": {}, "heartbeat_ms": 3000, "notes": list(notes)}


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    i18n.set_language("ko")
    theme.apply_theme(app, "")
    yield app


@pytest.fixture
def make_window(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(StickyNoteWidget, "bring_to_front",
                        lambda self, *, start_editing=False: None)
    made: list[MainWindow] = []

    def build(*, role="guest", replica=None, settings=None, nickname=ME):
        client = FakeClient(nickname)
        local = LocalStateStore(tmp_path / f"ls{len(made)}.json")
        # 접속 정보는 창을 만들기 전에 적힌다. 사본은 그 주소에서 받은 것만 보여 준다.
        local.state.last_host_ip = "10.0.0.5"
        win = MainWindow(client=client, local_state=local,
                         role=role, settings=settings or AppSettings(),
                         replica=replica)
        shown: list[str] = []
        monkeypatch.setattr(win, "_show_notification",
                            lambda text, warn=False: shown.append(text))
        win.shown = shown
        made.append(win)
        return win, client

    yield build
    for win in made:
        win._force_quit = True
        win._teardown_stickies()
        win.close()
        win.deleteLater()
    qapp.processEvents()


@pytest.fixture
def replica(tmp_path):
    store = ReplicaStore(tmp_path / "replica.sqlite3")
    store.apply_snapshot(
        [note("a", "공개 메모"), note("b", "내 메모", by=ME, private_owner=ME)],
        [{"id": "f1", "name": "회의", "color": "#BAE1FF"}],
        host_id="H1", host=OTHER, address="10.0.0.5:5555", nickname=ME)
    yield store
    store.close()


# ── 로컬 사본 ───────────────────────────────────────────────────────────

def test_copy_is_shown_read_only_until_the_host_answers(make_window, replica):
    win, client = make_window(replica=replica)

    # 호스트에 붙기 전인데도 메모가 보인다.
    assert set(win._notes) == {"a", "b"} and set(win._cards) == {"a", "b"}
    assert "f1" in win._folders
    assert not win._replica_banner.isHidden()
    assert "사본" in win._replica_text.text()
    # 읽기만 한다. 편집을 시작하지 않고 잠금도 청하지 않는다.
    sticky = win._stickies["a"]
    assert sticky._offline
    sticky._request_edit()
    assert client.of("lock") == [] and not sticky._edit_mode

    # 호스트가 꺼져 있다. 사본은 그대로 보인다.
    client.connectionChanged.emit(False)
    client.connectionChanged.emit(False)
    assert set(win._cards) == {"a", "b"} and not win._replica_banner.isHidden()
    assert win._empty_panel.isHidden()

    # 붙으면 호스트가 보낸 것으로 바뀌고 안내가 사라진다.
    client.connectionChanged.emit(True)
    client.welcomed.emit(welcome(note("a", "공개 메모"), note("c", "새로 온 메모")))
    assert set(win._notes) == {"a", "c"}
    assert win._replica_banner.isHidden()
    assert not win._stickies["a"]._offline


def test_copy_from_another_nickname_is_not_shown(make_window, replica):
    """사본에는 그 닉네임의 '나만 보기' 메모가 들어 있다."""
    win, _client = make_window(replica=replica, nickname="서연")
    assert win._notes == {} and win._replica_banner.isHidden()


def test_host_does_not_show_a_leftover_copy(make_window, replica):
    win, _client = make_window(role="host", replica=replica)
    assert win._notes == {}


def test_empty_copy_shows_nothing(make_window, tmp_path):
    store = ReplicaStore(tmp_path / "empty.sqlite3")
    win, _client = make_window(replica=store)
    assert win._notes == {} and win._replica_banner.isHidden()
    store.close()


# ── 끊긴 동안에는 바꾸지 못한다 ─────────────────────────────────────────

def test_offline_changes_are_refused_with_a_reason(make_window, replica, monkeypatch):
    win, client = make_window(replica=replica)
    asked = []
    monkeypatch.setattr(confirm, "ask", lambda *a, **k: asked.append(a) or True)
    refused = i18n.t("main.status_offline_no_change")

    for attempt in (
            lambda: win._delete_note_confirm("a"),
            lambda: win._on_card_private_toggled("a", True),
            lambda: win._on_card_move_to_folder("a", "f1"),
            lambda: win._on_restore_requested("a", "옛 글"),
            win._create_folder_dialog):
        win._status.clearMessage()
        attempt()
        assert win._status.currentMessage() == refused

    win._selected_notes = {"a", "b"}
    win._bulk_apply_privacy(True)
    win._bulk_apply_folder("f1")
    win._bulk_delete()

    # 호스트로 아무것도 보내지 않았고, 화면의 메모도 그대로다.
    assert client.calls == [] and asked == []
    assert win._notes["a"].get("private_owner") is None
    assert win._notes["a"].get("folder_id") is None
    assert not win._undo_btn.isVisibleTo(win)


def test_changes_go_through_when_connected(make_window, monkeypatch):
    win, client = make_window()
    client.connectionChanged.emit(True)
    client.welcomed.emit(welcome(note("a", "공개 메모")))
    monkeypatch.setattr(confirm, "ask", lambda *a, **k: True)

    win._on_card_private_toggled("a", True)
    win._delete_note_confirm("a")

    assert client.of("privacy") == [("privacy", "a", True)]
    assert client.of("delete") == [("delete", "a")]


# ── 새 메모 알림 ────────────────────────────────────────────────────────

def _announce(client, who: str, note_id: str = "x") -> None:
    client.noteAnnounced.emit({"action": "create", "user": who, "note_id": note_id})


def test_others_new_note_is_announced_once(make_window):
    win, client = make_window()
    client.connectionChanged.emit(True)
    client.welcomed.emit(welcome())

    _announce(client, OTHER)
    assert win.shown == []                 # 잠깐 모았다가 알린다
    win._flush_announcements()

    assert win.shown == ["민수님이 새 메모를 생성했습니다."]
    win._flush_announcements()
    assert len(win.shown) == 1


def test_my_own_note_is_not_announced(make_window):
    win, client = make_window()
    _announce(client, ME)
    win._flush_announcements()
    assert win.shown == [] and not win._announce_timer.isActive()


def test_burst_of_notes_is_one_notice(make_window):
    win, client = make_window()
    for i in range(3):
        _announce(client, OTHER, f"n{i}")
    _announce(client, "서연")
    win._flush_announcements()
    assert win.shown == ["민수님이 새 메모 3개를 생성했습니다.",
                         "서연님이 새 메모를 생성했습니다."]


def test_notice_can_be_turned_off(make_window):
    s = AppSettings()
    s.notify_new_note = False
    win, client = make_window(settings=s)
    _announce(client, OTHER)
    client.noteShared.emit({"user": OTHER, "note_id": "x"})
    win._flush_announcements()
    assert win.shown == []


def test_note_made_public_is_announced(make_window):
    win, client = make_window()
    client.noteShared.emit({"user": OTHER, "note_id": "a"})
    assert win.shown == []                 # 새 메모 알림과 같이 잠깐 모은다
    win._flush_announcements()
    assert win.shown == ["민수님이 메모를 공개했습니다."]

    # 폴더째 공개하면 한 번에 알린다. 내가 한 것은 알리지 않는다.
    win.shown.clear()
    for i in range(4):
        client.noteShared.emit({"user": OTHER, "note_id": f"n{i}"})
    client.noteShared.emit({"user": ME, "note_id": "mine"})
    _announce(client, "서연")
    win._flush_announcements()
    assert win.shown == ["서연님이 새 메모를 생성했습니다.",
                         "민수님이 메모 4개를 공개했습니다."]


def test_notice_does_not_depend_on_windows_notifications(qapp, tmp_path, monkeypatch):
    """Windows 에서 알림을 꺼 둔 PC 에서도 뜨도록, 트레이 풍선을 쓰지 않는다."""
    from app.ui import toast
    monkeypatch.setattr(toast, "user_is_busy", lambda: False)
    monkeypatch.setattr(StickyNoteWidget, "bring_to_front",
                        lambda self, *, start_editing=False: None)
    win = MainWindow(client=FakeClient(), local_state=LocalStateStore(tmp_path / "l.json"),
                     role="guest", settings=AppSettings())

    class Tray:
        said: list = []

        def isVisible(self):
            return True

        def showMessage(self, *args):
            self.said.append(args)

        def hide(self):
            pass

    win._tray = Tray()
    win._show_notification("민수님이 새 메모를 생성했습니다.")
    assert Tray.said == []
    assert [t.text() for t in win._toasts.toasts()] == ["민수님이 새 메모를 생성했습니다."]
    assert win._status.currentMessage() == "민수님이 새 메모를 생성했습니다."
    win._tray = None
    win._toasts.close_all()
    win._force_quit = True
    win.close()
    win.deleteLater()


# ── 새 메모의 기본 공개 범위 ────────────────────────────────────────────

def test_new_note_is_public_by_default(make_window):
    win, client = make_window()
    client.connectionChanged.emit(True)
    client.welcomed.emit(welcome())
    win._create_note("#FFE066")
    assert client.of("create") == [("create", None, None, False)]


def test_new_note_can_default_to_private(make_window):
    s = AppSettings()
    s.new_note_private = True
    win, client = make_window(settings=s)
    client.connectionChanged.emit(True)
    client.welcomed.emit(welcome(folders=[{"id": "f1", "name": "회의"}]))

    win._create_note("#FFE066")
    win._current_folder = "f1"
    win.handle_new_note_request()

    assert client.of("create") == [("create", None, ME, False),
                                   ("create", "f1", ME, False)]


def test_unsent_text_from_a_private_note_stays_private(make_window):
    win, client = make_window()
    client.connectionChanged.emit(True)
    client.welcomed.emit(welcome(note("mine", "내 글", by=ME, private_owner=ME),
                                 note("open", "공개 글")))
    for nid in ("mine", "open"):
        sticky = win._stickies[nid]
        sticky.enter_edit_mode()
        sticky._text.setPlainText(f"{nid} 끊긴 동안 쓴 글")
    client.connectionChanged.emit(False)
    client.calls.clear()

    # 다시 붙었더니 두 메모 모두 지워져 있다. 쓰던 글은 새 메모로 남긴다.
    client.connectionChanged.emit(True)
    client.welcomed.emit(welcome())

    # 나만 보던 메모의 글은 나만 보는 새 메모로, 공개 메모의 글은 설정대로(공개).
    assert sorted(client.of("create"), key=lambda c: str(c[2])) == [
        ("create", None, None, False), ("create", None, ME, False)]


def test_restoring_a_deleted_note_is_quiet(make_window, monkeypatch):
    win, client = make_window()
    client.connectionChanged.emit(True)
    client.welcomed.emit(welcome(note("a", "지울 메모")))
    monkeypatch.setattr(confirm, "ask", lambda *a, **k: True)
    win._delete_note_confirm("a")
    client.noteDeleted.emit("a")

    win._undo_delete()

    # 되돌리느라 다시 만든 메모는 다른 사람에게 '새 메모'로 알리지 않는다.
    assert client.of("create") == [("create", None, None, True)]


# ── 설정 창 ─────────────────────────────────────────────────────────────

def test_settings_dialog_saves_the_new_options(qapp, tmp_path, monkeypatch):
    s = AppSettings()
    monkeypatch.setattr(AppSettings, "save", lambda self, path=None: None)
    dlg = SettingsDialog(s, role="host")
    assert dlg._new_note_scope.currentData() is False
    assert dlg._notify_new_note.isChecked()
    assert [dlg._new_note_scope.itemText(i) for i in range(2)] == ["모두에게 공개", "나만 보기"]

    dlg._new_note_scope.setCurrentIndex(1)
    dlg._notify_new_note.setChecked(False)
    monkeypatch.setattr("app.ui.settings_dialog.set_windows_autostart", lambda on: True)
    monkeypatch.setattr("app.ui.settings_dialog.shell_menu.set_registered",
                        lambda *a, **k: None)
    dlg._on_accept()

    assert s.new_note_private is True and s.notify_new_note is False
    dlg.deleteLater()


def test_backup_tab_warns_about_the_same_disk_and_offers_restore(qapp, tmp_path):
    from app import config
    from PySide6.QtWidgets import QPushButton

    host = SettingsDialog(AppSettings(), role="host")
    hint = host._backup_dir_hint
    # 기본 백업 폴더는 메모 파일과 같은 디스크다.
    assert not hint.isHidden() and hint.text() == i18n.t("settings.note_backup_same_disk")
    other = "Z:\\" if not str(config.default_db_path()).upper().startswith("Z:") else "Y:\\"
    host._backup_dir.setText(other + "backups")
    assert hint.isHidden()
    assert host.backup_folder() == __import__("pathlib").Path(other + "backups")
    host._backup_dir.setText("")
    assert not hint.isHidden() and host.backup_folder() == config.default_backup_dir()

    asked = []
    host.restoreRequested.connect(lambda: asked.append(True))
    restore = [b for b in host.findChildren(QPushButton)
               if b.text() == i18n.t("settings.btn_restore")]
    assert len(restore) == 1
    restore[0].click()
    assert asked == [True]

    # 참여한 PC 에도 같은 단추가 있다. 받아 둔 사본을 거기서 꺼낸다.
    guest = SettingsDialog(AppSettings(), role="guest")
    assert guest._backup_dir_hint.isHidden()
    assert len([b for b in guest.findChildren(QPushButton)
                if b.text() == i18n.t("settings.btn_restore")]) == 1
    host.deleteLater()
    guest.deleteLater()


def test_restore_window_lists_backups_newest_first(qapp, tmp_path):
    from app.backup import list_backups
    from app.replica import backup_source
    from app.ui.copies_dialog import CopiesDialog
    older = tmp_path / "notes_20261118_090000.sqlite3"
    newer = tmp_path / "notes_20261119_103000.sqlite3"
    older.write_bytes(b"x" * 2048)
    newer.write_bytes(b"x" * (3 * 1024 * 1024))

    found = [backup_source(p) for p in list_backups(tmp_path)]
    dlg = CopiesDialog(found, host_notes=None, can_takeover=False, can_restore=True)
    assert dlg.selected_path() == str(newer)    # 가장 새것이 골라져 있다
    assert "3.0 MB" in dlg._pick.itemText(0) and "2 KB" in dlg._pick.itemText(1)
    dlg._pick.setCurrentIndex(1)
    assert dlg.selected_path() == str(older)
    # 읽을 수 없는 파일이라 메모가 없다. 그런 것으로는 복원하지 못한다.
    assert dlg._takeover_btn.text() == i18n.t("restore.btn_restore")
    assert not dlg._takeover_btn.isEnabled()
    empty = CopiesDialog([], host_notes=None, can_takeover=False, can_restore=True)
    assert empty.selected_path() == "" and not empty._pick.isEnabled()
    assert empty._pick.itemText(0) == i18n.t("restore.empty")
    dlg.deleteLater()
    empty.deleteLater()


# ── 글자 다듬기 ─────────────────────────────────────────────────────────

def test_text_is_smoothed_everywhere(qapp):
    from PySide6.QtWidgets import QLabel, QTextEdit
    font = qapp.font()
    assert font.hintingPreference() == QFont.PreferNoHinting
    assert font.styleStrategy() & QFont.PreferAntialias
    # 스타일시트로 글꼴을 바꾼 위젯과 메모 본문도 물려받는다.
    label = QLabel("글자")
    label.setStyleSheet("font-size: 14px; font-weight: 700;")
    label.ensurePolished()
    edit = QTextEdit()
    f = edit.font()
    f.setPointSize(13)
    edit.setFont(f)
    for got in (label.font(), edit.font()):
        assert got.hintingPreference() == QFont.PreferNoHinting
    label.deleteLater()
    edit.deleteLater()
