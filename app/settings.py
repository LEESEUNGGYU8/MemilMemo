from __future__ import annotations

import json
import platform
import re
import sys
from dataclasses import asdict, dataclass, field

from . import hotkeys as _hotkeys
from pathlib import Path

from . import config, packaging, startup_task

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
# 폴더 창은 탐색기처럼 아이콘으로도 보인다.
VIEW_ICONS = "icons"
FOLDER_VIEW_LAYOUTS = (VIEW_ICONS, VIEW_LIST, VIEW_GRID)

# 메모가 접히고 펴지고 미끄러지는 움직임의 빠르기(배). 1 이 기본이고 2 는 두 배 빠르다.
ANIMATION_SPEED_MIN = 0.5
ANIMATION_SPEED_MAX = 2.0
ANIMATION_SPEED_STEP = 0.1

_HEX_COLOR = re.compile(r"#[0-9A-Fa-f]{6}")


def clamp_animation_speed(value) -> float:
    """설정 파일이나 입력 칸의 값을 쓸 수 있는 빠르기로 맞춘다(0.1 단위)."""
    try:
        speed = float(value)
    except (TypeError, ValueError):
        return 1.0
    if speed != speed:                      # NaN
        return 1.0
    speed = max(ANIMATION_SPEED_MIN, min(ANIMATION_SPEED_MAX, speed))
    return round(speed, 1)


def clamp_transparency(value) -> int:
    """투명도(%)를 0 ~ 80 의 5 단위로 맞춘다. 모르는 값은 0(불투명)."""
    try:
        number = int(round(float(value) / 5.0)) * 5
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, min(100 - config.MIN_NOTE_OPACITY, number))


