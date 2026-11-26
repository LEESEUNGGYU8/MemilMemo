from __future__ import annotations

import time

from PySide6.QtCore import QObject, QTimer
from PySide6.QtNetwork import QAbstractSocket, QTcpSocket

from ..models import new_id
from ..protocol import (
    FrameParser,
    MSG_ACQUIRE_LOCK,
    MSG_AUTH,
    MSG_AUTH_FAILED,
    MSG_AUTH_REQUIRED,
    MSG_CREATE_FOLDER,
    MSG_CREATE_NOTE,
    MSG_DELETE_FOLDER,
    MSG_DELETE_NOTE,
    MSG_EMPTY_TRASH,
    MSG_GET_HISTORY,
    MSG_GET_TRASH,
    MSG_HELLO,
    MSG_LOCK_DENIED,
    MSG_PING,
    MSG_PONG,
    MSG_PURGE_NOTE,
    MSG_RELEASE_LOCK,
    MSG_RESTORE_NOTE,
    MSG_SET_NOTE_FOLDER,
    MSG_SET_NOTE_PRIVACY,
    MSG_SET_REMINDER,
    MSG_SET_NOTICE,
    MSG_SEEN,
    MSG_WIDGET_PUT,
    MSG_WIDGET_OP,
    MSG_UPDATE_FOLDER,
    MSG_UPDATE_NOTE,
    MSG_WELCOME,
    Message,
    make,
)
from ..share_auth import ShareAuth, guest_proof, host_proof, new_nonce, same, valid_nonce
from .client_base import ClientBase


RECONNECT_BACKOFF_MS = (1000, 2000, 4000, 8000)
# 끝낼 때 아직 보내지 못한 것을 내보내느라 기다리는 한도.
STOP_FLUSH_MS = 1500
# 끝낼 때, 보낸 것을 공유 PC 가 처리했다는 답을 기다리는 한도.
STOP_ANSWER_MS = 700


