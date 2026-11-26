"""메인 창의 틀: 왼쪽 옆줄, 상단바의 본인 아이콘과 접속 정보, 보기 고르기.

메인 창(``main_window``)은 메모와 연결을 다루고, 여기에는 그 둘레의 위젯만 둔다.

* 옆줄: '새 메모', 거르기(전체 메모, 표시, 숨김, 알림), 폴더(메모 수), 아래에
  휴지통, 수정 이력, 설정. 거르기와 폴더는 하나만 고른다.
* 본인 아이콘: 상단바 최소화 단추 왼쪽. 사람마다의 색에 이름 첫 글자, 연결 상태 점
  (연결됨 초록, 끊김 빨강, 혼자 쓰면 없음). 누르면 접속 정보가 열린다.
* 접속 정보: 나(이름, 역할, 연결 상태), 공유 PC 주소, 지금 접속한 사람과 편집 중인
  메모, '접속 정보 바꾸기', 혼자 쓸 때 '공유 시작', 끊겼을 때 '지금 다시 연결'.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QEvent, QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QIcon, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QScrollArea,
)

from .. import config, i18n
from . import theme
from .icons import svg_icon

RAIL_WIDTH = 212
NARROW_BELOW = 560          # 창 폭이 이보다 좁으면 옆줄을 접는다

PLACE_ALL, PLACE_SHOWN, PLACE_HIDDEN, PLACE_REMIND = "all", "shown", "hidden", "remind"
PLACE_TRASH, PLACE_FOLDER, PLACE_NONE = "trash", "folder", "none"
# 서식: 안에서는 폴더 하나지만 폴더 목록과 따로 보인다.

# 사람마다의 색(참가자 창과 같다). 어두운 메모 색과 흰색은 글자가 묻혀 뺀다.
_AVATAR_COLORS = list(config.COLORS[:6])


def person_color(nick: str) -> QColor:
    seed = 0
    for i, ch in enumerate(nick or ""):
        seed = (seed * 31 + ord(ch) * (i + 1)) % 100003
    return QColor(_AVATAR_COLORS[seed % len(_AVATAR_COLORS)])


def _paint_avatar(p: QPainter, rect: QRectF, nick: str, *, online: bool = True) -> None:
    p.setPen(Qt.NoPen)
    p.setBrush(person_color(nick) if online else QColor("#EDEDED"))
    p.drawEllipse(rect)
    font = QFont(p.font())
    font.setBold(True)
    font.setPixelSize(max(9, int(rect.height() * 0.44)))
    p.setFont(font)
    p.setPen(QColor("#1F1F1F" if online else "#8A8A8A"))
    p.drawText(rect, Qt.AlignCenter, (nick[:1] or "?").upper())


# ── 옆줄 ──

class RailButton(QAbstractButton):
    """옆줄의 한 줄: 그림, 이름, 오른쪽에 수. 고른 줄은 옅은 회색 바탕에 굵게."""

    HEIGHT = 34

    def __init__(self, icon: QIcon, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._icon = icon
        self._count = ""
        self.setText(text)
        self.setCheckable(True)
        self.setAutoExclusive(False)
        self.setFixedHeight(self.HEIGHT)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.TabFocus)
        self.setAttribute(Qt.WA_Hover)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAccessibleName(text)

    def set_icon(self, icon: QIcon) -> None:
        self._icon = icon
        self.update()

    def set_count(self, count) -> None:
        text = "" if count in (None, "") else str(count)
        if text != self._count:
            self._count = text
            self.setAccessibleName(f"{self.text()} {text}".strip())
            self.update()

    def count_text(self) -> str:
        return self._count

    def nextCheckState(self) -> None:  # noqa: N802
        """눌러도 스스로 켜고 끄지 않는다(고른 곳은 옆줄이 정한다). 이미 고른 곳을
        다시 눌러도 깜빡이지 않는다."""

    def sizeHint(self) -> QSize:
        return QSize(RAIL_WIDTH - 24, self.HEIGHT)

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self.isChecked() or self.isDown():
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#E9E9E9"))
            p.drawRoundedRect(rect, 8, 8)
        elif self.underMouse():
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#F0F0F0"))
            p.drawRoundedRect(rect, 8, 8)
        if self.hasFocus() and self.focusPolicy() != Qt.NoFocus:
            p.setPen(QPen(QColor(theme.COLOR_FOCUS), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 7, 7)
        self._icon.paint(p, 10, (self.height() - 16) // 2, 16, 16)
        font = QFont(self.font())
        font.setPixelSize(13)
        font.setWeight(QFont.Bold if self.isChecked() else QFont.Medium)
        p.setFont(font)
        right = self.width() - 10
        if self._count:
            count_font = QFont(font)
            count_font.setPixelSize(12)
            count_font.setWeight(QFont.Medium)
            p.setFont(count_font)
            p.setPen(QColor("#707070"))
            w = p.fontMetrics().horizontalAdvance(self._count)
            p.drawText(QRectF(right - w, 0, w, self.height()), Qt.AlignVCenter, self._count)
            right -= w + 10
            p.setFont(font)
        p.setPen(QColor("#1F1F1F" if self.isChecked() else "#3A3A3A"))
        text = p.fontMetrics().elidedText(self.text(), Qt.ElideRight, int(right - 36))
        p.drawText(QRectF(36, 0, right - 36, self.height()), Qt.AlignVCenter, text)


@dataclass
class FolderRow:
    folder_id: str
    name: str
    color: str
    private: bool
    count: int


class Rail(QWidget):
    """왼쪽 옆줄. 고르면 ``placeChosen(종류, 폴더 id)`` 를 낸다."""

    placeChosen = Signal(str, str)
    newNoteRequested = Signal()
    tagChosen = Signal(str)                    # #태그 를 눌렀다
    newFolderRequested = Signal()
    folderMenuRequested = Signal(str, QPoint)
    historyRequested = Signal()
    settingsRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("MainRail")
        self.setFixedWidth(RAIL_WIDTH)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            "QWidget#MainRail { background: #FAFAFA; border-right: 1px solid #EFEFEF; }")
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(2)
        self.new_button = QPushButton(i18n.t("main.btn_new_note"))
        self.new_button.setIcon(svg_icon("plus", color="#FFFFFF"))
        self.new_button.setIconSize(QSize(14, 14))
        self.new_button.setCursor(Qt.PointingHandCursor)
        self.new_button.setToolTip(i18n.t("main.tooltip_new_note"))
        self.new_button.setStyleSheet(
            "QPushButton { min-height: 20px; padding: 9px 12px; }")
        self.new_button.clicked.connect(self.newNoteRequested)
        v.addWidget(self.new_button)
        v.addSpacing(10)
        self.places: dict[str, RailButton] = {}
        for key, icon, label in ((PLACE_ALL, "list", "main.place_all"),
                                 (PLACE_SHOWN, "eye_on", "main.place_shown"),
                                 (PLACE_HIDDEN, "eye_off", "main.place_hidden"),
                                 (PLACE_REMIND, "bell", "main.place_remind")):
            b = RailButton(svg_icon(icon, color="#4A4A4A"), i18n.t(label))
            b.clicked.connect(lambda _c=False, k=key: self.placeChosen.emit(k, ""))
            self.places[key] = b
            v.addWidget(b)
        v.addSpacing(14)
        head = QHBoxLayout()
        head.setContentsMargins(10, 0, 0, 0)
        caption = QLabel(i18n.t("main.rail_folders"))
        caption.setProperty("role", "sectionHeader")
        head.addWidget(caption)
        head.addStretch(1)
        self.new_folder_button = QToolButton()
        self.new_folder_button.setIcon(svg_icon("plus", color="#4A4A4A"))
        self.new_folder_button.setIconSize(QSize(14, 14))
        self.new_folder_button.setToolTip(i18n.t("main.tooltip_new_folder"))
        self.new_folder_button.setAccessibleName(i18n.t("main.tooltip_new_folder"))
        self.new_folder_button.setCursor(Qt.PointingHandCursor)
        self.new_folder_button.setFixedSize(26, 26)
        self.new_folder_button.setStyleSheet(
            "QToolButton { border: none; border-radius: 6px; background: transparent; }"
            "QToolButton:hover { background: #EDEDED; }")
        self.new_folder_button.clicked.connect(self.newFolderRequested)
        head.addWidget(self.new_folder_button)
        # 폴더와 태그 줄은 한 칸에 두고, 많으면 그 칸만 굴러간다(줄을 누르지 않는다).
        # '폴더' 제목도 그 안에 둔다(태그까지 굴려도 제목이 맞다).
        self._list_area = QScrollArea(self)
        self._list_area.setObjectName("RailList")
        self._list_area.setFrameShape(QFrame.NoFrame)
        self._list_area.setWidgetResizable(True)
        self._list_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # 막대는 내용 옆에 자리를 잡지 않고 위에 얹는다(수의 세로줄이 위 칸과 같다).
        self._list_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # 막대는 옆줄의 오른쪽 여백에 둔다(줄의 수와 '+' 를 덮지 않는다).
        self._overlay_bar = theme.RoundedScrollBar(parent=self)
        # 잘린 줄 말고는 굴러간다는 표시가 이것뿐이라 조금 짙게.
        self._overlay_bar.set_colors("#B4B4B4", "#8F8F8F")
        self._overlay_bar.setFixedWidth(8)
        self._overlay_bar.hide()
        hidden_bar = self._list_area.verticalScrollBar()
        hidden_bar.rangeChanged.connect(self._sync_overlay_bar)
        hidden_bar.valueChanged.connect(self._overlay_bar.setValue)
        self._overlay_bar.valueChanged.connect(hidden_bar.setValue)
        self._list_area.installEventFilter(self)
        self._list_area.setStyleSheet(
            "QScrollArea#RailList, QScrollArea#RailList > QWidget > QWidget"
            " { background: transparent; }")
        inner = QWidget()
        inner.setAttribute(Qt.WA_TranslucentBackground)
        inner_v = QVBoxLayout(inner)
        inner_v.setContentsMargins(0, 0, 0, 0)
        inner_v.setSpacing(2)
        inner_v.addLayout(head)
        inner_v.addSpacing(2)
        self._folder_box = QVBoxLayout()
        self._folder_box.setContentsMargins(0, 0, 0, 0)
        self._folder_box.setSpacing(2)
        inner_v.addLayout(self._folder_box)
        self.folders: dict[str, RailButton] = {}
        # #태그: 본문에 쓴 태그(많이 쓴 것부터). 없으면 감춘다.
        self._tag_caption = QLabel(i18n.t("main.rail_tags"))
        self._tag_caption.setProperty("role", "sectionHeader")
        self._tag_caption.setContentsMargins(10, 14, 0, 4)
        self._tag_caption.hide()
        inner_v.addWidget(self._tag_caption)
        self._tag_box = QVBoxLayout()
        self._tag_box.setContentsMargins(0, 0, 0, 0)
        self._tag_box.setSpacing(2)
        inner_v.addLayout(self._tag_box)
        inner_v.addStretch(1)
        self._list_area.setWidget(inner)
        v.addWidget(self._list_area, 1)
        self.tags: dict[str, RailButton] = {}
        self._tag_sig: tuple = ()
        # 아래 묶음(휴지통, 수정 이력, 설정) 앞의 가는 선.
        v.addSpacing(6)
        rule = QFrame(self)
        rule.setFixedHeight(1)
        rule.setStyleSheet("background: #EFEFEF; border: none;")
        v.addWidget(rule)
        v.addSpacing(6)
        self.trash_button = RailButton(svg_icon("trash", color="#4A4A4A"),
                                       i18n.t("main.folder_trash"))
        self.trash_button.clicked.connect(lambda: self.placeChosen.emit(PLACE_TRASH, ""))
        self.history_button = RailButton(svg_icon("clock", color="#4A4A4A"),
                                         i18n.t("main.rail_history"))
        self.history_button.setCheckable(False)
        self.history_button.clicked.connect(self.historyRequested)
        self.settings_button = RailButton(svg_icon("settings", color="#4A4A4A"),
                                          i18n.t("main.tooltip_settings"))
        self.settings_button.setCheckable(False)
        self.settings_button.clicked.connect(self.settingsRequested)
        for b in (self.trash_button, self.history_button, self.settings_button):
            v.addWidget(b)

    def set_folders(self, rows: list[FolderRow], *, none_count: int | None = None) -> None:
        """폴더 줄을 다시 놓는다. ``none_count`` 를 주면 맨 끝에 '미분류'를 둔다."""
        while self._folder_box.count():
            item = self._folder_box.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()            # 지워질 때까지 옛 줄이 새 줄 위에 남지 않게
                w.deleteLater()
        self.folders = {}
        for row in rows:
            icon = svg_icon("lock" if row.private else "folder", color="#4A4A4A")
            b = RailButton(icon, row.name)
            b.set_count(row.count)
            b.clicked.connect(lambda _c=False, f=row.folder_id: self.placeChosen.emit(PLACE_FOLDER, f))
            b.setContextMenuPolicy(Qt.CustomContextMenu)
            b.customContextMenuRequested.connect(
                lambda pos, f=row.folder_id, button=b:
                self.folderMenuRequested.emit(f, button.mapToGlobal(pos)))
            self.folders[row.folder_id] = b
            self._folder_box.addWidget(b)
        if none_count is not None:
            b = RailButton(svg_icon("folder", color="#9A9A9A"), i18n.t("main.folder_uncategorized"))
            b.set_count(none_count)
            b.clicked.connect(lambda: self.placeChosen.emit(PLACE_NONE, ""))
            self.folders[""] = b
            self._folder_box.addWidget(b)

    def _sync_overlay_bar(self, low: int, high: int) -> None:
        bar = self._overlay_bar
        bar.setRange(low, high)
        bar.setPageStep(self._list_area.verticalScrollBar().pageStep())
        bar.setVisible(high > low)
        self._place_overlay_bar()

    def _place_overlay_bar(self) -> None:
        g = self._list_area.geometry()
        self._overlay_bar.setGeometry(g.right() + 2, g.top(), 8, g.height())
        self._overlay_bar.raise_()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self._list_area and event.type() in (QEvent.Resize, QEvent.Move):
            self._place_overlay_bar()
        return super().eventFilter(obj, event)

    def set_tags(self, tags: list[tuple[str, int]]) -> None:
        """태그 줄을 다시 놓는다(바뀌었을 때만)."""
        sig = tuple(tags)
        if sig == self._tag_sig:
            return
        self._tag_sig = sig
        while self._tag_box.count():
            item = self._tag_box.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.deleteLater()
        self.tags = {}
        for tag, count in tags:
            b = RailButton(svg_icon("hash", color="#4A4A4A"), tag)
            b.setCheckable(False)
            b.set_count(count)
            b.clicked.connect(lambda _c=False, t=tag: self.tagChosen.emit(t))
            self.tags[tag] = b
            self._tag_box.addWidget(b)
        self._tag_caption.setVisible(bool(tags))

    def set_counts(self, counts: dict[str, int]) -> None:
        for key, b in self.places.items():
            b.set_count(counts.get(key, ""))

    def set_trash(self, count, available: bool) -> None:
        self.trash_button.setVisible(available)
        self.trash_button.set_count(count)

    def set_current(self, kind: str, folder_id: str = "") -> None:
        for key, b in self.places.items():
            b.setChecked(kind == key)
        for fid, b in self.folders.items():
            b.setChecked((kind == PLACE_FOLDER and fid == folder_id)
                         or (kind == PLACE_NONE and fid == ""))
        self.trash_button.setChecked(kind == PLACE_TRASH)


# ── 보기 고르기 ──

class SegmentButton(QToolButton):
    def __init__(self, icon: str, tip: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setIcon(svg_icon(icon, color="#3A3A3A"))
        self.setIconSize(QSize(16, 16))
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setToolTip(tip)
        self.setAccessibleName(tip)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(32, 28)
        self.setStyleSheet(
            "QToolButton { border: none; border-radius: 6px; background: transparent; }"
            "QToolButton:hover { background: #EFEFEF; }"
            "QToolButton:checked { background: #FFFFFF; border: 1px solid #DADADA; }")


class ViewToggle(QFrame):
    """목록과 격자 두 단추."""

    changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ViewToggle")
        self.setStyleSheet("QFrame#ViewToggle { background: #F3F3F3; border-radius: 8px; }")
        row = QHBoxLayout(self)
        row.setContentsMargins(2, 2, 2, 2)
        row.setSpacing(2)
        self.list_button = SegmentButton("view_list", i18n.t("main.view_list_short"))
        self.grid_button = SegmentButton("view_grid", i18n.t("main.view_grid_short"))
        row.addWidget(self.list_button)
        row.addWidget(self.grid_button)
        self.list_button.clicked.connect(lambda: self.changed.emit("list"))
        self.grid_button.clicked.connect(lambda: self.changed.emit("grid"))

    def set_layout(self, layout: str) -> None:
        (self.grid_button if layout == "grid" else self.list_button).setChecked(True)


# ── 본인 아이콘과 접속 정보 ──

STATE_OK, STATE_OFF, STATE_SOLO, STATE_WAIT = "ok", "off", "solo", "wait"


class MeButton(QAbstractButton):
    """상단바의 본인 아이콘. 이름 첫 글자, 사람 색, 연결 상태 점."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._nick = ""
        self._state = STATE_WAIT
        self.setFixedSize(34, 34)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.TabFocus)
        self.setAttribute(Qt.WA_Hover)

    def set_person(self, nick: str, state: str, tip: str) -> None:
        self._nick, self._state = nick, state
        self.setToolTip(tip)
        self.setAccessibleName(tip)
        self.update()

    def state(self) -> str:
        return self._state

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self.underMouse() or self.isDown():
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 18))
            p.drawEllipse(QRectF(self.rect()))
        face = QRectF(4, 4, 26, 26)
        _paint_avatar(p, face, self._nick)
        if self.hasFocus():
            p.setPen(QPen(QColor(theme.COLOR_FOCUS), 2))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(face.adjusted(-2, -2, 2, 2))
        dot = {STATE_OK: theme.COLOR_STATE_OK, STATE_OFF: theme.COLOR_STATE_OFF,
               STATE_WAIT: theme.COLOR_STATE_WAIT}.get(self._state)
        if dot:
            p.setPen(QPen(QColor("#FFFFFF"), 2))
            p.setBrush(QColor(dot))
            p.drawEllipse(QRectF(22, 22, 10, 10))


