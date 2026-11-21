"""Contract tests for persistent GnaniSTTClient & GnaniTTSClient + opt-in @pytest.mark.live smoke test."""

from __future__ import annotations

import asyncio
import base64
import json
import struct
from array import array

import pytest
import websockets

from server.config import ServerSettings
from server.speech import GnaniSTTClient, GnaniTTSClient, compute_backoff_delay


def _wav_chunk(pcm: bytes, sr: int = 16000) -> bytes:
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + len(pcm),
        b"WAVE",
        b"fmt ",
        16,
        1,
        1,
        sr,
        sr * 2,
        2,
        16,
        b"data",
        len(pcm),
    )
    return header + pcm


def test_exponential_backoff_with_jitter_bounds() -> None:
    d0 = compute_backoff_delay(0, base_delay_s=0.2, max_delay_s=4.0)
    d2 = compute_backoff_delay(2, base_delay_s=0.2, max_delay_s=4.0)
    assert 0.1 <= d0 <= 0.2
    assert 0.4 <= d2 <= 0.8


@pytest.mark.asyncio
async def test_gnani_tts_persistent_socket_cancel_and_drain_reuses_connection() -> None:
    requests_received: list[dict[str, object]] = []

    async def mock_tts_server(ws) -> None:
        async for raw in ws:
            req = json.loads(raw)
            requests_received.append(req)
            total_chunks = 10
            for idx in range(1, total_chunks + 1):
                pcm = b"\x05\x00" * 160
                payload_bytes = _wav_chunk(pcm, 16000) if idx == 1 else pcm
                await ws.send(
                    json.dumps(
                        {
                            "type": "audio",
                            "data": {
                                "chunk_index": idx,
                                "audio": base64.b64encode(payload_bytes).decode("ascii"),
                                "is_final": idx == total_chunks,
                            },
                        }
                    )
                )
                await asyncio.sleep(0.01)

    async with websockets.serve(mock_tts_server, "127.0.0.1", 0) as srv:
        port = next(iter(srv.sockets)).getsockname()[1]
        cfg = ServerSettings(
            _env_file=None,
            gnani_api_key="test-key-contract",
            gnani_tts_url=f"ws://127.0.0.1:{port}/tts",
            gnani_tts_sample_rate=16000,
        )
        tts = GnaniTTSClient(cfg, session_id="contract-tts")
        await tts.open("hi-IN")
        assert tts.connect_count == 1

        # Turn 1: cancel after receiving 2 chunks -> must stop within 1 chunk & drain cleanly
        got_turn1: list[bytes] = []
        async for chunk in tts.synthesize("First long utterance"):
            got_turn1.append(chunk)
            if len(got_turn1) == 2:
                await tts.cancel()
        assert len(got_turn1) <= 3

        # Turn 2: must reuse the EXACT SAME persistent WebSocket (connect_count stays 1!)
        got_turn2 = [chunk async for chunk in tts.synthesize("Second utterance")]
        assert len(got_turn2) == 10
        assert tts.connect_count == 1
        assert len(requests_received) == 2
        assert requests_received[0]["model"] == "timbre-v3.0"
        await tts.close()


@pytest.mark.asyncio
async def test_gnani_stt_persistent_socket_streams_partial_and_final() -> None:
    async def mock_stt_server(ws) -> None:
        assert ws.request.headers.get("model") == "prisma-v3.0"
        assert ws.request.headers.get("language_code") == "hi-IN"
        await ws.send(json.dumps({"type": "connected", "message": "ready"}))
        chunks = 0
        async for frame in ws:
            if isinstance(frame, bytes):
                chunks += 1
                if chunks == 2:
                    await ws.send(
                        json.dumps(
                            {
                                "type": "partial",
                                "data": {"transcript": "रमेश के", "is_final": False, "confidence": 0.85},
                            }
                        )
                    )
                elif chunks == 10:
                    await ws.send(
                        json.dumps(
                            {
                                "type": "final",
                                "data": {
                                    "transcript": "रमेश के 250 रुपये",
                                    "is_final": True,
                                    "confidence": 0.97,
                                },
                            }
                        )
                    )

    async with websockets.serve(mock_stt_server, "127.0.0.1", 0) as srv:
        port = next(iter(srv.sockets)).getsockname()[1]
        cfg = ServerSettings(
            _env_file=None,
            gnani_api_key="test-key-contract",
            gnani_stt_url=f"ws://127.0.0.1:{port}/stt",
        )
        stt = GnaniSTTClient(cfg, session_id="contract-stt")
        await stt.open("hi-IN")
        await stt.send_audio(b"\x01\x02" * 320)
        await stt.send_audio(b"\x01\x02" * 320)
        await stt.end_utterance()

        events = []
        async with asyncio.timeout(3):
            async for ev in stt.events():
                events.append(ev)
                if ev.kind == "final":
                    break
        await stt.close()

        assert len(events) == 2
        assert events[0].kind == "partial"
        assert events[0].confidence == 0.85
        assert events[1].kind == "final"
        assert events[1].text == "रमेश के 250 रुपये"
        assert events[1].confidence == 0.97


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_gnani_tts_and_stt_smoke() -> None:
    """Opt-in live smoke test against Gnani Timbre v3.0 + Prisma v3.0 (run with: pytest -m live)."""
    cfg = ServerSettings()
    if not cfg.has_gnani_key():
        pytest.skip("GNANI_API_KEY not configured in .env")

    tts = GnaniTTSClient(cfg, session_id="live-smoke")
    await tts.open("hi-IN")
    pcm_chunks = [c async for c in tts.synthesize("रमेश के दो सौ पचास रुपये उधार लिख दो।")]
    sr = tts.sample_rate
    await tts.close()
    assert len(pcm_chunks) > 0

    raw_pcm = b"".join(pcm_chunks)
    arr = array("h")
    arr.frombytes(raw_pcm[: len(raw_pcm) - (len(raw_pcm) % 2)])
    pcm16k = array("h", arr[:: max(1, sr // 16000)]).tobytes()

    stt = GnaniSTTClient(cfg, session_id="live-smoke")
    await stt.open("hi-IN")
    for i in range(0, len(pcm16k), 1024):
        await stt.send_audio(pcm16k[i : i + 1024])
        await asyncio.sleep(0.01)
    await stt.end_utterance()

    final_text = ""
    async with asyncio.timeout(10):
        async for ev in stt.events():
            if ev.kind == "final":
                final_text = ev.text
                break
    await stt.close()
    assert "250" in final_text or "रमेश" in final_text
