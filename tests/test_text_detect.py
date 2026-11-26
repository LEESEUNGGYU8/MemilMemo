"""본문 자동 인식(text_detect): 날짜와 시각, 계산 줄, 고른 글의 수, 연락처, #태그, @이름,
같은 줄 모아 세기. 화면 없이 규칙만 본다."""
from __future__ import annotations

import time
from datetime import datetime
from decimal import Decimal

import pytest

from app import text_detect as td

NOW = datetime(2026, 11, 25, 10, 0)         # 수요일 오전 10시


def at(y, mo, d, h=9, mi=0) -> datetime:
    return datetime(y, mo, d, h, mi)


def one(text: str, now: datetime = NOW, lang: str = "ko") -> td.DateHit:
    """글 전체가 날짜 하나로 읽혀야 한다."""
    hits = td.find_dates(text, now, lang)
    assert len(hits) == 1, (text, hits)
    hit = hits[0]
    assert (hit.start, hit.end) == (0, len(text)), (text, text[hit.start:hit.end])
    return hit


# ── 날짜와 시각: 예시 ──

EXAMPLES = [
    # 한국어
    ("내일", at(2026, 11, 26), False, True),
    ("모레", at(2026, 11, 27), False, True),
    ("오늘 오후 3시", at(2026, 11, 25, 15), True, True),
    ("25일 오전 10시", at(2026, 11, 25, 10), True, True),
    ("11월 25일", at(2026, 11, 25), False, True),
    ("11월 25일 (화)", at(2026, 11, 25), False, True),
    ("12월 3일 14:30", at(2026, 12, 3, 14, 30), True, True),
    ("11/25", at(2026, 11, 25), False, True),
    ("11/25 15:00", at(2026, 11, 25, 15), True, True),
    ("다음 주 화요일", at(2026, 12, 1), False, True),
    ("이번 주 금요일", at(2026, 11, 27), False, True),
    ("화요일 오후 2시", at(2026, 12, 1, 14), True, True),
    ("오후 2시 30분", at(2026, 11, 25, 14, 30), True, False),
    ("3시 반", at(2026, 11, 25, 15, 30), True, False),
    ("오전 9시", at(2026, 11, 26, 9), True, False),
    ("10:30", at(2026, 11, 25, 10, 30), True, False),
    ("2026년 12월 1일", at(2026, 12, 1), False, True),
    ("2026-12-01", at(2026, 12, 1), False, True),
    ("2026.12.1", at(2026, 12, 1), False, True),
    ("12.1(월)", at(2026, 12, 1), False, True),
    # 영어
    ("today", at(2026, 11, 25), False, True),
    ("tomorrow", at(2026, 11, 26), False, True),
    ("next Tuesday", at(2026, 12, 1), False, True),
    ("Friday 3pm", at(2026, 11, 27, 15), True, True),
    ("Nov 25", at(2026, 11, 25), False, True),
    ("November 25 at 10:30", at(2026, 11, 25, 10, 30), True, True),
    ("25 Nov", at(2026, 11, 25), False, True),
    ("3pm", at(2026, 11, 25, 15), True, False),
    ("3:30 pm", at(2026, 11, 25, 15, 30), True, False),
    # 일본어
    ("今日", at(2026, 11, 25), False, True),
    ("明日", at(2026, 11, 26), False, True),
    ("明後日", at(2026, 11, 27), False, True),
    ("来週火曜日", at(2026, 12, 1), False, True),
    ("11月25日", at(2026, 11, 25), False, True),
    ("午後3時", at(2026, 11, 25, 15), True, False),
    ("15時30分", at(2026, 11, 25, 15, 30), True, False),
    ("3時半", at(2026, 11, 25, 15, 30), True, False),
]


@pytest.mark.parametrize("text,when,has_time,has_date", EXAMPLES)
def test_the_examples(text, when, has_time, has_date):
    hit = one(text)
    assert (hit.when, hit.has_time, hit.has_date) == (when, has_time, has_date)


