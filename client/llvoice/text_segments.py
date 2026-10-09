"""Canonical text-to-segment splitting for local batch synthesis."""

import re
from dataclasses import dataclass

SPLIT_PUNCTUATION = "punctuation"
SPLIT_LINES = "lines"
SPLIT_PARAGRAPHS = "paragraphs"
SPLIT_NONE = "none"
DEFAULT_DELIMITERS = ".,/"


@dataclass(frozen=True)
class SplitOptions:
    mode: str = SPLIT_PUNCTUATION
    delimiters: str = DEFAULT_DELIMITERS
    merge_limit: int = 0  # 0 = không gộp


def _pattern(options: SplitOptions) -> str | None:
    if options.mode == SPLIT_NONE:
        return None
    if options.mode == SPLIT_LINES:
        return r"\n+"
    if options.mode == SPLIT_PARAGRAPHS:
        return r"\n[ \t]*\n+"
    chars = "".join(re.escape(ch) for ch in dict.fromkeys(options.delimiters) if not ch.isspace())
    return f"[{chars}\n]+"


def split_text_segments(text: str, options: SplitOptions = SplitOptions()) -> list[str]:
    """Split text by the chosen mode, trimming and dropping empties.

    Line breaks always split in punctuation mode. When merge_limit > 0, neighbouring
    pieces are joined (keeping their punctuation) while the result stays within the limit.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    pattern = _pattern(options)
    parts = re.split(f"({pattern})", text) if pattern else [text]
    pieces: list[tuple[str, str]] = []  # (đoạn, dấu tách phía sau)
    for position in range(0, len(parts), 2):
        segment = " ".join(parts[position].split())
        delimiter = parts[position + 1] if position + 1 < len(parts) else ""
        if segment:
            pieces.append((segment, delimiter))
        elif pieces:
            pieces[-1] = (pieces[-1][0], pieces[-1][1] + delimiter)
    if options.merge_limit <= 0:
        return [segment for segment, _ in pieces]
    merged: list[str] = []
    current, trailing = "", ""
    for segment, delimiter in pieces:
        kept = "".join(ch for ch in trailing if not ch.isspace() and ch != "/")
        candidate = f"{current}{kept} {segment}" if current else segment
        if current and len(candidate) > options.merge_limit:
            merged.append(current)
            current = segment
        else:
            current = candidate
        trailing = delimiter
    if current:
        merged.append(current)
    return merged
