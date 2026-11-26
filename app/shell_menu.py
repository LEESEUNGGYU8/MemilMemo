"""Windows 우클릭 메뉴의 항목: 바탕 화면의 '새 메모 추가'와 '새 메모 폴더 추가',
파일과 폴더의 '새 메모에 링크로 넣기'.

현재 사용자(HKCU)에만 등록한다. 관리자 권한이 필요 없고, 설치 프로그램의
선택 항목과 앱 설정이 같은 키를 쓰므로 어느 쪽에서 켜고 꺼도 상태가 하나다.
켜져 있는지는 바탕 화면 항목으로 가린다. 파일과 폴더 항목은 그것을 따라간다.

Windows 11 은 레지스트리로 넣은 항목을 새 메뉴의 첫 화면이 아니라
'더 많은 옵션 표시' 안에 보인다(운영체제의 동작이라 앱에서 바꿀 수 없다).

스토어판(MSIX)은 레지스트리를 쓰지 않는다. 패키지에 든 메뉴 DLL
(shellext/memil_menu.cpp)이 항목을 만들고, 보일지와 메뉴의 글자는 데이터 폴더의
파일 하나(``MARKER_NAME``)로 정한다. 이 모듈의 함수들은 스토어판에서 그 파일을
쓰고 지운다."""
from __future__ import annotations

import platform
import sys
from pathlib import Path

from . import config, packaging
from .ipc import AT_CURSOR_FLAG, CLIP_NOTE_FLAG, LINK_NOTE_FLAG, NEW_FOLDER_FLAG, NEW_NOTE_FLAG

# 키 이름은 화면에 보이지 않는 내부 식별자다. 설치 프로그램(installer.iss)이
# 같은 경로를 쓰므로 함께 바꿔야 한다.
KEY_PATH = r"Software\Classes\DesktopBackground\Shell\SyncNotes.NewNote"
# 바탕 화면의 '새 메모 폴더 추가'. 메뉴는 키 이름의 차례로 놓이므로, 이 이름이어야
# '새 메모 추가' 바로 아래에 온다.
FOLDER_KEY_PATH = r"Software\Classes\DesktopBackground\Shell\SyncNotes.NewNoteFolder"
# '클립보드로 새 메모': 바탕 화면('새 메모 추가' 바로 아래)과 모든 파일.
CLIP_KEY_PATHS = (
    r"Software\Classes\DesktopBackground\Shell\SyncNotes.NewNoteClip",
    r"Software\Classes\*\shell\SyncNotes.ClipNote",
)
# 파일(모든 종류)과 폴더를 우클릭했을 때의 항목.
LINK_KEY_PATHS = (
    r"Software\Classes\*\shell\SyncNotes.LinkNote",
    r"Software\Classes\Directory\shell\SyncNotes.LinkNote",
)


# 스토어판의 메뉴 DLL 이 읽는 파일. 있으면 메뉴를 보이고, 첫 줄이 바탕 화면의
# '새 메모 추가', 둘째 줄이 파일과 폴더 항목, 셋째 줄이 '새 메모 폴더 추가', 넷째 줄이
# '클립보드로 새 메모'의 글자다(셋째, 넷째 줄은 나중에 생겼다. 줄의 차례를 바꾸지 않는다).
MARKER_NAME = "shell-menu.txt"


def _marker() -> Path:
    return config.packaged_data_dir() / MARKER_NAME


def _marker_text(label: str, link_label: str, folder_label: str = "",
                 clip_label: str = "") -> str:
    # 넷째 줄은 '클립보드로 새 메모'(나중에 생긴 줄이라 맨 뒤다. 줄의 차례를 바꾸지 않는다).
    def one(text: str) -> str:
        return " ".join(str(text).split())
    return f"{one(label)}\n{one(link_label)}\n{one(folder_label)}\n{one(clip_label)}\n"


def _read_marker() -> str | None:
    try:
        return _marker().read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _write_marker(label: str, link_label: str, folder_label: str = "",
                  clip_label: str = "") -> bool:
    if not label:
        return False
    path = _marker()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(path.name + ".part")
        part.write_text(_marker_text(label, link_label, folder_label, clip_label),
                        encoding="utf-8", newline="\n")
        part.replace(path)
        return True
    except OSError:
        return False


def _winreg():
    if platform.system() != "Windows":
        return None
    try:
        import winreg
    except ImportError:
        return None
    return winreg


