"""동기화와 저장 쪽 검토(2026-11-21)에서 나온 문제들(offscreen).

지운 메모의 이력이 남에게 보이던 것, 본문 거르기가 긴 글에 멈추던 것, 거절된 공개
범위 변경이 화면에 남던 것, 끝내는 순간의 글, 미뤄 둔 글, 같은 닉네임의 두 접속."""
from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QTextCursor  # noqa: E402
from PySide6.QtNetwork import QHostAddress, QTcpServer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app import config, html_guard, i18n  # noqa: E402
from app.database import SqliteRepository  # noqa: E402
from app.local_state import LocalStateStore  # noqa: E402
from app.network.client_base import ClientBase  # noqa: E402
from app.network.guest_client import GuestClient  # noqa: E402
from app.network.host_client import HostLocalClient  # noqa: E402
from app.network.host_server import MAX_QUEUED_REPLY, HostServer, _Session  # noqa: E402
from app.offline_drafts import KIND_EDIT, KIND_NEW, DraftStore  # noqa: E402
from app.protocol import (  # noqa: E402
    MSG_ACQUIRE_LOCK, MSG_GET_HISTORY, MSG_HELLO, MSG_RELEASE_LOCK,
    MSG_UPDATE_NOTE, FrameParser, make,
)
from app.settings import AppSettings  # noqa: E402
from app.sync.history_tracker import HistoryTracker  # noqa: E402
from app.sync.lock_manager import LockManager  # noqa: E402
from app.ui import theme  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
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


# ── 본문 거르기 ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("content", [
    "<" + "a" * 300_000,                            # '=' 없는 긴 글자열
    "<a " + "b" * 150_000 + '"=' + "c" * 150_000,
    "<p style='" + "/* " * 100_000 + "'>x</p>",     # 닫히지 않는 주석이 줄줄이
    "<style>" + "/* " * 100_000,
    "<a " + 'x="' * 100_000,
], ids=["long name", "name cut by a quote", "comments in a style",
        "comments in a style block", "unclosed quotes"])
def test_cleaning_a_long_note_does_not_stall_the_sharing_pc(content):
    """남이 보낸 프레임 하나에 걸리는 시간이 길이의 제곱으로 늘면, 30KB 로도
    공유 PC 가 10초쯤 멈춘다. 길이에 비례해야 한다."""
    started = time.perf_counter()
    html_guard.clean(content)
    assert time.perf_counter() - started < 2.0


def test_css_comments_still_cannot_hide_an_outside_address():
    hidden = '<p style="background: u/* */rl(\\\\srv\\a.png)">x</p>'
    assert "rl(" not in html_guard.clean(hidden)
    # 닫히지 않은 주석 안의 주소도 걸러 낸다(주석으로 치지 않는다).
    assert "url(" not in html_guard.clean('<p style="/* url(//srv/a.png)">x</p>')
    # 주석만 있는 스타일과 보통 스타일은 그대로다.
    plain = '<p style="/* 메모 */ color: #333; margin: 0">x</p>'
    assert html_guard.clean(plain) == plain


def test_attributes_are_still_read_after_the_speed_fix():
    assert html_guard.clean('<img src="\\\\srv\\share\\a.png">글') == "글"
    assert html_guard.clean("<img alt=x src=//srv/a.png>글") == "글"
    kept = '<img src="data:image/png;base64,AAAA" width="20">글'
    assert html_guard.clean(kept) == kept


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


class FakeSocket:
    """공유 PC 가 접속 하나에 대해 부르는 것만 흉내 낸다."""

    def __init__(self, queued: int = 0):
        self.queued = queued
        self.closed = False

    def bytesToWrite(self) -> int:
        return self.queued

    def disconnectFromHost(self) -> None:
        self.closed = True

    def deleteLater(self) -> None:
        pass


