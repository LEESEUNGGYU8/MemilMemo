"""메모 안 위젯의 그림과 편집기: 본문에 넣는 PNG 와 그 안의 id, 종류마다 그리기,
편집기를 눌렀을 때 내는 동작, 화면 문구."""
from __future__ import annotations

import os
import struct
import zlib
from datetime import date

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QIODevice, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app import i18n, widgets  # noqa: E402
from app.ui import theme  # noqa: E402
from app.widgets import calendar as cal  # noqa: E402
from app.widgets import png, texts  # noqa: E402

ME = "지우"
OTHER = "서연"
THIRD = "하준"
TODAY = date(2026, 11, 25)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    i18n.set_language("ko")
    theme.apply_theme(app, "")
    yield app


def _rich(name: str) -> dict:
    """종류마다 내용이 찬 데이터."""
    k = widgets.get(name)
    if name == "vote":
        d = k.new_data(ME, question="송년회 장소", options=["고깃집", "횟집", "뷔페"])
        for who, o in ((ME, 1), (OTHER, 3), (THIRD, 3)):
            d = k.apply_op(d, {"op": "vote", "option": o}, who, 0)[0]
        return d
    if name == "order":
        d = k.new_data(ME, menu=["김치찌개", "제육볶음"])
        return k.apply_op(d, {"op": "set_my_order", "item": "김치찌개", "qty": 2}, OTHER, 0)[0]
    if name == "ladder":
        d = k.new_data(ME, participants=[ME, OTHER, THIRD], results=["꽝", "커피", "간식"])
        return k.apply_op(d, {"op": "start", "seed": 9}, OTHER, 0)[0]
    if name == "dday":
        return k.new_data(ME, label="박람회", date="2026-12-05")
    if name == "calendar":
        d = k.new_data(ME, packs={"tax": True, "observances": True})
        return k.apply_op(d, {"op": "add_day_off", "date": "2026-11-27", "label": "실사"}, ME, 0)[0]
    if name == "graph":
        return k.new_data(ME, title="방문객", labels=list("월화수목금"),
                          series=[{"name": "이번 주", "values": [3, 5, None, 2, 7]},
                                  {"name": "지난주", "values": [-1, 4, 6, 2, 1]}])
    d = k.new_data(ME)
    for i, (x, y) in enumerate(((7, 7), (8, 8), (7, 8))):
        d = k.apply_op(d, {"op": "move", "x": x, "y": y}, (ME, OTHER)[i % 2], 0)[0]
    return d


# ── PNG 와 위젯 id ──

def _plain_png(w=8, h=4) -> bytes:
    img = QImage(w, h, QImage.Format_ARGB32)
    img.fill(QColor("#FFE066"))
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


def _chunk(kind: bytes, payload: bytes, crc: int | None = None) -> bytes:
    if crc is None:
        crc = zlib.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)


def test_the_widget_id_survives_in_the_png(qapp):
    kind = widgets.get("vote")
    data = _rich("vote")
    wid = widgets.new_id()
    raw = kind.snapshot(data, wid, 300, today=TODAY)
    assert raw.startswith(png.SIGNATURE) and png.read_id(raw) == wid
    image = QImage()
    assert image.loadFromData(raw) and image.width() == 300      # 옛 버전도 그림으로 읽는다
    url = png.to_data_url(raw)
    assert png.id_from_data_url(url) == wid
    html = (f'<p>회의</p><img src="data:image/png;base64,{png.to_data_url(_plain_png())[22:]}" />'
            f'<p><img src="{url}" /></p><img src="{url}" />')
    assert png.ids_in_html(html) == [wid]
    # id 를 다시 적으면 바뀌고, 조각은 하나만 남는다.
    again = png.with_id(raw, "other-id_2")
    assert png.read_id(again) == "other-id_2" and again.count(png.KEYWORD) == 1
    assert QImage.fromData(again).width() == 300


