from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import Folder, HistoryEntry, Note, new_id, now_ms
from .reminders import shared_from_dict

_SCHEMA_TABLES = """
CREATE TABLE IF NOT EXISTS notes (
  id            TEXT PRIMARY KEY,
  content       TEXT NOT NULL DEFAULT '',
  color         TEXT NOT NULL,
  width         INTEGER NOT NULL DEFAULT 240,
  height        INTEGER NOT NULL DEFAULT 200,
  created_by    TEXT NOT NULL,
  created_at    INTEGER NOT NULL,
  updated_by    TEXT NOT NULL,
  updated_at    INTEGER NOT NULL,
  version       INTEGER NOT NULL DEFAULT 1,
  deleted       INTEGER NOT NULL DEFAULT 0,
  folder_id     TEXT,
  private_owner TEXT
);

CREATE TABLE IF NOT EXISTS folders (
  id            TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  color         TEXT NOT NULL DEFAULT '#FFE066',
  created_by    TEXT NOT NULL,
  created_at    INTEGER NOT NULL,
  updated_at    INTEGER NOT NULL,
  private_owner TEXT,
  deleted       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS note_history (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  note_id        TEXT NOT NULL,
  user           TEXT NOT NULL,
  action         TEXT NOT NULL,
  content_before TEXT,
  content_after  TEXT,
  color_before   TEXT,
  color_after    TEXT,
  session_start  INTEGER NOT NULL,
  session_end    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trash (
  note_id    TEXT PRIMARY KEY,
  deleted_at INTEGER NOT NULL,
  deleted_by TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS note_reminders (
  note_id TEXT PRIMARY KEY,
  data    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS note_notices (
  note_id TEXT PRIMARY KEY,
  owner   TEXT NOT NULL,
  set_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS note_seen (
  note_id TEXT NOT NULL,
  user    TEXT NOT NULL,
  version INTEGER NOT NULL,
  seen_at INTEGER NOT NULL,
  PRIMARY KEY (note_id, user)
);

CREATE TABLE IF NOT EXISTS note_widgets (
  widget_id  TEXT PRIMARY KEY,
  note_id    TEXT NOT NULL,
  data       TEXT NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS participants (
  name      TEXT PRIMARY KEY,
  last_seen INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS note_mentions (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  user    TEXT NOT NULL,
  note_id TEXT NOT NULL,
  by_user TEXT NOT NULL,
  at      INTEGER NOT NULL
);
"""
# 휴지통은 표를 따로 둔다. notes 에 열을 더하면 옛 버전이 그 파일을 읽다가
# 죽는다(모르는 열을 Note 에 그대로 넘긴다). 표가 하나 더 있는 것은 옛 버전이
# 보지 않는다. 휴지통에 든 메모는 notes 에 deleted = 1 로 남아 있고, 이 표에는
# 언제 누가 버렸는지만 적는다. 이 표에 없는 지운 메모(이 기능이 생기기 전에 지운
# 것, 옛 버전이 지운 것)는 휴지통에 보이지 않고 저절로 지워지지도 않는다.
# 공유 알림(note_reminders)도 같은 까닭으로 표를 따로 둔다. 한 줄에 알림 하나를
# JSON 으로 적는다(reminders.shared_from_dict). 메모를 휴지통에 넣어도 남고,
# 휴지통에서 지울 때 함께 지운다.
# 공지(note_notices), 공지를 본 기록(note_seen), 아직 전하지 못한 @이름 알림
# (note_mentions)도 같다(1.2). 옛 버전은 이 표들을 보지 않는다.

