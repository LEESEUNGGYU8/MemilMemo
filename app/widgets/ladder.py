"""사다리 타기: 참가자와 결과, 공유 PC 가 정한 seed 로 만든 가로줄.

데이터
    participants: [이름...], results: [결과...](참가자 수와 같다),
    started: 시작했나, seed: 정수, rows: 줄 수, rungs: [[줄, 사이], ...]

동작
    set_entries {"participants": [...], "results": [...]}   만든 사람만, 시작 전에만.
    start {"seed": 정수}    누구나(참가자가 둘 이상일 때). seed 는 공유 PC 가
                            :meth:`Ladder.prepare_op` 로 채운다(참여 PC 가 보낸 값은 덮어쓴다).
    reset                   만든 사람만. 가로줄을 지우고 시작 전으로.

가로줄은 시작할 때 seed 로 한 번 만들어 데이터에 적는다. 그래서 앱 버전이 달라도
모두 같은 사다리를 본다. 같은 seed 면 언제나 같은 사다리다(:func:`make_rungs`)."""
from __future__ import annotations

import hashlib
import secrets

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QPainter, QPen, QPolygonF

from . import draw
from .base import (E_INVALID, E_LIMIT, E_NOT_ALLOWED, E_STARTED, Hit, WidgetKind, as_bool,
                   as_int, clean_name, clean_text, register, take)
from .texts import tr

MAX_PEOPLE = 30
MAX_RESULT_TEXT = 40
MIN_ROWS, MAX_ROWS = 8, 24
SEED_MAX = 2 ** 31 - 1
_M64 = (1 << 64) - 1

_HEAD = 22
_GAP = 14
_WHO = 16


class _Rng:
    """splitmix64. 파이썬 버전과 상관없이 같은 수를 낸다."""

    def __init__(self, seed: int) -> None:
        self.state = seed & _M64

    def next(self) -> int:
        self.state = (self.state + 0x9E3779B97F4A7C15) & _M64
        z = self.state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _M64
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _M64
        return z ^ (z >> 31)

    def below(self, n: int) -> int:
        return self.next() % n


def rows_for(n: int) -> int:
    return max(MIN_ROWS, min(MAX_ROWS, 2 * n + 2))


def make_rungs(n: int, rows: int, seed: int) -> list[list[int]]:
    """가로줄 [줄, 사이]. 한 줄에서 이웃한 가로줄은 없고, 모든 사이에 하나 이상 있다."""
    if n < 2:
        return []
    rng = _Rng(seed)
    rungs: list[list[int]] = []
    for r in range(rows):
        last = -2
        for g in range(n - 1):
            if g == last + 1:
                continue
            if rng.below(100) < 45:
                rungs.append([r, g])
                last = g
    used = {g for _r, g in rungs}
    for g in range(n - 1):
        if g in used:
            continue
        start = rng.below(rows)
        for k in range(rows):
            r = (start + k) % rows
            if not any(rr == r and abs(gg - g) <= 1 for rr, gg in rungs):
                rungs.append([r, g])
                break
    return sorted(rungs)


def trace(n: int, rungs: list[list[int]], start: int) -> tuple[int, list[tuple[int, int]]]:
    """``start`` 열에서 내려간 끝 열과 지나간 (줄, 열) 자리들."""
    by_row: dict[int, set[int]] = {}
    for r, g in rungs:
        by_row.setdefault(r, set()).add(g)
    col = start
    steps = [(-1, col)]
    for r in sorted(by_row):
        gaps = by_row[r]
        if col in gaps:
            steps.append((r, col))
            col += 1
            steps.append((r, col))
        elif col - 1 in gaps:
            steps.append((r, col))
            col -= 1
            steps.append((r, col))
    return col, steps


def outcome(data: dict) -> list[int]:
    """참가자마다 닿는 결과의 자리. 시작 전이면 빈 목록."""
    if not data.get("started"):
        return []
    n = len(data["participants"])
    return [trace(n, data["rungs"], i)[0] for i in range(n)]