MORE = [
    ("내일모레", at(2026, 11, 27), False, True),
    ("글피", at(2026, 11, 28), False, True),
    ("다다음 주 월요일", at(2026, 12, 7), False, True),
    ("다음주 화요일", at(2026, 12, 1), False, True),
    ("이번주 월요일", at(2026, 11, 23), False, True),       # 지난 요일도 그대로
    ("this Friday", at(2026, 11, 27), False, True),
    ("this Wednesday", at(2026, 11, 25), False, True),      # 오늘부터 친다
    ("Tue, Nov 25, 2026 at 3pm", at(2026, 11, 25, 15), True, True),
    ("11월 25일 수요일 오후 3시", at(2026, 11, 25, 15), True, True),
    ("2026/12/01", at(2026, 12, 1), False, True),
    ("2026. 12. 1.", at(2026, 12, 1), False, True),
    ("11/25/2027", at(2027, 11, 25), False, True),
    ("Nov 25, 2027", at(2027, 11, 25), False, True),
    ("25 November 2027", at(2027, 11, 25), False, True),
    ("2026年12月1日", at(2026, 12, 1), False, True),
    ("明日の午後3時", at(2026, 11, 26, 15), True, True),
    ("来週の金曜日", at(2026, 12, 4), False, True),
    ("내일 3시", at(2026, 11, 26, 15), True, True),
    ("내일3시", at(2026, 11, 26, 15), True, True),
    ("오후 2:30", at(2026, 11, 25, 14, 30), True, False),
    ("10:30 AM", at(2026, 11, 25, 10, 30), True, False),
    ("12pm", at(2026, 11, 25, 12), True, False),
    ("12am", at(2026, 11, 26, 0), True, False),
    ("3 p.m.", at(2026, 11, 25, 15), True, False),
    ("tomorrow noon", at(2026, 11, 26, 12), True, True),
    ("정오", at(2026, 11, 25, 12), True, False),
    ("밤 12시", at(2026, 11, 26, 0), True, False),
    ("밤 9시", at(2026, 11, 25, 21), True, False),
    ("저녁 7시", at(2026, 11, 25, 19), True, False),
    ("１１/２５", at(2026, 11, 25), False, True),
    ("3/4 15:00", at(2027, 3, 4, 15), True, True),          # 분수 같아도 시각이 붙으면 날짜
    ("3/4(목)", at(2027, 3, 4), False, True),
]


@pytest.mark.parametrize("text,when,has_time,has_date", MORE)
def test_more_forms(text, when, has_time, has_date):
    hit = one(text)
    assert (hit.when, hit.has_time, hit.has_date) == (when, has_time, has_date)


# ── 날짜와 시각: 날짜가 아닌 것 ──

@pytest.mark.parametrize("text", [
    "12,000", "12,000원", "₩12,000", "$3.50", "가격 3.5", "12.1", "0.25",
    "1.0.4", "버전 1.0.4 배포", "v2.10.3", "1.12.1",
    "010-1234-5678", "02-123-4567", "+82 10-1234-5678", "1588-1234",
    "신한 110-123-456789", "seoyeon@example.com",
    "https://example.com/2026/11/25/post", "www.example.com/11/25",
    "3시간", "2시간 30분", "3일 동안", "3일간", "3日間", "3時間",
    "1/2 컵", "3/4", "16:9", "10:30:15", "24시 편의점",
    "오늘날", "you may 3 go", "#내일", "제1시장", "3시즌", "1,225일",
    "2026-02-30", "2/30", "13/13", "합계 25", "3",
])
def test_not_dates(text):
    assert td.find_dates(text, NOW) == []


def test_a_date_in_a_sentence_keeps_its_span():
    text = "다음 회의 25일 오전 10시에 봐요"
    hits = td.find_dates(text, NOW)
    assert [text[h.start:h.end] for h in hits] == ["25일 오전 10시"]
    assert hits[0].when == at(2026, 11, 25, 10)


