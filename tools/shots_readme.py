"""소개 페이지(README)에 쓰는 합성 이미지와 GIF.

``capture_screenshots.py`` 가 데모 데이터로 앱을 띄운 뒤 이 모듈의 ``shoot`` 를
부른다. 창은 전부 ``QWidget.grab()`` 으로 찍어 한 장에 배치한다. 실제 화면은
찍지 않는다(배경과 창 테두리도 여기서 그린다).

만드는 파일
    hero.png        바탕 화면 위의 메모들과 메인 창, 접힌 쪽지·아이콘, 가장자리 탭
    links.png       파일·폴더 링크가 든 메모와 링크 창
    icons.png       접힌 메모와 폴더의 모양 네 가지
    views.png       '최근 메모' 보기 방식 여섯 가지
    fold.gif        접기 → 가장자리에 붙이기 → 펼치기
    fold-steps.png  같은 과정의 정지 그림(GIF 를 못 보는 곳용)
    editing.png     서식·목록·색·이미지 뷰어
    together.png    편집 중 표시, 참가자, 수정 이력

배율 200% 화면으로 따로 찍는 파일(``QT_SCALE_FACTOR=2`` 를 주고 돌린다)
    desktop-icons.png  아이콘으로 접은 메모와 폴더가 Windows 의 아이콘 옆에 놓인 모습
"""
from __future__ import annotations

import io

from PySide6.QtCore import QBuffer, QPointF, QRect, QRectF, QSize, QSizeF, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QFontMetricsF,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QApplication

from app import config, i18n
from app.ui import note_fold as nf

INK = "#1F2937"
SUB = "#4B5563"
BACKDROP_TOP = "#EEF1F5"
BACKDROP_BOTTOM = "#DCE2EA"
# 바탕 화면 장면에 쓰는 배경(실제 화면을 찍지 않고 그린다).
DESKTOP_A = "#243B55"
DESKTOP_B = "#3F5F7F"

LABELS = {
    "ko": {
        "list": "리스트", "grid": "그리드",
        "compact": "간략히", "standard": "표준", "detailed": "자세히",
        "step_open": "펼친 메모", "step_fold": "쪽지로 접기",
        "step_slip": "접힌 쪽지", "step_tab": "가장자리 탭",
        "step_peek": "마우스를 올리면 제목",
        "viewer": "이미지 보기", "history": "수정 이력", "sessions": "참가자",
        "settings": "설정", "link_dialog": "링크 고치기",
        "face_slip": "쪽지", "face_icon": "아이콘",
        "face_folder_closed": "접어 둔 폴더", "face_folder_open": "열어 둔 폴더",
        "face_tab": "가장자리 탭",
    },
    "en": {
        "list": "List", "grid": "Grid",
        "compact": "Compact", "standard": "Standard", "detailed": "Detailed",
        "step_open": "Open note", "step_fold": "Folding",
        "step_slip": "Folded slip", "step_tab": "Edge tab",
        "step_peek": "Hover for the title",
        "viewer": "Image viewer", "history": "History", "sessions": "People",
        "settings": "Settings", "link_dialog": "Edit link",
        "face_slip": "Slip", "face_icon": "Icon",
        "face_folder_closed": "Folded folder", "face_folder_open": "Open folder",
        "face_tab": "Edge tab",
    },
    "ja": {
        "list": "リスト", "grid": "グリッド",
        "compact": "簡略", "standard": "標準", "detailed": "詳細",
        "step_open": "開いたメモ", "step_fold": "折りたたみ",
        "step_slip": "折りたたんだ付箋", "step_tab": "端のタブ",
        "step_peek": "マウスを乗せるとタイトル",
        "viewer": "画像ビューアー", "history": "編集履歴", "sessions": "参加者",
        "settings": "設定", "link_dialog": "リンクを編集",
        "face_slip": "付箋", "face_icon": "アイコン",
        "face_folder_closed": "折りたたんだフォルダー",
        "face_folder_open": "開いたフォルダー", "face_tab": "端のタブ",
    },
}


def _t(key: str) -> str:
    lang = i18n.current_language() if hasattr(i18n, "current_language") else "ko"
    return LABELS.get(lang, LABELS["ko"]).get(key, key)


def _font(px: int, bold: bool = False) -> QFont:
    f = QFont(QApplication.font())
    f.setPixelSize(px)
    f.setBold(bold)
    return f


# ── 그리기 도우미 ───────────────────────────────────────────────────────

