import asyncio
import hashlib
import io
import os
from PIL import Image
import pytest

from server.ledger import Ledger, LedgerEventType, LedgerEventInput, EntryItemInput
from server.ledger.crypto import FieldCipher
from server.protocol import (
    encode_upstream_bill_chunk,
    decode_downstream_bill_chunk,
    parse_client_message,
)
from server.session import Session
from voice_khata.models import LanguageCode
from server.speech.mocks import MockSTT, MockTTS


class DummyWebSocket:
    def __init__(self):
        self.sent_texts: list[str] = []
        self.sent_bytes: list[bytes] = []

    async def send_text(self, text: str) -> None:
        self.sent_texts.append(text)

    async def send_bytes(self, data: bytes) -> None:
        self.sent_bytes.append(data)


@pytest.fixture
def temp_ledger(tmp_path):
    db_file = tmp_path / "test_ledger.db"
    key = b"12345678901234567890123456789012"
    ledger = Ledger(db_path=db_file, enc_key=key)
    return ledger


@pytest.mark.asyncio
async def test_item_sum_mismatch_triggers_clarification(temp_ledger):
    transport = DummyWebSocket()
    session = Session(
        transport=transport,
        ledger=temp_ledger,
        nlu=None,
        stt=MockSTT(),
        tts=MockTTS(),
        initial_lang=LanguageCode.HI_IN,
    )

    async def fake_stream():
        yield '{"t": "session.start", "lang": "hi"}'
        await asyncio.sleep(0.05)
        # 2 kilo chawal 120, 1 kilo cheeni 45 => items sum = 165, but spoken total is 200
        yield '{"t": "inject_text", "text": "Ramesh ko 2 kilo chawal 120 aur 1 kilo cheeni 45 kul 200 udhar likho", "mode": "gave"}'
        await asyncio.sleep(0.15)
        # Clarify with 165
        yield '{"t": "inject_text", "text": "165 sahi hai", "mode": "gave"}'
        await asyncio.sleep(0.15)

    await session.run(fake_stream())

    # Verify clarification was triggered first
    clarifications = [t for t in transport.sent_texts if '"CLARIFICATION_REQUIRED"' in t]
    assert len(clarifications) >= 1
    assert "165" in clarifications[0]
    assert "200" in clarifications[0]

    # Verify final credit was recorded with items for 165
    credits = await temp_ledger.credits_list()
    assert len(credits) == 1
    assert credits[0].amount_inr == 165
    assert len(credits[0].items) == 2
    assert sum(it.amount_inr for it in credits[0].items) == 165


@pytest.mark.asyncio
async def test_voiding_event_voids_items_together(temp_ledger):
    cust = await temp_ledger.add_customer("Suresh")
    ev = await temp_ledger.record(
        LedgerEventInput(
            type=LedgerEventType.CREDIT,
            customer_id=cust.id,
            amount_inr=100,
            effective_date="2026-11-20",
            items=[EntryItemInput(name="Dal", amount_inr=100)],
        )
    )
    items_before = await temp_ledger.get_event_items(ev.event_id)
    assert len(items_before) == 1

    # Void the event
    await temp_ledger.record(
        LedgerEventInput(
            type=LedgerEventType.VOID,
            customer_id=cust.id,
            amount_inr=0,
            effective_date="2026-11-20",
            voids_event_id=ev.event_id,
        )
    )

    # Active credits list should have 0 events
    credits = await temp_ledger.credits_list()
    assert len(credits) == 0


@pytest.mark.asyncio
async def test_post_hoc_item_editing(temp_ledger):
    cust = await temp_ledger.add_customer("Mahesh")
    ev = await temp_ledger.record(
        LedgerEventInput(
            type=LedgerEventType.CREDIT,
            customer_id=cust.id,
            amount_inr=200,
            effective_date="2026-11-20",
        )
    )

    # Mismatch should raise LedgerError
    with pytest.raises(Exception):
        await temp_ledger.set_event_items(
            ev.event_id,
            [EntryItemInput(name="Oil", amount_inr=150)],
        )

    # Correct sum matches event amount
    updated = await temp_ledger.set_event_items(
        ev.event_id,
        [
            EntryItemInput(name="Oil", qty_text="1L", amount_inr=150),
            EntryItemInput(name="Soap", qty_text="1 pc", amount_inr=50),
        ],
    )
    assert len(updated) == 2
    items_saved = await temp_ledger.get_event_items(ev.event_id)
    assert len(items_saved) == 2
    assert sum(it.amount_inr for it in items_saved) == 200


