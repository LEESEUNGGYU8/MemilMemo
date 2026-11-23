"""바탕 화면에 두는 메모 폴더 아이콘.

폴더에 든 메모를 한 아이콘으로 접어 둔다. 두 번 누르면 폴더가 열려 그 폴더의
메모가 바탕 화면에 나타나고, 다시 두 번 누르면 닫혀 아이콘 하나만 남는다.
어느 폴더를 아이콘으로 둘지와 열림 상태는 이 PC 에만 저장한다(다른 사람의
바탕 화면은 바뀌지 않는다).

크기와 자리는 진짜 바탕화면 아이콘의 칸을 따른다(note_fold.icon_grid). 이름은
바탕화면 아이콘처럼 그 자리에서 고친다(F2, 우클릭 메뉴, 새로 만든 직후)."""
from __future__ import annotations

import ctypes
import platform

from PySide6.QtCore import QEasingCurve, QPoint, QRect, QRectF, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QGuiApplication, QMouseEvent, QPainter
from PySide6.QtWidgets import QApplication, QLineEdit, QMenu, QWidget

from .. import config, i18n
from . import note_fold as nf
from . import dwm
from .theme import COLOR_FOCUS, LIGHT_MENU_QSS

# 폴더 이름의 길이 상한(이름을 그 자리에서 고칠 때).
NAME_MAX_LENGTH = 40


def _pin_to_bottom(hwnd: int) -> None:
    if platform.system() != "Windows" or hwnd == 0:
        return
    try:
        ctypes.windll.user32.SetWindowPos(hwnd, 1, 0, 0, 0, 0,
                                          0x0002 | 0x0001 | 0x0010)
    except Exception:
        pass


class _NameEdit(QLineEdit):
    """아이콘 아래에서 이름을 고치는 칸. Enter 로 정하고 Esc 로 그만둔다. 다른
    곳을 누르면 그때까지 쓴 것으로 정한다(바탕화면 아이콘과 같다)."""

    finished = Signal(bool)         # 정했는가(Esc 면 거짓)

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self._done = False
        self.setMaxLength(NAME_MAX_LENGTH)
        self.setAlignment(Qt.AlignHCenter)
        self.setFrame(False)
        self.setContextMenuPolicy(Qt.NoContextMenu)
        self.setStyleSheet(
            "QLineEdit { background: #FFFFFF; color: #111111; "
            f"border: 1px solid {COLOR_FOCUS}; border-radius: 2px; "
            f"padding: 0 2px; font-size: {nf.ICON_LABEL_PX}px; "
            "selection-background-color: #0A64D8; "
            "selection-color: #FFFFFF; }")

    def _finish(self, keep: bool) -> None:
        if self._done:
            return
        self._done = True
        self.finished.emit(keep)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._finish(True)
            event.accept()
            return
        if event.key() == Qt.Key_Escape:
            self._finish(False)
            event.accept()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self._finish(True)


