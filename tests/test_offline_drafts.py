"""공유 PC 와 끊긴 동안의 메모(offscreen).

끊긴 동안 새 메모를 만들고 사본의 메모를 고치기, 다시 붙었을 때 올릴지 묻기,
그리고 이 PC 가 없는 동안 남이 만든 메모를 받는 모양."""
from __future__ import annotations

import json
import os
import sqlite3
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app import i18n  # noqa: E402
from app.local_state import LocalStateStore  # noqa: E402
from app.network.client_base import ClientBase  # noqa: E402
from app.offline_drafts import KIND_EDIT, KIND_NEW, DraftStore  # noqa: E402
from app.settings import AppSettings  # noqa: E402
from app.ui import confirm, main_window as mw, theme  # noqa: E402
from app.ui.main_window import LOCAL_MARK, MainWindow  # noqa: E402
from app.ui.sticky_note import StickyNoteWidget  # noqa: E402

ME = "지우"
OTHER = "민수"
ADDRESS = "10.0.0.5:5555"


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


# ── 적어 두는 곳 ────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path):
    s = DraftStore(tmp_path / "offline.sqlite3")
    yield s
    s.close()


def test_drafts_are_kept_per_person_whatever_the_host_address(store):
    """공유 PC 의 주소가 바뀌거나 다른 PC 가 공유를 이어받아도 쓴 글은 따라온다.
    다른 닉네임의 글은 꺼내지 않는다(올리면 그 사람의 나만 보기 메모가 된다)."""
    store.save_new(note("a", "내 메모"), address=ADDRESS, nickname=ME)
    store.save_new(note("b", "주소가 바뀌기 전에 쓴 메모"),
                   address="10.0.0.9:5555", nickname=ME)
    store.save_new(note("c", "다른 닉네임으로 쓴 메모"), address=ADDRESS, nickname=OTHER)

    mine = store.pending(ME)

    assert [(d.key, d.kind) for d in mine] == [("a", KIND_NEW), ("b", KIND_NEW)]
    assert [d.key for d in store.pending(OTHER)] == ["c"]


def test_an_edit_keeps_what_it_started_from(store):
    """끊긴 동안 여러 번 고쳐도 견줄 기준은 처음 받은 메모다."""
    store.save_edit("a", note("a", "첫 수정"), base_version=3, started_online=True,
                    address=ADDRESS, nickname=ME)
    store.save_edit("a", note("a", "둘째 수정"), base_version=9, started_online=False,
                    address=ADDRESS, nickname=ME)

    (draft,) = store.pending(ME)

    assert (draft.kind, draft.base_id, draft.content) == (KIND_EDIT, "a", "둘째 수정")
    assert draft.base_version == 3
    # 끊긴 뒤에 다시 손댔으면 더는 '연결돼 있을 때 쓰던 글'이 아니다.
    assert not draft.started_online
    store.remove_edit("a", ME)
    assert store.pending(ME) == []


def test_two_people_on_one_pc_do_not_overwrite_each_others_edit(store):
    """한 PC 에서 닉네임을 바꿔 같은 메모를 고쳐도 앞사람의 글이 남는다."""
    store.save_edit("a", note("a", "내가 고친 글"), base_version=3, started_online=False,
                    address=ADDRESS, nickname=ME)
    store.save_edit("a", note("a", "다른 닉네임으로 고친 글"), base_version=7,
                    started_online=False, address=ADDRESS, nickname=OTHER)

    (mine,) = store.pending(ME)
    (theirs,) = store.pending(OTHER)

    assert (mine.content, mine.base_version) == ("내가 고친 글", 3)
    assert (theirs.content, theirs.base_version) == ("다른 닉네임으로 고친 글", 7)


