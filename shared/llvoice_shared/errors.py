"""Mã lỗi proxy trả về trong trường `code`. App dịch mã này sang tiếng Việt qua file locales."""

from enum import StrEnum


class ErrorCode(StrEnum):
    INTERNAL = "internal_error"
    VALIDATION = "validation_error"
    NOT_FOUND = "not_found"
    RATE_LIMITED = "rate_limited"
    APP_UPDATE_REQUIRED = "app_update_required"

    # Đăng nhập, khóa máy
    UNAUTHORIZED = "unauthorized"
    TOKEN_EXPIRED = "token_expired"
    HWID_MISMATCH = "hwid_mismatch"
    SESSION_REPLACED = "session_replaced"
    ACCOUNT_LOCKED = "account_locked"

    # Key kích hoạt, credit
    KEY_INVALID = "key_invalid"
    KEY_USED = "key_used"
    PLAN_EXPIRED = "plan_expired"
    CREDIT_INSUFFICIENT = "credit_insufficient"

    # Tạo giọng
    TEXT_TOO_LONG = "text_too_long"
    VOICE_NOT_FOUND = "voice_not_found"
    NO_SLOT_AVAILABLE = "no_slot_available"
    UPSTREAM_ERROR = "upstream_error"


HEADER_HWID = "X-HWID"
HEADER_APP_VERSION = "X-App-Version"
