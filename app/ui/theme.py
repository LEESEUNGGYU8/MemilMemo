from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPalette
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QScrollBar,
    QStyle,
    QStyleOptionSlider,
)

from .. import config, i18n


class RoundedScrollBar(QScrollBar):
    """핸들을 항상 둥글게 표시하는 스크롤바(기본은 세로, 가로도 된다).

    Windows 네이티브 스타일이 QSS 의 border-radius 를 무시하고 핸들을 각지게
    그리므로, 스타일이 계산한 슬라이더 영역에 알약형 핸들을 직접 페인팅한다.
    슬라이더 영역을 그대로 쓰므로 드래그 히트 영역과 그림이 어긋나지 않는다."""

    def __init__(self, handle: str = "#D4D4D4", hover: str = "#B4B4B4",
                 parent=None, orientation=Qt.Vertical) -> None:
        super().__init__(orientation, parent)
        self._handle = QColor(handle)
        self._hover = QColor(hover)
        self._hovered = False
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_TranslucentBackground)

    def set_colors(self, handle, hover) -> None:
        self._handle = QColor(handle)
        self._hover = QColor(hover)
        self.update()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def _slider_rect(self):
        opt = QStyleOptionSlider()
        opt.initFrom(self)
        opt.orientation = self.orientation()
        if self.orientation() == Qt.Horizontal:
            opt.state |= QStyle.State_Horizontal
        opt.minimum = self.minimum()
        opt.maximum = self.maximum()
        opt.sliderPosition = self.sliderPosition()
        opt.sliderValue = self.value()
        opt.pageStep = self.pageStep()
        opt.singleStep = self.singleStep()
        return self.style().subControlRect(
            QStyle.CC_ScrollBar, opt, QStyle.SC_ScrollBarSlider, self)

    def paintEvent(self, event) -> None:  # noqa: N802
        if self.minimum() >= self.maximum():
            return
        rect = self._slider_rect()
        if rect.width() <= 0 or rect.height() <= 0:
            return
        r = QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = min(r.width(), r.height()) / 2.0
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        active = self._hovered or self.isSliderDown()
        painter.setBrush(self._hover if active else self._handle)
        painter.drawRoundedRect(r, radius, radius)
        painter.end()


COLOR_BG = "#FFFFFF"
COLOR_BG_SUBTLE = "#FAFAFA"
COLOR_BG_HOVER = "#F0F0F0"
COLOR_BG_SOFT = "#F5F5F5"
COLOR_BORDER = "#EAEAEA"
COLOR_BORDER_STRONG = "#D5D5D5"
COLOR_TEXT = "#1F1F1F"
COLOR_TEXT_SUBTLE = "#6B6B6B"
# 작은 글자에 쓰므로 흰 바탕에서 4.5:1 을 넘겨야 한다(#707070 = 4.95:1).
COLOR_TEXT_MUTED = "#707070"
COLOR_PLACEHOLDER = "#767676"       # 4.54:1
COLOR_ACCENT = "#1F1F1F"
# 입력칸 테두리는 칸이 어디인지 알려 주는 경계라 3:1 을 넘긴다(3.36:1).
COLOR_INPUT_BORDER = "#8C8C8C"
# 글쇠로 옮겨 다닐 때 지금 어디에 있는지 보이는 초점 테두리.
COLOR_FOCUS = "#2563EB"
# 되돌릴 수 없는 일을 하는 단추.
COLOR_DANGER = "#B91C1C"
# 연결 상태(상태줄 바탕 #FAFAFA 에서 모두 4.5:1 이상).
COLOR_STATE_OK = "#15803D"
COLOR_STATE_WAIT = "#B45309"
COLOR_STATE_OFF = "#B91C1C"


# Windows 11 다크 모드에서 QMenu 항목이 전역 QSS 를 무시하고 네이티브 다크
# 팔레트로 그려지는 경우가 있어, 팝업마다 이 스타일을 다시 입혀 라이트 톤을 강제한다.
LIGHT_MENU_QSS = (
    "QMenu { background-color: #FFFFFF; border: 1px solid #EAEAEA; "
    "border-radius: 8px; padding: 4px; color: #1F1F1F; }"
    "QMenu::item { background-color: transparent; "
    "padding: 6px 22px 6px 14px; border-radius: 6px; "
    "color: #1F1F1F; min-width: 130px; }"
    "QMenu::item:selected { background-color: #F0F0F0; color: #1F1F1F; }"
    "QMenu::item:disabled { color: #A0A0A0; background-color: transparent; }"
    "QMenu::separator { height: 1px; background: #EAEAEA; "
    "margin: 4px 6px; }"
)


