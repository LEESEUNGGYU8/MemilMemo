"""작업 표시줄 목록과 바탕화면 우클릭 메뉴의 '새 메모' 테스트(offscreen).

실제 실행 중인 앱의 파이프와 실제 우클릭 메뉴 키는 건드리지 않는다. 레지스트리는
시험용 경로로 돌려서 쓰고 끝나면 지운다."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtGui import QCursor, QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app import config, i18n, ipc, shell_menu  # noqa: E402
from app.local_state import LocalStateStore  # noqa: E402
from app.network.client_base import ClientBase  # noqa: E402
from app.settings import AppSettings  # noqa: E402
from app.ui import main_window as mw  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.sticky_note import StickyNoteWidget  # noqa: E402

ME = "지우"


class FakeClient(ClientBase):
    """서버 없이 만든 메모를 곧바로 돌려주는 클라이언트."""

    def __init__(self, nickname: str = ME):
        super().__init__(nickname)
        self.created: list[dict] = []
        self.lock_requests: list[str] = []
        self.released: list[str] = []
        self.updated: list[tuple[str, str]] = []
        self.deliver = True

    def create_note(self, color, width, height, folder_id=None,
                    private_owner=None, quiet=False) -> None:
        note = {
            "id": f"n{len(self.created) + 1}", "version": 1, "color": color,
            "width": width, "height": height, "content": "",
            "folder_id": folder_id, "created_by": self._nickname,
            "updated_at": time.time(),
        }
        self.created.append(note)
        if self.deliver:
            self.noteCreated.emit(note)

    def acquire_lock(self, note_id: str) -> None:
        self.lock_requests.append(note_id)

    def release_lock(self, note_id: str) -> None:
        self.released.append(note_id)

    def update_note(self, note_id, content, color, width, height,
                    expected_version) -> None:
        self.updated.append((note_id, content))


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    i18n.set_language("ko")
    yield app


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    # 앞으로 올리기는 실제 창 순서를 바꾸므로 여기서는 불렸는지만 적는다.
    fronted: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        StickyNoteWidget, "bring_to_front",
        lambda self, *, start_editing=False: fronted.append(
            (self.note_id, start_editing)))
    client = FakeClient()
    settings = AppSettings()
    settings.fold_animation = False
    win = MainWindow(client=client,
                     local_state=LocalStateStore(tmp_path / "local_state.json"),
                     role="host", settings=settings)
    client.connectionChanged.emit(True)
    win.fronted = fronted
    yield win
    win._force_quit = True
    win._teardown_stickies()
    win.close()
    win.deleteLater()
    qapp.processEvents()


def _avail():
    return QGuiApplication.primaryScreen().availableGeometry()


# ── 실행 인자 ───────────────────────────────────────────────────────────

def test_new_note_command_from_argv():
    assert ipc.new_note_command(["app.exe"]) is None
    assert ipc.new_note_command(["app.exe", "--new-note"]) == ipc.CMD_NEW_NOTE
    assert ipc.new_note_command(
        ["app.exe", "--new-note", "--at-cursor"]) == ipc.CMD_NEW_NOTE_HERE
    # 자리 표시만 있고 '새 메모'가 아니면 아무 명령도 아니다.
    assert ipc.new_note_command(["app.exe", "--at-cursor"]) is None


# ── 실행 중인 인스턴스에 넘기기 ──────────────────────────────────────────

_SENDER = (
    "import sys; sys.path.insert(0, sys.argv[1]);"
    "from app import ipc; ipc.ipc_name = lambda: sys.argv[2];"
    "print(ipc.forward_command(sys.argv[3]))"
)


def _send_from_another_process(name: str, command: str) -> subprocess.Popen:
    """작업 표시줄 목록이 하듯 다른 프로세스에서 명령을 보낸다."""
    env = dict(os.environ)
    real = env.get("SYNCNOTES_TEST_REAL_APPDATA")
    if real:
        env["APPDATA"] = real       # 임시 APPDATA 로는 PySide6 를 못 찾는다
    root = str(Path(__file__).resolve().parent.parent)
    return subprocess.Popen([sys.executable, "-c", _SENDER, root, name, command],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, env=env)


@pytest.fixture
def receiver(qapp, monkeypatch):
    # 사용자의 실행 중인 앱이 쓰는 이름과 겹치지 않는 시험용 이름.
    name = f"SyncNotesTests-IPC-{os.getpid()}-{time.monotonic_ns()}"
    monkeypatch.setattr(ipc, "ipc_name", lambda: name)
    r = ipc.IpcReceiver()
    assert r.listen()
    r.got = []
    r.commandReceived.connect(r.got.append)
    r.name = name
    yield r
    r.stop()
    r.deleteLater()


def _wait_exit(qapp, proc: subprocess.Popen, seconds: float = 20.0) -> str:
    deadline = time.monotonic() + seconds
    while proc.poll() is None and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    if proc.poll() is None:
        proc.kill()
    out, err = proc.communicate()
    assert proc.returncode == 0, err
    return out.strip()


def test_command_reaches_the_running_instance(qapp, receiver):
    proc = _send_from_another_process(receiver.name, "new-note")
    assert _wait_exit(qapp, proc) == "True"
    assert receiver.got == ["new-note"]


def test_command_survives_a_busy_receiver(qapp, receiver):
    """받는 쪽이 잠깐 바빠도 명령이 사라지지 않는다.

    1.0.6 까지는 보낸 쪽이 0.5초만 기다리고 끊어서, 그 사이에 읽히지 않은
    명령은 그대로 사라졌다."""
    proc = _send_from_another_process(receiver.name, "new-note-here")
    time.sleep(2.0)                 # 이벤트 루프를 돌리지 않는다(바쁜 앱)
    assert receiver.got == []
    assert _wait_exit(qapp, proc) == "True"
    assert receiver.got == ["new-note-here"]


def test_forward_reports_when_nobody_listens(monkeypatch):
    monkeypatch.setattr(ipc, "ipc_name",
                        lambda: f"SyncNotesTests-Nobody-{os.getpid()}")
    # False 면 부르는 쪽(main)이 앱을 정상으로 켠다.
    assert ipc.forward_command("new-note") is False


# ── 밖에서 온 '새 메모' ──────────────────────────────────────────────────

def test_new_note_request_shows_only_the_note(window):
    assert not window.isVisible()
    window.handle_ipc_command("new-note")

    assert len(window._client.created) == 1
    sticky = window._stickies["n1"]
    assert sticky.isVisible()
    # 메인 창은 뜨지 않고, 새 메모만 앞으로 올라오며 바로 쓸 수 있게 한다.
    assert not window.isVisible()
    assert window.fronted == [("n1", True)]
    assert not window._local.state.get_note_state("n1").hidden


def test_new_note_request_while_disconnected_shows_main_window(window, qapp):
    window._client.connectionChanged.emit(False)
    window.handle_ipc_command("new-note")
    qapp.processEvents()

    # 만들 수 없을 때는 까닭이 보이도록 메인 창을 띄운다.
    assert window._client.created == []
    assert window.isVisible()
    assert window._new_note_spots == []


def test_new_note_here_opens_at_the_cursor(window, monkeypatch):
    avail = _avail()
    cursor = avail.topLeft() + QPoint(320, 240)
    monkeypatch.setattr(QCursor, "pos", staticmethod(lambda: cursor))
    window.handle_ipc_command("new-note-here")

    sticky = window._stickies["n1"]
    assert sticky.pos() == cursor - mw.NEW_NOTE_CURSOR_INSET
    st = window._local.state.get_note_state("n1")
    assert (st.x, st.y) == (sticky.x(), sticky.y())


def test_new_note_here_stays_inside_the_screen(window, monkeypatch):
    avail = _avail()
    monkeypatch.setattr(QCursor, "pos",
                        staticmethod(lambda: avail.bottomRight()))
    window.handle_ipc_command("new-note-here")

    sticky = window._stickies["n1"]
    assert avail.contains(sticky.geometry())


def test_new_notes_do_not_stack_on_the_same_spot(window):
    avail = _avail()
    window.handle_ipc_command("new-note")
    window.handle_ipc_command("new-note")
    window._create_note(config.DEFAULT_COLOR)   # 메인 창의 '새 메모' 단추

    first, second, third = (window._stickies[k].pos() for k in ("n1", "n2", "n3"))
    step = QPoint(mw.NEW_NOTE_STEP, mw.NEW_NOTE_STEP)
    assert first == avail.topLeft() + mw.NEW_NOTE_ORIGIN
    assert second == first + step
    assert third == second + step
    # 앱 안에서 만든 메모도 같은 규칙으로 앞에 나온다.
    assert [nid for nid, _ in window.fronted] == ["n1", "n2", "n3"]


def test_stale_cursor_spot_is_not_reused(window, monkeypatch):
    avail = _avail()
    window._client.deliver = False
    monkeypatch.setattr(QCursor, "pos",
                        staticmethod(lambda: avail.topLeft() + QPoint(400, 300)))
    window.handle_ipc_command("new-note-here")       # 만들어지지 못한 요청
    asked_at, spot = window._new_note_spots[0]
    window._new_note_spots[0] = (asked_at - mw.NEW_NOTE_SPOT_TTL - 1, spot)

    window._client.deliver = True
    window._create_note(config.DEFAULT_COLOR)
    # 한참 전 요청의 자리로 가지 않고 평소 자리에 놓인다.
    assert window._stickies["n2"].pos() == avail.topLeft() + mw.NEW_NOTE_ORIGIN


def test_note_made_by_someone_else_stays_hidden(window):
    window._client.noteCreated.emit({
        "id": "other", "version": 1, "color": "#BAE1FF", "width": 280,
        "height": 260, "content": "", "created_by": "민수",
        "updated_at": time.time(),
    })
    assert not window._stickies["other"].isVisible()
    assert window.fronted == []


# ── 앞으로 올린 뒤 편집 ──────────────────────────────────────────────────

def _sticky(qapp):
    note = {"id": "s1", "version": 1, "color": "#FFE066", "width": 280,
            "height": 260, "content": ""}
    w = StickyNoteWidget(note, ME, settings=AppSettings())
    w.apply_local_state(200, 160)
    w.show()
    qapp.processEvents()
    return w


def test_edit_starts_only_when_the_note_is_active(qapp, monkeypatch):
    w = _sticky(qapp)
    asked: list[str] = []
    w.requestLock.connect(asked.append)

    monkeypatch.setattr(w, "isActiveWindow", lambda: False)
    w._edit_if_active()
    assert asked == []          # 뒤에 남은 창은 잠금을 쥐지 않는다

    monkeypatch.setattr(w, "isActiveWindow", lambda: True)
    w._edit_if_active()
    assert asked == ["s1"]

    w._teardown = True
    w.close()


def test_bring_to_front_ignores_hidden_note(qapp):
    w = _sticky(qapp)
    w.hide()
    w.bring_to_front(start_editing=True)    # 예외 없이 아무 일도 하지 않는다
    assert not w.isVisible()
    w._teardown = True
    w.close()


# ── 바탕화면 우클릭 메뉴 등록 ────────────────────────────────────────────

@pytest.fixture
def test_key(monkeypatch):
    if not shell_menu.is_supported():
        pytest.skip("Windows 레지스트리가 없다")
    import winreg

    root = rf"Software\SyncNotesTests\pytest-{os.getpid()}"
    monkeypatch.setattr(shell_menu, "KEY_PATH", root + r"\NewNote")
    # 파일과 폴더 항목도 시험용 자리로 돌린다(실제 우클릭 메뉴를 건드리지 않는다).
    links = (root + r"\LinkFile", root + r"\LinkFolder")
    monkeypatch.setattr(shell_menu, "LINK_KEY_PATHS", links)
    monkeypatch.setattr(shell_menu, "FOLDER_KEY_PATH", root + r"\NewNoteFolder")
    yield root + r"\NewNote"
    for path in (root + r"\NewNote\command", root + r"\NewNote",
                 root + r"\NewNoteFolder\command", root + r"\NewNoteFolder",
                 links[0] + r"\command", links[0],
                 links[1] + r"\command", links[1], root,
                 r"Software\SyncNotesTests"):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
        except OSError:
            pass


def test_register_and_unregister(test_key):
    assert not shell_menu.is_registered()
    assert shell_menu.register("새 메모")
    assert shell_menu.is_registered()

    label, icon, command = shell_menu._read()
    assert label == "새 메모"
    assert icon == shell_menu.icon_location()
    assert command == shell_menu.command_line()
    assert command.endswith("--new-note --at-cursor")

    assert shell_menu.unregister()
    assert not shell_menu.is_registered()
    assert shell_menu.unregister()          # 없어도 성공으로 본다


def test_set_registered(test_key):
    assert shell_menu.set_registered(True, "새 메모")
    assert shell_menu.is_registered()
    assert shell_menu.set_registered(False, "새 메모")
    assert not shell_menu.is_registered()


def test_refresh_only_rewrites_installed_builds(test_key, monkeypatch):
    shell_menu.register("새 메모")

    # 개발 실행은 넣어 둔 메뉴를 고쳐 쓰지 않는다.
    shell_menu.refresh("New Note")
    assert shell_menu._read()[0] == "새 메모"

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Apps\Memo\Memo.exe")
    # 데이터 폴더를 따로 돌린 시험 실행(이 테스트가 그렇다)도 고쳐 쓰지 않는다.
    shell_menu.refresh("New Note")
    assert shell_menu._read()[0] == "새 메모"

    # 평소처럼 띄운 설치본은 언어나 실행 파일 위치가 바뀌면 맞춰 고친다.
    monkeypatch.setattr(config, "isolated_run", lambda: False)
    shell_menu.refresh("New Note")
    label, icon, command = shell_menu._read()
    assert label == "New Note"
    assert icon == r"C:\Apps\Memo\Memo.exe,0"
    assert command == r'"C:\Apps\Memo\Memo.exe" --new-note --at-cursor'


def test_refresh_does_not_add_a_missing_entry(test_key, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(config, "isolated_run", lambda: False)
    shell_menu.refresh("새 메모")
    assert not shell_menu.is_registered()


def test_settings_dialog_toggles_the_menu(qapp, test_key, monkeypatch):
    from app.ui import settings_dialog
    from app.ui.settings_dialog import SettingsDialog

    # 저장은 자동 실행 값도 쓴다. 실제 값은 건드리지 않게 막는다.
    monkeypatch.setattr(settings_dialog, "set_windows_autostart", lambda v: True)
    calls: list[tuple] = []
    real = shell_menu.set_registered
    monkeypatch.setattr(
        shell_menu, "set_registered",
        lambda on, label, link="", folder="": calls.append((on, label, link, folder))
        or real(on, label, link, folder))

    dlg = SettingsDialog(AppSettings(), role="host")
    assert not dlg._desktop_menu.isChecked()
    dlg._on_accept()
    assert calls == []                          # 바뀌지 않았으면 쓰지 않는다

    dlg = SettingsDialog(AppSettings(), role="host")
    dlg._desktop_menu.setChecked(True)
    dlg._on_accept()
    assert calls == [(True, "새 메모 추가", "새 메모에 링크로 넣기",
                      "새 메모 폴더 추가")]
    assert shell_menu.is_registered()
    assert shell_menu._read()[0] == "새 메모 추가"
    # 켜면 '새 메모 폴더 추가'와 파일, 폴더의 항목도 함께 들어간다.
    assert shell_menu._read_entry(shell_menu.FOLDER_KEY_PATH)[0] == "새 메모 폴더 추가"
    for path in shell_menu.LINK_KEY_PATHS:
        assert shell_menu._read_entry(path)[0] == "새 메모에 링크로 넣기"

    dlg = SettingsDialog(AppSettings(), role="host")
    assert dlg._desktop_menu.isChecked()        # 상태는 레지스트리에서 읽는다
    dlg._desktop_menu.setChecked(False)
    dlg._on_accept()
    assert not shell_menu.is_registered()
    assert shell_menu._read_entry(shell_menu.FOLDER_KEY_PATH) == ("", "", "")
    for path in shell_menu.LINK_KEY_PATHS:
        assert shell_menu._read_entry(path) == ("", "", "")


# ── 바탕화면 우클릭 메뉴: 새 메모 폴더 추가 ──────────────────────────────

def test_new_folder_command_from_argv():
    assert ipc.new_folder_command(
        ["app", "--new-folder", "--at-cursor"]) == ipc.CMD_NEW_FOLDER_HERE
    assert ipc.new_folder_command(["app", "--new-note", "--at-cursor"]) is None
    assert ipc.new_folder_command(["app"]) is None
    # 새 메모와 헷갈리지 않는다.
    assert ipc.new_note_command(["app", "--new-folder", "--at-cursor"]) is None


def test_register_adds_the_new_folder_entry_below_new_note(test_key):
    assert shell_menu.register("새 메모 추가", "", "새 메모 폴더 추가")
    label, icon, command = shell_menu._read_entry(shell_menu.FOLDER_KEY_PATH)
    assert label == "새 메모 폴더 추가"
    assert icon == shell_menu.icon_location()
    assert command == shell_menu.folder_command_line()
    assert command.endswith("--new-folder --at-cursor")
    assert shell_menu.unregister()
    assert shell_menu._read_entry(shell_menu.FOLDER_KEY_PATH) == ("", "", "")


def test_real_menu_keys_sort_new_note_above_new_folder():
    """탐색기는 키 이름의 차례로 메뉴를 놓는다. 폴더 항목이 바로 아래에 온다."""
    note_key = shell_menu.KEY_PATH.rsplit("\\", 1)
    folder_key = shell_menu.FOLDER_KEY_PATH.rsplit("\\", 1)
    assert note_key[0] == folder_key[0]
    assert note_key[1] < folder_key[1]
    assert folder_key[1].startswith(note_key[1])


def test_refresh_adds_the_new_folder_entry_for_an_older_install(test_key, monkeypatch):
    # 옛 버전은 '새 메모'만 넣었다. 올라온 뒤 처음 켤 때 글자가 바뀌고 폴더 항목이 생긴다.
    shell_menu.register("새 메모")
    assert shell_menu._read_entry(shell_menu.FOLDER_KEY_PATH) == ("", "", "")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Apps\Memo\Memo.exe")
    monkeypatch.setattr(config, "isolated_run", lambda: False)
    shell_menu.refresh("새 메모 추가", "새 메모에 링크로 넣기", "새 메모 폴더 추가")
    assert shell_menu._read()[0] == "새 메모 추가"
    assert shell_menu._read_entry(shell_menu.FOLDER_KEY_PATH) == (
        "새 메모 폴더 추가", r"C:\Apps\Memo\Memo.exe,0",
        r'"C:\Apps\Memo\Memo.exe" --new-folder --at-cursor')


# ── 파일·폴더 우클릭 메뉴: 새 메모에 링크로 넣기 ─────────────────────────

def test_link_note_paths_from_argv():
    assert ipc.link_note_paths(["app.exe"]) == []
    assert ipc.link_note_paths(["app.exe", "--new-note"]) == []
    path = r"C:\Work\견적 회신.xlsx"
    assert ipc.link_note_paths(["app.exe", "--link-note", path]) == [path]
    assert ipc.link_note_paths(["app.exe", "--link-note"]) == []
    command = ipc.link_note_command([path, r"D:\Docs"])
    assert command.splitlines() == [f"link-note {path}", r"link-note D:\Docs"]
    # 경로의 대소문자와 빈칸은 그대로 온다. 명령 이름만 소문자로 본다.
    assert ipc.split_command(f"LINK-NOTE {path}") == ("link-note", path)
    assert ipc.split_command("new-note-here") == ("new-note-here", "")
    assert ipc.split_command("") == ("", "")


def test_register_adds_file_and_folder_entries(test_key):
    assert shell_menu.register("새 메모", "새 메모에 링크로 넣기")
    for path in shell_menu.LINK_KEY_PATHS:
        label, icon, command = shell_menu._read_entry(path)
        assert label == "새 메모에 링크로 넣기"
        assert icon == shell_menu.icon_location()
        assert command == shell_menu.link_command_line()
        assert command.endswith('--link-note "%1"')
    assert shell_menu.unregister()
    for path in shell_menu.LINK_KEY_PATHS:
        assert shell_menu._read_entry(path) == ("", "", "")
    assert not shell_menu.is_registered()


def test_refresh_adds_link_entries_for_an_older_install(test_key, monkeypatch):
    # 옛 버전은 바탕화면 항목만 넣었다. 올라온 뒤 처음 켤 때 나머지가 생긴다.
    shell_menu.register("새 메모")
    assert shell_menu._read_entry(shell_menu.LINK_KEY_PATHS[0]) == ("", "", "")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Apps\Memo\Memo.exe")
    monkeypatch.setattr(config, "isolated_run", lambda: False)
    shell_menu.refresh("새 메모", "새 메모에 링크로 넣기")
    for path in shell_menu.LINK_KEY_PATHS:
        assert shell_menu._read_entry(path) == (
            "새 메모에 링크로 넣기", r"C:\Apps\Memo\Memo.exe,0",
            r'"C:\Apps\Memo\Memo.exe" --link-note "%1"')


def test_refresh_leaves_link_entries_alone_when_the_menu_is_off(test_key, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(config, "isolated_run", lambda: False)
    shell_menu.refresh("새 메모", "새 메모에 링크로 넣기")
    for path in shell_menu.LINK_KEY_PATHS:
        assert shell_menu._read_entry(path) == ("", "", "")


def test_only_one_process_boots_for_file_links(qapp, monkeypatch):
    if sys.platform != "win32":
        pytest.skip("Windows 에서만 프로세스가 여러 개 뜬다")
    import ctypes

    name = f"SyncNotesTests-Boot-{os.getpid()}-{time.monotonic_ns()}"
    monkeypatch.setattr(ipc, "ipc_name", lambda: name)
    monkeypatch.setattr(ipc, "_boot_mutex", None)
    assert ipc.claim_boot() is True
    assert ipc.claim_boot() is True            # 같은 프로세스는 몇 번을 물어도 같다
    code = ("import sys; sys.path.insert(0, sys.argv[1]);"
            "from app import ipc; ipc.ipc_name = lambda: sys.argv[2];"
            "print(ipc.claim_boot())")
    env = dict(os.environ)
    real = env.get("SYNCNOTES_TEST_REAL_APPDATA")
    if real:
        env["APPDATA"] = real
    root = str(Path(__file__).resolve().parent.parent)
    proc = subprocess.Popen([sys.executable, "-c", code, root, name],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, env=env)
    try:
        # 먼저 나선 프로세스가 있으면 다른 프로세스는 앱을 켜지 않는다.
        assert _wait_exit(qapp, proc) == "False"
    finally:
        ctypes.WinDLL("kernel32").CloseHandle(ctypes.c_void_p(ipc._boot_mutex))


def test_file_link_request_makes_a_note_holding_the_link(window, qapp, monkeypatch):
    path = r"C:\Work\견적 회신.xlsx"
    spot = _avail().topLeft() + QPoint(300, 220)
    monkeypatch.setattr(QCursor, "pos", staticmethod(lambda: spot))
    window.handle_ipc_command(f"link-note {path}")
    # 여럿을 골랐을 수 있어 잠깐 모은다. 그동안에는 아직 만들지 않는다.
    assert window._client.created == [] and window._link_batch_timer.isActive()
    window._link_batch_timer.stop()
    window._flush_link_batch()

    assert len(window._client.created) == 1
    sticky = window._stickies["n1"]
    assert sticky.isVisible() and not window.isVisible()
    assert sticky.pos() == spot - mw.NEW_NOTE_CURSOR_INSET
    # 빈 메모로 편집을 시작하지 않고, 잠금을 받아 링크를 넣는다.
    assert window.fronted == [("n1", False)]
    assert window._client.lock_requests == ["n1"]
    assert window._client.updated == []

    window._client.lockGranted.emit("n1")
    # 앞에 서지 못한 창(시험 화면)은 넣은 것만 보내고 편집을 끝낸다.
    assert [nid for nid, _html in window._client.updated] == ["n1"]
    html = window._client.updated[0][1]
    assert "견적 회신.xlsx" in html and "file:" in html
    assert window._client.released == ["n1"]
    assert not sticky._edit_mode and sticky._text.has_links()


def test_several_files_go_into_one_note(window, qapp, monkeypatch):
    monkeypatch.setattr(QCursor, "pos", staticmethod(lambda: QPoint(200, 200)))
    paths = [r"C:\Work\a.pdf", r"C:\Work\b.docx", r"C:\Work\자료"]
    for p in paths:
        window.handle_ipc_command(f"link-note {p}")
    window.handle_ipc_command(f"link-note {paths[0]}")      # 같은 경로는 한 번만
    window._link_batch_timer.stop()
    window._flush_link_batch()
    assert len(window._client.created) == 1
    window._client.lockGranted.emit("n1")
    html = window._client.updated[-1][1]
    assert all(name in html for name in ("a.pdf", "b.docx", "자료"))
    assert html.count("a.pdf") >= 1
    plain = window._stickies["n1"]._text.toPlainText()
    assert len([ln for ln in plain.splitlines() if ln.strip()]) == 3   # 한 줄에 하나

    # 조금 늦게 도착한 경로는 새 메모를 만들지 않고 같은 메모에 잇는다.
    window._client.lockReleased.emit("n1")
    window.handle_ipc_command(r"link-note C:\Work\late.txt")
    assert not window._link_batch_timer.isActive()
    assert len(window._client.created) == 1
    assert window._client.lock_requests == ["n1", "n1"]
    window._client.lockGranted.emit("n1")
    assert "late.txt" in window._client.updated[-1][1]


def test_late_path_makes_its_own_note_after_a_while(window, monkeypatch):
    monkeypatch.setattr(QCursor, "pos", staticmethod(lambda: QPoint(200, 200)))
    window.handle_link_note_request([r"C:\Work\a.pdf"], QPoint(200, 200))
    assert len(window._client.created) == 1
    note_id, made_at = window._link_note_recent
    window._link_note_recent = (note_id, made_at - mw.LINK_NOTE_JOIN_SECONDS - 1)
    window.handle_ipc_command(r"link-note C:\Work\b.pdf")
    assert window._link_batch == [r"C:\Work\b.pdf"]
    window._link_batch_timer.stop()
    window._flush_link_batch()
    assert len(window._client.created) == 2


def test_file_link_request_while_disconnected_shows_main_window(window, qapp):
    window._client.connectionChanged.emit(False)
    window.handle_link_note_request([r"C:\Work\a.pdf"])
    qapp.processEvents()
    assert window._client.created == [] and window.isVisible()
    assert window._link_note_queue == []
