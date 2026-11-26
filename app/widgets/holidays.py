"""달력 위젯의 공휴일, 기념일, 세무 신고일. 화면 코드가 없는 순수 로직이다.

표는 앱 안에 있고 앱 업데이트로 고친다(인터넷에서 받지 않는다). 나중에 정해진
임시공휴일은 다음 업데이트에서 더하고, 그 사이에는 사용자가 달력에 '쉬는 날'을
넣는다.

한국(KR)
- 날짜가 정해진 공휴일과 대체공휴일은 「공휴일에 관한 법률」과 「관공서의 공휴일에
  관한 규정」을 따라 계산한다. 2026 년부터 노동절(5월 1일)과 제헌절(7월 17일)이
  공휴일이고 대체공휴일도 적용된다(2026-04-28 국무회의 의결, 5월 1일 시행).
- 대체공휴일: 설날과 추석 연휴는 일요일이나 다른 공휴일과 겹치면 연휴 다음의 첫
  평일. 3.1절, 노동절, 어린이날, 부처님오신날, 제헌절, 광복절, 개천절, 한글날,
  성탄절은 토요일, 일요일, 다른 공휴일과 겹치면 그 다음의 첫 평일. 신정, 현충일,
  선거일은 대체공휴일이 없다. 대체공휴일끼리 겹치면 그다음 평일로 미룬다.
- 음력 명절(설날, 부처님오신날, 추석)은 미리 계산한 표다(아래 _KR_LUNAR).
- 선거일은 확인한 것만 넣는다(2026-06-03 지방선거, 2028-04-12 국회의원선거). 2030 년
  선거일은 법으로 계산은 되지만 아직 공식 발표가 없어 넣지 않았다.
- 표가 있는 해: 2026~2032 년(:func:`covered`).

일본(JP): 국민의 축일에 관한 법률의 규칙으로 계산한다(정해진 날, 해피 먼데이, 춘분과
추분, 대체 휴일, 국민의 휴일). 춘분과 추분은 1980~2099 년에 맞는 근사식을 쓴다.
2022~2099 년.

미국(US): 연방 공휴일(5 U.S.C. 6103). 날짜가 정해진 공휴일이 토요일이면 앞 금요일,
일요일이면 다음 월요일에 쉰다(observed). 2021 년부터(준틴스)."""
from __future__ import annotations

import functools
from datetime import date, timedelta

COUNTRIES = ("KR", "JP", "US")
LANGS = ("ko", "en", "ja")

# 한국 음력 명절의 양력 날짜: (설날, 부처님오신날, 추석) 당일.
# 출처: 한국천문연구원 천문우주지식정보 '월력요항' 자료
#   https://astro.kasi.re.kr/kor/life/post/calendarData?search_year=2026 (2027, 2028 도 같은 주소)
#   (2026-11-25 확인. 2028 년은 공식 발표 전 자료로 표시돼 있다)
# 2029~2032 년: 위키백과 "Public holidays in South Korea" 의 연도별 표
#   https://en.wikipedia.org/wiki/Public_holidays_in_South_Korea (2026-11-25 확인)
_KR_LUNAR = {
    2026: (date(2026, 2, 17), date(2026, 5, 24), date(2026, 9, 25)),
    2027: (date(2027, 2, 7), date(2027, 5, 13), date(2027, 9, 15)),
    2028: (date(2028, 1, 27), date(2028, 5, 2), date(2028, 10, 3)),
    2029: (date(2029, 2, 13), date(2029, 5, 20), date(2029, 9, 22)),
    2030: (date(2030, 2, 3), date(2030, 5, 9), date(2030, 9, 12)),
    2031: (date(2031, 1, 23), date(2031, 5, 28), date(2031, 10, 1)),
    2032: (date(2032, 2, 11), date(2032, 5, 16), date(2032, 9, 19)),
}
# 임기 만료에 따른 선거일(공휴일). 위 한국천문연구원 자료에서 확인한 것만.
_KR_ELECTIONS = {
    date(2026, 6, 3): "kr.election_local",
    date(2028, 4, 12): "kr.election_assembly",
}
# 노동절과 제헌절이 공휴일이 된 해.
_KR_2026 = 2026

