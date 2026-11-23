from __future__ import annotations

import getpass
from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import config, i18n
from ..network.discovery import Searcher
from ..share_auth import clean_password
from .icons import svg_icon
from .password_dialog import password_field
from .theme import COLOR_FOCUS, COLOR_TEXT_SUBTLE, refresh_style

# 찾은 공유 PC 는 이만큼까지만 보인다(창이 길어지지 않게).
MAX_FOUND_ROWS = 5


@dataclass
class ConnectionChoice:
    mode: str               # 'host' | 'guest'
    nickname: str
    port: int
    host_ip: str = ""
    heartbeat_ms: int = config.DEFAULT_HEARTBEAT_MS
    # 공유에 걸 비밀번호(공유 시작일 때). 빈 글이면 걸지 않는다. None 은 접속
    # 창을 거치지 않았다는 뜻이다: 적어 둔 것을 그대로 쓴다.
    password: str | None = None


def default_nickname() -> str:
    """처음 쓰는 사람의 이름 칸에 미리 넣어 줄 값: Windows 사용자 이름.

    예전 기본값은 host·guest 였다. 여럿이 그대로 두면 이름이 겹치고, 편집 잠금과
    '나만 보기'가 이름으로 갈리므로 서로 구별되지 않았다."""
    try:
        return (getpass.getuser() or "").strip()
    except Exception:
        return ""


class _RoleButton(QPushButton):
    """시작 방식 하나. 제목과 한 줄 설명을 함께 보인다."""

    def __init__(self, title: str, subtitle: str):
        super().__init__()
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(60)
        self.setMinimumWidth(360)
        self.setAccessibleName(f"{title}. {subtitle}")
        self.setStyleSheet(f"""
            QPushButton {{
                text-align: left;
                background: #F5F5F5;
                color: #1F1F1F;
                border: 1px solid #D5D5D5;
                border-radius: 10px;
                padding: 10px 14px;
                font-weight: 600;
            }}
            QPushButton:hover {{ background: #EBEBEB; }}
            QPushButton:checked {{
                background: #1F1F1F;
                color: white;
                border-color: #1F1F1F;
            }}
            QPushButton[kbFocus="true"] {{
                border: 2px solid {COLOR_FOCUS};
                padding: 9px 13px;
            }}
        """)
        self.setText(f"{title}\n{subtitle}")


class _FoundRow(QPushButton):
    """내부망에서 찾은 공유 PC 한 줄: 이름, 비밀번호가 걸렸으면 자물쇠, 주소.
    누르면 주소 칸에 들어간다."""

    picked = Signal(dict)

    def __init__(self, found: dict):
        super().__init__()
        self._found = found
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(30)
        where = found["address"]
        if found["port"] != config.DEFAULT_PORT:
            where += f":{found['port']}"
        self.setAccessibleName(f"{found['host']}, {where}")
        self.setStyleSheet(f"""
            QPushButton {{ background: transparent; border: none;
                           border-radius: 6px; padding: 0; }}
            QPushButton:hover {{ background: #F2F2F2; }}
            QPushButton:checked {{ background: #E9E9E9; }}
            QPushButton[kbFocus="true"] {{ border: 1px solid {COLOR_FOCUS}; }}
        """)
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 0, 8, 0)
        row.setSpacing(6)
        name = QLabel(found["host"])
        name.setTextFormat(Qt.PlainText)            # 남이 보낸 이름이다
        name.setStyleSheet("font-weight: 600; color: #1F1F1F; background: transparent;")
        row.addWidget(name)
        if found.get("locked"):
            lock = QLabel()
            lock.setPixmap(svg_icon("lock", 13, "#6B7280").pixmap(13, 13))
            lock.setStyleSheet("background: transparent;")
            row.addWidget(lock)
        row.addStretch(1)
        address = QLabel(where)
        address.setTextFormat(Qt.PlainText)
        address.setStyleSheet(
            f"font-size: 12px; color: {COLOR_TEXT_SUBTLE}; background: transparent;")
        row.addWidget(address)
        for label in self.findChildren(QLabel):
            label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.clicked.connect(lambda: self.picked.emit(self._found))