def test_malformed_pngs_have_no_id(qapp):
    plain = _plain_png()
    good = png.with_id(plain, "abc")
    ihdr_end = 8 + 25
    bad_crc = plain[:ihdr_end] + _chunk(b"tEXt", png.KEYWORD + b"\0abc", crc=1) + plain[ihdr_end:]
    bad_value = plain[:ihdr_end] + _chunk(b"tEXt", png.KEYWORD + b"\0../x") + plain[ihdr_end:]
    huge_len = plain[:ihdr_end] + struct.pack(">I", 0xFFFFFFF0) + b"tEXt" + b"\0" * 20
    after_idat = plain[:-12] + _chunk(b"tEXt", png.KEYWORD + b"\0abc") + plain[-12:]
    for raw in (plain, b"", b"not a png", png.SIGNATURE, good[:40], bad_crc, bad_value, huge_len,
                after_idat, None, "abc", 12, bytes(range(256)) * 10,
                png.SIGNATURE + b"\0\0\0\x05\x00\x01\x02\x03" + b"x" * 30):
        assert png.read_id(raw) is None, raw[:60] if isinstance(raw, bytes) else raw
    assert png.read_id(memoryview(good)) == "abc"
    for url in ("", "data:image/jpeg;base64,AAAA", "data:image/png;base64,***",
                "http://example.com/a.png", None):
        assert png.id_from_data_url(url) is None
    with pytest.raises(ValueError):
        png.with_id(plain, "bad id!")
    with pytest.raises(ValueError):
        png.with_id(b"junk", "abc")
    with pytest.raises(ValueError):
        png.to_png(QImage(), "abc")
    assert png.ids_in_html(None) == [] and png.ids_in_html("<p>no images</p>") == []


# ── 그리기 ──

def test_every_kind_renders_in_both_backgrounds(qapp):
    for name in widgets.kinds():
        kind = widgets.get(name)
        for data in (kind.new_data(ME), _rich(name)):
            for bg in ("white", "clear"):
                for note in ("#FFE066", "#3A3A3A"):
                    img = kind.render(dict(data, bg=bg), 280, viewer=OTHER, today=TODAY,
                                      background=note)
                    assert not img.isNull() and img.width() == 280 and img.height() > 30, name
        hi = kind.render(_rich(name), 300, viewer=ME, today=TODAY, ratio=2)
        assert hi.width() == 600 and hi.devicePixelRatio() == 2
        # 받을 수 없는 데이터도 빈 틀은 그린다.
        assert not kind.render({"kind": "nope"}, 300, today=TODAY).isNull()
        assert not kind.render(dict(kind.new_data(ME), v=9), 300, today=TODAY).isNull()


def test_white_fills_and_clear_lets_the_note_show(qapp):
    kind = widgets.get("calendar")
    data = _rich("calendar")
    white = kind.render(data, 300, today=TODAY)
    clear = kind.render(dict(data, bg="clear"), 300, today=TODAY)
    assert white.pixelColor(150, 4) == QColor("#FFFFFF")
    assert white.pixelColor(0, 0).alpha() < 255                       # 둥근 모서리
    assert clear.pixelColor(150, 4).alpha() == 0
    # 투명 바탕은 어두운 메모 위에서 흰 글자로 그린다.
    dark = kind.render(dict(data, bg="clear"), 300, today=TODAY, background="#2E4057")
    light_ink = [dark.pixelColor(x, y) for x in range(300) for y in range(12, 40)
                 if dark.pixelColor(x, y).alpha() > 200]
    assert light_ink and max(c.lightness() for c in light_ink) > 200


def test_long_and_markup_like_text_is_drawn_as_plain_text(qapp):
    kind = widgets.get("vote")
    data = kind.new_data(ME, question='<img src="\\\\server\\share\\a.png">' * 20,
                         options=["<b>굵게</b>", "x" * 200])
    img = kind.render(data, 200, today=TODAY)
    assert not img.isNull() and img.width() == 200


# ── 편집기 ──

def _editor(name, data=None, viewer=ME, width=300):
    kind = widgets.get(name)
    ed = kind.create_editor(data if data is not None else _rich(name), viewer, TODAY)
    ed.resize(width, 600)
    ed.canvas.resize(width, ed.canvas.height())
    got = []
    ed.opRequested.connect(got.append)
    return ed, got


