"""파일 종류별로 첨부: 종류 판정, 방법, 인코딩, 잘라 넣기, 표, 마크다운, 바로 가기,
폴더, 문서, 메일, 실행 파일. 화면 없이 offscreen 플랫폼으로 돈다."""
from __future__ import annotations

import codecs
import os
import re
import struct
import sys
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QImage, QTextCursor, QTextDocument, QTextFrameFormat, QTextLength  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app import file_insert as fi  # noqa: E402
from app import file_links as fl  # noqa: E402
from app import html_guard  # noqa: E402

CHECK0 = "data:image/png;base64,AAAA"
CHECK1 = "data:image/png;base64,BBBB"


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _href(path, is_dir=False):
    return fl.path_to_href(os.path.normpath(str(path)), is_dir=is_dir)


def _link(path, is_dir=False, label=None):
    """편집기가 저장하는 링크 꼴."""
    href = _href(path, is_dir)
    text = label or fl.display_name(href)
    return f'<a href="{href}">{text}</a>'


def _doc_with(html: str) -> QTextDocument:
    doc = QTextDocument()
    QTextCursor(doc).insertHtml(html)
    return doc


# ── 종류와 방법 ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,kind", [
    ("사진.PNG", fi.IMAGE), ("a.jpg", fi.IMAGE), ("a.jpeg", fi.IMAGE),
    ("a.gif", fi.IMAGE), ("a.webp", fi.IMAGE), ("a.bmp", fi.IMAGE),
    ("메모.txt", fi.TEXT), ("app.log", fi.TEXT),
    ("README.md", fi.MARKDOWN),
    ("자료.csv", fi.TABLE), ("자료.tsv", fi.TABLE),
    ("문서.pdf", fi.PDF),
    ("사이트.url", fi.URL), ("바로 가기.lnk", fi.SHORTCUT),
    ("보고서.docx", fi.DOCUMENT), ("보고서.hwpx", fi.DOCUMENT),
    ("메일.eml", fi.MAIL),
    ("setup.exe", fi.PROGRAM), ("run.bat", fi.PROGRAM), ("run.cmd", fi.PROGRAM),
    ("a.ps1", fi.PROGRAM), ("a.vbs", fi.PROGRAM), ("a.js", fi.PROGRAM),
    ("a.msi", fi.PROGRAM), ("main.py", fi.PROGRAM), ("evil.cmd.", fi.PROGRAM),
    ("evil.bat ", fi.PROGRAM),
    ("표.xlsx", fi.OTHER), ("그림.svg", fi.OTHER), ("이름없음", fi.OTHER),
    ("옛 문서.hwp", fi.OTHER), ("압축.zip", fi.OTHER),
])
def test_kind_by_extension(tmp_path, name, kind):
    assert fi.kind_of_path(str(tmp_path / name)) == kind


def test_folder_kind_wins_over_extension(tmp_path):
    folder = tmp_path / "자료.txt"
    folder.mkdir()
    assert fi.kind_of_path(str(folder)) == fi.FOLDER
    assert fi.kind_of_path("") == fi.OTHER


def test_modes_and_defaults():
    assert fi.modes_for(fi.IMAGE) == ["image", "link"]
    assert fi.modes_for(fi.TEXT) == ["text", "link", "both"]
    assert fi.modes_for(fi.MARKDOWN) == ["rich", "text", "link"]
    assert fi.modes_for(fi.TABLE) == ["table", "text", "link"]
    assert fi.modes_for(fi.PDF) == ["link"]
    assert fi.modes_for(fi.URL) == ["web", "link"]
    assert fi.modes_for(fi.FOLDER) == ["link", "list"]
    assert fi.modes_for(fi.DOCUMENT) == ["link", "text"]
    assert fi.modes_for(fi.MAIL) == ["summary", "link"]
    assert fi.modes_for(fi.PROGRAM) == ["link"]
    assert fi.modes_for(fi.OTHER) == ["link"]
    assert fi.modes_for("모르는 종류") == ["link"]
    for kind in fi.KINDS:
        assert fi.default_mode(kind) == fi.modes_for(kind)[0]
    # 설정 표로 기본을 바꾼다. 그 종류에 없는 방법은 무시한다.
    overrides = {fi.TEXT: "link", fi.FOLDER: "list", fi.PDF: "text"}
    assert fi.default_mode(fi.TEXT, overrides) == "link"
    assert fi.default_mode(fi.FOLDER, overrides) == "list"
    assert fi.default_mode(fi.PDF, overrides) == "link"
    assert fi.default_mode(fi.MAIL, overrides) == "summary"