class Host:
    """공유 PC 한 대와, 거기 붙은 접속들(보낸 것을 접속마다 적어 둔다)."""

    def __init__(self, tmp_path, idle_timeout_ms: int = 60_000):
        self.repo = SqliteRepository(tmp_path / "notes.sqlite3")
        self.server = HostServer(self.repo, LockManager(idle_timeout_ms),
                                 HistoryTracker(self.repo), host_nickname=OTHER)
        self.client = HostLocalClient(self.server, OTHER)
        self.seen = Recorder()
        self.server.attach_local_host()
        self.server.set_local_subscriber(self.seen)
        self.client._send_hello()
        self.heard: dict[int, Recorder] = {}
        send = self.server._send_to

        def route(session, message):
            if session.socket is None:
                send(session, message)
            else:
                self.heard[id(session)](message.type, message.data)
        self.server._send_to = route

    def join(self, nickname: str, *, queued: int = 0) -> _Session:
        sock = FakeSocket(queued)
        session = _Session(socket=sock, parser=FrameParser())
        self.server._sessions[sock] = session
        self.heard[id(session)] = Recorder()
        self.server._handle(session, make(MSG_HELLO, nickname=nickname))
        return session

    def said_to(self, session: _Session) -> Recorder:
        return self.heard[id(session)]

    def leave(self, session: _Session) -> None:
        self.server._on_disconnected(session.socket)

    def new_note(self, **more) -> str:
        self.client.create_note("#FFE066", 280, 260, **more)
        return self.seen.of("note_created")[-1]["note"]["id"]

    def write(self, session: _Session, note_id: str, text: str) -> None:
        current = self.repo.get_note(note_id)
        self.server._handle(session, make(
            MSG_UPDATE_NOTE, id=note_id, content=text, color="#FFE066",
            width=280, height=260, expected_version=current.version))

    def history_of(self, note_id: str) -> list[str]:
        return [e.action for e in self.repo.list_history(note_id)]


@pytest.fixture
def host(tmp_path):
    h = Host(tmp_path)
    yield h
    h.repo.close()


@pytest.mark.parametrize("ending", ["delete the folder", "make it public"])
def test_history_of_a_note_deleted_in_my_private_folder_stays_mine(host, ending):
    """폴더에 기대어 나만 보던 메모를 지운 뒤 그 폴더를 지우거나 공개하면, 지운
    글 전체가 담긴 이력을 누구나 받아 볼 수 있었다."""
    host.client.create_folder("내 폴더", "#BAE1FF", private=True)
    folder_id = host.seen.of("folder_created")[-1]["folder"]["id"]
    note_id = host.new_note(folder_id=folder_id)
    host.client.acquire_lock(note_id)
    host.client.update_note(note_id, "나만 보던 글", "#FFE066", 280, 260, 1)
    host.client.release_lock(note_id)
    host.client.delete_note(note_id)
    guest = host.join(ME)

    if ending == "delete the folder":
        host.client.delete_folder(folder_id)
    else:
        host.client.update_folder(folder_id, private=False)
    host.server._handle(guest, make(MSG_GET_HISTORY, note_id=None))

    assert host.said_to(guest).of("history_list")[-1]["entries"] == []
    # 주인은 계속 본다.
    host.client.get_history()
    mine = host.seen.of("history_list")[-1]["entries"]
    assert {e["note_id"] for e in mine} == {note_id} and len(mine) == 3


