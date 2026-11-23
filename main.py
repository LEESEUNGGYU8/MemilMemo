from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QTimer
from PySide6.QtGui import QCursor, QIcon
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from app import config, data_home, i18n, packaging, shell_menu
from app.backup import (
    BackupManager, backup_time, latest_good_backup, list_backups,
    replace_database, set_aside,
)
from app.database import SqliteRepository, check_database
from app.datetime_fmt import fmt_relative
from app.ipc import (
    CMD_NEW_NOTE_HERE, CMD_SHOW, IpcReceiver, claim_boot, forward_command,
    forward_when_ready, link_note_command, link_note_paths, new_folder_command,
    new_note_command,
)
from app.local_state import LocalStateStore
from app.network.discovery import Responder
from app.network.guest_client import GuestClient
from app.network.host_client import HostLocalClient
from app.network.host_server import HostServer
from app.offline_drafts import DraftStore
from app.replica import ReplicaStore, ReplicaSync, build_host_database
from app.settings import AppSettings, get_windows_autostart, set_windows_autostart
from app.share_auth import ShareAuth
from app.sync.history_tracker import HistoryTracker
from app.sync.lock_manager import LockManager
from app.ui import confirm, theme
from app.ui.connect_dialog import ConnectDialog, ConnectionChoice
from app.ui.main_window import MainWindow
from app.ui.restore_dialog import RestoreDialog
from app.ui.settings_dialog import SettingsDialog
from app.ui.update_dialog import UpdateAvailableDialog
from app.updater import (
    UpdateChecker, UpdateDownloader, launch_installer,
)
from app.win_jumplist import (
    refresh_app_name,
    register as register_jumplist,
    set_app_user_model_id,
)


def _sync_autostart(settings: AppSettings) -> None:
    if config.isolated_run():
        # 시험 실행은 실제 자동 실행 값을 읽지도 고치지도 않는다.
        return
    if packaging.is_packaged():
        # 스토어판의 자동 실행 상태는 Windows 가 쥐고 있다(사용자가 설정의 시작
        # 앱에서 바꿀 수 있다). 설정 파일의 값으로 덮어쓰지 않는다.
        return
    actual = get_windows_autostart()
    if settings.autostart and not actual:
        set_windows_autostart(True)
    elif not settings.autostart and actual:
        settings.autostart = True


def _saved_choice(local_store: LocalStateStore) -> ConnectionChoice | None:
    """저장해 둔 직전 접속 정보를 복원한다. 값이 있으면 다음 실행에서
    접속 다이얼로그를 건너뛰고 자동으로 접속한다."""
    ls = local_store.state
    if ls.last_mode == "host" and ls.nickname:
        return ConnectionChoice(
            mode="host",
            nickname=ls.nickname,
            port=ls.last_host_port or config.DEFAULT_PORT,
            heartbeat_ms=ls.last_heartbeat_ms or config.DEFAULT_HEARTBEAT_MS,
        )
    if ls.last_mode == "guest" and ls.nickname:
        return ConnectionChoice(
            mode="guest",
            nickname=ls.nickname,
            port=ls.last_host_port or config.DEFAULT_PORT,
            host_ip=ls.last_host_ip or "127.0.0.1",
        )
    return None


def _prompt_for_choice(local_store: LocalStateStore,
                       settings: AppSettings) -> ConnectionChoice | None:
    ls = local_store.state
    dlg = ConnectDialog(
        last_nickname=ls.nickname,
        last_ip=ls.last_host_ip,
        last_port=ls.last_host_port or settings.default_port,
    )
    if dlg.exec() != QDialog.Accepted:
        return None
    return dlg.choice()


def _persist_choice(local_store: LocalStateStore,
                    choice: ConnectionChoice) -> None:
    """현재 접속 정보를 저장한다. 다음 실행 때 자동 재접속에 쓰인다."""
    ls = local_store.state
    if choice.mode == "host":
        if choice.password is not None:
            # 접속 창에서 정했다. 비워 두었으면 비밀번호 없이 공유한다. 글자는
            # 적지 않고 거기서 만든 열쇠만 적어 둔다.
            auth = ShareAuth.from_password(choice.password)
            ls.share_auth = auth.to_dict() if auth is not None else None
    elif (ls.last_mode != "guest" or ls.last_host_ip != choice.host_ip
          or ls.last_host_port != choice.port):
        # 다른 공유 PC 에 참여한다. 전에 붙던 PC 의 열쇠(또는 이 PC 가 공유하며
        # 쓰던 것)는 그 PC 의 것이 아니다.
        ls.share_auth = None
    ls.last_mode = choice.mode
    ls.nickname = choice.nickname
    ls.last_host_port = choice.port
    if choice.mode == "host":
        ls.last_heartbeat_ms = choice.heartbeat_ms
    else:
        ls.last_host_ip = choice.host_ip
    local_store.save()


