"""
In-Process Session Manager for Voice Khata (/ws/session)
Single-worker stateful session handling with deterministic financial arithmetic
"""

import json
import logging
import asyncio
from typing import Set, Dict, Any, Optional
from fastapi import WebSocket, WebSocketDisconnect
from server.db import record_entry, get_customer_balance, reverse_entry, find_or_create_customer

logger = logging.getLogger("voice_khata.session")

class SessionManager:
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        async with self._lock:
            self.active_connections.add(ws)
        logger.info(f"WebSocket client connected. Active sessions: {len(self.active_connections)}")

    async def disconnect(self, ws: WebSocket):
        async with self._lock:
            self.active_connections.discard(ws)
        logger.info(f"WebSocket client disconnected. Remaining: {len(self.active_connections)}")

    async def close_all(self):
        """Gracefully close all active WebSockets on SIGTERM / application shutdown."""
        async with self._lock:
            for ws in list(self.active_connections):
                try:
                    await ws.close(code=1001, reason="Server shutting down")
                except Exception:
                    pass
            self.active_connections.clear()
        logger.info("All WebSocket sessions closed.")

session_manager = SessionManager()

async def handle_ws_session(websocket: WebSocket):
    await session_manager.connect(websocket)
    session_data: Dict[str, Any] = {"initialized": False}

    try:
        while True:
            text = await websocket.receive_text()
            try:
                # Parse JSON or handle raw text
                msg = json.loads(text) if text.strip().startswith("{") else {"type": text.strip()}
            except Exception:
                msg = {"type": text.strip()}

            msg_type = msg.get("type") or msg.get("action") or ""

            # 1. session.start -> respond with "ready"
            if msg_type == "session.start" or text.strip() == "session.start":
                session_data["initialized"] = True
                await websocket.send_json({
                    "type": "ready",
                    "status": "ready",
                    "ok": True,
                    "message": "Session initialized and ready"
                })
                continue

            # 2. entry.create / synthetic test entry
            if msg_type in ("entry.create", "create_entry", "post_entry"):
                cust_name = msg.get("customer_name") or msg.get("customer") or "TestCustomer"
                amt = int(msg.get("amount_paise") or (float(msg.get("amount") or 0) * 100))
                etype = msg.get("entry_type") or msg.get("type_code") or "gave"
                txn_id = msg.get("id") or msg.get("entry_id")
                note = msg.get("note", "Synthetic smoke entry")

                res = record_entry(customer_name=cust_name, amount_paise=amt, entry_type=etype, txn_id=txn_id, note=note)
                await websocket.send_json({
                    "type": "entry.created",
                    "status": "created",
                    "id": res["id"],
                    "customer_name": res["customer_name"],
                    "amount_paise": res["amount_paise"],
                    "balance_paise": res["balance_paise"]
                })
                continue

            # 3. balance.get / read balance
            if msg_type in ("balance.get", "get_balance", "read_balance"):
                cust_name = msg.get("customer_name") or msg.get("customer") or ""
                bal = get_customer_balance(cust_name)
                if bal is None:
                    # Check if customer exists or create with 0
                    cust = find_or_create_customer(cust_name)
                    bal = cust["balance_paise"]

                await websocket.send_json({
                    "type": "balance",
                    "status": "ok",
                    "customer_name": cust_name,
                    "balance_paise": bal
                })
                continue

            # 4. entry.reverse / reverse entry
            if msg_type in ("entry.reverse", "reverse_entry", "undo_entry"):
                txn_id = msg.get("id") or msg.get("entry_id")
                if not txn_id:
                    await websocket.send_json({"type": "error", "message": "Missing entry id to reverse"})
                    continue

                res = reverse_entry(txn_id)
                if not res:
                    await websocket.send_json({"type": "error", "message": f"Entry {txn_id} not found"})
                else:
                    await websocket.send_json({
                        "type": "entry.reversed",
                        "status": "reversed",
                        "id": res["id"],
                        "customer_name": res["customer_name"],
                        "balance_paise": res["balance_paise"]
                    })
                continue

            # 5. verify-persistence
            if msg_type in ("persistence.verify", "verify_persistence"):
                cust_name = msg.get("customer_name") or "PersistenceCustomer"
                bal = get_customer_balance(cust_name)
                await websocket.send_json({
                    "type": "persistence.verified",
                    "customer_name": cust_name,
                    "exists": bal is not None,
                    "balance_paise": bal if bal is not None else 0
                })
                continue

            # 6. Default ping/keepalive response
            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})
                continue

            # Unhandled message
            await websocket.send_json({"type": "ack", "received": msg_type})

    except WebSocketDisconnect:
        await session_manager.disconnect(websocket)
    except Exception as e:
        logger.warning(f"WebSocket session error: {e}")
        await session_manager.disconnect(websocket)
