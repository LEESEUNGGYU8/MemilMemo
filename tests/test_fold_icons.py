"""접힌 메모의 바탕화면 아이콘 모양과 폴더 아이콘 테스트(offscreen)."""
from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPixmap  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app import i18n  # noqa: E402
from app.local_state import FolderIconState, LocalStateStore  # noqa: E402
from app.settings import AppSettings  # noqa: E402
from app.ui import note_fold as nf  # noqa: E402
from app.ui.folder_icon import FolderIconWidget  # noqa: E402
from app.ui.sticky_note import StickyNoteWidget  # noqa: E402

SCREEN = QRect(0, 0, 1600, 900)
# 바탕화면을 읽지 않는 시험에서는 기본 격자(100% 배율의 기본 아이콘 크기)를 쓴다.
CELL = nf.ICON_CELL_DEFAULT


def _on_grid(pos: QPoint, area: QRect) -> bool:
    grid = nf.icon_grid(area)
    return grid.pos_of(*grid.cell_at(pos)) == pos


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    i18n.set_language("ko")
    yield app


def _settings(style: str = "slip") -> AppSettings:
    s = AppSettings()
    s.fold_animation = False
    s.fold_style = style
    return s


def _sticky(qapp, settings, **over) -> StickyNoteWidget:
    note = {"id": "n1", "version": 1, "color": "#FFE066", "width": 280,
            "height": 260, "content": "주간 회의 메모\n견적 회신"}
    note.update(over)
    w = StickyNoteWidget(note, "지우", settings=settings)
    w.apply_local_state(300, 200)
    w.show()
    qapp.processEvents()
    return w


def _dclick(w, pos: QPoint) -> None:
    # 위젯에 직접 보내는 mouseDClick 은 첫 누름을 보내지 않으므로 함께 보낸다.
    QTest.mousePress(w, Qt.LeftButton, Qt.NoModifier, pos)
    QTest.mouseRelease(w, Qt.LeftButton, Qt.NoModifier, pos)
    QTest.mouseDClick(w, Qt.LeftButton, Qt.NoModifier, pos)


# ── 격자 자리 ───────────────────────────────────────────────────────────

def test_snap_icon_pos_uses_grid_and_avoids_taken_cells():
    gw, gh = CELL.width(), CELL.height()
    origin = nf.icon_grid(SCREEN).pos_of(0, 0)
    got = nf.snap_icon_pos(QPoint(origin.x() + gw * 2 + 20,
                                  origin.y() + gh + 30), SCREEN, [])
    assert got == QPoint(origin.x() + gw * 2, origin.y() + gh)
    # 같은 칸이 차 있으면 가까운 빈 칸으로 비켜 놓는다.
    other = nf.snap_icon_pos(got + QPoint(3, 3), SCREEN, [got])
    assert other != got
    assert abs(other.x() - got.x()) <= gw and abs(other.y() - got.y()) <= gh
    assert (other.x() - origin.x()) % gw == 0
    assert (other.y() - origin.y()) % gh == 0
    # 화면 밖에 놓아도 화면 안 칸으로 들어온다.
    far = nf.snap_icon_pos(QPoint(99999, -500), SCREEN, [])
    assert SCREEN.contains(QRect(far, CELL))