def _when(path: Path) -> str:
    """백업을 뜬 때를 화면에 적을 글로."""
    return fmt_relative(int(backup_time(path) * 1000))


def _open_repository(db_path: Path,
                     settings: AppSettings) -> SqliteRepository | None:
    """메모 파일을 연다. 상해서 열 수 없으면 백업으로 되살릴지 묻는다.

    돌려준 것이 None 이면 시작하지 않는다."""
    try:
        repo = SqliteRepository(db_path)
        if not repo.check():
            return repo
        repo.close()
    except sqlite3.OperationalError as e:
        # 잠겨 있다, 쓸 권한이 없다 같은 일. 파일이 상한 것이 아니므로 손대지 않는다.
        confirm.notify(
            None, i18n.t("boot.start_failed_title"),
            i18n.t("boot.db_open_failed", message=str(e)),
            icon=QMessageBox.Critical)
        return None
    except sqlite3.DatabaseError:
        pass
    return _recover_database(db_path, settings)


def _recover_database(db_path: Path,
                      settings: AppSettings) -> SqliteRepository | None:
    """상한 메모 파일을 옮겨 두고(지우지 않는다) 백업이나 빈 파일로 다시 시작한다."""
    title = i18n.t("boot.db_damaged_title")
    detail = i18n.t("boot.db_damaged_detail")
    backup = latest_good_backup(settings.resolved_backup_dir())
    try:
        if backup is not None:
            if not confirm.ask(
                    None, title,
                    i18n.t("boot.db_damaged_restore", when=_when(backup)),
                    detail=detail, action=i18n.t("restore.btn_restore"),
                    cancel=i18n.t("boot.btn_quit")):
                return None
            replace_database(db_path, backup)
        else:
            if not confirm.ask(
                    None, title, i18n.t("boot.db_damaged_no_backup"),
                    detail=detail, action=i18n.t("boot.btn_start_empty"),
                    cancel=i18n.t("boot.btn_quit"), danger=True):
                return None
            set_aside(db_path, "damaged")
        repo = SqliteRepository(db_path)
        # 백업을 뜬 뒤에 쓴 메모는 참여한 PC 의 사본에만 남아 있다. 그 사본이
        # 되살린 옛 상태를 따라 지워지지 않도록 다른 메모 묶음으로 보이게 한다.
        repo.renew_host_id()
        return repo
    except (OSError, sqlite3.Error) as e:
        confirm.notify(
            None, i18n.t("boot.restore_failed_title"),
            i18n.t("boot.restore_failed_body", message=str(e)),
            icon=QMessageBox.Critical)
        return None


WAIT_PID_FLAG = "--wait-pid"
# '새 메모'로 켜졌을 때 공유 PC 에 붙기를 기다리는 시간(ms). 넘으면 이 PC 에만 만든다.
NEW_NOTE_HOST_WAIT_MS = 4000


def _wait_for_previous_instance(argv: list[str]) -> None:
    """다시 시작한 경우, 앞선 프로세스가 완전히 끝날 때까지 기다린다.

    기다리지 않으면 앞선 프로세스가 아직 쥐고 있는 파이프 이름과 포트를 새
    프로세스가 잡지 못한다."""
    if WAIT_PID_FLAG not in argv or sys.platform != "win32":
        return
    try:
        pid = int(argv[argv.index(WAIT_PID_FLAG) + 1])
        import ctypes
        SYNCHRONIZE = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if handle:
            ctypes.windll.kernel32.WaitForSingleObject(handle, 15000)
            ctypes.windll.kernel32.CloseHandle(handle)
    except (ValueError, IndexError, OSError):
        pass


