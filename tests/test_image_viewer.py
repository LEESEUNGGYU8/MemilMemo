"""이미지 뷰어(offscreen): 글 없는 막대, 배율 단추, 창에 맞춤과 실제 크기."""
from __future__ import annotations

import base64
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QIODevice, QPoint, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QToolButton  # noqa: E402

from app import i18n  # noqa: E402
from app.ui import image_viewer, theme  # noqa: E402
from app.ui.image_viewer import ImageViewerWindow  # noqa: E402


def image_url(w: int, h: int, color: str = "#3B6EA8") -> str:
    img = QImage(w, h, QImage.Format_RGB32)
    img.fill(QColor(color))
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(bytes(buf.data())).decode()


BIG, TALL, SMALL = image_url(1600, 900), image_url(600, 1400), image_url(200, 120)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    i18n.set_language("ko")
    theme.apply_theme(app, "")
    yield app


@pytest.fixture
def viewer(qapp):
    made = []

    def build(urls, index=0, size=(760, 520)):
        v = ImageViewerWindow()
        v.set_images(urls, index)
        v.present()
        v.resize(*size)
        qapp.processEvents()
        made.append(v)
        return v

    yield build
    for v in made:
        v.close()
    qapp.processEvents()


def test_bar_has_no_instructions(viewer):
    v = viewer([BIG, TALL])
    labels = [w.text() for w in v.findChildren(QLabel) if w.isVisibleTo(v)]
    # 보이는 글은 몇 번째 이미지인지와 배율뿐이다. 쓰는 법을 적어 두지 않는다.
    assert sorted(labels) == sorted(["1 / 2", v._zoom_lbl.text()])
    assert v._zoom_lbl.text().endswith("%")
    assert v.windowTitle() == "이미지 보기"
    for b in v.findChildren(QToolButton):
        tip = b.toolTip()
        assert tip and b.accessibleName() == tip
        assert "·" not in tip and "(" not in tip


def test_every_language_is_free_of_separators_and_hints():
    for lang in ("ko", "en", "ja"):
        i18n.set_language(lang)
        for key in ("imgview.tip_prev", "imgview.tip_next", "imgview.tip_fit",
                    "imgview.tip_actual", "imgview.tip_zoom_in",
                    "imgview.tip_zoom_out", "imgview.window_title"):
            text = i18n.t(key)
            assert text and text != key
            assert not any(ch in text for ch in "·・(（")
    i18n.set_language("ko")


def test_fit_leaves_a_margin_and_tracks_the_window(viewer):
    v = viewer([BIG])
    canvas = v.canvas()
    view = canvas.viewport().size()
    shown_w = 1600 * canvas.zoom()
    assert canvas.is_fit() and canvas.zoom() < 1.0
    # 그림이 창 테두리에 붙지 않는다.
    assert shown_w <= view.width() - 2 * image_viewer._FIT_MARGIN + 1
    assert v._fit_btn.isChecked() and not v._actual_btn.isChecked()
    before = canvas.zoom()
    v.resize(520, 420)
    QApplication.processEvents()
    assert canvas.zoom() < before          # 창을 줄이면 따라 줄어든다


def test_small_image_is_not_blown_up(viewer):
    v = viewer([SMALL])
    assert v.canvas().zoom() == pytest.approx(1.0)
    assert v._zoom_lbl.text() == "100%"
    # 이미지가 한 장이면 넘기는 단추가 없다.
    assert v._prev_btn.isHidden() and v._next_btn.isHidden() and v._counter.isHidden()


def test_zoom_buttons_step_and_leave_fit_mode(viewer):
    v = viewer([BIG])
    canvas = v.canvas()
    start = canvas.zoom()

    v._zoom_in_btn.click()
    assert canvas.zoom() == pytest.approx(start * image_viewer.ZOOM_STEP)
    assert not canvas.is_fit() and not v._fit_btn.isChecked()
    v._zoom_out_btn.click()
    v._zoom_out_btn.click()
    assert canvas.zoom() == pytest.approx(start / image_viewer.ZOOM_STEP)
    assert v._zoom_lbl.text() == f"{int(round(canvas.zoom() * 100))}%"

    v._actual_btn.click()
    assert canvas.zoom() == pytest.approx(1.0)
    assert v._actual_btn.isChecked() and not v._fit_btn.isChecked()
    # 이미 고른 단추를 다시 눌러도 그대로 눌린 모양이다.
    v._actual_btn.click()
    assert v._actual_btn.isChecked()
    v._fit_btn.click()
    assert canvas.is_fit() and v._fit_btn.isChecked() and not v._actual_btn.isChecked()


def test_zoom_stops_at_the_limits(viewer):
    v = viewer([SMALL])
    canvas = v.canvas()
    for _ in range(80):
        v._zoom_in_btn.click()
    assert canvas.zoom() == pytest.approx(image_viewer.MAX_SCALE)
    assert not v._zoom_in_btn.isEnabled() and v._zoom_out_btn.isEnabled()
    for _ in range(120):
        v._zoom_out_btn.click()
    assert canvas.zoom() == pytest.approx(image_viewer.MIN_SCALE)
    assert not v._zoom_out_btn.isEnabled() and v._zoom_in_btn.isEnabled()


def test_double_click_switches_between_fit_and_actual_size(viewer):
    v = viewer([BIG])
    canvas = v.canvas()
    center = QPoint(canvas.viewport().width() // 2, canvas.viewport().height() // 2)

    QTest.mouseDClick(canvas.viewport(), Qt.LeftButton, Qt.NoModifier, center)
    assert not canvas.is_fit() and canvas.zoom() == pytest.approx(1.0)
    QTest.mouseDClick(canvas.viewport(), Qt.LeftButton, Qt.NoModifier, center)
    assert canvas.is_fit() and canvas.zoom() < 1.0


def test_moving_between_images(viewer):
    v = viewer([BIG, TALL, SMALL])
    assert v._counter.text() == "1 / 3"
    assert not v._prev_btn.isEnabled() and v._next_btn.isEnabled()
    QTest.keyClick(v, Qt.Key_Right)
    assert v.current_index() == 1 and v._counter.text() == "2 / 3"
    v._next_btn.click()
    assert v.current_index() == 2
    assert v._prev_btn.isEnabled() and not v._next_btn.isEnabled()
    # 넘기면 새 그림에 맞춰 다시 맞춘다.
    assert v.canvas().is_fit() and v.canvas().zoom() == pytest.approx(1.0)
    QTest.keyClick(v, Qt.Key_Left)
    assert v.current_index() == 1


def test_broken_image_rests_the_zoom_controls(viewer):
    v = viewer(["data:image/png;base64,AAAA"], size=(560, 440))
    assert not v._error.isHidden() and v._zoom_lbl.text() == ""
    for b in (v._zoom_in_btn, v._zoom_out_btn, v._fit_btn, v._actual_btn):
        assert not b.isEnabled()
    assert not v._fit_btn.isChecked() and not v._actual_btn.isChecked()
