"""Append-only SQLite ledger with WAL mode, AES-GCM field encryption,
PRAGMA user_version migrations (v1 -> v2 -> v3), Asia/Kolkata day grouping,
and strict integer paise arithmetic (KhataSaathi event model).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import sqlite3
import time
import uuid
from collections.abc import Callable
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from server.ledger.crypto import (
    FieldCipher,
    detect_script,
    key_from_env,
    phonetic_key,
)
from server.ledger.models import (
    BillRecord,
    CommandAuditRecord,
    CreditRecord,
    CustomerAliasRecord,
    CustomerBalanceRecord,
    CustomerRecord,
    CustomerTimelineResult,
    EntryItemInput,
    EntryItemRecord,
    LedgerEventInput,
    LedgerEventRecord,
    LedgerEventType,
    LedgerSummaryStats,
    PaymentRecord,
    ReceivedDayGroup,
    ReceivedPaymentItem,
    ReceivedResult,
    SummaryResult,
    TimelineEventItem,
    _normalize_date_str,
    _normalize_ts_epoch_ms,
    _strict_int_paise,
)

logger = logging.getLogger("voice_khata.server.ledger")

try:
    from zoneinfo import ZoneInfo
    KOLKATA_TZ = ZoneInfo("Asia/Kolkata")
except Exception:
    from datetime import timedelta
    KOLKATA_TZ = timezone(timedelta(hours=5, minutes=30), name="Asia/Kolkata")


def epoch_ms_to_ist_datetime(ts_ms: int) -> datetime:
    """Convert UTC epoch milliseconds to an aware Asia/Kolkata datetime."""
    sec = ts_ms // 1000
    micro = (ts_ms % 1000) * 1000
    return datetime.fromtimestamp(sec, tz=timezone.utc).replace(microsecond=micro).astimezone(KOLKATA_TZ)


def epoch_ms_to_ist_date_str(ts_ms: int) -> str:
    """Convert UTC epoch milliseconds to Asia/Kolkata date string 'YYYY-MM-DD'."""
    return epoch_ms_to_ist_datetime(ts_ms).date().isoformat()


_CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS customers (
    id TEXT PRIMARY KEY,
    name_enc BLOB NOT NULL,
    name_norm TEXT NOT NULL,
    script TEXT NOT NULL,
    created_ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS customer_aliases (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers(id),
    alias_norm TEXT NOT NULL,
    alias_enc BLOB NOT NULL,
    UNIQUE(customer_id, alias_norm)
);

CREATE TABLE IF NOT EXISTS command_audit (
    command_id TEXT PRIMARY KEY,
    session_id TEXT NULL,
    parsed_intent_json TEXT NULL,
    confidence_band TEXT NULL,
    validation_result TEXT NULL,
    committed_event_ids TEXT NULL,
    transcript_enc BLOB NULL,
    created_ts INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    command_id TEXT NULL UNIQUE,
    type TEXT NOT NULL CHECK(type IN ('CREDIT_GIVEN', 'PAYMENT_RECEIVED', 'REVERSAL', 'CONSENT', 'SHARE_LOGGED')),
    customer_id TEXT NOT NULL REFERENCES customers(id),
    amount_paise INTEGER NOT NULL CHECK(typeof(amount_paise) = 'integer' AND amount_paise >= 0),
    business_date TEXT NOT NULL,
    effective_date TEXT NOT NULL,
    date_phrase TEXT NULL,
    ts INTEGER NOT NULL,
    source_session_id TEXT NULL,
    reverses_event_id TEXT NULL UNIQUE REFERENCES events(event_id),
    note_enc BLOB NULL,
    session_scope TEXT NULL
);

CREATE TABLE IF NOT EXISTS entry_items (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES events(event_id),
    name_enc BLOB NOT NULL,
    qty_text TEXT NULL,
    amount_paise INTEGER NOT NULL CHECK(typeof(amount_paise) = 'integer' AND amount_paise > 0)
);

CREATE TABLE IF NOT EXISTS bills (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES events(event_id),
    mime TEXT NOT NULL,
    size INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    path TEXT NOT NULL,
    created_ts INTEGER NOT NULL
);
"""

_CREATE_INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_customers_name_norm
    ON customers(name_norm);

CREATE INDEX IF NOT EXISTS idx_customer_aliases_norm
    ON customer_aliases(alias_norm);

CREATE INDEX IF NOT EXISTS idx_customer_aliases_cid
    ON customer_aliases(customer_id);

CREATE INDEX IF NOT EXISTS idx_command_audit_cmd
    ON command_audit(command_id);

CREATE INDEX IF NOT EXISTS idx_events_type_ts
    ON events(type, ts);

CREATE INDEX IF NOT EXISTS idx_events_customer_ts
    ON events(customer_id, ts);

CREATE INDEX IF NOT EXISTS idx_events_reverses_event_id
    ON events(reverses_event_id);

CREATE INDEX IF NOT EXISTS idx_events_customer_type
    ON events(customer_id, type);

CREATE INDEX IF NOT EXISTS idx_events_business_date
    ON events(business_date, type);

CREATE INDEX IF NOT EXISTS idx_events_effective_date
    ON events(effective_date, type);

CREATE INDEX IF NOT EXISTS idx_events_command_id
    ON events(command_id);

CREATE INDEX IF NOT EXISTS idx_events_session_seq
    ON events(session_scope, seq DESC);

CREATE INDEX IF NOT EXISTS idx_entry_items_event_id
    ON entry_items(event_id);

CREATE INDEX IF NOT EXISTS idx_bills_event_id
    ON bills(event_id);
"""

_CREATE_TRIGGERS_SQL = """
CREATE TRIGGER IF NOT EXISTS trg_events_no_update
BEFORE UPDATE ON events
BEGIN
    SELECT RAISE(ABORT, 'events table is append-only: UPDATE is forbidden');
END;

CREATE TRIGGER IF NOT EXISTS trg_events_no_delete
BEFORE DELETE ON events
BEGIN
    SELECT RAISE(ABORT, 'events table is append-only: DELETE is forbidden');
