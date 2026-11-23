"""바탕화면 아이콘과 같은 칸에 놓이는 메모와 폴더 아이콘(offscreen).

진짜 바탕화면은 읽지 않는다. 이 PC 에서 잰 값(아이콘 48, 칸 76x100, 첫 항목의
자리 (14, 2), 자리에서 칸까지 (-14, 3))을 넣어 격자를 계산해 본다."""
from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt  # noqa: E402
from PySide6.QtGui import QCursor, QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app import desktop_icons, i18n, ipc  # noqa: E402
from app.desktop_icons import DesktopView  # noqa: E402
from app.local_state import FolderIconState, LocalStateStore  # noqa: E402
from app.network.client_base import ClientBase  # noqa: E402
from app.settings import AppSettings  # noqa: E402
from app.ui import icon_grid as ig  # noqa: E402
from app.ui import note_fold as nf  # noqa: E402
from app.ui.folder_icon import FolderIconWidget  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.sticky_note import StickyNoteWidget  # noqa: E402

ME = "지우"
FULL = QRect(0, 0, 1920, 1080)
WORK = QRect(0, 0, 1920, 1032)


def view(icon=48, spacing=(76, 100), items=((14, 2), (14, 102), (90, 2)),
         snap=True, dpi=96, offset=(-14, 3)) -> DesktopView:
    return DesktopView(icon=icon, spacing=spacing, items=tuple(items), snap=snap,
                       window=0, dpi=dpi, offset=offset)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    i18n.set_language("ko")
    yield app


@pytest.fixture(autouse=True)
def default_grid_afterwards():
    yield
    # 시험이 넣은 격자가 다른 시험으로 새지 않게 한다.
    nf.set_icon_grid_source(None)


def _avail() -> QRect:
    return QGuiApplication.primaryScreen().availableGeometry()


# ── 격자 계산 ───────────────────────────────────────────────────────────

def test_grid_matches_the_measured_desktop():
    grid = ig.screen_grid(view(), FULL, WORK, 1.0)
    assert grid.cell == QSize(76, 100) and grid.icon == 48
    # 첫 칸은 화면 왼쪽 끝에서, 위에서 5픽셀 내려와 시작한다.
    assert grid.pos_of(0, 0) == QPoint(0, 5)
    assert grid.pos_of(3, 2) == QPoint(228, 205)
    assert (grid.cols(), grid.rows()) == (25, 10)
    # 진짜 아이콘이 놓인 칸을 안다.
    assert grid.taken == frozenset({(0, 0), (0, 1), (1, 0)})
    # 기본 격자(바탕화면을 읽지 못했을 때)와 같은 모양이다.
    assert grid.same_shape(nf.default_icon_grid(WORK))


def test_grid_is_estimated_when_the_offset_could_not_be_measured():
    measured = ig.screen_grid(view(), FULL, WORK, 1.0)
    guessed = ig.screen_grid(view(offset=None), FULL, WORK, 1.0)
    assert guessed.same_shape(measured) and guessed.taken == measured.taken
    # 아이콘이 하나도 없어도(재지 못한다) 기본 자리에서 시작한다.
    empty = ig.screen_grid(view(items=(), offset=None), FULL, WORK, 1.0)
    assert empty.same_shape(measured) and empty.taken == frozenset()


@pytest.mark.parametrize("icon,spacing", [
    (16, (60, 50)), (32, (76, 76)), (48, (76, 100)), (64, (92, 116)),
    (96, (124, 148)), (256, (284, 308))])
