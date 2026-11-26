"""공유 알림(모두, 고른 사람): 공유 PC 가 표에 적고 그 메모를 보는 사람에게만 알린다."""
from __future__ import annotations

import os
import sqlite3

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app import reminders as rm  # noqa: E402
from app.database import SqliteRepository  # noqa: E402
from app.network.client_base import ClientBase  # noqa: E402
from app.network.host_client import HostLocalClient  # noqa: E402
from app.network.host_server import HostServer, _Session  # noqa: E402
from app.protocol import MSG_SET_REMINDER, FrameParser, make  # noqa: E402
from app.sync.history_tracker import HistoryTracker  # noqa: E402
from app.sync.lock_manager import LockManager  # noqa: E402

HOST, GUEST, OTHER = "민수", "지우", "서연"
WEEKLY = rm.Rule(rm.WEEKLY, "2026-11-25", "09:00", [0, 3]).to_dict()


@pytest.fixture(scope="module", autouse=True)
def _qt():
    yield QApplication.instance() or QApplication([])


class FakeSocket:
    """참여한 PC 로 가는 글을 받아 둔다."""

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
        sock = FakeSocket()
        session = _Session(socket=sock, parser=FrameParser(), nickname=name,
                           hello_received=True)
        server._sessions[name] = session
        guests[name] = (session, sock)
    yield server, client, seen, repo, guests
    repo.close()


def make_note(client, seen, **kwargs) -> str:
    client.create_note("#FFE066", 280, 260, **kwargs)
    return seen.of("note_created")[-1]["note"]["id"]


def test_the_host_keeps_a_reminder_and_tells_everyone_who_sees_the_note(host):
    server, client, seen, repo, guests = host
    nid = make_note(client, seen)
    client.set_reminder(nid, {"rule": WEEKLY, "targets": [GUEST]})

    got = seen.of("reminder_changed")[-1]
    entry = got["reminder"]
    assert got["note_id"] == nid and got["by"] == HOST and got["done"] is False
    assert entry["rule"] == WEEKLY and entry["targets"] == [GUEST]
    assert entry["set_by"] == HOST and entry["set_at"] > 0
    for _session, sock in guests.values():
        assert sock.of("reminder_changed")[-1]["reminder"] == entry
    assert repo.list_reminders() == {nid: entry}

    # 새로 붙은 PC 도 welcome 으로 받는다.
    session, sock = guests[GUEST]
    server._send_welcome(session)
    assert sock.of("welcome")[-1]["reminders"] == {nid: entry}

    # 누가 한 번 알림을 끝내면 모두에게서 지운다.
    server._handle(session, make(MSG_SET_REMINDER, note_id=nid, reminder=None, done=True))
    cleared = guests[OTHER][1].of("reminder_changed")[-1]
    assert cleared == {"note_id": nid, "reminder": None, "by": GUEST, "done": True}
    assert repo.list_reminders() == {}


def test_private_notes_keep_their_reminders_to_the_owner(host):
    server, client, seen, _repo, guests = host
    mine = make_note(client, seen, private_owner=HOST)
    client.set_reminder(mine, {"rule": WEEKLY, "targets": []})
    assert seen.of("reminder_changed")[-1]["note_id"] == mine
    for _session, sock in guests.values():
        assert sock.of("reminder_changed") == []
    session, sock = guests[GUEST]
    server._send_welcome(session)
    assert sock.of("welcome")[-1]["reminders"] == {}
    # 볼 수 없는 메모에는 걸지도 지우지도 못한다.
    server._handle(session, make(MSG_SET_REMINDER, note_id=mine, reminder=None))
    assert sock.of("error")[-1]["message"] == "not allowed"


def test_bad_reminders_are_refused(host):
    server, client, seen, repo, guests = host
    nid = make_note(client, seen)
    session, sock = guests[GUEST]
    for bad in ({"rule": {"kind": "hourly", "time": "09:00"}},
                {"rule": WEEKLY, "targets": "everyone"},
                {"rule": WEEKLY, "targets": ["x" * 200]},
                "weekly"):
        server._handle(session, make(MSG_SET_REMINDER, note_id=nid, reminder=bad))
    assert [m["message"] for m in sock.of("error")] == ["bad reminder"] * 4
    server._handle(session, make(MSG_SET_REMINDER, note_id="nope", reminder=None))
    assert sock.of("error")[-1]["message"] == "not allowed"
    assert repo.list_reminders() == {}
    # 건 사람과 때는 공유 PC 가 적는다(보낸 값을 믿지 않는다).
    server._handle(session, make(MSG_SET_REMINDER, note_id=nid, reminder={
        "rule": WEEKLY, "targets": [OTHER, OTHER], "set_by": HOST, "set_at": 1}))
    entry = repo.list_reminders()[nid]
    assert entry["set_by"] == GUEST and entry["set_at"] > 1 and entry["targets"] == [OTHER]


def test_purging_a_note_drops_its_reminder_but_the_trash_keeps_it(host):
    _server, client, seen, repo, _guests = host
    nid = make_note(client, seen)
    client.set_reminder(nid, {"rule": WEEKLY, "targets": []})
    client.delete_note(nid)
    assert nid in repo.list_reminders()           # 되살리면 다시 쓴다
    client.purge_note(nid)
    assert repo.list_reminders() == {}


def test_older_versions_still_read_the_file(tmp_path):
    """표가 하나 더 있을 뿐이다. 옛 버전이 쓰던 파일도 그대로 열린다."""
    path = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE notes (id TEXT PRIMARY KEY, content TEXT NOT NULL DEFAULT '',"
                 " color TEXT NOT NULL, width INTEGER, height INTEGER, created_by TEXT,"
                 " created_at INTEGER, updated_by TEXT, updated_at INTEGER,"
                 " version INTEGER, deleted INTEGER NOT NULL DEFAULT 0)")
    conn.commit()
    conn.close()
    repo = SqliteRepository(path)
    repo.set_reminder("a", {"rule": WEEKLY, "targets": [], "set_by": HOST, "set_at": 5})
    repo._conn.execute("INSERT INTO note_reminders VALUES ('b', 'not json')")
    repo._conn.commit()
    assert list(repo.list_reminders()) == ["a"]
    repo.close()


def test_clients_take_only_well_formed_changes():
    client = ClientBase(GUEST)
    got = []
    client.reminderChanged.connect(lambda *a: got.append(a))
    entry = {"rule": WEEKLY, "targets": [OTHER], "set_by": HOST, "set_at": 7}
    client._dispatch("reminder_changed", {"note_id": "a", "reminder": entry, "by": HOST})
    client._dispatch("reminder_changed", {"note_id": "a", "reminder": {"rule": 1}})
    client._dispatch("reminder_changed", {"note_id": "", "reminder": None})
    client._dispatch("reminder_changed", {"note_id": "a", "reminder": None, "by": OTHER,
                                          "done": True})
    assert got == [("a", entry, HOST, False), ("a", None, OTHER, True)]


def test_targets_and_entries_are_checked():
    assert rm.clean_targets([]) == []
    assert rm.clean_targets([GUEST, GUEST, OTHER]) == [GUEST, OTHER]
    assert rm.clean_targets([" "]) is None and rm.clean_targets("all") is None
    assert rm.clean_targets([GUEST] * 101) is None
    assert rm.is_target({"targets": []}, GUEST) and rm.is_target({"targets": [GUEST]}, GUEST)
    assert not rm.is_target({"targets": [OTHER]}, GUEST)
    assert rm.shared_from_dict({"rule": WEEKLY, "set_at": True}) is None