def test_overrides_apply_to_attach(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hello", encoding="utf-8")
    att = fi.attach(str(f), overrides={fi.TEXT: "link"})
    assert att.mode == "link" and att.html.count("<a href") == 1


# ── 링크 꼴 ─────────────────────────────────────────────────────────────

def test_link_mode_matches_editor_anchor(tmp_path):
    f = tmp_path / "보고서 #3 (최종).txt"
    f.write_text("x", encoding="utf-8")
    att = fi.attach(str(f), "link")
    assert att.kind == fi.TEXT and att.mode == "link" and att.error is None
    assert _link(f) in att.html
    assert att.plain == str(f)
    # href 는 HTML 속성에 그대로 든다. 빈칸·한글·# 가 날것으로 남지 않는다.
    href = re.search(r'href="([^"]+)"', att.html).group(1)
    assert href.isascii() and " " not in href and "#" not in href
    assert fl.href_to_path(href) == str(f)


def test_links_behave_like_dropped_files_in_the_editor(qapp, tmp_path):
    from app.ui.rich_text import LINK_ICON_PROP, RichTextEdit
    f = tmp_path / "견적서.pdf"
    f.write_bytes(b"%PDF-1.4")
    folder = tmp_path / "자료"
    folder.mkdir()
    ours = RichTextEdit()
    for att in fi.attach_many([str(f), str(folder)]):
        ours.textCursor().insertHtml(att.html)
    ours.sync_link_icons()
    theirs = RichTextEdit()
    theirs.insert_file_links([str(f), str(folder)])

    def links(edit):
        out = []
        block = edit.document().begin()
        while block.isValid():
            for start, end, href in edit._link_runs(block):
                out.append((href, edit.link_text(start, end)))
            block = block.next()
        return out

    assert links(ours) == links(theirs)
    kinds = []
    block = ours.document().begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            fmt = it.fragment().charFormat()
            if fmt.hasProperty(LINK_ICON_PROP):
                kinds.append(fmt.property(LINK_ICON_PROP))
            it += 1
        block = block.next()
    assert kinds == [fl.KIND_PDF, fl.KIND_FOLDER]


def test_folder_link_ends_with_slash(tmp_path):
    folder = tmp_path / "공유 자료"
    folder.mkdir()
    att = fi.attach(str(folder))
    assert att.kind == fi.FOLDER and att.mode == "link"
    assert _link(folder, is_dir=True) in att.html
    assert re.search(r'href="[^"]+/"', att.html)


# ── 글(txt, log) ────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,encoding", [
    (codecs.BOM_UTF8 + "가나다\nabc".encode("utf-8"), "utf-8-sig"),
    (codecs.BOM_UTF16_LE + "가나다\r\nabc".encode("utf-16-le"), "utf-16-le"),
    (codecs.BOM_UTF16_BE + "가나다\rabc".encode("utf-16-be"), "utf-16-be"),
    ("가나다\nabc".encode("utf-8"), "utf-8"),
    ("가나다\nabc".encode("cp949"), "cp949"),
])
def test_text_encodings(tmp_path, raw, encoding):
    assert fi.decode_text(raw)[1] == encoding
    f = tmp_path / "메모.txt"
    f.write_bytes(raw)
    att = fi.attach(str(f))
    assert att.kind == fi.TEXT and att.mode == "text" and att.error is None
    assert att.plain == "가나다\nabc"
    assert "가나다" in att.html and not att.truncated


def test_latin1_fallback_and_control_characters(tmp_path):
    assert fi.decode_text(b"caf\xe9 au lait") == ("café au lait", "latin-1")
    f = tmp_path / "a.log"
    f.write_bytes(b"ok\x00\x07 line\x1b[0m\ttab\n")
    att = fi.attach(str(f))
    assert att.plain == "ok line[0m\ttab"


def test_text_html_keeps_spaces_and_escapes(tmp_path, qapp):
    f = tmp_path / "a.txt"
    f.write_text("  들여 쓴 줄\n\n<b>굵게?</b> & \"따옴표\"\t탭\n", encoding="utf-8")
    att = fi.attach(str(f))
    assert "<b>" not in att.html and "&lt;b&gt;" in att.html
    doc = _doc_with(att.html)
    assert doc.toPlainText() == "  들여 쓴 줄\n\n<b>굵게?</b> & \"따옴표\"\t탭"
    assert doc.blockCount() == 3
    assert html_guard.clean(att.html) == att.html


def test_text_modes_both_and_link(tmp_path, qapp):
    f = tmp_path / "a.txt"
    f.write_text("첫 줄\n둘째 줄", encoding="utf-8")
    both = fi.attach(str(f), "both")
    assert both.mode == "both"
    assert both.html.index("첫 줄") < both.html.index("<a href")
    assert _link(f) in both.html
    assert both.plain == f"첫 줄\n둘째 줄\n{f}"
    assert _doc_with(both.html).toPlainText() == "첫 줄\n둘째 줄\na.txt"
    link = fi.attach(str(f), "link")
    assert "첫 줄" not in link.html and _link(f) in link.html


def test_big_text_keeps_head_cut_at_a_line(tmp_path):
    f = tmp_path / "big.log"
    lines = [f"줄 {i:06d} 내용" for i in range(40000)]
    f.write_text("\n".join(lines), encoding="utf-8")
    assert f.stat().st_size > fi.TEXT_LIMIT
    att = fi.attach(str(f))
    assert att.truncated and att.mode == "text"
    got = att.plain.split("\n")
    assert got[-2:] == [fi.ELLIPSIS, str(f)]
    body = got[:-2]
    assert body == lines[:len(body)]
    assert len("\n".join(body).encode("utf-8")) <= fi.TEXT_LIMIT
    assert att.html.endswith(f"<p style=\"{fi._P_STYLE}\">{fi.ELLIPSIS} {_link(f)}</p>")


_CUT_CASES = {
    "utf8": lambda: "가".encode("utf-8") * 100000,          # 3바이트 글자 중간에서 잘린다
    "cp949": lambda: b"a" + "한".encode("cp949") * 150000,  # 2바이트 글자 중간에서 잘린다
    "utf16": lambda: codecs.BOM_UTF16_LE + ("가나\n".encode("utf-16-le") * 40000),
}


@pytest.mark.parametrize("case", sorted(_CUT_CASES))
def test_cut_never_leaves_broken_characters(tmp_path, case):
    f = tmp_path / "a.txt"
    f.write_bytes(_CUT_CASES[case]())
    att = fi.attach(str(f))
    assert att.truncated
    assert "\ufffd" not in att.plain
    first = att.plain.split("\n", 1)[0]
    assert first and set(first) <= set("가한a나")


def test_empty_and_missing_text_fall_back_to_link(tmp_path):
    empty = tmp_path / "빈.txt"
    empty.write_bytes(b"\n\n  \n")
    att = fi.attach(str(empty))
    assert att.mode == "link" and att.error == fi.E_EMPTY and _link(empty) in att.html
    gone = tmp_path / "없음.txt"
    att = fi.attach(str(gone))
    assert att.mode == "link" and att.error == fi.E_MISSING
    assert att.kind == fi.TEXT and att.modes == fi.modes_for(fi.TEXT)


