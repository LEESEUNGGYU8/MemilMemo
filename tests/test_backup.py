"""백업: 온전한 사본만 남기기, 상한 원본으로 옛 백업을 밀어내지 않기, 되살리기."""
from __future__ import annotations

import os
import sqlite3
import time
from datetime import datetime, timedelta

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app import backup, i18n  # noqa: E402
from app.backup import BackupManager, list_backups  # noqa: E402
from app.database import (  # noqa: E402
    SqliteRepository, check_database, snapshot_database,
)
from app.models import Note  # noqa: E402
from app.settings import AppSettings  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _qt():
    app = QApplication.instance() or QApplication([])
    i18n.set_language("ko")
    yield app


def _settings(tmp_path, keep=10) -> AppSettings:
    s = AppSettings()
    s.backup_dir = str(tmp_path / "backups")
    s.backup_keep_count = keep
    return s


def _repo_with(tmp_path, *texts: str) -> SqliteRepository:
    repo = SqliteRepository(tmp_path / "notes.sqlite3")
    for text in texts:
        note = Note.new(user="지우")
        note.content = text
        repo.insert_note(note)
    return repo


def _contents(path) -> list[str]:
    conn = sqlite3.connect(str(path))
    try:
        return sorted(r[0] for r in conn.execute("SELECT content FROM notes"))
    finally:
        conn.close()


def _damage(path) -> None:
    """파일 머리를 뭉개 SQLite 가 열지 못하게 만든다."""
    with open(path, "r+b") as f:
        f.write(b"\x00" * 200)


def test_backup_is_a_checked_copy(tmp_path):
    repo = _repo_with(tmp_path, "하나", "둘")
    mgr = BackupManager(repo.path, _settings(tmp_path))
    made, failed = [], []
    mgr.backupCreated.connect(made.append)
    mgr.backupFailed.connect(failed.append)

    dest = mgr.run_backup()

    assert dest is not None and dest.exists() and made == [str(dest)] and not failed
    assert check_database(dest) == ""
    assert _contents(dest) == ["둘", "하나"]
    # 뜨다 만 파일을 남기지 않는다.
    assert [p.name for p in dest.parent.iterdir()] == [dest.name]
    mgr.stop()
    repo.close()


def test_damaged_source_keeps_the_good_backup(tmp_path):
    repo = _repo_with(tmp_path, "살아 있는 메모")
    mgr = BackupManager(repo.path, _settings(tmp_path, keep=1))
    good = mgr.run_backup()
    repo.close()
    _damage(tmp_path / "notes.sqlite3")
    failed = []
    mgr.backupFailed.connect(failed.append)

    assert mgr.run_backup() is None

    # 상한 원본으로 뜬 것은 버리고, 하나뿐인 멀쩡한 백업을 밀어내지 않는다.
    assert failed == [i18n.t("backup.err_damaged")]
    assert list_backups(good.parent) == [good]
    assert _contents(good) == ["살아 있는 메모"]
    mgr.stop()


def test_unreachable_folder_is_not_reported_as_damage(tmp_path):
    repo = _repo_with(tmp_path, "메모")
    s = _settings(tmp_path)
    blocker = tmp_path / "file-not-folder"
    blocker.write_text("x")
    s.backup_dir = str(blocker / "sub")      # 만들 수 없는 폴더
    mgr = BackupManager(repo.path, s)
    failed = []
    mgr.backupFailed.connect(failed.append)

    assert mgr.run_backup() is None
    assert failed and failed[0] != i18n.t("backup.err_damaged")
    mgr.stop()
    repo.close()


def test_old_backups_are_trimmed_by_the_time_in_their_names(tmp_path):
    repo = _repo_with(tmp_path, "메모")
    s = _settings(tmp_path, keep=2)
    folder = tmp_path / "backups"
    folder.mkdir()
    # 이름으로는 가장 오래됐지만 고친 시각은 가장 새로운 파일. 이름을 따라야 한다.
    names = ["notes_20260101_090000.sqlite3", "notes_20260201_090000.sqlite3",
             "notes_20260301_090000.sqlite3"]
    for i, name in enumerate(names):
        snapshot_database(repo.path, folder / name)
        stamp = time.time() - i * 3600
        os.utime(folder / name, (stamp, stamp))
    (folder / "notes_20260401_090000.sqlite3.part").write_bytes(b"half")
    mgr = BackupManager(repo.path, s)

    newest = mgr.run_backup()

    kept = [p.name for p in list_backups(folder)]
    assert kept == [newest.name, "notes_20260301_090000.sqlite3"]
    # 뜨다 만 파일은 백업으로 치지 않는다(지우지도 않는다).
    assert (folder / "notes_20260401_090000.sqlite3.part").exists()
    mgr.stop()
    repo.close()


