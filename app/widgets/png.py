"""본문에 넣는 위젯 그림(PNG)과 그 안의 위젯 id.

본문에는 위젯이 PNG 그림(``data:image/png;base64,...``)으로 들어간다. 옛 버전은 마지막
모습의 그림을 본다. 그림 안의 글 조각(tEXt, 열쇠말 ``memil-widget``)에 위젯 id 를
적는다. 본문의 그림 주소는 바이트 그대로 저장되므로 옛 버전이 본문을 고쳐 저장해도
id 가 남는다.

남이 보낸 바이트를 읽으므로 길이와 경계를 모두 확인하고, 쓰는 조각은 CRC 가 맞는
것만 믿는다. 그림 자체를 풀지는 않는다."""
from __future__ import annotations

import base64
import binascii
import re
import struct
import zlib

SIGNATURE = b"\x89PNG\r\n\x1a\n"
KEYWORD = b"memil-widget"
DATA_URL_PREFIX = "data:image/png;base64,"
_ID = re.compile(rb"[A-Za-z0-9_-]{1,64}\Z")
_MAX_CHUNKS = 100_000
# id 조각은 IHDR 바로 뒤에 있다. 주소에서 id 만 찾을 때 앞부분만 푼다.
_HEAD_CHARS = 1024


def _chunk(kind: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)


def _chunks(data: bytes):
    """(종류, 내용, CRC 가 맞는가, 조각 전체)를 차례로. 모양이 깨지면 거기서 멈춘다."""
    if not data.startswith(SIGNATURE):
        return
    pos = len(SIGNATURE)
    count = 0
    size = len(data)
    while pos + 12 <= size and count < _MAX_CHUNKS:
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        kind = data[pos + 4:pos + 8]
        end = pos + 12 + length
        if length > 0x7FFFFFFF or end > size or not kind.isalpha():
            return
        payload = data[pos + 8:pos + 8 + length]
        (crc,) = struct.unpack(">I", data[end - 4:end])
        yield kind, payload, crc, data[pos:end]
        count += 1
        if kind == b"IEND":
            return
        pos = end


def _crc_ok(kind: bytes, payload: bytes, crc: int) -> bool:
    return (zlib.crc32(kind + payload) & 0xFFFFFFFF) == crc


def _is_id_chunk(kind: bytes, payload: bytes) -> bool:
    return kind == b"tEXt" and payload.partition(b"\0")[0] == KEYWORD


LABEL_KEYWORD = b"memil-label"


def _label_chunk(label: str) -> bytes:
    """보이는 이름(UTF-8, iTXt). 메모 목록이 위젯을 그림이 아닌 이 이름으로 읽는다."""
    text = (label or "")[:80].encode("utf-8")
    return _chunk(b"iTXt", LABEL_KEYWORD + bytes(5) + text)


def _is_label_chunk(kind: bytes, payload: bytes) -> bool:
    return kind == b"iTXt" and payload.startswith(LABEL_KEYWORD + bytes(1))


def with_id(png: bytes, widget_id: str, label: str = "") -> bytes:
    """PNG 바이트에 id 조각(과 보이는 이름)을 넣는다(IHDR 바로 뒤). 있던 것은 바꾼다."""
    if not isinstance(widget_id, str) or not _ID.match(widget_id.encode("ascii", "replace")):
        raise ValueError("bad widget id")
    parts = list(_chunks(bytes(png)))
    if not parts or parts[0][0] != b"IHDR" or parts[-1][0] != b"IEND":
        raise ValueError("not a png")
    out = [SIGNATURE, parts[0][3],
           _chunk(b"tEXt", KEYWORD + b"\0" + widget_id.encode("ascii"))]
    if label:
        out.append(_label_chunk(label))
    out += [whole for kind, payload, _crc, whole in parts[1:]
            if not _is_id_chunk(kind, payload) and not _is_label_chunk(kind, payload)]
    return b"".join(out)


