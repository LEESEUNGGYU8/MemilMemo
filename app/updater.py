"""GitHub Releases 기반 자동 업데이트."""

from __future__ import annotations

import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtNetwork import (
    QNetworkAccessManager,
    QNetworkReply,
    QNetworkRequest,
)

from . import config, i18n


def _releases_api_urls() -> list[str]:
    """새 버전을 찾아볼 주소. 앞에서부터 차례로 본다(config.UPDATE_REPO_NAMES)."""
    return [
        f"https://api.github.com/repos/{config.UPDATE_REPO_OWNER}/{name}"
        f"/releases/latest"
        for name in config.UPDATE_REPO_NAMES
    ]


def _parse_semver(s: str) -> tuple[int, int, int]:
    """버전 문자열을 비교 가능한 3-튜플로 바꾼다.

    빠진 minor/patch는 0으로 채우고, 각 자리의 비숫자 꼬리표는 잘라낸다."""
    s = (s or "").lstrip("v").strip()
    if "-" in s:
        s = s.split("-", 1)[0]
    parts: list[int] = []
    for p in s.split(".")[:3]:
        digits = ""
        for ch in p:
            if ch.isdigit():
                digits += ch
            else:
                break
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return (parts[0], parts[1], parts[2])


class UpdateChecker(QObject):
    updateAvailable = Signal(dict)   # {version, download_url, version_url, notes}
    upToDate = Signal()
    error = Signal(str)

    def __init__(self, current_version: str,
                 parent: QObject | None = None):
        super().__init__(parent)
        self._current = current_version
        self._mgr = QNetworkAccessManager(self)
        self._pending: list[str] = []
        # 한 곳이라도 "더 새 버전 없음"이라고 제대로 답했는가.
        self._answered = False
        self._last_error = ""

    def check(self) -> None:
        self._pending = _releases_api_urls()
        self._answered = False
        self._last_error = ""
        self._check_next()

    def _check_next(self) -> None:
        if not self._pending:
            # 끝까지 봤는데 더 새 버전이 없다. 답한 곳이 하나도 없으면 오류다.
            if self._answered:
                self.upToDate.emit()
            else:
                self.error.emit(
                    self._last_error or i18n.t("update.err_no_release"))
            return
        self._request(self._pending.pop(0))

    def _request(self, url: str) -> None:
        req = QNetworkRequest(QUrl(url))
        req.setRawHeader(b"User-Agent", b"MemilMemo-Updater")
        req.setRawHeader(b"Accept", b"application/vnd.github+json")
        reply = self._mgr.get(req)
        reply.finished.connect(lambda r=reply: self._on_response(r))

    def _on_response(self, reply: QNetworkReply) -> None:
        try:
            ok = reply.error() == QNetworkReply.NetworkError.NoError
            raw = bytes(reply.readAll()) if ok else b""
            detail = "" if ok else reply.errorString()
        finally:
            reply.deleteLater()
        self._handle_response(ok, raw, detail)

    def _handle_response(self, ok: bool, raw: bytes, detail: str = "") -> None:
        """저장소 한 곳의 답을 처리한다. 더 새 버전이 있으면 거기서 끝내고,
        없거나 답을 못 받았으면 다음 저장소로 넘어간다(아직 없는 저장소는 404)."""
        info, error = self._read_release(ok, raw, detail)
        if info is not None:
            self._pending = []
            self.updateAvailable.emit(info)
            return
        if error:
            self._last_error = error
        else:
            self._answered = True
        self._check_next()

    def _read_release(self, ok: bool, raw: bytes,
                      detail: str) -> tuple[dict | None, str]:
        """(받을 새 버전, 오류 문구). 둘 다 비면 '지금 버전이 최신'이다."""
        if not ok:
            return None, i18n.t("update.err_network", detail=detail)
        try:
            payload = json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            return None, i18n.t("update.err_parse", detail=exc)
        if not isinstance(payload, dict) or "tag_name" not in payload:
            msg = (payload.get("message")
                   if isinstance(payload, dict) else None)
            return None, str(msg or i18n.t("update.err_no_release"))
        tag = str(payload.get("tag_name", ""))
        if _parse_semver(tag) <= _parse_semver(self._current):
            return None, ""
        installer_url: str | None = None
        version_url: str | None = None
        for asset in payload.get("assets", []) or []:
            name = asset.get("name", "") or ""
            if name.lower().endswith(".exe"):
                installer_url = asset.get("browser_download_url")
            elif name == "version.json":
                version_url = asset.get("browser_download_url")
        if not installer_url:
            return None, i18n.t("update.err_no_installer")
        return {
            "version": tag,
            "download_url": installer_url,
            "version_url": version_url,
            "notes": str(payload.get("body") or ""),
        }, ""


