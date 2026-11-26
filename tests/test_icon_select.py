"""바탕화면의 메모 아이콘과 폴더 아이콘을 파일처럼 고르고 다루기."""
from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect, Qt  # noqa: E402
from PySide6.QtGui import QContextMenuEvent, QImage, QPainter  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app import config, desktop_icons, i18n  # noqa: E402
from app.desktop_icons import DesktopView  # noqa: E402
from app.local_state import LocalStateStore  # noqa: E402
from app.network.client_base import ClientBase  # noqa: E402
from app.settings import AppSettings  # noqa: E402
from app.ui import edit_menu, native_plate  # noqa: E402
from app.ui import note_fold as nf  # noqa: E402
from app import desktop_select  # noqa: E402
from app.ui.icon_select import (  # noqa: E402
    ON_BIN, ON_CONTAINER, DesktopKeys, DesktopMarquee, IconController,
    IconSelection, folder_key, note_key,
)
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.sticky_note import StickyNoteWidget  # noqa: E402

ME, OTHER = "지우", "민수"


def _wait_for(done, ms: int) -> None:
    """``done()`` 가 참이 될 때까지(길어야 ``ms``) 이벤트를 돌린다. 정해 둔 시간만 기다리면
    바쁜 PC 에서 타이머가 늦게 울려 시험이 가끔 틀렸다."""
    deadline = time.monotonic() + ms / 1000
    while not done() and time.monotonic() < deadline:
        QTest.qWait(20)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    i18n.set_language("ko")
    yield app


@pytest.fixture(autouse=True)
def _double_click(monkeypatch):
    # 이 PC 의 Windows 설정과 무관하게, 두 번 눌러 여는 것으로 본다.
    monkeypatch.setattr(desktop_icons, "single_click_opens", lambda: False)


# ── 고른 것 ─────────────────────────────────────────────────────────────

def test_selection_keeps_the_order_and_tells_changes():
    sel = IconSelection()
    seen: list[list[str]] = []
    sel.changed.connect(lambda: seen.append(sel.keys()))
    sel.select_only("note:a")
    sel.add("folder:f")
    sel.add("folder:f")                    # 이미 있다. 알리지 않는다
    sel.toggle("note:b")
    sel.toggle("note:a")
    assert seen == [["note:a"], ["note:a", "folder:f"],
                    ["note:a", "folder:f", "note:b"], ["folder:f", "note:b"]]
    assert sel.of("note") == ["b"] and sel.of("folder") == ["f"]
    sel.keep(["note:b"])
    assert sel.keys() == ["note:b"] and "note:b" in sel and len(sel) == 1


# ── 규칙(가짜 창과 가짜 메인 창으로) ────────────────────────────────────

class FakeIcon:
    def __init__(self, x: int, y: int):
        self._pos = QPoint(x, y)
        self.selected = False
        self.marquee = None
        self.opacity = 1.0
        self.calls: list = []

    def setWindowOpacity(self, value: float) -> None:  # noqa: N802
        self.opacity = value

    def pos(self) -> QPoint:
        return QPoint(self._pos)

    def geometry(self) -> QRect:
        return QRect(self._pos, nf.ICON_CELL_DEFAULT)

    def move(self, pos: QPoint) -> None:
        self._pos = QPoint(pos)

    def set_icon_selected(self, on: bool) -> None:
        self.selected = on

    def set_marquee(self, rect) -> None:
        self.marquee = rect

    def icon_hit(self, rect: QRect) -> bool:
        return rect.intersects(QRect(self._pos, nf.ICON_CELL_DEFAULT))

    def place_icon(self, pos: QPoint) -> None:
        self.calls.append(("place", pos.x(), pos.y()))
        self._pos = QPoint(pos)

    def snap_icon(self) -> None:
        self.calls.append(("snap", self._pos.x(), self._pos.y()))


class FakeHost:
    def __init__(self):
        self.icons = {note_key("a"): FakeIcon(0, 5), note_key("b"): FakeIcon(76, 5),
                      note_key("c"): FakeIcon(152, 5),
                      folder_key("f"): FakeIcon(0, 105)}
        self.calls: list = []

    def icon_widgets(self) -> dict:
        return dict(self.icons)

    all_icon_widgets = icon_widgets

    def delete_notes(self, ids):
        self.calls.append(("delete_notes", list(ids)))

    def delete_folder(self, fid):
        self.calls.append(("delete_folder", fid))

    def open_note(self, nid):
        self.calls.append(("open_note", nid))

    def open_folder_window(self, fid):
        self.calls.append(("open_folder", fid))

    def hide_notes(self, ids):
        self.calls.append(("hide", list(ids)))

    def move_notes_to_folder(self, ids, fid):
        self.calls.append(("move", list(ids), fid))

    def realign_icons(self):
        self.calls.append(("realign",))

    def rename_folder_icon(self, fid):
        self.calls.append(("rename", fid))


class NoWatch:
    def __init__(self):
        self.started: list[int] = []
        self._window = 0

    def window(self) -> int:
        return self._window

    def start(self, window: int) -> bool:
        self.started.append(window)
        self._window = window
        return True

    def stop(self) -> None:
        self._window = 0


class FakeGrid:
    def __init__(self, view=None):
        self._view = view
        self.lifted: list[list[int]] = []
        self.refreshes = 0

    def view(self):
        return self._view

    def set_lifted(self, indices) -> None:
        self.lifted.append(list(indices))

    def refresh(self) -> None:
        self.refreshes += 1


class FakeDesk:
    """탐색기 쪽. 고른 진짜 아이콘의 번호를 들고 있고, 시킨 일을 적어 둔다."""

    def __init__(self):
        self.real: list[int] = []
        self.calls: list = []
        self.focused = False
        self.down: set[int] = set()
        # 진짜 아이콘의 옮기기 전 자리와, 옮긴 뒤에 탐색기가 놓은 자리.
        self.spots: dict[int, tuple[int, int]] = {}
        self.now: dict[int, tuple[int, int]] = {}
        # Windows 휴지통의 번호, 폴더처럼 무엇을 담는 아이콘, 놓여 있는 아이콘의 수.
        self.bin = -1
        self.containers: set[int] = set()
        self.count: int | None = None
        self.auto = False
        self.pictures: list = []

    def bin_index(self) -> int:
        return self.bin

    def is_container(self, index: int) -> bool:
        self.calls.append(("is_container", index))
        return index in self.containers

    def item_count(self):
        return self.count

    def auto_arrange(self) -> bool:
        return self.auto

    def delete_selected(self) -> bool:
        self.calls.append(("delete",))
        return True

    def faces(self, indices):
        return [f for f in self.pictures if f[0] in indices], 48

    def window(self) -> int:
        return 1234

    def focus(self) -> bool:
        self.calls.append(("focus",))
        return True

    def is_focused(self) -> bool:
        return self.focused

    def key_down(self, vk: int) -> bool:
        return vk in self.down

    def selected(self) -> list[int]:
        return list(self.real)

    def deselect_all(self) -> bool:
        self.calls.append(("deselect",))
        self.real = []
        return True

    def select(self, indices, replace) -> bool:
        self.calls.append(("select", list(indices), replace))
        self.real = list(indices) if replace else sorted(set(self.real) | set(indices))
        return True

    def move_by(self, indices, dx, dy):
        self.calls.append(("move_by", list(indices), dx, dy))
        return {index: self.spots[index] for index in indices if index in self.spots}

    def positions(self, indices) -> dict:
        return {index: self.now.get(index, self.spots[index])
                for index in indices if index in self.spots}

    def place(self, spots) -> bool:
        self.calls.append(("place", dict(spots)))
        return True

    def spacing(self):
        return 76, 100

    def did(self, kind: str) -> list:
        return [c for c in self.calls if c[0] == kind]


