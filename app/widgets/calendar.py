"""달력: 한 달 칸, 나라별 공휴일, 켜고 끄는 일정 묶음, 사용자가 넣는 쉬는 날.

데이터
    country: "KR" | "JP" | "US"
    packs: {"holidays": 공휴일(기본 켬), "observances": 기념일, "tax": 세무 신고일}
    days_off: [{"date": "YYYY-MM-DD", "label": 이름}]   사용자가 넣은 쉬는 날(날짜 차례)

동작(누구나. 공유 메모의 달력이면 모두에게 보인다)
    set_pack {"pack": 이름, "on": bool}
    set_country {"country": 나라}
    add_day_off {"date": 날짜, "label": 이름}   있으면 이름만 바꾼다.
    remove_day_off {"date": 날짜}
    set_bg {"bg": white|clear}

보이는 달은 편집기마다 따로 넘긴다(데이터에 없다). 그림(PNG)은 오늘이 든 달이다.
일요일부터 시작하고, 공휴일과 쉬는 날은 빨간 글자, 토요일은 푸른 회색이다."""
from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QPainter, QPen

from . import draw, holidays
from .base import E_INVALID, E_LIMIT, Hit, WidgetKind, as_bool, as_date, clean_text, register, take
from .texts import lang, tr, tr_list

PACKS = ("holidays", "observances", "tax")
DEFAULT_PACKS = {"holidays": True, "observances": False, "tax": False}
MAX_DAYS_OFF = 100
MAX_LIST = 8

_HEAD = 28
_WEEK = 20
_CELL = 30
_LINE = 18

# 일정의 종류(목록에서 이 차례로 놓는다)
HOLIDAY, DAY_OFF, TAX, OBSERVANCE = "holiday", "day_off", "tax", "observance"
_ORDER = {HOLIDAY: 0, DAY_OFF: 1, TAX: 2, OBSERVANCE: 3}


def month_of(d: date) -> date:
    return d.replace(day=1)


def shift_month(month: date, step: int) -> date:
    y, m = divmod(month.month - 1 + step, 12)
    year = max(1901, min(2199, month.year + y))
    return date(year, m + 1, 1)


def first_cell(month: date) -> date:
    """달력 첫 칸(일요일)."""
    return month - timedelta(days=(month.weekday() + 1) % 7)


def month_events(data: dict, month: date, language: str | None = None
                 ) -> list[tuple[date, str, str]]:
    """그 달의 일정: (날짜, 종류, 이름) 를 날짜 차례로."""
    code = language or lang()
    country = data["country"]
    year = month.year
    out: list[tuple[date, str, str]] = []

    def take_month(table: dict[date, str], kind: str) -> None:
        for d, name in table.items():
            if (d.year, d.month) == (year, month.month):
                out.append((d, kind, name))

    if data["packs"]["holidays"]:
        take_month(holidays.holidays(country, year, code), HOLIDAY)
    if data["packs"]["observances"]:
        take_month(holidays.observances(country, year, code), OBSERVANCE)
    if data["packs"]["tax"]:
        take_month(holidays.tax_dates(country, year, code), TAX)
    for item in data["days_off"]:
        d = as_date(item["date"])
        if d is not None and (d.year, d.month) == (year, month.month):
            out.append((d, DAY_OFF, item["label"] or tr("cal.day_off", language=code)))
    out.sort(key=lambda e: (e[0], _ORDER[e[1]]))
    return out


