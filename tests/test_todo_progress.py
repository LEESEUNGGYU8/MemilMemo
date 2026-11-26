"""접힌 쪽지와 아이콘에 할 일 진행률('끝낸 것/모두')을 적는다."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QImage, QPainter, QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app import i18n  # noqa: E402
from app.settings import AppSettings  # noqa: E402
from app.ui import note_fold as nf  # noqa: E402
from app.ui import theme  # noqa: E402
from app.ui.rich_text import MARKER_CHECK1, MARKER_PROP  # noqa: E402
from app.ui.sticky_note import StickyNoteWidget  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    i18n.set_language("ko")
    theme.apply_theme(app, "")
    yield app


def _note(qapp):
    w = StickyNoteWidget({"id": "p1", "version": 1, "color": "#FFB3BA",
                          "width": 300, "height": 260, "content": ""},
                         "지우", settings=AppSettings())
    w.apply_local_state(200, 160)
    w.show()
    qapp.processEvents()
    return w


def _check_lines(w, lines: list[str], done: set[int]) -> None:
    w.enter_edit_mode()
    w._text.setPlainText("이번 주 할 일\n" + "\n".join(lines))
    cursor = w._text.textCursor()
    cursor.movePosition(QTextCursor.Start)
    cursor.movePosition(QTextCursor.NextBlock)
    cursor.movePosition(QTextCursor.End, QTextCursor.KeepAnchor)
    w._text.setTextCursor(cursor)
    w._apply_checkbox_marker()
    block = w._text.document().begin().next()
    index = 0
    while block.isValid():
        if index in done:
            c = QTextCursor(block)
            fmt = block.blockFormat()
            fmt.setProperty(MARKER_PROP, MARKER_CHECK1)
            c.setBlockFormat(fmt)
        index += 1
        block = block.next()


def test_counts_done_and_all_to_dos(qapp):
    w = _note(qapp)
    assert w.todo_progress() == ""
    _check_lines(w, ["세금계산서", "송장", "토너", "회식"], {0, 2})
    assert w.todo_progress() == "2/4"
    # 본문이 바뀌면 다시 센다.
    cursor = QTextCursor(w._text.document().lastBlock())
    fmt = cursor.blockFormat()
    fmt.setProperty(MARKER_PROP, MARKER_CHECK1)
    cursor.setBlockFormat(fmt)
    assert w.todo_progress() == "3/4"
    w.close()


def _slip(progress: str) -> QImage:
    img = QImage(184, 40, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    nf.paint_slip(p, QRectF(0, 0, 184, 40), "#FFB3BA", "이번 주 할 일", progress=progress)
    p.end()
    return img


def _differs(a: QImage, b: QImage, x0: int, x1: int) -> bool:
    return any(a.pixelColor(x, y) != b.pixelColor(x, y)
               for x in range(x0, x1) for y in range(8, 32))


def test_the_slip_shows_it_at_the_right(qapp):
    plain, shown = _slip(""), _slip("3/5")
    assert _differs(plain, shown, 120, 170)        # 오른쪽에 진행률
    assert not _differs(plain, shown, 0, 10)       # 왼쪽 가장자리는 그대로


def test_the_icon_shows_it_on_the_picture(qapp):
    def icon(progress: str) -> QImage:
        img = QImage(76, 100, QImage.Format_ARGB32)
        img.fill(Qt.transparent)
        p = QPainter(img)
        nf.paint_icon(p, QRectF(0, 0, 76, 100), "#FFB3BA", "할 일", icon=48.0,
                      progress=progress)
        p.end()
        return img
    a, b = icon(""), icon("3/5")
    assert any(a.pixelColor(x, y) != b.pixelColor(x, y)
               for x in range(38, 76) for y in range(30, 60))
