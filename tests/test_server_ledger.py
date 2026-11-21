"""Comprehensive tests for server.ledger (WAL, AES-GCM, phonetic keys, 1000-event model, concurrency, wipe, seed)."""

from __future__ import annotations

import asyncio
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from server.ledger import (
    FieldCipher,
    IdempotencyConflictError,
    Ledger,
    LedgerEventInput,
    LedgerEventType,
    LedgerNotFoundError,
    LedgerValidationError,
    detect_script,
    generate_key_b64,
    phonetic_key,
    seed_demo_data,
)


def _new_ledger(tmp_path: Path, name: str = "ledger.db", key: str | None = None) -> Ledger:
    return Ledger(tmp_path / name, enc_key=key or generate_key_b64())


def test_crypto_aes_gcm_random_96bit_nonce_and_cross_script_phonetic_key() -> None:
    key = generate_key_b64()
    cipher = FieldCipher(key)

    ct1 = cipher.encrypt_str("Ramesh Kumar", aad=b"customer:c1:name")
    ct2 = cipher.encrypt_str("Ramesh Kumar", aad=b"customer:c1:name")
    assert ct1 != ct2  # fresh random 96-bit nonce per field
    assert ct1[:12] != ct2[:12]
    assert b"Ramesh" not in ct1
    assert cipher.decrypt_str(ct1, aad=b"customer:c1:name") == "Ramesh Kumar"

    # Phonetic key is NOT plaintext and matches across Latin, Devanagari, Telugu
    k_lat = phonetic_key("Ramesh")
    k_dev = phonetic_key("रमेश")
    k_tel = phonetic_key("రమేష్")
    assert k_lat == k_dev == k_tel
    assert "ramesh" not in k_lat.lower()

    assert phonetic_key("Venkatesh") == phonetic_key("వెంకటేష్")
    assert detect_script("వెంకటేష్") == "telugu"
    assert detect_script("रवि वर्मा") == "devanagari"
    assert detect_script("Ramesh Kumar") == "latin"


@pytest.mark.asyncio
async def test_sqlite_wal_pragmas_env_key_encryption_and_append_only_triggers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = generate_key_b64()
    monkeypatch.setenv("LEDGER_ENC_KEY", key)
    db_file = tmp_path / "env_ledger.db"
    ledger = Ledger(db_file)  # picks up LEDGER_ENC_KEY from env

    cust = await ledger.add_customer("SecretCustomerName", customer_id="c1")
    ev = await ledger.record(
        LedgerEventInput(
            event_id="ev_enc_1",
            type=LedgerEventType.CREDIT,
            customer_id=cust.id,
            amount_inr=250,
            effective_date="2026-11-18",
            note="SensitiveNoteText",
        )
    )

    conn = sqlite3.connect(db_file)
    try:
        # Check WAL mode, exact columns, and encrypted storage
        assert conn.execute("PRAGMA journal_mode;").fetchone()[0].lower() == "wal"

        cust_cols = [r[1] for r in conn.execute("PRAGMA table_info(customers);").fetchall()]
        assert cust_cols[:5] == ["id", "name_enc", "name_norm", "script", "created_ts"]

        ev_cols = [r[1] for r in conn.execute("PRAGMA table_info(events);").fetchall()]
        assert "amount_paise" in ev_cols
        assert "reverses_event_id" in ev_cols
        assert "command_id" in ev_cols
        assert "business_date" in ev_cols

        raw_cust = conn.execute("SELECT name_enc, name_norm FROM customers WHERE id = 'c1'").fetchone()
        assert b"SecretCustomerName" not in bytes(raw_cust[0])
        assert "secretcustomername" not in str(raw_cust[1]).lower()

        raw_ev = conn.execute("SELECT note_enc FROM events WHERE event_id = 'ev_enc_1'").fetchone()
        assert b"SensitiveNoteText" not in bytes(raw_ev[0])

        # Append-only triggers forbid UPDATE and DELETE
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("UPDATE events SET amount_paise = 100 WHERE seq = ?", (ev.seq,))
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM events WHERE seq = ?", (ev.seq,))
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_idempotent_retry_and_conflict_detection(tmp_path: Path) -> None:
    ledger = _new_ledger(tmp_path)
    cust = await ledger.add_customer("Suresh Patel", customer_id="c_suresh")

    ev = LedgerEventInput(
        event_id="ev_idem_1",
        type=LedgerEventType.CREDIT,
        customer_id=cust.id,
        amount_inr=340,
        effective_date="2026-11-18",
        note="rice",
        session_scope="sess-A",
    )
    first = await ledger.record(ev)
    retry = await ledger.record(ev)

    assert retry.seq == first.seq
    assert retry.event_id == first.event_id
    assert await ledger.balance(cust) == 34000

    # Conflicting payload with same event_id must fail
    conflicting = LedgerEventInput(
        event_id="ev_idem_1",
        type=LedgerEventType.CREDIT,
        customer_id=cust.id,
        amount_inr=500,
        effective_date="2026-11-18",
    )
    with pytest.raises(IdempotencyConflictError):
        await ledger.record(conflicting)
    assert await ledger.balance(cust) == 34000


