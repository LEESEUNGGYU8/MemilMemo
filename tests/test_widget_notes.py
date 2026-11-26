"""메모 안 위젯을 앱에서: 공유 PC 의 표와 widget_put/widget_op, 메모에 넣기, 그림 위의 위젯."""
from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMenu  # noqa: E402

from app import i18n, widgets  # noqa: E402
from app.database import SqliteRepository  # noqa: E402
from app.local_state import LocalStateStore  # noqa: E402
from app.network.client_base import ClientBase  # noqa: E402
from app.network.host_client import HostLocalClient  # noqa: E402
from app.network.host_server import HostServer, _Session  # noqa: E402
from app.protocol import MSG_WIDGET_OP, MSG_WIDGET_PUT, FrameParser, make  # noqa: E402
from app.settings import AppSettings  # noqa: E402
from app.sync.history_tracker import HistoryTracker  # noqa: E402
from app.sync.lock_manager import LockManager  # noqa: E402
from app.ui import theme  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.sticky_note import StickyNoteWidget  # noqa: E402

HOST, GUEST = "민수", "지우"


@pytest.fixture(scope="module", autouse=True)
def qapp():
    app = QApplication.instance() or QApplication([])
    i18n.set_language("ko")
    theme.apply_theme(app, "")
    yield app


class FakeSocket:
    def __init__(self):
        self.parser = FrameParser()
        self.messages: list = []

    def write(self, data: bytes) -> None:
        self.messages.extend(self.parser.feed(bytes(data)))

    def disconnectFromHost(self) -> None:
        pass

    def of(self, kind: str) -> list[dict]:
        return [m.data for m in self.messages if m.type == kind]


@pytest.fixture
def host(tmp_path):
    repo = SqliteRepository(tmp_path / "notes.sqlite3")
    server = HostServer(repo, LockManager(idle_timeout_ms=60_000),
                        HistoryTracker(repo), host_nickname=HOST)
    client = HostLocalClient(server, HOST)
    seen = []
    server.attach_local_host()
    server.set_local_subscriber(lambda kind, data: seen.append((kind, data)))
    client._send_hello()
    sock = FakeSocket()
    session = _Session(socket=sock, parser=FrameParser(), nickname=GUEST, hello_received=True)
    server._sessions[GUEST] = session
    client.create_note("#FFE066", 280, 260)
    nid = [d for k, d in seen if k == "note_created"][-1]["note"]["id"]
    yield server, client, repo, session, sock, nid
    repo.close()


def test_a_vote_is_kept_by_the_host_and_votes_reach_everyone(host):
    server, client, repo, session, sock, nid = host
    vote = widgets.get("vote")
    data = vote.new_data("누구든", question="점심", options=["국밥", "냉면"])
    wid = widgets.new_id()
    client.put_widget(nid, wid, data)
    stored_note, stored = repo.get_widget(wid)
    assert stored_note == nid and stored["owner"] == HOST          # 만든 사람은 보낸 사람
    assert sock.of("widget_changed")[-1]["widget_id"] == wid
    option = stored["options"][0]["id"]
    server._handle(session, make(MSG_WIDGET_OP, widget_id=wid,
                                 op={"op": "vote", "option": option}))
    changed = sock.of("widget_changed")[-1]
    assert changed["by"] == GUEST and GUEST in str(changed["data"])
    server._handle(session, make(MSG_WIDGET_OP, widget_id=wid, op={"op": "close"}))
    assert sock.of("widget_error")[-1] == {"widget_id": wid, "error": "not_allowed"}
    server._handle(session, make(MSG_WIDGET_PUT, note_id=nid, widget_id=wid, data=data))
    assert sock.of("widget_error")[-1]["error"] == "exists"
    server._send_welcome(session)
    assert wid in sock.of("welcome")[-1]["widgets"]


def test_private_notes_keep_their_widgets_to_the_owner(host):
    server, client, repo, session, sock, _nid = host
    client.create_note("#FFE066", 280, 260, private_owner=HOST)
    mine = [n for n in repo.list_notes() if n.private_owner == HOST][0].id
    wid = widgets.new_id()
    server._handle(session, make(MSG_WIDGET_PUT, note_id=mine, widget_id=wid,
                                 data=widgets.get("dday").new_data(GUEST)))
    assert sock.of("widget_error")[-1]["error"] == "not_allowed"
    assert repo.get_widget(wid) is None


def note(nid):
    return {"id": nid, "version": 1, "color": "#FFE066", "width": 320, "height": 300,
            "content": "<p>점심</p>", "created_by": GUEST, "updated_by": GUEST,
            "updated_at": int(time.time() * 1000)}