def test_unknown_mode_uses_default_and_says_so(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("글", encoding="utf-8")
    att = fi.attach(str(f), "table")
    assert att.mode == "text" and att.error == fi.E_UNSUPPORTED and "글" in att.plain


def test_reads_are_bounded(tmp_path, monkeypatch, qapp):
    sizes: list[int] = []
    real_open = open

    class Tracked:
        def __init__(self, f):
            self.f = f

        def read(self, n=-1):
            sizes.append(n)
            return self.f.read(n)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.f.close()

    monkeypatch.setattr(fi, "open", lambda p, m="r", *a, **k: Tracked(real_open(p, m, *a, **k)),
                        raising=False)
    big = b"line of text\n" * 400000          # 5MB 넘게
    for name in ("a.txt", "a.md", "a.csv", "a.eml", "a.url", "a.lnk"):
        f = tmp_path / name
        f.write_bytes(big)
        fi.attach(str(f))
    assert sizes and all(0 < n <= fi.READ_LIMIT + 1 for n in sizes)


# ── 표(csv, tsv) ────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,text,expected", [
    ("a.csv", "이름,값,비고\n사과,\"1,000\",맛있다\n배,2,\n",
     [["이름", "값", "비고"], ["사과", "1,000", "맛있다"], ["배", "2", ""]]),
    ("a.csv", "이름;값\n사과;1,5\n배;2,5\n", [["이름", "값"], ["사과", "1,5"], ["배", "2,5"]]),
    ("a.csv", "이름\t값\n사과\t1, 2\n", [["이름", "값"], ["사과", "1, 2"]]),
    ("a.tsv", "a\tb,c\nd\te,f\n", [["a", "b,c"], ["d", "e,f"]]),
    ("a.csv", "제목,\"여러\n줄\"\n", [["제목", "여러\n줄"]]),
])
def test_csv_delimiter_sniffing(tmp_path, name, text, expected):
    f = tmp_path / name
    f.write_text(text, encoding="utf-8")
    att = fi.attach(str(f))
    assert att.kind == fi.TABLE and att.mode == "table" and att.error is None
    assert att.rows == expected and not att.truncated


def test_cp949_csv_and_ragged_rows(tmp_path):
    f = tmp_path / "엑셀.csv"
    f.write_bytes("품목,수량\n볼펜,3,추가\n지우개\n".encode("cp949"))
    att = fi.attach(str(f))
    assert att.rows == [["품목", "수량", ""], ["볼펜", "3", "추가"], ["지우개", "", ""]]
    assert "볼펜" in att.html and att.plain.startswith("품목\t수량")


def test_csv_capped_at_50_rows_and_12_columns(tmp_path):
    f = tmp_path / "큰 표.csv"
    rows = [",".join(f"r{r}c{c}" for c in range(15)) for r in range(60)]
    f.write_text("\n".join(rows), encoding="utf-8")
    att = fi.attach(str(f))
    assert att.truncated
    assert len(att.rows) == fi.TABLE_MAX_ROWS == 50
    assert all(len(r) == fi.TABLE_MAX_COLS == 12 for r in att.rows)
    assert att.rows[49][11] == "r49c11"
    assert "r50c0" not in att.html and "r0c12" not in att.html
    assert att.html.endswith(f"{fi.ELLIPSIS} {_link(f)}</p>")
    # 줄만 넘쳐도, 칸만 넘쳐도 자른다.
    g = tmp_path / "넓은 표.csv"
    g.write_text(",".join(str(i) for i in range(13)), encoding="utf-8")
    att = fi.attach(str(g))
    assert att.truncated and att.rows == [[str(i) for i in range(12)]]


def test_csv_table_looks_like_inserted_table(tmp_path, qapp):
    f = tmp_path / "a.csv"
    f.write_text("a,b\n<1>,&2\n", encoding="utf-8")
    att = fi.attach(str(f))
    doc = _doc_with(att.html)
    tables = doc.rootFrame().childFrames()
    assert len(tables) == 1
    table = tables[0]
    fmt = table.format()
    assert (table.rows(), table.columns()) == (2, 2)
    assert fmt.border() == 1 and fmt.borderStyle() == QTextFrameFormat.BorderStyle_Solid
    assert fmt.borderBrush().color().name() == "#9a9a9a"
    assert fmt.borderCollapse() and fmt.cellPadding() == 4 and fmt.cellSpacing() == 0
    assert fmt.width().type() == QTextLength.PercentageLength and fmt.width().rawValue() == 100
    cell = table.cellAt(1, 0)
    cur = cell.firstCursorPosition()
    cur.setPosition(cell.lastCursorPosition().position(), QTextCursor.KeepAnchor)
    assert cur.selectedText() == "<1>"
    assert fi.table_html(att.rows) in att.html


def test_csv_text_and_link_modes(tmp_path):
    f = tmp_path / "a.csv"
    f.write_text("a,b\n1,2\n", encoding="utf-8")
    text = fi.attach(str(f), "text")
    assert text.mode == "text" and text.plain == "a,b\n1,2" and "<table" not in text.html
    assert text.rows is None
    link = fi.attach(str(f), "link")
    assert link.html.count("<a href") == 1 and "<table" not in link.html


# ── 마크다운 ────────────────────────────────────────────────────────────

MARKDOWN = """# 회의 기록

안녕하세요. **굵게** *기울임* ~~취소~~ [사이트](https://example.com/a?b=1&c=2)
[나쁜 링크](javascript:alert(1)) [옆 파일](다른%20문서.md)

- [ ] 할 일 하나
- [x] 끝낸 일
- 그냥 항목
  - 딸린 항목

1. 첫째
2. 둘째

| 이름 | 값 |
|------|----|
| 사과 | 1 |

![바깥 그림](http://evil.example/x.png)
![로컬 그림](C:/Windows/Web/x.png)
<img src="http://evil.example/raw.png">
"""


