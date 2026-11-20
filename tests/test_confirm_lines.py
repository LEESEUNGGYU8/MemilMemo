"""확인창과 알림창의 줄 나눔(offscreen)."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QFont, QFontMetrics  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from app import i18n  # noqa: E402
from app.ui import confirm, theme  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    i18n.set_language("ko")
    theme.apply_theme(app, "")
    yield app


@pytest.fixture
def font(qapp):
    f = QFont(theme.load_application_fonts("ko"))
    f.setPixelSize(13)
    return f


def widths(text: str, font: QFont) -> list[int]:
    fm = QFontMetrics(font)
    return [fm.horizontalAdvance(line) for line in text.split("\n")]


def test_short_text_is_left_alone(font):
    assert confirm.break_lines("메모를 삭제합니다.", font) == "메모를 삭제합니다."
    assert confirm.break_lines("첫 줄\n둘째 줄", font) == "첫 줄\n둘째 줄"


def test_sentences_are_split_before_words(font):
    text = ("사본 메모를 기준으로 이 PC를 공유 PC로 설정합니다. "
            "프로그램을 종료 후 다시 시작합니다.")
    width = QFontMetrics(font).horizontalAdvance(text) - 10
    assert confirm.break_lines(text, font, width) == (
        "사본 메모를 기준으로 이 PC를 공유 PC로 설정합니다.\n"
        "프로그램을 종료 후 다시 시작합니다.")


def test_short_sentences_share_a_line_when_they_fit(font):
    text = "포트를 열 수 없습니다. 다른 포트를 고르세요. 그래도 안 되면 다시 켜 보세요."
    fm = QFontMetrics(font)
    width = fm.horizontalAdvance("포트를 열 수 없습니다. 다른 포트를 고르세요.") + 4
    assert confirm.break_lines(text, font, width) == (
        "포트를 열 수 없습니다. 다른 포트를 고르세요.\n그래도 안 되면 다시 켜 보세요.")


def test_long_sentence_does_not_leave_one_word_on_the_last_line(font):
    text = "원래 공유 PC를 다시 쓸 수 없을 때만 하세요."
    fm = QFontMetrics(font)
    # 마지막 낱말 하나만 넘치는 폭. Qt 에 맡기면 '하세요.' 만 다음 줄로 간다.
    width = fm.horizontalAdvance(text) - 6
    lines = confirm.break_lines(text, font, width).split("\n")
    assert len(lines) == 2 and lines[1] != "하세요."
    first, second = widths("\n".join(lines), font)
    assert max(first, second) <= width and min(first, second) > width * 0.3
    # 한글 낱말은 가운데서 끊기지 않는다.
    assert " ".join(lines) == text


def test_text_without_spaces_breaks_after_a_comma(font):
    text = "ダウンロードしたファイルを確認できないため、インストールしませんでした。"
    width = QFontMetrics(font).horizontalAdvance(text) - 10
    assert confirm.break_lines(text, font, width) == (
        "ダウンロードしたファイルを確認できないため、\nインストールしませんでした。")


def test_a_path_too_long_for_one_line_is_still_shown_whole(font):
    path = "C:\\Users\\someone\\Documents\\" + "아주긴폴더이름" * 12 + "\\notes.sqlite3"
    out = confirm.break_lines(path, font, 300)
    assert out.replace("\n", "") == path and max(widths(out, font)) <= 300


def test_boxes_are_wide_enough_for_their_lines(qapp):
    """나눈 줄이 창 안에서 다시 접히지 않는다."""
    for lang in ("ko", "en", "ja"):
        i18n.set_language(lang)
        box, _act, _cancel = confirm.build_ask(
            None, i18n.t("copies.btn_takeover"), i18n.t("copies.takeover_body"),
            action=i18n.t("copies.takeover_action"),
            detail=i18n.t("copies.takeover_detail"), danger=True)
        box.show()
        qapp.processEvents()
        labels = [box.findChild(QLabel, name) for name in
                  ("qt_msgbox_label", "qt_msgbox_informativelabel")]
        assert all(label is not None for label in labels), lang
        for label in labels:
            fm = label.fontMetrics()
            for line in label.text().split("\n"):
                assert fm.horizontalAdvance(line) <= label.width(), (lang, line)
        # 두 문장은 문장마다 한 줄, 그 아래 글은 한 줄에 들어간다.
        assert labels[0].text().count("\n") == 1, (lang, labels[0].text())
        assert "\n" not in labels[1].text(), (lang, labels[1].text())
        box.close()
        box.deleteLater()
    i18n.set_language("ko")
