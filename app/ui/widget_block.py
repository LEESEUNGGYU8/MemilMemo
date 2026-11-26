"""메모 안 위젯 하나의 자리(본문의 위젯 칸 위에 놓인다).

* 위젯(판)을 보이고 누르면 공유 PC 로 동작을 보낸다.
* 마우스를 올리면 오른쪽 위에 '⋯': 위젯 고치기, 크기, 바탕, 삭제. '⋯' 자리는 늘 비워
  두어 위젯 내용을 덮지 않는다.
* 오른쪽 가장자리를 끌어 폭을 바꾼다(놓으면 모두에게 같은 폭).
* 처음 넣을 때와 '위젯 고치기' 에서는 카드 안이 설정 칸으로 바뀐다(종류마다 다른 칸).

공유 PC 로 보내는 것은 모두 ``opRequested`` 로 낸다. 본문의 칸 크기와 위치는 메모 창이
맞춘다(``sizeChanged``, ``deleteRequested``)."""
from __future__ import annotations

from datetime import date

from PySide6.QtCore import QDate, QEvent, QObject, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractScrollArea,
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import i18n
from ..widgets import texts as wtexts
from ..widgets.base import MAX_WIDTH, MIN_WIDTH, as_date
from ..widgets.draw import MIN_WIDTH as DRAW_MIN
from .icons import svg_icon
from .theme import ALIGNED_MENU_QSS, refresh_style

# 크기 고르기(논리 픽셀). 0 은 메모 폭에 맞춤.
SIZES = (("widget.size_small", 240), ("widget.size_medium", 320),
         ("widget.size_large", 440), ("widget.size_fit", 0))
EDGE = 6                    # 폭을 바꾸는 오른쪽 가장자리

# 설정 칸: 위젯 카드와 같은 흰 판(10px 모서리, 14px 여백). 입력 칸은 메모 글보다 크지
# 않게(높이 30). 이름표는 12px 옅은 회색, 이름표와 칸 사이 4px, 칸과 칸 사이 12px.
_PANEL_QSS = (
    "QFrame#WidgetPanel { background: #FFFFFF; border: 1px solid #E6E6E6;"
    " border-radius: 10px; }"
    "QLabel#PanelTitle { font-size: 14px; font-weight: 600; color: #1F1F1F;"
    " background: transparent; }"
    "QLabel[role=\"fieldLabel\"] { color: #6B6B6B; font-size: 12px; background: transparent; }"
    "QLineEdit, QComboBox { min-height: 18px; max-height: 18px; padding: 5px 9px;"
    " font-size: 13px; border-radius: 6px; }"
    "QLineEdit:focus, QComboBox:focus { padding: 4px 8px; }"
    "QCheckBox { font-size: 13px; spacing: 8px; padding: 0px; }"
    "QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #8A8A8A;"
    " border-radius: 4px; background: #FFFFFF; }"
    "QCheckBox::indicator:hover { border-color: #6B6B6B; }"
    "QCheckBox::indicator:checked { background: #1F1F1F; border-color: #1F1F1F;"
    " image: url(\"{check}\"); }"
    "QPushButton#DateField { background: #FFFFFF; color: #1F1F1F; text-align: left;"
    " border: 1px solid #D1D5DB; border-radius: 6px; padding: 5px 9px; min-height: 18px;"
    " max-height: 18px; font-size: 13px; font-weight: 400; }"
    "QPushButton#DateField:hover { border-color: #9CA3AF; }"
    "QToolButton#AddRow { border: none; background: transparent; color: #6B6B6B;"
    " font-size: 12px; padding: 4px 6px; margin-left: -6px; border-radius: 6px; }"
    "QToolButton#AddRow:hover { background: #F4F4F4; color: #000000; }"
    "QTableWidget { border: 1px solid #D1D5DB; border-radius: 6px; gridline-color: #EEEEEE;"
    " font-size: 12px; background: #FFFFFF; selection-background-color: #EEF2F7;"
    " selection-color: #1F1F1F; }"
    "QTableWidget QLineEdit { border: none; border-radius: 0px; padding: 0px 4px;"
    " min-height: 0px; max-height: 1000px; }"
    "QHeaderView::section { background: #F7F7F7; color: #6B6B6B; font-size: 12px;"
    " border: none; border-bottom: 1px solid #E6E6E6; border-right: 1px solid #EEEEEE;"
    " padding: 4px 6px; }")


def _panel_qss() -> str:
    from .. import config
    check = (config.RESOURCE_DIR / "Image" / "check-white.svg").as_posix()
    return _PANEL_QSS.replace("{check}", check)


# 취소와 만들기 모두 높이 28(테두리 1 + 여백 6 + 글 14 + 여백 6 + 테두리 1).
_BTN_QSS = ("QPushButton { min-height: 14px; max-height: 14px; padding: 6px 14px;"
            " font-size: 12px; border-radius: 6px; border: 1px solid #1F1F1F; }"
            "QPushButton[secondary=\"true\"] { border: 1px solid #D2D2D2; }")


