"""Chữ hiển thị trong app nằm ở locales/<ngôn ngữ>.json. Thêm ngôn ngữ mới chỉ cần thêm file."""

import json
from pathlib import Path

LOCALES_DIR = Path(__file__).parent / "locales"

_strings: dict[str, str] = {}


def load(language: str) -> None:
    global _strings
    _strings = json.loads((LOCALES_DIR / f"{language}.json").read_text(encoding="utf-8"))


def tr(key: str, **params: object) -> str:
    """Trả về chữ theo khóa; thiếu khóa thì trả chính khóa để dễ phát hiện."""
    text = _strings.get(key, key)
    return text.format(**params) if params else text
