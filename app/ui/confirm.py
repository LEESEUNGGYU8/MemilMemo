"""확인창과 알림창.

Qt 기본 창은 단추 글자가 화면 언어와 무관하게 Yes/No/OK 로 나오고, 지우는
확인창의 기본 단추가 '예'라서 Enter 한 번에 지워진다. 여기서는 단추에 하는
일을 그대로 적고(삭제, 되돌리기), 기본 단추를 늘 '취소'로 둔다."""
from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QPushButton, QWidget

from .. import i18n
from .theme import refresh_style


def build_ask(parent: QWidget | None, title: str, text: str, *,
              action: str, detail: str = "", cancel: str = "",
              danger: bool = False
              ) -> tuple[QMessageBox, QPushButton, QPushButton]:
    """확인창을 만들어 (창, 실행 단추, 취소 단추)를 돌려준다."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning if danger else QMessageBox.Question)
    box.setWindowTitle(title)
    box.setText(text)
    if detail:
        box.setInformativeText(detail)
    act = box.addButton(action, QMessageBox.DestructiveRole if danger
                        else QMessageBox.AcceptRole)
    can = box.addButton(cancel or i18n.t("common.btn_cancel"),
                        QMessageBox.RejectRole)
    if danger:
        act.setProperty("danger", True)
    can.setProperty("secondary", True)
    # 단추는 만들어질 때 이미 모양이 정해진다. 속성을 붙인 뒤 다시 입힌다.
    refresh_style(act)
    refresh_style(can)
    # 실수로 누른 Enter 나 Esc 가 실행으로 이어지지 않게 한다.
    box.setDefaultButton(can)
    box.setEscapeButton(can)
    return box, act, can


def ask(parent: QWidget | None, title: str, text: str, *, action: str,
        detail: str = "", cancel: str = "", danger: bool = False) -> bool:
    """실행 단추를 눌렀으면 True."""
    box, act, _ = build_ask(parent, title, text, action=action, detail=detail,
                            cancel=cancel, danger=danger)
    box.exec()
    return box.clickedButton() is act


def notify(parent: QWidget | None, title: str, text: str, *,
           icon: QMessageBox.Icon = QMessageBox.Information,
           detail: str = "") -> None:
    """확인 단추 하나만 있는 알림창."""
    box = QMessageBox(parent)
    box.setIcon(icon)
    box.setWindowTitle(title)
    box.setText(text)
    if detail:
        box.setInformativeText(detail)
    ok = box.addButton(i18n.t("main.btn_ok"), QMessageBox.AcceptRole)
    ok.setMinimumWidth(96)
    box.exec()
