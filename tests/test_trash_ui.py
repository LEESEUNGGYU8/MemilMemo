"""휴지통의 화면: 묻지 않고 지우기, 되돌리기, 되살아난 메모의 자리, 휴지통 창."""
from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QKeySequence, QShortcut  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app import config, i18n  # noqa: E402
from app.datetime_fmt import fmt_relative  # noqa: E402
from app.local_state import LocalStateStore  # noqa: E402
from app.network.client_base import ClientBase  # noqa: E402
from app.settings import AppSettings  # noqa: E402
from app.ui import confirm  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.sticky_note import StickyNoteWidget  # noqa: E402
from app.ui.trash_dialog import TrashDialog, days_left  # noqa: E402
from app.settings import (  # noqa: E402
    VIEW_COMPACT, VIEW_DETAILED, VIEW_GRID, VIEW_ICONS, VIEW_LIST, VIEW_STANDARD,
)
from app.ui import edit_menu  # noqa: E402

ME, OTHER = "지우", "민수"
DAY = 24 * 60 * 60 * 1000


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    i18n.set_language("ko")
    yield app


def live(note_id: str, text: str = "<p>회의</p>") -> dict:
    now = int(time.time() * 1000)
    return {"id": note_id, "content": text, "color": "#FFE066", "version": 1,
            "width": 280, "height": 260, "created_by": OTHER, "updated_by": OTHER,
            "created_at": now, "updated_at": now, "deleted": 0,
            "folder_id": None, "private_owner": None}


class FakeClient(ClientBase):
    def __init__(self, nickname: str = ME):
        super().__init__(nickname)
        self.calls: list[tuple] = []

    def delete_note(self, note_id):
        self.calls.append(("delete", note_id))

    def restore_note(self, note_id):
        self.calls.append(("restore", note_id))

    def purge_note(self, note_id):
        self.calls.append(("purge", note_id))

    def empty_trash(self):
        self.calls.append(("empty",))

    def get_trash(self):
        self.calls.append(("get_trash",))

    def create_note(self, color, width, height, folder_id=None,
                    private_owner=None, quiet=False):
        self.calls.append(("create", color, quiet))

    def release_lock(self, note_id):
        self.calls.append(("release", note_id))

    def of(self, kind):
        return [c for c in self.calls if c[0] == kind]


def open_window(qapp, tmp_path, monkeypatch, *, trash_days=config.TRASH_KEEP_DAYS):
    monkeypatch.setattr(StickyNoteWidget, "bring_to_front",
                        lambda self, *, start_editing=False: None)
    client = FakeClient()
    win = MainWindow(client=client,
                     local_state=LocalStateStore(tmp_path / "local_state.json"),
                     role="guest", settings=AppSettings())
    client.connectionChanged.emit(True)
    welcome = {"users": [ME, OTHER], "host": OTHER, "folders": [], "locks": {},
               "heartbeat_ms": 3000, "notes": [live("a"), live("b")]}
    if trash_days:
        welcome["trash_days"] = trash_days
    client._dispatch("welcome", welcome)
    qapp.processEvents()
    return win, client


def close_window(qapp, win) -> None:
    win._force_quit = True
    if win._trash_dialog is not None:
        win._trash_dialog.close()
    win._teardown_stickies()
    win.close()
    win.deleteLater()
    qapp.processEvents()


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    win, client = open_window(qapp, tmp_path, monkeypatch)
    yield win, client
    close_window(qapp, win)


def refuse_questions(monkeypatch) -> list:
    asked: list = []
    monkeypatch.setattr(confirm, "ask",
                        lambda *a, **k: asked.append(a) or False)
    return asked


def test_deleting_does_not_ask_when_there_is_a_trash(window, monkeypatch):
    win, client = window
    asked = refuse_questions(monkeypatch)
    win._delete_note_confirm("a")
    assert asked == [] and client.of("delete") == [("delete", "a")]

    client._dispatch("note_deleted", {"id": "a"})
    assert "a" not in win._notes
    assert win._status.currentMessage() == i18n.t("main.status_trashed")
    assert not win._undo_btn.isHidden()


