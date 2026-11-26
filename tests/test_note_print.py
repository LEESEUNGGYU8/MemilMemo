"""인쇄와 PDF로 저장(app/ui/note_print.py): 쪽 나눔, 제목과 메타 줄, 메모 색, 목록
기호, 남이 보낸 본문이 바깥 자원을 열지 않는 것.

실제 프린터와 프린터 창은 쓰지 않는다. 그리기는 PDF 파일과 그림(QImage)으로만 본다."""
from __future__ import annotations

import os
import re
from datetime import datetime

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSizeF, QUrl  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QColor,
    QImage,
    QPageSize,
    QPainter,
    QPixmap,
    QTextDocument,
)
from PySide6.QtWidgets import QApplication  # noqa: E402

from app import i18n  # noqa: E402
from app.ui import note_print as P  # noqa: E402
from app.ui import rich_text as R  # noqa: E402
from app.ui import theme  # noqa: E402

A4 = QPageSize(QPageSize.A4).size(QPageSize.Point)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    i18n.set_language("ko")
    theme.apply_theme(app, "")
    yield app


def _ms(year: int, month: int, day: int, hour: int, minute: int) -> int:
    return int(datetime(year, month, day, hour, minute).timestamp() * 1000)


def _note(content: str = "<p>회의 준비</p>", **extra) -> dict:
    note = {"id": "n", "content": content, "color": "#FFE066", "created_by": "지우",
            "updated_at": _ms(datetime.now().year, 11, 24, 9, 35)}
    note.update(extra)
    return note


def _png(width: int = 40, height: int = 20, color: str = "#3366CC") -> str:
    image = QImage(width, height, QImage.Format_RGB32)
    image.fill(QColor(color))
    return R.image_to_data_url(image)


def _pdf_pages(path) -> int:
    data = open(path, "rb").read()
    assert data.startswith(b"%PDF")
    return len(re.findall(rb"/Type\s*/Page\b", data))


def _images(doc) -> list:
    out = []
    block = doc.begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            fmt = it.fragment().charFormat()
            if fmt.isImageFormat():
                out.append(fmt.toImageFormat())
            it += 1
        block = block.next()
    return out


def _blocks(doc) -> list:
    out = []
    block = doc.begin()
    while block.isValid():
        out.append(block)
        block = block.next()
    return out


