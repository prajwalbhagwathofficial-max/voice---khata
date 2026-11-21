"""WebSocket JSON message models and binary framing codec for /ws/session.

Binary framing:
  0x01 (client -> server): [1-byte type=0x01][PCM16 LE 16kHz mono 20ms frame]
  0x02 (server -> client): [1-byte type=0x02][4-byte u32 BE turn_id][PCM16 LE audio]
"""

from __future__ import annotations

import struct
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

FRAME_TYPE_UPSTREAM_AUDIO = 0x01
FRAME_TYPE_DOWNSTREAM_AUDIO = 0x02
FRAME_TYPE_UPSTREAM_BILL = 0x03
FRAME_TYPE_DOWNSTREAM_BILL = 0x04


def encode_upstream_audio(pcm_bytes: bytes) -> bytes:
    """Wrap raw 16 kHz mono PCM16 bytes with leading 0x01 frame type."""
    return bytes([FRAME_TYPE_UPSTREAM_AUDIO]) + pcm_bytes


def decode_upstream_audio(frame: bytes) -> bytes | None:
    """Decode a client binary frame; return PCM16 bytes if type 0x01 else None."""
    if len(frame) < 2 or frame[0] != FRAME_TYPE_UPSTREAM_AUDIO:
        return None
    return frame[1:]


def encode_downstream_audio(turn_id: int, pcm_bytes: bytes) -> bytes:
    """Encode downstream TTS PCM16 bytes with [0x02][u32 BE turn_id]."""
    if turn_id < 0 or turn_id > 0xFFFFFFFF:
        raise ValueError("turn_id must fit in unsigned 32-bit integer")
    return struct.pack(">BI", FRAME_TYPE_DOWNSTREAM_AUDIO, turn_id) + pcm_bytes


def decode_downstream_audio(frame: bytes) -> tuple[int, bytes] | None:
    """Decode a server binary frame; return (turn_id, pcm_bytes) if type 0x02 else None."""
    if len(frame) < 5 or frame[0] != FRAME_TYPE_DOWNSTREAM_AUDIO:
        return None
    turn_id = struct.unpack_from(">I", frame, 1)[0]
    return turn_id, frame[5:]


def encode_upstream_bill_chunk(upload_id: int, chunk: bytes) -> bytes:
    """Encode upstream bill binary frame: 0x03 | u32 upload_id | chunk."""
    if upload_id < 0 or upload_id > 0xFFFFFFFF:
        raise ValueError("upload_id must fit in unsigned 32-bit integer")
    return struct.pack(">BI", FRAME_TYPE_UPSTREAM_BILL, upload_id) + chunk


def decode_upstream_bill_chunk(frame: bytes) -> tuple[int, bytes] | None:
    """Decode upstream bill binary frame; return (upload_id, chunk) if type 0x03."""
    if len(frame) < 5 or frame[0] != FRAME_TYPE_UPSTREAM_BILL:
        return None
    upload_id = struct.unpack_from(">I", frame, 1)[0]
    return upload_id, frame[5:]


def encode_downstream_bill_chunk(transfer_id: int, chunk: bytes) -> bytes:
    """Encode downstream bill binary frame: 0x04 | u32 transfer_id | chunk."""
    if transfer_id < 0 or transfer_id > 0xFFFFFFFF:
        raise ValueError("transfer_id must fit in unsigned 32-bit integer")
    return struct.pack(">BI", FRAME_TYPE_DOWNSTREAM_BILL, transfer_id) + chunk


def decode_downstream_bill_chunk(frame: bytes) -> tuple[int, bytes] | None:
    """Decode downstream bill binary frame; return (transfer_id, chunk) if type 0x04."""
    if len(frame) < 5 or frame[0] != FRAME_TYPE_DOWNSTREAM_BILL:
        return None
    transfer_id = struct.unpack_from(">I", frame, 1)[0]
    return transfer_id, frame[5:]


