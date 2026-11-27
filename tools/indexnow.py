"""소개 사이트의 쪽이 바뀌었다고 IndexNow 로 검색 엔진(Bing, 네이버 등)에 알린다.

    python tools/indexnow.py            # 사이트맵의 모든 주소
    python tools/indexnow.py guide.html # 고른 쪽만(사이트 주소 뒤의 경로)

사이트를 푸시해 GitHub Pages 에 반영된 뒤에 돌린다. 열쇠 파일(docs/<열쇠>.txt)이 사이트에
올라가 있어야 받아 준다. 프로젝트 사이트라 열쇠 파일이 /MemilMemo/ 아래에 있으므로 그
아래의 주소만 보낼 수 있다(keyLocation 으로 알린다).

보내는 것은 공개된 사이트 주소와 열쇠뿐이다."""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = "https://leeseunggyu8.github.io/MemilMemo/"
HOST = "leeseunggyu8.github.io"
ENDPOINT = "https://api.indexnow.org/indexnow"


def key() -> str:
    found = [p.stem for p in (ROOT / "docs").glob("*.txt")
             if re.fullmatch(r"[0-9a-f]{32}", p.stem)
             and p.read_text(encoding="ascii").strip() == p.stem]
    if len(found) != 1:
        raise SystemExit(f"열쇠 파일을 하나 찾지 못했습니다: {found}")
    return found[0]


def urls(paths: list[str]) -> list[str]:
    if paths:
        return [SITE + p.lstrip("/") for p in paths]
    xml = (ROOT / "docs" / "sitemap.xml").read_text(encoding="utf-8")
    return re.findall(r"<loc>([^<]+)</loc>", xml)


def main() -> int:
    k = key()
    body = {"host": HOST, "key": k, "keyLocation": f"{SITE}{k}.txt", "urlList": urls(sys.argv[1:])}
    req = urllib.request.Request(
        ENDPOINT, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            print(resp.status, len(body["urlList"]), "주소를 알렸습니다")
            return 0
    except urllib.error.HTTPError as e:
        # 200, 202 는 받음. 403 은 열쇠 파일을 아직 못 찾음, 422 는 주소가 열쇠 위치 밖.
        print(e.code, e.read().decode("utf-8", "replace")[:300])
        return 1


if __name__ == "__main__":
    sys.exit(main())
