from __future__ import annotations

from collections import OrderedDict

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer


_SVG = {
    "plus": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path d="M12 5v14M5 12h14" stroke="{color}" stroke-width="2" '
        'stroke-linecap="round" fill="none"/></svg>'
    ),
    "minus": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path d="M5 12h14" stroke="{color}" stroke-width="2" '
        'stroke-linecap="round" fill="none"/></svg>'
    ),
    "note": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path d="M6.5 4h7.5l4.5 4.5V18a2 2 0 0 1-2 2h-10a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z'
        'M14 4v4.5h4.5" stroke="{color}" stroke-width="2" fill="none" '
        'stroke-linejoin="round"/></svg>'
    ),
    "undo": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path d="M8.5 5.5L4 10l4.5 4.5M4 10h10a5.5 5.5 0 0 1 0 11h-3" stroke="{color}" '
        'stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>'
    ),
    "redo": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path d="M15.5 5.5L20 10l-4.5 4.5M20 10H10a5.5 5.5 0 0 0 0 11h3" stroke="{color}" '
        'stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>'
    ),
    "cut": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<circle cx="6.5" cy="18" r="2.5" stroke="{color}" stroke-width="2" fill="none"/>'
        '<circle cx="17.5" cy="18" r="2.5" stroke="{color}" stroke-width="2" fill="none"/>'
        '<path d="M8.2 16.2L18 4M15.8 16.2L6 4" stroke="{color}" stroke-width="2" '
        'fill="none" stroke-linecap="round"/></svg>'
    ),
    "copy": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<rect x="8.5" y="8.5" width="11" height="12" rx="2" stroke="{color}" '
        'stroke-width="2" fill="none"/>'
        '<path d="M15.5 8.5V6a2 2 0 0 0-2-2H6.5a2 2 0 0 0-2 2v8.5a2 2 0 0 0 2 2h2" '
        'stroke="{color}" stroke-width="2" fill="none" stroke-linecap="round"/></svg>'
    ),
    "paste": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<rect x="5.5" y="5.5" width="13" height="15" rx="2" stroke="{color}" '
        'stroke-width="2" fill="none"/>'
        '<path d="M9 5.5V4.8a1.3 1.3 0 0 1 1.3-1.3h3.4A1.3 1.3 0 0 1 15 4.8v.7M9.5 11h5M9.5 15h5" '
        'stroke="{color}" stroke-width="2" fill="none" stroke-linecap="round"/></svg>'
    ),
    "select_all": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<rect x="4.5" y="4.5" width="15" height="15" rx="2.5" stroke="{color}" '
        'stroke-width="2" fill="none" stroke-dasharray="3 3"/>'
        '<path d="M8.5 12.2l2.4 2.4 4.6-5" stroke="{color}" stroke-width="2" '
        'fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>'
    ),
    "edit": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path d="M5 19l1-4L16.5 4.5a1.8 1.8 0 0 1 2.6 0l.4.4a1.8 1.8 0 0 1 0 2.6L9 18l-4 1z" '
        'stroke="{color}" stroke-width="2" fill="none" stroke-linejoin="round"/></svg>'
    ),
    "download": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path d="M12 4.5v10M7.5 10.5L12 15l4.5-4.5M5 19.5h14" stroke="{color}" '
        'stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>'
    ),
    "trash": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path d="M5 7h14M9.5 7V5.2a1.2 1.2 0 0 1 1.2-1.2h2.6a1.2 1.2 0 0 1 1.2 1.2V7'
        'M7 7l.8 11.3a1.8 1.8 0 0 0 1.8 1.7h4.8a1.8 1.8 0 0 0 1.8-1.7L17 7" stroke="{color}" '
        'stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>'
    ),
    "warning": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path d="M12 4.5l8.5 15h-17z" stroke="{color}" stroke-width="2" fill="none" '
        'stroke-linejoin="round"/>'
        '<path d="M12 10v4.5M12 17.2v.1" stroke="{color}" stroke-width="2" '
        'fill="none" stroke-linecap="round"/></svg>'
    ),
    "clock": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<circle cx="12" cy="12" r="9" stroke="{color}" stroke-width="2" fill="none"/>'
        '<path d="M12 7v5l3 2" stroke="{color}" stroke-width="2" '
        'stroke-linecap="round" fill="none"/></svg>'
    ),
    "eye_off": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path d="M3 3l18 18" stroke="{color}" stroke-width="2" stroke-linecap="round"/>'
        '<path d="M10.5 6.2A10 10 0 0 1 12 6c6.5 0 9 6 9 6a13 13 0 0 1-3.3 3.8" '
        'stroke="{color}" stroke-width="2" stroke-linecap="round" fill="none"/>'
        '<path d="M6.3 7.4C4 9 3 12 3 12s2.5 6 9 6c1.5 0 2.8-.3 4-.8" '
        'stroke="{color}" stroke-width="2" stroke-linecap="round" fill="none"/></svg>'
    ),
    "close": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path d="M6 6l12 12M18 6L6 18" stroke="{color}" stroke-width="2" '
        'stroke-linecap="round"/></svg>'
    ),
    "eye_on": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path d="M12 5C5.5 5 2.5 12 2.5 12S5 19 12 19s9.5-7 9.5-7S18 5 12 5z" '
        'stroke="{color}" stroke-width="2" fill="none" stroke-linejoin="round"/>'
        '<circle cx="12" cy="12" r="3" stroke="{color}" stroke-width="2" fill="none"/></svg>'
    ),
    "search": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<circle cx="10.5" cy="10.5" r="6.5" stroke="{color}" stroke-width="2" fill="none"/>'
        '<path d="M15.5 15.5l5 5" stroke="{color}" stroke-width="2" stroke-linecap="round"/></svg>'
    ),
    "more": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<circle cx="5" cy="12" r="1.5" fill="{color}"/>'
        '<circle cx="12" cy="12" r="1.5" fill="{color}"/>'
        '<circle cx="19" cy="12" r="1.5" fill="{color}"/></svg>'
    ),
    "link": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1" '
        'stroke="{color}" stroke-width="2" fill="none" stroke-linecap="round"/>'
        '<path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1" '
        'stroke="{color}" stroke-width="2" fill="none" stroke-linecap="round"/></svg>'
    ),
    "table": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<rect x="3.5" y="4.5" width="17" height="15" rx="2" stroke="{color}" '
        'stroke-width="2" fill="none"/>'
        '<path d="M3.5 9.5h17M3.5 14.5h17M10 4.5v15" stroke="{color}" stroke-width="2"/></svg>'
    ),
    # 체크 해제 예약: 돌아가는 화살표 안의 체크.
    "check_reset": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path d="M19.5 12a7.5 7.5 0 1 1-2.4-5.5" stroke="{color}" stroke-width="2" '
        'fill="none" stroke-linecap="round"/>'
        '<path d="M19.5 3.5v4h-4" stroke="{color}" stroke-width="2" fill="none" '
        'stroke-linecap="round" stroke-linejoin="round"/>'
        '<path d="M8.5 12.2l2.4 2.4 4.4-4.6" stroke="{color}" stroke-width="2" '
        'fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>'
    ),
    # 넣기(이미지·파일 링크): 종이 집게.
    "attach": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path d="M20 11.4l-8.3 8.3a5 5 0 0 1-7.1-7.1l8.6-8.6a3.4 3.4 0 0 1 '
        '4.8 4.8l-8.5 8.5a1.8 1.8 0 0 1-2.5-2.5l7.8-7.8" stroke="{color}" '
        'stroke-width="2" fill="none" stroke-linecap="round" '
        'stroke-linejoin="round"/></svg>'
    ),
    "bold": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path d="M8 5h5.5a3.5 3.5 0 0 1 0 7H8V5z" '
        'stroke="{color}" stroke-width="2" fill="none" stroke-linejoin="round"/>'
        '<path d="M8 12h6.5a3.5 3.5 0 0 1 0 7H8v-7z" '
        'stroke="{color}" stroke-width="2" fill="none" stroke-linejoin="round"/></svg>'
    ),
    "italic": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path d="M10 5h8M6 19h8M14 5l-4 14" '
        'stroke="{color}" stroke-width="2" stroke-linecap="round" fill="none"/></svg>'
    ),
    "underline": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path d="M7 4v8a5 5 0 0 0 10 0V4" '
        'stroke="{color}" stroke-width="2" stroke-linecap="round" fill="none"/>'
        '<path d="M6 20h12" stroke="{color}" stroke-width="2" stroke-linecap="round"/></svg>'
    ),
    "strike": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path d="M17 7.5c-1.2-1.7-3-2.5-5-2.5-2.8 0-4.5 1.4-4.5 3.2 0 1.6 1.4 2.6 3.5 3.3" '
        'stroke="{color}" stroke-width="2" fill="none" stroke-linecap="round"/>'
        '<path d="M7 16c1 1.8 3 3 5.5 3 2.8 0 4.5-1.4 4.5-3.3 0-1.1-.5-1.9-1.5-2.5" '
        'stroke="{color}" stroke-width="2" fill="none" stroke-linecap="round"/>'
        '<path d="M4 12h16" stroke="{color}" stroke-width="2" stroke-linecap="round"/></svg>'
    ),
    "list": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<circle cx="5" cy="6" r="1.6" fill="{color}"/>'
        '<circle cx="5" cy="12" r="1.6" fill="{color}"/>'
        '<circle cx="5" cy="18" r="1.6" fill="{color}"/>'
        '<path d="M10 6h11M10 12h11M10 18h11" stroke="{color}" stroke-width="2" stroke-linecap="round"/></svg>'
    ),
    "highlight": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path d="M10 14l6.5-6.5 3 3L13 17H10v-3z" stroke="{color}" '
        'stroke-width="1.9" fill="none" stroke-linejoin="round"/>'
        '<path d="M15 6.5l2.5-2.5 3 3-2.5 2.5" stroke="{color}" '
        'stroke-width="1.9" fill="none" stroke-linejoin="round"/>'
        '<path d="M5 20.5h8" stroke="{color}" stroke-width="3" '
        'stroke-linecap="round"/></svg>'
    ),
    "brush": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path d="M5 4.5h11.5a1.5 1.5 0 0 1 1.5 1.5v3a1.5 1.5 0 0 1-1.5 1.5H5z" '
        'stroke="{color}" stroke-width="1.8" fill="none" stroke-linejoin="round"/>'
        '<path d="M18 7.5h1.6v5.2h-7.1v2.3" stroke="{color}" stroke-width="1.8" '
        'fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
        '<rect x="11" y="15" width="3" height="5.5" rx="1" fill="{color}"/></svg>'
    ),
    "text_color": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path d="M11 4h2l5.4 13h-2.5l-1.3-3.3H9.4L8.1 17H5.6L11 4z'
        'M10.15 11.6h3.7L12 7l-1.85 4.6z" fill="{color}" fill-rule="evenodd"/>'
        '<rect x="5" y="19.2" width="14" height="2.6" rx="1.3" fill="{color}"/></svg>'
    ),
    "image": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<rect x="3" y="5" width="18" height="14" rx="2.5" '
        'stroke="{color}" stroke-width="2" fill="none"/>'
        '<circle cx="9" cy="10" r="1.6" fill="{color}"/>'
        '<path d="M3.5 17.5l4.5-4 4 4 3.5-3 5 4.5" '
        'stroke="{color}" stroke-width="2" fill="none" stroke-linejoin="round"/></svg>'
    ),
    "settings": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<circle cx="12" cy="12" r="3.2" stroke="{color}" stroke-width="2" fill="none"/>'
        '<path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1'
        'a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1'
        'a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1'
        'a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1'
        'a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1'
        'a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1'
        'a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1'
        'a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1'
        'a1.7 1.7 0 0 0-1.5 1z" '
        'stroke="{color}" stroke-width="2" fill="none" '
        'stroke-linecap="round" stroke-linejoin="round"/></svg>'
    ),
    "users": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<circle cx="9" cy="9" r="3.4" stroke="{color}" stroke-width="2" fill="none"/>'
        '<path d="M3 19c0-3 2.7-5 6-5s6 2 6 5" stroke="{color}" stroke-width="2" '
        'fill="none" stroke-linecap="round"/>'
        '<path d="M16 11.4a3 3 0 1 0 0-5.8" stroke="{color}" stroke-width="2" '
        'fill="none" stroke-linecap="round"/>'
        '<path d="M17 14.2c2.4.4 4 2 4 4.3" stroke="{color}" stroke-width="2" '
        'fill="none" stroke-linecap="round"/>'
        '</svg>'
    ),
    "lock": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path d="M8 11V8a4 4 0 0 1 8 0v3" stroke="{color}" stroke-width="2" '
        'fill="none" stroke-linecap="round"/>'
        '<rect x="5.5" y="11" width="13" height="9" rx="2.2" '
        'stroke="{color}" stroke-width="2" fill="none"/>'
        '<circle cx="12" cy="15" r="1.2" fill="{color}"/>'
        '</svg>'
    ),
    "folder": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z" '
        'stroke="{color}" stroke-width="2" fill="none" stroke-linejoin="round"/></svg>'
    ),
    "check": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path d="M5 12.5l4.5 4.5L19 7.5" stroke="{color}" stroke-width="2.4" '
        'fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>'
    ),
    "chevron_down": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path d="M6 9l6 6 6-6" stroke="{color}" stroke-width="2.2" '
        'fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>'
    ),
    # '최근 메모' 보기 방식: 줄 목록과 타일 격자.
    "view_list": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<rect x="3.5" y="4.5" width="17" height="4" rx="1.6" '
        'stroke="{color}" stroke-width="1.9" fill="none"/>'
        '<rect x="3.5" y="10" width="17" height="4" rx="1.6" '
        'stroke="{color}" stroke-width="1.9" fill="none"/>'
        '<rect x="3.5" y="15.5" width="17" height="4" rx="1.6" '
        'stroke="{color}" stroke-width="1.9" fill="none"/></svg>'
    ),
    "view_grid": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<rect x="3.5" y="3.5" width="7" height="7" rx="1.8" '
        'stroke="{color}" stroke-width="1.9" fill="none"/>'
        '<rect x="13.5" y="3.5" width="7" height="7" rx="1.8" '
        'stroke="{color}" stroke-width="1.9" fill="none"/>'
        '<rect x="3.5" y="13.5" width="7" height="7" rx="1.8" '
        'stroke="{color}" stroke-width="1.9" fill="none"/>'
        '<rect x="13.5" y="13.5" width="7" height="7" rx="1.8" '
        'stroke="{color}" stroke-width="1.9" fill="none"/></svg>'
    ),
    # 메모 접기(종이 아래쪽을 위로 접어 올리는 모양)와 펼치기.
    # 접힌 쪽지와 같은 모양: 오른쪽 위 모서리를 접어 넣은 납작한 종이.
    "opacity": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<circle cx="12" cy="12" r="8" stroke="{color}" stroke-width="1.8" fill="none"/>'
        '<path d="M12 4a8 8 0 0 1 0 16z" fill="{color}"/></svg>'
    ),
    "wrap": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path d="M4 6.5h16M4 12h12.5a3.2 3.2 0 0 1 0 6.4H12M4 17.5h4.5" stroke="{color}" '
        'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" fill="none"/>'
        '<path d="M14.2 16.2 12 18.4l2.2 2.2" stroke="{color}" stroke-width="1.8" '
        'stroke-linecap="round" stroke-linejoin="round" fill="none"/></svg>'
    ),
    "tally": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path d="M17.5 5.5h-11l6.2 6.5-6.2 6.5h11" stroke="{color}" stroke-width="1.8" '
        'stroke-linecap="round" stroke-linejoin="round" fill="none"/></svg>'
    ),
    "fold": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path d="M5 7.5h10.5L20.5 12.5V15.5a1.5 1.5 0 0 1-1.5 1.5H5'
        'a1.5 1.5 0 0 1-1.5-1.5V9A1.5 1.5 0 0 1 5 7.5z" stroke="{color}" '
        'stroke-width="2" fill="none" stroke-linejoin="round"/>'
        '<path d="M15.5 7.5v5h5" stroke="{color}" stroke-width="2" '
        'fill="none" stroke-linejoin="round"/></svg>'
    ),
    "unfold": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path d="M5 4h14v16H5z" stroke="{color}" stroke-width="2" '
        'fill="none" stroke-linejoin="round"/>'
        '<path d="M5 12h14" stroke="{color}" stroke-width="2" '
        'stroke-dasharray="2.6 2.4"/></svg>'
    ),
    "dock_left": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path d="M4 3v18" stroke="{color}" stroke-width="2.4" stroke-linecap="round"/>'
        '<path d="M4 8h9a2.5 2.5 0 0 1 2.5 2.5v3A2.5 2.5 0 0 1 13 16H4" '
        'stroke="{color}" stroke-width="2" fill="none" stroke-linejoin="round"/></svg>'
    ),
    "dock_right": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path d="M20 3v18" stroke="{color}" stroke-width="2.4" stroke-linecap="round"/>'
        '<path d="M20 8h-9a2.5 2.5 0 0 0-2.5 2.5v3A2.5 2.5 0 0 0 11 16h9" '
        'stroke="{color}" stroke-width="2" fill="none" stroke-linejoin="round"/></svg>'
    ),
    # 이미지 뷰어의 이전/다음 전환 버튼.
    "chevron_left": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path d="M15 5l-7 7 7 7" stroke="{color}" stroke-width="2.2" '
        'fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>'
    ),
    "chevron_right": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path d="M9 5l7 7-7 7" stroke="{color}" stroke-width="2.2" '
        'fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>'
    ),
    # 이미지 뷰어의 '창에 맞춤' (네 모서리 화살표).
    "fit_screen": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path d="M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5" '
        'stroke="{color}" stroke-width="2" fill="none" '
        'stroke-linecap="round" stroke-linejoin="round"/></svg>'
    ),
    # 이미지 뷰어의 '실제 크기' (안쪽에 원본 프레임이 든 사각형).
    "window": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<rect x="3.5" y="5" width="17" height="14" rx="2.5" '
        'stroke="{color}" stroke-width="2" fill="none"/>'
        '<path d="M3.5 9.5h17" stroke="{color}" stroke-width="2" fill="none"/></svg>'
    ),
    "swap": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path d="M5 8h14M16 5l3 3-3 3M19 16H5M8 13l-3 3 3 3" stroke="{color}" '
        'stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>'
    ),
    "power": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path d="M12 4v7.5M7.6 6.9a7 7 0 1 0 8.8 0" stroke="{color}" '
        'stroke-width="2" fill="none" stroke-linecap="round"/></svg>'
    ),
    "actual_size": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<rect x="3.5" y="3.5" width="17" height="17" rx="2.5" '
        'stroke="{color}" stroke-width="2" fill="none"/>'
        '<rect x="8.5" y="8.5" width="7" height="7" rx="1.2" fill="{color}"/></svg>'
    ),
    # 설정 창의 구역 표시.
    "hash": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path d="M9.5 4l-2 16M16.5 4l-2 16M5 9h15M4 15h15" stroke="{color}" '
        'stroke-width="2" stroke-linecap="round" fill="none"/></svg>'
    ),
    "widgets": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<rect x="3.5" y="3.5" width="7" height="7" rx="1.8" stroke="{color}" stroke-width="2" fill="none"/><rect x="13.5" y="3.5" width="7" height="7" rx="1.8" stroke="{color}" stroke-width="2" fill="none"/><rect x="3.5" y="13.5" width="7" height="7" rx="1.8" stroke="{color}" stroke-width="2" fill="none"/><path d="M17 13.5v7M13.5 17h7" stroke="{color}" stroke-width="2" stroke-linecap="round"/></svg>'
    ),
    "w_vote": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<rect x="3.5" y="4" width="17" height="16" rx="2.5" stroke="{color}" stroke-width="1.8" fill="none"/><circle cx="8" cy="9.5" r="1.6" fill="{color}"/><path d="M11.5 9.5h5.5" stroke="{color}" stroke-width="1.8" stroke-linecap="round"/><circle cx="8" cy="15" r="1.6" stroke="{color}" stroke-width="1.4" fill="none"/><path d="M11.5 15h4" stroke="{color}" stroke-width="1.8" stroke-linecap="round"/></svg>'
    ),
    "w_order": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path d="M6 3.5h12v17l-2-1.4-2 1.4-2-1.4-2 1.4-2-1.4-2 1.4z" stroke="{color}" stroke-width="1.8" fill="none" stroke-linejoin="round"/><path d="M9 8.5h6M9 12h6M9 15.5h3.5" stroke="{color}" stroke-width="1.8" stroke-linecap="round"/></svg>'
    ),
    "w_ladder": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path d="M7 3.5v17M17 3.5v17M7 8h10M7 13.5h10M7 18h5" stroke="{color}" stroke-width="1.8" stroke-linecap="round"/></svg>'
    ),
    "w_dday": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<rect x="3.5" y="5" width="17" height="15" rx="2.5" stroke="{color}" stroke-width="1.8" fill="none"/><path d="M8 3v4M16 3v4M3.5 9.5h17" stroke="{color}" stroke-width="1.8" stroke-linecap="round"/><path d="M12 12.2v4.6M9.7 14.5h4.6" stroke="{color}" stroke-width="1.8" stroke-linecap="round"/></svg>'
    ),
    "w_calendar": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<rect x="3.5" y="5" width="17" height="15" rx="2.5" stroke="{color}" stroke-width="1.8" fill="none"/><path d="M8 3v4M16 3v4M3.5 9.5h17" stroke="{color}" stroke-width="1.8" stroke-linecap="round"/><circle cx="8" cy="13.5" r="1" fill="{color}"/><circle cx="12" cy="13.5" r="1" fill="{color}"/><circle cx="16" cy="13.5" r="1" fill="{color}"/><circle cx="8" cy="17" r="1" fill="{color}"/><circle cx="12" cy="17" r="1" fill="{color}"/></svg>'
    ),
    "w_graph": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path d="M3.5 20.2h17" stroke="{color}" stroke-width="1.8" stroke-linecap="round"/><rect x="5.6" y="11.5" width="3.4" height="6.6" rx="0.9" stroke="{color}" stroke-width="1.6" fill="none"/><rect x="10.3" y="4.5" width="3.4" height="13.6" rx="0.9" stroke="{color}" stroke-width="1.6" fill="none"/><rect x="15" y="8.5" width="3.4" height="9.6" rx="0.9" stroke="{color}" stroke-width="1.6" fill="none"/></svg>'
    ),
    "w_omok": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<rect x="3.5" y="3.5" width="17" height="17" rx="2.5" stroke="{color}" stroke-width="1.8" fill="none"/><path d="M9.2 3.5v17M14.8 3.5v17M3.5 9.2h17M3.5 14.8h17" stroke="{color}" stroke-width="1" opacity="0.55"/><circle cx="9.2" cy="9.2" r="2.4" fill="{color}"/><circle cx="14.8" cy="14.8" r="2.4" fill="#FFFFFF" stroke="{color}" stroke-width="1.5"/></svg>'
    ),
    "printer": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path d="M7 9V4h10v5" stroke="{color}" stroke-width="1.8" fill="none" stroke-linejoin="round"/><rect x="3.5" y="9" width="17" height="8" rx="2" stroke="{color}" stroke-width="1.8" fill="none"/><rect x="7" y="14" width="10" height="6" rx="1" stroke="{color}" stroke-width="1.8" fill="#FFFFFF"/></svg>'
    ),
    "keyboard": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<rect x="2.5" y="6" width="19" height="12" rx="2.5" stroke="{color}" '
        'stroke-width="2" fill="none"/>'
        '<path d="M6.5 10h1M10.5 10h1M14.5 10h1M8 14h8" stroke="{color}" '
        'stroke-width="2" stroke-linecap="round"/></svg>'
    ),
    "bell": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path d="M6 16.5V11a6 6 0 0 1 12 0v5.5l1.5 2h-15z" stroke="{color}" '
        'stroke-width="2" fill="none" stroke-linejoin="round"/>'
        '<path d="M10 20.5a2.2 2.2 0 0 0 4 0" stroke="{color}" stroke-width="2" '
        'fill="none" stroke-linecap="round"/></svg>'
    ),
    "network": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<rect x="9" y="3.5" width="6" height="5" rx="1.4" '
        'stroke="{color}" stroke-width="2" fill="none"/>'
        '<rect x="3" y="15.5" width="6" height="5" rx="1.4" '
        'stroke="{color}" stroke-width="2" fill="none"/>'
        '<rect x="15" y="15.5" width="6" height="5" rx="1.4" '
        'stroke="{color}" stroke-width="2" fill="none"/>'
        '<path d="M12 8.5V12M6 15.5V12h12v3.5" stroke="{color}" stroke-width="2" '
        'fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>'
    ),
    "archive": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<rect x="3" y="4.5" width="18" height="4.5" rx="1.4" '
        'stroke="{color}" stroke-width="2" fill="none"/>'
        '<path d="M5 9v8.5a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V9M10 13h4" '
        'stroke="{color}" stroke-width="2" fill="none" '
        'stroke-linecap="round" stroke-linejoin="round"/></svg>'
    ),
    "info": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<circle cx="12" cy="12" r="8.5" stroke="{color}" stroke-width="2" fill="none"/>'
        '<path d="M12 11v5.5" stroke="{color}" stroke-width="2" '
        'fill="none" stroke-linecap="round"/>'
        '<circle cx="12" cy="7.8" r="1.2" fill="{color}"/></svg>'
    ),
}


