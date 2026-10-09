import pytest

from llvoice.srt import SrtCue, SrtError, format_srt, parse_srt, parse_srt_bytes, validate_cues

SAMPLE = """1\r\n00:00:00,000 --> 00:00:01,500\r\nHello\r\nworld\r\n\r\n3\r\n00:00:02,000 --> 00:00:03,000\r\nBye"""


def test_parse_multiline_crlf_noncontiguous_indices_and_missing_final_separator():
    assert parse_srt(SAMPLE) == [
        SrtCue(1, 0, 1500, "Hello\nworld"),
        SrtCue(3, 2000, 3000, "Bye"),
    ]


def test_parse_utf8_bom_bytes():
    assert parse_srt_bytes(b"\xef\xbb\xbf1\n00:00:00,000 --> 00:00:01,000\nH\xc3\xa9llo") == [SrtCue(1, 0, 1000, "Héllo")]


@pytest.mark.parametrize("text", [
    "x\n00:00:00,000 --> 00:00:01,000\nHi",
    "1\n00:00:00,000 --> 00:00:01,000\nHi\n\n1\n00:00:01,000 --> 00:00:02,000\nAgain",
    "1\n00:00:00,000 --> 00:00:00,000\nHi",
    "1\n00:00:02,000 --> 00:00:01,000\nHi",
    "1\nno-time\nHi",
    "1\n00:00:00,000 --> 00:00:01,000\n   ",
])
def test_malformed_cue_raises_contextual_error(text):
    with pytest.raises(SrtError):
        parse_srt(text)


def test_invalid_utf8_is_rejected():
    with pytest.raises(SrtError, match="UTF-8"):
        parse_srt_bytes(b"\xff\xfe")


def test_validation_accepts_adjacent_cues_and_returns_source_order():
    cues = [SrtCue(7, 0, 1000, "one"), SrtCue(12, 1000, 3000, "two")]
    assert validate_cues(cues) == cues


def test_validation_rejects_overlap_and_bad_order():
    with pytest.raises(SrtError, match="overlap"):
        validate_cues([SrtCue(1, 0, 2000, "a"), SrtCue(2, 1000, 3000, "b")])
    with pytest.raises(SrtError):
        validate_cues([SrtCue(2, 2000, 3000, "a"), SrtCue(1, 1000, 1500, "b")])


def test_validation_enforces_count_text_and_timeline_bounds():
    with pytest.raises(SrtError):
        validate_cues([SrtCue(i, i * 1000, (i + 1) * 1000, "x") for i in range(1, 102)])
    with pytest.raises(SrtError):
        validate_cues([SrtCue(1, 0, 1000, "x" * 4001)])
    with pytest.raises(SrtError):
        validate_cues([SrtCue(1, 0, 1000, "x" * 40000), SrtCue(2, 1000, 2000, "x")])
    with pytest.raises(SrtError):
        validate_cues([SrtCue(1, 0, 1_800_001, "x")])


def test_format_srt_round_trips_and_renumbers():
    cues = [SrtCue(5, 0, 1500, "Xin chào"), SrtCue(9, 1500, 61250, "Dòng một\nDòng hai")]
    text = format_srt(cues)
    assert text.startswith("1\n00:00:00,000 --> 00:00:01,500\nXin chào\n\n2\n")
    assert text.endswith("Dòng hai\n")
    assert parse_srt(text) == [SrtCue(1, 0, 1500, "Xin chào"), SrtCue(2, 1500, 61250, "Dòng một\nDòng hai")]
    assert format_srt([]) == ""
