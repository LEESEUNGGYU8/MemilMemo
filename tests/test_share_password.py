"""공유에 거는 비밀번호(offscreen).

비밀번호는 공유를 시작할 때 고른다(비워 두면 없다). 걸려 있으면 참여하는 PC 는
비밀번호를 안다는 것을 보여야 메모를 받고, 공유 PC 도 같은 것을 보여야 믿는다.
옛 버전과 섞여 도는 경우, 비밀번호를 걸거나 바꾸거나 없앤 뒤 전부터 참여하던 PC 가
겪는 일을 본다."""
from __future__ import annotations

import json
import os
import time
import unicodedata

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtNetwork import QHostAddress, QTcpServer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import main as app_main  # noqa: E402
from app import i18n, share_auth  # noqa: E402
from app.database import SqliteRepository  # noqa: E402
from app.local_state import LocalStateStore  # noqa: E402
from app.network.client_base import ClientBase  # noqa: E402
from app.network.guest_client import GuestClient  # noqa: E402
from app.network.host_client import HostLocalClient  # noqa: E402
from app.network.host_server import (  # noqa: E402
    AUTH_TRIES_PER_CONNECTION, AUTH_WAIT_SECONDS, HostServer, _Session,
)
from app.protocol import (  # noqa: E402
    MSG_ACQUIRE_LOCK, MSG_AUTH, MSG_AUTH_REQUIRED, MSG_HELLO, MSG_WELCOME,
    FrameParser, make,
)
from app.settings import AppSettings  # noqa: E402
from app.share_auth import ShareAuth, guest_proof, host_proof  # noqa: E402
from app.sync.history_tracker import HistoryTracker  # noqa: E402
from app.sync.lock_manager import LockManager  # noqa: E402
from app.ui import confirm, password_dialog, theme  # noqa: E402
from app.ui import main_window as mw  # noqa: E402
from app.ui.connect_dialog import ConnectDialog, ConnectionChoice  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.sticky_note import StickyNoteWidget  # noqa: E402

ME = "지우"
OTHER = "민수"
PASSWORD = "메밀 1234"


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    i18n.set_language("ko")
    theme.apply_theme(app, "")
    yield app


@pytest.fixture(scope="module")
def auth():
    """열쇠 만들기는 일부러 느리다. 한 번 만들어 같이 쓴다."""
    return ShareAuth.from_password(PASSWORD)


# ── 열쇠와 증명 ─────────────────────────────────────────────────────────

def test_blank_means_no_password():
    assert share_auth.clean_password("") == ""
    assert share_auth.clean_password("   ") == ""
    assert share_auth.clean_password(None) == ""
    assert ShareAuth.from_password("  ") is None
    # 글자 사이와 앞뒤의 빈칸은 쓴 사람이 넣은 것이다.
    assert share_auth.clean_password(" a b ") == " a b "


def test_the_same_letters_typed_differently_are_the_same_password(auth):
    """한글을 풀어 넣는 입력기와 모아 넣는 입력기가 같은 비밀번호로 들어가야 한다."""
    apart = unicodedata.normalize("NFD", PASSWORD)
    assert apart != PASSWORD
    again = ShareAuth.for_challenge(apart, auth.salt.hex(), auth.iterations)
    assert again.key == auth.key


def test_only_the_key_is_kept_never_the_password(auth):
    stored = json.dumps(auth.to_dict(), ensure_ascii=False)
    assert PASSWORD not in stored and "1234" not in stored
    back = ShareAuth.from_dict(json.loads(stored))
    assert back == auth
    # 같은 비밀번호라도 정할 때마다 소금이 달라 열쇠가 다르다.
    assert ShareAuth.from_password(PASSWORD).key != auth.key


@pytest.mark.parametrize("raw", [
    None, "글", {}, {"salt": "zz", "key": "00", "iterations": 200000},
    {"salt": "00" * 16, "key": "00" * 32, "iterations": 1},
    {"salt": "00" * 16, "key": "00" * 32, "iterations": 10 ** 9},
    {"salt": "00" * 16, "key": "00" * 32, "iterations": "200000"},
    {"salt": "00" * 15, "key": "00" * 32, "iterations": 200000},
])
def test_a_damaged_or_hostile_record_is_not_used(raw):
    assert ShareAuth.from_dict(raw) is None


