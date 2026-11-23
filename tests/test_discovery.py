"""내부망에서 공유 PC 찾기(offscreen, 이 PC 안에서만).

신호는 127.0.0.1 의 임시 포트로만 오간다. 시험으로 띄운 실행은 실제 포트를 열지
않고 내부망에 신호를 뿌리지도 않는다."""
from __future__ import annotations

import json
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtNetwork import QHostAddress, QUdpSocket  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app import config, i18n  # noqa: E402
from app.database import SqliteRepository  # noqa: E402
from app.local_state import LocalStateStore  # noqa: E402
from app.network import discovery  # noqa: E402
from app.network.client_base import ClientBase  # noqa: E402
from app.network.discovery import Responder, Searcher  # noqa: E402
from app.replica import ReplicaStore, build_host_database  # noqa: E402
from app.settings import AppSettings  # noqa: E402
from app.ui import confirm, theme  # noqa: E402
from app.ui import main_window as mw  # noqa: E402
from app.ui.connect_dialog import ConnectDialog  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.sticky_note import StickyNoteWidget  # noqa: E402

ME = "지우"
OTHER = "민수"


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    i18n.set_language("ko")
    theme.apply_theme(app, "")
    yield app


def told(host: str, port: int, host_id: str, **more) -> dict:
    return {"host": host, "port": port, "host_id": host_id,
            "took_over_from": "", "locked": False, "ver": "1.0.3", **more}


def search(qapp, targets, seconds: float = 4.0) -> list[dict]:
    searcher = Searcher(targets)
    got: list[list] = []
    searcher.finished.connect(got.append)
    searcher.search(400)
    end = time.monotonic() + seconds
    while not got and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.005)
    assert got, "찾기가 끝나지 않았다"
    return got[0]


@pytest.fixture
def hosts(qapp):
    made: list[Responder] = []

    def add(answer) -> tuple[str, int]:
        responder = Responder(answer, 0)
        assert responder.start()
        made.append(responder)
        return "127.0.0.1", responder.port()

    yield add
    for responder in made:
        responder.stop()


# ── 신호 ────────────────────────────────────────────────────────────────

def test_a_test_run_never_touches_the_real_network(monkeypatch):
    """테스트와 시험 실행은 쓰고 있는 앱의 포트를 열지 않고, 내부망에 신호를
    뿌리지도 않는다."""
    monkeypatch.delenv("MEMIL_DISCOVERY_PORT", raising=False)
    monkeypatch.delenv("MEMIL_DISCOVERY_TARGETS", raising=False)
    assert config.isolated_run()
    assert config.discovery_port() is None
    assert config.discovery_targets() == []
    monkeypatch.setenv("MEMIL_DISCOVERY_PORT", "6100")
    monkeypatch.setenv("MEMIL_DISCOVERY_TARGETS", "127.0.0.1:6100, 127.0.0.1:6101,x")
    assert config.discovery_port() == 6100
    assert config.discovery_targets() == [("127.0.0.1", 6100), ("127.0.0.1", 6101)]
    # 평소 실행은 정해진 포트와 내부망 전체다.
    monkeypatch.setattr(config, "isolated_run", lambda: False)
    assert config.discovery_port() == config.DISCOVERY_PORT
    assert config.discovery_targets() is None


def test_sharing_pcs_answer_with_who_and_where(qapp, hosts):
    a = hosts(lambda: told(OTHER, 5555, "H1", locked=True))
    b = hosts(lambda: told("서연", 5601, "H2", took_over_from="H1"))

    found = search(qapp, [a, b])

    assert found == [
        {"address": "127.0.0.1", "port": 5555, "host": OTHER, "host_id": "H1",
         "took_over_from": "", "locked": True},
        {"address": "127.0.0.1", "port": 5601, "host": "서연", "host_id": "H2",
         "took_over_from": "H1", "locked": False},
    ]


def test_nothing_found_ends_quietly(qapp, hosts):
    silent = QUdpSocket()
    assert silent.bind(QHostAddress(QHostAddress.LocalHost), 0)
    assert search(qapp, [("127.0.0.1", silent.localPort())]) == []
    assert search(qapp, []) == []                 # 보낼 곳이 없어도 끝난다
    silent.close()


