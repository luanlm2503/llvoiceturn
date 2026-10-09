"""SubRip parsing and timeline validation for local dubbing."""

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class SrtCue:
    index: int
    start_ms: int
    end_ms: int
    text: str


class SrtError(ValueError):
    """Invalid SRT content or edited timeline."""


_TIME = re.compile(r"^(\d{2,}):(\d{2}):(\d{2}),(\d{3})$")
_RANGE = re.compile(r"^\s*(\d{2,}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2,}:\d{2}:\d{2},\d{3})\s*$")
_MAX_CUES = 100
_MAX_CUE_TEXT = 4000
_MAX_TOTAL_TEXT = 40000
_MAX_TIMELINE_MS = 30 * 60 * 1000


def _parse_time(value: str, block: int) -> int:
    match = _TIME.fullmatch(value)
    if not match:
        raise SrtError(f"Block {block}: invalid timestamp {value!r}.")
    hours, minutes, seconds, milliseconds = map(int, match.groups())
    if minutes >= 60 or seconds >= 60:
        raise SrtError(f"Block {block}: timestamp minutes and seconds must be below 60.")
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + milliseconds


def format_time(value: int) -> str:
    hours, remainder = divmod(int(value), 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"

def format_timing(start_ms: int, end_ms: int) -> str:
    return f"{format_time(start_ms)} --> {format_time(end_ms)}"

def parse_timing(value: str, block: int) -> tuple[int, int]:
    """Đọc lại mốc "00:00:01,000 --> 00:00:02,000" người dùng sửa trong bảng."""
    match = _RANGE.fullmatch(value)
    if not match:
        raise SrtError(f"Block {block}: invalid timestamp range {value!r}.")
    return _parse_time(match.group(1), block), _parse_time(match.group(2), block)

def format_srt(cues: list[SrtCue]) -> str:
    """Ghi lại SubRip chuẩn (đánh số lại từ 1, dòng trống giữa các cue)."""
    blocks = [
        f"{number}\n{format_timing(cue.start_ms, cue.end_ms)}\n{cue.text.strip()}"
        for number, cue in enumerate(cues, 1)
    ]
    return "\n\n".join(blocks) + "\n" if blocks else ""

def parse_srt_bytes(content: bytes) -> list[SrtCue]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SrtError("SRT must be valid UTF-8 text.") from exc
    return parse_srt(text)


def parse_srt(content: str) -> list[SrtCue]:
    normalized = content.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n[ \t]*\n+", normalized.strip()) if normalized.strip() else []
    cues: list[SrtCue] = []
    seen: set[int] = set()
    for block_number, block in enumerate(blocks, 1):
        lines = block.split("\n")
        if len(lines) < 3 or not re.fullmatch(r"\s*\d+\s*", lines[0]):
            raise SrtError(f"Block {block_number}: expected cue number, timestamp range, and text.")
        index = int(lines[0].strip())
        if index <= 0 or index in seen:
            raise SrtError(f"Block {block_number}, cue {index}: cue number must be positive and unique.")
        range_match = _RANGE.fullmatch(lines[1])
        if not range_match:
            raise SrtError(f"Block {block_number}, cue {index}: invalid timestamp range.")
        start_ms = _parse_time(range_match.group(1), block_number)
        end_ms = _parse_time(range_match.group(2), block_number)
        text_value = "\n".join(lines[2:]).strip()
        if not text_value:
            raise SrtError(f"Block {block_number}, cue {index}: subtitle text is empty.")
        cues.append(SrtCue(index, start_ms, end_ms, text_value))
        seen.add(index)
    return validate_cues(cues)


def validate_cues(cues: list[SrtCue]) -> list[SrtCue]:
    if not 1 <= len(cues) <= _MAX_CUES:
        raise SrtError(f"SRT must contain 1 to {_MAX_CUES} cues.")
    seen: set[int] = set()
    total_text = 0
    previous: SrtCue | None = None
    normalized: list[SrtCue] = []
    for position, cue in enumerate(cues, 1):
        if cue.index <= 0 or cue.index in seen:
            raise SrtError(f"Cue {position}: cue number must be positive and unique.")
        if not isinstance(cue.start_ms, int) or not isinstance(cue.end_ms, int) or cue.start_ms < 0 or cue.start_ms >= cue.end_ms:
            raise SrtError(f"Cue {cue.index}: start time must be nonnegative and before end time.")
        if cue.end_ms > _MAX_TIMELINE_MS:
            raise SrtError(f"Cue {cue.index}: timeline cannot exceed 30 minutes.")
        text_value = cue.text.strip()
        if not text_value:
            raise SrtError(f"Cue {cue.index}: subtitle text is empty.")
        if len(text_value) > _MAX_CUE_TEXT:
            raise SrtError(f"Cue {cue.index}: text cannot exceed 4,000 characters.")
        total_text += len(text_value)
        if total_text > _MAX_TOTAL_TEXT:
            raise SrtError("Total subtitle text cannot exceed 40,000 characters.")
        if previous is not None:
            if cue.start_ms < previous.start_ms:
                raise SrtError(f"Cue {cue.index}: cues must be ordered by start time.")
            if cue.start_ms < previous.end_ms:
                raise SrtError(f"Cue {cue.index}: overlaps cue {previous.index}.")
        normalized_cue = SrtCue(cue.index, cue.start_ms, cue.end_ms, text_value)
        normalized.append(normalized_cue)
        previous = normalized_cue
        seen.add(cue.index)
    return normalized