def test_a_proof_cannot_be_reused_for_another_side_name_or_time(auth):
    proof = guest_proof(auth.key, "a" * 32, "b" * 32, ME)
    assert proof != guest_proof(auth.key, "c" * 32, "b" * 32, ME)      # 다른 접속
    assert proof != guest_proof(auth.key, "a" * 32, "b" * 32, OTHER)   # 다른 이름
    assert proof != host_proof(auth.key, "a" * 32, "b" * 32)           # 반대편
    assert not share_auth.same(proof, None) and share_auth.same(proof, proof)


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
    def __init__(self, peer: str = "192.168.0.21"):
        self.peer = peer
        self.closed = False

    def peerAddress(self):
        return QHostAddress(self.peer)

    def bytesToWrite(self) -> int:
        return 0

    def disconnectFromHost(self) -> None:
        self.closed = True

    def deleteLater(self) -> None:
        pass


class Host:
    def __init__(self, tmp_path, auth: ShareAuth | None):
        self.repo = SqliteRepository(tmp_path / "notes.sqlite3")
        self.server = HostServer(self.repo, LockManager(60_000),
                                 HistoryTracker(self.repo), host_nickname=OTHER,
                                 auth=auth)
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

    def connect(self, peer: str = "192.168.0.21") -> _Session:
        sock = FakeSocket(peer)
        session = _Session(socket=sock, parser=FrameParser())
        self.server._sessions[sock] = session
        self.heard[id(session)] = Recorder()
        return session

    def hello(self, session: _Session, nickname: str = ME, *, new: bool = True) -> None:
        data = {"nickname": nickname}
        if new:
            data["auth"] = 1
        self.server._handle(session, make(MSG_HELLO, **data))

    def said(self, session: _Session) -> Recorder:
        return self.heard[id(session)]

    def prove(self, session: _Session, key: bytes, nickname: str = ME,
              nonce: str = "5" * 32) -> None:
        asked = (self.said(session).of("auth_required")
                 + self.said(session).of("auth_failed"))[-1]
        self.server._handle(session, make(
            MSG_AUTH, nonce=nonce,
            proof=guest_proof(key, asked["nonce"], nonce, nickname)))


@pytest.fixture
def locked(tmp_path, auth):
    h = Host(tmp_path, auth)
    h.client.create_note("#FFE066", 280, 260)
    yield h
    h.repo.close()


@pytest.fixture
def unlocked(tmp_path):
    h = Host(tmp_path, None)
    yield h
    h.repo.close()


def test_sharing_without_a_password_works_as_before(unlocked):
    guest = unlocked.connect()
    unlocked.hello(guest)
    welcome = unlocked.said(guest).of("welcome")[0]
    assert welcome["locked"] is False and "auth" not in welcome
    assert unlocked.seen.of("user_joined") == [{"nickname": ME}]


def test_no_notes_are_sent_before_the_password_is_shown(locked, auth):
    guest = locked.connect()
    locked.hello(guest)

    assert locked.said(guest).kinds() == ["auth_required"]
    asked = locked.said(guest).of("auth_required")[0]
    assert asked["salt"] == auth.salt.hex() and asked["iterations"] == auth.iterations
    assert locked.seen.of("user_joined") == [] and not guest.hello_received
    # 그 사이에 보낸 요청은 받지 않는다.
    locked.server._handle(guest, make(MSG_ACQUIRE_LOCK, note_id="x"))
    assert locked.said(guest).kinds() == ["auth_required", "error"]

    locked.prove(guest, auth.key)

    welcome = locked.said(guest).of("welcome")[0]
    assert welcome["locked"] is True and len(welcome["notes"]) == 1
    # 공유 PC 도 비밀번호를 안다는 것을 보인다.
    assert welcome["auth"] == host_proof(auth.key, asked["nonce"], "5" * 32)
    assert locked.seen.of("user_joined") == [{"nickname": ME}]