def test_two_dates_in_one_line_stay_apart():
    text = "내일 3시, 모레 오전 10시"
    hits = td.find_dates(text, NOW)
    assert [text[h.start:h.end] for h in hits] == ["내일 3시", "모레 오전 10시"]
    assert [h.when for h in hits] == [at(2026, 11, 26, 15), at(2026, 11, 27, 10)]


def test_the_longest_candidate_wins():
    # '12월 1일' 과 '1일' 도 후보지만 가장 긴 것 하나만 남는다.
    assert one("2026년 12월 1일").when == at(2026, 12, 1)
    # 범위는 두 날짜다.
    text = "11/25~11/27 휴가"
    assert [text[h.start:h.end] for h in td.find_dates(text, NOW)] == ["11/25", "11/27"]


def test_a_line_break_does_not_join_a_date_and_a_time():
    hits = td.find_dates("내일\n3시", NOW)
    assert [(h.has_date, h.has_time) for h in hits] == [(True, False), (False, True)]


def test_a_date_without_a_year_is_the_next_one_on_or_after_today():
    assert one("11/25").when == at(2026, 11, 25)                   # 오늘은 친다
    tomorrow = datetime(2026, 11, 26, 10, 0)
    assert one("11/25", tomorrow).when == at(2027, 11, 25)         # 지났으면 내년
    assert one("11월 25일", tomorrow).when == at(2027, 11, 25)
    assert one("Nov 25", tomorrow).when == at(2027, 11, 25)
    assert one("1월 5일").when == at(2027, 1, 5)
    assert one("2/29").when == at(2028, 2, 29)                     # 다음 윤년
    assert td.find_dates("2월 30일", NOW) == []


def test_a_written_year_is_kept_even_if_past():
    assert one("2025-01-02").when == at(2025, 1, 2)


def test_a_bare_day_is_this_month_or_the_next_and_clamps_to_month_end():
    assert one("25일").when == at(2026, 11, 25)
    assert one("31일").when == at(2026, 11, 30)                    # 11월은 30일까지
    assert one("20일").when == at(2026, 12, 20)                    # 지났으면 다음 달
    jan31 = datetime(2026, 1, 31, 12, 0)
    assert one("30일", jan31).when == at(2026, 2, 28)              # 2월은 28일까지
    dec20 = datetime(2026, 12, 20, 12, 0)
    assert one("10일", dec20).when == at(2027, 1, 10)              # 해를 넘긴다


def test_a_weekday_alone_skips_today_unless_a_future_time_is_given():
    assert one("수요일").when == at(2026, 12, 2)
    assert one("Wednesday").when == at(2026, 12, 2)
    assert one("수요일 오후 3시").when == at(2026, 11, 25, 15)
    assert one("수요일 오전 9시").when == at(2026, 12, 2, 9)       # 오늘 9시는 지났다
    assert one("목요일").when == at(2026, 11, 26)


def test_a_time_alone_is_today_or_tomorrow():
    assert one("10:00").when == at(2026, 11, 26, 10)               # 지금과 같으면 내일
    assert one("10:01").when == at(2026, 11, 25, 10, 1)
    assert one("오전 9시").when == at(2026, 11, 26, 9)
    assert one("오늘 오전 9시").when == at(2026, 11, 25, 9)        # 날짜를 적으면 그대로


@pytest.mark.parametrize("text,hour,day", [
    ("1시", 13, 25), ("6시", 18, 25), ("7시", 7, 26), ("12시", 12, 25),
    ("13시", 13, 25), ("23시", 23, 25), ("0시", 0, 26),
    ("3:00", 15, 25), ("03:00", 3, 26), ("9:15", 9, 26), ("11:00", 11, 25),
])
def test_hours_without_am_or_pm(text, hour, day):
    when = one(text).when
    assert (when.day, when.hour) == (day, hour)


def test_lang_only_breaks_ties_in_slash_dates():
    for lang in ("ko", "ja", "en"):
        assert one("11/12", lang=lang).when == at(2027, 11, 12)    # 11월 12일은 지났다
    assert one("11/12", lang="en-GB").when == at(2026, 12, 11)
    for lang in ("ko", "en-GB"):
        assert one("25/11", lang=lang).when == at(2026, 11, 25)    # 25 는 달이 될 수 없다