# 인덱스를 테이블 생성과 분리해 둔다.
# 구버전 notes 테이블에는 folder_id 컬럼이 없어,
# _migrate 전에 그 컬럼을 참조하는 인덱스를 만들면 실패한다.
# 그래서 '테이블 → _migrate → 인덱스' 순서를 지켜야 한다.
_SCHEMA_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_folders_owner ON folders(private_owner, deleted);
CREATE INDEX IF NOT EXISTS idx_notes_folder ON notes(folder_id, deleted);
CREATE INDEX IF NOT EXISTS idx_hist_note ON note_history(note_id, session_end DESC);
"""


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def _migrate(conn: sqlite3.Connection) -> None:
    """첫 릴리스 이후 추가된 컬럼들을 구버전 DB에 채워 넣는다."""
    cols = _table_columns(conn, "notes")
    if "folder_id" not in cols:
        conn.execute("ALTER TABLE notes ADD COLUMN folder_id TEXT")
    if "private_owner" not in cols:
        conn.execute("ALTER TABLE notes ADD COLUMN private_owner TEXT")
    conn.commit()


class _Sentinel:
    """'인자를 안 넘긴 경우'와 '명시적으로 None을 넘긴 경우'를 구분한다."""


_UNCHANGED = _Sentinel()


def _open_readonly(path: Path) -> sqlite3.Connection:
    """있는 파일을 읽기만 하려고 연다.

    없는 경로를 그냥 열면 SQLite 가 빈 파일을 만들어 버리므로 먼저 확인한다.
    URI(``mode=ro``)는 공유 폴더 경로(\\\\서버\\폴더)에서 열리지 않아 쓰지 않는다."""
    if not path.is_file():
        raise FileNotFoundError(str(path))
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA query_only = ON")
    return conn


def check_database(path: Path | str, table: str = "notes") -> str:
    """메모 파일(또는 그 백업)이 온전한지 본다. 온전하면 빈 문자열, 아니면 까닭.

    ``table`` 은 그 파일에 꼭 있어야 하는 표다."""
    path = Path(path)
    try:
        conn = _open_readonly(path)
    except (OSError, sqlite3.Error) as e:
        return str(e) or type(e).__name__
    try:
        rows = conn.execute("PRAGMA quick_check").fetchall()
        result = "; ".join(str(r[0]) for r in rows)
        if result != "ok":
            return result or "quick_check failed"
        if table not in {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'")}:
            return f"no {table} table"
        return ""
    except sqlite3.Error as e:
        return str(e) or type(e).__name__
    finally:
        conn.close()


def snapshot_database(source: Path | str, dest: Path | str) -> None:
    """``source`` 의 지금 내용을 ``dest`` 에 새 파일로 떠 둔다.

    파일을 그대로 베끼지 않고 SQLite 의 백업 기능으로 뜬다. 쓰는 도중에 떠도
    한 시점의 온전한 내용이 담긴다."""
    src = _open_readonly(Path(source))
    try:
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


class SqliteRepository:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        try:
            self._conn.row_factory = sqlite3.Row
            self._prepare_schema()
        except sqlite3.Error:
            # 열다가 실패했으면 파일을 붙들고 있지 않는다(부르는 쪽이 옮겨 둘 수 있게).
            self._conn.close()
            raise

    def _prepare_schema(self) -> None:
        self._conn.executescript(_SCHEMA_TABLES)
        self._conn.commit()
        _migrate(self._conn)
        self._conn.executescript(_SCHEMA_INDEXES)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def check(self) -> str:
        """열려 있는 메모 파일이 온전한지 본다. 온전하면 빈 문자열.

        잠겨 있다, 읽을 수 없다 같은 일(OperationalError)은 상한 것이 아니므로
        그대로 올려 보낸다."""
        try:
            rows = self._conn.execute("PRAGMA quick_check").fetchall()
        except sqlite3.OperationalError:
            raise
        except sqlite3.DatabaseError as e:
            return str(e) or type(e).__name__
        result = "; ".join(str(r[0]) for r in rows)
        return "" if result == "ok" else (result or "quick_check failed")

    def host_id(self) -> str:
        """이 메모 묶음의 식별값. 처음 물을 때 만들어 파일 안에 적어 둔다.

        파일과 함께 다니므로 백업에서 되살려도 같은 값이고, 메모 파일을 새로
        만들면 다른 값이 된다. 참여한 PC 는 이 값으로 '같은 주소지만 다른 메모
        묶음'을 알아보고, 가지고 있던 사본을 덮어쓰지 않는다."""
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = 'host_id'").fetchone()
        if row and row[0]:
            return str(row[0])
        return self.renew_host_id()

    def renew_host_id(self) -> str:
        """식별값을 새로 매긴다. 백업에서 되살린 뒤에 부른다.

        되살린 내용은 백업을 뜬 뒤에 쓴 메모가 빠진 옛 상태다. 같은 묶음으로
        두면 참여한 PC 의 사본이 그 옛 상태를 따라가며 빠진 메모를 지운다.
        다른 묶음으로 보이게 해서, 되살리기 전의 사본을 지우지 않고 따로
        남겨 두게 한다."""
        value = new_id()
        self._conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('host_id', ?)",
            (value,))
        self._conn.commit()
        return value

    def took_over_from(self) -> str:
        """이 메모 묶음이 이어받은 공유의 식별값(사본으로 공유를 이어받았을 때).

        그 공유에 참여하던 PC 가 이 PC 를 찾았을 때 '공유를 이어받은 PC'임을
        알아본다. 이어받은 것이 아니면 빈 글."""
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = 'took_over_from'").fetchone()
        return str(row[0]) if row and row[0] else ""

    def set_took_over_from(self, host_id: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('took_over_from', ?)",
            (str(host_id or ""),))
        self._conn.commit()

    def restore_from(self, source: Path | str) -> None:
        """백업 파일의 내용으로 통째로 바꾼다(열려 있는 연결 그대로).

        SQLite 가 한 번의 쓰기로 처리하므로 도중에 실패하면 원래 내용이 남는다.
        옛 버전에서 만든 백업일 수 있어 끝나면 빠진 표와 열을 다시 채운다."""
        src = _open_readonly(Path(source))
        try:
            src.backup(self._conn)
        finally:
            src.close()
        self._prepare_schema()
        self.renew_host_id()

    def list_notes(self, include_deleted: bool = False) -> list[Note]:
        q = "SELECT * FROM notes"
        if not include_deleted:
            q += " WHERE deleted = 0"
        q += " ORDER BY created_at ASC"
        rows = self._conn.execute(q).fetchall()
        return [Note(**dict(r)) for r in rows]

    def get_note(self, note_id: str) -> Note | None:
        row = self._conn.execute(
            "SELECT * FROM notes WHERE id = ?", (note_id,)
        ).fetchone()
        return Note(**dict(row)) if row else None

    def insert_note(self, note: Note) -> None:
        self._conn.execute(
            """INSERT INTO notes
               (id, content, color, width, height, created_by, created_at,
                updated_by, updated_at, version, deleted, folder_id,
                private_owner)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                note.id, note.content, note.color, note.width, note.height,
                note.created_by, note.created_at, note.updated_by, note.updated_at,
                note.version, note.deleted, note.folder_id, note.private_owner,
            ),
        )
        self._conn.commit()

    def update_note(
        self, note_id: str, content: str, color: str, width: int, height: int,
        user: str, expected_version: int | None = None,
    ) -> Note | None:
        existing = self.get_note(note_id)
        if existing is None or existing.deleted:
            return None
        if expected_version is not None and existing.version != expected_version:
            return None
        new_version = existing.version + 1
        t = now_ms()
        self._conn.execute(
            """UPDATE notes SET content = ?, color = ?, width = ?, height = ?,
                                updated_by = ?, updated_at = ?, version = ?
               WHERE id = ?""",
            (content, color, width, height, user, t, new_version, note_id),
        )
        self._conn.commit()
        return self.get_note(note_id)

    def soft_delete_note(self, note_id: str, user: str) -> Note | None:
        existing = self.get_note(note_id)
        if existing is None or existing.deleted:
            return None
        t = now_ms()
        self._conn.execute(
            """UPDATE notes SET deleted = 1, updated_by = ?, updated_at = ?,
                                version = version + 1
               WHERE id = ?""",
            (user, t, note_id),
        )
        self._conn.commit()
        return self.get_note(note_id)

    # ── 휴지통 ──────────────────────────────────────────────────────────

    def trash_note(self, note_id: str, user: str) -> Note | None:
        """메모를 휴지통으로 옮긴다(지운 것으로 표시하고 언제 누가 버렸는지 적는다)."""
        note = self.soft_delete_note(note_id, user)
        if note is None:
            return None
        self._conn.execute(
            "INSERT OR REPLACE INTO trash (note_id, deleted_at, deleted_by)"
            " VALUES (?, ?, ?)", (note_id, now_ms(), user or ""))
        self._conn.commit()
        return note

    def list_trash(self) -> list[tuple[Note, int, str]]:
        """휴지통에 든 메모와 버린 때, 버린 사람(방금 버린 것부터)."""
        rows = self._conn.execute(
            "SELECT n.*, t.deleted_at AS trash_at, t.deleted_by AS trash_by"
            " FROM trash t JOIN notes n ON n.id = t.note_id"
            " WHERE n.deleted = 1 ORDER BY t.deleted_at DESC").fetchall()
        out: list[tuple[Note, int, str]] = []
        for row in rows:
            data = dict(row)
            at, by = int(data.pop("trash_at")), str(data.pop("trash_by") or "")
            out.append((Note(**data), at, by))
        return out

    def in_trash(self, note_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM trash t JOIN notes n ON n.id = t.note_id"
            " WHERE t.note_id = ? AND n.deleted = 1", (note_id,)).fetchone()
        return row is not None

    def restore_note(self, note_id: str, user: str) -> Note | None:
        """휴지통의 메모를 되살린다. 들어 있던 폴더가 그사이 지워졌으면 미분류로
        돌아온다."""
        if not self.in_trash(note_id):
            return None
        note = self.get_note(note_id)
        folder_id = note.folder_id if note is not None else None
        if folder_id:
            folder = self.get_folder(folder_id)
            if folder is None or folder.deleted:
                folder_id = None
        self._conn.execute(
            """UPDATE notes SET deleted = 0, folder_id = ?, updated_by = ?,
                                updated_at = ?, version = version + 1
               WHERE id = ? AND deleted = 1""",
            (folder_id, user, now_ms(), note_id))
        self._conn.execute("DELETE FROM trash WHERE note_id = ?", (note_id,))
        self._conn.commit()
        return self.get_note(note_id)

    def purge_note(self, note_id: str) -> bool:
        """휴지통의 메모를 메모 파일에서 지운다(이력도 함께). 되돌릴 수 없다.

        휴지통에 든 것만 지운다. 살아 있는 메모는 어떤 경우에도 지우지 않는다."""
        if not self.in_trash(note_id):
            return False
        self._conn.execute(
            "DELETE FROM note_history WHERE note_id = ?", (note_id,))
        self._conn.execute(
            "DELETE FROM notes WHERE id = ? AND deleted = 1", (note_id,))
        self._conn.execute("DELETE FROM trash WHERE note_id = ?", (note_id,))
        self._conn.execute("DELETE FROM note_reminders WHERE note_id = ?", (note_id,))
        for table in ("note_notices", "note_seen", "note_mentions", "note_widgets"):
            self._conn.execute(f"DELETE FROM {table} WHERE note_id = ?", (note_id,))
        self._conn.commit()
        return True

    # ── 공유 알림 ──

    def list_reminders(self) -> dict[str, dict]:
        """메모 id 에서 공유 알림으로. 읽을 수 없는 줄은 건너뛴다."""
        out: dict[str, dict] = {}
        for row in self._conn.execute("SELECT note_id, data FROM note_reminders"):
            try:
                entry = shared_from_dict(json.loads(row[1]))
            except (TypeError, ValueError):
                continue
            if entry is not None:
                out[str(row[0])] = entry
        return out

    def set_reminder(self, note_id: str, entry: dict) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO note_reminders (note_id, data) VALUES (?, ?)",
            (note_id, json.dumps(entry, ensure_ascii=False)))
        self._conn.commit()

    def delete_reminder(self, note_id: str) -> bool:
        cur = self._conn.execute(
            "DELETE FROM note_reminders WHERE note_id = ?", (note_id,))
        self._conn.commit()
        return cur.rowcount > 0

    # ── 공지, 읽음, @이름 (1.2) ──

    def list_notices(self) -> dict[str, str]:
        """공지인 메모 id 에서 만든 사람으로."""
        return {str(r[0]): str(r[1]) for r in
                self._conn.execute("SELECT note_id, owner FROM note_notices")}

    def set_notice(self, note_id: str, owner: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO note_notices (note_id, owner, set_at) VALUES (?, ?, ?)",
            (note_id, owner, now_ms()))
        self._conn.commit()

    def delete_notice(self, note_id: str) -> bool:
        cur = self._conn.execute("DELETE FROM note_notices WHERE note_id = ?", (note_id,))
        self._conn.execute("DELETE FROM note_seen WHERE note_id = ?", (note_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def set_seen(self, note_id: str, user: str, version: int) -> None:
        """그 사람이 그 판(version)까지 봤다. 더 옛 판으로 돌려 적지 않는다."""
        row = self._conn.execute(
            "SELECT version FROM note_seen WHERE note_id = ? AND user = ?",
            (note_id, user)).fetchone()
        if row is not None and int(row[0]) >= version:
            return
        self._conn.execute(
            "INSERT OR REPLACE INTO note_seen (note_id, user, version, seen_at) "
            "VALUES (?, ?, ?, ?)", (note_id, user, int(version), now_ms()))
        self._conn.commit()

    def list_seen(self, note_id: str) -> dict[str, dict]:
        return {str(r[0]): {"version": int(r[1]), "at": int(r[2])} for r in
                self._conn.execute(
                    "SELECT user, version, seen_at FROM note_seen WHERE note_id = ?",
                    (note_id,))}

    def list_widgets(self) -> dict[str, tuple[str, dict]]:
        """위젯 id → (메모 id, 데이터). 읽을 수 없는 줄은 건너뛴다."""
        out: dict[str, tuple[str, dict]] = {}
        for row in self._conn.execute("SELECT widget_id, note_id, data FROM note_widgets"):
            try:
                data = json.loads(row[2])
            except (TypeError, ValueError):
                continue
            if isinstance(data, dict):
                out[str(row[0])] = (str(row[1]), data)
        return out

    def get_widget(self, widget_id: str) -> tuple[str, dict] | None:
        row = self._conn.execute(
            "SELECT note_id, data FROM note_widgets WHERE widget_id = ?", (widget_id,)).fetchone()
        if row is None:
            return None
        try:
            data = json.loads(row[1])
        except (TypeError, ValueError):
            return None
        return (str(row[0]), data) if isinstance(data, dict) else None

    def count_widgets(self, note_id: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM note_widgets WHERE note_id = ?", (note_id,)).fetchone()
        return int(row[0]) if row else 0

    def put_widget(self, widget_id: str, note_id: str, data: dict) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO note_widgets (widget_id, note_id, data, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (widget_id, note_id, json.dumps(data, ensure_ascii=False), now_ms()))
        self._conn.commit()

    def touch_participant(self, name: str) -> None:
        """이 공유에 붙은 적이 있는 사람(이름 부르기와 이름 고르기에 쓴다)."""
        if name:
            self._conn.execute(
                "INSERT OR REPLACE INTO participants (name, last_seen) VALUES (?, ?)",
                (name, now_ms()))
            self._conn.commit()

    def list_authors(self) -> set[str]:
        """메모를 만들거나 고친 사람(본문은 읽지 않는다)."""
        rows = self._conn.execute(
            "SELECT created_by FROM notes UNION SELECT updated_by FROM notes")
        return {str(r[0]) for r in rows if r[0]}

    def list_participants(self) -> list[str]:
        return [str(r[0]) for r in self._conn.execute(
            "SELECT name FROM participants ORDER BY last_seen DESC")]

    def add_mention(self, user: str, note_id: str, by: str) -> None:
        self._conn.execute(
            "INSERT INTO note_mentions (user, note_id, by_user, at) VALUES (?, ?, ?, ?)",
            (user, note_id, by, now_ms()))
        # 한 사람에게 쌓이는 알림은 최근 50개까지.
        self._conn.execute(
            "DELETE FROM note_mentions WHERE user = ? AND id NOT IN ("
            "SELECT id FROM note_mentions WHERE user = ? ORDER BY id DESC LIMIT 50)",
            (user, user))
        self._conn.commit()

    def take_mentions(self, user: str) -> list[dict]:
        """그 사람에게 아직 전하지 못한 알림을 꺼낸다(꺼낸 것은 지운다)."""
        rows = self._conn.execute(
            "SELECT note_id, by_user, at FROM note_mentions WHERE user = ? ORDER BY id",
            (user,)).fetchall()
        if rows:
            self._conn.execute("DELETE FROM note_mentions WHERE user = ?", (user,))
            self._conn.commit()
        return [{"note_id": str(r[0]), "by": str(r[1]), "at": int(r[2])} for r in rows]

    def purge_trash(self, before_ms: int) -> list[str]:
        """``before_ms`` 보다 먼저 버린 메모를 메모 파일에서 지운다. 지운 메모의 id."""
        # 되살아난 메모의 낡은 줄(옛 버전이 이 파일을 쓰는 동안 생길 수 있다)을
        # 먼저 걷어 낸다. 그런 줄을 믿고 살아 있는 메모를 지우면 안 된다.
        self._conn.execute(
            "DELETE FROM trash WHERE note_id NOT IN"
            " (SELECT id FROM notes WHERE deleted = 1)")
        self._conn.commit()
        ids = [str(r[0]) for r in self._conn.execute(
            "SELECT note_id FROM trash WHERE deleted_at < ?",
            (int(before_ms),)).fetchall()]
        return [nid for nid in ids if self.purge_note(nid)]

    def set_note_folder(self, note_id: str, folder_id: str | None,
                        user: str) -> Note | None:
        existing = self.get_note(note_id)
        if existing is None or existing.deleted:
            return None
        t = now_ms()
        self._conn.execute(
            """UPDATE notes SET folder_id = ?, updated_by = ?, updated_at = ?,
                                version = version + 1
               WHERE id = ?""",
            (folder_id, user, t, note_id),
        )
        self._conn.commit()
        return self.get_note(note_id)

    def set_note_private_owner(self, note_id: str,
                                private_owner: str | None,
                                user: str) -> Note | None:
        existing = self.get_note(note_id)
        if existing is None or existing.deleted:
            return None
        t = now_ms()
        self._conn.execute(
            """UPDATE notes SET private_owner = ?, updated_by = ?,
                                updated_at = ?, version = version + 1
               WHERE id = ?""",
            (private_owner, user, t, note_id),
        )
        self._conn.commit()
        return self.get_note(note_id)

    def list_folders(self, include_deleted: bool = False) -> list[Folder]:
        q = "SELECT * FROM folders"
        if not include_deleted:
            q += " WHERE deleted = 0"
        q += " ORDER BY created_at ASC"
        rows = self._conn.execute(q).fetchall()
        return [Folder(**dict(r)) for r in rows]

    def get_folder(self, folder_id: str) -> Folder | None:
        row = self._conn.execute(
            "SELECT * FROM folders WHERE id = ?", (folder_id,)
        ).fetchone()
        return Folder(**dict(row)) if row else None

    def insert_folder(self, folder: Folder) -> None:
        self._conn.execute(
            """INSERT INTO folders
               (id, name, color, created_by, created_at, updated_at,
                private_owner, deleted)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                folder.id, folder.name, folder.color, folder.created_by,
                folder.created_at, folder.updated_at, folder.private_owner,
                folder.deleted,
            ),
        )
        self._conn.commit()

    def update_folder(self, folder_id: str, *, name: str | None = None,
                       color: str | None = None,
                       private_owner=_UNCHANGED) -> Folder | None:
        existing = self.get_folder(folder_id)
        if existing is None or existing.deleted:
            return None
        new_name = existing.name if name is None else name
        new_color = existing.color if color is None else color
        if private_owner is _UNCHANGED:
            new_owner = existing.private_owner
        else:
            new_owner = private_owner
        t = now_ms()
        self._conn.execute(
            """UPDATE folders SET name = ?, color = ?, private_owner = ?,
                                  updated_at = ?
               WHERE id = ?""",
            (new_name, new_color, new_owner, t, folder_id),
        )
        self._conn.commit()
        return self.get_folder(folder_id)

    def soft_delete_folder(self, folder_id: str) -> Folder | None:
        existing = self.get_folder(folder_id)
        if existing is None or existing.deleted:
            return None
        t = now_ms()
        self._conn.execute(
            "UPDATE folders SET deleted = 1, updated_at = ? WHERE id = ?",
            (t, folder_id),
        )
        # 폴더에 들어 있던 메모는 폴더에서 떼어내 '미분류'로 보낸다.
        self._conn.execute(
            "UPDATE notes SET folder_id = NULL, updated_at = ?"
            " WHERE folder_id = ?",
            (t, folder_id),
        )
        self._conn.commit()
        return self.get_folder(folder_id)

    def keep_deleted_notes_private(self, folder_id: str, owner: str) -> None:
        """나만 보던 폴더에서 지운 메모에 그 주인을 적어 둔다.

        그런 메모는 폴더에 기대어 비공개였다. 폴더가 지워지거나 공개되면 기댈
        곳이 없어지고, 주인이 적혀 있지 않으면 지운 글 전체가 담긴 이력을 누구나
        받아 볼 수 있게 된다."""
        self._conn.execute(
            "UPDATE notes SET private_owner = ? WHERE folder_id = ?"
            " AND deleted = 1 AND (private_owner IS NULL OR private_owner = '')",
            (owner, folder_id),
        )
        self._conn.commit()

    def list_notes_in_folder(self, folder_id: str) -> list[Note]:
        rows = self._conn.execute(
            "SELECT * FROM notes WHERE folder_id = ? AND deleted = 0"
            " ORDER BY created_at ASC",
            (folder_id,),
        ).fetchall()
        return [Note(**dict(r)) for r in rows]

    def insert_history(self, entry: HistoryEntry) -> HistoryEntry:
        cur = self._conn.execute(
            """INSERT INTO note_history
               (note_id, user, action, content_before, content_after,
                color_before, color_after, session_start, session_end)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entry.note_id, entry.user, entry.action,
                entry.content_before, entry.content_after,
                entry.color_before, entry.color_after,
                entry.session_start, entry.session_end,
            ),
        )
        self._conn.commit()
        entry.id = cur.lastrowid or 0
        return entry

    def list_history(self, note_id: str | None = None) -> list[HistoryEntry]:
        if note_id is None:
            rows = self._conn.execute(
                "SELECT * FROM note_history ORDER BY session_end DESC"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM note_history WHERE note_id = ?"
                " ORDER BY session_end DESC",
                (note_id,),
            ).fetchall()
        return [HistoryEntry(**dict(r)) for r in rows]
