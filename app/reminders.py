"""리마인더: 메모를 정한 때에 알려 준다.

규칙은 데이터만이다(한 번, 매일, 평일, 매주, 매월, 매년, 시각, 미리 알림). 다음
때를 구하는 계산(next_time)은 화면 코드가 없는 순수 함수다. 1.1 에서는 '나만'
(이 PC 에만 적고 이 PC 에서만 알린다)이고, 같은 규칙으로 '체크 해제 예약'도 쓴다.

그 날이 없는 달(31일, 2월 29일)은 그 달의 마지막 날로 본다. 반복의 날짜는 시작
날짜이기도 하다(그 날 전에는 울리지 않는다)."""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from . import config

ONCE, DAILY, WEEKDAYS, WEEKLY, MONTHLY, YEARLY = (
    "once", "daily", "weekdays", "weekly", "monthly", "yearly")
KINDS = (ONCE, DAILY, WEEKDAYS, WEEKLY, MONTHLY, YEARLY)
BY_DATE, BY_NTH, BY_LAST = "date", "nth", "last"
LEADS = (0, 10, 60, 1440)          # 미리 알림(분)
# 미루기(분). 내일 아침은 따로 계산한다.
SNOOZES = (10, 60)
MORNING = (9, 0)
_TIME = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# 알림의 날짜가 될 수 있는 해. Windows 의 시각 계산이 3001 년쯤에서 끝난다.
MIN_YEAR, MAX_YEAR = 1970, 2999
# 다음 때를 찾을 때 하루씩 넘겨 보는 한도(매년이 가장 길다).
_SEARCH_DAYS = 800
# 공유 알림의 받는 사람 수 한도.
MAX_TARGETS = 100


@dataclass
class Rule:
    kind: str = ONCE
    date: str = ""              # 기준 날짜 YYYY-MM-DD(한 번의 날, 매월·매년의 기준, 시작)
    time: str = "09:00"
    days: list[int] = field(default_factory=list)   # 매주: 월=0 … 일=6
    monthly: str = BY_DATE
    lead: int = 0               # 미리 알림(분)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "date": self.date, "time": self.time,
                "days": list(self.days), "monthly": self.monthly, "lead": self.lead}

    @classmethod
    def from_dict(cls, raw) -> "Rule | None":
        """저장된 값에서 규칙을 만든다. 모르는 값이면 None(손으로 고친 파일 등)."""
        if not isinstance(raw, dict):
            return None
        kind = raw.get("kind")
        time_ = raw.get("time")
        if kind not in KINDS or not isinstance(time_, str) or not _TIME.match(time_):
            return None
        day = raw.get("date") or ""
        if not isinstance(day, str) or (day and not _DATE.match(day)):
            return None
        if kind in (ONCE, MONTHLY, YEARLY):
            try:
                date.fromisoformat(day)
            except ValueError:
                return None
        if day and not MIN_YEAR <= int(day[:4]) <= MAX_YEAR:
            return None
        days = raw.get("days") or []
        if not isinstance(days, list) or not all(
                isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6 for d in days):
            return None
        if kind == WEEKLY and not days:
            return None
        monthly = raw.get("monthly") or BY_DATE
        if monthly not in (BY_DATE, BY_NTH, BY_LAST):
            monthly = BY_DATE
        lead = raw.get("lead") or 0
        if lead not in LEADS:
            lead = 0
        return cls(kind, day, time_, sorted(set(days)), monthly, int(lead))

    def hm(self) -> tuple[int, int]:
        h, m = self.time.split(":")
        return int(h), int(m)

    def base(self) -> date | None:
        try:
            return date.fromisoformat(self.date) if self.date else None
        except ValueError:
            return None


def _last_day(y: int, m: int) -> int:
    return calendar.monthrange(y, m)[1]


def nth_of(d: date) -> int:
    """그 달의 몇째 그 요일인가(0 부터)."""
    return (d.day - 1) // 7


def matches(rule: Rule, d: date) -> bool:
    """그 날이 규칙에 맞는가(시각은 따로 본다)."""
    base = rule.base()
    if rule.kind == ONCE:
        return base == d
    if rule.kind == DAILY:
        return True
    if rule.kind == WEEKDAYS:
        return d.weekday() < 5
    if rule.kind == WEEKLY:
        return d.weekday() in rule.days
    if base is None:
        return False
    if rule.kind == MONTHLY:
        last = _last_day(d.year, d.month)
        if rule.monthly == BY_LAST:
            return d.day == last
        if rule.monthly == BY_NTH:
            if d.weekday() != base.weekday():
                return False
            n = nth_of(base)
            if n >= 4:                       # 다섯째는 그 달의 마지막 그 요일
                return d.day + 7 > last
            return nth_of(d) == n
        return d.day == min(base.day, last)
    if rule.kind == YEARLY:
        return d.month == base.month and d.day == min(base.day, _last_day(d.year, base.month))
    return False


