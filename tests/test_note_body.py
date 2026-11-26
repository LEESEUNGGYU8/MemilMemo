"""메모 본문의 목록: 단계, 번호, 간격, 끝낸 할 일, 치면서 바뀌는 서식, 주소를 링크로.

저장 형식이 옛 버전과 섞여도 깨지지 않는지는 여기서 형식만 본다(단계는 블록의
들여쓰기 값, 번호는 이름 붙은 앵커). 옛 버전 코드로 읽고 고치게 한 시험은
2026-11-23 에 따로 했다."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData, QPoint, Qt  # noqa: E402
from PySide6.QtGui import QContextMenuEvent, QTextCursor, QTextDocument  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app import i18n  # noqa: E402
from app.settings import AppSettings  # noqa: E402
from app.ui import edit_menu, rich_text, theme  # noqa: E402
from app.ui.folder_icon import FolderIconWidget  # noqa: E402
from app.ui.sticky_note import StickyNoteWidget  # noqa: E402

R = rich_text


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    i18n.set_language("ko")
    theme.apply_theme(app, "")
    yield app


def _note(qapp, content: str = "", *, edit: bool = True, color: str = "#FFE066"):
    w = StickyNoteWidget({"id": "b1", "version": 1, "color": color,
                          "width": 300, "height": 320, "content": content},
                         "지우", settings=AppSettings())
    w.apply_local_state(200, 160)
    w.show()
    qapp.processEvents()
    if edit:
        w.enter_edit_mode()
        assert w._edit_mode
    return w


def _close(w) -> None:
    w._teardown = True
    w.close()


def _block(w, n: int):
    return w._text.document().findBlockByNumber(n)


def _at(w, n: int, *, end: bool = True) -> None:
    block = _block(w, n)
    cur = QTextCursor(block)
    cur.setPosition(block.position() + (block.length() - 1 if end else 0))
    w._text.setTextCursor(cur)


def _lines(w, text: str) -> None:
    w._text.setPlainText(text)
    w._text.document().clearUndoRedoStacks()


def _shape(w) -> list[tuple[int, int, str]]:
    """줄마다 (목록 종류, 단계, 글자)."""
    out = []
    block = w._text.document().begin()
    while block.isValid():
        out.append((w._marker_code(block), w._text.block_level(block), block.text()))
        block = block.next()
    return out


def _saved_blocks(html: str) -> list[dict]:
    """저장한 내용을 Qt 가 읽은 그대로(옛 버전이 보는 모양)."""
    doc = QTextDocument()
    doc.setHtml(html)
    out = []
    block = doc.begin()
    while block.isValid():
        bf = block.blockFormat()
        out.append({"text": block.text(), "indent": bf.indent(),
                    "left": bf.leftMargin(), "top": bf.topMargin()})
        block = block.next()
    return out


# ── 단계 ────────────────────────────────────────────────────────────────

def test_list_shortcut_makes_a_square_list(qapp):
    w = _note(qapp)
    _lines(w, "견적")
    _at(w, 0)
    w._toggle_list()
    assert _shape(w) == [(R.MARKER_SQUARE, 0, "견적")]
    _close(w)


def test_tab_moves_a_list_line_in_and_shift_tab_out(qapp):
    w = _note(qapp)
    _lines(w, "견적\n회신")
    for n in (0, 1):
        w._set_block_marker(_block(w, n), R.MARKER_SQUARE)
    flat = _block(w, 1).blockFormat().leftMargin()
    _at(w, 1)
    QTest.keyClick(w._text, Qt.Key_Tab)
    assert _shape(w)[1][:2] == (R.MARKER_SQUARE, 1)
    em = w._text.block_em(_block(w, 1))
    # 한 단계는 글자 반 칸이다.
    assert _block(w, 1).blockFormat().leftMargin() == pytest.approx(
        flat + R.LEVEL_STEP * em, abs=0.6)
    for _ in range(6):
        QTest.keyClick(w._text, Qt.Key_Tab)
    assert w._text.block_level(_block(w, 1)) == R.MAX_LEVEL     # 넷째 단계까지
    QTest.keyClick(w._text, Qt.Key_Backtab, Qt.ShiftModifier)
    assert w._text.block_level(_block(w, 1)) == R.MAX_LEVEL - 1
    assert "\t" not in w._text.toPlainText()
    _close(w)


def test_tab_on_a_plain_line_still_types_a_tab(qapp):
    w = _note(qapp)
    _lines(w, "글")
    _at(w, 0)
    QTest.keyClick(w._text, Qt.Key_Tab)
    assert w._text.toPlainText() == "글\t"
    _close(w)


def test_tab_moves_every_selected_list_line(qapp):
    w = _note(qapp)
    _lines(w, "하나\n둘\n셋")
    for n in range(3):
        w._set_block_marker(_block(w, n), R.MARKER_CHECK0)
    cur = QTextCursor(_block(w, 0))
    cur.setPosition(_block(w, 2).position() + 1, QTextCursor.KeepAnchor)
    w._text.setTextCursor(cur)
    QTest.keyClick(w._text, Qt.Key_Tab)
    assert [level for _c, level, _t in _shape(w)] == [1, 1, 1]
    _close(w)


def test_square_list_changes_symbol_by_level_and_others_keep_theirs():
    assert [R.marker_shape(R.MARKER_SQUARE, n) for n in range(5)] == [
        R.SHAPE_SQUARE, R.SHAPE_RING, R.SHAPE_DASH, R.SHAPE_MIDDOT, R.SHAPE_MIDDOT]
    for code, shape in ((R.MARKER_CIRCLE, R.SHAPE_DOT), (R.MARKER_ARROW, R.SHAPE_ARROW),
                        (R.MARKER_INFO, R.SHAPE_INFO)):
        assert {R.marker_shape(code, n) for n in range(4)} == {shape}


def test_enter_on_an_empty_sub_line_moves_out_then_ends_the_list(qapp):
    w = _note(qapp)
    _lines(w, "견적\n")
    w._set_block_marker(_block(w, 0), R.MARKER_SQUARE)
    w._set_block_marker(_block(w, 1), R.MARKER_SQUARE, 2)
    _at(w, 1)
    QTest.keyClick(w._text, Qt.Key_Return)
    assert _shape(w)[1][:2] == (R.MARKER_SQUARE, 1)
    QTest.keyClick(w._text, Qt.Key_Return)
    assert _shape(w)[1][:2] == (R.MARKER_SQUARE, 0)
    QTest.keyClick(w._text, Qt.Key_Return)
    assert _shape(w)[1][:2] == (R.MARKER_NONE, 0)
    assert w._text.document().blockCount() == 2
    _close(w)


def test_enter_keeps_the_level_for_the_next_line(qapp):
    w = _note(qapp)
    _lines(w, "견적\n회신")
    w._set_block_marker(_block(w, 0), R.MARKER_SQUARE)
    w._set_block_marker(_block(w, 1), R.MARKER_CHECK0, 1)
    _at(w, 1)
    QTest.keyClick(w._text, Qt.Key_Return)
    QTest.keyClicks(w._text, "new")
    qapp.processEvents()
    assert _shape(w)[2] == (R.MARKER_CHECK0, 1, "new")
    _close(w)


def test_backspace_at_line_start_moves_out_then_removes_the_marker(qapp):
    w = _note(qapp)
    _lines(w, "견적")
    w._set_block_marker(_block(w, 0), R.MARKER_CIRCLE, 1)
    _at(w, 0, end=False)
    QTest.keyClick(w._text, Qt.Key_Backspace)
    assert _shape(w) == [(R.MARKER_CIRCLE, 0, "견적")]
    QTest.keyClick(w._text, Qt.Key_Backspace)
    assert _shape(w) == [(R.MARKER_NONE, 0, "견적")]
    assert _block(w, 0).blockFormat().leftMargin() == 0
    _close(w)


def test_checked_line_sent_to_the_bottom_keeps_its_level(qapp):
    w = _note(qapp)
    w._settings.move_checked_to_bottom = True
    _lines(w, "하나\n둘\n셋")
    w._set_block_marker(_block(w, 0), R.MARKER_CHECK0)
    w._set_block_marker(_block(w, 1), R.MARKER_CHECK0, 1)
    w._set_block_marker(_block(w, 2), R.MARKER_CHECK0)
    w._toggle_checkbox_block(_block(w, 1))
    assert _shape(w)[-1] == (R.MARKER_CHECK1, 1, "둘")
    _close(w)


# ── 저장과 불러오기 ─────────────────────────────────────────────────────

def test_levels_are_saved_as_block_indent_with_no_margins(qapp):
    w = _note(qapp)
    _lines(w, "글\n견적\n회신\n1차\n끝")
    w._set_block_marker(_block(w, 1), R.MARKER_SQUARE)
    w._set_block_marker(_block(w, 2), R.MARKER_SQUARE, 1)
    w._set_block_marker(_block(w, 3), R.MARKER_CHECK0, 2)
    html = w._current_content()
    saved = _saved_blocks(html)
    assert [b["indent"] for b in saved] == [0, 0, 1, 2, 0]
    # 여백과 서식 사이의 간격은 화면에만 쓴다. 저장한 내용에는 없다.
    assert all(b["left"] == 0 and b["top"] == 0 for b in saved)
    # 단계가 달라도 네모 목록은 네모 그림으로 저장한다(옛 버전이 아는 그림).
    assert html.count(R.bullet_url_for("square")) == 2
    _close(w)

    again = _note(qapp, html, edit=False)
    assert _shape(again) == [
        (R.MARKER_NONE, 0, "글"), (R.MARKER_SQUARE, 0, "견적"),
        (R.MARKER_SQUARE, 1, "회신"), (R.MARKER_CHECK0, 2, "1차"),
        (R.MARKER_NONE, 0, "끝")]
    # Qt 의 들여쓰기(한 단계 40픽셀)는 쓰지 않는다.
    assert all(_block(again, n).blockFormat().indent() == 0 for n in range(5))
    assert again._current_content() == html                    # 다시 저장해도 같다
    _close(again)


def test_notes_saved_by_older_versions_load_as_before(qapp):
    old = (f'<p><b>제목</b></p>'
           f'<p><img src="{R.bullet_url_for("circle")}" />  항목</p>'
           f'<p><img src="{R.checkbox_url(True)}" />  끝낸 일</p>'
           f'<p><img src="{R.checkbox_url(False)}" /> 남은 일</p>')
    w = _note(qapp, old, edit=False)
    assert _shape(w) == [(R.MARKER_NONE, 0, "제목"), (R.MARKER_CIRCLE, 0, "항목"),
                         (R.MARKER_CHECK1, 0, "끝낸 일"), (R.MARKER_CHECK0, 0, "남은 일")]
    _close(w)


def test_indent_left_on_a_plain_line_by_an_older_version_is_dropped(qapp):
    # 옛 버전에서 딸린 줄의 목록을 풀면 들여쓰기 값만 남는다.
    html = ('<p style="-qt-block-indent:2;">글</p>'
            f'<p style="-qt-block-indent:1;"><img src="{R.bullet_url_for("square")}" />'
            '  항목</p>')
    w = _note(qapp, html, edit=False)
    assert _shape(w) == [(R.MARKER_NONE, 0, "글"), (R.MARKER_SQUARE, 1, "항목")]
    assert [b["indent"] for b in _saved_blocks(w._current_content())] == [0, 1]
    _close(w)


def test_a_plain_note_is_still_saved_as_plain_text(qapp):
    w = _note(qapp, "첫 줄\n둘째 줄")
    assert w._current_content() == "첫 줄\n둘째 줄"
    _close(w)


def test_leaving_edit_without_changes_sends_nothing(qapp):
    w = _note(qapp)
    _lines(w, "글\n하나\n둘\n셋")
    w._set_block_marker(_block(w, 1), R.MARKER_NUMBER)
    w._set_block_marker(_block(w, 2), R.MARKER_SQUARE, 1)
    w._set_block_marker(_block(w, 3), R.MARKER_CHECK1)
    html = w._current_content()
    _close(w)

    again = _note(qapp, html)
    sent: list[str] = []
    again.commitEdit.connect(lambda _id, content, *rest: sent.append(content))
    qapp.processEvents()
    again._commit_and_release()
    assert sent == []                   # 읽고 닫기만 했는데 내용이 바뀐 것으로 보면 안 된다
    _close(again)


# ── 번호 목록 ───────────────────────────────────────────────────────────

def _numbers(w) -> list[int | None]:
    numbers = w._text.list_numbers()
    return [numbers.get(n) for n in range(w._text.document().blockCount())]


def test_numbers_count_per_level_and_restart_after_a_break(qapp):
    w = _note(qapp)
    _lines(w, "하나\n둘\n딸린 글머리\n딸린 번호 1\n딸린 번호 2\n셋\n글\n다시 하나\n네모\n또 하나")
    for n, (code, level) in enumerate([
            (R.MARKER_NUMBER, 0), (R.MARKER_NUMBER, 0), (R.MARKER_SQUARE, 1),
            (R.MARKER_NUMBER, 1), (R.MARKER_NUMBER, 1), (R.MARKER_NUMBER, 0),
            (R.MARKER_NONE, 0), (R.MARKER_NUMBER, 0), (R.MARKER_SQUARE, 0),
            (R.MARKER_NUMBER, 0)]):
        if code != R.MARKER_NONE:
            w._set_block_marker(_block(w, n), code, level)
    # 딸린 줄은 위 목록을 끊지 않는다. 글 줄이나 같은 단계의 다른 기호가 끼면 다시 1.
    assert _numbers(w) == [1, 2, None, 1, 2, 3, None, 1, None, 1]
    _close(w)


def test_numbers_are_saved_as_text_with_a_named_anchor(qapp):
    w = _note(qapp)
    _lines(w, "받기\n등록\n보내기")
    w._set_block_marker(_block(w, 0), R.MARKER_NUMBER)
    w._set_block_marker(_block(w, 1), R.MARKER_NUMBER, 1)
    w._set_block_marker(_block(w, 2), R.MARKER_NUMBER)
    html = w._current_content()
    # 옛 버전에는 '1. 받기' 라는 글자로 보인다.
    assert [b["text"] for b in _saved_blocks(html)] == ["1. 받기", "1. 등록", "2. 보내기"]
    assert [b["indent"] for b in _saved_blocks(html)] == [0, 1, 0]
    assert html.count(f'name="{R.NUMBER_ANCHOR}"') == 3
    _close(w)

    again = _note(qapp, html, edit=False)
    assert _shape(again) == [(R.MARKER_NUMBER, 0, "받기"), (R.MARKER_NUMBER, 1, "등록"),
                             (R.MARKER_NUMBER, 0, "보내기")]
    assert _numbers(again) == [1, 1, 2]
    assert again._current_content() == html
    _close(again)


def test_number_text_typed_by_hand_is_not_a_numbered_list(qapp):
    w = _note(qapp, "<p><b>1. 굵게 친 글</b></p><p>2. 그냥 친 글</p>", edit=False)
    assert [code for code, _l, _t in _shape(w)] == [R.MARKER_NONE, R.MARKER_NONE]
    assert _shape(w)[0][2] == "1. 굵게 친 글"
    _close(w)


def test_number_anchor_with_the_space_inside_is_still_a_number(qapp):
    # 옛 버전이 번호 뒤의 빈칸까지 같은 앵커로 묶어 저장한 꼴.
    html = f'<p><a name="{R.NUMBER_ANCHOR}">3. </a>보내기</p>'
    w = _note(qapp, html, edit=False)
    assert _shape(w) == [(R.MARKER_NUMBER, 0, "보내기")]
    _close(w)


def test_a_long_numbered_list_gets_a_wider_column_for_every_line(qapp):
    w = _note(qapp)
    _lines(w, "\n".join(f"줄 {n}" for n in range(1, 11)))
    for n in range(10):
        w._text.set_block_marker(_block(w, n), R.MARKER_NUMBER)
    w._text.restyle_lists()
    margins = {round(_block(w, n).blockFormat().leftMargin()) for n in range(10)}
    assert len(margins) == 1                                   # 글자가 한 줄로 맞는다
    assert margins.pop() > R.marker_column(w._text.base_em())  # '10.' 이 들어갈 폭
    _close(w)


# ── 치면서 바뀌는 서식 ──────────────────────────────────────────────────

def _type(w, text: str) -> None:
    """글자를 친다. QTest 는 한글을 키로 보내지 못하므로(프로세스가 죽는다) 한글은
    입력기가 넣듯 커서 자리에 바로 넣는다."""
    for ch in text:
        if ch == " ":
            QTest.keyClick(w._text, Qt.Key_Space)
        elif ch.isascii():
            QTest.keyClicks(w._text, ch)
        else:
            w._text.textCursor().insertText(ch)


def test_dash_and_space_starts_a_square_list_and_backspace_restores_it(qapp):
    w = _note(qapp)
    _type(w, "- ")
    assert _shape(w) == [(R.MARKER_SQUARE, 0, "")]
    QTest.keyClick(w._text, Qt.Key_Backspace)
    assert _shape(w) == [(R.MARKER_NONE, 0, "- ")]     # 쳤던 글자로 돌아간다
    _close(w)


def test_brackets_and_space_start_a_todo(qapp):
    for typed in ("[] ", "[ ] "):
        w = _note(qapp)
        _type(w, typed)
        _type(w, "접수")
        assert _shape(w) == [(R.MARKER_CHECK0, 0, "접수")]
        _close(w)


def test_number_and_space_starts_or_continues_a_numbered_list(qapp):
    w = _note(qapp)
    _type(w, "1. ")
    _type(w, "받기")
    QTest.keyClick(w._text, Qt.Key_Return)     # 다음 줄은 번호가 이어진다
    _type(w, "등록")
    assert _shape(w) == [(R.MARKER_NUMBER, 0, "받기"), (R.MARKER_NUMBER, 0, "등록")]
    assert _numbers(w) == [1, 2]
    _close(w)


def test_a_number_that_would_not_be_next_stays_as_typed(qapp):
    w = _note(qapp)
    _type(w, "5. ")
    assert _shape(w) == [(R.MARKER_NONE, 0, "5. ")]
    _close(w)

    w = _note(qapp)
    _lines(w, "받기\n")
    w._set_block_marker(_block(w, 0), R.MARKER_NUMBER)
    _at(w, 1)
    _type(w, "2. ")                             # 앞 번호의 다음 수
    assert _shape(w)[1][:2] == (R.MARKER_NUMBER, 0)
    _close(w)


def test_typing_in_the_middle_of_a_line_changes_nothing(qapp):
    w = _note(qapp)
    _type(w, "가 - ")
    assert _shape(w) == [(R.MARKER_NONE, 0, "가 - ")]
    _close(w)


def test_another_key_after_the_change_forgets_the_typed_text(qapp):
    w = _note(qapp)
    _type(w, "- ")
    _type(w, "가")
    cur = w._text.textCursor()
    cur.setPosition(_block(w, 0).position())
    w._text.setTextCursor(cur)
    QTest.keyClick(w._text, Qt.Key_Backspace)
    assert _shape(w) == [(R.MARKER_NONE, 0, "가")]      # 목록만 풀린다
    _close(w)


# ── 간격과 끝낸 할 일 ───────────────────────────────────────────────────

def _tops(w) -> list[int]:
    doc = w._text.document()
    return [round(doc.findBlockByNumber(n).blockFormat().topMargin())
            for n in range(doc.blockCount())]


def test_gaps_follow_the_kind_of_line(qapp):
    w = _note(qapp)
    _lines(w, "글\n하나\n딸린 줄\n둘\n글\n글")
    w._set_block_marker(_block(w, 1), R.MARKER_SQUARE)
    w._set_block_marker(_block(w, 2), R.MARKER_SQUARE, 1)
    w._set_block_marker(_block(w, 3), R.MARKER_SQUARE)
    em = w._text.base_em()
    assert _tops(w) == [0, round(R.GAP_LIST_START * em), round(R.GAP_ITEM * em),
                        round(R.GAP_GROUP * em), round(R.GAP_LIST_END * em), 0]
    _close(w)


def test_no_gap_is_added_next_to_a_blank_line(qapp):
    w = _note(qapp)
    _lines(w, "글\n\n하나\n\n글")
    w._set_block_marker(_block(w, 2), R.MARKER_SQUARE)
    assert _tops(w) == [0, 0, 0, 0, 0]
    _close(w)


def test_gap_is_removed_again_when_the_list_goes_away(qapp):
    w = _note(qapp)
    _lines(w, "글\n하나\n글")
    w._set_block_marker(_block(w, 1), R.MARKER_SQUARE)
    assert _tops(w)[1] > 0 and _tops(w)[2] > 0
    w._set_block_marker(_block(w, 1), R.MARKER_NONE)
    assert _tops(w) == [0, 0, 0]
    _close(w)


def test_margins_pasted_text_came_with_are_left_alone(qapp):
    html = ('<p style="margin-top:12px;">글</p>'
            f'<p style="margin-top:12px;"><img src="{R.bullet_url_for("square")}" />'
            '  항목</p>')
    w = _note(qapp, html, edit=False)
    assert _tops(w) == [12, 12]
    assert [b["top"] for b in _saved_blocks(w._current_content())] == [12, 12]
    _close(w)


def test_a_finished_todo_is_dimmed_and_struck_on_screen_only(qapp):
    w = _note(qapp)
    _lines(w, "접수\n견적")
    w._set_block_marker(_block(w, 0), R.MARKER_CHECK0)
    w._set_block_marker(_block(w, 1), R.MARKER_CHECK0)
    steps = w._text.document().availableUndoSteps()
    w._toggle_checkbox_block(_block(w, 0))
    qapp.processEvents()
    done = _block(w, 0).layout().formats()
    assert done and done[0].format.fontStrikeOut()
    assert done[0].format.foreground().color().alpha() < 255
    assert not _block(w, 1).layout().formats()
    # 화면에만 입힌 서식이다: 저장 내용과 글자 서식, 되돌리기는 그대로다.
    assert "line-through" not in w._current_content()
    assert not QTextCursor(_block(w, 0)).charFormat().fontStrikeOut()
    assert w._text.document().availableUndoSteps() == steps + 1
    w._toggle_checkbox_block(_block(w, 0))
    qapp.processEvents()
    assert not _block(w, 0).layout().formats()
    _close(w)


def test_undo_takes_back_the_list_and_its_margin_together(qapp):
    w = _note(qapp)
    _lines(w, "글\n하나")
    _at(w, 1)
    w._toggle_list()
    qapp.processEvents()
    assert _block(w, 1).blockFormat().leftMargin() > 0
    w._text.undo()
    qapp.processEvents()
    assert _shape(w)[1] == (R.MARKER_NONE, 0, "하나")
    assert _block(w, 1).blockFormat().leftMargin() == 0 and _tops(w) == [0, 0]
    _close(w)


# ── 주소를 링크로 ───────────────────────────────────────────────────────

def _links(w) -> list[tuple[str, str]]:
    out = []
    block = w._text.document().begin()
    while block.isValid():
        for start, end, href in w._text._link_runs(block):
            out.append((w._text.link_text(start, end), href))
        block = block.next()
    return out


@pytest.mark.parametrize("typed, href", [
    ("https://example.com/a", "https://example.com/a"),
    ("http://192.168.0.20", "http://192.168.0.20"),
    ("www.example.com", "https://www.example.com"),
    ("file:///C:/docs/a.txt", "file:///C:/docs/a.txt"),
])
def test_an_address_becomes_a_link_when_a_space_follows(qapp, typed, href):
    w = _note(qapp)
    QTest.keyClicks(w._text, typed)
    QTest.keyClick(w._text, Qt.Key_Space)
    QTest.keyClicks(w._text, "x")
    qapp.processEvents()
    assert _links(w) == [(typed, href)]
    plain = w._text.toPlainText().replace(R.LINK_SPACER, "").replace(" ", "")
    assert plain == typed + "x"         # 이어서 친 글자는 링크에 딸려 가지 않는다
    _close(w)


def test_an_address_becomes_a_link_when_enter_follows(qapp):
    w = _note(qapp)
    QTest.keyClicks(w._text, "https://example.com")
    QTest.keyClick(w._text, Qt.Key_Return)
    _type(w, "다음")
    qapp.processEvents()
    assert _links(w) == [("https://example.com", "https://example.com")]
    assert w._text.document().blockCount() == 2
    _close(w)


@pytest.mark.parametrize("typed", ["example.com", "http://", "www.", "ftp://a.example/x",
                                    "javascript:alert(1)", "메모"])
def test_other_words_are_left_as_text(qapp, typed):
    w = _note(qapp)
    _type(w, typed)
    QTest.keyClick(w._text, Qt.Key_Space)
    qapp.processEvents()
    assert _links(w) == []
    _close(w)


def test_punctuation_after_an_address_stays_outside_the_link(qapp):
    w = _note(qapp)
    QTest.keyClicks(w._text, "(https://example.com/a).")
    QTest.keyClick(w._text, Qt.Key_Space)
    qapp.processEvents()
    assert _links(w) == []              # 괄호로 시작하는 낱말은 주소로 보지 않는다
    _close(w)

    w = _note(qapp)
    QTest.keyClicks(w._text, "https://example.com/a.")
    QTest.keyClick(w._text, Qt.Key_Space)
    qapp.processEvents()
    assert _links(w) == [("https://example.com/a", "https://example.com/a")]
    _close(w)


def test_addresses_in_pasted_text_become_links(qapp):
    w = _note(qapp)
    data = QMimeData()
    data.setText("자료 https://a.example/x, 그리고 www.b.example 참고\nfile:///C:/a.txt")
    w._text.insertFromMimeData(data)
    _type(w, "끝")
    qapp.processEvents()
    assert _links(w) == [("https://a.example/x", "https://a.example/x"),
                         ("www.b.example", "https://www.b.example"),
                         ("file:///C:/a.txt", "file:///C:/a.txt")]
    assert w._text.toPlainText().replace(R.LINK_SPACER, "").endswith("file:///C:/a.txt끝")
    _close(w)


def test_text_that_is_already_a_link_is_not_linked_again(qapp):
    w = _note(qapp)
    w._text.insert_link("https://example.com", "https://example.com")
    QTest.keyClick(w._text, Qt.Key_Space)
    qapp.processEvents()
    assert len(_links(w)) == 1
    # 아이콘 자리 글자는 하나뿐이다(toPlainText 는 그 글자를 보통 빈칸으로 바꾼다).
    assert w._text.document().toRawText().count(R.LINK_SPACER) == 1
    _close(w)


def test_undo_turns_the_new_link_back_into_text(qapp):
    w = _note(qapp)
    QTest.keyClicks(w._text, "https://example.com")
    QTest.keyClick(w._text, Qt.Key_Space)
    qapp.processEvents()
    assert _links(w)
    w._text.undo()          # 빈칸
    w._text.undo()          # 링크
    qapp.processEvents()
    assert _links(w) == []
    assert "https://example.com" in w._text.toPlainText()
    _close(w)


# ── 목록 메뉴와 폴더 아이콘 ─────────────────────────────────────────────

def test_list_menu_starts_with_square_and_has_numbers(qapp):
    w = _note(qapp)
    w._fill_list_menu()
    labels = [a.text() for a in w._list_menu.actions() if not a.isSeparator()]
    assert labels == [i18n.t("sticky.menu_square"), i18n.t("sticky.menu_circle"),
                      i18n.t("sticky.menu_arrow"), i18n.t("sticky.menu_info"),
                      i18n.t("sticky.menu_number"), i18n.t("sticky.menu_checkbox"),
                      i18n.t("sticky.menu_list_off")]
    _lines(w, "하나")
    _at(w, 0)
    w._apply_number_marker()
    assert _shape(w) == [(R.MARKER_NUMBER, 0, "하나")]
    w._apply_number_marker()            # 한 번 더 고르면 풀린다
    assert _shape(w) == [(R.MARKER_NONE, 0, "하나")]
    _close(w)


def test_folder_icon_menu_can_delete_the_folder(qapp, monkeypatch):
    icon = FolderIconWidget("f1", "업무", "#BAE1FF", settings=AppSettings())
    asked: list[str] = []
    icon.deleteRequested.connect(asked.append)
    seen: list[list[str]] = []

    def run(menu, _pos) -> None:
        seen.append([a.text() for a in menu.actions() if not a.isSeparator()])
        next(a for a in menu.actions()
             if a.text() == i18n.t("main.menu_folder_delete")).trigger()

    monkeypatch.setattr(edit_menu, "run_menu", run)
    icon.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Mouse, QPoint(5, 5),
                                            QPoint(5, 5)))
    assert seen == [[i18n.t("icons.menu_open"), i18n.t("foldericon.menu_open"),
                     i18n.t("foldericon.menu_rename"),
                     i18n.t("foldericon.menu_remove"),
                     i18n.t("main.menu_folder_delete")]]
    assert asked == ["f1"]
    icon.close()