@pytest.mark.parametrize("answer", [
    lambda: told("", 5555, "H1"),                           # 이름이 없다
    lambda: told(OTHER, 0, "H1"),                           # 포트가 틀렸다
    lambda: told(OTHER, "5555", "H1"),
    lambda: told(OTHER, True, "H1"),
    lambda: told(OTHER, 5555, ""),
    lambda: {"port": 5555},
], ids=["no name", "port 0", "port as text", "port as bool", "no id", "bare"])
def test_a_malformed_answer_is_dropped(qapp, hosts, answer):
    assert search(qapp, [hosts(answer)]) == []


def test_what_others_send_is_cut_to_size_and_typed(qapp, hosts):
    long_name = "가" * 100
    found = search(qapp, [hosts(lambda: told(long_name, 5555, "H" * 100,
                                             locked="yes", took_over_from=7))])
    (one,) = found
    assert len(one["host"]) == config.MAX_NICKNAME_LENGTH
    assert len(one["host_id"]) == 64
    assert one["locked"] is False and one["took_over_from"] == ""
    # 한 번에 받는 크기를 넘는 답은 통째로 버린다.
    huge = hosts(lambda: told("가" * 2000, 5555, "H1"))
    assert search(qapp, [huge]) == []


def test_an_answer_to_someone_elses_question_is_ignored(qapp):
    """되받아친 옛 답이나 남의 물음에 대한 답을 내 것으로 치지 않는다."""
    liar = QUdpSocket()
    assert liar.bind(QHostAddress(QHostAddress.LocalHost), 0)

    def reply():
        while liar.hasPendingDatagrams():
            datagram = liar.receiveDatagram()
            body = dict(told(OTHER, 5555, "H1"), app=discovery.MAGIC, re="not-asked")
            liar.writeDatagram(json.dumps(body).encode(), datagram.senderAddress(),
                               datagram.senderPort())
    liar.readyRead.connect(reply)
    assert search(qapp, [("127.0.0.1", liar.localPort())]) == []
    liar.close()


def test_other_traffic_on_the_port_gets_no_answer(qapp, hosts):
    where = hosts(lambda: told(OTHER, 5555, "H1"))
    probe = QUdpSocket()
    assert probe.bind(QHostAddress(QHostAddress.LocalHost), 0)
    for junk in (b"hello", b"{}", json.dumps({"app": "x", "ask": "1" * 16}).encode(),
                 json.dumps({"app": discovery.MAGIC, "ask": 5}).encode()):
        probe.writeDatagram(junk, QHostAddress(where[0]), where[1])
    end = time.monotonic() + 0.6
    while time.monotonic() < end:
        qapp.processEvents()
    assert not probe.hasPendingDatagrams()
    probe.close()


def test_a_flood_of_questions_is_not_answered_in_kind(qapp, hosts):
    where = hosts(lambda: told(OTHER, 5555, "H1"))
    probe = QUdpSocket()
    assert probe.bind(QHostAddress(QHostAddress.LocalHost), 0)
    ask = json.dumps({"app": discovery.MAGIC, "ask": "1" * 16}).encode()
    for _ in range(discovery.ANSWERS_PER_SECOND * 5):
        probe.writeDatagram(ask, QHostAddress(where[0]), where[1])
    answers = 0
    end = time.monotonic() + 0.8
    while time.monotonic() < end:
        qapp.processEvents()
        while probe.hasPendingDatagrams():
            probe.receiveDatagram()
            answers += 1
    assert 0 < answers <= discovery.ANSWERS_PER_SECOND * 2
    probe.close()


def test_a_copy_taken_over_remembers_which_sharing_it_continues(tmp_path):
    copy = ReplicaStore(tmp_path / "replica.sqlite3")
    copy.apply_snapshot([{"id": "a", "content": "회의록", "version": 1,
                          "updated_at": 1, "color": "#FFE066"}], [],
                        host_id="H1", host=OTHER, address="10.0.0.5:5555", nickname=ME)
    copy.close()

    build_host_database(tmp_path / "replica.sqlite3", tmp_path / "notes.sqlite3", ME)

    repo = SqliteRepository(tmp_path / "notes.sqlite3")
    try:
        assert repo.took_over_from() == "H1" and repo.host_id() != "H1"
    finally:
        repo.close()


# ── 접속 창 ─────────────────────────────────────────────────────────────

FOUND = [
    {"address": "192.168.10.3", "port": 5555, "host": OTHER, "host_id": "H1",
     "took_over_from": "", "locked": True},
    {"address": "192.168.10.7", "port": 5601, "host": "서연", "host_id": "H2",
     "took_over_from": "", "locked": False},
]


