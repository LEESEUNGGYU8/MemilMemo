"""1.0.2 뒤의 검토에서 나온 문제들(offscreen).

링크 실행 확인을 비켜 가는 이름, 꺾쇠가 든 평문, 공유 PC 가 거둔 잠금, 잇달아 보낸
저장, 다시 붙을 때의 덮어쓰기, 사라졌다 돌아오는 메모, 공유 PC 쪽의 잠금 확인."""
from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app import config, file_links as fl, html_guard, i18n, ipc, packaging  # noqa: E402
from app.database import SqliteRepository  # noqa: E402
from app.local_state import LocalStateStore  # noqa: E402
from app.network.client_base import ClientBase  # noqa: E402
from app.network.host_client import HostLocalClient  # noqa: E402
from app.network.host_server import HostServer, _Session  # noqa: E402
from app.offline_drafts import KIND_EDIT, KIND_NEW, DraftStore  # noqa: E402
from app.protocol import FrameParser  # noqa: E402
from app.settings import AppSettings  # noqa: E402
from app.sync.history_tracker import HistoryTracker  # noqa: E402
from app.sync.lock_manager import LockManager  # noqa: E402
from app.ui import confirm, drafts_dialog, theme  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.rich_text import html_to_plain  # noqa: E402
from app.ui.sticky_note import StickyNoteWidget  # noqa: E402

ME = "지우"
OTHER = "민수"


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    i18n.set_language("ko")
    theme.apply_theme(app, "")
    yield app


def note(nid: str, text: str, version: int = 1, **more) -> dict:
    return {"id": nid, "version": version, "color": "#FFE066", "width": 300,
            "height": 240, "content": text, "created_by": OTHER,
            "updated_by": OTHER, "updated_at": int(time.time() * 1000), **more}


# ── 링크 ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", [
    r"\\server\share\setup.cmd.", r"C:\work\run.bat ", r"C:\work\tool.exe. .",
    r"C:\work\script.ps1::$DATA", r"C:\work\run.BAT...",
])
def test_windows_ignores_trailing_dots_and_spaces_so_we_do_too(name):
    """Windows 는 이름 끝의 점과 빈칸을 떼고 연다. 그대로 보면 실행 파일이 확장자
    없는 파일로 보여, 묻지 않고 열린다."""
    assert fl.runs_when_opened(name)


@pytest.mark.parametrize("name", [
    r"C:\work\report.pdf", r"C:\work\notes.txt.", r"C:\work\folder.", r"C:\work\noext"])
def test_ordinary_files_are_still_opened_without_asking(name):
    assert not fl.runs_when_opened(name)


# ── 꺾쇠가 든 평문 ──────────────────────────────────────────────────────

PLAIN = "홍길동 <hong@example.com> 내선 <1234>\nif a < b and c > d: ok"


def test_angle_brackets_alone_do_not_make_a_note_html():
    assert not html_guard.is_markup(PLAIN)
    assert not html_guard.is_markup("가격 < 100원")
    assert not html_guard.is_markup("<a@b.com>")
    assert html_guard.is_markup("<p>글</p>")
    assert html_guard.is_markup('<img src="data:image/png;base64,AA" />')
    assert html_guard.is_markup('<!DOCTYPE HTML PUBLIC "x"><html><body></body></html>')
    # 제목, 검색, 알림이 쓰는 평문도 글자 그대로다.
    assert html_to_plain(PLAIN) == PLAIN
    assert html_to_plain("가격 < 100원") == "가격 < 100원"


def make_sticky(qapp, content: str = "", **more):
    w = StickyNoteWidget({"id": "n", "version": 1, "color": "#FFE066", "width": 300,
                          "height": 260, "content": content, **more}, ME,
                         settings=AppSettings())
    sent, locks, released = [], [], []
    w.commitEdit.connect(lambda *a: sent.append(a))
    w.requestLock.connect(locks.append)
    w.releaseLock.connect(released.append)
    w.show()
    qapp.processEvents()
    return w, sent, locks, released


def close(w) -> None:
    w._teardown = True
    w.close()
    w.deleteLater()


