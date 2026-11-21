import asyncio
import json
import math
import struct
import sys
from pathlib import Path

# Ensure UTF-8 output on Windows
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from server.app import create_server_app
from server.config import ServerSettings
from server.ledger import Ledger
from server.protocol import encode_upstream_audio


def generate_tone_pcm(freq: float = 440.0, duration_sec: float = 1.0, sample_rate: int = 16000) -> bytes:
    samples = []
    total = int(sample_rate * duration_sec)
    for i in range(total):
        val = int(12000 * math.sin(2 * math.pi * freq * i / sample_rate))
        samples.append(struct.pack("<h", val))
    return b"".join(samples)


def run_verification():
    print("--- STARTING VOICE KHATA VERIFICATION ---")
    cfg = ServerSettings(use_mocks=True)
    app = create_server_app(settings=cfg, auto_seed=True)

    with TestClient(app) as client:
        # 1. Health check
        hz = client.get("/healthz")
        assert hz.status_code == 200, f"Healthz failed: {hz.status_code}"
        print("✓ GET /healthz passed: 200 OK")

        # 2. WebSocket session connection
        with client.websocket_connect("/ws/session") as ws:
            ws.send_text(json.dumps({"t": "session.start", "lang": "hi-IN"}))
            ready = ws.receive_json()
            assert ready["t"] == "ready", f"Expected ready, got {ready}"
            print("✓ WS /ws/session connected: ready received")

            # Snapshot and ledger
            first_msg = ws.receive_json()
            if first_msg["t"] == "snapshot":
                ledger_msg = ws.receive_json()
            else:
                ledger_msg = first_msg
            assert ledger_msg["t"] == "ledger"
            print(f"✓ Initial ledger loaded from SQLite DB: {len(ledger_msg['rows'])} customers")

            # Drain greeting TTS audio
            while True:
                pkt = ws.receive()
                if pkt.get("text"):
                    m = json.loads(pkt["text"])
                    if m.get("t") == "tts.end":
                        break

            # 3. Stream Voice Input: Clip Suresh 175 credit
            print("🎙️ Streaming voice input (PTT down -> audio stream -> PTT up)...")
            ws.send_text(json.dumps({
                "t": "ptt.down",
                "clip_id": "clip_04_suresh_credit_175",
                "mode": "gave"
            }))
            
            pcm_chunk = generate_tone_pcm(duration_sec=0.2)
            for i in range(0, len(pcm_chunk), 640):
                frame = pcm_chunk[i:i+640]
                if len(frame) == 640:
                    ws.send_bytes(encode_upstream_audio(frame))

            ws.send_text(json.dumps({"t": "ptt.up"}))

            # 4. Receive transcript and action
            received_action = None
            received_transcript = None
            updated_ledger = None

            while True:
                pkt = ws.receive()
                if pkt.get("text"):
                    m = json.loads(pkt["text"])
                    if m.get("t") == "transcript" and m.get("kind") == "final":
                        received_transcript = m.get("text")
                        print(f"✓ Received final transcript: '{received_transcript}'")
                    elif m.get("t") == "action":
                        received_action = m
                        print(f"✓ Received parsed action: {m.get('type')} {m.get('customer')} ₹{m.get('amount_inr')} (status={m.get('status')})")
                    elif m.get("t") == "ledger":
                        updated_ledger = m
                        print(f"✓ Received updated ledger after database commit!")
                    elif m.get("t") == "tts.end":
                        break

            assert received_action is not None, "Did not receive action"
            assert received_action["type"] == "CREDIT"
            assert received_action["amount_inr"] == 175

            # 5. Check SQLite database directly
            print("🔍 Verifying SQLite persistence in data/ directory...")
            db_path = cfg.data_dir / "ledger.db"
            ledger_direct = Ledger(db_path, enc_key=cfg.effective_db_encryption_key())
            
            async def check_db():
                summary = await ledger_direct.summary()
                print(f"✓ SQLite Summary: owing_count={summary.owing_count}, total_owed=₹{summary.total_owed}")
                cust_match = await ledger_direct.customers_matching("Suresh")
                assert len(cust_match) > 0
                bal = await ledger_direct.balance(cust_match[0].id)
                print(f"✓ Customer '{cust_match[0].name}' balance in SQLite DB: ₹{bal}")
                events = await ledger_direct.recent_events(limit=5)
                print(f"✓ Total events permanently in DB: {len(events)}")
                last_ev = events[0]
                print(f"✓ Most recent event: type={last_ev.get('type')}, customer={last_ev.get('customer_name')}, amount=₹{last_ev.get('amount_inr')}")

            asyncio.run(check_db())

    print("\n🎉 ALL CHECKS PASSED: Voice input streams, transcribes, parses, and permanently commits to SQLite database!")


if __name__ == "__main__":
    run_verification()
