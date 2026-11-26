"""메모 안 위젯의 데이터와 동작: 종류 목록, 공유 PC 가 적용하는 동작, 권한, 받은 데이터 확인,
크기 한도. 화면 없이 돈다."""
from __future__ import annotations

import copy
import json

import pytest

from app import widgets
from app.widgets import base, dday, graph, ladder, omok, order, vote

ME = "지우"
OTHER = "서연"
THIRD = "하준"


def apply(data, op, user=ME, now_ms=1000):
    return widgets.get(data["kind"]).apply_op(data, op, user, now_ms)


def ok(data, op, user=ME):
    new, err = apply(data, op, user)
    assert err is None, err
    return new


# ── 공통 ──

def test_the_registry_lists_every_kind_with_its_metadata():
    assert widgets.kinds() == ("vote", "order", "ladder", "dday", "calendar", "graph", "omok")
    assert [k for k in widgets.kinds() if widgets.get(k).is_game] == ["omok"]
    assert {k for k in widgets.kinds() if widgets.get(k).bg_option} == {
        "vote", "dday", "calendar", "graph"}
    assert widgets.get("nope") is None and widgets.get(5) is None
    assert widgets.kind_of({"kind": "vote"}) is widgets.get("vote")


def test_new_data_has_the_common_fields_and_validates_to_itself():
    for name in widgets.kinds():
        kind = widgets.get(name)
        data = kind.new_data(ME)
        assert (data["kind"], data["v"], data["owner"], data["bg"]) == (name, 1, ME, "white")
        assert kind.validate(data) == data
        assert base.json_size(data) < widgets.MAX_BYTES
        assert widgets.validate_any(data) == data


def _sample_ops():
    v = vote.VOTE.new_data(ME, question="점심", options=["국밥", "냉면"])
    o = order.ORDER.new_data(ME, menu=["김치찌개"])
    lad = ladder.LADDER.new_data(ME, participants=[ME, OTHER], results=["꽝", "커피"])
    d = dday.DDAY.new_data(ME, label="박람회", date="2026-12-05")
    c = widgets.get("calendar").new_data(ME)
    g = graph.GRAPH.new_data(ME, labels=["월"], series=[{"name": "a", "values": [1]}])
    om = omok.OMOK.new_data(ME)
    return [(v, {"op": "vote", "option": 1}), (o, {"op": "set_my_order", "item": "라면", "qty": 2}),
            (lad, {"op": "start", "seed": 7}), (d, {"op": "set", "label": "전시"}),
            (c, {"op": "add_day_off", "date": "2026-12-24", "label": "휴무"}),
            (g, {"op": "set_kind", "chart": "line"}), (om, {"op": "move", "x": 7, "y": 7})]


def test_apply_op_never_changes_its_input():
    for data, op in _sample_ops():
        before, op_before = copy.deepcopy(data), copy.deepcopy(op)
        new, err = apply(data, op)
        assert err is None and new != data
        assert data == before and op == op_before
        # 거절해도 입력은 그대로다.
        _same, err = apply(data, {"op": "no_such_op"})
        assert err == "bad_op" and data == before


def test_bad_ops_and_unknown_people_are_refused():
    data = vote.VOTE.new_data(ME, options=["가"])
    for op in (None, "vote", {}, {"op": 3}, {"op": "explode"}):
        assert apply(data, op)[1] == "bad_op"
    assert apply(data, {"op": "vote", "option": 1}, user="")[1] == "not_allowed"
    assert apply(data, {"op": "vote", "option": 1}, user=None)[1] == "not_allowed"
    assert widgets.apply_any({"kind": "nope"}, {"op": "x"}, ME, 0)[1] == "invalid"


