"""
Unit and Integration Tests for FastAPI Render Deployment Server
"""

import os
import pytest
from fastapi.testclient import TestClient

# Configure environment for tests
os.environ["DATA_DIR"] = "./test_unit_data"
os.environ["USE_MOCKS"] = "true"
os.environ["ADMIN_TOKEN"] = "test_admin_token_xyz"
os.environ["DB_ENCRYPTION_KEY"] = "super_secret_test_key_123"

from server.main import app
from server.config import get_key_fingerprint, validate_config
from server.db import init_db, wipe_db, get_db

@pytest.fixture(scope="module", autouse=True)
def setup_teardown():
    init_db()
    yield
    wipe_db()
    if os.path.exists("./test_unit_data"):
        import shutil
        shutil.rmtree("./test_unit_data", ignore_errors=True)

def test_healthz():
    client = TestClient(app)
    res = client.get("/healthz")
    assert res.status_code == 200
    assert res.json() == {"ok": True}

def test_security_headers():
    client = TestClient(app)
    res = client.get("/healthz")
    assert res.status_code == 200
    assert "Content-Security-Policy" in res.headers
    assert res.headers["Permissions-Policy"] == "microphone=(self)"
    assert res.headers["X-Content-Type-Options"] == "nosniff"

def test_got_catchall_route():
    client = TestClient(app)
    res = client.get("/got")
    assert res.status_code == 200
    assert "text/html" in res.headers.get("content-type", "")

def test_gave_catchall_route():
    client = TestClient(app)
    res = client.get("/gave")
    assert res.status_code == 200
    assert "text/html" in res.headers.get("content-type", "")

def test_admin_wipe_unauthorized():
    client = TestClient(app)
    res = client.post("/admin/wipe")
    assert res.status_code == 401

def test_admin_export_unauthorized():
    client = TestClient(app)
    res = client.get("/admin/export")
    assert res.status_code == 401

def test_admin_export_authorized():
    client = TestClient(app)
    res = client.get("/admin/export", headers={"Authorization": "Bearer test_admin_token_xyz"})
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/octet-stream"
    assert len(res.content) > 0

def test_admin_wipe_authorized():
    client = TestClient(app)
    res = client.post("/admin/wipe", headers={"Authorization": "Bearer test_admin_token_xyz"})
    assert res.status_code == 200
    assert res.json().get("ok") is True

def test_key_fingerprint():
    fp = get_key_fingerprint("my_secret_key")
    assert len(fp) == 8
    assert fp != "my_secret_key"
    assert get_key_fingerprint("") == "none"

def test_fail_fast_on_missing_secret():
    # If USE_MOCKS is false and secret missing, must raise RuntimeError
    import server.config as cfg
    old_mocks = cfg.USE_MOCKS
    old_key = cfg.GNANI_API_KEY
    try:
        cfg.USE_MOCKS = False
        cfg.GNANI_API_KEY = ""
        with pytest.raises(RuntimeError, match="Missing required secrets"):
            validate_config()
    finally:
        cfg.USE_MOCKS = old_mocks
        cfg.GNANI_API_KEY = old_key