# 그림 칸을 맞춘 메뉴: 보통 항목과 하위 메뉴 줄의 그림과 글이 CenteredMenuItem(그림 칸 40,
# 글은 48)과 같은 세로줄에 온다.
ALIGNED_MENU_QSS = LIGHT_MENU_QSS + (
    "QMenu::item { padding: 6px 22px 6px 24px; }"
    "QMenu::icon { padding-left: 32px; }"
    "QMenu::indicator { left: 18px; width: 12px; height: 12px; }"
    "QMenu::right-arrow { right: 12px; }")


# 메모 색의 이름. 색 견본의 말풍선과 화면 낭독기가 헥스 코드 대신 이 이름을 읽는다.
_COLOR_NAMES = {
    "#FFE066": lambda: i18n.t("color.yellow"),
    "#FFB3BA": lambda: i18n.t("color.pink"),
    "#BAFFC9": lambda: i18n.t("color.green"),
    "#BAE1FF": lambda: i18n.t("color.blue"),
    "#E0BBE4": lambda: i18n.t("color.purple"),
    "#FFD9A6": lambda: i18n.t("color.orange"),
    "#F5F5F5": lambda: i18n.t("color.white"),
    "#3A3A3A": lambda: i18n.t("color.dark"),
    "#FFF3B0": lambda: i18n.t("color.cream"),
    "#FFAD9B": lambda: i18n.t("color.coral"),
    "#D7F29B": lambda: i18n.t("color.lime"),
    "#A6E8E0": lambda: i18n.t("color.mint"),
    "#C4CBFF": lambda: i18n.t("color.periwinkle"),
    "#E8D5BC": lambda: i18n.t("color.beige"),
    "#CFD8DC": lambda: i18n.t("color.gray"),
    "#2E4057": lambda: i18n.t("color.navy"),
}


def color_name(hex_color: str) -> str:
    """메모 색의 이름. 모르는 색은 헥스 코드를 그대로 돌려준다."""
    name = _COLOR_NAMES.get((hex_color or "").upper())
    return name() if name else hex_color


def text_color_for_bg(bg_hex: str) -> str:
    """메모 색 위에 올릴 글자 색. 어두운 메모에는 흰색, 나머지에는 먹색."""
    try:
        r = int(bg_hex[1:3], 16)
        g = int(bg_hex[3:5], 16)
        b = int(bg_hex[5:7], 16)
    except (ValueError, IndexError):
        return "#1F1F1F"
    lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
    return "#FFFFFF" if lum < 0.55 else "#1F1F1F"


def load_application_font(font_path: Path) -> str:
    if not font_path.exists():
        return ""
    font_id = QFontDatabase.addApplicationFont(str(font_path))
    if font_id < 0:
        return ""
    families = QFontDatabase.applicationFontFamilies(font_id)
    return families[0] if families else ""


def load_application_fonts(language: str) -> str:
    """언어별 기본 family 를 반환한다.

    글꼴 하나를 등록하면 메모리가 크게 든다(가변 글꼴이라 Windows 가 여러 굵기를 따로 읽는다.
    Pretendard 70MB, Pretendard JP 130MB 쯤). 그래서 화면 언어에 맞는 하나만 등록한다.
    Pretendard JP 는 한글, 가나, 한자를 모두 담고, Pretendard 는 한글과 가나를 담는다.
    한국어, 영어 화면의 한자는 폴백 체인의 시스템 글꼴(맑은 고딕 등)로 그린다.
    언어는 다시 켜야 바뀌므로 켤 때 한 번 정하면 된다."""
    if language == "ja":
        jp_family = load_application_font(config.FONT_PATH_JP)
        if jp_family:
            return jp_family
    return load_application_font(config.FONT_PATH) or load_application_font(config.FONT_PATH_JP)


