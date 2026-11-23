"""공유 PC 와 다시 붙었을 때, 끊긴 동안 쓴 메모를 어떻게 할지 묻는 창.

물음 한 줄, 저장하면 어떻게 되는지 한 줄, 그 아래에 메모마다 한 줄씩(생성 또는
수정, 그 메모의 첫 문장) 보여 준다. 목록은 접었다 펼 수 있다. 고를 수 있는 것은
셋이다: 저장(공유 PC 에 나만 보기 메모로 올린다), 삭제, 보류(나중에 정한다).

되돌릴 수 없는 삭제는 기본 단추로 두지 않는다. Enter 는 저장, Esc 와 닫기는
보류다. 삭제를 고르면 부르는 쪽이 한 번 더 확인한다."""
from __future__ import annotations

import re

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import i18n
from ..offline_drafts import KIND_EDIT, KIND_NEW
from . import confirm
from .icons import svg_icon
from .rich_text import has_non_checkbox_image, html_to_plain
from .theme import COLOR_TEXT_SUBTLE, RoundedScrollBar, refresh_style

KEEP, DISCARD, LATER = "keep", "discard", "later"

_WIDTH = 440
_MARGIN = 20
_ROW_HEIGHT = 26
# 이보다 많으면 목록 안에서 굴려 본다.
_VISIBLE_ROWS = 6
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|(?<=[。！？])")


def first_sentence(content: str) -> str:
    """메모의 첫 문장. 글이 없으면 내용의 종류를 적는다."""
    lines = [ln.strip() for ln in html_to_plain(content or "").replace(
        "￼", "").splitlines()]
    first = next((ln for ln in lines if ln), "")
    if not first:
        return i18n.t("main.note_title_image" if has_non_checkbox_image(content or "")
                      else "main.note_title_empty")
    return next((part for part in _SENTENCE_END.split(first) if part.strip()),
                first).strip()


def question(rows: list[tuple[str, str]]) -> str:
    """물음. 무엇이 있는지(만든 것, 고친 것, 둘 다)에 따라 달라진다."""
    created = any(kind == KIND_NEW for kind, _text in rows)
    edited = any(kind == KIND_EDIT for kind, _text in rows)
    if created and edited:
        return i18n.t("drafts.body_both")
    return i18n.t("drafts.body_edit" if edited else "drafts.body_new")


