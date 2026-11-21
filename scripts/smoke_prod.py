"""Production smoke test script for single-origin Voice Khata service.

Verifies:
  1. GET /healthz (200 {"ok": true}, security headers)
  2. GET / (200 HTML with Voice Khata shell)
  3. GET /got (200 HTML SPA route fallback)
  4. wss://<host>/ws/session (session.start -> ready handshake)
  5. Deterministic transaction commit, balance query, reversal, and balance check.
  6. Persistence verification across container restarts via --verify-persistence.

Usage:
  python scripts/smoke_prod.py http://localhost:8000
  python scripts/smoke_prod.py http://localhost:8000 --verify-persistence
  python scripts/smoke_prod.py https://voice-khata.onrender.com
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from urllib.parse import urlparse

try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import httpx
import websockets

PERSISTENCE_CUSTOMER_NAME = "PersistAlphaCust"
PERSISTENCE_AMOUNT_PAISE = 75000  # 750 INR


def _unique_customer_name(prefix: str = "SmokeCust") -> str:
    import uuid
    letters = "".join(c for c in uuid.uuid4().hex if c.isalpha())[:6].capitalize()
    return f"{prefix}{letters}"


def _to_ws_url(base_url: str) -> str:
    parsed = urlparse(base_url.rstrip("/"))
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return f"{scheme}://{parsed.netloc}/ws/session"


async def _recv_json_message(ws: Any, target_types: set[str], timeout: float = 10.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rem = max(0.1, deadline - time.monotonic())
        try:
            packet = await asyncio.wait_for(ws.recv(), timeout=rem)
        except asyncio.TimeoutError:
            break
        if isinstance(packet, str):
            try:
                data = json.loads(packet)
                if data.get("t") in target_types:
                    return data
            except json.JSONDecodeError:
                pass
    raise TimeoutError(f"Timed out waiting for message types: {target_types}")


async def run_smoke(base_url: str, verify_persistence: bool = False) -> None:
    base = base_url.rstrip("/")
    print(f"=== Voice Khata Smoke Test: {base} ===")
    total_t0 = time.monotonic()

    # 1. HTTP Endpoint checks
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        # GET /healthz
        t0 = time.monotonic()
        r_hz = await client.get(f"{base}/healthz")
        hz_ms = (time.monotonic() - t0) * 1000.0
        if r_hz.status_code != 200 or r_hz.json() != {"ok": True}:
            raise RuntimeError(f"GET /healthz failed: status={r_hz.status_code}, body={r_hz.text!r}")
        # Verify required security headers
        for hdr in ("permissions-policy", "x-content-type-options", "content-security-policy"):
            if hdr not in r_hz.headers:
                raise RuntimeError(f"Missing required security header: {hdr}")
        print(f"[PASS] GET /healthz -> 200 {r_hz.json()} ({hz_ms:.1f} ms)")

        # GET /
        t1 = time.monotonic()
        r_root = await client.get(f"{base}/")
        root_ms = (time.monotonic() - t1) * 1000.0
        ct_root = r_root.headers.get("content-type", "")
        if r_root.status_code != 200 or "html" not in ct_root.lower() or "<html" not in r_root.text.lower():
            raise RuntimeError(f"GET / failed: status={r_root.status_code}, content-type={ct_root!r}")
        print(f"[PASS] GET / -> 200 HTML ({len(r_root.text)} bytes, {root_ms:.1f} ms)")

        # GET /got (verify client-side catch-all SPA routing)
        t2 = time.monotonic()
        r_got = await client.get(f"{base}/got")
        got_ms = (time.monotonic() - t2) * 1000.0
        ct_got = r_got.headers.get("content-type", "")
        if r_got.status_code != 200 or "html" not in ct_got.lower() or "<html" not in r_got.text.lower():
            raise RuntimeError(f"GET /got failed: status={r_got.status_code}, content-type={ct_got!r}")
        print(f"[PASS] GET /got -> 200 HTML catch-all ({len(r_got.text)} bytes, {got_ms:.1f} ms)")

    # 2. WebSocket Protocol & Transaction Checks
    ws_url = _to_ws_url(base)
    t_ws = time.monotonic()
    async with websockets.connect(ws_url, open_timeout=15) as ws:
        ws_connect_ms = (time.monotonic() - t_ws) * 1000.0
        print(f"[PASS] WS connect to {ws_url} ({ws_connect_ms:.1f} ms)")

        # Handshake: session.start -> ready
        t_hs = time.monotonic()
        await ws.send(json.dumps({"t": "session.start", "lang": "en-IN"}))
        ready_msg = await _recv_json_message(ws, {"ready"}, timeout=10.0)
        hs_ms = (time.monotonic() - t_hs) * 1000.0
        print(f"[PASS] WS session ready: session_id={ready_msg.get('session_id')} ({hs_ms:.1f} ms)")

        if verify_persistence:
            # Verification mode: check that persistent customer from previous run survived restart
            print(f"[*] Verifying persistence for customer: {PERSISTENCE_CUSTOMER_NAME!r}")
            t_persist = time.monotonic()
            await ws.send(json.dumps({"t": "customer.create", "name": PERSISTENCE_CUSTOMER_NAME}))
            c_res = await _recv_json_message(ws, {"customer.create.result"}, timeout=10.0)
            if c_res.get("status") != "success" or not c_res.get("customer_id"):
                raise RuntimeError(f"Failed to find persistent customer: {c_res}")
            cid = c_res["customer_id"]

            await ws.send(json.dumps({"t": "customer.query", "id": cid}))
            q_res = await _recv_json_message(ws, {"customer.result"}, timeout=10.0)
            bal_paise = q_res.get("balance_paise", q_res.get("balance", 0) * 100)
            if bal_paise != PERSISTENCE_AMOUNT_PAISE:
                raise RuntimeError(
                    f"Persistence check failed! Expected {PERSISTENCE_AMOUNT_PAISE} paise, got {bal_paise} paise"
                )
            persist_ms = (time.monotonic() - t_persist) * 1000.0
            print(
                f"[PASS] Persistence verified: {PERSISTENCE_CUSTOMER_NAME} has INR {bal_paise // 100} ({persist_ms:.1f} ms)"
            )
        else:
            # Normal smoke cycle: commit entry -> read balance -> reverse -> verify balance
            cust_name = _unique_customer_name()
            t_entry = time.monotonic()
            await ws.send(
                json.dumps({
                    "t": "customer.create",
                    "name": cust_name,
                    "phone": "9999900000",
                    "initial_credit_paise": 50000,  # 50000 paise = 500 INR
                    "note": "Automated smoke entry",
                })
            )
            create_res = await _recv_json_message(ws, {"customer.create.result"}, timeout=10.0)
            if create_res.get("status") != "success" or not create_res.get("event_id"):
                raise RuntimeError(f"customer.create failed: {create_res}")
            cid = create_res["customer_id"]
            eid = create_res["event_id"]
            entry_ms = (time.monotonic() - t_entry) * 1000.0
            print(f"[PASS] Created customer {cust_name} with INR 500 credit (event_id={eid}, {entry_ms:.1f} ms)")

            # Query balance -> assert 50000 paise (500 INR)
            t_query = time.monotonic()
            await ws.send(json.dumps({"t": "customer.query", "id": cid}))
            q_res1 = await _recv_json_message(ws, {"customer.result"}, timeout=10.0)
            bal1 = q_res1.get("balance_paise", q_res1.get("balance", 0) * 100)
            if bal1 != 50000:
                raise RuntimeError(f"Expected balance 50000 paise, got {bal1}")
            query_ms = (time.monotonic() - t_query) * 1000.0
            print(f"[PASS] Verified balance = INR {bal1 // 100} ({query_ms:.1f} ms)")

            # Reverse the entry -> assert reversal succeeded
            t_rev = time.monotonic()
            await ws.send(
                json.dumps({
                    "t": "event.reverse",
                    "event_id": eid,
                    "reason": "Automated smoke test reversal",
                })
            )
            rev_res = await _recv_json_message(ws, {"event.reverse.result"}, timeout=10.0)
            if rev_res.get("status") != "success":
                raise RuntimeError(f"event.reverse failed: {rev_res}")
            rev_ms = (time.monotonic() - t_rev) * 1000.0
            print(f"[PASS] Appended REVERSAL event for {eid} ({rev_ms:.1f} ms)")

            # Query balance again -> assert 0 paise (0 INR)
            t_q2 = time.monotonic()
            await ws.send(json.dumps({"t": "customer.query", "id": cid}))
            q_res2 = await _recv_json_message(ws, {"customer.result"}, timeout=10.0)
            bal2 = q_res2.get("balance_paise", q_res2.get("balance", -1) * 100)
            if bal2 != 0:
                raise RuntimeError(f"Expected balance 0 paise after reversal, got {bal2}")
            q2_ms = (time.monotonic() - t_q2) * 1000.0
            print(f"[PASS] Verified balance after reversal = INR 0 ({q2_ms:.1f} ms)")

            # Setup persistent customer for subsequent --verify-persistence run
            await ws.send(
                json.dumps({
                    "t": "customer.create",
                    "name": PERSISTENCE_CUSTOMER_NAME,
                })
            )
            p_res = await _recv_json_message(ws, {"customer.create.result"}, timeout=10.0)
            if p_res.get("status") != "success":
                raise RuntimeError(f"Failed to find or create persistent customer: {p_res}")
            pid = p_res["customer_id"]

            await ws.send(json.dumps({"t": "customer.query", "id": pid}))
            p_bal_res = await _recv_json_message(ws, {"customer.result"}, timeout=10.0)
            cur_bal = p_bal_res.get("balance_paise", p_bal_res.get("balance", 0) * 100)
            if cur_bal != PERSISTENCE_AMOUNT_PAISE:
                diff = PERSISTENCE_AMOUNT_PAISE - cur_bal
                await ws.send(
                    json.dumps({
                        "t": "customer.create",
                        "name": PERSISTENCE_CUSTOMER_NAME,
                        "initial_credit_paise": diff,
                        "note": "Seed for persistence verification across restart",
                    })
                )
                await _recv_json_message(ws, {"customer.create.result"}, timeout=10.0)
            print(f"[PASS] Seeded persistent customer with INR {PERSISTENCE_AMOUNT_PAISE // 100} for restart verification")

    total_ms = (time.monotonic() - total_t0) * 1000.0
    print(f"=== ALL CHECKS PASSED in {total_ms:.1f} ms ===")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Voice Khata Production Smoke Test")
    parser.add_argument("base_url", help="Base URL of service, e.g. http://localhost:8000")
    parser.add_argument(
        "--verify-persistence",
        action="store_true",
        help="Verify entry created in prior run exists across container restarts",
    )
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    try:
        asyncio.run(run_smoke(args.base_url, verify_persistence=args.verify_persistence))
    except Exception as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
