"""사본을 둘러싼 상황들(offscreen).

공유 PC 둘을 오가는 PC, 사본으로 공유를 이어받았다가 원래 공유 PC 로 돌아온 PC,
한 번 가져온 메모를 다시 가져오지 않기."""
from __future__ import annotations

import os
import sqlite3

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app import i18n  # noqa: E402
from app.database import SqliteRepository  # noqa: E402
from app.local_state import LocalStateStore  # noqa: E402
from app.models import Folder, Note  # noqa: E402
from app.network.client_base import ClientBase  # noqa: E402
from app.replica import (  # noqa: E402
    ReplicaStore, copy_to_show, list_copies, missing_notes, read_host_database,
    read_source,
)
from app.settings import AppSettings  # noqa: E402
from app.ui import theme  # noqa: E402
from app.ui.copies_dialog import CopiesDialog  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402

ME = "지우"
OTHER = "민수"
A, B = "10.0.0.5:5555", "10.0.0.9:5555"


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    i18n.set_language("ko")
    theme.apply_theme(app, "")
    yield app


def note(nid: str, text: str = "", version: int = 1, **more) -> dict:
    return {"id": nid, "content": text or nid, "version": version,
            "updated_at": 1000 + version, "color": "#FFE066", **more}


def ids(store: ReplicaStore) -> set[str]:
    return {n["id"] for n in store.load()[0]}


@pytest.fixture
def store(tmp_path):
    s = ReplicaStore(tmp_path / "replica.sqlite3")
    yield s
    s.close()


# ── 공유 PC 둘을 오간다 ─────────────────────────────────────────────────

def test_going_back_and_forth_keeps_one_copy_per_host(store):
    first = [note("a"), note("b")]
    second = [note("x"), note("y")]
    store.apply_snapshot(first, [], host_id="H1", host=OTHER, address=A, nickname=ME)
    for _ in range(4):
        assert store.apply_snapshot(second, [], host_id="H2", host="서연",
                                    address=B, nickname=ME)
        assert ids(store) == {"x", "y"} and store.meta()["host_id"] == "H2"
        assert len(store.archives()) == 1        # 첫째 공유 PC 의 사본 한 벌
        assert store.apply_snapshot(first, [], host_id="H1", host=OTHER,
                                    address=A, nickname=ME)
        assert ids(store) == {"a", "b"} and store.meta()["host_id"] == "H1"
        assert len(store.archives()) == 1        # 둘째 공유 PC 의 사본 한 벌


def test_visiting_a_host_with_no_notes_does_not_pile_up_copies(store):
    """메모가 없는 공유 PC 를 오갈 때마다 원래 공유 PC 의 사본이 한 벌씩 늘었다."""
    first = [note("a"), note("b")]
    store.apply_snapshot(first, [], host_id="H1", host=OTHER, address=A, nickname=ME)
    for _ in range(3):
        store.apply_snapshot([], [], host_id="H2", host="서연", address=B, nickname=ME)
        assert ids(store) == set() and store.meta()["host_id"] == "H2"
        assert [ids_of(p) for p in store.archives()] == [{"a", "b"}]
        store.apply_snapshot(first, [], host_id="H1", host=OTHER, address=A,
                             nickname=ME)
        assert ids(store) == {"a", "b"} and store.meta()["host_id"] == "H1"
        assert [ids_of(p) for p in store.archives()] == [set()]


def test_copy_brought_back_follows_what_the_host_has_now(store):
    """돌아와 보니 그 사이 공유 PC 에서 메모가 지워지고 새로 생겼다."""
    store.apply_snapshot([note("a"), note("b")], [], host_id="H1")
    store.apply_snapshot([note("x")], [], host_id="H2")

    store.apply_snapshot([note("b", "고친 글", version=2), note("c")], [], host_id="H1")

    notes = {n["id"]: n["content"] for n in store.load()[0]}
    assert notes == {"b": "고친 글", "c": "c"}
    assert [ids_of(p) for p in store.archives()] == [{"x"}]