@pytest.fixture
def ctl(qapp):
    host = FakeHost()
    controller = IconController(
        host, FakeGrid(), marquee=DesktopMarquee(FakeGrid(), watch=NoWatch()),
        desk=FakeDesk())
    yield controller, host
    controller.stop()


def desk_of(controller) -> FakeDesk:
    return controller._desk


A, B, C, F = note_key("a"), note_key("b"), note_key("c"), folder_key("f")


def test_clicking_selects_like_a_file(ctl):
    c, host = ctl
    c.pressed(A, Qt.NoModifier)
    assert c.selection.keys() == [A] and host.icons[A].selected
    c.pressed(B, Qt.NoModifier)                 # 다른 것을 누르면 그것만
    assert c.selection.keys() == [B] and not host.icons[A].selected
    c.pressed(A, Qt.ControlModifier)            # Ctrl: 더하고
    assert c.selection.keys() == [B, A]
    c.pressed(B, Qt.ControlModifier)            # Ctrl 로 다시 누르면 빠진다
    assert c.selection.keys() == [A]


def test_shift_click_selects_everything_between_like_explorer(ctl):
    """a b c 가 한 줄에, f 가 a 아래에 있다. 기준과 누른 것이 만드는 네모 안의
    것이 모두 골라진다."""
    c, _host = ctl
    c.pressed(A, Qt.NoModifier)                 # 기준
    c.pressed(C, Qt.ShiftModifier)              # a 에서 c 까지
    assert c.selection.keys() == [A, B, C]
    c.pressed(F, Qt.ShiftModifier)              # 기준은 그대로 a: a 와 그 아래의 f
    assert c.selection.keys() == [A, F]
    c.pressed(C, Qt.ShiftModifier | Qt.ControlModifier)    # Ctrl 을 함께: 더한다
    assert c.selection.keys() == [A, F, B, C]


def test_shift_click_without_an_anchor_is_a_plain_click(ctl):
    c, _host = ctl
    c.pressed(B, Qt.ShiftModifier)
    assert c.selection.keys() == [B]
    c.pressed(C, Qt.ShiftModifier)              # 이제 b 가 기준이다
    assert c.selection.keys() == [B, C]


# ── 진짜 아이콘과 함께 ──────────────────────────────────────────────────

def test_a_plain_click_releases_the_real_icons_and_ctrl_keeps_them(ctl):
    c, _host = ctl
    desk = desk_of(c)
    desk.real = [3, 7]
    c.pressed(A, Qt.ControlModifier)            # 진짜 아이콘을 골라 둔 채 더한다
    assert desk.did("deselect") == [] and c.real_selected() == [3, 7]
    assert c.selection.keys() == [A]
    assert desk.did("focus")                    # 키보드는 바탕화면이 쥔다
    c.pressed(B, Qt.NoModifier)                 # 그냥 누르면 그것만 남는다
    assert desk.did("deselect") == [("deselect",)] and c.real_selected() == []
    assert c.selection.keys() == [B]


def test_the_pressed_icon_takes_the_keyboard_when_the_desktop_cannot(ctl):
    c, host = ctl
    desk = desk_of(c)
    desk.focus = lambda: False                  # 탐색기가 꺼져 있다
    woke: list[int] = []
    host.icons[A].activateWindow = lambda: woke.append(1)
    c.pressed(A, Qt.NoModifier)
    assert woke == [1]


def test_pressing_one_of_a_mixed_group_keeps_everything_until_released(ctl):
    c, _host = ctl
    desk = desk_of(c)
    desk.real = [3]
    c.selection.set([A, B])
    c.pressed(A, Qt.NoModifier)                 # 끌 수도 있다. 아직 그대로
    assert desk.did("deselect") == [] and c.selection.keys() == [A, B]
    c.clicked(A)                                # 끌지 않고 뗐다
    assert c.selection.keys() == [A] and desk.did("deselect") == [("deselect",)]


def test_dragging_a_memo_icon_takes_the_selected_real_icons_along(ctl):
    c, host = ctl
    desk = desk_of(c)
    desk.real = [3, 7]
    c.selection.set([A, B])
    c.pressed(A, Qt.NoModifier)
    c.drag_began(A)
    assert c._grid.lifted[-1] == [3, 7]         # 그 아이콘들이 비울 칸은 빈 칸으로 본다
    c.drag_moved(A, QPoint(152, 100))
    c.drag_ended(A, True, QPoint(152, 100))
    assert desk.did("move_by") == [("move_by", [3, 7], 152, 100)]
    assert c._grid.lifted[-1] == []
    assert host.icons[B].calls == [("snap", 228, 105)]
    _wait_for(lambda: ("realign",) in host.calls, 3000)      # 바쁜 PC 에서도 타이머를 기다린다
    assert ("realign",) in host.calls


def test_real_icons_stay_when_the_drag_did_not_end_in_a_move(ctl):
    c, _host = ctl
    desk = desk_of(c)
    desk.real = [3]
    c.selection.set([A, B])
    c.pressed(A, Qt.NoModifier)
    c.drag_began(A)
    c.drag_ended(A, False)                      # 벽에 붙었다
    c.pressed(A, Qt.NoModifier)
    c.drag_began(A)
    c.dropped_on_folder(A, "f")
    c.drag_ended(A, True, QPoint(10, 10))       # 메모 폴더에 넣었다
    c.pressed(A, Qt.NoModifier)
    c.drag_began(A)
    c.drag_ended(A, True, QPoint(0, 0))         # 제자리에 놓았다
    assert desk.did("move_by") == []


def test_a_real_move_that_went_wrong_is_put_back(ctl):
    """탐색기가 아이콘을 뜻한 곳과 동떨어진 데에 놓았으면 끌기 전으로 되돌린다."""
    c, host = ctl
    desk = desk_of(c)
    desk.real = [3, 7]
    desk.spots = {3: (314, 2), 7: (314, 102)}
    c.selection.set([A, B])
    c.pressed(A, Qt.NoModifier)
    c.drag_began(A)
    c.drag_ended(A, True, QPoint(152, 0))
    desk.now = {3: (466, 2), 7: (1400, 700)}    # 하나가 엉뚱한 데로 갔다
    _wait_for(lambda: desk.did("place"), 3000)      # 바쁜 PC 에서도 타이머를 기다린다
    assert desk.did("place") == [("place", {3: (314, 2), 7: (314, 102)})]
    assert ("realign",) in host.calls


def test_a_real_move_that_landed_in_a_neighbour_cell_is_left_alone(ctl):
    c, _host = ctl
    desk = desk_of(c)
    desk.real = [3]
    desk.spots = {3: (314, 2)}
    c.selection.set([A, B])
    c.pressed(A, Qt.NoModifier)
    c.drag_began(A)
    c.drag_ended(A, True, QPoint(152, 0))
    desk.now = {3: (466, 102)}                  # 놓일 칸이 차 있어 옆 칸으로 갔다
    QTest.qWait(500)
    assert desk.did("place") == []