class Canvas:
    """배경을 깔고 그 위에 창 그림을 그림자와 함께 올린다."""

    def __init__(self, width: int, height: int, *, desktop: bool = False,
                 scale: float = 1.0):
        # ``scale`` 이 2 면 같은 장면을 두 배 촘촘하게 그린다(자리는 그대로 쓴다).
        self.img = QImage(int(round(width * scale)), int(round(height * scale)),
                          QImage.Format_ARGB32_Premultiplied)
        self.img.setDevicePixelRatio(scale)
        self.w, self.h = width, height
        self.desktop = desktop
        self.p = QPainter(self.img)
        self.p.setRenderHint(QPainter.Antialiasing)
        self.p.setRenderHint(QPainter.SmoothPixmapTransform)
        if desktop:
            grad = QLinearGradient(0, 0, width, height)
            grad.setColorAt(0.0, QColor(DESKTOP_A))
            grad.setColorAt(1.0, QColor(DESKTOP_B))
        else:
            grad = QLinearGradient(0, 0, 0, height)
            grad.setColorAt(0.0, QColor(BACKDROP_TOP))
            grad.setColorAt(1.0, QColor(BACKDROP_BOTTOM))
        self.p.fillRect(0, 0, width, height, grad)

    def place(self, pix: QPixmap, x: float, y: float, *, radius: float = 12,
              shadow: bool = True) -> QRectF:
        w = pix.width() / pix.devicePixelRatio()
        h = pix.height() / pix.devicePixelRatio()
        if shadow:
            self.p.setPen(Qt.NoPen)
            for i in range(1, 15):
                self.p.setBrush(QColor(0, 0, 0, 9) if self.desktop
                                else QColor(17, 24, 39, 5))
                grow = i * 0.95
                self.p.drawRoundedRect(
                    QRectF(x - grow, y - grow + 7, w + 2 * grow, h + 2 * grow),
                    radius + grow, radius + grow)
        self.p.drawPixmap(QPointF(x, y), pix)
        return QRectF(x, y, w, h)

    def text(self, text: str, rect: QRectF, *, px: int = 15,
             bold: bool = True, color: str = INK,
             align=Qt.AlignLeft | Qt.AlignVCenter) -> None:
        self.p.setFont(_font(px, bold))
        self.p.setPen(QColor(color))
        self.p.drawText(rect, align, text)

    def edge(self, x: float, top: float, bottom: float) -> None:
        """화면 가장자리를 나타내는 가는 세로선."""
        self.p.setPen(QPen(QColor(17, 24, 39, 70), 2))
        self.p.drawLine(QPointF(x, top), QPointF(x, bottom))

    def finish(self) -> QImage:
        self.p.end()
        return self.img


def framed(pix: QPixmap, title: str) -> QPixmap:
    """찍은 창에 제목 줄을 붙여 창처럼 보이게 한다(grab 에는 OS 창틀이 없다)."""
    bar = 34
    w, h = pix.width(), pix.height()
    out = QPixmap(w, h + bar)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    shape = QPainterPath()
    shape.addRoundedRect(QRectF(0, 0, w, h + bar), 9, 9)
    p.setClipPath(shape)
    p.fillRect(0, 0, w, bar, QColor("#F3F4F6"))
    icon = QPixmap(str(config.ICON_PATH))
    if not icon.isNull():
        p.drawPixmap(QRectF(11, 9, 16, 16), icon, QRectF(icon.rect()))
    p.setFont(_font(12))
    p.setPen(QColor(INK))
    p.drawText(QRectF(35, 0, w - 150, bar), Qt.AlignVCenter | Qt.AlignLeft,
               title)
    p.setPen(QPen(QColor("#374151"), 1.2))
    cx = w - 116
    p.drawLine(QPointF(cx, 17.5), QPointF(cx + 10, 17.5))
    p.drawRect(QRectF(cx + 41, 12.5, 9.5, 9.5))
    p.drawLine(QPointF(cx + 83, 12.5), QPointF(cx + 93, 22.5))
    p.drawLine(QPointF(cx + 93, 12.5), QPointF(cx + 83, 22.5))
    p.drawPixmap(0, bar, pix)
    p.setClipping(False)
    p.setPen(QPen(QColor(17, 24, 39, 46), 1))
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h + bar - 1), 9, 9)
    p.end()
    return out


def rounded(pix: QPixmap, radius: float = 10) -> QPixmap:
    """메뉴처럼 네모나게 찍히는 그림의 모서리를 다듬는다."""
    out = QPixmap(pix.size())
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    shape = QPainterPath()
    shape.addRoundedRect(QRectF(0, 0, pix.width(), pix.height()),
                         radius, radius)
    p.setClipPath(shape)
    p.drawPixmap(0, 0, pix)
    p.setClipping(False)
    p.setPen(QPen(QColor(17, 24, 39, 40), 1))
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(QRectF(0.5, 0.5, pix.width() - 1, pix.height() - 1),
                      radius, radius)
    p.end()
    return out


def grab(widget, harness) -> QPixmap:
    harness.settle()
    return widget.grab()


