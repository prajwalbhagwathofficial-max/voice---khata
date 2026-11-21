"""Tests for multi-tenant private demo workspace per visitor."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from server.app import create_server_app
from server.config import ServerSettings
from server.ledger import COOKIE_NAME, WorkspaceManager, generate_key_b64
from server.ledger.models import LedgerEventInput, LedgerEventType


def test_visitor_id_validation():
    assert WorkspaceManager.is_valid_visitor_id("w_1234567890abcdef")
    assert WorkspaceManager.is_valid_visitor_id("w_demo_judge_1")
    assert not WorkspaceManager.is_valid_visitor_id("../../../etc/passwd")
    assert not WorkspaceManager.is_valid_visitor_id("w/invalid")
    assert not WorkspaceManager.is_valid_visitor_id("w;select*from")
    assert not WorkspaceManager.is_valid_visitor_id("")
    assert not WorkspaceManager.is_valid_visitor_id(None)


@pytest.mark.asyncio
async def test_isolated_seeded_workspaces(tmp_path: Path):
    enc_key = generate_key_b64()
    mgr = WorkspaceManager(base_dir=tmp_path, enc_key=enc_key, auto_seed=True)

    # 1. Two judges get separate ledgers
    ledger_a = await mgr.get_ledger("w_judge_a")
    ledger_b = await mgr.get_ledger("w_judge_b")

    assert ledger_a.db_path != ledger_b.db_path

    # Both start seeded with demo customers
    matches_a = await ledger_a.customers_matching("Ramesh")
    matches_b = await ledger_b.customers_matching("Ramesh")
    assert len(matches_a) > 0
    assert len(matches_b) > 0
    cust_a = matches_a[0]
    cust_b = matches_b[0]

    bal_a_before = await ledger_a.balance(cust_a.customer_id)
    bal_b_before = await ledger_b.balance(cust_b.customer_id)
    assert bal_a_before == bal_b_before

    # 2. Judge A gives udhar of Rs 500 (50000 paise) to Ramesh
    ev_a = await ledger_a.post_event(
        LedgerEventInput(
            type=LedgerEventType.CREDIT_GIVEN,
            customer_id=cust_a.customer_id,
            amount_paise=50000,
            note="Judge A milk purchase",
        )
    )

    bal_a_after = await ledger_a.balance(cust_a.customer_id)
    bal_b_after = await ledger_b.balance(cust_b.customer_id)

    # Judge A's balance changed, Judge B's remains pristine
    assert bal_a_after == bal_a_before + 50000
    assert bal_b_after == bal_b_before

    # 3. Finding event across workspaces finds Judge A's transaction
    found = await mgr.find_event_across_workspaces(ev_a.event_id, preferred_visitor_id="w_judge_b")
    assert found is not None
    found_ledger, found_ev = found
    assert found_ev.event_id == ev_a.event_id
    assert found_ledger.db_path == ledger_a.db_path

    # 4. Resetting workspace A restores clean state
    await mgr.reset_workspace("w_judge_a")
    bal_a_reset = await ledger_a.balance(cust_a.customer_id)
    assert bal_a_reset == bal_a_before

    await mgr.checkpoint_all()


def test_app_workspace_cookie_and_endpoints(tmp_path: Path):
    cfg = ServerSettings(
        data_dir=tmp_path,
        use_mocks=True,
    )
    app = create_server_app(settings=cfg, auto_seed=True)

    with TestClient(app) as client:
        # 1. Visiting root sets a vk_workspace cookie
        res = client.get("/")
        assert res.status_code == 200
        assert COOKIE_NAME in res.cookies
        cookie_val = res.cookies[COOKIE_NAME]
        assert WorkspaceManager.is_valid_visitor_id(cookie_val)

        # 2. /api/workspace reports current visitor ID
        res_ws = client.get("/api/workspace")
        assert res_ws.status_code == 200
        data = res_ws.json()
        assert data["workspace_id"] == cookie_val
        assert data["cookie_name"] == COOKIE_NAME

        # 3. Explicit query parameter sets workspace cookie
        res_custom = client.get("/?workspace=w_judge_special")
        assert res_custom.cookies.get(COOKIE_NAME) == "w_judge_special"

        # 4. /demo/reset redirects and issues a new random cookie
        res_reset = client.get("/demo/reset", follow_redirects=False)
        assert res_reset.status_code == 303
        assert COOKIE_NAME in res_reset.cookies
        new_cookie = res_reset.cookies[COOKIE_NAME]
        assert new_cookie != cookie_val
        assert WorkspaceManager.is_valid_visitor_id(new_cookie)


def test_websocket_workspace_isolation(tmp_path: Path):
    cfg = ServerSettings(
        data_dir=tmp_path,
        use_mocks=True,
    )
    app = create_server_app(settings=cfg, auto_seed=True)

    with TestClient(app) as client_a, TestClient(app) as client_b:
        client_a.cookies.set(COOKIE_NAME, "w_visitor_alpha")
        client_b.cookies.set(COOKIE_NAME, "w_visitor_beta")

        # Judge Alpha opens websocket session
        with client_a.websocket_connect("/ws/session") as ws_a:
            ws_a.send_text(json.dumps({"t": "session.start", "lang": "hi-IN"}))
            ready = ws_a.receive_json()
            assert ready["t"] == "ready"

            snapshot = ws_a.receive_json()
            assert snapshot["t"] == "snapshot"
            total_owed_alpha = snapshot["summary"]["total_owed_paise"]

            # Drain greeting audio
            while True:
                pkt = ws_a.receive()
                if pkt.get("text") is not None and json.loads(pkt["text"]).get("t") == "tts.end":
                    break

            # Judge Alpha injects a transaction: Gave Ramesh Kumar 500
            ws_a.send_text(
                json.dumps({
                    "t": "ptt.down",
                    "mode": "gave",
                    "clip_id": "clip_alpha_1",
                })
            )
            ws_a.send_text(
                json.dumps({
                    "t": "mock.inject",
                    "text": "Ramesh Kumar 500",
                    "mode": "gave",
                })
            )

            # Wait for action committed and updated summary
            while True:
                pkt = ws_a.receive()
                if pkt.get("text") is not None:
                    m = json.loads(pkt["text"])
                    if m.get("t") == "summary":
                        assert m["total_owed_paise"] == total_owed_alpha + 50000
                        break

        # Now Judge Beta connects in a separate session
        with client_b.websocket_connect("/ws/session") as ws_b:
            ws_b.send_text(json.dumps({"t": "session.start", "lang": "hi-IN"}))
            ready_b = ws_b.receive_json()
            assert ready_b["t"] == "ready"

            snapshot_b = ws_b.receive_json()
            assert snapshot_b["t"] == "snapshot"
            total_owed_beta = snapshot_b["summary"]["total_owed_paise"]

            # Judge Beta's workspace total owed is untouched!
            assert total_owed_beta == total_owed_alpha
