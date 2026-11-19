"""바탕 화면 우클릭 메뉴의 '새 메모' 항목.

현재 사용자(HKCU)에만 등록한다. 관리자 권한이 필요 없고, 설치 프로그램의
선택 항목과 앱 설정이 같은 키를 쓰므로 어느 쪽에서 켜고 꺼도 상태가 하나다.

Windows 11 은 레지스트리로 넣은 항목을 새 메뉴의 첫 화면이 아니라
'더 많은 옵션 표시' 안에 보인다(운영체제의 동작이라 앱에서 바꿀 수 없다)."""
from __future__ import annotations

import platform
import sys
from pathlib import Path

from . import config
from .ipc import AT_CURSOR_FLAG, NEW_NOTE_FLAG

# 키 이름은 화면에 보이지 않는 내부 식별자다. 설치 프로그램(installer.iss)이
# 같은 경로를 쓰므로 함께 바꿔야 한다.
KEY_PATH = r"Software\Classes\DesktopBackground\Shell\SyncNotes.NewNote"


def _winreg():
    if platform.system() != "Windows":
        return None
    try:
        import winreg
    except ImportError:
        return None
    return winreg


def command_line() -> str:
    """메뉴 항목이 실행할 명령. 개발 실행에서는 파이썬에 main.py 를 넘긴다."""
    flags = f"{NEW_NOTE_FLAG} {AT_CURSOR_FLAG}"
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" {flags}'
    script = Path(__file__).resolve().parent.parent / "main.py"
    return f'"{sys.executable}" "{script}" {flags}'


def icon_location() -> str:
    if getattr(sys, "frozen", False):
        return f"{sys.executable},0"
    return str(config.RESOURCE_DIR / "Image" / "Icon.ico")


def is_supported() -> bool:
    return _winreg() is not None


def is_registered() -> bool:
    winreg = _winreg()
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY_PATH + r"\command"):
            return True
    except OSError:
        return False


def _read() -> tuple[str, str, str]:
    """(메뉴 글자, 아이콘, 명령). 없으면 빈 문자열."""
    winreg = _winreg()
    label = icon = command = ""
    if winreg is None:
        return label, icon, command
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY_PATH) as key:
            try:
                label = str(winreg.QueryValueEx(key, "MUIVerb")[0])
            except OSError:
                pass
            try:
                icon = str(winreg.QueryValueEx(key, "Icon")[0])
            except OSError:
                pass
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            KEY_PATH + r"\command") as key:
            command = str(winreg.QueryValueEx(key, "")[0])
    except OSError:
        pass
    return label, icon, command


def register(label: str) -> bool:
    """메뉴 항목을 넣는다(이미 있으면 지금 값으로 고쳐 쓴다)."""
    winreg = _winreg()
    if winreg is None or not label:
        return False
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, KEY_PATH, 0,
                                winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "MUIVerb", 0, winreg.REG_SZ, label)
            winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, icon_location())
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,
                                KEY_PATH + r"\command", 0,
                                winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command_line())
        return True
    except OSError:
        return False


def unregister() -> bool:
    """메뉴 항목을 뺀다. 원래 없었어도 True."""
    winreg = _winreg()
    if winreg is None:
        return False
    ok = True
    for path in (KEY_PATH + r"\command", KEY_PATH):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
        except FileNotFoundError:
            pass
        except OSError:
            ok = False
    return ok


def set_registered(enabled: bool, label: str) -> bool:
    return register(label) if enabled else unregister()


def refresh(label: str) -> None:
    """이미 넣어 둔 항목을 지금 실행 파일과 화면 언어에 맞춘다.

    설치 위치나 언어가 바뀐 뒤에도 메뉴가 옛 경로·옛 글자로 남지 않게 앱을
    켤 때 부른다. 넣어 두지 않았으면 아무것도 하지 않는다. 설치본에서만
    고쳐 쓴다: 개발 실행이 설치본의 메뉴를 파이썬 경로로 바꿔 놓으면 안 된다."""
    if (not getattr(sys, "frozen", False) or config.isolated_run()
            or not is_registered()):
        return
    if _read() != (label, icon_location(), command_line()):
        register(label)
