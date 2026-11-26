"""파일을 메모 본문에 넣는 방법을 파일 종류마다 정한다(파일 종류별로 첨부).

지금까지는 그림만 본문에 들어가고 나머지는 모두 링크가 되었다. 여기서는 종류에
맞는 방법을 고른다. 글 파일은 글로, 표 파일은 표로, 마크다운은 서식 있는 글로,
메일은 요약으로 넣는다. 넣은 뒤 다른 방법으로 바꾸는 단추는 편집기가 단다. 이
모듈은 경로와 방법을 받아 ``QTextCursor.insertHtml`` 에 그대로 넣을 HTML 조각을
만드는 변환 층이다.

지키는 것:

- 파일은 읽기만 한다. 실행 파일과 스크립트는 내용을 읽지도 않는다.
- 어떤 파일도 앞부분 2MB 넘게 읽지 않는다. 압축 문서(docx, hwpx)는 항목 하나를
  5MB 까지만 푼다.
- 만든 HTML 은 바깥 자원을 부르지 않는다. 마크다운의 그림은 넣지 않고, 링크는
  웹(http, https)과 파일만 건다.
- 위젯을 만들지 않는다. 일꾼 스레드에서 불러도 된다. 마크다운은 함수 안에서
  문서를 만들어 쓰고 버린다(QGuiApplication 은 떠 있어야 한다).

링크는 편집기가 저장하는 꼴 그대로 ``<a href="file:///...">이름</a>`` 이다
(``RichTextEdit.insert_file_links`` 와 같은 href). 종류 아이콘 자리는 편집기가
``sync_link_icons`` 로 끼운다. 목록은 저장 형식(줄 앞의 글머리 그림, 번호 글자)으로
적는다. 넣은 뒤 메모가 불러올 때와 같은 변환을 거치면 편집기의 목록이 된다."""
from __future__ import annotations

import codecs
import csv
import html
import io
import os
import re
import struct
import zipfile
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser
from collections.abc import Callable, Mapping
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET

from . import file_links as fl
from . import html_guard

# ── 종류와 방법 ──────────────────────────────────────────────────────────

IMAGE = "image"
TEXT = "text"
MARKDOWN = "markdown"
TABLE = "table"
PDF = "pdf"
URL = "url"
SHORTCUT = "shortcut"
FOLDER = "folder"
DOCUMENT = "document"
MAIL = "mail"
PROGRAM = "program"
OTHER = "other"
KINDS = (IMAGE, TEXT, MARKDOWN, TABLE, PDF, URL, SHORTCUT, FOLDER, DOCUMENT,
         MAIL, PROGRAM, OTHER)

M_IMAGE = "image"       # 그림으로(편집기가 넣는다)
M_LINK = "link"         # 링크 하나
M_TEXT = "text"         # 글로
M_BOTH = "both"         # 글과 링크
M_RICH = "rich"         # 서식 있는 글로(마크다운)
M_TABLE = "table"       # 표로
M_WEB = "web"           # 바로 가기가 가리키는 웹 주소로
M_LIST = "list"         # 폴더 안의 파일 목록으로
M_SUMMARY = "summary"   # 메일 요약으로

# 종류마다 고를 수 있는 방법. 첫 것이 기본이다.
# PDF 의 '첫 쪽 그림과 링크'는 빌드에 QtPdf 를 넣으면 여기와 _BUILDERS 에 더한다.
_MODES: dict[str, tuple[str, ...]] = {
    IMAGE: (M_IMAGE, M_LINK),
    TEXT: (M_TEXT, M_LINK, M_BOTH),
    MARKDOWN: (M_RICH, M_TEXT, M_LINK),
    TABLE: (M_TABLE, M_TEXT, M_LINK),
    PDF: (M_LINK,),
    URL: (M_WEB, M_LINK),
    SHORTCUT: (M_LINK,),
    FOLDER: (M_LINK, M_LIST),
    DOCUMENT: (M_LINK, M_TEXT),
    MAIL: (M_SUMMARY, M_LINK),
    PROGRAM: (M_LINK,),
    OTHER: (M_LINK,),
}

# 그림은 편집기가 본문에 넣는 형식(rich_text._IMAGE_EXTS)과 같다.
_EXT_GROUPS = {
    IMAGE: "png jpg jpeg gif webp bmp apng",
    TEXT: "txt log",
    MARKDOWN: "md markdown",
    TABLE: "csv tsv",
    PDF: "pdf",
    DOCUMENT: "docx hwpx",
    MAIL: "eml",
}
_EXT_KIND = {ext: kind for kind, exts in _EXT_GROUPS.items() for ext in exts.split()}
# file_links.runs_when_opened 에 더해, 내용을 읽지 않고 링크로만 거는 것.
_PROGRAM_EXTS = frozenset("exe bat cmd ps1 vbs js msi com scr".split())

# 실패하면 방법을 링크로 바꾸고 까닭을 남긴다.
E_MISSING = "missing"           # 파일(바로 가기의 대상)이 없다
E_UNREADABLE = "unreadable"     # 읽거나 풀지 못했다
E_TOO_BIG = "too_big"           # 너무 커서 손대지 않았다(항목이 너무 많은 압축 파일)
E_EMPTY = "empty"               # 넣을 내용이 없다
E_UNSUPPORTED = "unsupported"   # 그 방법을 쓸 수 없다(웹 주소가 아닌 .url 등)

# ── 한도 ─────────────────────────────────────────────────────────────────

READ_LIMIT = 2 * 1024 * 1024        # 어떤 파일이든 이만큼까지만 읽는다
TEXT_LIMIT = 200 * 1024             # 글로 넣는 파일은 앞의 이만큼만(바이트)
CSV_READ_LIMIT = 512 * 1024         # 표를 만들려고 읽는 양
ZIP_MEMBER_LIMIT = 5 * 1024 * 1024  # 압축 문서의 항목 하나를 푸는 한도
ZIP_MAX_MEMBERS = 10000
HWPX_MAX_SECTIONS = 100
URL_FILE_LIMIT = 64 * 1024
LNK_LIMIT = 1024 * 1024
MAIL_BODY_CHARS = 2000
FOLDER_ITEMS = 50
FOLDER_SCAN = 10000                 # 폴더 하나에서 훑어보는 항목 수
CELL_CHARS = 1000
# 표 한 개의 한도와 모양은 메모의 표 넣기(sticky_note.insert_table)와 같다.
TABLE_MAX_ROWS = 50
TABLE_MAX_COLS = 12
TABLE_BORDER = "#9a9a9a"
MAX_LEVEL = 3                       # 목록 단계(rich_text.MAX_LEVEL)

# ── 결과 ─────────────────────────────────────────────────────────────────


