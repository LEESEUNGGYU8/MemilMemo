"""메인 창의 새 틀: 상단바(검색칸, 본인 아이콘), 옆줄의 폴더 메뉴, 접속 정보, 카드의 새
모양(목록 줄, 같은 크기의 네모 칸), 격자 칸 크기."""
from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from app import i18n  # noqa: E402
from app.local_state import LocalStateStore  # noqa: E402
from app.network.client_base import ClientBase  # noqa: E402
from app.settings import VIEW_COMPACT, VIEW_GRID, VIEW_LIST, VIEW_STANDARD, AppSettings  # noqa: E402
from app.ui import edit_menu, theme, window_frame  # noqa: E402
from app.ui import main_shell as shell  # noqa: E402
from app.ui.main_window import GRID_CELL, MainWindow  # noqa: E402
from app.ui.note_card import CardFlowLayout, NoteCard  # noqa: E402
from app.ui.sticky_note import StickyNoteWidget  # noqa: E402

ME, OTHER = "지우", "서연"


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    i18n.set_language("ko")
    theme.apply_theme(app, "")
    yield app


def note(nid, text="<p>메모</p>", **more):
    return {"id": nid, "version": 1, "color": "#FFE066", "width": 280, "height": 220,
            "content": text, "created_by": ME, "updated_by": ME,
            "updated_at": int(time.time() * 1000), **more}


class FakeClient(ClientBase):
    def __init__(self):
        super().__init__(ME)
        self.resumed = 0

    def resume(self):
        self.resumed += 1


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(StickyNoteWidget, "bring_to_front",
                        lambda self, *, start_editing=False: None)
    client = FakeClient()
    win = MainWindow(client=client, local_state=LocalStateStore(tmp_path / "ls.json"),
                     role="guest", settings=AppSettings(fold_animation=False))
    client.connectionChanged.emit(True)
    client._dispatch("welcome", {
        "users": [ME, OTHER], "host": OTHER, "locks": {"b": OTHER}, "heartbeat_ms": 3000,
        "trash_days": 30, "reminders": {},
        "folders": [{"id": "f1", "name": "업무", "color": "#BAFFC9", "private_owner": None}],
        "notes": [note("a", "<p>주간 회의</p>"), note("b", "<p>점심</p>", folder_id="f1")]})
    win.resize(780, 600)
    qapp.processEvents()
    yield win
    win._force_quit = True
    win._teardown_stickies()
    win.close()
    win.deleteLater()
    qapp.processEvents()


def test_the_title_bar_holds_the_search_and_my_icon(window, qapp):
    window.show()
    qapp.processEvents()
    bar = window_frame.bar_of(window)
    assert bar is window._title_bar and bar.kind == window_frame.WINDOW
    assert window.menuWidget() is bar
    assert window._search_input.parentWidget() is bar
    assert window._me_btn.parentWidget() is bar
    # 본인 아이콘은 최소화 단추 바로 왼쪽에 있다.
    me_right = window._me_btn.mapTo(bar, QPoint(window._me_btn.width(), 0)).x()
    min_left = bar.min_button.mapTo(bar, QPoint(0, 0)).x()
    assert 0 <= min_left - me_right <= 16
    assert window._me_btn.state() == shell.STATE_OK
    assert "지우" in window._me_btn.toolTip() and "참여 중" in window._me_btn.toolTip()
    assert window.width() == 780


def test_a_folder_in_the_rail_opens_its_menu(window, monkeypatch):
    shown = []
    monkeypatch.setattr(edit_menu, "run_menu",
                        lambda menu, pos: shown.append([a.text() for a in menu.actions()]))
    window._rail.folderMenuRequested.emit("f1", QPoint(10, 10))
    assert window._place == shell.PLACE_FOLDER and window._current_folder == "f1"
    assert shown and any(i18n.t("main.menu_folder_edit") in t or t == "" for t in shown[0])


def test_my_icon_opens_who_is_here_and_the_connection_actions(window, qapp):
    window._open_me_popup()
    popup = window._me_popup
    assert popup.name_label.text() == ME and popup.role_label.text() == "참여 중"
    assert popup.state_label.text() == "연결됨"
    assert len(popup.people_rows) == 1                     # 나를 뺀 사람
    texts = " ".join(w.text() for row in popup.people_rows
                     for w in row.findChildren(type(popup.name_label)))
    assert OTHER in texts and "'점심' 편집 중" in texts
    assert set(popup.actions) == {"change"}
    called = []
    window._reset_connection = lambda: called.append("change")
    popup.changeConnectionRequested.disconnect()
    popup.changeConnectionRequested.connect(window._reset_connection)
    popup.actions["change"].click()
    assert called == ["change"] and not popup.isVisible()
    # 끊기면 사람 대신 안내가 나오고 '지금 다시 연결'이 생긴다.
    window._client.connectionChanged.emit(False)
    window._open_me_popup()
    popup = window._me_popup
    assert "reconnect" in popup.actions and popup.people_rows == []
    popup.actions["reconnect"].click()
    assert window._client.resumed == 1


