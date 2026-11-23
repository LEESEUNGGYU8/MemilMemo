"""1.0.0 뒤의 수정(offscreen): 앱이 띄우는 알림, 편집 중인 메모 삭제 안내, 끌어다
놓은 이미지 저장, 우클릭 메뉴, 참가자 창, 접힌 아이콘의 작은 그림, 받아 둔 사본."""
from __future__ import annotations

import base64
import os
import sqlite3
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QIODevice, QMimeData, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QColor, QDragEnterEvent, QDropEvent, QImage, QPainter,
)
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QLineEdit, QMessageBox, QPushButton,
)

from app import i18n, replica as replica_mod  # noqa: E402
from app.database import SqliteRepository  # noqa: E402
from app.local_state import LocalStateStore  # noqa: E402
from app.models import Note  # noqa: E402
from app.network.client_base import ClientBase  # noqa: E402
from app.replica import (  # noqa: E402
    ReplicaStore, build_host_database, list_copies, missing_notes, read_copy,
)
from app.settings import AppSettings  # noqa: E402
from app.ui import confirm, edit_menu, note_fold as nf, theme, toast  # noqa: E402
from app.ui.copies_dialog import CopiesDialog  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.sessions_dialog import SessionsDialog  # noqa: E402
from app.ui.settings_dialog import SettingsDialog  # noqa: E402
from app.ui.sticky_note import StickyNoteWidget  # noqa: E402

ME = "지우"
OTHER = "민수"


def image_url(w: int = 120, h: int = 80, color: str = "#3B6EA8", noise: bool = False) -> str:
    if noise:
        img = QImage(os.urandom(w * h * 4), w, h, QImage.Format_RGB32).copy()
    else:
        img = QImage(w, h, QImage.Format_RGB32)
        img.fill(QColor(color))
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(bytes(buf.data())).decode()


def note(nid: str, text: str = "", by: str = OTHER, **more) -> dict:
    return {"id": nid, "version": 1, "color": "#FFE066", "width": 300, "height": 240,
            "content": text, "created_by": by,
            "updated_at": int(time.time() * 1000), **more}


def welcome(*notes: dict, folders=(), locks=None) -> dict:
    return {"users": [ME, OTHER], "host": OTHER, "folders": list(folders),
            "locks": dict(locks or {}), "heartbeat_ms": 3000, "notes": list(notes)}


class FakeClient(ClientBase):
    def __init__(self, nickname: str = ME, *, deletes: bool = True):
        super().__init__(nickname)
        self.calls: list[tuple] = []
        self._deletes = deletes

    def create_note(self, color, width, height, folder_id=None,
                    private_owner=None, quiet=False):
        self.calls.append(("create", folder_id, private_owner, quiet))

    def create_folder(self, name, color, private=False, folder_id=None):
        self.calls.append(("folder", name, private, folder_id))

    def delete_note(self, note_id):
        self.calls.append(("delete", note_id))
        if self._deletes:
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


@pytest.fixture(autouse=True)
def _not_busy(monkeypatch):
    # 시험을 돌리는 PC 가 전체 화면 중이어도 알림 시험이 흔들리지 않게 한다.
    monkeypatch.setattr(toast, "user_is_busy", lambda: False)


