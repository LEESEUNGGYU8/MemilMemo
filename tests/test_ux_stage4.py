"""UI/UX 개선 이어서(2026-11-19) 테스트(offscreen): 삭제 되돌리기, 오류 번역,
서식 줄, 색 견본 글쇠, 주소 복사, 트레이 안내, 업데이트 취소, 고배율 아이콘."""
from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu, QToolButton, QWidget  # noqa: E402

from app import i18n, updater  # noqa: E402
from app.local_state import LocalStateStore  # noqa: E402
from app.network.client_base import ClientBase  # noqa: E402
from app.settings import AppSettings  # noqa: E402
from app.ui import confirm, icons, theme  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.sticky_note import StickyNoteWidget, _wire_swatch_keys  # noqa: E402

ME = "지우"


class FakeClient(ClientBase):
    def __init__(self):
        super().__init__(ME)
        self.calls: list[tuple] = []
        self.n = 0

    def create_note(self, color, width, height, folder_id=None, private_owner=None, quiet=False):
        self.calls.append(("create", color, width, height, folder_id, private_owner))
        self.n += 1
        self.noteCreated.emit({
            "id": f"new{self.n}", "version": 1, "color": color, "width": width,
            "height": height, "content": "", "folder_id": folder_id,
            "private_owner": private_owner, "created_by": ME,
            "updated_at": time.time()})

    def delete_note(self, note_id):
        self.calls.append(("delete", note_id))
        self.noteDeleted.emit(note_id)

    def acquire_lock(self, note_id):
        self.calls.append(("lock", note_id))

    def release_lock(self, note_id):
        self.calls.append(("release", note_id))

    def update_note(self, note_id, content, color, width, height, expected_version):
        self.calls.append(("update", note_id, content))

    def of(self, kind):
        return [c for c in self.calls if c[0] == kind]


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
    client.connectionChanged.emit(True)
    client.welcomed.emit({
        "users": [ME], "host": ME, "folders": [], "locks": {}, "heartbeat_ms": 3000,
        "notes": [{"id": "n1", "version": 4, "color": "#BAE1FF", "width": 320,
                   "height": 240, "content": "주간 회의 메모", "created_by": "민수",
                   "updated_at": time.time()}]})
    win._local.state.set_note_state("n1", x=410, y=220, hidden=False)
    yield win
    win._force_quit = True
    win._teardown_stickies()
    win.close()
    win.deleteLater()
    qapp.processEvents()


# ── 삭제 되돌리기 ───────────────────────────────────────────────────────

def test_delete_can_be_undone(window, monkeypatch):
    client = window._client
    monkeypatch.setattr(confirm, "ask", lambda *a, **k: True)
    window._delete_note_confirm("n1")
    assert client.of("delete") == [("delete", "n1")]
    assert "n1" not in window._notes
    assert window._undo_btn.isVisibleTo(window) and window._undo_timer.isActive()

    window._undo_btn.click()
    # 같은 색·크기로 다시 만들고, 만들어지면 내용을 넣는다.
    assert client.of("create")[-1][1:4] == ("#BAE1FF", 320, 240)
    assert client.of("lock") == [("lock", "new1")]
    st = window._local.state.get_note_state("new1")
    assert (st.x, st.y) == (410, 220)                   # 내 바탕화면 자리 그대로
    client.lockGranted.emit("new1")
    assert client.of("update") == [("update", "new1", "주간 회의 메모")]
    assert not window._undo_btn.isVisibleTo(window)


def test_undo_disappears_after_a_while(window, monkeypatch):
    monkeypatch.setattr(confirm, "ask", lambda *a, **k: True)
    window._delete_note_confirm("n1")
    window._undo_timer.timeout.emit()
    assert not window._undo_btn.isVisibleTo(window)
    assert window._undo_snapshots == []


def test_cancelled_delete_keeps_the_note(window, monkeypatch):
    monkeypatch.setattr(confirm, "ask", lambda *a, **k: False)
    window._delete_note_confirm("n1")
    assert window._client.of("delete") == [] and "n1" in window._notes


# ── 호스트 오류 번역 ────────────────────────────────────────────────────

def test_host_errors_are_shown_in_the_screen_language(window):
    t = MainWindow._translate_error
    assert t("note locked by 민수") == "민수님이 편집 중입니다."
    assert "먼저 고쳤습니다" in t("update rejected (version mismatch or missing)")
    assert t("not allowed") == "권한이 없습니다."
    assert "비공개 폴더" in t("cannot move into another user's private folder")
    assert "something new" in t("something new")         # 모르는 것은 원문 그대로


# ── 카드 눈 단추 ────────────────────────────────────────────────────────

def test_eye_tooltip_says_what_a_click_does(window):
    card = window._cards["n1"]
    window._set_note_visibility("n1", True)
    assert card._eye_btn.toolTip() == "바탕화면에서 숨기기"
    window._set_note_visibility("n1", False)
    assert card._eye_btn.toolTip() == "바탕화면에 보이기"