class Ladder(WidgetKind):
    kind = "ladder"

    def _new(self, participants=(), results=(), **_ignored) -> dict:
        return {"participants": list(participants), "results": list(results), "started": False,
                "seed": 0, "rows": 0, "rungs": []}

    def _clean(self, data: dict) -> dict:
        people = [n for n in (clean_name(v) for v in take(data.get("participants"), MAX_PEOPLE))
                  if n]
        n = len(people)
        results = [clean_text(v, MAX_RESULT_TEXT) for v in take(data.get("results"), n)]
        results += [""] * (n - len(results))
        started = as_bool(data.get("started")) and n >= 2
        seed = as_int(data.get("seed"), 0, SEED_MAX, 0)
        rows = as_int(data.get("rows"), MIN_ROWS, MAX_ROWS, rows_for(n)) if started else 0
        rungs: list[list[int]] = []
        if started:
            found: set[tuple[int, int]] = set()
            for raw in take(data.get("rungs"), rows * MAX_PEOPLE):
                if isinstance(raw, list) and len(raw) == 2:
                    r = as_int(raw[0], 0, rows - 1)
                    g = as_int(raw[1], 0, n - 2)
                    if r is not None and g is not None:
                        found.add((r, g))
            kept: set[tuple[int, int]] = set()
            for r, g in sorted(found):
                if (r, g - 1) not in kept:       # 이웃한 가로줄은 길을 둘로 가른다
                    kept.add((r, g))
            rungs = [list(x) for x in sorted(kept)]
        return {"participants": people, "results": results, "started": started,
                "seed": seed if started else 0, "rows": rows, "rungs": rungs}

    def prepare_op(self, op: dict) -> dict:
        op = super().prepare_op(op)
        if op.get("op") == "start":
            op["seed"] = secrets.randbelow(SEED_MAX + 1)
        return op

    def _apply(self, data: dict, name: str, op: dict, user: str, now_ms: int) -> str | None:
        if name == "set_entries":
            if not self.is_owner(data, user):
                return E_NOT_ALLOWED
            if data["started"]:
                return E_STARTED
            people, results = op.get("participants"), op.get("results", [])
            if not isinstance(people, list) or not isinstance(results, list):
                return E_INVALID
            if len(people) > MAX_PEOPLE or len(results) > MAX_PEOPLE:
                return E_LIMIT
            names = [n for n in (clean_name(v) for v in people) if n]
            if len(set(names)) != len(names):
                return E_INVALID
            data["participants"] = names
            data["results"] = results[:len(names)]
            return None
        if name == "start":
            if data["started"]:
                return E_STARTED
            n = len(data["participants"])
            if n < 2:
                return E_INVALID
            seed = as_int(op.get("seed"), 0, SEED_MAX)
            if seed is None:
                # 공유 PC 가 seed 를 넣지 않았을 때도 모두 같은 값이 나오게 한다.
                digest = hashlib.sha256(f"{now_ms}:{user}".encode()).digest()
                seed = int.from_bytes(digest[:4], "big") & SEED_MAX
            rows = rows_for(n)
            data.update(started=True, seed=seed, rows=rows, rungs=make_rungs(n, rows, seed))
            return None
        if name == "reset":
            if not self.is_owner(data, user):
                return E_NOT_ALLOWED
            data.update(started=False, seed=0, rows=0, rungs=[])
            return None
        return super()._apply(data, name, op, user, now_ms)

    def _texts(self, data: dict) -> list[str]:
        return data["participants"] + data["results"]

    # ── 그리기 ──

    def _rows(self, data: dict) -> int:
        return data["rows"] if data["started"] else rows_for(len(data["participants"]))

    def _ladder_h(self, data: dict) -> float:
        return 20 + (self._rows(data) - 1) * _GAP

    def measure(self, data, width, ctx) -> int:
        y = draw.head_y() + draw.HEAD + 4
        y += _HEAD + 4 + self._ladder_h(data) + 4 + _HEAD + _WHO
        return int(y + 12 + draw.BTN_H + draw.PAD)

    def paint(self, p: QPainter, data, width, ctx, pal) -> list[Hit]:
        hits: list[Hit] = []
        people, results = data["participants"], data["results"]
        n = len(people)
        x0 = draw.PAD
        inner = width - 2 * draw.PAD
        top0 = draw.head_y()
        draw.text(p, x0, top0, draw.content_right(width) - x0, draw.HEAD, tr("kind.ladder"),
                  pal.ink, draw.bold(14))
        col_w = inner / max(1, n)
        xs = [x0 + col_w * (i + 0.5) for i in range(n)]
        f, b, small = draw.font(12), draw.bold(12), draw.font(11)
        ends = outcome(data)
        landed = {end: i for i, end in enumerate(ends)}
        y = top0 + draw.HEAD + 4
        for i, who in enumerate(people):
            color = pal.series(i + 1) if ends else pal.ink
            draw.text(p, xs[i] - col_w / 2 + 2, y, col_w - 4, _HEAD, who, color,
                      b if ends else f, Qt.AlignCenter)
        top = y + _HEAD + 4
        bottom = top + self._ladder_h(data)

        def row_y(r: int) -> float:
            return top + 10 + r * _GAP

        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(pal.line_strong if not pal.clear else pal.faint, 2, Qt.SolidLine, Qt.RoundCap)
        p.setPen(pen)
        for x in xs:
            p.drawLine(QPointF(x, top), QPointF(x, bottom))
        for r, g in data["rungs"]:
            p.drawLine(QPointF(xs[g], row_y(r)), QPointF(xs[g + 1], row_y(r)))
        if ends:
            for i in range(n):
                _end, steps = trace(n, data["rungs"], i)
                pts = [QPointF(xs[i], top)]
                for r, c in steps[1:]:
                    pts.append(QPointF(xs[c], row_y(r)))
                pts.append(QPointF(xs[_end], bottom))
                p.setPen(QPen(pal.series(i + 1), 3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                p.drawPolyline(QPolygonF(pts))
        y = bottom + 4
        for j, res in enumerate(results):
            who = landed.get(j)
            color = pal.series(who + 1) if who is not None else pal.muted
            draw.text(p, xs[j] - col_w / 2 + 2, y, col_w - 4, _HEAD, res or "-", color,
                      b if who is not None else f, Qt.AlignCenter)
            if who is not None:
                # 결과 밑에 그 결과를 받은 사람(줄은 늘 비워 두어 높이가 바뀌지 않는다).
                draw.text(p, xs[j] - col_w / 2 + 2, y + _HEAD, col_w - 4, _WHO, people[who],
                          pal.muted, small, Qt.AlignCenter)
        y += _HEAD + _WHO + 12
        right = x0 + inner
        if ctx.interactive and ctx.viewer:
            if not data["started"]:
                action = {"op": "start"}
                label = tr("ladder.start")
                right -= draw.button_width(label)
                ok = n >= 2
                hit = draw.button(p, right, y, label, pal, primary=True, ctx=ctx,
                                  action=action, enabled=ok)
                if ok:
                    hits.append(Hit(*hit, action))
                right -= 8
            elif self.is_owner(data, ctx.viewer):
                action = {"op": "reset"}
                label = tr("ladder.reset")
                right -= draw.button_width(label)
                hits.append(Hit(*draw.button(p, right, y, label, pal, ctx=ctx, action=action),
                                action))
                right -= 8
        draw.text(p, x0, y, right - x0, draw.BTN_H, tr("ladder.people_n", n=n), pal.muted,
                  draw.font(12))
        return hits


def split_entries(text: str, *, keep_empty: bool = False) -> list[str]:
    """쉼표나 줄바꿈으로 나눈 목록."""
    parts = [s.strip() for s in str(text or "").replace("\n", ",").split(",")]
    if keep_empty:
        while parts and not parts[-1]:
            parts.pop()
        return parts[:MAX_PEOPLE + 1]
    return [s for s in parts if s][:MAX_PEOPLE + 1]


LADDER = register(Ladder())
