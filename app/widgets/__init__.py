"""메모 안 위젯: 투표, 주문 모으기, 사다리 타기, D-day, 달력, 그래프, 오목.

    from app import widgets
    kind = widgets.get("vote")
    data = kind.new_data("지우", question="점심", options=["국밥", "냉면"])
    data, error = kind.apply_op(data, {"op": "vote", "option": 1}, "서연", now_ms)

자세한 약속은 :mod:`app.widgets.base` 에 있다."""
from __future__ import annotations

from .base import (BACKGROUNDS, BG_CLEAR, BG_WHITE, ERRORS, MAX_BYTES, Ctx, Hit, WidgetError,
                   WidgetKind, apply_any, get, kind_of, usable_kind, kinds, new_id, valid_id, validate_any)
from .png import (id_from_data_url, ids_in_html, label_from_data_url, read_id, read_label,
                  to_data_url, to_png, widgets_as_text)

__all__ = [
    "BACKGROUNDS", "BG_CLEAR", "BG_WHITE", "ERRORS", "MAX_BYTES", "Ctx", "Hit", "WidgetError",
    "WidgetKind", "apply_any", "get", "kind_of", "kinds", "new_id", "valid_id", "validate_any",
    "id_from_data_url", "ids_in_html", "read_id", "to_data_url", "to_png",
    "label_from_data_url", "read_label", "widgets_as_text", "label_of", "usable_kind",
]


def label_of(data) -> str:
    """위젯의 보이는 이름: '투표: 오늘 점심' 처럼 종류와 첫 글."""
    from .texts import tr
    kind = kind_of(data)
    if kind is None:
        return ""
    name = tr("kind." + kind.kind)
    try:
        texts = [t for t in kind._texts(kind.validate(data)) if t] if data else []
    except WidgetError:
        texts = []                  # 새 판의 데이터 등: 이름만
    first = texts[0][:40] if texts else ""
    return f"{name}: {first}" if first else name