def test_plain_note_with_angle_brackets_is_shown_as_typed(qapp):
    """서식 없이 저장된 옛 메모. 전에는 꺾쇠 사이의 글이 사라졌다."""
    w, *_ = make_sticky(qapp, PLAIN)
    assert w._text.toPlainText() == PLAIN
    close(w)


def test_note_with_angle_brackets_survives_a_round_trip(qapp):
    w, sent, _locks, _released = make_sticky(qapp)
    w.enter_edit_mode()
    w._text.setPlainText(PLAIN)
    w._commit_and_release()
    saved = sent[-1][1]
    # 꺾쇠가 든 글은 HTML 로 저장한다. 읽는 쪽이 옛 버전이어도 글이 남는다.
    assert html_guard.is_markup(saved) and "&lt;hong@example.com&gt;" in saved
    other, *_ = make_sticky(qapp, saved)
    assert other._text.toPlainText() == PLAIN
    close(w)
    close(other)


# ── 메모 창: 잠금을 거뒀을 때, 잇달아 보낸 저장 ─────────────────────────

def test_losing_the_lock_ends_the_edit(qapp):
    w, _sent, _locks, _released = make_sticky(qapp, "회의록")
    w.enter_edit_mode()
    assert w.lock_lost() is None                 # 보낼 것이 없다
    assert not w.is_editing() and w._text.isReadOnly()
    # 그 뒤 다른 사람이 고치면 화면이 따라간다(전에는 옛 글이 남은 채 판 번호만 올랐다).
    w.apply_server_state({"id": "n", "version": 2, "content": "남이 고친 글"})
    assert w._text.toPlainText() == "남이 고친 글" and w.version == 2
    close(w)


def test_losing_the_lock_hands_back_what_was_not_sent(qapp):
    w, sent, _locks, _released = make_sticky(qapp, "회의록")
    w.enter_edit_mode()
    w._text.setPlainText("회의록 쓰던 글")
    assert w.lock_lost() == "회의록 쓰던 글"
    assert sent == [] and w.unsent_edit() == "회의록 쓰던 글"
    assert w.unsent_base_version() == 1
    close(w)


def test_second_save_before_the_first_answer_is_not_rejected(qapp):
    """쓰다 멈춘 직후(자동 저장이 나간 직후) 곧바로 다른 곳을 눌렀다. 공유 PC 는
    온 차례대로 처리하므로 둘째 저장은 하나 올라간 판 번호를 적어야 한다."""
    w, sent, _locks, _released = make_sticky(qapp, "회의록", version=3)
    w.enter_edit_mode()
    w._text.setPlainText("회의록 하나")
    w._autosave_while_editing()
    w._text.setPlainText("회의록 하나 둘")
    w._commit_and_release()
    assert [(s[1], s[5]) for s in sent] == [("회의록 하나", 3), ("회의록 하나 둘", 4)]
    # 먼저 보낸 것의 답이 와도 화면은 나중에 쓴 글 그대로다.
    w.apply_server_state({"id": "n", "version": 4, "content": "회의록 하나"})
    assert w._text.toPlainText() == "회의록 하나 둘" and w.unsent_edit() is not None
    w.apply_server_state({"id": "n", "version": 5, "content": "회의록 하나 둘"})
    assert w.unsent_edit() is None and w.version == 5
    close(w)


# ── 메인 창 ─────────────────────────────────────────────────────────────

class FakeClient(ClientBase):
    def __init__(self, nickname: str = ME):
        super().__init__(nickname)
        self.calls: list[tuple] = []

    def acquire_lock(self, note_id):
        self.calls.append(("lock", note_id))

    def release_lock(self, note_id):
        self.calls.append(("release", note_id))

    def update_note(self, note_id, content, color, width, height, expected_version):
        self.calls.append(("update", note_id, content, expected_version))

    def create_note(self, color, width, height, folder_id=None,
                    private_owner=None, quiet=False):
        self.calls.append(("create", private_owner, quiet))

    def delete_note(self, note_id):
        self.calls.append(("delete", note_id))

    def of(self, kind: str) -> list[tuple]:
        return [c for c in self.calls if c[0] == kind]