_ICON_CACHE: "OrderedDict[tuple, QIcon]" = OrderedDict()
_ICON_CACHE_MAX = 512


def svg_icon(name: str, size: int = 18, color: str = "#333333") -> QIcon:
    """SVG 아이콘. 125~200% 배율 화면에서 흐리지 않도록 1배와 2배 그림을
    함께 넣는다(Qt 가 화면 배율에 맞는 쪽을 골라 그린다).

    같은 (이름, 크기, 색)은 한 번만 그린다. 메모 창과 카드마다 같은 그림을 여러 번
    그리던 것이 켤 때와 목록을 그릴 때 시간과 메모리를 많이 썼다. QIcon 은 내용을
    나눠 쓰는 값이라 돌려준 것을 고쳐도 저장한 것은 바뀌지 않는다."""
    key = (name, int(size), str(color))
    cached = _ICON_CACHE.get(key)
    if cached is not None:
        _ICON_CACHE.move_to_end(key)
        return QIcon(cached)
    icon = _render_svg_icon(name, size, color)
    _ICON_CACHE[key] = icon
    if len(_ICON_CACHE) > _ICON_CACHE_MAX:
        _ICON_CACHE.popitem(last=False)
    return QIcon(icon)


def _render_svg_icon(name: str, size: int, color: str) -> QIcon:
    svg_str = _SVG[name].format(color=color)
    renderer = QSvgRenderer(QByteArray(svg_str.encode("utf-8")))
    icon = QIcon()
    for scale in (1, 2):
        pix = QPixmap(size * scale, size * scale)
        pix.fill(Qt.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.Antialiasing)
        renderer.render(painter)
        painter.end()
        pix.setDevicePixelRatio(scale)
        icon.addPixmap(pix)
    return icon


