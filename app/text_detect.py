"""본문 자동 인식: 날짜와 시각, 계산 줄, 고른 글의 수, 연락처, #태그, @이름, 같은 줄 모아 세기.

모두 이 PC 안에서 정해 둔 규칙으로 읽고, 외부 서비스는 쓰지 않는다. 화면 코드가 없는
순수 함수이고 표준 라이브러리만 쓴다. 식은 작은 파서로 읽는다(eval 을 쓰지 않는다).

정규식은 미리 만들어 두고, 걸리는 시간은 글 길이에 거의 비례한다. 편집할 때마다 불러도
된다. 돌려주는 위치(start, end)는 모두 받은 글 안의 글자 위치다(end 는 포함하지 않는다).
전각 숫자와 기호(１２：／)는 반각과 같이 읽는다. 한 글자를 한 글자로 바꾸므로 위치는
그대로다."""
from __future__ import annotations

import calendar
import re
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import (ROUND_HALF_EVEN, ROUND_HALF_UP, Context, Decimal,
                     DivisionByZero, InvalidOperation, Overflow)

PHONE, EMAIL, ACCOUNT = "phone", "email", "account"


# ── 돌려주는 값 ──

@dataclass(frozen=True, slots=True)
class DateHit:
    start: int
    end: int
    when: datetime          # 이 PC 의 시각(시간대 없음)
    has_time: bool          # 시각을 적었나. 아니면 when 은 그 날 9시
    has_date: bool          # 날짜나 요일을 적었나. 아니면 시각만 적은 것


@dataclass(frozen=True, slots=True)
class CalcResult:
    value: Decimal
    text: str               # 보여 줄 글(format_number 규칙)
    expr_start: int         # 줄 안에서 식이 시작하는 자리


@dataclass(frozen=True, slots=True)
class NumberSummary:
    count: int
    total: Decimal
    average: Decimal
    commas: bool = False    # 고른 글의 수에 쉼표가 있었나
    values: tuple[Decimal, ...] = ()

    def format(self, value: Decimal) -> str:
        return format_number(value, self.commas)

    @property
    def total_text(self) -> str:
        return self.format(self.total)

    @property
    def average_text(self) -> str:
        return self.format(self.average)


@dataclass(frozen=True, slots=True)
class ContactHit:
    start: int
    end: int
    kind: str               # PHONE, EMAIL, ACCOUNT
    value: str              # 복사할 값
    bank: str = ""          # 계좌 앞에 적힌 은행 이름(있을 때만)


@dataclass(frozen=True, slots=True)
class TagHit:
    start: int
    end: int
    tag: str                # '#' 을 뺀 이름


@dataclass(frozen=True, slots=True)
class MentionHit:
    start: int              # '@' 의 자리
    end: int
    name: str               # 받은 이름 목록에 있는 그대로


@dataclass(slots=True)
class TallyRow:
    item: str
    quantity: int
    unit: str = ""
    names: list[str] = field(default_factory=list)


# ── 공통 ──

# 전각 숫자와 기호, 빼기 기호(U+2212)를 반각으로.
_HALF = str.maketrans("０１２３４５６７８９：／．，－（）＋＊％＝\u2212",
                      "0123456789:/.,-()+*%=-")
_SP = r"[ \t\u3000]"
_URL = re.compile(r"(?<![A-Za-z0-9])(?:[Hh][Tt][Tt][Pp][Ss]?://|[Ww][Ww][Ww]\.)[^\s<>\"']+")


def _url_spans(t: str) -> list[tuple[int, int]]:
    return [m.span() for m in _URL.finditer(t)]


class _Spans:
    """막을 구간들. 겹치는 것은 합쳐 두고, 어떤 구간과 겹치는지 이분 탐색으로 본다."""

    def __init__(self, spans) -> None:
        merged: list[list[int]] = []
        for s, e in sorted(spans):
            if merged and s < merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], e)
            else:
                merged.append([s, e])
        self._starts = [s for s, _ in merged]
        self._ends = [e for _, e in merged]

    def hits(self, s: int, e: int) -> bool:
        i = bisect_right(self._starts, s) - 1
        if i >= 0 and self._ends[i] > s:
            return True
        i += 1
        return i < len(self._starts) and self._starts[i] < e


def _keep_longest(items: list) -> list:
    """겹치는 것 가운데 가장 긴 것만 남긴다. 시작 자리 차례로 돌려준다."""
    kept: list = []
    for a in sorted(items, key=lambda a: (a.start, a.start - a.end)):
        if kept and a.start < kept[-1].end:
            if a.end - a.start > kept[-1].end - kept[-1].start:
                kept[-1] = a
            continue
        kept.append(a)
    return kept


# ── 날짜와 시각 ──
#
# 규칙
# - 해가 없는 날짜는 오늘이나 그 뒤로 처음 오는 그 날이다(오늘이 11/26 이면 '11/25' 는 내년).
# - '25일' 만 적으면 이번 달이고, 지났으면 다음 달이다. 그 날이 없는 달은 마지막 날로 본다.
# - 시각만 적으면 오늘이고, 이미 지났으면(지금과 같아도) 내일이다.
# - 시각 없이 날짜만 적으면 has_time 은 거짓이고 when 은 그 날 9시다.
# - 요일만 적으면 다음에 오는 그 요일이다. 오늘은 시각을 적었고 아직 안 지났을 때만 친다.
# - '이번 주, 다음 주, 다다음 주 X요일' 은 월요일에 시작하는 달력 주의 그 요일이다. 이번
#   주의 지난 요일이면 지난 날을 그대로 돌려준다. 영어 'next X' 도 다음 달력 주로 읽고,
#   'this X' 는 오늘부터 처음 오는 그 요일이다.
# - 오전, 오후(am, pm)가 없으면 1시부터 6시는 오후, 7시부터 12시는 그대로, 13시부터 23시도
#   그대로다. '03:30' 처럼 앞에 0 을 붙인 시각과 0시는 적은 그대로다.
# - '밤' 은 6시부터 11시를 저녁으로, 12시를 0시로 읽고, 1시부터 5시는 새벽 그대로다.
# - '3/4' 처럼 둘 다 한 자리인 빗금 날짜는 분수일 때가 많다. 요일이나 시각이 붙을 때만 친다.
# - 날짜와 시각이 빈칸, 쉼표, at, on, の, 에 로만 떨어져 있으면 하나로 합친다.
# - 후보가 겹치면 가장 긴 것을 고른다. 주소(URL), 전화번호, 계좌, 이메일, #태그 안은 보지 않는다.