@pytest.mark.asyncio
async def test_void_then_rerecord_and_session_scope(tmp_path: Path) -> None:
    ledger = _new_ledger(tmp_path)
    cust = await ledger.add_customer("Meena Devi", customer_id="c_meena")

    ev1 = await ledger.record(
        LedgerEventInput(
            event_id="ev_wrong",
            type=LedgerEventType.CREDIT,
            customer_id=cust.id,
            amount_inr=900,
            effective_date="2026-11-18",
            session_scope="sess-1",
        )
    )
    assert await ledger.balance(cust.id) == 90000
    assert await ledger.total_credit_on("2026-11-18") == 90000
    assert (await ledger.last_event("sess-1")).event_id == "ev_wrong"  # type: ignore[union-attr]

    # Void the mistaken credit (and retry void idempotently)
    v1 = await ledger.void(ev1.event_id, void_event_id="void_wrong", session_scope="sess-1")
    v1_retry = await ledger.void(ev1.event_id, void_event_id="void_wrong", session_scope="sess-1")
    assert v1.seq == v1_retry.seq
    assert v1.reverses_event_id == ev1.event_id
    assert await ledger.balance(cust.id) == 0
    assert await ledger.total_credit_on("2026-11-18") == 0

    # Cannot void a VOID event or missing event
    with pytest.raises(LedgerValidationError):
        await ledger.void(v1.event_id)
    with pytest.raises(LedgerNotFoundError):
        await ledger.void("does_not_exist")

    # Re-record corrected amount
    ev2 = await ledger.record(
        LedgerEventInput(
            event_id="ev_corrected",
            type=LedgerEventType.CREDIT,
            customer_id=cust.id,
            amount_inr=350,
            effective_date="2026-11-18",
            session_scope="sess-2",
        )
    )
    assert await ledger.balance(cust.id) == 35000
    assert await ledger.total_credit_on("2026-11-18") == 35000
    assert (await ledger.last_event("sess-1")).event_id == v1.event_id  # type: ignore[union-attr]
    assert (await ledger.last_event("sess-2")).event_id == ev2.event_id  # type: ignore[union-attr]
    assert (await ledger.last_event(None)).event_id == ev2.event_id  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_balance_correctness_across_1000_random_events_vs_reference_model(
    tmp_path: Path,
) -> None:
    ledger = _new_ledger(tmp_path, name="fuzz_1000.db")
    rng = random.Random(20261118)

    customer_ids: list[str] = []
    for idx in range(10):
        c = await ledger.add_customer(f"Customer {chr(65 + idx)}", customer_id=f"c_{idx}")
        customer_ids.append(c.id)

    base_day = date(2026, 11, 1)
    dates = [(base_day + timedelta(days=d)).isoformat() for d in range(10)]
    sessions = ["s1", "s2", "s3"]

    # Reference model state
    ref_events: dict[str, tuple[LedgerEventType, str, int, str]] = {}
    ref_voided: set[str] = set()
    ref_last_by_session: dict[str, str] = {}
    active_ids: list[str] = []

    for i in range(1000):
        roll = rng.random()
        sess = rng.choice(sessions)

        if roll < 0.18 and active_ids:
            # Void a random active event
            target_id = rng.choice(active_ids)
            active_ids.remove(target_id)
            vid = f"v_{i}"
            vrec = await ledger.void(target_id, void_event_id=vid, session_scope=sess)
            ref_voided.add(target_id)
            ref_last_by_session[sess] = vrec.event_id
        elif roll < 0.25 and ref_events:
            # Idempotent retry of an already recorded event
            retry_id = rng.choice(list(ref_events.keys()))
            ev_type, cid, amt, eff_date = ref_events[retry_id]
            await ledger.record(
                LedgerEventInput(
                    event_id=retry_id,
                    type=ev_type,
                    customer_id=cid,
                    amount_inr=amt,
                    effective_date=eff_date,
                    session_scope=sess,
                )
            )
        else:
            ev_type = LedgerEventType.CREDIT if rng.random() < 0.6 else LedgerEventType.PAYMENT
            cid = rng.choice(customer_ids)
            amt = rng.randint(1, 5000)
            eff_date = rng.choice(dates)
            eid = f"e_{i}"
            await ledger.record(
                LedgerEventInput(
                    event_id=eid,
                    type=ev_type,
                    customer_id=cid,
                    amount_inr=amt,
                    effective_date=eff_date,
                    note=f"item {i}" if i % 5 == 0 else None,
                    session_scope=sess,
                )
            )
            ref_events[eid] = (ev_type, cid, amt, eff_date)
            active_ids.append(eid)
            ref_last_by_session[sess] = eid

    # Compute exact reference balances and daily credit totals
    expected_bal = {cid: 0 for cid in customer_ids}
    expected_credit_on = {d: 0 for d in dates}
    for eid, (ev_type, cid, amt, eff_date) in ref_events.items():
        if eid in ref_voided:
            continue
        if ev_type == LedgerEventType.CREDIT:
            expected_bal[cid] += amt
            expected_credit_on[eff_date] += amt
        elif ev_type == LedgerEventType.PAYMENT:
            expected_bal[cid] -= amt

    # Compare balance(c) and balances_all()
    all_bals = await ledger.balances_all()
    assert len(all_bals) == len(customer_ids)
    for row in all_bals:
        assert row.balance_inr == expected_bal[row.customer.id]
        assert await ledger.balance(row.customer.id) == expected_bal[row.customer.id] * 100

    # Compare total_credit_on(date) for every date
    for d in dates:
        assert await ledger.total_credit_on(d) == expected_credit_on[d] * 100

    # Compare last_event(session_scope)
    for sess, expected_eid in ref_last_by_session.items():
        last = await ledger.last_event(sess)
        assert last is not None
        assert last.event_id == expected_eid


