"""진짜 바탕화면의 아이콘 격자를 화면마다의 격자(note_fold.IconGrid)로 바꿔 준다.

접힌 메모와 메모 폴더의 아이콘이 진짜 바탕화면 아이콘과 같은 칸에 같은 크기로
놓이게 한다. 사용자가 아이콘 크기를 바꾸면(바탕화면에서 Ctrl + 휠, 보기 메뉴)
곧 알아채고 ``changed`` 를 낸다. 받는 쪽은 아이콘 창들을 새 크기와 자리로 옮긴다.

바탕화면이 알려 주는 것(app/desktop_icons.py)은 물리 픽셀이다. 항목의 '자리'에서
칸의 왼쪽 위까지의 거리는 바탕화면에 물어 잰 값(DesktopView.offset)을 쓴다.
잴 수 없으면(바탕화면에 아이콘이 하나도 없다) 어림한다: 자리는 그림의 왼쪽이고
칸은 거기서 (칸 폭 - 그림) / 2 만큼 왼쪽에서, 3픽셀 아래에서 시작한다(이 PC 의
바탕화면에서 항목마다의 그림, 이름, 전체 사각형을 재서 확인한 값이다)."""
from __future__ import annotations

import os
import time
from collections import Counter
from dataclasses import replace

from PySide6.QtCore import QObject, QRect, QSize, QTimer, Signal
from PySide6.QtGui import QGuiApplication

from .. import config, desktop_icons
from ..desktop_icons import DesktopView
from . import note_fold as nf

# 아이콘 크기가 바뀌었는지 보는 간격. 가볍게 묻는 것이라(0.1 ms) 자주 봐도 된다.
WATCH_MS = 250
# 탐색기가 답하지 않을 때 다시 물어보기까지.
RETRY_SECONDS = 3.0
# 놓여 있는 아이콘의 자리는 이보다 오래됐으면 다시 읽는다(끌어다 놓을 때).
ITEMS_FRESH_SECONDS = 2.0
# 가벼운 물음은 칸의 간격만 본다. 아주 작은 아이콘은 크기가 달라져도 간격이 같을
# 수 있어, 가끔은 통째로 다시 읽는다. 바탕화면에 아이콘이 아주 많아 읽기가 느린
# PC 에서는 하지 않는다(그때는 아이콘을 접거나 옮길 때 다시 읽는다).
FULL_READ_SECONDS = 20.0
FULL_READ_MAX_COST = 0.04
# 잴 수 없을 때 쓰는 값: 항목의 자리에서 칸의 위까지(100% 배율), 첫 줄의 자리.
_ITEM_TOP = 3
_FIRST_ROW = 2


def _real_desktop_allowed() -> bool:
    """시험으로 띄운 실행은 이 PC 의 바탕화면 모양에 기대지 않는다(환경 변수로
    켠 때만 읽는다). 읽기만 하는 것이라 해롭지는 않지만, 결과가 PC 마다 달라진다."""
    return (not config.isolated_run()
            or os.environ.get("MEMIL_REAL_DESKTOP") == "1")


def _most_common(values: list[int], default: int) -> int:
    return Counter(values).most_common(1)[0][0] if values else default