def test_restart_does_not_back_up_again(tmp_path):
    repo = _repo_with(tmp_path, "메모")
    s = _settings(tmp_path)
    s.backup_interval_value, s.backup_interval_unit = 24, "hours"
    first = BackupManager(repo.path, s)
    first.run_backup()
    first.stop()

    again = BackupManager(repo.path, s)     # 앱을 껐다 켠 것과 같다
    again._tick()

    assert len(list_backups(tmp_path / "backups")) == 1
    again.stop()
    repo.close()


def test_unchanged_notes_are_not_backed_up_again(tmp_path):
    repo = _repo_with(tmp_path, "메모")
    s = _settings(tmp_path)
    s.backup_interval_value, s.backup_interval_unit = 1, "minutes"
    mgr = BackupManager(repo.path, s)
    mgr.run_backup()
    folder = tmp_path / "backups"
    long_ago = time.time() - 3600
    os.utime(repo.path, (long_ago, long_ago))     # 한 시간 전에 마지막으로 바뀌었다

    mgr._last_backup_ts = time.time() - 600       # 백업한 지 10분이 지났다
    mgr._tick()
    assert len(list_backups(folder)) == 1          # 바뀐 것이 없어 뜨지 않는다

    note = Note.new(user="지우")
    note.content = "새로 쓴 메모"
    repo.insert_note(note)                         # 파일이 바뀌었다
    mgr._last_backup_ts = time.time() - 600
    mgr._tick()
    backups = list_backups(folder)
    assert len(backups) == 2 and "새로 쓴 메모" in _contents(backups[0])
    mgr.stop()
    repo.close()


def test_failed_backup_waits_before_retrying(tmp_path):
    repo = _repo_with(tmp_path, "메모")
    s = _settings(tmp_path)
    blocker = tmp_path / "file-not-folder"
    blocker.write_text("x")
    s.backup_dir = str(blocker / "sub")
    s.backup_interval_value, s.backup_interval_unit = 1, "minutes"
    mgr = BackupManager(repo.path, s)
    failed = []
    mgr.backupFailed.connect(failed.append)

    mgr._tick()
    mgr._tick()        # 1분 뒤의 다음 틱. 바로 다시 붙어 보지 않는다.

    assert len(failed) == 1
    mgr.stop()
    repo.close()


def test_same_second_backups_get_different_names(tmp_path):
    repo = _repo_with(tmp_path, "메모")
    mgr = BackupManager(repo.path, _settings(tmp_path))
    a, b = mgr.run_backup(), mgr.run_backup()
    assert a != b and a.exists() and b.exists()
    mgr.stop()
    repo.close()


def test_backup_time_reads_the_name(tmp_path):
    p = tmp_path / "notes_20261119_103000.sqlite3"
    p.write_bytes(b"")
    assert backup.backup_time(p) == datetime(2026, 11, 19, 10, 30).timestamp()


# ── 되살리기 ────────────────────────────────────────────────────────────

def test_restore_replaces_the_open_database(tmp_path):
    repo = _repo_with(tmp_path, "백업에 있던 메모")
    mgr = BackupManager(repo.path, _settings(tmp_path))
    saved = mgr.run_backup()
    host_id = repo.host_id()
    extra = Note.new(user="지우")
    extra.content = "백업 뒤에 쓴 메모"
    repo.insert_note(extra)

    kept = mgr.keep_current()
    repo.restore_from(saved)

    assert [n.content for n in repo.list_notes()] == ["백업에 있던 메모"]
    # 되살린 뒤에는 다른 메모 묶음으로 보인다. 참여한 PC 는 백업 뒤에 쓴 메모가
    # 든 사본을 지우지 않고 따로 남겨 둔다.
    renewed = repo.host_id()
    assert renewed and renewed != host_id and repo.host_id() == renewed
    # 되살리기 직전의 내용은 옆에 남아 있다.
    assert _contents(kept) == ["백업 뒤에 쓴 메모", "백업에 있던 메모"]
    # 이어서 평소처럼 쓸 수 있다.
    more = Note.new(user="지우")
    repo.insert_note(more)
    assert len(repo.list_notes()) == 2
    mgr.stop()
    repo.close()


