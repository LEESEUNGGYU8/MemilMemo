"""위젯 화면 문구(한국어, 영어, 일본어).

위젯 묶음은 앱의 언어 사전(app/locales)에 키를 더하지 않고 여기에 따로 둔다.
언어는 앱이 정한 것(:func:`app.i18n.current_language`)을 따르고, 없는 키는
한국어로, 그래도 없으면 키 그대로 보인다."""
from __future__ import annotations

LANGS = ("ko", "en", "ja")

_T: dict[str, tuple[str, str, str]] = {
    # 종류 이름(넣기 메뉴에 쓴다)
    "kind.vote": ("투표", "Poll", "投票"),
    "kind.order": ("주문 모으기", "Order list", "注文まとめ"),
    "kind.ladder": ("사다리 타기", "Ladder game", "あみだくじ"),
    "kind.dday": ("D-day", "D-day", "D-day"),
    "kind.calendar": ("달력", "Calendar", "カレンダー"),
    "kind.graph": ("그래프", "Chart", "グラフ"),
    "kind.omok": ("오목", "Gomoku", "五目並べ"),
    # 바탕
    "bg.title": ("바탕", "Background", "背景"),
    "bg.white": ("흰색", "White", "白"),
    "bg.clear": ("투명", "Clear", "透明"),
    # 여럿이 쓰는 단추
    "common.close": ("마감", "End", "締め切る"),
    "common.reopen": ("다시 열기", "Reopen", "再開する"),
    "common.closed": ("마감", "Ended", "締め切り"),
    "common.apply": ("적용", "Apply", "適用"),
    # 위젯 메뉴
    "menu.more": ("위젯 메뉴", "Widget menu", "ウィジェットメニュー"),
    # 투표
    "vote.people": ("{n}명 참여", "{n} voted", "{n}人が投票"),
    "vote.empty": ("항목이 없습니다", "No options", "項目がありません"),
    "vote.add": ("항목 추가", "Add option", "項目を追加"),
    # 주문 모으기
    "order.total": ("모두 {n}", "Total {n}", "合計 {n}"),
    "order.add": ("메뉴 추가", "Add item", "メニューを追加"),
    "order.clear_mine": ("내 주문 지우기", "Clear mine", "自分の注文を消す"),
    "order.empty": ("메뉴가 없습니다", "No items", "メニューがありません"),
    # 사다리 타기
    "ladder.start": ("시작", "Start", "スタート"),
    "ladder.reset": ("다시", "Reset", "やり直す"),
    "ladder.people": ("참가자", "Players", "参加者"),
    "ladder.results": ("결과", "Results", "結果"),
    "ladder.people_n": ("{n}명", "{n} players", "{n}人"),
    # D-day
    "dday.no_date": ("날짜 없음", "No date", "日付なし"),
    "dday.label": ("이름", "Label", "名前"),
    # 달력
    "cal.month": ("{y}년 {m}월", "{mon} {y}", "{y}年{m}月"),
    "cal.month_short": ("{y}.{m}", "{mon} {y}", "{y}/{m}"),
    "cal.weekdays": ("일,월,화,수,목,금,토", "Sun,Mon,Tue,Wed,Thu,Fri,Sat",
                     "日,月,火,水,木,金,土"),
    "cal.months": ("1월,2월,3월,4월,5월,6월,7월,8월,9월,10월,11월,12월",
                   "January,February,March,April,May,June,July,August,September,"
                   "October,November,December",
                   "1月,2月,3月,4月,5月,6月,7月,8月,9月,10月,11月,12月"),
    "cal.day": ("{d}일", "{d}", "{d}日"),
    "cal.more": ("외 {n}개", "{n} more", "ほか{n}件"),
    "cal.none": ("일정 없음", "No events", "予定なし"),
    "cal.today": ("오늘", "Today", "今日"),
    "cal.pack.holidays": ("공휴일", "Holidays", "祝日"),
    "cal.pack.observances": ("기념일", "Observances", "記念日"),
    "cal.pack.tax": ("세무 신고일", "Tax dates", "税務申告日"),
    "cal.day_off": ("쉬는 날", "Day off", "休み"),
    "cal.add_day_off": ("쉬는 날로 정하기", "Mark as day off", "休みにする"),
    "cal.remove_day_off": ("쉬는 날 지우기", "Remove day off", "休みを外す"),
    "cal.country": ("나라", "Country", "国"),
    "cal.prev": ("이전 달", "Previous month", "前の月"),
    "cal.next": ("다음 달", "Next month", "次の月"),
    "cal.no_table": ("이 해의 공휴일 표가 없습니다", "No holiday table for this year",
                     "この年の祝日表がありません"),
    "country.KR": ("한국", "South Korea", "韓国"),
    "country.JP": ("일본", "Japan", "日本"),
    "country.US": ("미국", "United States", "アメリカ"),
    # 날짜
    "date.full": ("{y}년 {m}월 {d}일 ({wd})", "{wd}, {mon} {d}, {y}", "{y}年{m}月{d}日({wd})"),
    "date.short": ("{m}월 {d}일 ({wd})", "{wd}, {mon} {d}", "{m}月{d}日({wd})"),
    # 그래프
    "graph.bar": ("막대", "Bar", "棒"),
    "graph.line": ("꺾은선", "Line", "折れ線"),
    "graph.edit": ("데이터 고치기", "Edit data", "データを編集"),
    "graph.empty": ("데이터 없음", "No data", "データなし"),
    "graph.shape": ("모양", "Type", "種類"),
    "graph.add": ("데이터 넣기", "Add data", "データを入れる"),
    # 오목
    "omok.black": ("흑", "Black", "黒"),
    "omok.white": ("백", "White", "白"),
    "omok.turn": ("{name} 차례", "{name} to move", "{name}の番"),
    "omok.won": ("{name} 승리", "{name} wins", "{name}の勝ち"),
    "omok.draw": ("무승부", "Draw", "引き分け"),
    "omok.new": ("새 판", "New game", "新しい対局"),
    # 공유 PC 가 거절한 까닭
    "err.bad_op": ("알 수 없는 동작입니다", "Unknown action", "不明な操作です"),
    "err.invalid": ("잘못된 값입니다", "Invalid value", "無効な値です"),
    "err.not_allowed": ("만든 사람만 할 수 있습니다", "Only the creator can do this",
                        "作成者だけが操作できます"),
    "err.closed": ("마감했습니다", "Closed", "締め切りました"),
    "err.too_large": ("위젯이 너무 큽니다", "The widget is too large",
                      "ウィジェットが大きすぎます"),
    "err.limit": ("더 넣을 수 없습니다", "No more can be added", "これ以上追加できません"),
    "err.not_found": ("없는 항목입니다", "Item not found", "項目がありません"),
    "err.exists": ("이미 있습니다", "Already exists", "すでにあります"),
    "err.started": ("이미 시작했습니다", "Already started", "すでに始まっています"),
    "err.not_started": ("아직 시작하지 않았습니다", "Not started yet", "まだ始まっていません"),
    "err.game_over": ("끝난 판입니다", "The game is over", "対局は終わりました"),
    "err.not_your_turn": ("차례가 아닙니다", "Not your turn", "あなたの番ではありません"),
    "err.occupied": ("이미 둔 자리입니다", "Already taken", "すでに石があります"),
    "err.newer": ("앱을 업데이트하세요", "Update the app", "アプリを更新してください"),
}


