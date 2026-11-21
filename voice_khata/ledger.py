"""Append-only SQLite ledger with computed balances and shielded single transactions."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from voice_khata.logging_utils import log_event
from voice_khata.models import (
    CustomerBalance,
    EventType,
    LanguageCode,
    LedgerEvent,
    ShopSummary,
    clean_display_name,
    normalize_customer_name,
    validate_strict_positive_rupees,
)

logger = logging.getLogger("voice_khata.ledger")

_SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    normalized_name TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    created_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ledger_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_uid TEXT NOT NULL UNIQUE,
    created_at_utc TEXT NOT NULL,
    created_at_mono_ns INTEGER NOT NULL,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    event_type TEXT NOT NULL CHECK(event_type IN ('CREDIT_GIVEN', 'PAYMENT_RECEIVED', 'REVERSAL')),
    signed_amount_rupees INTEGER NOT NULL CHECK(
        typeof(signed_amount_rupees) = 'integer' AND signed_amount_rupees != 0
    ),
    reverses_event_id INTEGER NULL UNIQUE REFERENCES ledger_events(id),
    item_note TEXT NOT NULL DEFAULT '',
    transcript TEXT NOT NULL DEFAULT '',
    language_code TEXT NOT NULL DEFAULT 'hi-IN'
);

CREATE INDEX IF NOT EXISTS idx_ledger_events_customer_id
    ON ledger_events(customer_id, id DESC);

CREATE TRIGGER IF NOT EXISTS trg_ledger_events_no_update
BEFORE UPDATE ON ledger_events
BEGIN
    SELECT RAISE(ABORT, 'ledger_events is append-only: UPDATE is forbidden');
END;

CREATE TRIGGER IF NOT EXISTS trg_ledger_events_no_delete
BEFORE DELETE ON ledger_events
BEGIN
    SELECT RAISE(ABORT, 'ledger_events is append-only: DELETE is forbidden');
END;
"""


class LedgerError(Exception):
    """Domain error raised on invalid ledger operations."""