class Client(ClientBase):
    def __init__(self):
        super().__init__(GUEST)
        self.sent = []

    def put_widget(self, note_id, widget_id, data):
        self.sent.append(("put", note_id, widget_id))

    def widget_op(self, widget_id, op):
        self.sent.append(("op", widget_id, op))


def open_window(tmp_path, name, welcome_extra):
    client = Client()
    settings = AppSettings(fold_animation=False)
    settings.save = lambda path=None: None
    win = MainWindow(client=client, local_state=LocalStateStore(tmp_path / f"{name}.json"),
                     role="guest", settings=settings)
    client.connectionChanged.emit(True)
    client._dispatch("welcome", {"users": [GUEST, HOST], "host": HOST, "locks": {},
                                 "heartbeat_ms": 3000, "trash_days": 30, "reminders": {},
                                 "folders": [], "notes": [note("a")], **welcome_extra})
    return win


def close_window(win):
    win._force_quit = True
    win._teardown_stickies()
    win.close()
    win.deleteLater()


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setattr(StickyNoteWidget, "bring_to_front",
                        lambda self, *, start_editing=False: None)
    win = open_window(tmp_path, "w", {"widgets": {}})
    yield win
    close_window(win)


def _editing(window):
    sticky = window._stickies["a"]
    sticky.resize(360, 420)
    sticky.show()
    sticky.enter_edit_mode()
    return sticky


def test_a_widget_goes_into_a_note_and_its_presses_go_to_the_host(window):
    sticky = _editing(window)
    wid = sticky.insert_widget("vote")
    assert wid and ("put", "a", wid) in window._client.sent
    assert widgets.ids_in_html(sticky._current_content()) == [wid]
    sticky._sync_widget_views()
    block = sticky.widget_views()[wid]
    assert block.setup_open()                         # 처음에는 설정 칸이 열린다
    # 공유 PC 가 나눈 데이터가 오면 위젯이 바뀌고, 누르면 동작이 공유 PC 로 간다.
    data = widgets.get("vote").new_data(GUEST, question="점심", options=["국밥", "냉면"])
    window._client._dispatch("widget_changed", {"note_id": "a", "widget_id": wid,
                                                "data": data, "by": GUEST})
    option = block.data()["options"][0]["id"]
    hit = block.editor.find_hit(op="vote", option=option)
    assert hit is not None and block.editor.press(*hit.center())
    assert window._client.sent[-1] == ("op", wid, {"op": "vote", "option": option})


def test_the_setup_panel_sends_one_configure_and_closes(window):
    sticky = _editing(window)
    wid = sticky.insert_widget("vote")
    block = sticky.widget_views()[wid]
    form = block.form
    form.question.setText("오늘 점심")
    form.options.set_values(["국밥", "냉면", "돈가스"])
    form.multi.setChecked(True)
    block._done.click()
    op = window._client.sent[-1]
    assert op[0] == "op" and op[2] == {"op": "configure", "question": "오늘 점심",
                                       "options": ["국밥", "냉면", "돈가스"], "multi": True,
                                       "anonymous": False}
    assert not block.setup_open()


def test_cancelling_the_first_setup_takes_the_widget_out(window):
    sticky = _editing(window)
    wid = sticky.insert_widget("dday")
    block = sticky.widget_views()[wid]
    block._cancel.click()
    assert widgets.ids_in_html(sticky._current_content()) == []
    assert wid not in sticky.widget_views()


def test_size_menu_and_edge_drag_send_a_size(window, qapp):
    sticky = _editing(window)
    wid = sticky.insert_widget("omok")                # 정할 것이 없는 종류는 바로 쓴다
    sticky._sync_widget_views()
    sticky._sync_widget_views()
    block = sticky.widget_views()[wid]
    assert not block.setup_open()
    menu = block.build_menu()
    size = next(a.menu() for a in menu.actions() if a.menu() is not None)
    next(a for a in size.actions() if a.text() == "작게").trigger()
    assert window._client.sent[-1] == ("op", wid, {"op": "set_size", "w": 240})
    # 공유 PC 가 나눈 폭으로 그림이 다시 그려진다.
    data = dict(block.data(), w=240)
    window._client._dispatch("widget_changed", {"note_id": "a", "widget_id": wid,
                                                "data": data, "by": GUEST})
    sticky._sync_widget_views()
    sticky._sync_widget_views()
    assert block.width() == 240


