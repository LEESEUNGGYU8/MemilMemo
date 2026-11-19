"""자동 업데이트가 여러 릴리스 저장소를 차례로 보는 동작과 시험 실행 구분 테스트."""
from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication  # noqa: E402

from app import config, i18n, ipc, updater  # noqa: E402

REPO = "MemilMemo"


@pytest.fixture(scope="module")
def qapp():
    app = QCoreApplication.instance() or QCoreApplication([])
    i18n.set_language("ko")
    yield app


def _release(tag: str, installer: bool = True) -> bytes:
    assets = [{"name": "version.json",
               "browser_download_url": f"https://example.invalid/{tag}/version.json"}]
    if installer:
        assets.append({"name": f"Setup-{tag}.exe",
                       "browser_download_url": f"https://example.invalid/{tag}/setup.exe"})
    return json.dumps({"tag_name": tag, "body": "notes", "assets": assets}).encode()


class FakeChecker(updater.UpdateChecker):
    """네트워크 대신 저장소 이름별로 준비한 답을 돌려준다."""

    def __init__(self, current: str, replies: dict[str, bytes | None]):
        super().__init__(current)
        self.replies = replies
        self.asked: list[str] = []
        self.result: tuple[str, object] | None = None
        self.updateAvailable.connect(lambda info: self._done("update", info))
        self.upToDate.connect(lambda: self._done("latest", None))
        self.error.connect(lambda msg: self._done("error", msg))

    def _done(self, kind: str, value: object) -> None:
        assert self.result is None, "결과는 한 번만 나와야 한다"
        self.result = (kind, value)

    def _request(self, url: str) -> None:
        repo = url.split("/repos/")[1].split("/")[1]
        self.asked.append(repo)
        body = self.replies.get(repo)
        if body is None:                 # 없는 저장소: 404
            self._handle_response(False, b"", "Not Found")
        else:
            self._handle_response(True, body)


def test_updates_come_from_the_single_repo(qapp):
    assert config.UPDATE_REPO_NAMES == (REPO,)
    urls = updater._releases_api_urls()
    assert [u.split("/repos/")[1] for u in urls] == [
        f"{config.UPDATE_REPO_OWNER}/{REPO}/releases/latest"]


def test_newer_release_is_offered(qapp):
    c = FakeChecker("1.0.7", {REPO: _release("v1.1.0")})
    c.check()
    kind, info = c.result
    assert kind == "update" and info["version"] == "v1.1.0"
    assert info["download_url"].endswith("/v1.1.0/setup.exe")
    assert info["version_url"].endswith("/v1.1.0/version.json")


def test_same_version_is_up_to_date(qapp):
    c = FakeChecker("1.1.0", {REPO: _release("v1.1.0")})
    c.check()
    assert c.result == ("latest", None)


def test_release_without_installer_is_not_offered(qapp):
    # 설치 파일이 아직 안 올라간 릴리스는 받으라고 하지 않는다(오류는 화면에 안 뜬다).
    c = FakeChecker("1.0.7", {REPO: _release("v1.1.0", installer=False)})
    c.check()
    assert c.result[0] == "error"


def test_error_when_the_repo_does_not_answer(qapp):
    c = FakeChecker("1.0.7", {})
    c.check()
    assert c.result[0] == "error"


def test_checker_can_be_reused(qapp):
    c = FakeChecker("1.1.0", {REPO: _release("v1.1.0")})
    c.check()
    c.result = None
    c.check()
    assert c.result == ("latest", None)


def test_installer_name_has_no_version():
    # 소개 페이지의 고정 내려받기 주소가 이 이름을 쓴다.
    assert config.INSTALLER_NAME == "MemilMemo-Setup.exe"


# ── 시험 실행 구분 ───────────────────────────────────────────────────────

def test_isolated_run_compares_appdata_with_the_real_folder(monkeypatch):
    monkeypatch.setattr(config, "_real_appdata", lambda: r"C:\Users\me\AppData\Roaming")
    monkeypatch.setenv("APPDATA", r"C:\Users\me\AppData\Roaming")
    assert not config.isolated_run()
    monkeypatch.setenv("APPDATA", r"c:\users\ME\appdata\roaming" + "\\")
    assert not config.isolated_run()             # 대소문자·끝 구분자는 같은 폴더다
    monkeypatch.setenv("APPDATA", r"C:\Temp\syncnotes-test")
    assert config.isolated_run()


def test_isolated_run_is_false_when_unknown(monkeypatch):
    monkeypatch.setattr(config, "_real_appdata", lambda: None)
    monkeypatch.setenv("APPDATA", r"C:\Temp\x")
    assert not config.isolated_run()             # 모르면 평소 실행으로 본다


def test_pipe_name_differs_for_an_isolated_run(monkeypatch):
    monkeypatch.setattr(config, "_real_appdata", lambda: r"C:\Users\me\AppData\Roaming")
    monkeypatch.setenv("APPDATA", r"C:\Users\me\AppData\Roaming")
    normal = ipc.ipc_name()
    monkeypatch.setenv("APPDATA", r"C:\Temp\a")
    a = ipc.ipc_name()
    monkeypatch.setenv("APPDATA", r"C:\Temp\b")
    b = ipc.ipc_name()
    assert normal.startswith("SyncNotes-IPC-")
    assert a.startswith(normal + "-") and b.startswith(normal + "-")
    assert a != b                                 # 폴더가 다르면 서로도 섞이지 않는다
    monkeypatch.setenv("APPDATA", r"C:\Temp\a")
    assert ipc.ipc_name() == a                    # 같은 폴더의 두 번째 실행은 같은 이름


def test_this_test_run_is_isolated():
    # conftest 가 APPDATA 를 임시 폴더로 돌렸으므로, 테스트는 실제 앱의
    # 파이프 이름·작업 표시줄 목록과 섞이지 않는다.
    if config._real_appdata() is None:
        pytest.skip("Windows 가 아니다")
    assert config.isolated_run()