class GuestClient(ClientBase):
    def __init__(self, host: str, port: int, nickname: str,
                 parent: QObject | None = None):
        super().__init__(nickname, parent)
        self._host = host
        self._port = port
        self._sock = QTcpSocket(self)
        self._parser = FrameParser()
        self._sock.readyRead.connect(self._on_ready_read)
        self._sock.connected.connect(self._on_connected)
        self._sock.disconnected.connect(self._on_disconnected)
        self._sock.errorOccurred.connect(self._on_socket_error)

        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setSingleShot(True)
        self._reconnect_timer.timeout.connect(self._do_connect)
        self._reconnect_attempt = 0
        self._stopped = False
        # 사용자가 답할 때까지 붙지 않고 기다리는 중인가(암호를 넣지 않았다,
        # 공유 PC 를 믿을 수 없다).
        self._paused = False
        # 이 연결에서 첫 상태(welcome)를 받았는가. 받아야 '연결됨'이다.
        self._joined = False

        # 참여 암호(app/share_auth.py).
        self._auth: ShareAuth | None = None      # 적어 둔 열쇠(붙던 공유 PC 의 것)
        self._password: str | None = None        # 방금 넣은 암호. 다음 물음에 쓴다
        self._challenge: dict | None = None      # 공유 PC 가 낸 물음
        self._proving: tuple[ShareAuth, str] | None = None   # 보낸 증명의 열쇠와 내 값
        self._typed = False                      # 보낸 증명이 방금 넣은 암호의 것인가
        self._awaiting = False                   # 사용자에게 암호를 물어 둔 상태인가
        self._hold_until = 0.0                   # 너무 틀려 기다리는 때
        # 찾아낸 새 주소를 시험하는 중이면 돌아갈 주소.
        self._trial: tuple[str, int] | None = None
        # 다른 곳에 붙으려고 지금 연결을 스스로 끊는 중인가.
        self._switching = False
        # 끝내는 길에 보낸 마지막 물음의 표시. 그 답이 오면 닫아도 된다.
        self._bye = ""

    def start(self) -> None:
        self._stopped = False
        self._paused = False
        self._do_connect()

    def stop(self) -> None:
        self._stopped = True
        self._reconnect_timer.stop()
        if self._sock.state() != QAbstractSocket.UnconnectedState:
            self._flush()
            self._sock.disconnectFromHost()

    def _flush(self) -> None:
        """보내려고 쌓아 둔 것을 지금 내보낸다.

        소켓은 이벤트 루프가 돌 때 보낸다. 프로그램이 끝나는 길에는 루프가 더
        돌지 않아, 방금 쓴 글(마지막 저장과 잠금 반납)이 보내지 못한 채 소켓과
        함께 사라진다."""
        sock = self._sock
        if self._joined and sock.state() == QAbstractSocket.ConnectedState:
            # 맨 끝에 없는 메모의 잠금을 청한다. 공유 PC 는 받은 차례대로 처리하므로,
            # 거절하는 답이 오면 그 앞에 보낸 것은 모두 처리된 것이다(옛 버전의
            # 공유 PC 도 같은 답을 한다). 답을 기다리지 않고 닫으면, 닫는 순간에
            # 받지 않은 것이 남아 있을 때 Windows 가 연결을 끊어 버리고(RST),
            # 공유 PC 는 아직 읽지 않은 마지막 글을 잃는다.
            self._bye = "bye-" + new_nonce()
            sock.write(make(MSG_ACQUIRE_LOCK, note_id=self._bye).encode())
        deadline = time.monotonic() + STOP_FLUSH_MS / 1000
        while (sock.state() == QAbstractSocket.ConnectedState
               and sock.bytesToWrite() > 0 and time.monotonic() < deadline):
            sock.flush()
            if sock.bytesToWrite() > 0:
                sock.waitForBytesWritten(100)
        deadline = time.monotonic() + STOP_ANSWER_MS / 1000
        while (self._bye and sock.state() == QAbstractSocket.ConnectedState
               and time.monotonic() < deadline):
            sock.waitForReadyRead(50)
            self._read_until_bye()
        self._bye = ""

    def _read_until_bye(self) -> None:
        """끝내는 길이다. 받은 것은 버리고 마지막 물음의 답만 찾는다."""
        for msg in self._parser.feed(bytes(self._sock.readAll())):
            if (msg.type == MSG_LOCK_DENIED
                    and msg.data.get("note_id") == self._bye):
                self._bye = ""

    # ── 주소 ──

    def address(self) -> tuple[str, int]:
        return self._host, self._port

    def set_address(self, host: str, port: int) -> None:
        """붙을 공유 PC 를 바꾸고 곧바로 다시 붙는다."""
        self._trial = None
        self._host, self._port = host, int(port)
        self._reconnect_now()

    def follow(self, host: str, port: int) -> None:
        """찾아낸 주소가 내가 붙던 공유 PC 인지 붙어 본다.

        암호로 서로를 확인한 경우에만 그 주소로 옮긴다(followed). 아니면 묻지
        않고 원래 주소로 돌아간다(followFailed). 암호가 없는 공유 PC 에는 쓰지
        않는다: 확인할 방법이 없으므로 사용자에게 물은 뒤 set_address 를 쓴다."""
        if self._auth is None or self._trial is not None:
            return
        self._trial = (self._host, self._port)
        self._host, self._port = host, int(port)
        self._reconnect_now()

    def _fail_trial(self) -> None:
        if self._trial is None:
            return
        tried = (self._host, self._port)
        (self._host, self._port), self._trial = self._trial, None
        self.followFailed.emit(*tried)
        self._reconnect_now()

    def _reconnect_now(self) -> None:
        self._reconnect_timer.stop()
        self._reconnect_attempt = 0
        if not self._stopped and not self._paused:
            self._do_connect()

    # ── 참여 암호 ──

    def set_auth(self, auth: ShareAuth | None) -> None:
        """붙던 공유 PC 의 열쇠. 있으면 그 공유 PC 가 암호를 묻지 않거나 암호를
        모르는 것으로 드러날 때 받은 메모를 믿지 않는다."""
        self._auth = auth

    def submit_password(self, password: str) -> None:
        """사용자가 넣은 암호로 증명한다. 끊겨 있으면 다시 붙어서 한다."""
        self._password = password
        self._awaiting = False
        self._paused = False
        self._stopped = False
        if (self._sock.state() == QAbstractSocket.ConnectedState
                and self._challenge is not None and not self._joined):
            self._answer(self._challenge, wrong=False)
        elif self._sock.state() == QAbstractSocket.UnconnectedState:
            self._schedule_reconnect(soon=True)

    def pause(self) -> None:
        """다시 붙으려 들지 않고 기다린다(사용자가 암호 넣기를 그만뒀다)."""
        self._paused = True
        self._awaiting = False
        self._reconnect_timer.stop()
        self._sock.abort()

    def resume(self) -> None:
        self._paused = False
        self._stopped = False
        self._reconnect_now()

    def _answer(self, challenge: dict, *, wrong: bool) -> None:
        """공유 PC 의 물음에 답한다. 답할 열쇠가 없으면 사용자에게 묻게 한다."""
        self._challenge = challenge
        salt, count = challenge.get("salt"), challenge.get("iterations")
        if not valid_nonce(challenge.get("nonce")):
            return
        if self._password is not None:
            auth = ShareAuth.for_challenge(self._password, salt, count)
            self._password = None
            if auth is not None:
                self._prove(auth, typed=True)
                return
        if not wrong and self._auth is not None and self._auth.matches(salt, count):
            self._prove(self._auth, typed=False)
            return
        if self._trial is not None:
            # 찾아간 곳이 내가 알던 암호의 공유 PC 가 아니다. 묻지 않고 돌아간다.
            self._fail_trial()
            return
        if not self._awaiting:
            self._awaiting = True
            self.passwordRequired.emit({"wrong": wrong, "wait": 0})

    def _prove(self, auth: ShareAuth, *, typed: bool) -> None:
        nonce = new_nonce()
        self._proving, self._typed = (auth, nonce), typed
        self._send(make(MSG_AUTH, nonce=nonce, proof=guest_proof(
            auth.key, self._challenge["nonce"], nonce, self._nickname)))

    def _on_auth_failed(self, data: dict) -> None:
        typed, self._proving = self._typed, None
        if self._trial is not None:
            self._fail_trial()
            return
        try:
            wait = max(0, min(int(data.get("wait") or 0), 3600))
        except (TypeError, ValueError):
            wait = 0
        if wait:
            # 너무 틀렸다. 그때까지는 다시 붙어도 받아 주지 않는다.
            self._hold_until = time.monotonic() + wait
        if valid_nonce(data.get("nonce")):
            self._challenge = {"salt": data.get("salt"),
                               "iterations": data.get("iterations"),
                               "nonce": data.get("nonce")}
        else:
            self._challenge = None
        if not typed:
            # 적어 둔 열쇠가 맞지 않는다: 공유 PC 가 암호를 바꿨다.
            self._auth = None
        if not self._awaiting:
            self._awaiting = True
            self.passwordRequired.emit({"wrong": typed, "wait": wait})

    def _accept_welcome(self, data: dict) -> bool:
        """첫 상태를 받아들여도 되는가. 암호를 걸어 둔 공유 PC 라면 그쪽도 암호를
        안다는 증명이 맞아야 한다."""
        if self._proving is not None:
            (auth, nonce), self._proving = self._proving, None
            expected = host_proof(auth.key, (self._challenge or {}).get("nonce", ""),
                                  nonce)
            if not same(data.get("auth"), expected):
                self._refuse(self.hostUnverified)
                return False
            if auth != self._auth:
                self._auth = auth
                self.passwordAccepted.emit(auth.to_dict())
        elif self._auth is not None:
            # 암호를 걸어 두었던 공유 PC 가 묻지도 않고 메모를 보냈다. 암호를
            # 없앴을 수도, 그 PC 인 척하는 다른 PC 일 수도 있다. 사용자가 정한다.
            self._refuse(self.hostOpen)
            return False
        self._joined = True
        self._awaiting = False
        self._challenge = None
        if self._trial is not None:
            self._trial = None
            self.followed.emit(self._host, self._port)
        self.connectionChanged.emit(True)
        return True

    def _refuse(self, signal) -> None:
        if self._trial is not None:
            self._fail_trial()
            return
        self._paused = True
        self._reconnect_timer.stop()
        self._sock.abort()
        signal.emit()

    # ── 연결 ──

    def _do_connect(self) -> None:
        if self._stopped or self._paused:
            return
        self._parser = FrameParser()
        # 붙어 있던 연결을 끊고 가는 것이면(주소를 바꿨다) 끊김을 한 번 알린다.
        # 소켓이 알리는 끊김은 받지 않는다: 그쪽은 다시 붙기를 예약하고, 새 주소를
        # 시험하는 중이면 시험이 실패한 것으로 안다.
        was_joined = self._joined
        self._switching = True
        try:
            self._sock.abort()
        finally:
            self._switching = False
        self._joined = False
        self._challenge = None
        self._proving = None
        if was_joined:
            self.connectionChanged.emit(False)
        self._sock.connectToHost(self._host, self._port)

    def _schedule_reconnect(self, soon: bool = False) -> None:
        if self._stopped or self._paused:
            return
        if soon:
            delay = 0
        else:
            delay = RECONNECT_BACKOFF_MS[min(self._reconnect_attempt,
                                             len(RECONNECT_BACKOFF_MS) - 1)]
            self._reconnect_attempt += 1
        held = int((self._hold_until - time.monotonic()) * 1000)
        self._reconnect_timer.start(max(delay, held, 0))

    def _on_connected(self) -> None:
        self._reconnect_attempt = 0
        self._joined = False
        self._challenge = None
        self._proving = None
        # '연결됨'은 첫 상태를 받았을 때 알린다(_accept_welcome). 암호를 묻는
        # 공유 PC 에서는 암호를 맞히기 전까지 메모를 받지 못한 상태다.
        # auth=1: 암호를 물으면 답할 줄 안다(옛 버전에는 없는 값).
        self._send(make(MSG_HELLO, nickname=self._nickname, auth=1))

    def _on_disconnected(self) -> None:
        if self._switching:
            return
        self._joined = False
        self._challenge = None
        self._proving = None
        if self._trial is not None:
            self._fail_trial()
            return
        self.connectionChanged.emit(False)
        self._schedule_reconnect()

    def _on_socket_error(self, _err) -> None:
        # 연결돼 있었다면 곧 disconnected 가 재연결을 예약하므로, 미연결
        # 상태(connectToHost 실패)일 때만 여기서 직접 예약한다.
        if self._sock.state() == QAbstractSocket.UnconnectedState:
            if self._trial is not None:
                self._fail_trial()
                return
            # 붙지 못했다는 것을 화면에 알린다(안 알리면 '접속 중'에 머문다).
            self.connectionChanged.emit(False)
            self._schedule_reconnect()

    def _on_ready_read(self) -> None:
        if self._bye:
            self._read_until_bye()
            return
        chunk = bytes(self._sock.readAll())
        for msg in self._parser.feed(chunk):
            if msg.type == MSG_PING:
                self._send(make(MSG_PONG))
                continue
            if not self._joined:
                if msg.type == MSG_AUTH_REQUIRED:
                    self._answer(msg.data, wrong=False)
                    continue
                if msg.type == MSG_AUTH_FAILED:
                    self._on_auth_failed(msg.data)
                    continue
                if msg.type == MSG_WELCOME and not self._accept_welcome(msg.data):
                    return              # 믿지 못할 상대다. 같이 온 것도 버린다
            self._dispatch(msg.type, msg.data)

    def _send(self, msg: Message) -> None:
        if self._sock.state() == QAbstractSocket.ConnectedState:
            self._sock.write(msg.encode())

    def acquire_lock(self, note_id: str) -> None:
        self._send(make(MSG_ACQUIRE_LOCK, note_id=note_id))

    def release_lock(self, note_id: str) -> None:
        self._send(make(MSG_RELEASE_LOCK, note_id=note_id))

    def create_note(self, color: str, width: int, height: int,
                    folder_id: str | None = None,
                    private_owner: str | None = None,
                    quiet: bool = False) -> None:
        payload: dict = dict(id=new_id(), color=color, width=width,
                             height=height, folder_id=folder_id,
                             private_owner=private_owner)
        if quiet:
            payload["quiet"] = True
        self._send(make(MSG_CREATE_NOTE, **payload))

    def set_note_folder(self, note_id: str,
                         folder_id: str | None) -> None:
        self._send(make(MSG_SET_NOTE_FOLDER, id=note_id,
                        folder_id=folder_id))

    def set_note_privacy(self, note_id: str, private: bool) -> None:
        self._send(make(MSG_SET_NOTE_PRIVACY, id=note_id,
                        private=private))

    def create_folder(self, name: str, color: str,
                      private: bool = False,
                      folder_id: str | None = None) -> None:
        self._send(make(MSG_CREATE_FOLDER, id=folder_id or new_id(), name=name,
                        color=color, private=private))

    def update_folder(self, folder_id: str, *,
                       name: str | None = None,
                       color: str | None = None,
                       private: bool | None = None) -> None:
        payload: dict = {"id": folder_id}
        if name is not None:
            payload["name"] = name
        if color is not None:
            payload["color"] = color
        if private is not None:
            payload["private"] = private
        self._send(make(MSG_UPDATE_FOLDER, **payload))

    def delete_folder(self, folder_id: str) -> None:
        self._send(make(MSG_DELETE_FOLDER, id=folder_id))

    def update_note(
        self, note_id: str, content: str, color: str, width: int, height: int,
        expected_version: int,
    ) -> None:
        self._send(make(MSG_UPDATE_NOTE, id=note_id, content=content,
                        color=color, width=width, height=height,
                        expected_version=expected_version))

    def delete_note(self, note_id: str) -> None:
        self._send(make(MSG_DELETE_NOTE, id=note_id))

    def get_history(self, note_id: str | None = None) -> None:
        self._send(make(MSG_GET_HISTORY, note_id=note_id))

    def get_trash(self) -> None:
        self._send(make(MSG_GET_TRASH))

    def restore_note(self, note_id: str) -> None:
        self._send(make(MSG_RESTORE_NOTE, id=note_id))

    def purge_note(self, note_id: str) -> None:
        self._send(make(MSG_PURGE_NOTE, id=note_id))

    def empty_trash(self) -> None:
        self._send(make(MSG_EMPTY_TRASH))

    def set_reminder(self, note_id: str, reminder: dict | None,
                     done: bool = False) -> None:
        payload: dict = {"note_id": note_id, "reminder": reminder}
        if done:
            payload["done"] = True
        self._send(make(MSG_SET_REMINDER, **payload))

    def set_notice(self, note_id: str, on: bool) -> None:
        self._send(make(MSG_SET_NOTICE, note_id=note_id, on=bool(on)))

    def send_seen(self, note_id: str, version: int) -> None:
        self._send(make(MSG_SEEN, note_id=note_id, version=int(version)))

    def put_widget(self, note_id: str, widget_id: str, data: dict) -> None:
        self._send(make(MSG_WIDGET_PUT, note_id=note_id, widget_id=widget_id, data=data))

    def widget_op(self, widget_id: str, op: dict) -> None:
        self._send(make(MSG_WIDGET_OP, widget_id=widget_id, op=op))
