import asyncio
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from server.app import create_server_app
from server.config import ServerSettings
from server.ledger import Ledger
from server.session import detect_spoken_direction


def test_spoken_direction_heuristics():
    print("--- 1. Testing Spoken Direction Language Detection ---")
    
    # Credit / You Got / Inflow test cases
    credit_cases = [
        ("Ramesh 500 credit", "got"),
        ("Suresh 200 credited", "got"),
        ("Ramesh 100 credits", "got"),
        ("Ramesh 500 जमा", "got"),
        ("Meena Devi ne dhai sau rupaye jama kar diye", "got"),
        ("Ramesh 300 paid", "got"),
        ("Suresh 150 payment", "got"),
        ("Ramesh 400 received", "got"),
        ("Ramesh 200 mila", "got"),
        ("Suresh ne 300 diya", "got"),
        ("Ramesh 500 deposit", "got"),
    ]
    for text, expected in credit_cases:
        res = detect_spoken_direction(text, mode_hint=None)
        assert res == expected, f"Failed for '{text}': expected {expected}, got {res}"
        print(f"  ✓ '{text}' -> {res} (Credit / You Got)")

    # Debit / You Gave / Outflow test cases
    debit_cases = [
        ("Ramesh 500 debit", "gave"),
        ("Suresh 200 debited", "gave"),
        ("Ramesh 300 udhaar", "gave"),
        ("Ramesh Kumar ke teen sau chaalis rupaye udhaar likh do", "gave"),
        ("Ramesh 250 khata", "gave"),
        ("Ramesh ko 200 diya", "gave"),
        ("Ramesh 150 lent", "gave"),
        ("Ramesh 500 gave", "gave"),
    ]
    for text, expected in debit_cases:
        res = detect_spoken_direction(text, mode_hint=None)
        assert res == expected, f"Failed for '{text}': expected {expected}, got {res}"
        print(f"  ✓ '{text}' -> {res} (Debit / You Gave)")

    # Precedence test: "udhaar" or "debit" overrides "credit"
    prec_cases = [
        ("Ramesh 500 udhaar credit", "gave"),
        ("Ramesh 500 debit khata", "gave"),
    ]
    for text, expected in prec_cases:
        res = detect_spoken_direction(text, mode_hint=None)
        assert res == expected, f"Failed for '{text}': expected {expected}, got {res}"
        print(f"  ✓ Precedence '{text}' -> {res} (Debit / You Gave)")


