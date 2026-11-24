"""혼자 쓰기: 공유 PC 처럼 이 PC 에 메모를 두되 포트를 열지 않는다. 설정에서 접속 정보를
바꾸거나 공유를 시작한다."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMenu, QPushButton  # noqa: E402

import main as app_main  # noqa: E402
from app import config, i18n, shell_menu  # noqa: E402
from app.database import SqliteRepository  # noqa: E402
from app.local_state import LocalStateStore  # noqa: E402
from app.network.client_base import ClientBase  # noqa: E402
from app.network.host_client import HostLocalClient  # noqa: E402
from app.network.host_server import HostServer  # noqa: E402
from app.settings import AppSettings  # noqa: E402
from app.share_auth import ShareAuth  # noqa: E402
from app.sync.history_tracker import HistoryTracker  # noqa: E402
from app.sync.lock_manager import LockManager  # noqa: E402
from app.ui import confirm, password_dialog, settings_dialog, theme  # noqa: E402
from app.ui.connect_dialog import ConnectDialog, ConnectionChoice  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.password_dialog import ShareStartDialog  # noqa: E402
from app.ui.settings_dialog import SettingsDialog  # noqa: E402
from app.ui.sticky_note import StickyNoteWidget  # noqa: E402

ME = "지우"


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    i18n.set_language("ko")
    theme.apply_theme(app, "")
    yield app


class FakeClient(ClientBase):
    def __init__(self):
        super().__init__(ME)

    def release_lock(self, note_id):
        pass


def make_window(qapp, tmp_path, monkeypatch, *, solo: bool):
    monkeypatch.setattr(StickyNoteWidget, "bring_to_front",
                        lambda self, *, start_editing=False: None)
    client = FakeClient()
    win = MainWindow(client=client,
                     local_state=LocalStateStore(tmp_path / "local_state.json"),
                     role="host", settings=AppSettings(), solo=solo)
    client.connectionChanged.emit(True)
    client._dispatch("welcome", {
        "users": [ME], "host": ME, "locks": {}, "heartbeat_ms": 3000,
        "trash_days": config.TRASH_KEEP_DAYS, "folders": [], "notes": []})
    qapp.processEvents()
    return win


def close_window(qapp, win) -> None:
    win._force_quit = True
    win._teardown_stickies()
    win.close()
    win.deleteLater()
    qapp.processEvents()


# ── 접속 창 ─────────────────────────────────────────────────────────────

def test_the_connect_window_offers_using_it_alone(qapp):
    dlg = ConnectDialog(last_nickname=ME)
    assert dlg._btn_solo.text().startswith(i18n.t("connect.role_solo_title"))
    assert dlg._solo_form.isHidden() and not dlg._advanced_btn.isHidden()
    dlg._btn_solo.click()
    assert dlg._btn_solo.isChecked() and not dlg._btn_host.isChecked()
    assert not dlg._solo_form.isHidden() and dlg._host_form.isHidden()
    assert dlg._guest_form.isHidden()
    assert dlg._advanced_btn.isHidden()             # 포트도 연결 확인 주기도 없다
    dlg._solo_nickname.setText("  서연 ")
    dlg.accept()
    choice = dlg.choice()
    assert (choice.mode, choice.nickname, choice.password) == ("solo", "서연", None)
    # 다시 다른 방식을 고르면 고급 설정이 돌아온다.
    again = ConnectDialog(last_nickname=ME)
    again._btn_solo.click()
    again._btn_host.click()
    assert not again._advanced_btn.isHidden() and again._solo_form.isHidden()
    dlg.deleteLater()
    again.deleteLater()


# ── 적어 두기 ───────────────────────────────────────────────────────────

def test_solo_is_remembered_until_the_connection_is_changed(tmp_path):
    store = LocalStateStore(tmp_path / "local_state.json")
    # 전에 공유하며 걸어 둔 비밀번호가 있었다. 혼자 쓰는 동안 건드리지 않는다.
    store.state.share_auth = ShareAuth.from_password("회의실").to_dict()
    store.state.last_host_port = 5601
    assert app_main._saved_choice(store) is None
    app_main._persist_choice(store, ConnectionChoice(mode="solo", nickname=ME,
                                                     port=config.DEFAULT_PORT))
    again = LocalStateStore(tmp_path / "local_state.json")
    assert again.state.last_mode == "solo" and again.state.nickname == ME
    assert again.state.share_auth is not None and again.state.last_host_port == 5601
    choice = app_main._saved_choice(again)
    assert (choice.mode, choice.nickname) == ("solo", ME)
    again.state.last_mode = ""                      # 접속 정보 바꾸기
    assert app_main._saved_choice(again) is None


# ── 서버 ────────────────────────────────────────────────────────────────

def test_a_solo_start_opens_no_port_and_still_keeps_notes(qapp, tmp_path):
    repo = SqliteRepository(tmp_path / "notes.sqlite3")
    server = HostServer(repo, LockManager(idle_timeout_ms=60_000),
                        HistoryTracker(repo), host_nickname=ME)
    server.start_local()
    assert not server.is_listening() and server._server.serverPort() == 0
    assert not server._heartbeat_timer.isActive()   # 붙을 PC 가 없다
    assert server._trash_timer.isActive()           # 휴지통은 그대로 돈다
    client = HostLocalClient(server, ME)
    seen: list[tuple[str, dict]] = []
    server.attach_local_host()
    server.set_local_subscriber(lambda kind, data: seen.append((kind, data)))
    client._send_hello()
    client.create_note("#FFE066", 280, 260)
    qapp.processEvents()
    assert any(kind == "note_created" for kind, _data in seen)
    assert len(repo.list_notes()) == 1 and repo.list_notes()[0].created_by == ME
    server.stop()
    repo.close()


# ── 메인 창 ─────────────────────────────────────────────────────────────

def test_the_main_window_shows_no_participants_when_solo(qapp, tmp_path, monkeypatch):
    win = make_window(qapp, tmp_path, monkeypatch, solo=True)
    assert win.is_solo() and win._sessions_btn.isHidden()
    assert win._conn_badge._state.text() == i18n.t("main.state_solo")
    assert i18n.t("main.status_online_count", count=1) not in \
        win._conn_badge._detail.text()
    menu = QMenu()
    win._fill_tray_menu(menu)
    texts = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert i18n.t("main.tray_sessions") not in texts
    assert i18n.t("main.tray_change_connection") in texts
    close_window(qapp, win)

    shared = make_window(qapp, tmp_path, monkeypatch, solo=False)
    assert not shared._sessions_btn.isHidden()
    assert shared._conn_badge._state.text() == i18n.t("main.state_connected")
    menu = QMenu()
    shared._fill_tray_menu(menu)
    assert i18n.t("main.tray_sessions") in [a.text() for a in menu.actions()]
    close_window(qapp, shared)


def test_sharing_is_started_from_solo_only_after_the_question(qapp, tmp_path,
                                                              monkeypatch):
    win = make_window(qapp, tmp_path, monkeypatch, solo=True)
    asked: list[str] = []
    win.shareRequested.connect(asked.append)
    monkeypatch.setattr(password_dialog, "ask_share_start", lambda parent: None)
    win._start_sharing()                            # 그만뒀다
    assert asked == []
    monkeypatch.setattr(password_dialog, "ask_share_start", lambda parent: "회의실")
    win._start_sharing()
    monkeypatch.setattr(password_dialog, "ask_share_start", lambda parent: "")
    win._start_sharing()                            # 비밀번호 없이
    assert asked == ["회의실", ""]
    close_window(qapp, win)

    shared = make_window(qapp, tmp_path, monkeypatch, solo=False)
    shared.shareRequested.connect(asked.append)
    shared._start_sharing()                         # 이미 공유 중이면 아무 일도 없다
    assert asked == ["회의실", ""]
    close_window(qapp, shared)


def test_changing_the_connection_from_settings_asks_again_next_time(qapp, tmp_path,
                                                                    monkeypatch):
    win = make_window(qapp, tmp_path, monkeypatch, solo=True)
    win._local.state.last_mode = "solo"
    told: list = []
    monkeypatch.setattr(confirm, "notify",
                        lambda parent, title, text, **k: told.append(text))
    win._reset_connection()
    assert win._local.state.last_mode == ""
    assert told == [i18n.t("main.msg_reset_connection_body")]
    close_window(qapp, win)


# ── 설정 ────────────────────────────────────────────────────────────────

def test_the_network_page_has_the_two_actions(qapp, monkeypatch):
    monkeypatch.setattr(settings_dialog, "set_windows_autostart", lambda v: True)
    monkeypatch.setattr(shell_menu, "set_registered", lambda *a, **k: True)
    change, share = (i18n.t("main.tray_change_connection"),
                     i18n.t("settings.btn_share_start"))

    def buttons(dlg) -> dict[str, QPushButton]:
        return {b.text(): b for b in dlg.findChildren(QPushButton)}

    solo = SettingsDialog(AppSettings(), role="host", solo=True)
    fired: list[str] = []
    solo.resetConnectionRequested.connect(lambda: fired.append("change"))
    solo.shareRequested.connect(lambda: fired.append("share"))
    assert change in buttons(solo) and share in buttons(solo)
    buttons(solo)[share].click()
    buttons(solo)[change].click()
    assert fired == ["share", "change"]
    # 공유 중이거나 참여 중이면 '공유 시작'은 없고 '접속 정보 바꾸기'만 있다.
    for role in ("host", "guest"):
        dlg = SettingsDialog(AppSettings(), role=role)
        assert change in buttons(dlg) and share not in buttons(dlg)
        dlg.deleteLater()
    solo.deleteLater()


def test_the_share_question_takes_an_optional_password(qapp):
    dlg = ShareStartDialog()
    assert dlg.windowTitle() == i18n.t("settings.btn_share_start")
    assert dlg.password() == ""
    dlg._field.setText("회의실")
    assert dlg.password() == "회의실"
    dlg.deleteLater()


def test_every_language_has_the_words(qapp):
    from app.locales import en, ja, ko
    for module in (ko, en, ja):
        for key in ("connect.role_solo_title", "connect.role_solo_subtitle",
                    "main.state_solo", "settings.btn_share_start", "share.body"):
            assert module.STRINGS.get(key), (module.__name__, key)
