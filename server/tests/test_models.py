"""Test models task 1.03. Chạy trên SQLite in-memory, không cần PostgreSQL live."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

import app.models  # noqa: F401 — đăng ký metadata trước khi create_all
from app.db import Base
from app.models import (
    AccountSlot,
    ActivationKey,
    Customer,
    Plan,
    RefreshToken,
    SlotStatus,
    SystemSetting,
    SystemVoice,
    UsageLog,
)

EXPECTED_TABLES = {
    "customers",
    "plans",
    "activation_keys",
    "account_slots",
    "usage_log",
    "refresh_tokens",
    "system_settings",
    "system_voices",
}


def test_metadata_covers_exactly_8_tables() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES
    assert "customer_voices" not in Base.metadata.tables  # giai đoạn 2, ngoài phạm vi


def test_primary_keys_and_foreign_keys() -> None:
    tables = Base.metadata.tables
    assert list(tables["customers"].primary_key.columns)[0].name == "id"
    assert list(tables["system_settings"].primary_key.columns)[0].name == "key"
    assert list(tables["system_voices"].primary_key.columns)[0].name == "voice_id"
    fk_targets = {
        fk.parent.table.name: fk.column.table.name
        for table in tables.values()
        for fk in table.foreign_keys
    }
    assert fk_targets["activation_keys"] in {"plans", "customers"}
    assert fk_targets["refresh_tokens"] == "customers"


def test_unique_columns() -> None:
    tables = Base.metadata.tables
    assert tables["customers"].c.username.unique
    assert tables["customers"].c.hwid.unique
    assert tables["activation_keys"].c.key_hash.unique
    assert tables["refresh_tokens"].c.token_hash.unique
    assert tables["plans"].c.name.unique


def test_activation_key_stores_hash_not_raw_key() -> None:
    cols = {c.name for c in Base.metadata.tables["activation_keys"].columns}
    assert "key_hash" in cols
    assert "key" not in cols  # không bao giờ lưu key gốc


def test_slot_status_and_setting_columns_exist() -> None:
    slot_cols = {c.name for c in Base.metadata.tables["account_slots"].columns}
    assert {"status", "type", "api_key_enc", "cooldown_until"} <= slot_cols
    assert [s.value for s in SlotStatus] == ["active", "cooling", "auth_failed", "disabled"]
    assert "key" in {c.name for c in Base.metadata.tables["system_settings"].columns}


def test_timestamps_are_timezone_aware() -> None:
    from sqlalchemy import DateTime

    for table in Base.metadata.tables.values():
        for col in table.columns.values():
            if isinstance(col.type, DateTime) and col.name.endswith("_at"):
                assert col.type.timezone, f"{table.name}.{col.name} phải timezone-aware"


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as sess:
        yield sess
    engine.dispose()


def test_sqlite_roundtrip_plan_key_customer(session: Session) -> None:
    plan = Plan(name="Gói tháng", kind="plan", duration_days=30, credits=500_000)
    session.add(plan)
    session.flush()
    customer = Customer(username="khach01", plan_credits=500_000)
    session.add(customer)
    session.flush()
    key = ActivationKey(
        key_hash="ab" * 32,
        plan_id=plan.id,
        used_at=datetime.now(UTC),
        used_by_customer_id=customer.id,
    )
    session.add(key)
    session.commit()
    assert session.query(ActivationKey).count() == 1
    assert key.used_by.username == "khach01"
    assert key.plan.name == "Gói tháng"


def test_system_setting_credit_unit_chars(session: Session) -> None:
    session.add(
        SystemSetting(key="credit_unit_chars", value="1000", description="1 credit = 1000 ký tự")
    )
    session.commit()
    assert session.get(SystemSetting, "credit_unit_chars").value == "1000"


def test_system_voice_and_slot_defaults(session: Session) -> None:
    voice = SystemVoice(
        voice_id="dieu-chip", name="Dịu Chip", language="vi", model_compatibility=["speech-02-hd"]
    )
    slot = AccountSlot(name="slot-01")
    session.add_all([voice, slot])
    session.commit()
    assert session.get(AccountSlot, slot.id).status == SlotStatus.ACTIVE.value
    assert voice.model_compatibility == ["speech-02-hd"]


def test_cascade_delete_refresh_tokens(session: Session) -> None:
    customer = Customer(username="khach02")
    session.add(customer)
    session.flush()
    session.add(
        RefreshToken(
            customer_id=customer.id,
            token_hash="cd" * 32,
            hwid="ef" * 32,
            expires_at=datetime(2030, 1, 1, tzinfo=UTC),
        )
    )
    session.commit()
    session.delete(customer)
    session.commit()
    assert session.query(RefreshToken).count() == 0


def test_usage_log_links_customer_and_slot(session: Session) -> None:
    customer = Customer(username="khach03")
    slot = AccountSlot(name="slot-02", api_key_enc="enc-placeholder")
    session.add_all([customer, slot])
    session.flush()
    session.add(
        UsageLog(
            customer_id=customer.id,
            slot_id=slot.id,
            chars_reserved=1000,
            chars_used=980,
            model="speech-02-hd",
            voice_id="dieu-chip",
            status="committed",
        )
    )
    session.commit()
    entry = session.query(UsageLog).one()
    assert entry.customer.username == "khach03"
    assert entry.slot.name == "slot-02"


def test_reserved_usages_findable_for_cleanup_job(session: Session) -> None:
    """Task 1.23 cần truy vấn nhanh các khoản reserve treo — index phải tồn tại."""
    indexes = {
        idx["name"]
        for idx in inspect(session.bind).get_indexes("usage_log")
        if idx["name"] is not None
    }
    assert "ix_usage_log_status_created" in indexes
    assert "ix_usage_log_customer_created" in indexes
    session.execute(text("SELECT 1 FROM usage_log WHERE status = 'reserved'"))
