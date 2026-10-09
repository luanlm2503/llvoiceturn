"""Models giai đoạn 1 (task 1.03): 8 bảng proxy dùng.

Không gồm `customer_voices` và các bảng giai đoạn 2 khác — ngoài phạm vi task này.
Mọi mốc thời gian đều timezone-aware. Khóa bí mật (API key MiniMax, token)
không bao giờ lưu thô: chỉ lưu bản mã hóa hoặc hash SHA-256.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class SlotStatus(StrEnum):
    """Trạng thái slot MiniMax (task 1.11)."""

    ACTIVE = "active"
    COOLING = "cooling"
    AUTH_FAILED = "auth_failed"
    DISABLED = "disabled"


class SlotType(StrEnum):
    API = "api"  # API key chính thức (giai đoạn 1)
    WEB = "web"  # cookie/tài khoản web (giai đoạn 4)


class PlanKind(StrEnum):
    """Loại gói (task 1.31): plan tạo mới/gia hạn, topup chỉ cộng credit."""

    PLAN = "plan"
    TOPUP = "topup"


class CustomerStatus(StrEnum):
    ACTIVE = "active"
    LOCKED = "locked"  # admin khóa tay (task 1.54)


class UsageStatus(StrEnum):
    """Vòng đời một khoản trừ credit (task 1.20, 1.23)."""

    RESERVED = "reserved"  # giữ tạm, chờ MiniMax trả kết quả
    COMMITTED = "committed"  # chốt theo usage_characters thực tế
    RELEASED = "released"  # hoàn trả do lỗi/timeout


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Customer(TimestampMixin, Base):
    """Tài khoản khách. Credit còn = plan_credits + bonus_credits − used_credits."""

    __tablename__ = "customers"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'locked')", name="ck_customers_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # Đăng nhập theo quyết định 0.01 (chưa chốt): để nullable để hỗ trợ cả 2 phương án
    username: Mapped[str | None] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))  # argon2 (task 1.30)
    hwid: Mapped[str | None] = mapped_column(
        String(64), unique=True
    )  # SHA-256 hex, khóa sau lần kích hoạt đầu (task 1.33)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=CustomerStatus.ACTIVE.value, server_default="active"
    )
    plan_credits: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    bonus_credits: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    used_credits: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    plan_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_ip: Mapped[str | None] = mapped_column(String(45))

    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(
        back_populates="customer", cascade="all, delete-orphan"
    )
    usage_entries: Mapped[list["UsageLog"]] = relationship(back_populates="customer")
    used_keys: Mapped[list["ActivationKey"]] = relationship(back_populates="used_by")


class Plan(TimestampMixin, Base):
    """Gói do admin tự cấu hình (task 1.51): tên, loại, thời hạn, credit, ghi chú."""

    __tablename__ = "plans"
    __table_args__ = (CheckConstraint("kind IN ('plan', 'topup')", name="ck_plans_kind"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default=PlanKind.PLAN.value, server_default="plan"
    )
    duration_days: Mapped[int | None] = mapped_column(Integer)  # None với gói topup
    credits: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    note: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")

    activation_keys: Mapped[list["ActivationKey"]] = relationship(back_populates="plan")


class ActivationKey(TimestampMixin, Base):
    """Key kích hoạt dùng một lần. Chỉ lưu hash SHA-256, không lưu key gốc (task 1.31)."""

    __tablename__ = "activation_keys"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"), nullable=False, index=True)
    batch_id: Mapped[str | None] = mapped_column(
        String(64), index=True
    )  # nhóm key sinh hàng loạt để xuất CSV (task 1.53)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    used_by_customer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL"), index=True
    )

    plan: Mapped[Plan] = relationship(back_populates="activation_keys")
    used_by: Mapped[Customer | None] = relationship(back_populates="used_keys")


class AccountSlot(TimestampMixin, Base):
    """Một tài khoản MiniMax trong pool xoay vòng (task 1.11)."""

    __tablename__ = "account_slots"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'cooling', 'auth_failed', 'disabled')",
            name="ck_account_slots_status",
        ),
        CheckConstraint("type IN ('api', 'web')", name="ck_account_slots_type"),
        Index("ix_account_slots_status_cooldown", "status", "cooldown_until"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    type: Mapped[str] = mapped_column(
        String(16), nullable=False, default=SlotType.API.value, server_default="api"
    )
    api_key_enc: Mapped[str | None] = mapped_column(Text)  # mã hóa AES-GCM ở tầng app (task 1.04)
    cookie_enc: Mapped[str | None] = mapped_column(Text)  # slot web, giai đoạn 4
    group_name: Mapped[str | None] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=SlotStatus.ACTIVE.value, server_default="active"
    )
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Đếm số lần 1002/1039 trong cửa sổ hiện tại để ưu tiên slot ít lỗi nhất
    rate_limit_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    rate_limit_window_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    usage_entries: Mapped[list["UsageLog"]] = relationship(back_populates="slot")


class UsageLog(Base):
    """Nhật ký sử dụng để audit và tính lại credit (task 1.20, 1.23). Bất biến, không sửa."""

    __tablename__ = "usage_log"
    __table_args__ = (
        CheckConstraint(
            "status IN ('reserved', 'committed', 'released')", name="ck_usage_log_status"
        ),
        Index("ix_usage_log_customer_created", "customer_id", "created_at"),
        Index("ix_usage_log_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customers.id"), nullable=False, index=True
    )
    slot_id: Mapped[int | None] = mapped_column(
        ForeignKey("account_slots.id"), index=True
    )  # null nếu lỗi trước khi chọn slot
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=UsageStatus.RESERVED.value, server_default="reserved"
    )
    chars_reserved: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    chars_used: Mapped[int | None] = mapped_column(BigInteger)  # chốt sau khi MiniMax trả về
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    voice_id: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    customer: Mapped[Customer] = relationship(back_populates="usage_entries")
    slot: Mapped[AccountSlot | None] = relationship(back_populates="usage_entries")


class RefreshToken(Base):
    """Refresh token xoay vòng, khóa 1-1 với HWID (task 1.32). Chỉ lưu hash SHA-256."""

    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    hwid: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    customer: Mapped[Customer] = relationship(back_populates="refresh_tokens")


class SystemSetting(Base):
    """Cấu hình key-value, gồm credit_unit_chars (task 1.05)."""

    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class SystemVoice(Base):
    """Giọng hệ thống nhập tay từ tài liệu MiniMax (task 1.42)."""

    __tablename__ = "system_voices"

    voice_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    model_compatibility: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