class FolderIconWidget(QWidget):
    toggled = Signal(str)                 # folder_id: 열림/닫힘을 바꿔 달라
    moved = Signal(str, int, int)         # folder_id, x, y
    removeRequested = Signal(str)         # folder_id: 아이콘을 치워 달라
    renamed = Signal(str, str)            # folder_id, 새 이름

    def __init__(self, folder_id: str, name: str, color: str, *,
                 settings=None, parent: QWidget | None = None):
        super().__init__(parent, Qt.FramelessWindowHint | Qt.Tool)
        self.setWindowTitle(i18n.t("app.name"))
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.folder_id = folder_id
        self._name = name or ""
        self._color = color or config.DEFAULT_COLOR
        self._opened = False
        self._count = 0
        self._hover = False
        # 끌고 온 메모 아이콘이 이 위에 있는가(놓으면 이 폴더로 들어온다).
        self._drop_hover = False
        self._settings = settings
        self._press: QPoint | None = None
        self._grab = QPoint()
        self._dragging = False
        self._slot_resolver = None
        self._slide: QVariantAnimation | None = None
        self._edit: _NameEdit | None = None
        self.setToolTip(self._name)
        self._fit()

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

    def set_drop_hover(self, on: bool) -> None:
        if on != self._drop_hover:
            self._drop_hover = bool(on)
            self.update()

    def set_slot_resolver(self, resolver) -> None:
        """``resolver(owner_key, pos, screen_rect) -> QPoint`` 는 다른 아이콘과
        겹치지 않는 격자 자리를 돌려준다."""
        self._slot_resolver = resolver

    def owner_key(self) -> str:
        return "folder:" + self.folder_id

    # ── 자리 ────────────────────────────────────────────────────────────

    def _avail(self, at: QPoint | None = None) -> QRect:
        point = at if at is not None else self.geometry().center()
        scr = QGuiApplication.screenAt(point)
        if scr is None:
            scr = QGuiApplication.primaryScreen()
        return scr.availableGeometry() if scr is not None else QRect(
            0, 0, 1920, 1080)

    def _grid(self, at: QPoint | None = None) -> nf.IconGrid:
        return nf.icon_grid(self._avail(at))

    def _fit(self, at: QPoint | None = None) -> None:
        """창의 크기를 그 화면의 바탕화면 아이콘 한 칸에 맞춘다."""
        cell = self._grid(at).cell
        if self.size() != cell:
            self.setFixedSize(cell)
            self._place_edit()

    def _snapped(self, pos: QPoint) -> QPoint:
        avail = self._avail(pos + QPoint(self.width() // 2, self.height() // 2))
        if self._slot_resolver is not None:
            try:
                return QPoint(self._slot_resolver(self.owner_key(), pos, avail))
            except Exception:
                pass
        return nf.snap_icon_pos(pos, avail, [])

    def place(self, x: int, y: int) -> None:
        self.move(int(x), int(y))
        self._fit(QPoint(int(x) + 8, int(y) + 8))

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

    # ── 이름 고치기 ─────────────────────────────────────────────────────

    def is_renaming(self) -> bool:
        return self._edit is not None

    def begin_rename(self) -> None:
        """아이콘 아래 이름 자리에 입력 칸을 띄운다. 이름 전체가 골라져 있어
        곧바로 새 이름을 칠 수 있다."""
        if self._edit is not None:
            self._edit.setFocus(Qt.OtherFocusReason)
            return
        edit = _NameEdit(self)
        edit.setFont(nf.icon_label_font(self.font()))
        edit.setText(self._name)
        edit.finished.connect(self._end_rename)
        self._edit = edit
        self._place_edit()
        edit.show()
        self.setToolTip("")
        self.update()
        # 바탕화면 맨 아래에 깔린 창이다. 글자를 받으려면 창이 활성이어야 한다.
        self.raise_()
        self.activateWindow()
        edit.setFocus(Qt.OtherFocusReason)
        edit.selectAll()

    def _place_edit(self) -> None:
        if self._edit is None:
            return
        grid = self._grid()
        area = nf.icon_label_rect(QRectF(self.rect()), grid.icon, self.font(),
                                  lines=1, dpr=self.devicePixelRatioF())
        self._edit.setGeometry(QRect(1, int(round(area.top())) - 1,
                                     self.width() - 2,
                                     int(round(area.height())) + 3))

    def _end_rename(self, keep: bool) -> None:
        edit, self._edit = self._edit, None
        if edit is None:
            return
        name = " ".join(edit.text().split())
        edit.hide()
        edit.deleteLater()
        self.setToolTip(self._name)
        if keep and name and name != self._name:
            # 공유 PC 의 답이 오기 전에도 새 이름으로 보인다.
            self._name = name
            self.setToolTip(name)
            self.renamed.emit(self.folder_id, name)
        self.update()
        if self.isVisible():
            _pin_to_bottom(int(self.winId()))

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
                self._fit(target + QPoint(8, 8))
                self._slide_to(target)
                self.moved.emit(self.folder_id, target.x(), target.y())
        event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton and self._edit is None:
            self._press = None
            self.toggled.emit(self.folder_id)
        event.accept()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_F2:
            self.begin_rename()
            event.accept()
            return
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
        rename = menu.addAction(i18n.t("foldericon.menu_rename"))
        rename.triggered.connect(self.begin_rename)
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
                             count=self._count,
                             hover=self._hover or self._drop_hover,
                             font=self.font(), icon=self._grid().icon,
                             label=self._edit is None)
        painter.end()
