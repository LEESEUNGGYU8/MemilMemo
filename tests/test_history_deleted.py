"""이력 창에서 삭제된 메모의 이력을 따로 보고, 새 메모로 되살린다(offscreen).

삭제된 메모는 목록에 없어서 이력 창의 메모 고르기에도 나오지 않았다. 공유
PC 는 삭제된 메모의 이력도 보내 주므로(1.0.1 이하도 같다) 받은 이력에서
가려내 따로 보인다."""
from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app import config, i18n  # noqa: E402
from app.local_state import LocalStateStore  # noqa: E402
from app.network.client_base import ClientBase  # noqa: E402
from app.settings import AppSettings  # noqa: E402
from app.ui import confirm, theme  # noqa: E402
from app.ui import history_viewer as hv  # noqa: E402
from app.ui.history_viewer import HistoryViewer  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.sticky_note import StickyNoteWidget  # noqa: E402

ME = "지우"
OTHER = "민수"


def entry(note_id: str, action: str, who: str, at: int, before, after,
          color: str = "#FFE066") -> dict:
    return {"note_id": note_id, "user": who, "action": action,
            "content_before": before, "content_after": after,
            "color_before": color, "color_after": None if action == "delete" else color,
            "session_start": at - 1, "session_end": at, "id": at}


LIVE = {"id": "n1", "version": 3, "color": "#FFE066", "width": 300, "height": 240,
        "content": "<p>주간 회의</p><p>3시</p>", "created_by": ME}
GONE_TEXT = "<p>해체신고 절차</p><p>1. 서류 준비</p><p>2. 접수</p>"
ENTRIES = [
    entry("n1", "create", ME, 100, None, ""),
    entry("n1", "update", ME, 200, "", "<p>주간 회의</p><p>3시</p>"),
    entry("n2", "create", ME, 300, None, ""),
    entry("n2", "update", ME, 400, "", "<p>해체신고 절차</p><p>1. 서류 준비</p>"),
    entry("n2", "update", OTHER, 500, "<p>해체신고 절차</p><p>1. 서류 준비</p>",
          GONE_TEXT, color="#BAE1FF"),
    entry("n2", "delete", OTHER, 600, GONE_TEXT, None, color="#BAE1FF"),
]


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    i18n.set_language("ko")
    theme.apply_theme(app, "")
    yield app


@pytest.fixture
def viewer(qapp):
    v = HistoryViewer()
    v.set_notes([LIVE])
    v.set_entries(ENTRIES)
    v.show()
    qapp.processEvents()
    yield v
    v.close()
    v.deleteLater()


def rows(v: HistoryViewer) -> list[tuple[str, str]]:
    out = []
    for i in range(v._list.count()):
        e = v._list.item(i).data(Qt.UserRole)
        out.append((e["note_id"], e["action"]))
    return out


def combo(v: HistoryViewer) -> list[str]:
    return [v._note_combo.itemText(i) for i in range(v._note_combo.count())]


# ── 이력 창 ─────────────────────────────────────────────────────────────

def test_deleted_notes_are_listed_apart(viewer):
    # 처음에는 지금 있는 메모의 이력만 보인다.
    assert viewer._live_btn.isChecked() and viewer._gone_btn.isEnabled()
    assert rows(viewer) == [("n1", "update"), ("n1", "create")]
    assert combo(viewer) == [i18n.t("history.all"), "주간 회의"]

    viewer._gone_btn.click()
    assert rows(viewer) == [("n2", "delete"), ("n2", "update"), ("n2", "update"),
                            ("n2", "create")]
    # 삭제된 메모는 지워질 때의 첫 줄로 가리킨다.
    assert combo(viewer) == [i18n.t("history.all"), "해체신고 절차"]
    # 여러 메모를 섞어 보는 동안에는 줄마다 메모 이름을 적는다.
    assert viewer._list.item(0).data(hv._TITLE_ROLE) == "해체신고 절차"

    viewer._note_combo.setCurrentIndex(1)
    assert len(rows(viewer)) == 4
    assert not viewer._list.item(0).data(hv._TITLE_ROLE)

    viewer._live_btn.click()
    assert rows(viewer) == [("n1", "update"), ("n1", "create")]