def test_found_pcs_are_listed_and_picking_one_fills_the_address(qapp):
    dialog = ConnectDialog(last_nickname=ME)
    dialog._set_mode(1)
    assert not dialog._guest_layout.isRowVisible(dialog._found_box)

    dialog._show_found(FOUND)

    assert dialog._guest_layout.isRowVisible(dialog._found_box)
    assert [r.accessibleName() for r in dialog._found_rows] == [
        f"{OTHER}, 192.168.10.3", "서연, 192.168.10.7:5601"]
    # 둘이면 고르기 전에는 주소를 넣지 않는다.
    assert dialog._guest_ip.text() == "127.0.0.1"
    dialog._found_rows[1].click()
    assert dialog._guest_ip.text() == "192.168.10.7" and dialog._guest_port.value() == 5601
    assert [r.isChecked() for r in dialog._found_rows] == [False, True]
    dialog._found_rows[0].click()
    assert [r.isChecked() for r in dialog._found_rows] == [True, False]
    dialog._found_rows[1].click()
    dialog.accept()
    choice = dialog.choice()
    assert (choice.mode, choice.host_ip, choice.port) == ("guest", "192.168.10.7", 5601)


def test_the_only_pc_found_is_filled_in_unless_an_address_was_given(qapp):
    fresh = ConnectDialog(last_nickname=ME)
    fresh._set_mode(1)
    fresh._show_found(FOUND[:1])
    assert fresh._guest_ip.text() == "192.168.10.3"

    kept = ConnectDialog(last_nickname=ME, last_ip="10.0.0.5")
    kept._set_mode(1)
    kept._show_found(FOUND[:1])
    assert kept._guest_ip.text() == "10.0.0.5"        # 넣어 둔 주소를 바꾸지 않는다


def test_nothing_found_leaves_the_form_as_it_was(qapp):
    dialog = ConnectDialog(last_nickname=ME)
    dialog._set_mode(1)
    dialog._show_found(FOUND)
    dialog._show_found([])
    assert dialog._found_rows == []
    assert not dialog._guest_layout.isRowVisible(dialog._found_box)


# ── 붙던 공유 PC 를 다시 찾기 ───────────────────────────────────────────

class FakeClient(ClientBase):
    def __init__(self):
        super().__init__(ME)
        self.calls: list[tuple] = []
        self.where = ("10.0.0.5", 5555)

    def address(self):
        return self.where

    def set_address(self, host, port):
        self.where = (host, port)
        self.calls.append(("address", host, port))

    def follow(self, host, port):
        self.calls.append(("follow", host, port))


class FakeSearcher(QObject):
    finished = Signal(list)
    made: list["FakeSearcher"] = []

    def __init__(self, *a, **k):
        super().__init__()
        self.searches = 0
        FakeSearcher.made.append(self)

    def search(self, *a, **k):
        self.searches += 1


