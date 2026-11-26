"""메모 안 위젯의 공통 틀.

위젯은 데이터(JSON 으로 옮길 수 있는 dict)만 가진다. 코드는 앱 안에만 있다.
여럿이 함께 누르는 위젯(투표 한 표, 오목 한 수)은 이렇게 돈다.

1. 참여 PC 가 동작(op, dict)을 공유 PC 로 보낸다.
2. 공유 PC 는 받은 차례대로 :meth:`WidgetKind.apply_op` 로 적용한다. 같은 데이터와
   같은 동작이면 언제 어디서 돌려도 같은 결과가 나오는 순수 함수다.
3. 공유 PC 가 새 데이터를 모두에게 나눈다. 받는 PC 는 :meth:`WidgetKind.validate`
   를 거친 뒤 다시 그린다.

모든 종류가 지키는 것
- 데이터의 공통 칸: ``kind``, ``v``(형식 판), ``owner``(만든 사람), ``bg``(바탕).
- 위젯 하나의 데이터는 ``json.dumps(ensure_ascii=False).encode()`` 로 64 KB 까지다.
  넘게 하는 동작은 ``too_large`` 로 거절한다.
- 남이 보낸 글은 길이를 자르고 제어 문자와 방향 바꿈 문자를 뺀다. 그리기는
  QPainter.drawText 로 평문만 그린다(서식으로 읽지 않으니 파일을 열지 않는다).
- 만든 사람만 하는 동작은 ``user == owner`` 로 확인한다. 만든 사람이 비어 있으면
  (옛 데이터) 누구나 할 수 있다.

그리기와 편집기는 Qt 가 필요하다. 이 파일과 각 종류의 데이터 함수는 Qt 없이 돈다."""
from __future__ import annotations

import copy
import json
import re
import uuid
from datetime import date
from typing import NamedTuple

from .. import config

MAX_BYTES = 64 * 1024
MAX_TEXT = 200
MAX_NAME = config.MAX_NICKNAME_LENGTH
VERSION = 1

BG_WHITE, BG_CLEAR = "white", "clear"
BACKGROUNDS = (BG_WHITE, BG_CLEAR)

# 공유 PC 가 동작을 거절한 까닭(texts 의 "err." 뒤에 붙는 키).
E_BAD_OP = "bad_op"
E_INVALID = "invalid"
E_NOT_ALLOWED = "not_allowed"
E_CLOSED = "closed"
E_TOO_LARGE = "too_large"
E_LIMIT = "limit"
E_NOT_FOUND = "not_found"
E_EXISTS = "exists"
E_STARTED = "started"
E_NOT_STARTED = "not_started"
E_GAME_OVER = "game_over"
E_NOT_YOUR_TURN = "not_your_turn"
E_OCCUPIED = "occupied"
E_NEWER = "newer"
ERRORS = (E_BAD_OP, E_INVALID, E_NOT_ALLOWED, E_CLOSED, E_TOO_LARGE, E_LIMIT, E_NOT_FOUND,
          E_EXISTS, E_STARTED, E_NOT_STARTED, E_GAME_OVER, E_NOT_YOUR_TURN, E_OCCUPIED,
          E_NEWER)

_ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
# 한 줄 글에서 빼는 글자: 제어 문자(줄바꿈 포함)와 글의 방향을 바꾸는 문자.
_DROP = re.compile("[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069\u200e\u200f]")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")


class WidgetError(ValueError):
    """데이터나 동작을 받을 수 없다. ``key`` 는 :data:`ERRORS` 가운데 하나."""

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


def new_id() -> str:
    """새 위젯 id(32 자 16진수)."""
    return uuid.uuid4().hex


def valid_id(value) -> bool:
    return isinstance(value, str) and _ID.match(value) is not None


def clean_text(value, limit: int = MAX_TEXT) -> str:
    """한 줄 글. 글이 아니면 빈 글. 앞뒤 빈칸을 빼고 ``limit`` 자로 자른다."""
    if not isinstance(value, str):
        return ""
    value = _DROP.sub("", value[: limit * 4 + 16]).strip()
    return value[:limit].strip()


def clean_name(value) -> str:
    return clean_text(value, MAX_NAME)


