import os
import sys
from pathlib import Path

# 내부 식별자. 데이터 폴더(%APPDATA%\SyncNotes)와 자동 실행 값 이름의 원천이라
# 바꾸면 기존 메모가 보이지 않는다. 화면에 보이는 이름은 i18n 의 "app.name"(메밀메모).
APP_NAME = "SyncNotes"
# 앱 버전의 단일 출처. 자동 업데이트가 이 값을 GitHub 릴리스 태그와 비교한다.
# 메밀메모로 이름을 바꾸면서 1.0.0 부터 다시 센다(SyncNotes 는 1.0.7 까지였다).
APP_VERSION = "1.0.1"

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

COLORS = [
    "#FFE066",
    "#FFB3BA",
    "#BAFFC9",
    "#BAE1FF",
    "#E0BBE4",
    "#FFD9A6",
    "#F5F5F5",
    "#3A3A3A",
]
DEFAULT_COLOR = COLORS[0]

DEFAULT_HEARTBEAT_MS = 3000
DEFAULT_PORT = 5555

DEFAULT_NOTE_WIDTH = 280
DEFAULT_NOTE_HEIGHT = 260
MIN_NOTE_WIDTH = 220
MIN_NOTE_HEIGHT = 180
# 첨부 시 이 폭을 넘는 이미지는 여기까지 줄여 저장한다. 1920 은 FHD 스크린샷이
# 손실 없이 들어가는 폭이다(2026-09 이전에는 520 이었고, 이미지 뷰어에서 크게 볼
# 수 있도록 올렸다). 메모 안 표시는 RichTextEdit 이 표시 크기로 따로 줄여 그린다.
MAX_IMAGE_WIDTH = 1920
FORMAT_BAR_HEIGHT = 36

TEXT_MARGINS = (18, 10, 18, 18)  # 좌, 상, 우, 하 (px)
HANDLE_BAR_HEIGHT = 32
RESIZE_GRIP_SIZE = 12
CORNER_RADIUS = 12

LOCK_IDLE_TIMEOUT_MS = 5 * 60 * 1000


def _real_appdata() -> str | None:
    """Windows 가 알려 주는 실제 Roaming 폴더(환경 변수와 무관)."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
        CSIDL_APPDATA = 0x001A
        if ctypes.windll.shell32.SHGetFolderPathW(None, CSIDL_APPDATA, None, 0, buf) != 0:
            return None
        return buf.value or None
    except Exception:
        return None


def isolated_run() -> bool:
    """APPDATA 를 다른 곳으로 돌려 띄운 실행인가(테스트, 시험 실행).

    그런 실행은 실제로 쓰고 있는 앱과 섞이면 안 된다. 같은 파이프 이름으로 듣지
    않고(ipc.ipc_name), 작업 표시줄 목록이나 우클릭 메뉴 같은 사용자 환경을 고쳐
    쓰지 않는다. 알 수 없을 때는 평소 실행으로 본다."""
    env = os.environ.get("APPDATA")
    real = _real_appdata()
    if not env or not real:
        return False
    def norm(p: str) -> str:
        return os.path.normcase(os.path.normpath(p))
    return norm(env) != norm(real)


def app_data_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    path = Path(base) / APP_NAME
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


def default_replica_path() -> Path:
    """참여한 PC 에 두는 메모 사본. 호스트의 메모 파일(notes.sqlite3)과는 다른 파일이다."""
    return app_data_dir() / "replica.sqlite3"
