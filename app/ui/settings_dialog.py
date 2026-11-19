from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import config, i18n, shell_menu
from ..settings import (
    IMAGE_VIEWER_BUILTIN,
    IMAGE_VIEWER_SYSTEM,
    AppSettings,
    get_windows_autostart,
    set_windows_autostart,
)
from .icons import color_swatch_icon, svg_icon
from .theme import color_name, refresh_style


_DIALOG_BUTTON_WIDTH = 130  # "지금 백업 실행" 이 들어가면서도 저장/취소와 시각적으로 일치
_LABEL_ROW_HEIGHT = 34       # QLineEdit / QSpinBox 의 실효 높이와 동일


def _align_form_labels(form: QFormLayout) -> None:
    """라벨의 최소 높이를 필드와 맞춰 세로 가운데 정렬한다.

    ``setLabelAlignment`` 는 수평 정렬만 다루므로, 키가 작은 라벨은 더 큰 필드
    행의 상단에 붙어 버린다."""
    for row in range(form.rowCount()):
        item = form.itemAt(row, QFormLayout.LabelRole)
        if item is None:
            continue
        widget = item.widget()
        if widget is None:
            continue
        widget.setMinimumHeight(_LABEL_ROW_HEIGHT)
        widget.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)


class _ColorPicker(QWidget):
    colorChanged = Signal(str)

    _COLUMNS = 4

    def __init__(self, current: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._current = current
        self._buttons: dict[str, QToolButton] = {}

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)

        for index, hex_color in enumerate(config.COLORS):
            r, c = divmod(index, self._COLUMNS)
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
            grid.addWidget(btn, r, c)

        outer.addLayout(grid)
        outer.addStretch(1)
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
    copiesRequested = Signal()

    def __init__(self, settings: AppSettings, role: str = "guest",
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(i18n.t("settings.window_title", app=i18n.t("app.name")))
        self.setMinimumSize(540, 460)
        self._settings = settings
        self._role = role

        tabs = QTabWidget()
        tabs.addTab(self._build_general_tab(), i18n.t("settings.tab_general"))
        tabs.addTab(self._build_memo_tab(), i18n.t("settings.tab_memo"))
        tabs.addTab(self._build_startup_tab(), i18n.t("settings.tab_startup"))
        tabs.addTab(self._build_network_tab(), i18n.t("settings.tab_network"))
        tabs.addTab(self._build_backup_tab(), i18n.t("settings.tab_backup"))
        tabs.addTab(self._build_about_tab(), i18n.t("settings.tab_about"))

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        ok_btn = buttons.button(QDialogButtonBox.Ok)
        cancel_btn = buttons.button(QDialogButtonBox.Cancel)
        ok_btn.setText(i18n.t("settings.btn_save"))
        cancel_btn.setText(i18n.t("settings.btn_cancel"))
        # setFixedWidth 는 re-polish 뒤에 호출해야 한다. polish 가 크기 힌트를
        # 재계산하며 그 전에 지정한 폭을 덮어쓰기 때문이다.
        cancel_btn.setProperty("secondary", True)
        refresh_style(cancel_btn)
        ok_btn.setMinimumWidth(_DIALOG_BUTTON_WIDTH)
        cancel_btn.setMinimumWidth(_DIALOG_BUTTON_WIDTH)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 14)
        root.setSpacing(12)
        root.addWidget(tabs, 1)
        root.addWidget(buttons)

    def _build_general_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(8, 12, 8, 8)
        form.setVerticalSpacing(18)
        form.setHorizontalSpacing(24)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setLabelAlignment(Qt.AlignVCenter | Qt.AlignLeft)

        self._language = QComboBox()
        for code in i18n.SUPPORTED_LANGUAGES:
            self._language.addItem(i18n.LANGUAGE_NAMES.get(code, code),
                                   userData=code)
        cur_lang = i18n.resolve_language(self._settings.language_code)
        idx = self._language.findData(cur_lang)
        self._language.setCurrentIndex(idx if idx >= 0 else 0)
        form.addRow(i18n.t("settings.language"), self._language)

        lang_hint = QLabel(i18n.t("settings.language.restart_hint"))
        lang_hint.setProperty("role", "subtle")
        lang_hint.setWordWrap(True)
        form.addRow("", lang_hint)

        self._font_size = QSpinBox()
        self._font_size.setRange(8, 48)
        self._font_size.setSuffix(" pt")
        self._font_size.setValue(self._settings.default_font_size)
        form.addRow(i18n.t("settings.label_font_size"), self._font_size)

        self._color_picker = _ColorPicker(self._settings.default_note_color)
        form.addRow(i18n.t("settings.label_note_color"), self._color_picker)

        self._tray_check = QCheckBox(i18n.t("settings.check_tray"))
        self._tray_check.setChecked(self._settings.show_tray_icon)
        form.addRow("", self._tray_check)

        self._close_to_tray = QCheckBox(i18n.t("settings.check_close_to_tray"))
        self._close_to_tray.setChecked(self._settings.close_main_to_tray)
        form.addRow("", self._close_to_tray)

        # 바탕 화면 우클릭 메뉴의 '새 메모'. 상태는 설정 파일이 아니라
        # 레지스트리에 있다(설치 프로그램의 선택 항목과 같은 자리).
        self._desktop_menu = QCheckBox(i18n.t("settings.check_desktop_menu"))
        self._desktop_menu_was = shell_menu.is_registered()
        self._desktop_menu.setChecked(self._desktop_menu_was)
        self._desktop_menu.setEnabled(shell_menu.is_supported())
        form.addRow("", self._desktop_menu)

        menu_note = QLabel(i18n.t("settings.note_desktop_menu"))
        menu_note.setProperty("role", "subtle")
        menu_note.setWordWrap(True)
        form.addRow("", menu_note)
        _align_form_labels(form)
        return w

    def _build_memo_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(8, 12, 8, 8)
        form.setVerticalSpacing(18)
        form.setHorizontalSpacing(24)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setLabelAlignment(Qt.AlignVCenter | Qt.AlignLeft)

        # 새 메모를 누구에게 보일지. 만든 뒤에는 메모마다 바꿀 수 있다.
        self._new_note_scope = QComboBox()
        self._new_note_scope.addItem(
            i18n.t("settings.new_note_public"), userData=False)
        self._new_note_scope.addItem(
            i18n.t("settings.new_note_private"), userData=True)
        self._new_note_scope.setCurrentIndex(
            1 if self._settings.new_note_private else 0)
        form.addRow(i18n.t("settings.label_new_note_scope"),
                    self._new_note_scope)

        self._notify_new_note = QCheckBox(
            i18n.t("settings.check_notify_new_note"))
        self._notify_new_note.setChecked(self._settings.notify_new_note)
        form.addRow("", self._notify_new_note)

        self._move_checked_to_bottom = QCheckBox(
            i18n.t("settings.check_move_checked_to_bottom"))
        self._move_checked_to_bottom.setChecked(
            self._settings.move_checked_to_bottom)
        form.addRow("", self._move_checked_to_bottom)

        self._image_viewer = QComboBox()
        self._image_viewer.addItem(i18n.t("settings.image_viewer_builtin"),
                                   userData=IMAGE_VIEWER_BUILTIN)
        self._image_viewer.addItem(i18n.t("settings.image_viewer_system"),
                                   userData=IMAGE_VIEWER_SYSTEM)
        idx = self._image_viewer.findData(self._settings.image_viewer)
        self._image_viewer.setCurrentIndex(idx if idx >= 0 else 0)
        form.addRow(i18n.t("settings.label_image_viewer"), self._image_viewer)

        # 접힌 메모의 기본 모양. 메모마다 접힌 쪽지를 오른쪽 눌러 바꿀 수도 있다.
        self._fold_style = QComboBox()
        self._fold_style.addItem(i18n.t("settings.fold_style_slip"),
                                 userData="slip")
        self._fold_style.addItem(i18n.t("settings.fold_style_icon"),
                                 userData="icon")
        idx = self._fold_style.findData(self._settings.fold_style)
        self._fold_style.setCurrentIndex(idx if idx >= 0 else 0)
        form.addRow(i18n.t("settings.label_fold"), self._fold_style)

        self._fold_animation = QCheckBox(
            i18n.t("settings.check_fold_animation"))
        self._fold_animation.setChecked(self._settings.fold_animation)
        form.addRow("", self._fold_animation)

        self._dock_on_top = QCheckBox(i18n.t("settings.check_dock_on_top"))
        self._dock_on_top.setChecked(self._settings.dock_on_top)
        form.addRow("", self._dock_on_top)

        fold_note = QLabel(i18n.t("settings.note_fold"))
        fold_note.setProperty("role", "subtle")
        fold_note.setWordWrap(True)
        form.addRow("", fold_note)
        _align_form_labels(form)
        return w

    def _build_startup_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(8, 12, 8, 8)
        form.setVerticalSpacing(18)
        form.setHorizontalSpacing(24)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setLabelAlignment(Qt.AlignVCenter | Qt.AlignLeft)

        import platform
        is_windows = platform.system() == "Windows"

        self._autostart = QCheckBox(i18n.t("settings.check_autostart"))
        current = get_windows_autostart() if is_windows else self._settings.autostart
        self._autostart.setChecked(current)
        self._autostart.setEnabled(is_windows)
        form.addRow("", self._autostart)
        _align_form_labels(form)
        return w

    def _build_network_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(8, 12, 8, 8)
        form.setVerticalSpacing(18)
        form.setHorizontalSpacing(24)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setLabelAlignment(Qt.AlignVCenter | Qt.AlignLeft)

        self._port = QSpinBox()
        self._port.setRange(1, 65535)
        self._port.setValue(self._settings.default_port)
        form.addRow(i18n.t("settings.label_host_port"), self._port)

        self._heartbeat = QDoubleSpinBox()
        self._heartbeat.setDecimals(1)
        self._heartbeat.setRange(0.5, 60.0)
        self._heartbeat.setSingleStep(0.5)
        self._heartbeat.setSuffix(i18n.t("connect.unit_seconds"))
        self._heartbeat.setValue(self._settings.default_heartbeat_ms / 1000)
        form.addRow(i18n.t("settings.label_heartbeat"), self._heartbeat)

        note = QLabel(i18n.t("settings.note_network"))
        note.setProperty("role", "subtle")
        note.setWordWrap(True)
        form.addRow("", note)
        _align_form_labels(form)
        return w

    def _build_backup_tab(self) -> QWidget:
        w = QWidget()
        # 좌우 여백을 0 으로 둬야 하단 액션 버튼이 대화상자의 저장/취소 버튼과
        # 우측으로 정확히 정렬된다.
        outer = QVBoxLayout(w)
        outer.setContentsMargins(0, 12, 0, 8)
        outer.setSpacing(12)

        form_wrap = QWidget()
        form = QFormLayout(form_wrap)
        self._backup_form = form
        form.setContentsMargins(8, 0, 8, 0)
        form.setVerticalSpacing(18)
        form.setHorizontalSpacing(24)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setLabelAlignment(Qt.AlignVCenter | Qt.AlignLeft)

        dir_row = QHBoxLayout()
        dir_row.setSpacing(6)
        self._backup_dir = QLineEdit(self._settings.backup_dir
                                      or str(config.default_backup_dir()))
        browse = QToolButton()
        browse.setIcon(svg_icon("folder"))
        browse.setIconSize(QSize(16, 16))
        browse.setCursor(Qt.PointingHandCursor)
        browse.setAutoRaise(True)
        browse.setToolTip(i18n.t("settings.btn_browse_folder"))
        browse.clicked.connect(self._pick_backup_dir)
        dir_row.addWidget(self._backup_dir, 1)
        dir_row.addWidget(browse)
        dir_wrap = QWidget()
        dir_wrap.setLayout(dir_row)
        form.addRow(i18n.t("settings.label_backup_dir"), dir_wrap)

        # 백업이 메모와 같은 디스크에 있으면 디스크가 고장 날 때 함께 잃는다.
        self._backup_dir_hint = QLabel(i18n.t("settings.note_backup_same_disk"))
        self._backup_dir_hint.setProperty("role", "subtle")
        self._backup_dir_hint.setWordWrap(True)
        form.addRow("", self._backup_dir_hint)
        self._backup_dir.textChanged.connect(self._refresh_backup_dir_hint)
        self._refresh_backup_dir_hint()

        interval_row = QHBoxLayout()
        interval_row.setSpacing(6)
        self._backup_interval = QSpinBox()
        self._backup_interval.setRange(1, 999)
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
        self._backup_unit.setFixedWidth(96)
        interval_row.addWidget(self._backup_interval, 1)
        interval_row.addWidget(self._backup_unit)
        interval_wrap = QWidget()
        interval_wrap.setLayout(interval_row)
        form.addRow(i18n.t("settings.label_backup_interval"), interval_wrap)

        self._backup_keep = QSpinBox()
        self._backup_keep.setRange(1, 1000)
        self._backup_keep.setSuffix(i18n.t("settings.backup_keep_suffix"))
        self._backup_keep.setValue(self._settings.backup_keep_count)
        form.addRow(i18n.t("settings.label_backup_keep"), self._backup_keep)
        _align_form_labels(form)
        outer.addWidget(form_wrap)

        outer.addStretch(1)

        self._backup_enabled = QCheckBox(i18n.t("settings.check_backup_enabled"))
        self._backup_enabled.setChecked(self._settings.backup_enabled)
        # 켜고 끄는 칸은 한 줄, 단추들은 그 아래 줄에 오른쪽으로 모은다(단추가
        # 셋이라 한 줄에 같이 두면 글자가 잘린다).
        check_row = QHBoxLayout()
        check_row.setContentsMargins(8, 0, 0, 0)   # 왼쪽 8 = 폼의 왼쪽 inset 과 동일
        check_row.addWidget(self._backup_enabled)
        check_row.addStretch(1)
        outer.addLayout(check_row)
        bottom = QHBoxLayout()
        bottom.setContentsMargins(8, 0, 0, 0)
        bottom.setSpacing(10)
        bottom.addStretch(1)
        # 참여한 PC 가 받아 둔 메모 사본. 공유 PC 가 고장 났을 때 여기서 꺼낸다.
        copies_btn = QPushButton(i18n.t("settings.btn_copies"))
        copies_btn.setProperty("secondary", True)
        refresh_style(copies_btn)
        copies_btn.setCursor(Qt.PointingHandCursor)
        copies_btn.clicked.connect(self.copiesRequested.emit)
        bottom.addWidget(copies_btn)
        if self._role == "host":
            restore_btn = QPushButton(i18n.t("settings.btn_restore"))
            restore_btn.setProperty("secondary", True)
            refresh_style(restore_btn)
            restore_btn.setMinimumWidth(_DIALOG_BUTTON_WIDTH)
            restore_btn.setCursor(Qt.PointingHandCursor)
            restore_btn.clicked.connect(self.restoreRequested.emit)
            bottom.addWidget(restore_btn)
            now_btn = QPushButton(i18n.t("settings.btn_backup_now"))
            now_btn.setProperty("secondary", True)
            refresh_style(now_btn)
            # 고정 폭은 re-polish 뒤에 지정해야 스타일시트 최소 폭에 덮이지 않는다.
            now_btn.setMinimumWidth(_DIALOG_BUTTON_WIDTH)
            now_btn.setCursor(Qt.PointingHandCursor)
            now_btn.clicked.connect(self.backupNowRequested.emit)
            bottom.addWidget(now_btn)
        outer.addLayout(bottom)

        if self._role != "host":
            note_wrap = QWidget()
            note_v = QVBoxLayout(note_wrap)
            note_v.setContentsMargins(8, 0, 8, 0)
            note = QLabel(i18n.t("settings.note_backup_guest"))
            note.setProperty("role", "subtle")
            note.setWordWrap(True)
            note_v.addWidget(note)
            outer.addWidget(note_wrap)
        return w

    def _build_about_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 12, 8, 8)
        v.setSpacing(6)
        title = QLabel(i18n.t("app.name"))
        title.setStyleSheet("font-size: 18px; font-weight: 800; color: #1F1F1F;")
        v.addWidget(title)
        version = QLabel(
            i18n.t("settings.about_version", version=config.APP_VERSION))
        version.setProperty("role", "subtle")
        v.addWidget(version)
        desc = QLabel(i18n.t("settings.about_description"))
        desc.setWordWrap(True)
        v.addWidget(desc)
        v.addSpacing(8)
        credit = QLabel(i18n.t("settings.about_credit"))
        credit.setProperty("role", "subtle")
        credit.setWordWrap(True)
        v.addWidget(credit)
        v.addStretch(1)
        return w

    def _refresh_backup_dir_hint(self) -> None:
        """백업 폴더가 메모 파일과 같은 드라이브면 안내를 보인다(호스트만)."""
        def drive(p: str) -> str:
            return os.path.splitdrive(os.path.abspath(p))[0].lower()
        folder = self._backup_dir.text().strip() or str(config.default_backup_dir())
        same = drive(folder) == drive(str(config.default_db_path()))
        self._backup_form.setRowVisible(
            self._backup_dir_hint, self._role == "host" and same)

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
                want_menu, i18n.t("jumplist.new_note_title"))

        s.new_note_private = bool(self._new_note_scope.currentData())
        s.notify_new_note = self._notify_new_note.isChecked()
        s.move_checked_to_bottom = self._move_checked_to_bottom.isChecked()
        s.image_viewer = self._image_viewer.currentData() or IMAGE_VIEWER_BUILTIN
        s.fold_style = self._fold_style.currentData() or "slip"
        s.fold_animation = self._fold_animation.isChecked()
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

        s.save()
        self.accept()