def test_drafts_survive_reopening_and_a_damaged_file_is_set_aside(tmp_path):
    path = tmp_path / "offline.sqlite3"
    first = DraftStore(path)
    first.save_new(note("a", "남아야 하는 글"), address=ADDRESS, nickname=ME)
    first.close()
    again = DraftStore(path)
    assert [d.content for d in again.pending(ME)] == ["남아야 하는 글"]
    again.close()

    for extra in ("-wal", "-shm"):
        side = path.with_name(path.name + extra)
        if side.exists():
            side.unlink()
    path.write_bytes(b"not a database at all" * 50)
    fresh = DraftStore(path)
    assert fresh.pending(ME) == []
    fresh.close()
    assert [p.name for p in tmp_path.iterdir() if ".damaged-" in p.name]
    sqlite3.connect(str(path)).close()


# ── 메인 창 ─────────────────────────────────────────────────────────────

class FakeClient(ClientBase):
    """보낸 요청을 적어 두기만 한다. 공유 PC 의 답은 테스트가 낸다."""

    def __init__(self):
        super().__init__(ME)
        self.calls: list[tuple] = []

    def acquire_lock(self, note_id):
        self.calls.append(("lock", note_id))

    def release_lock(self, note_id):
        self.calls.append(("release", note_id))

    def update_note(self, note_id, content, color, width, height, expected_version):
        self.calls.append(("update", note_id, content))

    def create_note(self, color, width, height, folder_id=None,
                    private_owner=None, quiet=False):
        self.calls.append(("create", private_owner, quiet))

    def delete_note(self, note_id):
        self.calls.append(("delete", note_id))

    def of(self, kind: str) -> list[tuple]:
        return [c for c in self.calls if c[0] == kind]


class Guest:
    """참여한 PC 한 대: 메인 창, 가짜 클라이언트, 끊긴 동안 쓴 글을 적는 곳."""

    def __init__(self, tmp_path, store: DraftStore):
        self.client = FakeClient()
        self.store = store
        local = LocalStateStore(tmp_path / "local_state.json")
        local.state.last_host_ip, local.state.last_host_port = "10.0.0.5", 5555
        self.win = MainWindow(client=self.client, local_state=local, role="guest",
                              settings=AppSettings(), drafts=store)
        self.asked: list[list] = []
        self.answer = "later"
        self.win._choose_for_drafts = self._choose

    def _choose(self, drafts):
        self.asked.append(sorted((d.kind, d.content) for d in drafts))
        return self.answer

    def connect(self, *notes: dict, locks=None) -> None:
        self.client.connectionChanged.emit(True)
        self.client.welcomed.emit({
            "users": [ME, OTHER], "host": OTHER, "notes": list(notes),
            "locks": dict(locks or {}), "folders": [], "heartbeat_ms": 3000})
        QApplication.processEvents()        # 묻는 창은 화면이 바뀐 뒤에 뜬다

    def disconnect(self) -> None:
        self.client.connectionChanged.emit(False)

    def texts(self) -> list[str]:
        return sorted(n["content"] for n in self.win._notes.values())

    def write(self, note_id: str, add: str) -> None:
        """메모를 눌러 글을 덧붙이고 다른 곳을 눌러 끝낸다."""
        sticky = self.win._stickies[note_id]
        sticky._request_edit()
        assert sticky._edit_mode, "편집이 시작되지 않았다"
        cursor = sticky._text.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(add)
        sticky._commit_and_release()

    def new_note(self, text: str) -> str:
        before = set(self.win._notes)
        self.win._create_note("#BAE1FF")
        (made,) = set(self.win._notes) - before
        self.write(made, text)
        return made

    def plain(self, note_id: str) -> str:
        from app.ui.rich_text import html_to_plain
        return html_to_plain(self.win._notes[note_id]["content"]).strip()

    def answer_as_host(self, new_id: str) -> None:
        """방금 보낸 '새 메모' 요청에 공유 PC 가 답한다."""
        created = self.client.of("create")[-1]
        self.client.noteCreated.emit(note(
            new_id, "", created_by=ME, updated_by=ME, private_owner=created[1]))
        self.client.lockGranted.emit(new_id)