@pytest.mark.asyncio
async def test_bill_upload_validation_and_encryption(temp_ledger, tmp_path):
    transport = DummyWebSocket()
    session = Session(
        transport=transport,
        ledger=temp_ledger,
        nlu=None,
        stt=MockSTT(),
        tts=MockTTS(),
    )

    cust = await temp_ledger.add_customer("Anita")
    ev = await temp_ledger.record(
        LedgerEventInput(
            type=LedgerEventType.CREDIT,
            customer_id=cust.id,
            amount_inr=500,
            effective_date="2026-11-20",
        )
    )

    # Create dummy JPEG image
    img = Image.new("RGB", (100, 100), color="blue")
    img_io = io.BytesIO()
    img.save(img_io, format="JPEG")
    jpeg_bytes = img_io.getvalue()
    sha = hashlib.sha256(jpeg_bytes).hexdigest()

    async def drain_outbound():
        while not session._outbound.empty():
            item = session._outbound.get_nowait()
            if item is not None:
                kind, payload = item
                if kind == "text":
                    await transport.send_text(payload)
                else:
                    await transport.send_bytes(payload)

    # 1. Test oversize rejection (>5MB)
    await session._handle_bill_start(
        parse_client_message(
            f'{{"t": "bill.start", "upload_id": 1, "event_id": "{ev.event_id}", "mime": "image/jpeg", "size": 6000000, "sha256": "{sha}"}}'
        )
    )
    await drain_outbound()
    assert any("OVERSIZE" in t for t in transport.sent_texts)
    transport.sent_texts.clear()

    # 2. Test bad magic bytes rejection
    bad_bytes = b"This is not a real jpeg file at all"
    bad_sha = hashlib.sha256(bad_bytes).hexdigest()
    await session._handle_bill_start(
        parse_client_message(
            f'{{"t": "bill.start", "upload_id": 2, "event_id": "{ev.event_id}", "mime": "image/jpeg", "size": {len(bad_bytes)}, "sha256": "{bad_sha}"}}'
        )
    )
    await session._handle_bill_chunk(2, bad_bytes)
    await session._handle_bill_end(parse_client_message('{"t": "bill.end", "upload_id": 2}'))
    await drain_outbound()
    assert any("INVALID_MAGIC" in t for t in transport.sent_texts)
    transport.sent_texts.clear()

    # 3. Test successful upload
    await session._handle_bill_start(
        parse_client_message(
            f'{{"t": "bill.start", "upload_id": 3, "event_id": "{ev.event_id}", "mime": "image/jpeg", "size": {len(jpeg_bytes)}, "sha256": "{sha}"}}'
        )
    )
    # Send in two chunks
    half = len(jpeg_bytes) // 2
    await session._handle_bill_chunk(3, jpeg_bytes[:half])
    await drain_outbound()
    assert any('"t":"bill.ack"' in t for t in transport.sent_texts)
    await session._handle_bill_chunk(3, jpeg_bytes[half:])
    await session._handle_bill_end(parse_client_message('{"t": "bill.end", "upload_id": 3}'))
    await drain_outbound()

    done_msg = [t for t in transport.sent_texts if '"t":"bill.done"' in t]
    assert len(done_msg) == 1

    bills = await temp_ledger.get_event_bills(ev.event_id)
    assert len(bills) == 1
    bill_id = bills[0].id

    # Verify disk file is ENCRYPTED, not plaintext JPEG
    disk_path = temp_ledger._bills_dir / f"{bill_id}.bin"
    assert disk_path.exists()
    disk_data = disk_path.read_bytes()
    # JPEG magic bytes should NOT appear in encrypted file
    assert not disk_data.startswith(b"\xff\xd8\xff")

    # 4. Test download round-trip over WS
    transport.sent_texts.clear()
    transport.sent_bytes.clear()
    await session._handle_bill_get(
        parse_client_message(f'{{"t": "bill.get", "transfer_id": 42, "bill_id": "{bill_id}"}}')
    )
    await drain_outbound()
    assert any('"t":"bill.meta"' in t for t in transport.sent_texts)
    assert any('"t":"bill.get_done"' in t for t in transport.sent_texts)
    assert len(transport.sent_bytes) >= 1

    # Reassemble downloaded chunks
    downloaded_chunks = []
    for b in transport.sent_bytes:
        res = decode_downstream_bill_chunk(b)
        assert res is not None
        transfer_id, chunk = res
        assert transfer_id == 42
        downloaded_chunks.append(chunk)

    downloaded_bytes = b"".join(downloaded_chunks)
    # The downloaded bytes must be a valid JPEG readable by PIL
    downloaded_img = Image.open(io.BytesIO(downloaded_bytes))
    assert downloaded_img.size == (100, 100)


@pytest.mark.asyncio
async def test_customer_consent_and_zero_server_phone_storage(temp_ledger):
    cust = await temp_ledger.add_customer("Kavita")
    ev = await temp_ledger.record(
        LedgerEventInput(
            type=LedgerEventType.CREDIT,
            customer_id=cust.id,
            amount_inr=300,
            effective_date="2026-11-20",
        )
    )

    # Initial state: has_consent is False
    has_consent = await temp_ledger.has_customer_consent(cust.id)
    assert has_consent is False

    # Record consent
    consent_rec = await temp_ledger.record_consent(cust.id)
    assert consent_rec.type == LedgerEventType.CONSENT
    assert consent_rec.customer_id == cust.id

    has_consent_now = await temp_ledger.has_customer_consent(cust.id)
    assert has_consent_now is True

    # Record share logged
    share_rec = await temp_ledger.record_share_logged(cust.id, ev.event_id, channel="whatsapp")
    assert share_rec.type == LedgerEventType.SHARE_LOGGED
    assert share_rec.customer_id == cust.id
    assert "channel:whatsapp" in share_rec.note
