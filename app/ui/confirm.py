"""확인창과 알림창.

Qt 기본 창은 단추 글자가 화면 언어와 무관하게 Yes/No/OK 로 나오고, 지우는
확인창의 기본 단추가 '예'라서 Enter 한 번에 지워진다. 여기서는 단추에 하는
일을 그대로 적고(삭제, 되돌리기), 기본 단추를 늘 '취소'로 둔다.

줄도 여기서 나눈다. Qt 에 맡기면 창의 폭이 첫 글에 맞춰져, 그 아래 글이 낱말
하나만 다음 줄로 넘어가곤 한다."""
from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontMetrics, QTextLayout, QTextOption
from PySide6.QtWidgets import QLabel, QMessageBox, QPushButton, QWidget

from .. import i18n
from .theme import refresh_style

# 글 한 줄의 최대 폭(px). 이보다 긴 글만 줄을 나눈다.
MAX_LINE_WIDTH = 400
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|(?<=[。！？])\s*")
_CLAUSE_END = re.compile(r"(?<=[、，])")


def _fit(parts: list[str], joiner: str, fm: QFontMetrics, width: int) -> list[str]:
    """조각들을 차례로 이어 붙이되 한 줄이 ``width`` 를 넘지 않게 한다."""
    lines: list[str] = []
    for part in parts:
        if lines and fm.horizontalAdvance(lines[-1] + joiner + part) <= width:
            lines[-1] += joiner + part
        else:
            lines.append(part)
    return lines


def _units(text: str) -> tuple[list[str], str]:
    """한 문장을 나눠도 되는 조각들과, 조각을 다시 이을 때 넣는 글자.

    띄어쓰기가 있으면 낱말(한글은 그대로 두면 낱말 가운데서 끊긴다), 없으면
    쉼표로 끝나는 마디(일본어). 나눌 곳이 없으면 빈 목록."""
    words = text.split(" ")
    if len(words) > 1:
        return words, " "
    clauses = [c for c in _CLAUSE_END.split(text) if c]
    return (clauses, "") if len(clauses) > 1 else ([], "")


def _anywhere(text: str, font: QFont, width: int) -> list[str]:
    """나눌 곳이 없는 글(파일 경로)을 Qt 의 규칙으로 나눈다."""
    layout = QTextLayout(text, font)
    option = QTextOption()
    option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
    layout.setTextOption(option)
    layout.beginLayout()
    lines: list[str] = []
    while True:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(width)
        lines.append(text[line.textStart():line.textStart() + line.textLength()].strip())
    layout.endLayout()
    return [ln for ln in lines if ln] or [text]


def _balance(text: str, font: QFont, width: int) -> list[str]:
    """한 줄보다 긴 문장을 나눈다. 줄 수는 가장 적게, 줄 길이는 고르게 한다
    (마지막 줄에 낱말 하나만 남지 않게)."""
    fm = QFontMetrics(font)
    parts, joiner = _units(text)
    widest = max((fm.horizontalAdvance(p) for p in parts), default=width + 1)
    if widest > width:
        return _anywhere(text, font, width)
    count = len(_fit(parts, joiner, fm, width))
    low, high = widest, width
    while low < high:
        mid = (low + high) // 2
        if len(_fit(parts, joiner, fm, mid)) <= count:
            high = mid
        else:
            low = mid + 1
    return _fit(parts, joiner, fm, high)


def break_lines(text: str, font: QFont, width: int = MAX_LINE_WIDTH) -> str:
    """글을 보기 좋게 나눠 줄바꿈을 넣어 돌려준다.

    한 줄에 들어가면 그대로 둔다. 넘치면 문장이 끝나는 곳에서 먼저 나누고,
    한 문장이 한 줄보다 길 때만 그 문장 안에서 줄 길이를 고르게 나눈다.
    글에 이미 든 줄바꿈은 그대로 둔다."""
    fm = QFontMetrics(font)
    out: list[str] = []
    for paragraph in text.split("\n"):
        if fm.horizontalAdvance(paragraph) <= width:
            out.append(paragraph)
            continue
        sentences = [s for s in _SENTENCE_END.split(paragraph) if s]
        joiner = " " if " " in paragraph else ""
        for line in _fit(sentences, joiner, fm, width):
            if fm.horizontalAdvance(line) <= width:
                out.append(line)
            else:
                out.extend(_balance(line, font, width))
    return "\n".join(out)


def arrange(box: QMessageBox) -> None:
    """확인창의 글을 보기 좋게 나누고, 나눈 줄이 다시 접히지 않게 폭을 잡는다."""
    labels = [label for name in ("qt_msgbox_label", "qt_msgbox_informativelabel")
              if (label := box.findChild(QLabel, name)) is not None and label.text()]
    widest = 0
    for label in labels:
        label.ensurePolished()
        text = break_lines(label.text(), label.font())
        if text != label.text():
            label.setText(text)
        fm = QFontMetrics(label.font())
        widest = max([widest] + [fm.horizontalAdvance(ln) for ln in text.split("\n")])
    for label in labels:
        label.setMinimumWidth(widest + 4)


def build_ask(parent: QWidget | None, title: str, text: str, *,
              action: str, detail: str = "", cancel: str = "",
              danger: bool = False
              ) -> tuple[QMessageBox, QPushButton, QPushButton]:
    """확인창을 만들어 (창, 실행 단추, 취소 단추)를 돌려준다."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning if danger else QMessageBox.Question)
    box.setWindowTitle(title)
    # 메모 제목, 폴더 이름, 닉네임이 문구에 들어간다. 글자 그대로 보인다.
    box.setTextFormat(Qt.PlainText)
    box.setText(text)
    if detail:
        box.setInformativeText(detail)
    act = box.addButton(action, QMessageBox.DestructiveRole if danger
                        else QMessageBox.AcceptRole)
    can = box.addButton(cancel or i18n.t("common.btn_cancel"),
                        QMessageBox.RejectRole)
    if danger:
        act.setProperty("danger", True)
    can.setProperty("secondary", True)
    # 단추는 만들어질 때 이미 모양이 정해진다. 속성을 붙인 뒤 다시 입힌다.
    refresh_style(act)
    refresh_style(can)
    # 실수로 누른 Enter 나 Esc 가 실행으로 이어지지 않게 한다.
    box.setDefaultButton(can)
    box.setEscapeButton(can)
    arrange(box)
    return box, act, can


def ask(parent: QWidget | None, title: str, text: str, *, action: str,
        detail: str = "", cancel: str = "", danger: bool = False) -> bool:
    """실행 단추를 눌렀으면 True."""
    box, act, _ = build_ask(parent, title, text, action=action, detail=detail,
                            cancel=cancel, danger=danger)
    box.exec()
    return box.clickedButton() is act


def build_notify(parent: QWidget | None, title: str, text: str, *,
                 icon: QMessageBox.Icon = QMessageBox.Information,
                 detail: str = "") -> QMessageBox:
    box = QMessageBox(parent)
    box.setIcon(icon)
    box.setWindowTitle(title)
    box.setTextFormat(Qt.PlainText)
    box.setText(text)
    if detail:
        box.setInformativeText(detail)
    ok = box.addButton(i18n.t("main.btn_ok"), QMessageBox.AcceptRole)
    ok.setMinimumWidth(96)
    arrange(box)
    return box


def notify(parent: QWidget | None, title: str, text: str, *,
           icon: QMessageBox.Icon = QMessageBox.Information,
           detail: str = "") -> None:
    """확인 단추 하나만 있는 알림창."""
    build_notify(parent, title, text, icon=icon, detail=detail).exec()