# 본문용 폰트 스택. 등록한 Pretendard(화면 언어에 따라 둘 중 하나)를 먼저 두고
# 시스템 폰트로 폴백한다(등록하지 않은 이름은 건너뛴다).
CONTENT_FONT_STACK = ("'Pretendard Variable', 'Pretendard JP Variable', "
                      "'Malgun Gothic', 'Meiryo', sans-serif")


def font_stack(primary: str) -> str:
    """두 Pretendard variant 를 모두 포함해 한·일 혼용을 처리하는 폴백 체인."""
    primary = primary or "Segoe UI"
    return (f'"{primary}", "Pretendard Variable", "Pretendard JP Variable", '
            f'"Malgun Gothic", "Meiryo", "Segoe UI", sans-serif')


_QSS_TEMPLATE = """
* {{
    font-family: {font_stack};
    color: {text};
}}

QMainWindow, QDialog {{
    background-color: {bg};
}}

QWidget#ContentRoot {{
    background-color: {bg};
}}

QToolTip {{
    background: #1F1F1F;
    color: #FFFFFF;
    border: none;
    padding: 4px 8px;
    border-radius: 4px;
}}

QToolButton[primary="true"], QPushButton[primary="true"] {{
    background: {bg_soft};
    border: 1px solid {border};
    border-radius: 10px;
    padding: 10px 18px;
    color: {text};
    font-size: 13px;
    font-weight: 600;
    min-height: 24px;
    text-align: center;
}}
QToolButton[primary="true"]:hover, QPushButton[primary="true"]:hover {{
    background: {bg_hover};
    border-color: {border_strong};
}}
QToolButton[primary="true"]:pressed, QPushButton[primary="true"]:pressed {{
    background: #E2E2E2;
}}
QToolButton[primary="true"][kbFocus="true"], QPushButton[primary="true"][kbFocus="true"] {{
    border: 2px solid {focus};
    padding: 9px 17px;
}}
QToolButton[primary="true"]:disabled, QPushButton[primary="true"]:disabled {{
    background: {bg_subtle};
    color: #A3A3A3;
}}

QToolButton[iconOnly="true"] {{
    background: transparent;
    border: none;
    padding: 4px;
    border-radius: 6px;
}}
QToolButton[iconOnly="true"]:hover {{
    background: rgba(0, 0, 0, 0.06);
}}
QToolButton[iconOnly="true"][kbFocus="true"] {{
    border: 2px solid {focus};
    padding: 2px;
}}

QScrollBar:vertical {{
    border: none;
    background: transparent;
    width: 10px;
    margin: 4px 2px 4px 0;
}}
QScrollBar::handle:vertical {{
    background: #D4D4D4;
    border: none;
    border-radius: 5px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{
    background: #B4B4B4;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    border: none;
    background: none;
    height: 0;
    width: 0;
}}
QScrollBar::up-arrow:vertical, QScrollBar::down-arrow:vertical {{
    border: none;
    background: none;
    image: none;
    width: 0;
    height: 0;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: none;
}}
QScrollBar:horizontal {{
    border: none;
    background: transparent;
    height: 10px;
    margin: 0 4px 2px 4px;
}}
QScrollBar::handle:horizontal {{
    background: #D4D4D4;
    border: none;
    border-radius: 5px;
    min-width: 28px;
}}
QScrollBar::handle:horizontal:hover {{
    background: #B4B4B4;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    border: none;
    background: none;
    width: 0;
    height: 0;
}}
QScrollBar::left-arrow:horizontal, QScrollBar::right-arrow:horizontal {{
    border: none;
    background: none;
    image: none;
    width: 0;
    height: 0;
}}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    background: none;
}}

QListWidget {{
    background: {bg};
    border: none;
    outline: 0;
    padding: 4px;
}}
QListWidget::item {{
    padding: 8px 10px;
    margin: 2px 0;
    border-radius: 8px;
    border: none;
    background: transparent;
    color: {text};
}}
QListWidget::item:hover {{
    background: {bg_hover};
}}
QListWidget::item:selected {{
    background: #1F1F1F;
    color: #FFFFFF;
}}
QListWidget::item:selected:hover {{
    background: #2F2F2F;
}}
/* MainWindow 의 카드 리스트는 항목 자체가 완전 커스텀 위젯이라,
   아이템 레벨 chrome 은 렌더링하지 않는다. objectName 으로 opt-in 한다. */
QListWidget#FlatList::item,
QListWidget#FlatList::item:selected,
QListWidget#FlatList::item:hover {{
    background: transparent;
    color: {text};
    padding: 0;
    margin: 0;
}}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: #FFFFFF;
    border: 1px solid {input_border};
    border-radius: 8px;
    padding: 8px 12px;
    /* 고른 글자는 옅은 회색 바탕에 원래 글자색 그대로 둔다. 글자색을 따로
       정하지 않으면 흰 글자가 되어 보이지 않는다. */
    selection-background-color: #E3E3E3;
    selection-color: {text};
    min-height: 20px;
    font-size: 13px;
    color: {text};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border: 2px solid {focus};
    padding: 7px 11px;
}}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{
    background: {bg_subtle};
    color: #9CA3AF;
    border-color: {border_strong};
}}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    width: 0;
    height: 0;
    border: none;
}}
QComboBox::drop-down {{
    border: none;
    width: 26px;
}}
/* 화살표가 없으면 글자를 치는 칸처럼 보인다. */
QComboBox::down-arrow {{
    image: url("{combo_arrow}");
    width: 12px;
    height: 12px;
    margin-right: 10px;
}}

QPushButton {{
    background: {accent};
    color: white;
    border: none;
    border-radius: 8px;
    padding: 10px 22px;
    font-weight: 600;
    font-size: 13px;
    min-height: 22px;
}}
QPushButton:hover {{
    background: #333333;
}}
QPushButton:pressed {{
    background: #0F0F0F;
}}
QPushButton:default {{
    background: {accent};
}}
/* 초점 테두리는 글쇠(Tab)로 옮겨 왔을 때만 두른다. kbFocus 속성은
   KeyboardFocusMarker 가 붙인다. 마우스로 누른 단추에까지 테두리가 남으면
   화면이 어수선하다. 안쪽 여백을 테두리만큼 줄여 크기는 그대로다.
   누를 수 없는 단추는 한눈에 달라 보여야 한다. */
QPushButton[kbFocus="true"] {{
    border: 2px solid {focus};
    padding: 8px 20px;
}}
QPushButton:disabled {{
    background: #E5E7EB;
    color: #9CA3AF;
}}
/* 보조 버튼(취소, 찾아보기 등). 코드 곳곳에서 secondary 속성을 달아 두었지만
   모양이 없어 주 버튼과 똑같이 검게 보였다. */
QPushButton[secondary="true"] {{
    background: #FFFFFF;
    color: {text};
    border: 1px solid #C9CDD3;
    padding: 9px 21px;
}}
QPushButton[secondary="true"]:hover {{
    background: #F3F4F6;
    border-color: #9CA3AF;
}}
QPushButton[secondary="true"]:pressed {{
    background: #E5E7EB;
}}
QPushButton[secondary="true"][kbFocus="true"] {{
    border: 2px solid {focus};
    padding: 8px 20px;
}}
QPushButton[secondary="true"]:disabled {{
    background: #FFFFFF;
    color: #9CA3AF;
    border-color: #E5E7EB;
}}
/* 되돌릴 수 없는 일(삭제)을 하는 단추. */
QPushButton[danger="true"] {{
    background: {danger};
    color: #FFFFFF;
}}
QPushButton[danger="true"]:hover {{
    background: #991B1B;
}}
QPushButton[danger="true"]:pressed {{
    background: #7F1D1D;
}}
QPushButton[danger="true"][kbFocus="true"] {{
    border: 2px solid #1F1F1F;
    padding: 8px 20px;
}}

/* 테두리 자리를 늘 잡아 두어 초점이 올 때 글자가 밀리지 않게 한다. */
QCheckBox, QRadioButton {{
    border: 1px solid transparent;
    border-radius: 4px;
    padding: 2px 3px;
}}
QCheckBox[kbFocus="true"], QRadioButton[kbFocus="true"] {{
    border-color: {focus};
}}

QDialogButtonBox QPushButton {{
    min-width: 80px;
}}

QTabWidget::pane {{
    border: none;
    padding-top: 8px;
}}
QTabBar {{
    qproperty-drawBase: 0;
}}
QTabBar::tab {{
    background: transparent;
    padding: 8px 18px;
    border: none;
    color: {text_subtle};
    font-weight: 600;
    font-size: 13px;
}}
QTabBar::tab:hover {{
    color: {text};
}}
QTabBar::tab:selected {{
    color: {text};
    border-bottom: 2px solid {accent};
}}

QStatusBar {{
    background: {bg_subtle};
    border-top: 1px solid {border};
    color: {text_subtle};
    font-size: 11px;
    padding: 2px 12px;
}}
QStatusBar::item {{
    border: none;
}}

QLabel[role="sectionHeader"] {{
    font-size: 12px;
    font-weight: 600;
    color: {text_subtle};
    letter-spacing: 0.3px;
}}
QLabel[role="subtle"] {{
    color: {text_muted};
    font-size: 11px;
}}
QLabel[role="title"] {{
    font-size: 14px;
    font-weight: 700;
    color: {text};
}}

/* Windows 11 다크 모드 팔레트가 새어 들어오지 않도록 처리한다.
   ``background`` 대신 더 구체적인 ``background-color`` 를 쓰고, 항목은
   명시적으로 transparent 로 둔다. polish 단계에서 QSS 가 Qt 네이티브
   스타일을 이기도록 메뉴 본체의 색과 테두리도 함께 강제 지정한다. */
QMenu {{
    background-color: #FFFFFF;
    border: 1px solid {border};
    border-radius: 8px;
    padding: 4px;
    color: {text};
}}
QMenu::item {{
    background-color: transparent;
    padding: 6px 22px 6px 14px;
    border-radius: 6px;
    color: {text};
    min-width: 130px;
}}
QMenu::item:selected {{
    background-color: {bg_hover};
    color: {text};
}}
QMenu::item:disabled {{
    color: {text_muted};
    background-color: transparent;
}}
QMenu::separator {{
    height: 1px;
    background: {border};
    margin: 4px 6px;
}}
QMenu::right-arrow {{
    width: 10px;
    height: 10px;
}}

QFormLayout > QLabel {{
    color: {text_subtle};
    font-size: 12px;
}}
"""