def test_validate_refuses_other_kinds_newer_data_and_non_dicts():
    kind = vote.VOTE
    for raw in (None, [], "vote", {"kind": "order"}):
        with pytest.raises(widgets.WidgetError) as e:
            kind.validate(raw)
        assert e.value.key == "invalid"
    with pytest.raises(widgets.WidgetError) as e:
        kind.validate(dict(kind.new_data(ME), v=2))
    assert e.value.key == "newer"
    # 더 새 데이터에는 동작을 적용하지 않는다(모르는 칸을 잃지 않게).
    newer = dict(kind.new_data(ME, options=["가"]), v=2, extra="x")
    same, err = kind.apply_op(newer, {"op": "vote", "option": 1}, ME, 0)
    assert err == "newer" and same is newer


def test_text_is_trimmed_and_control_and_direction_characters_are_dropped():
    assert base.clean_text("  a\x00b\nc\u202ed\u2066e  ") == "abcde"
    assert base.clean_text("x" * 10_000) == "x" * 200
    assert base.clean_text(42) == "" and base.clean_name("n" * 100) == "n" * 64


def test_background_option_only_where_offered():
    data = vote.VOTE.new_data(ME)
    assert ok(data, {"op": "set_bg", "bg": "clear"})["bg"] == "clear"
    assert apply(data, {"op": "set_bg", "bg": "pink"})[1] == "invalid"
    game = omok.OMOK.new_data(ME)
    assert apply(game, {"op": "set_bg", "bg": "clear"})[1] == "bad_op"
    assert omok.OMOK.validate(dict(game, bg="clear"))["bg"] == "white"
    assert vote.VOTE.validate(dict(data, bg="<img src=x>"))["bg"] == "white"


# ── 투표 ──

def test_single_choice_moves_the_vote_and_multi_keeps_both():
    data = vote.VOTE.new_data(ME, question="점심", options=["국밥", "냉면", "김밥"])
    data = ok(data, {"op": "vote", "option": 1})
    data = ok(data, {"op": "vote", "option": 2})
    assert [o["voters"] for o in data["options"]] == [[], [ME], []]
    assert ok(data, {"op": "vote", "option": 2}) == data          # 같은 표는 한 번
    data = ok(data, {"op": "unvote", "option": 2})
    assert vote.Vote.participants(data) == 0
    assert ok(data, {"op": "unvote", "option": 2}) == data        # 없는 표 빼기도 그대로
    multi = vote.VOTE.new_data(ME, options=["가", "나"], multi=True)
    multi = ok(ok(multi, {"op": "vote", "option": 1}), {"op": "vote", "option": 2})
    assert vote.Vote.my_options(multi, ME) == [1, 2] and vote.Vote.participants(multi) == 1
    assert apply(multi, {"op": "vote", "option": 99})[1] == "not_found"
    assert apply(multi, {"op": "vote", "option": "1"})[1] == "not_found"


def test_two_votes_in_either_order_end_the_same():
    data = vote.VOTE.new_data(ME, options=["가", "나"])
    a = ({"op": "vote", "option": 1}, OTHER)
    b = ({"op": "vote", "option": 1}, THIRD)
    c = ({"op": "vote", "option": 2}, ME)
    first = data
    for op, who in (a, b, c):
        first = ok(first, op, who)
    second = data
    for op, who in (c, b, a):
        second = ok(second, op, who)
    assert first == second
    assert first["options"][0]["voters"] == sorted([OTHER, THIRD])


