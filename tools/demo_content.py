"""스크린샷에 쓰는 예시 내용(언어별).

소개 페이지와 스토어 그림에 나오는 메모, 폴더, 사람 이름, 그림 속 글자다.
``capture_screenshots.py`` 가 ``--lang`` 에 맞는 것을 골라 데모 데이터를 만든다.
세 언어의 모양(항목 수, 순서)은 같아야 한다."""
from __future__ import annotations

DEMO = {
    "ko": {
        # 나, 함께 쓰는 두 사람, 예전에 접속했던 사람
        "people": ("지우", "하준", "서연", "민호"),
        "folders": ("업무", "매장 리뉴얼", "개인"),
        "meeting": {
            "title": "주간 회의 메모",
            "items": ("신규 거래처 견적은 금요일까지 회신",
                      "창고 재고 실사 일정 확정",
                      "택배 마감 시간 오후 4시로 변경"),
            "next": "다음 회의 25일 오전 10시",
            # (지금 글, 고치기 전 글): 수정 이력에 나온다
            "edit": ("오후 4시로 변경", "오후 3시"),
        },
        "todo": {
            "title": "이번 주 할 일",
            "items": ("세금계산서 발행", "택배 송장 출력", "거래명세서 정리",
                      "프린터 토너 주문", "금요일 회식 장소 예약"),
            "edit": ("금요일 회식 장소 예약", "회식 장소 알아보기"),
        },
        "plan": {
            "title": "매장 배치 시안",
            "text1": "입구 쪽 행사 매대를 한 칸 넓히는 안입니다.",
            "text2": "주말 방문객이 평일의 두 배라 계산대 앞 동선을 비웁니다.",
            "entrance": "입구",
            "blocks": ("계산대", "진열 A", "진열 B", "신상품", "행사 매대", "창고"),
            "chart": "요일별 방문객",
            "days": ("월", "화", "수", "목", "금", "토", "일"),
        },
        "info": {
            "title": "사무실 정보",
            # (경로, 보이는 글, 폴더인가)
            "links": (("\\\\NAS\\업무", "업무 공유 폴더", True),
                      ("\\\\NAS\\업무\\연락망.xlsx", "비상 연락망", False),
                      ("\\\\NAS\\업무\\규정\\취업규칙.pdf", "취업규칙", False),
                      ("\\\\NAS\\업무\\양식\\휴가신청서.hwp", "휴가 신청서 양식", False)),
            "web": "복합기 관리 화면",
            # (앞, 형광펜을 칠한 부분, 뒤)
            "room": ("회의실 예약은 ", "이 메모에 이름과 시간", "을 적어 주세요."),
        },
        "me": {
            "title": "개인 메모",
            "items": ("치과 예약 목요일 6시 반", "어머니 생신 선물 고르기",
                      "자동차 보험 갱신"),
        },
        "lunch": {
            "title": "점심 주문 받아요",
            "items": ("김치찌개 2 (지우, 서연)", "제육볶음 1 (하준)"),
            "note": "11시 40분에 주문 넣습니다",
            "history": "점심 주문 받아요\n김치찌개 2\n제육볶음 1",
        },
        "closing": {
            "title": "월말 마감 체크",
            "items": ("매출 집계 대조", "미수금 목록 정리", "재고 실사표 보관"),
        },
        "reply": {
            "title": "문의 답변 문구",
            "text1": ("안녕하세요. 문의 주신 상품은 오늘 오후 4시 이전 결제 건까지 "
                      "당일 출고됩니다. 도서산간 지역은 하루 이틀 더 걸릴 수 있습니다."),
            "text2": "교환과 반품은 수령 후 7일 이내에 접수해 주세요. ",
            "underlined": "단순 변심은 왕복 배송비가 부과됩니다.",
        },
        # 공지로 올린 메모. ask 의 {a}, {b} 에는 '@이름' 이 들어간다.
        "notice": {
            "title": "사무실 공지",
            "items": ("12월 1일부터 새 출입 카드를 씁니다",
                      "금요일 오후 3시에 소방 점검이 있습니다"),
            "ask": "{a} {b} 회의실 예약표를 확인해 주세요",
        },
        # 위젯이 든 메모: 투표와 D-day. text 의 {me} 에는 '@이름' 이 들어간다.
        "party": {
            "title": "금요일 회식",
            "text": "{me} 투표가 끝나면 식당 예약 부탁해요",
            "question": "회식 메뉴",
            "options": ("삼겹살", "회", "중식"),
            "dday": "회식",
        },
        # 그래프가 든 메모
        "sales": {
            "title": "월별 매출",
            "chart": "매출 (만 원)",
            "labels": ("8월", "9월", "10월", "11월"),
            "series": ("올해", "작년"),
            "text": "11월은 행사 매대 효과로 지난해보다 14% 늘었습니다.",
        },
        # 접힌 모양 그림에 적는 짧은 제목: 쪽지, 가장자리 탭, 아이콘
        "faces": ("주간 회의", "할 일", "점심 주문 받아요"),
        "search": ("주문", "없는 검색어"),
        "quote_file": "C:\\Users\\Public\\Documents\\견적서.xlsx",
    },
    "en": {
        "people": ("Mia", "Leo", "Emma", "Noah"),
        "folders": ("Work", "Store Remodel", "Personal"),
        "meeting": {
            "title": "Weekly Meeting Notes",
            "items": ("Reply to the new client's quote by Friday",
                      "Confirm the warehouse stock count date",
                      "Courier cutoff moved to 4 PM"),
            "next": "Next meeting: the 25th, 10 AM",
            "edit": ("to 4 PM", "to 3 PM"),
        },
        "todo": {
            "title": "This Week's To-Do",
            "items": ("Send out invoices", "Print shipping labels",
                      "File delivery statements", "Order printer toner",
                      "Book a place for Friday's team dinner"),
            "edit": ("Book a place for Friday's team dinner",
                     "Look for a team dinner place"),
        },
        "plan": {
            "title": "Store Layout Draft",
            "text1": "This plan widens the promo display by the entrance by one section.",
            "text2": ("Weekend visitors are double the weekday count, so the aisle "
                      "in front of the checkout stays clear."),
            "entrance": "Entrance",
            "blocks": ("Checkout", "Shelf A", "Shelf B", "New Items", "Promo", "Storage"),
            "chart": "Visitors by Day",
            "days": ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
        },
        "info": {
            "title": "Office Info",
            "links": (("\\\\NAS\\Work", "Shared work folder", True),
                      ("\\\\NAS\\Work\\Contacts.xlsx", "Emergency contacts", False),
                      ("\\\\NAS\\Work\\Policies\\Handbook.pdf", "Employee handbook", False),
                      ("\\\\NAS\\Work\\Forms\\LeaveRequest.docx", "Leave request form", False)),
            "web": "Printer admin page",
            "room": ("To book the meeting room, write ",
                     "your name and time in this note", "."),
        },
        "me": {
            "title": "Personal",
            "items": ("Dentist, Thursday 6:30 PM", "Pick a birthday gift for Mom",
                      "Renew car insurance"),
        },
        "lunch": {
            "title": "Lunch Orders",
            "items": ("Chicken sandwich 2 (Mia, Emma)", "Veggie pasta 1 (Leo)"),
            "note": "Ordering at 11:40",
            "history": "Lunch Orders\nChicken sandwich 2\nVeggie pasta 1",
        },
        "closing": {
            "title": "Month-End Closing",
            "items": ("Reconcile sales totals", "List unpaid invoices",
                      "File the stock count sheet"),
        },
        "reply": {
            "title": "Reply Template",
            "text1": ("Hello. Orders paid before 4 PM today ship the same day. "
                      "Remote areas may take one or two more days."),
            "text2": "Please request exchanges and returns within 7 days of delivery. ",
            "underlined": "Returns for a change of mind are charged round-trip shipping.",
        },
        "notice": {
            "title": "Office Notice",
            "items": ("New access cards start on December 1",
                      "Fire inspection on Friday at 3 PM"),
            "ask": "{a} {b} please check the meeting room schedule",
        },
        "party": {
            "title": "Friday Team Dinner",
            "text": "{me} please book a table once the poll closes",
            "question": "Dinner menu",
            "options": ("Korean BBQ", "Sushi", "Chinese"),
            "dday": "Team dinner",
        },
        "sales": {
            "title": "Monthly Sales",
            "chart": "Sales ($K)",
            "labels": ("Aug", "Sep", "Oct", "Nov"),
            "series": ("This year", "Last year"),
            "text": "November is up 14% from last year thanks to the promo display.",
        },
        "faces": ("Weekly Meeting", "To-Do", "Lunch Orders"),
        "search": ("order", "no such text"),
        "quote_file": "C:\\Users\\Public\\Documents\\Quote.xlsx",
    },
    "ja": {
        "people": ("美咲", "翔太", "陽菜", "健太"),
        "folders": ("業務", "店舗改装", "個人"),
        "meeting": {
            "title": "週次ミーティングメモ",
            "items": ("新規取引先の見積もりは金曜までに返信",
                      "倉庫の棚卸し日程を確定",
                      "宅配の締め切りを午後4時に変更"),
            "next": "次回の会議は25日 午前10時",
            "edit": ("午後4時に変更", "午後3時"),
        },
        "todo": {
            "title": "今週のやること",
            "items": ("請求書を発行", "宅配の送り状を印刷", "納品書を整理",
                      "プリンターのトナーを注文", "金曜の食事会の店を予約"),
            "edit": ("金曜の食事会の店を予約", "食事会の店を探す"),
        },
        "plan": {
            "title": "売り場レイアウト案",
            "text1": "入口側の特設コーナーを1区画広げます。",
            "text2": "週末の来店客は平日の2倍なので、レジ前の通路を空けます。",
            "entrance": "入口",
            "blocks": ("レジ", "陳列 A", "陳列 B", "新商品", "特設", "倉庫"),
            "chart": "曜日別の来店客",
            "days": ("月", "火", "水", "木", "金", "土", "日"),
        },
        "info": {
            "title": "オフィス情報",
            "links": (("\\\\NAS\\業務", "業務共有フォルダー", True),
                      ("\\\\NAS\\業務\\連絡網.xlsx", "緊急連絡網", False),
                      ("\\\\NAS\\業務\\規程\\就業規則.pdf", "就業規則", False),
                      ("\\\\NAS\\業務\\様式\\休暇届.docx", "休暇届の様式", False)),
            "web": "複合機の管理画面",
            "room": ("会議室の予約は", "このメモに名前と時間", "を書いてください。"),
        },
        "me": {
            "title": "個人メモ",
            "items": ("歯医者の予約 木曜 6時半", "母の誕生日プレゼントを選ぶ",
                      "自動車保険の更新"),
        },
        "lunch": {
            "title": "ランチの注文",
            "items": ("唐揚げ弁当 2（美咲、陽菜）", "焼き魚弁当 1（翔太）"),
            "note": "11時40分に注文します",
            "history": "ランチの注文\n唐揚げ弁当 2\n焼き魚弁当 1",
        },
        "closing": {
            "title": "月末の締めチェック",
            "items": ("売上集計の照合", "未収金リストの整理", "棚卸表の保管"),
        },
        "reply": {
            "title": "お問い合わせ返信の文面",
            "text1": ("お問い合わせありがとうございます。本日午後4時までにご決済いただいた分は"
                      "当日出荷いたします。離島など一部地域は1〜2日多くかかる場合があります。"),
            "text2": "交換と返品は商品到着後7日以内にご連絡ください。",
            "underlined": "お客様都合の場合は往復送料をご負担いただきます。",
        },
        "notice": {
            "title": "オフィスのお知らせ",
            "items": ("12月1日から新しい入館カードを使います",
                      "金曜の午後3時に消防点検があります"),
            "ask": "{a} {b} 会議室の予約表を確認してください",
        },
        "party": {
            "title": "金曜の食事会",
            "text": "{me} 投票が終わったらお店の予約をお願いします",
            "question": "食事会のメニュー",
            "options": ("焼肉", "寿司", "中華"),
            "dday": "食事会",
        },
        "sales": {
            "title": "月別の売上",
            "chart": "売上（万円）",
            "labels": ("8月", "9月", "10月", "11月"),
            "series": ("今年", "昨年"),
            "text": "11月は特設コーナーの効果で昨年より14%増えました。",
        },
        "faces": ("週次ミーティング", "やること", "ランチの注文"),
        "search": ("注文", "ない言葉"),
        "quote_file": "C:\\Users\\Public\\Documents\\見積書.xlsx",
    },
}


def for_language(lang: str) -> dict:
    return DEMO.get(lang, DEMO["ko"])
