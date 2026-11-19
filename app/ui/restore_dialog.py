from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import i18n
from ..backup import backup_time
from ..datetime_fmt import fmt_relative
from .theme import refresh_style


def _size_text(path: Path) -> str:
    try:
        size = path.stat().st_size
    except OSError:
        return ""
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{max(1, round(size / 1024))} KB"


class RestoreDialog(QDialog):
    """되살릴 백업을 고른다. 새것이 맨 위에 있고 처음부터 골라져 있다."""

    def __init__(self, backups: list[Path], parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(i18n.t("restore.window_title"))
        self.setMinimumSize(380, 340)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(10)

        head = QLabel(i18n.t("restore.label_pick"))
        head.setStyleSheet("font-size: 12.5px; color: #4B5563;")
        root.addWidget(head)

        self._list = QListWidget()
        self._list.setAccessibleName(i18n.t("restore.label_pick"))
        for path in backups:
            when = fmt_relative(int(backup_time(path) * 1000))
            size = _size_text(path)
            item = QListWidgetItem(f"{when}    {size}" if size else when)
            item.setData(Qt.UserRole, str(path))
            item.setToolTip(str(path))
            self._list.addItem(item)
        if self._list.count():
            self._list.setCurrentRow(0)
        self._list.itemDoubleClicked.connect(lambda _item: self.accept())
        root.addWidget(self._list, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        ok = buttons.button(QDialogButtonBox.Ok)
        cancel = buttons.button(QDialogButtonBox.Cancel)
        ok.setText(i18n.t("restore.btn_restore"))
        cancel.setText(i18n.t("common.btn_cancel"))
        cancel.setProperty("secondary", True)
        refresh_style(cancel)
        ok.setMinimumWidth(110)
        cancel.setMinimumWidth(110)
        ok.setEnabled(self._list.count() > 0)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def selected(self) -> Path | None:
        item = self._list.currentItem()
        if item is None:
            return None
        return Path(item.data(Qt.UserRole))
