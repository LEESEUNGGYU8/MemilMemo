"""메모 안의 링크를 연다.

네트워크 경로는 상대 PC 가 꺼져 있으면 있는지 확인하는 데만 몇 초가 걸린다.
그동안 화면이 멈추지 않도록 확인은 작업 스레드에서 하고, 실제로 여는 일
(셸 호출)은 화면 스레드에서 한다."""
from __future__ import annotations

import os
import subprocess
import threading

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox, QWidget

from .. import file_links as fl
from .. import i18n


class _Prober(QObject):
    """경로가 있는지 작업 스레드에서 확인해 화면 스레드로 알린다."""

    done = Signal(int, bool)

    def __init__(self):
        super().__init__()
        self._seq = 0
        self._waiting: dict[int, tuple] = {}
        self.done.connect(self._on_done)

    def probe(self, path: str, on_result) -> None:
        self._seq += 1
        token = self._seq
        self._waiting[token] = (path, on_result)

        def work() -> None:
            try:
                found = os.path.exists(path)
            except OSError:
                found = False
            self.done.emit(token, found)

        threading.Thread(target=work, daemon=True).start()

    def _on_done(self, token: int, found: bool) -> None:
        item = self._waiting.pop(token, None)
        if item is None:
            return
        path, on_result = item
        try:
            on_result(path, found)
        except RuntimeError:
            # 기다리는 사이에 메모 창이 닫혔다.
            pass


_prober: _Prober | None = None


def _get_prober() -> _Prober:
    global _prober
    if _prober is None:
        _prober = _Prober()
    return _prober


def _warn(parent: QWidget | None, title: str, body: str) -> None:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle(title)
    box.setText(body)
    box.addButton(i18n.t("link.btn_ok"), QMessageBox.AcceptRole)
    box.exec()


def confirm_run(parent: QWidget | None, path: str) -> bool:
    """실행 파일·스크립트를 열기 전에 실제 경로를 보여 주고 묻는다. 기본 버튼은
    취소다(Enter 한 번에 실행되지 않게)."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle(i18n.t("link.confirm_run_title"))
    box.setText(i18n.t("link.confirm_run_body", path=path))
    run = box.addButton(i18n.t("link.btn_run"), QMessageBox.AcceptRole)
    cancel = box.addButton(i18n.t("link.btn_cancel"), QMessageBox.RejectRole)
    box.setDefaultButton(cancel)
    box.setEscapeButton(cancel)
    box.exec()
    return box.clickedButton() is run


def open_link(parent: QWidget | None, href: str) -> None:
    """링크를 연다. 지원하지 않는 주소는 열지 않고 알린다."""
    if fl.is_web(href):
        QDesktopServices.openUrl(QUrl(href))
        return
    path = fl.href_to_path(href)
    if not path:
        _warn(parent, i18n.t("link.msg_title"),
              i18n.t("link.msg_unsupported", target=href))
        return
    if fl.needs_confirm(href) and not confirm_run(parent, path):
        return

    def on_result(found_path: str, found: bool) -> None:
        if not found:
            hint = (i18n.t("link.msg_missing_local")
                    if fl.is_local_drive(found_path)
                    else i18n.t("link.msg_missing_network"))
            _warn(parent, i18n.t("link.msg_title"),
                  i18n.t("link.msg_missing", path=found_path) + "\n\n" + hint)
            return
        try:
            os.startfile(found_path)  # noqa: S606 - 사용자가 누른 바로 가기를 연다
        except OSError as exc:
            _warn(parent, i18n.t("link.msg_title"),
                  i18n.t("link.msg_open_failed", path=found_path,
                         error=str(exc)))

    _get_prober().probe(path, on_result)


def reveal_in_folder(parent: QWidget | None, href: str) -> None:
    """파일이 든 폴더를 열고 그 파일을 골라 보인다. 폴더 링크는 그 폴더를 연다."""
    path = fl.href_to_path(href)
    if not path:
        return
    if fl.is_folder_href(href):
        open_link(parent, href)
        return

    def on_result(found_path: str, found: bool) -> None:
        if not found:
            _warn(parent, i18n.t("link.msg_title"),
                  i18n.t("link.msg_missing", path=found_path))
            return
        try:
            subprocess.Popen(["explorer", "/select,", found_path])  # noqa: S603, S607
        except OSError as exc:
            _warn(parent, i18n.t("link.msg_title"),
                  i18n.t("link.msg_open_failed", path=found_path,
                         error=str(exc)))

    _get_prober().probe(path, on_result)
