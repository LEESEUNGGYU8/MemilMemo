"""메모를 종이나 PDF 로 찍는다(인쇄, PDF로 저장).

회의록을 결재 서류에 붙이거나 게시판에 붙일 때 쓴다. 메모마다 위에 제목(크고
굵게), 그 아래 만든 사람과 고친 때, 그 아래 본문을 놓고, 쪽마다 맨 아래에 쪽
번호('1 / 3')를 적는다. 기본은 흰 바탕이다(잉크를 아낀다). '메모 색 그대로'를
고르면 메모가 차지하는 곳을 그 메모의 색으로 칠한다. 여러 장은 메모마다 한 쪽,
또는 이어서 찍는다.

본문은 다른 참가자가 보낸 글이다. 화면과 같이 html_guard 로 바깥 자원을 부르는
조각을 걷어 내고, SafeTextDocument 를 물려받은 문서에 놓는다(본문에 든 ``data:``
그림만 읽고 파일이나 네트워크 경로는 열지 않는다). 목록 기호와 체크 상자는 화면처럼
왼쪽 여백에 직접 그린다. 저장 형식의 줄 앞 그림과 번호 글자를 화면과 같은 규칙으로
읽어 단계마다 기호(■ ○ – ·)와 들여쓰기를 맞춘다.

흐름: build_document 가 문서를 만들고, layout_pages 가 종이 크기에 맞춰 쪽을 나누고,
paint_pages 가 쪽마다 그린다. save_pdf 와 print_notes 는 이 셋을 잇는다.

문서는 화면 해상도(Qt 의 기본 해상도, 보통 96)의 단위로 배치하고, 그릴 때 장치의
해상도만큼 키운다. 그래서 같은 종이면 PDF 와 프린터의 쪽 나눔이 같다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from PySide6.QtCore import (
    QBuffer,
    QCoreApplication,
    QIODevice,
    QMarginsF,
    QPointF,
    QRectF,
    QSizeF,
    Qt,
    QUrl,
)
from PySide6.QtGui import (
    QAbstractTextDocumentLayout,
    QColor,
    QFont,
    QGuiApplication,
    QImage,
    QImageReader,
    QPageLayout,
    QPageSize,
    QPainter,
    QPalette,
    QPdfWriter,
    QPen,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextFormat,
    QTextImageFormat,
    QTextLayout,
    QTextOption,
)

from .. import config, html_guard, i18n
from . import font_colors
from .note_card import note_title_and_lines
from .rich_text import (
    CHECK_CODES,
    LEVEL_PROP,
    LEVEL_STEP,
    LINK_COLOR_DARK,
    LINK_COLOR_LIGHT,
    MARKER_CHECK1,
    MARKER_CODES,
    MARKER_NONE,
    MARKER_NUMBER,
    MARKER_PROP,
    MAX_LEVEL,
    MIN_READABLE_CONTRAST,
    NUMBER_ANCHOR,
    RichTextEdit,
    bullet_image,
    contrast_ratio,
    data_url_payload,
    draw_checkbox,
    draw_marker_shape,
    is_marker_url,
    marker_code_for_url,
    marker_column,
    marker_ink,
    marker_shape,
    url_to_bullet_name,
)
from .safe_text import SafeTextDocument, data_url_is_raster, safe_color
from .theme import text_color_for_bg

__all__ = [
    "MARGIN_MM",
    "PrintDocument",
    "build_document",
    "default_meta_text",
    "default_page_label",
    "default_title",
    "layout_pages",
    "paint_pages",
    "pdf_file_name",
    "print_notes",
    "save_pdf",
]

# 종이 가장자리에서 글까지(mm). 쪽 번호는 아래 여백의 가운데에 적는다.
MARGIN_MM = 15.0
# '메모 색 그대로'에서 색칠한 곳의 안쪽 여백(mm).
PAD_MM = 4.5
# 이어서 찍을 때 메모와 메모 사이(mm).
GAP_MM = 9.0
# 글자 크기(pt). 본문은 메모에 적힌 크기를 따르고, 서식 없는 메모만 BODY_PT 로 쓴다.
TITLE_PT = 16.0
META_PT = 9.0
FOOTER_PT = 9.0
BODY_PT = 11.0
# 제목으로 올리는 첫 줄의 길이 한도. 이보다 긴 첫 줄은 본문에 그대로 두고 줄여서 제목에 쓴다.
TITLE_MAX = 60

INK = "#1F1F1F"
PAPER = "#FFFFFF"
_FOOTER_INK = "#6B6B6B"
_RULE_INK = "#D6D6D6"
# 메타 줄과 끝낸 할 일의 글자는 글자 색을 바탕 쪽으로 이만큼 섞는다(화면의 흐린 글자와 같은 정도).
_META_MIX = 0.62
_DONE_MIX = 0.46

# 본문 그림이 쪽 높이에서 차지할 수 있는 몫. 제목과 함께 한 쪽에 들어가게 남긴다.
_IMAGE_MAX_HEIGHT = 0.8

# 이 앱이 쓰는 문구. 로캘 파일에 키가 없으면 여기 것을 쓴다.
_TEXTS: dict[str, dict[str, str]] = {
    "ko": {
        "print.meta": "{name} 작성, {time} 고침",
        "print.meta_by": "{name} 작성",
        "print.meta_edited": "{time} 고침",
        "print.date_ymd": "{y}년 {m}월 {d}일",
        "print.page": "{n} / {total}",
        "print.file_name": "메모",
    },
    "en": {
        "print.meta": "By {name}, edited {time}",
        "print.meta_by": "By {name}",
        "print.meta_edited": "Edited {time}",
        "print.date_ymd": "{m}/{d}/{y}",
        "print.page": "{n} / {total}",
        "print.file_name": "Note",
    },
    "ja": {
        "print.meta": "{name} 作成、{time} 編集",
        "print.meta_by": "{name} 作成",
        "print.meta_edited": "{time} 編集",
        "print.date_ymd": "{y}年{m}月{d}日",
        "print.page": "{n} / {total}",
        "print.file_name": "メモ",
    },
}


def _t(key: str, **kwargs) -> str:
    """로캘 파일의 문구. 아직 키가 없으면 이 모듈의 기본 문구."""
    text = i18n.t(key, **kwargs)
    if text != key:
        return text
    table = _TEXTS.get(i18n.current_language()) or _TEXTS["ko"]
    raw = table.get(key, key)
    try:
        return raw.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        return raw


# ── 문구 ─────────────────────────────────────────────────────────────

def _time_text(ms) -> str:
    """고친 때. 올해면 '11월 24일 오전 9:35', 다른 해면 연도를 붙인다."""
    if not isinstance(ms, int) or isinstance(ms, bool) or ms <= 0:
        return ""
    try:
        dt = datetime.fromtimestamp(ms / 1000)
    except (OverflowError, OSError, ValueError):
        return ""
    if dt.year == datetime.now().year:
        day = i18n.t("time.date_md", m=dt.month, d=dt.day)
    else:
        day = _t("print.date_ymd", y=dt.year, m=dt.month, d=dt.day)
    h = dt.hour
    ampm = i18n.t("time.pm") if h >= 12 else i18n.t("time.am")
    h12 = h if 1 <= h <= 12 else (h - 12 if h > 12 else 12)
    clock = i18n.t("time.clock_ampm", ampm=ampm, h=h12, mm=f"{dt.minute:02d}")
    return f"{day} {clock}"


def default_meta_text(note: dict) -> str:
    """제목 아래 줄: '지우 작성, 11월 24일 오전 9:35 고침'. 모르는 것은 뺀다."""
    name = note.get("created_by")
    name = name.strip() if isinstance(name, str) else ""
    when = _time_text(note.get("updated_at"))
    if name and when:
        return _t("print.meta", name=name, time=when)
    if name:
        return _t("print.meta_by", name=name)
    if when:
        return _t("print.meta_edited", time=when)
    return ""


def default_title(note: dict) -> str:
    """메모 제목. 앱의 다른 곳과 같이 본문의 첫 줄이다(없으면 이미지 메모, 빈 메모 문구)."""
    content = note.get("content")
    return note_title_and_lines(content if isinstance(content, str) else "")[0]


def default_page_label(n: int, total: int) -> str:
    return _t("print.page", n=n, total=total)


_BAD_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]+')
_SPACES = re.compile(r"\s+")
_RESERVED = re.compile(r"^(con|prn|aux|nul|com\d|lpt\d)$", re.I)


def pdf_file_name(title: str) -> str:
    """PDF 로 저장할 때 권하는 파일 이름. 파일 이름에 못 쓰는 글자는 빈칸으로 바꾼다."""
    name = _SPACES.sub(" ", _BAD_NAME.sub(" ", title or "")).strip().rstrip(". ")
    name = name[:80].rstrip(". ")
    if not name or _RESERVED.match(name):
        name = _t("print.file_name")
    return f"{name}.pdf"


# ── 문서 ─────────────────────────────────────────────────────────────

@dataclass
class _Span:
    """문서 안의 메모 한 장."""
    title_pos: int
    meta_pos: int                # 메타 줄이 없으면 -1
    body_pos: int                # 본문이 없으면 -1
    end_pos: int
    color: str
    dark: bool                   # 색칠한 바탕이 어두운가(글자와 기호를 밝게)
    numbers: dict[int, int] = field(default_factory=dict)   # 블록 번호 → 목록 번호
    top: float = 0.0             # 배치한 뒤의 위아래(문서 좌표)
    bottom: float = 0.0
    forced: bool = False         # 제목이 본문과 떨어져 다음 쪽으로 넘겼다


@dataclass
class _Page:
    width: float
    height: float
    margin: float
    pad: float


class PrintDocument(SafeTextDocument):
    """찍을 메모를 담은 문서.

    바깥 자원은 SafeTextDocument 처럼 읽지 않는다. 앱 안의 글머리 그림 이름
    (``x-sticky-bullet:``)만 앱이 그린 그림으로 바꿔 준다(줄 앞에 있는 것은 미리
    여백의 기호로 바꾸므로, 줄 가운데 남은 것만 여기로 온다)."""

    def __init__(self) -> None:
        super().__init__()
        self.keep_color = False
        self.one_per_page = True
        self.spans: list[_Span] = []
        self.gap = 0.0
        self.page: _Page | None = None
        self._image_sizes: dict[str, tuple[int, int] | None] = {}

    def loadResource(self, resource_type, name):  # noqa: N802
        if resource_type == QTextDocument.ImageResource:
            url = name.toString() if isinstance(name, QUrl) else str(name)
            bullet = url_to_bullet_name(url)
            if bullet is not None:
                image = bullet_image(bullet)
                if image is not None and not image.isNull():
                    return image
        return super().loadResource(resource_type, name)


def _source_dpi() -> float:
    """문서가 글자 크기(pt)를 픽셀로 바꿀 때 쓰는 해상도(Qt 의 기본 해상도)."""
    if QCoreApplication.testAttribute(Qt.AA_Use96Dpi):
        return 96.0
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return 96.0
    return float(round(screen.logicalDotsPerInchY())) or 96.0


def _mm(value: float) -> float:
    return value * _source_dpi() / 25.4


def _mix(ink: QColor, ground: QColor, amount: float) -> QColor:
    """``ink`` 를 ``amount`` 만큼, 나머지는 ``ground`` 로 섞은 색(투명도 없이)."""
    return QColor.fromRgbF(
        ink.redF() * amount + ground.redF() * (1 - amount),
        ink.greenF() * amount + ground.greenF() * (1 - amount),
        ink.blueF() * amount + ground.blueF() * (1 - amount))


def _default_font() -> QFont:
    """앱 글꼴. 화면의 글(theme.font_stack)처럼 한글과 일본어 글꼴을 뒤에 둔다."""
    font = QFont(QGuiApplication.font())
    primary = font.family() or "Segoe UI"
    families = [primary]
    for name in ("Pretendard Variable", "Pretendard JP Variable", "Malgun Gothic",
                 "Meiryo", "Segoe UI"):
        if name not in families:
            families.append(name)
    font.setFamilies(families)
    font.setPointSizeF(BODY_PT)
    return font


def _in_root(block) -> bool:
    cur = QTextCursor(block)
    return cur.currentTable() is None and block.textList() is None


def _has_image(block) -> bool:
    it = block.begin()
    while not it.atEnd():
        frag = it.fragment()
        it += 1
        if frag.isValid() and frag.charFormat().isImageFormat():
            return True
    return False


def _first_anchor_names(block) -> list[str]:
    it = block.begin()
    if it.atEnd():
        return []
    return list(it.fragment().charFormat().anchorNames())


def _plain_text(block) -> str:
    return " ".join(block.text().replace("￼", "").split())


def _is_blank(block) -> bool:
    return _in_root(block) and not _has_image(block) and not _plain_text(block)


def _lift_title(doc: PrintDocument, body_pos: int, title: str, lift: bool) -> None:
    """본문 앞의 빈 줄과, 제목으로 올린 첫 줄을 본문에서 뺀다.

    첫 줄이 제목과 같은 보통 글 줄일 때만 뺀다. 목록 줄, 표 안의 줄, 그림이 든
    줄은 그대로 둔다(제목과 겹쳐 보여도 내용은 잃지 않는다)."""
    first = doc.findBlock(body_pos)
    if not first.isValid() or first.position() != body_pos:
        return
    drop = []
    block = first
    lifted = not lift
    while block.isValid():
        if not _in_root(block) or _has_image(block):
            break
        text = _plain_text(block)
        if not text:
            drop.append(block)
        elif (not lifted and text == title
              and NUMBER_ANCHOR not in _first_anchor_names(block)):
            drop.append(block)
            lifted = True
        else:
            break
        block = block.next()
    if drop:
        _remove_blocks(doc, drop[0], drop[-1])


def _remove_blocks(doc: QTextDocument, first, last) -> None:
    """``first`` 부터 ``last`` 까지의 줄을 지운다. 앞 줄의 끝(줄 바꿈)부터 지워
    앞 줄의 서식은 그대로 둔다(앞 줄은 늘 있다: 제목이나 메타 줄)."""
    before = first.previous()
    cur = QTextCursor(doc)
    cur.setPosition(before.position() + before.length() - 1)
    cur.setPosition(last.position() + len(last.text()), QTextCursor.KeepAnchor)
    cur.removeSelectedText()


def _drop_trailing_blanks(doc: QTextDocument, body_pos: int) -> None:
    """본문 끝의 빈 줄을 뺀다(종이와 색칠한 곳을 늘리지 않게). 표 바로 뒤의 줄은
    Qt 가 표 다음에 두어야 하는 줄이라 남긴다."""
    last = doc.lastBlock()
    block = last
    first = None
    while block.isValid() and block.position() > body_pos:
        prev = block.previous()
        if not _is_blank(block) or not prev.isValid() or not _in_root(prev):
            break
        first = block
        block = prev
    if first is not None:
        _remove_blocks(doc, first, last)


def _leading_marker(block) -> tuple[int, int]:
    """줄 맨 앞의 목록 그림이면 ``(종류, 지울 글자 수)``. 그림 뒤 빈칸 두 개까지 센다."""
    it = block.begin()
    if it.atEnd():
        return MARKER_NONE, 0
    fmt = it.fragment().charFormat()
    if not fmt.isImageFormat():
        return MARKER_NONE, 0
    code = marker_code_for_url(fmt.toImageFormat().name())
    if code == MARKER_NONE:
        return MARKER_NONE, 0
    text = block.text()
    length = 1
    while length < 3 and len(text) > length and text[length] == " ":
        length += 1
    return code, length


def _leading_number(block) -> int:
    """번호 표시가 붙은 '1.' 로 시작하는 줄이면 지울 글자 수(번호와 뒤 빈칸 하나)."""
    if NUMBER_ANCHOR not in _first_anchor_names(block):
        return 0
    text = block.text()
    found = re.match(r"\d{1,4}\.", text)
    if not found:
        return 0
    return found.end() + (1 if text[found.end():found.end() + 1] == " " else 0)


def _blocks(doc: QTextDocument, start: int, end: int):
    block = doc.findBlock(start)
    while block.isValid() and block.position() <= end:
        yield block
        block = block.next()


def _convert_markers(doc: PrintDocument, start: int, end: int) -> int:
    """저장 형식의 목록(줄 앞 그림과 번호 글자, 단계는 블록의 들여쓰기 값)을 화면과
    같은 블록 속성으로 바꾼다. 지운 글자 수를 돌려준다."""
    positions = [b.position() for b in _blocks(doc, start, end)]
    cur = QTextCursor(doc)
    removed = 0
    for pos in reversed(positions):
        block = doc.findBlock(pos)
        saved_level = block.blockFormat().indent()
        code, length = _leading_marker(block)
        if code == MARKER_NONE:
            length = _leading_number(block)
            if length:
                code = MARKER_NUMBER
        if code == MARKER_NONE and not saved_level:
            continue
        if code != MARKER_NONE:
            cur.setPosition(pos)
            cur.setPosition(pos + length, QTextCursor.KeepAnchor)
            cur.removeSelectedText()
            removed += length
        fmt = QTextBlockFormat()
        # 단계는 따로 적고 Qt 의 들여쓰기(한 단계 40픽셀)는 쓰지 않는다. 목록이 아닌
        # 줄에 남은 값은 옛 버전에서 목록을 풀며 남긴 것이라 화면처럼 버린다.
        fmt.setIndent(0)
        if code != MARKER_NONE:
            fmt.setProperty(MARKER_PROP, code)
            fmt.setProperty(LEVEL_PROP, max(0, min(MAX_LEVEL, saved_level)))
        cur.setPosition(pos)
        cur.mergeBlockFormat(fmt)
    return removed


def _level(block) -> int:
    bf = block.blockFormat()
    if bf.intProperty(MARKER_PROP) not in MARKER_CODES:
        return 0
    return max(0, min(MAX_LEVEL, bf.intProperty(LEVEL_PROP)))


def _number_layout(doc: QTextDocument, start: int, end: int
                   ) -> tuple[dict[int, int], dict[int, int]]:
    """번호 목록의 줄마다 (번호, 그 목록에서 가장 긴 번호의 자릿수).

    화면(RichTextEdit._number_layout)과 같은 규칙이다. 같은 단계에서 이어지는 번호
    줄이 한 목록이고, 더 깊은 줄은 끊지 않으며, 글 줄이나 같은 단계의 다른 기호가
    끼면 다시 1 부터 센다."""
    numbers: dict[int, int] = {}
    runs: dict[int, list[int]] = {}
    digits: dict[int, int] = {}

    def close(level: int) -> None:
        run = runs.pop(level, None)
        if run:
            width = len(str(numbers[run[-1]]))
            for number in run:
                digits[number] = width

    for block in _blocks(doc, start, end):
        code = block.blockFormat().intProperty(MARKER_PROP)
        if code not in MARKER_CODES:
            for level in list(runs):
                close(level)
            continue
        level = _level(block)
        for deeper in [k for k in runs if k > level]:
            close(deeper)
        if code == MARKER_NUMBER:
            run = runs.setdefault(level, [])
            numbers[block.blockNumber()] = len(run) + 1
            run.append(block.blockNumber())
        else:
            close(level)
    for level in list(runs):
        close(level)
    return numbers, digits


def _lead_format(block) -> QTextCharFormat:
    """그 줄의 첫 글자 서식(그림은 건너뛴다)."""
    it = block.begin()
    while not it.atEnd():
        frag = it.fragment()
        it += 1
        if frag.isValid() and not frag.charFormat().isImageFormat():
            return frag.charFormat()
    return block.charFormat()


def _font_of(doc: QTextDocument, fmt: QTextCharFormat) -> QFont:
    font = fmt.font().resolve(doc.defaultFont())
    font.setUnderline(False)
    font.setStrikeOut(False)
    return font


def _em_of(doc: QTextDocument, fmt: QTextCharFormat) -> float:
    """글자 크기(문서 단위의 픽셀)."""
    font = _font_of(doc, fmt)
    if font.pointSizeF() > 0:
        return font.pointSizeF() * _source_dpi() / 72.0
    return float(max(1, font.pixelSize()))


def _number_label_width(doc: QTextDocument, fmt: QTextCharFormat, digits: int) -> float:
    return _text_line("0" * digits + ".", _font_of(doc, fmt))[1]


def _restyle_lists(doc: PrintDocument, start: int, end: int) -> dict[int, int]:
    """목록 줄의 왼쪽 여백(기호 칸 + 단계마다 글자 반 칸)과 서식 사이의 위 간격을
    화면과 같은 규칙으로 맞춘다. 번호 목록의 번호를 돌려준다."""
    numbers, digits = _number_layout(doc, start, end)
    base_em = _em_of(doc, QTextCharFormat())
    ops: list[tuple[int, QTextBlockFormat]] = []
    prev = None
    for block in _blocks(doc, start, end):
        bf = block.blockFormat()
        code = bf.intProperty(MARKER_PROP)
        level = _level(block)
        kind, empty = RichTextEdit._block_kind(block, code)
        merge = QTextBlockFormat()
        dirty = False
        if code in MARKER_CODES:
            fmt = _lead_format(block)
            em = _em_of(doc, fmt)
            column = marker_column(em)
            width = digits.get(block.blockNumber(), 1)
            if code == MARKER_NUMBER and width > 1:
                label = _number_label_width(doc, fmt, width)
                column = max(column, float(round(label + em * 0.58)))
            merge.setLeftMargin(column + level * LEVEL_STEP * em)
            dirty = True
        cur_kind = (kind, empty, level)
        gap = float(round(RichTextEdit._gap_before(prev, cur_kind) * base_em))
        if gap > 0 and bf.topMargin() == 0:
            merge.setTopMargin(gap)
            dirty = True
        if dirty:
            ops.append((block.position(), merge))
        prev = cur_kind
    cur = QTextCursor(doc)
    for pos, merge in ops:
        cur.setPosition(pos)
        cur.mergeBlockFormat(merge)
    return numbers


def _dress_body(doc: PrintDocument, start: int, end: int, paper: QColor,
                ink: QColor, light: bool) -> None:
    """본문 글자를 종이에 맞춘다.

    * 종이에서 사실상 보이지 않는 글자(어두운 메모의 흰 글자를 흰 종이에 찍는 경우
      등)는 화면의 ensure_readable 과 같은 규칙으로 읽히는 색으로 옮긴다.
    * 어두운 메모를 색 그대로 찍으면 색을 정하지 않은 글자를 희게 한다.
    * 링크는 종이에 맞는 링크 색으로.
    * 끝낸 할 일은 화면처럼 흐리게 하고 줄을 긋는다."""
    dark_ink, white = QColor(INK), QColor("#FFFFFF")
    link = QColor(LINK_COLOR_DARK if light else LINK_COLOR_LIGHT)
    fixes: list[tuple[int, int, QTextCharFormat]] = []
    for block in _blocks(doc, start, end):
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            it += 1
            if not frag.isValid() or frag.length() <= 0:
                continue
            fmt = frag.charFormat()
            if fmt.isImageFormat():
                continue
            change = QTextCharFormat()
            if fmt.isAnchor() and fmt.anchorHref():
                change.setForeground(link)
                fixes.append((frag.position(), frag.length(), change))
                continue
            brush = fmt.background()
            marked = brush.style() != Qt.NoBrush and brush.color().alpha() > 0
            explicit = (fmt.hasProperty(QTextFormat.ForegroundBrush)
                        and fmt.foreground().style() != Qt.NoBrush)
            ground = brush.color() if marked else paper
            fg = fmt.foreground().color() if explicit else ink
            if contrast_ratio(fg, ground) >= MIN_READABLE_CONTRAST:
                if explicit or ink == dark_ink:
                    continue
                better = ink
            elif explicit:
                better = font_colors.rescue(fg, ground)
            else:
                better = (dark_ink if contrast_ratio(dark_ink, ground)
                          >= contrast_ratio(white, ground) else white)
            change.setForeground(better)
            fixes.append((frag.position(), frag.length(), change))
    done = QTextCharFormat()
    done.setForeground(_mix(ink, paper, _DONE_MIX))
    done.setFontStrikeOut(True)
    for block in _blocks(doc, start, end):
        if block.blockFormat().intProperty(MARKER_PROP) == MARKER_CHECK1 and block.length() > 1:
            fixes.append((block.position(), block.length() - 1, done))
    cur = QTextCursor(doc)
    for pos, length, change in fixes:
        cur.setPosition(pos)
        cur.setPosition(pos + length, QTextCursor.KeepAnchor)
        cur.mergeCharFormat(change)


def _text_option() -> QTextOption:
    opt = QTextOption()
    # 배치는 화면 단위로 하고 그릴 때 키우므로, 화면에 맞춰 반올림한 글자 폭을 쓰지
    # 않는다(키웠을 때 글자 사이가 고르지 않다).
    opt.setUseDesignMetrics(True)
    opt.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
    return opt


def _shorten(title: str) -> str:
    cut = title[:TITLE_MAX].rstrip()
    space = cut.rfind(" ")
    if space > TITLE_MAX // 2:
        cut = cut[:space].rstrip()
    return cut + "…"


def build_document(notes: list[dict], *, keep_color: bool = False,
                   one_per_page: bool = True,
                   title_text: Callable[[dict], str] | None = None,
                   meta_text: Callable[[dict], str] | None = None,
                   base_font: QFont | None = None) -> PrintDocument:
    """메모들을 차례로 담은 문서를 만든다.

    ``notes`` 는 메모 dict(content, color, created_by, updated_at …)의 목록이다.
    ``keep_color`` 가 참이면 메모마다 그 색으로 칠하고(그리는 때에 칠한다), 아니면
    흰 종이에 찍는다. ``one_per_page`` 가 참이면 메모마다 새 쪽에서 시작한다.
    ``title_text`` 와 ``meta_text`` 는 메모 하나를 받아 제목과 그 아래 줄을 돌려준다
    (없으면 default_title, default_meta_text). ``base_font`` 는 제목, 메타 줄, 서식
    없는 메모의 글꼴이다(없으면 앱 글꼴의 11pt).

    문서는 A4 에 맞춰 배치해 둔다. 다른 종이에 찍을 때는 layout_pages(paint_pages 가
    부른다)가 다시 맞춘다."""
    doc = PrintDocument()
    doc.setUndoRedoEnabled(False)
    doc.keep_color = bool(keep_color)
    doc.one_per_page = bool(one_per_page)
    font = QFont(base_font) if base_font is not None else _default_font()
    doc.setDefaultFont(font)
    doc.setDefaultTextOption(_text_option())
    pad = _mm(PAD_MM) if doc.keep_color else 0.0
    doc.gap = _mm(GAP_MM) + 2 * pad
    title_of = title_text or default_title
    meta_of = meta_text or default_meta_text

    cur = QTextCursor(doc)
    first = True
    for note in notes:
        if not isinstance(note, dict):
            continue
        content = note.get("content")
        content = content if isinstance(content, str) else ""
        markup = html_guard.is_markup(content)
        color = safe_color(note.get("color"), config.DEFAULT_COLOR)
        dark = doc.keep_color and text_color_for_bg(color) == "#FFFFFF"
        paper = QColor(color if doc.keep_color else PAPER)
        ink = QColor("#FFFFFF" if dark else INK)
        title = " ".join(str(title_of(note) or "").split())
        lift = len(title) <= TITLE_MAX
        shown = title if lift else _shorten(title)
        meta = " ".join(str(meta_of(note) or "").split())

        # 제목
        tbf = QTextBlockFormat()
        tbf.setBottomMargin(_mm(1.2) if meta else _mm(4.5))
        tcf = QTextCharFormat()
        tcf.setFontPointSize(TITLE_PT)
        tcf.setFontWeight(QFont.Bold)
        tcf.setForeground(ink)
        if first:
            cur.setBlockFormat(tbf)
            cur.setBlockCharFormat(tcf)
        else:
            cur.insertBlock(tbf, tcf)
        title_pos = cur.block().position()
        cur.insertText(shown, tcf)

        # 만든 사람과 고친 때
        meta_pos = -1
        if meta:
            mbf = QTextBlockFormat()
            mbf.setBottomMargin(_mm(4.5))
            mcf = QTextCharFormat()
            mcf.setFontPointSize(META_PT)
            mcf.setFontWeight(QFont.Normal)
            mcf.setForeground(_mix(ink, paper, _META_MIX))
            cur.insertBlock(mbf, mcf)
            meta_pos = cur.block().position()
            cur.insertText(meta, mcf)

        # 본문
        cur.insertBlock(QTextBlockFormat(), QTextCharFormat())
        body_pos = cur.position()
        if markup:
            cur.insertHtml(html_guard.clean(content))
        elif content:
            cur.insertText(content.replace("\r\n", "\n").replace("\r", "\n"))
        _lift_title(doc, body_pos, title, lift)
        if cur.position() >= body_pos:
            _drop_trailing_blanks(doc, body_pos)
        end_pos = cur.position()
        span = _Span(title_pos, meta_pos, -1, end_pos, color, dark)
        if end_pos >= body_pos:
            span.body_pos = body_pos
            _convert_markers(doc, body_pos, end_pos)
            span.end_pos = cur.position()
            span.numbers = _restyle_lists(doc, body_pos, span.end_pos)
            _dress_body(doc, body_pos, span.end_pos, paper, ink, dark)
        doc.spans.append(span)
        first = False

    layout_pages(doc, QPageSize(QPageSize.A4).size(QPageSize.Point))
    return doc


def _reset_titles(doc: PrintDocument) -> None:
    """메모 사이의 나눔(새 쪽 또는 간격)을 처음 값으로 돌린다."""
    cur = QTextCursor(doc)
    for i, span in enumerate(doc.spans):
        if i == 0:
            continue
        bf = QTextBlockFormat()
        if doc.one_per_page:
            bf.setPageBreakPolicy(QTextFormat.PageBreak_AlwaysBefore)
            bf.setTopMargin(0.0)
        else:
            bf.setPageBreakPolicy(QTextFormat.PageBreak_Auto)
            bf.setTopMargin(doc.gap)
        cur.setPosition(span.title_pos)
        cur.mergeBlockFormat(bf)
        span.forced = False


# ── 쪽 나누기 ─────────────────────────────────────────────────────────

def _image_size(doc: PrintDocument, url: str) -> tuple[int, int] | None:
    """본문 그림의 원래 크기. 이 앱이 넣는 그림 형식만 읽는다."""
    if url in doc._image_sizes:
        return doc._image_sizes[url]
    size = None
    if url[:5].lower() == "data:" and data_url_is_raster(url):
        raw = data_url_payload(url)
        if not raw.isEmpty():
            buf = QBuffer(raw)
            buf.open(QIODevice.ReadOnly)
            reader = QImageReader(buf)
            reader.setDecideFormatFromContent(True)
            found = reader.size() if reader.canRead() else None
            buf.close()
            if found is None or found.width() <= 0 or found.height() <= 0:
                image = QImage()
                image.loadFromData(raw)
                found = None if image.isNull() else image.size()
            if found is not None:
                size = (found.width(), found.height())
    doc._image_sizes[url] = size
    return size


def _fit_images(doc: PrintDocument, avail_w: float, avail_h: float) -> None:
    """본문 그림을 원래 크기로, 쪽보다 크면 쪽에 맞게 줄여 놓는다(화면이 메모 폭에
    맞추는 것과 같은 규칙). 목록 기호 그림은 건드리지 않는다."""
    changes: list[tuple[int, int, QTextImageFormat]] = []
    block = doc.begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            it += 1
            if not frag.isValid() or not frag.charFormat().isImageFormat():
                continue
            img = frag.charFormat().toImageFormat()
            name = img.name()
            if not name or is_marker_url(name):
                continue
            size = _image_size(doc, name)
            if size is None:
                continue
            w0, h0 = size
            scale = min(1.0, avail_w / w0, avail_h / h0)
            w, h = max(1.0, round(w0 * scale)), max(1.0, round(h0 * scale))
            if abs(img.width() - w) < 0.5 and abs(img.height() - h) < 0.5:
                continue
            out = QTextImageFormat(img)
            out.setWidth(w)
            out.setHeight(h)
            changes.append((frag.position(), frag.length(), out))
        block = block.next()
    cur = QTextCursor(doc)
    for pos, length, fmt in changes:
        cur.setPosition(pos)
        cur.setPosition(pos + length, QTextCursor.KeepAnchor)
        cur.setCharFormat(fmt)


def _rect(doc: QTextDocument, block) -> QRectF:
    return doc.documentLayout().blockBoundingRect(block)


def _first_line_top(doc: QTextDocument, block) -> float:
    """줄의 첫 줄이 놓인 높이. 첫 줄이 다음 쪽으로 밀리면 블록의 자리는 앞 쪽에
    남으므로 블록이 아니라 줄을 본다."""
    rect = _rect(doc, block)
    layout = block.layout()
    if layout is not None and layout.lineCount() > 0:
        return rect.top() + layout.lineAt(0).y()
    return rect.top()


def _first_line_bottom(doc: QTextDocument, block) -> float:
    rect = _rect(doc, block)
    layout = block.layout()
    if layout is not None and layout.lineCount() > 0:
        line = layout.lineAt(0)
        return rect.top() + line.y() + line.height()
    return rect.bottom()


def _block_bottom(doc: QTextDocument, block) -> float:
    """줄의 마지막 줄 아래. blockBoundingRect 는 블록이 놓이기 시작한 자리에서
    높이만 재므로, 줄이 다음 쪽으로 밀렸으면 실제보다 위를 가리킨다."""
    rect = _rect(doc, block)
    layout = block.layout()
    if layout is not None and layout.lineCount() > 0:
        return rect.top() + layout.boundingRect().bottom()
    return rect.bottom()


def _keep_titles_with_body(doc: PrintDocument, page_h: float) -> None:
    """이어서 찍을 때, 쪽 끝에 제목만 남고 본문이 다음 쪽으로 가면 제목도 넘긴다."""
    cur = QTextCursor(doc)
    for i, span in enumerate(doc.spans):
        if i == 0:
            continue
        follow = span.body_pos if span.body_pos >= 0 else span.meta_pos
        if follow < 0:
            continue
        top = _first_line_top(doc, doc.findBlock(span.title_pos))
        bottom = _first_line_bottom(doc, doc.findBlock(follow))
        if int(top // page_h) == int((bottom - 0.5) // page_h):
            continue
        bf = QTextBlockFormat()
        bf.setPageBreakPolicy(QTextFormat.PageBreak_AlwaysBefore)
        bf.setTopMargin(0.0)
        cur.setPosition(span.title_pos)
        cur.mergeBlockFormat(bf)
        span.forced = True


def _measure_spans(doc: PrintDocument) -> None:
    """메모마다 문서에서 차지하는 위아래를 잰다(색칠과 구분선에 쓴다)."""
    layout = doc.documentLayout()
    frames = doc.rootFrame().childFrames()
    for span in doc.spans:
        top = _first_line_top(doc, doc.findBlock(span.title_pos))
        bottom = top
        for block in _blocks(doc, span.title_pos, span.end_pos):
            if not _rect(doc, block).isNull():
                bottom = max(bottom, _block_bottom(doc, block))
        for frame in frames:
            if span.title_pos <= frame.firstPosition() <= span.end_pos:
                bottom = max(bottom, layout.frameBoundingRect(frame).bottom())
        span.top, span.bottom = top, bottom


def layout_pages(doc: PrintDocument, page: QSizeF, *,
                 margin_mm: float = MARGIN_MM) -> int:
    """``page``(pt) 크기의 종이에 맞춰 문서를 쪽으로 나누고 쪽 수를 돌려준다.

    여백, 그림 크기, 제목을 본문과 같은 쪽에 두는 것을 이 종이에 맞춘다. 찍을
    메모가 없으면 0."""
    dpi = _source_dpi()
    width = page.width() * dpi / 72.0
    height = page.height() * dpi / 72.0
    margin = margin_mm * dpi / 25.4
    pad = _mm(PAD_MM) if doc.keep_color else 0.0
    doc.page = _Page(width, height, margin, pad)
    doc.setPageSize(QSizeF(width, height))
    frame = doc.rootFrame().frameFormat()
    frame.setBorder(0)
    frame.setPadding(0)
    frame.setLeftMargin(margin + pad)
    frame.setRightMargin(margin + pad)
    frame.setTopMargin(margin + pad)
    frame.setBottomMargin(margin + pad)
    doc.rootFrame().setFrameFormat(frame)
    if not doc.spans:
        return 0
    body_w = max(1.0, width - 2 * (margin + pad))
    body_h = max(1.0, height - 2 * (margin + pad))
    _fit_images(doc, body_w, body_h * _IMAGE_MAX_HEIGHT)
    _reset_titles(doc)
    if not doc.one_per_page:
        _keep_titles_with_body(doc, height)
    total = max(1, doc.pageCount())
    _measure_spans(doc)
    return total


# ── 그리기 ───────────────────────────────────────────────────────────

def _text_line(text: str, font: QFont) -> tuple[QTextLayout, float, float]:
    """한 줄 글자의 배치와 (폭, 기준선까지의 높이). 문서와 같은 길로 그린다."""
    layout = QTextLayout(text, font)
    opt = _text_option()
    opt.setWrapMode(QTextOption.NoWrap)
    layout.setTextOption(opt)
    layout.beginLayout()
    line = layout.createLine()
    if line.isValid():
        line.setPosition(QPointF(0, 0))
    # 줄 폭을 정하지 않은 마지막 줄은 endLayout 이 마저 배치한다. 잰 값은 그 뒤에 읽는다.
    layout.endLayout()
    if layout.lineCount() == 0:
        return layout, 0.0, 0.0
    line = layout.lineAt(0)
    return layout, line.naturalTextWidth(), line.ascent()


def _paint_markers(painter: QPainter, doc: PrintDocument, band: QRectF) -> None:
    """목록 기호, 체크 상자, 번호를 줄 앞 여백에 그린다(화면의 paintEvent 와 같은 자리)."""
    for span in doc.spans:
        if span.body_pos < 0 or span.bottom < band.top() or span.top > band.bottom():
            continue
        ink = marker_ink(span.dark)
        for block in _blocks(doc, span.body_pos, span.end_pos):
            code = block.blockFormat().intProperty(MARKER_PROP)
            if code not in MARKER_CODES:
                continue
            layout = block.layout()
            if layout is None or layout.lineCount() == 0:
                continue
            rect = _rect(doc, block)
            line = layout.lineAt(0)
            top = rect.top() + line.y()
            cy = top + line.height() / 2.0
            if cy < band.top() or cy > band.bottom():
                continue
            x = rect.left() + line.cursorToX(0)[0]
            fmt = _lead_format(block)
            em = _em_of(doc, fmt)
            column = marker_column(em)
            if code == MARKER_NUMBER:
                number = span.numbers.get(block.blockNumber(), 1)
                text, width, ascent = _text_line(f"{number}.", _font_of(doc, fmt))
                painter.setPen(ink)
                text.draw(painter, QPointF(x - em * 0.36 - width,
                                           top + line.ascent() - ascent))
            elif code in CHECK_CODES:
                draw_checkbox(painter, code == MARKER_CHECK1,
                              x - column + column * 0.42, cy, em, span.dark)
            else:
                draw_marker_shape(painter, marker_shape(code, _level(block)),
                                  x - column + column * 0.42, cy, em, ink)


def _paint_page(painter: QPainter, doc: PrintDocument, index: int, label: str) -> None:
    """``index``(0 부터) 쪽을 종이 좌표(문서 단위)로 그린다."""
    pg = doc.page
    top = index * pg.height
    band = QRectF(0, top + pg.margin, pg.width, pg.height - 2 * pg.margin)
    painter.save()
    painter.translate(0, -top)
    if doc.keep_color:
        for span in doc.spans:
            a = max(span.top - pg.pad, band.top())
            b = min(span.bottom + pg.pad, band.bottom())
            if b > a:
                painter.fillRect(QRectF(pg.margin, a, pg.width - 2 * pg.margin, b - a),
                                 QColor(span.color))
    elif not doc.one_per_page:
        # 이어서 찍은 메모 사이의 가는 선. 쪽 맨 위로 넘어간 제목 위에는 긋지 않는다.
        painter.setPen(QPen(QColor(_RULE_INK), 1.0))
        for span in doc.spans[1:]:
            y = span.top - doc.gap / 2.0
            if not span.forced and band.top() + 1 < y < band.bottom() and y < span.top:
                painter.drawLine(QPointF(pg.margin, y), QPointF(pg.width - pg.margin, y))
    painter.setClipRect(band)
    ctx = QAbstractTextDocumentLayout.PaintContext()
    ctx.clip = band
    palette = ctx.palette
    palette.setColor(QPalette.Text, QColor(INK))
    ctx.palette = palette
    doc.documentLayout().draw(painter, ctx)
    _paint_markers(painter, doc, band)
    painter.setClipping(False)
    painter.restore()
    if label:
        font = QFont(doc.defaultFont())
        font.setPointSizeF(FOOTER_PT)
        font.setBold(False)
        text, width, ascent = _text_line(label, font)
        painter.setPen(QColor(_FOOTER_INK))
        baseline = pg.height - pg.margin / 2.0 + ascent / 3.0
        text.draw(painter, QPointF((pg.width - width) / 2.0, baseline - ascent))


def _page_range(device, total: int) -> tuple[int, int]:
    """프린터 창에서 고른 쪽 범위(없으면 모두)."""
    first, last = 1, total
    print_range = getattr(device, "printRange", None)
    if callable(print_range) and print_range() != type(device).PrintRange.PageRange:
        return first, last
    from_page = getattr(device, "fromPage", None)
    to_page = getattr(device, "toPage", None)
    if callable(from_page) and callable(to_page) and from_page() > 0:
        first = max(1, from_page())
        last = min(total, to_page() if to_page() > 0 else total)
    return first, last


def paint_pages(doc: PrintDocument, device, *, margin_mm: float = MARGIN_MM,
                page_label: Callable[[int, int], str] | None = None) -> int:
    """문서를 ``device``(QPrinter 나 QPdfWriter)의 종이에 맞춰 나누고 쪽마다 그린다.
    그린 쪽 수를 돌려준다(장치를 열지 못하거나 찍을 것이 없으면 0).

    종이 크기와 방향은 장치의 쪽 설정(pageLayout)을 따르고, 여백은 종이 가장자리에서
    ``margin_mm`` 이다. 장치의 여백(인쇄할 수 없는 곳)은 좌표만 옮겨 맞춘다. 쪽 번호는
    ``page_label(n, total)`` 로 적는다(없으면 '1 / 3'). 프린터 창에서 쪽 범위를
    골랐으면 그 쪽만 그린다."""
    pl = device.pageLayout()
    full = pl.fullRect(QPageLayout.Point)
    paint = pl.paintRect(QPageLayout.Point)
    total = layout_pages(doc, full.size(), margin_mm=margin_mm)
    if total <= 0:
        return 0
    first, last = _page_range(device, total)
    if first > last:
        return 0
    label_of = page_label or default_page_label
    dpi = _source_dpi()
    dpi_x, dpi_y = float(device.logicalDpiX()), float(device.logicalDpiY())
    painter = QPainter()
    if not painter.begin(device):
        return 0
    try:
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        for n in range(first, last + 1):
            if n > first:
                device.newPage()
            painter.save()
            # 장치 좌표의 원점은 인쇄할 수 있는 곳의 왼쪽 위다. 종이 왼쪽 위로 옮긴다.
            painter.translate(-paint.left() * dpi_x / 72.0, -paint.top() * dpi_y / 72.0)
            painter.scale(dpi_x / dpi, dpi_y / dpi)
            _paint_page(painter, doc, n - 1, label_of(n, total))
            painter.restore()
    finally:
        painter.end()
    return last - first + 1


# ── 저장과 인쇄 ───────────────────────────────────────────────────────

def save_pdf(path: str, notes: list[dict], *, keep_color: bool = False,
             one_per_page: bool = True, page_size: QPageSize | None = None,
             margin_mm: float = MARGIN_MM, doc_title: str = "",
             title_text: Callable[[dict], str] | None = None,
             meta_text: Callable[[dict], str] | None = None,
             page_label: Callable[[int, int], str] | None = None,
             base_font: QFont | None = None) -> int:
    """메모들을 PDF 파일로 저장하고 쪽 수를 돌려준다.

    종이는 ``page_size``(없으면 A4), 세로 방향이다. ``doc_title`` 은 PDF 문서
    정보의 제목이다. 찍을 메모가 없으면 파일을 만들지 않고 0 을 돌려준다. 파일을
    쓸 수 없으면(폴더가 없거나 다른 프로그램이 열어 둔 파일) OSError."""
    notes = [n for n in notes if isinstance(n, dict)]
    if not notes:
        return 0
    doc = build_document(notes, keep_color=keep_color, one_per_page=one_per_page,
                         title_text=title_text, meta_text=meta_text,
                         base_font=base_font)
    writer = QPdfWriter(path)
    writer.setPageLayout(QPageLayout(page_size or QPageSize(QPageSize.A4),
                                     QPageLayout.Portrait, QMarginsF(0, 0, 0, 0)))
    if doc_title:
        writer.setTitle(doc_title)
    pages = paint_pages(doc, writer, margin_mm=margin_mm, page_label=page_label)
    # 다 그리면 파일을 닫는다. 바로 열어 볼 수 있게 장치를 여기서 지운다.
    del writer
    if pages <= 0:
        raise OSError(f"PDF 를 쓸 수 없다: {path}")
    return pages


def print_notes(parent, notes: list[dict], *, keep_color: bool = False,
                one_per_page: bool = True, page_size: QPageSize | None = None,
                margin_mm: float = MARGIN_MM, doc_name: str = "",
                title_text: Callable[[dict], str] | None = None,
                meta_text: Callable[[dict], str] | None = None,
                page_label: Callable[[int, int], str] | None = None,
                base_font: QFont | None = None) -> bool:
    """인쇄 미리 보기를 띄우고, 거기서 '인쇄' 를 누르면 Windows 의 프린터 창으로 찍는다.
    찍었으면 True, 취소했거나 찍을 것이 없으면 False.

    종이는 프린터의 기본 종이를 따르고(프린터 창에서 바꿀 수 있다), ``page_size`` 를
    주면 그것으로 시작한다. Windows 의 프린터 창에는 앱의 선택 칸을 넣을 수 없으므로
    ``keep_color`` 와 ``one_per_page`` 는 부르는 쪽이 미리 정해 넘긴다."""
    from PySide6.QtPrintSupport import QPrinter
    from PySide6.QtWidgets import QApplication

    from .print_preview import PrintPreviewDialog

    notes = [n for n in notes if isinstance(n, dict)]
    if not notes:
        return False
    doc = build_document(notes, keep_color=keep_color, one_per_page=one_per_page,
                         title_text=title_text, meta_text=meta_text,
                         base_font=base_font)
    printer = QPrinter(QPrinter.HighResolution)
    if page_size is not None:
        printer.setPageSize(page_size)
    printer.setFullPage(True)
    printer.setDocName(doc_name or (title_text or default_title)(notes[0]))
    total = layout_pages(doc, printer.pageLayout().fullRect(QPageLayout.Point).size(),
                         margin_mm=margin_mm)

    def paint(device) -> int:
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            return paint_pages(doc, device, margin_mm=margin_mm, page_label=page_label)
        finally:
            QApplication.restoreOverrideCursor()

    dialog = PrintPreviewDialog(printer, paint, total=total, parent=parent)
    try:
        dialog.exec()
        return dialog.printed
    finally:
        dialog.deleteLater()