def test_only_the_creator_closes_reopens_and_adds_options():
    data = vote.VOTE.new_data(ME, options=["가"])
    assert apply(data, {"op": "close"}, OTHER)[1] == "not_allowed"
    assert apply(data, {"op": "add_option", "text": "나"}, OTHER)[1] == "not_allowed"
    data = ok(data, {"op": "add_option", "text": "나"})
    assert [(o["id"], o["text"]) for o in data["options"]] == [(1, "가"), (2, "나")]
    assert apply(data, {"op": "add_option", "text": "나"})[1] == "exists"
    assert apply(data, {"op": "add_option", "text": "  "})[1] == "invalid"
    closed = ok(data, {"op": "close"})
    assert apply(closed, {"op": "vote", "option": 1}, OTHER)[1] == "closed"
    assert apply(closed, {"op": "unvote", "option": 1}, OTHER)[1] == "closed"
    assert apply(closed, {"op": "add_option", "text": "다"})[1] == "closed"
    assert apply(closed, {"op": "reopen"}, OTHER)[1] == "not_allowed"
    assert ok(closed, {"op": "reopen"})["closed"] is False
    # 지운 뒤 더해도 번호가 겹치지 않는다(동시에 온 표가 엉뚱한 항목에 가지 않게).
    assert data["next_id"] == 3
    full = vote.VOTE.new_data(ME, options=[str(i) for i in range(20)])
    assert apply(full, {"op": "add_option", "text": "x"})[1] == "limit"
    # 만든 사람이 없는 옛 데이터는 누구나 한다.
    orphan = dict(data, owner="")
    assert apply(orphan, {"op": "close"}, OTHER)[1] is None


def test_vote_validate_cleans_hostile_data():
    hostile = {
        "kind": "vote", "owner": ["x"], "bg": "clear", "question": "q" * 1_000_000,
        "options": [{"id": 1, "text": "가", "voters": [ME, 3, None, "", ME, "n" * 500]},
                    {"id": 1, "text": "중복 번호", "voters": []},
                    {"id": -4, "text": "음수", "voters": []},
                    {"id": True, "text": "참", "voters": []},
                    {"id": 2, "text": "가", "voters": []},
                    {"id": 3, "text": {"a": 1}, "voters": []},
                    "not a dict",
                    {"id": 5, "text": "나", "voters": "지우"},
                    {"id": 6, "text": "다", "voters": [ME]}] + [{"id": 100 + i, "text": str(i)}
                                                                for i in range(10_000)],
        "multi": "yes", "anonymous": 1, "closed": "true", "next_id": -1}
    clean = vote.VOTE.validate(hostile)
    assert clean["owner"] == "" and clean["bg"] == "clear" and len(clean["question"]) == 200
    assert [o["text"] for o in clean["options"]][:3] == ["가", "나", "다"]
    assert len(clean["options"]) <= vote.MAX_OPTIONS
    assert clean["options"][0]["voters"] == sorted([ME, "n" * 64])
    # 하나만 고르기인데 두 곳에 있는 사람은 앞의 표만 남는다.
    assert clean["options"][2]["voters"] == []
    assert (clean["multi"], clean["anonymous"], clean["closed"]) == (False, False, False)
    assert clean["next_id"] > max(o["id"] for o in clean["options"])
    json.dumps(clean)


def test_votes_that_would_pass_64kb_are_refused():
    data = vote.VOTE.new_data(ME, options=[f"항목 {i}" for i in range(20)], multi=True)
    names = [f"{i:04d}" + "가" * 60 for i in range(400)]
    for o in data["options"]:
        o["voters"] = sorted(names[:2])
    # 크기 한도 바로 밑까지 채운다.
    i = 2
    while True:
        trial = copy.deepcopy(data)
        trial["options"][i % 20]["voters"] = sorted(trial["options"][i % 20]["voters"]
                                                    + [names[i]])
        if base.json_size(trial) > widgets.MAX_BYTES:
            break
        data = trial
        i += 1
    assert vote.VOTE.validate(data) == data
    same, err = apply(data, {"op": "vote", "option": data["options"][0]["id"]}, "새사람" * 20)
    assert err == "too_large" and same == data
    with pytest.raises(widgets.WidgetError) as e:
        vote.VOTE.validate(trial)
    assert e.value.key == "too_large"
    # 동작 자체가 너무 커도 거절한다.
    assert apply(data, {"op": "vote", "option": 1, "pad": "x" * 70_000})[1] == "too_large"


# ── 주문 모으기 ──