_AM, _PM, _NIGHT = "am", "pm", "night"
_MORNING = time(9, 0)
_PERIOD_OF = {"오전": _AM, "아침": _AM, "새벽": _AM, "오후": _PM, "낮": _PM, "저녁": _PM,
              "밤": _NIGHT, "午前": _AM, "朝": _AM, "午後": _PM, "昼": _PM, "夕方": _PM,
              "夜": _NIGHT}
_PERIOD = r"(오전|오후|아침|새벽|낮|저녁|밤|午前|午後|夕方|朝|昼|夜)"
_AMPM = r"(?i:([ap])\.?m\.?)(?![A-Za-z])"
# 수 앞뒤의 경계. 더 긴 수, 소수, 가격, 버전, 낱말, '제1' 의 일부가 아니어야 한다.
_L = r"(?<![0-9A-Za-z_.,:/+#@$₩€£¥제第])"
_R = r"(?![0-9]|[.,:/][0-9])"
# 날짜 뒤의 '(화)', '(火)', '(Tue)'.
_WD_PAREN = (r"(?:" + _SP + r"*\([ \t]*(?:[월화수목금토일](?:요일)?|[月火水木金土日](?:曜日?)?"
             r"|(?i:(?:mon|tue|wed|thu|fri|sat|sun)[a-z]{0,6})\.?)[ \t]*\))")

_KJ_YMD = re.compile(
    _L + r"(?:([0-9]{4})" + _SP + r"*[년年]" + _SP + r"*)?([0-9]{1,2})" + _SP + r"*[월月]"
    + _SP + r"*([0-9]{1,2})" + _SP + r"*[일日]" + _WD_PAREN + "?")
_KJ_DAY = re.compile(
    _L + r"([0-9]{1,2})" + _SP + r"*[일日](?!" + _SP
    + r"*(?:간|동안|째|씩|치|후|뒤|만|이내|분|間|後|目|ぶり|分|中|おき))" + _WD_PAREN + "?")
_YMD = re.compile(
    _L + r"((?:19|20)[0-9]{2})(?:-([0-9]{1,2})-([0-9]{1,2})|/([0-9]{1,2})/([0-9]{1,2})"
    r"|\.[ ]?([0-9]{1,2})\.[ ]?([0-9]{1,2})\.?)" + _R + _WD_PAREN + "?")
_MD_SLASH = re.compile(
    _L + r"([0-9]{1,2})/([0-9]{1,2})(?:/((?:19|20)?[0-9]{2}))?" + _R + _WD_PAREN + "?")
# '12.1' 은 소수와 같으므로 '(월)' 이 붙을 때만 날짜다.
_MD_DOT = re.compile(_L + r"([0-9]{1,2})\.[ ]?([0-9]{1,2})\.?" + _WD_PAREN)

_MONTH_NAMES = ("january", "february", "march", "april", "may", "june", "july",
                "august", "september", "october", "november", "december")
_MONTH_OF = {name: i + 1 for i, name in enumerate(_MONTH_NAMES)}
_MONTH_OF.update({name[:3]: i + 1 for i, name in enumerate(_MONTH_NAMES)})
_MONTH_OF["sept"] = 9
_MONTH_ABBR = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sept", "Sep",
               "Oct", "Nov", "Dec")
# 긴 이름은 대소문자를 가리지 않는다. 줄인 이름과 May 는 첫 글자가 대문자여야 한다
# ('may 3 people' 같은 글을 날짜로 읽지 않게).
_EN_MONTH = ("(?:(?i:" + "|".join(n for n in _MONTH_NAMES if n != "may") + ")|"
             + "|".join(_MONTH_ABBR) + "|" + "|".join(a.upper() for a in _MONTH_ABBR) + ")")
_EN_MD = re.compile(
    r"(?<![A-Za-z])(" + _EN_MONTH + r")\.?" + _SP + r"+([0-9]{1,2})(?:st|nd|rd|th)?"
    r"(?:,?" + _SP + r"+((?:19|20)[0-9]{2}))?(?![0-9A-Za-z]|[.,:/][0-9])")
_EN_DM = re.compile(
    _L + r"([0-9]{1,2})(?:st|nd|rd|th)?" + _SP + r"+(" + _EN_MONTH + r")\.?"
    r"(?:,?" + _SP + r"+((?:19|20)[0-9]{2}))?(?![A-Za-z0-9])")
_EN_WD = re.compile(
    r"(?<![A-Za-z])(?:(?i:(this|next|coming))" + _SP + r"+)?"
    r"((?i:monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
    r"|Mon|Tues|Tue|Wed|Thurs|Thur|Thu|Fri|Sat|Sun|MON|TUE|WED|THU|FRI|SAT|SUN)(?![A-Za-z])")
