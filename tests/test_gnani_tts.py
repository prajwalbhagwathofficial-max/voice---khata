"""Tests for voice_khata.gnani_tts (Gnani Timbre v3.0 WebSocket client)."""

from __future__ import annotations

import asyncio
import base64
import json
import struct
from contextlib import asynccontextmanager

import pytest

from voice_khata.config import Settings
from voice_khata.gnani_tts import (
    GnaniTimbreTTSClient,
    build_timbre_payload,
    strip_wav_header,
)
from voice_khata.models import LanguageCode


def _make_wav(pcm: bytes, sample_rate: int = 24000) -> bytes:
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + len(pcm),
        b"WAVE",
        b"fmt ",
        16,
        1,  # PCM
        1,  # Mono
        sample_rate,
        sample_rate * 2,
        2,
        16,
        b"data",
        len(pcm),
    )
    return header + pcm


class _FakeTimbreWS:
    def __init__(self, frames: list[str], delay: float = 0.0) -> None:
        self.sent: list[str] = []
        self._frames = list(frames)
        self._delay = delay

    async def send(self, text: str) -> None:
        self.sent.append(text)

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        if not self._frames:
            raise StopAsyncIteration
        if self._delay:
            await asyncio.sleep(self._delay)
        return self._frames.pop(0)


def test_strip_wav_header_extracts_pcm_and_sample_rate() -> None:
    pcm_orig = b"\x10\x00\x20\x00\x30\x00"
    wav_bytes = _make_wav(pcm_orig, sample_rate=24000)
    pcm, rate = strip_wav_header(wav_bytes, default_sample_rate=48000)
    assert pcm == pcm_orig
    assert rate == 24000


def test_build_timbre_payload_uses_timbre_v3_stock_voice() -> None:
    cfg = Settings(_env_file=None, gnani_api_key="test-key", gnani_tts_voice="Avinash")
    payload = build_timbre_payload(cfg, text="नमस्ते", language_code=LanguageCode.HI_IN)
    assert payload["model"] == "timbre-v3.0"
    assert payload["voice"] == "Avinash"
    assert payload["language"] == "hi-IN"
    assert payload["audio_config"]["encoding"] == "linear_pcm"


@pytest.mark.asyncio
async def test_timbre_client_streams_chunks_and_strips_wav_header() -> None:
    cfg = Settings(_env_file=None, gnani_api_key="test-key")
    chunk1_pcm = b"\x01\x00" * 40
    chunk2_pcm = b"\x02\x00" * 40
    frames = [
        json.dumps(
            {
                "chunk_index": 1,
                "audio": base64.b64encode(_make_wav(chunk1_pcm, 48000)).decode("ascii"),
                "is_final": False,
            }
        ),
        json.dumps(
            {
                "chunk_index": 2,
                "audio": base64.b64encode(chunk2_pcm).decode("ascii"),
                "is_final": True,
            }
        ),
    ]
    fake_ws = _FakeTimbreWS(frames)

    @asynccontextmanager
    async def fake_connect(url: str, **kwargs):
        assert url == cfg.gnani_tts_url
        assert kwargs["additional_headers"]["X-API-Key-ID"] == "test-key"
        yield fake_ws

    client = GnaniTimbreTTSClient(cfg, connect_fn=fake_connect)
    chunks = [
        c
        async for c in client.synthesize_stream(
            text="Hello",
            language_code=LanguageCode.EN_IN,
            session_id="s1",
            turn_id="t001",
        )
    ]

    assert len(chunks) == 2
    assert chunks[0].pcm_bytes == chunk1_pcm
    assert chunks[0].sample_rate == 48000
    assert chunks[0].is_final is False
    assert chunks[1].pcm_bytes == chunk2_pcm
    assert chunks[1].is_final is True
    sent_payload = json.loads(fake_ws.sent[0])
    assert sent_payload["model"] == "timbre-v3.0"


@pytest.mark.asyncio
async def test_timbre_stream_supports_cooperative_cancellation() -> None:
    cfg = Settings(_env_file=None, gnani_api_key="test-key")
    frames = [
        json.dumps(
            {
                "chunk_index": i,
                "audio": base64.b64encode(b"\x01\x00" * 20).decode("ascii"),
                "is_final": False,
            }
        )
        for i in range(10)
    ]
    fake_ws = _FakeTimbreWS(frames, delay=0.05)

    @asynccontextmanager
    async def fake_connect(_url: str, **_kwargs):
        yield fake_ws

    client = GnaniTimbreTTSClient(cfg, connect_fn=fake_connect)

    async def consumer() -> None:
        async for _chunk in client.synthesize_stream(
            text="Long message",
            language_code=LanguageCode.EN_IN,
            session_id="s1",
            turn_id="t002",
        ):
            pass

    task = asyncio.create_task(consumer())
    await asyncio.sleep(0.07)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
