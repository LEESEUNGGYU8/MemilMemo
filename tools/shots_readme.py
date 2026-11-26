"""소개 페이지(README)에 쓰는 합성 이미지와 GIF.

``capture_screenshots.py`` 가 데모 데이터로 앱을 띄운 뒤 이 모듈의 ``shoot`` 를
부른다. 창은 전부 ``QWidget.grab()`` 으로 찍어 한 장에 배치한다. 실제 화면은
찍지 않는다(배경도 여기서 그린다). 창의 상단바는 앱이 직접 그리므로 찍은 그림에
들어 있다. Windows 11 이 두르는 둥근 모서리와 테두리만 여기서 더한다.

만드는 파일
    hero.png        바탕 화면 위의 메모들과 메인 창, 접힌 쪽지·아이콘, 가장자리 탭
    links.png       파일·폴더 링크가 든 메모와 링크 창
    icons.png       접힌 메모와 폴더의 모양 네 가지
    views.png       메모 목록의 보기 방식 여섯 가지
    fold.gif        접기 → 가장자리에 붙이기 → 펼치기
    fold-steps.png  같은 과정의 정지 그림(GIF 를 못 보는 곳용)
    editing.png     서식·목록·색·이미지 뷰어
    together.png    편집 중 표시, 참가자, 수정 이력
    reminders.png   알림 창, 머리 줄의 알림과 체크 해제 표시, 알림이 울린 모습
    widgets.png     메모 안의 위젯(투표, D-day, 그래프)과 위젯 고르기
    notice.png      목록 맨 위의 공지, 읽은 사람, @이름 알림

배율 200% 화면으로 따로 찍는 파일(``QT_SCALE_FACTOR=2`` 를 주고 돌린다)
    desktop-icons.png  아이콘으로 접은 메모와 폴더가 Windows 의 아이콘 옆에 놓인 모습

``--only hero,notice`` 처럼 주면 그 장면만 찍는다.
"""
from __future__ import annotations

import io

from PySide6.QtCore import QBuffer, QPoint, QPointF, QRect, QRectF, QSize, QSizeF, Qt, QTimer
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
from PySide6.QtWidgets import QApplication, QWidget

from app import i18n
from app.ui import note_fold as nf

INK = "#1F2937"
SUB = "#4B5563"
BACKDROP_TOP = "#EEF1F5"
BACKDROP_BOTTOM = "#DCE2EA"
# 바탕 화면 장면에 쓰는 배경(실제 화면을 찍지 않고 그린다).
DESKTOP_A = "#243B55"
DESKTOP_B = "#3F5F7F"
# Windows 11 이 창에 두르는 모서리.
WINDOW_RADIUS = 8

LABELS = {
    "ko": {
        "step_open": "펼친 메모", "step_fold": "쪽지로 접기",
        "step_slip": "접힌 쪽지", "step_tab": "가장자리 탭",
        "step_peek": "마우스를 올리면 제목",
        "face_slip": "쪽지", "face_icon": "아이콘",
        "face_folder_closed": "접어 둔 폴더", "face_folder_open": "열어 둔 폴더",
        "face_tab": "가장자리 탭",
    },
    "en": {
        "step_open": "Open note", "step_fold": "Folding",
        "step_slip": "Folded slip", "step_tab": "Edge tab",
        "step_peek": "Hover for the title",
        "face_slip": "Slip", "face_icon": "Icon",
        "face_folder_closed": "Folded folder", "face_folder_open": "Open folder",
        "face_tab": "Edge tab",
    },
    "ja": {
        "step_open": "開いたメモ", "step_fold": "折りたたみ",
        "step_slip": "折りたたんだ付箋", "step_tab": "端のタブ",
        "step_peek": "マウスを乗せるとタイトル",
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


def rounded(pix: QPixmap, radius: float = 10) -> QPixmap:
    """네모나게 찍히는 그림(창, 메뉴)의 모서리를 다듬고 가는 테두리를 두른다.
    창은 Windows 11 이 그리는 모양(둥근 모서리와 테두리)을 흉내 낸다."""
    out = QPixmap(pix.size())
    out.setDevicePixelRatio(pix.devicePixelRatio())
    out.fill(Qt.transparent)
    w = pix.width() / pix.devicePixelRatio()
    h = pix.height() / pix.devicePixelRatio()
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    shape = QPainterPath()
    shape.addRoundedRect(QRectF(0, 0, w, h), radius, radius)
    p.setClipPath(shape)
    p.drawPixmap(0, 0, pix)
    p.setClipping(False)
    p.setPen(QPen(QColor(17, 24, 39, 40), 1))
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), radius, radius)
    p.end()
    return out


