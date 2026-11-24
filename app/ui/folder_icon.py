"""바탕 화면에 두는 메모 폴더 아이콘.

폴더에 든 메모를 한 아이콘으로 접어 둔다. 진짜 폴더처럼 한 번 누르면 골라지고
두 번 누르면 그 폴더의 메모를 보여 주는 창이 열린다. 우클릭 메뉴의 '바탕화면에
펼치기'를 고르면 그 폴더의 메모가 바탕 화면에 나타나고, 다시 고르면 아이콘 하나만
남는다. 어느 폴더를 아이콘으로 둘지와 펼침 상태는 이 PC 에만 저장한다(다른
사람의 바탕 화면은 바뀌지 않는다).

크기와 자리는 진짜 바탕화면 아이콘의 칸을 따른다(note_fold.icon_grid). 이름은
바탕화면 아이콘처럼 그 자리에서 고친다(F2, 우클릭 메뉴, 새로 만든 직후)."""
from __future__ import annotations

import ctypes
import platform

from PySide6.QtCore import QEasingCurve, QPoint, QRect, QRectF, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QGuiApplication, QMouseEvent, QPainter
from PySide6.QtWidgets import QApplication, QLineEdit, QMenu, QWidget

from .. import config, desktop_icons, i18n
from . import note_fold as nf
from . import dwm, edit_menu
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
    toggled = Signal(str)                 # folder_id: 바탕화면에 펼침/접음을 바꿔 달라
    opened = Signal(str)                  # folder_id: 폴더 창을 열어 달라
    moved = Signal(str, int, int)         # folder_id, x, y
    removeRequested = Signal(str)         # folder_id: 아이콘을 치워 달라
    deleteRequested = Signal(str)         # folder_id: 폴더를 지워 달라
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
        # 바탕화면 아이콘처럼 고르고 함께 끄는 일을 맡는 곳(ui/icon_select.py).
        self._icon_ctl = None
        self._selected = False
        self._marquee_tint: QRectF | None = None
        self._origin = QPoint()
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

    # ── 바탕화면 아이콘처럼 고르기(ui/icon_select.py) ──

    def set_icon_controller(self, controller) -> None:
        self._icon_ctl = controller

    def icon_key(self) -> str:
        return self.owner_key()

    def set_icon_selected(self, on: bool) -> None:
        if bool(on) != self._selected:
            self._selected = bool(on)
            self.update()

    def set_marquee(self, rect: QRect | None) -> None:
        """바탕화면에서 끌어 고르는 사각형(화면 좌표). 끝나면 None."""
        tint = None
        if rect is not None and self.isVisible():
            hit = rect.intersected(self.geometry())
            if not hit.isEmpty():
                tint = QRectF(hit.translated(-self.pos()))
        if tint != self._marquee_tint:
            self._marquee_tint = tint
            self.update()

    def icon_hit(self, rect: QRect) -> bool:
        """그 사각형(화면 좌표)이 이 아이콘의 그림이나 이름에 닿는가."""
        if not self.isVisible():
            return False
        local = QRectF(rect.translated(-self.pos()))
        return any(local.intersects(r) for r in nf.icon_hit_rects(
            QRectF(self.rect()), self._name, self.font(), self._grid().icon,
            self.devicePixelRatioF()))

    def place_icon(self, pos: QPoint) -> None:
        """그 자리에 그대로 놓는다(끌기 전의 자리로 돌려보낼 때)."""
        self._slide_to(QPoint(pos))
        self.moved.emit(self.folder_id, pos.x(), pos.y())

    def snap_icon(self) -> QPoint:
        """지금 있는 곳에서 가장 가까운 빈 칸에 놓는다. 그 자리를 돌려준다."""
        target = self._snapped(self.pos())
        self._fit(target + QPoint(8, 8))
        self._slide_to(target)
        self.moved.emit(self.folder_id, target.x(), target.y())
        return target

    def icon_spot(self) -> QPoint:
        """아이콘이 놓인 자리. 미끄러져 가는 중이면 가서 놓일 자리."""
        slide = self._slide
        if slide is not None and slide.state() == QVariantAnimation.Running:
            end = slide.endValue()
            if isinstance(end, QPoint):
                return QPoint(end)
        return self.pos()

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
        anim.setDuration(nf.anim_ms(nf.SLIDE_MS))
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
            self._origin = QPoint(self.pos())
            self._dragging = False
            if self._icon_ctl is not None:
                self._icon_ctl.pressed(self.icon_key(), event.modifiers())
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
                if self._icon_ctl is not None:
                    self._icon_ctl.drag_began(self.icon_key())
            self.move(gp - self._grab)
            if self._icon_ctl is not None:
                # 고른 아이콘 여럿 가운데 하나를 끌면 나머지도 함께 움직인다.
                self._icon_ctl.drag_moved(self.icon_key(),
                                          self.pos() - self._origin, gp)
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton and self._press is not None:
            dragged = self._dragging
            self._press = None
            self._dragging = False
            if dragged:
                if (self._icon_ctl is not None and self._icon_ctl.dropped_on_real(
                        self.icon_key(), event.globalPosition().toPoint())):
                    # 진짜 바탕화면의 휴지통이나 폴더 위에 놓았다. 제자리로 돌아간다.
                    target = QPoint(self._origin)
                    self.place_icon(target)
                else:
                    target = self.snap_icon()
                if self._icon_ctl is not None:
                    self._icon_ctl.drag_ended(self.icon_key(), True,
                                              target - self._origin)
            else:
                if self._icon_ctl is not None:
                    self._icon_ctl.clicked(self.icon_key())
                plain = not (event.modifiers()
                             & (Qt.ControlModifier | Qt.ShiftModifier))
                if (plain and self._edit is None
                        and desktop_icons.single_click_opens()):
                    # Windows 를 '한 번 클릭해서 열기'로 쓰는 사용자다.
                    self.opened.emit(self.folder_id)
        event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton and self._edit is None:
            self._press = None
            self.opened.emit(self.folder_id)
        event.accept()

    def nativeEvent(self, event_type, message):  # noqa: N802
        # 바탕화면 아이콘으로 놓인 동안에는 눌려도 키보드를 가져가지 않는다. 진짜
        # 아이콘과 함께 고를 수 있게 키보드는 바탕화면이 쥐고 있어야 한다.
        if self._icon_ctl is not None and self._edit is None and nf.is_mouse_activate(message):
            return True, nf.MA_NOACTIVATE
        return super().nativeEvent(event_type, message)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_F2:
            self.begin_rename()
            event.accept()
            return
        if (self._icon_ctl is not None and self._edit is None
                and self._icon_ctl.key(self.icon_key(), event.key(),
                                       event.modifiers())):
            event.accept()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.opened.emit(self.folder_id)
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
        # 메뉴는 앞에 나온 창에서 띄워야 다른 곳을 눌렀을 때 닫힌다.
        self.activateWindow()
        if (self._icon_ctl is not None
                and self._icon_ctl.menu(self.icon_key(), event.globalPos())):
            # 여러 개를 골라 둔 상태다. 고른 것 전체에 쓰는 메뉴가 떴다.
            event.accept()
            return
        menu = QMenu(self)
        menu.setStyleSheet(LIGHT_MENU_QSS)
        window = menu.addAction(i18n.t("icons.menu_open"))
        window.triggered.connect(lambda: self.opened.emit(self.folder_id))
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
        delete = menu.addAction(i18n.t("main.menu_folder_delete"))
        delete.triggered.connect(
            lambda: self.deleteRequested.emit(self.folder_id))
        edit_menu.run_menu(menu, event.globalPos())
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
                             label=self._edit is None,
                             selected=self._selected,
                             tint=self._marquee_tint)
        painter.end()
