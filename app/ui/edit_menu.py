"""글 입력칸의 우클릭 메뉴.

Qt 가 기본으로 띄우는 메뉴는 화면 언어와 무관하게 영어(Undo, Cut…)로 나오고
모양도 앱의 다른 메뉴와 다르다. 여기서 같은 일을 하는 메뉴를 앱의 모양으로
만든다. 메모 본문은 스티키 메모가 직접 부르고(그림이나 보기 상태에 따른 항목이
더 붙는다), 검색칸 같은 한 줄 입력칸은 :class:`EditMenuFilter` 가 맡는다."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QLineEdit,
    QMenu,
    QTextEdit,
)

from .. import i18n
from .icons import svg_icon
from .menu_item import add_centered_menu_action
from .theme import LIGHT_MENU_QSS

MENU_MIN_WIDTH = 168


def new_menu(parent=None) -> QMenu:
    menu = QMenu(parent)
    menu.setStyleSheet(LIGHT_MENU_QSS)
    menu.setMinimumWidth(MENU_MIN_WIDTH)
    return menu


def run_menu(menu: QMenu, global_pos) -> None:
    """메뉴를 띄우고 닫힐 때까지 기다린다(시험에서는 이 함수를 바꿔 끼운다)."""
    menu.exec(global_pos)


def _state(widget) -> dict:
    """입력칸의 지금 상태: 무엇을 할 수 있는가."""
    if isinstance(widget, QLineEdit):
        hidden = widget.echoMode() != QLineEdit.Normal
        read_only = widget.isReadOnly()
        selected = widget.hasSelectedText()
        return {
            "undo": widget.isUndoAvailable() and not read_only,
            "redo": widget.isRedoAvailable() and not read_only,
            "cut": selected and not read_only and not hidden,
            "copy": selected and not hidden,
            "paste": not read_only and bool(QApplication.clipboard().text()),
            "select_all": bool(widget.text()),
            "editable": not read_only,
        }
    doc = widget.document()
    read_only = widget.isReadOnly()
    selected = widget.textCursor().hasSelection()
    return {
        "undo": doc.isUndoAvailable() and not read_only,
        "redo": doc.isRedoAvailable() and not read_only,
        "cut": selected and not read_only,
        "copy": selected,
        "paste": not read_only and widget.canPaste(),
        "select_all": not doc.isEmpty(),
        "editable": not read_only,
    }


def add_edit_actions(menu: QMenu, widget) -> None:
    """실행 취소부터 모두 선택까지를 메뉴에 넣는다(``QLineEdit``·``QTextEdit``).

    읽기 전용 칸에는 고칠 수 없는 항목을 넣지 않는다."""
    s = _state(widget)
    ink = "#3A3A3A"
    off = "#B0B0B0"

    def icon(name: str, on: bool):
        return svg_icon(name, color=ink if on else off)

    if s["editable"]:
        add_centered_menu_action(menu, icon("undo", s["undo"]),
                                 i18n.t("edit.undo"), widget.undo, enabled=s["undo"])
        add_centered_menu_action(menu, icon("redo", s["redo"]),
                                 i18n.t("edit.redo"), widget.redo, enabled=s["redo"])
        menu.addSeparator()
        add_centered_menu_action(menu, icon("cut", s["cut"]),
                                 i18n.t("edit.cut"), widget.cut, enabled=s["cut"])
    add_centered_menu_action(menu, icon("copy", s["copy"]),
                             i18n.t("edit.copy"), widget.copy, enabled=s["copy"])
    if s["editable"]:
        add_centered_menu_action(menu, icon("paste", s["paste"]),
                                 i18n.t("edit.paste"), widget.paste, enabled=s["paste"])
    menu.addSeparator()
    add_centered_menu_action(menu, icon("select_all", s["select_all"]),
                             i18n.t("edit.select_all"), widget.selectAll,
                             enabled=s["select_all"])


def build_edit_menu(widget) -> QMenu:
    menu = new_menu(widget)
    add_edit_actions(menu, widget)
    return menu


class EditMenuFilter(QObject):
    """한 줄 입력칸(검색, 닉네임, 주소…)의 우클릭 메뉴를 앱의 메뉴로 바꾼다.

    숫자 칸과 고르는 칸 안의 입력칸은 그 위젯이 스스로 메뉴를 다루므로 건드리지
    않는다. 메모 본문(``QTextEdit``)도 스티키 메모가 직접 다룬다."""

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if event.type() != QEvent.ContextMenu or not isinstance(obj, QLineEdit):
            return False
        if isinstance(obj.parent(), (QAbstractSpinBox, QComboBox)):
            return False
        menu = build_edit_menu(obj)
        run_menu(menu, event.globalPos())
        menu.deleteLater()
        return True


def plain_text_menu(widget: QTextEdit) -> QMenu:
    """메모가 아닌 여러 줄 칸(이력 보기 등)에 쓰는 같은 메뉴."""
    return build_edit_menu(widget)
