"""바탕화면 아이콘을 고르거나 마우스를 올렸을 때 그 뒤에 깔리는 판.

Windows 가 바탕화면 아이콘에 쓰는 그림(시각 스타일의 ``Desktop::ListView``)을
그대로 받아 그린다. 그래서 Windows 버전이나 테마가 달라도 진짜 아이콘과 같은
모양이다. 테마에 묻기만 하고 바탕화면은 건드리지 않는다. 받지 못하면(시각
스타일이 꺼져 있다, Windows 가 아니다) 같은 색으로 직접 그린다."""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage, QPainter, QPen

HOT = "hot"
SELECTED = "selected"
# 어디에 놓인 아이콘인가: 바탕화면, 탐색기 창(폴더를 연 창).
DESKTOP = "Desktop::ListView"
WINDOW = "Explorer::ListView"
# 시각 스타일의 목록 항목(LVP_LISTITEM)과 그 상태(LISS_HOT, LISS_SELECTED).
_PART_ITEM = 1
_STATES = {HOT: 2, SELECTED: 3}
# 직접 그릴 때 쓰는 값(Windows 11 의 그림에서 잰 것): 채움, 테두리.
_FALLBACK = {
    (DESKTOP, HOT): (QColor(218, 218, 218, 41), QColor(181, 181, 181, 48)),
    (DESKTOP, SELECTED): (QColor(218, 218, 218, 82), QColor(188, 188, 188, 91)),
    (WINDOW, HOT): (QColor(0, 137, 255, 26), QColor(0, 137, 255, 26)),
    (WINDOW, SELECTED): (QColor(0, 140, 255, 51), QColor(0, 120, 212, 140)),
}
_cache: dict[tuple[int, int, str, str], QImage | None] = {}


class _BitmapHeader(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


def _themed(width: int, height: int, state: str,
            theme_class: str = DESKTOP) -> QImage | None:
    """Windows 가 그려 주는 판(물리 픽셀). 그릴 수 없으면 None."""
    if sys.platform != "win32" or width <= 0 or height <= 0:
        return None
    try:
        uxtheme, gdi32 = ctypes.windll.uxtheme, ctypes.windll.gdi32
    except (AttributeError, OSError):
        return None
    uxtheme.OpenThemeData.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
    uxtheme.OpenThemeData.restype = wintypes.HANDLE
    uxtheme.DrawThemeBackground.argtypes = [
        wintypes.HANDLE, wintypes.HDC, ctypes.c_int, ctypes.c_int,
        ctypes.POINTER(wintypes.RECT), ctypes.POINTER(wintypes.RECT)]
    uxtheme.DrawThemeBackground.restype = ctypes.c_long
    uxtheme.CloseThemeData.argtypes = [wintypes.HANDLE]
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateDIBSection.argtypes = [
        wintypes.HDC, ctypes.POINTER(_BitmapHeader), wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
    gdi32.CreateDIBSection.restype = wintypes.HANDLE
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
    gdi32.SelectObject.restype = wintypes.HANDLE
    gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]

    theme = uxtheme.OpenThemeData(None, theme_class)
    if not theme:
        return None
    dc = bitmap = old = None
    try:
        header = _BitmapHeader()
        header.biSize = ctypes.sizeof(header)
        header.biWidth, header.biHeight = width, -height    # 위에서 아래로
        header.biPlanes, header.biBitCount = 1, 32
        bits = ctypes.c_void_p()
        dc = gdi32.CreateCompatibleDC(None)
        if not dc:
            return None
        bitmap = gdi32.CreateDIBSection(dc, ctypes.byref(header), 0,
                                        ctypes.byref(bits), None, 0)
        if not bitmap or not bits:
            return None
        old = gdi32.SelectObject(dc, bitmap)
        ctypes.memset(bits, 0, width * height * 4)
        rect = wintypes.RECT(0, 0, width, height)
        if uxtheme.DrawThemeBackground(theme, dc, _PART_ITEM, _STATES[state],
                                       ctypes.byref(rect), None) != 0:
            return None
        raw = ctypes.string_at(bits, width * height * 4)
        image = QImage(raw, width, height, width * 4,
                       QImage.Format_ARGB32_Premultiplied).copy()
        # 아무것도 그리지 않는 테마(고전 테마)면 직접 그린다.
        if image.pixelColor(width // 2, height // 2).alpha() == 0:
            return None
        return image
    except (OSError, ValueError):
        return None
    finally:
        if dc and old:
            gdi32.SelectObject(dc, old)
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if dc:
            gdi32.DeleteDC(dc)
        uxtheme.CloseThemeData(theme)


def clear_cache() -> None:
    """테마가 바뀌었을 때 받아 둔 그림을 버린다."""
    _cache.clear()


def paint(p: QPainter, rect: QRectF, state: str, dpr: float = 1.0,
          opacity: float = 1.0, theme: str = DESKTOP) -> None:
    """``rect`` 에 판을 그린다. ``state`` 는 HOT 또는 SELECTED, ``theme`` 은
    DESKTOP 또는 WINDOW."""
    if state not in _STATES or rect.width() <= 0 or rect.height() <= 0:
        return
    if theme not in (DESKTOP, WINDOW):
        theme = DESKTOP
    dpr = dpr or 1.0
    width = max(1, int(round(rect.width() * dpr)))
    height = max(1, int(round(rect.height() * dpr)))
    key = (width, height, state, theme)
    if key not in _cache:
        if len(_cache) > 64:
            _cache.clear()
        _cache[key] = _themed(width, height, state, theme)
    image = _cache[key]
    p.save()
    p.setOpacity(p.opacity() * opacity)
    if image is not None:
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawImage(rect, image)
    else:
        fill, edge = _FALLBACK[(theme, state)]
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(edge, 1.0))
        p.setBrush(fill)
        p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 3.0, 3.0)
    p.restore()


__all__ = ["DESKTOP", "HOT", "SELECTED", "WINDOW", "clear_cache", "paint"]
