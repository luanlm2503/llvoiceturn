import json

from llvoice_shared.errors import ErrorCode

from llvoice import i18n


def test_every_error_code_has_vietnamese_text() -> None:
    strings = json.loads((i18n.LOCALES_DIR / "vi.json").read_text(encoding="utf-8"))
    missing = [code.value for code in ErrorCode if f"error.{code.value}" not in strings]
    assert missing == []


def test_tr_fills_params() -> None:
    i18n.load("vi")
    assert "5" in i18n.tr("error.credit_insufficient", remaining=5, required=9)
