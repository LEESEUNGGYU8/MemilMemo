"""UI/UX 개선 1단계(안전과 가독성) 테스트(offscreen)."""
from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QSize  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QTextCursor, QTextFormat  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app import i18n  # noqa: E402
from app.local_state import LocalStateStore  # noqa: E402
from app.network.client_base import ClientBase  # noqa: E402
from app.settings import AppSettings  # noqa: E402
from app.ui import confirm, rich_text, theme  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.sticky_note import StickyNoteWidget  # noqa: E402

ME = "지우"


class FakeClient(ClientBase):
    def __init__(self):
        super().__init__(ME)
        self.created: list[dict] = []
        self.deleted: list[str] = []

    def create_note(self, color, width, height, folder_id=None,
                    private_owner=None, quiet=False) -> None:
        note = {"id": f"n{len(self.created) + 1}", "version": 1, "color": color,
                "width": width, "height": height, "content": "",
                "folder_id": folder_id, "created_by": self._nickname,
                "updated_at": time.time()}
        self.created.append(note)
        self.noteCreated.emit(note)

    def delete_note(self, note_id: str) -> None:
        self.deleted.append(note_id)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    i18n.set_language("ko")
    theme.apply_theme(app, "")
    yield app


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(StickyNoteWidget, "bring_to_front",
                        lambda self, *, start_editing=False: None)
    client = FakeClient()
    win = MainWindow(client=client,
                     local_state=LocalStateStore(tmp_path / "local_state.json"),
                     role="host", settings=AppSettings())
    yield win
    win._force_quit = True
    win._teardown_stickies()
    win.close()
    win.deleteLater()
    qapp.processEvents()


def _note(**over) -> dict:
    note = {"id": "s1", "version": 1, "color": "#FFE066", "width": 280,
            "height": 260, "content": ""}
    note.update(over)
    return note


def _sticky(qapp, **over) -> StickyNoteWidget:
    w = StickyNoteWidget(_note(**over), ME, settings=AppSettings())
    w.apply_local_state(200, 160)
    w.show()
    qapp.processEvents()
    return w


def _close(w) -> None:
    w._teardown = True
    w.close()


# ── 확인창 ──────────────────────────────────────────────────────────────

def test_destructive_confirm_defaults_to_cancel(qapp):
    box, act, can = confirm.build_ask(
        None, "메모 삭제", "'회의' 메모를 삭제합니다.", action="삭제", danger=True)
    # Enter 나 Esc 한 번에 지워지지 않는다.
    assert box.defaultButton() is can
    assert box.escapeButton() is can
    # 단추에는 하는 일을 화면 언어로 적는다(Yes/No 가 아니다).
    assert act.text() == "삭제" and can.text() == "취소"
    assert act.property("danger") is True
    assert box.buttonRole(act) == QMessageBox.DestructiveRole
    box.deleteLater()


def test_delete_confirm_names_the_note_and_its_reach(window, monkeypatch):
    window._client.connectionChanged.emit(True)
    window._client.noteCreated.emit(_note(
        id="n9", content="주간 회의 메모\n둘째 줄", created_by="민수"))
    asked: dict = {}

    def fake(parent, title, text, **kw):
        asked.update(title=title, text=text, **kw)
        return False

    monkeypatch.setattr(confirm, "ask", fake)
    window._delete_note_confirm("n9")
    assert "주간 회의 메모" in asked["text"]          # 무엇을 지우는가
    assert "모든 참가자" in asked["detail"]           # 어디까지 지워지는가
    assert asked["danger"] is True and asked["action"] == "삭제"
    assert window._client.deleted == []               # 취소하면 지우지 않는다

    monkeypatch.setattr(confirm, "ask", lambda *a, **k: True)
    window._delete_note_confirm("n9")
    assert window._client.deleted == ["n9"]


# ── 상태줄 ──────────────────────────────────────────────────────────────

def test_connection_state_is_always_shown(window):
    badge = window._conn_badge
    assert badge.state() == "connecting"
    window._client.connectionChanged.emit(True)
    assert badge.state() == "connected"
    assert badge._state.text() == "연결됨"
    assert ME in badge._detail.text()

    # 임시 문구가 지나가도 연결 상태는 남는다.
    window._status.showMessage("민수 접속", 3000)
    assert badge._state.text() == "연결됨"
    window._status.clearMessage()

    window._client.connectionChanged.emit(False)
    assert badge.state() == "disconnected"
    assert badge._state.text() == "연결 끊김"


