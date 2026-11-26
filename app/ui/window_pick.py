"""창에 붙이기(실험): 붙일 창을 고르는 창. 지금 보이는 창 목록에서 고르고, 붙일 조건을
정한다(제목에 이 글이 든 창, 또는 그 프로그램의 모든 창)."""
from __future__ import annotations

from PySide6.QtCore import QFileInfo, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter
from PySide6.QtWidgets import (
    QDialog,
    QFileIconProvider,
    QStyle,
    QStyledItemDelegate,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from .. import i18n
from .. import window_attach as wa
from . import window_frame
from .theme import refresh_style


def program_name(win: wa.WindowInfo) -> str:
    """보기 좋은 프로그램 이름: 'EXCEL' 은 'Excel', 그 밖은 실행 파일 이름 그대로."""
    name = win.program or ""
    return name.capitalize() if name.isupper() and len(name) > 3 else name


_ICONS: dict[str, QIcon] = {}


def program_icon(win: wa.WindowInfo) -> QIcon:
    """그 창의 프로그램 아이콘(실행 파일의 아이콘). 모르면 빈 아이콘."""
    exe = win.exe or ""
    icon = _ICONS.get(exe)
    if icon is None:
        icon = QIcon()
        if exe:
            try:
                icon = QFileIconProvider().icon(QFileInfo(exe))
            except Exception:  # noqa: BLE001  아이콘을 못 읽어도 목록은 보인다
                icon = QIcon()
        _ICONS[exe] = icon
    return icon


class _Row(QStyledItemDelegate):
    """창 한 줄: 프로그램 아이콘, 제목(줄임표), 오른쪽에 옅은 프로그램 이름. 고른 줄은 옅은 판."""

    def sizeHint(self, option, index) -> QSize:
        return QSize(option.rect.width(), 34)

    def paint(self, p: QPainter, option, index) -> None:
        title, program = index.data(Qt.UserRole) or ("", "")
        p.save()
        r = option.rect.adjusted(2, 1, -2, -1)
        if option.state & QStyle.State_Selected:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#ECECEC"))
            p.drawRoundedRect(r, 6, 6)
        f = QFont(option.font)
        f.setPixelSize(12)
        p.setFont(f)
        fm = p.fontMetrics()
        prog_w = min(110, fm.horizontalAdvance(program) + 2)
        p.setPen(QColor("#8A8A8A"))
        p.drawText(QRect(r.right() - 10 - prog_w, r.top(), prog_w, r.height()),
                   Qt.AlignVCenter | Qt.AlignRight, fm.elidedText(program, Qt.ElideRight, prog_w))
        icon = index.data(Qt.DecorationRole)
        left = r.left() + 10
        if isinstance(icon, QIcon) and not icon.isNull():
            icon.paint(p, QRect(left, r.top() + (r.height() - 16) // 2, 16, 16))
        left += 16 + 10
        f.setPixelSize(13)
        p.setFont(f)
        fm = p.fontMetrics()
        width = r.right() - 10 - prog_w - 12 - left
        p.setPen(QColor("#1F1F1F"))
        p.drawText(QRect(left, r.top(), width, r.height()), Qt.AlignVCenter | Qt.AlignLeft,
                   fm.elidedText(title, Qt.ElideRight, width))
        p.restore()


class WindowPickDialog(QDialog):
    """``windows`` 가운데 하나를 고른다. ``picked()`` 는 (창, 제목 조건). 제목 조건이 비면
    그 프로그램의 모든 창."""

    def __init__(self, windows: list[wa.WindowInfo], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(i18n.t("attach_win.menu"))
        self.setModal(True)
        self._windows = list(windows)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 16)
        root.setSpacing(10)

        self._list = QListWidget(self)
        self._list.setMinimumSize(400, 200)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._list.setItemDelegate(_Row(self._list))
        self._list.setStyleSheet("QListWidget { border: 1px solid #E3E3E3; border-radius: 8px;"
                                 " padding: 2px; outline: none; }")
        for win in self._windows:
            item = QListWidgetItem(win.title)
            item.setData(Qt.UserRole, (win.title, program_name(win)))
            item.setData(Qt.DecorationRole, program_icon(win))
            item.setToolTip(win.title)
            self._list.addItem(item)
        self._list.currentRowChanged.connect(self._on_row)
        root.addWidget(self._list)
        self._empty = QLabel(i18n.t("attach_win.empty"), self)
        self._empty.setAlignment(Qt.AlignCenter)
        self._empty.setMinimumSize(400, 200)
        self._empty.setStyleSheet("color: #6B6B6B; font-size: 13px;")
        self._empty.setVisible(not self._windows)
        self._list.setVisible(bool(self._windows))
        root.addWidget(self._empty)

        self._by_title = QRadioButton(i18n.t("attach_win.by_title"), self)
        self._title = QLineEdit(self)
        self._by_program = QRadioButton("", self)
        self._by_title.setChecked(True)
        self._title.textEdited.connect(lambda _t: self._by_title.setChecked(True))
        root.addWidget(self._by_title)
        indent = QHBoxLayout()
        indent.setContentsMargins(24, 0, 0, 0)
        indent.addWidget(self._title)
        root.addLayout(indent)
        root.addWidget(self._by_program)
        for w in (self._by_title, self._title, self._by_program):
            w.setVisible(bool(self._windows))

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        ok = buttons.button(QDialogButtonBox.Ok)
        ok.setText(i18n.t("attach_win.btn"))
        cancel = buttons.button(QDialogButtonBox.Cancel)
        cancel.setText(i18n.t("common.btn_cancel"))
        cancel.setProperty("secondary", True)
        refresh_style(cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._ok = ok
        root.addWidget(buttons)
        if self._windows:
            self._list.setCurrentRow(0)
        else:
            ok.hide()
        window_frame.install(self, window_frame.DIALOG)

    def _on_row(self, row: int) -> None:
        if 0 <= row < len(self._windows):
            win = self._windows[row]
            self._title.setText(win.title)
            self._by_program.setText(i18n.t("attach_win.by_program", name=program_name(win)))
            self._ok.setEnabled(True)

    def picked(self) -> tuple[wa.WindowInfo, str] | None:
        row = self._list.currentRow()
        if not 0 <= row < len(self._windows):
            return None
        title = self._title.text().strip() if self._by_title.isChecked() else ""
        return self._windows[row], title


def pick_window(parent: QWidget | None, windows: list[wa.WindowInfo]):
    """창을 띄워 묻는다. 고르면 (창, 제목 조건), 그만두면 None. 시험에서 바꿔 끼운다."""
    dlg = WindowPickDialog(windows, parent)
    try:
        if dlg.exec() != QDialog.Accepted:
            return None
        return dlg.picked()
    finally:
        dlg.deleteLater()


__all__ = ["WindowPickDialog", "pick_window"]
