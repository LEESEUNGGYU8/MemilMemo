from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Signal

from .. import reminders as rm


class ClientBase(QObject):
    """게스트(TCP)와 호스트(in-process)가 같은 시그널을 제공해, UI가
    역할에 따라 분기하지 않아도 되게 하는 통합 인터페이스."""

    welcomed = Signal(dict)                  # {heartbeat_ms, notes, locks, users, folders, host}
    noteCreated = Signal(dict)
    noteUpdated = Signal(dict)
    noteDeleted = Signal(str)
    folderCreated = Signal(dict)
    folderUpdated = Signal(dict)
    folderDeleted = Signal(str)
    lockGranted = Signal(str)
    lockDenied = Signal(str, str)            # note_id, holder
    lockHeld = Signal(str, str)              # note_id, holder
    lockReleased = Signal(str)
    userJoined = Signal(str)
    userLeft = Signal(str)
    historyAppended = Signal(dict)
    # 공유 알림이 바뀌었다(메모 id, 알림 dict 또는 None, 바꾼 사람, 완료로 지웠는가).
    reminderChanged = Signal(str, object, str, bool)
    # 1.2: 공지가 바뀌었다(메모 id, 만든 사람 또는 None, 바꾼 사람), 내 공지를 본
    # 기록(메모 id, {이름: {"version", "at"}}), 누가 나를 불렀다(메모 id, 부른 사람, 때).
    noticeChanged = Signal(str, object, str)
    seenChanged = Signal(str, dict)
    mentioned = Signal(str, str, int)
    # 위젯이 바뀌었다(메모 id, 위젯 id, 데이터, 바꾼 사람), 위젯 동작을 거절했다(위젯 id, 까닭).
    widgetChanged = Signal(str, str, dict, str)
    widgetError = Signal(str, str)
    # 누군가 메모를 새로 만들었다(이력 항목). 지운 메모를 되돌린 경우는 빠진다.
    noteAnnounced = Signal(dict)
    # 누군가 나만 보던 메모를 공개로 바꿨다({"user": 바꾼 사람, "note_id": 메모}).
    noteShared = Signal(dict)
    # 누군가 메모를 고치고 편집을 마쳤다(이력 항목). 편집 중에는 오지 않는다.
    # 편집 중에 저장된 글은 noteUpdated 로 온다.
    noteEdited = Signal(dict)
    historyList = Signal(str, list)          # note_id 또는 '', [entries]
    # 휴지통에 든 메모들(메모 dict 에 deleted_at, deleted_by 가 더 있다).
    trashList = Signal(list)
    # 휴지통이 달라졌다(누가 지웠다, 되살렸다, 비웠다). 열어 둔 창이 다시 묻는다.
    trashChanged = Signal()
    # 휴지통에서 되살아난 메모({"user": 되살린 사람, "note_id": 메모}).
    noteRestored = Signal(dict)
    connectionChanged = Signal(bool)
    errorOccurred = Signal(str)
    # 암호가 걸린 공유다. 사용자에게 암호를 물어야 한다({"wrong": 방금 넣은 암호가
    # 틀렸는가, "wait": 너무 틀려 기다려야 하는 초}).
    passwordRequired = Signal(dict)
    # 암호가 맞았다. 다음부터 묻지 않게 적어 둘 열쇠(ShareAuth.to_dict).
    passwordAccepted = Signal(dict)
    # 암호를 걸어 두었던 공유 PC 가 암호 없이 답했다. 붙지 않고 기다린다.
    hostOpen = Signal()
    # 암호를 아는 척했지만 모르는 상대다. 붙지 않는다.
    hostUnverified = Signal()
    # 찾아낸 새 주소가 붙던 공유 PC 로 확인돼 옮겼다 / 아니어서 돌아왔다.
    followed = Signal(str, int)
    followFailed = Signal(str, int)

    def __init__(self, nickname: str, parent: QObject | None = None):
        super().__init__(parent)
        self._nickname = nickname

    def nickname(self) -> str:
        return self._nickname

    def start(self) -> None: ...
    def stop(self) -> None: ...

    # 참여하는 PC 만 쓰는 것들(공유 PC 자신은 암호도 주소도 없다).
    def set_auth(self, auth) -> None: ...
    def submit_password(self, password: str) -> None: ...
    def pause(self) -> None: ...
    def resume(self) -> None: ...
    def address(self) -> tuple[str, int]: return "", 0
    def set_address(self, host: str, port: int) -> None: ...
    def follow(self, host: str, port: int) -> None: ...

    def acquire_lock(self, note_id: str) -> None: ...
    def release_lock(self, note_id: str) -> None: ...
    def create_note(self, color: str, width: int, height: int,
                    folder_id: str | None = None,
                    private_owner: str | None = None,
                    quiet: bool = False) -> None: ...
    def update_note(
        self, note_id: str, content: str, color: str, width: int, height: int,
        expected_version: int,
    ) -> None: ...
    def delete_note(self, note_id: str) -> None: ...
    def set_note_folder(self, note_id: str, folder_id: str | None) -> None: ...
    def set_note_privacy(self, note_id: str, private: bool) -> None: ...
    def create_folder(self, name: str, color: str,
                      private: bool = False,
                      folder_id: str | None = None) -> None: ...
    def update_folder(self, folder_id: str, *,
                       name: str | None = None,
                       color: str | None = None,
                       private: bool | None = None) -> None: ...
    def delete_folder(self, folder_id: str) -> None: ...
    def get_history(self, note_id: str | None = None) -> None: ...
    # 휴지통(공유 PC 가 1.0.4 이상일 때만 답한다).
    def get_trash(self) -> None: ...
    def restore_note(self, note_id: str) -> None: ...
    def purge_note(self, note_id: str) -> None: ...
    def empty_trash(self) -> None: ...
    # 공유 알림(공유 PC 가 welcome 에 reminders 를 보낼 때만 답한다).
    # ``reminder`` 는 {"rule", "targets"} 또는 None(지움).
    def set_reminder(self, note_id: str, reminder: dict | None,
                     done: bool = False) -> None: ...
    # 공지와 읽음(공유 PC 가 welcome 에 notices 를 보낼 때만 답한다).
    def set_notice(self, note_id: str, on: bool) -> None: ...
    def send_seen(self, note_id: str, version: int) -> None: ...
    # 위젯(공유 PC 가 welcome 에 widgets 를 보낼 때만 답한다).
    def put_widget(self, note_id: str, widget_id: str, data: dict) -> None: ...
    def widget_op(self, widget_id: str, op: dict) -> None: ...

    def _dispatch(self, msg_type: str, data: dict[str, Any]) -> None:  # noqa: C901
        if msg_type == "welcome":
            self.welcomed.emit(data)
        elif msg_type == "note_created":
            note = data.get("note", {})
            if data.get("restored_by"):
                # 메모가 오기 전에 알린다. 받는 쪽이 새 메모로 다루지 않게.
                self.noteRestored.emit({"user": str(data["restored_by"]),
                                        "note_id": note.get("id", "")})
            self.noteCreated.emit(note)
            if data.get("shared_by"):
                self.noteShared.emit({"user": str(data["shared_by"]),
                                      "note_id": note.get("id", "")})
        elif msg_type == "trash_list":
            notes = data.get("notes", [])
            self.trashList.emit(notes if isinstance(notes, list) else [])
        elif msg_type == "trash_changed":
            self.trashChanged.emit()
        elif msg_type == "note_updated":
            self.noteUpdated.emit(data.get("note", {}))
        elif msg_type == "note_deleted":
            self.noteDeleted.emit(data.get("id", ""))
        elif msg_type == "folder_created":
            self.folderCreated.emit(data.get("folder", {}))
        elif msg_type == "folder_updated":
            self.folderUpdated.emit(data.get("folder", {}))
        elif msg_type == "folder_deleted":
            self.folderDeleted.emit(data.get("id", ""))
        elif msg_type == "lock_granted":
            self.lockGranted.emit(data.get("note_id", ""))
        elif msg_type == "lock_denied":
            self.lockDenied.emit(data.get("note_id", ""), data.get("holder", ""))
        elif msg_type == "lock_held":
            self.lockHeld.emit(data.get("note_id", ""), data.get("holder", ""))
        elif msg_type == "lock_released":
            self.lockReleased.emit(data.get("note_id", ""))
        elif msg_type == "user_joined":
            self.userJoined.emit(data.get("nickname", ""))
        elif msg_type == "user_left":
            self.userLeft.emit(data.get("nickname", ""))
        elif msg_type == "history_appended":
            entry = data.get("entry", {})
            self.historyAppended.emit(entry)
            # 만든 기록은 실제로 새 메모가 생겼을 때만 온다(공개 범위가 바뀌어
            # 새로 보이게 된 메모에는 오지 않는다). 옛 버전 호스트도 같다.
            if entry.get("action") == "create" and not data.get("quiet"):
                self.noteAnnounced.emit(entry)
            # 고친 기록은 편집이 끝나 잠금을 풀 때, 내용이 달라졌으면 한 번 온다.
            # 글자를 칠 때마다 오지 않는다. 옛 버전 호스트도 같다.
            elif entry.get("action") == "update":
                self.noteEdited.emit(entry)
        elif msg_type == "reminder_changed":
            note_id = data.get("note_id")
            raw = data.get("reminder")
            entry = rm.shared_from_dict(raw) if raw is not None else None
            if not isinstance(note_id, str) or not note_id or (raw is not None and entry is None):
                return
            by = data.get("by")
            self.reminderChanged.emit(note_id, entry, by if isinstance(by, str) else "",
                                      data.get("done") is True)
        elif msg_type == "notice_changed":
            note_id = data.get("note_id")
            owner = data.get("owner")
            by = data.get("by")
            if isinstance(note_id, str) and note_id and (owner is None or isinstance(owner, str)):
                self.noticeChanged.emit(note_id, owner or None,
                                        by if isinstance(by, str) else "")
        elif msg_type == "seen_changed":
            note_id = data.get("note_id")
            seen = data.get("seen")
            if isinstance(note_id, str) and note_id and isinstance(seen, dict):
                self.seenChanged.emit(note_id, clean_seen(seen))
        elif msg_type == "widget_changed":
            note_id, wid, wdata = data.get("note_id"), data.get("widget_id"), data.get("data")
            by = data.get("by")
            if (isinstance(note_id, str) and isinstance(wid, str) and wid
                    and isinstance(wdata, dict)):
                self.widgetChanged.emit(note_id, wid, wdata, by if isinstance(by, str) else "")
        elif msg_type == "widget_error":
            wid, error = data.get("widget_id"), data.get("error")
            if isinstance(wid, str) and isinstance(error, str):
                self.widgetError.emit(wid, error)
        elif msg_type == "mentioned":
            note_id, by, at = data.get("note_id"), data.get("by"), data.get("at")
            if isinstance(note_id, str) and note_id and isinstance(by, str):
                self.mentioned.emit(note_id, by, at if isinstance(at, int) else 0)
        elif msg_type == "history_list":
            self.historyList.emit(data.get("note_id", "") or "", data.get("entries", []))
        elif msg_type == "error":
            self.errorOccurred.emit(data.get("message", ""))


def clean_seen(raw) -> dict[str, dict]:
    """공유 PC 가 보낸 본 기록을 고친다({이름: {"version": 정수, "at": 정수}})."""
    out: dict[str, dict] = {}
    if not isinstance(raw, dict):
        return out
    for name, entry in raw.items():
        if not isinstance(name, str) or not name or not isinstance(entry, dict):
            continue
        v, at = entry.get("version"), entry.get("at")
        if isinstance(v, int) and isinstance(at, int):
            out[name] = {"version": v, "at": at}
    return out
