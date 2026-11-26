"""위젯 편집기: 그림 자리 위에 올리는 진짜 위젯.

위에는 그림과 같은 모양을 그리는 판(canvas)이 있고, 종류에 따라 아래에 입력칸이
붙는다. 사용자가 누르면 공유 PC 로 보낼 동작을 ``opRequested(dict)`` 로 낸다.
데이터는 직접 바꾸지 않는다. 공유 PC 가 나눈 새 데이터를 ``set_data`` 로 받는다."""
from __future__ import annotations

import copy
from datetime import date

from PySide6.QtCore import QEvent, QPoint, Qt, Signal
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QMenu, QSizePolicy, QVBoxLayout, QWidget

from .base import Ctx, Hit, WidgetError, clean_name
from .draw import clamp_width, paint_widget
from .texts import tr

_MENU_QSS = (
    "QMenu { background-color: #FFFFFF; border: 1px solid #EAEAEA; "
    "border-radius: 8px; padding: 4px; color: #1F1F1F; }"
    "QMenu::item { background-color: transparent; "
    "padding: 6px 22px 6px 14px; border-radius: 6px; color: #1F1F1F; min-width: 130px; }"
    "QMenu::item:selected { background-color: #F0F0F0; color: #1F1F1F; }"
    "QMenu::separator { height: 1px; background: #EAEAEA; margin: 4px 6px; }")


