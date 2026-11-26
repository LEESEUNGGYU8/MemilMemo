"""인쇄 미리 보기: 찍힐 쪽을 먼저 보고 '인쇄' 를 누르면 Windows 의 프린터 창이 뜬다.

미리 보기는 프린터 창에서 고를 기본 프린터와 종이로 그린다. 프린터 창에서 종이나 방향을
바꾸면 찍을 때 그 종이에 맞춰 다시 나눈다(``note_print.paint_pages``)."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QPageLayout
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import i18n
from . import window_frame
from .icons import svg_icon
from .theme import refresh_style


class PrintPreviewDialog(QDialog):
    """쪽을 보여 주고, 인쇄를 누르면 프린터 창을 띄워 찍는다. ``printed`` 는 찍었는가."""

    def __init__(self, printer, paint: Callable[[object], int], *, total: int,
                 parent: QWidget | None = None) -> None:
        from PySide6.QtPrintSupport import QPrintPreviewWidget
        super().__init__(parent)
        self.setWindowTitle(i18n.t("print.preview"))
        self.setModal(True)
        self.printed = False
        self._printer = printer
        self._paint = paint
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.view = QPrintPreviewWidget(printer, self)
        # 쪽 뒤 바탕은 옅은 회색(Qt 기본의 짙은 회색 대신).
        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import QFrame, QGraphicsView
        for gv in self.view.findChildren(QGraphicsView):
            gv.setBackgroundBrush(QColor("#ECECEC"))
            gv.setFrameShape(QFrame.NoFrame)
        self.view.paintRequested.connect(lambda dev: self._paint(dev))
        self.view.setViewMode(QPrintPreviewWidget.SinglePageView)
        self.view.setZoomMode(QPrintPreviewWidget.FitInView)
        root.addWidget(self.view, 1)

        bar = QWidget(self)
        bar.setObjectName("PreviewBar")
        bar.setStyleSheet("QWidget#PreviewBar { background: #FFFFFF;"
                          " border-top: 1px solid #E6E6E6; }")
        row = QHBoxLayout(bar)
        row.setContentsMargins(16, 10, 16, 12)
        row.setSpacing(6)
        self._prev = self._nav("chevron_left", i18n.t("print.prev_page"), -1)
        self._next = self._nav("chevron_right", i18n.t("print.next_page"), 1)
        self._page = QLabel(self)
        self._page.setStyleSheet("color: #3A3A3A; font-size: 12px; background: transparent;")
        self._page.setMinimumWidth(64)
        self._page.setAlignment(Qt.AlignCenter)
        row.addWidget(self._prev)
        row.addWidget(self._page)
        row.addWidget(self._next)
        row.addSpacing(10)
        self._fit = QToolButton(self)
        self._fit.setCheckable(True)
        self._fit.setText(i18n.t("print.fit_width"))
        self._fit.setCursor(Qt.PointingHandCursor)
        self._fit.setStyleSheet(
            "QToolButton { border: 1px solid #D2D2D2; border-radius: 6px; padding: 4px 10px;"
            " font-size: 12px; color: #1F1F1F; background: #FFFFFF; }"
            "QToolButton:hover { background: #F4F4F4; }"
            "QToolButton:checked { background: #EDEDED; }")
        self._fit.toggled.connect(self._set_fit)
        row.addWidget(self._fit)
        row.addStretch(1)
        cancel = QPushButton(i18n.t("common.btn_cancel"), self)
        cancel.setProperty("secondary", True)
        refresh_style(cancel)
        self._print = QPushButton(i18n.t("print.menu_print"), self)
        self._print.setDefault(True)
        for b in (cancel, self._print):
            b.setCursor(Qt.PointingHandCursor)
            row.addWidget(b)
        cancel.clicked.connect(self.reject)
        self._print.clicked.connect(self._print_now)
        root.addWidget(bar)
        self.view.previewChanged.connect(self._show_page)
        self.resize(720, 820)
        window_frame.install(self, window_frame.DIALOG)
        self._total = total
        self._show_page()

    def _nav(self, icon: str, tip: str, step: int) -> QToolButton:
        b = QToolButton(self)
        b.setIcon(svg_icon(icon, color="#3A3A3A"))
        b.setToolTip(tip)
        b.setAccessibleName(tip)
        b.setCursor(Qt.PointingHandCursor)
        b.setFixedSize(28, 28)
        b.setStyleSheet("QToolButton { border: none; border-radius: 6px; background: transparent; }"
                        "QToolButton:hover { background: #F0F0F0; }"
                        "QToolButton:disabled { background: transparent; }")
        b.clicked.connect(lambda: self._go(step))
        return b

    def _go(self, step: int) -> None:
        self.view.setCurrentPage(max(1, min(self.view.pageCount(),
                                            self.view.currentPage() + step)))
        self._show_page()

    def _set_fit(self, wide: bool) -> None:
        from PySide6.QtPrintSupport import QPrintPreviewWidget
        self.view.setZoomMode(QPrintPreviewWidget.FitToWidth if wide
                              else QPrintPreviewWidget.FitInView)

    def _show_page(self) -> None:
        count = max(1, self.view.pageCount() or self._total)
        current = max(1, min(count, self.view.currentPage()))
        self._page.setText(i18n.t("print.page_of", page=current, total=count))
        self._prev.setEnabled(current > 1)
        self._next.setEnabled(current < count)

    def _print_now(self) -> None:
        """Windows 의 프린터 창을 띄워 찍는다. 프린터 창을 닫으면 미리 보기로 돌아온다."""
        from PySide6.QtPrintSupport import QAbstractPrintDialog, QPrintDialog
        dialog = QPrintDialog(self._printer, self)
        dialog.setOption(QAbstractPrintDialog.PrintPageRange, True)
        dialog.setOption(QAbstractPrintDialog.PrintSelection, False)
        dialog.setOption(QAbstractPrintDialog.PrintCurrentPage, False)
        dialog.setMinMax(1, max(1, self.view.pageCount() or self._total))
        if not dialog.exec():
            # 프린터나 종이를 바꿨을 수 있다. 미리 보기를 다시 그린다.
            self.view.updatePreview()
            return
        self.printed = self._paint(self._printer) > 0
        self.accept()


def page_layout_size(printer):
    return printer.pageLayout().fullRect(QPageLayout.Point).size()


__all__ = ["PrintPreviewDialog"]
