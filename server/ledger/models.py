"""Strict domain models for server.ledger (KhataSaathi integer paise model)."""

from __future__ import annotations

import time
import uuid
from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LedgerEventType(StrEnum):
    CREDIT_GIVEN = "CREDIT_GIVEN"
    PAYMENT_RECEIVED = "PAYMENT_RECEIVED"
    REVERSAL = "REVERSAL"
    CONSENT = "CONSENT"
    SHARE_LOGGED = "SHARE_LOGGED"

    # Backward-compatible aliases
    CREDIT = "CREDIT_GIVEN"
    PAYMENT = "PAYMENT_RECEIVED"
    VOID = "REVERSAL"


def _strict_int_paise(value: Any, *, allow_zero: bool = False) -> int:
    """Enforce that value is a strict Python integer representing paise."""
    if isinstance(value, bool) or isinstance(value, float) or not isinstance(value, int):
        raise ValueError("amount_paise must be a strict Python integer")
    if allow_zero:
        if value < 0:
            raise ValueError("amount_paise cannot be negative")
    else:
        if value <= 0:
            raise ValueError("amount_paise must be a positive integer")
    return value


def _normalize_date_str(value: date | datetime | str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).date().isoformat()
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return date.fromisoformat(str(value).strip()).isoformat()


def _normalize_ts_epoch_ms(value: Any) -> int:
    """Normalize timestamp representation to integer UTC epoch milliseconds."""
    if value is None:
        return int(time.time() * 1000)
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned.isdigit() or (cleaned.startswith("-") and cleaned[1:].isdigit()):
            return int(cleaned)
        try:
            dt = datetime.fromisoformat(cleaned)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except Exception:
            pass
    raise ValueError(f"Invalid timestamp value: {value!r}")