class _Canvas(QWidget):
    """그림을 그리고 누른 자리를 찾는 판."""

    def __init__(self, editor: "WidgetEditor") -> None:
        super().__init__(editor)
        self._editor = editor
        self._hits: list[Hit] = []
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_Hover)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.resize(300, 100)

    def refresh(self) -> None:
        ed = self._editor
        h = max(24, int(ed.kind.measure(ed.current(), clamp_width(self.width()), ed.ctx())))
        if self.height() != h or self.minimumHeight() != h:
            self.setFixedHeight(h)
        self.update()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if event.oldSize().width() != event.size().width():
            self.refresh()

    def paintEvent(self, _event) -> None:  # noqa: N802
        ed = self._editor
        p = QPainter(self)
        try:
            self._hits = paint_widget(ed.kind, p, ed.current(), clamp_width(self.width()),
                                      self.height(), ed.ctx())
        finally:
            p.end()

    def compute_hits(self) -> list[Hit]:
        """화면에 띄우지 않아도 누를 자리를 구한다(빈 그림에 한 번 그린다)."""
        ed = self._editor
        width = clamp_width(self.width())
        height = max(24, int(ed.kind.measure(ed.current(), width, ed.ctx())))
        img = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        p = QPainter(img)
        try:
            self._hits = paint_widget(ed.kind, p, ed.current(), width, height, ed.ctx())
        finally:
            p.end()
        return list(self._hits)

    def hit_at(self, x: float, y: float, *, menu: bool = False) -> Hit | None:
        """그 자리의 누를 것. ``menu`` 면 오른쪽 단추 메뉴용 자리(달력의 날)도 본다."""
        for hit in reversed(self._hits):
            if hit.contains(x, y) and (menu or not hit.action.get("menu")):
                return hit
        return None

    def _set_view(self, key: str, value) -> None:
        view = self._editor.view
        if view.get(key) != value:
            view[key] = value
            self.update()

    def event(self, event) -> bool:  # noqa: N802
        t = event.type()
        if t == QEvent.HoverEnter:
            self._set_view("card_hover", True)
        elif t == QEvent.HoverLeave:
            self._set_view("card_hover", False)
            self._set_view("hover", None)
        return super().event(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        pos = event.position()
        hit = self.hit_at(pos.x(), pos.y())
        if hit is not None:
            # 누르는 동안 짙은 바탕을 보이고, 같은 자리에서 떼면 동작한다.
            self._set_view("pressed", dict(hit.action))
        # 누를 것이 없는 자리도 뒤의 본문으로 보내지 않는다(본문 커서가 움직이지 않게).
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        # 두 번 누르기도 위젯 안에서 끝난다(본문의 그림 보기나 낱말 고르기로 가지 않는다).
        self.mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        pressed = self._editor.view.get("pressed")
        if event.button() != Qt.LeftButton or pressed is None:
            return super().mouseReleaseEvent(event)
        self._set_view("pressed", None)
        pos = event.position()
        hit = self.hit_at(pos.x(), pos.y())
        if hit is not None and hit.action == pressed:
            self._editor.trigger(dict(hit.action), self.hit_rect(hit))
        event.accept()

    def hit_rect(self, hit: Hit):
        from PySide6.QtCore import QRect
        return QRect(int(hit.x), int(hit.y), int(round(hit.w)), int(round(hit.h)))

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        pos = event.position()
        hit = self.hit_at(pos.x(), pos.y())
        self.setCursor(Qt.PointingHandCursor if hit is not None else Qt.ArrowCursor)
        # 그림만 있는 단추(달력의 이전 달, 다음 달, '⋯')는 말풍선으로 이름을 알린다.
        tip = hit.action.get("tip") if hit is not None else None
        text = tr(tip) if isinstance(tip, str) else ""
        if not text:
            # 줄임표로 줄인 글은 온전한 글을 말풍선으로 보인다.
            for rect, full in self._editor.view.get("tips", []):
                if rect.contains(pos):
                    text = full
                    break
        self.setToolTip(text)
        self._set_view("hover", dict(hit.action) if hit is not None else None)
        super().mouseMoveEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        ed = self._editor
        if ed.readonly:
            return
        items = ed.kind.context_actions(ed, event.pos().x(), event.pos().y())
        if not items:
            return
        menu = QMenu(self)
        menu.setStyleSheet(_MENU_QSS)
        for label, action, checked in items:
            if action is None:
                menu.addSeparator()
                continue
            act = menu.addAction(label)
            if checked is not None:
                act.setCheckable(True)
                act.setChecked(bool(checked))
            act.triggered.connect(lambda _=False, a=action: ed.trigger(a))
        menu.exec(event.globalPos())


class WidgetEditor(QWidget):
    """한 위젯의 편집기. ``opRequested`` 로 동작을 낸다."""

    opRequested = Signal(dict)
    # 판 밖의 일(위젯 메뉴, 설정 열기): (동작, 누른 자리)
    localRequested = Signal(dict, object)

    def __init__(self, kind, data, viewer: str, today: date, *, background: str = "#FFFFFF",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.kind = kind
        self.viewer = clean_name(viewer)
        self.today = today
        self.background = background
        self.view: dict = {}
        self.readonly = False
        self.extras: dict[str, QWidget] = {}
        self._data: dict = kind.new_data("")
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(6)
        self.canvas = _Canvas(self)
        box.addWidget(self.canvas)
        kind.build_extras(self)
        self.set_data(data)

    # ── 데이터 ──

    def current(self) -> dict:
        return self._data

    def data(self) -> dict:
        return copy.deepcopy(self._data)

    def set_data(self, data) -> None:
        """공유 PC 가 나눈 새 데이터. 받을 수 없으면 읽기만 한다."""
        try:
            self._data = self.kind.validate(data)
            self.readonly = False
        except WidgetError:
            self.readonly = True
        self.kind.sync_extras(self)
        self.canvas.refresh()

    def set_today(self, today: date) -> None:
        self.today = today
        self.canvas.refresh()

    def set_background(self, color: str) -> None:
        self.background = color
        self.canvas.update()

    def ctx(self) -> Ctx:
        return Ctx(self.viewer, self.today, self.background, not self.readonly, self.view)

    def is_owner(self) -> bool:
        return self.kind.is_owner(self._data, self.viewer)

    # ── 동작 ──

    def request(self, op: dict) -> None:
        """공유 PC 로 보낼 동작을 낸다. 읽기만 할 때는 내지 않는다."""
        if self.readonly or not self.viewer:
            return
        self.opRequested.emit(dict(op))

    # 판이 아니라 위젯 칸이 하는 일.
    OUTSIDE = ("menu", "setup", "input")

    def trigger(self, action: dict, rect=None) -> None:
        if "op" in action:
            self.request(action)
        elif action.get("local") in self.OUTSIDE:
            self.localRequested.emit(dict(action), rect)
        elif "local" in action:
            self.kind.local_action(self, action)
            self.canvas.refresh()

    def hits(self) -> list[Hit]:
        return self.canvas.compute_hits()

    def find_hit(self, **match) -> Hit | None:
        """동작의 값이 모두 같은 첫 자리(시험과 글쇠 조작용)."""
        for hit in self.hits():
            if all(hit.action.get(k) == v for k, v in match.items()):
                return hit
        return None

    def press(self, x: float, y: float) -> bool:
        """그 자리를 누른 것처럼 한다. 누를 것이 있었으면 True."""
        self.hits()
        hit = self.canvas.hit_at(x, y)
        if hit is None:
            return False
        self.trigger(hit.action)
        return True

    def hit_point(self, hit: Hit) -> QPoint:
        x, y = hit.center()
        return QPoint(int(x), int(y))