def test_icon_faces_paint_and_keep_cell_hittable(qapp):
    img = QImage(CELL, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    cell = QRectF(0, 0, CELL.width(), CELL.height())
    nf.paint_icon(p, cell, "#FFE066", "주간 회의 메모", font=qapp.font())
    p.end()
    glyph = nf.icon_glyph_rect(cell).center().toPoint()
    assert img.pixelColor(glyph).alpha() == 255
    # 그림 둘레의 빈 곳도 완전히 투명하지 않아야 마우스가 뒤 창으로 빠지지 않는다.
    assert img.pixelColor(8, 30).alpha() >= 1

    # 마우스를 올리면 진짜 바탕화면 아이콘처럼 뒤에 옅은 판이 깔린다. 그림은
    # 그대로다. 고르면 판이 더 짙어진다.
    plain = img.pixelColor(glyph)
    img.fill(Qt.transparent)
    p = QPainter(img)
    nf.paint_icon(p, cell, "#FFE066", "주간 회의 메모", hover=True,
                  font=qapp.font())
    p.end()
    hot = img.pixelColor(8, 30).alpha()
    assert hot > 2
    assert img.pixelColor(glyph) == plain
    # 판은 이름의 끝까지만이다. 칸의 맨 아래는 비어 있다.
    assert img.pixelColor(8, CELL.height() - 2).alpha() <= 2

    img.fill(Qt.transparent)
    p = QPainter(img)
    nf.paint_icon(p, cell, "#FFE066", "주간 회의 메모", selected=True,
                  font=qapp.font())
    p.end()
    assert img.pixelColor(8, 30).alpha() > hot

    img.fill(Qt.transparent)
    p = QPainter(img)
    nf.paint_folder_icon(p, cell, "#BAE1FF", "업무", opened=True, count=128,
                         font=qapp.font())
    p.end()
    assert img.pixelColor(glyph).alpha() == 255


def test_fold_frames_end_as_icon(qapp):
    pix = QPixmap(280, 260)
    pix.fill(QColor("#FFE066"))
    frames = nf.FoldFrames(pix, CELL, color="#FFE066", title="메모",
                           font=qapp.font(), style=nf.FOLD_ICON)
    want = QImage(280, 260, QImage.Format_ARGB32_Premultiplied)
    want.fill(Qt.transparent)
    p = QPainter(want)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    nf.paint_icon(p, frames.slip_rect(), "#FFE066", "메모", font=qapp.font())
    p.end()
    got = QImage(280, 260, QImage.Format_ARGB32_Premultiplied)
    got.fill(Qt.transparent)
    p = QPainter(got)
    for t in (0.2, 0.5, 0.75, 0.9):
        frames.paint(p, t)
    p.end()
    got.fill(Qt.transparent)
    p = QPainter(got)
    frames.paint(p, 1.0)
    p.end()
    assert got == want
    # 붙인 메모는 모양 설정과 무관하게 탭으로 접힌다.
    docked = nf.FoldFrames(pix, nf.TAB_SIZE, color="#FFE066", title="메모",
                           dock=nf.DOCK_LEFT, font=qapp.font(),
                           style=nf.FOLD_ICON)
    assert docked.style == nf.FOLD_SLIP


# ── 메모 창 ─────────────────────────────────────────────────────────────

def test_icon_style_folds_to_icon_and_opens_on_double_click(qapp):
    w = _sticky(qapp, _settings("icon"))
    events: list[tuple] = []
    w.foldChanged.connect(lambda *a: events.append(a))
    open_geo = QRect(w.geometry())
    avail = QGuiApplication.primaryScreen().availableGeometry()
    w.fold()
    assert w.is_folded() and w.fold_style() == nf.FOLD_ICON
    # 아이콘은 펼쳐 있던 곳에서 가장 가까운 칸에 놓인다.
    spot = nf.snap_icon_pos(open_geo.topLeft(), avail)
    assert w.geometry() == QRect(spot, CELL)
    assert abs(spot.x() - open_geo.x()) <= CELL.width() // 2
    assert abs(spot.y() - open_geo.y()) <= CELL.height() // 2
    assert w.toolTip() == "주간 회의 메모"
    # 설정의 기본 모양을 따랐을 뿐이라 메모에는 모양이 기록되지 않는다.
    assert events[-1][0] == "n1"
    assert events[-1][1]["folded"] is True and events[-1][1]["fold_style"] == ""

    # 아이콘은 한 번 눌러서는 펴지지 않고 두 번 눌러야 펴진다.
    QTest.mouseClick(w, Qt.LeftButton, Qt.NoModifier, QPoint(40, 30))
    assert w.is_folded()
    _dclick(w, QPoint(40, 30))
    assert not w.is_folded() and w.geometry() == open_geo
    assert w.toolTip() == ""
    w._teardown = True
    w.close()


def test_style_can_be_switched_per_note(qapp):
    settings = _settings("slip")
    w = _sticky(qapp, settings)
    events: list[tuple] = []
    w.foldChanged.connect(lambda *a: events.append(a))
    w.fold(style=nf.FOLD_ICON)
    assert w.size() == CELL
    assert events[-1][1]["fold_style"] == nf.FOLD_ICON
    w.set_fold_style(nf.FOLD_SLIP)
    assert w.size() == nf.SLIP_SIZE
    assert events[-1][1]["fold_style"] == nf.FOLD_SLIP
    assert w.is_folded()
    # 쪽지는 한 번 누르면 펴진다.
    QTest.mouseClick(w, Qt.LeftButton, Qt.NoModifier, QPoint(20, 20))
    assert not w.is_folded()
    # 다음에 접으면 이 메모는 고른 모양(쪽지)으로 접힌다. 설정이 바뀌어도 그대로다.
    settings.fold_style = "icon"
    w.fold()
    assert w.size() == nf.SLIP_SIZE
    w._teardown = True
    w.close()


def test_folded_note_follows_a_changed_default_shape(qapp):
    settings = _settings("slip")
    w = _sticky(qapp, settings)
    w.fold()
    at = w.pos()
    assert w.size() == nf.SLIP_SIZE
    settings.fold_style = "icon"
    w.refresh_fold_face()
    avail = QGuiApplication.primaryScreen().availableGeometry()
    # 아이콘이 되면서 가까운 칸에 맞춰진다.
    assert w.size() == CELL and w.pos() == nf.snap_icon_pos(at, avail)
    assert w.toolTip() == "주간 회의 메모"
    settings.fold_style = "slip"
    w.refresh_fold_face()
    assert w.size() == nf.SLIP_SIZE and w.toolTip() == ""
    w._teardown = True
    w.close()


def test_a_shape_chosen_for_one_note_ignores_the_default(qapp):
    settings = _settings("slip")
    w = _sticky(qapp, settings)
    w.fold(style=nf.FOLD_SLIP)
    settings.fold_style = "icon"
    w.refresh_fold_face()
    assert w.size() == nf.SLIP_SIZE
    # 벽에 붙인 메모는 늘 탭이다.
    docked = _sticky(qapp, settings, id="n2")
    docked.fold(dock=nf.DOCK_LEFT)
    tab = docked.size()
    docked.refresh_fold_face()
    assert docked.size() == tab
    for x in (w, docked):
        x._teardown = True
        x.close()


def test_dragged_icon_snaps_to_grid_without_docking(qapp):
    w = _sticky(qapp, _settings("icon"))
    avail = QGuiApplication.primaryScreen().availableGeometry()
    taken = [nf.snap_icon_pos(QPoint(avail.left() + 20, 330), avail, [])]
    w.set_icon_resolver(lambda key, pos, screen: nf.snap_icon_pos(
        pos, screen, taken))
    w.fold()
    w._slip_press = w.mapToGlobal(QPoint(30, 30))
    w._slip_grab = QPoint(30, 30)
    w._begin_slip_drag()
    # 벽에 닿아 있는 첫 칸까지 끌어도(창의 끝이 벽에 닿는다) 아이콘으로 남는다.
    w._drag_slip_to(QPoint(avail.left() + 30, 360))
    assert w.geometry().left() == avail.left()
    assert w._drag_dock == nf.DOCK_NONE
    assert w.size() == CELL
    w._slip_press = None
    w._slip_dragging = False
    w._end_slip_drag()
    assert w.dock_side() == nf.DOCK_NONE and w.is_folded()
    pos = w.pos()
    assert pos != taken[0]
    assert _on_grid(pos, avail)
    # 펴면 펼쳐 있던 자리로 돌아가고, 접으면 아이콘을 둔 자리로 온다.
    w.unfold()
    assert w.pos() == QPoint(300, 200)
    w.fold()
    assert w.pos() == pos and w.size() == CELL
    w._teardown = True
    w.close()


def test_icon_pushed_into_a_wall_becomes_a_tab(qapp):
    w = _sticky(qapp, _settings("icon"))
    avail = QGuiApplication.primaryScreen().availableGeometry()
    w.fold()
    w._slip_press = w.mapToGlobal(QPoint(30, 30))
    w._slip_grab = QPoint(30, 30)
    w._begin_slip_drag()
    # 그림이 벽에 닿기 전에는 아이콘이다(첫 칸에 놓으려는 것일 수 있다).
    w._drag_slip_to(QPoint(avail.left() + 20, 360))
    assert w._drag_dock == nf.DOCK_NONE
    # 그림이 벽을 넘도록 밀면, 아이콘 설정과 무관하게 쪽지 모양으로 붙는다.
    w._drag_slip_to(QPoint(avail.left() + 8, 360))
    assert w._drag_dock == nf.DOCK_LEFT
    assert w.size() == nf.SLIP_SIZE and w.geometry().left() == avail.left()
    w._slip_press = None
    w._slip_dragging = False
    w._end_slip_drag()
    assert w.dock_side() == nf.DOCK_LEFT and w.size() == nf.TAB_SIZE
    assert w.fold_style() == nf.FOLD_SLIP
    # 벽에서 떼어 내면 다시 아이콘이다.
    w._slip_press = w.mapToGlobal(QPoint(20, 20))
    w._slip_grab = QPoint(20, 20)
    w._begin_slip_drag()
    # 붙어 있던 탭은 벽을 따라 끄는 동안 탭으로 남는다.
    w._drag_slip_to(QPoint(avail.left() + 22, 420))
    assert w._drag_dock == nf.DOCK_LEFT
    w._drag_slip_to(QPoint(avail.left() + 300, 360))
    assert w._drag_dock == nf.DOCK_NONE and w.size() == CELL
    w._slip_press = None
    w._slip_dragging = False
    w._end_slip_drag()
    assert w.dock_side() == nf.DOCK_NONE and w.fold_style() == nf.FOLD_ICON
    w._teardown = True
    w.close()


def test_icon_at_the_screen_edge_does_not_drift(qapp):
    # 화면 오른쪽 끝 칸에 둔 아이콘을 펴고 접어도 아이콘은 제자리다.
    w = _sticky(qapp, _settings("icon"))
    avail = QGuiApplication.primaryScreen().availableGeometry()
    w.fold()
    w._slip_press = w.mapToGlobal(QPoint(30, 30))
    w._slip_grab = QPoint(30, 30)
    w._begin_slip_drag()
    w._drag_slip_to(QPoint(avail.right() - 70, 300))
    assert w._drag_dock == nf.DOCK_NONE
    w._slip_press = None
    w._slip_dragging = False
    w._end_slip_drag()
    spot = w.pos()
    assert spot.x() + 280 > avail.right()     # 여기서 그대로 펴면 화면을 넘는다
    for _ in range(3):
        w.unfold()
        assert w.geometry() == QRect(300, 200, 280, 260)
        w.fold()
        assert w.pos() == spot
    w._teardown = True
    w.close()


def test_docking_an_icon_note_uses_a_tab_and_returns_to_icon(qapp):
    w = _sticky(qapp, _settings("icon"))
    avail = QGuiApplication.primaryScreen().availableGeometry()
    w.dock(nf.DOCK_LEFT)
    assert w.is_folded() and w.dock_side() == nf.DOCK_LEFT
    assert w.size() == nf.TAB_SIZE and w.geometry().left() == avail.left()
    assert w.fold_style() == nf.FOLD_SLIP      # 붙인 동안은 탭이다.
    w.undock()
    assert w.dock_side() == nf.DOCK_NONE and w.size() == CELL
    # 벽에서 뗀 아이콘도 바탕화면 아이콘의 칸에 놓인다.
    assert _on_grid(w.pos(), avail)
    w._teardown = True
    w.close()


def test_restore_icon_style(qapp):
    w = StickyNoteWidget({"id": "r", "version": 1, "color": "#BAE1FF",
                          "width": 280, "height": 260, "content": "복원"},
                         "지우", settings=_settings("slip"))
    w.apply_local_state(410, 222, 300, 240, folded=True, fold_style="icon")
    assert w.geometry() == QRect(QPoint(410, 222), CELL)
    assert w.fold_style() == nf.FOLD_ICON
    w.show()
    w.unfold()
    assert w.geometry() == QRect(410, 222, 300, 240)
    w._teardown = True
    w.close()


def test_fade_to_shows_and_hides(qapp):
    w = _sticky(qapp, _settings())
    w.fade_to(False)
    assert not w.isVisible()
    w.fade_to(True)
    assert w.isVisible() and w.windowOpacity() >= 0.99
    w._teardown = True
    w.close()


# ── 폴더 아이콘 ─────────────────────────────────────────────────────────

def test_folder_icon_widget_signals(qapp):
    icon = FolderIconWidget("f1", "업무", "#BAE1FF", settings=_settings())
    icon.place(500, 300)
    icon.show()
    qapp.processEvents()
    toggled: list[str] = []
    opened: list[str] = []
    moved: list[tuple] = []
    icon.toggled.connect(toggled.append)
    icon.opened.connect(opened.append)
    icon.moved.connect(lambda *a: moved.append(a))
    assert icon.size() == CELL and icon.toolTip() == "업무"
    QTest.mouseClick(icon, Qt.LeftButton, Qt.NoModifier, QPoint(40, 30))
    assert opened == []                       # 한 번 눌러서는 열리지 않는다.
    _dclick(icon, QPoint(40, 30))
    # 두 번 누르면 폴더 창이 열린다. 바탕화면에 펼치는 것은 메뉴에서 한다.
    assert opened == ["f1"] and toggled == []
    icon.set_state(opened=True, count=3)
    assert icon.is_opened()

    # 끌어다 놓으면 격자에 맞춰지고 그 자리를 알린다.
    start = icon.mapToGlobal(QPoint(40, 30))
    icon._press = start
    icon._grab = QPoint(40, 30)
    icon._dragging = True
    icon.move(700, 420)
    QTest.mouseRelease(icon, Qt.LeftButton, Qt.NoModifier, QPoint(40, 30))
    assert moved and moved[-1][0] == "f1"
    avail = QGuiApplication.primaryScreen().availableGeometry()
    assert _on_grid(QPoint(moved[-1][1], moved[-1][2]), avail)
    icon.close()


def test_local_state_saves_folder_icons_and_fold_style(tmp_path):
    path = tmp_path / "local_state.json"
    store = LocalStateStore(path)
    store.state.set_note_state("a", x=1, y=2, fold_style="icon")
    store.state.folder_icons["f1"] = FolderIconState(x=900, y=40, opened=True)
    store.save()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert set(raw["notes"]["a"]) == {"x", "y", "hidden", "w", "h"}
    assert raw["note_folds"]["a"]["fold_style"] == "icon"
    assert raw["folder_icons"] == {"f1": {"x": 900, "y": 40, "opened": True}}
    again = LocalStateStore(path).state
    assert again.notes["a"].fold_style == "icon"
    assert again.folder_icons["f1"] == FolderIconState(x=900, y=40, opened=True)

    # 이상한 값이 섞여도 파일 전체를 버리지 않는다.
    raw["folder_icons"] = {"ok": {"x": 5, "y": 6}, "bad": "x",
                           "worse": {"x": "가"}}
    raw["note_folds"]["a"]["fold_style"] = "별"
    path.write_text(json.dumps(raw), encoding="utf-8")
    odd = LocalStateStore(path).state
    assert odd.folder_icons == {"ok": FolderIconState(x=5, y=6, opened=False)}
    assert odd.notes["a"].fold_style == ""


def test_settings_fold_style_round_trip(tmp_path):
    path = tmp_path / "settings.json"
    s = AppSettings()
    assert s.fold_style == "slip"
    s.fold_style = "icon"
    s.save(path)
    assert AppSettings.load(path).fold_style == "icon"
    path.write_text('{"fold_style": "모름"}', encoding="utf-8")
    assert AppSettings.load(path).fold_style == "slip"
