"""알림(나만)과 체크 해제 예약: 규칙 계산, 정하는 창, 알림 창의 단추, 메모 머리 줄,
메인 창의 매 분 확인."""
from __future__ import annotations

import json
import os
from datetime import date, datetime

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent, QMouseEvent, QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app import i18n  # noqa: E402
from app import reminders as rm  # noqa: E402
from app.local_state import LocalStateStore  # noqa: E402
from app.network.client_base import ClientBase  # noqa: E402
from app.settings import AppSettings  # noqa: E402
from app.ui import reminder_dialog, theme  # noqa: E402
from app.ui import toast as toast_mod  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.rich_text import MARKER_CHECK1, MARKER_PROP  # noqa: E402
from app.ui.sticky_note import StickyNoteWidget  # noqa: E402

ME = "지우"
OTHER = "서연"


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    i18n.set_language("ko")
    theme.apply_theme(app, "")
    yield app


def R(**kw) -> rm.Rule:
    return rm.Rule(**kw)


# ── 규칙 ──

def test_the_31st_falls_on_the_last_day_of_short_months():
    rule = R(kind=rm.MONTHLY, date="2026-01-31", time="10:00")
    at = rm.next_time(rule, datetime(2026, 2, 1))
    assert at == datetime(2026, 2, 28, 10, 0)
    assert rm.next_time(rule, at) == datetime(2026, 3, 31, 10, 0)
    assert rm.next_time(rule, datetime(2026, 4, 1)) == datetime(2026, 4, 30, 10, 0)


def test_nth_weekday_and_the_fifth_means_the_last_one():
    second_thu = R(kind=rm.MONTHLY, date="2026-11-12", time="09:00", monthly=rm.BY_NTH)
    assert rm.next_time(second_thu, datetime(2026, 11, 13)) == datetime(2026, 12, 10, 9, 0)
    fifth_thu = R(kind=rm.MONTHLY, date="2026-10-29", time="09:00", monthly=rm.BY_NTH)
    # 11월에는 목요일이 넷뿐이다. 그 달의 마지막 목요일로 본다.
    assert rm.next_time(fifth_thu, datetime(2026, 10, 30)) == datetime(2026, 11, 26, 9, 0)


def test_last_day_and_the_leap_day():
    last = R(kind=rm.MONTHLY, date="2026-01-15", time="18:00", monthly=rm.BY_LAST)
    assert rm.next_time(last, datetime(2027, 2, 3)) == datetime(2027, 2, 28, 18, 0)
    leap = R(kind=rm.YEARLY, date="2028-02-29", time="09:00")
    assert rm.next_time(leap, datetime(2029, 1, 1)) == datetime(2029, 2, 28, 9, 0)
    assert rm.next_time(leap, datetime(2031, 3, 1)) == datetime(2032, 2, 29, 9, 0)


def test_weekdays_skip_the_weekend_and_weekly_uses_the_days():
    friday = datetime(2026, 11, 27, 10, 0)
    assert rm.next_time(R(kind=rm.WEEKDAYS, time="09:00"), friday) == datetime(2026, 11, 30, 9, 0)
    weekly = R(kind=rm.WEEKLY, time="09:00", days=[0, 3])          # 월, 목
    assert rm.next_time(weekly, datetime(2026, 11, 25, 12, 0)) == datetime(2026, 11, 26, 9, 0)
    assert rm.next_time(weekly, datetime(2026, 11, 26, 9, 0)) == datetime(2026, 11, 30, 9, 0)


def test_a_repeat_starts_on_its_date():
    daily = R(kind=rm.DAILY, date="2026-12-01", time="09:00")
    assert rm.next_time(daily, datetime(2026, 11, 25, 12)) == datetime(2026, 12, 1, 9, 0)
    assert rm.next_time(daily, datetime(2026, 12, 3, 12)) == datetime(2026, 12, 4, 9, 0)
    monthly = R(kind=rm.MONTHLY, date="2026-12-15", time="09:00")
    assert rm.next_time(monthly, datetime(2026, 11, 10)) == datetime(2026, 12, 15, 9, 0)


def test_once_in_the_past_and_the_latest_missed_time():
    once = R(kind=rm.ONCE, date="2026-11-25", time="15:00")
    assert rm.next_time(once, datetime(2026, 11, 25, 15, 0)) is None
    daily = R(kind=rm.DAILY, time="09:00")
    assert rm.last_time(daily, datetime(2026, 11, 20, 12), datetime(2026, 11, 25, 8)) \
        == datetime(2026, 11, 24, 9)
    assert rm.last_time(daily, datetime(2026, 11, 25, 9), datetime(2026, 11, 25, 9, 30)) is None


def test_snooze_and_quick_picks():
    now = datetime(2026, 11, 25, 14, 37, 20)
    assert rm.snooze_until(now, 10) == datetime(2026, 11, 25, 14, 47)
    assert rm.snooze_until(now, None) == datetime(2026, 11, 26, 9, 0)
    monday = rm.quick(now, "monday")
    assert (monday.kind, monday.date, monday.time) == (rm.ONCE, "2026-11-30", "09:00")
    assert rm.quick(now, "60m").time == "15:37"


def test_saved_rules_are_checked():
    assert rm.Rule.from_dict({"kind": "weekly", "time": "09:00", "days": []}) is None
    assert rm.Rule.from_dict({"kind": "once", "time": "25:00", "date": "2026-11-25"}) is None
    assert rm.Rule.from_dict({"kind": "once", "time": "09:00", "date": "2026-02-30"}) is None
    assert rm.Rule.from_dict("daily") is None
    assert rm.Rule.from_dict({"kind": "daily", "time": "09:00", "lead": 7}).lead == 0
    rule = R(kind=rm.WEEKLY, time="08:30", days=[4, 0], lead=10)
    assert rm.Rule.from_dict(rule.to_dict()) == rm.Rule(rm.WEEKLY, "", "08:30", [0, 4],
                                                        rm.BY_DATE, 10)


