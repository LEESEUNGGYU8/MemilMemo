"""메모 크기: 한 줄짜리 메모는 그만큼 줄일 수 있다. 편집하는 동안만 서식 줄이 들어가게
잠시 넓히고, 손잡이를 두 번 누르면 높이를 내용에 맞춘다. 넓은 표는 메모 폭을 넘지
않게 메모를 넓히고, 그래도 넘치면 둥근 가로 막대가 보인다."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, QSize, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent, QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app import config, i18n  # noqa: E402
from app.settings import AppSettings  # noqa: E402
from app.ui import theme  # noqa: E402
from app.ui.sticky_note import StickyNoteWidget  # noqa: E402
from app.ui.theme import RoundedScrollBar  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    i18n.set_language("ko")
    theme.apply_theme(app, "")
    yield app


def _note(qapp, w=280, h=220, content=""):
    n = StickyNoteWidget({"id": "s1", "version": 1, "color": "#FFE066",
                          "width": w, "height": h, "content": content},
                         "지우", settings=AppSettings())
    n.apply_local_state(200, 160, w, h)
    n.show()
    qapp.processEvents()
    return n


def test_a_note_can_be_made_as_small_as_one_line(qapp):
    n = _note(qapp)
    assert n.minimumSize() == QSize(config.MIN_NOTE_WIDTH, config.MIN_NOTE_HEIGHT)
    assert config.MIN_NOTE_WIDTH < 220 and config.MIN_NOTE_HEIGHT < 100
    n.resize(config.MIN_NOTE_WIDTH, config.MIN_NOTE_HEIGHT)
    assert n.size() == QSize(config.MIN_NOTE_WIDTH, config.MIN_NOTE_HEIGHT)
    n.close()


def test_editing_grows_a_small_note_and_gives_it_back(qapp):
    seen = []
    n = _note(qapp, 170, 90, "점심 11시 40분")
    n.commitEdit.connect(lambda *a: seen.append((a[3], a[4])))
    n.enter_edit_mode()
    assert n.size() == QSize(config.EDIT_MIN_WIDTH, config.EDIT_MIN_HEIGHT)
    n._text.insertPlainText("!")
    n._commit_and_release()
    assert n.size() == QSize(170, 90)
    # 공유 PC 에는 잠시 넓힌 크기가 아니라 원래 크기를 보낸다.
    assert seen and seen[-1] == (170, 90)
    # 편집하는 동안 손으로 크기를 바꿨으면 그 크기를 그대로 둔다.
    n.enter_edit_mode()
    n.resize(300, 200)
    n._commit_and_release()
    assert n.size() == QSize(300, 200)
    n.close()


def test_double_clicking_the_grip_fits_the_height_to_the_text(qapp):
    n = _note(qapp, 280, 300, "점심 11시 40분")
    grip = n._grip
    local = QPointF(grip.width() / 2, grip.height() / 2)
    ev = QMouseEvent(QEvent.MouseButtonDblClick, local, grip.mapToGlobal(local),
                     Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    sizes = []
    n.sizeChanged.connect(lambda nid, w, h: sizes.append((w, h)))
    grip.mouseDoubleClickEvent(ev)
    assert n.height() < 120 and n.height() >= config.MIN_NOTE_HEIGHT
    assert sizes == [(280, n.height())]
    # 긴 메모는 화면 높이까지만 늘어난다.
    long = _note(qapp, 280, 120, "\n".join(f"{i}번째 줄" for i in range(200)))
    long.fit_to_content()
    assert long.height() <= long._avail().height() and long.height() > 300
    n.close()
    long.close()


def test_a_wide_table_widens_the_note_instead_of_spilling_over(qapp):
    n = _note(qapp, 220, 260)
    n.enter_edit_mode()
    n._text.setPlainText("소모품")
    n._text.moveCursor(QTextCursor.End)
    n.insert_table(3, 8)
    qapp.processEvents()
    doc = n._text.document()
    assert doc.size().width() <= n._text.viewport().width()
    assert n._text.horizontalScrollBar().maximum() == 0
    n.close()


def test_both_scrollbars_are_drawn_round(qapp):
    n = _note(qapp)
    assert isinstance(n._text.verticalScrollBar(), RoundedScrollBar)
    h = n._text.horizontalScrollBar()
    assert isinstance(h, RoundedScrollBar) and h.orientation() == Qt.Horizontal
    h.setRange(0, 100)
    h.setPageStep(50)
    h.resize(200, 10)
    rect = h._slider_rect()
    assert rect.width() > rect.height() > 0          # 가로 막대의 손잡이는 가로로 길다
    n.close()
