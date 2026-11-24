"""백업에서 복원: 백업과 받아 둔 사본을 한 창에서 고르기, 파일을 골라 넣기,
백업 파일로 메모 파일을 새로 만들기."""
from __future__ import annotations

import os
import sqlite3

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QDropEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QFileDialog  # noqa: E402

from app import i18n  # noqa: E402
from app.backup import list_backups  # noqa: E402
from app.database import SqliteRepository, snapshot_database  # noqa: E402
from app.models import Note  # noqa: E402
from app.replica import (  # noqa: E402
    ReplicaStore, backup_source, build_host_database_from_backup, file_kind,
    list_copies, source_from_file,
)
from app.ui import confirm, theme  # noqa: E402
from app.ui.copies_dialog import CopiesDialog  # noqa: E402

ME, OTHER = "지우", "민수"


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    i18n.set_language("ko")
    theme.apply_theme(app, "")
    yield app


def make_db(path, texts, *, private: str = "") -> None:
    """공유 PC 의 메모 파일 한 벌. ``private`` 는 남이 혼자 보는 메모의 글."""
    repo = SqliteRepository(path)
    for text in texts:
        note = Note.new(ME)
        note.content = f"<p>{text}</p>"
        repo.insert_note(note)
    if private:
        note = Note.new(OTHER)
        note.content = f"<p>{private}</p>"
        note.private_owner = OTHER
        repo.insert_note(note)
    repo.close()


def contents(path) -> set[str]:
    conn = sqlite3.connect(str(path))
    try:
        return {row[0] for row in conn.execute(
            "SELECT content FROM notes WHERE deleted = 0")}
    finally:
        conn.close()


@pytest.fixture
def backups(tmp_path):
    """백업 폴더에 백업 둘(옛것에는 메모 하나, 새것에는 둘)."""
    folder = tmp_path / "backups"
    folder.mkdir()
    live = tmp_path / "live.sqlite3"
    make_db(live, ["회의"])
    snapshot_database(live, folder / "notes_20261120_090000.sqlite3")
    make_db(live, ["견적"], private="남의 비공개")
    snapshot_database(live, folder / "notes_20261122_090000.sqlite3")
    return folder


@pytest.fixture
def copy_file(tmp_path):
    store = ReplicaStore(tmp_path / "replica.sqlite3")
    store.apply_snapshot(
        [{"id": "a", "content": "<p>받아 둔 메모</p>", "version": 1,
          "updated_at": 5, "color": "#FFE066"}], [],
        host_id="H1", host=OTHER, address="10.0.0.5:5555", nickname=ME)
    store.close()
    return tmp_path / "replica.sqlite3"


# ── 파일이 무엇인지 ─────────────────────────────────────────────────────

def test_files_are_told_apart_without_being_changed(tmp_path, backups, copy_file):
    backup = list_backups(backups)[0]
    before = backup.read_bytes()
    assert file_kind(backup) == "backup"
    assert file_kind(copy_file) == "copy"
    junk = tmp_path / "memo.txt"
    junk.write_text("메모가 아니다", encoding="utf-8")
    other = tmp_path / "other.sqlite3"
    conn = sqlite3.connect(str(other))
    conn.execute("CREATE TABLE notes (id TEXT, title TEXT)")
    conn.commit()
    conn.close()
    assert file_kind(junk) == "" and file_kind(other) == ""
    assert file_kind(tmp_path / "nowhere.sqlite3") == ""
    assert not (tmp_path / "nowhere.sqlite3").exists()      # 없는 파일을 만들지 않는다
    assert backup.read_bytes() == before

    picked = source_from_file(backup)
    assert (picked.kind, picked.picked, picked.note_count) == ("backup", True, -1)
    got = source_from_file(copy_file)
    assert (got.kind, got.picked, got.host, got.note_count) == ("copy", True, OTHER, 1)
    assert source_from_file(junk) is None and source_from_file(other) is None


# ── 백업으로 메모 파일을 새로 만든다(참여하던 PC 가 공유를 시작한다) ──────

