"""
SQLite Database Layer for Voice Khata
- WAL mode, synchronous=NORMAL, busy_timeout=5000
- Migrations via PRAGMA user_version
- Online backup via SQLite Backup API
"""

import os
import sqlite3
import threading
import logging
import tempfile
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple
from server.config import DATA_DIR

logger = logging.getLogger("voice_khata.db")
db_lock = threading.RLock()
_conn: Optional[sqlite3.Connection] = None

def get_db_path() -> str:
    return os.path.join(DATA_DIR, "khata.db")

def verify_data_dir():
    """Verify DATA_DIR exists and is writable by the running process."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        test_file = os.path.join(DATA_DIR, ".write_test")
        with open(test_file, "w") as f:
            f.write("ok")
        os.remove(test_file)
        logger.info(f"DATA_DIR '{DATA_DIR}' verified writable.")
    except Exception as e:
        logger.critical(f"FATAL: DATA_DIR '{DATA_DIR}' is NOT writable: {e}")
        raise RuntimeError(f"DATA_DIR '{DATA_DIR}' is NOT writable: {e}")

def get_db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        init_db()
    return _conn

def init_db():
    """Open SQLite, set WAL pragmas, run migrations, seed demo data if empty."""
    global _conn
    with db_lock:
        verify_data_dir()
        db_path = get_db_path()
        conn = sqlite3.connect(db_path, check_same_thread=False, autocommit=True)
        conn.row_factory = sqlite3.Row

        # Mandatory production SQLite settings
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=5000;")

        cur = conn.cursor()
        cur.execute("PRAGMA user_version;")
        row = cur.fetchone()
        version = row[0] if row else 0

        # Migration 1: Initial tables
        if version < 1:
            logger.info("Running DB migration to user_version=1...")
            cur.execute("""
            CREATE TABLE IF NOT EXISTS customers (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                phone TEXT DEFAULT '',
                city TEXT DEFAULT 'Kengeri',
                balance_paise INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id TEXT PRIMARY KEY,
                customer_id TEXT NOT NULL,
                customer_name TEXT NOT NULL,
                type TEXT NOT NULL,
                amount_paise INTEGER NOT NULL,
                balance_after_paise INTEGER NOT NULL,
                note TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                reversed_at TEXT DEFAULT NULL,
                FOREIGN KEY (customer_id) REFERENCES customers(id)
            );
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_txn_cust ON transactions(customer_id);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_txn_created ON transactions(created_at);")
            cur.execute("PRAGMA user_version = 1;")
            logger.info("DB migration to user_version=1 complete.")

        # Seed demo data ONLY if customers table is completely empty
        cur.execute("SELECT COUNT(*) FROM customers;")
        cust_count = cur.fetchone()[0]
        if cust_count == 0:
            logger.info("Customers table is empty. Seeding initial demo customers...")
            now = datetime.now(timezone.utc).isoformat()
            demo_custs = [
                ("cust_ramesh_1", "Ramesh", "8317365720", "Kengeri", 150000, now, now),
                ("cust_suresh_2", "Suresh", "9845012345", "Rajajinagar", 80000, now, now),
                ("cust_neha_3", "Neha", "9900112233", "Jayanagar", 0, now, now),
            ]
            cur.executemany(
                "INSERT INTO customers (id, name, phone, city, balance_paise, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                demo_custs
            )
            # Insert initial transaction for Ramesh
            cur.execute(
                "INSERT INTO transactions (id, customer_id, customer_name, type, amount_paise, balance_after_paise, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("txn_seed_1", "cust_ramesh_1", "Ramesh", "gave", 150000, 150000, "Kirana opening balance", now)
            )
            logger.info("Demo data seeded successfully.")
        else:
            logger.info(f"Database already contains {cust_count} customers. Skipping seeding (data preserved).")

        _conn = conn

def export_db_bytes() -> bytes:
    """Stream a consistent online snapshot of the database using SQLite's backup API."""
    with db_lock:
        src = get_db()
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            dest = sqlite3.connect(tmp_path)
            src.backup(dest)
            dest.close()

            with open(tmp_path, "rb") as f:
                data = f.read()
            return data
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

def wipe_db():
    """Wipe all customers, transactions, and non-db files in DATA_DIR."""
    with db_lock:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM transactions;")
        cur.execute("DELETE FROM customers;")
        cur.execute("VACUUM;")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")

    # Clean non-database files in DATA_DIR
    for item in os.listdir(DATA_DIR):
        if not item.endswith(".db") and not item.endswith(".db-wal") and not item.endswith(".db-shm"):
            fpath = os.path.join(DATA_DIR, item)
            if os.path.isfile(fpath):
                try:
                    os.remove(fpath)
                except Exception:
                    pass