class _RecordingDocument(QTextDocument):
    def __init__(self):
        super().__init__()
        self.asked: list[str] = []

    def loadResource(self, rtype, name):  # noqa: N802
        self.asked.append(name.toString())
        image = QImage(1, 1, QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        return image


def test_markdown_rich(tmp_path, qapp):
    f = tmp_path / "회의.md"
    f.write_text(MARKDOWN, encoding="utf-8")
    att = fi.attach(str(f), checkbox_urls=(CHECK0, CHECK1), font_pt=11)
    assert att.kind == fi.MARKDOWN and att.mode == "rich" and att.error is None
    h = att.html
    # 제목: 굵고 큰 글자.
    assert 'font-size:16pt; font-weight:700;">회의 기록</span>' in h
    assert "<b>굵게</b>" in h and "<i>기울임</i>" in h and "<s>취소</s>" in h
    # 웹 링크는 남고, javascript: 는 글자만 남는다. 상대 경로는 파일 옆이다.
    assert '<a href="https://example.com/a?b=1&amp;c=2">사이트</a>' in h
    assert "javascript" not in h and "나쁜 링크" in h
    assert f'<a href="{_href(tmp_path / "다른 문서.md")}">옆 파일</a>' in h
    # 목록은 저장 형식: 할 일 그림, 네모 글머리, 번호 글자, 단계는 들여쓰기 값.
    assert f'<img src="{CHECK0}" />  할 일 하나' in h
    assert f'<img src="{CHECK1}" />  끝낸 일' in h
    assert '<img src="x-sticky-bullet:square" />  그냥 항목' in h
    assert re.search(r'-qt-block-indent:1;"><img src="x-sticky-bullet:square" />  딸린 항목', h)
    assert '<a name="memil-num"></a>1. 첫째' in h and '<a name="memil-num"></a>2. 둘째' in h
    # 표는 메모의 표 모양이고 첫 줄이 굵다.
    assert fi._TABLE_OPEN in h and "<td><b>이름</b></td>" in h and "<td>사과</td>" in h
    # 그림은 하나도 들어가지 않는다. 대체 글만 남고, 날것의 HTML 은 글자다.
    assert "evil.example/x.png" not in h and "C:/Windows" not in h
    assert "바깥 그림" in h and "로컬 그림" in h
    assert "&lt;img src=&quot;http://evil.example/raw.png&quot;&gt;" in h
    srcs = re.findall(r'<img src="([^"]*)"', h)
    assert set(srcs) == {CHECK0, CHECK1, "x-sticky-bullet:square"}
    assert html_guard.clean(h) == h
    assert "회의 기록" in att.plain and not att.truncated


def test_markdown_never_loads_outside_resources(tmp_path, qapp):
    f = tmp_path / "a.md"
    f.write_text(MARKDOWN, encoding="utf-8")
    att = fi.attach(str(f), checkbox_urls=(CHECK0, CHECK1))
    doc = _RecordingDocument()
    QTextCursor(doc).insertHtml(att.html)
    doc.setTextWidth(400)
    doc.documentLayout().documentSize()          # 배치하면서 그림을 찾는다
    assert doc.asked
    assert all(n.startswith(("data:image/", "x-sticky-bullet:")) for n in doc.asked)


def test_markdown_blocks_in_a_document(tmp_path, qapp):
    f = tmp_path / "a.md"
    f.write_text("# 제목\n\n- [ ] 일\n- 항목\n  - 딸림\n\n1. 번호\n\n문단\n", encoding="utf-8")
    att = fi.attach(str(f), checkbox_urls=(CHECK0, CHECK1))
    doc = _doc_with(att.html)
    blocks = []
    block = doc.begin()
    while block.isValid():
        first = block.begin()
        lead = ""
        if not first.atEnd():
            fmt = first.fragment().charFormat()
            if fmt.isImageFormat():
                lead = fmt.toImageFormat().name()
            elif "memil-num" in fmt.anchorNames():
                lead = "num"
        blocks.append((block.text().replace("\ufffc", "").strip(), lead,
                       block.blockFormat().indent()))
        block = block.next()
    # 제목 바로 뒤에는 빈 줄을 두지 않고, 묶음과 묶음 사이에 한 줄을 둔다.
    assert blocks == [
        ("제목", "", 0),
        ("일", CHECK0, 0), ("항목", "x-sticky-bullet:square", 0),
        ("딸림", "x-sticky-bullet:square", 1), ("", "", 0),
        ("1. 번호", "num", 0), ("", "", 0), ("문단", "", 0),
    ]


def test_markdown_without_checkbox_urls_uses_glyphs(tmp_path, qapp):
    f = tmp_path / "a.md"
    f.write_text("- [ ] 아직\n- [x] 끝\n", encoding="utf-8")
    att = fi.attach(str(f))
    assert "\u2610 아직" in att.html and "\u2611 끝" in att.html
    assert "<img" not in att.html
    # 그림 주소 꼴이 아닌 값은 받지 않는다.
    att = fi.attach(str(f), checkbox_urls=('x" onerror="y', CHECK1))
    assert "onerror" not in att.html and "\u2610 아직" in att.html


def test_markdown_text_and_link_modes(tmp_path, qapp):
    f = tmp_path / "a.md"
    f.write_text("# 제목\n\n- 항목\n", encoding="utf-8")
    text = fi.attach(str(f), "text")
    assert text.mode == "text" and text.plain == "# 제목\n\n- 항목"
    assert "x-sticky-bullet" not in text.html and "# 제목" in text.html
    link = fi.attach(str(f), "link")
    assert link.html.count("<a href") == 1


def test_markdown_big_table_is_capped(tmp_path, qapp):
    f = tmp_path / "a.md"
    header = "| " + " | ".join(f"h{i}" for i in range(14)) + " |"
    sep = "|" + "---|" * 14
    body = ["| " + " | ".join(f"r{r}c{c}" for c in range(14)) + " |" for r in range(60)]
    f.write_text("\n".join([header, sep, *body]), encoding="utf-8")
    att = fi.attach(str(f))
    assert att.truncated
    doc = _doc_with(att.html)
    table = doc.rootFrame().childFrames()[0]
    assert (table.rows(), table.columns()) == (50, 12)
    assert att.html.endswith(f"{fi.ELLIPSIS} {_link(f)}</p>")


# ── 인터넷 바로 가기(.url) ──────────────────────────────────────────────

def _url_file(path, url_line, section="InternetShortcut"):
    path.write_text(
        "[{000214A0-0000-0000-C000-000000000046}]\r\nProp3=19,11\r\n"
        f"[{section}]\r\nIDList=\r\n{url_line}\r\nIconIndex=0\r\n", encoding="utf-8")


def test_url_shortcut_becomes_web_link(tmp_path):
    f = tmp_path / "회사 홈페이지.url"
    _url_file(f, "URL=https://example.com/a?b=1&c=2")
    att = fi.attach(str(f))
    assert att.kind == fi.URL and att.mode == "web" and att.error is None
    assert att.html == (f'<p style="{fi._P_STYLE}"><a href="https://example.com/a?b=1&amp;c=2">'
                        "회사 홈페이지</a></p>")
    assert att.plain == "https://example.com/a?b=1&c=2"
    # 링크 방법은 .url 파일 자체에 건다.
    link = fi.attach(str(f), "link")
    assert _link(f) in link.html


@pytest.mark.parametrize("line,section", [
    ("URL=javascript:alert(1)", "InternetShortcut"),
    ("URL=file:///C:/Windows/System32/calc.exe", "InternetShortcut"),
    ("URL=ms-settings:privacy", "InternetShortcut"),
    ("URL=https://", "InternetShortcut"),
    ("URL=https://example.com", "Other"),
])
def test_url_shortcut_refuses_non_web(tmp_path, line, section):
    f = tmp_path / "a.url"
    _url_file(f, line, section)
    assert fi.read_url_file(str(f)) is None
    att = fi.attach(str(f))
    assert att.mode == "link" and att.error == fi.E_UNSUPPORTED
    assert "javascript" not in att.html and "calc" not in att.html
    assert _link(f) in att.html


# ── 바로 가기(.lnk) ─────────────────────────────────────────────────────

_LNK_CLSID = bytes.fromhex("0114020000000000c000000000000046")
_MY_COMPUTER = uuid.UUID("20D04FE0-3AEA-1069-A2D8-08002B30309D").bytes_le


def _linkinfo(path: str) -> bytes:
    volume = struct.pack("<IIII", 0x11, 3, 0x1234, 0x10) + b"\0"
    off_vol = 0x24
    off_base = off_vol + len(volume)
    ansi_base = b"\0"                 # 유니코드 쪽을 읽는지 보려고 비워 둔다
    off_suffix = off_base + len(ansi_base)
    off_base_u = off_suffix + 1
    uni = path.encode("utf-16-le") + b"\0\0"
    off_suffix_u = off_base_u + len(uni)
    tail = volume + ansi_base + b"\0" + uni + b"\0\0"
    total = 0x24 + len(tail)
    return struct.pack("<9I", total, 0x24, 1, off_vol, off_base, 0, off_suffix,
                       off_base_u, off_suffix_u) + tail


def _file_item(name: str, is_dir: bool) -> bytes:
    short = b"SHORT~1\0"
    data = (bytes([0x31 if is_dir else 0x32, 0]) + struct.pack("<I", 0) + b"\0" * 4
            + struct.pack("<H", 0x10 if is_dir else 0x20) + short)
    long_name = name.encode("utf-16-le") + b"\0\0"
    ext = (struct.pack("<HHI", 0, 9, 0xBEEF0004) + b"\0" * 8 + struct.pack("<HH", 0x2E, 0)
           + b"\0" * 16 + struct.pack("<H", 0) + b"\0" * 8 + long_name + struct.pack("<H", 0x14))
    ext = struct.pack("<H", len(ext)) + ext[2:]
    item = data + ext
    return struct.pack("<H", len(item) + 2) + item


def _idlist(path: str) -> bytes:
    drive, rest = os.path.splitdrive(path)
    root = bytes([0x1F, 0x50]) + _MY_COMPUTER
    items = struct.pack("<H", len(root) + 2) + root
    vol = bytes([0x2F]) + (drive + "\\").encode("ascii") + b"\0" * 19
    items += struct.pack("<H", len(vol) + 2) + vol
    parts = [p for p in rest.split("\\") if p]
    for i, part in enumerate(parts):
        items += _file_item(part, i < len(parts) - 1)
    return items + b"\0\0"


def _lnk(*, local: str | None = None, relative: str | None = None,
         idlist: bytes | None = None, env: str | None = None) -> bytes:
    flags = 0x80                       # 유니코드 글자
    body = b""
    if idlist is not None:
        flags |= 0x1
        body += struct.pack("<H", len(idlist)) + idlist
    if local is not None:
        flags |= 0x2
        body += _linkinfo(local)
    if relative is not None:
        flags |= 0x8
        body += struct.pack("<H", len(relative)) + relative.encode("utf-16-le")
    extra = b""
    if env is not None:
        flags |= 0x200
        ansi = env.encode("ascii").ljust(260, b"\0")
        uni = env.encode("utf-16-le").ljust(520, b"\0")
        extra = struct.pack("<II", 0x314, 0xA0000001) + ansi + uni
    header = struct.pack("<I", 0x4C) + _LNK_CLSID + struct.pack("<I", flags) + b"\0" * 52
    assert len(header) == 76
    return header + body + extra + struct.pack("<I", 0)


@pytest.fixture
def target(tmp_path):
    folder = tmp_path / "대상 폴더"
    folder.mkdir()
    f = folder / "메모 파일.txt"
    f.write_text("바로 가기로 넣은 글", encoding="utf-8")
    return f


@pytest.mark.parametrize("how", ["linkinfo", "idlist", "relative", "env"])
def test_shortcut_follows_target_rules(tmp_path, target, how, monkeypatch):
    lnk = tmp_path / "메모 바로 가기.lnk"
    if how == "linkinfo":
        data = _lnk(local=str(target))
    elif how == "idlist":
        data = _lnk(idlist=_idlist(str(target)))
    elif how == "relative":
        data = _lnk(relative=".\\대상 폴더\\메모 파일.txt")
    else:
        monkeypatch.setenv("FI_TEST_DIR", str(tmp_path))
        data = _lnk(env="%FI_TEST_DIR%\\" + "x.txt")
        (tmp_path / "x.txt").write_text("바로 가기로 넣은 글", encoding="utf-8")
        target = tmp_path / "x.txt"
    lnk.write_bytes(data)
    assert fi.kind_of_path(str(lnk)) == fi.SHORTCUT
    assert fi.resolve_shortcut(str(lnk)) == str(target)
    att = fi.attach(str(lnk))
    assert att.kind == fi.TEXT and att.mode == "text" and att.error is None
    assert att.path == str(lnk) and att.target == str(target) and att.source == str(target)
    assert att.plain == "바로 가기로 넣은 글"
    # 링크 방법은 대상에 건다.
    link = fi.attach(str(lnk), "link")
    assert _link(target) in link.html and ".lnk" not in link.html


def test_shortcut_prefers_existing_candidate(tmp_path, target):
    lnk = tmp_path / "a.lnk"
    lnk.write_bytes(_lnk(local=str(tmp_path / "옮겨진 파일.txt"),
                         relative=".\\대상 폴더\\메모 파일.txt"))
    assert fi.resolve_shortcut(str(lnk)) == str(target)


def test_shortcut_with_missing_target_links_to_itself(tmp_path):
    lnk = tmp_path / "깨진 바로 가기.lnk"
    gone = str(tmp_path / "지워진 파일.txt")
    lnk.write_bytes(_lnk(local=gone))
    att = fi.attach(str(lnk))
    assert att.kind == fi.SHORTCUT and att.mode == "link"
    assert att.error == fi.E_MISSING and att.target == gone
    assert _link(lnk) in att.html


@pytest.mark.parametrize("data", [b"", b"not a shortcut", b"\x4c\0\0\0" + b"\0" * 100,
                                  _lnk(local="x")[:90]],
                         ids=["empty", "text", "no-clsid", "cut"])
def test_broken_shortcut_links_to_itself(tmp_path, data):
    lnk = tmp_path / "이상한.lnk"
    lnk.write_bytes(data)
    att = fi.attach(str(lnk))
    assert att.kind == fi.SHORTCUT and att.mode == "link"
    assert att.error in (fi.E_UNREADABLE, fi.E_MISSING)
    assert _link(lnk) in att.html


def test_shortcut_to_folder_and_program(tmp_path, monkeypatch):
    folder = tmp_path / "프로젝트"
    folder.mkdir()
    (folder / "a.txt").write_text("a", encoding="utf-8")
    lnk = tmp_path / "폴더.lnk"
    lnk.write_bytes(_lnk(local=str(folder)))
    att = fi.attach(str(lnk))
    assert att.kind == fi.FOLDER and att.mode == "link"
    assert _link(folder, is_dir=True) in att.html
    listed = fi.attach(str(lnk), "list")
    assert listed.mode == "list" and "a.txt" in listed.html

    exe = tmp_path / "tool.exe"
    exe.write_bytes(b"MZ secret")
    lnk2 = tmp_path / "도구.lnk"
    lnk2.write_bytes(_lnk(local=str(exe)))
    real = fi._read_head

    def guard(path, limit):
        assert not str(path).lower().endswith(".exe"), "실행 파일을 읽었다"
        return real(path, limit)

    monkeypatch.setattr(fi, "_read_head", guard)
    att = fi.attach(str(lnk2), "text")
    assert att.kind == fi.PROGRAM and att.mode == "link" and att.target == str(exe)
    # 프로그램은 바로 가기 파일에 건다(인자와 시작 폴더가 남는다).
    assert _link(lnk2) in att.html and "secret" not in att.html


def test_shortcut_to_shortcut_is_not_followed(tmp_path, target):
    inner = tmp_path / "안쪽.lnk"
    inner.write_bytes(_lnk(local=str(target)))
    outer = tmp_path / "바깥.lnk"
    outer.write_bytes(_lnk(local=str(inner)))
    att = fi.attach(str(outer))
    assert att.kind == fi.SHORTCUT and att.mode == "link"
    assert _link(outer) in att.html


# ── 실행 파일과 스크립트 ────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["setup.exe", "run.bat", "run.cmd", "a.ps1", "a.vbs",
                                  "a.js", "a.msi", "main.py", "a.hta"])