def test_orders_add_items_count_totals_and_name_people():
    data = order.ORDER.new_data(OTHER, menu=["김치찌개", "제육볶음"])
    data = ok(data, {"op": "set_my_order", "item": "김치찌개", "qty": 1}, ME)
    data = ok(data, {"op": "set_my_order", "item": "김치찌개", "qty": 1}, OTHER)
    data = ok(data, {"op": "set_my_order", "item": "된장찌개", "qty": 2}, THIRD)
    assert data["menu"] == ["김치찌개", "제육볶음", "된장찌개"]          # 없던 메뉴는 끝에
    assert order.summary_lines(data) == ["김치찌개 2 (서연, 지우)", "된장찌개 2 (하준 2)"]
    assert order.item_totals(data) == {"김치찌개": 2, "제육볶음": 0, "된장찌개": 2}
    data = ok(data, {"op": "set_my_order", "item": "김치찌개", "qty": 0}, ME)
    assert ME not in data["orders"]
    data = ok(data, {"op": "set_my_order", "item": "제육볶음", "qty": 3}, THIRD)
    assert data["orders"][THIRD] == {"제육볶음": 3, "된장찌개": 2}   # 메뉴 차례
    data = ok(data, {"op": "clear_my_order"}, THIRD)
    assert list(data["orders"]) == [OTHER]
    for qty in (-1, 100, "2", True, None, 1.5):
        assert apply(data, {"op": "set_my_order", "item": "라면", "qty": qty})[1] == "invalid"
    assert apply(data, {"op": "set_my_order", "item": " ", "qty": 1})[1] == "invalid"
    assert "김치찌개 1 (서연)" in order.ORDER.search_text(data)
    assert "제육볶음" in order.ORDER.search_text(data)


def test_orders_from_two_people_in_either_order_end_the_same():
    data = order.ORDER.new_data(ME, menu=["국수"])
    x = ({"op": "set_my_order", "item": "국수", "qty": 1}, THIRD)
    y = ({"op": "set_my_order", "item": "국수", "qty": 2}, OTHER)
    assert ok(ok(data, *x), *y) == ok(ok(data, *y), *x)


def test_only_the_creator_closes_and_changes_the_menu():
    data = order.ORDER.new_data(ME, menu=["가", "나"])
    data = ok(data, {"op": "set_my_order", "item": "나", "qty": 1}, OTHER)
    assert apply(data, {"op": "close"}, OTHER)[1] == "not_allowed"
    assert apply(data, {"op": "set_menu", "menu": ["가"]}, OTHER)[1] == "not_allowed"
    closed = ok(data, {"op": "close"})
    assert apply(closed, {"op": "set_my_order", "item": "가", "qty": 1}, OTHER)[1] == "closed"
    assert apply(closed, {"op": "clear_my_order"}, OTHER)[1] == "closed"
    assert ok(closed, {"op": "reopen"})["closed"] is False
    trimmed = ok(data, {"op": "set_menu", "menu": ["가", "다"]})
    assert trimmed["menu"] == ["가", "다"] and trimmed["orders"] == {}   # 빠진 메뉴의 주문도 지움
    assert apply(data, {"op": "set_menu", "menu": [str(i) for i in range(31)]})[1] == "limit"
    assert apply(data, {"op": "set_menu", "menu": "가"})[1] == "invalid"
    full = order.ORDER.new_data(ME, menu=[str(i) for i in range(30)])
    assert apply(full, {"op": "set_my_order", "item": "새 메뉴", "qty": 1})[1] == "limit"


def test_order_validate_drops_negative_and_unknown_lines():
    raw = {"kind": "order", "owner": ME, "menu": ["가", "가", 3, "나", "x" * 500],
           "orders": {ME: {"가": -2, "나": 2, "없는 메뉴": 1}, OTHER: {"가": 1000},
                      THIRD: "많이", "": {"가": 1}, 7: {"가": 1}},
           "closed": None}
    clean = order.ORDER.validate(raw)
    assert clean["menu"] == ["가", "나", "x" * 60]
    assert clean["orders"] == {ME: {"나": 2}}
    assert clean["closed"] is False


