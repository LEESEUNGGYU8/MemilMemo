"""참가자 창: 공유 PC 의 주소와 지금 접속한 사람.

이 창에서 사람이 찾는 것은 둘이다. 다른 PC 에 알려 줄 주소, 그리고 누가 들어와
있는가. 그래서 주소를 맨 위에 두고 복사 단추를 붙이며, 사람은 접속한 사람과
지난 접속자로만 나눈다. 연결 확인 주기 같은 내부 값은 적지 않는다.

모양은 메인 창과 같다. 흰 바탕, 옅은 회색 칸, 검은 단추. 사람 표시의 색은 메모
색에서 가져온다."""
from __future__ import annotations

from typing import Mapping

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import config, i18n
from ..local_state import UserRecord
from .theme import RoundedScrollBar

# 사람 표시에 쓰는 색. 어두운 메모 색과 흰색은 글자가 묻혀 뺀다.
_AVATAR_COLORS = [c for c in config.COLORS if c not in ("#F5F5F5", "#3A3A3A")]
_SURFACE = "#FAFBFD"
_LINE = "#ECECEC"


def _avatar_color(nick: str, *, online: bool) -> QColor:
    if not online:
        return QColor("#EDEDED")
    # 글자 자리까지 섞어, 성이 같거나 글자 합이 비슷한 이름이 같은 색으로 몰리지 않게 한다.
    seed = 0
    for i, ch in enumerate(nick or ""):
        seed = (seed * 31 + ord(ch) * (i + 1)) % 100003
    return QColor(_AVATAR_COLORS[seed % len(_AVATAR_COLORS)])


def _avatar_pixmap(nick: str, *, online: bool, size: int = 32) -> QPixmap:
    scale = 2
    pix = QPixmap(size * scale, size * scale)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(_avatar_color(nick, online=online))
    p.drawEllipse(0, 0, size * scale, size * scale)
    letter = (nick[0] if nick else "?").upper()
    f = p.font()
    f.setBold(True)
    f.setPixelSize(int(size * scale * 0.44))
    p.setFont(f)
    p.setPen(QColor("#1F1F1F" if online else "#8A8A8A"))
    p.drawText(0, 0, size * scale, size * scale, Qt.AlignCenter, letter)
    p.end()
    pix.setDevicePixelRatio(scale)
    return pix