def test_programs_are_never_read(tmp_path, monkeypatch, name):
    f = tmp_path / name
    f.write_bytes(b"echo secret")

    def boom(*a, **k):
        raise AssertionError("실행 파일을 읽었다")

    monkeypatch.setattr(fi, "_read_head", boom)
    monkeypatch.setattr(fi, "open", boom, raising=False)
    monkeypatch.setattr(fi.zipfile, "ZipFile", boom)
    assert fi.kind_of_path(str(f)) == fi.PROGRAM
    for mode in (None, "link", "text", "both", "rich", "table", "summary", "list"):
        att = fi.attach(str(f), mode)
        assert att.kind == fi.PROGRAM and att.mode == "link"
        assert att.html == f'<p style="{fi._P_STYLE}">{_link(f)}</p>'
        assert "secret" not in att.html


def test_pdf_is_link_only_and_not_read(tmp_path, monkeypatch):
    f = tmp_path / "설명서.pdf"
    f.write_bytes(b"%PDF-1.7 secret")
    monkeypatch.setattr(fi, "_read_head", lambda *a: (_ for _ in ()).throw(AssertionError))
    att = fi.attach(str(f))
    assert att.kind == fi.PDF and att.mode == "link" and att.modes == ["link"]
    assert _link(f) in att.html


