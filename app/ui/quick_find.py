"""어디서나 메모 찾기: 단축키로 화면 위 가운데에 뜨는 찾기 창.

치는 대로 제목과 본문에서 찾는다. 위아래 화살표로 고르고 Enter 로 그 메모를 앞으로
꺼낸다. Esc 나 다른 곳을 누르면 닫힌다."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QLayout,
    QGraphicsDropShadowEffect,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QStyledItemDelegate,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from .. import i18n
from .icons import svg_icon

MAX_ROWS = 8
_ROW_H = 52
_WIDTH = 560
_SHADOW = 18                      # 그림자가 들어갈 바깥 여백


def find_notes(notes: list[dict], query: str, limit: int = MAX_ROWS) -> list[dict]:
    """``notes`` 는 ``{"id", "title", "body", "folder", "updated_at"}``. 찾는 말이
    없으면 최근에 고친 순. 있으면 제목에 든 것이 먼저, 그다음 본문에 든 것(각각
    최근 순). 결과마다 본문에서 찾은 줄을 ``snippet`` 으로 붙인다."""
    q = (query or "").strip().lower()
    recent = sorted(notes, key=lambda n: -int(n.get("updated_at") or 0))
    if not q:
        return [dict(n, snippet=_line_after_title(n)) for n in recent[:limit]]
    in_title, in_body = [], []
    for n in recent:
        title = (n.get("title") or "").lower()
        body = n.get("body") or ""
        if q in title:
            in_title.append(dict(n, snippet=_line_after_title(n)))
        else:
            line = _line_with(body, q)
            if line is not None:
                in_body.append(dict(n, snippet=line))
    return (in_title + in_body)[:limit]


def _line_after_title(n: dict) -> str:
    """제목 다음의 첫 본문 줄(제목과 같은 줄은 건너뛴다)."""
    title = (n.get("title") or "").strip()
    for line in (n.get("body") or "").splitlines():
        line = line.strip()
        if line and line != title:
            return line
    return ""


def _first_line(body: str) -> str:
    for line in (body or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def _line_with(body: str, q: str) -> str | None:
    for line in (body or "").splitlines():
        if q in line.lower():
            return line.strip()
    return None


class _RowDelegate(QStyledItemDelegate):
    """한 줄: 제목(굵게)과 그 아래 찾은 줄, 오른쪽에 폴더."""

    def sizeHint(self, option, index) -> QSize:
        return QSize(option.rect.width(), _ROW_H)

    def paint(self, p: QPainter, option, index) -> None:
        data = index.data(Qt.UserRole) or {}
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(option.rect).adjusted(6, 2, -6, -2)
        if option.state & QStyle.State_Selected:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#F0F0F0"))
            p.drawRoundedRect(r, 8, 8)
        color = QColor(data.get("color") or "#FFE066")
        p.setPen(QColor(0, 0, 0, 46))          # 흰 메모도 견본이 보이게
        p.setBrush(color)
        p.drawRoundedRect(QRectF(r.left() + 12.5, r.center().y() - 5.5, 11, 11), 3, 3)
        text_left = r.left() + 38
        folder = data.get("folder") or ""
        f = QFont(option.font)
        f.setPixelSize(12)
        folder_w = 0
        if folder:
            p.setFont(f)
            folder_w = min(140, p.fontMetrics().horizontalAdvance(folder) + 4)
            p.setPen(QColor("#8A8A8A"))
            p.drawText(QRectF(r.right() - 12 - folder_w, r.top(), folder_w, r.height()),
                       Qt.AlignVCenter | Qt.AlignRight,
                       p.fontMetrics().elidedText(folder, Qt.ElideRight, folder_w))
        width = r.right() - 12 - (folder_w + 12 if folder else 0) - text_left
        t = QFont(option.font)
        t.setPixelSize(14)
        t.setWeight(QFont.DemiBold)
        p.setFont(t)
        p.setPen(QColor("#1F1F1F"))
        title = data.get("title") or ""
        snippet = data.get("snippet") or ""
        two = bool(snippet) and snippet != title
        # 본문 줄이 없으면 제목을 줄의 가운데에(견본, 폴더와 같은 높이).
        title_rect = (QRectF(text_left, r.top() + 6, width, 20) if two
                      else QRectF(text_left, r.top(), width, r.height()))
        p.drawText(title_rect, Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(title, Qt.ElideRight, int(width)))
        if two:
            p.setFont(f)
            p.setPen(QColor("#6B6B6B"))
            p.drawText(QRectF(text_left, r.top() + 26, width, 18),
                       Qt.AlignLeft | Qt.AlignVCenter,
                       p.fontMetrics().elidedText(snippet, Qt.ElideRight, int(width)))
        p.restore()


class QuickFind(QWidget):
    """찾기 창. ``picked(note_id)`` 를 내고 닫힌다. 메모 목록은 열 때마다
    ``open_at`` 에 넘긴다."""

    picked = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle(i18n.t("hotkey.act_find"))
        self._notes: list[dict] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(_SHADOW, _SHADOW - 6, _SHADOW, _SHADOW + 6)
        # 창 높이가 결과 줄 수를 바로 따른다(한 글자 늦지 않게).
        outer.setSizeConstraint(QLayout.SetFixedSize)
        self._card = QFrame(self)
        self._card.setObjectName("QuickFindCard")
        self._card.setStyleSheet(
            "QFrame#QuickFindCard { background: #FFFFFF; border: 1px solid #E3E3E3;"
            " border-radius: 12px; }")
        shadow = QGraphicsDropShadowEffect(self._card)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 56))
        self._card.setGraphicsEffect(shadow)
        outer.addWidget(self._card)

        v = QVBoxLayout(self._card)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(4)
        self._input = QLineEdit(self._card)
        self._input.setObjectName("QuickFindInput")
        self._input.setPlaceholderText(i18n.t("hotkey.find_placeholder"))
        self._input.addAction(svg_icon("search", color="#888888"), QLineEdit.LeadingPosition)
        self._input.setStyleSheet(
            "QLineEdit#QuickFindInput { border: none; background: transparent;"
            " font-size: 16px; min-height: 40px; max-height: 40px; padding: 0 6px; }")
        self._input.textChanged.connect(self._refresh)
        self._input.installEventFilter(self)
        v.addWidget(self._input)

        self._line = QFrame(self._card)
        self._line.setFixedHeight(1)
        self._line.setStyleSheet("background: #EFEFEF; border: none;")
        v.addWidget(self._line)

        self._list = QListWidget(self._card)
        self._list.setObjectName("QuickFindList")
        self._list.setItemDelegate(_RowDelegate(self._list))
        self._list.setFrameShape(QFrame.NoFrame)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._list.setFocusPolicy(Qt.NoFocus)
        self._list.setStyleSheet("QListWidget#QuickFindList { background: transparent;"
                                 " border: none; outline: none; }")
        self._list.itemClicked.connect(lambda item: self._pick(item))
        v.addWidget(self._list)

        self._empty = QLabel(i18n.t("hotkey.find_empty"), self._card)
        self._empty.setAlignment(Qt.AlignCenter)
        self._empty.setStyleSheet("color: #8A8A8A; font-size: 13px; padding: 14px;")
        v.addWidget(self._empty)
        self.setFixedWidth(_WIDTH + 2 * _SHADOW)

    # ── 열고 닫기 ──

    def open_at(self, notes: list[dict], at: QPoint | None = None) -> None:
        """``notes`` 로 찾는다. ``at`` 이 있는 화면의 위 가운데에 띄운다."""
        self._notes = list(notes)
        self._input.clear()
        self._refresh()
        screen = QGuiApplication.screenAt(at or QCursor.pos()) or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry() if screen is not None else QRect(0, 0, 1280, 720)
        self.adjustSize()
        x = avail.left() + (avail.width() - self.width()) // 2
        y = avail.top() + int(avail.height() * 0.16) - _SHADOW
        self.move(x, max(avail.top(), y))
        self.show()
        self.raise_()
        self.activateWindow()
        self._input.setFocus(Qt.OtherFocusReason)

    def changeEvent(self, event) -> None:
        if event.type() == QEvent.ActivationChange and not self.isActiveWindow() \
                and self.isVisible():
            self.hide()
        super().changeEvent(event)

    # ── 찾기 ──

    def results(self) -> list[str]:
        """지금 보이는 결과의 메모 id(시험용)."""
        return [self._list.item(i).data(Qt.UserRole)["id"] for i in range(self._list.count())]

    def _refresh(self) -> None:
        found = find_notes(self._notes, self._input.text())
        self._list.clear()
        for n in found:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, n)
            item.setSizeHint(QSize(_WIDTH - 16, _ROW_H))
            self._list.addItem(item)
        if found:
            self._list.setCurrentRow(0)
        self._list.setFixedHeight(len(found) * _ROW_H + (4 if found else 0))
        self._list.setVisible(bool(found))
        self._empty.setVisible(not found and bool(self._input.text().strip()))
        self._line.setVisible(bool(found) or self._empty.isVisible())
        self.adjustSize()

    def _pick(self, item: QListWidgetItem | None = None) -> None:
        item = item or self._list.currentItem()
        if item is None:
            return
        note_id = (item.data(Qt.UserRole) or {}).get("id")
        self.hide()
        if note_id:
            self.picked.emit(note_id)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self._input and event.type() == QEvent.KeyPress:
            key = event.key()
            if key == Qt.Key_Escape:
                self.hide()
                return True
            if key in (Qt.Key_Return, Qt.Key_Enter):
                self._pick()
                return True
            if key in (Qt.Key_Down, Qt.Key_Up) and self._list.count():
                row = self._list.currentRow() + (1 if key == Qt.Key_Down else -1)
                self._list.setCurrentRow(max(0, min(self._list.count() - 1, row)))
                return True
        return super().eventFilter(obj, event)


__all__ = ["QuickFind", "find_notes"]
