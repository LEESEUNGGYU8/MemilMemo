"""모니터 구성마다 메모 자리를 따로 기억한다.

노트북만 쓸 때와 모니터를 꽂았을 때처럼 모니터 구성이 바뀌면, 그 구성에서 마지막으로
둔 자리로 메모를 옮긴다. 처음 보는 구성이면 지금처럼 화면 안으로 모으고, 그 뒤에
옮긴 자리를 그 구성의 자리로 기억한다.

구성의 열쇠는 모니터마다의 장치 이름, 위치, 크기, 배율을 정렬해 이은 글이다. 메모
자리는 local_state 의 screen_layouts(열쇠 → 메모 id → 자리)에 이 PC 에만 적는다."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QGuiApplication

# 모니터를 꽂고 뽑을 때는 신호가 여러 번 잇따라 온다. 조용해진 뒤 한 번만 본다.
SETTLE_MS = 1500
# 기억하는 구성의 수(오래된 것부터 버린다).
MAX_LAYOUTS = 8


def screens_key(screens) -> str:
    """모니터 구성의 열쇠. ``screens`` 는 (이름, x, y, 폭, 높이, 배율) 들."""
    parts = sorted(f"{name}:{x},{y},{w}x{h}@{round(float(dpr), 2)}"
                   for name, x, y, w, h, dpr in screens)
    return "|".join(parts)


def current_screens() -> list[tuple]:
    out = []
    for s in QGuiApplication.screens():
        g = s.geometry()
        out.append((s.name(), g.x(), g.y(), g.width(), g.height(), s.devicePixelRatio()))
    return out


class ScreenLayouts(QObject):
    """모니터 구성이 바뀐 것을 알린다(``switched(전, 뒤)``)."""

    switched = Signal(str, str)

    def __init__(self, parent: QObject | None = None, *,
                 screens: Callable[[], list] = current_screens,
                 watch: bool = True) -> None:
        super().__init__(parent)
        self._screens = screens
        self._key = screens_key(self._screens())
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(SETTLE_MS)
        self._timer.timeout.connect(self.check)
        if watch:
            app = QGuiApplication.instance()
            if app is not None:
                app.screenAdded.connect(self._poke)
                app.screenRemoved.connect(self._poke)
                for screen in app.screens():
                    self._watch(screen)

    def _watch(self, screen) -> None:
        screen.geometryChanged.connect(self._poke)
        screen.logicalDotsPerInchChanged.connect(self._poke)

    def _poke(self, *args) -> None:
        for arg in args:
            if hasattr(arg, "geometryChanged"):
                try:
                    self._watch(arg)
                except (RuntimeError, TypeError):
                    pass
        self._timer.start()

    @property
    def key(self) -> str:
        return self._key

    def check(self) -> None:
        new = screens_key(self._screens())
        if new != self._key:
            old, self._key = self._key, new
            self.switched.emit(old, new)


def keep_recent(layouts: dict, key: str) -> None:
    """``key`` 를 가장 최근 것으로 두고, 너무 많으면 오래된 구성부터 버린다."""
    if key in layouts:
        layouts[key] = layouts.pop(key)
    while len(layouts) > MAX_LAYOUTS:
        layouts.pop(next(iter(layouts)))
