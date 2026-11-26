"""백업에서 복원: 백업과 받아 둔 사본을 열어 보고, 거기서 메모를 되찾는 창.

여기에 모이는 것은 세 가지다.

- 백업: 공유 PC 가 때맞춰 떠 둔 메모 파일(설정의 백업 폴더).
- 공유 PC 에서 받은 사본: 참여한 PC 가 받은 메모를 그대로 적어 둔 것. 공유 PC 가
  고장 났을 때 쓴다. 이 PC 가 공유 PC 였을 때의 메모 파일도 여기에 넣는다.
- 고른 파일: '파일 선택'으로 고르거나 이 창에 끌어다 놓은 백업 파일.

고르면 그 안의 메모를 읽을 수 있다. 할 수 있는 일은 둘이다.

- 가져오기: 지금 연결된 공유 PC 에 없는 메모를 새 메모로 만든다. 있는 메모를
  덮어쓰지 않는다.
- 복원(공유 PC), 공유 시작(참여한 PC): 메모 전체를 그 내용으로 바꾼다. 실제로
  바꾸는 일과 그 전에 묻는 일은 부르는 쪽(main.py)이 한다.

파일은 읽기만 한다. 고른 파일을 메모 파일로 쓰지 않고, 그 내용으로 메모 파일을
새로 만든다."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import i18n
from ..datetime_fmt import fmt_relative
from ..replica import CopyInfo, missing_notes, read_source, source_from_file
from . import window_frame
from . import confirm
from .icons import color_swatch_icon
from .note_card import note_title_and_lines
from .rich_text import html_to_plain
from .theme import refresh_style

_IMG_TAG = "<img"
BACKUP, COPY, FILE = "backup", "copy", "file"


def _image_count(html: str) -> int:
    """본문에 든 이미지 수(글머리와 체크 상자 그림은 뺀다)."""
    from .note_fold import _IMG_SRC
    from .rich_text import is_marker_url
    if not html or _IMG_TAG not in html.lower():
        return 0
    return sum(1 for m in _IMG_SRC.finditer(html) if not is_marker_url(m.group(1)))


def _size_text(path: Path) -> str:
    try:
        size = path.stat().st_size
    except OSError:
        return ""
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{max(1, round(size / 1024))} KB"


def _group(info: CopyInfo) -> str:
    if info.picked:
        return FILE
    return BACKUP if info.kind == "backup" else COPY


def _label(info: CopyInfo) -> str:
    when = fmt_relative(info.synced_at)
    if info.picked or info.kind == "backup":
        name = info.path.name if info.picked else when
        size = _size_text(info.path)
        return f"{name}    {size}" if size else name
    if info.kind == "host":
        return i18n.t("copies.item_own", when=when, count=info.note_count)
    if info.host:
        return i18n.t("copies.item", when=when, host=info.host,
                      count=info.note_count)
    return i18n.t("copies.item_no_host", when=when, count=info.note_count)


class CopiesDialog(QDialog):
    importRequested = Signal(list, list)   # 가져올 메모들, 그 사본의 폴더들
    takeoverRequested = Signal(str)        # 공유를 시작할 사본이나 백업 파일의 경로
    restoreRequested = Signal(str)         # 복원할 백업 파일의 경로

    def __init__(self, copies: list[CopyInfo], *,
                 host_notes: dict[str, dict[str, Any]] | None,
                 can_takeover: bool, nickname: str = "",
                 parent: QWidget | None = None, can_restore: bool = False):
        """``copies`` 는 보여 줄 것들(백업과 사본). ``host_notes`` 는 지금 연결된
        공유 PC 의 메모(메모 id → 메모). 연결돼 있지 않으면 None 이고, 그때는
        가져오기를 할 수 없다. ``nickname`` 은 공유 PC 의 메모 파일에서 내 것만
        고르는 데 쓴다. ``can_restore`` 는 공유 PC 에서, ``can_takeover`` 는 참여한
        PC 에서 참이다."""
        super().__init__(parent)
        self.setWindowTitle(i18n.t("restore.window_title"))
        self.resize(660, 470)
        self.setMinimumSize(520, 380)
        self.setAcceptDrops(True)
        self._copies = list(copies)
        self._host_notes = host_notes
        self._nickname = nickname
        self._can_takeover = bool(can_takeover)
        self._can_restore = bool(can_restore)
        self._notes: list[dict[str, Any]] = []
        self._folders: list[dict[str, Any]] = []
        self._missing: list[dict[str, Any]] = []
        # 목록의 줄마다 그 줄의 메모(묶음 이름 줄은 None).
        self._rows: list[dict[str, Any] | None] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(8)
        self._pick = QComboBox()
        self._pick.setAccessibleName(i18n.t("restore.label_pick"))
        top.addWidget(self._pick, 1)
        self._file_btn = QPushButton(i18n.t("restore.btn_file"))
        self._file_btn.setProperty("secondary", True)
        refresh_style(self._file_btn)
        self._file_btn.setCursor(Qt.PointingHandCursor)
        self._file_btn.setAutoDefault(False)
        self._file_btn.clicked.connect(self._pick_file)
        top.addWidget(self._file_btn)
        root.addLayout(top)

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
        # 메모 전체를 고른 것의 내용으로 바꾸는 단추. 공유 PC 에서는 '복원',
        # 참여한 PC 에서는 '공유 시작'이다.
        self._takeover_btn = QPushButton(i18n.t("copies.btn_takeover"))
        self._takeover_btn.setProperty("secondary", True)
        refresh_style(self._takeover_btn)
        self._takeover_btn.setCursor(Qt.PointingHandCursor)
        self._takeover_btn.setAutoDefault(False)
        self._takeover_btn.setVisible(self._can_takeover or self._can_restore)
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
        self._fill_pick()
        window_frame.install(self, window_frame.DIALOG)

    # ── 고를 것들 ──

    def _fill_pick(self, select: str = "") -> None:
        """고르는 칸을 다시 채운다. 종류가 섞여 있으면 묶음마다 이름을 붙인다."""
        pick = self._pick
        pick.blockSignals(True)
        pick.clear()
        groups = [g for g in (FILE, BACKUP, COPY)
                  if any(_group(c) == g for c in self._copies)]
        ordered = self._copies if len(groups) < 2 else [
            c for g in groups for c in self._copies if _group(c) == g]
        seen = ""
        for info in ordered:
            group = _group(info)
            if len(groups) > 1 and group != seen:
                seen = group
                pick.addItem(i18n.t(f"restore.group_{group}"))
                head = pick.model().item(pick.count() - 1)
                head.setFlags(Qt.NoItemFlags)
                font = head.font()
                font.setBold(True)
                font.setPixelSize(11)
                head.setFont(font)
            pick.addItem(_label(info), userData=str(info.path))
            pick.setItemData(pick.count() - 1, str(info.path), Qt.ToolTipRole)
        empty = not self._copies
        pick.setEnabled(not empty)
        if empty:
            pick.addItem(i18n.t("restore.empty"))
        index = pick.findData(select) if select else -1
        if index < 0:
            index = next((i for i in range(pick.count()) if pick.itemData(i)), 0)
        pick.setCurrentIndex(index)
        pick.blockSignals(False)
        self._load_copy(index)

    def sources(self) -> list[CopyInfo]:
        return list(self._copies)

    def add_file(self, path: str | Path) -> bool:
        """사용자가 고르거나 끌어다 놓은 파일을 목록에 넣고 고른다. 메모 백업이
        아니면 알리고 False."""
        path = Path(path)
        known = next((c for c in self._copies if c.path == path), None)
        if known is None:
            info = source_from_file(path)
            if info is None:
                confirm.notify(self, i18n.t("restore.window_title"),
                               i18n.t("restore.err_not_backup"),
                               icon=QMessageBox.Warning)
                return False
            self._copies.append(info)
        self._fill_pick(str(path))
        return True

    def _pick_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, i18n.t("restore.btn_file"), "", i18n.t("restore.file_filter"))
        if path:
            self.add_file(path)

    @staticmethod
    def _dropped_file(event) -> str:
        data = event.mimeData()
        if not data.hasUrls():
            return ""
        files = [u.toLocalFile() for u in data.urls() if u.isLocalFile()]
        return files[0] if len(files) == 1 and Path(files[0]).is_file() else ""

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if self._dropped_file(event):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        path = self._dropped_file(event)
        if path:
            event.acceptProposedAction()
            self.add_file(path)

    # ── 고른 것 ──

    def selected_path(self) -> str:
        return self._pick.currentData() or ""

    def _selected(self) -> CopyInfo | None:
        path = self.selected_path()
        return next((c for c in self._copies if str(c.path) == path), None)

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
        groups = ([("copies.group_missing", self._missing),
                   ("copies.group_present", rest)]
                  if self._missing else [("", self._notes)])
        for heading, notes in groups:
            if heading and notes:
                head = QListWidgetItem(i18n.t(heading, count=len(notes)))
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
        self._refresh_action(info)

    def _refresh_action(self, info: CopyInfo | None) -> None:
        """메모 전체를 바꾸는 단추의 이름과, 누를 수 있는지."""
        backup = info is not None and info.kind == "backup"
        usable = bool(self._notes) and info is not None
        if self._can_restore:
            # 공유 PC 는 제 메모 파일의 백업으로만 되돌린다. 받아 둔 사본에는 남의
            # '나만 보기' 메모와 수정 이력이 없다. 거기서는 빠진 메모만 가져온다.
            self._takeover_btn.setText(i18n.t("restore.btn_restore"))
            self._takeover_btn.setEnabled(usable and backup)
            return
        self._takeover_btn.setText(i18n.t(
            "restore.btn_takeover_backup" if backup else "copies.btn_takeover"))
        # 이 PC 가 공유하던 메모 파일은 사본이 아니다. 그것으로 다시 공유하려면
        # 접속 창에서 '이 PC에서 공유 시작'을 고르면 된다.
        self._takeover_btn.setEnabled(usable and info.kind != "host")

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
        if not path:
            return
        if self._can_restore:
            self.restoreRequested.emit(path)
        else:
            self.takeoverRequested.emit(path)


__all__ = ["CopiesDialog"]