def test_the_sharing_pc_itself_is_never_asked(locked):
    assert locked.seen.of("welcome")[0]["locked"] is True
    assert locked.seen.of("auth_required") == []


def test_a_wrong_password_gets_nothing_and_a_fresh_question(locked, auth):
    guest = locked.connect()
    locked.hello(guest)
    first = locked.said(guest).of("auth_required")[0]["nonce"]
    wrong = ShareAuth.for_challenge("틀린 것", auth.salt.hex(), auth.iterations)

    locked.prove(guest, wrong.key)

    failed = locked.said(guest).of("auth_failed")[0]
    assert failed["left"] == AUTH_TRIES_PER_CONNECTION - 1
    assert failed["nonce"] != first and failed["salt"] == auth.salt.hex()
    assert locked.said(guest).of("welcome") == [] and not guest.socket.closed
    # 다시 맞게 넣으면 들어간다.
    locked.prove(guest, auth.key)
    assert len(locked.said(guest).of("welcome")) == 1


def test_an_overheard_proof_does_not_open_another_connection(locked, auth):
    first = locked.connect()
    locked.hello(first)
    asked = locked.said(first).of("auth_required")[0]["nonce"]
    overheard = guest_proof(auth.key, asked, "5" * 32, ME)

    thief = locked.connect("192.168.0.99")
    locked.hello(thief)
    locked.server._handle(thief, make(MSG_AUTH, nonce="5" * 32, proof=overheard))

    assert locked.said(thief).of("welcome") == []


def test_a_proof_made_for_one_name_does_not_admit_another(locked, auth):
    guest = locked.connect()
    locked.hello(guest, nickname=OTHER)         # 남의 이름으로 인사하고
    locked.prove(guest, auth.key, nickname=ME)  # 제 이름으로 만든 증명을 낸다
    assert locked.said(guest).of("welcome") == []


def test_guessing_is_slowed_down(locked, auth):
    wrong = ShareAuth.for_challenge("틀린 것", auth.salt.hex(), auth.iterations)
    guest = locked.connect("192.168.0.50")
    locked.hello(guest)
    for _ in range(AUTH_TRIES_PER_CONNECTION):
        locked.prove(guest, wrong.key)

    last = locked.said(guest).of("auth_failed")[-1]
    assert last["left"] == 0 and last["wait"] == AUTH_WAIT_SECONDS
    assert guest.socket.closed

    # 그 주소는 한동안 묻지도 않고 돌려보낸다. 맞는 비밀번호여도 기다려야 한다.
    again = locked.connect("192.168.0.50")
    locked.hello(again)
    assert locked.said(again).kinds() == ["auth_failed"]
    assert 0 < locked.said(again).of("auth_failed")[0]["wait"] <= AUTH_WAIT_SECONDS
    assert again.socket.closed
    # 다른 PC 는 영향을 받지 않는다.
    other = locked.connect("192.168.0.51")
    locked.hello(other)
    locked.prove(other, auth.key)
    assert len(locked.said(other).of("welcome")) == 1


def test_an_old_version_is_told_why_and_let_go(locked):
    """1.0.2 까지는 비밀번호를 모른다. 붙여 두면 그 PC 는 연결된 줄 알고 메모를
    고치려 든다. 까닭을 그쪽 화면에 띄우고 끊는다."""
    locked.server.old_version_message = i18n.t("auth.old_version")
    old = locked.connect()

    locked.hello(old, new=False)

    assert locked.said(old).kinds() == ["error"]
    assert "업데이트" in locked.said(old).of("error")[0]["message"]
    assert old.socket.closed and not old.hello_received
    assert locked.seen.of("user_joined") == []
    assert ME not in locked.server._current_users()


def test_an_old_version_still_joins_when_there_is_no_password(unlocked):
    old = unlocked.connect()
    unlocked.hello(old, new=False)
    assert unlocked.said(old).kinds() == ["welcome"]


def test_restoring_a_backup_reaches_people_who_already_showed_the_password(locked, auth):
    guest = locked.connect()
    locked.hello(guest)
    locked.prove(guest, auth.key)
    locked.said(guest).messages.clear()

    locked.server.resync_all()

    assert locked.said(guest).kinds() == ["welcome"]