def test_a_move_under_auto_arrange_is_not_checked_against_the_drop(ctl):
    """'자동 정렬'이면 놓일 자리는 탐색기가 정한다. 뜻한 자리와 달라도 되돌리지 않는다."""
    c, host = ctl
    desk = desk_of(c)
    desk.real, desk.auto = [3], True
    desk.spots = {3: (314, 2)}
    c.selection.set([A, B])
    c.pressed(A, Qt.NoModifier)
    c.drag_began(A)
    c.drag_ended(A, True, QPoint(152, 0))
    desk.now = {3: (14, 702)}                   # 줄의 끝으로 갔다
    _wait_for(lambda: ("realign",) in host.calls, 3000)      # 바쁜 PC 에서도 타이머를 기다린다
    assert desk.did("move_by") == [("move_by", [3], 152, 0)]
    assert desk.did("place") == [] and ("realign",) in host.calls


def real_view() -> DesktopView:
    # 진짜 아이콘 셋: 번호 3 은 칸 (300, 5), 번호 7 은 그 아래 칸, 번호 9 는
    # 칸 (452, 5) 에 있는 Windows 휴지통.
    return DesktopView(icon=48, spacing=(76, 100),
                       items=((314, 2), (314, 102), (466, 2)),
                       snap=True, window=1234, dpi=96, offset=(-14, 3),
                       indices=(3, 7, 9), bin=9)


ON_THE_BIN = QPoint(490, 40)
ON_ITEM_7 = QPoint(338, 140)


class FakeGhost:
    def __init__(self):
        self.calls: list = []

    def begin(self, faces, icon, delta=None) -> None:
        self.calls.append(("begin", [(QRect(cell), name) for cell, _i, name in faces],
                           icon, QPoint(delta or QPoint())))

    def follow(self, delta) -> None:
        self.calls.append(("follow", delta.x(), delta.y()))

    def set_fade(self, on) -> None:
        self.calls.append(("fade", on))

    def end(self) -> None:
        self.calls.append(("end",))


@pytest.fixture
def mixed(qapp):
    host, grid, probe = FakeHost(), FakeGrid(real_view()), Probe()
    desk = FakeDesk()
    desk.bin = 9
    controller = IconController(
        host, grid, marquee=DesktopMarquee(grid, probe=probe, watch=NoWatch()),
        desk=desk, ghost=FakeGhost())
    yield controller, host, probe
    controller.stop()


def test_dropping_a_mixed_group_on_the_recycle_bin_trashes_both_kinds(mixed):
    """메모 아이콘을 잡고 Windows 휴지통에 놓았다. 메모는 메모 휴지통으로 가고,
    함께 고른 파일은 탐색기가 지운다. 끌던 아이콘들은 제자리로 돌아간다."""
    c, host, _probe = mixed
    desk = desk_of(c)
    desk.real = [3]
    c.selection.set([A, B, F])
    c.pressed(A, Qt.NoModifier)
    c.drag_began(A)
    c.drag_moved(A, QPoint(400, 0), QPoint(120, 40))
    assert host.icons[A].opacity == 1.0
    c.drag_moved(A, QPoint(452, 0), ON_THE_BIN)         # 휴지통 위에서는 흐려진다
    assert host.icons[A].opacity < 1.0 and host.icons[B].opacity < 1.0
    assert host.icons[C].opacity == 1.0
    assert c.dropped_on_real(A, ON_THE_BIN) is True
    c.drag_ended(A, True, QPoint(0, 0))
    assert host.icons[A].opacity == 1.0 and host.icons[B].opacity == 1.0
    assert host.icons[B].calls == [("place", 76, 5)]
    assert ("delete_notes", ["a", "b"]) in host.calls
    assert ("delete_folder", "f") in host.calls
    assert desk.did("delete") == [("delete",)] and desk.did("move_by") == []
    assert desk.did("is_container") == []               # 휴지통은 묻지 않고 안다


def test_dropping_memo_icons_alone_on_the_recycle_bin_asks_nothing_of_explorer(mixed):
    c, host, _probe = mixed
    desk = desk_of(c)
    c.selection.set([A])
    c.pressed(A, Qt.NoModifier)
    c.drag_began(A)
    assert c.dropped_on_real(A, ON_THE_BIN) is True
    c.drag_ended(A, True, QPoint(0, 0))
    assert host.calls == [("delete_notes", ["a"])] and desk.did("delete") == []


def test_dropping_on_a_real_folder_sends_everything_home(mixed):
    """메모 아이콘은 바탕화면에만 있다. 진짜 폴더에 놓으면 아무것도 옮기지 않는다."""
    c, host, _probe = mixed
    desk = desk_of(c)
    desk.real, desk.containers = [3], {7}
    c.selection.set([A, B])
    c.pressed(A, Qt.NoModifier)
    c.drag_began(A)
    c.drag_moved(A, QPoint(300, 100), ON_ITEM_7)
    assert host.icons[A].opacity == 1.0                 # 휴지통이 아니면 흐려지지 않는다
    assert c.dropped_on_real(A, ON_ITEM_7) is True
    c.drag_ended(A, True, QPoint(0, 0))
    assert host.icons[B].calls == [("place", 76, 5)]
    assert host.calls == [] and desk.did("move_by") == [] and desk.did("delete") == []


def test_dropping_on_a_plain_file_or_on_one_of_the_group_is_an_ordinary_move(mixed):
    c, _host, _probe = mixed
    desk = desk_of(c)
    desk.real = [3]
    c.selection.set([A])
    c.pressed(A, Qt.NoModifier)
    c.drag_began(A)
    assert c.dropped_on_real(A, ON_ITEM_7) is False     # 무엇을 담는 것이 아니다
    assert c.dropped_on_real(A, QPoint(338, 40)) is False   # 함께 끄는 3 번 위다
    assert c.dropped_on_real(A, QPoint(900, 600)) is False  # 빈 곳이다
    c.drag_ended(A, True, QPoint(76, 0))
    assert desk.did("move_by") == [("move_by", [3], 76, 0)]


def test_the_selected_real_icons_follow_as_faint_pictures(mixed, qapp):
    """메모 아이콘을 잡고 끄는 동안 함께 고른 진짜 아이콘의 그림이 따라온다."""
    c, _host, _probe = mixed
    desk, ghost = desk_of(c), c._ghost
    picture = QImage(48, 48, QImage.Format_ARGB32)
    desk.real = [3, 7]
    desk.pictures = [(3, picture, "견적서"), (7, picture, "사진")]
    c.selection.set([A])
    c.pressed(A, Qt.NoModifier)
    c.drag_began(A)
    c.drag_moved(A, QPoint(10, 0), QPoint(50, 50))
    qapp.processEvents()                                # 그림은 끌기를 시작한 뒤에 뜬다
    begun = [call for call in ghost.calls if call[0] == "begin"]
    assert begun == [("begin", [(QRect(300, 5, 76, 100), "견적서"),
                                (QRect(300, 105, 76, 100), "사진")],
                      48.0, QPoint(10, 0))]
    c.drag_moved(A, QPoint(76, 100), QPoint(120, 150))
    assert ("follow", 76, 100) in ghost.calls
    c.drag_ended(A, True, QPoint(76, 100))
    assert ghost.calls[-1] == ("end",)