# ── 글 ──

def test_texts(qapp):
    rule = R(kind=rm.WEEKLY, time="09:00", days=[0, 3])
    assert reminder_dialog.describe(rule) == "매주 월, 목 오전 9:00"
    now = datetime(2026, 11, 25, 10, 0)
    assert reminder_dialog.short_when(datetime(2026, 11, 25, 15, 0), now) == "오늘 오후 3:00"
    assert reminder_dialog.short_when(datetime(2026, 11, 26, 9, 0), now) == "내일 오전 9:00"
    assert reminder_dialog.short_when(datetime(2026, 11, 27, 9, 0), now) == "금 오전 9:00"
    assert reminder_dialog.short_when(datetime(2026, 12, 10, 9, 0), now) == "12/10 오전 9:00"
    forms = reminder_dialog.when_forms
    assert forms(datetime(2026, 11, 25, 15, 0), now) == ["오늘 오후 3:00", "오후 3:00", "15:00"]
    assert forms(datetime(2026, 11, 26, 21, 5), now) == ["내일 오후 9:05", "내일 21:05", "내일"]
    assert forms(datetime(2026, 11, 27, 9, 0), now) == ["금 오전 9:00", "금 9:00", "금"]
    assert forms(datetime(2026, 12, 10, 9, 0), now) == ["12/10 오전 9:00", "12/10 9:00", "12/10"]
    today = date(2026, 11, 25)
    assert reminder_dialog.format_day(date(2026, 12, 1), today) == "12월 1일 (화)"
    assert reminder_dialog.format_day(date(2027, 1, 5), today) == "2027년 1월 5일 (화)"


# ── 정하는 창 ──

def test_the_dialog_shows_and_returns_the_rule(qapp):
    now = lambda: datetime(2026, 11, 25, 10, 0)  # noqa: E731
    rule = R(kind=rm.WEEKLY, date="2026-11-25", time="08:30", days=[0, 3], lead=10)
    d = reminder_dialog.ReminderDialog(None, rule, now=now)
    got = d.rule()
    assert (got.kind, got.time, got.days, got.lead) == (rm.WEEKLY, "08:30", [0, 3], 10)
    assert d._save.isEnabled() and d._clear.isVisibleTo(d)
    # 날짜와 시각은 같은 펼침 칸이고, 반복이면 다음 알림을 한 줄로 알린다.
    assert (d.date_text(), d.time_text()) == ("11월 25일 (수)", "오전 8:30")
    assert type(d._date) is type(d._time)
    assert d.status_text() == "다음 알림 11월 26일 (목) 오전 8:30"
    # 요일을 모두 끄면 저장할 수 없다.
    for b in d._days:
        b.setChecked(False)
    assert not d._save.isEnabled()
    d.deleteLater()


def test_a_past_one_time_reminder_cannot_be_saved(qapp):
    now = lambda: datetime(2026, 11, 25, 10, 0)  # noqa: E731
    d = reminder_dialog.ReminderDialog(None, R(kind=rm.ONCE, date="2026-11-25", time="09:00"),
                                       now=now)
    assert not d._save.isEnabled() and d.status_text() == "지난 시각입니다"
    d._take_quick("10m")
    assert d._save.isEnabled() and d.rule().time == "10:10"
    assert d.status_text() == ""            # 한 번이면 날짜와 시각 칸이 곧 알릴 때다
    d.deleteLater()


def test_quick_picks_change_only_the_time_and_weekly_follows_the_date(qapp):
    now = lambda: datetime(2026, 11, 25, 10, 0)  # noqa: E731  수요일
    d = reminder_dialog.ReminderDialog(None, None, now=now)
    assert d.rule().kind == rm.ONCE and d.date_text() == "11월 26일 (목)"
    d._kind.setCurrentIndex(d._kind.findData(rm.WEEKLY))
    assert d.rule().days == [3]                         # 날짜의 요일을 고른다
    d._take_quick("monday")
    got = d.rule()
    assert (got.kind, got.date, got.time, got.days) == (rm.WEEKLY, "2026-11-30", "09:00", [0])
    d._days[4].setChecked(True)                         # 요일을 더 고르면 따라가지 않는다
    d._set_day(date(2026, 12, 2))
    assert d.rule().days == [0, 4]
    d._kind.setCurrentIndex(d._kind.findData(rm.MONTHLY))
    assert d._month_row.isVisibleTo(d) and not d._day_row.isVisibleTo(d)
    assert d._by[rm.BY_NTH].text() == "첫째 수요일"
    d.deleteLater()


def test_an_old_repeat_shows_today_as_its_start(qapp):
    now = lambda: datetime(2026, 11, 25, 10, 0)  # noqa: E731
    d = reminder_dialog.ReminderDialog(None, R(kind=rm.DAILY, date="2026-10-01", time="09:00"),
                                       now=now)
    assert d.date_text() == "11월 25일 (수)"
    assert d.status_text() == "다음 알림 11월 26일 (목) 오전 9:00"
    d.deleteLater()


def _click(widget, x, y):
    for kind in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
        QApplication.sendEvent(widget, QMouseEvent(kind, QPointF(x, y), QPointF(x, y),
                                                   Qt.LeftButton, Qt.LeftButton,
                                                   Qt.NoModifier))


def _key(widget, key):
    QApplication.sendEvent(widget, QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier))