# ── 사다리 타기 ──

def test_the_same_seed_always_builds_the_same_ladder():
    assert ladder.make_rungs(4, 10, 12345) == [[0, 0], [1, 0], [3, 1], [4, 0], [5, 2], [6, 0],
                                               [7, 2], [8, 2], [9, 1]]
    for seed in (0, 1, 99, 2 ** 31 - 1):
        for n in range(2, 31):
            rows = ladder.rows_for(n)
            rungs = ladder.make_rungs(n, rows, seed)
            assert rungs == ladder.make_rungs(n, rows, seed)
            assert {g for _r, g in rungs} == set(range(n - 1))        # 모든 사이에 가로줄
            spots = {tuple(r) for r in rungs}
            assert not any((r, g + 1) in spots for r, g in spots)     # 이웃한 가로줄 없음
            ends = [ladder.trace(n, rungs, i)[0] for i in range(n)]
            assert sorted(ends) == list(range(n))                     # 한 사람에 한 결과


def test_start_uses_the_hosts_seed_and_everyone_sees_the_same_result():
    data = ladder.LADDER.new_data(ME, participants=[ME, OTHER, THIRD], results=["꽝", "커피"])
    assert data["results"] == ["꽝", "커피", ""]
    op = ladder.LADDER.prepare_op({"op": "start", "seed": 1})      # 참여 PC 의 seed 는 덮어쓴다
    assert op["op"] == "start" and isinstance(op["seed"], int)
    started = ok(data, {"op": "start", "seed": 4242}, OTHER)
    again = ok(data, {"op": "start", "seed": 4242}, THIRD)
    assert started == again and started["seed"] == 4242
    assert started["rungs"] == ladder.make_rungs(3, ladder.rows_for(3), 4242)
    assert sorted(ladder.outcome(started)) == [0, 1, 2]
    assert apply(started, {"op": "start", "seed": 1})[1] == "started"
    # seed 가 없으면 받은 시각과 사람으로 정한다(어디서 돌려도 같다).
    a = ladder.LADDER.apply_op(data, {"op": "start"}, OTHER, 777)[0]
    b = ladder.LADDER.apply_op(data, {"op": "start"}, OTHER, 777)[0]
    assert a == b and a["started"]


def test_ladder_permissions_and_entries():
    data = ladder.LADDER.new_data(ME)
    assert apply(data, {"op": "start", "seed": 1})[1] == "invalid"          # 둘 이상이어야
    op = {"op": "set_entries", "participants": [ME, OTHER], "results": ["꽝", "당첨", "남는 것"]}
    assert apply(data, op, OTHER)[1] == "not_allowed"
    data = ok(data, op)
    assert data["results"] == ["꽝", "당첨"]
    dup = {"op": "set_entries", "participants": [ME, ME], "results": []}
    assert apply(data, dup)[1] == "invalid"
    many = {"op": "set_entries", "participants": [str(i) for i in range(31)], "results": []}
    assert apply(data, many)[1] == "limit"
    started = ok(data, {"op": "start", "seed": 3}, OTHER)
    assert apply(started, op)[1] == "started"
    assert apply(started, {"op": "reset"}, OTHER)[1] == "not_allowed"
    reset = ok(started, {"op": "reset"})
    assert (reset["started"], reset["rungs"], reset["seed"]) == (False, [], 0)
    assert ladder.split_entries("지우, 서연,\n하준,,") == [ME, OTHER, THIRD]
    assert ladder.split_entries("꽝,,커피,", keep_empty=True) == ["꽝", "", "커피"]