def test_an_older_host_still_asks_before_deleting(qapp, tmp_path, monkeypatch):
    win, client = open_window(qapp, tmp_path, monkeypatch, trash_days=0)
    try:
        asked = refuse_questions(monkeypatch)
        win._delete_note_confirm("a")
        assert len(asked) == 1 and client.of("delete") == []
        # 휴지통이 없으니 휴지통 창도 열리지 않는다.
        win.open_trash()
        assert win._trash_dialog is None and client.of("get_trash") == []
    finally:
        close_window(qapp, win)


def test_several_notes_go_to_the_trash_at_once(window, monkeypatch):
    win, client = window
    asked = refuse_questions(monkeypatch)
    win.delete_notes(["a", "b", "a", "gone"])
    assert asked == []
    assert client.of("delete") == [("delete", "a"), ("delete", "b")]
    client._dispatch("note_deleted", {"id": "a"})
    assert win._status.currentMessage() == i18n.t("main.bulk_trashed", count=2)


def test_undo_restores_the_same_note_where_it_was(window):
    win, client = window
    win._local.state.set_note_state("a", x=640, y=320, hidden=False)
    win._delete_note_confirm("a")
    client._dispatch("note_deleted", {"id": "a"})
    assert "a" in win._local.state.trashed

    win._undo_delete()
    assert client.of("restore") == [("restore", "a")] and client.of("create") == []

    client._dispatch("note_created", {"note": live("a"), "restored_by": ME})
    state = win._local.state.notes["a"]
    assert (state.x, state.y, state.hidden) == (640, 320, False)
    assert "a" in win._notes and "a" in win._stickies
    assert "a" not in win._local.state.trashed


def test_a_note_someone_else_restores_comes_back_as_i_left_it(window):
    win, client = window
    win._local.state.set_note_state("b", x=200, y=150, hidden=True)
    client._dispatch("note_deleted", {"id": "b"})
    client._dispatch("note_created", {"note": live("b"), "restored_by": OTHER})
    state = win._local.state.notes["b"]
    assert (state.x, state.y, state.hidden) == (200, 150, True)


def test_a_restored_note_this_pc_never_placed_shows_only_to_who_restored(window):
    win, client = window
    client._dispatch("note_created", {"note": live("c"), "restored_by": OTHER})
    assert win._local.state.notes["c"].hidden is True
    client._dispatch("note_created", {"note": live("d"), "restored_by": ME})
    assert win._local.state.notes["d"].hidden is False


def test_trashed_places_survive_a_restart_and_expire(tmp_path):
    store = LocalStateStore(tmp_path / "local_state.json")
    store.state.set_note_state("a", x=11, y=22, folded=True, fold_style="icon",
                               fold_x=5, fold_y=6, fold_at=True)
    store.state.set_note_state("old", x=1, y=2)
    store.state.keep_trashed("a")
    store.state.keep_trashed("old")
    at, state = store.state.trashed["old"]
    store.state.trashed["old"] = (at - (config.TRASH_KEEP_DAYS + 2) * DAY, state)
    store.save()

    again = LocalStateStore(tmp_path / "local_state.json")
    assert set(again.state.trashed) == {"a"} and "a" not in again.state.notes
    kept = again.state.take_trashed("a")
    assert (kept.x, kept.y, kept.folded, kept.fold_style) == (11, 22, True, "icon")
    assert (kept.fold_x, kept.fold_y, kept.fold_at) == (5, 6, True)


