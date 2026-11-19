from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict, fields
from pathlib import Path
from typing import Any

from . import config


@dataclass
class NoteLocalState:
    x: int = 120
    y: int = 120
    hidden: bool = False
    w: int = 0   # 0 이면 서버에 저장된 기본 크기를 쓴다.
    h: int = 0
    # 쪽지로 접어 둔 상태와, 붙여 둔 화면 가장자리("left"/"right")·세로 자리.
    folded: bool = False
    dock: str = ""
    dock_y: int = 0
    # 이 메모를 접었을 때의 모양("slip"·"icon"). 비어 있으면 설정의 기본 모양.
    fold_style: str = ""


@dataclass
class FolderIconState:
    """바탕 화면에 둔 메모 폴더 아이콘의 자리와 열림 여부. 닫혀 있으면 그
    폴더의 메모는 바탕 화면에 나오지 않고 아이콘 하나로 접혀 있다."""
    x: int = 0
    y: int = 0
    opened: bool = False


# 파일의 "notes" 항목에는 이 다섯 값만 쓴다. 1.0.6 이하의 로더는 모르는 키를
# 만나면 파일 전체를 버리고 빈 상태로 시작하므로(닉네임·위치가 함께 사라진다),
# 뒤에 생긴 값은 옛 버전이 읽지 않는 별도 키("note_folds")에 둔다.
_NOTE_BASE_KEYS = ("x", "y", "hidden", "w", "h")
_NOTE_FOLD_KEYS = ("folded", "dock", "dock_y", "fold_style")
_NOTE_FIELDS = {f.name for f in fields(NoteLocalState)}


def _note_from_raw(payload: Any, fold: Any) -> NoteLocalState:
    """모르는 키는 버리고 아는 값만으로 만든다(앞으로 값이 늘어도 옛 파일과
    새 파일을 서로 읽을 수 있게)."""
    data: dict[str, Any] = {}
    if isinstance(payload, dict):
        data.update({k: v for k, v in payload.items() if k in _NOTE_FIELDS})
    if isinstance(fold, dict):
        data.update({k: v for k, v in fold.items() if k in _NOTE_FOLD_KEYS})
    state = NoteLocalState(**data)
    if state.dock not in ("left", "right"):
        state.dock = ""
    state.folded = bool(state.folded)
    if state.fold_style not in ("slip", "icon"):
        state.fold_style = ""
    try:
        state.dock_y = int(state.dock_y)
    except (TypeError, ValueError):
        state.dock_y = 0
    return state


def _folder_icons_from_raw(raw: Any) -> dict[str, FolderIconState]:
    out: dict[str, FolderIconState] = {}
    if not isinstance(raw, dict):
        return out
    for fid, payload in raw.items():
        if not isinstance(payload, dict):
            continue
        try:
            out[str(fid)] = FolderIconState(
                x=int(payload.get("x", 0)), y=int(payload.get("y", 0)),
                opened=bool(payload.get("opened", False)))
        except (TypeError, ValueError):
            continue
    return out


@dataclass
class UserRecord:
    """타임스탬프는 모두 epoch 밀리초 단위다.

    - ``last_joined_at`` 은 현재 세션 시작 시각이며, 0이면 지속 시간 미상.
    - ``last_disconnected_at`` 은 이전 세션이 실제로 끊긴 시각이다.
    """
    last_seen_at: int = 0
    last_joined_at: int = 0
    last_disconnected_at: int = 0