@pytest.fixture
def guest(qapp, tmp_path, monkeypatch, store):
    monkeypatch.setattr(StickyNoteWidget, "bring_to_front",
                        lambda self, *, start_editing=False: None)
    monkeypatch.setattr(mw, "DRAFTS_ASK_MS", 0)
    monkeypatch.setattr(confirm, "ask", lambda *a, **k: True)
    g = Guest(tmp_path, store)
    yield g
    g.win._force_quit = True
    g.win._teardown_stickies()
    g.win.close()
    g.win.deleteLater()
    qapp.processEvents()


def test_new_note_can_be_written_while_disconnected(guest):
    guest.connect(note("a", "회의록"))
    guest.disconnect()
    guest.client.calls.clear()

    made = guest.new_note("끊긴 동안 쓴 글")

    local = guest.win._notes[made]
    assert guest.plain(made) == "끊긴 동안 쓴 글"
    assert local[LOCAL_MARK] == KIND_NEW and local["private_owner"] == ME
    assert guest.client.calls == []                     # 닿지 않을 요청은 보내지 않는다
    (draft,) = guest.store.pending(ME)
    assert draft.kind == KIND_NEW and draft.key == made
    assert LOCAL_MARK not in draft.note                 # 이 PC 의 표시는 적지 않는다
    assert guest.win._stickies[made]._lock_label.text() == "오프라인"
    # 다시 고칠 수 있고, 고친 것이 적힌다.
    guest.write(made, " 한 줄 더")
    assert guest.store.pending(ME)[0].content == guest.win._notes[made]["content"]
    assert guest.plain(made).endswith("한 줄 더")


def test_copy_note_can_be_edited_while_disconnected(guest):
    guest.connect(note("a", "회의록", version=4))
    guest.win._set_note_visibility("a", True)
    guest.disconnect()
    guest.client.calls.clear()

    guest.write("a", " 끊긴 동안 고침")

    assert guest.plain("a") == "회의록 끊긴 동안 고침"
    assert guest.win._notes["a"][LOCAL_MARK] == KIND_EDIT
    assert guest.client.calls == []
    (draft,) = guest.store.pending(ME)
    assert (draft.kind, draft.base_id, draft.base_version, draft.started_online) == (
        KIND_EDIT, "a", 4, False)
    # 한 번 더 고쳐도 원래 메모 하나에 글 하나다.
    guest.write("a", " 또 고침")
    assert len(guest.store.pending(ME)) == 1


def test_only_notes_made_offline_can_be_deleted_offline(guest):
    guest.connect(note("a", "회의록"))
    guest.disconnect()
    made = guest.new_note("지울 글")

    guest.win._delete_note_confirm("a")                 # 공유 PC 의 메모
    assert "a" in guest.win._notes and "끊겨" in guest.win._status.currentMessage()
    guest.win._delete_note_confirm(made)
    assert made not in guest.win._notes and made not in guest.win._stickies
    assert guest.store.pending(ME) == []
    assert guest.client.of("delete") == []


def test_deleting_a_note_i_am_still_typing_in_really_deletes_it(guest):
    """지우는 메모에 쓰던 글이 새 메모로 되살아나면 안 된다."""
    guest.connect(note("a", "회의록"))
    guest.disconnect()
    guest.win._create_note("#BAE1FF")
    (made,) = set(guest.win._notes) - {"a"}
    sticky = guest.win._stickies[made]
    sticky._request_edit()
    sticky._text.setPlainText("쓰다가 지우는 글")

    guest.win._delete_note_confirm(made)

    assert guest.texts() == ["회의록"]
    assert guest.store.pending(ME) == [] and guest.win._orphan_texts == []