def test_ladder_validate_drops_bad_rungs():
    raw = {"kind": "ladder", "owner": ME, "participants": [ME, OTHER, THIRD, 5, ""],
           "results": ["a"], "started": True, "seed": -3, "rows": 10,
           "rungs": [[0, 0], [0, 1], [2, 5], [3, -1], [11, 0], "x", [1, 1], [1, 1], [4, True]]}
    clean = ladder.LADDER.validate(raw)
    assert clean["participants"] == [ME, OTHER, THIRD]
    assert clean["results"] == ["a", "", ""]
    assert clean["rungs"] == [[0, 0], [1, 1]] and clean["seed"] == 0
    lonely = ladder.LADDER.validate(dict(raw, participants=[ME]))
    assert lonely["started"] is False and lonely["rungs"] == []


# ── D-day ──

def test_dday_labels():
    target = dday.date(2026, 12, 7)
    assert dday.dday_label(target, dday.date(2026, 11, 25)) == "D-12"
    assert dday.dday_label(target, target) == "D-day"
    assert dday.dday_label(target, dday.date(2026, 12, 10)) == "D+3"


def test_dday_set_and_validate():
    data = dday.DDAY.new_data(ME, label="박람회", date="2026-12-05")
    data = ok(data, {"op": "set", "date": "2027-01-02"}, OTHER)
    assert (data["label"], data["date"]) == ("박람회", "2027-01-02")
    data = ok(data, {"op": "set", "label": "  전시회  "})
    assert data["label"] == "전시회"
    assert ok(data, {"op": "set", "date": ""})["date"] == ""
    for bad in ("2026-02-30", "26-1-1", "3000-01-01", 20260101, None):
        assert apply(data, {"op": "set", "date": bad})[1] == "invalid"
    assert apply(data, {"op": "set"})[1] == "invalid"
    assert apply(data, {"op": "set", "label": ["x"]})[1] == "invalid"
    clean = dday.DDAY.validate({"kind": "dday", "label": 5, "date": "2026-13-01"})
    assert (clean["label"], clean["date"]) == ("", "")


# ── 그래프 ──

def test_graph_data_limits_and_values():
    g = graph.GRAPH
    data = g.new_data(ME)
    data = ok(data, {"op": "set_data", "labels": ["1월", "2월", "3월"], "title": "매출",
                     "series": [{"name": "올해", "values": [1, 2.5, None]},
                                {"name": "작년", "values": [float("nan"), True, "3", 1e20]}]},
              OTHER)
    assert data["series"][0]["values"] == [1, 2.5, None]
    assert data["series"][1]["values"] == [None, None, None]
    assert data["title"] == "매출"
    assert apply(data, {"op": "set_data", "labels": [str(i) for i in range(201)],
                        "series": []})[1] == "limit"
    seven = [{"name": str(i), "values": [1]} for i in range(7)]
    assert apply(data, {"op": "set_data", "labels": ["a"], "series": seven})[1] == "limit"
    assert apply(data, {"op": "set_data", "labels": "abc", "series": []})[1] == "invalid"
    assert ok(data, {"op": "set_kind", "chart": "line"})["chart"] == "line"
    assert apply(data, {"op": "set_kind", "chart": "pie"})[1] == "invalid"
    # 값이 모자라면 빈 칸으로 채우고, 넘치면 자른다.
    clean = g.validate(dict(data, series=[{"name": "x", "values": [1, 2, 3, 4, 5]},
                                          {"name": "y", "values": []}]))
    assert [s["values"] for s in clean["series"]] == [[1, 2, 3], [None, None, None]]


def test_graph_table_text_round_trip():
    text = "이름, 올해, 작년\n1월, 10, 8\n2월, 12.5,\n3월\t7\t9"
    labels, series = graph.parse_table_text(text)
    assert labels == ["1월", "2월", "3월"]
    assert series == [{"name": "올해", "values": [10, 12.5, 7]},
                      {"name": "작년", "values": [8, None, 9]}]
    data = graph.GRAPH.new_data(ME, labels=labels, series=series)
    assert graph.parse_table_text(graph.to_table_text(data)) == (labels, series)
    assert graph.parse_table_text("") == ([], [])


# ── 오목 ──