# --- Client -> Server JSON Messages ---
class SessionStartMsg(BaseModel):
    model_config = ConfigDict(extra="ignore")
    t: Literal["session.start"]
    lang: str | None = None


class PttDownMsg(BaseModel):
    model_config = ConfigDict(extra="ignore")
    t: Literal["ptt.down"]
    clip_id: str | None = None
    lang: str | None = None
    mode: Literal["gave", "got"] | None = None


class PttUpMsg(BaseModel):
    model_config = ConfigDict(extra="ignore")
    t: Literal["ptt.up"]


class InterruptMsg(BaseModel):
    model_config = ConfigDict(extra="ignore")
    t: Literal["interrupt"]


class ResetDemoMsg(BaseModel):
    model_config = ConfigDict(extra="ignore")
    t: Literal["reset_demo"]
    profile: Literal["demo_clean", "demo_full"] = "demo_full"


class InjectTextMsg(BaseModel):
    """Used by MockSTT tests/evals to inject a transcript directly over the socket."""

    model_config = ConfigDict(extra="ignore")
    t: Literal["mock.inject", "inject_text"]
    text: str
    lang: str | None = None
    mode: Literal["gave", "got"] | None = None


class ReceivedQueryMsg(BaseModel):
    """Client query for received payments: received.query {date|from,to}."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    t: Literal["received.query"]
    date: str | None = None
    from_date: str | None = Field(default=None, alias="from")
    to_date: str | None = Field(default=None, alias="to")


class CustomerQueryMsg(BaseModel):
    """Client query for customer timeline: customer.query {id}."""

    model_config = ConfigDict(extra="ignore")
    t: Literal["customer.query"]
    id: str


class EntryItemWire(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    qty_text: str | None = None
    amount_paise: int = 0
    amount_inr: int = 0

    def __init__(self, **data: Any) -> None:
        if "amount_paise" not in data and "amount_inr" in data:
            data["amount_paise"] = int(data["amount_inr"]) * 100
        elif "amount_inr" not in data and "amount_paise" in data:
            data["amount_inr"] = int(data["amount_paise"]) // 100
        super().__init__(**data)


class EntryItemsSetMsg(BaseModel):
    """Client message to set or replace items for an existing entry: entry.items.set."""

    model_config = ConfigDict(extra="ignore")
    t: Literal["entry.items.set"]
    event_id: str
    items: list[EntryItemWire]


class BillStartMsg(BaseModel):
    """Initiate a bill upload for an event: bill.start."""

    model_config = ConfigDict(extra="ignore")
    t: Literal["bill.start"]
    upload_id: int
    event_id: str
    mime: str
    size: int
    sha256: str


class BillEndMsg(BaseModel):
    """Conclude a bill upload stream: bill.end."""

    model_config = ConfigDict(extra="ignore")
    t: Literal["bill.end"]
    upload_id: int


class BillGetMsg(BaseModel):
    """Request a bill file download over WS: bill.get."""

    model_config = ConfigDict(extra="ignore")
    t: Literal["bill.get"]
    transfer_id: int
    bill_id: str


class CustomerConsentMsg(BaseModel):
    """Record customer opt-in consent for message sharing: customer.consent."""

    model_config = ConfigDict(extra="ignore")
    t: Literal["customer.consent"]
    customer_id: str


class ShareLoggedMsg(BaseModel):
    """Record sharing handoff event: share.logged (never contains phone number)."""

    model_config = ConfigDict(extra="ignore")
    t: Literal["share.logged"]
    customer_id: str
    event_id: str
    channel: str = "native"


class EventDeleteMsg(BaseModel):
    """Client message to directly delete a transaction event: event.delete."""

    model_config = ConfigDict(extra="ignore")
    t: Literal["event.delete"]
    event_id: str


class EventReverseMsg(BaseModel):
    """Client message to reverse a transaction event: event.reverse."""

    model_config = ConfigDict(extra="ignore")
    t: Literal["event.reverse"]
    event_id: str
    reason: str | None = None


class CustomerCreateMsg(BaseModel):
    """Client message to explicitly add a new customer: customer.create."""

    model_config = ConfigDict(extra="ignore")
    t: Literal["customer.create"]
    name: str
    phone: str | None = None
    initial_credit_paise: int | None = None
    note: str | None = None


ClientMessage = Annotated[
    Union[
        SessionStartMsg,
        PttDownMsg,
        PttUpMsg,
        InterruptMsg,
        ResetDemoMsg,
        InjectTextMsg,
        ReceivedQueryMsg,
        CustomerQueryMsg,
        EntryItemsSetMsg,
        BillStartMsg,
        BillEndMsg,
        BillGetMsg,
        CustomerConsentMsg,
        ShareLoggedMsg,
        EventDeleteMsg,
        EventReverseMsg,
        CustomerCreateMsg,
    ],
    Field(discriminator="t"),
]

_CLIENT_MSG_ADAPTER: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)


def parse_client_message(raw_json: str) -> ClientMessage:
    return _CLIENT_MSG_ADAPTER.validate_json(raw_json)


# --- Server -> Client JSON Messages ---
class ReadyMsg(BaseModel):
    t: Literal["ready"] = "ready"
    session_id: str
    turn_id: int = 0
    sample_rate: int = 48000
    lang: str = "hi-IN"


class SnapshotMsg(BaseModel):
    """Snapshot sent on session start: {summary, customers, recent_events}."""

    t: Literal["snapshot"] = "snapshot"
    summary: dict[str, Any]
    customers: list[dict[str, Any]]
    recent_events: list[dict[str, Any]]


class SummaryMsg(BaseModel):
    """Summary sent after every commit: {owing_count, total_owed_paise, total_owed, ...}."""

    t: Literal["summary"] = "summary"
    owing_count: int
    total_owed_paise: int = 0
    total_owed: int = 0
    total_advance_paise: int = 0
    total_advance: int = 0
    advance_count: int = 0

    def __init__(self, **data: Any) -> None:
        if "total_owed_paise" not in data and "total_owed" in data:
            data["total_owed_paise"] = int(data["total_owed"]) * 100
        elif "total_owed" not in data and "total_owed_paise" in data:
            data["total_owed"] = int(data["total_owed_paise"]) // 100
        if "total_advance_paise" not in data and "total_advance" in data:
            data["total_advance_paise"] = int(data["total_advance"]) * 100
        elif "total_advance" not in data and "total_advance_paise" in data:
            data["total_advance"] = int(data["total_advance_paise"]) // 100
        super().__init__(**data)


class ReceivedResultMsg(BaseModel):
    """Reply to received.query."""

    t: Literal["received.result"] = "received.result"
    range_total_paise: int = 0
    range_total: int = 0
    days: list[dict[str, Any]]

    def __init__(self, **data: Any) -> None:
        if "range_total_paise" not in data and "range_total" in data:
            data["range_total_paise"] = int(data["range_total"]) * 100
        elif "range_total" not in data and "range_total_paise" in data:
            data["range_total"] = int(data["range_total_paise"]) // 100
        super().__init__(**data)


class CustomerResultMsg(BaseModel):
    """Reply to customer.query."""

    t: Literal["customer.result"] = "customer.result"
    customer_id: str
    customer_name: str
    balance_paise: int = 0
    balance: int = 0
    advance_paise: int = 0
    advance: int = 0
    is_advance: bool
    timeline: list[dict[str, Any]]

    def __init__(self, **data: Any) -> None:
        if "balance_paise" not in data and "balance" in data:
            data["balance_paise"] = int(data["balance"]) * 100
        elif "balance" not in data and "balance_paise" in data:
            data["balance"] = int(data["balance_paise"]) // 100
        if "advance_paise" not in data and "advance" in data:
            data["advance_paise"] = int(data["advance"]) * 100
        elif "advance" not in data and "advance_paise" in data:
            data["advance"] = int(data["advance_paise"]) // 100
        super().__init__(**data)


class TurnStartMsg(BaseModel):
    t: Literal["turn.start"] = "turn.start"
    turn_id: int
    sample_rate: int


class TtsEndMsg(BaseModel):
    t: Literal["tts.end"] = "tts.end"
    turn_id: int


class TranscriptMsg(BaseModel):
    t: Literal["transcript"] = "transcript"
    kind: Literal["partial", "final"]
    text: str
    confidence: float | None = None
    turn_id: int | None = None


class ActionMsg(BaseModel):
    t: Literal["action"] = "action"
    turn_id: int
    type: str
    customer: str | None = None
    customer_id: str | None = None
    amount_paise: int | None = None
    amount_inr: int | None = None
    effective_date: str | None = None
    note: str | None = None
    status: str = "recorded"
    correction_applied: bool = False
    direction_overridden: bool = False
    candidates: list[str] | None = None
    event_id: str | None = None
    items: list[dict[str, Any]] | None = None
    items_sum_paise: int | None = None
    items_sum: int | None = None
    stated_total_paise: int | None = None
    stated_total: int | None = None
    question: str | None = None

    def __init__(self, **data: Any) -> None:
        if "amount_paise" not in data and data.get("amount_inr") is not None:
            data["amount_paise"] = int(data["amount_inr"]) * 100
        elif "amount_inr" not in data and data.get("amount_paise") is not None:
            data["amount_inr"] = int(data["amount_paise"]) // 100
        if "items_sum_paise" not in data and data.get("items_sum") is not None:
            data["items_sum_paise"] = int(data["items_sum"]) * 100
        elif "items_sum" not in data and data.get("items_sum_paise") is not None:
            data["items_sum"] = int(data["items_sum_paise"]) // 100
        if "stated_total_paise" not in data and data.get("stated_total") is not None:
            data["stated_total_paise"] = int(data["stated_total"]) * 100
        elif "stated_total" not in data and data.get("stated_total_paise") is not None:
            data["stated_total"] = int(data["stated_total_paise"]) // 100
        super().__init__(**data)


class LedgerRowMsg(BaseModel):
    customer_id: str
    name: str
    script: str
    balance_paise: int = 0
    balance_inr: int = 0
    item_count: int = 0
    has_bill: bool = False
    has_consent: bool = False

    def __init__(self, **data: Any) -> None:
        if "balance_paise" not in data and "balance_inr" in data:
            data["balance_paise"] = int(data["balance_inr"]) * 100
        elif "balance_inr" not in data and "balance_paise" in data:
            data["balance_inr"] = int(data["balance_paise"]) // 100
        super().__init__(**data)


class PaymentItemMsg(BaseModel):
    seq: int
    event_id: str
    customer_id: str
    customer_name: str
    amount_paise: int = 0
    amount_inr: int = 0
    effective_date: str
    ts: int | str
    formatted_time: str = ""
    note: str | None = None
    items: list[dict[str, Any]] = Field(default_factory=list)
    bills: list[dict[str, Any]] = Field(default_factory=list)
    has_bill: bool = False
    bill_id: str | None = None
    bill_mime: str | None = None

    def __init__(self, **data: Any) -> None:
        if "amount_paise" not in data and "amount_inr" in data:
            data["amount_paise"] = int(data["amount_inr"]) * 100
        elif "amount_inr" not in data and "amount_paise" in data:
            data["amount_inr"] = int(data["amount_paise"]) // 100
        super().__init__(**data)


class CreditItemMsg(BaseModel):
    seq: int
    event_id: str
    customer_id: str
    customer_name: str
    amount_paise: int = 0
    amount_inr: int = 0
    effective_date: str
    ts: int | str
    formatted_time: str = ""
    note: str | None = None
    items: list[dict[str, Any]] = Field(default_factory=list)
    bills: list[dict[str, Any]] = Field(default_factory=list)
    has_bill: bool = False
    bill_id: str | None = None
    bill_mime: str | None = None

    def __init__(self, **data: Any) -> None:
        if "amount_paise" not in data and "amount_inr" in data:
            data["amount_paise"] = int(data["amount_inr"]) * 100
        elif "amount_inr" not in data and "amount_paise" in data:
            data["amount_inr"] = int(data["amount_paise"]) // 100
        super().__init__(**data)


class LedgerSummaryMsg(BaseModel):
    debtor_count: int = 0
    total_owed_paise: int = 0
    total_owed: int = 0
    total_received_today_paise: int = 0
    total_received_today: int = 0

    def __init__(self, **data: Any) -> None:
        if "total_owed_paise" not in data and "total_owed" in data:
            data["total_owed_paise"] = int(data["total_owed"]) * 100
        elif "total_owed" not in data and "total_owed_paise" in data:
            data["total_owed"] = int(data["total_owed_paise"]) // 100
        if "total_received_today_paise" not in data and "total_received_today" in data:
            data["total_received_today_paise"] = int(data["total_received_today"]) * 100
        elif "total_received_today" not in data and "total_received_today_paise" in data:
            data["total_received_today"] = int(data["total_received_today_paise"]) // 100
        super().__init__(**data)


class LedgerMsg(BaseModel):
    t: Literal["ledger"] = "ledger"
    rows: list[LedgerRowMsg] = Field(default_factory=list)
    summary: LedgerSummaryMsg = Field(default_factory=LedgerSummaryMsg)
    payments: list[PaymentItemMsg] = Field(default_factory=list)
    credits: list[CreditItemMsg] = Field(default_factory=list)


class BillAckMsg(BaseModel):
    t: Literal["bill.ack"] = "bill.ack"
    upload_id: int
    received_bytes: int


class BillDoneMsg(BaseModel):
    t: Literal["bill.done"] = "bill.done"
    upload_id: int
    bill_id: str
    event_id: str


class BillErrorMsg(BaseModel):
    t: Literal["bill.error"] = "bill.error"
    upload_id: int | None = None
    code: str
    message: str


class BillMetaMsg(BaseModel):
    t: Literal["bill.meta"] = "bill.meta"
    transfer_id: int
    bill_id: str
    mime: str
    size: int
    sha256: str


class BillGetDoneMsg(BaseModel):
    t: Literal["bill.get_done"] = "bill.get_done"
    transfer_id: int
    bill_id: str


class EntryItemsResultMsg(BaseModel):
    t: Literal["entry.items.result"] = "entry.items.result"
    event_id: str
    status: str
    items: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None


class CustomerConsentResultMsg(BaseModel):
    t: Literal["customer.consent.result"] = "customer.consent.result"
    customer_id: str
    has_consent: bool


class CustomerCreateResultMsg(BaseModel):
    """Server notification after creating a customer: customer.create.result."""

    t: Literal["customer.create.result"] = "customer.create.result"
    status: Literal["success", "error"]
    customer_id: str | None = None
    customer_name: str | None = None
    event_id: str | None = None
    error: str | None = None


class EventDeleteResultMsg(BaseModel):
    """Server notification after directly deleting an event: event.delete.result."""

    t: Literal["event.delete.result"] = "event.delete.result"
    event_id: str
    status: Literal["success", "error"]
    error: str | None = None


class EventReverseResultMsg(BaseModel):
    """Server notification after reversing an event: event.reverse.result."""

    t: Literal["event.reverse.result"] = "event.reverse.result"
    event_id: str
    status: Literal["success", "error"]
    error: str | None = None


class FlushMsg(BaseModel):
    t: Literal["flush"] = "flush"
    turn_id: int | None = None


class ErrorMsg(BaseModel):
    t: Literal["error"] = "error"
    message: str