@pytest.fixture
def guest(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(StickyNoteWidget, "bring_to_front",
                        lambda self, *, start_editing=False: None)
    client = FakeClient()
    store = DraftStore(tmp_path / "offline.sqlite3")
    win = MainWindow(client=client, local_state=LocalStateStore(tmp_path / "ls.json"),
                     role="guest", settings=AppSettings(), drafts=store)
    win._choose_for_drafts = lambda drafts: "later"
    win.store = store
    yield win
    win._force_quit = True
    win._teardown_stickies()
    win.close()
    win.deleteLater()
    store.close()
    qapp.processEvents()


def welcome(win, *notes: dict, locks=None) -> None:
    win._client.connectionChanged.emit(True)
    win._client.welcomed.emit({"users": [ME, OTHER], "host": OTHER,
                               "notes": list(notes), "locks": dict(locks or {}),
                               "folders": [], "heartbeat_ms": 3000})


def start_editing(win, note_id: str, text: str) -> StickyNoteWidget:
    win._set_note_visibility(note_id, True)
    sticky = win._stickies[note_id]
    win._client.lockGranted.emit(note_id)
    assert sticky.is_editing()
    cursor = sticky._text.textCursor()
    cursor.movePosition(QTextCursor.End)
    cursor.insertText(text)
    return sticky


def test_lock_taken_back_by_the_host_ends_my_edit(guest):
    """메모에 커서를 둔 채 오래 두면 공유 PC 가 잠금을 거둔다. 편집 중인 채로 두면
    나중에 한 글자만 쳐도 그 사이 남이 고친 글을 덮어쓴다."""
    welcome(guest, note("a", "회의록", version=4))
    sticky = start_editing(guest, "a", " 쓰던 글")
    guest._client.calls.clear()

    guest._client.lockReleased.emit("a")

    assert not sticky.is_editing()
    # 보내지 못한 글은 잠금을 다시 받아 보낸다.
    assert guest._client.of("lock") == [("lock", "a")]
    guest._client.lockGranted.emit("a")
    assert guest._client.of("update") == [("update", "a", "회의록 쓰던 글", 4)]
    assert not sticky.is_editing()


def test_stale_text_is_not_pushed_over_someone_elses_edit(guest):
    """거절돼 닿지 않은 저장이 남아 있다가, 다시 붙을 때 남의 글을 덮어쓰면 안 된다."""
    welcome(guest, note("a", "회의록", version=4))
    sticky = start_editing(guest, "a", " 닿지 못한 글")
    sticky._commit_and_release()                       # 답이 오지 않았다
    guest._on_note_updated(note("a", "남이 고친 글", version=6))
    assert sticky.unsent_edit() is not None and sticky.unsent_base_version() == 4
    guest._client.calls.clear()

    guest._client.connectionChanged.emit(False)
    welcome(guest, note("a", "남이 고친 글", version=6))

    assert guest._client.of("lock") == []              # 그 메모에 다시 보내지 않는다
    assert guest._client.of("create")                  # 새 메모로 남긴다


def test_my_own_leftover_lock_does_not_send_my_text_to_a_new_note(guest):
    """잠깐 끊겼다 붙으면 공유 PC 가 끊긴 연결의 내 잠금을 아직 들고 있을 수 있다."""
    welcome(guest, note("a", "회의록", version=4))
    start_editing(guest, "a", " 쓰던 글")
    guest._client.calls.clear()

    guest._client.connectionChanged.emit(False)
    welcome(guest, note("a", "회의록", version=4), locks={"a": ME})

    assert guest._client.of("create") == []
    assert guest._client.of("lock") == [("lock", "a")]


def test_note_that_comes_right_back_keeps_its_place(guest):
    """공개 범위가 바뀌면 옛 공유 PC 는 그 메모를 지웠다가 다시 만들어 보낸다."""
    welcome(guest, note("a", "회의록", created_by=ME))
    guest._set_note_visibility("a", True)
    guest._local.state.set_note_state("a", x=640, y=480, w=420, h=360, folded=True,
                                      fold_style="icon")

    guest._client.noteDeleted.emit("a")
    guest._client.noteCreated.emit(note("a", "회의록", created_by=ME, private_owner=ME))

    state = guest._local.state.notes["a"]
    assert (state.x, state.y, state.w, state.h) == (640, 480, 420, 360)
    assert state.folded and state.fold_style == "icon" and not state.hidden
    assert guest._notes["a"]["private_owner"] == ME


def test_a_note_deleted_for_real_is_forgotten(guest):
    welcome(guest, note("a", "회의록"))
    guest._local.state.set_note_state("a", x=640, y=480)
    guest._client.noteDeleted.emit("a")
    assert "a" not in guest._local.state.notes and "a" not in guest._notes


def test_text_is_kept_when_the_note_is_taken_from_under_me(guest):
    """내가 쓰는 중에 다른 사람이 그 메모를 나만 보기로 바꿨다(옛 공유 PC)."""
    welcome(guest, note("a", "회의록"))
    start_editing(guest, "a", " 쓰던 글")
    guest._locks["a"] = ME
    guest._client.calls.clear()

    guest._client.noteDeleted.emit("a")

    assert guest._client.of("create") == [("create", ME, False)]   # 새 메모(나만 보기)
    assert guest._client.of("release") == [("release", "a")]       # 잠금을 돌려준다
    assert guest._orphan_texts == ["회의록 쓰던 글"]


def test_unanswered_requests_do_not_fire_after_reconnecting(guest):
    welcome(guest, note("a", "회의록", version=4))
    guest._pending_resend["a"] = "보내려던 글"          # 잠금을 청해 놓고 답을 못 받았다
    guest._pending_restore["a"] = "되돌리려던 글"
    guest._recreate_queue.append({"content": "지웠다 되돌리려던 글", "pos": (0, 0),
                                  "hidden": False})

    guest._client.connectionChanged.emit(False)

    assert guest._pending_resend == {} and guest._pending_restore == {}
    assert guest._recreate_queue == []
    # 보내려던 글은 버리지 않고 적어 둔다. 다시 붙으면 그 메모에 넣는다.
    (draft,) = guest.store.pending(ME)
    assert (draft.kind, draft.base_id, draft.content, draft.started_online) == (
        KIND_EDIT, "a", "보내려던 글", True)
    guest._client.calls.clear()
    welcome(guest, note("a", "회의록", version=4))
    assert guest._client.of("lock") == [("lock", "a")]


def test_text_meant_for_a_new_note_is_kept_as_a_note_when_the_host_is_lost(guest):
    welcome(guest, note("a", "회의록"))
    guest._orphan_texts.append("새 메모에 담으려던 글")

    guest._client.connectionChanged.emit(False)

    assert guest._orphan_texts == []
    (draft,) = guest.store.pending(ME)
    assert draft.kind == KIND_NEW and draft.content == "새 메모에 담으려던 글"
    assert "새 메모에 담으려던 글" in [n["content"] for n in guest._notes.values()]


# ── 공유 PC 의 메인 창: 답이 그 자리에서 온다 ───────────────────────────

@pytest.fixture
def host_window(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(StickyNoteWidget, "bring_to_front",
                        lambda self, *, start_editing=False: None)
    repo = SqliteRepository(tmp_path / "notes.sqlite3")
    server = HostServer(repo, LockManager(idle_timeout_ms=60_000),
                        HistoryTracker(repo), host_nickname=OTHER)
    client = HostLocalClient(server, OTHER)
    win = MainWindow(client=client, local_state=LocalStateStore(tmp_path / "ls.json"),
                     role="host", settings=AppSettings())
    client.start()
    qapp.processEvents()                 # 첫 인사(hello)는 한 박자 뒤에 나간다
    yield win, client, repo
    win._force_quit = True
    win._teardown_stickies()
    win.close()
    win.deleteLater()
    qapp.processEvents()
    repo.close()


def test_reviving_on_the_sharing_pc_gives_a_note_that_can_be_edited(host_window):
    """공유 PC 에서는 잠금과 저장의 답이 곧바로 온다. 메모 창을 만들기 전에 글이
    들어가 버리면 창은 빈 옛 내용으로 만들어지고 그 뒤의 편집이 모두 거절된다."""
    win, _client, repo = host_window

    win._on_revive_requested("<p>되살린 글</p>", "#FFE066")

    (saved,) = repo.list_notes()
    sticky = win._stickies[saved.id]
    assert sticky._text.toPlainText() == "되살린 글"
    assert sticky.version == saved.version
    assert win._status.currentMessage() == i18n.t("main.status_revived")


# ── 공유 PC ─────────────────────────────────────────────────────────────

class Recorder:
    def __init__(self):
        self.messages: list[tuple[str, dict]] = []

    def __call__(self, kind: str, data: dict) -> None:
        self.messages.append((kind, data))

    def of(self, kind: str) -> list[dict]:
        return [d for k, d in self.messages if k == kind]

    def kinds(self) -> list[str]:
        return [k for k, _d in self.messages]


@pytest.fixture
def host(tmp_path):
    repo = SqliteRepository(tmp_path / "notes.sqlite3")
    server = HostServer(repo, LockManager(idle_timeout_ms=60_000),
                        HistoryTracker(repo), host_nickname=OTHER)
    client = HostLocalClient(server, OTHER)
    seen = Recorder()
    server.attach_local_host()
    server.set_local_subscriber(seen)
    client._send_hello()
    guest = _Session(socket=None, parser=FrameParser(), nickname=ME, hello_received=True)
    guest_seen = Recorder()
    server._sessions["guest"] = guest
    send = server._send_to
    server._send_to = lambda s, m: (guest_seen(m.type, m.data) if s is guest
                                    else send(s, m))
    yield server, client, seen, guest_seen
    repo.close()


def test_note_someone_is_editing_cannot_be_made_private(host):
    server, client, seen, guest_seen = host
    client.create_note("#FFE066", 280, 260)
    note_id = seen.of("note_created")[-1]["note"]["id"]
    server.locks.acquire(note_id, ME)                  # 다른 사람이 편집 중
    seen.messages.clear()
    guest_seen.messages.clear()

    client.set_note_privacy(note_id, True)

    assert seen.of("error")[-1]["message"] == f"note locked by {ME}"
    assert guest_seen.of("note_deleted") == []         # 편집하던 사람에게서 사라지지 않는다


def test_note_someone_is_editing_cannot_be_moved_into_a_private_folder(host):
    server, client, seen, guest_seen = host
    client.create_folder("내 폴더", "#BAE1FF", private=True)
    folder_id = seen.of("folder_created")[-1]["folder"]["id"]
    client.create_folder("회의", "#BAE1FF")
    public_id = seen.of("folder_created")[-1]["folder"]["id"]
    client.create_note("#FFE066", 280, 260)
    note_id = seen.of("note_created")[-1]["note"]["id"]
    server.locks.acquire(note_id, ME)
    guest_seen.messages.clear()

    client.set_note_folder(note_id, folder_id)
    assert guest_seen.of("note_deleted") == []
    # 보는 사람이 달라지지 않는 이동은 그대로 된다.
    client.set_note_folder(note_id, public_id)
    assert guest_seen.of("note_updated")[-1]["note"]["folder_id"] == public_id


def test_folder_made_private_keeps_its_owner_s_notes_in_place(host):
    """주인에게는 고친 것으로 알린다. 지웠다 다시 만들어 보내면 주인의 PC 에서
    폴더와 메모의 자리, 크기, 접힘이 처음으로 돌아간다."""
    server, client, seen, guest_seen = host
    client.create_folder("회의", "#BAE1FF")
    folder_id = seen.of("folder_created")[-1]["folder"]["id"]
    client.create_note("#FFE066", 280, 260, folder_id=folder_id)
    note_id = seen.of("note_created")[-1]["note"]["id"]
    seen.messages.clear()
    guest_seen.messages.clear()

    client.update_folder(folder_id, private=True)

    assert seen.kinds() == ["folder_updated", "note_updated"]
    assert seen.of("folder_updated")[0]["folder"]["private_owner"] == OTHER
    assert guest_seen.kinds() == ["folder_deleted", "note_deleted"]
    assert guest_seen.of("note_deleted")[0]["id"] == note_id
    seen.messages.clear()
    guest_seen.messages.clear()

    client.update_folder(folder_id, private=False)

    assert seen.kinds() == ["folder_updated", "note_updated"]
    assert guest_seen.kinds() == ["folder_created", "note_created"]
    assert guest_seen.of("note_created")[0]["shared_by"] == OTHER


def test_folder_cannot_be_made_private_while_someone_edits_a_note_in_it(host):
    server, client, seen, guest_seen = host
    client.create_folder("회의", "#BAE1FF")
    folder_id = seen.of("folder_created")[-1]["folder"]["id"]
    client.create_note("#FFE066", 280, 260, folder_id=folder_id)
    note_id = seen.of("note_created")[-1]["note"]["id"]
    server.locks.acquire(note_id, ME)
    seen.messages.clear()
    guest_seen.messages.clear()

    client.update_folder(folder_id, private=True)

    assert seen.of("error")[-1]["message"] == f"note locked by {ME}"
    assert guest_seen.messages == []
    # 이름만 바꾸는 것은 된다.
    client.update_folder(folder_id, name="주간 회의")
    assert guest_seen.of("folder_updated")[-1]["folder"]["name"] == "주간 회의"


# ── 설치판과 스토어판, 시험 실행 ────────────────────────────────────────

def test_the_two_editions_do_not_take_each_others_commands(monkeypatch):
    monkeypatch.setattr(config, "isolated_run", lambda: False)
    monkeypatch.setattr(packaging, "is_packaged", lambda: False)
    classic = ipc.ipc_name()
    monkeypatch.setattr(packaging, "is_packaged", lambda: True)
    assert ipc.ipc_name() != classic and ipc.ipc_name().startswith(classic)


def test_redirecting_the_profile_folder_also_counts_as_isolated(monkeypatch):
    """USERPROFILE 까지 돌려 띄우면 Windows 에 묻는 길이 실패한다. 그때도 시험
    실행으로 알아봐야 실제 앱의 명령 통로와 레지스트리를 건드리지 않는다."""
    monkeypatch.setattr(config, "_real_profile", lambda: r"C:\Users\me")
    monkeypatch.setattr(config, "_real_appdata", lambda: r"C:\Users\me\AppData\Roaming")
    monkeypatch.setenv("APPDATA", r"C:\Users\me\AppData\Roaming")
    monkeypatch.setenv("USERPROFILE", r"C:\Users\me")
    assert not config.isolated_run()
    monkeypatch.setenv("USERPROFILE", r"D:\test\profile")
    assert config.isolated_run()
    monkeypatch.setenv("USERPROFILE", r"C:\Users\me")
    monkeypatch.setenv("APPDATA", r"D:\test\roaming")
    assert config.isolated_run()


@pytest.mark.skipif(os.name != "nt", reason="Windows 전용")
def test_real_folders_are_found_without_the_environment(monkeypatch, tmp_path):
    real = config._real_appdata()
    monkeypatch.setenv("USERPROFILE", str(tmp_path))     # AppData 가 없는 폴더
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    assert config._real_profile() and config._real_appdata() == real
    assert config.isolated_run()


# ── 한 번 더 켰을 때 ────────────────────────────────────────────────────

def test_second_plain_launch_shows_the_running_app_instead_of_starting_another(
        monkeypatch):
    """바로가기를 한 번 더 눌렀다. 하나 더 뜨면 같은 닉네임으로 두 번 접속해 메모
    창이 겹쳐 뜨고, 공유 PC 라면 포트를 열지 못해 접속 정보까지 지운다."""
    import main as app_main
    sent: list[str] = []
    monkeypatch.setattr(app_main, "forward_command",
                        lambda command, **k: sent.append(command) or True)
    assert app_main._already_running(["app.exe"], False) and sent == ["show"]

    # 떠 있는 것이 없으면 평소대로 켠다.
    monkeypatch.setattr(app_main, "forward_command", lambda command, **k: False)
    monkeypatch.setattr(app_main, "claim_boot", lambda: True)
    assert not app_main._already_running(["app.exe"], False)

    # 다른 실행이 켜지는 중이면 뜨기를 기다렸다가 그 창을 부른다.
    waited: list[str] = []
    monkeypatch.setattr(app_main, "claim_boot", lambda: False)
    monkeypatch.setattr(app_main, "forward_when_ready",
                        lambda command, **k: waited.append(command) or True)
    assert app_main._already_running(["app.exe"], False) and waited == ["show"]

    # 역할을 바꿔 스스로 다시 켠 경우와 명령으로 켜진 경우는 다루지 않는다.
    assert not app_main._already_running(["app.exe", "--wait-pid", "1234"], False)
    assert not app_main._already_running(["app.exe", "--new-note"], True)


# ── 묻는 창 ─────────────────────────────────────────────────────────────

def test_question_names_what_was_done(qapp):
    new = [(KIND_NEW, "새 글")]
    edit = [(KIND_EDIT, "고친 글")]
    assert drafts_dialog.question(new) == "공유 PC와 연결이 끊긴 동안 생성된 내용을 유지할까요?"
    assert drafts_dialog.question(edit) == "공유 PC와 연결이 끊긴 동안 수정된 내용을 유지할까요?"
    assert drafts_dialog.question(new + edit) == (
        "공유 PC와 연결이 끊긴 동안 생성/수정된 내용을 유지할까요?")


def test_each_note_is_listed_with_its_first_sentence(qapp):
    assert drafts_dialog.first_sentence(
        "<p>회의는 3시입니다. 자료는 김과장이 준비합니다.</p><p>둘째 줄</p>") == "회의는 3시입니다."
    assert drafts_dialog.first_sentence("\n\n거래처 연락\n견적서") == "거래처 연락"
    assert drafts_dialog.first_sentence("") == i18n.t("main.note_title_empty")
    dialog = drafts_dialog.DraftsDialog([(KIND_NEW, "거래처 미팅 준비"),
                                         (KIND_EDIT, "주간 회의 메모")])
    dialog.show()
    qapp.processEvents()
    rows = [dialog._list.widget().layout().itemAt(i).widget().accessibleName()
            for i in range(2)]
    assert rows == ["생성, 거래처 미팅 준비", "수정, 주간 회의 메모"]
    assert dialog._toggle.text() == "메모 2개"
    assert [b.text() for b in (dialog._keep, dialog._discard, dialog._later)] == [
        "저장", "삭제", "보류"]
    # 목록은 접었다 펼 수 있다. 접으면 창도 그만큼 줄어든다.
    tall = dialog.height()
    dialog._toggle.click()
    qapp.processEvents()
    assert not dialog._list.isVisible() and dialog.height() < tall
    dialog._toggle.click()
    qapp.processEvents()
    assert dialog._list.isVisible() and dialog.height() == tall
    dialog.close()
    dialog.deleteLater()


def test_closing_the_question_means_later_and_delete_is_never_the_default(qapp):
    dialog = drafts_dialog.DraftsDialog([(KIND_NEW, "글")])
    assert dialog._keep.isDefault() and not dialog._discard.isDefault()
    dialog.reject()                                     # Esc 또는 닫기
    assert dialog.choice() == drafts_dialog.LATER
    for button, choice in ((dialog._keep, drafts_dialog.KEEP),
                           (dialog._discard, drafts_dialog.DISCARD),
                           (dialog._later, drafts_dialog.LATER)):
        fresh = drafts_dialog.DraftsDialog([(KIND_NEW, "글")])
        {dialog._keep: fresh._keep, dialog._discard: fresh._discard,
         dialog._later: fresh._later}[button].click()
        assert fresh.choice() == choice
        fresh.deleteLater()
    dialog.deleteLater()
    assert confirm is not None
