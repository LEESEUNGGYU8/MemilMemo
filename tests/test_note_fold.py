"""메모 접기 · 가장자리 붙이기 테스트. 화면 없이 offscreen 플랫폼으로 돈다.

자리 계산과 그림은 순수 함수로, 창 동작은 움직임을 끈 설정으로 확인한다
(움직임을 켠 경로는 끝 상태만 따로 본다)."""
from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPixmap  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app import config, i18n  # noqa: E402
from app.local_state import LocalStateStore, NoteLocalState  # noqa: E402
from app.settings import AppSettings  # noqa: E402
from app.ui import note_fold as nf  # noqa: E402
from app.ui.sticky_note import StickyNoteWidget  # noqa: E402

SCREEN = QRect(0, 0, 1600, 900)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    i18n.set_language("ko")
    yield app


def _note(**over) -> dict:
    note = {
        "id": "n1", "version": 1, "color": "#FFE066", "width": 280,
        "height": 260, "content": "주간 회의 메모\n견적 회신\n재고 실사",
        "private_owner": None,
    }
    note.update(over)
    return note


def _sticky(qapp, *, animate: bool = False, **over) -> StickyNoteWidget:
    settings = AppSettings()
    settings.fold_animation = animate
    w = StickyNoteWidget(_note(**over), "지우", settings=settings)
    w.apply_local_state(300, 200)
    w.show()
    qapp.processEvents()
    return w


def _avail() -> QRect:
    return QGuiApplication.primaryScreen().availableGeometry()


# ── 자리 계산 ───────────────────────────────────────────────────────────

def _one(screen: QRect = SCREEN) -> list[tuple[QRect, QRect]]:
    return [(QRect(screen), QRect(screen))]


def test_wall_for_drag_finds_all_four_walls():
    slip = nf.SLIP_SIZE
    screens = _one()

    def wall(pos: QPoint) -> str:
        rect = QRect(pos, slip)
        return nf.wall_for_drag(rect, rect.center(), screens)[0]

    assert wall(QPoint(3, 300)) == nf.DOCK_LEFT
    assert wall(QPoint(-40, 300)) == nf.DOCK_LEFT
    assert wall(QPoint(SCREEN.right() - slip.width() - 2, 300)) == nf.DOCK_RIGHT
    assert wall(QPoint(600, 2)) == nf.DOCK_TOP
    assert wall(QPoint(600, SCREEN.bottom() - slip.height() - 1)) == nf.DOCK_BOTTOM
    assert wall(QPoint(600, 300)) == nf.DOCK_NONE
    # 아이콘 격자의 첫 칸(벽에서 10)은 붙지 않는다. 벽 옆에도 아이콘을 둘 수 있다.
    assert wall(QPoint(nf.ICON_GRID_ORIGIN.x(), 300)) == nf.DOCK_NONE
    # 모서리에서는 더 깊이 들어간 쪽이 이긴다.
    assert wall(QPoint(-30, -5)) == nf.DOCK_LEFT
    assert wall(QPoint(-5, -30)) == nf.DOCK_TOP


def test_wall_for_drag_uses_the_taskbar_edge():
    # 작업 표시줄이 아래에 있으면 그 위쪽 끝이 아래 벽이다.
    geo = QRect(0, 0, 1600, 900)
    avail = QRect(0, 0, 1600, 852)
    cursor = QPoint(700, 850)
    side, index = nf.wall_for_drag(QRect(cursor, QSize(1, 1)), cursor,
                                   [(geo, avail)], nf.EDGE_PUSH)
    assert (side, index) == (nf.DOCK_BOTTOM, 0)
    inside = QPoint(700, 800)
    assert nf.wall_for_drag(QRect(inside, QSize(1, 1)), inside,
                            [(geo, avail)], nf.EDGE_PUSH)[0] == nf.DOCK_NONE