def as_int(value, lo: int, hi: int, default: int | None = None) -> int | None:
    """``lo`` 부터 ``hi`` 사이의 정수만. bool 은 정수로 치지 않는다."""
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        return default
    return value


def as_bool(value, default: bool = False) -> bool:
    return value if isinstance(value, bool) else default


def as_date(value) -> date | None:
    """'YYYY-MM-DD' 글을 날짜로. 1900 년부터 2200 년까지만."""
    if not isinstance(value, str) or not _DATE.match(value):
        return None
    try:
        d = date.fromisoformat(value)
    except ValueError:
        return None
    return d if 1900 <= d.year <= 2200 else None


def json_size(data) -> int:
    return len(json.dumps(data, ensure_ascii=False).encode())


def fits(data) -> bool:
    try:
        return json_size(data) <= MAX_BYTES
    except (TypeError, ValueError, RecursionError):
        return False


# 위젯 폭의 범위(논리 픽셀). 0 은 메모 폭에 맞춤.
MIN_WIDTH, MAX_WIDTH = 200, 900


def clean_width(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return 0
    return max(MIN_WIDTH, min(MAX_WIDTH, value))


# 위젯 높이의 범위(높이를 고를 수 있는 종류만 쓴다). 0 은 내용에 맞춤.
MIN_HEIGHT, MAX_HEIGHT = 160, 1200


def clean_height(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return 0
    return max(MIN_HEIGHT, min(MAX_HEIGHT, value))


def take(raw, limit: int) -> list:
    """목록의 앞 ``limit`` 개. 목록이 아니면 빈 목록(남이 보낸 긴 목록을 다 훑지 않는다)."""
    return raw[:limit] if isinstance(raw, list) else []


class Hit(NamedTuple):
    """편집기에서 누를 수 있는 자리. ``action`` 은 공유 PC 로 보낼 동작({"op": ...})
    이거나 이 PC 에서만 바뀌는 보기({"local": ...})다."""
    x: float
    y: float
    w: float
    h: float
    action: dict

    def contains(self, px: float, py: float) -> bool:
        return self.x <= px < self.x + self.w and self.y <= py < self.y + self.h

    def center(self) -> tuple[float, float]:
        return self.x + self.w / 2, self.y + self.h / 2


class Ctx:
    """그릴 때 쓰는 것. ``background`` 는 위젯 뒤의 메모 색이다(투명 바탕일 때 글자
    색을 고른다). ``view`` 는 편집기만 가진 보기 상태(달력의 달 등)."""

    def __init__(self, viewer: str = "", today: date | None = None,
                 background: str = "#FFFFFF", interactive: bool = False,
                 view: dict | None = None) -> None:
        self.viewer = clean_name(viewer)
        self.today = today or date.today()
        self.background = background
        self.interactive = interactive
        self.view = view if view is not None else {}


class WidgetKind:
    """종류마다 하나씩 만드는 틀. 데이터 쪽 함수는 입력을 바꾸지 않는다."""

    kind: str = ""
    is_game: bool = False
    # 흰 바탕과 투명 바탕을 고를 수 있는가(달력, 그래프, 투표, D-day).
    bg_option: bool = False
    # 높이도 끌어서 바꿀 수 있는가(그래프). 나머지는 내용에 맞는 높이다.
    sized_height: bool = False

    # ── 데이터 ──

    def new_data(self, owner: str, **options) -> dict:
        """새 위젯의 데이터. 선택 사항은 종류마다 다르다(모르는 것은 무시한다)."""
        data = {"kind": self.kind, "v": VERSION, "owner": clean_name(owner),
                "bg": options.pop("bg", BG_WHITE)}
        data.update(self._new(**options))
        return self.validate(data)

    def validate(self, data) -> dict:
        """다른 PC 에서 온 데이터를 확인해 깨끗한 새 dict 로 돌려준다.

        모양이 틀린 칸은 버리거나 기본값으로 둔다. 종류가 다르거나 dict 가 아니면
        ``invalid``, 더 새 형식이면 ``newer``, 64 KB 를 넘으면 ``too_large`` 로
        :class:`WidgetError` 를 낸다."""
        if not isinstance(data, dict) or data.get("kind") != self.kind:
            raise WidgetError(E_INVALID)
        v = data.get("v", VERSION)
        if isinstance(v, int) and not isinstance(v, bool) and v > VERSION:
            raise WidgetError(E_NEWER)
        bg = data.get("bg")
        out = {"kind": self.kind, "v": VERSION, "owner": clean_name(data.get("owner")),
               "bg": bg if bg in BACKGROUNDS and self.bg_option else BG_WHITE,
               "w": clean_width(data.get("w")), "h": clean_height(data.get("h"))}
        out.update(self._clean(data))
        if not fits(out):
            raise WidgetError(E_TOO_LARGE)
        return out

    def apply_op(self, data: dict, op: dict, user: str, now_ms: int) -> tuple[dict, str | None]:
        """동작 하나를 적용한 새 데이터와 거절한 까닭(없으면 None).

        입력은 바꾸지 않는다. 거절하면 확인을 거친 지금 데이터를 그대로 돌려준다
        (데이터 자체를 받을 수 없으면 받은 것을 그대로)."""
        try:
            current = self.validate(data)
        except WidgetError as e:
            return data, e.key
        if not isinstance(op, dict) or not isinstance(op.get("op"), str):
            return current, E_BAD_OP
        if not fits(op):
            return current, E_TOO_LARGE
        user = clean_name(user)
        if not user:
            return current, E_NOT_ALLOWED
        if isinstance(now_ms, bool) or not isinstance(now_ms, int):
            now_ms = 0
        work = copy.deepcopy(current)
        name = op["op"]
        try:
            if name == "set_bg":
                err = self._set_bg(work, op)
            elif name == "set_size":
                # 위젯의 폭(0 은 메모 폭에 맞춤). 누구나 바꾼다(보이는 크기일 뿐이다).
                w, h = op.get("w"), op.get("h", work.get("h", 0))
                if (isinstance(w, bool) or not isinstance(w, int)
                        or isinstance(h, bool) or not isinstance(h, int)):
                    err = E_INVALID
                else:
                    work["w"] = clean_width(w)
                    work["h"] = clean_height(h) if self.sized_height else 0
                    err = None
            else:
                err = self._apply(work, name, op, user, now_ms)
            if err is None:
                work = self.validate(work)
        except WidgetError as e:
            err = e.key
        if err is not None:
            return current, err
        return work, None

    def prepare_op(self, op: dict) -> dict:
        """공유 PC 가 적용 전에 부른다. 공유 PC 만 정하는 값(사다리의 seed)을 채우고
        참여 PC 가 보낸 같은 이름의 값은 덮어쓴다. 기본은 그대로 복사."""
        return dict(op) if isinstance(op, dict) else {}

    def is_owner(self, data: dict, user: str) -> bool:
        owner = data.get("owner") or ""
        return not owner or owner == user

    def search_text(self, data) -> str:
        """찾기용 글. 받을 수 없는 데이터면 빈 글."""
        try:
            clean = self.validate(data)
        except WidgetError:
            return ""
        return " ".join(t for t in self._texts(clean) if t)

    def _set_bg(self, data: dict, op: dict) -> str | None:
        if not self.bg_option:
            return E_BAD_OP
        if op.get("bg") not in BACKGROUNDS:
            return E_INVALID
        data["bg"] = op["bg"]
        return None

    # 종류마다 채우는 것
    def _new(self, **options) -> dict:
        return {}

    def _clean(self, data: dict) -> dict:
        return {}

    def _apply(self, data: dict, name: str, op: dict, user: str, now_ms: int) -> str | None:
        """``data``(복사본)를 고친다. 거절하면 까닭을 돌려준다."""
        return E_BAD_OP

    def _texts(self, data: dict) -> list[str]:
        return []

    # ── 그리기(Qt) ──

    def measure(self, data: dict, width: int, ctx: Ctx) -> int:
        raise NotImplementedError

    def paint(self, p, data: dict, width: int, ctx: Ctx, pal) -> list[Hit]:
        raise NotImplementedError

    def render(self, data, width: int, *, viewer: str = "", today: date | None = None,
               background: str = "#FFFFFF", ratio: float = 1.0, view: dict | None = None):
        """지금 모습을 그린 QImage. 화면에도, 본문에 넣을 PNG 에도 쓴다.

        ``background`` 는 위젯 뒤의 메모 색, ``ratio`` 는 화면 배율이다. 받을 수 없는
        데이터면 빈 틀을 그린다(None 을 돌려주지 않는다)."""
        from .draw import render_image
        return render_image(self, data, width, Ctx(viewer, today, background, False, view),
                            ratio)

    def snapshot(self, data, widget_id: str, width: int, *, today: date | None = None,
                 background: str = "#FFFFFF", ratio: float = 1.0) -> bytes:
        """본문에 넣을 PNG. 보는 사람과 상관없는 모습(내 표시 없음)이고 위젯 id 를 담는다."""
        from .png import to_png
        return to_png(self.render(data, width, viewer="", today=today, background=background,
                                  ratio=ratio), widget_id)

    def create_editor(self, data, viewer: str, today: date | None = None, *,
                      background: str = "#FFFFFF", parent=None):
        """누를 수 있는 편집기(QWidget). 사용자가 무엇을 하면 ``opRequested(dict)`` 를
        낼 뿐 데이터를 직접 바꾸지 않는다. 새 데이터는 ``set_data`` 로 넣는다."""
        from .editor import WidgetEditor
        return WidgetEditor(self, data, viewer, today or date.today(),
                            background=background, parent=parent)

    # 편집기의 갈래(필요한 종류만 채운다)
    def build_extras(self, editor) -> None:
        """그림 아래에 입력칸 같은 것을 더한다."""

    def sync_extras(self, editor) -> None:
        """데이터가 바뀐 뒤 입력칸을 맞춘다."""

    def local_action(self, editor, action: dict) -> None:
        """{"local": ...} 를 눌렀을 때(보기만 바뀐다)."""

    def context_actions(self, editor, x: float, y: float) -> list[tuple[str, dict, bool | None]]:
        """오른쪽 단추 메뉴: (이름, 동작, 고름 표시)."""
        return []

    def menu_choices(self, data: dict) -> list[tuple[str, list[tuple[str, dict, bool]]]]:
        """'⋯' 메뉴에 더할 고르기: [(하위 메뉴 이름, [(이름, 동작, 고름)])]."""
        return []

    def input_op(self, data: dict, purpose: str, text: str, viewer: str) -> dict | None:
        """판 위의 '+ 추가' 칸에 적은 글로 보낼 동작(없으면 None). {"local": "input"}
        을 누르면 위젯 칸이 그 자리에 입력 칸을 띄운다."""
        return None


# ── 종류 목록 ──

_KINDS: dict[str, WidgetKind] = {}
# 넣기 메뉴의 차례. 함께 누르는 도구를 앞에 둔다.
_ORDER = ("vote", "order", "ladder", "dday", "calendar", "graph", "omok")


def register(kind: WidgetKind) -> WidgetKind:
    _KINDS[kind.kind] = kind
    return kind


def _load() -> None:
    if len(_KINDS) >= 7:
        return
    from . import vote, order, ladder, dday, calendar, graph, omok  # noqa: F401


def kinds() -> tuple[str, ...]:
    _load()
    rank = {k: i for i, k in enumerate(_ORDER)}
    return tuple(sorted(_KINDS, key=lambda k: (rank.get(k, len(rank)), k)))


def get(kind) -> WidgetKind | None:
    """종류 이름으로 찾는다. 모르는 종류면 None."""
    _load()
    return _KINDS.get(kind) if isinstance(kind, str) else None


def kind_of(data) -> WidgetKind | None:
    return get(data.get("kind")) if isinstance(data, dict) else None


def usable_kind(data) -> WidgetKind | None:
    """이 앱이 그릴 수 있는 위젯 데이터의 종류. 모르는 종류나 확인을 통과하지 못하는
    데이터(새 판의 앱이 만든 것 등)는 None: 저장된 그림을 그대로 둔다."""
    kind = kind_of(data)
    if kind is None:
        return None
    try:
        kind.validate(data)
    except WidgetError:
        return None
    return kind


def validate_any(data) -> dict:
    """종류를 가려 확인한다. 모르는 종류면 ``invalid``."""
    kind = kind_of(data)
    if kind is None:
        raise WidgetError(E_INVALID)
    return kind.validate(data)


def apply_any(data, op, user: str, now_ms: int) -> tuple[object, str | None]:
    kind = kind_of(data)
    if kind is None:
        return data, E_INVALID
    return kind.apply_op(data, op, user, now_ms)
