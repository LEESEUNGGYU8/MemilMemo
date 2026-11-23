"""메모 본문(HTML)에서, 받는 PC 가 바깥 자원을 열게 만드는 조각을 걷어 낸다.

메모 본문은 다른 참가자가 보낸다. 본문이 ``<img src="\\\\서버\\공유\\a.png">`` 나
``<link rel="stylesheet" href="...">`` 를 담고 있으면 그것을 화면에 그리는 PC 가
그 경로를 연다. 네트워크 경로라면 Windows 가 그 서버에 로그인 정보를 보낸다.

이 앱이 만드는 본문에는 그런 조각이 없다. 그림은 본문 안에 ``data:`` 로 들어 있고
글머리와 체크 상자는 앱 안의 그림이다. 그래서 그 밖의 것은 모두 걷어 내도 정상
메모는 한 글자도 달라지지 않는다.

화면 코드가 없는 순수 로직이다. 공유 PC 는 받은 본문을 저장하기 전에, 각 PC 는
화면에 그리기 전에 거친다(공유 PC 에서 걸러 두면 아직 옛 버전인 PC 도 보호된다).
"""
from __future__ import annotations

import html
import re

# 태그 하나: '<' 부터 '>' 까지. 닫히지 않은 채 끝난 것도 태그로 본다(Qt 가 그렇게 읽는다).
_TAG = re.compile(r"<[^>]*(?:>|\Z)")
_NAME = re.compile(r"<\s*(/?)\s*([^\s<>/]*)")
# 이름=값. 값은 따옴표로 싸였거나 빈칸 없이 이어진다. 이름은 낱말의 첫 글자에서만
# 찾기 시작한다: 낱말 중간마다 다시 찾으면 '=' 없는 긴 글자열 하나에 걸리는
# 시간이 길이의 제곱으로 늘어, 남이 보낸 프레임 하나가 공유 PC 를 멈춰 세운다.
_ATTR = re.compile(
    r"""(?<![^\s"'<>/=])([^\s"'<>/=]+)\s*=\s*("[^"]*"|'[^']*'|[^\s"'>]*)""")
# 이름과 값에 끼워 넣어 눈속임하는 제어 문자.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_CSS_NOISE = re.compile(r"[\s\\]")

# 통째로 버리는 태그. 바깥 자원을 불러오거나 문서의 기준 주소를 바꾼다.
_DROP_TAGS = frozenset((
    "link", "base", "object", "embed", "applet", "iframe", "frame",
    "frameset", "script"))
# 값이 주소인 속성. Qt 는 img 의 src 를 source 라고 적어도 받는다.
_URL_ATTRS = frozenset((
    "src", "source", "background", "lowsrc", "dynsrc", "poster", "data",
    "codebase"))
# 본문 안에 든 그림과 앱 안의 글머리 그림만 허용한다.
_ALLOWED_PREFIXES = ("data:image/", "x-sticky-bullet:")


def _plain(text: str) -> str:
    """엔티티로 적은 글자를 풀고(Qt 는 태그 이름과 속성 이름의 엔티티도 푼다)
    제어 문자를 뺀 소문자."""
    return _CONTROL.sub("", html.unescape(text)).lower()


def _url_allowed(value: str) -> bool:
    # 그림 본문은 수 MB 일 수 있다. 앞부분만 본다.
    head = _plain(value[:96]).strip()
    return head.startswith(_ALLOWED_PREFIXES)


def _css_is_safe(css: str) -> bool:
    """스타일 안에 바깥 자원을 부르는 구문이 없는가."""
    flat = _CSS_NOISE.sub("", _without_css_comments(_plain(css)))
    return "url(" not in flat and "@import" not in flat


def _without_css_comments(text: str) -> str:
    """``/* ... */`` 를 뺀 글. 닫히지 않은 주석은 그대로 둔다.

    정규식으로 찾으면 닫히지 않는 ``/*`` 가 줄줄이 있을 때 하나마다 끝까지
    훑는다. 앞에서부터 한 번만 훑는다."""
    if "/*" not in text:
        return text
    out: list[str] = []
    pos = 0
    while True:
        start = text.find("/*", pos)
        end = text.find("*/", start + 2) if start >= 0 else -1
        if end < 0:
            out.append(text[pos:])
            return "".join(out)
        out.append(text[pos:start])
        pos = end + 2


def _tag_name(tag: str) -> tuple[bool, str]:
    m = _NAME.match(tag)
    if not m:
        return False, ""
    return bool(m.group(1)), _plain(m.group(2))


def _tag_is_safe(tag: str, name: str) -> bool:
    if name in _DROP_TAGS:
        return False
    for raw_key, raw_value in _ATTR.findall(tag):
        key = _plain(raw_key)
        value = raw_value
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key in _URL_ATTRS and not _url_allowed(value):
            return False
        if key == "style" and not _css_is_safe(value):
            return False
    return True


# 이 앱이 저장하는 서식(HTML)에 나오는 태그들. 이 가운데 하나라도 있어야 HTML 로 본다.
_KNOWN_TAG = re.compile(
    r"</?(?:!doctype|html|head|body|meta|title|style|p|br|span|div|a|img|"
    r"b|i|u|s|strong|em|ul|ol|li|table|tbody|thead|tr|td|th|font|h[1-6]|hr|pre|"
    r"blockquote|sub|sup|center)(?=[\s/>])[^<>]*>", re.I)


def is_markup(content) -> bool:
    """서식이 든 저장 형식(HTML)인가.

    서식이 없는 메모는 쓴 글자 그대로 저장된다. 그런 글에 꺾쇠가 들어 있다고
    HTML 로 읽으면('홍길동 <hong@example.com>', 'a < b 이고 c > d') 꺾쇠 사이의
    글이 태그로 읽혀 사라진다. 아는 태그가 있을 때만 HTML 로 본다."""
    return (isinstance(content, str) and "<" in content
            and _KNOWN_TAG.search(content) is not None)


def clean(content) -> str:
    """본문에서 바깥 자원을 부르는 조각을 걷어 낸 것. 그런 조각이 없으면 받은
    글자 그대로 돌려준다."""
    if not isinstance(content, str):
        return ""
    if "<" not in content:
        return content
    out: list[str] = []
    pos = 0
    changed = False
    matches = list(_TAG.finditer(content))
    i = 0
    while i < len(matches):
        m = matches[i]
        tag = m.group(0)
        closing, name = _tag_name(tag)
        keep_until = m.end()
        drop = False
        if not closing and name == "style":
            # <style> 묶음은 닫는 태그까지 한 덩어리로 본다.
            j = i + 1
            end = len(content)
            while j < len(matches):
                c_closing, c_name = _tag_name(matches[j].group(0))
                if c_closing and c_name == "style":
                    end = matches[j].end()
                    break
                j += 1
            body = content[m.end():matches[j].start() if j < len(matches) else end]
            if not _tag_is_safe(tag, name) or not _css_is_safe(body):
                drop = True
            keep_until = end
            i = j if j < len(matches) else len(matches) - 1
        elif not closing and not _tag_is_safe(tag, name):
            drop = True
        out.append(content[pos:m.start()])
        if drop:
            changed = True
        else:
            out.append(content[m.start():keep_until])
        pos = keep_until
        i += 1
    if not changed:
        return content
    out.append(content[pos:])
    return "".join(out)
