"""수정 알림, 새 설정 창, 자동 업데이트, 창 둘레의 판 없애기(offscreen).

수정 알림은 편집 한 번에 한 번 뜬다. 편집 중에 저장된 글이 처음 도착했을 때
띄우고, 그 뒤의 저장과 편집이 끝날 때 오는 이력 항목으로는 다시 띄우지 않는다."""
from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

from app import config, i18n  # noqa: E402
from app.local_state import LocalStateStore  # noqa: E402
from app.network.client_base import ClientBase  # noqa: E402
from app.settings import AppSettings  # noqa: E402
from app.ui import dwm, theme  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.settings_dialog import SettingsDialog, Switch  # noqa: E402
from app.ui.sticky_note import StickyNoteWidget  # noqa: E402

ME = "지우"
OTHER = "민수"


class FakeClient(ClientBase):
    def __init__(self, nickname: str = ME):
        super().__init__(nickname)
        self.calls: list[tuple] = []

    def acquire_lock(self, note_id):
        self.calls.append(("lock", note_id))

    def release_lock(self, note_id):
        self.calls.append(("release", note_id))


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    i18n.set_language("ko")
    theme.apply_theme(app, "")
    yield app


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(StickyNoteWidget, "bring_to_front",
                        lambda self, *, start_editing=False: None)
    client = FakeClient()
    win = MainWindow(client=client,
                     local_state=LocalStateStore(tmp_path / "local_state.json"),
                     role="guest", settings=AppSettings())
    shown: list[str] = []
    monkeypatch.setattr(win, "_show_notification",
                        lambda text, warn=False: shown.append(text))
    win.shown = shown
    yield win
    win._force_quit = True
    win._teardown_stickies()
    win.close()
    win.deleteLater()
    qapp.processEvents()


def edited(client: FakeClient, who: str, note_id: str, after: str,
           before: str = "<p>예전 내용</p>") -> None:
    """호스트가 편집이 끝났을 때 보내는 이력 항목."""
    client._dispatch("history_appended", {"entry": {
        "note_id": note_id, "user": who, "action": "update",
        "content_before": before, "content_after": after,
        "color_before": "#FFE066", "color_after": "#FFE066",
        "started_at": 1, "ended_at": 2}})


# ── 수정 알림 ───────────────────────────────────────────────────────────

def test_edit_by_someone_else_is_announced_with_the_title(window):
    edited(window._client, OTHER, "n1", "<p>주간 회의</p><p>3시로 변경</p>")
    assert window._announce_timer.isActive()
    window._flush_announcements()
    assert window.shown == ["민수님이 '주간 회의' 메모를 수정했습니다."]


def a_note(note_id: str, content: str, by: str = OTHER, version: int = 1,
           **more) -> dict:
    return {"id": note_id, "version": version, "color": "#FFE066", "width": 300,
            "height": 240, "content": content, "created_by": OTHER,
            "updated_by": by, "updated_at": time.time(), "private_owner": None,
            "folder_id": None, **more}


def welcome(window, *notes) -> None:
    window._client._dispatch("welcome", {
        "users": [ME, OTHER], "host": OTHER, "folders": [], "locks": {},
        "heartbeat_ms": 3000, "notes": list(notes)})


def typed(window, note_id: str, content: str, version: int, by: str = OTHER) -> None:
    """편집 중에 저장된 글이 도착했다."""
    window._client._dispatch("note_updated", {
        "note": a_note(note_id, content, by, version)})


def test_edit_is_announced_while_the_editor_is_still_in_the_note(window):
    # 쓰던 사람이 메모에 커서를 둔 채 있어도(편집이 끝나지 않아도) 알린다.
    welcome(window, a_note("n1", "주간 회의\n화요일 2시"))
    window._client._dispatch("lock_held", {"note_id": "n1", "holder": OTHER})
    typed(window, "n1", "주간 회의\n화요일 3시", 2)
    assert window._announce_timer.isActive()
    window._flush_announcements()
    assert window.shown == ["민수님이 '주간 회의' 메모를 수정했습니다."]

    # 계속 쓰는 동안과 편집이 끝날 때에는 다시 알리지 않는다.
    for i in range(5):
        typed(window, "n1", "주간 회의\n화요일 3시" + "가" * (i + 1), 3 + i)
    edited(window._client, OTHER, "n1", "주간 회의\n화요일 3시가가가가가",
           before="주간 회의\n화요일 2시")
    window._client._dispatch("lock_released", {"note_id": "n1"})
    window._flush_announcements()
    assert len(window.shown) == 1