def _page_of(doc, y: float) -> int:
    return int(y // doc.page.height)


def _paint(doc, index: int = 0) -> QImage:
    """한 쪽을 문서 단위 그대로 그림으로 그린다."""
    pg = doc.page
    image = QImage(int(pg.width), int(pg.height), QImage.Format_RGB32)
    image.fill(QColor("#FFFFFF"))
    painter = QPainter(image)
    P._paint_page(painter, doc, index, "")
    painter.end()
    return image


# ── PDF 와 쪽 ─────────────────────────────────────────────────────────

def test_pdf_has_one_page_per_note(qapp, tmp_path):
    path = tmp_path / "notes.pdf"
    notes = [_note("<p>첫째</p>"), _note("<p>둘째</p>"), _note("셋째")]
    assert P.save_pdf(str(path), notes) == 3
    assert _pdf_pages(path) == 3


def test_short_notes_printed_continuously_share_a_page(qapp, tmp_path):
    path = tmp_path / "notes.pdf"
    notes = [_note("<p>첫째</p>"), _note("<p>둘째</p>"), _note("셋째")]
    assert P.save_pdf(str(path), notes, one_per_page=False) == 1
    assert _pdf_pages(path) == 1


def test_a_long_note_runs_over_several_pages(qapp, tmp_path):
    long = _note("\n".join(f"{n}번째 줄" for n in range(1, 301)))
    path = tmp_path / "long.pdf"
    pages = P.save_pdf(str(path), [long])
    assert pages >= 3
    assert _pdf_pages(path) == pages
    # 이어서 찍어도 긴 메모는 여러 쪽이고, 뒤의 짧은 메모는 그 끝에 붙는다.
    both = tmp_path / "both.pdf"
    assert P.save_pdf(str(both), [long, _note("<p>짧은 메모</p>")],
                      one_per_page=False) == pages
    assert P.save_pdf(str(both), [long, _note("<p>짧은 메모</p>")]) == pages + 1


def test_every_page_gets_its_number(qapp, tmp_path):
    seen = []

    def label(n: int, total: int) -> str:
        seen.append((n, total))
        return f"{n} / {total}"

    P.save_pdf(str(tmp_path / "n.pdf"), [_note(), _note(), _note()], page_label=label)
    assert seen == [(1, 3), (2, 3), (3, 3)]
    assert P.default_page_label(2, 5) == "2 / 5"


def test_nothing_to_print_writes_no_file(qapp, tmp_path):
    path = tmp_path / "none.pdf"
    assert P.save_pdf(str(path), []) == 0
    assert P.save_pdf(str(path), ["글자", None]) == 0
    assert not path.exists()
    assert P.build_document([]).spans == []


def test_a_pdf_that_cannot_be_written_raises(qapp, tmp_path):
    with pytest.raises(OSError):
        P.save_pdf(str(tmp_path / "없는 폴더" / "a.pdf"), [_note()])


def test_page_size_and_margins_follow_the_paper(qapp, tmp_path):
    doc = P.build_document([_note()])
    a4 = doc.page
    dpi = P._source_dpi()
    assert a4.width == pytest.approx(595 * dpi / 72, abs=1)
    assert a4.margin == pytest.approx(15 * dpi / 25.4)
    frame = doc.rootFrame().frameFormat()
    assert frame.leftMargin() == pytest.approx(a4.margin)
    # 흰 종이에서는 여백이 15mm, 색을 칠하면 그 안쪽에 조금 더 띄운다.
    colored = P.build_document([_note()], keep_color=True)
    assert colored.rootFrame().frameFormat().leftMargin() > a4.margin
    # 다른 종이(A5)에 맞추면 쪽이 늘어난다.
    long = _note("\n".join(f"{n}번째 줄" for n in range(1, 120)))
    doc = P.build_document([long])
    on_a4 = doc.pageCount()
    a5 = QPageSize(QPageSize.A5).size(QPageSize.Point)
    assert P.layout_pages(doc, a5) > on_a4
    path = tmp_path / "a5.pdf"
    assert P.save_pdf(str(path), [long], page_size=QPageSize(QPageSize.A5)) > on_a4


# ── 제목과 메타 줄 ─────────────────────────────────────────────────────

def test_title_and_meta_line_are_on_top(qapp):
    year = datetime.now().year
    note = _note("<p>주간 회의 메모</p><p>견적 회신</p>",
                 updated_at=_ms(year, 11, 24, 9, 35))
    doc = P.build_document([note])
    blocks = _blocks(doc)
    assert blocks[0].text() == "주간 회의 메모"
    assert blocks[1].text() == "지우 작성, 11월 24일 오전 9:35 고침"
    # 제목으로 올린 첫 줄은 본문에서 다시 찍지 않는다.
    assert doc.toPlainText().count("주간 회의 메모") == 1
    assert blocks[2].text() == "견적 회신"
    title_fmt = blocks[0].begin().fragment().charFormat()
    body_fmt = blocks[2].begin().fragment().charFormat()
    assert title_fmt.fontWeight() >= 700
    assert title_fmt.fontPointSize() > P._font_of(doc, body_fmt).pointSizeF()


def test_meta_line_leaves_out_what_is_unknown(qapp):
    last_year = datetime.now().year - 1
    assert (P.default_meta_text({"created_by": "민준",
                                 "updated_at": _ms(last_year, 3, 2, 15, 4)})
            == f"민준 작성, {last_year}년 3월 2일 오후 3:04 고침")
    assert P.default_meta_text({"created_by": "민준"}) == "민준 작성"
    assert P.default_meta_text({"updated_at": _ms(datetime.now().year, 1, 5, 0, 7)}) \
        == "1월 5일 오전 12:07 고침"
    assert P.default_meta_text({"created_by": "", "updated_at": 0}) == ""
    assert P.default_meta_text({"created_by": 3, "updated_at": "어제"}) == ""
    doc = P.build_document([{"id": "x", "content": "<p>제목</p><p>본문</p>"}])
    assert [b.text() for b in _blocks(doc)] == ["제목", "본문"]


def test_caller_can_give_the_title_and_meta_line(qapp):
    doc = P.build_document([_note("<p>첫 줄</p><p>둘째 줄</p>")],
                           title_text=lambda n: "회의록",
                           meta_text=lambda n: "작성자 지우")
    assert [b.text() for b in _blocks(doc)] == ["회의록", "작성자 지우", "첫 줄", "둘째 줄"]


def test_a_long_first_line_stays_in_the_body(qapp):
    line = "가나다라 " * 30
    doc = P.build_document([_note(f"<p>{line}</p>")])
    blocks = _blocks(doc)
    assert blocks[0].text().endswith("…")
    assert len(blocks[0].text()) <= P.TITLE_MAX + 1
    assert blocks[2].text().strip() == line.strip()


def test_a_list_line_on_top_is_kept_in_the_body(qapp):
    html = f'<p><img src="{R.checkbox_url(False)}" />  할 일</p><p>메모</p>'
    doc = P.build_document([_note(html)])
    blocks = _blocks(doc)
    assert blocks[0].text() == "할 일"
    assert blocks[2].text() == "할 일"
    assert blocks[2].blockFormat().intProperty(R.MARKER_PROP) == R.MARKER_CHECK0


def test_empty_notes_still_get_a_title(qapp, tmp_path):
    notes = [{"id": "e1", "content": ""}, {"id": "e2"}, {"id": "e3", "content": None},
             {"id": "e4", "content": "<p></p><p> </p>"},
             {"id": "e5", "content": f'<p><img src="{_png()}" /></p>'}]
    doc = P.build_document(notes)
    titles = [doc.findBlock(s.title_pos).text() for s in doc.spans]
    assert titles == ["(빈 메모)"] * 4 + ["(이미지)"]
    assert all(s.body_pos == -1 for s in doc.spans[:4])
    assert doc.spans[4].body_pos >= 0
    assert P.save_pdf(str(tmp_path / "e.pdf"), notes) == 5


def test_blank_lines_around_the_body_are_left_out(qapp):
    doc = P.build_document([_note("<p></p><p>제목</p><p></p><p>본문</p><p></p><p></p>")])
    assert [b.text() for b in _blocks(doc)] == [
        "제목", "지우 작성, 11월 24일 오전 9:35 고침", "본문"]


# ── 목록, 그림, 표 ─────────────────────────────────────────────────────

def test_list_markers_are_drawn_in_the_margin_like_on_screen(qapp):
    html = ('<p>제목</p>'
            f'<p><img src="{R.bullet_url_for("square")}" />  견적</p>'
            f'<p style="-qt-block-indent:1;"><img src="{R.bullet_url_for("square")}" />'
            '  회신</p>'
            f'<p><img src="{R.checkbox_url(True)}" />  끝낸 일</p>'
            f'<p><a name="{R.NUMBER_ANCHOR}"></a>1. 받기</p>'
            f'<p><a name="{R.NUMBER_ANCHOR}"></a>2. 등록</p>'
            '<p>1. 손으로 친 번호</p>')
    doc = P.build_document([_note(html)])
    body = _blocks(doc)[2:]
    shape = [(b.text(), b.blockFormat().intProperty(R.MARKER_PROP), P._level(b))
             for b in body]
    assert shape == [("견적", R.MARKER_SQUARE, 0), ("회신", R.MARKER_SQUARE, 1),
                     ("끝낸 일", R.MARKER_CHECK1, 0), ("받기", R.MARKER_NUMBER, 0),
                     ("등록", R.MARKER_NUMBER, 0), ("1. 손으로 친 번호", R.MARKER_NONE, 0)]
    # 기호 그림은 줄에서 빠지고 여백에 그린다. 딸린 줄은 더 들어간다.
    assert not any(R.is_marker_url(f.name()) for f in _images(doc))
    assert body[1].blockFormat().leftMargin() > body[0].blockFormat().leftMargin() > 0
    assert all(b.blockFormat().indent() == 0 for b in body)
    assert doc.spans[0].numbers == {body[3].blockNumber(): 1, body[4].blockNumber(): 2}
    # 끝낸 할 일은 화면처럼 줄을 긋는다.
    assert body[2].begin().fragment().charFormat().fontStrikeOut()


def test_markers_are_painted(qapp):
    html = f'<p>제목</p><p><img src="{R.bullet_url_for("square")}" />  항목</p>'
    doc = P.build_document([_note(html)])
    block = _blocks(doc)[2]
    rect = doc.documentLayout().blockBoundingRect(block)
    line = block.layout().lineAt(0)
    image = _paint(doc)
    # 글 왼쪽의 기호 칸에 무언가 그려졌다.
    x0 = int(rect.left() + line.x() - R.marker_column(P._em_of(doc, P._lead_format(block))))
    y0, y1 = int(rect.top() + line.y()), int(rect.top() + line.y() + line.height())
    inked = [image.pixelColor(x, y).lightness() < 200
             for x in range(x0, int(rect.left() + line.x())) for y in range(y0, y1)]
    assert any(inked)


def test_data_png_images_are_kept_at_their_size_up_to_the_page_width(qapp, tmp_path):
    small, wide = _png(40, 20), _png(3000, 300)
    doc = P.build_document([_note(f'<p>그림</p><p><img src="{small}" width="10" height="5" />'
                                  f'<img src="{wide}" /></p>')])
    shown = {f.name(): (f.width(), f.height()) for f in _images(doc)}
    assert shown[small] == (40, 20)
    pg = doc.page
    room = pg.width - 2 * pg.margin
    assert shown[wide][0] == pytest.approx(room, abs=1)
    assert shown[wide][1] == pytest.approx(room / 10, abs=1)
    # 그려 보면 그림이 실제로 읽혀 있다.
    assert P.save_pdf(str(tmp_path / "img.pdf"), [_note(f'<p><img src="{small}" /></p>')]) == 1
    _paint(doc)
    loaded = doc.resource(QTextDocument.ImageResource, QUrl(small))
    assert loaded is not None
    if not isinstance(loaded, (QImage, QPixmap)):
        loaded = QImage.fromData(loaded)
    assert loaded.width() == 40


def test_tall_images_fit_on_one_page(qapp):
    tall = _png(200, 5000)
    doc = P.build_document([_note(f'<p><img src="{tall}" /></p>')])
    image = _images(doc)[0]
    pg = doc.page
    assert image.height() < pg.height - 2 * pg.margin
    assert image.width() == pytest.approx(200 * image.height() / 5000, abs=1)


def test_tables_are_printed(qapp, tmp_path):
    html = ('<p>표</p><table border="1"><tr><td>품목</td><td>수량</td></tr>'
            '<tr><td>사과</td><td>3</td></tr></table>')
    doc = P.build_document([_note(html)])
    assert doc.rootFrame().childFrames()
    assert "사과" in doc.toPlainText()
    span = doc.spans[0]
    table = doc.rootFrame().childFrames()[0]
    assert span.bottom >= doc.documentLayout().frameBoundingRect(table).bottom()
    assert P.save_pdf(str(tmp_path / "t.pdf"), [_note(html)], keep_color=True) == 1


# ── 남이 보낸 본문 ─────────────────────────────────────────────────────

def test_outside_images_and_styles_are_never_opened(qapp, tmp_path, monkeypatch):
    asked: list[str] = []
    original = P.PrintDocument.loadResource

    def spy(self, kind, name):
        asked.append(name.toString() if isinstance(name, QUrl) else str(name))
        return original(self, kind, name)

    monkeypatch.setattr(P.PrintDocument, "loadResource", spy)
    png = _png()
    html = ('<html><head><link rel="stylesheet" href="\\\\server\\share\\a.css" />'
            '<style>p { background: url(http://example.com/b.png); }</style></head><body>'
            '<p>제목</p>'
            '<p><img src="http://example.com/a.png" /></p>'
            '<p><img src="file:///C:/Windows/win.ini" /></p>'
            '<p><img src="\\\\server\\share\\c.png" /></p>'
            f'<p style="background-image: url(file:///C:/x.png)">글</p>'
            f'<p><img src="{png}" /></p></body></html>')
    assert P.save_pdf(str(tmp_path / "safe.pdf"), [_note(html)], keep_color=True) == 1
    names = [f.name() for f in _images(P.build_document([_note(html)]))]
    assert names == [png]
    assert asked and all(n.startswith("data:") for n in asked)


def test_the_document_itself_refuses_outside_resources(qapp):
    doc = P.PrintDocument()
    for url in ("file:///C:/Windows/win.ini", "http://example.com/a.png",
                "//server/share/a.png"):
        image = doc.loadResource(QTextDocument.ImageResource, QUrl(url))
        assert isinstance(image, QImage) and image.size().width() == 1
        assert doc.loadResource(QTextDocument.StyleSheetResource, QUrl(url)) is None
    # 앱 안의 글머리 그림 이름만 앱이 그린 그림으로 바꾼다.
    bullet = doc.loadResource(QTextDocument.ImageResource,
                              QUrl(R.bullet_url_for("circle")))
    assert isinstance(bullet, QImage) and bullet.width() > 1


def test_markup_looking_titles_and_names_are_plain_text(qapp):
    # 서식 없이 저장된 글은 꺾쇠가 있어도 글자 그대로다(화면과 같다).
    doc = P.build_document([_note("홍길동 <hong@example.com>\na < b 이고 c > d",
                                  created_by='<img src="file:///C:/a.png">')])
    blocks = _blocks(doc)
    assert blocks[0].text() == "홍길동 <hong@example.com>"
    assert blocks[1].text().startswith('<img src="file:///C:/a.png"> 작성')
    assert blocks[2].text() == "a < b 이고 c > d"
    assert _images(doc) == []


# ── 종이 색과 글자 색 ─────────────────────────────────────────────────

def test_white_paper_by_default_and_note_color_when_kept(qapp, tmp_path):
    note = _note("<p>제목</p><p>본문</p>", color="#BAFFC9")
    plain = P.build_document([note])
    pg = plain.page
    inside = (int(pg.margin + 3), int(pg.margin + 3))
    assert _paint(plain).pixelColor(*inside).name() == "#ffffff"
    kept = P.build_document([note], keep_color=True)
    assert _paint(kept).pixelColor(*inside).name() == "#baffc9"
    # 색칠은 쪽 여백까지 번지지 않는다.
    assert _paint(kept).pixelColor(3, 3).name() == "#ffffff"
    for color in ("#3A3A3A", "#2E4057", "#FFE066"):
        assert P.save_pdf(str(tmp_path / "c.pdf"),
                          [_note(color=color), _note("평문 메모", color=color)],
                          keep_color=True, one_per_page=False) >= 1


def test_text_stays_readable_on_the_paper(qapp):
    html = ('<p>제목</p><p><span style="color:#ffffff;">흰 글자</span></p>'
            '<p>보통 글자</p><p><a href="https://example.com">링크</a></p>')
    white = P.build_document([_note(html, color="#2E4057")])
    blocks = _blocks(white)
    fg = blocks[2].begin().fragment().charFormat().foreground().color()
    assert R.contrast_ratio(fg, QColor("#FFFFFF")) >= R.MIN_READABLE_CONTRAST
    # 어두운 메모를 색 그대로 찍으면 색을 정하지 않은 글자가 희다.
    dark = P.build_document([_note(html, color="#2E4057")], keep_color=True)
    blocks = _blocks(dark)
    plain = blocks[3].begin().fragment().charFormat().foreground().color()
    assert R.contrast_ratio(plain, QColor("#2E4057")) >= 4.5
    title = blocks[0].begin().fragment().charFormat().foreground().color()
    assert title.name() == "#ffffff"
    link = blocks[4].begin().fragment().charFormat().foreground().color()
    assert link.name() == R.LINK_COLOR_DARK.lower()


# ── 이어서 찍기 ───────────────────────────────────────────────────────

def test_a_title_is_never_left_alone_at_the_bottom_of_a_page(qapp):
    second = _note("<p>둘째 메모</p><p>본문 첫 줄</p><p>본문 둘째 줄</p>")
    moved = 0
    for count in range(30, 80, 2):
        first = _note("\n".join(f"{n}번째 줄" for n in range(1, count)))
        doc = P.build_document([first, second], one_per_page=False)
        span = doc.spans[1]
        title_top = P._first_line_top(doc, doc.findBlock(span.title_pos))
        body = P._first_line_bottom(doc, doc.findBlock(span.body_pos))
        assert _page_of(doc, title_top) == _page_of(doc, body - 0.5)
        moved += span.forced
        if span.forced:
            # 넘긴 제목은 새 쪽의 맨 위에서 시작한다.
            pg = doc.page
            assert title_top - _page_of(doc, title_top) * pg.height == \
                pytest.approx(pg.margin, abs=1)
    assert moved > 0


def test_relayout_for_another_paper_starts_from_the_original_breaks(qapp):
    first = _note("\n".join(f"{n}번째 줄" for n in range(1, 47)))
    second = _note("<p>둘째 메모</p><p>본문</p>")
    doc = P.build_document([first, second], one_per_page=False)
    big = QSizeF(A4.width(), A4.height() * 3)
    P.layout_pages(doc, big)
    assert not doc.spans[1].forced
    assert doc.pageCount() == 1


# ── 문구 ─────────────────────────────────────────────────────────────

def test_texts_come_from_the_locale_file_first(qapp, monkeypatch):
    monkeypatch.setitem(i18n.TABLES["ko"], "print.page", "{n}쪽 / {total}쪽")
    assert P.default_page_label(1, 2) == "1쪽 / 2쪽"


@pytest.mark.parametrize("language, expected", [
    ("ko", "지우 작성"), ("en", "By 지우"), ("ja", "지우 作成")])
def test_texts_have_fallbacks_for_each_language(qapp, language, expected):
    try:
        i18n.set_language(language)
        assert P.default_meta_text({"created_by": "지우"}) == expected
    finally:
        i18n.set_language("ko")


def test_pdf_file_name(qapp):
    assert P.pdf_file_name("주간 회의: 11/24") == "주간 회의 11 24.pdf"
    assert P.pdf_file_name("  ") == "메모.pdf"
    assert P.pdf_file_name("con") == "메모.pdf"
    assert P.pdf_file_name("끝에 점...") == "끝에 점.pdf"
    assert len(P.pdf_file_name("가" * 300)) == 84
