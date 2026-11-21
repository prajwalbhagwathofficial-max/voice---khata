"""Tests for voice_khata.app (FastAPI static UI + single /ws/session endpoint)."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi.testclient import TestClient

from voice_khata.app import create_app
from voice_khata.config import Settings
from voice_khata.gnani_stt import STTEvent
from voice_khata.gnani_tts import TTSAudioChunk
from voice_khata.ledger import LedgerStore
from voice_khata.models import IntentType, LanguageCode, ParsedIntent


class _StubNLU:
    async def extract_intent(
        self,
        transcript: str,
        *,
        default_language: LanguageCode,
        awaiting_confirmation: bool = False,
    ) -> ParsedIntent:
        return ParsedIntent(intent=IntentType.QUERY_TOTAL, language_code=default_language)


class _StubSTTStream:
    async def start(self) -> None:
        return None

    async def send_audio(self, pcm_bytes: bytes) -> None:
        return None

    async def flush_utterance(self) -> None:
        return None

    async def events(self) -> AsyncIterator[STTEvent]:
        yield STTEvent(
            transcript="Total balance",
            is_final=True,
            language_code=LanguageCode.EN_IN,
            mono_ns=time.monotonic_ns(),
        )

    async def close(self) -> None:
        return None


class _StubSTTFactory:
    def create_stream(self, *, language_code: LanguageCode, session_id: str, turn_id: str):
        return _StubSTTStream()


class _StubTTS:
    async def synthesize_stream(
        self,
        *,
        text: str,
        language_code: LanguageCode,
        session_id: str,
        turn_id: str,
    ) -> AsyncIterator[TTSAudioChunk]:
        yield TTSAudioChunk(
            seq=1,
            pcm_bytes=b"\x01\x00" * 16,
            sample_rate=48000,
            is_final=True,
            mono_ns=time.monotonic_ns(),
        )


def test_app_serves_laptop_ui_and_streams_ws_session(tmp_path: Path) -> None:
    cfg = Settings(_env_file=None, voice_khata_db_path=tmp_path / "app_test.db")
    ledger = LedgerStore(cfg.voice_khata_db_path)
    app = create_app(
        settings=cfg,
        ledger=ledger,
        nlu=_StubNLU(),
        stt_factory=_StubSTTFactory(),
        tts_client=_StubTTS(),
    )

    # Verify no REST dynamic ledger endpoints exist (WebSocket only for dynamic data)
    http_paths = {route.path for route in app.routes if hasattr(route, "methods")}
    assert "/" in http_paths
    assert not any("ledger" in p or "balance" in p for p in http_paths)

    with TestClient(app) as client:
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Voice Khata" in resp.text

        with client.websocket_connect("/ws/session?lang=kn-IN") as ws:
            ready = ws.receive_json()
            assert ready["type"] == "session_ready"
            assert ready["language_code"] == "kn-IN"

            snapshot = ws.receive_json()
            assert snapshot["type"] == "ledger_snapshot"
            assert snapshot["summary"]["total_outstanding_rupees"] == 0

            ws.send_json({"type": "session_start", "language_code": "kn-IN"})

            seen_types: list[str] = []
            agent_text = ""
            for _ in range(12):
                msg = ws.receive_json()
                seen_types.append(msg["type"])
                if msg["type"] == "agent_text":
                    agent_text = msg["text"]
                if msg["type"] == "state" and msg["state"] == "IDLE" and agent_text:
                    break

            assert "ಎಐ" in agent_text
            assert "tts_chunk" in seen_types
            assert "latency" in seen_types
