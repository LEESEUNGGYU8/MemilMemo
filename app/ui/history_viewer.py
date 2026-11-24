from __future__ import annotations

import difflib
import html
import re
from datetime import datetime
from typing import Any

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .. import datetime_fmt, i18n
from . import confirm, theme
from .rich_text import has_non_checkbox_image, html_to_plain, is_marker_url
from .safe_text import SafeTextDocument, safe_color


_IMG_SRC_RE = re.compile(r'<img[^>]*src=["\']([^"\']+)["\']', re.IGNORECASE)


def _count_content_images(html_str: str | None) -> int:
    if not html_str:
        return 0
    return sum(1 for m in _IMG_SRC_RE.finditer(html_str)
               if not is_marker_url(m.group(1)))


_ACTION_COLOR = {
    "create": ("#E6F5EC", "#15803D"),
    "update": ("#E6EEFB", "#1D4ED8"),
    "delete": ("#FCE3E7", "#B0122C"),
    # 휴지통에서 되살린 기록(1.0.4 부터).
    "restore": ("#E6F5EC", "#15803D"),
}


def _action_label(action: str) -> str:
    return i18n.t(f"history.action_{action}") if action in _ACTION_COLOR else action


def _fmt_ts(ms: int) -> str:
    if not ms:
        return ""
    return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M:%S")


def _fmt_relative(ms: int) -> str:
    return datetime_fmt.fmt_relative(ms)


def _render_inline_diff(before: str | None, after: str | None) -> str:
    a = html_to_plain(before or "")
    b = html_to_plain(after or "")
    img_delta = _count_content_images(after) - _count_content_images(before)
    img_note = ""
    if img_delta > 0:
        img_note = i18n.t("history.image_added", n=img_delta)
    elif img_delta < 0:
        img_note = i18n.t("history.image_removed", n=-img_delta)
    note_html = ""
    if img_note:
        note_html = (
            '<div style="margin-bottom:8px; padding:4px 10px; '
            'background:#EAF6EE; color:#0E5E2B; border-radius:6px; '
            'font-weight:600;">' + html.escape(img_note) + '</div>')
    if a == b:
        if note_html:
            return note_html
        return ('<div style="color:#8A8A8A; font-style:italic;">'
                + i18n.t("history.no_body_change") + '</div>')

    def tok(s: str) -> list[str]:
        out: list[str] = []
        buf = ""
        for ch in s:
            if ch.isalnum():
                buf += ch
            else:
                if buf:
                    out.append(buf)
                    buf = ""
                out.append(ch)
        if buf:
            out.append(buf)
        return out

    a_toks = tok(a)
    b_toks = tok(b)
    sm = difflib.SequenceMatcher(None, a_toks, b_toks, autojunk=False)
    parts: list[str] = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            parts.append(html.escape("".join(a_toks[i1:i2]))
                          .replace("\n", "<br/>"))
        elif op == "delete":
            seg = html.escape("".join(a_toks[i1:i2])).replace("\n", "<br/>")
            parts.append(
                f'<span style="background:#FFE0E4; color:#8A0014; '
                f'text-decoration:line-through; border-radius:3px; padding:0 2px;">{seg}</span>')
        elif op == "insert":
            seg = html.escape("".join(b_toks[j1:j2])).replace("\n", "<br/>")
            parts.append(
                f'<span style="background:#DFF5E1; color:#0E5E2B; '
                f'border-radius:3px; padding:0 2px;">{seg}</span>')
        elif op == "replace":
            del_seg = html.escape("".join(a_toks[i1:i2])).replace("\n", "<br/>")
            ins_seg = html.escape("".join(b_toks[j1:j2])).replace("\n", "<br/>")
            parts.append(
                f'<span style="background:#FFE0E4; color:#8A0014; '
                f'text-decoration:line-through; border-radius:3px; padding:0 2px;">{del_seg}</span>'
                f'<span style="background:#DFF5E1; color:#0E5E2B; '
                f'border-radius:3px; padding:0 2px;">{ins_seg}</span>')
    return note_html + (
        f'<div style="font-family: {theme.CONTENT_FONT_STACK}; '
        'font-size: 13px; line-height: 1.7; color:#1F1F1F; '
        'white-space: pre-wrap;">' + "".join(parts) + "</div>")