def test_a_refused_change_is_answered_with_how_things_really_are(host):
    """청한 쪽은 화면을 먼저 바꿔 놓는다. 거절만 알리면 그 화면이 틀린 채 남는다."""
    host.client.create_folder("내 폴더", "#BAE1FF", private=True)
    private_folder = host.seen.of("folder_created")[-1]["folder"]["id"]
    host.client.create_folder("회의", "#BAE1FF")
    folder_id = host.seen.of("folder_created")[-1]["folder"]["id"]
    note_id = host.new_note(folder_id=folder_id)
    guest = host.join(ME)
    host.server._handle(guest, make(MSG_ACQUIRE_LOCK, note_id=note_id))
    host.seen.messages.clear()

    host.client.set_note_privacy(note_id, True)
    assert host.seen.kinds() == ["error", "note_updated"]
    assert host.seen.of("note_updated")[0]["note"]["private_owner"] is None

    host.seen.messages.clear()
    host.client.set_note_folder(note_id, private_folder)
    assert host.seen.kinds() == ["error", "note_updated"]
    assert host.seen.of("note_updated")[0]["note"]["folder_id"] == folder_id

    host.seen.messages.clear()
    host.client.update_folder(folder_id, private=True)
    assert host.seen.kinds() == ["error", "folder_updated"]
    assert host.seen.of("folder_updated")[0]["folder"]["private_owner"] is None


def test_my_other_pc_leaving_does_not_end_my_edit(host):
    """한 사람이 PC 두 대에서 같은 닉네임을 쓴다. 한쪽이 끊길 때 다른 쪽이 쓰고
    있는 메모의 잠금까지 풀면 그 편집이 도중에 끊긴다."""
    note_id = host.new_note()
    idle_note = host.new_note()
    desk = host.join(ME)
    laptop = host.join(ME)
    host.server._handle(desk, make(MSG_ACQUIRE_LOCK, note_id=note_id))
    host.server._handle(laptop, make(MSG_ACQUIRE_LOCK, note_id=idle_note))
    host.seen.messages.clear()
    host.said_to(desk).messages.clear()

    host.leave(laptop)

    assert host.server.locks.holder(note_id) == ME
    # 끊긴 쪽이 쥐고 있던 잠금만 풀린다. 그 사람은 아직 붙어 있다.
    assert host.server.locks.holder(idle_note) is None
    assert [d["note_id"] for d in host.said_to(desk).of("lock_released")] == [idle_note]
    assert host.seen.of("user_left") == []
    assert host.server._current_users().count(ME) == 1

    host.leave(desk)

    assert host.server.locks.holder(note_id) is None
    assert host.seen.of("user_left") == [{"nickname": ME}]


def test_reconnecting_before_the_old_connection_dies_keeps_the_new_edit(host):
    """다시 붙었는데 공유 PC 는 옛 접속이 죽은 줄 아직 모른다. 옛 접속이 정리될
    때 새 접속이 이어서 쓰던 메모의 잠금을 풀면 안 된다."""
    note_id = host.new_note()
    old = host.join(ME)
    host.server._handle(old, make(MSG_ACQUIRE_LOCK, note_id=note_id))
    again = host.join(ME)
    host.server._handle(again, make(MSG_ACQUIRE_LOCK, note_id=note_id))
    host.write(again, note_id, "이어서 쓴 글")
    host.said_to(again).messages.clear()

    host.leave(old)

    assert host.server.locks.holder(note_id) == ME
    assert host.said_to(again).of("lock_released") == []
    host.server._handle(again, make(MSG_RELEASE_LOCK, note_id=note_id))
    assert host.history_of(note_id) == ["update", "create"]


def test_an_edit_cut_off_by_a_lost_connection_is_in_the_history(host):
    note_id = host.new_note()
    guest = host.join(ME)
    host.server._handle(guest, make(MSG_ACQUIRE_LOCK, note_id=note_id))
    host.write(guest, note_id, "저장까지 된 글")
    host.seen.messages.clear()

    host.leave(guest)

    assert host.history_of(note_id) == ["update", "create"]
    assert host.seen.of("history_appended")[0]["entry"]["content_after"] == "저장까지 된 글"


