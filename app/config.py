import os
import sys
from pathlib import Path

from . import packaging

# 내부 식별자. 데이터 폴더(%APPDATA%\SyncNotes)와 자동 실행 값 이름의 원천이라
# 바꾸면 기존 메모가 보이지 않는다. 화면에 보이는 이름은 i18n 의 "app.name"(메밀메모).
APP_NAME = "SyncNotes"
# 앱 버전의 단일 출처. 자동 업데이트가 이 값을 GitHub 릴리스 태그와 비교한다.
# 메밀메모로 이름을 바꾸면서 1.0.0 부터 다시 센다(SyncNotes 는 1.0.7 까지였다).
APP_VERSION = "1.1.0"
# 닉네임의 길이 상한. 참여한 PC 마다 이 이름을 적어 두므로 끝없이 길면 안 된다.
MAX_NICKNAME_LENGTH = 64

# 포크해서 다른 곳에 배포하려면 이 두 값만 바꾼다.
UPDATE_REPO_OWNER = "LEESEUNGGYU8"
# 새 버전을 찾아볼 릴리스 저장소. 앞에서부터 차례로 보고, 더 새 버전이 있는
# 첫 저장소에서 받는다.
#
# 메밀메모는 소스와 릴리스를 한 저장소(MemilMemo)에 둔다. 1.0.7 은 옛 저장소
# (SyncNotes-releases)에서 받던 설치본을 여기로 넘겨주는 버전이었다.
#
# 소유 계정은 지금 계정 그대로 둔다. 아직 없는 계정 이름을 적어 두면 남이 그
# 이름을 만들어 설치 파일을 올릴 수 있다.
UPDATE_REPO_NAMES = ("MemilMemo",)

# 설치 파일 이름. 버전을 넣지 않아야 소개 페이지의 내려받기 주소
# (releases/latest/download/<이 이름>)가 버전마다 바뀌지 않는다.
INSTALLER_NAME = "MemilMemo-Setup.exe"


def _resource_root() -> Path:
    """런타임에 Resources 폴더가 위치하는 디렉토리를 반환.

    * 개발 모드: 프로젝트 루트(``app`` 패키지의 부모).
    * PyInstaller 번들 내부: 압축 해제 디렉토리(``sys._MEIPASS``).
      ``--add-data "Resources;Resources"``로 넣은 리소스가 여기 있다.
    """
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
    return Path(__file__).resolve().parent.parent


_PROJECT_ROOT = _resource_root()
RESOURCE_DIR = _PROJECT_ROOT / "Resources"
_FONT_DIR = RESOURCE_DIR / "Font" / "Pretendard"
FONT_PATH = _FONT_DIR / "PretendardVariable.ttf"
FONT_PATH_JP = _FONT_DIR / "PretendardJPVariable.ttf"
FONT_FALLBACK_FAMILY = "Pretendard"
ICON_PATH = RESOURCE_DIR / "Image" / "Icon.png"

# 메모 색. 앞의 여덟은 처음부터 있던 색이고, 뒤의 여덟은 1.0.4 에서 더했다. 고르는
# 자리에서는 여덟 칸씩 두 줄로 놓여, 더한 색이 비슷한 옛 색의 바로 아래에 온다
# (노랑 아래 크림, 검정 아래 남색). 옛 버전은 모르는 색도 그대로 칠한다.
COLORS = [
    "#FFE066",
    "#FFB3BA",
    "#BAFFC9",
    "#BAE1FF",
    "#E0BBE4",
    "#FFD9A6",
    "#F5F5F5",
    "#3A3A3A",
    "#FFF3B0",
    "#FFAD9B",
    "#D7F29B",
    "#A6E8E0",
    "#C4CBFF",
    "#E8D5BC",
    "#CFD8DC",
    "#2E4057",
]
COLOR_COLUMNS = 8
DEFAULT_COLOR = COLORS[0]

DEFAULT_HEARTBEAT_MS = 3000
DEFAULT_PORT = 5555
# 내부망에서 공유 PC 를 찾는 신호를 받는 UDP 포트(app/network/discovery.py).
# 공유에 쓰는 TCP 포트는 바꿀 수 있지만 이 번호는 모든 PC 가 같아야 한다.
DISCOVERY_PORT = 5555

DEFAULT_NOTE_WIDTH = 280
DEFAULT_NOTE_HEIGHT = 260
# 메모를 줄일 수 있는 한도. 머리 줄 단추와 글 한 줄이 들어가는 크기다(한 줄짜리
# 메모를 그만큼만 차지하게 둘 수 있다).
MIN_NOTE_WIDTH = 160
MIN_NOTE_HEIGHT = 84
# 편집하는 동안의 한도. 서식 줄과 글 두어 줄이 들어가야 해서, 이보다 작은 메모는
# 편집하는 동안만 잠시 넓혔다가 마치면 돌린다.
EDIT_MIN_WIDTH = 220
EDIT_MIN_HEIGHT = 150
# 첨부 시 이 폭을 넘는 이미지는 여기까지 줄여 저장한다. 1920 은 FHD 스크린샷이
# 손실 없이 들어가는 폭이다(2026-09 이전에는 520 이었고, 이미지 뷰어에서 크게 볼
# 수 있도록 올렸다). 메모 안 표시는 RichTextEdit 이 표시 크기로 따로 줄여 그린다.
MAX_IMAGE_WIDTH = 1920
FORMAT_BAR_HEIGHT = 36

TEXT_MARGINS = (18, 10, 18, 18)  # 좌, 상, 우, 하 (px)
HANDLE_BAR_HEIGHT = 32
# 메모 바탕을 가장 옅게 했을 때의 불투명도(%). 이보다 옅으면 바탕을 눌러도
# 창이 잡히지 않는다(완전히 투명한 곳은 Windows 가 누름을 아래 창으로 넘긴다).
MIN_NOTE_OPACITY = 20
RESIZE_GRIP_SIZE = 12
CORNER_RADIUS = 12