def lang() -> str:
    """지금 앱의 언어. 앱 밖(순수 시험 등)에서는 한국어."""
    try:
        from .. import i18n
        code = i18n.current_language()
    except Exception:  # noqa: BLE001  Qt 없이 부를 때
        code = "ko"
    return code if code in LANGS else "ko"


def tr(key: str, /, *, language: str | None = None, **kwargs) -> str:
    row = _T.get(key)
    if row is None:
        return key
    code = language if language in LANGS else lang()
    text = row[LANGS.index(code)] or row[0]
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return text
    return text


def tr_list(key: str, *, language: str | None = None) -> list[str]:
    return tr(key, language=language).split(",")


def all_texts() -> dict[str, tuple[str, str, str]]:
    """모든 문구(검토와 시험용)."""
    return dict(_T)


def format_date(d, *, language: str | None = None, short: bool = False) -> str:
    """'2026년 12월 5일 (토)' 꼴. ``short`` 면 해를 뺀다('12월 5일 (토)')."""
    code = language if language in LANGS else lang()
    wd = tr_list("cal.weekdays", language=code)[(d.weekday() + 1) % 7]
    mon = tr_list("cal.months", language=code)[d.month - 1]
    return tr("date.short" if short else "date.full", language=code, y=d.year, m=d.month,
              d=d.day, wd=wd, mon=mon)


def format_number(value) -> str:
    """정수는 천 단위 쉼표, 소수는 둘째 자리까지(뒤의 0 은 뺀다)."""
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, int) or float(value).is_integer():
        return f"{int(value):,}"
    text = f"{value:,.2f}".rstrip("0").rstrip(".")
    return text
