from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from typing import Callable

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QTimer, Signal
from PySide6.QtNetwork import QHostAddress, QTcpServer, QTcpSocket

from .. import html_guard

from .. import config
from .. import mentions
from .. import reminders as rm
from ..share_auth import ShareAuth, guest_proof, host_proof, new_nonce, same, valid_nonce
from ..database import SqliteRepository
from ..models import Folder, Note, now_ms
from ..protocol import (
    FrameParser,
    MAX_FRAME_BEFORE_HELLO,
    MAX_FRAME_FROM_GUEST,
    MSG_ACQUIRE_LOCK,
    MSG_AUTH,
    MSG_AUTH_FAILED,
    MSG_AUTH_REQUIRED,
    MSG_CREATE_FOLDER,
    MSG_CREATE_NOTE,
    MSG_DELETE_FOLDER,
    MSG_DELETE_NOTE,
    MSG_EMPTY_TRASH,
    MSG_ERROR,
    MSG_FOLDER_CREATED,
    MSG_FOLDER_DELETED,
    MSG_FOLDER_UPDATED,
    MSG_GET_HISTORY,
    MSG_GET_TRASH,
    MSG_HELLO,
    MSG_HISTORY_APPENDED,
    MSG_HISTORY_LIST,
    MSG_LOCK_DENIED,
    MSG_LOCK_GRANTED,
    MSG_LOCK_HELD,
    MSG_LOCK_RELEASED,
    MSG_NOTE_CREATED,
    MSG_NOTE_DELETED,
    MSG_NOTE_UPDATED,
    MSG_PING,
    MSG_PURGE_NOTE,
    MSG_RELEASE_LOCK,
    MSG_REMINDER_CHANGED,
    MSG_RESTORE_NOTE,
    MSG_SET_NOTE_FOLDER,
    MSG_SET_NOTE_PRIVACY,
    MSG_SET_REMINDER,
    MSG_SET_NOTICE,
    MSG_SEEN,
    MSG_NOTICE_CHANGED,
    MSG_SEEN_CHANGED,
    MSG_MENTIONED,
    MSG_WIDGET_PUT,
    MSG_WIDGET_OP,
    MSG_WIDGET_CHANGED,
    MSG_WIDGET_ERROR,
    MSG_TRASH_CHANGED,
    MSG_TRASH_LIST,
    MSG_UPDATE_FOLDER,
    MSG_UPDATE_NOTE,
    MSG_USER_JOINED,
    MSG_USER_LEFT,
    MSG_WELCOME,
    Message,
    make,
)
from ..sync.history_tracker import HistoryTracker
from ..sync.lock_manager import LockManager


@dataclass
class _Session:
    socket: QTcpSocket | None   # None 은 로컬 호스트 세션을 뜻한다.
    parser: FrameParser
    nickname: str = ""
    hello_received: bool = False
    # 이 접속이 받아 쥐고 있는 잠금. 잠금은 닉네임으로 적히지만 같은 닉네임의
    # 접속이 둘일 수 있다(한 사람의 PC 두 대, 다시 붙었는데 공유 PC 는 아직 옛
    # 접속이 죽은 줄 모르는 동안). 한쪽이 끊길 때 다른 쪽의 잠금까지 풀지 않는다.
    held: set[str] = field(default_factory=set)
    # 암호가 걸린 공유에 들어오려는 접속: 인사한 이름, 이번에 낸 값(nonce), 틀린 횟수.
    pending_nick: str = ""
    challenge: str = ""
    auth_tries: int = 0


# 암호를 한 접속에서 이만큼 틀리면 끊는다. 한 주소에서 잇달아 이만큼 틀리면
# 잠깐 받지 않는다(틀릴수록 길어진다). 암호를 하나씩 넣어 보는 것을 늦춘다.
AUTH_TRIES_PER_CONNECTION = 5
AUTH_FAILS_BEFORE_WAIT = 5
AUTH_WAIT_SECONDS = 30
AUTH_WAIT_MAX_SECONDS = 600

# 답을 이만큼 보내지 못하고 쌓아 둔 상대의 이력 요청은 받지 않는다. 이력은
# 통째로 보내므로, 요청만 되풀이해도 공유 PC 의 메모리가 답으로 찬다.
MAX_QUEUED_REPLY = 8 * 1024 * 1024

# 휴지통에서 보관 기간이 지난 메모를 찾아 지우는 간격.
TRASH_SWEEP_MS = 60 * 60 * 1000

LocalSubscriber = Callable[[str, dict], None]


# 메모 하나에 넣을 수 있는 위젯의 수(지운 것의 데이터까지).
MAX_WIDGETS_PER_NOTE = 40


