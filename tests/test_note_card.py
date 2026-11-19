"""'최근 메모' 카드와 보기 방식(간략히·표준·자세히 x 리스트·그리드) 테스트.
화면 없이 offscreen 플랫폼으로 돈다."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from app import i18n  # noqa: E402
from app.settings import (  # noqa: E402
    VIEW_COMPACT,
    VIEW_DENSITIES,
    VIEW_DETAILED,
    VIEW_GRID,
    VIEW_LAYOUTS,
    VIEW_LIST,
    VIEW_STANDARD,
    AppSettings,
)
from app.ui.note_card import (  # noqa: E402
    GRID_MIN_COLUMN,
    CardFlowLayout,
    ClampLabel,
    NoteCard,
    note_title_and_lines,
)

LONG = ("주간 회의 메모\n신규 거래처 견적은 금요일까지 회신\n창고 재고 실사 일정 확정\n"
        "택배 마감 시간 오후 4시로 변경\n다음 회의는 화요일 오전")


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    i18n.set_language("ko")
    yield app


def _note(nid: str = "a", **over) -> dict:
    note = {"id": nid, "color": "#FFE066", "updated_at": 0,
            "updated_by": "지우", "content": LONG}
    note.update(over)
    return note


def test_title_and_lines():
    title, rest, has_image = note_title_and_lines("\n\n첫 줄\n둘째 줄\n")
    assert (title, rest, has_image) == ("첫 줄", ["둘째 줄"], False)
    title, rest, has_image = note_title_and_lines("")
    assert title == i18n.t("main.note_title_empty") and rest == []


def test_clamp_label_limits_lines_and_breaks_long_words(qapp):
    label = ClampLabel(2)
    label.setText("공백없이아주길게이어지는글자는폭을넘기지않고글자단위로접혀야합니다" * 3)
    lines = label.lines_for(120)
    assert len(lines) == 2
    assert lines[-1].endswith("…")
    one = label.heightForWidth(120)
    label.setMaxLines(4)
    assert len(label.lines_for(120)) == 4
    assert label.heightForWidth(120) > one
    # 짧은 글은 줄 수를 채우지 않는다.
    label.setText("짧은 글")
    assert label.lines_for(200) == ["짧은 글"]


def _host(qapp, count: int, width: int, column: int):
    host = QWidget()
    layout = CardFlowLayout(host)
    layout.set_min_column_width(column)
    cards = []
    for i in range(count):
        card = NoteCard(_note(f"n{i}"), True)
        layout.addWidget(card)
        cards.append(card)
    host.resize(width, 600)
    host.show()
    qapp.processEvents()
    return host, layout, cards


def test_list_layout_is_one_full_width_column(qapp):
    host, layout, cards = _host(qapp, 3, 340, 0)
    xs = {c.geometry().x() for c in cards}
    widths = {c.geometry().width() for c in cards}
    assert len(xs) == 1 and len(widths) == 1
    tops = [c.geometry().y() for c in cards]
    assert tops == sorted(tops) and len(set(tops)) == 3
    host.close()


def test_grid_layout_fills_columns_and_reflows(qapp):
    host, layout, cards = _host(qapp, 5, 340, GRID_MIN_COLUMN)
    for c in cards:
        c.set_view(VIEW_STANDARD, VIEW_GRID)
    layout.invalidate()
    host.resize(341, 600)
    qapp.processEvents()
    # 340px 에는 148px 타일이 두 칸 들어간다.
    assert len({c.geometry().x() for c in cards}) == 2
    assert len({c.geometry().width() for c in cards}) == 1
    assert cards[0].geometry().y() == cards[1].geometry().y()
    host.resize(520, 600)
    qapp.processEvents()
    assert len({c.geometry().x() for c in cards}) == 3
    # 숨긴 카드는 자리를 차지하지 않는다.
    cards[0].setVisible(False)
    layout.invalidate()
    host.resize(521, 600)
    qapp.processEvents()
    assert cards[1].geometry().x() == min(c.geometry().x() for c in cards[1:])
    assert cards[1].geometry().y() == min(c.geometry().y() for c in cards[1:])
    host.close()


def test_insert_widget_puts_card_first(qapp):
    host, layout, cards = _host(qapp, 2, 340, 0)
    newest = NoteCard(_note("new"), True)
    layout.insertWidget(0, newest)
    host.resize(341, 600)
    qapp.processEvents()
    assert newest.geometry().y() < cards[0].geometry().y()
    host.close()


@pytest.mark.parametrize("layout_mode", VIEW_LAYOUTS)
def test_density_changes_card_height(qapp, layout_mode):
    heights = {}
    for density in VIEW_DENSITIES:
        card = NoteCard(_note(), True)
        card.set_view(density, layout_mode)
        heights[density] = card.heightForWidth(
            300 if layout_mode == VIEW_LIST else 160)
    assert heights[VIEW_COMPACT] < heights[VIEW_STANDARD] < heights[VIEW_DETAILED]


def test_dark_note_card_uses_light_text(qapp):
    dark = NoteCard(_note(color="#3A3A3A"), True)
    light = NoteCard(_note(color="#FFE066"), True)
    assert dark._fg == "#FFFFFF"
    assert light._fg == "#1F1F1F"
    # 색이 바뀌면 글자색도 따라 바뀐다.
    dark.update_from(_note(color="#BAE1FF"), True)
    assert dark._fg == "#1F1F1F"


def test_view_settings_round_trip_and_reject_unknown(tmp_path):
    path = tmp_path / "settings.json"
    s = AppSettings()
    assert (s.notes_view_density, s.notes_view_layout) == (
        VIEW_STANDARD, VIEW_LIST)
    s.notes_view_density = VIEW_DETAILED
    s.notes_view_layout = VIEW_GRID
    s.fold_animation = False
    s.save(path)
    again = AppSettings.load(path)
    assert (again.notes_view_density, again.notes_view_layout,
            again.fold_animation, again.dock_on_top) == (
        VIEW_DETAILED, VIEW_GRID, False, True)
    # 손으로 고친 이상한 값은 기본값으로 돌아온다.
    path.write_text('{"notes_view_density": "huge", "notes_view_layout": 3}',
                    encoding="utf-8")
    odd = AppSettings.load(path)
    assert (odd.notes_view_density, odd.notes_view_layout) == (
        VIEW_STANDARD, VIEW_LIST)