@dataclass
class LocalState:
    nickname: str = ""
    last_host_ip: str = "127.0.0.1"
    last_host_port: int = config.DEFAULT_PORT
    last_mode: str = ""   # "" | "host" | "guest" — 자동 재접속 판단용
    last_heartbeat_ms: int = config.DEFAULT_HEARTBEAT_MS
    notes: dict[str, NoteLocalState] = field(default_factory=dict)
    users: dict[str, UserRecord] = field(default_factory=dict)
    folder_icons: dict[str, FolderIconState] = field(default_factory=dict)

    def get_note_state(self, note_id: str) -> NoteLocalState:
        if note_id not in self.notes:
            self.notes[note_id] = NoteLocalState()
        return self.notes[note_id]

    def set_note_state(
        self, note_id: str, x: int | None = None, y: int | None = None,
        hidden: bool | None = None, w: int | None = None, h: int | None = None,
        folded: bool | None = None, dock: str | None = None,
        dock_y: int | None = None, fold_style: str | None = None,
    ) -> NoteLocalState:
        state = self.get_note_state(note_id)
        if fold_style is not None:
            state.fold_style = fold_style if fold_style in ("slip", "icon") else ""
        if folded is not None:
            state.folded = bool(folded)
        if dock is not None:
            state.dock = dock if dock in ("left", "right") else ""
        if dock_y is not None:
            state.dock_y = int(dock_y)
        if x is not None:
            state.x = x
        if y is not None:
            state.y = y
        if hidden is not None:
            state.hidden = hidden
        if w is not None:
            state.w = w
        if h is not None:
            state.h = h
        return state

    def remove_note(self, note_id: str) -> None:
        self.notes.pop(note_id, None)

    def touch_user(self, nickname: str, joined: bool = False, *,
                   disconnected: bool = False) -> UserRecord:
        """``joined`` 이면 ``last_joined_at`` 을, ``disconnected`` 이면
        ``last_disconnected_at`` 을 함께 갱신한다."""
        if not nickname:
            return UserRecord()
        rec = self.users.get(nickname) or UserRecord()
        ts = int(time.time() * 1000)
        rec.last_seen_at = ts
        if joined:
            rec.last_joined_at = ts
        if disconnected:
            rec.last_disconnected_at = ts
        self.users[nickname] = rec
        return rec

    def touch_user_in_session(self, nickname: str, *, is_self: bool) -> UserRecord:
        """welcome 명단을 처리할 때 쓴다.

        접속 시점에 이미 명단에 있던 다른 참가자의 join 시각은 알 수 없으므로,
        ``last_joined_at`` 을 본인에게만 지금 시각으로 찍고
        나머지는 0(미상)으로 둔다. 이후 ``user_joined`` 이벤트가 오면
        정확한 시각으로 갱신된다."""
        if not nickname:
            return UserRecord()
        rec = self.users.get(nickname) or UserRecord()
        ts = int(time.time() * 1000)
        rec.last_seen_at = ts
        rec.last_joined_at = ts if is_self else 0
        self.users[nickname] = rec
        return rec


class LocalStateStore:
    def __init__(self, path: Path | None = None):
        self.path = path or config.default_local_state_path()
        self.state = self._load()

    def _load(self) -> LocalState:
        if not self.path.exists():
            return LocalState()
        try:
            raw: dict[str, Any] = json.loads(self.path.read_text(encoding="utf-8"))
            folds = raw.get("note_folds", {})
            if not isinstance(folds, dict):
                folds = {}
            notes = {
                nid: _note_from_raw(v, folds.get(nid))
                for nid, v in raw.get("notes", {}).items()
            }
            users: dict[str, UserRecord] = {}
            for nick, payload in raw.get("users", {}).items():
                if not isinstance(payload, dict):
                    continue
                users[nick] = UserRecord(
                    last_seen_at=int(payload.get("last_seen_at", 0)),
                    last_joined_at=int(payload.get("last_joined_at", 0)),
                    last_disconnected_at=int(
                        payload.get("last_disconnected_at", 0)),
                )
            return LocalState(
                nickname=raw.get("nickname", ""),
                last_host_ip=raw.get("last_host_ip", "127.0.0.1"),
                last_host_port=raw.get("last_host_port", config.DEFAULT_PORT),
                last_mode=raw.get("last_mode", ""),
                last_heartbeat_ms=raw.get("last_heartbeat_ms",
                                           config.DEFAULT_HEARTBEAT_MS),
                notes=notes,
                users=users,
                folder_icons=_folder_icons_from_raw(raw.get("folder_icons")),
            )
        except (json.JSONDecodeError, TypeError, ValueError):
            return LocalState()

    def save(self) -> None:
        data = {
            "nickname": self.state.nickname,
            "last_host_ip": self.state.last_host_ip,
            "last_host_port": self.state.last_host_port,
            "last_mode": self.state.last_mode,
            "last_heartbeat_ms": self.state.last_heartbeat_ms,
            "notes": {
                nid: {k: getattr(v, k) for k in _NOTE_BASE_KEYS}
                for nid, v in self.state.notes.items()
            },
            "note_folds": {
                nid: {k: getattr(v, k) for k in _NOTE_FOLD_KEYS}
                for nid, v in self.state.notes.items()
                if v.folded or v.dock or v.fold_style
            },
            "folder_icons": {
                fid: asdict(v) for fid, v in self.state.folder_icons.items()
            },
            "users": {n: asdict(v) for n, v in self.state.users.items()},
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")