def _already_running(argv: list[str], has_command: bool) -> bool:
    """그냥 켰는데(바로가기를 한 번 더 눌렀다) 앱이 이미 떠 있는가. 그렇다면 떠
    있는 메인 창을 앞으로 부르고 True 를 돌려준다(이 실행은 끝난다).

    하나 더 뜨면 같은 닉네임으로 두 번 접속해 메모 창이 겹쳐 뜨고, 두 실행이 같은
    창 배치 파일을 번갈아 덮어쓴다. 공유 PC 라면 포트를 열지 못해 저장해 둔 접속
    정보까지 지운다. 역할을 바꿔 스스로 다시 켠 경우(--wait-pid)와 '새 메모' 같은
    명령으로 켜진 경우는 여기서 다루지 않는다."""
    if has_command or WAIT_PID_FLAG in argv:
        return False
    if forward_command(CMD_SHOW):
        return True
    if not claim_boot():
        # 다른 실행이 지금 켜지는 중이다. 뜨기를 기다렸다가 그 창을 부른다.
        forward_when_ready(CMD_SHOW)
        return True
    return False


def _restart_app(window: MainWindow) -> None:
    """프로그램을 끄고 같은 방식으로 다시 켠다(역할을 바꾼 뒤)."""
    import os

    from PySide6.QtCore import QProcess
    extra = [WAIT_PID_FLAG, str(os.getpid())]
    if getattr(sys, "frozen", False):
        program, arguments = sys.executable, extra
    else:
        program, arguments = sys.executable, [str(Path(__file__).resolve())] + extra
    QProcess.startDetached(program, arguments)
    window._quit_app()


def _open_replica() -> ReplicaStore | None:
    """이 PC 에 두는 메모 사본. 열지 못해도 앱은 그대로 쓴다(사본만 없다)."""
    try:
        return ReplicaStore(config.default_replica_path())
    except (OSError, sqlite3.Error):
        return None


def _open_drafts() -> DraftStore | None:
    """끊긴 동안 쓴 메모를 적어 두는 곳. 열지 못해도 앱은 그대로 쓴다(끊긴
    동안에는 전처럼 읽기만 한다)."""
    try:
        return DraftStore(config.default_drafts_path())
    except (OSError, sqlite3.Error):
        return None


def _prepare_process() -> None:
    """창을 만들기 전에 이 프로세스에 해 둘 일."""
    import os

    # Qt 는 그림을 읽기 전에 같은 이름에 '@2x' 를 붙인 파일이 있는지 찾아본다.
    # 메모 본문이 네트워크 경로의 그림을 가리키면 찾아보는 것만으로 그 서버에
    # 접속한다. 이 앱은 그런 이름의 그림을 쓰지 않으므로 찾아보지 않게 한다.
    os.environ.setdefault("QT_HIGHDPI_DISABLE_2X_IMAGE_LOADING", "1")
    if getattr(sys, "frozen", False):
        # 탐색기의 우클릭 메뉴로 켜지면 작업 폴더가 그 파일이 든 폴더다. 그 폴더의
        # 파일이 프로그램 이름으로 잘못 잡히지 않게 설치 폴더로 옮겨 둔다.
        try:
            os.chdir(os.path.dirname(os.path.abspath(sys.executable)))
        except OSError:
            pass


class _App(QApplication):
    """끝내 달라는 요청을 창들보다 먼저 듣는 QApplication.

    Qt 는 종료 요청을 받으면 보이는 창을 하나씩 닫아 본다. 메모 창은 닫기를
    '숨기기'로 받으므로, 채비 없이 요청이 오면 메모 하나가 숨김으로 바뀌고
    종료는 취소된다. 어디서 요청이 오든 창들이 먼저 채비하게 한다."""

    def __init__(self, argv: list[str]) -> None:
        super().__init__(argv)
        self._before_quit = None

    def set_before_quit(self, callback) -> None:
        self._before_quit = callback

    def event(self, e) -> bool:  # noqa: N802
        if e.type() == QEvent.Quit and self._before_quit is not None:
            try:
                self._before_quit()
            except Exception:
                pass
        return super().event(e)