def test_a_backup_becomes_a_new_note_file_and_the_backup_is_left_alone(tmp_path, backups):
    backup = list_backups(backups)[0]
    old_id = SqliteRepository(backup).host_id()            # 열었다 닫아 식별값만 본다
    before = backup.read_bytes()
    db = tmp_path / "home" / "notes.sqlite3"

    assert build_host_database_from_backup(backup, db) is None   # 옮겨 둘 것이 없었다
    assert contents(db) == {"<p>회의</p>", "<p>견적</p>", "<p>남의 비공개</p>"}
    # 고른 파일을 그대로 쓰지 않는다. 내용만 떠 왔고, 다른 묶음으로 알아보게 한다.
    assert db.resolve() != backup.resolve()
    repo = SqliteRepository(db)
    assert repo.check() == "" and repo.host_id() and repo.host_id() != old_id
    note = Note.new(ME)
    note.content = "<p>이어서 쓴 메모</p>"
    repo.insert_note(note)
    repo.close()
    assert "<p>이어서 쓴 메모</p>" not in contents(backup)
    assert backup.read_bytes() == before                   # 백업 파일은 그대로다


def test_the_note_file_that_was_there_is_set_aside_not_deleted(tmp_path, backups):
    db = tmp_path / "notes.sqlite3"
    make_db(db, ["이 PC 에서 쓰던 메모"])
    kept = build_host_database_from_backup(list_backups(backups)[0], db)
    assert kept is not None and kept.exists() and "before-takeover" in kept.name
    assert contents(kept) == {"<p>이 PC 에서 쓰던 메모</p>"}
    assert "<p>이 PC 에서 쓰던 메모</p>" not in contents(db)


def test_what_is_not_a_backup_never_replaces_the_note_file(tmp_path, copy_file):
    db = tmp_path / "notes.sqlite3"
    make_db(db, ["지켜야 하는 메모"])
    junk = tmp_path / "junk.sqlite3"
    junk.write_bytes(b"not a database at all")
    for bad in (copy_file, junk, tmp_path / "nowhere.sqlite3"):
        with pytest.raises((ValueError, OSError, sqlite3.Error)):
            build_host_database_from_backup(bad, db)
        assert contents(db) == {"<p>지켜야 하는 메모</p>"}
    assert not db.with_name(db.name + ".new").exists()
    assert [p.name for p in tmp_path.glob("notes.before-*")] == []


# ── 창 ──────────────────────────────────────────────────────────────────

def test_the_sharing_pc_restores_only_from_backups(qapp, backups, copy_file):
    found = [backup_source(p) for p in list_backups(backups)] + list_copies(copy_file)
    dlg = CopiesDialog(found, host_notes={}, can_takeover=False, can_restore=True,
                       nickname=ME)
    # 종류가 섞여 있으면 묶음마다 이름이 붙는다. 고를 수 없는 줄이다.
    texts = [dlg._pick.itemText(i) for i in range(dlg._pick.count())]
    assert texts[0] == i18n.t("restore.group_backup")
    assert texts[3] == i18n.t("restore.group_copy") and len(texts) == 5
    assert dlg.selected_path() == str(list_backups(backups)[0])   # 가장 새 백업
    # 남이 혼자 보던 메모는 보여 주지 않는다.
    rows = [dlg._list.item(i).text() for i in range(dlg._list.count())]
    assert "남의 비공개" not in " ".join(rows) and len(dlg._notes) == 2
    assert dlg._takeover_btn.text() == i18n.t("restore.btn_restore")
    assert dlg._takeover_btn.isEnabled()
    asked, taken = [], []
    dlg.restoreRequested.connect(asked.append)
    dlg.takeoverRequested.connect(taken.append)
    dlg._takeover_btn.click()
    assert asked == [str(list_backups(backups)[0])] and taken == []
    # 받아 둔 사본으로는 되돌리지 않는다. 빠진 메모만 가져온다.
    dlg._pick.setCurrentIndex(4)
    assert dlg._selected().kind == "copy" and not dlg._takeover_btn.isEnabled()
    assert dlg._import_btn.isEnabled()
    dlg.deleteLater()