def test_long_edit_is_announced_once_however_long_it_takes(window):
    welcome(window, a_note("n1", "주간 회의"))
    window._client._dispatch("lock_held", {"note_id": "n1", "holder": OTHER})
    typed(window, "n1", "주간 회의 1", 2)
    window._flush_announcements()
    # 한참 멈췄다가 이어 쓴다(조용한 시간이 지났다). 같은 편집이므로 조용하다.
    window._edit_notified[(OTHER, "n1")] -= window._EDIT_NOTICE_COOLDOWN + 1
    typed(window, "n1", "주간 회의 12", 3)
    window._flush_announcements()
    assert len(window.shown) == 1

    # 편집을 끝내고 한참 뒤에 다시 고치면 새로 알린다.
    window._client._dispatch("lock_released", {"note_id": "n1"})
    window._edit_notified[(OTHER, "n1")] -= window._EDIT_NOTICE_COOLDOWN + 1
    window._client._dispatch("lock_held", {"note_id": "n1", "holder": OTHER})
    typed(window, "n1", "주간 회의 123", 4)
    window._flush_announcements()
    assert len(window.shown) == 2


def test_filling_a_new_note_is_never_an_edit_however_many_saves(window):
    welcome(window, a_note("n1", ""))
    window._client._dispatch("lock_held", {"note_id": "n1", "holder": OTHER})
    typed(window, "n1", "첫 줄", 2)
    typed(window, "n1", "첫 줄\n둘째 줄", 3)
    window._edit_notified[(OTHER, "n1")] -= window._EDIT_NOTICE_COOLDOWN + 1
    typed(window, "n1", "첫 줄\n둘째 줄\n셋째 줄", 4)
    edited(window._client, OTHER, "n1", "첫 줄\n둘째 줄\n셋째 줄", before="")
    window._client._dispatch("lock_released", {"note_id": "n1"})
    window._flush_announcements()
    assert window.shown == []


def test_changes_other_than_the_text_and_my_own_saves_are_silent(window):
    welcome(window, a_note("n1", "주간 회의"))
    # 다른 사람이 폴더나 공개 범위만 바꿨다(내용은 그대로).
    typed(window, "n1", "주간 회의", 2)
    # 내가 저장한 글이 돌아왔다.
    typed(window, "n1", "주간 회의 (내가 고침)", 3, by=ME)
    window._flush_announcements()
    assert window.shown == [] and not window._announce_timer.isActive()

    window._settings.notify_note_edit = False
    typed(window, "n1", "남이 고침", 4)
    window._flush_announcements()
    assert window.shown == []


def test_my_own_edit_and_switched_off_setting_are_silent(window):
    edited(window._client, ME, "n1", "<p>내가 고침</p>")
    window._flush_announcements()
    assert window.shown == []

    window._settings.notify_note_edit = False
    edited(window._client, OTHER, "n1", "<p>남이 고침</p>")
    window._flush_announcements()
    assert window.shown == []


def test_filling_an_empty_note_is_not_an_edit(window):
    # 새 메모의 첫 글, 지웠다 되돌린 메모, 사본에서 가져온 메모가 이 길로 채워진다.
    edited(window._client, OTHER, "n1", "<p>첫 글</p>", before="")
    window._flush_announcements()
    assert window.shown == []
    # 만든 것 자체는 따로 알린다(전과 같다).
    window._client._dispatch("history_appended", {"entry": {
        "note_id": "n1", "user": OTHER, "action": "create"}})
    window._flush_announcements()
    assert window.shown == ["민수님이 새 메모를 생성했습니다."]