def test_english_case_rules():
    assert one("NOV 25").when == at(2026, 11, 25)
    assert one("november 25").when == at(2026, 11, 25)
    assert td.find_dates("we may 5 people", NOW) == []
    assert one("May 5").when == at(2027, 5, 5)
    assert td.find_dates("the afternoon", NOW) == []


def test_dates_inside_urls_contacts_and_tags_are_skipped():
    text = "https://x.com/2026/11/25 #내일 2023-12-15-1234 내일"
    hits = td.find_dates(text, NOW)
    assert [text[h.start:h.end] for h in hits] == ["내일"]


# ── 계산 줄 ──

@pytest.mark.parametrize("line,value,text", [
    ("점심 12,000 + 8,500 =", "20500", "20,500"),
    ("합계: (3+4)×2 =", "14", "14"),
    ("0.1+0.2=", "0.3", "0.3"),
    ("1.1 * 3 =", "3.3", "3.3"),
    ("100 + 10% =", "110", "110"),
    ("100 - 10% =", "90", "90"),
    ("200 * 15% =", "30", "30"),
    ("200 × 15% =", "30", "30"),
    ("50% + 1 =", "1.5", "1.5"),
    ("(100 + 100)% * 3 =", "6", "6"),
    ("3 x 4 =", "12", "12"),
    ("3x4=", "12", "12"),
    ("3 X (1+1) =", "6", "6"),
    ("12 ÷ 4 =", "3", "3"),
    ("1/3 =", None, "0.333333"),
    ("2/3=", None, "0.666667"),
    ("-3 + 5 =", "2", "2"),
    ("2 * -3 =", "-6", "-6"),
    ("(1 + 2) * (3 + 4) =", "21", "21"),
    ("1 + 2 * 3 =", "7", "7"),
    ("10 - 2 - 3 =", "5", "5"),
    ("1,000 + 1 =", "1001", "1,001"),           # 쉼표를 썼으면 결과에도
    ("5000 * 3 =", "15000", "15,000"),          # 다섯 자리부터 쉼표
    ("1000 + 2000 =", "3000", "3000"),
    ("-12000 - 500 =", "-12500", "-12,500"),
    ("12,000원 + 8,500원 =", "20500", "20,500"),
    ("$3.50 * 2 =", "7", "7"),
    ("3 + 4 =   ", "7", "7"),
    ("x = 3 + 4 =", "7", "7"),
    ("A-1 3+4 =", "7", "7"),
    ("１＋２＝", "3", "3"),
    ("10 − 4 =", "6", "6"),
    (".5 + .25 =", "0.75", "0.75"),
])
def test_calc_lines(line, value, text):
    got = td.calc_line(line)
    assert got is not None, line
    if value is not None:
        assert got.value == Decimal(value)
    assert got.text == text


def test_calc_reports_where_the_expression_starts():
    line = "점심 12,000 + 8,500 ="
    assert td.calc_line(line).expr_start == line.index("12,000")
    line = "합계: (3+4)×2 ="
    assert td.calc_line(line).expr_start == line.index("(")
    assert td.calc_line("3+4=").expr_start == 0


def test_calc_is_exact():
    got = td.calc_line("0.1 + 0.2 =")
    assert got.value == Decimal("0.3") and isinstance(got.value, Decimal)
    assert td.calc_line("0.1 * 3 - 0.3 =").text == "0"
    assert td.calc_line("1 / 3 * 3 =").text == "1"


@pytest.mark.parametrize("line", [
    "3 + 4",                # '=' 가 없다
    "3 + 4 = 7",            # 이미 결과가 있다
    "=",
    "합계 =",
    "12,000 =",             # 수 하나
    "-5 =",
    "50% =",
    "(3) =",
    "10 / 0 =",
    "0 / 0 =",
    "1 + =",
    "* 3 =",
    "1 + 2) =",
    "1,2 + 3 =",            # 쉼표 묶음이 맞지 않는다
    "1 2 + =",
    "3 x =",
    "3 + 4 ==",
    "10%% + 1 =",
])
def test_not_calculations(line):
    assert td.calc_line(line) is None