def test_no_deleted_notes_means_nothing_to_switch_to(qapp):
    v = HistoryViewer()
    v.set_notes([LIVE])
    v.set_entries(ENTRIES[:2])
    assert not v._gone_btn.isEnabled() and v._live_btn.isChecked()
    assert len(rows(v)) == 2
    v.deleteLater()


def test_delete_entry_shows_the_text_as_it_was(viewer):
    viewer._gone_btn.click()
    viewer._list.setCurrentRow(0)
    text = viewer._detail.toPlainText()
    assert "해체신고 절차" in text and "2. 접수" in text
    assert "line-through" not in viewer._detail.toHtml()
    assert viewer._header.text().startswith(i18n.t("history.action_delete"))


def test_deleted_note_comes_back_as_a_new_note(viewer, monkeypatch):
    asked: list[tuple] = []
    monkeypatch.setattr(confirm, "ask",
                        lambda parent, title, text, **k: asked.append((title, k)) or True)
    revived: list[tuple[str, str]] = []
    restored: list[tuple[str, str]] = []
    viewer.reviveRequested.connect(lambda c, col: revived.append((c, col)))
    viewer.restoreRequested.connect(lambda n, c: restored.append((n, c)))

    viewer._gone_btn.click()
    viewer._list.setCurrentRow(0)                    # 삭제 항목
    assert viewer._restore_btn.isEnabled()
    assert viewer._restore_btn.text() == i18n.t("history.btn_revive")
    viewer._restore_btn.click()
    assert revived == [(GONE_TEXT, "#BAE1FF")]
    assert asked[-1][0] == i18n.t("history.revive_title")

    viewer._list.setCurrentRow(2)                    # 그 전의 수정 항목
    viewer._restore_btn.click()
    assert revived[-1] == ("<p>해체신고 절차</p><p>1. 서류 준비</p>", "#FFE066")
    assert restored == []

    # 지금 있는 메모는 전처럼 그 자리에 되돌린다.
    viewer._live_btn.click()
    viewer._list.setCurrentRow(0)
    assert viewer._restore_btn.text() == i18n.t("history.btn_restore")
    viewer._restore_btn.click()
    assert restored == [("n1", "<p>주간 회의</p><p>3시</p>")]


def test_refusing_the_question_revives_nothing(viewer, monkeypatch):
    monkeypatch.setattr(confirm, "ask", lambda *a, **k: False)
    revived: list = []
    viewer.reviveRequested.connect(lambda c, col: revived.append(c))
    viewer._gone_btn.click()
    viewer._list.setCurrentRow(0)
    viewer._restore_btn.click()
    assert revived == []


def test_select_note_switches_to_the_right_side(viewer):
    viewer.select_note("n2")
    assert viewer._gone_btn.isChecked()
    assert viewer._note_combo.currentData() == "n2" and len(rows(viewer)) == 4
    viewer.select_note("n1")
    assert viewer._live_btn.isChecked()
    assert viewer._note_combo.currentData() == "n1" and len(rows(viewer)) == 2


def test_a_note_deleted_while_the_window_is_open_moves_over(viewer):
    viewer.set_notes([])                              # 메모가 방금 지워졌다
    viewer.append_entry(entry("n1", "delete", OTHER, 700,
                              "<p>주간 회의</p><p>3시</p>", None))
    assert rows(viewer) == []                         # 지금 있는 메모가 없다
    viewer._gone_btn.click()
    assert combo(viewer) == [i18n.t("history.all"), "주간 회의", "해체신고 절차"]
    assert rows(viewer)[0] == ("n1", "delete")


def test_image_only_and_empty_notes_have_a_name(qapp):
    png = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4"
           "2mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
    v = HistoryViewer()
    v.set_notes([])
    v.set_entries([
        entry("a", "delete", ME, 10, f'<p><img src="{png}"></p>', None),
        entry("b", "delete", ME, 20, "", None),
    ])
    v._gone_btn.click()
    assert combo(v) == [i18n.t("history.all"), i18n.t("history.empty_note"),
                        i18n.t("main.note_title_image")]
    v._list.setCurrentRow(1)                          # 그림만 든 메모
    assert i18n.t("history.image_count", n=1) in v._detail.toPlainText()
    v.deleteLater()