def test_repeated_edits_of_one_note_are_announced_once_for_a_while(window):
    # 할 일 상자를 하나씩 누르면 누를 때마다 편집 한 번이다.
    for i in range(4):
        edited(window._client, OTHER, "n1", f"<p>할 일 {i}</p>")
    window._flush_announcements()
    assert window.shown == ["민수님이 '할 일 0' 메모를 수정했습니다."]

    edited(window._client, OTHER, "n1", "<p>또 고침</p>")
    window._flush_announcements()
    assert len(window.shown) == 1

    # 시간이 지나면 다시 알린다.
    key = (OTHER, "n1")
    window._edit_notified[key] -= window._EDIT_NOTICE_COOLDOWN + 1
    edited(window._client, OTHER, "n1", "<p>나중에 고침</p>")
    window._flush_announcements()
    assert window.shown[-1] == "민수님이 '나중에 고침' 메모를 수정했습니다."


def test_several_notes_edited_at_once_make_one_notice(window):
    edited(window._client, OTHER, "n1", "<p>하나</p>")
    edited(window._client, OTHER, "n2", "<p>둘</p>")
    edited(window._client, "서연", "n3", "")
    window._flush_announcements()
    assert window.shown == ["민수님이 메모 2개를 수정했습니다.",
                            "서연님이 메모를 수정했습니다."]


def test_long_title_is_cut_in_the_notice(window):
    edited(window._client, OTHER, "n1", "<p>" + "가" * 60 + "</p>")
    window._flush_announcements()
    text = window.shown[0]
    assert "…" in text and len(text) < 60


# ── 설정 창 ─────────────────────────────────────────────────────────────

def test_settings_dialog_has_a_section_list_and_an_icon_on_about(qapp):
    dlg = SettingsDialog(AppSettings(), role="host")
    titles = [dlg._nav.button(i).text() for i in range(dlg._pages.count())]
    assert titles == ["일반", "메모", "접기", "알림", "네트워크", "백업", "정보"]
    assert dlg._pages.currentIndex() == 0 and dlg._nav.button(0).isChecked()

    dlg.show_page(6)
    assert dlg._pages.currentIndex() == 6 and dlg._nav.button(6).isChecked()
    about = dlg._pages.widget(6)
    labels = about.findChildren(QLabel)
    # 프로그램 이름 위에 큰 아이콘이 있다.
    icon = next(lbl for lbl in labels if lbl.pixmap() and not lbl.pixmap().isNull())
    name = next(lbl for lbl in labels if lbl.text() == i18n.t("app.name"))
    assert labels.index(icon) < labels.index(name)
    assert icon.pixmap().width() >= 80
    assert any(config.APP_VERSION in lbl.text() for lbl in labels)

    # 구역을 누르면 그 쪽이 보인다.
    dlg._nav.button(3).click()
    assert dlg._pages.currentIndex() == 3
    # 쓰는 법을 설명하는 글은 두지 않는다.
    every = " ".join(lbl.text() for lbl in dlg.findChildren(QLabel))
    assert "두 번 누르면" not in every and " · " not in every
    dlg.deleteLater()


def test_settings_dialog_saves_notice_and_update_options(qapp, monkeypatch):
    s = AppSettings()
    s.auto_update_tried = "1.0.2"
    monkeypatch.setattr(AppSettings, "save", lambda self, path=None: None)
    monkeypatch.setattr("app.ui.settings_dialog.set_windows_autostart", lambda on: True)
    monkeypatch.setattr("app.ui.settings_dialog.shell_menu.set_registered",
                        lambda *a, **k: None)
    dlg = SettingsDialog(s, role="host")
    assert dlg._notify_note_edit.isChecked()        # 기본은 알림 켜짐
    assert not dlg._auto_update.isChecked()         # 기본은 자동 업데이트 꺼짐
    assert isinstance(dlg._auto_update, Switch)

    dlg._notify_note_edit.setChecked(False)
    dlg._auto_update.setChecked(True)
    dlg._on_accept()
    assert s.notify_note_edit is False and s.auto_update is True
    # 다시 켰으면 지난번에 끝내지 못한 버전도 새로 시도한다.
    assert s.auto_update_tried == ""
    dlg.deleteLater()

    # 손대지 않은 값은 그대로 둔다.
    s.auto_update_tried = "1.0.3"
    again = SettingsDialog(s, role="host")
    again._on_accept()
    assert s.auto_update is True and s.auto_update_tried == "1.0.3"
    again.deleteLater()