def _launcher() -> str:
    """이 앱을 실행하는 명령의 앞부분. 개발 실행에서는 파이썬에 main.py 를 넘긴다."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    script = Path(__file__).resolve().parent.parent / "main.py"
    return f'"{sys.executable}" "{script}"'


def command_line() -> str:
    """바탕 화면 항목이 실행할 명령."""
    return f"{_launcher()} {NEW_NOTE_FLAG} {AT_CURSOR_FLAG}"


def folder_command_line() -> str:
    """바탕 화면의 '새 메모 폴더 추가'가 실행할 명령."""
    return f"{_launcher()} {NEW_FOLDER_FLAG} {AT_CURSOR_FLAG}"


def clip_command_line() -> str:
    """'클립보드로 새 메모'가 실행할 명령(우클릭한 자리에 만든다)."""
    return f"{_launcher()} {CLIP_NOTE_FLAG} {AT_CURSOR_FLAG}"


def link_command_line() -> str:
    """파일·폴더 항목이 실행할 명령. ``%1`` 자리에 우클릭한 경로가 들어온다."""
    return f'{_launcher()} {LINK_NOTE_FLAG} "%1"'


def icon_location() -> str:
    if getattr(sys, "frozen", False):
        return f"{sys.executable},0"
    return str(config.RESOURCE_DIR / "Image" / "Icon.ico")


def is_supported() -> bool:
    return packaging.is_packaged() or _winreg() is not None


# 스토어판의 바탕화면 '새 메모'는 이 빌드(Windows 11 22H2)부터 나온다.
_DESKTOP_VERB_BUILD = 22621


def desktop_item_available() -> bool:
    """바탕화면 우클릭 메뉴의 '새 메모'가 나오는 Windows 인가.

    설치판은 어디서나 나온다. 스토어판은 패키지가 메뉴를 넣는 방식이라, 그보다
    옛 Windows 에서는 파일과 폴더의 항목만 나온다."""
    if not packaging.is_packaged():
        return True
    try:
        return sys.getwindowsversion().build >= _DESKTOP_VERB_BUILD
    except Exception:
        return True


def is_registered() -> bool:
    if packaging.is_packaged():
        return _marker().is_file()
    winreg = _winreg()
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY_PATH + r"\command"):
            return True
    except OSError:
        return False


def _read_entry(path: str) -> tuple[str, str, str]:
    """(메뉴 글자, 아이콘, 명령). 없으면 빈 문자열."""
    winreg = _winreg()
    label = icon = command = ""
    if winreg is None:
        return label, icon, command
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            try:
                label = str(winreg.QueryValueEx(key, "MUIVerb")[0])
            except OSError:
                pass
            try:
                icon = str(winreg.QueryValueEx(key, "Icon")[0])
            except OSError:
                pass
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            path + r"\command") as key:
            command = str(winreg.QueryValueEx(key, "")[0])
    except OSError:
        pass
    return label, icon, command


def _read() -> tuple[str, str, str]:
    return _read_entry(KEY_PATH)


def _write_entry(path: str, label: str, command: str) -> bool:
    winreg = _winreg()
    if winreg is None or not label:
        return False
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0,
                                winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "MUIVerb", 0, winreg.REG_SZ, label)
            winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, icon_location())
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,
                                path + r"\command", 0,
                                winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command)
        return True
    except OSError:
        return False


def _delete_entry(path: str) -> bool:
    winreg = _winreg()
    if winreg is None:
        return False
    ok = True
    for sub in (path + r"\command", path):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)
        except FileNotFoundError:
            pass
        except OSError:
            ok = False
    return ok


def _entries(label: str, link_label: str, folder_label: str = "",
             clip_label: str = "") -> list[tuple[str, str, str]]:
    """넣을 항목마다 ``(키, 메뉴 글자, 명령)``."""
    wanted = [(KEY_PATH, label, command_line())]
    if folder_label:
        wanted.append((FOLDER_KEY_PATH, folder_label, folder_command_line()))
    if clip_label:
        wanted += [(path, clip_label, clip_command_line()) for path in CLIP_KEY_PATHS]
    if link_label:
        wanted += [(path, link_label, link_command_line())
                   for path in LINK_KEY_PATHS]
    return wanted


def register(label: str, link_label: str = "", folder_label: str = "",
             clip_label: str = "") -> bool:
    """메뉴 항목을 넣는다(이미 있으면 지금 값으로 고쳐 쓴다). ``link_label`` 은
    파일과 폴더 항목의, ``folder_label`` 은 '새 메모 폴더 추가'의, ``clip_label`` 은
    '클립보드로 새 메모'(바탕 화면과 파일)의 글자다."""
    if packaging.is_packaged():
        return _write_marker(label, link_label, folder_label, clip_label)
    if _winreg() is None or not label:
        return False
    ok = True
    for path, text, command in _entries(label, link_label, folder_label, clip_label):
        ok = _write_entry(path, text, command) and ok
    return ok


def unregister() -> bool:
    """메뉴 항목을 모두 뺀다. 원래 없었어도 True."""
    if packaging.is_packaged():
        try:
            _marker().unlink(missing_ok=True)
            return True
        except OSError:
            return False
    if _winreg() is None:
        return False
    ok = True
    for path in (KEY_PATH, FOLDER_KEY_PATH, *CLIP_KEY_PATHS, *LINK_KEY_PATHS):
        ok = _delete_entry(path) and ok
    return ok


def set_registered(enabled: bool, label: str, link_label: str = "",
                   folder_label: str = "", clip_label: str = "") -> bool:
    return (register(label, link_label, folder_label, clip_label) if enabled
            else unregister())


def refresh(label: str, link_label: str = "", folder_label: str = "",
            clip_label: str = "") -> None:
    """이미 넣어 둔 항목을 지금 실행 파일과 화면 언어에 맞춘다.

    설치 위치나 언어가 바뀐 뒤에도 메뉴가 옛 경로·옛 글자로 남지 않게 앱을
    켤 때 부른다. 바탕 화면 항목만 넣어 둔 옛 버전에서 올라왔으면 파일과 폴더
    항목도 이때 생긴다. 넣어 두지 않았으면 아무것도 하지 않는다. 설치본에서만
    고쳐 쓴다: 개발 실행이 설치본의 메뉴를 파이썬 경로로 바꿔 놓으면 안 된다."""
    if packaging.is_packaged():
        # 스토어판은 메뉴의 글자만 화면 언어에 맞춘다.
        wanted = _marker_text(label, link_label, folder_label, clip_label)
        if is_registered() and _read_marker() != wanted:
            _write_marker(label, link_label, folder_label, clip_label)
        return
    if (not getattr(sys, "frozen", False) or config.isolated_run()
            or not is_supported() or not is_registered()):
        return
    icon = icon_location()
    for path, text, command in _entries(label, link_label, folder_label, clip_label):
        if _read_entry(path) != (text, icon, command):
            _write_entry(path, text, command)