def test_what_the_sharing_pc_tells_a_searcher(locked, unlocked):
    told = locked.server.beacon(5601)
    assert told["host"] == OTHER and told["port"] == 5601 and told["locked"] is True
    assert told["host_id"] == locked.repo.host_id() and told["took_over_from"] == ""
    # 비밀번호에서 나온 값은 싣지 않는다(누구나 물어볼 수 있는 답이다).
    assert set(told) == {"host", "port", "host_id", "took_over_from", "locked", "ver"}
    assert unlocked.server.beacon(5555)["locked"] is False


# ── 참여하는 PC(실제 소켓, 이 PC 안에서만) ──────────────────────────────

class Wire:
    """공유 PC 한 대를 127.0.0.1 의 임시 포트에 띄우고 참여하는 PC 를 붙인다."""

    def __init__(self, qapp, tmp_path, auth: ShareAuth | None, name: str = "a"):
        self.qapp = qapp
        self.repo = SqliteRepository(tmp_path / f"{name}.sqlite3")
        self.server = HostServer(self.repo, LockManager(60_000),
                                 HistoryTracker(self.repo), host_nickname=OTHER,
                                 auth=auth)
        self.server.attach_local_host()
        assert self.server._server.listen(QHostAddress(QHostAddress.LocalHost), 0)
        self.port = self.server._server.serverPort()
        self.clients: list[GuestClient] = []

    def guest(self, key: ShareAuth | None = None, port: int | None = None):
        client = GuestClient("127.0.0.1", port or self.port, ME)
        client.set_auth(key)
        log = Recorder()
        client.welcomed.connect(lambda d: log("welcome", d))
        client.connectionChanged.connect(lambda on: log("connected" if on else "lost", {}))
        client.passwordRequired.connect(lambda d: log("ask", d))
        client.passwordAccepted.connect(lambda d: log("accepted", d))
        client.hostOpen.connect(lambda: log("open", {}))
        client.hostUnverified.connect(lambda: log("unverified", {}))
        client.followed.connect(lambda h, p: log("followed", {"port": p}))
        client.followFailed.connect(lambda h, p: log("follow failed", {"port": p}))
        client.errorOccurred.connect(lambda m: log("error", {"message": m}))
        self.clients.append(client)
        client.start()
        return client, log

    def wait(self, cond, seconds: float = 5.0) -> bool:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.qapp.processEvents()
            if cond():
                return True
            time.sleep(0.005)
        return bool(cond())

    def close(self) -> None:
        for client in self.clients:
            client.stop()
        self.qapp.processEvents()
        self.server.stop()
        # 끊긴 접속의 소켓은 지우기를 미뤄 둔다. 서버가 살아 있는 동안 마저 지운다
        # (processEvents 만으로는 미뤄 둔 지우기가 돌지 않는다).
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.qapp.processEvents()
        self.repo.close()


@pytest.fixture
def wire(qapp, tmp_path, auth):
    w = Wire(qapp, tmp_path, auth)
    yield w
    w.close()


@pytest.fixture
def open_wire(qapp, tmp_path):
    w = Wire(qapp, tmp_path, None)
    yield w
    w.close()


def test_joining_an_open_host_is_connected_once_the_notes_arrive(open_wire):
    client, log = open_wire.guest()
    assert open_wire.wait(lambda: log.of("welcome"))
    assert log.kinds() == ["connected", "welcome"]


def test_the_password_is_asked_and_remembered(wire, auth):
    client, log = wire.guest()
    assert wire.wait(lambda: log.of("ask"))
    # 비밀번호를 맞히기 전에는 연결된 것이 아니다.
    assert log.kinds() == ["ask"] and log.of("ask")[0] == {"wrong": False, "wait": 0}

    client.submit_password(PASSWORD)

    assert wire.wait(lambda: log.of("welcome"))
    assert log.kinds() == ["ask", "accepted", "connected", "welcome"]
    remembered = ShareAuth.from_dict(log.of("accepted")[0])
    assert remembered == auth

    # 다음에는 적어 둔 열쇠로 묻지 않고 들어간다.
    again, log2 = wire.guest(remembered)
    assert wire.wait(lambda: log2.of("welcome"))
    assert log2.kinds() == ["connected", "welcome"]


