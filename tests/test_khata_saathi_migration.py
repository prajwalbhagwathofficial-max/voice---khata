"""KhataSaathi migration and event model validation tests.

Covers all 6 requirements from AGENTS.md / PS2 spec:
1. Migration on fixture DB built with OLD schema (v1/v2), with balances identical before and after (x 100).
2. Signed-sum equals effective-sum on 1,000 random operations vs reference model.
3. Reverse-twice rejected (cannot reverse already reversed event or a REVERSAL).
4. Idempotent retry on command_id and event_id.
5. Atomic correct() appending REVERSAL + new event in ONE transaction.
6. Grep test that fails on float use in ledger modules.
"""

from __future__ import annotations

import ast
import random
import re
import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from server.ledger import (
    FieldCipher,
    IdempotencyConflictError,
    Ledger,
    LedgerEventInput,
    LedgerEventType,
    LedgerNotFoundError,
    LedgerValidationError,
    generate_key_b64,
)
from server.ledger.models import _strict_int_paise


# ==============================================================================
# 1. Migration on fixture DB built with OLD schema
# ==============================================================================
@pytest.mark.asyncio
async def test_migration_on_old_schema_fixture_db_preserves_balances_x100(
    tmp_path: Path,
) -> None:
    """Test ordered migration from legacy schema (v1 or v2 with amount_inr, voids_event_id).
    
    Verifies:
    - PRAGMA user_version upgrades to 3.
    - Columns amount_paise, reverses_event_id, business_date, command_id exist.
    - Tables customer_aliases and command_audit exist.
    - REVERSAL rows copy amount and customer of target.
    - Balances identical before and after (x 100 paise).
    """
    db_file = tmp_path / "legacy_v2.db"
    key = generate_key_b64()
    cipher = FieldCipher(key)

    # Build fixture DB with schema v2 (user_version = 2)
    conn = sqlite3.connect(str(db_file))
    conn.execute("PRAGMA foreign_keys = OFF;")
    conn.execute(
        """
        CREATE TABLE customers (
            id TEXT PRIMARY KEY,
            name_enc BLOB NOT NULL,
            name_norm TEXT NOT NULL,
            script TEXT NOT NULL,
            created_ts INTEGER NOT NULL
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE events (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL UNIQUE,
            type TEXT NOT NULL,
            customer_id TEXT NOT NULL REFERENCES customers(id),
            amount_inr INTEGER NOT NULL,
            effective_date TEXT NOT NULL,
            ts INTEGER NOT NULL,
            voids_event_id TEXT NULL UNIQUE REFERENCES events(event_id),
            note_enc BLOB NULL,
            session_scope TEXT NULL
        );
        """
    )
    conn.execute("PRAGMA user_version = 2;")

    # Populate 3 customers:
    # 1. Ramesh: Credit 500, Payment 150 -> Net 350 INR
    # 2. Suresh: Credit 1000, Voided, Credit 200 -> Net 200 INR
    # 3. Lakshmi: Payment 300 (advance) -> Net -300 INR
    c1_name = cipher.encrypt_str("Ramesh Kumar", aad=b"customer:c_ramesh:name")
    c2_name = cipher.encrypt_str("Suresh Patel", aad=b"customer:c_suresh:name")
    c3_name = cipher.encrypt_str("Lakshmi Rao", aad=b"customer:c_lakshmi:name")

    conn.execute("INSERT INTO customers VALUES (?, ?, ?, ?, ?)", ("c_ramesh", c1_name, "RMS", "latin", 1000))
    conn.execute("INSERT INTO customers VALUES (?, ?, ?, ?, ?)", ("c_suresh", c2_name, "SRS", "latin", 2000))
    conn.execute("INSERT INTO customers VALUES (?, ?, ?, ?, ?)", ("c_lakshmi", c3_name, "LKS", "latin", 3000))

    # Ramesh events
    conn.execute(
        "INSERT INTO events VALUES (1, 'ev_1', 'CREDIT', 'c_ramesh', 500, '2026-11-18', 1000, NULL, NULL, 's1')"
    )
    conn.execute(
        "INSERT INTO events VALUES (2, 'ev_2', 'PAYMENT', 'c_ramesh', 150, '2026-11-18', 2000, NULL, NULL, 's1')"
    )

    # Suresh events (including a VOID)
    conn.execute(
        "INSERT INTO events VALUES (3, 'ev_3', 'CREDIT', 'c_suresh', 1000, '2026-11-18', 3000, NULL, NULL, 's2')"
    )
    # Legacy VOID rows had amount_inr = 0 and customer_id was often arbitrary or target's
    conn.execute(
        "INSERT INTO events VALUES (4, 'ev_4', 'VOID', 'c_suresh', 0, '2026-11-18', 4000, 'ev_3', NULL, 's2')"
    )
    conn.execute(
        "INSERT INTO events VALUES (5, 'ev_5', 'CREDIT', 'c_suresh', 200, '2026-11-18', 5000, NULL, NULL, 's2')"
    )

    # Lakshmi events
    conn.execute(
        "INSERT INTO events VALUES (6, 'ev_6', 'PAYMENT', 'c_lakshmi', 300, '2026-11-18', 6000, NULL, NULL, 's3')"
    )

    conn.commit()
    conn.close()

    expected_balances_paise = {
        "c_ramesh": 350 * 100,
        "c_suresh": 200 * 100,
        "c_lakshmi": -300 * 100,
    }

    # Open with KhataSaathi Ledger to trigger migration
    ledger = Ledger(db_file, enc_key=key)

    # Verify PRAGMA user_version is 3
    conn_migrated = sqlite3.connect(str(db_file))
    try:
        user_ver = conn_migrated.execute("PRAGMA user_version;").fetchone()[0]
        assert user_ver == 3

        # Verify new tables
        table_names = [
            r[0]
            for r in conn_migrated.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        assert "customer_aliases" in table_names
        assert "command_audit" in table_names

        # Verify events table schema
        ev_cols = {
            r[1]: r[2]
            for r in conn_migrated.execute("PRAGMA table_info(events);").fetchall()
        }
        assert "amount_paise" in ev_cols
        assert "reverses_event_id" in ev_cols
        assert "business_date" in ev_cols
        assert "command_id" in ev_cols
        assert "amount_inr" not in ev_cols
        assert "voids_event_id" not in ev_cols

        # Check migrated VOID -> REVERSAL row copied target amount (1000 INR = 100000 paise)
        rev_row = conn_migrated.execute(
            "SELECT type, reverses_event_id, amount_paise, customer_id FROM events WHERE event_id = 'ev_4'"
        ).fetchone()
        assert rev_row[0] == "REVERSAL"
        assert rev_row[1] == "ev_3"
        assert rev_row[2] == 100000
        assert rev_row[3] == "c_suresh"

        # Check event types: CREDIT -> CREDIT_GIVEN, PAYMENT -> PAYMENT_RECEIVED
        ev1_type = conn_migrated.execute(
            "SELECT type FROM events WHERE event_id = 'ev_1'"
        ).fetchone()[0]
        assert ev1_type == "CREDIT_GIVEN"

        ev2_type = conn_migrated.execute(
            "SELECT type FROM events WHERE event_id = 'ev_2'"
        ).fetchone()[0]
        assert ev2_type == "PAYMENT_RECEIVED"

    finally:
        conn_migrated.close()

    # Verify balances match exactly before and after (x 100 paise)
    for cid, exp_bal in expected_balances_paise.items():
        actual_bal = await ledger.balance(cid)
        assert actual_bal == exp_bal

    # Verify effective events
    eff_ramesh = await ledger.effective_events("c_ramesh")
    assert len(eff_ramesh) == 2
    eff_suresh = await ledger.effective_events("c_suresh")
    assert len(eff_suresh) == 1
    assert eff_suresh[0].event_id == "ev_5"


# ==============================================================================
# 2. Signed-sum equals effective-sum on 1,000 random operations vs reference model
# ==============================================================================
class FuzzReferenceModel:
    def __init__(self, customers: list[str]) -> None:
        self.customers = set(customers)
        self.events: dict[str, tuple[str, str, int]] = {}  # eid -> (type, cid, amt_paise)
        self.reversed_events: set[str] = set()

    def add_event(self, eid: str, ev_type: str, cid: str, amt_paise: int) -> None:
        self.events[eid] = (ev_type, cid, amt_paise)

    def reverse_event(self, target_eid: str) -> None:
        self.reversed_events.add(target_eid)

    def balance(self, cid: str) -> int:
        bal = 0
        for eid, (ev_type, c, amt) in self.events.items():
            if c != cid:
                continue
            if eid in self.reversed_events:
                continue
            if ev_type == "CREDIT_GIVEN":
                bal += amt
            elif ev_type == "PAYMENT_RECEIVED":
                bal -= amt
        return bal


@pytest.mark.asyncio
async def test_signed_sum_equals_effective_sum_1000_random_operations_vs_reference(
    tmp_path: Path,
) -> None:
    """1,000 random operations against Ledger vs reference model.
    
    Verifies:
    - Signed sum over ALL rows (Ledger.balance) == effective sum (sum over effective_events)
    - Both equal the pure reference model balance
    - outstanding_total matches sum of positive balances
    """
    db_file = tmp_path / "fuzz_khata_1000.db"
    key = generate_key_b64()
    ledger = Ledger(db_file, enc_key=key)

    rng = random.Random(42)
    customer_ids: list[str] = []
    for i in range(10):
        c = await ledger.add_customer(f"Customer {i}", customer_id=f"cust_{i}")
        customer_ids.append(c.id)

    ref = FuzzReferenceModel(customer_ids)
    active_eids: list[str] = []
    all_cmd_ids: set[str] = set()

    for op_i in range(1000):
        roll = rng.random()
        cid = rng.choice(customer_ids)

        if roll < 0.12 and active_eids:
            # Operation 1: reverse an active event
            target_eid = rng.choice(active_eids)
            active_eids.remove(target_eid)
            rev_cmd = f"cmd_rev_{op_i}"
            all_cmd_ids.add(rev_cmd)
            await ledger.reverse(target_eid, reason="fuzz reversal", command_id=rev_cmd)
            ref.reverse_event(target_eid)

        elif roll < 0.20 and active_eids:
            # Operation 2: atomic correct() on an active event
            target_eid = rng.choice(active_eids)
            active_eids.remove(target_eid)
            new_amt = rng.randint(50, 10000) * 100  # whole rupees in paise
            new_eid = f"ev_corr_{op_i}"
            corr_cmd = f"cmd_corr_{op_i}"
            all_cmd_ids.add(corr_cmd)

            target_type, target_cid, _ = ref.events[target_eid]
            new_fields = {
                "event_id": new_eid,
                "amount_paise": new_amt,
                "note": "corrected in fuzz",
            }
            await ledger.correct(target_eid, new_fields, command_id=corr_cmd)
            ref.reverse_event(target_eid)
            ref.add_event(new_eid, target_type, target_cid, new_amt)
            active_eids.append(new_eid)

        elif roll < 0.28 and all_cmd_ids:
            # Operation 3: idempotent retry of an existing command_id
            retry_cmd = rng.choice(list(all_cmd_ids))
            # Resubmit dummy event with same command_id -> should return existing without changing state
            dummy_inp = LedgerEventInput(
                command_id=retry_cmd,
                type=LedgerEventType.CREDIT_GIVEN,
                customer_id=cid,
                amount_paise=10000,
            )
            await ledger.post_event(dummy_inp)

        else:
            # Operation 4: post new credit or payment
            ev_type = (
                LedgerEventType.CREDIT_GIVEN
                if rng.random() < 0.6
                else LedgerEventType.PAYMENT_RECEIVED
            )
            amt_paise = rng.randint(10, 5000) * 100  # 10 to 5000 INR
            eid = f"ev_fuzz_{op_i}"
            cmd = f"cmd_fuzz_{op_i}"
            all_cmd_ids.add(cmd)

            inp = LedgerEventInput(
                event_id=eid,
                command_id=cmd,
                type=ev_type,
                customer_id=cid,
                amount_paise=amt_paise,
            )
            await ledger.post_event(inp)
            ref.add_event(eid, ev_type.value, cid, amt_paise)
            active_eids.append(eid)

    # Post-run invariants verification across all customers
    total_positive_owed = 0
    for cid in customer_ids:
        # 1. Signed-sum over ALL rows via balance()
        signed_bal = await ledger.balance(cid)

        # 2. Effective sum over effective_events()
        eff_evs = await ledger.effective_events(cid)
        eff_bal = sum(
            e.amount_paise if e.type == LedgerEventType.CREDIT_GIVEN else -e.amount_paise
            for e in eff_evs
        )

        # 3. Ground truth reference balance
        ref_bal = ref.balance(cid)

        assert signed_bal == eff_bal, f"Customer {cid}: signed sum {signed_bal} != effective sum {eff_bal}"
        assert signed_bal == ref_bal, f"Customer {cid}: signed sum {signed_bal} != reference {ref_bal}"

        if signed_bal > 0:
            total_positive_owed += signed_bal

    # Check outstanding total
    out_total = await ledger.outstanding_total()
    assert out_total == total_positive_owed


# ==============================================================================
# 3. Reverse-twice rejected
# ==============================================================================
@pytest.mark.asyncio
async def test_reverse_twice_and_reverse_reversal_rejected(tmp_path: Path) -> None:
    """Ledger.reverse() must refuse to reverse a REVERSAL or an already reversed event."""
    db_file = tmp_path / "reverse_test.db"
    ledger = Ledger(db_file, enc_key=generate_key_b64())
    cust = await ledger.add_customer("Test Customer", customer_id="c_rev_test")

    # Post initial credit
    ev = await ledger.post_event(
        LedgerEventInput(
            event_id="ev_to_rev",
            type=LedgerEventType.CREDIT_GIVEN,
            customer_id=cust.id,
            amount_paise=45000,  # 450 INR
        )
    )
    assert await ledger.balance(cust.id) == 45000

    # 1. First reversal succeeds
    rev = await ledger.reverse("ev_to_rev", reason="customer returned goods")
    assert rev.type == LedgerEventType.REVERSAL
    assert rev.reverses_event_id == "ev_to_rev"
    assert rev.amount_paise == 45000
    assert await ledger.balance(cust.id) == 0

    # 2. Second reversal of same event raises LedgerValidationError
    with pytest.raises(LedgerValidationError, match="already reversed"):
        await ledger.reverse("ev_to_rev", reason="duplicate reversal attempt")

    # 3. Reversing the REVERSAL itself raises LedgerValidationError
    with pytest.raises(LedgerValidationError, match="Cannot reverse a reversal"):
        await ledger.reverse(rev.event_id, reason="attempting to reverse reversal")

    # 4. Reversing non-existent event raises LedgerNotFoundError
    with pytest.raises(LedgerNotFoundError):
        await ledger.reverse("non_existent_event_id")


# ==============================================================================
# 4. Idempotent retry on command_id and event_id
# ==============================================================================
@pytest.mark.asyncio
async def test_idempotent_retry_on_command_id_and_conflict(tmp_path: Path) -> None:
    """post_event() must be idempotent on command_id without creating duplicate rows."""
    db_file = tmp_path / "idem_test.db"
    ledger = Ledger(db_file, enc_key=generate_key_b64())
    cust = await ledger.add_customer("Idem Customer", customer_id="c_idem")

    cmd_id = "cmd_unique_order_101"
    inp = LedgerEventInput(
        command_id=cmd_id,
        event_id="ev_idem_first",
        type=LedgerEventType.CREDIT_GIVEN,
        customer_id=cust.id,
        amount_paise=25000,
        note="wheat flour",
    )

    first_rec = await ledger.post_event(inp)
    assert first_rec.command_id == cmd_id
    assert first_rec.amount_paise == 25000

    # Exact retry with same command_id returns existing event record
    retry_rec = await ledger.post_event(inp)
    assert retry_rec.seq == first_rec.seq
    assert retry_rec.event_id == first_rec.event_id
    assert retry_rec.command_id == cmd_id

    # Verify only ONE event row exists in the database
    conn = sqlite3.connect(str(db_file))
    try:
        count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        assert count == 1
    finally:
        conn.close()

    # Conflicting payload with same event_id raises IdempotencyConflictError
    conflicting = LedgerEventInput(
        event_id="ev_idem_first",
        type=LedgerEventType.CREDIT_GIVEN,
        customer_id=cust.id,
        amount_paise=99000,
    )
    with pytest.raises(IdempotencyConflictError):
        await ledger.post_event(conflicting)


# ==============================================================================
# 5. Atomic correct()
# ==============================================================================
@pytest.mark.asyncio
async def test_atomic_correct_appends_reversal_plus_new_event_in_one_tx(
    tmp_path: Path,
) -> None:
    """correct(event_id, new_fields) must append REVERSAL + corrected event in ONE transaction."""
    db_file = tmp_path / "correct_test.db"
    ledger = Ledger(db_file, enc_key=generate_key_b64())
    cust = await ledger.add_customer("Correct Customer", customer_id="c_correct")

    # Record initial wrong payment: 1,000 INR (100,000 paise)
    wrong_ev = await ledger.post_event(
        LedgerEventInput(
            event_id="ev_wrong_payment",
            type=LedgerEventType.PAYMENT_RECEIVED,
            customer_id=cust.id,
            amount_paise=100000,
            business_date="2026-11-18",
            note="cash wrong entry",
        )
    )
    assert await ledger.balance(cust.id) == -100000

    # Atomically correct it to 100 INR (10,000 paise)
    new_fields = {
        "event_id": "ev_corrected_payment",
        "amount_paise": 10000,
        "note": "cash corrected entry",
    }
    rev_ev, new_ev = await ledger.correct("ev_wrong_payment", new_fields, command_id="cmd_corr_1")

    assert rev_ev.type == LedgerEventType.REVERSAL
    assert rev_ev.reverses_event_id == "ev_wrong_payment"
    assert rev_ev.amount_paise == 100000
    assert new_ev.type == LedgerEventType.PAYMENT_RECEIVED
    assert new_ev.amount_paise == 10000
    assert new_ev.event_id == "ev_corrected_payment"

    # Balance is now -10,000 paise (-100 INR)
    assert await ledger.balance(cust.id) == -10000

    # Effective events only contains the corrected payment
    eff = await ledger.effective_events(cust.id)
    assert len(eff) == 1
    assert eff[0].event_id == "ev_corrected_payment"

    # Attempting to correct an already reversed event must fail atomically
    with pytest.raises(LedgerValidationError, match="already reversed"):
        await ledger.correct("ev_wrong_payment", {"amount_paise": 5000})


# ==============================================================================
# 6. Grep test that fails on float use in ledger modules
# ==============================================================================
def test_grep_no_float_use_in_ledger_modules() -> None:
    """Scans server/ledger/*.py to enforce money arithmetic is strictly integer paise."""
    ledger_dir = Path("server/ledger")
    assert ledger_dir.is_dir(), "server/ledger directory must exist"

    py_files = list(ledger_dir.glob("*.py"))
    assert len(py_files) >= 3, "Expected at least models.py, store.py, crypto.py in server/ledger"

    disallowed_patterns = [
        re.compile(r"\bfloat\s*\("),            # float(...) conversions
        re.compile(r":\s*float\b"),              # float type annotations on money fields
        re.compile(r"->\s*float\b"),             # float return types
        re.compile(r"\bamount_paise\s*[:=]\s*.*float"),
    ]

    violations: list[str] = []

    for f in py_files:
        content = f.read_text(encoding="utf-8")
        # Ignore time.time() which returns float seconds for ts ms normalization
        lines = content.splitlines()
        for idx, line in enumerate(lines, start=1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""'):
                continue
            for pat in disallowed_patterns:
                if pat.search(line):
                    # Check if line is specifically doing time or float check test
                    if "time.time" in line or "isinstance(value, float)" in line:
                        continue
                    violations.append(f"{f.name}:{idx} -> {line.strip()}")

    assert not violations, f"Float money usage detected in ledger modules:\n" + "\n".join(violations)

    # Test _strict_int_paise validator directly rejects floats
    with pytest.raises(ValueError, match="strict Python integer"):
        _strict_int_paise(100.5)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="strict Python integer"):
        _strict_int_paise(100.0)  # type: ignore[arg-type]

    assert _strict_int_paise(10000) == 10000