@dataclass
class AppSettings:
    # 빈 문자열 = 미선택 → 시작 시 OS 로케일을 감지해 적용한다.
    language_code: str = ""
    default_font_size: int = 11
    default_note_color: str = config.DEFAULT_COLOR
    show_tray_icon: bool = True
    close_main_to_tray: bool = True

    move_checked_to_bottom: bool = False
    # 메모 글을 메모 폭에서 자동으로 줄바꿈한다(메모마다 더 보기 메뉴에서 바꿀 수 있다).
    word_wrap: bool = True

    # 메모 안 이미지를 더블 클릭했을 때 여는 방법.
    # "builtin" = 앱 자체 이미지 뷰어, "system" = OS 기본 이미지 뷰어(임시 파일 경유).
    image_viewer: str = IMAGE_VIEWER_BUILTIN

    notes_view_density: str = VIEW_STANDARD
    notes_view_layout: str = VIEW_LIST
    # 폴더 아이콘을 열면 뜨는 창의 보기 방식(아이콘, 리스트, 그리드)과 밀도.
    folder_view_layout: str = VIEW_ICONS
    folder_view_density: str = VIEW_STANDARD
    # 휴지통 창의 보기 방식. 지운 메모가 많을 때 한눈에 보이게 고른다.
    trash_view_layout: str = VIEW_LIST
    trash_view_density: str = VIEW_STANDARD

    # 접고 펴는 그림을 움직임으로 보일지. 꺼도 접기 자체는 그대로 된다.
    fold_animation: bool = True
    # 그 움직임의 빠르기(0.5 ~ 2.0 배).
    animation_speed: float = 1.0
    # 접힌 메모의 기본 모양: "slip"(쪽지) 또는 "icon"(바탕화면 아이콘).
    fold_style: str = "slip"
    # 메모 바탕의 기본 투명도(%, 0 ~ 80). 메모마다 더 보기 메뉴에서 따로 정할 수 있다.
    note_transparency: int = 0
    # 마우스를 올리면 투명한 메모도 진하게 보인다.
    opacity_hover_solid: bool = True
    # 화면 가장자리에 붙인 메모(탭과 거기서 펼친 메모)를 다른 창 위에 둘지.
    dock_on_top: bool = True

    autostart: bool = False
    # 메인 창을 닫으면 트레이로 내려간다는 것을 한 번 알렸는가.
    tray_hint_shown: bool = False

    default_port: int = config.DEFAULT_PORT
    default_heartbeat_ms: int = config.DEFAULT_HEARTBEAT_MS

    # 켜 두면 실행할 때 새 버전을 묻지 않고 받아 설치한다.
    auto_update: bool = False
    # 자동으로 설치를 시작해 본 버전. 설치가 끝나지 않아 같은 버전이 다시 보이면
    # (Windows 의 설치 허용 창에서 거절한 경우 등) 그때는 묻는 창으로 돌아간다.
    auto_update_tried: str = ""

    # 다른 사람이 공개 메모를 새로 만들면 알린다.
    notify_new_note: bool = True
    # 다른 사람이 메모를 고치고 편집을 마치면 알린다.
    notify_note_edit: bool = True
    # 리마인더가 울릴 때 Windows 알림 소리를 낸다.
    reminder_sound: bool = True
    # 공지를 읽었다는 것을 만든 사람에게 알리지 않는다.
    hide_read_receipts: bool = False
    # 메모 안 위젯 가운데 미니게임(오목)을 넣을 수 있게 한다.
    widget_games: bool = True
    # 메모를 다른 프로그램의 창에 붙이기(실험, 기본 꺼짐).
    window_attach: bool = False
    # 파일을 첨부할 때 종류마다 고른 방식({종류: 방식}). 없는 종류는 기본 방식.
    attach_modes: dict = field(default_factory=dict)
    # 인쇄와 PDF 를 메모 색 그대로 찍는다(꺼져 있으면 흰 바탕).
    print_keep_color: bool = False
    # 폴더를 인쇄할 때 메모마다 새 쪽에서 시작한다(꺼져 있으면 이어서).
    print_one_per_page: bool = True
    # 어디서나 쓰는 단축키: {일: {"key": 키보드 조합, "mouse": 마우스 조합}}.
    hotkeys: dict = field(default_factory=lambda: _hotkeys.clean_bindings(None))
    # 새 메모를 '나만 보기'로 만든다. 꺼져 있으면 모두에게 공개한다.
    new_note_private: bool = False

    # 글자 색과 메모 색의 '다른 색'에서 고른 색들(방금 고른 것부터, "#RRGGBB").
    recent_font_colors: list = field(default_factory=list)
    recent_note_colors: list = field(default_factory=list)

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
        if loaded.folder_view_layout not in FOLDER_VIEW_LAYOUTS:
            loaded.folder_view_layout = VIEW_ICONS
        if loaded.folder_view_density not in VIEW_DENSITIES:
            loaded.folder_view_density = VIEW_STANDARD
        if loaded.trash_view_layout not in FOLDER_VIEW_LAYOUTS:
            loaded.trash_view_layout = VIEW_LIST
        if loaded.trash_view_density not in VIEW_DENSITIES:
            loaded.trash_view_density = VIEW_STANDARD
        loaded.note_transparency = clamp_transparency(loaded.note_transparency)
        if loaded.fold_style not in ("slip", "icon"):
            loaded.fold_style = "slip"
        loaded.hotkeys = _hotkeys.clean_bindings(loaded.hotkeys)
        if not isinstance(loaded.attach_modes, dict):
            loaded.attach_modes = {}
        if isinstance(loaded.animation_speed, bool):
            loaded.animation_speed = 1.0
        loaded.animation_speed = clamp_animation_speed(loaded.animation_speed)
        for name in ("recent_font_colors", "recent_note_colors"):
            recents = getattr(loaded, name)
            setattr(loaded, name, [
                value.upper()
                for value in (recents if isinstance(recents, list) else [])
                if isinstance(value, str) and _HEX_COLOR.fullmatch(value)][:10])
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


def autostart_supported() -> bool:
    """이 앱이 자동 실행을 켜고 끌 수 있는가.

    설치판은 레지스트리의 시작 프로그램 값으로 한다. 스토어판이 거기에 적는
    값은 Windows 에 보이지 않으므로 패키지에 적어 둔 시작 작업으로 한다
    (``startup_task``). 사용자가 Windows 설정의 시작 앱에서 꺼 둔 것은 앱이
    다시 켤 수 없다."""
    if platform.system() != "Windows":
        return False
    if packaging.is_packaged():
        return startup_task.can_change()
    return True


def set_windows_autostart(enabled: bool) -> bool:
    if packaging.is_packaged():
        return startup_task.set_enabled(enabled)
    if not autostart_supported():
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
    if packaging.is_packaged():
        return startup_task.is_enabled()
    if not autostart_supported():
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
