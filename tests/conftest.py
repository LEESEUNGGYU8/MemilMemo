"""테스트 공통 준비.

테스트가 실수로라도 실제 사용자 데이터(%APPDATA%\\SyncNotes)를 건드리지 않도록
APPDATA 를 임시 폴더로 돌린다. 파이썬이 뜬 뒤에 바꾸므로 사용자 site-packages
경로에는 영향이 없다(실행 전에 환경 변수로 바꾸면 pytest 자체를 못 찾는다)."""
from __future__ import annotations

import os
import tempfile

# 테스트가 띄우는 다른 파이썬 프로세스는 원래 값으로 시작해야 사용자
# site-packages 를 찾는다. 그쪽에 넘겨줄 수 있게 남겨 둔다.
os.environ.setdefault("SYNCNOTES_TEST_REAL_APPDATA", os.environ.get("APPDATA", ""))
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="syncnotes-tests-")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