class ConnectDialog(QDialog):
    def __init__(self, last_nickname: str = "",
                 last_ip: str = "127.0.0.1",
                 last_port: int = config.DEFAULT_PORT,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(i18n.t("connect.window_title", app=i18n.t("app.name")))
        self.setModal(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 16)
        root.setSpacing(12)
        # 고른 방식과 '고급 설정'에 따라 칸 수가 달라진다. 창이 늘 내용에 맞게 한다.
        root.setSizeConstraint(QLayout.SetFixedSize)

        title = QLabel(i18n.t("connect.choose_mode"))
        title.setProperty("role", "title")
        root.addWidget(title)

        self._btn_host = _RoleButton(i18n.t("connect.role_host_title"),
                                      i18n.t("connect.role_host_subtitle"))
        self._btn_guest = _RoleButton(i18n.t("connect.role_guest_title"),
                                       i18n.t("connect.role_guest_subtitle"))
        self._btn_host.setChecked(True)
        self._btn_host.clicked.connect(lambda: self._set_mode(0))
        self._btn_guest.clicked.connect(lambda: self._set_mode(1))
        # 나란히 두면 영어·일본어에서 글자가 잘렸다. 위아래로 쌓는다.
        root.addWidget(self._btn_host)
        root.addWidget(self._btn_guest)

        nick = (last_nickname or "").strip() or default_nickname()
        self._advanced_rows: list[tuple[QFormLayout, QWidget]] = []
        self._mode = 0
        self._host_form = self._build_host_form(nick)
        self._guest_form = self._build_guest_form(nick, last_ip, last_port)
        root.addWidget(self._host_form)
        root.addWidget(self._guest_form)
        self._guest_form.hide()

        # 포트와 연결 확인 주기는 거의 손댈 일이 없다. 접어 둔다.
        self._advanced_btn = QToolButton()
        self._advanced_btn.setText(i18n.t("connect.advanced"))
        self._advanced_btn.setCheckable(True)
        self._advanced_btn.setCursor(Qt.PointingHandCursor)
        self._advanced_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._advanced_btn.setArrowType(Qt.RightArrow)
        self._advanced_btn.setStyleSheet(
            "QToolButton { border: none; background: transparent; "
            "color: #4B5563; font-size: 12px; font-weight: 600; padding: 2px 0; }"
            f"QToolButton[kbFocus=\"true\"] {{ border: 1px solid {COLOR_FOCUS}; "
            "border-radius: 4px; }")
        self._advanced_btn.toggled.connect(self._set_advanced)
        root.addWidget(self._advanced_btn, 0, Qt.AlignLeft)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        buttons.button(QDialogButtonBox.Ok).setText(i18n.t("connect.btn_start"))
        cancel_btn = buttons.button(QDialogButtonBox.Cancel)
        cancel_btn.setText(i18n.t("connect.btn_cancel"))
        cancel_btn.setProperty("secondary", True)
        refresh_style(cancel_btn)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._choice: ConnectionChoice | None = None
        self._set_advanced(False)
        # 내부망에서 공유 중인 PC 를 찾는다(참여를 골랐을 때).
        self._searcher = Searcher(parent=self)
        self._searcher.finished.connect(self._show_found)

    def _set_mode(self, idx: int) -> None:
        self._mode = idx
        self._btn_host.setChecked(idx == 0)
        self._btn_guest.setChecked(idx == 1)
        self._host_form.setVisible(idx == 0)
        self._guest_form.setVisible(idx == 1)
        if idx == 1:
            self._searcher.search()

    def _show_found(self, found: list) -> None:
        """찾은 공유 PC 를 주소 칸 아래에 늘어놓는다. 없으면 줄도 없다."""
        for row in self._found_rows:
            row.setParent(None)
            row.deleteLater()
        self._found_rows = []
        for item in found[:MAX_FOUND_ROWS]:
            row = _FoundRow(item)
            row.picked.connect(self._pick_found)
            self._found_layout.addWidget(row)
            self._found_rows.append(row)
        self._guest_layout.setRowVisible(self._found_box, bool(self._found_rows))
        # 하나뿐이고 주소를 아직 넣지 않았으면 그 PC 를 넣어 둔다.
        untouched = (not self._guest_ip.isModified()
                     and self._guest_ip.text().strip() in ("", "127.0.0.1"))
        if len(self._found_rows) == 1 and untouched:
            self._pick_found(found[0])

    def _pick_found(self, found: dict) -> None:
        self._guest_ip.setText(found["address"])
        self._guest_port.setValue(int(found["port"]))
        # 신호로 건너온 것은 사본이다. 주소로 견준다.
        picked = (found["address"], found["port"])
        for row in self._found_rows:
            row.setChecked((row._found["address"], row._found["port"]) == picked)

    def found(self) -> list[dict]:
        return [row._found for row in self._found_rows]

    def _set_advanced(self, shown: bool) -> None:
        self._advanced_btn.setArrowType(Qt.DownArrow if shown else Qt.RightArrow)
        for form, field in self._advanced_rows:
            form.setRowVisible(field, shown)

    @staticmethod
    def _form(parent: QWidget) -> QFormLayout:
        form = QFormLayout(parent)
        form.setContentsMargins(0, 6, 0, 0)
        form.setSpacing(10)
        return form

    def _add_advanced(self, form: QFormLayout, text: str, field: QWidget) -> None:
        form.addRow(text, field)
        self._advanced_rows.append((form, field))

    def _build_host_form(self, nickname: str) -> QWidget:
        w = QWidget()
        form = self._form(w)
        self._host_nickname = QLineEdit(nickname)
        self._host_nickname.setMaxLength(config.MAX_NICKNAME_LENGTH)
        self._host_port = QSpinBox()
        self._host_port.setRange(1, 65535)
        self._host_port.setValue(config.DEFAULT_PORT)
        # 밀리초가 아니라 초로 적는다(3000 ms → 3초).
        self._host_heartbeat = QDoubleSpinBox()
        self._host_heartbeat.setDecimals(1)
        self._host_heartbeat.setRange(0.5, 60.0)
        self._host_heartbeat.setSingleStep(0.5)
        self._host_heartbeat.setSuffix(i18n.t("connect.unit_seconds"))
        self._host_heartbeat.setValue(config.DEFAULT_HEARTBEAT_MS / 1000)
        form.addRow(i18n.t("connect.field_nickname"), self._host_nickname)
        # 비워 두면 비밀번호 없이 공유한다.
        self._host_password = password_field(i18n.t("connect.password_placeholder"))
        form.addRow(i18n.t("connect.field_password"), self._host_password)
        self._add_advanced(form, i18n.t("connect.field_port"), self._host_port)
        self._add_advanced(form, i18n.t("connect.field_heartbeat"),
                           self._host_heartbeat)
        return w

    def _build_guest_form(self, nickname: str, last_ip: str,
                         last_port: int) -> QWidget:
        w = QWidget()
        form = self._form(w)
        self._guest_nickname = QLineEdit(nickname)
        self._guest_nickname.setMaxLength(config.MAX_NICKNAME_LENGTH)
        self._guest_ip = QLineEdit(last_ip)
        self._guest_ip.setPlaceholderText(i18n.t("connect.host_ip_placeholder"))
        self._guest_port = QSpinBox()
        self._guest_port.setRange(1, 65535)
        self._guest_port.setValue(last_port)
        form.addRow(i18n.t("connect.field_nickname"), self._guest_nickname)
        form.addRow(i18n.t("connect.field_host_ip"), self._guest_ip)
        hint = QLabel(i18n.t("connect.host_ip_hint"))
        hint.setProperty("role", "subtle")
        hint.setWordWrap(True)
        form.addRow("", hint)
        self._found_box = QWidget()
        self._found_layout = QVBoxLayout(self._found_box)
        self._found_layout.setContentsMargins(0, 0, 0, 0)
        self._found_layout.setSpacing(2)
        self._found_rows: list[_FoundRow] = []
        form.addRow(i18n.t("connect.found"), self._found_box)
        form.setRowVisible(self._found_box, False)
        self._guest_layout = form
        self._add_advanced(form, i18n.t("connect.field_port"), self._guest_port)
        return w

    def accept(self) -> None:
        if self._mode == 0:
            nick = (self._host_nickname.text().strip() or default_nickname()
                    or "host")
            self._choice = ConnectionChoice(
                mode="host", nickname=nick,
                port=self._host_port.value(),
                heartbeat_ms=int(round(self._host_heartbeat.value() * 1000)),
                password=clean_password(self._host_password.text()),
            )
        else:
            nick = (self._guest_nickname.text().strip() or default_nickname()
                    or "guest")
            self._choice = ConnectionChoice(
                mode="guest", nickname=nick,
                port=self._guest_port.value(),
                host_ip=self._guest_ip.text().strip() or "127.0.0.1",
            )
        super().accept()

    def choice(self) -> ConnectionChoice | None:
        return self._choice
