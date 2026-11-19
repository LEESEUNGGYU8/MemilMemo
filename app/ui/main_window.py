from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import QPoint, QPointF, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor, QCursor, QGuiApplication, QIcon, QKeySequence, QPainter, QPen,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStatusBar,
    QSystemTrayIcon,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import config, i18n
from ..ipc import CMD_NEW_NOTE, CMD_NEW_NOTE_HERE, CMD_SHOW
from ..datetime_fmt import fmt_relative
from ..local_state import FolderIconState, LocalStateStore
from ..network.client_base import ClientBase
from ..models import new_id
from ..replica import ReplicaStore, list_copies
from ..settings import (
    VIEW_COMPACT,
    VIEW_DETAILED,
    VIEW_GRID,
    VIEW_LIST,
    VIEW_STANDARD,
    AppSettings,
)
from . import confirm, theme
from .copies_dialog import CopiesDialog
from .folder_dialog import FolderEditDialog
from .folder_icon import FolderIconWidget
from .history_viewer import HistoryViewer
from .icons import color_swatch_icon, svg_icon
from .menu_item import add_centered_menu_action
from .note_card import GRID_MIN_COLUMN, CardFlowLayout, NoteCard
from .note_fold import FOLD_ICON, ICON_GRID, free_dock_slot, snap_icon_pos
from .rich_text import has_non_checkbox_image, html_to_plain
from .sessions_dialog import SessionsDialog
from .settings_dialog import SettingsDialog
from .sticky_note import StickyNoteWidget
from .theme import RoundedScrollBar
from .toast import ToastManager


# 실제 폴더 id 와 같은 dict 키 자리에 분기 없이 섞여 저장되도록 문자열 sentinel 로 둔다.
FOLDER_ALL = "__all__"
FOLDER_NONE = "__none__"

# 새 메모가 놓이는 자리: 화면의 쓸 수 있는 영역 왼쪽 위에서 이만큼 들어온 곳.
NEW_NOTE_ORIGIN = QPoint(120, 120)
# 그 자리에 이미 메모가 있으면 이만큼씩 비켜 놓는다(손잡이 줄이 서로 보일 만큼).
NEW_NOTE_STEP = 28
# 우클릭한 자리에 띄울 때는 마우스가 메모의 손잡이 줄 위에 오도록 조금 당긴다.
NEW_NOTE_CURSOR_INSET = QPoint(24, 16)
# 밖에서 온 '새 메모' 요청의 자리를 기억해 두는 시간(초). 그 안에 메모가
# 만들어지지 않았으면 잊는다. 뒤늦게 만든 다른 메모가 그 자리로 가면 안 된다.
NEW_NOTE_SPOT_TTL = 10.0


def _local_lan_ip() -> str:
    """이 PC 의 LAN 주소를 best-effort 로 찾아낸다.

    UDP connect 는 패킷을 보내지 않지만 OS 가 송신 인터페이스를 고르게
    하므로, 그 소스 IP 가 LAN 에서 보이는 주소가 된다. 인터페이스가
    없으면 loopback 으로 폴백한다."""
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(0)
        sock.connect(("10.255.255.255", 1))
        return sock.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        sock.close()


class _ConnectionBadge(QWidget):
    """상태줄 오른쪽에 늘 보이는 연결 상태.

    임시 문구(누가 접속했다, 메모를 옮겼다)가 지나가도 사라지지 않는다. 색만으로
    구별하지 않도록 색 점 옆에 상태를 글자로 적는다."""

    _COLORS = {
        "connected": theme.COLOR_STATE_OK,
        "connecting": theme.COLOR_STATE_WAIT,
        "disconnected": theme.COLOR_STATE_OFF,
    }

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 2, 0)
        row.setSpacing(6)
        self._dot = QFrame()
        self._dot.setFixedSize(8, 8)
        self._state = QLabel()
        self._detail = QLabel()
        self._detail.setStyleSheet(
            f"color: {theme.COLOR_TEXT_SUBTLE}; font-size: 11px;")
        row.addWidget(self._dot, 0, Qt.AlignVCenter)
        row.addWidget(self._state)
        row.addWidget(self._detail)
        self._name = ""

    def set_state(self, state: str, text: str, detail: str) -> None:
        color = self._COLORS.get(state, theme.COLOR_STATE_WAIT)
        self._name = state
        self._dot.setStyleSheet(
            f"background: {color}; border-radius: 4px; border: none;")
        self._state.setStyleSheet(
            f"color: {color}; font-size: 11px; font-weight: 700;")
        self._state.setText(text)
        self._detail.setText(detail)
        self.setAccessibleName(f"{text}, {detail}")

    def state(self) -> str:
        return self._name

    def set_compact(self, compact: bool) -> None:
        """임시 문구가 떠 있는 동안에는 자리를 내주고 상태만 남긴다."""
        self._detail.setVisible(not compact)