def main() -> int:
    _prepare_process()
    _wait_for_previous_instance(sys.argv)
    # 작업 표시줄 목록이나 바탕 화면 우클릭 메뉴의 '새 메모'로 실행됐는가.
    new_note_cmd = new_note_command(sys.argv)

    # 이번 실행의 목적이 명령 전달뿐이면, 이미 떠 있는 인스턴스에 넘기고 바로 종료한다.
    # 실행 중인 인스턴스가 없으면 본 프로세스가 정상 부팅한 뒤 welcome 직후에 직접 처리한다.
    if new_note_cmd and forward_command(new_note_cmd):
        return 0

    # 파일·폴더 우클릭 메뉴의 '새 메모에 링크로 넣기'로 실행됐는가.
    link_paths = link_note_paths(sys.argv)
    if link_paths and forward_command(link_note_command(link_paths)):
        return 0
    # 바탕 화면 우클릭 메뉴의 '새 메모 폴더 추가'로 실행됐는가.
    new_folder_cmd = new_folder_command(sys.argv)
    if new_folder_cmd and forward_command(new_folder_cmd):
        return 0
    if _already_running(sys.argv,
                        bool(new_note_cmd or link_paths or new_folder_cmd)):
        return 0
    # 여기부터는 앱을 켠다. 파일을 여럿 골랐으면 같은 실행이 동시에 여러 개
    # 뜨므로 하나만 켜고, 나머지는 그쪽이 뜨기를 기다렸다가 넘기고 끝난다.
    if not claim_boot() and link_paths:
        forward_when_ready(link_note_command(link_paths))
        return 0

    # 창이 표시되기 전에 호출해야 작업 표시줄이 본 프로세스를 AppUserModelID로 그룹화하고,
    # 뒤이어 등록하는 점프리스트가 앱 아이콘에 제대로 붙는다.
    set_app_user_model_id()
    # 업데이트한 PC 의 작업 표시줄에 옛 파일 이름이 남지 않게 한다.
    refresh_app_name()

    app = _App(sys.argv)
    app.setApplicationName(config.APP_NAME)
    app.setOrganizationName(config.APP_NAME)
    app.setQuitOnLastWindowClosed(False)

    # 우클릭 메뉴로 켜진 경우 메모를 띄울 자리. 접속을 마칠 때쯤에는 마우스가
    # 다른 데 가 있으므로 지금 적어 둔다.
    new_note_spot = (QCursor.pos()
                     if (new_note_cmd == CMD_NEW_NOTE_HERE or link_paths
                         or new_folder_cmd)
                     else None)

    # 스토어판을 처음 켰으면 설치판이 쓰던 메모와 설정을 가져온다(설정을 읽기
    # 전에 한다). 설치판에서는 아무 일도 하지 않는다.
    data_home.adopt_classic_data()
    settings = AppSettings.load()

    # 언어를 가장 먼저 확정한다.
    # 폰트 family 선택(일본어→Pretendard JP)과 이후 만들어질 모든 위젯의 텍스트가 이 값에 의존하기 때문이다.
    # 최초 실행이면 OS 로케일을 감지해 그 결과를 설정에 저장해 둔다.
    language = i18n.resolve_language(settings.language_code)
    i18n.set_language(language)
    if not settings.language_code:
        settings.language_code = language

    font_family = theme.load_application_fonts(language)
    theme.apply_theme(app, font_family)
    app_icon = QIcon(str(config.ICON_PATH)) if config.ICON_PATH.exists() else QIcon()
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)

    _sync_autostart(settings)
    settings.save()
    # 바탕 화면 우클릭 메뉴를 넣어 둔 경우 실행 파일 경로와 글자를 지금 것으로 맞춘다.
    shell_menu.refresh(i18n.t("shell.new_note_title"),
                       i18n.t("shell.link_note_title"),
                       i18n.t("shell.new_folder_title"))

    local_store = LocalStateStore()

    backup_manager: BackupManager | None = None

    # 저장된 접속 정보가 있으면 다이얼로그 없이 자동 접속한다. 호스트로
    # 시작하다가 포트 바인딩에 실패하면 저장된 역할을 비우고 다시 시도한다.

    while True:
        choice = _saved_choice(local_store)
        if choice is None:
            choice = _prompt_for_choice(local_store, settings)
            if choice is None:
                return 0

        if choice.mode == "host":
            db_path = config.default_db_path()
            repo = _open_repository(db_path, settings)
            if repo is None:
                return 0
            locks = LockManager(idle_timeout_ms=config.LOCK_IDLE_TIMEOUT_MS)
            history = HistoryTracker(repo)
            if choice.password is not None:
                # 포트를 열기 전에 정해 둔다(저장은 아래 _persist_choice 가 한다).
                picked = ShareAuth.from_password(choice.password)
                local_store.state.share_auth = (
                    picked.to_dict() if picked is not None else None)
                choice.password = None
            server = HostServer(
                repo=repo, lock_manager=locks, history_tracker=history,
                host_nickname=choice.nickname, heartbeat_ms=choice.heartbeat_ms,
                auth=ShareAuth.from_dict(local_store.state.share_auth),
            )
            server.old_version_message = i18n.t("auth.old_version")
            if not server.start(choice.port):
                repo.close()
                confirm.notify(
                    None, i18n.t("boot.start_failed_title"),
                    i18n.t("boot.port_bind_failed", port=choice.port),
                    icon=QMessageBox.Critical)
                # 저장된 역할을 비워 다음 루프에서 다이얼로그가 다시 뜨게 한다.
                local_store.state.last_mode = ""
                local_store.save()
                continue
            _persist_choice(local_store, choice)
            # 내부망에서 공유 PC 를 찾는 신호에 답한다. 포트를 열지 못해도 공유는 된다.
            beacon = None
            if config.discovery_port() is not None:
                beacon = Responder(lambda: server.beacon(choice.port),
                                   config.discovery_port(), parent=app)
                beacon.start()
            client = HostLocalClient(server=server, nickname=choice.nickname)
            # 중요: client.start() 이전에 MainWindow를 먼저 만들어야 한다.
            # 그래야 초기 welcome(저장된 메모 포함)이 도착할 때 시그널 구독이 이미 연결돼 있다.
            # 참여하던 PC 가 사본으로 공유를 이어받았으면 끊긴 동안 쓴 메모가 남아
            # 있을 수 있다. 그때만 열어, 공유 PC 가 된 뒤에 올릴지 묻는다.
            host_drafts = (_open_drafts()
                           if config.default_drafts_path().exists() else None)
            window = MainWindow(client=client, local_state=local_store,
                                role="host", settings=settings, app_icon=app_icon,
                                drafts=host_drafts)
            client.start()
            backup_manager = BackupManager(db_path=db_path, settings=settings)

            def _on_backup_now():
                # 손으로 누른 백업은 성공이든 실패든 그 자리에서 알린다.
                errors: list[str] = []
                remember = errors.append
                backup_manager.backupFailed.connect(remember)
                try:
                    dest = backup_manager.run_backup()
                finally:
                    backup_manager.backupFailed.disconnect(remember)
                if dest is not None:
                    confirm.notify(
                        window, i18n.t("boot.backup_done_title"),
                        i18n.t("boot.backup_done_body", dest=dest))
                else:
                    confirm.notify(
                        window, i18n.t("boot.backup_failed_title"),
                        i18n.t("boot.backup_failed_body",
                               message=errors[-1] if errors else ""),
                        icon=QMessageBox.Warning)

            window.backupRequested.connect(_on_backup_now)
            # 때맞춰 도는 자동 백업의 실패는 창을 띄우지 않고 상태줄에 남긴다.
            backup_manager.backupFailed.connect(window.show_backup_failure)

            def _on_restore():
                # 설정 창에서 눌렀다. 아직 저장하지 않은 백업 폴더라도 거기서 찾는다
                # (다른 PC 에서 가져온 백업 폴더를 가리켜 되살리는 경우).
                parent = QApplication.activeModalWidget() or window
                folder = (parent.backup_folder()
                          if isinstance(parent, SettingsDialog)
                          else settings.resolved_backup_dir())
                backups = list_backups(folder)
                if not backups:
                    confirm.notify(parent, i18n.t("restore.window_title"),
                                   i18n.t("restore.empty"))
                    return
                picker = RestoreDialog(backups, parent)
                if picker.exec() != QDialog.Accepted:
                    return
                chosen = picker.selected()
                if chosen is None:
                    return
                if check_database(chosen):
                    confirm.notify(
                        parent, i18n.t("boot.restore_failed_title"),
                        i18n.t("restore.err_damaged"), icon=QMessageBox.Warning)
                    return
                if not confirm.ask(
                        parent, i18n.t("restore.window_title"),
                        i18n.t("restore.confirm_body", when=_when(chosen)),
                        detail=i18n.t("restore.confirm_detail"),
                        action=i18n.t("restore.btn_restore"), danger=True):
                    return
                try:
                    # 쓰던 글까지 담은 지금 상태를 먼저 남겨 둔 뒤에 바꾼다.
                    window._flush_pending_edits()
                    backup_manager.keep_current()
                    repo.restore_from(chosen)
                except (OSError, sqlite3.Error) as e:
                    confirm.notify(
                        parent, i18n.t("boot.restore_failed_title"),
                        i18n.t("boot.restore_failed_body", message=str(e)),
                        icon=QMessageBox.Warning)
                    return
                # 참가자 모두에게 되살린 내용을 다시 보낸다.
                server.resync_all()
                confirm.notify(
                    parent, i18n.t("boot.restore_done_title"),
                    i18n.t("boot.restore_done_body", when=_when(chosen)))

            window.restoreRequested.connect(_on_restore)

            def _shutdown():
                try:
                    window._flush_pending_edits()
                    # 모든 메모를 teardown 모드로 표시해 둔다.
                    # Windows 세션 종료 흐름에서 Qt가 각 창에 closeEvent를 보내더라도,
                    # hideRequested가 발화돼 local_state가 hidden으로 오염되는 것을 막는다.
                    window._teardown_stickies()
                except Exception:
                    pass
                if backup_manager is not None:
                    backup_manager.stop()
                if beacon is not None:
                    beacon.stop()
                server.stop()
                repo.close()
                if host_drafts is not None:
                    host_drafts.close()
                local_store.save()
                settings.save()

            app.aboutToQuit.connect(_shutdown)
            break
        else:  # guest
            _persist_choice(local_store, choice)
            client = GuestClient(choice.host_ip, choice.port, choice.nickname)
            # 붙던 공유 PC 의 비밀번호 열쇠. 있으면 묻지 않고 들어간다.
            client.set_auth(ShareAuth.from_dict(local_store.state.share_auth))
            # 받은 메모를 이 PC 에도 적어 둔다. 호스트가 꺼져 있으면 이것을 보여 준다.
            replica = _open_replica()
            # 끊긴 동안 이 PC 에서 쓴 메모. 다시 붙으면 올릴지 묻는다.
            drafts = _open_drafts()
            # 호스트와 같은 이유로 MainWindow를 먼저 만들어 시그널을 구독한 뒤 client.start()를 호출한다.
            window = MainWindow(client=client, local_state=local_store,
                                role="guest", settings=settings, app_icon=app_icon,
                                replica=replica, drafts=drafts)
            if replica is not None:
                # app 에 매달아 둔다(창보다 오래 살아 마지막 변경까지 적는다).
                ReplicaSync(replica, client,
                            address=f"{choice.host_ip}:{choice.port}", parent=app)

            def _on_takeover(copy_path: str, nickname=choice.nickname):
                # 공유 PC 를 다시 쓸 수 없을 때, 받아 둔 사본으로 이 PC 가 공유를 잇는다.
                parent = QApplication.activeModalWidget() or window
                if not confirm.ask(
                        parent, i18n.t("copies.btn_takeover"),
                        i18n.t("copies.takeover_body"),
                        detail=i18n.t("copies.takeover_detail"),
                        action=i18n.t("copies.takeover_action"), danger=True):
                    return
                try:
                    build_host_database(Path(copy_path), config.default_db_path(),
                                        nickname)
                except (OSError, sqlite3.Error, ValueError) as e:
                    confirm.notify(
                        parent, i18n.t("boot.start_failed_title"),
                        i18n.t("copies.takeover_failed", message=str(e)),
                        icon=QMessageBox.Critical)
                    return
                # 다음 실행부터 이 PC 가 공유 PC 다. 곧바로 다시 시작한다.
                local_store.state.last_mode = "host"
                local_store.state.last_host_port = settings.default_port
                local_store.save()
                _restart_app(window)

            window.takeoverRequested.connect(_on_takeover)
            client.start()

            def _shutdown_guest():
                try:
                    window._flush_pending_edits()
                    # 호스트의 _shutdown과 같은 이유로, 자동 실행이나 세션 종료 사이클에서 메모 가시성이 의도와 다르게 바뀌지 않게 한다.
                    window._teardown_stickies()
                except Exception:
                    pass
                client.stop()
                if replica is not None:
                    replica.close()
                if drafts is not None:
                    drafts.close()
                local_store.save()
                settings.save()

            app.aboutToQuit.connect(_shutdown_guest)
            break

    app.setQuitOnLastWindowClosed(True)
    app.set_before_quit(window.prepare_quit)
    window.show()

    # 작업 표시줄 점프리스트 명령을 위한 단일 인스턴스 IPC.
    # 사용자별 파이프를 먼저 잡은 프로세스가 이후의 '새 메모' 실행을 처리한다.
    # 동시에 떠 있는 다른 인스턴스는 바인딩만 실패한 채 평소대로 동작한다.
    ipc = IpcReceiver(app)
    if ipc.listen():
        ipc.commandReceived.connect(window.handle_ipc_command)

    # 점프리스트는 매 실행마다 다시 등록한다. 비용이 작고 멱등이며,
    # exe 경로를 sys.executable로 두면 frozen·dev 모두 자기 자신을 실행한다.
    register_jumplist(exe_path=sys.executable)

    # 본 프로세스가 '새 메모'로 직접 실행된 경우(전달할 인스턴스가 없었을 때),
    # welcome 스냅샷을 받은 뒤에 메모를 만든다.
    # 그 전에 호출하면 클라이언트가 아직 연결되지 않아 create_note가 무산된다.
    if new_note_cmd or link_paths:
        def _create_after_welcome(_data=None):
            try:
                client.welcomed.disconnect(_create_after_welcome)
            except (RuntimeError, TypeError):
                pass
            if link_paths:
                QTimer.singleShot(0, lambda: window.handle_link_note_request(
                    link_paths, new_note_spot))
            else:
                QTimer.singleShot(
                    0, lambda: window.handle_new_note_request(new_note_spot))
        client.welcomed.connect(_create_after_welcome)

        def _create_without_host():
            # 공유 PC 가 꺼져 있으면 welcome 이 오지 않는다. 잠깐 기다려 보고도
            # 붙지 못했으면 이 PC 에만 만든다(다시 붙으면 올릴지 묻는다).
            if not window._connected:
                _create_after_welcome()
        QTimer.singleShot(NEW_NOTE_HOST_WAIT_MS, _create_without_host)

    if new_folder_cmd:
        # '새 메모 폴더 추가'로 켜졌다. 폴더는 공유 PC 에 만들므로 붙은 뒤에 만든다.
        def _folder_after_welcome(_data=None):
            try:
                client.welcomed.disconnect(_folder_after_welcome)
            except (RuntimeError, TypeError):
                pass
            QTimer.singleShot(
                0, lambda: window.handle_new_folder_request(new_note_spot))
        client.welcomed.connect(_folder_after_welcome)

    # 자동 업데이트 확인은 시작 5초 후에 동작한다.
    # welcome 핸드셰이크와 UI가 안정된 뒤에 수행하려는 목적이다.
    # 네트워크 오류는 조용히 무시하고, 새 버전이 있을 때만 다이얼로그를 띄운다.
    _schedule_update_check(app, window, settings)

    return app.exec()