_NAMES: dict[str, tuple[str, str, str]] = {
    # 한국 공휴일
    "kr.new_year": ("신정", "New Year's Day", "新正"),
    "kr.seollal": ("설날", "Seollal", "ソルラル"),
    "kr.seollal_holiday": ("설날 연휴", "Seollal holiday", "ソルラル連休"),
    "kr.independence": ("삼일절", "Independence Movement Day", "三一節"),
    "kr.labor": ("노동절", "Labor Day", "労働節"),
    "kr.children": ("어린이날", "Children's Day", "こどもの日"),
    "kr.buddha": ("부처님오신날", "Buddha's Birthday", "釈迦誕生日"),
    "kr.memorial": ("현충일", "Memorial Day", "顕忠日"),
    "kr.constitution": ("제헌절", "Constitution Day", "制憲節"),
    "kr.liberation": ("광복절", "Liberation Day", "光復節"),
    "kr.chuseok": ("추석", "Chuseok", "秋夕"),
    "kr.chuseok_holiday": ("추석 연휴", "Chuseok holiday", "秋夕連休"),
    "kr.foundation": ("개천절", "National Foundation Day", "開天節"),
    "kr.hangul": ("한글날", "Hangul Day", "ハングルの日"),
    "kr.christmas": ("성탄절", "Christmas Day", "クリスマス"),
    "kr.substitute": ("대체공휴일", "Substitute holiday", "振替休日"),
    "kr.election_local": ("지방선거일", "Local election day", "地方選挙日"),
    "kr.election_assembly": ("국회의원선거일", "National Assembly election day",
                             "国会議員選挙日"),
    # 한국 기념일(쉬지 않는 날)
    "kr.arbor": ("식목일", "Arbor Day", "植樹の日"),
    "kr.parents": ("어버이날", "Parents' Day", "父母の日"),
    "kr.teachers": ("스승의 날", "Teachers' Day", "先生の日"),
    "kr.armed_forces": ("국군의 날", "Armed Forces Day", "国軍の日"),
    # 한국 세무 신고일
    "kr.tax.withholding": ("원천세 신고", "Withholding tax return", "源泉税申告"),
    "kr.tax.vat": ("부가세 신고", "VAT return", "付加価値税申告"),
    "kr.tax.corporate": ("법인세 신고", "Corporate tax return", "法人税申告"),
    "kr.tax.income": ("종합소득세 신고", "Income tax return", "総合所得税申告"),
    # 일본 공휴일
    "jp.new_year": ("원단", "New Year's Day", "元日"),
    "jp.coming_of_age": ("성인의 날", "Coming of Age Day", "成人の日"),
    "jp.foundation": ("건국기념일", "National Foundation Day", "建国記念の日"),
    "jp.emperor": ("천황탄생일", "Emperor's Birthday", "天皇誕生日"),
    "jp.vernal": ("춘분의 날", "Vernal Equinox Day", "春分の日"),
    "jp.showa": ("쇼와의 날", "Showa Day", "昭和の日"),
    "jp.constitution": ("헌법기념일", "Constitution Memorial Day", "憲法記念日"),
    "jp.greenery": ("녹색의 날", "Greenery Day", "みどりの日"),
    "jp.children": ("어린이날", "Children's Day", "こどもの日"),
    "jp.marine": ("바다의 날", "Marine Day", "海の日"),
    "jp.mountain": ("산의 날", "Mountain Day", "山の日"),
    "jp.aged": ("경로의 날", "Respect for the Aged Day", "敬老の日"),
    "jp.autumnal": ("추분의 날", "Autumnal Equinox Day", "秋分の日"),
    "jp.sports": ("스포츠의 날", "Sports Day", "スポーツの日"),
    "jp.culture": ("문화의 날", "Culture Day", "文化の日"),
    "jp.labor_thanks": ("근로감사의 날", "Labor Thanksgiving Day", "勤労感謝の日"),
    "jp.substitute": ("대체 휴일", "Substitute holiday", "振替休日"),
    "jp.citizens": ("국민의 휴일", "Citizens' holiday", "国民の休日"),
    # 일본 기념일
    "jp.hinamatsuri": ("히나마쓰리", "Hinamatsuri", "ひな祭り"),
    "jp.mothers": ("어머니의 날", "Mother's Day", "母の日"),
    "jp.fathers": ("아버지의 날", "Father's Day", "父の日"),
    "jp.tanabata": ("칠석", "Tanabata", "七夕"),
    "jp.shichigosan": ("시치고산", "Shichi-Go-San", "七五三"),
    "jp.christmas": ("크리스마스", "Christmas", "クリスマス"),
    # 미국 연방 공휴일
    "us.new_year": ("새해 첫날", "New Year's Day", "元日"),
    "us.mlk": ("마틴 루서 킹 데이", "Martin Luther King Jr. Day", "キング牧師記念日"),
    "us.washington": ("대통령의 날", "Washington's Birthday", "大統領の日"),
    "us.memorial": ("메모리얼 데이", "Memorial Day", "戦没将兵追悼記念日"),
    "us.juneteenth": ("준틴스", "Juneteenth", "ジューンティーンス"),
    "us.independence": ("독립기념일", "Independence Day", "独立記念日"),
    "us.labor": ("노동절", "Labor Day", "レイバー・デー"),
    "us.columbus": ("콜럼버스 데이", "Columbus Day", "コロンブス・デー"),
    "us.veterans": ("재향군인의 날", "Veterans Day", "退役軍人の日"),
    "us.thanksgiving": ("추수감사절", "Thanksgiving Day", "感謝祭"),
    "us.christmas": ("크리스마스", "Christmas Day", "クリスマス"),
    # 미국 기념일
    "us.valentine": ("밸런타인데이", "Valentine's Day", "バレンタインデー"),
    "us.st_patrick": ("성 패트릭의 날", "St. Patrick's Day", "聖パトリックの日"),
    "us.mothers": ("어머니의 날", "Mother's Day", "母の日"),
    "us.fathers": ("아버지의 날", "Father's Day", "父の日"),
    "us.halloween": ("핼러윈", "Halloween", "ハロウィーン"),
    "us.christmas_eve": ("크리스마스이브", "Christmas Eve", "クリスマスイブ"),
    "us.new_years_eve": ("새해 전날", "New Year's Eve", "大晦日"),
}
# 미국의 옮겨 쉬는 날 이름: "{name} (observed)".
_OBSERVED = ("{name} 대체 휴일", "{name} (observed)", "{name} 振替休日")


