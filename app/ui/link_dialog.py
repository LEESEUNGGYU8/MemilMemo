"""메모에 파일·폴더·웹 링크를 넣거나 고치는 창.

표시할 내용과 연결할 대상을 따로 받는다. 대상이 이 PC 에서만 통하는 경로이면
그렇다고 알리고, 공유 폴더나 네트워크 드라이브 안의 파일이면 다른 PC 에서도
열리는 네트워크 경로로 바꿔 넣을 수 있게 한다."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import file_links as fl
from .. import i18n
from . import window_frame
from .link_icons import link_icon_pixmap

# 번역 키 검사기가 글자 그대로 적힌 호출만 세므로, 표에 키 이름만 두지 않고
# 호출을 그대로 적어 둔다.
_KIND_LABELS = {
    fl.KIND_FOLDER: lambda: i18n.t("link.kind_folder"),
    fl.KIND_WEB: lambda: i18n.t("link.kind_web"),
    fl.KIND_PDF: lambda: i18n.t("link.kind_pdf"),
    fl.KIND_WORD: lambda: i18n.t("link.kind_word"),
    fl.KIND_HWP: lambda: i18n.t("link.kind_hwp"),
    fl.KIND_SHEET: lambda: i18n.t("link.kind_sheet"),
    fl.KIND_SLIDE: lambda: i18n.t("link.kind_slide"),
    fl.KIND_TEXT: lambda: i18n.t("link.kind_text"),
    fl.KIND_IMAGE: lambda: i18n.t("link.kind_image"),
    fl.KIND_VIDEO: lambda: i18n.t("link.kind_video"),
    fl.KIND_AUDIO: lambda: i18n.t("link.kind_audio"),
    fl.KIND_ARCHIVE: lambda: i18n.t("link.kind_archive"),
    fl.KIND_CODE: lambda: i18n.t("link.kind_code"),
    fl.KIND_APP: lambda: i18n.t("link.kind_app"),
    fl.KIND_FILE: lambda: i18n.t("link.kind_file"),
}


def kind_label(kind: str) -> str:
    """링크 종류의 이름(말풍선과 넣기 창에 쓴다)."""
    return _KIND_LABELS.get(kind, _KIND_LABELS[fl.KIND_FILE])()


class LinkDialog(QDialog):
    """``exec`` 뒤 ``href`` 와 ``text`` 를 읽는다. 고치는 중에 '링크 해제'를
    누르면 ``remove_requested`` 가 참이다."""

    def __init__(self, *, text: str = "", href: str = "",
                 editing: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(i18n.t("link.dialog_title_edit") if editing
                            else i18n.t("link.dialog_title_new"))
        self.setMinimumWidth(460)
        self.href = ""
        self.text = ""
        self.remove_requested = False
        # 대상이 폴더인지: 찾아보기로 고르면 확실하고, 손으로 적으면 끝의
        # 구분자나 실제 확인(이 PC 의 경로일 때만)으로 정한다.
        self._is_dir: bool | None = (fl.is_folder_href(href) if href else None)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(8)

        label_css = "font-size: 12.5px; color: #4B5563;"
        lbl_text = QLabel(i18n.t("link.label_text"))
        lbl_text.setStyleSheet(label_css)
        root.addWidget(lbl_text)
        self._text_edit = QLineEdit(text)
        self._text_edit.setPlaceholderText(i18n.t("link.placeholder_text"))
        self._text_edit.setMinimumHeight(32)
        lbl_text.setBuddy(self._text_edit)
        root.addWidget(self._text_edit)

        root.addSpacing(6)
        lbl_target = QLabel(i18n.t("link.label_target"))
        lbl_target.setStyleSheet(label_css)
        root.addWidget(lbl_target)
        target_row = QHBoxLayout()
        target_row.setSpacing(6)
        self._target_edit = QLineEdit(fl.describe_target(href) if href else "")
        self._target_edit.setPlaceholderText(i18n.t("link.placeholder_target"))
        self._target_edit.setMinimumHeight(32)
        lbl_target.setBuddy(self._target_edit)
        target_row.addWidget(self._target_edit, 1)
        root.addLayout(target_row)

        browse_row = QHBoxLayout()
        browse_row.setSpacing(6)
        self._file_btn = QPushButton(i18n.t("link.btn_pick_file"))
        self._folder_btn = QPushButton(i18n.t("link.btn_pick_folder"))
        for b in (self._file_btn, self._folder_btn):
            b.setProperty("secondary", True)
            b.setAutoDefault(False)
            browse_row.addWidget(b)
        browse_row.addStretch(1)
        root.addLayout(browse_row)
        self._file_btn.clicked.connect(self._pick_file)
        self._folder_btn.clicked.connect(self._pick_folder)

        # 대상 미리보기: 아이콘, 종류, 다른 PC 에서 열리는지.
        info = QWidget()
        info.setObjectName("LinkInfo")
        info.setStyleSheet(
            "#LinkInfo { background: #F5F6F8; border-radius: 8px; }")
        info_row = QHBoxLayout(info)
        info_row.setContentsMargins(12, 10, 12, 10)
        info_row.setSpacing(10)
        self._info_icon = QLabel()
        self._info_icon.setFixedSize(28, 28)
        self._info_icon.setStyleSheet("background: transparent;")
        info_row.addWidget(self._info_icon, 0, Qt.AlignTop)
        info_col = QVBoxLayout()
        info_col.setSpacing(2)
        self._info_kind = QLabel()
        self._info_kind.setStyleSheet(
            "font-size: 12.5px; font-weight: 600; color: #1F2937; "
            "background: transparent;")
        self._info_note = QLabel()
        self._info_note.setWordWrap(True)
        self._info_note.setStyleSheet(
            "font-size: 12px; color: #4B5563; background: transparent;")
        info_col.addWidget(self._info_kind)
        info_col.addWidget(self._info_note)
        self._network_btn = QPushButton(i18n.t("link.btn_use_network"))
        self._network_btn.setProperty("secondary", True)
        self._network_btn.setAutoDefault(False)
        self._network_btn.clicked.connect(self._use_network_path)
        self._network_btn.hide()
        info_col.addWidget(self._network_btn, 0, Qt.AlignLeft)
        info_row.addLayout(info_col, 1)
        root.addSpacing(4)
        root.addWidget(info)

        self._error = QLabel()
        self._error.setWordWrap(True)
        self._error.setStyleSheet("font-size: 12px; color: #B42318;")
        self._error.hide()
        root.addWidget(self._error)

        root.addSpacing(6)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        if editing:
            remove = QPushButton(i18n.t("link.btn_remove"))
            remove.setProperty("secondary", True)
            remove.setAutoDefault(False)
            remove.clicked.connect(self._on_remove)
            buttons.addWidget(remove)
        buttons.addStretch(1)
        cancel = QPushButton(i18n.t("link.btn_cancel"))
        cancel.setProperty("secondary", True)
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        self._ok = QPushButton(i18n.t("link.btn_save") if editing
                               else i18n.t("link.btn_insert"))
        self._ok.setDefault(True)
        self._ok.clicked.connect(self._on_accept)
        buttons.addWidget(cancel)
        buttons.addWidget(self._ok)
        root.addLayout(buttons)

        self._network_path: str | None = None
        self._target_edit.textChanged.connect(self._on_target_edited)
        self._refresh_info()
        if href and not text:
            self._text_edit.setFocus()
        elif not href:
            self._target_edit.setFocus()
        window_frame.install(self, window_frame.DIALOG)

    # ── 찾아보기 ────────────────────────────────────────────────────────

    def _start_dir(self) -> str:
        current = self._target_edit.text().strip().strip('"')
        if fl.is_local_drive(current) or fl.is_unc(current):
            folder = current if self._is_dir else os.path.dirname(current)
            if folder:
                return folder
        return ""

    def _pick_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, i18n.t("link.pick_file_title"), self._start_dir())
        if path:
            self._set_target(os.path.normpath(path), is_dir=False)

    def _pick_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, i18n.t("link.pick_folder_title"), self._start_dir())
        if path:
            self._set_target(os.path.normpath(path), is_dir=True)

    def _set_target(self, path: str, *, is_dir: bool) -> None:
        self._is_dir = is_dir
        self._target_edit.blockSignals(True)
        self._target_edit.setText(path)
        self._target_edit.blockSignals(False)
        if not self._text_edit.text().strip():
            href = fl.parse_target(path, is_dir=is_dir)
            if href:
                self._text_edit.setText(fl.display_name(href))
        self._refresh_info()

    def _on_target_edited(self, _text: str) -> None:
        # 손으로 고치면 폴더인지 다시 판단한다.
        self._is_dir = None
        self._refresh_info()

    # ── 미리보기 ────────────────────────────────────────────────────────

    def _current_href(self) -> str | None:
        raw = self._target_edit.text()
        is_dir = self._is_dir
        stripped = raw.strip().strip('"')
        if is_dir is None and fl.is_local_drive(stripped):
            # 이 PC 의 경로는 바로 확인할 수 있다(네트워크 경로는 느릴 수 있어
            # 여기서 건드리지 않는다).
            try:
                if os.path.isdir(stripped):
                    is_dir = True
            except OSError:
                pass
        return fl.parse_target(raw, is_dir=is_dir)

    def _refresh_info(self) -> None:
        self._error.hide()
        href = self._current_href()
        self._network_path = None
        self._network_btn.hide()
        if not href:
            self._info_icon.setPixmap(link_icon_pixmap(
                fl.KIND_FILE, 28, self.devicePixelRatioF()))
            self._info_kind.setText(i18n.t("link.info_empty_title"))
            self._info_note.setText("")
            self._refit()
            return
        kind = fl.kind_of(href)
        self._info_icon.setPixmap(link_icon_pixmap(
            kind, 28, self.devicePixelRatioF()))
        self._info_kind.setText(kind_label(kind))
        if fl.is_web(href):
            self._info_note.setText(i18n.t("link.info_web"))
            self._refit()
            return
        path = fl.href_to_path(href) or ""
        if fl.is_unc(path):
            note = i18n.t("link.info_network")
        else:
            network = fl.network_path_for(path)
            if network:
                self._network_path = network
                note = i18n.t("link.info_local_shareable", path=network)
                self._network_btn.show()
            else:
                note = i18n.t("link.info_local_only")
        if fl.needs_confirm(href):
            note += "\n" + i18n.t("link.info_executable")
        self._info_note.setText(note)
        self._refit()

    def _refit(self) -> None:
        """안내문이 길어지면(줄바꿈되는 라벨) 창 높이를 다시 잡는다. 그대로 두면
        늘어난 글이 좁은 자리에 눌려 겹쳐 보인다."""
        QTimer.singleShot(0, self._apply_fit)

    def _apply_fit(self) -> None:
        layout = self.layout()
        if layout is None:
            return
        layout.activate()
        need = layout.totalHeightForWidth(self.width())
        if need > 0 and need != self.height():
            self.resize(self.width(), need)

    def _use_network_path(self) -> None:
        if not self._network_path:
            return
        is_dir = self._is_dir
        if is_dir is None:
            href = self._current_href()
            is_dir = bool(href and fl.is_folder_href(href))
        self._set_target(self._network_path, is_dir=bool(is_dir))

    # ── 닫기 ────────────────────────────────────────────────────────────

    def _on_remove(self) -> None:
        self.remove_requested = True
        self.accept()

    def _on_accept(self) -> None:
        href = self._current_href()
        if not href:
            self._error.setText(
                i18n.t("link.error_empty")
                if not self._target_edit.text().strip()
                else i18n.t("link.error_unsupported"))
            self._error.show()
            self._target_edit.setFocus()
            return
        text = " ".join(self._text_edit.text().split())
        self.href = href
        self.text = text or fl.display_name(href)
        self.accept()