def test_new_note_is_refused_with_a_reason_while_offline(window):
    window._create_note("#FFE066")                    # 아직 붙기 전
    assert window._client.created == []
    assert "만들 수 없습니다" in window._status.currentMessage()
    window._client.connectionChanged.emit(True)
    window._create_note("#FFE066")
    assert len(window._client.created) == 1


# ── 빈 목록 안내 ────────────────────────────────────────────────────────

def test_empty_states_say_what_to_do_next(window):
    window._client.connectionChanged.emit(True)
    window._apply_search_filter("")
    assert window._empty_kind == "none"
    assert not window._empty_panel.isHidden() and window._scroll.isHidden()
    assert window._empty_action.text() == "새 메모"

    window._empty_action.click()                      # 안내의 단추로 바로 만든다
    assert len(window._client.created) == 1
    assert window._empty_kind == "" and window._empty_panel.isHidden()

    window._search_input.setText("없는말")
    assert window._empty_kind == "search"
    assert "없는말" in window._empty_title.text()
    window._empty_action.click()                      # 검색어 지우기
    assert window._search_input.text() == "" and window._empty_kind == ""


# ── 어두운 메모의 가독성 ────────────────────────────────────────────────

def test_markers_follow_the_note_tone(qapp):
    dark = _sticky(qapp, color="#3A3A3A")
    light = _sticky(qapp, id="s2", color="#FFE066")
    assert dark._text._marker_light is True
    assert light._text._marker_light is False
    glyph_dark = rich_text.marker_display_glyph(rich_text.MARKER_CIRCLE, True)
    glyph_light = rich_text.marker_display_glyph(rich_text.MARKER_CIRCLE, False)
    center = QPoint(rich_text.CHECKBOX_LEFT_PAD + 8, 8)
    on_dark = rich_text.contrast_ratio(glyph_dark.pixelColor(center), QColor("#3A3A3A"))
    on_light = rich_text.contrast_ratio(glyph_light.pixelColor(center), QColor("#FFE066"))
    assert on_dark >= 4.5 and on_light >= 4.5
    _close(dark)
    _close(light)


def test_legacy_marker_images_are_unchanged():
    # 옛 메모의 HTML 은 이 그림의 바이트로 체크박스를 알아본다. 바뀌면 안 된다.
    assert rich_text.is_checkbox_url(rich_text.checkbox_url(True))
    img = rich_text.bullet_image(rich_text.BULLET_CIRCLE)
    center = QPoint(rich_text.CHECKBOX_LEFT_PAD + 8, 8)
    assert img.pixelColor(center) == QColor("#2F2F2F")


def test_highlighted_text_stays_readable_on_a_dark_note(qapp):
    html = '<p>보통 글 <span style="background-color:#fff59d;">형광펜 글</span></p>'
    w = _sticky(qapp, color="#3A3A3A", content=html)
    doc = w._text.document()
    cur = QTextCursor(doc)
    cur.setPosition(doc.toPlainText().index("형광펜") + 1)
    fmt = cur.charFormat()
    ratio = rich_text.contrast_ratio(fmt.foreground().color(),
                                     fmt.background().color())
    assert ratio >= 4.5                                 # 예전에는 흰 글자라 1.1:1
    cur.setPosition(1)
    assert not cur.charFormat().hasProperty(QTextFormat.ForegroundBrush)   # 보통 글은 그대로
    _close(w)


def test_text_made_invisible_by_a_color_change_is_fixed(qapp):
    html = '<p><span style="color:#1f1f1f;">검게 지정한 글</span></p>'
    w = _sticky(qapp, color="#FFE066", content=html)
    w.apply_server_state(_note(color="#3A3A3A", content=html, version=2))
    cur = QTextCursor(w._text.document())
    cur.setPosition(2)
    ratio = rich_text.contrast_ratio(cur.charFormat().foreground().color(),
                                     QColor("#3A3A3A"))
    assert ratio >= 4.5
    _close(w)