def test_grid_follows_any_icon_size(icon, spacing):
    cx, cy = spacing
    first = ((cx - icon) // 2, 2)
    items = (first, (first[0] + cx * 4, first[1] + cy * 2))
    grid = ig.screen_grid(view(icon, spacing, items, offset=(-first[0], 3)),
                          FULL, WORK, 1.0)
    assert grid.cell == QSize(cx, cy) and grid.icon == icon
    assert grid.pos_of(0, 0) == QPoint(0, 5)
    assert grid.pos_of(4, 2) == QPoint(cx * 4, 5 + cy * 2)
    assert grid.taken == frozenset({(0, 0), (4, 2)})
    # 화면을 벗어나는 칸은 없다.
    last = grid.pos_of(grid.cols() - 1, grid.rows() - 1)
    assert WORK.contains(QRect(last, grid.cell))


def test_grid_on_a_scaled_screen_stays_on_the_real_cells():
    # 150% 배율: Qt 가 주는 화면은 1280x720, 진짜 픽셀은 1920x1080.
    geometry, area = QRect(0, 0, 1280, 720), QRect(0, 0, 1280, 688)
    v = view(72, (113, 150), ((20, 2), (20 + 113 * 7, 2 + 150 * 3)), dpi=144,
             offset=(-20, 3))
    grid = ig.screen_grid(v, geometry, area, 1.5)
    assert grid.cell == QSize(75, 100) and grid.icon == 48
    # 칸마다 반올림한 폭을 더해 가지 않는다. 오른쪽 끝에서도 진짜 칸과 반 픽셀 안이다.
    for col in (0, 1, 7, 15):
        real = col * 113 / 1.5
        assert abs(grid.pos_of(col, 0).x() - real) <= 0.5
    for row in (0, 3, 6):
        real = (5 + row * 150) / 1.5
        assert abs(grid.pos_of(0, row).y() - real) <= 0.5
    assert (7, 3) in grid.taken


def test_second_screen_uses_the_same_cell_layout():
    second_full, second_work = QRect(1920, 0, 1920, 1080), QRect(1920, 0, 1920, 1032)
    screens = [(FULL, WORK, 1.0), (second_full, second_work, 1.0)]
    v = view()
    residue = ig.item_residue(v, screens)
    assert residue == (14, 2)
    grid = ig.screen_grid(v, second_full, second_work, 1.0, residue)
    # 아이콘이 없는 화면도 같은 자리에서 칸이 시작한다.
    assert grid.pos_of(0, 0) == QPoint(1920, 5) and grid.taken == frozenset()


def test_grid_starts_at_the_work_area_not_the_screen():
    # 작업 표시줄을 왼쪽에 둔 화면.
    area = QRect(62, 0, 1858, 1080)
    v = view(items=((62 + 14, 2), (62 + 14 + 76, 2)))
    grid = ig.screen_grid(v, FULL, area, 1.0)
    assert grid.pos_of(0, 0) == QPoint(62, 5)
    assert grid.taken == frozenset({(0, 0), (1, 0)})


def test_free_placement_falls_back_to_the_default_spot():
    # '아이콘을 그리드에 맞춤'을 끈 바탕화면은 아이콘의 자리로 칸을 짐작할 수 없다.
    v = view(items=((333, 77), (41, 512)), snap=False)
    assert ig.item_residue(v, [(FULL, WORK, 1.0)]) == (14, 2)


def test_snap_avoids_real_icons_and_our_own():
    grid = ig.screen_grid(view(), FULL, WORK, 1.0)
    # 진짜 아이콘이 놓인 칸 (0, 0) 에 놓으면 가까운 빈 칸으로 간다.
    got = nf.snap_icon_pos(QPoint(4, 9), WORK, [], grid=grid)
    assert grid.cell_at(got) not in grid.taken
    assert grid.cell_at(got) == (1, 1)
    # 우리 아이콘이 있는 칸도 비킨다.
    other = nf.snap_icon_pos(QPoint(4, 9), WORK, [got], grid=grid)
    assert other != got and grid.cell_at(other) not in grid.taken
    # 빈 칸은 그대로다.
    free = nf.snap_icon_pos(QPoint(76 * 5 + 20, 5 + 100 * 4 - 30), WORK, [], grid=grid)
    assert free == grid.pos_of(5, 4)


def test_icons_keep_their_cell_when_the_grid_changes():
    small = ig.screen_grid(view(), FULL, WORK, 1.0)
    large = ig.screen_grid(view(96, (124, 148), ((14, 2),)), FULL, WORK, 1.0)
    assert not small.same_shape(large)
    # 진짜 아이콘처럼 같은 칸에 남는다.
    assert nf.move_to_grid(small.pos_of(3, 2), small, large) == large.pos_of(3, 2)
    # 큰 격자에 없는 칸은 끝 칸으로 들어온다.
    edge = nf.move_to_grid(small.pos_of(24, 9), small, large)
    assert edge == large.pos_of(large.cols() - 1, large.rows() - 1)
    assert WORK.contains(QRect(edge, large.cell))
    # 되돌리면 제자리다.
    assert nf.move_to_grid(large.pos_of(3, 2), large, small) == small.pos_of(3, 2)


def test_wall_probe_lets_icons_sit_in_the_first_cell():
    cell = QRect(0, 205, 76, 100)
    probe = nf.icon_wall_probe(cell, 48)
    assert cell.contains(probe) and probe.left() - cell.left() >= 14 + nf.SNAP_DISTANCE
    # 아주 작은 칸에서도 뒤집히지 않는다.
    tiny = nf.icon_wall_probe(QRect(0, 0, 40, 30), 16)
    assert tiny.width() > 0 and tiny.height() > 0


# ── 그림 ────────────────────────────────────────────────────────────────

def _strong_box(img: QImage, limit_y: int) -> QRect:
    xs, ys = [], []
    for y in range(min(img.height(), limit_y)):
        for x in range(img.width()):
            if img.pixelColor(x, y).alpha() >= 200:
                xs.append(x)
                ys.append(y)
    return QRect(QPoint(min(xs), min(ys)), QPoint(max(xs), max(ys)))


@pytest.mark.parametrize("icon,cell", [
    (16, (60, 50)), (32, (76, 76)), (48, (76, 100)), (96, (124, 148)),
    (256, (284, 308))])
def test_glyph_sits_where_a_real_icon_image_would(qapp, icon, cell):
    img = QImage(QSize(*cell), QImage.Format_ARGB32_Premultiplied)
    for painter in ("note", "folder"):
        img.fill(Qt.transparent)
        p = QPainter(img)
        rect = QRectF(0, 0, cell[0], cell[1])
        if painter == "note":
            nf.paint_icon(p, rect, "#FFE066", "", icon=icon)
        else:
            nf.paint_folder_icon(p, rect, "#BAE1FF", "", icon=icon)
        p.end()
        box = _strong_box(img, 2 + icon + 2)
        want = QRect((cell[0] - icon) // 2, 2, icon, icon)
        # 그림은 진짜 아이콘의 그림 칸(폭의 가운데, 위에서 2픽셀) 안에 있다.
        assert want.adjusted(-1, -1, 1, 1).contains(box), (painter, box, want)
        # 그 칸을 거의 채운다(메모는 세로로, 폴더는 가로로 꽉 찬다).
        if painter == "note":
            assert box.height() >= icon - 2 and box.width() >= icon * 0.8
        else:
            assert box.width() >= icon - 2 and box.height() >= icon * 0.6
        assert abs(box.center().x() - want.center().x()) <= 1


def test_padding_inside_the_cell_is_in_screen_pixels():
    cell = QRectF(0, 0, 75, 100)
    # 100% 에서는 위 2, 그림과 이름 사이 3.
    assert nf.icon_glyph_rect(cell, 48).top() == 2
    assert nf.icon_label_rect(cell, 48).top() == 2 + 48 + 3
    # 150% 에서도 화면 픽셀로 같은 값이다(논리 픽셀로는 그만큼 줄어든다).
    assert nf.icon_glyph_rect(cell, 48, 1.5).top() == pytest.approx(2 / 1.5)
    assert nf.icon_label_rect(cell, 48, dpr=1.5).top() == pytest.approx(48 + 5 / 1.5)


def test_open_folder_flap_has_no_sharp_corners():
    points = [QPointF(10, 10), QPointF(60, 10), QPointF(54, 40), QPointF(4, 40)]
    path = nf.rounded_polygon(points, 4.0)
    for corner in points:
        assert not path.contains(corner)
    assert path.contains(QPointF(32, 25))
    # 변의 가운데는 그대로 지난다.
    assert path.contains(QPointF(35, 10.4)) and path.contains(QPointF(29, 39.6))
    # 변보다 큰 반지름을 줘도 깨지지 않는다.
    assert not nf.rounded_polygon(points, 400.0).isEmpty()
    assert nf.rounded_polygon(points[:2], 4.0).isEmpty()


def test_open_folder_is_painted_with_round_corners(qapp):
    size = 96
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    for opened in (False, True):
        img.fill(Qt.transparent)
        p = QPainter(img)
        nf.paint_folder_glyph(p, nf.folder_glyph_rect(QRectF(0, 0, size, size)),
                              "#BAE1FF", opened=opened)
        p.end()
        box = _strong_box(img, size)
        # 아래쪽 두 모서리의 바로 그 화소는 비어 있다(둥글게 깎였다).
        assert img.pixelColor(box.left(), box.bottom()).alpha() < 128, opened
        assert img.pixelColor(box.right(), box.bottom()).alpha() < 128, opened


# ── 바탕화면 지켜보기 ───────────────────────────────────────────────────

def test_isolated_runs_do_not_read_the_real_desktop(qapp, monkeypatch):
    monkeypatch.delenv("MEMIL_REAL_DESKTOP", raising=False)
    assert not ig._real_desktop_allowed()
    called: list[int] = []
    monkeypatch.setattr(desktop_icons, "read", lambda: called.append(1))
    watch = ig.DesktopGrid()
    watch.refresh()
    assert called == [] and watch.view() is None
    assert watch.grid_for(_avail()) is None
    # 그때는 기본 격자를 쓴다.
    nf.set_icon_grid_source(watch.grid_for)
    assert nf.icon_grid(_avail()).same_shape(nf.default_icon_grid(_avail()))


def test_watch_reports_a_changed_icon_size_once(qapp):
    now = {"view": view(), "spacing": (76, 100)}
    watch = ig.DesktopGrid(reader=lambda: now["view"],
                           light=lambda window: now["spacing"])
    seen: list[int] = []
    watch.changed.connect(lambda: seen.append(1))
    watch.refresh()
    assert seen == [1]
    area = _avail()
    assert watch.grid_for(area).cell == QSize(76, 100)
    # 그대로면 알리지 않는다.
    watch._tick()
    watch.refresh()
    assert seen == [1]
    # 진짜 아이콘을 옮기기만 한 것은 칸의 모양이 아니다.
    now["view"] = view(items=((14, 2), (14 + 76 * 3, 2)))
    watch.refresh()
    assert seen == [1] and (3, 0) in watch.grid_for(area).taken
    # 사용자가 아이콘 크기를 바꿨다(Ctrl + 휠). 가벼운 물음이 먼저 알아챈다.
    now["view"] = view(96, (124, 148), ((14, 2),))
    now["spacing"] = (124, 148)
    watch._tick()
    assert seen == [1, 1]
    assert watch.grid_for(area).cell == QSize(124, 148)
    assert watch.grid_for(area).icon == 96


def test_watch_rereads_now_and_then_unless_reading_is_slow(qapp):
    # 칸의 간격은 그대로인데 아이콘 크기만 달라진 경우(가벼운 물음으로는 모른다).
    now = {"view": view(16, (75, 75), ((29, 2),), offset=(-29, 3)), "reads": 0}

    def reader():
        now["reads"] += 1
        return now["view"]

    watch = ig.DesktopGrid(reader=reader, light=lambda window: (75, 75))
    seen: list[int] = []
    watch.changed.connect(lambda: seen.append(1))
    watch.refresh()
    assert (now["reads"], seen) == (1, [1])
    now["view"] = view(20, (75, 75), ((27, 2),), offset=(-27, 3))
    watch._tick()
    assert now["reads"] == 1                 # 방금 읽었으면 다시 읽지 않는다
    watch._read_at -= ig.FULL_READ_SECONDS + 1
    watch._tick()
    assert now["reads"] == 2 and seen == [1, 1]
    assert watch.grid_for(_avail()).icon == 20
    # 읽기가 느린 PC(바탕화면에 아이콘이 아주 많다)에서는 가끔 읽기를 하지 않는다.
    watch._read_at -= ig.FULL_READ_SECONDS + 1
    watch._read_cost = ig.FULL_READ_MAX_COST * 3
    watch._tick()
    assert now["reads"] == 2


def test_watch_survives_an_explorer_that_does_not_answer(qapp):
    state = {"fail": False}

    def reader():
        if state["fail"]:
            raise OSError("탐색기가 답하지 않는다")
        return view()

    watch = ig.DesktopGrid(reader=reader, light=lambda window: None)
    watch.refresh()
    assert watch.grid_for(_avail()) is not None
    state["fail"] = True
    watch.refresh()
    assert watch.view() is None and watch.grid_for(_avail()) is None
    watch._tick()                       # 다시 물어볼 뿐 터지지 않는다


def test_cell_offset_needs_a_window_and_items():
    assert desktop_icons._cell_offset(0, [(0, 14, 2)], (76, 100)) is None
    assert desktop_icons._cell_offset(1234, [], (76, 100)) is None


# ── 메인 창: 격자가 바뀌면 아이콘이 따라간다 ────────────────────────────

class FakeClient(ClientBase):
    def __init__(self, nickname: str = ME):
        super().__init__(nickname)
        self.folders_made: list[dict] = []
        self.folder_updates: list[tuple[str, dict]] = []
        self.moves: list[tuple[str, str | None]] = []

    def create_folder(self, name, color, private=False, folder_id=None) -> None:
        folder = {"id": folder_id or f"f{len(self.folders_made) + 1}", "name": name,
                  "color": color, "private_owner": None}
        self.folders_made.append(folder)
        self.folderCreated.emit(dict(folder))

    def update_folder(self, folder_id, *, name=None, color=None, private=None) -> None:
        self.folder_updates.append((folder_id, {"name": name, "color": color}))

    def set_note_folder(self, note_id, folder_id) -> None:
        self.moves.append((note_id, folder_id))


def _note(nid: str, text: str = "메모", folder_id=None) -> dict:
    return {"id": nid, "version": 1, "color": "#FFE066", "width": 280,
            "height": 240, "content": text, "created_by": ME, "updated_by": ME,
            "updated_at": int(time.time() * 1000), "folder_id": folder_id}


def _window(qapp, tmp_path, notes=(), folders=(), seed=None, welcome=True):
    store = LocalStateStore(tmp_path / "local_state.json")
    if seed is not None:
        seed(store.state)
        store.save()
    client = FakeClient()
    settings = AppSettings()
    settings.fold_animation = False
    settings.fold_style = "icon"
    win = MainWindow(client=client, local_state=store, role="host", settings=settings)
    client.connectionChanged.emit(True)
    if welcome:
        client.welcomed.emit({"heartbeat_ms": 3000, "notes": list(notes), "locks": {},
                              "users": [ME], "folders": list(folders), "host": ME})
    qapp.processEvents()
    return win


def _close(qapp, win) -> None:
    win._force_quit = True
    win._desktop_grid.stop()
    win._teardown_stickies()
    win._teardown_folder_icons()
    win.close()
    win.deleteLater()
    qapp.processEvents()
    nf.set_icon_grid_source(None)


def _use_desktop(win, v: DesktopView) -> None:
    """이 창이 보는 바탕화면을 ``v`` 로 바꾼다(사용자가 아이콘 크기를 바꿨다)."""
    win._desktop_grid._reader = lambda: v
    win._desktop_grid._light = lambda window: v.spacing
    win._desktop_grid.refresh()


FOLDER = {"id": "f1", "name": "업무", "color": "#BAE1FF", "private_owner": None}


def _legacy(state) -> None:
    # 이 기능이 없던 버전의 고정 격자(96x106, 벽에서 10)에 놓인 아이콘들.
    state.hides_new = True
    state.set_note_state("a", x=300, y=200, folded=True, fold_style="icon",
                         fold_x=10 + 96 * 2, fold_y=10 + 106 * 3, fold_at=True)
    state.set_note_state("b", x=320, y=220, folded=True, fold_style="icon",
                         fold_x=10 + 96 * 3, fold_y=10 + 106 * 3, fold_at=True)
    state.folder_icons["f1"] = FolderIconState(x=10 + 96 * 5, y=10, opened=True)


def test_icons_from_the_old_fixed_grid_move_to_the_nearest_real_cell(qapp, tmp_path):
    win = _window(qapp, tmp_path, [_note("a"), _note("b")], [FOLDER], _legacy)
    area = _avail()
    grid = nf.icon_grid(area)
    a, b, icon = win._stickies["a"], win._stickies["b"], win._folder_icons["f1"]
    for widget, old in ((a, QPoint(202, 328)), (b, QPoint(298, 328)),
                        (icon, QPoint(490, 10))):
        assert widget.size() == grid.cell
        pos = widget.pos()
        assert grid.pos_of(*grid.cell_at(pos)) == pos           # 칸에 맞는다
        assert abs(pos.x() - old.x()) <= grid.cell.width() // 2 + 1
        assert abs(pos.y() - old.y()) <= grid.cell.height() // 2 + 1
    assert a.pos() != b.pos()
    # 옮긴 자리와 그때의 격자를 적어 둔다.
    state = win._local.state
    assert (state.notes["a"].fold_x, state.notes["a"].fold_y) == (a.x(), a.y())
    assert (state.folder_icons["f1"].x, state.folder_icons["f1"].y) == (icon.x(), icon.y())
    key = ",".join(str(v) for v in (area.left(), area.top(), area.width(), area.height()))
    assert state.icon_grids[key] == [76.0, 100.0, 0.0, 5.0]
    _close(qapp, win)


def _on_cells(state) -> None:
    state.hides_new = True
    state.set_note_state("a", x=300, y=200, folded=True, fold_style="icon",
                         fold_x=76 * 2, fold_y=5 + 100 * 3, fold_at=True)
    # 펼쳐 둔 메모도 접으면 갈 칸을 가지고 있다.
    state.set_note_state("b", x=320, y=220, folded=False, fold_style="icon",
                         fold_x=76 * 4, fold_y=5 + 100, fold_at=True)
    state.folder_icons["f1"] = FolderIconState(x=76 * 5, y=5, opened=True)
    state.icon_grids["0,0,800,800"] = [76.0, 100.0, 0.0, 5.0]


def test_icons_resize_and_keep_their_cells_when_desktop_icons_change(qapp, tmp_path):
    win = _window(qapp, tmp_path, [_note("a"), _note("b")], [FOLDER], _on_cells)
    area = _avail()
    a, b, icon = win._stickies["a"], win._stickies["b"], win._folder_icons["f1"]
    assert a.pos() == QPoint(152, 305) and a.size() == QSize(76, 100)
    assert icon.pos() == QPoint(380, 5)

    # 사용자가 바탕화면 아이콘을 키웠다.
    _use_desktop(win, view(96, (124, 148), ((14, 2),)))
    big = nf.icon_grid(area)
    assert big.cell == QSize(124, 148) and big.icon == 96
    assert a.geometry() == QRect(big.pos_of(2, 3), big.cell)
    assert icon.geometry() == QRect(big.pos_of(5, 0), big.cell)
    # 펼쳐 둔 메모는 그대로 펼쳐 있고, 접으면 갈 칸만 따라간다.
    assert not b.is_folded() and b.pos() == QPoint(320, 220)
    state = win._local.state
    assert (state.notes["b"].fold_x, state.notes["b"].fold_y) == (
        big.pos_of(4, 1).x(), big.pos_of(4, 1).y())
    assert (state.notes["a"].fold_x, state.notes["a"].fold_y) == (a.x(), a.y())
    assert (state.folder_icons["f1"].x, state.folder_icons["f1"].y) == (icon.x(), icon.y())
    b.fold()
    assert b.geometry() == QRect(big.pos_of(4, 1), big.cell)

    # 다시 줄이면 같은 칸으로 돌아온다.
    _use_desktop(win, view(32, (76, 76), ((22, 2),), offset=(-22, 3)))
    small = nf.icon_grid(area)
    assert small.cell == QSize(76, 76) and small.icon == 32
    assert a.geometry() == QRect(small.pos_of(2, 3), small.cell)
    assert b.geometry() == QRect(small.pos_of(4, 1), small.cell)
    assert icon.geometry() == QRect(small.pos_of(5, 0), small.cell)
    _close(qapp, win)


def test_icons_avoid_real_desktop_icons_when_the_grid_arrives(qapp, tmp_path):
    win = _window(qapp, tmp_path, [_note("a")], [FOLDER], _on_cells)
    a, icon = win._stickies["a"], win._folder_icons["f1"]
    # 진짜 아이콘이 우리 아이콘의 칸 (2, 3) 과 (5, 0) 에 놓여 있다.
    _use_desktop(win, view(96, (124, 148),
                           ((14 + 124 * 2, 2 + 148 * 3), (14 + 124 * 5, 2))))
    grid = nf.icon_grid(_avail())
    for widget in (a, icon):
        cell = grid.cell_at(widget.pos())
        assert cell not in grid.taken
        assert grid.pos_of(*cell) == widget.pos()
    _close(qapp, win)


def test_size_changed_while_the_app_was_closed(qapp, tmp_path):
    # 지난번에 적어 둔 격자(76x100)와 지금 바탕화면(124x148)이 다르다.
    store_seed = _on_cells
    store = LocalStateStore(tmp_path / "local_state.json")
    store_seed(store.state)
    store.save()
    client = FakeClient()
    settings = AppSettings()
    settings.fold_animation = False
    settings.fold_style = "icon"
    win = MainWindow(client=client, local_state=store, role="host", settings=settings)
    _use_desktop(win, view(96, (124, 148), ((14, 2),)))
    client.connectionChanged.emit(True)
    client.welcomed.emit({"heartbeat_ms": 3000, "notes": [_note("a")], "locks": {},
                          "users": [ME], "folders": [FOLDER], "host": ME})
    qapp.processEvents()
    big = nf.icon_grid(_avail())
    assert win._stickies["a"].geometry() == QRect(big.pos_of(2, 3), big.cell)
    assert win._folder_icons["f1"].geometry() == QRect(big.pos_of(5, 0), big.cell)
    _close(qapp, win)


def test_aligned_icons_stay_put_on_the_next_start(qapp, tmp_path):
    win = _window(qapp, tmp_path, [_note("a")], [FOLDER], _legacy)
    spot, folder_spot = win._stickies["a"].pos(), win._folder_icons["f1"].pos()
    _close(qapp, win)
    again = _window(qapp, tmp_path, [_note("a")], [FOLDER])
    assert again._stickies["a"].pos() == spot
    assert again._folder_icons["f1"].pos() == folder_spot
    _close(qapp, again)


# ── 새 메모 폴더 추가 ───────────────────────────────────────────────────

def test_new_folder_from_the_desktop_menu_asks_for_a_name_in_place(
        qapp, tmp_path, monkeypatch):
    win = _window(qapp, tmp_path)
    area = _avail()
    cursor = area.topLeft() + QPoint(410, 330)
    monkeypatch.setattr(QCursor, "pos", staticmethod(lambda: cursor))
    win.handle_ipc_command(ipc.CMD_NEW_FOLDER_HERE)
    qapp.processEvents()

    made = win._client.folders_made
    assert len(made) == 1 and made[0]["name"] == "새 폴더"
    icon = win._folder_icons[made[0]["id"]]
    grid = nf.icon_grid(area)
    # 누른 자리가 든 칸에 아이콘이 놓이고, 메인 창은 뜨지 않는다.
    assert icon.isVisible() and icon.geometry().contains(cursor)
    assert grid.pos_of(*grid.cell_at(icon.pos())) == icon.pos()
    assert not win.isVisible()
    assert win._local.state.folder_icons[made[0]["id"]].opened is False
    # 바탕화면에서 새 폴더를 만들 때처럼 이름 전체가 골라진 입력 칸이 떠 있다.
    assert icon.is_renaming()
    edit = icon._edit
    assert edit.text() == "새 폴더" and edit.selectedText() == "새 폴더"
    assert icon.rect().contains(edit.geometry())
    assert edit.geometry().top() >= 2 + grid.icon

    # 곧바로 치면 골라져 있던 기본 이름이 새 이름으로 바뀐다(QTest 는 영문만 친다).
    QTest.keyClicks(edit, "Clients 2026")
    assert edit.text() == "Clients 2026"
    QTest.keyClick(edit, Qt.Key_Return)
    assert not icon.is_renaming()
    assert win._client.folder_updates == [(made[0]["id"],
                                           {"name": "Clients 2026", "color": None})]
    assert icon.toolTip() == "Clients 2026"
    _close(qapp, win)


def test_new_folder_names_do_not_collide_and_escape_keeps_the_default(
        qapp, tmp_path):
    win = _window(qapp, tmp_path)
    at = _avail().topLeft() + QPoint(300, 300)
    win.handle_new_folder_request(at)
    first = win._client.folders_made[0]
    icon = win._folder_icons[first["id"]]
    icon._edit.setText("쓰다 만 이름")
    QTest.keyClick(icon._edit, Qt.Key_Escape)
    assert not icon.is_renaming() and icon.toolTip() == "새 폴더"
    assert win._client.folder_updates == []

    # 같은 자리에서 한 번 더 만들면 이름도 칸도 겹치지 않는다.
    win.handle_new_folder_request(at)
    second = win._client.folders_made[1]
    assert second["name"] == "새 폴더 (2)"
    other = win._folder_icons[second["id"]]
    assert other.pos() != icon.pos()
    # 빈 이름으로 정하면 고치지 않는다.
    other._edit.clear()
    QTest.keyClick(other._edit, Qt.Key_Return)
    assert win._client.folder_updates == [] and other.toolTip() == "새 폴더 (2)"
    _close(qapp, win)


def test_new_folder_needs_the_shared_pc(qapp, tmp_path):
    win = _window(qapp, tmp_path)
    win._client.connectionChanged.emit(False)
    win.handle_new_folder_request(_avail().topLeft() + QPoint(300, 300))
    qapp.processEvents()
    assert win._client.folders_made == [] and win._folder_icons == {}
    assert win._new_folder_spots == {}
    assert win.isVisible()              # 까닭(연결 상태)이 보이는 메인 창을 띄운다
    _close(qapp, win)


def test_folder_made_elsewhere_gets_no_icon(qapp, tmp_path):
    win = _window(qapp, tmp_path)
    # 목록에서 만들었거나 다른 PC 가 만든 폴더는 아이콘이 생기지 않는다.
    win._client.folderCreated.emit({"id": "x", "name": "남이 만든 폴더",
                                    "color": "#BAE1FF", "private_owner": None})
    assert win._folder_icons == {} and "x" in win._folders
    _close(qapp, win)


def test_folder_icon_can_be_renamed_in_place(qapp, tmp_path):
    def seed(state):
        state.folder_icons["f1"] = FolderIconState(x=76 * 5, y=5, opened=False)
        state.icon_grids["0,0,800,800"] = [76.0, 100.0, 0.0, 5.0]

    win = _window(qapp, tmp_path, [], [FOLDER], seed)
    icon = win._folder_icons["f1"]
    QTest.keyClick(icon, Qt.Key_F2)
    assert icon.is_renaming() and icon._edit.text() == "업무"
    # 입력 칸 밖(아이콘 그림)을 누르면 그때까지 쓴 것으로 정한다. 바뀐 것이 없으면
    # 보내지 않는다.
    QTest.mouseClick(icon, Qt.LeftButton, Qt.NoModifier, QPoint(38, 20))
    assert not icon.is_renaming() and win._client.folder_updates == []
    QTest.keyClick(icon, Qt.Key_F2)
    icon._edit.setText("  업무   2팀 ")
    QTest.keyClick(icon._edit, Qt.Key_Enter)
    assert win._client.folder_updates == [("f1", {"name": "업무 2팀", "color": None})]
    # 같은 이름으로 정하면 보내지 않는다.
    icon.begin_rename()
    QTest.keyClick(icon._edit, Qt.Key_Return)
    assert len(win._client.folder_updates) == 1
    _close(qapp, win)


def test_rename_box_follows_the_icon_size(qapp):
    icon = FolderIconWidget("f", "업무", "#BAE1FF", settings=AppSettings())
    grid = nf.IconGrid(area=_avail(), cell=QSize(124, 148), icon=96.0,
                       x0=0.0, y0=5.0, dx=124.0, dy=148.0)
    nf.set_icon_grid_source(lambda area: grid)
    icon.place(124, 5)
    icon.show()
    icon.begin_rename()
    assert icon.size() == QSize(124, 148)
    box = icon._edit.geometry()
    assert box.top() >= 2 + 96 and box.bottom() <= 148 and box.width() >= 120
    icon._end_rename(False)
    icon.close()


# ── 아이콘을 폴더 아이콘에 끌어다 놓기 ──────────────────────────────────

def _drag(widget: StickyNoteWidget, to: QPoint, grab=QPoint(38, 26)) -> None:
    widget._slip_press = widget.mapToGlobal(grab)
    widget._slip_grab = QPoint(grab)
    widget._begin_slip_drag()
    widget._drag_slip_to(to)


def _drop(widget: StickyNoteWidget) -> None:
    widget._slip_press = None
    widget._slip_dragging = False
    widget._end_slip_drag()


def _two_folders(state) -> None:
    state.hides_new = True
    state.set_note_state("a", x=300, y=200, folded=True, fold_style="icon",
                         fold_x=76 * 2, fold_y=5 + 100 * 3, fold_at=True)
    state.folder_icons["f1"] = FolderIconState(x=76 * 5, y=5, opened=True)
    state.folder_icons["f2"] = FolderIconState(x=76 * 6, y=5, opened=False)
    state.icon_grids["0,0,800,800"] = [76.0, 100.0, 0.0, 5.0]


CLOSED = {"id": "f2", "name": "보관", "color": "#FFB3BA", "private_owner": None}


def test_dropping_a_note_icon_on_a_folder_icon_moves_it_into_the_folder(
        qapp, tmp_path):
    win = _window(qapp, tmp_path, [_note("a")], [FOLDER, CLOSED], _two_folders)
    a, opened, closed = win._stickies["a"], win._folder_icons["f1"], win._folder_icons["f2"]
    home = a.pos()

    # 폴더 아이콘 위를 지나는 동안 그 폴더만 받을 채비를 한다.
    _drag(a, opened.geometry().center())
    assert opened._drop_hover and not closed._drop_hover
    a._drag_slip_to(closed.geometry().center())
    assert closed._drop_hover and not opened._drop_hover
    a._drag_slip_to(opened.geometry().center())
    _drop(a)

    assert win._client.moves == [("a", "f1")]
    assert win._notes["a"]["folder_id"] == "f1"
    assert not opened._drop_hover and not closed._drop_hover
    # 열려 있는 폴더라 메모는 보이고, 아이콘은 끌기 전의 자리로 돌아간다.
    assert a.isVisible() and a.pos() == home and a.is_folded()
    assert win._local.state.notes["a"].fold_x == home.x()
    assert opened._count == 1
    _close(qapp, win)


def test_dropping_on_a_closed_folder_tucks_the_note_away(qapp, tmp_path):
    win = _window(qapp, tmp_path, [_note("a")], [FOLDER, CLOSED], _two_folders)
    a, closed = win._stickies["a"], win._folder_icons["f2"]
    _drag(a, closed.geometry().center())
    _drop(a)
    assert win._client.moves == [("a", "f2")]
    # 닫힌 폴더에 들어간 메모는 폴더를 열 때까지 바탕화면에서 사라진다.
    assert not a.isVisible() and closed._count == 1
    win._toggle_folder_icon("f2")
    assert a.isVisible()
    _close(qapp, win)


def test_dropping_beside_a_folder_just_moves_the_icon(qapp, tmp_path):
    win = _window(qapp, tmp_path, [_note("a")], [FOLDER, CLOSED], _two_folders)
    a = win._stickies["a"]
    grid = nf.icon_grid(_avail())
    _drag(a, grid.pos_of(3, 5) + QPoint(38, 26))
    _drop(a)
    assert win._client.moves == []
    assert a.pos() == grid.pos_of(3, 5)
    # 이미 그 폴더에 든 메모를 다시 놓아도 또 보내지 않는다.
    win._notes["a"]["folder_id"] = "f1"
    _drag(a, win._folder_icons["f1"].geometry().center())
    _drop(a)
    assert win._client.moves == [] and a.pos() == grid.pos_of(3, 5)
    _close(qapp, win)


def test_slips_are_not_dropped_into_folders(qapp, tmp_path):
    def seed(state):
        _two_folders(state)
        state.set_note_state("a", fold_style="slip")

    win = _window(qapp, tmp_path, [_note("a")], [FOLDER, CLOSED], seed)
    a, opened = win._stickies["a"], win._folder_icons["f1"]
    assert a.fold_style() == nf.FOLD_SLIP
    _drag(a, opened.geometry().center(), grab=QPoint(20, 12))
    assert not opened._drop_hover
    _drop(a)
    assert win._client.moves == []
    _close(qapp, win)
