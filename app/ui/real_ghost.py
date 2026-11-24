"""메모 아이콘을 잡고 끄는 동안, 함께 고른 진짜 바탕화면 아이콘이 따라오는 모습.

탐색기는 파일 아이콘을 끌 때 아이콘을 제자리에 둔 채 흐린 그림이 마우스를 따라오게
한다. 메모 아이콘을 잡고 끌 때는 탐색기가 끄는 것이 아니라서 그 그림이 없다. 같은
모습을 여기서 그린다. 진짜 아이콘은 놓을 때 한 번에 옮긴다(ui/icon_select.py).

마우스를 받지 않는 창 한 장이다. 그림과 이름은 탐색기에 물어 읽은 것이다
(app/desktop_select.py 의 ``faces``)."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QRectF, Qt
from PySide6.QtGui import QFontDatabase, QImage, QPainter
from PySide6.QtWidgets import QWidget

from . import dwm
from . import note_fold as nf

# 끌려가는 그림의 진하기(탐색기의 끄는 그림과 비슷하게).
GHOST_OPACITY = 0.55


class RealGhost(QWidget):
    def __init__(self) -> None:
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint
                         | Qt.WindowStaysOnTopHint | Qt.WindowTransparentForInput
                         | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.NoFocus)
        # 진짜 아이콘의 이름은 프로그램 글꼴이 아니라 Windows 의 글꼴로 적혀 있다.
        self._font = QFontDatabase.systemFont(QFontDatabase.GeneralFont)
        self._faces: list[tuple[QRect, QImage, str]] = []
        self._icon = 48.0
        self._origin = QPoint()
        self._fade = 1.0

    def faces(self) -> list[tuple[QRect, QImage, str]]:
        return list(self._faces)

    def begin(self, faces: list[tuple[QRect, QImage, str]], icon: float,
              delta: QPoint | None = None) -> None:
        """``faces`` 는 (아이콘이 놓인 칸, 그림, 이름), 칸은 화면 좌표다. ``icon`` 은
        그림의 한 변. ``delta`` 는 이미 끌려온 만큼."""
        faces = [(QRect(cell), image, name) for cell, image, name in faces
                 if not image.isNull() and not cell.isEmpty()]
        if not faces:
            self.end()
            return
        box = QRect(faces[0][0])
        for cell, _image, _name in faces[1:]:
            box = box.united(cell)
        self._faces = [(cell.translated(-box.topLeft()), image, name)
                       for cell, image, name in faces]
        self._icon = float(icon)
        self._origin = box.topLeft()
        self._fade = 1.0
        self.setGeometry(QRect(self._origin + (delta or QPoint()), box.size()))
        self.show()
        self.update()

    def follow(self, delta: QPoint) -> None:
        if self._faces:
            self.move(self._origin + delta)

    def set_fade(self, on: bool) -> None:
        fade = 0.45 if on else 1.0
        if fade != self._fade:
            self._fade = fade
            self.update()

    def end(self) -> None:
        self._faces = []
        self.hide()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        dwm.set_bare(int(self.winId()))

    def paintEvent(self, event) -> None:  # noqa: N802
        if not self._faces:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setOpacity(GHOST_OPACITY * self._fade)
        dpr = self.devicePixelRatioF()
        for cell, image, name in self._faces:
            box = QRectF(cell)
            p.drawImage(nf.icon_glyph_rect(box, self._icon, dpr), image)
            nf.paint_icon_label(p, box, name, self._font, self._icon)
        p.end()


__all__ = ["GHOST_OPACITY", "RealGhost"]
