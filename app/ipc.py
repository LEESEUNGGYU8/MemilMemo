"""단일 인스턴스 IPC.

명명된 로컬 서버를 가장 먼저 바인딩한 프로세스가 점프리스트 명령의 수신자가 되고,
이후 실행된 프로세스('--new-note' 등)는 명령을 그쪽으로 넘기고 종료한다.
바인딩에 실패한 나머지 인스턴스도 평소대로 동작한다."""
from __future__ import annotations

import ctypes
import getpass
import hashlib
import os
import sys
import time

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from . import config

# 작업 표시줄 목록과 바탕 화면 우클릭 메뉴의 '새 메모'가 실행 파일에 넘기는 표시.
NEW_NOTE_FLAG = "--new-note"
# 우클릭한 자리 근처에 메모를 띄우라는 표시(바탕 화면 우클릭 메뉴만 붙인다).
AT_CURSOR_FLAG = "--at-cursor"

# 떠 있는 인스턴스에 넘기는 명령.
CMD_NEW_NOTE = "new-note"
CMD_NEW_NOTE_HERE = "new-note-here"
CMD_SHOW = "show"
# 명령을 읽은 인스턴스가 보낸 쪽에 돌려주는 답.
ACK = b"ok\n"


def new_note_command(argv: list[str]) -> str | None:
    """실행 인자가 '새 메모' 요청이면 넘길 명령을, 아니면 None 을 돌려준다."""
    if NEW_NOTE_FLAG not in argv:
        return None
    return CMD_NEW_NOTE_HERE if AT_CURSOR_FLAG in argv else CMD_NEW_NOTE


def _allow_foreground_handoff() -> None:
    """명령을 받는 쪽이 자기 창을 앞으로 가져올 수 있게 허락한다.

    Windows 는 사용자가 방금 조작한 프로세스만 창을 앞에 세울 수 있게 한다.
    사용자가 누른 것은 지금 막 뜬 이 프로세스이고 메모를 띄우는 것은 이미 떠
    있던 프로세스라서, 넘겨주지 않으면 새 메모가 글쇠 입력을 받지 못한다."""
    if sys.platform != "win32":
        return
    try:
        ASFW_ANY = 0xFFFFFFFF
        ctypes.windll.user32.AllowSetForegroundWindow(ASFW_ANY)
    except Exception:
        pass


def ipc_name() -> str:
    """다른 Windows 계정과 충돌하지 않도록 사용자별 파이프 이름을 만든다."""
    try:
        user = getpass.getuser() or "default"
    except Exception:
        user = "default"
    # Windows 명명된 파이프는 경로 문자에 민감하므로 안전한 문자만 남긴다.
    safe = "".join(ch if ch.isalnum() else "_" for ch in user)
    name = f"SyncNotes-IPC-{safe}"
    if config.isolated_run():
        # 데이터 폴더를 따로 쓰는 시험 실행은 실제 앱의 '새 메모'를 가로채지 않게
        # 이름도 따로 쓴다. 같은 폴더로 띄운 두 번째 실행끼리는 이름이 같다.
        tag = hashlib.sha1(
            os.environ.get("APPDATA", "").encode("utf-8", "ignore")).hexdigest()
        name += f"-{tag[:10]}"
    return name


def forward_command(command: str, timeout_ms: int = 500,
                    reply_timeout_ms: int = 5000) -> bool:
    """이미 실행 중인 인스턴스에 ``command`` 를 전달한다.

    아무도 듣고 있지 않으면 False를 돌려주며, 이때 호출 측은 정상 부팅을 이어가면 된다.

    받는 쪽이 읽었다는 답(ACK)을 보낼 때까지 기다린 뒤 끊는다. 파이프에 쓴 내용은
    받는 쪽이 읽어야 넘어가는데, 받는 쪽이 잠깐 바쁜 사이에 이쪽이 먼저 끊고
    끝나 버리면 명령이 사라진다(1.0.6 까지는 0.5초 안에 읽히지 않으면 사라졌다).
    답이 끝내 오지 않아도 True 다. 떠 있는 인스턴스가 있는 것은 분명하므로 앱을
    하나 더 켜는 쪽이 더 나쁘다."""
    socket = QLocalSocket()
    socket.connectToServer(ipc_name())
    if not socket.waitForConnected(timeout_ms):
        return False
    _allow_foreground_handoff()
    socket.write((command + "\n").encode("utf-8"))
    socket.flush()
    deadline = time.monotonic() + reply_timeout_ms / 1000
    while socket.state() == QLocalSocket.ConnectedState:
        left_ms = int((deadline - time.monotonic()) * 1000)
        if left_ms <= 0:
            break
        if socket.bytesAvailable() or socket.waitForReadyRead(left_ms):
            if ACK in bytes(socket.readAll()):
                break
    socket.disconnectFromServer()
    return True


class IpcReceiver(QObject):
    commandReceived = Signal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_new_connection)
        self._is_owner = False

    def listen(self) -> bool:
        """IPC 이름을 잡아 명령 수신자가 되면 True를 반환한다."""
        name = ipc_name()
        # 이전 프로세스가 비정상 종료하며 남긴 stale 핸들을 정리한 뒤 다시 시도한다.
        if not self._server.listen(name):
            QLocalServer.removeServer(name)
            if not self._server.listen(name):
                return False
        self._is_owner = True
        return True

    def stop(self) -> None:
        self._server.close()

    def is_owner(self) -> bool:
        return self._is_owner

    def _on_new_connection(self) -> None:
        while True:
            sock = self._server.nextPendingConnection()
            if sock is None:
                return
            sock.readyRead.connect(lambda s=sock: self._on_ready_read(s))
            sock.disconnected.connect(sock.deleteLater)
            # 연결을 집어 들기 전에 이미 도착한 내용은 readyRead 가 다시 오지 않는다.
            if sock.bytesAvailable():
                self._on_ready_read(sock)

    def _on_ready_read(self, sock: QLocalSocket) -> None:
        data = bytes(sock.readAll()).decode("utf-8", errors="ignore")
        commands = [line.strip() for line in data.splitlines() if line.strip()]
        if not commands:
            return
        # 보낸 쪽은 이 답을 받고 끝난다. 명령을 처리하기 전에 먼저 보낸다
        # (처리 중에 창이 뜨는 등 시간이 걸릴 수 있다).
        sock.write(ACK)
        sock.flush()
        for command in commands:
            self.commandReceived.emit(command)
