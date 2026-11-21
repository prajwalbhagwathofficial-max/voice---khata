"""Multi-tenant demo workspace manager for Voice Khata.

Provides an isolated, seeded SQLite ledger per visitor/judge based on browser cookie.
Nobody sees anyone else's history, and each judge starts clean with a private workspace.
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
from pathlib import Path
from typing import Any

from server.ledger.seed import seed_demo_data
from server.ledger.store import Ledger

logger = logging.getLogger("voice_khata.workspace")

COOKIE_NAME = "vk_workspace"
_SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{6,64}$")


class WorkspaceManager:
    """Manages separate SQLite database files and Ledger instances per visitor."""

    def __init__(
        self,
        base_dir: Path,
        enc_key: str,
        default_ledger: Ledger | None = None,
        auto_seed: bool = True,
    ) -> None:
        self.workspaces_dir = base_dir / "workspaces"
        self.enc_key = enc_key
        self.default_ledger = default_ledger
        self.auto_seed = auto_seed
        self._ledgers: dict[str, Ledger] = {}
        self._lock = asyncio.Lock()
        self.workspaces_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def generate_visitor_id() -> str:
        """Generate a random visitor workspace token."""
        return f"w_{secrets.token_hex(8)}"

    @staticmethod
    def is_valid_visitor_id(visitor_id: str | None) -> bool:
        """Validate visitor token format to prevent path traversal or invalid characters."""
        if not visitor_id or not isinstance(visitor_id, str):
            return False
        return bool(_SAFE_ID_RE.match(visitor_id))

    async def get_ledger(self, visitor_id: str | None) -> Ledger:
        """Get or initialize the isolated Ledger for the given visitor_id."""
        if not self.is_valid_visitor_id(visitor_id) or visitor_id == "default":
            if self.default_ledger is not None:
                return self.default_ledger
            visitor_id = "default"

        async with self._lock:
            if visitor_id in self._ledgers:
                return self._ledgers[visitor_id]

            db_path = self.workspaces_dir / f"ledger_{visitor_id}.db"
            is_new = not db_path.exists()
            ledger = Ledger(db_path, enc_key=self.enc_key)

            if is_new and self.auto_seed:
                await seed_demo_data(ledger)
                logger.info("Initialized and seeded fresh workspace: %s", visitor_id)

            self._ledgers[visitor_id] = ledger
            return ledger

    async def reset_workspace(self, visitor_id: str) -> Ledger:
        """Reset and reseed an existing workspace."""
        if not self.is_valid_visitor_id(visitor_id):
            raise ValueError(f"Invalid visitor ID: {visitor_id}")

        async with self._lock:
            db_path = self.workspaces_dir / f"ledger_{visitor_id}.db"
            if visitor_id in self._ledgers:
                ledger = self._ledgers[visitor_id]
                await ledger.wipe_all()
                if self.auto_seed:
                    await seed_demo_data(ledger)
                return ledger

            ledger = Ledger(db_path, enc_key=self.enc_key)
            await ledger.wipe_all()
            if self.auto_seed:
                await seed_demo_data(ledger)
            self._ledgers[visitor_id] = ledger
            return ledger

    async def find_event_across_workspaces(
        self, event_id: str, preferred_visitor_id: str | None = None
    ) -> tuple[Ledger, Any] | None:
        """Find an event across workspaces, checking preferred workspace first."""
        if self.is_valid_visitor_id(preferred_visitor_id):
            pref_ledger = await self.get_ledger(preferred_visitor_id)
            ev = await pref_ledger.get_event_by_id(event_id)
            if ev is not None:
                return pref_ledger, ev

        if self.default_ledger is not None:
            ev = await self.default_ledger.get_event_by_id(event_id)
            if ev is not None:
                return self.default_ledger, ev

        # Check currently active cached ledgers
        for vid, l in list(self._ledgers.items()):
            if vid == preferred_visitor_id:
                continue
            ev = await l.get_event_by_id(event_id)
            if ev is not None:
                return l, ev

        # Check existing db files on disk
        for p in self.workspaces_dir.glob("ledger_*.db"):
            vid = p.stem.replace("ledger_", "")
            if vid in self._ledgers or vid == preferred_visitor_id:
                continue
            l = await self.get_ledger(vid)
            ev = await l.get_event_by_id(event_id)
            if ev is not None:
                return l, ev

        return None

    async def find_bill_across_workspaces(
        self, event_id: str, bill_id: str, preferred_visitor_id: str | None = None
    ) -> tuple[Ledger, Any, bytes] | None:
        """Find a bill across workspaces."""
        if self.is_valid_visitor_id(preferred_visitor_id):
            pref_ledger = await self.get_ledger(preferred_visitor_id)
            res = await pref_ledger.get_bill_by_id(bill_id)
            if res is not None:
                record, data = res
                if record.event_id == event_id:
                    return pref_ledger, record, data

        if self.default_ledger is not None:
            res = await self.default_ledger.get_bill_by_id(bill_id)
            if res is not None:
                record, data = res
                if record.event_id == event_id:
                    return self.default_ledger, record, data

        for vid, l in list(self._ledgers.items()):
            if vid == preferred_visitor_id:
                continue
            res = await l.get_bill_by_id(bill_id)
            if res is not None:
                record, data = res
                if record.event_id == event_id:
                    return l, record, data

        for p in self.workspaces_dir.glob("ledger_*.db"):
            vid = p.stem.replace("ledger_", "")
            if vid in self._ledgers or vid == preferred_visitor_id:
                continue
            l = await self.get_ledger(vid)
            res = await l.get_bill_by_id(bill_id)
            if res is not None:
                record, data = res
                if record.event_id == event_id:
                    return l, record, data

        return None

    async def checkpoint_all(self) -> None:
        """Checkpoint WAL on all active workspace ledgers."""
        async with self._lock:
            for vid, l in list(self._ledgers.items()):
                try:
                    await l.checkpoint()
                except Exception as exc:
                    logger.warning("Failed to checkpoint workspace %s: %s", vid, exc)
