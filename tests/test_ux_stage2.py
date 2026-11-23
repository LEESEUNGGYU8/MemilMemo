"""UI/UX 개선 2단계(연결과 신뢰) 테스트(offscreen): 끊긴 동안 쓴 글을 버리지 않는다."""
from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app import i18n  # noqa: E402
from app.local_state import LocalStateStore  # noqa: E402
from app.network.client_base import ClientBase  # noqa: E402
from app.settings import AppSettings  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.sticky_note import StickyNoteWidget  # noqa: E402

ME = "지우"


class FakeClient(ClientBase):
    """보낸 요청을 적어 두기만 한다. 응답은 테스트가 직접 낸다."""

    def __init__(self, nickname: str = ME):
        super().__init__(nickname)
        self.calls: list[tuple] = []

    def acquire_lock(self, note_id):
        self.calls.append(("lock", note_id))

    def release_lock(self, note_id):
        self.calls.append(("release", note_id))

    def update_note(self, note_id, content, color, width, height, expected_version):
        self.calls.append(("update", note_id, content, expected_version))

    def create_note(self, color, width, height, folder_id=None, private_owner=None, quiet=False):
        self.calls.append(("create",))

    def of(self, kind: str) -> list[tuple]:
        return [c for c in self.calls if c[0] == kind]


def _note(**over) -> dict:
    note = {"id": "n1", "version": 3, "color": "#FFE066", "width": 280,
            "height": 260, "content": "회의 3시", "created_by": "민수",
            "updated_at": time.time()}
    note.update(over)
    return note


def _welcome(client, *notes, locks=None) -> None:
    client.welcomed.emit({"users": [ME, "민수"], "host": "민수",
                          "notes": list(notes), "locks": locks or {},
                          "folders": [], "heartbeat_ms": 3000})


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    i18n.set_language("ko")
    yield app


