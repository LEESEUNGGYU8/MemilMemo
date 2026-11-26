"""앱이 그리는 상단바: 대화 상자, 큰 창, 확인 창, 가장자리와 최대화 단추 판정."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QDialog, QLabel, QLineEdit, QMainWindow, QVBoxLayout, QWidget,
)

from app import i18n  # noqa: E402
from app.ui import theme  # noqa: E402
from app.ui import window_frame as wf  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    i18n.set_language("ko")
    theme.apply_theme(app, "")
    yield app


def _dialog(title="알림"):
    d = QDialog()
    d.setWindowTitle(title)
    lay = QVBoxLayout(d)
    lay.setContentsMargins(20, 16, 20, 14)
    lay.addWidget(QLabel("본문"))
    return d


def test_a_dialog_gets_a_title_bar_on_top_of_its_layout(qapp):
    d = _dialog()
    bar = wf.install(d, wf.DIALOG)
    assert wf.install(d, wf.DIALOG) is bar                    # 두 번 입히지 않는다
    assert d.windowFlags() & Qt.FramelessWindowHint
    assert d.layout().contentsMargins().top() == 16 + wf.HEIGHTS[wf.DIALOG]
    assert bar.title() == "알림" and bar.min_button is None and bar.max_button is None
    d.setWindowTitle("체크 해제 예약")
    assert bar.title() == "체크 해제 예약"
    d.resize(380, 300)
    d.show()
    qapp.processEvents()
    assert bar.geometry().topLeft() == QPoint(0, 0) and bar.width() == d.width()
    assert bar.close_button.accessibleName() == "닫기"
    bar.close_button.click()
    assert not d.isVisible()
    d.deleteLater()


def test_a_confirm_box_has_only_the_close_button(qapp):
    d = _dialog("메모 삭제")
    bar = wf.install(d, wf.BARE)
    d.show()
    qapp.processEvents()
    assert not bar._title.isVisible() and bar.close_button.isVisible()
    d.close()
    d.deleteLater()


def test_a_big_window_has_caption_buttons_and_toggles_maximized(qapp):
    w = QMainWindow()
    w.setWindowTitle("메밀메모")
    w.setCentralWidget(QWidget())
    bar = wf.install(w, wf.WINDOW)
    assert w.menuWidget() is bar and bar.height() == wf.HEIGHTS[wf.WINDOW] == 48
    assert [b.accessibleName() for b in (bar.min_button, bar.max_button, bar.close_button)] \
        == ["최소화", "최대화", "닫기"]
    w.resize(780, 600)
    w.show()
    qapp.processEvents()
    bar.max_button.click()
    qapp.processEvents()
    assert w.isMaximized() and bar.max_button.accessibleName() == "이전 크기로"
    bar.toggle_maximized()
    qapp.processEvents()
    assert not w.isMaximized() and bar.max_button.accessibleName() == "최대화"
    w.close()
    w.deleteLater()


def test_only_empty_parts_of_the_bar_move_the_window(qapp):
    w = QMainWindow()
    w.setWindowTitle("메밀메모")
    w.setCentralWidget(QWidget())
    bar = wf.install(w, wf.WINDOW)
    search = QLineEdit()
    search.setFixedWidth(200)
    bar.add_widget(search)
    w.resize(780, 600)
    w.show()
    qapp.processEvents()
    title = bar._title.mapTo(bar, bar._title.rect().center())
    assert bar.is_drag_area(title)
    assert not bar.is_drag_area(search.geometry().center())
    assert not bar.is_drag_area(bar.close_button.geometry().center())
    assert bar.is_drag_area(QPoint(search.geometry().left() - 20, 20))
    w.close()
    w.deleteLater()


def test_edges_resize_and_the_maximize_button_opens_snap_layouts(qapp):
    w = QMainWindow()
    w.setCentralWidget(QWidget())
    bar = wf.install(w, wf.WINDOW)
    w.resize(780, 600)
    w.show()
    qapp.processEvents()
    hit = lambda x, y, **kw: wf.hit_test(w, bar, QPointF(x, y),  # noqa: E731
                                         resizable=kw.get("resizable", True),
                                         snap=kw.get("snap", True))
    assert hit(2, 300) == wf.HTLEFT and hit(778, 300) == wf.HTRIGHT
    assert hit(2, 2) == wf.HTTOPLEFT and hit(778, 598) == wf.HTBOTTOMRIGHT
    assert hit(400, 2) == wf.HTTOP and hit(400, 598) == wf.HTBOTTOM
    assert hit(400, 300) == wf.HTCLIENT
    assert hit(2, 300, resizable=False) == wf.HTCLIENT
    corner = bar.max_button.mapTo(w, QPoint(0, 0))
    inside = (corner.x() + 20, corner.y() + 20)
    assert hit(*inside) == wf.HTMAXBUTTON
    assert hit(*inside, snap=False) == wf.HTCLIENT           # Windows 10
    w.showMaximized()
    qapp.processEvents()
    assert hit(2, 300) == wf.HTCLIENT                          # 최대화하면 가장자리가 없다
    w.close()
    w.deleteLater()
