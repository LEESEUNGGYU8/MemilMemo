"""참여한 PC 에 두는 메모 사본.

메모의 원본은 공유를 시작한 PC 한 곳에 있다. 그 PC 가 고장 나면 메모가 모두
사라지므로, 참여한 PC 마다 받은 메모를 그대로 적어 둔다. 담는 것은 호스트가
이 PC 에 보내 준 것, 곧 공개 메모와 내 '나만 보기' 메모뿐이다. 남의 비공개
메모는 애초에 오지 않으므로 사본에도 없다.

사본은 호스트가 꺼져 있을 때 읽기 전용으로 보여 주는 데 쓴다.

가장 조심하는 일은 **멀쩡한 사본을 빈 내용으로 덮어쓰는 것**이다. 호스트의
디스크가 나가 메모 파일이 새로 만들어지면, 같은 주소로 다시 붙은 게스트는
메모가 하나도 없는 첫 상태를 받는다. 그대로 따라 지우면 마지막 남은 사본까지
없어진다. 그래서 받은 것이 다른 메모 묶음이면(식별값이 다르거나, 겹치는 메모가
하나도 없으면) 가지고 있던 사본을 지우지 않고 다른 이름으로 옮겨 둔다."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject

from .models import Folder, Note
from .network.client_base import ClientBase

_SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
  id         TEXT PRIMARY KEY,
  version    INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER NOT NULL DEFAULT 0,
  data       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS folders (
  id   TEXT PRIMARY KEY,
  data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""

# 옮겨 둔 옛 사본을 몇 벌까지 남길지.
KEEP_ARCHIVES = 5
_STAMP = "%Y%m%d_%H%M%S"


def _stamp(note: dict[str, Any]) -> tuple[int, int]:
    return int(note.get("version") or 0), int(note.get("updated_at") or 0)


def archive_paths(replica_path: Path) -> list[Path]:
    """다른 메모 묶음을 받느라 옮겨 둔 옛 사본들(새것부터)."""
    replica_path = Path(replica_path)
    pattern = re.compile(
        re.escape(replica_path.stem) + r"\.archive-(\d{8}_\d{6})(?:-(\d+))?"
        + re.escape(replica_path.suffix) + "$")
    found = []
    try:
        entries = list(replica_path.parent.iterdir())
    except OSError:
        return []
    for p in entries:
        m = pattern.match(p.name)
        if m and p.is_file():
            # 같은 초에 옮긴 것은 뒤에 붙은 번호가 클수록 새것이다.
            found.append(((m.group(1), int(m.group(2) or 1)), p))
    return [p for _key, p in sorted(found, key=lambda x: x[0], reverse=True)]


@dataclass
class CopyInfo:
    """이 PC 에 있는 사본 한 벌."""
    path: Path
    current: bool        # 지금 받고 있는 사본인가(아니면 옮겨 둔 옛 사본)
    synced_at: int       # 마지막으로 받은 시각(ms)
    host: str
    nickname: str
    host_id: str
    note_count: int


def read_copy(path: Path | str) -> tuple[list[dict[str, Any]], list[dict[str, Any]],
                                         dict[str, str]]:
    """사본 파일을 읽기만 한다: (메모들, 폴더들, 딸린 정보)."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(str(path))
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA query_only = ON")

        def rows(table: str) -> list[dict[str, Any]]:
            out = []
            for (raw,) in conn.execute(f"SELECT data FROM {table}"):
                try:
                    item = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                if isinstance(item, dict) and item.get("id"):
                    out.append(item)
            return out

        meta = {str(k): str(v) for k, v in conn.execute("SELECT key, value FROM meta")}
        return rows("notes"), rows("folders"), meta
    finally:
        conn.close()


