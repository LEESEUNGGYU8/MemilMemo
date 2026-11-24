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

from .. import config, html_guard, i18n
from ..ipc import (
    CMD_LINK_NOTE, CMD_NEW_FOLDER_HERE, CMD_NEW_NOTE, CMD_NEW_NOTE_HERE,
    CMD_SHOW, split_command,
)
from ..datetime_fmt import fmt_relative
from ..local_state import FolderIconState, LocalStateStore, NoteLocalState
from ..network.client_base import ClientBase
from ..models import new_id
from ..offline_drafts import KIND_EDIT, KIND_NEW, Draft, DraftStore
from ..backup import list_backups
from ..replica import (
    ReplicaStore, backup_source, copy_to_show, list_copies, read_copy,
)
from ..settings import (
    VIEW_COMPACT,
    VIEW_DETAILED,
    VIEW_GRID,
    VIEW_LIST,
    VIEW_STANDARD,
    AppSettings,
)
from . import confirm, drafts_dialog, password_dialog, theme
from ..network.discovery import Searcher
from .copies_dialog import CopiesDialog
from .folder_dialog import FolderEditDialog
from .folder_icon import FolderIconWidget
from .folder_window import FolderWindow
from .history_viewer import HistoryViewer
from .icons import color_swatch_icon, folder_lock_icon, svg_icon
from .menu_item import add_centered_menu_action
from .note_card import GRID_MIN_COLUMN, CardFlowLayout, NoteCard
from . import note_fold as nf
from .icon_grid import DesktopGrid
from .icon_select import IconController, folder_key, note_key
from .note_fold import FOLD_ICON, free_dock_slot, snap_icon_pos
from .rich_text import has_non_checkbox_image, html_to_plain
from .sessions_dialog import SessionsDialog
from .settings_dialog import SettingsDialog
from .sticky_note import StickyNoteWidget
from .theme import RoundedScrollBar
from . import toast
from .toast import ToastManager
from .trash_dialog import TrashDialog


# 실제 폴더 id 와 같은 dict 키 자리에 분기 없이 섞여 저장되도록 문자열 sentinel 로 둔다.
FOLDER_ALL = "__all__"
FOLDER_NONE = "__none__"

# 목록에 무엇을 보이는가. 메모 수 단추를 누를 때마다 이 차례로 넘어간다.
LIST_ALL, LIST_SHOWN, LIST_HIDDEN = "all", "shown", "hidden"
LIST_MODES = (LIST_ALL, LIST_SHOWN, LIST_HIDDEN)

# 파일을 여럿 골라 우클릭 메뉴를 누르면 경로가 하나씩 따로 도착한다. 이만큼
# 기다려 모으고, 메모를 만든 뒤에도 얼마 동안은 뒤늦게 온 경로를 같은 메모에 잇는다.
LINK_NOTE_BATCH_MS = 300
LINK_NOTE_JOIN_SECONDS = 4.0

