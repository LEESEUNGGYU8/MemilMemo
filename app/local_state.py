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
    # 쪽지로 접어 둔 상태와, 붙여 둔 화면 벽("left"·"right"·"top"·"bottom"),
    # 벽을 따라 잰 탭의 자리(좌우 벽은 y, 위아래 벽은 x).
    folded: bool = False
    dock: str = ""
    dock_y: int = 0
    # 이 메모를 접었을 때의 모양("slip"·"icon"). 비어 있으면 설정의 기본 모양.
    fold_style: str = ""
    # 접힌 메모를 끌어다 둔 자리. x·y(펼친 자리)와 따로 기억해, 접으면 이리로
    # 가고 펴면 펼쳐 있던 자리로 돌아간다. fold_at 이 거짓이면 둔 자리가 없다.
    fold_x: int = 0
    fold_y: int = 0
    fold_at: bool = False


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
_NOTE_FOLD_KEYS = ("folded", "dock", "dock_y", "fold_style",
                   "fold_x", "fold_y", "fold_at")
_NOTE_FIELDS = {f.name for f in fields(NoteLocalState)}
_DOCK_SIDES = ("left", "right", "top", "bottom")


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _note_from_raw(payload: Any, fold: Any) -> NoteLocalState:
    """모르는 키는 버리고 아는 값만으로 만든다(앞으로 값이 늘어도 옛 파일과
    새 파일을 서로 읽을 수 있게)."""
    data: dict[str, Any] = {}
    if isinstance(payload, dict):
        data.update({k: v for k, v in payload.items() if k in _NOTE_FIELDS})
    if isinstance(fold, dict):
        data.update({k: v for k, v in fold.items() if k in _NOTE_FOLD_KEYS})
    state = NoteLocalState(**data)
    if state.dock not in _DOCK_SIDES:
        state.dock = ""
    state.folded = bool(state.folded)
    if state.fold_style not in ("slip", "icon"):
        state.fold_style = ""
    state.dock_y = _as_int(state.dock_y)
    state.fold_x = _as_int(state.fold_x)
    state.fold_y = _as_int(state.fold_y)
    state.fold_at = bool(state.fold_at)
    if (isinstance(fold, dict) and "fold_at" not in fold
            and state.folded and not state.dock):
        # 접힌 자리를 따로 적지 않던 버전(1.0.1 이하)은 쪽지를 둔 자리를 x·y 에
        # 적었다. 그 자리를 접힌 자리로 삼아, 펴고 접어도 쪽지가 옮겨 가지 않게 한다.
        state.fold_x, state.fold_y, state.fold_at = state.x, state.y, True
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
    # 이 PC 가 처음 받는 남의 메모를 숨김으로 두기 시작했는가. 그 규칙이 없던
    # 버전은 받은 메모를 모두 띄웠고 자리 기록(notes)을 그때 바로 적지 않았다.
    # 그래서 그런 버전에서 올라온 PC 는 기록이 없다는 것만으로 처음 받는 메모라
    # 할 수 없다. 공유 PC 에서 메모를 한 번 받고 나서 켠다.
    hides_new: bool = False
    # 공유에 거는 암호의 열쇠(app/share_auth.py 의 ShareAuth.to_dict). 공유 PC 는 제
    # 것을, 참여하는 PC 는 붙던 공유 PC 의 것을 적어 둔다. 암호가 없으면 None.
    share_auth: dict | None = None
    # 아이콘을 마지막으로 맞춰 놓은 바탕화면 격자(작업 영역 "x,y,w,h" →
    # [칸 폭, 칸 높이, 첫 칸의 x, 첫 칸의 y]). 앱이 꺼져 있는 동안 바탕화면
    # 아이콘의 크기가 바뀌었으면, 켤 때 아이콘들을 같은 칸으로 옮기는 데 쓴다.
    icon_grids: dict[str, list] = field(default_factory=dict)
    # 휴지통으로 간 메모가 이 PC 에서 있던 자리와 접힘(메모 id → (버린 때, 상태)).
    # 되살아나면 그대로 돌아온다. 휴지통의 보관 기간이 지나면 버린다.
    trashed: dict[str, tuple[int, NoteLocalState]] = field(default_factory=dict)
    # 메모마다 이 PC 에서 정한 바탕의 불투명도(%, 20 ~ 99). 없으면 설정의 기본값.
    # 메모 항목(notes)에 넣지 않는다: 1.0.6 이하는 모르는 키를 보면 파일을 버린다.
    note_opacity: dict[str, int] = field(default_factory=dict)
    # 모니터 구성마다 메모를 둔 자리(구성 열쇠 → 메모 id → [x, y, 접힌 x, 접힌 y,
    # 접힌 자리를 둠]). 노트북만 쓸 때와 모니터를 꽂았을 때를 따로 기억한다.
    screen_layouts: dict[str, dict[str, list]] = field(default_factory=dict)
    # 이 PC 에서만 거는 메모 알림과 체크 해제 예약(메모 id → {"rule": 규칙,
    # "set_at": 건 때, "fired": 마지막으로 처리한 회차, ...}, 때는 ms). 지운 메모의
    # 예약은 휴지통에 있는 동안 남겨 두고 되살리면 다시 쓴다.
    reminders: dict[str, dict] = field(default_factory=dict)
    check_resets: dict[str, dict] = field(default_factory=dict)
    # 공유 PC 가 나누는 알림(모두, 고른 사람)의 사본과 이 PC 의 처리 상태(메모 id →
    # {"rule", "targets", "set_by", "set_at", "fired", "snooze", "lead"}). 공유 PC 와
    # 끊겨 있어도 사본으로 알린다.
    shared_reminders: dict[str, dict] = field(default_factory=dict)
    # '항목별 합계 보기' 를 켠 메모(이 PC 설정).
    tally_notes: list = field(default_factory=list)
    # 메모마다 고른 자동 줄바꿈(메모 id: 켬/끔). 없으면 설정의 기본값.
    wrap_notes: dict = field(default_factory=dict)
    # 프로그램 창에 붙인 메모(메모 id → {"exe", "title", "corner", "dx", "dy", "label"}).
    attachments: dict = field(default_factory=dict)

    def keep_trashed(self, note_id: str) -> None:
        """사라진 메모의 자리를 따로 적어 두고 메모 목록에서는 뺀다."""
        state = self.notes.pop(note_id, None)
        if state is not None:
            self.trashed[note_id] = (int(time.time() * 1000), state)

    def take_trashed(self, note_id: str) -> NoteLocalState | None:
        kept = self.trashed.pop(note_id, None)
        return kept[1] if kept is not None else None

    def get_note_state(self, note_id: str) -> NoteLocalState:
        if note_id not in self.notes:
            self.notes[note_id] = NoteLocalState()
        return self.notes[note_id]

    def set_note_state(
        self, note_id: str, x: int | None = None, y: int | None = None,
        hidden: bool | None = None, w: int | None = None, h: int | None = None,
        folded: bool | None = None, dock: str | None = None,
        dock_y: int | None = None, fold_style: str | None = None,
        fold_x: int | None = None, fold_y: int | None = None,
        fold_at: bool | None = None,
    ) -> NoteLocalState:
        state = self.get_note_state(note_id)
        if fold_style is not None:
            state.fold_style = fold_style if fold_style in ("slip", "icon") else ""
        if folded is not None:
            state.folded = bool(folded)
        if dock is not None:
            state.dock = dock if dock in _DOCK_SIDES else ""
        if dock_y is not None:
            state.dock_y = int(dock_y)
        if fold_x is not None:
            state.fold_x = int(fold_x)
        if fold_y is not None:
            state.fold_y = int(fold_y)
        if fold_at is not None:
            state.fold_at = bool(fold_at)
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
        self.note_opacity.pop(note_id, None)

    def move_note_extras(self, old_id: str, new_id: str) -> bool:
        """메모마다 이 PC 에서 정한 것(알림, 체크 해제 예약, 투명도, 자동 줄바꿈, 항목별
        합계, 창에 붙이기)을 다른 id 로 옮긴다. 끊긴 동안 만든 메모가 올라가 새 id 를 받을
        때 쓴다. 옮긴 것이 있으면 True."""
        if not old_id or not new_id or old_id == new_id:
            return False
        moved = False
        for table in (self.note_opacity, self.reminders, self.check_resets,
                      self.wrap_notes, self.attachments):
            if old_id in table:
                table[new_id] = table.pop(old_id)
                moved = True
        if old_id in self.tally_notes:
            self.tally_notes = [new_id if n == old_id else n for n in self.tally_notes]
            moved = True
        return moved

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