def test_calc_survives_hostile_lines():
    assert td.calc_line("(" * 100 + "1" + ")" * 100 + "+1=") is None
    td.calc_line("-" * 200 + "1+1=")                # 터지지만 않으면 된다
    assert td.calc_line("1+" * 10000 + "1=") is None  # 한도보다 긴 식은 일부만 계산하지 않는다
    assert td.calc_line("9" * 200 + " * " + "9" * 40 + "=") is not None


def test_format_number():
    f = td.format_number
    assert f(Decimal("1234")) == "1234"
    assert f(Decimal("1234"), commas=True) == "1,234"
    assert f(Decimal("12345.50")) == "12,345.5"
    assert f(Decimal("-12345")) == "-12,345"
    assert f(Decimal("2.0")) == "2"
    assert f(Decimal("0.0000005")) == "0.000001"
    assert f(Decimal("-0.0000001")) == "0"
    assert f(Decimal("1E+3")) == "1000"
    assert f(Decimal("1E+30")) == "1,000,000,000,000,000,000,000,000,000,000"


# ── 고른 글의 수 ──

def test_summary_of_amounts():
    s = td.summarize_numbers("점심 12,000\n커피 8,500\n택시 3,000")
    assert (s.count, s.total) == (3, Decimal("23500"))
    assert s.total_text == "23,500"
    assert s.average_text == "7,833.333333"
    assert s.values == (Decimal("12000"), Decimal("8500"), Decimal("3000"))


def test_summary_is_exact_and_keeps_negatives():
    assert td.summarize_numbers("0.1 0.2").total == Decimal("0.3")
    s = td.summarize_numbers("수입 1000, 지출 -500")
    assert s.total == Decimal("500") and s.average == Decimal("250")
    s = td.summarize_numbers("1 2")
    assert (s.total_text, s.average_text) == ("3", "1.5")


def test_summary_skips_dates_times_phones_versions_and_more():
    text = ("11월 25일 회의 3시, 10:30, 11/25, 010-1234-5678, 버전 1.0.4, "
            "https://x.com/12/34 #회의3 A4 1/2 2026년 30분 금액 1,000 2,000")
    s = td.summarize_numbers(text)
    assert s.values == (Decimal("1000"), Decimal("2000"))


def test_summary_needs_two_numbers():
    assert td.summarize_numbers("") is None
    assert td.summarize_numbers("합계 12,000") is None
    assert td.summarize_numbers("11월 25일 3시") is None


# ── 연락처 ──

def test_phone_numbers():
    text = ("010-1234-5678 01012345678 010.1234.5678 02-123-4567 031-123-4567 "
            "070-1234-5678 (02) 123-4567 1588-1234 +82 10-1234-5678 +82 010-1234-5678")
    hits = td.find_contacts(text)
    assert all(h.kind == td.PHONE for h in hits)
    assert [h.value for h in hits] == [
        "010-1234-5678", "010-1234-5678", "010-1234-5678", "02-123-4567", "031-123-4567",
        "070-1234-5678", "02-123-4567", "1588-1234", "+82-10-1234-5678", "+82-10-1234-5678"]
    for h in hits:
        assert text[h.start:h.end].strip() == text[h.start:h.end]


def test_emails():
    text = "메일 Seo.Yeon+memo@Example.CO.KR 로, 또는 a_b@x.io."
    hits = td.find_contacts(text)
    assert [(h.kind, h.value) for h in hits] == [
        (td.EMAIL, "Seo.Yeon+memo@example.co.kr"), (td.EMAIL, "a_b@x.io")]
    assert text[hits[1].start:hits[1].end] == "a_b@x.io"


def test_a_phone_inside_an_email_is_the_email():
    hits = td.find_contacts("01012345678@gmail.com")
    assert [(h.kind, h.value) for h in hits] == [(td.EMAIL, "01012345678@gmail.com")]


