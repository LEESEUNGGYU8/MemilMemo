"""호스트: 메모 묶음 식별값, 새 메모 알림 신호, 백업을 되살린 뒤 다시 보내기."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.database import SqliteRepository, snapshot_database  # noqa: E402
from app.network.client_base import ClientBase  # noqa: E402
from app.network.host_client import HostLocalClient  # noqa: E402
from app.network.host_server import HostServer, _Session  # noqa: E402
from app.protocol import FrameParser  # noqa: E402
from app.sync.history_tracker import HistoryTracker  # noqa: E402
from app.sync.lock_manager import LockManager  # noqa: E402

HOST = "민수"


@pytest.fixture(scope="module", autouse=True)
def _qt():
    yield QApplication.instance() or QApplication([])


class Recorder:
    """호스트가 한 참가자에게 보낸 것을 적어 둔다."""

    def __init__(self):
        self.messages: list[tuple[str, dict]] = []

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
    yield server, client, seen, repo
    repo.close()


def test_welcome_carries_the_dataset_id(host):
    server, _client, seen, repo = host
    welcome = seen.of("welcome")[-1]
    assert welcome["host_id"] == repo.host_id() and welcome["host_id"]


def test_new_note_is_announced_but_a_restored_one_is_not(host):
    _server, client, seen, _repo = host
    client.create_note("#FFE066", 280, 260)
    client.create_note("#FFE066", 280, 260, quiet=True)

    appended = seen.of("history_appended")
    assert [e["entry"]["action"] for e in appended] == ["create", "create"]
    assert "quiet" not in appended[0] and appended[1]["quiet"] is True
    # 이력 항목 자체에는 손대지 않는다(옛 버전은 모르는 값이 있으면 읽지 못한다).
    assert "quiet" not in appended[1]["entry"]


def test_client_announces_only_real_creations():
    client = ClientBase("지우")
    announced, appended = [], []
    client.noteAnnounced.connect(announced.append)
    client.historyAppended.connect(appended.append)
    create = {"action": "create", "user": "민수", "note_id": "a"}

    client._dispatch("history_appended", {"entry": create})
    client._dispatch("history_appended", {"entry": create, "quiet": True})
    client._dispatch("history_appended",
                     {"entry": {"action": "update", "user": "민수", "note_id": "a"}})
    client._dispatch("history_appended",
                     {"entry": {"action": "delete", "user": "민수", "note_id": "a"}})

    assert announced == [create]
    assert len(appended) == 4          # 이력 창에는 모두 간다


def test_restore_resends_everything_and_drops_locks(host, tmp_path):
    server, client, seen, repo = host
    client.create_note("#FFE066", 280, 260)
    note_id = seen.of("note_created")[-1]["note"]["id"]
    saved = tmp_path / "saved.sqlite3"
    snapshot_database(repo.path, saved)
    old_id = repo.host_id()
    client.create_note("#BAE1FF", 280, 260)          # 백업 뒤에 만든 메모
    client.acquire_lock(note_id)
    assert server.locks.snapshot() == {note_id: HOST}
    # 원격 참가자 한 명(소켓 대신 보낸 것을 적어 두는 가짜).
    guest = _Session(socket=None, parser=FrameParser(), nickname="지우",
                     hello_received=True)
    guest_seen = Recorder()
    server._sessions["guest"] = guest
    send = server._send_to
    server._send_to = lambda s, m: (guest_seen(m.type, m.data) if s is guest
                                    else send(s, m))
    seen.messages.clear()

    repo.restore_from(saved)
    server.resync_all()

    for who in (seen, guest_seen):
        welcome = who.of("welcome")[-1]
        assert [n["id"] for n in welcome["notes"]] == [note_id]
        assert welcome["locks"] == {}
        # 되살린 뒤에는 다른 메모 묶음으로 알린다(사본을 따로 남기게).
        assert welcome["host_id"] != old_id
    assert server.locks.snapshot() == {}
    # 잡혀 있던 편집 세션이 남아 엉뚱한 이력을 만들지 않는다.
    client.release_lock(note_id)
    assert seen.of("history_appended") == []


# ── 나만 보던 메모를 공개로 바꾸기 ──────────────────────────────────────

def _with_guest(server):
    """원격 참가자 한 명을 붙이고, 그 사람에게 간 것을 적는 기록기를 돌려준다."""
    guest = _Session(socket=None, parser=FrameParser(), nickname="지우",
                     hello_received=True)
    seen = Recorder()
    server._sessions["guest"] = guest
    send = server._send_to
    server._send_to = lambda s, m: (seen(m.type, m.data) if s is guest
                                    else send(s, m))
    return seen


def test_making_a_note_public_tells_others_who_did_it(host):
    server, client, seen, _repo = host
    guest_seen = _with_guest(server)
    client.create_note("#FFE066", 280, 260, private_owner=HOST)
    note_id = seen.of("note_created")[-1]["note"]["id"]
    assert guest_seen.of("note_created") == []         # 남의 비공개 메모는 오지 않는다
    seen.messages.clear()

    client.set_note_privacy(note_id, False)

    created = guest_seen.of("note_created")
    assert len(created) == 1 and created[0]["note"]["id"] == note_id
    assert created[0]["shared_by"] == HOST
    # 바꾼 본인에게는 고쳐진 것으로만 간다(새 메모로 다시 뜨지 않는다).
    assert seen.of("note_created") == [] and len(seen.of("note_updated")) == 1

    # 다시 나만 보기로 바꾸면 남에게서는 사라지고, 알릴 것은 없다.
    guest_seen.messages.clear()
    client.set_note_privacy(note_id, True)
    assert guest_seen.of("note_created") == []
    assert guest_seen.of("note_deleted") == [{"id": note_id}]


def test_new_public_note_is_not_marked_as_shared(host):
    server, client, _seen, _repo = host
    guest_seen = _with_guest(server)
    client.create_note("#FFE066", 280, 260)
    assert "shared_by" not in guest_seen.of("note_created")[-1]


def test_making_a_folder_public_shares_its_notes(host):
    server, client, seen, repo = host
    guest_seen = _with_guest(server)
    client.create_folder("내 폴더", "#BAE1FF", private=True)
    folder_id = seen.of("folder_created")[-1]["folder"]["id"]
    client.create_note("#FFE066", 280, 260, folder_id=folder_id)
    client.create_note("#FFE066", 280, 260, folder_id=folder_id)
    assert guest_seen.of("note_created") == []

    client.update_folder(folder_id, private=False)

    shared = guest_seen.of("note_created")
    assert len(shared) == 2 and {m["shared_by"] for m in shared} == {HOST}
    assert len(repo.list_notes_in_folder(folder_id)) == 2


def test_moving_a_note_out_of_a_private_folder_shares_it(host):
    server, client, seen, _repo = host
    guest_seen = _with_guest(server)
    client.create_folder("내 폴더", "#BAE1FF", private=True)
    folder_id = seen.of("folder_created")[-1]["folder"]["id"]
    client.create_note("#FFE066", 280, 260, folder_id=folder_id)
    note_id = seen.of("note_created")[-1]["note"]["id"]

    client.set_note_folder(note_id, None)

    assert [m["shared_by"] for m in guest_seen.of("note_created")] == [HOST]


def test_client_reports_shared_notes():
    client = ClientBase("지우")
    shared, created = [], []
    client.noteShared.connect(shared.append)
    client.noteCreated.connect(created.append)

    client._dispatch("note_created", {"note": {"id": "a"}, "shared_by": "민수"})
    client._dispatch("note_created", {"note": {"id": "b"}})      # 옛 버전 호스트, 새 메모

    assert shared == [{"user": "민수", "note_id": "a"}]
    assert [n["id"] for n in created] == ["a", "b"]


def test_deleting_a_private_folder_keeps_its_notes_private(host):
    server, client, seen, repo = host
    guest_seen = _with_guest(server)
    client.create_folder("내 폴더", "#BAE1FF", private=True)
    folder_id = seen.of("folder_created")[-1]["folder"]["id"]
    client.create_note("#FFE066", 280, 260, folder_id=folder_id)
    client.create_note("#FFE066", 280, 260, folder_id=folder_id, private_owner=HOST)

    client.delete_folder(folder_id)

    # 폴더가 없어져도 그 안의 메모는 다른 사람에게 가지 않는다.
    assert guest_seen.of("note_created") == []
    notes = repo.list_notes()
    assert len(notes) == 2 and {n.private_owner for n in notes} == {HOST}
    assert {n.folder_id for n in notes} == {None}


def test_deleting_a_public_folder_leaves_its_notes_public(host):
    server, client, seen, repo = host
    _with_guest(server)
    client.create_folder("회의", "#BAE1FF")
    folder_id = seen.of("folder_created")[-1]["folder"]["id"]
    client.create_note("#FFE066", 280, 260, folder_id=folder_id)
    client.delete_folder(folder_id)
    assert [n.private_owner for n in repo.list_notes()] == [None]