def item_offset(view: DesktopView) -> tuple[int, int]:
    """항목의 자리에서 그 항목이 차지하는 칸의 왼쪽 위까지(물리 픽셀)."""
    if view.offset is not None:
        return view.offset
    return (-((view.spacing[0] - view.icon) // 2),
            int(round(_ITEM_TOP * view.dpi / 96.0)))


def screen_grid(view: DesktopView, geometry: QRect, area: QRect,
                dpr: float, residue: tuple[int, int] | None = None) -> nf.IconGrid:
    """한 화면의 격자. ``geometry`` 와 ``area`` 는 Qt 가 주는 그 화면의 자리와
    작업 영역이다(왼쪽 위는 물리 픽셀과 같고, 크기는 배율로 나눈 값)."""
    dpr = dpr or 1.0
    sx, sy = geometry.left(), geometry.top()
    left = sx + (area.left() - sx) * dpr
    top = sy + (area.top() - sy) * dpr
    right, bottom = left + area.width() * dpr, top + area.height() * dpr
    side = view.icon
    cx, cy = view.spacing
    off_x, off_y = item_offset(view)
    mine = [(x, y) for x, y in view.items
            if left <= x < right and top <= y < bottom]
    if residue is None:
        residue = item_residue(view, [(geometry, area, dpr)])
    rx, ry = residue
    # 칸 (0, 0) 의 왼쪽 위(물리 픽셀). 작업 영역 밖으로 나가면 한 칸 들인다.
    x0 = left + rx + off_x
    y0 = top + ry + off_y
    if x0 < left - 0.5:
        x0 += cx
    if y0 < top - 0.5:
        y0 += cy
    taken = frozenset(
        (int(round((x + off_x - x0) / cx)), int(round((y + off_y - y0) / cy)))
        for x, y in mine)
    return nf.IconGrid(
        area=QRect(area),
        cell=QSize(int(round(cx / dpr)), int(round(cy / dpr))),
        icon=side / dpr,
        x0=sx + (x0 - sx) / dpr, y0=sy + (y0 - sy) / dpr,
        dx=cx / dpr, dy=cy / dpr, taken=taken)


def item_residue(view: DesktopView,
                 screens: list[tuple[QRect, QRect, float]]) -> tuple[int, int]:
    """놓여 있는 아이콘들이 칸 안에서 차지하는 자리(작업 영역의 왼쪽 위에서 잰
    나머지). 격자에 맞춰 놓여 있으면 모두 같다. 아이콘이 없는 화면은 다른 화면의
    값을 쓰고, 어디에도 없으면 기본 자리(그림이 칸의 가운데)를 쓴다."""
    cx, cy = view.spacing
    # 첫 줄은 배율과 무관하게 창틀 두께(2픽셀)만큼 내려와 있다.
    default = ((cx - view.icon) // 2, _FIRST_ROW)
    if not view.snap:
        return default
    xs: list[int] = []
    ys: list[int] = []
    for geometry, area, dpr in screens:
        dpr = dpr or 1.0
        sx, sy = geometry.left(), geometry.top()
        left = sx + (area.left() - sx) * dpr
        top = sy + (area.top() - sy) * dpr
        right, bottom = left + area.width() * dpr, top + area.height() * dpr
        for x, y in view.items:
            if left <= x < right and top <= y < bottom:
                xs.append(int(round(x - left)) % cx)
                ys.append(int(round(y - top)) % cy)
    return _most_common(xs, default[0]), _most_common(ys, default[1])


class DesktopGrid(QObject):
    """바탕화면의 아이콘 격자를 지켜본다."""

    changed = Signal()      # 칸의 크기나 자리가 달라졌다
    refreshed = Signal()    # 바탕화면을 다시 읽었다(아이콘 목록 창이 바뀌었을 수 있다)

    def __init__(self, parent: QObject | None = None, *, reader=None,
                 light=None):
        super().__init__(parent)
        allowed = _real_desktop_allowed()
        self._reader = reader or (desktop_icons.read if allowed else (lambda: None))
        self._light = light or desktop_icons.spacing_of
        self._view: DesktopView | None = None
        self._grids: dict[tuple, nf.IconGrid] = {}
        # 지금 함께 끌려가고 있는 진짜 아이콘들의 번호. 그 아이콘들이 비울 칸은
        # 빈 칸으로 본다.
        self._lifted: frozenset[int] = frozenset()
        self._read_at = 0.0
        self._read_cost = 0.0
        self._retry_at = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(WATCH_MS)
        self._timer.timeout.connect(self._tick)

    # ── 켜고 끄기 ──

    def start(self) -> None:
        self.refresh()
        app = QGuiApplication.instance()
        if app is not None:
            app.screenAdded.connect(self._on_screens)
            app.screenRemoved.connect(self._on_screens)
            for screen in app.screens():
                self._watch(screen)
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _watch(self, screen) -> None:
        screen.geometryChanged.connect(self._on_screens)
        screen.availableGeometryChanged.connect(self._on_screens)
        screen.logicalDotsPerInchChanged.connect(self._on_screens)

    def _on_screens(self, *_args) -> None:
        app = QGuiApplication.instance()
        if app is not None:
            for screen in app.screens():
                try:
                    screen.geometryChanged.disconnect(self._on_screens)
                    screen.availableGeometryChanged.disconnect(self._on_screens)
                    screen.logicalDotsPerInchChanged.disconnect(self._on_screens)
                except (RuntimeError, TypeError):
                    pass
                self._watch(screen)
        self.refresh()

    # ── 읽기 ──

    def view(self) -> DesktopView | None:
        return self._view

    def _screens(self) -> list[tuple[QRect, QRect, float]]:
        return [(s.geometry(), s.availableGeometry(), s.devicePixelRatio())
                for s in QGuiApplication.screens()]

    def refresh(self) -> None:
        """바탕화면에 다시 묻는다. 칸의 모양이 달라졌으면 ``changed`` 를 낸다."""
        started = time.monotonic()
        try:
            view = self._reader()
        except Exception:
            view = None
        self._read_at = time.monotonic()
        self._read_cost = self._read_at - started
        self._view = view
        before, self._grids = self._grids, self._build(view)
        grids = self._grids
        same = (before.keys() == grids.keys() and all(
            before[key].same_shape(grids[key]) for key in grids))
        if not same:
            self.changed.emit()
        self.refreshed.emit()

    def _build(self, view: DesktopView | None) -> dict[tuple, nf.IconGrid]:
        grids: dict[tuple, nf.IconGrid] = {}
        if view is None:
            return grids
        screens = self._screens()
        # 칸의 자리는 놓여 있는 아이콘 전부로 잰다(끌려가는 것도 아직 제 칸에 있다).
        residue = item_residue(view, screens)
        placed = view
        if self._lifted:
            indices = view.indices or tuple(range(len(view.items)))
            kept = [(index, pos) for index, pos in zip(indices, view.items)
                    if index not in self._lifted]
            placed = replace(view, items=tuple(pos for _index, pos in kept),
                             indices=tuple(index for index, _pos in kept))
        for geometry, area, dpr in screens:
            grids[self._key(area)] = screen_grid(placed, geometry, area, dpr,
                                                 residue)
        return grids

    def set_lifted(self, indices) -> None:
        """그 번호의 진짜 아이콘들이 곧 다른 칸으로 옮겨진다(메모 아이콘과 함께
        끌려가는 중이다). 그 아이콘들이 있는 칸을 빈 칸으로 본다. 빈 목록을 주면
        원래대로 본다."""
        lifted = frozenset(int(index) for index in indices)
        if lifted == self._lifted:
            return
        self._lifted = lifted
        self._grids = self._build(self._view)

    def refresh_items(self) -> None:
        """놓여 있는 아이콘의 자리가 오래됐으면 다시 읽는다(끌어다 놓기 전에)."""
        if time.monotonic() - self._read_at > ITEMS_FRESH_SECONDS:
            self.refresh()

    def _tick(self) -> None:
        view = self._view
        now = time.monotonic()
        if view is None:
            # 아직 읽지 못했다(탐색기가 떠 있지 않았다). 가끔 다시 물어본다.
            if now >= self._retry_at:
                self._retry_at = now + RETRY_SECONDS
                self.refresh()
            return
        spacing = self._light(view.window)
        if spacing == view.spacing:
            if (now - self._read_at > FULL_READ_SECONDS
                    and self._read_cost <= FULL_READ_MAX_COST):
                self.refresh()
            return
        if spacing is None and now < self._retry_at:
            return                      # 탐색기가 답하지 않는다. 조금 뒤에 다시 본다
        self._retry_at = now + RETRY_SECONDS
        self.refresh()

    # ── 격자 ──

    @staticmethod
    def _key(area: QRect) -> tuple:
        return area.left(), area.top(), area.width(), area.height()

    def grid_for(self, area: QRect) -> nf.IconGrid | None:
        """그 화면(작업 영역)의 격자. 바탕화면을 읽지 못했으면 None."""
        grid = self._grids.get(self._key(area))
        if grid is not None or self._view is None:
            return grid
        # 작업 영역이 방금 달라졌다(작업 표시줄을 옮겼다). 그 화면을 찾아 새로 낸다.
        for geometry, avail, dpr in self._screens():
            if avail == area or avail.contains(area.center()):
                grid = screen_grid(self._view, geometry, avail, dpr,
                                   item_residue(self._view, self._screens()))
                self._grids[self._key(area)] = grid
                return grid
        return None


__all__ = ["DesktopGrid", "item_offset", "item_residue", "screen_grid"]
