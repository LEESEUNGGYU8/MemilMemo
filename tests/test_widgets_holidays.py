"""달력 위젯의 공휴일 표(한국, 일본, 미국), 기념일, 세무 신고일, 달력 데이터와 동작.

한국의 2026~2028 년 기대값은 한국천문연구원 월력요항 자료와 같다(2026 년은 그 뒤 공휴일이
된 노동절을 더했다). 화면 없이 돈다."""
from __future__ import annotations

from datetime import date

from app.widgets import calendar as cal
from app.widgets import holidays as h

ME = "지우"


def D(y, m, d):
    return date(y, m, d)


def _dates(table):
    return sorted(table)


# ── 한국 ──

def test_korea_2026_matches_the_official_table_with_labor_and_constitution_day():
    got = h.holiday_keys("KR", 2026)
    expected = [D(2026, 1, 1), D(2026, 2, 16), D(2026, 2, 17), D(2026, 2, 18), D(2026, 3, 1),
                D(2026, 3, 2), D(2026, 5, 1), D(2026, 5, 5), D(2026, 5, 24), D(2026, 5, 25),
                D(2026, 6, 3), D(2026, 6, 6), D(2026, 7, 17), D(2026, 8, 15), D(2026, 8, 17),
                D(2026, 9, 24), D(2026, 9, 25), D(2026, 9, 26), D(2026, 10, 3), D(2026, 10, 5),
                D(2026, 10, 9), D(2026, 12, 25)]
    assert _dates(got) == expected
    subs = [d for d, keys in got.items() if "kr.substitute" in keys]
    assert subs == [D(2026, 3, 2), D(2026, 5, 25), D(2026, 8, 17), D(2026, 10, 5)]
    names = h.holidays("KR", 2026)
    assert names[D(2026, 5, 1)] == "노동절" and names[D(2026, 7, 17)] == "제헌절"
    assert names[D(2026, 6, 3)] == "지방선거일" and names[D(2026, 2, 17)] == "설날"
    assert names[D(2026, 9, 26)] == "추석 연휴"
    # 추석 연휴의 토요일과 토요일인 현충일은 대체공휴일이 없다.
    assert D(2026, 9, 28) not in got and D(2026, 6, 8) not in got


def test_korea_2027_substitutes():
    got = h.holiday_keys("KR", 2027)
    subs = [d for d, keys in got.items() if "kr.substitute" in keys]
    assert subs == [D(2027, 2, 9), D(2027, 5, 3), D(2027, 7, 19), D(2027, 8, 16),
                    D(2027, 10, 4), D(2027, 10, 11), D(2027, 12, 27)]
    assert len(got) == 24
    assert D(2027, 6, 7) not in got                      # 일요일인 현충일도 없다


def test_korea_2028_overlap_and_election():
    got = h.holidays("KR", 2028)
    assert got[D(2028, 10, 3)] == "개천절, 추석"
    assert got[D(2028, 10, 5)] == "대체공휴일"            # 겹친 날 하나에 하루
    assert D(2028, 10, 6) not in got
    assert got[D(2028, 4, 12)] == "국회의원선거일"
    assert D(2028, 1, 3) not in got                       # 토요일인 신정은 대체공휴일이 없다


def test_korea_later_years_follow_the_same_rules():
    assert h.holidays("KR", 2029)[D(2029, 9, 24)] == "대체공휴일"   # 추석 연휴의 일요일
    assert h.holidays("KR", 2029)[D(2029, 5, 7)] == "대체공휴일"    # 토요일 어린이날
    assert h.holidays("KR", 2029)[D(2029, 5, 21)] == "대체공휴일"   # 일요일 부처님오신날
    assert h.holidays("KR", 2030)[D(2030, 2, 5)] == "대체공휴일"    # 일요일 설날
    assert h.holidays("KR", 2032)[D(2032, 9, 21)] == "대체공휴일"
    assert h.covered("KR", 2026) and h.covered("KR", 2032)
    assert not h.covered("KR", 2025) and not h.covered("KR", 2033)
    # 표가 없는 해도 날짜가 정해진 공휴일은 보인다.
    assert D(2033, 3, 1) in h.holidays("KR", 2033)


def test_holiday_names_in_three_languages():
    assert h.holidays("KR", 2026, "en")[D(2026, 9, 25)] == "Chuseok"
    assert h.holidays("KR", 2026, "ja")[D(2026, 10, 9)] == "ハングルの日"
    assert h.holidays("JP", 2026, "ko")[D(2026, 5, 5)] == "어린이날"
    assert h.holidays("US", 2026, "ja")[D(2026, 7, 3)] == "独立記念日 振替休日"
    assert h.holidays("US", 2026, "ko")[D(2026, 7, 3)] == "독립기념일 대체 휴일"
    for key in h._NAMES:
        assert all(h._NAMES[key]), key


