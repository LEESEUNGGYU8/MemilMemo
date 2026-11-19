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

def test_dock_side_near_edges():
    slip = nf.SLIP_SIZE
    assert nf.dock_side_near(QRect(QPoint(10, 300), slip), SCREEN) == nf.DOCK_LEFT
    assert nf.dock_side_near(QRect(QPoint(-40, 300), slip), SCREEN) == nf.DOCK_LEFT
    right = QRect(QPoint(SCREEN.right() - slip.width() - 5, 300), slip)
    assert nf.dock_side_near(right, SCREEN) == nf.DOCK_RIGHT
    assert nf.dock_side_near(QRect(QPoint(600, 300), slip), SCREEN) == nf.DOCK_NONE


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


def test_open_rect_for_dock_stays_on_screen():
    size = QSize(280, 260)
    left = nf.open_rect_for_dock(nf.DOCK_LEFT, size, 300, SCREEN)
    assert (left.x(), left.y(), left.size()) == (0, 300, size)
    right = nf.open_rect_for_dock(nf.DOCK_RIGHT, size, 300, SCREEN)
    assert right.right() == SCREEN.right()
    low = nf.open_rect_for_dock(nf.DOCK_LEFT, size, 880, SCREEN)
    assert low.bottom() <= SCREEN.bottom()


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
])
def test_fold_frames_start_open_and_end_as_slip(qapp, anchor, dock):
    pix = QPixmap(280, 260)
    pix.fill(QColor("#FFE066"))
    size = nf.TAB_SIZE if dock else nf.SLIP_SIZE
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
              "fold_style": ""}}

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
            "odd": {"folded": 1, "dock": "top", "dock_y": "x"},
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
    assert "ghost" not in state.notes


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
    assert events[-1] == ("n1", True, "", 0, "")

    w.unfold()
    assert not w.is_folded()
    assert w.geometry() == open_geo
    assert w._text.isVisible() and w._handle.isVisible()
    assert w.minimumWidth() == config.MIN_NOTE_WIDTH
    assert events[-1][1] is False
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


def test_dock_from_menu_then_unfold_at_edge(qapp):
    w = _sticky(qapp)
    avail = _avail()
    w.dock(nf.DOCK_RIGHT)
    assert w.is_folded() and w.dock_side() == nf.DOCK_RIGHT
    geo = w.geometry()
    assert geo.size() == nf.TAB_SIZE
    assert geo.right() == avail.right()
    top, bottom = w.dock_span()
    assert (top, bottom) == (geo.top(), geo.top() + nf.TAB_SIZE.height())

    w.unfold()
    assert not w.is_folded() and w.dock_side() == nf.DOCK_RIGHT
    assert w.geometry().right() == avail.right()
    assert w.size() == QSize(280, 260)

    # 붙인 채 접으면 같은 탭 자리로 돌아간다.
    w.fold()
    assert w.geometry() == geo

    w.undock()
    assert w.dock_side() == nf.DOCK_NONE
    assert w.size() == nf.SLIP_SIZE
    assert w.geometry().right() < avail.right()
    w._teardown = True
    w.close()


def test_dragging_slip_to_edge_docks_and_away_undocks(qapp):
    w = _sticky(qapp)
    avail = _avail()
    w.fold()
    start = w.mapToGlobal(QPoint(30, 20))

    def drag(to: QPoint) -> None:
        QTest.mousePress(w, Qt.LeftButton, Qt.NoModifier,
                         w.mapFromGlobal(start))
        for step in (0.3, 0.6, 1.0):
            g = start + (to - start) * step
            QTest.mouseMove(w, w.mapFromGlobal(g))
            # offscreen 에서는 mouseMove 가 버튼 상태를 싣지 않는 경우가 있어
            # 같은 경로를 직접 태운다.
            if not w._slip_dragging:
                w._begin_slip_drag()
            w._drag_slip_to(g)
        QTest.mouseRelease(w, Qt.LeftButton, Qt.NoModifier,
                           w.mapFromGlobal(to))

    drag(QPoint(avail.left() + 12, 400))
    assert w.dock_side() == nf.DOCK_LEFT
    assert w.geometry().left() == avail.left()
    assert w.size() == nf.TAB_SIZE
    assert w.is_folded()

    start = w.mapToGlobal(QPoint(20, 20))
    drag(QPoint(avail.left() + 500, 300))
    assert w.dock_side() == nf.DOCK_NONE
    assert w.size() == nf.SLIP_SIZE
    assert w.is_folded()
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
    odd.apply_local_state(100, 100, folded=False, dock="top", dock_y=5)
    assert odd.dock_side() == nf.DOCK_NONE and not odd.is_folded()
    for w in (slip, tab, odd):
        w._teardown = True
        w.close()


def test_dock_resolver_keeps_tabs_apart(qapp):
    settings = AppSettings()
    settings.fold_animation = False
    stickies: dict[str, StickyNoteWidget] = {}

    def resolver(note_id, side, y, height, screen_rect):
        spans = [s.dock_span() for nid, s in stickies.items()
                 if nid != note_id and s.isVisible() and s.dock_side() == side]
        return nf.free_dock_slot(y, height, screen_rect, spans)

    for nid in ("a", "b"):
        w = StickyNoteWidget(_note(id=nid), "지우", settings=settings)
        w.set_dock_resolver(resolver)
        w.apply_local_state(200, 300)
        w.show()
        stickies[nid] = w
    stickies["a"].dock(nf.DOCK_LEFT)
    stickies["b"].dock(nf.DOCK_LEFT)
    a, b = stickies["a"].dock_span(), stickies["b"].dock_span()
    assert a[1] + nf.TAB_GAP <= b[0] or b[1] + nf.TAB_GAP <= a[0]
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