def test_the_desktop_is_read_again_for_a_file_that_was_just_made(mixed, qapp):
    """방금 만든 파일은 읽어 둔 자리에 없다. 그림을 띄우기 전에 다시 읽는다."""
    c, _host, _probe = mixed
    desk, ghost = desk_of(c), c._ghost
    picture = QImage(48, 48, QImage.Format_ARGB32)
    desk.real = [3, 11]                                 # 11 번은 아직 읽지 못했다
    desk.pictures = [(3, picture, "견적서"), (11, picture, "새 파일")]
    c.selection.set([A])
    c.pressed(A, Qt.NoModifier)
    before = c._grid.refreshes
    c.drag_began(A)
    qapp.processEvents()
    assert c._grid.refreshes == before + 1
    begun = [call for call in ghost.calls if call[0] == "begin"]
    assert len(begun) == 1 and [name for _cell, name in begun[0][1]] == ["견적서"]
    c.drag_ended(A, True, QPoint(0, 0))


def test_no_picture_follows_when_the_drag_ended_before_it_was_read(mixed, qapp):
    c, _host, _probe = mixed
    desk, ghost = desk_of(c), c._ghost
    desk.real = [3]
    desk.pictures = [(3, QImage(48, 48, QImage.Format_ARGB32), "견적서")]
    c.selection.set([A])
    c.pressed(A, Qt.NoModifier)
    c.drag_began(A)
    c.drag_ended(A, True, QPoint(76, 0))
    qapp.processEvents()
    assert [call for call in ghost.calls if call[0] == "begin"] == []


def test_dragging_files_with_memos_onto_the_recycle_bin_trashes_the_memos_too(mixed):
    """진짜 아이콘을 잡고 Windows 휴지통에 놓았다. 파일은 탐색기가 지운다. 지운 것을
    본 뒤에 따라간 메모를 메모 휴지통으로 보낸다."""
    c, host, probe = mixed
    desk = desk_of(c)
    desk.count = 3
    c.selection.set([A, B])
    c._real = [3]
    press_real(c, probe)
    probe.pos = (490, 40)                               # 휴지통 위로 끌었다
    c.marquee._tick()
    release_real(c, probe)
    _wait_for(lambda: host.icons[A].calls, 3000)      # 바쁜 PC 에서도 타이머를 기다린다
    assert host.icons[A].calls == [("place", 0, 5)]     # 우선 제자리로
    assert not [call for call in host.calls if call[0] == "delete_notes"]
    desk.count = 2                                      # 탐색기가 파일을 지웠다
    QTest.qWait(700)
    assert ("delete_notes", ["a", "b"]) in host.calls


def test_memos_stay_when_explorer_did_not_delete_the_files(mixed):
    c, host, probe = mixed
    desk = desk_of(c)
    desk.count = 3
    c.selection.set([A])
    c._real = [3]
    press_real(c, probe)
    probe.pos = (490, 40)
    c.marquee._tick()
    release_real(c, probe)
    QTest.qWait(500)
    c.selection.clear()                                 # 그 사이에 다른 곳을 눌렀다
    desk.count = 2
    QTest.qWait(700)
    assert not [call for call in host.calls if call[0] == "delete_notes"]


def press_real(c, probe, at=(330, 30), *, adding=False, shift=False) -> None:
    probe.pos, probe.primary, probe.adding, probe.shift = at, True, adding, shift
    c.marquee._on_capture()


def release_real(c, probe) -> None:
    probe.primary = False
    c.marquee._tick()


def test_pressing_an_unselected_real_icon_releases_ours(mixed):
    c, _host, probe = mixed
    c.selection.set([A, B])
    press_real(c, probe)                        # 번호 3. 골라 둔 것이 아니다
    assert c.marquee.item() == 3 and c.selection.keys() == []
    release_real(c, probe)


def test_ctrl_click_on_a_real_icon_keeps_ours(mixed):
    c, _host, probe = mixed
    c.selection.set([A])
    press_real(c, probe, adding=True)
    release_real(c, probe)
    assert c.selection.keys() == [A]
    desk_of(c).real = [3]
    QTest.qWait(200)                            # 뗀 뒤에 탐색기가 고른 것을 읽는다
    assert c.real_selected() == [3]


def test_dragging_a_selected_real_icon_takes_our_icons_along(mixed):
    c, host, probe = mixed
    c.selection.set([A, B])
    c._real = [3, 7]
    press_real(c, probe)
    assert c.selection.keys() == [A, B]         # 함께 고른 것이라 그대로다
    probe.pos = (482, 130)                      # 오른쪽으로 두 칸, 아래로 한 칸
    c.marquee._tick()
    assert host.icons[A].pos() == QPoint(152, 105)
    assert host.icons[B].pos() == QPoint(228, 105)
    assert host.icons[C].pos() == QPoint(152, 5)        # 고르지 않은 것은 그대로
    # 탐색기가 진짜 아이콘을 옮겨 놓았다.
    c._grid._view = DesktopView(
        icon=48, spacing=(76, 100), items=((466, 102), (466, 202), (618, 2)),
        snap=True, window=1234, dpi=96, offset=(-14, 3), indices=(3, 7, 9), bin=9)
    release_real(c, probe)
    _wait_for(lambda: ("realign",) in host.calls, 3000)      # 바쁜 PC 에서도 타이머를 기다린다
    assert ("realign",) in host.calls           # 따라간 자리의 칸에 맞춰 놓는다
    assert host.icons[A].calls == [] and c.selection.keys() == [A, B]


def test_our_icons_go_back_when_the_real_icons_did_not_move(mixed):
    c, host, probe = mixed
    c.selection.set([A])
    c._real = [3]
    press_real(c, probe)
    probe.pos = (900, 600)
    c.marquee._tick()
    assert host.icons[A].pos() != QPoint(0, 5)
    release_real(c, probe)                      # 다른 창에 놓았거나 그만뒀다
    _wait_for(lambda: host.icons[A].calls, 3000)      # 바쁜 PC 에서도 타이머를 기다린다
    assert host.icons[A].calls == [("place", 0, 5)]
    assert ("realign",) not in host.calls


def test_clicking_a_real_icon_of_a_mixed_group_leaves_only_it(mixed):
    c, _host, probe = mixed
    c.selection.set([A, B])
    c._real = [3, 7]
    press_real(c, probe)
    release_real(c, probe)                      # 끌지 않고 뗐다
    assert c.selection.keys() == []


def test_shift_click_on_a_real_icon_takes_our_icons_in_between(mixed):
    c, _host, probe = mixed
    c.pressed(A, Qt.NoModifier)                 # 기준: 맨 왼쪽의 a
    press_real(c, probe, adding=True, shift=True)       # 같은 줄의 진짜 아이콘
    assert c.selection.keys() == [A, B, C]
    release_real(c, probe)


def test_shift_click_on_ours_also_selects_the_real_icons_in_between(mixed):
    c, _host, probe = mixed
    desk = desk_of(c)
    press_real(c, probe)                        # 기준: 진짜 아이콘(번호 3)
    release_real(c, probe)
    c.pressed(A, Qt.ShiftModifier)
    assert c.selection.keys() == [A, B, C]
    assert desk.did("select") == [("select", [3], True)]


# ── 키보드는 바탕화면이 쥐고 있다 ───────────────────────────────────────

def test_keys_pressed_on_the_desktop_reach_the_selected_icons(ctl):
    c, host = ctl
    desk = desk_of(c)
    c.selection.set([A, F])
    c._on_desktop_key("enter")
    assert host.calls == [("open_note", "a"), ("open_folder", "f")]
    host.calls.clear()
    c._on_desktop_key("delete")
    assert host.calls == [("delete_notes", ["a"]), ("delete_folder", "f")]
    c._on_desktop_key("select_all")
    assert set(c.selection.keys()) == {A, B, C, F}
    desk.real = [3]
    c._on_desktop_key("escape")                 # 양쪽 모두 풀린다
    assert c.selection.keys() == [] and desk.did("deselect") == [("deselect",)]