_EN_WD_OF = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
_EN_REL = re.compile(
    r"(?<![A-Za-z])(?i:(?:the" + _SP + r"+)?day" + _SP + r"+after" + _SP
    + r"+tomorrow|today|tomorrow|tmrw)(?![A-Za-z])")

_REL_DAYS = {"오늘": 0, "내일모레": 2, "내일": 1, "모레": 2, "글피": 3,
             "今日": 0, "明日": 1, "明後日": 2}
_KJ_REL = re.compile(r"(?<![가-힣])(?:오늘(?!날)|내일모레|내일|모레|글피)|明後日|明日|今日")
_WD_KO = "월화수목금토일"
_WD_JA = "月火水木金土日"
_WEEK_SHIFT = {"이번": 0, "금": 0, "다음": 1, "담": 1, "차": 1, "다다음": 2,
               "今週": 0, "来週": 1, "再来週": 2}
_KO_WD = re.compile(
    r"(?:(이번|금|다다음|다음|담|차)" + _SP + r"?주" + _SP + r"*)?([월화수목금토일])요일")
_JA_WD = re.compile(
    r"(?:(今週|再来週|来週)" + _SP + r"*の?" + _SP + r"*)?([月火水木金土日])曜日?")

# '3시간', '3시즌' 같은 낱말은 시각이 아니다.
_KJ_TIME = re.compile(
    r"(?:" + _PERIOD + _SP + r"*)?" + _L + r"([0-9]{1,2})" + _SP + r"*[시時](?![간間즌리트험장청민])"
    r"(?:" + _SP + r"*([0-9]{1,2})" + _SP + r"*[분分]|" + _SP
    + r"*(반(?![가-힣])|반(?=[에까부쯤이은])|半))?")
_COLON = re.compile(
    r"(?:" + _PERIOD + _SP + r"*)?" + _L + r"([0-9]{1,2}):([0-5][0-9])(?![0-9]|[:.][0-9])"
    r"(?:" + _SP + r"*" + _AMPM + r")?")
_EN_HOUR = re.compile(_L + r"([0-9]{1,2})" + _SP + r"*" + _AMPM)
_NOON = re.compile(r"(?<![A-Za-z])(?i:noon)(?![A-Za-z])|정오|正午")

_GAP = re.compile(r"[ \t\u3000,]*(?:(?:(?i:at|on)(?![A-Za-z])|@|の|에)[ \t\u3000,]*)?")
_GAP_MAX = 6

# '11/12' 처럼 두 가지로 읽히는 날짜를 일/월로 읽는 언어. 앱의 세 언어(ko, ja, en)는
# 모두 월/일로 읽으므로 지금은 결과가 같다. 첫 수가 12 보다 크면 언어와 상관없이 일/월이다.
_DAY_FIRST = frozenset({"en-gb", "en-au", "en-nz", "en-ie", "fr", "de", "es", "it"})


@dataclass(slots=True)
class _Atom:
    start: int
    end: int
    kind: str               # "d" 날짜, "w" 요일, "t" 시각
    spec: tuple
    weak: bool = False      # 분수일 수도 있는 '3/4'


@dataclass(slots=True)
class _Group:
    start: int
    end: int
    d: _Atom | None = None
    w: _Atom | None = None
    t: _Atom | None = None


def _day_first(lang: str) -> bool:
    key = (lang or "").lower().replace("_", "-")
    return key in _DAY_FIRST or key.split("-")[0] in _DAY_FIRST


def _hour(h: int, period: str | None, padded: bool) -> int | None:
    """오전, 오후를 반영한 24시간제의 시. 맞지 않으면 None."""
    if h > 23:
        return None
    if period == _AM:
        return 0 if h == 12 else (h if h < 12 else None)
    if period == _PM:
        return 12 if h in (0, 12) else (h + 12 if h < 12 else h)
    if period == _NIGHT:
        if h == 12:
            return 0
        return h + 12 if 6 <= h <= 11 else h
    if padded or h == 0 or h >= 7:
        return h
    return h + 12                       # 1시부터 6시는 오후로 본다


