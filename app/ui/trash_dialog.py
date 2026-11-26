"""휴지통: 지운 메모를 되살리거나 완전히 지운다.

폴더 창과 같은 틀이다. 지운 메모를 아이콘, 리스트, 그리드로 늘어놓고 보기 방식과
밀도를 고른다. 아래에는 휴지통에만 있는 것이 붙는다: 되살리기와 완전히 지우기,
비우기. 두 번 누르면 그 메모의 글과 삭제 정보(누가 언제 지웠고 얼마나 남았는지)를
미리 본다.

리스트와 그리드의 카드는 체크하듯 고른다. 누르면 켜지고 다시 누르면 꺼진다. 빈 곳이
거의 없는 보기라, 하나만 고르는 방식으로는 고른 것을 풀 길이 없다.

지운 메모는 공유 PC 가 보관 기간 동안 들고 있다. 이 창은 공유 PC 에 물어 받은
목록을 보여 줄 뿐이고, 되살리기와 지우기는 부르는 쪽(메인 창)이 공유 PC 에 청한다.
남이 혼자 보던 메모는 목록에 오지 않는다."""
from __future__ import annotations

import time
import weakref
from typing import Any

from PySide6.QtCore import QEvent, QPoint, Qt, Signal
from PySide6.QtGui import QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import i18n
from ..datetime_fmt import fmt_relative
from ..settings import AppSettings
from . import window_frame
from . import confirm, edit_menu
from .folder_window import FolderWindow
from .icons import svg_icon
from .menu_item import add_centered_menu_action
from .note_card import NoteCard, note_title_and_lines
from .rich_text import html_to_plain
from .theme import refresh_style

_DAY_MS = 24 * 60 * 60 * 1000