@dataclass
class Attachment:
    """파일 하나를 넣은 결과.

    ``html`` 은 ``insertHtml`` 에 그대로 넣는다. 그림 방법(``M_IMAGE``)은 비어
    있다. 편집기가 ``source`` 의 그림을 직접 넣는다. ``plain`` 은 서식 없이 넣을
    때 쓴다. 실패하면 ``mode`` 가 링크로 바뀌고 ``error`` 에 까닭이 남는다."""
    path: str                       # 받은 경로
    kind: str                       # 실제로 쓴 종류(바로 가기는 대상의 종류)
    mode: str                       # 실제로 쓴 방법
    modes: list[str] = field(default_factory=list)   # 고를 수 있는 방법, 첫 것이 기본
    html: str = ""
    plain: str = ""
    rows: list[list[str]] | None = None   # 표로 넣었을 때의 칸 글자
    truncated: bool = False         # 앞부분만 넣었다
    target: str | None = None       # 바로 가기가 가리키는 경로
    error: str | None = None

    @property
    def source(self) -> str:
        """실제로 읽은 파일(바로 가기면 그 대상)."""
        return self.target or self.path


@dataclass
class _Options:
    overrides: Mapping[str, str] | None = None
    checkbox_urls: tuple[str, str] | None = None
    font_pt: float = 11.0


@dataclass
class _Job:
    path: str           # 읽을 파일
    link_path: str      # 링크로 걸 파일
    att: Attachment
    opts: _Options