def grab(widget, harness) -> QPixmap:
    harness.settle()
    return widget.grab()


def window_shot(widget, harness) -> QPixmap:
    """창 하나(상단바는 앱이 그린다)에 Windows 11 의 모서리를 입힌다."""
    return rounded(grab(widget, harness), WINDOW_RADIUS)


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


def fit_note(note, harness, width: int, height: int, limit: int = 720) -> None:
    """메모를 ``width`` 폭으로 두고, 본문이 굴리지 않고 다 보일 때까지 키운다(언어마다
    줄 바꿈이 달라 높이가 다르다)."""
    note.resize(width, height)
    harness.settle(150)
    bar = note._text.verticalScrollBar()
    while bar.maximum() > 0 and note.height() < limit:
        note.resize(width, note.height() + 8)
        harness.settle(60)


def last_toast(window):
    toasts = window._toasts.toasts()
    return toasts[-1] if toasts else None


def dialog_shot(dlg, harness) -> QPixmap:
    """대화 상자를 띄워 찍고 닫는다. 처음 칸에 간 초점 테두리는 지운다."""
    dlg.show()
    harness.settle(200)
    focus = dlg.focusWidget()
    if focus is not None:
        focus.clearFocus()
    pix = window_shot(dlg, harness)
    dlg.close()
    dlg.deleteLater()
    return pix


def stack(c: "Canvas", items: list, x: float, top: float, bottom: float,
          gap: float = 36) -> None:
    """그림 여럿을 세로로 쌓아 ``top``~``bottom`` 의 가운데에 둔다.
    ``items`` 는 (그림, 왼쪽 들여쓰기, place 의 선택) 목록."""
    total = sum(p.height() / p.devicePixelRatio() for p, _dx, _kw in items)
    total += gap * (len(items) - 1)
    y = top + max(0.0, (bottom - top - total) / 2)
    for pix, dx, kw in items:
        c.place(pix, x + dx, y, **kw)
        y += pix.height() / pix.devicePixelRatio() + gap


def _quiet_toasts(window):
    """알림이 소리 없이, 전체 화면 여부와 상관없이 뜨게 한다. 되돌리는 함수를 준다."""
    from app.ui import toast as toast_mod
    busy = toast_mod.user_is_busy
    sound = getattr(window._settings, "reminder_sound", True)
    toast_mod.user_is_busy = lambda: False
    window._settings.reminder_sound = False

    def restore():
        toast_mod.user_is_busy = busy
        window._settings.reminder_sound = sound
        window._toasts.close_all()
    return restore


# ── 장면 ────────────────────────────────────────────────────────────────

def _hero(window, harness) -> None:
    st = window._stickies
    window._set_view_mode(density="standard", layout="grid")
    window.resize(780, 680)
    window._choose_place("all")
    window._refresh_schedule_chips()

    reply, lunch, deploy = st["n_reply"], st["n_lunch"], st["n_deploy"]
    info = st["n_info"]
    homes = {w: w.geometry() for w in (reply, lunch, deploy, info)}
    # 사무실 정보의 마지막 줄까지 보이게 한다(반쯤 잘린 줄이 남지 않게).
    info.resize(300, 300)
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
    window._scroll.verticalScrollBar().setValue(0)

    c = Canvas(1600, 790, desktop=True)
    c.place(grab(st["n_meeting"], harness), 64, 56)
    c.place(grab(st["n_todo"], harness), 396, 56)
    c.place(grab(info, harness), 64, 352)
    c.place(grab(reply, harness), 396, 368, radius=10)
    # 아이콘은 그림자 없이 올린다(창이 아니라 바탕 화면에 놓인 것처럼).
    c.place(grab(lunch, harness), 402, 436, shadow=False)
    c.place(grab(window._folder_icons["f_store"], harness), 502, 436,
            shadow=False)
    c.place(grab(window._folder_icons["f_me"], harness), 602, 436,
            shadow=False)
    # 가장자리 탭: 마우스를 올려 제목이 보이는 상태.
    c.place(grab(deploy, harness), 0, 690, radius=10)
    c.place(window_shot(window, harness), 760, 56, radius=WINDOW_RADIUS)
    harness.save(c.finish(), "hero")

    window._remove_folder_icon("f_store")
    window._remove_folder_icon("f_me")
    deploy.undock()
    for w in (reply, lunch, deploy):
        w.unfold()
        w.set_fold_style(nf.FOLD_SLIP)
    for w, geo in homes.items():
        w.setGeometry(geo)
        w.positionChanged.emit(w.note_id, geo.x(), geo.y())
    window._set_view_mode(density="standard", layout="list")
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
    dialog = window_shot(dlg, harness)
    dlg.close()

    c = Canvas(1120, 470)
    c.place(note_pix, 60, 56)
    if menu is not None:
        c.place(rounded(menu, 8), 250, 350, radius=8)
    c.place(dialog, 560, 44, radius=WINDOW_RADIUS)
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