def test_a_wrong_password_is_asked_again(wire):
    client, log = wire.guest()
    assert wire.wait(lambda: log.of("ask"))
    client.submit_password("틀린 것")
    assert wire.wait(lambda: len(log.of("ask")) == 2)
    assert log.of("ask")[1]["wrong"] is True and log.of("welcome") == []

    client.submit_password(PASSWORD)
    assert wire.wait(lambda: log.of("welcome"))


def test_after_the_host_changes_its_password_the_old_key_is_not_even_tried(
        qapp, tmp_path, auth):
    """소금이 달라졌으면 적어 둔 열쇠는 맞을 리 없다. 틀린 증명을 보내 횟수를
    깎지 않고 바로 묻는다."""
    changed = Wire(qapp, tmp_path, ShareAuth.from_password("새 비밀번호"))
    try:
        client, log = changed.guest(auth)
        assert changed.wait(lambda: log.of("ask"))
        assert log.of("ask")[0]["wrong"] is False
        assert changed.server._auth_fails == {}
        client.submit_password("새 비밀번호")
        assert changed.wait(lambda: log.of("welcome"))
        assert ShareAuth.from_dict(log.of("accepted")[0]) == changed.server.auth
    finally:
        changed.close()


def test_a_host_that_dropped_its_password_is_not_trusted_silently(open_wire, auth):
    """비밀번호를 걸어 두었던 공유 PC 가 묻지도 않고 메모를 보낸다. 비밀번호를
    없앴을 수도, 그 PC 인 척하는 다른 PC 일 수도 있다. 사용자가 정한다."""
    client, log = open_wire.guest(auth)
    assert open_wire.wait(lambda: log.of("open"))
    assert log.of("welcome") == [] and log.of("connected") == []
    # 스스로 다시 붙으려 들지 않는다.
    open_wire.wait(lambda: False, 1.3)
    assert log.kinds().count("open") == 1

    client.set_auth(None)                 # 사용자가 '연결'을 골랐다
    client.resume()
    assert open_wire.wait(lambda: log.of("welcome"))


def test_a_pretender_that_does_not_know_the_password_is_refused(qapp, auth):
    """비밀번호를 묻는 시늉을 하고 아무 증명이나 받아 주는 상대."""
    server = QTcpServer()
    assert server.listen(QHostAddress(QHostAddress.LocalHost), 0)
    peers = []

    def serve():
        peer = server.nextPendingConnection()
        parser = FrameParser()
        peers.append(peer)

        def read():
            for message in parser.feed(bytes(peer.readAll())):
                if message.type == MSG_HELLO:
                    peer.write(make(MSG_AUTH_REQUIRED, salt=auth.salt.hex(),
                                    iterations=auth.iterations,
                                    nonce="7" * 32).encode())
                elif message.type == MSG_AUTH:
                    peer.write(make(MSG_WELCOME, notes=[], folders=[], locks={},
                                    users=[], host="가짜", host_id="x",
                                    auth="0" * 64).encode())
        peer.readyRead.connect(read)

    server.newConnection.connect(serve)
    client = GuestClient("127.0.0.1", server.serverPort(), ME)
    client.set_auth(auth)
    seen = Recorder()
    client.welcomed.connect(lambda d: seen("welcome", d))
    client.hostUnverified.connect(lambda: seen("unverified", {}))
    client.start()
    end = time.monotonic() + 5
    while time.monotonic() < end and not seen.messages:
        qapp.processEvents()
    assert seen.kinds() == ["unverified"]
    client.stop()
    server.close()


def test_giving_up_on_the_password_stops_the_retries(wire):
    client, log = wire.guest()
    assert wire.wait(lambda: log.of("ask"))
    client.pause()
    wire.wait(lambda: False, 1.5)
    assert len(log.of("ask")) == 1
    # 나중에 넣으면 그때 다시 붙는다.
    client.submit_password(PASSWORD)
    assert wire.wait(lambda: log.of("welcome"))


