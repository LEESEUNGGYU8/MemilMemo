"""이 PC 에 받아 둔 메모 사본을 열어 보는 창.

공유 PC 가 고장 났을 때 쓰는 화면이다. 사본마다 언제 누구에게서 받은 것인지와
메모가 몇 개인지 보이고, 고르면 그 안의 메모를 읽을 수 있다. 여기서 할 수 있는
일은 둘이다.

- 가져오기: 지금 연결된 공유 PC 에 없는 메모를 사본에서 새 메모로 만든다.
  있는 메모를 덮어쓰지 않는다.
- 이 사본으로 공유 시작: 이 PC 가 공유 PC 가 된다(참여 중인 PC 에서만).

사본 파일은 읽기만 한다."""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import i18n
from ..datetime_fmt import fmt_relative
from ..replica import CopyInfo, missing_notes, read_source
from .icons import color_swatch_icon
from .note_card import note_title_and_lines
from .rich_text import html_to_plain
from .theme import refresh_style

_IMG_TAG = "<img"


def _image_count(html: str) -> int:
    """본문에 든 이미지 수(글머리와 체크 상자 그림은 뺀다)."""
    from .note_fold import _IMG_SRC
    from .rich_text import is_marker_url
    if not html or _IMG_TAG not in html.lower():
        return 0
    return sum(1 for m in _IMG_SRC.finditer(html) if not is_marker_url(m.group(1)))


