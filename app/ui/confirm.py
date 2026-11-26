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
from . import window_frame
from .theme import refresh_style

# 글 한 줄의 최대 폭(px). 이보다 긴 글만 줄을 나눈다.
MAX_LINE_WIDTH = 400
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|(?<=[。！？])\s*")
_CLAUSE_END = re.compile(r"(?<=[、，])")
_PATH_STEP = re.compile(r"(?<=[\\/])")
_KANA = re.compile("[\u3040-\u30ff]")


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

    띄어 쓰는 글이면 낱말(한글은 그대로 두면 낱말 가운데서 끊긴다). 일본어는
    띄어 쓰지 않으므로('共有 PC' 같은 빈칸이 있어도) 쉼표로 끝나는 마디로 본다.
    나눌 곳이 없으면 빈 목록."""
    words = text.split(" ")
    if len(words) > 1 and not _KANA.search(text):
        return words, " "
    clauses = [c for c in _CLAUSE_END.split(text) if c]
    if len(clauses) > 1:
        return clauses, ""
    # 파일 경로는 폴더 이름 사이에서 나눈다(이름 한가운데서 끊기지 않게).
    steps = [c for c in _PATH_STEP.split(text) if c]
    return (steps, "") if len(steps) > 2 else ([], "")


def _anywhere(text: str, font: QFont, width: int) -> list[str]:
    """나눌 조각이 없거나 조각 하나가 폭보다 긴 글(일본어 문장, 파일 경로)을
    Qt 의 규칙으로 나눈다."""
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


def _narrowest(lines_at, low: int, width: int) -> list[str]:
    """줄 수가 늘지 않는 가장 좁은 폭에서 나눈 줄들(줄 길이가 고르게 된다)."""
    count = len(lines_at(width))
    high = width
    while low < high:
        mid = (low + high) // 2
        if len(lines_at(mid)) <= count:
            high = mid
        else:
            low = mid + 1
    return lines_at(high)


def _balance(text: str, font: QFont, width: int) -> list[str]:
    """한 줄보다 긴 문장을 나눈다. 줄 수는 가장 적게, 줄 길이는 고르게 한다
    (마지막 줄에 낱말 하나만 남지 않게)."""
    fm = QFontMetrics(font)
    parts, joiner = _units(text)
    widest = max((fm.horizontalAdvance(p) for p in parts), default=width + 1)
    if widest > width:
        return _narrowest(lambda w: _anywhere(text, font, w), 40, width)
    return _narrowest(lambda w: _fit(parts, joiner, fm, w), widest, width)


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
    # 글이 한두 줄뿐이면 아이콘보다 낮아 위쪽에 붙어 보인다. 아이콘의 가운데에
    # 맞춘다(아래에 덧붙이는 글이 있을 때는 첫 줄이 아이콘의 위와 나란하다).
    icon = box.findChild(QLabel, "qt_msgboxex_icon_label")
    main = box.findChild(QLabel, "qt_msgbox_label")
    if (icon is not None and main is not None and len(labels) == 1
            and labels[0] is main and not icon.pixmap().isNull()):
        tall = icon.sizeHint().height()
        lines = len(main.text().splitlines()) or 1
        if lines * QFontMetrics(main.font()).lineSpacing() <= tall:
            main.setMinimumHeight(tall)
            main.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)


def frame_box(box: QMessageBox) -> None:
    """확인창에 앱의 상단바(닫기만)를 입힌다. 묻는 말이 곧 제목이라 제목은 그리지
    않는다. 확인창은 글을 바꿀 때마다 레이아웃을 새로 만든다. 상단바 자리는
    레이아웃의 여백이 아니라 창의 안쪽 여백으로 비워 둔다."""
    if window_frame.bar_of(box) is not None:
        return
    bar = window_frame.install(box, window_frame.BARE)
    lay = box.layout()
    if lay is not None:
        m = lay.contentsMargins()
        lay.setContentsMargins(m.left(), max(0, m.top() - bar.height()),
                               m.right(), m.bottom())
    box.setContentsMargins(0, bar.height(), 0, 0)


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
    frame_box(box)
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
    frame_box(box)
    return box


def notify(parent: QWidget | None, title: str, text: str, *,
           icon: QMessageBox.Icon = QMessageBox.Information,
           detail: str = "") -> None:
    """확인 단추 하나만 있는 알림창."""
    build_notify(parent, title, text, icon=icon, detail=detail).exec()