def test_a_found_address_is_adopted_only_when_the_password_matches(
        qapp, tmp_path, wire, auth):
    """주소가 바뀐 공유 PC 를 찾았을 때: 붙어 봐서 비밀번호로 확인되면 옮긴다."""
    moved = Wire(qapp, tmp_path, auth, name="moved")       # 같은 비밀번호, 새 주소
    stranger = Wire(qapp, tmp_path, ShareAuth.from_password("다른 것"), name="s")
    nobody = Wire(qapp, tmp_path, None, name="n")
    try:
        client, log = wire.guest(auth)
        assert wire.wait(lambda: log.of("welcome"))

        client.follow("127.0.0.1", stranger.port)          # 비밀번호가 다른 PC
        assert wire.wait(lambda: log.of("follow failed"))
        assert client.address() == ("127.0.0.1", wire.port)
        assert log.of("ask") == []                         # 묻지 않고 돌아온다

        client.follow("127.0.0.1", nobody.port)            # 비밀번호가 없는 PC
        assert wire.wait(lambda: len(log.of("follow failed")) == 2)
        assert log.of("open") == []

        assert wire.wait(lambda: len(log.of("welcome")) >= 2)   # 원래 자리로 돌아왔다
        client.follow("127.0.0.1", moved.port)
        assert wire.wait(lambda: log.of("followed"))
        assert client.address() == ("127.0.0.1", moved.port)
        assert log.of("followed")[0]["port"] == moved.port
    finally:
        for w in (moved, stranger, nobody):
            w.close()


# ── 메인 창 ─────────────────────────────────────────────────────────────

class FakeClient(ClientBase):
    def __init__(self):
        super().__init__(ME)
        self.calls: list[tuple] = []
        self.where = ("10.0.0.5", 5555)

    def submit_password(self, password):
        self.calls.append(("password", password))

    def pause(self):
        self.calls.append(("pause",))

    def resume(self):
        self.calls.append(("resume",))

    def set_auth(self, auth):
        self.calls.append(("auth", auth))

    def address(self):
        return self.where

    def set_address(self, host, port):
        self.where = (host, port)
        self.calls.append(("address", host, port))

    def follow(self, host, port):
        self.calls.append(("follow", host, port))


class FakeSearcher:
    made: list["FakeSearcher"] = []

    def __init__(self, *a, **k):
        from PySide6.QtCore import QObject, Signal

        class _Signals(QObject):
            finished = Signal(list)
        self._signals = _Signals()
        self.finished = self._signals.finished
        self.searches = 0
        FakeSearcher.made.append(self)

    def search(self, *a, **k):
        self.searches += 1