def screen_scale() -> float:
    """찍는 화면의 배율. ``QT_SCALE_FACTOR=2`` 를 주고 돌리면 2 다."""
    screen = QApplication.primaryScreen()
    return float(screen.devicePixelRatio()) if screen is not None else 1.0


def grab_popup(trigger, harness) -> QPixmap | None:
    """``exec`` 로 뜨는 메뉴를 찍어 돌려준다."""
    got: list[QPixmap] = []

    def _take():
        pop = QApplication.activePopupWidget()
        if pop is not None:
            got.append(pop.grab())
            pop.close()

    QTimer.singleShot(250, _take)
    trigger()
    harness.settle(60)
    return got[0] if got else None


# ── 장면 ────────────────────────────────────────────────────────────────

def _hero(window, harness) -> None:
    st = window._stickies
    window._set_view_mode(density="standard", layout="list")
    window.resize(380, 640)

    reply, lunch, deploy = st["n_reply"], st["n_lunch"], st["n_deploy"]
    homes = {w: w.geometry() for w in (reply, lunch, deploy)}
    reply.fold(style=nf.FOLD_SLIP)
    lunch.fold(style=nf.FOLD_ICON)
    deploy.dock(nf.DOCK_LEFT)
    deploy.setGeometry(deploy._tab_rect(True))
    # 폴더 아이콘: 하나는 열어 둔 폴더(그 메모가 보인다), 하나는 접어 둔 폴더.
    window._place_folder_icon("f_store")
    window._set_folder_opened("f_store", True, animate=False)
    window._place_folder_icon("f_me")
    harness.settle(150)
    # 방금 한 일의 임시 안내가 상태줄에 남지 않게 한다.
    window._status.clearMessage()
    window._refresh_status_text()

    c = Canvas(1600, 790, desktop=True)
    c.place(grab(st["n_meeting"], harness), 110, 64)
    c.place(grab(st["n_todo"], harness), 440, 64)
    c.place(grab(st["n_plan"], harness), 804, 64)
    c.place(grab(st["n_info"], harness), 110, 380)
    c.place(grab(reply, harness), 440, 372, radius=10)
    # 아이콘은 그림자 없이 올린다(창이 아니라 바탕 화면에 놓인 것처럼).
    c.place(grab(lunch, harness), 446, 440, shadow=False)
    c.place(grab(window._folder_icons["f_store"], harness), 546, 440,
            shadow=False)
    c.place(grab(window._folder_icons["f_me"], harness), 646, 440,
            shadow=False)
    # 가장자리 탭: 마우스를 올려 제목이 보이는 상태.
    c.place(grab(deploy, harness), 0, 664, radius=10)
    main = framed(grab(window, harness), i18n.t("app.name"))
    c.place(main, 1186, 58, radius=9)
    harness.save(c.finish(), "hero")

    window._remove_folder_icon("f_store")
    window._remove_folder_icon("f_me")
    deploy.undock()
    for w in (reply, lunch, deploy):
        w.unfold()
        w.set_fold_style(nf.FOLD_SLIP)
        w.setGeometry(homes[w])
        w.positionChanged.emit(w.note_id, homes[w].x(), homes[w].y())
    harness.settle(100)


# ── Windows 의 아이콘 ───────────────────────────────────────────────────
# 바탕 화면 장면에 함께 놓는다. 이 PC 의 Windows 가 쓰는 그림과 이름을 묻기만
# 하고(셸의 그림 목록), 사용자의 바탕화면은 읽지도 찍지도 않는다.

_SHELL_ITEMS = (
    # (셸에서 부르는 이름, 정해 둔 그림 번호)
    ("::{20D04FE0-3AEA-1069-A2D8-08002B30309D}", None),   # 내 PC
    # 휴지통은 찬 모습과 빈 모습이 다르다. 늘 빈 모습(SIID_RECYCLER)을 쓴다.
    ("::{645FF040-5081-101B-9F08-00AA002F954E}", 31),
    ("::{F02C1A0D-BE21-4350-88B0-7367FC96EF3C}", None),   # 네트워크
)


# 이 PC 의 Windows 는 제 언어의 이름을 준다(내 PC, 휴지통, 네트워크). 다른 언어의
# 그림을 찍을 때는 그 언어의 Windows 가 쓰는 이름과 글꼴로 바꿔 적는다.
_SHELL_NAMES = {
    "en": ("This PC", "Recycle Bin", "Network"),
    "ja": ("PC", "ごみ箱", "ネットワーク"),
}
_SHELL_FONTS = {"en": "Segoe UI", "ja": "Yu Gothic UI"}
# 화면 없이 그릴 때는 글꼴을 파일로 등록해야 찾아진다(Windows 의 Fonts 폴더).
_SHELL_FONT_FILES = {"ja": ("YuGothR.ttc", "YuGothM.ttc")}