def _play(moves, players=(ME, OTHER)):
    data = omok.OMOK.new_data(ME)
    for i, (x, y) in enumerate(moves):
        data = ok(data, {"op": "move", "x": x, "y": y}, players[i % 2])
    return data


def _five(dx, dy, x=3, y=3, count=5):
    """흑이 (dx, dy) 방향으로 count 개를 잇고, 백은 멀리 둔다."""
    black = [(x + dx * i, y + dy * i) for i in range(count)]
    white = [(14, i) for i in range(count)]
    order_ = []
    for i, b in enumerate(black):
        order_.append(b)
        if i < count - 1:
            order_.append(white[i])
    return order_


@pytest.mark.parametrize("dx,dy,x,y", [(1, 0, 2, 5), (0, 1, 5, 2), (1, 1, 2, 2), (1, -1, 2, 10)])
def test_five_in_a_row_wins_in_every_direction(dx, dy, x, y):
    data = _play(_five(dx, dy, x, y))
    assert data["winner"] == "black"
    assert data["line"] == sorted([[x + dx * i, y + dy * i] for i in range(5)])
    assert apply(data, {"op": "move", "x": 0, "y": 0}, OTHER)[1] == "game_over"


def test_four_is_not_enough_and_an_overline_also_wins():
    assert omok.OVERLINE_WINS is True
    four = _play(_five(1, 0, 2, 5, count=4))
    assert four["winner"] == ""
    # 가운데를 비워 둔 다섯과 하나를 이어 여섯을 만든다.
    moves = [(1, 5), (0, 14), (2, 5), (2, 14), (3, 5), (4, 14), (5, 5), (6, 14), (6, 5),
             (8, 14), (4, 5)]
    six = _play(moves)
    assert six["winner"] == "black" and len(six["line"]) == 6


def test_the_first_two_people_play_and_others_watch():
    data = omok.OMOK.new_data(OTHER)
    data = ok(data, {"op": "move", "x": 7, "y": 7}, ME)
    assert (data["black"], data["white"]) == (ME, "")
    assert apply(data, {"op": "move", "x": 8, "y": 8}, ME)[1] == "not_your_turn"
    data = ok(data, {"op": "move", "x": 8, "y": 8}, OTHER)
    assert data["white"] == OTHER
    assert apply(data, {"op": "move", "x": 1, "y": 1}, THIRD)[1] == "not_your_turn"
    assert apply(data, {"op": "move", "x": 1, "y": 1}, OTHER)[1] == "not_your_turn"
    assert apply(data, {"op": "move", "x": 7, "y": 7}, ME)[1] == "occupied"
    for x, y in ((15, 0), (-1, 0), ("1", 2), (True, 1)):
        assert apply(data, {"op": "move", "x": x, "y": y}, ME)[1] == "invalid"
    assert omok.can_move(data, ME) and not omok.can_move(data, OTHER)
    assert not omok.can_move(data, THIRD)
    # 판 중간에 구경하는 사람은 새 판을 못 연다. 두는 사람과 만든 사람은 연다.
    assert apply(data, {"op": "new_game"}, THIRD)[1] == "not_allowed"
    assert ok(data, {"op": "new_game"}, ME)["moves"] == []
    assert ok(data, {"op": "new_game"}, OTHER)["black"] == ""
    won = _play(_five(1, 0, 2, 5))
    assert ok(won, {"op": "new_game"}, THIRD) == omok.OMOK.new_data(ME)