def test_the_widget_button_opens_a_gallery_without_games_when_off(window):
    sticky = _editing(window)
    assert sticky._widget_btn.isEnabled()
    assert "omok" in sticky.widget_kinds()
    sticky.set_widget_env(True, False)
    assert "omok" not in sticky.widget_kinds() and "vote" in sticky.widget_kinds()


def test_a_widget_note_is_not_an_image_note(window):
    from app.ui.note_card import note_title_and_lines
    sticky = _editing(window)
    sticky._text.clear()
    wid = sticky.insert_widget("vote")
    data = widgets.get("vote").new_data(GUEST, question="오늘 점심", options=["국밥"])
    sticky.set_widget_data(wid, data)
    sticky._sync_widget_views()
    sticky._sync_widget_views()
    html = sticky._current_content()
    title, _lines, has_image = note_title_and_lines(html)
    assert title == "투표: 오늘 점심" and has_image is False


def test_deleting_a_widget_in_read_mode_waits_for_editing(window):
    sticky = _editing(window)
    wid = sticky.insert_widget("omok")
    sticky._commit_and_release()
    requested = []
    sticky.requestLock.connect(requested.append)
    sticky.delete_widget(wid)
    assert requested == ["a"] and widgets.ids_in_html(sticky._current_content()) == [wid]
    sticky.enter_edit_mode()
    assert widgets.ids_in_html(sticky._current_content()) == []


def test_an_old_host_keeps_widgets_as_pictures(tmp_path, monkeypatch):
    monkeypatch.setattr(StickyNoteWidget, "bring_to_front",
                        lambda self, *, start_editing=False: None)
    win = open_window(tmp_path, "old", {})
    sticky = win._stickies["a"]
    sticky.enter_edit_mode()
    assert sticky.insert_widget("vote") is None             # 넣을 수 없다
    assert not sticky._widget_btn.isEnabled()
    close_window(win)


def test_the_widget_button_click_opens_the_gallery(window, monkeypatch):
    """서식 막대의 위젯 단추를 누르면 고르는 판이 열린다(누름 값이 자리로 가지 않는다)."""
    from app.ui import edit_menu
    sticky = _editing(window)
    shown = []
    monkeypatch.setattr(edit_menu, "run_menu", lambda menu, at: shown.append(at))
    sticky._widget_btn.click()
    assert len(shown) == 1 and sticky._widget_gallery is not None


def test_a_widget_lives_in_the_text_as_a_widget_not_an_image(window):
    """본문 안 위젯은 그림이 아니다(그림 보기, 그림 메뉴, 그림 맞춤이 닿지 않는다). 저장할
    때만 그림으로 적고, 다시 불러오면 위젯 칸으로 돌아온다."""
    from app.ui import widget_object as wobj
    sticky = _editing(window)
    wid = sticky.insert_widget("omok")
    sticky._sync_widget_views()
    doc = sticky._text.document()

    def images():
        out = []
        block = doc.begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                if it.fragment().charFormat().isImageFormat():
                    out.append(it.fragment().charFormat().toImageFormat().name())
                it += 1
            block = block.next()
        return out

    assert images() == [] and [w for w, _p, _f in wobj.find_objects(doc)] == [wid]
    html = sticky._current_content()
    assert widgets.ids_in_html(html) == [wid]
    sticky._apply_content_to_view(html)
    assert images() == [] and [w for w, _p, _f in wobj.find_objects(doc)] == [wid]
    # 같은 모습이면 저장 내용이 같다(위젯 그림을 다시 그려 괜히 바뀌지 않는다).
    assert sticky._current_content() == html


def test_resizing_a_widget_does_not_touch_the_text(window):
    """크기가 바뀌어도 본문은 그대로다(되돌리기 기록과 저장 내용이 바뀌지 않는다)."""
    sticky = _editing(window)
    wid = sticky.insert_widget("omok")
    sticky._sync_widget_views()
    doc = sticky._text.document()
    before = (doc.availableUndoSteps(), doc.toPlainText())
    changed = []
    doc.contentsChanged.connect(lambda: changed.append(1))
    block = sticky.widget_views()[wid]
    data = dict(block.data(), w=240)
    window._client._dispatch("widget_changed", {"note_id": "a", "widget_id": wid,
                                                "data": data, "by": GUEST})
    sticky._sync_widget_views()
    sticky._sync_widget_views()
    assert block.width() == 240 and sticky._widget_sizes[wid][0] == 240
    assert (doc.availableUndoSteps(), doc.toPlainText()) == before