def _main_area_rect(window) -> QRect:
    """메인 창에서 옆줄과 상단바를 뺀 목록 자리(창 안의 좌표)."""
    area = window.findChild(QWidget, "MainArea")
    if area is None:
        return window.rect()
    return QRect(area.mapTo(window, QPoint(0, 0)), area.size())


def _views(window, harness) -> None:
    """목록의 보기 방식 여섯 가지(리스트와 그리드, 작게·보통·크게). 메인 창에서 목록
    자리만 잘라 3x2 로 놓는다. 위에 크기, 왼쪽에 보기 이름을 적는다."""
    window.resize(720, 580)
    window._choose_place("all")
    densities = ("compact", "standard", "detailed")
    layouts = ("list", "grid")
    shots: dict[tuple[str, str], QPixmap] = {}
    for layout in layouts:
        for density in densities:
            window._set_view_mode(density=density, layout=layout)
            window._scroll.verticalScrollBar().setValue(0)
            harness.settle(150)
            whole = grab(window, harness)
            shots[(layout, density)] = rounded(whole.copy(_main_area_rect(window)), 10)
    cw = shots[("list", "compact")].width()
    ch = shots[("list", "compact")].height()
    pad, gap, head, side = 34, 30, 40, 96
    width = pad * 2 + side + cw * 3 + gap * 2
    height = pad + head + ch * 2 + gap + pad
    c = Canvas(width, height)
    for i, density in enumerate(densities):
        x = pad + side + i * (cw + gap)
        c.text(i18n.t(f"main.size_{density}"), QRectF(x, pad, cw, head - 10),
               px=17, align=Qt.AlignHCenter | Qt.AlignVCenter)
    for r, layout in enumerate(layouts):
        y = pad + head + r * (ch + gap)
        c.text(i18n.t(f"main.view_{layout}"), QRectF(pad, y + 8, side - 14, 28),
               px=17)
        for i, density in enumerate(densities):
            x = pad + side + i * (cw + gap)
            c.place(shots[(layout, density)], x, y, radius=10)
    harness.save(c.finish(), "views")
    window._set_view_mode(density="standard", layout="list")
    window.resize(780, 600)


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
    home = note.geometry()
    note._request_edit()
    harness.settle()
    # 서식 줄이 나오면 본문 칸이 줄어든다. 마지막 줄(다음 회의)까지 보이게 키운다.
    fit_note(note, harness, home.width(), home.height())
    editing = grab(note, harness)
    list_menu = grab_popup(note._list_menu.exec, harness)
    color_menu = grab_popup(note._show_color_menu, harness)
    highlight = grab_popup(note._show_highlight_menu, harness)
    note._commit_and_release()
    note.setGeometry(home)

    plan = window._stickies["n_plan"]
    plan_pix = grab(plan, harness)
    viewer_pix = None
    urls = plan._text.content_image_urls()
    if urls:
        plan._open_image_viewer(urls[0], 0)
        harness.settle(250)
        if plan._image_viewer is not None:
            plan._image_viewer.resize(560, 470)
            viewer_pix = window_shot(plan._image_viewer, harness)
            plan._image_viewer.close()

    # 메모 아래에 색 고르기와 형광펜, 오른쪽에 목록 메뉴.
    color_y = 56 + editing.height() + 28
    high_y = color_y + (color_menu.height() + 22 if color_menu is not None else 0)
    bottom = high_y + (highlight.height() if highlight is not None else 0)
    c = Canvas(1480, max(680, bottom + 30))
    c.place(editing, 60, 56)
    if color_menu is not None:
        c.place(rounded(color_menu, 8), 60, color_y, radius=8)
    if highlight is not None:
        c.place(rounded(highlight, 8), 80, high_y, radius=8)
    if list_menu is not None:
        c.place(rounded(list_menu, 8), 386, 56 + editing.height() - 110, radius=8)
    c.place(plan_pix, 540, 56)
    if viewer_pix is not None:
        c.place(viewer_pix, 900, 56, radius=WINDOW_RADIUS)
    harness.save(c.finish(), "editing")