def _schedule_update_check(app: QApplication, window: MainWindow,
                           settings: AppSettings) -> None:
    if packaging.is_packaged():
        # 스토어판은 스토어가 올린다. 설치 파일을 받아 실행하지 않는다.
        return

    def _begin_check():
        checker = UpdateChecker(config.APP_VERSION, parent=app)

        def _on_available(info: dict):
            version = str(info.get("version") or "")
            if (settings.auto_update and version
                    and settings.auto_update_tried != version):
                _install_quietly(app, window, settings, info)
            else:
                # 자동 업데이트를 켜지 않았거나, 이 버전을 한 번 설치하려다
                # 끝내지 못했다(설치 허용 창에서 거절한 경우 등). 묻는다.
                _prompt_and_install(app, window, info)

        def _on_error(_msg: str):
            # 네트워크 오류는 흔하다(오프라인, rate-limit 등).
            # 사용자에게 노출하지 않는다.
            pass

        checker.updateAvailable.connect(_on_available)
        checker.upToDate.connect(lambda: None)
        checker.error.connect(_on_error)
        checker.check()

    QTimer.singleShot(5000, _begin_check)


# 자동 업데이트가 편집이 끝나기를 기다리며 다시 확인하는 간격.
_AUTO_UPDATE_RETRY_MS = 3000