def test_a_joined_pc_can_start_sharing_from_a_copy_or_a_backup(qapp, backups, copy_file):
    found = list_copies(copy_file) + [backup_source(p) for p in list_backups(backups)]
    dlg = CopiesDialog(found, host_notes=None, can_takeover=True, nickname=ME)
    taken, asked = [], []
    dlg.takeoverRequested.connect(taken.append)
    dlg.restoreRequested.connect(asked.append)
    assert dlg._selected().kind == "backup"                 # 묶음의 차례: 백업, 사본
    assert dlg._takeover_btn.text() == i18n.t("restore.btn_takeover_backup")
    dlg._takeover_btn.click()
    dlg._pick.setCurrentIndex(dlg._pick.findData(str(copy_file)))
    assert dlg._takeover_btn.text() == i18n.t("copies.btn_takeover")
    dlg._takeover_btn.click()
    assert taken == [str(list_backups(backups)[0]), str(copy_file)] and asked == []
    assert not dlg._import_btn.isEnabled()                  # 연결돼 있지 않다
    dlg.deleteLater()


def test_a_chosen_file_is_added_and_selected(qapp, tmp_path, backups, monkeypatch):
    elsewhere = tmp_path / "usb" / "가져온 백업.sqlite3"
    elsewhere.parent.mkdir()
    snapshot_database(list_backups(backups)[1], elsewhere)
    dlg = CopiesDialog([backup_source(p) for p in list_backups(backups)],
                       host_notes={}, can_takeover=False, can_restore=True,
                       nickname=ME)
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (str(elsewhere), "")))
    dlg._file_btn.click()
    assert dlg.selected_path() == str(elsewhere) and dlg._selected().picked
    texts = [dlg._pick.itemText(i) for i in range(dlg._pick.count())]
    assert texts[0] == i18n.t("restore.group_file") and "가져온 백업.sqlite3" in texts[1]
    assert [n["content"] for n in dlg._notes] == ["<p>회의</p>"]
    assert dlg._takeover_btn.isEnabled()
    # 같은 파일을 다시 골라도 한 줄만 있다.
    assert dlg.add_file(elsewhere) and len(dlg.sources()) == 3
    dlg.deleteLater()


def test_a_file_that_is_not_a_backup_is_refused_with_a_word(qapp, tmp_path, monkeypatch):
    junk = tmp_path / "사진.sqlite3"
    junk.write_bytes(b"\x89PNG not a database")
    told: list = []
    monkeypatch.setattr(confirm, "notify",
                        lambda parent, title, text, **k: told.append((title, text)))
    dlg = CopiesDialog([], host_notes={}, can_takeover=False, can_restore=True)
    assert dlg.add_file(junk) is False and dlg.sources() == []
    assert told == [(i18n.t("restore.window_title"), i18n.t("restore.err_not_backup"))]
    dlg.deleteLater()


def test_a_file_dropped_on_the_window_is_taken(qapp, backups):
    dlg = CopiesDialog([], host_notes={}, can_takeover=False, can_restore=True,
                       nickname=ME)
    assert dlg.acceptDrops()
    path = list_backups(backups)[0]
    data = QMimeData()
    data.setUrls([QUrl.fromLocalFile(str(path))])
    event = QDropEvent(QPointF(QPoint(40, 40)), Qt.CopyAction, data, Qt.LeftButton,
                       Qt.NoModifier)
    dlg.dropEvent(event)
    assert dlg.selected_path() == str(path) and dlg._takeover_btn.isEnabled()
    # 글이나 여러 파일을 놓은 것은 받지 않는다.
    text = QMimeData()
    text.setText("메모")
    assert CopiesDialog._dropped_file(
        QDropEvent(QPointF(QPoint(40, 40)), Qt.CopyAction, text, Qt.LeftButton,
                   Qt.NoModifier)) == ""
    dlg.deleteLater()
