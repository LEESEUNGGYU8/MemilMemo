from __future__ import annotations

import os
import re
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from . import i18n
from .database import check_database, snapshot_database
from .settings import AppSettings

_NAME = re.compile(r"^notes_(\d{8}_\d{6})\.sqlite3$")
_STAMP = "%Y%m%d_%H%M%S"
# 자동 백업이 실패한 뒤 다시 해 보기까지 두는 시간(초). 공유 폴더가 꺼져 있을 때
# 1분마다 붙어 보느라 화면이 멈칫하지 않게 한다.
_RETRY_AFTER = 10 * 60


def backup_time(path: Path) -> float:
    """백업을 뜬 시각. 파일 이름에 적힌 것을 쓰고, 읽지 못하면 고친 시각."""
    m = _NAME.match(path.name)
    if m:
        try:
            return datetime.strptime(m.group(1), _STAMP).timestamp()
        except ValueError:
            pass
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def list_backups(folder: Path) -> list[Path]:
    """폴더 안의 백업 파일을 새것부터. 뜨다 만 파일(.part)은 넣지 않는다."""
    try:
        found = [p for p in folder.iterdir()
                 if p.is_file() and _NAME.match(p.name)]
    except OSError:
        return []
    return sorted(found, key=backup_time, reverse=True)


def latest_good_backup(folder: Path) -> Path | None:
    """온전한 백업 가운데 가장 새것."""
    for p in list_backups(folder):
        if not check_database(p):
            return p
    return None


def set_aside(db_path: Path, tag: str) -> Path | None:
    """메모 파일을 지우지 않고 옆으로 옮겨 둔다(같은 폴더, 다른 이름).

    쓰다 만 기록(-journal)이 남아 있으면 함께 옮긴다. 그대로 두면 SQLite 가
    새로 들어온 파일에 그 기록을 되감아 넣어 망가뜨린다."""
    if not db_path.exists():
        return None
    stamp = datetime.now().strftime(_STAMP)
    kept = db_path.with_name(f"{db_path.stem}.{tag}-{stamp}{db_path.suffix}")
    os.replace(db_path, kept)
    for extra in ("-journal", "-wal", "-shm"):
        side = db_path.with_name(db_path.name + extra)
        if side.exists():
            os.replace(side, kept.with_name(kept.name + extra))
    return kept


def replace_database(db_path: Path, backup: Path) -> Path | None:
    """열 수 없게 된 메모 파일을 옮겨 두고 그 자리에 백업을 놓는다.

    옮겨 둔 파일의 경로를 돌려준다. 앱을 시작할 때, 파일을 아직 열지 않은
    상태에서만 쓴다."""
    kept = set_aside(db_path, "damaged")
    snapshot_database(backup, db_path)
    return kept


