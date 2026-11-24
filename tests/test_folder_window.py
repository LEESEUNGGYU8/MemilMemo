"""폴더 아이콘을 열면 뜨는 창: 아이콘 보기와 카드 보기, 메모 꺼내 보기."""
from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QContextMenuEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app import config, desktop_icons, i18n  # noqa: E402
from app.local_state import LocalStateStore  # noqa: E402
from app.network.client_base import ClientBase  # noqa: E402
from app.settings import (  # noqa: E402
    VIEW_COMPACT, VIEW_GRID, VIEW_ICONS, VIEW_LIST, AppSettings,
)
from app.ui import edit_menu  # noqa: E402
from app.ui import note_fold as nf  # noqa: E402
from app.ui.folder_window import FolderWindow, NoteIconView  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.sticky_note import StickyNoteWidget  # noqa: E402

ME, OTHER = "지우", "민수"


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    i18n.set_language("ko")
    yield app


@pytest.fixture(autouse=True)
def _double_click(monkeypatch):
    monkeypatch.setattr(desktop_icons, "single_click_opens", lambda: False)


def item(note_id: str, title: str, *, hidden: bool = False) -> dict:
    note = {"id": note_id, "content": f"<p>{title}</p>", "color": "#FFE066",
            "version": 1, "width": 280, "height": 260, "created_by": OTHER,
            "updated_by": OTHER, "created_at": 1, "updated_at": 1, "deleted": 0,
            "folder_id": "f1", "private_owner": None}
    return {"id": note_id, "title": title, "color": "#FFE066", "private": False,
            "hidden": hidden, "content": note["content"], "note": note}


# ── 아이콘 보기 ─────────────────────────────────────────────────────────

@pytest.fixture
def view(qapp):
    v = NoteIconView()
    v.resize(4 * 86 + 20 + 10, 300)           # 한 줄에 네 개
    v.set_items([item(n, t) for n, t in (
        ("a", "회의"), ("b", "견적"), ("c", "점심"), ("d", "마감"),
        ("e", "연락처"), ("f", "휴가"))])
    v.show()
    qapp.processEvents()
    yield v
    v.close()
    v.deleteLater()


def center(v: NoteIconView, index: int) -> QPoint:
    cell = v.cell_rect(index)
    return QPoint(int(cell.center().x()), int(cell.top() + 26))


def test_icons_flow_in_rows(view):
    assert view._columns() == 4
    assert view.cell_rect(0).topLeft().toPoint() == QPoint(10, 10)
    assert view.cell_rect(4).topLeft().toPoint() == QPoint(10, 110)
    assert view.minimumHeight() == 2 * 100 + 20
    assert view.index_at(center(view, 5)) == 5
    assert view.index_at(QPoint(view.width() - 4, 280)) == -1


def test_click_ctrl_and_shift_select_like_files(view):
    QTest.mouseClick(view, Qt.LeftButton, Qt.NoModifier, center(view, 1))
    assert view.selected_ids() == ["b"]
    QTest.mouseClick(view, Qt.LeftButton, Qt.ControlModifier, center(view, 3))
    assert view.selected_ids() == ["b", "d"]
    QTest.mouseClick(view, Qt.LeftButton, Qt.ControlModifier, center(view, 1))
    assert view.selected_ids() == ["d"]
    # Shift 는 마지막으로 누른 것(방금 뺀 b)에서 누른 데까지 잇는다. 탐색기도 그렇다.
    QTest.mouseClick(view, Qt.LeftButton, Qt.ShiftModifier, center(view, 5))
    assert view.selected_ids() == ["b", "c", "d", "e", "f"]
    # 여러 개 가운데 하나를 누르고 떼면 그것만 남는다.
    QTest.mouseClick(view, Qt.LeftButton, Qt.NoModifier, center(view, 4))
    assert view.selected_ids() == ["e"]
    # 빈 곳을 누르면 풀린다.
    QTest.mouseClick(view, Qt.LeftButton, Qt.NoModifier, QPoint(view.width() - 4, 280))
    assert view.selected_ids() == []


def test_dragging_on_empty_space_selects_with_a_rectangle(qapp, view):
    start = QPoint(view.width() - 4, 6)
    QTest.mousePress(view, Qt.LeftButton, Qt.NoModifier, start)
    QTest.mouseMove(view, center(view, 2))
    qapp.processEvents()
    assert view.selected_ids() == ["c", "d"] and view._band.isVisible()
    QTest.mouseMove(view, center(view, 5))
    qapp.processEvents()
    # 둘째 줄의 둘째 칸까지 넓히면 사각형에 걸친 것이 모두 골라진다.
    assert set(view.selected_ids()) == {"b", "c", "d", "f"}
    QTest.mouseRelease(view, Qt.LeftButton, Qt.NoModifier, center(view, 5))
    assert not view._band.isVisible()