def test_restore_from_an_old_version_backup_fills_missing_columns(tmp_path):
    old = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(str(old))
    conn.executescript("""
        CREATE TABLE notes (
          id TEXT PRIMARY KEY, content TEXT NOT NULL DEFAULT '', color TEXT NOT NULL,
          width INTEGER NOT NULL DEFAULT 240, height INTEGER NOT NULL DEFAULT 200,
          created_by TEXT NOT NULL, created_at INTEGER NOT NULL,
          updated_by TEXT NOT NULL, updated_at INTEGER NOT NULL,
          version INTEGER NOT NULL DEFAULT 1, deleted INTEGER NOT NULL DEFAULT 0);
        INSERT INTO notes (id, content, color, created_by, created_at, updated_by, updated_at)
          VALUES ('a', '옛 메모', '#FFE066', '지우', 1, '지우', 1);
    """)
    conn.commit()
    conn.close()
    repo = _repo_with(tmp_path, "지금 메모")

    repo.restore_from(old)

    notes = repo.list_notes()
    assert [n.content for n in notes] == ["옛 메모"]
    assert notes[0].folder_id is None and notes[0].private_owner is None
    assert repo.list_folders() == [] and repo.list_history() == []
    assert repo.host_id()                  # 옛 백업에는 없던 식별값을 새로 만든다
    repo.close()


def test_damaged_file_is_set_aside_and_replaced_by_the_backup(tmp_path):
    repo = _repo_with(tmp_path, "되살릴 메모")
    mgr = BackupManager(repo.path, _settings(tmp_path))
    mgr.run_backup()
    mgr.stop()
    repo.close()
    db = tmp_path / "notes.sqlite3"
    _damage(db)
    with pytest.raises(sqlite3.DatabaseError):
        SqliteRepository(db)
    journal = tmp_path / "notes.sqlite3-journal"
    journal.write_bytes(b"leftover")

    good = backup.latest_good_backup(tmp_path / "backups")
    kept = backup.replace_database(db, good)

    # 상한 파일은 지우지 않고 옮겨 둔다. 쓰다 만 기록도 함께 옮겨, 되살린
    # 파일에 섞여 들어가지 않게 한다.
    assert kept.exists() and kept.name.startswith("notes.damaged-")
    assert not journal.exists()
    assert kept.with_name(kept.name + "-journal").read_bytes() == b"leftover"
    reopened = SqliteRepository(db)
    assert [n.content for n in reopened.list_notes()] == ["되살릴 메모"]
    assert reopened.check() == ""
    reopened.close()


def test_latest_good_backup_skips_damaged_ones(tmp_path):
    repo = _repo_with(tmp_path, "메모")
    folder = tmp_path / "backups"
    folder.mkdir()
    now = datetime.now()
    older = folder / f"notes_{(now - timedelta(days=1)).strftime('%Y%m%d_%H%M%S')}.sqlite3"
    newer = folder / f"notes_{now.strftime('%Y%m%d_%H%M%S')}.sqlite3"
    snapshot_database(repo.path, older)
    snapshot_database(repo.path, newer)
    _damage(newer)
    repo.close()

    assert backup.latest_good_backup(folder) == older
    assert backup.latest_good_backup(tmp_path / "nowhere") is None


def test_checking_a_missing_file_does_not_create_it(tmp_path):
    missing = tmp_path / "nope.sqlite3"
    assert check_database(missing) != ""
    assert not missing.exists()
    other = tmp_path / "other.sqlite3"
    conn = sqlite3.connect(str(other))
    conn.execute("CREATE TABLE something (x)")
    conn.commit()
    conn.close()
    assert check_database(other) == "no notes table"


def test_host_id_stays_with_the_file(tmp_path):
    repo = _repo_with(tmp_path, "메모")
    first = repo.host_id()
    assert first and repo.host_id() == first
    repo.close()
    again = SqliteRepository(tmp_path / "notes.sqlite3")
    assert again.host_id() == first
    again.close()
    fresh = SqliteRepository(tmp_path / "fresh.sqlite3")
    assert fresh.host_id() != first       # 새로 만든 메모 파일은 다른 묶음이다
    fresh.close()


# ── 설정 ────────────────────────────────────────────────────────────────

def test_backup_turns_on_once_for_settings_from_older_versions(tmp_path):
    import json
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"backup_enabled": False, "default_font_size": 13}),
                    encoding="utf-8")
    loaded = AppSettings.load(path)
    assert loaded.backup_enabled is True and loaded.default_font_size == 13

    loaded.backup_enabled = False          # 사용자가 다시 껐다
    loaded.save(path)
    assert AppSettings.load(path).backup_enabled is False

    assert AppSettings.load(tmp_path / "none.json").backup_enabled is True
