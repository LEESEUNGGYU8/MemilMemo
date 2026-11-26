"""오목: 15×15 판, 흑이 먼저 둔다. 먼저 두는 두 사람이 흑과 백이 되고 나머지는 구경한다.

규칙: 가로, 세로, 두 대각선 가운데 한 방향으로 같은 색이 다섯 개 이상 이어지면 이긴다.
여섯 개 이상(장목)도 이긴 것으로 본다(OVERLINE_WINS). 흑의 금수(삼삼, 사사, 장목)는
두지 않는다. 판이 다 차면 무승부.

데이터
    moves: [[x, y], ...] 둔 차례, black: 흑을 쥔 사람, white: 백을 쥔 사람,
    winner: "" | "black" | "white" | "draw", line: 이긴 줄의 자리들

동작
    move {"x": 0~14, "y": 0~14}  흑 차례에 흑이 비어 있으면 둔 사람이 흑, 백 차례에 백이
                                비어 있으면 둔 사람이 백(흑과 같은 사람은 안 된다).
    new_game                    두 사람과 만든 사람은 언제나, 다른 사람은 판이 끝났거나
                                아직 아무도 두지 않았을 때.

받은 데이터의 승패는 믿지 않고 둔 차례를 다시 두어 본다. 이긴 뒤의 수는 버린다.
설정에서 끌 수 있는 미니게임이다(is_game)."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen

from . import draw
from .base import (E_GAME_OVER, E_INVALID, E_NOT_ALLOWED, E_NOT_YOUR_TURN, E_OCCUPIED, Hit,
                   WidgetKind, as_int, clean_name, register, take)
from .texts import tr

SIZE = 15
OVERLINE_WINS = True
BLACK, WHITE, DRAW = "black", "white", "draw"
_DIRS = ((1, 0), (0, 1), (1, 1), (1, -1))
_STARS = ((3, 3), (3, 11), (11, 3), (11, 11), (7, 7))
_STATUS = 28


def winning_line(board: dict[tuple[int, int], str], x: int, y: int) -> list[tuple[int, int]]:
    """(x, y) 에 놓은 돌로 생긴 이긴 줄. 없으면 빈 목록."""
    color = board.get((x, y))
    if color is None:
        return []
    for dx, dy in _DIRS:
        line = [(x, y)]
        for sign in (1, -1):
            cx, cy = x + dx * sign, y + dy * sign
            while board.get((cx, cy)) == color:
                line.append((cx, cy))
                cx, cy = cx + dx * sign, cy + dy * sign
        if len(line) == 5 or (len(line) > 5 and OVERLINE_WINS):
            return sorted(line)
    return []


def replay(moves: list) -> tuple[list[list[int]], str, list[list[int]]]:
    """둔 차례를 다시 두어 본다: (받은 수, 승패, 이긴 줄). 틀린 수에서 멈춘다."""
    board: dict[tuple[int, int], str] = {}
    kept: list[list[int]] = []
    for raw in take(moves, SIZE * SIZE):
        if not (isinstance(raw, list) and len(raw) == 2):
            break
        x, y = as_int(raw[0], 0, SIZE - 1), as_int(raw[1], 0, SIZE - 1)
        if x is None or y is None or (x, y) in board:
            break
        color = BLACK if len(kept) % 2 == 0 else WHITE
        board[(x, y)] = color
        kept.append([x, y])
        line = winning_line(board, x, y)
        if line:
            return kept, color, [list(c) for c in line]
    winner = DRAW if len(kept) == SIZE * SIZE else ""
    return kept, winner, []


def to_move(data: dict) -> str:
    return BLACK if len(data["moves"]) % 2 == 0 else WHITE


def can_move(data: dict, user: str) -> bool:
    """이 사람이 지금 둘 수 있는가(편집기가 쓸데없는 동작을 보내지 않게)."""
    if not user or data["winner"]:
        return False
    color = to_move(data)
    player = data[color]
    other = data[WHITE if color == BLACK else BLACK]
    return player == user or (not player and other != user)


class Omok(WidgetKind):
    kind = "omok"
    is_game = True

    def _new(self, **_ignored) -> dict:
        return {"moves": [], "black": "", "white": "", "winner": "", "line": []}

    def _clean(self, data: dict) -> dict:
        moves, winner, line = replay(data.get("moves"))
        black, white = clean_name(data.get("black")), clean_name(data.get("white"))
        if black and black == white:
            white = ""
        return {"moves": moves, "black": black, "white": white, "winner": winner, "line": line}

    def _apply(self, data: dict, name: str, op: dict, user: str, now_ms: int) -> str | None:
        if name == "move":
            if data["winner"]:
                return E_GAME_OVER
            x, y = as_int(op.get("x"), 0, SIZE - 1), as_int(op.get("y"), 0, SIZE - 1)
            if x is None or y is None:
                return E_INVALID
            if [x, y] in data["moves"]:
                return E_OCCUPIED
            color = to_move(data)
            other = WHITE if color == BLACK else BLACK
            if data[color]:
                if data[color] != user:
                    return E_NOT_YOUR_TURN
            elif data[other] == user:
                return E_NOT_YOUR_TURN
            else:
                data[color] = user
            data["moves"].append([x, y])
            return None                      # 승패는 validate 가 다시 두어 보며 정한다
        if name == "new_game":
            in_game = user in (data["black"], data["white"]) or self.is_owner(data, user)
            if not (in_game or data["winner"] or not data["moves"]):
                return E_NOT_ALLOWED
            data.update(moves=[], black="", white="", winner="", line=[])
            return None
        return super()._apply(data, name, op, user, now_ms)

    def _texts(self, data: dict) -> list[str]:
        return [data["black"], data["white"]]

    # ── 그리기 ──

    _MAX_BOARD = 420

    def _board(self, width: int) -> float:
        return float(min(self._MAX_BOARD, width - 2 * draw.PAD))

    def measure(self, data, width, ctx) -> int:
        # 판의 상태와 상관없이 같은 높이(새 판을 눌러도 위젯 크기가 그대로다).
        return int(draw.head_y() + draw.HEAD + draw.GAP + self._board(width) + 12
                   + draw.BTN_H + draw.PAD)

    def _state(self, data: dict) -> str:
        if data["winner"] == DRAW:
            return tr("omok.draw")
        if data["winner"]:
            return tr("omok.won", name=data[data["winner"]] or tr("omok." + data["winner"]))
        color = to_move(data)
        return tr("omok.turn", name=data[color] or tr("omok." + color))

    def _can_new(self, data: dict, viewer: str) -> bool:
        if not viewer or not data["moves"]:
            return False
        in_game = viewer in (data["black"], data["white"]) or self.is_owner(data, viewer)
        return in_game or bool(data["winner"])

    def paint(self, p: QPainter, data, width, ctx, pal) -> list[Hit]:
        hits: list[Hit] = []
        x0 = draw.PAD
        inner = width - 2 * draw.PAD
        top = draw.head_y()
        b, small = draw.bold(14), draw.font(12)
        # 머리 줄: 지금 상태(누구 차례, 누가 이겼나).
        draw.text(p, x0, top, draw.content_right(width) - x0, draw.HEAD, self._state(data),
                  pal.red if data["winner"] and data["winner"] != DRAW else pal.ink, b)
        side = self._board(width)
        bx = x0 + (inner - side) / 2
        y0 = top + draw.HEAD + draw.GAP
        cell = side / SIZE
        p.setRenderHint(QPainter.Antialiasing)

        def pos(i: int) -> float:
            return cell * (i + 0.5)

        p.setPen(QPen(pal.line_strong, 1))
        for i in range(SIZE):
            p.drawLine(QPointF(bx + pos(0), y0 + pos(i)), QPointF(bx + pos(SIZE - 1), y0 + pos(i)))
            p.drawLine(QPointF(bx + pos(i), y0 + pos(0)), QPointF(bx + pos(i), y0 + pos(SIZE - 1)))
        p.setPen(Qt.NoPen)
        p.setBrush(pal.faint)
        for sx, sy in _STARS:
            p.drawEllipse(QPointF(bx + pos(sx), y0 + pos(sy)), 2.2, 2.2)
        taken = set()
        r = cell * 0.43
        for i, (mx, my) in enumerate(data["moves"]):
            taken.add((mx, my))
            _stone(p, QPointF(bx + pos(mx), y0 + pos(my)), r, i % 2 == 0)
        if data["moves"]:
            lx, ly = data["moves"][-1]
            black_last = len(data["moves"]) % 2 == 1
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#FFFFFF" if black_last else "#1F1F1F"))
            p.drawEllipse(QPointF(bx + pos(lx), y0 + pos(ly)), r * 0.22, r * 0.22)
        if data["line"]:
            first, last = data["line"][0], data["line"][-1]
            p.setPen(QPen(pal.red, 2, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(bx + pos(first[0]), y0 + pos(first[1])),
                       QPointF(bx + pos(last[0]), y0 + pos(last[1])))
        p.setBrush(Qt.NoBrush)
        if ctx.interactive and can_move(data, ctx.viewer):
            black_next = to_move(data) == BLACK
            hover = ctx.view.get("hover") or {}
            if hover.get("op") == "move":
                # 둘 자리에 옅은 돌을 미리 보인다.
                p.setOpacity(0.35)
                _stone(p, QPointF(bx + pos(hover["x"]), y0 + pos(hover["y"])), r, black_next)
                p.setOpacity(1.0)
            for gy in range(SIZE):
                for gx in range(SIZE):
                    if (gx, gy) not in taken:
                        hits.append(Hit(bx + cell * gx, y0 + cell * gy, cell, cell,
                                        {"op": "move", "x": gx, "y": gy}))
        y = y0 + side + 12
        # 바닥 줄: 두 사람(왼쪽)과 새 판(오른쪽). 늘 한 줄이다.
        right = x0 + inner
        if ctx.interactive and ctx.viewer:
            label = tr("omok.new")
            action = {"op": "new_game"}
            ok = self._can_new(data, ctx.viewer)
            right -= draw.button_width(label)
            hit = draw.button(p, right, y, label, pal, primary=bool(data["winner"]) and ok,
                              ctx=ctx, action=action, enabled=ok)
            if ok:
                hits.append(Hit(*hit, action))
            right -= 12
        cx = x0
        room = max(0.0, right - x0)
        for color in (BLACK, WHITE):
            _stone(p, QPointF(cx + 6, y + draw.BTN_H / 2), 5.5, color == BLACK)
            who = data[color] or tr("omok." + color)
            tw = min(room / 2 - 22, draw.text_width(small, who) + 2)
            if tw <= 8:
                break
            draw.text(p, cx + 16, y, tw, draw.BTN_H, who, pal.ink if data[color] else pal.muted,
                      small)
            cx += 16 + tw + 14
        return hits


def _stone(p: QPainter, center: QPointF, r: float, black: bool) -> None:
    if black:
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#1F1F1F"))
    else:
        p.setPen(QPen(QColor("#1F1F1F"), 1))
        p.setBrush(QColor("#FFFFFF"))
    p.drawEllipse(QRectF(center.x() - r, center.y() - r, 2 * r, 2 * r))
    p.setBrush(Qt.NoBrush)


OMOK = register(Omok())