def test_the_trash_window_lists_and_asks_the_host(window, monkeypatch):
    win, client = window
    win.open_trash()
    dlg = win._trash_dialog
    assert dlg is not None and client.of("get_trash") == [("get_trash",)]

    now = int(time.time() * 1000)
    client._dispatch("trash_list", {"notes": [
        dict(live("x", "<p>견적서</p>"), deleted_at=now - 3 * DAY, deleted_by=OTHER),
        dict(live("y", "<p>회식</p>"), deleted_at=now - DAY, deleted_by=ME),
    ], "keep_days": 30})
    assert dlg.note_ids() == ["y", "x"]                   # 방금 버린 것부터
    assert dlg.icon_view().ids() == ["y", "x"] and set(dlg.cards()) == {"x", "y"}
    # 파일처럼, 고르기 전에는 아무것도 골라져 있지 않다.
    assert dlg.selected_ids() == [] and not dlg._restore_btn.isEnabled()
    dlg.select(["y"])

    dlg._restore()
    assert client.of("restore") == [("restore", "y")]

    # 완전히 지우기는 묻는다. 거절하면 아무것도 보내지 않는다.
    asked = refuse_questions(monkeypatch)
    dlg._purge()
    dlg._empty_all()
    assert len(asked) == 2 and client.of("purge") == [] and client.of("empty") == []
    monkeypatch.setattr(confirm, "ask", lambda *a, **k: True)
    dlg._purge()
    dlg._empty_all()
    assert client.of("purge") == [("purge", "y")] and client.of("empty") == [("empty",)]

    # 누가 휴지통을 바꾸면 열어 둔 창이 다시 묻는다.
    client._dispatch("trash_changed", {})
    assert len(client.of("get_trash")) == 2
    client._dispatch("trash_list", {"notes": []})
    assert dlg.note_ids() == [] and dlg._stack.currentWidget() is dlg._empty
    assert dlg.selected_ids() == []
    assert not dlg._restore_btn.isEnabled() and not dlg._empty_btn.isEnabled()


def test_days_left_counts_down_to_zero():
    now = 1_000 * DAY
    assert days_left(now, 30, now) == 30
    assert days_left(now - DAY - 1, 30, now) == 29
    assert days_left(now - 30 * DAY + 60_000, 30, now) == 1
    assert days_left(now - 31 * DAY, 30, now) == 0


def trashed(note_id: str, text: str, days_ago: int, by: str = OTHER) -> dict:
    now = int(time.time() * 1000)
    return dict(live(note_id, f"<p>{text}</p>"), deleted_at=now - days_ago * DAY,
                deleted_by=by)


@pytest.fixture
def trash(qapp):
    settings = AppSettings()
    settings.save = lambda path=None: None
    dlg = TrashDialog(30, settings)
    dlg.set_notes([trashed("a", "견적서", 1), trashed("b", "회식", 2),
                   trashed("c", "토너", 3), trashed("d", "주차", 4)])
    yield dlg, settings
    dlg.close()
    dlg.deleteLater()


def test_dialog_can_be_built_alone(qapp):
    dlg = TrashDialog(30)
    dlg.set_notes([dict(live("x"), deleted_at=1, deleted_by="")])
    assert dlg.selected_ids() == [] and not dlg._restore_btn.isEnabled()
    dlg.select(["x"])
    assert dlg._restore_btn.isEnabled() and dlg._purge_btn.isEnabled()
    dlg.deleteLater()


def test_the_trash_has_the_same_views_as_a_folder_window(trash):
    dlg, settings = trash
    assert settings.trash_view_layout == VIEW_LIST
    assert dlg._stack.currentWidget() is dlg._card_scroll
    dlg.set_view(layout=VIEW_ICONS)
    assert dlg._stack.currentWidget() is dlg._icon_scroll
    dlg.set_view(layout=VIEW_GRID, density=VIEW_COMPACT)
    assert dlg._stack.currentWidget() is dlg._card_scroll
    # 폴더 창의 보기 방식과는 따로 적어 둔다.
    assert (settings.trash_view_layout, settings.trash_view_density) == (
        VIEW_GRID, VIEW_COMPACT)
    assert settings.folder_view_layout == VIEW_ICONS


def test_trash_cards_only_select_and_tell_the_days_left(trash):
    dlg, _settings = trash
    card = dlg.cards()["a"]
    assert card._eye_btn.isHidden() and card._menu_btn.isHidden()
    assert card._timestamp.text() == i18n.t("trash.days_left", days=29)
    # 체크는 왼쪽 끝에 있다.
    assert not card._check.isHidden() and card._top.itemAt(0).widget() is card._check
    dlg.set_view(density=VIEW_DETAILED)
    assert OTHER not in card._meta.text()        # 누가 지웠는지는 미리 보기에 있다