# ── 메모 창: 서식 단추는 편집 중에만 ────────────────────────────────────

def test_format_buttons_show_only_while_editing(qapp):
    w = StickyNoteWidget({"id": "s", "version": 1, "color": "#FFE066", "width": 280,
                          "height": 260, "content": "메모"}, ME, settings=AppSettings())
    w.apply_local_state(200, 160)
    w.show()
    qapp.processEvents()
    assert not w._bold_btn.isVisible() and not w._format_bar.isVisible()
    assert w._grip.isVisible()                          # 크기 조절 손잡이는 늘 있다
    # 읽는 동안에는 본문이 메모 끝까지 내려온다(아래에 빈 띠가 남지 않는다).
    assert w._text.geometry().bottom() >= w.height() - 4
    assert w._grip.geometry().bottomRight().x() >= w.width() - 3
    height = w._text.height()
    w.enter_edit_mode()
    qapp.processEvents()
    assert w._bold_btn.isVisible()
    assert w._text.height() < height                    # 편집 중에만 서식 줄이 자리를 쓴다
    w._commit_and_release()
    qapp.processEvents()
    assert not w._format_bar.isVisible() and w._text.height() == height
    w._teardown = True
    w.close()


# ── 색 견본 글쇠 조작 ───────────────────────────────────────────────────

def test_swatches_can_be_picked_with_the_keyboard(qapp):
    menu = QMenu()
    box = QWidget()
    picked: list[int] = []
    buttons = []
    for i in range(6):
        b = QToolButton(box)
        b.clicked.connect(lambda _c=False, i=i: picked.append(i))
        buttons.append(b)
    _wire_swatch_keys(menu, box, columns=3)
    box.show()
    buttons[0].setFocus()
    qapp.processEvents()
    QTest.keyClick(buttons[0], Qt.Key_Right)
    assert buttons[1].hasFocus()
    QTest.keyClick(buttons[1], Qt.Key_Down)
    assert buttons[4].hasFocus()
    QTest.keyClick(buttons[4], Qt.Key_Return)
    assert picked == [4]
    box.deleteLater()
    menu.deleteLater()


# ── 트레이 메뉴 ─────────────────────────────────────────────────────────

def test_tray_menu_is_short_and_every_row_has_an_icon(window):
    menu = QMenu()
    window._fill_tray_menu(menu)
    rows = [a for a in menu.actions() if not a.isSeparator()]
    assert [a.text() for a in rows] == [
        "새 메모", "메인 창 열기", "참가자", "설정", "접속 정보 바꾸기", "종료"]
    # 메모 제목은 넣지 않는다(예전에는 15개까지 늘어섰다).
    assert "주간 회의 메모" not in [a.text() for a in rows]
    from PySide6.QtWidgets import QLabel
    for a in rows:
        icon_label = a.defaultWidget().findChildren(QLabel)[0]
        assert icon_label.pixmap() is not None and not icon_label.pixmap().isNull()
    menu.deleteLater()


# ── 참가자 창: 주소 복사 ────────────────────────────────────────────────

def test_participants_window_copies_the_address(window):
    window._open_sessions()
    header = window._sessions_dialog._header
    address = header._addr_value.text()
    assert address and header._copy_btn.isVisibleTo(header)
    header._copy_btn.click()
    assert QGuiApplication.clipboard().text() == address
    assert header._copy_btn.text() == "복사했습니다"
    window._sessions_dialog.close()


# ── 트레이로 내릴 때 한 번 알린다 ───────────────────────────────────────

def test_close_to_tray_tells_once(window, monkeypatch):
    if window._tray is None:
        pytest.skip("트레이가 없는 환경")
    shown: list[str] = []
    monkeypatch.setattr(window._toasts, "show_text",
                        lambda text, **k: shown.append(text) or True)
    monkeypatch.setattr(window._tray, "isVisible", lambda: True)
    monkeypatch.setattr(window._settings, "save", lambda *a, **k: None)
    window._settings.close_main_to_tray = True
    window.show()
    window.close()
    window.show()
    window.close()
    assert shown == ["트레이에서 계속 실행됩니다."]


# ── 업데이트 받기 취소 ──────────────────────────────────────────────────

def test_cancelled_download_emits_nothing(qapp):
    d = updater.UpdateDownloader({"version": "v9", "download_url": "https://example.invalid/x.exe",
                                  "version_url": None})
    out: list[str] = []
    d.installerReady.connect(out.append)
    d.error.connect(out.append)
    d.cancel()

    class _Reply:
        def deleteLater(self):
            out.append("deleted")

    d._on_installer(_Reply())
    assert out == ["deleted"]


# ── 고배율 화면의 아이콘 ────────────────────────────────────────────────

def test_icons_carry_a_sharp_2x_image():
    # 125~200% 화면에서는 2배 그림을 줄여 쓴다(1배 그림을 늘리면 흐리다).
    icon = icons.svg_icon("plus", 18)
    assert sorted(s.width() for s in icon.availableSizes()) == [18, 36]
