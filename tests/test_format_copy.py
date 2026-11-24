"""서식 복사: 글자 서식 전체와 글머리 기호를 떠서 다른 메모에도 입힌다."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtGui import QColor, QFont, QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app import i18n  # noqa: E402
from app.settings import AppSettings  # noqa: E402
from app.ui import edit_menu, rich_text, theme  # noqa: E402
from app.ui.sticky_note import StickyNoteWidget  # noqa: E402

R = rich_text


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    i18n.set_language("ko")
    theme.apply_theme(app, "")
    yield app


@pytest.fixture(autouse=True)
def _empty_clip():
    R.set_copied_format(None)
    yield
    R.set_copied_format(None)


def _note(qapp, text: str = "", *, edit: bool = True, note_id: str = "f1",
          color: str = "#FFE066"):
    w = StickyNoteWidget({"id": note_id, "version": 1, "color": color,
                          "width": 300, "height": 320, "content": ""},
                         "지우", settings=AppSettings())
    w.apply_local_state(200, 160)
    w.show()
    qapp.processEvents()
    w.enter_edit_mode()
    w._text.setPlainText(text)
    w._text.document().clearUndoRedoStacks()
    if not edit:
        w._edit_mode = False
    return w


def _close(w) -> None:
    w._teardown = True
    w.close()


def _select(w, block: int, start: int = 0, length: int | None = None) -> None:
    b = w._text.document().findBlockByNumber(block)
    cur = QTextCursor(b)
    cur.setPosition(b.position() + start)
    cur.setPosition(b.position() + (b.length() - 1 if length is None
                                    else start + length), QTextCursor.KeepAnchor)
    w._text.setTextCursor(cur)


def _fmt(w, block: int, offset: int = 1):
    b = w._text.document().findBlockByNumber(block)
    cur = QTextCursor(b)
    cur.setPosition(b.position() + offset)
    return cur.charFormat()


def _marker(w, block: int) -> tuple[int, int]:
    b = w._text.document().findBlockByNumber(block)
    return b.blockFormat().intProperty(R.MARKER_PROP), w._text.block_level(b)


def _dress_first_line(w) -> None:
    """첫 줄을 굵게, 기울여, 밑줄과 줄 긋기, 파랑에 노란 형광펜, 2단계 동그라미로."""
    _select(w, 0)
    w._toggle_bold()
    w._toggle_italic()
    w._toggle_underline()
    w._toggle_strike()
    w._apply_font_color(QColor("#1D4ED8"))
    w._apply_highlight("#FFF59D")
    w._text.set_block_marker(w._text.document().findBlockByNumber(0),
                             R.MARKER_CIRCLE, 1)
    w._text.restyle_lists()


def test_the_whole_font_format_and_the_bullet_are_copied(qapp):
    w = _note(qapp, "회의 준비\n자료 인쇄")
    _dress_first_line(w)
    _select(w, 0, 0, 2)
    w._copy_format()
    clip = R.copied_format()
    assert (clip.bold, clip.italic, clip.underline, clip.strike) == (True,) * 4
    assert clip.ink == "#1d4ed8" and clip.highlight == "#fff59d"
    assert (clip.marker, clip.level) == (R.MARKER_CIRCLE, 1)
    _close(w)


def test_pasting_dresses_the_selection_in_another_note(qapp):
    source = _note(qapp, "회의 준비", note_id="s1")
    _dress_first_line(source)
    _select(source, 0)
    source._copy_format()

    target = _note(qapp, "거래처 회신\n견적서 보내기\n손대지 않는 줄", note_id="t1")
    _select(target, 0)
    cur = target._text.textCursor()                 # 첫 줄 처음부터 둘째 줄 끝까지
    second = target._text.document().findBlockByNumber(1)
    cur.setPosition(0)
    cur.setPosition(second.position() + second.length() - 1, QTextCursor.KeepAnchor)
    target._text.setTextCursor(cur)
    target._paste_format()
    for block in (0, 1):
        fmt = _fmt(target, block)
        assert fmt.fontWeight() > QFont.Normal and fmt.fontItalic()
        assert fmt.fontUnderline() and fmt.fontStrikeOut()
        assert fmt.foreground().color().name() == "#1d4ed8"
        assert fmt.background().color().name() == "#fff59d"
        assert _marker(target, block) == (R.MARKER_CIRCLE, 1)
    plain = _fmt(target, 2)
    assert plain.fontWeight() <= QFont.Normal and _marker(target, 2)[0] == R.MARKER_NONE
    # 되돌리기 한 번에 서식과 글머리 기호가 함께 돌아간다.
    target._text.undo()
    assert _fmt(target, 0).fontWeight() <= QFont.Normal
    assert _marker(target, 0)[0] == R.MARKER_NONE
    assert target._text.toPlainText() == "거래처 회신\n견적서 보내기\n손대지 않는 줄"
    _close(source)
    _close(target)


def test_plain_format_clears_the_font_but_keeps_the_bullets(qapp):
    """목록이 아닌 줄에서 뜬 서식은 있던 글머리 기호를 지우지 않는다."""
    w = _note(qapp, "평범한 줄\n꾸민 줄")
    _select(w, 1)
    w._toggle_bold()
    w._apply_font_color(QColor("#B91C1C"))
    w._apply_highlight("#C5E1A5")
    w._text.set_block_marker(w._text.document().findBlockByNumber(1),
                             R.MARKER_SQUARE, 0)
    _select(w, 0, 0, 2)
    w._copy_format()
    assert R.copied_format() == R.CopiedFormat()
    _select(w, 1)
    w._paste_format()
    fmt = _fmt(w, 1)
    assert fmt.fontWeight() <= QFont.Normal
    assert not fmt.hasProperty(rich_text.QTextFormat.ForegroundBrush)
    assert not fmt.hasProperty(rich_text.QTextFormat.BackgroundBrush)
    assert _marker(w, 1) == (R.MARKER_SQUARE, 0)
    assert "color:" not in w._text.toHtml()
    _close(w)


def test_without_a_selection_the_word_under_the_click_is_used(qapp):
    w = _note(qapp, "회의 준비 자료")
    _select(w, 0, 0, 2)
    w._toggle_bold()
    cur = w._text.textCursor()
    cur.clearSelection()
    w._text.setTextCursor(cur)
    # '회의'를 누르고 뜬 뒤 '자료'를 누르고 입힌다.
    first = w._text.cursorRect(QTextCursor(w._text.document())).center()
    w._copy_format(first + QPoint(2, 0))
    assert R.copied_format().bold
    end = QTextCursor(w._text.document())
    end.setPosition(7)
    w._paste_format(w._text.cursorRect(end).center())
    assert _fmt(w, 0, 7).fontWeight() > QFont.Normal        # 자료
    assert _fmt(w, 0, 4).fontWeight() <= QFont.Normal       # 준비는 그대로
    _close(w)


def test_a_finished_task_is_copied_as_a_task_and_a_link_keeps_its_look(qapp):
    w = _note(qapp, "끝낸 일\n주소 https://example.com 참고")
    doc = w._text.document()
    w._text.set_block_marker(doc.findBlockByNumber(0), R.MARKER_CHECK1, 0)
    _select(w, 0)
    w._copy_format()
    assert R.copied_format().marker == R.MARKER_CHECK0      # 끝낸 표시는 서식이 아니다
    # 링크에서 뜨면 밑줄과 색은 따라오지 않는다.
    w._text.sync_link_icons()
    block = doc.findBlockByNumber(1)
    cur = QTextCursor(block)
    cur.setPosition(block.position() + 6)
    cur.setPosition(block.position() + 9, QTextCursor.KeepAnchor)
    w._text.setTextCursor(cur)
    if w._text.link_at_cursor() is not None:
        w._copy_format()
        clip = R.copied_format()
        assert not clip.underline and clip.ink == ""
    _close(w)


def test_the_menu_offers_copy_always_and_paste_while_editing(qapp, monkeypatch):
    shown: list[list[tuple[str, bool]]] = []

    def run(menu, _pos):
        shown.append([(a.text(), a.isEnabled()) for a in menu.actions()
                      if not a.isSeparator()])

    monkeypatch.setattr(edit_menu, "run_menu", run)
    copy, paste = i18n.t("sticky.menu_format_copy"), i18n.t("sticky.menu_format_paste")
    w = _note(qapp, "회의 준비")
    w._show_text_menu(QPoint(20, 12), QPoint(0, 0))
    assert (copy, True) in shown[-1] and (paste, False) in shown[-1]    # 뜬 것이 없다
    _select(w, 0)
    w._copy_format()
    w._show_text_menu(QPoint(20, 12), QPoint(0, 0))
    assert (paste, True) in shown[-1]
    # 보기 상태에서는 뜰 수만 있다.
    w._commit_and_release()
    qapp.processEvents()
    if not w._edit_mode:
        w._show_text_menu(QPoint(20, 12), QPoint(0, 0))
        names = [name for name, _on in shown[-1]]
        assert copy in names and paste not in names
        R.set_copied_format(R.CopiedFormat(bold=True))
        w._paste_format()                           # 편집 중이 아니면 아무 일도 없다
        assert _fmt(w, 0).fontWeight() <= QFont.Normal
    _close(w)


def test_an_ink_that_cannot_be_seen_on_the_other_note_is_moved(qapp):
    source = _note(qapp, "어두운 글자", note_id="s2")
    _select(source, 0)
    source._apply_font_color(QColor("#171717"))
    _select(source, 0)
    source._copy_format()
    target = _note(qapp, "어두운 메모", note_id="t2", color="#3A3A3A")
    _select(target, 0)
    target._paste_format()
    assert _fmt(target, 0).foreground().color().name() == "#ffffff"
    _close(source)
    _close(target)