class CustomerRecord(BaseModel):
    """Decrypted customer view returned by the ledger API."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    name_norm: str
    script: str
    created_ts: str

    @property
    def customer_id(self) -> str:
        return self.id


class CustomerAliasRecord(BaseModel):
    """Customer alias mapping for phonetic name matching."""

    model_config = ConfigDict(frozen=True)

    id: str
    customer_id: str
    alias_norm: str
    alias_enc: bytes


class CommandAuditRecord(BaseModel):
    """Immutable audit record for idempotent commands."""

    model_config = ConfigDict(frozen=True)

    command_id: str
    session_id: str | None = None
    parsed_intent_json: str | None = None
    confidence_band: str | None = None
    validation_result: str | None = None
    committed_event_ids: str | None = None
    transcript_enc: bytes | None = None
    created_ts: int = Field(default_factory=lambda: int(time.time() * 1000))


class EntryItemInput(BaseModel):
    """Input representation of an item on an entry (in integer paise)."""

    model_config = ConfigDict(frozen=True)

    name: str
    qty_text: str | None = None
    amount_paise: int

    def __init__(self, **data: Any) -> None:
        if "amount_paise" not in data and "amount_inr" in data:
            data["amount_paise"] = int(data["amount_inr"]) * 100
        super().__init__(**data)

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("Item name cannot be empty")
        return s

    @field_validator("amount_paise")
    @classmethod
    def _positive_amount(cls, v: int) -> int:
        return _strict_int_paise(v, allow_zero=False)

    @property
    def amount_inr(self) -> int:
        return self.amount_paise // 100


class EntryItemRecord(BaseModel):
    """Decrypted item stored in entry_items table (in integer paise)."""

    model_config = ConfigDict(frozen=True)

    id: str
    event_id: str
    name: str
    qty_text: str | None = None
    amount_paise: int

    @property
    def amount_inr(self) -> int:
        return self.amount_paise // 100


class BillRecord(BaseModel):
    """Metadata record for a bill attached to an event."""

    model_config = ConfigDict(frozen=True)

    id: str
    event_id: str
    mime: str
    size: int
    sha256: str
    path: str
    created_ts: int


class LedgerEventInput(BaseModel):
    """Input payload for Ledger.post_event(event)."""

    model_config = ConfigDict(frozen=True)

    command_id: str | None = None
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    type: LedgerEventType
    customer_id: str
    amount_paise: int
    business_date: str | None = None
    effective_date: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).date().isoformat()
    )
    date_phrase: str | None = None
    ts: int = Field(default_factory=lambda: int(time.time() * 1000))
    source_session_id: str | None = None
    reverses_event_id: str | None = None
    note: str | None = None
    session_scope: str | None = None
    items: list[EntryItemInput] = Field(default_factory=list)

    def __init__(self, **data: Any) -> None:
        if "amount_paise" not in data and "amount_inr" in data:
            data["amount_paise"] = int(data["amount_inr"]) * 100
        if "reverses_event_id" not in data and "voids_event_id" in data:
            data["reverses_event_id"] = data["voids_event_id"]
        super().__init__(**data)

    @field_validator("event_id", "customer_id")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Identifier cannot be empty")
        return cleaned

    @field_validator("effective_date", mode="before")
    @classmethod
    def _validate_effective_date(cls, value: Any) -> str:
        return _normalize_date_str(value)

    @field_validator("ts", mode="before")
    @classmethod
    def _validate_ts(cls, value: Any) -> int:
        return _normalize_ts_epoch_ms(value)

    @field_validator("amount_paise", mode="before")
    @classmethod
    def _validate_amount(cls, value: Any, info: Any) -> int:
        ev_type = info.data.get("type")
        allow_zero = ev_type in (
            LedgerEventType.REVERSAL,
            LedgerEventType.CONSENT,
            LedgerEventType.SHARE_LOGGED,
        )
        return _strict_int_paise(value, allow_zero=allow_zero)

    @property
    def amount_inr(self) -> int:
        return self.amount_paise // 100

    @property
    def voids_event_id(self) -> str | None:
        return self.reverses_event_id


class LedgerEventRecord(BaseModel):
    """Immutable decrypted ledger event stored in SQLite."""

    model_config = ConfigDict(frozen=True)

    seq: int
    event_id: str
    command_id: str | None = None
    type: LedgerEventType
    customer_id: str
    amount_paise: int
    business_date: str
    effective_date: str
    date_phrase: str | None = None
    ts: int
    source_session_id: str | None = None
    reverses_event_id: str | None = None
    note: str | None = None
    session_scope: str | None = None
    items: list[EntryItemRecord] = Field(default_factory=list)
    bills: list[BillRecord] = Field(default_factory=list)

    @property
    def amount_inr(self) -> int:
        return self.amount_paise // 100

    @property
    def voids_event_id(self) -> str | None:
        return self.reverses_event_id


class CustomerBalanceRecord(BaseModel):
    """Computed customer balance derived purely from SQL aggregate."""

    model_config = ConfigDict(frozen=True)

    customer: CustomerRecord
    balance_paise: int
    item_count: int = 0
    has_bill: bool = False
    has_consent: bool = False

    @field_validator("balance_paise", mode="before")
    @classmethod
    def _strict_signed_int(cls, value: Any) -> int:
        if isinstance(value, bool) or isinstance(value, float) or not isinstance(value, int):
            raise ValueError("balance_paise must be a strict Python integer")
        return value

    @property
    def balance_inr(self) -> int:
        return self.balance_paise // 100


class SummaryResult(BaseModel):
    """Summary query result: {owing_count, total_owed, ...}."""

    model_config = ConfigDict(frozen=True)

    owing_count: int
    total_owed_paise: int
    total_advance_paise: int = 0
    advance_count: int = 0

    @property
    def total_owed(self) -> int:
        return self.total_owed_paise // 100

    @property
    def total_advance(self) -> int:
        return self.total_advance_paise // 100


class ReceivedPaymentItem(BaseModel):
    """Non-reversed payment entry in the received query."""

    model_config = ConfigDict(frozen=True)

    seq: int
    event_id: str
    customer_id: str
    customer_name: str
    amount_paise: int
    ts: int
    ist_time: str
    effective_date: str
    business_date: str
    note: str | None = None

    @property
    def amount_inr(self) -> int:
        return self.amount_paise // 100


class ReceivedDayGroup(BaseModel):
    """Day-grouped payments in Asia/Kolkata."""

    model_config = ConfigDict(frozen=True)

    date: str
    total_paise: int
    count: int
    items: list[ReceivedPaymentItem]

    @property
    def total(self) -> int:
        return self.total_paise // 100


class ReceivedResult(BaseModel):
    """Received payments query result grouped by IST day."""

    model_config = ConfigDict(frozen=True)

    range_total_paise: int
    days: list[ReceivedDayGroup]

    @property
    def range_total(self) -> int:
        return self.range_total_paise // 100


class TimelineEventItem(BaseModel):
    """Single event item in customer timeline."""

    model_config = ConfigDict(frozen=True)

    seq: int
    event_id: str
    type: LedgerEventType
    amount_paise: int
    ts: int
    ist_time: str
    business_date: str
    effective_date: str
    note: str | None = None
    running_balance_paise: int
    is_reversal: bool = False
    is_reversed: bool = False
    reverses_event_id: str | None = None
    reversed_by_event_id: str | None = None
    items: list[EntryItemRecord] = Field(default_factory=list)
    bills: list[BillRecord] = Field(default_factory=list)
    share_logs: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def amount_inr(self) -> int:
        return self.amount_paise // 100

    @property
    def running_balance(self) -> int:
        return self.running_balance_paise // 100


class CustomerTimelineResult(BaseModel):
    """Customer timeline query result."""

    model_config = ConfigDict(frozen=True)

    customer_id: str
    customer_name: str
    balance_paise: int
    advance_paise: int
    is_advance: bool
    events: list[TimelineEventItem]

    @property
    def balance(self) -> int:
        return self.balance_paise // 100

    @property
    def advance(self) -> int:
        return self.advance_paise // 100


# Backward-compatible aliases
class PaymentRecord(BaseModel):
    model_config = ConfigDict(frozen=True)
    seq: int
    event_id: str
    customer_id: str
    customer_name: str
    amount_paise: int
    effective_date: str
    ts: Any
    formatted_time: str
    note: str | None = None
    items: list[EntryItemRecord] = Field(default_factory=list)
    bills: list[BillRecord] = Field(default_factory=list)

    @property
    def amount_inr(self) -> int:
        return self.amount_paise // 100


class CreditRecord(BaseModel):
    model_config = ConfigDict(frozen=True)
    seq: int
    event_id: str
    customer_id: str
    customer_name: str
    amount_paise: int
    effective_date: str
    ts: Any
    formatted_time: str
    note: str | None = None
    items: list[EntryItemRecord] = Field(default_factory=list)
    bills: list[BillRecord] = Field(default_factory=list)

    @property
    def amount_inr(self) -> int:
        return self.amount_paise // 100


class LedgerSummaryStats(BaseModel):
    model_config = ConfigDict(frozen=True)
    debtor_count: int
    total_owed_paise: int
    total_received_today_paise: int

    @property
    def total_owed(self) -> int:
        return self.total_owed_paise // 100

    @property
    def total_received_today(self) -> int:
        return self.total_received_today_paise // 100
