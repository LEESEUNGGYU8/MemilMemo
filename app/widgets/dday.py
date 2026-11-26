"""D-day: 날짜 하나와 이름. 오늘에서 그 날까지를 "D-12", "D-day", "D+3" 으로 보인다.

데이터
    label: 이름, date: "YYYY-MM-DD"(비어 있으면 날짜 없음)

동작
    set {"label": 글, "date": "YYYY-MM-DD" 또는 ""}   누구나(메모 글을 고치는 것과 같다).
                                                      준 칸만 바꾼다.
    set_bg {"bg": white|clear}                         누구나.

오늘은 그리는 PC 가 넘긴다(``today``). 데이터에는 남지 않는다."""
from __future__ import annotations

from datetime import date

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QFont, QPainter

from . import draw
from .base import E_INVALID, Hit, WidgetKind, as_date, clean_text, register
from .texts import format_date, tr

_H = 40


def dday_label(target: date, today: date) -> str:
    """'D-12', 'D-day', 'D+3'."""
    days = (target - today).days
    if days > 0:
        return f"D-{days}"
    if days == 0:
        return "D-day"
    return f"D+{-days}"


class DDay(WidgetKind):
    kind = "dday"
    bg_option = True

    def _new(self, label: str = "", date: str = "", **_ignored) -> dict:  # noqa: A002
        return {"label": label, "date": date}

    def _clean(self, data: dict) -> dict:
        d = as_date(data.get("date"))
        return {"label": clean_text(data.get("label")), "date": d.isoformat() if d else ""}

    def _apply(self, data: dict, name: str, op: dict, user: str, now_ms: int) -> str | None:
        if name == "set":
            if "label" not in op and "date" not in op:
                return E_INVALID
            if "label" in op:
                if not isinstance(op["label"], str):
                    return E_INVALID
                data["label"] = clean_text(op["label"])
            if "date" in op:
                raw = op["date"]
                if raw == "":
                    data["date"] = ""
                else:
                    d = as_date(raw)
                    if d is None:
                        return E_INVALID
                    data["date"] = d.isoformat()
            return None
        return super()._apply(data, name, op, user, now_ms)

    def _texts(self, data: dict) -> list[str]:
        return [data["label"], data["date"]]

    # ── 그리기 ──

    _BODY = 44

    def measure(self, data, width, ctx) -> int:
        return draw.PAD + self._BODY + draw.PAD

    def paint(self, p: QPainter, data, width, ctx, pal) -> list[Hit]:
        x0 = draw.PAD
        top = draw.head_y()
        target = as_date(data["date"])
        big = draw.font(26, QFont.Bold)
        label_f, small = draw.bold(14), draw.font(12)
        hits: list[Hit] = []
        if target is not None:
            head = dday_label(target, ctx.today)
            hw = min(draw.text_width(big, head) + 2, (width - 2 * x0) * 0.5)
            draw.text(p, x0, draw.PAD - 6, hw, self._BODY + 12, head, pal.ink, big)
            tx = x0 + hw + 16
            when = (format_date(target, short=True) if target.year == ctx.today.year
                    else format_date(target))
        else:
            tx = x0
            when = tr("dday.no_date")
        name = data["label"]
        # 첫 줄('⋯' 와 같은 높이)은 '⋯' 앞까지, 둘째 줄은 카드 오른쪽 여백까지.
        first_w = max(0.0, draw.content_right(width) - tx)
        second_w = max(0.0, width - draw.PAD - tx)
        if ctx.interactive and ctx.viewer:
            # 이름과 날짜를 누르면 고치는 칸이 열린다.
            action = {"local": "setup"}
            area = QRectF(tx - 6, top, first_w + 6, draw.HEAD + 20)
            draw.hover_fill(p, area, pal, draw.state(ctx, action))
            hits.append(Hit(area.x(), area.y(), area.width(), area.height(), action))
        if name:
            draw.text(p, tx, top, first_w, draw.HEAD, name, pal.ink, label_f)
            draw.text(p, tx, top + draw.HEAD - 2, second_w, 18, when, pal.muted, small)
        else:
            draw.text(p, tx, top + 9, first_w, draw.HEAD, when,
                      pal.muted if target is None else pal.ink,
                      draw.font() if target is None else label_f)
        return hits


DDAY = register(DDay())
