"""메모 안 이미지를 크게 보는 자체 뷰어 창.

메모의 ``<img>`` 는 뷰포트 폭에 맞춰 줄여 그리므로, 원본 해상도로 다시
디코드해 별도 창에 띄운다. 처음 열릴 때는 이미지 전체가 보이도록 창에
맞춘다(작은 이미지는 창을 이미지 크기로 줄이고, 그림을 억지로 키우지 않는다).

화면에는 그림과 아래 막대만 둔다. 막대 왼쪽은 같은 메모의 다른 이미지로
넘기는 단추, 오른쪽은 축소, 배율, 확대와 '창에 맞춤', '실제 크기' 단추다.
쓰는 법을 글로 적어 두지 않는다. 단추가 하는 일은 그림과 말풍선으로 알리고,
휠 확대, 끌어서 이동, 두 번 눌러 맞춤과 실제 크기 오가기, 방향키로 넘기기는
그대로 된다. 애니메이션(GIF, APNG 등)은 그대로 재생한다.

창은 스티키 메모의 자식이 아니라 독립 최상위 창이다. 스티키는 Z-order
최하단에 고정되는 도구 창이라, 그 소유 창으로 만들면 Windows 가 둘을 한
묶음으로 다뤄 뷰어를 누르는 순간 메모가 다른 창 위로 튀어나온다.
``WA_QuitOnClose`` 를 꺼 두어, 메인 창이 트레이에 숨고 메모가 모두 닫힌
상태에서 이 창을 닫아도 '마지막 창 닫힘' 으로 앱이 종료되지 않게 한다."""
from __future__ import annotations

from PySide6.QtCore import (
    QBuffer,
    QIODevice,
    QRectF,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QCursor,
    QGuiApplication,
    QIcon,
    QMovie,
    QPainter,
    QPixmap,
    QTransform,
)
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import i18n
from .icons import svg_icon
from .rich_text import data_url_image, data_url_payload, is_animated_data_url


_BG = "#1C1C1C"
_BAR_BG = "#262626"
_FG = "#F2F2F2"
_FG_MUTED = "#A0A0A0"
# 지금은 누를 수 없는 단추의 그림 색(첫 장에서의 '이전', 그림이 없을 때의 배율 단추).
_FG_OFF = "#5E5E5E"

MIN_SCALE = 0.05
MAX_SCALE = 32.0
ZOOM_STEP = 1.15

_BAR_HEIGHT = 48
_CANVAS_PAD = 16
# 창에 맞출 때 그림 둘레에 남기는 여백. 그림이 창 테두리에 붙지 않게 한다.
_FIT_MARGIN = 14
_MIN_WINDOW = QSize(560, 440)
_SCREEN_FRACTION = 0.85


def _bar_button(icon_name: str, tip: str, handler, *,
                checkable: bool = False) -> QToolButton:
    b = QToolButton()
    icon = svg_icon(icon_name, color=_FG)
    off = svg_icon(icon_name, color=_FG_OFF)
    for ratio in (1.0, 2.0):
        icon.addPixmap(off.pixmap(QSize(18, 18), ratio), QIcon.Disabled)
    b.setIcon(icon)
    b.setIconSize(QSize(20, 20))
    b.setToolTip(tip)
    b.setAccessibleName(tip)
    b.setCursor(Qt.PointingHandCursor)
    b.setFocusPolicy(Qt.NoFocus)
    b.setAutoRaise(True)
    b.setCheckable(checkable)
    b.setFixedSize(34, 34)
    b.setStyleSheet(
        "QToolButton { border: none; border-radius: 8px; padding: 4px; "
        "background: transparent; }"
        "QToolButton:hover { background: rgba(255,255,255,0.12); }"
        "QToolButton:pressed { background: rgba(255,255,255,0.20); }"
        # 지금 보는 방식(창에 맞춤, 실제 크기)인 단추는 눌린 모양으로 둔다.
        "QToolButton:checked { background: rgba(255,255,255,0.16); }"
        "QToolButton:disabled { background: transparent; }"
    )
    b.clicked.connect(handler)
    return b