def test_reconnecting_asks_and_later_keeps_everything(guest):
    guest.connect(note("a", "회의록"))
    guest.win._set_note_visibility("a", True)
    guest.disconnect()
    guest.new_note("새 글")
    guest.write("a", " 고침")

    guest.connect(note("a", "회의록"))

    # 화면은 공유 PC 의 것으로 돌아가고, 쓴 글은 어떻게 할지 묻는다.
    assert guest.texts() == ["회의록"]
    assert len(guest.asked) == 1 and [kind for kind, _ in guest.asked[0]] == [
        KIND_EDIT, KIND_NEW]
    assert len(guest.store.pending(ME)) == 2
    assert guest.win._replica_banner.isVisibleTo(guest.win)
    assert guest.win._replica_text.text() == "올리지 않은 메모 2개"
    # '나중에'를 고른 뒤에는 다시 붙어도 묻지 않는다. 알림 줄을 누르면 묻는다.
    guest.disconnect()
    assert len(guest.texts()) == 2 and any("고침" in t for t in guest.texts())
    guest.connect(note("a", "회의록"))
    assert len(guest.asked) == 1
    guest.win._copies_btn.click()
    assert len(guest.asked) == 2


def test_kept_notes_go_up_as_private_new_notes(guest):
    guest.connect(note("a", "회의록"))
    guest.win._set_note_visibility("a", True)
    guest.win._local.state.set_note_state("a", x=300, y=200)
    guest.disconnect()
    made = guest.new_note("새 글")
    guest.win._local.state.set_note_state(made, x=640, y=480)
    guest.write("a", " 고침")
    guest.answer = "keep"
    guest.client.calls.clear()

    guest.connect(note("a", "회의록"))

    # 둘 다 나만 보기 새 메모로, 알림 없이 만든다. 원래 메모는 건드리지 않는다.
    assert guest.client.of("create") == [("create", ME, True)] * 2
    assert guest.client.of("update") == []
    for new_id in ("h1", "h2"):
        created = note(new_id, "", created_by=ME, updated_by=ME, private_owner=ME)
        guest.client.noteCreated.emit(created)
        guest.client.lockGranted.emit(new_id)
    sent = {c[1]: c[2] for c in guest.client.of("update")}
    assert set(sent) == {"h1", "h2"} and "a" not in sent
    from app.ui.rich_text import html_to_plain
    assert sorted(html_to_plain(t).strip() for t in sent.values()) == [
        "새 글", "회의록 고침"]
    # 올린 뒤에는 적어 둔 것과 알림이 없다.
    assert guest.store.pending(ME) == []
    assert not guest.win._replica_banner.isVisibleTo(guest.win)
    # 만든 메모는 두던 자리에, 고친 글의 새 메모는 원래 메모 옆에 놓인다.
    spots = sorted((guest.win._local.state.notes[n].x, guest.win._local.state.notes[n].y)
                   for n in ("h1", "h2"))
    assert spots == [(328, 228), (640, 480)]
    assert made not in guest.win._local.state.notes


def test_discarding_needs_a_second_yes(guest, monkeypatch):
    guest.connect(note("a", "회의록"))
    guest.disconnect()
    guest.new_note("버릴 글")
    guest.answer = "discard"
    answers = iter([False, True])
    monkeypatch.setattr(confirm, "ask", lambda *a, **k: next(answers))

    guest.connect(note("a", "회의록"))

    # 처음에는 확인에서 물러섰다. 다시 물었고 이번에는 지웠다.
    assert len(guest.asked) == 2
    assert guest.store.pending(ME) == []
    assert guest.client.of("create") == []
    assert not guest.win._replica_banner.isVisibleTo(guest.win)


def test_nothing_is_asked_about_empty_or_unchanged_drafts(guest):
    guest.connect(note("a", "회의록"))
    guest.win._set_note_visibility("a", True)
    guest.disconnect()
    guest.win._create_note("#BAE1FF")                   # 만들기만 하고 쓰지 않았다
    guest.write("a", " 고침")
    edited = guest.win._notes["a"]["content"]

    # 그 사이 공유 PC 의 메모가 내가 고친 글과 같아져 있다.
    guest.connect(note("a", edited, version=2))

    assert guest.asked == [] and guest.store.pending(ME) == []


