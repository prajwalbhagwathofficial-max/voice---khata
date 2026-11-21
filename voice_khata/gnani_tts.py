"""Custom asyncio WebSocket streaming client for Gnani Timbre v3.0 TTS."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import struct
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any, Protocol

import websockets

from voice_khata.config import Settings
from voice_khata.logging_utils import log_event
from voice_khata.models import LanguageCode

logger = logging.getLogger("voice_khata.gnani_tts")


@dataclass(frozen=True)
class TTSAudioChunk:
    """Single decoded 16-bit LE mono PCM audio chunk streamed from Timbre v3.0."""

    seq: int
    pcm_bytes: bytes
    sample_rate: int
    is_final: bool
    mono_ns: int


class TTSClientProtocol(Protocol):
    def synthesize_stream(
        self,
        *,
        text: str,
        language_code: LanguageCode,
        session_id: str,
        turn_id: str,
    ) -> AsyncIterator[TTSAudioChunk]: ...


def strip_wav_header(raw_bytes: bytes, default_sample_rate: int) -> tuple[bytes, int]:
    """Strip a RIFF/WAVE header if present and return (pcm_bytes, sample_rate)."""
    if len(raw_bytes) < 12 or raw_bytes[:4] != b"RIFF" or raw_bytes[8:12] != b"WAVE":
        return raw_bytes, default_sample_rate

    sample_rate = default_sample_rate
    offset = 12
    total_len = len(raw_bytes)

    while offset + 8 <= total_len:
        chunk_id = raw_bytes[offset : offset + 4]
        chunk_size = struct.unpack_from("<I", raw_bytes, offset + 4)[0]
        chunk_data_start = offset + 8

        if chunk_id == b"fmt " and chunk_data_start + 8 <= total_len:
            sample_rate = struct.unpack_from("<I", raw_bytes, chunk_data_start + 4)[0]
        elif chunk_id == b"data":
            return raw_bytes[chunk_data_start:], sample_rate

        # Chunks are word-aligned (padded to even byte boundary)
        offset = chunk_data_start + chunk_size + (chunk_size % 2)

    return raw_bytes, sample_rate


def build_timbre_payload(
    settings: Settings, *, text: str, language_code: LanguageCode
) -> dict[str, Any]:
    """Build Timbre v3.0 synthesis request payload using only stock voices."""
    return {
        "text": text,
        "voice": settings.gnani_tts_voice,
        "model": settings.gnani_tts_model,
        "language": language_code.value,
        "speed": 1.0,
        "audio_config": {
            "sample_rate": settings.gnani_tts_sample_rate,
            "num_channels": 1,
            "sample_width": 2,
            "encoding": "linear_pcm",
            "container": "wav",
        },
    }


def build_timbre_headers(settings: Settings) -> dict[str, str]:
    """Build WebSocket upgrade headers for Gnani Timbre v3.0."""
    api_key = settings.gnani_api_key.get_secret_value().strip()
    if not api_key:
        raise RuntimeError("GNANI_API_KEY is not configured in .env")
    return {"X-API-Key-ID": api_key}


class GnaniTimbreTTSClient:
    """Streams TTS audio chunks from Gnani Timbre v3.0 over WebSocket."""

    def __init__(
        self,
        settings: Settings,
        connect_fn: Callable[..., Any] = websockets.connect,
    ) -> None:
        self._settings = settings
        self._connect_fn = connect_fn

    async def synthesize_stream(
        self,
        *,
        text: str,
        language_code: LanguageCode,
        session_id: str,
        turn_id: str,
    ) -> AsyncIterator[TTSAudioChunk]:
        if not text.strip():
            return

        headers = build_timbre_headers(self._settings)
        payload = build_timbre_payload(
            self._settings, text=text, language_code=language_code
        )
        sample_rate = self._settings.gnani_tts_sample_rate
        seq = 0

        log_event(
            logger,
            "tts.connect_start",
            session_id=session_id,
            turn_id=turn_id,
            model=self._settings.gnani_tts_model,
            voice=self._settings.gnani_tts_voice,
            language_code=language_code.value,
            text_len=len(text),
        )

        try:
            async with self._connect_fn(
                self._settings.gnani_tts_url,
                additional_headers=headers,
                open_timeout=10,
                ping_interval=20,
                ping_timeout=10,
            ) as ws:
                await ws.send(json.dumps(payload, ensure_ascii=False))
                log_event(
                    logger,
                    "tts.request_sent",
                    session_id=session_id,
                    turn_id=turn_id,
                )

                async for raw_msg in ws:
                    now_ns = time.monotonic_ns()
                    if isinstance(raw_msg, bytes):
                        pcm, sample_rate = strip_wav_header(raw_msg, sample_rate)
                        if pcm:
                            seq += 1
                            yield TTSAudioChunk(
                                seq=seq,
                                pcm_bytes=pcm,
                                sample_rate=sample_rate,
                                is_final=False,
                                mono_ns=now_ns,
                            )
                        continue

                    try:
                        frame = json.loads(raw_msg)
                    except json.JSONDecodeError:
                        continue

                    if not isinstance(frame, dict):
                        continue

                    msg_type = str(frame.get("type", "")).strip().lower()
                    inner = frame.get("data") if isinstance(frame.get("data"), dict) else frame

                    if msg_type == "error" or frame.get("error") or inner.get("error"):
                        err = inner.get("error") or frame.get("error") or inner.get("message") or frame.get("message")
                        raise RuntimeError(f"Gnani Timbre v3.0 error: {err}")

                    if msg_type in {"complete", "done", "end"}:
                        log_event(
                            logger,
                            "tts.stream_complete",
                            mono_ns=now_ns,
                            session_id=session_id,
                            turn_id=turn_id,
                            total_chunks=seq,
                        )
                        yield TTSAudioChunk(
                            seq=seq + 1,
                            pcm_bytes=b"",
                            sample_rate=sample_rate,
                            is_final=True,
                            mono_ns=now_ns,
                        )
                        break

                    audio_b64 = inner.get("audio") or ""
                    is_final = bool(inner.get("is_final", frame.get("is_final", False)))

                    pcm = b""
                    if audio_b64:
                        raw_audio = base64.b64decode(audio_b64)
                        pcm, sample_rate = strip_wav_header(raw_audio, sample_rate)

                    if pcm or is_final:
                        seq += 1
                        if seq == 1:
                            log_event(
                                logger,
                                "tts.first_chunk",
                                mono_ns=now_ns,
                                session_id=session_id,
                                turn_id=turn_id,
                                byte_len=len(pcm),
                                sample_rate=sample_rate,
                            )
                        yield TTSAudioChunk(
                            seq=seq,
                            pcm_bytes=pcm,
                            sample_rate=sample_rate,
                            is_final=is_final,
                            mono_ns=now_ns,
                        )

                    if is_final:
                        log_event(
                            logger,
                            "tts.stream_complete",
                            mono_ns=now_ns,
                            session_id=session_id,
                            turn_id=turn_id,
                            total_chunks=seq,
                        )
                        break
        except asyncio.CancelledError:
            log_event(
                logger,
                "tts.cancelled",
                session_id=session_id,
                turn_id=turn_id,
                chunks_emitted=seq,
            )
            raise
