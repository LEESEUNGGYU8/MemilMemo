"""앱의 모든 창(메인 창 빼고)이 앱이 그리는 상단바를 쓴다. 대화 상자는 닫기만,
큰 창은 최소화, 최대화, 닫기, 확인창은 제목 없이 닫기만."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from app import i18n  # noqa: E402
from app.settings import AppSettings  # noqa: E402
from app.ui import confirm, theme  # noqa: E402
from app.ui import window_frame as wf  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    i18n.set_language("ko")
    theme.apply_theme(app, "")
    yield app


def _dialogs():
    from app.ui.connect_dialog import ConnectDialog
    from app.ui.copies_dialog import CopiesDialog
    from app.ui.drafts_dialog import DraftsDialog
    from app.ui.folder_dialog import FolderEditDialog
    from app.ui.font_colors import ColorPicker
    from app.ui.link_dialog import LinkDialog
    from app.ui.password_dialog import PasswordDialog, ShareStartDialog
    from app.ui.reminder_dialog import ReminderDialog
    from app.ui.sessions_dialog import SessionsDialog
    from app.ui.settings_dialog import SettingsDialog
    from app.ui.trash_dialog import _Preview
    from app.ui.update_dialog import UpdateAvailableDialog
    from app.offline_drafts import KIND_NEW
    return [
        lambda: ConnectDialog("지우"),
        lambda: CopiesDialog([], host_notes=None, can_takeover=False),
        lambda: DraftsDialog([(KIND_NEW, "끊긴 동안 쓴 글")]),
        lambda: FolderEditDialog(),
        lambda: ColorPicker("메모 색", QColor("#FFE066")),
        lambda: LinkDialog(text="", href="", editing=False),
        lambda: PasswordDialog(),
        lambda: ShareStartDialog(),
        lambda: ReminderDialog(None, None),
        lambda: SessionsDialog(),
        lambda: SettingsDialog(AppSettings()),
        lambda: _Preview(),
        lambda: UpdateAvailableDialog(current_version="1.0.4", info={"version": "1.0.5"}),
    ]


def test_dialogs_get_a_dialog_bar_with_their_title(qapp):
    for make in _dialogs():
        d = make()
        bar = wf.bar_of(d)
        assert bar is not None, type(d).__name__
        assert bar.kind == wf.DIALOG and bar.min_button is None and bar.max_button is None
        assert bar.title() == d.windowTitle()
        # 내용은 상단바 아래에서 시작한다.
        assert d.layout().contentsMargins().top() >= wf.HEIGHTS[wf.DIALOG], type(d).__name__
        d.deleteLater()


def test_big_windows_get_minimize_and_maximize(qapp):
    from app.ui.folder_window import FolderWindow
    from app.ui.history_viewer import HistoryViewer
    from app.ui.image_viewer import ImageViewerWindow
    from app.ui.trash_dialog import TrashDialog
    for make in (lambda: FolderWindow("f1", AppSettings()), ImageViewerWindow,
                 lambda: TrashDialog(30, AppSettings()), HistoryViewer):
        w = make()
        bar = wf.bar_of(w)
        assert bar is not None and bar.kind == wf.WINDOW, type(w).__name__
        assert bar.min_button is not None and bar.max_button is not None
        w.deleteLater()


def test_confirm_boxes_have_no_title_and_keep_room_when_their_text_changes(qapp):
    box, _act, _cancel = confirm.build_ask(None, "메모 삭제", "휴지통으로 옮길까요?",
                                           action="옮기기", detail="30일 뒤 지워집니다.")
    bar = wf.bar_of(box)
    assert bar is not None and bar.kind == wf.BARE and bar.title() == "메모 삭제"
    box.show()
    qapp.processEvents()
    assert not bar._title.isVisible()
    # 확인창은 글을 바꿀 때 레이아웃을 새로 만든다. 그래도 글은 상단바 아래다.
    box.setInformativeText("받는 중 50%")
    qapp.processEvents()
    main = box.findChild(QLabel, "qt_msgbox_label")
    assert main.mapTo(box, main.rect().topLeft()).y() >= bar.height()
    box.close()
    note = confirm.build_notify(None, "알림", "저장했습니다.")
    assert wf.bar_of(note).kind == wf.BARE
    confirm.frame_box(note)                  # 두 번 입혀도 여백이 두 번 늘지 않는다
    assert note.contentsMargins().top() == wf.HEIGHTS[wf.BARE]
    box.deleteLater()
    note.deleteLater()