# ── 일본 ──

def test_japan_2026_equinoxes_and_the_silver_week():
    got = h.holidays("JP", 2026, "ja")
    assert got[D(2026, 3, 20)] == "春分の日" and got[D(2026, 9, 23)] == "秋分の日"
    assert got[D(2026, 9, 21)] == "敬老の日" and got[D(2026, 9, 22)] == "国民の休日"
    assert got[D(2026, 5, 6)] == "振替休日"                # 憲法記念日가 일요일
    assert got[D(2026, 1, 12)] == "成人の日" and got[D(2026, 10, 12)] == "スポーツの日"
    assert got[D(2026, 7, 20)] == "海の日"
    assert len(got) == 18
    assert h.vernal_equinox(2027) == D(2027, 3, 21)
    assert h.holidays("JP", 2027, "ja")[D(2027, 3, 22)] == "振替休日"
    assert h.autumnal_equinox(2028) == D(2028, 9, 22)
    assert h.holidays("JP", 2019) == {}                  # 바뀌기 전 해는 다루지 않는다


# ── 미국 ──

def test_usa_2026_and_2027_observed_days():
    got = h.holidays("US", 2026, "en")
    assert got[D(2026, 7, 4)] == "Independence Day"
    assert got[D(2026, 7, 3)] == "Independence Day (observed)"
    assert got[D(2026, 1, 19)] == "Martin Luther King Jr. Day"
    assert got[D(2026, 5, 25)] == "Memorial Day" and got[D(2026, 11, 26)] == "Thanksgiving Day"
    assert got[D(2026, 6, 19)] == "Juneteenth"
    later = h.holidays("US", 2027, "en")
    assert later[D(2027, 6, 18)] == "Juneteenth (observed)"
    assert later[D(2027, 7, 5)] == "Independence Day (observed)"
    assert later[D(2027, 12, 24)] == "Christmas Day (observed)"
    # 2028 년 1월 1일이 토요일이라 2027 년 12월 31일에 쉰다.
    assert later[D(2027, 12, 31)] == "New Year's Day (observed)"
    assert D(2027, 12, 31) not in h.holidays("US", 2028)


# ── 기념일, 영업일, 세무 신고일 ──

def test_observances_are_separate_from_days_off():
    kr = h.observances("KR", 2026)
    assert kr[D(2026, 5, 8)] == "어버이날" and kr[D(2026, 5, 15)] == "스승의 날"
    assert D(2026, 5, 8) not in h.holidays("KR", 2026)
    assert h.observances("JP", 2026, "ja")[D(2026, 5, 10)] == "母の日"
    assert h.observances("US", 2026, "en")[D(2026, 6, 21)] == "Father's Day"


def test_next_business_day_skips_weekends_and_holidays():
    assert h.next_business_day(D(2026, 11, 25), "KR") == D(2026, 11, 25)
    assert h.next_business_day(D(2026, 9, 24), "KR") == D(2026, 9, 28)    # 추석과 주말
    assert h.next_business_day(D(2026, 7, 3), "US") == D(2026, 7, 6)
    assert h.next_business_day(D(2026, 5, 5), "JP") == D(2026, 5, 7)
    assert not h.is_business_day(D(2026, 5, 1), "KR")


def test_korean_tax_dates_move_to_the_next_business_day():
    got = h.tax_dates("KR", 2026)
    assert got[D(2026, 1, 12)] == "원천세 신고"          # 1월 10일이 토요일
    assert got[D(2026, 1, 26)] == "부가세 신고"          # 1월 25일이 일요일
    assert got[D(2026, 3, 31)] == "법인세 신고"
    assert got[D(2026, 6, 1)] == "종합소득세 신고"        # 5월 31일이 일요일
    assert got[D(2026, 10, 12)] == "원천세 신고"
    assert len([d for d, n in got.items() if "원천세" in n]) == 12
    # 2027 년 10월 10일은 일요일, 11일은 한글날 대체공휴일이라 12일이다.
    assert h.tax_dates("KR", 2027)[D(2027, 10, 12)] == "원천세 신고"
    assert h.tax_dates("KR", 2026, "en")[D(2026, 3, 31)] == "Corporate tax return"
    assert h.tax_dates("JP", 2026) == {} and h.tax_dates("US", 2026) == {}
    assert h.holidays("XX", 2026) == {} and h.holidays("KR", 99999) == {}