LOCK_IDLE_TIMEOUT_MS = 5 * 60 * 1000

# 지운 메모는 휴지통에 이만큼 두었다가 메모 파일에서 지운다.
TRASH_KEEP_DAYS = 30


def _real_profile() -> str | None:
    """이 Windows 계정의 실제 사용자 폴더(환경 변수와 무관)."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        userenv = ctypes.WinDLL("userenv", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        advapi32.OpenProcessToken.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
        userenv.GetUserProfileDirectoryW.argtypes = [
            wintypes.HANDLE, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        TOKEN_QUERY = 0x0008
        token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(
                kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)):
            return None
        try:
            size = wintypes.DWORD(0)
            userenv.GetUserProfileDirectoryW(token, None, ctypes.byref(size))
            buf = ctypes.create_unicode_buffer(max(size.value, 260))
            size = wintypes.DWORD(len(buf))
            if not userenv.GetUserProfileDirectoryW(token, buf, ctypes.byref(size)):
                return None
            return buf.value or None
        finally:
            kernel32.CloseHandle(token)
    except Exception:
        return None


def _real_appdata() -> str | None:
    """Windows 가 알려 주는 실제 Roaming 폴더(환경 변수와 무관)."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
        CSIDL_APPDATA = 0x001A
        if ctypes.windll.shell32.SHGetFolderPathW(None, CSIDL_APPDATA, None, 0, buf) == 0:
            return buf.value or None
    except Exception:
        pass
    # USERPROFILE 까지 다른 곳으로 돌려 띄우면 위의 물음이 실패한다(돌린 폴더에
    # AppData 가 없다). 계정의 실제 사용자 폴더에서 헤아린다.
    profile = _real_profile()
    return os.path.join(profile, "AppData", "Roaming") if profile else None


def isolated_run() -> bool:
    """APPDATA 나 USERPROFILE 을 다른 곳으로 돌려 띄운 실행인가(테스트, 시험 실행).

    그런 실행은 실제로 쓰고 있는 앱과 섞이면 안 된다. 같은 파이프 이름으로 듣지
    않고(ipc.ipc_name), 작업 표시줄 목록이나 우클릭 메뉴 같은 사용자 환경을 고쳐
    쓰지 않는다. 알 수 없을 때는 평소 실행으로 본다."""
    def norm(p: str) -> str:
        return os.path.normcase(os.path.normpath(p))

    env = os.environ.get("APPDATA")
    real = _real_appdata()
    if env and real and norm(env) != norm(real):
        return True
    home = os.environ.get("USERPROFILE")
    profile = _real_profile()
    return bool(home and profile and norm(home) != norm(profile))


def discovery_port() -> int | None:
    """공유 PC 가 찾는 신호를 받을 포트. None 이면 받지 않는다.

    시험으로 띄운 실행은 실제 포트를 쓰지 않는다(쓰고 있는 앱과 섞인다). 시험이
    환경 변수로 포트를 정해 준 때만 받는다."""
    if not isolated_run():
        return DISCOVERY_PORT
    try:
        port = int(os.environ.get("MEMIL_DISCOVERY_PORT", ""))
    except ValueError:
        return None
    return port if 1 <= port <= 65535 else None


def discovery_targets() -> list[tuple[str, int]] | None:
    """찾는 신호를 보낼 곳. None 이면 내부망 전체다.

    시험으로 띄운 실행은 내부망에 신호를 뿌리지 않는다. 환경 변수로 받은
    곳(``주소:포트,주소:포트``)에만 보내고, 없으면 아무 데도 보내지 않는다."""
    if not isolated_run():
        return None
    targets = []
    for item in os.environ.get("MEMIL_DISCOVERY_TARGETS", "").split(","):
        host, _, port = item.strip().rpartition(":")
        if host and port.isdigit():
            targets.append((host, int(port)))
    return targets


# 스토어판이 메모를 두는 폴더(사용자 폴더 바로 아래). 패키지 앱이 AppData 에
# 새로 만드는 파일은 앱 전용 자리로 돌려져 앱을 제거할 때 지워진다. 메모는
# 제거해도 남아야 하므로 AppData 밖에 둔다.
PACKAGED_DATA_FOLDER = "MemilMemo"


def classic_data_dir() -> Path:
    """설치판의 데이터 폴더. 폴더를 만들지 않는다."""
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / APP_NAME


def packaged_data_dir() -> Path:
    """스토어판의 데이터 폴더. 폴더를 만들지 않는다."""
    base = os.environ.get("USERPROFILE") or str(Path.home())
    return Path(base) / PACKAGED_DATA_FOLDER


def app_data_dir() -> Path:
    path = packaged_data_dir() if packaging.is_packaged() else classic_data_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_db_path() -> Path:
    return app_data_dir() / "notes.sqlite3"


def default_local_state_path() -> Path:
    return app_data_dir() / "local_state.json"


def default_settings_path() -> Path:
    return app_data_dir() / "settings.json"


def default_backup_dir() -> Path:
    return app_data_dir() / "backups"


def default_drafts_path() -> Path:
    """공유 PC 와 끊긴 동안 이 PC 에서 쓴 메모(참여한 PC). 다시 붙으면 올릴지 묻는다."""
    return app_data_dir() / "offline.sqlite3"


def default_replica_path() -> Path:
    """참여한 PC 에 두는 메모 사본. 호스트의 메모 파일(notes.sqlite3)과는 다른 파일이다."""
    return app_data_dir() / "replica.sqlite3"