def _render_plain(content: str | None) -> str:
    """지워진 메모의 본문을 그대로 보인다(지운 글 표시 없이 읽을 수 있게)."""
    text = html_to_plain(content or "")
    images = _count_content_images(content)
    note_html = ""
    if images:
        note_html = (
            '<div style="margin-bottom:8px; padding:4px 10px; '
            'background:#F1F1F1; color:#555555; border-radius:6px; '
            'font-weight:600;">'
            + html.escape(i18n.t("history.image_count", n=images)) + '</div>')
    if not text.strip():
        return note_html or (
            '<div style="color:#8A8A8A; font-style:italic;">'
            + i18n.t("history.empty_note") + '</div>')
    return note_html + (
        f'<div style="font-family: {theme.CONTENT_FONT_STACK}; '
        'font-size: 13px; line-height: 1.7; color:#1F1F1F; '
        'white-space: pre-wrap;">'
        + html.escape(text).replace("\n", "<br/>") + "</div>")


def _title_of(content: str | None, limit: int = 40) -> str:
    """메모를 가리키는 이름: 첫 줄, 없으면 내용의 종류."""
    raw = content or ""
    first = (html_to_plain(raw).splitlines() or [""])[0].strip()
    if not first:
        first = (i18n.t("main.note_title_image") if has_non_checkbox_image(raw)
                 else i18n.t("history.empty_note"))
    return first[:limit] + ("…" if len(first) > limit else "")


# 여러 메모의 이력을 섞어 보일 때 줄에 적는 메모 이름.
_TITLE_ROLE = Qt.UserRole + 1

_SCOPE_QSS = """
QFrame#Scope { background: #F1F1F1; border-radius: 9px; }
QFrame#Scope QPushButton {
    background: transparent; color: #6B6B6B; border: none; border-radius: 7px;
    padding: 5px 14px; min-height: 18px; min-width: 0;
    font-size: 12px; font-weight: 600;
}
QFrame#Scope QPushButton:hover { background: transparent; color: #1F1F1F; }
QFrame#Scope QPushButton:checked { background: #FFFFFF; color: #1F1F1F; }
QFrame#Scope QPushButton:disabled { background: transparent; color: #C4C4C4; }
"""


class _HistoryItemDelegate(QStyledItemDelegate):
    """선택·호버 배경을 직접 그린다. 그렇지 않으면 기본 배경이 행 요소를 가린다."""

    ROW_HEIGHT = 62

    def sizeHint(self, option, index) -> QSize:
        return QSize(0, self.ROW_HEIGHT)

    def paint(self, painter: QPainter, option, index) -> None:
        entry = index.data(Qt.UserRole)
        if not entry:
            super().paint(painter, option, index)
            return

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)

        is_selected = bool(option.state & QStyle.StateFlag.State_Selected)
        is_hover = bool(option.state & QStyle.StateFlag.State_MouseOver)

        rect = option.rect.adjusted(6, 3, -6, -3)

        if is_selected:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#1F1F1F"))
            painter.drawRoundedRect(rect, 9, 9)
        elif is_hover:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#F1F1F1"))
            painter.drawRoundedRect(rect, 9, 9)

        action = entry.get("action", "")
        bg, fg = _ACTION_COLOR.get(action, ("#ECECEC", "#555555"))
        label = _action_label(action)

        font_badge = QFont(option.font)
        font_badge.setBold(True)
        font_badge.setPointSizeF(9.0)
        painter.setFont(font_badge)
        # 글자 폭에 맞춘다. 고정 42px 에는 영어 낱말이 들어가지 않았다.
        badge_w = max(42, painter.fontMetrics().horizontalAdvance(label) + 16)
        badge_rect = QRect(rect.x() + 10, rect.y() + 10, badge_w, 20)
        painter.setBrush(QColor(bg))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(badge_rect, 6, 6)
        painter.setPen(QColor(fg))
        painter.drawText(badge_rect, Qt.AlignCenter, label)

        # 한 메모의 이력만 볼 때는 첫 줄에 고친 사람을, 여러 메모를 섞어 볼
        # 때는 첫 줄에 메모 이름을 적고 고친 사람은 둘째 줄로 내린다.
        title = index.data(_TITLE_ROLE) or ""
        user = entry.get("user", "")
        when = _fmt_relative(entry.get("session_end", 0))

        font_user = QFont(option.font)
        font_user.setBold(True)
        font_user.setPointSizeF(10.5)
        painter.setFont(font_user)
        painter.setPen(QColor("#FFFFFF" if is_selected else "#1F1F1F"))
        user_x = badge_rect.right() + 10
        user_rect = QRect(user_x, rect.y() + 10, rect.right() - user_x - 8, 22)
        first = title or user
        painter.drawText(
            user_rect, Qt.AlignVCenter | Qt.AlignLeft,
            painter.fontMetrics().elidedText(first, Qt.ElideRight,
                                             user_rect.width()))

        font_ts = QFont(option.font)
        font_ts.setPointSizeF(9.5)
        painter.setFont(font_ts)
        painter.setPen(QColor("#B4B4B4" if is_selected else "#6B6B6B"))
        ts_rect = QRect(rect.x() + 12, rect.y() + 34, rect.width() - 20, 18)
        second = f"{user}   {when}" if title else when
        painter.drawText(
            ts_rect, Qt.AlignVCenter | Qt.AlignLeft,
            painter.fontMetrics().elidedText(second, Qt.ElideRight,
                                             ts_rect.width()))

        painter.restore()