def _install_quietly(app: QApplication, window: MainWindow,
                     settings: AppSettings, info: dict) -> None:
    """자동 업데이트: 묻지 않고 받아서 설치한다.

    받다가 실패하면 조용히 넘어가고 다음 실행 때 다시 본다. 설치 파일은 관리자
    권한으로 도므로 Windows 의 설치 허용 창은 그대로 뜬다(앱이 건너뛸 수 없다)."""
    version = str(info.get("version") or "")
    downloader = UpdateDownloader(info, parent=app)
    # 잠시 뒤 앱이 꺼지고 설치 허용 창이 뜬다. 까닭을 먼저 알린다.
    window.notify(i18n.t("boot.auto_update_started", version=version))

    def _launch(path: str) -> None:
        if window.is_editing():
            # 쓰던 글을 끊지 않는다. 편집이 끝나면 설치한다.
            QTimer.singleShot(_AUTO_UPDATE_RETRY_MS, lambda: _launch(path))
            return
        # 같은 버전을 실행할 때마다 되풀이해 설치하지 않도록 적어 둔다.
        settings.auto_update_tried = version
        settings.save()
        sha = downloader.verified_sha256
        # 여기서 스스로 끝내지 않는다. Windows 의 설치 허용 창에서 거절하면 설치는
        # 시작되지 않는데, 그때 앱까지 꺼져 있으면 안 된다. 설치가 실제로 시작되면
        # 설치 프로그램이 이 앱을 닫고, 끝난 뒤 새 버전을 다시 켠다.
        launch_installer(path, sha)

    downloader.installerReady.connect(_launch)
    downloader.error.connect(lambda _message: None)
    downloader.start()


