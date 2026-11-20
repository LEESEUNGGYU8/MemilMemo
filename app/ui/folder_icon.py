"""바탕 화면에 두는 메모 폴더 아이콘.

폴더에 든 메모를 한 아이콘으로 접어 둔다. 두 번 누르면 폴더가 열려 그 폴더의
메모가 바탕 화면에 나타나고, 다시 두 번 누르면 닫혀 아이콘 하나만 남는다.
어느 폴더를 아이콘으로 둘지와 열림 상태는 이 PC 에만 저장한다(다른 사람의
바탕 화면은 바뀌지 않는다)."""
from __future__ import annotations

import ctypes
import platform

from PySide6.QtCore import QEasingCurve, QPoint, QRect, QRectF, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QGuiApplication, QMouseEvent, QPainter
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from .. import config, i18n
from . import note_fold as nf
from . import dwm
from .theme import LIGHT_MENU_QSS


def _pin_to_bottom(hwnd: int) -> None:
    if platform.system() != "Windows" or hwnd == 0:
        return
    try:
        ctypes.windll.user32.SetWindowPos(hwnd, 1, 0, 0, 0, 0,
                                          0x0002 | 0x0001 | 0x0010)
    except Exception:
        pass


class FolderIconWidget(QWidget):
    toggled = Signal(str)                 # folder_id: 열림/닫힘을 바꿔 달라
    moved = Signal(str, int, int)         # folder_id, x, y
    removeRequested = Signal(str)         # folder_id: 아이콘을 치워 달라

    def __init__(self, folder_id: str, name: str, color: str, *,
                 settings=None, parent: QWidget | None = None):
        super().__init__(parent, Qt.FramelessWindowHint | Qt.Tool)
        self.setWindowTitle(i18n.t("app.name"))
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setFixedSize(nf.ICON_SIZE)
        self.setCursor(Qt.PointingHandCursor)
        self.folder_id = folder_id
        self._name = name or ""
        self._color = color or config.DEFAULT_COLOR
        self._opened = False
        self._count = 0
        self._hover = False
        self._settings = settings
        self._press: QPoint | None = None
        self._grab = QPoint()
        self._dragging = False
        self._slot_resolver = None
        self._slide: QVariantAnimation | None = None
        self.setToolTip(self._name)

    # ── 상태 ────────────────────────────────────────────────────────────

    def set_folder(self, name: str, color: str) -> None:
        self._name = name or ""
        self._color = color or config.DEFAULT_COLOR
        self.setToolTip(self._name)
        self.update()

    def set_state(self, *, opened: bool, count: int) -> None:
        if (opened, count) != (self._opened, self._count):
            self._opened = bool(opened)
            self._count = max(0, int(count))
            self.update()

    def is_opened(self) -> bool:
        return self._opened

    def set_slot_resolver(self, resolver) -> None:
        """``resolver(owner_key, pos, screen_rect) -> QPoint`` 는 다른 아이콘과
        겹치지 않는 격자 자리를 돌려준다."""
        self._slot_resolver = resolver

    def owner_key(self) -> str:
        return "folder:" + self.folder_id

    # ── 자리 ────────────────────────────────────────────────────────────

    def _avail(self) -> QRect:
        scr = QGuiApplication.screenAt(self.geometry().center())
        if scr is None:
            scr = QGuiApplication.primaryScreen()
        return scr.availableGeometry() if scr is not None else QRect(
            0, 0, 1920, 1080)

    def _snapped(self, pos: QPoint) -> QPoint:
        avail = self._avail()
        if self._slot_resolver is not None:
            try:
                return QPoint(self._slot_resolver(self.owner_key(), pos, avail))
            except Exception:
                pass
        return nf.snap_icon_pos(pos, avail, [])

    def place(self, x: int, y: int) -> None:
        self.move(int(x), int(y))

    def _animations_on(self) -> bool:
        s = self._settings
        if s is not None and not getattr(s, "fold_animation", True):
            return False
        return self.isVisible() and nf.system_animations_enabled()

    def _slide_to(self, pos: QPoint) -> None:
        if self._slide is not None:
            self._slide.stop()
            self._slide.deleteLater()
            self._slide = None
        if pos == self.pos():
            return
        if not self._animations_on():
            self.move(pos)
            return
        anim = QVariantAnimation(self)
        anim.setStartValue(self.pos())
        anim.setEndValue(pos)
        anim.setDuration(nf.SLIDE_MS)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.valueChanged.connect(
            lambda v: self.move(v) if isinstance(v, QPoint) else None)
        self._slide = anim
        anim.start()

    # ── 마우스 ──────────────────────────────────────────────────────────

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._press = event.globalPosition().toPoint()
            self._grab = self._press - self.frameGeometry().topLeft()
            self._dragging = False
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._press is not None and (event.buttons() & Qt.LeftButton):
            gp = event.globalPosition().toPoint()
            if not self._dragging:
                if ((gp - self._press).manhattanLength()
                        < QApplication.startDragDistance()):
                    event.accept()
                    return
                self._dragging = True
            self.move(gp - self._grab)
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton and self._press is not None:
            dragged = self._dragging
            self._press = None
            self._dragging = False
            if dragged:
                target = self._snapped(self.pos())
                self._slide_to(target)
                self.moved.emit(self.folder_id, target.x(), target.y())
        event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._press = None
            self.toggled.emit(self.folder_id)
        event.accept()

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.toggled.emit(self.folder_id)
            event.accept()
            return
        super().keyPressEvent(event)

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        menu.setStyleSheet(LIGHT_MENU_QSS)
        toggle = menu.addAction(
            i18n.t("foldericon.menu_close") if self._opened
            else i18n.t("foldericon.menu_open"))
        toggle.triggered.connect(lambda: self.toggled.emit(self.folder_id))
        menu.addSeparator()
        remove = menu.addAction(i18n.t("foldericon.menu_remove"))
        remove.triggered.connect(
            lambda: self.removeRequested.emit(self.folder_id))
        menu.exec(event.globalPos())
        event.accept()

    # ── 표시 ────────────────────────────────────────────────────────────

    def showEvent(self, event) -> None:
        super().showEvent(event)
        hwnd = int(self.winId())
        dwm.set_bare(hwnd)
        _pin_to_bottom(hwnd)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        nf.paint_folder_icon(painter, QRectF(self.rect()), self._color,
                             self._name, opened=self._opened,
                             count=self._count, hover=self._hover,
                             font=self.font())
        painter.end()