def test_a_new_omok_game_keeps_the_widget_size(window):
    sticky = _editing(window)
    wid = sticky.insert_widget("omok")
    sticky._sync_widget_views()
    block = sticky.widget_views()[wid]
    kind = widgets.get("omok")
    data = block.data()
    sizes = set()
    for step in range(3):
        data = kind.apply_op(data, {"op": "move", "x": 7 + step, "y": 7}, GUEST, 0)[0]
        sticky.set_widget_data(wid, data)
        sticky._sync_widget_views()
        sizes.add(sticky._widget_sizes[wid])
    data = kind.apply_op(data, {"op": "new_game"}, GUEST, 0)[0]
    sticky.set_widget_data(wid, data)
    sticky._sync_widget_views()
    sizes.add(sticky._widget_sizes[wid])
    assert len(sizes) == 1


def test_a_widget_without_data_shows_its_saved_picture(window):
    """데이터를 아직 받지 못한 위젯은 칸에 저장된 그림을 그린다(빈 자리가 아니다)."""
    from app.ui import widget_object as wobj
    sticky = _editing(window)
    wid = sticky.insert_widget("omok")
    sticky._sync_widget_views()
    html = sticky._current_content()
    sticky._widget_data.pop(wid)
    sticky._apply_content_to_view(html)
    sticky._sync_widget_views()
    assert wid not in sticky.widget_views()
    (_w, _pos, fmt), = wobj.find_objects(sticky._text.document())
    assert wobj.stored_size(fmt)[1] > 100
    assert wobj.picture_of(sticky._text.document(), fmt.property(wobj.P_URL)) is not None


def test_cut_and_paste_moves_a_widget_without_copying_it(window):
    """위젯이 든 글을 잘라 붙이면 위젯이 그대로 옮겨 가고, 복사해 한 번 더 붙이면 두 번째는
    빠진다(한 위젯이 두 자리에 있지 않다). 위젯 칸 뒤에 친 글은 글자다."""
    from PySide6.QtGui import QTextCursor
    from app.ui import widget_object as wobj
    sticky = _editing(window)
    sticky._text.clear()
    sticky._text.insertPlainText("앞")
    wid = sticky.insert_widget("omok")
    doc = sticky._text.document()
    cursor = QTextCursor(doc)
    cursor.setPosition(0)
    cursor.movePosition(QTextCursor.End, QTextCursor.KeepAnchor)
    sticky._text.setTextCursor(cursor)
    mime = sticky._text.createMimeDataFromSelection()
    sticky._text.textCursor().removeSelectedText()
    assert wobj.find_objects(doc) == []
    sticky._text.insertFromMimeData(mime)
    assert [w for w, _p, _f in wobj.find_objects(doc)] == [wid]
    sticky._text.insertFromMimeData(mime)                 # 같은 것을 또 붙인다
    assert [w for w, _p, _f in wobj.find_objects(doc)] == [wid]
    assert "￼￼" not in doc.toPlainText()
    end = QTextCursor(doc)
    end.setPosition(wobj.find_objects(doc)[0][1] + 1)
    sticky._text.setTextCursor(end)
    sticky._text.insertPlainText("뒤")
    assert len(wobj.find_objects(doc)) == 1


def test_a_fitted_widget_follows_the_note_width(window, qapp):
    sticky = _editing(window)
    wid = sticky.insert_widget("omok")
    sticky._sync_widget_views()
    before = sticky._widget_sizes[wid][0]
    sticky.resize(sticky.width() + 80, sticky.height())
    for _ in range(5):
        qapp.processEvents()
    sticky._widget_timer.timeout.emit()
    assert sticky._widget_sizes[wid][0] > before


