"""그래프: 작은 표 데이터를 막대나 꺾은선으로 그린다.

데이터
    title: 제목, chart: "bar" | "line",
    labels: [가로축 이름...](200 개까지),
    series: [{"name": 계열 이름, "values": [수 또는 null ...]}](6 개까지, 값 수는 labels 와 같다)

동작(누구나. 같은 메모의 표에서 데이터를 옮겨 올 때도 set_data 를 쓴다)
    set_data {"labels": [...], "series": [...], "title": 글(없으면 그대로)}
              한도를 넘으면 자르지 않고 limit 로 거절한다.
    set_kind {"chart": "bar" | "line"}
    set_bg {"bg": white|clear}"""
from __future__ import annotations

import itertools
import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QPainter, QPen

from . import draw
from .base import E_INVALID, E_LIMIT, Hit, WidgetKind, clean_text, register, take
from .texts import format_number, tr

MAX_POINTS = 200
MAX_SERIES = 6
MAX_LABEL = 40
MAX_VALUE = 1e15
CHARTS = ("bar", "line")

_HEAD = 26
_PLOT = 150
_XLAB = 18
_LEGEND = 20


def clean_value(value):
    """유한한 수만. 그 밖은 None(빈 칸)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if abs(value) > MAX_VALUE:
        return None
    return value


class Graph(WidgetKind):
    kind = "graph"
    bg_option = True
    sized_height = True

    def _new(self, title: str = "", chart: str = "bar", labels=(), series=(), **_ignored) -> dict:
        return {"title": title, "chart": chart, "labels": list(labels), "series": list(series)}

    def _clean(self, data: dict) -> dict:
        labels = [clean_text(v, MAX_LABEL) for v in take(data.get("labels"), MAX_POINTS)]
        n = len(labels)
        series = []
        for raw in take(data.get("series"), MAX_SERIES):
            if not isinstance(raw, dict):
                continue
            values = [clean_value(v) for v in take(raw.get("values"), n)]
            values += [None] * (n - len(values))
            series.append({"name": clean_text(raw.get("name"), MAX_LABEL), "values": values})
        chart = data.get("chart")
        return {"title": clean_text(data.get("title")),
                "chart": chart if chart in CHARTS else "bar",
                "labels": labels, "series": series}

    def _apply(self, data: dict, name: str, op: dict, user: str, now_ms: int) -> str | None:
        if name == "set_data":
            labels, series = op.get("labels"), op.get("series")
            if not isinstance(labels, list) or not isinstance(series, list):
                return E_INVALID
            if len(labels) > MAX_POINTS or len(series) > MAX_SERIES:
                return E_LIMIT
            for s in series:
                if not isinstance(s, dict) or not isinstance(s.get("values", []), list):
                    return E_INVALID
                if len(s.get("values", [])) > MAX_POINTS:
                    return E_LIMIT
            data["labels"] = labels
            data["series"] = series
            if "title" in op:
                if not isinstance(op["title"], str):
                    return E_INVALID
                data["title"] = op["title"]
            return None
        if name == "set_kind":
            if op.get("chart") not in CHARTS:
                return E_INVALID
            data["chart"] = op["chart"]
            return None
        return super()._apply(data, name, op, user, now_ms)

    def _texts(self, data: dict) -> list[str]:
        return [data["title"]] + data["labels"] + [s["name"] for s in data["series"]]

    # ── 그리기 ──

    def _rest(self, data: dict) -> float:
        """그림 칸을 뺀 높이(머리 줄, 가로축 글, 범례, 여백)."""
        h = draw.head_y() + draw.HEAD + draw.GAP + _XLAB + draw.PAD
        if len(data["series"]) > 1:
            h += 6 + _LEGEND
        return h

    def _plot_h(self, data: dict, ctx) -> float:
        """그림 칸의 높이. 끄는 동안의 높이나 정해 둔 높이(``h``)가 있으면 그에 맞춘다."""
        total = ctx.view.get("preview_h") or data.get("h") or 0
        if total:
            return max(90.0, total - self._rest(data))
        return float(_PLOT)

    def measure(self, data, width, ctx) -> int:
        return int(self._rest(data) + self._plot_h(data, ctx))

    def paint(self, p: QPainter, data, width, ctx, pal) -> list[Hit]:
        hits: list[Hit] = []
        x0 = draw.PAD
        inner = width - 2 * draw.PAD
        small = draw.font(11)
        y = draw.head_y()
        right = draw.content_right(width)
        draw.text(p, x0, y, right - x0, draw.HEAD, data["title"] or tr("kind.graph"),
                  pal.ink if data["title"] else pal.muted, draw.bold(14))
        y += draw.HEAD + draw.GAP
        values = [v for s in data["series"] for v in s["values"] if v is not None]
        n = len(data["labels"])
        plot_h = self._plot_h(data, ctx)
        if not n or not values:
            mid = y + (plot_h + _XLAB) / 2
            if ctx.interactive and ctx.viewer:
                label = tr("graph.add")
                action = {"local": "setup"}
                bw = draw.button_width(label)
                draw.text(p, x0, mid - 30, inner, 20, tr("graph.empty"), pal.muted, draw.font(),
                          Qt.AlignCenter)
                hits.append(Hit(*draw.button(p, x0 + (inner - bw) / 2, mid - 4, label, pal,
                                             ctx=ctx, action=action), action))
            else:
                draw.text(p, x0, y, inner, plot_h + _XLAB, tr("graph.empty"), pal.muted,
                          draw.font(), Qt.AlignCenter)
            y += plot_h + _XLAB
        else:
            y = self._plot(p, data, x0, y, inner, values, pal, plot_h)
        if len(data["series"]) > 1:
            y += 6
            x = x0
            for i, s in enumerate(data["series"]):
                label = s["name"] or str(i + 1)
                w = min(inner - (x - x0), draw.text_width(small, label) + 30)
                if w < 30:
                    break
                p.setPen(Qt.NoPen)
                p.setBrush(pal.series(i))
                p.drawRoundedRect(QRectF(x, y + 5, 10, 10), 3, 3)
                p.setBrush(Qt.NoBrush)
                draw.text(p, x + 15, y, w - 15, _LEGEND, label, pal.ink, small)
                x += w
            y += _LEGEND
        return hits

    def menu_choices(self, data: dict):
        """막대와 꺾은선은 '⋯' 메뉴의 '모양' 에서 고른다."""
        return [(tr("graph.shape"), [(tr("graph." + c), {"op": "set_kind", "chart": c},
                                      data.get("chart") == c) for c in CHARTS])]

    def _segments(self, p: QPainter, data: dict, right: float, y: float, pal, ctx,
                  hits: list[Hit]) -> float:
        """'막대 | 꺾은선' 고르기. 고른 쪽은 흰 판 위 굵은 글자, 마우스를 올리면 옅은 바탕."""
        small = draw.font(12)
        labels = [(c, tr("graph." + c)) for c in CHARTS]
        widths = [draw.text_width(draw.bold(12), t) + 20 for _c, t in labels]
        total = sum(widths) + 4
        x = right - total
        h = draw.MENU
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(pal.track)
        p.drawRoundedRect(QRectF(x, y, total, h), 7, 7)
        x += 2
        for (chart, label), w in zip(labels, widths, strict=True):
            on = data["chart"] == chart
            action = {"op": "set_kind", "chart": chart}
            r = QRectF(x, y + 2, w, h - 4)
            if on:
                p.setPen(QPen(pal.line, 1))
                p.setBrush(pal.surface or pal.hover)
                p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 5, 5)
                p.setBrush(Qt.NoBrush)
            else:
                draw.hover_fill(p, r, pal, draw.state(ctx, action), radius=5)
                hits.append(Hit(r.x(), r.y(), r.width(), r.height(), action))
            draw.text(p, x, y, w, h, label, pal.ink if on else pal.muted,
                      draw.bold(12) if on else small, Qt.AlignCenter, elide=False)
            x += w
        return total

    def _plot(self, p: QPainter, data: dict, x0: float, y: float, inner: float,
              values: list, pal, plot_h: float = _PLOT) -> float:
        small = draw.font(12)
        vmax = max(0, max(values))
        vmin = min(0, min(values))
        if vmax == vmin:
            vmax = vmin + 1
        top_label, low_label = format_number(vmax), format_number(vmin)
        series_n = max(1, len(data["series"]))
        n_labels = len(data["labels"])
        # 막대 위에 값이 보이면 세로축 숫자는 같은 것을 되풀이하니 뺀다.
        values_shown = (data["chart"] == "bar" and n_labels * series_n <= 16)
        gutter = (0.0 if values_shown else
                  max(draw.text_width(small, top_label), draw.text_width(small, low_label)) + 8)
        px, pw = x0 + gutter, inner - gutter
        # 막대 위(음수면 아래)의 값 글자가 들어갈 자리를 남긴다.
        bar_chart = data["chart"] == "bar"
        top = y + (18 if bar_chart else 6)
        bottom = y + plot_h - (16 if bar_chart and vmin < 0 else 0)

        def ypos(v: float) -> float:
            return bottom - (v - vmin) / (vmax - vmin) * (bottom - top)

        if gutter:
            draw.text(p, x0, top - 7, gutter - 6, 14, top_label, pal.muted, small,
                      Qt.AlignRight | Qt.AlignVCenter, elide=False)
            if vmin < 0:
                draw.text(p, x0, bottom - 7, gutter - 6, 14, low_label, pal.muted, small,
                          Qt.AlignRight | Qt.AlignVCenter, elide=False)
            draw.hline(p, px, px + pw, top + 0.5, pal.line)
        zero = ypos(0)
        draw.hline(p, px, px + pw, zero + 0.5, pal.line_strong)
        labels = data["labels"]
        series = data["series"]
        n, s = len(labels), max(1, len(series))
        group = pw / n
        p.setRenderHint(QPainter.Antialiasing)
        if data["chart"] == "bar":
            bar = max(1.0, min(28.0, group * 0.7 / s))
            show_values = values_shown and bar >= 14
            for i in range(n):
                left = px + group * i + (group - bar * s) / 2
                for k, ser in enumerate(series):
                    v = ser["values"][i]
                    if v is None:
                        continue
                    y1, y2 = sorted((ypos(v), zero))
                    p.setPen(Qt.NoPen)
                    p.setBrush(pal.series(k))
                    p.drawRoundedRect(QRectF(left + bar * k + 1, y1, bar - 2, max(1.0, y2 - y1)),
                                      2, 2)
                    p.setBrush(Qt.NoBrush)
                    if show_values:
                        ly = y1 - 15 if v >= 0 else y2 + 1
                        draw.text(p, left + bar * k - 10, ly, bar + 20, 14, format_number(v),
                                  pal.ink, small, Qt.AlignCenter, elide=False)
        else:
            dots = n <= 40
            for k, ser in enumerate(series):
                color = pal.series(k)
                p.setPen(QPen(color, 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                prev = None
                for i, v in enumerate(ser["values"]):
                    if v is None:
                        prev = None
                        continue
                    pt = QPointF(px + group * (i + 0.5), ypos(v))
                    if prev is not None:
                        p.drawLine(prev, pt)
                    prev = pt
                if dots:
                    p.setPen(Qt.NoPen)
                    p.setBrush(color)
                    for i, v in enumerate(ser["values"]):
                        if v is not None:
                            p.drawEllipse(QPointF(px + group * (i + 0.5), ypos(v)), 2.6, 2.6)
                    p.setBrush(Qt.NoBrush)
        widest = max((draw.text_width(small, t) for t in labels), default=0) + 8
        step = max(1, math.ceil(widest / group)) if group > 0 else 1
        for i in range(0, n, step):
            cx = px + group * (i + 0.5)
            w = group * step
            draw.text(p, cx - w / 2, y + plot_h + 2, w, _XLAB - 2, labels[i], pal.muted, small,
                      Qt.AlignCenter)
        return y + plot_h + _XLAB


def _number(cell: str):
    cell = cell.strip().replace(" ", "")
    if not cell:
        return None
    try:
        value = float(cell)
    except ValueError:
        return None
    if not math.isfinite(value):
        return None
    return int(value) if value.is_integer() and abs(value) < 2 ** 53 else value


def parse_table_text(text: str) -> tuple[list[str], list[dict]]:
    """'이름, 계열1, 계열2' 머리 줄 다음에 '가로축, 값, 값' 줄들. 칸은 탭이나 쉼표로 나눈다.

    한도(점 200 개, 계열 6 개)를 넘는 것은 그대로 돌려준다. 공유 PC 가 거절한다."""
    rows = []
    for line in itertools.islice(str(text or "").splitlines(), MAX_POINTS + 2):
        if not line.strip():
            continue
        cells = line.split("\t") if "\t" in line else line.split(",")
        rows.append([c.strip() for c in cells])
    if not rows:
        return [], []
    head, body = rows[0], rows[1:]
    names = head[1:MAX_SERIES + 2]
    labels = [r[0] for r in body]
    series = []
    for k, name in enumerate(names):
        values = [_number(r[k + 1]) if k + 1 < len(r) else None for r in body]
        series.append({"name": name, "values": values})
    return labels, series


def to_table_text(data: dict) -> str:
    lines = [", ".join([""] + [s["name"] for s in data["series"]])]
    for i, label in enumerate(data["labels"]):
        cells = [label]
        for s in data["series"]:
            v = s["values"][i]
            cells.append("" if v is None else (str(int(v)) if float(v).is_integer() else str(v)))
        lines.append(", ".join(cells))
    return "\n".join(lines)


GRAPH = register(Graph())
