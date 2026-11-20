"""Microsoft Store 등록 화면에 올릴 스크린샷과 아이콘을 만든다.

    python tools/capture_screenshots.py --set readme --lang ko --out etc/shots/store-ko
    python tools/store_shots.py --lang ko --source etc/shots/store-ko --out etc/store/ko

스토어는 PNG, 1366x768 이상의 그림을 받는다(데스크톱용 10장까지). 소개 페이지용
장면(capture_screenshots.py 의 readme 묶음)을 1920x1080 바탕에 제목과 함께 올린다.
실제 화면은 찍지 않는다.

``--icon`` 을 주면 300x300 앱 아이콘도 함께 만든다."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QColor,
    QFont,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
)

from app import config  # noqa: E402
from app.ui import theme  # noqa: E402

WIDTH, HEIGHT = 1920, 1080
INK, INK_SOFT = "#1F2937", "#4B5563"
BG_TOP, BG_BOTTOM = "#FBFAF6", "#F1EDE0"
# 장면이 들어갈 자리와, 흐려지지 않게 키우는 한도.
AREA = QRectF(100, 262, WIDTH - 200, 740)
MAX_SCALE = 1.3

# (장면 파일, 제목, 한 줄 설명). 글은 소개 페이지의 것과 같다.
SCENES = {
    "ko": [
        ("hero", "사무실 PC끼리 같이 쓰는 바탕화면 메모",
         "외부 인터넷 접속 없이 내부망 안에서만 동기화됩니다."),
        ("together", "메모 이력 실시간 확인 및 복원",
         "편집 중인 사용자를 표시하고, 수정 이력에서 원하는 시점으로 복원합니다."),
        ("fold-steps", "메모 접기 및 화면 가장자리 고정",
         "쪽지나 아이콘으로 접고, 화면 끝으로 끌어 탭으로 고정합니다."),
        ("links", "파일과 폴더 바로 가기 링크",
         "공유 폴더의 파일과 폴더를 링크로 넣고, 종류별 아이콘으로 구분합니다."),
        ("editing", "할 일 목록과 서식",
         "체크박스, 글머리 기호, 글자 색, 형광펜, 이미지를 지원합니다."),
    ],
    "en": [
        ("hero", "Desktop sticky notes shared across office PCs",
         "Syncs only inside your local network. No internet connection needed."),
        ("together", "See who is editing and restore any version",
         "Shows who is editing a note and restores it from the edit history."),
        ("fold-steps", "Fold notes and dock them to the screen edge",
         "Fold a note into a slip or an icon, or drag it to the edge as a tab."),
        ("links", "Links to files and folders",
         "Add links to files and folders on shared drives, each with its own icon."),
        ("editing", "Checklists and formatting",
         "Checkboxes, bullets, text colors, highlighting, and images."),
    ],
    "ja": [
        ("hero", "オフィスのPC同士で共有するデスクトップメモ",
         "インターネットに接続せず、社内ネットワークの中だけで同期します。"),
        ("together", "編集履歴の確認と復元",
         "編集中のユーザーを表示し、編集履歴から好きな時点に復元します。"),
        ("fold-steps", "メモの折りたたみと画面端への固定",
         "付箋やアイコンに折りたたみ、画面の端にドラッグしてタブとして固定します。"),
        ("links", "ファイルとフォルダーへのリンク",
         "共有フォルダーのファイルとフォルダーをリンクで追加し、種類別のアイコンで区別します。"),
        ("editing", "チェックリストと書式",
         "チェックボックス、箇条書き、文字色、蛍光ペン、画像に対応します。"),
    ],
}


def _font(family: str, px: int, weight: QFont.Weight) -> QFont:
    f = QFont(family) if family else QFont()
    f.setPixelSize(px)
    f.setWeight(weight)
    return f


def compose(scene: QImage, title: str, body: str, family: str) -> QImage:
    out = QImage(WIDTH, HEIGHT, QImage.Format_ARGB32_Premultiplied)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    p.setRenderHint(QPainter.TextAntialiasing)
    grad = QLinearGradient(0, 0, 0, HEIGHT)
    grad.setColorAt(0.0, QColor(BG_TOP))
    grad.setColorAt(1.0, QColor(BG_BOTTOM))
    p.fillRect(0, 0, WIDTH, HEIGHT, grad)

    p.setPen(QColor(INK))
    title_font = _font(family, 60, QFont.ExtraBold)
    title_font.setLetterSpacing(QFont.AbsoluteSpacing, -1.6)
    p.setFont(title_font)
    p.drawText(QRectF(80, 70, WIDTH - 160, 84), Qt.AlignCenter, title)
    p.setPen(QColor(INK_SOFT))
    p.setFont(_font(family, 28, QFont.Medium))
    p.drawText(QRectF(80, 164, WIDTH - 160, 46), Qt.AlignCenter, body)

    scale = min(AREA.width() / scene.width(), AREA.height() / scene.height(),
                MAX_SCALE)
    w, h = scene.width() * scale, scene.height() * scale
    x = AREA.center().x() - w / 2.0
    y = AREA.center().y() - h / 2.0
    radius = 18.0
    p.setPen(Qt.NoPen)
    for i in range(1, 19):
        p.setBrush(QColor(31, 41, 55, 4))
        grow = i * 1.3
        p.drawRoundedRect(QRectF(x - grow, y - grow + 12, w + 2 * grow,
                                 h + 2 * grow), radius + grow, radius + grow)
    shape = QPainterPath()
    shape.addRoundedRect(QRectF(x, y, w, h), radius, radius)
    p.setClipPath(shape)
    p.drawImage(QRectF(x, y, w, h), scene)
    p.setClipping(False)
    p.end()
    return out


def write_icon(path: Path, side: int = 300) -> None:
    source = QImage(str(config.ICON_PATH))
    if source.isNull():
        raise SystemExit(f"아이콘을 읽지 못했습니다: {config.ICON_PATH}")
    source.scaled(side, side, Qt.KeepAspectRatio,
                  Qt.SmoothTransformation).save(str(path), "PNG")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--lang", default="ko", choices=sorted(SCENES))
    ap.add_argument("--source", required=True,
                    help="capture_screenshots.py --set readme 로 찍은 폴더")
    ap.add_argument("--out", required=True)
    ap.add_argument("--icon", action="store_true",
                    help="300x300 앱 아이콘도 만든다")
    args = ap.parse_args()

    app = QGuiApplication.instance() or QGuiApplication([])
    family = theme.load_application_fonts(args.lang)
    source, out = Path(args.source), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for i, (name, title, body) in enumerate(SCENES[args.lang], start=1):
        scene = QImage(str(source / f"{name}.png"))
        if scene.isNull():
            raise SystemExit(f"장면이 없습니다: {source / (name + '.png')}")
        path = out / f"{i:02d}-{name}.png"
        if not compose(scene, title, body, family).save(str(path), "PNG"):
            raise SystemExit(f"쓰지 못했습니다: {path}")
        print(f"{path}  {WIDTH}x{HEIGHT}  {path.stat().st_size:,} bytes")
    if args.icon:
        icon = out / "icon-300.png"
        write_icon(icon)
        print(icon)
    del app
    return 0


if __name__ == "__main__":
    sys.exit(main())
