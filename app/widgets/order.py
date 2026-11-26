"""주문 모으기: 메뉴와 사람마다의 주문, 메뉴별 합계("김치찌개 2 (지우, 서연)").

데이터
    menu: [메뉴...], orders: {이름: {메뉴: 수량}}, closed: 마감

동작
    set_my_order {"item": 메뉴, "qty": 0~99}   누구나(자기 줄만). 0 이면 그 메뉴를 뺀다.
                                               없는 메뉴면 메뉴 끝에 더한다.
    clear_my_order                              누구나(자기 줄만).
    close, reopen                               만든 사람만.
    set_menu {"menu": [메뉴...]}                만든 사람만. 빠진 메뉴의 주문도 지운다.
    마감 뒤에는 주문을 바꿀 수 없다.

주문은 이름 차례, 한 사람의 메뉴는 메뉴 차례로 둔다. 다른 사람의 주문 둘이 어느
차례로 와도 결과가 같다."""
from __future__ import annotations

import itertools

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QPainter, QPen

from . import draw
from .base import (E_CLOSED, E_INVALID, E_LIMIT, E_NOT_ALLOWED, Hit, WidgetKind, as_bool,
                   as_int, clean_name, clean_text, register, take)
from .texts import format_number, tr

MAX_ITEMS = 30
MAX_PEOPLE = 100
MAX_QTY = 99
MAX_ITEM_TEXT = 60

_ROW = 32


