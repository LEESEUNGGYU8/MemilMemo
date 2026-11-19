"""참여한 PC 의 메모 사본: 받은 대로 적기, 멀쩡한 사본을 빈 내용으로 덮지 않기."""
from __future__ import annotations

import os
import sqlite3

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app import replica as replica_mod  # noqa: E402
from app.network.client_base import ClientBase  # noqa: E402
from app.replica import ReplicaStore, ReplicaSync  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _qt():
    yield QApplication.instance() or QApplication([])


def note(nid: str, text: str = "", version: int = 1, **more) -> dict:
    return {"id": nid, "content": text or nid, "version": version,
            "updated_at": 1000 + version, "color": "#FFE066", **more}


def ids(store: ReplicaStore) -> set[str]:
    return {n["id"] for n in store.load()[0]}


def stored(path) -> set[str]:
    conn = sqlite3.connect(str(path))
    try:
        return {r[0] for r in conn.execute("SELECT id FROM notes")}
    finally:
        conn.close()


@pytest.fixture
def store(tmp_path):
    s = ReplicaStore(tmp_path / "replica.sqlite3")
    yield s
    s.close()


def test_snapshot_is_stored_as_received(store):
    folder = {"id": "f1", "name": "회의", "color": "#BAE1FF"}
    archived = store.apply_snapshot(
        [note("a", "공개 메모"), note("b", "내 메모", private_owner="지우", folder_id="f1")],
        [folder], host_id="H1", host="민수", address="10.0.0.5:5555", nickname="지우")

    notes, folders = store.load()
    assert not archived
    assert {n["id"]: n["content"] for n in notes} == {"a": "공개 메모", "b": "내 메모"}
    # 앞으로 생길 값도 버리지 않도록 받은 것을 통째로 적는다.
    assert next(n for n in notes if n["id"] == "b")["private_owner"] == "지우"
    assert folders == [folder]
    meta = store.meta()
    assert (meta["host_id"], meta["host"], meta["address"], meta["nickname"]) == (
        "H1", "민수", "10.0.0.5:5555", "지우")
    assert int(meta["synced_at"]) > 0


def test_changes_are_applied_one_by_one(store):
    store.apply_snapshot([note("a")], [], host_id="H1")
    store.upsert_note(note("b", "새 메모"))
    store.upsert_note(note("a", "고친 글", version=2))
    store.delete_note("b")
    store.upsert_folder({"id": "f1", "name": "폴더"})
    store.upsert_folder({"id": "f1", "name": "바꾼 이름"})
    store.upsert_folder({"id": "f2", "name": "지울 폴더"})
    store.delete_folder("f2")

    notes, folders = store.load()
    assert [(n["id"], n["content"]) for n in notes] == [("a", "고친 글")]
    assert folders == [{"id": "f1", "name": "바꾼 이름"}]


def test_reconnect_follows_the_same_host(store):
    store.apply_snapshot([note("a"), note("b"), note("c")], [{"id": "f", "name": "x"}],
                         host_id="H1")
    # 끊긴 사이 b 가 지워지고 c 가 고쳐졌다.
    archived = store.apply_snapshot([note("a"), note("c", "고친 글", version=5)], [],
                                    host_id="H1")
    notes, folders = store.load()
    assert not archived and store.archives() == []
    assert {n["id"]: n["content"] for n in notes} == {"a": "a", "c": "고친 글"}
    assert folders == []


def test_same_host_may_become_empty(store):
    """같은 메모 묶음에서 모두 지운 것이라면 그대로 따른다."""
    store.apply_snapshot([note("a")], [], host_id="H1")
    assert not store.apply_snapshot([], [], host_id="H1")
    assert store.is_empty() and store.archives() == []


def test_new_empty_host_does_not_wipe_the_copy(store, tmp_path):
    """호스트의 메모 파일이 새로 만들어졌다(디스크 고장 뒤 다시 설치).

    같은 주소로 다시 붙으면 메모가 하나도 없는 첫 상태가 온다. 가지고 있던
    사본은 마지막 남은 것일 수 있으므로 지우지 않고 옮겨 둔다."""
    store.apply_snapshot([note("a", "하나뿐인 사본"), note("b")], [], host_id="H1")

    archived = store.apply_snapshot([], [], host_id="H2")

    assert archived and store.is_empty()
    kept = store.archives()
    assert len(kept) == 1 and kept[0].parent == tmp_path
    assert stored(kept[0]) == {"a", "b"}
    assert store.meta()["host_id"] == "H2"


def test_another_hosts_notes_do_not_replace_the_copy(store):
    store.apply_snapshot([note("a"), note("b")], [], host_id="H1")
    assert store.apply_snapshot([note("x"), note("y")], [], host_id="H2")
    assert ids(store) == {"x", "y"}
    assert stored(store.archives()[0]) == {"a", "b"}