def test_arrow_keys_on_the_desktop_release_ours(ctl):
    c, _host = ctl
    c.selection.set([A, B])
    c._on_desktop_key("move")
    assert c.selection.keys() == []


def test_nothing_happens_on_keys_when_nothing_of_ours_is_selected(ctl):
    c, host = ctl
    for name in ("delete", "enter", "escape", "move", "rename"):
        c._on_desktop_key(name)
    assert host.calls == [] and desk_of(c).calls == []


def test_f2_renames_a_lone_memo_folder(ctl):
    c, host = ctl
    desk = desk_of(c)
    c.selection.set([F])
    desk.real = [3]
    c._on_desktop_key("rename")                 # 진짜 아이콘도 골라져 있다. 탐색기의 일이다
    assert host.calls == []
    desk.real = []
    c._on_desktop_key("rename")
    assert host.calls == [("rename", "f")]
    host.calls.clear()
    c.selection.set([A])
    c._on_desktop_key("rename")                 # 메모 아이콘에는 이름 칸이 없다
    assert host.calls == []


def test_desktop_keys_tell_only_new_presses_while_the_desktop_has_the_keyboard(qapp):
    desk = FakeDesk()
    keys = DesktopKeys(desk)
    seen: list[str] = []
    keys.pressed.connect(seen.append)
    desk.down = {desktop_select.VK_DELETE}
    keys._tick()                                # 키보드가 다른 창에 있다
    assert seen == []
    desk.focused = True
    keys._tick()                                # 넘어오기 전부터 눌려 있던 것은 세지 않는다
    keys._tick()
    assert seen == [] and keys.is_focused()
    desk.down = set()
    keys._tick()
    desk.down = {desktop_select.VK_DELETE}
    keys._tick()
    keys._tick()                                # 누르고 있는 동안 한 번만
    assert seen == ["delete"]
    desk.down = {desktop_select.VK_A}
    keys._tick()                                # Ctrl 없는 a 는 글자다
    desk.down = {desktop_select.VK_A, desktop_select.VK_CONTROL}
    keys._tick()
    assert seen == ["delete", "select_all"]
    desk.down = {desktop_select.VK_RETURN, desktop_select.VK_MENU}
    keys._tick()                                # Alt+Enter 는 속성 창이다
    desk.down = {0x27}
    keys._tick()
    assert seen == ["delete", "select_all", "move"]
    desk.focused = False
    desk.down = {desktop_select.VK_RETURN}
    keys._tick()
    keys._tick()
    assert seen == ["delete", "select_all", "move"]
    keys.stop()


def test_desktop_calls_do_nothing_without_a_desktop(qapp):
    assert desktop_select.selected(0) is None
    assert desktop_select.is_focused(0) is False
    assert desktop_select.focus(0) is False
    assert desktop_select.move_by([], 10, 10) is None
    assert desktop_select.move_by([1], 0, 0) is None


def test_icon_windows_refuse_activation_by_mouse(qapp):
    import ctypes
    from ctypes import wintypes
    msg = wintypes.MSG()
    msg.message = 0x0021
    assert nf.is_mouse_activate(ctypes.addressof(msg)) is True
    msg.message = 0x0201
    assert nf.is_mouse_activate(ctypes.addressof(msg)) is False


def test_pressing_one_of_several_keeps_them_until_released(ctl):
    c, _host = ctl
    c.selection.set([A, B, C])
    c.pressed(B, Qt.NoModifier)                 # 끌 수도 있으니 아직 그대로
    assert c.selection.keys() == [A, B, C]
    c.clicked(B)                                # 끌지 않고 뗐다: 그것만
    assert c.selection.keys() == [B]


def test_selected_icons_move_together_and_snap(ctl):
    c, host = ctl
    c.selection.set([A, B, F])
    c.pressed(A, Qt.NoModifier)
    c.drag_began(A)
    c.drag_moved(A, QPoint(200, 100))
    assert host.icons[B].pos() == QPoint(276, 105)
    assert host.icons[F].pos() == QPoint(200, 205)
    assert host.icons[C].pos() == QPoint(152, 5)        # 고르지 않은 것은 그대로
    c.drag_ended(A, True)
    assert host.icons[B].calls == [("snap", 276, 105)]
    assert host.icons[F].calls == [("snap", 200, 205)]
    assert c.selection.keys() == [A, B, F]              # 끌고 나서도 골라져 있다


def test_dragging_an_unselected_icon_moves_only_it(ctl):
    c, host = ctl
    c.selection.set([B, C])
    c.pressed(A, Qt.NoModifier)
    c.drag_began(A)
    c.drag_moved(A, QPoint(50, 50))
    c.drag_ended(A, True)
    assert host.icons[B].calls == [] and host.icons[C].calls == []


def test_dropping_on_a_folder_takes_the_others_in_and_puts_icons_back(ctl):
    c, host = ctl
    c.selection.set([A, B, F])
    c.drag_began(A)
    c.drag_moved(A, QPoint(30, 90))
    c.dropped_on_folder(A, "f")
    c.drag_ended(A, True)
    assert ("move", ["b"], "f") in host.calls          # 끌던 것은 메인 창이 옮긴다
    assert host.icons[B].calls == [("place", 76, 5)]
    assert host.icons[F].calls == [("place", 0, 105)]


def test_docking_the_dragged_one_sends_the_others_home(ctl):
    c, host = ctl
    c.selection.set([A, B])
    c.drag_began(A)
    c.drag_moved(A, QPoint(-300, 0))
    c.drag_ended(A, False)
    assert host.icons[B].calls == [("place", 76, 5)]


def test_keys_act_on_everything_selected(ctl):
    c, host = ctl
    c.selection.set([A, B, F])
    assert c.key(A, Qt.Key_Return, Qt.NoModifier) is True
    assert [x for x in host.calls if x[0].startswith("open")] == [
        ("open_note", "a"), ("open_note", "b"), ("open_folder", "f")]
    host.calls.clear()
    assert c.key(A, Qt.Key_Delete, Qt.NoModifier) is True
    assert host.calls == [("delete_notes", ["a", "b"]), ("delete_folder", "f")]
    assert c.key(A, Qt.Key_A, Qt.ControlModifier) is True
    assert set(c.selection.keys()) == {A, B, C, F}
    assert c.key(A, Qt.Key_Escape, Qt.NoModifier) is True
    assert c.selection.keys() == []


def test_a_single_icon_handles_enter_itself(ctl):
    c, host = ctl
    assert c.key(A, Qt.Key_Return, Qt.NoModifier) is False
    assert c.key(A, Qt.Key_F2, Qt.NoModifier) is False
    assert host.calls == []


def test_holding_ctrl_does_not_change_the_selection(ctl):
    """Ctrl 로 하나를 뺀 뒤 다음 것을 고르려고 Ctrl 을 누르고 있어도 그대로다."""
    c, _host = ctl
    c.selection.set([A])
    assert c.key(B, Qt.Key_Control, Qt.ControlModifier) is False
    assert c.selection.keys() == [A]


def test_delete_on_an_icon_outside_the_selection_deletes_only_it(ctl):
    c, host = ctl
    c.selection.set([A])
    assert c.key(B, Qt.Key_Delete, Qt.NoModifier) is True
    assert host.calls == [("delete_notes", ["b"])]