def test_a_lock_taken_back_for_idleness_still_leaves_history(tmp_path):
    host = Host(tmp_path, idle_timeout_ms=-1)           # 손대지 않은 것으로 친다
    try:
        note_id = host.new_note()
        guest = host.join(ME)
        host.server._handle(guest, make(MSG_ACQUIRE_LOCK, note_id=note_id))
        host.write(guest, note_id, "거두기 전에 저장된 글")

        host.server._on_idle_reap()

        assert host.server.locks.holder(note_id) is None
        assert host.history_of(note_id) == ["update", "create"]
        # 잠금을 거둔 줄 모르고 계속 쓰는 옛 버전의 글도 끝낼 때 이력에 남는다.
        host.write(guest, note_id, "거둔 뒤에 더 쓴 글")
        host.server._handle(guest, make(MSG_RELEASE_LOCK, note_id=note_id))
        assert host.history_of(note_id) == ["update", "update", "create"]
    finally:
        host.repo.close()


def test_an_endless_nickname_is_turned_away(host):
    """닉네임은 모든 참가자에게 알려지고 각 PC 의 파일에 적힌다."""
    host.seen.messages.clear()
    guest = host.join("가" * (config.MAX_NICKNAME_LENGTH + 1))

    assert not guest.hello_received and guest.socket.closed
    assert host.said_to(guest).kinds() == ["error"]
    assert host.seen.of("user_joined") == []
    # 한도까지는 받는다.
    assert host.join("가" * config.MAX_NICKNAME_LENGTH).hello_received


def test_history_is_not_sent_again_while_the_last_answer_is_still_queued(host):
    host.new_note()
    busy = host.join(ME, queued=MAX_QUEUED_REPLY + 1)
    host.said_to(busy).messages.clear()

    host.server._handle(busy, make(MSG_GET_HISTORY, note_id=None))
    assert host.said_to(busy).of("history_list") == []

    busy.socket.queued = 0
    host.server._handle(busy, make(MSG_GET_HISTORY, note_id=None))
    assert len(host.said_to(busy).of("history_list")) == 1


# ── 참여한 PC ───────────────────────────────────────────────────────────

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

    def set_note_privacy(self, note_id, private):
        self.calls.append(("privacy", note_id, private))

    def set_note_folder(self, note_id, folder_id):
        self.calls.append(("folder", note_id, folder_id))

    def of(self, kind: str) -> list[tuple]:
        return [c for c in self.calls if c[0] == kind]


def make_guest(tmp_path, store: DraftStore) -> MainWindow:
    win = MainWindow(client=FakeClient(),
                     local_state=LocalStateStore(tmp_path / "ls.json"),
                     role="guest", settings=AppSettings(), drafts=store)
    win.asked = []
    win.answer = "later"
    win._choose_for_drafts = lambda drafts: (
        win.asked.append(sorted((d.kind, d.content) for d in drafts)) or win.answer)
    return win


def close(qapp, win) -> None:
    win._force_quit = True
    win._teardown_stickies()
    win.close()
    win.deleteLater()
    qapp.processEvents()


@pytest.fixture
def store(tmp_path):
    s = DraftStore(tmp_path / "offline.sqlite3")
    yield s
    s.close()


@pytest.fixture
def guest(qapp, tmp_path, monkeypatch, store):
    from app.ui import main_window as mw
    monkeypatch.setattr(StickyNoteWidget, "bring_to_front",
                        lambda self, *, start_editing=False: None)
    monkeypatch.setattr(mw, "DRAFTS_ASK_MS", 0)
    win = make_guest(tmp_path, store)
    yield win
    close(qapp, win)


def welcome(win, *notes: dict, locks=None, folders=()) -> None:
    win._client.connectionChanged.emit(True)
    win._client.welcomed.emit({"users": [ME, OTHER], "host": OTHER,
                               "notes": list(notes), "locks": dict(locks or {}),
                               "folders": list(folders), "heartbeat_ms": 3000})
    QApplication.processEvents()


def type_into(sticky: StickyNoteWidget, text: str) -> None:
    cursor = sticky._text.textCursor()
    cursor.movePosition(QTextCursor.End)
    cursor.insertText(text)


def start_editing(win, note_id: str, text: str) -> StickyNoteWidget:
    win._set_note_visibility(note_id, True)
    sticky = win._stickies[note_id]
    sticky._request_edit()
    if win._connected:
        win._client.lockGranted.emit(note_id)
    assert sticky.is_editing()
    type_into(sticky, text)
    return sticky