def name(key: str, lang: str = "ko") -> str:
    """이름 키를 그 언어의 이름으로. ``us.xxx@observed`` 는 옮겨 쉬는 날."""
    i = LANGS.index(lang) if lang in LANGS else 0
    base, _sep, tag = key.partition("@")
    row = _NAMES.get(base)
    if row is None:
        return key
    text = row[i]
    if tag == "observed":
        return _OBSERVED[i].format(name=text)
    return text


def _join(keys: list[str], lang: str) -> str:
    return ", ".join(name(k, lang) for k in keys)


def _named(table: dict[date, list[str]], lang: str) -> dict[date, str]:
    return {d: _join(keys, lang) for d, keys in sorted(table.items())}


def nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """그 달의 n 째 요일(월=0). n 이 -1 이면 마지막."""
    if n < 0:
        last = (date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1))
        return last - timedelta(days=(last.weekday() - weekday) % 7)
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def covered(country: str, year: int) -> bool:
    """이 해의 공휴일 표가 있는가."""
    if country == "KR":
        return year in _KR_LUNAR
    if country == "JP":
        return 2022 <= year <= 2099
    if country == "US":
        return 2021 <= year <= 2200
    return False


# ── 한국 ──

_KR_SUB_ANY = frozenset((
    "kr.independence", "kr.labor", "kr.children", "kr.buddha", "kr.constitution",
    "kr.liberation", "kr.foundation", "kr.hangul", "kr.christmas"))
