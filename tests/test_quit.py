"""프로그램을 끝낼 때 메모가 숨김으로 바뀌지 않는다(offscreen).

Qt 는 종료 요청을 받으면 보이는 창을 하나씩 닫아 본다. 메모 창은 닫기를
'숨기기'로 받으므로, 채비 없이 종료가 시작되면 메모 하나가 숨김으로 바뀌고
종료는 취소된다. 1.0.1 까지 앱 안에서 업데이트하면 이 일이 일어났다."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app import i18n  # noqa: E402
from app.local_state import LocalStateStore  # noqa: E402
from app.network.client_base import ClientBase  # noqa: E402
from app.settings import AppSettings  # noqa: E402
from app.ui import theme  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.sticky_note import StickyNoteWidget  # noqa: E402

ME = "지우"


class FakeClient(ClientBase):
    def __init__(self, nickname: str = ME):
        super().__init__(nickname)
        self.stopped = 0

    def stop(self):
        self.stopped += 1


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    i18n.set_language("ko")
    theme.apply_theme(app, "")
    yield app


def _welcome(client: FakeClient, count: int = 3) -> list[str]:
    notes = [{"id": f"n{i}", "version": 1, "color": "#FFE066", "width": 280,
              "height": 240, "content": f"메모 {i}", "created_by": ME,
              "updated_at": time.time(), "private_owner": None,
              "folder_id": None}
             for i in range(count)]
    client._dispatch("welcome", {"notes": notes, "folders": [], "locks": {},
                                 "sessions": [], "history": []})
    return [n["id"] for n in notes]


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(StickyNoteWidget, "bring_to_front",
                        lambda self, *, start_editing=False: None)
    store = LocalStateStore(tmp_path / "local_state.json")
    win = MainWindow(client=FakeClient(), local_state=store, role="guest",
                     settings=AppSettings())
    win.store = store
    yield win
    win._force_quit = True
    win._teardown_stickies()
    win.close()
    win.deleteLater()
    qapp.processEvents()


def test_closing_a_note_window_hides_that_note(qapp, window):
    # 메모 창을 닫는 것은 숨기기다. 이 동작이 있어서 종료 채비가 필요하다.
    ids = _welcome(window._client)
    qapp.processEvents()
    assert all(window._stickies[i].isVisible() for i in ids)
    window._stickies[ids[0]].close()
    assert window.store.state.get_note_state(ids[0]).hidden
    assert not window.store.state.get_note_state(ids[1]).hidden


def test_prepare_quit_takes_the_notes_down_without_hiding_them(qapp, window):
    ids = _welcome(window._client)
    qapp.processEvents()
    stickies = [window._stickies[i] for i in ids]

    window.prepare_quit()
    assert window._stickies == {} and window._force_quit
    # 그 뒤에 Qt 가 창을 닫아 봐도 숨김으로 적히지 않는다.
    for w in stickies:
        w.close()
    assert [window.store.state.get_note_state(i).hidden for i in ids] == [
        False, False, False]
    # 여러 번 불러도 된다.
    window.prepare_quit()


def test_quit_app_goes_through_the_same_preparation(qapp, window, monkeypatch):
    ids = _welcome(window._client)
    qapp.processEvents()
    quits: list[int] = []
    monkeypatch.setattr(QApplication, "quit", staticmethod(lambda: quits.append(1)))
    window._quit_app()
    assert quits == [1] and window._client.stopped == 1
    assert window._stickies == {}
    assert not any(window.store.state.get_note_state(i).hidden for i in ids)


# 앱을 통째로 켜고, 채비 없이 QApplication.quit() 을 부른다(업데이트 창에서
# 설치를 누른 뒤 예전 코드가 하던 일). 끝나야 하고, 숨겨진 메모가 없어야 한다.
_WHOLE_APP = r'''
import faulthandler, json, os, socket, sys, tempfile
from pathlib import Path

faulthandler.dump_traceback_later(40, exit=True)
root = sys.argv[1]
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="syncnotes-quit-test-")
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, root)
sys.argv = [str(Path(root) / "main.py")]

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

import main as app_main
from app import config
from app.database import SqliteRepository
from app.models import Note
from app.ui import confirm

assert config.isolated_run()
s = socket.socket()
s.bind(("127.0.0.1", 0))
port = s.getsockname()[1]
s.close()
config.default_local_state_path().parent.mkdir(parents=True, exist_ok=True)
config.default_local_state_path().write_text(json.dumps(
    {"nickname": "tester", "last_host_ip": "127.0.0.1", "last_host_port": port,
     "last_mode": "host", "last_heartbeat_ms": 3000, "notes": {}, "users": {}}),
    encoding="utf-8")
config.default_settings_path().write_text(
    json.dumps({"language_code": "ko", "show_tray_icon": False}), encoding="utf-8")
confirm.ask = lambda parent, title, text, **k: True
confirm.notify = lambda parent, title, text, **k: None
repo = SqliteRepository(config.default_db_path())
for text in ("one", "two", "three"):
    n = Note.new(user="tester")
    n.content = text
    repo.insert_note(n)
repo.close()
report = {}


def probe(app, window, settings):
    def ask_to_quit():
        report["notes_shown"] = sum(w.isVisible() for w in window._stickies.values())
        QApplication.quit()

    def still_here():
        report["quit_was_cancelled"] = True
        window._quit_app()

    QTimer.singleShot(1200, ask_to_quit)
    QTimer.singleShot(3000, still_here)


app_main._schedule_update_check = probe
code = app_main.main()
saved = json.loads(config.default_local_state_path().read_text(encoding="utf-8"))
report["hidden"] = sorted(k for k, v in saved.get("notes", {}).items()
                          if v.get("hidden"))
print(json.dumps({"exit": code, **report}))
'''


def test_a_bare_quit_request_ends_the_app_and_hides_nothing(qapp):
    env = dict(os.environ)
    real = env.get("SYNCNOTES_TEST_REAL_APPDATA")
    if real:
        env["APPDATA"] = real       # 임시 APPDATA 로는 PySide6 를 못 찾는다
    root = str(Path(__file__).resolve().parent.parent)
    proc = subprocess.run([sys.executable, "-c", _WHOLE_APP, root],
                          capture_output=True, text=True, env=env, timeout=90)
    assert proc.returncode == 0, proc.stderr[-2000:]
    report = json.loads(proc.stdout.strip().splitlines()[-1])
    assert report["notes_shown"] == 3
    assert "quit_was_cancelled" not in report
    assert report["hidden"] == [] and report["exit"] == 0
