"""메모 폴더 아이콘을 열면 뜨는 창: 그 폴더에 든 메모를 보여 준다.

보기 방식은 셋이다. 탐색기처럼 아이콘으로 늘어놓거나, 메인 창의 목록처럼 카드를
리스트나 그리드로 놓는다. 고른 방식은 설정에 적어 두어 다음에도 그대로 열린다.

아이콘 보기에서는 파일처럼 다룬다: 한 번 눌러 고르고 두 번 눌러 연다. Ctrl 과
Shift 로 여러 개를 고르고, 빈 곳을 끌어 사각형으로 고른다. Enter 는 열기, Delete 는
지우기, Ctrl+A 는 모두 고르기, 방향키는 옆 아이콘으로.

여기에는 화면만 있다. 메모를 열고 지우는 일은 메인 창이 한다."""
from __future__ import annotations

import weakref
from typing import Any, Callable

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QRubberBand,
    QScrollArea,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import desktop_icons, i18n
from ..settings import (
    VIEW_COMPACT,
    VIEW_DETAILED,
    VIEW_GRID,
    VIEW_ICONS,
    VIEW_LIST,
    VIEW_STANDARD,
)
from . import window_frame
from . import edit_menu
from . import note_fold as nf
from .icons import svg_icon
from .menu_item import add_centered_menu_action
from .note_card import GRID_MIN_COLUMN, CardFlowLayout, NoteCard
from .theme import LIGHT_MENU_QSS, RoundedScrollBar

# 아이콘 한 칸과 그림의 크기(바탕화면의 기본 크기와 같다), 둘레의 여백.
ICON_SIDE = float(nf.ICON_DEFAULT)
CELL = QSize(nf.ICON_CELL_DEFAULT.width() + 10, nf.ICON_CELL_DEFAULT.height())
MARGIN = 10
INK = QColor("#1F1F1F")