def build_stylesheet(font_family: str) -> str:
    return _QSS_TEMPLATE.format(
        font_stack=font_stack(font_family),
        bg=COLOR_BG,
        bg_subtle=COLOR_BG_SUBTLE,
        bg_soft=COLOR_BG_SOFT,
        bg_hover=COLOR_BG_HOVER,
        border=COLOR_BORDER,
        border_strong=COLOR_BORDER_STRONG,
        text=COLOR_TEXT,
        text_subtle=COLOR_TEXT_SUBTLE,
        text_muted=COLOR_TEXT_MUTED,
        accent=COLOR_ACCENT,
        input_border=COLOR_INPUT_BORDER,
        focus=COLOR_FOCUS,
        danger=COLOR_DANGER,
        combo_arrow=(config.RESOURCE_DIR / "Image" / "chevron-down.svg").as_posix(),
    )


class KeyboardFocusMarker(QObject):
    """글쇠로 초점이 옮겨 온 단추에만 ``kbFocus`` 속성을 붙인다.

    Qt 스타일시트의 ``:focus`` 는 마우스로 눌러 생긴 초점과 Tab 으로 옮겨 온
    초점을 가리지 않는다. 그대로 쓰면 누른 단추마다 테두리가 남는다. 초점
    테두리는 글쇠로 다니는 사람에게 지금 자리를 알려 주는 표시이므로 그때만
    보인다(입력칸은 어느 쪽이든 늘 보인다)."""

    _KEY_REASONS = (Qt.TabFocusReason, Qt.BacktabFocusReason,
                    Qt.ShortcutFocusReason)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        kind = event.type()
        if kind == QEvent.FocusIn or kind == QEvent.FocusOut:
            if isinstance(obj, QAbstractButton):
                want = (kind == QEvent.FocusIn
                        and event.reason() in self._KEY_REASONS)
                if bool(obj.property("kbFocus")) != want:
                    obj.setProperty("kbFocus", want)
                    refresh_style(obj)
        elif kind == QEvent.Show or kind == QEvent.ToolTipChange:
            # 그림만 있는 단추는 화면 낭독기에 이름이 없다. 말풍선 글자를 이름으로 준다.
            if (isinstance(obj, QAbstractButton) and not obj.accessibleName()
                    and not obj.text() and obj.toolTip()):
                obj.setAccessibleName(obj.toolTip())
        return False