@pytest.fixture
def guest(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(StickyNoteWidget, "bring_to_front",
                        lambda self, *, start_editing=False: None)
    client = FakeClient()
    store = LocalStateStore(tmp_path / "local_state.json")
    store.state.last_host_ip = "192.168.0.12"
    store.state.last_host_port = 5555
    win = MainWindow(client=client, local_state=store, role="guest",
                     settings=AppSettings())
    yield win
    win._force_quit = True
    win._teardown_stickies()
    win.close()
    win.deleteLater()
    qapp.processEvents()


def _start_editing(win, text: str) -> StickyNoteWidget:
    """메모를 보이게 하고 편집에 들어가 글을 쓴다(호스트가 잠금을 준 것처럼)."""
    win._set_note_visibility("n1", True)
    sticky = win._stickies["n1"]
    win._client.lockGranted.emit("n1")
    assert sticky._edit_mode
    sticky._text.insertPlainText(text)
    return sticky


# ── 상태 표시 ───────────────────────────────────────────────────────────

def test_note_shows_offline_and_cannot_edit_with_nowhere_to_keep_it(guest):
    """끊긴 동안 쓴 글을 적어 둘 곳을 열지 못한 PC(이 창에는 주지 않았다)."""
    client = guest._client
    client.connectionChanged.emit(True)
    _welcome(client, _note())
    guest._set_note_visibility("n1", True)
    sticky = guest._stickies["n1"]

    client.connectionChanged.emit(False)
    assert sticky._lock_label.text() == "오프라인"
    client.calls.clear()
    sticky._request_edit()
    assert client.of("lock") == []                    # 닿지 않을 요청은 보내지 않는다
    assert not sticky._edit_mode and not sticky._pending_lock
    assert "끊겨" in guest._status.currentMessage()    # 까닭을 알린다

    client.connectionChanged.emit(True)
    assert sticky._lock_label.text() == ""
    sticky._request_edit()
    assert client.of("lock") == [("lock", "n1")]


def test_unreachable_host_is_explained(guest):
    client = guest._client
    badge = guest._conn_badge
    assert badge._state.text() == "접속 중"
    client.connectionChanged.emit(False)              # 첫 실패는 조금 더 기다린다
    assert badge._state.text() == "접속 중"
    client.connectionChanged.emit(False)
    assert badge._state.text() == "응답 없음"
    assert guest._empty_kind == "unreachable"
    assert "192.168.0.12:5555" in guest._empty_body.text()

    client.connectionChanged.emit(True)               # 붙으면 안내가 걷힌다
    _welcome(client, _note())
    assert badge._state.text() == "연결됨"
    assert guest._empty_kind == ""


# ── 끊긴 동안 쓴 글 ─────────────────────────────────────────────────────

def test_text_written_while_offline_is_sent_after_reconnect(guest):
    client = guest._client
    client.connectionChanged.emit(True)
    _welcome(client, _note())
    sticky = _start_editing(guest, " 대회의실")
    client.connectionChanged.emit(False)
    sticky._text.insertPlainText(" (자료 지참)")        # 끊긴 줄 모르고 이어 쓴다
    typed = sticky._current_content()
    assert "자료 지참" in typed

    client.calls.clear()
    client.connectionChanged.emit(True)
    _welcome(client, _note())                          # 그 사이 아무도 고치지 않았다
    assert client.of("lock") == [("lock", "n1")]       # 원래 메모에 다시 넣는다
    assert client.of("create") == []

    client.lockGranted.emit("n1")
    assert client.of("update") == [("update", "n1", typed, 3)]
    assert client.of("release") == [("release", "n1")]
    assert "보냈습니다" in guest._status.currentMessage()
    assert not guest._stickies["n1"]._edit_mode        # 편집 창으로 열지는 않는다


def test_offline_text_is_kept_as_a_new_note_when_someone_else_edited(guest):
    client = guest._client
    client.connectionChanged.emit(True)
    _welcome(client, _note())
    sticky = _start_editing(guest, " 대회의실")
    client.connectionChanged.emit(False)
    typed = sticky._current_content()

    client.calls.clear()
    client.connectionChanged.emit(True)
    _welcome(client, _note(version=4, content="회의 4시로 변경"))   # 민수가 고쳤다
    # 민수의 글을 덮어쓰지 않는다. 내 글은 새 메모로 남긴다.
    assert client.of("lock") == [] and client.of("update") == []
    assert client.of("create") == [("create",)]

    client.noteCreated.emit(_note(id="n2", version=1, content="", created_by=ME))
    assert client.of("lock") == [("lock", "n2")]
    assert "새 메모로 남겼습니다" in guest._status.currentMessage()
    client.lockGranted.emit("n2")
    assert client.of("update") == [("update", "n2", typed, 1)]
    assert guest._orphan_texts == [] and guest._pending_resend == {}


def test_offline_text_is_kept_when_the_note_was_deleted_or_locked(guest):
    client = guest._client
    client.connectionChanged.emit(True)
    _welcome(client, _note())
    sticky = _start_editing(guest, " 추가")
    typed = sticky._current_content()
    client.connectionChanged.emit(False)
    client.calls.clear()
    client.connectionChanged.emit(True)
    _welcome(client)                                    # 메모가 지워졌다
    assert client.of("create") == [("create",)]
    assert guest._orphan_texts == [typed]

    # 다시 보내려는 순간 다른 사람이 잡은 경우도 새 메모로 돌린다.
    guest._orphan_texts.clear()
    client.calls.clear()
    guest._pending_resend["n1"] = "잡힌 글"
    client.lockDenied.emit("n1", "민수")
    assert client.of("create") == [("create",)]
    assert guest._orphan_texts == ["잡힌 글"]


def test_nothing_is_resent_when_there_was_no_unsent_edit(guest):
    client = guest._client
    client.connectionChanged.emit(True)
    _welcome(client, _note())
    guest._set_note_visibility("n1", True)
    sticky = guest._stickies["n1"]
    client.lockGranted.emit("n1")                       # 편집에 들어갔지만 쓰지 않았다
    assert sticky._edit_mode and sticky.unsent_edit() is None
    client.connectionChanged.emit(False)
    client.calls.clear()
    client.connectionChanged.emit(True)
    _welcome(client, _note())
    assert client.calls == []


def test_text_that_already_reached_the_host_is_not_duplicated(guest):
    client = guest._client
    client.connectionChanged.emit(True)
    _welcome(client, _note())
    sticky = _start_editing(guest, " 대회의실")
    sticky._idle_save()                                 # 보냈지만 답이 오기 전에 끊겼다
    sent = client.of("update")[-1][2]
    client.connectionChanged.emit(False)
    client.calls.clear()
    client.connectionChanged.emit(True)
    _welcome(client, _note(version=4, content=sent))    # 호스트에는 들어가 있었다
    assert client.calls == []
