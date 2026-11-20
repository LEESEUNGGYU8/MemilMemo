"""스토어판이 처음 켜질 때, 설치판이 쓰던 메모와 설정을 한 번 가져온다.

스토어판은 메모를 AppData 밖(``config.packaged_data_dir``)에 둔다. 설치판을
쓰던 PC 에서 스토어판을 처음 켜면 빈 화면으로 시작하지 않게 옛 폴더의 내용을
베껴 온다.

* 옛 폴더(``%APPDATA%\\SyncNotes``)는 읽기만 한다. 지우지도 옮기지도 않는다.
* 새 폴더에 이미 무엇이 있으면 아무것도 하지 않는다(덮어쓰지 않는다).
* 메모 파일은 SQLite 의 백업 기능으로 뜨고, 온전한 것만 들인다.
* 가져온 뒤로 두 판은 서로 다른 폴더를 쓴다.

설치판에서는 아무 일도 하지 않는다."""
from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path

from . import config, packaging
from .database import check_database, snapshot_database

# 메모 파일(공유 PC 의 원본, 참여 PC 의 사본).
_DATABASES = ("notes.sqlite3", "replica.sqlite3")
# 이 PC 의 설정과 창 배치.
_PLAIN = ("settings.json", "local_state.json")
# 베끼는 동안 쓰는 폴더. 다 베낀 뒤에 한꺼번에 제자리로 옮긴다.
_STAGE = "adopt.tmp"


def adopt_classic_data() -> list[str]:
    """가져온 파일 이름들. 가져올 것이 없었으면 빈 목록."""
    if not packaging.is_packaged():
        return []
    try:
        return _adopt(config.classic_data_dir(), config.app_data_dir())
    except OSError:
        return []


def _adopt(old: Path, new: Path) -> list[str]:
    if not old.is_dir() or _same(old, new):
        return []
    if any((new / name).exists() for name in _DATABASES + _PLAIN):
        return []
    stage = new / _STAGE
    shutil.rmtree(stage, ignore_errors=True)    # 지난번에 하다 만 것
    stage.mkdir(parents=True, exist_ok=True)
    try:
        ready = [name for name in _DATABASES
                 if _copy_database(old / name, stage / name)]
        ready += [name for name in _PLAIN
                  if _copy_plain(old / name, stage / name)]
        for name in ready:
            os.replace(stage / name, new / name)
        return ready
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def _same(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def _copy_database(src: Path, dst: Path) -> bool:
    if not src.is_file():
        return False
    try:
        snapshot_database(src, dst)
    except (OSError, sqlite3.Error):
        _discard(dst)
        return False
    if check_database(dst):
        # 옛 파일이 상해 있다. 상한 것을 들이느니 빈 채로 시작한다(옛 파일은
        # 그 자리에 그대로 있다).
        _discard(dst)
        return False
    return True


def _copy_plain(src: Path, dst: Path) -> bool:
    if not src.is_file():
        return False
    try:
        shutil.copyfile(src, dst)
    except OSError:
        _discard(dst)
        return False
    return True


def _discard(path: Path) -> None:
    for suffix in ("", "-journal", "-wal", "-shm"):
        try:
            os.remove(str(path) + suffix)
        except OSError:
            pass