def test_typing_that_began_online_goes_back_into_the_same_note(guest, tmp_path):
    """잠깐 끊겼다 붙은 경우. 앱을 껐다 켠 뒤라도, 그 사이 아무도 고치지 않았으면
    묻지 않고 원래 메모에 넣는다."""
    guest.store.save_edit("a", note("a", "회의록 쓰던 글"), base_version=4,
                          started_online=True, address=ADDRESS, nickname=ME)

    guest.connect(note("a", "회의록", version=4))

    assert guest.asked == []
    assert guest.client.of("lock") == [("lock", "a")]
    guest.client.lockGranted.emit("a")
    assert guest.client.of("update") == [("update", "a", "회의록 쓰던 글")]
    assert guest.store.pending(ME) == []


def test_typing_that_began_online_is_asked_about_if_the_note_changed(guest):
    guest.store.save_edit("a", note("a", "회의록 쓰던 글"), base_version=4,
                          started_online=True, address=ADDRESS, nickname=ME)

    guest.connect(note("a", "남이 먼저 고친 글", version=5))

    assert guest.client.of("lock") == []                # 남의 글을 덮어쓰지 않는다
    assert guest.asked == [[(KIND_EDIT, "회의록 쓰던 글")]]


def test_drafts_follow_me_to_a_new_host_address_but_not_to_another_person(guest):
    guest.store.save_new(note("x", "주소가 바뀌기 전에 쓴 글", created_by=ME),
                         address="10.0.0.9:5555", nickname=ME)
    guest.store.save_new(note("y", "다른 닉네임의 글"), address=ADDRESS, nickname=OTHER)

    guest.connect(note("a", "회의록"))

    assert guest.asked == [[(KIND_NEW, "주소가 바뀌기 전에 쓴 글")]]
    guest.disconnect()
    assert guest.texts() == ["주소가 바뀌기 전에 쓴 글", "회의록"]
    assert len(guest.store.pending(OTHER)) == 1


def test_edit_of_a_note_the_copy_does_not_have_is_shown_as_a_new_note(guest):
    """다른 공유 PC 의 메모를 고친 글. 지금 보이는 메모들에는 그 원래 메모가 없다."""
    guest.store.save_edit("gone", note("gone", "다른 곳에서 고친 글"), base_version=2,
                          started_online=False, address="10.0.0.9:5555", nickname=ME)
    guest.connect(note("a", "회의록"))
    guest.asked.clear()

    guest.disconnect()

    assert guest.texts() == ["다른 곳에서 고친 글", "회의록"]
    (draft,) = guest.store.pending(ME)
    assert draft.kind == KIND_NEW and draft.note["private_owner"] == ME


def test_pc_that_took_over_sharing_is_asked_too(qapp, tmp_path, store, monkeypatch):
    """참여하던 PC 가 끊긴 동안 메모를 쓰고, 사본으로 공유를 이어받았다."""
    monkeypatch.setattr(StickyNoteWidget, "bring_to_front",
                        lambda self, *, start_editing=False: None)
    monkeypatch.setattr(mw, "DRAFTS_ASK_MS", 0)
    store.save_new(note("n", "끊긴 동안 쓴 글", created_by=ME, private_owner=ME),
                   address=ADDRESS, nickname=ME)
    client = FakeClient()
    win = MainWindow(client=client, local_state=LocalStateStore(tmp_path / "ls.json"),
                     role="host", settings=AppSettings(), drafts=store)
    asked = []
    win._choose_for_drafts = lambda drafts: asked.append(len(drafts)) or "keep"
    try:
        client.connectionChanged.emit(True)
        client.welcomed.emit({"users": [ME], "host": ME, "notes": [note("a", "사본의 메모")],
                              "locks": {}, "folders": [], "heartbeat_ms": 3000})
        qapp.processEvents()
        assert asked == [1] and client.of("create") == [("create", ME, True)]
    finally:
        win._force_quit = True
        win._teardown_stickies()
        win.close()
        win.deleteLater()