class HistoryViewer(QDialog):
    # (note_id, content_after)
    restoreRequested = Signal(str, str)
    # 삭제된 메모를 새 메모로 되살린다: (content, color)
    reviveRequested = Signal(str, str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(i18n.t("history.window_title"))
        self.resize(940, 580)
        self.setMinimumSize(740, 420)

        self._entries: list[dict[str, Any]] = []
        self._notes: dict[str, dict[str, Any]] = {}
        # 삭제된 메모: id -> 지워질 때의 본문과 색. 새로 지워진 것부터다.
        self._gone: dict[str, dict[str, Any]] = {}
        self._titles: dict[str, str] = {}
        self._show_gone = False
        self._current_entry: dict[str, Any] | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        top = QHBoxLayout()
        top.setSpacing(10)
        # 지금 있는 메모의 이력과 삭제된 메모의 이력을 나눠 본다.
        scope = QFrame()
        scope.setObjectName("Scope")
        scope.setStyleSheet(_SCOPE_QSS)
        scope_h = QHBoxLayout(scope)
        scope_h.setContentsMargins(3, 3, 3, 3)
        scope_h.setSpacing(2)
        self._scope = QButtonGroup(self)
        self._live_btn = QPushButton(i18n.t("history.tab_live"))
        self._gone_btn = QPushButton(i18n.t("history.tab_deleted"))
        for i, btn in enumerate((self._live_btn, self._gone_btn)):
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFocusPolicy(Qt.TabFocus)
            self._scope.addButton(btn, i)
            scope_h.addWidget(btn)
        self._live_btn.setChecked(True)
        self._gone_btn.setEnabled(False)
        self._scope.idClicked.connect(lambda i: self._set_scope(bool(i)))
        top.addWidget(scope)
        self._note_combo = QComboBox()
        self._note_combo.addItem(i18n.t("history.all"), userData=None)
        self._note_combo.currentIndexChanged.connect(self._refresh_list)
        top.addWidget(self._note_combo, 1)
        root.addLayout(top)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(10)

        left = QFrame()
        left.setFrameShape(QFrame.NoFrame)
        left_v = QVBoxLayout(left)
        left_v.setContentsMargins(0, 0, 0, 0)
        left_v.setSpacing(0)
        self._list = QListWidget()
        self._list.setItemDelegate(_HistoryItemDelegate(self._list))
        self._list.setVerticalScrollBar(theme.RoundedScrollBar())
        self._list.setMouseTracking(True)
        self._list.setUniformItemSizes(True)
        self._list.currentItemChanged.connect(self._on_select)
        self._list.setStyleSheet(
            "QListWidget { background:#FFFFFF; border:1px solid #EAEAEA; "
            "border-radius: 10px; padding: 4px; }"
            "QListWidget::item { padding: 0; margin: 0; background: transparent; }"
            "QListWidget::item:selected { background: transparent; color: #1F1F1F; }"
            "QListWidget::item:hover { background: transparent; }"
        )
        left_v.addWidget(self._list)
        splitter.addWidget(left)

        right = QFrame()
        right.setFrameShape(QFrame.NoFrame)
        right_v = QVBoxLayout(right)
        right_v.setContentsMargins(16, 0, 0, 0)
        right_v.setSpacing(10)

        self._header = QLabel(i18n.t("history.select_prompt"))
        # 고친 사람의 닉네임이 들어간다. 글자 그대로 보인다.
        self._header.setTextFormat(Qt.PlainText)
        self._header.setStyleSheet(
            "font-size: 16px; font-weight: 700; color: #1F1F1F;")
        right_v.addWidget(self._header)

        self._meta = QLabel("")
        self._meta.setStyleSheet("color:#6B6B6B; font-size: 12px;")
        self._meta.setWordWrap(True)
        self._meta.setTextFormat(Qt.RichText)
        right_v.addWidget(self._meta)

        self._color_row = QLabel("")
        self._color_row.setStyleSheet("font-size: 12px;")
        self._color_row.setTextFormat(Qt.RichText)
        self._color_row.setVisible(False)
        right_v.addWidget(self._color_row)

        self._section = QLabel(i18n.t("history.section_body"))
        self._section.setProperty("role", "sectionHeader")
        self._section.setStyleSheet(
            "font-size: 12px; font-weight: 700; color:#1F1F1F; margin-top: 4px;")
        right_v.addWidget(self._section)

        self._detail = QTextBrowser()
        self._detail.setDocument(SafeTextDocument(self._detail))
        self._detail.setOpenExternalLinks(False)
        self._detail.setStyleSheet(
            "QTextBrowser { background:#FAFAFA; border:1px solid #EAEAEA; "
            "border-radius: 10px; padding: 12px; font-size: 13px; "
            f"font-family: {theme.CONTENT_FONT_STACK}; }}"
        )
        right_v.addWidget(self._detail, 1)

        restore_row = QHBoxLayout()
        restore_row.addStretch(1)
        self._restore_btn = QPushButton(i18n.t("history.btn_restore"))
        self._restore_btn.setCursor(Qt.PointingHandCursor)
        self._restore_btn.setProperty("secondary", True)
        self._restore_btn.setEnabled(False)
        self._restore_btn.clicked.connect(self._on_restore_clicked)
        restore_row.addWidget(self._restore_btn)
        right_v.addLayout(restore_row)

        splitter.addWidget(right)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([300, 620])
        root.addWidget(splitter, 1)

    def set_notes(self, notes: list[dict[str, Any]]) -> None:
        self._notes = {n["id"]: n for n in notes}
        self._titles = {n["id"]: _title_of(n.get("content")) for n in notes}
        self._rebuild()

    def set_entries(self, entries: list[dict[str, Any]]) -> None:
        self._entries = sorted(
            (e for e in entries if isinstance(e, dict)),
            key=lambda e: e.get("session_end", 0), reverse=True)
        self._rebuild()

    def append_entry(self, entry: dict[str, Any]) -> None:
        self._entries.insert(0, entry)
        self._rebuild()

    def select_note(self, note_id: str) -> None:
        """그 메모의 이력만 보인다(목록의 메모에서 이력을 열었을 때)."""
        self._set_scope(note_id in self._gone)
        idx = self._note_combo.findData(note_id)
        if idx >= 0:
            self._note_combo.setCurrentIndex(idx)

    def _set_scope(self, gone: bool) -> None:
        gone = gone and bool(self._gone)
        self._show_gone = gone
        (self._gone_btn if gone else self._live_btn).setChecked(True)
        self._fill_combo(None)
        self._refresh_list()

    def _rebuild(self) -> None:
        """받은 이력에서 삭제된 메모를 가려내고 화면을 다시 채운다."""
        gone: dict[str, dict[str, Any]] = {}
        for e in self._entries:          # 새것부터
            nid = e.get("note_id")
            if (not nid or nid in self._notes or nid in gone
                    or e.get("action") != "delete"):
                continue
            content = e.get("content_before")
            if content is None:
                content = e.get("content_after")
            gone[nid] = {"content": content or "",
                         "color": e.get("color_before") or e.get("color_after")
                         or ""}
            if nid not in self._titles:
                self._titles[nid] = _title_of(content)
        self._gone = gone
        self._gone_btn.setEnabled(bool(gone))
        if self._show_gone and not gone:
            self._show_gone = False
        (self._gone_btn if self._show_gone else self._live_btn).setChecked(True)
        self._fill_combo(self._note_combo.currentData())
        self._refresh_list()

    def _fill_combo(self, keep: str | None) -> None:
        ids = list(self._gone) if self._show_gone else list(self._notes)
        self._note_combo.blockSignals(True)
        self._note_combo.clear()
        self._note_combo.addItem(i18n.t("history.all"), userData=None)
        for nid in ids:
            self._note_combo.addItem(self._titles.get(nid, ""), userData=nid)
        idx = self._note_combo.findData(keep) if keep is not None else 0
        self._note_combo.setCurrentIndex(max(0, idx))
        self._note_combo.blockSignals(False)

    def _current_note_filter(self) -> str | None:
        return self._note_combo.currentData()

    def _refresh_list(self) -> None:
        keep = self._current_entry
        self._list.clear()
        note_id = self._current_note_filter()
        for e in self._entries:
            nid = e.get("note_id")
            if (nid in self._gone) != self._show_gone:
                continue
            if note_id is not None and nid != note_id:
                continue
            item = QListWidgetItem()
            item.setData(Qt.UserRole, e)
            if note_id is None:
                if nid not in self._titles:
                    self._titles[nid] = _title_of(
                        e.get("content_after") or e.get("content_before"))
                item.setData(_TITLE_ROLE, self._titles[nid])
            self._list.addItem(item)
            if keep is not None and e is keep:
                self._list.setCurrentItem(item)

    def _on_select(self, current: QListWidgetItem | None, _prev) -> None:
        if current is None:
            self._header.setText(i18n.t("history.select_prompt"))
            self._meta.setText("")
            self._section.setText(i18n.t("history.section_body"))
            self._color_row.setVisible(False)
            self._detail.clear()
            self._current_entry = None
            self._restore_btn.setEnabled(False)
            self._restore_btn.setToolTip("")
            return
        e = current.data(Qt.UserRole)
        self._current_entry = e
        action = e.get("action", "")
        label = _action_label(action)
        user = e.get("user", "")
        self._header.setText(
            i18n.t(f"history.by_{action}", user=user) if action in _ACTION_COLOR
            else f"{label} {user}")
        start = _fmt_ts(e.get("session_start", 0))
        end = _fmt_ts(e.get("session_end", 0))
        self._meta.setText(
            i18n.t("history.meta", start=start, end=end)
        )
        cb, ca = e.get("color_before"), e.get("color_after")
        # 삭제 항목에는 뒤의 색이 없다. 색이 바뀐 것처럼 보이지 않게 한다.
        if action != "delete" and cb != ca and (cb or ca):
            # 색 값도 남이 보낸 글자다. 색 꼴일 때만 서식 안에 넣는다.
            self._color_row.setText(
                i18n.t("history.color_change") + " "
                f"<span style='display:inline-block; background:{safe_color(cb)}; "
                f"padding: 0 10px; border-radius:3px;'>&nbsp;</span> "
                f"<b>→</b> "
                f"<span style='display:inline-block; background:{safe_color(ca)}; "
                f"padding: 0 10px; border-radius:3px;'>&nbsp;</span>"
            )
            self._color_row.setVisible(True)
        else:
            self._color_row.setVisible(False)
        if action == "delete":
            # 지워질 때의 본문이다. 지운 글 표시 없이 그대로 보인다.
            self._section.setText(i18n.t("history.section_deleted"))
            self._detail.setHtml(_render_plain(e.get("content_before")))
        else:
            self._section.setText(i18n.t("history.section_body"))
            self._detail.setHtml(_render_inline_diff(
                e.get("content_before"), e.get("content_after")))

        note_id = e.get("note_id")
        if note_id in self._gone:
            # 삭제된 메모는 그 자리에 되돌릴 수 없다(서버가 받지 않는다). 그
            # 시점의 본문으로 새 메모를 만든다.
            self._restore_btn.setText(i18n.t("history.btn_revive"))
            self._restore_btn.setEnabled(self._entry_content(e) is not None)
            self._restore_btn.setToolTip("")
            return
        self._restore_btn.setText(i18n.t("history.btn_restore"))
        can_restore = (
            action != "delete"
            and e.get("content_after") is not None
            and note_id in self._notes
        )
        self._restore_btn.setEnabled(can_restore)
        if can_restore:
            self._restore_btn.setToolTip(
                i18n.t("history.tip_restore"))
        elif note_id not in self._notes:
            self._restore_btn.setToolTip(
                i18n.t("history.tip_gone"))
        else:
            self._restore_btn.setToolTip(
                i18n.t("history.tip_no_content"))

    @staticmethod
    def _entry_content(e: dict[str, Any]) -> str | None:
        """그 항목이 가리키는 시점의 본문. 삭제 항목은 지워지기 직전의 본문이다."""
        if e.get("action") == "delete":
            return e.get("content_before")
        return e.get("content_after")

    def _on_restore_clicked(self) -> None:
        e = self._current_entry
        if e is None:
            return
        note_id = e.get("note_id")
        when = _fmt_ts(e.get("session_end", 0))
        user = e.get("user") or ""
        if note_id in self._gone:
            content = self._entry_content(e)
            if content is None:
                return
            if confirm.ask(
                    self, i18n.t("history.revive_title"),
                    i18n.t("history.revive_body", when=when, user=user),
                    action=i18n.t("history.revive_action")):
                color = (e.get("color_after") or e.get("color_before")
                         or self._gone[note_id].get("color") or "")
                self.reviveRequested.emit(content, str(color))
            return
        content = e.get("content_after")
        if not note_id or content is None:
            return
        if not confirm.ask(
                self, i18n.t("history.confirm_title"),
                i18n.t("history.confirm_body", when=when, user=user),
                action=i18n.t("history.confirm_action")):
            return
        self.restoreRequested.emit(note_id, content)
