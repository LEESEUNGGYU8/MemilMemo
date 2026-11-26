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
    ap.add_argument("--only", default="",
                    help="readme 묶음에서 이 장면만 찍는다(쉼표로 여럿, 예: hero,notice).")
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
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

import demo_content  # noqa: E402
import main as app_main  # noqa: E402
from app import config  # noqa: E402

assert str(config.default_db_path()).startswith(str(DATA_ROOT)), (
    "데이터 폴더가 격리되지 않았습니다.")


# ── 데모 데이터 ──────────────────────────────────────────────────────────

NOW_MS = int(time.time() * 1000)
MIN = 60 * 1000
HOUR = 60 * MIN
DAY = 24 * HOUR

# 메모, 폴더, 사람 이름은 고른 언어의 것을 쓴다(tools/demo_content.py).
TEXT = demo_content.for_language(ARGS.lang)
HOST, USER_A, USER_B, USER_OLD = TEXT["people"]

FOLDERS = [
    # id, 이름, 색, 나만 보기
    ("f_work", TEXT["folders"][0], "#BAE1FF", False),
    ("f_store", TEXT["folders"][1], "#BAFFC9", False),
    ("f_me", TEXT["folders"][2], "#E0BBE4", True),
]


def _font(px: int, bold: bool = False) -> QFont:
    from app.ui import theme
    # 일본어는 그 글자가 든 글꼴로 그린다.
    f = QFont(theme.load_application_fonts(ARGS.lang) or "Pretendard Variable")
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
    plan = TEXT["plan"]
    p.drawText(QRectF(400, h - 36, 160, 30), Qt.AlignCenter, plan["entrance"])
    blocks = [
        (90, 90, 250, 90, "#BAE1FF"),
        (90, 230, 120, 250, "#FFE066"),
        (250, 230, 120, 250, "#FFE066"),
        (420, 90, 230, 150, "#BAFFC9"),
        (420, 290, 230, 130, "#FFD9A6"),
        (700, 90, 180, 330, "#E0BBE4"),
    ]
    for (x, y, bw, bh, color), label in zip(blocks, plan["blocks"]):
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
               TEXT["plan"]["chart"])
    days = TEXT["plan"]["days"]
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


def _weekday_ahead(weekday: int):
    """오늘이나 그 뒤의 가장 가까운 그 요일(월=0)의 날짜."""
    import datetime
    today = datetime.date.today()
    return today + datetime.timedelta(days=(weekday - today.weekday()) % 7)


# 메모 안 위젯: 위젯 id → (메모 id, 데이터). 데이터는 seed() 가 만든다(그리기에
# QApplication 이 필요하다).
WIDGETS: dict[str, tuple[str, dict]] = {}


def _build_widgets() -> None:
    """투표(표가 들어온 것), D-day, 그래프의 데이터. 공유 PC 를 거친 것처럼 동작을
    차례로 적용해 만든다."""
    from app import widgets
    party, sales = TEXT["party"], TEXT["sales"]
    vote = widgets.get("vote")
    data = vote.new_data(USER_B, question=party["question"],
                         options=list(party["options"]))
    for user, option in ((USER_A, 1), (USER_B, 2), (HOST, 1), (USER_OLD, 3)):
        data, err = vote.apply_op(data, {"op": "vote", "option": option}, user, NOW_MS)
        assert err is None, err
    WIDGETS["w_vote"] = ("n_party", data)
    dday = widgets.get("dday")
    WIDGETS["w_dday"] = ("n_party", dday.new_data(
        USER_B, label=party["dday"], date=_weekday_ahead(4).isoformat()))
    graph = widgets.get("graph")
    WIDGETS["w_sales"] = ("n_sales", graph.new_data(
        USER_A, title=sales["chart"], chart="bar", labels=list(sales["labels"]),
        series=[{"name": sales["series"][0], "values": [465, 510, 488, 560]},
                {"name": sales["series"][1], "values": [400, 455, 470, 492]}]))


def _widget_html(wid: str, color: str, width: int) -> str:
    """본문에 저장되는 위젯 그림(위젯 id 를 품은 PNG). 앱이 불러올 때 위젯 칸으로 바꾼다."""
    import datetime
    from app import widgets
    _nid, data = WIDGETS[wid]
    kind = widgets.kind_of(data)
    img = kind.render(data, width, viewer="", today=datetime.date.today(),
                      background=color)
    png = widgets.to_png(img, wid, label=widgets.label_of(data))
    return (f'<p><img src="{widgets.to_data_url(png)}" width="{img.width()}" '
            f'height="{img.height()}" /></p>')