def test_private_lock_icon_follows_the_note_tone(qapp):
    w = _sticky(qapp, color="#3A3A3A", private_owner=ME)
    img = w._private_icon.pixmap().toImage()
    lights = sum(1 for x in range(img.width()) for y in range(img.height())
                 if img.pixelColor(x, y).alpha() > 128
                 and img.pixelColor(x, y).lightness() > 180)
    assert lights > 0                                   # 어두운 메모에서는 밝게 그린다
    _close(w)


# ── 화면 밖 메모, 크기 ──────────────────────────────────────────────────

def test_note_saved_off_screen_is_pulled_back(qapp):
    avail = QGuiApplication.primaryScreen().availableGeometry()
    w = StickyNoteWidget(_note(), ME, settings=AppSettings())
    w.apply_local_state(50000, 50000, 300, 240)
    assert avail.contains(w.geometry())
    _close(w)

    kept = StickyNoteWidget(_note(id="k"), ME, settings=AppSettings())
    kept.apply_local_state(avail.left() + 40, avail.top() + 30, 300, 240)
    assert kept.pos() == avail.topLeft() + QPoint(40, 30)   # 멀쩡한 자리는 그대로
    _close(kept)


def test_reveal_brings_back_a_note_that_went_off_screen(qapp):
    avail = QGuiApplication.primaryScreen().availableGeometry()
    w = _sticky(qapp)
    moved: list[tuple] = []
    w.positionChanged.connect(lambda nid, x, y: moved.append((x, y)))
    w.move(60000, 100)                                  # 모니터를 뗀 것처럼
    w.reveal()
    assert avail.contains(w.geometry())
    assert moved and moved[-1] == (w.x(), w.y())
    _close(w)


def test_local_size_is_not_overwritten_by_someone_elses_edit(qapp):
    w = StickyNoteWidget(_note(), ME, settings=AppSettings())
    w.apply_local_state(200, 160, 420, 380)             # 이 PC 에서 맞춰 둔 크기
    w.show()
    qapp.processEvents()
    w.apply_server_state(_note(content="남이 고침", width=300, height=220, version=2))
    assert w.size() == QSize(420, 380)
    _close(w)

    fresh = StickyNoteWidget(_note(id="f"), ME, settings=AppSettings())
    fresh.apply_local_state(200, 160)                   # 정해 둔 크기가 없으면 따른다
    fresh.show()
    qapp.processEvents()
    fresh.apply_server_state(_note(id="f", content="x", width=320, height=300, version=2))
    assert fresh.size() == QSize(320, 300)
    _close(fresh)


# ── 입력이 멈추면 곧 보낸다 ─────────────────────────────────────────────

def test_idle_save_sends_while_keeping_the_lock(qapp):
    w = _sticky(qapp)
    sent: list[str] = []
    released: list[str] = []
    w.commitEdit.connect(lambda nid, content, *rest: sent.append(content))
    w.releaseLock.connect(released.append)
    w.enter_edit_mode()
    w._text.insertPlainText("회의 3시")
    assert w._idle_save_timer.isActive()                # 입력이 타이머를 다시 건다
    w._idle_save()
    assert len(sent) == 1 and "회의 3시" in sent[0]
    assert released == [] and w._edit_mode              # 편집은 이어진다

    # 보낸 것이 돌아오기 전에는 다시 보내지 않는다(판 번호가 어긋난다).
    w._text.insertPlainText(" 대회의실")
    w._idle_save()
    assert len(sent) == 1 and w._idle_save_timer.isActive()
    _close(w)


def test_theme_tokens_meet_contrast():
    white = QColor("#FFFFFF")
    assert rich_text.contrast_ratio(QColor(theme.COLOR_TEXT_MUTED), white) >= 4.5
    assert rich_text.contrast_ratio(QColor(theme.COLOR_PLACEHOLDER), white) >= 4.5
    assert rich_text.contrast_ratio(QColor(theme.COLOR_INPUT_BORDER), white) >= 3.0
    bar = QColor(theme.COLOR_BG_SUBTLE)
    for c in (theme.COLOR_STATE_OK, theme.COLOR_STATE_WAIT, theme.COLOR_STATE_OFF):
        assert rich_text.contrast_ratio(QColor(c), bar) >= 4.5
    qss = theme.build_stylesheet("")
    assert "selection-color" in qss                     # 고른 글자가 흰색으로 묻히지 않는다
    assert "QPushButton:disabled" in qss and 'QPushButton[kbFocus="true"]' in qss
