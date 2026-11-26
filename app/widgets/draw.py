"""위젯 그리기의 공통 도구: 색, 글꼴, 카드, 단추, 마우스를 올린 상태.

모든 위젯이 같은 틀을 쓴다.

* 카드: 흰 바탕(기본)이나 투명 바탕(메모 색이 비침), 1px 옅은 테두리, 10px 둥근 모서리,
  안쪽 여백 14px.
* 머리 줄(26px): 제목이 왼쪽, 종류의 작은 단추가 오른쪽. 오른쪽 위 끝 26px 칸은 '⋯'
  자리라 늘 비워 둔다(위젯 내용이 '⋯' 밑에 깔리지 않는다). :func:`content_right` 까지만 쓴다.
* 글: 13px 본문, 12px 보조(옅은 회색), 수는 같은 폭 숫자.
* 단추: 높이 28px, 6px 둥근 모서리. 권하는 단추 하나만 짙게. 마우스를 올리면 옅은 바탕,
  누르는 동안 조금 더 짙은 바탕.
* 묶음 사이는 묶음 안의 두 배 넘게 띄운다(안 8, 사이 16).

색을 칠한 알림 카드는 만들지 않는다. 글은 모두 QPainter.drawText 로 평문만 그린다."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QGuiApplication,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
)

from .base import BG_CLEAR, BG_WHITE, Ctx, Hit, WidgetError

INK = "#1F1F1F"
MUTED = "#6B6B6B"
FAINT = "#ABABAB"
LINE = "#E6E6E6"
LINE_STRONG = "#D2D2D2"
RED = "#B91C1C"
SATURDAY = "#3F5F7F"
RADIUS = 10
FONT_PX = 13
PAD = 14
GAP = 8              # 묶음 안
GROUP = 16           # 묶음 사이
HEAD = 26            # 머리 줄
BTN_H = 28           # 단추 높이
MENU = 26            # '⋯' 단추
MIN_WIDTH = 160
MAX_WIDTH = 2000
# 여러 줄(사다리 길, 그래프 계열)을 가르는 색. 짙은 글자 색 다음은 차분한 색들.
SERIES = ("#3A3A3A", "#2563EB", "#B45309", "#15803D", "#7C3AED", "#B91C1C",
          "#0E7490", "#A21CAF")
# 어두운 메모 위(투명 바탕)에서 읽히는 같은 차례의 밝은 색.
SERIES_DARK = ("#FFFFFF", "#60A5FA", "#FBBF24", "#4ADE80", "#C084FC", "#F87171",
               "#22D3EE", "#E879F9")
# 고르지 않은 칸의 테두리(3:1 넘는 대비).
CHECK_LINE = "#8A8A8A"
# 동작을 견줄 때 보지 않는 값(말풍선 이름 등).
_SKIP = ("tip", "menu")


def _hex_ok(value) -> bool:
    return (isinstance(value, str) and len(value) == 7 and value[0] == "#"
            and all(c in "0123456789abcdefABCDEF" for c in value[1:]))


def _is_dark(hex_color: str) -> bool:
    if not _hex_ok(hex_color):
        return False
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255.0 < 0.55


def _alpha(color: str, a: float) -> QColor:
    c = QColor(color)
    c.setAlphaF(a)
    return c


class Palette:
    """바탕에 맞춘 색. 흰 바탕이면 앱의 색 그대로, 투명이면 메모 색 위에서 읽히는 색."""

    def __init__(self, bg: str, note_color: str = "#FFFFFF") -> None:
        self.clear = bg == BG_CLEAR
        dark = self.clear and _is_dark(note_color)
        self.dark = dark
        self.surface = None if self.clear else QColor("#FFFFFF")
        if not self.clear:
            self.ink = QColor(INK)
            self.muted = QColor(MUTED)
            self.faint = QColor(FAINT)
            self.line = QColor(LINE)
            self.line_strong = QColor(LINE_STRONG)
            self.red = QColor(RED)
            self.saturday = QColor(SATURDAY)
            self.track = QColor("#EFEFEF")
            self.hover = QColor("#F4F4F4")
            self.pressed = QColor("#EAEAEA")
            self.check_line = QColor(CHECK_LINE)
        else:
            base = "#FFFFFF" if dark else INK
            self.ink = QColor(base)
            self.muted = _alpha(base, 0.72)
            self.faint = _alpha(base, 0.42)
            self.line = _alpha(base, 0.16)
            self.line_strong = _alpha(base, 0.3)
            self.red = QColor("#FFA3A3" if dark else RED)
            self.saturday = QColor("#B5CBE6" if dark else SATURDAY)
            self.track = _alpha(base, 0.1)
            self.hover = _alpha(base, 0.07)
            self.pressed = _alpha(base, 0.13)
            self.check_line = _alpha(base, 0.6)
        # 고른 것을 칠하는 색과 그 위의 글자 색.
        self.fill = QColor(self.ink)
        self.fill_hover = QColor("#3A3A3A") if not dark else _alpha("#FFFFFF", 0.86)
        self.on_fill = QColor(INK if dark else "#FFFFFF")

    def series(self, i: int) -> QColor:
        table = SERIES_DARK if self.dark else SERIES
        return QColor(table[i % len(table)])


def font(px: int = FONT_PX, weight=QFont.Normal) -> QFont:
    app = QGuiApplication.instance()
    f = QFont(app.font()) if app is not None else QFont()
    f.setPixelSize(px)
    f.setWeight(weight)
    f.setHintingPreference(QFont.PreferNoHinting)
    f.setStyleStrategy(QFont.PreferAntialias)
    return f


def bold(px: int = FONT_PX) -> QFont:
    return font(px, QFont.DemiBold)


def metrics(f: QFont) -> QFontMetricsF:
    return QFontMetricsF(f)


def text_width(f: QFont, text: str) -> float:
    return QFontMetricsF(f).horizontalAdvance(text)


# 그리는 동안 줄임표로 줄인 글: (자리, 온전한 글). 판이 말풍선으로 보인다.
_TIPS: list | None = None


def elided(f: QFont, s: str, w: float) -> str:
    """줄임표로 줄인 글. 줄임표 앞의 빈칸은 뺀다."""
    out = QFontMetricsF(f).elidedText(s, Qt.ElideRight, w)
    if out != s and out.endswith("…"):
        out = out[:-1].rstrip() + "…"
    return out


def text(p: QPainter, x: float, y: float, w: float, h: float, s: str, color, f: QFont,
         align=Qt.AlignLeft | Qt.AlignVCenter, elide: bool = True) -> None:
    p.setFont(f)
    p.setPen(QColor(color))
    if elide and w > 0:
        shown = elided(f, s, w)
        if shown != s and _TIPS is not None:
            _TIPS.append((QRectF(x, y, w, h), s))
        s = shown
    p.drawText(QRectF(x, y, max(0.0, w), h), int(align), s)


def wrap_lines(f: QFont, s: str, width: float, max_lines: int = 3) -> list[str]:
    """줄을 바꾼 글(``max_lines`` 줄까지). 넘치면 마지막 줄 끝을 줄임표로."""
    from PySide6.QtGui import QTextLayout, QTextOption
    if not s:
        return [""]
    layout = QTextLayout(s, f)
    option = QTextOption()
    option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
    layout.setTextOption(option)
    lines: list[str] = []
    layout.beginLayout()
    while True:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(max(10.0, width))
        lines.append(s[line.textStart():line.textStart() + line.textLength()])
    layout.endLayout()
    if len(lines) > max_lines:
        rest = "".join(lines[max_lines - 1:]).replace("\n", " ")
        lines = lines[:max_lines - 1] + [elided(f, rest, width)]
    return [ln.rstrip() for ln in lines]


def wrapped_height(f: QFont, text: str, width: float, max_lines: int = 3) -> float:
    """줄을 바꿔 그릴 때의 높이(``max_lines`` 줄까지)."""
    return QFontMetricsF(f).height() * len(wrap_lines(f, text, width, max_lines))


def wrapped(p: QPainter, x: float, y: float, w: float, h: float, s: str, color, f: QFont,
            max_lines: int = 3) -> None:
    """줄을 바꿔 그린다. 넘치면 마지막 줄을 줄임표로 끝낸다(잘린 줄이 보이지 않는다)."""
    fm = QFontMetricsF(f)
    lines = wrap_lines(f, s, w, max(1, min(max_lines, int(h // fm.height()) or 1)))
    p.setFont(f)
    p.setPen(QColor(color))
    for i, line in enumerate(lines):
        p.drawText(QRectF(x, y + i * fm.height(), w, fm.height()),
                   int(Qt.AlignLeft | Qt.AlignVCenter), line)


def hline(p: QPainter, x1: float, x2: float, y: float, color, width: float = 1.0) -> None:
    p.setPen(QPen(QColor(color), width))
    p.drawLine(QPointF(x1, y), QPointF(x2, y))


# ── 머리 줄과 '⋯' 자리 ──

def menu_rect(width: float) -> QRectF:
    """오른쪽 위 '⋯' 단추의 자리. 위젯 내용은 이 칸을 비워 둔다."""
    return QRectF(width - 8 - MENU, 8, MENU, MENU)


def content_right(width: float) -> float:
    """머리 줄에서 글과 단추를 놓을 수 있는 오른쪽 끝('⋯' 자리 앞)."""
    return menu_rect(width).left() - 6


def head_y() -> float:
    """머리 줄의 위쪽(머리 줄 가운데가 '⋯' 가운데와 같다)."""
    return menu_rect(0).center().y() - HEAD / 2


# ── 마우스 상태 ──

def _same(a: dict | None, b: dict | None) -> bool:
    if not a or not b:
        return False
    keys = (set(a) | set(b)) - set(_SKIP)
    return all(a.get(k) == b.get(k) for k in keys)


def state(ctx: Ctx | None, action: dict | None) -> str:
    """그 동작의 단추가 지금 어떤가: "pressed", "hover", ""."""
    if ctx is None or not ctx.interactive or not action:
        return ""
    if _same(ctx.view.get("pressed"), action):
        return "pressed"
    if _same(ctx.view.get("hover"), action):
        return "hover"
    return ""


def hover_fill(p: QPainter, rect: QRectF, pal: Palette, how: str, radius: float = 6) -> None:
    """마우스를 올렸거나 누르는 동안의 옅은 바탕."""
    if not how:
        return
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(pal.pressed if how == "pressed" else pal.hover)
    p.drawRoundedRect(rect, radius, radius)
    p.setBrush(Qt.NoBrush)


# ── 단추 ──

def button_width(label: str, f: QFont | None = None) -> float:
    return text_width(f or font(12), label) + 24


def button(p: QPainter, x: float, y: float, label: str, pal: Palette, *, primary: bool = False,
           h: float = BTN_H, f: QFont | None = None, ctx: Ctx | None = None,
           action: dict | None = None, enabled: bool = True) -> tuple[float, float, float, float]:
    """글자 단추. 권하는 단추 하나만 짙게(primary) 그린다. (x, y, w, h) 를 돌려준다."""
    f = f or font(12)
    w = button_width(label, f)
    r = QRectF(x + 0.5, y + 0.5, w - 1, h - 1)
    how = state(ctx, action) if enabled else ""
    p.setRenderHint(QPainter.Antialiasing)
    if primary and enabled:
        p.setPen(Qt.NoPen)
        p.setBrush(pal.fill_hover if how else pal.fill)
        p.drawRoundedRect(r, 6, 6)
        ink = pal.on_fill
    else:
        if how:
            p.setPen(Qt.NoPen)
            p.setBrush(pal.pressed if how == "pressed" else pal.hover)
            p.drawRoundedRect(r, 6, 6)
        p.setPen(QPen(pal.line_strong if enabled else pal.line, 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r, 6, 6)
        ink = pal.ink if enabled else pal.faint
    p.setBrush(Qt.NoBrush)
    text(p, x, y, w, h, label, ink, f, Qt.AlignCenter, elide=False)
    return x, y, w, h


def glyph(p: QPainter, rect: QRectF, name: str, color) -> None:
    """작은 그림(펜으로 그린다): "more", "prev", "next", "plus", "minus"."""
    c = rect.center()
    p.setRenderHint(QPainter.Antialiasing)
    if name == "more":
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(color))
        for dx in (-5.0, 0.0, 5.0):
            p.drawEllipse(QPointF(c.x() + dx, c.y()), 1.4, 1.4)
        p.setBrush(Qt.NoBrush)
        return
    pen = QPen(QColor(color), 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    if name in ("prev", "next"):
        d = -3.0 if name == "prev" else 3.0
        # 꺾쇠는 뾰족한 쪽으로 조금 옮겨야 가운데로 보인다.
        cx = c.x() - d * 0.25
        p.drawLine(QPointF(cx - d, c.y() - 5), QPointF(cx + d, c.y()))
        p.drawLine(QPointF(cx + d, c.y()), QPointF(cx - d, c.y() + 5))
    elif name in ("plus", "minus"):
        p.drawLine(QPointF(c.x() - 4.5, c.y()), QPointF(c.x() + 4.5, c.y()))
        if name == "plus":
            p.drawLine(QPointF(c.x(), c.y() - 4.5), QPointF(c.x(), c.y() + 4.5))


def icon_button(p: QPainter, x: float, y: float, name: str, pal: Palette, *,
                ctx: Ctx | None = None, action: dict | None = None, size: float = MENU,
                enabled: bool = True, border: bool = False) -> tuple[float, float, float, float]:
    """그림만 있는 단추(26px). 마우스를 올리면 옅은 바탕."""
    r = QRectF(x, y, size, size)
    how = state(ctx, action) if enabled else ""
    hover_fill(p, r, pal, how)
    if border:
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(pal.line_strong if enabled else pal.line, 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)
    glyph(p, r, name, pal.ink if enabled else pal.faint)
    return x, y, size, size


def check(p: QPainter, x: float, y: float, on: bool, pal: Palette, *, round_: bool = False,
          size: float = 16) -> None:
    """고름 표시(네모는 여러 개 고르기, 동그라미는 하나만)."""
    r = QRectF(x + 0.75, y + 0.75, size - 1.5, size - 1.5)
    p.setRenderHint(QPainter.Antialiasing)
    if on:
        p.setPen(Qt.NoPen)
        p.setBrush(pal.fill)
    else:
        p.setPen(QPen(pal.check_line, 1.5))
        p.setBrush(Qt.NoBrush)
    if round_:
        p.drawEllipse(r)
        if on:
            p.setBrush(pal.on_fill)
            p.drawEllipse(r.center(), size * 0.17, size * 0.17)
    else:
        p.drawRoundedRect(r, 4, 4)
        if on:
            s = size / 16.0
            p.setPen(QPen(pal.on_fill, 1.7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawPolyline([QPointF(x + 4.2 * s, y + 8.2 * s), QPointF(x + 6.9 * s, y + 10.8 * s),
                            QPointF(x + 11.8 * s, y + 5.4 * s)])
    p.setBrush(Qt.NoBrush)


def bar(p: QPainter, x: float, y: float, w: float, share: float, pal: Palette, *,
        strong: bool = False, h: float = 4) -> None:
    """가는 막대(비율)."""
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(pal.track)
    p.drawRoundedRect(QRectF(x, y, w, h), h / 2, h / 2)
    if share > 0:
        p.setBrush(pal.fill if strong else pal.faint)
        p.drawRoundedRect(QRectF(x, y, max(h, w * min(1.0, share)), h), h / 2, h / 2)
    p.setBrush(Qt.NoBrush)


# ── 카드 ──

def frame(p: QPainter, w: float, h: float, pal: Palette) -> None:
    """흰 바탕이면 둥근 흰 판과 옅은 테두리. 투명이면 아무것도 그리지 않는다."""
    if pal.surface is None:
        return
    p.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), RADIUS, RADIUS)
    p.fillPath(path, pal.surface)
    p.setPen(QPen(QColor(LINE), 1))
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)


def clamp_width(width) -> int:
    try:
        width = int(width)
    except (TypeError, ValueError):
        width = 300
    return max(MIN_WIDTH, min(MAX_WIDTH, width))


MENU_ACTION = {"local": "menu", "tip": "menu.more"}


def paint_widget(kind, p: QPainter, data: dict, width: int, height: int, ctx: Ctx) -> list[Hit]:
    """바탕 틀과 내용을 그리고 누를 자리를 돌려준다(편집기와 그림이 함께 쓴다). 누를 수
    있는 화면이면 마우스를 올렸을 때 오른쪽 위에 '⋯' 를 그린다."""
    global _TIPS
    pal = Palette(data.get("bg", BG_WHITE), ctx.background)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)
    frame(p, width, height, pal)
    tips: list = []
    _TIPS = tips if ctx.interactive else None
    try:
        hits = kind.paint(p, data, width, ctx, pal)
    finally:
        _TIPS = None
    ctx.view["tips"] = tips
    if ctx.interactive and ctx.view.get("menu_ok", True):
        r = menu_rect(width)
        shown = (ctx.view.get("card_hover") or ctx.view.get("menu_open")
                 or state(ctx, MENU_ACTION))
        if shown:
            if ctx.view.get("menu_open"):
                hover_fill(p, r, pal, "pressed")
            icon_button(p, r.x(), r.y(), "more", pal, ctx=ctx, action=MENU_ACTION)
        hits.append(Hit(r.x(), r.y(), r.width(), r.height(), dict(MENU_ACTION)))
    if ctx.interactive and ctx.view.get("resizing"):
        # 폭을 바꾸는 동안 오른쪽 가장자리에 가는 선.
        p.setPen(QPen(QColor("#2563EB"), 2))
        p.drawLine(QPointF(width - 1, 6), QPointF(width - 1, height - 6))
    return hits


def blank(width: int, label: str, ctx: Ctx, ratio: float = 1.0) -> QImage:
    """받을 수 없는 데이터일 때 그리는 빈 틀."""
    h = 48
    img = _image(width, h, ratio)
    p = QPainter(img)
    pal = Palette(BG_WHITE, ctx.background)
    frame(p, width, h, pal)
    text(p, PAD, 0, width - 2 * PAD, h, label, pal.muted, font())
    p.end()
    return img


def _image(width: int, height: int, ratio: float) -> QImage:
    ratio = max(1.0, min(4.0, float(ratio or 1.0)))
    img = QImage(max(1, round(width * ratio)), max(1, round(height * ratio)),
                 QImage.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(ratio)
    img.fill(Qt.transparent)
    return img


def render_image(kind, data, width, ctx: Ctx, ratio: float = 1.0) -> QImage:
    width = clamp_width(width)
    try:
        clean = kind.validate(data)
    except WidgetError as e:
        from .texts import tr
        label = tr("err.newer") if e.key == "newer" else tr("kind." + kind.kind)
        return blank(width, label, ctx, ratio)
    height = max(24, int(kind.measure(clean, width, ctx)))
    img = _image(width, height, ratio)
    p = QPainter(img)
    try:
        paint_widget(kind, p, clean, width, height, ctx)
    finally:
        p.end()
    return img