def read_label(png) -> str:
    """PNG 안의 보이는 이름(없으면 빈 글)."""
    if not isinstance(png, (bytes, bytearray, memoryview)):
        return ""
    for kind, payload, crc, _whole in _chunks(bytes(png)):
        if _is_label_chunk(kind, payload) and _crc_ok(kind, payload, crc):
            body = payload[len(LABEL_KEYWORD) + 1:]
            # 압축 표시 둘, 언어 끝, 옮긴 이름 끝을 건너뛴다.
            if len(body) < 4 or body[0] != 0:
                return ""
            rest = body[2:]
            lang_end = rest.find(bytes(1))
            if lang_end < 0:
                return ""
            rest = rest[lang_end + 1:]
            tr_end = rest.find(bytes(1))
            if tr_end < 0:
                return ""
            return rest[tr_end + 1:].decode("utf-8", "replace")[:80]
        if kind == b"IDAT":
            return ""
    return ""


def label_from_data_url(url) -> str:
    """본문 그림 주소에서 위젯의 보이는 이름. 위젯이 아니거나 이름이 없으면 빈 글."""
    if id_from_data_url(url) is None:
        return ""
    head = url[len(DATA_URL_PREFIX):len(DATA_URL_PREFIX) + _HEAD_CHARS]
    head = head[:len(head) - len(head) % 4]
    try:
        raw = base64.b64decode(head, validate=True)
    except (binascii.Error, ValueError):
        return ""
    return read_label(raw)


_IMG_TAG = re.compile(
    r"""<img\b[^>]*?src=(["'])(data:image/png;base64,[A-Za-z0-9+/=]{16,})\1[^>]*>""", re.I)


def widgets_as_text(html) -> str:
    """본문의 위젯 그림을 그 이름 글로 바꾼다(제목, 찾기, 그림 여부를 셀 때 쓴다).
    위젯이 아닌 그림은 그대로 둔다."""
    if not isinstance(html, str) or "base64" not in html:
        return html
    import html as _html

    def put(m) -> str:
        url = m.group(2)
        if id_from_data_url(url[:len(DATA_URL_PREFIX) + _HEAD_CHARS]) is None:
            return m.group(0)
        return _html.escape(label_from_data_url(url) or " ")
    return _IMG_TAG.sub(put, html)


def to_png(image, widget_id: str, label: str = "") -> bytes:
    """QImage 를 id(와 보이는 이름)가 든 PNG 바이트로."""
    from PySide6.QtCore import QBuffer, QIODevice
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    if image is None or image.isNull() or not image.save(buf, "PNG"):
        raise ValueError("empty image")
    return with_id(bytes(buf.data()), widget_id, label)


def read_id(png) -> str | None:
    """PNG 안의 위젯 id. 없거나 모양이 틀리면 None."""
    if not isinstance(png, (bytes, bytearray, memoryview)):
        return None
    for kind, payload, crc, _whole in _chunks(bytes(png)):
        if _is_id_chunk(kind, payload) and _crc_ok(kind, payload, crc):
            value = payload.partition(b"\0")[2]
            if _ID.match(value):
                return value.decode("ascii")
        if kind == b"IDAT":
            return None            # id 조각은 그림 바이트 앞에만 둔다
    return None


def to_data_url(png: bytes) -> str:
    return DATA_URL_PREFIX + base64.b64encode(bytes(png)).decode("ascii")


def id_from_data_url(url) -> str | None:
    """본문 그림 주소(``data:image/png;base64,...``)에서 위젯 id 를 찾는다. 앞부분만 푼다."""
    if not isinstance(url, str) or not url[:len(DATA_URL_PREFIX)].lower() == DATA_URL_PREFIX:
        return None
    head = url[len(DATA_URL_PREFIX):len(DATA_URL_PREFIX) + _HEAD_CHARS]
    head = head[:len(head) - len(head) % 4]
    try:
        raw = base64.b64decode(head, validate=True)
    except (binascii.Error, ValueError):
        return None
    return read_id(raw)


_IMG_SRC = re.compile(r"""data:image/png;base64,[A-Za-z0-9+/=]{16,}""", re.I)


def ids_in_html(html) -> list[str]:
    """본문(HTML)에 든 위젯 그림의 id 들(나온 차례, 겹치면 한 번)."""
    if not isinstance(html, str):
        return []
    out: list[str] = []
    for m in _IMG_SRC.finditer(html):
        found = id_from_data_url(m.group(0)[:len(DATA_URL_PREFIX) + _HEAD_CHARS])
        if found and found not in out:
            out.append(found)
    return out