class UpdateDownloader(QObject):
    downloadProgress = Signal(int, int)   # 수신 바이트, 전체 바이트
    installerReady = Signal(str)          # 검증된 .exe 경로
    error = Signal(str)

    def __init__(self, info: dict, parent: QObject | None = None):
        super().__init__(parent)
        self._info = info
        self._mgr = QNetworkAccessManager(self)
        self._expected_sha: str | None = None
        self._reply: QNetworkReply | None = None
        self._cancelled = False
        # 확인을 마친 설치 파일의 해시. 실행 직전에 파일이 그대로인지 다시 본다.
        self.verified_sha256 = ""

    def cancel(self) -> None:
        """받기를 멈춘다. 멈춘 뒤에는 어떤 신호도 내지 않는다."""
        self._cancelled = True
        if self._reply is not None:
            self._reply.abort()

    def start(self) -> None:
        version_url = self._info.get("version_url")
        if version_url:
            req = QNetworkRequest(QUrl(version_url))
            req.setRawHeader(b"User-Agent", b"MemilMemo-Updater")
            reply = self._mgr.get(req)
            self._reply = reply
            reply.finished.connect(
                lambda r=reply: self._on_version_json(r))
        else:
            # 받은 파일이 올린 것과 같은지 확인할 길이 없으면 설치하지 않는다.
            self.error.emit(i18n.t("update.err_no_checksum"))

    def _on_version_json(self, reply: QNetworkReply) -> None:
        if self._cancelled:
            reply.deleteLater()
            return
        try:
            self._expected_sha = None
            if reply.error() == QNetworkReply.NetworkError.NoError:
                try:
                    meta = json.loads(
                        bytes(reply.readAll())
                        .decode("utf-8", errors="replace"))
                    value = meta.get("sha256") if isinstance(meta, dict) else ""
                    value = str(value or "").lower().strip()
                    if re.fullmatch(r"[0-9a-f]{64}", value):
                        self._expected_sha = value
                except json.JSONDecodeError:
                    pass
            if self._expected_sha is None:
                self.error.emit(i18n.t("update.err_no_checksum"))
                return
            self._download_installer()
        finally:
            reply.deleteLater()

    def _download_installer(self) -> None:
        # GitHub 자산 URL은 CDN으로 리다이렉트되므로 리다이렉트를 허용한다.
        req = QNetworkRequest(QUrl(self._info["download_url"]))
        req.setRawHeader(b"User-Agent", b"MemilMemo-Updater")
        req.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
        reply = self._mgr.get(req)
        self._reply = reply
        reply.downloadProgress.connect(self.downloadProgress.emit)
        reply.finished.connect(lambda r=reply: self._on_installer(r))

    def _on_installer(self, reply: QNetworkReply) -> None:
        self._reply = None
        if self._cancelled:
            reply.deleteLater()
            return
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self.error.emit(
                    i18n.t("update.err_download", detail=reply.errorString()))
                return
            data = bytes(reply.readAll())
            actual_sha = hashlib.sha256(data).hexdigest()
            if actual_sha.lower() != self._expected_sha:
                # 게시된 바이트와 다르면(손상 또는 변조) 실행하지 않는다.
                self.error.emit(
                    i18n.t("update.err_hash_mismatch",
                           expected=self._expected_sha, actual=actual_sha))
                return
            version = re.sub(r"[^0-9A-Za-z._-]", "_",
                             str(self._info.get("version") or ""))
            try:
                # 이름을 미리 알 수 없는, 이 사용자만 쓰는 새 폴더에 둔다.
                folder = Path(tempfile.mkdtemp(prefix="MemilMemo-update-"))
                target = folder / f"MemilMemo-Setup-{version}.exe"
                target.write_bytes(data)
            except OSError as exc:
                self.error.emit(i18n.t("update.err_temp_write", detail=exc))
                return
            self.verified_sha256 = actual_sha
            self.installerReady.emit(str(target))
        finally:
            reply.deleteLater()


def launch_installer(installer_path: str, expected_sha256: str = "") -> bool:
    """Inno Setup 인스톨러를 silent 모드로 실행한다. 실행했으면 True.

    ``expected_sha256`` 을 주면 실행 직전에 파일을 다시 읽어 그 해시와 같은지
    본다. 받아 둔 뒤 실행하기까지 시간이 걸릴 수 있고(편집이 끝나기를 기다리는
    자동 업데이트), 그 사이에 파일이 바뀌었으면 실행하지 않는다.

    인스톨러가 실행 중인 앱을 종료시킨 뒤 바이너리를 교체하므로,
    호출 측도 곧바로 깨끗하게 exit해야 한다."""
    import subprocess
    if expected_sha256:
        try:
            actual = hashlib.sha256(Path(installer_path).read_bytes()).hexdigest()
        except OSError:
            return False
        if actual != expected_sha256.lower():
            return False
    try:
        subprocess.Popen([
            installer_path,
            "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
        ], close_fds=True)
    except OSError as exc:
        if sys.stderr is not None:
            sys.stderr.write(f"[updater] failed to launch installer: {exc}\n")
        return False
    return True