class NoteIconView(QWidget):
    """메모를 탐색기의 아이콘 보기처럼 늘어놓는다."""

    activated = Signal(str)                 # 그 메모를 열어 달라
    deleteRequested = Signal(list)          # 고른 메모를 지워 달라
    visibilityRequested = Signal(list, bool)    # 고른 메모를 바탕화면에 보이거나 숨겨 달라
    selectionChanged = Signal()
    # 제 메뉴를 띄우지 않는 보기(휴지통)에서 오른쪽 단추로 눌렀다: 화면에서의 자리.
    menuRequested = Signal(QPoint)

    def __init__(self, parent: QWidget | None = None, *, own_menu: bool = True):
        super().__init__(parent)
        self._own_menu = own_menu
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self._items: list[dict[str, Any]] = []
        self._selected: list[str] = []
        self._anchor = ""
        self._hover = ""
        self._pending_single = ""
        self._band_origin: QPoint | None = None
        self._band_base: list[str] = []
        self._band = QRubberBand(QRubberBand.Rectangle, self)

    # ── 내용 ──

    def set_items(self, items: list[dict[str, Any]]) -> None:
        """``items`` 의 항목: id, title, color, private, hidden, content."""
        self._items = list(items)
        alive = {item["id"] for item in self._items}
        kept = [nid for nid in self._selected if nid in alive]
        changed, self._selected = kept != self._selected, kept
        if self._anchor not in alive:
            self._anchor = ""
        if self._hover not in alive:
            self._hover = ""
        self._relayout()
        self.update()
        if changed:
            self.selectionChanged.emit()

    def ids(self) -> list[str]:
        return [item["id"] for item in self._items]

    def selected_ids(self) -> list[str]:
        return list(self._selected)

    def select(self, ids: list[str]) -> None:
        alive = set(self.ids())
        ids = [nid for nid in dict.fromkeys(ids) if nid in alive]
        if ids != self._selected:
            self._selected = ids
            self.update()
            self.selectionChanged.emit()

    # ── 자리 ──

    def _columns(self) -> int:
        return max(1, (self.width() - 2 * MARGIN) // CELL.width())

    def cell_rect(self, index: int) -> QRectF:
        row, col = divmod(index, self._columns())
        return QRectF(MARGIN + col * CELL.width(), MARGIN + row * CELL.height(),
                      CELL.width(), CELL.height())

    def _relayout(self) -> None:
        rows = -(-len(self._items) // self._columns()) if self._items else 0
        self.setMinimumHeight(rows * CELL.height() + 2 * MARGIN)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._relayout()

    def _plate(self, index: int) -> QRectF:
        return nf.icon_plate_rect(self.cell_rect(index), self._items[index]["title"],
                                  self.font(), ICON_SIDE, self.devicePixelRatioF())

    def index_at(self, pos: QPoint) -> int:
        for index in range(len(self._items)):
            if self._plate(index).contains(pos):
                return index
        return -1

    def _hit(self, index: int, rect: QRect) -> bool:
        box = QRectF(rect)
        return any(box.intersects(r) for r in nf.icon_hit_rects(
            self.cell_rect(index), self._items[index]["title"], self.font(),
            ICON_SIDE, self.devicePixelRatioF()))

    # ── 그리기 ──

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        for index, item in enumerate(self._items):
            cell = self.cell_rect(index)
            if not cell.intersects(QRectF(event.rect())):
                continue
            # 바탕화면에서 숨긴 메모는 숨긴 파일처럼 옅게 보인다.
            p.setOpacity(0.5 if item.get("hidden") else 1.0)
            nf.paint_icon(
                p, cell, item["color"], item["title"],
                private=bool(item.get("private")),
                hover=item["id"] == self._hover, font=self.font(),
                thumb=nf.note_thumbnail(item.get("content") or ""),
                icon=ICON_SIDE, selected=item["id"] in self._selected, ink=INK)
        p.end()

    # ── 마우스 ──

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        self.setFocus(Qt.MouseFocusReason)
        index = self.index_at(event.position().toPoint())
        mods = event.modifiers()
        self._pending_single = ""
        if index < 0:
            # 빈 곳: 끌면 사각형으로 고른다. Ctrl 을 누르고 있으면 고른 것에 더한다.
            self._band_origin = event.position().toPoint()
            self._band_base = list(self._selected) if mods & Qt.ControlModifier else []
            if not mods & Qt.ControlModifier:
                self.select([])
            return
        nid = self._items[index]["id"]
        if mods & Qt.ControlModifier:
            self.select([n for n in self._selected if n != nid]
                        if nid in self._selected else self._selected + [nid])
            self._anchor = nid
        elif mods & Qt.ShiftModifier and self._anchor in self.ids():
            a, b = sorted((self.ids().index(self._anchor), index))
            self.select(self.ids()[a:b + 1])
        elif nid in self._selected:
            self._pending_single = nid
        else:
            self.select([nid])
            self._anchor = nid

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        pos = event.position().toPoint()
        if self._band_origin is not None and event.buttons() & Qt.LeftButton:
            rect = QRect(self._band_origin, pos).normalized()
            self._band.setGeometry(rect)
            self._band.show()
            hits = [self._items[i]["id"] for i in range(len(self._items))
                    if self._hit(i, rect)]
            self.select(self._band_base + hits)
            return
        index = self.index_at(pos)
        hover = self._items[index]["id"] if index >= 0 else ""
        if hover != self._hover:
            self._hover = hover
            self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.LeftButton:
            return super().mouseReleaseEvent(event)
        banded = self._band_origin is not None
        self._band_origin = None
        self._band.hide()
        if banded:
            return
        index = self.index_at(event.position().toPoint())
        if index < 0:
            return
        nid = self._items[index]["id"]
        if self._pending_single == nid:
            self.select([nid])
            self._anchor = nid
        self._pending_single = ""
        plain = not (event.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier))
        if plain and desktop_icons.single_click_opens():
            self.activated.emit(nid)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        index = self.index_at(event.position().toPoint())
        if event.button() == Qt.LeftButton and index >= 0:
            self._pending_single = ""
            self.activated.emit(self._items[index]["id"])

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self._hover:
            self._hover = ""
            self.update()
        super().leaveEvent(event)

    # ── 글쇠 ──

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        if key in (Qt.Key_Return, Qt.Key_Enter) and self._selected:
            for nid in list(self._selected):
                self.activated.emit(nid)
        elif key == Qt.Key_Delete and self._selected:
            self.deleteRequested.emit(list(self._selected))
        elif key == Qt.Key_A and event.modifiers() & Qt.ControlModifier:
            self.select(self.ids())
        elif key == Qt.Key_Escape and self._selected:
            self.select([])
        elif key in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down):
            self._step(key)
        else:
            return super().keyPressEvent(event)
        event.accept()

    def _step(self, key: int) -> None:
        """방향키: 옆(위아래) 아이콘으로 옮겨 그것만 고른다."""
        ids = self.ids()
        if not ids:
            return
        current = ids.index(self._anchor) if self._anchor in ids else -1
        if current < 0:
            target = 0
        else:
            step = {Qt.Key_Left: -1, Qt.Key_Right: 1, Qt.Key_Up: -self._columns(),
                    Qt.Key_Down: self._columns()}[key]
            target = current + step
            if not 0 <= target < len(ids):
                return
        self._anchor = ids[target]
        self.select([ids[target]])

    # ── 메뉴 ──

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        index = self.index_at(event.pos())
        if index < 0:
            return
        item = self._items[index]
        if item["id"] not in self._selected:
            self.select([item["id"]])
            self._anchor = item["id"]
        chosen = list(self._selected)
        if not self._own_menu:
            self.menuRequested.emit(event.globalPos())
            event.accept()
            return
        # 메뉴의 항목이 이 창을 붙들고 있지 않게 약하게만 가리킨다. 붙들고 있으면
        # 메뉴가 지워지는 순간에 창이 함께 지워질 수 있다.
        me = weakref.ref(self)

        def act(what: str) -> None:
            view = me()
            if view is None:
                return
            if what == "open":
                for nid in chosen:
                    view.activated.emit(nid)
            elif what == "delete":
                view.deleteRequested.emit(chosen)
            else:
                view.visibilityRequested.emit(chosen, what == "show")

        menu = edit_menu.new_menu(self)
        ink = "#3A3A3A"
        add_centered_menu_action(
            menu, svg_icon("unfold", color=ink), i18n.t("icons.menu_open"),
            lambda: act("open"))
        if item.get("hidden"):
            add_centered_menu_action(
                menu, svg_icon("eye_on", color=ink), i18n.t("folderwin.menu_show"),
                lambda: act("show"))
        else:
            add_centered_menu_action(
                menu, svg_icon("eye_off", color=ink), i18n.t("sticky.more_hide"),
                lambda: act("hide"))
        menu.addSeparator()
        add_centered_menu_action(
            menu, svg_icon("trash", color=ink), i18n.t("common.btn_delete"),
            lambda: act("delete"))
        edit_menu.run_menu(menu, event.globalPos())
        menu.deleteLater()
        event.accept()