def _prompt_and_install(app: QApplication, window: MainWindow,
                         info: dict) -> None:
    """업데이트 알림 다이얼로그를 띄운다.
    사용자가 수락하면 다운로드 → 무결성 검증 → 인스톨러 실행까지 진행한다."""
    dlg = UpdateAvailableDialog(
        current_version=config.APP_VERSION,
        info=info,
        parent=window,
    )
    if dlg.exec() != QDialog.Accepted:
        return

    progress = QMessageBox(window)
    progress.setIcon(QMessageBox.Information)
    progress.setWindowTitle(i18n.t("boot.update_downloading_title"))
    progress.setText(i18n.t("boot.update_downloading_body"))
    progress.setInformativeText(i18n.t("boot.update_progress", percent=0))
    cancel_btn = progress.addButton(i18n.t("common.btn_cancel"),
                                    QMessageBox.RejectRole)
    cancel_btn.setProperty("secondary", True)
    progress.setEscapeButton(cancel_btn)

    downloader = UpdateDownloader(info, parent=app)
    finished = {"done": False}

    def _on_progress(received: int, total: int):
        if total > 0:
            progress.setInformativeText(i18n.t(
                "boot.update_progress", percent=int(received * 100 / total)))

    def _on_closed(_result=None):
        # 취소 단추나 창 닫기. 받던 것을 멈춘다.
        if not finished["done"]:
            finished["done"] = True
            downloader.cancel()

    def _close_progress():
        finished["done"] = True
        progress.close()

    progress.finished.connect(_on_closed)
    downloader.downloadProgress.connect(_on_progress)
    progress.show()

    def _on_ready(path: str):
        _close_progress()
        if not launch_installer(path, downloader.verified_sha256):
            confirm.notify(
                window, i18n.t("boot.update_failed_title"),
                i18n.t("boot.update_failed_body",
                       message=i18n.t("update.err_no_checksum")),
                icon=QMessageBox.Warning)
            return
        # 여기서 스스로 끝내지 않는다(자동 업데이트와 같다). 설치가 시작되면
        # 설치 프로그램이 이 앱을 닫고, 설치 허용 창에서 거절하면 앱이 남는다.
        # 전에는 여기서 app.quit() 을 불렀는데, 메모 창이 떠 있으면 종료는
        # 취소되고 메모 하나만 숨김으로 바뀌었다.

    def _on_error(message: str):
        _close_progress()
        confirm.notify(
            window, i18n.t("boot.update_failed_title"),
            i18n.t("boot.update_failed_body", message=message),
            icon=QMessageBox.Warning)

    downloader.installerReady.connect(_on_ready)
    downloader.error.connect(_on_error)
    downloader.start()


if __name__ == "__main__":
    sys.exit(main())