_KR_SUB_SUNDAY = frozenset((
    "kr.seollal", "kr.seollal_holiday", "kr.chuseok", "kr.chuseok_holiday"))


def _kr_base(year: int) -> dict[date, list[str]]:
    days: dict[date, list[str]] = {}

    def add(d: date, key: str) -> None:
        days.setdefault(d, []).append(key)

    add(date(year, 1, 1), "kr.new_year")
    add(date(year, 3, 1), "kr.independence")
    if year >= _KR_2026:
        add(date(year, 5, 1), "kr.labor")
    add(date(year, 5, 5), "kr.children")
    add(date(year, 6, 6), "kr.memorial")
    if year >= _KR_2026:
        add(date(year, 7, 17), "kr.constitution")
    add(date(year, 8, 15), "kr.liberation")
    add(date(year, 10, 3), "kr.foundation")
    add(date(year, 10, 9), "kr.hangul")
    add(date(year, 12, 25), "kr.christmas")
    lunar = _KR_LUNAR.get(year)
    if lunar:
        seollal, buddha, chuseok = lunar
        add(seollal - timedelta(days=1), "kr.seollal_holiday")
        add(seollal, "kr.seollal")
        add(seollal + timedelta(days=1), "kr.seollal_holiday")
        add(buddha, "kr.buddha")
        add(chuseok - timedelta(days=1), "kr.chuseok_holiday")
        add(chuseok, "kr.chuseok")
        add(chuseok + timedelta(days=1), "kr.chuseok_holiday")
    for d, key in _KR_ELECTIONS.items():
        if d.year == year:
            add(d, key)
    return days


def _kr_lunar_end(year: int, d: date) -> date:
    """d 가 설날이나 추석 연휴 안이면 그 연휴의 마지막 날, 아니면 d."""
    lunar = _KR_LUNAR.get(year)
    if lunar:
        for center in (lunar[0], lunar[2]):
            if abs((d - center).days) <= 1:
                return center + timedelta(days=1)
    return d


@functools.lru_cache(maxsize=64)
def _kr(year: int) -> tuple[tuple[date, tuple[str, ...]], ...]:
    base = _kr_base(year)
    taken = set(base)
    subs: list[date] = []
    for d in sorted(base):
        keys = base[d]
        any_day = any(k in _KR_SUB_ANY for k in keys)
        sunday_only = any(k in _KR_SUB_SUNDAY for k in keys)
        if not (any_day or sunday_only):
            continue
        overlap = len(keys) >= 2
        lost = (overlap or (any_day and d.weekday() >= 5)
                or (sunday_only and d.weekday() == 6))
        if not lost:
            continue
        ref = _kr_lunar_end(year, d) if sunday_only else d
        cand = ref + timedelta(days=1)
        while cand.weekday() >= 5 or cand in taken:
            cand += timedelta(days=1)
        taken.add(cand)
        subs.append(cand)
    table = {d: list(keys) for d, keys in base.items()}
    for d in subs:
        table.setdefault(d, []).append("kr.substitute")
    return tuple((d, tuple(table[d])) for d in sorted(table))


def _kr_observances(year: int) -> dict[date, list[str]]:
    return {date(year, 4, 5): ["kr.arbor"], date(year, 5, 8): ["kr.parents"],
            date(year, 5, 15): ["kr.teachers"], date(year, 10, 1): ["kr.armed_forces"]}