def test_seams_between_monitors_take_tabs_when_the_cursor_is_close():
    # 큰 화면 오른쪽에 작은 화면이 위를 맞춰 붙어 있다.
    big = QRect(0, 0, 1920, 1080)
    small = QRect(1920, 0, 1280, 720)
    screens = [(big, QRect(0, 0, 1920, 1040)), (small, QRect(small))]

    def push(x: int, y: int) -> tuple[str, int]:
        cursor = QPoint(x, y)
        return nf.wall_for_drag(QRect(cursor, QSize(1, 1)), cursor, screens,
                                nf.EDGE_PUSH)

    # 두 화면이 맞닿은 경계: 커서가 있는 쪽 화면의 끝에 붙는다.
    assert push(1918, 300) == (nf.DOCK_RIGHT, 0)
    assert push(1921, 300) == (nf.DOCK_LEFT, 1)
    edge = nf.SEAM_DISTANCE
    assert push(1919 - edge, 300) == (nf.DOCK_RIGHT, 0)
    assert push(1920 + edge, 300) == (nf.DOCK_LEFT, 1)
    # 경계에서 조금만 떨어져도 그냥 옮기는 것이다.
    assert push(1919 - edge - 1, 300) == (nf.DOCK_NONE, 0)
    assert push(1920 + edge + 1, 300) == (nf.DOCK_NONE, 1)
    # 작은 화면이 닿지 않는 아래쪽 구간은 큰 화면의 바깥벽이다(커서를 끝까지 민다).
    assert push(1918, 900) == (nf.DOCK_RIGHT, 0)
    assert push(1919 - edge, 900) == (nf.DOCK_NONE, 0)
    # 바깥쪽 끝은 그 화면의 벽이다.
    assert push(3198, 300) == (nf.DOCK_RIGHT, 1)
    assert push(2500, 718) == (nf.DOCK_BOTTOM, 1)
    assert push(2500, 1) == (nf.DOCK_TOP, 1)
    assert push(1, 500) == (nf.DOCK_LEFT, 0)
    assert push(8, 500) == (nf.DOCK_NONE, 0)
    assert nf.is_outer_wall(nf.DOCK_RIGHT, 0, screens, 300) is False
    assert nf.is_outer_wall(nf.DOCK_RIGHT, 0, screens, 900) is True
    assert nf.is_outer_wall(nf.DOCK_LEFT, 1, screens, 300) is False

    # 접힌 쪽지를 옆 화면으로 넘길 때: 쪽지의 끝이 경계를 넘어도 커서가 멀면
    # 걸리지 않고 지나간다. 커서가 경계에 오면 붙는다.
    slip = QRect(QPoint(1800, 300), nf.SLIP_SIZE)
    assert slip.right() > big.right()
    assert nf.wall_for_drag(slip, QPoint(1820, 320), screens)[0] == nf.DOCK_NONE
    assert nf.wall_for_drag(slip, QPoint(1912, 320), screens) == (nf.DOCK_RIGHT, 0)
    over = QRect(QPoint(1900, 300), nf.SLIP_SIZE)
    assert nf.wall_for_drag(over, QPoint(1990, 320), screens)[0] == nf.DOCK_NONE
    assert nf.wall_for_drag(over, QPoint(1925, 320), screens) == (nf.DOCK_LEFT, 1)
    # 바깥벽은 전처럼 쪽지의 끝이 닿으면 붙는다.
    assert nf.wall_for_drag(QRect(QPoint(3, 300), nf.SLIP_SIZE),
                            QPoint(90, 320), screens) == (nf.DOCK_LEFT, 0)

    # 위아래로 쌓은 화면: 맞닿은 위아래 끝도 같다.
    top = QRect(0, -1080, 1920, 1080)
    stacked = [(big, QRect(big)), (top, QRect(top))]
    assert nf.is_outer_wall(nf.DOCK_TOP, 0, stacked, 800) is False
    assert nf.is_outer_wall(nf.DOCK_BOTTOM, 1, stacked, 800) is False
    assert nf.is_outer_wall(nf.DOCK_TOP, 1, stacked, 800) is True

    def push2(x: int, y: int) -> tuple[str, int]:
        cursor = QPoint(x, y)
        return nf.wall_for_drag(QRect(cursor, QSize(1, 1)), cursor, stacked,
                                nf.EDGE_PUSH)

    assert push2(800, 3) == (nf.DOCK_TOP, 0)
    assert push2(800, -3) == (nf.DOCK_BOTTOM, 1)
    assert push2(800, 40) == (nf.DOCK_NONE, 0)
    # 어느 화면에도 없는 점은 가장 가까운 화면으로 친다.
    assert nf.screen_index_at(QPoint(5000, 300), screens) == 1
    assert nf.screen_index_at(QPoint(-50, 300), screens) == 0


def test_free_dock_slot_avoids_other_tabs():
    h = nf.TAB_SIZE.height()
    assert nf.free_dock_slot(100, h, SCREEN, []) == 100
    # 100~140 이 차 있으면 그 아래 빈 자리로 밀린다.
    got = nf.free_dock_slot(110, h, SCREEN, [(100, 100 + h)])
    assert got == 100 + h + nf.TAB_GAP
    # 아래가 화면 끝이면 위로 올려 찾는다.
    bottom = SCREEN.bottom() - h - 8
    got = nf.free_dock_slot(bottom, h, SCREEN, [(bottom, bottom + h)])
    assert got == bottom - nf.TAB_GAP - h
    # 화면 밖 값은 화면 안으로 들어온다.
    assert nf.free_dock_slot(-500, h, SCREEN, []) == SCREEN.top() + 8


def test_tab_rect_on_each_wall():
    left = nf.tab_rect(nf.DOCK_LEFT, 300, SCREEN)
    assert left == QRect(0, 300, nf.TAB_SIZE.width(), nf.TAB_SIZE.height())
    right = nf.tab_rect(nf.DOCK_RIGHT, 300, SCREEN)
    assert right.right() == SCREEN.right() and right.size() == nf.TAB_SIZE
    top = nf.tab_rect(nf.DOCK_TOP, 500, SCREEN)
    assert top == QRect(500, 0, nf.HTAB_SIZE.width(), nf.HTAB_SIZE.height())
    bottom = nf.tab_rect(nf.DOCK_BOTTOM, 500, SCREEN)
    assert bottom.bottom() == SCREEN.bottom() and bottom.size() == nf.HTAB_SIZE
    # 마우스를 올리면 좌우 탭은 길어지고, 위아래 탭은 벽에서 더 나온다.
    wide = nf.tab_rect(nf.DOCK_RIGHT, 300, SCREEN, expanded=True, wide=200)
    assert wide.width() == 200 and wide.right() == SCREEN.right()
    grown = nf.tab_rect(nf.DOCK_BOTTOM, 500, SCREEN, expanded=True)
    assert grown.height() == nf.HTAB_SIZE.height() + nf.HTAB_GROW
    assert grown.bottom() == SCREEN.bottom()
    assert nf.tab_length(nf.DOCK_LEFT) == nf.TAB_SIZE.height()
    assert nf.tab_length(nf.DOCK_TOP) == nf.HTAB_SIZE.width()