def test_the_month_panel_picks_by_mouse_and_keys(qapp):
    today = date(2026, 11, 25)
    panel = reminder_dialog.MonthPanel(date(2026, 11, 26), today, today)
    got = []
    panel.picked.connect(got.append)
    grid = panel.grid
    assert panel.title() == "2026년 11월" and grid.first_cell() == date(2026, 11, 1)
    cell = grid.CELL

    def center(d):
        i = (d - grid.first_cell()).days
        row, col = divmod(i, 7)
        return col * cell + cell / 2, grid.HEAD + row * cell + cell / 2

    _click(grid, *center(date(2026, 11, 24)))          # 지난 날은 고를 수 없다
    _click(grid, *center(date(2026, 11, 28)))
    assert got == [date(2026, 11, 28)]
    assert not panel._prev.isEnabled()                   # 이번 달 앞으로는 가지 않는다
    panel._next.click()
    assert panel.title() == "2026년 12월" and panel._prev.isEnabled()
    _key(grid, Qt.Key_Down)                              # 12월 5일
    _key(grid, Qt.Key_Return)
    assert got[-1] == date(2026, 12, 5)
    _key(grid, Qt.Key_PageDown)
    _key(grid, Qt.Key_Enter)
    assert got[-1] == date(2027, 1, 5) and panel.title() == "2027년 1월"
    panel.deleteLater()


def test_the_time_panel_has_three_columns(qapp):
    panel = reminder_dialog.TimePanel(21, 7)
    got, done = [], []
    panel.picked.connect(lambda h, m: got.append((h, m)))
    panel.done.connect(lambda: done.append(True))
    assert panel.value() == (21, 7)
    assert panel._cols[0] is panel.ampm                  # 한국어는 오전/오후가 먼저다
    assert panel.minute.item(panel.minute.currentRow()).text() == "07"
    panel.ampm.setCurrentRow(0)
    assert got[-1] == (9, 7)
    panel.hour.setCurrentRow(0)                          # 12 → 오전 0시
    assert got[-1] == (0, 7)
    _key(panel.minute, Qt.Key_Return)
    assert done == [True]
    panel.deleteLater()
    i18n.set_language("en")
    try:
        panel = reminder_dialog.TimePanel(9, 30)
        assert panel._cols[-1] is panel.ampm             # 영어는 AM/PM 이 뒤다
        panel.deleteLater()
    finally:
        i18n.set_language("ko")


def test_the_reset_dialog_only_repeats(qapp):
    d = reminder_dialog.ReminderDialog(None, None, mode=reminder_dialog.RESET,
                                       now=lambda: datetime(2026, 11, 25, 10, 0))
    assert d._kind.findData(rm.ONCE) < 0 and not d._quick
    assert d.status_text() == "다음 해제 11월 26일 (목) 오전 8:30"
    assert not d._lead.isVisibleTo(d) and not d._clear.isVisibleTo(d)
    assert d.rule().kind == rm.DAILY and d.rule().lead == 0
    d.deleteLater()


# ── 알림 창 ──

def test_action_toast_buttons_menus_and_staying(qapp, monkeypatch):
    monkeypatch.setattr(toast_mod, "user_is_busy", lambda: False)
    manager = toast_mod.ToastManager(None, None)
    got = []
    t = manager.show_actions("주간 회의 메모", "매주 월, 목 오전 9:00",
                             [("10분 뒤", "snooze10", False), ("열기", "open", False),
                              ("완료", "done", True)],
                             menus={"snooze10": [("1시간 뒤", "snooze60")]})
    assert t is not None and t.persistent and t.sub_text() == "매주 월, 목 오전 9:00"
    t.action.connect(got.append)
    buttons = t.buttons()
    assert list(buttons) == ["snooze10", "open", "done"]
    assert buttons["snooze10"].menu() is not None
    # 화살표는 글 바로 옆에 있고, 글과 화살표는 한 덩이로 가운데 있다.
    split = buttons["snooze10"]
    t.show()
    qapp.processEvents()
    arrow = split.arrow_rect()
    text_w = split.fontMetrics().horizontalAdvance(split.text())
    left = arrow.left() - split.GAP - text_w
    assert abs(left - (split.width() - arrow.right() - 1)) <= 1
    assert split.in_menu_zone(arrow.center().x()) and not split.in_menu_zone(left + 2)
    assert not t._timer.isActive()                  # 스스로 사라지지 않는다
    # 단추들은 같은 너비로 카드 폭을 채운다.
    t.adjustSize()
    widths = {b.sizeHint().width() > 0 for b in buttons.values()}
    assert widths == {True}
    buttons["snooze10"].menu().actions()[0].trigger()
    assert got == ["snooze60"]
    manager.close_all()
    qapp.processEvents()


def test_a_busy_screen_gets_no_reminder_toast(qapp, monkeypatch):
    monkeypatch.setattr(toast_mod, "user_is_busy", lambda: True)
    manager = toast_mod.ToastManager(None, None)
    assert manager.show_actions("메모", "", [("완료", "done", True)]) is None


# ── 이 PC 에 적기 ──

def test_schedules_are_kept_outside_the_note_entries(tmp_path):
    path = tmp_path / "local_state.json"
    store = LocalStateStore(path)
    store.state.set_note_state("a", x=1, y=2)
    store.state.reminders["a"] = {"rule": R(kind=rm.DAILY, time="09:00").to_dict(),
                                  "set_at": 1000, "fired": 0}
    store.state.check_resets["a"] = {"rule": R(kind=rm.WEEKDAYS, time="08:30").to_dict(),
                                     "set_at": 1000}
    store.state.reminders["gone"] = {"rule": R(kind=rm.DAILY, time="09:00").to_dict()}
    store.state.reminders["bad"] = {"rule": {"kind": "weekly", "time": "09:00", "days": []}}
    store.state.set_note_state("bad", x=1, y=2)
    store.save()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert set(raw["notes"]["a"]) <= {"x", "y", "hidden", "w", "h"}
    again = LocalStateStore(path).state
    # 모르는 메모의 예약과 틀린 규칙은 버린다. 0 인 때는 적지 않는다.
    assert set(again.reminders) == {"a"} and set(again.check_resets) == {"a"}
    assert again.reminders["a"] == {"rule": R(kind=rm.DAILY, time="09:00").to_dict(),
                                    "set_at": 1000}


# ── 메모 창 ──