def shell_items() -> list[tuple[QImage, str]]:
    """Windows 의 아이콘 그림(256픽셀)과 그 이름. 읽지 못하면 빈 목록."""
    try:
        import ctypes
        from ctypes import wintypes
        shell32, ole32 = ctypes.windll.shell32, ctypes.windll.ole32
        comctl32, user32 = ctypes.windll.comctl32, ctypes.windll.user32
    except (ImportError, AttributeError, OSError):
        return []

    class FileInfo(ctypes.Structure):
        _fields_ = [("hIcon", wintypes.HANDLE), ("iIcon", ctypes.c_int),
                    ("dwAttributes", wintypes.DWORD),
                    ("szDisplayName", wintypes.WCHAR * 260),
                    ("szTypeName", wintypes.WCHAR * 80)]

    class StockInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("hIcon", wintypes.HANDLE),
                    ("iSysImageIndex", ctypes.c_int), ("iIcon", ctypes.c_int),
                    ("szPath", wintypes.WCHAR * 260)]

    class Guid(ctypes.Structure):
        _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD),
                    ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]

    shell32.SHParseDisplayName.argtypes = [
        wintypes.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
        wintypes.ULONG, ctypes.c_void_p]
    shell32.SHParseDisplayName.restype = ctypes.c_long
    shell32.SHGetFileInfoW.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(FileInfo),
        wintypes.UINT, wintypes.UINT]
    shell32.SHGetFileInfoW.restype = ctypes.c_void_p
    shell32.SHGetStockIconInfo.argtypes = [
        ctypes.c_int, wintypes.UINT, ctypes.POINTER(StockInfo)]
    shell32.SHGetStockIconInfo.restype = ctypes.c_long
    shell32.SHGetImageList.argtypes = [
        ctypes.c_int, ctypes.POINTER(Guid), ctypes.POINTER(ctypes.c_void_p)]
    shell32.SHGetImageList.restype = ctypes.c_long
    comctl32.ImageList_GetIcon.argtypes = [
        ctypes.c_void_p, ctypes.c_int, wintypes.UINT]
    comctl32.ImageList_GetIcon.restype = ctypes.c_void_p
    user32.DestroyIcon.argtypes = [ctypes.c_void_p]
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]

    ole32.CoInitialize(None)
    images = ctypes.c_void_p()
    iid = Guid(0x46EB5926, 0x582E, 0x4017, (ctypes.c_ubyte * 8)(
        0x9F, 0xDF, 0xE8, 0x99, 0x8D, 0xAA, 0x09, 0x50))      # IImageList
    if shell32.SHGetImageList(4, ctypes.byref(iid),            # SHIL_JUMBO
                              ctypes.byref(images)) != 0 or not images:
        return []
    out: list[tuple[QImage, str]] = []
    for parsing_name, stock in _SHELL_ITEMS:
        pidl = ctypes.c_void_p()
        if shell32.SHParseDisplayName(parsing_name, None, ctypes.byref(pidl),
                                      0, None) != 0 or not pidl:
            continue
        info = FileInfo()
        # SHGFI_PIDL | SHGFI_SYSICONINDEX | SHGFI_DISPLAYNAME
        found = shell32.SHGetFileInfoW(pidl, 0, ctypes.byref(info),
                                       ctypes.sizeof(info), 0x8 | 0x4000 | 0x200)
        ole32.CoTaskMemFree(pidl)
        if not found:
            continue
        index = info.iIcon
        if stock is not None:
            fixed = StockInfo()
            fixed.cbSize = ctypes.sizeof(fixed)
            if shell32.SHGetStockIconInfo(stock, 0x4000,       # SHGSI_SYSICONINDEX
                                          ctypes.byref(fixed)) == 0:
                index = fixed.iSysImageIndex
        handle = comctl32.ImageList_GetIcon(images, index, 0x1)   # ILD_TRANSPARENT
        if not handle:
            continue
        image = QImage.fromHICON(handle)
        user32.DestroyIcon(handle)
        if not image.isNull():
            out.append((image, info.szDisplayName))
    return out


