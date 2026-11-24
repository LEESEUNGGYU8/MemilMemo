"""메모 색과 글자 색: 색조별 견본, 색 고르는 창과 색상 코드, 최근 색, 보이지 않는
글자 색을 옮기기."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QTextCursor  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QToolButton  # noqa: E402

from app import config, i18n  # noqa: E402
from app.locales import en, ja, ko  # noqa: E402
from app.settings import AppSettings  # noqa: E402
from app.ui import edit_menu, theme  # noqa: E402
from app.ui import font_colors as fc  # noqa: E402
from app.ui.folder_dialog import FolderEditDialog  # noqa: E402
from app.ui.settings_dialog import _ColorPicker  # noqa: E402
from app.ui.sticky_note import HIGHLIGHT_COLORS, StickyNoteWidget  # noqa: E402

DARK_NOTES = [c for c in config.COLORS if fc.is_dark_ground(QColor(c))]


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    i18n.set_language("ko")
    theme.apply_theme(app, "")
    yield app


def _note(qapp, content: str = "<p>회의 준비</p>", *, color: str = "#FFE066",
          settings: AppSettings | None = None):
    settings = settings or AppSettings()
    settings.save = lambda path=None: None
    w = StickyNoteWidget({"id": "c1", "version": 1, "color": color,
                          "width": 300, "height": 320, "content": content},
                         "지우", settings=settings)
    w.apply_local_state(200, 160)
    w.show()
    qapp.processEvents()
    w.enter_edit_mode()
    assert w._edit_mode
    return w


def _close(w) -> None:
    w._teardown = True
    w.close()


def _select_all(w) -> None:
    cur = w._text.textCursor()
    cur.select(QTextCursor.Document)
    w._text.setTextCursor(cur)


def _ink_at(w, position: int = 1) -> QColor | None:
    cur = QTextCursor(w._text.document())
    cur.setPosition(position)
    return fc.explicit_ink(cur.charFormat())


def _click_cell(grid: fc.SwatchGrid, row: int, column: int) -> None:
    QTest.mouseClick(grid, Qt.LeftButton, pos=grid.cell_center(row, column).toPoint())


# ── 메모 색 ─────────────────────────────────────────────────────────────

def test_there_are_twice_as_many_note_colors_and_the_old_ones_keep_their_place():
    assert len(config.COLORS) == 16 == len(set(config.COLORS))
    assert config.COLORS[:8] == ["#FFE066", "#FFB3BA", "#BAFFC9", "#BAE1FF",
                                 "#E0BBE4", "#FFD9A6", "#F5F5F5", "#3A3A3A"]
    assert config.DEFAULT_COLOR == "#FFE066"
    assert len(DARK_NOTES) == 2 and "#2E4057" in DARK_NOTES


def test_every_note_color_has_a_name_in_every_language(qapp):
    for lang in ("ko", "en", "ja"):
        i18n.set_language(lang)
        names = [theme.color_name(c) for c in config.COLORS]
        assert not any(name.startswith("#") for name in names)
        assert len(set(names)) == 16
    i18n.set_language("ko")


def test_small_grey_text_still_reads_on_every_note_color():
    """카드의 11px 글자(시각, 남은 날)가 가장 옅은 색 위에서도 4.5:1 을 넘는다."""
    for value in config.COLORS:
        ground = QColor(value)
        dark = fc.is_dark_ground(ground)
        alpha = (200 if dark else 170) / 255.0
        ink = 255 if dark else 0
        faint = QColor(*[round(ink * alpha + channel * (1 - alpha))
                         for channel in (ground.red(), ground.green(), ground.blue())])
        assert fc.contrast(faint, ground) >= 4.5, value


def test_the_pickers_lay_the_colors_out_in_two_rows(qapp):
    picker = _ColorPicker("#2E4057")
    grid = picker.layout()
    assert grid.rowCount() == 2 and grid.columnCount() == config.COLOR_COLUMNS
    assert picker._buttons["#2E4057"].isChecked()
    picker._select("#FFF3B0")
    assert picker.value() == "#FFF3B0"
    dlg = FolderEditDialog(name="업무", color="#A6E8E0")
    assert len(dlg._color_buttons) == 16 and dlg._color_buttons["#A6E8E0"].isChecked()
    dlg.deleteLater()


# ── 견본 ────────────────────────────────────────────────────────────────

def test_the_palette_is_a_square_sorted_by_hue_with_black_and_white():
    assert len(fc.PALETTE) == 10 and all(len(row) == 10 for row in fc.PALETTE)
    codes = [code for row in fc.PALETTE for code in row]
    assert len(set(codes)) == 100 and all(fc.parse_code(c) is not None for c in codes)
    # 맨 왼쪽 열은 흰색에서 검정까지의 무채색이다.
    greys = [QColor(row[0]) for row in fc.PALETTE]
    assert greys[0].name() == "#ffffff" and greys[-1].name() == "#000000"
    assert all(g.red() == g.green() == g.blue() for g in greys)
    # 열마다 아래로 갈수록 어둡고, 한 열은 한 색조다.
    for column in range(10):
        tones = [fc._lum(QColor(row[column])) for row in fc.PALETTE]
        assert tones == sorted(tones, reverse=True), fc.HUES[column]
    for column in range(1, 10):
        hues = [QColor(row[column]).hslHueF() for row in fc.PALETTE[2:8]]
        assert max(hues) - min(hues) < 0.09, fc.HUES[column]


def test_hue_names_exist_in_every_language(qapp):
    for module in (ko, en, ja):
        for key in [f"ink.{name}" for name in fc.HUES] + [
                "sticky.font_color_more", "sticky.font_color_recent",
                "color.code", "color.btn_apply"]:
            assert key in module.STRINGS, (module.__name__, key)
    assert fc.hue_name(1) == "빨강"


def test_a_swatch_is_only_its_color_and_tells_its_code(qapp):
    grid = fc.SwatchGrid(fc.PALETTE, names=True)
    assert grid.tip_at(grid.cell_center(4, 1)) == "빨강 #EF4444"
    assert grid.tip_at(QPoint(-5, -5)) == ""
    picked: list = []
    grid.picked.connect(picked.append)
    _click_cell(grid, 4, 7)
    assert [c.name().upper() for c in picked] == [fc.PALETTE[4][7]]
    grid.set_current(QColor("#EF4444"))
    assert grid.current() == "#EF4444"
    grid.show()
    qapp.processEvents()
    grid.setFocus()
    QTest.keyClick(grid, Qt.Key_Right)          # 지금 색의 오른쪽 칸으로
    QTest.keyClick(grid, Qt.Key_Down)
    QTest.keyClick(grid, Qt.Key_Return)
    assert picked[-1].name().upper() == fc.PALETTE[5][2]
    grid.close()


def test_color_codes_are_read_in_the_usual_spellings():
    assert fc.parse_code("#1a2B3c").name() == "#1a2b3c"
    assert fc.parse_code("FF8800").name() == "#ff8800"
    assert fc.parse_code(" #f80 ").name() == "#ff8800"
    for bad in ("", "#12", "red", "#GGGGGG", "#1234567", "12345"):
        assert fc.parse_code(bad) is None, bad
    assert fc.code_of(QColor("#abcdef")) == "#ABCDEF"
    assert fc.clean_recents(["#abcdef", "red", 3, "#ABCDEF", "#12"]) == ["#ABCDEF"]
    assert fc.remember(["#111111", "#222222"], QColor("#222222")) == ["#222222",
                                                                    "#111111"]
    assert len(fc.remember([f"#0000{n:02X}" for n in range(10)],
                           QColor("#ABCDEF"))) == fc.RECENT_MAX


# ── 글자 색을 고르는 판 ─────────────────────────────────────────────────

def test_the_panel_shows_the_palette_recents_and_the_two_buttons(qapp):
    panel = fc.FontColorPanel(QColor("#1F1F1F"), QColor("#2563EB"),
                              ["#0B3D91", "#FFE9A8"])
    assert panel.grid.colors() == [c for row in fc.PALETTE for c in row]
    assert panel.grid.current() == "#2563EB" and not panel.default_button.isChecked()
    assert panel.recent is not None and panel.recent.colors() == ["#0B3D91",
                                                                   "#FFE9A8"]
    picked, more = [], []
    panel.picked.connect(picked.append)
    panel.moreRequested.connect(lambda: more.append(1))
    _click_cell(panel.grid, 9, 0)
    _click_cell(panel.recent, 0, 1)
    panel.default_button.click()
    panel.more_button.click()
    assert [p.name().upper() if p else None for p in picked] == [
        "#000000", "#FFE9A8", None]
    assert more == [1]
    plain = fc.FontColorPanel(QColor("#1F1F1F"), None, [])
    assert plain.default_button.isChecked() and plain.recent is None
    assert plain.grid.current() == ""


# ── 색 고르는 창 ────────────────────────────────────────────────────────

def test_the_picker_keeps_its_parts_in_step(qapp):
    dlg = fc.ColorPicker("글자 색", QColor("#2563EB"), ["#0B3D91"])
    assert dlg.code.text() == "#2563EB" and dlg.grid.current() == "#2563EB"
    # 견본을 누르면 코드와 판이 따라온다.
    _click_cell(dlg.grid, 4, 1)
    assert dlg.code.text() == "#EF4444" and dlg.color().name() == "#ef4444"
    # 판에서 고르면 견본의 테두리는 풀리고 코드가 바뀐다.
    QTest.mouseClick(dlg.field, Qt.LeftButton, pos=QPoint(104, 75))
    assert dlg.code.text() != "#EF4444" and dlg.grid.current() == dlg.code.text()
    assert dlg.color().name().upper() == dlg.code.text()
    # 흰색과 검정은 판의 귀퉁이에 있다.
    dlg.field._take(QPointF(0, 0))
    assert dlg.code.text() == "#FFFFFF"
    QTest.mouseClick(dlg.field, Qt.LeftButton, pos=QPoint(60, 149))
    assert dlg.code.text() == "#000000"
    # 색조 띠를 누르면 그 색조로 바뀐다(검정은 검정 그대로다).
    dlg.field._take(QPointF(207, 0))
    QTest.mouseClick(dlg.hue, Qt.LeftButton, pos=QPoint(69, 8))     # 초록 언저리
    green = dlg.color()
    assert green.green() > green.red() and green.green() > green.blue()
    dlg.deleteLater()


def test_a_typed_code_is_applied(qapp):
    dlg = fc.ColorPicker("메모 색", QColor("#FFE066"))
    assert not dlg.code.testAttribute(Qt.WA_InputMethodEnabled)    # 한글 상태여도 쳐진다
    dlg.code.selectAll()
    QTest.keyClicks(dlg.code, "1a7f5")
    assert dlg.code.text() == "#1A7F5"              # '#' 은 늘 있고 대문자로 적힌다
    assert dlg.color().name() == "#ffe066"          # 다 치기 전에는 그대로다
    QTest.keyClick(dlg.code, Qt.Key_Return)         # 다 치지 않은 코드로는 닫히지 않는다
    assert dlg.result() != fc.ColorPicker.Accepted
    QTest.keyClicks(dlg.code, "c")
    assert dlg.color().name() == "#1a7f5c" and dlg.code.text() == "#1A7F5C"
    assert dlg.grid.current() == "#1A7F5C"
    QTest.keyClick(dlg.code, Qt.Key_Return)         # Enter 로 적용한다
    assert dlg.result() == fc.ColorPicker.Accepted
    dlg.deleteLater()


def test_the_code_field_keeps_its_hash_and_takes_only_code_letters(qapp):
    dlg = fc.ColorPicker("글자 색", QColor("#2563EB"))
    code = dlg.code
    # '#' 은 지워지지 않는다.
    code.selectAll()
    QTest.keyClick(code, Qt.Key_Delete)
    assert code.text() == "#" and code.cursorPosition() == 1
    QTest.keyClick(code, Qt.Key_Backspace)
    assert code.text() == "#"
    QTest.keyClick(code, Qt.Key_Home)
    assert code.cursorPosition() == 1               # 커서가 '#' 앞으로 가지 않는다
    # 영문자와 숫자가 아닌 것은 들어가지 않는다. 코드에 쓰지 않는 글자(G~Z)도 같다.
    QTest.keyClicks(code, "ab-!@ z 9")
    assert code.text() == "#AB9"
    code.insert("한글")
    assert code.text() == "#AB9"
    # 여섯 자를 넘겨 쳐지지 않는다.
    QTest.keyClicks(code, "012345")
    assert code.text() == "#AB9012" and dlg.color().name() == "#ab9012"
    # 전각 영문자와 숫자는 반각으로 바꿔 받는다(붙여 넣은 글도 같다).
    assert fc.code_digits("ＦＦ８８００") == "FF8800"
    assert fc.code_digits("＃ｆｆ８８００") == "FF8800"
    code.selectAll()
    code.insert("ｆｆ８８００")
    assert code.text() == "#FF8800"
    code.selectAll()
    code.insert(" #1d4ed8 ")                        # 붙여 넣은 글
    assert code.text() == "#1D4ED8"
    dlg.deleteLater()


def test_the_two_sides_of_the_picker_are_as_tall_as_each_other(qapp):
    """최근 색을 뺀 왼쪽(견본)과 오른쪽(판, 색조 띠, 코드 줄)의 위아래가 같다."""
    for recents in ([], ["#0B3D91"]):
        dlg = fc.ColorPicker("메모 색", QColor("#FFE066"), recents)
        dlg.setAttribute(Qt.WA_DontShowOnScreen, True)
        dlg.show()
        qapp.processEvents()
        grid_top = dlg.grid.mapTo(dlg, QPoint(0, 0)).y() + fc.GAP
        grid_bottom = dlg.grid.mapTo(dlg, QPoint(0, dlg.grid.height())).y() - fc.GAP
        assert dlg.field.mapTo(dlg, QPoint(0, 0)).y() == grid_top
        for part in (dlg.code, dlg.preview):
            assert part.mapTo(dlg, QPoint(0, part.height())).y() == grid_bottom
        dlg.close()
        dlg.deleteLater()


# ── 보이지 않는 글자 색을 옮기기 ────────────────────────────────────────

def test_an_unreadable_ink_moves_within_its_hue():
    navy, yellow = QColor("#2E4057"), QColor("#FFE066")
    deep_red = QColor(fc.PALETTE[8][1])
    moved = fc.rescue(deep_red, navy)
    assert fc.code_of(moved) in [row[1] for row in fc.PALETTE]      # 같은 열(빨강)
    assert fc.contrast(moved, navy) >= fc.RESCUE_CONTRAST
    pale = fc.rescue(QColor(fc.PALETTE[1][3]), yellow)              # 옅은 노랑
    assert fc.code_of(pale) in [row[3] for row in fc.PALETTE]
    assert fc.contrast(pale, yellow) >= fc.RESCUE_CONTRAST
    # 견본에 없는 색은 색조를 두고 밝기만 옮긴다.
    custom = fc.rescue(QColor("#123A7A"), navy)
    assert abs(custom.hslHueF() - QColor("#123A7A").hslHueF()) < 0.03
    assert fc.contrast(custom, navy) >= fc.RESCUE_CONTRAST - 0.05
    # 무채색은 검정이나 흰색이 된다.
    assert fc.rescue(QColor("#1F1F1F"), navy).name() == "#ffffff"
    assert fc.rescue(QColor("#FFFFFF"), yellow).name() == "#1f1f1f"


# ── 메모에 칠하기 ───────────────────────────────────────────────────────

def test_a_picked_color_is_applied_and_shown_on_the_button(qapp):
    w = _note(qapp)
    _select_all(w)
    plain = w._font_icon_state
    blue = QColor("#1D4ED8")
    w._apply_font_color(blue)
    assert _ink_at(w).name().upper() == "#1D4ED8"
    assert w._current_ink().name().upper() == "#1D4ED8"
    assert w._font_icon_state != plain and w._font_icon_state[1] == blue.name()
    assert "#1d4ed8" in w._text.toHtml().lower()
    assert w._font_color_panel().grid.current() == "#1D4ED8"
    _close(w)


def test_any_readable_color_stays_as_it_was_picked(qapp):
    """고른 색을 메모 색에 맞춰 다른 색으로 바꾸지 않는다. 보이지 않을 때만 옮긴다."""
    w = _note(qapp)
    _select_all(w)
    w._apply_font_color(QColor("#F97316"))          # 노란 메모의 주황(2:1 을 넘는다)
    assert _ink_at(w).name().upper() == "#F97316"
    w._apply_font_color(QColor("#FEF08A"))          # 노란 메모의 옅은 노랑은 안 보인다
    moved = _ink_at(w)
    assert moved.name().upper() != "#FEF08A"
    assert fc.code_of(moved) in [row[3] for row in fc.PALETTE]
    assert fc.contrast(moved, QColor("#FFE066")) >= fc.RESCUE_CONTRAST
    _close(w)


def test_the_default_color_is_no_color_at_all(qapp):
    """'기본 색'은 글자색을 메모의 글자색으로 못박지 않는다. 메모 색을 어둡게 바꿔도
    글자가 따라 밝아지고, 저장하는 글에도 색이 남지 않는다."""
    w = _note(qapp)
    _select_all(w)
    w._apply_font_color(QColor("#1D4ED8"))
    _select_all(w)
    w._apply_font_color(None)
    assert _ink_at(w) is None and w._current_ink() is None
    assert "color:" not in w._text.toHtml().replace("background-color:", "")
    assert w._font_icon_state[1] == w._font_icon_state[0]
    assert w._font_color_panel().default_button.isChecked()
    w._set_color("#2E4057")
    assert _ink_at(w) is None                       # 색을 정한 글자가 되지 않는다
    _close(w)


def test_a_dark_ink_keeps_its_hue_when_the_note_turns_dark(qapp):
    w = _note(qapp)
    _select_all(w)
    w._apply_font_color(QColor(fc.PALETTE[8][4]))   # 짙은 초록
    w._set_color("#2E4057")
    moved = _ink_at(w)
    assert fc.code_of(moved) in [row[4] for row in fc.PALETTE]
    assert fc.contrast(moved, QColor("#2E4057")) >= fc.RESCUE_CONTRAST
    _close(w)


def test_highlighted_text_on_a_dark_note_stays_readable(qapp):
    w = _note(qapp, color="#3A3A3A")
    _select_all(w)
    w._apply_highlight(HIGHLIGHT_COLORS[0])
    # 흰 글자에 밝은 형광펜이다. 글자는 형광펜 위에서 읽히는 색이 된다.
    assert fc.contrast(_ink_at(w), QColor(HIGHLIGHT_COLORS[0])) >= 4.5
    _close(w)


def test_another_color_for_the_text_is_asked_applied_and_remembered(qapp, monkeypatch):
    settings = AppSettings()
    saved: list = []
    w = _note(qapp, settings=settings)
    settings.save = lambda path=None: saved.append(list(settings.recent_font_colors))
    _select_all(w)
    asked: list = []

    def ask(parent, title, initial, recents=None):
        asked.append((title, initial.name().upper(), list(recents or [])))
        return QColor("#0B3D91")

    monkeypatch.setattr(fc, "ask_color", ask)
    w._pick_font_color()
    assert asked == [(i18n.t("sticky.tip_font_color"), "#1F1F1F", [])]
    assert _ink_at(w).name().upper() == "#0B3D91" and w._edit_mode
    assert settings.recent_font_colors == ["#0B3D91"] and saved == [["#0B3D91"]]
    assert w._font_color_panel().recent.colors() == ["#0B3D91"]
    # 견본에 있는 색을 창에서 골랐으면 최근 색에 넣지 않는다.
    monkeypatch.setattr(fc, "ask_color", lambda *a, **k: QColor("#EF4444"))
    w._pick_font_color()
    assert settings.recent_font_colors == ["#0B3D91"]
    # 고르지 않고 닫으면 그대로다.
    monkeypatch.setattr(fc, "ask_color", lambda *a, **k: None)
    w._pick_font_color()
    assert _ink_at(w).name().upper() == "#EF4444"
    _close(w)


def test_the_text_color_menu_leads_to_the_picker(qapp, monkeypatch):
    w = _note(qapp)
    _select_all(w)
    seen: list = []

    def run(menu, _where):
        panel = menu.actions()[0].defaultWidget()
        seen.append(type(panel).__name__)
        panel.more_button.click()

    monkeypatch.setattr(edit_menu, "run_menu", run)
    monkeypatch.setattr(fc, "ask_color", lambda *a, **k: QColor("#7C3AED"))
    w._show_font_color_menu()
    assert seen == ["FontColorPanel"] and _ink_at(w).name().upper() == "#7C3AED"
    _close(w)


# ── 메모 색의 '다른 색' ─────────────────────────────────────────────────

def test_a_note_can_take_any_color_and_remembers_it(qapp, monkeypatch):
    settings = AppSettings()
    w = _note(qapp, settings=settings)
    settings.save = lambda path=None: None
    shown: list = []

    def run(menu, _where):
        box = menu.actions()[0].defaultWidget()
        buttons = box.findChildren(QToolButton)
        shown.append(len(buttons))
        next(b for b in buttons
             if b.text() == i18n.t("sticky.font_color_more")).click()

    monkeypatch.setattr(edit_menu, "run_menu", run)
    asked: list = []

    def ask(parent, title, initial, recents=None):
        asked.append((title, initial.name().upper()))
        return QColor("#123456")

    monkeypatch.setattr(fc, "ask_color", ask)
    w._show_color_menu()
    assert shown == [17]                            # 견본 열여섯과 '다른 색'
    assert asked == [(i18n.t("sticky.tip_color"), "#FFE066")]
    assert w.color == "#123456" and w._edit_mode
    assert settings.recent_note_colors == ["#123456"]
    # 어두운 색이라 글자는 흰색이 된다.
    assert "color: #FFFFFF" in w._text.styleSheet()
    # 다음에 열면 최근 색으로 한 칸이 더 있고, 지금 색으로 켜져 있다.
    recent: list = []

    def look(menu, _where):
        box = menu.actions()[0].defaultWidget()
        recent.extend(b.toolTip() for b in box.findChildren(QToolButton)
                      if b.isChecked())

    monkeypatch.setattr(edit_menu, "run_menu", look)
    w._show_color_menu()
    assert recent == ["#123456"]
    _close(w)


def test_recent_colors_survive_the_settings_file(tmp_path):
    path = tmp_path / "settings.json"
    s = AppSettings()
    s.recent_font_colors = ["#0b3d91", "nope", "#FFE9A8"]
    s.recent_note_colors = ["#123456", 7]
    s.save(path)
    loaded = AppSettings.load(path)
    assert loaded.recent_font_colors == ["#0B3D91", "#FFE9A8"]
    assert loaded.recent_note_colors == ["#123456"]