def _together(window, harness) -> None:
    locked = window._stickies["n_lunch"]
    locked.set_lock_holder(harness.USER_A)
    locked_pix = grab(locked, harness)
    locked.set_lock_holder(None)

    window._open_sessions()
    harness.settle(200)
    dlg = window._sessions_dialog
    # 지난 접속자까지 굴리지 않고 보이게 한다.
    dlg.resize(dlg.width(), max(dlg.height(), 576))
    harness.settle(150)
    sessions = window_shot(dlg, harness)
    dlg.close()

    window._open_history()
    harness.settle(300)
    hv = window._history_viewer
    hv.resize(760, 520)
    hv._list.setCurrentRow(0)
    history = window_shot(hv, harness)
    hv.close()

    c = Canvas(1700, 700)
    c.place(sessions, 56, 52, radius=WINDOW_RADIUS)
    c.place(history, 584, 52, radius=WINDOW_RADIUS)
    c.place(locked_pix, 1376, 52)
    harness.save(c.finish(), "together")


# ── 1.1 의 새 기능 ───────────────────────────────────────────────────────

def _reminders(window, harness) -> None:
    """할 일 메모의 머리 줄(알림, 체크 해제 예약), 알림 창, 체크 해제 창, 울린 알림."""
    from app import reminders as rm
    from app.ui import reminder_dialog as rd

    nid = "n_todo"
    note = window._stickies[nid]
    home = note.geometry()
    # 머리 줄의 때가 짧은 꼴로 줄지 않을 만큼 넓힌다.
    note.resize(380, 300)
    window._refresh_schedule_chips()
    harness.settle(150)
    note_pix = grab(note, harness)

    st = window._local.state
    shared = st.shared_reminders[nid]
    rule = rm.Rule.from_dict(shared["rule"])
    targets = list(shared["targets"])
    remind_pix = dialog_shot(rd.ReminderDialog(
        None, rule, mode=rd.REMIND, targets=targets,
        people=window._reminder_people(targets),
        me=window._client.nickname(), share=rd.SHARE_OK), harness)
    reset_rule = rm.Rule.from_dict(st.check_resets[nid]["rule"])
    reset_pix = dialog_shot(rd.ReminderDialog(None, reset_rule, mode=rd.RESET), harness)

    # 알림이 울린 모습(소리는 끈다).
    restore = _quiet_toasts(window)
    try:
        now = window._now()
        window._ring(rm.to_ms(now), window._SHARED, nid, rule.to_dict(), now=now)
        harness.settle(300)
        toast = last_toast(window)
        toast_pix = grab(toast, harness) if toast is not None else None
    finally:
        restore()
    note.setGeometry(home)
    harness.settle(100)

    # 왼쪽: 메모(세로 가운데), 가운데: 알림 창, 오른쪽: 체크 해제 창과 그 아래 화면
    # 구석에 뜨는 알림.
    # 언어마다 창 폭이 조금 달라도 그림 크기는 같게(소개 페이지가 크기를 적어 둔다)
    # 정해 둔 폭에 가운데로 놓는다.
    pad, gap = 52, 44
    rw, rh = remind_pix.width(), remind_pix.height()
    left_w = note_pix.width()
    content_w = left_w + gap + rw + gap + reset_pix.width()
    width = max(1440, content_w + 2 * pad)
    x0 = (width - content_w) // 2
    right_x = x0 + left_w + gap + rw + gap
    c = Canvas(width, rh + 2 * pad)
    stack(c, [(note_pix, 0, {})], x0, pad, pad + rh)
    c.place(remind_pix, x0 + left_w + gap, pad, radius=WINDOW_RADIUS)
    c.place(reset_pix, right_x, pad, radius=WINDOW_RADIUS)
    if toast_pix is not None:
        # 알림 카드는 제 그림자(여백 10)를 품고 있다. 오른쪽 끝을 창과 맞춘다.
        c.place(toast_pix, right_x + reset_pix.width() - toast_pix.width() + 10,
                pad + rh - toast_pix.height() + 10, shadow=False)
    harness.save(c.finish(), "reminders")