# ── 그림 ────────────────────────────────────────────────────────────────

def test_image_is_classified_for_the_editor(tmp_path):
    f = tmp_path / "사진.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\n")
    att = fi.attach(str(f))
    assert att.kind == fi.IMAGE and att.mode == "image"
    assert att.html == "" and att.source == str(f) and att.error is None
    link = fi.attach(str(f), "link")
    assert _link(f) in link.html
    gone = fi.attach(str(tmp_path / "없는 사진.jpg"))
    assert gone.mode == "link" and gone.error == fi.E_MISSING


# ── 폴더 ────────────────────────────────────────────────────────────────

def test_folder_list_order_and_hidden(tmp_path):
    root = tmp_path / "자료실"
    root.mkdir()
    for d in ("b", "A", "c10", "c2"):
        (root / d).mkdir()
    for n in ("z.txt", "a10.txt", "a2.txt", "B.txt"):
        (root / n).write_text("x", encoding="utf-8")
    hidden = root / "desktop.ini"
    hidden.write_text("x", encoding="utf-8")
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.kernel32.SetFileAttributesW(str(hidden), 0x2 | 0x4)
    att = fi.attach(str(root), "list")
    assert att.kind == fi.FOLDER and att.mode == "list" and not att.truncated
    names = re.findall(r'<a href="[^"]+">([^<]+)</a>', att.html)
    expected = ["A", "b", "c2", "c10", "a2.txt", "a10.txt", "B.txt", "z.txt"]
    if sys.platform == "win32":
        assert names == ["자료실", *expected]
    hrefs = re.findall(r'<a href="([^"]+)">', att.html)
    assert hrefs[0] == _href(root, is_dir=True)
    assert hrefs[1] == _href(root / "A", is_dir=True)
    assert hrefs[5] == _href(root / "a2.txt")
    assert att.plain.split("\n")[0] == str(root)


