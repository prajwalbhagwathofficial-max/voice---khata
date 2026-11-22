"""Domain models for Voice Khata with strict integer-rupee invariants."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_TRANSACTION_RUPEES = 10_000_000

_WHITESPACE_RE = re.compile(r"\s+")


class LanguageCode(StrEnum):
    HI_IN = "hi-IN"
    KN_IN = "kn-IN"
    TE_IN = "te-IN"
    EN_IN = "en-IN"

    @classmethod
    def normalize(cls, value: str | None, default: LanguageCode | None = None) -> LanguageCode:
        fallback = default or cls.HI_IN
        if not value:
            return fallback
        cleaned = value.strip().lower()
        if cleaned in {"hi", "hi-in", "hindi", "hinglish", "hi-en"}:
            return cls.HI_IN
        if cleaned in {"kn", "kn-in", "kannada", "kanglish", "kn-en"}:
            return cls.KN_IN
        if cleaned in {"te", "te-in", "telugu", "tenglish", "te-en"}:
            return cls.TE_IN
        if cleaned in {"en", "en-in", "en-us", "english"}:
            return cls.EN_IN
        return fallback


class EventType(StrEnum):
    CREDIT_GIVEN = "CREDIT_GIVEN"
    PAYMENT_RECEIVED = "PAYMENT_RECEIVED"
    REVERSAL = "REVERSAL"


class IntentType(StrEnum):
    RECORD_CREDIT = "RECORD_CREDIT"
    RECORD_PAYMENT = "RECORD_PAYMENT"
    UNDO_LAST = "UNDO_LAST"
    QUERY_BALANCE = "QUERY_BALANCE"
    QUERY_TOTAL = "QUERY_TOTAL"
    QUERY_HISTORY = "QUERY_HISTORY"
    CONFIRM = "CONFIRM"
    CANCEL = "CANCEL"
    UNSUPPORTED_LENDING = "UNSUPPORTED_LENDING"
    UNKNOWN = "UNKNOWN"


class SessionState(StrEnum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    PROCESSING = "PROCESSING"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    SPEAKING = "SPEAKING"


def validate_strict_positive_rupees(value: Any) -> int:
    """Enforce integer rupees only: reject bool, float, decimals, and non-positive values."""
    if isinstance(value, bool):
        raise ValueError("Rupee amount must be an integer, not boolean")
    if isinstance(value, float):
        raise ValueError("Rupee amount must be a whole integer (no paise/decimals)")
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped.isdigit():
            raise ValueError("Rupee amount must be a whole positive integer string")
        value = int(stripped)
    if not isinstance(value, int):
        raise ValueError("Rupee amount must be an integer")
    if value <= 0:
        raise ValueError("Rupee amount must be greater than zero")
    if value > MAX_TRANSACTION_RUPEES:
        raise ValueError(f"Rupee amount exceeds maximum allowed ({MAX_TRANSACTION_RUPEES})")
    return value


def validate_strict_signed_rupees(value: Any) -> int:
    """Enforce integer rupees for computed balances or signed ledger deltas."""
    if isinstance(value, bool) or isinstance(value, float) or not isinstance(value, int):
        raise ValueError("Signed rupee value must be a strict Python int")
    return value


def normalize_customer_name(name: str) -> str:
    """Collapse whitespace and casefold for deterministic customer lookup."""
    return _WHITESPACE_RE.sub(" ", name.strip()).casefold()


def clean_display_name(name: str) -> str:
    """Clean display name while preserving script (Devanagari/Kannada/Latin)."""
    cleaned = _WHITESPACE_RE.sub(" ", name.strip())
    if not cleaned:
        raise ValueError("Customer name cannot be empty")
    if len(cleaned) > 80:
        raise ValueError("Customer name too long")
    return cleaned


PositiveRupees = Annotated[int, Field(gt=0, le=MAX_TRANSACTION_RUPEES)]


class ParsedIntent(BaseModel):
    """Structured intent extracted from a transcript.

    Never carries computed arithmetic or LLM-generated response text.
    """

    model_config = ConfigDict(extra="forbid")

    intent: IntentType
    customer_name: str | None = None
    amount_rupees: int | None = None
    item_note: str = ""
    language_code: LanguageCode = LanguageCode.HI_IN
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @field_validator("amount_rupees", mode="before")
    @classmethod
    def _validate_amount(cls, value: Any) -> int | None:
        if value is None or value == "":
            return None
        return validate_strict_positive_rupees(value)

    @field_validator("customer_name", mode="before")
    @classmethod
    def _validate_customer_name(cls, value: Any) -> str | None:
        if value is None:
            return None
        text = _WHITESPACE_RE.sub(" ", str(value).strip())
        return text or None

    @field_validator("item_note", mode="before")
    @classmethod
    def _validate_item_note(cls, value: Any) -> str:
        if value is None:
            return ""
        return _WHITESPACE_RE.sub(" ", str(value).strip())[:120]

    @field_validator("language_code", mode="before")
    @classmethod
    def _validate_language(cls, value: Any) -> LanguageCode:
        if isinstance(value, LanguageCode):
            return value
        return LanguageCode.normalize(str(value) if value is not None else None)


class LedgerEvent(BaseModel):
    """Immutable append-only ledger event row."""

    model_config = ConfigDict(frozen=True)

    id: int
    event_uid: str
    created_at_utc: str
    created_at_mono_ns: int
    customer_id: int
    customer_name: str
    event_type: EventType
    signed_amount_rupees: int
    reverses_event_id: int | None = None
    item_note: str = ""
    transcript: str = ""
    language_code: LanguageCode = LanguageCode.HI_IN

    @field_validator("signed_amount_rupees", mode="before")
    @classmethod
    def _strict_signed_int(cls, value: Any) -> int:
        val = validate_strict_signed_rupees(value)
        if val == 0:
            raise ValueError("Ledger event signed_amount_rupees cannot be zero")
        return val


class CustomerBalance(BaseModel):
    """Computed balance for a single customer (never stored on customer row)."""

    model_config = ConfigDict(frozen=True)

    customer_id: int
    customer_name: str
    balance_rupees: int
    event_count: int
    total_credit_rupees: int = 0
    total_payment_rupees: int = 0

    @field_validator("balance_rupees", mode="before")
    @classmethod
    def _strict_balance_int(cls, value: Any) -> int:
        return validate_strict_signed_rupees(value)


class ShopSummary(BaseModel):
    """Computed shop-wide totals derived purely from append-only events."""

    model_config = ConfigDict(frozen=True)

    total_outstanding_rupees: int
    customer_count: int
    active_credit_customers: int
    total_events: int

    @field_validator("total_outstanding_rupees", mode="before")
    @classmethod
    def _strict_total_int(cls, value: Any) -> int:
        return validate_strict_signed_rupees(value)


class PendingAction(BaseModel):
    """A validated write operation waiting for shopkeeper voice/UI confirmation."""

    model_config = ConfigDict(frozen=True)

    action_id: str
    intent: IntentType
    language_code: LanguageCode
    customer_name: str | None = None
    amount_rupees: int | None = None
    item_note: str = ""
    transcript: str = ""
    target_event_id: int | None = None

    @field_validator("amount_rupees", mode="before")
    @classmethod
    def _strict_amount(cls, value: Any) -> int | None:
        if value is None:
            return None
        return validate_strict_positive_rupees(value)