def test_full_pipeline_persistence():
    print("\n--- 2. Testing End-to-End WebSocket & SQLite Persistence ---")
    cfg = ServerSettings(use_mocks=True)
    app = create_server_app(settings=cfg, auto_seed=True)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/session") as ws:
            ws.send_text(json.dumps({"t": "session.start", "lang": "hi-IN"}))
            ready = ws.receive_json()
            assert ready["t"] == "ready"

            # Drain initial snapshot, ledger, and TTS greeting
            while True:
                pkt = ws.receive()
                if pkt.get("text"):
                    m = json.loads(pkt["text"])
                    if m.get("t") == "tts.end":
                        break

            # TEST 1: Inject text "Ramesh Kumar 500 credit" (Direct match -> PAYMENT / You Got)
            print("  -> Injecting: 'Ramesh Kumar 500 credit'...")
            ws.send_text(json.dumps({
                "t": "inject_text",
                "text": "Ramesh Kumar 500 credit",
                "mode": "gave"  # Even if user was on gave screen, language "credit" must override!
            }))

            credit_action = None
            while True:
                pkt = ws.receive()
                if pkt.get("text"):
                    m = json.loads(pkt["text"])
                    if m.get("t") == "action":
                        credit_action = m
                    elif m.get("t") == "tts.end":
                        break

            assert credit_action is not None, "Did not receive action for credit injection"
            assert credit_action["type"] == "PAYMENT", f"Expected PAYMENT, got {credit_action['type']}"
            assert credit_action["amount_inr"] == 500
            assert "You Got" in credit_action.get("status", "")
            print(f"  ✓ Direct Action recognized as '{credit_action['type']}' ₹{credit_action['amount_inr']} with status: {credit_action.get('status')}")

            # TEST 2: Inject text "Suresh Patel 300 debit" (Direct match -> CREDIT / You Gave)
            print("  -> Injecting: 'Suresh Patel 300 debit'...")
            ws.send_text(json.dumps({
                "t": "inject_text",
                "text": "Suresh Patel 300 debit",
                "mode": "got"  # Even if user was on got screen, language "debit" must override!
            }))

            debit_action = None
            while True:
                pkt = ws.receive()
                if pkt.get("text"):
                    m = json.loads(pkt["text"])
                    if m.get("t") == "action":
                        debit_action = m
                    elif m.get("t") == "tts.end":
                        break

            assert debit_action is not None, "Did not receive action for debit injection"
            assert debit_action["type"] == "CREDIT", f"Expected CREDIT, got {debit_action['type']}"
            assert debit_action["amount_inr"] == 300
            assert "You Gave" in debit_action.get("status", "")
            print(f"  ✓ Direct Action recognized as '{debit_action['type']}' ₹{debit_action['amount_inr']} with status: {debit_action.get('status')}")

            # TEST 3: Disambiguation turn ("Ramesh 200 credit" -> DISAMBIGUATE -> "Sharma" -> PAYMENT)
            print("  -> Testing disambiguation: 'Ramesh 200 credit'...")
            ws.send_text(json.dumps({
                "t": "inject_text",
                "text": "Ramesh 200 credit",
                "mode": "got"
            }))

            disam_action = None
            while True:
                pkt = ws.receive()
                if pkt.get("text"):
                    m = json.loads(pkt["text"])
                    if m.get("t") == "action":
                        disam_action = m
                    elif m.get("t") == "tts.end":
                        break

            assert disam_action is not None
            assert disam_action["type"] == "DISAMBIGUATE"
            assert "candidates" in disam_action and len(disam_action["candidates"]) >= 2
            print(f"  ✓ Disambiguation triggered with candidates: {disam_action['candidates']}")

            print("  -> Resolving disambiguation with candidate 'Sharma'...")
            ws.send_text(json.dumps({
                "t": "inject_text",
                "text": "Sharma",
                "mode": "got"
            }))

            resolved_action = None
            while True:
                pkt = ws.receive()
                if pkt.get("text"):
                    m = json.loads(pkt["text"])
                    if m.get("t") == "action":
                        resolved_action = m
                    elif m.get("t") == "tts.end":
                        break

            assert resolved_action is not None
            assert resolved_action["type"] == "PAYMENT"
            assert "Sharma" in resolved_action.get("customer", "")
            print(f"  ✓ Disambiguation resolved: {resolved_action['type']} for {resolved_action.get('customer')} ₹{resolved_action.get('amount_inr')}")

    # TEST 3: Verify directly in SQLite Database
    print("\n--- 3. Verifying SQLite Database Contents ---")
    db_path = cfg.data_dir / "ledger.db"
    ledger_direct = Ledger(db_path, enc_key=cfg.effective_db_encryption_key())

    async def verify_db():
        # Verify payments_list contains Ramesh 500
        payments = await ledger_direct.payments_list()
        ramesh_payment = next((p for p in payments if "Ramesh" in p.customer_name and p.amount_inr == 500), None)
        assert ramesh_payment is not None, f"Ramesh ₹500 payment not found in payments_list! Payments: {payments[:3]}"
        print(f"  ✓ Ramesh ₹500 found in SQLite payments_list (You Got section):")
        print(f"    - Event ID: {ramesh_payment.event_id}")
        print(f"    - Effective Date: {ramesh_payment.effective_date}")
        print(f"    - Timestamp: {ramesh_payment.formatted_time or ramesh_payment.ts}")
        print(f"    - Amount: ₹{ramesh_payment.amount_inr}")

        # Verify received summary grouping for You Got section
        received_summary = await ledger_direct.received()
        assert len(received_summary.days) > 0
        print(f"  ✓ You Got Daily Grouping: {len(received_summary.days)} day(s), range_total=₹{received_summary.range_total}")
        for day in received_summary.days[:2]:
            print(f"    Day: {day.date} | Count: {day.count} | Total Received: ₹{day.total}")

        # Verify Suresh debit in recent credits
        credits = await ledger_direct.credits_list()
        suresh_credit = next((c for c in credits if "Suresh" in c.customer_name and c.amount_inr == 300), None)
        assert suresh_credit is not None, f"Suresh ₹300 debit not found in credits_list!"
        print(f"  ✓ Suresh ₹300 debit found in SQLite credits_list (You Gave section):")
        print(f"    - Event ID: {suresh_credit.event_id}")
        print(f"    - Effective Date: {suresh_credit.effective_date}")
        print(f"    - Timestamp: {suresh_credit.formatted_time or suresh_credit.ts}")
        print(f"    - Amount: ₹{suresh_credit.amount_inr}")

    asyncio.run(verify_db())
    print("\n🎉 ALL TESTS PASSED: Language-based credit/debit classification & You Got persistence work perfectly!")


if __name__ == "__main__":
    test_spoken_direction_heuristics()
    test_full_pipeline_persistence()
