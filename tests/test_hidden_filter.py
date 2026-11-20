"""목록 머리줄의 메모 수 단추: 누를 때마다 전체 → 표시 → 숨김(offscreen).

숨긴 메모는 바탕화면에 없고, 목록에서는 첫 줄 제목으로만 찾아야 한다. 어떤 메모가
숨겨져 있는지 한눈에 보이지 않아, 있는 메모를 사라진 것으로 알기 쉬웠다."""
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
from app.ui.main_window import (  # noqa: E402
    FOLDER_NONE,
    LIST_ALL,
    LIST_HIDDEN,
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


def test_button_starts_with_every_note(window):
    btn = window._count_btn
    assert not btn.isHidden()
    assert btn.text() == "전체 5개"
    assert window._list_mode == LIST_ALL
    assert shown(window) == {"a", "b", "c", "d", "e"}


def test_each_click_moves_to_the_next_list(window):
    btn = window._count_btn
    btn.click()                                   # 바탕화면에 표시한 메모
    assert window._list_mode == LIST_SHOWN
    assert btn.text() == "표시 3개" and shown(window) == {"a", "c", "e"}
    btn.click()                                   # 숨긴 메모
    assert window._list_mode == LIST_HIDDEN
    assert btn.text() == "숨김 2개" and shown(window) == {"b", "d"}
    btn.click()                                   # 다시 전체
    assert window._list_mode == LIST_ALL
    assert btn.text() == "전체 5개" and shown(window) == {"a", "b", "c", "d", "e"}


def test_a_list_with_no_notes_is_skipped(window):
    # 숨긴 메모가 없으면 전체와 표시만 오간다(빈 목록에 멈추지 않는다).
    window._toggle_visibility_from_card("b")
    window._toggle_visibility_from_card("d")
    btn = window._count_btn
    btn.click()
    assert btn.text() == "표시 5개"
    btn.click()
    assert btn.text() == "전체 5개"

    # 모두 숨겼으면 전체와 숨김만 오간다.
    for nid in "abcde":
        window._toggle_visibility_from_card(nid)
    btn.click()
    assert btn.text() == "숨김 5개"
    btn.click()
    assert btn.text() == "전체 5개"


def test_showing_a_note_takes_it_off_the_hidden_list(qapp, window):
    window._count_btn.click()
    window._count_btn.click()
    window._toggle_visibility_from_card("b")         # 바탕화면에 보이기
    assert not window.store.state.get_note_state("b").hidden
    assert window._stickies["b"].isVisible()
    assert shown(window) == {"d"}
    assert window._count_btn.text() == "숨김 1개"

    # 마지막 하나까지 보이면 목록은 전체로 돌아온다(빈 목록으로 남지 않는다).
    window._toggle_visibility_from_card("d")
    assert window._list_mode == LIST_ALL
    assert window._count_btn.text() == "전체 5개"
    assert shown(window) == {"a", "b", "c", "d", "e"}


def test_hiding_a_note_moves_it_between_the_lists(window):
    window._count_btn.click()                         # 표시
    window._toggle_visibility_from_card("e")          # 바탕화면에서 숨기기
    assert window._count_btn.text() == "표시 2개" and shown(window) == {"a", "c"}
    window._count_btn.click()                         # 숨김
    assert window._count_btn.text() == "숨김 3개" and shown(window) == {"b", "d", "e"}


def test_count_follows_the_folder_and_the_search(window):
    window._count_btn.click()
    window._count_btn.click()                         # 숨김
    window._select_folder("f1")
    assert window._count_btn.text() == "숨김 1개"
    assert shown(window) == {"b"}

    # 숨긴 메모가 없는 폴더로 가면 전체로 돌아간다(빈 목록으로 남지 않는다).
    window._toggle_visibility_from_card("d")
    window._select_folder(FOLDER_NONE)
    assert window._list_mode == LIST_ALL
    assert window._count_btn.text() == "전체 3개"
    assert shown(window) == {"a", "d", "e"}

    # 수는 지금 목록에 보이는 메모 수다(검색어에 맞는 것만).
    window._select_folder("f1")
    assert window._count_btn.text() == "전체 2개"
    window._search_input.setText("해체신고")
    assert window._count_btn.text() == "전체 1개" and shown(window) == {"b"}
    window._count_btn.click()                         # 표시: 검색에 맞는 것이 없다
    assert window._count_btn.text() == "표시 0개" and shown(window) == set()
    window._count_btn.click()                         # 숨김
    assert window._count_btn.text() == "숨김 1개" and shown(window) == {"b"}


def test_a_note_made_by_someone_else_arrives_hidden_and_is_counted(window):
    window._client._dispatch("note_created", {"note": note(
        "z", "<p>민수의 메모</p>", by=OTHER)})
    assert window.store.state.get_note_state("z").hidden
    assert window._count_btn.text() == "전체 6개"
    window._count_btn.click()
    window._count_btn.click()
    assert window._count_btn.text() == "숨김 3개"


def test_deleting_a_hidden_note_lowers_the_count(window):
    window._count_btn.click()
    window._count_btn.click()
    window._client._dispatch("note_deleted", {"id": "b"})
    assert window._count_btn.text() == "숨김 1개" and shown(window) == {"d"}


def test_button_is_hidden_while_there_are_no_notes(window):
    for nid in "abcde":
        window._client._dispatch("note_deleted", {"id": nid})
    assert window._count_btn.isHidden()


def test_labels_exist_in_every_language(window):
    for lang in ("en", "ja", "ko"):
        i18n.set_language(lang)
        window._refresh_count()
        assert "5" in window._count_btn.text()
        assert window._count_btn.text() == i18n.t("main.count_all", count=5)
