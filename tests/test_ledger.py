"""Tests for voice_khata.ledger (append-only triggers, computed balances, asyncio.shield)."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path

import pytest

from voice_khata.ledger import LedgerError, LedgerStore
from voice_khata.models import EventType, LanguageCode


@pytest.mark.asyncio
async def test_append_only_events_and_computed_balances(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path / "khata.db")

    ev1, bal1 = await store.record_credit(
        customer_name="Aarav Traders",
        amount_rupees=250,
        item_note="rice",
        language_code=LanguageCode.HI_IN,
    )
    assert ev1.event_type == EventType.CREDIT_GIVEN
    assert ev1.signed_amount_rupees == 250
    assert bal1.balance_rupees == 250
    assert bal1.event_count == 1

    ev2, bal2 = await store.record_credit(
        customer_name="  aarav   traders ",
        amount_rupees=100,
        item_note="oil",
        language_code=LanguageCode.EN_IN,
    )
    assert ev2.customer_id == ev1.customer_id
    assert bal2.balance_rupees == 350
    assert bal2.event_count == 2

    ev3, bal3 = await store.record_payment(
        customer_name="Aarav Traders",
        amount_rupees=200,
        language_code=LanguageCode.HI_IN,
    )
    assert ev3.event_type == EventType.PAYMENT_RECEIVED
    assert ev3.signed_amount_rupees == -200
    assert bal3.balance_rupees == 150
    assert bal3.event_count == 3

    summary = await store.get_shop_summary()
    assert summary.total_outstanding_rupees == 150
    assert summary.customer_count == 1
    assert summary.active_credit_customers == 1
    assert summary.total_events == 3


@pytest.mark.asyncio
async def test_reversal_is_append_only_and_cannot_reverse_twice(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path / "khata.db")

    ev1, _ = await store.record_credit(customer_name="Kavya", amount_rupees=400)
    rev, orig, bal = await store.reverse_event(target_event_id=ev1.id)

    assert orig.id == ev1.id
    assert rev.event_type == EventType.REVERSAL
    assert rev.signed_amount_rupees == -400
    assert rev.reverses_event_id == ev1.id
    assert bal.balance_rupees == 0
    assert bal.event_count == 2

    with pytest.raises(LedgerError):
        await store.reverse_event(target_event_id=ev1.id)

    assert await store.get_last_reversible_event("Kavya") is None


@pytest.mark.asyncio
async def test_sqlite_triggers_forbid_update_and_delete(tmp_path: Path) -> None:
    db_file = tmp_path / "khata.db"
    store = LedgerStore(db_file)
    ev, _ = await store.record_credit(customer_name="Nikhil", amount_rupees=120)

    conn = sqlite3.connect(db_file)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                "UPDATE ledger_events SET signed_amount_rupees = 999 WHERE id = ?",
                (ev.id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM ledger_events WHERE id = ?", (ev.id,))

        # Verify customers table has no phone_number or balance column
        cols = {row[1] for row in conn.execute("PRAGMA table_info(customers)").fetchall()}
        assert "balance" not in cols
        assert "balance_rupees" not in cols
        assert "phone" not in cols
        assert "phone_number" not in cols
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_idempotent_event_uid_does_not_double_post(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path / "khata.db")
    ev1, bal1 = await store.record_credit(
        customer_name="Deepak", amount_rupees=300, event_uid="uid-fixed-1"
    )
    ev2, bal2 = await store.record_credit(
        customer_name="Deepak", amount_rupees=300, event_uid="uid-fixed-1"
    )
    assert ev1.id == ev2.id
    assert bal1.balance_rupees == 300
    assert bal2.balance_rupees == 300
    assert bal2.event_count == 1


@pytest.mark.asyncio
async def test_shielded_commit_completes_even_if_caller_task_is_cancelled(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path / "khata.db")
    original_append = store._append_entry_sync
    entered = asyncio.Event()

    def slow_append(*args, **kwargs):
        # Signal that the commit thread started, then simulate brief disk latency
        time.sleep(0.08)
        return original_append(*args, **kwargs)

    store._append_entry_sync = slow_append  # type: ignore[method-assign]

    async def caller() -> None:
        entered.set()
        await store.record_credit(customer_name="Shreya", amount_rupees=500)

    task = asyncio.create_task(caller())
    await entered.wait()
    await asyncio.sleep(0.02)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    # Give the shielded worker thread time to finish committing
    await asyncio.sleep(0.15)
    bal = await store.get_customer_balance("Shreya")
    assert bal is not None
    assert bal.balance_rupees == 500
    assert bal.event_count == 1