def test_a_note_someone_is_editing_is_not_shown_as_private(guest):
    """공유 PC 는 거절한다. 화면을 먼저 바꿔 두면 공개된 메모가 나만 보기로 보인다."""
    welcome(guest, note("a", "회의록"), note("b", "장보기"), locks={"a": OTHER})

    guest._on_card_private_toggled("a", True)

    assert guest._notes["a"].get("private_owner") is None
    assert guest._client.of("privacy") == []
    # 아무도 쓰지 않는 메모는 전처럼 바뀐다.
    guest._on_card_private_toggled("b", True)
    assert guest._notes["b"]["private_owner"] == ME
    assert guest._client.of("privacy") == [("privacy", "b", True)]


def test_only_the_free_notes_of_a_selection_are_made_private(guest):
    welcome(guest, note("a", "회의록"), note("b", "장보기"), locks={"a": OTHER})
    guest._selected_notes = {"a", "b"}

    guest._bulk_apply_privacy(True)

    assert guest._notes["a"].get("private_owner") is None
    assert guest._client.of("privacy") == [("privacy", "b", True)]


def test_a_note_someone_is_editing_is_not_moved_out_of_their_sight(guest):
    mine = {"id": "f1", "name": "내 폴더", "color": "#BAE1FF", "private_owner": ME}
    shared = {"id": "f2", "name": "회의", "color": "#BAE1FF", "private_owner": None}
    welcome(guest, note("a", "회의록"), locks={"a": OTHER}, folders=[mine, shared])

    guest._on_card_move_to_folder("a", "f1")
    assert guest._notes["a"].get("folder_id") is None
    assert guest._client.of("folder") == []
    # 보는 사람이 달라지지 않는 이동은 된다.
    guest._on_card_move_to_folder("a", "f2")
    assert guest._client.of("folder") == [("folder", "a", "f2")]


def test_a_postponed_text_is_not_lost_to_a_later_edit_of_the_same_note(guest, store):
    """끊긴 동안 고친 글을 보류해 두고, 붙어 있을 때 같은 메모를 다시 쓰다가
    끊기면 먼저 쓴 글이 묻지도 않고 사라졌다."""
    welcome(guest, note("a", "회의록", version=4))
    guest._client.connectionChanged.emit(False)
    start_editing(guest, "a", " 끊긴 동안 쓴 문단")._commit_and_release()
    (first,) = store.pending(ME)
    assert first.kind == KIND_EDIT

    welcome(guest, note("a", "회의록", version=4))            # 묻는 창에서 '보류'
    assert len(guest.asked) == 1 and len(store.pending(ME)) == 1
    sticky = start_editing(guest, "a", " 붙어 있을 때 쓴 글")
    guest._client.connectionChanged.emit(False)             # 쓰는 도중에 끊겼다
    sticky._commit_and_release()

    kept = {d.kind: d for d in store.pending(ME)}
    assert set(kept) == {KIND_NEW, KIND_EDIT}
    assert "끊긴 동안 쓴 문단" in kept[KIND_NEW].content      # 새 메모로 돌려 두었다
    assert kept[KIND_NEW].note["private_owner"] == ME
    assert "붙어 있을 때 쓴 글" in kept[KIND_EDIT].content
    # 끊긴 채로 더 써도 방금 쓰던 글을 또 떼어 놓지 않는다.
    sticky._request_edit()
    type_into(sticky, " 더 쓴 글")
    sticky._commit_and_release()
    assert len(store.pending(ME)) == 2