def test_double_click_and_enter_open(view):
    opened: list[str] = []
    view.activated.connect(opened.append)
    QTest.mouseClick(view, Qt.LeftButton, Qt.NoModifier, center(view, 2))
    assert opened == []                         # 한 번 눌러서는 열리지 않는다
    QTest.mouseDClick(view, Qt.LeftButton, Qt.NoModifier, center(view, 2))
    assert opened == ["c"]
    view.select(["a", "b"])
    QTest.keyClick(view, Qt.Key_Return)
    assert opened == ["c", "a", "b"]


def test_single_click_opens_when_windows_is_set_that_way(view, monkeypatch):
    monkeypatch.setattr(desktop_icons, "single_click_opens", lambda: True)
    opened: list[str] = []
    view.activated.connect(opened.append)
    QTest.mouseClick(view, Qt.LeftButton, Qt.NoModifier, center(view, 0))
    QTest.mouseClick(view, Qt.LeftButton, Qt.ControlModifier, center(view, 1))
    assert opened == ["a"]                      # Ctrl 로 고를 때는 열지 않는다


def test_keys_delete_select_all_and_move(view):
    deleted: list[list[str]] = []
    view.deleteRequested.connect(deleted.append)
    QTest.keyClick(view, Qt.Key_Delete)
    assert deleted == []                        # 고른 것이 없다
    QTest.keyClick(view, Qt.Key_A, Qt.ControlModifier)
    assert view.selected_ids() == ["a", "b", "c", "d", "e", "f"]
    QTest.keyClick(view, Qt.Key_Escape)
    assert view.selected_ids() == []
    QTest.keyClick(view, Qt.Key_Right)
    assert view.selected_ids() == ["a"]         # 처음에는 첫 아이콘
    QTest.keyClick(view, Qt.Key_Right)
    QTest.keyClick(view, Qt.Key_Down)
    assert view.selected_ids() == ["f"]         # b 의 아래
    QTest.keyClick(view, Qt.Key_Down)           # 더 내려갈 곳이 없다
    assert view.selected_ids() == ["f"]
    QTest.keyClick(view, Qt.Key_Up)
    QTest.keyClick(view, Qt.Key_Left)
    assert view.selected_ids() == ["a"]
    QTest.keyClick(view, Qt.Key_Delete)
    assert deleted == [["a"]]


def test_menu_acts_on_what_is_selected(view, monkeypatch):
    shown: list[list[str]] = []
    pick = {"text": i18n.t("common.btn_delete")}

    def run(menu, _pos):
        shown.append([a.text() for a in menu.actions() if not a.isSeparator()])
        next(a for a in menu.actions() if a.text() == pick["text"]).trigger()

    monkeypatch.setattr(edit_menu, "run_menu", run)
    deleted: list = []
    shows: list = []
    view.deleteRequested.connect(deleted.append)
    view.visibilityRequested.connect(lambda ids, on: shows.append((ids, on)))
    view.select(["a", "b"])

    def menu_at(index: int) -> None:
        pos = center(view, index)
        view.contextMenuEvent(QContextMenuEvent(
            QContextMenuEvent.Mouse, pos, view.mapToGlobal(pos)))

    menu_at(1)                                  # 고른 것 위에서: 둘 다
    assert shown[-1] == [i18n.t("icons.menu_open"), i18n.t("sticky.more_hide"),
                         i18n.t("common.btn_delete")]
    assert deleted == [["a", "b"]]
    pick["text"] = i18n.t("sticky.more_hide")
    menu_at(3)                                  # 고르지 않은 것 위에서: 그것만
    assert view.selected_ids() == ["d"] and shows == [(["d"], False)]
    # 빈 곳에서는 메뉴가 없다.
    view.contextMenuEvent(QContextMenuEvent(
        QContextMenuEvent.Mouse, QPoint(view.width() - 4, 280), QPoint(0, 0)))
    assert len(shown) == 2


def test_hidden_notes_offer_to_show(view, monkeypatch):
    view.set_items([item("a", "회의", hidden=True)])
    shown: list[list[str]] = []
    monkeypatch.setattr(edit_menu, "run_menu", lambda menu, _pos: shown.append(
        [a.text() for a in menu.actions() if not a.isSeparator()]))
    pos = center(view, 0)
    view.contextMenuEvent(QContextMenuEvent(
        QContextMenuEvent.Mouse, pos, view.mapToGlobal(pos)))
    assert shown == [[i18n.t("icons.menu_open"), i18n.t("folderwin.menu_show"),
                      i18n.t("common.btn_delete")]]