def test_folder_list_capped_at_50(tmp_path):
    root = tmp_path / "많은 파일"
    root.mkdir()
    for i in range(60):
        (root / f"f{i:02d}.txt").write_text("x", encoding="utf-8")
    att = fi.attach(str(root), "list")
    names = re.findall(r'<a href="[^"]+">([^<]+)</a>', att.html)
    assert att.truncated and len(names) == 1 + fi.FOLDER_ITEMS
    assert names[1] == "f00.txt" and names[-1] == "f49.txt"
    assert att.html.endswith(f'<p style="{fi._P_STYLE}">{fi.ELLIPSIS}</p>')


# ── 문서(docx, hwpx) ────────────────────────────────────────────────────

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _docx(path, body_xml: str) -> None:
    xml = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           f'<w:document xmlns:w="{W}"><w:body>{body_xml}</w:body></w:document>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", xml)


def test_docx_text(tmp_path):
    f = tmp_path / "보고서.docx"
    _docx(f, (
        '<w:p><w:pPr><w:tabs><w:tab w:val="left" w:pos="720"/></w:tabs></w:pPr>'
        "<w:r><w:t>첫 문단</w:t></w:r></w:p>"
        '<w:p><w:r><w:t xml:space="preserve">이름 </w:t></w:r><w:r><w:tab/><w:t>값</w:t></w:r></w:p>'
        "<w:p><w:r><w:t>줄</w:t><w:br/><w:t>바꿈</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>남은 글</w:t></w:r><w:r><w:delText>지운 글</w:delText></w:r></w:p>"
        "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>칸1 &lt;a&gt;</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:p><w:r><w:t>칸2</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"))
    att = fi.attach(str(f))
    assert att.kind == fi.DOCUMENT and att.mode == "link" and _link(f) in att.html
    att = fi.attach(str(f), "text")
    assert att.mode == "text" and att.error is None and not att.truncated
    assert att.plain == "첫 문단\n이름 \t값\n줄\n바꿈\n남은 글\n칸1 <a>\n칸2"
    assert "칸1 &lt;a&gt;" in att.html