def test_the_host_can_copy_its_address(qapp):
    info = shell.MeInfo(nick=ME, role="host", state=shell.STATE_OK, state_text="연결됨",
                        address="192.168.0.12:5555", people=[(ME, ""), (OTHER, "")])
    popup = shell.MePopup(info)
    popup.copy_button.click()
    assert QGuiApplication.clipboard().text() == "192.168.0.12:5555"
    assert popup.copy_button.text() == "복사함"
    popup.close()


def test_list_rows_show_actions_only_on_hover(qapp):
    card = NoteCard(note("a"), True)
    card.set_view(VIEW_STANDARD, VIEW_LIST)
    card.resize(500, 60)
    card.show()
    qapp.processEvents()
    card._hover = False                     # 화면 밖 창은 마우스 자리를 알 수 없다
    card._refresh_actions()
    top = card._top_box.height()
    qapp.processEvents()
    stamp_right = card._timestamp.geometry().right()
    assert card._menu_btn.isHidden() and not card._timestamp.isHidden()
    card._hover = True
    card._refresh_actions()
    card._top.activate()
    qapp.processEvents()
    # 시각 오른쪽에 더 보기가 나오고 시각은 그만큼 왼쪽으로 간다. 윗줄 높이는 그대로다.
    # 표시·숨기기 단추는 없다(더 보기 메뉴에 있다).
    assert not card._menu_btn.isHidden() and not card._timestamp.isHidden()
    assert card._timestamp.geometry().right() < stamp_right
    assert card._eye_btn.isHidden() and card._top_box.height() == top
    card.set_marks(remind=True, editing=OTHER)
    assert not card._remind_indicator.isHidden() and not card._edit_indicator.isHidden()
    assert card._edit_name.text() == OTHER
    card.set_visible(False)
    assert not card._hidden_indicator.isHidden()
    card.close()


def test_the_grid_is_square_cells_of_one_size(qapp):
    host = QWidget()
    layout = CardFlowLayout(host)
    layout.set_square(GRID_CELL[VIEW_STANDARD])
    layout.setSpacing(10)
    cards = []
    for i in range(5):
        c = NoteCard(note(f"n{i}", "<p>제목</p><p>한 줄</p><p>두 줄</p>"), True)
        c.set_square(True)
        c.set_view(VIEW_STANDARD, VIEW_GRID)
        layout.addWidget(c)
        cards.append(c)
    host.resize(560, 800)
    host.show()
    qapp.processEvents()
    ref = GRID_CELL[VIEW_STANDARD]
    m = layout.contentsMargins()
    for width in range(380, 1200, 7):
        host.resize(width, 800)
        qapp.processEvents()
        sizes = {(c.width(), c.height()) for c in cards}
        assert len(sizes) == 1
        (w, h), = sizes
        assert w == h and 0.82 * ref <= w <= 1.26 * ref, (width, w)
        # 칸들이 줄 폭을 채운다(오른쪽에 칸 하나만큼의 빈자리가 생기지 않는다).
        if layout.column_count(host.width() - m.left() - m.right()) <= len(cards):
            right = max(c.geometry().right() for c in cards if c.y() == cards[0].y())
            assert host.width() - m.right() - right < 12, (width, right)
    # 글자는 칸 크기와 같은 비율로 커지고 작아진다.
    host.resize(560, 800)
    qapp.processEvents()
    small = cards[0]._title.font().pixelSize(), cards[0].width()
    host.resize(700, 800)
    qapp.processEvents()
    big = cards[0]._title.font().pixelSize(), cards[0].width()
    assert (big[0] > small[0]) == (big[1] > small[1] * 1.04) or big[0] == small[0]
    assert cards[0]._body._max_lines > 1                    # 칸 높이만큼 본문을 보인다
    host.close()


