"""Tests for receipt generation and public receipt endpoints (/receipt/{event_id}, /r/{event_id}).

Verifies that:
1. Clicking receipt link shows a real (non-mock) Neo-Brutalist receipt saying:
   "आपका उधार VoiceKhata पर जोड़ दिया गया है" / "Your Udhar has been successfully added on VoiceKhata"
2. Formatted amounts in INR and paise arithmetic are accurate (e.g. ₹500).
3. Itemised breakdown table and attached bills are accurately rendered.
4. Short URL alias /r/{event_id} works identically.
5. Non-existent transactions return a clean 404 receipt not found page.
"""

from __future__ import annotations

from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from server.app import create_server_app
from server.config import ServerSettings
from server.ledger import (
    EntryItemInput,
    EntryItemRecord,
    BillRecord,
    Ledger,
    LedgerEventInput,
    LedgerEventRecord,
    LedgerEventType,
    generate_key_b64,
)
from server.receipt import render_receipt_html, render_receipt_not_found_html


def test_render_receipt_html_credit_udhar_content() -> None:
    """Receipt must state 'आपका उधार VoiceKhata पर जोड़ दिया गया है' with correct details."""
    event = LedgerEventRecord(
        seq=1,
        event_id="ev_test_123",
        type=LedgerEventType.CREDIT,
        customer_id="c1",
        amount_paise=50000,  # ₹500.00
        business_date="2026-11-21",
        effective_date="2026-11-21",
        ts=1795273764886,
        note="5kg Aashirvaad Atta, 1kg Toor Dal",
    )
    items = [
        EntryItemRecord(id="i1", event_id="ev_test_123", name="Aashirvaad Atta 5kg", qty_text="1 pack", amount_paise=32000),
        EntryItemRecord(id="i2", event_id="ev_test_123", name="Toor Dal", qty_text="1 kg", amount_paise=18000),
    ]
    bills = [
        BillRecord(id="b1", event_id="ev_test_123", mime="image/jpeg", size=2048, sha256="abc123hash", path="dummy.jpg", created_ts=1795273764886)
    ]

    html = render_receipt_html(
        event=event,
        customer_name="Ramesh Kumar",
        balance_inr=1500,
        items=items,
        bills=bills,
        shop_name="Gupta Kirana Store",
    )

    # 1. Verification of the user requested message banner
    assert "आपका उधार VoiceKhata पर जोड़ दिया गया है" in html
    assert "Your Udhar has been successfully added on VoiceKhata" in html

    # 2. Details
    assert "Ramesh Kumar" in html
    assert "500" in html
    assert "Gupta Kirana Store" in html
    assert "2026-11-21" in html

    # 3. Items breakdown
    assert "Aashirvaad Atta 5kg" in html
    assert "320" in html
    assert "Toor Dal" in html
    assert "180" in html

    # 4. Bill attachment preview
    assert "/receipt/ev_test_123/bill/b1" in html

    # 5. Outstanding balance
    assert "1,500" in html


def test_render_receipt_html_payment_content() -> None:
    """Payment receipts state that payment was recorded successfully."""
    event = LedgerEventRecord(
        seq=2,
        event_id="ev_pay_456",
        type=LedgerEventType.PAYMENT,
        customer_id="c2",
        amount_paise=20000,  # ₹200.00
        business_date="2026-11-21",
        effective_date="2026-11-21",
        ts=1795273764886,
    )
    html = render_receipt_html(
        event=event,
        customer_name="Sita Devi",
        balance_inr=300,
        shop_name="Sri Balaji Provisions",
    )

    assert "आपकी जमा राशि VoiceKhata पर दर्ज कर ली गई है" in html
    assert "Your Payment has been successfully recorded on VoiceKhata" in html
    assert "Sita Devi" in html
    assert "200" in html
    assert "Sri Balaji Provisions" in html


def test_render_receipt_not_found_html() -> None:
    """Not found page renders clearly with Neo-Brutalist design."""
    html = render_receipt_not_found_html("non_existent_id_999")
    assert "रसीद नहीं मिली" in html
    assert "Receipt Not Found" in html
    assert "non_existent_id_999" in html


@pytest.mark.asyncio
async def test_receipt_endpoint_real_db(tmp_path: Path) -> None:
    """Full HTTP test: create real transaction in SQLite ledger and fetch /receipt/{event_id}."""
    db_file = tmp_path / "receipt_test.db"
    key = generate_key_b64()
    ledger = Ledger(db_file, enc_key=key)

    # 1. Add customer
    cust = await ledger.add_customer("Mahesh Anna", customer_id="c_mahesh")

    # 2. Add credit event (₹500 = 50000 paise)
    ev = await ledger.record(
        LedgerEventInput(
            event_id="ev_rec_500",
            type=LedgerEventType.CREDIT,
            customer_id=cust.id,
            amount_paise=50000,
            effective_date="2026-11-21",
            note="Groceries purchase",
        )
    )

    # 3. Add itemised breakdown
    await ledger.set_event_items(
        ev.event_id,
        [
            EntryItemInput(name="Rice 5kg", qty_text="1 bag", amount_paise=35000),
            EntryItemInput(name="Sugar 2kg", qty_text="2 kg", amount_paise=15000),
        ],
    )

    # 4. Attach bill
    dummy_jpg = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 32
    await ledger.attach_bill(
        event_id=ev.event_id,
        mime="image/jpeg",
        data_or_path=dummy_jpg,
    )

    # 5. Create app using this ledger
    settings = ServerSettings(
        data_dir=tmp_path,
        ledger_db=db_file,
        ledger_enc_key=key,
        client_dist_dir=Path("client/dist"),
        shop_name="Anna Kirana General Store",
    )
    app = create_server_app(
        settings=settings,
        ledger=ledger,
        auto_seed=False,
        prewarm_tts_cache=False,
    )

    client = TestClient(app)

    # Test GET /receipt/{event_id}
    res = client.get(f"/receipt/{ev.event_id}")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/html")
    assert "आपका उधार VoiceKhata पर जोड़ दिया गया है" in res.text
    assert "Your Udhar has been successfully added on VoiceKhata" in res.text
    assert "Mahesh Anna" in res.text
    assert "500" in res.text
    assert "Rice 5kg" in res.text
    assert "Sugar 2kg" in res.text
    assert "Anna Kirana General Store" in res.text

    # Test short URL /r/{event_id}
    res_short = client.get(f"/r/{ev.event_id}")
    assert res_short.status_code == 200
    assert "आपका उधार VoiceKhata पर जोड़ दिया गया है" in res_short.text

    # Test bill image download
    bills = await ledger.get_event_bills(ev.event_id)
    assert len(bills) == 1
    bill_id = bills[0].id
    res_bill = client.get(f"/receipt/{ev.event_id}/bill/{bill_id}")
    assert res_bill.status_code == 200
    assert res_bill.headers["content-type"] == "image/jpeg"
    assert res_bill.content == dummy_jpg

    # Test 404 for invalid event
    res_404 = client.get("/receipt/invalid_random_event_id")
    assert res_404.status_code == 404
    assert "रसीद नहीं मिली" in res_404.text
