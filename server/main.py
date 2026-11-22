"""
Voice Khata - Single-Process Unified Render Web Service
FastAPI Backend + Built Client Static Delivery + SQLite Persistent Ledger
"""

import os
import mimetypes
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request, Response, HTTPException, Depends, Header, WebSocket
from fastapi.responses import JSONResponse, FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from server.config import (
    validate_config,
    DATA_DIR,
    ADMIN_TOKEN,
    USE_MOCKS,
    GNANI_API_KEY,
    GROQ_API_KEY,
    DB_ENCRYPTION_KEY,
    STT_MODE,
    get_key_fingerprint,
)
from server.db import (
    init_db,
    verify_data_dir,
    wipe_db,
    export_db_bytes,
    checkpoint_and_close,
    get_all_customers,
    get_all_transactions,
)
from server.session import handle_ws_session, session_manager

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("voice_khata.main")

# Ensure .js and .mjs are served as application/javascript
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("application/javascript", ".mjs")

# Find static files directory:
# In Docker: /app/static
# In Local Dev: ../client/dist or ../client
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.getenv("STATIC_DIR", "")
if not STATIC_DIR or not os.path.isdir(STATIC_DIR):
    candidates = [
        "/app/static",
        os.path.join(BASE_DIR, "static"),
        os.path.join(BASE_DIR, "client", "dist"),
        os.path.join(BASE_DIR, "client"),
    ]
    for c in candidates:
        if os.path.isdir(c):
            STATIC_DIR = c
            break

logger.info(f"Using STATIC_DIR: {STATIC_DIR}")

# ─── Lifespan (Startup & Graceful Shutdown) ──────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Boot validation (Fail fast if missing secrets)
    logger.info("Initializing Voice Khata Server...")
    validate_config()

    # 2. Verify DATA_DIR and open SQLite with WAL
    verify_data_dir()
    init_db()
    logger.info(f"Server ready. DATA_DIR={DATA_DIR}, DB Key Fingerprint={get_key_fingerprint(DB_ENCRYPTION_KEY)}")

    yield

    # 3. Graceful Shutdown on SIGTERM
    logger.info("SIGTERM received. Starting graceful shutdown...")
    await session_manager.close_all()
    checkpoint_and_close()
    logger.info("Graceful shutdown finished.")

# Create FastAPI application
app = FastAPI(title="Voice Khata", version="1.0.0", lifespan=lifespan)

# ─── Mandatory Security Headers Middleware ──────────────────────────────────
@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)

    # Content-Security-Policy (self with necessary inline/blob/audio worker support)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob: wss: ws: https:; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' blob:; "
        "worker-src 'self' blob:; "
        "media-src 'self' blob: data:; "
        "connect-src 'self' wss: ws: https:; "
        "style-src 'self' 'unsafe-inline' https:; "
        "img-src 'self' data: blob: https:;"
    )
    # Permissions-Policy: microphone access for PWA speech input
    response.headers["Permissions-Policy"] = "microphone=(self)"
    # X-Content-Type-Options: prevent MIME type sniffing
    response.headers["X-Content-Type-Options"] = "nosniff"

    # Enforce application/javascript MIME type for .js/.mjs files
    path = request.url.path
    if path.endswith(".js") or path.endswith(".mjs"):
        response.headers["Content-Type"] = "application/javascript; charset=utf-8"

    return response

# ─── 1. ROUTE: /ws/session (WebSocket) ──────────────────────────────────────
@app.websocket("/ws/session")
async def websocket_session_endpoint(websocket: WebSocket):
    await handle_ws_session(websocket)

# ─── 2. ROUTE: /healthz (Pure Health Check) ─────────────────────────────────
# Non-negotiable requirement: must NOT touch Gnani, Groq or the DB!
@app.get("/healthz")
async def healthz():
    return {"ok": True}

# Also support /api/health for frontend status badge
@app.get("/api/health")
async def api_health():
    return {"status": "ok", "ok": True}