def test_bank_accounts():
    text = ("국민은행 123456-01-123456\n신한 110-123-456789\n카카오뱅크 3333-01-1234567\n"
            "농협: 352-1234-5678-93\n토스뱅크 계좌 1000-1234-5678\n110-123-456789")
    hits = td.find_contacts(text)
    assert [(h.kind, h.value, h.bank) for h in hits] == [
        (td.ACCOUNT, "12345601123456", "국민"),
        (td.ACCOUNT, "110123456789", "신한"),
        (td.ACCOUNT, "3333011234567", "카카오뱅크"),
        (td.ACCOUNT, "3521234567893", "농협"),
        (td.ACCOUNT, "100012345678", "토스뱅크"),
        (td.ACCOUNT, "110123456789", ""),
    ]
    assert text[hits[0].start:hits[0].end] == "123456-01-123456"


def test_business_number_shape_needs_a_bank_name():
    assert td.find_contacts("사업자 123-45-67890") == []
    hits = td.find_contacts("기업은행 123-45-67890")
    assert [(h.kind, h.value, h.bank) for h in hits] == [(td.ACCOUNT, "1234567890", "기업")]


@pytest.mark.parametrize("text", [
    "2026-12-01", "1234-5678-9012-3456", "1500-2000원", "12,000", "1.0.4",
    "978-89-1234-567-8", "11/25 15:00", "123-4567", "010-1234-56789",
])
def test_not_contacts(text):
    assert td.find_contacts(text) == []


def test_fullwidth_phone():
    hits = td.find_contacts("전화 ０１０－１２３４－５６７８")
    assert [h.value for h in hits] == ["010-1234-5678"]


# ── #태그 ──

def test_tags():
    text = "#회계 #거래처, 회의 #회의2 #tag_1 #日本語 #cafe"
    hits = td.find_tags(text)
    assert [h.tag for h in hits] == ["회계", "거래처", "회의2", "tag_1", "日本語", "cafe"]
    for h in hits:
        assert text[h.start:h.end] == "#" + h.tag


@pytest.mark.parametrize("text", [
    "#FFF", "#fff", "#A1B2C3", "#a1b2c3", "#FFFF", "#11223344", "#1", "#2024", "#_",
    "&#123;", "a#b", "가#나", "# 제목", "##제목", "https://example.com/page#section",
])
def test_not_tags(text):
    assert td.find_tags(text) == []


def test_tags_in_is_unique_and_case_insensitive():
    assert td.tags_in("#Tag 하나 #tag 둘 #회계 #TAG #회계 #other") == ["Tag", "회계", "other"]
    assert td.tags_in("") == []


# ── @이름 ──

def test_mentions_take_the_longest_known_name():
    text = "@서연이 견적서 확인, @서연님 고마워요"
    hits = td.find_mentions(text, ["서연", "서연이"])
    assert [(h.name, text[h.start:h.end]) for h in hits] == [("서연이", "@서연이"), ("서연", "@서연")]


def test_mentions_only_known_names_and_not_emails():
    names = ["서연", "Ann"]
    assert td.find_mentions("@지우 @하준", names) == []
    assert td.find_mentions("me@서연.com a@Ann", names) == []
    assert [h.name for h in td.find_mentions("(@서연) @ann, @Anna", names)] == ["서연", "Ann"]
    assert td.find_mentions("@서연", []) == []
    assert td.find_mentions("@서연", ["", "  "]) == []
    assert td.find_mentions("끝@", names) == []
    assert [h.name for h in td.find_mentions("@@서연", names)] == ["서연"]


# ── 같은 줄 모아 세기 ──

def test_tally_lines_groups_items():
    rows = td.tally_lines([
        "김치찌개 2 (지우, 서연)",
        "제육볶음 1 (하준)",
        "김치찌개 1 (민준)",
        "아메리카노 3잔",
    ])
    assert rows == [
        td.TallyRow("김치찌개", 3, "", ["지우", "서연", "민준"]),
        td.TallyRow("제육볶음", 1, "", ["하준"]),
        td.TallyRow("아메리카노", 3, "잔", []),
    ]


