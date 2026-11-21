"""Single-origin FastAPI application serving /ws/session, /healthz, /admin/*, /receipt/*, and SPA client."""

from __future__ import annotations

import hmac
import logging
import mimetypes
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from server.config import ServerSettings
from server.ledger import Ledger, generate_key_b64, seed_demo_data
from server.nlu.groq_nlu import GroqIntentExtractor
from server.receipt import render_receipt_html, render_receipt_not_found_html
from server.session import UnifiedSessionOrchestrator
from server.speech import GnaniSTTClient, GnaniTTSClient, MockSTT, MockTTS, STTClient, TTSClient
from voice_khata import templates
from voice_khata.logging_utils import configure_logging, log_event
from voice_khata.models import LanguageCode
from voice_khata.nlu import NLUClientProtocol

# Ensure .js and .mjs (including AudioWorklets) map to application/javascript on all OSes
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("application/javascript", ".mjs")

logger = logging.getLogger("voice_khata.server.app")

ROOT_DIR = Path(__file__).resolve().parent.parent


def _resolve_static_dir(explicit: Path | None = None) -> Path | None:
    if explicit is not None and explicit.exists():
        return explicit
    for candidate in (
        Path("/app/static"),
        ROOT_DIR / "client" / "dist",
        ROOT_DIR / "static",
    ):
        if candidate.exists() and (candidate / "index.html").exists():
            return candidate
    return None


async def _iter_ws_packets(websocket: WebSocket) -> AsyncIterator[bytes | str]:
    try:
        while True:
            packet = await websocket.receive()
            if packet.get("type") == "websocket.disconnect":
                break
            if packet.get("bytes") is not None:
                yield packet["bytes"]
            elif packet.get("text") is not None:
                yield packet["text"]
    except WebSocketDisconnect:
        return


async def _presynthesize_tts_cache(
    make_tts: Callable[[str], TTSClient],
) -> dict[tuple[str, str], tuple[int, list[bytes]]]:
    """Pre-synthesize static first-turn AI greetings at startup for instant playback."""
    cache: dict[tuple[str, str], tuple[int, list[bytes]]] = {}
    phrases = [
        (LanguageCode.HI_IN, templates.render_ai_greeting(LanguageCode.HI_IN)),
        (LanguageCode.EN_IN, templates.render_ai_greeting(LanguageCode.EN_IN)),
    ]
    tts = make_tts("startup-cache")
    try:
        for lang, text in phrases:
            await tts.open(lang.value)
            if hasattr(tts, "set_language"):
                tts.set_language(lang.value)  # type: ignore[attr-defined]
            chunks = [c async for c in tts.synthesize(text)]
            if chunks:
                cache[(lang.value, text)] = (tts.sample_rate, chunks)
    except Exception as exc:
        log_event(
            logger,
            "server.tts_cache_warn",
            level=logging.WARNING,
            error=str(exc),
        )
    finally:
        await tts.close()
    return cache


def _verify_admin_token(
    cfg: ServerSettings,
    authorization: str | None = None,
    x_admin_token: str | None = None,
) -> None:
    if not cfg.has_admin_token():
        raise HTTPException(status_code=503, detail="ADMIN_TOKEN is not configured")

    expected = cfg.admin_token.get_secret_value().strip()
    provided = ""
    if x_admin_token:
        provided = x_admin_token.strip()
    elif authorization and authorization.lower().startswith("bearer "):
        provided = authorization[7:].strip()

    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing admin token")


