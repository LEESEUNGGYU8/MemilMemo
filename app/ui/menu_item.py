from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QWidget,
    QWidgetAction,
)


class CenteredMenuItem(QWidget):
    """아이콘을 고정 폭 칼럼에 가운데 정렬해, 아이콘 유무와 무관하게 텍스트
    들여쓰기를 일정하게 유지한다."""

    activated = Signal()

    ICON_COL_WIDTH = 40
    ROW_HEIGHT = 30
    ICON_SIZE = 16

    def __init__(self, icon: QIcon | None, text: str, *,
                 enabled: bool = True, checked: bool = False,
                 header: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_Hover, True)
        self.setMouseTracking(True)
        self.setFixedHeight(self.ROW_HEIGHT)
        self.setCursor(Qt.PointingHandCursor if enabled else Qt.ArrowCursor)

        row = QHBoxLayout(self)
        row.setContentsMargins(4, 0, 18, 0)
        row.setSpacing(0)

        icon_lbl = QLabel(self)
        icon_lbl.setFixedWidth(self.ICON_COL_WIDTH)
        icon_lbl.setAlignment(Qt.AlignCenter)
        if icon is not None and not icon.isNull():
            icon_lbl.setPixmap(icon.pixmap(self.ICON_SIZE, self.ICON_SIZE))
        row.addWidget(icon_lbl)

        self._text_lbl = QLabel(text, self)
        if header:
            # 묶음 이름표: 누를 수 없지만 흐린 항목과는 달라 보여야 한다.
            style = ("color: #6B6B6B; font-size: 11px; font-weight: 700; "
                     "background: transparent;")
        elif not enabled:
            style = "color: #A0A0A0; font-size: 13px; background: transparent;"
        elif checked:
            style = ("color: #1F1F1F; font-size: 13px; font-weight: 700; "
                     "background: transparent;")
        else:
            style = "color: #1F1F1F; font-size: 13px; background: transparent;"
        self._text_lbl.setStyleSheet(style)
        row.addWidget(self._text_lbl, 1)

        if checked:
            # 고른 항목은 색이 아니라 굵은 글자와 체크 표시로 알린다.
            from .icons import svg_icon
            mark = QLabel(self)
            mark.setFixedWidth(22)
            mark.setAlignment(Qt.AlignCenter)
            mark.setPixmap(svg_icon("check", color="#1F1F1F").pixmap(14, 14))
            mark.setStyleSheet("background: transparent;")
            row.addWidget(mark)

        self._hover = False
        self._enabled = enabled
        self.setAccessibleName(text)

    def set_active(self, active: bool) -> None:
        """글쇠로 옮겨 온 항목도 마우스를 올린 것처럼 강조한다."""
        active = bool(active) and self._enabled
        if active != self._hover:
            self._hover = active
            self.update()

    def enterEvent(self, event) -> None:  # noqa: N802
        if self._enabled:
            self._hover = True
            self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if (self._enabled
                and event.button() == Qt.LeftButton
                and self.rect().contains(event.position().toPoint())):
            self.activated.emit()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        if not self._hover:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#F0F0F0"))
        p.drawRoundedRect(self.rect().adjusted(2, 1, -2, -1), 5, 5)


def add_centered_menu_action(menu: QMenu, icon: QIcon | None, text: str,
                              handler=None, *,
                              enabled: bool = True, checked: bool = False,
                              header: bool = False) -> QWidgetAction:
    if header:
        enabled = False
    widget = CenteredMenuItem(icon, text, enabled=enabled, checked=checked,
                              header=header)
    action = QWidgetAction(menu)
    action.setDefaultWidget(widget)
    action.setEnabled(enabled)
    # 화면 낭독기와 글쇠 이동이 이 항목을 알아보게 글자도 넣는다.
    action.setText(text)
    if enabled and handler is not None:
        widget.activated.connect(handler)
        widget.activated.connect(menu.close)
        # 마우스는 위젯이 직접 받지만, 방향키로 옮겨 Enter 를 누르면 메뉴가
        # 액션을 실행한다. 여기에 잇지 않으면 아무 일 없이 닫힌다.
        action.triggered.connect(handler)
    if enabled:
        # 방향키로 옮겨 다닐 때 지금 항목이 어디인지 보이게 한다.
        menu.hovered.connect(
            lambda current, a=action, w=widget: w.set_active(current is a))
    menu.addAction(action)
    return action