def test_the_cell_size_menu_changes_the_grid(window, monkeypatch):
    window._set_view_mode(layout="grid")
    assert not window._size_btn.isHidden() and window._size_btn.text() == "보통"
    picked = []

    def run(menu, pos):
        picked.append([a.text() for a in menu.actions()])
        menu.actions()[0].trigger()
    monkeypatch.setattr(edit_menu, "run_menu", run)
    window._show_size_menu()
    assert window._settings.notes_view_density == VIEW_COMPACT
    assert window._cards_layout.square() == GRID_CELL[VIEW_COMPACT]
    assert window._size_btn.text() == "작게"
    window._set_view_mode(layout="list")
    # 목록도 작게, 보통, 크게를 고른다.
    assert not window._size_btn.isHidden() and window._cards_layout.square() == 0
    heights = {}
    for density in (VIEW_COMPACT, VIEW_STANDARD, "detailed"):
        window._set_view_mode(density=density)
        card = window._cards["a"]
        heights[density] = card.heightForWidth(500)
    assert heights[VIEW_COMPACT] < heights[VIEW_STANDARD] < heights["detailed"]


def test_the_search_starts_in_line_with_the_list_title(window, qapp):
    window.show()
    qapp.processEvents()
    search_x = window._search_input.mapTo(window, QPoint(0, 0)).x()
    title_x = window._place_title.mapTo(window, QPoint(0, 0)).x()
    assert abs(search_x - title_x) <= 2 and window._title_bar.height() == 48


def test_a_narrow_window_can_really_be_made_narrow(window, qapp):
    window.show()
    window.resize(400, 600)
    qapp.processEvents()
    assert window.width() == 400 and window._rail.isHidden()
    # 좁은 창에서도 검색칸 높이는 그대로다.
    assert window._search_input.height() == 36
    assert not window._narrow_bar.isHidden()
    window.resize(780, 600)
    qapp.processEvents()
    assert not window._rail.isHidden()


def test_choosing_the_place_already_chosen_keeps_it_chosen(window):
    button = window._rail.places["all"]
    assert button.isChecked()
    button.click()
    assert button.isChecked() and window._place == shell.PLACE_ALL


def test_popups_close_without_reopening_on_the_same_press(window):
    window._open_me_popup()
    assert window._me_popup.testAttribute(Qt.WA_NoMouseReplay)
    window._me_popup.close()


def test_settings_open_beside_the_main_window_and_are_ready_early(window, qapp, monkeypatch):
    from app.ui import settings_dialog
    monkeypatch.setattr(settings_dialog, "get_windows_autostart", lambda: False)
    window._prebuild_settings()
    ready = window._settings_dialog
    assert ready is not None and not ready.isVisible()
    window._open_settings()
    assert window._settings_dialog is ready and ready.isVisible() and not ready.isModal()
    window._open_settings()                              # 다시 눌러도 하나다
    assert window._settings_dialog is ready
    ready.reject()                                       # 저장하지 않고 닫는다
    qapp.processEvents()
    assert window._settings_dialog is None


def test_the_history_window_is_its_own_window_and_centers_its_prompt(window, qapp):
    viewer = window._ensure_history_viewer()
    assert viewer.parent() is None
    assert window_frame.bar_of(viewer).min_button is not None
    viewer.show()
    qapp.processEvents()
    assert not viewer._prompt.isHidden() and viewer._detail_box.isHidden()
    assert viewer._prompt.alignment() & Qt.AlignVCenter
    viewer.close()


def test_grid_titles_start_at_the_same_height_with_or_without_marks(qapp):
    host = QWidget()
    layout = CardFlowLayout(host)
    layout.set_square(GRID_CELL[VIEW_STANDARD])
    plain = NoteCard(note("a", "<p>제목</p><p>본문</p>"), True)
    marked = NoteCard(note("b", "<p>제목</p><p>본문</p>"), False)
    for c in (plain, marked):
        c.set_square(True)
        c.set_view(VIEW_STANDARD, VIEW_GRID)
        layout.addWidget(c)
    marked.set_marks(remind=True, editing=OTHER)
    host.resize(560, 400)
    host.show()
    qapp.processEvents()
    assert plain._title.y() == marked._title.y()
    marked._hover = True                       # 마우스를 올려도 움직이지 않는다
    marked._refresh_actions()
    qapp.processEvents()
    assert plain._title.y() == marked._title.y()
    host.close()