def checkpoint_and_close():
    """Graceful shutdown: checkpoint WAL and close DB connection."""
    global _conn
    with db_lock:
        if _conn:
            try:
                logger.info("Checkpointing WAL to master database file...")
                _conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                _conn.close()
                logger.info("SQLite database safely closed.")
            except Exception as e:
                logger.warning(f"Error closing SQLite database: {e}")
            finally:
                _conn = None

# ─── Data Access Helpers for Ledger ─────────────────────────────────────────

def find_or_create_customer(name: str, city: str = "Kengeri", phone: str = "") -> Dict[str, Any]:
    with db_lock:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM customers WHERE LOWER(TRIM(name)) = LOWER(TRIM(?)) LIMIT 1;", (name,))
        row = cur.fetchone()
        if row:
            return dict(row)
        
        now = datetime.now(timezone.utc).isoformat()
        clean_name = name.strip()
        cust_id = f"cust_{clean_name.lower().replace(' ', '_')}_{int(datetime.now().timestamp())}"
        cur.execute(
            "INSERT INTO customers (id, name, phone, city, balance_paise, created_at, updated_at) VALUES (?, ?, ?, ?, 0, ?, ?)",
            (cust_id, clean_name, phone, city, now, now)
        )
        return {
            "id": cust_id, "name": clean_name, "phone": phone, "city": city,
            "balance_paise": 0, "created_at": now, "updated_at": now
        }

def get_customer_balance(name: str) -> Optional[int]:
    with db_lock:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT balance_paise FROM customers WHERE LOWER(TRIM(name)) = LOWER(TRIM(?)) LIMIT 1;", (name,))
        row = cur.fetchone()
        return row[0] if row else None

def record_entry(customer_name: str, amount_paise: int, entry_type: str, txn_id: Optional[str] = None, note: str = "") -> Dict[str, Any]:
    with db_lock:
        conn = get_db()
        cust = find_or_create_customer(customer_name)
        curr_balance = cust["balance_paise"]

        if entry_type in ("gave", "credit", "udhaar"):
            new_balance = curr_balance + amount_paise
        else:  # got, payment, vasooli
            new_balance = max(0, curr_balance - amount_paise)

        now = datetime.now(timezone.utc).isoformat()
        if not txn_id:
            txn_id = f"txn_{int(datetime.now().timestamp() * 1000)}"

        cur = conn.cursor()
        cur.execute(
            "UPDATE customers SET balance_paise = ?, updated_at = ? WHERE id = ?;",
            (new_balance, now, cust["id"])
        )
        cur.execute(
            "INSERT INTO transactions (id, customer_id, customer_name, type, amount_paise, balance_after_paise, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (txn_id, cust["id"], cust["name"], entry_type, amount_paise, new_balance, note, now)
        )
        return {
            "id": txn_id,
            "customer_id": cust["id"],
            "customer_name": cust["name"],
            "type": entry_type,
            "amount_paise": amount_paise,
            "balance_paise": new_balance,
            "created_at": now
        }

def reverse_entry(txn_id: str) -> Optional[Dict[str, Any]]:
    with db_lock:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM transactions WHERE id = ? LIMIT 1;", (txn_id,))
        row = cur.fetchone()
        if not row:
            return None

        txn = dict(row)
        if txn.get("reversed_at"):
            # Already reversed
            cur.execute("SELECT balance_paise FROM customers WHERE id = ?;", (txn["customer_id"],))
            c_row = cur.fetchone()
            return {
                "id": txn_id,
                "customer_name": txn["customer_name"],
                "balance_paise": c_row[0] if c_row else 0,
                "already_reversed": True
            }

        now = datetime.now(timezone.utc).isoformat()
        cur.execute("SELECT balance_paise FROM customers WHERE id = ?;", (txn["customer_id"],))
        c_row = cur.fetchone()
        curr_balance = c_row[0] if c_row else 0

        # Undo arithmetic
        if txn["type"] in ("gave", "credit", "udhaar"):
            new_balance = max(0, curr_balance - txn["amount_paise"])
        else:
            new_balance = curr_balance + txn["amount_paise"]

        cur.execute("UPDATE customers SET balance_paise = ?, updated_at = ? WHERE id = ?;", (new_balance, now, txn["customer_id"]))
        cur.execute("UPDATE transactions SET reversed_at = ? WHERE id = ?;", (now, txn_id))

        return {
            "id": txn_id,
            "customer_name": txn["customer_name"],
            "balance_paise": new_balance,
            "reversed_at": now
        }

def get_all_customers() -> List[Dict[str, Any]]:
    with db_lock:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM customers ORDER BY balance_paise DESC;")
        return [dict(r) for r in cur.fetchall()]

def get_all_transactions(limit: int = 50) -> List[Dict[str, Any]]:
    with db_lock:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM transactions ORDER BY created_at DESC LIMIT ?;", (limit,))
        return [dict(r) for r in cur.fetchall()]