def _checklist_html(qapp, done=(0, 2)) -> str:
    w = StickyNoteWidget({"id": "tmp", "version": 1, "color": "#FFE066",
                          "width": 300, "height": 260, "content": ""},
                         ME, settings=AppSettings())
    w.apply_local_state(200, 160)
    w.show()
    qapp.processEvents()
    w.enter_edit_mode()
    w._text.setPlainText("개점 준비\n불 켜기\n금고 확인\n바닥 청소")
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
    html = w._current_content()
    w._edit_mode = False
    w.close()
    return html


def _note(qapp, content, note_id="c1"):
    w = StickyNoteWidget({"id": note_id, "version": 3, "color": "#FFE066",
                          "width": 300, "height": 260, "content": content},
                         ME, settings=AppSettings())
    w.apply_local_state(200, 160)
    w.show()
    qapp.processEvents()
    return w


def test_unchecking_takes_the_lock_changes_only_checks_and_lets_go(qapp):
    w = _note(qapp, _checklist_html(qapp))
    assert w.todo_progress() == "2/3"
    asked, commits, released = [], [], []
    w.requestLock.connect(asked.append)
    w.commitEdit.connect(lambda *a: commits.append(a))
    w.releaseLock.connect(released.append)
    assert w.uncheck_all()
    assert asked == ["c1"] and not commits
    w.enter_edit_mode()                     # 공유 PC 가 잠금을 줬다
    assert len(commits) == 1 and released == ["c1"]
    nid, content, _color, width, height, version = commits[0]
    assert (nid, version, width, height) == ("c1", 3, 300, 260)
    assert w.todo_progress() == "0/3" and not w.is_editing()
    assert "금고 확인" in content and "바닥 청소" in content
    # 더 해제할 것이 없으면 잠금을 청하지 않는다.
    assert not w.uncheck_all() and asked == ["c1"]
    w.close()


def test_unchecking_waits_while_someone_else_edits(qapp):
    w = _note(qapp, _checklist_html(qapp), note_id="c2")
    w.set_lock_holder(OTHER)
    asked = []
    w.requestLock.connect(asked.append)
    assert not w.uncheck_all() and asked == []
    w.set_lock_holder(None)
    assert w.uncheck_all() and asked == ["c2"]
    w.show_lock_denied(OTHER)               # 그 사이 다른 사람이 잡았다
    assert not w._pending_uncheck
    w.close()


def test_a_folded_note_stays_folded_while_unchecking(qapp):
    w = _note(qapp, _checklist_html(qapp), note_id="c3")
    w._settings.fold_animation = False
    w.fold()
    qapp.processEvents()
    commits = []
    w.commitEdit.connect(lambda *a: commits.append(a))
    assert w.uncheck_all()
    w.enter_edit_mode()
    assert w.is_folded() and not w.is_editing()
    assert commits and (commits[0][3], commits[0][4]) == (300, 260)   # 펼친 크기를 보낸다
    w.close()


def test_the_handle_bar_shows_the_next_times_and_shrinks_to_icons(qapp):
    w = _note(qapp, "", note_id="c4")
    opened = []
    w.reminderRequested.connect(opened.append)
    now = datetime(2026, 11, 25, 10, 0)
    w.set_schedule(reminder_dialog.when_forms(datetime(2026, 11, 25, 15, 0), now),
                   "11월 25일 (수) 오후 3:00",
                   reminder_dialog.when_forms(datetime(2026, 11, 26, 8, 30), now), "체크 해제")
    assert w.schedule_texts() == ("오늘 오후 3:00", "내일 오전 8:30")
    w._remind_chip.click()
    assert opened == ["c4"]
    # 넓은 데서 좁혀 가면 체크 해제 쪽부터 짧은 꼴로, 그다음 알림 쪽을 줄인다
    # (글자 폭은 환경마다 달라 훑어 본다). 꼴은 셋이고 3 은 그림만, 4 는 감춤이다.
    # 둘 다 가장 짧은 꼴이 되면 더 보기 메뉴에도 있는 기록, 접기 단추를 먼저 감추고, 그다음
    # 그림만으로 줄인다. 단추는 찌그러지지 않는다.
    buttons = (w._color_btn, w._history_btn, w._fold_btn, w._more_btn, w._close_btn)
    seen = []
    shown = []
    for width in range(1000, 99, -4):
        w.resize(width, 200)
        qapp.processEvents()
        state = (*w.schedule_levels(), w._history_btn.isHidden(), w._fold_btn.isHidden())
        if not seen or seen[-1] != state:
            seen.append(state)
            shown.append((w._remind_chip.shown_text(), w._reset_chip.shown_text()))
        assert w._close_btn.geometry().right() <= w._handle.width()
        for b in buttons:
            assert b.isHidden() or b.width() >= b.sizeHint().width()
        assert not w._more_btn.isHidden() and not w._close_btn.isHidden()
    assert seen[0] == (0, 0, False, False)
    assert all(x <= y for a, b in zip(seen, seen[1:]) for x, y in zip(a, b))
    assert all(b >= 2 for a, b, *_ in seen if a > 0)     # 알림 쪽은 체크 해제 쪽 다음
    assert all(a >= 2 and b >= 2 for a, b, h, f in seen if h)   # 단추는 가장 짧은 꼴 뒤
    assert all(h and f for a, b, h, f in seen if a >= 3 or b >= 3)   # 그림만은 단추 뒤
    assert (3, 3, True, True) in seen and seen[-1][:2] in ((3, 4), (4, 4))
    assert ("오늘 오후 3:00", "내일") in shown and ("15:00", "내일") in shown
    w.resize(1000, 200)
    qapp.processEvents()
    assert w.schedule_levels() == (0, 0) and not w._history_btn.isHidden()
    # 그림과 글 사이를 띄운다.
    chip = w._remind_chip
    assert chip.width_at(0) - chip.width_at(3) > chip.fontMetrics().horizontalAdvance(
        chip.full_text()) + 4
    w.set_schedule()
    assert w.schedule_texts() == ("", "") and w._remind_chip.isHidden()
    w.resize(160, 200)                                   # 때가 없어도 단추는 찌그러지지 않는다
    qapp.processEvents()
    for b in buttons:
        assert b.isHidden() or b.width() >= b.sizeHint().width()
    w.close()