class Order(WidgetKind):
    kind = "order"

    def _new(self, menu=(), **_ignored) -> dict:
        return {"menu": list(menu)[:MAX_ITEMS], "orders": {}, "closed": False}

    def _clean(self, data: dict) -> dict:
        menu: list[str] = []
        for raw in take(data.get("menu"), MAX_ITEMS):
            item = clean_text(raw, MAX_ITEM_TEXT)
            if item and item not in menu:
                menu.append(item)
        orders: dict[str, dict[str, int]] = {}
        raw_orders = data.get("orders")
        if isinstance(raw_orders, dict):
            for who, lines in itertools.islice(raw_orders.items(), MAX_PEOPLE):
                name = clean_name(who)
                if not name or not isinstance(lines, dict):
                    continue
                mine: dict[str, int] = {}
                for item, qty in itertools.islice(lines.items(), MAX_ITEMS):
                    item = clean_text(item, MAX_ITEM_TEXT)
                    qty = as_int(qty, 1, MAX_QTY)
                    if item in menu and qty is not None:
                        mine[item] = qty
                if mine:
                    orders[name] = {m: mine[m] for m in menu if m in mine}
        return {"menu": menu, "orders": {k: orders[k] for k in sorted(orders)},
                "closed": as_bool(data.get("closed"))}

    def _apply(self, data: dict, name: str, op: dict, user: str, now_ms: int) -> str | None:
        if name == "set_my_order":
            if data["closed"]:
                return E_CLOSED
            item = clean_text(op.get("item"), MAX_ITEM_TEXT)
            qty = as_int(op.get("qty"), 0, MAX_QTY)
            if not item or qty is None:
                return E_INVALID
            mine = dict(data["orders"].get(user, {}))
            if qty == 0:
                mine.pop(item, None)
            else:
                if item not in data["menu"]:
                    if len(data["menu"]) >= MAX_ITEMS:
                        return E_LIMIT
                    data["menu"].append(item)
                if user not in data["orders"] and len(data["orders"]) >= MAX_PEOPLE:
                    return E_LIMIT
                mine[item] = qty
            if mine:
                data["orders"][user] = mine
            else:
                data["orders"].pop(user, None)
            return None
        if name == "clear_my_order":
            if data["closed"]:
                return E_CLOSED
            data["orders"].pop(user, None)
            return None
        if name in ("close", "reopen"):
            if not self.is_owner(data, user):
                return E_NOT_ALLOWED
            data["closed"] = name == "close"
            return None
        if name == "set_menu":
            if not self.is_owner(data, user):
                return E_NOT_ALLOWED
            raw = op.get("menu")
            if not isinstance(raw, list) or len(raw) > MAX_ITEMS:
                return E_LIMIT if isinstance(raw, list) else E_INVALID
            data["menu"] = raw
            return None
        return super()._apply(data, name, op, user, now_ms)

    def _texts(self, data: dict) -> list[str]:
        ordered = item_totals(data)
        return summary_lines(data) + [m for m in data["menu"] if not ordered.get(m)]

    # ── 그리기 ──

    _ROW = 38

    _ADD = {"local": "input", "purpose": "order_item", "hint": "order.add"}

    def _can_order(self, data: dict, ctx) -> bool:
        return ctx.interactive and bool(ctx.viewer) and not data["closed"]

    def _can_add(self, data: dict, ctx) -> bool:
        return self._can_order(data, ctx) and len(data["menu"]) < MAX_ITEMS

    _NAMES = 16

    def _row_h(self, data: dict, item: str) -> int:
        return self._ROW + (self._NAMES if who_ordered(data, item) else 0)

    def measure(self, data, width, ctx) -> int:
        y = draw.head_y() + draw.HEAD + 4
        y += sum(self._row_h(data, m) for m in data["menu"]) if data["menu"] else 28
        if self._can_add(data, ctx):
            y += 4 + 30
        return int(y + 12 + draw.BTN_H + draw.PAD)

    def input_op(self, data: dict, purpose: str, text: str, viewer: str) -> dict | None:
        item = clean_text(text, MAX_ITEM_TEXT)
        if purpose != "order_item" or not item:
            return None
        mine = data["orders"].get(viewer, {})
        return {"op": "set_my_order", "item": item, "qty": min(MAX_QTY, mine.get(item, 0) + 1)}

    def paint(self, p: QPainter, data, width, ctx, pal) -> list[Hit]:
        hits: list[Hit] = []
        x0 = draw.PAD
        inner = width - 2 * draw.PAD
        f, b, small = draw.font(), draw.bold(), draw.font(12)
        top = draw.head_y()
        draw.text(p, x0, top, draw.content_right(width) - x0, draw.HEAD, tr("kind.order"),
                  pal.ink, draw.bold(14))
        can_order = self._can_order(data, ctx)
        mine = data["orders"].get(ctx.viewer, {}) if ctx.viewer else {}
        totals = item_totals(data)
        y = top + draw.HEAD + 4
        if not data["menu"]:
            draw.text(p, x0, y, inner, 28, tr("order.empty"), pal.muted, f)
            y += 28
        for i, item in enumerate(data["menu"]):
            row = self._row_h(data, item)
            people = who_ordered(data, item)
            line_h = self._ROW if not people else self._ROW - 12
            if i:
                draw.hline(p, x0, x0 + inner, y + 0.5, pal.line)
            right = x0 + inner
            if can_order:
                qty = mine.get(item, 0)
                bx = right - 28
                plus = {"op": "set_my_order", "item": item, "qty": min(MAX_QTY, qty + 1)}
                minus = {"op": "set_my_order", "item": item, "qty": max(0, qty - 1)}
                by = y + (row - 28) / 2
                hits.append(Hit(*draw.icon_button(p, bx, by, "plus", pal, ctx=ctx, action=plus,
                                                  enabled=qty < MAX_QTY, border=True, size=28),
                                plus))
                draw.text(p, bx - 30, y, 30, row, str(qty), pal.ink if qty else pal.muted,
                          b if qty else f, Qt.AlignCenter, elide=False)
                if qty > 0:
                    hits.append(Hit(*draw.icon_button(p, bx - 58, by, "minus", pal, ctx=ctx,
                                                      action=minus, border=True, size=28),
                                    minus))
                else:
                    draw.icon_button(p, bx - 58, by, "minus", pal, enabled=False, border=True,
                                     size=28)
                right = bx - 58 - 12
            total = totals.get(item, 0)
            tw = draw.text_width(b, format_number(total)) + 1
            iw = min(draw.text_width(f, item) + 1, max(40.0, right - x0 - tw - 8))
            ty = y + (4 if people else 0)
            draw.text(p, x0, ty, iw, line_h, item, pal.ink, f)
            tx = x0 + iw + 8
            draw.text(p, tx, ty, tw, line_h, format_number(total),
                      pal.ink if total else pal.muted, b, elide=False)
            if people:
                # 시킨 사람은 메뉴 밑 줄에(메뉴 이름을 덜 줄인다).
                draw.text(p, x0, ty + line_h - 6, right - x0, self._NAMES, people, pal.muted,
                          small)
            y += row
        if self._can_add(data, ctx):
            # 누구나 메뉴에 없는 것을 적어 시킬 수 있다(적은 칸은 위젯 칸이 띄운다).
            y += 4
            label = "+ " + tr("order.add")
            w = draw.text_width(small, label) + 16
            r = QRectF(x0 - 6, y, w, 30)
            draw.hover_fill(p, r, pal, draw.state(ctx, self._ADD))
            draw.text(p, x0 + 2, y, w - 8, 30, label, pal.muted, small, elide=False)
            hits.append(Hit(r.x(), r.y(), r.width(), r.height(), dict(self._ADD)))
            y += 30
        y += 12
        foot = tr("order.total", n=format_number(sum(totals.values())))
        if data["closed"]:
            foot += ", " + tr("common.closed")
        bx = x0 + inner
        if ctx.interactive and ctx.viewer and self.is_owner(data, ctx.viewer):
            label = tr("common.reopen") if data["closed"] else tr("common.close")
            action = {"op": "reopen" if data["closed"] else "close"}
            bx -= draw.button_width(label)
            hits.append(Hit(*draw.button(p, bx, y, label, pal, ctx=ctx, action=action), action))
            bx -= 8
        if can_order and mine:
            label = tr("order.clear_mine")
            action = {"op": "clear_my_order"}
            # 좁으면 모두 몇 개인지를 먼저 보인다(내 주문은 − 로도 뺄 수 있다).
            if bx - draw.button_width(label) - 8 - x0 >= draw.text_width(small, foot) + 4:
                bx -= draw.button_width(label)
                hits.append(Hit(*draw.button(p, bx, y, label, pal, ctx=ctx, action=action),
                                action))
                bx -= 8
        draw.text(p, x0, y, bx - x0, draw.BTN_H, foot, pal.muted, small)
        return hits


def item_totals(data: dict) -> dict[str, int]:
    totals = {item: 0 for item in data["menu"]}
    for lines in data["orders"].values():
        for item, qty in lines.items():
            totals[item] = totals.get(item, 0) + qty
    return totals


def who_ordered(data: dict, item: str) -> str:
    """'지우, 서연 2' 꼴(둘 이상 시킨 사람은 수량을 붙인다)."""
    out = []
    for who, lines in data["orders"].items():
        qty = lines.get(item, 0)
        if qty:
            out.append(who if qty == 1 else f"{who} {qty}")
    return ", ".join(out)


def summary_lines(data: dict) -> list[str]:
    """'김치찌개 2 (지우, 서연)' 꼴의 줄들(주문이 있는 메뉴만)."""
    lines = []
    totals = item_totals(data)
    for item in data["menu"]:
        if totals.get(item):
            lines.append(f"{item} {totals[item]} ({who_ordered(data, item)})")
    return lines


ORDER = register(Order())
