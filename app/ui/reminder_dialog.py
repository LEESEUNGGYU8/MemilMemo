"""메모 알림(리마인더)과 체크 해제 예약을 정하는 창, 그리고 때를 글로 적는 함수.

창은 구역으로 나뉜다. 울리는 때(날짜, 시각, 빨리 정하기), 주기(반복과 요일·매월의
기준, 미리 알림), 받는 사람(모두, 나만, 참여자 선택). 맨 아래 한 줄이 다음 때를
알린다. 받는 사람은 함께 쓰는 메모에만 있다. 체크 해제 예약은 빨리 정하기, 미리
알림, 받는 사람이 없고 반복만 고른다.

반복의 날짜는 시작 날짜다(그 날 전에는 울리지 않는다). 날짜와 시각 칸은 다른
펼침 칸과 같은 모양이고, 펼치면 달력과 시각 판을 띄운다."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Callable

from PySide6.QtCore import QEvent, QPoint, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from .. import i18n
from .. import reminders as rm
from . import window_frame
from . import edit_menu
from .icons import svg_icon
from .theme import refresh_style

_WIDTH = 380
REMIND, RESET = "remind", "reset"
# 받는 사람을 고를 수 있는가: 고를 수 있음, 공유 PC 가 옛 버전, 공유 PC 와 끊김.
# None 이면 구역을 감춘다(혼자 쓰기, 나만 보는 메모, 체크 해제 예약).
SHARE_OK, SHARE_OLD, SHARE_OFFLINE = "ok", "old", "offline"
TO_ALL, TO_ME, TO_PICK = "all", "me", "pick"
_RED = "#B91C1C"


# ── 글로 적기 ──

def _names(key: str) -> list[str]:
    return [x.strip() for x in i18n.t(key).split(",")]


def weekday_name(index: int) -> str:
    return _names("reminder.weekdays")[index % 7]


def format_time(h: int, m: int) -> str:
    ampm = i18n.t("reminder.am") if h < 12 else i18n.t("reminder.pm")
    return i18n.t("reminder.time_fmt", ampm=ampm, h=h % 12 or 12, mm=f"{m:02d}")


def format_date(d: date) -> str:
    return i18n.t("reminder.date_fmt", m=d.month, d=d.day, wd=weekday_name(d.weekday()),
                  mon=_names("reminder.months")[d.month - 1])


def format_day(d: date, today: date) -> str:
    """날짜 칸의 글. 올해가 아니면 해를 붙인다."""
    if d.year == today.year:
        return format_date(d)
    return i18n.t("reminder.date_year_fmt", y=d.year, m=d.month, d=d.day,
                  wd=weekday_name(d.weekday()), mon=_names("reminder.months")[d.month - 1])


def monthly_which(rule: rm.Rule) -> str:
    base = rule.base() or date.today()
    if rule.monthly == rm.BY_LAST:
        return i18n.t("reminder.by_last")
    if rule.monthly == rm.BY_NTH:
        return i18n.t("reminder.by_nth", nth=_names("reminder.nth")[min(4, rm.nth_of(base))],
                      wd=weekday_name(base.weekday()))
    return i18n.t("reminder.by_date", day=base.day)


def describe(rule: rm.Rule) -> str:
    """'매주 월, 목 오전 9:00' 같은 규칙의 요약."""
    t = format_time(*rule.hm())
    if rule.kind == rm.ONCE:
        base = rule.base()
        return i18n.t("reminder.desc_once", date=format_date(base) if base else "", time=t)
    if rule.kind == rm.WEEKLY:
        days = i18n.t("reminder.days_sep").join(weekday_name(d) for d in rule.days)
        return i18n.t("reminder.desc_weekly", days=days, time=t)
    if rule.kind == rm.MONTHLY:
        return i18n.t("reminder.desc_monthly", which=monthly_which(rule), time=t)
    if rule.kind == rm.YEARLY:
        base = rule.base() or date.today()
        return i18n.t("reminder.desc_yearly", m=base.month, d=base.day,
                      mon=_names("reminder.months")[base.month - 1], time=t)
    return i18n.t(f"reminder.desc_{rule.kind}", time=t)


def _when(at: datetime, now: datetime, time_text: str) -> str:
    """오늘, 내일, 요일, 날짜 가운데 하나에 ``time_text`` 를 붙인다."""
    days = (at.date() - now.date()).days
    if days == 0:
        text = i18n.t("reminder.today", time=time_text)
    elif days == 1:
        text = i18n.t("reminder.tomorrow", time=time_text)
    elif 1 < days < 7:
        text = i18n.t("reminder.short_wd", wd=weekday_name(at.weekday()), time=time_text)
    else:
        text = i18n.t("reminder.short_date", m=at.month, d=at.day, time=time_text)
    return " ".join(text.split())


def short_when(at: datetime, now: datetime) -> str:
    """짧은 때: '오늘 오후 3:00', '내일 오전 9:00', '금 오전 9:00', '12/10 오전 9:00'."""
    return _when(at, now, format_time(at.hour, at.minute))


def when_forms(at: datetime, now: datetime) -> list[str]:
    """메모 머리 줄에 적을 때를 긴 것부터. 좁으면 뒤의 짧은 꼴을 쓴다.

    오늘이면 '오늘 오후 3:00', '오후 3:00', '15:00'. 다른 날이면 '내일 오전 9:00',
    '내일 9:00', '내일'. 시각만 남길 때는 오전/오후 대신 24시간으로 적는다."""
    compact = f"{at.hour}:{at.minute:02d}"
    if at.date() == now.date():
        forms = [short_when(at, now), format_time(at.hour, at.minute), compact]
    else:
        forms = [short_when(at, now), _when(at, now, compact), _when(at, now, "")]
    out: list[str] = []
    for text in forms:
        if text and text not in out:
            out.append(text)
    return out


def long_when(at: datetime) -> str:
    return f"{format_date(at.date())} {format_time(at.hour, at.minute)}"


def lead_name(minutes: int) -> str:
    return i18n.t(f"reminder.lead_{minutes}")


# ── 달력과 시각 판 ──

class _MonthGrid(QWidget):
    """한 달 칸. 일요일부터 여섯 줄이다. 누르거나 Enter 로 고른다."""

    picked = Signal(object)
    monthChanged = Signal()

    CELL = 34
    HEAD = 24

    def __init__(self, selected: date, today: date, minimum: date | None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.selected = selected
        self.today = today
        self.minimum = minimum
        self.cursor = selected
        self.month = selected.replace(day=1)
        self._hover: date | None = None
        self._keys = False
        self.setFixedSize(7 * self.CELL, self.HEAD + 6 * self.CELL)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName(i18n.t("reminder.a11y_date"))

    def first_cell(self) -> date:
        return self.month - timedelta(days=(self.month.weekday() + 1) % 7)

    def day_at(self, x: float, y: float) -> date | None:
        if y < self.HEAD:
            return None
        col, row = int(x // self.CELL), int((y - self.HEAD) // self.CELL)
        if not (0 <= col < 7 and 0 <= row < 6):
            return None
        return self.first_cell() + timedelta(days=row * 7 + col)

    def allowed(self, d: date) -> bool:
        return self.minimum is None or d >= self.minimum

    def can_go_back(self) -> bool:
        return self.minimum is None or self.month > self.minimum.replace(day=1)

    def shift_month(self, step: int) -> None:
        if step < 0 and not self.can_go_back():
            return
        y, m = divmod(self.month.month - 1 + step, 12)
        self.month = date(self.month.year + y, m + 1, 1)
        self.monthChanged.emit()
        self.update()

    def pick(self, d: date) -> None:
        if not self.allowed(d):
            return
        self.selected = self.cursor = d
        self.update()
        self.picked.emit(d)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        font = QFont(self.font())
        font.setPixelSize(11)
        p.setFont(font)
        names = _names("reminder.weekdays")       # 월 … 일
        for col, wd in enumerate((6, 0, 1, 2, 3, 4, 5)):
            p.setPen(QColor(_RED if wd == 6 else "#707070"))
            p.drawText(QRectF(col * self.CELL, 0, self.CELL, self.HEAD), Qt.AlignCenter,
                       names[wd])
        font.setPixelSize(12)
        start = self.first_cell()
        for i in range(42):
            d = start + timedelta(days=i)
            row, col = divmod(i, 7)
            cell = QRectF(col * self.CELL, self.HEAD + row * self.CELL,
                          self.CELL, self.CELL).adjusted(3, 3, -3, -3)
            ok = self.allowed(d)
            chosen = d == self.selected
            if chosen:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor("#1F1F1F"))
                p.drawEllipse(cell)
            elif ok and d == self._hover:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor("#F0F0F0"))
                p.drawEllipse(cell)
            if d == self.today and not chosen:
                p.setPen(QPen(QColor("#8C8C8C"), 1))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(cell.adjusted(0.5, 0.5, -0.5, -0.5))
            if self._keys and self.hasFocus() and d == self.cursor:
                p.setPen(QPen(QColor("#2563EB"), 2))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(cell.adjusted(-1, -1, 1, 1))
            if chosen:
                ink = "#FFFFFF"
            elif not ok:
                ink = "#C8C8C8"
            elif d.month != self.month.month:
                ink = "#B0B0B0"
            elif d.weekday() == 6:
                ink = _RED
            else:
                ink = "#1F1F1F"
            font.setWeight(QFont.DemiBold if chosen or d == self.today else QFont.Normal)
            p.setFont(font)
            p.setPen(QColor(ink))
            p.drawText(cell, Qt.AlignCenter, str(d.day))

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        d = self.day_at(pos.x(), pos.y())
        if d != self._hover:
            self._hover = d
            self.update()

    def leaveEvent(self, event) -> None:
        self._hover = None
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        pos = event.position()
        d = self.day_at(pos.x(), pos.y())
        if d is not None:
            self._keys = False
            self.pick(d)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        step = {Qt.Key_Left: -1, Qt.Key_Right: 1, Qt.Key_Up: -7, Qt.Key_Down: 7}.get(key)
        if step:
            self._move(self.cursor + timedelta(days=step))
            return
        if key in (Qt.Key_PageUp, Qt.Key_PageDown):
            months = -1 if key == Qt.Key_PageUp else 1
            y, m = divmod(self.cursor.month - 1 + months, 12)
            first = date(self.cursor.year + y, m + 1, 1)
            last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
            self._move(first.replace(day=min(self.cursor.day, last.day)))
            return
        if key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.pick(self.cursor)
            return
        super().keyPressEvent(event)

    def _move(self, d: date) -> None:
        if not self.allowed(d):
            return
        self._keys = True
        self.cursor = d
        if (d.year, d.month) != (self.month.year, self.month.month):
            self.month = d.replace(day=1)
            self.monthChanged.emit()
        self.update()


def _nav_button(icon: str, tip: str) -> QToolButton:
    b = QToolButton()
    b.setIcon(svg_icon(icon, color="#3A3A3A"))
    b.setIconSize(QSize(14, 14))
    b.setFixedSize(28, 28)
    b.setToolTip(tip)
    b.setAccessibleName(tip)
    b.setCursor(Qt.PointingHandCursor)
    b.setFocusPolicy(Qt.NoFocus)
    b.setAutoRaise(True)
    b.setStyleSheet(
        "QToolButton { border: none; border-radius: 6px; background: transparent; }"
        "QToolButton:hover { background: #F0F0F0; }"
        "QToolButton:disabled { background: transparent; }")
    return b


class MonthPanel(QWidget):
    """달력 판: 위에 해와 달, 이전 달과 다음 달 단추, 아래에 한 달 칸."""

    picked = Signal(object)

    def __init__(self, selected: date, today: date, minimum: date | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 4, 8, 6)
        root.setSpacing(2)
        head = QHBoxLayout()
        head.setContentsMargins(8, 0, 0, 0)
        head.setSpacing(2)
        self._title = QLabel()
        self._title.setStyleSheet(
            "font-size: 13px; font-weight: 700; color: #1F1F1F; background: transparent;")
        self._prev = _nav_button("chevron_left", i18n.t("reminder.prev_month"))
        self._next = _nav_button("chevron_right", i18n.t("reminder.next_month"))
        head.addWidget(self._title)
        head.addStretch(1)
        head.addWidget(self._prev)
        head.addWidget(self._next)
        root.addLayout(head)
        self.grid = _MonthGrid(selected, today, minimum, self)
        root.addWidget(self.grid)
        self._prev.clicked.connect(lambda: self.grid.shift_month(-1))
        self._next.clicked.connect(lambda: self.grid.shift_month(1))
        self.grid.monthChanged.connect(self._refresh_head)
        self.grid.picked.connect(self.picked)
        self._refresh_head()

    def title(self) -> str:
        return self._title.text()

    def _refresh_head(self) -> None:
        m = self.grid.month
        self._title.setText(i18n.t("reminder.month_fmt", y=m.year, m=m.month,
                                   mon=_names("reminder.months")[m.month - 1]))
        self._prev.setEnabled(self.grid.can_go_back())

    def focus_target(self) -> QWidget:
        return self.grid


_COLUMN_QSS = (
    "QListWidget { background: transparent; border: none; padding: 0; outline: 0; }"
    "QListWidget::item { padding: 0; margin: 1px 2px; border-radius: 6px; "
    "color: #1F1F1F; background: transparent; }"
    "QListWidget::item:hover { background: #F0F0F0; }"
    "QListWidget::item:selected { background: #1F1F1F; color: #FFFFFF; }")


class TimePanel(QWidget):
    """시각 판: 오전/오후, 시, 분(5분 단위) 세 줄. 고를 때마다 ``picked``, 분을
    누르거나 Enter 면 ``done``. 화살표 ←→ 로 줄을 옮긴다."""

    picked = Signal(int, int)
    done = Signal()

    ROW = 30
    ROWS = 6

    def __init__(self, h: int, m: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._hours = [12] + list(range(1, 12))
        self._minutes = sorted(set(range(0, 60, 5)) | {m})
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 4, 4, 4)
        row.setSpacing(2)
        self.ampm = self._column([i18n.t("reminder.am"), i18n.t("reminder.pm")],
                                 0 if h < 12 else 1, 64)
        self.hour = self._column([str(x) for x in self._hours],
                                 self._hours.index(h % 12 or 12), 52)
        self.minute = self._column([f"{x:02d}" for x in self._minutes],
                                   self._minutes.index(m), 52)
        order = i18n.t("reminder.time_fmt", ampm="\x01", h="\x02", mm="\x03")
        if order.find("\x01") < order.find("\x02"):
            self._cols = [self.ampm, self.hour, self.minute]
        else:
            self._cols = [self.hour, self.minute, self.ampm]
        for col in self._cols:
            row.addWidget(col)
            col.currentRowChanged.connect(self._changed)
        self.minute.itemClicked.connect(lambda *_: self.done.emit())

    def _column(self, items: list[str], current: int, width: int) -> QListWidget:
        lw = QListWidget(self)
        lw.setStyleSheet(_COLUMN_QSS)
        lw.setFixedSize(width, self.ROW * self.ROWS + 4)
        lw.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        lw.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        lw.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        lw.setCursor(Qt.PointingHandCursor)
        for text in items:
            item = QListWidgetItem(text)
            item.setTextAlignment(Qt.AlignCenter)
            item.setSizeHint(QSize(width - 6, self.ROW))
            lw.addItem(item)
        lw.setCurrentRow(current)
        lw.installEventFilter(self)
        return lw

    def value(self) -> tuple[int, int]:
        hour = self._hours[max(0, self.hour.currentRow())] % 12
        if self.ampm.currentRow() == 1:
            hour += 12
        return hour, self._minutes[max(0, self.minute.currentRow())]

    def _changed(self, *_args) -> None:
        self.picked.emit(*self.value())

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self._center)

    def _center(self) -> None:
        for col in self._cols:
            if col.currentItem() is not None:
                col.scrollToItem(col.currentItem(), QAbstractItemView.PositionAtCenter)

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.KeyPress and obj in self._cols:
            key = event.key()
            if key in (Qt.Key_Left, Qt.Key_Right):
                j = self._cols.index(obj) + (1 if key == Qt.Key_Right else -1)
                if 0 <= j < len(self._cols):
                    self._cols[j].setFocus(Qt.TabFocusReason)
                return True
            if key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
                self.done.emit()
                return True
        return super().eventFilter(obj, event)

    def focus_target(self) -> QWidget:
        return self.hour


class _PickerCombo(QComboBox):
    """펼치면 따로 고르는 판(달력, 시각)을 띄우는 칸. 생김새는 다른 펼침 칸과 같다."""

    def __init__(self, make_panel: Callable[[QMenu], QWidget],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.addItem("")
        self._make_panel = make_panel
        self._menu: QMenu | None = None
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumContentsLength(6)
        self.setCursor(Qt.PointingHandCursor)

    def set_text(self, text: str) -> None:
        self.setItemText(0, text)

    def text(self) -> str:
        return self.itemText(0)

    def showPopup(self) -> None:
        if self._menu is not None:
            return
        menu = edit_menu.new_menu(self)
        menu.setMinimumWidth(0)
        panel = self._make_panel(menu)
        action = QWidgetAction(menu)
        action.setDefaultWidget(panel)
        menu.addAction(action)
        menu.aboutToShow.connect(
            lambda w=panel.focus_target(): QTimer.singleShot(0, w.setFocus))
        self._menu = menu
        try:
            edit_menu.run_menu(menu, self.mapToGlobal(QPoint(0, self.height() + 2)))
        finally:
            self._menu = None
            menu.deleteLater()

    def hidePopup(self) -> None:
        if self._menu is not None:
            self._menu.close()

    def wheelEvent(self, event) -> None:
        event.ignore()


# ── 창 ──

class ReminderDialog(QDialog):
    """알림이나 체크 해제 예약을 정한다. ``result_action`` 은 저장 'save', 지우기
    'clear', 그만둠 None 이고, 저장이면 ``rule()`` 이 정한 규칙이다."""

    def __init__(self, parent: QWidget | None = None, rule: rm.Rule | None = None, *,
                 mode: str = REMIND, now: Callable[[], datetime] = datetime.now,
                 targets: list[str] | None = None, people: list[str] | None = None,
                 me: str = "", share: str | None = None, fresh: bool = False) -> None:
        """``targets`` 는 받는 사람: None 이면 나만, 빈 목록이면 모두, 아니면 그
        사람들. ``people`` 은 고를 수 있는 사람(나부터). ``fresh`` 면 ``rule`` 은
        처음 거는 알림의 처음 값이다(끄기 단추가 없다)."""
        super().__init__(parent)
        self._mode = mode
        self._now = now
        self._ready = False
        self.result_action: str | None = None
        reset = mode == RESET
        self.setWindowTitle(i18n.t("reminder.reset_title" if reset else "reminder.title"))
        start = rule or self._default_rule()
        self._day = self._now().date()
        self._hm = (9, 0)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(0)
        root.setSizeConstraint(QLayout.SetFixedSize)
        root.addStrut(_WIDTH - 40)

        # ── 울리는 때 ──
        root.addWidget(self._header(i18n.t(
            "reminder.sec_when_reset" if reset else "reminder.sec_when")))
        root.addSpacing(8)
        when = QHBoxLayout()
        when.setSpacing(8)
        self._date = _PickerCombo(self._date_panel)
        self._date.setAccessibleName(i18n.t("reminder.a11y_date"))
        self._time = _PickerCombo(self._time_panel)
        self._time.setAccessibleName(i18n.t("reminder.a11y_time"))
        when.addWidget(self._date, 3)
        when.addWidget(self._time, 2)
        root.addLayout(when)
        self._quick: dict[str, QPushButton] = {}
        if not reset:
            root.addSpacing(10)
            row = QHBoxLayout()
            row.setSpacing(6)
            for key in ("10m", "60m", "morning", "monday"):
                b = self._chip(i18n.t(f"reminder.quick_{key}"))
                b.clicked.connect(lambda _c=False, k=key: self._take_quick(k))
                self._quick[key] = b
                row.addWidget(b)
            row.addStretch(1)
            root.addLayout(row)

        root.addSpacing(16)
        root.addWidget(self._line())
        root.addSpacing(14)

        # ── 주기: 반복, 미리 알림 ──
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        grid.addWidget(self._header(i18n.t("reminder.repeat")), 0, 0)
        self._kind = QComboBox()
        kinds = (rm.DAILY, rm.WEEKDAYS, rm.WEEKLY, rm.MONTHLY, rm.YEARLY)
        for kind in kinds if reset else (rm.ONCE, *kinds):
            self._kind.addItem(i18n.t(f"reminder.kind_{kind}"), userData=kind)
        grid.addWidget(self._kind, 1, 0)
        self._lead_label = self._header(i18n.t("reminder.lead"))
        self._lead = QComboBox()
        for minutes in rm.LEADS:
            self._lead.addItem(lead_name(minutes), userData=minutes)
        grid.addWidget(self._lead_label, 0, 1)
        grid.addWidget(self._lead, 1, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        root.addLayout(grid)
        self._lead_label.setVisible(not reset)
        self._lead.setVisible(not reset)

        # 요일(매주)
        self._day_row = QWidget()
        days = QHBoxLayout(self._day_row)
        days.setContentsMargins(0, 10, 0, 0)
        days.setSpacing(4)
        self._days: list[QPushButton] = []
        for i in range(7):
            b = self._chip(weekday_name(i), checkable=True)
            # 한 글자 요일은 36 폭. 영어(Mon)처럼 긴 이름은 여백을 줄여 글 폭에 맞춘다
            # (36 에 여백 10 이면 글자가 잘린다).
            text_w = b.fontMetrics().horizontalAdvance(b.text())
            if text_w + 20 > 36:
                b.setStyleSheet(b.styleSheet().replace("padding: 0 10px", "padding: 0 4px"))
                b.setFixedWidth(text_w + 16)
            else:
                b.setFixedWidth(36)
            self._days.append(b)
            days.addWidget(b)
        days.addStretch(1)
        root.addWidget(self._day_row)

        # 매월의 기준
        self._month_row = QWidget()
        month = QHBoxLayout(self._month_row)
        month.setContentsMargins(0, 10, 0, 0)
        month.setSpacing(12)
        self._by: dict[str, QRadioButton] = {}
        for key in (rm.BY_DATE, rm.BY_NTH, rm.BY_LAST):
            r = QRadioButton()
            self._by[key] = r
            month.addWidget(r)
        month.addStretch(1)
        root.addWidget(self._month_row)

        # ── 받는 사람(함께 쓰는 메모의 알림만) ──
        self._share = None if reset else share
        self._me = me
        self._people = list(people or ([me] if me else []))
        for name in targets or []:
            if name not in self._people:
                self._people.append(name)
        # 끊긴 동안 공유 알림은 바꿀 수 없다(공유 PC 가 가진 것이다).
        self._shared_locked = targets is not None and self._share not in (None, SHARE_OK)
        self._to_box = QWidget()
        to = QVBoxLayout(self._to_box)
        to.setContentsMargins(0, 0, 0, 0)
        to.setSpacing(0)
        to.addSpacing(16)
        to.addWidget(self._line())
        to.addSpacing(14)
        to.addWidget(self._header(i18n.t("reminder.sec_to")))
        to.addSpacing(8)
        to_grid = QGridLayout()
        to_grid.setContentsMargins(0, 0, 0, 0)
        to_grid.setHorizontalSpacing(8)
        self._to = QComboBox()
        for key in (TO_ALL, TO_ME, TO_PICK):
            self._to.addItem(i18n.t(f"reminder.to_{key}"), userData=key)
        to_grid.addWidget(self._to, 0, 0)
        to_grid.setColumnStretch(0, 1)
        to_grid.setColumnStretch(1, 1)
        to.addLayout(to_grid)
        self._to_hint = QLabel()
        self._to_hint.setWordWrap(True)
        self._to_hint.setStyleSheet(
            "color: #6B6B6B; font-size: 12px; background: transparent; padding-top: 8px;")
        to.addWidget(self._to_hint)
        self._people_box = QWidget()
        people_rows = QVBoxLayout(self._people_box)
        people_rows.setContentsMargins(0, 10, 0, 0)
        people_rows.setSpacing(6)
        self._picks: dict[str, QPushButton] = {}
        line: QHBoxLayout | None = None
        used, room = 0, _WIDTH - 40
        for name in self._people:
            b = self._chip(name, checkable=True)
            b.setMaximumWidth(room)
            width = b.sizeHint().width()
            if line is None or used + width > room:
                if line is not None:
                    line.addStretch(1)
                line = QHBoxLayout()
                line.setSpacing(6)
                people_rows.addLayout(line)
                used = 0
            line.addWidget(b)
            used += width + 6
            self._picks[name] = b
        if line is not None:
            line.addStretch(1)
        to.addWidget(self._people_box)
        root.addWidget(self._to_box)
        self._to_box.setVisible(self._share is not None)
        if self._share in (SHARE_OLD, SHARE_OFFLINE):
            model = self._to.model()
            for key in (TO_ALL, TO_PICK):
                model.item(self._to.findData(key)).setEnabled(self._shared_locked)
            hint = ("reminder.to_hint_locked" if self._shared_locked
                    else "reminder.to_hint_old" if self._share == SHARE_OLD
                    else "reminder.to_hint_offline")
            self._to_hint.setText(i18n.t(hint))
        self._to_hint.setVisible(bool(self._to_hint.text()))
        if self._shared_locked:
            self._to.setEnabled(False)
            self._people_box.setEnabled(False)

        root.addSpacing(16)
        root.addWidget(self._line())

        # 다음 때(반복), 지난 시각, 요일을 고르라는 말
        self._status_box = QWidget()
        status = QVBoxLayout(self._status_box)
        status.setContentsMargins(0, 12, 0, 0)
        self._status = QLabel()
        self._status.setWordWrap(True)
        status.addWidget(self._status)
        root.addWidget(self._status_box)
        root.addSpacing(14)

        # 단추
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self._clear = QPushButton(i18n.t(
            "reminder.reset_btn_clear" if reset else "reminder.btn_clear"))
        self._clear.setProperty("secondary", True)
        refresh_style(self._clear)
        self._clear.setAutoDefault(False)
        self._clear.setVisible(rule is not None and not fresh)
        self._clear.clicked.connect(lambda: self._finish("clear"))
        buttons.addWidget(self._clear)
        buttons.addStretch(1)
        self._save = QPushButton(i18n.t("reminder.btn_save"))
        self._save.setMinimumWidth(96)
        self._save.setDefault(True)
        self._save.clicked.connect(lambda: self._finish("save"))
        cancel = QPushButton(i18n.t("common.btn_cancel"))
        cancel.setProperty("secondary", True)
        refresh_style(cancel)
        cancel.setMinimumWidth(96)
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(self._save)
        buttons.addWidget(cancel)
        root.addLayout(buttons)

        self._load(start)
        self._load_targets(targets)
        self._to.currentIndexChanged.connect(self._refresh)
        for b in self._picks.values():
            b.toggled.connect(self._refresh)
        self._kind.currentIndexChanged.connect(self._on_kind)
        self._lead.currentIndexChanged.connect(self._refresh)
        for b in self._days:
            b.toggled.connect(self._refresh)
        for r in self._by.values():
            r.toggled.connect(self._refresh)
        self._ready = True
        self._refresh()
        window_frame.install(self, window_frame.DIALOG)

    # ── 안쪽 ──

    @staticmethod
    def _header(text: str) -> QLabel:
        label = QLabel(text)
        label.setProperty("role", "sectionHeader")
        refresh_style(label)
        return label

    @staticmethod
    def _line() -> QFrame:
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background: #EFEFEF; border: none;")
        return line

    def _chip(self, text: str, checkable: bool = False) -> QPushButton:
        b = QPushButton(text)
        b.setCheckable(checkable)
        b.setAutoDefault(False)
        b.setCursor(Qt.PointingHandCursor)
        b.setProperty("secondary", True)
        b.setStyleSheet(
            "QPushButton { min-height: 26px; padding: 0 10px; border-radius: 13px; }"
            "QPushButton:checked { background: #1F1F1F; color: #FFFFFF; border-color: #1F1F1F; }")
        refresh_style(b)
        return b

    def _default_rule(self) -> rm.Rule:
        now = self._now()
        if self._mode == RESET:
            return rm.Rule(rm.DAILY, now.date().isoformat(), "08:30")
        t = now + timedelta(days=1)
        return rm.Rule(rm.ONCE, t.date().isoformat(), "09:00")

    def _load(self, rule: rm.Rule) -> None:
        kind = rule.kind if self._kind.findData(rule.kind) >= 0 else rm.DAILY
        self._kind.setCurrentIndex(self._kind.findData(kind))
        today = self._now().date()
        base = rule.base() or today
        if kind in (rm.DAILY, rm.WEEKDAYS, rm.WEEKLY) and base < today:
            base = today            # 이미 시작한 반복은 오늘부터 보여 준다
        self._set_day(base, follow=False)
        self._set_hm(*rule.hm())
        for i, b in enumerate(self._days):
            b.setChecked(i in rule.days)
        if kind == rm.WEEKLY and not rule.days:
            self._days[base.weekday()].setChecked(True)
        self._by.get(rule.monthly, self._by[rm.BY_DATE]).setChecked(True)
        self._lead.setCurrentIndex(max(0, self._lead.findData(rule.lead)))

    def _load_targets(self, targets: list[str] | None) -> None:
        if self._share is None or targets is None:
            key = TO_ME
        elif not targets:
            key = TO_ALL
        else:
            key = TO_PICK
            for name in targets:
                self._picks[name].setChecked(True)
        self._to.setCurrentIndex(self._to.findData(key))

    def targets(self) -> list[str] | None:
        """받는 사람: None 이면 나만(이 PC 에만 적는다), 빈 목록이면 모두."""
        if self._share is None:
            return None
        key = self._to.currentData()
        if key == TO_ME:
            return None
        if key == TO_ALL:
            return []
        names = [name for name, b in self._picks.items() if b.isChecked()]
        return None if names == [self._me] else names

    def people(self) -> list[str]:
        return list(self._picks)

    def rule(self) -> rm.Rule:
        kind = self._kind.currentData()
        h, m = self._hm
        days = [i for i, b in enumerate(self._days) if b.isChecked()] if kind == rm.WEEKLY else []
        monthly = next((k for k, r in self._by.items() if r.isChecked()), rm.BY_DATE)
        lead = int(self._lead.currentData() or 0) if self._mode != RESET else 0
        return rm.Rule(kind, self._day.isoformat(), f"{h:02d}:{m:02d}", days, monthly, lead)

    def date_text(self) -> str:
        return self._date.text()

    def time_text(self) -> str:
        return self._time.text()

    def _set_day(self, d: date, follow: bool = True) -> None:
        """날짜를 바꾼다. 매주에서 그 날의 요일 하나만 골라 두었으면 요일도 따라간다."""
        old, self._day = self._day, d
        self._date.set_text(format_day(d, self._now().date()))
        if follow and self._kind.currentData() == rm.WEEKLY and old.weekday() != d.weekday():
            checked = [i for i, b in enumerate(self._days) if b.isChecked()]
            if checked == [old.weekday()]:
                self._days[old.weekday()].setChecked(False)
                self._days[d.weekday()].setChecked(True)
        self._refresh()

    def _set_hm(self, h: int, m: int) -> None:
        self._hm = (h, m)
        self._time.set_text(format_time(h, m))
        self._refresh()

    def _date_panel(self, menu: QMenu) -> QWidget:
        today = self._now().date()
        panel = MonthPanel(self._day, today, today, menu)
        panel.picked.connect(lambda d: (self._set_day(d), menu.close()))
        return panel

    def _time_panel(self, menu: QMenu) -> QWidget:
        panel = TimePanel(*self._hm, parent=menu)
        panel.picked.connect(self._set_hm)
        panel.done.connect(menu.close)
        return panel

    def _take_quick(self, key: str) -> None:
        """빨리 정하기는 울리는 때만 바꾼다(반복은 그대로)."""
        q = rm.quick(self._now(), key)
        self._set_day(q.base() or self._now().date())
        self._set_hm(*q.hm())

    def _on_kind(self, *_args) -> None:
        if self._kind.currentData() == rm.WEEKLY and not any(b.isChecked() for b in self._days):
            self._days[self._day.weekday()].setChecked(True)
        self._refresh()

    def _refresh(self, *_args) -> None:
        if not self._ready:
            return
        kind = self._kind.currentData()
        self._day_row.setVisible(kind == rm.WEEKLY)
        self._month_row.setVisible(kind == rm.MONTHLY)
        rule = self.rule()
        for key, radio in self._by.items():
            probe = rm.Rule(rm.MONTHLY, rule.date, rule.time, monthly=key)
            radio.setText(monthly_which(probe))
        picking = self._share is not None and self._to.currentData() == TO_PICK
        self._people_box.setVisible(picking)
        nobody = picking and not any(b.isChecked() for b in self._picks.values())
        valid = not (kind == rm.WEEKLY and not rule.days)
        nxt = rm.next_time(rule, self._now()) if valid else None
        error = False
        if not valid:
            text, error = i18n.t("reminder.pick_days"), True
        elif nxt is None:
            text, error = i18n.t("reminder.past"), True
        elif nobody:
            text, error = i18n.t("reminder.pick_people"), True
        elif kind == rm.ONCE:
            text = ""           # 날짜와 시각 칸이 곧 알릴 때다
        else:
            key = "reminder.next_reset" if self._mode == RESET else "reminder.next"
            text = i18n.t(key, when=long_when(nxt))
        self._status.setText(text)
        self._status.setStyleSheet(
            f"color: {_RED if error else '#6B6B6B'}; font-size: 12px; background: transparent;")
        self._status_box.setVisible(bool(text))
        self._save.setEnabled(valid and nxt is not None and not nobody
                              and not self._shared_locked)
        self._clear.setEnabled(not self._shared_locked)

    def status_text(self) -> str:
        return self._status.text() if not self._status_box.isHidden() else ""

    def _finish(self, action: str) -> None:
        self.result_action = action
        self.accept()


def ask_reminder(parent: QWidget | None, rule: rm.Rule | None, mode: str = REMIND,
                 **options) -> tuple[str | None, rm.Rule | None, list[str] | None]:
    """창을 띄워 묻는다. ('save', 규칙, 받는 사람), ('clear', None, None), 그만두면
    (None, None, None). ``options`` 는 ReminderDialog 의 targets, people, me, share.
    시험에서는 이 함수를 바꿔 끼운다."""
    dialog = ReminderDialog(parent, rule, mode=mode, **options)
    try:
        if dialog.exec() != QDialog.Accepted:
            return None, None, None
        if dialog.result_action == "clear":
            return "clear", None, None
        return "save", dialog.rule(), dialog.targets()
    finally:
        dialog.deleteLater()


__all__ = ["MonthPanel", "REMIND", "RESET", "SHARE_OFFLINE", "SHARE_OK", "SHARE_OLD",
           "ReminderDialog", "TimePanel", "ask_reminder",
           "describe", "format_date", "format_day", "format_time", "long_when", "short_when",
           "when_forms"]