def test_vote_editor_sends_votes_and_owner_actions(qapp):
    data = _rich("vote")
    ed, got = _editor("vote", data, viewer=OTHER)
    ed.show()
    qapp.processEvents()
    hit = ed.find_hit(op="vote", option=1)
    # 누르는 동안은 짙은 바탕만 보이고, 같은 자리에서 떼면 보낸다.
    QTest.mousePress(ed.canvas, Qt.LeftButton, Qt.NoModifier, ed.hit_point(hit))
    assert got == [] and ed.view["pressed"] == {"op": "vote", "option": 1}
    QTest.mouseRelease(ed.canvas, Qt.LeftButton, Qt.NoModifier, ed.hit_point(hit))
    assert got == [{"op": "vote", "option": 1}] and ed.view["pressed"] is None
    assert ed.find_hit(op="unvote", option=3) is not None        # 내 표는 누르면 뺀다
    assert ed.find_hit(op="close") is None                        # 만든 사람이 아니다
    assert ed.data() == data                                      # 편집기는 데이터를 바꾸지 않는다
    ed.close()
    ed, got = _editor("vote", data, viewer=ME)
    assert ed.press(*ed.find_hit(op="close").center()) and got[-1] == {"op": "close"}
    closed = widgets.get("vote").apply_op(data, {"op": "close"}, ME, 0)[0]
    ed.set_data(closed)
    assert ed.find_hit(op="vote", option=1) is None and ed.find_hit(op="reopen") is not None
    # 받을 수 없는 데이터가 오면 읽기만 하고 동작을 내지 않는다('⋯' 자리만 남는다).
    ed.set_data({"kind": "vote", "v": 99})
    assert ed.readonly and [h.action for h in ed.hits()] in ([], [{"local": "menu",
                                                                   "tip": "menu.more"}])
    count = len(got)
    ed.request({"op": "reopen"})
    assert len(got) == count
    ed.close()


def test_every_card_keeps_the_menu_corner_free(qapp):
    """'⋯' 자리(오른쪽 위)에는 위젯의 단추가 없다(겹쳐 눌리지 않는다)."""
    from app.widgets import draw
    for name in widgets.kinds():
        for width in (220, 300, 440):
            ed, _got = _editor(name, viewer=ME, width=width)
            corner = draw.menu_rect(ed.canvas.width())
            for hit in ed.hits():
                if hit.action.get("local") == "menu":
                    continue
                box = (hit.x, hit.y, hit.x + hit.w, hit.y + hit.h)
                apart = (box[2] <= corner.left() or box[0] >= corner.right()
                         or box[3] <= corner.top() or box[1] >= corner.bottom())
                assert apart, (name, width, hit.action)
            ed.close()


def test_order_editor_steps_quantities(qapp):
    ed, got = _editor("order", viewer=OTHER)
    plus = ed.find_hit(op="set_my_order", item="김치찌개", qty=3)
    minus = ed.find_hit(op="set_my_order", item="김치찌개", qty=1)
    assert plus and minus
    ed.press(*plus.center())
    ed.press(*ed.find_hit(op="clear_my_order").center())
    assert got == [{"op": "set_my_order", "item": "김치찌개", "qty": 3}, {"op": "clear_my_order"}]
    # 메뉴에 없는 것은 '+ 메뉴 추가' 자리에 적어 시킨다(누구나).
    add = ed.find_hit(local="input", purpose="order_item")
    assert add is not None
    kind = widgets.get("order")
    assert kind.input_op(ed.current(), "order_item", "  라면 ", OTHER) == {
        "op": "set_my_order", "item": "라면", "qty": 1}
    assert kind.input_op(ed.current(), "order_item", "김치찌개", OTHER)["qty"] == 3
    assert kind.input_op(ed.current(), "order_item", "   ", OTHER) is None
    assert ed.find_hit(op="close") is None
    ed.close()