def _widgets(window, harness) -> None:
    """투표와 D-day 가 든 메모, 그래프가 든 메모, 서식 줄에서 여는 위젯 고르기."""
    st = window._stickies
    party, sales = st["n_party"], st["n_sales"]
    homes = {w: w.geometry() for w in (party, sales)}
    fit_note(party, harness, 310, 470)
    party.resize(310, party.height() + 16)
    harness.settle(150)
    party_pix = grab(party, harness)

    # 위젯 고르기에는 소개 글에 적은 여섯 가지만(미니게임은 설정에서 켜는 것).
    games = window._settings.widget_games
    window._settings.widget_games = False
    window._apply_widget_env(sales)
    sales.resize(360, 476)
    harness.settle(150)
    sales._request_edit()
    harness.settle(200)
    sales_pix = grab(sales, harness)
    gallery = grab_popup(sales._show_widget_gallery, harness)
    sales._commit_and_release()
    window._settings.widget_games = games
    window._apply_widget_env(sales)
    harness.settle(100)
    for w, geo in homes.items():
        w.setGeometry(geo)

    pad, gap = 56, 44
    top = pad
    height = max(party_pix.height(), sales_pix.height())
    sales_x = pad + party_pix.width() + gap
    gallery_x = sales_x + sales_pix.width() + gap
    width = gallery_x + (gallery.width() if gallery is not None else 0) + pad
    # 메모 높이는 언어마다 조금 다르다. 그림 크기는 같게 둔다.
    c = Canvas(width, max(630, height + 2 * pad))
    c.place(party_pix, pad, top)
    c.place(sales_pix, sales_x, top)
    if gallery is not None:
        # 서식 줄의 위젯 단추로 여는 고르기 판. 두 메모 곁의 세로 가운데에 둔다.
        stack(c, [(rounded(gallery, 8), 0, {"radius": 8})], gallery_x, top, top + height)
    harness.save(c.finish(), "widgets")


def _notice(window, harness) -> None:
    """목록 맨 위에 고정된 공지, 내 공지를 읽은 사람, 나를 부른 @이름 알림."""
    nid = "n_notice"
    note = window._stickies[nid]
    window._choose_place("all")
    window._set_view_mode(density="standard", layout="list")
    window.resize(760, 600)
    window._refresh_notices()
    window._scroll.verticalScrollBar().setValue(0)
    harness.settle(200)
    main_pix = window_shot(window, harness)
    home = note.geometry()
    fit_note(note, harness, home.width(), 170)
    note.resize(home.width(), note.height() + 20)
    harness.settle(100)
    note_pix = grab(note, harness)
    read_menu = grab_popup(lambda: window._show_read_list(nid), harness)

    restore = _quiet_toasts(window)
    try:
        window._on_mentioned("n_party", harness.USER_B, 0)
        harness.settle(300)
        toast = last_toast(window)
        toast_pix = grab(toast, harness) if toast is not None else None
    finally:
        restore()

    pad, gap = 52, 48
    mw, mh = main_pix.width(), main_pix.height()
    right_x = pad + mw + gap
    right_w = max(note_pix.width(), toast_pix.width() - 20 if toast_pix else 0)
    c = Canvas(right_x + right_w + pad, mh + 2 * pad)
    note.setGeometry(home)
    c.place(main_pix, pad, pad, radius=WINDOW_RADIUS)
    c.place(note_pix, right_x, pad)
    if read_menu is not None:
        # 머리 줄의 '읽음' 을 누르면 그 아래에 뜬다. 메모 글을 가리지 않게 메모 아래에 둔다.
        chip = note._read_chip.mapTo(note, QPoint(0, 0))
        c.place(rounded(read_menu, 8), right_x + chip.x() - 6,
                pad + note_pix.height() + 14, radius=8)
    if toast_pix is not None:
        # 화면 구석에 뜨는 알림(제 그림자의 여백 10 을 품고 있다).
        c.place(toast_pix, right_x + right_w - toast_pix.width() + 10,
                pad + mh - toast_pix.height() + 10, shadow=False)
    harness.save(c.finish(), "notice")


SCENES = {
    "hero": _hero, "links": _links, "icons": _icons, "views": _views,
    "fold": _fold, "editing": _editing, "together": _together,
    "reminders": _reminders, "widgets": _widgets, "notice": _notice,
}


def shoot(window, harness) -> None:
    animate = window._settings.fold_animation
    window._settings.fold_animation = False
    window._users = [harness.HOST, harness.USER_A, harness.USER_B]
    window._refresh_status_text()
    # 아이콘 이름처럼 작은 글자가 주인공인 그림은 배율 200% 화면으로 찍어야
    # 소개 페이지에서 또렷하다. 나머지 장면은 100% 화면의 크기로 짜여 있다.
    if screen_scale() > 1.0:
        scenes = [_desktop]
    else:
        only = [s.strip() for s in getattr(harness.ARGS, "only", "").split(",") if s.strip()]
        unknown = [s for s in only if s not in SCENES]
        if unknown:
            raise SystemExit(f"모르는 장면: {', '.join(unknown)} (있는 것: {', '.join(SCENES)})")
        scenes = [SCENES[s] for s in only] if only else list(SCENES.values())
    try:
        for scene in scenes:
            scene(window, harness)
    finally:
        window._settings.fold_animation = animate