class LedgerStore:
    """Append-only SQLite ledger where all balances are computed from events.

    Write operations run inside a single SQLite BEGIN IMMEDIATE transaction
    and are wrapped with asyncio.shield() so cooperative turn cancellation
    never leaves a partial write.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        if str(self._db_path) != ":memory:":
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = asyncio.Lock()
        self._init_db_sync()

    @property
    def db_path(self) -> Path:
        return self._db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self._db_path),
            timeout=15.0,
            isolation_level=None,  # Explicit BEGIN IMMEDIATE / COMMIT / ROLLBACK
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        if str(self._db_path) != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
        return conn

    def _init_db_sync(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(_SCHEMA_SQL)
        finally:
            conn.close()

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> LedgerEvent:
        return LedgerEvent(
            id=int(row["id"]),
            event_uid=str(row["event_uid"]),
            created_at_utc=str(row["created_at_utc"]),
            created_at_mono_ns=int(row["created_at_mono_ns"]),
            customer_id=int(row["customer_id"]),
            customer_name=str(row["customer_name"]),
            event_type=EventType(str(row["event_type"])),
            signed_amount_rupees=int(row["signed_amount_rupees"]),
            reverses_event_id=(
                int(row["reverses_event_id"]) if row["reverses_event_id"] is not None else None
            ),
            item_note=str(row["item_note"]),
            transcript=str(row["transcript"]),
            language_code=LanguageCode.normalize(str(row["language_code"])),
        )

    def _compute_customer_balance_in_tx(
        self, conn: sqlite3.Connection, customer_id: int
    ) -> CustomerBalance:
        row = conn.execute(
            """
            SELECT
                c.id AS customer_id,
                c.display_name AS customer_name,
                COALESCE(SUM(e.signed_amount_rupees), 0) AS balance_rupees,
                COUNT(e.id) AS event_count
            FROM customers c
            LEFT JOIN ledger_events e ON e.customer_id = c.id
            WHERE c.id = ?
            GROUP BY c.id, c.display_name
            """,
            (customer_id,),
        ).fetchone()
        if row is None:
            raise LedgerError(f"Customer id {customer_id} not found")
        return CustomerBalance(
            customer_id=int(row["customer_id"]),
            customer_name=str(row["customer_name"]),
            balance_rupees=int(row["balance_rupees"]),
            event_count=int(row["event_count"]),
        )

    def _append_entry_sync(
        self,
        *,
        customer_name: str,
        event_type: EventType,
        amount_rupees: int,
        item_note: str,
        transcript: str,
        language_code: LanguageCode,
        event_uid: str,
    ) -> tuple[LedgerEvent, CustomerBalance]:
        if event_type not in (EventType.CREDIT_GIVEN, EventType.PAYMENT_RECEIVED):
            raise LedgerError(f"Use reverse_event for {event_type}")

        amount = validate_strict_positive_rupees(amount_rupees)
        display_name = clean_display_name(customer_name)
        norm_name = normalize_customer_name(display_name)
        signed_amount = amount if event_type == EventType.CREDIT_GIVEN else -amount

        now_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")
        now_mono_ns = time.monotonic_ns()

        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")

            existing = conn.execute(
                """
                SELECT e.*, c.display_name AS customer_name
                FROM ledger_events e
                JOIN customers c ON c.id = e.customer_id
                WHERE e.event_uid = ?
                """,
                (event_uid,),
            ).fetchone()
            if existing is not None:
                event = self._row_to_event(existing)
                balance = self._compute_customer_balance_in_tx(conn, event.customer_id)
                conn.execute("COMMIT")
                return event, balance

            cust_row = conn.execute(
                "SELECT id, display_name FROM customers WHERE normalized_name = ?",
                (norm_name,),
            ).fetchone()
            if cust_row is None:
                cur = conn.execute(
                    """
                    INSERT INTO customers (normalized_name, display_name, created_at_utc)
                    VALUES (?, ?, ?)
                    """,
                    (norm_name, display_name, now_utc),
                )
                customer_id = int(cur.lastrowid)
                resolved_display = display_name
            else:
                customer_id = int(cust_row["id"])
                resolved_display = str(cust_row["display_name"])

            cur = conn.execute(
                """
                INSERT INTO ledger_events (
                    event_uid, created_at_utc, created_at_mono_ns,
                    customer_id, event_type, signed_amount_rupees,
                    reverses_event_id, item_note, transcript, language_code
                )
                VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
                """,
                (
                    event_uid,
                    now_utc,
                    now_mono_ns,
                    customer_id,
                    event_type.value,
                    signed_amount,
                    item_note.strip()[:120],
                    transcript.strip()[:300],
                    language_code.value,
                ),
            )
            event_id = int(cur.lastrowid)
            balance = self._compute_customer_balance_in_tx(conn, customer_id)
            conn.execute("COMMIT")

            event = LedgerEvent(
                id=event_id,
                event_uid=event_uid,
                created_at_utc=now_utc,
                created_at_mono_ns=now_mono_ns,
                customer_id=customer_id,
                customer_name=resolved_display,
                event_type=event_type,
                signed_amount_rupees=signed_amount,
                reverses_event_id=None,
                item_note=item_note.strip()[:120],
                transcript=transcript.strip()[:300],
                language_code=language_code,
            )
            log_event(
                logger,
                "ledger.event_committed",
                mono_ns=now_mono_ns,
                event_id=event_id,
                event_type=event_type.value,
                customer_id=customer_id,
                signed_amount_rupees=signed_amount,
                balance_rupees=balance.balance_rupees,
            )
            return event, balance
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def _reverse_event_sync(
        self,
        *,
        target_event_id: int,
        transcript: str,
        language_code: LanguageCode,
        event_uid: str,
    ) -> tuple[LedgerEvent, LedgerEvent, CustomerBalance]:
        now_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")
        now_mono_ns = time.monotonic_ns()

        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")

            existing = conn.execute(
                """
                SELECT e.*, c.display_name AS customer_name
                FROM ledger_events e
                JOIN customers c ON c.id = e.customer_id
                WHERE e.event_uid = ?
                """,
                (event_uid,),
            ).fetchone()
            if existing is not None:
                rev_event = self._row_to_event(existing)
                orig_row = conn.execute(
                    """
                    SELECT e.*, c.display_name AS customer_name
                    FROM ledger_events e
                    JOIN customers c ON c.id = e.customer_id
                    WHERE e.id = ?
                    """,
                    (rev_event.reverses_event_id,),
                ).fetchone()
                orig_event = self._row_to_event(orig_row)
                balance = self._compute_customer_balance_in_tx(conn, rev_event.customer_id)
                conn.execute("COMMIT")
                return rev_event, orig_event, balance

            orig_row = conn.execute(
                """
                SELECT e.*, c.display_name AS customer_name
                FROM ledger_events e
                JOIN customers c ON c.id = e.customer_id
                WHERE e.id = ?
                """,
                (target_event_id,),
            ).fetchone()
            if orig_row is None:
                raise LedgerError(f"Event {target_event_id} does not exist")

            orig_event = self._row_to_event(orig_row)
            if orig_event.event_type == EventType.REVERSAL:
                raise LedgerError("Cannot reverse a reversal event")

            already_reversed = conn.execute(
                "SELECT id FROM ledger_events WHERE reverses_event_id = ?",
                (target_event_id,),
            ).fetchone()
            if already_reversed is not None:
                raise LedgerError(f"Event {target_event_id} has already been reversed")

            compensating_amount = -orig_event.signed_amount_rupees
            cur = conn.execute(
                """
                INSERT INTO ledger_events (
                    event_uid, created_at_utc, created_at_mono_ns,
                    customer_id, event_type, signed_amount_rupees,
                    reverses_event_id, item_note, transcript, language_code
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_uid,
                    now_utc,
                    now_mono_ns,
                    orig_event.customer_id,
                    EventType.REVERSAL.value,
                    compensating_amount,
                    orig_event.id,
                    f"Reversal of #{orig_event.id}",
                    transcript.strip()[:300],
                    language_code.value,
                ),
            )
            rev_id = int(cur.lastrowid)
            balance = self._compute_customer_balance_in_tx(conn, orig_event.customer_id)
            conn.execute("COMMIT")

            rev_event = LedgerEvent(
                id=rev_id,
                event_uid=event_uid,
                created_at_utc=now_utc,
                created_at_mono_ns=now_mono_ns,
                customer_id=orig_event.customer_id,
                customer_name=orig_event.customer_name,
                event_type=EventType.REVERSAL,
                signed_amount_rupees=compensating_amount,
                reverses_event_id=orig_event.id,
                item_note=f"Reversal of #{orig_event.id}",
                transcript=transcript.strip()[:300],
                language_code=language_code,
            )
            log_event(
                logger,
                "ledger.reversal_committed",
                mono_ns=now_mono_ns,
                event_id=rev_id,
                reverses_event_id=orig_event.id,
                customer_id=orig_event.customer_id,
                signed_amount_rupees=compensating_amount,
                balance_rupees=balance.balance_rupees,
            )
            return rev_event, orig_event, balance
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    async def _run_shielded_write(self, fn, *args, **kwargs):
        """Serialize writes and shield the SQLite transaction from cancellation."""

        async def _commit_task():
            async with self._write_lock:
                return await asyncio.to_thread(fn, *args, **kwargs)

        return await asyncio.shield(_commit_task())

    async def record_credit(
        self,
        *,
        customer_name: str,
        amount_rupees: int,
        item_note: str = "",
        transcript: str = "",
        language_code: LanguageCode = LanguageCode.HI_IN,
        event_uid: str | None = None,
    ) -> tuple[LedgerEvent, CustomerBalance]:
        uid = event_uid or uuid.uuid4().hex
        return await self._run_shielded_write(
            self._append_entry_sync,
            customer_name=customer_name,
            event_type=EventType.CREDIT_GIVEN,
            amount_rupees=amount_rupees,
            item_note=item_note,
            transcript=transcript,
            language_code=language_code,
            event_uid=uid,
        )

    async def record_payment(
        self,
        *,
        customer_name: str,
        amount_rupees: int,
        item_note: str = "",
        transcript: str = "",
        language_code: LanguageCode = LanguageCode.HI_IN,
        event_uid: str | None = None,
    ) -> tuple[LedgerEvent, CustomerBalance]:
        uid = event_uid or uuid.uuid4().hex
        return await self._run_shielded_write(
            self._append_entry_sync,
            customer_name=customer_name,
            event_type=EventType.PAYMENT_RECEIVED,
            amount_rupees=amount_rupees,
            item_note=item_note,
            transcript=transcript,
            language_code=language_code,
            event_uid=uid,
        )

    async def reverse_event(
        self,
        *,
        target_event_id: int,
        transcript: str = "",
        language_code: LanguageCode = LanguageCode.HI_IN,
        event_uid: str | None = None,
    ) -> tuple[LedgerEvent, LedgerEvent, CustomerBalance]:
        uid = event_uid or uuid.uuid4().hex
        return await self._run_shielded_write(
            self._reverse_event_sync,
            target_event_id=target_event_id,
            transcript=transcript,
            language_code=language_code,
            event_uid=uid,
        )

    def _get_customer_balance_sync(self, customer_name: str) -> CustomerBalance | None:
        norm_name = normalize_customer_name(customer_name)
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT
                    c.id AS customer_id,
                    c.display_name AS customer_name,
                    COALESCE(SUM(e.signed_amount_rupees), 0) AS balance_rupees,
                    COUNT(e.id) AS event_count
                FROM customers c
                LEFT JOIN ledger_events e ON e.customer_id = c.id
                WHERE c.normalized_name = ?
                GROUP BY c.id, c.display_name
                """,
                (norm_name,),
            ).fetchone()
            if row is None:
                return None
            return CustomerBalance(
                customer_id=int(row["customer_id"]),
                customer_name=str(row["customer_name"]),
                balance_rupees=int(row["balance_rupees"]),
                event_count=int(row["event_count"]),
            )
        finally:
            conn.close()

    async def get_customer_balance(self, customer_name: str) -> CustomerBalance | None:
        return await asyncio.to_thread(self._get_customer_balance_sync, customer_name)

    def _list_customer_balances_sync(self) -> list[CustomerBalance]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT
                    c.id AS customer_id,
                    c.display_name AS customer_name,
                    COALESCE(SUM(e.signed_amount_rupees), 0) AS balance_rupees,
                    COUNT(e.id) AS event_count
                FROM customers c
                LEFT JOIN ledger_events e ON e.customer_id = c.id
                GROUP BY c.id, c.display_name
                ORDER BY balance_rupees DESC, c.display_name ASC
                """
            ).fetchall()
            return [
                CustomerBalance(
                    customer_id=int(r["customer_id"]),
                    customer_name=str(r["customer_name"]),
                    balance_rupees=int(r["balance_rupees"]),
                    event_count=int(r["event_count"]),
                )
                for r in rows
            ]
        finally:
            conn.close()

    async def list_customer_balances(self) -> list[CustomerBalance]:
        return await asyncio.to_thread(self._list_customer_balances_sync)

    def _get_shop_summary_sync(self) -> ShopSummary:
        conn = self._connect()
        try:
            balances = self._list_customer_balances_sync()
            total_outstanding = sum(b.balance_rupees for b in balances)
            active_credit_customers = sum(1 for b in balances if b.balance_rupees > 0)
            total_events_row = conn.execute("SELECT COUNT(*) AS cnt FROM ledger_events").fetchone()
            total_events = int(total_events_row["cnt"]) if total_events_row else 0
            return ShopSummary(
                total_outstanding_rupees=total_outstanding,
                customer_count=len(balances),
                active_credit_customers=active_credit_customers,
                total_events=total_events,
            )
        finally:
            conn.close()

    async def get_shop_summary(self) -> ShopSummary:
        return await asyncio.to_thread(self._get_shop_summary_sync)

    def _list_recent_events_sync(
        self, limit: int = 20, customer_name: str | None = None
    ) -> list[LedgerEvent]:
        conn = self._connect()
        try:
            if customer_name:
                norm_name = normalize_customer_name(customer_name)
                rows = conn.execute(
                    """
                    SELECT e.*, c.display_name AS customer_name
                    FROM ledger_events e
                    JOIN customers c ON c.id = e.customer_id
                    WHERE c.normalized_name = ?
                    ORDER BY e.id DESC
                    LIMIT ?
                    """,
                    (norm_name, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT e.*, c.display_name AS customer_name
                    FROM ledger_events e
                    JOIN customers c ON c.id = e.customer_id
                    ORDER BY e.id DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            return [self._row_to_event(r) for r in rows]
        finally:
            conn.close()

    async def list_recent_events(
        self, limit: int = 20, customer_name: str | None = None
    ) -> list[LedgerEvent]:
        return await asyncio.to_thread(self._list_recent_events_sync, limit, customer_name)

    def _get_last_reversible_event_sync(
        self, customer_name: str | None = None
    ) -> LedgerEvent | None:
        conn = self._connect()
        try:
            if customer_name:
                norm_name = normalize_customer_name(customer_name)
                row = conn.execute(
                    """
                    SELECT e.*, c.display_name AS customer_name
                    FROM ledger_events e
                    JOIN customers c ON c.id = e.customer_id
                    WHERE c.normalized_name = ?
                      AND e.event_type IN ('CREDIT_GIVEN', 'PAYMENT_RECEIVED')
                      AND NOT EXISTS (
                          SELECT 1 FROM ledger_events r WHERE r.reverses_event_id = e.id
                      )
                    ORDER BY e.id DESC
                    LIMIT 1
                    """,
                    (norm_name,),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT e.*, c.display_name AS customer_name
                    FROM ledger_events e
                    JOIN customers c ON c.id = e.customer_id
                    WHERE e.event_type IN ('CREDIT_GIVEN', 'PAYMENT_RECEIVED')
                      AND NOT EXISTS (
                          SELECT 1 FROM ledger_events r WHERE r.reverses_event_id = e.id
                      )
                    ORDER BY e.id DESC
                    LIMIT 1
                    """
                ).fetchone()
            return self._row_to_event(row) if row is not None else None
        finally:
            conn.close()

    async def get_last_reversible_event(
        self, customer_name: str | None = None
    ) -> LedgerEvent | None:
        return await asyncio.to_thread(self._get_last_reversible_event_sync, customer_name)