# ── 메인 창 ──

class FakeClient(ClientBase):
    def __init__(self):
        super().__init__(ME)
        self.calls: list[tuple] = []

    def acquire_lock(self, note_id):
        self.calls.append(("lock", note_id))

    def release_lock(self, note_id):
        self.calls.append(("release", note_id))

    def update_note(self, note_id, content, color, width, height, expected_version):
        self.calls.append(("update", note_id, content))

    def set_reminder(self, note_id, reminder, done=False):
        self.calls.append(("set_reminder", note_id, reminder, done))


class Clock:
    def __init__(self, at: datetime):
        self.at = at

    def __call__(self) -> datetime:
        return self.at


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(StickyNoteWidget, "bring_to_front",
                        lambda self, *, start_editing=False: None)
    monkeypatch.setattr(toast_mod, "user_is_busy", lambda: False)
    client = FakeClient()
    win = MainWindow(client=client, local_state=LocalStateStore(tmp_path / "ls.json"),
                     role="host", settings=AppSettings(fold_animation=False))
    clock = Clock(datetime(2026, 11, 25, 8, 59, 30))
    win._now = clock
    win.test_clock = clock
    yield win
    win._force_quit = True
    win._toasts.close_all()
    win._teardown_stickies()
    win.close()
    win.deleteLater()
    qapp.processEvents()


def _welcome(win, *notes, reminders=None):
    win._client.connectionChanged.emit(True)
    data = {"users": [ME], "host": ME, "locks": {}, "heartbeat_ms": 3000,
            "trash_days": 30, "folders": [], "notes": list(notes)}
    if reminders is not None:
        data["reminders"] = reminders       # 공유 알림을 아는 공유 PC
    win._client._dispatch("welcome", data)
    QApplication.processEvents()


def _plain_note(nid="n1", content="주간 회의 메모", updated_at=0):
    return {"id": nid, "version": 1, "color": "#FFE066", "content": content,
            "width": 240, "height": 180, "created_by": ME, "updated_by": ME,
            "updated_at": updated_at}


def _toasts(win):
    return [t for t in win._toasts._toasts if not t._closing]


def test_saving_a_reminder_shows_it_on_the_note(window, monkeypatch):
    _welcome(window, _plain_note())
    rule = R(kind=rm.WEEKLY, date="2026-11-25", time="09:00", days=[0, 3])
    monkeypatch.setattr(reminder_dialog, "ask_reminder",
                        lambda parent, r, mode, **kw: ("save", rule, None))
    window._stickies["n1"].reminderRequested.emit("n1")
    entry = window._local.state.reminders["n1"]
    assert entry["rule"] == rule.to_dict() and entry["set_at"] > 0
    assert window._stickies["n1"].schedule_texts()[0] == "내일 오전 9:00"
    saved = json.loads(window._local.path.read_text(encoding="utf-8"))
    assert saved["reminders"]["n1"]["rule"]["days"] == [0, 3]
    monkeypatch.setattr(reminder_dialog, "ask_reminder",
                        lambda parent, r, mode, **kw: ("clear", None, None))
    window._stickies["n1"].reminderRequested.emit("n1")
    assert "n1" not in window._local.state.reminders
    assert window._stickies["n1"].schedule_texts() == ("", "")


def test_a_due_reminder_rings_and_can_be_snoozed(window):
    _welcome(window, _plain_note())
    st = window._local.state
    st.reminders["n1"] = {"rule": R(kind=rm.ONCE, date="2026-11-25", time="09:00").to_dict(),
                          "set_at": rm.to_ms(datetime(2026, 11, 25, 8, 0))}
    window._check_schedules()
    assert _toasts(window) == []
    window.test_clock.at = datetime(2026, 11, 25, 9, 0, 0, 300_000)
    window._check_schedules()
    shown = _toasts(window)
    assert len(shown) == 1 and shown[0].text() == "주간 회의 메모"
    assert shown[0].sub_text() == "오전 9:00"
    assert "n1" not in st.reminders             # 한 번 알림은 울리면 지운다
    shown[0].buttons()["snooze10"].click()
    assert st.reminders["n1"]["snooze"] == rm.to_ms(datetime(2026, 11, 25, 9, 10))
    assert window._stickies["n1"].schedule_texts()[0] == "오늘 오전 9:10"
    window.test_clock.at = datetime(2026, 11, 25, 9, 10, 0, 300_000)
    window._check_schedules()
    again = _toasts(window)
    assert len(again) == 1
    again[0].buttons()["done"].click()
    assert "n1" not in st.reminders


def test_ringing_plays_a_sound_and_shakes_a_folded_note(window, monkeypatch):
    from app.ui import main_window as mw
    _welcome(window, _plain_note())
    sounds, shakes = [], []
    monkeypatch.setattr(mw, "play_reminder_sound", lambda: sounds.append(1))
    monkeypatch.setattr(StickyNoteWidget, "shake", lambda self: shakes.append(self.note_id))
    sticky = window._stickies["n1"]
    sticky.show()
    sticky.fold()
    assert sticky.is_folded()
    st = window._local.state
    st.reminders["n1"] = {"rule": R(kind=rm.ONCE, date="2026-11-25", time="09:00").to_dict(),
                          "set_at": rm.to_ms(datetime(2026, 11, 25, 8, 0))}
    window._check_schedules()
    assert sticky.has_reminder()                      # 접힌 모양에 종을 그린다
    window.test_clock.at = datetime(2026, 11, 25, 9, 0, 0, 300_000)
    window._check_schedules()
    assert sounds == [1] and shakes == ["n1"]
    window._settings.reminder_sound = False           # 설정에서 끔
    st.reminders["n1"] = {"rule": R(kind=rm.ONCE, date="2026-11-25", time="09:30").to_dict(),
                          "set_at": rm.to_ms(datetime(2026, 11, 25, 9, 1))}
    window.test_clock.at = datetime(2026, 11, 25, 9, 30, 0, 300_000)
    window._check_schedules()
    assert sounds == [1] and shakes == ["n1", "n1"]


