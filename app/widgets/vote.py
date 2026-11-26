"""투표: 질문 하나와 항목들. 하나만 고르기나 여러 개 고르기, 이름 공개나 익명.

데이터
    question: 질문, options: [{"id": 번호, "text": 항목, "voters": [이름...]}],
    multi: 여러 개 고르기, anonymous: 익명, closed: 마감, next_id: 다음 항목 번호

동작
    vote {"option": id}        누구나. 하나만 고르기면 다른 항목의 내 표는 뺀다.
    unvote {"option": id}      누구나(자기 표만).
    close, reopen              만든 사람만.
    add_option {"text": 글}    만든 사람만. 마감 뒤에는 안 된다.
    set_bg {"bg": white|clear}  누구나.

표를 던진 사람 목록은 이름 차례로 둔다. 다른 사람의 표 둘이 어느 차례로 와도 결과가
같다. 익명은 화면에 이름을 보이지 않는다는 뜻이다(누가 어디에 냈는지는 공유 PC 가
알아야 한 사람 한 표를 지킨다. 데이터는 참여 PC 들에도 간다)."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QPainter, QPen

from . import draw
from .base import (E_CLOSED, E_EXISTS, E_INVALID, E_LIMIT, E_NOT_ALLOWED, E_NOT_FOUND,
                   MAX_TEXT, Hit, WidgetKind, as_bool, as_int, clean_name,
                   clean_text, register, take)
from .texts import tr

MAX_OPTIONS = 20
MAX_VOTERS = 1000
_MAX_ID = 1_000_000

_ROW = 34
_NAMES = 20


class Vote(WidgetKind):
    kind = "vote"
    bg_option = True

    def _new(self, question: str = "", options=(), multi: bool = False,
             anonymous: bool = False, **_ignored) -> dict:
        opts = [{"id": i + 1, "text": t, "voters": []}
                for i, t in enumerate(list(options)[:MAX_OPTIONS])]
        return {"question": question, "options": opts, "multi": multi,
                "anonymous": anonymous, "closed": False, "next_id": len(opts) + 1}

    def _clean(self, data: dict) -> dict:
        options = []
        seen_ids: set[int] = set()
        seen_text: set[str] = set()
        for raw in take(data.get("options"), MAX_OPTIONS):
            if not isinstance(raw, dict):
                continue
            oid = as_int(raw.get("id"), 1, _MAX_ID)
            text = clean_text(raw.get("text"))
            if oid is None or oid in seen_ids or not text or text in seen_text:
                continue
            voters = sorted({n for n in (clean_name(v) for v in take(raw.get("voters"), MAX_VOTERS))
                             if n})
            seen_ids.add(oid)
            seen_text.add(text)
            options.append({"id": oid, "text": text, "voters": voters})
        multi = as_bool(data.get("multi"))
        if not multi:
            # 하나만 고르기인데 한 사람이 여럿에 있으면 앞 항목의 표만 남긴다.
            used: set[str] = set()
            for o in options:
                o["voters"] = [v for v in o["voters"] if v not in used]
                used.update(o["voters"])
        top = max(seen_ids, default=0) + 1
        next_id = as_int(data.get("next_id"), 1, _MAX_ID + 1, top)
        return {"question": clean_text(data.get("question")), "options": options,
                "multi": multi, "anonymous": as_bool(data.get("anonymous")),
                "closed": as_bool(data.get("closed")), "next_id": max(next_id, top)}

    def _apply(self, data: dict, name: str, op: dict, user: str, now_ms: int) -> str | None:
        if name in ("vote", "unvote"):
            if data["closed"]:
                return E_CLOSED
            oid = as_int(op.get("option"), 1, _MAX_ID)
            target = next((o for o in data["options"] if o["id"] == oid), None)
            if target is None:
                return E_NOT_FOUND
            if name == "unvote":
                target["voters"] = [v for v in target["voters"] if v != user]
                return None
            if not data["multi"]:
                for o in data["options"]:
                    o["voters"] = [v for v in o["voters"] if v != user]
            if user not in target["voters"]:
                if len(target["voters"]) >= MAX_VOTERS:
                    return E_LIMIT
                target["voters"] = sorted(target["voters"] + [user])
            return None
        if name in ("close", "reopen"):
            if not self.is_owner(data, user):
                return E_NOT_ALLOWED
            data["closed"] = name == "close"
            return None
        if name == "configure":
            # 만든 사람이 질문, 항목, 여러 개 고르기, 이름 감추기를 한 번에 고친다. 글이 같은
            # 항목은 표를 그대로 두고, 빠진 항목은 그 표와 함께 지운다.
            if not self.is_owner(data, user):
                return E_NOT_ALLOWED
            texts: list[str] = []
            for raw in take(op.get("options"), MAX_OPTIONS):
                t = clean_text(raw)
                if t and t not in texts:
                    texts.append(t)
            kept = {o["text"]: o for o in data["options"]}
            options = []
            for t in texts:
                if t in kept:
                    options.append(kept[t])
                    continue
                if data["next_id"] > _MAX_ID:
                    return E_LIMIT
                options.append({"id": data["next_id"], "text": t, "voters": []})
                data["next_id"] += 1
            data["options"] = options
            if "question" in op:
                data["question"] = clean_text(op.get("question"))
            if "multi" in op:
                data["multi"] = as_bool(op.get("multi"))
            if "anonymous" in op:
                data["anonymous"] = as_bool(op.get("anonymous"))
            return None
        if name == "add_option":
            if not self.is_owner(data, user):
                return E_NOT_ALLOWED
            if data["closed"]:
                return E_CLOSED
            text = clean_text(op.get("text"))
            if not text:
                return E_INVALID
            if any(o["text"] == text for o in data["options"]):
                return E_EXISTS
            if len(data["options"]) >= MAX_OPTIONS or data["next_id"] > _MAX_ID:
                return E_LIMIT
            data["options"].append({"id": data["next_id"], "text": text, "voters": []})
            data["next_id"] += 1
            return None
        return super()._apply(data, name, op, user, now_ms)

    def _texts(self, data: dict) -> list[str]:
        return [data["question"]] + [o["text"] for o in data["options"]]

    # ── 계산 ──

    @staticmethod
    def participants(data: dict) -> int:
        return len({v for o in data["options"] for v in o["voters"]})

    @staticmethod
    def my_options(data: dict, user: str) -> list[int]:
        return [o["id"] for o in data["options"] if user and user in o["voters"]]

    # ── 그리기 ──

    _LINE = 30      # 항목 글과 막대
    _NAMES = 18     # 고른 사람 이름 줄

    def _text_w(self, width: int) -> float:
        inner = width - 2 * draw.PAD
        return inner - 26 - draw.text_width(draw.bold(), "000") - 10

    def _lines(self, option: dict, width: int) -> list[str]:
        return draw.wrap_lines(draw.font(), option["text"], self._text_w(width), max_lines=2)

    def _row_h(self, data: dict, option: dict, width: int = 300) -> int:
        named = not data["anonymous"] and option["voters"]
        extra = 20 * (len(self._lines(option, width)) - 1)
        return self._LINE + extra + (self._NAMES if named else 0)

    def _title(self, data: dict) -> str:
        return data["question"] or tr("kind.vote")

    def _title_h(self, data: dict, width: int) -> float:
        """질문은 두 줄까지 줄을 바꾼다. 첫 줄은 머리 줄에 앉는다."""
        w = draw.content_right(width) - draw.PAD
        h = draw.wrapped_height(draw.bold(14), self._title(data), w, max_lines=2)
        return max(float(draw.HEAD), h + (draw.HEAD - draw.metrics(draw.bold(14)).height()))

    def measure(self, data, width, ctx) -> int:
        y = draw.head_y() + self._title_h(data, width) + draw.GAP
        if data["options"]:
            y += sum(self._row_h(data, o, width) for o in data["options"])
            y += 4 * (len(data["options"]) - 1)
        else:
            y += 24
        return int(y + 12 + draw.BTN_H + draw.PAD)

    def paint(self, p: QPainter, data, width, ctx, pal) -> list[Hit]:
        hits: list[Hit] = []
        x0 = draw.PAD
        inner = width - 2 * draw.PAD
        top = draw.head_y()
        th = self._title_h(data, width)
        title_f = draw.bold(14)
        pad_top = (draw.HEAD - draw.metrics(title_f).height()) / 2
        draw.wrapped(p, x0, top + pad_top, draw.content_right(width) - x0, th - pad_top,
                     self._title(data), pal.ink if data["question"] else pal.muted, title_f,
                     max_lines=2)
        y = top + th + draw.GAP
        f, small = draw.font(), draw.font(12)
        if not data["options"]:
            draw.text(p, x0, y, inner, 24, tr("vote.empty"), pal.muted, f)
            y += 24
        total = max(1, self.participants(data))
        mine = set(self.my_options(data, ctx.viewer))
        can_vote = ctx.interactive and not data["closed"] and bool(ctx.viewer)
        for i, o in enumerate(data["options"]):
            if i:
                y += 4
            row_h = self._row_h(data, o, width)
            lines = self._lines(o, width)
            extra = 20 * (len(lines) - 1)
            chosen = o["id"] in mine
            action = {"op": "unvote" if chosen else "vote", "option": o["id"]}
            row = QRectF(x0 - 6, y - 2, inner + 12, row_h + 2)
            if can_vote:
                draw.hover_fill(p, row, pal, draw.state(ctx, action))
                hits.append(Hit(row.x(), row.y(), row.width(), row.height(), action))
            if data["closed"] and not chosen:
                # 마감한 투표는 고를 수 없다는 것이 보이게 빈 표시를 옅게.
                p.setRenderHint(QPainter.Antialiasing)
                p.setPen(QPen(pal.line_strong, 1.5))
                p.setBrush(Qt.NoBrush)
                box = QRectF(x0 + 0.75, y + 2.75, 14.5, 14.5)
                if data["multi"]:
                    p.drawRoundedRect(box, 4, 4)
                else:
                    p.drawEllipse(box)
            else:
                draw.check(p, x0, y + 2, chosen, pal, round_=not data["multi"])
            count = str(len(o["voters"]))
            cw = draw.text_width(draw.bold(), count) + 2
            for i_line, line in enumerate(lines):
                draw.text(p, x0 + 26, y + 20 * i_line, self._text_w(width), 20, line,
                          pal.ink, f)
            draw.text(p, x0 + inner - cw, y, cw, 20, count, pal.ink if chosen else pal.muted,
                      draw.bold() if chosen else f, Qt.AlignRight | Qt.AlignVCenter, elide=False)
            draw.bar(p, x0 + 26, y + 24 + extra, inner - 26, len(o["voters"]) / total, pal,
                     strong=chosen)
            if not data["anonymous"] and o["voters"]:
                draw.text(p, x0 + 26, y + self._LINE + extra, inner - 26, self._NAMES,
                          ", ".join(o["voters"]), pal.muted, small)
            y += row_h
        y += 12
        foot = tr("vote.people", n=self.participants(data))
        if data["closed"]:
            foot += ", " + tr("common.closed")
        right = x0 + inner
        if ctx.interactive and ctx.viewer and self.is_owner(data, ctx.viewer):
            label = tr("common.reopen") if data["closed"] else tr("common.close")
            action = {"op": "reopen" if data["closed"] else "close"}
            bw = draw.button_width(label)
            right -= bw
            hits.append(Hit(*draw.button(p, right, y, label, pal, ctx=ctx, action=action),
                            action))
            right -= 8
        draw.text(p, x0, y, right - x0, draw.BTN_H, foot, pal.muted, small)
        return hits


def _pt(x: float, y: float) -> QPointF:
    return QPointF(x, y)


VOTE = register(Vote())