@dataclass
class MeInfo:
    """접속 정보 창에 적을 것."""
    nick: str
    role: str                 # "host" | "guest" | "solo"
    state: str                # STATE_*
    state_text: str           # '연결됨', '연결 끊김' …
    address: str = ""         # 공유 PC(참여 중) 또는 이 PC(공유 PC) 주소
    people: list[tuple[str, str]] = field(default_factory=list)   # (이름, 편집 중인 메모 제목)


class MePopup(QFrame):
    """본인 아이콘을 누르면 열리는 접속 정보. 바깥을 누르거나 Esc 로 닫힌다."""

    changeConnectionRequested = Signal()
    shareRequested = Signal()
    reconnectRequested = Signal()

    def __init__(self, info: MeInfo, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        # 열린 채로 본인 아이콘을 다시 누르면 닫히기만 한다(그 누름으로 다시 열리지 않게).
        self.setAttribute(Qt.WA_NoMouseReplay)
        self.setObjectName("MePopup")
        self.info = info
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QFrame(self)
        card.setObjectName("MeCard")
        card.setStyleSheet(
            "QFrame#MeCard { background: #FFFFFF; border: 1px solid #E3E3E3; border-radius: 12px; }"
            "QLabel { background: transparent; }")
        outer.addWidget(card)
        v = QVBoxLayout(card)
        v.setContentsMargins(16, 14, 16, 12)
        v.setSpacing(0)

        # 나
        me = QHBoxLayout()
        me.setSpacing(10)
        me.addWidget(_Face(info.nick, 36))
        names = QVBoxLayout()
        names.setSpacing(1)
        self.name_label = QLabel(info.nick)
        self.name_label.setTextFormat(Qt.PlainText)
        self.name_label.setStyleSheet("font-size: 14px; font-weight: 700; color: #1F1F1F;")
        self.role_label = QLabel(i18n.t(f"me.role_{info.role}"))
        self.role_label.setStyleSheet("font-size: 12px; color: #6B6B6B;")
        names.addWidget(self.name_label)
        names.addWidget(self.role_label)
        me.addLayout(names, 1)
        v.addLayout(me)

        # 연결 상태와 주소
        if info.role != "solo":
            v.addSpacing(12)
            color = {STATE_OK: theme.COLOR_STATE_OK, STATE_OFF: theme.COLOR_STATE_OFF}.get(
                info.state, theme.COLOR_STATE_WAIT)
            state = QHBoxLayout()
            state.setSpacing(7)
            dot = QFrame()
            dot.setFixedSize(8, 8)
            dot.setStyleSheet(f"background: {color}; border-radius: 4px; border: none;")
            state.addWidget(dot, 0, Qt.AlignVCenter)
            self.state_label = QLabel(info.state_text)
            self.state_label.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {color};")
            state.addWidget(self.state_label)
            state.addStretch(1)
            v.addLayout(state)
            if info.address:
                v.addSpacing(6)
                addr = QHBoxLayout()
                addr.setSpacing(8)
                key = QLabel(i18n.t("me.my_address" if info.role == "host" else "me.address"))
                key.setStyleSheet("font-size: 12px; color: #6B6B6B;")
                addr.addWidget(key)
                self.address_label = QLabel(info.address)
                self.address_label.setTextFormat(Qt.PlainText)
                self.address_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
                self.address_label.setStyleSheet("font-size: 12px; color: #1F1F1F; font-weight: 600;")
                addr.addWidget(self.address_label, 1)
                if info.role == "host":
                    self.copy_button = _TextButton(i18n.t("me.copy"))
                    self.copy_button.clicked.connect(self._copy_address)
                    addr.addWidget(self.copy_button)
                v.addLayout(addr)

            # 지금 접속한 사람
            v.addSpacing(12)
            v.addWidget(_Line())
            v.addSpacing(10)
            others = [(n, t) for n, t in info.people if n != info.nick]
            head = QLabel(i18n.t("me.online", count=len(others)) if info.state == STATE_OK
                          else i18n.t("me.offline_people"))
            head.setProperty("role", "sectionHeader")
            v.addWidget(head)
            self.people_rows: list[QWidget] = []
            if info.state == STATE_OK:
                for nick, editing in others:
                    row = QWidget()
                    h = QHBoxLayout(row)
                    h.setContentsMargins(0, 6, 0, 0)
                    h.setSpacing(8)
                    h.addWidget(_Face(nick, 22))
                    name = QLabel(nick)
                    name.setTextFormat(Qt.PlainText)
                    name.setStyleSheet("font-size: 13px; font-weight: 600; color: #1F1F1F;")
                    h.addWidget(name)
                    if editing:
                        doing = QLabel(i18n.t("me.editing", title=editing))
                        doing.setTextFormat(Qt.PlainText)
                        doing.setStyleSheet("font-size: 12px; color: #B45309;")
                        h.addWidget(doing, 1)
                    else:
                        h.addStretch(1)
                    v.addWidget(row)
                    self.people_rows.append(row)

        # 할 일
        v.addSpacing(12)
        v.addWidget(_Line())
        v.addSpacing(6)
        self.actions: dict[str, QPushButton] = {}
        if info.state == STATE_OFF and info.role == "guest":
            self._action("reconnect", "me.reconnect", "network", self.reconnectRequested)
        if info.role == "solo":
            self._action("share", "settings.btn_share_start", "users", self.shareRequested)
        self._action("change", "main.tray_change_connection", "swap",
                     self.changeConnectionRequested)
        for b in self.actions.values():
            v.addWidget(b)
        # 영어와 일본어의 주소 줄이 300 에 잘렸다. 글에 맞춰 넓히되 너무 넓어지지 않게.
        self.setMinimumWidth(300)
        self.setMaximumWidth(440)

    def _action(self, key: str, text_key: str, icon: str, signal) -> None:
        b = QPushButton(i18n.t(text_key))
        b.setIcon(svg_icon(icon, color="#3A3A3A"))
        b.setIconSize(QSize(16, 16))
        b.setCursor(Qt.PointingHandCursor)
        b.setStyleSheet(
            "QPushButton { background: transparent; color: #1F1F1F; border: none; "
            "border-radius: 8px; padding: 8px 8px; text-align: left; font-weight: 600; "
            "min-height: 18px; }"
            "QPushButton:hover { background: #F2F2F2; }"
            f"QPushButton:focus {{ background: #F2F2F2; }}")
        b.clicked.connect(lambda: (self.close(), signal.emit()))
        self.actions[key] = b

    def _copy_address(self) -> None:
        QGuiApplication.clipboard().setText(self.info.address)
        self.copy_button.setText(i18n.t("me.copied"))

    def open_below(self, anchor: QWidget) -> None:
        """아이콘 아래, 아이콘 오른쪽 끝에 맞춰 연다(창 밖으로 나가지 않게)."""
        self.adjustSize()
        corner = anchor.mapToGlobal(QPoint(anchor.width(), anchor.height() + 6))
        screen = anchor.screen().availableGeometry()
        x = max(screen.left() + 8, min(corner.x() - self.width() + 8,
                                       screen.right() - self.width() - 8))
        self.move(x, corner.y())
        self.show()
        first = next(iter(self.actions.values()), None)
        if first is not None:
            first.setFocus(Qt.PopupFocusReason)


class _Face(QWidget):
    def __init__(self, nick: str, size: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._nick = nick
        self.setFixedSize(size, size)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        _paint_avatar(p, QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), self._nick)


class _Line(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(1)
        self.setStyleSheet("background: #EFEFEF; border: none;")


class _TextButton(QPushButton):
    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(
            "QPushButton { background: transparent; border: 1px solid #D5D5D5; "
            "border-radius: 6px; padding: 2px 8px; color: #1F1F1F; font-size: 11.5px; "
            "font-weight: 600; min-height: 0; }"
            "QPushButton:hover { background: #F2F2F2; }")


class ElidedLabel(QLabel):
    """한 줄 글. 자리가 모자라면 끝을 '…' 로 줄이고, 그때는 말풍선에 다 보인다."""

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(0, super().minimumSizeHint().height())

    def setText(self, text: str) -> None:  # noqa: N802
        super().setText(text)
        self._sync_tip()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._sync_tip()

    def _shown(self) -> str:
        return self.fontMetrics().elidedText(self.text(), Qt.ElideRight, max(0, self.width()))

    def _sync_tip(self) -> None:
        self.setToolTip(self.text() if self._shown() != self.text() else "")

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        self.style().drawItemText(painter, self.rect(), int(self.alignment()), self.palette(),
                                  self.isEnabled(), self._shown(), self.foregroundRole())
        painter.end()


__all__ = ["ElidedLabel", "FolderRow", "MeButton", "MeInfo", "MePopup", "NARROW_BELOW", "PLACE_ALL",
           "PLACE_FOLDER", "PLACE_HIDDEN", "PLACE_NONE", "PLACE_REMIND", "PLACE_SHOWN",
           "PLACE_TRASH", "RAIL_WIDTH", "Rail", "RailButton", "STATE_OFF", "STATE_OK",
           "STATE_SOLO", "STATE_WAIT", "ViewToggle", "person_color"]