def apply_theme(app: QApplication, font_family: str) -> None:
    # QSS 가 덮지 못한 위젯이 Windows 다크 모드 시스템 색으로 폴백하지 않도록
    # 라이트 팔레트를 강제한다. 예: 팝업 QMenu 가 검정 위 검정으로 표시되는 문제.
    pal = app.palette()
    pal.setColor(QPalette.Window, QColor(COLOR_BG))
    pal.setColor(QPalette.Base, QColor(COLOR_BG))
    pal.setColor(QPalette.AlternateBase, QColor(COLOR_BG_SUBTLE))
    pal.setColor(QPalette.WindowText, QColor(COLOR_TEXT))
    pal.setColor(QPalette.Text, QColor(COLOR_TEXT))
    pal.setColor(QPalette.ButtonText, QColor(COLOR_TEXT))
    pal.setColor(QPalette.Button, QColor(COLOR_BG_SOFT))
    pal.setColor(QPalette.ToolTipBase, QColor("#1F1F1F"))
    pal.setColor(QPalette.ToolTipText, QColor("#FFFFFF"))
    pal.setColor(QPalette.Highlight, QColor("#1F1F1F"))
    pal.setColor(QPalette.HighlightedText, QColor("#FFFFFF"))
    pal.setColor(QPalette.PlaceholderText, QColor(COLOR_PLACEHOLDER))
    app.setPalette(pal)
    app.setStyleSheet(build_stylesheet(font_family))
    if getattr(app, "_kb_focus_marker", None) is None:
        app._kb_focus_marker = KeyboardFocusMarker(app)
        app.installEventFilter(app._kb_focus_marker)
    if getattr(app, "_plain_label_guard", None) is None:
        # 닉네임이나 메모 제목이 HTML 처럼 생겼어도 글자 그대로 그린다.
        from .safe_text import PlainLabelGuard
        app._plain_label_guard = PlainLabelGuard(app)
        app.installEventFilter(app._plain_label_guard)
    if getattr(app, "_edit_menu_filter", None) is None:
        # 입력칸의 우클릭 메뉴를 앱의 메뉴로 바꾼다(Qt 기본은 영어다).
        from .edit_menu import EditMenuFilter
        app._edit_menu_filter = EditMenuFilter(app)
        app.installEventFilter(app._edit_menu_filter)
    default = QFont(font_family or "Segoe UI", 10)
    app.setFont(smooth_font(default))