def test_menu_for_several_acts_on_all(ctl, monkeypatch):
    c, host = ctl
    shown: list[list[str]] = []

    def run(menu, _pos):
        shown.append([a.text() for a in menu.actions() if not a.isSeparator()])
        next(a for a in menu.actions()
             if a.text() == i18n.t("common.btn_delete")).trigger()

    monkeypatch.setattr(edit_menu, "run_menu", run)
    # 하나만 고른 상태면 그 아이콘이 제 메뉴를 띄운다.
    assert c.menu(A, QPoint(0, 0)) is False and shown == []
    assert c.selection.keys() == [A]
    c.selection.set([A, B])
    assert c.menu(B, QPoint(0, 0)) is True
    assert shown == [[i18n.t("icons.menu_open"), i18n.t("sticky.more_hide"),
                      i18n.t("common.btn_delete")]]
    assert host.calls == [("delete_notes", ["a", "b"])]
    # 고르지 않은 것을 오른쪽으로 누르면 그것만 골라진다.
    assert c.menu(C, QPoint(0, 0)) is False and c.selection.keys() == [C]


def test_marquee_selects_what_it_touches(ctl):
    c, host = ctl
    c.selection.set([F])
    c.marquee.pressed.emit(False)               # 빈 곳을 눌렀다: 고른 것이 풀린다
    assert c.selection.keys() == []
    c.marquee.moved.emit(QRect(60, 0, 40, 60))  # a 와 b 에 걸친다
    assert c.selection.keys() == [A, B]
    assert all(icon.marquee == QRect(60, 0, 40, 60) for icon in host.icons.values())
    c.marquee.moved.emit(QRect(80, 0, 20, 60))  # 줄이면 빠진다
    assert c.selection.keys() == [B]
    c.marquee.finished.emit(False)
    assert all(icon.marquee is None for icon in host.icons.values())
    assert c.selection.keys() == [B] and ("realign",) not in host.calls


def test_marquee_with_ctrl_adds_to_what_was_selected(ctl):
    c, _host = ctl
    c.selection.set([F])
    c.marquee.pressed.emit(True)
    c.marquee.moved.emit(QRect(160, 0, 20, 30))
    assert c.selection.keys() == [F, C]


def test_after_real_icons_were_dragged_ours_get_out_of_the_way(qapp, ctl):
    c, host = ctl
    c.marquee.finished.emit(True)
    _wait_for(lambda: ("realign",) in host.calls, 3000)      # 바쁜 PC 에서도 타이머를 기다린다
    assert ("realign",) in host.calls


# ── 바탕화면의 누름과 끌기를 따라가기 ───────────────────────────────────

class Probe:
    def __init__(self):
        self.pos = (300, 300)
        self.primary = False
        self.secondary = False
        self.adding = False
        self.shift = False

    def cursor_pos(self):
        return self.pos

    def primary_down(self):
        return self.primary

    def secondary_down(self):
        return self.secondary

    def adding_keys(self):
        return self.adding

    def shift_down(self):
        return self.shift

    def drag_threshold(self):
        return 4


def desktop_view(items=()) -> DesktopView:
    return DesktopView(icon=48, spacing=(76, 100), items=tuple(items), snap=True,
                       window=1234, dpi=96, offset=(-14, 3))


def marquee_with(view=None):
    probe, watch = Probe(), NoWatch()
    m = DesktopMarquee(FakeGrid(view), probe=probe, watch=watch)
    events: list = []
    m.pressed.connect(lambda adding: events.append(("pressed", adding)))
    m.moved.connect(lambda rect: events.append(("moved", rect)))
    m.finished.connect(lambda real: events.append(("finished", real)))
    return m, probe, watch, events


def test_one_press_is_one_gesture_and_gives_the_rectangle(qapp):
    m, probe, _watch, events = marquee_with()
    probe.primary = True
    m._on_capture()
    m._on_capture()                 # 한 번 누를 때 신호는 두세 번 온다
    m._on_capture()
    assert events == [("pressed", False)] and m.is_active()
    probe.pos = (302, 301)          # 끌기로 치기에는 덜 움직였다
    m._tick()
    assert events == [("pressed", False)]
    probe.pos = (250, 380)
    m._tick()
    assert events[-1] == ("moved", QRect(QPoint(250, 300), QPoint(300, 380)))
    probe.primary = False
    m._tick()
    assert events[-1] == ("finished", False) and not m.is_active()
    m.stop()


def test_a_click_without_moving_is_just_a_press(qapp):
    m, probe, _watch, events = marquee_with()
    probe.primary, probe.adding = True, True
    m._on_capture()
    probe.primary = False
    m._tick()
    assert events == [("pressed", True), ("finished", False)]
    m.stop()


def test_dragging_a_real_icon_is_not_a_marquee(qapp):
    # 진짜 아이콘 하나가 (114, 102) 에 있다: 칸은 (100, 105) 에서 76x85.
    m, probe, _watch, events = marquee_with(desktop_view([(114, 102)]))
    probe.pos, probe.primary = (130, 130), True
    m._on_capture()
    probe.pos = (400, 400)
    m._tick()
    probe.primary = False
    m._tick()
    assert events == [("pressed", False), ("finished", True)]
    # 그 아래 빈 곳에서 시작하면 사각형이다.
    probe.pos, probe.primary = (130, 195), True
    m._on_capture()
    probe.pos = (400, 400)
    m._tick()
    assert events[-1][0] == "moved"
    m.stop()


def test_right_button_only_clears(qapp):
    m, probe, _watch, events = marquee_with()
    probe.secondary = True
    m._on_capture()
    assert events == [("pressed", False)] and not m.is_active()


def test_watch_follows_the_desktop_window(qapp):
    grid = FakeGrid(None)
    watch = NoWatch()
    m = DesktopMarquee(grid, watch=watch)
    m.sync()
    assert watch.started == []          # 바탕화면을 읽지 못했다(시험 실행)
    grid._view = desktop_view()
    m.sync()
    m.sync()
    assert watch.started == [1234]      # 같은 창이면 다시 걸지 않는다
    grid._view = None                   # 탐색기가 사라졌다
    m.sync()
    assert watch.window() == 0


# ── 그리기 ──────────────────────────────────────────────────────────────