def test_new_note_asked_from_outside_the_app_works_offline(guest):
    """작업 표시줄이나 바탕화면 우클릭 메뉴의 '새 메모'."""
    guest.connect(note("a", "회의록"))
    guest.disconnect()
    before = set(guest.win._notes)

    guest.win.handle_new_note_request()

    (made,) = set(guest.win._notes) - before
    assert guest.win._notes[made][LOCAL_MARK] == KIND_NEW
    assert len(guest.store.pending(ME)) == 1


def test_the_question_waits_while_the_screen_is_busy(guest, monkeypatch):
    """전체 화면으로 발표하는 중에는 창을 띄우지 않는다. 알림 줄은 남는다."""
    from app.ui import toast
    guest.connect(note("a", "회의록"))
    guest.disconnect()
    guest.new_note("새 글")
    monkeypatch.setattr(toast, "user_is_busy", lambda: True)
    monkeypatch.setattr(mw, "DRAFTS_BUSY_RETRY_MS", 3_600_000)

    guest.connect(note("a", "회의록"))

    assert guest.asked == []
    assert guest.win._replica_text.text() == "올리지 않은 메모 1개"
    guest.win._copies_btn.click()                       # 직접 누르면 묻는다
    assert len(guest.asked) == 1


def test_drafts_come_back_when_the_app_starts_disconnected(qapp, tmp_path, store,
                                                           monkeypatch):
    monkeypatch.setattr(StickyNoteWidget, "bring_to_front",
                        lambda self, *, start_editing=False: None)
    store.save_new(note("n", "지난번에 쓴 글", created_by=ME, private_owner=ME),
                   address=ADDRESS, nickname=ME)
    g = Guest(tmp_path, store)
    try:
        # 사본이 없어도(한 번도 붙지 못했어도) 쓴 글은 보인다.
        assert g.texts() == ["지난번에 쓴 글"]
        assert g.win._notes["n"][LOCAL_MARK] == KIND_NEW
        g.write("n", " 이어서")
        assert "이어서" in store.pending(ME)[0].content
    finally:
        g.win._force_quit = True
        g.win._teardown_stickies()
        g.win.close()
        g.win.deleteLater()


def test_my_own_offline_edit_is_not_announced_as_someone_elses(qapp, tmp_path, store,
                                                              monkeypatch):
    """끊긴 채 앱을 다시 켜면 사본 위에 내가 고친 글을 얹는다. 그것이 원래 메모를
    쓴 사람이 방금 고친 것처럼 알려지면 안 된다."""
    from app.replica import ReplicaStore
    monkeypatch.setattr(StickyNoteWidget, "bring_to_front",
                        lambda self, *, start_editing=False: None)
    copy = ReplicaStore(tmp_path / "replica.sqlite3")
    copy.apply_snapshot([note("a", "회의록", version=4)], [], host_id="H1",
                        address=ADDRESS, nickname=ME)
    store.save_edit("a", note("a", "회의록 내가 고침", updated_by=ME), base_version=4,
                    started_online=False, address=ADDRESS, nickname=ME)
    client = FakeClient()
    local = LocalStateStore(tmp_path / "ls.json")
    local.state.last_host_ip, local.state.last_host_port = "10.0.0.5", 5555
    win = MainWindow(client=client, local_state=local, role="guest",
                     settings=AppSettings(), replica=copy, drafts=store)
    try:
        shown = win._notes["a"]
        assert shown["content"] == "회의록 내가 고침" and shown["updated_by"] == ME
        assert win._announce_edited == {} and not win._announce_timer.isActive()
    finally:
        win._force_quit = True
        win._teardown_stickies()
        win.close()
        win.deleteLater()
        copy.close()