def ids_of(path) -> set[str]:
    conn = sqlite3.connect(str(path))
    try:
        return {r[0] for r in conn.execute("SELECT id FROM notes")}
    finally:
        conn.close()


def test_new_notes_file_on_the_same_host_is_still_set_aside(store):
    """오간 적이 있어도, 새로 만들어진 메모 파일(다른 식별값)은 처음 보는 묶음이다."""
    store.apply_snapshot([note("a"), note("b")], [], host_id="H1")
    store.apply_snapshot([note("x")], [], host_id="H2")
    store.apply_snapshot([note("a"), note("b")], [], host_id="H1")

    assert store.apply_snapshot([], [], host_id="H3")

    assert store.is_empty()
    assert sorted(map(sorted, map(ids_of, store.archives()))) == [["a", "b"], ["x"]]


def test_hosts_without_an_id_are_never_mixed_up(store):
    """식별값을 보내지 않는 옛 버전 공유 PC 끼리는 되살려 쓰지 않는다."""
    store.apply_snapshot([note("a")], [])
    store.apply_snapshot([note("x")], [])
    store.apply_snapshot([note("a")], [])
    assert ids(store) == {"a"} and len(store.archives()) == 2


def test_copy_shown_before_connecting_is_the_one_from_that_address(store):
    path = store.path
    store.apply_snapshot([note("a")], [], host_id="H1", address=A, nickname=ME)
    assert copy_to_show(path, A, ME) == path
    # 다른 닉네임으로 받은 사본에는 그 사람의 비공개 메모가 있다.
    assert copy_to_show(path, A, OTHER) is None
    # 처음 붙어 보는 주소: 그 주소에서 받은 사본이 없다.
    assert copy_to_show(path, B, ME) is None

    store.apply_snapshot([note("x")], [], host_id="H2", address=B, nickname=ME)
    assert copy_to_show(path, B, ME) == path
    back = copy_to_show(path, A, ME)             # 옮겨 둔 첫째 공유 PC 의 사본
    assert back is not None and back != path and ids_of(back) == {"a"}


def test_copy_without_an_address_is_shown_as_before(store):
    store.apply_snapshot([note("a")], [], host_id="H1", nickname=ME)
    assert copy_to_show(store.path, A, ME) == store.path


class _Client(ClientBase):
    def __init__(self):
        super().__init__(ME)
        self.created: list = []

    def create_note(self, color, width, height, folder_id=None,
                    private_owner=None, quiet=False):
        self.created.append((folder_id, private_owner))

    def create_folder(self, name, color, private=False, folder_id=None):
        pass

    def acquire_lock(self, note_id):
        pass


def test_window_shows_the_copy_of_the_host_it_is_joining(qapp, store, tmp_path):
    full = dict(color="#FFE066", width=300, height=240, created_by=OTHER)
    store.apply_snapshot([note("a", "첫째 공유 PC 의 메모", **full)], [],
                         host_id="H1", address=A, nickname=ME)
    store.apply_snapshot([note("x", "둘째 공유 PC 의 메모", **full)], [],
                         host_id="H2", address=B, nickname=ME)
    local = LocalStateStore(tmp_path / "ls.json")
    local.state.last_host_ip, local.state.last_host_port = "10.0.0.5", 5555

    win = MainWindow(client=_Client(), local_state=local, role="guest",
                     settings=AppSettings(), replica=store)
    try:
        assert [n["content"] for n in win._notes.values()] == ["첫째 공유 PC 의 메모"]
        assert win._replica_shown
    finally:
        win._force_quit = True
        win._teardown_stickies()
        win.close()
        win.deleteLater()


# ── 이 PC 가 공유하던 메모 ──────────────────────────────────────────────

