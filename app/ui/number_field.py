"""숫자를 적는 칸(설정과 접속 창의 글꼴 크기, 움직임 속도, 포트, 연결 확인 주기 등).

Qt 의 숫자 칸을 그대로 쓰면 치기가 어색했다.

- 단위를 칸 안에 적으면('1.0 배') 숫자를 고르거나 지울 때 단위가 걸리적거린다.
  단위는 칸 밖에 따로 적는다(``with_unit``).
- 범위를 벗어난 숫자는 치는 도중에 조용히 막혔다(0.5 ~ 2.0 인 칸에 3 이 안 쳐진다).
  치는 동안에는 막지 않고, 다 친 뒤에 가장 가까운 값으로 맞춘다.
- 숫자와 점이 아닌 글자는 들어가지 않는다. 전각 숫자는 반각으로 바꿔 받는다.
- 칸에 들어가면 값 전체가 골라져 있어 바로 새 값을 칠 수 있다.
- 마우스 휠은 칸에 들어가 있을 때만 값을 바꾼다(설정 창을 굴리다가 값이 바뀌지
  않게).

소수점은 화면 언어와 무관하게 늘 점이다."""
from __future__ import annotations

import unicodedata

from PySide6.QtCore import QLocale, Qt, QTimer
from PySide6.QtGui import QValidator
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QWidget,
)

_DIGITS = "0123456789"


def typed_number(text: str, decimals: int = 0) -> str:
    """친 글에서 숫자와(소수가 있는 칸이면) 점 하나만 남긴다. 소수는 ``decimals``
    자리까지만 받는다."""
    plain = unicodedata.normalize("NFKC", text or "")
    out: list[str] = []
    dotted, places = False, 0
    for ch in plain:
        if ch in _DIGITS:
            if dotted:
                if places >= decimals:
                    continue
                places += 1
            out.append(ch)
        elif ch == "." and decimals > 0 and not dotted:
            dotted = True
            out.append(ch)
    return "".join(out)


class _Typing:
    """두 숫자 칸이 함께 쓰는 것."""

    _decimals = 0

    def _prepare(self) -> None:
        self.setLocale(QLocale.c())
        self.setGroupSeparatorShown(False)
        self.setCorrectionMode(QAbstractSpinBox.CorrectToNearestValue)
        self.setKeyboardTracking(False)
        self.setFocusPolicy(Qt.StrongFocus)
        # 숫자와 점뿐이다. 한글 입력 상태여도 그대로 쳐지게 한다.
        self.lineEdit().setAttribute(Qt.WA_InputMethodEnabled, False)

    def _parse(self, text: str):
        raise NotImplementedError

    def validate(self, text: str, pos: int):
        kept = typed_number(text, self._decimals)
        at = len(typed_number(text[:pos], self._decimals))
        value = self._parse(kept)
        inside = value is not None and self.minimum() <= value <= self.maximum()
        state = QValidator.Acceptable if inside else QValidator.Intermediate
        return state, kept, min(at, len(kept))

    def fixup(self, text: str) -> str:
        """다 친 값을 범위 안의 가장 가까운 값으로 맞춘다. 비어 있으면 있던 값."""
        value = self._parse(typed_number(text, self._decimals))
        if value is None:
            value = self.value()
        value = max(self.minimum(), min(self.maximum(), value))
        return self.textFromValue(value)

    def valueFromText(self, text: str):  # noqa: N802
        value = self._parse(typed_number(text, self._decimals))
        return self.value() if value is None else value

    def focusInEvent(self, event) -> None:  # noqa: N802
        super().focusInEvent(event)
        # 누르는 동작이 끝난 뒤에 고른다(누른 자리로 커서가 옮겨지며 풀리지 않게).
        QTimer.singleShot(0, self.selectAll)

    def wheelEvent(self, event) -> None:  # noqa: N802
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class IntField(_Typing, QSpinBox):
    """정수를 적는 칸."""

    def __init__(self, low: int, high: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.setRange(low, high)
        self._prepare()

    def _parse(self, text: str):
        try:
            return int(text)
        except ValueError:
            return None


class DecimalField(_Typing, QDoubleSpinBox):
    """소수를 적는 칸."""

    def __init__(self, low: float, high: float, step: float, decimals: int = 1,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self._decimals = decimals
        self.setDecimals(decimals)
        self.setRange(low, high)
        self.setSingleStep(step)
        self._prepare()

    def _parse(self, text: str):
        try:
            return float(text)
        except ValueError:
            return None


def with_unit(field: QWidget, unit: str) -> QWidget:
    """숫자 칸과, 그 오른쪽에 따로 적는 단위. 칸 안에는 숫자만 남는다."""
    box = QWidget()
    row = QHBoxLayout(box)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(6)
    row.addWidget(field, 1)
    label = QLabel(unit.strip())
    label.setObjectName("FieldUnit")
    row.addWidget(label)
    box.unit_label = label
    return box


__all__ = ["DecimalField", "IntField", "typed_number", "with_unit"]
