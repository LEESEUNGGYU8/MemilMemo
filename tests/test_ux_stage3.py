"""UI/UX 개선 3단계(키보드와 접근성) 테스트(offscreen)."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu  # noqa: E402

from app import i18n  # noqa: E402
from app.ui import theme  # noqa: E402
from app.ui.menu_item import add_centered_menu_action  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    i18n.set_language("ko")
    theme.apply_theme(app, "")
    yield app


def _menu(calls: list[str]) -> tuple[QMenu, list]:
    menu = QMenu()
    actions = [
        add_centered_menu_action(menu, None, "묶음", header=True),
        add_centered_menu_action(menu, None, "첫째", lambda: calls.append("첫째")),
        add_centered_menu_action(menu, None, "둘째", lambda: calls.append("둘째")),
        add_centered_menu_action(menu, None, "못 누름", lambda: calls.append("x"),
                                 enabled=False),
    ]
    return menu, actions


def test_custom_menu_works_with_arrow_keys_and_enter(qapp):
    calls: list[str] = []
    menu, actions = _menu(calls)
    menu.popup(QPoint(40, 40))
    qapp.processEvents()

    QTest.keyClick(menu, Qt.Key_Down)
    assert menu.activeAction() is actions[1]          # 이름표는 건너뛴다
    assert actions[1].defaultWidget()._hover           # 지금 항목이 강조된다
    QTest.keyClick(menu, Qt.Key_Down)
    assert menu.activeAction() is actions[2]
    assert actions[2].defaultWidget()._hover
    assert not actions[1].defaultWidget()._hover

    QTest.keyClick(menu, Qt.Key_Return)
    qapp.processEvents()
    assert calls == ["둘째"]                           # 예전에는 아무 일 없이 닫혔다
    assert not menu.isVisible()


def test_custom_menu_click_runs_the_handler_once(qapp):
    calls: list[str] = []
    menu, actions = _menu(calls)
    menu.popup(QPoint(40, 40))
    qapp.processEvents()
    item = actions[1].defaultWidget()
    QTest.mouseClick(item, Qt.LeftButton, Qt.NoModifier, item.rect().center())
    qapp.processEvents()
    assert calls == ["첫째"]
    assert not menu.isVisible()


def test_menu_items_have_names_for_screen_readers(qapp):
    menu, actions = _menu([])
    assert actions[1].text() == "첫째"
    assert actions[1].defaultWidget().accessibleName() == "첫째"


# ── 카드와 메모 창의 글쇠 조작 ──────────────────────────────────────────

def _card(qapp):
    from app.ui.note_card import NoteCard
    card = NoteCard({"id": "c1", "version": 1, "color": "#FFE066",
                     "content": "주간 회의 메모", "updated_at": 0}, visible=True)
    card.resize(320, 90)
    card.show()
    qapp.processEvents()
    return card


def test_card_can_be_opened_and_deleted_from_the_keyboard(qapp):
    card = _card(qapp)
    opened: list[str] = []
    deleted: list[str] = []
    card.clicked.connect(opened.append)
    card.deleteRequested.connect(deleted.append)
    assert card.focusPolicy() == Qt.StrongFocus          # Tab 순서에 들어 있다
    assert card.accessibleName() == "주간 회의 메모"

    QTest.keyClick(card, Qt.Key_Return)
    assert opened == ["c1"]
    QTest.keyClick(card, Qt.Key_Delete)
    assert deleted == ["c1"]
    card.deleteLater()


def test_card_focus_ring_is_for_keyboard_focus_only(qapp):
    card = _card(qapp)
    card.setFocus(Qt.MouseFocusReason)
    qapp.processEvents()
    assert not card._kb_focus
    card.clearFocus()
    card.setFocus(Qt.TabFocusReason)
    qapp.processEvents()
    if card.hasFocus():                                   # offscreen 에서도 초점이 오면
        assert card._kb_focus
    card.deleteLater()


def test_note_can_be_edited_and_left_from_the_keyboard(qapp):
    from app.settings import AppSettings
    from app.ui.sticky_note import StickyNoteWidget
    w = StickyNoteWidget({"id": "s1", "version": 1, "color": "#FFE066",
                          "width": 280, "height": 260, "content": "메모"},
                         "지우", settings=AppSettings())
    w.apply_local_state(200, 160)
    w.show()
    qapp.processEvents()
    asked: list[str] = []
    released: list[str] = []
    w.requestLock.connect(asked.append)
    w.releaseLock.connect(released.append)

    QTest.keyClick(w, Qt.Key_F2)
    assert asked == ["s1"]                                # F2 로 편집을 청한다
    w.enter_edit_mode()
    QTest.keyClick(w, Qt.Key_Escape)
    assert not w._edit_mode and released == ["s1"]        # Esc 로 마친다
    w._teardown = True
    w.close()


def test_icon_buttons_get_names_and_colours_get_words(qapp):
    from PySide6.QtWidgets import QToolButton
    btn = QToolButton()
    btn.setToolTip("참가자 보기")
    btn.show()
    qapp.processEvents()
    assert btn.accessibleName() == "참가자 보기"         # 말풍선 글자가 이름이 된다
    btn.deleteLater()
    assert theme.color_name("#FFE066") == "노랑"
    assert theme.color_name("#3a3a3a") == "검정"
    assert theme.color_name("#123456") == "#123456"       # 모르는 색은 그대로


# ── 읽기 상태에서 체크박스 한 번 누르기 ─────────────────────────────────

def _todo_note(qapp):
    from PySide6.QtGui import QTextCursor
    from app.settings import AppSettings
    from app.ui import rich_text
    from app.ui.sticky_note import StickyNoteWidget
    w = StickyNoteWidget({"id": "t1", "version": 1, "color": "#FFE066",
                          "width": 300, "height": 260, "content": ""},
                         "지우", settings=AppSettings())
    w.apply_local_state(200, 160)
    w.show()
    qapp.processEvents()
    w.enter_edit_mode()
    w._text.setPlainText("우유 사기\n보고서 내기")
    doc = w._text.document()
    for i in range(2):
        w._set_block_marker(doc.findBlockByNumber(i), rich_text.MARKER_CHECK0)
    w._commit_and_release()
    assert not w._edit_mode
    return w, rich_text, QTextCursor


def _box_pos(w, QTextCursor, line: int):
    block = w._text.document().findBlockByNumber(line)
    rect = w._text.cursorRect(QTextCursor(block))
    return QPoint(rect.left() - 14, rect.center().y())


def test_checkbox_toggles_with_one_click_in_view_mode(qapp):
    w, rich_text, QTextCursor = _todo_note(qapp)
    asked: list[str] = []
    sent: list[str] = []
    released: list[str] = []
    w.requestLock.connect(asked.append)
    w.commitEdit.connect(lambda nid, content, *rest: sent.append(content))
    w.releaseLock.connect(released.append)

    QTest.mousePress(w._text.viewport(), Qt.LeftButton, Qt.NoModifier,
                     _box_pos(w, QTextCursor, 1))
    assert asked == ["t1"] and w._pending_check == 1   # 잠금을 청하고 기다린다
    w.enter_edit_mode()                                 # 호스트가 잠금을 줬다
    doc = w._text.document()
    assert w._marker_code(doc.findBlockByNumber(1)) == rich_text.MARKER_CHECK1
    assert w._marker_code(doc.findBlockByNumber(0)) == rich_text.MARKER_CHECK0
    assert len(sent) == 1 and released == ["t1"]        # 저장하고 곧바로 잠금을 푼다
    assert not w._edit_mode                             # 편집 상태로 남지 않는다
    w._teardown = True
    w.close()


def test_click_on_text_still_enters_edit_mode(qapp):
    w, rich_text, QTextCursor = _todo_note(qapp)
    block = w._text.document().findBlockByNumber(0)
    rect = w._text.cursorRect(QTextCursor(block))
    QTest.mousePress(w._text.viewport(), Qt.LeftButton, Qt.NoModifier,
                     QPoint(rect.left() + 20, rect.center().y()))
    assert w._pending_check is None
    w.enter_edit_mode()
    assert w._edit_mode                                 # 글자를 누르면 평소처럼 편집
    w._teardown = True
    w.close()


def test_refused_checkbox_click_changes_nothing(qapp):
    w, rich_text, QTextCursor = _todo_note(qapp)
    QTest.mousePress(w._text.viewport(), Qt.LeftButton, Qt.NoModifier,
                     _box_pos(w, QTextCursor, 0))
    w.show_lock_denied("서연")                          # 다른 사람이 편집 중이다
    assert w._pending_check is None
    assert w._marker_code(w._text.document().findBlockByNumber(0)) == \
        rich_text.MARKER_CHECK0
    w._teardown = True
    w.close()