def test_ladder_editor_starts_and_resets(qapp):
    kind = widgets.get("ladder")
    fresh = kind.new_data(ME, participants=[ME, OTHER], results=["꽝", "커피"])
    ed, got = _editor("ladder", fresh, viewer=OTHER)
    before = kind.measure(fresh, 300, ed.ctx())
    ed.press(*ed.find_hit(op="start").center())
    assert got == [{"op": "start"}]
    ed.close()
    ed, got = _editor("ladder", fresh, viewer=ME)
    ed.set_data(_rich("ladder"))
    assert ed.find_hit(op="start") is None
    ed.press(*ed.find_hit(op="reset").center())
    assert got[-1] == {"op": "reset"}
    # 시작해도 높이는 같다(결과 밑의 이름 줄은 처음부터 비워 둔다).
    started = kind.apply_op(fresh, {"op": "start", "seed": 3}, ME, 0)[0]
    assert kind.measure(started, 300, ed.ctx()) == before
    ed.close()


def test_dday_card_opens_its_setup_when_pressed(qapp):
    ed, got = _editor("dday", viewer=OTHER)
    local = []
    ed.localRequested.connect(lambda action, rect: local.append(action))
    ed.press(*ed.find_hit(local="setup").center())
    assert local == [{"local": "setup"}] and got == []
    kind = widgets.get("dday")
    assert kind.measure(ed.current(), 300, ed.ctx()) == kind.measure(
        kind.new_data(ME), 300, ed.ctx())                   # 날짜가 없어도 같은 높이
    ed.close()


def test_a_host_round_trip_keeps_every_pc_in_step(qapp):
    """참여 PC 의 동작이 공유 PC 를 거쳐 모든 편집기에 같은 그림으로 돌아온다."""
    kind = widgets.get("ladder")
    data = kind.new_data(ME, participants=[ME, OTHER, THIRD], results=["꽝", "커피", "간식"])
    mine, sent = _editor("ladder", data, viewer=OTHER)
    theirs, _ = _editor("ladder", data, viewer=THIRD)
    mine.press(*mine.find_hit(op="start").center())
    op = kind.prepare_op(dict(sent[-1], seed="client"))   # 공유 PC 가 seed 를 정한다
    assert isinstance(op["seed"], int) and 0 <= op["seed"] < 2 ** 31
    shared, err = kind.apply_op(data, op, OTHER, 1000)
    assert err is None
    for ed in (mine, theirs):
        ed.set_data(shared)
    a = kind.render(mine.data(), 300, today=TODAY)
    b = kind.render(theirs.data(), 300, today=TODAY)
    assert a == b and mine.data()["rungs"] == theirs.data()["rungs"]
    mine.close()
    theirs.close()


def test_calendar_editor_moves_months_and_toggles_packs(qapp):
    ed, got = _editor("calendar", viewer=OTHER)
    ed.press(*ed.find_hit(local="month", step=1).center())
    assert ed.view["month"] == date(2026, 12, 1) and got == []          # 보는 달은 나만 바뀐다
    ed.press(*ed.find_hit(local="month", step=-1).center())
    ed.press(*ed.find_hit(local="month", step=-1).center())
    assert ed.view["month"] == date(2026, 10, 1)
    ed.press(*ed.find_hit(op="set_pack", pack="observances").center())
    assert got == [{"op": "set_pack", "pack": "observances", "on": False}]
    day = ed.find_hit(local="day", date=date(2026, 10, 20))
    assert not ed.press(*day.center())                    # 날을 눌러도 아무것도 하지 않는다
    items = ed.kind.context_actions(ed, *day.center())
    assert items[0] == ("쉬는 날로 정하기",
                        {"op": "add_day_off", "date": "2026-10-20", "label": ""}, None)
    assert ("일본", {"op": "set_country", "country": "JP"}, False) in items
    assert ("한국", {"op": "set_country", "country": "KR"}, True) in items
    ed.view["month"] = date(2026, 11, 1)
    marked = ed.find_hit(local="day", date=date(2026, 11, 27))
    assert ed.kind.context_actions(ed, *marked.center())[0][1] == {
        "op": "remove_day_off", "date": "2026-11-27"}
    ed.close()


def test_calendar_keeps_one_height_and_notes_a_missing_table(qapp):
    kind = cal.CALENDAR
    data = kind.new_data(ME)
    heights = {kind.measure(data, 300, widgets.Ctx(today=date(2026, m, 1))) for m in (2, 9, 11)}
    assert len(heights) == 1                    # 달을 넘겨도 위젯 크기가 그대로다
    assert kind._missing(data, date(2040, 9, 1)) and not kind._missing(data, date(2026, 9, 1))