def shell_label_font(lang: str = "ko") -> QFont:
    """Windows 가 바탕화면 아이콘의 이름에 쓰는 글꼴. ``lang`` 이 이 PC 의 언어가
    아니면 그 언어의 Windows 가 쓰는 글꼴을 먼저 찾는다."""
    family, px = "", 12
    try:
        import ctypes
        from ctypes import wintypes

        class LogFont(ctypes.Structure):
            _fields_ = ([(n, wintypes.LONG) for n in (
                "lfHeight", "lfWidth", "lfEscapement", "lfOrientation",
                "lfWeight")] + [(n, ctypes.c_ubyte) for n in (
                    "lfItalic", "lfUnderline", "lfStrikeOut", "lfCharSet",
                    "lfOutPrecision", "lfClipPrecision", "lfQuality",
                    "lfPitchAndFamily")] + [("lfFaceName", wintypes.WCHAR * 32)])

        lf = LogFont()
        # SPI_GETICONTITLELOGFONT
        if ctypes.windll.user32.SystemParametersInfoW(
                0x001F, ctypes.sizeof(lf), ctypes.byref(lf), 0):
            family, px = lf.lfFaceName, abs(lf.lfHeight) or 12
    except (ImportError, AttributeError, OSError):
        pass
    # 화면 없이 그릴 때는 등록해 둔 글꼴 이름(영문)으로만 찾아진다.
    wanted = _SHELL_FONTS.get(lang, "")
    if wanted and wanted not in QFontDatabase.families():
        import os
        from pathlib import Path
        fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        for file in _SHELL_FONT_FILES.get(lang, ()):
            if (fonts / file).exists():
                QFontDatabase.addApplicationFont(str(fonts / file))
    known = QFontDatabase.families()
    for name in (wanted, family, "Malgun Gothic", "Segoe UI"):
        if name and name in known:
            family = name
            break
    f = QFont(family or QApplication.font().family())
    f.setPixelSize(px)
    return f


def paint_shell_icon(p: QPainter, cell: QRectF, image: QImage, name: str,
                     font: QFont, scale: float) -> None:
    """Windows 의 아이콘 하나를 진짜 바탕화면과 같은 자리에 그린다(그림은 칸의
    가운데 위쪽, 이름은 그 아래에 흰 글자와 번진 그림자)."""
    p.save()
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    p.drawImage(nf.icon_glyph_rect(cell, nf.ICON_DEFAULT, scale), image)
    fm = QFontMetricsF(font)
    area = nf.icon_label_rect(cell, nf.ICON_DEFAULT, font, dpr=scale)
    p.setFont(font)
    for i, line in enumerate(nf._wrap_label(name, font, area.width())):
        rect = QRectF(area.left(), area.top() + i * fm.lineSpacing(),
                      area.width(), fm.lineSpacing())
        flags = Qt.AlignHCenter | Qt.AlignVCenter
        p.setPen(QColor(0, 0, 0, 30))
        for dx in (-1.0, 0.0, 1.0, 2.0):
            for dy in (-1.0, 0.0, 1.0, 2.0):
                p.drawText(rect.translated(dx, dy), flags, line)
        p.setPen(QColor("#FFFFFF"))
        p.drawText(rect, flags, line)
    p.restore()


def _desktop(window, harness) -> None:
    """아이콘으로 접은 메모와 폴더가 Windows 의 아이콘과 한 격자에 놓인 모습.

    칸의 크기와 간격, 그림과 이름의 자리는 앱이 진짜 바탕화면에 맞춰 쓰는 값
    그대로다(``note_fold`` 의 기본 격자: 아이콘 48, 칸 76x100)."""
    shell = shell_items()
    if not shell:
        print("Windows 의 아이콘을 읽지 못해 desktop-icons.png 는 건너뜁니다.")
        return
    lang = getattr(getattr(harness, "ARGS", None), "lang", "ko")
    names = _SHELL_NAMES.get(lang)
    if names and len(names) == len(shell):
        shell = [(image, name) for (image, _own), name in zip(shell, names)]
    st = window._stickies
    scale = screen_scale()
    width, height = 664, 328
    grid = nf.default_icon_grid(QRect(0, 0, width, height))

    icons = (st["n_lunch"], st["n_info"], st["n_deploy"])
    homes = {w: w.geometry() for w in icons}
    for w in icons:
        w.fold(style=nf.FOLD_ICON)
    # 열어 둔 폴더 하나(그 메모가 보인다)와 접어 둔 폴더 둘.
    folders = ("f_work", "f_store", "f_me")
    for fid in folders:
        window._place_folder_icon(fid)
    window._set_folder_opened("f_work", True, animate=False)
    harness.settle(150)

    def cell(col: int, row: int) -> QRectF:
        return QRectF(QPointF(grid.pos_of(col, row)), QSizeF(grid.cell))

    c = Canvas(width, height, desktop=True, scale=scale)
    label_font = shell_label_font(lang)
    for row, (image, name) in enumerate(shell):
        paint_shell_icon(c.p, cell(0, row), image, name, label_font, scale)
    for row, w in enumerate(icons):
        at = cell(1, row)
        c.place(grab(w, harness), at.x(), at.y(), shadow=False)
    for row, fid in enumerate(folders):
        at = cell(2, row)
        c.place(grab(window._folder_icons[fid], harness), at.x(), at.y(),
                shadow=False)
    c.place(grab(st["n_reply"], harness), 292, 24)
    harness.save(c.finish(), "desktop-icons")

    for fid in folders:
        window._remove_folder_icon(fid)
    for w in icons:
        w.unfold()
        w.set_fold_style(nf.FOLD_SLIP)
        w.setGeometry(homes[w])
        w.positionChanged.emit(w.note_id, homes[w].x(), homes[w].y())
    harness.settle(100)