class _ImageCanvas(QGraphicsView):
    """이미지 한 장을 담는 캔버스. 휠 확대·축소와 손 모양 드래그 이동을 맡는다."""

    zoomChanged = Signal(float)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self._item = QGraphicsPixmapItem()
        self._item.setTransformationMode(Qt.SmoothTransformation)
        self._scene.addItem(self._item)
        self.setScene(self._scene)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameStyle(QFrame.NoFrame)
        self.setBackgroundBrush(QColor(_BG))
        self.setAlignment(Qt.AlignCenter)
        # 방향키는 창이 받아 이미지 전환에 쓴다. 뷰가 초점을 가지면
        # QAbstractScrollArea 가 스크롤로 먹어 버린다.
        self.setFocusPolicy(Qt.NoFocus)
        # 사용자가 휠·버튼으로 배율을 바꾸기 전까지는 창 크기를 따라 맞춘다.
        self._fit_mode = True

    def has_image(self) -> bool:
        return not self._item.pixmap().isNull()

    def image_size(self) -> QSize:
        return self._item.pixmap().size()

    def set_pixmap(self, pix: QPixmap) -> None:
        self._item.setPixmap(pix)
        self._scene.setSceneRect(QRectF(pix.rect()))
        self.fit()

    def update_frame(self, pix: QPixmap) -> None:
        """애니메이션 프레임 교체. 크기가 같으므로 배율·위치는 건드리지 않는다."""
        if pix.isNull():
            return
        self._item.setPixmap(pix)

    def zoom(self) -> float:
        return float(self.transform().m11())

    def is_fit(self) -> bool:
        """창 크기를 따라 맞추는 중인가."""
        return self._fit_mode

    def fit(self) -> None:
        """그림 전체가 보이게 맞춘다. 둘레에 여백을 두고, 작은 그림은 키우지 않는다
        (작은 화면 캡처를 창만큼 늘리면 흐려진다)."""
        self._fit_mode = True
        if not self.has_image():
            self.resetTransform()
            self.zoomChanged.emit(1.0)
            return
        img = self.image_size()
        view = self.viewport().size()
        scale = min((view.width() - 2 * _FIT_MARGIN) / max(1, img.width()),
                    (view.height() - 2 * _FIT_MARGIN) / max(1, img.height()),
                    1.0)
        scale = max(MIN_SCALE, scale)
        self.setTransform(QTransform.fromScale(scale, scale))
        self.centerOn(self._item)
        self.zoomChanged.emit(self.zoom())

    def actual_size(self) -> None:
        self._fit_mode = False
        self.resetTransform()
        if self.has_image():
            self.centerOn(self._item)
        self.zoomChanged.emit(self.zoom())

    def zoom_by(self, factor: float, *, under_mouse: bool = True) -> None:
        if not self.has_image():
            return
        cur = self.zoom()
        new = max(MIN_SCALE, min(MAX_SCALE, cur * factor))
        if abs(new - cur) < 1e-6:
            return
        self._fit_mode = False
        self.setTransformationAnchor(
            QGraphicsView.AnchorUnderMouse if under_mouse
            else QGraphicsView.AnchorViewCenter)
        self.scale(new / cur, new / cur)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.zoomChanged.emit(self.zoom())

    def wheelEvent(self, event) -> None:  # noqa: N802
        delta = event.angleDelta().y()
        if delta != 0:
            self.zoom_by(ZOOM_STEP if delta > 0 else 1.0 / ZOOM_STEP)
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        """두 번 누르면 '창에 맞춤'과 '실제 크기'를 오간다."""
        if event.button() == Qt.LeftButton and self.has_image():
            if self._fit_mode:
                self.actual_size()
            else:
                self.fit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._fit_mode:
            self.fit()