class CopiesDialog(QDialog):
    importRequested = Signal(list, list)   # 가져올 메모들, 그 사본의 폴더들
    takeoverRequested = Signal(str)        # 공유를 시작할 사본 파일의 경로

    def __init__(self, copies: list[CopyInfo], *,
                 host_notes: dict[str, dict[str, Any]] | None,
                 can_takeover: bool, nickname: str = "",
                 parent: QWidget | None = None):
        """``host_notes`` 는 지금 연결된 공유 PC 의 메모(메모 id → 메모). 연결돼
        있지 않으면 None 이고, 그때는 가져오기를 할 수 없다. ``nickname`` 은 이 PC 가
        공유하던 메모 파일에서 내 것만 고르는 데 쓴다."""
        super().__init__(parent)
        self.setWindowTitle(i18n.t("copies.window_title"))
        self.resize(660, 470)
        self.setMinimumSize(520, 380)
        self._copies = copies
        self._host_notes = host_notes
        self._nickname = nickname
        self._notes: list[dict[str, Any]] = []
        self._folders: list[dict[str, Any]] = []
        self._missing: list[dict[str, Any]] = []
        # 목록의 줄마다 그 줄의 메모(묶음 이름 줄은 None).
        self._rows: list[dict[str, Any] | None] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(10)

        self._pick = QComboBox()
        self._pick.setAccessibleName(i18n.t("copies.window_title"))
        for info in copies:
            when = fmt_relative(info.synced_at)
            if info.kind == "host":
                label = i18n.t("copies.item_own", when=when, count=info.note_count)
            elif info.host:
                label = i18n.t("copies.item", when=when, host=info.host,
                               count=info.note_count)
            else:
                label = i18n.t("copies.item_no_host", when=when,
                               count=info.note_count)
            self._pick.addItem(label, userData=str(info.path))
        root.addWidget(self._pick)

        body = QHBoxLayout()
        body.setSpacing(10)
        self._list = QListWidget()
        self._list.setAccessibleName(i18n.t("copies.list_name"))
        self._list.setMinimumWidth(240)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._list.setTextElideMode(Qt.ElideRight)
        self._list.currentRowChanged.connect(self._show_note)
        body.addWidget(self._list, 5)
        self._preview = QPlainTextEdit()
        self._preview.setReadOnly(True)
        self._preview.setAccessibleName(i18n.t("copies.preview_name"))
        self._preview.setStyleSheet(
            "QPlainTextEdit { background: #FAFBFD; border: 1px solid #ECECEC; "
            "border-radius: 10px; padding: 8px; color: #1F1F1F; }")
        body.addWidget(self._preview, 6)
        root.addLayout(body, 1)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self._takeover_btn = QPushButton(i18n.t("copies.btn_takeover"))
        self._takeover_btn.setProperty("secondary", True)
        refresh_style(self._takeover_btn)
        self._takeover_btn.setCursor(Qt.PointingHandCursor)
        self._takeover_btn.setVisible(can_takeover)
        self._takeover_btn.clicked.connect(self._takeover)
        bottom.addWidget(self._takeover_btn)
        bottom.addStretch(1)
        self._import_btn = QPushButton(i18n.t("copies.btn_import_none"))
        self._import_btn.setCursor(Qt.PointingHandCursor)
        self._import_btn.setMinimumWidth(130)
        self._import_btn.clicked.connect(self._import)
        bottom.addWidget(self._import_btn)
        close = QPushButton(i18n.t("sessions.btn_close"))
        close.setProperty("secondary", True)
        refresh_style(close)
        close.setCursor(Qt.PointingHandCursor)
        close.setMinimumWidth(96)
        close.clicked.connect(self.reject)
        bottom.addWidget(close)
        root.addLayout(bottom)

        self._pick.currentIndexChanged.connect(self._load_copy)
        self._load_copy(0)

    # ── 고른 사본 ──

    def selected_path(self) -> str:
        return self._pick.currentData() or ""

    def _selected(self) -> CopyInfo | None:
        index = self._pick.currentIndex()
        return self._copies[index] if 0 <= index < len(self._copies) else None

    def missing(self) -> list[dict[str, Any]]:
        return list(self._missing)

    def _load_copy(self, _index: int) -> None:
        self._list.clear()
        self._preview.clear()
        self._notes, self._folders, self._missing = [], [], []
        info = self._selected()
        if info is not None:
            try:
                self._notes, self._folders, _meta = read_source(info, self._nickname)
            except Exception:
                self._notes, self._folders = [], []
        self._notes.sort(key=lambda n: n.get("updated_at", 0), reverse=True)
        if self._host_notes is not None:
            self._missing = missing_notes(self._notes, self._host_notes)
        missing_ids = {id(n) for n in self._missing}
        rest = [n for n in self._notes if id(n) not in missing_ids]
        self._rows = []
        # 공유 PC 에 없는 메모를 위에 따로 묶는다. 연결돼 있지 않으면 가를 수 없다.
        groups = ([(i18n.t("copies.group_missing"), self._missing),
                   (i18n.t("copies.group_present"), rest)]
                  if self._missing else [("", self._notes)])
        for heading, notes in groups:
            if heading and notes:
                head = QListWidgetItem(f"{heading} {len(notes)}")
                head.setFlags(Qt.NoItemFlags)
                font = head.font()
                font.setBold(True)
                font.setPixelSize(11)
                head.setFont(font)
                self._list.addItem(head)
                self._rows.append(None)
            for note in notes:
                title = note_title_and_lines(note.get("content") or "")[0]
                item = QListWidgetItem(
                    color_swatch_icon(note.get("color") or "#FFE066", 14), title)
                if note.get("private_owner"):
                    item.setToolTip(i18n.t("folder.private_toggle"))
                self._list.addItem(item)
                self._rows.append(note)
        first = next((i for i, n in enumerate(self._rows) if n is not None), -1)
        if first >= 0:
            self._list.setCurrentRow(first)
        count = len(self._missing)
        self._import_btn.setEnabled(count > 0)
        self._import_btn.setText(
            i18n.t("copies.btn_import", count=count) if count
            else i18n.t("copies.btn_import_none"))
        # 이 PC 가 공유하던 메모 파일은 사본이 아니다. 그것으로 다시 공유하려면
        # 접속 창에서 '이 PC에서 공유 시작'을 고르면 된다.
        self._takeover_btn.setEnabled(
            bool(self._notes) and info is not None and info.kind != "host")

    def _show_note(self, row: int) -> None:
        note = self._rows[row] if 0 <= row < len(self._rows) else None
        if note is None:
            self._preview.clear()
            return
        raw = note.get("content") or ""
        text = html_to_plain(raw).replace("￼", "").strip()
        images = _image_count(raw)
        if images:
            text = (text + "\n\n" if text else "") + i18n.t(
                "copies.images", count=images)
        self._preview.setPlainText(text)

    def _import(self) -> None:
        if self._missing:
            self.importRequested.emit(list(self._missing), list(self._folders))
            self.accept()

    def _takeover(self) -> None:
        path = self.selected_path()
        if path:
            self.takeoverRequested.emit(path)


__all__ = ["CopiesDialog"]