def test_plate_is_painted_even_without_a_theme(qapp, monkeypatch):
    monkeypatch.setattr(native_plate, "_themed", lambda *a, **k: None)
    native_plate.clear_cache()
    img = QImage(80, 80, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    from PySide6.QtCore import QRectF
    native_plate.paint(p, QRectF(0, 0, 76, 70), native_plate.SELECTED)
    native_plate.paint(p, QRectF(0, 0, 0, 0), native_plate.SELECTED)   # 빈 자리
    p.end()
    assert img.pixelColor(38, 35).alpha() > 40
    assert img.pixelColor(78, 78).alpha() == 0
    native_plate.clear_cache()


def test_hit_area_is_the_picture_and_the_name_not_the_whole_cell(qapp):
    from PySide6.QtCore import QRectF
    cell = QRectF(0, 0, 76, 100)
    rects = nf.icon_hit_rects(cell, "회의", qapp.font(), 48)
    assert len(rects) == 2
    picture, name = rects
    assert picture.width() == 48 and name.top() >= picture.bottom()
    # 칸의 왼쪽 위 귀퉁이와 맨 아래는 비어 있다.
    assert not any(r.contains(2, 2) for r in rects)
    assert not any(r.contains(38, 98) for r in rects)
    plate = nf.icon_plate_rect(cell, "회의", qapp.font(), 48)
    assert plate.width() == 76 and plate.top() == 0 and plate.height() < 100


# ── 실제 창으로 ─────────────────────────────────────────────────────────

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


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(StickyNoteWidget, "bring_to_front",
                        lambda self, *, start_editing=False: None)
    client = FakeClient()
    win = MainWindow(client=client,
                     local_state=LocalStateStore(tmp_path / "local_state.json"),
                     role="guest", settings=AppSettings(fold_animation=False))
    client.connectionChanged.emit(True)
    client._dispatch("welcome", {
        "users": [ME, OTHER], "host": OTHER, "locks": {}, "heartbeat_ms": 3000,
        "trash_days": config.TRASH_KEEP_DAYS,
        "folders": [{"id": "f1", "name": "업무", "color": "#BAE1FF",
                     "created_by": OTHER, "created_at": 1, "updated_at": 1,
                     "private_owner": None, "deleted": 0}],
        "notes": [note("a", "회의"), note("b", "견적"), note("c", "점심")]})
    qapp.processEvents()
    for nid in "abc":
        win._set_note_visibility(nid, True)
        win._stickies[nid].fold(style=nf.FOLD_ICON)
    win._place_folder_icon("f1", quiet=True)
    qapp.processEvents()
    yield win, client
    win._force_quit = True
    win._close_folder_windows()
    win._teardown_stickies()
    win._teardown_folder_icons()
    win.close()
    win.deleteLater()
    qapp.processEvents()


def test_icons_on_the_desktop_are_the_selectable_ones(window):
    win, _client = window
    assert set(win.icon_widgets()) == {note_key("a"), note_key("b"), note_key("c"),
                                       folder_key("f1")}
    win._stickies["c"].unfold()
    assert note_key("c") not in win.icon_widgets()
    assert note_key("c") in win.all_icon_widgets()


def test_click_selects_and_ctrl_click_adds(qapp, window):
    win, _client = window
    a, b = win._stickies["a"], win._stickies["b"]
    QTest.mouseClick(a, Qt.LeftButton, Qt.NoModifier, QPoint(38, 30))
    assert win._icon_ctl.selection.keys() == [note_key("a")]
    assert a._icon_selected and not b._icon_selected and a.is_folded()
    QTest.mouseClick(b, Qt.LeftButton, Qt.ControlModifier, QPoint(38, 30))
    QTest.mouseClick(win._folder_icons["f1"], Qt.LeftButton, Qt.ControlModifier,
                     QPoint(38, 30))
    assert win._icon_ctl.selection.keys() == [note_key("a"), note_key("b"),
                                              folder_key("f1")]
    assert b._icon_selected and win._folder_icons["f1"]._selected
    # 펴면 더는 아이콘이 아니다. 고른 것에서 빠진다.
    a.unfold()
    assert note_key("a") not in win._icon_ctl.selection and not a._icon_selected


def test_delete_key_sends_selected_notes_to_the_trash(qapp, window):
    win, client = window
    a, b = win._stickies["a"], win._stickies["b"]
    QTest.mouseClick(a, Qt.LeftButton, Qt.NoModifier, QPoint(38, 30))
    QTest.mouseClick(b, Qt.LeftButton, Qt.ControlModifier, QPoint(38, 30))
    QTest.keyClick(b, Qt.Key_Delete)
    assert client.of("delete") == [("delete", "a"), ("delete", "b")]


def test_folded_menu_can_delete(qapp, window, monkeypatch):
    win, client = window
    a = win._stickies["a"]
    shown: list[list[str]] = []

    def run(menu, _pos):
        shown.append([x.text() for x in menu.actions() if not x.isSeparator()])
        next(x for x in menu.actions()
             if x.text() == i18n.t("common.btn_delete")).trigger()

    monkeypatch.setattr(edit_menu, "run_menu", run)
    a.contextMenuEvent(QContextMenuEvent(
        QContextMenuEvent.Mouse, QPoint(38, 30), a.mapToGlobal(QPoint(38, 30))))
    assert i18n.t("common.btn_delete") in shown[0]
    assert win._icon_ctl.selection.keys() == [note_key("a")]
    assert client.of("delete") == [("delete", "a")]


def test_marquee_over_real_widgets(qapp, window):
    win, _client = window
    a = win._stickies["a"]
    rect = a.geometry().adjusted(20, 10, -20, -60)      # 그림 한가운데
    win._icon_ctl.marquee.pressed.emit(False)
    win._icon_ctl.marquee.moved.emit(rect)
    assert win._icon_ctl.selection.keys() == [note_key("a")]
    assert a._icon_selected and a._marquee_tint is not None
    # 칸의 귀퉁이만 스치면 고르지 않는다(탐색기도 그림과 이름으로 본다).
    corner = QRect(a.geometry().topLeft(), a.geometry().topLeft() + QPoint(6, 1))
    win._icon_ctl.marquee.moved.emit(corner)
    assert win._icon_ctl.selection.keys() == []
    win._icon_ctl.marquee.finished.emit(False)
    assert a._marquee_tint is None


def test_selected_icons_are_dragged_together(qapp, window):
    win, _client = window
    a, b = win._stickies["a"], win._stickies["b"]
    before_b = b.pos()
    win._icon_ctl.selection.set([note_key("a"), note_key("b")])
    start = a.mapToGlobal(QPoint(38, 30))
    a._slip_press = start
    a._slip_grab = QPoint(38, 30)
    a._begin_slip_drag()
    a._drag_slip_to(start + QPoint(228, 200))
    assert b.pos() == before_b + QPoint(228, 200)
    a._end_slip_drag()
    grid = nf.icon_grid(win._area_at(b.pos()))
    assert b.pos() == grid.pos_of(*grid.cell_at(b.pos()))      # 칸에 맞춰 놓였다
    assert b.pos() != before_b and b.pos() != a.pos()
    assert win._local.state.notes["b"].fold_at is True


def test_dropping_several_on_a_folder_icon_moves_them_all(qapp, window):
    win, client = window
    a, b = win._stickies["a"], win._stickies["b"]
    home_a, home_b = a.pos(), b.pos()
    icon = win._folder_icons["f1"]
    win._icon_ctl.selection.set([note_key("a"), note_key("b")])
    start = a.mapToGlobal(QPoint(38, 30))
    a._slip_press = start
    a._slip_grab = QPoint(38, 30)
    a._begin_slip_drag()
    a._drag_slip_to(icon.geometry().center())
    qapp.processEvents()
    a._end_slip_drag()
    assert sorted(client.of("folder")) == [("folder", "a", "f1"), ("folder", "b", "f1")]
    # 접어 둔 폴더로 들어갔으니 바탕화면에서 사라진다. 아이콘의 자리는 끌기 전
    # 그대로라, 폴더를 펼치면 있던 곳에 다시 나타난다.
    assert not a.isVisible() and not b.isVisible()
    assert a._fold_pos == home_a and b._fold_pos == home_b
    win._set_folder_opened("f1", True, animate=False)
    qapp.processEvents()
    assert b.isVisible() and b.pos() == home_b and a.pos() == home_a


def _line_up(win) -> tuple:
    """a b c 를 첫 줄에 나란히, 폴더 아이콘을 a 의 두 칸 아래에 놓는다."""
    grid = nf.icon_grid(win._area_at(QPoint(100, 100)))
    a, b, c = (win._stickies[n] for n in "abc")
    for col, w in enumerate((a, b, c)):
        w.move_icon(grid.pos_of(2 + col, 0))
    spot = grid.pos_of(2, 2)
    win._folder_icons["f1"].place(spot.x(), spot.y())
    return a, b, c, win._folder_icons["f1"]


def test_shift_click_takes_the_icons_in_between(qapp, window):
    win, _client = window
    a, _b, c, folder = _line_up(win)
    QTest.mouseClick(a, Qt.LeftButton, Qt.NoModifier, QPoint(38, 30))
    QTest.mouseClick(c, Qt.LeftButton, Qt.ShiftModifier, QPoint(38, 30))
    assert win._icon_ctl.selection.keys() == [note_key("a"), note_key("b"),
                                              note_key("c")]
    # 기준(a)은 그대로다. 아래의 폴더까지 누르면 a 와 폴더만 남는다.
    QTest.mouseClick(folder, Qt.LeftButton, Qt.ShiftModifier, QPoint(38, 30))
    assert win._icon_ctl.selection.keys() == [note_key("a"), folder_key("f1")]


def test_dragging_memo_icons_moves_the_selected_real_icons_too(qapp, window):
    win, _client = window
    a, b, _c, _folder = _line_up(win)
    desk = FakeDesk()
    desk.real = [5]
    win._icon_ctl._desk = desk
    win._icon_ctl.selection.set([note_key("a"), note_key("b")])
    win._icon_ctl.pressed(note_key("a"), Qt.NoModifier)
    start = a.mapToGlobal(QPoint(38, 30))
    a._slip_press = start
    a._slip_grab = QPoint(38, 30)
    a._begin_slip_drag()
    a._drag_slip_to(start + QPoint(152, 300))       # 오른쪽으로 두 칸, 아래로 세 칸
    a._end_slip_drag()
    assert desk.did("move_by") == [("move_by", [5], 152, 300)]
    assert win._desktop_grid._lifted == frozenset()


def _drag(note, by: QPoint) -> None:
    start = note.mapToGlobal(QPoint(38, 30))
    note._slip_press = start
    note._slip_grab = QPoint(38, 30)
    note._begin_slip_drag()
    note._drag_slip_to(start + by)


def test_memo_icons_dropped_on_the_recycle_bin_go_to_the_memo_trash(qapp, window):
    win, client = window
    a, b, _c, _folder = _line_up(win)
    home_a, home_b = a.pos(), b.pos()
    desk = FakeDesk()
    desk.real = [5]
    ctl = win._icon_ctl
    ctl._desk = desk
    ctl._real_under = lambda pos, ask=False: ON_BIN
    ctl.selection.set([note_key("a"), note_key("b")])
    ctl.pressed(note_key("a"), Qt.NoModifier)
    _drag(a, QPoint(304, 200))
    assert a.windowOpacity() < 1.0 and b.windowOpacity() < 1.0   # 휴지통 위에서 흐려진다
    a._end_slip_drag()
    assert client.of("delete") == [("delete", "a"), ("delete", "b")]
    assert desk.did("delete") == [("delete",)]          # 함께 고른 파일은 탐색기가
    assert desk.did("move_by") == []
    # 지워지지 않았을 때(공유 PC 가 거절했다)에도 제자리에 있도록 돌아가 있다.
    assert a._fold_pos == home_a and b._fold_pos == home_b
    assert a.windowOpacity() == 1.0 and b.windowOpacity() == 1.0


def test_memo_icons_dropped_on_a_real_folder_come_back(qapp, window):
    win, client = window
    a, b, _c, _folder = _line_up(win)
    home_a, home_b = a.pos(), b.pos()
    desk = FakeDesk()
    desk.real = [5]
    ctl = win._icon_ctl
    ctl._desk = desk
    ctl._real_under = lambda pos, ask=False: ON_CONTAINER if ask else ""
    ctl.selection.set([note_key("a"), note_key("b")])
    ctl.pressed(note_key("a"), Qt.NoModifier)
    _drag(a, QPoint(304, 200))
    assert a.windowOpacity() == 1.0
    a._end_slip_drag()
    assert a._fold_pos == home_a and b._fold_pos == home_b
    assert client.calls == [] and desk.did("move_by") == [] and desk.did("delete") == []


def test_a_folder_icon_dropped_on_a_real_folder_comes_back(qapp, window):
    win, _client = window
    _a, _b, _c, folder = _line_up(win)
    home = folder.pos()
    ctl = win._icon_ctl
    ctl._desk = FakeDesk()
    ctl._real_under = lambda pos, ask=False: ON_CONTAINER if ask else ""
    QTest.mousePress(folder, Qt.LeftButton, Qt.NoModifier, QPoint(38, 30))
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent
    target = folder.mapToGlobal(QPoint(38, 30)) + QPoint(228, 100)
    QApplication.sendEvent(folder, QMouseEvent(
        QEvent.MouseMove, QPointF(folder.mapFromGlobal(target)), QPointF(target),
        Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
    assert folder.pos() != home
    QApplication.sendEvent(folder, QMouseEvent(
        QEvent.MouseButtonRelease, QPointF(folder.mapFromGlobal(target)),
        QPointF(target), Qt.LeftButton, Qt.NoButton, Qt.NoModifier))
    assert folder.icon_spot() == home


def test_the_folder_icon_passes_how_far_it_was_dragged(qapp, window):
    win, _client = window
    _a, _b, _c, folder = _line_up(win)
    desk = FakeDesk()
    desk.real = [8]
    win._icon_ctl._desk = desk
    win._icon_ctl.selection.set([folder_key("f1"), note_key("a")])
    at = folder.mapToGlobal(QPoint(38, 30))
    QTest.mousePress(folder, Qt.LeftButton, Qt.NoModifier, QPoint(38, 30))
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent
    target = at + QPoint(228, 100)
    QApplication.sendEvent(folder, QMouseEvent(
        QEvent.MouseMove, QPointF(folder.mapFromGlobal(target)), QPointF(target),
        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    QTest.mouseRelease(folder, Qt.LeftButton, Qt.NoModifier,
                       folder.mapFromGlobal(target))
    assert desk.did("move_by") == [("move_by", [8], 228, 100)]


def test_private_folders_read_as_folders_in_the_selector(qapp, window):
    """나만 보는 폴더도 다른 폴더와 같은 줄에 이름만 적힌다(자물쇠 그림이 알린다)."""
    win, client = window
    client._dispatch("folder_created", {"folder": {
        "id": "p1", "name": "개인", "color": "#E0BBE4", "created_by": ME,
        "created_at": 1, "updated_at": 1, "private_owner": ME, "deleted": 0}})
    qapp.processEvents()
    menu = win._folder_selector_menu()
    texts = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert "개인" in texts and "업무" in texts
    assert not any("나만 보기" in t for t in texts)
    # 표시는 폴더 색으로 채운 자물쇠다(다른 폴더의 색 동그라미 자리).
    from app.ui.icons import folder_lock_icon
    body = folder_lock_icon("#E0BBE4", 18).pixmap(18, 18).toImage().pixelColor(9, 12)
    assert (body.red(), body.green(), body.blue()) == (0xE0, 0xBB, 0xE4)
    win._select_folder("p1")
    assert win._folder_chip.text() == "개인"
    menu.deleteLater()