# ── 일본 ──

def _jp_equinox(year: int, base: float) -> int:
    """국립천문대 계산과 맞는 근사식(1980~2099 년)."""
    y = year - 1980
    return int(base + 0.242194 * y - int(y / 4))


def vernal_equinox(year: int) -> date:
    return date(year, 3, _jp_equinox(year, 20.8431))


def autumnal_equinox(year: int) -> date:
    return date(year, 9, _jp_equinox(year, 23.2488))


@functools.lru_cache(maxsize=64)
def _jp(year: int) -> tuple[tuple[date, tuple[str, ...]], ...]:
    if not covered("JP", year):
        return ()
    national = {
        date(year, 1, 1): "jp.new_year",
        nth_weekday(year, 1, 0, 2): "jp.coming_of_age",
        date(year, 2, 11): "jp.foundation",
        date(year, 2, 23): "jp.emperor",
        vernal_equinox(year): "jp.vernal",
        date(year, 4, 29): "jp.showa",
        date(year, 5, 3): "jp.constitution",
        date(year, 5, 4): "jp.greenery",
        date(year, 5, 5): "jp.children",
        nth_weekday(year, 7, 0, 3): "jp.marine",
        date(year, 8, 11): "jp.mountain",
        nth_weekday(year, 9, 0, 3): "jp.aged",
        autumnal_equinox(year): "jp.autumnal",
        nth_weekday(year, 10, 0, 2): "jp.sports",
        date(year, 11, 3): "jp.culture",
        date(year, 11, 23): "jp.labor_thanks",
    }
    table = {d: [k] for d, k in national.items()}
    # 국민의 휴일: 앞날과 다음 날이 모두 축일인 날(축일이 아닌 날만).
    for d in sorted(national):
        mid = d + timedelta(days=1)
        if mid not in national and mid + timedelta(days=1) in national:
            table.setdefault(mid, []).append("jp.citizens")
    # 대체 휴일: 축일이 일요일이면 그 뒤로 가장 가까운, 축일이 아닌 날.
    for d in sorted(national):
        if d.weekday() == 6:
            cand = d + timedelta(days=1)
            while cand in national:
                cand += timedelta(days=1)
            if cand not in table:
                table[cand] = ["jp.substitute"]
    return tuple((d, tuple(table[d])) for d in sorted(table))


def _jp_observances(year: int) -> dict[date, list[str]]:
    return {date(year, 3, 3): ["jp.hinamatsuri"], nth_weekday(year, 5, 6, 2): ["jp.mothers"],
            nth_weekday(year, 6, 6, 3): ["jp.fathers"], date(year, 7, 7): ["jp.tanabata"],
            date(year, 11, 15): ["jp.shichigosan"], date(year, 12, 25): ["jp.christmas"]}


# ── 미국 ──

def _us_fixed(year: int) -> dict[date, str]:
    days = {date(year, 1, 1): "us.new_year", date(year, 7, 4): "us.independence",
            date(year, 11, 11): "us.veterans", date(year, 12, 25): "us.christmas"}
    if year >= 2021:
        days[date(year, 6, 19)] = "us.juneteenth"
    return days