def test_text_typed_just_before_quitting_is_also_kept_on_this_pc(qapp, guest, store,
                                                                tmp_path):
    """끝내는 순간에 보낸 글은 닿았는지 알 수 없다. 이 PC 에도 적어 두었다가
    다음에 붙을 때 가린다."""
    welcome(guest, note("a", "회의록", version=4))
    start_editing(guest, "a", " 끝내기 직전에 쓴 글")

    guest._flush_pending_edits()

    sent = guest._client.of("update")[-1]
    (kept,) = store.pending(ME)
    assert kept.kind == KIND_EDIT and kept.content == sent[2]
    assert kept.started_online and kept.base_version == 4

    # 다음 실행. 글이 닿아 있었으면 조용히 지운다.
    again = make_guest(tmp_path / "second", store)
    try:
        welcome(again, note("a", sent[2], version=5, updated_by=ME))
        assert store.pending(ME) == [] and again.asked == []
    finally:
        close(qapp, again)


def test_text_that_did_not_reach_the_host_before_quitting_is_put_back(qapp, guest,
                                                                     store, tmp_path):
    welcome(guest, note("a", "회의록", version=4))
    start_editing(guest, "a", " 닿지 못한 글")
    guest._flush_pending_edits()
    text = guest._client.of("update")[-1][2]

    again = make_guest(tmp_path / "second", store)
    try:
        welcome(again, note("a", "회의록", version=4))        # 공유 PC 의 글은 그대로다
        assert again.asked == []                            # 묻지 않고 제자리에 넣는다
        assert again._client.of("lock") == [("lock", "a")]
        again._client.lockGranted.emit("a")
        assert again._client.of("update")[-1][1:3] == ("a", text)
    finally:
        close(qapp, again)


def test_nothing_is_kept_when_quitting_without_unsent_text(guest, store):
    welcome(guest, note("a", "회의록", version=4))
    guest._set_note_visibility("a", True)

    guest._flush_pending_edits()

    assert store.pending(ME) == []


# ── 실제 소켓 ───────────────────────────────────────────────────────────

def test_the_last_save_leaves_the_socket_before_the_program_ends(qapp):
    """소켓은 이벤트 루프가 돌 때 보낸다. 끝내는 길에는 루프가 더 돌지 않으므로
    stop() 이 돌아오기 전에 내보내야 한다."""
    server = QTcpServer()
    assert server.listen(QHostAddress(QHostAddress.LocalHost), 0)
    client = GuestClient("127.0.0.1", server.serverPort(), ME)
    client.start()
    deadline = time.monotonic() + 5
    peer = None
    while time.monotonic() < deadline and peer is None:
        qapp.processEvents()
        peer = server.nextPendingConnection()
    assert peer is not None
    while time.monotonic() < deadline and not peer.bytesAvailable():
        qapp.processEvents()                    # 인사가 왔다
    peer.readAll()

    client.update_note("a", "끝내기 직전에 쓴 글", "#FFE066", 280, 260, 4)
    client.release_lock("a")
    assert client._sock.bytesToWrite() > 0
    client.stop()
    assert client._sock.bytesToWrite() == 0     # 루프를 돌리지 않고도 나갔다

    parser = FrameParser()
    got: list[str] = []
    while time.monotonic() < deadline and len(got) < 2:
        qapp.processEvents()
        got += [m.type for m in parser.feed(bytes(peer.readAll()))]
    assert got == ["update_note", "release_lock"]
    peer.close()
    server.close()


# ── 끝내는 순간의 마지막 글 ─────────────────────────────────────────────