END;
"""

SCHEMA_VERSION = 3


def _migrate_v0_to_v1(conn: sqlite3.Connection) -> None:
    """Migrate schema from legacy (user_version=0) to v1 without dropping data."""
    table_exists = (
        conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'").fetchone()
        is not None
    )

    if not table_exists:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS customers (
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
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                type TEXT NOT NULL CHECK(type IN ('CREDIT', 'PAYMENT', 'VOID')),
                customer_id TEXT NOT NULL REFERENCES customers(id),
                amount_inr INTEGER NOT NULL CHECK(typeof(amount_inr) = 'integer'),
                effective_date TEXT NOT NULL,
                ts INTEGER NOT NULL,
                voids_event_id TEXT NULL UNIQUE REFERENCES events(event_id),
                note_enc BLOB NULL,
                session_scope TEXT NULL
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_customers_name_norm ON customers(name_norm);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_type_ts ON events(type, ts);")
        return

    # Existing schema: drop old triggers to allow table upgrade
    conn.execute("DROP TRIGGER IF EXISTS trg_events_no_update")
    conn.execute("DROP TRIGGER IF EXISTS trg_events_no_delete")

    # Recreate events table with ts INTEGER while preserving all existing rows
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS events_v1 (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL UNIQUE,
            type TEXT NOT NULL CHECK(type IN ('CREDIT', 'PAYMENT', 'VOID')),
            customer_id TEXT NOT NULL REFERENCES customers(id),
            amount_inr INTEGER NOT NULL CHECK(typeof(amount_inr) = 'integer'),
            effective_date TEXT NOT NULL,
            ts INTEGER NOT NULL,
            voids_event_id TEXT NULL UNIQUE REFERENCES events_v1(event_id),
            note_enc BLOB NULL,
            session_scope TEXT NULL
        );
        """
    )

    old_rows = conn.execute(
        """
        SELECT seq, event_id, type, customer_id, amount_inr, effective_date, ts, voids_event_id, note_enc, session_scope
        FROM events ORDER BY seq ASC
        """
    ).fetchall()

    for r in old_rows:
        ts_val = _normalize_ts_epoch_ms(r["ts"])
        conn.execute(
            """
            INSERT INTO events_v1 (
                seq, event_id, type, customer_id, amount_inr, effective_date, ts, voids_event_id, note_enc, session_scope
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(r["seq"]),
                str(r["event_id"]),
                str(r["type"]),
                str(r["customer_id"]),
                int(r["amount_inr"]),
                str(r["effective_date"]),
                ts_val,
                str(r["voids_event_id"]) if r["voids_event_id"] is not None else None,
                r["note_enc"],
                str(r["session_scope"]) if r["session_scope"] is not None else None,
            ),
        )

    conn.execute("DROP TABLE events")
    conn.execute("ALTER TABLE events_v1 RENAME TO events")


def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    """Migrate schema from v1 to v2: entry_items, bills, and CONSENT/SHARE_LOGGED event types."""
    conn.execute("DROP TRIGGER IF EXISTS trg_events_no_update")
    conn.execute("DROP TRIGGER IF EXISTS trg_events_no_delete")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS events_v2 (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL UNIQUE,
            type TEXT NOT NULL CHECK(type IN ('CREDIT', 'PAYMENT', 'VOID', 'CONSENT', 'SHARE_LOGGED')),
            customer_id TEXT NOT NULL REFERENCES customers(id),
            amount_inr INTEGER NOT NULL CHECK(typeof(amount_inr) = 'integer' AND amount_inr >= 0),
            effective_date TEXT NOT NULL,
            ts INTEGER NOT NULL,
            voids_event_id TEXT NULL UNIQUE REFERENCES events_v2(event_id),
            note_enc BLOB NULL,
            session_scope TEXT NULL
        );
        """
    )
    conn.execute(
        """
        INSERT INTO events_v2 (
            seq, event_id, type, customer_id, amount_inr, effective_date, ts, voids_event_id, note_enc, session_scope
        )
        SELECT seq, event_id, type, customer_id, amount_inr, effective_date, ts, voids_event_id, note_enc, session_scope
        FROM events ORDER BY seq ASC;
        """
    )
    conn.execute("DROP TABLE events")
    conn.execute("ALTER TABLE events_v2 RENAME TO events")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS entry_items (
            id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL REFERENCES events(event_id),
            name_enc BLOB NOT NULL,
            qty_text TEXT NULL,
            amount_inr INTEGER NOT NULL CHECK(typeof(amount_inr) = 'integer' AND amount_inr > 0)
        );
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bills (
            id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL REFERENCES events(event_id),
            mime TEXT NOT NULL,
            size INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            path TEXT NOT NULL,
            created_ts INTEGER NOT NULL
        );
        """
    )


def _migrate_v2_to_v3(conn: sqlite3.Connection) -> None:
    """Migrate schema from v2 to v3 (KhataSaathi integer paise model):
    - amount_inr -> amount_paise INTEGER (value x 100)
    - Types: CREDIT->CREDIT_GIVEN, PAYMENT->PAYMENT_RECEIVED, VOID->REVERSAL
    - voids_event_id -> reverses_event_id
    - Add columns: business_date (shop-local Asia/Kolkata), date_phrase, source_session_id, command_id
    - New tables: customer_aliases, command_audit
    - Migrated REVERSAL rows copy the target event's customer and amount
    - entry_items: amount_inr -> amount_paise (value x 100)
    """
    conn.execute("DROP TRIGGER IF EXISTS trg_events_no_update")
    conn.execute("DROP TRIGGER IF EXISTS trg_events_no_delete")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS events_v3 (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL UNIQUE,
            command_id TEXT NULL UNIQUE,
            type TEXT NOT NULL CHECK(type IN ('CREDIT_GIVEN', 'PAYMENT_RECEIVED', 'REVERSAL', 'CONSENT', 'SHARE_LOGGED')),
            customer_id TEXT NOT NULL REFERENCES customers(id),
            amount_paise INTEGER NOT NULL CHECK(typeof(amount_paise) = 'integer' AND amount_paise >= 0),
            business_date TEXT NOT NULL,
            effective_date TEXT NOT NULL,
            date_phrase TEXT NULL,
            ts INTEGER NOT NULL,
            source_session_id TEXT NULL,
            reverses_event_id TEXT NULL UNIQUE REFERENCES events_v3(event_id),
            note_enc BLOB NULL,
            session_scope TEXT NULL
        );
        """
    )

    type_mapping = {
        "CREDIT": "CREDIT_GIVEN",
        "PAYMENT": "PAYMENT_RECEIVED",
        "VOID": "REVERSAL",
        "CREDIT_GIVEN": "CREDIT_GIVEN",
        "PAYMENT_RECEIVED": "PAYMENT_RECEIVED",
        "REVERSAL": "REVERSAL",
        "CONSENT": "CONSENT",
        "SHARE_LOGGED": "SHARE_LOGGED",
    }

    old_rows = conn.execute("SELECT * FROM events ORDER BY seq ASC").fetchall()
    for row in old_rows:
        keys = row.keys()
        seq_val = int(row["seq"])
        eid = str(row["event_id"])
        raw_type = str(row["type"])
        new_type = type_mapping.get(raw_type, raw_type)
        cid = str(row["customer_id"])

        if "amount_paise" in keys:
            amt_paise = int(row["amount_paise"])
        else:
            amt_paise = int(row["amount_inr"]) * 100

        ts_val = _normalize_ts_epoch_ms(row["ts"])
        b_date = epoch_ms_to_ist_date_str(ts_val)
        eff_date = str(row["effective_date"])

        rev_id = None
        if "voids_event_id" in keys and row["voids_event_id"] is not None:
            rev_id = str(row["voids_event_id"])
        elif "reverses_event_id" in keys and row["reverses_event_id"] is not None:
            rev_id = str(row["reverses_event_id"])

        # Migrated REVERSAL rows copy the amount and customer of the target
        if new_type == "REVERSAL" and rev_id:
            target_row = conn.execute(
                "SELECT customer_id, amount_inr FROM events WHERE event_id = ?",
                (rev_id,),
            ).fetchone()
            if target_row is not None:
                cid = str(target_row["customer_id"])
                if "amount_paise" in target_row.keys() and target_row["amount_paise"] is not None:
                    amt_paise = int(target_row["amount_paise"])
                else:
                    amt_paise = int(target_row["amount_inr"]) * 100

        cmd_id = (
            str(row["command_id"])
            if "command_id" in keys and row["command_id"] is not None
            else f"cmd_migrated_{eid}"
        )
        d_phrase = str(row["date_phrase"]) if "date_phrase" in keys and row["date_phrase"] is not None else None
        src_sess = (
            str(row["source_session_id"])
            if "source_session_id" in keys and row["source_session_id"] is not None
            else None
        )
        note_enc = row["note_enc"]
        sess_scope = str(row["session_scope"]) if row["session_scope"] is not None else None

        conn.execute(
            """
            INSERT INTO events_v3 (
                seq, event_id, command_id, type, customer_id, amount_paise,
                business_date, effective_date, date_phrase, ts,
                source_session_id, reverses_event_id, note_enc, session_scope
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                seq_val,
                eid,
                cmd_id,
                new_type,
                cid,
                amt_paise,
                b_date,
                eff_date,
                d_phrase,
                ts_val,
                src_sess,
                rev_id,
                note_enc,
                sess_scope,
            ),
        )

    conn.execute("DROP TABLE events")
    conn.execute("ALTER TABLE events_v3 RENAME TO events")

    # Migrate entry_items to amount_paise
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS entry_items_v3 (
            id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL REFERENCES events(event_id),
            name_enc BLOB NOT NULL,
            qty_text TEXT NULL,
            amount_paise INTEGER NOT NULL CHECK(typeof(amount_paise) = 'integer' AND amount_paise > 0)
        );
        """
    )
    ei_cols = [
        r[1]
        for r in conn.execute("PRAGMA table_info(entry_items)").fetchall()
    ]
    if "amount_inr" in ei_cols:
        conn.execute(
            """
            INSERT INTO entry_items_v3 (id, event_id, name_enc, qty_text, amount_paise)
            SELECT id, event_id, name_enc, qty_text, amount_inr * 100
            FROM entry_items
            """
        )
        conn.execute("DROP TABLE entry_items")
        conn.execute("ALTER TABLE entry_items_v3 RENAME TO entry_items")
    elif "amount_paise" in ei_cols:
        conn.execute("DROP TABLE entry_items_v3")
    else:
        conn.execute("ALTER TABLE entry_items_v3 RENAME TO entry_items")

    # Ensure bills table exists
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bills (
            id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL REFERENCES events(event_id),
            mime TEXT NOT NULL,
            size INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            path TEXT NOT NULL,
            created_ts INTEGER NOT NULL
        );
        """
    )

    # Create new tables: customer_aliases, command_audit
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS customer_aliases (
            id TEXT PRIMARY KEY,
            customer_id TEXT NOT NULL REFERENCES customers(id),
            alias_norm TEXT NOT NULL,
            alias_enc BLOB NOT NULL,
            UNIQUE(customer_id, alias_norm)
        );
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS command_audit (
            command_id TEXT PRIMARY KEY,
            session_id TEXT NULL,
            parsed_intent_json TEXT NULL,
            confidence_band TEXT NULL,
            validation_result TEXT NULL,
            committed_event_ids TEXT NULL,
            transcript_enc BLOB NULL,
            created_ts INTEGER NOT NULL
        );
        """
    )

    # Recreate all indexes and triggers
    for sql_chunk in (_CREATE_INDEXES_SQL, _CREATE_TRIGGERS_SQL):
        for stmt in sql_chunk.split(";\n\n"):
            cleaned = stmt.strip()
            if cleaned:
                conn.execute(cleaned)


MIGRATIONS: list[Callable[[sqlite3.Connection], None]] = [
    _migrate_v0_to_v1,
    _migrate_v1_to_v2,
    _migrate_v2_to_v3,
]


def _run_migrations(conn: sqlite3.Connection) -> None:
    cur_ver = int(conn.execute("PRAGMA user_version").fetchone()[0])
    for target_ver, mig_fn in enumerate(MIGRATIONS, start=1):
        if cur_ver < target_ver:
            mig_fn(conn)
            conn.execute(f"PRAGMA user_version = {target_ver}")


class LedgerError(Exception):
    """Base error for ledger operations."""


class LedgerNotFoundError(LedgerError):
    """Raised when a referenced customer or event does not exist."""


class LedgerValidationError(LedgerError):
    """Raised on invalid domain transitions (e.g., voiding a VOID/REVERSAL event)."""


class IdempotencyConflictError(LedgerError):
    """Raised when an existing event_id is retried with a different payload."""


