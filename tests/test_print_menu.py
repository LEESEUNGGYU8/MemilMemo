"""인쇄와 PDF 를 메모와 폴더 메뉴에서 쓴다."""
from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtWidgets import QApplication, QFileDialog  # noqa: E402

from app import i18n  # noqa: E402
from app.local_state import LocalStateStore  # noqa: E402
from app.network.client_base import ClientBase  # noqa: E402
from app.settings import AppSettings  # noqa: E402
from app.ui import edit_menu, theme  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.sticky_note import StickyNoteWidget  # noqa: E402

ME = "지우"


def note(nid, text, folder=None):
    return {"id": nid, "version": 1, "color": "#FFE066", "width": 280, "height": 220,
            "content": text, "created_by": ME, "updated_by": ME, "folder_id": folder,
            "updated_at": int(time.time() * 1000)}


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    i18n.set_language("ko")
    theme.apply_theme(app, "")
    monkeypatch.setattr(StickyNoteWidget, "bring_to_front",
                        lambda self, *, start_editing=False: None)
    client = ClientBase(ME)
    settings = AppSettings(fold_animation=False)
    settings.save = lambda path=None: None
    win = MainWindow(client=client, local_state=LocalStateStore(tmp_path / "ls.json"),
                     role="guest", settings=settings)
    client.connectionChanged.emit(True)
    client._dispatch("welcome", {
        "users": [ME], "host": ME, "locks": {}, "heartbeat_ms": 3000, "trash_days": 30,
        "reminders": {}, "folders": [{"id": "f", "name": "업무", "color": "#FFFFFF",
                                      "private_owner": None}],
        "notes": [note("a", "<p>주간 회의 메모</p><p>견적 회신</p>", "f"),
                  note("b", "<p>점심 주문</p>", "f")]})
    yield win
    win._force_quit = True
    win._teardown_stickies()
    win.close()
    win.deleteLater()
    app.processEvents()


def test_a_note_saves_as_pdf_from_its_more_menu(window, tmp_path, monkeypatch):
    target = tmp_path / "회의.pdf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(target), "PDF (*.pdf)")))
    shown = []
    monkeypatch.setattr(edit_menu, "run_menu", lambda menu, pos: shown.append(menu))
    sticky = window._stickies["a"]
    sticky._show_more_menu()
    pdf = next(a for a in shown[-1].actions() if a.text() == "PDF로 저장")
    pdf.trigger()
    assert target.read_bytes().startswith(b"%PDF")


def test_a_folder_prints_its_notes_in_list_order(window, monkeypatch):
    calls = []
    from app.ui import note_print
    monkeypatch.setattr(note_print, "print_notes",
                        lambda parent, notes, **kw: calls.append(([n["id"] for n in notes], kw)))
    shown = []
    monkeypatch.setattr(edit_menu, "run_menu", lambda menu, pos: shown.append(menu))
    window._show_folder_menu("f", QPoint(0, 0))
    sub = next(a.menu() for a in shown[-1].actions() if a.text() == "인쇄")
    texts = [a.text() for a in sub.actions() if not a.isSeparator()]
    assert texts == ["인쇄", "PDF로 저장", "메모마다 한 쪽", "이어서", "메모 색 그대로"]
    next(a for a in sub.actions() if a.text() == "이어서").trigger()    # 쪽 나누기를 고른다
    assert window._settings.print_one_per_page is False
    next(a for a in sub.actions() if a.text() == "인쇄").trigger()
    ids, kw = calls[-1]
    assert sorted(ids) == ["a", "b"] and kw["one_per_page"] is False
    keep = next(a for a in sub.actions() if a.text() == "메모 색 그대로")
    keep.trigger()
    assert window._settings.print_keep_color is True