class ImageViewerWindow(QWidget):
    """한 메모의 이미지 목록을 넘겨 가며 보는 독립 창."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, Qt.Window)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setAttribute(Qt.WA_QuitOnClose, False)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setObjectName("ImageViewerWindow")
        self.setWindowTitle(i18n.t("imgview.window_title"))
        self.setMinimumSize(400, 300)
        self.setStyleSheet(
            f"QWidget#ImageViewerWindow {{ background: {_BG}; }}")

        self._urls: list[str] = []
        self._index = 0
        self._movie: QMovie | None = None
        self._movie_buf: QBuffer | None = None
        self._placed = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._canvas = _ImageCanvas(self)
        self._canvas.zoomChanged.connect(self._on_zoom_changed)
        root.addWidget(self._canvas, 1)

        self._error = QLabel(i18n.t("imgview.load_failed"), self._canvas)
        self._error.setAlignment(Qt.AlignCenter)
        self._error.setStyleSheet(
            f"color: {_FG_MUTED}; font-size: 14px; background: transparent;")
        self._error.hide()

        bar = QWidget(self)
        bar.setObjectName("ImageViewerBar")
        bar.setAttribute(Qt.WA_StyledBackground, True)
        bar.setFixedHeight(_BAR_HEIGHT)
        bar.setStyleSheet(
            f"QWidget#ImageViewerBar {{ background: {_BAR_BG}; "
            "border-top: 1px solid rgba(255,255,255,0.07); }")
        row = QHBoxLayout(bar)
        row.setContentsMargins(10, 0, 10, 0)
        row.setSpacing(2)

        # 왼쪽: 같은 메모의 다른 이미지로 넘기기(이미지가 한 장이면 감춘다).
        self._prev_btn = _bar_button(
            "chevron_left", i18n.t("imgview.tip_prev"), self.show_prev)
        self._counter = QLabel("", bar)
        self._counter.setAlignment(Qt.AlignCenter)
        self._counter.setMinimumWidth(52)
        self._counter.setStyleSheet(
            f"color: {_FG}; font-size: 13px; font-weight: 600; "
            "background: transparent;")
        self._next_btn = _bar_button(
            "chevron_right", i18n.t("imgview.tip_next"), self.show_next)
        row.addWidget(self._prev_btn)
        row.addWidget(self._counter)
        row.addWidget(self._next_btn)
        row.addStretch(1)

        # 오른쪽: 축소, 배율, 확대. 그 옆에 창에 맞춤과 실제 크기.
        self._zoom_out_btn = _bar_button(
            "minus", i18n.t("imgview.tip_zoom_out"),
            lambda: self._canvas.zoom_by(1.0 / ZOOM_STEP, under_mouse=False))
        self._zoom_lbl = QLabel("", bar)
        self._zoom_lbl.setAlignment(Qt.AlignCenter)
        self._zoom_lbl.setFixedWidth(50)
        self._zoom_lbl.setStyleSheet(
            f"color: {_FG}; font-size: 12px; font-weight: 600; "
            "background: transparent;")
        self._zoom_in_btn = _bar_button(
            "plus", i18n.t("imgview.tip_zoom_in"),
            lambda: self._canvas.zoom_by(ZOOM_STEP, under_mouse=False))
        row.addWidget(self._zoom_out_btn)
        row.addWidget(self._zoom_lbl)
        row.addWidget(self._zoom_in_btn)
        row.addSpacing(8)
        self._fit_btn = _bar_button(
            "fit_screen", i18n.t("imgview.tip_fit"), self._canvas.fit,
            checkable=True)
        self._actual_btn = _bar_button(
            "actual_size", i18n.t("imgview.tip_actual"),
            self._canvas.actual_size, checkable=True)
        row.addWidget(self._fit_btn)
        row.addWidget(self._actual_btn)
        root.addWidget(bar)

        self.setFocusPolicy(Qt.StrongFocus)

    # ── 공개 API ──

    def set_images(self, urls: list[str], index: int = 0) -> None:
        self._urls = [u for u in urls if u]
        if not self._urls:
            self._index = 0
        else:
            self._index = max(0, min(int(index), len(self._urls) - 1))
        self._show_current()

    def present(self, anchor_screen=None) -> None:
        """창을 띄운다. 처음이면 이미지 크기에 맞춰 화면 가운데 놓는다."""
        if not self._placed:
            self._place_on(anchor_screen)
            self._placed = True
        self.show()
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.OtherFocusReason)

    def current_index(self) -> int:
        return self._index

    def image_count(self) -> int:
        return len(self._urls)

    def canvas(self) -> _ImageCanvas:
        return self._canvas

    def show_prev(self) -> None:
        if self._index > 0:
            self._index -= 1
            self._show_current()

    def show_next(self) -> None:
        if self._index < len(self._urls) - 1:
            self._index += 1
            self._show_current()

    # ── 내부 ──

    def _place_on(self, anchor_screen) -> None:
        screen = (anchor_screen
                  or QGuiApplication.screenAt(QCursor.pos())
                  or QGuiApplication.primaryScreen())
        if screen is None:
            self.resize(_MIN_WINDOW)
            return
        avail = screen.availableGeometry()
        max_w = int(avail.width() * _SCREEN_FRACTION)
        max_h = int(avail.height() * _SCREEN_FRACTION)
        img = self._canvas.image_size()
        want_w = img.width() + 2 * _CANVAS_PAD
        want_h = img.height() + 2 * _CANVAS_PAD + _BAR_HEIGHT
        w = max(_MIN_WINDOW.width(), min(want_w, max_w))
        h = max(_MIN_WINDOW.height(), min(want_h, max_h))
        self.resize(w, h)
        x = avail.x() + (avail.width() - w) // 2
        y = avail.y() + max(0, (avail.height() - h) // 2 - 12)
        self.move(x, y)

    def _stop_movie(self) -> None:
        if self._movie is not None:
            try:
                self._movie.frameChanged.disconnect(self._on_movie_frame)
            except (RuntimeError, TypeError):
                pass
            self._movie.stop()
            self._movie = None
        if self._movie_buf is not None:
            self._movie_buf.close()
            self._movie_buf = None

    def _start_movie(self, data_url: str) -> None:
        ba = data_url_payload(data_url)
        if ba.isEmpty():
            return
        buf = QBuffer(self)
        buf.setData(ba)
        if not buf.open(QIODevice.ReadOnly):
            return
        movie = QMovie(self)
        movie.setDevice(buf)
        movie.setCacheMode(QMovie.CacheAll)
        if not movie.isValid():
            buf.close()
            return
        self._movie = movie
        self._movie_buf = buf
        movie.frameChanged.connect(self._on_movie_frame)
        movie.start()

    def _on_movie_frame(self, _frame: int) -> None:
        if self._movie is None:
            return
        self._canvas.update_frame(self._movie.currentPixmap())

    def _show_current(self) -> None:
        self._stop_movie()
        total = len(self._urls)
        if total == 0:
            self._canvas.set_pixmap(QPixmap())
            self._error.show()
            self._counter.setText("")
            self._refresh_nav()
            return
        url = self._urls[self._index]
        pix = QPixmap.fromImage(data_url_image(url))
        self._canvas.set_pixmap(pix)
        self._error.setVisible(pix.isNull())
        if not pix.isNull() and is_animated_data_url(url):
            self._start_movie(url)
        self._counter.setText(
            i18n.t("imgview.counter", index=self._index + 1, total=total))
        self._refresh_nav()

    def _refresh_nav(self) -> None:
        total = len(self._urls)
        multiple = total > 1
        self._prev_btn.setVisible(multiple)
        self._next_btn.setVisible(multiple)
        self._counter.setVisible(multiple)
        self._prev_btn.setEnabled(self._index > 0)
        self._next_btn.setEnabled(self._index < total - 1)
        self._refresh_zoom_controls()

    def _refresh_zoom_controls(self) -> None:
        """배율 글자와 단추를 지금 상태에 맞춘다. 그림이 없으면 모두 쉰다."""
        has = self._canvas.has_image()
        scale = self._canvas.zoom()
        self._zoom_lbl.setText(
            i18n.t("imgview.zoom", percent=int(round(scale * 100)))
            if has else "")
        self._zoom_out_btn.setEnabled(has and scale > MIN_SCALE * 1.0001)
        self._zoom_in_btn.setEnabled(has and scale < MAX_SCALE / 1.0001)
        fit = has and self._canvas.is_fit()
        self._fit_btn.setEnabled(has)
        self._actual_btn.setEnabled(has)
        self._fit_btn.setChecked(fit)
        self._actual_btn.setChecked(
            has and not fit and abs(scale - 1.0) < 0.005)

    def _on_zoom_changed(self, _scale: float) -> None:
        self._refresh_zoom_controls()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._error.setGeometry(self._canvas.rect())

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        if key == Qt.Key_Escape:
            self.close()
        elif key in (Qt.Key_Left, Qt.Key_PageUp):
            self.show_prev()
        elif key in (Qt.Key_Right, Qt.Key_PageDown, Qt.Key_Space):
            self.show_next()
        elif key == Qt.Key_0:
            self._canvas.fit()
        elif key == Qt.Key_1:
            self._canvas.actual_size()
        elif key in (Qt.Key_Plus, Qt.Key_Equal):
            self._canvas.zoom_by(ZOOM_STEP, under_mouse=False)
        elif key in (Qt.Key_Minus, Qt.Key_Underscore):
            self._canvas.zoom_by(1.0 / ZOOM_STEP, under_mouse=False)
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._stop_movie()
        super().closeEvent(event)


__all__ = ["ImageViewerWindow"]