class Ledger:
    """Append-only SQLite ledger with WAL mode, AES-GCM field encryption,
    ordered user_version migrations, and Asia/Kolkata day grouping.
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        enc_key: bytes | str | None = None,
        busy_timeout_ms: int = 5000,
    ) -> None:
        if db_path is None:
            data_dir = Path(os.environ.get("DATA_DIR", "./data"))
            self._db_path = data_dir / "ledger.db"
        else:
            p = Path(db_path)
            if p.is_dir() or str(p).endswith(("/", "\\")):
                self._db_path = p / "ledger.db"
            else:
                self._db_path = p
        if str(self._db_path) == ":memory:":
            raise ValueError("Use a file path (e.g., tmp_path / 'ledger.db') for WAL SQLite ledger")
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._busy_timeout_ms = int(busy_timeout_ms)
        self._cipher = FieldCipher(enc_key if enc_key is not None else key_from_env())
        self._write_lock = asyncio.Lock()
        self._bills_dir = self._db_path.parent / "bills"
        self._bills_dir.mkdir(parents=True, exist_ok=True)
        self._init_db_sync()

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def bills_dir(self) -> Path:
        return self._bills_dir

    @property
    def cipher(self) -> FieldCipher:
        return self._cipher

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self._db_path),
            timeout=self._busy_timeout_ms // 1000,
            isolation_level=None,  # Explicit BEGIN IMMEDIATE / COMMIT / ROLLBACK
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms};")
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def _init_db_sync(self) -> None:
        conn = self._connect()
        try:
            conn.execute("PRAGMA foreign_keys = OFF;")
            conn.execute("BEGIN IMMEDIATE")
            _run_migrations(conn)
            conn.execute("COMMIT")
            conn.execute("PRAGMA foreign_keys = ON;")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def _decrypt_customer_row(self, row: sqlite3.Row) -> CustomerRecord:
        cid = str(row["id"])
        name = self._cipher.decrypt_str(
            bytes(row["name_enc"]), aad=f"customer:{cid}:name".encode("ascii")
        )
        return CustomerRecord(
            id=cid,
            name=name,
            name_norm=str(row["name_norm"]),
            script=str(row["script"]),
            created_ts=str(row["created_ts"]),
        )

    def _decrypt_event_row(self, row: sqlite3.Row) -> LedgerEventRecord:
        eid = str(row["event_id"])
        note_blob = row["note_enc"]
        note = (
            self._cipher.decrypt_str(bytes(note_blob), aad=f"event:{eid}:note".encode("ascii"))
            if note_blob is not None
            else None
        )
        keys = row.keys()
        amt_paise = (
            int(row["amount_paise"])
            if "amount_paise" in keys
            else int(row["amount_inr"]) * 100
        )
        cmd_id = (
            str(row["command_id"])
            if "command_id" in keys and row["command_id"] is not None
            else None
        )
        ts_val = _normalize_ts_epoch_ms(row["ts"])
        b_date = (
            str(row["business_date"])
            if "business_date" in keys and row["business_date"] is not None
            else epoch_ms_to_ist_date_str(ts_val)
        )
        d_phrase = (
            str(row["date_phrase"])
            if "date_phrase" in keys and row["date_phrase"] is not None
            else None
        )
        src_sess = (
            str(row["source_session_id"])
            if "source_session_id" in keys and row["source_session_id"] is not None
            else None
        )

        reverses_id = None
        if "reverses_event_id" in keys and row["reverses_event_id"] is not None:
            reverses_id = str(row["reverses_event_id"])
        elif "voids_event_id" in keys and row["voids_event_id"] is not None:
            reverses_id = str(row["voids_event_id"])

        return LedgerEventRecord(
            seq=int(row["seq"]),
            event_id=eid,
            command_id=cmd_id,
            type=LedgerEventType(str(row["type"])),
            customer_id=str(row["customer_id"]),
            amount_paise=amt_paise,
            business_date=b_date,
            effective_date=str(row["effective_date"]),
            date_phrase=d_phrase,
            ts=ts_val,
            source_session_id=src_sess,
            reverses_event_id=reverses_id,
            note=note,
            session_scope=(
                str(row["session_scope"]) if row["session_scope"] is not None else None
            ),
        )

    def _run_tx_with_retry(self, fn: Any) -> Any:
        delay_ms = 5
        for attempt in range(8):
            try:
                return fn()
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                    raise
                if attempt == 7:
                    raise
                time.sleep(delay_ms // 1000 or 0.005)
                delay_ms = min(delay_ms * 2, 100)
        raise RuntimeError("unreachable")

    async def _shielded_write(self, fn: Any) -> Any:
        async def _task():
            async with self._write_lock:
                return await asyncio.to_thread(self._run_tx_with_retry, fn)

        return await asyncio.shield(_task())

    # ------------------------------------------------------------------
    # Customer Management & Aliases
    # ------------------------------------------------------------------
    def _add_customer_sync(self, name: str, customer_id: str | None) -> CustomerRecord:
        cleaned = " ".join(name.strip().split())
        if not cleaned:
            raise LedgerValidationError("Customer name cannot be empty")
        cid = customer_id.strip() if customer_id else f"cust_{uuid.uuid4().hex[:12]}"
        norm = phonetic_key(cleaned)
        if not norm:
            raise LedgerValidationError("Customer name must contain phonetic characters")
        script = detect_script(cleaned)
        created_ts = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        name_enc = self._cipher.encrypt_str(cleaned, aad=f"customer:{cid}:name".encode("ascii"))

        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT id, name_enc, name_norm, script, created_ts FROM customers WHERE id = ?",
                (cid,),
            ).fetchone()
            if existing is not None:
                conn.execute("COMMIT")
                return self._decrypt_customer_row(existing)

            conn.execute(
                """
                INSERT INTO customers (id, name_enc, name_norm, script, created_ts)
                VALUES (?, ?, ?, ?, ?)
                """,
                (cid, name_enc, norm, script, created_ts),
            )
            conn.execute("COMMIT")
            return CustomerRecord(
                id=cid,
                name=cleaned,
                name_norm=norm,
                script=script,
                created_ts=created_ts,
            )
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    async def add_customer(
        self, name: str, *, customer_id: str | None = None
    ) -> CustomerRecord:
        """Create a customer with AES-GCM encrypted name and phonetic key."""
        return await self._shielded_write(lambda: self._add_customer_sync(name, customer_id))

    def _add_customer_alias_sync(self, customer_id: str, alias: str) -> CustomerAliasRecord:
        cleaned = " ".join(alias.strip().split())
        if not cleaned:
            raise LedgerValidationError("Alias cannot be empty")
        norm = phonetic_key(cleaned)
        if not norm:
            raise LedgerValidationError("Alias must contain phonetic characters")
        alias_id = f"alias_{uuid.uuid4().hex[:12]}"
        alias_enc = self._cipher.encrypt_str(
            cleaned, aad=f"customer:{customer_id}:alias:{norm}".encode("ascii")
        )

        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cust = conn.execute("SELECT 1 FROM customers WHERE id = ?", (customer_id,)).fetchone()
            if cust is None:
                raise LedgerNotFoundError(f"Customer {customer_id!r} not found")

            existing = conn.execute(
                "SELECT id, customer_id, alias_norm, alias_enc FROM customer_aliases WHERE customer_id = ? AND alias_norm = ?",
                (customer_id, norm),
            ).fetchone()
            if existing is not None:
                conn.execute("COMMIT")
                return CustomerAliasRecord(
                    id=str(existing["id"]),
                    customer_id=str(existing["customer_id"]),
                    alias_norm=str(existing["alias_norm"]),
                    alias_enc=bytes(existing["alias_enc"]),
                )

            conn.execute(
                """
                INSERT INTO customer_aliases (id, customer_id, alias_norm, alias_enc)
                VALUES (?, ?, ?, ?)
                """,
                (alias_id, customer_id, norm, alias_enc),
            )
            conn.execute("COMMIT")
            return CustomerAliasRecord(
                id=alias_id,
                customer_id=customer_id,
                alias_norm=norm,
                alias_enc=alias_enc,
            )
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    async def add_customer_alias(self, customer_id: str, alias: str) -> CustomerAliasRecord:
        """Add phonetic alias mapping for an existing customer."""
        return await self._shielded_write(lambda: self._add_customer_alias_sync(customer_id, alias))

    def _get_customer_sync(self, customer_id: str) -> CustomerRecord | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT id, name_enc, name_norm, script, created_ts FROM customers WHERE id = ?",
                (customer_id,),
            ).fetchone()
            return self._decrypt_customer_row(row) if row is not None else None
        finally:
            conn.close()

    async def get_customer(self, customer_id: str) -> CustomerRecord | None:
        return await asyncio.to_thread(self._get_customer_sync, customer_id)

    def _customers_matching_sync(self, name: str) -> list[CustomerRecord]:
        query_key = phonetic_key(name)
        if not query_key:
            return []
        query_tokens = query_key.split()
        first_tok = query_tokens[0]

        conn = self._connect()
        try:
            matched_map: dict[str, CustomerRecord] = {}

            # 1. Search primary customer names
            rows = conn.execute(
                """
                SELECT id, name_enc, name_norm, script, created_ts
                FROM customers
                WHERE name_norm = ?
                   OR name_norm LIKE ?
                   OR name_norm LIKE ?
                   OR name_norm LIKE ?
                ORDER BY created_ts ASC, id ASC
                """,
                (first_tok, f"{first_tok} %", f"% {first_tok}", f"% {first_tok} %"),
            ).fetchall()

            for row in rows:
                row_tokens = str(row["name_norm"]).split()
                if all(qt in row_tokens for qt in query_tokens):
                    rec = self._decrypt_customer_row(row)
                    matched_map[rec.id] = rec

            # 2. Search aliases
            alias_rows = conn.execute(
                """
                SELECT c.id, c.name_enc, c.name_norm, c.script, c.created_ts, a.alias_norm
                FROM customer_aliases a
                JOIN customers c ON c.id = a.customer_id
                WHERE a.alias_norm = ?
                   OR a.alias_norm LIKE ?
                   OR a.alias_norm LIKE ?
                   OR a.alias_norm LIKE ?
                ORDER BY c.created_ts ASC, c.id ASC
                """,
                (first_tok, f"{first_tok} %", f"% {first_tok}", f"% {first_tok} %"),
            ).fetchall()

            for row in alias_rows:
                cid = str(row["id"])
                if cid in matched_map:
                    continue
                row_tokens = str(row["alias_norm"]).split()
                if all(qt in row_tokens for qt in query_tokens):
                    matched_map[cid] = self._decrypt_customer_row(row)

            return list(matched_map.values())
        finally:
            conn.close()

    async def customers_matching(self, name: str) -> list[CustomerRecord]:
        """Return all customers whose phonetic key or alias matches the queried name."""
        return await asyncio.to_thread(self._customers_matching_sync, name)

    def _customers_count_sync(self) -> int:
        conn = self._connect()
        try:
            row = conn.execute("SELECT count(*) FROM customers").fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()

    async def customers_count(self) -> int:
        return await asyncio.to_thread(self._customers_count_sync)

    def _customers_all_sync(self) -> list[CustomerRecord]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, name_enc, name_norm, script, created_ts FROM customers ORDER BY created_ts ASC, id ASC"
            ).fetchall()
            return [self._decrypt_customer_row(r) for r in rows]
        finally:
            conn.close()

    async def customers_all(self) -> list[CustomerRecord]:
        return await asyncio.to_thread(self._customers_all_sync)

    # ------------------------------------------------------------------
    # Event Writes (Idempotent post_event, reverse, correct)
    # ------------------------------------------------------------------
    def _post_event_sync(
        self,
        event: LedgerEventInput,
        audit_info: CommandAuditRecord | dict[str, Any] | None = None,
    ) -> LedgerEventRecord:
        note_enc = (
            self._cipher.encrypt_str(
                event.note, aad=f"event:{event.event_id}:note".encode("ascii")
            )
            if event.note is not None
            else None
        )

        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")

            # 1. Idempotency check on command_id
            if event.command_id:
                existing_cmd = conn.execute(
                    "SELECT * FROM events WHERE command_id = ?",
                    (event.command_id,),
                ).fetchone()
                if existing_cmd is not None:
                    conn.execute("COMMIT")
                    return self._decrypt_event_row(existing_cmd)

            # 2. Idempotency check on event_id
            existing_eid = conn.execute(
                "SELECT * FROM events WHERE event_id = ?",
                (event.event_id,),
            ).fetchone()
            if existing_eid is not None:
                rec = self._decrypt_event_row(existing_eid)
                if (
                    rec.type != event.type
                    or rec.customer_id != event.customer_id
                    or rec.amount_paise != event.amount_paise
                    or rec.effective_date != event.effective_date
                    or rec.reverses_event_id != event.reverses_event_id
                ):
                    conn.execute("ROLLBACK")
                    raise IdempotencyConflictError(
                        f"event_id {event.event_id!r} already exists with a different payload"
                    )
                conn.execute("COMMIT")
                return rec

            # 3. Check customer existence
            cust = conn.execute(
                "SELECT id FROM customers WHERE id = ?", (event.customer_id,)
            ).fetchone()
            if cust is None:
                raise LedgerNotFoundError(f"Customer {event.customer_id!r} not found")

            # 4. Handle REVERSAL validation and target lookup
            amt_paise = event.amount_paise
            cid = event.customer_id

            if event.type == LedgerEventType.REVERSAL:
                if not event.reverses_event_id:
                    raise LedgerValidationError("REVERSAL event requires reverses_event_id")
                target = conn.execute(
                    "SELECT * FROM events WHERE event_id = ?",
                    (event.reverses_event_id,),
                ).fetchone()
                if target is None:
                    raise LedgerNotFoundError(
                        f"Target event {event.reverses_event_id!r} not found"
                    )
                if str(target["type"]) in ("REVERSAL", "VOID"):
                    raise LedgerValidationError("Cannot reverse a reversal")

                # Check if target is already reversed
                already_rev = conn.execute(
                    "SELECT 1 FROM events WHERE type IN ('REVERSAL', 'VOID') AND reverses_event_id = ?",
                    (event.reverses_event_id,),
                ).fetchone()
                if already_rev is not None:
                    raise LedgerValidationError("Event is already reversed")

                # REVERSAL row copies amount and customer of the target
                target_rec = self._decrypt_event_row(target)
                cid = target_rec.customer_id
                amt_paise = target_rec.amount_paise

            elif event.reverses_event_id is not None:
                raise LedgerValidationError("Only REVERSAL events may set reverses_event_id")

            # 5. Populate business_date
            b_date = event.business_date or epoch_ms_to_ist_date_str(event.ts)
            cmd_id = event.command_id or f"cmd_{event.event_id}"

            cur = conn.execute(
                """
                INSERT INTO events (
                    event_id, command_id, type, customer_id, amount_paise,
                    business_date, effective_date, date_phrase, ts,
                    source_session_id, reverses_event_id, note_enc, session_scope
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    cmd_id,
                    event.type.value,
                    cid,
                    amt_paise,
                    b_date,
                    event.effective_date,
                    event.date_phrase,
                    event.ts,
                    event.source_session_id,
                    event.reverses_event_id,
                    note_enc,
                    event.session_scope,
                ),
            )
            seq = int(cur.lastrowid)

            # 6. Insert entry_items if provided
            created_items: list[EntryItemRecord] = []
            if event.items:
                item_sum = sum(it.amount_paise for it in event.items)
                if item_sum != amt_paise:
                    raise LedgerValidationError(
                        f"Item sum ({item_sum}) must equal event amount ({amt_paise})"
                    )
                for it in event.items:
                    item_id = f"item_{uuid.uuid4().hex[:12]}"
                    name_enc = self._cipher.encrypt_str(
                        it.name, aad=f"entry_item:{item_id}:name".encode("ascii")
                    )
                    conn.execute(
                        """
                        INSERT INTO entry_items (id, event_id, name_enc, qty_text, amount_paise)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (item_id, event.event_id, name_enc, it.qty_text, it.amount_paise),
                    )
                    created_items.append(
                        EntryItemRecord(
                            id=item_id,
                            event_id=event.event_id,
                            name=it.name,
                            qty_text=it.qty_text,
                            amount_paise=it.amount_paise,
                        )
                    )

            # 7. Record command audit if provided
            if audit_info:
                audit_model = (
                    audit_info
                    if isinstance(audit_info, CommandAuditRecord)
                    else CommandAuditRecord.model_validate(audit_info)
                )
                conn.execute(
                    """
                    INSERT OR REPLACE INTO command_audit (
                        command_id, session_id, parsed_intent_json, confidence_band,
                        validation_result, committed_event_ids, transcript_enc, created_ts
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        audit_model.command_id,
                        audit_model.session_id,
                        audit_model.parsed_intent_json,
                        audit_model.confidence_band,
                        audit_model.validation_result,
                        audit_model.committed_event_ids or event.event_id,
                        audit_model.transcript_enc,
                        audit_model.created_ts,
                    ),
                )

            conn.execute("COMMIT")

            return LedgerEventRecord(
                seq=seq,
                event_id=event.event_id,
                command_id=cmd_id,
                type=event.type,
                customer_id=cid,
                amount_paise=amt_paise,
                business_date=b_date,
                effective_date=event.effective_date,
                date_phrase=event.date_phrase,
                ts=event.ts,
                source_session_id=event.source_session_id,
                reverses_event_id=event.reverses_event_id,
                note=event.note,
                session_scope=event.session_scope,
                items=created_items,
            )
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    async def post_event(
        self,
        event: LedgerEventInput | dict[str, Any],
        audit_info: CommandAuditRecord | dict[str, Any] | None = None,
    ) -> LedgerEventRecord:
        """Post an append-only ledger event (idempotent on command_id, shielded)."""
        model = (
            event
            if isinstance(event, LedgerEventInput)
            else LedgerEventInput.model_validate(event)
        )
        return await self._shielded_write(lambda: self._post_event_sync(model, audit_info))

    async def record(self, event: LedgerEventInput | dict[str, Any]) -> LedgerEventRecord:
        """Backward-compatible alias for post_event."""
        return await self.post_event(event)

    def _reverse_sync(
        self,
        event_id: str,
        *,
        reason: str | None = None,
        command_id: str | None = None,
        session_scope: str | None = None,
    ) -> LedgerEventRecord:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")

            target = conn.execute(
                "SELECT * FROM events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            if target is None:
                raise LedgerNotFoundError(f"Event {event_id!r} not found")
            if str(target["type"]) in ("REVERSAL", "VOID"):
                raise LedgerValidationError("Cannot reverse a reversal")

            # Check if target is already reversed
            existing_rev = conn.execute(
                "SELECT * FROM events WHERE type IN ('REVERSAL', 'VOID') AND reverses_event_id = ?",
                (event_id,),
            ).fetchone()
            if existing_rev is not None:
                rec = self._decrypt_event_row(existing_rev)
                # If exact same command_id is retried, return existing
                if command_id and rec.command_id == command_id:
                    conn.execute("COMMIT")
                    return rec
                raise LedgerValidationError("Event is already reversed")

            target_rec = self._decrypt_event_row(target)
            rev_eid = f"rev_{uuid.uuid4().hex[:12]}"
            cmd_id = command_id or f"cmd_{rev_eid}"
            now_ts = int(time.time_ns() // 1_000_000)
            b_date = epoch_ms_to_ist_date_str(now_ts)
            eff_date = target_rec.effective_date
            cid = target_rec.customer_id
            amt_paise = target_rec.amount_paise
            resolved_scope = session_scope or target_rec.session_scope

            note_enc = (
                self._cipher.encrypt_str(reason, aad=f"event:{rev_eid}:note".encode("ascii"))
                if reason is not None
                else None
            )

            cur = conn.execute(
                """
                INSERT INTO events (
                    event_id, command_id, type, customer_id, amount_paise,
                    business_date, effective_date, date_phrase, ts,
                    source_session_id, reverses_event_id, note_enc, session_scope
                )
                VALUES (?, ?, 'REVERSAL', ?, ?, ?, ?, NULL, ?, NULL, ?, ?, ?)
                """,
                (
                    rev_eid,
                    cmd_id,
                    cid,
                    amt_paise,
                    b_date,
                    eff_date,
                    now_ts,
                    event_id,
                    note_enc,
                    resolved_scope,
                ),
            )
            seq = int(cur.lastrowid)
            conn.execute("COMMIT")

            return LedgerEventRecord(
                seq=seq,
                event_id=rev_eid,
                command_id=cmd_id,
                type=LedgerEventType.REVERSAL,
                customer_id=cid,
                amount_paise=amt_paise,
                business_date=b_date,
                effective_date=eff_date,
                ts=now_ts,
                reverses_event_id=event_id,
                note=reason,
                session_scope=resolved_scope,
            )
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    async def reverse(
        self,
        event_id: str,
        *,
        reason: str | None = None,
        command_id: str | None = None,
        session_scope: str | None = None,
    ) -> LedgerEventRecord:
        """Reverse an existing event (refuses to reverse a reversal or already reversed event)."""
        return await self._shielded_write(
            lambda: self._reverse_sync(
                event_id, reason=reason, command_id=command_id, session_scope=session_scope
            )
        )

    async def void(
        self,
        event_id: str,
        *,
        void_event_id: str | None = None,
        session_scope: str | None = None,
        note: str | None = None,
    ) -> LedgerEventRecord:
        """Backward-compatible alias for reverse."""
        return await self._shielded_write(
            lambda: self._reverse_sync(
                event_id, reason=note, command_id=void_event_id, session_scope=session_scope
            )
        )

    def _correct_sync(
        self,
        event_id: str,
        new_fields: dict[str, Any] | LedgerEventInput,
        *,
        reason: str | None = None,
        command_id: str | None = None,
    ) -> tuple[LedgerEventRecord, LedgerEventRecord]:
        """Atomically reverse event_id and post a corrected replacement in ONE transaction."""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")

            # 1. Target check
            target = conn.execute(
                "SELECT * FROM events WHERE event_id = ?", (event_id,)
            ).fetchone()
            if target is None:
                raise LedgerNotFoundError(f"Event {event_id!r} not found")
            if str(target["type"]) in ("REVERSAL", "VOID"):
                raise LedgerValidationError("Cannot reverse a reversal")

            already_rev = conn.execute(
                "SELECT 1 FROM events WHERE type IN ('REVERSAL', 'VOID') AND reverses_event_id = ?",
                (event_id,),
            ).fetchone()
            if already_rev is not None:
                raise LedgerValidationError("Event is already reversed")

            target_rec = self._decrypt_event_row(target)
            now_ts = int(time.time_ns() // 1_000_000)
            b_date = epoch_ms_to_ist_date_str(now_ts)

            # 2. Append REVERSAL event
            rev_eid = f"rev_{uuid.uuid4().hex[:12]}"
            rev_cmd = f"{command_id}_rev" if command_id else f"cmd_{rev_eid}"
            note_rev = (
                self._cipher.encrypt_str(reason or "correction reversal", aad=f"event:{rev_eid}:note".encode("ascii"))
            )

            cur_rev = conn.execute(
                """
                INSERT INTO events (
                    event_id, command_id, type, customer_id, amount_paise,
                    business_date, effective_date, date_phrase, ts,
                    source_session_id, reverses_event_id, note_enc, session_scope
                )
                VALUES (?, ?, 'REVERSAL', ?, ?, ?, ?, NULL, ?, NULL, ?, ?, ?)
                """,
                (
                    rev_eid,
                    rev_cmd,
                    target_rec.customer_id,
                    target_rec.amount_paise,
                    b_date,
                    target_rec.effective_date,
                    now_ts,
                    event_id,
                    note_rev,
                    target_rec.session_scope,
                ),
            )
            seq_rev = int(cur_rev.lastrowid)

            reversal_record = LedgerEventRecord(
                seq=seq_rev,
                event_id=rev_eid,
                command_id=rev_cmd,
                type=LedgerEventType.REVERSAL,
                customer_id=target_rec.customer_id,
                amount_paise=target_rec.amount_paise,
                business_date=b_date,
                effective_date=target_rec.effective_date,
                ts=now_ts,
                reverses_event_id=event_id,
                note=reason or "correction reversal",
                session_scope=target_rec.session_scope,
            )

            # 3. Build new corrected event
            raw_fields = new_fields.model_dump() if isinstance(new_fields, LedgerEventInput) else dict(new_fields)
            new_eid = raw_fields.get("event_id") or f"ev_{uuid.uuid4().hex[:12]}"
            new_cmd = command_id or f"cmd_{new_eid}"
            new_type_str = raw_fields.get("type", target_rec.type.value)
            new_type = LedgerEventType(new_type_str)
            new_cid = raw_fields.get("customer_id", target_rec.customer_id)

            if "amount_paise" in raw_fields:
                new_amt = _strict_int_paise(raw_fields["amount_paise"])
            elif "amount_inr" in raw_fields:
                new_amt = _strict_int_paise(int(raw_fields["amount_inr"]) * 100)
            else:
                new_amt = target_rec.amount_paise

            new_eff_date = raw_fields.get("effective_date", target_rec.effective_date)
            new_note = raw_fields.get("note", target_rec.note)
            new_scope = raw_fields.get("session_scope", target_rec.session_scope)

            # Check new customer exists
            cust = conn.execute("SELECT 1 FROM customers WHERE id = ?", (new_cid,)).fetchone()
            if cust is None:
                raise LedgerNotFoundError(f"Customer {new_cid!r} not found")

            new_note_enc = (
                self._cipher.encrypt_str(new_note, aad=f"event:{new_eid}:note".encode("ascii"))
                if new_note is not None
                else None
            )

            cur_new = conn.execute(
                """
                INSERT INTO events (
                    event_id, command_id, type, customer_id, amount_paise,
                    business_date, effective_date, date_phrase, ts,
                    source_session_id, reverses_event_id, note_enc, session_scope
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL, NULL, ?, ?)
                """,
                (
                    new_eid,
                    new_cmd,
                    new_type.value,
                    new_cid,
                    new_amt,
                    b_date,
                    new_eff_date,
                    now_ts + 1,
                    new_note_enc,
                    new_scope,
                ),
            )
            seq_new = int(cur_new.lastrowid)

            # Insert items for corrected event if present
            created_items: list[EntryItemRecord] = []
            raw_items = raw_fields.get("items", [])
            if raw_items:
                parsed_items = [
                    it if isinstance(it, EntryItemInput) else EntryItemInput.model_validate(it)
                    for it in raw_items
                ]
                item_sum = sum(it.amount_paise for it in parsed_items)
                if item_sum != new_amt:
                    raise LedgerValidationError(
                        f"Item sum ({item_sum}) must equal event amount ({new_amt})"
                    )
                for it in parsed_items:
                    item_id = f"item_{uuid.uuid4().hex[:12]}"
                    name_enc = self._cipher.encrypt_str(
                        it.name, aad=f"entry_item:{item_id}:name".encode("ascii")
                    )
                    conn.execute(
                        """
                        INSERT INTO entry_items (id, event_id, name_enc, qty_text, amount_paise)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (item_id, new_eid, name_enc, it.qty_text, it.amount_paise),
                    )
                    created_items.append(
                        EntryItemRecord(
                            id=item_id,
                            event_id=new_eid,
                            name=it.name,
                            qty_text=it.qty_text,
                            amount_paise=it.amount_paise,
                        )
                    )

            conn.execute("COMMIT")

            corrected_record = LedgerEventRecord(
                seq=seq_new,
                event_id=new_eid,
                command_id=new_cmd,
                type=new_type,
                customer_id=new_cid,
                amount_paise=new_amt,
                business_date=b_date,
                effective_date=new_eff_date,
                ts=now_ts + 1,
                note=new_note,
                session_scope=new_scope,
                items=created_items,
            )
            return reversal_record, corrected_record
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    async def correct(
        self,
        event_id: str,
        new_fields: dict[str, Any] | LedgerEventInput,
        *,
        reason: str | None = None,
        command_id: str | None = None,
    ) -> tuple[LedgerEventRecord, LedgerEventRecord]:
        """Atomically reverse event_id and append replacement event in ONE transaction."""
        return await self._shielded_write(
            lambda: self._correct_sync(event_id, new_fields, reason=reason, command_id=command_id)
        )

    # ------------------------------------------------------------------
    # Effective Events & Balances (Requirements 2, 3)
    # ------------------------------------------------------------------
    def _effective_events_sync(self, customer_id: str | None = None) -> list[LedgerEventRecord]:
        conn = self._connect()
        try:
            clauses = [
                "e.type NOT IN ('REVERSAL', 'VOID')",
                """NOT EXISTS (
                    SELECT 1 FROM events r
                    WHERE r.type IN ('REVERSAL', 'VOID')
                      AND r.reverses_event_id = e.event_id
                )""",
            ]
            params: list[Any] = []
            if customer_id is not None:
                clauses.append("e.customer_id = ?")
                params.append(customer_id)

            where_sql = f"WHERE {' AND '.join(clauses)}"
            rows = conn.execute(
                f"SELECT * FROM events e {where_sql} ORDER BY e.ts ASC, e.seq ASC",
                tuple(params),
            ).fetchall()
            return [self._decrypt_event_row(r) for r in rows]
        finally:
            conn.close()

    async def effective_events(self, customer_id: str | None = None) -> list[LedgerEventRecord]:
        """Return non-reversal events with no reversal pointing at them."""
        return await asyncio.to_thread(self._effective_events_sync, customer_id)

    def _balance_sync(self, customer_id: str) -> int:
        """Signed sum over ALL rows: credit +, payment -, reversal of credit -, reversal of payment +."""
        conn = self._connect()
        try:
            exists = conn.execute(
                "SELECT 1 FROM customers WHERE id = ?", (customer_id,)
            ).fetchone()
            if exists is None:
                raise LedgerNotFoundError(f"Customer {customer_id!r} not found")

            row = conn.execute(
                """
                SELECT COALESCE(SUM(
                    CASE
                        WHEN e.type IN ('CREDIT_GIVEN', 'CREDIT') THEN e.amount_paise
                        WHEN e.type IN ('PAYMENT_RECEIVED', 'PAYMENT') THEN -e.amount_paise
                        WHEN e.type IN ('REVERSAL', 'VOID') THEN
                            CASE
                                WHEN target.type IN ('CREDIT_GIVEN', 'CREDIT') THEN -e.amount_paise
                                WHEN target.type IN ('PAYMENT_RECEIVED', 'PAYMENT') THEN e.amount_paise
                                ELSE 0
                            END
                        ELSE 0
                    END
                ), 0) AS balance_paise
                FROM events e
                LEFT JOIN events target ON e.reverses_event_id = target.event_id
                WHERE e.customer_id = ?
                """,
                (customer_id,),
            ).fetchone()
            return int(row["balance_paise"]) if row is not None else 0
        finally:
            conn.close()

    async def balance(self, customer: str | CustomerRecord) -> int:
        """Compute integer paise balance for a customer via signed sum over ALL rows."""
        cid = customer.id if isinstance(customer, CustomerRecord) else str(customer)
        return await asyncio.to_thread(self._balance_sync, cid)

    def _balances_all_sync(self) -> list[CustomerBalanceRecord]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT
                    c.id,
                    c.name_enc,
                    c.name_norm,
                    c.script,
                    c.created_ts,
                    COALESCE(SUM(
                        CASE
                            WHEN e.type IN ('CREDIT_GIVEN', 'CREDIT') THEN e.amount_paise
                            WHEN e.type IN ('PAYMENT_RECEIVED', 'PAYMENT') THEN -e.amount_paise
                            WHEN e.type IN ('REVERSAL', 'VOID') THEN
                                CASE
                                    WHEN target.type IN ('CREDIT_GIVEN', 'CREDIT') THEN -e.amount_paise
                                    WHEN target.type IN ('PAYMENT_RECEIVED', 'PAYMENT') THEN e.amount_paise
                                    ELSE 0
                                END
                            ELSE 0
                        END
                    ), 0) AS balance_paise,
                    (
                        SELECT COUNT(*)
                        FROM entry_items ei
                        JOIN events ev ON ev.event_id = ei.event_id
                        LEFT JOIN events v2 ON v2.type IN ('REVERSAL', 'VOID') AND v2.reverses_event_id = ev.event_id
                        WHERE ev.customer_id = c.id AND v2.seq IS NULL
                    ) AS item_count,
                    EXISTS (
                        SELECT 1
                        FROM bills b
                        JOIN events ev ON ev.event_id = b.event_id
                        LEFT JOIN events v3 ON v3.type IN ('REVERSAL', 'VOID') AND v3.reverses_event_id = ev.event_id
                        WHERE ev.customer_id = c.id AND v3.seq IS NULL
                    ) AS has_bill,
                    EXISTS (
                        SELECT 1
                        FROM events ce
                        LEFT JOIN events v4 ON v4.type IN ('REVERSAL', 'VOID') AND v4.reverses_event_id = ce.event_id
                        WHERE ce.customer_id = c.id AND ce.type = 'CONSENT' AND v4.seq IS NULL
                    ) AS has_consent
                FROM customers c
                LEFT JOIN events e
                    ON e.customer_id = c.id
                LEFT JOIN events target
                    ON e.reverses_event_id = target.event_id
                GROUP BY c.id
                ORDER BY c.created_ts ASC, c.id ASC
                """
            ).fetchall()
            return [
                CustomerBalanceRecord(
                    customer=self._decrypt_customer_row(row),
                    balance_paise=int(row["balance_paise"]),
                    item_count=int(row["item_count"]) if "item_count" in row.keys() else 0,
                    has_bill=bool(row["has_bill"]) if "has_bill" in row.keys() else False,
                    has_consent=bool(row["has_consent"]) if "has_consent" in row.keys() else False,
                )
                for row in rows
            ]
        finally:
            conn.close()

    async def balances_all(self) -> list[CustomerBalanceRecord]:
        """Return computed balances for all customers via signed aggregate."""
        return await asyncio.to_thread(self._balances_all_sync)

    def _outstanding_total_sync(self) -> int:
        balances = self._balances_all_sync()
        return sum(b.balance_paise for b in balances if b.balance_paise > 0)

    async def outstanding_total(self) -> int:
        """Sum of customer balances where balance > 0 (customers with balance <= 0 not counted)."""
        return await asyncio.to_thread(self._outstanding_total_sync)

    def _todays_credit_sync(self, business_date: str | None = None) -> int:
        b_date = business_date or epoch_ms_to_ist_date_str(int(time.time_ns() // 1_000_000))
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(e.amount_paise), 0) AS total_paise
                FROM events e
                WHERE e.type IN ('CREDIT_GIVEN', 'CREDIT')
                  AND e.business_date = ?
                  AND NOT EXISTS (
                      SELECT 1 FROM events r
                      WHERE r.type IN ('REVERSAL', 'VOID') AND r.reverses_event_id = e.event_id
                  )
                """,
                (b_date,),
            ).fetchone()
            return int(row["total_paise"]) if row is not None else 0
        finally:
            conn.close()

    async def todays_credit(self, business_date: str | None = None) -> int:
        """Sum of effective CREDIT_GIVEN events for shop-local business_date (in integer paise)."""
        return await asyncio.to_thread(self._todays_credit_sync, business_date)

    def _todays_payments_sync(self, business_date: str | None = None) -> int:
        b_date = business_date or epoch_ms_to_ist_date_str(int(time.time_ns() // 1_000_000))
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(e.amount_paise), 0) AS total_paise
                FROM events e
                WHERE e.type IN ('PAYMENT_RECEIVED', 'PAYMENT')
                  AND e.business_date = ?
                  AND NOT EXISTS (
                      SELECT 1 FROM events r
                      WHERE r.type IN ('REVERSAL', 'VOID') AND r.reverses_event_id = e.event_id
                  )
                """,
                (b_date,),
            ).fetchone()
            return int(row["total_paise"]) if row is not None else 0
        finally:
            conn.close()

    async def todays_payments(self, business_date: str | None = None) -> int:
        """Sum of effective PAYMENT_RECEIVED events for shop-local business_date (in integer paise)."""
        return await asyncio.to_thread(self._todays_payments_sync, business_date)

    def _find_near_duplicates_sync(
        self,
        customer_id: str,
        amount_paise: int,
        type: LedgerEventType | str,
        window_s: int = 60,
        current_ts: int | None = None,
    ) -> list[LedgerEventRecord]:
        ref_ts = current_ts or int(time.time_ns() // 1_000_000)
        min_ts = ref_ts - (window_s * 1000)
        max_ts = ref_ts + (window_s * 1000)
        type_str = type.value if isinstance(type, LedgerEventType) else str(type)
        if type_str == "CREDIT":
            type_str = "CREDIT_GIVEN"
        elif type_str == "PAYMENT":
            type_str = "PAYMENT_RECEIVED"

        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT e.* FROM events e
                WHERE e.customer_id = ?
                AND e.amount_paise = ?
                AND (e.type = ? OR (e.type = 'CREDIT' AND ? = 'CREDIT_GIVEN') OR (e.type = 'PAYMENT' AND ? = 'PAYMENT_RECEIVED'))
                AND e.ts >= ? AND e.ts <= ?
                AND NOT EXISTS (
                    SELECT 1 FROM events r
                    WHERE r.type IN ('REVERSAL', 'VOID') AND r.reverses_event_id = e.event_id
                )
                ORDER BY e.ts DESC
                """,
                (customer_id, amount_paise, type_str, type_str, type_str, min_ts, max_ts),
            ).fetchall()
            return [self._decrypt_event_row(r) for r in rows]
        finally:
            conn.close()

    async def find_near_duplicates(
        self,
        customer_id: str,
        amount_paise: int,
        type: LedgerEventType | str,
        window_s: int = 60,
        current_ts: int | None = None,
    ) -> list[LedgerEventRecord]:
        return await asyncio.to_thread(
            self._find_near_duplicates_sync, customer_id, amount_paise, type, window_s, current_ts
        )

    # ------------------------------------------------------------------
    # Summary, Received, Customer Timeline Queries
    # ------------------------------------------------------------------
    def _summary_sync(self) -> SummaryResult:
        balances = self._balances_all_sync()
        owing_count = 0
        total_owed = 0
        total_advance = 0
        advance_count = 0
        for b in balances:
            bal = b.balance_paise
            if bal > 0:
                owing_count += 1
                total_owed += bal
            elif bal < 0:
                advance_count += 1
                total_advance += (-bal)
        return SummaryResult(
            owing_count=owing_count,
            total_owed_paise=total_owed,
            total_advance_paise=total_advance,
            advance_count=advance_count,
        )

    async def summary(self) -> SummaryResult:
        """Summary query: {owing_count, total_owed_paise}. Customers with balance <= 0 not counted as owing."""
        return await asyncio.to_thread(self._summary_sync)

    def _received_sync(
        self,
        from_date: str | date | datetime | int | None = None,
        to_date: str | date | datetime | int | None = None,
    ) -> ReceivedResult:
        from_str: str | None = None
        if from_date is not None:
            if isinstance(from_date, int):
                from_str = epoch_ms_to_ist_date_str(from_date)
            elif isinstance(from_date, (datetime, date)):
                from_str = from_date.strftime("%Y-%m-%d")
            else:
                from_str = str(from_date).strip()[:10]

        to_str: str | None = None
        if to_date is not None:
            if isinstance(to_date, int):
                to_str = epoch_ms_to_ist_date_str(to_date)
            elif isinstance(to_date, (datetime, date)):
                to_str = to_date.strftime("%Y-%m-%d")
            else:
                to_str = str(to_date).strip()[:10]

        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT
                    e.seq,
                    e.event_id,
                    e.customer_id,
                    c.name_enc,
                    e.amount_paise,
                    e.ts,
                    e.effective_date,
                    e.business_date,
                    e.note_enc
                FROM events e
                JOIN customers c ON c.id = e.customer_id
                WHERE e.type IN ('PAYMENT_RECEIVED', 'PAYMENT')
                  AND NOT EXISTS (
                      SELECT 1 FROM events r
                      WHERE r.type IN ('REVERSAL', 'VOID') AND r.reverses_event_id = e.event_id
                  )
                ORDER BY e.ts DESC, e.seq DESC
                """
            ).fetchall()

            groups_map: dict[str, list[ReceivedPaymentItem]] = {}
            range_total = 0

            for r in rows:
                ts_ms = _normalize_ts_epoch_ms(r["ts"])
                dt_ist = epoch_ms_to_ist_datetime(ts_ms)
                day_key = dt_ist.date().isoformat()

                if from_str is not None and day_key < from_str:
                    continue
                if to_str is not None and day_key > to_str:
                    continue

                cid = str(r["customer_id"])
                eid = str(r["event_id"])
                cust_name = self._cipher.decrypt_str(
                    bytes(r["name_enc"]), aad=f"customer:{cid}:name".encode("ascii")
                )
                note = (
                    self._cipher.decrypt_str(
                        bytes(r["note_enc"]), aad=f"event:{eid}:note".encode("ascii")
                    )
                    if r["note_enc"] is not None
                    else None
                )
                amt = int(r["amount_paise"])
                range_total += amt

                b_date = (
                    str(r["business_date"])
                    if "business_date" in r.keys() and r["business_date"] is not None
                    else day_key
                )

                item = ReceivedPaymentItem(
                    seq=int(r["seq"]),
                    event_id=eid,
                    customer_id=cid,
                    customer_name=cust_name,
                    amount_paise=amt,
                    ts=ts_ms,
                    ist_time=dt_ist.strftime("%d %b %Y, %I:%M %p"),
                    effective_date=str(r["effective_date"]),
                    business_date=b_date,
                    note=note,
                )
                if day_key not in groups_map:
                    groups_map[day_key] = []
                groups_map[day_key].append(item)

            sorted_days = sorted(groups_map.keys(), reverse=True)
            day_groups = [
                ReceivedDayGroup(
                    date=d,
                    total_paise=sum(item.amount_paise for item in groups_map[d]),
                    count=len(groups_map[d]),
                    items=groups_map[d],
                )
                for d in sorted_days
            ]
            return ReceivedResult(range_total_paise=range_total, days=day_groups)
        finally:
            conn.close()

    async def received(
        self,
        from_date: str | date | datetime | int | None = None,
        to_date: str | date | datetime | int | None = None,
    ) -> ReceivedResult:
        """Effective PAYMENT_RECEIVED events grouped by IST day, newest first."""
        return await asyncio.to_thread(self._received_sync, from_date, to_date)

    def _customer_timeline_sync(self, customer_id: str) -> CustomerTimelineResult:
        conn = self._connect()
        try:
            cust_row = conn.execute(
                "SELECT id, name_enc FROM customers WHERE id = ?", (customer_id,)
            ).fetchone()
            if cust_row is None:
                raise LedgerNotFoundError(f"Customer {customer_id!r} not found")

            cust_name = self._cipher.decrypt_str(
                bytes(cust_row["name_enc"]), aad=f"customer:{customer_id}:name".encode("ascii")
            )

            # Get all events for customer in chronological order
            rows = conn.execute(
                """
                SELECT
                    e.seq,
                    e.event_id,
                    e.type,
                    e.amount_paise,
                    e.ts,
                    e.business_date,
                    e.effective_date,
                    e.reverses_event_id,
                    e.note_enc,
                    target.type AS target_type,
                    (
                        SELECT rev.event_id FROM events rev
                        WHERE rev.type IN ('REVERSAL', 'VOID')
                          AND rev.reverses_event_id = e.event_id
                        LIMIT 1
                    ) AS reversed_by_event_id
                FROM events e
                LEFT JOIN events target
                    ON e.reverses_event_id = target.event_id
                WHERE e.customer_id = ?
                ORDER BY e.ts ASC, e.seq ASC
                """,
                (customer_id,),
            ).fetchall()

            running_balance = 0
            items: list[TimelineEventItem] = []

            for r in rows:
                amt = int(r["amount_paise"])
                ev_type = LedgerEventType(r["type"])
                target_type = str(r["target_type"]) if r["target_type"] is not None else None

                is_rev = ev_type in (LedgerEventType.REVERSAL, LedgerEventType.VOID)
                is_reversed = r["reversed_by_event_id"] is not None

                # Update running balance chronologically
                if ev_type in (LedgerEventType.CREDIT_GIVEN, LedgerEventType.CREDIT):
                    running_balance += amt
                elif ev_type in (LedgerEventType.PAYMENT_RECEIVED, LedgerEventType.PAYMENT):
                    running_balance -= amt
                elif is_rev:
                    if target_type in ("CREDIT_GIVEN", "CREDIT"):
                        running_balance -= amt
                    elif target_type in ("PAYMENT_RECEIVED", "PAYMENT"):
                        running_balance += amt

                eid = str(r["event_id"])
                note = (
                    self._cipher.decrypt_str(
                        bytes(r["note_enc"]), aad=f"event:{eid}:note".encode("ascii")
                    )
                    if r["note_enc"] is not None
                    else None
                )
                ts_ms = _normalize_ts_epoch_ms(r["ts"])
                dt_ist = epoch_ms_to_ist_datetime(ts_ms)

                ev_items = self._get_items_for_event_sync(conn, eid)
                ev_bills = self._get_bills_for_event_sync(conn, eid)
                ev_shares = self._get_shares_for_event_sync(conn, eid)

                items.append(
                    TimelineEventItem(
                        seq=int(r["seq"]),
                        event_id=eid,
                        type=ev_type,
                        amount_paise=amt,
                        ts=ts_ms,
                        ist_time=dt_ist.strftime("%d %b %Y, %I:%M %p"),
                        business_date=str(r["business_date"]),
                        effective_date=str(r["effective_date"]),
                        note=note,
                        running_balance_paise=running_balance,
                        is_reversal=is_rev,
                        is_reversed=is_reversed,
                        reverses_event_id=str(r["reverses_event_id"]) if r["reverses_event_id"] is not None else None,
                        reversed_by_event_id=str(r["reversed_by_event_id"]) if r["reversed_by_event_id"] is not None else None,
                        items=ev_items,
                        bills=ev_bills,
                        share_logs=ev_shares,
                    )
                )

            advance = abs(running_balance) if running_balance < 0 else 0
            is_advance = running_balance < 0

            return CustomerTimelineResult(
                customer_id=customer_id,
                customer_name=cust_name,
                balance_paise=running_balance,
                advance_paise=advance,
                is_advance=is_advance,
                events=items,
            )
        finally:
            conn.close()

    async def customer_timeline(self, customer_id: str) -> CustomerTimelineResult:
        """Customer timeline with all rows (including reversals, flagged), running balance in integer paise."""
        return await asyncio.to_thread(self._customer_timeline_sync, customer_id)

    # ------------------------------------------------------------------
    # Entry Items & Bills & Shares
    # ------------------------------------------------------------------
    def _get_items_for_event_sync(
        self, conn: sqlite3.Connection, event_id: str
    ) -> list[EntryItemRecord]:
        rows = conn.execute(
            """
            SELECT id, event_id, name_enc, qty_text, amount_paise
            FROM entry_items WHERE event_id = ?
            ORDER BY rowid ASC
            """,
            (event_id,),
        ).fetchall()
        items: list[EntryItemRecord] = []
        for r in rows:
            iid = str(r["id"])
            name = self._cipher.decrypt_str(
                bytes(r["name_enc"]), aad=f"entry_item:{iid}:name".encode("ascii")
            )
            items.append(
                EntryItemRecord(
                    id=iid,
                    event_id=str(r["event_id"]),
                    name=name,
                    qty_text=str(r["qty_text"]) if r["qty_text"] is not None else None,
                    amount_paise=int(r["amount_paise"]),
                )
            )
        return items

    def _get_event_items_sync(self, event_id: str) -> list[EntryItemRecord]:
        conn = self._connect()
        try:
            return self._get_items_for_event_sync(conn, event_id)
        finally:
            conn.close()

    async def get_event_items(self, event_id: str) -> list[EntryItemRecord]:
        return await asyncio.to_thread(self._get_event_items_sync, event_id)

    def _set_event_items_sync(
        self, event_id: str, items: list[EntryItemInput]
    ) -> list[EntryItemRecord]:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            ev = conn.execute(
                "SELECT * FROM events WHERE event_id = ?", (event_id,)
            ).fetchone()
            if ev is None:
                raise LedgerNotFoundError(f"Event {event_id!r} not found")
            if str(ev["type"]) not in ("CREDIT_GIVEN", "PAYMENT_RECEIVED", "CREDIT", "PAYMENT"):
                raise LedgerValidationError("Cannot attach items to non-transaction event")

            rev_row = conn.execute(
                "SELECT 1 FROM events WHERE type IN ('REVERSAL', 'VOID') AND reverses_event_id = ?",
                (event_id,),
            ).fetchone()
            if rev_row is not None:
                raise LedgerValidationError("Cannot edit items on a reversed event")

            ev_amount = int(ev["amount_paise"])
            item_sum = sum(it.amount_paise for it in items)
            if item_sum != ev_amount:
                raise LedgerValidationError(
                    f"Item sum ({item_sum}) must equal event amount ({ev_amount})"
                )

            conn.execute("DELETE FROM entry_items WHERE event_id = ?", (event_id,))
            created_items: list[EntryItemRecord] = []
            for it in items:
                item_id = f"item_{uuid.uuid4().hex[:12]}"
                name_enc = self._cipher.encrypt_str(
                    it.name, aad=f"entry_item:{item_id}:name".encode("ascii")
                )
                conn.execute(
                    """
                    INSERT INTO entry_items (id, event_id, name_enc, qty_text, amount_paise)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (item_id, event_id, name_enc, it.qty_text, it.amount_paise),
                )
                created_items.append(
                    EntryItemRecord(
                        id=item_id,
                        event_id=event_id,
                        name=it.name,
                        qty_text=it.qty_text,
                        amount_paise=it.amount_paise,
                    )
                )
            conn.execute("COMMIT")
            return created_items
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    async def set_event_items(
        self, event_id: str, items: list[EntryItemInput]
    ) -> list[EntryItemRecord]:
        return await self._shielded_write(lambda: self._set_event_items_sync(event_id, items))

    def _get_bills_for_event_sync(
        self, conn: sqlite3.Connection, event_id: str
    ) -> list[BillRecord]:
        rows = conn.execute(
            """
            SELECT id, event_id, mime, size, sha256, path, created_ts
            FROM bills WHERE event_id = ?
            ORDER BY created_ts ASC
            """,
            (event_id,),
        ).fetchall()
        bills: list[BillRecord] = []
        for r in rows:
            bills.append(
                BillRecord(
                    id=str(r["id"]),
                    event_id=str(r["event_id"]),
                    mime=str(r["mime"]),
                    size=int(r["size"]),
                    sha256=str(r["sha256"]),
                    path=str(r["path"]),
                    created_ts=int(r["created_ts"]),
                )
            )
        return bills

    def _get_event_bills_sync(self, event_id: str) -> list[BillRecord]:
        conn = self._connect()
        try:
            return self._get_bills_for_event_sync(conn, event_id)
        finally:
            conn.close()

    async def get_event_bills(self, event_id: str) -> list[BillRecord]:
        return await asyncio.to_thread(self._get_event_bills_sync, event_id)

    def _attach_bill_sync(
        self,
        event_id: str,
        mime: str,
        data_or_path: bytes | str,
        *,
        bill_id: str | None = None,
        created_ts: int | None = None,
    ) -> BillRecord:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            ev = conn.execute("SELECT 1 FROM events WHERE event_id = ?", (event_id,)).fetchone()
            if ev is None:
                raise LedgerNotFoundError(f"Event {event_id!r} not found")
            bid = bill_id or f"bill_{uuid.uuid4().hex}"
            ts = created_ts or int(time.time_ns() // 1_000_000)

            if isinstance(data_or_path, bytes):
                enc_data = self._cipher.encrypt_bytes(data_or_path, aad=f"bill:{bid}".encode("ascii"))
                target_path = self._bills_dir / f"{bid}.bin"
                target_path.write_bytes(enc_data)
                sha = hashlib.sha256(data_or_path).hexdigest()
                size = len(data_or_path)
                path_str = str(target_path)
            else:
                path_str = data_or_path
                sha = ""
                size = 0

            conn.execute(
                """
                INSERT INTO bills (id, event_id, mime, size, sha256, path, created_ts)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (bid, event_id, mime, size, sha, path_str, ts),
            )
            conn.execute("COMMIT")
            return BillRecord(
                id=bid,
                event_id=event_id,
                mime=mime,
                size=size,
                sha256=sha,
                path=path_str,
                created_ts=ts,
            )
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    async def attach_bill(
        self,
        event_id: str,
        mime: str,
        data_or_path: bytes | str,
        *,
        bill_id: str | None = None,
        created_ts: int | None = None,
    ) -> BillRecord:
        return await self._shielded_write(
            lambda: self._attach_bill_sync(
                event_id,
                mime,
                data_or_path,
                bill_id=bill_id,
                created_ts=created_ts,
            )
        )

    def _get_bill_by_id_sync(self, bill_id: str) -> tuple[BillRecord, bytes] | None:
        conn = self._connect()
        try:
            r = conn.execute(
                "SELECT id, event_id, mime, size, sha256, path, created_ts FROM bills WHERE id = ?",
                (bill_id,),
            ).fetchone()
            if r is None:
                return None
            rec = BillRecord(
                id=str(r["id"]),
                event_id=str(r["event_id"]),
                mime=str(r["mime"]),
                size=int(r["size"]),
                sha256=str(r["sha256"]),
                path=str(r["path"]),
                created_ts=int(r["created_ts"]),
            )
            p = Path(rec.path)
            if not p.exists():
                return rec, b""
            enc_data = p.read_bytes()
            plain_data = self._cipher.decrypt_bytes(enc_data, aad=f"bill:{rec.id}".encode("ascii"))
            return rec, plain_data
        finally:
            conn.close()

    async def get_bill_by_id(self, bill_id: str) -> tuple[BillRecord, bytes] | None:
        return await asyncio.to_thread(self._get_bill_by_id_sync, bill_id)

    def _has_customer_consent_sync(self, customer_id: str) -> bool:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT 1 FROM events e
                WHERE e.customer_id = ?
                  AND e.type = 'CONSENT'
                  AND NOT EXISTS (
                      SELECT 1 FROM events r
                      WHERE r.type IN ('REVERSAL', 'VOID') AND r.reverses_event_id = e.event_id
                  )
                LIMIT 1
                """,
                (customer_id,),
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    async def has_customer_consent(self, customer_id: str) -> bool:
        return await asyncio.to_thread(self._has_customer_consent_sync, customer_id)

    async def record_consent(
        self, customer_id: str, session_scope: str | None = None
    ) -> LedgerEventRecord:
        inp = LedgerEventInput(
            type=LedgerEventType.CONSENT,
            customer_id=customer_id,
            amount_paise=0,
            session_scope=session_scope,
        )
        return await self.post_event(inp)

    async def record_share_logged(
        self, customer_id: str, event_id: str, channel: str, session_scope: str | None = None
    ) -> LedgerEventRecord:
        inp = LedgerEventInput(
            type=LedgerEventType.SHARE_LOGGED,
            customer_id=customer_id,
            amount_paise=0,
            note=f"channel:{channel}:event:{event_id}",
            session_scope=session_scope,
        )
        return await self.post_event(inp)

    def _get_shares_for_event_sync(
        self, conn: sqlite3.Connection, event_id: str
    ) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT e.event_id, e.ts, e.note_enc
            FROM events e
            WHERE e.type = 'SHARE_LOGGED'
              AND NOT EXISTS (
                  SELECT 1 FROM events r
                  WHERE r.type IN ('REVERSAL', 'VOID') AND r.reverses_event_id = e.event_id
              )
            ORDER BY e.ts ASC
            """
        ).fetchall()
        shares: list[dict[str, Any]] = []
        for r in rows:
            note_blob = r["note_enc"]
            if note_blob is None:
                continue
            eid = str(r["event_id"])
            try:
                note_str = self._cipher.decrypt_str(
                    bytes(note_blob), aad=f"event:{eid}:note".encode("ascii")
                )
            except Exception:
                continue
            if f":event:{event_id}" in note_str:
                parts = note_str.split(":")
                channel = parts[1] if len(parts) > 1 else "whatsapp"
                ts_ms = _normalize_ts_epoch_ms(r["ts"])
                dt_ist = epoch_ms_to_ist_datetime(ts_ms)
                shares.append({
                    "channel": channel,
                    "ts": ts_ms,
                    "ist_time": dt_ist.strftime("%I:%M %p"),
                })
        return shares

    def _get_event_by_id_sync(self, event_id: str) -> LedgerEventRecord | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            return self._decrypt_event_row(row) if row is not None else None
        finally:
            conn.close()

    async def get_event_by_id(self, event_id: str) -> LedgerEventRecord | None:
        return await asyncio.to_thread(self._get_event_by_id_sync, event_id)

    # ------------------------------------------------------------------
    # Additional helpers & Backward-compatible methods
    # ------------------------------------------------------------------
    def _total_credit_on_sync(self, effective_date: str) -> int:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(e.amount_paise), 0) AS total_credit_paise
                FROM events e
                WHERE e.type IN ('CREDIT_GIVEN', 'CREDIT')
                  AND e.effective_date = ?
                  AND NOT EXISTS (
                      SELECT 1 FROM events r
                      WHERE r.type IN ('REVERSAL', 'VOID') AND r.reverses_event_id = e.event_id
                  )
                """,
                (effective_date,),
            ).fetchone()
            return int(row["total_credit_paise"]) if row is not None else 0
        finally:
            conn.close()

    async def total_credit_on(self, effective_date: date | datetime | str) -> int:
        iso = _normalize_date_str(effective_date)
        return await asyncio.to_thread(self._total_credit_on_sync, iso)

    def _total_payment_on_sync(self, effective_date: str) -> int:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(e.amount_paise), 0) AS total_payment_paise
                FROM events e
                WHERE e.type IN ('PAYMENT_RECEIVED', 'PAYMENT')
                  AND e.effective_date = ?
                  AND NOT EXISTS (
                      SELECT 1 FROM events r
                      WHERE r.type IN ('REVERSAL', 'VOID') AND r.reverses_event_id = e.event_id
                  )
                """,
                (effective_date,),
            ).fetchone()
            return int(row["total_payment_paise"]) if row is not None else 0
        finally:
            conn.close()

    async def total_payment_on(self, effective_date: date | datetime | str) -> int:
        iso = _normalize_date_str(effective_date)
        return await asyncio.to_thread(self._total_payment_on_sync, iso)

    def _payments_list_sync(self, limit: int = 500) -> list[PaymentRecord]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT
                    e.seq,
                    e.event_id,
                    e.customer_id,
                    c.name_enc,
                    e.amount_paise,
                    e.effective_date,
                    e.ts,
                    e.note_enc
                FROM events e
                JOIN customers c ON c.id = e.customer_id
                WHERE e.type IN ('PAYMENT_RECEIVED', 'PAYMENT')
                  AND NOT EXISTS (
                      SELECT 1 FROM events r
                      WHERE r.type IN ('REVERSAL', 'VOID') AND r.reverses_event_id = e.event_id
                  )
                ORDER BY e.ts DESC, e.seq DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            results: list[PaymentRecord] = []
            for r in rows:
                cid = str(r["customer_id"])
                eid = str(r["event_id"])
                name = self._cipher.decrypt_str(
                    bytes(r["name_enc"]), aad=f"customer:{cid}:name".encode("ascii")
                )
                note = (
                    self._cipher.decrypt_str(
                        bytes(r["note_enc"]), aad=f"event:{eid}:note".encode("ascii")
                    )
                    if r["note_enc"] is not None
                    else None
                )
                ts_ms = _normalize_ts_epoch_ms(r["ts"])
                dt_ist = epoch_ms_to_ist_datetime(ts_ms)
                ev_items = self._get_items_for_event_sync(conn, eid)
                ev_bills = self._get_bills_for_event_sync(conn, eid)
                results.append(
                    PaymentRecord(
                        seq=int(r["seq"]),
                        event_id=eid,
                        customer_id=cid,
                        customer_name=name,
                        amount_paise=int(r["amount_paise"]),
                        effective_date=str(r["effective_date"]),
                        ts=ts_ms,
                        formatted_time=dt_ist.strftime("%d %b %Y, %I:%M %p"),
                        note=note,
                        items=ev_items,
                        bills=ev_bills,
                    )
                )
            return results
        finally:
            conn.close()

    async def payments_list(self, limit: int = 500) -> list[PaymentRecord]:
        return await asyncio.to_thread(self._payments_list_sync, limit)

    def _credits_list_sync(self, limit: int = 500) -> list[CreditRecord]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT
                    e.seq,
                    e.event_id,
                    e.customer_id,
                    c.name_enc,
                    e.amount_paise,
                    e.effective_date,
                    e.ts,
                    e.note_enc
                FROM events e
                JOIN customers c ON c.id = e.customer_id
                WHERE e.type IN ('CREDIT_GIVEN', 'CREDIT')
                  AND NOT EXISTS (
                      SELECT 1 FROM events r
                      WHERE r.type IN ('REVERSAL', 'VOID') AND r.reverses_event_id = e.event_id
                  )
                ORDER BY e.ts DESC, e.seq DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            results: list[CreditRecord] = []
            for r in rows:
                cid = str(r["customer_id"])
                eid = str(r["event_id"])
                name = self._cipher.decrypt_str(
                    bytes(r["name_enc"]), aad=f"customer:{cid}:name".encode("ascii")
                )
                note = (
                    self._cipher.decrypt_str(
                        bytes(r["note_enc"]), aad=f"event:{eid}:note".encode("ascii")
                    )
                    if r["note_enc"] is not None
                    else None
                )
                ts_ms = _normalize_ts_epoch_ms(r["ts"])
                dt_ist = epoch_ms_to_ist_datetime(ts_ms)
                ev_items = self._get_items_for_event_sync(conn, eid)
                ev_bills = self._get_bills_for_event_sync(conn, eid)
                results.append(
                    CreditRecord(
                        seq=int(r["seq"]),
                        event_id=eid,
                        customer_id=cid,
                        customer_name=name,
                        amount_paise=int(r["amount_paise"]),
                        effective_date=str(r["effective_date"]),
                        ts=ts_ms,
                        formatted_time=dt_ist.strftime("%d %b %Y, %I:%M %p"),
                        note=note,
                        items=ev_items,
                        bills=ev_bills,
                    )
                )
            return results
        finally:
            conn.close()

    async def credits_list(self, limit: int = 500) -> list[CreditRecord]:
        return await asyncio.to_thread(self._credits_list_sync, limit)

    def _summary_stats_sync(self) -> LedgerSummaryStats:
        conn = self._connect()
        try:
            s = self._summary_sync()
            today_local = date.today().isoformat()
            today_ist = epoch_ms_to_ist_date_str(int(time.time_ns() // 1_000_000))
            total_received_today = max(
                self._total_payment_on_sync(today_local),
                self._total_payment_on_sync(today_ist),
            )
            return LedgerSummaryStats(
                debtor_count=s.owing_count,
                total_owed_paise=s.total_owed_paise,
                total_received_today_paise=total_received_today,
            )
        finally:
            conn.close()

    async def summary_stats(self) -> LedgerSummaryStats:
        return await asyncio.to_thread(self._summary_stats_sync)

    def _last_event_sync(
        self, session_scope: str | None, *, active_only: bool
    ) -> LedgerEventRecord | None:
        conn = self._connect()
        try:
            clauses: list[str] = []
            params: list[Any] = []
            if session_scope is not None:
                clauses.append("e.session_scope = ?")
                params.append(session_scope)
            if active_only:
                clauses.append("e.type IN ('CREDIT_GIVEN', 'PAYMENT_RECEIVED', 'CREDIT', 'PAYMENT')")
                clauses.append(
                    "NOT EXISTS (SELECT 1 FROM events r WHERE r.type IN ('REVERSAL', 'VOID') AND r.reverses_event_id = e.event_id)"
                )
            where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            row = conn.execute(
                f"SELECT e.* FROM events e {where_sql} ORDER BY e.seq DESC LIMIT 1",
                tuple(params),
            ).fetchone()
            return self._decrypt_event_row(row) if row is not None else None
        finally:
            conn.close()

    async def last_event(
        self, session_scope: str | None = None, *, active_only: bool = False
    ) -> LedgerEventRecord | None:
        return await asyncio.to_thread(
            self._last_event_sync, session_scope, active_only=active_only
        )

    def _recent_events_sync(self, limit: int = 50) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT
                    e.seq,
                    e.event_id,
                    e.command_id,
                    e.type,
                    e.customer_id,
                    c.name_enc,
                    e.amount_paise,
                    e.business_date,
                    e.effective_date,
                    e.ts,
                    e.reverses_event_id,
                    e.note_enc,
                    e.session_scope
                FROM events e
                JOIN customers c ON c.id = e.customer_id
                ORDER BY e.ts DESC, e.seq DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            events: list[dict[str, Any]] = []
            for r in rows:
                cid = str(r["customer_id"])
                eid = str(r["event_id"])
                cust_name = self._cipher.decrypt_str(
                    bytes(r["name_enc"]), aad=f"customer:{cid}:name".encode("ascii")
                )
                note = (
                    self._cipher.decrypt_str(
                        bytes(r["note_enc"]), aad=f"event:{eid}:note".encode("ascii")
                    )
                    if r["note_enc"] is not None
                    else None
                )
                ts_ms = _normalize_ts_epoch_ms(r["ts"])
                dt_ist = epoch_ms_to_ist_datetime(ts_ms)
                ev_items = self._get_items_for_event_sync(conn, eid)
                ev_bills = self._get_bills_for_event_sync(conn, eid)
                ev_shares = self._get_shares_for_event_sync(conn, eid)
                amt_paise = int(r["amount_paise"])
                events.append({
                    "seq": int(r["seq"]),
                    "event_id": eid,
                    "command_id": str(r["command_id"]) if r["command_id"] is not None else None,
                    "type": str(r["type"]),
                    "customer_id": cid,
                    "customer_name": cust_name,
                    "amount_paise": amt_paise,
                    "amount_inr": amt_paise // 100,
                    "business_date": str(r["business_date"]),
                    "effective_date": str(r["effective_date"]),
                    "ts": ts_ms,
                    "ist_time": dt_ist.strftime("%d %b %Y, %I:%M %p"),
                    "reverses_event_id": str(r["reverses_event_id"]) if r["reverses_event_id"] is not None else None,
                    "voids_event_id": str(r["reverses_event_id"]) if r["reverses_event_id"] is not None else None,
                    "note": note,
                    "session_scope": str(r["session_scope"]) if r["session_scope"] is not None else None,
                    "items": [it.model_dump() for it in ev_items],
                    "bills": [b.model_dump() for b in ev_bills],
                    "share_logs": ev_shares,
                    "has_bill": len(ev_bills) > 0,
                    "item_count": len(ev_items),
                })
            return events
        finally:
            conn.close()

    async def recent_events(self, limit: int = 50) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._recent_events_sync, limit)

    def _wipe_all_sync(self) -> None:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DROP TRIGGER IF EXISTS trg_events_no_delete")
            conn.execute("DROP TRIGGER IF EXISTS trg_events_no_update")
            conn.execute("DELETE FROM entry_items")
            conn.execute("DELETE FROM bills")
            conn.execute("DELETE FROM command_audit")
            conn.execute("DELETE FROM customer_aliases")
            conn.execute("DELETE FROM events")
            conn.execute("DELETE FROM customers")
            conn.execute(
                "DELETE FROM sqlite_sequence WHERE name IN ('events', 'entry_items', 'bills')"
            )
            for stmt in _CREATE_TRIGGERS_SQL.split(";\n\n"):
                cleaned = stmt.strip()
                if cleaned:
                    conn.execute(cleaned)
            conn.execute("COMMIT")

            if self._bills_dir.exists():
                for f in self._bills_dir.glob("*.bin"):
                    try:
                        f.unlink(missing_ok=True)
                    except Exception:
                        pass
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    async def wipe_all(self) -> None:
        """Atomically wipe all ledger tables and restore append-only triggers."""
        await self._shielded_write(self._wipe_all_sync)

    def _delete_event_sync(self, event_id: str) -> bool:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            target = conn.execute(
                "SELECT event_id, customer_id, amount_paise, type FROM events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            if target is None:
                conn.execute("ROLLBACK")
                return False

            # Drop append-only triggers for the atomic deletion
            conn.execute("DROP TRIGGER IF EXISTS trg_events_no_delete")
            conn.execute("DROP TRIGGER IF EXISTS trg_events_no_update")

            # Clean up bill files on disk if any
            bill_rows = conn.execute(
                "SELECT path FROM bills WHERE event_id = ?", (event_id,)
            ).fetchall()
            for br in bill_rows:
                if br["path"]:
                    try:
                        Path(br["path"]).unlink(missing_ok=True)
                    except Exception:
                        pass

            # Clean child and referencing records
            conn.execute("DELETE FROM entry_items WHERE event_id = ?", (event_id,))
            conn.execute("DELETE FROM bills WHERE event_id = ?", (event_id,))
            conn.execute("DELETE FROM events WHERE reverses_event_id = ?", (event_id,))
            conn.execute("DELETE FROM events WHERE event_id = ?", (event_id,))

            # Re-install append-only triggers
            for stmt in _CREATE_TRIGGERS_SQL.split(";\n\n"):
                cleaned = stmt.strip()
                if cleaned:
                    conn.execute(cleaned)

            conn.execute("COMMIT")
            return True
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    async def delete_event(self, event_id: str) -> bool:
        """Directly delete a transaction event from the database."""
        return await self._shielded_write(lambda: self._delete_event_sync(event_id))

    def _export_backup_sync(self) -> bytes:
        src = self._connect()
        try:
            dest = sqlite3.connect(":memory:")
            try:
                src.backup(dest)
                return dest.serialize()
            finally:
                dest.close()
        finally:
            src.close()

    async def export_backup_bytes(self) -> bytes:
        """Create and stream a consistent snapshot of the SQLite database using SQLite's backup API."""
        return await asyncio.to_thread(self._export_backup_sync)

    def _checkpoint_sync(self) -> None:
        conn = self._connect()
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        finally:
            conn.close()

    async def checkpoint(self) -> None:
        """Checkpoint SQLite WAL file after finishing in-flight commits."""
        async with self._write_lock:
            await asyncio.to_thread(self._checkpoint_sync)