@pytest.mark.asyncio
async def test_concurrent_writers_across_tasks_and_multiple_ledger_connections(
    tmp_path: Path,
) -> None:
    key = generate_key_b64()
    db_file = tmp_path / "concurrent.db"
    ledger_a = Ledger(db_file, enc_key=key)
    ledger_b = Ledger(db_file, enc_key=key)

    cust = await ledger_a.add_customer("Ramesh Kumar", customer_id="c_conc")

    async def writer(instance: Ledger, prefix: str, count: int) -> None:
        for i in range(count):
            await instance.record(
                LedgerEventInput(
                    event_id=f"{prefix}_{i}",
                    type=LedgerEventType.CREDIT if i % 2 == 0 else LedgerEventType.PAYMENT,
                    customer_id=cust.id,
                    amount_inr=100 if i % 2 == 0 else 40,
                    effective_date="2026-11-18",
                    session_scope=prefix,
                )
            )

    async with asyncio.TaskGroup() as tg:
        tg.create_task(writer(ledger_a, "w1", 20))
        tg.create_task(writer(ledger_b, "w2", 20))
        tg.create_task(writer(ledger_a, "w3", 20))
        tg.create_task(writer(ledger_b, "w4", 20))

    # Each writer posts 10 credits (+1000) and 10 payments (-400) -> +600 per writer * 4 = 2400 INR = 240000 paise
    assert await ledger_a.balance(cust.id) == 240000
    assert await ledger_b.balance(cust.id) == 240000
    assert await ledger_a.total_credit_on("2026-11-18") == 400000