def _date_atoms(t: str, day_first: bool) -> list[_Atom]:
    out: list[_Atom] = []
    add = out.append

    def add_date(m: re.Match, y: int | None, mo: int, d: int, weak: bool = False) -> None:
        # 알림으로 걸 수 있는 해만(reminders.MIN_YEAR~MAX_YEAR).
        if 1 <= mo <= 12 and 1 <= d <= 31 and (y is None or 1970 <= y <= 2999):
            add(_Atom(m.start(), m.end(), "d", ("abs", y, mo, d), weak))

    for m in _KJ_YMD.finditer(t):
        add_date(m, int(m[1]) if m[1] else None, int(m[2]), int(m[3]))
    for m in _YMD.finditer(t):
        mo, d = (m[2], m[3]) if m[2] else (m[4], m[5]) if m[4] else (m[6], m[7])
        add_date(m, int(m[1]), int(mo), int(d))
    for m in _MD_SLASH.finditer(t):
        a, b = int(m[1]), int(m[2])
        mo, d = (b, a) if a > 12 or (day_first and b <= 12) else (a, b)
        y = None
        if m[3]:
            y = int(m[3]) + (2000 if len(m[3]) == 2 else 0)
        weak = not m[3] and "(" not in m[0] and len(m[1]) == 1 and len(m[2]) == 1
        add_date(m, y, mo, d, weak)
    for m in _MD_DOT.finditer(t):
        add_date(m, None, int(m[1]), int(m[2]))
    for m in _EN_MD.finditer(t):
        add_date(m, int(m[3]) if m[3] else None, _MONTH_OF[m[1].lower()], int(m[2]))
    for m in _EN_DM.finditer(t):
        add_date(m, int(m[3]) if m[3] else None, _MONTH_OF[m[2].lower()], int(m[1]))
    for m in _KJ_DAY.finditer(t):
        d = int(m[1])
        if 1 <= d <= 31:
            add(_Atom(m.start(), m.end(), "d", ("day", d)))
    for m in _KJ_REL.finditer(t):
        add(_Atom(m.start(), m.end(), "d", ("rel", _REL_DAYS[m[0]])))
    for m in _EN_REL.finditer(t):
        word = m[0].lower()
        n = 0 if word == "today" else 2 if "after" in word else 1
        add(_Atom(m.start(), m.end(), "d", ("rel", n)))
    for m in _KO_WD.finditer(t):
        add(_Atom(m.start(), m.end(), "w",
                  (_WD_KO.index(m[2]), _WEEK_SHIFT[m[1]] if m[1] else None)))
    for m in _JA_WD.finditer(t):
        add(_Atom(m.start(), m.end(), "w",
                  (_WD_JA.index(m[2]), _WEEK_SHIFT[m[1]] if m[1] else None)))
    for m in _EN_WD.finditer(t):
        mod = (m[1] or "").lower()
        shift = 1 if mod == "next" else ("this" if mod else None)
        add(_Atom(m.start(), m.end(), "w", (_EN_WD_OF[m[2][:3].lower()], shift)))

    for m in _KJ_TIME.finditer(t):
        h = _hour(int(m[2]), _PERIOD_OF.get(m[1]), len(m[2]) == 2 and m[2][0] == "0")
        mi = 30 if m[4] else (int(m[3]) if m[3] else 0)
        if h is not None and mi < 60:
            add(_Atom(m.start(), m.end(), "t", (h, mi)))
    for m in _COLON.finditer(t):
        if m[4]:
            period = _AM if m[4].lower() == "a" else _PM
            if int(m[2]) > 12:
                continue
        else:
            period = _PERIOD_OF.get(m[1])
        h = _hour(int(m[2]), period, len(m[2]) == 2 and m[2][0] == "0")
        if h is not None:
            add(_Atom(m.start(), m.end(), "t", (h, int(m[3]))))
    for m in _EN_HOUR.finditer(t):
        if 1 <= int(m[1]) <= 12:
            h = _hour(int(m[1]), _AM if m[2].lower() == "a" else _PM, False)
            add(_Atom(m.start(), m.end(), "t", (h, 0)))
    for m in _NOON.finditer(t):
        add(_Atom(m.start(), m.end(), "t", (12, 0)))
    return out


def _date_groups(t: str, lang: str, contacts: list[ContactHit] | None = None) -> list[_Group]:
    """날짜, 요일, 시각 조각을 찾아 겹침을 풀고, 붙어 있는 것을 하나로 묶는다."""
    atoms = _date_atoms(t, _day_first(lang))
    if not atoms:
        return []
    if contacts is None:
        contacts = _contacts(t)
    blocked = _Spans(_url_spans(t) + [(c.start, c.end) for c in contacts]
                     + [(h.start, h.end) for h in _tags(t)])
    atoms = _keep_longest([a for a in atoms if not blocked.hits(a.start, a.end)])
    groups: list[_Group] = []
    for a in atoms:
        g = groups[-1] if groups else None
        if (g is not None and getattr(g, a.kind) is None and a.start - g.end <= _GAP_MAX
                and _GAP.fullmatch(t, g.end, a.start)):
            setattr(g, a.kind, a)
            g.end = a.end
        else:
            g = _Group(a.start, a.end)
            setattr(g, a.kind, a)
            groups.append(g)
    return [g for g in groups
            if not (g.d is not None and g.d.weak and g.t is None and g.w is None)]


def _last_day(y: int, m: int) -> int:
    return calendar.monthrange(y, m)[1]


def _on_or_after(today: date, mo: int, d: int) -> date | None:
    """오늘이나 그 뒤로 처음 오는 mo월 d일. 2월 29일은 다음 윤년이다."""
    for y in range(today.year, today.year + 9):
        try:
            day = date(y, mo, d)
        except ValueError:
            continue
        if day >= today:
            return day
    return None


def _day_of_month(today: date, d: int) -> date:
    """이번 달 d일, 지났으면 다음 달 d일. 그 날이 없는 달은 마지막 날."""
    day = date(today.year, today.month, min(d, _last_day(today.year, today.month)))
    if day >= today:
        return day
    y, mo = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
    return date(y, mo, min(d, _last_day(y, mo)))


def _resolve(g: _Group, now: datetime) -> tuple[datetime, bool, bool] | None:
    today = now.date()
    at = time(*g.t.spec) if g.t is not None else None
    day: date | None
    if g.d is not None:
        kind = g.d.spec[0]
        if kind == "rel":
            day = today + timedelta(days=g.d.spec[1])
        elif kind == "day":
            day = _day_of_month(today, g.d.spec[1])
        else:
            _, y, mo, d = g.d.spec
            if y is None:
                day = _on_or_after(today, mo, d)
            else:
                try:
                    day = date(y, mo, d)
                except ValueError:
                    return None
    elif g.w is not None:
        wd, shift = g.w.spec
        if isinstance(shift, int):
            day = today + timedelta(days=wd - today.weekday() + 7 * shift)
        else:
            delta = (wd - today.weekday()) % 7
            if delta == 0 and shift is None and (
                    at is None or datetime.combine(today, at) <= now):
                delta = 7
            day = today + timedelta(days=delta)
    else:
        when = datetime.combine(today, at)
        if when <= now:
            when += timedelta(days=1)
        return when, True, False
    if day is None:
        return None
    if at is None:
        return datetime.combine(day, _MORNING), False, True
    return datetime.combine(day, at), True, True


