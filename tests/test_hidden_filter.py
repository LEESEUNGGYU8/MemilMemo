"""메인 창 왼쪽 옆줄의 거르기와 폴더: 전체 메모, 표시, 숨김, 알림, 폴더(offscreen).

숨긴 메모는 바탕화면에 없고, 목록에서는 첫 줄 제목으로만 찾아야 한다. 옆줄에
늘 수를 보여 어떤 메모가 숨겨져 있는지 한눈에 알게 한다. 좁은 창에서는 옆줄이
위의 펼침 칸 하나로 접힌다."""
from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app import i18n  # noqa: E402
from app.local_state import LocalStateStore  # noqa: E402
from app.network.client_base import ClientBase  # noqa: E402
from app.settings import AppSettings  # noqa: E402
from app.ui import theme  # noqa: E402
from app import reminders as rm  # noqa: E402
from app.ui import main_shell as shell  # noqa: E402
from app.ui.main_window import (  # noqa: E402
    FOLDER_NONE,
    LIST_ALL,
    LIST_HIDDEN,
    LIST_REMIND,
    LIST_SHOWN,
    MainWindow,
)
from app.ui.sticky_note import StickyNoteWidget  # noqa: E402

ME = "지우"
OTHER = "민수"


class FakeClient(ClientBase):
    pass


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    i18n.set_language("ko")
    theme.apply_theme(app, "")
    yield app


def note(nid: str, text: str, by: str = ME, **more) -> dict:
    return {"id": nid, "version": 1, "color": "#FFE066", "width": 300, "height": 240,
            "content": text, "created_by": by,
            "updated_at": int(time.time() * 1000), **more}


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(StickyNoteWidget, "bring_to_front",
                        lambda self, *, start_editing=False: None)
    store = LocalStateStore(tmp_path / "local_state.json")
    # 셋은 바탕화면에 떠 있고, 둘은 숨겨 둔 상태로 시작한다.
    for nid, hidden in (("a", False), ("b", True), ("c", False), ("d", True),
                        ("e", False)):
        store.state.set_note_state(nid, x=40, y=40, hidden=hidden)
    client = FakeClient(ME)
    win = MainWindow(client=client, local_state=store, role="guest",
                     settings=AppSettings())
    win.store = store
    client.connectionChanged.emit(True)
    client._dispatch("welcome", {
        "users": [ME, OTHER], "host": OTHER, "locks": {}, "heartbeat_ms": 3000,
        "folders": [{"id": "f1", "name": "업무", "color": "#BAFFC9",
                     "private_owner": None}],
        "notes": [note("a", "<p>주간 회의</p>"),
                  note("b", "<p>도시가스 해지</p><p>해체신고서 준비</p>",
                       folder_id="f1"),
                  note("c", "<p>견적 회신</p>", folder_id="f1"),
                  note("d", "<p>재고 실사</p>"),
                  note("e", "<p>택배 송장</p>")]})
    qapp.processEvents()
    yield win
    win._force_quit = True
    win._teardown_stickies()
    win.close()
    win.deleteLater()
    qapp.processEvents()


def shown(win) -> set[str]:
    return {nid for nid, card in win._cards.items() if not card.isHidden()}


def counts(win) -> dict[str, str]:
    return {k: b.count_text() for k, b in win._rail.places.items()}


def test_every_note_is_listed_first_and_the_rail_counts_them(window):
    assert window._list_mode == LIST_ALL and window._place == shell.PLACE_ALL
    assert window._place_title.text() == "전체 메모" and window._place_count.text() == "5개"
    assert counts(window) == {"all": "5", "shown": "3", "hidden": "2", "remind": "0"}
    assert window._rail.places["all"].isChecked()
    assert shown(window) == {"a", "b", "c", "d", "e"}


def test_each_filter_in_the_rail(window):
    window._rail.places["shown"].click()              # 바탕화면에 표시한 메모
    assert window._list_mode == LIST_SHOWN and window._place_title.text() == "표시"
    assert window._place_count.text() == "3개" and shown(window) == {"a", "c", "e"}
    assert window._rail.places["shown"].isChecked() and not window._rail.places["all"].isChecked()
    window._rail.places["hidden"].click()             # 숨긴 메모
    assert window._list_mode == LIST_HIDDEN
    assert window._place_count.text() == "2개" and shown(window) == {"b", "d"}
    window._rail.places["all"].click()
    assert window._list_mode == LIST_ALL and shown(window) == {"a", "b", "c", "d", "e"}


def test_an_empty_filter_says_so_and_offers_every_note(window):
    window._toggle_visibility_from_card("b")
    window._toggle_visibility_from_card("d")
    window._rail.places["hidden"].click()
    assert shown(window) == set() and not window._empty_panel.isHidden()
    assert window._empty_title.text() == "숨긴 메모가 없습니다"
    assert window._empty_action.text() == "전체 메모 보기"
    window._empty_action.click()
    assert window._place == shell.PLACE_ALL and shown(window) == {"a", "b", "c", "d", "e"}