def test_a_list_card_keeps_the_title_beside_the_days_left(trash):
    """윗줄에 남은 날만 있으면 카드가 한 줄만큼 헛되이 길어진다."""
    dlg, _settings = trash
    card = dlg.cards()["a"]
    dlg.set_view(layout=VIEW_LIST, density=VIEW_STANDARD)
    assert not card._inline_title.isHidden() and card._title.isHidden()
    assert card._inline_title.text() == "견적서"
    plain = card.heightForWidth(560)
    card.set_plain(False)                        # 메인 창의 카드와 견준다
    assert plain < card.heightForWidth(560)
    card.set_plain(True)
    dlg.set_view(layout=VIEW_GRID)               # 그리드에서는 제목이 아래에 있다
    assert card._inline_title.isHidden() and not card._title.isHidden()


def test_cards_are_checked_and_unchecked_by_clicking(trash, monkeypatch):
    dlg, _settings = trash
    held = {"mods": Qt.NoModifier}
    monkeypatch.setattr(QApplication, "keyboardModifiers",
                        staticmethod(lambda: held["mods"]))
    dlg._on_card_clicked("a")
    assert dlg.selected_ids() == ["a"]
    dlg._on_card_clicked("c")                   # 다른 것은 그대로 둔다
    assert dlg.selected_ids() == ["a", "c"]
    dlg._on_card_clicked("a")                   # 다시 누르면 풀린다
    assert dlg.selected_ids() == ["c"]
    dlg._on_card_clicked("c")
    assert dlg.selected_ids() == [] and not dlg._restore_btn.isEnabled()
    held["mods"] = Qt.ControlModifier           # Ctrl 을 눌러도 같다
    dlg._on_card_clicked("b")
    assert dlg.selected_ids() == ["b"]
    held["mods"] = Qt.ShiftModifier
    dlg._on_card_clicked("d")                   # 마지막으로 누른 b 에서 d 까지 보탠다
    assert dlg.selected_ids() == ["b", "c", "d"]
    assert dlg.cards()["c"].is_selected() and not dlg.cards()["a"].is_selected()


def test_a_real_click_on_a_checked_card_unchecks_it_and_leaves_the_others(trash, qapp):
    """마우스로 눌렀을 때의 일. 카드가 받지 않은 누름은 바탕으로도 넘어가는데,
    거기서 모두 풀어 버리면 뗄 때 카드가 다시 체크한다."""
    dlg, _settings = trash
    dlg.resize(600, 430)
    dlg.show()
    qapp.processEvents()
    a, b = dlg.cards()["a"], dlg.cards()["b"]
    QTest.mouseClick(a, Qt.LeftButton, pos=QPoint(60, 14))
    QTest.mouseClick(b, Qt.LeftButton, pos=QPoint(60, 14))
    assert dlg.selected_ids() == ["a", "b"]
    QTest.mouseClick(a, Qt.LeftButton, pos=QPoint(60, 14))
    assert dlg.selected_ids() == ["b"] and not a.is_selected() and b.is_selected()
    QTest.mouseClick(b, Qt.LeftButton, pos=QPoint(60, 14))
    assert dlg.selected_ids() == []
    dlg.hide()


def test_ctrl_a_checks_everything_in_the_list_and_the_grid(trash):
    dlg, _settings = trash
    keys = [k for k in dlg.findChildren(QShortcut)
            if k.key() == QKeySequence(QKeySequence.SelectAll)]
    assert len(keys) == 1
    for layout in (VIEW_LIST, VIEW_GRID, VIEW_ICONS):
        dlg.set_view(layout=layout)
        dlg.select([])
        keys[0].activated.emit()
        assert dlg.selected_ids() == ["a", "b", "c", "d"]
        assert all(card.is_selected() for card in dlg.cards().values())
        assert dlg.icon_view().selected_ids() == ["a", "b", "c", "d"]
    assert dlg._count.text() == ""              # 창 위쪽에 안내 문구를 두지 않는다