def find_dates(text: str, now: datetime, lang: str = "ko") -> list[DateHit]:
    """본문의 날짜와 시각. 세 언어(한국어, 일본어, 영어)를 늘 함께 읽는다.

    ``lang`` 은 '11/12' 처럼 두 가지로 읽히는 날짜의 차례만 정한다."""
    t = text.translate(_HALF)
    hits: list[DateHit] = []
    for g in _date_groups(t, lang):
        got = _resolve(g, now)
        if got is not None:
            hits.append(DateHit(g.start, g.end, *got))
    return hits


# ── 계산 줄 ──
#
# 줄이 '=' 로 끝나면 바로 앞의 식을 계산한다. 앞의 이름표('점심', '합계:')는 건너뛴다.
# 수는 쉼표(12,000)와 소수를 읽고, + - * / × ÷ ( ) % 와 수 사이의 x 를 읽는다. '원' 과
# 돈 기호(₩ $ € ¥ £)는 빈칸처럼 건너뛴다. 연산자가 하나도 없으면(수 하나, '-5', '50%')
# 계산으로 보지 않는다. 식은 '=' 앞 256 글자까지만 읽고, 그보다 길면 None 이다.
#
# 결과의 글: 소수 여섯째 자리까지 반올림하고 끝의 0 을 뗀다. 식에 쉼표를 썼거나 정수 부분이
# 다섯 자리 이상이면 천 단위 쉼표를 넣는다.
#
# 퍼센트: a + b% 는 a*(1+b/100), a - b% 는 a*(1-b/100), a * b% 는 a*b/100, 그 밖의 b% 는 b/100.

_CALC_SCAN = 256            # '=' 앞에서 식을 찾는 글자 수 한도
_CALC_TRIES = 24            # 식이 시작될 만한 자리를 해 보는 수 한도
_CALC_DEPTH = 40            # 괄호와 부호를 겹쳐 쓰는 깊이 한도
_CALC_SKIP = frozenset(" \t\u3000원₩$€¥£")
_CALC_CHARS = frozenset("0123456789.,+-*/×÷xX()%") | _CALC_SKIP
_CALC_FIRST = frozenset("0123456789.(-+")
_NUM_TOKEN = re.compile(r"[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?|\.[0-9]+")
_CALC_CTX = Context(prec=60, rounding=ROUND_HALF_EVEN,
                    traps=[DivisionByZero, InvalidOperation, Overflow])
_FMT_CTX = Context(prec=400, rounding=ROUND_HALF_UP)
_SIX = Decimal("0.000001")
_ONE = Decimal(1)
_HUNDRED = Decimal(100)


class _CalcError(Exception):
    pass


def _calc_tokens(s: str) -> tuple[list[tuple[str, object]], bool] | None:
    """식을 조각으로. 읽을 수 없는 글자가 있으면 None. 둘째 값은 쉼표를 썼는가."""
    toks: list[tuple[str, object]] = []
    commas = False
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c in _CALC_SKIP:
            i += 1
        elif c in "0123456789.":
            m = _NUM_TOKEN.match(s, i)
            if m is None or (m.end() < n and s[m.end()] in "0123456789.,"):
                return None
            commas = commas or "," in m[0]
            toks.append(("n", Decimal(m[0].replace(",", ""))))
            i = m.end()
        elif c in "+-*/×÷":
            toks.append(("op", "*" if c == "×" else "/" if c == "÷" else c))
            i += 1
        elif c in "xX":
            j = i + 1
            while j < n and s[j] in _CALC_SKIP:
                j += 1
            if (not toks or toks[-1][0] not in ("n", ")", "%")
                    or j >= n or s[j] not in "0123456789.("):
                return None                 # 수 사이의 x 만 곱하기다
            toks.append(("op", "*"))
            i += 1
        elif c in "()%":
            toks.append((c, None))
            i += 1
        else:
            return None
    return toks, commas


class _Calc:
    """작은 재귀 하강 파서. 식 = 항 (+|- 항)*, 항 = 인수 (*|/ 인수)*, 인수 = 부호 인수 | 수 | (식), 뒤에 %."""

    def __init__(self, toks: list[tuple[str, object]]) -> None:
        self.toks = toks
        self.i = 0
        self.ops = 0
        self.depth = 0

    def _peek(self) -> tuple[str, object]:
        return self.toks[self.i] if self.i < len(self.toks) else ("", None)

    def run(self) -> Decimal:
        value = self._expr()
        if self.i != len(self.toks) or self.ops == 0:
            raise _CalcError
        return value

    def _expr(self) -> Decimal:
        acc, _ = self._term()
        while True:
            kind, op = self._peek()
            if kind != "op" or op not in ("+", "-"):
                return acc
            self.i += 1
            self.ops += 1
            rhs, pct = self._term()
            if pct:                         # a + b% 는 a 에 a 의 b% 를 더한다
                rate = _CALC_CTX.add(_ONE, rhs) if op == "+" else _CALC_CTX.subtract(_ONE, rhs)
                acc = _CALC_CTX.multiply(acc, rate)
            else:
                acc = _CALC_CTX.add(acc, rhs) if op == "+" else _CALC_CTX.subtract(acc, rhs)

    def _term(self) -> tuple[Decimal, bool]:
        acc, pct = self._factor()
        while True:
            kind, op = self._peek()
            if kind != "op" or op not in ("*", "/"):
                return acc, pct
            self.i += 1
            self.ops += 1
            rhs, _ = self._factor()
            acc = _CALC_CTX.multiply(acc, rhs) if op == "*" else _CALC_CTX.divide(acc, rhs)
            pct = False

    def _factor(self) -> tuple[Decimal, bool]:
        self.depth += 1
        if self.depth > _CALC_DEPTH:
            raise _CalcError
        try:
            kind, val = self._peek()
            if kind == "op" and val in ("+", "-"):
                self.i += 1
                v, pct = self._factor()
                return (_CALC_CTX.minus(v) if val == "-" else v), pct
            if kind == "n":
                self.i += 1
                v = val
            elif kind == "(":
                self.i += 1
                v = self._expr()
                if self._peek()[0] != ")":
                    raise _CalcError
                self.i += 1
            else:
                raise _CalcError
            if self._peek()[0] == "%":
                self.i += 1
                return _CALC_CTX.divide(v, _HUNDRED), True
            return v, False
        finally:
            self.depth -= 1


