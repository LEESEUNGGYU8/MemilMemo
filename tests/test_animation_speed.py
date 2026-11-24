"""메모가 움직이는 빠르기(설정의 '움직임 속도')."""
from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app import i18n, shell_menu  # noqa: E402
from app.settings import (  # noqa: E402
    ANIMATION_SPEED_MAX, ANIMATION_SPEED_MIN, ANIMATION_SPEED_STEP, AppSettings,
    clamp_animation_speed,
)
from app.ui import note_fold as nf  # noqa: E402
from app.ui import settings_dialog  # noqa: E402
from app.ui.folder_icon import FolderIconWidget  # noqa: E402
from app.ui.settings_dialog import SettingsDialog  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    i18n.set_language("ko")
    yield app


@pytest.fixture(autouse=True)
def normal_speed_afterwards():
    yield
    nf.set_animation_speed(1.0)


def test_speed_stays_between_half_and_double_in_tenths():
    assert (ANIMATION_SPEED_MIN, ANIMATION_SPEED_MAX, ANIMATION_SPEED_STEP) == (
        0.5, 2.0, 0.1)
    assert clamp_animation_speed(1.0) == 1.0
    assert clamp_animation_speed(0.1) == 0.5
    assert clamp_animation_speed(7) == 2.0
    assert clamp_animation_speed(1.26) == 1.3
    assert clamp_animation_speed("1.5") == 1.5
    for junk in ("fast", None, float("nan"), [1]):
        assert clamp_animation_speed(junk) == 1.0


def test_the_default_is_one_and_a_bad_file_value_is_put_right(tmp_path):
    assert AppSettings().animation_speed == 1.0
    path = tmp_path / "settings.json"
    for raw, want in ((1.7, 1.7), (9, 2.0), (0, 0.5), ("quick", 1.0), (True, 1.0)):
        path.write_text(json.dumps({"animation_speed": raw}), encoding="utf-8")
        assert AppSettings.load(path).animation_speed == want
    # 옛 버전이 쓴 파일에는 이 값이 없다.
    path.write_text(json.dumps({"fold_animation": False}), encoding="utf-8")
    assert AppSettings.load(path).animation_speed == 1.0


def test_twice_as_fast_takes_half_the_time():
    assert nf.anim_ms(nf.FOLD_MS) == nf.FOLD_MS
    nf.set_animation_speed(2.0)
    assert nf.anim_ms(400) == 200 and nf.anim_ms(nf.FADE_MS) == 85
    nf.set_animation_speed(0.5)
    assert nf.anim_ms(400) == 800
    nf.set_animation_speed("x")                 # 모르는 값이면 기본 빠르기
    assert nf.animation_speed() == 1.0
    nf.set_animation_speed(0)
    assert nf.anim_ms(400) == 400


def test_an_icon_slides_at_the_chosen_speed(qapp, monkeypatch):
    monkeypatch.setattr(nf, "system_animations_enabled", lambda: True)
    icon = FolderIconWidget("f", "업무", "#BAE1FF")
    icon.show()
    try:
        nf.set_animation_speed(2.0)
        icon._slide_to(icon.pos() + QPoint(200, 0))
        assert icon._slide.duration() == nf.SLIDE_MS // 2
        # 미끄러져 가는 동안에는 가서 놓일 자리가 그 아이콘의 자리다.
        assert icon.icon_spot() == icon._slide.endValue()
        nf.set_animation_speed(0.5)
        icon._slide_to(icon.pos() + QPoint(0, 300))
        assert icon._slide.duration() == nf.SLIDE_MS * 2
    finally:
        icon.close()
        icon.deleteLater()


def test_the_settings_window_offers_the_speed(qapp, monkeypatch):
    # 저장은 자동 실행 값과 우클릭 메뉴도 쓴다. 실제 값은 건드리지 않게 막는다.
    monkeypatch.setattr(settings_dialog, "set_windows_autostart", lambda v: True)
    monkeypatch.setattr(shell_menu, "set_registered", lambda *a, **k: True)
    settings = AppSettings(animation_speed=1.5)
    settings.save = lambda path=None: None
    dlg = SettingsDialog(settings, role="host")
    box = dlg._animation_speed
    assert box.value() == 1.5
    assert (box.minimum(), box.maximum(), round(box.singleStep(), 2)) == (0.5, 2.0, 0.1)
    box.stepUp()
    assert round(box.value(), 1) == 1.6
    # 움직임을 끄면 빠르기를 고를 일이 없다.
    dlg._fold_animation.setChecked(False)
    assert not box.isEnabled()
    dlg._fold_animation.setChecked(True)
    assert box.isEnabled()
    box.setValue(0.7)
    dlg._on_accept()
    assert settings.animation_speed == 0.7
    dlg.deleteLater()