def test_tally_skips_lines_that_do_not_fit():
    rows = td.tally_lines([
        "점심 주문", "", "합계 20,500", "3개월", "(지우)", "점심 12,000 + 8,500 =",
        "- 콜라 x2 (지우)", "• Coke 1", "1. 콜라 3 (서연 하준)", "■ 김밥 2인분",
        "버전 1.0.4", "コーヒー 2杯",
    ])
    assert rows == [
        td.TallyRow("콜라", 5, "", ["지우", "서연", "하준"]),
        td.TallyRow("Coke", 1, "", []),
        td.TallyRow("김밥", 2, "인분", []),
        td.TallyRow("コーヒー", 2, "杯", []),
    ]


def test_tally_groups_case_and_spacing_and_keeps_names_once():
    rows = td.tally_lines(["Ice  Latte 2 (Ann)", "ice latte 1 (Ann, Bo)", "Ice Latte x3"])
    assert rows == [td.TallyRow("Ice Latte", 6, "", ["Ann", "Bo"])]


def test_tally_needs_two_parsed_lines():
    assert td.tally_lines(["김치찌개 2 (지우)"]) == []
    assert td.tally_lines(["김치찌개 2 (지우)", "오늘 점심"]) == []
    assert td.tally_lines([]) == []


# ── 빠르기 ──

_CHUNK = ("다음 회의 25일 오전 10시, 내일 3시 #회계 @서연 견적서. 점심 12,000 + 8,500 =\n"
          "버전 1.0.4 배포 010-1234-5678 a@b.com 신한 110-123-456789 Friday 3pm 11/25 15:00\n"
          "김치찌개 2 (지우, 서연)\n아메리카노 3잔 https://example.com/a/b#c 2026.12.1 합계 3,000\n")
_BIG = (_CHUNK * (20000 // len(_CHUNK) + 1))[:20000]
_NAMES = ["서연", "서연이", "지우"] + [f"user{i}" for i in range(100)]

_FINDERS = {
    "dates": lambda s: td.find_dates(s, NOW),
    "calc": lambda s: [td.calc_line(line) for line in s.split("\n")],
    "calc_one_line": lambda s: td.calc_line(s + "="),
    "summary": td.summarize_numbers,
    "contacts": td.find_contacts,
    "tags": td.find_tags,
    "tags_in": td.tags_in,
    "mentions": lambda s: td.find_mentions(s, _NAMES),
    "tally": lambda s: td.tally_lines(s.split("\n")),
}


def _best_of_three(fn, arg) -> float:
    best = float("inf")
    for _ in range(3):
        t0 = time.perf_counter()
        fn(arg)
        best = min(best, time.perf_counter() - t0)
    return best


@pytest.mark.parametrize("name", list(_FINDERS))
@pytest.mark.parametrize("text", [
    _BIG, "1" * 20000, "a" * 20000, "가" * 20000, "@" * 20000, "#" * 20000,
    "1." * 10000, "1-" * 10000, "1/" * 10000, "1:" * 10000, "a." * 10000,
    "a@" + "b." * 9999, "3 " * 10000, "1x" * 10000, "(" * 20000,
], ids=["mixed", "digits", "letters", "hangul", "at", "hash", "dots", "dashes", "slashes",
        "colons", "dotted", "email", "spaced", "times", "parens"])
def test_each_finder_is_fast_on_20k_chars(name, text):
    assert len(text) >= 19999
    assert _best_of_three(_FINDERS[name], text) < 0.1


def test_the_mixed_text_finds_things():
    assert len(td.find_dates(_BIG, NOW)) > 100
    assert len(td.find_contacts(_BIG)) > 100
    assert td.tags_in(_BIG) == ["회계"]
    assert td.calc_line("점심 12,000 + 8,500 =").text == "20,500"
    assert td.tally_lines(_BIG.split("\n"))[0].item == "김치찌개"
