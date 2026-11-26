"""파일 종류별로 첨부: 메모 편집기에 놓거나 붙여 넣은 파일, '첨부 방식' 단추, 설정의 기본."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData, QUrl  # noqa: E402
from PySide6.QtGui import QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app import i18n  # noqa: E402
from app.settings import AppSettings  # noqa: E402
from app.ui import theme  # noqa: E402
from app.ui.rich_text import MARKER_PROP, RichTextEdit  # noqa: E402
from app.ui.sticky_note import StickyNoteWidget  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    i18n.set_language("ko")
    theme.apply_theme(app, "")
    yield app


def files_mime(*paths) -> QMimeData:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    return mime


def test_a_text_file_goes_in_as_text_and_can_switch_to_a_link(qapp, tmp_path):
    txt = tmp_path / "회의.txt"
    txt.write_text("첫 줄\n둘째 줄", encoding="utf-8")
    edit = RichTextEdit()
    edit.insertFromMimeData(files_mime(txt))
    assert "첫 줄" in edit.toPlainText() and "둘째 줄" in edit.toPlainText()
    button = edit.attach_button()
    assert button is not None and button.text().startswith("첨부 방식")
    edit._switch_attachment("link")
    assert "첫 줄" not in edit.toPlainText() and "회의.txt" in edit.toPlainText()
    edit.document().undo()          # 바꾸기 전으로(한 번에)
    assert "첫 줄" in edit.toPlainText() and "회의.txt" not in edit.toPlainText()
    edit.document().undo()          # 첨부 전으로(한 번에 빠진다)
    assert edit.toPlainText().strip() == ""


def test_typing_hides_the_attach_button(qapp, tmp_path):
    txt = tmp_path / "a.txt"
    txt.write_text("글", encoding="utf-8")
    edit = RichTextEdit()
    edit.insertFromMimeData(files_mime(txt))
    assert edit.attach_button() is not None
    edit.textCursor().insertText("더")
    assert edit.attach_button() is None


def test_a_csv_becomes_a_table_and_a_pdf_stays_a_link(qapp, tmp_path):
    csv = tmp_path / "단가.csv"
    csv.write_text("품목,단가\n복사용지,5000\n토너,32000\n", encoding="utf-8")
    pdf = tmp_path / "견적.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    edit = RichTextEdit()
    edit.insertFromMimeData(files_mime(csv, pdf))
    tables = [f for f in edit.document().rootFrame().childFrames()]
    assert tables and "복사용지" in edit.toPlainText()
    assert "견적.pdf" in edit.toPlainText()
    assert edit.attach_button() is None          # PDF 는 링크 하나뿐이다


def test_settings_can_make_text_files_links(qapp, tmp_path):
    txt = tmp_path / "b.txt"
    txt.write_text("본문", encoding="utf-8")
    edit = RichTextEdit()
    edit.set_attach_modes({"text": "link"})
    edit.insertFromMimeData(files_mime(txt))
    assert "본문" not in edit.toPlainText() and "b.txt" in edit.toPlainText()


def test_a_right_drag_asks_how_to_attach(qapp, tmp_path, monkeypatch):
    txt = tmp_path / "c.txt"
    txt.write_text("내용", encoding="utf-8")
    edit = RichTextEdit()
    monkeypatch.setattr(edit, "_drop_mode_menu", lambda paths: "both")
    edit._right_drag = True
    mode = edit._drop_mode_menu([str(txt)])
    edit.attach_files([str(txt)], mode)
    text = edit.toPlainText()
    assert "내용" in text and "c.txt" in text


def test_markdown_lists_become_note_lists_only_inside_the_attachment(qapp, tmp_path):
    md = tmp_path / "할 일.md"
    md.write_text("# 할 일\n\n- [ ] 토너 주문\n- [x] 견적 회신\n- 메모\n", encoding="utf-8")
    note = StickyNoteWidget({"id": "n", "content": "<p>1. 그대로 둘 글</p>",
                             "color": "#FFE066"}, "지우", settings=AppSettings())
    doc = note._text.document()
    cursor = note._text.textCursor()
    cursor.movePosition(QTextCursor.End)
    note._text.setTextCursor(cursor)
    note._text.insertFromMimeData(files_mime(md))
    first = doc.begin()
    assert first.text().startswith("1. 그대로")          # 원래 줄의 '1.' 은 번호로 바뀌지 않는다
    marked = []
    block = doc.begin()
    while block.isValid():
        if block.blockFormat().intProperty(MARKER_PROP):
            marked.append(block.text())
        block = block.next()
    assert any("토너 주문" in t for t in marked) and any("메모" in t for t in marked)
    note._teardown = True
    note.close()