@pytest.fixture
def guest(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(StickyNoteWidget, "bring_to_front",
                        lambda self, *, start_editing=False: None)
    monkeypatch.setattr(mw, "Searcher", FakeSearcher)
    monkeypatch.setattr(mw.toast, "user_is_busy", lambda: False)
    FakeSearcher.made.clear()
    local = LocalStateStore(tmp_path / "local_state.json")
    local.state.last_host_ip, local.state.last_host_port = "10.0.0.5", 5555
    win = MainWindow(client=FakeClient(), local_state=local, role="guest",
                     settings=AppSettings())
    win.asked: list = []
    win.answer = True
    monkeypatch.setattr(confirm, "ask", lambda parent, title, text, **k: (
        win.asked.append((text, k.get("detail"))), win.answer)[1])
    yield win
    win._force_quit = True
    win._teardown_stickies()
    win.close()
    win.deleteLater()
    qapp.processEvents()


def join(win, host_id: str = "H1") -> None:
    win._client.connectionChanged.emit(True)
    win._client.welcomed.emit({"users": [ME, OTHER], "host": OTHER, "notes": [],
                               "locks": {}, "folders": [], "heartbeat_ms": 3000,
                               "host_id": host_id})


def lose(win, times: int) -> None:
    for _ in range(times):
        win._client.connectionChanged.emit(False)


def searcher() -> FakeSearcher:
    return FakeSearcher.made[-1]


def test_the_search_starts_only_after_repeated_failures(guest):
    join(guest)
    lose(guest, mw.FIND_AFTER_FAILS - 1)
    assert FakeSearcher.made == []                # 잠깐 끊긴 것으로 찾아 나서지 않는다
    lose(guest, 1)
    assert searcher().searches == 1
    lose(guest, 5)
    assert searcher().searches == 1               # 잇달아 찾지 않는다(간격을 둔다)


def test_a_pc_that_never_joined_has_nothing_to_look_for(guest):
    lose(guest, mw.FIND_AFTER_FAILS + 2)
    assert FakeSearcher.made == []


def moved(**more) -> dict:
    return {"address": "192.168.10.44", "port": 5555, "host": OTHER, "host_id": "H1",
            "took_over_from": "", "locked": False, **more}


def test_with_a_password_the_new_address_is_tried_without_asking(guest):
    guest._local.state.share_auth = {"salt": "aa", "key": "bb", "iterations": 1}
    join(guest)
    lose(guest, mw.FIND_AFTER_FAILS)

    searcher().finished.emit([moved(locked=True)])

    assert guest._client.calls == [("follow", "192.168.10.44", 5555)]
    assert guest.asked == []
    # 맞는 것으로 확인되면 그 주소를 적어 둔다.
    guest._client.followed.emit("192.168.10.44", 5555)
    assert guest._local.state.last_host_ip == "192.168.10.44"
    saved = json.loads(guest._local.path.read_text(encoding="utf-8"))
    assert saved["last_host_ip"] == "192.168.10.44"
    # 아니던 곳은 다시 찔러 보지 않는다.
    searcher().finished.emit([moved(locked=True)])
    assert len(guest._client.calls) == 1


def test_without_a_password_the_user_decides(guest):
    """답은 누구나 꾸밀 수 있다. 확인할 방법이 없으면 묻는다."""
    join(guest)
    lose(guest, mw.FIND_AFTER_FAILS)

    searcher().finished.emit([moved()])

    assert guest.asked == [(i18n.t("discover.moved_body"), "192.168.10.44")]
    assert guest._client.calls == [("address", "192.168.10.44", 5555)]
    assert guest._local.state.last_host_ip == "192.168.10.44"


def test_saying_no_is_remembered_for_this_run(guest):
    guest.answer = False
    join(guest)
    lose(guest, mw.FIND_AFTER_FAILS)
    searcher().finished.emit([moved()])
    searcher().finished.emit([moved()])
    assert len(guest.asked) == 1 and guest._client.calls == []
    assert guest._local.state.last_host_ip == "10.0.0.5"


def test_a_pc_that_took_over_the_sharing_is_offered_by_name(guest):
    join(guest)
    lose(guest, mw.FIND_AFTER_FAILS)

    searcher().finished.emit([moved(host="서연", host_id="H9", took_over_from="H1")])

    assert guest.asked == [(i18n.t("discover.took_over_body", host="서연"),
                            "192.168.10.44")]
    assert guest._client.calls == [("address", "192.168.10.44", 5555)]


def test_unrelated_sharing_pcs_are_left_alone(guest):
    join(guest)
    lose(guest, mw.FIND_AFTER_FAILS)
    searcher().finished.emit([
        moved(host="다른 팀", host_id="H7"),                 # 상관없는 공유
        moved(address="10.0.0.5"),                           # 지금 붙으려는 그 주소
    ])
    assert guest.asked == [] and guest._client.calls == []


def test_an_answer_that_arrives_after_reconnecting_is_ignored(guest):
    join(guest)
    lose(guest, mw.FIND_AFTER_FAILS)
    join(guest)
    searcher().finished.emit([moved()])
    assert guest.asked == [] and guest._client.calls == []


def test_the_question_waits_while_presenting(guest, monkeypatch):
    monkeypatch.setattr(mw.toast, "user_is_busy", lambda: True)
    join(guest)
    lose(guest, mw.FIND_AFTER_FAILS)
    searcher().finished.emit([moved()])
    assert guest.asked == []
    # 끝난 뒤 다시 찾으면 그때 묻는다(붙지 않기로 한 것으로 치지 않는다).
    monkeypatch.setattr(mw.toast, "user_is_busy", lambda: False)
    searcher().finished.emit([moved()])
    assert len(guest.asked) == 1
