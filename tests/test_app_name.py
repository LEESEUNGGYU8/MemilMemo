"""작업 표시줄에 나오는 프로그램 이름: Windows 가 기억해 둔 옛 이름 지우기.

실제로 쓰는 자리 대신 시험용 레지스트리 자리를 쓴다."""
from __future__ import annotations

import os
import sys

import pytest

from app import config, packaging, win_jumplist

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows 전용")

EXE = r"C:\Program Files\SyncNotes\SyncNotes.exe"


@pytest.fixture
def cache(monkeypatch):
    import winreg

    root = rf"Software\SyncNotesTests\pytest-{os.getpid()}"
    path = root + r"\MuiCache"
    monkeypatch.setattr(win_jumplist, "NAME_CACHE_KEY", path)
    key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, path)

    class Cache:
        @staticmethod
        def put(name: str, value: str) -> None:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)

        @staticmethod
        def get(name: str):
            try:
                return winreg.QueryValueEx(key, name)[0]
            except OSError:
                return None

    yield Cache
    winreg.CloseKey(key)
    for sub in (path, root, r"Software\SyncNotesTests"):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)
        except OSError:
            pass


def test_file_name_remembered_as_the_app_name_is_forgotten(cache):
    cache.put(EXE + ".FriendlyAppName", "SyncNotes.exe")
    cache.put(r"C:\Other\App.exe.FriendlyAppName", "App.exe")

    assert win_jumplist.forget_stale_app_name(EXE)

    assert cache.get(EXE + ".FriendlyAppName") is None
    # 남의 것은 건드리지 않는다.
    assert cache.get(r"C:\Other\App.exe.FriendlyAppName") == "App.exe"
    assert not win_jumplist.forget_stale_app_name(EXE)      # 지울 것이 없다


def test_a_real_name_is_kept(cache):
    cache.put(EXE + ".FriendlyAppName", "메밀메모")
    assert not win_jumplist.forget_stale_app_name(EXE)
    assert cache.get(EXE + ".FriendlyAppName") == "메밀메모"


def test_only_the_installed_app_touches_the_cache(cache, monkeypatch):
    seen = []
    monkeypatch.setattr(win_jumplist, "forget_stale_app_name", seen.append)

    # 소스로 띄운 실행(시험, 개발)
    monkeypatch.delattr(sys, "frozen", raising=False)
    win_jumplist.refresh_app_name()
    # 빌드본이지만 시험용으로 격리해 띄운 실행
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(config, "isolated_run", lambda: True)
    win_jumplist.refresh_app_name()
    # 스토어판: 이름은 패키지가 정한다
    monkeypatch.setattr(config, "isolated_run", lambda: False)
    monkeypatch.setattr(packaging, "is_packaged", lambda: True)
    win_jumplist.refresh_app_name()
    assert seen == []

    monkeypatch.setattr(packaging, "is_packaged", lambda: False)
    win_jumplist.refresh_app_name()
    assert seen == [sys.executable]