def smooth_font(font: QFont) -> QFont:
    """글자 가장자리를 가로세로 모두 다듬어 그리게 한다.

    화면 배율 100% 에서 Qt 는 글자를 픽셀 격자에 맞춰(힌팅) 그리고 가로 방향만
    다듬는다. 그래서 한글의 가로 획은 1px 아니면 2px 로 뚝뚝 끊기고, ㅇ·ㅅ 같은
    둥근 획과 빗금에는 계단이 진다. 힌팅을 끄면 세로 방향도 다듬어 웹 브라우저가
    같은 글꼴을 그릴 때와 비슷한 모양이 된다. 글자 그림은 한 번 만들어 두고 다시
    쓰므로 느려지지 않는다.

    PreferAntialias 는 Windows 의 '화면 글꼴의 가장자리 다듬기'가 꺼져 있어도
    (성능 옵션을 '최적 성능'으로 둔 PC) 다듬어 그리게 한다.

    앱 글꼴에 한 번 주면 위젯과 메모 본문이 물려받는다."""
    font.setHintingPreference(QFont.PreferNoHinting)
    font.setStyleStrategy(QFont.PreferAntialias)
    return font


def refresh_style(widget) -> None:
    """polish 이후 바뀐 동적 프로퍼티를 셀렉터에 다시 반영한다.

    Qt 는 polish 뒤의 ``setProperty`` 를 자동 재평가하지 않으므로, 스타일에
    영향을 주는 setProperty 호출 뒤에는 이 헬퍼를 거쳐야 한다."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
