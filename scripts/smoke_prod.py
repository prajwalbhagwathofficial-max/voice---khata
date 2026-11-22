#!/usr/bin/env python3
"""
Production Smoke Test for Voice Khata
Usage:
    python scripts/smoke_prod.py <base_url> [--verify-persistence]

Verifies:
1. GET /healthz (returns 200 {"ok":true} without touching Gnani, Groq or DB)
2. GET / (returns HTML 200)
3. GET /got (returns HTML 200 via SPA catch-all, NOT 404)
4. WebSocket wss://<host>/ws/session -> session.start -> ready
5. Post synthetic entry -> read balance -> reverse entry -> verify balance
6. Optional --verify-persistence across restarts
"""

import sys
import time
import json
import asyncio
import argparse
import httpx
import websockets

PERSISTENCE_CUSTOMER = "PersistenceCustomer"
PERSISTENCE_AMOUNT_PAISE = 75000

async def run_smoke_test(base_url: str, verify_persistence: bool = False):
    base_url = base_url.rstrip("/")
    ws_url = base_url.replace("https://", "wss://").replace("http://", "ws://") + "/ws/session"

    total_start = time.perf_counter()
    print("=" * 60)
    print(f"VOICE KHATA SMOKE TEST: {base_url}")
    print(f"Target WebSocket URL: {ws_url}")
    print(f"Verify Persistence Mode: {verify_persistence}")
    print("=" * 60)

    # ─── 1. HTTP Endpoint Tests ─────────────────────────────────────────────
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        # Test 1: GET /healthz
        t0 = time.perf_counter()
        res_health = await client.get(f"{base_url}/healthz")
        t_health = (time.perf_counter() - t0) * 1000
        if res_health.status_code != 200:
            print(f"[FAIL] GET /healthz returned status {res_health.status_code}: {res_health.text}")
            sys.exit(1)
        try:
            data = res_health.json()
            if not data.get("ok"):
                print(f"[FAIL] GET /healthz expected ok:true, got: {data}")
                sys.exit(1)
        except Exception as e:
            print(f"[FAIL] GET /healthz response is not valid JSON: {e}")
            sys.exit(1)
        print(f"[PASS] GET /healthz -> 200 {data} ({t_health:.2f} ms)")

        # Test 2: GET /
        t0 = time.perf_counter()
        res_root = await client.get(f"{base_url}/")
        t_root = (time.perf_counter() - t0) * 1000
        if res_root.status_code != 200 or "text/html" not in res_root.headers.get("content-type", ""):
            print(f"[FAIL] GET / expected 200 HTML, got status {res_root.status_code}, type {res_root.headers.get('content-type')}")
            sys.exit(1)
        print(f"[PASS] GET / -> 200 HTML ({t_root:.2f} ms)")

        # Test 3: GET /got (Must return HTML, not 404!)
        t0 = time.perf_counter()
        res_got = await client.get(f"{base_url}/got")
        t_got = (time.perf_counter() - t0) * 1000
        if res_got.status_code != 200 or "text/html" not in res_got.headers.get("content-type", ""):
            print(f"[FAIL] GET /got expected 200 HTML, got {res_got.status_code}: {res_got.text[:100]}")
            sys.exit(1)
        print(f"[PASS] GET /got -> 200 HTML (SPA catch-all verified) ({t_got:.2f} ms)")

    # ─── 2. WebSocket Session Tests (/ws/session) ───────────────────────────
    ws_conn_start = time.perf_counter()
    try:
        async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
            t_ws_conn = (time.perf_counter() - ws_conn_start) * 1000
            print(f"[PASS] Connected to WebSocket {ws_url} ({t_ws_conn:.2f} ms)")

            # Step A: send session.start, wait for ready
            t0 = time.perf_counter()
            await ws.send(json.dumps({"type": "session.start"}))
            ready_resp = json.loads(await ws.recv())
            t_ready = (time.perf_counter() - t0) * 1000
            if ready_resp.get("type") != "ready" and ready_resp.get("status") != "ready":
                print(f"[FAIL] Expected ready message from server, got: {ready_resp}")
                sys.exit(1)
            print(f"[PASS] session.start -> {ready_resp.get('type', 'ready')} ({t_ready:.2f} ms)")

            # Step B: Persistence Check
            if verify_persistence:
                t0 = time.perf_counter()
                await ws.send(json.dumps({
                    "type": "balance.get",
                    "customer_name": PERSISTENCE_CUSTOMER
                }))
                p_resp = json.loads(await ws.recv())
                t_p = (time.perf_counter() - t0) * 1000
                p_bal = p_resp.get("balance_paise", 0)
                if p_bal != PERSISTENCE_AMOUNT_PAISE:
                    print(f"[FAIL] Persistence verification failed! Expected {PERSISTENCE_AMOUNT_PAISE} paise for {PERSISTENCE_CUSTOMER}, got: {p_bal}")
                    sys.exit(1)
                print(f"[PASS] --verify-persistence CONFIRMED: {PERSISTENCE_CUSTOMER} has {p_bal} paise across restart! ({t_p:.2f} ms)")
            else:
                # Seed a persistence record for future --verify-persistence checks
                await ws.send(json.dumps({
                    "type": "entry.create",
                    "customer_name": PERSISTENCE_CUSTOMER,
                    "amount_paise": PERSISTENCE_AMOUNT_PAISE,
                    "entry_type": "gave",
                    "id": "txn_persist_check_1"
                }))
                await ws.recv()
                print(f"[INFO] Planted persistence record for {PERSISTENCE_CUSTOMER} ({PERSISTENCE_AMOUNT_PAISE} paise).")

            # Step C: Post one synthetic test entry
            smoke_txn_id = f"smoke_txn_{int(time.time() * 1000)}"
            smoke_customer = f"SmokeTestUser_{int(time.time())}"
            smoke_amt = 50000  # Rs 500 in paise

            t0 = time.perf_counter()
            await ws.send(json.dumps({
                "type": "entry.create",
                "customer_name": smoke_customer,
                "amount_paise": smoke_amt,
                "entry_type": "gave",
                "id": smoke_txn_id,
                "note": "Smoke synthetic credit test"
            }))
            create_resp = json.loads(await ws.recv())
            t_create = (time.perf_counter() - t0) * 1000

            if create_resp.get("type") != "entry.created" or create_resp.get("amount_paise") != smoke_amt:
                print(f"[FAIL] Entry creation failed: {create_resp}")
                sys.exit(1)
            print(f"[PASS] Post synthetic test entry ({smoke_amt} paise for {smoke_customer}) -> id={smoke_txn_id} ({t_create:.2f} ms)")

            # Step D: Read the balance
            t0 = time.perf_counter()
            await ws.send(json.dumps({
                "type": "balance.get",
                "customer_name": smoke_customer
            }))
            bal_resp = json.loads(await ws.recv())
            t_bal = (time.perf_counter() - t0) * 1000
            bal = bal_resp.get("balance_paise", 0)
            if bal != smoke_amt:
                print(f"[FAIL] Balance verification failed: Expected {smoke_amt}, got {bal}")
                sys.exit(1)
            print(f"[PASS] Read balance confirmed: {bal} paise (Rs {bal/100:.2f}) ({t_bal:.2f} ms)")

            # Step E: Reverse the entry
            t0 = time.perf_counter()
            await ws.send(json.dumps({
                "type": "entry.reverse",
                "id": smoke_txn_id
            }))
            rev_resp = json.loads(await ws.recv())
            t_rev = (time.perf_counter() - t0) * 1000
            if rev_resp.get("type") != "entry.reversed":
                print(f"[FAIL] Reversal failed: {rev_resp}")
                sys.exit(1)
            print(f"[PASS] Reversed entry {smoke_txn_id} ({t_rev:.2f} ms)")

            # Step F: Verify balance after reversal
            t0 = time.perf_counter()
            await ws.send(json.dumps({
                "type": "balance.get",
                "customer_name": smoke_customer
            }))
            after_rev_bal = json.loads(await ws.recv())
            t_final = (time.perf_counter() - t0) * 1000
            final_bal = after_rev_bal.get("balance_paise", 0)
            if final_bal != 0:
                print(f"[FAIL] Balance after reversal expected 0, got {final_bal}")
                sys.exit(1)
            print(f"[PASS] Balance after reversal confirmed: {final_bal} paise (Rs 0.00) ({t_final:.2f} ms)")

    except Exception as e:
        print(f"[FAIL] WebSocket test failed with error: {e}")
        sys.exit(1)

    total_time = (time.perf_counter() - total_start) * 1000
    print("=" * 60)
    print(f"ALL SMOKE TESTS PASSED SUCCESSFULLY in {total_time:.2f} ms!")
    print("=" * 60)
    sys.exit(0)

def main():
    parser = argparse.ArgumentParser(description="Voice Khata Production Smoke Test")
    parser.add_argument("base_url", nargs="?", default="http://localhost:8000", help="Base URL (e.g. http://localhost:8000)")
    parser.add_argument("--verify-persistence", action="store_true", help="Verify persistence of data written by previous run")
    args = parser.parse_args()

    asyncio.run(run_smoke_test(args.base_url, args.verify_persistence))

if __name__ == "__main__":
    main()