def test_old_host_without_an_id_is_compared_by_notes(store):
    """식별값을 보내지 않는 옛 버전 호스트. 겹치는 메모가 있으면 같은 묶음으로 본다."""
    store.apply_snapshot([note("a"), note("b")], [])
    assert not store.apply_snapshot([note("b"), note("c")], [])
    assert ids(store) == {"b", "c"}
    # 겹치는 메모가 하나도 없으면 다른 묶음이다(빈 첫 상태도 여기에 든다).
    assert store.apply_snapshot([], [])
    assert stored(store.archives()[0]) == {"b", "c"}


def test_host_that_starts_sending_an_id_is_still_the_same(store):
    store.apply_snapshot([note("a")], [])                 # 옛 버전 호스트
    assert not store.apply_snapshot([note("a"), note("b")], [], host_id="H1")
    assert store.meta()["host_id"] == "H1" and store.archives() == []


def test_only_changed_notes_are_rewritten(store, monkeypatch):
    store.apply_snapshot([note("a"), note("b"), note("c")], [], host_id="H1")
    written = []
    original = ReplicaStore._put_note
    monkeypatch.setattr(ReplicaStore, "_put_note",
                        lambda self, n: (written.append(n["id"]), original(self, n))[1])

    store.apply_snapshot([note("a"), note("b", "고침", version=2), note("c"), note("d")],
                         [], host_id="H1")

    assert sorted(written) == ["b", "d"]


def test_archives_are_capped(store, monkeypatch):
    monkeypatch.setattr(replica_mod, "KEEP_ARCHIVES", 2)
    for i in range(5):
        store.apply_snapshot([note(f"n{i}")], [], host_id=f"H{i}")
    assert len(store.archives()) == 2
    # 가장 새로 옮긴 두 벌이 남는다.
    assert [stored(p) for p in store.archives()] == [{"n3"}, {"n2"}]
    assert ids(store) == {"n4"}


def test_damaged_copy_is_set_aside_not_deleted(tmp_path):
    path = tmp_path / "replica.sqlite3"
    path.write_bytes(b"this is not a database" * 50)

    s = ReplicaStore(path)

    assert s.is_empty()
    s.apply_snapshot([note("a")], [], host_id="H1")
    assert ids(s) == {"a"}
    moved = list(tmp_path.glob("replica.damaged-*.sqlite3"))
    assert len(moved) == 1 and moved[0].read_bytes().startswith(b"this is not")
    s.close()


def test_copy_survives_reopening(tmp_path):
    path = tmp_path / "replica.sqlite3"
    s = ReplicaStore(path)
    s.apply_snapshot([note("a", "남는 글")], [{"id": "f", "name": "폴더"}],
                     host_id="H1", nickname="지우")
    s.close()

    again = ReplicaStore(path)
    notes, folders = again.load()
    assert [n["content"] for n in notes] == ["남는 글"]
    assert folders == [{"id": "f", "name": "폴더"}]
    assert again.meta()["nickname"] == "지우"
    again.close()


# ── 호스트에서 오는 변화를 옮겨 적기 ────────────────────────────────────

def test_sync_follows_the_client(store):
    client = ClientBase("지우")
    sync = ReplicaSync(store, client, address="10.0.0.5:5555")

    client.welcomed.emit({"notes": [note("a"), note("b")],
                          "folders": [{"id": "f", "name": "폴더"}],
                          "host": "민수", "host_id": "H1"})
    client.noteCreated.emit(note("c", "새 메모"))
    client.noteUpdated.emit(note("a", "고친 글", version=2))
    client.noteDeleted.emit("b")
    client.folderUpdated.emit({"id": "f", "name": "바꾼 이름"})
    client.folderCreated.emit({"id": "g", "name": "새 폴더"})
    client.folderDeleted.emit("g")

    notes, folders = store.load()
    assert {n["id"]: n["content"] for n in notes} == {"a": "고친 글", "c": "새 메모"}
    assert folders == [{"id": "f", "name": "바꾼 이름"}]
    meta = store.meta()
    assert (meta["host"], meta["host_id"], meta["address"], meta["nickname"]) == (
        "민수", "H1", "10.0.0.5:5555", "지우")
    assert sync is not None


def test_sync_stops_quietly_when_the_copy_cannot_be_written(store):
    client = ClientBase("지우")
    ReplicaSync(store, client)
    client.welcomed.emit({"notes": [note("a")], "folders": []})
    store.close()                      # 디스크가 사라진 것과 같다

    # 사본을 못 쓰게 되어도 앱은 그대로 돈다.
    client.noteCreated.emit(note("b"))
    client.noteDeleted.emit("a")
    client.welcomed.emit({"notes": [], "folders": []})