class Calendar(WidgetKind):
    kind = "calendar"
    bg_option = True

    def _new(self, country: str = "KR", packs=None, **_ignored) -> dict:
        return {"country": country, "packs": dict(DEFAULT_PACKS, **(packs or {})),
                "days_off": []}

    def _clean(self, data: dict) -> dict:
        country = data.get("country")
        raw_packs = data.get("packs") if isinstance(data.get("packs"), dict) else {}
        packs = {k: as_bool(raw_packs.get(k), DEFAULT_PACKS[k]) for k in PACKS}
        days: dict[str, str] = {}
        for raw in take(data.get("days_off"), MAX_DAYS_OFF):
            if not isinstance(raw, dict):
                continue
            d = as_date(raw.get("date"))
            if d is not None and d.isoformat() not in days:
                days[d.isoformat()] = clean_text(raw.get("label"), 60)
        return {"country": country if country in holidays.COUNTRIES else "KR", "packs": packs,
                "days_off": [{"date": k, "label": days[k]} for k in sorted(days)]}

    def _apply(self, data: dict, name: str, op: dict, user: str, now_ms: int) -> str | None:
        if name == "set_pack":
            if op.get("pack") not in PACKS or not isinstance(op.get("on"), bool):
                return E_INVALID
            data["packs"][op["pack"]] = op["on"]
            return None
        if name == "set_country":
            if op.get("country") not in holidays.COUNTRIES:
                return E_INVALID
            data["country"] = op["country"]
            return None
        if name == "add_day_off":
            d = as_date(op.get("date"))
            label = op.get("label", "")
            if d is None or not isinstance(label, str):
                return E_INVALID
            key = d.isoformat()
            for item in data["days_off"]:
                if item["date"] == key:
                    item["label"] = clean_text(label, 60)
                    return None
            if len(data["days_off"]) >= MAX_DAYS_OFF:
                return E_LIMIT
            data["days_off"].append({"date": key, "label": clean_text(label, 60)})
            return None
        if name == "remove_day_off":
            d = as_date(op.get("date"))
            if d is None:
                return E_INVALID
            data["days_off"] = [i for i in data["days_off"] if i["date"] != d.isoformat()]
            return None
        return super()._apply(data, name, op, user, now_ms)

    def _texts(self, data: dict) -> list[str]:
        return [i["label"] for i in data["days_off"]]

    # ── 그리기 ──

    _LIST = 3          # 아래 일정 줄 수(달마다 높이가 바뀌지 않게 늘 같다)

    def _month(self, ctx) -> date:
        m = ctx.view.get("month")
        return m if isinstance(m, date) else month_of(ctx.today)

    def _pack_rows(self, data: dict, width: int) -> list[list[tuple[str, float]]]:
        """묶음 켜기 줄(넓이가 모자라면 두 줄)."""
        small = draw.font(12)
        names = [k for k in PACKS if k != "tax" or data["country"] == "KR"]
        rows: list[list[tuple[str, float]]] = [[]]
        used = 0.0
        inner = width - 2 * draw.PAD
        for k in names:
            w = 22 + draw.text_width(small, tr("cal.pack." + k)) + 16
            if rows[-1] and used + w > inner:
                rows.append([])
                used = 0.0
            rows[-1].append((k, w))
            used += w
        return rows

    def measure(self, data, width, ctx) -> int:
        h = draw.head_y() + draw.HEAD + 6 + _WEEK + 6 * _CELL
        h += draw.GAP + 1 + 10 + self._LIST * _LINE
        if ctx.interactive:
            h += 12 + 26 * len(self._pack_rows(data, width))
        return int(h + draw.PAD)

    @staticmethod
    def _missing(data: dict, month: date) -> bool:
        return data["packs"]["holidays"] and not holidays.covered(data["country"], month.year)

    def paint(self, p: QPainter, data, width, ctx, pal) -> list[Hit]:
        hits: list[Hit] = []
        month = self._month(ctx)
        x0 = draw.PAD
        inner = width - 2 * draw.PAD
        small = draw.font(12)
        y = draw.head_y()
        title = tr("cal.month", y=month.year, m=month.month,
                   mon=tr_list("cal.months")[month.month - 1])
        right = draw.content_right(width)
        if ctx.interactive:
            for step, name in ((1, "next"), (-1, "prev")):
                right -= draw.MENU
                action = {"local": "month", "step": step,
                          "tip": "cal.prev" if step < 0 else "cal.next"}
                hits.append(Hit(*draw.icon_button(p, right, y, name, pal, ctx=ctx,
                                                  action=action), action))
                right -= 2
            if month != month_of(ctx.today):
                label = tr("cal.today")
                bw = draw.button_width(label) - 8
                right -= bw + 4
                action = {"local": "month_today"}
                r = QRectF(right, y, bw, draw.MENU)
                draw.hover_fill(p, r, pal, draw.state(ctx, action))
                draw.text(p, right, y, bw, draw.MENU, label, pal.ink, small, Qt.AlignCenter,
                          elide=False)
                hits.append(Hit(r.x(), r.y(), r.width(), r.height(), action))
        if draw.text_width(draw.bold(14), title) > right - x0 - 6:
            title = tr("cal.month_short", y=month.year, m=month.month,
                       mon=tr_list("cal.months")[month.month - 1][:3])
        draw.text(p, x0, y, right - x0 - 6, draw.HEAD, title, pal.ink, draw.bold(14))
        y += draw.HEAD + 6
        cell_w = inner / 7
        names = tr_list("cal.weekdays")
        for col in range(7):
            color = pal.red if col == 0 else pal.saturday if col == 6 else pal.muted
            draw.text(p, x0 + col * cell_w, y, cell_w, _WEEK, names[col], color, small,
                      Qt.AlignCenter, elide=False)
        y += _WEEK
        events = month_events(data, month)
        red_days = {d for d, kind, _n in events if kind in (HOLIDAY, DAY_OFF)}
        dot_days = {d for d, kind, _n in events if kind in (TAX, OBSERVANCE)}
        start = first_cell(month)
        for i in range(42):
            d = start + timedelta(days=i)
            row, col = divmod(i, 7)
            cx = x0 + col * cell_w
            cy = y + row * _CELL
            inside = d.month == month.month
            if not inside:
                color = pal.muted            # 앞뒤 달의 날은 읽히는 회색(칸이 비어 보이지 않게)
            elif d in red_days or col == 0:
                color = pal.red
            elif col == 6:
                color = pal.saturday
            else:
                color = pal.ink
            action = {"local": "day", "date": d, "menu": True}
            if ctx.interactive and inside:
                draw.hover_fill(p, QRectF(cx + cell_w / 2 - 13, cy + 1, 26, 24), pal,
                                draw.state(ctx, action), radius=12)
            if d == ctx.today:
                p.setPen(Qt.NoPen)
                p.setBrush(pal.fill)
                p.drawEllipse(QPointF(cx + cell_w / 2, cy + 13), 11.5, 11.5)
                p.setBrush(Qt.NoBrush)
                color = pal.on_fill
            draw.text(p, cx, cy + 3, cell_w, 20, str(d.day), color,
                      draw.bold(12) if d == ctx.today else small, Qt.AlignCenter, elide=False)
            if inside and d in dot_days:
                p.setPen(Qt.NoPen)
                p.setBrush(pal.muted)
                p.drawEllipse(QPointF(cx + cell_w / 2, cy + 27), 1.8, 1.8)
                p.setBrush(Qt.NoBrush)
            if ctx.interactive and inside:
                hits.append(Hit(cx, cy, cell_w, _CELL, action))
        y += 6 * _CELL + draw.GAP
        # 날짜 칸과 일정 사이를 가는 선으로 나눈다.
        draw.hline(p, x0, x0 + inner, y + 0.5, pal.line)
        y += 1 + 10
        lines: list[tuple[str, str, object]] = []
        if self._missing(data, month):
            lines.append(("", tr("cal.no_table"), pal.muted))
        room = self._LIST - len(lines)
        shown = events if len(events) <= room else events[:room - 1]
        for d, kind, name in shown:
            lines.append((tr("cal.day", d=d.day), name,
                          pal.red if kind in (HOLIDAY, DAY_OFF) else pal.ink))
        if len(events) > len(shown):
            lines.append(("", tr("cal.more", n=len(events) - len(shown)), pal.muted))
        lines_done = False
        if not lines:
            # 일정이 없으면 비워 둔 칸의 가운데에 한 줄.
            y += (self._LIST - 1) * _LINE / 2
            draw.text(p, x0, y, inner, _LINE, tr("cal.none"), pal.muted, small)
            y += _LINE + (self._LIST - 1) * _LINE / 2
            lines_done = True
        day_w = max(draw.text_width(small, tr("cal.day", d=n)) for n in (28, 30, 31)) + 10
        for day, name, color in ([] if lines_done else lines):
            if day:
                draw.text(p, x0, y, day_w, _LINE, day, color, small)
                draw.text(p, x0 + day_w, y, inner - day_w, _LINE, name, pal.ink, small)
            else:
                draw.text(p, x0, y, inner, _LINE, name, color, small)
            y += _LINE
        if not lines_done:
            y += (self._LIST - len(lines)) * _LINE
        if ctx.interactive:
            y += 12
            for row in self._pack_rows(data, width):
                x = x0
                for key, w in row:
                    on = data["packs"][key]
                    action = {"op": "set_pack", "pack": key, "on": not on}
                    r = QRectF(x - 4, y, w - 8, 26)
                    draw.hover_fill(p, r, pal, draw.state(ctx, action))
                    draw.check(p, x, y + 5, on, pal)
                    draw.text(p, x + 22, y, w - 22, 26, tr("cal.pack." + key), pal.ink, small,
                              elide=False)
                    hits.append(Hit(r.x(), r.y(), r.width(), r.height(), action))
                    x += w
                y += 26
        return hits

    # ── 편집기 ──

    def local_action(self, editor, action: dict) -> None:
        if action.get("local") == "month":
            current = editor.view.get("month") or month_of(editor.today)
            editor.view["month"] = shift_month(current, int(action.get("step", 0)))
        elif action.get("local") == "month_today":
            editor.view["month"] = month_of(editor.today)

    def context_actions(self, editor, x: float, y: float):
        data = editor.current()
        items: list[tuple[str, dict | None, bool | None]] = []
        hit = editor.canvas.hit_at(x, y, menu=True)
        if hit is not None and hit.action.get("local") == "day":
            d = hit.action["date"]
            key = d.isoformat()
            if any(i["date"] == key for i in data["days_off"]):
                items.append((tr("cal.remove_day_off"), {"op": "remove_day_off", "date": key},
                              None))
            else:
                items.append((tr("cal.add_day_off"),
                              {"op": "add_day_off", "date": key, "label": ""}, None))
            items.append(("", None, None))
        for code in holidays.COUNTRIES:
            items.append((tr("country." + code), {"op": "set_country", "country": code},
                          data["country"] == code))
        return items


CALENDAR = register(Calendar())
