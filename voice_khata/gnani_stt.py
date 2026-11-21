"""Custom asyncio WebSocket streaming client for Gnani Prisma v3.0 STT."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any, Protocol

import websockets

from voice_khata.config import Settings
from voice_khata.logging_utils import log_event
from voice_khata.models import LanguageCode

logger = logging.getLogger("voice_khata.gnani_stt")

# 16kHz, 16-bit mono PCM = 32,000 bytes/second. 500ms trailing silence = 16,000 bytes.
_TRAILING_SILENCE_CHUNK = b"\x00" * 3200
_TRAILING_SILENCE_CHUNKS = 5


@dataclass(frozen=True)
class STTEvent:
    """Transcript or status event emitted by Gnani Prisma v3.0."""

    transcript: str
    is_final: bool
    language_code: LanguageCode | None = None
    confidence: float | None = None
    event_type: str = "transcript"
    mono_ns: int = 0


class STTStreamProtocol(Protocol):
    async def start(self) -> None: ...
    async def send_audio(self, pcm_bytes: bytes) -> None: ...
    async def flush_utterance(self) -> None: ...
    def events(self) -> AsyncIterator[STTEvent]: ...
    async def close(self) -> None: ...


class STTClientFactoryProtocol(Protocol):
    def create_stream(
        self,
        *,
        language_code: LanguageCode,
        session_id: str,
        turn_id: str,
    ) -> STTStreamProtocol: ...


def build_prisma_headers(settings: Settings, language_code: LanguageCode) -> dict[str, str]:
    """Build WebSocket upgrade headers for Gnani Prisma v3.0 streaming STT."""
    api_key = settings.gnani_api_key.get_secret_value().strip()
    if not api_key:
        raise RuntimeError("GNANI_API_KEY is not configured in .env")
    return {
        "X-API-Key-ID": api_key,
        "model": settings.gnani_stt_model,
        "language_code": language_code.value,
        "x-sample-rate": "16000",
        "format": "transcribe",
    }


def parse_prisma_message(
    raw_message: str | bytes, default_language: LanguageCode
) -> STTEvent | None:
    """Parse a JSON message from Gnani Prisma v3.0 WebSocket."""
    if isinstance(raw_message, bytes):
        raw_message = raw_message.decode("utf-8", errors="ignore")

    try:
        data = json.loads(raw_message)
    except json.JSONDecodeError:
        return None

    if not isinstance(data, dict):
        return None

    now_ns = time.monotonic_ns()
    event_name = str(data.get("type") or data.get("event") or "").strip().lower()
    if event_name in {"connected", "processing", "vad", "speech_start", "speech_end"}:
        return STTEvent(
            transcript="",
            is_final=False,
            language_code=LanguageCode.normalize(
                data.get("language_code"), default=default_language
            ),
            event_type=event_name,
            mono_ns=now_ns,
        )

    inner = data.get("data") if isinstance(data.get("data"), dict) else data
    transcript = str(
        inner.get("transcript") or inner.get("text") or data.get("transcript") or data.get("text") or ""
    ).strip()
    is_final = bool(
        inner.get("is_final")
        or inner.get("final")
        or data.get("is_final")
        or data.get("final")
        or event_name == "final"
    )
    if not transcript and not is_final:
        return None

    lang_raw = inner.get("language_code") or inner.get("language") or data.get("language_code")
    lang = LanguageCode.normalize(lang_raw, default=default_language) if lang_raw else None
    conf_raw = inner.get("confidence", data.get("confidence"))
    confidence = float(conf_raw) if isinstance(conf_raw, (int, float)) else None

    return STTEvent(
        transcript=transcript,
        is_final=is_final,
        language_code=lang,
        confidence=confidence,
        event_type="transcript",
        mono_ns=now_ns,
    )


class GnaniPrismaSTTStream:
    """Single-utterance or streaming session against Gnani Prisma v3.0 WebSocket."""

    def __init__(
        self,
        settings: Settings,
        *,
        language_code: LanguageCode,
        session_id: str,
        turn_id: str,
        connect_fn: Callable[..., Any] = websockets.connect,
    ) -> None:
        self._settings = settings
        self._language_code = language_code
        self._session_id = session_id
        self._turn_id = turn_id
        self._connect_fn = connect_fn

        self._audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=128)
        self._event_queue: asyncio.Queue[STTEvent | None] = asyncio.Queue(maxsize=64)
        self._runner_task: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()
        self._closed = False
        self._start_error: BaseException | None = None

    async def start(self) -> None:
        if self._runner_task is not None:
            return
        self._runner_task = asyncio.create_task(
            self._run_loop(), name=f"stt-{self._session_id}-{self._turn_id}"
        )
        await self._ready.wait()
        if self._start_error is not None:
            raise self._start_error

    async def _run_loop(self) -> None:
        headers = build_prisma_headers(self._settings, self._language_code)
        try:
            async with self._connect_fn(
                self._settings.gnani_stt_url,
                additional_headers=headers,
                open_timeout=10,
                ping_interval=20,
                ping_timeout=10,
            ) as ws:
                log_event(
                    logger,
                    "stt.connected",
                    session_id=self._session_id,
                    turn_id=self._turn_id,
                    model=self._settings.gnani_stt_model,
                    language_code=self._language_code.value,
                )
                self._ready.set()

                async with asyncio.TaskGroup() as tg:
                    sender = tg.create_task(self._send_audio_loop(ws))
                    receiver = tg.create_task(self._receive_loop(ws))
                    # Receiver exits on final or socket close; sender exits on None sentinel.
                    await receiver
                    if not sender.done():
                        sender.cancel()
        except asyncio.CancelledError:
            log_event(
                logger,
                "stt.cancelled",
                session_id=self._session_id,
                turn_id=self._turn_id,
            )
            raise
        except Exception as exc:
            if not self._ready.is_set():
                self._start_error = exc
                self._ready.set()
            log_event(
                logger,
                "stt.error",
                level=logging.ERROR,
                session_id=self._session_id,
                turn_id=self._turn_id,
                error=str(exc),
            )
        finally:
            self._closed = True
            if not self._ready.is_set():
                self._ready.set()
            await self._event_queue.put(None)

    async def _send_audio_loop(self, ws: Any) -> None:
        chunks_sent = 0
        try:
            while True:
                chunk = await self._audio_queue.get()
                if chunk is None:
                    break
                await ws.send(chunk)
                chunks_sent += 1
                if chunks_sent == 1:
                    log_event(
                        logger,
                        "stt.first_audio_chunk_sent",
                        session_id=self._session_id,
                        turn_id=self._turn_id,
                        byte_len=len(chunk),
                    )
        except asyncio.CancelledError:
            raise

    async def _receive_loop(self, ws: Any) -> None:
        first_partial_logged = False
        try:
            async for raw in ws:
                event = parse_prisma_message(raw, self._language_code)
                if event is None:
                    continue
                if event.event_type == "transcript":
                    if not event.is_final and not first_partial_logged:
                        first_partial_logged = True
                        log_event(
                            logger,
                            "stt.first_partial",
                            mono_ns=event.mono_ns,
                            session_id=self._session_id,
                            turn_id=self._turn_id,
                        )
                    elif event.is_final:
                        log_event(
                            logger,
                            "stt.final_transcript",
                            mono_ns=event.mono_ns,
                            session_id=self._session_id,
                            turn_id=self._turn_id,
                            transcript_len=len(event.transcript),
                        )
                    await self._event_queue.put(event)
                    if event.is_final:
                        break
        except asyncio.CancelledError:
            raise

    async def send_audio(self, pcm_bytes: bytes) -> None:
        if self._closed or not pcm_bytes:
            return
        try:
            self._audio_queue.put_nowait(pcm_bytes)
        except asyncio.QueueFull:
            # Drop oldest frame under backpressure to keep realtime latency low.
            try:
                self._audio_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self._audio_queue.put_nowait(pcm_bytes)

    async def flush_utterance(self) -> None:
        """Pad trailing silence so Prisma v3.0 VAD closes the utterance cleanly."""
        if self._closed:
            return
        for _ in range(_TRAILING_SILENCE_CHUNKS):
            await self.send_audio(_TRAILING_SILENCE_CHUNK)
            await asyncio.sleep(0.01)

    async def events(self) -> AsyncIterator[STTEvent]:
        while True:
            event = await self._event_queue.get()
            if event is None:
                break
            yield event

    async def close(self) -> None:
        if self._closed and self._runner_task is None:
            return
        self._closed = True
        try:
            self._audio_queue.put_nowait(None)
        except asyncio.QueueFull:
            pass
        if self._runner_task is not None and not self._runner_task.done():
            self._runner_task.cancel()
            try:
                await self._runner_task
            except asyncio.CancelledError:
                pass
        self._runner_task = None


class GnaniPrismaSTTFactory:
    """Factory creating Prisma v3.0 streams bound to application settings."""

    def __init__(
        self,
        settings: Settings,
        connect_fn: Callable[..., Any] = websockets.connect,
    ) -> None:
        self._settings = settings
        self._connect_fn = connect_fn

    def create_stream(
        self,
        *,
        language_code: LanguageCode,
        session_id: str,
        turn_id: str,
    ) -> GnaniPrismaSTTStream:
        return GnaniPrismaSTTStream(
            self._settings,
            language_code=language_code,
            session_id=session_id,
            turn_id=turn_id,
            connect_fn=self._connect_fn,
        )
