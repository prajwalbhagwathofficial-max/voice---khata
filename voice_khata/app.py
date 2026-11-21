"""FastAPI application serving the laptop browser UI and single /ws/session stream."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from voice_khata.config import Settings
from voice_khata.gnani_stt import GnaniPrismaSTTFactory, STTClientFactoryProtocol
from voice_khata.gnani_tts import GnaniTimbreTTSClient, TTSClientProtocol
from voice_khata.ledger import LedgerStore
from voice_khata.logging_utils import configure_logging, log_event
from voice_khata.models import LanguageCode
from voice_khata.nlu import GeminiIntentExtractor, NLUClientProtocol
from voice_khata.orchestrator import SessionOrchestrator

logger = logging.getLogger("voice_khata.app")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


async def _iter_websocket_messages(websocket: WebSocket) -> AsyncIterator[dict[str, Any] | bytes]:
    """Yield binary PCM audio frames and parsed JSON control messages from a FastAPI WebSocket."""
    try:
        while True:
            packet = await websocket.receive()
            msg_type = packet.get("type")
            if msg_type == "websocket.disconnect":
                break
            raw_bytes = packet.get("bytes")
            if raw_bytes is not None:
                yield raw_bytes
                continue
            raw_text = packet.get("text")
            if raw_text is not None:
                try:
                    parsed = json.loads(raw_text)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    yield parsed
    except WebSocketDisconnect:
        return


def create_app(
    *,
    settings: Settings | None = None,
    ledger: LedgerStore | None = None,
    nlu: NLUClientProtocol | None = None,
    stt_factory: STTClientFactoryProtocol | None = None,
    tts_client: TTSClientProtocol | None = None,
) -> FastAPI:
    """Create the Voice Khata FastAPI application.

    All dynamic data (audio, transcripts, ledger balances/events, TTS chunks,
    confirmations, latency telemetry) flows exclusively over /ws/session.
    """
    cfg = settings or Settings()
    configure_logging(level=cfg.voice_khata_log_level, secrets=cfg.secret_values())

    store = ledger or LedgerStore(cfg.voice_khata_db_path)
    nlu_engine = nlu or GeminiIntentExtractor(cfg)
    stt_eng = stt_factory or GnaniPrismaSTTFactory(cfg)
    tts_eng = tts_client or GnaniTimbreTTSClient(cfg)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        log_event(
            logger,
            "app.startup",
            stt_model=cfg.gnani_stt_model,
            tts_model=cfg.gnani_tts_model,
            tts_voice=cfg.gnani_tts_voice,
            db_path=str(store.db_path),
            gnani_configured=cfg.has_gnani_key(),
            gemini_configured=cfg.has_gemini_key(),
        )
        yield
        log_event(logger, "app.shutdown")

    app = FastAPI(title="Voice Khata", version="0.1.0", lifespan=lifespan)
    app.state.settings = cfg
    app.state.ledger = store
    app.state.nlu = nlu_engine
    app.state.stt_factory = stt_eng
    app.state.tts = tts_eng

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.websocket("/ws/session")
    async def ws_session(websocket: WebSocket) -> None:
        await websocket.accept()
        lang_param = websocket.query_params.get("lang")
        initial_lang = LanguageCode.normalize(lang_param, default=LanguageCode.HI_IN)

        orchestrator = SessionOrchestrator(
            transport=websocket,
            ledger=app.state.ledger,
            nlu=app.state.nlu,
            stt_factory=app.state.stt_factory,
            tts=app.state.tts,
            initial_language=initial_lang,
        )
        try:
            await orchestrator.run(_iter_websocket_messages(websocket))
        except WebSocketDisconnect:
            log_event(logger, "ws.disconnected", session_id=orchestrator.session_id)

    return app


app = create_app()