def test_folded_shapes_draw_a_bell_for_reminded_notes(qapp):
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter
    from app.ui import note_fold as nf
    images = []
    for bell in (False, True):
        img = QImage(220, 40, QImage.Format_ARGB32)
        img.fill(0)
        p = QPainter(img)
        nf.paint_slip(p, QRectF(0, 0, 220, 40), "#FFE066", "주간 회의", bell=bell)
        p.end()
        images.append(img)
    assert images[0] != images[1]
    img = QImage(120, 120, QImage.Format_ARGB32)
    img.fill(0)
    p = QPainter(img)
    nf.paint_icon(p, QRectF(0, 0, 100, 110), "#FFE066", "주간 회의", bell=True, icon=48)
    p.end()


def test_shaking_moves_a_folded_note_and_puts_it_back(qapp, window, monkeypatch):
    from app.ui import note_fold as nf
    monkeypatch.setattr(nf, "system_animations_enabled", lambda: True)
    _welcome(window, _plain_note())
    sticky = window._stickies["n1"]
    sticky.show()
    sticky.fold()
    home = sticky.pos()
    sticky.shake()
    anim = sticky._shake_anim
    assert anim is not None
    anim.setCurrentTime(anim.duration() // 4)
    moved = sticky.pos()
    anim.setCurrentTime(anim.duration())
    qapp.processEvents()
    assert moved != home and sticky.pos() == home and sticky._shake_anim is None


def test_repeating_reminders_ring_once_per_time_with_an_early_alert(window):
    _welcome(window, _plain_note())
    st = window._local.state
    st.reminders["n1"] = {"rule": R(kind=rm.DAILY, time="09:30", lead=10).to_dict(),
                          "set_at": rm.to_ms(datetime(2026, 11, 25, 8, 0))}
    window.test_clock.at = datetime(2026, 11, 25, 9, 20, 0, 300_000)
    window._check_schedules()
    lead = _toasts(window)
    assert len(lead) == 1 and lead[0].sub_text() == "오늘 오전 9:30에 알림"
    assert not lead[0].persistent
    window._check_schedules()
    assert len(_toasts(window)) == 1             # 미리 알림은 한 번만
    window.test_clock.at = datetime(2026, 11, 25, 9, 30, 0, 300_000)
    window._check_schedules()
    window._check_schedules()
    rings = [t for t in _toasts(window) if t.persistent]
    assert len(rings) == 1 and rings[0].sub_text() == "매일 오전 9:30"
    assert st.reminders["n1"]["fired"] == rm.to_ms(datetime(2026, 11, 25, 9, 30))
    assert window._stickies["n1"].schedule_texts()[0] == "내일 오전 9:30"


def test_reminders_missed_while_off_come_together(window):
    _welcome(window, _plain_note(), _plain_note("n2", "프린터 토너 주문"))
    st = window._local.state
    set_at = rm.to_ms(datetime(2026, 11, 23, 8, 0))
    st.reminders["n1"] = {"rule": R(kind=rm.DAILY, time="09:00").to_dict(), "set_at": set_at}
    st.reminders["n2"] = {"rule": R(kind=rm.ONCE, date="2026-11-24", time="15:00").to_dict(),
                          "set_at": set_at}
    window.test_clock.at = datetime(2026, 11, 25, 10, 0)
    window._check_schedules()
    shown = _toasts(window)
    assert len(shown) == 1 and shown[0].text() == "놓친 알림 2개"
    lines = shown[0].sub_text().split("\n")
    assert lines == ["11/24 오후 3:00  프린터 토너 주문", "오늘 오전 9:00  주간 회의 메모"]
    assert "n2" not in st.reminders
    # 반복 알림은 마지막 회차만 처리했다. 다음은 내일이다.
    assert st.reminders["n1"]["fired"] == rm.to_ms(datetime(2026, 11, 25, 9, 0))


def test_a_busy_screen_holds_the_reminder_until_later(window, monkeypatch):
    _welcome(window, _plain_note())
    st = window._local.state
    st.reminders["n1"] = {"rule": R(kind=rm.DAILY, time="09:00").to_dict(),
                          "set_at": rm.to_ms(datetime(2026, 11, 25, 8, 0))}
    monkeypatch.setattr(toast_mod, "user_is_busy", lambda: True)
    window.test_clock.at = datetime(2026, 11, 25, 9, 0, 0, 300_000)
    window._check_schedules()
    assert _toasts(window) == [] and window._held_rings
    monkeypatch.setattr(toast_mod, "user_is_busy", lambda: False)
    window.test_clock.at = datetime(2026, 11, 25, 9, 1, 0, 300_000)
    window._check_schedules()
    assert len(_toasts(window)) == 1 and not window._held_rings


def test_check_reset_unchecks_at_the_time(window, qapp):
    _welcome(window, _plain_note("c1", _checklist_html(qapp),
                                 updated_at=rm.to_ms(datetime(2026, 11, 24, 18, 0))))
    st = window._local.state
    st.check_resets["c1"] = {"rule": R(kind=rm.DAILY, time="09:00").to_dict(),
                             "set_at": rm.to_ms(datetime(2026, 11, 24, 20, 0))}
    window._refresh_schedule_chips()
    assert window._stickies["c1"].schedule_texts()[1] == "오늘 오전 9:00"
    window.test_clock.at = datetime(2026, 11, 25, 9, 0, 0, 300_000)
    window._check_schedules()
    assert ("lock", "c1") in window._client.calls
    window._client.lockGranted.emit("c1")
    updates = [c for c in window._client.calls if c[0] == "update"]
    assert len(updates) == 1 and window._stickies["c1"].todo_progress() == "0/3"
    assert ("release", "c1") in window._client.calls
    assert window._stickies["c1"].schedule_texts()[1] == "내일 오전 9:00"


def test_check_reset_waits_for_the_editor_then_unchecks(window, qapp):
    _welcome(window, _plain_note("c1", _checklist_html(qapp)))
    w = window._stickies["c1"]
    w.set_lock_holder(OTHER)
    st = window._local.state
    st.check_resets["c1"] = {"rule": R(kind=rm.DAILY, time="09:00").to_dict(),
                             "set_at": rm.to_ms(datetime(2026, 11, 24, 20, 0))}
    window.test_clock.at = datetime(2026, 11, 25, 9, 0, 0, 300_000)
    window._check_schedules()
    assert ("lock", "c1") not in window._client.calls
    assert st.check_resets["c1"]["wait"] == rm.to_ms(datetime(2026, 11, 25, 9, 0))
    # 편집이 10분 뒤에 끝났다(그 사람이 고쳐 updated_at 이 그 뒤다). 그래도 해제한다.
    window._notes["c1"]["updated_at"] = rm.to_ms(datetime(2026, 11, 25, 9, 9))
    w.set_lock_holder(None)
    window.test_clock.at = datetime(2026, 11, 25, 9, 10, 0, 300_000)
    window._check_schedules()
    assert ("lock", "c1") in window._client.calls and "wait" not in st.check_resets["c1"]


def test_a_reset_missed_while_off_is_skipped_if_someone_already_used_the_list(window, qapp):
    html = _checklist_html(qapp)
    _welcome(window, _plain_note("c1", html, updated_at=rm.to_ms(datetime(2026, 11, 25, 9, 20))),
             _plain_note("c2", html, updated_at=rm.to_ms(datetime(2026, 11, 24, 18, 0))))
    st = window._local.state
    for nid in ("c1", "c2"):
        st.check_resets[nid] = {"rule": R(kind=rm.DAILY, time="09:00").to_dict(),
                                "set_at": rm.to_ms(datetime(2026, 11, 24, 20, 0))}
    window.test_clock.at = datetime(2026, 11, 25, 10, 0)       # 켜 보니 지나 있다
    window._check_schedules()
    locks = [c for c in window._client.calls if c[0] == "lock"]
    assert locks == [("lock", "c2")]
    assert st.check_resets["c1"]["fired"] == rm.to_ms(datetime(2026, 11, 25, 9, 0))


def test_the_more_menu_offers_check_reset_only_for_todo_notes(window, qapp, monkeypatch):
    from app.ui import edit_menu
    _welcome(window, _plain_note("p1", "그냥 메모"), _plain_note("c1", _checklist_html(qapp)))
    seen = {}

    def grab(menu, pos):
        seen.setdefault("menus", []).append([a.text() for a in menu.actions()])

    monkeypatch.setattr(edit_menu, "run_menu", grab)
    window._stickies["p1"]._show_more_menu()
    window._stickies["c1"]._show_more_menu()
    plain, todo = seen["menus"]
    assert "알림" in plain and "체크 해제 예약" not in plain
    assert "알림" in todo and "체크 해제 예약" in todo


# ── 받는 사람(모두, 나만, 참여자 선택) ──

def _dialog(**kw):
    return reminder_dialog.ReminderDialog(
        None, kw.pop("rule", None), now=lambda: datetime(2026, 11, 25, 10, 0),
        me=ME, people=[ME, OTHER, "민수"], **kw)


def test_the_dialog_sends_to_everyone_by_default_and_can_pick_people(qapp):
    d = _dialog(targets=[], share=reminder_dialog.SHARE_OK)
    assert d._to_box.isVisibleTo(d) and d._to.currentText() == "모두"
    assert d.targets() == [] and not d._people_box.isVisibleTo(d)
    d._to.setCurrentIndex(d._to.findData(reminder_dialog.TO_PICK))
    assert d._people_box.isVisibleTo(d) and d.people() == [ME, OTHER, "민수"]
    assert d.status_text() == "받을 참여자를 선택하세요" and not d._save.isEnabled()
    d._picks[OTHER].setChecked(True)
    assert d.targets() == [OTHER] and d._save.isEnabled()
    d._picks[OTHER].setChecked(False)
    d._picks[ME].setChecked(True)
    assert d.targets() is None                      # 나만 고르면 이 PC 에만 적는다
    d._to.setCurrentIndex(d._to.findData(reminder_dialog.TO_ME))
    assert d.targets() is None and not d._people_box.isVisibleTo(d)
    d.deleteLater()
    # 고른 사람을 그대로 보이고, 목록에 없는 사람도 더한다.
    d = _dialog(targets=["다른 사람"], share=reminder_dialog.SHARE_OK,
                rule=R(kind=rm.DAILY, time="09:00"))
    assert d._to.currentData() == reminder_dialog.TO_PICK
    assert d.targets() == ["다른 사람"] and d.people()[-1] == "다른 사람"
    d.deleteLater()


def test_the_dialog_keeps_to_this_pc_when_sharing_is_not_possible(qapp):
    d = _dialog(share=reminder_dialog.SHARE_OLD)
    model = d._to.model()
    assert d._to_box.isVisibleTo(d) and d.targets() is None
    assert not model.item(d._to.findData(reminder_dialog.TO_ALL)).isEnabled()
    assert d._to_hint.text() == "공유 PC를 업데이트하면 다른 사람에게도 보낼 수 있습니다"
    d.deleteLater()
    d = _dialog(share=reminder_dialog.SHARE_OFFLINE)
    assert d._to_hint.text() == "연결되면 다른 사람에게도 보낼 수 있습니다"
    d.deleteLater()
    # 끊긴 동안 공유 알림은 바꿀 수 없다.
    d = _dialog(targets=[OTHER], share=reminder_dialog.SHARE_OFFLINE,
                rule=R(kind=rm.DAILY, time="09:00"))
    assert not d._save.isEnabled() and not d._clear.isEnabled() and not d._to.isEnabled()
    assert d._to_hint.text() == "연결되면 바꿀 수 있습니다"
    d.deleteLater()
    for kw in ({"share": None, "targets": []},
               {"share": reminder_dialog.SHARE_OK, "targets": [], "mode": reminder_dialog.RESET}):
        d = _dialog(**kw)
        assert not d._to_box.isVisibleTo(d) and d.targets() is None
        d.deleteLater()


def _shared(rule, targets, by=OTHER, at=datetime(2026, 11, 25, 8, 0)):
    return {"rule": rule.to_dict(), "targets": targets, "set_by": by, "set_at": rm.to_ms(at)}


def test_shared_reminders_ring_only_for_their_people(window):
    once = R(kind=rm.ONCE, date="2026-11-25", time="09:00")
    _welcome(window, _plain_note("n1"), _plain_note("n2", "다른 메모"),
             reminders={"n1": _shared(once, [ME]), "n2": _shared(once, [OTHER])})
    assert window._shared_ok
    assert window._stickies["n1"].schedule_texts()[0] == "오늘 오전 9:00"
    assert window._stickies["n2"].schedule_texts() == ("", "")   # 남에게 보낸 것
    window.test_clock.at = datetime(2026, 11, 25, 9, 0, 0, 300_000)
    window._check_schedules()
    shown = _toasts(window)
    assert [t.text() for t in shown] == ["주간 회의 메모"]
    assert shown[0].sub_text() == "오전 9:00, 서연님이 보냄"
    # 한 번 알림을 끝내면 공유 PC 에 지우라고 한다(모두에게서 지워진다).
    shown[0].buttons()["done"].click()
    assert window._client.calls[-1] == ("set_reminder", "n1", None, True)
    window._client._dispatch("reminder_changed", {"note_id": "n1", "reminder": None,
                                                 "by": ME, "done": True})
    assert "n1" not in window._local.state.shared_reminders


def test_welcome_keeps_what_this_pc_already_handled(window):
    daily = R(kind=rm.DAILY, date="2026-11-01", time="09:00")
    monday = R(kind=rm.WEEKLY, date="2026-11-01", time="09:00", days=[0])
    old = datetime(2026, 11, 10, 8, 0)
    _welcome(window, _plain_note("n1"), _plain_note("n2", "월요 메모"),
             reminders={"n1": _shared(daily, [], at=old), "n2": _shared(monday, [], at=old)})
    st = window._local.state
    # 처음 받는 알림은 하루보다 오래전 회차를 놓친 것으로 치지 않는다.
    assert st.shared_reminders["n2"]["fired"] == rm.to_ms(datetime(2026, 11, 24, 8, 59, 30))
    window._check_schedules()
    assert [t.sub_text() for t in _toasts(window)] == ["11/24 오전 9:00, 서연님이 보냄"]
    window._toasts.close_all()
    QApplication.processEvents()
    fired = st.shared_reminders["n1"]["fired"]
    # 같은 알림이 다시 오면 처리한 회차를 잇고, 바뀌면 새로 센다.
    _welcome(window, _plain_note("n1"), _plain_note("n2", "월요 메모"),
             reminders={"n1": _shared(daily, [], at=old), "n2": _shared(monday, [], at=old)})
    assert st.shared_reminders["n1"]["fired"] == fired
    window._check_schedules()
    assert _toasts(window) == []
    _welcome(window, _plain_note("n1"), reminders={})
    assert st.shared_reminders == {}
    # 옛 공유 PC(공유 알림을 모름)에 붙으면 사본을 버린다.
    _welcome(window, _plain_note("n1"))
    assert not window._shared_ok and st.shared_reminders == {}


def test_the_dialog_sends_to_the_shared_pc_or_keeps_to_this_pc(window, monkeypatch):
    _welcome(window, _plain_note(), reminders={})
    rule = R(kind=rm.DAILY, date="2026-11-25", time="09:00")
    asked = []

    def answer(result):
        def ask(parent, r, mode, **kw):
            asked.append(kw)
            return result
        monkeypatch.setattr(reminder_dialog, "ask_reminder", ask)

    answer(("save", rule, []))
    window._stickies["n1"].reminderRequested.emit("n1")
    assert asked[-1]["targets"] == [] and asked[-1]["share"] == reminder_dialog.SHARE_OK
    assert asked[-1]["people"][0] == ME and asked[-1]["me"] == ME
    assert window._client.calls[-1] == ("set_reminder", "n1",
                                        {"rule": rule.to_dict(), "targets": []}, False)
    assert window._local.state.reminders == {}
    # 공유 PC 가 받아 알리면 머리 줄에 보인다.
    window._client._dispatch("reminder_changed", {
        "note_id": "n1", "reminder": _shared(rule, [], by=ME, at=datetime(2026, 11, 25, 8, 59)),
        "by": ME})
    assert window._stickies["n1"].schedule_texts()[0] == "오늘 오전 9:00"
    # 나만으로 바꾸면 공유 알림을 거두고 이 PC 에 적는다.
    answer(("save", rule, None))
    window._stickies["n1"].reminderRequested.emit("n1")
    assert asked[-1]["targets"] == []               # 지금 걸린 공유 알림을 보였다
    assert window._client.calls[-1] == ("set_reminder", "n1", None, False)
    assert window._local.state.reminders["n1"]["rule"] == rule.to_dict()


def test_who_can_be_picked_depends_on_the_note_and_the_connection(window, monkeypatch):
    private = dict(_plain_note("p1", "내 메모"), private_owner=ME)
    _welcome(window, _plain_note(), private)
    shares = []
    monkeypatch.setattr(reminder_dialog, "ask_reminder",
                        lambda parent, r, mode, **kw: (shares.append(kw["share"]),
                                                       (None, None, None))[1])
    window._stickies["n1"].reminderRequested.emit("n1")
    window._stickies["p1"].reminderRequested.emit("p1")
    window._client.connectionChanged.emit(False)
    window._stickies["n1"].reminderRequested.emit("n1")
    window._solo = True
    window._stickies["n1"].reminderRequested.emit("n1")
    assert shares == [reminder_dialog.SHARE_OLD, None, reminder_dialog.SHARE_OFFLINE, None]