def _label(text: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("role", "fieldLabel")
    return label


def _add_button(text: str) -> QToolButton:
    b = QToolButton()
    b.setObjectName("AddRow")
    b.setText("+ " + text)
    b.setCursor(Qt.PointingHandCursor)
    b.setFocusPolicy(Qt.TabFocus)
    return b


class _ListField(QWidget):
    """한 줄에 하나씩 적는 목록(투표 항목, 메뉴, 참가자). 줄 끝의 ×로 빼고 '+ 추가' 로 더한다.
    Enter 는 다음 줄을 열고, 비어 있는 마지막 줄에서 Enter 를 누르면 목록을 나간다(``left``)."""

    left = Signal()
    # 줄이 늘거나 줄었다(위젯 칸의 높이를 다시 맞춘다).
    resized = Signal()

    def __init__(self, add_text: str, minimum: int = 1, limit: int = 30,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._minimum = minimum
        self._limit = limit
        self._v = QVBoxLayout(self)
        self._v.setContentsMargins(0, 0, 0, 0)
        self._v.setSpacing(6)
        self._rows: list[QLineEdit] = []
        self._add = _add_button(add_text)
        self._add.clicked.connect(lambda: self.add_row("", focus=True))
        self._v.addWidget(self._add, 0, Qt.AlignLeft)

    def set_values(self, values: list[str]) -> None:
        for row in self._rows:
            self._v.removeWidget(row)
            row.hide()
            row.deleteLater()
        self._rows = []
        for text in values:
            self.add_row(text)
        while len(self._rows) < self._minimum:
            self.add_row("")

    def values(self, unique: bool = True) -> list[str]:
        """채운 줄의 글. ``unique`` 면 같은 글은 한 번만(사다리 결과는 같은 글도 그대로)."""
        out = []
        for row in self._rows:
            text = row.text().strip()
            if text and (not unique or text not in out):
                out.append(text)
        return out

    def rows(self) -> list[QLineEdit]:
        return list(self._rows)

    def add_row(self, text: str = "", focus: bool = False) -> None:
        if len(self._rows) >= self._limit:
            return
        line = QLineEdit(text, self)
        line.setMaxLength(200)
        # Enter 는 eventFilter 가 받아 끝낸다(본문으로 새지 않게).
        remove = QToolButton(line)
        remove.setIcon(svg_icon("close", color="#6B6B6B"))
        remove.setIconSize(QSize(10, 10))
        remove.setCursor(Qt.PointingHandCursor)
        remove.setFocusPolicy(Qt.NoFocus)
        remove.setToolTip(i18n.t("common.btn_delete"))
        remove.setStyleSheet(
            "QToolButton { border: none; background: transparent; border-radius: 4px; }"
            "QToolButton:hover { background: #F0F0F0; }")
        remove.clicked.connect(lambda: self._remove(line))
        line.setTextMargins(0, 0, 18, 0)
        line._remove_btn = remove           # 크기를 바꿀 때 오른쪽에 붙인다
        line.installEventFilter(self)
        self._rows.append(line)
        self._v.insertWidget(len(self._rows) - 1, line)
        self._add.setVisible(len(self._rows) < self._limit)
        line.show()                     # 보이기 전에는 글쇠를 받을 수 없다
        if focus:
            line.setFocus()
        self.updateGeometry()
        self.resized.emit()

    def _on_return(self, line: QLineEdit) -> None:
        if line not in self._rows:
            return
        last = line is self._rows[-1]
        if last and not line.text().strip():
            if len(self._rows) > self._minimum:
                self._remove(line)
            self.left.emit()
            return
        if last:
            self.add_row("", focus=True)
        else:
            self._rows[self._rows.index(line) + 1].setFocus()

    def _remove(self, line: QLineEdit) -> None:
        if line not in self._rows:
            return
        if len(self._rows) <= self._minimum:
            line.clear()
            return
        self._rows.remove(line)
        self._v.removeWidget(line)
        line.hide()
        line.deleteLater()
        self._add.setVisible(True)
        self.updateGeometry()
        self.resized.emit()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if (event.type() == QEvent.KeyPress and obj in self._rows
                and event.key() in (Qt.Key_Return, Qt.Key_Enter)
                and not event.modifiers() & Qt.ControlModifier):
            self._on_return(obj)
            return True
        if event.type() == QEvent.Resize and hasattr(obj, "_remove_btn"):
            btn = obj._remove_btn
            btn.setGeometry(obj.width() - 24, (obj.height() - 18) // 2, 18, 18)
        return super().eventFilter(obj, event)


class _DateField(QPushButton):
    """날짜 칸: 누르면 앱의 달력 판이 열린다."""

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DateField")
        self.setCursor(Qt.PointingHandCursor)
        self.setIcon(svg_icon("w_calendar", color="#6B6B6B", size=14))
        self.setIconSize(QSize(14, 14))
        self._date = date.today()
        self.clicked.connect(self._open)
        self._popup = None
        self._show()

    def value(self) -> date:
        return self._date

    def set_value(self, d: date) -> None:
        self._date = d
        self._show()

    def _show(self) -> None:
        self.setText("  " + wtexts.format_date(self._date))

    def _open(self) -> None:
        from .reminder_dialog import MonthPanel
        popup = QFrame(self, Qt.Popup)
        popup.setObjectName("DatePopup")
        popup.setStyleSheet("QFrame#DatePopup { background: #FFFFFF; border: 1px solid #D5D5D5;"
                            " border-radius: 8px; }")
        box = QVBoxLayout(popup)
        box.setContentsMargins(4, 4, 4, 4)
        panel = MonthPanel(self._date, date.today(), None, popup)
        box.addWidget(panel)

        def picked(value) -> None:
            d = value if isinstance(value, date) else value.toPython()
            popup.close()
            if isinstance(d, date):
                self.set_value(d)
                self.changed.emit()

        panel.picked.connect(picked)
        popup.adjustSize()
        popup.move(self.mapToGlobal(QPoint(0, self.height() + 4)))
        popup.show()
        self._popup = popup


class _Form(QWidget):
    """종류마다 다른 설정 칸. ``load(data)``, ``ops(data)`` 로 보낼 동작들."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.v = QVBoxLayout(self)
        self.v.setContentsMargins(0, 0, 0, 0)
        self.v.setSpacing(4)

    def section(self, text: str, widget: QWidget) -> None:
        if self.v.count():
            self.v.addSpacing(10)
        self.v.addWidget(_label(text))
        self.v.addWidget(widget)

    def first_field(self) -> QWidget | None:
        return None

    def load(self, data: dict) -> None:
        raise NotImplementedError

    def ops(self, data: dict) -> list[dict]:
        raise NotImplementedError


class _VoteForm(_Form):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.question = QLineEdit()
        self.question.setMaxLength(200)
        self.options = _ListField(i18n.t("widget.add_option"), minimum=2, limit=20)
        self.multi = QCheckBox(i18n.t("widget.vote_multi"))
        self.anonymous = QCheckBox(i18n.t("widget.vote_anonymous"))
        self.section(i18n.t("widget.vote_question"), self.question)
        self.section(i18n.t("widget.vote_options"), self.options)
        self.v.addSpacing(8)
        self.v.addWidget(self.multi)
        self.v.addSpacing(2)
        self.v.addWidget(self.anonymous)

    def first_field(self) -> QWidget:
        return self.question

    def load(self, data: dict) -> None:
        self.question.setText(data.get("question", ""))
        self.options.set_values([o["text"] for o in data.get("options", [])])
        self.multi.setChecked(bool(data.get("multi")))
        self.anonymous.setChecked(bool(data.get("anonymous")))

    def ops(self, data: dict) -> list[dict]:
        return [{"op": "configure", "question": self.question.text().strip(),
                 "options": self.options.values(), "multi": self.multi.isChecked(),
                 "anonymous": self.anonymous.isChecked()}]


class _OrderForm(_Form):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.menu = _ListField(i18n.t("widget.add_item"), minimum=1, limit=30)
        self.section(i18n.t("widget.order_menu"), self.menu)

    def first_field(self) -> QWidget | None:
        rows = self.menu.rows()
        return rows[0] if rows else None

    def load(self, data: dict) -> None:
        self.menu.set_values(list(data.get("menu", [])))

    def ops(self, data: dict) -> list[dict]:
        return [{"op": "set_menu", "menu": self.menu.values()}]


class _LadderForm(_Form):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.people = _ListField(i18n.t("widget.add_person"), minimum=2, limit=30)
        self.results = _ListField(i18n.t("widget.add_result"), minimum=2, limit=30)
        self.section(wtexts.tr("ladder.people"), self.people)
        self.section(wtexts.tr("ladder.results"), self.results)

    def first_field(self) -> QWidget | None:
        rows = self.people.rows()
        return rows[0] if rows else None

    def load(self, data: dict) -> None:
        self.people.set_values(list(data.get("participants", [])))
        self.results.set_values(list(data.get("results", [])))

    def ops(self, data: dict) -> list[dict]:
        return [{"op": "set_entries", "participants": self.people.values(),
                 "results": self.results.values(unique=False)}]


class _DdayForm(_Form):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.label = QLineEdit()
        self.label.setMaxLength(200)
        self.day = _DateField()
        self.section(i18n.t("widget.dday_label"), self.label)
        self.section(i18n.t("widget.dday_date"), self.day)

    def first_field(self) -> QWidget:
        return self.label

    def load(self, data: dict) -> None:
        self.label.setText(data.get("label", ""))
        d = as_date(data.get("date"))
        if d is None:
            d = QDate.currentDate().addDays(7).toPython()
        self.day.set_value(d)

    def ops(self, data: dict) -> list[dict]:
        return [{"op": "set", "label": self.label.text().strip(),
                 "date": self.day.value().isoformat()}]


class _CalendarForm(_Form):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.country = QComboBox()
        for code in ("KR", "JP", "US"):
            self.country.addItem(wtexts.tr("country." + code), userData=code)
        # 공휴일, 기념일, 세무 신고일은 달력 아래의 칸에서 바로 켜고 끈다.
        self.section(wtexts.tr("cal.country"), self.country)

    def first_field(self) -> QWidget:
        return self.country

    def load(self, data: dict) -> None:
        index = self.country.findData(data.get("country", "KR"))
        self.country.setCurrentIndex(max(0, index))

    def ops(self, data: dict) -> list[dict]:
        if self.country.currentData() != data.get("country"):
            return [{"op": "set_country", "country": self.country.currentData()}]
        return []


class _GraphForm(_Form):
    _ROW_H = 26
    _ROWS_SHOWN = 6

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.title = QLineEdit()
        self.title.setMaxLength(200)
        self.chart = QComboBox()
        self.chart.addItem(wtexts.tr("graph.bar"), userData="bar")
        self.chart.addItem(wtexts.tr("graph.line"), userData="line")
        self.table = QTableWidget(4, 2)
        self.table.setHorizontalHeaderLabels([i18n.t("widget.graph_item"),
                                              i18n.t("widget.graph_value")])
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(self._ROW_H)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.horizontalHeader().setFixedHeight(self._ROW_H)
        self.table.setFrameShape(QFrame.NoFrame)
        self.table.setEditTriggers(QAbstractItemView.AllEditTriggers)
        self._fit_table()
        self.table.model().rowsInserted.connect(self._fit_table)
        self.table.model().rowsRemoved.connect(self._fit_table)
        row = QHBoxLayout()
        row.setContentsMargins(0, 2, 0, 0)
        row.setSpacing(4)
        for text, fn in ((i18n.t("widget.graph_add_row"), self._add_row),
                         (i18n.t("widget.graph_add_series"), self._add_series)):
            b = _add_button(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        self.section(i18n.t("widget.graph_title"), self.title)
        self.section(i18n.t("widget.graph_shape"), self.chart)
        self.section(i18n.t("widget.graph_data"), self.table)
        self.v.addLayout(row)

    def first_field(self) -> QWidget:
        return self.title

    def _fit_table(self, *_args) -> None:
        """줄 수만큼(여섯 줄까지) 높이를 맞춘다. 더 많으면 표 안에서 굴러간다."""
        rows = max(1, min(self._ROWS_SHOWN, self.table.rowCount()))
        self.table.setFixedHeight(self._ROW_H * (rows + 1) + 2)

    def _add_row(self) -> None:
        if self.table.rowCount() < 200:
            self.table.insertRow(self.table.rowCount())
            self.table.scrollToBottom()

    def _add_series(self) -> None:
        if self.table.columnCount() < 7:
            col = self.table.columnCount()
            self.table.insertColumn(col)
            self.table.setHorizontalHeaderItem(
                col, QTableWidgetItem(f"{i18n.t('widget.graph_value')} {col}"))

    def load(self, data: dict) -> None:
        self.title.setText(data.get("title", ""))
        self.chart.setCurrentIndex(max(0, self.chart.findData(data.get("chart", "bar"))))
        labels = list(data.get("labels", []))
        series = list(data.get("series", []))
        self.table.clearContents()
        self.table.setRowCount(max(4, len(labels)))
        self.table.setColumnCount(1 + max(1, len(series)))
        self.table.setHorizontalHeaderItem(0, QTableWidgetItem(i18n.t("widget.graph_item")))
        for c in range(max(1, len(series))):
            name = series[c]["name"] if c < len(series) and series[c].get("name") else \
                i18n.t("widget.graph_value")
            self.table.setHorizontalHeaderItem(c + 1, QTableWidgetItem(name))
        for r, label in enumerate(labels):
            self.table.setItem(r, 0, QTableWidgetItem(label))
            for c, s in enumerate(series):
                values = s.get("values", [])
                if r < len(values) and values[r] is not None:
                    self.table.setItem(r, c + 1, QTableWidgetItem(_num_text(values[r])))

    def ops(self, data: dict) -> list[dict]:
        labels, series = [], [{"name": self._header(c), "values": []}
                              for c in range(1, self.table.columnCount())]
        for r in range(self.table.rowCount()):
            label = self._cell(r, 0)
            values = [_to_num(self._cell(r, c)) for c in range(1, self.table.columnCount())]
            if not label and all(v is None for v in values):
                continue
            labels.append(label)
            for s, v in zip(series, values):
                s["values"].append(v)
        out = [{"op": "set_data", "labels": labels, "series": series,
                "title": self.title.text().strip()}]
        if self.chart.currentData() != data.get("chart"):
            out.append({"op": "set_kind", "chart": self.chart.currentData()})
        return out

    def _cell(self, r: int, c: int) -> str:
        item = self.table.item(r, c)
        return item.text().strip() if item is not None else ""

    def _header(self, c: int) -> str:
        item = self.table.horizontalHeaderItem(c)
        return item.text().strip() if item is not None else ""


class _NoWheel(QObject):
    """고르는 칸이 마우스 휠로 바뀌지 않게(메모를 굴리다 값이 바뀌지 않는다). 휠은 메모로 간다."""

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Wheel and not obj.hasFocus():
            # 위젯이 든 메모(본문 칸)의 굴리기 막대로 보낸다.
            parent = obj.parentWidget()
            while parent is not None and not (isinstance(parent, QAbstractScrollArea)
                                              and not isinstance(parent, QAbstractItemView)):
                parent = parent.parentWidget()
            if parent is not None:
                QApplication.sendEvent(parent.verticalScrollBar(), event)
            return True
        return False


_NO_WHEEL = None


def _no_wheel(widget: QWidget) -> None:
    global _NO_WHEEL
    if _NO_WHEEL is None:
        _NO_WHEEL = _NoWheel()
    widget.setFocusPolicy(Qt.StrongFocus)
    widget.installEventFilter(_NO_WHEEL)


def _num_text(value) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def _to_num(text: str):
    try:
        return float(text.replace(",", "")) if text else None
    except ValueError:
        return None


_FORMS = {"vote": _VoteForm, "order": _OrderForm, "ladder": _LadderForm,
          "dday": _DdayForm, "calendar": _CalendarForm, "graph": _GraphForm}
# 넣자마자 정할 것이 있는 종류(나머지는 바로 쓴다).
_SETUP_FIRST = {"vote", "order", "ladder", "dday", "graph"}
# 만든 사람만 고칠 수 있는 종류(나머지는 누구나).
_OWNER_ONLY = {"vote", "order", "ladder"}


def wants_setup(kind_name: str) -> bool:
    return kind_name in _SETUP_FIRST


def has_form(kind_name: str) -> bool:
    return kind_name in _FORMS


class _Grip(QWidget):
    """오른쪽 아래 모서리의 크기 손잡이. 위젯에 마우스를 올린 동안 보인다."""

    SIZE = 14

    def __init__(self, block: "WidgetBlock") -> None:
        super().__init__(block)
        self._block = block
        self._from = None
        self._hot = False
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setToolTip(i18n.t("widget.size"))
        self.hide()

    def paintEvent(self, _event) -> None:  # noqa: N802
        from PySide6.QtGui import QColor, QPainter, QPen
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor("#6B6B6B" if self._hot or self._from else "#A0A0A0"), 1.4,
                      Qt.SolidLine, Qt.RoundCap))
        s = self.SIZE
        for k in (4, 8):
            p.drawLine(s - 2, s - 2 - k, s - 2 - k, s - 2)
        p.end()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hot = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hot = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._from = (event.globalPosition().toPoint(), self._block.width(),
                          self._block.height())
            self._block.begin_resize()
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._from is not None:
            start, w, h = self._from
            d = event.globalPosition().toPoint() - start
            self._block.resize_to(w + d.x(), h + d.y())
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._from is not None:
            self._from = None
            self._block.end_resize()
            self.update()
        event.accept()


class WidgetBlock(QWidget):
    """본문의 위젯 칸 위에 놓이는 위젯 하나."""

    opRequested = Signal(dict)
    # 본문 칸의 크기를 바꿔야 한다(높이나 폭이 달라졌다). 폭은 미리 보기 폭.
    sizeChanged = Signal()
    # 다 보이게 메모를 키워 달라(설정 칸이 열리거나 커졌다).
    roomWanted = Signal()
    deleteRequested = Signal()

    def __init__(self, kind, data: dict, viewer: str, today: date, *, background: str,
                 can_delete: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.kind = kind
        self.viewer = viewer
        self.can_delete = can_delete
        self.preview_width = 0          # 끄는 동안의 폭(0 이면 데이터의 폭)
        self._setup_first = False
        self._drag_from: tuple[int, int] | None = None
        self.setMouseTracking(True)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.editor = kind.create_editor(data, viewer, today, background=background, parent=self)
        self.editor.opRequested.connect(self.opRequested)
        self.editor.localRequested.connect(self._on_local)
        v.addWidget(self.editor)
        self.editor.canvas.installEventFilter(self)
        self.editor.installEventFilter(self)
        # 위젯마다 붙어 있던 입력 칸은 설정 칸으로 옮겼다. 다시 나타나려 하면 감춘다.
        for w in self.editor.extras.values():
            w.installEventFilter(self)
        self._hide_extras()

        # 설정 칸(처음 넣을 때와 '위젯 고치기'). 열리면 카드 자리를 차지한다.
        self.panel = QFrame(self)
        self.panel.setObjectName("WidgetPanel")
        self.panel.setStyleSheet(_panel_qss())
        pv = QVBoxLayout(self.panel)
        pv.setContentsMargins(14, 12, 14, 14)
        pv.setSpacing(0)
        title = QLabel(wtexts.tr("kind." + kind.kind), self.panel)
        title.setObjectName("PanelTitle")
        pv.addWidget(title)
        pv.addSpacing(10)
        self.form = _FORMS[kind.kind]() if kind.kind in _FORMS else None
        if self.form is not None:
            pv.addWidget(self.form)
        pv.addSpacing(16)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        self._cancel = QPushButton(i18n.t("common.btn_cancel"))
        self._cancel.setProperty("secondary", True)
        refresh_style(self._cancel)
        self._done = QPushButton(i18n.t("widget.btn_done"))
        for b in (self._cancel, self._done):
            b.setStyleSheet(_BTN_QSS)
            b.setCursor(Qt.PointingHandCursor)
            buttons.addWidget(b)
        pv.addLayout(buttons)
        self._cancel.clicked.connect(self._cancel_setup)
        self._done.clicked.connect(self._apply_setup)
        if self.form is not None:
            for combo in self.form.findChildren(QComboBox):
                _no_wheel(combo)
            for field in self.form.findChildren(_ListField):
                field.left.connect(lambda f=field: self._next_input(f))
                field.resized.connect(self._panel_resized)
        self.panel.hide()
        v.addWidget(self.panel)
        # 설정 칸에서도 오른쪽 가장자리로 폭을 바꾼다.
        self.panel.setMouseTracking(True)
        self.panel.installEventFilter(self)
        self._grip = _Grip(self)
        self.preview_height = 0         # 끄는 동안의 높이(높이를 고를 수 있는 종류만)

    # ── 데이터 ──

    def data(self) -> dict:
        return self.editor.current()

    def set_data(self, data: dict) -> None:
        before = self.content_height(self.width() or 320)
        self.editor.set_data(data)
        self._hide_extras()
        if self.content_height(self.width() or 320) != before:
            self.sizeChanged.emit()

    def set_readonly(self, on: bool) -> None:
        self.editor.readonly = bool(on) or self.editor.readonly
        self.editor.view["menu_ok"] = not on
        self.editor.canvas.update()

    def _hide_extras(self) -> None:
        for w in self.editor.extras.values():
            w.hide()

    _edge_start = None

    def wanted_width(self) -> int:
        """데이터에 적힌 폭(0 이면 메모 폭에 맞춤)."""
        if self.preview_width:
            return self.preview_width
        return int(self.data().get("w") or 0)

    def content_height(self, width: int) -> int:
        """이 폭에서의 높이(설정 칸이 열려 있으면 그 칸의 높이)."""
        if self._panel_wanted:
            self.panel.setFixedWidth(max(DRAW_MIN, width))
            self.panel.layout().activate()
            return self.panel.sizeHint().height()
        return max(24, int(self.kind.measure(self.data(), max(DRAW_MIN, width),
                                             self.editor.ctx())))

    _panel_wanted = False

    # ── 설정 칸 ──

    def can_configure(self) -> bool:
        if self.form is None or self.editor.readonly:
            return False
        if self.kind.kind in _OWNER_ONLY:
            return self.kind.is_owner(self.data(), self.viewer)
        return True

    def open_setup(self, first: bool = False) -> None:
        if not self.can_configure():
            return
        self._setup_first = first
        self.form.load(self.data())
        self._done.setText(i18n.t("widget.btn_create" if first else "widget.btn_done"))
        self._panel_wanted = True
        self.editor.hide()
        self.panel.show()
        self.roomWanted.emit()
        self.sizeChanged.emit()
        field = self.form.first_field()
        if field is not None:
            field.setFocus()

    def _panel_resized(self) -> None:
        if self._panel_wanted:
            self.roomWanted.emit()
            self.sizeChanged.emit()

    def setup_open(self) -> bool:
        return self._panel_wanted

    def _close_setup(self) -> None:
        self._panel_wanted = False
        self.panel.hide()
        self.editor.show()
        self.sizeChanged.emit()

    def _cancel_setup(self) -> None:
        first = self._setup_first
        self._close_setup()
        if first:
            # 처음 넣고 아무것도 정하지 않았다. 넣은 위젯을 뺀다.
            self.deleteRequested.emit()

    def _apply_setup(self) -> None:
        if self.form is not None:
            for op in self.form.ops(self.data()):
                self.opRequested.emit(op)
        self._close_setup()

    # ── 글쇠: 위젯 안에서 끝난다(본문의 줄바꿈이나 탭으로 새지 않는다) ──

    def _focus_chain(self, *, inputs_only: bool = False) -> list[QWidget]:
        """Tab 으로 도는 칸들. 보이는 차례(위에서 아래, 왼쪽에서 오른쪽)."""
        root = self.panel if self._panel_wanted else self.editor
        out = []
        for w in root.findChildren(QWidget):
            if not w.isVisibleTo(self) or not w.isEnabled():
                continue
            if not (w.focusPolicy() & Qt.TabFocus):
                continue
            parent = w.parentWidget()
            inside_view = False
            while parent is not None and parent is not root:
                if isinstance(parent, (QAbstractItemView, QComboBox)):
                    inside_view = True          # 표 안의 편집 칸 등은 표가 맡는다
                    break
                parent = parent.parentWidget()
            if inside_view:
                continue
            if inputs_only and (w.objectName() == "AddRow" or w in (self._cancel, self._done)):
                continue
            out.append(w)
        out.sort(key=lambda w: (w.mapTo(self, QPoint(0, 0)).y(), w.mapTo(self, QPoint(0, 0)).x()))
        return out

    @staticmethod
    def _member(chain: list[QWidget], widget: QWidget | None) -> int:
        while widget is not None:
            if widget in chain:
                return chain.index(widget)
            widget = widget.parentWidget()
        return -1

    def focusNextPrevChild(self, next_: bool) -> bool:  # noqa: N802
        chain = self._focus_chain()
        if not chain:
            return True
        at = self._member(chain, QApplication.focusWidget())
        step = 1 if next_ else -1
        target = chain[(at + step) % len(chain)] if at >= 0 else chain[0 if next_ else -1]
        target.setFocus(Qt.TabFocusReason if next_ else Qt.BacktabFocusReason)
        return True

    def _next_input(self, after: QWidget | None) -> None:
        """Enter: 다음 입력 칸으로. 더 없으면 설정을 마친다."""
        chain = self._focus_chain(inputs_only=True)
        # 지금 칸(목록이면 그 목록의 마지막 줄) 다음 칸.
        inside = [i for i, w in enumerate(chain)
                  if after is not None and (w is after or after.isAncestorOf(w))]
        if not inside and after is not None:
            at = self._member(chain, after)
            inside = [at] if at >= 0 else []
        later = chain[max(inside) + 1:] if inside else []
        if later:
            later[0].setFocus(Qt.TabFocusReason)
        elif self._panel_wanted:
            self._apply_setup()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        focus = QApplication.focusWidget()
        event.accept()                  # 위젯 안에서 남은 글쇠는 본문으로 보내지 않는다
        if not self._panel_wanted:
            return
        if key == Qt.Key_Escape:
            self._cancel_setup()
            return
        if key not in (Qt.Key_Return, Qt.Key_Enter):
            return
        if event.modifiers() & Qt.ControlModifier:
            self._apply_setup()
            return
        if isinstance(focus, (QPushButton, QToolButton)):
            focus.click()               # 단추 위의 Enter 는 그 단추
            return
        self._next_input(focus)

    # ── 메뉴 ──

    def _on_local(self, action: dict, rect) -> None:
        name = action.get("local")
        if name == "setup":
            self.open_setup(False)
        elif name == "menu":
            at = rect.bottomLeft() + QPoint(0, 4) if rect is not None else QPoint(0, 0)
            self._show_menu(self.editor.canvas.mapToGlobal(at))
        elif name == "input" and rect is not None:
            self.open_input(action, rect)

    def open_input(self, action: dict, rect) -> QLineEdit:
        """판의 '+ 추가' 자리에 한 줄 입력 칸을 띄운다. Enter 로 보내고 Esc 나 다른 곳을
        누르면 닫는다."""
        self.close_input()
        line = QLineEdit(self.editor.canvas)
        line.setObjectName("WidgetInput")
        line.setMaxLength(200)
        hint = action.get("hint")
        if isinstance(hint, str):
            line.setPlaceholderText(wtexts.tr(hint))
        line.setStyleSheet(
            "QLineEdit#WidgetInput { min-height: 18px; max-height: 18px; padding: 4px 8px;"
            " font-size: 12px; border-radius: 6px; }")
        width = max(160, min(self.editor.canvas.width() - rect.x() - 14, 260))
        line.setGeometry(rect.x(), rect.y(), width, rect.height())
        purpose = action.get("purpose", "")

        def submit() -> None:
            op = self.kind.input_op(self.data(), purpose, line.text(), self.viewer)
            self.close_input()
            if op is not None:
                self.opRequested.emit(op)

        line.returnPressed.connect(submit)
        line.installEventFilter(self)
        line.show()
        line.setFocus()
        self._input = line
        return line

    def close_input(self) -> None:
        line = getattr(self, "_input", None)
        self._input = None
        if line is not None:
            line.removeEventFilter(self)
            line.hide()
            line.deleteLater()

    def build_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.setAttribute(Qt.WA_NoMouseReplay)
        menu.setStyleSheet(ALIGNED_MENU_QSS)
        if self.can_configure():
            menu.addAction(svg_icon("edit", color="#3A3A3A"), i18n.t("widget.edit"),
                           lambda: self.open_setup(False))
        size = QMenu(i18n.t("widget.size"), menu)
        size.setStyleSheet(ALIGNED_MENU_QSS)
        current = int(self.data().get("w") or 0)
        for key, px in SIZES:
            act = size.addAction(i18n.t(key))
            act.setCheckable(True)
            act.setChecked(px == current)
            act.triggered.connect(lambda _c=False, w=px: self.opRequested.emit(
                {"op": "set_size", "w": w}))
        menu.addMenu(size)
        for title, choices in self.kind.menu_choices(self.data()):
            sub = QMenu(title, menu)
            sub.setStyleSheet(ALIGNED_MENU_QSS)
            for label, op, on in choices:
                act = sub.addAction(label)
                act.setCheckable(True)
                act.setChecked(bool(on))
                act.triggered.connect(lambda _c=False, o=op: self.opRequested.emit(dict(o)))
            menu.addMenu(sub)
        if getattr(self.kind, "bg_option", False):
            bg = QMenu(wtexts.tr("bg.title"), menu)
            bg.setStyleSheet(ALIGNED_MENU_QSS)
            for value, key in (("white", "bg.white"), ("clear", "bg.clear")):
                act = bg.addAction(wtexts.tr(key))
                act.setCheckable(True)
                act.setChecked(self.data().get("bg", "white") == value)
                act.triggered.connect(lambda _c=False, b=value: self.opRequested.emit(
                    {"op": "set_bg", "bg": b}))
            menu.addMenu(bg)
        if self.can_delete:
            menu.addSeparator()
            menu.addAction(svg_icon("trash", color="#3A3A3A"), i18n.t("widget.delete"),
                           self.deleteRequested.emit)
        return menu

    def _context_menu(self, pos: QPoint, at: QPoint) -> None:
        """오른쪽 단추: 그 자리의 일(달력의 쉬는 날 등) 다음에 위젯 메뉴."""
        if self._panel_wanted:
            return
        items = ([] if self.editor.readonly
                 else self.kind.context_actions(self.editor, pos.x(), pos.y()))
        menu = self.build_menu()
        first = menu.actions()[0] if menu.actions() else None
        if items:
            for label, action, checked in items:
                if action is None:
                    menu.insertSeparator(first)
                    continue
                act = QAction(label, menu)
                if checked is not None:
                    act.setCheckable(True)
                    act.setChecked(bool(checked))
                act.triggered.connect(lambda _c=False, a=action: self.editor.trigger(a))
                menu.insertAction(first, act)
            menu.insertSeparator(first)
        menu.exec(at)
        menu.deleteLater()

    def _show_menu(self, at: QPoint) -> None:
        menu = self.build_menu()
        self.editor.view["menu_open"] = True
        self.editor.canvas.update()
        try:
            menu.exec(at)
        finally:
            self.editor.view["menu_open"] = False
            self.editor.view["hover"] = None
            self.editor.canvas.update()
            menu.deleteLater()

    # ── 마우스: 오른쪽 가장자리로 폭 바꾸기 ──

    def _on_edge(self, x: float) -> bool:
        return not self.editor.readonly and self.width() - EDGE <= x <= self.width() + 1

    # ── 끌어서 크기 바꾸기(모서리 손잡이와 오른쪽 가장자리) ──

    def _sized_height(self) -> bool:
        return bool(getattr(self.kind, "sized_height", False)) and not self._panel_wanted

    def begin_resize(self) -> None:
        self._drag_from = (0, self.width())
        self.editor.view["resizing"] = True
        self.editor.canvas.update()

    def resize_to(self, width: int, height: int | None = None) -> None:
        width = max(DRAW_MIN, min(MAX_WIDTH, int(width)))
        changed = width != self.preview_width
        self.preview_width = width
        if height is not None and self._sized_height():
            height = max(160, min(1200, int(height)))
            if height != self.preview_height:
                self.preview_height = height
                self.editor.view["preview_h"] = height
                self.editor.canvas.refresh()
                changed = True
        if changed:
            self.sizeChanged.emit()

    def end_resize(self) -> None:
        self._drag_from = None
        width = self.preview_width or self.width()
        op = {"op": "set_size", "w": int(round(width / 10) * 10)}
        if self.preview_height:
            op["h"] = int(round(self.preview_height / 10) * 10)
        self.preview_width = 0
        self.preview_height = 0
        self.editor.view["resizing"] = False
        self.editor.view.pop("preview_h", None)
        self.editor.canvas.update()
        self.opRequested.emit(op)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        g = self._grip
        g.move(self.width() - g.width() - 3, self.height() - g.height() - 3)
        g.raise_()
        g.setCursor(Qt.SizeFDiagCursor if self._sized_height() else Qt.SizeHorCursor)

    def enterEvent(self, event) -> None:  # noqa: N802
        if not self.editor.readonly:
            self._grip.show()
            self._grip.raise_()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self._drag_from is None:
            self._grip.hide()
        super().leaveEvent(event)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is getattr(self, "_input", None):
            if event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape:
                self.close_input()
                return True
            if event.type() == QEvent.FocusOut:
                self.close_input()
            return False
        if event.type() == QEvent.Show and obj in self.editor.extras.values():
            obj.hide()
            return False
        if obj not in (self.editor, self.editor.canvas, self.panel):
            return super().eventFilter(obj, event)
        t = event.type()
        if t == QEvent.ContextMenu and obj is self.editor.canvas:
            self._context_menu(event.pos(), event.globalPos())
            return True
        if t in (QEvent.MouseMove, QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
            pos = obj.mapTo(self, event.position().toPoint())
            if t == QEvent.MouseMove:
                if self._drag_from is not None and self._edge_start is not None:
                    start_x, start_w = self._edge_start
                    self.resize_to(start_w + pos.x() - start_x)
                    return True
                if self._on_edge(pos.x()):
                    obj.setCursor(Qt.SizeHorCursor)
                    return True
                if obj is self.panel:
                    obj.unsetCursor()
                return False
            if t == QEvent.MouseButtonPress and event.button() == Qt.LeftButton \
                    and self._on_edge(pos.x()):
                self._edge_start = (pos.x(), self.width())
                self.begin_resize()
                return True
            if t == QEvent.MouseButtonRelease and self._edge_start is not None:
                self._edge_start = None
                self.end_resize()
                return True
        return super().eventFilter(obj, event)


__all__ = ["SIZES", "WidgetBlock", "has_form", "wants_setup"]
