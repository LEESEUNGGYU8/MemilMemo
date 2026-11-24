"""바탕화면에 놓인 메모 아이콘과 메모 폴더 아이콘을 파일처럼 고르고 다룬다.

진짜 바탕화면 아이콘과 한 묶음처럼 움직이게 한다.

- 한 번 누르면 고른다. Ctrl 을 누른 채 누르면 더하거나 빼고, Shift 를 누른 채
  누르면 기준 아이콘과의 사이에 든 것을 모두 고른다. 진짜 아이콘을 골라 둔 채로
  해도 그 선택은 남는다.
- 메모 아이콘은 눌려도 키보드를 가져가지 않는다. 키보드는 바탕화면이 쥐고 있고,
  거기서 눌린 Delete, Enter, Ctrl+A, Esc 를 우리도 알아채 고른 메모에 같은 일을
  한다(진짜 파일은 탐색기가 스스로 한다).
- 바탕화면 빈 곳을 끌어 고르는 사각형에 닿으면 진짜 아이콘과 함께 골라진다.
- 고른 것을 끌면 함께 움직인다. 진짜 아이콘을 잡고 끌면 고른 메모 아이콘이
  따라가고, 메모 아이콘을 잡고 끌면 고른 진짜 아이콘도 같은 만큼 옮겨진다
  (끄는 동안에는 흐린 그림이 따라오고, 놓을 때 옮긴다).
- 고른 것을 Windows 휴지통 아이콘에 놓으면 메모는 메모 휴지통으로, 파일은 Windows
  휴지통으로 간다. 폴더처럼 무엇을 담는 다른 아이콘에 놓으면 메모는 제자리로
  돌아온다(메모 아이콘은 바탕화면에만 있다).

여기에는 고른 상태와 규칙만 있다. 메모와 폴더에 실제로 하는 일(지우기, 폴더로
옮기기, 창 열기)은 메인 창이 한다(``host``). 탐색기에 하는 일은 ``desk`` 가 한다
(app/desktop_select.py)."""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QGuiApplication, QImage

from .. import desktop_select, desktop_watch, i18n
from . import edit_menu
from .icon_grid import item_offset
from .icons import svg_icon
from .menu_item import add_centered_menu_action

NOTE, FOLDER = "note", "folder"
# 끌어 고르는 동안 사각형을 다시 보는 간격.
MARQUEE_TICK_MS = 16
# 바탕화면에서 진짜 아이콘을 끈 뒤, 달라진 자리를 다시 읽기까지.
REALIGN_MS = 400
# 바탕화면에서 단추를 뗀 뒤, 탐색기가 고른 것을 다시 읽기까지.
SNAPSHOT_MS = 120
# 바탕화면이 키보드를 쥐고 있는지 보는 간격과, 쥐고 있는 동안 글쇠를 보는 간격.
KEYS_IDLE_MS = 200
KEYS_WATCH_MS = 25
# 진짜 아이콘을 Windows 휴지통에 놓은 뒤, 탐색기가 지웠는지 다시 보는 간격과 횟수
# (탐색기가 지울지 묻는 창을 띄웠으면 사용자가 답할 때까지 걸린다).
BIN_POLL_MS = 500
BIN_TRIES = 60
# 놓은 곳에 있던 진짜 아이콘의 종류.
ON_BIN, ON_CONTAINER = "bin", "container"
# Windows 휴지통 위에 올라가 있는 동안 끌려가는 아이콘의 진하기.
DROP_FADE = 0.45


def note_key(note_id: str) -> str:
    return f"{NOTE}:{note_id}"


def folder_key(folder_id: str) -> str:
    return f"{FOLDER}:{folder_id}"


def split_key(key: str) -> tuple[str, str]:
    kind, _, ident = key.partition(":")
    return kind, ident


class IconSelection(QObject):
    """고른 아이콘들(고른 차례대로)."""

    changed = Signal()

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._keys: list[str] = []

    def keys(self) -> list[str]:
        return list(self._keys)

    def __contains__(self, key: str) -> bool:
        return key in self._keys

    def __len__(self) -> int:
        return len(self._keys)

    def of(self, kind: str) -> list[str]:
        """고른 것 가운데 메모(또는 폴더)의 id."""
        return [split_key(k)[1] for k in self._keys if split_key(k)[0] == kind]

    def set(self, keys) -> None:
        keys = list(dict.fromkeys(keys))
        if keys != self._keys:
            self._keys = keys
            self.changed.emit()

    def clear(self) -> None:
        self.set([])

    def select_only(self, key: str) -> None:
        self.set([key])

    def add(self, key: str) -> None:
        if key not in self._keys:
            self.set(self._keys + [key])

    def toggle(self, key: str) -> None:
        if key in self._keys:
            self.set([k for k in self._keys if k != key])
        else:
            self.add(key)

    def keep(self, alive) -> None:
        """사라진 아이콘을 고른 것에서 뺀다."""
        alive = set(alive)
        self.set([k for k in self._keys if k in alive])


def _screen_scale(x: int, y: int) -> tuple[QRect | None, float]:
    for screen in QGuiApplication.screens():
        g = screen.geometry()
        dpr = screen.devicePixelRatio() or 1.0
        if (g.left() <= x < g.left() + g.width() * dpr
                and g.top() <= y < g.top() + g.height() * dpr):
            return g, dpr
    return None, 1.0