_FOLDER_LOCK = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
    '<path d="M8 11V8a4 4 0 0 1 8 0v3" stroke="{edge}" stroke-width="2" '
    'fill="none" stroke-linecap="round"/>'
    '<rect x="4.8" y="10.4" width="14.4" height="10.2" rx="2.8" fill="{color}" '
    'stroke="{edge}" stroke-width="1.3"/></svg>'
)


def folder_lock_icon(color: str, size: int = 18) -> QIcon:
    """나만 보는 폴더의 표시: 폴더 색으로 채운 자물쇠. 다른 폴더의 색 동그라미와
    같은 자리에 놓여, 폴더이면서 나만 본다는 것이 함께 읽힌다."""
    fill = QColor(color)
    if not fill.isValid():
        fill = QColor("#FFE066")
    svg_str = _FOLDER_LOCK.format(color=fill.name(), edge="#5B5B5B")
    renderer = QSvgRenderer(QByteArray(svg_str.encode("utf-8")))
    icon = QIcon()
    for scale in (1, 2):
        pix = QPixmap(size * scale, size * scale)
        pix.fill(Qt.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.Antialiasing)
        renderer.render(painter)
        painter.end()
        pix.setDevicePixelRatio(scale)
        icon.addPixmap(pix)
    return icon


_TEXT_COLOR = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
    '<path d="M11 4h2l5.4 13h-2.5l-1.3-3.3H9.4L8.1 17H5.6L11 4z'
    'M10.15 11.6h3.7L12 7l-1.85 4.6z" fill="{color}" fill-rule="evenodd"/>'
    '<rect x="4" y="18.6" width="16" height="3.6" rx="1.4" fill="{bar}" '
    'stroke="{edge}" stroke-width="0.6"/></svg>'
)


