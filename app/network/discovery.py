"""내부망에서 공유 PC 찾기.

참여하려는 PC 가 내부망 전체에 짧은 신호(UDP)를 보내면, 공유 중인 PC 가 자기
이름과 포트로 답한다. 주소를 손으로 넣지 않아도 되고, 공유 PC 의 주소가 바뀌거나
다른 PC 가 공유를 이어받았을 때 다시 찾아갈 수 있다.

* 같은 공유기 아래에서만 닿는다(신호가 공유기를 넘지 않는다). 못 찾으면 전처럼
  주소를 직접 넣는다.
* 답은 누구나 꾸며 낼 수 있다. 여기서 받은 것은 '후보'일 뿐이고, 믿을지는 붙어
  본 뒤 암호로 가리거나 사용자에게 묻는다(메인 창이 한다).
* 옛 버전은 신호에 답하지 않고 보내지도 않는다. 서로 영향이 없다.

Windows 방화벽: 공유 PC 는 공유를 처음 시작할 때 허용을 받는데, 그 허용에 UDP 도
들어 있다. 찾는 쪽은 포트를 정하지 않고 열어(0번) 보내므로 허용 창이 뜨지 않는다."""
from __future__ import annotations

import json
import secrets
import time
from typing import Any, Callable

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import (
    QAbstractSocket,
    QHostAddress,
    QNetworkInterface,
    QUdpSocket,
)

from .. import config

MAGIC = "memilmemo-find"
VERSION = 1
MAX_DATAGRAM = 2048
# 한 번 찾을 때 답을 기다리는 시간, 그 사이 신호를 보내는 횟수(UDP 는 잃을 수 있다).
SEARCH_MS = 1300
SEARCH_SENDS = 2
MAX_FOUND = 16
# 공유 PC 가 1초에 답하는 횟수의 상한. 신호를 퍼부어도 답을 퍼붓지 않는다.
ANSWERS_PER_SECOND = 20


def _text(value: Any, limit: int) -> str:
    return value[:limit] if isinstance(value, str) else ""


def _ipv4(address: QHostAddress) -> str:
    """보낸 쪽 주소를 '192.168.0.12' 꼴로. IPv4 가 아니면 빈 글."""
    ok = False
    try:
        number, ok = address.toIPv4Address()
    except (TypeError, ValueError):
        number = address.toIPv4Address()
        ok = bool(number)
    return QHostAddress(number).toString() if ok and number else ""


def broadcast_targets(port: int) -> list[tuple[str, int]]:
    """신호를 보낼 곳: 내부망 전체. 망마다의 방송 주소에도 보낸다(전체 방송
    주소를 막는 공유기가 있다)."""
    targets = {("255.255.255.255", port)}
    try:
        for iface in QNetworkInterface.allInterfaces():
            flags = iface.flags()
            if not (flags & QNetworkInterface.IsUp
                    and flags & QNetworkInterface.IsRunning) \
                    or flags & QNetworkInterface.IsLoopBack:
                continue
            for entry in iface.addressEntries():
                if entry.ip().protocol() != QAbstractSocket.IPv4Protocol:
                    continue
                cast = entry.broadcast()
                if not cast.isNull():
                    targets.add((cast.toString(), port))
    except Exception:
        pass
    return sorted(targets)