# ── 달력 위젯의 데이터 ──

def test_calendar_packs_country_and_days_off():
    kind = cal.CALENDAR
    data = kind.new_data(ME)
    assert data["packs"] == {"holidays": True, "observances": False, "tax": False}
    data, err = kind.apply_op(data, {"op": "set_pack", "pack": "tax", "on": True}, "서연", 0)
    assert err is None and data["packs"]["tax"] is True
    assert kind.apply_op(data, {"op": "set_pack", "pack": "tax", "on": 1}, ME, 0)[1] == "invalid"
    assert kind.apply_op(data, {"op": "set_pack", "pack": "x", "on": True}, ME, 0)[1] == "invalid"
    data, err = kind.apply_op(data, {"op": "add_day_off", "date": "2026-11-27",
                                     "label": "재고 실사"}, ME, 0)
    data, err = kind.apply_op(data, {"op": "add_day_off", "date": "2026-11-20"}, ME, 0)
    assert [d["date"] for d in data["days_off"]] == ["2026-11-20", "2026-11-27"]
    data, err = kind.apply_op(data, {"op": "add_day_off", "date": "2026-11-27",
                                     "label": "실사"}, ME, 0)
    assert data["days_off"][1]["label"] == "실사" and len(data["days_off"]) == 2
    assert kind.apply_op(data, {"op": "add_day_off", "date": "11/27"}, ME, 0)[1] == "invalid"
    data, err = kind.apply_op(data, {"op": "remove_day_off", "date": "2026-11-20"}, ME, 0)
    assert [d["date"] for d in data["days_off"]] == ["2026-11-27"]
    data, err = kind.apply_op(data, {"op": "set_country", "country": "JP"}, ME, 0)
    assert data["country"] == "JP"
    assert kind.apply_op(data, {"op": "set_country", "country": "FR"}, ME, 0)[1] == "invalid"
    full = dict(data, days_off=[{"date": f"2027-{m:02d}-{d:02d}", "label": ""}
                                for m in range(1, 5) for d in range(1, 26)])
    assert kind.apply_op(full, {"op": "add_day_off", "date": "2028-01-01"}, ME, 0)[1] == "limit"


def test_calendar_month_events_in_order():
    kind = cal.CALENDAR
    data = kind.new_data(ME, packs={"observances": True, "tax": True})
    data = kind.apply_op(data, {"op": "add_day_off", "date": "2026-05-04", "label": ""}, ME, 0)[0]
    events = cal.month_events(data, D(2026, 5, 1), "ko")
    assert events[:4] == [(D(2026, 5, 1), cal.HOLIDAY, "노동절"),
                          (D(2026, 5, 4), cal.DAY_OFF, "쉬는 날"),
                          (D(2026, 5, 5), cal.HOLIDAY, "어린이날"),
                          (D(2026, 5, 8), cal.OBSERVANCE, "어버이날")]
    assert (D(2026, 5, 11), cal.TAX, "원천세 신고") in events
    off = kind.apply_op(data, {"op": "set_pack", "pack": "holidays", "on": False}, ME, 0)[0]
    assert all(kind_ != cal.HOLIDAY for _d, kind_, _n in cal.month_events(off, D(2026, 5, 1)))
    assert cal.first_cell(D(2026, 11, 1)) == D(2026, 11, 1)          # 일요일
    assert cal.first_cell(D(2026, 12, 1)) == D(2026, 11, 29)
    assert cal.shift_month(D(2026, 12, 1), 1) == D(2027, 1, 1)
    assert cal.shift_month(D(2026, 1, 1), -1) == D(2025, 12, 1)


def test_calendar_validate_cleans_hostile_data():
    raw = {"kind": "calendar", "country": "../KR", "packs": {"holidays": "no", "tax": True,
                                                             "evil": True},
           "days_off": [{"date": "2026-02-30"}, {"date": "2026-12-24", "label": "x" * 999},
                        {"date": "2026-12-24", "label": "dup"}, "bad", None]
           + [{"date": "2027-01-01"}] * 100_000}
    clean = cal.CALENDAR.validate(raw)
    assert clean["country"] == "KR"
    assert clean["packs"] == {"holidays": True, "observances": False, "tax": True}
    assert clean["days_off"] == [{"date": "2026-12-24", "label": "x" * 60},
                                 {"date": "2027-01-01", "label": ""}]
