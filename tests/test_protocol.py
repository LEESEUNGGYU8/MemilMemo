from app.protocol import FrameParser, make


def test_roundtrip():
    m = make("hello", nickname="alice")
    encoded = m.encode()
    parser = FrameParser()
    msgs = parser.feed(encoded)
    assert len(msgs) == 1
    assert msgs[0].type == "hello"
    assert msgs[0].data == {"nickname": "alice"}


def test_multiple_frames_in_one_chunk():
    a = make("ping").encode()
    b = make("pong", ts=123).encode()
    parser = FrameParser()
    msgs = parser.feed(a + b)
    assert [m.type for m in msgs] == ["ping", "pong"]


def test_partial_frame_buffering():
    data = make("note_updated", note={"id": "x", "content": "hi"}).encode()
    parser = FrameParser()
    # feed one byte at a time
    out = []
    for i in range(len(data)):
        out.extend(parser.feed(data[i : i + 1]))
    assert len(out) == 1
    assert out[0].type == "note_updated"
    assert out[0].data["note"]["id"] == "x"


def test_malformed_frame_dropped():
    parser = FrameParser()
    # length 5, body is garbage that won't decode
    bad = (5).to_bytes(4, "big") + b"\xff\xff\xff\xff\xff"
    msgs = parser.feed(bad)
    assert msgs == []


def test_frame_longer_than_the_limit_stops_the_parser():
    from app.protocol import LENGTH_HEADER

    parser = FrameParser(max_frame=1024)
    good = make("hello", nickname="지우").encode()
    # 길이만 4GB 라고 적고 조금만 보낸다. 받는 쪽은 모으지 않고 그만둔다.
    huge = LENGTH_HEADER.pack(0xFFFFFFFF) + b"x" * 100
    msgs = parser.feed(good + huge + good)
    assert [m.type for m in msgs] == ["hello"]
    assert parser.overflowed is True and len(parser._buf) == 0
    assert parser.feed(good) == []          # 그 뒤로는 받지 않는다

    # 상한을 주지 않으면 전처럼 얼마든 기다린다(참여자가 공유 PC 에서 받는 쪽).
    loose = FrameParser()
    assert loose.feed(huge) == [] and loose.overflowed is False


def test_limit_can_grow_between_messages():
    parser = FrameParser(max_frame=64)
    hello = make("hello", nickname="지우").encode()
    big = make("update_note", id="n1", content="가" * 200).encode()
    seen = []
    for msg in parser.iter_feed(hello + big):
        seen.append(msg.type)
        parser.max_frame = 1 << 20          # 인사를 받은 뒤에 늘린다
    assert seen == ["hello", "update_note"] and parser.overflowed is False


def test_frames_that_are_not_messages_are_skipped():
    import json

    from app.protocol import LENGTH_HEADER

    def frame(obj) -> bytes:
        body = json.dumps(obj).encode("utf-8")
        return LENGTH_HEADER.pack(len(body)) + body

    parser = FrameParser()
    good = make("pong").encode()
    odd = (frame([1, 2, 3]) + frame("text") + frame({"type": 5, "data": {}})
           + frame({"type": "x", "data": [1]}) + frame({"no_type": 1}))
    msgs = parser.feed(odd + good)
    assert [m.type for m in msgs] == ["pong"]
