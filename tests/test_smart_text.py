"""본문 자동 인식: 날짜 밑줄과 누르면 알림, 계산 줄, 고른 글의 합계, 연락처 복사, #태그 거르기."""
from __future__ import annotations

import os
import time
from datetime import datetime

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QMouseEvent, QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app import i18n  # noqa: E402
from app import reminders as rm  # noqa: E402
from app.local_state import LocalStateStore  # noqa: E402
from app.network.client_base import ClientBase  # noqa: E402
from app.settings import AppSettings  # noqa: E402
from app.ui import reminder_dialog, theme  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.rich_text import RichTextEdit  # noqa: E402
from app.ui.sticky_note import StickyNoteWidget  # noqa: E402

NOW = datetime(2026, 11, 25, 10, 0)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    i18n.set_language("ko")
    theme.apply_theme(app, "")
    yield app


def edit_with(text, read_only=True):
    edit = RichTextEdit()
    edit.resize(400, 200)
    edit.setPlainText(text)
    edit.setReadOnly(read_only)
    return edit


def test_dates_and_contacts_are_found_only_while_not_editing(qapp):
    edit = edit_with("다음 회의 내일 오후 3시\n연락처 010-1234-5678")
    edit.refresh_smart(NOW)
    kinds = [(h.kind, h.value) for h in edit.smart_hits()]
    assert ("date", datetime(2026, 11, 26, 15, 0)) in kinds
    assert ("contact", "010-1234-5678") in kinds
    date = next(h for h in edit.smart_hits() if h.kind == "date")
    cursor = QTextCursor(edit.document())
    cursor.setPosition(date.start + 1)
    point = edit.cursorRect(cursor).center()
    assert edit.smart_at(point) is not None and edit.smart_at(point).kind == "date"
    edit.setReadOnly(False)
    edit.refresh_smart(NOW)
    assert edit.smart_hits() == []


def test_a_calculation_line_shows_and_takes_its_result(qapp):
    edit = edit_with("점심 12,000 + 8,500 =", read_only=False)
    cursor = edit.textCursor()
    cursor.movePosition(QTextCursor.End)
    edit.setTextCursor(cursor)
    assert edit.calc_hint().text == "20,500"
    assert edit.accept_calc()
    assert edit.toPlainText() == "점심 12,000 + 8,500 = 20,500"
    assert edit.calc_hint() is None
    edit.grab()                                     # 그려도 된다


def _note(text, settings=None):
    note = StickyNoteWidget({"id": "n", "content": text, "color": "#FFE066"}, "지우",
                            settings=settings or AppSettings())
    note.resize(320, 240)
    return note


def test_selecting_numbers_shows_their_sum(qapp):
    note = _note("<p>복사용지 5,000</p><p>토너 32,000</p>")
    cursor = note._text.textCursor()
    cursor.select(QTextCursor.Document)
    note._text.setTextCursor(cursor)
    # 고르기를 멈춘 뒤에 센다.
    assert note._sum_timer.isActive()
    note._sum_timer.stop()
    note._on_selection_changed()
    assert note._sum_line.text() == "합계 37,000    평균 18,500    개수 2"
    assert not note._sum_line.isHidden()
    cursor.clearSelection()
    note._text.setTextCursor(cursor)
    note._on_selection_changed()
    assert note._sum_line.isHidden()
    note._teardown = True
    note.close()