@pytest.mark.asyncio
async def test_wipe_all_clears_data_and_preserves_triggers(tmp_path: Path) -> None:
    db_file = tmp_path / "wipe.db"
    ledger = Ledger(db_file, enc_key=generate_key_b64())
    await seed_demo_data(ledger, base_date=date(2026, 11, 18))

    assert len(await ledger.balances_all()) == 9
    assert await ledger.last_event() is not None

    await ledger.wipe_all()

    assert await ledger.balances_all() == []
    assert await ledger.last_event() is None
    assert await ledger.total_credit_on("2026-11-18") == 0

    # Sequence resets and append-only triggers remain active after wipe_all
    cust = await ledger.add_customer("Fresh Customer", customer_id="c_fresh")
    ev = await ledger.record(
        LedgerEventInput(
            event_id="ev_after_wipe",
            type=LedgerEventType.CREDIT,
            customer_id=cust.id,
            amount_inr=100,
            effective_date="2026-11-18",
        )
    )
    assert ev.seq == 1

    conn = sqlite3.connect(db_file)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM events WHERE seq = 1")
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_seed_script_creates_8_customers_two_ramesh_and_telugu_name(
    tmp_path: Path,
) -> None:
    ledger = _new_ledger(tmp_path, name="seed.db")
    base = date(2026, 11, 18)

    # Seed twice to verify idempotency
    first = await seed_demo_data(ledger, base_date=base)
    second = await seed_demo_data(ledger, base_date=base)
    assert len(first) == 9
    assert len(second) == 9

    all_bals = await ledger.balances_all()
    assert len(all_bals) == 9

    # Two "Ramesh" customers matched across Latin, Devanagari, and Telugu queries
    ramesh_lat = await ledger.customers_matching("Ramesh")
    ramesh_hi = await ledger.customers_matching("रमेश")
    ramesh_te = await ledger.customers_matching("రమేష్")
    assert len(ramesh_lat) == 2
    assert {c.id for c in ramesh_lat} == {"cust_ramesh_1", "cust_ramesh_auto"}
    assert {c.id for c in ramesh_hi} == {"cust_ramesh_1", "cust_ramesh_auto"}
    assert {c.id for c in ramesh_te} == {"cust_ramesh_1", "cust_ramesh_auto"}

    # Telugu-script customer exists and matches both Telugu & Latin queries
    telugu_customers = [b.customer for b in all_bals if b.customer.script == "telugu"]
    assert len(telugu_customers) == 1
    assert telugu_customers[0].name == "వెంకటేష్"
    assert [c.id for c in await ledger.customers_matching("Venkatesh")] == ["cust_venkatesh_te"]
    assert [c.id for c in await ledger.customers_matching("వెంకటేష్")] == ["cust_venkatesh_te"]

    # Kannada-script customer exists
    kannada_customers = [b.customer for b in all_bals if b.customer.script == "kannada"]
    assert len(kannada_customers) == 1
    assert kannada_customers[0].name == "ಮಂಜುನಾಥ್"

    # Anil's 999 entry was voided and replaced with 125
    assert await ledger.balance("cust_anil") == 12500
    # Today's non-voided credits in paise: 115000 (Rs 1150: Suresh 175 + Meena 150 + Ravi 150 + Anil 125 + Manjunath 300 + Venkatesh 250)
    assert await ledger.total_credit_on(base) == 115000

    # Test payments_list, credits_list, total_payment_on, and summary_stats
    payments = await ledger.payments_list()
    assert len(payments) == 2
    assert payments[0].amount_inr in (140, 420)
    assert payments[0].customer_name in ("Ramesh Kumar", "Meena Devi")

    credits = await ledger.credits_list()
    assert len(credits) >= 6

    stats = await ledger.summary_stats()
    assert stats.debtor_count > 0
    assert stats.total_owed > 0

    # Record a payment today and verify total_payment_on
    today = date.today().isoformat()
    await ledger.record(
        LedgerEventInput(
            type=LedgerEventType.PAYMENT,
            customer_id="cust_anil",
            amount_inr=50,
            effective_date=today,
            note="today payment",
        )
    )
    assert await ledger.total_payment_on(today) >= 5000
    updated_stats = await ledger.summary_stats()
    assert updated_stats.total_received_today >= 50


@pytest.mark.asyncio
async def test_delete_event_directly_from_database(tmp_path: Path) -> None:
    ledger = _new_ledger(tmp_path, name="delete_test.db")
    cust = await ledger.add_customer("Deepak Patel", customer_id="c_deepak")

    # 1. Record credit 500 Rs (50,000 paise)
    cr_ev = await ledger.record(
        LedgerEventInput(
            type=LedgerEventType.CREDIT_GIVEN,
            customer_id=cust.id,
            amount_inr=500,
            effective_date="2026-11-21",
            note="Groceries",
        )
    )
    assert await ledger.balance(cust.id) == 50000

    # 2. Record payment 200 Rs (20,000 paise)
    pay_ev = await ledger.record(
        LedgerEventInput(
            type=LedgerEventType.PAYMENT_RECEIVED,
            customer_id=cust.id,
            amount_inr=200,
            effective_date="2026-11-21",
            note="Cash received",
        )
    )
    assert await ledger.balance(cust.id) == 30000

    # 3. Check presence in lists
    payments = await ledger.payments_list()
    assert any(p.event_id == pay_ev.event_id for p in payments)

    credits = await ledger.credits_list()
    assert any(c.event_id == cr_ev.event_id for c in credits)

    # 4. Directly delete payment transaction
    deleted = await ledger.delete_event(pay_ev.event_id)
    assert deleted is True

    # Balance restores to 500 Rs (50,000 paise)
    assert await ledger.balance(cust.id) == 50000

    # Gone from payments list
    payments_after = await ledger.payments_list()
    assert not any(p.event_id == pay_ev.event_id for p in payments_after)

    # 5. Directly delete credit transaction
    deleted_cr = await ledger.delete_event(cr_ev.event_id)
    assert deleted_cr is True

    # Balance is now 0
    assert await ledger.balance(cust.id) == 0

    credits_after = await ledger.credits_list()
    assert not any(c.event_id == cr_ev.event_id for c in credits_after)

    # 6. Verify non-existent delete returns False
    assert await ledger.delete_event("non_existent_id") is False

    # 7. Verify append-only triggers remain intact against raw SQL DELETE
    await ledger.record(
        LedgerEventInput(
            type=LedgerEventType.CREDIT_GIVEN,
            customer_id=cust.id,
            amount_inr=100,
            effective_date="2026-11-21",
        )
    )
    db_file = tmp_path / "delete_test.db"
    conn = sqlite3.connect(db_file)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM events")
    finally:
        conn.close()

