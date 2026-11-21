"""Comprehensive tests for the 6 server requirements:
1. SQLite location (DATA_DIR), WAL, PRAGMA user_version migrations, data survives restart, seed only if empty.
2. UTC epoch ms timestamps, Asia/Kolkata day boundaries at 23:59 vs 00:01.
3. Queries against a Reference Model: summary(), received(), customer_timeline().
4. WebSocket snapshot, summary after commit, received.query -> received.result, customer.query -> customer.result.
5. ptt.down mode hint ("gave"|"got") and spoken direction override with direction_overridden: true.
6. Voided events excluded, overpayment handling.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from server.app import create_server_app
from server.config import ServerSettings
from server.ledger import (
    CustomerRecord,
    FieldCipher,
    Ledger,
    LedgerEventInput,
    LedgerEventType,
    generate_key_b64,
    seed_demo_data,
)
from server.ledger.store import (
    KOLKATA_TZ,
    _CREATE_INDEXES_SQL,
    _CREATE_TABLES_SQL,
    _CREATE_TRIGGERS_SQL,
    epoch_ms_to_ist_datetime,
)
from server.protocol import (
    CustomerQueryMsg,
    CustomerResultMsg,
    InjectTextMsg,
    PttDownMsg,
    ReadyMsg,
    ReceivedQueryMsg,
    ReceivedResultMsg,
    SessionStartMsg,
    SnapshotMsg,
    SummaryMsg,
)


# ----------------------------------------------------------------------
# Pure Python Reference Model (Requirement 3)
# ----------------------------------------------------------------------
class ReferenceLedger:
    """In-memory reference implementation of Ledger semantics."""

    def __init__(self) -> None:
        self.customers: dict[str, dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []

    def add_customer(self, cid: str, name: str) -> None:
        self.customers[cid] = {"id": cid, "name": name}

    def record_event(
        self,
        eid: str,
        ev_type: str,
        customer_id: str,
        amount_inr: int,
        effective_date: str,
        ts_ms: int,
        voids_eid: str | None = None,
        note: str | None = None,
    ) -> None:
        self.events.append({
            "seq": len(self.events) + 1,
            "event_id": eid,
            "type": ev_type,
            "customer_id": customer_id,
            "amount_inr": amount_inr,
            "effective_date": effective_date,
            "ts": ts_ms,
            "voids_event_id": voids_eid,
            "note": note,
        })

    def _active_events(self) -> list[dict[str, Any]]:
        voided_ids = {
            e["voids_event_id"]
            for e in self.events
            if e["type"] == "VOID" and e["voids_event_id"]
        }
        return [
            e
            for e in self.events
            if e["type"] in ("CREDIT", "PAYMENT") and e["event_id"] not in voided_ids
        ]

    def summary(self) -> dict[str, int]:
        balances: dict[str, int] = {cid: 0 for cid in self.customers}
        for e in self._active_events():
            cid = e["customer_id"]
            if e["type"] == "CREDIT":
                balances[cid] += e["amount_inr"]
            elif e["type"] == "PAYMENT":
                balances[cid] -= e["amount_inr"]

        owing_count = sum(1 for b in balances.values() if b > 0)
        total_owed = sum(b for b in balances.values() if b > 0)
        advance_count = sum(1 for b in balances.values() if b < 0)
        total_advance = sum(-b for b in balances.values() if b < 0)
        return {
            "owing_count": owing_count,
            "total_owed": total_owed,
            "advance_count": advance_count,
            "total_advance": total_advance,
        }

    def received(self, from_date: str | None = None, to_date: str | None = None) -> dict[str, Any]:
        active_payments = [e for e in self._active_events() if e["type"] == "PAYMENT"]
        # Group by IST day
        day_map: dict[str, list[dict[str, Any]]] = {}
        range_total = 0

        # Sort newest ts first
        active_payments.sort(key=lambda x: (x["ts"], x["seq"]), reverse=True)

        for e in active_payments:
            dt_ist = epoch_ms_to_ist_datetime(e["ts"])
            day_str = dt_ist.date().isoformat()
            if from_date and day_str < from_date:
                continue
            if to_date and day_str > to_date:
                continue

            range_total += e["amount_inr"]
            if day_str not in day_map:
                day_map[day_str] = []
            day_map[day_str].append(e)

        sorted_days = sorted(day_map.keys(), reverse=True)
        days_out = []
        for d in sorted_days:
            items = day_map[d]
            days_out.append({
                "date": d,
                "total": sum(i["amount_inr"] for i in items),
                "count": len(items),
            })
        return {"range_total": range_total, "days": days_out}

    def customer_timeline(self, customer_id: str) -> dict[str, Any]:
        active = [e for e in self._active_events() if e["customer_id"] == customer_id]
        active.sort(key=lambda x: (x["ts"], x["seq"]))
        running = 0
        timeline_items = []
        for e in active:
            if e["type"] == "CREDIT":
                running += e["amount_inr"]
            elif e["type"] == "PAYMENT":
                running -= e["amount_inr"]
            timeline_items.append({
                "event_id": e["event_id"],
                "type": e["type"],
                "amount_inr": e["amount_inr"],
                "running_balance": running,
            })
        advance = -running if running < 0 else 0
        return {
            "customer_id": customer_id,
            "balance": running,
            "advance": advance,
            "is_advance": running < 0,
            "timeline": timeline_items,
        }


# ----------------------------------------------------------------------
# Requirement 1 & 6: Data survives restart, seed only if empty, migrations
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_data_survives_process_restart(tmp_path: Path) -> None:
    db_file = tmp_path / "data" / "ledger.db"
    key = generate_key_b64()

    # Step 1: Open store, write customer and events
    ledger1 = Ledger(db_file, enc_key=key)
    c1 = await ledger1.add_customer("Ramesh Kumar", customer_id="c_restart_1")
    await ledger1.record(
        LedgerEventInput(
            event_id="ev_r1",
            type=LedgerEventType.CREDIT,
            customer_id=c1.id,
            amount_inr=500,
            effective_date="2026-11-19",
            ts=1763539200000,
        )
    )
    await ledger1.record(
        LedgerEventInput(
            event_id="ev_r2",
            type=LedgerEventType.PAYMENT,
            customer_id=c1.id,
            amount_inr=200,
            effective_date="2026-11-19",
            ts=1763539260000,
        )
    )
    bal1 = await ledger1.balance(c1.id)
    assert bal1 == 30000

    # Step 2: "Process restart" - create new Ledger instance against the same SQLite file
    ledger2 = Ledger(db_file, enc_key=key)
    c1_read = await ledger2.get_customer(c1.id)
    assert c1_read is not None
    assert c1_read.name == "Ramesh Kumar"
    bal2 = await ledger2.balance(c1.id)
    assert bal2 == 30000
    summary = await ledger2.summary()
    assert summary.owing_count == 1
    assert summary.total_owed == 300


@pytest.mark.asyncio
async def test_seed_synthetic_customers_only_if_empty(tmp_path: Path) -> None:
    db_file = tmp_path / "seed_check.db"
    key = generate_key_b64()
    ledger = Ledger(db_file, enc_key=key)

    # Empty table -> seed should populate customers
    assert await ledger.customers_count() == 0
    seeded = await seed_demo_data(ledger)
    assert len(seeded) == 9
    assert await ledger.customers_count() == 9

    # Add a custom customer
    await ledger.add_customer("Custom Trader", customer_id="c_custom")
    assert await ledger.customers_count() == 10

    # Re-running seed_demo_data without force must NOT overwrite or duplicate
    reseeded = await seed_demo_data(ledger, force=False)
    assert len(reseeded) == 10
    assert await ledger.customers_count() == 10
    assert any(c.name == "Custom Trader" for c in reseeded)


@pytest.mark.asyncio
async def test_migration_from_v0_keeps_all_rows_and_converts_ts_to_epoch_ms(tmp_path: Path) -> None:
    """Simulate a legacy SQLite ledger (user_version=0) with string ts columns."""
    db_file = tmp_path / "legacy_v0.db"
    key = generate_key_b64()
    cipher = FieldCipher(key)

    conn = sqlite3.connect(str(db_file))
    conn.execute(
        """
        CREATE TABLE customers (
            id TEXT PRIMARY KEY,
            name_enc BLOB NOT NULL,
            name_norm TEXT NOT NULL,
            script TEXT NOT NULL,
            created_ts TEXT NOT NULL
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE events (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL UNIQUE,
            type TEXT NOT NULL,
            customer_id TEXT NOT NULL,
            amount_inr INTEGER NOT NULL,
            effective_date TEXT NOT NULL,
            ts TEXT NOT NULL,
            voids_event_id TEXT NULL,
            note_enc BLOB NULL,
            session_scope TEXT NULL
        );
        """
    )
    # user_version is 0 by default in SQLite
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0

    # Insert legacy customer and events with ISO string ts
    c_enc = cipher.encrypt_str("Suresh Legacy", aad=b"customer:c_leg:name")
    conn.execute(
        "INSERT INTO customers VALUES (?, ?, ?, ?, ?)",
        ("c_leg", c_enc, "SRS", "latin", "2026-11-01T00:00:00"),
    )
    conn.execute(
        "INSERT INTO events VALUES (1, 'ev_legacy_1', 'CREDIT', 'c_leg', 400, '2026-11-18', '2026-11-18T10:00:00+00:00', NULL, NULL, 'seed')",
    )
    conn.execute(
        "INSERT INTO events VALUES (2, 'ev_legacy_2', 'PAYMENT', 'c_leg', 150, '2026-11-18', '2026-11-18T12:00:00+00:00', NULL, NULL, 'seed')",
    )
    conn.commit()
    conn.close()

    # Now open with Ledger which runs ordered migrations to v1
    ledger = Ledger(db_file, enc_key=key)

    # Verify user_version is 3
    conn2 = sqlite3.connect(str(db_file))
    try:
        assert conn2.execute("PRAGMA user_version").fetchone()[0] == 3
        # Check table schema: ts should be INTEGER and amount_paise exists
        cols = {r[1]: r[2] for r in conn2.execute("PRAGMA table_info(events)").fetchall()}
        assert cols["ts"].upper() == "INTEGER"
        assert "amount_paise" in cols
        # Check required indexes exist
        idx_names = [r[1] for r in conn2.execute("PRAGMA index_list(events)").fetchall()]
        assert "idx_events_type_ts" in idx_names
        assert "idx_events_customer_ts" in idx_names
        assert "idx_events_reverses_event_id" in idx_names
    finally:
        conn2.close()

    # Verify all legacy rows survived and balance is intact (400 - 150 = 250 INR = 25000 paise)
    bal = await ledger.balance("c_leg")
    assert bal == 25000
    timeline = await ledger.customer_timeline("c_leg")
    assert len(timeline.events) == 2
    assert isinstance(timeline.events[0].ts, int)
    assert timeline.events[0].ts > 1_000_000_000_000


# ----------------------------------------------------------------------
# Requirement 2 & 6: IST Day Boundary at 23:59 vs 00:01
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ist_day_boundary_at_2359_vs_0001(tmp_path: Path) -> None:
    """Asia/Kolkata day boundary:
    2026-11-18 23:59:00 IST = 2026-11-18 18:29:00 UTC
    2026-11-19 00:01:00 IST = 2026-11-18 18:31:00 UTC
    Separated by 2 minutes in UTC, but must fall into separate IST days.
    """
    ledger = Ledger(tmp_path / "ist_boundary.db", enc_key=generate_key_b64())
    c = await ledger.add_customer("Boundary Tester", customer_id="c_bound")

    # Day D (2026-11-18) 23:59 IST
    dt_day_d = datetime(2026, 11, 18, 23, 59, 0, tzinfo=KOLKATA_TZ)
    ts_day_d_ms = int(dt_day_d.timestamp() * 1000)

    # Day D+1 (2026-11-19) 00:01 IST
    dt_day_d1 = datetime(2026, 11, 19, 0, 1, 0, tzinfo=KOLKATA_TZ)
    ts_day_d1_ms = int(dt_day_d1.timestamp() * 1000)

    # Record two payments
    await ledger.record(
        LedgerEventInput(
            event_id="ev_d",
            type=LedgerEventType.PAYMENT,
            customer_id=c.id,
            amount_inr=111,
            effective_date="2026-11-18",
            ts=ts_day_d_ms,
        )
    )
    await ledger.record(
        LedgerEventInput(
            event_id="ev_d1",
            type=LedgerEventType.PAYMENT,
            customer_id=c.id,
            amount_inr=222,
            effective_date="2026-11-19",
            ts=ts_day_d1_ms,
        )
    )

    # Grouped received query across both days
    res = await ledger.received()
    assert res.range_total == 333
    assert len(res.days) == 2
    assert res.days[0].date == "2026-11-19"
    assert res.days[0].total == 222
    assert res.days[1].date == "2026-11-18"
    assert res.days[1].total == 111

    # Date-filtered received queries
    only_d = await ledger.received(from_date="2026-11-18", to_date="2026-11-18")
    assert only_d.range_total == 111
    assert len(only_d.days) == 1
    assert only_d.days[0].date == "2026-11-18"

    only_d1 = await ledger.received(from_date="2026-11-19", to_date="2026-11-19")
    assert only_d1.range_total == 222
    assert len(only_d1.days) == 1
    assert only_d1.days[0].date == "2026-11-19"


# ----------------------------------------------------------------------
# Requirement 3 & 6: Reference Model Tests, Voids Excluded, Overpayment
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_reference_model_comparison_with_voids_and_overpayment(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ref_model.db", enc_key=generate_key_b64())
    ref = ReferenceLedger()

    customers = [
        ("cust_1", "Alice"),
        ("cust_2", "Bob"),
        ("cust_3", "Charlie"),
    ]
    for cid, name in customers:
        await ledger.add_customer(name, customer_id=cid)
        ref.add_customer(cid, name)

    t0 = int(datetime(2026, 11, 18, 10, 0, tzinfo=KOLKATA_TZ).timestamp() * 1000)
    t1 = t0 + 60_000
    t2 = t0 + 120_000
    t3 = t0 + 180_000
    t4 = t0 + 240_000

    # Alice: credit 500, payment 200 -> balance 300
    await ledger.record(
        LedgerEventInput(
            event_id="e1",
            type=LedgerEventType.CREDIT,
            customer_id="cust_1",
            amount_inr=500,
            effective_date="2026-11-18",
            ts=t0,
        )
    )
    ref.record_event("e1", "CREDIT", "cust_1", 500, "2026-11-18", t0)

    await ledger.record(
        LedgerEventInput(
            event_id="e2",
            type=LedgerEventType.PAYMENT,
            customer_id="cust_1",
            amount_inr=200,
            effective_date="2026-11-18",
            ts=t1,
        )
    )
    ref.record_event("e2", "PAYMENT", "cust_1", 200, "2026-11-18", t1)

    # Bob: credit 1000, then voided -> balance 0
    await ledger.record(
        LedgerEventInput(
            event_id="e3",
            type=LedgerEventType.CREDIT,
            customer_id="cust_2",
            amount_inr=1000,
            effective_date="2026-11-18",
            ts=t2,
        )
    )
    ref.record_event("e3", "CREDIT", "cust_2", 1000, "2026-11-18", t2)

    await ledger.void("e3", void_event_id="v_e3")
    ref.record_event("v_e3", "VOID", "cust_2", 0, "2026-11-18", t2 + 1000, voids_eid="e3")

    # Charlie: credit 200, but pays 500 -> overpayment! Balance = -300 (advance = 300)
    await ledger.record(
        LedgerEventInput(
            event_id="e4",
            type=LedgerEventType.CREDIT,
            customer_id="cust_3",
            amount_inr=200,
            effective_date="2026-11-18",
            ts=t3,
        )
    )
    ref.record_event("e4", "CREDIT", "cust_3", 200, "2026-11-18", t3)

    await ledger.record(
        LedgerEventInput(
            event_id="e5",
            type=LedgerEventType.PAYMENT,
            customer_id="cust_3",
            amount_inr=500,
            effective_date="2026-11-18",
            ts=t4,
        )
    )
    ref.record_event("e5", "PAYMENT", "cust_3", 500, "2026-11-18", t4)

    # 1. Compare summary()
    summary_actual = await ledger.summary()
    summary_expected = ref.summary()
    assert summary_actual.owing_count == summary_expected["owing_count"] == 1  # Alice only
    assert summary_actual.total_owed == summary_expected["total_owed"] == 300
    assert summary_actual.advance_count == summary_expected["advance_count"] == 1  # Charlie only
    assert summary_actual.total_advance == summary_expected["total_advance"] == 300

    # 2. Compare received()
    received_actual = await ledger.received()
    received_expected = ref.received()
    assert received_actual.range_total == received_expected["range_total"] == 700  # 200 + 500
    assert len(received_actual.days) == len(received_expected["days"]) == 1
    assert received_actual.days[0].total == received_expected["days"][0]["total"] == 700
    assert received_actual.days[0].count == received_expected["days"][0]["count"] == 2

    # 3. Compare customer_timeline()
    timeline_charlie = await ledger.customer_timeline("cust_3")
    expected_charlie = ref.customer_timeline("cust_3")
    assert timeline_charlie.balance == expected_charlie["balance"] == -300
    assert timeline_charlie.advance == expected_charlie["advance"] == 300
    assert timeline_charlie.is_advance is True
    assert len(timeline_charlie.events) == 2
    assert timeline_charlie.events[0].running_balance == 200
    assert timeline_charlie.events[1].running_balance == -300

    # Bob's timeline includes both credit and reversal events, flagged accordingly, with running balance returning to 0
    timeline_bob = await ledger.customer_timeline("cust_2")
    assert timeline_bob.balance == 0
    assert len(timeline_bob.events) == 2
    assert timeline_bob.events[0].is_reversed is True
    assert timeline_bob.events[1].is_reversal is True
    assert timeline_bob.events[1].running_balance == 0


# ----------------------------------------------------------------------
# Requirement 4 & 5: WebSocket Protocol Queries, Mode Hint & Spoken Direction Override
# ----------------------------------------------------------------------
def test_websocket_snapshot_summary_queries_and_mode_override(tmp_path: Path) -> None:
    cfg = ServerSettings(
        _env_file=None,
        use_mocks=True,
        data_dir=tmp_path,
        db_encryption_key=generate_key_b64(),
        admin_token="token-456",
    )
    app = create_server_app(settings=cfg, auto_seed=False)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/session") as ws:
            # 1. Session Start -> receives ReadyMsg AND SnapshotMsg
            ws.send_text(json.dumps({"t": "session.start", "lang": "hi-IN"}))

            ready = ws.receive_json()
            assert ready["t"] == "ready"

            snapshot = ws.receive_json()
            assert snapshot["t"] == "snapshot"
            assert "summary" in snapshot
            assert "customers" in snapshot
            assert "recent_events" in snapshot

            # Drain greeting audio
            while True:
                pkt = ws.receive()
                if pkt.get("text") is not None and json.loads(pkt["text"])["t"] == "tts.end":
                    break

            # 2. Add customer & record entries via WebSocket
            # Mode hint: "gave" (You gave), spoken direction: absent ("Ramesh 500")
            # Result: Mode hint wins -> RECORD_CREDIT, direction_overridden: False
            ws.send_text(
                json.dumps({
                    "t": "ptt.down",
                    "mode": "gave",
                    "clip_id": "clip_01_ramesh_credit_340",
                })
            )
            # Inject text directly with mode hint
            ws.send_text(
                json.dumps({
                    "t": "mock.inject",
                    "text": "Ramesh Kumar 500",
                    "mode": "gave",
                })
            )

            # Collect messages
            seen_action = None
            seen_summary = None
            while True:
                pkt = ws.receive()
                if pkt.get("text") is not None:
                    m = json.loads(pkt["text"])
                    if m["t"] == "action":
                        seen_action = m
                    elif m["t"] == "summary":
                        seen_summary = m
                    elif m["t"] == "tts.end":
                        break

            assert seen_action is not None
            assert seen_action["type"] in ("CREDIT", "CREDIT_GIVEN")
            assert seen_action["amount_inr"] == 500
            assert seen_action["customer"] == "Ramesh Kumar"
            assert seen_action["direction_overridden"] is False
            assert seen_summary is not None
            assert seen_summary["owing_count"] == 1
            assert seen_summary["total_owed"] == 500

            # 3. Spoken Direction Override:
            # Mode hint is "got" (You got), BUT spoken direction is explicitly "udhar" (credit/gave)
            # Result: Speech wins -> CREDIT, direction_overridden: True
            ws.send_text(
                json.dumps({
                    "t": "ptt.down",
                    "mode": "got",
                })
            )
            ws.send_text(
                json.dumps({
                    "t": "mock.inject",
                    "text": "Ramesh Kumar 200 udhar",
                    "mode": "got",
                })
            )

            seen_action2 = None
            while True:
                pkt = ws.receive()
                if pkt.get("text") is not None:
                    m = json.loads(pkt["text"])
                    if m["t"] == "action":
                        seen_action2 = m
                    elif m["t"] == "tts.end":
                        break

            assert seen_action2 is not None
            assert seen_action2["type"] in ("CREDIT", "CREDIT_GIVEN")
            assert seen_action2["amount_inr"] == 200
            assert seen_action2["direction_overridden"] is True

            # 4. Spoken Direction Override in reverse:
            # Mode hint is "gave" (You gave), BUT spoken direction is explicitly "jama" (payment/got)
            # Result: Speech wins -> PAYMENT, direction_overridden: True
            ws.send_text(
                json.dumps({
                    "t": "ptt.down",
                    "mode": "gave",
                })
            )
            ws.send_text(
                json.dumps({
                    "t": "mock.inject",
                    "text": "Ramesh Kumar 300 jama",
                    "mode": "gave",
                })
            )

            seen_action3 = None
            while True:
                pkt = ws.receive()
                if pkt.get("text") is not None:
                    m = json.loads(pkt["text"])
                    if m["t"] == "action":
                        seen_action3 = m
                    elif m["t"] == "tts.end":
                        break

            assert seen_action3 is not None
            assert seen_action3["type"] in ("PAYMENT", "PAYMENT_RECEIVED")
            assert seen_action3["amount_inr"] == 300
            assert seen_action3["direction_overridden"] is True

            # 5. Client queries: received.query
            ws.send_text(json.dumps({"t": "received.query"}))
            rec_res = ws.receive_json()
            assert rec_res["t"] == "received.result"
            assert rec_res["range_total"] == 300
            assert len(rec_res["days"]) >= 1

            # 6. Client queries: customer.query
            store = app.state.ledger
            matches = store._customers_matching_sync("Ramesh Kumar")
            assert len(matches) > 0
            cid = matches[0].id
            ws.send_text(json.dumps({"t": "customer.query", "id": cid}))
            cust_res = ws.receive_json()
            assert cust_res["t"] == "customer.result"
            assert cust_res["customer_id"] == cid
            assert cust_res["customer_name"] == "Ramesh Kumar"
            assert cust_res["balance"] == 400  # 500 + 200 - 300 = 400
            assert cust_res["advance"] == 0
            assert cust_res["is_advance"] is False
            assert len(cust_res["timeline"]) == 3


@pytest.mark.asyncio
async def test_udhar_3000_then_returned_2998_multilingual(tmp_path: Path) -> None:
    """Test scenario: Udhar 3000, then user inputs 'he returned 2998' across languages.

    Must state the remaining payable amount is Rs 2 and correctly process across languages.
    """
    from server.session import Session
    from server.ledger.store import Ledger
    from server.speech.mocks import MockSTT, MockTTS
    from voice_khata.models import LanguageCode

    class DummyWS:
        def __init__(self):
            self.sent_texts: list[str] = []
            self.sent_bytes: list[bytes] = []

        async def send_text(self, t: str) -> None:
            self.sent_texts.append(t)

        async def send_bytes(self, b: bytes) -> None:
            self.sent_bytes.append(b)

    class CapturingTTS(MockTTS):
        def __init__(self):
            super().__init__()
            self.spoken_texts: list[str] = []

        async def synthesize(self, text: str):
            self.spoken_texts.append(text)
            async for chunk in super().synthesize(text):
                yield chunk

    # Scenario 1: Hindi credit 3000, English 'he returned 2998'
    db_file1 = tmp_path / "scenario1.db"
    ledger1 = Ledger(db_file1, enc_key="0" * 64)
    ws1 = DummyWS()
    tts1 = CapturingTTS()
    session1 = Session(
        transport=ws1,
        ledger=ledger1,
        nlu=None,
        stt=MockSTT(),
        tts=tts1,
        initial_lang=LanguageCode.HI_IN,
    )

    async def stream1():
        yield json.dumps({"t": "session.start", "lang": "hi"})
        await asyncio.sleep(0.05)
        yield json.dumps({"t": "inject_text", "text": "Sunil ko 3000 udhar diya", "mode": "gave"})
        await asyncio.sleep(0.15)
        yield json.dumps({"t": "inject_text", "text": "he returned 2998", "mode": "gave"})
        await asyncio.sleep(0.15)

    await session1.run(stream1())

    # Verify balance is 2 rupees
    sunil_matches = await ledger1.customers_matching("Sunil")
    assert len(sunil_matches) == 1
    bal1 = await ledger1.balance(sunil_matches[0].id)
    assert bal1 == 200  # 200 paise = 2 INR

    # Verify TTS spoken text states the remaining payable amount is 2 rupees
    assert any("The remaining payable amount is 2 rupees" in s for s in tts1.spoken_texts)

    # Scenario 2: Devanagari Hindi credit 3000, English 'Ramesh returned 2998'
    db_file2 = tmp_path / "scenario2.db"
    ledger2 = Ledger(db_file2, enc_key="0" * 64)
    ws2 = DummyWS()
    tts2 = CapturingTTS()
    session2 = Session(
        transport=ws2,
        ledger=ledger2,
        nlu=None,
        stt=MockSTT(),
        tts=tts2,
        initial_lang=LanguageCode.HI_IN,
    )

    async def stream2():
        yield json.dumps({"t": "session.start", "lang": "hi"})
        await asyncio.sleep(0.05)
        yield json.dumps({"t": "inject_text", "text": "रमेश को 3000 उधार दिया", "mode": "gave"})
        await asyncio.sleep(0.15)
        yield json.dumps({"t": "inject_text", "text": "Ramesh returned 2998", "mode": "gave"})
        await asyncio.sleep(0.15)

    await session2.run(stream2())

    ramesh_matches = await ledger2.customers_matching("Ramesh")
    assert len(ramesh_matches) == 1
    bal2 = await ledger2.balance(ramesh_matches[0].id)
    assert bal2 == 200  # 200 paise = 2 INR
    assert any("The remaining payable amount is 2 rupees" in s for s in tts2.spoken_texts)

