from llvoice.text_segments import split_text_segments


def test_split_each_punctuation_delimiter():
    assert split_text_segments("Một.Hai,Ba/Bốn") == ["Một", "Hai", "Ba", "Bốn"]


def test_split_lf_crlf_and_blank_paragraphs():
    assert split_text_segments("Một\nHai\r\n\r\nBa\rBốn") == ["Một", "Hai", "Ba", "Bốn"]


def test_consecutive_delimiters_drop_empty_segments():
    assert split_text_segments("..,,//\n\nA,,B") == ["A", "B"]


def test_trims_segments_and_preserves_order():
    assert split_text_segments("  đầu , giữa / cuối. ") == ["đầu", "giữa", "cuối"]


def test_empty_or_delimiter_only_input_returns_no_segments():
    assert split_text_segments(" \r\n.,/ \n") == []