@pytest.fixture
def make_window(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(StickyNoteWidget, "bring_to_front",
                        lambda self, *, start_editing=False: None)
    made: list[MainWindow] = []
    told: list[tuple[str, str]] = []
    monkeypatch.setattr(confirm, "notify",
                        lambda parent, title, text, **k: told.append((title, text)))

    def build(*, role="guest", deletes=True, connect=True, notes=(), locks=None):
        client = FakeClient(deletes=deletes)
        win = MainWindow(client=client,
                         local_state=LocalStateStore(tmp_path / f"ls{len(made)}.json"),
                         role=role, settings=AppSettings())
        win.told = told
        made.append(win)
        if connect:
            client.connectionChanged.emit(True)
            client.welcomed.emit(welcome(*notes, locks=locks))
        return win, client

    yield build
    for win in made:
        win._toasts.close_all()
        win._force_quit = True
        win._teardown_stickies()
        win.close()
        win.deleteLater()
    qapp.processEvents()


def make_sticky(qapp, content: str = "", **more) -> tuple[StickyNoteWidget, list, list]:
    w = StickyNoteWidget({"id": "n", "version": 1, "color": "#FFE066", "width": 300,
                          "height": 260, "content": content, **more}, ME,
                         settings=AppSettings())
    sent, locks = [], []
    w.commitEdit.connect(lambda *a: sent.append(a[1]))
    w.requestLock.connect(locks.append)
    w.show()
    qapp.processEvents()
    return w, sent, locks


def drop_image(w: StickyNoteWidget) -> None:
    mime = QMimeData()
    img = QImage(60, 40, QImage.Format_RGB32)
    img.fill(QColor("#B85C38"))
    mime.setImageData(img)
    # Qt 는 끌어 들어온 적이 없는 창에는 놓기를 전하지 않는다. 순서대로 보낸다.
    enter = QDragEnterEvent(QPointF(40, 40).toPoint(), Qt.CopyAction, mime,
                            Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(w._text.viewport(), enter)
    event = QDropEvent(QPointF(40, 40), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(w._text.viewport(), event)
    QApplication.processEvents()


# ── 알림 ────────────────────────────────────────────────────────────────

def test_notification_is_a_window_of_our_own(make_window):
    win, client = make_window()
    client.noteAnnounced.emit({"action": "create", "user": OTHER, "note_id": "x"})
    win._flush_announcements()

    shown = win._toasts.toasts()
    assert [t.text() for t in shown] == ["민수님이 새 메모를 생성했습니다."]
    t = shown[0]
    # 하던 일을 끊지 않는다. 초점을 가져가지 않고 다른 창 위에 뜬다.
    flags = t.windowFlags()
    assert flags & Qt.WindowDoesNotAcceptFocus and flags & Qt.WindowStaysOnTopHint
    assert t.testAttribute(Qt.WA_ShowWithoutActivating)
    assert t._timer.isActive() and t._timer.interval() == toast.SHOW_MS
    # 메인 창이 열려 있으면 상태줄에도 남는다.
    assert win._status.currentMessage() == "민수님이 새 메모를 생성했습니다."


def test_clicking_a_notification_opens_the_main_window(make_window, monkeypatch):
    win, _client = make_window()
    opened = []
    monkeypatch.setattr(win, "_show_main_window", lambda: opened.append(True))
    win._toasts.clicked.disconnect()
    win._toasts.clicked.connect(win._show_main_window)
    win._show_notification("알림")
    win._toasts.toasts()[0].clicked.emit()
    assert opened == [True]


def test_notifications_stack_and_the_oldest_gives_way(qapp):
    manager = toast.ToastManager()
    for i in range(toast.MAX_VISIBLE + 2):
        assert manager.show_text(f"알림 {i}")
    live = manager.toasts()
    assert [t.text() for t in live] == ["알림 2", "알림 3", "알림 4"]
    tops = [t.y() for t in live]
    assert tops == sorted(tops, reverse=True)       # 먼저 뜬 것이 아래
    assert len({t.x() for t in live}) == 1
    manager.close_all()
    assert manager.toasts() == []


def test_no_notification_during_a_presentation(qapp, monkeypatch):
    monkeypatch.setattr(toast, "user_is_busy", lambda: True)
    manager = toast.ToastManager()
    assert manager.show_text("알림") is False and manager.toasts() == []


def test_host_errors_are_shown_as_a_notification(make_window):
    win, client = make_window()
    client.errorOccurred.emit("update rejected (version mismatch or missing)")
    assert [t.text() for t in win._toasts.toasts()] == [i18n.t("error.conflict")]


# ── 편집 중인 메모 지우기 ───────────────────────────────────────────────

def test_deleting_a_note_someone_is_editing_explains_why(make_window, monkeypatch):
    win, client = make_window(notes=[note("a", "회의 메모")], locks={"a": OTHER})
    asked = []
    monkeypatch.setattr(confirm, "ask", lambda *a, **k: asked.append(a) or True)

    win._delete_note_confirm("a")

    # 확인을 묻기 전에, 눈에 띄는 창으로 까닭을 알린다. 호스트로는 보내지 않는다.
    assert win.told == [("삭제할 수 없음", "민수님이 편집 중인 메모입니다.")]
    assert asked == [] and client.of("delete") == []
    assert "a" in win._notes and not win._undo_btn.isVisibleTo(win)


def test_my_own_editing_does_not_block_deleting(make_window, monkeypatch):
    win, client = make_window(notes=[note("a", "회의 메모")], locks={"a": ME})
    monkeypatch.setattr(confirm, "ask", lambda *a, **k: True)
    win._delete_note_confirm("a")
    assert client.of("delete") == [("delete", "a")] and win.told == []


def test_bulk_delete_skips_notes_being_edited(make_window, monkeypatch):
    win, client = make_window(notes=[note("a", "하나"), note("b", "둘"), note("c", "셋")],
                              locks={"b": OTHER})
    asked = []
    monkeypatch.setattr(confirm, "ask", lambda *a, **k: asked.append(a[2]) or True)
    win._selected_notes = {"a", "b", "c"}

    win._bulk_delete()

    assert sorted(c[1] for c in client.of("delete")) == ["a", "c"]
    assert asked == [i18n.t("main.msg_bulk_delete_body", count=2)]
    assert win.told == [("삭제할 수 없음",
                         "1개는 다른 사람이 편집 중이라 지우지 못했습니다.")]
    assert "b" in win._notes


def test_undo_appears_only_after_the_host_deleted(make_window, monkeypatch):
    win, client = make_window(deletes=False, notes=[note("a", "회의 메모")])
    monkeypatch.setattr(confirm, "ask", lambda *a, **k: True)

    win._delete_note_confirm("a")
    # 보냈을 뿐 아직 지워지지 않았다. '지웠습니다'와 되돌리기를 띄우지 않는다.
    assert client.of("delete") == [("delete", "a")]
    assert not win._undo_btn.isVisibleTo(win) and win._undo_snapshots == []

    client.noteDeleted.emit("a")
    assert win._undo_btn.isVisibleTo(win) and len(win._undo_snapshots) == 1
    assert win._status.currentMessage() == i18n.t("main.status_deleted")


def test_refused_delete_leaves_nothing_to_undo(make_window, monkeypatch):
    """확인하는 사이 다른 사람이 편집을 시작해 호스트가 거절했다."""
    win, client = make_window(deletes=False, notes=[note("a", "회의 메모")])
    monkeypatch.setattr(confirm, "ask", lambda *a, **k: True)
    win._delete_note_confirm("a")

    client.errorOccurred.emit("note locked by 민수")

    assert win.told == [("삭제할 수 없음", "민수님이 편집 중인 메모입니다.")]
    assert win._pending_deletes == {} and not win._undo_btn.isVisibleTo(win)
    # 되돌리기를 눌러도 지워지지 않은 메모가 한 벌 더 생기지 않는다.
    win._undo_delete()
    assert client.of("create") == []


# ── 이미지: 끌어다 놓기와 곧바로 저장 ──────────────────────────────────

def test_image_dropped_on_a_resting_note_is_saved(qapp):
    w, sent, locks = make_sticky(qapp)

    drop_image(w)
    # 잠금 없이 화면에만 넣지 않는다. 먼저 편집을 청한다.
    assert locks == ["n"] and "<img" not in w._text.toHtml()

    w.enter_edit_mode()                 # 호스트가 허락했다
    qapp.processEvents()
    assert "<img" in w._text.toHtml()
    w._save_after_image()
    assert len(sent) == 1 and "<img" in sent[0]
    w._teardown = True
    w.close()


def test_drop_is_refused_when_someone_else_is_editing(qapp):
    w, sent, locks = make_sticky(qapp)
    w.set_lock_holder(OTHER)
    drop_image(w)
    assert locks == [] and sent == [] and "<img" not in w._text.toHtml()
    assert w._pending_drop is None and not w._overlay.isHidden()
    w._teardown = True
    w.close()


def test_drop_asks_to_edit_even_offline(qapp):
    """끊긴 동안에도 편집을 청한다(메인 창이 이 PC 에서만 고치게 해 준다)."""
    w, sent, locks = make_sticky(qapp)
    w.set_offline(True)
    drop_image(w)
    assert locks == ["n"] and w._pending_drop is not None
    # 들어줄 수 없으면 놓은 것도 잊는다.
    w.cancel_pending_lock()
    assert w._pending_drop is None and not w._pending_lock
    assert "<img" not in w._text.toHtml()
    w._teardown = True
    w.close()


def test_denied_lock_forgets_the_dropped_image(qapp):
    w, _sent, locks = make_sticky(qapp)
    drop_image(w)
    assert locks == ["n"] and w._pending_drop is not None
    w.show_lock_denied(OTHER)
    assert w._pending_drop is None
    w._teardown = True
    w.close()


def test_large_image_is_sent_right_after_it_is_inserted(qapp, monkeypatch):
    monkeypatch.setattr(StickyNoteWidget, "_IDLE_SAVE_MAX_CHARS", 50)
    w, sent, _locks = make_sticky(qapp)
    w.enter_edit_mode()
    img = QImage(80, 60, QImage.Format_RGB32)
    img.fill(QColor("#3B6EA8"))

    w._text.insert_image(img)
    assert w._image_save_timer.isActive()
    # 입력이 멈췄을 때의 저장은 큰 메모를 건너뛴다. 이미지 직후의 저장은 보낸다.
    w._idle_save()
    assert sent == []
    w._save_after_image()
    assert len(sent) == 1 and "<img" in sent[0]
    w._teardown = True
    w.close()


# ── 우클릭 메뉴 ─────────────────────────────────────────────────────────

def _menu_texts(menu) -> list[str]:
    return [a.text() for a in menu.actions() if not a.isSeparator()]


def _captured_menu(monkeypatch) -> list:
    """메뉴를 띄우지 않고 붙잡는다(띄우면 시험이 멈춘다)."""
    seen: list = []
    monkeypatch.setattr(edit_menu, "run_menu", lambda menu, pos: seen.append(
        [(a.text(), a.isEnabled()) for a in menu.actions() if not a.isSeparator()]))
    return seen


def test_note_menu_while_resting(qapp, monkeypatch):
    seen = _captured_menu(monkeypatch)
    w, _sent, locks = make_sticky(qapp, "회의는 3시")
    w._show_text_menu(w._text.viewport().rect().center(), w.mapToGlobal(w.rect().center()))
    assert seen[-1] == [("편집", True), ("전체 복사", True)]

    w.set_lock_holder(OTHER)
    w._show_text_menu(w._text.viewport().rect().center(), w.mapToGlobal(w.rect().center()))
    assert seen[-1] == [("편집", False), ("전체 복사", True)]
    w._teardown = True
    w.close()


def test_note_menu_while_editing_is_in_the_app_language(qapp, monkeypatch):
    seen = _captured_menu(monkeypatch)
    w, _sent, _locks = make_sticky(qapp, "회의는 3시")
    w.enter_edit_mode()
    w._text.selectAll()
    w._show_text_menu(w._text.viewport().rect().center(), w.mapToGlobal(w.rect().center()))
    texts = [t for t, _on in seen[-1]]
    assert texts == ["실행 취소", "다시 실행", "잘라내기", "복사", "붙여넣기", "모두 선택"]
    assert dict(seen[-1])["잘라내기"] and dict(seen[-1])["복사"]
    assert not dict(seen[-1])["실행 취소"]           # 아직 고친 것이 없다
    w._teardown = True
    w.close()


def test_note_menu_on_an_image(qapp, monkeypatch):
    seen = _captured_menu(monkeypatch)
    w, _sent, _locks = make_sticky(qapp, f'<p><img src="{image_url(200, 120)}" /></p>')
    monkeypatch.setattr(w._text, "content_image_at", lambda pos: (image_url(200, 120), 0))
    w._show_text_menu(w._text.viewport().rect().center(), w.mapToGlobal(w.rect().center()))
    assert [t for t, _ in seen[-1]][:3] == ["크게 보기", "이미지 복사", "이미지 저장"]
    w._teardown = True
    w.close()


def test_line_edits_get_the_same_menu(qapp, monkeypatch):
    edit = QLineEdit("검색어")
    edit.selectAll()
    menu = edit_menu.build_edit_menu(edit)
    assert _menu_texts(menu) == ["실행 취소", "다시 실행", "잘라내기", "복사",
                                "붙여넣기", "모두 선택"]
    read_only = QLineEdit("주소")
    read_only.setReadOnly(True)
    read_only.selectAll()
    assert _menu_texts(edit_menu.build_edit_menu(read_only)) == ["복사", "모두 선택"]
    # 앱 전체에 걸어 둔 필터가 Qt 의 영어 메뉴 대신 이 메뉴를 띄운다.
    assert isinstance(qapp._edit_menu_filter, edit_menu.EditMenuFilter)
    for lang in ("en", "ja"):
        i18n.set_language(lang)
        assert all(t and "." not in t for t in _menu_texts(edit_menu.build_edit_menu(edit)))
    i18n.set_language("ko")


# ── 참가자 창 ───────────────────────────────────────────────────────────

def test_participants_window_is_flat_and_short(qapp):
    dlg = SessionsDialog()
    dlg.refresh(host=OTHER, online={ME, OTHER, "서연"}, my_nickname=ME, my_role="guest",
                records={}, connected=True, heartbeat_ms=3000,
                peer_address="192.168.10.3:5555")
    header = dlg._header
    assert "gradient" not in header.styleSheet()
    assert header._host_name.text() == OTHER
    assert header._addr_value.text() == "192.168.10.3:5555"
    assert header._count_pill.text() == "접속 3명"
    from PySide6.QtWidgets import QLabel
    texts = " ".join(lbl.text() for lbl in dlg.findChildren(QLabel))
    # 연결 확인 주기 같은 내부 값과, 줄마다 되풀이하던 '현재 접속 중'은 적지 않는다.
    for gone in ("확인 주기", "3초", "역할", "현재 접속 중"):
        assert gone not in texts
    assert "호스트" in texts and "본인" in texts
    dlg.deleteLater()


# ── 접힌 아이콘의 작은 그림 ─────────────────────────────────────────────

def _render_icon(thumb) -> QImage:
    cell = nf.ICON_CELL_DEFAULT
    img = QImage(cell, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    nf.paint_icon(p, QRectF(0, 0, cell.width(), cell.height()),
                  "#FFE066", "메모", thumb=thumb)
    p.end()
    return img


def test_folded_icon_shows_the_first_image(qapp):
    red = image_url(300, 200, "#D0312D")
    html = f'<p>사진</p><p><img src="{red}" /></p>'
    thumb = nf.note_thumbnail(html)
    assert thumb is not None and not thumb.isNull()
    assert nf.note_thumbnail(html) is thumb                  # 다시 풀지 않는다
    assert nf.note_thumbnail("<p>글만 있는 메모</p>") is None

    plain, with_thumb = _render_icon(None), _render_icon(thumb)
    assert plain != with_thumb
    glyph = nf.icon_glyph_rect(QRectF(0, 0, nf.ICON_CELL_DEFAULT.width(),
                                      nf.ICON_CELL_DEFAULT.height()))
    center = with_thumb.pixelColor(int(glyph.center().x()), int(glyph.center().y() + 6))
    assert center.red() > 180 and center.green() < 90        # 종이 안에 사진이 보인다


def test_folded_note_passes_its_image_to_the_icon(qapp):
    w, _sent, _locks = make_sticky(qapp, f'<p><img src="{image_url(200, 120)}" /></p>')
    assert w._fold_thumb() is not None
    w.apply_server_state({"id": "n", "version": 2, "content": "글만"})
    assert w._fold_thumb() is None
    w._teardown = True
    w.close()


# ── 받아 둔 사본 ────────────────────────────────────────────────────────

@pytest.fixture
def copies(tmp_path):
    """옮겨 둔 옛 사본 한 벌과 지금 사본 한 벌."""
    path = tmp_path / "replica.sqlite3"
    store = ReplicaStore(path)
    store.apply_snapshot(
        [note("a", "공개 메모"), note("b", "내 비공개", by=ME, private_owner=ME),
         note("c", "폴더 속 메모", by=ME, folder_id="f1")],
        [{"id": "f1", "name": "내 폴더", "color": "#BAE1FF", "private_owner": ME,
          "created_by": ME, "created_at": 1, "updated_at": 1}],
        host_id="H1", host=OTHER, nickname=ME)
    store.apply_snapshot([note("x", "새 공유 PC 의 메모")], [], host_id="H2",
                         host="서연", nickname=ME)
    store.close()
    return path


def test_copies_are_listed_current_first(copies):
    found = list_copies(copies)
    assert [(c.current, c.host, c.note_count) for c in found] == [
        (True, "서연", 1), (False, OTHER, 3)]
    notes, folders, meta = read_copy(found[1].path)
    assert {n["id"] for n in notes} == {"a", "b", "c"} and len(folders) == 1
    assert meta["nickname"] == ME
    assert list_copies(copies.parent / "nowhere.sqlite3") == []


def test_missing_notes_never_include_what_the_host_has():
    mine = [note("a", "같은 글"), note("b", "내 쪽이 나중", updated_at=200),
            note("c", "호스트 쪽이 나중", updated_at=100), note("d", "호스트에 없음")]
    host = {"a": note("a", "같은 글"), "b": note("b", "옛 글", updated_at=100),
            "c": note("c", "새 글", updated_at=200)}
    assert [n["id"] for n in missing_notes(mine, host)] == ["b", "d"]


def test_copies_dialog_marks_and_offers_what_is_missing(qapp, copies):
    found = list_copies(copies)
    host_notes = {"a": note("a", "공개 메모")}
    dlg = CopiesDialog(found, host_notes=host_notes, can_takeover=True)
    dlg._pick.setCurrentIndex(1)                    # 옮겨 둔 옛 사본
    rows = [dlg._list.item(i).text() for i in range(dlg._list.count())]
    # 공유 PC 에 없는 메모를 위에 따로 묶어 보여 준다.
    assert rows[0] == "공유 PC에 없는 메모 2" and rows[3] == "공유 PC에도 있는 메모 1"
    assert sorted(rows[1:3]) == ["내 비공개", "폴더 속 메모"] and rows[4] == "공개 메모"
    assert dlg._import_btn.text() == "2개 가져오기"
    assert dlg._list.currentRow() == 1 and dlg._preview.toPlainText() in (
        "내 비공개", "폴더 속 메모")
    asked, taken = [], []
    dlg.importRequested.connect(lambda notes, folders: asked.append((notes, folders)))
    dlg.takeoverRequested.connect(taken.append)
    dlg._import_btn.click()
    assert sorted(n["id"] for n in asked[0][0]) == ["b", "c"] and len(asked[0][1]) == 1
    dlg._takeover_btn.click()
    assert taken == [str(found[1].path)]

    # 연결돼 있지 않으면 가져올 수 없다. 호스트로 쓰는 PC 에는 이어받기가 없다.
    offline = CopiesDialog(found, host_notes=None, can_takeover=False)
    assert not offline._import_btn.isEnabled() and offline._takeover_btn.isHidden()
    dlg.deleteLater()
    offline.deleteLater()


def test_import_creates_new_notes_and_keeps_them_private(make_window):
    win, client = make_window(notes=[note("a", "공개 메모")])
    client.calls.clear()
    folders = [{"id": "f1", "name": "내 폴더", "color": "#BAE1FF", "private_owner": ME}]

    win._import_from_copy(
        [note("b", "내 비공개", by=ME, private_owner=ME),
         note("c", "폴더 속 메모", by=ME, folder_id="f1"),
         note("d", "공개였던 메모")], folders)

    made_folder = client.of("folder")
    assert len(made_folder) == 1 and made_folder[0][1:3] == ("내 폴더", True)
    new_folder = made_folder[0][3]
    assert new_folder and new_folder != "f1"         # 옛 id 를 다시 쓰지 않는다
    # 새 메모로 만들고(있는 메모를 덮어쓰지 않는다), 알림 없이, 나만 보던 것은 나만 보게.
    assert client.of("create") == [("create", None, ME, True),
                                   ("create", new_folder, ME, True),
                                   ("create", None, None, True)]
    assert [s["content"] for s in win._recreate_queue] == [
        "내 비공개", "폴더 속 메모", "공개였던 메모"]
    assert all(s["hidden"] for s in win._recreate_queue)     # 바탕화면에 쏟아지지 않는다


def test_import_needs_a_connection(make_window):
    win, client = make_window(connect=False)
    win._import_from_copy([note("b", "메모")], [])
    assert client.calls == [] and win._recreate_queue == []


def test_taking_over_builds_the_notes_file_from_a_copy(copies, tmp_path):
    old_copy = list_copies(copies)[1].path
    db = tmp_path / "host" / "notes.sqlite3"
    db.parent.mkdir()
    # 이 PC 에는 예전에 공유 PC 였을 때의 메모 파일이 남아 있다.
    earlier = SqliteRepository(db)
    keep = Note.new("지우")
    keep.content = "예전 메모 파일의 메모"
    earlier.insert_note(keep)
    earlier.close()

    kept = build_host_database(old_copy, db, "지우새이름")

    # 있던 파일은 지우지 않고 옮겨 둔다.
    assert kept is not None and kept.exists() and kept.name.startswith(
        "notes.before-takeover-")
    conn = sqlite3.connect(str(kept))
    assert [r[0] for r in conn.execute("SELECT content FROM notes")] == [
        "예전 메모 파일의 메모"]
    conn.close()
    repo = SqliteRepository(db)
    notes = {n.id: n for n in repo.list_notes()}
    assert {n.content for n in notes.values()} == {"공개 메모", "내 비공개", "폴더 속 메모"}
    # 닉네임이 바뀌었어도 나만 보던 메모와 폴더는 지금 닉네임의 것이 된다.
    assert notes["b"].private_owner == "지우새이름" and notes["a"].private_owner is None
    folders = repo.list_folders()
    assert [(f.name, f.private_owner) for f in folders] == [("내 폴더", "지우새이름")]
    assert notes["c"].folder_id == folders[0].id
    assert repo.check() == "" and repo.host_id()
    repo.close()
    # 사본 파일은 그대로다.
    assert len(read_copy(old_copy)[0]) == 3


def test_taking_over_refuses_an_empty_copy(tmp_path):
    empty = ReplicaStore(tmp_path / "replica.sqlite3")
    empty.close()
    with pytest.raises(ValueError):
        build_host_database(tmp_path / "replica.sqlite3", tmp_path / "notes.sqlite3", ME)
    assert not (tmp_path / "notes.sqlite3").exists()


def test_settings_and_banner_lead_to_the_copies(qapp, make_window, monkeypatch, tmp_path):
    asked = []
    dlg = SettingsDialog(AppSettings(), role="guest")
    dlg.copiesRequested.connect(lambda: asked.append("settings"))
    button = [b for b in dlg.findChildren(QPushButton)
              if b.text() == i18n.t("settings.btn_copies")]
    assert len(button) == 1
    button[0].click()
    assert asked == ["settings"]
    dlg.deleteLater()

    win, _client = make_window(connect=False)
    monkeypatch.setattr(replica_mod, "list_copies", lambda path, **k: [])
    monkeypatch.setattr("app.ui.main_window.list_copies", lambda path, **k: [])
    win._copies_btn.click()
    assert win.told == [(i18n.t("copies.window_title"), i18n.t("copies.empty"))]
    assert QMessageBox is not None