def test_a_picture_and_the_text_never_overlap_in_any_cell_size(qapp, monkeypatch):
    from PySide6.QtGui import QPixmap
    from app.ui import note_card
    monkeypatch.setattr(note_card, "_thumbnail", lambda url: QPixmap(84, 64))
    html = ('<p>매장 배치 시안</p><p><img src="data:image/png;base64,AAAA"></p>'
            '<p>입구 쪽 행사 매대를 한 칸 넓히는 안입니다.</p><p>주말 방문객이 평일의 두 배라</p>')
    monkeypatch.setattr(note_card, "has_non_checkbox_image", lambda raw: True)
    for density in (VIEW_COMPACT, VIEW_STANDARD, "detailed"):
        for side in (132, 150, 168, 176, 200, 212, 240):
            card = NoteCard(note("p", html), True)
            card.set_square(True)
            card.set_view(density, VIEW_GRID)
            card.resize(side, side)
            card.show()
            qapp.processEvents()
            card._fit_body_to_cell()
            card.layout().activate()
            qapp.processEvents()
            title, thumb, body = (card._title.geometry(), card._thumb.geometry(),
                                  card._body.geometry())
            if not card._thumb.isHidden():
                assert title.bottom() < thumb.top(), (density, side)
                if not card._body.isHidden():
                    assert not thumb.intersects(body), (density, side)
            card.close()


def test_the_narrowest_window_still_shows_two_grid_columns(window, qapp):
    window.show()
    for density in (VIEW_COMPACT, VIEW_STANDARD, "detailed"):
        window._set_view_mode(density=density, layout="grid")
        window.resize(200, 600)                              # 가장 좁게
        qapp.processEvents()
        window._cards_layout.activate()
        qapp.processEvents()
        assert window.width() == window.minimumWidth()
        xs = {c.x() for c in window._cards.values() if not c.isHidden()}
        assert len(xs) == 2, (density, window.width(), xs)
    window._set_view_mode(layout="list")


def test_the_rail_folds_where_two_grid_columns_no_longer_fit_beside_it(window, qapp):
    window.show()
    window._set_view_mode(layout="grid")
    edge = window._narrow_below()
    window.resize(edge + 4, 600)
    qapp.processEvents()
    window._cards_layout.activate()
    assert not window._rail.isHidden()
    assert len({c.x() for c in window._cards.values() if not c.isHidden()}) >= 2
    window.resize(edge - 4, 600)
    qapp.processEvents()
    assert window._rail.isHidden()
    # 비좁아도 검색칸은 너무 짧아지지 않는다.
    window.resize(edge + 4, 600)
    qapp.processEvents()
    assert window._search_input.width() >= 220
    window._set_view_mode(layout="list")


def test_clicking_a_note_that_is_open_hides_it_and_clicking_again_shows_it(window, qapp):
    window.show()
    qapp.processEvents()
    sticky = window._stickies["a"]
    sticky.show()
    qapp.processEvents()
    assert not window._local.state.get_note_state("a").hidden and sticky.isVisible()
    window._on_card_clicked("a")                         # 펼쳐 있다: 숨긴다
    qapp.processEvents()
    assert window._local.state.get_note_state("a").hidden and not sticky.isVisible()
    window._on_card_clicked("a")                         # 숨어 있다: 다시 보인다
    qapp.processEvents()
    assert not window._local.state.get_note_state("a").hidden


def _shown(window, qapp, nid="a"):
    window.show()
    sticky = window._stickies[nid]
    sticky.show()
    qapp.processEvents()
    return sticky


def test_a_folded_note_comes_out_open_and_folds_back_on_the_second_click(window, qapp):
    sticky = _shown(window, qapp)
    sticky.fold()
    qapp.processEvents()
    assert sticky.is_folded()
    assert window._cards["a"]._fold_indicator.isVisibleTo(window._cards["a"])
    window._on_card_clicked("a")                         # 꺼낸다: 펼쳐서 앞으로
    qapp.processEvents()
    assert sticky.isVisible() and not sticky.is_folded()
    window._on_card_clicked("a")                         # 넣어 둔다: 다시 접힌다(숨기지 않는다)
    qapp.processEvents()
    assert sticky.is_folded() and sticky.isVisible()
    assert not window._local.state.get_note_state("a").hidden


def test_a_hidden_note_comes_out_and_goes_back_into_hiding(window, qapp):
    sticky = _shown(window, qapp)
    window._set_note_visibility("a", False)
    window._on_card_clicked("a")
    qapp.processEvents()
    assert sticky.isVisible() and not window._local.state.get_note_state("a").hidden
    window._on_card_clicked("a")
    qapp.processEvents()
    assert not sticky.isVisible() and window._local.state.get_note_state("a").hidden