def test_tab_and_enter_stay_inside_the_widget_setup(window, qapp):
    """설정 칸에서 Tab 은 위젯 안의 다음 칸으로, Enter 는 다음 칸(마지막이면 완료)으로 간다.
    메모 본문에는 탭이나 줄바꿈이 들어가지 않는다."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    sticky = _editing(window)
    sticky.activateWindow = lambda: None
    wid = sticky.insert_widget("vote")
    block = sticky.widget_views()[wid]
    form = block.form
    text_before = sticky._text.toPlainText()
    form.question.setFocus()
    qapp.processEvents()
    QTest.keyClick(form.question, Qt.Key_Tab)
    rows = form.options.rows()
    assert QApplication.focusWidget() is rows[0]
    QTest.keyClick(rows[0], Qt.Key_Tab, Qt.ShiftModifier)
    assert QApplication.focusWidget() is form.question
    form.question.insert("점심")          # QTest.keyClicks 는 한글을 치지 못한다
    QTest.keyClick(form.question, Qt.Key_Return)
    assert QApplication.focusWidget() is rows[0]
    rows[0].insert("국밥")
    QTest.keyClick(rows[0], Qt.Key_Return)              # 다음 줄로
    assert QApplication.focusWidget() is rows[1]
    rows[1].insert("냉면")
    QTest.keyClick(rows[1], Qt.Key_Return)              # 마지막 줄이면 새 줄
    third = form.options.rows()[2]
    assert QApplication.focusWidget() is third
    QTest.keyClick(third, Qt.Key_Return)                # 빈 마지막 줄은 목록을 나간다
    assert len(form.options.rows()) == 2
    assert QApplication.focusWidget() is form.multi
    QTest.keyClick(form.multi, Qt.Key_Return)
    assert QApplication.focusWidget() is form.anonymous
    QTest.keyClick(form.anonymous, Qt.Key_Return)       # 더 없으면 만든다
    assert not block.setup_open()
    assert window._client.sent[-1][2]["options"] == ["국밥", "냉면"]
    assert sticky._text.toPlainText() == text_before


def test_word_wrap_is_chosen_per_note_with_a_default_in_settings(window):
    """자동 줄바꿈은 더 보기 메뉴에서 메모마다 끄고 켜며(이 PC 에 기억), 고른 적이 없는
    메모는 설정의 기본값을 따른다."""
    sticky = _editing(window)
    assert sticky.word_wrap()
    assert sticky._text.lineWrapMode() == sticky._text.LineWrapMode.WidgetWidth
    sticky.wrapToggled.emit("a", False)
    assert not sticky.word_wrap() and window._local.state.wrap_notes == {"a": False}
    assert sticky._text.lineWrapMode() == sticky._text.LineWrapMode.NoWrap
    sticky.wrapToggled.emit("a", True)
    assert window._local.state.wrap_notes == {}           # 기본값과 같으면 적지 않는다
    window._settings.word_wrap = False
    assert window._note_wraps("a") is False
    window._local.state.wrap_notes["a"] = True
    assert window._note_wraps("a") is True


def test_the_grip_and_the_edge_resize_widgets_even_in_setup(window, qapp):
    """오른쪽 아래 손잡이로 폭을(그래프는 높이도), 설정 칸에서도 오른쪽 가장자리로 폭을 바꾼다."""
    sticky = _editing(window)
    wid = sticky.insert_widget("graph")
    sticky._sync_widget_views()
    block = sticky.widget_views()[wid]
    assert block.setup_open()
    block.begin_resize()
    block.resize_to(250, 500)                       # 설정 칸에서는 높이를 고르지 않는다
    block.end_resize()
    assert window._client.sent[-1] == ("op", wid, {"op": "set_size", "w": 250})
    block._close_setup()
    block.begin_resize()
    block.resize_to(300, 330)
    sticky._sync_widget_views()
    assert sticky._widget_sizes[wid][1] == 330      # 끄는 동안 그래프 높이가 따라온다
    block.end_resize()
    assert window._client.sent[-1] == ("op", wid, {"op": "set_size", "w": 300, "h": 330})
    graph = widgets.get("graph")
    data, err = graph.apply_op(block.data(), {"op": "set_size", "w": 300, "h": 330}, GUEST, 0)
    assert err is None and data["h"] == 330
    vote = widgets.get("vote")
    vdata, err = vote.apply_op(vote.new_data(GUEST), {"op": "set_size", "w": 300, "h": 330},
                               GUEST, 0)
    assert err is None and vdata["h"] == 0          # 높이는 그래프만 고른다


def test_a_widget_moves_with_the_text_right_away(window, qapp):
    """위젯 위에 줄을 더하면 위젯이 그 자리에서 바로 내려간다(타이머를 기다리지 않는다)."""
    from PySide6.QtGui import QTextCursor
    from app.ui import widget_object as wobj
    sticky = _editing(window)
    wid = sticky.insert_widget("omok")
    sticky._sync_widget_views()
    block = sticky.widget_views()[wid]
    top = block.y()
    sticky._widget_timer.stop()
    cursor = QTextCursor(sticky._text.document())
    cursor.setPosition(0)
    cursor.insertText("한 줄\n두 줄\n")
    pos = wobj.find_objects(sticky._text.document())[0][1]
    want = sticky._widget_object_rect(pos, *sticky._widget_sizes[wid])
    assert block.y() == want.y() > top