def to_logical(x: int, y: int) -> QPoint:
    """물리 픽셀의 화면 좌표를 Qt 좌표로 바꾼다. 화면의 왼쪽 위는 두 좌표가
    같고, 그 안에서의 거리만 배율로 나눈다."""
    g, dpr = _screen_scale(x, y)
    if g is None:
        return QPoint(int(x), int(y))
    return QPoint(int(round(g.left() + (x - g.left()) / dpr)),
                  int(round(g.top() + (y - g.top()) / dpr)))


def scale_at(pos: QPoint) -> float:
    """그 자리(Qt 좌표)의 화면 배율."""
    screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
    return (screen.devicePixelRatio() or 1.0) if screen is not None else 1.0


class DesktopMarquee(QObject):
    """바탕화면에서 사용자가 누르고 끄는 것을 따라간다(app/desktop_watch.py).

    탐색기가 그리는 사각형의 자리는 알려 주는 곳이 없다. 탐색기도 누른 자리와
    지금 마우스 자리로 사각형을 그리므로, 같은 두 점으로 같은 사각형을 얻는다."""

    pressed = Signal(bool)      # 바탕화면이 눌렸다(Ctrl·Shift 가 눌려 있었는가)
    moved = Signal(QRect)       # 끌어 고르는 사각형(Qt 좌표)
    dragged = Signal(QPoint)    # 진짜 아이콘을 잡고 끄는 중이다(누른 자리에서 움직인 만큼)
    finished = Signal(bool)     # 단추를 뗐다(진짜 아이콘을 눌러 끌었는가)

    def __init__(self, grid, parent: QObject | None = None, *,
                 probe=desktop_watch, watch=None):
        super().__init__(parent)
        self._grid = grid
        self._probe = probe
        self._watch = watch or desktop_watch.CaptureWatch(self._on_capture)
        self._active = False
        self._start = (0, 0)
        self._item = -1
        self._item_pos: tuple[int, int] | None = None
        self._shift = False
        self._dragging = False
        self._end = (0, 0)
        self._timer = QTimer(self)
        self._timer.setInterval(MARQUEE_TICK_MS)
        self._timer.timeout.connect(self._tick)

    def sync(self) -> None:
        """바탕화면 아이콘 목록 창이 바뀌었으면(탐색기가 다시 떴다) 다시 건다."""
        view = self._grid.view()
        window = int(view.window) if view is not None else 0
        if window == self._watch.window():
            return
        if window:
            self._watch.start(window)
        else:
            self._watch.stop()

    def stop(self) -> None:
        self._timer.stop()
        self._active = False
        self._watch.stop()

    def is_active(self) -> bool:
        """고르는 단추가 눌린 채인가(끌어 고르거나 진짜 아이콘을 끄는 중)."""
        return self._active

    def item(self) -> int:
        """마지막으로 눌린 진짜 아이콘의 번호. 빈 곳을 눌렀으면 -1."""
        return self._item

    def item_pos(self) -> tuple[int, int] | None:
        """눌린 진짜 아이콘이 그때 있던 자리(물리 픽셀)."""
        return self._item_pos

    def shift_held(self) -> bool:
        """마지막으로 눌렸을 때 Shift 가 눌려 있었는가."""
        return self._shift

    def dragged_far(self) -> bool:
        """누른 뒤 끄는 것으로 볼 만큼 움직였는가."""
        return self._dragging

    def release_pos(self) -> QPoint:
        """마지막으로 단추를 뗀 자리(Qt 좌표)."""
        return to_logical(*self._end)

    # ── 진짜 아이콘의 자리(마지막으로 읽어 둔 것) ──

    def _cells(self) -> list[tuple[int, int, int, int, int]]:
        """진짜 아이콘마다 (번호, 왼쪽, 위, 폭, 높이). 물리 픽셀이다. 끄는 동안에는
        탐색기에 묻지 않는다(답을 기다리다 멈춘다). 읽어 둔 것으로 본다."""
        view = self._grid.view()
        if view is None:
            return []
        off_x, off_y = item_offset(view)
        width = view.spacing[0]
        # 그림과 이름 두 줄이 차지하는 높이(여백 7, 한 줄 15: 배율 100% 에서 잰 값).
        scale = view.dpi / 96.0
        height = min(view.spacing[1],
                     view.icon + int(round(7 + 2 * 15 * scale)))
        indices = getattr(view, "indices", ()) or range(len(view.items))
        return [(int(index), x + off_x, y + off_y, width, height)
                for index, (x, y) in zip(indices, view.items)]

    def _real_icon_at(self, point: tuple[int, int]) -> tuple[int, tuple[int, int] | None]:
        px, py = point
        view = self._grid.view()
        for (index, left, top, width, height), pos in zip(
                self._cells(), view.items if view is not None else ()):
            if left <= px < left + width and top <= py < top + height:
                return index, (int(pos[0]), int(pos[1]))
        return -1, None

    def item_rect(self, index: int) -> QRect | None:
        """그 번호의 진짜 아이콘이 차지하는 자리(Qt 좌표)."""
        for number, left, top, width, height in self._cells():
            if number == index:
                a = to_logical(left, top)
                b = to_logical(left + width - 1, top + height - 1)
                return QRect(a, b)
        return None

    def cell_rect(self, index: int) -> QRect | None:
        """그 번호의 진짜 아이콘이 놓인 칸 전체(Qt 좌표). 우리 아이콘의 창과 같은
        크기다."""
        view = self._grid.view()
        for number, left, top, width, _height in self._cells():
            if number == index:
                a = to_logical(left, top)
                b = to_logical(left + width - 1, top + view.spacing[1] - 1)
                return QRect(a, b)
        return None

    def item_at(self, pos: QPoint) -> int:
        """그 점(Qt 좌표)에 있는 진짜 아이콘의 번호. 없으면 -1."""
        for number, left, top, width, height in self._cells():
            a = to_logical(left, top)
            b = to_logical(left + width - 1, top + height - 1)
            if QRect(a, b).contains(pos):
                return number
        return -1

    def items_in(self, rect: QRect) -> list[int]:
        """그 사각형(Qt 좌표) 안에 가운데가 든 진짜 아이콘들의 번호."""
        out = []
        for number, left, top, width, height in self._cells():
            if rect.contains(to_logical(left + width // 2, top + height // 2)):
                out.append(number)
        return out

    def item_now(self, index: int) -> tuple[int, int] | None:
        """그 번호의 진짜 아이콘이 지금 있는 자리(마지막으로 읽은 것, 물리 픽셀)."""
        view = self._grid.view()
        if view is None:
            return None
        indices = getattr(view, "indices", ()) or range(len(view.items))
        for number, pos in zip(indices, view.items):
            if number == index:
                return int(pos[0]), int(pos[1])
        return None

    # ── 누르고 끄는 것 ──

    def _on_capture(self) -> None:
        if self._active:
            return              # 한 번 누를 때 잇달아 오는 것은 첫 번째만 쓴다
        probe = self._probe
        primary = probe.primary_down()
        if not primary and not probe.secondary_down():
            return
        self._start = probe.cursor_pos()
        self._item, self._item_pos = self._real_icon_at(self._start)
        self._shift = bool(getattr(probe, "shift_down", lambda: False)())
        self._dragging = False
        if primary:
            self._active = True
            self.pressed.emit(bool(probe.adding_keys()))
            self._timer.start()
        else:
            # 오른쪽 단추로 눌렀다. 탐색기가 고른 것을 바꾸므로 우리 것도 맞춘다.
            self.pressed.emit(bool(probe.adding_keys()))

    def _tick(self) -> None:
        probe = self._probe
        if not probe.primary_down():
            self._timer.stop()
            self._active = False
            self._end = probe.cursor_pos()
            self.finished.emit(self._item >= 0)
            return
        x, y = probe.cursor_pos()
        sx, sy = self._start
        if not self._dragging:
            if max(abs(x - sx), abs(y - sy)) < probe.drag_threshold():
                return
            self._dragging = True
        if self._item >= 0:
            # 진짜 아이콘을 끄는 중이다. 사각형이 아니다.
            self.dragged.emit(to_logical(x, y) - to_logical(sx, sy))
            return
        a, b = to_logical(min(sx, x), min(sy, y)), to_logical(max(sx, x), max(sy, y))
        self.moved.emit(QRect(a, b))


class Desk:
    """진짜 바탕화면에 묻고 하는 일(app/desktop_select.py). 바탕화면을 읽지
    못했으면(탐색기가 꺼져 있다, 시험 실행) 아무 일도 하지 않는다."""

    def __init__(self, grid):
        self._grid = grid

    def window(self) -> int:
        view = self._grid.view()
        return int(view.window) if view is not None else 0

    def focus(self) -> bool:
        return desktop_select.focus(self.window())

    def is_focused(self) -> bool:
        return desktop_select.is_focused(self.window())

    def key_down(self, vk: int) -> bool:
        return desktop_select.key_down(vk)

    def selected(self) -> list[int]:
        return desktop_select.selected(self.window()) or []

    def deselect_all(self) -> bool:
        return bool(self.window()) and desktop_select.deselect_all()

    def select(self, indices: list[int], replace: bool) -> bool:
        return bool(self.window()) and desktop_select.select(
            indices, replace=replace)

    def move_by(self, indices: list[int], dx: int, dy: int):
        """옮기기 전의 자리(번호 → 자리)를 돌려준다. 하지 못했으면 None."""
        if not self.window():
            return None
        return desktop_select.move_by(indices, dx, dy)

    def positions(self, indices: list[int]) -> dict:
        return desktop_select.positions(indices) if self.window() else {}

    def place(self, spots: dict) -> bool:
        return bool(self.window()) and desktop_select.place(spots)

    def spacing(self) -> tuple[int, int]:
        view = self._grid.view()
        return tuple(view.spacing) if view is not None else (0, 0)

    def item_count(self) -> int | None:
        return desktop_select.item_count(self.window())

    def bin_index(self) -> int:
        """Windows 휴지통 아이콘의 번호. 바탕화면에 없으면 -1."""
        view = self._grid.view()
        return int(getattr(view, "bin", -1)) if view is not None else -1

    def auto_arrange(self) -> bool:
        view = self._grid.view()
        return bool(getattr(view, "auto", False)) if view is not None else False

    def is_container(self, index: int) -> bool:
        return bool(self.window()) and desktop_select.is_container(index)

    def delete_selected(self) -> bool:
        """탐색기에 고른 진짜 아이콘을 지워 달라고 청한다. 탐색기가 지울지 묻는
        창을 띄우면 답이 올 때까지 걸리므로 화면이 멈추지 않게 따로 부른다."""
        if not self.window():
            return False
        threading.Thread(target=desktop_select.delete_selected, daemon=True).start()
        return True

    def faces(self, indices: list[int]) -> tuple[list[tuple[int, QImage, str]], int]:
        """그 번호의 진짜 아이콘마다 (번호, 그림, 이름)과 그림의 한 변(물리 픽셀)."""
        view = self._grid.view()
        if view is None or not self.window():
            return [], 0
        out = []
        for index, handle, name in desktop_select.faces(indices, int(view.icon)):
            image = QImage.fromHICON(handle)
            desktop_select.destroy_icon(handle)
            if not image.isNull():
                out.append((index, image, name))
        return out, int(view.icon)


class DesktopKeys(QObject):
    """키보드가 바탕화면에 있는 동안 눌린 글쇠를 알아챈다.

    메모 아이콘은 키보드를 가져가지 않으므로 Delete 나 Enter 는 바탕화면으로
    간다. 진짜 파일은 탐색기가 다루고, 고른 메모에는 우리가 같은 일을 한다.
    글쇠를 가로채지 않는다. 눌려 있는지를 물어볼 뿐이다."""

    pressed = Signal(str)       # delete, enter, escape, rename, select_all, move

    def __init__(self, desk, parent: QObject | None = None):
        super().__init__(parent)
        self._desk = desk
        self._focused = False
        self._down: set[str] = set()
        # 방금 바탕화면이 눌렸으면 잠깐 동안은 자주 본다(남은 횟수).
        self._eager = 0
        self._timer = QTimer(self)
        self._timer.setInterval(KEYS_IDLE_MS)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self._focused = False
        self._down.clear()

    def is_focused(self) -> bool:
        return self._focused

    def wake(self) -> None:
        """방금 바탕화면이 눌렸다. 곧 올 글쇠를 놓치지 않게 바로 본다."""
        if self._timer.isActive():
            self._eager = 40
            self._timer.setInterval(KEYS_WATCH_MS)
            self._tick()

    def _held(self) -> set[str]:
        ds, desk = desktop_select, self._desk
        ctrl, alt = desk.key_down(ds.VK_CONTROL), desk.key_down(ds.VK_MENU)
        shift = desk.key_down(ds.VK_SHIFT)
        held: set[str] = set()
        if desk.key_down(ds.VK_DELETE) and not ctrl and not alt:
            held.add("delete")
        if desk.key_down(ds.VK_RETURN) and not ctrl and not alt and not shift:
            held.add("enter")
        if desk.key_down(ds.VK_ESCAPE) and not ctrl and not alt:
            held.add("escape")
        if desk.key_down(ds.VK_F2) and not ctrl and not alt and not shift:
            held.add("rename")
        if desk.key_down(ds.VK_A) and ctrl and not alt and not shift:
            held.add("select_all")
        if (not ctrl and not alt and not shift
                and any(desk.key_down(vk) for vk in ds.VK_MOVES)):
            held.add("move")
        return held

    def _tick(self) -> None:
        focused = bool(self._desk.is_focused())
        if focused != self._focused:
            self._focused = focused
            self._timer.setInterval(KEYS_WATCH_MS if focused else KEYS_IDLE_MS)
            # 바탕화면으로 넘어오기 전부터 눌려 있던 글쇠는 새로 누른 것이 아니다.
            self._down = self._held() if focused else set()
            return
        if not focused:
            if self._eager > 0:
                self._eager -= 1
                if self._eager == 0:
                    self._timer.setInterval(KEYS_IDLE_MS)
            return
        held = self._held()
        fresh = [name for name in ("select_all", "delete", "enter", "escape",
                                   "rename", "move")
                 if name in held and name not in self._down]
        self._down = held
        for name in fresh:
            self.pressed.emit(name)


class IconController(QObject):
    """아이콘 창들이 알려 오는 누름, 끌기, 글쇠, 메뉴를 받아 고른 것을 바꾸고
    고른 것 전체에 일을 한다.

    ``host`` 는 메인 창이다. 쓰는 것: ``icon_widgets()`` (키 → 아이콘 창),
    ``all_icon_widgets()``, ``delete_notes``, ``delete_folder``, ``open_note``,
    ``open_folder_window``, ``hide_notes``, ``move_notes_to_folder``,
    ``realign_icons``, ``rename_folder_icon``."""

    def __init__(self, host, grid, parent: QObject | None = None, *,
                 marquee: DesktopMarquee | None = None, desk=None, ghost=None):
        super().__init__(parent)
        self._host = host
        self._grid = grid
        self._desk = desk or Desk(grid)
        # 끄는 동안 진짜 아이콘이 따라오는 모습(ui/real_ghost.py). 처음 쓸 때 만든다.
        self._ghost = ghost
        self._drag_serial = 0
        self._drag_delta = QPoint()
        # 끌던 것을 놓은 곳에 있던 진짜 아이콘의 종류(ON_BIN, ON_CONTAINER).
        self._drop = ""
        self._faded = False
        # 진짜 아이콘을 잡고 끌기 시작했을 때 바탕화면에 있던 아이콘의 수.
        self._follow_count: int | None = None
        self.selection = IconSelection(self)
        self.selection.changed.connect(self._apply)
        self.marquee = marquee or DesktopMarquee(grid, self)
        self.marquee.pressed.connect(self._on_desktop_pressed)
        self.marquee.moved.connect(self._on_marquee)
        self.marquee.dragged.connect(self._on_real_dragged)
        self.marquee.finished.connect(self._on_desktop_released)
        self.keys = DesktopKeys(self._desk, self)
        self.keys.pressed.connect(self._on_desktop_key)
        self.keys.start()
        # 끌어 고르기 전에 골라 두었던 것(Ctrl·Shift 를 누르고 끌면 거기에 더한다).
        self._base: list[str] = []
        # 고른 것 가운데 하나를 눌렀다. 끌지 않고 떼면 그것만 고른다.
        self._pending_single = ""
        # Shift 로 범위를 고를 때의 기준 아이콘이 차지한 자리(우리 것이든 진짜든).
        self._anchor: QRect | None = None
        # 함께 끄는 아이콘들의 끌기 전 자리(키 → 자리).
        self._group: dict[str, QPoint] = {}
        self._group_dropped = False
        # 탐색기가 고르고 있는 진짜 아이콘의 번호들(마지막으로 읽은 것).
        self._real: list[int] = []
        # 메모 아이콘을 잡고 끌 때 함께 옮길 진짜 아이콘들.
        self._real_group: list[int] = []
        # 진짜 아이콘을 잡고 끌 때 따라가는 우리 아이콘들의 끌기 전 자리.
        self._follow: dict[str, QPoint] = {}

    def stop(self) -> None:
        self.keys.stop()
        self.marquee.stop()
        self._end_ghost()

    # ── 고른 것을 화면에 ──

    def _widgets(self) -> dict:
        return self._host.icon_widgets()

    def _apply(self) -> None:
        for key, widget in self._host.all_icon_widgets().items():
            widget.set_icon_selected(key in self.selection)

    def refresh(self) -> None:
        """아이콘이 생기거나 사라졌다. 없는 것을 고른 것에서 빼고 다시 칠한다."""
        widgets = self._widgets()
        self.selection.keep(widgets)
        self._apply()

    # ── 탐색기 쪽에 고른 것 ──

    def real_selected(self) -> list[int]:
        return list(self._real)

    def _read_real(self) -> None:
        self._real = list(self._desk.selected())

    def _deselect_real(self) -> None:
        if self._real:
            self._desk.deselect_all()
            self._real = []

    def _focus_desktop(self, widget=None) -> None:
        """메모 아이콘은 눌려도 키보드를 가져가지 않는다. 바탕화면이 받게 한다.
        바탕화면을 앞으로 부르지 못했으면(탐색기가 꺼져 있다) 눌린 아이콘이
        받는다. 어느 쪽도 받지 않으면 글쇠가 쓰고 있던 다른 창으로 간다."""
        if not self._desk.focus():
            activate = getattr(widget, "activateWindow", None)
            if activate is not None:
                activate()
        self.keys.wake()

    # ── 아이콘을 눌렀다 ──

    def _range(self, target: QRect) -> tuple[list[str], list[int]]:
        """기준 아이콘과 누른 아이콘 사이의 네모 안에 든 우리 아이콘과 진짜 아이콘."""
        box = (self._anchor or target).united(target)
        keys = [key for key, widget in self._widgets().items()
                if box.contains(widget.geometry().center())]
        return keys, self.marquee.items_in(box)

    def pressed(self, key: str, modifiers) -> None:
        self._pending_single = ""
        widget = self._widgets().get(key)
        rect = QRect(widget.geometry()) if widget is not None else None
        self._read_real()
        ctrl = bool(modifiers & Qt.ControlModifier)
        if (modifiers & Qt.ShiftModifier) and rect is not None \
                and self._anchor is not None:
            keys, real = self._range(rect)
            if key not in keys:
                keys.append(key)
            if ctrl:
                self.selection.set(self.selection.keys() + keys)
            else:
                self.selection.set(keys)
            if real or (self._real and not ctrl):
                self._desk.select(real, not ctrl)
                self._read_real()
        elif ctrl:
            self.selection.toggle(key)
            self._anchor = rect
        elif key in self.selection:
            # 끌지 않고 떼면 이것만 고른다(함께 고른 것을 끌려고 누른 것일 수 있다).
            self._pending_single = key
        else:
            self.selection.select_only(key)
            self._anchor = rect
            self._deselect_real()
        self._focus_desktop(widget)

    def clicked(self, key: str) -> None:
        """끌지 않고 뗐다."""
        if self._pending_single == key:
            self.selection.select_only(key)
            widget = self._widgets().get(key)
            self._anchor = QRect(widget.geometry()) if widget is not None else None
            self._deselect_real()
        self._pending_single = ""

    # ── 함께 끌기(메모 아이콘을 잡았다) ──

    def drag_began(self, key: str) -> None:
        self._pending_single = ""
        self._group, self._group_dropped = {}, False
        self._real_group = []
        self._drop = ""
        self._drag_serial += 1
        self._drag_delta = QPoint()
        self._end_ghost()
        self._grid.set_lifted([])       # 끝맺지 못한 지난 끌기가 남긴 것을 지운다
        if key not in self.selection:
            return
        for other, widget in self._widgets().items():
            if other != key and other in self.selection:
                self._group[other] = QPoint(widget.pos())
        # 함께 고른 진짜 아이콘도 같은 만큼 옮긴다. 그 아이콘들이 비울 칸은
        # 끄는 동안 빈 칸으로 본다(한 칸씩 옆으로 옮길 때 서로의 자리가 된다).
        if 0 < len(self._real) <= desktop_select.MAX_SELECTED:
            self._real_group = list(self._real)
            self._grid.set_lifted(self._real_group)
            # 그림을 읽는 데 잠깐 걸린다. 끌기부터 시작하고 그림은 곧 띄운다.
            serial = self._drag_serial
            QTimer.singleShot(0, lambda: self._begin_ghost(serial))

    def _begin_ghost(self, serial: int) -> None:
        if serial != self._drag_serial or not self._real_group:
            return                      # 그 사이에 끌기가 끝났다
        faces, side = self._desk.faces(self._real_group)
        if any(self.marquee.cell_rect(index) is None for index, _i, _n in faces):
            # 방금 생긴 파일은 읽어 둔 자리에 없다. 바탕화면을 다시 읽는다.
            self._grid.refresh()
        cells = []
        for index, image, name in faces:
            cell = self.marquee.cell_rect(index)
            if cell is not None:
                cells.append((cell, image, name))
        if not cells:
            return
        if self._ghost is None:
            from .real_ghost import RealGhost
            self._ghost = RealGhost()
        self._ghost.begin(cells, side / scale_at(cells[0][0].center()),
                          self._drag_delta)

    def _end_ghost(self) -> None:
        if self._ghost is not None:
            self._ghost.end()

    def _real_under(self, pos: QPoint, *, ask: bool = False) -> str:
        """그 점에 있는 진짜 아이콘의 종류. 함께 끄는 아이콘은 치지 않는다. ``ask``
        가 참이면 폴더처럼 무엇을 담는 아이콘인지도 탐색기에 묻는다."""
        index = self.marquee.item_at(pos)
        if index < 0 or index in self._real or index in self._real_group:
            return ""
        if index == self._desk.bin_index():
            return ON_BIN
        return ON_CONTAINER if ask and self._desk.is_container(index) else ""

    def _set_fade(self, keys: list[str], on: bool) -> None:
        """Windows 휴지통 위에 올라가 있는 동안 끌려가는 것을 흐리게 한다."""
        if on == self._faded:
            return
        self._faded = on
        widgets = self._host.all_icon_widgets()
        for name in keys:
            widget = widgets.get(name)
            if widget is not None:
                widget.setWindowOpacity(DROP_FADE if on else 1.0)
        if self._ghost is not None:
            self._ghost.set_fade(on)

    def drag_moved(self, key: str, delta: QPoint,
                   cursor: QPoint | None = None) -> None:
        widgets = self._widgets()
        for other, origin in self._group.items():
            widget = widgets.get(other)
            if widget is not None:
                widget.move(origin + delta)
        self._drag_delta = QPoint(delta)
        if self._ghost is not None:
            self._ghost.follow(delta)
        self._set_fade([key, *self._group], self._real_under(
            cursor if cursor is not None else QCursor.pos()) == ON_BIN)

    def dropped_on_real(self, key: str, pos: QPoint) -> bool:
        """끌던 것을 진짜 아이콘 위에 놓았는가. Windows 휴지통이면 고른 메모를
        메모 휴지통으로 보내고(함께 고른 파일은 탐색기가 Windows 휴지통으로
        보낸다), 폴더처럼 무엇을 담는 것이면 아무것도 옮기지 않는다. 어느 쪽이든
        참을 돌려주고, 끌던 아이콘은 끌기 전의 자리로 돌아가야 한다."""
        kind = self._real_under(pos, ask=True)
        if not kind:
            return False
        self._drop = kind
        self._group_dropped = True
        return True

    def dropped_on_folder(self, key: str, folder_id: str) -> None:
        """끌던 메모를 메모 폴더 아이콘에 놓았다. 함께 끌던 메모도 들어간다."""
        notes = [split_key(k)[1] for k in self._group if split_key(k)[0] == NOTE]
        if notes:
            self._host.move_notes_to_folder(notes, folder_id)
        self._group_dropped = True

    def drag_ended(self, key: str, moved: bool = True,
                   delta: QPoint | None = None) -> None:
        """끌기가 끝났다. ``moved`` 가 거짓이면 끌던 아이콘이 제자리로 돌아갔다
        (벽에 붙었다). 함께 끌던 것도 돌려보낸다. ``delta`` 는 끌던 아이콘이
        끌기 전의 자리에서 놓인 자리까지 옮겨 간 만큼이다."""
        group, self._group = self._group, {}
        real, self._real_group = self._real_group, []
        drop, self._drop = self._drop, ""
        self._drag_serial += 1
        self._set_fade([key, *group], False)
        self._end_ghost()
        # 폴더로 들어가 보이지 않게 된 것도 제자리로 돌려 둔다.
        widgets = self._host.all_icon_widgets()
        back = self._group_dropped or not moved
        self._group_dropped = False
        for other, origin in group.items():
            widget = widgets.get(other)
            if widget is None:
                continue
            if back:
                widget.place_icon(origin)
            else:
                widget.snap_icon()
        if real:
            self._grid.set_lifted([])
            if not back and delta is not None and not delta.isNull():
                widget = widgets.get(key)
                scale = scale_at(widget.geometry().center()) if widget else 1.0
                dx, dy = (int(round(delta.x() * scale)),
                          int(round(delta.y() * scale)))
                before = self._desk.move_by(real, dx, dy)
                QTimer.singleShot(
                    REALIGN_MS, lambda: self._after_real_move(before, dx, dy))
        if drop == ON_BIN:
            # Windows 휴지통에 놓았다. 메모는 메모 휴지통으로 보내고, 함께 고른
            # 파일은 탐색기에 지워 달라고 청한다.
            self._delete_keys(self.selection.keys() if key in self.selection
                              else [key])
            if real and self._desk.delete_selected():
                QTimer.singleShot(REALIGN_MS, self._after_real_delete)

    def _after_real_delete(self) -> None:
        self._host.realign_icons()
        self._read_real()

    def _after_real_move(self, before, dx: int, dy: int) -> None:
        """진짜 아이콘을 옮긴 뒤. 뜻한 자리와 한 칸 넘게 어긋난 것이 있으면
        (탐색기가 다르게 받아들였다) 옮기기 전의 자리로 모두 돌려놓는다. 사용자의
        바탕화면이 흐트러진 채로 남지 않게 하려는 것이다. '자동 정렬'이 켜져
        있으면 자리는 탐색기가 정하므로 견주지 않는다."""
        if isinstance(before, dict) and before and not self._desk.auto_arrange():
            cx, cy = self._desk.spacing()
            now = self._desk.positions(list(before))
            off = [index for index, (x, y) in before.items()
                   if index in now and cx > 0 and cy > 0
                   and (abs(now[index][0] - (x + dx)) > cx * 1.5
                        or abs(now[index][1] - (y + dy)) > cy * 1.5)]
            if off:
                self._desk.place(before)
        # 옮겨진 진짜 아이콘과 같은 칸에 놓인 우리 아이콘은 비켜 준다.
        self._host.realign_icons()
        self._read_real()

    # ── 글쇠 ──

    def key(self, key: str, qt_key: int, modifiers) -> bool:
        """아이콘이 받은 글쇠(이름을 고친 뒤처럼 아이콘 창이 키보드를 쥐고 있을
        때). 여기서 다뤘으면 True."""
        if qt_key == Qt.Key_Escape:
            self.selection.clear()
            return True
        if qt_key == Qt.Key_A and modifiers & Qt.ControlModifier:
            self.selection.set(self._widgets().keys())
            return True
        if qt_key == Qt.Key_Delete:
            # 글쇠를 받은 아이콘이 고른 것에 없으면(Ctrl 로 뺐다) 그것만 지운다.
            if key not in self.selection:
                self.selection.select_only(key)
            self.delete_selected()
            return True
        if (qt_key in (Qt.Key_Return, Qt.Key_Enter) and key in self.selection
                and len(self.selection) > 1):
            self.open_selected()
            return True
        return False

    def _on_desktop_key(self, name: str) -> None:
        """키보드가 바탕화면에 있을 때 눌린 글쇠. 진짜 파일에는 탐색기가 같은 일을
        스스로 한다."""
        if name == "select_all":
            self.selection.set(self._widgets().keys())
            QTimer.singleShot(SNAPSHOT_MS, self._read_real)
            return
        if not len(self.selection):
            return
        if name == "delete":
            self.delete_selected()
        elif name == "enter":
            self.open_selected()
        elif name == "escape":
            self.selection.clear()
            self._read_real()
            self._deselect_real()
        elif name == "move":
            # 방향키는 진짜 아이콘 하나로 선택을 옮긴다. 우리 것은 풀린다.
            self.selection.clear()
        elif name == "rename":
            folders = self.selection.of(FOLDER)
            self._read_real()
            if len(self.selection) == 1 and folders and not self._real:
                self._host.rename_folder_icon(folders[0])

    # ── 고른 것 전체에 하는 일 ──

    def open_selected(self) -> None:
        for nid in self.selection.of(NOTE):
            self._host.open_note(nid)
        for fid in self.selection.of(FOLDER):
            self._host.open_folder_window(fid)

    def hide_selected(self) -> None:
        self._host.hide_notes(self.selection.of(NOTE))

    def delete_selected(self) -> None:
        """메모는 휴지통으로 보낸다. 폴더는 하나씩 묻고 지운다."""
        self._delete_keys(self.selection.keys())

    def _delete_keys(self, keys: list[str]) -> None:
        parts = [split_key(key) for key in keys]
        notes = [ident for kind, ident in parts if kind == NOTE]
        if notes:
            self._host.delete_notes(notes)
        for kind, ident in parts:
            if kind == FOLDER:
                self._host.delete_folder(ident)

    def menu(self, key: str, global_pos: QPoint) -> bool:
        """아이콘을 오른쪽 단추로 눌렀다. 여러 개를 골라 둔 상태면 고른 것 전체에
        쓰는 메뉴를 띄우고 True. 하나면 그 아이콘이 제 메뉴를 띄운다."""
        if key not in self.selection:
            self.selection.select_only(key)
            self._read_real()
            self._deselect_real()
        if len(self.selection) < 2:
            return False
        notes = self.selection.of(NOTE)
        menu = edit_menu.new_menu()
        ink = "#3A3A3A"
        add_centered_menu_action(
            menu, svg_icon("unfold", color=ink), i18n.t("icons.menu_open"),
            self.open_selected)
        if notes:
            add_centered_menu_action(
                menu, svg_icon("eye_off", color=ink), i18n.t("sticky.more_hide"),
                self.hide_selected)
        menu.addSeparator()
        add_centered_menu_action(
            menu, svg_icon("trash", color=ink), i18n.t("common.btn_delete"),
            self.delete_selected)
        edit_menu.run_menu(menu, global_pos)
        menu.deleteLater()
        return True

    # ── 바탕화면이 눌렸다 ──

    def _on_desktop_pressed(self, adding: bool) -> None:
        self.keys.wake()
        marquee = self.marquee
        item = marquee.item()
        self._follow = {}
        # 함께 골라 둔 진짜 아이콘을 눌렀다. 탐색기는 고른 것을 그대로 두고(끌 수
        # 있게), 끌지 않고 떼면 그 아이콘만 고른다. 우리도 같게 한다.
        grouped = item >= 0 and item in self._real and len(self.selection) > 0
        if item >= 0 and marquee.shift_held() and self._anchor is not None:
            # Shift 로 범위를 골랐다. 그 네모 안의 우리 아이콘도 고른다.
            rect = marquee.item_rect(item)
            if rect is not None:
                keys, _real = self._range(rect)
                self.selection.set(keys)
            self._base = self.selection.keys()
            return
        self._base = self.selection.keys() if (adding or grouped) else []
        if grouped and not adding and marquee.is_active():
            widgets = self._widgets()
            self._follow = {key: QPoint(widgets[key].pos())
                            for key in self.selection.keys() if key in widgets}
            self._follow_count = self._desk.item_count()
        elif not adding and not grouped:
            self.selection.clear()
        if item >= 0 and not marquee.shift_held():
            self._anchor = marquee.item_rect(item)
        elif item < 0 and not adding:
            self._anchor = None

    def _on_marquee(self, rect: QRect) -> None:
        hits = []
        for key, widget in self._widgets().items():
            widget.set_marquee(rect)
            if widget.icon_hit(rect):
                hits.append(key)
        self.selection.set(self._base + hits)

    def _on_real_dragged(self, delta: QPoint) -> None:
        """진짜 아이콘을 잡고 끄는 중이다. 함께 고른 우리 아이콘이 따라간다."""
        widgets = self._widgets()
        for key, origin in self._follow.items():
            widget = widgets.get(key)
            if widget is not None:
                widget.move(origin + delta)

    def _on_desktop_released(self, real_drag: bool) -> None:
        for widget in self._widgets().values():
            widget.set_marquee(None)
        self._base = []
        follow, self._follow = self._follow, {}
        marquee = self.marquee
        if follow:
            if not marquee.dragged_far():
                # 누르기만 했다. 탐색기는 그 아이콘만 고른다.
                self.selection.clear()
            else:
                item, before = marquee.item(), marquee.item_pos()
                on_bin = self._real_under(marquee.release_pos()) == ON_BIN
                QTimer.singleShot(
                    REALIGN_MS,
                    lambda: self._settle_follow(follow, item, before, on_bin))
        elif real_drag:
            # 진짜 아이콘이 우리 아이콘의 칸에 놓였을 수 있다. 비켜 준다.
            QTimer.singleShot(REALIGN_MS, self._host.realign_icons)
        QTimer.singleShot(SNAPSHOT_MS, self._read_real)

    def _settle_follow(self, follow: dict, item: int, before,
                       on_bin: bool = False) -> None:
        """진짜 아이콘을 끌어다 놓은 뒤. 그 아이콘이 실제로 옮겨졌으면 따라간
        우리 아이콘을 그 자리의 칸에 놓고, 아니면(끌다 그만뒀다, 다른 창이나
        폴더에 놓았다) 끌기 전의 자리로 돌려보낸다. Windows 휴지통에 놓았으면
        탐색기가 파일을 지운 것을 본 뒤에 따라간 메모를 메모 휴지통으로 보낸다."""
        self._grid.refresh()
        after = self.marquee.item_now(item)
        if (not on_bin and before is not None and after is not None
                and after != before):
            # 우리 아이콘은 따라간 자리에서 가장 가까운 빈 칸에 놓인다.
            self._host.realign_icons()
        else:
            widgets = self._host.all_icon_widgets()
            for key, origin in follow.items():
                widget = widgets.get(key)
                if widget is not None:
                    widget.place_icon(origin)
        self._read_real()
        if on_bin:
            self._await_bin(list(follow), self._follow_count, BIN_TRIES)

    def _await_bin(self, keys: list[str], count: int | None, tries: int) -> None:
        """탐색기가 끌어다 놓은 파일을 지웠는지 본다. 바탕화면의 아이콘이 줄었으면
        지운 것이다. 사용자가 탐색기의 묻는 창에서 그만뒀거나, 그 사이에 다른 것을
        골랐으면 메모는 그대로 둔다."""
        if not count or not all(key in self.selection for key in keys):
            return
        now = self._desk.item_count()
        if now is not None and now < count:
            self._delete_keys(keys)
            self._host.realign_icons()
            self._read_real()
        elif tries > 0:
            QTimer.singleShot(
                BIN_POLL_MS, lambda: self._await_bin(keys, count, tries - 1))


__all__ = ["Desk", "DesktopKeys", "DesktopMarquee", "FOLDER", "IconController",
           "IconSelection", "NOTE", "ON_BIN", "ON_CONTAINER", "folder_key",
           "note_key", "scale_at", "split_key", "to_logical"]
