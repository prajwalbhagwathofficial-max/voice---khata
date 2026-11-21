"""Protocol, /healthz, binary framing, MockSTT/MockTTS, and end-to-end /ws/session tests."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from server.app import create_server_app
from server.config import ServerSettings
from server.ledger import Ledger, generate_key_b64
from server.protocol import (
    FRAME_TYPE_DOWNSTREAM_AUDIO,
    FRAME_TYPE_UPSTREAM_AUDIO,
    decode_downstream_audio,
    decode_upstream_audio,
    encode_downstream_audio,
    encode_upstream_audio,
)


def test_binary_framing_codec_round_trip() -> None:
    pcm = b"\x01\x02" * 320  # 20 ms 16 kHz PCM16
    up = encode_upstream_audio(pcm)
    assert up[0] == FRAME_TYPE_UPSTREAM_AUDIO
    assert len(up) == 641
    assert decode_upstream_audio(up) == pcm
    assert decode_upstream_audio(b"\x02\x00\x00") is None

    down = encode_downstream_audio(77, pcm)
    assert down[0] == FRAME_TYPE_DOWNSTREAM_AUDIO
    decoded = decode_downstream_audio(down)
    assert decoded is not None
    assert decoded[0] == 77
    assert decoded[1] == pcm
    assert decode_downstream_audio(b"\x01\x00\x00\x00\x01") is None


def test_protocol_ready_healthz_ptt_credit_interrupt_and_reset(tmp_path: Path) -> None:
    cfg = ServerSettings(
        _env_file=None,
        use_mocks=True,
        data_dir=tmp_path,
        db_encryption_key=generate_key_b64(),
        admin_token="secret-judge-token-123",
    )
    app = create_server_app(settings=cfg, auto_seed=True)

    with TestClient(app) as client:
        hz = client.get("/healthz")
        assert hz.status_code == 200
        assert hz.json() == {"ok": True}
        assert hz.headers["permissions-policy"] == "microphone=(self)"
        assert hz.headers["x-content-type-options"] == "nosniff"

        idx = client.get("/")
        assert idx.status_code == 200
        assert "Voice Khata" in idx.text

        # AudioWorklet .js files MUST be served as application/javascript
        cap_js = client.get("/worklets/capture-processor.js")
        assert cap_js.status_code == 200
        assert cap_js.headers["content-type"].split(";")[0].strip() == "application/javascript"
        assert cap_js.headers["permissions-policy"] == "microphone=(self)"
        assert cap_js.headers["x-content-type-options"] == "nosniff"

        play_js = client.get("/worklets/playback-processor.js")
        assert play_js.status_code == 200
        assert play_js.headers["content-type"].split(";")[0].strip() == "application/javascript"

        # StaticFiles mount at "/" must NOT shadow /ws/session
        with client.websocket_connect("/ws/session") as ws:
            ws.send_text(json.dumps({"t": "session.start", "lang": "hi-IN"}))

            ready = ws.receive_json()
            assert ready["t"] == "ready"
            assert ready["lang"] == "hi-IN"

            first_msg = ws.receive_json()
            if first_msg["t"] == "snapshot":
                assert "summary" in first_msg
                assert "customers" in first_msg
                assert "recent_events" in first_msg
                ledger_msg = ws.receive_json()
            else:
                ledger_msg = first_msg
            assert ledger_msg["t"] == "ledger"
            assert len(ledger_msg["rows"]) in (8, 9)
            assert "summary" in ledger_msg
            assert ledger_msg["summary"]["debtor_count"] > 0
            assert ledger_msg["summary"]["total_owed"] > 0
            assert "payments" in ledger_msg
            assert len(ledger_msg["payments"]) >= 2


            # Drain Turn-0 AI greeting audio until tts.end
            greeting_audio_frames = 0
            while True:
                pkt = ws.receive()
                if pkt.get("bytes") is not None:
                    decoded = decode_downstream_audio(pkt["bytes"])
                    assert decoded is not None
                    greeting_audio_frames += 1
                elif pkt.get("text") is not None:
                    msg = json.loads(pkt["text"])
                    if msg["t"] == "tts.end":
                        break
            assert greeting_audio_frames > 0

            # 1) Bare "Ramesh" triggers two-Ramesh DISAMBIGUATE safely instead of guessing
            ws.send_text(
                json.dumps({"t": "ptt.down", "clip_id": "clip_01_ramesh_credit_340"})
            )
            ws.send_bytes(encode_upstream_audio(b"\x00\x01" * 320))
            ws.send_text(json.dumps({"t": "ptt.up"}))

            disambig_action = None
            while True:
                pkt = ws.receive()
                if pkt.get("text") is not None:
                    msg = json.loads(pkt["text"])
                    if msg["t"] == "action":
                        disambig_action = msg
                    elif msg["t"] == "tts.end":
                        break
            assert disambig_action is not None
            assert disambig_action["type"] == "DISAMBIGUATE"
            assert disambig_action["amount_inr"] == 340

            # Follow-up answer "Ramesh Kumar" resolves pending disambiguation immediately
            ws.send_text(json.dumps({"t": "mock.inject", "text": "Ramesh Kumar"}))
            resolved_action = None
            while True:
                pkt = ws.receive()
                if pkt.get("text") is not None:
                    msg = json.loads(pkt["text"])
                    if msg["t"] == "action":
                        resolved_action = msg
                    elif msg["t"] == "tts.end":
                        break
            assert resolved_action is not None
            assert resolved_action["type"] in ("CREDIT", "CREDIT_GIVEN")
            assert resolved_action["customer"] == "Ramesh Kumar"
            assert resolved_action["amount_inr"] == 340

            # 2) Unambiguous "Suresh" commits CREDIT 175 and pushes refreshed ledger
            ws.send_text(
                json.dumps({"t": "ptt.down", "clip_id": "clip_04_suresh_credit_175"})
            )
            ws.send_bytes(encode_upstream_audio(b"\x00\x01" * 320))
            ws.send_text(json.dumps({"t": "ptt.up"}))

            seen_action = None
            seen_final = None
            updated_ledger = None
            turn_audio = 0

            while True:
                pkt = ws.receive()
                if pkt.get("bytes") is not None:
                    decoded = decode_downstream_audio(pkt["bytes"])
                    assert decoded is not None
                    turn_audio += 1
                elif pkt.get("text") is not None:
                    msg = json.loads(pkt["text"])
                    if msg["t"] == "transcript" and msg["kind"] == "final":
                        seen_final = msg["text"]
                    elif msg["t"] == "action":
                        seen_action = msg
                    elif msg["t"] == "ledger":
                        updated_ledger = msg
                    elif msg["t"] == "tts.end":
                        break

            assert seen_final is not None
            assert seen_action is not None
            assert seen_action["type"] in ("CREDIT", "CREDIT_GIVEN")
            assert seen_action["amount_inr"] == 175
            assert updated_ledger is not None
            assert turn_audio > 0

            # Barge-in interrupt triggers flush
            ws.send_text(json.dumps({"t": "interrupt"}))
            flush = ws.receive_json()
            assert flush["t"] == "flush"

            # Reset demo data wipes and re-seeds 8 customers
            ws.send_text(json.dumps({"t": "reset_demo"}))
            flush2 = ws.receive_json()
            assert flush2["t"] == "flush"
            post_reset = ws.receive_json()
            if post_reset["t"] == "summary":
                assert post_reset["owing_count"] > 0
                reset_ledger = ws.receive_json()
            else:
                reset_ledger = post_reset
            assert reset_ledger["t"] == "ledger"
            assert len(reset_ledger["rows"]) in (8, 9)

        # /admin/wipe enforces ADMIN_TOKEN
        assert client.post("/admin/wipe").status_code == 401
        assert client.post("/admin/wipe", headers={"X-Admin-Token": "wrong"}).status_code == 401
        ok_wipe = client.post(
            "/admin/wipe", headers={"Authorization": "Bearer secret-judge-token-123"}
        )
        assert ok_wipe.status_code == 200
        assert ok_wipe.json() == {"ok": True, "wiped": True}


def test_startup_fails_fast_when_required_secrets_missing(tmp_path: Path) -> None:
    import pytest

    cfg = ServerSettings(
        _env_file=None,
        use_mocks=False,
        data_dir=tmp_path,
        gnani_api_key="",
        groq_api_key="",
        db_encryption_key="",
        ledger_enc_key="",
    )
    app = create_server_app(settings=cfg)
    with pytest.raises(RuntimeError, match="GNANI_API_KEY.*GROQ_API_KEY.*DB_ENCRYPTION_KEY"):
        with TestClient(app):
            pass


def test_event_delete_over_websocket(tmp_path: Path) -> None:
    cfg = ServerSettings(
        _env_file=None,
        use_mocks=True,
        data_dir=tmp_path,
        db_encryption_key=generate_key_b64(),
        admin_token="secret-judge-token-123",
    )
    app = create_server_app(settings=cfg, auto_seed=True)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/session") as ws:
            ws.send_text(json.dumps({"t": "session.start", "lang": "en-IN"}))
            # Drain until initial ledger and tts.end
            initial_payments = None
            while True:
                pkt = ws.receive()
                if "text" in pkt and pkt["text"] is not None:
                    data = json.loads(pkt["text"])
                    if data.get("t") == "ledger":
                        initial_payments = data["payments"]
                    elif data.get("t") == "tts.end":
                        break
            assert initial_payments is not None and len(initial_payments) >= 1
            target_event_id = initial_payments[0]["event_id"]

            # Send event.delete
            ws.send_text(json.dumps({"t": "event.delete", "event_id": target_event_id}))
            del_res = ws.receive_json()
            assert del_res["t"] == "event.delete.result"
            assert del_res.get("error") is None
            assert del_res["status"] == "success"
            assert del_res["event_id"] == target_event_id

            # Next message is updated ledger
            updated_ledger = ws.receive_json()
            assert updated_ledger["t"] == "ledger"
            assert not any(p["event_id"] == target_event_id for p in updated_ledger["payments"])


def test_websocket_customer_create_with_initial_udhar(tmp_path: Path) -> None:
    """Explicitly adding customer with initial udhar records customer and event in ledger."""
    cfg = ServerSettings(
        _env_file=None,
        use_mocks=True,
        data_dir=tmp_path,
        db_encryption_key=generate_key_b64(),
        admin_token="secret-judge-token-123",
    )
    app = create_server_app(settings=cfg, auto_seed=True)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/session") as ws:
            ws.send_text(json.dumps({"t": "session.start", "lang": "en-IN"}))
            while True:
                pkt = ws.receive()
                if "text" in pkt and pkt["text"] is not None:
                    if json.loads(pkt["text"]).get("t") == "tts.end":
                        break

            # Send customer.create with initial udhar ₹500
            ws.send_text(json.dumps({
                "t": "customer.create",
                "name": "Naveen Gowda",
                "phone": "9876543210",
                "initial_credit_paise": 50000,
                "note": "Atta and Dal",
            }))
            res = ws.receive_json()
            assert res["t"] == "customer.create.result"
            assert res["status"] == "success"
            assert res["customer_name"] == "Naveen Gowda"
            assert res["customer_id"] is not None
            assert res["event_id"] is not None

            # Next message is updated ledger containing Naveen Gowda with 500 balance
            ledger_msg = ws.receive_json()
            assert ledger_msg["t"] == "ledger"
            matching_row = next((r for r in ledger_msg["rows"] if r["name"] == "Naveen Gowda"), None)
            assert matching_row is not None
            assert matching_row["balance_inr"] == 500


def test_spa_catch_all_and_security_headers(tmp_path: Path) -> None:
    cfg = ServerSettings(
        _env_file=None,
        use_mocks=True,
        data_dir=tmp_path,
        db_encryption_key=generate_key_b64(),
        admin_token="secret-judge-token-123",
    )
    app = create_server_app(settings=cfg, auto_seed=True)

    with TestClient(app) as client:
        # Security headers
        hz = client.get("/healthz")
        assert hz.status_code == 200
        assert "microphone=(self)" in hz.headers["permissions-policy"]
        assert hz.headers["x-content-type-options"] == "nosniff"
        assert "default-src 'self'" in hz.headers["content-security-policy"]

        # SPA Routes return 200 HTML
        for path in ("/got", "/gave", "/customers/1", "/settings"):
            r = client.get(path)
            assert r.status_code == 200, f"Failed for {path}"
            assert "html" in r.headers["content-type"].lower()
            assert "<html" in r.text.lower()


def test_admin_export_backup_stream(tmp_path: Path) -> None:
    import io
    import sqlite3

    cfg = ServerSettings(
        _env_file=None,
        use_mocks=True,
        data_dir=tmp_path,
        db_encryption_key=generate_key_b64(),
        admin_token="secret-judge-token-123",
    )
    app = create_server_app(settings=cfg, auto_seed=True)

    with TestClient(app) as client:
        # 401 without token
        unauth = client.get("/admin/export")
        assert unauth.status_code == 401

        # 200 with Bearer token
        res = client.get("/admin/export", headers={"Authorization": "Bearer secret-judge-token-123"})
        assert res.status_code == 200
        assert res.headers["content-type"] == "application/octet-stream"
        assert "ledger_backup.db" in res.headers["content-disposition"]
        db_bytes = res.content
        assert len(db_bytes) > 0

        # Verify the exported bytes are a valid SQLite database
        backup_file = tmp_path / "restored.db"
        backup_file.write_bytes(db_bytes)
        conn = sqlite3.connect(str(backup_file))
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        table_names = {t[0] for t in tables}
        conn.close()
        assert "events" in table_names
        assert "customers" in table_names


def test_websocket_event_reverse(tmp_path: Path) -> None:
    cfg = ServerSettings(
        _env_file=None,
        use_mocks=True,
        data_dir=tmp_path,
        db_encryption_key=generate_key_b64(),
        admin_token="secret-judge-token-123",
    )
    app = create_server_app(settings=cfg, auto_seed=True)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/session") as ws:
            ws.send_text(json.dumps({"t": "session.start", "lang": "en-IN"}))
            while True:
                pkt = ws.receive()
                if "text" in pkt and pkt["text"] is not None:
                    if json.loads(pkt["text"]).get("t") == "tts.end":
                        break

            # 1. Create customer with credit
            ws.send_text(
                json.dumps({
                    "t": "customer.create",
                    "name": "Reverse Test Customer",
                    "initial_credit_paise": 30000,
                })
            )
            c_res = ws.receive_json()
            assert c_res["t"] == "customer.create.result"
            assert c_res["status"] == "success"
            cid = c_res["customer_id"]
            eid = c_res["event_id"]
            # Consume pushed ledger
            ws.receive_json()

            # 2. Query balance
            ws.send_text(json.dumps({"t": "customer.query", "id": cid}))
            q1 = ws.receive_json()
            assert q1["t"] == "customer.result"
            assert q1["balance_paise"] == 30000

            # 3. Send event.reverse
            ws.send_text(json.dumps({"t": "event.reverse", "event_id": eid}))
            r_res = ws.receive_json()
            assert r_res["t"] == "event.reverse.result"
            assert r_res["status"] == "success"
            # Consume pushed ledger
            ws.receive_json()

            # 4. Query balance again
            ws.send_text(json.dumps({"t": "customer.query", "id": cid}))
            q2 = ws.receive_json()
            assert q2["t"] == "customer.result"
            assert q2["balance_paise"] == 0

