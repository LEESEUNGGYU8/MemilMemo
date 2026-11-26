"""표 넣기: 크기 고르기, 저장과 다시 불러오기, Tab 으로 칸 옮기기, 줄과 칸 더하고 지우기."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent, QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu  # noqa: E402

from app import html_guard, i18n  # noqa: E402
from app.settings import AppSettings  # noqa: E402
from app.ui import edit_menu, theme  # noqa: E402
from app.ui.rich_text import MARKER_PROP  # noqa: E402
from app.ui.sticky_note import (  # noqa: E402
    TABLE_MAX_ROWS, StickyNoteWidget, TableSizePicker)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    i18n.set_language("ko")
    theme.apply_theme(app, "")
    yield app


def _note(qapp, content: str = "", note_id: str = "t1"):
    w = StickyNoteWidget({"id": note_id, "version": 1, "color": "#FFE066",
                          "width": 320, "height": 300, "content": content},
                         "지우", settings=AppSettings())
    w.apply_local_state(200, 160)
    w.show()
    qapp.processEvents()
    return w


def _tables(w):
    return [f for f in w._text.document().rootFrame().childFrames()
            if f.__class__.__name__ == "QTextTable"]


def _type_in_cells(w, table, texts):
    for (r, c), text in texts.items():
        cur = table.cellAt(r, c).firstCursorPosition()
        cur.insertText(text)


def test_insert_puts_an_empty_table_and_the_cursor_in_its_first_cell(qapp):
    w = _note(qapp)
    w.enter_edit_mode()
    w.insert_table(3, 4)
    tables = _tables(w)
    assert len(tables) == 1 and (tables[0].rows(), tables[0].columns()) == (3, 4)
    cell = tables[0].cellAt(w._text.textCursor())
    assert (cell.row(), cell.column()) == (0, 0)
    w.close()


def test_a_table_on_a_written_line_goes_below_it(qapp):
    w = _note(qapp)
    w.enter_edit_mode()
    w._text.setPlainText("11월 소모품")
    w._text.moveCursor(QTextCursor.End)
    w.insert_table(2, 2)
    assert w._text.document().begin().text() == "11월 소모품"
    assert len(_tables(w)) == 1
    w.close()


def test_a_list_marker_does_not_follow_into_the_cells(qapp):
    w = _note(qapp)
    w.enter_edit_mode()
    w._text.setPlainText("")
    w._apply_checkbox_marker()
    w.insert_table(2, 2)
    table = _tables(w)[0]
    for r in range(2):
        for c in range(2):
            block = table.cellAt(r, c).firstCursorPosition().block()
            assert block.blockFormat().intProperty(MARKER_PROP) == 0
    w.close()


def test_a_table_of_plain_words_is_saved_and_comes_back(qapp):
    w = _note(qapp)
    w.enter_edit_mode()
    w.insert_table(2, 3)
    _type_in_cells(w, _tables(w)[0], {(0, 0): "품목", (0, 1): "수량", (1, 0): "토너",
                                       (1, 1): "2", (1, 2): "168,000"})
    saved = w._current_content()
    assert "<table" in saved                    # 글자만 있어도 평문으로 저장하지 않는다
    assert "<table" in html_guard.clean(saved)  # 공유 PC 와 옛 버전의 거르기에서도 남는다
    again = _note(qapp, saved, note_id="t2")
    table = _tables(again)[0]
    assert (table.rows(), table.columns()) == (2, 3)
    cell = table.cellAt(1, 2)
    cur = cell.firstCursorPosition()
    cur.setPosition(cell.lastCursorPosition().position(), QTextCursor.KeepAnchor)
    assert cur.selectedText() == "168,000"
    w.close()
    again.close()


def _key(w, key, mods=Qt.NoModifier):
    return w._handle_list_key(QKeyEvent(QKeyEvent.KeyPress, key, mods))


def test_tab_moves_between_cells_and_adds_a_row_at_the_end(qapp):
    w = _note(qapp)
    w.enter_edit_mode()
    w.insert_table(2, 2)
    table = _tables(w)[0]

    def at():
        c = table.cellAt(w._text.textCursor())
        return c.row(), c.column()

    assert _key(w, Qt.Key_Tab) and at() == (0, 1)
    assert _key(w, Qt.Key_Tab) and at() == (1, 0)
    assert _key(w, Qt.Key_Backtab, Qt.ShiftModifier) and at() == (0, 1)
    w._text.setTextCursor(table.cellAt(1, 1).firstCursorPosition())
    assert _key(w, Qt.Key_Tab) and at() == (2, 0) and table.rows() == 3
    w.close()


def _menu_texts(menu: QMenu) -> dict:
    return {a.text(): a for a in menu.actions() if a.text()}


def test_right_click_in_a_cell_adds_and_removes_rows_and_columns(qapp):
    w = _note(qapp)
    w.enter_edit_mode()
    w.insert_table(2, 2)
    table = _tables(w)[0]
    rect = w._text.cursorRect(table.cellAt(1, 1).firstCursorPosition())
    pos = rect.center()
    menu = edit_menu.new_menu(w)
    w._add_table_actions(menu, pos)
    sub = _menu_texts(w._table_menu)
    sub[i18n.t("sticky.table_row_below")].trigger()
    sub[i18n.t("sticky.table_col_left")].trigger()
    assert (table.rows(), table.columns()) == (3, 3)
    sub[i18n.t("sticky.table_row_delete")].trigger()
    assert table.rows() == 2
    sub[i18n.t("sticky.table_delete")].trigger()
    assert _tables(w) == []
    # 표 밖에서는 메뉴에 표 항목이 없다.
    w._table_menu = None
    menu2 = edit_menu.new_menu(w)
    w._add_table_actions(menu2, QPoint(5, 5))
    assert w._table_menu is None
    w.close()


def test_the_picker_chooses_by_mouse_and_keys(qapp):
    picker = TableSizePicker()
    got: list = []
    picker.picked.connect(lambda r, c: got.append((r, c)))
    assert picker.size_text() == "1줄 1칸"
    picker.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Right, Qt.NoModifier))
    picker.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Down, Qt.NoModifier))
    assert picker.size_text() == "2줄 2칸"
    picker.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))
    assert got == [(2, 2)]
    assert picker._cell_at(QPoint(8 + 2 * 21 + 3, 8 + 3)) == (1, 3)
    assert picker._cell_at(QPoint(2, 2)) is None
    picker.deleteLater()


def test_the_attach_menu_picks_a_size_then_inserts(qapp, monkeypatch):
    w = _note(qapp)
    w.enter_edit_mode()

    def run(menu, pos):
        w._table_picker.picked.emit(3, 2)

    monkeypatch.setattr(edit_menu, "run_menu", run)
    w._show_table_picker()
    tables = _tables(w)
    assert len(tables) == 1 and (tables[0].rows(), tables[0].columns()) == (3, 2)
    w.insert_table(TABLE_MAX_ROWS + 9, 1)         # 한도를 넘지 않는다
    assert max(t.rows() for t in _tables(w)) == TABLE_MAX_ROWS
    w.close()