class FolderWindow(QWidget):
    noteActivated = Signal(str)
    deleteRequested = Signal(list)
    visibilityRequested = Signal(list, bool)
    closed = Signal(str)

    def __init__(self, folder_id: str, settings, *,
                 wire_card: Callable[[NoteCard], None] | None = None,
                 icon: QIcon | None = None, parent: QWidget | None = None,
                 view_keys: tuple[str, str] = ("folder_view_layout",
                                               "folder_view_density"),
                 own_menu: bool = True):
        """``wire_card`` 는 카드 보기의 카드 한 장을 메인 창의 처리에 잇는다
        (지우기, 이력, 나만 보기, 폴더 옮기기는 메인 창의 목록과 똑같이 된다).
        ``view_keys`` 는 고른 보기 방식과 밀도를 적어 둘 설정의 이름이다."""
        super().__init__(parent, Qt.Window)
        self.folder_id = folder_id
        self._settings = settings
        self._wire_card = wire_card
        self._layout_key, self._density_key = view_keys
        self._cards: dict[str, NoteCard] = {}
        self._folder_context: list[tuple[str, str]] = []
        # 닫아도 지우지 않는다(메인 창이 들고 있다가 다시 열 때 그대로 쓴다).
        if icon is not None and not icon.isNull():
            self.setWindowIcon(icon)
        self.resize(540, 400)
        self.setMinimumSize(300, 220)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        bar = QHBoxLayout()
        bar.setContentsMargins(14, 8, 10, 8)
        self._count = QLabel("")
        self._count.setProperty("role", "subtle")
        bar.addWidget(self._count, 1)
        self._view_btn = QToolButton()
        self._view_btn.setCursor(Qt.PointingHandCursor)
        self._view_btn.setToolTip(i18n.t("main.tooltip_view"))
        self._view_btn.setAccessibleName(i18n.t("main.tooltip_view"))
        self._view_btn.setAutoRaise(True)
        self._view_btn.clicked.connect(self._show_view_menu)
        bar.addWidget(self._view_btn)
        root.addLayout(bar)

        self._stack = QStackedWidget(self)
        root.addWidget(self._stack, 1)

        self._icons = NoteIconView(own_menu=own_menu)
        self._icons.activated.connect(self.noteActivated.emit)
        self._icons.deleteRequested.connect(self.deleteRequested.emit)
        self._icons.visibilityRequested.connect(self.visibilityRequested.emit)
        self._icon_scroll = self._scroller(self._icons, "#FFFFFF")
        self._stack.addWidget(self._icon_scroll)

        self._cards_container = QWidget()
        self._cards_layout = CardFlowLayout(self._cards_container, spacing=8)
        self._cards_layout.setContentsMargins(12, 10, 12, 12)
        self._card_scroll = self._scroller(self._cards_container, "#FFFFFF")
        self._stack.addWidget(self._card_scroll)

        self._empty = QLabel(i18n.t("folderwin.empty"))
        self._empty.setAlignment(Qt.AlignCenter)
        self._empty.setProperty("role", "subtle")
        self._stack.addWidget(self._empty)

        self._has_notes = False
        self._refresh_view()
        window_frame.install(self, window_frame.WINDOW)

    @staticmethod
    def _scroller(inner: QWidget, background: str) -> QScrollArea:
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        area.setVerticalScrollBar(RoundedScrollBar())
        area.setStyleSheet(f"QScrollArea {{ background: {background}; }}")
        inner.setAutoFillBackground(True)
        palette = inner.palette()
        palette.setColor(inner.backgroundRole(), QColor(background))
        inner.setPalette(palette)
        area.setWidget(inner)
        return area

    # ── 내용 ──

    def icon_view(self) -> NoteIconView:
        return self._icons

    def cards(self) -> dict[str, NoteCard]:
        return self._cards

    def set_content(self, name: str, items: list[dict[str, Any]],
                    folder_context: list[tuple[str, str]] | None = None) -> None:
        """폴더 이름과 그 안의 메모들. ``items`` 의 항목: id, title, color,
        private, hidden, content, note(카드에 넘길 메모 그대로)."""
        self.setWindowTitle(name or i18n.t("main.folder_default_name"))
        self._folder_context = list(folder_context or [])
        self._count.setText(i18n.t("folderwin.count", count=len(items)))
        self._icons.set_items(items)
        self._sync_cards(items)
        self._has_notes = bool(items)
        self._refresh_view()

    def _sync_cards(self, items: list[dict[str, Any]]) -> None:
        wanted = [item["id"] for item in items]
        for nid in [n for n in self._cards if n not in wanted]:
            card = self._cards.pop(nid)
            self._cards_layout.removeWidget(card)
            card.setParent(None)
            card.deleteLater()
        for position, item in enumerate(items):
            note, visible = item.get("note") or {}, not item.get("hidden")
            card = self._cards.get(item["id"])
            if card is None:
                card = NoteCard(note, visible=visible)
                if self._wire_card is not None:
                    self._wire_card(card)
                self._cards[item["id"]] = card
            else:
                card.update_from(note, visible=visible)
                self._cards_layout.removeWidget(card)
            card.set_folder_context(self._folder_context)
            self._dress_card(card, item)
            self._cards_layout.insertWidget(position, card)
        self._apply_card_view()

    def _dress_card(self, card: NoteCard, item: dict[str, Any]) -> None:
        """카드 한 장에 그 항목만의 것을 입힌다(물려받는 창이 쓴다)."""

    # ── 보기 방식 ──

    def _layout_mode(self) -> str:
        return getattr(self._settings, self._layout_key)

    def _density(self) -> str:
        return getattr(self._settings, self._density_key)

    def _apply_card_view(self) -> None:
        layout = VIEW_GRID if self._layout_mode() == VIEW_GRID else VIEW_LIST
        grid = layout == VIEW_GRID
        compact = self._density() == VIEW_COMPACT
        self._cards_layout.set_min_column_width(GRID_MIN_COLUMN if grid else 0)
        self._cards_layout.setSpacing(5 if (compact and not grid) else 8)
        for card in self._cards.values():
            card.set_view(self._density(), layout)
        self._cards_layout.invalidate()
        self._cards_container.updateGeometry()

    def _refresh_view(self) -> None:
        mode = self._layout_mode()
        if not self._has_notes:
            self._stack.setCurrentWidget(self._empty)
        elif mode == VIEW_ICONS:
            self._stack.setCurrentWidget(self._icon_scroll)
        else:
            self._apply_card_view()
            self._stack.setCurrentWidget(self._card_scroll)
        name = {VIEW_GRID: "view_grid", VIEW_LIST: "view_list"}.get(mode, "folder")
        self._view_btn.setIcon(svg_icon(name, color="#4B5563"))

    def set_view(self, *, layout: str | None = None,
                 density: str | None = None) -> None:
        s = self._settings
        if layout is not None:
            setattr(s, self._layout_key, layout)
        if density is not None:
            setattr(s, self._density_key, density)
        s.save()
        self._refresh_view()

    def refresh_view(self) -> None:
        """다른 폴더 창에서 보기 방식을 바꿨다. 이 창도 따른다."""
        self._refresh_view()

    def _show_view_menu(self) -> None:
        me = weakref.ref(self)      # 메뉴의 항목이 이 창을 붙들고 있지 않게

        def choose(**how) -> None:
            window = me()
            if window is not None:
                window.set_view(**how)

        menu = QMenu(self._view_btn)
        menu.setStyleSheet(LIGHT_MENU_QSS)
        add_centered_menu_action(
            menu, None, i18n.t("main.view_section_layout"), header=True)
        for key, label, icon in ((VIEW_ICONS, "folderwin.view_icons", "folder"),
                                 (VIEW_LIST, "main.view_list", "view_list"),
                                 (VIEW_GRID, "main.view_grid", "view_grid")):
            add_centered_menu_action(
                menu, svg_icon(icon, color="#3A3A3A"), i18n.t(label),
                lambda k=key: choose(layout=k),
                checked=(self._layout_mode() == key))
        if self._layout_mode() != VIEW_ICONS:
            menu.addSeparator()
            add_centered_menu_action(
                menu, None, i18n.t("main.view_section_density"), header=True)
            for key, label in ((VIEW_COMPACT, "main.view_compact"),
                               (VIEW_STANDARD, "main.view_standard"),
                               (VIEW_DETAILED, "main.view_detailed")):
                add_centered_menu_action(
                    menu, None, i18n.t(label),
                    lambda k=key: choose(density=k),
                    checked=(self._density() == key))
        pos = self._view_btn.mapToGlobal(
            QPoint(self._view_btn.width() - menu.sizeHint().width(),
                   self._view_btn.height() + 2))
        edit_menu.run_menu(menu, pos)
        menu.deleteLater()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.closed.emit(self.folder_id)
        super().closeEvent(event)


__all__ = ["CELL", "FolderWindow", "ICON_SIDE", "NoteIconView"]
