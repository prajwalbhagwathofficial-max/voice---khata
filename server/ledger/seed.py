"""Idempotent synthetic seed script for KhataSaathi supporting demo_clean and demo_full profiles."""

from __future__ import annotations

import asyncio
import os
from datetime import date, datetime, time, timedelta
from typing import Literal

from server.ledger.models import CustomerRecord, LedgerEventInput, LedgerEventType
from server.ledger.store import KOLKATA_TZ, Ledger

DEMO_CLEAN_CUSTOMERS: tuple[tuple[str, str], ...] = (
    ("cust_ramesh_1", "Ramesh Kumar"),
    ("cust_suresh", "Suresh Patel"),
    ("cust_meena", "Meena Devi"),
    ("cust_ravi_hi", "रवि वर्मा"),
    ("cust_lakshmi", "Lakshmi Rao"),
    ("cust_anil", "Anil Nair"),
)

DEMO_FULL_ADDITIONAL_CUSTOMERS: tuple[tuple[str, str], ...] = (
    ("cust_ramesh_auto", "Ramesh Auto"),
    ("cust_manjunath_kn", "ಮಂಜುನಾಥ್"),
    ("cust_venkatesh_te", "వెంకటేష్"),
)

SEED_CUSTOMERS = DEMO_CLEAN_CUSTOMERS + DEMO_FULL_ADDITIONAL_CUSTOMERS


