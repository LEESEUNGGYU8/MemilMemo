from __future__ import annotations

import os
import platform
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import config, i18n, packaging, shell_menu
from ..settings import (
    ANIMATION_SPEED_MAX,
    ANIMATION_SPEED_MIN,
    ANIMATION_SPEED_STEP,
    IMAGE_VIEWER_BUILTIN,
    IMAGE_VIEWER_SYSTEM,
    AppSettings,
    autostart_supported,
    clamp_animation_speed,
    get_windows_autostart,
    set_windows_autostart,
)
from . import note_fold
from .icons import color_swatch_icon, svg_icon
from .number_field import DecimalField, IntField, with_unit
from .theme import COLOR_FOCUS, color_name, refresh_style


_DIALOG_BUTTON_WIDTH = 112
_NAV_WIDTH = 168
_FIELD_WIDTH = 184           # 고르는 칸과 숫자 칸의 폭. 줄마다 오른쪽 끝이 맞는다.
_ABOUT_ICON = 92

# 이 창 안에서만 쓰는 모양. 앱 전체 스타일 위에 덧입힌다.
_QSS = """
QWidget#SettingsNav {
    background: #FAFAFA;
    border-right: 1px solid #EAEAEA;
}
QPushButton#NavItem {
    background: transparent;
    color: #4A4A4A;
    border: none;
    border-radius: 8px;
    padding: 0 12px;
    min-height: 36px;
    font-size: 13px;
    font-weight: 500;
    text-align: left;
}
QPushButton#NavItem:hover {
    background: #F0F0F0;
    color: #1F1F1F;
}
QPushButton#NavItem:checked {
    background: #E9E9E9;
    color: #1F1F1F;
    font-weight: 700;
}
QPushButton#NavItem[kbFocus="true"] {
    border: 2px solid #2563EB;
    padding: 0 10px;
}
QLabel#PageTitle {
    font-size: 17px;
    font-weight: 700;
    color: #1F1F1F;
}
QLabel#PageNote, QLabel#RowHint {
    font-size: 11px;
    color: #707070;
}
QLabel#RowLabel {
    font-size: 13px;
    color: #1F1F1F;
}
QFrame#RowLine {
    background: #EFEFEF;
    border: none;
}
QFrame#FootLine {
    background: #EAEAEA;
    border: none;
}
QLabel#AboutName {
    font-size: 20px;
    font-weight: 800;
    color: #1F1F1F;
}
QLabel#AboutVersion {
    font-size: 12px;
    color: #707070;
}
QLabel#AboutText {
    font-size: 13px;
    color: #1F1F1F;
}
"""


class Switch(QCheckBox):
    """켜고 끄는 스위치. 동작(글쇠, 상태, 신호)은 QCheckBox 그대로이고 모양만
    다르다. 글자는 옆의 줄 이름이 맡으므로 스위치에는 글자가 없다."""

    _W, _H, _PAD = 38, 22, 3

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self._knob = 0.0                      # 0 = 꺼짐 자리, 1 = 켜짐 자리
        self._anim: QVariantAnimation | None = None
        self.toggled.connect(self._on_toggled)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self._W + 2 * self._PAD, self._H + 2 * self._PAD)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def hitButton(self, pos) -> bool:  # noqa: N802
        return self.rect().contains(pos)

    def _on_toggled(self, on: bool) -> None:
        if self._anim is not None:
            self._anim.stop()
            self._anim.deleteLater()
            self._anim = None
        target = 1.0 if on else 0.0
        if not self.isVisible() or not note_fold.system_animations_enabled():
            self._knob = target
            self.update()
            return
        anim = QVariantAnimation(self)
        anim.setDuration(120)
        anim.setStartValue(float(self._knob))
        anim.setEndValue(target)
        anim.valueChanged.connect(self._on_step)
        self._anim = anim
        anim.start()

    def _on_step(self, value) -> None:
        self._knob = float(value)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        track = QRectF((self.width() - self._W) / 2.0,
                       (self.height() - self._H) / 2.0, self._W, self._H)
        if not self.isEnabled():
            fill = QColor("#D9DBDF")
        elif self.isChecked():
            fill = QColor("#1F1F1F")
        else:
            fill = QColor("#8C8C8C")          # 흰 바탕에서 3:1 을 넘는다
        p.setPen(Qt.NoPen)
        p.setBrush(fill)
        p.drawRoundedRect(track, self._H / 2.0, self._H / 2.0)
        d = self._H - 6.0
        x = track.left() + 3.0 + self._knob * (self._W - self._H)
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(QRectF(x, track.top() + 3.0, d, d))
        if self.property("kbFocus"):
            # 글쇠로 옮겨 왔을 때만 두르는 초점 테두리(theme.KeyboardFocusMarker).
            p.setPen(QPen(QColor(COLOR_FOCUS), 2.0))
            p.setBrush(Qt.NoBrush)
            ring = track.adjusted(-2.0, -2.0, 2.0, 2.0)
            p.drawRoundedRect(ring, ring.height() / 2.0, ring.height() / 2.0)
        p.end()