def test_graph_shape_is_in_the_menu_and_an_empty_graph_asks_for_data(qapp):
    ed, got = _editor("graph", viewer=OTHER)
    # 막대와 꺾은선은 '⋯' 메뉴의 '모양' 에서 고른다(제목이 머리 줄을 다 쓴다).
    assert ed.find_hit(op="set_kind", chart="line") is None
    (title, choices), = ed.kind.menu_choices(ed.current())
    assert title == "모양" and [c[1] for c in choices] == [
        {"op": "set_kind", "chart": "bar"}, {"op": "set_kind", "chart": "line"}]
    assert [c[2] for c in choices] == [True, False]
    assert ed.find_hit(local="setup") is None               # 데이터는 '⋯' 의 위젯 고치기로
    empty, _ = _editor("graph", widgets.get("graph").new_data(ME), viewer=OTHER)
    assert empty.find_hit(local="setup") is not None        # 빈 그래프는 '데이터 넣기'
    ed.close()
    empty.close()


def test_omok_editor_moves_only_for_the_player_to_move(qapp):
    data = _rich("omok")                     # 흑 지우, 백 서연, 백 차례
    ed, got = _editor("omok", data, viewer=OTHER)
    ed.press(*ed.find_hit(op="move", x=0, y=0).center())
    assert got == [{"op": "move", "x": 0, "y": 0}]
    assert ed.find_hit(op="move", x=7, y=7) is None        # 둔 자리는 누를 수 없다
    ed.close()
    for viewer in (ME, THIRD):
        ed, got = _editor("omok", data, viewer=viewer)
        assert ed.find_hit(op="move") is None
        ed.close()
    kind = widgets.get("omok")
    won = kind.new_data(ME)
    moves = [(2, 5), (0, 14), (3, 5), (2, 14), (4, 5), (4, 14), (5, 5), (6, 14), (6, 5)]
    for i, (x, y) in enumerate(moves):
        won = kind.apply_op(won, {"op": "move", "x": x, "y": y}, (ME, OTHER)[i % 2], 0)[0]
    assert won["winner"] == "black"
    ed, got = _editor("omok", won, viewer=THIRD)
    assert ed.find_hit(op="move") is None
    ed.press(*ed.find_hit(op="new_game").center())
    assert got == [{"op": "new_game"}]
    ed.close()


def test_an_editor_without_a_viewer_sends_nothing(qapp):
    ed, got = _editor("vote", viewer="")
    assert ed.find_hit(op="vote") is None
    ed.request({"op": "vote", "option": 1})
    assert got == []
    ed.close()


# ── 문구 ──

def test_texts_have_three_languages_and_follow_the_copy_rules():
    for key, row in texts.all_texts().items():
        assert len(row) == 3 and all(row), key
        assert all("\u00b7" not in t for t in row), key           # 가운뎃점 구분자를 쓰지 않는다
    for err in widgets.ERRORS:
        assert texts.tr("err." + err) != "err." + err
    assert texts.tr("vote.people", language="ko", n=6) == "6명 참여"
    assert texts.tr("omok.won", language="en", name="Ann") == "Ann wins"
    assert texts.tr("no.such.key") == "no.such.key"
    d = date(2026, 12, 5)
    assert texts.format_date(d, language="ko") == "2026년 12월 5일 (토)"
    assert texts.format_date(d, language="en") == "Sat, December 5, 2026"
    assert texts.format_date(d, language="ja") == "2026年12月5日(土)"
    assert texts.format_number(1234567) == "1,234,567"
    assert texts.format_number(2.5) == "2.5" and texts.format_number(3.0) == "3"


def test_texts_follow_the_app_language(qapp):
    try:
        i18n.set_language("ja")
        assert texts.tr("kind.omok") == "五目並べ"
        assert texts.tr("cal.month", y=2026, m=11, mon="x") == "2026年11月"
        i18n.set_language("en")
        assert texts.tr("cal.month", y=2026, m=11, mon="November") == "November 2026"
    finally:
        i18n.set_language("ko")
    assert texts.tr("kind.omok") == "오목"
