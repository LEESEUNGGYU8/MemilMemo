"""숫자를 적는 칸: 숫자와 점만, 단위는 칸 밖, 치는 동안 막지 않고 다 친 뒤 맞추기."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QWheelEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from app import i18n, shell_menu  # noqa: E402
from app.settings import AppSettings  # noqa: E402
from app.ui import settings_dialog, theme  # noqa: E402
from app.ui.connect_dialog import ConnectDialog  # noqa: E402
from app.ui.number_field import DecimalField, IntField, typed_number, with_unit  # noqa: E402
from app.ui.settings_dialog import SettingsDialog  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    i18n.set_language("ko")
    theme.apply_theme(app, "")
    yield app


def _type(field, text: str) -> None:
    edit = field.lineEdit()
    edit.selectAll()
    QTest.keyClicks(edit, text)


def test_only_digits_and_one_dot_are_kept():
    assert typed_number("1.5 배", 1) == "1.5"
    assert typed_number("a-3,0초", 1) == "30"
    assert typed_number("1.2.3", 1) == "1.2"
    assert typed_number("1.26", 1) == "1.2"         # 소수는 한 자리까지만 쳐진다
    assert typed_number("1.5", 0) == "15"           # 정수 칸에는 점이 없다
    assert typed_number("１２．５", 1) == "12.5"     # 전각 숫자는 반각으로 받는다
    assert typed_number("", 1) == "" and typed_number("배", 1) == ""


def test_the_speed_can_be_typed_without_being_blocked(qapp):
    field = DecimalField(0.5, 2.0, 0.1)
    field.setValue(1.0)
    assert field.suffix() == "" and field.text() == "1.0"
    _type(field, "0.7")
    field.interpretText()
    assert round(field.value(), 1) == 0.7
    # 범위를 넘는 숫자도 치는 동안에는 쳐진다. 다 치면 가장 가까운 값이 된다.
    _type(field, "3")
    assert field.lineEdit().text() == "3"
    field.interpretText()
    assert field.value() == 2.0 and field.text() == "2.0"
    _type(field, "0.1")
    field.interpretText()
    assert field.value() == 0.5
    # 숫자와 점이 아닌 것은 들어가지 않는다.
    _type(field, "1.5x-")
    field.lineEdit().insert("배")
    assert field.lineEdit().text() == "1.5"
    field.interpretText()
    assert field.value() == 1.5
    # 비워 두고 나가면 있던 값이 남는다.
    field.lineEdit().selectAll()
    QTest.keyClick(field.lineEdit(), Qt.Key_Delete)
    assert field.lineEdit().text() == ""
    field.interpretText()
    assert field.value() == 1.5 and field.text() == "1.5"
    field.deleteLater()


def test_whole_numbers_are_brought_into_range_when_done(qapp):
    port = IntField(1, 65535)
    port.setValue(5555)
    _type(port, "70000")
    assert port.lineEdit().text() == "70000"
    port.interpretText()
    assert port.value() == 65535 and port.text() == "65535"
    _type(port, "0")
    port.interpretText()
    assert port.value() == 1
    _type(port, "5.6k01")                           # 점과 글자는 들어가지 않는다
    assert port.lineEdit().text() == "5601"
    port.interpretText()
    assert port.value() == 5601
    port.deleteLater()


def test_entering_the_field_selects_the_value_and_the_wheel_waits_for_focus(qapp):
    field = DecimalField(0.5, 60.0, 0.5)
    field.setValue(3.0)
    field.show()
    qapp.processEvents()

    def wheel() -> QWheelEvent:
        return QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, 120),
                           Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)

    field.clearFocus()
    field.wheelEvent(wheel())
    assert field.value() == 3.0                     # 칸에 들어가 있지 않으면 그대로다
    field.setFocus(Qt.TabFocusReason)
    qapp.processEvents()
    QTest.qWait(20)
    if field.hasFocus():
        assert field.lineEdit().selectedText() == "3.0"
        field.wheelEvent(wheel())
        assert field.value() == 3.5
    field.close()
    field.deleteLater()


def test_the_unit_sits_outside_the_field(qapp):
    field = IntField(1, 1000)
    box = with_unit(field, " 개")
    labels = box.findChildren(QLabel)
    assert [label.text() for label in labels] == ["개"] and box.unit_label is labels[0]
    assert field.parentWidget() is box and field.suffix() == ""
    box.deleteLater()


def test_settings_fields_hold_numbers_only_in_every_language(qapp, monkeypatch):
    monkeypatch.setattr(settings_dialog, "set_windows_autostart", lambda v: True)
    monkeypatch.setattr(shell_menu, "set_registered", lambda *a, **k: True)
    for lang in ("ko", "en", "ja"):
        i18n.set_language(lang)
        settings = AppSettings(animation_speed=1.3)
        settings.save = lambda path=None: None
        dlg = SettingsDialog(settings, role="host")
        fields = {"font": dlg._font_size, "speed": dlg._animation_speed,
                  "port": dlg._port, "heartbeat": dlg._heartbeat,
                  "interval": dlg._backup_interval, "keep": dlg._backup_keep}
        for name, field in fields.items():
            assert set(field.text()) <= set("0123456789."), (lang, name, field.text())
            assert field.suffix() == "" and field.prefix() == ""
            assert isinstance(field, (IntField, DecimalField))
        # 단위는 칸 옆에 따로 있다.
        for name in ("font", "speed", "heartbeat", "keep"):
            unit = fields[name].parentWidget().unit_label.text()
            assert unit and unit == unit.strip(), (lang, name)
        assert dlg._animation_speed.text() == "1.3"
        dlg.deleteLater()
    i18n.set_language("ko")
    assert SettingsDialog(AppSettings(), role="host")._animation_speed \
        .parentWidget().unit_label.text() == "배"


def test_the_connect_window_uses_the_same_fields(qapp):
    dlg = ConnectDialog()
    for field in (dlg._host_port, dlg._guest_port):
        assert isinstance(field, IntField) and field.suffix() == ""
    assert isinstance(dlg._host_heartbeat, DecimalField)
    assert dlg._host_heartbeat.suffix() == ""
    assert dlg._host_heartbeat.parentWidget().unit_label.text() == "초"
    dlg.deleteLater()