def list_copies(replica_path: Path | str) -> list[CopyInfo]:
    """이 PC 에 있는 사본들. 지금 사본이 먼저, 옮겨 둔 것은 새것부터.

    메모가 하나도 없거나 읽지 못하는 파일은 넣지 않는다."""
    replica_path = Path(replica_path)
    out: list[CopyInfo] = []
    for path, current in [(replica_path, True)] + [
            (p, False) for p in archive_paths(replica_path)]:
        try:
            notes, _folders, meta = read_copy(path)
        except (OSError, sqlite3.Error):
            continue
        if not notes:
            continue
        try:
            synced = int(meta.get("synced_at") or 0)
        except ValueError:
            synced = 0
        out.append(CopyInfo(path=path, current=current, synced_at=synced,
                            host=meta.get("host", ""),
                            nickname=meta.get("nickname", ""),
                            host_id=meta.get("host_id", ""),
                            note_count=len(notes)))
    return out


def missing_notes(copy_notes: list[dict[str, Any]],
                  host_notes: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """사본에는 있는데 지금 공유 PC 에는 없는 내용.

    공유 PC 에 그 메모가 아예 없거나, 있더라도 사본 쪽이 더 나중에 고친 다른
    내용이면 넣는다. 가져올 때는 새 메모로 만들기 때문에 있는 메모를 덮어쓰지
    않는다."""
    out = []
    for note in copy_notes:
        there = host_notes.get(note.get("id"))
        if there is None:
            out.append(note)
        elif ((note.get("content") or "") != (there.get("content") or "")
              and int(note.get("updated_at") or 0) > int(there.get("updated_at") or 0)):
            out.append(note)
    return out


def build_host_database(copy_path: Path | str, db_path: Path | str,
                        nickname: str) -> Path | None:
    """사본으로 공유 PC 의 메모 파일을 새로 만든다(이 PC 가 공유를 이어받는다).

    이 PC 에 메모 파일이 이미 있으면 지우지 않고 옆으로 옮겨 둔다. 옮겨 둔 파일의
    경로를 돌려준다. 사본을 받을 때와 닉네임이 달라졌으면 '나만 보기' 메모의
    주인을 지금 닉네임으로 바꿔, 주인이 자기 메모를 못 보게 되는 일을 막는다.
    수정 이력은 사본에 없으므로 따라오지 않는다."""
    from .backup import set_aside
    from .database import SqliteRepository

    copy_path, db_path = Path(copy_path), Path(db_path)
    notes, folders, meta = read_copy(copy_path)
    if not notes:
        raise ValueError("empty copy")
    was = meta.get("nickname", "")

    def owner(value):
        return nickname if (value and was and value == was) else (value or None)

    fresh = db_path.with_name(db_path.name + ".new")
    if fresh.exists():
        fresh.unlink()
    repo = SqliteRepository(fresh)
    try:
        folder_ids = set()
        for raw in folders:
            try:
                folder = Folder.from_dict(raw)
            except TypeError:
                continue
            folder.deleted = 0
            folder.private_owner = owner(folder.private_owner)
            repo.insert_folder(folder)
            folder_ids.add(folder.id)
        for raw in notes:
            try:
                note = Note.from_dict(raw)
            except TypeError:
                continue
            note.deleted = 0
            note.content = note.content or ""
            note.private_owner = owner(note.private_owner)
            if note.folder_id not in folder_ids:
                note.folder_id = None
            note.created_by = note.created_by or nickname
            note.updated_by = note.updated_by or note.created_by
            repo.insert_note(note)
    finally:
        repo.close()
    kept = set_aside(db_path, "before-takeover")
    os.replace(fresh, db_path)
    return kept


class ReplicaStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        try:
            self._open()
        except sqlite3.DatabaseError:
            # 사본 파일이 상했다. 지우지 않고 옮겨 둔 뒤 새로 시작한다.
            self._close_quietly()
            self._move_aside("damaged")
            self._open()

    # ── 파일 ──

    def _open(self) -> None:
        conn = sqlite3.connect(str(self.path))
        self._conn = conn
        # 사본은 자주 조금씩 쓴다(누가 글을 쓸 때마다). 쓸 때마다 디스크를
        # 기다리지 않게 하되, 전원이 나가도 파일이 상하지는 않는 방식으로 둔다.
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.executescript(_SCHEMA)
        conn.commit()
        conn.execute("SELECT count(*) FROM notes").fetchone()

    def _close_quietly(self) -> None:
        if self._conn is None:
            return
        try:
            self._conn.close()
        except sqlite3.Error:
            pass
        self._conn = None

    def close(self) -> None:
        self._close_quietly()

    def _move_aside(self, tag: str) -> Path | None:
        """지금 파일을 지우지 않고 다른 이름으로 옮긴다. 옮긴 경로를 돌려준다."""
        if not self.path.exists():
            return None
        base = f"{self.path.stem}.{tag}-{datetime.now().strftime(_STAMP)}"
        # 같은 초에 이미 옮긴 것이 있으면 번호를 하나 올린다. 지워져 빈 번호를
        # 다시 쓰지 않는다(다시 쓰면 새것이 옛것으로 보여 먼저 지워진다).
        taken = re.compile(re.escape(base) + r"(?:-(\d+))?"
                           + re.escape(self.path.suffix) + "$")
        used = [int(m.group(1) or 1) for p in self.path.parent.iterdir()
                if (m := taken.match(p.name))]
        n = max(used, default=0) + 1
        kept = self.path.with_name(
            f"{base}{self.path.suffix}" if n == 1
            else f"{base}-{n}{self.path.suffix}")
        os.replace(self.path, kept)
        for extra in ("-wal", "-shm"):
            side = self.path.with_name(self.path.name + extra)
            if side.exists():
                os.replace(side, kept.with_name(kept.name + extra))
        return kept

    def archives(self) -> list[Path]:
        """다른 메모 묶음을 받느라 옮겨 둔 옛 사본들(새것부터)."""
        return archive_paths(self.path)

    def _archive(self) -> None:
        self._close_quietly()
        self._move_aside("archive")
        for old in self.archives()[KEEP_ARCHIVES:]:
            for extra in ("", "-wal", "-shm"):
                try:
                    old.with_name(old.name + extra).unlink()
                except OSError:
                    pass
        self._open()

    # ── 읽기 ──

    def meta(self) -> dict[str, str]:
        rows = self._conn.execute("SELECT key, value FROM meta").fetchall()
        return {str(k): str(v) for k, v in rows}

    def note_ids(self) -> set[str]:
        return {r[0] for r in self._conn.execute("SELECT id FROM notes")}

    def is_empty(self) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM notes LIMIT 1").fetchone() is None

    def load(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """(메모들, 폴더들). 읽지 못하는 줄은 건너뛴다."""
        def rows(table: str) -> list[dict[str, Any]]:
            out = []
            for (raw,) in self._conn.execute(f"SELECT data FROM {table}"):
                try:
                    item = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                if isinstance(item, dict) and item.get("id"):
                    out.append(item)
            return out
        return rows("notes"), rows("folders")

    # ── 쓰기 ──

    def _is_other_dataset(self, host_id: str, incoming: set[str]) -> bool:
        """받은 첫 상태가 지금 사본과 다른 메모 묶음의 것인가."""
        mine = self.note_ids()
        if not mine:
            return False        # 잃을 것이 없다
        known = self.meta().get("host_id", "")
        if known and host_id:
            return known != host_id
        # 식별값을 모른다(옛 버전 호스트). 겹치는 메모가 하나도 없으면 다른 묶음이다.
        return not (mine & incoming)

    def apply_snapshot(self, notes: list[dict[str, Any]],
                       folders: list[dict[str, Any]], *, host_id: str = "",
                       host: str = "", address: str = "",
                       nickname: str = "") -> bool:
        """호스트가 보낸 첫 상태에 사본을 맞춘다.

        다른 메모 묶음이면 지금 사본을 옮겨 두고 새로 시작한다(그때 True)."""
        notes = [n for n in notes if isinstance(n, dict) and n.get("id")]
        folders = [f for f in folders if isinstance(f, dict) and f.get("id")]
        incoming = {n["id"] for n in notes}
        archived = self._is_other_dataset(host_id, incoming)
        if archived:
            self._archive()
        c = self._conn
        have = {r[0]: (int(r[1]), int(r[2])) for r in c.execute(
            "SELECT id, version, updated_at FROM notes")}
        with c:
            for n in notes:
                # 바뀐 메모만 다시 쓴다(그림이 든 메모는 몇 MB 씩 된다).
                if have.get(n["id"]) != _stamp(n):
                    self._put_note(n)
            gone = [(nid,) for nid in have if nid not in incoming]
            c.executemany("DELETE FROM notes WHERE id = ?", gone)
            c.execute("DELETE FROM folders")
            c.executemany(
                "INSERT INTO folders (id, data) VALUES (?, ?)",
                [(f["id"], json.dumps(f, ensure_ascii=False)) for f in folders])
            self._put_meta(host_id=host_id, host=host, address=address,
                           nickname=nickname)
        return archived

    def _put_note(self, note: dict[str, Any]) -> None:
        version, updated_at = _stamp(note)
        self._conn.execute(
            "INSERT OR REPLACE INTO notes (id, version, updated_at, data)"
            " VALUES (?, ?, ?, ?)",
            (note["id"], version, updated_at,
             json.dumps(note, ensure_ascii=False)))

    def _put_meta(self, **values: str) -> None:
        values["synced_at"] = str(int(time.time() * 1000))
        self._conn.executemany(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            [(k, str(v)) for k, v in values.items()])

    def upsert_note(self, note: dict[str, Any]) -> None:
        if not note.get("id"):
            return
        with self._conn:
            self._put_note(note)
            self._put_meta()

    def delete_note(self, note_id: str) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
            self._put_meta()

    def upsert_folder(self, folder: dict[str, Any]) -> None:
        if not folder.get("id"):
            return
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO folders (id, data) VALUES (?, ?)",
                (folder["id"], json.dumps(folder, ensure_ascii=False)))
            self._put_meta()

    def delete_folder(self, folder_id: str) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM folders WHERE id = ?", (folder_id,))
            self._put_meta()


class ReplicaSync(QObject):
    """호스트에서 오는 변화를 사본에 그대로 옮겨 적는다.

    화면(메인 창)과는 따로 듣는다. 화면은 응답을 기다리지 않고 먼저 고쳐
    보여 주기도 하지만, 사본에는 호스트가 확정해 보낸 것만 들어간다.

    사본을 쓰다 문제가 생겨도 앱은 멈추지 않는다. 그때는 적기를 그만둔다."""

    def __init__(self, store: ReplicaStore, client: ClientBase,
                 address: str = "", parent: QObject | None = None):
        super().__init__(parent)
        self._store: ReplicaStore | None = store
        self._client = client
        self._address = address
        client.welcomed.connect(self._on_welcomed)
        client.noteCreated.connect(lambda n: self._guard(self._upsert_note, n))
        client.noteUpdated.connect(lambda n: self._guard(self._upsert_note, n))
        client.noteDeleted.connect(
            lambda nid: self._guard(lambda s: s.delete_note(nid)))
        client.folderCreated.connect(
            lambda f: self._guard(lambda s: s.upsert_folder(f)))
        client.folderUpdated.connect(
            lambda f: self._guard(lambda s: s.upsert_folder(f)))
        client.folderDeleted.connect(
            lambda fid: self._guard(lambda s: s.delete_folder(fid)))

    @staticmethod
    def _upsert_note(store: ReplicaStore, note: dict) -> None:
        store.upsert_note(note)

    def _guard(self, fn, *args) -> None:
        if self._store is None:
            return
        try:
            if args:
                fn(self._store, *args)
            else:
                fn(self._store)
        except (sqlite3.Error, OSError):
            self._store = None

    def _on_welcomed(self, data: dict) -> None:
        def apply(store: ReplicaStore) -> None:
            store.apply_snapshot(
                list(data.get("notes") or []), list(data.get("folders") or []),
                host_id=str(data.get("host_id") or ""),
                host=str(data.get("host") or ""),
                address=self._address,
                nickname=self._client.nickname())
        self._guard(apply)