# 새 메모가 놓이는 자리: 화면의 쓸 수 있는 영역 왼쪽 위에서 이만큼 들어온 곳.
NEW_NOTE_ORIGIN = QPoint(120, 120)
# 그 자리에 이미 메모가 있으면 이만큼씩 비켜 놓는다(손잡이 줄이 서로 보일 만큼).
NEW_NOTE_STEP = 28
# 우클릭한 자리에 띄울 때는 마우스가 메모의 손잡이 줄 위에 오도록 조금 당긴다.
NEW_NOTE_CURSOR_INSET = QPoint(24, 16)
# 밖에서 온 '새 메모' 요청의 자리를 기억해 두는 시간(초). 그 안에 메모가
# 만들어지지 않았으면 잊는다. 뒤늦게 만든 다른 메모가 그 자리로 가면 안 된다.
NEW_NOTE_SPOT_TTL = 10.0
# 공유 PC 에 올라가지 않은 메모에 붙이는 표시(메모 dict 의 키). 값은 KIND_NEW
# (끊긴 동안 만든 메모) 또는 KIND_EDIT(사본의 메모를 끊긴 동안 고친 것)다.
LOCAL_MARK = "offline"
# 다시 붙은 뒤 끊긴 동안 쓴 메모를 어떻게 할지 묻기까지의 틈(ms). 화면이 공유
# PC 의 내용으로 바뀐 뒤에 묻는다.
DRAFTS_ASK_MS = 600
# 전체 화면 중이라 묻지 못했을 때 다시 살펴보기까지의 틈(ms).
DRAFTS_BUSY_RETRY_MS = 20000
# 공유 PC 에 잇달아 이만큼 붙지 못하면 내부망에서 다시 찾아본다. 그 뒤로는 이 간격으로.
FIND_AFTER_FAILS = 3
FIND_EVERY_SECONDS = 30
# 고친 메모를 새 메모로 올릴 때 원래 메모에서 비켜 놓는 거리(px).
DRAFT_OFFSET = 28
# 사라진 메모의 자리와 접힘을 들고 있는 시간(초). 같은 메모가 이 안에 다시 오면 되살린다.
GONE_STATE_TTL = 5.0


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
        self._state.setTextFormat(Qt.PlainText)
        self._detail.setTextFormat(Qt.PlainText)
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
    restoreRequested = Signal(str)      # 복원할 백업 파일의 경로(공유 PC)
    shareRequested = Signal(str)        # 혼자 쓰던 메모의 공유 시작(걸 비밀번호)
    takeoverRequested = Signal(str)     # 공유를 이어받을 사본이나 백업 파일의 경로

    # 지운 뒤 '되돌리기'가 보이는 시간.
    _UNDO_MS = 10_000
    # 새 메모 알림을 모으는 시간. 잇달아 만든 메모는 한 번에 알린다.
    _ANNOUNCE_MS = 900
    # 같은 사람이 같은 메모를 잇달아 고칠 때, 마지막으로 고친 뒤 이만큼 조용해야
    # 다시 알린다(초). 할 일 상자를 하나씩 누르는 것도 저마다 편집 한 번이라,
    # 없으면 누를 때마다 뜬다.
    _EDIT_NOTICE_COOLDOWN = 30.0
    _NOTICE_TITLE_MAX = 22

    def __init__(self, client: ClientBase, local_state: LocalStateStore,
                 role: str, settings: AppSettings | None = None,
                 app_icon: QIcon | None = None, parent=None,
                 replica: ReplicaStore | None = None,
                 drafts: DraftStore | None = None, *, solo: bool = False):
        """``solo`` 는 공유하지 않고 혼자 쓰는 중인가. 그때 ``role`` 은 "host" 다
        (메모가 이 PC 에 있다). 포트를 열지 않았으므로 참가자가 없다."""
        super().__init__(parent)
        self._solo = bool(solo)
        self.setWindowTitle(i18n.t("app.name"))
        self.resize(380, 600)
        if app_icon is not None and not app_icon.isNull():
            self.setWindowIcon(app_icon)

        self._client = client
        self._local = local_state
        self._role = role
        self._settings = settings or AppSettings()
        nf.set_animation_speed(self._settings.animation_speed)
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
        # 목록에 무엇을 보이는가: 전체, 바탕화면에 표시한 메모, 숨긴 메모.
        self._list_mode = LIST_ALL
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
        # 파일 우클릭 메뉴로 만드는 중인 메모에 넣을 경로 묶음(만들어지는 차례대로).
        self._link_note_queue: list[list[str]] = []
        # 한꺼번에 고른 파일들의 경로를 모으는 중인 묶음과 그때의 마우스 자리.
        self._link_batch: list[str] = []
        self._link_batch_at: QPoint | None = None
        self._link_batch_timer = QTimer(self)
        self._link_batch_timer.setSingleShot(True)
        self._link_batch_timer.setInterval(LINK_NOTE_BATCH_MS)
        self._link_batch_timer.timeout.connect(self._flush_link_batch)
        # 방금 만든 링크 메모와 만든 때. 뒤늦게 온 경로를 같은 메모에 잇는다.
        self._link_note_recent: tuple[str, float] | None = None
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
        # 끊긴 동안 이 PC 에서 쓴 메모. 다시 붙으면 올릴지 묻는다. 참여하던 PC 가
        # 사본으로 공유를 이어받으면 공유 PC 가 된 뒤에 묻는다.
        self._drafts = drafts
        # 끊긴 동안 편집을 시작한 사본의 메모(공유 PC 의 잠금 없이 이 PC 에서만 고친다).
        self._offline_edit_ids: set[str] = set()
        # 내가 지우는 중인 끊긴 동안의 메모(쓰던 글을 새 메모로 남기지 않는다).
        self._discarding: set[str] = set()
        # 방금 사라진 메모의 자리와 접힘(메모 id → (상태, 사라진 때)).
        self._gone_states: dict[str, tuple[Any, float]] = {}
        # 올리는 중인 것: 공유 PC 의 메모 id → 끊긴 동안 쓴 글의 키.
        self._draft_uploads: dict[str, str] = {}
        self._drafts_in_flight: set[str] = set()
        # 마지막으로 붙은 뒤에 '보내지 못한 글'을 적어 둔 메모. 그 전부터 적혀 있던
        # 글(다시 붙었을 때 보류한 것)과 가린다.
        self._kept_since_welcome: set[str] = set()
        # 이번 실행에서 '나중에'를 골랐는가. 그러면 다시 붙어도 묻지 않고 알림 줄만 둔다.
        self._drafts_postponed = False
        self._drafts_asking = False
        # 목록 위 알림 줄이 지금 무엇을 알리는가("copy", "drafts", "gate", 없으면 빈 글).
        self._notice_mode = ""
        # 공유 PC 에 닿았는데 들어가지 못하고 사용자를 기다리는 까닭.
        # "password": 비밀번호를 넣어야 한다, "open": 비밀번호를 걸어 두었던 공유
        # PC 가 비밀번호 없이 답했다, "unverified": 비밀번호를 모르는 상대다.
        self._gate = ""
        self._gate_info: dict = {}
        self._gate_asking = False
        # 창에 매단 타이머로 묻는다. 창이 닫힌 뒤에 묻는 창이 뜨지 않는다.
        self._gate_timer = QTimer(self)
        self._gate_timer.setSingleShot(True)
        self._gate_timer.timeout.connect(self._open_gate_when_free)
        # 붙던 공유 PC 의 메모 묶음 식별값. 주소가 바뀐 그 PC 를 알아보는 데 쓴다.
        self._host_id = ""
        # 지금 붙은 공유에 비밀번호가 걸려 있는가(참가자 창에 보인다).
        self._locked = False
        self._fail_streak = 0
        self._finder: Searcher | None = None
        self._last_search = 0.0
        # 이번 실행에서 붙지 않기로 한(또는 붙어 봤는데 아니던) 주소.
        self._passed_hosts: set[tuple[str, int]] = set()
        # 알릴 새 메모(만든 사람 → 개수). 잠깐 모았다가 한 번에 알린다.
        self._announce: dict[str, int] = {}
        # 알릴 공개 전환(바꾼 사람 → 개수).
        self._announce_shared: dict[str, int] = {}
        # 고친 사람 → 고친 메모들(메모 id → 제목). 알린 때는 따로 적어 둔다.
        self._announce_edited: dict[str, dict[str, str]] = {}
        self._edit_notified: dict[tuple[str, str], float] = {}
        # 지금 편집 중인 사람의 이번 편집을 이미 다뤘는가(메모 id → 그 사람).
        self._edit_handled: dict[str, str] = {}
        # 화면 오른쪽 아래에 잠깐 뜨는 알림. 누르면 메인 창을 연다.
        self._toasts = ToastManager(self, app_icon)
        self._toasts.clicked.connect(self._show_main_window)
        # 지워 달라고 보냈고 아직 답을 못 받은 메모(메모 id → 되돌릴 때 쓸 내용).
        self._pending_deletes: dict[str, dict] = {}
        self._delete_message = ""
        self._delete_round = 0
        # 공유 PC 가 지운 메모를 휴지통에 두는 날 수. 0 이면 휴지통이 없는 옛
        # 버전의 공유 PC 다(지우면 되살릴 수 없으므로 지우기 전에 묻는다).
        self._trash_days = 0
        self._trash_dialog: TrashDialog | None = None
        # 휴지통에서 되살아나 곧 도착할 메모(메모 id → 되살린 사람).
        self._restoring: dict[str, str] = {}
        self._announce_timer = QTimer(self)
        self._announce_timer.setSingleShot(True)
        self._announce_timer.setInterval(self._ANNOUNCE_MS)
        self._announce_timer.timeout.connect(self._flush_announcements)

        # 접힌 메모와 메모 폴더의 아이콘은 진짜 바탕화면 아이콘과 같은 칸에 같은
        # 크기로 놓인다. 사용자가 바탕화면 아이콘의 크기를 바꾸면 따라 바뀐다.
        self._desktop_grid = DesktopGrid(self)
        nf.set_icon_grid_source(self._desktop_grid.grid_for)
        # 아이콘들을 마지막으로 맞춰 놓은 격자(작업 영역 → 격자).
        self._grids_used: dict[tuple, nf.IconGrid] = {}
        # 우클릭 메뉴의 '새 메모 폴더 추가'로 만든 폴더(id → 놓을 자리).
        # 공유 PC 가 만들었다고 답하면 그 자리에 아이콘을 두고 이름을 받는다.
        self._new_folder_spots: dict[str, tuple[float, QPoint | None]] = {}
        # 메모와 폴더를 한 번이라도 불러왔는가. 그 전에는 맞출 아이콘이 없다.
        self._icons_loaded = False
        self._desktop_grid.changed.connect(self._align_icons)
        # 아이콘을 바탕화면 아이콘처럼 고르고 함께 다루는 일(ui/icon_select.py).
        # 바탕화면 빈 곳을 끌어 고르는 것도 여기서 따라간다.
        self._icon_ctl = IconController(self, self._desktop_grid, self)
        self._desktop_grid.refreshed.connect(self._icon_ctl.marquee.sync)
        # 폴더 아이콘을 열면 뜨는 창(폴더 id → 창).
        self._folder_windows: dict[str, FolderWindow] = {}
        # 폴더 창에서 꺼내 본 메모. 폴더가 접혀 있어도 이 메모만 바탕화면에
        # 나와 있다. 닫으면 다시 폴더 안으로 들어간다(이번 실행 동안만).
        self._peeked: set[str] = set()
        self._desktop_grid.start()

        self._build_ui()
        self._wire_client()
        self._build_tray()
        self._show_replica()
        self._show_drafts()

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
        self._sessions_btn.setVisible(not self._solo)       # 혼자 쓰면 참가자가 없다
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

        # 메모 수 단추. 누를 때마다 전체 → 표시 → 숨김 차례로 목록을 바꾼다.
        # 숨긴 메모는 목록에서 첫 줄 제목으로만 찾아야 해서 놓치기 쉽다.
        self._count_btn = QToolButton()
        self._count_btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self._count_btn.setCursor(Qt.PointingHandCursor)
        self._count_btn.setFocusPolicy(Qt.NoFocus)
        self._count_btn.setStyleSheet(
            "QToolButton { background: rgba(17, 24, 39, 0.07); border: none; "
            "  border-radius: 9px; padding: 3px 10px; color: #111827; "
            "  font-size: 11px; font-weight: 700; }"
            "QToolButton:hover { background: rgba(17, 24, 39, 0.12); }"
            "QToolButton:pressed { background: rgba(17, 24, 39, 0.18); }"
        )
        self._count_btn.clicked.connect(self._cycle_list_mode)
        hdr.addWidget(self._count_btn, 0)
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
        # 목록 위의 한 줄 알림. 이 PC 의 사본을 보여 주는 중이거나, 끊긴 동안 쓴
        # 메모를 아직 올리지 않았을 때 나온다. 상자나 바탕색 없이 글로만 두고,
        # 상태줄의 연결 표시와 같은 색 점을 앞에 둔다. 오른쪽 글자를 누르면 사본
        # 창(또는 올릴지 묻는 창)이 열린다.
        self._replica_banner = QWidget()
        self._replica_banner.setObjectName("ListNotice")
        banner_row = QHBoxLayout(self._replica_banner)
        banner_row.setContentsMargins(4, 2, 2, 8)
        banner_row.setSpacing(7)
        self._notice_dot = QFrame()
        self._notice_dot.setFixedSize(6, 6)
        banner_row.addWidget(self._notice_dot, 0, Qt.AlignVCenter)
        self._replica_text = QLabel("")
        self._replica_text.setTextFormat(Qt.PlainText)
        self._replica_text.setStyleSheet(
            "color: #374151; font-size: 12px; font-weight: 600; "
            "background: transparent;")
        banner_row.addWidget(self._replica_text, 1, Qt.AlignVCenter)
        self._copies_btn = QPushButton(i18n.t("main.replica_banner_btn"))
        self._copies_btn.setCursor(Qt.PointingHandCursor)
        self._copies_btn.setFlat(True)
        self._copies_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; "
            f"padding: 0 2px; color: {theme.COLOR_FOCUS}; font-size: 12px; "
            "font-weight: 700; min-width: 0; min-height: 0; }"
            "QPushButton:hover { color: #1D4ED8; text-decoration: underline; }"
            "QPushButton:focus { text-decoration: underline; }")
        self._copies_btn.clicked.connect(self._on_notice_action)
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
        c.noteEdited.connect(self._on_note_edited)
        c.historyList.connect(self._on_history_list)
        c.noteRestored.connect(self._on_note_restored)
        c.trashList.connect(self._on_trash_list)
        c.trashChanged.connect(self._on_trash_changed)
        c.connectionChanged.connect(self._on_connection_changed)
        c.errorOccurred.connect(self._on_error)
        c.passwordRequired.connect(self._on_password_required)
        c.passwordAccepted.connect(self._on_password_accepted)
        c.hostOpen.connect(lambda: self._hold_at_gate("open"))
        c.hostUnverified.connect(lambda: self._hold_at_gate("unverified"))
        c.followed.connect(self._on_followed)

    def _create_note(self, color: str, *, private: bool | None = None) -> None:
        """새 메모를 만든다. ``private`` 를 주지 않으면 설정의 기본 공개 범위를 따른다."""
        # 전체 / 미분류 뷰에서는 폴더 없음으로, 그 외엔 현재 폴더에 넣는다.
        folder_id = (self._current_folder
                     if self._current_folder not in
                     (FOLDER_ALL, FOLDER_NONE)
                     else None)
        if not self._connected:
            if self._can_work_offline():
                # 끊긴 동안에는 이 PC 에만 만든다. 다시 붙으면 올릴지 묻는다.
                self._create_offline_note(color, folder_id)
                return
            # 끊긴 동안 보낸 요청은 그대로 사라진다. 아무 일도 안 일어난 것처럼
            # 두지 않고 까닭을 알린다.
            self._status.showMessage(
                i18n.t("main.status_offline_no_create"), 5000)
            return
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
        cmd, arg = split_command(command)
        if cmd == CMD_NEW_NOTE:
            self.handle_new_note_request()
        elif cmd == CMD_NEW_NOTE_HERE:
            # 바탕 화면 우클릭 메뉴에서 왔다. 마우스가 있는 자리에 띄운다.
            self.handle_new_note_request(QCursor.pos())
        elif cmd == CMD_NEW_FOLDER_HERE:
            self.handle_new_folder_request(QCursor.pos())
        elif cmd == CMD_LINK_NOTE:
            if arg:
                self._queue_link_path(arg)
        elif cmd == CMD_SHOW:
            self._show_main_window()

    def _queue_link_path(self, path: str) -> None:
        """파일·폴더 우클릭 메뉴에서 온 경로. 여럿을 골랐으면 경로마다 따로
        도착하므로 잠깐 모아 메모 하나에 담는다."""
        now = time.monotonic()
        recent = self._link_note_recent
        if recent is not None and now - recent[1] <= LINK_NOTE_JOIN_SECONDS:
            # 방금 만든 링크 메모에 뒤늦게 도착한 경로다. 같은 메모에 잇는다.
            w = self._stickies.get(recent[0])
            if (w is not None and self._connected
                    and not w.is_locked_by_other()):
                self._link_note_recent = (recent[0], now)
                w.add_file_links([path])
                return
        if not self._link_batch:
            self._link_batch_at = QCursor.pos()
        if path not in self._link_batch:
            self._link_batch.append(path)
        self._link_batch_timer.start()

    def _flush_link_batch(self) -> None:
        paths, self._link_batch = self._link_batch, []
        if paths:
            self.handle_link_note_request(paths, self._link_batch_at)

    def handle_link_note_request(self, paths: list[str],
                                 at: QPoint | None = None) -> None:
        """파일·폴더를 가리키는 링크가 든 새 메모를 만든다."""
        paths = [p for p in paths if p]
        if not paths:
            return
        if not self._connected and not self._can_work_offline():
            self._show_main_window()
            return
        self._link_note_queue.append(paths)
        self._new_note_spots.append((time.monotonic(), at))
        self._create_note(self._settings.default_note_color)

    def handle_new_note_request(self, at: QPoint | None = None) -> None:
        """앱 밖(작업 표시줄 목록, 바탕 화면 우클릭 메뉴)에서 온 '새 메모'.

        메인 창은 건드리지 않는다. 만들어진 메모만 다른 창들 위로 올라온다
        (_on_note_created). ``at`` 을 주면 그 자리에 띄운다."""
        if not self._connected and not self._can_work_offline():
            # 만들 수 없다. 아무 일도 안 일어난 것처럼 보이지 않도록, 까닭(연결
            # 상태)이 보이는 메인 창을 띄운다.
            self._show_main_window()
            return
        self._new_note_spots.append((time.monotonic(), at))
        self._create_note(self._settings.default_note_color)

    def handle_new_folder_request(self, at: QPoint | None = None) -> None:
        """바탕 화면 우클릭 메뉴의 '새 메모 폴더 추가'. 폴더를 만들어 그 자리에
        아이콘으로 두고, 바탕화면에서 새 폴더를 만들 때처럼 곧바로 이름을 받는다."""
        if not self._connected:
            # 폴더는 공유 PC 에 만든다. 끊겨 있으면 까닭이 보이는 메인 창을 띄운다.
            self._show_main_window()
            self._require_online()
            return
        folder_id = new_id()
        self._new_folder_spots[folder_id] = (time.monotonic(), at)
        self._client.create_folder(
            self._free_folder_name(i18n.t("main.folder_new_default_name")),
            config.DEFAULT_COLOR, False, folder_id=folder_id)

    def _free_folder_name(self, base: str) -> str:
        """이미 있는 폴더와 겹치지 않는 이름: 새 폴더, 새 폴더 (2), ..."""
        names = {str(f.get("name") or "") for f in self._folders.values()}
        if base not in names:
            return base
        number = 2
        while f"{base} ({number})" in names:
            number += 1
        return f"{base} ({number})"

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
            self._history_viewer.reviveRequested.connect(
                self._on_revive_requested)
        return self._history_viewer

    def _sync_history_notes(self) -> None:
        """이력 창이 떠 있으면 메모 목록을 지금 것으로 맞춘다(방금 지워진 메모가
        삭제된 메모 쪽으로 넘어간다)."""
        if self._history_viewer is not None and self._history_viewer.isVisible():
            self._history_viewer.set_notes(list(self._notes.values()))

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self._settings, role=self._role, parent=self,
                             solo=self._solo)
        dlg.backupNowRequested.connect(self.backupRequested.emit)
        dlg.resetConnectionRequested.connect(self._reset_connection)
        dlg.shareRequested.connect(self._start_sharing)
        # 아직 저장하지 않은 백업 폴더라도 거기서 찾는다(다른 PC 에서 가져온
        # 백업 폴더를 가리켜 되살리는 경우).
        dlg.restoreRequested.connect(lambda: self.open_restore(dlg.backup_folder()))
        dlg.exec()
        self._apply_tray_visibility()
        nf.set_animation_speed(self._settings.animation_speed)
        # '붙인 메모를 다른 창 위에'와 접힌 모양은 떠 있는 메모에 바로 반영한다.
        for w in self._stickies.values():
            w.refresh_dock_layer()
            w.refresh_fold_face()

    def open_copies(self) -> None:
        """끊겨 있다는 안내를 눌렀다. 받아 둔 사본을 열어 본다."""
        self.open_restore()

    def open_restore(self, backup_folder=None) -> None:
        """백업과 이 PC 에 받아 둔 사본을 열어 본다. 빠진 메모를 가져오거나, 메모
        전체를 그 내용으로 되돌린다(공유 PC 는 복원, 참여한 PC 는 그것으로 공유를
        시작한다). 아무것도 없어도 창은 뜬다(파일을 골라 넣을 수 있다)."""
        parent = QApplication.activeModalWidget() or self
        host = self._role != "guest"
        dlg = CopiesDialog(
            self._restore_sources(backup_folder),
            host_notes=dict(self._notes) if self._connected else None,
            can_takeover=not host, can_restore=host,
            nickname=self._client.nickname(), parent=parent)
        dlg.importRequested.connect(self._import_from_copy)
        dlg.takeoverRequested.connect(self.takeoverRequested.emit)
        dlg.restoreRequested.connect(self.restoreRequested.emit)
        dlg.exec()

    def _restore_sources(self, backup_folder=None) -> list:
        """복원 창에 보여 줄 것들. 공유 PC 에서는 백업이 먼저, 참여한 PC 에서는
        받아 둔 사본이 먼저다."""
        folder = backup_folder or self._settings.resolved_backup_dir()
        backups = [backup_source(path) for path in list_backups(folder)]
        copies = self._copy_sources()
        return copies + backups if self._role == "guest" else backups + copies

    def _copy_sources(self) -> list:
        """사본 창에 보여 줄 것들. 참여 중인 PC 에서는 이 PC 가 공유 PC 였을 때의
        메모 파일도 넣는다(공유 PC 로 쓰는 동안에는 그 파일이 곧 지금 메모다)."""
        return list_copies(
            config.default_replica_path(),
            host_db=config.default_db_path() if self._role == "guest" else None,
            nickname=self._client.nickname())

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
            locked=self._locked and self._connected,
        )

    def _on_connection_changed(self, connected: bool) -> None:
        self._connected = connected
        if connected:
            self._gate, self._gate_info = "", {}
            self._fail_streak = 0
        else:
            self._fail_streak += 1
        if connected:
            self._was_connected = True
            self._connect_failures = 0
            # '연결 끊김' 안내가 아직 떠 있으면 걷는다.
            self._status.clearMessage()
        elif self._was_connected:
            self._status.showMessage(i18n.t("main.status_disconnected"), 6000)
        else:
            self._connect_failures += 1
        # 끊긴 동안에는 메모에 '오프라인'을 적는다.
        for w in self._stickies.values():
            w.set_offline(not connected)
        if not connected:
            self._enter_offline()
            self._find_host_again()
        self._refresh_status_text()
        self._refresh_count()
        self._refresh_sessions_dialog()
        self._refresh_replica_banner()

    # ── 비밀번호가 걸린 공유 ──

    def _on_password_required(self, info: dict) -> None:
        self._gate_info = dict(info or {})
        self._hold_at_gate("password")

    def _on_password_accepted(self, auth: dict) -> None:
        # 다음부터 묻지 않게 열쇠를 적어 둔다(비밀번호 글자는 적지 않는다).
        self._local.state.share_auth = dict(auth)
        self._local.save()

    def _hold_at_gate(self, gate: str) -> None:
        self._gate = gate
        self._refresh_status_text()
        self._refresh_replica_banner()
        self._gate_timer.start(0)

    def _open_gate_when_free(self) -> None:
        """스스로 묻는 경우. 전체 화면으로 발표하는 중이면 창을 띄우지 않고
        기다린다(목록 위의 알림은 그대로 있다)."""
        if (not self._gate or self._gate_asking or self._connected
                or self._force_quit):
            return
        if toast.user_is_busy():
            self._gate_timer.start(DRAFTS_BUSY_RETRY_MS)
            return
        self._open_gate()

    def _open_gate(self) -> None:
        """들어가지 못하고 기다리는 까닭을 사용자와 푼다."""
        if self._gate_asking or not self._gate:
            return
        self._gate_asking = True
        try:
            if self._gate == "password":
                info, self._gate_info = self._gate_info, {}
                text = self._ask_password(bool(info.get("wrong")),
                                          int(info.get("wait") or 0))
                if text:
                    self._client.submit_password(text)
                else:
                    self._client.pause()        # 알림 줄에서 다시 넣을 수 있다
            elif self._gate == "open":
                if confirm.ask(self, i18n.t("auth.window_title"),
                               i18n.t("auth.open_body"),
                               action=i18n.t("auth.btn_connect")):
                    self._local.state.share_auth = None
                    self._local.save()
                    self._client.set_auth(None)
                    self._gate = ""
                    self._client.resume()
            else:
                confirm.notify(self, i18n.t("auth.window_title"),
                               i18n.t("auth.unverified_body"),
                               icon=QMessageBox.Warning)
        finally:
            self._gate_asking = False
        self._refresh_status_text()
        self._refresh_replica_banner()

    def _ask_password(self, wrong: bool, wait: int) -> str | None:
        return password_dialog.ask(self, wrong=wrong, wait=wait)

    # ── 공유 PC 다시 찾기 ──

    def _find_host_again(self) -> None:
        """붙던 공유 PC 에 거듭 붙지 못하면 내부망에서 찾아본다. 주소가 바뀌었거나
        다른 PC 가 공유를 이어받았을 수 있다."""
        if (self._role != "guest" or self._connected or self._gate
                or not self._host_id or self._fail_streak < FIND_AFTER_FAILS):
            return
        now = time.monotonic()
        if self._last_search and now - self._last_search < FIND_EVERY_SECONDS:
            return
        self._last_search = now
        if self._finder is None:
            self._finder = Searcher(parent=self)
            self._finder.finished.connect(self._on_hosts_found)
        self._finder.search()

    def _on_hosts_found(self, found: list) -> None:
        if self._connected or self._gate or self._force_quit:
            return
        current = self._client.address()
        known = bool(self._local.state.share_auth)
        for item in found:
            where = (item["address"], item["port"])
            if where == current or where in self._passed_hosts:
                continue
            if item["host_id"] == self._host_id:
                if item["locked"] and known:
                    # 비밀번호로 서로를 확인할 수 있다. 붙어 봐서 맞으면 옮기고,
                    # 아니면 묻지 않고 돌아온다. 같은 곳을 거듭 찔러 보지는 않는다.
                    self._passed_hosts.add(where)
                    self._client.follow(*where)
                    return
                body = i18n.t("discover.moved_body")
            elif item["took_over_from"] == self._host_id:
                body = i18n.t("discover.took_over_body", host=item["host"])
            else:
                continue                    # 내가 붙던 공유와 상관없는 PC 다
            # 확인할 방법이 없다(답은 누구나 꾸밀 수 있다). 사용자가 정한다.
            if toast.user_is_busy():
                return                      # 다음에 찾았을 때 묻는다
            self._passed_hosts.add(where)
            if confirm.ask(self, i18n.t("discover.title"), body,
                           detail=item["address"],
                           action=i18n.t("auth.btn_connect")):
                self._on_followed(*where, quiet=True)
                self._client.set_address(*where)
            return

    def _on_followed(self, host: str, port: int, quiet: bool = False) -> None:
        """붙을 공유 PC 의 주소가 바뀌었다. 다음에 켤 때도 그 주소로 붙는다."""
        self._local.state.last_host_ip = host
        self._local.state.last_host_port = int(port)
        self._local.save()
        if not quiet:
            self._show_notification(i18n.t("discover.followed"))

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

    def _move_blocked_by(self, note_id: str, folder_id) -> str:
        """이 폴더로 옮기면 보는 사람이 달라지는데 다른 사람이 편집 중이면 그
        사람의 닉네임. 옮겨도 되면 빈 문자열."""
        holder = self._locked_by_other(note_id)
        note = self._notes.get(note_id)
        if not holder or note is None or note.get("private_owner"):
            return ""

        def owner(fid):
            return (self._folders.get(fid or "") or {}).get("private_owner") or None

        return holder if owner(note.get("folder_id")) != owner(folder_id) else ""

    def _tell_locked(self, holder: str) -> None:
        confirm.notify(
            self, i18n.t("main.msg_delete_locked_title"),
            i18n.t("main.msg_delete_locked_body",
                   holder=holder or i18n.t("main.other_user")),
            icon=QMessageBox.Warning)

    def _on_welcomed(self, data: dict) -> None:
        self._users = list(data.get("users", []))
        self._host_nickname = data.get("host", "") or self._host_nickname
        self._host_id = str(data.get("host_id") or "") or self._host_id
        self._locked = data.get("locked") is True
        days = data.get("trash_days")
        self._trash_days = days if isinstance(days, int) and days > 0 else 0
        self._heartbeat_ms = int(data.get("heartbeat_ms", 0)) or self._heartbeat_ms
        # 호스트가 보낸 것으로 바꾼다. 사본을 보여 주던 중이었으면 여기서 끝난다.
        self._replica_shown = False
        self._refresh_replica_banner()
        # 끊긴 동안 이 PC 에서만 고치던 글은 지금 것까지 적어 둔다(아래에서 묻는다).
        self._flush_offline_edits()
        self._kept_since_welcome.clear()
        # 끊긴 동안 쓰던 글은 아래에서 메모 창을 새로 만들면 사라진다. 먼저 챙긴다.
        unsent = {nid: text for nid, w in self._stickies.items()
                  if (text := w.unsent_edit())}
        seen_versions = {nid: self._stickies[nid].unsent_base_version()
                         for nid in unsent}
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
        self._settle_drafts(set(unsent))

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
        self._edit_handled.clear()
        sorted_notes = sorted(
            notes,
            key=lambda n: n.get("updated_at", 0),
            reverse=True,
        )
        self._hide_notes_new_to_this_pc(sorted_notes, received=not offline)
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
        # 아이콘으로 접힌 메모와 폴더 아이콘을 바탕화면 아이콘의 칸에 맞춘다
        # (앱이 꺼져 있는 동안 바탕화면 아이콘의 크기가 바뀌었을 수 있다).
        self._icons_loaded = True
        self._align_icons()

    def _hide_notes_new_to_this_pc(self, notes: list, *,
                                   received: bool) -> None:
        """이 PC 가 처음 받는 남의 메모는 숨김으로 둔다.

        붙어 있을 때 남이 만든 메모는 숨김으로 들어온다(_on_note_created). 꺼져
        있거나 끊긴 동안 만들어진 메모도 같게 다룬다. 그러지 않으면 다시 붙었을
        때 그 메모들이 바탕화면의 같은 자리에 한꺼번에 뜬다.

        ``received`` 는 공유 PC 가 방금 보낸 것인가(사본을 보여 줄 때는 거짓)."""
        state = self._local.state
        if not state.hides_new:
            # 이 규칙이 없던 버전에서 올라온 PC 다. 그 버전은 받은 메모를 모두
            # 띄웠고 자리 기록을 뒤늦게 적었으므로, 기록이 없는 메모도 바탕화면에
            # 떠 있었을 수 있다. 지금 받은 것은 모두 본 것으로 적고 다음부터 가린다.
            if received:
                for note in notes:
                    state.get_note_state(note["id"])
                state.hides_new = True
                self._local.save()
            return
        me = self._client.nickname()
        known = state.notes
        fresh = [n["id"] for n in notes
                 if n["id"] not in known and n.get("created_by") != me]
        for note_id in fresh:
            state.get_note_state(note_id).hidden = True
        if fresh:
            self._local.save()

    def _show_replica(self) -> None:
        """호스트에 붙기 전에 이 PC 의 사본을 먼저 보여 준다(읽기 전용).

        호스트가 꺼져 있어도 메모를 볼 수 있다. 붙으면 호스트가 보낸 것으로
        바뀐다. 다른 닉네임으로 받은 사본은 보여 주지 않는다(그 사람의 '나만
        보기' 메모가 들어 있다). 공유 PC 를 바꾼 직후에는 지금 사본이 다른 공유
        PC 의 것이므로, 붙으려는 주소에서 받아 둔 사본을 찾아 보여 준다."""
        if self._replica is None or self._role != "guest":
            return
        ls = self._local.state
        try:
            path = copy_to_show(self._replica.path,
                                f"{ls.last_host_ip}:{ls.last_host_port}",
                                self._client.nickname())
            if path is None:
                return
            if path == self._replica.path:
                meta = self._replica.meta()
                notes, folders = self._replica.load()
            else:
                notes, folders, meta = read_copy(path)
        except Exception:
            return
        self._host_id = str(meta.get("host_id") or "")
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
        """목록 위 알림 줄을 지금 상황에 맞춘다."""
        showing_copy = self._replica_shown and not self._connected
        waiting = len(self._pending_drafts()) if self._connected else 0
        if self._gate and not self._connected:
            # 공유 PC 에 닿았는데 들어가지 못했다. 할 일이 있는 쪽을 먼저 알린다.
            self._notice_mode = "gate"
            if self._gate == "password":
                text = i18n.t("auth.notice_password")
                button = i18n.t("auth.notice_password_btn")
            elif self._gate == "open":
                text = i18n.t("auth.notice_open")
                button = i18n.t("auth.btn_connect")
            else:
                text = i18n.t("auth.notice_unverified")
                button = i18n.t("auth.notice_retry")
            self._replica_text.setText(text)
            self._copies_btn.setText(button)
            color = theme.COLOR_STATE_WAIT
        elif showing_copy:
            self._notice_mode = "copy"
            self._replica_text.setText(i18n.t(
                "main.replica_banner",
                when=fmt_relative(self._replica_synced_at)))
            self._copies_btn.setText(i18n.t("main.replica_banner_btn"))
            # 상태줄의 연결 표시와 같은 색을 쓴다(접속 중이면 주황, 끊겼으면 빨강).
            color = _ConnectionBadge._COLORS.get(
                self._conn_badge.state(), theme.COLOR_STATE_WAIT)
        elif waiting:
            self._notice_mode = "drafts"
            self._replica_text.setText(i18n.t("drafts.notice", count=waiting))
            self._copies_btn.setText(i18n.t("drafts.notice_btn"))
            color = theme.COLOR_STATE_WAIT
        else:
            self._notice_mode = ""
            color = theme.COLOR_STATE_OFF
        self._notice_dot.setStyleSheet(
            f"background: {color}; border-radius: 3px; border: none;")
        self._replica_banner.setVisible(bool(self._notice_mode))
        self._scroll.parentWidget().layout().setSpacing(0)

    def _on_notice_action(self) -> None:
        mode = getattr(self, "_notice_mode", "")
        if mode == "drafts":
            self._drafts_postponed = False
            self._ask_about_drafts()
        elif mode == "gate":
            if self._gate in ("password", "open"):
                self._open_gate()
            else:
                self._gate = ""
                self._client.resume()
                self._refresh_status_text()
                self._refresh_replica_banner()
        else:
            self.open_copies()

    # ── 끊긴 동안 쓴 메모 ──

    def _address(self) -> str:
        """붙으려는 공유 PC 의 주소. 끊긴 동안 쓴 글에 함께 적어 둔다."""
        ls = self._local.state
        return f"{ls.last_host_ip}:{ls.last_host_port}"

    def _can_work_offline(self) -> bool:
        """공유 PC 없이 이 PC 에서만 메모를 만들고 고칠 수 있는 때인가."""
        return self._drafts is not None and not self._connected

    def _is_local_note(self, note_id: str) -> bool:
        return (self._notes.get(note_id) or {}).get(LOCAL_MARK) == KIND_NEW

    @staticmethod
    def _blank(content: str) -> bool:
        return (not html_to_plain(content or "").replace("\ufffc", "").strip()
                and "<img" not in (content or "").lower())

    def _pending_drafts(self) -> list[Draft]:
        """내가 끊긴 동안 쓴 것들(올리는 중인 것은 뺀다)."""
        if self._drafts is None:
            return []
        try:
            found = self._drafts.pending(self._client.nickname())
        except Exception:
            return []
        return [d for d in found if d.key not in self._drafts_in_flight]

    def _create_offline_note(self, color: str, folder_id: str | None) -> None:
        me = self._client.nickname()
        now = int(time.time() * 1000)
        # 원래 메모에 넣지 못해 새 메모로 남기려던 글이 있으면 그 글을 담는다.
        content = self._orphan_texts.pop(0) if self._orphan_texts else ""
        note = {
            "id": new_id(), "content": content, "color": color,
            "width": config.DEFAULT_NOTE_WIDTH,
            "height": config.DEFAULT_NOTE_HEIGHT,
            "created_by": me, "created_at": now,
            "updated_by": me, "updated_at": now, "version": 1, "deleted": 0,
            "folder_id": folder_id if folder_id in self._folders else None,
            # 올릴 때 나만 보기로 들어간다. 그때까지도 그렇게 보여 준다.
            "private_owner": me, LOCAL_MARK: KIND_NEW,
        }
        self._drafts.save_new(self._storable(note), address=self._address(),
                              nickname=me)
        self._on_note_created(note)
        w = self._stickies.get(note["id"])
        if w is not None:
            w.set_offline(True)

    @staticmethod
    def _storable(note: dict) -> dict:
        return {k: v for k, v in note.items() if k != LOCAL_MARK}

    def _save_offline(self, note_id: str, content: str, color: str,
                      w: int, h: int) -> None:
        """끊긴 동안 고친 글을 이 PC 에 적어 둔다."""
        note = self._notes.get(note_id)
        if note is None or self._drafts is None:
            return
        me = self._client.nickname()
        changed = dict(note, content=content, color=color, width=w, height=h,
                       updated_by=me, updated_at=int(time.time() * 1000))
        if note.get(LOCAL_MARK) == KIND_NEW:
            changed["version"] = int(note.get("version") or 1) + 1
            self._drafts.save_new(self._storable(changed),
                                  address=self._address(), nickname=me)
            self._on_note_updated(changed)
            return
        # 사본의 메모다. 연결돼 있을 때 쓰기 시작한 글은 메모 창이 '보내지 못한
        # 글'로 쥐고 있다가 다시 붙으면 제자리에 넣는다. 여기에는 앱을 껐다
        # 켜도 남도록 적어만 둔다.
        started_online = note_id not in self._offline_edit_ids
        if started_online:
            self._set_aside_older_draft(note_id, content)
        self._drafts.save_edit(
            note_id, self._storable(changed),
            base_version=int(note.get("version") or 0),
            started_online=started_online,
            address=self._address(), nickname=me)
        if started_online:
            return
        changed[LOCAL_MARK] = KIND_EDIT
        self._on_note_updated(changed)

    def _set_aside_older_draft(self, note_id: str, content: str) -> None:
        """이 메모를 고쳐 적어 둔 글이 전부터 있으면 새 메모로 돌려 둔다.

        다시 붙었을 때 보류한 글이다. 그 뒤 붙어 있는 동안 쓰기 시작한 글은
        공유 PC 의 글에서 시작한 다른 글이라, 같은 자리에 적으면 먼저 쓴 글이
        묻지도 않고 사라진다. 올리면 어차피 새 메모가 될 글이므로 지금 그렇게
        적어 둔다."""
        if self._drafts is None or note_id in self._kept_since_welcome:
            return
        self._kept_since_welcome.add(note_id)
        if note_id in self._draft_uploads:
            return                          # 제자리에 넣는 중인 같은 줄기의 글이다
        me = self._client.nickname()
        earlier = self._drafts.get_edit(note_id, me)
        if (earlier is None or earlier.content == content
                or self._blank(earlier.content)):
            return
        self._drafts.remove(earlier.key)
        self._drafts.save_new(dict(earlier.note, id=new_id(), private_owner=me),
                              address=self._address(), nickname=me)
        # 끊긴 동안이면 바탕화면과 목록에도 따로 보인다(붙어 있으면 아무 일 없다).
        QTimer.singleShot(0, self._show_drafts)

    def _keep_text_in_flight(self) -> None:
        """프로그램을 끝내며 보내는 글을 이 PC 에도 적어 둔다.

        답을 기다리지 않고 끝나므로 그 글이 공유 PC 에 닿았는지 알 수 없다(막
        끊긴 연결은 보낸 것처럼 보인다). 닿았으면 다음에 붙을 때 같은 글이라
        조용히 지워지고, 닿지 못했으면 그때 제자리에 넣는다."""
        if (self._drafts is None or self._role != "guest"
                or not self._connected):
            return
        me = self._client.nickname()
        try:
            for note_id, w in list(self._stickies.items()):
                note = self._notes.get(note_id)
                if (note is None or note_id in self._offline_edit_ids
                        or note.get(LOCAL_MARK)):
                    continue
                text = w.unsent_edit()
                if text is None:
                    continue
                self._set_aside_older_draft(note_id, text)
                self._drafts.save_edit(
                    note_id, self._storable(dict(note, content=text)),
                    base_version=w.unsent_base_version(), started_online=True,
                    address=self._address(), nickname=me)
            self._keep_unanswered_text()
        except Exception:
            pass                            # 끝내는 길이다. 적지 못해도 보내기는 한다

    def _delete_local_note(self, note_id: str) -> None:
        """끊긴 동안 만든 메모를 지운다. 공유 PC 에 없는 메모라 이 PC 에서만 지운다."""
        note = self._notes.get(note_id)
        if note is None:
            return
        if not confirm.ask(
                self, i18n.t("main.msg_delete_confirm_title"),
                i18n.t("main.msg_delete_confirm_body",
                       title=self._note_title(note, 30)),
                detail=i18n.t("drafts.delete_detail"),
                action=i18n.t("common.btn_delete"), danger=True):
            return
        if self._drafts is not None:
            self._drafts.remove(note_id)
        self._offline_edit_ids.discard(note_id)
        # 내가 지우는 것이다. 쓰던 글을 새 메모로 남기지 않는다.
        self._discarding.add(note_id)
        try:
            self._on_note_deleted(note_id)
        finally:
            self._discarding.discard(note_id)

    def _enter_offline(self) -> None:
        """공유 PC 와 끊겼다(또는 아직 붙지 못했다)."""
        me = self._client.nickname()
        # 누가 편집 중이었는지는 이제 알 수 없다. 남의 잠금 표시를 걷어, 끊긴
        # 동안에도 메모를 고칠 수 있게 한다.
        self._locks = {nid: who for nid, who in self._locks.items() if who == me}
        for w in self._stickies.values():
            if w.is_locked_by_other():
                w.set_lock_holder(None)
        # 보내 놓고 답을 받지 못한 일은 잊는다. 다시 붙었을 때 엉뚱한 메모와
        # 짝지어지지 않게 한다. 올리던 글은 적어 둔 채이므로 다시 묻게 된다.
        self._keep_unanswered_text()
        self._recreate_queue.clear()
        self._link_note_queue.clear()
        self._pending_restore.clear()
        self._draft_uploads.clear()
        self._drafts_in_flight.clear()
        if (self._role == "guest" and self._was_connected and self._notes
                and not self._replica_shown):
            # 지금 보이는 것은 끊기기 직전에 받은 내용이다. 앱을 켤 때 사본을
            # 보여 주는 것과 같게 목록 위에 알린다.
            self._replica_shown = True
            self._replica_synced_at = int(time.time() * 1000)
        self._show_drafts()

    def _keep_unanswered_text(self) -> None:
        """잠금을 청해 놓고 넣지 못한 글과, 새 메모에 담으려던 글을 챙긴다.

        그대로 두면 다시 붙은 뒤 그 메모를 편집하려고 누르는 순간 옛 글이
        들어가거나, 다음에 만드는 아무 메모에 담긴다. 이 PC 에 적어 두었다가
        다시 붙었을 때 다룬다(적어 둘 곳이 없으면 새 메모에 담을 글로 남긴다)."""
        me = self._client.nickname()
        pending, self._pending_resend = self._pending_resend, {}
        for note_id, text in pending.items():
            if note_id in self._draft_uploads:
                continue                    # 올리던 글이다. 적어 둔 것이 그대로 있다
            note = self._notes.get(note_id)
            if self._drafts is None or note is None:
                self._orphan_texts.append(text)
                continue
            self._set_aside_older_draft(note_id, text)
            self._drafts.save_edit(
                note_id, self._storable(dict(note, content=text)),
                base_version=int(note.get("version") or 0), started_online=True,
                address=self._address(), nickname=me)
        if self._drafts is None:
            return
        orphans, self._orphan_texts = self._orphan_texts, []
        now = int(time.time() * 1000)
        for text in orphans:
            self._drafts.save_new({
                "id": new_id(), "content": text,
                "color": self._settings.default_note_color or config.DEFAULT_COLOR,
                "width": config.DEFAULT_NOTE_WIDTH,
                "height": config.DEFAULT_NOTE_HEIGHT,
                "created_by": me, "created_at": now, "updated_by": me,
                "updated_at": now, "version": 1, "deleted": 0,
                "folder_id": None, "private_owner": me,
            }, address=self._address(), nickname=me)

    def _show_drafts(self) -> None:
        """끊긴 동안 쓴 메모를 목록과 바탕화면에 올려 둔다(붙어 있지 않을 때만)."""
        if not self._can_work_offline():
            return
        me = self._client.nickname()
        shown = False
        for d in self._pending_drafts():
            if d.kind == KIND_NEW:
                if d.key in self._notes:
                    continue
                note = dict(d.note, id=d.key)
                note[LOCAL_MARK] = KIND_NEW
            else:
                base = self._notes.get(d.base_id)
                if (base is None and d.started_online
                        and not (self._replica_shown or self._was_connected)):
                    # 받아 둔 메모를 아직 하나도 보이지 못했다. 원래 메모가 없는
                    # 것이 아니라 모르는 것이다. 붙어 있을 때 쓰던 글이므로 붙은
                    # 뒤에 제자리에 넣을 수 있는지 본다.
                    continue
                if base is None:
                    # 원래 메모가 사본에 없다. 끊긴 동안 만든 메모로 다룬다.
                    note = dict(d.note, id=new_id(), private_owner=me)
                    self._drafts.remove(d.key)
                    self._drafts.save_new(note, address=self._address(), nickname=me)
                    note[LOCAL_MARK] = KIND_NEW
                else:
                    held = self._stickies.get(d.base_id)
                    if held is not None and held.unsent_edit() is not None:
                        continue        # 메모 창이 그 글을 쥐고 있다
                    if (base.get(LOCAL_MARK) == KIND_EDIT
                            and (base.get("content") or "") == d.content):
                        continue
                    # 내가 고친 글이다. 고친 사람을 원래 메모의 것으로 두면 그 사람이
                    # 방금 고친 것처럼 알림이 뜬다.
                    note = dict(base, content=d.content,
                                color=d.note.get("color") or base.get("color"),
                                updated_by=me,
                                updated_at=d.note.get("updated_at")
                                or base.get("updated_at"))
                    note[LOCAL_MARK] = KIND_EDIT
            if note["id"] in self._notes:
                self._on_note_updated(note)
            else:
                self._notes[note["id"]] = note
                self._append_card(note)
                self._ensure_sticky(note)
            w = self._stickies.get(note["id"])
            if w is not None:
                w.set_offline(True)
            shown = True
        if shown:
            self._apply_search_filter()
            self._refresh_folder_icons()
            self._invalidate_tray_menu()

    def _flush_offline_edits(self) -> None:
        """끊긴 동안 시작한 편집을 지금 글까지 적어 둔다. 다시 붙어 화면이 공유
        PC 의 내용으로 바뀌기 직전에 부른다."""
        for nid, w in list(self._stickies.items()):
            if nid in self._offline_edit_ids or self._is_local_note(nid):
                w.flush_pending_edit()
        self._offline_edit_ids.clear()

    def _forget_draft(self, draft: Draft) -> None:
        if self._drafts is None:
            return
        self._drafts.remove(draft.key)
        if draft.kind == KIND_NEW:
            self._local.state.remove_note(draft.key)
            self._local.save()

    def _finish_draft(self, key: str) -> None:
        """끊긴 동안 쓴 글 하나가 공유 PC 에 들어갔다."""
        self._drafts_in_flight.discard(key)
        if self._drafts is not None:
            draft = self._drafts.get(key)
            if draft is not None:
                self._forget_draft(draft)
        self._refresh_replica_banner()

    def _settle_drafts(self, handled: set[str]) -> None:
        """다시 붙은 뒤, 끊긴 동안 쓴 글을 정리하고 남은 것은 어떻게 할지 묻는다.

        ``handled`` 는 메모 창이 쥐고 있다가 이미 다시 보낸 글의 메모 id 다."""
        if self._drafts is None:
            return
        for d in self._pending_drafts():
            if d.kind == KIND_EDIT and d.base_id in handled:
                self._forget_draft(d)           # 방금 그 글을 다뤘다
                continue
            if self._blank(d.content):
                self._forget_draft(d)           # 쓴 것이 없다
                continue
            if d.kind != KIND_EDIT:
                continue
            note = self._notes.get(d.base_id)
            if note is not None and (note.get("content") or "") == d.content:
                self._forget_draft(d)           # 같은 글이 이미 들어가 있다
                continue
            if (d.started_online and note is not None
                    and int(note.get("version") or 0) == d.base_version
                    and not self._locks.get(d.base_id)):
                # 연결돼 있을 때 쓰기 시작한 글이고 그 사이 아무도 고치지 않았다.
                # 잠깐 끊겼다 붙은 것이므로 묻지 않고 제자리에 넣는다.
                self._drafts_in_flight.add(d.key)
                self._draft_uploads[d.base_id] = d.key
                self._pending_resend[d.base_id] = d.content
                self._client.acquire_lock(d.base_id)
        self._refresh_replica_banner()
        if self._pending_drafts() and not self._drafts_postponed:
            QTimer.singleShot(DRAFTS_ASK_MS, self._ask_about_drafts_when_free)

    def _ask_about_drafts_when_free(self) -> None:
        """다시 붙은 뒤 스스로 묻는 경우. 전체 화면으로 발표하는 중이면 창을
        띄우지 않고 기다린다(목록 위의 알림은 그대로 있다)."""
        if toast.user_is_busy():
            QTimer.singleShot(DRAFTS_BUSY_RETRY_MS, self._ask_about_drafts_when_free)
            return
        if not self._drafts_postponed:
            self._ask_about_drafts()

    def _ask_about_drafts(self) -> None:
        """끊긴 동안 쓴 메모를 공유 PC 에 올릴지, 지울지, 나중에 정할지 묻는다."""
        if self._drafts_asking or not self._connected:
            return
        self._drafts_asking = True
        try:
            while True:
                drafts = self._pending_drafts()
                if not drafts:
                    break
                choice = self._choose_for_drafts(drafts)
                if choice == "keep":
                    self._upload_drafts(drafts)
                    break
                if choice != "discard":
                    self._drafts_postponed = True
                    break
                if confirm.ask(
                        self, i18n.t("drafts.title"),
                        i18n.t("drafts.discard_body", count=len(drafts)),
                        detail=i18n.t("drafts.discard_detail"),
                        action=i18n.t("common.btn_delete"), danger=True):
                    for d in drafts:
                        self._forget_draft(d)
                    break
        finally:
            self._drafts_asking = False
        self._refresh_replica_banner()

    def _choose_for_drafts(self, drafts: list[Draft]) -> str:
        """'keep'(저장: 공유 PC 에 올린다), 'discard'(삭제), 'later'(보류)."""
        rows = [(d.kind, drafts_dialog.first_sentence(d.content)) for d in drafts]
        return drafts_dialog.ask(self, rows)

    def _upload_drafts(self, drafts: list[Draft]) -> None:
        """끊긴 동안 쓴 메모를 공유 PC 에 새 메모로 만든다(나만 보기).

        고친 메모도 새 메모가 된다. 원래 메모는 건드리지 않는다."""
        if not self._require_online():
            return
        me = self._client.nickname()
        for d in drafts:
            note = d.note
            color = note.get("color") or config.DEFAULT_COLOR
            width = int(note.get("width") or config.DEFAULT_NOTE_WIDTH)
            height = int(note.get("height") or config.DEFAULT_NOTE_HEIGHT)
            folder_id = (note.get("folder_id")
                         if note.get("folder_id") in self._folders else None)
            if d.kind == KIND_NEW:
                seen = self._local.state.notes.get(d.key)
                pos = (seen.x, seen.y) if seen is not None else None
                hidden = seen.hidden if seen is not None else False
            else:
                seen = self._local.state.notes.get(d.base_id)
                pos = ((seen.x + DRAFT_OFFSET, seen.y + DRAFT_OFFSET)
                       if seen is not None else None)
                hidden = False
            if pos is None:
                at = self._new_note_position(width, height, None)
                pos = (at.x(), at.y())
            self._drafts_in_flight.add(d.key)
            self._recreate_queue.append({
                "content": d.content, "color": color, "width": width,
                "height": height, "folder_id": folder_id, "private": True,
                "pos": pos, "hidden": hidden, "draft": d.key,
            })
            self._client.create_note(
                color, width, height, folder_id=folder_id,
                private_owner=me, quiet=True)
        self._status.showMessage(
            i18n.t("drafts.uploaded", count=len(drafts)), 6000)

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
                    and self._locks.get(note_id) in (None, self._client.nickname())):
                # 잠깐 끊겼다 붙으면 공유 PC 가 끊긴 연결의 내 잠금을 아직 들고
                # 있을 수 있다. 그것은 남이 편집 중인 것이 아니다.
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

    def _take_gone_state(self, note_id: str):
        """방금 사라졌던 메모의 자리와 접힘. 없거나 오래됐으면 None."""
        now = time.monotonic()
        self._gone_states = {nid: kept for nid, kept in self._gone_states.items()
                             if now - kept[1] < GONE_STATE_TTL}
        kept = self._gone_states.pop(note_id, None)
        return kept[0] if kept is not None else None

    def _on_note_restored(self, info: dict) -> None:
        """휴지통에서 되살아난 메모가 곧 온다(_on_note_created 가 이어서 받는다)."""
        note_id = str(info.get("note_id") or "")
        if note_id:
            self._restoring[note_id] = str(info.get("user") or "")

    def _state_for_restored(self, note: dict, by: str) -> NoteLocalState:
        """휴지통에서 돌아온 메모를 이 PC 에서 둘 자리. 버리기 전의 자리와 접힘이
        남아 있으면 그대로 쓴다. 없으면 되살린 사람의 화면에만 띄운다."""
        kept = self._local.state.take_trashed(note["id"])
        if kept is not None:
            return kept
        state = NoteLocalState()
        pos = self._new_note_position(
            int(note.get("width") or config.DEFAULT_NOTE_WIDTH),
            int(note.get("height") or config.DEFAULT_NOTE_HEIGHT), None)
        state.x, state.y = pos.x(), pos.y()
        state.hidden = by != self._client.nickname()
        return state

    def _on_note_created(self, note: dict) -> None:
        back = self._take_gone_state(note["id"])
        restored_by = self._restoring.pop(note["id"], None)
        if back is None and restored_by is not None:
            back = self._state_for_restored(note, restored_by)
        # 다시 온 메모다. 휴지통으로 갔을 때를 대비해 적어 둔 자리는 이제 쓸 일이 없다.
        self._local.state.trashed.pop(note["id"], None)
        if back is not None:
            # 새 메모가 아니다. 공개 범위가 바뀌어 지워졌다가 다시 온 같은
            # 메모다. 두던 자리, 크기, 접힘, 숨김을 그대로 되살린다.
            self._notes[note["id"]] = note
            self._local.state.notes[note["id"]] = back
            self._local.save()
            self._append_card(note)
            self._apply_search_filter()
            self._ensure_sticky(note)
            self._sync_history_notes()
            self._refresh_folder_icons()
            self._invalidate_tray_menu()
            return
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
            if restoring.get("draft"):
                # 끊긴 동안 쓴 글을 올리는 중이다. 글이 들어가면 적어 둔 것을 지운다.
                self._draft_uploads[note["id"]] = restoring["draft"]
            self._pending_resend[note["id"]] = restoring["content"]
        elif is_creator:
            pos = self._new_note_position(
                int(note.get("width") or config.DEFAULT_NOTE_WIDTH),
                int(note.get("height") or config.DEFAULT_NOTE_HEIGHT),
                self._take_new_note_spot())
            st.x, st.y = pos.x(), pos.y()
        self._local.save()
        # 남이 만든 메모는 숨김으로 들어온다. '숨김 N' 의 수를 맞춘다.
        self._apply_search_filter()
        if is_creator:
            # 아이콘으로 접어 둔 폴더에 새 메모를 만들면 보이지 않는다. 폴더를 연다.
            self._open_folder_of(note["id"], animate=False)
        self._ensure_sticky(note)
        self._sync_history_notes()
        if restoring is not None:
            self._recovery_notes.add(note["id"])   # '보냈습니다' 안내는 띄우지 않는다
            # 글을 넣으려고 잠금을 청한다. 메모 창을 만든 뒤에 청해야 한다: 공유
            # PC 에서는 답이 그 자리에서 오므로, 먼저 청하면 글이 들어간 뒤에
            # 창이 빈 옛 내용으로 만들어져 그 뒤의 편집이 모두 거절된다.
            self._client.acquire_lock(note["id"])
            self._refresh_folder_icons()
            self._invalidate_tray_menu()
            if restoring.get("front"):
                # 이력 창에서 되살린 메모는 어디에 생겼는지 보여야 한다.
                w = self._stickies.get(note["id"])
                if w is not None:
                    w.bring_to_front()
            return
        if is_creator:
            recovered = bool(self._orphan_texts)
            links = None
            if recovered:
                # 끊긴 동안 쓴 글을 담으려고 만든 메모다. 그 글을 넣는다.
                self._pending_resend[note["id"]] = self._orphan_texts.pop(0)
                self._recovery_notes.add(note["id"])
                self._client.acquire_lock(note["id"])
                self._status.showMessage(
                    i18n.t("main.status_unsent_new_note"), 10000)
            elif self._link_note_queue:
                links = self._link_note_queue.pop(0)
            # 메모는 평소 바탕 화면 쪽에 깔린다. 방금 만든 메모는 다른 창을 쓰던
            # 중이어도 보여야 하고 바로 쓸 수 있어야 하므로 앞으로 올린다.
            w = self._stickies.get(note["id"])
            if w is not None:
                w.bring_to_front(start_editing=not recovered and not links)
                if links:
                    # 파일 우클릭 메뉴로 만든 메모: 그 파일의 링크를 넣는다.
                    self._link_note_recent = (note["id"], time.monotonic())
                    w.add_file_links(links)
        self._refresh_folder_icons()
        self._invalidate_tray_menu()

    def _on_note_updated(self, note: dict) -> None:
        self._notice_live_edit(self._notes.get(note["id"]), note)
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
        gone = self._notes.pop(note_id, None)
        self._edit_handled.pop(note_id, None)
        if note_id in self._selected_notes:
            self._selected_notes.discard(note_id)
            self._refresh_bulk_bar()
        c = self._cards.pop(note_id, None)
        if c is not None:
            c.setParent(None)
            c.deleteLater()
        w = self._stickies.pop(note_id, None)
        if w is not None:
            # 내가 쓰던 메모가 사라졌다(다른 사람이 나만 보기로 바꾸거나 자기
            # 폴더로 옮겼다). 보내지 못한 글은 새 메모로 남기고, 쥐고 있던 잠금은
            # 돌려준다(그러지 않으면 그 사람에게 내가 편집 중이라고 한동안 보인다).
            unsent = (None if undoable is not None or note_id in self._discarding
                      else w.unsent_edit())
            if unsent is not None and not self._blank(unsent):
                self._keep_as_new_note(unsent, private=True)
            if (self._locks.get(note_id) == self._client.nickname()
                    and not (gone or {}).get(LOCAL_MARK)):
                self._client.release_lock(note_id)
            # closeEvent 의 hideRequested 가 local_state 를 오염시키지 않도록
            # teardown 으로 표시한 뒤 닫는다(_on_welcomed 와 같은 예방책).
            w._teardown = True
            w.close()
            w.deleteLater()
        self._locks.pop(note_id, None)
        self._pending_resend.pop(note_id, None)
        self._pending_restore.pop(note_id, None)
        # 같은 메모가 곧바로 다시 올 수 있다(공개 범위가 바뀌면 옛 공유 PC 는
        # 지웠다가 다시 만들어 보낸다). 그때 자리와 접힘을 되살리려고 잠깐 들고 있는다.
        state = self._local.state.notes.get(note_id)
        if state is not None and undoable is None:
            self._gone_states[note_id] = (state, time.monotonic())
        if self._trash_days and not (gone or {}).get(LOCAL_MARK):
            # 휴지통에서 되살아나면 두던 자리로 돌아오게 적어 둔다.
            self._local.state.keep_trashed(note_id)
        self._local.state.remove_note(note_id)
        self._local.save()
        self._apply_search_filter()
        self._refresh_folder_icons()
        self._invalidate_tray_menu()
        self._sync_history_notes()

    def _on_folder_created(self, folder: dict) -> None:
        if not folder.get("id"):
            return
        self._folders[folder["id"]] = folder
        self._refresh_folder_chip()
        self._refresh_card_folder_context()
        asked = self._new_folder_spots.pop(folder["id"], None)
        if asked is not None and time.monotonic() - asked[0] <= NEW_NOTE_SPOT_TTL:
            # 우클릭 메뉴로 만든 폴더다. 그 자리에 아이콘을 두고 이름을 받는다.
            self._place_folder_icon(folder["id"], at=asked[1], quiet=True)
            icon = self._folder_icons.get(folder["id"])
            if icon is not None:
                icon.begin_rename()
            return
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
        # 나만 보는 폴더도 이름만 적는다(단추의 자물쇠가 알려 준다). 이름 뒤에
        # 붙인 글은 폴더가 아닌 다른 분류처럼 읽혔다.
        name = f.get("name") or i18n.t("main.folder_default_name")
        return name, f.get("color") or config.DEFAULT_COLOR

    def _show_folder_selector(self) -> None:
        menu = self._folder_selector_menu()
        menu.exec(self._folder_chip.mapToGlobal(
            QPoint(0, self._folder_chip.height())))

    def _folder_selector_menu(self) -> QMenu:
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
                color = f.get("color") or config.DEFAULT_COLOR
                # 나만 보는 폴더는 폴더 색의 자물쇠로 표시한다. 다른 폴더와 같은
                # 줄에 같은 꼴(색 표시와 이름)로 놓여 폴더로 읽힌다.
                icon = (folder_lock_icon(color) if f.get("private_owner")
                        else color_swatch_icon(color))
                fid = f["id"]
                add_centered_menu_action(
                    menu, icon, name,
                    lambda _fid=fid: self._select_folder(_fid))
        menu.addSeparator()
        add_centered_menu_action(
            menu, svg_icon("plus"), i18n.t("main.menu_new_folder"),
            self._create_folder_dialog)
        if self._trash_days and self._connected:
            menu.addSeparator()
            add_centered_menu_action(
                menu, svg_icon("trash"), i18n.t("main.folder_trash"),
                self.open_trash)
        return menu

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
        self._delete_folder(self._current_folder)

    def _delete_folder(self, folder_id: str) -> None:
        """폴더를 지운다(묻고 나서). 메인 창의 메뉴와 폴더 아이콘의 메뉴가 함께 쓴다."""
        f = self._folders.get(folder_id)
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
            uploaded = self._draft_uploads.pop(note_id, None)
            if uploaded is not None:
                self._finish_draft(uploaded)
            if note_id in self._recovery_notes:
                # 새 메모로 남겼다는 안내가 이미 떠 있다.
                self._recovery_notes.discard(note_id)
            elif uploaded is None:
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
            uploaded = self._draft_uploads.pop(note_id, None)
            if uploaded is not None:
                self._finish_draft(uploaded)
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

    def _on_revive_requested(self, content: str, color: str) -> None:
        """삭제된 메모를 그 시점의 본문으로 새 메모에 되살린다.

        삭제된 메모가 나만 보던 것이었는지는 이력에 남지 않는다. 남에게 보이면
        안 되는 글일 수 있으므로 나만 보는 메모로 만든다(공개는 사용자가 바꾼다)."""
        if not self._require_online():
            return
        # 메모 색은 견본 밖의 색일 수도 있다('다른 색'). 색이 아닌 값만 바꾼다.
        if not QColor(color or "").isValid():
            color = self._settings.default_note_color or config.DEFAULT_COLOR
        width, height = config.DEFAULT_NOTE_WIDTH, config.DEFAULT_NOTE_HEIGHT
        pos = self._new_note_position(width, height, None)
        self._recreate_queue.append({
            "content": html_guard.clean(content or ""), "color": color,
            "width": width, "height": height, "folder_id": None,
            "private": True, "pos": (pos.x(), pos.y()), "hidden": False,
            "front": True,
        })
        self._client.create_note(
            color, width, height, folder_id=None,
            private_owner=self._client.nickname(), quiet=True)
        self._status.showMessage(i18n.t("main.status_revived"), 6000)

    def _on_lock_held(self, note_id: str, holder: str) -> None:
        self._locks[note_id] = holder
        if self._edit_handled.get(note_id) != holder:
            self._edit_handled.pop(note_id, None)
        w = self._stickies.get(note_id)
        if w is not None:
            w.set_lock_holder(holder)

    def _on_lock_released(self, note_id: str) -> None:
        self._locks.pop(note_id, None)
        self._edit_handled.pop(note_id, None)
        w = self._stickies.get(note_id)
        if w is not None:
            if (w.is_editing() and note_id not in self._offline_edit_ids
                    and not self._is_local_note(note_id)):
                # 내가 편집 중인데 잠금이 풀렸다: 오래 손대지 않아 공유 PC 가
                # 거둔 것이다(내가 끝낸 편집이면 이미 편집 중이 아니다). 편집을
                # 끝내고, 보내지 못한 글이 있으면 잠금을 다시 받아 보낸다.
                unsent = w.lock_lost()
                if unsent is not None:
                    self._pending_resend[note_id] = unsent
                    self._client.acquire_lock(note_id)
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

    def _on_note_edited(self, entry: dict) -> None:
        """다른 사람이 메모를 고치고 편집을 마쳤다.

        호스트는 편집이 끝나 잠금을 풀 때, 내용이나 색이 달라졌으면 이력 항목을
        한 번 보낸다. 편집 중에 저장된 글로 이미 알렸으면 다시 알리지 않는다."""
        self._announce_edit(
            str(entry.get("user") or ""), str(entry.get("note_id") or ""),
            str(entry.get("content_before") or ""),
            str(entry.get("content_after") or ""))

    def _notice_live_edit(self, old: dict | None, new: dict) -> None:
        """다른 사람이 편집 중에 저장한 글을 받았다.

        편집이 끝날 때 오는 이력 항목만 기다리면, 쓰던 사람이 메모에 커서를 둔
        동안에는 글이 바뀌어도 알림이 없다. 폴더, 공개 범위, 크기만 바뀐 것은
        내용이 같으므로 여기서 알리지 않는다."""
        if old is None:
            return
        before = str(old.get("content") or "")
        after = str(new.get("content") or "")
        if before != after:
            self._announce_edit(str(new.get("updated_by") or ""),
                                str(new.get("id") or ""), before, after)

    def _announce_edit(self, who: str, note_id: str, before: str,
                       after: str) -> None:
        """다른 사람의 수정을 알린다. 편집 한 번에 한 번이고, 글자마다 알리지 않는다.

        빈 메모에 처음 글을 채운 것은 알리지 않는다. 새 메모는 만들 때 이미
        알렸고, 지웠다 되돌린 메모와 사본에서 가져온 메모도 이 길로 채워진다."""
        if (not who or not note_id or who == self._client.nickname()
                or not self._settings.notify_note_edit):
            return
        now = time.monotonic()
        key = (who, note_id)
        last = self._edit_notified.get(key)
        if len(self._edit_notified) > 200:
            self._edit_notified = {
                k: t for k, t in self._edit_notified.items()
                if now - t < self._EDIT_NOTICE_COOLDOWN}
        # 잇달아 고치는 동안에는 조용한 시간이 이어진다.
        self._edit_notified[key] = now
        if self._locks.get(note_id) == who:
            # 그 사람이 지금 편집 중이다. 이번 편집은 한 번만 다룬다.
            if self._edit_handled.get(note_id) == who:
                return
            self._edit_handled[note_id] = who
        if not before.strip():
            return
        if last is not None and now - last < self._EDIT_NOTICE_COOLDOWN:
            return
        title = (html_to_plain(after).strip().splitlines() or [""])[0].strip()
        if len(title) > self._NOTICE_TITLE_MAX:
            title = title[:self._NOTICE_TITLE_MAX].rstrip() + "…"
        self._announce_edited.setdefault(who, {})[note_id] = title
        self._announce_timer.start()

    def _flush_announcements(self) -> None:
        created, self._announce = self._announce, {}
        shared, self._announce_shared = self._announce_shared, {}
        edited, self._announce_edited = self._announce_edited, {}
        for who, notes in edited.items():
            titles = list(notes.values())
            if len(titles) > 1:
                text = i18n.t("main.notify_edited_notes", name=who,
                              count=len(titles))
            elif titles[0]:
                text = i18n.t("main.notify_edited_titled", name=who,
                              title=titles[0])
            else:
                text = i18n.t("main.notify_edited_note", name=who)
            self._show_notification(text)
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
        ids = [nid for nid in self._selected_notes if nid in self._notes]
        locked = [nid for nid in ids
                  if self._move_blocked_by(nid, folder_id)]
        if locked:
            self._show_notification(
                i18n.t("error.locked",
                       holder=self._locked_by_other(locked[0])), True)
            ids = [nid for nid in ids if nid not in locked]
            if not ids:
                return
        for nid in ids:
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
        ids = [nid for nid in self._selected_notes if nid in self._notes]
        # 다른 사람이 편집 중인 메모는 바꾸지 않는다(그 사람의 창이 닫힌다).
        locked = [nid for nid in ids if self._locked_by_other(nid)]
        if locked:
            self._show_notification(
                i18n.t("error.locked",
                       holder=self._locked_by_other(locked[0])), True)
            ids = [nid for nid in ids if nid not in locked]
            if not ids:
                return
        for nid in ids:
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
        if self._delete_many(ids):
            self._exit_selection_mode()

    def _delete_many(self, ids: list[str]) -> bool:
        """메모 여러 개를 지운다. 지우기를 보냈으면 True."""
        if not ids or not self._require_online():
            return False
        # 다른 사람이 편집 중인 메모는 지울 수 없다. 나머지만 지우고 따로 알린다.
        locked = [nid for nid in ids if self._locked_by_other(nid)]
        free = [nid for nid in ids if nid not in locked]
        if not free:
            self._tell_locked(self._locked_by_other(locked[0]))
            return False
        if self._trash_days:
            # 휴지통으로 간다. 되살릴 수 있으므로 묻지 않는다.
            self._delete_with_undo(
                free, i18n.t("main.bulk_trashed", count=len(free)))
        elif confirm.ask(
                self, i18n.t("main.msg_bulk_delete_title"),
                i18n.t("main.msg_bulk_delete_body", count=len(free)),
                detail=i18n.t("main.msg_delete_scope"),
                action=i18n.t("common.btn_delete"), danger=True):
            self._delete_with_undo(
                free, i18n.t("main.bulk_deleted", count=len(free)))
        else:
            return False
        if locked:
            confirm.notify(
                self, i18n.t("main.msg_delete_locked_title"),
                i18n.t("main.msg_bulk_locked_body", count=len(locked)),
                icon=QMessageBox.Warning)
        return True

    def delete_notes(self, note_ids: list[str]) -> None:
        """고른 메모들을 지운다(바탕화면의 아이콘과 폴더 창이 함께 쓴다)."""
        ids = [nid for nid in dict.fromkeys(note_ids) if nid in self._notes]
        if len(ids) == 1:
            self._delete_note_confirm(ids[0])
            return
        # 끊긴 동안 이 PC 에서만 쓴 메모는 이 PC 에서 바로 지운다.
        local = [nid for nid in ids
                 if self._notes[nid].get(LOCAL_MARK) == KIND_NEW]
        for nid in local:
            self._delete_local_note(nid)
        self._delete_many([nid for nid in ids if nid not in local])

    # ── 휴지통 ──────────────────────────────────────────────────────────

    def open_trash(self) -> None:
        """지운 메모를 되살리거나 완전히 지우는 창을 연다."""
        if not self._trash_days or not self._require_online():
            return
        if self._trash_dialog is None:
            dlg = TrashDialog(self._trash_days, self._settings, self,
                              icon=self._app_icon)
            dlg.restoreRequested.connect(self._restore_from_trash)
            dlg.purgeRequested.connect(self._purge_from_trash)
            dlg.emptyRequested.connect(self._empty_trash)
            self._trash_dialog = dlg
        self._trash_dialog.show()
        self._trash_dialog.raise_()
        self._trash_dialog.activateWindow()
        self._client.get_trash()

    def _on_trash_list(self, notes: list) -> None:
        if self._trash_dialog is not None:
            self._trash_dialog.set_notes(notes, self._trash_days or None)

    def _on_trash_changed(self) -> None:
        # 창을 열어 둔 동안에만 다시 묻는다(목록은 공유 PC 가 들고 있다).
        if (self._trash_dialog is not None and self._trash_dialog.isVisible()
                and self._connected):
            self._client.get_trash()

    def _restore_from_trash(self, note_ids: list) -> None:
        if not self._require_online():
            return
        for nid in note_ids:
            self._client.restore_note(nid)

    def _purge_from_trash(self, note_ids: list) -> None:
        if not self._require_online():
            return
        for nid in note_ids:
            self._client.purge_note(nid)

    def _empty_trash(self) -> None:
        if self._require_online():
            self._client.empty_trash()

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
        holder = self._locked_by_other(note_id)
        if holder:
            # 다른 사람이 쓰고 있다. 나만 보기로 바꾸면 그 사람의 창이 쓰던 글과
            # 함께 닫힌다. 공유 PC 도 거절하므로 화면을 먼저 바꾸지 않는다.
            self._show_notification(i18n.t("error.locked", holder=holder), True)
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
        holder = self._move_blocked_by(note_id, folder_id)
        if holder:
            self._show_notification(i18n.t("error.locked", holder=holder), True)
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
                "id": nid,
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
        if self._trash_days:
            # 휴지통에서 그 메모를 그대로 되살린다(이력도 이어진다).
            for snap in snapshots:
                self._client.restore_note(snap["id"])
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
        if note is None:
            return
        if note.get(LOCAL_MARK) == KIND_NEW:
            self._delete_local_note(note_id)
            return
        if not self._require_online():
            return
        holder = self._locked_by_other(note_id)
        if holder:
            # 지울 수 없는 까닭을 확인창을 띄우기 전에 알린다.
            self._tell_locked(holder)
            return
        if self._trash_days:
            # 휴지통으로 간다. 되살릴 수 있으므로 묻지 않는다.
            self._delete_with_undo([note_id], i18n.t("main.status_trashed"))
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

    def _refresh_count(self) -> None:
        """메모 수 단추의 글과, 목록이 비었을 때의 안내를 맞춘다."""
        total = len(self._notes)
        # isVisible() 은 창이 아직 뜨지 않았으면 늘 False 다. 숨겼는지를 본다.
        visible = sum(1 for c in self._cards.values() if not c.isHidden())
        self._refresh_empty_panel(total, visible)
        self._count_btn.setVisible(bool(total))
        # 수는 지금 목록에 보이는 메모 수다(폴더와 검색어에 맞는 것 가운데).
        self._count_btn.setText({
            LIST_ALL: i18n.t("main.count_all", count=visible),
            LIST_SHOWN: i18n.t("main.count_shown", count=visible),
            LIST_HIDDEN: i18n.t("main.count_hidden", count=visible),
        }[self._list_mode])

    def _hidden_note_ids(self) -> set[str]:
        """지금 보는 폴더에서 바탕화면에 숨겨 둔 메모."""
        notes = self._local.state.notes
        return {nid for nid, note in self._notes.items()
                if self._note_in_current_folder(note)
                and nid in notes and notes[nid].hidden}

    def _list_mode_counts(self, hidden_ids: set[str]) -> dict[str, int]:
        """지금 보는 폴더의 메모 수: 전체, 바탕화면에 표시한 것, 숨긴 것."""
        total = sum(1 for note in self._notes.values()
                    if self._note_in_current_folder(note))
        return {LIST_ALL: total, LIST_SHOWN: total - len(hidden_ids),
                LIST_HIDDEN: len(hidden_ids)}

    def _cycle_list_mode(self) -> None:
        """전체 → 표시 → 숨김 차례로 넘긴다. 메모가 없는 쪽은 건너뛴다."""
        counts = self._list_mode_counts(self._hidden_note_ids())
        at = LIST_MODES.index(self._list_mode)
        for step in range(1, len(LIST_MODES) + 1):
            mode = LIST_MODES[(at + step) % len(LIST_MODES)]
            if mode == LIST_ALL or counts[mode]:
                break
        self._list_mode = mode
        self._apply_search_filter()

    def _apply_search_filter(self, text: str | None = None) -> None:
        """폴더 선택, 표시·숨김 고르기, 검색어를 AND 로 결합해 카드 가시성을 정한다."""
        if text is None:
            text = self._search_input.text()
        query = (text or "").strip().lower()
        self._search_query = query
        hidden_ids = self._hidden_note_ids()
        if not self._list_mode_counts(hidden_ids)[self._list_mode]:
            # 고른 쪽에 메모가 없는 폴더다. 빈 목록으로 두지 않고 전체로 돌아간다.
            self._list_mode = LIST_ALL
        for note_id, card in self._cards.items():
            note = self._notes.get(note_id, {})
            matches = self._note_in_current_folder(note) and (
                not query
                or query in html_to_plain(note.get("content") or "").lower())
            if self._list_mode == LIST_SHOWN:
                matches = matches and note_id not in hidden_ids
            elif self._list_mode == LIST_HIDDEN:
                matches = matches and note_id in hidden_ids
            card.setVisible(matches)
        self._refresh_count()

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
            w.requestLock.connect(self._on_request_lock)
            w.releaseLock.connect(self._on_release_lock)
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
            w.set_drop_probe(self._on_icon_dragged)
            w.set_icon_controller(self._icon_ctl)
            self._stickies[note_id] = w

        st = self._local.state.get_note_state(note_id)
        if created:
            # 위치·크기·접힘은 클라이언트별 로컬 상태다. 저장된 크기가 있으면
            # 서버 기본 크기보다 우선한다.
            w.apply_local_state(st.x, st.y, st.w, st.h, folded=st.folded,
                                dock=st.dock, dock_y=st.dock_y,
                                fold_style=st.fold_style, fold_x=st.fold_x,
                                fold_y=st.fold_y, fold_at=st.fold_at)
        elif not w.is_folded():
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

    def _on_request_lock(self, note_id: str) -> None:
        if self._can_work_offline():
            # 공유 PC 에 물을 수 없다. 이 PC 에서만 고친다(다시 붙으면 올릴지 묻는다).
            self._offline_edit_ids.add(note_id)
            w = self._stickies.get(note_id)
            if w is not None:
                w.enter_edit_mode()
            return
        if not self._connected:
            # 끊겼는데 이 PC 에 적어 둘 곳도 없다(파일을 열지 못했다). 요청을
            # 보내도 닿지 않으므로 기다리게 두지 않고 까닭을 알린다.
            w = self._stickies.get(note_id)
            if w is not None:
                w.cancel_pending_lock()
            self._require_online()
            return
        self._client.acquire_lock(note_id)

    def _on_release_lock(self, note_id: str) -> None:
        if note_id in self._offline_edit_ids:
            self._offline_edit_ids.discard(note_id)
            return
        if self._is_local_note(note_id):
            return
        self._client.release_lock(note_id)

    def _on_commit_edit(self, note_id: str, content: str, color: str,
                        w: int, h: int, version: int) -> None:
        if self._drafts is not None and (
                not self._connected or note_id in self._offline_edit_ids
                or self._is_local_note(note_id)):
            self._save_offline(note_id, content, color, w, h)
            return
        self._client.update_note(note_id, content, color, w, h, version)

    def _on_position_changed(self, note_id: str, x: int, y: int) -> None:
        self._local.state.set_note_state(note_id, x=x, y=y)
        self._local.save()

    def _on_size_changed(self, note_id: str, w: int, h: int) -> None:
        self._local.state.set_note_state(note_id, w=w, h=h)
        self._local.save()

    def _on_fold_changed(self, note_id: str, state: dict) -> None:
        self._local.state.set_note_state(note_id, **state)
        self._local.save()
        # 펴거나 벽에 붙이면 더는 아이콘이 아니다. 고른 것에서 뺀다.
        self._icon_ctl.refresh()

    # ── 아이콘 고르기(ui/icon_select.py 의 IconController 가 부른다) ──

    def all_icon_widgets(self) -> dict:
        """메모 창과 폴더 아이콘 전부(아이콘 모양이 아닌 것도)."""
        out = {note_key(nid): w for nid, w in self._stickies.items()}
        out.update({folder_key(fid): icon
                    for fid, icon in self._folder_icons.items()})
        return out

    def icon_widgets(self) -> dict:
        """지금 바탕화면에 아이콘으로 놓여 있는 것들(키 → 창)."""
        out = {note_key(nid): w for nid, w in self._stickies.items()
               if w.isVisible() and w.is_desktop_icon()}
        out.update({folder_key(fid): icon
                    for fid, icon in self._folder_icons.items()
                    if icon.isVisible()})
        return out

    def open_note(self, note_id: str) -> None:
        w = self._stickies.get(note_id)
        if w is not None and w.is_folded():
            w.unfold()
            # 아이콘은 눌려도 앞으로 나오지 않는다. 펴는 메모는 앞으로 부른다.
            w.raise_()
            w.activateWindow()

    def hide_notes(self, note_ids: list[str]) -> None:
        for nid in note_ids:
            if nid in self._notes:
                self._set_note_visibility(nid, False)

    def move_notes_to_folder(self, note_ids: list[str], folder_id: str) -> None:
        for nid in note_ids:
            note = self._notes.get(nid)
            if note is not None and (note.get("folder_id") or None) != folder_id:
                self._on_card_move_to_folder(nid, folder_id)

    def delete_folder(self, folder_id: str) -> None:
        self._delete_folder(folder_id)

    def rename_folder_icon(self, folder_id: str) -> None:
        icon = self._folder_icons.get(folder_id)
        if icon is not None and icon.isVisible():
            icon.begin_rename()

    # ── 폴더 창 ─────────────────────────────────────────────────────────

    def _folder_under_window(self, pos: QPoint) -> str:
        """그 점에 열려 있는 폴더 창의 폴더 id. 없으면 빈 글."""
        for fid, win in self._folder_windows.items():
            if win.isVisible() and win.frameGeometry().contains(pos):
                return fid
        return ""

    def open_folder_window(self, folder_id: str) -> None:
        """폴더에 든 메모를 보여 주는 창을 연다(폴더 아이콘을 두 번 눌렀다)."""
        if folder_id not in self._folders:
            return
        win = self._folder_windows.get(folder_id)
        if win is None:
            win = FolderWindow(folder_id, self._settings,
                               wire_card=self._wire_folder_card,
                               icon=self._app_icon)
            win.noteActivated.connect(self.peek_note)
            win.deleteRequested.connect(self.delete_notes)
            win.visibilityRequested.connect(self._set_notes_visibility)
            self._folder_windows[folder_id] = win
            self._fill_folder_window(folder_id)
            icon = self._folder_icons.get(folder_id)
            if icon is not None:
                # 아이콘 옆에서 열린다. 화면 밖으로 나가면 안으로 들인다.
                area = self._area_at(icon.geometry().center())
                at = icon.geometry().topRight() + QPoint(12, 0)
                at.setX(max(area.left(), min(at.x(), area.right() - win.width())))
                at.setY(max(area.top(), min(at.y(), area.bottom() - win.height() - 40)))
                win.move(at)
        else:
            self._fill_folder_window(folder_id)
        win.show()
        win.raise_()
        win.activateWindow()

    def _folder_items(self, folder_id: str) -> list[dict]:
        """폴더 창에 보일 그 폴더의 메모들(방금 고친 것부터)."""
        notes = [self._notes[nid] for nid in self._folder_note_ids(folder_id)]
        notes.sort(key=lambda n: n.get("updated_at") or 0, reverse=True)
        return [{
            "id": note["id"], "title": self._note_title(note),
            "color": note.get("color") or config.DEFAULT_COLOR,
            "private": bool(note.get("private_owner")),
            "hidden": self._local.state.get_note_state(note["id"]).hidden,
            "content": note.get("content") or "", "note": note,
        } for note in notes]

    def _fill_folder_window(self, folder_id: str) -> None:
        win = self._folder_windows.get(folder_id)
        folder = self._folders.get(folder_id)
        if win is None or folder is None:
            return
        win.set_content(folder.get("name") or "", self._folder_items(folder_id),
                        self._folder_context_for_cards())

    def _refresh_folder_windows(self) -> None:
        for fid, win in list(self._folder_windows.items()):
            if fid not in self._folders:
                # 폴더가 지워졌다. 창도 없앤다.
                del self._folder_windows[fid]
                win.close()
                win.deleteLater()
            elif win.isVisible():
                self._fill_folder_window(fid)

    def _close_folder_windows(self) -> None:
        windows, self._folder_windows = list(self._folder_windows.values()), {}
        for win in windows:
            win.close()
            win.deleteLater()

    def _wire_folder_card(self, card: NoteCard) -> None:
        """폴더 창의 카드 한 장. 누르면 그 메모만 꺼내 보고, 나머지는 메인 창의
        목록에 있는 카드와 똑같이 된다."""
        card.clicked.connect(self.peek_note)
        card.toggleVisibility.connect(self._toggle_visibility_from_card)
        card.deleteRequested.connect(self._delete_note_confirm)
        card.historyRequested.connect(self._open_history_for)
        card.privateToggleRequested.connect(self._on_card_private_toggled)
        card.moveToFolderRequested.connect(self._on_card_move_to_folder)

    def peek_note(self, note_id: str) -> None:
        """폴더 창에서 메모를 열었다. 폴더가 접혀 있어도 그 메모만 바탕화면에
        펼쳐 보인다. 닫으면 다시 폴더 안으로 들어간다."""
        if note_id not in self._notes:
            return
        note = self._notes[note_id]
        state = self._local.state.get_note_state(note_id)
        if state.hidden or self._folder_closed(note.get("folder_id")):
            self._peeked.add(note_id)
        self._sync_note_shown(note_id)
        w = self._stickies.get(note_id)
        if w is not None:
            w.setWindowOpacity(1.0)
            w.reveal()

    def _set_notes_visibility(self, note_ids: list, visible: bool) -> None:
        for nid in note_ids:
            if nid in self._notes:
                self._peeked.discard(nid)
                self._set_note_visibility(nid, visible)

    def realign_icons(self) -> None:
        """진짜 바탕화면 아이콘의 자리가 달라졌을 수 있다. 다시 읽고, 같은 칸에
        겹친 우리 아이콘은 가까운 빈 칸으로 비킨다."""
        if self._force_quit:
            return
        self._desktop_grid.refresh()
        self._align_icons()

    def _resolve_icon_pos(self, owner_key: str, pos, screen_rect):
        """아이콘 모양으로 접은 메모와 폴더 아이콘이 서로 겹치지 않는 격자
        자리를 돌려준다. 진짜 바탕화면 아이콘이 놓인 칸도 비킨다."""
        # 놓여 있는 진짜 아이콘의 자리가 오래됐으면 다시 읽는다.
        self._desktop_grid.refresh_items()
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

    @staticmethod
    def _area_key(area: QRect) -> tuple:
        return area.left(), area.top(), area.width(), area.height()

    def _area_at(self, pos: QPoint) -> QRect:
        """그 점이 놓인(없으면 주) 화면의 작업 영역."""
        screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
        return (screen.availableGeometry() if screen is not None
                else QRect(0, 0, 1920, 1080))

    def _grid_before(self, area: QRect) -> nf.IconGrid | None:
        """이 화면의 아이콘들을 마지막으로 맞춰 놓은 격자. 이번 실행에서 맞춘
        적이 없으면 지난 실행이 적어 둔 것이다. 그것도 없으면(이 기능이 없던
        버전에서 올라왔다, 화면 구성이 달라졌다) None 이고, 아이콘은 지금 있는
        곳에서 가장 가까운 칸으로 간다."""
        key = self._area_key(area)
        if key in self._grids_used:
            return self._grids_used[key]
        saved = self._local.state.icon_grids
        raw = saved.get(",".join(str(v) for v in key))
        if raw is not None:
            return nf.IconGrid(
                area=QRect(area),
                cell=QSize(int(round(raw[0])), int(round(raw[1]))),
                icon=float(nf.ICON_DEFAULT), x0=area.left() + raw[2],
                y0=area.top() + raw[3], dx=raw[0], dy=raw[1])
        return None

    def _align_icons(self) -> None:
        """아이콘으로 접힌 메모와 메모 폴더 아이콘을 지금의 바탕화면 격자에 맞춘다.

        바탕화면 아이콘의 크기가 바뀌면 진짜 아이콘들은 제 칸을 지킨 채 칸의
        크기만 달라진다. 우리 아이콘도 같은 칸으로 옮기고 크기를 맞춘다."""
        if self._force_quit or not self._icons_loaded:
            return
        items: list[tuple[QPoint, str, str]] = []
        for nid, w in self._stickies.items():
            if w.wants_icon_spot():
                items.append((w.icon_spot(), "note", nid))
        for fid, icon in self._folder_icons.items():
            items.append((icon.icon_spot(), "folder", fid))
        items.sort(key=lambda item: (item[0].x(), item[0].y(), item[2]))
        placed: dict[tuple, list[QPoint]] = {}
        moved = False
        for pos, kind, key in items:
            area = self._area_at(pos + QPoint(8, 8))
            grid = nf.icon_grid(area)
            before = self._grid_before(area)
            if before is not None and not before.same_shape(grid):
                target = nf.move_to_grid(pos, before, grid)
            else:
                target = grid.pos_of(*grid.cell_at(pos))
            taken = placed.setdefault(self._area_key(area), [])
            target = snap_icon_pos(target, area, taken, grid=grid)
            taken.append(target)
            if kind == "note":
                w = self._stickies[key]
                if target != pos or w.size() != grid.cell:
                    w.move_icon(target)
                    moved = True
            else:
                icon = self._folder_icons[key]
                icon.place(target.x(), target.y())
                state = self._local.state.folder_icons.get(key)
                if state is not None and (state.x, state.y) != (target.x(), target.y()):
                    state.x, state.y = target.x(), target.y()
                    moved = True
                icon.update()
        self._remember_grids()
        if moved:
            self._local.save()

    def _remember_grids(self) -> None:
        """지금의 격자를 적어 둔다(다음에 격자가 바뀌었을 때 견줄 기준)."""
        saved = self._local.state.icon_grids
        changed = False
        for screen in QGuiApplication.screens():
            area = screen.availableGeometry()
            grid = nf.icon_grid(area)
            key = self._area_key(area)
            self._grids_used[key] = grid
            value = [grid.dx, grid.dy, grid.x0 - area.left(), grid.y0 - area.top()]
            name = ",".join(str(v) for v in key)
            if saved.get(name) != value:
                saved[name] = value
                changed = True
        if changed:
            self._local.save()

    def _folder_under(self, pos: QPoint) -> str:
        """그 점 아래에 있는 메모 폴더 아이콘의 폴더 id. 없으면 빈 글."""
        for fid, icon in self._folder_icons.items():
            if icon.isVisible() and icon.geometry().contains(pos):
                return fid
        return ""

    def _on_icon_dragged(self, note_id: str, pos: QPoint, dropped: bool) -> bool:
        """아이콘으로 접힌 메모를 끄는 동안과 놓았을 때. 메모 폴더 아이콘 위에
        놓으면 그 폴더로 옮긴다(받았으면 참)."""
        # 폴더 아이콘 위나, 열어 둔 폴더 창 위에 놓으면 그 폴더로 들어간다.
        target = self._folder_under(pos) or self._folder_under_window(pos)
        for fid, icon in self._folder_icons.items():
            icon.set_drop_hover(fid == target and not dropped)
        if not dropped or not target:
            return False
        note = self._notes.get(note_id) or {}
        if (note.get("folder_id") or None) != target:
            self._on_card_move_to_folder(note_id, target)
        # 함께 끌던 메모도 그 폴더로 들어간다.
        self._icon_ctl.dropped_on_folder(note_key(note_id), target)
        return True

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
        if note_id in self._peeked:
            return True
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
        if not shown and note_key(note_id) in self._icon_ctl.selection:
            self._icon_ctl.selection.toggle(note_key(note_id))

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
                icon.opened.connect(self.open_folder_window)
                icon.set_icon_controller(self._icon_ctl)
                icon.moved.connect(self._on_folder_icon_moved)
                icon.removeRequested.connect(self._remove_folder_icon)
                icon.deleteRequested.connect(self._delete_folder)
                icon.renamed.connect(self._on_folder_icon_renamed)
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
        self._icon_ctl.refresh()
        self._refresh_folder_windows()

    def _place_folder_icon(self, folder_id: str, at: QPoint | None = None,
                           quiet: bool = False) -> None:
        """폴더를 바탕 화면 아이콘으로 접는다. 그 폴더의 메모는 아이콘을 열 때까지
        바탕 화면에서 사라진다(메모 자체는 그대로다). ``at`` 을 주면 그 자리에서
        가장 가까운 빈 칸에 놓는다."""
        if folder_id not in self._folders:
            return
        if at is not None:
            avail = self._area_at(at)
            grid = nf.icon_grid(avail)
            # 누른 자리가 칸의 한가운데쯤에 오게 한다.
            start = at - QPoint(grid.cell.width() // 2, grid.cell.height() // 2)
        else:
            screen = QApplication.primaryScreen()
            if screen is None:
                return
            avail = screen.availableGeometry()
            grid = nf.icon_grid(avail)
            # Windows 바탕화면 아이콘은 보통 왼쪽에 모여 있다. 겹치지 않게 오른쪽
            # 위에서부터 놓는다.
            start = grid.pos_of(grid.cols() - 1, 0)
        pos = self._resolve_icon_pos("folder:" + folder_id, start, avail)
        self._local.state.folder_icons[folder_id] = FolderIconState(
            x=pos.x(), y=pos.y(), opened=False)
        self._local.save()
        self._refresh_folder_icons()
        for nid in self._folder_note_ids(folder_id):
            self._sync_note_shown(nid, animate=True)
        if not quiet:
            name = self._folders[folder_id].get("name") or ""
            self._status.showMessage(
                i18n.t("main.status_folder_icon_placed", folder=name), 5000)

    def _on_folder_icon_renamed(self, folder_id: str, name: str) -> None:
        """폴더 아이콘의 이름을 그 자리에서 고쳤다."""
        folder = self._folders.get(folder_id)
        if folder is None or not name or name == folder.get("name"):
            return
        if not self._require_online():
            self._refresh_folder_icons()        # 고치기 전의 이름으로 되돌린다
            return
        self._client.update_folder(folder_id, name=name)

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
        """같은 화면 같은 벽에 이미 붙어 있는 탭을 피해 자리를 잡는다. ``y`` 와
        ``height`` 는 벽을 따라 잰 자리와 길이다."""
        spans = [
            w.dock_span() for nid, w in self._stickies.items()
            if nid != note_id and w.isVisible() and w.dock_side() == side
            and w.dock_screen_rect() == screen_rect
        ]
        return free_dock_slot(y, height, screen_rect, spans, side)

    def _set_note_visibility(self, note_id: str, visible: bool) -> None:
        if not visible and note_id in self._peeked:
            # 폴더 창에서 꺼내 본 메모를 닫았다. 다시 폴더 안으로 들어갈 뿐이고,
            # 숨긴 메모로 바꾸지는 않는다.
            self._peeked.discard(note_id)
            self._sync_note_shown(note_id)
            return
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
        # '숨김 N' 의 수와, 숨긴 메모만 보는 중이면 목록도 다시 맞춘다.
        self._apply_search_filter()
        self._refresh_folder_icons()

    def _open_history_for(self, note_id: str) -> None:
        self._ensure_history_viewer()
        self._history_viewer.set_notes(list(self._notes.values()))
        self._history_viewer.select_note(note_id)
        self._history_viewer.show()
        self._history_viewer.raise_()
        self._client.get_history(None)

    def _refresh_status_text(self) -> None:
        """상태줄 오른쪽의 연결 상태, 역할, 닉네임, 접속 인원을 다시 적는다."""
        if self._connected and self._solo:
            state, text = "connected", i18n.t("main.state_solo")
        elif self._connected:
            state, text = "connected", i18n.t("main.state_connected")
        elif self._gate:
            state = "connecting"
            text = (i18n.t("main.state_locked") if self._gate == "password"
                    else i18n.t("main.state_held"))
        elif self._was_connected:
            state, text = "disconnected", i18n.t("main.state_disconnected")
        elif self._host_unreachable():
            state, text = "disconnected", i18n.t("main.state_unreachable")
        else:
            state, text = "connecting", i18n.t("main.state_connecting")
        detail = self._client.nickname()
        if self._connected and self._users and not self._solo:
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
        if not self._solo:
            self._add_tray_item(menu, svg_icon("users"),
                                i18n.t("main.tray_sessions"), self._open_sessions)
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
        self._keep_text_in_flight()
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
        confirm.notify(QApplication.activeModalWidget() or self,
                       i18n.t("main.msg_reset_connection_title"),
                       i18n.t("main.msg_reset_connection_body"))

    def is_solo(self) -> bool:
        return self._solo

    def _start_sharing(self) -> None:
        """혼자 쓰던 메모의 공유를 시작한다. 비밀번호를 걸지 묻고, 실제로 바꾸는
        일(다음부터 공유 PC 로 켜지게 적어 두고 다시 켠다)은 main.py 가 한다."""
        if not self._solo:
            return
        password = password_dialog.ask_share_start(
            QApplication.activeModalWidget() or self)
        if password is not None:
            self.shareRequested.emit(password)

    def notify(self, text: str) -> None:
        """화면 오른쪽 아래에 잠깐 알린다(창 밖의 코드가 쓴다)."""
        self._show_notification(text)

    def is_editing(self) -> bool:
        """지금 편집 중인 메모가 있는가."""
        return any(w._edit_mode for w in self._stickies.values())

    def prepare_quit(self) -> None:
        """프로그램이 끝나기 전에 창들이 해 둘 일. 여러 번 불러도 된다.

        Qt 는 종료 요청을 받으면 보이는 창을 하나씩 닫아 본다. 메모 창은 닫기를
        '숨기기'로 받으므로, 이 채비 없이 종료가 시작되면 메모 하나가 숨김으로
        바뀌고 종료는 취소된다."""
        self._force_quit = True
        self._gate_timer.stop()
        self._desktop_grid.stop()
        self._icon_ctl.stop()
        self._close_folder_windows()
        self._flush_pending_edits()
        self._teardown_stickies()
        self._toasts.close_all()
        if self._tray is not None:
            self._tray.hide()

    def _quit_app(self) -> None:
        self.prepare_quit()
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