def test_top_tabs_leave_the_corners_to_side_tabs():
    length = nf.tab_length(nf.DOCK_TOP)
    first = nf.clamp_dock_y(-500, length, SCREEN, nf.DOCK_TOP)
    last = nf.clamp_dock_y(99999, length, SCREEN, nf.DOCK_TOP)
    side_tab = nf.tab_rect(nf.DOCK_LEFT, SCREEN.top() + 8, SCREEN, expanded=False)
    assert not nf.tab_rect(nf.DOCK_TOP, first, SCREEN,
                           expanded=True).intersects(side_tab)
    assert nf.tab_rect(nf.DOCK_TOP, last, SCREEN).right() < SCREEN.right() - 46
    # 같은 벽의 탭끼리는 벽을 따라 비켜 놓인다.
    got = nf.free_dock_slot(400, length, SCREEN, [(380, 380 + length)],
                            nf.DOCK_TOP)
    assert got == 380 + length + nf.TAB_GAP


def test_snap_to_wall_and_keep_on_screen():
    slip = QRect(QPoint(-30, 870), nf.SLIP_SIZE)
    left = nf.snap_to_wall(slip, nf.DOCK_LEFT, SCREEN)
    assert left.left() == 0 and left.bottom() <= SCREEN.bottom()
    top = nf.snap_to_wall(QRect(QPoint(1500, -9), nf.SLIP_SIZE), nf.DOCK_TOP,
                          SCREEN)
    assert top.top() == 0 and top.right() <= SCREEN.right()
    kept = nf.keep_on_screen(QRect(1500, 800, 280, 260), SCREEN)
    assert kept.right() == SCREEN.right() and kept.bottom() == SCREEN.bottom()
    same = QRect(100, 100, 280, 260)
    assert nf.keep_on_screen(same, SCREEN) == same


# ── 접는 그림 ───────────────────────────────────────────────────────────