class Responder(QObject):
    """공유 PC 쪽: 찾는 신호에 답한다. ``answer()`` 가 답할 내용을 준다."""

    def __init__(self, answer: Callable[[], dict], port: int,
                 parent: QObject | None = None):
        super().__init__(parent)
        self._answer = answer
        self._port = int(port)
        self._sock: QUdpSocket | None = None
        self._window = 0.0
        self._sent = 0

    def start(self) -> bool:
        """포트를 열지 못해도(다른 프로그램이 쓰고 있다) 공유는 그대로 된다.
        찾기만 안 될 뿐이다."""
        sock = QUdpSocket(self)
        if not sock.bind(QHostAddress(QHostAddress.AnyIPv4), self._port,
                         QUdpSocket.ShareAddress | QUdpSocket.ReuseAddressHint):
            sock.deleteLater()
            return False
        sock.readyRead.connect(self._on_ready)
        self._sock = sock
        return True

    def port(self) -> int:
        return self._sock.localPort() if self._sock is not None else 0

    def stop(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock.deleteLater()
            self._sock = None

    def _allowed(self) -> bool:
        now = time.monotonic()
        if now - self._window >= 1.0:
            self._window, self._sent = now, 0
        self._sent += 1
        return self._sent <= ANSWERS_PER_SECOND

    def _on_ready(self) -> None:
        sock = self._sock
        while sock is not None and sock.hasPendingDatagrams():
            datagram = sock.receiveDatagram(MAX_DATAGRAM)
            try:
                ask = json.loads(bytes(datagram.data()).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            if (not isinstance(ask, dict) or ask.get("app") != MAGIC
                    or not isinstance(ask.get("ask"), str)
                    or not 8 <= len(ask["ask"]) <= 64 or not self._allowed()):
                continue
            try:
                reply = dict(self._answer(), app=MAGIC, v=VERSION, re=ask["ask"])
                payload = json.dumps(reply).encode("utf-8")
            except Exception:
                continue
            sock.writeDatagram(payload, datagram.senderAddress(),
                               datagram.senderPort())


class Searcher(QObject):
    """참여하는 PC 쪽: 신호를 보내고 답을 모은다.

    ``finished`` 는 찾은 공유 PC 들을 준다. 하나는 이런 dict 다:
    ``{"address", "port", "host", "host_id", "took_over_from", "locked"}``."""

    finished = Signal(list)

    def __init__(self, targets: list[tuple[str, int]] | None = None,
                 parent: QObject | None = None):
        super().__init__(parent)
        # None 이면 부를 때마다 내부망 전체로 정한다(망이 바뀔 수 있다).
        self._targets = targets
        self._sock: QUdpSocket | None = None
        self._ask = ""
        self._found: dict[tuple[str, int], dict] = {}
        self._sends_left = 0
        self._send_timer = QTimer(self)
        self._send_timer.timeout.connect(self._send)
        self._done_timer = QTimer(self)
        self._done_timer.setSingleShot(True)
        self._done_timer.timeout.connect(self._finish)

    def is_searching(self) -> bool:
        return self._done_timer.isActive()

    def search(self, wait_ms: int = SEARCH_MS) -> None:
        if self.is_searching():
            return
        targets = self._targets
        if targets is None:
            targets = config.discovery_targets()
        if targets is None:
            targets = broadcast_targets(config.DISCOVERY_PORT)
        self._now = list(targets)
        self._found = {}
        self._ask = secrets.token_hex(8)
        self._close()
        if self._now:
            sock = QUdpSocket(self)
            # 포트를 정하지 않는다(0번). 정해서 열면 Windows 방화벽이 허용 창을 띄운다.
            if sock.bind(QHostAddress(QHostAddress.AnyIPv4), 0):
                sock.readyRead.connect(self._on_ready)
                self._sock = sock
                self._sends_left = SEARCH_SENDS
                self._send()
                self._send_timer.start(max(150, wait_ms // (SEARCH_SENDS + 1)))
            else:
                sock.deleteLater()
        self._done_timer.start(wait_ms if self._sock is not None else 0)

    def _send(self) -> None:
        if self._sock is None or self._sends_left <= 0:
            self._send_timer.stop()
            return
        self._sends_left -= 1
        payload = json.dumps({"app": MAGIC, "v": VERSION,
                              "ask": self._ask}).encode("utf-8")
        for address, port in self._now:
            self._sock.writeDatagram(payload, QHostAddress(address), int(port))

    def _on_ready(self) -> None:
        sock = self._sock
        while sock is not None and sock.hasPendingDatagrams():
            datagram = sock.receiveDatagram(MAX_DATAGRAM)
            try:
                raw = json.loads(bytes(datagram.data()).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            found = self._read(raw, _ipv4(datagram.senderAddress()))
            if found is not None and len(self._found) < MAX_FOUND:
                self._found.setdefault((found["address"], found["port"]), found)

    def _read(self, raw: Any, address: str) -> dict | None:
        """남이 보낸 답에서 쓸 값만 고른다. 모양이 틀리면 버린다."""
        if (not isinstance(raw, dict) or raw.get("app") != MAGIC
                or raw.get("re") != self._ask or not address):
            return None
        port = raw.get("port")
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            return None
        host = _text(raw.get("host"), config.MAX_NICKNAME_LENGTH)
        host_id = _text(raw.get("host_id"), 64)
        if not host or not host_id:
            return None
        return {"address": address, "port": port, "host": host,
                "host_id": host_id,
                "took_over_from": _text(raw.get("took_over_from"), 64),
                "locked": raw.get("locked") is True}

    def _close(self) -> None:
        self._send_timer.stop()
        if self._sock is not None:
            self._sock.close()
            self._sock.deleteLater()
            self._sock = None

    def _finish(self) -> None:
        self._close()
        found = sorted(self._found.values(),
                       key=lambda f: (f["host"].casefold(), f["address"], f["port"]))
        self.finished.emit(found)

    def stop(self) -> None:
        self._done_timer.stop()
        self._close()


__all__ = ["Responder", "Searcher", "broadcast_targets"]
