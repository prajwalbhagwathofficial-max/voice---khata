"""
Voice Khata - Single-Process Unified Render Web Service
FastAPI Backend + Built Client Static Delivery + SQLite Persistent Ledger
"""

import os
import json
import asyncio
import mimetypes
import logging
import httpx
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
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' blob: https://cdn.tailwindcss.com; "
        "worker-src 'self' blob:; "
        "media-src 'self' blob: data:; "
        "connect-src 'self' wss: ws: https:; "
        "style-src 'self' 'unsafe-inline' https: https://fonts.googleapis.com; "
        "font-src 'self' data: https://fonts.gstatic.com https://fonts.googleapis.com; "
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
        "groq": {
            "configured": bool(GROQ_API_KEY),
            "masked": mask(GROQ_API_KEY),
            "apiKey": GROQ_API_KEY or "",
            "model": os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
        },
        "gnani": {
            "configured": bool(GNANI_API_KEY),
            "masked": mask(GNANI_API_KEY),
            "apiKey": GNANI_API_KEY or "",
        },
        "db": {"configured": bool(DB_ENCRYPTION_KEY), "fingerprint": get_key_fingerprint(DB_ENCRYPTION_KEY)},
    }

@app.post("/api/settings/keys")
async def update_api_keys(req: Request):
    global GROQ_API_KEY, GNANI_API_KEY
    try:
        data = await req.json()
        if data.get("groqApiKey"):
            GROQ_API_KEY = str(data["groqApiKey"]).strip()
        if data.get("gnaniApiKey"):
            GNANI_API_KEY = str(data["gnaniApiKey"]).strip()
        return {"success": True, "message": "API keys updated successfully"}
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=400)

@app.post("/api/voice/extract")
async def extract_voice_intent(req: Request):
    body = {}
    try:
        body = await req.json()
        transcript = body.get("transcript", "")
        language = body.get("language", "hi")
        active_key = GROQ_API_KEY or os.getenv("GROQ_API_KEY", "")
        if not active_key or not transcript:
            return {"intent": "unknown", "customerName": None, "amount": None, "rawTranscript": transcript}
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {active_key}", "Content-Type": "application/json"},
                json={
                    "model": os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
                    "messages": [
                        {"role": "system", "content": "You extract structured intent from Indian kirana store speech. Return valid JSON only with keys: intent (credit, payment, purchase, query, greeting, unknown), customerName (string or null), amount (number or null), items (array), note (string)."},
                        {"role": "user", "content": f"Language: {language}\nSpoken utterance: {transcript}"}
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.1
                }
            )
            if resp.status_code == 200:
                result = resp.json()
                content = result["choices"][0]["message"]["content"]
                return json.loads(content)
            return {"intent": "unknown", "rawTranscript": transcript}
    except Exception as e:
        logger.warning(f"Groq intent extraction error: {e}")
        return {"intent": "unknown", "rawTranscript": body.get("transcript", "")}

@app.websocket("/api/voice/stt")
async def websocket_stt_proxy(client_ws: WebSocket):
    await client_ws.accept()
    query_params = dict(client_ws.query_params)
    active_lang = query_params.get("lang", "indic-auto")
    active_key = GNANI_API_KEY or os.getenv("GNANI_API_KEY", "")
    if not active_key:
        await client_ws.send_json({"type": "error", "message": "Gnani API key not configured"})
        await client_ws.close(code=1008)
        return

    stt_url = os.getenv("GNANI_STT_URL", "wss://api.vachana.ai/stt/v3/stream")
    headers = {
        "x-api-key-id": active_key,
        "x-model": "prisma-v3.0",
        "language_code": active_lang,
        "x-sample-rate": "16000",
    }
    import websockets
    try:
        async with websockets.connect(stt_url, extra_headers=headers) as gnani_ws:
            async def from_client():
                try:
                    while True:
                        msg = await client_ws.receive()
                        if "bytes" in msg and msg["bytes"]:
                            await gnani_ws.send(msg["bytes"])
                        elif "text" in msg and msg["text"]:
                            await gnani_ws.send(msg["text"])
                except Exception:
                    pass

            async def from_gnani():
                try:
                    async for gmsg in gnani_ws:
                        if isinstance(gmsg, str):
                            data = json.loads(gmsg) if gmsg.strip().startswith("{") else {"text": gmsg}
                            if "text" in data and "transcript" not in data:
                                data["transcript"] = data["text"]
                            await client_ws.send_text(json.dumps(data))
                        else:
                            await client_ws.send_bytes(gmsg)
                except Exception:
                    pass

            await asyncio.gather(from_client(), from_gnani(), return_exceptions=True)
    except Exception as e:
        logger.warning(f"Gnani STT connection error: {e}")
        try:
            await client_ws.send_json({"type": "error", "message": str(e)})
            await client_ws.close()
        except Exception:
            pass

@app.websocket("/api/voice/tts")
async def websocket_tts_proxy(client_ws: WebSocket):
    await client_ws.accept()
    active_key = GNANI_API_KEY or os.getenv("GNANI_API_KEY", "")
    if not active_key:
        await client_ws.send_json({"type": "error", "message": "Gnani API key not configured"})
        await client_ws.close(code=1008)
        return

    tts_url = os.getenv("GNANI_TTS_URL", "wss://api.vachana.ai/api/v1/tts")
    headers = {"X-API-Key-ID": active_key}
    import websockets
    try:
        while True:
            text_data = await client_ws.receive_text()
            req_data = json.loads(text_data) if text_data.strip().startswith("{") else {"text": text_data}
            text = req_data.get("text", "")
            if not text:
                continue

            voice = req_data.get("voice", "Shivani")
            lang = req_data.get("language", "hi-IN")
            payload = {
                "text": text,
                "voice": voice,
                "model": "timbre-v3.0",
                "language": lang,
                "speed": 1.0,
                "audio_config": {
                    "sample_rate": 48000,
                    "num_channels": 1,
                    "sample_width": 2,
                    "encoding": "linear_pcm",
                    "container": "wav",
                },
            }
            async with websockets.connect(tts_url, extra_headers=headers) as gnani_ws:
                await gnani_ws.send(json.dumps(payload))
                async for chunk in gnani_ws:
                    await client_ws.send_text(chunk if isinstance(chunk, str) else chunk.decode("utf-8", errors="ignore"))
    except Exception as e:
        logger.warning(f"Gnani TTS session finished: {e}")

@app.get("/api/sync/pull")
async def sync_pull():
    return {"customers": get_all_customers(), "transactions": get_all_transactions(), "timestamp": 0}

@app.post("/api/sync/push")
async def sync_push(req: Request):
    return {"status": "ok", "synced": True}

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