# ─── 3. ROUTE: /admin/* (Administrative Management) ─────────────────────────
def verify_admin_auth(authorization: Optional[str] = Header(None)):
    """Enforce Bearer ADMIN_TOKEN authentication."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid Authorization header format. Expected 'Bearer <ADMIN_TOKEN>'")
    token = parts[1].strip()
    if token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid or unauthorized admin token")
    return True

@app.post("/admin/wipe")
async def admin_wipe(_: bool = Depends(verify_admin_auth)):
    """Deletes all data and files in DATA_DIR (Bearer ADMIN_TOKEN)."""
    logger.warning("Admin wipe requested. Wiping all transactions, customers, and data files.")
    wipe_db()
    return {"ok": True, "message": "All data and records successfully wiped."}

@app.get("/admin/export")
async def admin_export(_: bool = Depends(verify_admin_auth)):
    """Streams a consistent snapshot of the database using SQLite's backup API."""
    logger.info("Admin export requested. Generating consistent SQLite backup stream.")
    db_bytes = export_db_bytes()
    return Response(
        content=db_bytes,
        media_type="application/octet-stream",
        headers={"Content-Disposition": "attachment; filename=khata_backup.db"}
    )

# ─── Complementary API Routes for PWA Client Execution ──────────────────────
@app.get("/api/settings")
async def api_settings():
    return {
        "shop": {"name": "Mera Kirana Store", "city": "Kengeri"},
        "limits": {"max_credit_amount": 50000},
        "voice": {"preferred_language": "hi", "stt_mode": STT_MODE},
        "use_mocks": USE_MOCKS,
    }

@app.get("/api/settings/keys-status")
async def api_keys_status():
    mask = lambda k: (k[:4] + "..." + k[-4:]) if (k and len(k) > 8) else ("****" if k else None)
    return {
        "groq": {"configured": bool(GROQ_API_KEY), "masked": mask(GROQ_API_KEY)},
        "gnani": {"configured": bool(GNANI_API_KEY), "masked": mask(GNANI_API_KEY)},
        "db": {"configured": bool(DB_ENCRYPTION_KEY), "fingerprint": get_key_fingerprint(DB_ENCRYPTION_KEY)},
    }

@app.get("/api/customers")
async def api_customers():
    return get_all_customers()

@app.get("/api/transactions")
async def api_transactions():
    return get_all_transactions()

# ─── 4. ASSETS MOUNT & STATIC CATCH-ALL ROUTE ───────────────────────────────
# Mount /assets if the directory exists inside STATIC_DIR
assets_dir = os.path.join(STATIC_DIR, "assets")
if os.path.isdir(assets_dir):
    app.mount("/assets", StaticFiles(directory=assets_dir, html=False), name="assets")

@app.get("/{full_path:path}")
async def serve_spa_and_static_files(full_path: str):
    """
    Catch-all GET route:
    - If full_path matches a static file on disk, serve it with proper MIME.
    - Otherwise, returns index.html so client routes refresh cleanly (/gave, /got, /customers/1, /settings).
    """
    index_file = os.path.join(STATIC_DIR, "index.html")

    # If asking for root, return index.html
    if not full_path or full_path == "":
        if os.path.isfile(index_file):
            return FileResponse(index_file, media_type="text/html")
        return JSONResponse({"ok": True, "message": "Voice Khata API running (index.html not built yet)"})

    # Check if a static file directly exists
    target_file = os.path.join(STATIC_DIR, full_path)
    if os.path.isfile(target_file):
        media_type, _ = mimetypes.guess_type(target_file)
        if target_file.endswith(".js") or target_file.endswith(".mjs"):
            media_type = "application/javascript"
        return FileResponse(target_file, media_type=media_type)

    # For SPA client routes (like /got, /gave, /customers/1, /settings), return index.html
    if os.path.isfile(index_file):
        return FileResponse(index_file, media_type="text/html")

    raise HTTPException(status_code=404, detail="File not found")