def _notes() -> list[dict]:
    from app.ui.rich_text import bullet_url_for, checkbox_url, image_to_data_url

    def chk(done: bool, text: str) -> str:
        # 끝낸 줄을 흐리게 하고 줄을 긋는 것은 앱이 한다(1.0.4 부터).
        return f'<p><img src="{checkbox_url(done)}" /> {text}</p>'

    def bul(text: str, kind: str = "square") -> str:
        # 네모가 기본 목록이다.
        return f'<p><img src="{bullet_url_for(kind)}" /> {text}</p>'

    from app import file_links as fl

    def link(path: str, text: str, is_dir: bool = False) -> str:
        return f'<p><a href="{fl.path_to_href(path, is_dir)}">{text}</a></p>'

    plan = image_to_data_url(_draw_floor_plan())
    chart = image_to_data_url(_draw_chart())
    title = 'style="font-size:13pt; font-weight:700;"'
    t = TEXT
    meeting, todo, layout, info = t["meeting"], t["todo"], t["plan"], t["info"]
    me, lunch, closing, reply = t["me"], t["lunch"], t["closing"], t["reply"]
    notice, party, sales = t["notice"], t["party"], t["sales"]
    return [
        dict(id="n_meeting", color="#FFE066", folder="f_work", by=USER_A,
             upd_by=HOST, age=4 * MIN, pos=(60, 60), size=(300, 250),
             content=(
                 f'<p><span {title}>{meeting["title"]}</span></p>'
                 + "".join(bul(item) for item in meeting["items"])
                 + '<p><span style="background-color:#C5E1A5; font-weight:700;">'
                   f'{meeting["next"]}</span></p>')),
        dict(id="n_todo", color="#FFB3BA", folder="f_work", by=HOST,
             upd_by=USER_B, age=22 * MIN, pos=(390, 60), size=(280, 270),
             content=(
                 f'<p><span {title}>{todo["title"]}</span></p>'
                 + "".join(chk(i < 2, item)
                           for i, item in enumerate(todo["items"])))),
        dict(id="n_plan", color="#BAFFC9", folder="f_store", by=USER_B,
             upd_by=USER_B, age=3 * HOUR, pos=(700, 60), size=(340, 420),
             content=(
                 f'<p><span {title}>{layout["title"]}</span></p>'
                 f'<p><img src="{plan}" /></p>'
                 f'<p>{layout["text1"]}</p>'
                 f'<p><img src="{chart}" /></p>'
                 f'<p>{layout["text2"]}</p>')),
        dict(id="n_info", color="#BAE1FF", folder=None, by=HOST,
             upd_by=HOST, age=1 * DAY + 2 * HOUR, pos=(60, 340), size=(300, 250),
             content=(
                 f'<p><span {title}>{info["title"]}</span></p>'
                 + "".join(link(path, text, is_dir)
                           for path, text, is_dir in info["links"])
                 + f'<p><a href="http://192.168.0.20">{info["web"]}</a></p>'
                 f'<p>{info["room"][0]}<span style="background-color:#FFF59D;">'
                 f'{info["room"][1]}</span>{info["room"][2]}</p>')),
        dict(id="n_me", color="#E0BBE4", folder="f_me", by=HOST, upd_by=HOST,
             private=HOST, age=2 * DAY, pos=(390, 360), size=(280, 200),
             content=(
                 f'<p><span {title}>{me["title"]}</span></p>'
                 + "".join(chk(i == 2, item)
                           for i, item in enumerate(me["items"])))),
        dict(id="n_lunch", color="#FFD9A6", folder=None, by=USER_A,
             upd_by=USER_A, age=50 * MIN, pos=(60, 590), size=(300, 232),
             content=(
                 f'<p><span {title}>{lunch["title"]}</span></p>'
                 + "".join(bul(item, "arrow") for item in lunch["items"])
                 + '<p><span style="color:#D64545; font-weight:700;">'
                   f'{lunch["note"]}</span></p>')),
        dict(id="n_deploy", color="#3A3A3A", folder="f_work", by=HOST,
             upd_by=HOST, age=5 * DAY, pos=(390, 590), size=(280, 200),
             content=(
                 '<p><span style="font-size:13pt; font-weight:700; '
                 f'color:#FFFFFF;">{closing["title"]}</span></p>'
                 + "".join(chk(i == 0, item)
                           for i, item in enumerate(closing["items"])))),
        dict(id="n_reply", color="#F5F5F5", folder="f_work", by=USER_B,
             upd_by=HOST, age=9 * DAY, pos=(700, 510), size=(340, 280),
             content=(
                 f'<p><span {title}>{reply["title"]}</span></p>'
                 f'<p>{reply["text1"]}</p>'
                 f'<p>{reply["text2"]}'
                 '<span style="text-decoration: underline;">'
                 f'{reply["underlined"]}</span></p>')),
        # 1.1 의 새 기능에 쓰는 메모: 공지, 위젯(투표와 D-day, 그래프).
        dict(id="n_notice", color="#C4CBFF", folder="f_work", by=HOST,
             upd_by=HOST, age=35 * MIN, pos=(60, 60), size=(360, 236),
             content=(
                 f'<p><span {title}>{notice["title"]}</span></p>'
                 + "".join(bul(item) for item in notice["items"])
                 + f'<p>{notice["ask"].format(a="@" + USER_A, b="@" + USER_B)}</p>')),
        dict(id="n_party", color="#D7F29B", folder=None, by=USER_B,
             upd_by=USER_B, age=15 * MIN, pos=(390, 60), size=(310, 440),
             content=(
                 f'<p><span {title}>{party["title"]}</span></p>'
                 f'<p>{party["text"].format(me="@" + HOST)}</p>'
                 + _widget_html("w_vote", "#D7F29B", 262)
                 + _widget_html("w_dday", "#D7F29B", 262))),
        dict(id="n_sales", color="#A6E8E0", folder="f_store", by=USER_A,
             upd_by=USER_A, age=2 * HOUR + 10 * MIN, pos=(700, 60), size=(340, 360),
             content=(
                 f'<p><span {title}>{sales["title"]}</span></p>'
                 + _widget_html("w_sales", "#A6E8E0", 292)
                 + f'<p>{sales["text"]}</p>')),
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

    from app import i18n
    from app import reminders as rm
    today = time.strftime("%Y-%m-%d")
    # 본문에 저장하는 위젯 그림과 그 이름(목록의 미리 보기 글)은 그린 PC 의 언어로
    # 적힌다. 앱이 언어를 정하기 전이므로 여기서 먼저 정한다.
    i18n.set_language(ARGS.lang)
    _build_widgets()
    notes = _notes()
    (app_dir / "local_state.json").write_text(json.dumps({
        # 할 일 메모: 매주 월요일 아침에 체크를 푼다(방금 건 것이라 아직 울리지 않는다).
        "check_resets": {"n_todo": {
            "rule": rm.Rule(rm.WEEKLY, today, "08:30", [0]).to_dict(),
            "set_at": NOW_MS - MIN}},
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
            USER_A: {"last_seen_at": NOW_MS - 3 * MIN,
                     "last_joined_at": NOW_MS - 2 * HOUR,
                     "last_disconnected_at": 0},
            USER_B: {"last_seen_at": NOW_MS - 1 * MIN,
                     "last_joined_at": NOW_MS - 5 * HOUR,
                     "last_disconnected_at": 0},
            USER_OLD: {"last_seen_at": NOW_MS - 2 * DAY,
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
    before = todo["content"].replace(*TEXT["todo"]["edit"])
    meeting = next(n for n in notes if n["id"] == "n_meeting")
    for entry in (
        HistoryEntry(note_id="n_todo", user=USER_B, action="update",
                     content_before=before, content_after=todo["content"],
                     color_before="#FFB3BA", color_after="#FFB3BA",
                     session_start=NOW_MS - 25 * MIN,
                     session_end=NOW_MS - 22 * MIN),
        HistoryEntry(note_id="n_meeting", user=HOST, action="update",
                     content_before=meeting["content"].replace(
                         *TEXT["meeting"]["edit"]),
                     content_after=meeting["content"],
                     color_before="#FFE066", color_after="#FFE066",
                     session_start=NOW_MS - 6 * MIN,
                     session_end=NOW_MS - 4 * MIN),
        HistoryEntry(note_id="n_meeting", user=USER_A, action="create",
                     content_before=None, content_after="",
                     color_before=None, color_after="#FFE066",
                     session_start=NOW_MS - 2 * HOUR,
                     session_end=NOW_MS - 2 * HOUR),
        HistoryEntry(note_id="n_lunch", user=USER_A, action="update",
                     content_before=TEXT["lunch"]["title"],
                     content_after=TEXT["lunch"]["history"],
                     color_before="#FFD9A6", color_after="#FFD9A6",
                     session_start=NOW_MS - 55 * MIN,
                     session_end=NOW_MS - 50 * MIN),
    ):
        repo.insert_history(entry)
    # 공지(내가 올린 것)와 그것을 본 사람(민호는 아직 안 봤다).
    repo.set_notice("n_notice", HOST)
    for user, ago in ((USER_A, 21 * MIN), (USER_B, 8 * MIN)):
        repo._conn.execute(
            "INSERT OR REPLACE INTO note_seen (note_id, user, version, seen_at) "
            "VALUES (?, ?, ?, ?)", ("n_notice", user, 3, NOW_MS - ago))
    repo._conn.commit()
    # 할 일 메모의 공유 알림: 매주 금요일 오후 4시, 10분 전에 미리, 고른 세 사람에게.
    repo.set_reminder("n_todo", {
        "rule": rm.Rule(rm.WEEKLY, today, "16:00", [4], lead=10).to_dict(),
        "targets": [HOST, USER_A, USER_B], "set_by": HOST, "set_at": NOW_MS - MIN})
    for wid, (nid, data) in WIDGETS.items():
        repo.put_widget(wid, nid, data)
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

    window._users = [HOST, USER_A, USER_B]
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
    window._search_input.setText(TEXT["search"][0])
    shot(window, "raw-main-search")
    window._search_input.setText(TEXT["search"][1])
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
    locked.set_lock_holder(USER_A)
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
        sample_path, sample_text, _is_dir = TEXT["info"]["links"][1]
        href = fl.path_to_href(sample_path)
        popup_shot(lambda: info._show_link_menu(href, QPoint(200, 200)),
                   "raw-menu-link")
        ld = LinkDialog(text=sample_text, href=href, editing=True)
        ld.show()
        shot(ld, "raw-link-dialog-edit")
        ld.close()
        ld = LinkDialog()
        ld.show()
        ld._target_edit.setText(TEXT["quote_file"])
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
    dlg.show()
    for i, key in enumerate(("general", "memo", "fold", "notify", "network",
                             "backup", "about")):
        dlg.show_page(i)
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
    # 참가자 창에 이 PC 의 실제 주소와 촬영용 포트 대신 예시 주소를 보여 준다.
    from app.ui import main_window as main_window_module
    main_window_module._local_lan_ip = lambda: "192.168.0.12"
    window._local.state.last_host_port = config.DEFAULT_PORT
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
    # main.py 는 QApplication 이 아니라 그것을 물려받은 _App 을 만든다.
    real = app_main._App

    class _HookedApp(real):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            seed()
            QTimer.singleShot(1500, run)

    app_main._App = _HookedApp


def main() -> int:
    app_main.register_jumplist = lambda **kw: None
    app_main._schedule_update_check = lambda *args: None
    # 실행 중인 실제 앱과 같은 파이프 이름으로 듣지 않는다. 같은 이름이면
    # 작업 표시줄의 '새 메모'가 이 촬영용 인스턴스로 올 수 있다.
    from app import ipc
    ipc.ipc_name = lambda: f"SyncNotesShots-IPC-{os.getpid()}"
    # 촬영용 공유 포트는 이 PC 안에서만 연다(내부망의 다른 PC 가 들어올 수 없다).
    from PySide6.QtNetwork import QHostAddress
    from app.network import host_server

    class _Loopback(QHostAddress):
        Any = QHostAddress.SpecialAddress.LocalHost

    host_server.QHostAddress = _Loopback
    _hook_qapp()
    try:
        return app_main.main()
    finally:
        if not ARGS.keep:
            shutil.rmtree(DATA_ROOT, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