def test_a_full_board_without_five_is_a_draw():
    blacks = [(x, y) for y in range(15) for x in range(15) if ((x // 2) + y) % 2 == 0]
    whites = [(x, y) for y in range(15) for x in range(15) if ((x // 2) + y) % 2 == 1]
    moves = [[*blacks[i // 2]] if i % 2 == 0 else [*whites[i // 2]] for i in range(225)]
    data = omok.OMOK.validate({"kind": "omok", "moves": moves, "black": ME, "white": OTHER})
    assert data["winner"] == "draw" and len(data["moves"]) == 225


def test_omok_validate_replays_and_does_not_trust_the_winner():
    moves = [[x, y] for x, y in _five(1, 0, 2, 5)]
    raw = {"kind": "omok", "moves": moves + [[0, 0], [1, 1]], "black": ME, "white": ME,
           "winner": "white", "line": [[9, 9]]}
    clean = omok.OMOK.validate(raw)
    assert clean["moves"] == moves and clean["winner"] == "black"
    assert clean["white"] == ""                        # 한 사람이 두 색을 쥘 수 없다
    broken = omok.OMOK.validate({"kind": "omok", "moves": [[1, 1], [1, 1], [2, 2]],
                                 "winner": "black"})
    assert broken["moves"] == [[1, 1]] and broken["winner"] == ""
    junk = omok.OMOK.validate({"kind": "omok", "moves": [[1, 2], [99, 0], [3, 3]]})
    assert junk["moves"] == [[1, 2]]
    huge = omok.OMOK.validate({"kind": "omok", "moves": [[1, 1]] * 1_000_000})
    assert huge["moves"] == [[1, 1]]


# ── 찾기용 글 ──

def test_search_text():
    v = vote.VOTE.new_data(ME, question="송년회 장소", options=["고깃집"], anonymous=True)
    v = ok(v, {"op": "vote", "option": 1}, OTHER)
    assert vote.VOTE.search_text(v) == "송년회 장소 고깃집"           # 익명이면 이름은 넣지 않는다
    lad = ladder.LADDER.new_data(ME, participants=[ME, OTHER], results=["꽝", "커피"])
    assert ladder.LADDER.search_text(lad) == f"{ME} {OTHER} 꽝 커피"
    assert dday.DDAY.search_text(dday.DDAY.new_data(ME, label="박람회", date="2026-12-05")) \
        == "박람회 2026-12-05"
    assert vote.VOTE.search_text("garbage") == ""


def test_random_junk_never_breaks_validate_or_apply_op():
    import random
    rng = random.Random(20261125)
    atoms = [None, True, False, 0, -1, 2 ** 70, 1.5, float("inf"), "", "x" * 300, "\x00\u202e",
             "2026-12-24", [], {}, ME]

    def junk(depth=0):
        r = rng.random()
        if depth > 2 or r < 0.5:
            return rng.choice(atoms)
        if r < 0.75:
            return [junk(depth + 1) for _ in range(rng.randint(0, 4))]
        keys = ["kind", "v", "owner", "bg", "options", "voters", "id", "text", "menu", "orders",
                "participants", "results", "started", "rungs", "rows", "seed", "label", "date",
                "country", "packs", "days_off", "labels", "series", "values", "chart", "moves",
                "black", "white", "winner", "x", "y", "op", "option", "item", "qty", "on", "pack"]
        return {rng.choice(keys): junk(depth + 1) for _ in range(rng.randint(0, 5))}

    ops = ["vote", "unvote", "close", "reopen", "add_option", "set_my_order", "clear_my_order",
           "set_menu", "set_entries", "start", "reset", "set", "set_pack", "set_country",
           "add_day_off", "remove_day_off", "set_data", "set_kind", "move", "new_game", "set_bg"]
    for name in widgets.kinds():
        kind = widgets.get(name)
        base_data = kind.new_data(ME)
        for _ in range(300):
            raw = junk()
            if isinstance(raw, dict) and rng.random() < 0.7:
                raw["kind"] = name
            try:
                clean = kind.validate(raw)
            except widgets.WidgetError:
                clean = base_data
            else:
                json.dumps(clean)
                assert kind.validate(clean) == clean           # 한 번 거르면 그대로다
            op = junk()
            if not isinstance(op, dict):
                op = {}
            op["op"] = rng.choice(ops)
            new, err = kind.apply_op(clean, op, rng.choice([ME, OTHER, ""]), 5)
            assert err is None or err in widgets.ERRORS
            if err is None:
                assert kind.validate(new) == new