def test_clicking_between_the_cards_clears_the_checks(trash, qapp):
    dlg, _settings = trash
    dlg.resize(600, 430)
    dlg.show()
    qapp.processEvents()
    dlg.select(["a", "b"])
    QTest.mouseClick(dlg._cards_container, Qt.LeftButton, pos=QPoint(2, 2))
    assert dlg.selected_ids() == []
    dlg.hide()


def test_enter_on_a_card_opens_the_preview_and_space_checks_it(trash):
    dlg, _settings = trash
    card = dlg.cards()["b"]
    QTest.keyClick(card, Qt.Key_Space)
    assert dlg.selected_ids() == ["b"]
    QTest.keyClick(card, Qt.Key_Return)
    assert dlg._preview is not None and dlg._preview.note_id() == "b"
    assert dlg.selected_ids() == ["b"]
    dlg._preview.close()


def test_the_selection_is_the_same_in_every_view(trash):
    dlg, _settings = trash
    dlg.set_view(layout=VIEW_ICONS)
    dlg.icon_view().select(["b", "c"])
    assert dlg.selected_ids() == ["b", "c"]
    assert dlg.cards()["b"].is_selected() and dlg.cards()["c"].is_selected()
    dlg.select(["d"])
    assert dlg.icon_view().selected_ids() == ["d"]
    # 고른 것이 휴지통에서 사라지면 고른 것에서도 빠진다.
    dlg.set_notes([trashed("a", "견적서", 1)])
    assert dlg.selected_ids() == [] and not dlg._restore_btn.isEnabled()


def test_opening_a_trashed_note_shows_its_text_and_can_restore(trash):
    dlg, _settings = trash
    restored: list = []
    dlg.restoreRequested.connect(restored.append)
    dlg.noteActivated.emit("b")                 # 두 번 눌렀다
    preview = dlg._preview
    assert preview is not None and preview.text() == "회식"
    assert preview.windowTitle() == "회식"
    # 삭제 정보는 미리 보기 안에 칸을 나눠 적는다.
    assert preview.facts() == {
        "by": OTHER, "when": fmt_relative(dlg._note("b")["deleted_at"]),
        "left": i18n.t("trash.info_days", days=28)}
    preview._restore()
    assert restored == [["b"]] and preview.isHidden()
    # 보고 있던 메모가 휴지통에서 사라지면 미리 보기도 닫힌다.
    dlg.noteActivated.emit("c")
    dlg.set_notes([trashed("a", "견적서", 1)])
    assert preview.isHidden()
    preview.close()


def test_the_menu_acts_on_the_selection(trash, monkeypatch):
    dlg, _settings = trash
    restored: list = []
    purged: list = []
    dlg.restoreRequested.connect(restored.append)
    dlg.purgeRequested.connect(purged.append)
    monkeypatch.setattr(confirm, "ask", lambda *a, **k: True)
    pick = {"text": i18n.t("trash.btn_restore")}
    shown: list[list[str]] = []

    def run(menu, _pos):
        shown.append([a.text() for a in menu.actions() if not a.isSeparator()])
        next(a for a in menu.actions() if a.text() == pick["text"]).trigger()

    monkeypatch.setattr(edit_menu, "run_menu", run)
    dlg.select(["a", "b"])
    dlg._on_card_menu("b", QPoint(0, 0))        # 고른 것 가운데 하나: 전체에
    assert shown == [[i18n.t("trash.btn_restore"), i18n.t("trash.btn_purge")]]
    assert restored == [["a", "b"]]
    pick["text"] = i18n.t("trash.btn_purge")
    dlg._on_card_menu("d", QPoint(0, 0))        # 고르지 않은 것: 그것만
    assert dlg.selected_ids() == ["d"] and purged == [["d"]]
    # 아이콘 보기에서 Delete 를 눌렀다.
    dlg.deleteRequested.emit(["a", "c"])
    assert purged == [["d"], ["a", "c"]]