class DraftsDialog(QDialog):
    def __init__(self, rows: list[tuple[str, str]], parent: QWidget | None = None):
        """``rows`` 는 (KIND_NEW 또는 KIND_EDIT, 그 메모의 첫 문장)들이다."""
        super().__init__(parent)
        self.setWindowTitle(i18n.t("drafts.title"))
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self._choice = LATER
        self._rows = list(rows)
        edited = any(kind == KIND_EDIT for kind, _text in rows)
        inner = _WIDTH - 2 * _MARGIN

        root = QVBoxLayout(self)
        root.setContentsMargins(_MARGIN, 18, _MARGIN, 16)
        root.setSpacing(0)

        self._question = QLabel()
        self._question.setTextFormat(Qt.PlainText)
        self._question.setStyleSheet("font-size: 13.5px; font-weight: 600;")
        self._question.ensurePolished()
        self._question.setText(confirm.break_lines(
            question(rows), self._question.font(), inner))
        root.addWidget(self._question)
        root.addSpacing(6)

        self._detail = QLabel()
        self._detail.setTextFormat(Qt.PlainText)
        self._detail.setStyleSheet(f"font-size: 12px; color: {COLOR_TEXT_SUBTLE};")
        self._detail.ensurePolished()
        self._detail.setText(confirm.break_lines(
            i18n.t("drafts.detail_edit" if edited else "drafts.detail_new"),
            self._detail.font(), inner))
        root.addWidget(self._detail)
        root.addSpacing(14)

        # 목록을 접고 펴는 줄. 화살표와 '메모 n개'.
        self._toggle = QToolButton()
        self._toggle.setCheckable(True)
        self._toggle.setChecked(True)
        self._toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._toggle.setIconSize(QSize(14, 14))
        self._toggle.setCursor(Qt.PointingHandCursor)
        self._toggle.setText(i18n.t("drafts.list_toggle", count=len(rows)))
        self._toggle.setStyleSheet(
            "QToolButton { background: transparent; border: none; padding: 2px 4px "
            "2px 0; color: #374151; font-size: 12px; font-weight: 700; }"
            "QToolButton:hover { color: #111827; }")
        self._toggle.toggled.connect(self._set_expanded)
        root.addWidget(self._toggle, 0, Qt.AlignLeft)
        root.addSpacing(6)

        self._list = QScrollArea()
        self._list.setObjectName("DraftList")
        self._list.setWidgetResizable(True)
        self._list.setFrameShape(QFrame.NoFrame)
        self._list.setVerticalScrollBar(RoundedScrollBar())
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._list.setStyleSheet(
            "QScrollArea#DraftList { background: #FAFBFD; "
            "border: 1px solid #ECECEC; border-radius: 10px; }")
        self._list.viewport().setStyleSheet("background: transparent;")
        self._list.setAccessibleName(self._toggle.text())
        holder = QWidget()
        holder.setStyleSheet("background: transparent;")
        column = QVBoxLayout(holder)
        column.setContentsMargins(12, 6, 12, 6)
        column.setSpacing(0)
        tags = {KIND_NEW: i18n.t("drafts.tag_new"), KIND_EDIT: i18n.t("drafts.tag_edit")}
        tag_font = self.font()
        tag_font.setPixelSize(11)
        tag_font.setBold(True)
        tag_width = max(QFontMetrics(tag_font).horizontalAdvance(t)
                        for t in tags.values()) + 4
        text_width = inner - 2 * 12 - tag_width - 10 - 12
        for kind, text in rows:
            row = QWidget()
            row.setFixedHeight(_ROW_HEIGHT)
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            line.setSpacing(10)
            tag = QLabel(tags.get(kind, ""))
            tag.setFont(tag_font)
            tag.setFixedWidth(tag_width)
            tag.setStyleSheet(f"color: {COLOR_TEXT_SUBTLE};")
            sentence = QLabel()
            sentence.setTextFormat(Qt.PlainText)        # 메모의 글 그대로다
            sentence.setStyleSheet("font-size: 12.5px; color: #1F1F1F;")
            sentence.ensurePolished()
            sentence.setText(sentence.fontMetrics().elidedText(
                text, Qt.ElideRight, text_width))
            sentence.setToolTip(text if sentence.text() != text else "")
            row.setAccessibleName(f"{tag.text()}, {text}")
            line.addWidget(tag, 0, Qt.AlignVCenter)
            line.addWidget(sentence, 1, Qt.AlignVCenter)
            column.addWidget(row)
        self._list.setWidget(holder)
        shown = min(len(rows), _VISIBLE_ROWS)
        self._list.setFixedHeight(shown * _ROW_HEIGHT + 2 * 6 + 2)
        root.addWidget(self._list)
        root.addSpacing(16)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        self._keep = QPushButton(i18n.t("drafts.btn_keep"))
        self._discard = QPushButton(i18n.t("common.btn_delete"))
        self._later = QPushButton(i18n.t("drafts.btn_later"))
        for button, choice in ((self._keep, KEEP), (self._discard, DISCARD),
                               (self._later, LATER)):
            button.setCursor(Qt.PointingHandCursor)
            button.setMinimumWidth(92)
            if button is not self._keep:
                # 권하는 단추 하나만 짙게 그린다.
                button.setProperty("secondary", True)
                refresh_style(button)
            button.clicked.connect(lambda _checked=False, c=choice: self._pick(c))
            buttons.addWidget(button)
        root.addLayout(buttons)
        self._keep.setDefault(True)
        self._keep.setFocus()

        self.setFixedWidth(_WIDTH)
        self._set_expanded(True)

    def _set_expanded(self, expanded: bool) -> None:
        self._toggle.setIcon(svg_icon(
            "chevron_down" if expanded else "chevron_right", 14, "#374151"))
        self._list.setVisible(expanded)
        # 창의 높이를 내용에 맞춘다(폭은 그대로).
        self.setMinimumHeight(0)
        self.setMaximumHeight(16777215)
        self.adjustSize()
        self.setFixedHeight(self.sizeHint().height())

    def _pick(self, choice: str) -> None:
        self._choice = choice
        self.accept()

    def choice(self) -> str:
        """고른 것. 고르지 않고 닫았으면 보류다."""
        return self._choice


def ask(parent: QWidget | None, rows: list[tuple[str, str]]) -> str:
    """창을 띄워 고른 것(KEEP, DISCARD, LATER)을 돌려준다."""
    dialog = DraftsDialog(rows, parent)
    dialog.exec()
    return dialog.choice()


__all__ = ["DISCARD", "DraftsDialog", "KEEP", "LATER", "ask", "first_sentence",
           "question"]
