"""@이름 찾기(공유 PC 가 메모를 저장할 때 새로 생긴 언급을 찾는다). Qt 없이 돈다."""
from __future__ import annotations

import html
import re

from .text_detect import find_mentions

_TAG = re.compile(r"<[^>]+>")
_BREAK = re.compile(r"(?i)<\s*(br|/p|/li|/div|/tr|/h[1-6])\b[^>]*>")
_STYLE = re.compile(r"(?is)<(style|script)\b.*?</\1\s*>")


def plain_of(content: str) -> str:
    """메모 본문(HTML)의 글만. 줄 바꿈은 남긴다."""
    text = _STYLE.sub("", content or "")
    text = _BREAK.sub("\n", text)
    text = _TAG.sub("", text)
    return html.unescape(text)


def mentioned(text: str, names) -> set[str]:
    """글에서 '@이름' 으로 부른 사람들. ``names`` 에 든 이름만 센다(규칙은
    text_detect.find_mentions: 긴 이름부터, 앞이 글자면 메일 주소로 본다)."""
    return {hit.name for hit in find_mentions(text or "", [n for n in names if n])}


def new_mentions(old_content: str, new_content: str, names) -> set[str]:
    """고친 뒤에 새로 부른 사람(전에도 불렀던 사람은 빼고)."""
    return mentioned(plain_of(new_content), names) - mentioned(plain_of(old_content), names)