def test_a_covered_note_is_brought_forward_instead_of_hidden(window, qapp, monkeypatch):
    """펼쳐 있어도 다른 창에 가려 안 보이면 숨기지 않고 앞으로 꺼낸다."""
    sticky = _shown(window, qapp)
    flashes = []
    monkeypatch.setattr(StickyNoteWidget, "in_view", lambda self, share=0.6: False)
    monkeypatch.setattr(StickyNoteWidget, "flash", lambda self: flashes.append(self.note_id))
    window._on_card_clicked("a")
    qapp.processEvents()
    assert sticky.isVisible() and flashes == ["a"]
    monkeypatch.setattr(StickyNoteWidget, "in_view", lambda self, share=0.6: True)
    window._on_card_clicked("a")                         # 이제 눈앞에 있다: 넣어 둔다
    qapp.processEvents()
    assert not sticky.isVisible() and window._local.state.get_note_state("a").hidden


def test_a_note_in_a_closed_folder_comes_out_alone_and_goes_back_in(window, qapp):
    from app.local_state import FolderIconState
    sticky = _shown(window, qapp, "b")
    window._local.state.folder_icons["f1"] = FolderIconState(x=0, y=0, opened=False)
    window._sync_note_shown("b")
    qapp.processEvents()
    assert not sticky.isVisible()
    window.toggle_note_out("b")                          # 폴더 창의 카드도 같은 길
    qapp.processEvents()
    assert sticky.isVisible() and window._folder_closed("f1")
    window.toggle_note_out("b")
    qapp.processEvents()
    assert not sticky.isVisible() and not window._local.state.get_note_state("b").hidden


def test_folding_by_hand_forgets_how_the_note_was_taken_out(window, qapp):
    sticky = _shown(window, qapp)
    window._set_note_visibility("a", False)
    window._on_card_clicked("a")
    qapp.processEvents()
    assert "a" in window._taken_out
    sticky.fold()                                        # 메모에서 직접 접었다
    qapp.processEvents()
    assert "a" not in window._taken_out


def test_the_flash_ring_shows_and_goes_away(qapp):
    from app.ui.sticky_note import _FlashRing
    host = QWidget()
    host.resize(200, 120)
    ring = _FlashRing(host)
    ring.setGeometry(host.rect())
    ring.level = 1.0
    ring.show()
    assert not ring.grab().isNull()
    host.deleteLater()


def test_the_more_menu_is_snug_and_starts_with_showing_or_hiding(qapp):
    card = NoteCard(note("a"), False)
    card.set_folder_context([("f1", "업무")])
    menu = card.build_menu()
    texts = [a.text() for a in menu.actions()]
    assert texts[0] == "바탕화면에 보이기"
    # 하위 메뉴가 build_menu 를 벗어난 뒤에도 남아 있다.
    move = next(a.menu() for a in menu.actions() if a.menu() is not None)
    assert [a.text() for a in move.actions() if not a.isSeparator()] == ["미분류", "업무"]
    from app.ui import note_card
    assert "min-width: 0px" in note_card._CARD_MENU_QSS          # 앱 메뉴의 최소 폭을 끈다
    assert "right-arrow { right: 12px; }" in note_card._CARD_MENU_QSS
    card.close()


def test_switching_the_cell_size_keeps_every_card_the_same_type_size(window, qapp):
    """크기를 바꾸면 처음부터 모든 카드의 글자가 같은 크기다(창 크기를 바꿨다 되돌려야
    맞던 것: 앞 크기의 배율이 남아 있었다)."""
    for nid in "cdefgh":
        window._client._dispatch("note_created", {"note": note(nid, f"<p>메모 {nid}</p>")})
    window.show()
    window._set_view_mode(layout="grid")
    for density in (VIEW_STANDARD, "detailed", VIEW_COMPACT, "detailed"):
        window._set_view_mode(density=density)
        qapp.processEvents()
        sizes = {(card._title.font().pixelSize(), round(card._scale, 2))
                 for card in window._cards.values() if card.isVisible()}
        assert len(sizes) == 1, (density, sizes)


