"""스토어판이 처음 켜질 때, 설치판이 쓰던 메모와 설정을 한 번 가져온다.

스토어판은 메모를 AppData 밖(``config.packaged_data_dir``)에 둔다. 설치판을
쓰던 PC 에서 스토어판을 처음 켜면 빈 화면으로 시작하지 않게 옛 폴더의 내용을
베껴 온다.

* 옛 폴더(``%APPDATA%\\SyncNotes``)는 읽기만 한다. 지우지도 옮기지도 않고,
  쓰다 만 기록이 남아 있어도 합치지 않는다.
* 새 폴더에 이미 무엇이 있으면 아무것도 하지 않는다(덮어쓰지 않는다).
* 메모 파일은 SQLite 의 백업 기능으로 뜨고, 온전한 것만 들인다.
* 가져온 뒤로 두 판은 서로 다른 폴더를 쓴다.

설치판에서는 아무 일도 하지 않는다."""
from __future__ import annotations

import os
import shutil
import sqlite3
import time
from pathlib import Path

from . import config, packaging
from .database import check_database, snapshot_database

# 메모 파일과 그 안에 꼭 있어야 하는 표: 공유 PC 의 원본, 참여 PC 의 사본,
# 끊긴 동안 쓴 메모.
_DATABASES = {"notes.sqlite3": "notes", "replica.sqlite3": "notes",
              "offline.sqlite3": "drafts"}
# 이 PC 의 설정과 창 배치.
_PLAIN = ("settings.json", "local_state.json")
# 베끼는 동안 쓰는 폴더. 다 베낀 뒤에 한꺼번에 제자리로 옮긴다.
_STAGE = "adopt.tmp"
# 두 실행이 한꺼번에 가져오지 않게 쥐는 잠금 파일, 다른 실행을 기다리는 시간(초),
# 죽은 실행이 남긴 잠금으로 보는 나이(초), 제자리로 옮기기를 다시 해 보는 횟수.
_LOCK = "adopt.lock"
_LOCK_WAIT = 60.0
_LOCK_STALE = 300.0
_MOVE_TRIES = 6
_NAMES = tuple(_DATABASES) + _PLAIN
# 메모 파일에 딸린, 아직 합쳐지지 않은 기록. 함께 베껴야 마지막에 쓴 것까지 온다.
_SIDECARS = ("-wal", "-journal")
# 베끼는 동안 옛 파일이 바뀌면(설치판이 켜져 있다) 다시 베껴 보는 횟수.
_COPY_TRIES = 3


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
    if any((new / name).exists() for name in _NAMES):
        return []
    lock = _take_lock(new / _LOCK)
    if lock is None:
        # 같은 때에 켜진 다른 실행이 가져오는 중이다. 끝나기를 기다렸다가 그대로 쓴다.
        _wait_for(new / _LOCK)
        return []
    stage = new / _STAGE
    try:
        if any((new / name).exists() for name in _NAMES):
            return []                       # 잠금을 쥐기 직전에 다른 실행이 끝냈다
        shutil.rmtree(stage, ignore_errors=True)    # 지난번에 하다 만 것
        stage.mkdir(parents=True, exist_ok=True)
        ready = [name for name, table in _DATABASES.items()
                 if _copy_database(old / name, stage / name, table)]
        ready += [name for name in _PLAIN
                  if _copy_plain(old / name, stage / name)]
        return [name for name in ready if _move(stage / name, new / name)]
    finally:
        shutil.rmtree(stage, ignore_errors=True)
        os.close(lock)
        _discard(new / _LOCK)


def _take_lock(path: Path) -> int | None:
    """가져오는 동안 쥐는 잠금 파일. 다른 실행이 쥐고 있으면 None."""
    try:
        if path.exists() and time.time() - path.stat().st_mtime > _LOCK_STALE:
            os.remove(path)                 # 가져오다 죽은 실행이 남긴 것
    except OSError:
        pass
    try:
        return os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except OSError:
        return None


def _wait_for(path: Path) -> None:
    deadline = time.time() + _LOCK_WAIT
    while path.exists() and time.time() < deadline:
        time.sleep(0.1)


def _move(src: Path, dst: Path) -> bool:
    """베껴 둔 파일을 제자리로 옮긴다. 다른 프로그램(백신)이 방금 만든 파일을
    잠깐 쥐고 있을 수 있으므로 몇 번 다시 해 본다."""
    for attempt in range(_MOVE_TRIES):
        try:
            os.replace(src, dst)
            return True
        except OSError:
            if attempt + 1 < _MOVE_TRIES:
                time.sleep(0.2)
    return False


def _same(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def _copy_database(src: Path, dst: Path, table: str) -> bool:
    """옛 폴더의 메모 파일 하나를 ``dst`` 로 가져온다.

    옛 파일을 SQLite 로 열지 않는다. 열기만 해도 쓰다 만 기록(-wal)이 원래
    파일에 합쳐지거나 그 폴더에 새 파일이 생긴다. 파일과 그 기록을 그대로 베껴
    와서 그 사본을 열고, 온전한 것만 들인다."""
    if not src.is_file():
        return False
    raw = dst.with_name("raw-" + dst.name)
    try:
        for _attempt in range(_COPY_TRIES):
            before = _stamp(src)
            _discard(raw)
            shutil.copyfile(src, raw)
            for suffix in _SIDECARS:
                side = Path(str(src) + suffix)
                if side.is_file():
                    shutil.copyfile(side, str(raw) + suffix)
            if _stamp(src) == before:
                break               # 베끼는 동안 옛 파일이 바뀌지 않았다
        snapshot_database(raw, dst)
    except (OSError, sqlite3.Error):
        _discard(dst)
        return False
    finally:
        _discard(raw)
    if check_database(dst, table):
        # 옛 파일이 상해 있다. 상한 것을 들이느니 빈 채로 시작한다(옛 파일은
        # 그 자리에 그대로 있다).
        _discard(dst)
        return False
    return True


def _stamp(path: Path) -> list[tuple[int, int] | None]:
    """파일과 딸린 기록의 크기와 고친 때. 베끼는 동안 바뀌었는지 볼 때 쓴다."""
    out: list[tuple[int, int] | None] = []
    for suffix in ("",) + _SIDECARS:
        try:
            st = os.stat(str(path) + suffix)
            out.append((st.st_size, st.st_mtime_ns))
        except OSError:
            out.append(None)
    return out


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
