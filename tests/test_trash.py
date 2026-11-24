"""휴지통: 지운 메모를 두었다가 되살리고, 보관 기간이 지나면 메모 파일에서 지운다."""
from __future__ import annotations

import os
import sqlite3
from dataclasses import fields

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app import config  # noqa: E402
from app.database import SqliteRepository  # noqa: E402
from app.models import Folder, HistoryEntry, Note, now_ms  # noqa: E402
from app.network.client_base import ClientBase  # noqa: E402
from app.network.host_client import HostLocalClient  # noqa: E402
from app.network.host_server import HostServer, _Session  # noqa: E402
from app.protocol import (  # noqa: E402
    MSG_DELETE_NOTE, MSG_EMPTY_TRASH, MSG_GET_TRASH, MSG_PURGE_NOTE,
    MSG_RESTORE_NOTE, FrameParser, make,
)
from app.sync.history_tracker import HistoryTracker  # noqa: E402
from app.sync.lock_manager import LockManager  # noqa: E402

HOST, GUEST = "민수", "지우"
DAY = 24 * 60 * 60 * 1000


@pytest.fixture(scope="module", autouse=True)
def _qt():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def repo(tmp_path):
    r = SqliteRepository(tmp_path / "notes.sqlite3")
    yield r
    r.close()


def add_note(repo: SqliteRepository, text: str = "글", *, folder=None,
             private=None) -> Note:
    note = Note.new(HOST, folder_id=folder, private_owner=private)
    note.content = text
    repo.insert_note(note)
    repo.insert_history(HistoryEntry(
        note_id=note.id, user=HOST, action="create", content_before=None,
        content_after=text, color_before=None, color_after=note.color,
        session_start=1, session_end=1))
    return note


def age_trash(repo: SqliteRepository, note_id: str, days: float) -> None:
    repo._conn.execute("UPDATE trash SET deleted_at = ? WHERE note_id = ?",
                       (now_ms() - int(days * DAY), note_id))
    repo._conn.commit()


# ── 메모 파일 ───────────────────────────────────────────────────────────

def test_trashed_note_leaves_the_list_and_shows_in_the_trash(repo):
    note = add_note(repo, "회의")
    assert repo.trash_note(note.id, GUEST) is not None

    assert repo.list_notes() == []
    (found, at, by), = repo.list_trash()
    assert found.id == note.id and found.content == "회의"
    assert by == GUEST and abs(at - now_ms()) < 5000
    assert repo.in_trash(note.id)


def test_restored_note_is_the_same_note(repo):
    note = add_note(repo, "회의")
    repo.trash_note(note.id, HOST)
    back = repo.restore_note(note.id, GUEST)

    assert back is not None and back.id == note.id and not back.deleted
    assert back.content == "회의" and back.version > note.version
    assert repo.list_trash() == [] and not repo.in_trash(note.id)
    assert [n.id for n in repo.list_notes()] == [note.id]
    # 이력은 그대로 이어진다.
    assert [e.action for e in repo.list_history(note.id)] == ["create"]


def test_restoring_into_a_folder_that_is_gone_lands_outside(repo):
    folder = Folder.new(HOST, "업무")
    repo.insert_folder(folder)
    note = add_note(repo, folder=folder.id)
    repo.trash_note(note.id, HOST)
    repo.soft_delete_folder(folder.id)

    assert repo.restore_note(note.id, HOST).folder_id is None


def test_restore_and_purge_refuse_what_is_not_in_the_trash(repo):
    live = add_note(repo, "살아 있는 메모")
    old = add_note(repo, "전에 지운 메모")
    repo.soft_delete_note(old.id, HOST)        # 휴지통이 생기기 전의 지우기

    for note in (live, old):
        assert repo.restore_note(note.id, HOST) is None
        assert repo.purge_note(note.id) is False
    assert repo.get_note(live.id) is not None and repo.get_note(old.id) is not None
    assert repo.list_trash() == []


