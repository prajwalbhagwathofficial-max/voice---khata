"""Tests for voice_khata.gnani_stt (Gnani Prisma v3.0 WebSocket client)."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import pytest

from voice_khata.config import Settings
from voice_khata.gnani_stt import (
    GnaniPrismaSTTStream,
    build_prisma_headers,
    parse_prisma_message,
)
from voice_khata.models import LanguageCode


class _FakePrismaWS:
    def __init__(self, incoming: list[str]) -> None:
        self.sent: list[bytes] = []
        self._incoming = list(incoming)

    async def send(self, payload: bytes) -> None:
        self.sent.append(payload)

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        if not self._incoming:
            raise StopAsyncIteration
        await asyncio.sleep(0.005)
        return self._incoming.pop(0)


def test_build_prisma_headers_uses_prisma_v3_and_api_key() -> None:
    cfg = Settings(_env_file=None, gnani_api_key="test-gnani-key")
    headers = build_prisma_headers(cfg, LanguageCode.KN_IN)
    assert headers["X-API-Key-ID"] == "test-gnani-key"
    assert headers["model"] == "prisma-v3.0"
    assert headers["language_code"] == "kn-IN"
    assert headers["x-sample-rate"] == "16000"


def test_parse_prisma_message_handles_partial_and_final() -> None:
    partial = parse_prisma_message(
        json.dumps({"success": True, "is_final": False, "transcript": "रमेश के"}),
        LanguageCode.HI_IN,
    )
    assert partial is not None
    assert partial.is_final is False
    assert partial.transcript == "रमेश के"
    assert partial.mono_ns > 0

    final = parse_prisma_message(
        json.dumps(
            {
                "success": True,
                "is_final": True,
                "transcript": "रमेश के 200 रुपये उधार",
                "confidence": 0.94,
                "language_code": "hi-IN",
            }
        ),
        LanguageCode.EN_IN,
    )
    assert final is not None
    assert final.is_final is True
    assert final.language_code == LanguageCode.HI_IN
    assert final.confidence == 0.94


@pytest.mark.asyncio
async def test_prisma_stream_sends_pcm_and_emits_partial_and_final() -> None:
    cfg = Settings(_env_file=None, gnani_api_key="test-gnani-key")
    fake_ws = _FakePrismaWS(
        [
            json.dumps({"event": "connected", "status": "success", "model": "prisma-v3.0"}),
            json.dumps({"success": True, "is_final": False, "transcript": "Ramesh"}),
            json.dumps({"success": True, "is_final": True, "transcript": "Ramesh 150 credit"}),
        ]
    )
    captured_kwargs: dict[str, object] = {}

    @asynccontextmanager
    async def fake_connect(url: str, **kwargs):
        captured_kwargs["url"] = url
        captured_kwargs.update(kwargs)
        yield fake_ws

    stream = GnaniPrismaSTTStream(
        cfg,
        language_code=LanguageCode.EN_IN,
        session_id="s1",
        turn_id="t001",
        connect_fn=fake_connect,
    )
    await stream.start()
    await stream.send_audio(b"\x01\x02" * 1600)

    received = [ev async for ev in stream.events()]
    await stream.close()

    assert captured_kwargs["url"] == cfg.gnani_stt_url
    assert captured_kwargs["additional_headers"]["model"] == "prisma-v3.0"  # type: ignore[index]
    assert len(received) == 2
    assert received[0].is_final is False
    assert received[0].transcript == "Ramesh"
    assert received[1].is_final is True
    assert received[1].transcript == "Ramesh 150 credit"
    assert len(fake_ws.sent) >= 1
