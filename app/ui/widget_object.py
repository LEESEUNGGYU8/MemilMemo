"""메모 본문 안의 위젯 자리(그림이 아닌 글자 하나 크기의 칸).

본문에서 위젯은 그림이 아니다. 글자 하나(U+FFFC)가 위젯 칸 형식을 달고 들어가고, 그
칸의 크기는 위젯이 정한다(본문을 고치지 않고 크기만 바뀐다). 진짜 위젯은 메모 창이
그 칸 위에 올린다. 그래서 그림 보기, 그림 메뉴, 그림 크기 맞춤 같은 그림의 일이 위젯에
닿지 않는다.

저장할 때만 위젯 칸을 지금 모습의 그림(위젯 id 와 이름을 품은 PNG)으로 바꿔 적는다.
위젯을 모르는 옛 버전은 그 그림을 보고, 이 버전은 불러올 때 다시 위젯 칸으로 바꾼다.
위젯 데이터를 아직 받지 못했으면 칸에 저장된 그림을 그대로 그린다."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, QRectF, QSizeF, QUrl
from PySide6.QtGui import (
    QImage,
    QPainter,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextFormat,
    QPyTextObject,
    QTextImageFormat,
)

# 위젯 칸의 형식 번호와 칸이 품는 값.
WIDGET_OBJECT = QTextFormat.UserObject + 7
P_ID = QTextFormat.UserProperty + 701       # 위젯 id
P_URL = QTextFormat.UserProperty + 702      # 저장된 그림(data URL). 데이터가 없을 때 그린다
P_W = QTextFormat.UserProperty + 703        # 저장된 그림의 폭
P_H = QTextFormat.UserProperty + 704        # 저장된 그림의 높이
CHAR = "￼"


def object_format(wid: str, url: str = "", width: int = 0, height: int = 0) -> QTextCharFormat:
    fmt = QTextCharFormat()
    fmt.setObjectType(WIDGET_OBJECT)
    fmt.setProperty(P_ID, wid)
    if url:
        fmt.setProperty(P_URL, url)
    if width > 0 and height > 0:
        fmt.setProperty(P_W, int(width))
        fmt.setProperty(P_H, int(height))
    return fmt


def is_widget_format(fmt) -> bool:
    return fmt is not None and fmt.objectType() == WIDGET_OBJECT


def widget_id(fmt) -> str:
    value = fmt.property(P_ID) if is_widget_format(fmt) else None
    return value if isinstance(value, str) else ""


def stored_size(fmt) -> tuple[int, int]:
    w, h = fmt.property(P_W), fmt.property(P_H)
    try:
        return max(0, int(w or 0)), max(0, int(h or 0))
    except (TypeError, ValueError):
        return 0, 0


def find_objects(doc: QTextDocument) -> list[tuple[str, int, QTextCharFormat]]:
    """본문의 위젯 칸: (위젯 id, 위치, 형식). 앞에서부터."""
    out = []
    block = doc.begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            if frag.isValid():
                fmt = frag.charFormat()
                if is_widget_format(fmt):
                    wid = widget_id(fmt)
                    # 한 조각에 같은 칸이 여럿 붙어 있을 수 있다(복사).
                    for i in range(frag.length()):
                        if wid:
                            out.append((wid, frag.position() + i, fmt))
            it += 1
        block = block.next()
    return out


def widget_images(doc: QTextDocument) -> list[tuple[str, int, QTextImageFormat]]:
    """위젯 그림(저장 형식이나 붙여 넣은 것): (위젯 id, 위치, 그림 형식)."""
    from ..widgets import id_from_data_url
    out = []
    block = doc.begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            if frag.isValid() and frag.charFormat().isImageFormat():
                fmt = frag.charFormat().toImageFormat()
                wid = id_from_data_url(fmt.name())
                if wid:
                    out.append((wid, frag.position(), fmt))
            it += 1
        block = block.next()
    return out


def images_to_objects(doc: QTextDocument, *, join: bool = False) -> int:
    """위젯 그림을 위젯 칸으로 바꾼다. 바꾼 수를 돌려준다. ``join`` 이면 앞의 편집과 한
    번에 되돌린다(붙여 넣은 직후)."""
    found = widget_images(doc)
    if not found:
        return 0
    cursor = QTextCursor(doc)
    if join:
        cursor.joinPreviousEditBlock()
    else:
        cursor.beginEditBlock()
    try:
        for wid, pos, fmt in reversed(found):
            w, h = int(fmt.width()), int(fmt.height())
            if w <= 0 or h <= 0:
                image = picture_of(doc, fmt.name())
                if image is not None:
                    w, h = image.width(), image.height()
            cursor.setPosition(pos)
            cursor.setPosition(pos + 1, QTextCursor.KeepAnchor)
            cursor.insertText(CHAR, object_format(wid, fmt.name(), w, h))
    finally:
        cursor.endEditBlock()
    return len(found)


def objects_to_images(doc: QTextDocument,
                      picture: Callable[[str, QTextCharFormat], tuple[str, int, int]]) -> None:
    """위젯 칸을 그림으로 바꾼다(저장할 복제본에서). ``picture(id, 형식)`` 은
    (data URL, 폭, 높이). URL 이 비면 그 칸은 뺀다."""
    found = find_objects(doc)
    if not found:
        return
    cursor = QTextCursor(doc)
    for wid, pos, fmt in reversed(found):
        url, w, h = picture(wid, fmt)
        cursor.setPosition(pos)
        cursor.setPosition(pos + 1, QTextCursor.KeepAnchor)
        if not url:
            cursor.removeSelectedText()
            continue
        image = QTextImageFormat()
        image.setName(url)
        if w > 0 and h > 0:
            image.setWidth(w)
            image.setHeight(h)
        cursor.insertImage(image)


class WidgetObjectHandler(QPyTextObject):
    """위젯 칸의 크기와 그리기. 크기는 ``size_of(id)`` 가 정하고(위젯이 없으면 저장된
    그림의 크기), 진짜 위젯이 올라가 있지 않은 칸만 저장된 그림을 그린다.

    PySide 에서는 QPyTextObject 를 물려받아야 Qt 가 그리기 틀로 알아본다(QObject 와
    QTextObjectInterface 를 함께 물려받으면 등록이 조용히 무시된다)."""

    def __init__(self, parent: QObject, size_of: Callable[[str], tuple[int, int] | None],
                 live: Callable[[str], bool]) -> None:
        super().__init__(parent)
        self._size_of = size_of
        self._live = live

    def intrinsicSize(self, doc, pos, fmt) -> QSizeF:  # noqa: N802
        wid = widget_id(fmt)
        size = self._size_of(wid) if wid else None
        if size:
            return QSizeF(size[0], size[1])
        w, h = stored_size(fmt)
        return QSizeF(w or 240, h or 48)

    def drawObject(self, painter: QPainter, rect: QRectF, doc, pos, fmt) -> None:  # noqa: N802
        wid = widget_id(fmt)
        if wid and self._live(wid):
            return
        url = fmt.property(P_URL)
        if isinstance(url, str) and url:
            image = picture_of(doc, url)
            if image is not None:
                painter.drawImage(rect, image)


def picture_of(doc: QTextDocument, url: str) -> QImage | None:
    """저장된 그림. 문서에 없으면 data URL 을 풀어 넣어 둔다."""
    image = doc.resource(QTextDocument.ImageResource, QUrl(url))
    if isinstance(image, QImage) and not image.isNull():
        return image
    head, _, body = url.partition(",")
    if not head.startswith("data:image/") or ";base64" not in head:
        return None
    import base64
    try:
        raw = base64.b64decode(body, validate=False)
    except (ValueError, TypeError):
        return None
    image = QImage.fromData(raw)
    if image.isNull():
        return None
    doc.addResource(QTextDocument.ImageResource, QUrl(url), image)
    return image


def install(doc: QTextDocument, handler: WidgetObjectHandler) -> None:
    doc.documentLayout().registerHandler(WIDGET_OBJECT, handler)


__all__ = ["CHAR", "WIDGET_OBJECT", "WidgetObjectHandler", "find_objects", "images_to_objects",
           "install", "is_widget_format", "object_format", "objects_to_images", "stored_size",
           "widget_id", "widget_images"]