def _evaluate(expr: str) -> tuple[Decimal, bool] | None:
    got = _calc_tokens(expr)
    if got is None or not got[0]:
        return None
    try:
        return _Calc(got[0]).run(), got[1]
    except (_CalcError, ArithmeticError):
        return None


def format_number(value: Decimal, commas: bool = False) -> str:
    """수를 보여 줄 글로. 소수는 여섯째 자리까지 반올림하고 끝의 0 은 뗀다(정수는 '.0'
    없이). 천 단위 쉼표는 ``commas`` 가 참이거나 정수 부분이 다섯 자리 이상일 때 넣는다."""
    try:
        q = value.quantize(_SIX, context=_FMT_CTX)
    except ArithmeticError:
        q = value
    text = format(q, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    neg = text.startswith("-")
    if neg:
        text = text[1:]
    whole, dot, frac = text.partition(".")
    if whole.strip("0") == "" and not frac:
        return "0"
    if commas or len(whole) >= 5:
        whole = f"{int(whole):,}"
    return ("-" if neg else "") + whole + dot + frac


def calc_line(line: str) -> CalcResult | None:
    """줄이 '=' 로 끝나면(뒤의 빈칸은 괜찮다) 바로 앞의 식을 계산한다. 아니면 None.

    0 으로 나누기, 읽을 수 없는 식, 연산자가 없는 수 하나도 None 이다."""
    s = line.translate(_HALF)
    end = len(s.rstrip())
    if end == 0 or s[end - 1] != "=":
        return None
    eq = end - 1
    lo = max(0, eq - _CALC_SCAN)
    i = eq
    while i > lo and s[i - 1] in _CALC_CHARS:
        i -= 1
    if i == lo and lo > 0 and s[lo - 1] in _CALC_CHARS:
        return None                         # 한도보다 긴 식은 일부만 계산하지 않는다
    tries = 0
    for st in range(i, eq):
        if s[st] not in _CALC_FIRST or (st > i and s[st - 1] in "0123456789.,"):
            continue                        # 수의 가운데에서는 시작하지 않는다
        tries += 1
        if tries > _CALC_TRIES:
            break
        got = _evaluate(s[st:eq])
        if got is not None:
            return CalcResult(got[0], format_number(*got), st)
    return None


# ── 고른 글의 수 ──
#
# 날짜, 시각, 전화번호, 계좌, 이메일, 주소, #태그 안의 수와 버전(1.0.4), 분수(1/2),
# 년 월 일 시 분 초가 붙은 수는 세지 않는다. 영문자 바로 뒤의 수(A4, MP3)도 세지 않는다.

_NUMBER = re.compile(
    r"(?<![0-9A-Za-z_.,:/#+\-])(-?)([0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?)"
    r"(?![0-9]|[.,:/][0-9]|" + _SP + r"*[년월일시분초年月日時分秒])")


def summarize_numbers(text: str) -> NumberSummary | None:
    """고른 글 안의 수의 개수, 합계, 평균. 수가 둘보다 적으면 None."""
    t = text.translate(_HALF)
    found = list(_NUMBER.finditer(t))
    if len(found) < 2:
        return None
    contacts = _contacts(t)
    blocked = _Spans(_url_spans(t) + [(c.start, c.end) for c in contacts]
                     + [(g.start, g.end) for g in _date_groups(t, "ko", contacts)]
                     + [(h.start, h.end) for h in _tags(t)])
    values: list[Decimal] = []
    commas = False
    for m in found:
        if blocked.hits(m.start(), m.end()):
            continue
        commas = commas or "," in m[2]
        v = Decimal(m[2].replace(",", ""))
        values.append(-v if m[1] else v)
    if len(values) < 2:
        return None
    total = Decimal(0)
    for v in values:
        total = _CALC_CTX.add(total, v)
    average = _CALC_CTX.divide(total, Decimal(len(values)))
    return NumberSummary(len(values), total, average, commas, tuple(values))


# ── 연락처 ──
#
# 전화번호는 한국 번호만 읽는다(휴대폰, 지역 번호, 대표 번호 15xx 16xx 18xx, +82).
# 복사할 값은 '-' 로 이은 모양이다. 계좌는 '-' 로 이은 셋이나 네 묶음, 숫자 10 개에서
# 14 개이고 전화번호가 아닌 것만 친다. 사업자등록번호 모양(3-2-5)은 은행 이름이 앞에 있을
# 때만 계좌로 본다. 계좌의 복사할 값은 숫자만이다.

_PH_L = r"(?<![0-9A-Za-z+\-.])"
_PH_R = r"(?![0-9]|[\-.][0-9])"
_AREA = r"0(?:2|3[1-3]|4[1-4]|5[1-5]|6[1-4]|70|50[2-8])"
_MOBILE = re.compile(_PH_L + r"(01[016789])([\-. ]?)([0-9]{3,4})\2([0-9]{4})" + _PH_R)
_LANDLINE = re.compile(_PH_L + r"(" + _AREA + r")([\-. ])([0-9]{3,4})\2([0-9]{4})" + _PH_R)
_LANDLINE_PAREN = re.compile(
    r"\((" + _AREA + r")\)[ ]?([0-9]{3,4})[\-. ]([0-9]{4})" + _PH_R)
_REP = re.compile(
    _PH_L + r"(1(?:5(?:44|66|77|88|99)|6(?:00|44|61|66|70|88)|8(?:00|33|55|77|99)))-([0-9]{4})"
    + _PH_R)
_INTL = re.compile(
    r"(?<![0-9A-Za-z+])\+82[\- ]?(?:\(0\)[\- ]?)?0?(1[016789]|2|3[1-3]|4[1-4]|5[1-5]|6[1-4]|70)"
    r"[\-. ]?([0-9]{3,4})[\-. ]?([0-9]{4})" + _PH_R)
_EMAIL = re.compile(
    r"(?<![A-Za-z0-9._%+\-])[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}"
    r"(?![A-Za-z0-9\-]|\.[A-Za-z0-9])")
_ACCOUNT = re.compile(
    r"(?<![0-9A-Za-z+\-.,/])[0-9]{2,6}(?:-[0-9]{2,7}){2,3}(?![0-9]|[\-.,/:][0-9])")
_BANK = re.compile(
    r"(카카오뱅크|토스뱅크|케이뱅크|새마을금고|우체국|국민|신한|우리|하나|농협|기업|카카오|토스|"
    r"씨티|새마을|신협|수협|산업|부산|대구|경남|광주|전북|제주|SC제일|KB|NH|IBK|SC)(?:은행)?"
    r"(?:" + _SP + r"*계좌(?:번호)?)?" + _SP + r"*:?" + _SP + r"*$", re.IGNORECASE)
_BANK_WINDOW = 20
_MOBILE_PREFIXES = frozenset({"010", "011", "016", "017", "018", "019"})


def _bank_before(t: str, start: int) -> str:
    m = _BANK.search(t, max(0, start - _BANK_WINDOW), start)
    return m[1] if m else ""


def _contacts(t: str) -> list[ContactHit]:
    found: list[ContactHit] = []
    for m in _MOBILE.finditer(t):
        found.append(ContactHit(m.start(), m.end(), PHONE, f"{m[1]}-{m[3]}-{m[4]}"))
    for m in _LANDLINE.finditer(t):
        found.append(ContactHit(m.start(), m.end(), PHONE, f"{m[1]}-{m[3]}-{m[4]}"))
    for m in _LANDLINE_PAREN.finditer(t):
        found.append(ContactHit(m.start(), m.end(), PHONE, f"{m[1]}-{m[2]}-{m[3]}"))
    for m in _REP.finditer(t):
        found.append(ContactHit(m.start(), m.end(), PHONE, f"{m[1]}-{m[2]}"))
    for m in _INTL.finditer(t):
        found.append(ContactHit(m.start(), m.end(), PHONE, f"+82-{m[1]}-{m[2]}-{m[3]}"))
    for m in _EMAIL.finditer(t):
        local, _, domain = m[0].rpartition("@")
        found.append(ContactHit(m.start(), m.end(), EMAIL, f"{local}@{domain.lower()}"))
    found = _keep_longest(found)
    taken = _Spans([(c.start, c.end) for c in found])
    for m in _ACCOUNT.finditer(t):
        if taken.hits(m.start(), m.end()):
            continue
        groups = m[0].split("-")
        digits = "".join(groups)
        if not 10 <= len(digits) <= 14 or groups[0] in _MOBILE_PREFIXES:
            continue                        # 010 으로 시작하면 잘못 적은 전화번호다
        bank = _bank_before(t, m.start())
        if not bank and [len(g) for g in groups] == [3, 2, 5]:
            continue                        # 사업자등록번호 모양
        found.append(ContactHit(m.start(), m.end(), ACCOUNT, digits, bank))
    found.sort(key=lambda c: c.start)
    return found


def find_contacts(text: str) -> list[ContactHit]:
    """전화번호, 이메일, 계좌번호. 글의 차례로."""
    return _contacts(text.translate(_HALF))


# ── #태그 ──
#
# '#' 뒤의 글자, 숫자, 밑줄. 숫자와 밑줄만이면 태그가 아니다(#1). '#' 앞이 글자나 '&'
# (HTML 의 &#123;)나 '#' 이면 태그가 아니다. 색 값(#FFF, #A1B2C3)도 아니다. 넷과 여덟
# 자리는 숫자가 섞였거나 모두 대문자일 때만 색으로 본다(#cafe 는 태그, #FFFF 는 색).
# 주소(URL) 안은 보지 않는다.

_TAG = re.compile(r"(?<![\w&#])#(\w+)")
_HEX = frozenset("0123456789abcdefABCDEF")


def _is_hex_color(tag: str) -> bool:
    if not all(c in _HEX for c in tag):
        return False
    if len(tag) in (3, 6):
        return True
    return len(tag) in (4, 8) and (any(c.isdigit() for c in tag) or tag.isupper())


def _tags(t: str) -> list[TagHit]:
    out: list[TagHit] = []
    urls: _Spans | None = None
    for m in _TAG.finditer(t):
        tag = m[1]
        if all(c.isdigit() or c == "_" for c in tag) or _is_hex_color(tag):
            continue
        if urls is None:
            urls = _Spans(_url_spans(t))
        if urls.hits(m.start(), m.end()):
            continue
        out.append(TagHit(m.start(), m.end(), tag))
    return out


def find_tags(text: str) -> list[TagHit]:
    """본문의 #태그. 글의 차례로."""
    return _tags(text)


def tags_in(text: str) -> list[str]:
    """본문의 태그 이름. 대소문자를 가리지 않고 한 번씩, 처음 나온 모양과 차례로."""
    seen: set[str] = set()
    out: list[str] = []
    for hit in _tags(text):
        key = hit.tag.casefold()
        if key not in seen:
            seen.add(key)
            out.append(hit.tag)
    return out


# ── @이름 ──
#
# '@' 뒤가 받은 이름 가운데 하나일 때만 친다. 긴 이름이 먼저다(서연이 > 서연). '@' 앞이
# 글자나 숫자이면(이메일) 치지 않는다. 대소문자는 가리지 않는다. 영문자로 끝나는 이름은
# 뒤에 영문자나 숫자가 이어지면 치지 않는다(@Ann 은 @Anna 가 아니다). 한글 이름 뒤의
# 조사(님, 이, 에게)는 괜찮다.

def _is_ascii_word(ch: str) -> bool:
    return ch.isascii() and (ch.isalnum() or ch == "_")


def find_mentions(text: str, names: list[str]) -> list[MentionHit]:
    """본문의 @이름. ``names`` 에 있는 이름만, 글의 차례로."""
    by_first: dict[str, list[tuple[str, str]]] = {}
    for name in sorted({n.strip() for n in names if n and n.strip()}, key=lambda n: (-len(n), n)):
        by_first.setdefault(name[0].casefold(), []).append((name, name.casefold()))
    out: list[MentionHit] = []
    if not by_first:
        return out
    n = len(text)
    pos = text.find("@")
    while pos != -1:
        at = pos + 1
        if at < n and (pos == 0 or not (text[pos - 1].isalnum() or text[pos - 1] == "_")):
            for name, key in by_first.get(text[at].casefold(), ()):
                end = at + len(name)
                if text[at:end].casefold() != key:
                    continue
                if end < n and _is_ascii_word(name[-1]) and _is_ascii_word(text[end]):
                    continue
                out.append(MentionHit(pos, end, name))
                break
        pos = text.find("@", at)
    return out


# ── 같은 줄 모아 세기 ──
#
# '김치찌개 2 (지우, 서연)', '아메리카노 3잔', '콜라 x2' 처럼 '이름 수[단위] [(사람들)]'
# 모양의 줄을 읽는다. 앞의 글머리(-, •, ■, ○, 1.)는 뗀다. 이름은 글자가 하나는 있어야
# 한다. 수는 1 에서 4 자리다. 같은 이름(빈칸을 하나로, 대소문자 무시)끼리 수를 더하고,
# 사람은 처음 나온 차례로 한 번씩 모은다. 단위는 처음 적힌 것을 쓴다.

_TALLY_UNITS = ("인분", "그릇", "박스", "세트", "pcs", "개", "잔", "명", "장", "병", "캔",
                "pc", "ea", "人前", "個", "杯", "枚", "本", "箱", "名", "つ", "x", "X", "×")
_TALLY_TAIL = re.compile(
    r"(?:(?<![A-Za-z])[xX×]" + _SP + r"*)?(?<![0-9.,:/\-])([0-9]{1,4})"
    r"(?:" + _SP + r"*(" + "|".join(_TALLY_UNITS) + r"))?" + _SP + r"*\Z")
_TALLY_HEAD = re.compile(
    _SP + r"*(?:[-*•·■□○●◦\u2013\u2014☐☑☒"
    r"✓✔]+|[0-9]{1,3}[.)](?=" + _SP + r"))" + _SP + r"*")
_TALLY_NAME_SPLIT = re.compile(r"[,、/]")
_TALLY_MAX = 200
_TALLY_TRIM = " \t\u3000:=-\u2013\u2014"


def _tally_line(line: str) -> tuple[str, int, str, list[str]] | None:
    if len(line) > _TALLY_MAX:
        return None
    s = line.translate(_HALF).strip()
    names: list[str] = []
    if s.endswith(")"):
        open_ = s.rfind("(")
        inner = s[open_ + 1:-1] if open_ >= 0 else ""
        if open_ < 0 or ")" in inner:
            return None
        parts = [p.strip() for p in _TALLY_NAME_SPLIT.split(inner)]
        if len(parts) == 1:
            parts = parts[0].split()
        names = [p for p in parts if p]
        s = s[:open_].rstrip()
    m = _TALLY_TAIL.search(s)
    if m is None:
        return None
    item = s[:m.start()]
    head = _TALLY_HEAD.match(item)
    if head is not None:
        item = item[head.end():]
    item = " ".join(item.split()).rstrip(_TALLY_TRIM)
    if not any(c.isalpha() for c in item):
        return None
    unit = m[2] or ""
    if unit in ("X", "×"):
        unit = "x"
    return item, int(m[1]), unit, names


def tally_lines(lines: list[str]) -> list[TallyRow]:
    """같은 모양의 줄을 이름별로 모아 센다. 읽힌 줄이 둘보다 적으면 빈 목록.

    처음 나온 차례로 돌려준다. 모양이 맞지 않는 줄은 건너뛴다."""
    rows: dict[str, TallyRow] = {}
    parsed = 0
    for line in lines:
        got = _tally_line(line)
        if got is None:
            continue
        item, qty, unit, names = got
        parsed += 1
        row = rows.get(item.casefold())
        if row is None:
            row = rows[item.casefold()] = TallyRow(item, 0, unit, [])
        row.quantity += qty
        if not row.unit:
            row.unit = unit
        for name in names:
            if name not in row.names:
                row.names.append(name)
    if parsed < 2:
        return []
    return list(rows.values())