class _Row(QWidget):
    """설정 한 줄: 왼쪽에 이름(과 덧붙이는 말), 오른쪽에 고르는 칸.

    스위치가 달린 줄은 줄 어디를 눌러도 스위치가 바뀐다."""

    def __init__(self, label: str, control: QWidget | None, hint: str = "",
                 *, below: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self._switch = control if isinstance(control, Switch) else None
        if self._switch is not None:
            self.setCursor(Qt.PointingHandCursor)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(3)
        # 부모를 주고 만든다. 부모 없는 위젯에 setVisible(True) 를 부르면 그
        # 위젯이 잠깐 독립된 창으로 뜬다(설정 창을 열 때 번쩍이던 것).
        self.label = QLabel(label, self)
        self.label.setObjectName("RowLabel")
        text.addWidget(self.label)
        self.hint = QLabel(hint, self)
        self.hint.setObjectName("RowHint")
        self.hint.setWordWrap(True)
        self.hint.setVisible(bool(hint))
        text.addWidget(self.hint)

        if below:
            # 폴더 경로처럼 긴 칸은 이름 아래에 줄 전체로 놓는다.
            outer = QVBoxLayout(self)
            outer.setContentsMargins(0, 11, 0, 11)
            outer.setSpacing(8)
            outer.addWidget(self.label)
            if control is not None:
                outer.addWidget(control)
            outer.addWidget(self.hint)
        else:
            outer = QHBoxLayout(self)
            outer.setContentsMargins(0, 9, 0, 9)
            outer.setSpacing(16)
            outer.addLayout(text, 1)
            if control is not None:
                outer.addWidget(control, 0, Qt.AlignVCenter | Qt.AlignRight)
            self.setMinimumHeight(50)
        if control is not None and not control.accessibleName():
            control.setAccessibleName(label)

    def set_hint(self, text: str) -> None:
        self.hint.setText(text)
        self.hint.setVisible(bool(text))

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if (self._switch is not None and event.button() == Qt.LeftButton
                and self._switch.isEnabled()
                and self.rect().contains(event.position().toPoint())):
            self._switch.click()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _Page(QWidget):
    """설정 한 쪽: 제목 아래에 줄을 쌓는다. 줄 사이에는 가는 선이 놓인다."""

    def __init__(self, title: str, note: str = "",
                 parent: QWidget | None = None):
        super().__init__(parent)
        self._v = QVBoxLayout(self)
        self._v.setContentsMargins(28, 22, 28, 12)
        self._v.setSpacing(0)
        heading = QLabel(title)
        heading.setObjectName("PageTitle")
        self._v.addWidget(heading)
        if note:
            sub = QLabel(note)
            sub.setObjectName("PageNote")
            sub.setWordWrap(True)
            self._v.addSpacing(4)
            self._v.addWidget(sub)
        self._v.addSpacing(8)
        self._rows = 0

    def add_row(self, label: str, control: QWidget | None = None,
                hint: str = "", *, below: bool = False) -> _Row:
        if self._rows:
            line = QFrame()
            line.setObjectName("RowLine")
            line.setFixedHeight(1)
            self._v.addWidget(line)
        row = _Row(label, control, hint, below=below)
        self._v.addWidget(row)
        self._rows += 1
        return row

    def add_layout(self, layout, top: int = 0) -> None:
        if top:
            self._v.addSpacing(top)
        self._v.addLayout(layout)

    def add_stretch(self) -> None:
        self._v.addStretch(1)


def _field(widget: QWidget) -> QWidget:
    widget.setFixedWidth(_FIELD_WIDTH)
    return widget


class _ColorPicker(QWidget):
    colorChanged = Signal(str)

    def __init__(self, current: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._current = current
        self._buttons: dict[str, QToolButton] = {}

        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(4)
        for index, hex_color in enumerate(config.COLORS):
            btn = QToolButton()
            btn.setFixedSize(26, 26)
            btn.setIcon(color_swatch_icon(hex_color, 20))
            btn.setIconSize(QSize(20, 20))
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setToolTip(color_name(hex_color))
            btn.setStyleSheet(
                "QToolButton { border: 1px solid transparent; padding: 2px; "
                "border-radius: 6px; background: transparent; }"
                "QToolButton:hover { background: rgba(0,0,0,0.06); }"
                "QToolButton:checked { border: 2px solid #1F1F1F; }"
            )
            btn.clicked.connect(lambda _=False, c=hex_color: self._select(c))
            self._buttons[hex_color] = btn
            grid.addWidget(btn, *divmod(index, config.COLOR_COLUMNS))
        self._refresh()

    def _select(self, color: str) -> None:
        self._current = color
        self._refresh()
        self.colorChanged.emit(color)

    def _refresh(self) -> None:
        for c, b in self._buttons.items():
            b.setChecked(c == self._current)

    def value(self) -> str:
        return self._current


class SettingsDialog(QDialog):
    """전달받은 AppSettings 를 Accept 시 in-place 로 수정한 뒤 .save() 한다."""

    backupNowRequested = Signal()
    restoreRequested = Signal()
    resetConnectionRequested = Signal()     # 접속 정보 바꾸기
    shareRequested = Signal()               # 혼자 쓰던 메모의 공유 시작

    def __init__(self, settings: AppSettings, role: str = "guest",
                 parent: QWidget | None = None, *, solo: bool = False):
        """``solo`` 는 공유하지 않고 혼자 쓰는 중인가(그때 ``role`` 은 "host" 다)."""
        super().__init__(parent)
        self.setWindowTitle(i18n.t("settings.window_title", app=i18n.t("app.name")))
        self.setMinimumSize(700, 500)
        self.setStyleSheet(_QSS)
        self._settings = settings
        self._role = role
        self._solo = bool(solo)

        nav = QWidget()
        nav.setObjectName("SettingsNav")
        nav.setAttribute(Qt.WA_StyledBackground, True)
        nav.setFixedWidth(_NAV_WIDTH)
        nav_v = QVBoxLayout(nav)
        nav_v.setContentsMargins(10, 18, 10, 12)
        nav_v.setSpacing(2)
        self._nav = QButtonGroup(self)
        self._nav.setExclusive(True)
        self._pages = QStackedWidget()

        for icon, title, build in (
            ("settings", i18n.t("settings.tab_general"), self._build_general_page),
            ("note", i18n.t("settings.tab_memo"), self._build_memo_page),
            ("fold", i18n.t("settings.tab_fold"), self._build_fold_page),
            ("bell", i18n.t("settings.tab_notify"), self._build_notify_page),
            ("network", i18n.t("settings.tab_network"), self._build_network_page),
            ("archive", i18n.t("settings.tab_backup"), self._build_backup_page),
            ("info", i18n.t("settings.tab_about"), self._build_about_page),
        ):
            index = self._pages.addWidget(build(title))
            item = QPushButton(title)
            item.setObjectName("NavItem")
            item.setIcon(svg_icon(icon, color="#4A4A4A"))
            item.setIconSize(QSize(16, 16))
            item.setCheckable(True)
            item.setAutoDefault(False)
            item.setCursor(Qt.PointingHandCursor)
            self._nav.addButton(item, index)
            nav_v.addWidget(item)
        nav_v.addStretch(1)
        self._nav.idClicked.connect(self._pages.setCurrentIndex)
        self._nav.button(0).setChecked(True)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        ok_btn = buttons.button(QDialogButtonBox.Ok)
        cancel_btn = buttons.button(QDialogButtonBox.Cancel)
        ok_btn.setText(i18n.t("settings.btn_save"))
        cancel_btn.setText(i18n.t("settings.btn_cancel"))
        # setMinimumWidth 는 re-polish 뒤에 호출해야 한다. polish 가 크기 힌트를
        # 재계산하며 그 전에 지정한 폭을 덮어쓰기 때문이다.
        cancel_btn.setProperty("secondary", True)
        refresh_style(cancel_btn)
        ok_btn.setMinimumWidth(_DIALOG_BUTTON_WIDTH)
        cancel_btn.setMinimumWidth(_DIALOG_BUTTON_WIDTH)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        foot_line = QFrame()
        foot_line.setObjectName("FootLine")
        foot_line.setFixedHeight(1)
        foot = QHBoxLayout()
        foot.setContentsMargins(28, 12, 20, 14)
        foot.addWidget(buttons)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        right.addWidget(self._pages, 1)
        right.addWidget(foot_line)
        right.addLayout(foot)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(nav)
        root.addLayout(right, 1)

    def show_page(self, index: int) -> None:
        """``index`` 번째 구역을 보인다."""
        button = self._nav.button(index)
        if button is not None:
            button.setChecked(True)
            self._pages.setCurrentIndex(index)

    def _switch(self, on: bool) -> Switch:
        switch = Switch()
        switch.setChecked(bool(on))
        return switch

    # ── 구역 ────────────────────────────────────────────────────────────

    def _build_general_page(self, title: str) -> QWidget:
        page = _Page(title)

        self._language = QComboBox()
        for code in i18n.SUPPORTED_LANGUAGES:
            self._language.addItem(i18n.LANGUAGE_NAMES.get(code, code),
                                   userData=code)
        cur_lang = i18n.resolve_language(self._settings.language_code)
        idx = self._language.findData(cur_lang)
        self._language.setCurrentIndex(idx if idx >= 0 else 0)
        page.add_row(i18n.t("settings.language"), _field(self._language),
                     i18n.t("settings.language.restart_hint"))

        is_windows = platform.system() == "Windows"
        self._autostart = self._switch(
            get_windows_autostart() if is_windows else self._settings.autostart)
        can_autostart = is_windows and autostart_supported()
        self._autostart.setEnabled(can_autostart)
        store = packaging.is_packaged()
        # 스토어판에서 사용자가 Windows 설정의 시작 앱에서 꺼 두었으면 앱이 바꿀
        # 수 없다. 그때만 어디서 바꾸는지 적는다.
        page.add_row(i18n.t("settings.check_autostart"), self._autostart,
                     i18n.t("settings.note_autostart_windows")
                     if store and not can_autostart else "")

        self._tray_check = self._switch(self._settings.show_tray_icon)
        page.add_row(i18n.t("settings.check_tray"), self._tray_check)

        self._close_to_tray = self._switch(self._settings.close_main_to_tray)
        page.add_row(i18n.t("settings.check_close_to_tray"), self._close_to_tray)

        # 바탕 화면의 '새 메모'와 파일·폴더의 '새 메모에 링크로 넣기'. 상태는 설정
        # 파일이 아니라 레지스트리에 있다(설치 프로그램의 선택 항목과 같은 자리).
        self._desktop_menu_was = shell_menu.is_registered()
        self._desktop_menu = self._switch(self._desktop_menu_was)
        self._desktop_menu.setEnabled(shell_menu.is_supported())
        # 스토어판의 메뉴는 Windows 11 의 첫 화면 메뉴에 나오므로 덧붙일 말이 없다.
        # 스토어판의 바탕화면 항목은 Windows 11 22H2 부터 나온다. 그보다 옛
        # Windows 에서는 나오지 않는 것을 켠다고 적지 않는다.
        label = (i18n.t("settings.check_desktop_menu")
                 if shell_menu.desktop_item_available()
                 else i18n.t("settings.check_file_menu"))
        page.add_row(label, self._desktop_menu,
                     "" if store else i18n.t("settings.note_desktop_menu"))
        page.add_stretch()
        return page

    def _build_memo_page(self, title: str) -> QWidget:
        page = _Page(title)

        self._font_size = IntField(8, 48)
        self._font_size.setValue(self._settings.default_font_size)
        page.add_row(i18n.t("settings.label_font_size"),
                     _field(with_unit(self._font_size, "pt")))

        self._color_picker = _ColorPicker(self._settings.default_note_color)
        page.add_row(i18n.t("settings.label_note_color"), self._color_picker)

        # 새 메모를 누구에게 보일지. 만든 뒤에는 메모마다 바꿀 수 있다.
        self._new_note_scope = QComboBox()
        self._new_note_scope.addItem(
            i18n.t("settings.new_note_public"), userData=False)
        self._new_note_scope.addItem(
            i18n.t("settings.new_note_private"), userData=True)
        self._new_note_scope.setCurrentIndex(
            1 if self._settings.new_note_private else 0)
        page.add_row(i18n.t("settings.label_new_note_scope"),
                     _field(self._new_note_scope))

        self._move_checked_to_bottom = self._switch(
            self._settings.move_checked_to_bottom)
        page.add_row(i18n.t("settings.check_move_checked_to_bottom"),
                     self._move_checked_to_bottom)

        self._image_viewer = QComboBox()
        self._image_viewer.addItem(i18n.t("settings.image_viewer_builtin"),
                                   userData=IMAGE_VIEWER_BUILTIN)
        self._image_viewer.addItem(i18n.t("settings.image_viewer_system"),
                                   userData=IMAGE_VIEWER_SYSTEM)
        idx = self._image_viewer.findData(self._settings.image_viewer)
        self._image_viewer.setCurrentIndex(idx if idx >= 0 else 0)
        page.add_row(i18n.t("settings.label_image_viewer"),
                     _field(self._image_viewer))
        page.add_stretch()
        return page

    def _build_fold_page(self, title: str) -> QWidget:
        page = _Page(title)

        # 접힌 메모의 기본 모양. 메모마다 접힌 쪽지를 오른쪽 눌러 바꿀 수도 있다.
        self._fold_style = QComboBox()
        self._fold_style.addItem(i18n.t("settings.fold_style_slip"),
                                 userData="slip")
        self._fold_style.addItem(i18n.t("settings.fold_style_icon"),
                                 userData="icon")
        idx = self._fold_style.findData(self._settings.fold_style)
        self._fold_style.setCurrentIndex(idx if idx >= 0 else 0)
        page.add_row(i18n.t("settings.label_fold"), _field(self._fold_style))

        self._fold_animation = self._switch(self._settings.fold_animation)
        page.add_row(i18n.t("settings.check_fold_animation"),
                     self._fold_animation)

        self._animation_speed = DecimalField(
            ANIMATION_SPEED_MIN, ANIMATION_SPEED_MAX, ANIMATION_SPEED_STEP)
        self._animation_speed.setValue(
            clamp_animation_speed(self._settings.animation_speed))
        speed = with_unit(self._animation_speed, i18n.t("settings.unit_times"))
        speed.setEnabled(self._fold_animation.isChecked())
        self._fold_animation.toggled.connect(speed.setEnabled)
        page.add_row(i18n.t("settings.label_animation_speed"), _field(speed))

        self._dock_on_top = self._switch(self._settings.dock_on_top)
        page.add_row(i18n.t("settings.check_dock_on_top"), self._dock_on_top)
        page.add_stretch()
        return page

    def _build_notify_page(self, title: str) -> QWidget:
        page = _Page(title)
        self._notify_new_note = self._switch(self._settings.notify_new_note)
        page.add_row(i18n.t("settings.check_notify_new_note"),
                     self._notify_new_note)
        self._notify_note_edit = self._switch(self._settings.notify_note_edit)
        page.add_row(i18n.t("settings.check_notify_note_edit"),
                     self._notify_note_edit)
        page.add_stretch()
        return page

    def _build_network_page(self, title: str) -> QWidget:
        page = _Page(title, i18n.t("settings.note_network"))

        self._port = IntField(1, 65535)
        self._port.setValue(self._settings.default_port)
        page.add_row(i18n.t("settings.label_host_port"), _field(self._port))

        self._heartbeat = DecimalField(0.5, 60.0, 0.5)
        self._heartbeat.setValue(self._settings.default_heartbeat_ms / 1000)
        page.add_row(i18n.t("settings.label_heartbeat"), _field(with_unit(
            self._heartbeat, i18n.t("connect.unit_seconds"))))
        page.add_stretch()

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(8)
        actions.addStretch(1)
        if self._solo:
            # 혼자 쓰던 메모를 다른 PC 와 나눈다. 그다음부터는 공유 PC 로 켜진다.
            actions.addWidget(self._action_button(
                i18n.t("settings.btn_share_start"), self.shareRequested.emit))
        # 다음에 켤 때 시작 방식을 다시 고른다(트레이 메뉴에도 있다).
        actions.addWidget(self._action_button(
            i18n.t("main.tray_change_connection"),
            self.resetConnectionRequested.emit))
        page.add_layout(actions, top=8)
        return page

    def _build_backup_page(self, title: str) -> QWidget:
        is_host = self._role == "host"
        page = _Page(title, "" if is_host else i18n.t("settings.note_backup_guest"))

        self._backup_enabled = self._switch(self._settings.backup_enabled)
        page.add_row(i18n.t("settings.check_backup_enabled"), self._backup_enabled)

        dir_wrap = QWidget()
        dir_row = QHBoxLayout(dir_wrap)
        dir_row.setContentsMargins(0, 0, 0, 0)
        dir_row.setSpacing(6)
        self._backup_dir = QLineEdit(self._settings.backup_dir
                                      or str(config.default_backup_dir()))
        browse = QToolButton()
        browse.setIcon(svg_icon("folder"))
        browse.setIconSize(QSize(16, 16))
        browse.setCursor(Qt.PointingHandCursor)
        browse.setProperty("iconOnly", True)
        browse.setFixedSize(34, 34)
        browse.setToolTip(i18n.t("settings.btn_browse_folder"))
        browse.clicked.connect(self._pick_backup_dir)
        dir_row.addWidget(self._backup_dir, 1)
        dir_row.addWidget(browse)
        # 백업이 메모와 같은 디스크에 있으면 디스크가 고장 날 때 함께 잃는다.
        dir_line = page.add_row(i18n.t("settings.label_backup_dir"), dir_wrap,
                                i18n.t("settings.note_backup_same_disk"),
                                below=True)
        self._backup_dir_hint = dir_line.hint
        self._backup_dir.textChanged.connect(self._refresh_backup_dir_hint)
        self._refresh_backup_dir_hint()

        interval_wrap = QWidget()
        interval_row = QHBoxLayout(interval_wrap)
        interval_row.setContentsMargins(0, 0, 0, 0)
        interval_row.setSpacing(6)
        self._backup_interval = IntField(1, 999)
        self._backup_interval.setValue(self._settings.backup_interval_value)
        self._backup_unit = QComboBox()
        for label, key in (
            (i18n.t("settings.backup_unit_minutes"), "minutes"),
            (i18n.t("settings.backup_unit_hours"), "hours"),
            (i18n.t("settings.backup_unit_days"), "days"),
        ):
            self._backup_unit.addItem(label, userData=key)
        idx = self._backup_unit.findData(self._settings.backup_interval_unit)
        self._backup_unit.setCurrentIndex(idx if idx >= 0 else 1)
        # 두 칸의 폭을 합쳐 다른 줄의 칸과 같게 한다. 숫자 칸을 늘어나게 두면
        # 제 최소 폭을 고집해, 단위 칸이 줄 밖으로 밀려 오른쪽이 잘린다.
        self._backup_unit.ensurePolished()
        unit_width = max(92, self._backup_unit.sizeHint().width())
        self._backup_unit.setFixedWidth(unit_width)
        self._backup_interval.setFixedWidth(
            _FIELD_WIDTH - interval_row.spacing() - unit_width)
        interval_row.addWidget(self._backup_interval)
        interval_row.addWidget(self._backup_unit)
        self._backup_interval.setAccessibleName(
            i18n.t("settings.label_backup_interval"))
        page.add_row(i18n.t("settings.label_backup_interval"),
                     _field(interval_wrap))

        self._backup_keep = IntField(1, 1000)
        self._backup_keep.setValue(self._settings.backup_keep_count)
        page.add_row(i18n.t("settings.label_backup_keep"), _field(with_unit(
            self._backup_keep, i18n.t("settings.backup_keep_suffix"))))
        page.add_stretch()

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(8)
        actions.addStretch(1)
        # 백업과, 참여한 PC 가 받아 둔 메모 사본을 한 창에서 고른다. 공유 PC 가
        # 고장 났을 때 참여한 PC 도 여기서 메모를 꺼낸다.
        actions.addWidget(self._action_button(
            i18n.t("settings.btn_restore"), self.restoreRequested.emit))
        if is_host:
            actions.addWidget(self._action_button(
                i18n.t("settings.btn_backup_now"), self.backupNowRequested.emit))
        page.add_layout(actions, top=8)
        return page

    def _action_button(self, text: str, handler) -> QPushButton:
        button = QPushButton(text)
        button.setProperty("secondary", True)
        refresh_style(button)
        button.setAutoDefault(False)
        button.setCursor(Qt.PointingHandCursor)
        button.clicked.connect(handler)
        return button

    def _build_about_page(self, title: str) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(28, 30, 28, 12)
        v.setSpacing(0)

        icon = QLabel()
        icon.setAlignment(Qt.AlignHCenter)
        icon.setFixedHeight(_ABOUT_ICON)
        icon.setAccessibleName(i18n.t("app.name"))
        source = QPixmap(str(config.ICON_PATH))
        if not source.isNull():
            dpr = self.devicePixelRatioF() or 1.0
            side = int(round(_ABOUT_ICON * dpr))
            scaled = source.scaled(side, side, Qt.KeepAspectRatio,
                                   Qt.SmoothTransformation)
            scaled.setDevicePixelRatio(dpr)
            icon.setPixmap(scaled)
        v.addWidget(icon)
        v.addSpacing(12)

        name = QLabel(i18n.t("app.name"))
        name.setObjectName("AboutName")
        name.setAlignment(Qt.AlignHCenter)
        v.addWidget(name)
        v.addSpacing(4)
        version = QLabel(
            i18n.t("settings.about_version", version=config.APP_VERSION))
        version.setObjectName("AboutVersion")
        version.setAlignment(Qt.AlignHCenter)
        v.addWidget(version)
        v.addSpacing(10)
        desc = QLabel(i18n.t("settings.about_description"))
        desc.setObjectName("AboutText")
        desc.setAlignment(Qt.AlignHCenter)
        desc.setWordWrap(True)
        v.addWidget(desc)
        v.addSpacing(20)

        self._auto_update = self._switch(self._settings.auto_update)
        if not packaging.is_packaged():
            # 스토어판은 스토어가 올리므로 이 줄이 없다.
            line = QFrame()
            line.setObjectName("RowLine")
            line.setFixedHeight(1)
            v.addWidget(line)
            v.addWidget(_Row(i18n.t("settings.check_auto_update"),
                             self._auto_update,
                             i18n.t("settings.note_auto_update")))
            line = QFrame()
            line.setObjectName("RowLine")
            line.setFixedHeight(1)
            v.addWidget(line)

        v.addStretch(1)
        credit = QLabel(i18n.t("settings.about_credit"))
        credit.setObjectName("RowHint")
        credit.setWordWrap(True)
        v.addWidget(credit)
        return page

    # ── 동작 ────────────────────────────────────────────────────────────

    def _refresh_backup_dir_hint(self) -> None:
        """백업 폴더가 메모 파일과 같은 드라이브면 안내를 보인다(호스트만)."""
        def drive(p: str) -> str:
            return os.path.splitdrive(os.path.abspath(p))[0].lower()
        folder = self._backup_dir.text().strip() or str(config.default_backup_dir())
        same = drive(folder) == drive(str(config.default_db_path()))
        self._backup_dir_hint.setVisible(self._role == "host" and same)

    def backup_folder(self) -> Path:
        """지금 입력칸에 적힌 백업 폴더(아직 저장하지 않은 값 포함)."""
        raw = self._backup_dir.text().strip()
        return Path(raw) if raw else config.default_backup_dir()

    def _pick_backup_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, i18n.t("settings.dialog_pick_backup_dir"),
            self._backup_dir.text() or str(Path.home()))
        if path:
            self._backup_dir.setText(path)

    def _on_accept(self) -> None:
        s = self._settings
        s.language_code = self._language.currentData() or ""
        s.default_font_size = int(self._font_size.value())
        s.default_note_color = self._color_picker.value()
        s.show_tray_icon = self._tray_check.isChecked()
        s.close_main_to_tray = self._close_to_tray.isChecked()
        # 바뀌었을 때만 레지스트리를 건드린다.
        want_menu = self._desktop_menu.isChecked()
        if self._desktop_menu.isEnabled() and want_menu != self._desktop_menu_was:
            shell_menu.set_registered(
                want_menu, i18n.t("shell.new_note_title"),
                i18n.t("shell.link_note_title"),
                i18n.t("shell.new_folder_title"))

        s.new_note_private = bool(self._new_note_scope.currentData())
        s.notify_new_note = self._notify_new_note.isChecked()
        s.notify_note_edit = self._notify_note_edit.isChecked()
        s.move_checked_to_bottom = self._move_checked_to_bottom.isChecked()
        s.image_viewer = self._image_viewer.currentData() or IMAGE_VIEWER_BUILTIN
        s.fold_style = self._fold_style.currentData() or "slip"
        s.fold_animation = self._fold_animation.isChecked()
        s.animation_speed = clamp_animation_speed(self._animation_speed.value())
        s.dock_on_top = self._dock_on_top.isChecked()

        s.autostart = self._autostart.isChecked()
        if self._autostart.isEnabled():
            set_windows_autostart(s.autostart)

        s.default_port = int(self._port.value())
        s.default_heartbeat_ms = int(round(self._heartbeat.value() * 1000))

        s.backup_enabled = self._backup_enabled.isChecked()
        raw_dir = self._backup_dir.text().strip()
        default_dir = str(config.default_backup_dir())
        s.backup_dir = "" if (not raw_dir or raw_dir == default_dir) else raw_dir
        s.backup_interval_value = int(self._backup_interval.value())
        s.backup_interval_unit = self._backup_unit.currentData() or "hours"
        s.backup_keep_count = int(self._backup_keep.value())

        if s.auto_update != self._auto_update.isChecked():
            s.auto_update = self._auto_update.isChecked()
            # 다시 켰으면 지난번에 끝내지 못한 버전도 새로 시도한다.
            s.auto_update_tried = ""

        s.save()
        self.accept()
