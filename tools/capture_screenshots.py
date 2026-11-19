"""소개 페이지용 스크린샷 자동 촬영.

격리된 임시 데이터 폴더에 데모 메모를 심고 앱을 호스트로 띄운 뒤, 창마다
``QWidget.grab()`` 으로 찍어 PNG 로 저장한다. 실제 화면을 캡처하지 않으므로
사용자의 바탕화면이나 운영 데이터(%APPDATA%\\SyncNotes)는 찍히지도 바뀌지도
않는다. 실행 중인 SyncNotes 가 있어도 함께 돌릴 수 있다(다른 포트, 다른 폴더).

사용법::

    python tools/capture_screenshots.py                 # docs/images 에 전부
    python tools/capture_screenshots.py --out etc/shots --set raw
    python tools/capture_screenshots.py --platform native   # 실제 창으로(잠깐 화면에 뜬다)

``--set``
    raw    창마다 한 장씩(진단·검수용).
    readme 소개 페이지에 쓰는 합성 이미지와 GIF.
    all    둘 다(기본).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

HOST = "지우"


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=str(ROOT / "docs" / "images"))
    ap.add_argument("--set", dest="which", default="all",
                    choices=("raw", "readme", "all"))
    ap.add_argument("--lang", default="ko", choices=("ko", "en", "ja"))
    ap.add_argument("--platform", default="offscreen",
                    choices=("offscreen", "native"))
    ap.add_argument("--port", type=int, default=5611,
                    help="데모 호스트가 쓸 포트(실행 중인 앱과 겹치지 않게).")
    ap.add_argument("--keep", action="store_true",
                    help="임시 데이터 폴더를 지우지 않는다(디버그용).")
    return ap.parse_args()


ARGS = _parse_args()
PORT = ARGS.port
OUT = Path(ARGS.out).resolve()
OUT.mkdir(parents=True, exist_ok=True)

# 앱 모듈을 들이기 전에 데이터 폴더와 화면 플랫폼을 정한다.
DATA_ROOT = Path(tempfile.mkdtemp(prefix="syncnotes-shots-"))
os.environ["APPDATA"] = str(DATA_ROOT)
if ARGS.platform == "offscreen":
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QColor, QFont, QFontDatabase, QImage, QPainter, QPainterPath, QPen,
)
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QTabWidget, QWidget  # noqa: E402

import main as app_main  # noqa: E402
from app import config  # noqa: E402

assert str(config.default_db_path()).startswith(str(DATA_ROOT)), (
    "데이터 폴더가 격리되지 않았습니다.")


# ── 데모 데이터 ──────────────────────────────────────────────────────────

NOW_MS = int(time.time() * 1000)
MIN = 60 * 1000
HOUR = 60 * MIN
DAY = 24 * HOUR

FOLDERS = [
    # id, 이름, 색, 나만 보기
    ("f_work", "업무", "#BAE1FF", False),
    ("f_store", "매장 리뉴얼", "#BAFFC9", False),
    ("f_me", "개인", "#E0BBE4", True),
]


def _font(px: int, bold: bool = False) -> QFont:
    f = QFont("Pretendard Variable")
    f.setPixelSize(px)
    f.setBold(bold)
    return f


def _draw_floor_plan() -> QImage:
    """매장 배치 시안처럼 보이는 그림(외부 자료 없이 직접 그린다)."""
    w, h = 960, 600
    img = QImage(w, h, QImage.Format_ARGB32)
    img.fill(QColor("#FBFAF6"))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor("#2F3A4A"), 6))
    p.setBrush(QColor("#FFFFFF"))
    p.drawRoundedRect(QRectF(40, 40, w - 80, h - 80), 10, 10)
    # 입구
    p.setPen(QPen(QColor("#FBFAF6"), 10))
    p.drawLine(QPointF(400, h - 40), QPointF(560, h - 40))
    p.setPen(QPen(QColor("#2F3A4A"), 3))
    p.setFont(_font(22, True))
    p.drawText(QRectF(400, h - 36, 160, 30), Qt.AlignCenter, "입구")
    blocks = [
        (90, 90, 250, 90, "#BAE1FF", "계산대"),
        (90, 230, 120, 250, "#FFE066", "진열 A"),
        (250, 230, 120, 250, "#FFE066", "진열 B"),
        (420, 90, 230, 150, "#BAFFC9", "신상품"),
        (420, 290, 230, 130, "#FFD9A6", "행사 매대"),
        (700, 90, 180, 330, "#E0BBE4", "창고"),
    ]
    for x, y, bw, bh, color, label in blocks:
        p.setPen(QPen(QColor("#2F3A4A"), 3))
        p.setBrush(QColor(color))
        p.drawRoundedRect(QRectF(x, y, bw, bh), 8, 8)
        p.setFont(_font(24, True))
        p.setPen(QColor("#1F2937"))
        p.drawText(QRectF(x, y, bw, bh), Qt.AlignCenter, label)
    # 동선 화살표
    pen = QPen(QColor("#D64545"), 5, Qt.DashLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    path = QPainterPath(QPointF(480, h - 70))
    path.cubicTo(QPointF(480, 470), QPointF(390, 470), QPointF(390, 360))
    path.cubicTo(QPointF(390, 260), QPointF(400, 250), QPointF(410, 200))
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)
    p.end()
    return img


def _draw_chart() -> QImage:
    """요일별 방문객 막대 그래프."""
    w, h = 900, 520
    img = QImage(w, h, QImage.Format_ARGB32)
    img.fill(QColor("#FFFFFF"))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setFont(_font(30, True))
    p.setPen(QColor("#111827"))
    p.drawText(QRectF(50, 26, w - 100, 50), Qt.AlignLeft | Qt.AlignVCenter,
               "요일별 방문객")
    days = ["월", "화", "수", "목", "금", "토", "일"]
    vals = [84, 92, 78, 105, 131, 188, 162]
    base_y, top = h - 80, 110
    bar_w, gap = 76, 40
    x = 70
    p.setPen(QPen(QColor("#E5E7EB"), 2))
    for i in range(4):
        y = base_y - (base_y - top) * i / 3
        p.drawLine(QPointF(50, y), QPointF(w - 40, y))
    for d, v in zip(days, vals):
        bh = (base_y - top) * v / 200
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#2563EB" if v < 150 else "#F59E0B"))
        p.drawRoundedRect(QRectF(x, base_y - bh, bar_w, bh), 8, 8)
        p.setPen(QColor("#374151"))
        p.setFont(_font(24, True))
        p.drawText(QRectF(x, base_y + 8, bar_w, 36), Qt.AlignCenter, d)
        p.setFont(_font(22))
        p.drawText(QRectF(x - 10, base_y - bh - 36, bar_w + 20, 30),
                   Qt.AlignCenter, str(v))
        x += bar_w + gap
    p.end()
    return img


def _notes() -> list[dict]:
    from app.ui.rich_text import bullet_url_for, checkbox_url, image_to_data_url

    def chk(done: bool, text: str) -> str:
        style = ' style="text-decoration: line-through; color:#6B7280;"' if done else ""
        return (f'<p><img src="{checkbox_url(done)}" /> '
                f'<span{style}>{text}</span></p>')

    def bul(text: str, kind: str = "circle") -> str:
        return f'<p><img src="{bullet_url_for(kind)}" /> {text}</p>'

    from app import file_links as fl

    def link(path: str, text: str, is_dir: bool = False) -> str:
        return f'<p><a href="{fl.path_to_href(path, is_dir)}">{text}</a></p>'

    plan = image_to_data_url(_draw_floor_plan())
    chart = image_to_data_url(_draw_chart())
    title = 'style="font-size:13pt; font-weight:700;"'
    return [
        dict(id="n_meeting", color="#FFE066", folder="f_work", by="하준",
             upd_by=HOST, age=4 * MIN, pos=(60, 60), size=(300, 250),
             content=(
                 f'<p><span {title}>주간 회의 메모</span></p>'
                 + bul("신규 거래처 견적은 금요일까지 회신")
                 + bul("창고 재고 실사 일정 확정")
                 + bul("택배 마감 시간 오후 4시로 변경")
                 + '<p><span style="background-color:#C5E1A5; font-weight:700;">'
                   '다음 회의 25일 오전 10시</span></p>')),
        dict(id="n_todo", color="#FFB3BA", folder="f_work", by=HOST,
             upd_by="서연", age=22 * MIN, pos=(390, 60), size=(280, 270),
             content=(
                 f'<p><span {title}>이번 주 할 일</span></p>'
                 + chk(True, "세금계산서 발행")
                 + chk(True, "택배 송장 출력")
                 + chk(False, "거래명세서 정리")
                 + chk(False, "프린터 토너 주문")
                 + chk(False, "금요일 회식 장소 예약"))),
        dict(id="n_plan", color="#BAFFC9", folder="f_store", by="서연",
             upd_by="서연", age=3 * HOUR, pos=(700, 60), size=(340, 420),
             content=(
                 f'<p><span {title}>매장 배치 시안</span></p>'
                 f'<p><img src="{plan}" /></p>'
                 '<p>입구 쪽 행사 매대를 한 칸 넓히는 안입니다.</p>'
                 f'<p><img src="{chart}" /></p>'
                 '<p>주말 방문객이 평일의 두 배라 계산대 앞 동선을 비웁니다.</p>')),
        dict(id="n_info", color="#BAE1FF", folder=None, by=HOST,
             upd_by=HOST, age=1 * DAY + 2 * HOUR, pos=(60, 340), size=(300, 250),
             content=(
                 f'<p><span {title}>사무실 정보</span></p>'
                 + link("\\\\NAS\\업무", "업무 공유 폴더", True)
                 + link("\\\\NAS\\업무\\연락망.xlsx", "비상 연락망")
                 + link("\\\\NAS\\업무\\규정\\취업규칙.pdf", "취업규칙")
                 + link("\\\\NAS\\업무\\양식\\휴가신청서.hwp", "휴가 신청서 양식")
                 + '<p><a href="http://192.168.0.20">복합기 관리 화면</a></p>'
                 '<p>회의실 예약은 <span style="background-color:#FFF59D;">'
                 '이 메모에 이름과 시간</span>을 적어 주세요.</p>')),
        dict(id="n_me", color="#E0BBE4", folder="f_me", by=HOST, upd_by=HOST,
             private=HOST, age=2 * DAY, pos=(390, 360), size=(280, 200),
             content=(
                 f'<p><span {title}>개인 메모</span></p>'
                 + chk(False, "치과 예약 목요일 6시 반")
                 + chk(False, "어머니 생신 선물 고르기")
                 + chk(True, "자동차 보험 갱신"))),
        dict(id="n_lunch", color="#FFD9A6", folder=None, by="하준",
             upd_by="하준", age=50 * MIN, pos=(60, 590), size=(300, 232),
             content=(
                 f'<p><span {title}>점심 주문 받아요</span></p>'
                 + bul("김치찌개 2 (지우, 서연)", "arrow")
                 + bul("제육볶음 1 (하준)", "arrow")
                 + '<p><span style="color:#D64545; font-weight:700;">'
                   '11시 40분에 주문 넣습니다</span></p>')),
        dict(id="n_deploy", color="#3A3A3A", folder="f_work", by=HOST,
             upd_by=HOST, age=5 * DAY, pos=(390, 590), size=(280, 200),
             content=(
                 '<p><span style="font-size:13pt; font-weight:700; '
                 'color:#FFFFFF;">월말 마감 체크</span></p>'
                 + chk(True, "매출 집계 대조")
                 + chk(False, "미수금 목록 정리")
                 + chk(False, "재고 실사표 보관"))),
        dict(id="n_reply", color="#F5F5F5", folder="f_work", by="서연",
             upd_by=HOST, age=9 * DAY, pos=(700, 510), size=(340, 280),
             content=(
                 f'<p><span {title}>문의 답변 문구</span></p>'
                 '<p>안녕하세요. 문의 주신 상품은 오늘 오후 4시 이전 결제 건까지 '
                 '당일 출고됩니다. 도서산간 지역은 하루 이틀 더 걸릴 수 있습니다.</p>'
                 '<p>교환과 반품은 수령 후 7일 이내에 접수해 주세요. '
                 '<span style="text-decoration: underline;">단순 변심은 왕복 배송비가 '
                 '부과됩니다.</span></p>')),
    ]


def _register_fallback_fonts() -> None:
    """offscreen 플랫폼은 시스템 글꼴을 읽지 않는다. 앱 글꼴 사슬의 폴백
    (맑은 고딕·Segoe UI)을 직접 등록해 기호 글자가 실제 화면처럼 그려지게 한다."""
    if ARGS.platform != "offscreen":
        return
    from PySide6.QtGui import QFont
    # 기호 글자(▾ 등)는 실제 Windows 에서 Segoe UI Symbol 로 넘어간다.
    for family in ("Pretendard Variable", "Pretendard JP Variable"):
        QFont.insertSubstitutions(family, ["Segoe UI Symbol", "Malgun Gothic"])
    fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for name in ("malgun.ttf", "malgunbd.ttf", "segoeui.ttf", "segoeuib.ttf",
                 "seguisym.ttf", "meiryo.ttc"):
        f = fonts / name
        if f.exists():
            QFontDatabase.addApplicationFont(str(f))


def seed() -> None:
    """임시 데이터 폴더에 설정·로컬 상태·DB 를 만든다."""
    QFontDatabase.addApplicationFont(str(config.FONT_PATH))
    _register_fallback_fonts()
    from app.database import SqliteRepository
    from app.models import Folder, HistoryEntry, Note

    app_dir = config.app_data_dir()
    (app_dir / "settings.json").write_text(json.dumps({
        "language_code": ARGS.lang,
        "show_tray_icon": False,
        "close_main_to_tray": False,
    }, ensure_ascii=False), encoding="utf-8")

    notes = _notes()
    (app_dir / "local_state.json").write_text(json.dumps({
        "nickname": HOST,
        "last_host_ip": "192.168.0.12",
        "last_host_port": PORT,
        "last_mode": "host",
        "last_heartbeat_ms": 3000,
        "notes": {n["id"]: {"x": n["pos"][0], "y": n["pos"][1],
                            "hidden": False,
                            "w": n["size"][0], "h": n["size"][1]}
                  for n in notes},
        "users": {
            "하준": {"last_seen_at": NOW_MS - 3 * MIN,
                     "last_joined_at": NOW_MS - 2 * HOUR,
                     "last_disconnected_at": 0},
            "서연": {"last_seen_at": NOW_MS - 1 * MIN,
                     "last_joined_at": NOW_MS - 5 * HOUR,
                     "last_disconnected_at": 0},
            "민호": {"last_seen_at": NOW_MS - 2 * DAY,
                     "last_joined_at": NOW_MS - 2 * DAY - HOUR,
                     "last_disconnected_at": NOW_MS - 2 * DAY},
        },
    }, ensure_ascii=False), encoding="utf-8")

    repo = SqliteRepository(config.default_db_path())
    for fid, name, color, private in FOLDERS:
        f = Folder.new(user=HOST, name=name, color=color,
                       private_owner=HOST if private else None)
        f.id = fid
        repo.insert_folder(f)
    for n in notes:
        t = NOW_MS - n["age"]
        repo.insert_note(Note(
            id=n["id"], content=n["content"], color=n["color"],
            width=n["size"][0], height=n["size"][1],
            created_by=n["by"], created_at=t - 2 * HOUR,
            updated_by=n["upd_by"], updated_at=t, version=3,
            folder_id=n["folder"], private_owner=n.get("private"),
        ))
    todo = next(n for n in notes if n["id"] == "n_todo")
    before = todo["content"].replace(
        "금요일 회식 장소 예약", "회식 장소 알아보기")
    meeting = next(n for n in notes if n["id"] == "n_meeting")
    for entry in (
        HistoryEntry(note_id="n_todo", user="서연", action="update",
                     content_before=before, content_after=todo["content"],
                     color_before="#FFB3BA", color_after="#FFB3BA",
                     session_start=NOW_MS - 25 * MIN,
                     session_end=NOW_MS - 22 * MIN),
        HistoryEntry(note_id="n_meeting", user=HOST, action="update",
                     content_before=meeting["content"].replace(
                         "오후 4시로 변경", "오후 3시"),
                     content_after=meeting["content"],
                     color_before="#FFE066", color_after="#FFE066",
                     session_start=NOW_MS - 6 * MIN,
                     session_end=NOW_MS - 4 * MIN),
        HistoryEntry(note_id="n_meeting", user="하준", action="create",
                     content_before=None, content_after="",
                     color_before=None, color_after="#FFE066",
                     session_start=NOW_MS - 2 * HOUR,
                     session_end=NOW_MS - 2 * HOUR),
        HistoryEntry(note_id="n_lunch", user="하준", action="update",
                     content_before="점심 주문 받아요",
                     content_after="점심 주문 받아요\n김치찌개 2\n제육볶음 1",
                     color_before="#FFD9A6", color_after="#FFD9A6",
                     session_start=NOW_MS - 55 * MIN,
                     session_end=NOW_MS - 50 * MIN),
    ):
        repo.insert_history(entry)
    repo.close()


# ── 촬영 ────────────────────────────────────────────────────────────────

SAVED: list[str] = []


def save(pix_or_img, name: str) -> Path:
    path = OUT / f"{name}.png"
    pix_or_img.save(str(path), "PNG")
    SAVED.append(path.name)
    return path


def settle(ms: int = 120) -> None:
    QTest.qWait(ms)
    QApplication.processEvents()


def shot(widget: QWidget, name: str) -> Path:
    settle()
    return save(widget.grab(), name)


def popup_shot(trigger, name: str) -> None:
    """``exec`` 로 뜨는 메뉴를 찍는다. 뜬 뒤 찍고 닫아 ``exec`` 를 풀어 준다."""
    def _grab():
        pop = QApplication.activePopupWidget()
        if pop is not None:
            save(pop.grab(), name)
            pop.close()
    QTimer.singleShot(250, _grab)
    trigger()
    settle(60)


def find_main():
    for w in QApplication.topLevelWidgets():
        if w.__class__.__name__ == "MainWindow":
            return w
    return None


def shoot_raw(window) -> None:
    """창마다 한 장. 지금 화면이 어떻게 생겼는지 그대로 남긴다."""
    from app.ui.connect_dialog import ConnectDialog
    from app.ui.folder_dialog import FolderEditDialog
    from app.ui.settings_dialog import SettingsDialog
    from app.ui.update_dialog import UpdateAvailableDialog

    window._users = [HOST, "하준", "서연"]
    window._refresh_status_text()
    window.resize(380, 640)
    shot(window, "raw-main")

    # 보기 방식: 표시 밀도 셋과 배치 둘
    if hasattr(window, "_set_view_mode"):
        for layout in ("list", "grid"):
            for density in ("compact", "standard", "detailed"):
                window._set_view_mode(density=density, layout=layout)
                shot(window, f"raw-main-{layout}-{density}")
        window.resize(620, 640)
        window._set_view_mode(density="standard", layout="grid")
        shot(window, "raw-main-grid-wide")
        window.resize(380, 640)
        popup_shot(window._show_view_menu, "raw-menu-view")
        window._set_view_mode(density="standard", layout="list")

    # 검색·폴더·선택 모드
    window._search_input.setText("주문")
    shot(window, "raw-main-search")
    window._search_input.setText("없는 검색어")
    shot(window, "raw-main-search-empty")
    window._search_input.clear()
    window._select_folder("f_work")
    shot(window, "raw-main-folder")
    window._select_folder("__all__")
    window._select_btn.setChecked(True)
    for nid in ("n_meeting", "n_todo"):
        window._on_card_clicked(nid)
    shot(window, "raw-main-select")
    window._select_btn.setChecked(False)
    popup_shot(window._show_folder_selector, "raw-menu-folder-selector")
    popup_shot(window._show_folder_menu, "raw-menu-folder-actions")
    card = window._cards["n_meeting"]
    popup_shot(card._open_menu, "raw-menu-card")

    # 메모 창
    for nid, w in window._stickies.items():
        shot(w, f"raw-note-{nid[2:]}")
    note = window._stickies["n_meeting"]
    note._request_edit()
    settle()
    shot(note, "raw-note-editing")
    popup_shot(note._show_color_menu, "raw-menu-note-color")
    popup_shot(note._show_highlight_menu, "raw-menu-note-highlight")
    popup_shot(note._show_font_color_menu, "raw-menu-note-fontcolor")
    popup_shot(note._list_menu.exec, "raw-menu-note-list")
    note._commit_and_release()
    popup_shot(note._show_more_menu, "raw-menu-note-more")
    locked = window._stickies["n_lunch"]
    locked.set_lock_holder("하준")
    shot(locked, "raw-note-locked")
    locked.set_lock_holder(None)

    # 파일 링크: 넣기 메뉴, 링크 창, 링크 우클릭 메뉴
    if hasattr(note, "_insert_link"):
        from PySide6.QtCore import QPoint
        from app import file_links as fl
        from app.ui.link_dialog import LinkDialog
        info = window._stickies["n_info"]
        info._request_edit()
        settle()
        popup_shot(info._show_attach_menu, "raw-menu-note-attach")
        info._commit_and_release()
        href = fl.path_to_href("\\\\NAS\\업무\\연락망.xlsx")
        popup_shot(lambda: info._show_link_menu(href, QPoint(200, 200)),
                   "raw-menu-link")
        ld = LinkDialog(text="비상 연락망", href=href, editing=True)
        ld.show()
        shot(ld, "raw-link-dialog-edit")
        ld.close()
        ld = LinkDialog()
        ld.show()
        ld._target_edit.setText("C:\\Users\\Public\\Documents\\견적서.xlsx")
        shot(ld, "raw-link-dialog-local")
        ld.close()

    # 접기 · 가장자리 붙이기(움직임 없이 끝 상태만 찍는다)
    if hasattr(note, "fold"):
        animate = window._settings.fold_animation
        window._settings.fold_animation = False
        todo = window._stickies["n_todo"]
        home = todo.geometry()
        todo.fold()
        shot(todo, "raw-fold-slip")
        todo.dock("left")
        shot(todo, "raw-fold-tab")
        todo.setGeometry(todo._tab_rect(True))
        todo._slip_hover = True
        shot(todo, "raw-fold-tab-hover")
        todo._slip_hover = False
        todo.unfold()
        shot(todo, "raw-fold-docked-open")
        popup_shot(todo._show_more_menu, "raw-menu-note-more-docked")
        todo.undock()
        todo.setGeometry(home)
        todo.positionChanged.emit(todo.note_id, home.x(), home.y())
        dark = window._stickies["n_deploy"]
        dark.fold()
        shot(dark, "raw-fold-slip-dark")
        dark.unfold()
        private = window._stickies["n_me"]
        private.fold()
        shot(private, "raw-fold-slip-private")
        private.set_fold_style("icon")
        shot(private, "raw-fold-icon-private")
        private.unfold()
        private.set_fold_style("slip")
        # 폴더를 바탕화면 아이콘으로 접기
        if hasattr(window, "_place_folder_icon"):
            window._place_folder_icon("f_work")
            settle(150)
            shot(window._folder_icons["f_work"], "raw-folder-icon-closed")
            window._toggle_folder_icon("f_work")
            settle(150)
            shot(window._folder_icons["f_work"], "raw-folder-icon-open")
            popup_shot(window._show_folder_menu, "raw-menu-folder-actions-all")
            window._select_folder("f_work")
            popup_shot(window._show_folder_menu, "raw-menu-folder-actions-real")
            window._select_folder("__all__")
            window._remove_folder_icon("f_work")
            settle(100)
        window._settings.fold_animation = animate

    # 이미지 뷰어
    plan = window._stickies["n_plan"]
    urls = plan._text.content_image_urls()
    if urls:
        plan._open_image_viewer(urls[0], 0)
        settle(250)
        if plan._image_viewer is not None:
            shot(plan._image_viewer, "raw-image-viewer")
            plan._image_viewer.close()

    # 대화상자
    window._open_history()
    settle(300)
    hv = window._history_viewer
    hv._list.setCurrentRow(0)
    shot(hv, "raw-history")
    hv.close()

    window._open_sessions()
    settle(200)
    shot(window._sessions_dialog, "raw-sessions")
    window._sessions_dialog.close()

    dlg = SettingsDialog(window._settings, role="host", parent=window)
    tabs = dlg.findChild(QTabWidget)
    dlg.show()
    for i, key in enumerate(("general", "memo", "startup", "network",
                             "backup", "about")):
        tabs.setCurrentIndex(i)
        shot(dlg, f"raw-settings-{key}")
    dlg.close()

    cd = ConnectDialog(last_nickname="", last_ip="192.168.0.12",
                       last_port=5555)
    cd.show()
    shot(cd, "raw-connect")
    cd.close()

    fd = FolderEditDialog(parent=window, title="새 폴더",
                          name="", color="#BAE1FF", private=False) \
        if _accepts(FolderEditDialog, "title") else None
    if fd is not None:
        fd.show()
        shot(fd, "raw-folder-dialog")
        fd.close()

    ud = UpdateAvailableDialog(
        current_version=config.APP_VERSION,
        info={"version": "9.9.9", "notes": "변경 사항\n- 예시 항목 하나\n- 예시 항목 둘",
              "installer_url": "", "version_url": ""},
        parent=window)
    ud.show()
    shot(ud, "raw-update-dialog")
    ud.close()


def _accepts(cls, param: str) -> bool:
    import inspect
    try:
        return param in inspect.signature(cls.__init__).parameters
    except (TypeError, ValueError):
        return False


def run() -> None:
    window = find_main()
    if window is None:
        print("메인 창을 찾지 못했습니다.")
        QApplication.quit()
        return
    try:
        if ARGS.which in ("raw", "all"):
            shoot_raw(window)
        if ARGS.which in ("readme", "all"):
            try:
                import shots_readme
            except ImportError:
                shots_readme = None
            if shots_readme is not None:
                shots_readme.shoot(window, sys.modules[__name__])
    finally:
        print(f"저장 {len(SAVED)}장 → {OUT}")
        for name in SAVED:
            print("  ", name)
        window._quit_app()


def _hook_qapp() -> None:
    import PySide6.QtWidgets as qw
    real = qw.QApplication

    class _HookedApp(real):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            seed()
            QTimer.singleShot(1500, run)

    app_main.QApplication = _HookedApp


def main() -> int:
    app_main.register_jumplist = lambda **kw: None
    app_main._schedule_update_check = lambda app, window: None
    # 실행 중인 실제 앱과 같은 파이프 이름으로 듣지 않는다. 같은 이름이면
    # 작업 표시줄의 '새 메모'가 이 촬영용 인스턴스로 올 수 있다.
    from app import ipc
    ipc.ipc_name = lambda: f"SyncNotesShots-IPC-{os.getpid()}"
    _hook_qapp()
    try:
        return app_main.main()
    finally:
        if not ARGS.keep:
            shutil.rmtree(DATA_ROOT, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
