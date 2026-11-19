from __future__ import annotations

import json
import platform
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from . import config

IMAGE_VIEWER_BUILTIN = "builtin"
IMAGE_VIEWER_SYSTEM = "system"
IMAGE_VIEWER_CHOICES = (IMAGE_VIEWER_BUILTIN, IMAGE_VIEWER_SYSTEM)

# 메인 창 '최근 메모' 목록을 그리는 방식. 표시 밀도와 배치는 따로 고른다.
VIEW_COMPACT = "compact"
VIEW_STANDARD = "standard"
VIEW_DETAILED = "detailed"
VIEW_DENSITIES = (VIEW_COMPACT, VIEW_STANDARD, VIEW_DETAILED)
VIEW_LIST = "list"
VIEW_GRID = "grid"
VIEW_LAYOUTS = (VIEW_LIST, VIEW_GRID)


@dataclass
class AppSettings:
    # 빈 문자열 = 미선택 → 시작 시 OS 로케일을 감지해 적용한다.
    language_code: str = ""
    default_font_size: int = 11
    default_note_color: str = config.DEFAULT_COLOR
    show_tray_icon: bool = True
    close_main_to_tray: bool = True

    move_checked_to_bottom: bool = False

    # 메모 안 이미지를 더블 클릭했을 때 여는 방법.
    # "builtin" = 앱 자체 이미지 뷰어, "system" = OS 기본 이미지 뷰어(임시 파일 경유).
    image_viewer: str = IMAGE_VIEWER_BUILTIN

    notes_view_density: str = VIEW_STANDARD
    notes_view_layout: str = VIEW_LIST

    # 접고 펴는 그림을 움직임으로 보일지. 꺼도 접기 자체는 그대로 된다.
    fold_animation: bool = True
    # 접힌 메모의 기본 모양: "slip"(쪽지) 또는 "icon"(바탕화면 아이콘).
    fold_style: str = "slip"
    # 화면 가장자리에 붙인 메모(탭과 거기서 펼친 메모)를 다른 창 위에 둘지.
    dock_on_top: bool = True

    autostart: bool = False
    # 메인 창을 닫으면 트레이로 내려간다는 것을 한 번 알렸는가.
    tray_hint_shown: bool = False

    default_port: int = config.DEFAULT_PORT
    default_heartbeat_ms: int = config.DEFAULT_HEARTBEAT_MS

    # 다른 사람이 공개 메모를 새로 만들면 알린다.
    notify_new_note: bool = True
    # 새 메모를 '나만 보기'로 만든다. 꺼져 있으면 모두에게 공개한다.
    new_note_private: bool = False

    backup_enabled: bool = True
    # 자동 백업이 기본으로 꺼져 있던 때(1.0.7 까지)의 설정 파일에는 이 값이 없다.
    # 그런 파일을 처음 읽을 때 한 번 켜 준다. 그 뒤에 끈 것은 그대로 둔다.
    backup_default_applied: bool = True
    backup_dir: str = ""                 # 빈 문자열 = app_data_dir()/backups
    backup_interval_value: int = 24
    backup_interval_unit: str = "hours"  # "minutes" | "hours" | "days"
    backup_keep_count: int = 10

    @classmethod
    def load(cls, path: Path | None = None) -> "AppSettings":
        p = path or config.default_settings_path()
        if not p.exists():
            return cls()
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            known = {k: raw[k] for k in cls.__dataclass_fields__ if k in raw}
            loaded = cls(**known)
        except (json.JSONDecodeError, TypeError, ValueError):
            return cls()
        # 손으로 고친 설정 파일의 모르는 값은 기본값으로 되돌린다.
        if loaded.image_viewer not in IMAGE_VIEWER_CHOICES:
            loaded.image_viewer = IMAGE_VIEWER_BUILTIN
        if loaded.notes_view_density not in VIEW_DENSITIES:
            loaded.notes_view_density = VIEW_STANDARD
        if loaded.notes_view_layout not in VIEW_LAYOUTS:
            loaded.notes_view_layout = VIEW_LIST
        if loaded.fold_style not in ("slip", "icon"):
            loaded.fold_style = "slip"
        if "backup_default_applied" not in raw:
            loaded.backup_enabled = True
            loaded.backup_default_applied = True
        return loaded

    def save(self, path: Path | None = None) -> None:
        p = path or config.default_settings_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def resolved_backup_dir(self) -> Path:
        if self.backup_dir:
            return Path(self.backup_dir)
        return config.default_backup_dir()

    def backup_interval_seconds(self) -> int:
        v = max(1, int(self.backup_interval_value))
        unit = self.backup_interval_unit
        if unit == "minutes":
            return v * 60
        if unit == "days":
            return v * 24 * 3600
        return v * 3600


def _autostart_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    script = Path(__file__).resolve().parent.parent / "main.py"
    return f'"{sys.executable}" "{script}"'


def set_windows_autostart(enabled: bool) -> bool:
    if platform.system() != "Windows":
        return False
    try:
        import winreg
    except ImportError:
        return False
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_ALL_ACCESS
        ) as key:
            if enabled:
                winreg.SetValueEx(
                    key, config.APP_NAME, 0, winreg.REG_SZ, _autostart_command())
            else:
                try:
                    winreg.DeleteValue(key, config.APP_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


def get_windows_autostart() -> bool:
    if platform.system() != "Windows":
        return False
    try:
        import winreg
    except ImportError:
        return False
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            winreg.QueryValueEx(key, config.APP_NAME)
            return True
    except FileNotFoundError:
        return False
    except OSError:
        return False
