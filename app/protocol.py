from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from typing import Any

LENGTH_HEADER = struct.Struct(">I")

# 프레임 길이는 보낸 쪽이 4바이트로 적는다. 그대로 믿으면 받는 쪽이 4GB 까지
# 모으려 든다. 공유 PC 는 참여자가 보낸 프레임에 상한을 둔다. 인사(hello)는
# 닉네임 하나라 작고, 그 뒤로 가장 큰 것은 그림이 든 메모 한 장이다.
MAX_FRAME_BEFORE_HELLO = 64 * 1024
MAX_FRAME_FROM_GUEST = 256 * 1024 * 1024

# 클라이언트 → 서버
MSG_HELLO = "hello"
MSG_ACQUIRE_LOCK = "acquire_lock"
MSG_RELEASE_LOCK = "release_lock"
MSG_CREATE_NOTE = "create_note"
MSG_UPDATE_NOTE = "update_note"
MSG_DELETE_NOTE = "delete_note"
MSG_SET_NOTE_FOLDER = "set_note_folder"
MSG_SET_NOTE_PRIVACY = "set_note_privacy"
MSG_CREATE_FOLDER = "create_folder"
MSG_UPDATE_FOLDER = "update_folder"
MSG_DELETE_FOLDER = "delete_folder"
MSG_PONG = "pong"
MSG_GET_HISTORY = "get_history"
# 암호가 걸린 공유에 참여할 때, 암호를 안다는 증명(app/share_auth.py).
MSG_AUTH = "auth"
# 휴지통(1.0.4 부터). delete_note 로 지운 메모는 휴지통으로 간다. 옛 공유 PC 는
# 이 메시지들을 모르고 지나친다(welcome 에 trash_days 가 없으면 휴지통이 없다).
MSG_GET_TRASH = "get_trash"
MSG_RESTORE_NOTE = "restore_note"
MSG_PURGE_NOTE = "purge_note"
MSG_EMPTY_TRASH = "empty_trash"

# 서버 → 클라이언트
MSG_WELCOME = "welcome"
MSG_LOCK_GRANTED = "lock_granted"
MSG_LOCK_DENIED = "lock_denied"
MSG_LOCK_HELD = "lock_held"
MSG_LOCK_RELEASED = "lock_released"
MSG_NOTE_CREATED = "note_created"
MSG_NOTE_UPDATED = "note_updated"
MSG_NOTE_DELETED = "note_deleted"
MSG_FOLDER_CREATED = "folder_created"
MSG_FOLDER_UPDATED = "folder_updated"
MSG_FOLDER_DELETED = "folder_deleted"
MSG_HISTORY_APPENDED = "history_appended"
MSG_HISTORY_LIST = "history_list"
MSG_USER_JOINED = "user_joined"
MSG_USER_LEFT = "user_left"
MSG_PING = "ping"
MSG_ERROR = "error"
# 암호가 걸린 공유다. 인사(hello)에 이것으로 답하고, 증명이 맞으면 welcome 을 보낸다.
MSG_AUTH_REQUIRED = "auth_required"
MSG_AUTH_FAILED = "auth_failed"
# 휴지통에 든 메모들(그 참가자가 볼 수 있는 것만), 휴지통이 달라졌다는 알림.
MSG_TRASH_LIST = "trash_list"
MSG_TRASH_CHANGED = "trash_changed"


@dataclass
class Message:
    type: str
    data: dict[str, Any]

    def encode(self) -> bytes:
        body = json.dumps({"type": self.type, "data": self.data}).encode("utf-8")
        return LENGTH_HEADER.pack(len(body)) + body


class FrameParser:
    """이어 붙여 온 바이트에서 프레임을 하나씩 꺼낸다.

    ``max_frame`` 을 주면 그보다 긴 프레임이 왔을 때 받기를 그만두고
    ``overflowed`` 를 켠다(부르는 쪽이 연결을 끊는다). 값은 도중에 바꿀 수 있다."""

    def __init__(self, max_frame: int | None = None) -> None:
        self._buf = bytearray()
        self.max_frame = max_frame
        self.overflowed = False

    def feed(self, chunk: bytes) -> list[Message]:
        return list(self.iter_feed(chunk))

    def iter_feed(self, chunk: bytes):
        """``feed`` 와 같되 하나씩 내준다. 메시지를 처리하는 사이에 바뀐
        ``max_frame`` 이 바로 다음 프레임부터 적용된다."""
        if self.overflowed:
            return
        self._buf.extend(chunk)
        while True:
            if len(self._buf) < LENGTH_HEADER.size:
                break
            (length,) = LENGTH_HEADER.unpack_from(self._buf, 0)
            if self.max_frame is not None and length > self.max_frame:
                self.overflowed = True
                self._buf.clear()
                break
            total = LENGTH_HEADER.size + length
            if len(self._buf) < total:
                break
            body = bytes(self._buf[LENGTH_HEADER.size:total])
            del self._buf[:total]
            try:
                payload = json.loads(body.decode("utf-8"))
                msg_type = payload["type"]
                data = payload.get("data", {})
            except (json.JSONDecodeError, KeyError, UnicodeDecodeError,
                    TypeError, AttributeError, RecursionError):
                # 잘못된 프레임은 연결을 끊지 않고 건너뛴다.
                continue
            if not isinstance(msg_type, str) or not isinstance(data, dict):
                continue
            yield Message(type=msg_type, data=data)


def make(msg_type: str, **data: Any) -> Message:
    return Message(type=msg_type, data=data)