def text_color_icon(color: str, bar: str, size: int = 18) -> QIcon:
    """글자 색 단추의 그림. 글자는 메모의 글자색으로, 아래의 띠는 지금 글자에
    정해져 있는 색으로 그린다."""
    svg_str = _TEXT_COLOR.format(color=color, bar=bar, edge=color)
    renderer = QSvgRenderer(QByteArray(svg_str.encode("utf-8")))
    icon = QIcon()
    for scale in (1, 2):
        pix = QPixmap(size * scale, size * scale)
        pix.fill(Qt.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.Antialiasing)
        renderer.render(painter)
        painter.end()
        pix.setDevicePixelRatio(scale)
        icon.addPixmap(pix)
    return icon


def color_swatch_icon(color: str, size: int = 18) -> QIcon:
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor(color))
    try:
        r = int(color[1:3], 16)
        g = int(color[3:5], 16)
        b = int(color[5:7], 16)
        lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
    except (ValueError, IndexError):
        lum = 0.5
    if lum < 0.30:
        border = QColor(255, 255, 255, 110)
    else:
        border = QColor(0, 0, 0, 110)
    painter.setPen(QPen(border, 1))
    margin = 2
    painter.drawEllipse(margin, margin, size - 2 * margin, size - 2 * margin)
    painter.end()
    return QIcon(pix)