def test_a_card_resized_before_its_new_size_arrives_still_scales_right(qapp):
    """크게로 바꿀 때 배치가 먼저 카드를 새 칸 폭으로 늘리고(아직 앞 크기라 배율이 크게
    나온다) 그다음 크기가 바뀌면, 그 폭과 새 크기의 기준 칸으로 배율을 다시 잰다."""
    from app.ui.note_card import GRID_CELL, VIEW_COMPACT, VIEW_DETAILED, VIEW_GRID, NoteCard
    card = NoteCard(note("y"), True)
    card.set_view(VIEW_COMPACT, VIEW_GRID)
    card.set_square(True)
    card.show()
    card.resize(256, 256)                       # 아직 '작게' 인 채로 '크게' 칸 폭이 왔다
    qapp.processEvents()
    card.set_view(VIEW_DETAILED, VIEW_GRID)
    assert abs(card._scale - 256 / GRID_CELL[VIEW_DETAILED]) < 0.01
    assert card._title.font().pixelSize() == round(15 * 256 / GRID_CELL[VIEW_DETAILED])
    card.close()


def test_grid_card_time_sits_as_far_from_the_right_as_the_marks_from_the_left(qapp):
    """격자 카드 머리 줄: 시각 글자의 끝과 카드 오른쪽 사이가 왼쪽 그림과 카드 왼쪽 사이와
    같다(작게, 보통, 크게). 짧은 시각('11:04')도 오른쪽에 붙는다."""
    from app.ui.note_card import GRID_CELL, VIEW_GRID, NoteCard
    for density, side in GRID_CELL.items():
        card = NoteCard(note("z"), False)
        card.set_view(density, VIEW_GRID)
        card.set_square(True)
        card.resize(side, side)
        card.show()
        qapp.processEvents()
        card._hover = card._kb_focus = False
        card._refresh_actions()
        card._fit_head()
        qapp.processEvents()
        root = card._root.contentsMargins()
        assert root.left() == root.right()
        assert card._timestamp.alignment() & Qt.AlignRight
        assert card._timestamp.minimumWidth() <= card._timestamp.fontMetrics().horizontalAdvance(
            card._timestamp.text()) + 4
        card.close()


def test_every_layout_pass_corrects_a_stale_card_scale(qapp):
    """배율이 어떤 까닭으로 어긋나 있어도(앞의 폭으로 잰 배율), 배치가 칸을 다시 놓으면
    바로잡힌다. 칸의 자리와 크기가 그대로라 크기 바뀜 알림이 오지 않아도 그렇다."""
    from PySide6.QtWidgets import QWidget
    from app.ui.note_card import GRID_CELL, VIEW_GRID, CardFlowLayout, NoteCard
    host = QWidget()
    layout = CardFlowLayout(host, spacing=10)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.set_square(GRID_CELL[VIEW_STANDARD])
    card = NoteCard(note("x", "<p>주간 회의 메모</p>"), True)
    card.set_square(True)
    card.set_view(VIEW_STANDARD, VIEW_GRID)
    layout.addWidget(card)
    host.resize(GRID_CELL[VIEW_STANDARD], 400)
    host.show()
    qapp.processEvents()
    assert card.width() == GRID_CELL[VIEW_STANDARD] and card._title.font().pixelSize() == 14
    card._scale = 3.1                       # 목록 폭(525)으로 잰 배율이 남은 모습
    card._apply_view_metrics()
    assert card._title.font().pixelSize() > 40
    layout.invalidate()
    layout.activate()
    qapp.processEvents()
    assert abs(card._scale - 1.0) < 0.01 and card._title.font().pixelSize() == 14
    host.close()


def test_a_layout_pass_inside_the_metrics_update_is_not_overwritten(qapp):
    """목록에서 격자로 바꿀 때 카드가 제목을 보이는 순간(setVisible) Qt 가 바깥 배치를
    그 자리에서 돌려 칸 폭을 알린다. 그때 바로잡은 글자를 바깥의 적용이 목록 폭으로 잰
    큰 글자로 덮어쓰지 않는다(보통에서 '주간 회의 메모' 만 44px 이던 것)."""
    from app.ui.note_card import GRID_CELL, VIEW_GRID, VIEW_LIST, NoteCard
    card = NoteCard(note("m", "<p>주간 회의 메모</p>"), True)
    card.set_view(VIEW_STANDARD, VIEW_LIST)
    card.resize(526, 60)                       # 목록 폭
    card.show()
    qapp.processEvents()
    real = card._title.setVisible

    def show_and_layout(on):
        real(on)
        if on:
            card.set_cell(GRID_CELL[VIEW_STANDARD])    # 배치가 끼어든다
    card._title.setVisible = show_and_layout
    card.set_square(True)
    card.set_view(VIEW_STANDARD, VIEW_GRID)
    assert card._title.font().pixelSize() == 14 and abs(card._scale - 1.0) < 0.01
    card.close()