def test_losing_the_host_clears_other_peoples_locks(guest):
    """누가 편집 중이었는지는 끊긴 뒤로는 알 수 없다. 그 표시가 남아 있으면 끊긴
    동안 그 메모를 고칠 수 없다."""
    guest.connect(note("a", "회의록"), locks={"a": OTHER})
    guest.win._set_note_visibility("a", True)
    sticky = guest.win._stickies["a"]
    assert sticky.is_locked_by_other()

    guest.disconnect()

    assert not sticky.is_locked_by_other() and guest.win._locks == {}
    guest.write("a", " 끊긴 동안 고침")
    assert len(guest.store.pending(ME)) == 1
    # 보이는 것은 끊기기 직전의 내용이다. 목록 위에 알린다.
    assert guest.win._replica_banner.isVisibleTo(guest.win)
    assert guest.win._replica_text.text().endswith("받은 사본")


# ── 이 PC 가 없는 동안 생긴 메모 ────────────────────────────────────────

def test_notes_made_by_others_while_away_arrive_hidden(guest):
    guest.connect(note("a", "전부터 보던 메모"), note("mine", "내가 만든 메모", created_by=ME))
    guest.win._set_note_visibility("a", True)
    guest.disconnect()

    guest.connect(note("a", "전부터 보던 메모"), note("mine", "내가 만든 메모", created_by=ME),
                  note("b", "없는 동안 남이 만든 메모"))

    state = guest.win._local.state.notes
    assert state["b"].hidden and not guest.win._stickies["b"].isVisible()
    assert not state["a"].hidden                         # 보이게 둔 것은 그대로
    assert not state["mine"].hidden                      # 내 메모는 숨기지 않는다
    assert guest.win._place_count.text() == "3개"


def test_notes_already_on_the_desktop_stay_after_updating(qapp, tmp_path,
                                                          monkeypatch, store):
    """옛 버전은 받은 메모를 모두 띄웠고 그 자리 기록을 바로 적지 않았다. 그런
    PC 가 올라오면 기록이 없는 메모도 떠 있던 것이므로 가리지 않는다."""
    monkeypatch.setattr(StickyNoteWidget, "bring_to_front",
                        lambda self, *, start_editing=False: None)
    monkeypatch.setattr(mw, "DRAFTS_ASK_MS", 0)
    path = tmp_path / "local_state.json"
    path.write_text(json.dumps({
        "nickname": ME, "last_mode": "guest",
        "notes": {"a": {"x": 300, "y": 200, "hidden": False, "w": 0, "h": 0}},
    }), encoding="utf-8")
    g = Guest(tmp_path, store)
    try:
        g.connect(note("a", "자리를 옮겨 둔 메모"), note("b", "한 번도 건드리지 않은 메모"))
        state = g.win._local.state
        assert not state.notes["b"].hidden and g.win._stickies["b"].isVisible()
        # 한 번 받은 뒤로는 새 규칙을 쓴다. 파일에도 적혀 다음 실행까지 간다.
        assert json.loads(path.read_text(encoding="utf-8"))["hides_new"] is True
        g.disconnect()
        g.connect(note("a", "자리를 옮겨 둔 메모"), note("b", "한 번도 건드리지 않은 메모"),
                  note("c", "없는 동안 남이 만든 메모"))
        assert state.notes["c"].hidden and not state.notes["b"].hidden
    finally:
        g.win._force_quit = True
        g.win._teardown_stickies()
        g.win.close()
        g.win.deleteLater()
        qapp.processEvents()


def test_a_new_install_hides_other_peoples_notes_from_the_start(tmp_path):
    fresh = LocalStateStore(tmp_path / "none.json")
    assert fresh.state.hides_new
    fresh.save()
    assert LocalStateStore(tmp_path / "none.json").state.hides_new