async def seed_demo_data(
    ledger: Ledger,
    *,
    profile: Literal["demo_clean", "demo_full"] = "demo_full",
    base_date: date | None = None,
    force: bool = False,
) -> list[CustomerRecord]:
    """Seed synthetic kirana customers and sample append-only events idempotently.
    - demo_clean: Ramesh Kumar with zero balance + 5 others, no duplicate names.
    - demo_full: adds 'Ramesh Auto' as second Ramesh with alias, 1 Kannada-script name, 1 Telugu-script name.
    """
    if not force:
        cust_count = await ledger.customers_count()
        if cust_count > 0:
            return await ledger.customers_all()

    today = base_date or date.today()
    yesterday = today - timedelta(days=1)
    two_days_ago = today - timedelta(days=2)

    def _to_epoch_ms(d: date, hour: int = 12) -> int:
        dt = datetime.combine(d, time(hour, 0), tzinfo=KOLKATA_TZ)
        return int(dt.timestamp() * 1000)

    ts_today = _to_epoch_ms(today, 14)
    ts_yesterday = _to_epoch_ms(yesterday, 12)
    ts_two_days_ago = _to_epoch_ms(two_days_ago, 10)

    customers: list[CustomerRecord] = []

    # 1. Add base clean customers (Ramesh Kumar + 5 others, no duplicate names)
    for cid, name in DEMO_CLEAN_CUSTOMERS:
        customers.append(await ledger.add_customer(name, customer_id=cid))

    events: list[LedgerEventInput] = [
        # Ramesh Kumar: credit 34,000 paise (Rs 340) + payment 34,000 paise (Rs 340) => ZERO balance
        LedgerEventInput(
            event_id="seed_ev_001",
            command_id="cmd_seed_001",
            type=LedgerEventType.CREDIT_GIVEN,
            customer_id="cust_ramesh_1",
            amount_paise=34000,
            business_date=two_days_ago.isoformat(),
            effective_date=two_days_ago.isoformat(),
            ts=ts_two_days_ago,
            note="rice and dal",
            session_scope="seed",
        ),
        LedgerEventInput(
            event_id="seed_ev_002",
            command_id="cmd_seed_002",
            type=LedgerEventType.PAYMENT_RECEIVED,
            customer_id="cust_ramesh_1",
            amount_paise=34000,
            business_date=yesterday.isoformat(),
            effective_date=yesterday.isoformat(),
            ts=ts_yesterday,
            note="cash settled in full",
            session_scope="seed",
        ),
        # Suresh Patel: credit 17,500 paise (Rs 175)
        LedgerEventInput(
            event_id="seed_ev_003",
            command_id="cmd_seed_003",
            type=LedgerEventType.CREDIT_GIVEN,
            customer_id="cust_suresh",
            amount_paise=17500,
            business_date=today.isoformat(),
            effective_date=today.isoformat(),
            ts=ts_today,
            note="milk and bread",
            session_scope="seed",
        ),
        # Meena Devi: credit 42,000 paise (Rs 420), payment 42,000 paise (Rs 420), new credit 15,000 paise
        LedgerEventInput(
            event_id="seed_ev_004",
            command_id="cmd_seed_004",
            type=LedgerEventType.CREDIT_GIVEN,
            customer_id="cust_meena",
            amount_paise=42000,
            business_date=yesterday.isoformat(),
            effective_date=yesterday.isoformat(),
            ts=ts_yesterday,
            note="atta 10kg",
            session_scope="seed",
        ),
        LedgerEventInput(
            event_id="seed_ev_005",
            command_id="cmd_seed_005",
            type=LedgerEventType.PAYMENT_RECEIVED,
            customer_id="cust_meena",
            amount_paise=42000,
            business_date=today.isoformat(),
            effective_date=today.isoformat(),
            ts=ts_today,
            note="settled yesterday",
            session_scope="seed",
        ),
        LedgerEventInput(
            event_id="seed_ev_006",
            command_id="cmd_seed_006",
            type=LedgerEventType.CREDIT_GIVEN,
            customer_id="cust_meena",
            amount_paise=15000,
            business_date=today.isoformat(),
            effective_date=today.isoformat(),
            ts=ts_today,
            note="oil packet",
            session_scope="seed",
        ),
        # Ravi Verma: credit 15,000 paise (Rs 150)
        LedgerEventInput(
            event_id="seed_ev_007",
            command_id="cmd_seed_007",
            type=LedgerEventType.CREDIT_GIVEN,
            customer_id="cust_ravi_hi",
            amount_paise=15000,
            business_date=today.isoformat(),
            effective_date=today.isoformat(),
            ts=ts_today,
            note="चाय पत्ती",
            session_scope="seed",
        ),
        # Lakshmi Rao: credit 60,000 paise (Rs 600)
        LedgerEventInput(
            event_id="seed_ev_008",
            command_id="cmd_seed_008",
            type=LedgerEventType.CREDIT_GIVEN,
            customer_id="cust_lakshmi",
            amount_paise=60000,
            business_date=two_days_ago.isoformat(),
            effective_date=two_days_ago.isoformat(),
            ts=ts_two_days_ago,
            note="groceries",
            session_scope="seed",
        ),
        # Anil Nair: mistaken credit 99,900 paise, reversed, corrected to 12,500 paise (Rs 125)
        LedgerEventInput(
            event_id="seed_ev_009",
            command_id="cmd_seed_009",
            type=LedgerEventType.CREDIT_GIVEN,
            customer_id="cust_anil",
            amount_paise=99900,
            business_date=today.isoformat(),
            effective_date=today.isoformat(),
            ts=ts_today,
            note="mistyped entry",
            session_scope="seed",
        ),
    ]

    for ev in events:
        await ledger.post_event(ev)

    # Reverse Anil's mistaken entry and append corrected entry
    await ledger.reverse("seed_ev_009", reason="mistyped entry", command_id="cmd_seed_rev_009")
    await ledger.post_event(
        LedgerEventInput(
            event_id="seed_ev_010",
            command_id="cmd_seed_010",
            type=LedgerEventType.CREDIT_GIVEN,
            customer_id="cust_anil",
            amount_paise=12500,
            business_date=today.isoformat(),
            effective_date=today.isoformat(),
            ts=ts_today,
            note="corrected sugar entry",
            session_scope="seed",
        )
    )

    # If demo_full, add Ramesh Auto (second Ramesh) with alias, Kannada name, Telugu name
    if profile == "demo_full":
        for cid, name in DEMO_FULL_ADDITIONAL_CUSTOMERS:
            customers.append(await ledger.add_customer(name, customer_id=cid))

        # Add alias for Ramesh Auto
        await ledger.add_customer_alias("cust_ramesh_auto", "Ramesh Driver")

        full_events = [
            # Ramesh Auto: credit 50,000 paise (Rs 500)
            LedgerEventInput(
                event_id="seed_ev_011",
                command_id="cmd_seed_011",
                type=LedgerEventType.CREDIT_GIVEN,
                customer_id="cust_ramesh_auto",
                amount_paise=50000,
                business_date=yesterday.isoformat(),
                effective_date=yesterday.isoformat(),
                ts=ts_yesterday,
                note="cooking oil and snacks",
                session_scope="seed",
            ),
            # Manjunath (Kannada-script): credit 30,000 paise (Rs 300)
            LedgerEventInput(
                event_id="seed_ev_012",
                command_id="cmd_seed_012",
                type=LedgerEventType.CREDIT_GIVEN,
                customer_id="cust_manjunath_kn",
                amount_paise=30000,
                business_date=today.isoformat(),
                effective_date=today.isoformat(),
                ts=ts_today,
                note="ಅಕ್ಕಿ ಮತ್ತು ಬೆಲ್ಲ",
                session_scope="seed",
            ),
            # Venkatesh (Telugu-script): credit 25,000 paise (Rs 250)
            LedgerEventInput(
                event_id="seed_ev_013",
                command_id="cmd_seed_013",
                type=LedgerEventType.CREDIT_GIVEN,
                customer_id="cust_venkatesh_te",
                amount_paise=25000,
                business_date=today.isoformat(),
                effective_date=today.isoformat(),
                ts=ts_today,
                note="పప్పు మరియు బియ్యం",
                session_scope="seed",
            ),
        ]
        for ev in full_events:
            await ledger.post_event(ev)

    return customers
