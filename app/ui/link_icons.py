"""파일 링크 앞에 붙는 종류별 아이콘.

메모 색이 여덟 가지(어두운 색 포함)라 글자색을 따라가는 단색 아이콘 대신,
흰 종이 바탕에 종류별 색 띠를 두른 그림을 쓴다. 어느 메모 색 위에서도 같은
모양으로 읽히고, 16px 안팎에서는 색 띠가 종류를 가른다."""
from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from .. import file_links as fl

_LINE = "#4B5563"

_PAGE = (
    '<path d="M6.5 2.5H14l5 5V20a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 5 20'
    'V4a1.5 1.5 0 0 1 1.5-1.5z" fill="#FFFFFF" stroke="' + _LINE + '" '
    'stroke-width="1.3" stroke-linejoin="round"/>'
    '<path d="M14 2.5v5h5" fill="none" stroke="' + _LINE + '" '
    'stroke-width="1.3" stroke-linejoin="round"/>'
)


def _band(color: str) -> str:
    """종이 아래쪽을 가로지르는 색 띠(파일 형식 딱지)."""
    return (f'<rect x="2.5" y="12" width="19" height="7.5" rx="1.8" '
            f'fill="{color}"/>')


def _glyph(d: str, *, fill: bool = False) -> str:
    if fill:
        return f'<path d="{d}" fill="#FFFFFF"/>'
    return (f'<path d="{d}" fill="none" stroke="#FFFFFF" stroke-width="1.5" '
            'stroke-linecap="round" stroke-linejoin="round"/>')


_BODY = {
    fl.KIND_FILE: _PAGE,
    fl.KIND_TEXT: _PAGE + (
        '<path d="M8.5 12h7M8.5 15h7M8.5 18h4.5" stroke="#6B7280" '
        'stroke-width="1.4" stroke-linecap="round"/>'),
    fl.KIND_PDF: _PAGE + _band("#E5484D") + _glyph(
        "M7 17.6v-3.6h1.3a1.1 1.1 0 0 1 0 2.2H7"
        "M11 14v3.6h.9a1.8 1.8 0 0 0 0-3.6z"
        "M15.4 17.6V14h2M15.4 15.9h1.6"),
    fl.KIND_WORD: _PAGE + _band("#2B7CD3") + _glyph(
        "M7.5 14l1.3 3.6 1.3-3.6 1.3 3.6 1.3-3.6M15 14.4h2.5M15 17.2h2.5"),
    fl.KIND_HWP: _PAGE + _band("#1FA3C9") + _glyph(
        "M8.6 13.6v4.4M12.2 13.6v4.4M8.6 15.8h3.6M15.2 14.2h2.2M15.2 17.4h2.2"),
    fl.KIND_SHEET: _PAGE + _band("#1E9E5A") + _glyph(
        "M7 14h10M7 17.5h10M10.4 13v5.5M13.8 13v5.5"),
    fl.KIND_SLIDE: _PAGE + _band("#E8713A") + _glyph(
        "M8 17.8v-2M12 17.8v-4M16 17.8v-3"),
    fl.KIND_IMAGE: _PAGE + (
        '<path d="M7.2 18.6l3.3-4.2 2.4 2.7 1.7-1.9 2.6 3.4z" fill="#3BA776"/>'
        '<circle cx="9.7" cy="11.3" r="1.35" fill="#F5B93A"/>'),
    fl.KIND_VIDEO: _PAGE + _band("#7C5CFC") + _glyph(
        "M10.4 13.6v4.4l3.8-2.2z", fill=True),
    fl.KIND_AUDIO: _PAGE + _band("#D6409F") + _glyph(
        "M11 17.4a1.2 1.2 0 1 1-1.2-1.2H11zM11 17.4v-4l3.4-.7v3.6"
        "M14.4 16.3a1.2 1.2 0 1 1-1.2-1.2h1.2"),
    fl.KIND_ARCHIVE: _PAGE + _band("#A66A1F") + (
        '<path d="M12 3.5v8" stroke="' + _LINE + '" stroke-width="1.4" '
        'stroke-dasharray="1.6 1.4"/>') + _glyph(
        "M10.6 14.2h2.8v3.2h-2.8z"),
    fl.KIND_CODE: _PAGE + _band("#475569") + _glyph(
        "M9.6 14l-1.9 1.8 1.9 1.8M14.4 14l1.9 1.8-1.9 1.8M12.8 13.6l-1.6 4.4"),
    fl.KIND_APP: (
        '<rect x="3" y="4.5" width="18" height="15" rx="2.2" fill="#FFFFFF" '
        'stroke="' + _LINE + '" stroke-width="1.3"/>'
        '<path d="M3 8.6h18" stroke="' + _LINE + '" stroke-width="1.3"/>'
        '<circle cx="5.8" cy="6.6" r="0.85" fill="#E5484D"/>'
        '<circle cx="8.4" cy="6.6" r="0.85" fill="#F5B93A"/>'
        '<path d="M10.2 11.6l3.6 2.5-3.6 2.5z" fill="#2B7CD3"/>'),
    fl.KIND_FOLDER: (
        '<path d="M3 7a2 2 0 0 1 2-2h4.3l2 2H19a2 2 0 0 1 2 2v8.5a2 2 0 0 1-2 2'
        'H5a2 2 0 0 1-2-2z" fill="#F7C948" stroke="#A66A1F" stroke-width="1.3" '
        'stroke-linejoin="round"/>'
        '<path d="M3.4 10h17.2" stroke="#A66A1F" stroke-width="1.1" '
        'stroke-opacity="0.55"/>'),
    fl.KIND_WEB: (
        '<circle cx="12" cy="12" r="8.6" fill="#FFFFFF" stroke="#2B7CD3" '
        'stroke-width="1.4"/>'
        '<path d="M3.6 12h16.8M12 3.4c2.9 2.6 2.9 14.6 0 17.2'
        'M12 3.4c-2.9 2.6-2.9 14.6 0 17.2" fill="none" stroke="#2B7CD3" '
        'stroke-width="1.2"/>'),
}

_CACHE: dict[tuple[str, int], QPixmap] = {}


def link_icon_svg(kind: str) -> str:
    body = _BODY.get(kind) or _BODY[fl.KIND_FILE]
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
            + body + '</svg>')


def link_icon_pixmap(kind: str, size: float, dpr: float = 1.0) -> QPixmap:
    """논리 크기 ``size`` 의 아이콘. 화면 배율만큼 큰 그림에 배율을 적어
    돌려주므로 그대로 그리면 또렷하다."""
    dpr = max(1.0, float(dpr))
    px = max(8, int(round(size * dpr)))
    key = (kind if kind in _BODY else fl.KIND_FILE, px)
    pix = _CACHE.get(key)
    if pix is None:
        renderer = QSvgRenderer(QByteArray(link_icon_svg(key[0]).encode("utf-8")))
        pix = QPixmap(px, px)
        pix.fill(Qt.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.Antialiasing)
        renderer.render(painter, QRectF(0, 0, px, px))
        painter.end()
        _CACHE[key] = pix
    out = QPixmap(pix)
    out.setDevicePixelRatio(px / max(1.0, float(size)))
    return out


def link_icon(kind: str, size: int = 18) -> QIcon:
    return QIcon(link_icon_pixmap(kind, size, 2.0))
