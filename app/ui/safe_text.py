"""다른 PC 에서 온 글을 화면에 그릴 때 지킬 것.

메모 본문, 닉네임, 폴더 이름, 색 값은 모두 다른 참가자가 보낸 값이다. Qt 는
HTML 처럼 생긴 글자를 서식 있는 글로 그리고, 서식 있는 글이 가리키는 그림과
스타일시트를 파일에서 읽는다. 그 경로가 ``\\\\서버\\공유`` 꼴이면 Windows 가 그
서버에 접속하면서 로그인 정보(NTLM)를 보낸다. 메모 한 장을 받는 것만으로 그런
접속이 일어나서는 안 된다.

- 본문을 읽고 그리는 문서는 :class:`SafeTextDocument` 를 쓴다(본문에 든
  ``data:`` 그림만 읽는다).
- 글자만 보이는 QLabel 은 평문으로 그린다(:class:`PlainLabelGuard`).
- 말풍선도 라벨이라 같은 규칙을 따른다.
"""
from __future__ import annotations

import base64
import binascii
import re

from PySide6.QtCore import QEvent, QObject, Qt, QUrl
from PySide6.QtGui import QImage, QTextDocument
from PySide6.QtWidgets import QLabel

_COLOR = re.compile(r"#[0-9A-Fa-f]{6}\Z")


class SafeTextDocument(QTextDocument):
    """본문이 가리키는 바깥 자원을 읽지 않는 문서.

    ``<img src>`` 나 ``<link href>`` 가 이 PC 의 파일이나 네트워크 경로를
    가리켜도 열지 않는다. 본문 안에 ``data:`` 로 든 그림만 읽는다."""

    def loadResource(self, resource_type, name):  # noqa: N802
        url = name.toString() if isinstance(name, QUrl) else str(name)
        if url[:5].lower() == "data:":
            if (resource_type == QTextDocument.ImageResource
                    and not data_url_is_raster(url)):
                return _blank_image()
            return super().loadResource(resource_type, name)
        if resource_type == QTextDocument.ImageResource:
            # 빈 값을 돌려주면 Qt 가 그 이름을 파일로 보고 직접 열어 본다. 빈
            # 그림을 돌려줘서 거기까지 가지 않게 한다.
            return _blank_image()
        return None


def is_plain_raster(data) -> bool:
    """PNG, JPEG, GIF, BMP, WEBP 인가. 파일 앞머리로 가린다.

    이 앱이 본문에 넣는 그림은 이 다섯 가지뿐이다. 그 밖의 형식(SVG, PDF,
    TIFF 등)은 풀어 보지 않는다. 남이 보낸 바이트를 덜 다듬어진 해석기에
    넘기지 않고, SVG 가 가리키는 바깥 파일을 여는 일도 없게 한다."""
    head = bytes(data[:16])
    return (head.startswith(b"\x89PNG\r\n\x1a\n")
            or head.startswith(b"\xff\xd8\xff")
            or head[:6] in (b"GIF87a", b"GIF89a")
            or head.startswith(b"BM")
            or (head[:4] == b"RIFF" and head[8:12] == b"WEBP"))


def data_url_is_raster(url: str) -> bool:
    """``data:`` 주소에 든 것이 위의 그림 형식인가(base64 로 든 것만 본다)."""
    head, sep, payload = (url or "").partition(",")
    if not sep or ";base64" not in head.lower():
        return False
    chunk = payload[:32]
    try:
        raw = base64.b64decode(chunk + "=" * (-len(chunk) % 4))
    except (binascii.Error, ValueError):
        return False
    return is_plain_raster(raw)


def _blank_image() -> QImage:
    image = QImage(1, 1, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    return image


def safe_color(value, fallback: str = "#CCCCCC") -> str:
    """``#RRGGBB`` 꼴일 때만 그대로 쓴다. 남이 보낸 색 값을 서식 안에 넣기 전에 거른다."""
    text = str(value or "")
    return text if _COLOR.match(text) else fallback


class PlainLabelGuard(QObject):
    """QLabel 이 글자를 서식으로 알아서 바꿔 그리지 않게 한다.

    QLabel 의 기본값은 '알아서'다. 닉네임이나 메모 제목에 ``<img src=...>`` 같은
    글자가 있으면 그것을 그림으로 그리려고 파일을 연다. 이 앱의 화면 문구에는
    서식이 없으므로, 서식을 쓰겠다고 따로 정한 라벨만 빼고 모두 평문으로 둔다."""

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if (event.type() == QEvent.Polish and isinstance(obj, QLabel)
                and obj.textFormat() == Qt.AutoText):
            obj.setTextFormat(Qt.PlainText)
        return False