def _trashed_from_raw(raw: Any) -> dict[str, tuple[int, NoteLocalState]]:
    """휴지통으로 간 메모의 자리. 보관 기간이 지난 것은 읽지 않는다."""
    out: dict[str, tuple[int, NoteLocalState]] = {}
    if not isinstance(raw, dict):
        return out
    oldest = int(time.time() * 1000) - (
        (config.TRASH_KEEP_DAYS + 1) * 24 * 60 * 60 * 1000)
    for nid, payload in raw.items():
        if not isinstance(nid, str) or not isinstance(payload, dict):
            continue
        at = _as_int(payload.get("at"))
        if at < oldest:
            continue
        try:
            out[nid] = (at, _note_from_raw(payload, payload))
        except (TypeError, ValueError):
            continue
    return out


def _icon_grids_from_raw(raw: Any) -> dict[str, list]:
    out: dict[str, list] = {}
    if not isinstance(raw, dict):
        return out
    for key, value in raw.items():
        if (isinstance(key, str) and isinstance(value, list) and len(value) == 4
                and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                        for v in value)
                and value[0] > 0 and value[1] > 0):
            out[key] = [float(v) for v in value]
    return out


def _opacity_from_raw(raw: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    if not isinstance(raw, dict):
        return out
    for nid, value in raw.items():
        if (isinstance(nid, str) and isinstance(value, int) and not isinstance(value, bool)
                and config.MIN_NOTE_OPACITY <= value < 100):
            out[nid] = value
    return out


# 예약 항목에 적는 때(ms)들. 규칙은 reminders.Rule 이 확인한다.
_SCHEDULE_TIMES = ("set_at", "fired", "snooze", "lead", "wait")


def _schedules_from_raw(raw: Any, known: set) -> dict[str, dict]:
    """예약을 읽는다. 이 PC 가 모르는 메모(휴지통에서도 사라진 것)의 예약은 버린다."""
    from .reminders import Rule

    out: dict[str, dict] = {}
    if not isinstance(raw, dict):
        return out
    for nid, entry in raw.items():
        if not isinstance(nid, str) or not isinstance(entry, dict) or nid not in known:
            continue
        rule = Rule.from_dict(entry.get("rule"))
        if rule is None:
            continue
        kept = {"rule": rule.to_dict()}
        for key in _SCHEDULE_TIMES:
            value = entry.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                kept[key] = value
        out[nid] = kept
    return out


def _shared_from_raw(raw: Any, known: set) -> dict[str, dict]:
    """공유 알림의 사본을 읽는다. 이 PC 가 모르는 메모의 것은 버린다."""
    from .reminders import shared_from_dict

    out: dict[str, dict] = {}
    if not isinstance(raw, dict):
        return out
    for nid, entry in raw.items():
        if not isinstance(nid, str) or not isinstance(entry, dict) or nid not in known:
            continue
        kept = shared_from_dict(entry)
        if kept is None:
            continue
        for key in ("fired", "snooze", "lead"):
            value = entry.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                kept[key] = value
        out[nid] = kept
    return out


def _layouts_from_raw(raw: Any) -> dict[str, dict[str, list]]:
    out: dict[str, dict[str, list]] = {}
    if not isinstance(raw, dict):
        return out
    for key, notes in raw.items():
        if not isinstance(key, str) or not isinstance(notes, dict):
            continue
        spots = {}
        for nid, spot in notes.items():
            if (isinstance(nid, str) and isinstance(spot, list) and len(spot) == 5
                    and all(isinstance(v, int) and not isinstance(v, bool) for v in spot)):
                spots[nid] = list(spot)
        if spots:
            out[key] = spots
    return out


class LocalStateStore:
    def __init__(self, path: Path | None = None):
        self.path = path or config.default_local_state_path()
        self.state = self._load()

    def _load(self) -> LocalState:
        if not self.path.exists():
            # 새로 설치한 PC 다. 받아 본 메모가 없으니 처음부터 가린다.
            return LocalState(hides_new=True)
        try:
            raw: dict[str, Any] = json.loads(self.path.read_text(encoding="utf-8"))
            folds = raw.get("note_folds", {})
            if not isinstance(folds, dict):
                folds = {}
            notes = {
                nid: _note_from_raw(v, folds.get(nid))
                for nid, v in raw.get("notes", {}).items()
            }
            trashed_raw = raw.get("trashed_notes")
            known = set(notes) | (set(trashed_raw) if isinstance(trashed_raw, dict) else set())
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
                hides_new=bool(raw.get("hides_new", False)),
                share_auth=(raw.get("share_auth")
                            if isinstance(raw.get("share_auth"), dict) else None),
                icon_grids=_icon_grids_from_raw(raw.get("icon_grids")),
                trashed=_trashed_from_raw(raw.get("trashed_notes")),
                note_opacity={k: v for k, v in _opacity_from_raw(raw.get("note_opacity")).items()
                              if k in known},
                screen_layouts={key: {nid: v for nid, v in layout.items() if nid in known}
                                for key, layout in
                                _layouts_from_raw(raw.get("screen_layouts")).items()},
                reminders=_schedules_from_raw(raw.get("reminders"), known),
                check_resets=_schedules_from_raw(raw.get("check_resets"), known),
                shared_reminders=_shared_from_raw(raw.get("shared_reminders"), known),
                # 이 PC 가 모르는 메모(지워진 지 오래된 것)의 것은 버린다. 많으면 나중 것을 둔다.
                tally_notes=[n for n in raw.get("tally_notes")
                             if isinstance(n, str) and n in known][-500:]
                if isinstance(raw.get("tally_notes"), list) else [],
                wrap_notes={k: v for k, v in (raw.get("wrap_notes") or {}).items()
                            if isinstance(k, str) and isinstance(v, bool) and k in known}
                if isinstance(raw.get("wrap_notes"), dict) else {},
                attachments={k: v for k, v in (raw.get("attachments") or {}).items()
                             if isinstance(k, str) and isinstance(v, dict) and k in known}
                if isinstance(raw.get("attachments"), dict) else {},
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
                if v.folded or v.dock or v.fold_style or v.fold_at
            },
            "folder_icons": {
                fid: asdict(v) for fid, v in self.state.folder_icons.items()
            },
            "users": {n: asdict(v) for n, v in self.state.users.items()},
            "hides_new": self.state.hides_new,
        }
        if self.state.share_auth:
            data["share_auth"] = self.state.share_auth
        if self.state.icon_grids:
            data["icon_grids"] = self.state.icon_grids
        if self.state.screen_layouts:
            data["screen_layouts"] = self.state.screen_layouts
        if self.state.note_opacity:
            data["note_opacity"] = dict(self.state.note_opacity)
        if self.state.reminders:
            data["reminders"] = self.state.reminders
        if self.state.check_resets:
            data["check_resets"] = self.state.check_resets
        if self.state.shared_reminders:
            data["shared_reminders"] = self.state.shared_reminders
        if self.state.tally_notes:
            data["tally_notes"] = list(self.state.tally_notes)
        if self.state.wrap_notes:
            data["wrap_notes"] = dict(self.state.wrap_notes)
        if self.state.attachments:
            data["attachments"] = dict(self.state.attachments)
        if self.state.trashed:
            data["trashed_notes"] = {
                nid: {"at": at, **{k: getattr(v, k)
                                   for k in _NOTE_BASE_KEYS + _NOTE_FOLD_KEYS}}
                for nid, (at, v) in self.state.trashed.items()
            }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")