def _click(note, hit):
    cursor = QTextCursor(note._text.document())
    cursor.setPosition(hit.start + 1)
    point = QPointF(note._text.cursorRect(cursor).center())
    for kind in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
        event = QMouseEvent(kind, point, point, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        assert note._smart_event(event)


def test_clicking_a_date_asks_for_a_reminder_and_a_contact_copies(qapp):
    note = _note("<p>마감 12월 3일 14:30</p><p>메일 kim@example.com</p>")
    note.show()
    note._text.refresh_smart(NOW)
    got = []
    note.dateReminderRequested.connect(lambda *a: got.append(a))
    date = next(h for h in note._text.smart_hits() if h.kind == "date")
    _click(note, date)
    assert got == [("n", datetime(2026, 12, 3, 14, 30), True)]
    mail = next(h for h in note._text.smart_hits() if h.kind == "contact")
    _click(note, mail)
    assert QGuiApplication.clipboard().text() == "kim@example.com"
    note._teardown = True
    note.close()


# ── 메인 창 ──

def note_dict(nid, text):
    return {"id": nid, "version": 1, "color": "#FFE066", "width": 280, "height": 220,
            "content": text, "created_by": "지우", "updated_by": "지우",
            "updated_at": int(time.time() * 1000)}


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(StickyNoteWidget, "bring_to_front",
                        lambda self, *, start_editing=False: None)
    client = ClientBase("지우")
    win = MainWindow(client=client, local_state=LocalStateStore(tmp_path / "ls.json"),
                     role="guest", settings=AppSettings(fold_animation=False))
    client.connectionChanged.emit(True)
    client._dispatch("welcome", {
        "users": ["지우"], "host": "지우", "locks": {}, "heartbeat_ms": 3000,
        "trash_days": 30, "reminders": {}, "folders": [],
        "notes": [note_dict("a", "<p>견적 #회계</p>"), note_dict("b", "<p>#회계팀 일정</p>"),
                  note_dict("c", "<p>#회계 마감 #세금</p>")]})
    yield win
    win._force_quit = True
    win._teardown_stickies()
    win.close()
    win.deleteLater()
    qapp.processEvents()


def test_a_date_opens_a_fresh_reminder_at_that_time(window, monkeypatch):
    asked = []
    monkeypatch.setattr(reminder_dialog, "ask_reminder",
                        lambda parent, rule, mode, **kw: asked.append((rule, kw)) or
                        (None, None, None))
    window._on_date_reminder("a", datetime(2026, 12, 3, 14, 30), True)
    rule, kw = asked[-1]
    assert rule == rm.Rule(rm.ONCE, "2026-12-03", "14:30") and kw.get("fresh") is True
    window._on_date_reminder("a", datetime(2026, 12, 4), False)    # 시각이 없으면 9시
    assert asked[-1][0].time == "09:00"


def test_tags_appear_in_the_rail_and_filter_exactly(window, qapp):
    tags = window._rail.tags
    assert list(tags)[:2] == ["회계", "세금"] or "회계" in tags
    assert tags["회계"].count_text() == "2"
    tags["회계"].click()
    assert window._search_input.text() == "#회계"
    visible = {nid for nid, c in window._cards.items() if not c.isHidden()}
    assert visible == {"a", "c"}                    # '#회계팀' 은 다른 태그다


def test_typing_at_offers_participant_names(qapp):
    note = _note("<p></p>")
    note.set_people(["서연", "서준", "하준"])
    note._edit_mode = True
    note._text.setReadOnly(False)
    note._text.insertPlainText("확인 부탁 @서")
    assert note.mention_choices() == ["서연", "서준"]
    from PySide6.QtGui import QKeyEvent
    down = QKeyEvent(QEvent.KeyPress, Qt.Key_Down, Qt.NoModifier)
    enter = QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier)
    assert note._mention_key(down) and note._mention_key(enter)
    assert note._text.toPlainText() == "확인 부탁 @서준 "
    assert note.mention_choices() == []
    note._text.insertPlainText("mail@서")                   # 메일 주소 모양은 아니다
    assert note.mention_choices() == []
    note._teardown = True
    note.close()


def test_similar_lines_are_totalled_when_turned_on(qapp):
    note = _note("<p>김치찌개 2 (지우, 서연)</p><p>제육볶음 1 (하준)</p><p>김치찌개 1 (민준)</p>")
    assert note._tally_line.isHidden()
    note.set_tally(True)
    text = note._tally_line.text()
    assert "항목별 합계" in text and "김치찌개 3&nbsp;&nbsp;" in text
    assert "지우, 서연, 민준" in text and "제육볶음 1" in text and "하준" in text
    note.set_tally(False)
    assert note._tally_line.isHidden()
    note._teardown = True
    note.close()


def test_the_tally_choice_is_kept_on_this_pc(window):
    window._stickies["a"].tallyToggled.emit("a", True)
    assert window._local.state.tally_notes == ["a"]
    from app.local_state import LocalStateStore
    again = LocalStateStore(window._local.path)
    assert again.state.tally_notes == ["a"]
    window._stickies["a"].tallyToggled.emit("a", False)
    assert window._local.state.tally_notes == []


def test_tags_in_a_note_are_drawn_apart_from_plain_text(qapp):
    """#태그는 남색 글자와 옅은 바탕으로 보인다(화면에만, 저장 내용은 그대로). 색 값과
    번호는 태그가 아니다."""
    from app.ui.rich_text import TAG_INK, RichTextEdit
    edit = RichTextEdit()
    edit.setPlainText("재고 실사 #재고 #견적\n색 #FFF 번호 #1")
    spans = edit.tag_spans()
    text = edit.toPlainText()
    assert [text[a:b] for a, b in spans] == ["#재고", "#견적"]
    block = edit.document().begin()
    colors = {r.format.foreground().color().name().upper()
              for r in block.layout().formats() if r.length}
    assert TAG_INK[0].upper() in colors
    html = edit.toHtml()
    assert TAG_INK[0].lower() not in html.lower()          # 저장 내용에는 없다
    edit.close()