@pytest.fixture
def own_db(tmp_path):
    """이 PC 가 공유 PC 였을 때의 메모 파일."""
    repo = SqliteRepository(tmp_path / "notes.sqlite3")
    mine = Folder.new(ME, "내 폴더", private_owner=ME)
    theirs = Folder.new(OTHER, "민수 폴더", private_owner=OTHER)
    for folder in (mine, theirs):
        repo.insert_folder(folder)

    def add(text, by=ME, **more):
        n = Note.new(by)
        n.content = text
        for key, value in more.items():
            setattr(n, key, value)
        repo.insert_note(n)
        return n

    add("공개 메모", by=OTHER)
    add("내 비공개", private_owner=ME)
    add("내 폴더 속", folder_id=mine.id)
    add("민수 비공개", by=OTHER, private_owner=OTHER)
    add("민수 폴더 속", by=OTHER, folder_id=theirs.id)
    add("지운 메모", deleted=1)
    repo.close()
    return tmp_path / "notes.sqlite3"


def test_own_notes_file_shows_only_what_is_mine_to_see(own_db):
    notes, folders, meta = read_host_database(own_db, ME)
    assert sorted(n["content"] for n in notes) == ["공개 메모", "내 비공개", "내 폴더 속"]
    assert [f["name"] for f in folders] == ["내 폴더"]
    assert meta["nickname"] == ME and int(meta["synced_at"]) > 0
    # 읽기만 한다.
    repo = SqliteRepository(own_db)
    assert len(repo.list_notes(include_deleted=True)) == 6
    repo.close()


def test_own_notes_file_is_listed_after_the_copies(own_db, tmp_path):
    replica = tmp_path / "replica.sqlite3"
    store = ReplicaStore(replica)
    store.apply_snapshot([note("a")], [], host_id="H1", host=OTHER, nickname=ME)
    store.close()

    found = list_copies(replica, host_db=own_db, nickname=ME)

    assert [(c.kind, c.current, c.note_count) for c in found] == [
        ("copy", True, 1), ("host", False, 3)]
    assert len(read_source(found[1], ME)[0]) == 3
    # 공유 PC 로 쓰는 동안에는 넣지 않는다(그 파일이 곧 지금 메모다).
    assert [c.kind for c in list_copies(replica)] == ["copy"]
    # 다른 닉네임에게는 공개 메모만 보인다.
    assert list_copies(replica, host_db=own_db,
                       nickname="처음 온 사람")[1].note_count == 1
    assert [c.kind for c in list_copies(
        replica, host_db=tmp_path / "nowhere.sqlite3", nickname=ME)] == ["copy"]


def test_dialog_offers_what_was_written_while_this_pc_was_sharing(qapp, own_db, tmp_path):
    found = list_copies(tmp_path / "replica.sqlite3", host_db=own_db, nickname=ME)
    host_notes = {"h1": note("h1", "공개 메모")}
    dlg = CopiesDialog(found, host_notes=host_notes, can_takeover=True, nickname=ME)

    assert "이 PC에서 공유" in dlg._pick.itemText(0)
    assert dlg._import_btn.text() == "2개 가져오기"
    assert sorted(n["content"] for n in dlg.missing()) == ["내 비공개", "내 폴더 속"]
    # 사본이 아니므로 '이 사본으로 공유 시작'은 누를 수 없다.
    assert not dlg._takeover_btn.isEnabled()
    dlg.deleteLater()


# ── 가져오기 ────────────────────────────────────────────────────────────

def test_what_was_brought_in_once_is_not_offered_again():
    copy = [note("a", "이어받은 뒤 쓴 메모"), note("b", "고친 글", version=5)]
    host = {"b": note("b", "원래 글")}
    assert [n["id"] for n in missing_notes(copy, host)] == ["a", "b"]
    # 가져오면 새 id 의 메모로 생긴다.
    host["n1"] = note("n1", "이어받은 뒤 쓴 메모")
    host["n2"] = note("n2", "고친 글")
    assert missing_notes(copy, host) == []


def test_empty_notes_are_not_worth_bringing_in():
    assert missing_notes([{"id": "a", "content": "", "updated_at": 5}], {}) == []