class MainWindow(QMainWindow):
    backupRequested = Signal()
    restoreRequested = Signal()
    takeoverRequested = Signal(str)     # 공유를 이어받을 사본 파일의 경로

    # 지운 뒤 '되돌리기'가 보이는 시간.
    _UNDO_MS = 10_000
    # 새 메모 알림을 모으는 시간. 잇달아 만든 메모는 한 번에 알린다.
    _ANNOUNCE_MS = 900

    def __init__(self, client: ClientBase, local_state: LocalStateStore,
                 role: str, settings: AppSettings | None = None,
                 app_icon: QIcon | None = None, parent=None,
                 replica: ReplicaStore | None = None):
        super().__init__(parent)
        self.setWindowTitle(i18n.t("app.name"))
        self.resize(380, 600)
        if app_icon is not None and not app_icon.isNull():
            self.setWindowIcon(app_icon)

        self._client = client
        self._local = local_state
        self._role = role
        self._settings = settings or AppSettings()
        self._app_icon = app_icon
        self._notes: dict[str, dict[str, Any]] = {}
        self._folders: dict[str, dict[str, Any]] = {}
        self._current_folder: str = FOLDER_ALL
        self._locks: dict[str, str] = {}
        self._stickies: dict[str, StickyNoteWidget] = {}
        # 바탕 화면에 아이콘으로 둔 폴더(폴더 id → 아이콘 창).
        self._folder_icons: dict[str, FolderIconWidget] = {}
        self._cards: dict[str, NoteCard] = {}
        self._users: list[str] = []
        self._history_viewer: HistoryViewer | None = None
        self._sessions_dialog: SessionsDialog | None = None
        # 호스트는 첫 welcome 이전부터 자기 닉네임이 호스트 닉네임임을 알므로 미리 채운다.
        self._host_nickname: str = (
            client.nickname() if role == "host" else "")
        self._connected: bool = False
        # 한 번이라도 붙었던 적이 있는가. 처음의 '접속 중'과 그 뒤의 '끊김'을 가른다.
        self._was_connected: bool = False
        self._heartbeat_ms: int = 0
        self._force_quit = False
        self._tray: QSystemTrayIcon | None = None
        self._search_query: str = ""
        # 복원이 일반 lock → update → release 흐름을 타야 이력 항목이 남으므로,
        # 클릭 시 여기에 내용을 담아 두고 _on_lock_granted 에서 소비한다.
        self._pending_restore: dict[str, str] = {}
        # 연결이 끊긴 동안 쓴 글을 다시 붙은 뒤 보내려고 잡아 둔 것(메모 id → 내용).
        self._pending_resend: dict[str, str] = {}
        # 원래 메모에 넣지 못해 새 메모로 남길 글. 새 메모가 만들어지면 채운다.
        self._orphan_texts: list[str] = []
        self._recovery_notes: set[str] = set()
        # 방금 지운 메모를 되돌릴 수 있게 잠깐 들고 있는 내용.
        self._undo_snapshots: list[dict] = []
        # 되돌리기로 다시 만드는 중인 메모(만들어지는 차례대로).
        self._recreate_queue: list[dict] = []
        # 게스트가 한 번도 붙지 못한 채 실패한 횟수.
        self._connect_failures: int = 0
        self._selection_mode: bool = False
        self._selected_notes: set[str] = set()
        self._selection_anchor: str | None = None
        # 밖에서 온 '새 메모' 요청이 바란 자리(요청 시각, 자리 또는 None).
        # 메모 id 는 만들어진 뒤에야 알 수 있어 순서대로 짝짓는다.
        self._new_note_spots: list[tuple[float, QPoint | None]] = []
        # 이 PC 에 남겨 둔 메모 사본(게스트). 호스트가 꺼져 있을 때 보여 준다.
        self._replica = replica
        self._replica_shown = False
        self._replica_synced_at = 0
        # 알릴 새 메모(만든 사람 → 개수). 잠깐 모았다가 한 번에 알린다.
        self._announce: dict[str, int] = {}
        # 알릴 공개 전환(바꾼 사람 → 개수).
        self._announce_shared: dict[str, int] = {}
        # 화면 오른쪽 아래에 잠깐 뜨는 알림. 누르면 메인 창을 연다.
        self._toasts = ToastManager(self, app_icon)
        self._toasts.clicked.connect(self._show_main_window)
        # 지워 달라고 보냈고 아직 답을 못 받은 메모(메모 id → 되돌릴 때 쓸 내용).
        self._pending_deletes: dict[str, dict] = {}
        self._delete_message = ""
        self._delete_round = 0
        self._announce_timer = QTimer(self)
        self._announce_timer.setSingleShot(True)
        self._announce_timer.setInterval(self._ANNOUNCE_MS)
        self._announce_timer.timeout.connect(self._flush_announcements)

        self._build_ui()
        self._wire_client()
        self._build_tray()
        self._show_replica()

    def _build_ui(self) -> None:
        root = QWidget(self)
        root.setObjectName("ContentRoot")
        self.setCentralWidget(root)

        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        top = QWidget()
        top_row = QHBoxLayout(top)
        top_row.setContentsMargins(12, 12, 12, 8)
        top_row.setSpacing(8)

        self._new_btn = QPushButton(i18n.t("main.btn_new_note"))
        self._new_btn.setIcon(svg_icon("plus"))
        self._new_btn.setIconSize(QSize(16, 16))
        self._new_btn.setProperty("primary", True)
        self._new_btn.setCursor(Qt.PointingHandCursor)
        self._new_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._new_btn.setMinimumHeight(44)
        self._new_btn.setToolTip(i18n.t("main.tooltip_new_note"))
        self._new_btn.clicked.connect(
            lambda: self._create_note(self._settings.default_note_color))
        top_row.addWidget(self._new_btn, 1)

        self._hist_btn = QPushButton(i18n.t("main.btn_history"))
        self._hist_btn.setIcon(svg_icon("clock"))
        self._hist_btn.setIconSize(QSize(16, 16))
        self._hist_btn.setProperty("primary", True)
        self._hist_btn.setCursor(Qt.PointingHandCursor)
        self._hist_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._hist_btn.setMinimumHeight(44)
        self._hist_btn.clicked.connect(self._open_history)
        top_row.addWidget(self._hist_btn, 1)

        square_style = (
            "QToolButton { background: #F5F5F5; border: 1px solid #EAEAEA; "
            "border-radius: 10px; padding: 0; color: #1F1F1F; }"
            "QToolButton:hover { background: #F0F0F0; border-color: #D5D5D5; }"
            "QToolButton:pressed { background: #E2E2E2; }"
        )

        self._sessions_btn = QToolButton()
        self._sessions_btn.setIcon(svg_icon("users"))
        self._sessions_btn.setIconSize(QSize(16, 16))
        self._sessions_btn.setToolTip(i18n.t("main.tooltip_sessions"))
        self._sessions_btn.setCursor(Qt.PointingHandCursor)
        self._sessions_btn.setFixedSize(44, 44)
        self._sessions_btn.setStyleSheet(square_style)
        self._sessions_btn.clicked.connect(self._open_sessions)
        top_row.addWidget(self._sessions_btn)

        self._settings_btn = QToolButton()
        self._settings_btn.setIcon(svg_icon("settings"))
        self._settings_btn.setIconSize(QSize(16, 16))
        self._settings_btn.setToolTip(i18n.t("main.tooltip_settings"))
        self._settings_btn.setCursor(Qt.PointingHandCursor)
        self._settings_btn.setFixedSize(44, 44)
        self._settings_btn.setStyleSheet(square_style)
        self._settings_btn.clicked.connect(self._open_settings)
        top_row.addWidget(self._settings_btn)

        v.addWidget(top)

        search_wrap = QWidget()
        search_layout = QHBoxLayout(search_wrap)
        search_layout.setContentsMargins(12, 2, 12, 6)
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText(i18n.t("main.search_placeholder"))
        self._search_input.setClearButtonEnabled(True)
        from PySide6.QtGui import QAction
        search_action = QAction(svg_icon("search", color="#888888"),
                                 "", self._search_input)
        self._search_input.addAction(
            search_action, QLineEdit.LeadingPosition)
        self._search_input.textChanged.connect(self._apply_search_filter)
        search_layout.addWidget(self._search_input)
        v.addWidget(search_wrap)

        folder_wrap = QWidget()
        folder_row = QHBoxLayout(folder_wrap)
        folder_row.setContentsMargins(12, 0, 12, 8)
        folder_row.setSpacing(8)

        self._folder_chip = QPushButton(i18n.t("main.folder_all"))
        self._folder_chip.setIcon(svg_icon("folder", color="#3A3A3A"))
        self._folder_chip.setIconSize(QSize(16, 16))
        self._folder_chip.setCursor(Qt.PointingHandCursor)
        self._folder_chip.setMinimumHeight(34)
        self._folder_chip.setStyleSheet(
            "QPushButton { background: #F5F5F5; border: 1px solid #EAEAEA; "
            "border-radius: 10px; padding: 4px 32px 4px 12px; "
            "color: #1F1F1F; text-align: left; font-weight: 600; }"
            "QPushButton:hover { background: #F0F0F0; border-color: #D5D5D5; }"
        )
        # 펼침 표식은 글자(글리프)가 아니라 아이콘으로 오른쪽 끝에 둔다.
        chip_row = QHBoxLayout(self._folder_chip)
        chip_row.setContentsMargins(0, 0, 11, 0)
        chip_row.addStretch(1)
        chip_chevron = QLabel(self._folder_chip)
        chip_chevron.setPixmap(
            svg_icon("chevron_down", color="#5B5B5B").pixmap(14, 14))
        chip_chevron.setAttribute(Qt.WA_TransparentForMouseEvents)
        chip_chevron.setStyleSheet("background: transparent; border: none;")
        chip_row.addWidget(chip_chevron, 0, Qt.AlignVCenter)
        self._folder_chip.clicked.connect(self._show_folder_selector)
        folder_row.addWidget(self._folder_chip, 1)

        self._folder_menu_btn = QToolButton()
        self._folder_menu_btn.setIcon(svg_icon("more"))
        self._folder_menu_btn.setIconSize(QSize(16, 16))
        self._folder_menu_btn.setToolTip(i18n.t("main.tooltip_folder_actions"))
        self._folder_menu_btn.setCursor(Qt.PointingHandCursor)
        self._folder_menu_btn.setFixedSize(34, 34)
        self._folder_menu_btn.setStyleSheet(
            "QToolButton { background: #F5F5F5; border: 1px solid #EAEAEA; "
            "border-radius: 10px; padding: 0; color: #1F1F1F; }"
            "QToolButton:hover { background: #F0F0F0; border-color: #D5D5D5; }"
        )
        self._folder_menu_btn.clicked.connect(self._show_folder_menu)
        folder_row.addWidget(self._folder_menu_btn)

        v.addWidget(folder_wrap)

        hdr_row = QWidget()
        hdr = QHBoxLayout(hdr_row)
        hdr.setContentsMargins(20, 12, 16, 8)
        hdr.setSpacing(8)
        header_lbl = QLabel(i18n.t("main.header_recent_notes"))
        header_lbl.setStyleSheet(
            "color: #111827; font-size: 14px; font-weight: 800; "
            "letter-spacing: -0.2px;")
        hdr.addWidget(header_lbl, 0)

        self._empty_hint = QLabel("")
        self._empty_hint.setStyleSheet(
            "color: #4B5563; font-size: 11px; font-weight: 700; "
            "background: rgba(17, 24, 39, 0.07); "
            "border-radius: 9px; padding: 2px 9px;")
        hdr.addWidget(self._empty_hint, 0)
        hdr.addStretch(1)

        self._view_btn = QToolButton()
        self._view_btn.setIconSize(QSize(16, 16))
        self._view_btn.setToolTip(i18n.t("main.tooltip_view"))
        self._view_btn.setAccessibleName(i18n.t("main.tooltip_view"))
        self._view_btn.setCursor(Qt.PointingHandCursor)
        self._view_btn.setFixedSize(32, 26)
        self._view_btn.setStyleSheet(
            "QToolButton { background: transparent; border: 1px solid "
            "  #D5D5D5; border-radius: 7px; padding: 0; }"
            "QToolButton:hover { background: #F3F4F6; }"
            "QToolButton:pressed { background: #E5E7EB; }"
            "QToolButton:focus { border-color: #1F1F1F; }"
        )
        self._view_btn.clicked.connect(self._show_view_menu)
        hdr.addWidget(self._view_btn)

        self._select_btn = QToolButton()
        self._select_btn.setText(i18n.t("main.btn_select"))
        self._select_btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self._select_btn.setCheckable(True)
        self._select_btn.setCursor(Qt.PointingHandCursor)
        self._select_btn.setFocusPolicy(Qt.NoFocus)
        self._select_btn.setStyleSheet(
            "QToolButton { background: transparent; border: 1px solid "
            "  #D5D5D5; border-radius: 7px; padding: 3px 10px; "
            "  color: #4B5563; font-size: 11.5px; font-weight: 700; }"
            "QToolButton:hover { background: #F3F4F6; }"
            "QToolButton:checked { background: #2563EB; "
            "  border-color: #2563EB; color: #FFFFFF; }"
        )
        self._select_btn.toggled.connect(self._on_select_mode_toggled)
        hdr.addWidget(self._select_btn)

        v.addWidget(hdr_row)

        scroll_wrap = QWidget()
        scroll_wrap_v = QVBoxLayout(scroll_wrap)
        scroll_wrap_v.setContentsMargins(12, 2, 12, 10)
        scroll_wrap_v.setSpacing(0)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.NoFrame)
        self._scroll.setVerticalScrollBar(RoundedScrollBar())
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(
            "QScrollArea { background: #FAFBFD; "
            "border: 1px solid #ECECEC; border-radius: 14px; }"
        )
        # 뷰포트를 투명으로 둬야 스크롤 영역이 그리는 둥근 배경이 드러난다.
        self._scroll.viewport().setStyleSheet("background: transparent;")
        self._scroll.viewport().setAutoFillBackground(False)

        self._cards_container = QWidget()
        self._cards_container.setObjectName("CardsContainer")
        self._cards_container.setStyleSheet("background: transparent;")
        self._cards_layout = CardFlowLayout(self._cards_container)
        self._cards_layout.setContentsMargins(10, 10, 10, 10)
        self._cards_layout.setSpacing(8)

        self._scroll.setWidget(self._cards_container)
        # 호스트에 붙지 못해 이 PC 의 사본을 보여 주는 동안 그 사실을 적어 둔다.
        # 옆의 단추로 사본 창을 연다(공유 PC 를 다시 쓸 수 없을 때 이어받는 길).
        self._replica_banner = QFrame()
        self._replica_banner.setObjectName("ReplicaBanner")
        self._replica_banner.setStyleSheet(
            "QFrame#ReplicaBanner { background: #FFF7D6; "
            "border: 1px solid #F1DE9A; border-radius: 10px; }"
            "QFrame#ReplicaBanner QLabel { color: #5C4A00; font-size: 12px; "
            "font-weight: 600; background: transparent; }"
            "QFrame#ReplicaBanner QPushButton { background: #FFFFFF; "
            "color: #5C4A00; border: 1px solid #E3CC7A; border-radius: 7px; "
            "padding: 3px 10px; font-size: 11.5px; font-weight: 700; }"
            "QFrame#ReplicaBanner QPushButton:hover { background: #FFFBEA; }")
        banner_row = QHBoxLayout(self._replica_banner)
        banner_row.setContentsMargins(12, 6, 8, 6)
        banner_row.setSpacing(8)
        self._replica_text = QLabel("")
        self._replica_text.setWordWrap(True)
        banner_row.addWidget(self._replica_text, 1)
        self._copies_btn = QPushButton(i18n.t("main.replica_banner_btn"))
        self._copies_btn.setCursor(Qt.PointingHandCursor)
        self._copies_btn.clicked.connect(self.open_copies)
        banner_row.addWidget(self._copies_btn, 0, Qt.AlignVCenter)
        self._replica_banner.hide()
        scroll_wrap_v.addWidget(self._replica_banner)
        scroll_wrap_v.addSpacing(0)
        scroll_wrap_v.addWidget(self._scroll)

        # 보여 줄 메모가 없을 때: 지금 상황 한 줄과 다음에 할 일 하나.
        self._empty_panel = QFrame()
        self._empty_panel.setObjectName("EmptyPanel")
        self._empty_panel.setStyleSheet(
            "QFrame#EmptyPanel { background: #FAFBFD; "
            "border: 1px solid #ECECEC; border-radius: 14px; }")
        empty_v = QVBoxLayout(self._empty_panel)
        empty_v.setContentsMargins(28, 24, 28, 24)
        empty_v.setSpacing(8)
        empty_v.addStretch(2)
        self._empty_title = QLabel("")
        self._empty_title.setAlignment(Qt.AlignHCenter)
        self._empty_title.setWordWrap(True)
        self._empty_title.setStyleSheet(
            "color: #1F2937; font-size: 14px; font-weight: 700;")
        empty_v.addWidget(self._empty_title)
        self._empty_body = QLabel("")
        self._empty_body.setAlignment(Qt.AlignHCenter)
        self._empty_body.setWordWrap(True)
        self._empty_body.setStyleSheet("color: #4B5563; font-size: 12px;")
        empty_v.addWidget(self._empty_body)
        empty_v.addSpacing(8)
        self._empty_action = QPushButton("")
        self._empty_action.setProperty("secondary", True)
        self._empty_action.setCursor(Qt.PointingHandCursor)
        self._empty_action.clicked.connect(self._on_empty_action)
        empty_v.addWidget(self._empty_action, 0, Qt.AlignHCenter)
        empty_v.addStretch(3)
        self._empty_kind = ""
        self._empty_panel.hide()
        scroll_wrap_v.addWidget(self._empty_panel)
        v.addWidget(scroll_wrap, 1)

        self._bulk_bar = QWidget()
        self._bulk_bar.setObjectName("BulkActionBar")
        self._bulk_bar.setStyleSheet(
            "QWidget#BulkActionBar { background: #FFFFFF; "
            "border-top: 1px solid #E5E7EB; }"
            "QWidget#BulkActionBar QLabel { color: #1F2937; "
            "font-size: 12px; font-weight: 700; }"
            "QWidget#BulkActionBar QPushButton { background: #F3F4F6; "
            "color: #1F2937; border: 1px solid #D6D8DC; "
            "border-radius: 8px; padding: 6px 12px; "
            "font-size: 11.5px; font-weight: 700; }"
            "QWidget#BulkActionBar QPushButton:hover { "
            "background: #E5E7EB; border-color: #C0C4CB; }"
            "QWidget#BulkActionBar QPushButton:disabled { "
            "color: #B0B4BA; background: #F7F7F7; "
            "border-color: #ECECEC; }"
            "QWidget#BulkActionBar QPushButton#BulkDelete { "
            "color: #B91C1C; border-color: #FAD3D3; "
            "background: #FEF2F2; }"
            "QWidget#BulkActionBar QPushButton#BulkDelete:hover { "
            "background: #FEE2E2; border-color: #F4ACAC; }"
        )
        bulk_row = QHBoxLayout(self._bulk_bar)
        bulk_row.setContentsMargins(14, 8, 14, 8)
        bulk_row.setSpacing(8)
        self._bulk_count_lbl = QLabel(i18n.t("main.bulk_selected_count", count=0))
        bulk_row.addWidget(self._bulk_count_lbl)
        bulk_row.addStretch(1)
        self._bulk_select_all_btn = QPushButton(i18n.t("main.bulk_select_all"))
        self._bulk_select_all_btn.setCursor(Qt.PointingHandCursor)
        self._bulk_select_all_btn.clicked.connect(self._bulk_select_all)
        bulk_row.addWidget(self._bulk_select_all_btn)
        self._bulk_folder_btn = QPushButton(i18n.t("main.bulk_move_folder"))
        self._bulk_folder_btn.setCursor(Qt.PointingHandCursor)
        self._bulk_folder_btn.clicked.connect(self._bulk_show_folder_menu)
        bulk_row.addWidget(self._bulk_folder_btn)
        self._bulk_private_btn = QPushButton(i18n.t("main.bulk_private"))
        self._bulk_private_btn.setCursor(Qt.PointingHandCursor)
        self._bulk_private_btn.clicked.connect(self._bulk_show_privacy_menu)
        bulk_row.addWidget(self._bulk_private_btn)
        self._bulk_delete_btn = QPushButton(i18n.t("main.bulk_delete"))
        self._bulk_delete_btn.setObjectName("BulkDelete")
        self._bulk_delete_btn.setCursor(Qt.PointingHandCursor)
        self._bulk_delete_btn.clicked.connect(self._bulk_delete)
        bulk_row.addWidget(self._bulk_delete_btn)
        self._bulk_bar.hide()
        v.addWidget(self._bulk_bar)

        status = QStatusBar()
        self.setStatusBar(status)
        self._status = status
        # 연결 상태는 임시 문구에 밀려 사라지지 않도록 상태줄 오른쪽에 늘 둔다.
        # 지운 직후 잠깐 보이는 '되돌리기'.
        self._undo_btn = QPushButton(i18n.t("main.btn_undo"))
        self._undo_btn.setProperty("secondary", True)
        self._undo_btn.setCursor(Qt.PointingHandCursor)
        self._undo_btn.setStyleSheet(
            "QPushButton { padding: 2px 10px; min-height: 18px; "
            "font-size: 11px; border-radius: 6px; }")
        self._undo_btn.clicked.connect(self._undo_delete)
        self._undo_btn.hide()
        status.addPermanentWidget(self._undo_btn)
        self._undo_timer = QTimer(self)
        self._undo_timer.setSingleShot(True)
        self._undo_timer.setInterval(self._UNDO_MS)
        self._undo_timer.timeout.connect(self._forget_undo)
        self._conn_badge = _ConnectionBadge()
        status.addPermanentWidget(self._conn_badge)
        status.messageChanged.connect(
            lambda text: self._conn_badge.set_compact(bool(text)))
        self._refresh_status_text()

        self._select_all_shortcut = QShortcut(
            QKeySequence.SelectAll, self, self._select_all_via_shortcut)
        # 메인 창에서 가장 자주 하는 두 가지는 글쇠로도 된다.
        self._new_note_shortcut = QShortcut(
            QKeySequence.New, self,
            lambda: self._create_note(self._settings.default_note_color))
        self._find_shortcut = QShortcut(
            QKeySequence.Find, self, self._focus_search)
        self._refresh_view_mode()

    def _focus_search(self) -> None:
        self._search_input.setFocus(Qt.ShortcutFocusReason)
        self._search_input.selectAll()

    def _on_empty_action(self) -> None:
        if self._empty_kind == "search":
            self._search_input.clear()
            self._search_input.setFocus(Qt.OtherFocusReason)
        elif self._empty_kind == "unreachable":
            self._reset_connection()
        else:
            self._create_note(self._settings.default_note_color)

    def show_backup_failure(self, message: str) -> None:
        """때맞춰 도는 자동 백업이 실패했다. 하던 일을 끊지 않게 상태줄에만 남긴다."""
        self._status.showMessage(
            i18n.t("main.status_backup_failed", message=message), 10000)

    # ── 보기 방식 ──

    def _show_view_menu(self) -> None:
        s = self._settings
        menu = QMenu(self._view_btn)
        add_centered_menu_action(
            menu, None, i18n.t("main.view_section_density"), header=True)
        for key, label in ((VIEW_COMPACT, "main.view_compact"),
                           (VIEW_STANDARD, "main.view_standard"),
                           (VIEW_DETAILED, "main.view_detailed")):
            add_centered_menu_action(
                menu, None, i18n.t(label),
                lambda k=key: self._set_view_mode(density=k),
                checked=(s.notes_view_density == key))
        menu.addSeparator()
        add_centered_menu_action(
            menu, None, i18n.t("main.view_section_layout"), header=True)
        for key, label, icon in ((VIEW_LIST, "main.view_list", "view_list"),
                                 (VIEW_GRID, "main.view_grid", "view_grid")):
            add_centered_menu_action(
                menu, svg_icon(icon, color="#3A3A3A"), i18n.t(label),
                lambda k=key: self._set_view_mode(layout=k),
                checked=(s.notes_view_layout == key))
        # 버튼이 창 오른쪽 끝에 있어 메뉴를 버튼의 오른쪽 끝에 맞춰 연다.
        pos = self._view_btn.mapToGlobal(
            QPoint(self._view_btn.width() - menu.sizeHint().width(),
                   self._view_btn.height() + 2))
        menu.exec(pos)

    def _set_view_mode(self, *, density: str | None = None,
                       layout: str | None = None) -> None:
        s = self._settings
        if density is not None:
            s.notes_view_density = density
        if layout is not None:
            s.notes_view_layout = layout
        s.save()
        self._refresh_view_mode()

    def _refresh_view_mode(self) -> None:
        """설정에 적힌 보기 방식을 카드와 배치에 입힌다."""
        s = self._settings
        grid = s.notes_view_layout == VIEW_GRID
        compact = s.notes_view_density == VIEW_COMPACT
        self._cards_layout.set_min_column_width(GRID_MIN_COLUMN if grid else 0)
        self._cards_layout.setSpacing(5 if (compact and not grid) else 8)
        for card in self._cards.values():
            card.set_view(s.notes_view_density, s.notes_view_layout)
        self._view_btn.setIcon(svg_icon(
            "view_grid" if grid else "view_list", color="#4B5563"))
        self._cards_layout.invalidate()
        self._cards_container.updateGeometry()

    def _wire_client(self) -> None:
        c = self._client
        c.welcomed.connect(self._on_welcomed)
        c.noteCreated.connect(self._on_note_created)
        c.noteUpdated.connect(self._on_note_updated)
        c.noteDeleted.connect(self._on_note_deleted)
        c.folderCreated.connect(self._on_folder_created)
        c.folderUpdated.connect(self._on_folder_updated)
        c.folderDeleted.connect(self._on_folder_deleted)
        c.lockGranted.connect(self._on_lock_granted)
        c.lockDenied.connect(self._on_lock_denied)
        c.lockHeld.connect(self._on_lock_held)
        c.lockReleased.connect(self._on_lock_released)
        c.userJoined.connect(self._on_user_joined)
        c.userLeft.connect(self._on_user_left)
        c.historyAppended.connect(self._on_history_appended)
        c.noteAnnounced.connect(self._on_note_announced)
        c.noteShared.connect(self._on_note_shared)
        c.historyList.connect(self._on_history_list)
        c.connectionChanged.connect(self._on_connection_changed)
        c.errorOccurred.connect(self._on_error)

    def _create_note(self, color: str, *, private: bool | None = None) -> None:
        """새 메모를 만든다. ``private`` 를 주지 않으면 설정의 기본 공개 범위를 따른다."""
        if not self._connected:
            # 끊긴 동안 보낸 요청은 그대로 사라진다. 아무 일도 안 일어난 것처럼
            # 두지 않고 까닭을 알린다.
            self._status.showMessage(
                i18n.t("main.status_offline_no_create"), 5000)
            return
        # 전체 / 미분류 뷰에서는 폴더 없음으로, 그 외엔 현재 폴더에 넣는다.
        folder_id = (self._current_folder
                     if self._current_folder not in
                     (FOLDER_ALL, FOLDER_NONE)
                     else None)
        if private is None:
            private = bool(self._settings.new_note_private)
        self._client.create_note(
            color, config.DEFAULT_NOTE_WIDTH, config.DEFAULT_NOTE_HEIGHT,
            folder_id=folder_id,
            private_owner=self._client.nickname() if private else None)

    def _require_online(self) -> bool:
        """호스트에 붙어 있을 때만 되는 일 앞에서 부른다.

        끊긴 동안 보낸 요청은 닿지 않는다. 화면만 바뀌고 실제로는 그대로인
        일이 없도록 막고 까닭을 알린다."""
        if self._connected:
            return True
        self._status.showMessage(i18n.t("main.status_offline_no_change"), 5000)
        return False

    def _note_is_private(self, note: dict | None) -> bool:
        """나만 보는 메모인가(메모 자체가 비공개거나 비공개 폴더 안에 있다)."""
        if not note:
            return False
        if note.get("private_owner"):
            return True
        folder = self._folders.get(note.get("folder_id") or "")
        return bool(folder and folder.get("private_owner"))

    def handle_ipc_command(self, command: str) -> None:
        """형제 프로세스가 전달한 명령 줄을 분배한다(예: 작업 표시줄
        점프리스트에서 '새 메모' 클릭)."""
        cmd = (command or "").strip().lower()
        if cmd == CMD_NEW_NOTE:
            self.handle_new_note_request()
        elif cmd == CMD_NEW_NOTE_HERE:
            # 바탕 화면 우클릭 메뉴에서 왔다. 마우스가 있는 자리에 띄운다.
            self.handle_new_note_request(QCursor.pos())
        elif cmd == CMD_SHOW:
            self._show_main_window()

    def handle_new_note_request(self, at: QPoint | None = None) -> None:
        """앱 밖(작업 표시줄 목록, 바탕 화면 우클릭 메뉴)에서 온 '새 메모'.

        메인 창은 건드리지 않는다. 만들어진 메모만 다른 창들 위로 올라온다
        (_on_note_created). ``at`` 을 주면 그 자리에 띄운다."""
        if not self._connected:
            # 끊긴 동안에는 만들 수 없다. 아무 일도 안 일어난 것처럼 보이지
            # 않도록, 까닭(연결 상태)이 보이는 메인 창을 띄운다.
            self._show_main_window()
            return
        self._new_note_spots.append((time.monotonic(), at))
        self._create_note(self._settings.default_note_color)

    def _take_new_note_spot(self) -> QPoint | None:
        """방금 만들어진 메모에 해당하는 요청 자리를 꺼낸다."""
        now = time.monotonic()
        while self._new_note_spots:
            asked_at, spot = self._new_note_spots.pop(0)
            if now - asked_at <= NEW_NOTE_SPOT_TTL:
                return spot
        return None

    def _new_note_position(self, width: int, height: int,
                           at: QPoint | None) -> QPoint:
        """방금 만든 메모를 띄울 자리.

        우클릭 메뉴에서 왔으면 마우스 자리에, 아니면 주 화면 왼쪽 위에서 시작해
        이미 메모가 놓인 자리를 비켜 간다. 어느 쪽이든 화면 안에 들어오게 한다."""
        screen = QGuiApplication.screenAt(at) if at is not None else None
        screen = screen or QGuiApplication.primaryScreen()
        avail = (screen.availableGeometry() if screen is not None
                 else QRect(0, 0, 1280, 720))

        def inside(p: QPoint) -> QPoint:
            x = max(avail.left(), min(p.x(), avail.right() - width + 1))
            y = max(avail.top(), min(p.y(), avail.bottom() - height + 1))
            return QPoint(x, y)

        if at is not None:
            return inside(at - NEW_NOTE_CURSOR_INSET)

        taken = [w.pos() for w in self._stickies.values()
                 if w.isVisible() and not w.is_folded()]
        near = NEW_NOTE_STEP // 2
        pos = inside(avail.topLeft() + NEW_NOTE_ORIGIN)
        while any(abs(t.x() - pos.x()) < near and abs(t.y() - pos.y()) < near
                  for t in taken):
            nxt = pos + QPoint(NEW_NOTE_STEP, NEW_NOTE_STEP)
            if inside(nxt) != nxt:
                # 화면 끝까지 갔다. 더 비킬 데가 없으니 겹쳐 놓는다.
                break
            pos = nxt
        return pos

    def _open_history(self) -> None:
        self._ensure_history_viewer()
        self._history_viewer.set_notes(list(self._notes.values()))
        self._history_viewer.show()
        self._history_viewer.raise_()
        self._client.get_history(None)

    def _ensure_history_viewer(self) -> HistoryViewer:
        if self._history_viewer is None:
            self._history_viewer = HistoryViewer(self)
            self._history_viewer.restoreRequested.connect(
                self._on_restore_requested)
        return self._history_viewer

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self._settings, role=self._role, parent=self)
        dlg.backupNowRequested.connect(self.backupRequested.emit)
        dlg.restoreRequested.connect(self.restoreRequested.emit)
        dlg.copiesRequested.connect(self.open_copies)
        dlg.exec()
        self._apply_tray_visibility()
        # '붙인 메모를 다른 창 위에' 설정은 떠 있는 메모에 바로 반영한다.
        for w in self._stickies.values():
            w.refresh_dock_layer()

    def open_copies(self) -> None:
        """이 PC 에 받아 둔 사본을 열어 본다. 빠진 메모를 가져오거나, 공유 PC 를
        다시 쓸 수 없을 때 이 PC 가 공유를 이어받는다."""
        parent = QApplication.activeModalWidget() or self
        copies = list_copies(config.default_replica_path())
        if not copies:
            confirm.notify(parent, i18n.t("copies.window_title"),
                           i18n.t("copies.empty"))
            return
        dlg = CopiesDialog(
            copies,
            host_notes=dict(self._notes) if self._connected else None,
            can_takeover=(self._role == "guest"), parent=parent)
        dlg.importRequested.connect(self._import_from_copy)
        dlg.takeoverRequested.connect(self.takeoverRequested.emit)
        dlg.exec()

    def _import_from_copy(self, notes: list, folders: list) -> None:
        """사본에만 있는 메모를 새 메모로 만든다. 있는 메모는 건드리지 않는다.

        나만 보던 메모(메모 자체가 비공개였거나 비공개 폴더 안에 있던 것)는 새
        메모도 나만 보게 만든다. 공유 PC 에 없는 폴더는 같은 이름으로 만든다."""
        if not notes or not self._require_online():
            return
        me = self._client.nickname()
        by_id = {f.get("id"): f for f in folders}
        folder_map: dict[str, str] = {}
        for note in notes:
            old = note.get("folder_id")
            source = by_id.get(old)
            if not old or old in self._folders or old in folder_map or not source:
                continue
            made = new_id()
            self._client.create_folder(
                source.get("name") or i18n.t("main.folder_new_default_name"),
                source.get("color") or config.DEFAULT_COLOR,
                bool(source.get("private_owner")), folder_id=made)
            folder_map[old] = made
        for note in notes:
            old = note.get("folder_id")
            folder_id = folder_map.get(old) or (old if old in self._folders else None)
            private = bool(note.get("private_owner")) or bool(
                (by_id.get(old) or {}).get("private_owner"))
            seen = self._local.state.notes.get(note.get("id"))
            color = note.get("color") or config.DEFAULT_COLOR
            width = int(note.get("width") or config.DEFAULT_NOTE_WIDTH)
            height = int(note.get("height") or config.DEFAULT_NOTE_HEIGHT)
            self._recreate_queue.append({
                "content": note.get("content") or "", "color": color,
                "width": width, "height": height, "folder_id": folder_id,
                "private": private,
                # 바탕화면에 한꺼번에 쏟아지지 않게, 전에 꺼내 두었던 메모만 그 자리에 띄운다.
                "pos": (seen.x, seen.y) if seen is not None else (120, 120),
                "hidden": seen.hidden if seen is not None else True,
            })
            self._client.create_note(
                color, width, height, folder_id=folder_id,
                private_owner=me if private else None, quiet=True)
        self._status.showMessage(
            i18n.t("copies.imported", count=len(notes)), 6000)

    def _open_sessions(self) -> None:
        if self._sessions_dialog is None:
            self._sessions_dialog = SessionsDialog(self)
        self._refresh_sessions_dialog()
        self._sessions_dialog.show()
        self._sessions_dialog.raise_()
        self._sessions_dialog.activateWindow()

    def _refresh_sessions_dialog(self) -> None:
        if self._sessions_dialog is None:
            return
        ls = self._local.state
        if self._role == "host":
            # 호스트도 게스트와 같은 '주소:포트' 포맷을 보도록 본 PC 의 LAN 주소를 쓴다.
            peer_address = f"{_local_lan_ip()}:{ls.last_host_port}"
        else:
            peer_address = f"{ls.last_host_ip}:{ls.last_host_port}"
        self._sessions_dialog.refresh(
            host=self._host_nickname,
            online=set(self._users),
            my_nickname=self._client.nickname(),
            my_role=self._role,
            records=ls.users,
            connected=self._connected,
            heartbeat_ms=self._heartbeat_ms,
            peer_address=peer_address,
        )

    def _on_connection_changed(self, connected: bool) -> None:
        self._connected = connected
        if connected:
            self._was_connected = True
            self._connect_failures = 0
            # '연결 끊김' 안내가 아직 떠 있으면 걷는다.
            self._status.clearMessage()
        elif self._was_connected:
            self._status.showMessage(i18n.t("main.status_disconnected"), 6000)
        else:
            self._connect_failures += 1
        # 끊긴 동안에는 메모에 '오프라인'을 적고 새 편집을 막는다.
        for w in self._stickies.values():
            w.set_offline(not connected)
        self._refresh_status_text()
        self._refresh_empty_hint()
        self._refresh_sessions_dialog()
        self._refresh_replica_banner()

    def _host_unreachable(self) -> bool:
        """게스트가 호스트에 한 번도 붙지 못하고 거듭 실패하는 중인가."""
        return (self._role == "guest" and not self._connected
                and not self._was_connected and self._connect_failures >= 2)

    @staticmethod
    def _translate_error(message: str) -> str:
        """호스트가 보내는 영어 오류를 화면 언어로 바꾼다. 모르는 것은 그대로."""
        text = (message or "").strip()
        if text.startswith("note locked by "):
            return i18n.t("error.locked", holder=text[len("note locked by "):])
        if text.startswith("update rejected"):
            return i18n.t("error.conflict")
        if text == "not allowed":
            return i18n.t("error.not_allowed")
        if text.startswith("cannot move into another user's private folder"):
            return i18n.t("error.private_folder")
        return i18n.t("main.status_error", message=text)

    def _on_error(self, message: str) -> None:
        text = (message or "").strip()
        if text.startswith("note locked by ") and self._pending_deletes:
            # 지우려던 메모를 그 사이 다른 사람이 편집하기 시작했다.
            self._pending_deletes.clear()
            self._tell_locked(text[len("note locked by "):])
            return
        # 상태줄 구석의 작은 글자만으로는 놓친다. 알림으로도 띄운다.
        self._show_notification(self._translate_error(message), True)

    def _locked_by_other(self, note_id: str) -> str:
        """다른 사람이 편집 중이면 그 사람의 닉네임, 아니면 빈 문자열."""
        holder = self._locks.get(note_id) or ""
        return holder if holder != self._client.nickname() else ""

    def _tell_locked(self, holder: str) -> None:
        confirm.notify(
            self, i18n.t("main.msg_delete_locked_title"),
            i18n.t("main.msg_delete_locked_body",
                   holder=holder or i18n.t("main.other_user")),
            icon=QMessageBox.Warning)

    def _on_welcomed(self, data: dict) -> None:
        self._users = list(data.get("users", []))
        self._host_nickname = data.get("host", "") or self._host_nickname
        self._heartbeat_ms = int(data.get("heartbeat_ms", 0)) or self._heartbeat_ms
        # 호스트가 보낸 것으로 바꾼다. 사본을 보여 주던 중이었으면 여기서 끝난다.
        self._replica_shown = False
        self._refresh_replica_banner()
        # 끊긴 동안 쓰던 글은 아래에서 메모 창을 새로 만들면 사라진다. 먼저 챙긴다.
        unsent = {nid: text for nid, w in self._stickies.items()
                  if (text := w.unsent_edit())}
        seen_versions = {nid: self._stickies[nid].version for nid in unsent}
        # 나만 보던 메모의 글은 되살릴 때도 나만 보게 한다. 폴더가 바뀌기 전에 적어 둔다.
        was_private = {nid: self._note_is_private(self._notes.get(nid))
                       for nid in unsent}
        # 접속 시점에 이미 명단에 있던 참가자의 세션 시작 시각은 알 수 없으므로
        # 본인만 지금으로 찍고 나머지는 0 으로 둔다(이후 join 은 user_joined 가 채운다).
        me = self._client.nickname()
        for nick in self._users:
            self._local.state.touch_user_in_session(nick, is_self=(nick == me))
        self._local.save()
        self._refresh_sessions_dialog()
        self._refresh_status_text()
        self._load_snapshot(data.get("notes") or [], data.get("folders") or [],
                            dict(data.get("locks", {})), offline=False)
        self._recover_unsent(unsent, seen_versions, was_private)

    def _load_snapshot(self, notes: list, folders: list, locks: dict, *,
                       offline: bool) -> None:
        """메모와 폴더를 받은 것으로 통째로 바꿔 화면을 새로 짠다.

        호스트가 보낸 첫 상태(welcome)에도, 이 PC 에 남겨 둔 사본을 보여 줄
        때에도 쓴다. welcome 마다 처음부터 다시 짜므로 재연결도 깔끔하다."""
        self._folders = {f["id"]: f for f in folders}
        if (self._current_folder not in (FOLDER_ALL, FOLDER_NONE)
                and self._current_folder not in self._folders):
            self._current_folder = FOLDER_ALL
        self._refresh_folder_chip()
        self._refresh_card_folder_context()
        # 아래에서 기존 메모를 닫기 전에 teardown 으로 표시한다. 그러지 않으면
        # closeEvent 의 hideRequested 가 local_state 를 hidden=True 로 바꿔, 재구성된
        # 메모가 전부 숨겨진 채 시작된다(짧은 단절 후 재접속이나 재시작 시 관측).
        for w in self._stickies.values():
            w._teardown = True
        for w in list(self._stickies.values()):
            w.close()
            w.deleteLater()
        self._stickies.clear()
        for c in list(self._cards.values()):
            c.setParent(None)
            c.deleteLater()
        self._cards.clear()
        self._notes.clear()
        self._locks = locks
        sorted_notes = sorted(
            notes,
            key=lambda n: n.get("updated_at", 0),
            reverse=True,
        )
        for note in sorted_notes:
            self._notes[note["id"]] = note
            self._append_card(note)
        self._apply_search_filter()
        for note in sorted_notes:
            self._ensure_sticky(note)
            if offline:
                # 사본이다. 호스트에 닿을 때까지는 읽기만 한다.
                self._stickies[note["id"]].set_offline(True)
        self._refresh_folder_icons()

    def _show_replica(self) -> None:
        """호스트에 붙기 전에 이 PC 의 사본을 먼저 보여 준다(읽기 전용).

        호스트가 꺼져 있어도 메모를 볼 수 있다. 붙으면 호스트가 보낸 것으로
        바뀐다. 다른 닉네임으로 받은 사본은 보여 주지 않는다(그 사람의 '나만
        보기' 메모가 들어 있다)."""
        if self._replica is None or self._role != "guest":
            return
        try:
            meta = self._replica.meta()
            if meta.get("nickname") != self._client.nickname():
                return
            notes, folders = self._replica.load()
        except Exception:
            return
        if not notes:
            return
        try:
            self._replica_synced_at = int(meta.get("synced_at") or 0)
        except ValueError:
            self._replica_synced_at = 0
        self._replica_shown = True
        self._load_snapshot(notes, folders, {}, offline=True)
        self._refresh_replica_banner()

    def _refresh_replica_banner(self) -> None:
        showing = self._replica_shown and not self._connected
        if showing:
            self._replica_text.setText(i18n.t(
                "main.replica_banner",
                when=fmt_relative(self._replica_synced_at)))
        self._replica_banner.setVisible(showing)
        # 안내와 목록 사이를 띄운다(안내가 없을 때는 틈도 없다).
        self._replica_banner.setContentsMargins(0, 0, 0, 0)
        self._scroll.parentWidget().layout().setSpacing(8 if showing else 0)

    def _recover_unsent(self, unsent: dict[str, str],
                        seen_versions: dict[str, int],
                        was_private: dict[str, bool] | None = None) -> None:
        """연결이 끊긴 동안 쓴 글을 버리지 않는다.

        그 사이 아무도 고치지 않은 메모에는 그대로 다시 보낸다. 메모가 지워졌거나
        다른 사람이 고쳤으면 그쪽을 덮어쓰지 않고 새 메모로 남긴다."""
        was_private = was_private or {}
        for note_id, text in unsent.items():
            note = self._notes.get(note_id)
            if note is not None and (note.get("content") or "") == text:
                continue        # 끊기기 직전에 보낸 것이 이미 들어가 있다
            if (note is not None
                    and int(note.get("version", 0)) == seen_versions.get(note_id)
                    and not self._locks.get(note_id)):
                self._pending_resend[note_id] = text
                self._client.acquire_lock(note_id)
            else:
                self._keep_as_new_note(text, private=was_private.get(note_id, False))

    def _keep_as_new_note(self, text: str, private: bool = False) -> None:
        """원래 메모에 넣지 못한 글을 새 메모로 남긴다.

        나만 보던 메모의 글이면 새 메모도 나만 보게 만든다. 아니면 설정의 기본을 따른다."""
        self._orphan_texts.append(text)
        self._create_note(self._settings.default_note_color,
                          private=True if private else None)

    def _on_note_created(self, note: dict) -> None:
        self._notes[note["id"]] = note
        self._append_card(note)
        self._move_card_to_top(note["id"])
        self._apply_search_filter()
        # 한 번의 "새 메모" 클릭이 협업자 모두의 바탕 화면에 창을 띄우지 않도록,
        # 만든 사용자에게만 띄우고 나머지 클라이언트에서는 숨김으로 둔다.
        is_creator = (note.get("created_by") == self._client.nickname())
        st = self._local.state.get_note_state(note["id"])
        st.hidden = not is_creator
        restoring = self._recreate_queue.pop(0) if (
            is_creator and self._recreate_queue) else None
        if restoring is not None:
            st.x, st.y = restoring["pos"]
            st.hidden = restoring["hidden"]
            self._pending_resend[note["id"]] = restoring["content"]
            self._client.acquire_lock(note["id"])
        elif is_creator:
            pos = self._new_note_position(
                int(note.get("width") or config.DEFAULT_NOTE_WIDTH),
                int(note.get("height") or config.DEFAULT_NOTE_HEIGHT),
                self._take_new_note_spot())
            st.x, st.y = pos.x(), pos.y()
        self._local.save()
        if is_creator:
            # 아이콘으로 접어 둔 폴더에 새 메모를 만들면 보이지 않는다. 폴더를 연다.
            self._open_folder_of(note["id"], animate=False)
        self._ensure_sticky(note)
        if restoring is not None:
            self._recovery_notes.add(note["id"])   # '보냈습니다' 안내는 띄우지 않는다
            self._refresh_folder_icons()
            self._invalidate_tray_menu()
            return
        if is_creator:
            recovered = bool(self._orphan_texts)
            if recovered:
                # 끊긴 동안 쓴 글을 담으려고 만든 메모다. 그 글을 넣는다.
                self._pending_resend[note["id"]] = self._orphan_texts.pop(0)
                self._recovery_notes.add(note["id"])
                self._client.acquire_lock(note["id"])
                self._status.showMessage(
                    i18n.t("main.status_unsent_new_note"), 10000)
            # 메모는 평소 바탕 화면 쪽에 깔린다. 방금 만든 메모는 다른 창을 쓰던
            # 중이어도 보여야 하고 바로 쓸 수 있어야 하므로 앞으로 올린다.
            w = self._stickies.get(note["id"])
            if w is not None:
                w.bring_to_front(start_editing=not recovered)
        self._refresh_folder_icons()
        self._invalidate_tray_menu()

    def _on_note_updated(self, note: dict) -> None:
        self._notes[note["id"]] = note
        c = self._cards.get(note["id"])
        if c is not None:
            c.update_from(note, visible=not self._local.state.get_note_state(note["id"]).hidden)
        w = self._stickies.get(note["id"])
        if w is not None:
            w.apply_server_state(note)
        # 다른 사람이 폴더를 옮겼을 수 있다. 접어 둔 폴더로 들어가면 감춘다.
        self._sync_note_shown(note["id"])
        self._refresh_folder_icons()
        # 서버는 실제 변경에만 note_updated 를 보내므로, 도착할 때마다 맨 위로 올린다.
        self._move_card_to_top(note["id"])
        self._apply_search_filter()
        self._invalidate_tray_menu()

    def _move_card_to_top(self, note_id: str) -> None:
        card = self._cards.get(note_id)
        if card is None:
            return
        self._cards_layout.removeWidget(card)
        self._cards_layout.insertWidget(0, card)

    def _on_note_deleted(self, note_id: str) -> None:
        # 내가 지워 달라고 한 메모가 실제로 지워졌다. 이제 되돌릴 수 있다.
        undoable = self._pending_deletes.pop(note_id, None)
        if undoable is not None:
            self._undo_snapshots.append(undoable)
            self._undo_btn.show()
            self._undo_timer.start()
            self._status.showMessage(self._delete_message, self._UNDO_MS)
        self._notes.pop(note_id, None)
        if note_id in self._selected_notes:
            self._selected_notes.discard(note_id)
            self._refresh_bulk_bar()
        c = self._cards.pop(note_id, None)
        if c is not None:
            c.setParent(None)
            c.deleteLater()
        w = self._stickies.pop(note_id, None)
        if w is not None:
            # closeEvent 의 hideRequested 가 local_state 를 오염시키지 않도록
            # teardown 으로 표시한 뒤 닫는다(_on_welcomed 와 같은 예방책).
            w._teardown = True
            w.close()
            w.deleteLater()
        self._local.state.remove_note(note_id)
        self._local.save()
        self._apply_search_filter()
        self._refresh_folder_icons()
        self._invalidate_tray_menu()

    def _on_folder_created(self, folder: dict) -> None:
        if not folder.get("id"):
            return
        self._folders[folder["id"]] = folder
        self._refresh_folder_chip()
        self._refresh_card_folder_context()
        self._refresh_folder_icons()

    def _on_folder_updated(self, folder: dict) -> None:
        if not folder.get("id"):
            return
        self._folders[folder["id"]] = folder
        if self._current_folder == folder["id"]:
            self._refresh_folder_chip()
        self._refresh_card_folder_context()
        self._apply_search_filter()
        self._refresh_folder_icons()

    def _on_folder_deleted(self, folder_id: str) -> None:
        self._folders.pop(folder_id, None)
        if self._current_folder == folder_id:
            self._current_folder = FOLDER_ALL
        self._refresh_folder_chip()
        self._refresh_card_folder_context()
        self._apply_search_filter()
        # 폴더가 없어지면 아이콘과 그 상태도 치운다. 폴더에 있던 메모는 미분류가
        # 되어 다시 보인다.
        if self._local.state.folder_icons.pop(folder_id, None) is not None:
            self._local.save()
        self._refresh_folder_icons()
        for nid in list(self._notes):
            self._sync_note_shown(nid)

    def _refresh_card_folder_context(self) -> None:
        ctx = self._folder_context_for_cards()
        for card in self._cards.values():
            card.set_folder_context(ctx)

    def _refresh_folder_chip(self) -> None:
        label, _ = self._current_folder_label_and_color()
        self._folder_chip.setText(label)
        cf = self._current_folder
        is_private_folder = (
            cf not in (FOLDER_ALL, FOLDER_NONE)
            and bool((self._folders.get(cf) or {}).get("private_owner"))
        )
        icon_name = "lock" if is_private_folder else "folder"
        self._folder_chip.setIcon(svg_icon(icon_name, color="#3A3A3A"))

    def _current_folder_label_and_color(self) -> tuple[str, str]:
        cf = self._current_folder
        if cf == FOLDER_ALL:
            return i18n.t("main.folder_all"), "#7B7B7B"
        if cf == FOLDER_NONE:
            return i18n.t("main.folder_uncategorized"), "#7B7B7B"
        f = self._folders.get(cf)
        if not f:
            return i18n.t("main.folder_all"), "#7B7B7B"
        name = f.get("name") or i18n.t("main.folder_default_name")
        label = (i18n.t("main.folder_private_label", name=name)
                 if f.get("private_owner") else name)
        return label, f.get("color") or config.DEFAULT_COLOR

    def _show_folder_selector(self) -> None:
        menu = QMenu(self._folder_chip)
        add_centered_menu_action(
            menu, svg_icon("folder"), i18n.t("main.folder_all"),
            lambda: self._select_folder(FOLDER_ALL))
        add_centered_menu_action(
            menu, None, i18n.t("main.folder_uncategorized"),
            lambda: self._select_folder(FOLDER_NONE))
        folders = sorted(
            self._folders.values(),
            key=lambda f: (f.get("name") or "").lower(),
        )
        if folders:
            menu.addSeparator()
            for f in folders:
                name = f.get("name") or i18n.t("main.folder_default_name")
                if f.get("private_owner"):
                    name = i18n.t("main.folder_private_label", name=name)
                    icon = svg_icon("lock", color="#5B5B5B")
                else:
                    color = f.get("color") or config.DEFAULT_COLOR
                    icon = color_swatch_icon(color)
                fid = f["id"]
                add_centered_menu_action(
                    menu, icon, name,
                    lambda _fid=fid: self._select_folder(_fid))
        menu.addSeparator()
        add_centered_menu_action(
            menu, svg_icon("plus"), i18n.t("main.menu_new_folder"),
            self._create_folder_dialog)
        menu.exec(self._folder_chip.mapToGlobal(
            QPoint(0, self._folder_chip.height())))

    def _show_folder_menu(self) -> None:
        cf = self._current_folder
        is_real = cf not in (FOLDER_ALL, FOLDER_NONE)
        menu = QMenu(self._folder_menu_btn)
        add_centered_menu_action(
            menu, svg_icon("eye_on"), i18n.t("main.menu_folder_show_all"),
            lambda: self._bulk_set_folder_visibility(True))
        add_centered_menu_action(
            menu, svg_icon("eye_off"), i18n.t("main.menu_folder_hide_all"),
            lambda: self._bulk_set_folder_visibility(False))
        add_centered_menu_action(
            menu, svg_icon("clock"), i18n.t("main.menu_folder_history"),
            self._open_folder_history)
        if is_real:
            menu.addSeparator()
            if cf in self._local.state.folder_icons:
                add_centered_menu_action(
                    menu, svg_icon("unfold"),
                    i18n.t("main.menu_folder_icon_remove"),
                    lambda: self._remove_folder_icon(cf))
            else:
                add_centered_menu_action(
                    menu, svg_icon("folder"),
                    i18n.t("main.menu_folder_icon_place"),
                    lambda: self._place_folder_icon(cf))
            add_centered_menu_action(
                menu, None, i18n.t("main.menu_folder_edit"),
                self._edit_current_folder)
            add_centered_menu_action(
                menu, None, i18n.t("main.menu_folder_delete"),
                self._delete_current_folder)
        menu.exec(self._folder_menu_btn.mapToGlobal(
            QPoint(0, self._folder_menu_btn.height())))

    def _select_folder(self, folder_id: str) -> None:
        self._current_folder = folder_id
        self._refresh_folder_chip()
        self._apply_search_filter()

    def _create_folder_dialog(self) -> None:
        if not self._require_online():
            return
        dlg = FolderEditDialog(title=i18n.t("main.dialog_new_folder_title"), parent=self)
        if dlg.exec() != QDialog.Accepted:
            return
        name = dlg.name() or i18n.t("main.folder_new_default_name")
        self._client.create_folder(name, dlg.color(), dlg.private())

    def _edit_current_folder(self) -> None:
        f = self._folders.get(self._current_folder)
        if f is None or not self._require_online():
            return
        dlg = FolderEditDialog(
            title=i18n.t("main.dialog_edit_folder_title"),
            name=f.get("name") or "",
            color=f.get("color") or config.DEFAULT_COLOR,
            private=bool(f.get("private_owner")),
            parent=self,
        )
        if dlg.exec() != QDialog.Accepted:
            return
        kwargs: dict = {}
        new_name = dlg.name() or (f.get("name") or "")
        if new_name != f.get("name"):
            kwargs["name"] = new_name
        if dlg.color() != f.get("color"):
            kwargs["color"] = dlg.color()
        if dlg.private() != bool(f.get("private_owner")):
            kwargs["private"] = dlg.private()
        if kwargs:
            self._client.update_folder(f["id"], **kwargs)

    def _delete_current_folder(self) -> None:
        f = self._folders.get(self._current_folder)
        if f is None or not self._require_online():
            return
        if not confirm.ask(
                self, i18n.t("main.msg_folder_delete_title"),
                i18n.t("main.msg_folder_delete_body", name=f.get('name', '')),
                detail=i18n.t("main.msg_folder_delete_detail"),
                action=i18n.t("common.btn_delete"), danger=True):
            return
        self._client.delete_folder(f["id"])

    def _bulk_set_folder_visibility(self, visible: bool) -> None:
        for note_id, note in self._notes.items():
            if not self._note_in_current_folder(note):
                continue
            self._set_note_visibility(note_id, visible)

    def _open_folder_history(self) -> None:
        self._open_history()

    def _note_in_current_folder(self, note: dict) -> bool:
        cf = self._current_folder
        nfid = note.get("folder_id") or None
        if cf == FOLDER_ALL:
            return True
        if cf == FOLDER_NONE:
            return nfid is None
        return nfid == cf

    def _on_lock_granted(self, note_id: str) -> None:
        self._locks[note_id] = self._client.nickname()
        # 복원용으로 잡은 잠금이면 스냅샷만 푸시하고 곧바로 해제한다(편집 모드로 열지 않음).
        pending_content = self._pending_restore.pop(note_id, None)
        if pending_content is not None:
            note = self._notes.get(note_id)
            if note is None:
                self._client.release_lock(note_id)
                return
            self._client.update_note(
                note_id,
                pending_content,
                note.get("color", config.DEFAULT_COLOR),
                int(note.get("width", config.DEFAULT_NOTE_WIDTH)),
                int(note.get("height", config.DEFAULT_NOTE_HEIGHT)),
                int(note.get("version", 1)),
            )
            self._client.release_lock(note_id)
            self._status.showMessage(i18n.t("main.status_note_restored"), 4000)
            return
        resend = self._pending_resend.pop(note_id, None)
        if resend is not None:
            note = self._notes.get(note_id)
            if note is None:
                self._client.release_lock(note_id)
                self._keep_as_new_note(resend)
                return
            self._client.update_note(
                note_id, resend,
                note.get("color", config.DEFAULT_COLOR),
                int(note.get("width", config.DEFAULT_NOTE_WIDTH)),
                int(note.get("height", config.DEFAULT_NOTE_HEIGHT)),
                int(note.get("version", 1)),
            )
            self._client.release_lock(note_id)
            if note_id in self._recovery_notes:
                # 새 메모로 남겼다는 안내가 이미 떠 있다.
                self._recovery_notes.discard(note_id)
            else:
                self._status.showMessage(
                    i18n.t("main.status_unsent_resent"), 6000)
            return
        w = self._stickies.get(note_id)
        if w is not None:
            w.enter_edit_mode()

    def _on_lock_denied(self, note_id: str, holder: str) -> None:
        # 다시 보내려던 글: 그 사이 다른 사람이 잡았다. 새 메모로 남긴다.
        resend = self._pending_resend.pop(note_id, None)
        if resend is not None:
            self._keep_as_new_note(
                resend, private=self._note_is_private(self._notes.get(note_id)))
            return
        # 복원 대기 중 거부면 조용히 넘기지 말고 복원 불가를 알린다.
        if self._pending_restore.pop(note_id, None) is not None:
            confirm.notify(
                self,
                i18n.t("main.msg_restore_unavailable_title"),
                i18n.t("main.msg_restore_locked_body",
                       holder=holder or i18n.t("main.other_user")),
            )
            return
        w = self._stickies.get(note_id)
        if w is not None:
            w.show_lock_denied(holder or i18n.t("main.other_user"))

    def _on_restore_requested(self, note_id: str, content: str) -> None:
        """표준 lock → update → release 흐름으로 처리해야 서버가 롤백에 대한
        새 이력 항목을 만들고 모든 클라이언트가 변경을 받아 본다."""
        if note_id not in self._notes:
            confirm.notify(
                self, i18n.t("main.msg_restore_unavailable_title"),
                i18n.t("main.msg_restore_note_gone_body"),
                icon=QMessageBox.Warning)
            return
        if not self._require_online():
            return
        self._pending_restore[note_id] = content
        self._client.acquire_lock(note_id)

    def _on_lock_held(self, note_id: str, holder: str) -> None:
        self._locks[note_id] = holder
        w = self._stickies.get(note_id)
        if w is not None:
            w.set_lock_holder(holder)

    def _on_lock_released(self, note_id: str) -> None:
        self._locks.pop(note_id, None)
        w = self._stickies.get(note_id)
        if w is not None:
            w.set_lock_holder(None)

    def _on_user_joined(self, nickname: str) -> None:
        if nickname not in self._users:
            self._users.append(nickname)
        self._local.state.touch_user(nickname, joined=True)
        self._local.save()
        self._refresh_status_text()
        self._refresh_sessions_dialog()
        self._status.showMessage(i18n.t("main.status_user_joined", nick=nickname), 3000)

    def _on_user_left(self, nickname: str) -> None:
        if nickname in self._users:
            self._users.remove(nickname)
        # 참가자 대화상자가 last_seen_at 이 아니라 last_disconnected_at 으로
        # 세션 종료 시각을 보여주므로, 여기서 disconnect 시점을 명시적으로 찍는다.
        self._local.state.touch_user(
            nickname, joined=False, disconnected=True)
        self._local.save()
        self._refresh_status_text()
        self._refresh_sessions_dialog()
        self._status.showMessage(i18n.t("main.status_user_left", nick=nickname), 3000)

    def _on_history_appended(self, entry: dict) -> None:
        if self._history_viewer is not None and self._history_viewer.isVisible():
            self._history_viewer.append_entry(entry)

    def _on_history_list(self, _note_id: str, entries: list) -> None:
        if self._history_viewer is not None:
            self._history_viewer.set_entries(entries)

    def _on_note_announced(self, entry: dict) -> None:
        """다른 사람이 공개 메모를 새로 만들었다.

        남의 '나만 보기' 메모는 이 PC 로 오지 않으므로 여기 닿는 것은 모두
        내가 볼 수 있는 메모다. 내가 만든 것은 알리지 않는다."""
        who = str(entry.get("user") or "")
        if (not who or who == self._client.nickname()
                or not self._settings.notify_new_note):
            return
        self._announce[who] = self._announce.get(who, 0) + 1
        self._announce_timer.start()

    def _on_note_shared(self, entry: dict) -> None:
        """다른 사람이 나만 보던 메모를 공개로 바꿨다(폴더째 공개한 경우 포함)."""
        who = str(entry.get("user") or "")
        if (not who or who == self._client.nickname()
                or not self._settings.notify_new_note):
            return
        self._announce_shared[who] = self._announce_shared.get(who, 0) + 1
        self._announce_timer.start()

    def _flush_announcements(self) -> None:
        created, self._announce = self._announce, {}
        shared, self._announce_shared = self._announce_shared, {}
        for who, count in created.items():
            self._show_notification(
                i18n.t("main.notify_new_note", name=who) if count == 1
                else i18n.t("main.notify_new_notes", name=who, count=count))
        for who, count in shared.items():
            self._show_notification(
                i18n.t("main.notify_shared_note", name=who) if count == 1
                else i18n.t("main.notify_shared_notes", name=who, count=count))

    def _show_notification(self, text: str, warn: bool = False) -> None:
        """화면 오른쪽 아래에 잠깐 알린다. 메인 창이 열려 있으면 상태줄에도 남는다.

        Windows 알림을 쓰지 않는다. Windows 설정에서 알림을 꺼 둔 PC 에서는
        아무것도 뜨지 않았다(app/ui/toast.py)."""
        self._toasts.show_text(text, warn=warn)
        self._status.showMessage(text, 6000)

    def _append_card(self, note: dict) -> None:
        if note["id"] in self._cards:
            self._cards[note["id"]].update_from(
                note,
                visible=not self._local.state.get_note_state(note["id"]).hidden,
            )
            self._cards[note["id"]].set_folder_context(
                self._folder_context_for_cards())
            return
        card = NoteCard(
            note,
            visible=not self._local.state.get_note_state(note["id"]).hidden,
        )
        card.set_folder_context(self._folder_context_for_cards())
        card.set_view(self._settings.notes_view_density,
                      self._settings.notes_view_layout)
        if self._selection_mode:
            card.set_selection_mode(True)
        card.clicked.connect(self._on_card_clicked)
        card.rangeClickRequested.connect(self._on_card_range_click)
        card.toggleVisibility.connect(self._toggle_visibility_from_card)
        card.deleteRequested.connect(self._delete_note_confirm)
        card.historyRequested.connect(self._open_history_for)
        card.privateToggleRequested.connect(self._on_card_private_toggled)
        card.moveToFolderRequested.connect(self._on_card_move_to_folder)
        self._cards[note["id"]] = card
        self._cards_layout.addWidget(card)

    def _folder_context_for_cards(self) -> list[tuple[str, str]]:
        return [
            (fid, (i18n.t("main.folder_private_label",
                          name=f.get("name") or i18n.t("main.folder_default_name"))
                   if f.get("private_owner")
                   else (f.get("name") or i18n.t("main.folder_default_name"))))
            for fid, f in sorted(
                self._folders.items(),
                key=lambda kv: (kv[1].get("name") or "").lower())
        ]

    def _on_select_mode_toggled(self, on: bool) -> None:
        self._selection_mode = on
        if not on:
            self._selected_notes.clear()
            self._selection_anchor = None
        for card in self._cards.values():
            card.set_selection_mode(on)
            card.set_selected(False)
        self._bulk_bar.setVisible(on)
        self._refresh_bulk_bar()
        if on:
            self._status.showMessage(
                i18n.t("main.status_selection_hint"),
                4000)

    def _refresh_bulk_bar(self) -> None:
        count = len(self._selected_notes)
        self._bulk_count_lbl.setText(i18n.t("main.bulk_selected_count", count=count))
        for btn in (self._bulk_folder_btn,
                    self._bulk_private_btn,
                    self._bulk_delete_btn):
            btn.setEnabled(count > 0)
        visible_ids = self._visible_card_ids()
        all_selected = (
            bool(visible_ids)
            and visible_ids.issubset(self._selected_notes))
        self._bulk_select_all_btn.setText(
            i18n.t("main.bulk_deselect_all") if all_selected
            else i18n.t("main.bulk_select_all"))

    def _visible_card_ids(self) -> set[str]:
        return {nid for nid, c in self._cards.items()
                if c.isVisible()}

    def _visible_card_ids_ordered(self) -> list[str]:
        """Shift+Click 범위가 사용자가 보는 순서와 일치하도록 화면 순서대로 반환한다."""
        visible = [(nid, c) for nid, c in self._cards.items()
                   if c.isVisible()]
        visible.sort(key=lambda x: self._cards_layout.indexOf(x[1]))
        return [nid for nid, _ in visible]

    def _on_card_range_click(self, note_id: str) -> None:
        """anchor 와 이번 클릭 사이의 가시 카드를 양 끝 포함으로 선택한다.
        anchor 가 없으면 단일 토글로 폴백한다."""
        if not self._selection_mode:
            return
        anchor = self._selection_anchor
        if anchor is None or anchor == note_id:
            self._on_card_clicked(note_id)
            return
        ordered = self._visible_card_ids_ordered()
        if anchor not in ordered or note_id not in ordered:
            self._on_card_clicked(note_id)
            return
        a_idx = ordered.index(anchor)
        n_idx = ordered.index(note_id)
        if a_idx > n_idx:
            a_idx, n_idx = n_idx, a_idx
        for rid in ordered[a_idx:n_idx + 1]:
            self._selected_notes.add(rid)
            card = self._cards.get(rid)
            if card is not None:
                card.set_selected(True)
        # 연속 Shift+Click 이 최근 끝점에서 계속 확장되도록 대상을 새 anchor 로 둔다.
        self._selection_anchor = note_id
        self._refresh_bulk_bar()

    def _select_all_via_shortcut(self) -> None:
        if not self._selection_mode:
            self._select_btn.setChecked(True)
        visible_ids = self._visible_card_ids()
        if not visible_ids:
            return
        self._selected_notes |= visible_ids
        for nid, card in self._cards.items():
            card.set_selected(nid in self._selected_notes)
        self._refresh_bulk_bar()

    def _bulk_select_all(self) -> None:
        visible_ids = self._visible_card_ids()
        all_selected = (
            bool(visible_ids)
            and visible_ids.issubset(self._selected_notes))
        if all_selected:
            self._selected_notes -= visible_ids
        else:
            self._selected_notes |= visible_ids
        for nid, card in self._cards.items():
            card.set_selected(nid in self._selected_notes)
        self._refresh_bulk_bar()

    def _bulk_show_folder_menu(self) -> None:
        if not self._selected_notes:
            return
        menu = QMenu(self._bulk_folder_btn)
        add_centered_menu_action(
            menu, None, i18n.t("main.folder_uncategorized"),
            lambda: self._bulk_apply_folder(None))
        if self._folders:
            menu.addSeparator()
            for fid, fname in self._folder_context_for_cards():
                add_centered_menu_action(
                    menu, None, fname,
                    lambda _fid=fid: self._bulk_apply_folder(_fid))
        menu.exec(self._bulk_folder_btn.mapToGlobal(
            QPoint(0, -menu.sizeHint().height())))

    def _bulk_apply_folder(self, folder_id) -> None:
        if not self._require_online():
            return
        ids = list(self._selected_notes)
        for nid in ids:
            if nid not in self._notes:
                continue
            note = self._notes[nid]
            note["folder_id"] = folder_id
            card = self._cards.get(nid)
            if card is not None:
                card.update_from(
                    note,
                    visible=not self._local.state.get_note_state(nid).hidden,
                )
            self._client.set_note_folder(nid, folder_id)
            self._sync_note_shown(nid)
        self._apply_search_filter()
        self._refresh_folder_icons()
        target = ((self._folders.get(folder_id, {}) or {}).get("name")
                  if folder_id else i18n.t("main.folder_uncategorized"))
        self._status.showMessage(
            i18n.t("main.bulk_moved", count=len(ids), folder=target),
            4000)
        self._exit_selection_mode()

    def _bulk_show_privacy_menu(self) -> None:
        menu = QMenu(self._bulk_private_btn)
        add_centered_menu_action(
            menu, svg_icon("lock", color="#3A3A3A"),
            i18n.t("main.menu_bulk_set_private"),
            lambda: self._bulk_apply_privacy(True))
        add_centered_menu_action(
            menu, None, i18n.t("main.menu_bulk_set_public"),
            lambda: self._bulk_apply_privacy(False))
        menu.exec(self._bulk_private_btn.mapToGlobal(
            QPoint(0, -menu.sizeHint().height())))

    def _bulk_apply_privacy(self, private: bool) -> None:
        if not self._require_online():
            return
        ids = list(self._selected_notes)
        for nid in ids:
            if nid not in self._notes:
                continue
            note = self._notes[nid]
            note["private_owner"] = (
                self._client.nickname() if private else None)
            card = self._cards.get(nid)
            if card is not None:
                card.update_from(
                    note,
                    visible=not self._local.state.get_note_state(nid).hidden,
                )
            w = self._stickies.get(nid)
            if w is not None:
                w.apply_server_state(note)
            self._client.set_note_privacy(nid, private)
        self._status.showMessage(
            i18n.t("main.bulk_privacy_on", count=len(ids)) if private
            else i18n.t("main.bulk_privacy_off", count=len(ids)),
            4000)
        self._exit_selection_mode()

    def _bulk_delete(self) -> None:
        ids = [nid for nid in self._selected_notes if nid in self._notes]
        if not ids or not self._require_online():
            return
        # 다른 사람이 편집 중인 메모는 지울 수 없다. 나머지만 지우고 따로 알린다.
        locked = [nid for nid in ids if self._locked_by_other(nid)]
        free = [nid for nid in ids if nid not in locked]
        if not free:
            self._tell_locked(self._locked_by_other(locked[0]))
            return
        if not confirm.ask(
                self, i18n.t("main.msg_bulk_delete_title"),
                i18n.t("main.msg_bulk_delete_body", count=len(free)),
                detail=i18n.t("main.msg_delete_scope"),
                action=i18n.t("common.btn_delete"), danger=True):
            return
        self._delete_with_undo(free, i18n.t("main.bulk_deleted", count=len(free)))
        self._exit_selection_mode()
        if locked:
            confirm.notify(
                self, i18n.t("main.msg_delete_locked_title"),
                i18n.t("main.msg_bulk_locked_body", count=len(locked)),
                icon=QMessageBox.Warning)

    def _exit_selection_mode(self) -> None:
        if not self._selection_mode:
            return
        # _on_select_mode_toggled 가 두 번 재귀되지 않도록 토글 시그널을 막는다.
        self._select_btn.blockSignals(True)
        self._select_btn.setChecked(False)
        self._select_btn.blockSignals(False)
        self._on_select_mode_toggled(False)

    def _on_card_private_toggled(self, note_id: str, private: bool) -> None:
        """로컬 사본을 먼저 바꾸는 낙관적 갱신이라, MSG_SET_NOTE_PRIVACY 를
        무시하는 구버전 호스트와 통신해도 최소한 로컬 피드백은 보장된다."""
        if not self._require_online():
            return
        note = self._notes.get(note_id)
        if note is not None:
            note["private_owner"] = (
                self._client.nickname() if private else None)
            c = self._cards.get(note_id)
            if c is not None:
                c.update_from(
                    note,
                    visible=not self._local.state.get_note_state(note_id).hidden,
                )
            w = self._stickies.get(note_id)
            if w is not None:
                w.apply_server_state(note)
        self._client.set_note_privacy(note_id, private)
        self._status.showMessage(
            i18n.t("main.status_note_private_on") if private
            else i18n.t("main.status_note_private_off"),
            4000)

    def _on_card_move_to_folder(self, note_id: str,
                                  folder_id) -> None:
        if not self._require_online():
            return
        note = self._notes.get(note_id)
        if note is not None:
            note["folder_id"] = folder_id
            c = self._cards.get(note_id)
            if c is not None:
                c.update_from(
                    note,
                    visible=not self._local.state.get_note_state(note_id).hidden,
                )
        self._client.set_note_folder(note_id, folder_id)
        self._sync_note_shown(note_id)
        self._refresh_folder_icons()
        self._apply_search_filter()
        if folder_id is None:
            self._status.showMessage(i18n.t("main.status_note_moved_uncategorized"), 3000)
        else:
            fname = ((self._folders.get(folder_id, {}) or {}).get("name")
                     or i18n.t("main.folder_default_name"))
            self._status.showMessage(
                i18n.t("main.status_note_moved_folder", folder=fname), 3000)

    def _on_card_clicked(self, note_id: str) -> None:
        if self._selection_mode:
            if note_id in self._selected_notes:
                self._selected_notes.discard(note_id)
            else:
                self._selected_notes.add(note_id)
            card = self._cards.get(note_id)
            if card is not None:
                card.set_selected(note_id in self._selected_notes)
            # Shift+Click 이 범위 계산에 쓰도록 마지막 일반 클릭을 anchor 로 기록한다.
            self._selection_anchor = note_id
            self._refresh_bulk_bar()
            return
        st = self._local.state.get_note_state(note_id)
        if st.hidden:
            self._set_note_visibility(note_id, True)
        else:
            # 아이콘으로 접어 둔 폴더 안의 메모면 폴더를 열어야 보인다.
            self._open_folder_of(note_id)
        w = self._stickies.get(note_id)
        if w is not None:
            # 접어 둔 메모는 펼쳐서 보여 준다.
            w.reveal()

    def _toggle_visibility_from_card(self, note_id: str) -> None:
        st = self._local.state.get_note_state(note_id)
        # 현재 hidden 값을 새 visible 로 넘겨 가시 상태를 뒤집는다.
        self._set_note_visibility(note_id, st.hidden)

    def _delete_with_undo(self, note_ids: list[str], message: str) -> None:
        """메모를 지우고 잠깐 '되돌리기'를 보인다.

        되돌리면 같은 내용·색·폴더로 새 메모를 만든다(지운 메모의 이력은
        돌아오지 않는다). 내 바탕화면 위치도 그대로 둔다."""
        pending: dict[str, dict] = {}
        for nid in note_ids:
            note = self._notes.get(nid)
            if note is None:
                continue
            st = self._local.state.get_note_state(nid)
            pending[nid] = {
                "content": note.get("content") or "",
                "color": note.get("color") or config.DEFAULT_COLOR,
                "width": int(note.get("width") or config.DEFAULT_NOTE_WIDTH),
                "height": int(note.get("height") or config.DEFAULT_NOTE_HEIGHT),
                "folder_id": note.get("folder_id"),
                "private": bool(note.get("private_owner")),
                "pos": (st.x, st.y), "hidden": st.hidden,
            }
        if not pending:
            return
        # 호스트가 지웠다고 알려 온 메모만 되돌릴 수 있게 한다(_on_note_deleted).
        # 보내자마자 '지웠습니다'와 되돌리기를 띄우면, 호스트가 거절했을 때(다른
        # 사람이 편집 중) 지워지지도 않은 메모를 되돌리기가 한 벌 더 만든다.
        self._undo_timer.stop()
        self._forget_undo()
        self._pending_deletes = pending
        self._delete_message = message
        self._delete_round += 1
        this_round = self._delete_round
        for nid in list(pending):
            self._client.delete_note(nid)
        QTimer.singleShot(
            5000, lambda: self._drop_unconfirmed_deletes(this_round))

    def _drop_unconfirmed_deletes(self, this_round: int) -> None:
        """답이 오지 않은 삭제 요청을 잊는다(그 사이 새로 지운 것은 건드리지 않는다)."""
        if this_round == self._delete_round:
            self._pending_deletes.clear()

    def _forget_undo(self) -> None:
        self._undo_snapshots = []
        self._undo_btn.hide()

    def _undo_delete(self) -> None:
        snapshots, self._undo_snapshots = self._undo_snapshots, []
        self._undo_timer.stop()
        self._undo_btn.hide()
        self._status.clearMessage()
        if not self._connected:
            # 끊긴 동안에는 만들 수 없다. 까닭을 알린다.
            self._status.showMessage(
                i18n.t("main.status_offline_no_create"), 5000)
            return
        me = self._client.nickname()
        for snap in snapshots:
            self._recreate_queue.append(snap)
            folder_id = snap["folder_id"] if snap["folder_id"] in self._folders else None
            self._client.create_note(
                snap["color"], snap["width"], snap["height"],
                folder_id=folder_id,
                private_owner=me if snap["private"] else None,
                quiet=True)

    def _note_title(self, note: dict, limit: int = 40) -> str:
        """목록과 확인창에서 메모를 가리키는 이름: 첫 줄, 없으면 내용의 종류."""
        raw = note.get("content") or ""
        first_line = (html_to_plain(raw).splitlines() or [""])[0].strip()
        if first_line:
            title = first_line
        elif has_non_checkbox_image(raw):
            title = i18n.t("main.note_title_image")
        else:
            title = i18n.t("main.note_title_empty")
        return title[:limit] + "…" if len(title) > limit else title

    def _delete_note_confirm(self, note_id: str) -> None:
        note = self._notes.get(note_id)
        if note is None or not self._require_online():
            return
        holder = self._locked_by_other(note_id)
        if holder:
            # 지울 수 없는 까닭을 확인창을 띄우기 전에 알린다.
            self._tell_locked(holder)
            return
        # 삭제는 모든 참가자에게서 지워지고 되돌릴 수 없다. 무엇을 지우는지와
        # 그 범위를 적고, 기본 단추는 취소로 둔다.
        if confirm.ask(
                self, i18n.t("main.msg_delete_confirm_title"),
                i18n.t("main.msg_delete_confirm_body",
                       title=self._note_title(note, 30)),
                detail=i18n.t("main.msg_delete_scope"),
                action=i18n.t("common.btn_delete"), danger=True):
            self._delete_with_undo([note_id], i18n.t("main.status_deleted"))

    def _refresh_empty_panel(self, total: int, visible: int) -> None:
        """보여 줄 카드가 없으면 목록 자리에 안내를 띄운다."""
        if visible:
            kind = ""
        elif self._host_unreachable():
            kind = "unreachable"
        elif self._search_query:
            kind = "search"
        elif not total:
            kind = "none"
        else:
            kind = "folder"
        self._empty_kind = kind
        self._empty_panel.setVisible(bool(kind))
        self._scroll.setVisible(not kind)
        if not kind:
            return
        new_note = i18n.t("main.btn_new_note")
        if kind == "unreachable":
            ls = self._local.state
            self._empty_title.setText(i18n.t("main.empty_unreachable_title"))
            self._empty_body.setText(i18n.t(
                "main.empty_unreachable_body",
                address=f"{ls.last_host_ip}:{ls.last_host_port}"))
            self._empty_action.setText(i18n.t("main.empty_unreachable_action"))
        elif kind == "search":
            shown = self._search_input.text().strip()
            if len(shown) > 24:
                shown = shown[:24] + "…"
            self._empty_title.setText(
                i18n.t("main.empty_search_title", query=shown))
            self._empty_body.setText("")
            self._empty_action.setText(i18n.t("main.empty_search_clear"))
        elif kind == "none":
            self._empty_title.setText(i18n.t("main.empty_none_title"))
            self._empty_body.setText("")
            self._empty_action.setText(new_note)
        else:
            self._empty_title.setText(i18n.t("main.empty_folder_title"))
            self._empty_body.setText("")
            self._empty_action.setText(new_note)
        self._empty_body.setVisible(bool(self._empty_body.text()))

    def _refresh_empty_hint(self) -> None:
        total = len(self._notes)
        # isVisible() 은 창이 아직 뜨지 않았으면 늘 False 다. 숨겼는지를 본다.
        visible = sum(1 for c in self._cards.values() if not c.isHidden())
        self._refresh_empty_panel(total, visible)
        self._empty_hint.setVisible(bool(total))
        if not total:
            return
        # "7 / 7개" 같은 잡음을 피하려고 일부가 가려진 경우에만 분수로 표시한다.
        if visible == total:
            self._empty_hint.setText(i18n.t("main.hint_count_total", total=total))
        else:
            self._empty_hint.setText(
                i18n.t("main.hint_count_partial", visible=visible, total=total))

    def _apply_search_filter(self, text: str | None = None) -> None:
        """폴더 선택과 검색어를 AND 로 결합해 카드 가시성을 정한다."""
        if text is None:
            text = self._search_input.text()
        query = (text or "").strip().lower()
        self._search_query = query
        for note_id, card in self._cards.items():
            note = self._notes.get(note_id, {})
            in_folder = self._note_in_current_folder(note)
            if not in_folder:
                card.setVisible(False)
                continue
            if not query:
                card.setVisible(True)
                continue
            plain = html_to_plain(note.get("content") or "").lower()
            card.setVisible(query in plain)
        self._refresh_empty_hint()

    def _ensure_sticky(self, note: dict) -> None:
        note_id = note["id"]
        created = note_id not in self._stickies
        if not created:
            w = self._stickies[note_id]
            w.apply_server_state(note)
        else:
            w = StickyNoteWidget(
                note, self._client.nickname(),
                default_font_size=self._settings.default_font_size,
                settings=self._settings,
            )
            w.requestLock.connect(self._client.acquire_lock)
            w.releaseLock.connect(self._client.release_lock)
            w.commitEdit.connect(self._on_commit_edit)
            w.deleteRequested.connect(self._delete_note_confirm)
            w.hideRequested.connect(
                lambda nid: self._set_note_visibility(nid, False))
            w.historyRequested.connect(self._open_history_for)
            w.privateToggleRequested.connect(
                self._on_card_private_toggled)
            w.positionChanged.connect(self._on_position_changed)
            w.sizeChanged.connect(self._on_size_changed)
            w.foldChanged.connect(self._on_fold_changed)
            w.set_dock_resolver(self._resolve_dock_slot)
            w.set_icon_resolver(self._resolve_icon_pos)
            self._stickies[note_id] = w

        st = self._local.state.get_note_state(note_id)
        if created:
            # 위치·크기·접힘은 클라이언트별 로컬 상태다. 저장된 크기가 있으면
            # 서버 기본 크기보다 우선한다.
            w.apply_local_state(st.x, st.y, st.w, st.h, folded=st.folded,
                                dock=st.dock, dock_y=st.dock_y,
                                fold_style=st.fold_style)
        elif not w.is_folded() and not w.dock_side():
            w.move(st.x, st.y)
            if st.w and st.h:
                w.resize(st.w, st.h)
        holder = self._locks.get(note_id)
        if holder and holder != self._client.nickname():
            w.set_lock_holder(holder)
        else:
            w.set_lock_holder(None)
        if self._note_shown(note_id):
            w.show()
        else:
            w.hide()

    def _on_commit_edit(self, note_id: str, content: str, color: str,
                        w: int, h: int, version: int) -> None:
        self._client.update_note(note_id, content, color, w, h, version)

    def _on_position_changed(self, note_id: str, x: int, y: int) -> None:
        self._local.state.set_note_state(note_id, x=x, y=y)
        self._local.save()

    def _on_size_changed(self, note_id: str, w: int, h: int) -> None:
        self._local.state.set_note_state(note_id, w=w, h=h)
        self._local.save()

    def _on_fold_changed(self, note_id: str, folded: bool, dock: str,
                         dock_y: int, fold_style: str = "") -> None:
        self._local.state.set_note_state(
            note_id, folded=folded, dock=dock, dock_y=dock_y,
            fold_style=fold_style)
        self._local.save()

    def _resolve_icon_pos(self, owner_key: str, pos, screen_rect):
        """아이콘 모양으로 접은 메모와 폴더 아이콘이 서로 겹치지 않는 격자
        자리를 돌려준다."""
        taken = [
            w.pos() for nid, w in self._stickies.items()
            if nid != owner_key and w.isVisible() and w.is_folded()
            and not w.dock_side() and w.fold_style() == FOLD_ICON
        ]
        taken += [
            fw.pos() for fid, fw in self._folder_icons.items()
            if "folder:" + fid != owner_key and fw.isVisible()
        ]
        return snap_icon_pos(pos, screen_rect, taken)

    # ── 바탕 화면 폴더 아이콘 ───────────────────────────────────────────

    def _folder_closed(self, folder_id) -> bool:
        """아이콘으로 접어 둔(닫힌) 폴더인가. 그 폴더의 메모는 바탕 화면에
        나오지 않는다."""
        if not folder_id or folder_id not in self._folders:
            return False
        state = self._local.state.folder_icons.get(folder_id)
        return state is not None and not state.opened

    def _note_shown(self, note_id: str) -> bool:
        """이 메모가 지금 바탕 화면에 나와야 하는가."""
        if self._local.state.get_note_state(note_id).hidden:
            return False
        note = self._notes.get(note_id) or {}
        return not self._folder_closed(note.get("folder_id"))

    def _sync_note_shown(self, note_id: str, animate: bool = False) -> None:
        w = self._stickies.get(note_id)
        if w is None:
            return
        shown = self._note_shown(note_id)
        if animate:
            w.fade_to(shown)
        elif shown != w.isVisible():
            w.setVisible(shown)

    def _folder_note_ids(self, folder_id: str) -> list[str]:
        return [nid for nid, note in self._notes.items()
                if (note.get("folder_id") or None) == folder_id]

    def _folder_note_count(self, folder_id: str) -> int:
        """폴더를 열면 나타날 메모 수(숨긴 메모는 세지 않는다)."""
        return sum(1 for nid in self._folder_note_ids(folder_id)
                   if not self._local.state.get_note_state(nid).hidden)

    def _refresh_folder_icons(self) -> None:
        """폴더 목록과 저장된 상태에 맞춰 폴더 아이콘 창을 만들고 고치고 없앤다."""
        states = self._local.state.folder_icons
        want = [fid for fid in states if fid in self._folders]
        for fid in list(self._folder_icons):
            if fid not in want:
                icon = self._folder_icons.pop(fid)
                icon.close()
                icon.deleteLater()
        for fid in want:
            folder = self._folders[fid]
            state = states[fid]
            icon = self._folder_icons.get(fid)
            if icon is None:
                icon = FolderIconWidget(
                    fid, folder.get("name") or "",
                    folder.get("color") or config.DEFAULT_COLOR,
                    settings=self._settings)
                icon.toggled.connect(self._toggle_folder_icon)
                icon.moved.connect(self._on_folder_icon_moved)
                icon.removeRequested.connect(self._remove_folder_icon)
                icon.set_slot_resolver(self._resolve_icon_pos)
                icon.place(state.x, state.y)
                self._folder_icons[fid] = icon
            else:
                icon.set_folder(folder.get("name") or "",
                                folder.get("color") or config.DEFAULT_COLOR)
            icon.set_state(opened=state.opened,
                           count=self._folder_note_count(fid))
            if not icon.isVisible():
                icon.show()

    def _place_folder_icon(self, folder_id: str) -> None:
        """폴더를 바탕 화면 아이콘으로 접는다. 그 폴더의 메모는 아이콘을 열 때까지
        바탕 화면에서 사라진다(메모 자체는 그대로다)."""
        if folder_id not in self._folders:
            return
        screen = QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen is not None else None
        if avail is None:
            return
        # Windows 바탕화면 아이콘은 보통 왼쪽에 모여 있다. 겹치지 않게 오른쪽
        # 위에서부터 놓는다.
        start = avail.topRight() + QPoint(-ICON_GRID.width() - 4, 10)
        pos = self._resolve_icon_pos("folder:" + folder_id, start, avail)
        self._local.state.folder_icons[folder_id] = FolderIconState(
            x=pos.x(), y=pos.y(), opened=False)
        self._local.save()
        self._refresh_folder_icons()
        for nid in self._folder_note_ids(folder_id):
            self._sync_note_shown(nid, animate=True)
        name = self._folders[folder_id].get("name") or ""
        self._status.showMessage(
            i18n.t("main.status_folder_icon_placed", folder=name), 5000)

    def _remove_folder_icon(self, folder_id: str) -> None:
        if self._local.state.folder_icons.pop(folder_id, None) is None:
            return
        self._local.save()
        self._refresh_folder_icons()
        for nid in self._folder_note_ids(folder_id):
            self._sync_note_shown(nid, animate=True)

    def _toggle_folder_icon(self, folder_id: str) -> None:
        state = self._local.state.folder_icons.get(folder_id)
        if state is None:
            return
        self._set_folder_opened(folder_id, not state.opened)

    def _set_folder_opened(self, folder_id: str, opened: bool,
                           animate: bool = True) -> None:
        state = self._local.state.folder_icons.get(folder_id)
        if state is None or state.opened == opened:
            return
        state.opened = opened
        self._local.save()
        self._refresh_folder_icons()
        for nid in self._folder_note_ids(folder_id):
            if not opened:
                w = self._stickies.get(nid)
                if w is not None:
                    # 편집 중이던 메모는 저장하고 접는다.
                    w.flush_pending_edit()
            self._sync_note_shown(nid, animate=animate)

    def _open_folder_of(self, note_id: str, animate: bool = True) -> None:
        """메모를 보여 달라는 요청이 오면, 그 메모가 든 폴더가 접혀 있을 때 연다."""
        note = self._notes.get(note_id) or {}
        folder_id = note.get("folder_id") or None
        if self._folder_closed(folder_id):
            self._set_folder_opened(folder_id, True, animate=animate)

    def _on_folder_icon_moved(self, folder_id: str, x: int, y: int) -> None:
        state = self._local.state.folder_icons.get(folder_id)
        if state is None:
            return
        state.x, state.y = int(x), int(y)
        self._local.save()

    def _teardown_folder_icons(self) -> None:
        for icon in list(self._folder_icons.values()):
            icon.close()
            icon.deleteLater()
        self._folder_icons.clear()

    def _resolve_dock_slot(self, note_id: str, side: str, y: int,
                           height: int, screen_rect) -> int:
        """같은 화면 같은 가장자리에 이미 붙어 있는 탭을 피해 자리를 잡는다."""
        spans = [
            w.dock_span() for nid, w in self._stickies.items()
            if nid != note_id and w.isVisible() and w.dock_side() == side
            and w.dock_screen_rect() == screen_rect
        ]
        return free_dock_slot(y, height, screen_rect, spans)

    def _set_note_visibility(self, note_id: str, visible: bool) -> None:
        self._local.state.set_note_state(note_id, hidden=not visible)
        self._local.save()
        if visible:
            # 보이라고 한 메모가 접어 둔 폴더 안에 있으면 폴더를 연다.
            self._open_folder_of(note_id)
        w = self._stickies.get(note_id)
        if w is not None:
            if visible:
                w.setWindowOpacity(1.0)
                w.show()
                w.raise_()
            else:
                w.hide()
        card = self._cards.get(note_id)
        if card is not None:
            card.set_visible(visible)
        self._refresh_folder_icons()

    def _open_history_for(self, note_id: str) -> None:
        self._ensure_history_viewer()
        self._history_viewer.set_notes(list(self._notes.values()))
        idx = self._history_viewer._note_combo.findData(note_id)
        if idx >= 0:
            self._history_viewer._note_combo.setCurrentIndex(idx)
        self._history_viewer.show()
        self._history_viewer.raise_()
        self._client.get_history(None)

    def _refresh_status_text(self) -> None:
        """상태줄 오른쪽의 연결 상태, 역할, 닉네임, 접속 인원을 다시 적는다."""
        if self._connected:
            state, text = "connected", i18n.t("main.state_connected")
        elif self._was_connected:
            state, text = "disconnected", i18n.t("main.state_disconnected")
        elif self._host_unreachable():
            state, text = "disconnected", i18n.t("main.state_unreachable")
        else:
            state, text = "connecting", i18n.t("main.state_connecting")
        detail = self._client.nickname()
        if self._connected and self._users:
            detail += "   " + i18n.t("main.status_online_count",
                                     count=len(self._users))
        self._conn_badge.set_state(state, text, detail)
        self._refresh_tray_state(state, text)

    def _build_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        icon = self._app_icon or self.windowIcon()
        self._tray = QSystemTrayIcon(icon, self)
        self._tray.setToolTip(i18n.t("app.name"))
        self._refresh_status_text()
        menu = QMenu()
        menu.aboutToShow.connect(self._rebuild_tray_menu)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray_menu_dirty = True
        self._apply_tray_visibility()

    def _apply_tray_visibility(self) -> None:
        if self._tray is None:
            return
        if self._settings.show_tray_icon:
            self._tray.show()
        else:
            self._tray.hide()

    def _invalidate_tray_menu(self) -> None:
        self._tray_menu_dirty = True

    def _add_tray_item(
        self, menu: QMenu, icon: QIcon | None, text: str,
        handler=None, enabled: bool = True,
    ) -> None:
        add_centered_menu_action(menu, icon, text, handler, enabled=enabled)

    def _rebuild_tray_menu(self) -> None:
        if self._tray is None:
            return
        self._fill_tray_menu(self._tray.contextMenu())
        self._tray_menu_dirty = False

    def _fill_tray_menu(self, menu: QMenu) -> None:
        """트레이 메뉴. 자주 쓰는 것부터, 줄마다 아이콘을 둔다.

        예전에는 메모 제목 15개가 가운데를 차지해 메뉴가 화면 절반만큼 길었다.
        메모는 메인 창에서 찾는다."""
        menu.clear()
        menu.setMinimumWidth(196)
        self._add_tray_item(
            menu, svg_icon("plus"), i18n.t("main.btn_new_note"),
            lambda: self._create_note(self._settings.default_note_color))
        menu.addSeparator()
        self._add_tray_item(menu, svg_icon("window"), i18n.t("main.tray_show_main"),
                            self._show_main_window)
        self._add_tray_item(menu, svg_icon("users"), i18n.t("main.tray_sessions"),
                            self._open_sessions)
        self._add_tray_item(menu, svg_icon("settings"), i18n.t("main.tray_settings"),
                            self._open_settings)
        menu.addSeparator()
        self._add_tray_item(menu, svg_icon("swap"),
                            i18n.t("main.tray_change_connection"),
                            self._reset_connection)
        self._add_tray_item(menu, svg_icon("power"), i18n.t("main.tray_quit"),
                            self._quit_app)

    def _refresh_tray_state(self, state: str, text: str) -> None:
        """트레이 아이콘에 연결 상태를 싣는다: 말풍선에 글자로, 끊겼을 때는
        아이콘 귀퉁이에 붉은 점으로. 메인 창을 닫아 두고 쓰는 동안에도 보인다."""
        if self._tray is None:
            return
        self._tray.setToolTip(
            i18n.t("main.tray_tooltip", app=i18n.t("app.name"), state=text))
        base = self._app_icon or self.windowIcon()
        if base is None or base.isNull():
            return
        if state != "disconnected":
            self._tray.setIcon(base)
            return
        marked = QIcon()
        for size in (16, 20, 24, 32):
            pix = base.pixmap(size, size)
            p = QPainter(pix)
            p.setRenderHint(QPainter.Antialiasing)
            r = size * 0.24
            cx, cy = size - r - 0.5, size - r - 0.5
            p.setPen(QPen(QColor("#FFFFFF"), max(1.0, size / 16)))
            p.setBrush(QColor(theme.COLOR_STATE_OFF))
            p.drawEllipse(QPointF(cx, cy), r, r)
            p.end()
            marked.addPixmap(pix)
        self._tray.setIcon(marked)

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.Trigger:
            self._show_main_window()

    def _show_main_window(self) -> None:
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def _flush_pending_edits(self) -> None:
        """teardown 전에 호출해, 편집 중이던 내용이 재시작 시 사라지지 않게 한다."""
        for sticky in list(self._stickies.values()):
            try:
                sticky.flush_pending_edit()
            except Exception:
                pass

    def _teardown_stickies(self) -> None:
        """일반 close 흐름을 거치면 local_state 에 hidden 으로 표시돼 다음
        실행 시 사라지므로, 종료 시에는 teardown 으로 파괴한다."""
        for w in list(self._stickies.values()):
            w._teardown = True
            w.close()
            w.deleteLater()
        self._stickies.clear()
        self._teardown_folder_icons()

    def _reset_connection(self) -> None:
        """다음 실행 때 접속 대화상자가 다시 뜨도록 last_mode 만 지운다.
        앱을 종료하거나 현재 세션을 끊지는 않는다."""
        self._local.state.last_mode = ""
        self._local.save()
        confirm.notify(self, i18n.t("main.msg_reset_connection_title"),
                       i18n.t("main.msg_reset_connection_body"))

    def _quit_app(self) -> None:
        self._force_quit = True
        self._flush_pending_edits()
        self._teardown_stickies()
        self._toasts.close_all()
        if self._tray is not None:
            self._tray.hide()
        self._client.stop()
        QApplication.quit()

    def closeEvent(self, event) -> None:
        # 트레이가 켜져 있으면 X 로 닫아도 숨기기만 해서 동기화를 유지한다.
        if (not self._force_quit
                and self._tray is not None
                and self._tray.isVisible()
                and self._settings.close_main_to_tray):
            event.ignore()
            self.hide()
            if not self._settings.tray_hint_shown:
                self._settings.tray_hint_shown = True
                self._settings.save()
                self._toasts.show_text(i18n.t("main.tray_running_hint"))
            return
        self._flush_pending_edits()
        self._teardown_stickies()
        self._client.stop()
        if self._tray is not None:
            self._tray.hide()
        super().closeEvent(event)