def test_purge_removes_the_note_and_its_history(repo):
    note = add_note(repo)
    keep = add_note(repo, "다른 메모")
    repo.trash_note(note.id, HOST)

    assert repo.purge_note(note.id) is True
    assert repo.get_note(note.id) is None
    assert repo.list_history(note.id) == []
    assert repo.get_note(keep.id) is not None
    assert len(repo.list_history(keep.id)) == 1


def test_only_notes_past_the_keep_time_are_swept(repo):
    fresh, stale = add_note(repo, "어제"), add_note(repo, "두 달 전")
    legacy, live = add_note(repo, "전에 지운 메모"), add_note(repo, "살아 있는 메모")
    repo.trash_note(fresh.id, HOST)
    repo.trash_note(stale.id, HOST)
    repo.soft_delete_note(legacy.id, HOST)
    age_trash(repo, fresh.id, 1)
    age_trash(repo, stale.id, 60)
    # 옛 버전이 쓰는 동안 남았을 수 있는 낡은 줄: 살아 있는 메모를 가리킨다.
    repo._conn.execute(
        "INSERT INTO trash (note_id, deleted_at, deleted_by) VALUES (?, 1, '')",
        (live.id,))
    repo._conn.commit()

    gone = repo.purge_trash(now_ms() - config.TRASH_KEEP_DAYS * DAY)

    assert gone == [stale.id]
    assert repo.get_note(stale.id) is None
    assert repo.get_note(live.id) is not None and not repo.get_note(live.id).deleted
    assert repo.get_note(legacy.id) is not None        # 전에 지운 것은 그대로 둔다
    assert [n.id for n, _at, _by in repo.list_trash()] == [fresh.id]


def test_older_versions_still_read_the_file(repo, tmp_path):
    """옛 버전은 notes 의 줄을 그대로 Note 에 넘긴다. 열이 늘면 읽다가 죽는다."""
    note = add_note(repo)
    repo.trash_note(note.id, HOST)
    conn = sqlite3.connect(str(tmp_path / "notes.sqlite3"))
    columns = {row[1] for row in conn.execute("PRAGMA table_info(notes)")}
    conn.close()
    assert columns == {f.name for f in fields(Note)}


# ── 공유 PC ─────────────────────────────────────────────────────────────

class Recorder:
    def __init__(self):
        self.messages: list[tuple[str, dict]] = []

    def __call__(self, kind: str, data: dict) -> None:
        self.messages.append((kind, data))

    def of(self, kind: str) -> list[dict]:
        return [d for k, d in self.messages if k == kind]


@pytest.fixture
def host(repo):
    server = HostServer(repo, LockManager(idle_timeout_ms=60_000),
                        HistoryTracker(repo), host_nickname=HOST)
    client = HostLocalClient(server, HOST)
    seen = Recorder()
    server.attach_local_host()
    server.set_local_subscriber(seen)
    client._send_hello()
    guest = _Session(socket=None, parser=FrameParser(), nickname=GUEST,
                     hello_received=True)
    server._sessions["guest"] = guest
    return server, client, seen, repo, guest


def make_note(client, seen, **kwargs) -> str:
    client.create_note("#FFE066", 280, 260, **kwargs)
    return seen.of("note_created")[-1]["note"]["id"]


def test_welcome_tells_how_long_the_trash_keeps(host):
    _server, _client, seen, _repo, _guest = host
    assert seen.of("welcome")[-1]["trash_days"] == config.TRASH_KEEP_DAYS


def test_deleting_moves_the_note_to_the_trash(host):
    _server, client, seen, repo, _guest = host
    nid = make_note(client, seen)
    client.delete_note(nid)

    assert seen.of("note_deleted")[-1]["id"] == nid
    assert seen.of("trash_changed")
    client.get_trash()
    listed = seen.of("trash_list")[-1]
    assert [n["id"] for n in listed["notes"]] == [nid]
    assert listed["notes"][0]["deleted_by"] == HOST
    assert listed["keep_days"] == config.TRASH_KEEP_DAYS
    assert repo.in_trash(nid)