def test_selection_survives_a_refresh_but_drops_what_is_gone(view):
    view.select(["a", "c"])
    view.set_items([item("c", "점심"), item("z", "새 메모")])
    assert view.selected_ids() == ["c"] and view.ids() == ["c", "z"]


# ── 창 ──────────────────────────────────────────────────────────────────

@pytest.fixture
def settings(tmp_path, monkeypatch):
    s = AppSettings()
    monkeypatch.setattr(AppSettings, "save", lambda self, path=None: None)
    return s


def test_window_switches_between_icons_and_cards(qapp, settings):
    wired: list = []
    win = FolderWindow("f1", settings, wire_card=wired.append)
    win.set_content("업무", [item("a", "회의"), item("b", "견적", hidden=True)])
    win.show()
    qapp.processEvents()
    assert win.windowTitle() == "업무"
    assert win._count.text() == i18n.t("folderwin.count", count=2)
    assert win._stack.currentWidget() is win._icon_scroll        # 처음은 아이콘
    assert win.icon_view().ids() == ["a", "b"]
    assert set(win.cards()) == {"a", "b"} and len(wired) == 2

    win.set_view(layout=VIEW_GRID)
    assert settings.folder_view_layout == VIEW_GRID
    assert win._stack.currentWidget() is win._card_scroll
    win.set_view(layout=VIEW_LIST, density=VIEW_COMPACT)
    assert settings.folder_view_density == VIEW_COMPACT
    assert win._stack.currentWidget() is win._card_scroll
    win.set_view(layout=VIEW_ICONS)
    assert win._stack.currentWidget() is win._icon_scroll

    # 비면 빈 폴더라고 알린다. 다시 차면 고른 보기로 돌아온다.
    win.set_content("업무", [])
    assert win._stack.currentWidget() is win._empty and win.cards() == {}
    win.set_content("업무 2", [item("c", "점심")])
    assert win._stack.currentWidget() is win._icon_scroll
    assert win.windowTitle() == "업무 2" and len(wired) == 3
    closed: list[str] = []
    win.closed.connect(closed.append)
    win.close()
    assert closed == ["f1"]


def test_view_menu_offers_three_layouts(qapp, settings, monkeypatch):
    win = FolderWindow("f1", settings)
    win.set_content("업무", [item("a", "회의")])
    shown: list[list[str]] = []
    monkeypatch.setattr(edit_menu, "run_menu", lambda menu, _pos: shown.append(
        [a.text() for a in menu.actions() if not a.isSeparator()]))
    win._show_view_menu()
    assert shown[-1] == [i18n.t("main.view_section_layout"),
                         i18n.t("folderwin.view_icons"), i18n.t("main.view_list"),
                         i18n.t("main.view_grid")]
    win.set_view(layout=VIEW_LIST)
    win._show_view_menu()
    # 카드로 볼 때는 밀도도 고른다.
    assert shown[-1][4:] == [i18n.t("main.view_section_density"),
                             i18n.t("main.view_compact"), i18n.t("main.view_standard"),
                             i18n.t("main.view_detailed")]
    win.close()


# ── 메인 창과 함께 ──────────────────────────────────────────────────────

def note(note_id: str, text: str, folder=None) -> dict:
    now = int(time.time() * 1000)
    return {"id": note_id, "content": f"<p>{text}</p>", "color": "#FFE066",
            "version": 1, "width": 280, "height": 260, "created_by": OTHER,
            "updated_by": OTHER, "created_at": now, "updated_at": now,
            "deleted": 0, "folder_id": folder, "private_owner": None}


class FakeClient(ClientBase):
    def __init__(self):
        super().__init__(ME)
        self.calls: list[tuple] = []

    def delete_note(self, note_id):
        self.calls.append(("delete", note_id))

    def set_note_folder(self, note_id, folder_id):
        self.calls.append(("folder", note_id, folder_id))

    def release_lock(self, note_id):
        pass

    def of(self, kind):
        return [c for c in self.calls if c[0] == kind]


FOLDER = {"id": "f1", "name": "업무", "color": "#BAE1FF", "created_by": OTHER,
          "created_at": 1, "updated_at": 1, "private_owner": None, "deleted": 0}


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(StickyNoteWidget, "bring_to_front",
                        lambda self, *, start_editing=False: None)
    monkeypatch.setattr(AppSettings, "save", lambda self, path=None: None)
    client = FakeClient()
    win = MainWindow(client=client,
                     local_state=LocalStateStore(tmp_path / "local_state.json"),
                     role="guest", settings=AppSettings(fold_animation=False))
    client.connectionChanged.emit(True)
    client._dispatch("welcome", {
        "users": [ME, OTHER], "host": OTHER, "locks": {}, "heartbeat_ms": 3000,
        "trash_days": config.TRASH_KEEP_DAYS, "folders": [dict(FOLDER)],
        "notes": [note("a", "회의", "f1"), note("b", "견적", "f1"),
                  note("c", "점심")]})
    qapp.processEvents()
    for nid in "abc":
        win._set_note_visibility(nid, True)
    win._place_folder_icon("f1", quiet=True)        # 폴더는 접혀 있다
    qapp.processEvents()
    yield win, client
    win._force_quit = True
    win._close_folder_windows()
    win._teardown_stickies()
    win._teardown_folder_icons()
    win.close()
    win.deleteLater()
    qapp.processEvents()