def create_server_app(
    *,
    settings: ServerSettings | None = None,
    ledger: Ledger | None = None,
    nlu: NLUClientProtocol | None = None,
    stt_factory: Callable[[str], STTClient] | None = None,
    tts_factory: Callable[[str], TTSClient] | None = None,
    static_dir: Path | None = None,
    auto_seed: bool = True,
    prewarm_tts_cache: bool = True,
) -> FastAPI:
    """Create the single-origin Voice Khata FastAPI app (single-worker Render/Docker ready)."""
    cfg = settings or ServerSettings()
    configure_logging(level=cfg.voice_khata_log_level, secrets=cfg.secret_values())

    def _default_stt(session_id: str) -> STTClient:
        if cfg.use_mocks:
            return MockSTT()
        return GnaniSTTClient(cfg, session_id=session_id)

    def _default_tts(session_id: str) -> TTSClient:
        if cfg.use_mocks:
            return MockTTS()
        return GnaniTTSClient(cfg, session_id=session_id)

    make_stt = stt_factory or _default_stt
    make_tts = tts_factory or _default_tts

    @asynccontextmanager
    async def lifespan(app_instance: FastAPI) -> AsyncIterator[None]:
        # 1. Fail fast if required production secrets are missing
        cfg.validate_required_secrets()

        # 2. Verify DATA_DIR is writable at boot
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        probe = cfg.data_dir / ".probe_write"
        try:
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
        except Exception as exc:
            raise RuntimeError(f"Startup aborted: DATA_DIR {cfg.data_dir} is not writable") from exc

        # 3. Log DB key fingerprint (never log secret values)
        db_key_fp = cfg.db_encryption_key_fingerprint()
        log_event(logger, "server.db_key", key_fingerprint=db_key_fp)

        # 4. Create SQLite DB in DATA_DIR, run schema/triggers, seed synthetic data if empty
        if ledger is not None:
            store = ledger
        else:
            enc_key = cfg.effective_db_encryption_key()
            if not enc_key:
                key_file = cfg.data_dir / ".ledger_key"
                if key_file.exists():
                    enc_key = key_file.read_text(encoding="utf-8").strip()
                else:
                    enc_key = generate_key_b64()
                    try:
                        key_file.write_text(enc_key, encoding="utf-8")
                    except Exception:
                        pass
            store = Ledger(cfg.resolved_db_path(), enc_key=enc_key)

        if auto_seed:
            cust_count = await store.customers_count()
            if cust_count == 0:
                await seed_demo_data(store)

        # 5. Configure Groq NLU (never arithmetic or query replies)
        nlu_engine: NLUClientProtocol | None = nlu
        if nlu_engine is None and not cfg.use_mocks and cfg.has_groq_key():
            nlu_engine = GroqIntentExtractor(cfg)

        # 6. Pre-synthesize cached static TTS phrases (Gnani per-session sockets stay lazy)
        tts_cache: dict[tuple[str, str], tuple[int, list[bytes]]] = {}
        if prewarm_tts_cache:
            tts_cache = await _presynthesize_tts_cache(make_tts)

        app_instance.state.ledger = store
        app_instance.state.nlu = nlu_engine
        app_instance.state.tts_cache = tts_cache
        app_instance.state.active_websockets = set()

        log_event(
            logger,
            "server.startup",
            use_mocks=cfg.use_mocks,
            stt_model=cfg.gnani_stt_model,
            tts_model=cfg.gnani_tts_model,
            groq_model=cfg.groq_model,
            data_dir=str(cfg.data_dir),
            db_key_fp=db_key_fp,
            cached_tts_phrases=len(tts_cache),
        )
        try:
            yield
        finally:
            # Graceful shutdown: close WebSockets, finish in-flight commits, checkpoint WAL
            active_sockets = list(getattr(app_instance.state, "active_websockets", []))
            for ws in active_sockets:
                try:
                    await ws.close(code=1001, reason="Server shutting down")
                except Exception:
                    pass
            if hasattr(app_instance.state, "active_websockets"):
                app_instance.state.active_websockets.clear()

            if store is not None:
                try:
                    await store.checkpoint()
                except Exception as exc:
                    log_event(logger, "server.shutdown.checkpoint_error", error=str(exc))

            if isinstance(nlu_engine, GroqIntentExtractor):
                await nlu_engine.close()
            log_event(logger, "server.shutdown")

    app = FastAPI(title="Voice Khata", version="0.1.0", lifespan=lifespan)
    app.state.settings = cfg
    app.state.ledger = ledger
    app.state.nlu = nlu
    app.state.tts_cache = {}
    app.state.active_websockets = set()

    @app.middleware("http")
    async def _security_and_mime_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Permissions-Policy"] = "microphone=(self)"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "connect-src 'self' ws: wss:; "
            "img-src 'self' data: blob:; "
            "media-src 'self' blob:; "
            "style-src 'self' 'unsafe-inline' https://api.fontshare.com; "
            "font-src 'self' https://api.fontshare.com https://cdn.fontshare.com data:; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' blob:; "
            "worker-src 'self' blob:;"
        )
        path_lower = request.url.path.lower()
        if path_lower.endswith(".js") or path_lower.endswith(".mjs"):
            response.headers["content-type"] = "application/javascript"
        if path_lower == "/" or path_lower.endswith(".html"):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    # 1. /ws/session (WebSocket Route)
    @app.websocket("/ws/session")
    async def ws_session(websocket: WebSocket) -> None:
        await websocket.accept()
        app.state.active_websockets.add(websocket)
        lang = LanguageCode.normalize(
            websocket.query_params.get("lang"), default=LanguageCode.HI_IN
        )
        store: Ledger = app.state.ledger
        session_id = f"s_{id(websocket):x}"
        orch = UnifiedSessionOrchestrator(
            transport=websocket,
            ledger=store,
            nlu=app.state.nlu,
            stt=make_stt(session_id),
            tts=make_tts(session_id),
            session_id=session_id,
            initial_lang=lang,
            tts_cache=app.state.tts_cache,
            settings=cfg,
        )
        try:
            await orch.run(_iter_ws_packets(websocket))
        except WebSocketDisconnect:
            log_event(logger, "server.ws.disconnected", session_id=orch.session_id)
        finally:
            app.state.active_websockets.discard(websocket)

    # 2. /healthz (GET Route)
    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        # Must NOT touch Gnani, Groq or the DB
        return {"ok": True}

    # 3. /admin/* (Admin Routes)
    @app.post("/admin/wipe")
    async def admin_wipe(
        authorization: str | None = Header(default=None),
        x_admin_token: str | None = Header(default=None),
    ) -> dict[str, bool]:
        _verify_admin_token(cfg, authorization, x_admin_token)
        store: Ledger | None = app.state.ledger
        if store is None:
            raise HTTPException(status_code=503, detail="Ledger not initialized")

        await store.wipe_all()
        log_event(logger, "admin.wipe_completed")
        return {"ok": True, "wiped": True}

    @app.get("/admin/export")
    async def admin_export(
        authorization: str | None = Header(default=None),
        x_admin_token: str | None = Header(default=None),
    ) -> StreamingResponse:
        """Stream a consistent snapshot of the encrypted SQLite DB using SQLite's backup API."""
        _verify_admin_token(cfg, authorization, x_admin_token)
        store: Ledger | None = app.state.ledger
        if store is None:
            raise HTTPException(status_code=503, detail="Ledger not initialized")

        snapshot = await store.export_backup_bytes()

        async def _stream():
            chunk_size = 64 * 1024
            for i in range(0, len(snapshot), chunk_size):
                yield snapshot[i : i + chunk_size]

        return StreamingResponse(
            _stream(),
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": 'attachment; filename="ledger_backup.db"',
                "Content-Length": str(len(snapshot)),
            },
        )

    # 4. /receipt/* and /r/* (Receipt Routes)
    @app.get("/receipt/{event_id}", response_class=HTMLResponse)
    @app.get("/r/{event_id}", response_class=HTMLResponse)
    async def view_public_receipt(event_id: str) -> HTMLResponse:
        """Render a tamper-evident digital receipt for the customer."""
        store: Ledger | None = app.state.ledger
        if store is None:
            raise HTTPException(status_code=503, detail="Ledger not initialized")
        event = await store.get_event_by_id(event_id)
        if event is None:
            return HTMLResponse(
                status_code=404,
                content=render_receipt_not_found_html(event_id),
            )
        customer = await store.get_customer(event.customer_id)
        customer_name = customer.name if customer is not None else "Customer"
        bal_paise = await store.balance(event.customer_id)
        bal_inr = bal_paise // 100
        items = await store.get_event_items(event.event_id)
        bills = await store.get_event_bills(event.event_id)
        return HTMLResponse(
            status_code=200,
            content=render_receipt_html(
                event=event,
                customer_name=customer_name,
                balance_inr=bal_inr,
                items=items,
                bills=bills,
                shop_name=cfg.shop_name,
            ),
        )

    @app.get("/receipt/{event_id}/bill/{bill_id}")
    async def view_receipt_bill(event_id: str, bill_id: str) -> Response:
        """Serve bill image/document attached to a receipt."""
        store: Ledger | None = app.state.ledger
        if store is None:
            raise HTTPException(status_code=503, detail="Ledger not initialized")
        res = await store.get_bill_by_id(bill_id)
        if res is None:
            raise HTTPException(status_code=404, detail="Bill record not found")
        record, data = res
        if record.event_id != event_id:
            raise HTTPException(status_code=403, detail="Bill does not belong to this event")
        return Response(content=data, media_type=record.mime)

    # 5. Static Files (/assets, /worklets)
    resolved_static = _resolve_static_dir(static_dir)
    if resolved_static is not None:
        assets_dir = resolved_static / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")
        worklets_dir = resolved_static / "worklets"
        if worklets_dir.is_dir():
            app.mount("/worklets", StaticFiles(directory=str(worklets_dir)), name="worklets")

    # 6. Root and Catch-all SPA Routes (/gave, /got, /customers/1, /settings)
    @app.get("/")
    async def root_index() -> Response:
        if resolved_static is not None:
            index_path = resolved_static / "index.html"
            if index_path.is_file():
                return FileResponse(index_path, media_type="text/html")
        return HTMLResponse("<!doctype html><html><head><title>Voice Khata</title></head><body><h1>Voice Khata</h1><div id='root'></div></body></html>")

    @app.get("/{full_path:path}")
    async def spa_catch_all(full_path: str) -> Response:
        if resolved_static is not None:
            try:
                candidate = (resolved_static / full_path).resolve()
                if candidate.is_relative_to(resolved_static.resolve()) and candidate.is_file():
                    media_type = None
                    if candidate.suffix in (".js", ".mjs"):
                        media_type = "application/javascript"
                    return FileResponse(candidate, media_type=media_type)
            except (ValueError, Exception):
                pass

            index_path = resolved_static / "index.html"
            if index_path.is_file():
                return FileResponse(index_path, media_type="text/html")

        return HTMLResponse("<!doctype html><html><head><title>Voice Khata</title></head><body><div id='root'></div></body></html>")

    return app


app = create_server_app()