class _Chip(QLabel):
    def __init__(self, text: str, *, fg: str, bg: str, parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setStyleSheet(
            f"background:{bg}; color:{fg}; "
            "border-radius: 7px; padding: 2px 8px; "
            "font-size: 10.5px; font-weight: 700;"
        )


class _SectionLabel(QWidget):
    def __init__(self, title: str, count: int, parent: QWidget | None = None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 8, 4, 4)
        row.setSpacing(8)
        lbl = QLabel(title)
        lbl.setStyleSheet("font-size: 12px; font-weight: 700; color: #4B5563;")
        row.addWidget(lbl, 0, Qt.AlignVCenter)
        chip = QLabel(str(count))
        chip.setStyleSheet(
            "background: rgba(17, 24, 39, 0.07); color: #4B5563; "
            "border-radius: 9px; padding: 1px 8px; "
            "font-size: 10.5px; font-weight: 700;")
        row.addWidget(chip, 0, Qt.AlignVCenter)
        row.addStretch(1)


class _UserCard(QFrame):
    """한 사람. 접속 여부는 묶음(참가자 목록, 지난 접속자)이 알려 주므로 줄마다
    되풀이해 적지 않는다."""

    def __init__(
        self,
        nickname: str,
        *,
        is_host: bool,
        is_self: bool,
        online: bool,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("ParticipantCard")
        self.setStyleSheet(
            "QFrame#ParticipantCard {"
            "  background: #FFFFFF;"
            f"  border: 1px solid {_LINE};"
            "  border-radius: 10px;"
            "}"
        )
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 9, 14, 9)
        row.setSpacing(10)

        avatar = QLabel()
        avatar.setFixedSize(32, 32)
        avatar.setPixmap(_avatar_pixmap(nickname, online=online))
        row.addWidget(avatar, 0, Qt.AlignVCenter)

        name_lbl = QLabel(nickname)
        name_lbl.setStyleSheet(
            "font-size: 13.5px; font-weight: 700; "
            f"color: {'#1F1F1F' if online else '#707070'};")
        row.addWidget(name_lbl, 0, Qt.AlignVCenter)
        if is_host:
            row.addWidget(_Chip(i18n.t("sessions.chip_host"),
                                fg="#FFFFFF", bg="#1F1F1F"), 0, Qt.AlignVCenter)
        if is_self:
            row.addWidget(_Chip(i18n.t("sessions.chip_self"),
                                fg="#374151", bg="#E5E7EB"), 0, Qt.AlignVCenter)
        row.addStretch(1)
        state = (i18n.t("sessions.status_online") if online
                 else i18n.t("sessions.status_offline"))
        self.setAccessibleName(f"{nickname}, {state}")


class _SessionHeader(QFrame):
    """공유 PC 의 이름과 주소. 주소는 다른 PC 가 접속할 때 넣는 값이다."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("SessionHeader")
        self.setStyleSheet(
            "QFrame#SessionHeader {"
            f"  background: {_SURFACE};"
            f"  border: 1px solid {_LINE};"
            "  border-radius: 14px;"
            "}"
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 14, 16, 14)
        outer.setSpacing(4)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)
        caption = QLabel(i18n.t("sessions.caption_host"))
        caption.setStyleSheet(
            "color: #6B7280; font-size: 11px; font-weight: 700;")
        top.addWidget(caption, 0, Qt.AlignVCenter)
        top.addStretch(1)
        self._count_pill = QLabel("")
        self._count_pill.setStyleSheet(
            "background: rgba(17, 24, 39, 0.07); color: #1F2937; "
            "border-radius: 10px; padding: 3px 10px; "
            "font-size: 11.5px; font-weight: 700;")
        top.addWidget(self._count_pill, 0, Qt.AlignVCenter)
        outer.addLayout(top)

        self._host_name = QLabel("")
        self._host_name.setStyleSheet(
            "color: #1F1F1F; font-size: 17px; font-weight: 800;")
        self._host_name.setTextInteractionFlags(Qt.TextSelectableByMouse)
        outer.addWidget(self._host_name)

        addr_row = QHBoxLayout()
        addr_row.setContentsMargins(0, 2, 0, 0)
        addr_row.setSpacing(10)
        self._addr_value = QLabel("")
        self._addr_value.setStyleSheet(
            "color: #374151; font-size: 13px; font-weight: 600;")
        self._addr_value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        addr_row.addWidget(self._addr_value, 0, Qt.AlignVCenter)
        addr_row.addStretch(1)
        # 다른 PC 가 '공유 PC 주소'에 넣을 값이다. 바로 옮겨 적을 수 있게 한다.
        self._copy_btn = QToolButton()
        self._copy_btn.setText(i18n.t("sessions.btn_copy_address"))
        self._copy_btn.setCursor(Qt.PointingHandCursor)
        self._copy_btn.setStyleSheet(
            "QToolButton { border: 1px solid #D5D5D5; border-radius: 8px; "
            "background: #FFFFFF; color: #1F1F1F; font-size: 11.5px; "
            "font-weight: 700; padding: 5px 12px; }"
            "QToolButton:hover { background: #F3F4F6; }"
            "QToolButton:pressed { background: #E5E7EB; }")
        self._copy_btn.clicked.connect(self._copy_address)
        addr_row.addWidget(self._copy_btn, 0, Qt.AlignVCenter)
        outer.addLayout(addr_row)

        self._copy_reset = QTimer(self)
        self._copy_reset.setSingleShot(True)
        self._copy_reset.setInterval(1500)
        self._copy_reset.timeout.connect(
            lambda: self._copy_btn.setText(i18n.t("sessions.btn_copy_address")))

    def update_text(self, *, host: str, online_count: int,
                    peer_address: str) -> None:
        self._host_name.setText(host or i18n.t("sessions.host_unknown"))
        self._count_pill.setText(
            i18n.t("sessions.count_online", online=online_count))
        self._addr_value.setText(
            peer_address or i18n.t("sessions.address_unknown"))
        self._copy_btn.setVisible(bool(peer_address))

    def _copy_address(self) -> None:
        QGuiApplication.clipboard().setText(self._addr_value.text())
        self._copy_btn.setText(i18n.t("sessions.copied"))
        self._copy_reset.start()


class SessionsDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(i18n.t("sessions.window_title"))
        self.resize(420, 520)
        self.setMinimumSize(360, 340)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 14)
        root.setSpacing(10)

        self._header = _SessionHeader()
        root.addWidget(self._header)

        # 뷰포트를 transparent 로 둬야 QScrollArea 의 둥근 배경이 드러난다.
        # 기본값으로 두면 뷰포트의 Base 팔레트 색이 사각형으로 모서리를 덮는다.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setVerticalScrollBar(RoundedScrollBar())
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            f"QScrollArea {{ background: {_SURFACE}; "
            f"border: 1px solid {_LINE}; border-radius: 14px; }}"
        )
        scroll.viewport().setStyleSheet("background: transparent;")
        scroll.viewport().setAutoFillBackground(False)

        self._content = QWidget()
        self._content.setStyleSheet("background: transparent;")
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(10, 8, 10, 10)
        self._content_layout.setSpacing(6)
        self._content_layout.addStretch(1)
        scroll.setWidget(self._content)
        root.addWidget(scroll, 1)

        self._empty = QLabel(i18n.t("sessions.empty_hint"))
        self._empty.setAlignment(Qt.AlignCenter)
        self._empty.setStyleSheet(
            "color:#707070; font-size: 12px; padding: 18px;")
        self._empty.hide()
        root.addWidget(self._empty)

        buttons = QDialogButtonBox(QDialogButtonBox.Close, parent=self)
        close_btn = buttons.button(QDialogButtonBox.Close)
        close_btn.setText(i18n.t("sessions.btn_close"))
        close_btn.setMinimumWidth(96)
        close_btn.setCursor(Qt.PointingHandCursor)
        buttons.rejected.connect(self.reject)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(buttons)
        root.addLayout(bottom)

    def refresh(
        self,
        *,
        host: str,
        online: set[str],
        my_nickname: str,
        my_role: str,
        records: Mapping[str, UserRecord],
        connected: bool,
        heartbeat_ms: int = 0,
        peer_address: str = "",
    ) -> None:
        all_nicks: set[str] = set(online) | set(records.keys())
        if host:
            all_nicks.add(host)
        if my_nickname:
            all_nicks.add(my_nickname)

        online_nicks = [n for n in all_nicks if n in online]
        offline_nicks = [n for n in all_nicks if n not in online]

        # 온라인 정렬: 본인을 맨 위, 그다음 호스트(세션의 앵커), 나머지는
        # 알파벳 순으로 둔다.
        def online_sort_key(nick: str):
            return (
                0 if nick == my_nickname else 1,
                0 if nick == host else 1,
                nick.lower(),
            )

        # 오프라인: 최근에 본 사람 우선.
        def offline_sort_key(nick: str):
            rec = records.get(nick)
            seen = rec.last_seen_at if rec is not None else 0
            return (-seen, nick.lower())

        online_nicks.sort(key=online_sort_key)
        offline_nicks.sort(key=offline_sort_key)

        self._header.update_text(
            host=host,
            online_count=len(online_nicks),
            peer_address=peer_address,
        )

        self._clear_content()
        if not all_nicks:
            self._empty.show()
            self._content.hide()
            return
        self._empty.hide()
        self._content.show()

        for title, nicks, is_online in (
                (i18n.t("sessions.section_online"), online_nicks, True),
                (i18n.t("sessions.section_offline"), offline_nicks, False)):
            if not nicks:
                continue
            self._add_section(title, len(nicks))
            for nick in nicks:
                self._add_user_card(nick, online=is_online,
                                    is_host=(nick == host),
                                    is_self=(nick == my_nickname))

    def _clear_content(self) -> None:
        while self._content_layout.count() > 1:
            item = self._content_layout.takeAt(0)
            if item is None:
                break
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def _add_section(self, title: str, count: int) -> None:
        sec = _SectionLabel(title, count)
        self._content_layout.insertWidget(
            self._content_layout.count() - 1, sec)

    def _add_user_card(self, nickname: str, *, online: bool, is_host: bool,
                       is_self: bool) -> None:
        card = _UserCard(nickname=nickname, is_host=is_host, is_self=is_self,
                         online=online)
        self._content_layout.insertWidget(
            self._content_layout.count() - 1, card)