def _links(window, harness) -> None:
    """파일·폴더 링크가 든 메모, 넣기 메뉴, 링크 창."""
    from app import file_links as fl
    from app.ui.link_dialog import LinkDialog

    info = window._stickies["n_info"]
    info.resize(300, 300)
    harness.settle()
    info._request_edit()
    harness.settle()
    menu = grab_popup(info._show_attach_menu, harness)
    info._commit_and_release()
    note_pix = grab(info, harness)
    sample_path, sample_text, _is_dir = harness.TEXT["info"]["links"][1]
    href = fl.path_to_href(sample_path)
    dlg = LinkDialog(text=sample_text, href=href, editing=True)
    dlg.show()
    harness.settle(150)
    dialog = framed(grab(dlg, harness), _t("link_dialog"))
    dlg.close()

    c = Canvas(1120, 470)
    c.place(note_pix, 60, 56)
    if menu is not None:
        c.place(rounded(menu, 8), 250, 350, radius=8)
    c.place(dialog, 560, 44, radius=9)
    harness.save(c.finish(), "links")


def _icons(window, harness) -> None:
    """접힌 메모와 폴더가 바탕 화면에 놓이는 모양을 크게 보여 준다."""
    font = QApplication.font()
    scale = 2.0
    cell_w, cell_h = nf.ICON_CELL_DEFAULT.width(), nf.ICON_CELL_DEFAULT.height()
    items = (
        ("face_slip", "slip"), ("face_icon", "icon"),
        ("face_folder_closed", "closed"), ("face_folder_open", "open"),
        ("face_tab", "tab"),
    )
    pad, gap = 44, 30
    col_w = 200
    c = Canvas(pad * 2 + col_w * len(items) + gap * (len(items) - 1), 330,
               desktop=True)
    for i, (key, kind) in enumerate(items):
        x = pad + i * (col_w + gap)
        c.text(_t(key), QRectF(x, 30, col_w, 26), px=16, color="#FFFFFF",
               align=Qt.AlignHCenter | Qt.AlignVCenter)
        c.p.save()
        c.p.translate(x + col_w / 2.0, 86)
        c.p.scale(scale, scale)
        if kind == "slip":
            w = 96.0
            nf.paint_slip(c.p, QRectF(-w / 2, 28, w, nf.SLIP_SIZE.height()),
                          "#FFE066", harness.TEXT["faces"][0], font=font)
        elif kind == "tab":
            w = 74.0
            nf.paint_slip(c.p, QRectF(-w / 2, 28, w, nf.TAB_SIZE.height()),
                          "#FFB3BA", harness.TEXT["faces"][1], dock=nf.DOCK_LEFT, hover=True,
                          font=font)
        elif kind == "icon":
            nf.paint_icon(c.p, QRectF(-cell_w / 2, 0, cell_w, cell_h),
                          "#FFD9A6", harness.TEXT["faces"][2], font=font)
        else:
            nf.paint_folder_icon(
                c.p, QRectF(-cell_w / 2, 0, cell_w, cell_h), "#BAE1FF",
                harness.TEXT["folders"][0], opened=(kind == "open"), count=4,
                font=font)
        c.p.restore()
    harness.save(c.finish(), "icons")


def _views(window, harness) -> None:
    window.resize(380, 560)
    pad, gap, head = 34, 30, 34
    cols = ("compact", "standard", "detailed")
    rows = ("list", "grid")
    width = pad * 2 + 380 * 3 + gap * 2
    height = pad + (head + 560) * 2 + gap + pad
    c = Canvas(width, height)
    for r, layout in enumerate(rows):
        for i, density in enumerate(cols):
            window._set_view_mode(density=density, layout=layout)
            x = pad + i * (380 + gap)
            y = pad + r * (head + 560 + gap)
            c.text(f"{_t(layout)} · {_t(density)}",
                   QRectF(x + 2, y, 380, head - 8), px=16)
            c.place(rounded(grab(window, harness), 10), x, y + head, radius=10)
    harness.save(c.finish(), "views")
    window._set_view_mode(density="standard", layout="list")
    window.resize(380, 640)