def days_left(deleted_at: int, keep_days: int, now_ms: int | None = None) -> int:
    """완전히 지워지기까지 남은 날 수(오늘 지워지면 0)."""
    now = int(time.time() * 1000) if now_ms is None else now_ms
    remaining = int(deleted_at) + keep_days * _DAY_MS - now
    return max(0, -(-remaining // _DAY_MS))


def _button(key: str, *, secondary: bool = False) -> QPushButton:
    button = QPushButton(i18n.t(key))
    if secondary:
        button.setProperty("secondary", True)
        refresh_style(button)
    button.setCursor(Qt.PointingHandCursor)
    return button


class _Preview(QDialog):
    """지운 메모 한 장의 글. 남이 쓴 글이라 꾸밈 없이 글자만 보인다."""

    restoreRequested = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.resize(420, 380)
        self.setMinimumSize(320, 260)
        self._note_id = ""
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 12)
        root.setSpacing(10)
        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        self._text.setAccessibleName(i18n.t("copies.preview_name"))
        self._text.setStyleSheet(
            "QPlainTextEdit { background: #FAFBFD; border: 1px solid #ECECEC; "
            "border-radius: 10px; padding: 8px; color: #1F1F1F; }")
        root.addWidget(self._text, 1)
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background: #ECECEC;")
        root.addWidget(line)
        # 삭제 정보: 이름표와 값을 줄마다 나란히.
        bottom = QHBoxLayout()
        bottom.setSpacing(12)
        info = QGridLayout()
        info.setContentsMargins(0, 0, 0, 0)
        info.setHorizontalSpacing(14)
        info.setVerticalSpacing(4)
        self._facts: dict[str, QLabel] = {}
        for row, key in enumerate(("by", "when", "left")):
            name = QLabel(i18n.t(f"trash.info_{key}"))
            name.setProperty("role", "subtle")
            value = QLabel("")
            value.setTextFormat(Qt.PlainText)          # 남의 닉네임이 들어간다
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            name.setBuddy(value)
            info.addWidget(name, row, 0)
            info.addWidget(value, row, 1)
            self._facts[key] = value
        info.setColumnStretch(1, 1)
        bottom.addLayout(info, 1)
        self._restore_btn = _button("trash.btn_restore")
        self._restore_btn.setMinimumWidth(110)
        self._restore_btn.clicked.connect(self._restore)
        bottom.addWidget(self._restore_btn, 0, Qt.AlignBottom)
        root.addLayout(bottom)
        window_frame.install(self, window_frame.DIALOG)

    def note_id(self) -> str:
        return self._note_id

    def text(self) -> str:
        return self._text.toPlainText()

    def facts(self) -> dict[str, str]:
        return {key: label.text() for key, label in self._facts.items()}

    def show_note(self, note: dict[str, Any], keep_days: int) -> None:
        self._note_id = note["id"]
        raw = note.get("content") or ""
        self.setWindowTitle(note_title_and_lines(raw)[0]
                            or i18n.t("trash.window_title"))
        deleted_at = int(note.get("deleted_at") or 0)
        self._facts["by"].setText(str(note.get("deleted_by") or ""))
        self._facts["when"].setText(fmt_relative(deleted_at))
        self._facts["left"].setText(
            i18n.t("trash.info_days", days=days_left(deleted_at, keep_days)))
        self._text.setPlainText(html_to_plain(raw).replace("￼", "").strip())
        self.show()
        self.raise_()
        self.activateWindow()

    def _restore(self) -> None:
        if self._note_id:
            self.restoreRequested.emit(self._note_id)
        self.hide()


class TrashDialog(FolderWindow):
    restoreRequested = Signal(list)     # 되살릴 메모 id 들
    purgeRequested = Signal(list)       # 완전히 지울 메모 id 들
    emptyRequested = Signal()

    def __init__(self, keep_days: int, settings: AppSettings | None = None,
                 parent: QWidget | None = None, *, icon: QIcon | None = None):
        super().__init__(
            "", settings if settings is not None else AppSettings(),
            icon=icon, parent=parent,
            view_keys=("trash_view_layout", "trash_view_density"),
            own_menu=False)
        self.resize(640, 440)
        self.setMinimumSize(420, 300)
        self._keep_days = int(keep_days)
        self._notes: list[dict[str, Any]] = []
        self._chosen: list[str] = []
        self._anchor = ""
        self._preview: _Preview | None = None
        self._empty.setText(i18n.t("trash.empty"))

        bottom = QHBoxLayout()
        bottom.setContentsMargins(14, 8, 14, 12)
        bottom.setSpacing(8)
        self._empty_btn = _button("trash.btn_empty", secondary=True)
        self._empty_btn.clicked.connect(self._empty_all)
        bottom.addWidget(self._empty_btn)
        bottom.addStretch(1)
        self._purge_btn = _button("trash.btn_purge", secondary=True)
        self._purge_btn.clicked.connect(self._purge)
        bottom.addWidget(self._purge_btn)
        self._restore_btn = _button("trash.btn_restore")
        self._restore_btn.setMinimumWidth(110)
        self._restore_btn.clicked.connect(self._restore)
        bottom.addWidget(self._restore_btn)
        self.layout().addLayout(bottom)

        self._icons.setAccessibleName(i18n.t("trash.list_name"))
        self._icons.selectionChanged.connect(self._on_icons_selection)
        self._icons.menuRequested.connect(self._show_menu)
        self.noteActivated.connect(self._show_preview)      # 두 번 누름, Enter
        self.deleteRequested.connect(self._purge_ids)       # 아이콘 보기의 Delete
        # 카드 사이나 아래의 빈 곳을 누르면 고른 것이 풀린다.
        self._cards_container.installEventFilter(self)
        # 리스트와 그리드에서도 Ctrl+A 로 모두 고른다.
        everything = QShortcut(QKeySequence.SelectAll, self)
        everything.setContext(Qt.WidgetWithChildrenShortcut)
        everything.activated.connect(lambda: self.select(self.note_ids()))
        self.set_notes([])

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        # 카드가 받지 않은 누름은 이 바탕으로도 넘어온다. 카드 위를 누른 것은
        # 빈 곳을 누른 것이 아니다(여기서 풀면 카드가 곧바로 다시 체크한다).
        if (obj is self._cards_container
                and event.type() == QEvent.MouseButtonPress
                and event.button() == Qt.LeftButton and self._chosen
                and self._cards_container.childAt(event.position().toPoint()) is None):
            self.select([])
        return super().eventFilter(obj, event)

    # ── 목록 ──

    def set_notes(self, notes: list[dict[str, Any]],
                  keep_days: int | None = None) -> None:
        """공유 PC 가 보낸 휴지통의 메모들로 바꾼다(방금 지운 것부터). 고르고
        있던 것은 남아 있으면 그대로 둔다."""
        if keep_days:
            self._keep_days = int(keep_days)
        self._notes = [n for n in notes if isinstance(n, dict) and n.get("id")]
        self._notes.sort(key=lambda n: int(n.get("deleted_at") or 0), reverse=True)
        alive = set(self.note_ids())
        self._chosen = [nid for nid in self._chosen if nid in alive]
        if self._anchor not in alive:
            self._anchor = ""
        items = [{
            "id": note["id"],
            "title": note_title_and_lines(note.get("content") or "")[0],
            "color": note.get("color") or "#FFE066",
            "private": bool(note.get("private_owner")),
            "hidden": False, "content": note.get("content") or "", "note": note,
        } for note in self._notes]
        self.set_content(i18n.t("trash.window_title"), items)
        self._count.setText("")
        self._empty_btn.setEnabled(bool(self._notes))
        if self._preview is not None and self._preview.note_id() not in alive:
            self._preview.hide()
        self._apply_selection()

    def note_ids(self) -> list[str]:
        return [n["id"] for n in self._notes]

    def selected_ids(self) -> list[str]:
        return list(self._chosen)

    def select(self, ids: list[str]) -> None:
        alive = set(self.note_ids())
        self._chosen = [nid for nid in dict.fromkeys(ids) if nid in alive]
        self._apply_selection()

    def _note(self, note_id: str) -> dict[str, Any] | None:
        return next((n for n in self._notes if n["id"] == note_id), None)

    def _dress_card(self, card: NoteCard, item: dict[str, Any]) -> None:
        # 휴지통의 카드는 고르는 것만 된다. 시각 자리에는 남은 날을 적는다.
        # 누가 언제 지웠는지는 미리 보기에 있다.
        left = days_left(int(item["note"].get("deleted_at") or 0), self._keep_days)
        card.set_plain(True)
        card.set_caption(i18n.t("trash.days_left", days=left), "")
        if not card.property("trashWired"):
            card.setProperty("trashWired", True)
            card.clicked.connect(self._on_card_clicked)
            card.doubleClicked.connect(self._show_preview)
            card.menuRequested.connect(self._on_card_menu)
            card.deleteRequested.connect(self._on_card_delete)

    # ── 고르기 ──

    def _apply_selection(self) -> None:
        chosen = set(self._chosen)
        for nid, card in self._cards.items():
            card.set_selected(nid in chosen)
        if self._icons.selected_ids() != self._chosen:
            self._icons.select(self._chosen)
        self._restore_btn.setEnabled(bool(self._chosen))
        self._purge_btn.setEnabled(bool(self._chosen))

    def _on_icons_selection(self) -> None:
        ids = self._icons.selected_ids()
        if ids != self._chosen:
            self._chosen = ids
            self._apply_selection()

    def _on_card_clicked(self, note_id: str) -> None:
        ids = self.note_ids()
        if note_id not in ids:
            return
        mods = QApplication.keyboardModifiers()
        if mods & Qt.ShiftModifier and self._anchor in ids:
            # 마지막으로 누른 것부터 여기까지를 보탠다.
            a, b = sorted((ids.index(self._anchor), ids.index(note_id)))
            added = [n for n in ids[a:b + 1] if n not in self._chosen]
            self._chosen = self._chosen + added
        else:
            # 체크처럼 켜고 끈다. 다른 것은 그대로 둔다.
            self._chosen = ([n for n in self._chosen if n != note_id]
                            if note_id in self._chosen
                            else self._chosen + [note_id])
            self._anchor = note_id
        self._apply_selection()

    # ── 메뉴와 미리 보기 ──

    def _on_card_menu(self, note_id: str, pos: QPoint) -> None:
        if note_id not in self._chosen:
            self._chosen, self._anchor = [note_id], note_id
            self._apply_selection()
        self._show_menu(pos)

    def _show_menu(self, pos: QPoint) -> None:
        if not self._chosen:
            return
        me = weakref.ref(self)      # 메뉴의 항목이 이 창을 붙들고 있지 않게

        def act(what: str) -> None:
            window = me()
            if window is not None:
                window._restore() if what == "restore" else window._purge()

        menu = edit_menu.new_menu(self)
        ink = "#3A3A3A"
        add_centered_menu_action(
            menu, svg_icon("undo", color=ink), i18n.t("trash.btn_restore"),
            lambda: act("restore"))
        menu.addSeparator()
        add_centered_menu_action(
            menu, svg_icon("trash", color=ink), i18n.t("trash.btn_purge"),
            lambda: act("purge"))
        edit_menu.run_menu(menu, pos)
        menu.deleteLater()

    def _show_preview(self, note_id: str) -> None:
        note = self._note(note_id)
        if note is None:
            return
        if self._preview is None:
            self._preview = _Preview(self)
            self._preview.restoreRequested.connect(
                lambda nid: self.restoreRequested.emit([nid]))
        self._preview.show_note(note, self._keep_days)

    # ── 하는 일 ──

    def _restore(self) -> None:
        ids = self.selected_ids()
        if ids:
            self.restoreRequested.emit(ids)

    def _purge(self) -> None:
        self._purge_ids(self.selected_ids())

    def _on_card_delete(self, note_id: str) -> None:
        # 글쇠를 받은 카드가 고른 것에 없으면 그것만 지운다.
        self._purge_ids(self._chosen if note_id in self._chosen else [note_id])

    def _purge_ids(self, ids: list[str]) -> None:
        ids = [nid for nid in ids if self._note(nid) is not None]
        if ids and confirm.ask(
                self, i18n.t("trash.btn_purge"),
                i18n.t("trash.purge_body", count=len(ids)),
                detail=i18n.t("trash.purge_detail"),
                action=i18n.t("common.btn_delete"), danger=True):
            self.purgeRequested.emit(ids)

    def _empty_all(self) -> None:
        if self._notes and confirm.ask(
                self, i18n.t("trash.btn_empty"),
                i18n.t("trash.empty_body", count=len(self._notes)),
                detail=i18n.t("trash.purge_detail"),
                action=i18n.t("trash.btn_empty"), danger=True):
            self.emptyRequested.emit()


__all__ = ["TrashDialog", "days_left"]