@functools.lru_cache(maxsize=64)
def _us(year: int) -> tuple[tuple[date, tuple[str, ...]], ...]:
    if not covered("US", year):
        return ()
    table: dict[date, list[str]] = {}
    fixed = _us_fixed(year)
    for d, key in fixed.items():
        table.setdefault(d, []).append(key)
    for d, key in ((nth_weekday(year, 1, 0, 3), "us.mlk"),
                   (nth_weekday(year, 2, 0, 3), "us.washington"),
                   (nth_weekday(year, 5, 0, -1), "us.memorial"),
                   (nth_weekday(year, 9, 0, 1), "us.labor"),
                   (nth_weekday(year, 10, 0, 2), "us.columbus"),
                   (nth_weekday(year, 11, 3, 4), "us.thanksgiving")):
        table.setdefault(d, []).append(key)
    # 옮겨 쉬는 날. 다음 해 1월 1일이 토요일이면 올해 12월 31일에 쉰다.
    candidates = list(fixed.items())
    nxt = date(year + 1, 1, 1)
    if nxt.weekday() == 5:
        candidates.append((nxt, "us.new_year"))
    for d, key in candidates:
        if d.weekday() == 5:
            obs = d - timedelta(days=1)
        elif d.weekday() == 6:
            obs = d + timedelta(days=1)
        else:
            continue
        if obs.year == year:
            table.setdefault(obs, []).append(key + "@observed")
    return tuple((d, tuple(table[d])) for d in sorted(table))


def _us_observances(year: int) -> dict[date, list[str]]:
    return {date(year, 2, 14): ["us.valentine"], date(year, 3, 17): ["us.st_patrick"],
            nth_weekday(year, 5, 6, 2): ["us.mothers"], nth_weekday(year, 6, 6, 3): ["us.fathers"],
            date(year, 10, 31): ["us.halloween"], date(year, 12, 24): ["us.christmas_eve"],
            date(year, 12, 31): ["us.new_years_eve"]}


# ── 바깥에 내놓는 함수 ──

def holiday_keys(country: str, year: int) -> dict[date, list[str]]:
    """공휴일(쉬는 날)의 이름 키. 한 날에 둘이 겹치면 둘 다."""
    if not isinstance(year, int) or not 1900 <= year <= 2200:
        return {}
    if country == "KR":
        rows = _kr(year)
    elif country == "JP":
        rows = _jp(year)
    elif country == "US":
        rows = _us(year)
    else:
        return {}
    return {d: list(keys) for d, keys in rows}


def holidays(country: str, year: int, lang: str = "ko") -> dict[date, str]:
    """그 해의 공휴일(대체공휴일, 옮겨 쉬는 날 포함): 날짜 → 이름."""
    return _named(holiday_keys(country, year), lang)


def observance_keys(country: str, year: int) -> dict[date, list[str]]:
    if not isinstance(year, int) or not 1900 <= year <= 2200:
        return {}
    if country == "KR":
        return _kr_observances(year)
    if country == "JP":
        return _jp_observances(year)
    if country == "US":
        return _us_observances(year)
    return {}


def observances(country: str, year: int, lang: str = "ko") -> dict[date, str]:
    """쉬지 않는 기념일: 날짜 → 이름."""
    return _named(observance_keys(country, year), lang)


def is_business_day(d: date, country: str) -> bool:
    return d.weekday() < 5 and d not in holiday_keys(country, d.year)


def next_business_day(d: date, country: str) -> date:
    """d 가 영업일이면 d, 아니면 그다음 영업일(주말과 공휴일을 건너뛴다)."""
    for _ in range(40):
        if is_business_day(d, country):
            return d
        d += timedelta(days=1)
    return d


def tax_date_keys(country: str, year: int) -> dict[date, list[str]]:
    """세무 신고 기한. 기한이 주말이나 공휴일이면 다음 영업일로 옮긴다. 지금은 한국만."""
    if country != "KR" or not isinstance(year, int) or not 1900 <= year <= 2200:
        return {}
    items = [(date(year, m, 10), "kr.tax.withholding") for m in range(1, 13)]
    items += [(date(year, m, 25), "kr.tax.vat") for m in (1, 4, 7, 10)]
    items += [(date(year, 3, 31), "kr.tax.corporate"), (date(year, 5, 31), "kr.tax.income")]
    out: dict[date, list[str]] = {}
    for d, key in items:
        out.setdefault(next_business_day(d, country), []).append(key)
    return dict(sorted(out.items()))


def tax_dates(country: str, year: int, lang: str = "ko") -> dict[date, str]:
    return _named(tax_date_keys(country, year), lang)