def test_switch_toggles_from_anywhere_on_its_row(qapp):
    dlg = SettingsDialog(AppSettings(), role="host")
    dlg.show()
    dlg.show_page(3)
    qapp.processEvents()
    switch = dlg._notify_new_note
    assert switch.isChecked()
    assert switch.accessibleName() == i18n.t("settings.check_notify_new_note")
    row = switch.parentWidget()
    QTest.mouseClick(row, Qt.LeftButton, Qt.NoModifier, QPoint(20, row.height() // 2))
    assert not switch.isChecked()
    QTest.mouseClick(switch, Qt.LeftButton, Qt.NoModifier,
                     switch.rect().center())
    assert switch.isChecked()
    QTest.keyClick(switch, Qt.Key_Space)
    assert not switch.isChecked()
    dlg.close()
    dlg.deleteLater()


def test_backup_buttons_follow_the_role(qapp):
    def texts(dlg):
        return [b.text() for b in dlg.findChildren(QPushButton)]

    host = SettingsDialog(AppSettings(), role="host")
    guest = SettingsDialog(AppSettings(), role="guest")
    for key in ("settings.btn_restore", "settings.btn_backup_now"):
        assert i18n.t(key) in texts(host)
    # 받아 둔 사본은 '백업에서 복원'에 합쳤다. 참여한 PC 에도 그 단추가 있다.
    assert i18n.t("settings.btn_restore") in texts(guest)
    assert i18n.t("settings.btn_backup_now") not in texts(guest)
    # 참여한 PC 에는 백업하지 않는 까닭을 적는다.
    every = " ".join(lbl.text() for lbl in guest.findChildren(QLabel))
    assert i18n.t("settings.note_backup_guest") in every
    host.deleteLater()
    guest.deleteLater()


def test_settings_file_keeps_the_new_options(tmp_path):
    path = tmp_path / "settings.json"
    s = AppSettings()
    assert (s.notify_note_edit, s.auto_update, s.auto_update_tried) == (True, False, "")
    s.notify_note_edit = False
    s.auto_update = True
    s.auto_update_tried = "1.0.2"
    s.save(path)
    again = AppSettings.load(path)
    assert (again.notify_note_edit, again.auto_update,
            again.auto_update_tried) == (False, True, "1.0.2")


# ── 자동 업데이트 ───────────────────────────────────────────────────────

class _FakeWindow:
    def __init__(self):
        self.editing = False
        self.notices: list[str] = []
        self.quit_calls = 0

    def is_editing(self) -> bool:
        return self.editing

    def notify(self, text: str) -> None:
        self.notices.append(text)

    def _quit_app(self) -> None:
        self.quit_calls += 1


@pytest.fixture
def update_env(qapp, monkeypatch):
    import main

    made: list = []

    class FakeDownloader:
        def __init__(self, info, parent=None):
            from PySide6.QtCore import QObject, Signal

            class _Signals(QObject):
                installerReady = Signal(str)
                error = Signal(str)

            self._signals = _Signals()
            self.installerReady = self._signals.installerReady
            self.error = self._signals.error
            self.info = info
            self.started = False
            self.verified_sha256 = "ab" * 32
            made.append(self)

        def start(self):
            self.started = True

    launched: list[str] = []
    prompted: list[dict] = []
    monkeypatch.setattr(main, "UpdateDownloader", FakeDownloader)
    monkeypatch.setattr(main, "launch_installer",
                        lambda path, sha="": launched.append(path) or True)
    monkeypatch.setattr(main, "_prompt_and_install",
                        lambda app, window, info: prompted.append(info))
    monkeypatch.setattr(main, "_AUTO_UPDATE_RETRY_MS", 30)
    monkeypatch.setattr(AppSettings, "save", lambda self, path=None: None)
    return main, made, launched, prompted


INFO = {"version": "1.0.2", "download_url": "https://example.invalid/setup.exe",
        "version_url": "", "notes": ""}


def test_auto_update_installs_without_asking(qapp, update_env):
    main, made, launched, prompted = update_env
    settings = AppSettings()
    settings.auto_update = True
    win = _FakeWindow()
    main._install_quietly(qapp, win, settings, dict(INFO))

    assert prompted == [] and len(made) == 1 and made[0].started
    assert win.notices == ["1.0.2 업데이트를 설치합니다."]
    assert launched == []                          # 다 받기 전에는 설치하지 않는다
    made[0].installerReady.emit(r"C:\Temp\MemilMemo-Setup-1.0.2.exe")
    assert launched == [r"C:\Temp\MemilMemo-Setup-1.0.2.exe"]
    assert settings.auto_update_tried == "1.0.2"
    QTest.qWait(300)
    # 스스로 끝내지 않는다. 설치 허용을 거절하면 앱은 그대로 남아야 하고, 설치가
    # 시작되면 설치 프로그램이 앱을 닫는다.
    assert win.quit_calls == 0


def test_auto_update_waits_for_editing_to_finish(qapp, update_env):
    main, made, launched, _prompted = update_env
    settings = AppSettings()
    settings.auto_update = True
    win = _FakeWindow()
    win.editing = True
    main._install_quietly(qapp, win, settings, dict(INFO))
    made[0].installerReady.emit("setup.exe")
    QTest.qWait(150)
    assert launched == [] and win.quit_calls == 0 and settings.auto_update_tried == ""
    win.editing = False
    QTest.qWait(400)
    assert launched == ["setup.exe"] and win.quit_calls == 0
    assert settings.auto_update_tried == "1.0.2"


def test_failed_download_stays_quiet_and_can_be_retried(qapp, update_env):
    main, made, launched, _prompted = update_env
    settings = AppSettings()
    settings.auto_update = True
    win = _FakeWindow()
    main._install_quietly(qapp, win, settings, dict(INFO))
    made[0].error.emit("network down")
    QTest.qWait(80)
    assert launched == [] and win.quit_calls == 0
    assert settings.auto_update_tried == ""        # 다음 실행 때 다시 시도한다


def test_update_check_chooses_between_asking_and_installing(qapp, update_env, monkeypatch):
    main, made, _launched, prompted = update_env

    class FakeChecker:
        last = None

        def __init__(self, version, parent=None):
            from PySide6.QtCore import QObject, Signal

            class _Signals(QObject):
                updateAvailable = Signal(dict)
                upToDate = Signal()
                error = Signal(str)

            self._signals = _Signals()
            self.updateAvailable = self._signals.updateAvailable
            self.upToDate = self._signals.upToDate
            self.error = self._signals.error
            FakeChecker.last = self

        def check(self):
            pass

    monkeypatch.setattr(main, "UpdateChecker", FakeChecker)
    monkeypatch.setattr(main.QTimer, "singleShot",
                        staticmethod(lambda ms, fn: fn()))

    def run(settings) -> None:
        main._schedule_update_check(qapp, _FakeWindow(), settings)
        FakeChecker.last.updateAvailable.emit(dict(INFO))

    off = AppSettings()
    run(off)
    assert len(prompted) == 1 and made == []       # 꺼져 있으면 전처럼 묻는다

    on = AppSettings()
    on.auto_update = True
    run(on)
    assert len(prompted) == 1 and len(made) == 1   # 켜져 있으면 묻지 않는다

    # 이 버전을 한 번 설치하려다 끝내지 못했으면(설치 허용을 거절한 경우 등)
    # 실행할 때마다 되풀이하지 않고 묻는 창으로 돌아간다.
    stuck = AppSettings()
    stuck.auto_update = True
    stuck.auto_update_tried = "1.0.2"
    run(stuck)
    assert len(prompted) == 2 and len(made) == 1


# ── 창 둘레의 판 ────────────────────────────────────────────────────────

def test_folded_note_and_toast_ask_windows_for_a_bare_window(qapp, monkeypatch):
    from app.ui import toast

    calls: list[tuple[int, bool]] = []
    monkeypatch.setattr(dwm, "set_bare",
                        lambda hwnd, bare=True: calls.append((int(hwnd), bool(bare))))
    monkeypatch.setattr(dwm, "hide_border", lambda hwnd: None)
    settings = AppSettings()
    settings.fold_animation = False
    w = StickyNoteWidget({"id": "n1", "version": 1, "color": "#FFE066",
                          "width": 280, "height": 260, "content": "메모"},
                         ME, settings=settings)
    w.apply_local_state(200, 160)
    w.show()
    hwnd = int(w.winId())
    assert calls[-1] == (hwnd, False)              # 펼친 메모는 그림자를 둔다
    w.fold()
    assert (hwnd, True) in calls                   # 접히면 둘레에 아무것도 두르지 않는다
    w.unfold()
    assert calls[-1] == (hwnd, False)
    w._teardown = True
    w.close()

    monkeypatch.setattr(toast, "user_is_busy", lambda: False)
    manager = toast.ToastManager()
    manager.show_text("알림")
    note = manager.toasts()[0]
    assert (int(note.winId()), True) in calls
    manager.close_all()


def test_dwm_helpers_do_nothing_without_a_window():
    dwm.set_bare(0)
    dwm.hide_border(0)


# ── 창을 만드는 동안 따로 뜨는 창 ────────────────────────────────────────

def test_settings_dialog_shows_no_stray_windows(qapp):
    """부모 없는 위젯에 setVisible(True) 를 부르면 그 위젯이 잠깐 독립된 창으로
    뜬다. 설정 창을 열 때 안내 글이 그렇게 번쩍였다."""
    from PySide6.QtCore import QEvent, QObject
    from PySide6.QtWidgets import QDialog, QWidget

    stray: list[str] = []

    class Watch(QObject):
        def eventFilter(self, obj, event):  # noqa: N802
            if (event.type() == QEvent.Show and isinstance(obj, QWidget)
                    and obj.isWindow() and not isinstance(obj, QDialog)):
                stray.append(f"{type(obj).__name__}:{obj.objectName()}")
            return False

    watch = Watch()
    qapp.installEventFilter(watch)
    try:
        for role in ("host", "guest"):
            dlg = SettingsDialog(AppSettings(), role=role)
            dlg.show()
            for i in range(dlg._pages.count()):
                dlg.show_page(i)
                qapp.processEvents()
            dlg.close()
            dlg.deleteLater()
    finally:
        qapp.removeEventFilter(watch)
    assert stray == []


# ── 줄 밖으로 밀려 잘리는 칸 ─────────────────────────────────────────────

def test_no_settings_control_sticks_out_of_its_row(qapp):
    """백업 주기의 단위 칸이 줄 밖으로 밀려 오른쪽이 잘렸다. 어느 쪽에서도 칸이
    제 자리(부모) 밖으로 나가지 않아야 한다."""
    from PySide6.QtWidgets import QWidget

    from app.ui import settings_dialog

    try:
        for lang in ("en", "ja", "ko"):
            i18n.set_language(lang)
            # 앱이 쓰는 글꼴로 잰다. 글꼴 없이 재면 글자 폭이 실제와 다르다.
            theme.apply_theme(qapp, theme.load_application_fonts(lang))
            for role in ("host", "guest"):
                dlg = SettingsDialog(AppSettings(), role=role)
                dlg.show()
                for i in range(dlg._pages.count()):
                    dlg.show_page(i)
                    qapp.processEvents()
                    page = dlg._pages.widget(i)
                    for w in page.findChildren(QWidget):
                        if not w.isVisibleTo(page):
                            continue
                        g, parent = w.geometry(), w.parentWidget()
                        assert (g.left() >= 0 and g.top() >= 0
                                and g.right() < parent.width()
                                and g.bottom() < parent.height()), (
                            lang, role, i, type(w).__name__, g, parent.size())
                # 숫자 칸과 단위 칸은 폭이 정해져 있고, 합이 다른 줄의 칸과 같다.
                # (글꼴에 따라 숫자 칸의 최소 폭이 달라져도 밀려나지 않는다.)
                spin, unit = dlg._backup_interval, dlg._backup_unit
                assert spin.minimumWidth() == spin.maximumWidth()
                assert unit.minimumWidth() == unit.maximumWidth()
                gap = spin.parentWidget().layout().spacing()
                assert (spin.width() + gap + unit.width()
                        == settings_dialog._FIELD_WIDTH)
                dlg.close()
                dlg.deleteLater()
    finally:
        i18n.set_language("ko")
        theme.apply_theme(qapp, "")
