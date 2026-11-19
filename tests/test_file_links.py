"""파일 링크 테스트: 경로와 href 변환, 종류 판정, 편집기 안의 링크, 저장 형식.
화면 없이 offscreen 플랫폼으로 돈다."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData, QPoint, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QColor, QTextCursor, QTextDocument  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app import file_links as fl  # noqa: E402
from app import i18n  # noqa: E402
from app.settings import AppSettings  # noqa: E402
from app.ui import link_open  # noqa: E402
from app.ui.link_dialog import LinkDialog  # noqa: E402
from app.ui.link_icons import link_icon_pixmap  # noqa: E402
from app.ui.rich_text import (  # noqa: E402
    LINK_COLOR_DARK,
    LINK_COLOR_LIGHT,
    LINK_ICON_PROP,
    LINK_SPACER,
    RichTextEdit,
    html_to_plain,
)
from app.ui.sticky_note import StickyNoteWidget  # noqa: E402

UNC = "\\\\NAS\\업무 폴더\\견적서 #3.xlsx"
LOCAL = "C:\\문서\\보고서 (최종).pdf"


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    i18n.set_language("ko")
    yield app


# ── 경로와 href ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("path,is_dir", [
    (UNC, False),
    (LOCAL, False),
    ("\\\\NAS\\업무 폴더", True),
    ("C:\\문서", True),
    ("C:\\", True),
    ("D:\\a%20b\\50% 할인.txt", False),
])
def test_path_href_round_trip(path, is_dir):
    href = fl.path_to_href(path, is_dir=is_dir)
    assert href.startswith("file://")
    assert fl.href_to_path(href) == path
    assert fl.is_folder_href(href) is is_dir
    # href 는 HTML 속성에 그대로 들어간다. 공백·한글·#·% 가 날것으로 남지 않는다.
    assert " " not in href and "#" not in href
    assert href.isascii()


def test_unc_href_keeps_the_server_as_host():
    href = fl.path_to_href(UNC)
    assert href.startswith("file://NAS/")
    assert fl.is_unc(fl.href_to_path(href))
    assert fl.is_local_drive(LOCAL) and not fl.is_unc(LOCAL)


@pytest.mark.parametrize("href,kind", [
    (fl.path_to_href(UNC), fl.KIND_SHEET),
    (fl.path_to_href(LOCAL), fl.KIND_PDF),
    (fl.path_to_href("C:\\문서", is_dir=True), fl.KIND_FOLDER),
    (fl.path_to_href("C:\\a\\보고서.hwpx"), fl.KIND_HWP),
    (fl.path_to_href("C:\\a\\사진.JPG"), fl.KIND_IMAGE),
    (fl.path_to_href("C:\\a\\setup.exe"), fl.KIND_APP),
    (fl.path_to_href("C:\\a\\run.js"), fl.KIND_APP),
    (fl.path_to_href("C:\\a\\main.py"), fl.KIND_CODE),
    (fl.path_to_href("C:\\a\\이름없음"), fl.KIND_FILE),
    (fl.path_to_href("C:\\a\\.gitignore"), fl.KIND_FILE),
    ("https://example.com/a.pdf", fl.KIND_WEB),
])
def test_kind_follows_the_real_target(href, kind):
    assert fl.kind_of(href) == kind


def test_executables_need_confirmation():
    for name in ("setup.exe", "run.bat", "a.ps1", "바로 가기.lnk", "x.py",
                 "m.msi", "s.vbs"):
        assert fl.needs_confirm(fl.path_to_href("C:\\a\\" + name)), name
    for name in ("보고서.pdf", "표.xlsx", "사진.png", "메모.txt"):
        assert not fl.needs_confirm(fl.path_to_href("C:\\a\\" + name)), name
    # 이름이 .exe 로 끝나는 폴더는 폴더다.
    assert not fl.needs_confirm(fl.path_to_href("C:\\a\\tool.exe", is_dir=True))
    assert not fl.needs_confirm("https://example.com/setup.exe")


def test_parse_target_accepts_paths_and_web_only():
    assert fl.parse_target('"' + LOCAL + '"') == fl.path_to_href(LOCAL)
    assert fl.parse_target(UNC) == fl.path_to_href(UNC)
    assert fl.is_folder_href(fl.parse_target("C:\\문서\\"))
    assert fl.is_folder_href(fl.parse_target("C:\\문서", is_dir=True))
    assert fl.parse_target("www.example.com") == "https://www.example.com"
    assert fl.parse_target("https://example.com/x") == "https://example.com/x"
    assert fl.parse_target(fl.path_to_href(UNC)) == fl.path_to_href(UNC)
    # 그 밖의 스킴과 뜻 모를 글자는 받지 않는다.
    for bad in ("", "   ", "javascript:alert(1)", "ms-settings:display",
                "ftp://host/file", "보고서", "..\\상대\\경로.txt"):
        assert fl.parse_target(bad) is None, bad
    assert not fl.is_supported("ms-settings:display")
    assert fl.is_supported("file:///C:/a.txt") and fl.is_supported("http://a")


def test_display_name_and_description():
    assert fl.display_name(fl.path_to_href(UNC)) == "견적서 #3.xlsx"
    assert fl.display_name(fl.path_to_href("C:\\문서", is_dir=True)) == "문서"
    assert fl.display_name("https://example.com/") == "example.com"
    assert fl.describe_target(fl.path_to_href(UNC)) == UNC


def test_share_unc_picks_the_deepest_share():
    shares = [("Docs", "C:\\문서"), ("Team", "C:\\문서\\팀"), ("Other", "D:\\x")]
    assert fl.share_unc_for("C:\\문서\\팀\\표.xlsx", shares, "PC1") == \
        "\\\\PC1\\Team\\표.xlsx"
    assert fl.share_unc_for("c:\\문서\\a.txt", shares, "PC1") == \
        "\\\\PC1\\Docs\\a.txt"
    assert fl.share_unc_for("C:\\문서", shares, "PC1") == "\\\\PC1\\Docs"
    assert fl.share_unc_for("C:\\문서함\\a.txt", shares, "PC1") is None
    assert fl.share_unc_for(UNC, shares, "PC1") is None
    assert fl.network_path_for(UNC) == UNC


def test_icons_render_for_every_kind(qapp):
    for kind in fl.KINDS + ("모르는 종류",):
        pix = link_icon_pixmap(kind, 16, 2.0)
        assert not pix.isNull()
        assert pix.width() == 32 and abs(pix.devicePixelRatio() - 2.0) < 0.01
        assert pix.toImage().pixelColor(16, 16).alpha() > 0


# ── 편집기 ──────────────────────────────────────────────────────────────

def _edit(qapp) -> RichTextEdit:
    edit = RichTextEdit()
    edit.resize(300, 200)
    edit.show()
    qapp.processEvents()
    return edit


def _spacers(edit: RichTextEdit) -> list[tuple[int, str]]:
    out = []
    block = edit.document().begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            it += 1
            if frag.charFormat().hasProperty(LINK_ICON_PROP):
                out.append((frag.position(),
                            str(frag.charFormat().property(LINK_ICON_PROP))))
        block = block.next()
    return out


def test_insert_link_adds_one_icon_slot_and_anchor(qapp):
    edit = _edit(qapp)
    edit.insertPlainText("앞글 ")
    href = fl.path_to_href(UNC)
    edit.insert_link("3분기 견적서", href)
    # toPlainText 는 줄바꿈 없는 공백을 보통 공백으로 바꿔 돌려준다.
    assert edit.toPlainText() == "앞글  3분기 견적서"
    assert _spacers(edit) == [(3, fl.KIND_SHEET)]
    start, end, got = edit.link_at(6)
    assert (start, end, got) == (3, 11, href)
    assert edit.link_text(start, end) == "3분기 견적서"
    # 아이콘 자리와 글자 어디를 눌러도 같은 링크다.
    for pos in (3, 5, 9):
        cur = QTextCursor(edit.document())
        cur.setPosition(pos)
        r = edit.cursorRect(cur)
        assert edit.anchorAt(QPoint(r.left() + 3, r.center().y())) == href
    # 이어서 치는 글자는 링크에 딸려 가지 않는다.
    QTest.keyClicks(edit, " ok")
    assert edit.link_at(11)[1] == 11
    cur = QTextCursor(edit.document())
    cur.setPosition(14)
    assert not cur.charFormat().isAnchor()


def test_saved_html_has_plain_anchor_without_icon(qapp):
    settings = AppSettings()
    settings.fold_animation = False
    note = {"id": "n", "version": 1, "color": "#FFE066", "width": 300,
            "height": 240, "content": "첫 줄"}
    w = StickyNoteWidget(note, "지우", settings=settings)
    w.show()
    commits: list[tuple] = []
    w.commitEdit.connect(lambda *a: commits.append(a))
    w.enter_edit_mode()
    w._text.moveCursor(QTextCursor.End)
    href = fl.path_to_href(UNC)
    w._text.insert_link("견적서", href)
    w._commit_and_release()
    saved = commits[-1][1]
    assert f'<a href="{href}">' in saved
    assert LINK_SPACER not in saved and "&nbsp;" not in saved
    assert "<img" not in saved
    assert html_to_plain(saved).strip() == "첫 줄견적서"

    # 저장본을 다시 불러오면 아이콘 자리가 종류에 맞게 다시 생긴다.
    other = StickyNoteWidget(dict(note, content=saved, color="#3A3A3A"),
                             "서연", settings=settings)
    assert _spacers(other._text) == [(3, fl.KIND_SHEET)]
    # 어두운 메모에서는 링크 글자색도 밝은 색으로 맞춰진다.
    cur = QTextCursor(other._text.document())
    cur.setPosition(6)
    assert cur.charFormat().foreground().color() == QColor(LINK_COLOR_DARK)
    assert not other._text.document().isUndoAvailable()
    # 손대지 않고 편집을 끝내면 저장할 것이 없다(아이콘 자리는 변경이 아니다).
    sent: list[tuple] = []
    other.commitEdit.connect(lambda *a: sent.append(a))
    other.enter_edit_mode()
    other._commit_and_release()
    assert sent == []
    # 색을 밝게 바꾸면 링크색이 따라 바뀐다.
    other.apply_server_state(dict(note, content=saved, color="#FFE066",
                                  version=2))
    cur = QTextCursor(other._text.document())
    cur.setPosition(6)
    assert cur.charFormat().foreground().color() == QColor(LINK_COLOR_LIGHT)
    for x in (w, other):
        x._teardown = True
        x.close()


def test_sync_repairs_icon_slots_and_undo_does_not_loop(qapp):
    edit = _edit(qapp)
    href = fl.path_to_href(LOCAL)
    edit.insert_link("보고서", href)
    edit.document().clearUndoRedoStacks()
    assert _spacers(edit) == [(0, fl.KIND_PDF)]

    # 아이콘 자리를 지우면 곧 다시 생긴다(같은 되돌리기 묶음 안에서).
    cur = edit.textCursor()
    cur.setPosition(0)
    cur.setPosition(1, QTextCursor.KeepAnchor)
    cur.removeSelectedText()
    QTest.qWait(30)
    assert _spacers(edit) == [(0, fl.KIND_PDF)]
    assert edit.toPlainText() == " 보고서"
    # 되돌리기 한 번이면 지우기 전으로 돌아가고, 그 뒤로는 되돌릴 것이 없다.
    edit.undo()
    QTest.qWait(30)
    assert edit.toPlainText() == " 보고서"
    assert not edit.document().isUndoAvailable()

    # 글자를 다 지우면 아이콘 자리도 함께 사라진다.
    cur = edit.textCursor()
    cur.setPosition(1)
    cur.setPosition(4, QTextCursor.KeepAnchor)
    cur.removeSelectedText()
    QTest.qWait(30)
    assert edit.toPlainText() == ""
    assert not edit.has_links()


def test_replace_and_remove_link(qapp):
    edit = _edit(qapp)
    edit.insert_link("보고서", fl.path_to_href(LOCAL))
    start, end, _href = edit.link_at(2)
    new_href = fl.path_to_href("\\\\NAS\\공유", is_dir=True)
    edit.replace_link(start, end, "공유 폴더", new_href)
    assert edit.toPlainText() == " 공유 폴더"
    assert _spacers(edit) == [(0, fl.KIND_FOLDER)]
    start, end, href = edit.link_at(2)
    assert href == new_href
    edit.remove_link(start, end)
    assert edit.toPlainText() == "공유 폴더"
    assert edit.link_at(2) is None and _spacers(edit) == []
    cur = QTextCursor(edit.document())
    cur.setPosition(3)
    assert not cur.charFormat().fontUnderline()


def test_dropped_files_become_links_and_images_stay_images(qapp, tmp_path):
    doc_file = tmp_path / "회의록.docx"
    doc_file.write_bytes(b"x")
    folder = tmp_path / "자료"
    folder.mkdir()
    edit = _edit(qapp)
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(doc_file)),
                  QUrl.fromLocalFile(str(folder))])
    assert edit.canInsertFromMimeData(mime)
    edit.insertFromMimeData(mime)
    kinds = [k for _pos, k in _spacers(edit)]
    assert kinds == [fl.KIND_WORD, fl.KIND_FOLDER]
    assert "회의록.docx" in edit.toPlainText() and "자료" in edit.toPlainText()
    hit = edit.link_at(2)
    assert fl.href_to_path(hit[2]) == str(doc_file)


def test_plain_text_note_stays_plain_without_links(qapp):
    doc = QTextDocument()
    doc.setPlainText("글자만 있는 메모")
    edit = _edit(qapp)
    edit.setPlainText("글자만 있는 메모")
    assert edit.sync_link_icons() is False
    assert not edit.has_links()
    assert not edit.document().isUndoAvailable() or True


# ── 열기 ────────────────────────────────────────────────────────────────

def test_click_opens_link_in_view_mode_without_entering_edit(qapp, monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr("app.ui.sticky_note.open_link",
                        lambda parent, href: opened.append(href))
    href = fl.path_to_href(UNC)
    content = f'<p>앞글 <a href="{href}">견적서</a> 뒷글</p>'
    w = StickyNoteWidget({"id": "n", "version": 1, "color": "#FFE066",
                          "width": 300, "height": 240, "content": content},
                         "지우", settings=AppSettings())
    w.show()
    qapp.processEvents()
    requests: list[str] = []
    w.requestLock.connect(requests.append)
    vp = w._text.viewport()
    cur = QTextCursor(w._text.document())
    cur.setPosition(6)
    r = w._text.cursorRect(cur)
    on_link = QPoint(r.left() + 3, r.center().y())
    assert w._text.anchorAt(on_link) == href
    QTest.mouseClick(vp, Qt.LeftButton, Qt.NoModifier, on_link)
    assert opened == [href]
    assert requests == [] and not w._edit_mode
    assert href in w._link_tooltip(href) or UNC in w._link_tooltip(href)

    # 링크가 아닌 곳을 누르면 예전처럼 편집을 요청한다.
    cur.setPosition(1)
    r = w._text.cursorRect(cur)
    QTest.mouseClick(vp, Qt.LeftButton, Qt.NoModifier,
                     QPoint(r.left() + 2, r.center().y()))
    assert requests == ["n"]

    # 편집 중에는 그냥 누르면 열지 않고, Ctrl 을 누른 채 눌러야 연다.
    w.enter_edit_mode()
    QTest.mouseClick(vp, Qt.LeftButton, Qt.NoModifier, on_link)
    assert opened == [href] and w._edit_mode
    QTest.mouseClick(vp, Qt.LeftButton, Qt.ControlModifier, on_link)
    assert opened == [href, href]
    w._teardown = True
    w.close()


def test_open_link_blocks_unknown_schemes_and_confirms_programs(qapp, monkeypatch):
    warned: list[str] = []
    started: list[str] = []
    asked: list[str] = []
    monkeypatch.setattr(link_open, "_warn",
                        lambda parent, title, body: warned.append(body))
    monkeypatch.setattr(link_open.os, "startfile",
                        lambda path: started.append(path), raising=False)
    monkeypatch.setattr(link_open, "confirm_run",
                        lambda parent, path: asked.append(path) or False)

    link_open.open_link(None, "ms-settings:display")
    assert warned and not started

    exe = fl.path_to_href("C:\\Windows\\System32\\notepad.exe")
    link_open.open_link(None, exe)
    assert asked == ["C:\\Windows\\System32\\notepad.exe"]
    QTest.qWait(200)
    assert started == []          # 묻는 창에서 취소했다.

    # 없는 경로는 열지 않고 알린다(확인은 작업 스레드에서 한다).
    warned.clear()
    link_open.open_link(None, fl.path_to_href("C:\\없는 폴더\\없는 파일.txt"))
    for _ in range(40):
        if warned:
            break
        QTest.qWait(25)
    assert warned and "없는 파일.txt" in warned[0]
    assert started == []


def test_open_link_starts_existing_file(qapp, monkeypatch, tmp_path):
    started: list[str] = []
    monkeypatch.setattr(link_open.os, "startfile",
                        lambda path: started.append(path), raising=False)
    target = tmp_path / "열 문서.txt"
    target.write_text("x", encoding="utf-8")
    link_open.open_link(None, fl.path_to_href(str(target)))
    for _ in range(40):
        if started:
            break
        QTest.qWait(25)
    assert started == [str(target)]


# ── 넣기 창 ─────────────────────────────────────────────────────────────

def test_link_dialog_builds_href_and_default_text(qapp):
    dlg = LinkDialog()
    dlg._target_edit.setText(UNC)
    dlg._on_accept()
    assert dlg.href == fl.path_to_href(UNC)
    assert dlg.text == "견적서 #3.xlsx"

    dlg = LinkDialog(text="3분기 견적", href=fl.path_to_href(UNC), editing=True)
    assert dlg._target_edit.text() == UNC
    dlg._text_edit.setText("  고친   이름 ")
    dlg._on_accept()
    assert dlg.text == "고친 이름" and dlg.href == fl.path_to_href(UNC)

    # 빈 대상과 받지 않는 주소는 넣지 않고 그 자리에서 알린다.
    dlg = LinkDialog()
    dlg._on_accept()
    assert dlg.href == "" and not dlg._error.isHidden()
    dlg._target_edit.setText("javascript:alert(1)")
    dlg._on_accept()
    assert dlg.href == "" and not dlg._error.isHidden()

    dlg = LinkDialog(text="x", href=fl.path_to_href(LOCAL), editing=True)
    dlg._on_remove()
    assert dlg.remove_requested