def _fold_frames(window, harness) -> tuple[list[QImage], list[int]]:
    """접기 → 가장자리로 → 제목 보기 → 펼치기를 프레임으로 만든다."""
    note = window._stickies["n_todo"]
    pix = grab(note, harness)
    title = note._title()
    color = note.color
    font = note.font()
    nw, nh = pix.width(), pix.height()
    W, H = 560, 380
    home = QPointF(150, 54)       # 떠 있는 메모의 자리
    edge = QPointF(0, 54)         # 가장자리에 붙었을 때의 자리
    tab_w = float(nf.TAB_SIZE.width())
    peek_w = float(nf.tab_width_for(title, font))
    slip_w, slip_h = float(nf.SLIP_SIZE.width()), float(nf.SLIP_SIZE.height())

    floating = nf.FoldFrames(pix, nf.SLIP_SIZE, color=color, title=title,
                             anchor=nf.DOCK_LEFT, dock=nf.DOCK_NONE, font=font)
    docked = nf.FoldFrames(pix, QSize(int(peek_w), int(slip_h)), color=color,
                           title=title, anchor=nf.DOCK_LEFT,
                           dock=nf.DOCK_LEFT, font=font)

    frames: list[QImage] = []
    delays: list[int] = []

    def blank() -> tuple[QImage, QPainter]:
        img = QImage(W, H, QImage.Format_ARGB32_Premultiplied)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        grad = QLinearGradient(0, 0, 0, H)
        grad.setColorAt(0.0, QColor(BACKDROP_TOP))
        grad.setColorAt(1.0, QColor(BACKDROP_BOTTOM))
        p.fillRect(0, 0, W, H, grad)
        return img, p

    def add(draw, ms: int) -> None:
        img, p = blank()
        draw(p)
        p.end()
        frames.append(img)
        delays.append(ms)

    def fold_at(origin: QPointF, fr: nf.FoldFrames, t: float):
        def draw(p: QPainter) -> None:
            p.translate(origin)
            fr.paint(p, t)
        return draw

    def slip_at(x: float, y: float, w: float, dock: str, hover: bool = False):
        def draw(p: QPainter) -> None:
            nf.paint_slip(p, QRectF(x, y, w, slip_h), color, title, dock=dock,
                          hover=hover, font=font)
        return draw

    step = 40
    add(fold_at(home, floating, 0.0), 900)
    n = max(2, nf.FOLD_MS // step)
    for i in range(1, n):
        add(fold_at(home, floating, i / n), step)
    add(slip_at(home.x(), home.y(), slip_w, nf.DOCK_NONE), 650)
    # 끌어서 가장자리로: 가까워지면 탭 모양이 되어 붙는다.
    for i in range(1, 8):
        u = i / 7
        ease = 1 - (1 - u) ** 3
        x = home.x() * (1 - ease)
        near = x <= nf.SNAP_DISTANCE
        add(slip_at(0 if near else x, home.y(), slip_w,
                    nf.DOCK_LEFT if near else nf.DOCK_NONE, hover=True), step)
    for i in range(1, 5):
        w = slip_w + (tab_w - slip_w) * (i / 4)
        add(slip_at(0, edge.y(), w, nf.DOCK_LEFT), step)
    add(slip_at(0, edge.y(), tab_w, nf.DOCK_LEFT), 800)
    for i in range(1, 5):
        w = tab_w + (peek_w - tab_w) * (i / 4)
        add(slip_at(0, edge.y(), w, nf.DOCK_LEFT, hover=True), step)
    add(slip_at(0, edge.y(), peek_w, nf.DOCK_LEFT, hover=True), 600)
    n = max(2, nf.UNFOLD_MS // step)
    for i in range(1, n):
        add(fold_at(edge, docked, 1 - i / n), step)
    add(fold_at(edge, docked, 0.0), 1300)
    assert nw <= W and nh + home.y() <= H
    return frames, delays


def _to_pil(img: QImage):
    from PIL import Image
    buf = QBuffer()
    buf.open(QBuffer.ReadWrite)
    img.save(buf, "PNG")
    return Image.open(io.BytesIO(bytes(buf.data()))).convert("RGB")


def _fold(window, harness) -> None:
    frames, delays = _fold_frames(window, harness)
    try:
        pil = [_to_pil(f) for f in frames]
        # 프레임마다 팔레트를 따로 잡으면 색이 일렁인다. 한 팔레트로 통일한다.
        base = pil[0].quantize(colors=255, dither=0)
        quant = [f.quantize(palette=base, dither=0) for f in pil]
        path = harness.OUT / "fold.gif"
        quant[0].save(str(path), save_all=True, append_images=quant[1:],
                      duration=delays, loop=0, optimize=False, disposal=1)
        harness.SAVED.append(path.name)
    except ImportError:
        print("Pillow 가 없어 fold.gif 는 건너뜁니다.")

    note = window._stickies["n_todo"]
    pix = grab(note, harness)
    title, color, font = note._title(), note.color, note.font()
    fr = nf.FoldFrames(pix, nf.SLIP_SIZE, color=color, title=title,
                       anchor=nf.DOCK_LEFT, dock=nf.DOCK_NONE, font=font)
    cell_w, head, pad = pix.width(), 36, 34
    peek = nf.tab_width_for(title, font)
    width = pad * 2 + cell_w * 4 + 36 * 3
    c = Canvas(width, pad + head + pix.height() + pad)
    steps = (("step_open", 0.0), ("step_fold", 0.27), ("step_fold", 0.55),
             ("step_slip", 1.0))
    for i, (key, t) in enumerate(steps):
        x = pad + i * (cell_w + 36)
        if i != 2:
            c.text(_t(key), QRectF(x, pad, cell_w, head - 10), px=15)
        c.p.save()
        c.p.translate(x, pad + head)
        fr.paint(c.p, t)
        c.p.restore()
    # 마지막 칸 아래쪽에 가장자리 탭 두 모습을 함께 보인다.
    x = pad + 3 * (cell_w + 36)
    y = pad + head + 92
    c.text(_t("step_tab"), QRectF(x, y - 30, cell_w, 24), px=15)
    c.edge(x, y - 2, y + 106)
    nf.paint_slip(c.p, QRectF(x + 1, y + 4, nf.TAB_SIZE.width(),
                              nf.TAB_SIZE.height()),
                  color, title, dock=nf.DOCK_LEFT, font=font)
    nf.paint_slip(c.p, QRectF(x + 1, y + 56, peek, nf.TAB_SIZE.height()),
                  color, title, dock=nf.DOCK_LEFT, hover=True, font=font)
    c.text(_t("step_peek"), QRectF(x + peek + 14, y + 56, 190, 40),
           px=13, bold=False, color=SUB)
    harness.save(c.finish(), "fold-steps")


def _editing(window, harness) -> None:
    note = window._stickies["n_meeting"]
    note._request_edit()
    harness.settle()
    editing = grab(note, harness)
    list_menu = grab_popup(note._list_menu.exec, harness)
    color_menu = grab_popup(note._show_color_menu, harness)
    highlight = grab_popup(note._show_highlight_menu, harness)
    note._commit_and_release()

    plan = window._stickies["n_plan"]
    plan_pix = grab(plan, harness)
    viewer_pix = None
    urls = plan._text.content_image_urls()
    if urls:
        plan._open_image_viewer(urls[0], 0)
        harness.settle(250)
        if plan._image_viewer is not None:
            plan._image_viewer.resize(560, 470)
            viewer_pix = framed(grab(plan._image_viewer, harness), _t("viewer"))
            plan._image_viewer.close()

    c = Canvas(1400, 600)
    c.place(editing, 60, 60)
    if list_menu is not None:
        c.place(rounded(list_menu, 8), 250, 318, radius=8)
    if color_menu is not None:
        c.place(rounded(color_menu, 8), 60, 344, radius=8)
    if highlight is not None:
        c.place(rounded(highlight, 8), 100, 450, radius=8)
    c.place(plan_pix, 440, 60)
    if viewer_pix is not None:
        c.place(viewer_pix, 800, 48, radius=9)
    harness.save(c.finish(), "editing")


def _together(window, harness) -> None:
    locked = window._stickies["n_lunch"]
    locked.set_lock_holder(harness.USER_A)
    locked_pix = grab(locked, harness)
    locked.set_lock_holder(None)

    window._open_sessions()
    harness.settle(200)
    sessions = framed(grab(window._sessions_dialog, harness), _t("sessions"))
    window._sessions_dialog.close()

    window._open_history()
    harness.settle(300)
    hv = window._history_viewer
    hv.resize(760, 480)
    hv._list.setCurrentRow(0)
    history = framed(grab(hv, harness), _t("history"))
    hv.close()

    c = Canvas(1700, 700)
    c.place(sessions, 56, 52, radius=9)
    c.place(history, 584, 52, radius=9)
    c.place(locked_pix, 1356, 52)
    harness.save(c.finish(), "together")


def shoot(window, harness) -> None:
    animate = window._settings.fold_animation
    window._settings.fold_animation = False
    window._users = [harness.HOST, harness.USER_A, harness.USER_B]
    window._refresh_status_text()
    # 아이콘 이름처럼 작은 글자가 주인공인 그림은 배율 200% 화면으로 찍어야
    # 소개 페이지에서 또렷하다. 나머지 장면은 100% 화면의 크기로 짜여 있다.
    if screen_scale() > 1.0:
        scenes = (_desktop,)
    else:
        scenes = (_hero, _links, _icons, _views, _fold, _editing, _together)
    try:
        for scene in scenes:
            scene(window, harness)
    finally:
        window._settings.fold_animation = animate