def test_an_older_guest_deleting_also_goes_to_the_trash(host):
    """옛 버전은 delete_note 만 안다. 그것도 휴지통으로 간다."""
    server, client, seen, repo, guest = host
    nid = make_note(client, seen)
    server._handle(guest, make(MSG_DELETE_NOTE, id=nid))
    (note, _at, by), = repo.list_trash()
    assert note.id == nid and by == GUEST


def test_restoring_brings_the_same_note_back_to_everyone(host):
    _server, client, seen, repo, _guest = host
    nid = make_note(client, seen)
    client.delete_note(nid)
    seen.messages.clear()
    client.restore_note(nid)

    created = seen.of("note_created")
    assert created and all(c["note"]["id"] == nid for c in created)
    assert created[0]["restored_by"] == HOST and created[0]["note"]["deleted"] == 0
    appended = seen.of("history_appended")
    assert appended[0]["entry"]["action"] == "restore" and appended[0]["quiet"] is True
    assert seen.of("trash_changed")
    assert repo.list_trash() == [] and not repo.get_note(nid).deleted


def test_restore_is_not_announced_as_a_new_note():
    client = ClientBase(GUEST)
    announced, restored = [], []
    client.noteAnnounced.connect(announced.append)
    client.noteRestored.connect(restored.append)
    client._dispatch("note_created", {"note": {"id": "a"}, "restored_by": HOST})
    client._dispatch("history_appended", {
        "entry": {"action": "restore", "user": HOST, "note_id": "a"},
        "quiet": True})
    assert announced == [] and restored == [{"user": HOST, "note_id": "a"}]


def test_private_notes_stay_private_in_the_trash(host):
    server, client, seen, repo, guest = host
    mine = make_note(client, seen, private_owner=HOST)
    shared = make_note(client, seen)
    client.delete_note(mine)
    client.delete_note(shared)

    seen.messages.clear()
    server._handle(guest, make(MSG_GET_TRASH))
    assert [n["id"] for n in seen.of("trash_list")[-1]["notes"]] == [shared]

    # 남의 메모는 되살리지도 지우지도 못한다.
    server._handle(guest, make(MSG_RESTORE_NOTE, id=mine))
    server._handle(guest, make(MSG_PURGE_NOTE, id=mine))
    assert repo.in_trash(mine)
    assert [e["message"] for e in seen.of("error")] == ["not allowed"] * 2

    # 비우기도 보이는 것만 지운다.
    server._handle(guest, make(MSG_EMPTY_TRASH))
    assert repo.get_note(shared) is None and repo.in_trash(mine)


def test_purging_one_note_is_final(host):
    _server, client, seen, repo, _guest = host
    nid = make_note(client, seen)
    client.delete_note(nid)
    client.purge_note(nid)
    assert repo.get_note(nid) is None and repo.list_history(nid) == []
    client.restore_note(nid)                       # 이미 없다. 아무 일도 없다
    assert repo.get_note(nid) is None


def test_live_notes_cannot_be_purged_through_the_trash_messages(host):
    _server, client, seen, repo, _guest = host
    nid = make_note(client, seen)
    client.purge_note(nid)
    client.empty_trash()
    assert repo.get_note(nid) is not None and not repo.get_note(nid).deleted


def test_the_host_sweeps_old_trash(host):
    server, client, seen, repo, _guest = host
    old, new = make_note(client, seen), make_note(client, seen)
    client.delete_note(old)
    client.delete_note(new)
    age_trash(repo, old, config.TRASH_KEEP_DAYS + 1)
    age_trash(repo, new, config.TRASH_KEEP_DAYS - 1)
    seen.messages.clear()

    server.purge_old_trash()

    assert repo.get_note(old) is None and repo.in_trash(new)
    assert seen.of("trash_changed")
    seen.messages.clear()
    server.purge_old_trash()                       # 지울 것이 없으면 조용하다
    assert seen.messages == []