class BackupManager(QObject):
    """호스트에서만 의미가 있다. 게스트에는 DB가 없다."""

    backupCreated = Signal(str)   # 백업 파일 경로
    backupFailed = Signal(str)    # 오류 메시지

    def __init__(self, db_path: Path, settings: AppSettings,
                 parent: QObject | None = None):
        super().__init__(parent)
        self.db_path = Path(db_path)
        self.settings = settings
        # 마지막으로 백업한 시각. 폴더에 남은 파일에서 읽어 오므로(_sync_folder)
        # 앱을 껐다 켤 때마다 새로 뜨지 않는다.
        self._last_backup_ts: float = 0.0
        self._last_failure_ts: float = 0.0
        self._folder_seen: str | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(60 * 1000)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _target_dir(self) -> Path:
        target = self.settings.resolved_backup_dir()
        target.mkdir(parents=True, exist_ok=True)
        return target

    def _sync_folder(self) -> None:
        """백업 폴더가 바뀌었으면 그 폴더의 가장 새 백업 시각을 다시 읽는다."""
        folder = str(self.settings.resolved_backup_dir())
        if folder == self._folder_seen:
            return
        self._folder_seen = folder
        backups = list_backups(Path(folder))
        self._last_backup_ts = backup_time(backups[0]) if backups else 0.0
        self._last_failure_ts = 0.0

    def _changed_since_backup(self) -> bool:
        """마지막 백업 뒤로 메모 파일이 바뀌었는가. 모르겠으면 바뀐 것으로 본다."""
        try:
            modified = self.db_path.stat().st_mtime
        except OSError:
            return True
        # 같은 순간에 걸친 경우에는 한 번 더 뜨는 쪽을 고른다.
        return modified + 2 > self._last_backup_ts

    def _tick(self) -> None:
        if not self.settings.backup_enabled:
            return
        self._sync_folder()
        interval = max(60, self.settings.backup_interval_seconds())
        now = datetime.now().timestamp()
        if now - self._last_backup_ts < interval:
            return
        if now - self._last_failure_ts < _RETRY_AFTER:
            return
        if self._last_backup_ts and not self._changed_since_backup():
            # 바뀐 것이 없으면 뜨지 않는다. 같은 내용이 쌓여 옛 백업을 밀어내지
            # 않게 한다. 다음 주기에 다시 본다.
            self._last_backup_ts = now
            return
        self.run_backup()

    def run_backup(self) -> Path | None:
        """지금 내용을 백업한다. 온전하게 떠졌는지 확인한 뒤에만 남긴다."""
        if not self.db_path.exists():
            self._fail(i18n.t("backup.err_no_db"))
            return None
        part: Path | None = None
        try:
            target = self._target_dir()
            self._folder_seen = str(self.settings.resolved_backup_dir())
            dest = self._free_name(target)
            part = dest.with_name(dest.name + ".part")
            snapshot_database(self.db_path, part)
            problem = check_database(part)
            if problem:
                # 원본이 상했다. 상한 사본으로 멀쩡한 옛 백업을 밀어내지 않는다.
                self._discard(part)
                self._fail(i18n.t("backup.err_damaged"))
                return None
            os.replace(part, dest)
        except sqlite3.OperationalError as e:
            # 폴더에 쓸 수 없다, 파일이 잠겼다 같은 일. 파일이 상한 것은 아니다.
            self._discard(part)
            self._fail(str(e))
            return None
        except sqlite3.DatabaseError:
            self._discard(part)
            self._fail(i18n.t("backup.err_damaged"))
            return None
        except (OSError, sqlite3.Error) as e:
            self._discard(part)
            self._fail(str(e))
            return None
        self._last_backup_ts = datetime.now().timestamp()
        self._last_failure_ts = 0.0
        self._cleanup_old(target)
        self.backupCreated.emit(str(dest))
        return dest

    def keep_current(self) -> Path:
        """복원 직전의 메모 파일을 그대로 베껴 둔다(백업 폴더와 무관하게 같은 자리에).

        파일이 상해 있어도 베낄 수 있고, 한 벌만 둔다. 잘못 복원했을 때 돌아갈 길이다."""
        kept = self.db_path.with_name(
            f"{self.db_path.stem}.before-restore{self.db_path.suffix}")
        shutil.copy2(self.db_path, kept)
        return kept

    @staticmethod
    def _free_name(target: Path) -> Path:
        when = datetime.now()
        while True:
            dest = target / f"notes_{when.strftime(_STAMP)}.sqlite3"
            if not dest.exists() and not dest.with_name(dest.name + ".part").exists():
                return dest
            when += timedelta(seconds=1)

    @staticmethod
    def _discard(part: Path | None) -> None:
        if part is None:
            return
        try:
            part.unlink()
        except OSError:
            pass

    def _fail(self, message: str) -> None:
        self._last_failure_ts = datetime.now().timestamp()
        self.backupFailed.emit(message)

    def _cleanup_old(self, target: Path) -> None:
        keep = max(1, int(self.settings.backup_keep_count))
        for old in list_backups(target)[keep:]:
            try:
                old.unlink()
            except OSError:
                pass

    def stop(self) -> None:
        self._timer.stop()