@pytest.fixture
def guest(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(StickyNoteWidget, "bring_to_front",
                        lambda self, *, start_editing=False: None)
    monkeypatch.setattr(mw, "Searcher", FakeSearcher)
    monkeypatch.setattr(mw.toast, "user_is_busy", lambda: False)
    FakeSearcher.made.clear()
    local = LocalStateStore(tmp_path / "local_state.json")
    local.state.last_host_ip, local.state.last_host_port = "10.0.0.5", 5555
    win = MainWindow(client=FakeClient(), local_state=local, role="guest",
                     settings=AppSettings())
    win.typed: list = []
    win.answers: list = []
    monkeypatch.setattr(
        win, "_ask_password",
        lambda wrong, wait: (win.typed.append((wrong, wait)),
                             win.answers.pop(0) if win.answers else None)[1])
    yield win
    win._force_quit = True
    win._teardown_stickies()
    win.close()
    win.deleteLater()
    qapp.processEvents()


def notice(win) -> tuple[str, str] | None:
    if not win._replica_banner.isVisibleTo(win):
        return None
    return win._replica_text.text(), win._copies_btn.text()


def test_the_password_typed_is_passed_on(qapp, guest):
    guest.answers = [PASSWORD]
    guest._client.passwordRequired.emit({"wrong": False, "wait": 0})
    qapp.processEvents()

    assert guest.typed == [(False, 0)]
    assert guest._client.calls == [("password", PASSWORD)]
    assert guest._conn_badge._state.text() == "비밀번호 필요"

    guest._client.passwordAccepted.emit({"salt": "aa", "key": "bb", "iterations": 1})
    guest._client.connectionChanged.emit(True)
    assert notice(guest) is None and guest._conn_badge._state.text() == "연결됨"
    # 열쇠는 파일에 적혀 다음 실행에 쓰인다.
    saved = json.loads(guest._local.path.read_text(encoding="utf-8"))
    assert saved["share_auth"] == {"salt": "aa", "key": "bb", "iterations": 1}


def test_cancelling_waits_with_a_line_to_try_again(qapp, guest):
    guest._client.passwordRequired.emit({"wrong": False, "wait": 0})
    qapp.processEvents()

    assert guest._client.calls == [("pause",)]            # 거듭 묻지 않게 멈춘다
    assert notice(guest) == ("비밀번호가 필요합니다", "입력")

    guest.answers = [PASSWORD]
    guest._copies_btn.click()
    assert guest._client.calls[-1] == ("password", PASSWORD)


def test_a_wrong_password_is_said_in_the_next_question(qapp, guest):
    guest.answers = ["틀린 것", PASSWORD]
    guest._client.passwordRequired.emit({"wrong": False, "wait": 0})
    qapp.processEvents()
    guest._client.passwordRequired.emit({"wrong": True, "wait": 30})
    qapp.processEvents()
    assert guest.typed == [(False, 0), (True, 30)]


def test_the_question_is_not_put_over_a_presentation(qapp, guest, monkeypatch):
    monkeypatch.setattr(mw.toast, "user_is_busy", lambda: True)
    guest._client.passwordRequired.emit({"wrong": False, "wait": 0})
    qapp.processEvents()
    assert guest.typed == [] and guest._client.calls == []
    assert notice(guest) == ("비밀번호가 필요합니다", "입력")


def test_a_host_without_its_password_needs_a_yes(qapp, guest, monkeypatch):
    guest._local.state.share_auth = {"salt": "aa", "key": "bb", "iterations": 1}
    asked = []
    monkeypatch.setattr(confirm, "ask", lambda *a, **k: (asked.append(a[2]), False)[1])
    guest._client.hostOpen.emit()
    qapp.processEvents()

    assert asked == [i18n.t("auth.open_body")]
    assert guest._client.calls == []                      # 붙지 않는다
    assert notice(guest) == ("비밀번호 없이 공유 중인 PC입니다", "연결")
    assert guest._local.state.share_auth is not None

    monkeypatch.setattr(confirm, "ask", lambda *a, **k: True)
    guest._copies_btn.click()
    assert guest._client.calls == [("auth", None), ("resume",)]
    assert guest._local.state.share_auth is None


def test_a_pretender_is_reported_and_left_alone(qapp, guest, monkeypatch):
    told = []
    monkeypatch.setattr(confirm, "notify", lambda *a, **k: told.append(a[2]))
    guest._client.hostUnverified.emit()
    qapp.processEvents()
    assert told == [i18n.t("auth.unverified_body")]
    assert notice(guest) == ("공유 PC를 확인하지 못했습니다", "다시 연결")
    guest._copies_btn.click()
    assert guest._client.calls == [("resume",)]


# ── 접속 창과 적어 두기 ─────────────────────────────────────────────────

def test_the_share_form_has_one_optional_password_line(qapp):
    dialog = ConnectDialog(last_nickname=OTHER)
    assert dialog._host_password.placeholderText() == "선택 사항"
    dialog.accept()
    assert dialog.choice().mode == "host" and dialog.choice().password == ""

    typed = ConnectDialog(last_nickname=OTHER)
    typed._host_password.setText(PASSWORD)
    typed.accept()
    assert typed.choice().password == PASSWORD
    # 가려서 보이고, 눈 단추로 잠깐 볼 수 있다.
    from PySide6.QtWidgets import QLineEdit
    assert typed._host_password.echoMode() == QLineEdit.Password
    typed._host_password.actions()[0].trigger()
    assert typed._host_password.echoMode() == QLineEdit.Normal


def test_only_spaces_is_no_password(qapp):
    dialog = ConnectDialog(last_nickname=OTHER)
    dialog._host_password.setText("    ")
    dialog.accept()
    assert dialog.choice().password == ""


def host_choice(password):
    return ConnectionChoice(mode="host", nickname=OTHER, port=5555, password=password)


def guest_choice(address="10.0.0.5", port=5555):
    return ConnectionChoice(mode="guest", nickname=ME, port=port, host_ip=address)


def test_the_password_is_set_changed_and_removed_from_the_share_form(tmp_path):
    store = LocalStateStore(tmp_path / "ls.json")
    app_main._persist_choice(store, host_choice(PASSWORD))
    first = ShareAuth.from_dict(store.state.share_auth)
    assert first is not None
    assert ShareAuth.for_challenge(PASSWORD, first.salt.hex(), first.iterations) == first
    assert PASSWORD not in store.path.read_text(encoding="utf-8")

    # 저장해 둔 대로 다시 켜질 때(접속 창을 거치지 않는다)는 그대로다.
    app_main._persist_choice(store, host_choice(None))
    assert ShareAuth.from_dict(LocalStateStore(store.path).state.share_auth) == first

    # 접속 창에서 비워 두면 없어진다.
    app_main._persist_choice(store, host_choice(""))
    assert store.state.share_auth is None
    assert "share_auth" not in json.loads(store.path.read_text(encoding="utf-8"))


def test_a_remembered_key_belongs_to_one_host(tmp_path, auth):
    store = LocalStateStore(tmp_path / "ls.json")
    app_main._persist_choice(store, guest_choice())
    store.state.share_auth = auth.to_dict()

    app_main._persist_choice(store, guest_choice())               # 같은 공유 PC
    assert store.state.share_auth == auth.to_dict()
    app_main._persist_choice(store, guest_choice("10.0.0.9"))     # 다른 공유 PC
    assert store.state.share_auth is None

    # 공유하던 PC 가 참여로 바꾸면, 제 비밀번호를 남의 공유 PC 의 것으로 쓰지 않는다.
    app_main._persist_choice(store, host_choice(PASSWORD))
    app_main._persist_choice(store, guest_choice("10.0.0.9"))
    assert store.state.share_auth is None


def test_a_file_from_an_older_version_has_no_password(tmp_path):
    path = tmp_path / "ls.json"
    path.write_text(json.dumps({"nickname": OTHER, "last_mode": "host",
                                "notes": {}}), encoding="utf-8")
    store = LocalStateStore(path)
    assert store.state.share_auth is None
    store.save()
    assert "share_auth" not in json.loads(path.read_text(encoding="utf-8"))


def test_the_password_window(qapp):
    dialog = password_dialog.PasswordDialog(wrong=True, wait=30)
    assert "맞지 않습니다" in dialog._note.text() and "30초" in dialog._note.text()
    assert not dialog._ok.isEnabled()                 # 빈칸으로는 확인할 수 없다
    dialog._field.setText("   ")
    assert not dialog._ok.isEnabled()
    dialog._field.setText(PASSWORD)
    assert dialog._ok.isEnabled() and dialog.password() == PASSWORD
    plain = password_dialog.PasswordDialog()
    assert plain._note.text() == ""


def test_the_participants_window_shows_that_a_password_is_on(qapp, guest):
    """공유 PC 에서 비밀번호가 걸려 있는지 확인하는 곳."""
    guest._open_sessions()
    header = guest._sessions_dialog._header
    guest._client.connectionChanged.emit(True)
    guest._client.welcomed.emit({"users": [ME, OTHER], "host": OTHER, "notes": [],
                                 "locks": {}, "folders": [], "heartbeat_ms": 3000,
                                 "locked": True})
    assert header._lock.isVisibleTo(header)
    assert header._lock.accessibleName() == "비밀번호 사용"
    guest._client.welcomed.emit({"users": [ME, OTHER], "host": OTHER, "notes": [],
                                 "locks": {}, "folders": [], "heartbeat_ms": 3000})
    assert not header._lock.isVisibleTo(header)
    guest._sessions_dialog.close()