def test_double_clicking_the_folder_icon_opens_its_window(qapp, window):
    win, _client = window
    icon = win._folder_icons["f1"]
    QTest.mousePress(icon, Qt.LeftButton, Qt.NoModifier, QPoint(38, 30))
    QTest.mouseRelease(icon, Qt.LeftButton, Qt.NoModifier, QPoint(38, 30))
    assert win._folder_windows == {}                # 한 번은 고르기만 한다
    QTest.mouseDClick(icon, Qt.LeftButton, Qt.NoModifier, QPoint(38, 30))
    fw = win._folder_windows["f1"]
    assert fw.isVisible() and fw.windowTitle() == "업무"
    assert sorted(fw.icon_view().ids()) == ["a", "b"]
    # 폴더는 접힌 그대로다(창을 연다고 메모가 바탕화면에 쏟아지지 않는다).
    assert not win._local.state.folder_icons["f1"].opened
    assert not win._stickies["a"].isVisible()
    # 다시 열면 같은 창이 앞으로 온다.
    win.open_folder_window("f1")
    assert win._folder_windows["f1"] is fw


def test_opening_a_note_from_the_window_takes_out_only_that_note(qapp, window):
    win, _client = window
    win.open_folder_window("f1")
    fw = win._folder_windows["f1"]
    fw.icon_view().activated.emit("a")
    qapp.processEvents()
    assert win._stickies["a"].isVisible() and not win._stickies["b"].isVisible()
    assert not win._local.state.folder_icons["f1"].opened
    # 닫으면 다시 폴더 안으로 들어간다. 숨긴 메모가 되지는 않는다.
    win._set_note_visibility("a", False)
    assert not win._stickies["a"].isVisible()
    assert win._local.state.notes["a"].hidden is False
    win._set_folder_opened("f1", True, animate=False)
    assert win._stickies["a"].isVisible() and win._stickies["b"].isVisible()


def test_window_follows_changes_and_closes_with_the_folder(qapp, window):
    win, client = window
    win.open_folder_window("f1")
    fw = win._folder_windows["f1"]
    client._dispatch("note_created", {"note": note("d", "새 메모", "f1")})
    assert "d" in fw.icon_view().ids()
    client._dispatch("note_deleted", {"id": "a"})
    assert "a" not in fw.icon_view().ids()
    client._dispatch("folder_updated", {"folder": dict(FOLDER, name="총무")})
    assert fw.windowTitle() == "총무"
    client._dispatch("folder_deleted", {"id": "f1"})
    qapp.processEvents()
    assert "f1" not in win._folder_windows


def test_delete_from_the_window_goes_to_the_trash(qapp, window):
    win, client = window
    win.open_folder_window("f1")
    fw = win._folder_windows["f1"]
    fw.icon_view().select(["a", "b"])
    QTest.keyClick(fw.icon_view(), Qt.Key_Delete)
    assert sorted(client.of("delete")) == [("delete", "a"), ("delete", "b")]


def test_cards_in_the_window_work_like_the_main_list(qapp, window):
    win, client = window
    win.open_folder_window("f1")
    fw = win._folder_windows["f1"]
    fw.set_view(layout=VIEW_LIST)
    card = fw.cards()["b"]
    card.clicked.emit("b")
    qapp.processEvents()
    assert win._stickies["b"].isVisible() and not win._stickies["a"].isVisible()
    card.deleteRequested.emit("b")
    assert client.of("delete") == [("delete", "b")]


def test_dropping_a_note_icon_on_the_window_moves_it_in(qapp, window):
    win, client = window
    win.open_folder_window("f1")
    fw = win._folder_windows["f1"]
    fw.move(300, 300)
    qapp.processEvents()
    c = win._stickies["c"]
    c.fold(style=nf.FOLD_ICON)
    inside = fw.frameGeometry().center()
    assert win._on_icon_dragged("c", inside, True) is True
    assert client.of("folder") == [("folder", "c", "f1")]
    assert win._on_icon_dragged("c", QPoint(5, 700), True) is False