def test_hwpx_text_sections_in_number_order(tmp_path):
    f = tmp_path / "한글 문서.hwpx"
    ns = ('xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
          'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph"')

    def section(*paras):
        body = "".join(f"<hp:p><hp:run><hp:t>{p}</hp:t></hp:run></hp:p>" for p in paras)
        return f'<?xml version="1.0" encoding="UTF-8"?><hs:sec {ns}>{body}</hs:sec>'

    with zipfile.ZipFile(f, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/hwp+zip")
        zf.writestr("Contents/section0.xml",
                    section("한글 문서", '앞<hp:tab width="4000"/>뒤',
                            "위<hp:lineBreak/>아래"))
        zf.writestr("Contents/section10.xml", section("열한째 구역"))
        zf.writestr("Contents/section2.xml", section("셋째 구역"))
        zf.writestr("Contents/section1.xml", section("둘째 구역"))
        zf.writestr("Contents/header.xml", "<x>머리</x>")
    att = fi.attach(str(f), "text")
    assert att.mode == "text" and att.error is None
    assert att.plain == "한글 문서\n앞\t뒤\n위\n아래\n둘째 구역\n셋째 구역\n열한째 구역"


def test_document_zip_bomb_is_bounded(tmp_path):
    f = tmp_path / "폭탄.docx"
    filler = " " * (20 * 1024 * 1024)            # 풀면 20MB, 압축하면 작다
    _docx(f, f"<w:p><w:r><w:t>처음</w:t></w:r></w:p>{filler}"
             "<w:p><w:r><w:t>끝</w:t></w:r></w:p>")
    assert f.stat().st_size < 1024 * 1024
    att = fi.attach(str(f), "text")
    assert att.mode == "text" and att.truncated
    assert att.plain.split("\n")[0] == "처음" and "끝" not in att.plain
    assert att.html.endswith(f"{fi.ELLIPSIS} {_link(f)}</p>")


def test_document_with_too_many_members(tmp_path, monkeypatch):
    f = tmp_path / "a.docx"
    _docx(f, "<w:p><w:r><w:t>글</w:t></w:r></w:p>")
    monkeypatch.setattr(fi, "ZIP_MAX_MEMBERS", 1)
    att = fi.attach(str(f), "text")
    assert att.mode == "link" and att.error == fi.E_TOO_BIG


@pytest.mark.parametrize("payload", [b"not a zip", None])
def test_broken_documents_fall_back(tmp_path, payload):
    f = tmp_path / "깨짐.docx"
    if payload is None:
        with zipfile.ZipFile(f, "w") as zf:
            zf.writestr("word/document.xml", "<w:document><w:body><w:p>")
    else:
        f.write_bytes(payload)
    att = fi.attach(str(f), "text")
    assert att.mode == "link" and att.error in (fi.E_UNREADABLE, fi.E_EMPTY)
    assert _link(f) in att.html


# ── 메일(eml) ───────────────────────────────────────────────────────────

def _mail(path, *, body: str | None = "안녕하세요.\n\n회의는 3시입니다.",
          html: str | None = None) -> None:
    msg = EmailMessage()
    msg["Subject"] = "회의 일정 안내"
    msg["From"] = "홍길동 <hong@example.com>"
    msg["To"] = "team@example.com"
    msg["Date"] = "Tue, 24 Nov 2026 09:30:00 +0900"
    if body is not None:
        msg.set_content(body)
        if html is not None:
            msg.add_alternative(html, subtype="html")
    else:
        msg.set_content(html, subtype="html")
    path.write_bytes(bytes(msg))


def test_mail_summary(tmp_path):
    f = tmp_path / "메일.eml"
    _mail(f, html="<p>HTML 쪽</p>")
    att = fi.attach(str(f))
    assert att.kind == fi.MAIL and att.mode == "summary" and att.error is None
    when = datetime(2026, 11, 24, 9, 30, tzinfo=timezone(timedelta(hours=9))).astimezone()
    stamp = when.strftime("%Y.%m.%d %H:%M")
    assert att.plain == (f"회의 일정 안내\n홍길동 <hong@example.com>\n{stamp}\n\n"
                         f"안녕하세요.\n\n회의는 3시입니다.\n{f}")
    assert "<b>회의 일정 안내</b>" in att.html and "&lt;hong@example.com&gt;" in att.html
    assert "HTML 쪽" not in att.html
    assert att.html.endswith(f'<p style="{fi._P_STYLE}">{_link(f)}</p>')
    assert not att.truncated
    link = fi.attach(str(f), "link")
    assert "회의 일정" not in link.html and _link(f) in link.html


def test_mail_body_is_cut_near_2000_chars(tmp_path):
    f = tmp_path / "긴 메일.eml"
    _mail(f, body="첫 줄\n" + "긴 본문 내용 " * 600)
    att = fi.attach(str(f))
    assert att.truncated
    body = att.plain.split("\n\n", 1)[1].rsplit("\n", 1)[0]
    assert body.endswith(fi.ELLIPSIS) and len(body) <= fi.MAIL_BODY_CHARS + 1
    assert len(body) > fi.MAIL_BODY_CHARS - 200


def test_html_only_mail_becomes_text(tmp_path):
    f = tmp_path / "광고.eml"
    _mail(f, body=None, html=(
        "<html><head><title>제목칸</title><style>p{color:red}</style>"
        "<script>alert(1)</script></head><body><p>첫 줄</p>"
        "<p>둘째 <img src='http://evil.example/t.png'> 줄<br>셋째 &amp; 줄</p></body></html>"))
    att = fi.attach(str(f))
    assert att.mode == "summary"
    body = att.plain.split("\n\n", 1)[1]
    assert body.startswith("첫 줄\n\n둘째 줄\n셋째 & 줄")
    assert "alert" not in att.plain and "color" not in att.plain and "제목칸" not in att.plain
    assert "evil" not in att.html and "<img" not in att.html


def test_mail_without_subject_uses_file_name(tmp_path):
    f = tmp_path / "제목 없음.eml"
    f.write_bytes(b"From: a@example.com\r\n\r\nbody only\r\n")
    att = fi.attach(str(f))
    assert att.mode == "summary" and att.plain.startswith("제목 없음\na@example.com\n")


# ── 여러 파일 ───────────────────────────────────────────────────────────

def test_attach_many_keeps_order(tmp_path):
    a = tmp_path / "a.txt"
    a.write_text("글", encoding="utf-8")
    b = tmp_path / "b.png"
    b.write_bytes(b"\x89PNG\r\n\x1a\n")
    c = tmp_path / "c.zip"
    c.write_bytes(b"PK")
    d = tmp_path / "d"
    d.mkdir()
    got = fi.attach_many([str(c), str(a), str(d), str(b)])
    assert [g.kind for g in got] == [fi.OTHER, fi.TEXT, fi.FOLDER, fi.IMAGE]
    assert [g.mode for g in got] == ["link", "text", "link", "image"]
    assert [g.path for g in got] == [str(c), str(a), str(d), str(b)]


def test_every_output_passes_the_html_guard(tmp_path, qapp):
    f = tmp_path / "a.md"
    f.write_text(MARKDOWN, encoding="utf-8")
    g = tmp_path / "a.csv"
    g.write_text("<img src=x>,b\n", encoding="utf-8")
    h = tmp_path / "a.txt"
    h.write_text('<img src="\\\\server\\share\\x.png">', encoding="utf-8")
    for path in (f, g, h):
        for mode in fi.modes_for(fi.kind_of_path(str(path))):
            att = fi.attach(str(path), mode)
            assert html_guard.clean(att.html) == att.html
            assert "<img src=\"\\\\" not in att.html and "<img src=x" not in att.html