# ── 메인 창 ─────────────────────────────────────────────────────────────

class FakeClient(ClientBase):
    def __init__(self, nickname: str = ME):
        super().__init__(nickname)
        self.calls: list[tuple] = []

    def create_note(self, color, width, height, folder_id=None,
                    private_owner=None, quiet=False):
        self.calls.append(("create", color, folder_id, private_owner, quiet))

    def acquire_lock(self, note_id):
        self.calls.append(("lock", note_id))

    def release_lock(self, note_id):
        self.calls.append(("release", note_id))

    def update_note(self, note_id, content, color, width, height, expected_version):
        self.calls.append(("update", note_id, content))

    def get_history(self, note_id=None):
        self.calls.append(("history", note_id))

    def of(self, kind):
        return [c for c in self.calls if c[0] == kind]


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    fronted: list[str] = []
    monkeypatch.setattr(StickyNoteWidget, "bring_to_front",
                        lambda self, *, start_editing=False: fronted.append(self.note_id))
    client = FakeClient()
    win = MainWindow(client=client,
                     local_state=LocalStateStore(tmp_path / "local_state.json"),
                     role="guest", settings=AppSettings())
    win.fronted = fronted
    client.connectionChanged.emit(True)
    client._dispatch("welcome", {
        "users": [ME, OTHER], "host": OTHER, "folders": [], "locks": {},
        "heartbeat_ms": 3000, "notes": [dict(LIVE, updated_at=int(time.time() * 1000))]})
    qapp.processEvents()
    yield win
    win._force_quit = True
    win._teardown_stickies()
    win.close()
    win.deleteLater()
    qapp.processEvents()


def test_reviving_makes_a_private_note_with_that_text(qapp, window):
    client = window._client
    window._on_revive_requested(GONE_TEXT, "#BAE1FF")
    # 나만 보던 메모였을 수 있으므로 나만 보는 메모로 만든다.
    assert client.of("create") == [("create", "#BAE1FF", None, ME, True)]
    assert window._status.currentMessage() == i18n.t("main.status_revived")

    client._dispatch("note_created", {"note": {
        "id": "new1", "version": 1, "color": "#BAE1FF", "width": 280, "height": 260,
        "content": "", "created_by": ME, "private_owner": ME,
        "updated_at": int(time.time() * 1000)}})
    qapp.processEvents()
    # 본문은 잠금을 받은 뒤에 넣는다(다른 되살리기와 같은 길).
    assert ("lock", "new1") in client.calls
    assert window._pending_resend.get("new1") == GONE_TEXT
    assert not window._local.state.get_note_state("new1").hidden
    assert window.fronted == ["new1"]


def test_reviving_with_an_odd_color_uses_the_default(qapp, window):
    window._on_revive_requested("<p>글</p>", "red'><img src=x>")
    assert window._client.of("create")[0][1] in config.COLORS


def test_reviving_needs_a_connection(qapp, window):
    window._client.connectionChanged.emit(False)
    window._on_revive_requested(GONE_TEXT, "#FFE066")
    assert window._client.of("create") == []
    assert window._recreate_queue == []


def test_history_window_follows_a_deletion(qapp, window):
    client = window._client
    window._open_history()
    viewer = window._history_viewer
    client._dispatch("history_list", {"note_id": "", "entries": ENTRIES[:2]})
    assert not viewer._gone_btn.isEnabled()

    client._dispatch("note_deleted", {"id": "n1"})
    client._dispatch("history_appended", {"entry": entry(
        "n1", "delete", OTHER, 900, "<p>주간 회의</p><p>3시</p>", None)})
    assert viewer._gone_btn.isEnabled()
    viewer._gone_btn.click()
    assert rows(viewer)[0] == ("n1", "delete")
    viewer.close()


def test_opening_the_history_of_one_note_shows_only_that_note(qapp, window):
    window._open_history_for("n1")
    viewer = window._history_viewer
    window._client._dispatch("history_list", {"note_id": "", "entries": ENTRIES})
    assert viewer._live_btn.isChecked()
    assert viewer._note_combo.currentData() == "n1"
    assert rows(viewer) == [("n1", "update"), ("n1", "create")]
    viewer.close()