def next_time(rule: Rule, after: datetime) -> datetime | None:
    """``after`` 보다 뒤인 첫 알림 때. 없으면(지난 한 번 알림) None."""
    h, m = rule.hm()
    if rule.kind == ONCE:
        base = rule.base()
        if base is None:
            return None
        at = datetime(base.year, base.month, base.day, h, m)
        return at if at > after else None
    start = after.date()
    base = rule.base()
    if base is not None and base > start:
        start = base                 # 반복은 기준 날짜부터 시작한다
    for i in range(_SEARCH_DAYS):
        d = start + timedelta(days=i)
        if matches(rule, d):
            at = datetime(d.year, d.month, d.day, h, m)
            if at > after:
                return at
    return None


def last_time(rule: Rule, since: datetime, until: datetime) -> datetime | None:
    """``since`` 뒤, ``until`` 까지 가운데 마지막 알림 때(꺼져 있던 동안 지난 것)."""
    found = None
    at = next_time(rule, since)
    guard = 0
    while at is not None and at <= until and guard < 2000:
        found = at
        at = next_time(rule, at)
        guard += 1
    return found


def snooze_until(now: datetime, minutes: int | None) -> datetime:
    """미루기. ``minutes`` 가 None 이면 내일 아침 9시."""
    if minutes is None:
        t = now + timedelta(days=1)
        return datetime(t.year, t.month, t.day, *MORNING)
    return now.replace(second=0, microsecond=0) + timedelta(minutes=minutes)


def quick(now: datetime, which: str) -> Rule:
    """빨리 정하기: '10m', '60m', 'morning'(내일 아침), 'monday'(다음 주 월요일 아침)."""
    if which in ("10m", "60m"):
        at = now.replace(second=0, microsecond=0) + timedelta(minutes=int(which[:-1]))
    elif which == "monday":
        d = now.date() + timedelta(days=7 - now.weekday())
        at = datetime(d.year, d.month, d.day, *MORNING)
    else:
        t = now + timedelta(days=1)
        at = datetime(t.year, t.month, t.day, *MORNING)
    return Rule(ONCE, at.date().isoformat(), f"{at.hour:02d}:{at.minute:02d}")


def to_ms(at: datetime) -> int:
    return int(at.timestamp() * 1000)


def from_ms(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000)


# ── 공유 PC 가 나누는 알림(모두, 고른 사람) ──
#
# {"rule": 규칙, "targets": 받는 사람(빈 목록이면 모두), "set_by": 건 사람,
#  "set_at": 공유 PC 가 받은 때(ms)}. 울린 회차와 미루기는 PC 마다 따로 적는다.

def clean_targets(raw) -> list[str] | None:
    """받는 사람 목록을 확인한다. 빈 목록은 모두. 모르는 값이면 None."""
    if not isinstance(raw, list) or len(raw) > MAX_TARGETS:
        return None
    out: list[str] = []
    for name in raw:
        if (not isinstance(name, str) or not name.strip()
                or len(name) > config.MAX_NICKNAME_LENGTH):
            return None
        if name not in out:
            out.append(name)
    return out


def shared_from_dict(raw) -> dict | None:
    """공유 알림 한 건을 확인해 깨끗한 dict 로. 모르는 값이면 None."""
    if not isinstance(raw, dict):
        return None
    rule = Rule.from_dict(raw.get("rule"))
    targets = clean_targets(raw.get("targets", []))
    set_by = raw.get("set_by", "")
    set_at = raw.get("set_at", 0)
    if (rule is None or targets is None or not isinstance(set_by, str)
            or len(set_by) > config.MAX_NICKNAME_LENGTH
            or not isinstance(set_at, int) or isinstance(set_at, bool) or set_at < 0):
        return None
    return {"rule": rule.to_dict(), "targets": targets, "set_by": set_by, "set_at": set_at}


def is_target(entry: dict, nickname: str) -> bool:
    """이 사람이 받는 알림인가. 받는 사람이 비어 있으면 모두다."""
    targets = entry.get("targets") or []
    return not targets or nickname in targets
