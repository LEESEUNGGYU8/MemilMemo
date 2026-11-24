"""비밀번호가 걸린 공유에 참여할 때 비밀번호를 묻는 창.

물음 한 줄, 입력 칸 하나, 확인과 취소. 방금 넣은 것이 틀렸으면 그 아래에 한 줄로
알린다. 취소하면 붙지 않고 기다린다(메인 창의 알림 줄에서 다시 넣을 수 있다)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import i18n
from ..share_auth import MAX_PASSWORD_LENGTH, clean_password
from . import confirm
from .icons import svg_icon
from .theme import COLOR_TEXT_SUBTLE, refresh_style

_WIDTH = 360
_COLOR_WRONG = "#B42318"


def password_field(placeholder: str = "") -> QLineEdit:
    """글자를 가려 보이는 입력 칸. 오른쪽의 눈 단추로 잠깐 볼 수 있다(틀리게
    넣은 줄 모르고 정하는 일을 막는다)."""
    field = QLineEdit()
    field.setEchoMode(QLineEdit.Password)
    field.setMaxLength(MAX_PASSWORD_LENGTH)
    field.setPlaceholderText(placeholder)
    reveal = QAction(field)
    reveal.setCheckable(True)

    def show(shown: bool) -> None:
        field.setEchoMode(QLineEdit.Normal if shown else QLineEdit.Password)
        reveal.setIcon(svg_icon("eye_on" if shown else "eye_off", 16, "#6B7280"))
        reveal.setToolTip(i18n.t("auth.hide") if shown else i18n.t("auth.show"))

    reveal.toggled.connect(show)
    show(False)
    field.addAction(reveal, QLineEdit.TrailingPosition)
    return field


class PasswordDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, *, wrong: bool = False,
                 wait: int = 0):
        super().__init__(parent)
        self.setWindowTitle(i18n.t("auth.window_title"))
        self.setModal(True)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 16)
        root.setSpacing(0)

        question = QLabel(i18n.t("auth.ask_body"))
        question.setTextFormat(Qt.PlainText)
        question.setWordWrap(True)
        question.setStyleSheet("font-size: 13.5px; font-weight: 600; color: #1F1F1F;")
        root.addWidget(question)
        root.addSpacing(12)

        self._field = password_field()
        self._field.setAccessibleName(i18n.t("auth.window_title"))
        root.addWidget(self._field)

        # 틀렸다는 말과, 너무 틀려 기다려야 한다는 말. 없으면 줄도 없다.
        notes = []
        if wrong:
            notes.append(i18n.t("auth.wrong"))
        if wait > 0:
            notes.append(i18n.t("auth.wait", seconds=int(wait)))
        self._note = QLabel("\n".join(notes))
        self._note.setTextFormat(Qt.PlainText)
        self._note.setWordWrap(True)
        self._note.setStyleSheet(
            f"font-size: 12px; color: {_COLOR_WRONG if wrong else COLOR_TEXT_SUBTLE};")
        self._note.setVisible(bool(notes))
        if notes:
            root.addSpacing(8)
        root.addWidget(self._note)
        root.addSpacing(16)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        self._ok = QPushButton(i18n.t("auth.btn_ok"))
        self._cancel = QPushButton(i18n.t("common.btn_cancel"))
        self._cancel.setProperty("secondary", True)
        refresh_style(self._cancel)
        for button in (self._ok, self._cancel):
            button.setCursor(Qt.PointingHandCursor)
            button.setMinimumWidth(92)
            buttons.addWidget(button)
        root.addLayout(buttons)
        self._ok.setDefault(True)
        self._ok.setEnabled(False)
        self._ok.clicked.connect(self.accept)
        self._cancel.clicked.connect(self.reject)
        # 빈칸으로는 확인할 수 없다(비밀번호가 없다는 뜻이 된다).
        self._field.textChanged.connect(
            lambda text: self._ok.setEnabled(bool(clean_password(text))))
        self._field.setFocus()
        self.setFixedWidth(_WIDTH)

    def note(self) -> str:
        return self._note.text() if self._note.isVisibleTo(self) or self._note.text() else ""

    def password(self) -> str:
        return clean_password(self._field.text())


def ask(parent: QWidget | None, *, wrong: bool = False, wait: int = 0) -> str | None:
    """창을 띄워 넣은 비밀번호를 돌려준다. 취소하면 None."""
    dialog = PasswordDialog(parent, wrong=wrong, wait=wait)
    if dialog.exec() != QDialog.Accepted:
        return None
    return dialog.password() or None


class ShareStartDialog(QDialog):
    """혼자 쓰던 메모의 공유를 시작한다. 비밀번호는 걸어도 되고 안 걸어도 된다."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(i18n.t("settings.btn_share_start"))
        self.setFixedWidth(_WIDTH)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 14)
        root.setSpacing(10)
        body = QLabel()
        body.ensurePolished()
        # 문장이 끝나는 곳에서 줄을 나눈다(낱말 몇 개만 다음 줄로 넘어가지 않게).
        body.setText(confirm.break_lines(i18n.t("share.body"), body.font(),
                                         _WIDTH - 40))
        root.addWidget(body)
        self._field = password_field(i18n.t("connect.password_placeholder"))
        self._field.setAccessibleName(i18n.t("connect.field_password"))
        line = QHBoxLayout()
        line.setSpacing(10)
        line.addWidget(QLabel(i18n.t("connect.field_password")))
        line.addWidget(self._field, 1)
        root.addLayout(line)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        ok = QPushButton(i18n.t("copies.takeover_action"))
        ok.setMinimumWidth(104)
        ok.setCursor(Qt.PointingHandCursor)
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        cancel = QPushButton(i18n.t("common.btn_cancel"))
        cancel.setProperty("secondary", True)
        refresh_style(cancel)
        cancel.setMinimumWidth(104)
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(ok)
        buttons.addWidget(cancel)
        root.addLayout(buttons)

    def password(self) -> str:
        return clean_password(self._field.text())


def ask_share_start(parent: QWidget | None) -> str | None:
    """공유를 시작할지 묻는다. 시작하면 걸 비밀번호(안 걸면 빈 글), 그만두면 None
    (시험에서는 이 함수를 바꿔 끼운다)."""
    dialog = ShareStartDialog(parent)
    try:
        return dialog.password() if dialog.exec() == QDialog.Accepted else None
    finally:
        dialog.deleteLater()


__all__ = ["PasswordDialog", "ShareStartDialog", "ask", "ask_share_start",
           "password_field"]
