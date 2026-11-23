"""스토어판(MSIX 패키지)일 때의 동작. 패키지 안에서 도는 것처럼 꾸며 본다(offscreen).

패키지 앱은 AppData 와 HKCU 에 쓰는 것이 앱 전용 자리로 돌려진다. 그래서
스토어판은 메모를 AppData 밖에 두고, 레지스트리에 기대는 기능을 쓰지 않는다.
여기서는 그 분기가 맞게 갈리는지, 그리고 설치판의 동작이 그대로인지를 본다.
Windows 가 실제로 돌려 주는 동작은 패키지를 설치해야 볼 수 있다."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from app import (  # noqa: E402
    config,
    data_home,
    i18n,
    image_export,
    packaging,
    shell_menu,
    startup_task,
    win_jumplist,
)
from app import settings as settings_mod  # noqa: E402
from app.database import SqliteRepository, check_database  # noqa: E402
from app.models import Note  # noqa: E402
from app.replica import ReplicaStore  # noqa: E402
from app.settings import AppSettings  # noqa: E402
from app.ui import theme  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    i18n.set_language("ko")
    theme.apply_theme(app, "")
    yield app


@pytest.fixture
def places(tmp_path, monkeypatch):
    """설치판의 폴더(APPDATA 아래)와 스토어판의 폴더(사용자 폴더 아래)."""
    roaming = tmp_path / "Roaming"
    profile = tmp_path / "Profile"
    temp = tmp_path / "Temp"
    for folder in (roaming, profile, temp):
        folder.mkdir()
    monkeypatch.setenv("APPDATA", str(roaming))
    monkeypatch.setenv("USERPROFILE", str(profile))
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    return roaming / config.APP_NAME, profile / config.PACKAGED_DATA_FOLDER


@pytest.fixture
def store(monkeypatch):
    """패키지 안에서 도는 것처럼 꾸민다."""
    monkeypatch.setattr(packaging, "is_packaged", lambda: True)


def _tree(folder: Path) -> dict[str, str]:
    """폴더 안 파일들의 이름과 내용 해시."""
    return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*")) if p.is_file()}


def _fill_classic(old: Path, count: int = 3) -> list[str]:
    old.mkdir(parents=True, exist_ok=True)
    repo = SqliteRepository(old / "notes.sqlite3")
    ids = []
    for i in range(count):
        note = Note.new(user="지우")
        note.content = f"메모 {i}"
        repo.insert_note(note)
        ids.append(note.id)
    repo.close()
    replica = ReplicaStore(old / "replica.sqlite3")
    replica.apply_snapshot(
        [{"id": "r1", "version": 1, "content": "받아 둔 메모", "updated_at": 1}],
        [], host_id="h1", host="공유 PC", address="10.0.0.2:5555", nickname="지우")
    replica.close()
    (old / "settings.json").write_text(
        json.dumps({"language_code": "ko", "default_font_size": 13}),
        encoding="utf-8")
    (old / "local_state.json").write_text(
        json.dumps({"nickname": "지우", "last_mode": "host", "notes": {
            ids[0]: {"x": 40, "y": 50, "w": 0, "h": 0, "hidden": False}},
            "users": {}}), encoding="utf-8")
    return ids


# ── 판별 ────────────────────────────────────────────────────────────────

def test_this_run_is_not_packaged():
    assert packaging.package_full_name() == "" and not packaging.is_packaged()


def test_classic_edition_keeps_its_folder(places):
    old, new = places
    assert config.app_data_dir() == old
    assert config.default_db_path() == old / "notes.sqlite3"
    assert not new.exists()
    assert image_export.temp_image_dir() == (
        Path(tempfile.gettempdir()) / config.APP_NAME / "images")


# ── 저장 위치 ───────────────────────────────────────────────────────────

def test_store_edition_keeps_everything_outside_appdata(places, store):
    old, new = places
    paths = [config.app_data_dir(), config.default_db_path(),
             config.default_local_state_path(), config.default_settings_path(),
             config.default_backup_dir(), config.default_replica_path(),
             AppSettings().resolved_backup_dir(),
             # 다른 프로그램(그림 뷰어)에 넘기는 파일도 돌려지지 않는 곳에 둔다.
             image_export.temp_image_dir()]
    roaming = old.parent
    temp = Path(tempfile.gettempdir())
    for path in paths:
        assert new == path or new in path.parents, path
        assert roaming not in path.parents and temp not in path.parents, path
    assert new.is_dir() and not old.exists()


# ── 설치판의 메모 가져오기 ──────────────────────────────────────────────

def test_first_store_run_adopts_the_classic_data(places, store):
    old, new = places
    ids = _fill_classic(old)
    before = _tree(old)

    assert sorted(data_home.adopt_classic_data()) == [
        "local_state.json", "notes.sqlite3", "replica.sqlite3", "settings.json"]
    # 옛 폴더는 그대로다(지우지도 고치지도 않는다).
    assert _tree(old) == before

    assert check_database(new / "notes.sqlite3") == ""
    repo = SqliteRepository(new / "notes.sqlite3")
    try:
        got = {n.id: n.content for n in repo.list_notes()}
    finally:
        repo.close()
    assert got == {nid: f"메모 {i}" for i, nid in enumerate(ids)}
    replica = ReplicaStore(new / "replica.sqlite3")
    try:
        assert replica.note_ids() == {"r1"}
        assert replica.meta().get("host_id") == "h1"
    finally:
        replica.close()
    for name in ("settings.json", "local_state.json"):
        assert (new / name).read_bytes() == (old / name).read_bytes()
    assert not (new / "adopt.tmp").exists()


def test_adoption_happens_once_and_never_overwrites(places, store):
    old, new = places
    _fill_classic(old)
    assert data_home.adopt_classic_data()
    (new / "settings.json").write_text('{"language_code": "en"}', encoding="utf-8")
    kept = _tree(new)
    assert data_home.adopt_classic_data() == []
    assert _tree(new) == kept


def test_adoption_skips_a_folder_that_is_already_in_use(places, store):
    old, new = places
    _fill_classic(old)
    new.mkdir(parents=True)
    (new / "local_state.json").write_text("{}", encoding="utf-8")
    assert data_home.adopt_classic_data() == []
    assert _tree(new) == {"local_state.json": hashlib.sha256(b"{}").hexdigest()}


def test_adoption_leaves_out_a_damaged_note_file(places, store):
    old, new = places
    _fill_classic(old)
    raw = bytearray((old / "notes.sqlite3").read_bytes())
    raw[100:4000] = b"\xff" * 3900          # 첫 쪽을 망가뜨린다
    (old / "notes.sqlite3").write_bytes(bytes(raw))
    before = _tree(old)

    got = data_home.adopt_classic_data()
    assert "notes.sqlite3" not in got and "local_state.json" in got
    assert not (new / "notes.sqlite3").exists()
    assert _tree(old) == before


def test_adoption_clears_a_half_done_attempt(places, store):
    old, new = places
    _fill_classic(old)
    (new / "adopt.tmp").mkdir(parents=True)
    (new / "adopt.tmp" / "notes.sqlite3").write_bytes(b"half")
    assert "notes.sqlite3" in data_home.adopt_classic_data()
    assert check_database(new / "notes.sqlite3") == ""
    assert not (new / "adopt.tmp").exists()


def test_adoption_does_not_touch_a_classic_file_that_was_left_open(places, store):
    """설치판이 꺼지지 않고 죽었다(전원이 나갔다). 사본 파일 옆에 아직 합쳐지지
    않은 기록(-wal)이 남아 있다. 그 기록까지 가져오되, 옛 폴더는 건드리지 않는다."""
    import shutil
    import sqlite3
    old, new = places
    _fill_classic(old)
    # 사본에 메모를 하나 더 쓰고, 닫기 전의 파일들을 그대로 떠 둔다(죽은 모습).
    live = ReplicaStore(old / "replica.sqlite3")
    live.upsert_note({"id": "r2", "version": 1, "content": "죽기 직전에 받은 메모",
                      "updated_at": 2})
    crashed = old.parent / "crashed"
    crashed.mkdir()
    for path in old.glob("replica.sqlite3*"):
        shutil.copyfile(path, crashed / path.name)
    live.close()
    for path in old.glob("replica.sqlite3*"):
        path.unlink()
    for path in crashed.iterdir():
        shutil.copyfile(path, old / path.name)
    assert (old / "replica.sqlite3-wal").stat().st_size > 0
    before = _tree(old)

    assert "replica.sqlite3" in data_home.adopt_classic_data()

    assert _tree(old) == before              # 합치지도, 파일을 만들지도 않았다
    conn = sqlite3.connect(str(new / "replica.sqlite3"))
    try:
        assert {r[0] for r in conn.execute("SELECT id FROM notes")} == {"r1", "r2"}
    finally:
        conn.close()
    assert not list(new.glob("raw-*")) and not (new / "adopt.tmp").exists()


def test_adoption_brings_notes_written_offline(places, store):
    from app.offline_drafts import DraftStore
    old, new = places
    _fill_classic(old)
    drafts = DraftStore(old / "offline.sqlite3")
    drafts.save_new({"id": "d1", "content": "끊긴 동안 쓴 글"}, address="10.0.0.2:5555",
                    nickname="지우")
    drafts.close()

    assert "offline.sqlite3" in data_home.adopt_classic_data()

    got = DraftStore(new / "offline.sqlite3")
    try:
        assert [d.content for d in got.pending("지우")] == ["끊긴 동안 쓴 글"]
    finally:
        got.close()


def test_two_first_runs_do_not_adopt_at_the_same_time(places, store, monkeypatch):
    """창이 뜨기 전에 한 번 더 켰다. 뒤에 켜진 실행은 앞선 실행이 끝나기를 기다린다."""
    old, new = places
    _fill_classic(old)
    new.mkdir(parents=True)
    (new / "adopt.lock").write_bytes(b"")              # 앞선 실행이 가져오는 중
    monkeypatch.setattr(data_home, "_LOCK_WAIT", 0.2)

    assert data_home.adopt_classic_data() == []
    assert not (new / "notes.sqlite3").exists()        # 겹쳐서 옮기지 않았다

    # 가져오다 죽은 실행이 남긴 오래된 잠금은 걷고 가져온다.
    stale = time.time() - 3600
    os.utime(new / "adopt.lock", (stale, stale))
    assert "notes.sqlite3" in data_home.adopt_classic_data()
    assert not (new / "adopt.lock").exists()


def test_file_held_by_another_program_is_moved_on_a_later_try(places, store, monkeypatch):
    """방금 만든 파일을 백신이 잠깐 쥐고 있다. 한 번 실패했다고 그 파일을 빼놓지 않는다."""
    old, new = places
    _fill_classic(old)
    real = os.replace
    failed = []

    def flaky(src, dst):
        if str(dst).endswith("local_state.json") and not failed:
            failed.append(dst)
            raise PermissionError(13, "in use")
        return real(src, dst)

    monkeypatch.setattr(data_home.os, "replace", flaky)
    monkeypatch.setattr(data_home.time, "sleep", lambda _s: None)

    assert "local_state.json" in data_home.adopt_classic_data()
    assert failed and (new / "local_state.json").is_file()


def test_classic_edition_adopts_nothing(places):
    old, new = places
    _fill_classic(old)
    before = _tree(old)
    assert data_home.adopt_classic_data() == []
    assert not new.exists() and _tree(old) == before


def test_nothing_to_adopt_on_a_new_pc(places, store):
    old, new = places
    assert data_home.adopt_classic_data() == []
    assert not old.exists()


# ── 레지스트리에 기대는 기능 ────────────────────────────────────────────

@pytest.fixture
def no_registry(monkeypatch):
    """레지스트리에 쓰려 들면 테스트가 실패한다."""
    import winreg

    def refuse(*a, **k):
        raise AssertionError("스토어판이 레지스트리에 쓰려 했다")

    for name in ("SetValueEx", "CreateKey", "CreateKeyEx", "DeleteKey",
                 "DeleteValue"):
        monkeypatch.setattr(winreg, name, refuse)


@pytest.fixture
def task(monkeypatch):
    """패키지의 시작 작업을 흉내 낸다. ``task["state"]`` 가 Windows 가 쥔 상태다."""
    box = {"state": startup_task.DISABLED, "calls": []}

    def run(action):
        box["calls"].append(action.__name__)
        if action is startup_task._enable:
            if box["state"] == startup_task.DISABLED:
                box["state"] = startup_task.ENABLED
        elif action is startup_task._disable:
            if box["state"] == startup_task.ENABLED:
                box["state"] = startup_task.DISABLED
        return box["state"]

    monkeypatch.setattr(startup_task, "_run", run)
    return box


def test_store_edition_uses_the_startup_task_not_the_run_key(
        store, no_registry, task):
    assert settings_mod.autostart_supported()
    assert settings_mod.get_windows_autostart() is False
    assert settings_mod.set_windows_autostart(True) is True
    assert task["state"] == startup_task.ENABLED
    assert settings_mod.get_windows_autostart() is True
    assert settings_mod.set_windows_autostart(False) is True
    assert task["state"] == startup_task.DISABLED


def test_startup_task_switched_off_in_windows_cannot_be_switched_on(
        store, no_registry, task):
    # 사용자가 Windows 설정의 시작 앱에서 꺼 둔 것은 앱이 다시 켜지 못한다.
    task["state"] = startup_task.DISABLED_BY_USER
    assert not settings_mod.autostart_supported()
    assert settings_mod.set_windows_autostart(True) is False
    assert settings_mod.get_windows_autostart() is False
    task["state"] = startup_task.ENABLED_BY_POLICY
    assert settings_mod.get_windows_autostart() is True
    assert not settings_mod.autostart_supported()


def test_startup_task_is_unavailable_outside_a_package():
    assert startup_task.state() is None
    assert not startup_task.is_enabled() and not startup_task.can_change()
    assert startup_task.set_enabled(True) is False


def test_store_edition_keeps_the_shell_menu_in_a_file(places, store, no_registry):
    old, new = places
    marker = new / shell_menu.MARKER_NAME
    assert shell_menu.is_supported() and not shell_menu.is_registered()
    # 넣어 두지 않았으면 켤 때 맞추는 일도 하지 않는다.
    shell_menu.refresh("새 메모", "새 메모에 링크로 넣기")
    assert not marker.exists()

    assert shell_menu.set_registered(True, "새 메모 추가", "새 메모에 링크로 넣기",
                                     "새 메모 폴더 추가")
    assert shell_menu.is_registered()
    # 메뉴 DLL 이 읽는 꼴: UTF-8, 첫 줄이 바탕 화면의 새 메모, 둘째 줄이 파일 항목,
    # 셋째 줄이 바탕 화면의 새 메모 폴더(나중에 생긴 줄이라 맨 뒤다).
    assert marker.read_bytes() == (
        "새 메모 추가\n새 메모에 링크로 넣기\n새 메모 폴더 추가\n".encode("utf-8"))

    # 화면 언어가 바뀌면 메뉴의 글자도 따라간다.
    shell_menu.refresh("Add New Note", "Link in a New Note", "Add New Note Folder")
    assert marker.read_text(encoding="utf-8") == (
        "Add New Note\nLink in a New Note\nAdd New Note Folder\n")

    # 옛 버전이 적어 둔 두 줄짜리 파일은 켤 때 세 줄로 고쳐 적는다.
    marker.write_text("새 메모\n새 메모에 링크로 넣기\n", encoding="utf-8")
    shell_menu.refresh("새 메모 추가", "새 메모에 링크로 넣기", "새 메모 폴더 추가")
    assert marker.read_text(encoding="utf-8").splitlines() == [
        "새 메모 추가", "새 메모에 링크로 넣기", "새 메모 폴더 추가"]

    assert shell_menu.set_registered(False, "새 메모", "")
    assert not marker.exists() and not shell_menu.is_registered()
    assert shell_menu.set_registered(False, "새 메모", "")      # 없어도 된다


def test_classic_edition_never_writes_the_marker(places, monkeypatch):
    old, new = places
    monkeypatch.setattr(shell_menu, "KEY_PATH",
                        r"Software\SyncNotesTests\NoSuchKey")
    assert not shell_menu.is_registered()
    assert not (new / shell_menu.MARKER_NAME).exists() and not new.exists()


def test_store_edition_leaves_the_taskbar_identity_alone(store, monkeypatch):
    called: list[str] = []
    monkeypatch.setattr(config, "isolated_run", lambda: False)
    monkeypatch.setattr(win_jumplist, "CoCreateInstance",
                        lambda *a, **k: called.append("list"), raising=False)
    monkeypatch.setattr(win_jumplist, "_app_id", lambda: called.append("id") or "x")
    win_jumplist.set_app_user_model_id()
    win_jumplist.register(exe_path=sys.executable)
    assert called == []


def test_store_edition_does_not_check_github_for_updates(store, monkeypatch):
    import main

    timers: list[int] = []
    monkeypatch.setattr(main.QTimer, "singleShot",
                        staticmethod(lambda ms, fn: timers.append(ms)))
    main._schedule_update_check(None, None, AppSettings())
    assert timers == []


def test_classic_edition_still_checks_for_updates(monkeypatch):
    import main

    timers: list[int] = []
    monkeypatch.setattr(main.QTimer, "singleShot",
                        staticmethod(lambda ms, fn: timers.append(ms)))
    main._schedule_update_check(None, None, AppSettings())
    assert timers == [5000]


# ── 설정 창 ─────────────────────────────────────────────────────────────

def _row_labels(dlg) -> set[str]:
    return {w.text() for w in dlg.findChildren(QLabel)}


def test_store_settings_switch_the_startup_task_and_the_menu(
        qapp, places, store, no_registry, task):
    from app.ui.settings_dialog import SettingsDialog

    old, new = places
    settings = AppSettings()
    settings.save = lambda path=None: None
    dlg = SettingsDialog(settings, role="host")
    labels = _row_labels(dlg)
    # 스토어가 올리므로 자동 업데이트 줄만 없다.
    assert i18n.t("settings.check_auto_update") not in labels
    assert i18n.t("settings.check_autostart") in labels
    assert i18n.t("settings.check_desktop_menu") in labels
    # 스토어판의 메뉴는 Windows 11 의 첫 화면 메뉴에 나온다. 덧붙이는 말이 없다.
    assert i18n.t("settings.note_desktop_menu") not in labels
    assert i18n.t("settings.note_autostart_windows") not in labels
    assert dlg._autostart.isEnabled() and not dlg._autostart.isChecked()
    assert dlg._desktop_menu.isEnabled() and not dlg._desktop_menu.isChecked()

    dlg._autostart.setChecked(True)
    dlg._desktop_menu.setChecked(True)
    dlg._on_accept()
    assert task["state"] == startup_task.ENABLED
    assert (new / shell_menu.MARKER_NAME).read_text(encoding="utf-8") == (
        "새 메모 추가\n새 메모에 링크로 넣기\n새 메모 폴더 추가\n")
    dlg.deleteLater()

    again = SettingsDialog(settings, role="host")
    assert again._autostart.isChecked() and again._desktop_menu.isChecked()
    again._autostart.setChecked(False)
    again._desktop_menu.setChecked(False)
    again._on_accept()
    assert task["state"] == startup_task.DISABLED
    assert not (new / shell_menu.MARKER_NAME).exists()
    again.deleteLater()


def test_store_settings_say_where_to_change_a_locked_startup_task(
        qapp, places, store, no_registry, task):
    from app.ui.settings_dialog import SettingsDialog

    task["state"] = startup_task.DISABLED_BY_USER
    settings = AppSettings()
    settings.save = lambda path=None: None
    dlg = SettingsDialog(settings, role="host")
    assert not dlg._autostart.isEnabled() and not dlg._autostart.isChecked()
    assert i18n.t("settings.note_autostart_windows") in _row_labels(dlg)
    dlg._on_accept()
    assert task["state"] == startup_task.DISABLED_BY_USER
    assert "_enable" not in task["calls"]
    dlg.deleteLater()


def test_settings_dialog_is_unchanged_in_the_classic_edition(qapp):
    from app.ui.settings_dialog import SettingsDialog

    dlg = SettingsDialog(AppSettings(), role="host")
    labels = _row_labels(dlg)
    for key in ("settings.check_autostart", "settings.check_desktop_menu",
                "settings.check_auto_update", "settings.note_desktop_menu"):
        assert i18n.t(key) in labels, key
    assert i18n.t("settings.note_autostart_windows") not in labels
    dlg.deleteLater()


# ── 앱을 통째로 ─────────────────────────────────────────────────────────
# 스토어판처럼 꾸민 앱을 켠다. 설치판이 쓰던 폴더가 있는 PC 다. 메모를 가져와
# 띄우고, 새 메모를 만들고, 그림을 다른 프로그램에 넘길 파일로 내고, 끝낸다.
# 그동안 AppData(설치판의 폴더 포함)와 임시 폴더에는 아무것도 쓰지 않아야 하고,
# 레지스트리에도 쓰지 않아야 한다.
_WHOLE_APP = r'''
import faulthandler, hashlib, json, os, socket, sys, tempfile
from pathlib import Path

faulthandler.dump_traceback_later(60, exit=True)
root = sys.argv[1]
base = Path(tempfile.mkdtemp(prefix="syncnotes-store-test-"))
roaming, profile, temp = base / "Roaming", base / "Profile", base / "Temp"
for folder in (roaming, profile, temp):
    folder.mkdir()
os.environ["APPDATA"] = str(roaming)
os.environ["USERPROFILE"] = str(profile)
os.environ["TEMP"] = os.environ["TMP"] = str(temp)
tempfile.tempdir = None
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, root)
sys.argv = [str(Path(root) / "main.py")]

import winreg
registry_writes = []
for name in ("SetValueEx", "CreateKey", "CreateKeyEx", "DeleteKey", "DeleteValue"):
    setattr(winreg, name,
            lambda *a, _n=name, **k: registry_writes.append(_n))

from app import config, packaging
packaging.is_packaged = lambda: True

from app.database import SqliteRepository
from app.models import Note

old = config.classic_data_dir()
new = config.packaged_data_dir()
assert roaming in old.parents and profile in new.parents
s = socket.socket()
s.bind(("127.0.0.1", 0))
port = s.getsockname()[1]
s.close()
old.mkdir(parents=True)
repo = SqliteRepository(old / "notes.sqlite3")
for text in ("one", "two", "three"):
    n = Note.new(user="tester")
    n.content = text
    repo.insert_note(n)
repo.close()
(old / "local_state.json").write_text(json.dumps(
    {"nickname": "tester", "last_host_ip": "127.0.0.1", "last_host_port": port,
     "last_mode": "host", "last_heartbeat_ms": 3000, "notes": {}, "users": {}}),
    encoding="utf-8")
(old / "settings.json").write_text(
    json.dumps({"language_code": "ko", "show_tray_icon": False}), encoding="utf-8")


def tree(folder):
    return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*")) if p.is_file()}


roaming_before = tree(roaming)

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

import main as app_main
from app import image_export
from app.ui import confirm

confirm.ask = lambda parent, title, text, **k: True
confirm.notify = lambda parent, title, text, **k: None
report = {}
update_checks = []
app_main.UpdateChecker = lambda *a, **k: update_checks.append(1)
schedule = app_main._schedule_update_check
PNG = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4"
       "2mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def probe(app, window, settings):
    schedule(app, window, settings)

    def use():
        report["notes_shown"] = sum(w.isVisible() for w in window._stickies.values())
        window.handle_new_note_request(None)

    def more():
        report["notes_after_new"] = len(window._stickies)
        first = next(iter(window._stickies.values()))
        first.fold()
        exported = image_export.export_to_temp(PNG)
        report["export_ok"] = bool(exported and exported.is_file())
        report["export_outside_appdata"] = bool(
            exported and new in exported.parents)

    def leave():
        window._quit_app()

    QTimer.singleShot(1200, use)
    QTimer.singleShot(2200, more)
    QTimer.singleShot(6500, leave)      # 업데이트 확인(5초 뒤)이 지난 다음


app_main._schedule_update_check = probe
code = app_main.main()
report.update(
    exit=code,
    update_checks=len(update_checks),
    registry_writes=registry_writes,
    roaming_unchanged=tree(roaming) == roaming_before,
    temp_files=sorted(tree(temp)),
    store_files=sorted(p.name for p in new.iterdir()),
)
repo = SqliteRepository(new / "notes.sqlite3")
report["store_notes"] = len(repo.list_notes())
repo.close()
print(json.dumps(report))
'''


def test_whole_app_as_the_store_edition_stays_out_of_appdata(qapp):
    env = dict(os.environ)
    real = env.get("SYNCNOTES_TEST_REAL_APPDATA")
    if real:
        env["APPDATA"] = real       # 임시 APPDATA 로는 PySide6 를 못 찾는다
    root = str(Path(__file__).resolve().parent.parent)
    proc = subprocess.run([sys.executable, "-c", _WHOLE_APP, root],
                          capture_output=True, text=True, env=env, timeout=120)
    assert proc.returncode == 0, proc.stderr[-3000:]
    report = json.loads(proc.stdout.strip().splitlines()[-1])
    assert report["exit"] == 0
    # 설치판의 메모를 가져와 띄웠고, 새 메모도 스토어판의 폴더에 적혔다.
    assert report["notes_shown"] == 3 and report["notes_after_new"] == 4
    assert report["store_notes"] == 4
    assert {"notes.sqlite3", "local_state.json", "settings.json"} <= set(
        report["store_files"])
    # AppData 와 임시 폴더, 레지스트리에는 쓰지 않았다.
    assert report["roaming_unchanged"]
    assert report["temp_files"] == []
    assert report["registry_writes"] == []
    assert report["export_ok"] and report["export_outside_appdata"]
    assert report["update_checks"] == 0


# ── 묶음 설정과 메뉴 DLL ────────────────────────────────────────────────

def _clsids_in_dll_source() -> dict[str, str]:
    import re

    text = (Path(__file__).resolve().parent.parent / "shellext"
            / "memil_menu.cpp").read_text(encoding="utf-8")
    found: dict[str, str] = {}
    pattern = re.compile(
        r"const CLSID (CLSID_\w+)\s*=\s*\{0x([0-9A-Fa-f]{8}),\s*0x([0-9A-Fa-f]{4}),"
        r"\s*0x([0-9A-Fa-f]{4}),\s*\{([^}]*)\}\};")
    for name, a, b, c, tail in pattern.findall(text):
        d = [part.strip()[2:].upper().rjust(2, "0") for part in tail.split(",")]
        found[name] = (f"{a}-{b}-{c}-{d[0]}{d[1]}-{''.join(d[2:])}").upper()
    return found


def test_menu_dll_and_package_manifest_agree_on_the_menu_entries():
    import importlib.util
    from xml.etree import ElementTree

    path = Path(__file__).resolve().parent.parent / "tools" / "make_msix.py"
    spec = importlib.util.spec_from_file_location("make_msix_for_test", path)
    msix = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(msix)

    # DLL 이 아는 클래스와 묶음에 적는 클래스가 같아야 탐색기가 메뉴를 띄운다.
    assert _clsids_in_dll_source() == {
        "CLSID_NewNote": msix.CLSID_NEW_NOTE, "CLSID_LinkNote": msix.CLSID_LINK_NOTE,
        "CLSID_NewFolder": msix.CLSID_NEW_FOLDER}
    assert len({msix.CLSID_NEW_NOTE, msix.CLSID_LINK_NOTE, msix.CLSID_NEW_FOLDER}) == 3

    text = msix.MANIFEST.format(
        identity='"Test.App"', publisher='"CN=Test"', version='"1.0.0.0"',
        publisher_display="Test", languages='    <Resource Language="ko-KR" />',
        exe=msix.EXE_NAME, task=msix.STARTUP_TASK_ID, alias=msix.ALIAS, port=5555,
        find_port=5555, menu_dll=msix.MENU_DLL, clsid_new=msix.CLSID_NEW_NOTE,
        clsid_link=msix.CLSID_LINK_NOTE, clsid_folder=msix.CLSID_NEW_FOLDER)
    root = ElementTree.fromstring(text.encode("utf-8"))
    com = "{http://schemas.microsoft.com/appx/manifest/com/windows10}"
    d10 = "{http://schemas.microsoft.com/appx/manifest/desktop/windows10/10}"
    classes = [el.get("Id") for el in root.iter(com + "Class")]
    assert classes == [msix.CLSID_NEW_NOTE, msix.CLSID_LINK_NOTE, msix.CLSID_NEW_FOLDER]
    # 바탕 화면 메뉴: '새 메모 추가' 다음에 '새 메모 폴더 추가'.
    desktop = [el for el in root.iter(d10 + "ItemType")
               if el.get("Type") == "DesktopBackground"]
    assert len(desktop) == 1
    verbs = [(v.get("Id"), v.get("Clsid")) for v in desktop[0]]
    assert verbs == [("MemilNewNote", msix.CLSID_NEW_NOTE),
                     ("MemilNewNoteFolder", msix.CLSID_NEW_FOLDER)]