class _ThreadHost:
    """다른 PC 의 공유 PC 처럼, 이 테스트의 이벤트 루프와 따로 도는 상대.

    참여하는 PC 는 끝낼 때 답을 기다리며 멈춰 있다. 같은 이벤트 루프의 서버는 그
    사이에 답할 수 없으므로 스레드로 흉내 낸다."""

    def __init__(self, *, answer: bool, delay: float = 0.15):
        import socket
        import threading
        self.answer, self.delay = answer, delay
        self.got: list[str] = []
        self._listener = socket.socket()
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(1)
        self.port = self._listener.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        conn, _addr = self._listener.accept()
        conn.settimeout(3)
        parser = FrameParser()
        try:
            while True:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                for message in parser.feed(chunk):
                    self.got.append(message.type)
                    if message.type == MSG_HELLO:
                        conn.sendall(make("welcome", notes=[], folders=[], locks={},
                                          users=[], host=OTHER, host_id="H").encode())
                    elif (message.type == MSG_ACQUIRE_LOCK and self.answer
                          and str(message.data.get("note_id")).startswith("bye-")):
                        time.sleep(self.delay)
                        conn.sendall(make("lock_denied", holder="", reason="not_found",
                                          note_id=message.data["note_id"]).encode())
        except OSError:
            pass
        finally:
            conn.close()

    def close(self) -> None:
        self._listener.close()
        self._thread.join(3)


def _join(qapp, host: _ThreadHost) -> GuestClient:
    client = GuestClient("127.0.0.1", host.port, ME)
    joined = []
    client.welcomed.connect(joined.append)
    client.start()
    end = time.monotonic() + 5
    while time.monotonic() < end and not joined:
        qapp.processEvents()
        time.sleep(0.005)
    assert joined
    return client


def test_quitting_waits_until_the_host_has_taken_the_last_save(qapp):
    """보내자마자 닫으면 Windows 가 연결을 끊어 버리는 수가 있고, 그러면 공유 PC 는
    아직 읽지 않은 마지막 글을 잃는다. 공유 PC 가 거기까지 처리했다는 답을 받고
    닫는다."""
    host = _ThreadHost(answer=True)
    try:
        client = _join(qapp, host)
        client.update_note("a", "끝내기 직전에 쓴 글", "#FFE066", 280, 260, 4)
        client.release_lock("a")
        started = time.monotonic()
        client.stop()
        took = time.monotonic() - started
        assert host.got[:3] == ["hello", "update_note", "release_lock"]
        assert host.got[3] == "acquire_lock"          # 마지막 물음
        assert 0.1 <= took < 0.6                      # 답이 올 때까지만 기다렸다
    finally:
        host.close()


def test_quitting_does_not_hang_on_a_host_that_never_answers(qapp):
    host = _ThreadHost(answer=False)
    try:
        client = _join(qapp, host)
        client.update_note("a", "글", "#FFE066", 280, 260, 4)
        started = time.monotonic()
        client.stop()
        assert time.monotonic() - started < 1.5
    finally:
        host.close()


def test_the_last_question_is_one_every_host_version_answers(host):
    """없는 메모의 잠금을 청하면 옛 버전의 공유 PC 도 거절하는 답을 한다."""
    guest = host.join(ME)
    host.said_to(guest).messages.clear()
    host.server._handle(guest, make(MSG_ACQUIRE_LOCK, note_id="bye-1234"))
    (denied,) = host.said_to(guest).of("lock_denied")
    assert denied["note_id"] == "bye-1234"
    assert host.server.locks.holder("bye-1234") is None


def test_what_arrived_just_before_the_disconnect_is_still_handled(host):
    """끊김을 먼저 알게 되어도, 그 전에 온 마지막 저장을 버리지 않는다."""
    note_id = host.new_note()
    guest = host.join(ME)
    host.server._handle(guest, make(MSG_ACQUIRE_LOCK, note_id=note_id))
    current = host.repo.get_note(note_id)
    left = (make(MSG_UPDATE_NOTE, id=note_id, content="끝내기 직전에 쓴 글",
                 color="#FFE066", width=280, height=260,
                 expected_version=current.version).encode()
            + make(MSG_RELEASE_LOCK, note_id=note_id).encode())
    sock = guest.socket
    sock.pending = left
    sock.bytesAvailable = lambda: len(sock.pending)

    def read_all():
        data, sock.pending = sock.pending, b""
        return data
    sock.readAll = read_all

    host.leave(guest)

    assert host.repo.get_note(note_id).content == "끝내기 직전에 쓴 글"
    assert host.history_of(note_id) == ["update", "create"]