def _render(frames: nf.FoldFrames, t: float) -> QImage:
    img = QImage(frames.W, frames.H, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    frames.paint(p, t)
    p.end()
    return img


@pytest.mark.parametrize("anchor,dock", [
    (nf.DOCK_LEFT, nf.DOCK_NONE),
    (nf.DOCK_LEFT, nf.DOCK_LEFT),
    (nf.DOCK_RIGHT, nf.DOCK_RIGHT),
    (nf.DOCK_LEFT, nf.DOCK_TOP),
    (nf.DOCK_LEFT, nf.DOCK_BOTTOM),
])
def test_fold_frames_start_open_and_end_as_slip(qapp, anchor, dock):
    pix = QPixmap(280, 260)
    pix.fill(QColor("#FFE066"))
    size = nf.tab_size(dock) if dock else nf.SLIP_SIZE
    frames = nf.FoldFrames(pix, size, color="#FFE066", title="주간 회의",
                           anchor=anchor, dock=dock, font=qapp.font())
    first = _render(frames, 0.0)
    assert first.pixelColor(270, 250) == QColor("#FFE066")
    # 중간 프레임들은 예외 없이 그려지고, 접힐수록 아래쪽이 비어 간다.
    for t in (0.1, 0.3, 0.5, 0.7, 0.9):
        _render(frames, t)
    last = _render(frames, 1.0)
    assert last.pixelColor(140, 250).alpha() == 0
    # 끝 프레임은 접힌 쪽지를 그리는 그림과 똑같다(끝에서 튀지 않는다).
    want = QImage(frames.W, frames.H, QImage.Format_ARGB32_Premultiplied)
    want.fill(Qt.transparent)
    p = QPainter(want)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    nf.paint_slip(p, frames.slip_rect(), "#FFE066", "주간 회의", dock=dock,
                  font=qapp.font())
    p.end()
    assert last == want
    slip = frames.slip_rect()
    assert slip.size() == QRectF(0, 0, size.width(), size.height()).size()
    if anchor == nf.DOCK_RIGHT:
        assert slip.right() == frames.W
    else:
        assert slip.left() == 0


def test_tab_width_grows_with_title(qapp):
    short = nf.tab_width_for("가", qapp.font())
    long = nf.tab_width_for("아주 긴 제목의 메모입니다 정말로 길어요 끝이 없어요",
                            qapp.font())
    assert short >= nf.TAB_SIZE.width()
    assert short < long <= nf.TAB_MAX_WIDTH


# ── 로컬 상태 저장 ──────────────────────────────────────────────────────

def test_local_state_keeps_fold_in_separate_key(tmp_path):
    path = tmp_path / "local_state.json"
    store = LocalStateStore(path)
    store.state.nickname = "지우"
    store.state.set_note_state("a", x=10, y=20, w=300, h=240)
    store.state.set_note_state("b", x=30, y=40, folded=True, dock="right",
                               dock_y=420)
    store.save()

    raw = json.loads(path.read_text(encoding="utf-8"))
    # 옛 버전(1.0.6 이하)은 notes 항목을 그대로 생성자에 넘긴다. 모르는 키가
    # 섞이면 파일 전체를 버리므로, notes 에는 옛 다섯 값만 있어야 한다.
    for entry in raw["notes"].values():
        assert set(entry) == {"x", "y", "hidden", "w", "h"}
    assert raw["note_folds"] == {
        "b": {"folded": True, "dock": "right", "dock_y": 420,
              "fold_style": "", "fold_x": 0, "fold_y": 0, "fold_at": False}}

    again = LocalStateStore(path).state
    assert again.nickname == "지우"
    assert again.notes["a"] == NoteLocalState(x=10, y=20, w=300, h=240)
    b = again.notes["b"]
    assert (b.folded, b.dock, b.dock_y) == (True, "right", 420)


def test_local_state_reads_old_and_odd_files(tmp_path):
    path = tmp_path / "local_state.json"
    path.write_text(json.dumps({
        "nickname": "서연",
        "notes": {
            "old": {"x": 1, "y": 2, "hidden": True, "w": 0, "h": 0},
            "odd": {"x": 5, "y": 6, "future_key": 1},
        },
        "note_folds": {
            "odd": {"folded": 1, "dock": "middle", "dock_y": "x",
                    "fold_x": "?", "fold_at": 1},
            "ghost": {"folded": True},
        },
    }), encoding="utf-8")
    state = LocalStateStore(path).state
    assert state.nickname == "서연"
    assert state.notes["old"].hidden is True
    assert state.notes["old"].folded is False
    odd = state.notes["odd"]
    # 모르는 키와 잘못된 값 때문에 파일을 버리지 않는다.
    assert (odd.x, odd.y, odd.folded, odd.dock, odd.dock_y) == (5, 6, True, "", 0)
    assert (odd.fold_x, odd.fold_y, odd.fold_at) == (0, 0, True)
    assert "ghost" not in state.notes


def test_local_state_keeps_the_folded_place_apart_from_the_open_place(tmp_path):
    path = tmp_path / "local_state.json"
    store = LocalStateStore(path)
    store.state.set_note_state("a", x=400, y=300, folded=False, dock="top",
                               dock_y=640, fold_x=640, fold_y=0, fold_at=True)
    # 펼쳐 둔 메모도 접힌 메모를 둔 자리는 남는다.
    store.state.set_note_state("b", x=10, y=20, fold_x=1500, fold_y=40,
                               fold_at=True)
    store.save()
    again = LocalStateStore(path).state
    a, b = again.notes["a"], again.notes["b"]
    assert (a.x, a.y, a.dock, a.dock_y) == (400, 300, "top", 640)
    assert (a.fold_x, a.fold_y, a.fold_at) == (640, 0, True)
    assert (b.x, b.y, b.fold_x, b.fold_y, b.fold_at) == (10, 20, 1500, 40, True)
    assert b.folded is False


def test_local_state_reads_a_slip_left_by_an_older_version(tmp_path):
    # 1.0.1 이하는 접힌 쪽지를 둔 자리를 x·y 에 적었고 fold_at 은 없었다.
    path = tmp_path / "local_state.json"
    path.write_text(json.dumps({
        "notes": {"s": {"x": 1700, "y": 90, "hidden": False, "w": 0, "h": 0},
                  "t": {"x": 0, "y": 300, "hidden": False, "w": 0, "h": 0},
                  "o": {"x": 50, "y": 60, "hidden": False, "w": 0, "h": 0}},
        "note_folds": {
            "s": {"folded": True, "dock": "", "dock_y": 0, "fold_style": "icon"},
            "t": {"folded": True, "dock": "left", "dock_y": 300, "fold_style": ""},
            "o": {"folded": False, "dock": "", "dock_y": 0, "fold_style": "icon"},
        },
    }), encoding="utf-8")
    state = LocalStateStore(path).state
    s, t, o = state.notes["s"], state.notes["t"], state.notes["o"]
    assert (s.fold_x, s.fold_y, s.fold_at) == (1700, 90, True)
    # 붙인 탭은 창이 화면을 보고 자리를 다시 잡는다. 펼친 메모는 둔 자리가 없다.
    assert t.fold_at is False and t.dock == "left"
    assert o.fold_at is False


# ── 창 동작 ─────────────────────────────────────────────────────────────

def test_fold_and_unfold_keep_position_and_size(qapp):
    w = _sticky(qapp)
    events: list[tuple] = []
    w.foldChanged.connect(lambda *a: events.append(a))
    open_geo = QRect(w.geometry())

    w.fold()
    assert w.is_folded()
    assert w.size() == nf.SLIP_SIZE
    assert w.pos() == open_geo.topLeft()
    assert not w._text.isVisible() and not w._handle.isVisible()
    assert events[-1] == ("n1", {
        "folded": True, "dock": "", "dock_y": 0, "fold_style": "",
        "fold_x": 0, "fold_y": 0, "fold_at": False})

    w.unfold()
    assert not w.is_folded()
    assert w.geometry() == open_geo
    assert w._text.isVisible() and w._handle.isVisible()
    assert w.minimumWidth() == config.MIN_NOTE_WIDTH
    assert events[-1][1]["folded"] is False
    w._teardown = True
    w.close()


def test_slip_click_unfolds_and_handle_double_click_folds(qapp):
    w = _sticky(qapp)
    handle_pos = QPoint(40, config.HANDLE_BAR_HEIGHT // 2)
    # 위젯에 직접 보내는 mouseDClick 은 첫 누름을 보내지 않으므로 함께 보낸다.
    QTest.mousePress(w, Qt.LeftButton, Qt.NoModifier, handle_pos)
    QTest.mouseRelease(w, Qt.LeftButton, Qt.NoModifier, handle_pos)
    QTest.mouseDClick(w, Qt.LeftButton, Qt.NoModifier, handle_pos)
    assert w.is_folded()
    QTest.mouseClick(w, Qt.LeftButton, Qt.NoModifier, QPoint(20, 20))
    assert not w.is_folded()
    w._teardown = True
    w.close()


def test_fold_commits_edit_first(qapp):
    w = _sticky(qapp)
    commits: list[tuple] = []
    releases: list[str] = []
    w.commitEdit.connect(lambda *a: commits.append(a))
    w.releaseLock.connect(releases.append)
    w.enter_edit_mode()
    w._text.setPlainText("고친 내용")
    w.fold()
    assert w.is_folded() and not w._edit_mode
    assert commits and commits[-1][1] == "고친 내용"
    # 저장되는 크기는 쪽지 크기가 아니라 펼친 크기다.
    assert (commits[-1][3], commits[-1][4]) == (280, 260)
    assert releases == ["n1"]
    w._teardown = True
    w.close()


def _drag_face(w: StickyNoteWidget, grab: QPoint, to: QPoint) -> None:
    """접힌 메모를 ``grab`` 자리를 잡고 ``to`` 까지 끌어다 놓는다."""
    start = w.mapToGlobal(grab)
    QTest.mousePress(w, Qt.LeftButton, Qt.NoModifier, grab)
    for step in (0.3, 0.6, 1.0):
        g = start + (to - start) * step
        QTest.mouseMove(w, w.mapFromGlobal(g))
        # offscreen 에서는 mouseMove 가 버튼 상태를 싣지 않는 경우가 있어
        # 같은 경로를 직접 태운다.
        if not w._slip_dragging:
            w._begin_slip_drag()
        w._drag_slip_to(g)
    QTest.mouseRelease(w, Qt.LeftButton, Qt.NoModifier, w.mapFromGlobal(to))


def _drag_open(w: StickyNoteWidget, to: QPoint) -> None:
    """펼친 메모의 손잡이 줄을 잡고 커서를 ``to`` 까지 끌어다 놓는다."""
    grab = QPoint(60, config.HANDLE_BAR_HEIGHT // 2)
    QTest.mousePress(w, Qt.LeftButton, Qt.NoModifier, grab)
    assert w._drag_offset is not None
    w.move(to - grab)
    w._preview_wall(to)                     # mouseMove 가 하는 일(위와 같은 까닭)
    QTest.mouseRelease(w, Qt.LeftButton, Qt.NoModifier, w.mapFromGlobal(to))


def test_docked_tab_unfolds_where_the_note_was_open(qapp):
    w = _sticky(qapp)
    avail = _avail()
    open_geo = QRect(w.geometry())
    w.dock(nf.DOCK_RIGHT)
    assert w.is_folded() and w.dock_side() == nf.DOCK_RIGHT
    geo = w.geometry()
    assert geo.size() == nf.TAB_SIZE
    assert geo.right() == avail.right()
    top, bottom = w.dock_span()
    assert (top, bottom) == (geo.top(), geo.top() + nf.TAB_SIZE.height())

    # 펴면 벽 옆이 아니라 펼쳐 있던 자리로 돌아온다. 붙여 둔 벽은 그대로다.
    w.unfold()
    assert not w.is_folded() and w.dock_side() == nf.DOCK_RIGHT
    assert w.geometry() == open_geo

    # 다시 접으면 같은 탭 자리로 돌아간다.
    w.fold()
    assert w.geometry() == geo

    w.undock()
    assert w.dock_side() == nf.DOCK_NONE
    assert w.size() == nf.SLIP_SIZE
    assert w.geometry().right() < avail.right()
    w.unfold()
    assert w.geometry() == open_geo
    w._teardown = True
    w.close()


def test_dragging_slip_to_edge_docks_and_away_undocks(qapp):
    w = _sticky(qapp)
    avail = _avail()
    w.fold()

    _drag_face(w, QPoint(30, 20), QPoint(avail.left() + 12, 400))
    assert w.dock_side() == nf.DOCK_LEFT
    assert w.geometry().left() == avail.left()
    assert w.size() == nf.TAB_SIZE
    assert w.is_folded()

    _drag_face(w, QPoint(20, 20), QPoint(avail.left() + 500, 300))
    assert w.dock_side() == nf.DOCK_NONE
    assert w.size() == nf.SLIP_SIZE
    assert w.is_folded()
    w._teardown = True
    w.close()


def test_dragging_slip_to_top_and_bottom_walls(qapp):
    w = _sticky(qapp)
    avail = _avail()
    w.fold()

    _drag_face(w, QPoint(30, 20), QPoint(400, avail.top() + 6))
    assert w.dock_side() == nf.DOCK_TOP and w.is_folded()
    geo = w.geometry()
    assert geo.top() == avail.top() and geo.size() == nf.HTAB_SIZE
    # 커서가 탭 가운데에 온다.
    assert abs(geo.center().x() - 400) <= 1
    assert w.dock_span() == (geo.left(), geo.left() + nf.HTAB_SIZE.width())
    assert w.toolTip() == "주간 회의 메모"       # 제목이 잘릴 수 있어 말풍선으로 보인다

    _drag_face(w, QPoint(30, 12), QPoint(300, avail.bottom() - 3))
    assert w.dock_side() == nf.DOCK_BOTTOM
    geo = w.geometry()
    assert geo.bottom() == avail.bottom() and geo.size() == nf.HTAB_SIZE

    # 마우스를 올리면 벽에서 조금 더 나온다.
    w._slip_hover = True
    assert w._tab_rect(True).height() == nf.HTAB_SIZE.height() + nf.HTAB_GROW
    assert w._tab_rect(True).bottom() == avail.bottom()
    w._teardown = True
    w.close()


def test_unfold_returns_to_the_last_open_place(qapp):
    w = _sticky(qapp)
    open_geo = QRect(w.geometry())
    w.fold()
    # 쪽지를 화면의 빈 곳으로 옮겨 둔다.
    _drag_face(w, QPoint(30, 20), QPoint(560, 640))
    spot = w.pos()
    assert spot != open_geo.topLeft() and w.dock_side() == nf.DOCK_NONE
    state = w.fold_state()
    assert (state["fold_x"], state["fold_y"], state["fold_at"]) == (
        spot.x(), spot.y(), True)

    w.unfold()
    assert w.geometry() == open_geo          # 펼쳐 있던 자리로 돌아온다
    w.fold()
    assert w.pos() == spot                   # 접으면 쪽지를 둔 자리로 간다

    # 펼친 메모를 다른 곳으로 옮겨도 쪽지를 둔 자리는 그대로다.
    w.unfold()
    w.move(120, 90)
    w.fold()
    assert w.pos() == spot
    w.unfold()
    assert w.pos() == QPoint(120, 90)
    w._teardown = True
    w.close()


def test_dragging_open_note_to_a_wall_makes_a_tab(qapp):
    settings = AppSettings()
    settings.fold_animation = False
    settings.fold_style = "icon"             # 아이콘 설정이어도 벽에서는 탭이다
    w = StickyNoteWidget(_note(), "지우", settings=settings)
    w.apply_local_state(300, 200)
    w.show()
    qapp.processEvents()
    avail = _avail()
    open_geo = QRect(w.geometry())
    events: list[tuple] = []
    w.foldChanged.connect(lambda *a: events.append(a))

    # 화면 안쪽으로 옮기는 것은 그냥 옮기기다.
    _drag_open(w, QPoint(400, 300))
    assert not w.is_folded() and w.dock_side() == nf.DOCK_NONE
    moved_geo = QRect(w.geometry())
    assert moved_geo != open_geo

    # 커서를 위쪽 벽까지 밀면 놓을 자리에 탭이 미리 보이고, 놓으면 탭이 된다.
    grab = QPoint(60, config.HANDLE_BAR_HEIGHT // 2)
    QTest.mousePress(w, Qt.LeftButton, Qt.NoModifier, grab)
    to = QPoint(420, avail.top() + 2)
    w.move(to - grab)
    w._preview_wall(to)
    assert w._drag_dock == nf.DOCK_TOP
    assert w._ghost is not None and w._ghost.isVisible()
    assert w._ghost.geometry().top() == avail.top()
    QTest.mouseRelease(w, Qt.LeftButton, Qt.NoModifier, w.mapFromGlobal(to))
    assert not w._ghost.isVisible()
    assert w.is_folded() and w.dock_side() == nf.DOCK_TOP
    assert w.size() == nf.HTAB_SIZE and w.geometry().top() == avail.top()
    assert w.fold_style() == nf.FOLD_SLIP
    assert events[-1][1]["dock"] == "top" and events[-1][1]["fold_at"] is True

    # 펴면 벽으로 끌기 전에 있던 자리로 돌아온다.
    w.unfold()
    assert w.geometry() == moved_geo

    # 오른쪽 벽도 같다.
    _drag_open(w, QPoint(avail.right() - 1, 380))
    assert w.is_folded() and w.dock_side() == nf.DOCK_RIGHT
    assert w.geometry().right() == avail.right() and w.size() == nf.TAB_SIZE
    w.unfold()
    assert w.geometry() == moved_geo
    w._teardown = True
    w.close()


def test_slip_kept_at_the_screen_edge_by_an_older_version_stays_put(qapp):
    settings = AppSettings()
    settings.fold_animation = False
    avail = _avail()
    w = StickyNoteWidget(_note(id="e"), "지우", settings=settings)
    # 옛 버전은 화면 오른쪽 끝에 둔 아이콘의 자리를 펼친 자리로 적었다.
    x = avail.right() - nf.ICON_SIZE.width() - 1
    w.apply_local_state(x, 120, 300, 280, folded=True, fold_style="icon",
                        fold_x=x, fold_y=120, fold_at=True)
    w.show()
    assert w.pos() == QPoint(x, 120)
    w.unfold()
    # 메모는 화면 안에서 펴지고,
    assert avail.contains(w.geometry())
    opened = w.pos()
    # 다시 접으면 아이콘은 있던 자리로 돌아간다(왼쪽으로 밀려나지 않는다).
    w.fold()
    assert w.pos() == QPoint(x, 120)
    w.unfold()
    assert w.pos() == opened
    w._teardown = True
    w.close()


def test_server_update_while_folded_does_not_resize_slip(qapp):
    w = _sticky(qapp)
    w.fold()
    w.apply_server_state(_note(width=400, height=320, version=2,
                               content="새 제목\n둘째 줄"))
    assert w.size() == nf.SLIP_SIZE
    assert w._title() == "새 제목"
    w.unfold()
    assert w.size() == QSize(400, 320)
    w._teardown = True
    w.close()


def test_lock_notice_shows_only_when_edit_is_refused(qapp):
    w = _sticky(qapp)
    w.set_lock_holder("서연")
    # 남이 편집하는 동안에도 본문은 가리지 않는다. 누구인지는 손잡이 줄에 적는다.
    assert not w._overlay.isVisible()
    assert "서연" in w._lock_label.text()

    # 고치려다 거절됐을 때만 알림이 잠깐 뜬다.
    w.show_lock_denied("서연")
    assert w._overlay.isVisible()
    assert w._overlay_timer.isActive()
    w._overlay_timer.timeout.emit()
    assert not w._overlay.isVisible()

    # 접힌 쪽지 위에는 띄우지 않는다.
    w.fold()
    w.show_lock_denied("서연")
    assert not w._overlay.isVisible()
    w.unfold()
    w.set_lock_holder(None)
    assert not w._overlay.isVisible()
    w._teardown = True
    w.close()


def test_restore_folded_and_docked_state(qapp):
    settings = AppSettings()
    settings.fold_animation = False
    avail = _avail()

    slip = StickyNoteWidget(_note(id="s"), "지우", settings=settings)
    slip.apply_local_state(320, 240, 300, 280, folded=True)
    assert slip.is_folded()
    assert slip.geometry() == QRect(QPoint(320, 240), nf.SLIP_SIZE)
    slip.show()
    slip.unfold()
    assert slip.geometry() == QRect(320, 240, 300, 280)

    tab = StickyNoteWidget(_note(id="t"), "지우", settings=settings)
    tab.apply_local_state(0, 300, 0, 0, folded=True, dock="left", dock_y=300)
    assert tab.dock_side() == nf.DOCK_LEFT
    assert tab.geometry() == QRect(avail.left(), 300, nf.TAB_SIZE.width(),
                                   nf.TAB_SIZE.height())
    # 잘못 저장된 값은 붙이지 않는다.
    odd = StickyNoteWidget(_note(id="o"), "지우", settings=settings)
    odd.apply_local_state(100, 100, folded=False, dock="middle", dock_y=5)
    assert odd.dock_side() == nf.DOCK_NONE and not odd.is_folded()

    # 접힌 자리와 펼친 자리를 따로 적은 상태: 탭은 둔 자리에, 펴면 펼친 자리에.
    top = StickyNoteWidget(_note(id="p"), "지우", settings=settings)
    top.apply_local_state(260, 240, 300, 280, folded=True, dock="top",
                          dock_y=400, fold_x=400, fold_y=avail.top(),
                          fold_at=True)
    assert top.dock_side() == nf.DOCK_TOP
    assert top.geometry() == QRect(QPoint(400, avail.top()), nf.HTAB_SIZE)
    top.show()
    top.unfold()
    assert top.geometry() == QRect(260, 240, 300, 280)

    free = StickyNoteWidget(_note(id="q"), "지우", settings=settings)
    free.apply_local_state(260, 240, 300, 280, folded=True, fold_x=520,
                           fold_y=600, fold_at=True)
    assert free.geometry() == QRect(QPoint(520, 600), nf.SLIP_SIZE)
    free.show()
    free.unfold()
    assert free.geometry() == QRect(260, 240, 300, 280)
    for w in (slip, tab, odd, top, free):
        w._teardown = True
        w.close()


def test_dock_resolver_keeps_tabs_apart(qapp):
    settings = AppSettings()
    settings.fold_animation = False
    stickies: dict[str, StickyNoteWidget] = {}

    def resolver(note_id, side, y, height, screen_rect):
        spans = [s.dock_span() for nid, s in stickies.items()
                 if nid != note_id and s.isVisible() and s.dock_side() == side]
        return nf.free_dock_slot(y, height, screen_rect, spans, side)

    for nid in ("a", "b"):
        w = StickyNoteWidget(_note(id=nid), "지우", settings=settings)
        w.set_dock_resolver(resolver)
        w.apply_local_state(200, 300)
        w.show()
        stickies[nid] = w
    for side in (nf.DOCK_LEFT, nf.DOCK_TOP):
        stickies["a"].dock(side)
        stickies["b"].dock(side)
        a, b = stickies["a"].dock_span(), stickies["b"].dock_span()
        assert a[1] + nf.TAB_GAP <= b[0] or b[1] + nf.TAB_GAP <= a[0]
        assert not stickies["a"].geometry().intersects(stickies["b"].geometry())
    for w in stickies.values():
        w._teardown = True
        w.close()


def test_animated_fold_reaches_the_same_end_state(qapp, monkeypatch):
    monkeypatch.setattr(nf, "system_animations_enabled", lambda: True)
    w = _sticky(qapp, animate=True)
    open_geo = QRect(w.geometry())
    w.fold()
    # 접는 동안에는 창이 펼친 크기 그대로이고 누름을 받지 않는다.
    assert w._fold_busy() and w.geometry() == open_geo
    QTest.mouseClick(w, Qt.LeftButton, Qt.NoModifier, QPoint(20, 20))
    QTest.qWait(nf.FOLD_MS + nf.SLIDE_MS + 250)
    assert w.is_folded() and not w._fold_busy()
    assert w.geometry() == QRect(open_geo.topLeft(), nf.SLIP_SIZE)

    w.unfold()
    assert w._fold_busy()
    QTest.qWait(nf.UNFOLD_MS + 250)
    assert not w.is_folded() and not w._fold_busy()
    assert w.geometry() == open_geo and w._text.isVisible()

    # 접는 도중에 숨기면 끝 상태로 바로 넘어간다.
    w.fold()
    w.hide()
    assert w.is_folded() and not w._fold_busy()
    assert w.size() == nf.SLIP_SIZE
    w._teardown = True
    w.close()


def test_lock_granted_after_folding_is_returned(qapp):
    # 본문을 눌러 잠금을 요청한 직후(게스트는 응답이 늦다) 접으면, 뒤늦게 온
    # 허용으로 보이지 않는 편집 상태에 들어가 잠금을 쥐고 있으면 안 된다.
    w = _sticky(qapp)
    requests: list[str] = []
    releases: list[str] = []
    w.requestLock.connect(requests.append)
    w.releaseLock.connect(releases.append)
    w._request_edit()
    assert requests == ["n1"] and w._pending_lock
    w.fold()
    w.enter_edit_mode()
    assert not w._edit_mode
    assert releases == ["n1"]
    assert w._text.isReadOnly()
    w._teardown = True
    w.close()


def test_animated_unfold_moves_to_the_open_place_first(qapp, monkeypatch):
    monkeypatch.setattr(nf, "system_animations_enabled", lambda: True)
    w = _sticky(qapp, animate=True)
    open_geo = QRect(w.geometry())
    w.fold()
    QTest.qWait(nf.FOLD_MS + nf.SLIDE_MS + 250)
    _drag_face(w, QPoint(30, 20), QPoint(560, 640))
    QTest.qWait(nf.SLIDE_MS + 100)
    spot = w.pos()

    w.unfold()
    # 먼저 펼칠 자리로 옮겨 가고, 그동안에는 누름을 받지 않는다.
    assert w.is_folded() and w._fold_busy()
    QTest.mouseClick(w, Qt.LeftButton, Qt.NoModifier, QPoint(20, 20))
    QTest.qWait(nf.SLIDE_MS + nf.UNFOLD_MS + 300)
    assert not w.is_folded() and not w._fold_busy()
    assert w.geometry() == open_geo and w._text.isVisible()

    w.fold()
    QTest.qWait(nf.FOLD_MS + nf.SLIDE_MS + 300)
    assert w.is_folded() and w.pos() == spot

    # 옮겨 가는 도중에 숨기면 다 펼친 상태로 넘어간다.
    w.unfold()
    assert w._fold_busy()
    w.hide()
    assert not w.is_folded() and not w._fold_busy()
    assert w.geometry() == open_geo
    w._teardown = True
    w.close()


def test_folded_menu_has_no_dock_items(qapp, monkeypatch):
    from app.ui import edit_menu

    seen: list[list[str]] = []

    def fake_run(menu, pos):
        seen.append([a.text() for a in menu.actions()
                     if not a.isSeparator()])

    monkeypatch.setattr(edit_menu, "run_menu", fake_run)
    w = _sticky(qapp)
    w._show_more_menu()
    assert i18n.t("sticky.more_fold") in seen[-1]
    assert i18n.t("sticky.more_fold_icon") in seen[-1]
    assert not any("가장자리" in text for text in seen[-1])

    w.dock(nf.DOCK_LEFT)
    from PySide6.QtGui import QContextMenuEvent
    w.contextMenuEvent(QContextMenuEvent(
        QContextMenuEvent.Mouse, QPoint(5, 5), w.mapToGlobal(QPoint(5, 5))))
    assert seen[-1] == [i18n.t("sticky.slip_unfold"), i18n.t("sticky.more_hide")]
    w.unfold()
    w._show_more_menu()
    # 벽에 붙여 둔 메모는 접는 길이 하나다.
    assert i18n.t("sticky.tip_fold") in seen[-1]
    assert i18n.t("sticky.more_fold_icon") not in seen[-1]
    w._teardown = True
    w.close()


# ── 다른 창 위에 두기 ───────────────────────────────────────────────────

@pytest.fixture
def layers(monkeypatch):
    """창을 '항상 위'로 올리고 내린 기록."""
    from app.ui import sticky_note as sn

    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(sn, "_set_window_topmost",
                        lambda hwnd, on: calls.append(("top", on)))
    monkeypatch.setattr(sn, "_pin_window_to_bottom",
                        lambda hwnd: calls.append(("bottom", True)))
    return calls


def test_only_a_folded_tab_stays_above_other_windows(qapp, layers):
    w = _sticky(qapp)
    assert not w._topmost
    w.dock(nf.DOCK_RIGHT)
    assert w.is_folded() and w._topmost

    # 붙였던 메모도 펼치면 보통 창이다(쓰고 있는 다른 창을 가리지 않는다).
    # 붙여 둔 벽은 기억한다.
    w.unfold()
    assert w.dock_side() == nf.DOCK_RIGHT
    assert not w._topmost and layers[-1] == ("top", False)

    w.fold()
    assert w._topmost and layers[-1] == ("top", True)
    w.undock()
    assert not w._topmost
    w._teardown = True
    w.close()


def test_open_note_that_remembers_a_wall_starts_below_other_windows(qapp, layers):
    # 벽에 붙였다가 펼쳐 둔 채로 앱을 다시 켠 상태.
    settings = AppSettings()
    settings.fold_animation = False
    avail = _avail()
    w = StickyNoteWidget(_note(id="z"), "지우", settings=settings)
    w.apply_local_state(260, 240, 300, 280, folded=False, dock="right",
                        dock_y=300, fold_x=avail.right() - 45, fold_y=300,
                        fold_at=True)
    w.show()
    qapp.processEvents()
    assert not w.is_folded() and w.dock_side() == nf.DOCK_RIGHT
    assert not w._topmost
    assert ("top", True) not in layers and ("bottom", True) in layers

    tab = StickyNoteWidget(_note(id="y"), "지우", settings=settings)
    tab.apply_local_state(260, 240, 300, 280, folded=True, dock="right",
                          dock_y=420)
    tab.show()
    qapp.processEvents()
    assert tab._topmost and layers[-1] == ("top", True)

    # 설정을 끄면 탭도 보통 창이다.
    settings.dock_on_top = False
    tab.refresh_dock_layer()
    assert not tab._topmost and layers[-1] == ("top", False)
    for x in (w, tab):
        x._teardown = True
        x.close()


def test_animated_unfold_of_a_tab_ends_as_a_normal_window(qapp, layers, monkeypatch):
    monkeypatch.setattr(nf, "system_animations_enabled", lambda: True)
    w = _sticky(qapp, animate=True)
    w.dock(nf.DOCK_LEFT)
    QTest.qWait(nf.FOLD_MS + nf.SLIDE_MS + 300)
    assert w.is_folded() and w._topmost

    w.unfold()
    # 펴지는 동안에는 위에 그대로 있어 움직임이 다른 창에 가리지 않는다.
    assert w._fold_busy() and w._topmost
    QTest.qWait(nf.SLIDE_MS + nf.UNFOLD_MS + 300)
    assert not w.is_folded() and not w._fold_busy()
    assert not w._topmost and layers[-1] == ("top", False)
    w._teardown = True
    w.close()
