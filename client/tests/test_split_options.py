from llvoice.text_segments import (
    SPLIT_LINES,
    SPLIT_NONE,
    SPLIT_PARAGRAPHS,
    SplitOptions,
    split_text_segments,
)

TEXT = "Câu một. Câu hai, vế sau!\nDòng hai\n\nĐoạn mới dòng một\ndòng hai"


def test_default_matches_previous_behaviour():
    assert split_text_segments("Một.Hai,Ba/Bốn\nNăm") == ["Một", "Hai", "Ba", "Bốn", "Năm"]


def test_custom_delimiters():
    options = SplitOptions(delimiters=".!?")
    assert split_text_segments(TEXT, options) == [
        "Câu một", "Câu hai, vế sau", "Dòng hai", "Đoạn mới dòng một", "dòng hai",
    ]


def test_special_regex_characters_are_literal():
    assert split_text_segments("a-b]c^d\\e", SplitOptions(delimiters="-]^\\")) == ["a", "b", "c", "d", "e"]


def test_lines_paragraphs_and_none():
    assert split_text_segments(TEXT, SplitOptions(mode=SPLIT_LINES)) == [
        "Câu một. Câu hai, vế sau!", "Dòng hai", "Đoạn mới dòng một", "dòng hai",
    ]
    assert split_text_segments(TEXT, SplitOptions(mode=SPLIT_PARAGRAPHS)) == [
        "Câu một. Câu hai, vế sau! Dòng hai", "Đoạn mới dòng một dòng hai",
    ]
    assert split_text_segments("  a \n b  ", SplitOptions(mode=SPLIT_NONE)) == ["a b"]


def test_merge_keeps_punctuation_and_respects_limit():
    options = SplitOptions(delimiters=".,", merge_limit=20)
    # Đoạn dài hơn giới hạn vẫn giữ nguyên, không bị cắt giữa chừng.
    assert split_text_segments("Một hai. Ba bốn, năm sáu. Bảy tám chín mười mười một", options) == [
        "Một hai. Ba bốn", "năm sáu", "Bảy tám chín mười mười một",
    ]


def test_merge_joins_short_pieces():
    options = SplitOptions(delimiters=".,", merge_limit=40)
    assert split_text_segments("A. B, C.\nD", options) == ["A. B, C. D"]
    assert split_text_segments("a/b", SplitOptions(merge_limit=10)) == ["a b"]