class HostServer(QObject):
    """원격 게스트(TCP)와 호스트 UI(로컬 세션)를 같은 처리 파이프라인으로 합류시킨다."""

    serverStarted = Signal(int)
    serverStopped = Signal()
    serverError = Signal(str)

    def __init__(
        self,
        repo: SqliteRepository,
        lock_manager: LockManager,
        history_tracker: HistoryTracker,
        host_nickname: str,
        heartbeat_ms: int = config.DEFAULT_HEARTBEAT_MS,
        parent: QObject | None = None,
        auth: ShareAuth | None = None,
    ) -> None:
        super().__init__(parent)
        self.repo = repo
        self.locks = lock_manager
        self.history = history_tracker
        self.host_nickname = host_nickname
        self.heartbeat_ms = heartbeat_ms
        # 공유에 건 암호. None 이면 누구나 들어온다.
        self.auth = auth
        # 암호를 모르는 옛 버전이 붙었을 때 보여 줄 글(그쪽 화면에 그대로 나온다).
        self.old_version_message = "password required. update the app."
        # 암호를 틀린 주소 → (잇달아 틀린 횟수, 다시 받기 시작하는 때).
        self._auth_fails: dict[str, tuple[int, float]] = {}

        self._server = QTcpServer(self)
        self._server.newConnection.connect(self._on_new_connection)
        self._sessions: dict[QTcpSocket, _Session] = {}
        self._local: _Session | None = None
        self._local_subscriber: LocalSubscriber | None = None

        self._heartbeat_timer = QTimer(self)
        self._heartbeat_timer.timeout.connect(self._on_heartbeat)

        self._idle_reap_timer = QTimer(self)
        self._idle_reap_timer.timeout.connect(self._on_idle_reap)

        # 휴지통에서 오래된 메모를 걷어 낸다(켤 때 한 번, 그 뒤로 한 시간마다).
        self._trash_timer = QTimer(self)
        self._trash_timer.timeout.connect(self.purge_old_trash)

    def start(self, port: int) -> bool:
        if not self._server.listen(QHostAddress.Any, port):
            self.serverError.emit(self._server.errorString())
            return False
        self._heartbeat_timer.start(self.heartbeat_ms)
        self._idle_reap_timer.start(30_000)
        self._trash_timer.start(TRASH_SWEEP_MS)
        self.purge_old_trash()
        self.serverStarted.emit(self._server.serverPort())
        return True

    def start_local(self) -> None:
        """포트를 열지 않고 시작한다(혼자 쓰기). 메모는 공유 PC 처럼 이 PC 에 두고
        이 PC 의 화면만 붙는다. 다른 PC 는 들어올 수 없다."""
        self._idle_reap_timer.start(30_000)
        self._trash_timer.start(TRASH_SWEEP_MS)
        self.purge_old_trash()

    def is_listening(self) -> bool:
        return self._server.isListening()

    def stop(self) -> None:
        self._heartbeat_timer.stop()
        self._idle_reap_timer.stop()
        self._trash_timer.stop()
        for sock in list(self._sessions):
            sock.disconnectFromHost()
            sock.deleteLater()
            # 미뤄 두지 않고 지금 지운다. 지우기를 미뤄 둔 채 이 서버가 먼저
            # 사라지면(끝나는 길에는 순서를 알 수 없다) 뒤늦게 지워지다 죽는다.
            QCoreApplication.sendPostedEvents(sock, QEvent.DeferredDelete)
        self._sessions.clear()
        self._server.close()
        self.serverStopped.emit()

    def set_local_subscriber(self, cb: LocalSubscriber | None) -> None:
        self._local_subscriber = cb

    def attach_local_host(self) -> None:
        self._local = _Session(socket=None, parser=FrameParser(),
                               nickname=self.host_nickname, hello_received=True)
        self.repo.touch_participant(self.host_nickname)
        # 호스트는 항상 존재하는 기준 사용자이므로 user_joined 는 보내지 않는다.

    def handle_from_local(self, msg: Message) -> None:
        assert self._local is not None, "call attach_local_host() first"
        self._handle(self._local, msg)

    def _on_new_connection(self) -> None:
        while self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            # 인사를 받기 전에는 작은 프레임만 받는다(_handle_hello 에서 늘린다).
            session = _Session(socket=sock,
                               parser=FrameParser(MAX_FRAME_BEFORE_HELLO))
            self._sessions[sock] = session
            sock.readyRead.connect(lambda s=sock: self._on_ready_read(s))
            sock.disconnected.connect(lambda s=sock: self._on_disconnected(s))

    def _on_ready_read(self, sock: QTcpSocket) -> None:
        session = self._sessions.get(sock)
        if session is None:
            return
        chunk = bytes(sock.readAll())
        for msg in session.parser.iter_feed(chunk):
            try:
                self._handle(session, msg)
            except (TypeError, ValueError, AttributeError, KeyError, OverflowError):
                # 모양이 틀린 메시지 하나 때문에 같이 온 나머지를 버리지 않는다.
                continue
        if session.parser.overflowed:
            # 터무니없이 긴 프레임을 보내겠다는 상대다. 받아 모으지 않고 끊는다.
            sock.abort()

    def _on_disconnected(self, sock: QTcpSocket) -> None:
        if sock in self._sessions:
            # 끊기기 직전에 보낸 것이 아직 남아 있으면 마저 처리한다(끝내면서
            # 보낸 마지막 저장과 잠금 반납).
            try:
                if sock.bytesAvailable() > 0:
                    self._on_ready_read(sock)
            except Exception:
                pass
        session = self._sessions.pop(sock, None)
        if session is None:
            return
        nick = session.nickname
        if nick:
            # 같은 닉네임의 접속이 남아 있으면 이 접속이 받은 잠금만 푼다. 남은
            # 쪽이 쓰고 있는 메모의 잠금까지 풀면 그 편집이 도중에 끊긴다.
            twin = self._has_session(nick)
            if twin:
                released = [nid for nid in session.held
                            if self.locks.release(nid, nick)]
            else:
                released = self.locks.release_all_by(nick)
            for nid in released:
                self._forget_held(nid)
                self._close_edit(nid, nick)
                # 나만 보는 메모의 잠금이 풀린 것은 그 메모를 보는 사람에게만 알린다.
                self._broadcast_for_note(
                    nid, make(MSG_LOCK_RELEASED, note_id=nid))
            if not twin:
                self._broadcast(make(MSG_USER_LEFT, nickname=nick))
        sock.deleteLater()

    def _has_session(self, nickname: str) -> bool:
        """이 닉네임으로 붙어 있는 접속이 있는가(공유 PC 자신도 센다)."""
        if self._local is not None and self._local.nickname == nickname:
            return True
        return any(s.hello_received and s.nickname == nickname
                   for s in self._sessions.values())

    def _forget_held(self, note_id: str) -> None:
        for s in self._sessions.values():
            s.held.discard(note_id)
        if self._local is not None:
            self._local.held.discard(note_id)

    def _close_edit(self, note_id: str, nickname: str, *,
                    reopen: bool = False) -> None:
        """잠금이 끝나기 전에 저장된 글을 이력에 남긴다(쓰던 사람이 끊겼거나
        오래 손대지 않아 잠금을 거둘 때). 그냥 버리면 그 편집이 이력에 없다.

        ``reopen`` 이면 그 사람의 편집을 지금 글에서 다시 시작해 둔다. 잠금을
        거둔 줄 모르고 계속 쓰는 옛 버전의 글도 끝낼 때 이력에 남는다."""
        note = self.repo.get_note(note_id)
        if note is None or not nickname:
            self.history.cancel_session(note_id, nickname)
            return
        entry = self.history.end_session(note, nickname)
        if entry is not None:
            self._broadcast_for_note(
                note_id, make(MSG_HISTORY_APPENDED, entry=entry.to_dict()))
        if reopen and not note.deleted:
            self.history.begin_session(note, nickname)

    def _send_to(self, session: _Session, msg: Message) -> None:
        if session.socket is None:
            if self._local_subscriber is not None:
                self._local_subscriber(msg.type, msg.data)
        else:
            session.socket.write(msg.encode())

    def _broadcast(self, msg: Message, exclude: _Session | None = None) -> None:
        for s in self._sessions.values():
            if s is exclude or not s.hello_received:
                continue
            self._send_to(s, msg)
        if self._local is not None and self._local is not exclude:
            self._send_to(self._local, msg)

    def _send_to_nickname(self, nickname: str | None,
                            msg: Message) -> None:
        if not nickname:
            return
        for s in self._sessions.values():
            if s.hello_received and s.nickname == nickname:
                self._send_to(s, msg)
        if (self._local is not None
                and self._local.nickname == nickname):
            self._send_to(self._local, msg)

    def _broadcast_to_all_except_nickname(
            self, msg: Message, *, except_nickname: str | None) -> None:
        for s in self._sessions.values():
            if not s.hello_received:
                continue
            if except_nickname and s.nickname == except_nickname:
                continue
            self._send_to(s, msg)
        if self._local is not None:
            if (except_nickname
                    and self._local.nickname == except_nickname):
                return
            self._send_to(self._local, msg)

    @staticmethod
    def _shared_note(note_dict: dict, actor: str) -> Message:
        """나만 보던 메모가 공개됐다. 받는 쪽에서 알릴 수 있게 누가 했는지 싣는다
        (옛 버전은 모르는 값을 그냥 지나친다)."""
        msg = make(MSG_NOTE_CREATED, note=note_dict)
        if actor:
            msg.data["shared_by"] = actor
        return msg

    def _propagate_audience_change(
            self, note_id: str, note_dict: dict, *,
            prev_owner: str | None, new_owner: str | None,
            actor: str = "") -> None:
        """가시 사용자 집합이 바뀔 때 이벤트를 분배한다.

        접근권을 유지한 사용자에게는 ``note_updated`` 만 보내 그 화면의 메모
        위젯을 깜빡임 없이 살려 둔다. 접근권을 잃은 사용자에게는 ``note_deleted``,
        새로 얻은 사용자에게는 ``note_created`` 를 보낸다."""
        if new_owner is not None and self.repo.delete_notice(note_id):
            # 나만 보는 메모가 됐다. 공지는 모두가 보는 메모에만 있다.
            self._send_to_nickname(new_owner, make(
                MSG_NOTICE_CHANGED, note_id=note_id, owner=None, by=actor))
        if prev_owner == new_owner:
            self._broadcast_for_owner(
                make(MSG_NOTE_UPDATED, note=note_dict),
                private_owner=new_owner)
            return
        if prev_owner is None and new_owner is not None:
            self._send_to_nickname(
                new_owner,
                make(MSG_NOTE_UPDATED, note=note_dict))
            self._broadcast_to_all_except_nickname(
                make(MSG_NOTE_DELETED, id=note_id),
                except_nickname=new_owner)
            return
        if prev_owner is not None and new_owner is None:
            self._send_to_nickname(
                prev_owner,
                make(MSG_NOTE_UPDATED, note=note_dict))
            self._broadcast_to_all_except_nickname(
                self._shared_note(note_dict, actor),
                except_nickname=prev_owner)
            return
        # 비공개 소유자가 다른 사람으로 교체된 경우로, 양쪽에 공통 사용자가 없다.
        self._send_to_nickname(
            prev_owner, make(MSG_NOTE_DELETED, id=note_id))
        self._send_to_nickname(
            new_owner, make(MSG_NOTE_CREATED, note=note_dict))

    def _broadcast_for_owner(self, msg: Message, *,
                              private_owner: str | None,
                              exclude: _Session | None = None) -> None:
        """``private_owner`` 가 ``None`` 이면 공개 항목으로 보고 모든 세션에 보낸다."""
        if private_owner is None:
            self._broadcast(msg, exclude=exclude)
            return
        for s in self._sessions.values():
            if s is exclude or not s.hello_received:
                continue
            if s.nickname != private_owner:
                continue
            self._send_to(s, msg)
        if (self._local is not None and self._local is not exclude
                and self._local.nickname == private_owner):
            self._send_to(self._local, msg)

    def _effective_owner_for_note(self, note: Note) -> str | None:
        """비공개 폴더 안의 메모는 자기 플래그와 무관하게 폴더의 소유자를 상속한다."""
        if note.private_owner:
            return note.private_owner
        if note.folder_id:
            folder = self.repo.get_folder(note.folder_id)
            if folder and folder.private_owner:
                return folder.private_owner
        return None

    def _broadcast_for_note(self, note_id: str, msg: Message, *,
                             exclude: _Session | None = None) -> None:
        note = self.repo.get_note(note_id)
        owner = self._effective_owner_for_note(note) if note else None
        self._broadcast_for_owner(msg, private_owner=owner, exclude=exclude)

    def _visible_notes_for(self, nickname: str) -> list[Note]:
        return [n for n in self.repo.list_notes()
                if self._effective_owner_for_note(n) in (None, nickname)]

    def _visible_folders_for(self, nickname: str) -> list[Folder]:
        return [f for f in self.repo.list_folders()
                if f.private_owner in (None, nickname)]

    def _current_users(self) -> list[str]:
        users = [s.nickname for s in self._sessions.values() if s.hello_received]
        if self._local:
            users.append(self._local.nickname)
        # 같은 닉네임의 접속이 둘이어도 한 사람이다.
        return list(dict.fromkeys(users))

    def _handle(self, session: _Session, msg: Message) -> None:
        t = msg.type
        d = msg.data
        if t == MSG_HELLO:
            if session.hello_received and session is not self._local:
                # 인사는 접속마다 한 번이다. 되풀이할 때마다 메모 전체를 다시
                # 보내 주면 그것만으로 공유 PC 의 메모리를 채울 수 있다.
                return
            self._handle_hello(session, d.get("nickname", "anon"), d)
        elif (t == MSG_AUTH and not session.hello_received
              and session is not self._local):
            self._handle_auth(session, d)
        elif not session.hello_received and session is not self._local:
            self._send_to(session, make(MSG_ERROR, message="hello required first"))
        elif t == MSG_ACQUIRE_LOCK:
            self._handle_acquire(session, d.get("note_id", ""))
        elif t == MSG_RELEASE_LOCK:
            self._handle_release(session, d.get("note_id", ""))
        elif t == MSG_CREATE_NOTE:
            self._handle_create(session, d)
        elif t == MSG_UPDATE_NOTE:
            self._handle_update(session, d)
        elif t == MSG_DELETE_NOTE:
            self._handle_delete(session, d.get("id", ""))
        elif t == MSG_SET_NOTE_FOLDER:
            self._handle_set_folder(session, d.get("id", ""),
                                     d.get("folder_id"))
        elif t == MSG_SET_NOTE_PRIVACY:
            self._handle_set_privacy(session, d.get("id", ""),
                                      bool(d.get("private", False)))
        elif t == MSG_CREATE_FOLDER:
            self._handle_folder_create(session, d)
        elif t == MSG_UPDATE_FOLDER:
            self._handle_folder_update(session, d)
        elif t == MSG_DELETE_FOLDER:
            self._handle_folder_delete(session, d.get("id", ""))
        elif t == MSG_GET_HISTORY:
            self._handle_get_history(session, d.get("note_id"))
        elif t == MSG_GET_TRASH:
            self._handle_get_trash(session)
        elif t == MSG_RESTORE_NOTE:
            self._handle_restore(session, d.get("id", ""))
        elif t == MSG_PURGE_NOTE:
            self._handle_purge(session, d.get("id", ""))
        elif t == MSG_EMPTY_TRASH:
            self._handle_empty_trash(session)
        elif t == MSG_SET_REMINDER:
            self._handle_set_reminder(session, d)
        elif t == MSG_SET_NOTICE:
            self._handle_set_notice(session, d)
        elif t == MSG_SEEN:
            self._handle_seen(session, d)
        elif t == MSG_WIDGET_PUT:
            self._handle_widget_put(session, d)
        elif t == MSG_WIDGET_OP:
            self._handle_widget_op(session, d)

    def _handle_hello(self, session: _Session, nickname: str,
                      d: dict | None = None) -> None:
        if not isinstance(nickname, str) or not nickname:
            nickname = "anon"
        if (session.socket is not None
                and len(nickname) > config.MAX_NICKNAME_LENGTH):
            # 이 이름은 모든 참가자에게 알려지고 각 PC 에 적힌다.
            self._send_to(session, make(MSG_ERROR, message="nickname too long"))
            session.socket.disconnectFromHost()
            return
        if self.auth is not None and session.socket is not None:
            self._ask_for_proof(session, nickname, d or {})
            return
        self._admit(session, nickname)

    def _admit(self, session: _Session, nickname: str, proof: str = "") -> None:
        session.nickname = nickname
        session.hello_received = True
        self.repo.touch_participant(nickname)
        session.parser.max_frame = MAX_FRAME_FROM_GUEST
        self._send_welcome(session, proof)
        self._broadcast(make(MSG_USER_JOINED, nickname=session.nickname),
                        exclude=session)

    # ── 참여 암호 ──

    def _ask_for_proof(self, session: _Session, nickname: str, d: dict) -> None:
        """암호가 걸린 공유다. 메모를 보내기 전에 암호를 아는지 묻는다."""
        if session.challenge:
            return                      # 이미 물었다. 인사는 접속마다 한 번이다
        if not d.get("auth"):
            # 암호를 모르는 옛 버전이다. 그쪽 화면에 까닭을 띄우고 끊는다(붙여
            # 두면 그 PC 는 연결된 줄 알고 메모를 고치려 든다).
            self._send_to(session, make(MSG_ERROR, message=self.old_version_message))
            session.socket.disconnectFromHost()
            return
        wait = self._auth_wait(session)
        if wait:
            self._send_to(session, make(MSG_AUTH_FAILED, left=0, wait=wait))
            session.socket.disconnectFromHost()
            return
        session.pending_nick = nickname
        session.challenge = new_nonce()
        self._send_to(session, make(MSG_AUTH_REQUIRED, **self._challenge(session)))

    def _challenge(self, session: _Session) -> dict:
        return {"salt": self.auth.salt.hex(), "iterations": self.auth.iterations,
                "nonce": session.challenge}

    def _handle_auth(self, session: _Session, d: dict) -> None:
        if self.auth is None or not session.challenge:
            return
        guest_nonce = d.get("nonce")
        ok = valid_nonce(guest_nonce) and same(
            d.get("proof"),
            guest_proof(self.auth.key, session.challenge, guest_nonce,
                        session.pending_nick))
        if not ok:
            session.auth_tries += 1
            wait = self._count_auth_failure(session)
            left = max(0, AUTH_TRIES_PER_CONNECTION - session.auth_tries)
            if wait or not left:
                self._send_to(session, make(MSG_AUTH_FAILED, left=0, wait=wait))
                session.socket.disconnectFromHost()
                return
            # 같은 값으로 다시 증명하지 못하게 새 값을 낸다.
            session.challenge = new_nonce()
            self._send_to(session, make(MSG_AUTH_FAILED, left=left,
                                        **self._challenge(session)))
            return
        self._auth_fails.pop(self._peer(session), None)
        host_nonce, session.challenge = session.challenge, ""
        self._admit(session, session.pending_nick,
                    proof=host_proof(self.auth.key, host_nonce, guest_nonce))

    @staticmethod
    def _peer(session: _Session) -> str:
        try:
            return str(session.socket.peerAddress().toString())
        except Exception:
            return ""

    def _auth_wait(self, session: _Session) -> int:
        """이 주소가 암호를 너무 틀려 기다려야 하는 초. 받아도 되면 0."""
        _count, until = self._auth_fails.get(self._peer(session), (0, 0.0))
        return max(0, int(until - time.monotonic() + 0.999))

    def _count_auth_failure(self, session: _Session) -> int:
        now = time.monotonic()
        # 오래된 기록은 버린다(주소를 바꿔 가며 틀려도 표가 끝없이 자라지 않게).
        for peer, (_n, until) in list(self._auth_fails.items()):
            if until + 3600 < now and len(self._auth_fails) > 256:
                del self._auth_fails[peer]
        peer = self._peer(session)
        count = self._auth_fails.get(peer, (0, 0.0))[0] + 1
        wait = 0
        if count % AUTH_FAILS_BEFORE_WAIT == 0:
            wait = min(AUTH_WAIT_SECONDS * 2 ** (count // AUTH_FAILS_BEFORE_WAIT - 1),
                       AUTH_WAIT_MAX_SECONDS)
        self._auth_fails[peer] = (count, now + wait)
        return wait

    def beacon(self, port: int) -> dict:
        """내부망에서 공유 PC 를 찾는 신호에 답할 내용(app/network/discovery.py)."""
        return {"host": self.host_nickname, "port": int(port),
                "host_id": self.repo.host_id(),
                "took_over_from": self.repo.took_over_from(),
                "locked": self.auth is not None, "ver": config.APP_VERSION}

    def _send_welcome(self, session: _Session, proof: str = "") -> None:
        """그 참가자가 볼 수 있는 메모와 폴더를 처음 상태로 보낸다."""
        notes = [n.to_dict() for n in self._visible_notes_for(session.nickname)]
        folders = [f.to_dict()
                   for f in self._visible_folders_for(session.nickname)]
        # 볼 수 없는 메모의 잠금(누가 어떤 메모를 편집 중인지)은 알리지 않는다.
        visible = {n["id"] for n in notes}
        locks = {nid: holder for nid, holder in self.locks.snapshot().items()
                 if nid in visible}
        self._send_to(session, make(
            MSG_WELCOME,
            heartbeat_ms=self.heartbeat_ms,
            notes=notes,
            folders=folders,
            locks=locks,
            users=self._current_users(),
            host=self.host_nickname,
            # 메모 묶음의 식별값. 참여한 PC 가 가진 사본이 이 묶음의 것인지 가린다.
            host_id=self.repo.host_id(),
            # 암호가 걸린 공유인가, 그리고 이 공유 PC 도 암호를 안다는 증명.
            locked=self.auth is not None,
            # 지운 메모를 휴지통에 두는 날 수. 이 값이 없는 공유 PC(옛 버전)는
            # 휴지통이 없어, 지우면 되살릴 수 없다.
            trash_days=config.TRASH_KEEP_DAYS,
            # 그 참가자가 볼 수 있는 메모의 공유 알림. 이 값이 없는 공유 PC(옛
            # 버전)는 공유 알림을 모른다(참여한 PC 는 '나만'만 걸게 한다).
            reminders={nid: entry for nid, entry in self.repo.list_reminders().items()
                       if nid in visible},
            # 1.2: 공지(메모 id → 만든 사람), 내 공지를 본 기록, 꺼져 있던 동안 온
            # @이름 알림. 이 값이 없는 공유 PC(옛 버전)는 공지를 모른다.
            notices={nid: owner for nid, owner in self.repo.list_notices().items()
                     if nid in visible},
            seen={nid: self.repo.list_seen(nid)
                  for nid, owner in self.repo.list_notices().items()
                  if owner == session.nickname and nid in visible},
            mentions=[m for m in self.repo.take_mentions(session.nickname)
                      if m["note_id"] in visible],
            # 그 사람이 볼 수 있는 메모의 위젯(위젯 id → 메모 id 와 데이터). 이 값이 없는
            # 공유 PC(옛 버전)의 위젯은 그림으로만 보인다.
            widgets={wid: {"note_id": nid, "data": data}
                     for wid, (nid, data) in self.repo.list_widgets().items()
                     if nid in visible},
            **({"auth": proof} if proof else {}),
        ))

    def resync_all(self) -> None:
        """저장소 내용이 통째로 바뀌었다(백업에서 복원). 모두에게 처음 상태를 다시 보낸다.

        잡혀 있던 잠금과 편집 세션은 바뀌기 전 내용을 가리키므로 버린다. 받는
        쪽은 welcome 을 받을 때마다 화면을 새로 짜므로 옛 버전도 그대로 따라온다."""
        self.locks.clear()
        self.history.clear()
        for s in list(self._sessions.values()):
            s.held.clear()
            if s.hello_received:
                self._send_welcome(s)
        if self._local is not None:
            self._local.held.clear()
            self._send_welcome(self._local)

    def _handle_acquire(self, session: _Session, note_id: str) -> None:
        note = self.repo.get_note(note_id)
        if note is None or note.deleted:
            self._send_to(session, make(MSG_LOCK_DENIED, note_id=note_id,
                                         holder="", reason="not_found"))
            return
        # 정상 클라이언트는 타인의 비공개 메모를 요청하지 않지만 위변조에 대비해 차단한다.
        owner = self._effective_owner_for_note(note)
        if owner and owner != session.nickname:
            self._send_to(session, make(MSG_LOCK_DENIED, note_id=note_id,
                                         holder="", reason="forbidden"))
            return
        notice_owner = self._notice_owner_blocking(note_id, session)
        if notice_owner:
            self._send_to(session, make(MSG_LOCK_DENIED, note_id=note_id,
                                         holder="", reason="notice",
                                         owner=notice_owner))
            return
        granted, holder = self.locks.acquire(note_id, session.nickname)
        if granted:
            # 같은 닉네임의 다른 접속이 쥐고 있었다면 이제 이 접속의 것이다.
            self._forget_held(note_id)
            session.held.add(note_id)
            self.history.begin_session(note, session.nickname)
            self._send_to(session, make(MSG_LOCK_GRANTED, note_id=note_id))
            self._broadcast_for_note(
                note_id,
                make(MSG_LOCK_HELD, note_id=note_id,
                     holder=session.nickname),
                exclude=session)
        else:
            self._send_to(session, make(MSG_LOCK_DENIED, note_id=note_id,
                                         holder=holder or ""))

    def _handle_release(self, session: _Session, note_id: str) -> None:
        note = self.repo.get_note(note_id)
        if note is not None:
            entry = self.history.end_session(note, session.nickname)
            if entry is not None:
                self._broadcast_for_note(
                    note_id,
                    make(MSG_HISTORY_APPENDED, entry=entry.to_dict()))
        if self.locks.release(note_id, session.nickname):
            self._forget_held(note_id)
            self._broadcast_for_note(
                note_id, make(MSG_LOCK_RELEASED, note_id=note_id))

    def _handle_create(self, session: _Session, d: dict) -> None:
        # 나만 보기로 만드는 메모의 주인은 만든 사람이다. 보낸 값을 그대로 쓰면
        # 남의 이름으로 메모를 끼워 넣을 수 있다. 남의 나만 보는 폴더에도 넣지 못한다.
        folder_id = d.get("folder_id") or None
        if folder_id is not None:
            folder = (self.repo.get_folder(folder_id)
                      if isinstance(folder_id, str) else None)
            if (folder is None or folder.deleted
                    or (folder.private_owner
                        and folder.private_owner != session.nickname)):
                folder_id = None
        note = Note.new(
            user=session.nickname,
            color=d.get("color"),
            folder_id=folder_id,
            private_owner=session.nickname if d.get("private_owner") else None,
        )
        if "id" in d and d["id"]:
            note.id = d["id"]
        note.width = int(d.get("width", note.width))
        note.height = int(d.get("height", note.height))
        self.repo.insert_note(note)
        entry = self.history.record_create(note, session.nickname)
        self._broadcast_for_note(
            note.id, make(MSG_NOTE_CREATED, note=note.to_dict()))
        appended = make(MSG_HISTORY_APPENDED, entry=entry.to_dict())
        if d.get("quiet"):
            # 지운 메모를 되돌리느라 다시 만든 것이다. 받는 쪽이 '새 메모'로
            # 알리지 않게 표시해 둔다(이력에는 그대로 남는다).
            appended.data["quiet"] = True
        self._broadcast_for_note(note.id, appended)

    def _handle_update(self, session: _Session, d: dict) -> None:
        note_id = d.get("id", "")
        if not isinstance(note_id, str):
            return
        # 남의 '나만 보기' 메모는 고치지 못한다(잠금을 받을 때와 같은 규칙이다.
        # 잠금 없이 곧바로 보낸 수정도 막아야 한다).
        existing = self.repo.get_note(note_id)
        owner = self._effective_owner_for_note(existing) if existing else None
        if owner and owner != session.nickname:
            self._send_to(session, make(MSG_ERROR, message="not allowed"))
            return
        holder = self.locks.holder(note_id)
        # 공지가 되기 전부터 잠금을 쥐고 쓰던 사람의 저장은 받는다(글을 잃지 않게).
        if holder != session.nickname and self._notice_owner_blocking(note_id, session):
            self._send_to(session, make(MSG_ERROR, message="notice"))
            return
        if holder and holder != session.nickname:
            self._send_to(session, make(MSG_ERROR,
                message=f"note locked by {holder}"))
            return
        self.locks.touch(note_id, session.nickname)
        updated = self.repo.update_note(
            note_id=note_id,
            # 본문이 가리키는 바깥 자원(네트워크 경로의 그림 등)을 걷어 내고 저장한다.
            # 여기서 걸러 두면 아직 옛 버전인 PC 도 그 메모를 안전하게 받는다.
            content=html_guard.clean(d.get("content", "")),
            color=d.get("color", config.DEFAULT_COLOR),
            width=int(d.get("width", config.DEFAULT_NOTE_WIDTH)),
            height=int(d.get("height", config.DEFAULT_NOTE_HEIGHT)),
            user=session.nickname,
            expected_version=d.get("expected_version"),
        )
        if updated is None:
            self._send_to(session, make(MSG_ERROR,
                message="update rejected (version mismatch or missing)"))
            return
        self._broadcast_for_note(
            updated.id, make(MSG_NOTE_UPDATED, note=updated.to_dict()))
        if existing is not None:
            self._send_mentions(existing, updated, session.nickname)

    def _handle_delete(self, session: _Session, note_id: str) -> None:
        if not isinstance(note_id, str):
            return
        holder = self.locks.holder(note_id)
        if holder and holder != session.nickname:
            self._send_to(session, make(MSG_ERROR,
                message=f"note locked by {holder}"))
            return
        existing = self.repo.get_note(note_id)
        if existing is None or existing.deleted:
            return
        if self._notice_owner_blocking(note_id, session):
            self._send_to(session, make(MSG_ERROR, message="notice"))
            return
        # soft-delete 가 소유자를 지우기 전에 캡처해야 삭제 이벤트를 원래 청중에게 보낼 수 있다.
        owner = self._effective_owner_for_note(existing)
        if owner and owner != session.nickname:
            # 남의 '나만 보기' 메모는 지우지 못한다.
            self._send_to(session, make(MSG_ERROR, message="not allowed"))
            return
        entry = self.history.record_delete(existing, session.nickname)
        # 지운 메모는 휴지통으로 간다. 옛 버전의 참여자가 지워도 같다.
        self.repo.trash_note(note_id, session.nickname)
        self.locks.release(note_id, session.nickname)
        self._forget_held(note_id)
        self._broadcast_for_owner(
            make(MSG_NOTE_DELETED, id=note_id), private_owner=owner)
        self._broadcast_for_owner(
            make(MSG_HISTORY_APPENDED, entry=entry.to_dict()),
            private_owner=owner)
        self._broadcast(make(MSG_TRASH_CHANGED))

    # ── 휴지통 ──────────────────────────────────────────────────────────

    def _may_touch_trashed(self, session: _Session, note_id) -> Note | None:
        """그 참가자가 휴지통의 그 메모를 다룰 수 있으면 메모를, 아니면 None.
        남이 혼자 보던 메모는 휴지통에서도 그 사람만 본다."""
        if not isinstance(note_id, str) or not self.repo.in_trash(note_id):
            return None
        note = self.repo.get_note(note_id)
        if note is None:
            return None
        owner = self._effective_owner_for_note(note)
        if owner and owner != session.nickname:
            self._send_to(session, make(MSG_ERROR, message="not allowed"))
            return None
        return note

    def _handle_get_trash(self, session: _Session) -> None:
        if (session.socket is not None
                and session.socket.bytesToWrite() > MAX_QUEUED_REPLY):
            return      # 앞서 보낸 답도 아직 받아 가지 않았다
        items = []
        for note, deleted_at, deleted_by in self.repo.list_trash():
            if self._effective_owner_for_note(note) in (None, session.nickname):
                item = note.to_dict()
                item["deleted_at"] = deleted_at
                item["deleted_by"] = deleted_by
                items.append(item)
        self._send_to(session, make(MSG_TRASH_LIST, notes=items,
                                     keep_days=config.TRASH_KEEP_DAYS))

    def _handle_restore(self, session: _Session, note_id) -> None:
        if self._may_touch_trashed(session, note_id) is None:
            return
        note = self.repo.restore_note(note_id, session.nickname)
        if note is None:
            return
        owner = self._effective_owner_for_note(note)
        entry = self.history.record_restore(note, session.nickname)
        # 옛 버전의 참여자에게는 새로 생긴 메모로 보인다(같은 id 로 돌아온다).
        created = make(MSG_NOTE_CREATED, note=note.to_dict())
        created.data["restored_by"] = session.nickname
        self._broadcast_for_owner(created, private_owner=owner)
        appended = make(MSG_HISTORY_APPENDED, entry=entry.to_dict())
        appended.data["quiet"] = True
        self._broadcast_for_owner(appended, private_owner=owner)
        self._broadcast(make(MSG_TRASH_CHANGED))

    def _handle_purge(self, session: _Session, note_id) -> None:
        if self._may_touch_trashed(session, note_id) is None:
            return
        if self.repo.purge_note(note_id):
            self._broadcast(make(MSG_TRASH_CHANGED))

    def _handle_empty_trash(self, session: _Session) -> None:
        """그 참가자에게 보이는 휴지통의 메모를 모두 지운다."""
        changed = False
        for note, _at, _by in self.repo.list_trash():
            if self._effective_owner_for_note(note) in (None, session.nickname):
                changed = self.repo.purge_note(note.id) or changed
        if changed:
            self._broadcast(make(MSG_TRASH_CHANGED))

    def purge_old_trash(self) -> None:
        """보관 기간이 지난 휴지통의 메모를 메모 파일에서 지운다."""
        cutoff = now_ms() - config.TRASH_KEEP_DAYS * 24 * 60 * 60 * 1000
        try:
            gone = self.repo.purge_trash(cutoff)
        except sqlite3.Error:
            return          # 파일이 잠겨 있다. 다음 차례에 다시 한다
        if gone:
            self._broadcast(make(MSG_TRASH_CHANGED))

    def _handle_set_folder(self, session: _Session, note_id: str,
                            folder_id: str | None) -> None:
        existing = self.repo.get_note(note_id)
        if existing is None or existing.deleted:
            return
        owner = self._effective_owner_for_note(existing)
        if owner and owner != session.nickname:
            self._send_to(session, make(MSG_ERROR,
                message="not allowed"))
            return
        if self._notice_owner_blocking(note_id, session):
            self._send_back(session, existing)
            return
        target_folder = (self.repo.get_folder(folder_id)
                         if folder_id else None)
        if folder_id and (target_folder is None or target_folder.deleted):
            return
        if (target_folder and target_folder.private_owner
                and target_folder.private_owner != session.nickname):
            self._send_to(session, make(MSG_ERROR,
                message="cannot move into another user's private folder"))
            return
        # 옮긴 뒤 누가 보게 되는가. 달라지는데 다른 사람이 편집 중이면 거절한다
        # (그 사람의 창이 쓰던 글과 함께 닫힌다).
        next_owner = existing.private_owner or (
            target_folder.private_owner if target_folder else None)
        if next_owner != owner and self._locked_by_other(note_id, session):
            self._send_back(session, existing)
            return
        updated = self.repo.set_note_folder(
            note_id, folder_id, session.nickname)
        if updated is None:
            return
        # 비공개 폴더로 들어가거나 나오면 메모의 실효 소유자가 뒤집힐 수 있다.
        new_owner = self._effective_owner_for_note(updated)
        self._propagate_audience_change(
            note_id, updated.to_dict(),
            prev_owner=owner, new_owner=new_owner, actor=session.nickname)

    def _handle_set_privacy(self, session: _Session, note_id: str,
                             private: bool) -> None:
        existing = self.repo.get_note(note_id)
        if existing is None or existing.deleted:
            return
        if self._notice_owner_blocking(note_id, session):
            self._send_back(session, existing)
            return
        # 공지는 나만 보는 메모가 될 때 _propagate_audience_change 가 지운다.
        owner = self._effective_owner_for_note(existing)
        # 이미 비공개인 메모는 소유자만 바꿀 수 있으나 공개 메모는 누구든 비공개로 전환할 수 있다.
        if owner and owner != session.nickname:
            self._send_to(session, make(MSG_ERROR, message="not allowed"))
            return
        if self._locked_by_other(note_id, session):
            self._send_back(session, existing)
            return
        new_owner_value = session.nickname if private else None
        updated = self.repo.set_note_private_owner(
            note_id, new_owner_value, session.nickname)
        if updated is None:
            return
        new_effective = self._effective_owner_for_note(updated)
        self._propagate_audience_change(
            note_id, updated.to_dict(),
            prev_owner=owner, new_owner=new_effective, actor=session.nickname)

    def _locked_by_other(self, note_id: str, session: _Session) -> bool:
        """다른 사람이 편집 중인 메모인가. 그렇다면 요청한 쪽에 까닭을 알린다."""
        holder = self.locks.holder(note_id)
        if not holder or holder == session.nickname:
            return False
        self._send_to(session, make(MSG_ERROR,
                                    message=f"note locked by {holder}"))
        return True

    def _send_back(self, session: _Session, note: Note) -> None:
        """청한 일을 거절했다. 청한 쪽은 화면을 먼저 바꿔 놓았으므로(나만 보기
        표시, 폴더) 지금 상태를 보내 되돌리게 한다."""
        self._send_to(session, make(MSG_NOTE_UPDATED, note=note.to_dict()))

    def _handle_folder_create(self, session: _Session, d: dict) -> None:
        private_owner = session.nickname if d.get("private") else None
        folder = Folder.new(
            user=session.nickname,
            name=str(d.get("name") or "새 폴더"),
            color=d.get("color"),
            private_owner=private_owner,
        )
        if d.get("id"):
            folder.id = str(d["id"])
        self.repo.insert_folder(folder)
        self._broadcast_for_owner(
            make(MSG_FOLDER_CREATED, folder=folder.to_dict()),
            private_owner=private_owner)

    def _handle_folder_update(self, session: _Session, d: dict) -> None:
        folder_id = str(d.get("id") or "")
        existing = self.repo.get_folder(folder_id) if folder_id else None
        if existing is None or existing.deleted:
            return
        if (existing.private_owner
                and existing.private_owner != session.nickname):
            self._send_to(session, make(MSG_ERROR, message="not allowed"))
            return
        # ``private`` 는 3상태다. 키가 없으면 프라이버시를 그대로 두고, 있으면 그 값으로 갱신한다.
        kwargs: dict = {}
        if "name" in d:
            kwargs["name"] = str(d["name"])
        if "color" in d:
            kwargs["color"] = str(d["color"])
        if "private" in d:
            kwargs["private_owner"] = (
                session.nickname if d["private"] else None)
            if kwargs["private_owner"] != existing.private_owner:
                # 폴더를 나만 보기로 바꾸면 그 안의 메모가 남에게서 사라진다.
                # 그 가운데 다른 사람이 편집 중인 것이 있으면 거절한다.
                for note in self.repo.list_notes_in_folder(folder_id):
                    if self._locked_by_other(note.id, session):
                        # 청한 쪽이 먼저 바꿔 놓은 폴더 표시를 되돌리게 한다.
                        self._send_to(session, make(
                            MSG_FOLDER_UPDATED, folder=existing.to_dict()))
                        return
                if existing.private_owner:
                    # 공개로 바꾼다. 이 폴더에서 지운 메모는 계속 주인만 본다.
                    self.repo.keep_deleted_notes_private(
                        folder_id, existing.private_owner)
        updated = self.repo.update_folder(folder_id, **kwargs)
        if updated is None:
            return
        # 폴더 프라이버시가 바뀌면 폴더를 상속한 메모의 가시성도 바뀌므로 함께 재브로드캐스트한다.
        prev_owner = existing.private_owner
        next_owner = updated.private_owner
        if prev_owner == next_owner:
            self._broadcast_for_owner(
                make(MSG_FOLDER_UPDATED, folder=updated.to_dict()),
                private_owner=next_owner)
        else:
            # 계속 보는 사람(폴더의 주인)에게는 고친 것으로 알리고, 보게 되거나
            # 못 보게 되는 사람에게만 만들기와 지우기를 보낸다. 주인에게도
            # 지웠다 다시 만들어 보내면 그 PC 에서 폴더와 메모의 자리, 크기,
            # 접힘이 모두 처음으로 돌아간다.
            keeper = prev_owner or next_owner
            self._send_to_nickname(
                keeper, make(MSG_FOLDER_UPDATED, folder=updated.to_dict()))
            self._broadcast_to_all_except_nickname(
                make(MSG_FOLDER_DELETED if next_owner else MSG_FOLDER_CREATED,
                     **({"id": folder_id} if next_owner
                        else {"folder": updated.to_dict()})),
                except_nickname=keeper)
            for note in self.repo.list_notes_in_folder(folder_id):
                # 폴더에 기대어 보이던 메모만 보는 사람이 바뀐다. 제 주인이 따로
                # 적힌 메모는 그대로다.
                self._propagate_audience_change(
                    note.id, note.to_dict(),
                    prev_owner=note.private_owner or prev_owner,
                    new_owner=self._effective_owner_for_note(note),
                    actor=session.nickname)

    def _handle_folder_delete(self, session: _Session,
                               folder_id: str) -> None:
        existing = self.repo.get_folder(folder_id)
        if existing is None or existing.deleted:
            return
        if (existing.private_owner
                and existing.private_owner != session.nickname):
            self._send_to(session, make(MSG_ERROR, message="not allowed"))
            return
        # 메모가 폴더에서 분리되기 전에 목록을 캡처해야 새 가시성으로 다시 브로드캐스트할 수 있다.
        contained = self.repo.list_notes_in_folder(folder_id)
        prev_owner = existing.private_owner
        if prev_owner:
            # 나만 보던 폴더를 지운다고 그 안의 메모가 모두에게 공개되면 안 된다.
            # 폴더에 기대어 비공개였던 메모에 주인을 직접 적어 둔다.
            for note in contained:
                if not note.private_owner:
                    self.repo.set_note_private_owner(
                        note.id, prev_owner, session.nickname)
            # 이 폴더에서 이미 지운 메모도 같다(지운 글이 이력에 남아 있다).
            self.repo.keep_deleted_notes_private(folder_id, prev_owner)
        deleted_folder = self.repo.soft_delete_folder(folder_id)
        if deleted_folder is None:
            return
        self._broadcast_for_owner(
            make(MSG_FOLDER_DELETED, id=folder_id),
            private_owner=prev_owner)
        # 비공개 폴더에서 프라이버시를 상속하던 메모는 이제 자기 private_owner 만 따른다.
        for note in contained:
            refreshed = self.repo.get_note(note.id)
            if refreshed is None:
                continue
            new_eff = self._effective_owner_for_note(refreshed)
            if prev_owner == new_eff:
                self._broadcast_for_owner(
                    make(MSG_NOTE_UPDATED, note=refreshed.to_dict()),
                    private_owner=new_eff)
            else:
                self._broadcast_for_owner(
                    make(MSG_NOTE_DELETED, id=refreshed.id),
                    private_owner=prev_owner)
                created = (self._shared_note(refreshed.to_dict(), session.nickname)
                           if prev_owner is not None and new_eff is None
                           else make(MSG_NOTE_CREATED, note=refreshed.to_dict()))
                self._broadcast_for_owner(created, private_owner=new_eff)

    # ── 공지, 읽음, @이름 (1.2) ──

    def _notice_owner_blocking(self, note_id: str, session: _Session) -> str:
        """공지라서 이 사람이 고치거나 지우거나 옮길 수 없으면 만든 사람의 이름.
        나만 보는 메모는 공지로 막지 않는다(그 주인만 보는 메모다)."""
        owner = self.repo.list_notices().get(note_id, "")
        if not owner or owner == session.nickname:
            return ""
        note = self.repo.get_note(note_id)
        if note is None or self._effective_owner_for_note(note) is not None:
            return ""
        return owner

    def _handle_set_notice(self, session: _Session, d: dict) -> None:
        """공지로 만드는 것은 메모를 만든 사람만(모두가 보는 메모일 때). 푸는 것은
        만든 사람과 공유 PC 사용자(만든 사람이 떠났을 때를 위해)."""
        note_id = d.get("note_id")
        note = (self.repo.get_note(note_id)
                if isinstance(note_id, str) and note_id else None)
        if note is None or note.deleted:
            return
        on = d.get("on") is True
        current = self.repo.list_notices().get(note_id)
        if on:
            if (note.created_by != session.nickname
                    or self._effective_owner_for_note(note) is not None):
                self._send_to(session, make(MSG_ERROR, message="not allowed"))
                return
            if current == session.nickname:
                return
            # 다른 사람이 편집 중이면 공지로 만들지 않는다(그 사람의 저장이 막힌다).
            if self._locked_by_other(note_id, session):
                return
            self.repo.set_notice(note_id, session.nickname)
            owner = session.nickname
        else:
            if current is None:
                return
            if current != session.nickname and session is not self._local:
                self._send_to(session, make(MSG_ERROR, message="not allowed"))
                return
            self.repo.delete_notice(note_id)
            owner = None
        self._broadcast_for_note(note_id, make(
            MSG_NOTICE_CHANGED, note_id=note_id, owner=owner, by=session.nickname))

    def _handle_seen(self, session: _Session, d: dict) -> None:
        """공지를 봤다. 만든 사람에게 본 기록을 보낸다(다른 사람에게는 보내지 않는다)."""
        note_id = d.get("note_id")
        if not isinstance(note_id, str):
            return
        owner = self.repo.list_notices().get(note_id)
        note = self.repo.get_note(note_id)
        if (not owner or owner == session.nickname or note is None or note.deleted
                or self._effective_owner_for_note(note) is not None):
            return
        try:
            version = int(d.get("version"))
        except (TypeError, ValueError):
            return
        self.repo.set_seen(note_id, session.nickname, max(1, min(version, note.version)))
        self._send_to_nickname(owner, make(
            MSG_SEEN_CHANGED, note_id=note_id, seen=self.repo.list_seen(note_id)))

    def _widget_note_ok(self, session: _Session, note_id) -> bool:
        note = (self.repo.get_note(note_id)
                if isinstance(note_id, str) and note_id else None)
        return (note is not None and not note.deleted
                and self._effective_owner_for_note(note) in (None, session.nickname))

    def _handle_widget_put(self, session: _Session, d: dict) -> None:
        """메모에 위젯을 넣었다. 만든 사람은 보낸 사람이다(보낸 값을 믿지 않는다)."""
        from .. import widgets
        note_id, wid, data = d.get("note_id"), d.get("widget_id"), d.get("data")
        if not widgets.valid_id(wid) or not isinstance(data, dict):
            self._send_to(session, make(MSG_WIDGET_ERROR, widget_id=str(wid or ""),
                                         error="invalid"))
            return
        if not self._widget_note_ok(session, note_id):
            self._send_to(session, make(MSG_WIDGET_ERROR, widget_id=wid, error="not_allowed"))
            return
        if self.repo.get_widget(wid) is not None:
            self._send_to(session, make(MSG_WIDGET_ERROR, widget_id=wid, error="exists"))
            return
        # 메모 하나에 넣을 수 있는 위젯의 수. 지운 위젯의 데이터도 메모가 완전히 지워질
        # 때까지 남으므로, 끝없이 쌓이지 않게 막는다.
        if self.repo.count_widgets(note_id) >= MAX_WIDGETS_PER_NOTE:
            self._send_to(session, make(MSG_WIDGET_ERROR, widget_id=wid, error="limit"))
            return
        kind = widgets.kind_of(data)
        try:
            if kind is None:
                raise widgets.WidgetError("invalid")
            clean = kind.validate(dict(data, owner=session.nickname))
        except widgets.WidgetError as error:
            self._send_to(session, make(MSG_WIDGET_ERROR, widget_id=wid, error=error.key))
            return
        self.repo.put_widget(wid, note_id, clean)
        self._broadcast_for_note(note_id, make(
            MSG_WIDGET_CHANGED, note_id=note_id, widget_id=wid, data=clean,
            by=session.nickname))

    def _handle_widget_op(self, session: _Session, d: dict) -> None:
        """위젯을 눌렀다(한 표, 한 수). 받은 차례대로 하나씩 적용한다."""
        from .. import widgets
        wid, op = d.get("widget_id"), d.get("op")
        stored = self.repo.get_widget(wid) if widgets.valid_id(wid) else None
        if stored is None or not isinstance(op, dict):
            self._send_to(session, make(MSG_WIDGET_ERROR, widget_id=str(wid or ""),
                                         error="not_found"))
            return
        note_id, data = stored
        if not self._widget_note_ok(session, note_id):
            self._send_to(session, make(MSG_WIDGET_ERROR, widget_id=wid, error="not_allowed"))
            return
        kind = widgets.kind_of(data)
        if kind is None:
            return
        new, error = kind.apply_op(data, kind.prepare_op(op), session.nickname, now_ms())
        if error:
            self._send_to(session, make(MSG_WIDGET_ERROR, widget_id=wid, error=error))
            return
        self.repo.put_widget(wid, note_id, new)
        self._broadcast_for_note(note_id, make(
            MSG_WIDGET_CHANGED, note_id=note_id, widget_id=wid, data=new,
            by=session.nickname))

    def _known_users(self) -> set[str]:
        """'@이름' 으로 부를 수 있는 사람: 붙은 적이 있는 사람과 메모를 만들거나 고친 사람."""
        names = set(self._current_users()) | set(self.repo.list_participants())
        names |= self.repo.list_authors()
        names.discard("")
        return names

    def _send_mentions(self, before: Note, after: Note, by: str) -> None:
        """고친 글에 새로 생긴 '@이름' 을 그 사람에게 알린다. 꺼져 있으면 적어 두었다가
        다음 접속(welcome)에 보낸다. 모두가 보는 메모만."""
        if self._effective_owner_for_note(after) is not None:
            return
        if "@" not in (after.content or ""):
            return              # 저장할 때마다 사람 목록을 읽지 않는다
        for name in mentions.new_mentions(before.content or "", after.content or "",
                                          self._known_users()):
            if name == by:
                continue
            if self._has_session(name):
                self._send_to_nickname(name, make(
                    MSG_MENTIONED, note_id=after.id, by=by, at=now_ms()))
            else:
                self.repo.add_mention(name, after.id, by)

    def _handle_set_reminder(self, session: _Session, d: dict) -> None:
        """공유 알림을 걸거나 바꾸거나 지운다. 그 메모를 볼 수 있는 사람이면 누구나
        한다(메모 글을 고칠 수 있는 것과 같다). 그 메모를 보는 모두에게 알린다."""
        note_id = d.get("note_id")
        note = (self.repo.get_note(note_id)
                if isinstance(note_id, str) and note_id else None)
        if (note is None or note.deleted
                or self._effective_owner_for_note(note) not in (None, session.nickname)):
            self._send_to(session, make(MSG_ERROR, message="not allowed"))
            return
        raw = d.get("reminder")
        if raw is None:
            if not self.repo.delete_reminder(note_id):
                return
            entry = None
        else:
            entry = (rm.shared_from_dict({"rule": raw.get("rule"),
                                          "targets": raw.get("targets", []),
                                          "set_by": session.nickname,
                                          "set_at": now_ms()})
                     if isinstance(raw, dict) else None)
            if entry is None:
                self._send_to(session, make(MSG_ERROR, message="bad reminder"))
                return
            self.repo.set_reminder(note_id, entry)
        self._broadcast_for_note(note_id, make(
            MSG_REMINDER_CHANGED, note_id=note_id, reminder=entry,
            by=session.nickname, done=bool(d.get("done"))))

    def _handle_get_history(self, session: _Session, note_id: str | None) -> None:
        if (session.socket is not None
                and session.socket.bytesToWrite() > MAX_QUEUED_REPLY):
            return      # 앞서 보낸 답도 아직 받아 가지 않았다
        visible_ids = {n.id
                       for n in self._visible_notes_for(session.nickname)}
        # soft-delete 된 메모도 포함하지 않으면 그 삭제 이력이 사라진다.
        for n in self.repo.list_notes(include_deleted=True):
            if n.deleted and self._effective_owner_for_note(n) in (
                    None, session.nickname):
                visible_ids.add(n.id)
        entries = []
        for e in self.repo.list_history(note_id):
            if e.note_id in visible_ids:
                entries.append(e.to_dict())
        self._send_to(session, make(MSG_HISTORY_LIST,
                                     note_id=note_id or "",
                                     entries=entries))

    def _on_heartbeat(self) -> None:
        self._broadcast(make(MSG_PING, ts=now_ms()))

    def _on_idle_reap(self) -> None:
        holders = self.locks.snapshot()
        for nid in self.locks.reap_idle():
            self._forget_held(nid)
            # 거두기 전에 저장된 글은 이력에 남긴다. 그냥 두면 같은 사람이 잠금을
            # 다시 받을 때 그 편집의 시작점이 지금 글로 바뀌어 이력에서 빠진다.
            self._close_edit(nid, holders.get(nid, ""), reopen=True)
            self._broadcast_for_note(nid, make(MSG_LOCK_RELEASED, note_id=nid))
