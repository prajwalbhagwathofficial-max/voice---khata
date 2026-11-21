import urllib.parse
import pytest
from voice_khata.models import LanguageCode
from voice_khata.templates import (
    BANNED_PRESSURE_WORDS,
    render_item_sum_mismatch,
    render_share_receipt,
    validate_no_pressure_words,
)
from server.protocol import (
    FRAME_TYPE_UPSTREAM_BILL,
    FRAME_TYPE_DOWNSTREAM_BILL,
    encode_upstream_bill_chunk,
    decode_upstream_bill_chunk,
    encode_downstream_bill_chunk,
    decode_downstream_bill_chunk,
    parse_client_message,
    EntryItemsSetMsg,
    BillStartMsg,
    BillEndMsg,
    BillGetMsg,
    CustomerConsentMsg,
    ShareLoggedMsg,
)


def test_validate_no_pressure_words():
    for word in BANNED_PRESSURE_WORDS:
        with pytest.raises(ValueError, match="banned pressure word"):
            validate_no_pressure_words(f"Please pay this {word} now")

    validate_no_pressure_words("Thank you for shopping at Kirana Store. Your balance is 500.")


def test_render_item_sum_mismatch():
    hi = render_item_sum_mismatch("hi", 330, 340)
    assert "330" in hi and "340" in hi
    validate_no_pressure_words(hi)

    te = render_item_sum_mismatch("te", 250, 300)
    assert "250" in te and "300" in te
    validate_no_pressure_words(te)

    en = render_item_sum_mismatch("en", 100, 150)
    assert "100" in en and "150" in en
    validate_no_pressure_words(en)


def test_render_share_receipt_languages_and_pressure_free():
    items = [
        {"name": "Rice", "qty_text": "2 kg", "amount_inr": 120},
        {"name": "Sugar", "qty_text": "1 kg", "amount_inr": 45},
    ]

    for lang in ["en", "hi", "te", "kn"]:
        receipt = render_share_receipt(
            lang=lang,
            shop_name="Gupta General Store",
            date_str="2026-11-20",
            customer_name="Ramesh",
            items=items,
            entry_amount=165,
            current_balance=450,
        )
        validate_no_pressure_words(receipt)
        assert "Gupta General Store" in receipt
        assert "2026-11-20" in receipt
        assert "Ramesh" in receipt
        assert "165" in receipt
        assert "450" in receipt

        # Test URL encoding for whatsapp / sms link
        encoded = urllib.parse.quote(receipt)
        decoded = urllib.parse.unquote(encoded)
        assert decoded == receipt


def test_bill_framing_codecs():
    data = b"\x00\x01\x02\x03test-payload"
    up_frame = encode_upstream_bill_chunk(42, data)
    assert up_frame[0] == FRAME_TYPE_UPSTREAM_BILL
    decoded_up = decode_upstream_bill_chunk(up_frame)
    assert decoded_up is not None
    assert decoded_up[0] == 42
    assert decoded_up[1] == data

    down_frame = encode_downstream_bill_chunk(101, data)
    assert down_frame[0] == FRAME_TYPE_DOWNSTREAM_BILL
    decoded_down = decode_downstream_bill_chunk(down_frame)
    assert decoded_down is not None
    assert decoded_down[0] == 101
    assert decoded_down[1] == data


def test_client_message_parsing():
    raw_items_set = '{"t": "entry.items.set", "event_id": "ev-1", "items": [{"name": "dal", "amount_inr": 120}]}'
    msg = parse_client_message(raw_items_set)
    assert isinstance(msg, EntryItemsSetMsg)
    assert msg.event_id == "ev-1"
    assert msg.items[0].name == "dal"
    assert msg.items[0].amount_inr == 120

    raw_bill_start = '{"t": "bill.start", "upload_id": 1, "event_id": "ev-1", "mime": "image/jpeg", "size": 1024, "sha256": "abc"}'
    msg_bs = parse_client_message(raw_bill_start)
    assert isinstance(msg_bs, BillStartMsg)
    assert msg_bs.upload_id == 1

    raw_bill_end = '{"t": "bill.end", "upload_id": 1}'
    msg_be = parse_client_message(raw_bill_end)
    assert isinstance(msg_be, BillEndMsg)

    raw_bill_get = '{"t": "bill.get", "transfer_id": 7, "bill_id": "b-1"}'
    msg_bg = parse_client_message(raw_bill_get)
    assert isinstance(msg_bg, BillGetMsg)

    raw_consent = '{"t": "customer.consent", "customer_id": "c-1"}'
    msg_c = parse_client_message(raw_consent)
    assert isinstance(msg_c, CustomerConsentMsg)

    raw_share = '{"t": "share.logged", "customer_id": "c-1", "event_id": "ev-1", "channel": "whatsapp"}'
    msg_s = parse_client_message(raw_share)
    assert isinstance(msg_s, ShareLoggedMsg)