class _Fallback(Exception):
    """이 방법으로 넣지 못한다. 링크로 물러선다."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# ── 종류 판정 ────────────────────────────────────────────────────────────

def _norm(path: str) -> str:
    raw = str(path or "").strip().strip('"').strip()
    return os.path.normpath(raw) if raw else ""


def _is_dir(path: str) -> bool:
    try:
        return os.path.isdir(path)
    except (OSError, ValueError):
        return False


def _exists(path: str) -> bool:
    try:
        return os.path.exists(path)
    except (OSError, ValueError):
        return False


def _ext(path: str) -> str:
    # Windows 가 떼고 여는 이름 끝의 점과 빈칸, 데이터 스트림 표기를 링크와 같은
    # 규칙으로 뗀다('a.cmd.' 는 cmd 다).
    return fl._ext_of(path)


def kind_of_path(path: str) -> str:
    """경로의 종류. 폴더인지는 파일 시스템에 묻고, 나머지는 확장자로 정한다."""
    p = _norm(path)
    if not p:
        return OTHER
    if _is_dir(p):
        return FOLDER
    ext = _ext(p)
    # url 과 lnk 는 여는 것이 곧 실행인 목록에도 들어 있다. 먼저 가른다.
    if ext == "url":
        return URL
    if ext == "lnk":
        return SHORTCUT
    if ext in _PROGRAM_EXTS or fl.runs_when_opened(p):
        return PROGRAM
    return _EXT_KIND.get(ext, OTHER)


def modes_for(kind: str) -> list[str]:
    """그 종류에서 고를 수 있는 방법. 첫 것이 기본이다."""
    return list(_MODES.get(kind, (M_LINK,)))


def default_mode(kind: str, overrides: Mapping[str, str] | None = None) -> str:
    """기본 방법. ``overrides`` 는 설정 표({종류: 방법})다. 그 종류에 없는 방법은 무시한다."""
    modes = modes_for(kind)
    if overrides:
        want = overrides.get(kind)
        if want in modes:
            return want
    return modes[0]


# ── 넣기 ─────────────────────────────────────────────────────────────────

def attach(path: str, mode: str | None = None, *,
           overrides: Mapping[str, str] | None = None,
           checkbox_urls: tuple[str, str] | None = None,
           font_pt: float = 11.0) -> Attachment:
    """파일 하나를 넣을 HTML 을 만든다.

    ``mode`` 가 None 이면 기본 방법(``overrides`` 반영)이다. 그 종류에 없는
    방법이면 기본 방법으로 넣고 ``error`` 를 ``unsupported`` 로 둔다.

    ``checkbox_urls`` 는 편집기의 체크 상자 그림 주소(빈 상자, 찬 상자)다.
    화면 스레드에서 ``rich_text.checkbox_url(False/True)`` 로 구해 넘긴다. 없으면
    마크다운의 할 일은 ☐ ☑ 글자로 넣는다. ``font_pt`` 는 메모의 기본 글자 크기로,
    마크다운 제목의 크기를 정한다."""
    opts = _Options(overrides, _valid_checkbox_urls(checkbox_urls), float(font_pt or 11.0))
    p = _norm(path)
    kind = kind_of_path(p)
    if kind == SHORTCUT:
        return _attach_shortcut(p, mode, opts)
    return _attach_kind(p, kind, mode, opts, link_path=p, original=p)


def attach_many(paths: list[str], **kwargs) -> list[Attachment]:
    """여러 파일을 받은 차례대로. 방법은 모두 기본이다(``attach`` 의 이름 인자를 함께 받는다)."""
    return [attach(p, **kwargs) for p in paths]


def _attach_kind(path: str, kind: str, mode: str | None, opts: _Options, *,
                 link_path: str, original: str) -> Attachment:
    modes = modes_for(kind)
    error = None
    if mode is None:
        mode = default_mode(kind, opts.overrides)
    elif mode not in modes:
        mode, error = default_mode(kind, opts.overrides), E_UNSUPPORTED
    att = Attachment(path=original, kind=kind, mode=mode, modes=modes, error=error)
    job = _Job(path, link_path, att, opts)
    if mode != M_LINK and not _exists(path):
        _fill_link(job, E_MISSING)
        return att
    builder = _BUILDERS.get((kind, mode), _build_link)
    try:
        builder(job)
    except _Fallback as fb:
        _fill_link(job, fb.reason)
    except OSError:
        _fill_link(job, E_UNREADABLE)
    except Exception:  # noqa: BLE001
        # 남이 만든 파일이다. 어떤 형식 오류든 링크로 물러선다.
        _fill_link(job, E_UNREADABLE)
    return att


def _attach_shortcut(lnk: str, mode: str | None, opts: _Options) -> Attachment:
    """바로 가기(.lnk)는 대상을 찾아 그 종류의 규칙을 따른다. 찾지 못하면
    바로 가기 파일 자체에 링크를 건다."""
    try:
        target = resolve_shortcut(lnk)
    except Exception:  # noqa: BLE001
        target = None
    if target and _exists(target):
        kind = kind_of_path(target)
        if kind != SHORTCUT:
            # 프로그램은 바로 가기에 걸어 둔다. 바로 가기에 적힌 인자와 시작 폴더가 남는다.
            link_path = lnk if kind == PROGRAM else target
            att = _attach_kind(target, kind, mode, opts, link_path=link_path,
                               original=lnk)
            att.target = target
            return att
    if not target:
        error = E_UNREADABLE
    elif not _exists(target):
        error = E_MISSING
    else:
        error = E_UNSUPPORTED       # 바로 가기의 바로 가기는 따라가지 않는다
    att = Attachment(path=lnk, kind=SHORTCUT, mode=M_LINK, modes=modes_for(SHORTCUT),
                     target=target)
    _fill_link(_Job(lnk, lnk, att, opts), error)
    return att


# ── HTML 조각 ────────────────────────────────────────────────────────────

_P_STYLE = "margin-top:0px; margin-bottom:0px; white-space:pre-wrap;"
_EMPTY_P = ('<p style="-qt-paragraph-type:empty; margin-top:0px; margin-bottom:0px;">'
            '<br /></p>')
# 메모의 표 넣기와 같은 모양(Qt 가 그 표를 HTML 로 적는 꼴 그대로).
_TABLE_OPEN = (f'<table border="1" style=" border-color:{TABLE_BORDER}; '
               'border-style:solid; border-collapse:collapse;" width="100%" '
               'cellspacing="0" cellpadding="4">')
# 저장 형식의 목록 표시(sticky_note._marker_save_html 이 쓰는 꼴).
_BULLET_IMG = '<img src="x-sticky-bullet:square" />'
_NUMBER_ANCHOR = '<a name="memil-num"></a>'
_CHECKBOX_URL = re.compile(r"data:image/png;base64,[A-Za-z0-9+/=]+\Z")
_CHECK_GLYPHS = ("☐", "☑")
ELLIPSIS = "…"


def _esc(text: str) -> str:
    return html.escape(text, quote=True)


def _para(inner: str, level: int = 0) -> str:
    if not inner:
        return _EMPTY_P
    style = _P_STYLE + (f" -qt-block-indent:{level};" if level else "")
    return f'<p style="{style}">{inner}</p>'


def lines_html(text: str) -> str:
    """평문을 줄마다 문단으로. 빈칸과 탭은 그대로 둔다."""
    return "".join(_para(_esc(line)) for line in text.split("\n"))


def link_href(path: str) -> str:
    """편집기가 끌어다 놓은 파일에 거는 것과 같은 href(네트워크 드라이브는 공유 경로로)."""
    p = os.path.normpath(path)
    target = fl.mapped_drive_unc(p) or p
    return fl.path_to_href(target, is_dir=_is_dir(p))


def link_html(path: str, label: str | None = None) -> str:
    """파일·폴더 링크 하나. 글자를 비워 두면 파일 이름이다."""
    href = link_href(path)
    text = " ".join((label or "").split()) or fl.display_name(href)
    return f'<a href="{_esc(href)}">{_esc(text)}</a>'


def table_html(rows: list[list[str]]) -> str:
    """칸 글자로 표를 만든다. 모양은 메모의 표 넣기와 같다."""
    return _table_from_cells([[_cell_html(c) for c in row] for row in rows])


def _cell_html(text: str) -> str:
    text = _clean_text(text)
    if len(text) > CELL_CHARS:
        text = text[:CELL_CHARS] + ELLIPSIS
    return "<br />".join(_esc(line) for line in text.split("\n"))


def _table_from_cells(cells: list[list[str]]) -> str:
    if not cells:
        return ""
    width = max(len(r) for r in cells)
    out = [_TABLE_OPEN]
    for row in cells:
        out.append("<tr>")
        for c in list(row) + [""] * (width - len(row)):
            out.append(f"<td>{c}</td>")
        out.append("</tr>")
    out.append("</table>")
    return "".join(out)


def _valid_checkbox_urls(urls) -> tuple[str, str] | None:
    if (isinstance(urls, (tuple, list)) and len(urls) == 2
            and all(isinstance(u, str) and _CHECKBOX_URL.match(u) for u in urls)):
        return urls[0], urls[1]
    return None


def _tail(link: str) -> str:
    """앞부분만 넣었을 때 끝에 붙이는 줄(말줄임표와 파일 링크)."""
    return _para(f"{ELLIPSIS} {link}")


def _finish(att: Attachment, body: str, link_path: str, *, with_link: bool = False) -> None:
    """본문 뒤에 링크를 붙여 마무리한다. 잘렸으면 말줄임표와 링크를 붙인다."""
    link = link_html(link_path)
    if with_link:
        body += (_para(ELLIPSIS) if att.truncated else "") + _para(link)
    elif att.truncated:
        body += _tail(link)
    att.html = html_guard.clean(body)


# ── 글 읽기 ──────────────────────────────────────────────────────────────

_BOMS = ((codecs.BOM_UTF8, "utf-8-sig", "utf-8"),
         (codecs.BOM_UTF16_LE, "utf-16-le", "utf-16-le"),
         (codecs.BOM_UTF16_BE, "utf-16-be", "utf-16-be"))
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f﻿￼]")


def _read_head(path: str, limit: int) -> tuple[bytes, bool]:
    """앞의 ``limit`` 바이트와, 그 뒤에 더 있는지."""
    limit = min(limit, READ_LIMIT)
    with open(path, "rb") as f:
        data = f.read(limit + 1)
    if len(data) > limit:
        return data[:limit], True
    return data, False


def _decode(data: bytes, encoding: str, complete: bool, errors: str) -> str:
    # 잘린 앞부분이면 끝에 걸친 글자 조각을 버린다(오류로 보지 않는다).
    decoder = codecs.getincrementaldecoder(encoding)(errors=errors)
    return decoder.decode(data, final=complete)


def decode_text(data: bytes, complete: bool = True) -> tuple[str, str]:
    """바이트를 글로. ``(글, 인코딩 이름)``.

    BOM(UTF-8, UTF-16 LE/BE)이 있으면 따르고, 없으면 UTF-8, CP949, latin-1
    차례로 해 본다. ``complete`` 가 False 면 잘린 앞부분으로 본다."""
    for bom, name, codec in _BOMS:
        if data.startswith(bom):
            return _decode(data[len(bom):], codec, complete, "replace"), name
    for codec in ("utf-8", "cp949"):
        try:
            return _decode(data, codec, complete, "strict"), codec
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1"), "latin-1"


def _clean_text(text: str) -> str:
    """줄바꿈을 ``\\n`` 으로 맞추고 제어 문자를 뺀다(탭은 둔다)."""
    text = (text.replace("\r\n", "\n").replace("\r", "\n")
            .replace(" ", "\n").replace(" ", "\n"))
    return _CTRL.sub("", text)


def read_text(path: str, limit: int = TEXT_LIMIT) -> tuple[str, bool]:
    """글 파일의 앞부분. ``(글, 잘렸는가)``. 잘렸으면 마지막 줄바꿈에서 끊는다."""
    data, cut = _read_head(path, limit)
    text, _enc = decode_text(data, complete=not cut)
    text = _clean_text(text)
    if cut:
        end = text.rfind("\n")
        if end > 0:
            text = text[:end]
    return text, cut


def _trim_blank_lines(text: str) -> str:
    lines = text.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


# ── 만드는 방법들 ────────────────────────────────────────────────────────

def _fill_link(job: _Job, error: str | None) -> None:
    att = job.att
    att.mode = M_LINK
    if error:
        att.error = error
    att.html = _para(link_html(job.link_path))
    att.plain = job.link_path
    att.rows = None
    att.truncated = False


def _build_link(job: _Job) -> None:
    _fill_link(job, None)


def _build_image(job: _Job) -> None:
    # 편집기가 원본 바이트와 움직이는 그림을 그대로 다룬다. 여기서는 가르기만 한다.
    job.att.html = ""
    job.att.plain = ""


def _build_text(job: _Job, *, with_link: bool = False) -> None:
    text, cut = read_text(job.path)
    text = _trim_blank_lines(text)
    if not text.strip():
        raise _Fallback(E_EMPTY)
    att = job.att
    att.truncated = cut
    att.plain = text + (f"\n{ELLIPSIS}" if cut else "")
    if with_link or cut:
        att.plain += f"\n{job.link_path}"
    _finish(att, lines_html(text), job.link_path, with_link=with_link)


def _build_both(job: _Job) -> None:
    _build_text(job, with_link=True)


# ── 표(csv, tsv) ──

def _sniff_delimiter(sample: str, ext: str) -> str:
    """쉼표, 탭, 쌍반점 가운데 줄마다 같은 칸 수를 가장 많이 내는 것."""
    order = ("\t", ",", ";") if ext == "tsv" else (",", ";", "\t")
    best, best_score = order[0], 0
    for delim in order:
        try:
            rows = [r for _, r in zip(range(30), csv.reader(io.StringIO(sample),
                                                            delimiter=delim)) if r]
        except csv.Error:
            continue
        counts: dict[int, int] = {}
        for r in rows:
            counts[len(r)] = counts.get(len(r), 0) + 1
        if not counts:
            continue
        width, freq = max(counts.items(), key=lambda kv: (kv[1], kv[0]))
        if width < 2:
            continue
        score = freq * 1000 + width
        if score > best_score:
            best, best_score = delim, score
    return best


def read_table(path: str) -> tuple[list[list[str]], bool]:
    """표 파일의 앞부분을 칸 글자로. ``(줄들, 잘렸는가)``. 50줄, 12칸까지."""
    data, cut = _read_head(path, CSV_READ_LIMIT)
    text, _enc = decode_text(data, complete=not cut)
    text = _clean_text(text)
    if cut:
        end = text.rfind("\n")
        if end > 0:
            text = text[:end]
    delim = _sniff_delimiter(text[:64 * 1024], _ext(path))
    rows: list[list[str]] = []
    truncated = cut
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    try:
        for row in reader:
            if not row:
                continue
            if len(rows) >= TABLE_MAX_ROWS:
                truncated = True
                break
            if len(row) > TABLE_MAX_COLS:
                truncated = True
                row = row[:TABLE_MAX_COLS]
            rows.append(row)
    except csv.Error:
        # 칸 하나가 너무 길거나 따옴표가 깨졌다. 읽은 데까지만 쓴다.
        if not rows:
            raise
        truncated = True
    if not rows:
        return [], truncated
    width = max(len(r) for r in rows)
    return [r + [""] * (width - len(r)) for r in rows], truncated


def _build_table(job: _Job) -> None:
    rows, cut = read_table(job.path)
    if not rows or not any(c.strip() for r in rows for c in r):
        raise _Fallback(E_EMPTY)
    att = job.att
    att.rows = rows
    att.truncated = cut
    att.plain = "\n".join("\t".join(r) for r in rows)
    if cut:
        att.plain += f"\n{ELLIPSIS}\n{job.link_path}"
    _finish(att, table_html(rows), job.link_path)


# ── 마크다운 ──

_HEADING_SCALE = {1: 1.5, 2: 1.3, 3: 1.15}


def _safe_href(href: str, base_dir: str) -> str:
    """마크다운 링크를 메모 링크로. 웹과 파일만 받는다. 상대 경로는 파일 옆으로 본다."""
    h = (href or "").strip()
    if not h or h.startswith("#") or len(h) > 4096:
        return ""
    if fl.is_web(h):
        if re.search(r"[\s\x00-\x1f\x7f]", h) or not urlsplit(h).netloc.strip("."):
            return ""
        return h
    if fl.is_file_href(h):
        path = fl.href_to_path(h)
        return fl.path_to_href(path) if path else ""
    if fl.is_local_drive(h) or fl.is_unc(h):
        return fl.path_to_href(h)
    if re.match(r"^[A-Za-z][A-Za-z0-9+.\-]*:", h):
        return ""       # javascript:, mailto: 등
    rel = unquote(h.split("#", 1)[0].split("?", 1)[0])
    if not rel or not base_dir:
        return ""
    return fl.path_to_href(os.path.normpath(os.path.join(base_dir, rel)))


def _md_inline(block, base_dir: str, *, heading: bool = False) -> str:
    """줄 안의 글과 서식. 제목 줄과 표의 머리 줄은 굵기를 따로 주므로 굵게 표시를
    빼고 적는다(Qt 가 그 줄들을 이미 굵게 읽는다)."""
    from PySide6.QtGui import QTextFormat
    parts: list[str] = []
    it = block.begin()
    while not it.atEnd():
        frag = it.fragment()
        it += 1
        if not frag.isValid():
            continue
        fmt = frag.charFormat()
        if fmt.isImageFormat():
            # 그림은 넣지 않는다(바깥 주소를 열지 않게). 대체 글만 남긴다.
            alt = " ".join(fmt.stringProperty(QTextFormat.ImageAltText).split())
            if alt:
                parts.append(_esc(alt))
            continue
        text = _clean_text(frag.text())
        if not text:
            continue
        s = "<br />".join(_esc(t) for t in text.split("\n"))
        if fmt.fontWeight() >= 600 and not heading:
            s = f"<b>{s}</b>"
        if fmt.fontItalic():
            s = f"<i>{s}</i>"
        if fmt.fontStrikeOut():
            s = f"<s>{s}</s>"
        href = _safe_href(fmt.anchorHref(), base_dir) if fmt.isAnchor() else ""
        if href:
            s = f'<a href="{_esc(href)}">{s}</a>'
        parts.append(s)
    return "".join(parts)


def _md_cell_blocks(doc, cell):
    block = doc.findBlock(cell.firstPosition())
    while block.isValid() and block.position() <= cell.lastPosition():
        yield block
        block = block.next()


def _md_table(doc, table, base_dir: str) -> tuple[str, list[list[str]], bool]:
    rows = min(table.rows(), TABLE_MAX_ROWS)
    cols = min(table.columns(), TABLE_MAX_COLS)
    cells: list[list[str]] = []
    plain: list[list[str]] = []
    for r in range(rows):
        row_html, row_plain = [], []
        for c in range(cols):
            blocks = list(_md_cell_blocks(doc, table.cellAt(r, c)))
            # 마크다운 표의 첫 줄은 머리 줄이다. 줄째 한 번만 굵게 적는다.
            inner = "<br />".join(_md_inline(b, base_dir, heading=r == 0) for b in blocks)
            if r == 0 and inner:
                inner = f"<b>{inner}</b>"
            row_html.append(inner)
            row_plain.append(_clean_text("\n".join(b.text() for b in blocks)))
        cells.append(row_html)
        plain.append(row_plain)
    cut = table.rows() > TABLE_MAX_ROWS or table.columns() > TABLE_MAX_COLS
    return _table_from_cells(cells), plain, cut


def _md_list_lead(block, opts: _Options) -> str:
    from PySide6.QtGui import QTextBlockFormat, QTextListFormat
    marker = block.blockFormat().marker()
    if marker in (QTextBlockFormat.MarkerType.Unchecked, QTextBlockFormat.MarkerType.Checked):
        checked = marker == QTextBlockFormat.MarkerType.Checked
        if opts.checkbox_urls:
            return f'<img src="{opts.checkbox_urls[int(checked)]}" />  '
        return _CHECK_GLYPHS[int(checked)] + " "
    lst = block.textList()
    style = lst.format().style()
    S = QTextListFormat.Style
    if style in (S.ListDecimal, S.ListLowerAlpha, S.ListUpperAlpha,
                 S.ListLowerRoman, S.ListUpperRoman):
        return f"{_NUMBER_ANCHOR}{lst.itemNumber(block) + 1}. "
    return _BULLET_IMG + "  "


def markdown_html(text: str, base_dir: str = "", *,
                  checkbox_urls: tuple[str, str] | None = None,
                  font_pt: float = 11.0) -> tuple[str, str, bool]:
    """마크다운을 메모 본문 HTML 로. ``(html, 평문, 표가 잘렸는가)``.

    Qt 의 마크다운 읽기(GitHub 방언, HTML 은 글자로)로 구조를 얻은 뒤 이 앱의
    저장 형식으로 다시 적는다. 제목은 굵고 큰 글자, 목록은 글머리(네모)·번호·할 일,
    표는 메모의 표 모양이다. 그림은 넣지 않는다."""
    from PySide6.QtGui import QTextDocument, QTextFormat, QTextTable

    from .ui.safe_text import SafeTextDocument

    opts = _Options(None, _valid_checkbox_urls(checkbox_urls), float(font_pt or 11.0))
    # 어떤 경우에도 본문이 가리키는 파일·네트워크 경로를 열지 않는 문서.
    doc = SafeTextDocument()
    feats = (QTextDocument.MarkdownFeature.MarkdownDialectGitHub
             | QTextDocument.MarkdownFeature.MarkdownNoHTML)
    doc.setMarkdown(text, feats)
    out: list[str] = []
    prev: str | None = None
    force_gap = False
    cut = False
    top_list = -1

    def emit(group: str, piece: str) -> None:
        # 같은 목록의 줄, 이어진 코드와 인용은 붙이고 나머지 묶음 사이에는 빈 줄을
        # 둔다. 제목 바로 뒤에는 두지 않는다.
        nonlocal prev, force_gap
        if prev is not None:
            joined = (prev == group and group.startswith(("list", "code", "quote"))
                      and not force_gap)
            if not joined and prev != "heading":
                out.append(_EMPTY_P)
        out.append(piece)
        prev, force_gap = group, False

    it = doc.rootFrame().begin()
    while not it.atEnd():
        frame = it.currentFrame()
        block = it.currentBlock()
        it += 1
        if frame is not None:
            if isinstance(frame, QTextTable):
                piece, _rows, table_cut = _md_table(doc, frame, base_dir)
                cut = cut or table_cut
                if piece:
                    emit("table", piece)
            continue
        if not block.isValid():
            continue
        bf = block.blockFormat()
        if bf.hasProperty(QTextFormat.BlockTrailingHorizontalRulerWidth):
            force_gap = True
            continue
        level = bf.headingLevel()
        if level:
            inner = _md_inline(block, base_dir, heading=True)
            if not inner:
                continue
            scale = _HEADING_SCALE.get(level, 1.0)
            size = f" font-size:{round(opts.font_pt * scale)}pt;" if scale != 1.0 else ""
            emit("heading", _para(f'<span style="{size.strip()} font-weight:700;">'
                                  f"{inner}</span>"))
        elif block.textList() is not None:
            lst = block.textList()
            depth = max(0, min(MAX_LEVEL, lst.format().indent() - 1))
            if depth == 0 or top_list < 0:
                # 맨 위 단계 목록의 첫 줄 자리로 목록을 가른다(딸린 목록은 따로 센다).
                top_list = lst.item(0).position()
            emit(f"list@{top_list}", _para(_md_list_lead(block, opts)
                                           + _md_inline(block, base_dir), depth))
        elif bf.hasProperty(QTextFormat.BlockCodeFence):
            emit("code", _para(_esc(_clean_text(block.text()))))
        else:
            inner = _md_inline(block, base_dir)
            if not inner.strip():
                continue
            group = "quote" if bf.hasProperty(QTextFormat.BlockQuoteLevel) else "para"
            emit(group, _para(inner))
    plain = _clean_text(doc.toPlainText())
    return html_guard.clean("".join(out)), plain, cut


def _build_markdown(job: _Job) -> None:
    text, cut = read_text(job.path)
    if not text.strip():
        raise _Fallback(E_EMPTY)
    body, plain, table_cut = markdown_html(
        text, os.path.dirname(job.path), checkbox_urls=job.opts.checkbox_urls,
        font_pt=job.opts.font_pt)
    if not body:
        raise _Fallback(E_EMPTY)
    att = job.att
    att.truncated = cut or table_cut
    att.plain = _trim_blank_lines(plain)
    if att.truncated:
        att.plain += f"\n{ELLIPSIS}\n{job.link_path}"
    _finish(att, body, job.link_path)


# ── 인터넷 바로 가기(.url) ──

def _web_url(value: str) -> str | None:
    v = (value or "").strip()
    if not v or len(v) > 4096 or re.search(r"[\s\x00-\x1f\x7f]", v):
        return None
    parts = urlsplit(v)
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc.strip("."):
        return None
    return v


def read_url_file(path: str) -> str | None:
    """인터넷 바로 가기의 ``[InternetShortcut]`` 칸 ``URL=`` 값. 웹 주소가 아니면 None."""
    data, _cut = _read_head(path, URL_FILE_LIMIT)
    text, _enc = decode_text(data)
    section = ""
    for raw in _clean_text(text).split("\n"):
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            continue
        if section != "internetshortcut":
            continue
        key, sep, value = line.partition("=")
        if sep and key.strip().lower() == "url":
            return _web_url(value)
    return None


def _build_web(job: _Job) -> None:
    url = read_url_file(job.path)
    if not url:
        raise _Fallback(E_UNSUPPORTED)
    name = os.path.splitext(os.path.basename(job.path))[0].strip() or url
    att = job.att
    att.plain = url
    att.html = _para(f'<a href="{_esc(url)}">{_esc(name)}</a>')


# ── 폴더 ──

def _natural_key(name: str) -> list:
    return [int(t) if t.isdigit() else t.casefold() for t in re.split(r"(\d+)", name)]


def _hidden(entry) -> bool:
    try:
        attrs = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attrs & 0x6)     # 숨김(2), 시스템(4)


def list_folder(path: str, limit: int = FOLDER_ITEMS) -> tuple[list[tuple[str, bool]], bool]:
    """폴더 안의 ``(이름, 폴더인가)``. 폴더 먼저, 이름 차례. ``(목록, 더 있는가)``.
    숨김·시스템 항목은 뺀다."""
    dirs: list[str] = []
    files: list[str] = []
    more = False
    with os.scandir(path) as entries:
        for count, entry in enumerate(entries):
            if count >= FOLDER_SCAN:
                more = True
                break
            if _hidden(entry):
                continue
            try:
                is_dir = entry.is_dir()
            except OSError:
                is_dir = False
            (dirs if is_dir else files).append(entry.name)
    items = ([(n, True) for n in sorted(dirs, key=_natural_key)]
             + [(n, False) for n in sorted(files, key=_natural_key)])
    if len(items) > limit:
        items, more = items[:limit], True
    return items, more


def _build_folder_list(job: _Job) -> None:
    items, more = list_folder(job.path)
    att = job.att
    att.truncated = more
    base = fl.mapped_drive_unc(job.path) or job.path
    parts = [_para(link_html(job.link_path))]
    lines = [job.link_path]
    for name, is_dir in items:
        href = fl.path_to_href(os.path.join(base, name), is_dir=is_dir)
        parts.append(_para(f'<a href="{_esc(href)}">{_esc(name)}</a>'))
        lines.append(os.path.join(job.path, name))
    if more:
        parts.append(_para(ELLIPSIS))
        lines.append(ELLIPSIS)
    att.plain = "\n".join(lines)
    att.html = html_guard.clean("".join(parts))


# ── 문서(docx, hwpx) ──

def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _xml_paragraphs(stream, budget: list[int]) -> tuple[list[str], bool]:
    """워드·한글 XML 에서 문단 글자만 뽑는다. ``(문단들, 잘렸는가)``.

    문단(p), 글자(t), 탭(tab), 줄바꿈(br, cr, lineBreak)만 본다. 항목 하나는
    5MB 까지만 풀고, 모은 글자가 한도를 넘으면 멈춘다. ``budget`` 은 남은 글자 수다."""
    parser = ET.XMLPullParser(events=("start", "end"))
    stack: list[str] = []
    paras: list[str] = []
    pieces: list[list[str]] = []
    in_t = 0
    read = 0
    cut = False
    while True:
        chunk = stream.read(64 * 1024)
        if not chunk:
            break
        read += len(chunk)
        if read > ZIP_MEMBER_LIMIT:
            chunk = chunk[:len(chunk) - (read - ZIP_MEMBER_LIMIT)]
            cut = True
        try:
            parser.feed(chunk)
            events = list(parser.read_events())
        except ET.ParseError:
            if paras:
                return paras, True
            raise
        for event, elem in events:
            name = _local(elem.tag)
            if event == "start":
                stack.append(name)
                if name == "p":
                    pieces.append([])
                elif name == "t":
                    in_t += 1
                continue
            stack.pop()
            parent = stack[-1] if stack else ""
            if name == "t":
                in_t -= 1
                if pieces:
                    pieces[-1].append(_t_text(elem))
            elif name == "tab" and not in_t and parent != "tabs" and pieces:
                pieces[-1].append("\t")
            elif name in ("br", "cr", "lineBreak") and not in_t and pieces:
                pieces[-1].append("\n")
            elif name == "p" and pieces:
                text = _clean_text("".join(pieces.pop()))
                paras.append(text)
                budget[0] -= len(text) + 1
                elem.clear()
                if budget[0] <= 0:
                    return paras, True
        if cut:
            break
    return paras, cut


def _t_text(elem) -> str:
    """글자 요소 안의 글. 한글은 글 사이에 탭과 줄바꿈 요소를 끼운다."""
    out = [elem.text or ""]
    for child in elem:
        name = _local(child.tag)
        if name == "tab":
            out.append("\t")
        elif name == "lineBreak":
            out.append("\n")
        out.append(child.tail or "")
    return "".join(out)


def _hwpx_sections(names: list[str]) -> list[str]:
    found = []
    for n in names:
        m = re.fullmatch(r"Contents/section(\d+)\.xml", n, re.I)
        if m:
            found.append((int(m.group(1)), n))
    return [n for _i, n in sorted(found)][:HWPX_MAX_SECTIONS]


def read_document_text(path: str) -> tuple[str, bool]:
    """docx 와 hwpx 의 글자만. ``(글, 잘렸는가)``. 압축 항목은 5MB 까지만 푼다."""
    ext = _ext(path)
    budget = [TEXT_LIMIT]
    with zipfile.ZipFile(path) as zf:
        infos = zf.infolist()
        if len(infos) > ZIP_MAX_MEMBERS:
            raise _Fallback(E_TOO_BIG)
        names = [i.filename for i in infos]
        if ext == "docx":
            members = ["word/document.xml"] if "word/document.xml" in names else []
        else:
            members = _hwpx_sections(names)
        if not members:
            raise _Fallback(E_UNREADABLE)
        paras: list[str] = []
        cut = False
        for name in members:
            with zf.open(name) as stream:
                got, member_cut = _xml_paragraphs(stream, budget)
            paras.extend(got)
            if member_cut or budget[0] <= 0:
                cut = True
                break
    text = "\n".join(paras)
    if len(text) > TEXT_LIMIT:
        text, cut = text[:TEXT_LIMIT], True
    return text, cut


def _build_document_text(job: _Job) -> None:
    text, cut = read_document_text(job.path)
    text = _trim_blank_lines(text)
    if not text.strip():
        raise _Fallback(E_EMPTY)
    att = job.att
    att.truncated = cut
    att.plain = text + (f"\n{ELLIPSIS}\n{job.link_path}" if cut else "")
    _finish(att, lines_html(text), job.link_path)


# ── 메일(eml) ──

class _HtmlText(HTMLParser):
    """메일의 HTML 본문을 글로. 그림이나 바깥 자원은 보지 않는다."""

    _BLOCKS = frozenset("p div li tr table ul ol blockquote pre h1 h2 h3 h4 h5 h6 "
                        "hr section article header footer".split())
    _SKIP = frozenset(("script", "style", "head", "title"))

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs) -> None:
        if tag in self._SKIP:
            self.skip += 1
        elif tag == "br" or tag in self._BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag) -> None:
        if tag in self._SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in self._BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data) -> None:
        if not self.skip:
            self.parts.append(re.sub(r"\s+", " ", data))

    def text(self) -> str:
        lines = [" ".join(line.split()) for line in "".join(self.parts).split("\n")]
        return "\n".join(lines)


def html_to_text(markup: str) -> str:
    parser = _HtmlText()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:  # noqa: BLE001
        pass
    return parser.text()


def _header(msg, name: str) -> str:
    try:
        value = msg[name]
    except Exception:  # noqa: BLE001
        return ""
    return " ".join(str(value or "").split())


def _mail_date(msg) -> str:
    try:
        header = msg["date"]
        dt = getattr(header, "datetime", None)
    except Exception:  # noqa: BLE001
        return ""
    if dt is None:
        return " ".join(str(header or "").split())
    if dt.tzinfo is not None:
        dt = dt.astimezone()
    return dt.strftime("%Y.%m.%d %H:%M")


def _mail_body(msg) -> str:
    try:
        part = msg.get_body(preferencelist=("plain", "html"))
    except Exception:  # noqa: BLE001
        part = None
    if part is None:
        return ""
    try:
        content = part.get_content()
    except Exception:  # noqa: BLE001
        content = None
    if not isinstance(content, str):
        raw = part.get_payload(decode=True) or b""
        content, _enc = decode_text(raw if isinstance(raw, bytes) else b"")
    if part.get_content_subtype() == "html":
        content = html_to_text(content)
    return _clean_text(content)


def read_mail(path: str) -> dict:
    """메일 파일의 제목, 보낸 사람, 날짜, 본문 앞부분.
    ``{"subject", "from", "date", "body", "truncated"}``."""
    data, cut = _read_head(path, READ_LIMIT)
    msg = BytesParser(policy=policy.default).parsebytes(data)
    body = _trim_blank_lines(re.sub(r"\n{3,}", "\n\n", _mail_body(msg)))
    truncated = cut
    if len(body) > MAIL_BODY_CHARS:
        head = body[:MAIL_BODY_CHARS]
        space = max(head.rfind("\n"), head.rfind(" "))
        if space > MAIL_BODY_CHARS - 200:
            head = head[:space]
        body, truncated = head.rstrip() + ELLIPSIS, True
    return {"subject": _header(msg, "subject"), "from": _header(msg, "from"),
            "date": _mail_date(msg), "body": body, "truncated": truncated}


def _build_mail(job: _Job) -> None:
    mail = read_mail(job.path)
    subject = mail["subject"] or os.path.splitext(os.path.basename(job.path))[0]
    head = [subject, mail["from"], mail["date"]]
    parts = [_para(f"<b>{_esc(subject)}</b>")]
    parts += [_para(_esc(v)) for v in head[1:] if v]
    lines = [v for v in head if v]
    if mail["body"]:
        parts.append(_EMPTY_P)
        parts.append(lines_html(mail["body"]))
        lines += ["", mail["body"]]
    att = job.att
    att.truncated = mail["truncated"]
    # 요약은 늘 일부다. 메일 파일 링크를 끝에 붙인다.
    parts.append(_para(link_html(job.link_path)))
    lines.append(job.link_path)
    att.plain = "\n".join(lines)
    att.html = html_guard.clean("".join(parts))


# ── 바로 가기(.lnk) 풀기 ─────────────────────────────────────────────────
#
# MS-SHLLINK 형식을 직접 읽는다(COM 이나 다른 프로그램을 부르지 않는다).
# 머리(76바이트) → LinkTargetIDList → LinkInfo → StringData → ExtraData.

_LNK_CLSID = bytes.fromhex("0114020000000000c000000000000046")
_HAS_IDLIST, _HAS_LINKINFO, _HAS_NAME, _HAS_RELPATH = 0x1, 0x2, 0x4, 0x8
_HAS_WORKDIR, _HAS_ARGS, _HAS_ICON, _IS_UNICODE = 0x10, 0x20, 0x40, 0x80
_FORCE_NO_LINKINFO = 0x100
_ENV_BLOCK = 0xA0000001
try:
    codecs.lookup("mbcs")
    _ANSI = "mbcs"
except LookupError:
    _ANSI = "cp1252"


def _cstr(data: bytes, off: int) -> str:
    if off <= 0 or off >= len(data):
        return ""
    end = data.find(b"\0", off)
    raw = data[off:end if end >= 0 else len(data)]
    return raw.decode(_ANSI, errors="replace")


def _cstr16(data: bytes, off: int) -> str:
    if off <= 0 or off >= len(data):
        return ""
    end = off
    while end + 1 < len(data) and (data[end] or data[end + 1]):
        end += 2
    return data[off:end].decode("utf-16-le", errors="replace")


def _join(head: str, tail: str) -> str:
    if not tail:
        return head
    if head.endswith("\\"):
        return head + tail
    return head + "\\" + tail


def _linkinfo_path(info: bytes) -> str | None:
    if len(info) < 0x1C:
        return None
    (_size, header, flags, _vol, base_off, net_off,
     suffix_off) = struct.unpack_from("<7I", info, 0)
    base_u = suffix_u = 0
    if header >= 0x24 and len(info) >= 0x24:
        base_u, suffix_u = struct.unpack_from("<2I", info, 0x1C)
    suffix = _cstr16(info, suffix_u) if suffix_u else _cstr(info, suffix_off)
    if flags & 0x1:
        base = _cstr16(info, base_u) if base_u else _cstr(info, base_off)
        if base:
            return _join(base, suffix)
    if flags & 0x2 and 0 < net_off < len(info):
        blk = info[net_off:]
        if len(blk) >= 0x14:
            name_off = struct.unpack_from("<I", blk, 8)[0]
            name = ""
            if name_off > 0x14 and len(blk) >= 0x1C:
                name = _cstr16(blk, struct.unpack_from("<I", blk, 0x14)[0])
            name = name or _cstr(blk, name_off)
            if name:
                return _join(name, suffix)
    return None


def _beef0004_name(blk: bytes) -> str | None:
    """파일 항목에 딸린 확장 칸(0xBEEF0004)의 긴 이름."""
    if len(blk) < 20:
        return None
    size, version = struct.unpack_from("<HH", blk, 0)
    blk = blk[:size] if size >= 20 else blk
    if version >= 9:
        off = 46
    elif version >= 8:
        off = 42
    elif version >= 7:
        off = 38
    elif version >= 3:
        off = 20
    else:
        return None
    return _cstr16(blk, off) or None


_BAD_NAME = re.compile(r'[\x00-\x1f\\/:*?"<>|]')


def _file_entry_name(item: bytes) -> str | None:
    if len(item) < 13:
        return None
    if item[0] & 0x04:
        short = _cstr16(item, 12)
    else:
        short = _cstr(item, 12)
    sig = item.find(b"\x04\x00\xef\xbe", 12)
    name = _beef0004_name(item[sig - 4:]) if sig >= 16 else None
    name = name or short
    if not name or name in (".", "..") or _BAD_NAME.search(name):
        return None
    return name


def _idlist_path(idl: bytes) -> str | None:
    """LinkTargetIDList 의 항목(드라이브, 네트워크 위치, 폴더와 파일 이름)을 이어 경로로.
    모르는 항목(제어판, 라이브러리 등)이 있으면 None."""
    root = ""
    names: list[str] = []
    pos = 0
    while pos + 2 <= len(idl):
        size = struct.unpack_from("<H", idl, pos)[0]
        if size == 0:
            break
        if size < 3 or pos + size > len(idl):
            return None
        item = idl[pos + 2:pos + size]
        pos += size
        kind = item[0]
        cls = kind & 0x70
        if kind == 0x1F:
            continue        # 뿌리(내 PC 등). 드라이브 항목이 뒤따른다.
        if cls == 0x20:
            drive = item[1:].split(b"\0", 1)[0].decode("ascii", errors="replace")
            if not re.fullmatch(r"[A-Za-z]:\\?", drive):
                return None
            root, names = drive[:2] + "\\", []
        elif cls == 0x40:
            loc = _cstr(item, 3)
            if not loc.startswith("\\\\"):
                return None
            root, names = loc, []
        elif cls == 0x30:
            name = _file_entry_name(item)
            if not name or not root:
                return None
            names.append(name)
        else:
            return None
    if not root:
        return None
    path = root
    for name in names:
        path = _join(path, name)
    return path


def shell_link_targets(data: bytes, base_dir: str = "") -> list[str]:
    """바로 가기 파일 바이트에서 찾은 대상 경로 후보(믿을 만한 차례).
    LinkInfo, 환경 변수 칸, IDList, 상대 경로 차례다. 형식이 아니면 빈 목록."""
    if (len(data) < 76 or struct.unpack_from("<I", data, 0)[0] != 0x4C
            or data[4:20] != _LNK_CLSID):
        return []
    flags = struct.unpack_from("<I", data, 20)[0]
    pos = 76
    found: dict[str, str | None] = {"info": None, "env": None, "idlist": None,
                                    "relative": None}
    try:
        if flags & _HAS_IDLIST:
            size = struct.unpack_from("<H", data, pos)[0]
            found["idlist"] = _idlist_path(data[pos + 2:pos + 2 + size])
            pos += 2 + size
        if flags & _HAS_LINKINFO:
            size = struct.unpack_from("<I", data, pos)[0]
            if not flags & _FORCE_NO_LINKINFO:
                found["info"] = _linkinfo_path(data[pos:pos + size])
            pos += max(size, 4)
        unicode = bool(flags & _IS_UNICODE)
        for bit in (_HAS_NAME, _HAS_RELPATH, _HAS_WORKDIR, _HAS_ARGS, _HAS_ICON):
            if not flags & bit:
                continue
            count = struct.unpack_from("<H", data, pos)[0]
            pos += 2
            nbytes = count * 2 if unicode else count
            raw = data[pos:pos + nbytes]
            pos += nbytes
            if bit == _HAS_RELPATH:
                found["relative"] = raw.decode("utf-16-le" if unicode else _ANSI,
                                               errors="replace")
        while pos + 8 <= len(data):
            bsize, sig = struct.unpack_from("<II", data, pos)
            if bsize < 8:
                break
            if sig == _ENV_BLOCK and bsize >= 0x314 and pos + 0x314 <= len(data):
                # TargetAnsi(260바이트) 다음에 TargetUnicode(520바이트)가 온다.
                block = b"\0" + data[pos + 8:pos + 0x314]
                env = _cstr16(block, 1 + 260) or _cstr(block, 1)
                if env:
                    found["env"] = os.path.expandvars(env)
            pos += bsize
    except struct.error:
        pass
    rel = found["relative"]
    if rel:
        found["relative"] = os.path.join(base_dir, rel) if base_dir else rel
    out: list[str] = []
    for key in ("info", "env", "idlist", "relative"):
        value = found[key]
        if value and not _CTRL.search(value):
            norm = os.path.normpath(value)
            if norm not in out:
                out.append(norm)
    return out


def resolve_shortcut(path: str) -> str | None:
    """바로 가기(.lnk)의 대상 경로. 있는 것을 먼저 고르고, 없으면 첫 후보. 못 읽으면 None."""
    data, _cut = _read_head(path, LNK_LIMIT)
    candidates = shell_link_targets(data, os.path.dirname(os.path.abspath(path)))
    for c in candidates:
        if _exists(c):
            return c
    return candidates[0] if candidates else None


# ── 방법표 ───────────────────────────────────────────────────────────────

_BUILDERS: dict[tuple[str, str], Callable[[_Job], None]] = {
    (IMAGE, M_IMAGE): _build_image,
    (TEXT, M_TEXT): _build_text,
    (TEXT, M_BOTH): _build_both,
    (MARKDOWN, M_RICH): _build_markdown,
    (MARKDOWN, M_TEXT): _build_text,
    (TABLE, M_TABLE): _build_table,
    (TABLE, M_TEXT): _build_text,
    (URL, M_WEB): _build_web,
    (FOLDER, M_LIST): _build_folder_list,
    (DOCUMENT, M_TEXT): _build_document_text,
    (MAIL, M_SUMMARY): _build_mail,
}
