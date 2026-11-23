"""공유 PC 와 끊긴 동안 이 PC 에서 쓴 메모(참여한 PC 전용).

끊긴 동안에도 새 메모를 만들고, 받아 둔 사본의 메모를 고칠 수 있다. 그 글은
공유 PC 에 닿지 않았으므로 여기에 적어 둔다. 앱을 껐다 켜도 남는다. 다시
붙으면 사용자에게 물어 공유 PC 에 올리거나 지운다(메인 창이 한다).

두 가지가 있다.

* 새 메모(``KIND_NEW``): 끊긴 동안 만든 메모. 키는 그 메모의 id.
* 고친 메모(``KIND_EDIT``): 사본의 메모를 고친 글. 키는 ``edit:<원래 id>|<닉네임>``
  이고 한 사람이 원래 메모 하나에 하나만 둔다. 올릴 때는 원래 메모를 덮어쓰지 않고 새 메모가
  된다. 다만 연결돼 있을 때 시작한 편집(``started_online``)은 잠깐 끊겼다 붙은
  경우이므로, 그 사이 원래 메모가 그대로면 제자리에 넣는다.

누가 썼는지(닉네임)를 함께 적는다. 올릴 때 나만 보기 메모가 되므로, 다른
닉네임으로 붙었을 때는 꺼내지 않는다. 어느 공유 PC 를 보며 썼는지(주소)도 적어
두지만 그것으로 가리지는 않는다: 공유 PC 의 주소가 바뀌거나 공유를 다른 PC 가
이어받아도 쓴 글이 따라와야 한다."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

KIND_NEW = "new"
KIND_EDIT = "edit"
_EDIT_PREFIX = "edit:"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS drafts (
  key        TEXT PRIMARY KEY,
  kind       TEXT NOT NULL,
  address    TEXT NOT NULL DEFAULT '',
  nickname   TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER NOT NULL DEFAULT 0,
  data       TEXT NOT NULL
);
"""


def edit_key(base_id: str, nickname: str = "") -> str:
    """고친 글의 키. 닉네임을 넣어, 한 PC 에서 닉네임을 바꿔 같은 메모를 고쳐도
    앞사람의 글을 덮어쓰지 않게 한다."""
    if nickname:
        return f"{_EDIT_PREFIX}{base_id}|{nickname}"
    return _EDIT_PREFIX + base_id


@dataclass
class Draft:
    key: str
    kind: str
    note: dict[str, Any]         # content, color, width, height, folder_id, ...
    base_id: str = ""            # 고친 메모일 때: 원래 메모의 id
    base_version: int = 0        # 고치기 시작할 때 원래 메모의 판 번호
    started_online: bool = False
    updated_at: int = 0

    @property
    def content(self) -> str:
        return str(self.note.get("content") or "")


class DraftStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        try:
            self._open()
        except sqlite3.DatabaseError:
            # 파일이 상했다. 지우지 않고 옮겨 둔 뒤 새로 시작한다.
            self.close()
            self._move_aside()
            self._open()

    def _open(self) -> None:
        conn = sqlite3.connect(str(self.path))
        self._conn = conn
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.executescript(_SCHEMA)
        conn.commit()
        conn.execute("SELECT count(*) FROM drafts").fetchone()

    def _move_aside(self) -> None:
        if not self.path.exists():
            return
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        kept = self.path.with_name(f"{self.path.stem}.damaged-{stamp}{self.path.suffix}")
        os.replace(self.path, kept)
        for extra in ("-wal", "-shm"):
            side = self.path.with_name(self.path.name + extra)
            if side.exists():
                os.replace(side, kept.with_name(kept.name + extra))

    def close(self) -> None:
        if self._conn is None:
            return
        try:
            self._conn.close()
        except sqlite3.Error:
            pass
        self._conn = None

    # ── 쓰기 ──

    def _put(self, key: str, kind: str, payload: dict[str, Any], *,
             address: str, nickname: str) -> None:
        now = int(time.time() * 1000)
        with self._conn:
            self._conn.execute(
                "INSERT INTO drafts (key, kind, address, nickname, created_at,"
                " updated_at, data) VALUES (?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(key) DO UPDATE SET kind = excluded.kind,"
                " address = excluded.address, nickname = excluded.nickname,"
                " updated_at = excluded.updated_at, data = excluded.data",
                (key, kind, address, nickname, now, now,
                 json.dumps(payload, ensure_ascii=False)))

    def save_new(self, note: dict[str, Any], *, address: str, nickname: str) -> None:
        """끊긴 동안 만든 메모를 적는다(고칠 때마다 다시 부른다)."""
        if not note.get("id"):
            return
        self._put(str(note["id"]), KIND_NEW, {"note": note},
                  address=address, nickname=nickname)

    def save_edit(self, base_id: str, note: dict[str, Any], *, base_version: int,
                  started_online: bool, address: str, nickname: str) -> None:
        """사본의 메모를 고친 글을 적는다. 원래 메모 하나에 하나만 둔다.

        이미 적어 둔 것이 있으면 처음의 판 번호와 '연결돼 있을 때 시작했는가'를
        그대로 둔다(끊긴 동안 여러 번 고쳐도 견줄 기준은 처음 받은 메모다)."""
        if not base_id:
            return
        key = edit_key(base_id, nickname)
        earlier = self.get(key)
        if earlier is not None:
            base_version = earlier.base_version
            started_online = earlier.started_online and started_online
        self._put(key, KIND_EDIT,
                  {"note": note, "base_id": base_id,
                   "base_version": int(base_version),
                   "started_online": bool(started_online)},
                  address=address, nickname=nickname)

    def remove(self, key: str) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM drafts WHERE key = ?", (key,))

    def remove_edit(self, base_id: str, nickname: str = "") -> None:
        self.remove(edit_key(base_id, nickname))

    def get_edit(self, base_id: str, nickname: str = "") -> Draft | None:
        return self.get(edit_key(base_id, nickname))

    # ── 읽기 ──

    @staticmethod
    def _draft(key: str, kind: str, updated_at: int, raw: str) -> Draft | None:
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return None
        note = payload.get("note") if isinstance(payload, dict) else None
        if not isinstance(note, dict):
            return None
        return Draft(key=key, kind=kind, note=note,
                     base_id=str(payload.get("base_id") or ""),
                     base_version=int(payload.get("base_version") or 0),
                     started_online=bool(payload.get("started_online")),
                     updated_at=int(updated_at or 0))

    def get(self, key: str) -> Draft | None:
        row = self._conn.execute(
            "SELECT key, kind, updated_at, data FROM drafts WHERE key = ?",
            (key,)).fetchone()
        return self._draft(*row) if row else None

    def pending(self, nickname: str) -> list[Draft]:
        """이 닉네임으로 끊긴 동안 쓴 것들(쓴 순서대로)."""
        rows = self._conn.execute(
            "SELECT key, kind, updated_at, data FROM drafts"
            " WHERE nickname = ? ORDER BY created_at, rowid",
            (nickname,)).fetchall()
        return [d for row in rows if (d := self._draft(*row)) is not None]


__all__ = ["Draft", "DraftStore", "KIND_EDIT", "KIND_NEW", "edit_key"]