def test_showing_a_note_takes_it_off_the_hidden_list(qapp, window):
    window._rail.places["hidden"].click()
    window._toggle_visibility_from_card("b")         # 바탕화면에 보이기
    assert not window.store.state.get_note_state("b").hidden
    assert window._stickies["b"].isVisible()
    assert shown(window) == {"d"} and window._place_count.text() == "1개"
    assert counts(window)["hidden"] == "1"
    # 마지막 하나까지 보여도 고른 곳에 머문다(비었다고 알린다).
    window._toggle_visibility_from_card("d")
    assert window._list_mode == LIST_HIDDEN and shown(window) == set()
    assert not window._empty_panel.isHidden()


def test_hiding_a_note_moves_it_between_the_lists(window):
    window._rail.places["shown"].click()
    window._toggle_visibility_from_card("e")          # 바탕화면에서 숨기기
    assert window._place_count.text() == "2개" and shown(window) == {"a", "c"}
    assert counts(window)["shown"] == "2" and counts(window)["hidden"] == "3"
    window._rail.places["hidden"].click()
    assert shown(window) == {"b", "d", "e"}


def test_folders_in_the_rail_show_their_notes_and_follow_the_search(window):
    folders = window._rail.folders
    assert folders["f1"].count_text() == "2" and folders[""].count_text() == "3"
    folders["f1"].click()
    assert window._place == shell.PLACE_FOLDER and window._place_title.text() == "업무"
    assert shown(window) == {"b", "c"} and folders["f1"].isChecked()
    window._select_folder(FOLDER_NONE)
    assert window._place_title.text() == "미분류" and shown(window) == {"a", "d", "e"}
    # 수는 지금 목록에 보이는 메모 수다(검색어에 맞는 것만).
    window._select_folder("f1")
    window._search_input.setText("해체신고")
    assert window._place_count.text() == "1개" and shown(window) == {"b"}
    # 거르기를 고르면 폴더는 풀린다.
    window._rail.places["shown"].click()
    assert window._place == shell.PLACE_SHOWN and not folders["f1"].isChecked()


def test_the_reminder_filter_lists_notes_with_reminders(window):
    st = window.store.state
    st.reminders["a"] = {"rule": rm.Rule(rm.DAILY, "2026-11-25", "09:00").to_dict(),
                         "set_at": 1}
    st.check_resets["c"] = {"rule": rm.Rule(rm.DAILY, "2026-11-25", "08:30").to_dict(),
                            "set_at": 1}
    window._refresh_schedule_chips()
    assert counts(window)["remind"] == "2"
    window._rail.places["remind"].click()
    assert window._list_mode == LIST_REMIND and shown(window) == {"a", "c"}
    assert window._cards["a"]._remind and window._cards["c"]._reset


def test_a_note_made_by_someone_else_arrives_hidden_and_is_counted(window):
    window._client._dispatch("note_created", {"note": note(
        "z", "<p>민수의 메모</p>", by=OTHER)})
    assert window.store.state.get_note_state("z").hidden
    assert counts(window)["all"] == "6" and counts(window)["hidden"] == "3"
    assert window._place_count.text() == "6개"


def test_deleting_a_hidden_note_lowers_the_count(window):
    window._rail.places["hidden"].click()
    window._client._dispatch("note_deleted", {"id": "b"})
    assert window._place_count.text() == "1개" and shown(window) == {"d"}


def test_a_narrow_window_folds_the_rail_into_one_menu(qapp, window):
    window.resize(780, 600)
    window._apply_narrow(False)
    assert not window._rail.isHidden() and window._narrow_bar.isHidden()
    assert window._search_input.parentWidget() is window._title_bar
    window._apply_narrow(True)
    assert window._rail.isHidden() and not window._narrow_bar.isHidden()
    assert window._search_input.parentWidget() is window._narrow_search
    menu = window._folder_selector_menu()
    labels = [a.text() or a.defaultWidget().accessibleName() if hasattr(a, "defaultWidget")
              else a.text() for a in menu.actions()]
    assert labels
    window._select_folder("f1")
    assert window._folder_chip.text() == "업무"
    window._apply_narrow(False)
    assert window._search_input.parentWidget() is window._title_bar


def test_labels_exist_in_every_language(window):
    try:
        for lang in ("en", "ja", "ko"):
            i18n.set_language(lang)
            window._refresh_count()
            assert "5" in window._place_count.text()
            assert window._place_count.text() == i18n.t("main.count_n", count=5)
            for key in ("all", "shown", "hidden", "remind"):
                assert not i18n.t(f"main.place_{key}").startswith("main.")
    finally:
        i18n.set_language("ko")
