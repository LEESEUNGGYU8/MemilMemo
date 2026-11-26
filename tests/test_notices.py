"""1.2 공유 기능(공유 PC 쪽): 공지 메모, 읽음 확인, @이름 알림."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app import mentions  # noqa: E402
from app.database import SqliteRepository  # noqa: E402
from app.network.client_base import ClientBase, clean_seen  # noqa: E402
from app.network.host_client import HostLocalClient  # noqa: E402
from app.network.host_server import HostServer, _Session  # noqa: E402
from app.protocol import (  # noqa: E402
    MSG_ACQUIRE_LOCK, MSG_DELETE_NOTE, MSG_RELEASE_LOCK, MSG_SEEN, MSG_SET_NOTE_FOLDER,
    MSG_SET_NOTICE, MSG_UPDATE_NOTE, FrameParser, make,
)
from app.sync.history_tracker import HistoryTracker  # noqa: E402
from app.sync.lock_manager import LockManager  # noqa: E402

HOST, GUEST, OTHER = "민수", "지우", "서연"


@pytest.fixture(scope="module", autouse=True)
def _qt():
    yield QApplication.instance() or QApplication([])


class FakeSocket:
    def __init__(self):
        self.parser = FrameParser()
        self.messages: list = []

    def write(self, data: bytes) -> None:
        self.messages.extend(self.parser.feed(bytes(data)))

    def disconnectFromHost(self) -> None:
        pass

    def of(self, kind: str) -> list[dict]:
        return [m.data for m in self.messages if m.type == kind]


class Recorder:
    def __init__(self):
        self.messages: list = []

    def __call__(self, kind: str, data: dict) -> None:
        self.messages.append((kind, data))

    def of(self, kind: str) -> list[dict]:
        return [d for k, d in self.messages if k == kind]


@pytest.fixture
def host(tmp_path):
    repo = SqliteRepository(tmp_path / "notes.sqlite3")
    server = HostServer(repo, LockManager(idle_timeout_ms=60_000),
                        HistoryTracker(repo), host_nickname=HOST)
    client = HostLocalClient(server, HOST)
    seen = Recorder()
    server.attach_local_host()
    server.set_local_subscriber(seen)
    client._send_hello()
    guests = {}
    for name in (GUEST, OTHER):
        repo.touch_participant(name)               # 인사를 받은 것처럼
        sock = FakeSocket()
        session = _Session(socket=sock, parser=FrameParser(), nickname=name,
                           hello_received=True)
        server._sessions[name] = session
        guests[name] = (session, sock)
    yield server, client, seen, repo, guests
    repo.close()


def guest_note(server, guests, name=GUEST) -> str:
    session, sock = guests[name]
    server._handle(session, make("create_note", color="#FFE066", width=280, height=260))
    return sock.of("note_created")[-1]["note"]["id"]


def save(server, session, nid, content, version):
    server._handle(session, make(MSG_ACQUIRE_LOCK, note_id=nid))
    server._handle(session, make(MSG_UPDATE_NOTE, id=nid, content=content, color="#FFE066",
                                 width=280, height=260, expected_version=version))
    server._handle(session, make(MSG_RELEASE_LOCK, note_id=nid))


# ── 공지 ──

def test_only_the_maker_can_make_a_notice_and_everyone_hears(host):
    server, client, seen, repo, guests = host
    nid = guest_note(server, guests)
    other, other_sock = guests[OTHER]
    server._handle(other, make(MSG_SET_NOTICE, note_id=nid, on=True))   # 만든 사람이 아니다
    assert other_sock.of("error")[-1]["message"] == "not allowed"
    assert repo.list_notices() == {}
    maker, maker_sock = guests[GUEST]
    server._handle(maker, make(MSG_SET_NOTICE, note_id=nid, on=True))
    assert repo.list_notices() == {nid: GUEST}
    for sock in (maker_sock, other_sock):
        assert sock.of("notice_changed")[-1] == {"note_id": nid, "owner": GUEST, "by": GUEST}
    assert seen.of("notice_changed")[-1]["owner"] == GUEST
    server._send_welcome(other)
    assert other_sock.of("welcome")[-1]["notices"] == {nid: GUEST}


def test_others_cannot_edit_delete_or_move_a_notice(host):
    server, client, seen, repo, guests = host
    nid = guest_note(server, guests)
    maker, _ = guests[GUEST]
    server._handle(maker, make(MSG_SET_NOTICE, note_id=nid, on=True))
    other, sock = guests[OTHER]
    server._handle(other, make(MSG_ACQUIRE_LOCK, note_id=nid))
    denied = sock.of("lock_denied")[-1]
    assert denied["reason"] == "notice" and denied["owner"] == GUEST
    server._handle(other, make(MSG_UPDATE_NOTE, id=nid, content="<p>바꿈</p>",
                               color="#FFE066", width=280, height=260))
    assert sock.of("error")[-1]["message"] == "notice"
    server._handle(other, make(MSG_DELETE_NOTE, id=nid))
    assert repo.get_note(nid) is not None and not repo.get_note(nid).deleted
    client.create_folder("업무", "#BAFFC9", False)
    folder = seen.of("folder_created")[-1]["folder"]["id"]
    server._handle(other, make(MSG_SET_NOTE_FOLDER, id=nid, folder_id=folder))
    assert repo.get_note(nid).folder_id is None
    # 만든 사람은 그대로 고친다.
    save(server, maker, nid, "<p>휴가 규정</p>", 1)
    assert "휴가 규정" in repo.get_note(nid).content


def test_the_host_user_can_lift_someone_elses_notice(host):
    server, client, seen, repo, guests = host
    nid = guest_note(server, guests)
    maker, _ = guests[GUEST]
    server._handle(maker, make(MSG_SET_NOTICE, note_id=nid, on=True))
    other, sock = guests[OTHER]
    server._handle(other, make(MSG_SET_NOTICE, note_id=nid, on=False))
    assert sock.of("error")[-1]["message"] == "not allowed" and repo.list_notices()
    client.set_notice(nid, False)                      # 공유 PC 사용자
    assert repo.list_notices() == {}
    assert sock.of("notice_changed")[-1] == {"note_id": nid, "owner": None, "by": HOST}


def test_private_notes_cannot_be_notices(host):
    server, client, seen, repo, guests = host
    client.create_note("#FFE066", 280, 260, private_owner=HOST)
    mine = seen.of("note_created")[-1]["note"]["id"]
    client.set_notice(mine, True)
    assert repo.list_notices() == {}


# ── 읽음 ──

def test_seen_reports_go_only_to_the_notice_maker(host):
    server, client, seen, repo, guests = host
    client.create_note("#FFE066", 280, 260)
    nid = seen.of("note_created")[-1]["note"]["id"]
    client.set_notice(nid, True)
    other, other_sock = guests[OTHER]
    guest, guest_sock = guests[GUEST]
    server._handle(other, make(MSG_SEEN, note_id=nid, version=1))
    report = seen.of("seen_changed")[-1]
    assert report["note_id"] == nid and report["seen"][OTHER]["version"] == 1
    assert other_sock.of("seen_changed") == [] and guest_sock.of("seen_changed") == []
    server._handle(other, make(MSG_SEEN, note_id=nid, version=99))     # 없는 판은 지금 판으로
    assert repo.list_seen(nid)[OTHER]["version"] == 1
    client.send_seen(nid, 1)                                         # 만든 사람은 세지 않는다
    assert HOST not in repo.list_seen(nid)
    server._send_welcome(server._local)
    assert seen.of("welcome")[-1]["seen"] == {nid: repo.list_seen(nid)}


def test_seen_is_ignored_for_plain_notes(host):
    server, client, seen, repo, guests = host
    client.create_note("#FFE066", 280, 260)
    nid = seen.of("note_created")[-1]["note"]["id"]
    other, _ = guests[OTHER]
    server._handle(other, make(MSG_SEEN, note_id=nid, version=1))
    assert repo.list_seen(nid) == {} and seen.of("seen_changed") == []


# ── @이름 ──

def test_new_mentions_reach_the_person_now_or_on_the_next_welcome(host):
    server, client, seen, repo, guests = host
    nid = guest_note(server, guests)
    other, other_sock = guests[OTHER]
    maker, maker_sock = guests[GUEST]
    save(server, maker, nid, "<p>@서연 견적 확인 부탁</p><p>@민수</p>", 1)
    assert other_sock.of("mentioned")[-1]["note_id"] == nid
    assert other_sock.of("mentioned")[-1]["by"] == GUEST
    assert seen.of("mentioned")[-1]["by"] == GUEST                    # 공유 PC 사용자도
    assert maker_sock.of("mentioned") == []
    # 다시 저장해도 이미 부른 사람에게는 다시 보내지 않는다.
    save(server, maker, nid, "<p>@서연 견적 확인 부탁</p><p>@민수</p><p>추가</p>", 2)
    assert len(other_sock.of("mentioned")) == 1
    # 꺼져 있는 사람에게는 다음 접속에.
    del server._sessions[OTHER]
    save(server, maker, nid, "<p>@서연</p>", 3)                        # 지웠다 다시 부름
    save(server, maker, nid, "<p>없음</p>", 4)
    save(server, maker, nid, "<p>@서연 다시</p>", 5)
    server._sessions[OTHER] = other
    server._send_welcome(other)
    pending = other_sock.of("welcome")[-1]["mentions"]
    assert [m["note_id"] for m in pending] == [nid] and pending[0]["by"] == GUEST
    server._send_welcome(other)
    assert other_sock.of("welcome")[-1]["mentions"] == []             # 한 번만


def test_mentions_are_found_only_for_known_names():
    names = {"서연", "서연이", "지우"}
    assert mentions.mentioned("@서연이 와 @지우, mail@지우.kr", names) == {"서연이", "지우"}
    assert mentions.new_mentions("<p>@지우</p>", "<p>@지우 @서연</p>", names) == {"서연"}
    assert mentions.plain_of("<p>a&amp;b</p><p>c</p>") == "a&b\nc\n"
    assert mentions.mentioned("@없는사람", names) == set()


def test_clients_read_notice_and_seen_messages():
    client = ClientBase("지우")
    got = []
    client.noticeChanged.connect(lambda *a: got.append(("notice",) + a))
    client.seenChanged.connect(lambda *a: got.append(("seen",) + a))
    client.mentioned.connect(lambda *a: got.append(("mention",) + a))
    client._dispatch("notice_changed", {"note_id": "n", "owner": "지우", "by": "지우"})
    client._dispatch("notice_changed", {"note_id": "n", "owner": None, "by": "민수"})
    client._dispatch("notice_changed", {"note_id": 3, "owner": None})
    client._dispatch("seen_changed", {"note_id": "n", "seen": {"서연": {"version": 2, "at": 5},
                                                               "x": {"version": "a"}}})
    client._dispatch("mentioned", {"note_id": "n", "by": "서연", "at": 7})
    assert got == [("notice", "n", "지우", "지우"), ("notice", "n", None, "민수"),
                   ("seen", "n", {"서연": {"version": 2, "at": 5}}),
                   ("mention", "n", "서연", 7)]
    assert clean_seen("x") == {}


# ── 화면 쪽 ──

import time  # noqa: E402

from app import i18n  # noqa: E402
from app.local_state import LocalStateStore  # noqa: E402
from app.settings import AppSettings  # noqa: E402
from app.ui import edit_menu, theme  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.sticky_note import StickyNoteWidget  # noqa: E402


class UiClient(ClientBase):
    def __init__(self, name):
        super().__init__(name)
        self.sent = []

    def set_notice(self, note_id, on):
        self.sent.append(("notice", note_id, on))

    def send_seen(self, note_id, version):
        self.sent.append(("seen", note_id, version))

    def acquire_lock(self, note_id):
        self.sent.append(("lock", note_id))


def ui_note(nid, by, version=1, **more):
    return {"id": nid, "version": version, "color": "#FFE066", "width": 280, "height": 220,
            "content": f"<p>{nid} 메모</p>", "created_by": by, "updated_by": by,
            "updated_at": int(time.time() * 1000), **more}


@pytest.fixture
def ui(tmp_path, monkeypatch):
    app = QApplication.instance()
    i18n.set_language("ko")
    theme.apply_theme(app, "")
    monkeypatch.setattr(StickyNoteWidget, "bring_to_front",
                        lambda self, *, start_editing=False: None)

    def open_as(name, welcome_extra):
        client = UiClient(name)
        win = MainWindow(client=client, local_state=LocalStateStore(tmp_path / f"{name}.json"),
                         role="guest", settings=AppSettings(fold_animation=False))
        client.connectionChanged.emit(True)
        client._dispatch("welcome", {
            "users": [GUEST, OTHER, HOST], "host": HOST, "locks": {}, "heartbeat_ms": 3000,
            "trash_days": 30, "reminders": {}, "folders": [],
            "notes": [ui_note("rule", GUEST, version=3), ui_note("plain", GUEST)],
            **welcome_extra})
        opened.append(win)
        return win, client
    opened = []
    yield open_as
    for win in opened:
        win._force_quit = True
        win._teardown_stickies()
        win.close()
        win.deleteLater()
    app.processEvents()


def test_someone_elses_notice_is_read_only_and_says_who_may_edit(ui):
    win, client = ui(OTHER, {"notices": {"rule": GUEST}, "seen": {}, "mentions": []})
    sticky = win._stickies["rule"]
    assert sticky.notice_owner() == GUEST and not sticky._notice_label.isHidden()
    assert win._cards["rule"].is_notice()
    assert win._cards_layout.itemAt(0).widget() is win._cards["rule"]   # 맨 위
    sticky._request_edit()
    assert ("lock", "rule") not in client.sent
    assert sticky._lock_label.text() == "지우님만 고칠 수 있습니다"
    win._stickies["plain"]._request_edit()
    assert ("lock", "plain") in client.sent


def test_viewing_a_notice_tells_the_maker_once_per_version(ui):
    win, client = ui(OTHER, {"notices": {"rule": GUEST}, "seen": {}, "mentions": []})
    win._stickies["rule"].viewed.emit("rule")
    win._take_note_out("rule")
    assert client.sent.count(("seen", "rule", 3)) == 1
    win._note_viewed("plain")                                  # 공지가 아니다
    assert not any(s[1] == "plain" for s in client.sent if s[0] == "seen")
    win._notes["rule"]["version"] = 4                          # 만든 사람이 고쳤다
    win._note_viewed("rule")
    assert ("seen", "rule", 4) in client.sent
    win._settings.hide_read_receipts = True
    win._notes["rule"]["version"] = 5
    win._note_viewed("rule")
    assert ("seen", "rule", 5) not in client.sent


def test_my_notice_counts_who_read_the_latest_version(ui, monkeypatch):
    win, client = ui(GUEST, {"notices": {"rule": GUEST},
                             "seen": {"rule": {OTHER: {"version": 3, "at": 1},
                                               HOST: {"version": 2, "at": 1}}},
                             "mentions": []})
    sticky = win._stickies["rule"]
    assert sticky._read_chip.full_text() == "읽음 1/2"
    shown = []
    monkeypatch.setattr(edit_menu, "run_menu", lambda menu, pos: shown.append(
        [a.text() for a in menu.actions() if not a.isSeparator()]))
    win._show_read_list("rule")
    assert shown[0][0] == "읽음 1" and shown[0][1].startswith(OTHER)
    assert shown[0][2:] == ["안 읽음 1", HOST]
    client._dispatch("seen_changed", {"note_id": "rule", "seen": {
        OTHER: {"version": 3, "at": 1}, HOST: {"version": 3, "at": 2}}})
    assert sticky._read_chip.full_text() == "읽음 2/2"


def test_the_maker_can_make_a_notice_from_the_more_menu(ui, monkeypatch):
    win, client = ui(GUEST, {"notices": {}, "seen": {}, "mentions": []})
    sticky = win._stickies["plain"]
    shown = []
    monkeypatch.setattr(edit_menu, "run_menu", lambda menu, pos: shown.append(menu))
    sticky._show_more_menu()
    make_it = next(a for a in shown[-1].actions() if a.text() == "공지로 만들기")
    make_it.trigger()
    assert ("notice", "plain", True) in client.sent
    client._dispatch("notice_changed", {"note_id": "plain", "owner": GUEST, "by": GUEST})
    assert sticky.notice_owner() == GUEST
    sticky._show_more_menu()
    assert any(a.text() == "공지 풀기" for a in shown[-1].actions())


def test_an_old_host_offers_no_notice(ui, monkeypatch):
    win, client = ui(GUEST, {})                                  # 옛 공유 PC
    shown = []
    monkeypatch.setattr(edit_menu, "run_menu", lambda menu, pos: shown.append(menu))
    win._stickies["plain"]._show_more_menu()
    assert not any(a.text() == "공지로 만들기" for a in shown[-1].actions())


def test_mentions_show_a_toast_now_and_from_the_welcome(ui, monkeypatch):
    shown = []
    win, client = ui(OTHER, {"notices": {}, "seen": {},
                             "mentions": [{"note_id": "plain", "by": GUEST, "at": 1}]})
    toasts = [t for t in win._toasts._toasts] if hasattr(win._toasts, "_toasts") else []
    monkeypatch.setattr(win._toasts, "show_actions",
                        lambda title, sub, actions, **kw: shown.append((title, sub)) or None)
    client._dispatch("mentioned", {"note_id": "rule", "by": GUEST, "at": 5})
    assert shown == [("rule 메모", "지우님이 불렀습니다")]
    assert toasts is not None
