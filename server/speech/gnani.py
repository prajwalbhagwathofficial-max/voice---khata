"""Persistent pre-warmed Gnani Prisma v3.0 STT and Timbre v3.0 TTS WebSocket clients.

Features:
- Persistent WebSocket per session, pre-warmed on open(lang) with keepalive pings.
- Auth strictly from .env (X-API-Key-ID, never logged).
- Reconnect with exponential backoff + jitter.
- End-of-utterance flush on end_utterance() (ptt.up).
- Streaming TTS chunks with cancel() stopping within one chunk and draining
  remaining server frames without breaking the persistent socket.
- Per-call monotonic latency logging.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import random
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

import websockets

from server.config import ServerSettings
from server.speech.interfaces import STTClient, STTTranscriptEvent, TTSClient
from voice_khata.gnani_tts import strip_wav_header
from voice_khata.logging_utils import log_event

logger = logging.getLogger("voice_khata.server.speech.gnani")

# 16 kHz mono PCM16: 20 ms = 640 bytes; 1000 ms trailing silence = 50 * 640 bytes for Prisma VAD
_SILENCE_CHUNK = b"\x00" * 640
_FLUSH_SILENCE_CHUNKS = 50


def compute_backoff_delay(
    attempt: int,
    *,
    base_delay_s: float = 0.25,
    max_delay_s: float = 8.0,
    rng: random.Random | None = None,
) -> float:
    """Compute exponential backoff with full jitter."""
    exp = min(max_delay_s, base_delay_s * (2**attempt))
    r = rng.random() if rng is not None else random.random()
    return round(exp * (0.5 + 0.5 * r), 4)


class GnaniSTTClient(STTClient):
    """Persistent pre-warmed Gnani Prisma v3.0 streaming STT WebSocket client."""

    def __init__(
        self,
        settings: ServerSettings,
        *,
        session_id: str = "session",
        connect_fn: Callable[..., Any] = websockets.connect,
        max_reconnect_attempts: int = 3,
        backoff_base_s: float = 0.2,
    ) -> None:
        self._settings = settings
        self._session_id = session_id
        self._connect_fn = connect_fn
        self._max_reconnect_attempts = max_reconnect_attempts
        self._backoff_base_s = backoff_base_s

        self._lang = "hi-IN"
        self._ws: Any | None = None
        self._ws_cm: Any | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._events: asyncio.Queue[STTTranscriptEvent | None] = asyncio.Queue(maxsize=128)
        self._closed = False
        self._send_lock = asyncio.Lock()
        self._call_start_mono_ns: int | None = None
        self._first_partial_logged = False
        self.reconnect_count = 0

    def _headers(self) -> dict[str, str]:
        api_key = self._settings.gnani_api_key.get_secret_value().strip()
        if not api_key:
            raise RuntimeError("GNANI_API_KEY is not configured in .env")
        return {
            "X-API-Key-ID": api_key,
            "model": self._settings.gnani_stt_model,
            "language_code": self._lang,
            "x-sample-rate": "16000",
            "format": "transcribe",
        }

    async def _connect_once(self) -> None:
        t0 = time.monotonic_ns()
        cm = self._connect_fn(
            self._settings.gnani_stt_url,
            additional_headers=self._headers(),
            open_timeout=10,
            ping_interval=20,
            ping_timeout=10,
        )
        ws = await cm.__aenter__()
        self._ws_cm = cm
        self._ws = ws
        log_event(
            logger,
            "gnani_stt.connected",
            session_id=self._session_id,
            language=self._lang,
            model=self._settings.gnani_stt_model,
            connect_ms=round((time.monotonic_ns() - t0) / 1_000_000.0, 2),
        )

    async def _close_socket(self) -> None:
        cm = self._ws_cm
        self._ws_cm = None
        self._ws = None
        if cm is not None:
            try:
                await cm.__aexit__(None, None, None)
            except Exception:
                pass

    async def _connect_with_backoff(self) -> None:
        last_exc: Exception | None = None
        for attempt in range(self._max_reconnect_attempts + 1):
            try:
                await self._connect_once()
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_exc = exc
                if attempt >= self._max_reconnect_attempts:
                    break
                delay = compute_backoff_delay(attempt, base_delay_s=self._backoff_base_s)
                self.reconnect_count += 1
                log_event(
                    logger,
                    "gnani_stt.reconnect_backoff",
                    level=logging.WARNING,
                    session_id=self._session_id,
                    attempt=attempt + 1,
                    delay_s=delay,
                    error=str(exc),
                )
                await asyncio.sleep(delay)
        assert last_exc is not None
        raise last_exc

    async def open(self, lang: str) -> None:
        self._lang = lang
        self._closed = False
        await self._connect_with_backoff()
        self._reader_task = asyncio.create_task(
            self._reader_loop(), name=f"gnani-stt-reader-{self._session_id}"
        )

    async def _reader_loop(self) -> None:
        try:
            while not self._closed:
                ws = self._ws
                if ws is None:
                    break
                try:
                    async for raw in ws:
                        now_ns = time.monotonic_ns()
                        if isinstance(raw, bytes):
                            raw = raw.decode("utf-8", errors="ignore")
                        try:
                            data = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(data, dict):
                            continue

                        msg_type = str(data.get("type") or data.get("event") or "").strip().lower()
                        if msg_type in {"connected", "processing", "vad", "speech_start", "speech_end"}:
                            continue

                        if msg_type == "error":
                            log_event(
                                logger,
                                "gnani_stt.error_frame",
                                level=logging.WARNING,
                                session_id=self._session_id,
                                error=data.get("message"),
                            )
                            await self._events.put(
                                STTTranscriptEvent(
                                    kind="final",
                                    text="",
                                    confidence=0.0,
                                    mono_ns=now_ns,
                                )
                            )
                            continue

                        inner = data.get("data") if isinstance(data.get("data"), dict) else data
                        text = str(
                            inner.get("transcript")
                            or inner.get("text")
                            or data.get("transcript")
                            or data.get("text")
                            or ""
                        ).strip()
                        is_final = bool(
                            inner.get("is_final")
                            or inner.get("final")
                            or data.get("is_final")
                            or data.get("final")
                            or msg_type == "final"
                        )
                        if not text and not is_final:
                            continue

                        conf_raw = inner.get("confidence", data.get("confidence"))
                        conf = float(conf_raw) if isinstance(conf_raw, (int, float)) else None
                        detected_lang = (
                            inner.get("detected_language_code")
                            or data.get("detected_language_code")
                            or inner.get("language_code")
                            or data.get("language_code")
                        )
                        kind = "final" if is_final else "partial"

                        if kind == "partial" and not self._first_partial_logged:
                            self._first_partial_logged = True
                            elapsed = (
                                round((now_ns - self._call_start_mono_ns) / 1_000_000.0, 2)
                                if self._call_start_mono_ns
                                else None
                            )
                            log_event(
                                logger,
                                "gnani_stt.first_partial",
                                mono_ns=now_ns,
                                session_id=self._session_id,
                                elapsed_ms=elapsed,
                            )
                        elif kind == "final":
                            elapsed = (
                                round((now_ns - self._call_start_mono_ns) / 1_000_000.0, 2)
                                if self._call_start_mono_ns
                                else None
                            )
                            log_event(
                                logger,
                                "gnani_stt.final",
                                mono_ns=now_ns,
                                session_id=self._session_id,
                                elapsed_ms=elapsed,
                                confidence=conf,
                                transcript_len=len(text),
                                detected_language=detected_lang,
                            )
                            self._call_start_mono_ns = None
                            self._first_partial_logged = False

                        await self._events.put(
                            STTTranscriptEvent(
                                kind=kind,
                                text=text,
                                confidence=conf,
                                mono_ns=now_ns,
                                language_code=str(detected_lang) if detected_lang else None,
                            )
                        )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if self._closed:
                        break
                    log_event(
                        logger,
                        "gnani_stt.connection_lost",
                        level=logging.WARNING,
                        session_id=self._session_id,
                        error=str(exc),
                    )
                    await self._close_socket()
                    try:
                        await self._connect_with_backoff()
                    except Exception:
                        break
                else:
                    # Clean server close -> reconnect if session is still open
                    if self._closed:
                        break
                    await self._close_socket()
                    try:
                        await self._connect_with_backoff()
                    except Exception:
                        break
        finally:
            await self._events.put(None)

    async def send_audio(self, pcm_bytes: bytes) -> None:
        if self._closed or not pcm_bytes:
            return
        async with self._send_lock:
            if self._call_start_mono_ns is None:
                self._call_start_mono_ns = time.monotonic_ns()
                self._first_partial_logged = False
            if self._ws is None:
                await self._connect_with_backoff()
            try:
                await self._ws.send(pcm_bytes)
            except Exception:
                await self._close_socket()
                await self._connect_with_backoff()
                await self._ws.send(pcm_bytes)

    async def end_utterance(self) -> None:
        """Flush trailing silence and commit frame so Prisma v3.0 VAD emits the final transcript."""
        if self._closed:
            return
        if self._ws is not None:
            try:
                await self._ws.send(json.dumps({"type": "commit"}))
            except Exception:
                pass
        for _ in range(15):
            await self.send_audio(_SILENCE_CHUNK)
            await asyncio.sleep(0.005)
        log_event(logger, "gnani_stt.end_utterance_flushed", session_id=self._session_id)

    async def events(self) -> AsyncIterator[STTTranscriptEvent]:
        while True:
            ev = await self._events.get()
            if ev is None:
                break
            yield ev

    async def close(self) -> None:
        self._closed = True
        if self._reader_task is not None and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        self._reader_task = None
        await self._close_socket()


class GnaniTTSClient(TTSClient):
    """Persistent pre-warmed Gnani Timbre v3.0 streaming TTS WebSocket client.

    Supports cancel() stopping generation within one chunk and draining
    remaining frames from the persistent socket without breaking it.
    """

    def __init__(
        self,
        settings: ServerSettings,
        *,
        session_id: str = "session",
        connect_fn: Callable[..., Any] = websockets.connect,
        max_reconnect_attempts: int = 3,
        backoff_base_s: float = 0.2,
        drain_timeout_s: float = 1.5,
    ) -> None:
        self._settings = settings
        self._session_id = session_id
        self._connect_fn = connect_fn
        self._max_reconnect_attempts = max_reconnect_attempts
        self._backoff_base_s = backoff_base_s
        self._drain_timeout_s = drain_timeout_s

        self._lang = "hi-IN"
        self._sample_rate = settings.gnani_tts_sample_rate
        self._ws: Any | None = None
        self._ws_cm: Any | None = None
        self._cancel_event = asyncio.Event()
        self._in_flight = False
        self._stream_lock = asyncio.Lock()
        self.connect_count = 0
        self.reconnect_count = 0

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    def _headers(self) -> dict[str, str]:
        api_key = self._settings.gnani_api_key.get_secret_value().strip()
        if not api_key:
            raise RuntimeError("GNANI_API_KEY is not configured in .env")
        return {"X-API-Key-ID": api_key}

    async def _connect_once(self) -> None:
        t0 = time.monotonic_ns()
        cm = self._connect_fn(
            self._settings.gnani_tts_url,
            additional_headers=self._headers(),
            open_timeout=10,
            ping_interval=20,
            ping_timeout=10,
        )
        ws = await cm.__aenter__()
        self._ws_cm = cm
        self._ws = ws
        self.connect_count += 1
        log_event(
            logger,
            "gnani_tts.connected",
            session_id=self._session_id,
            language=self._lang,
            voice=self._settings.gnani_tts_voice,
            connect_ms=round((time.monotonic_ns() - t0) / 1_000_000.0, 2),
        )

    async def _close_socket(self) -> None:
        cm = self._ws_cm
        self._ws_cm = None
        self._ws = None
        if cm is not None:
            try:
                await cm.__aexit__(None, None, None)
            except Exception:
                pass

    async def _connect_with_backoff(self) -> None:
        last_exc: Exception | None = None
        for attempt in range(self._max_reconnect_attempts + 1):
            try:
                await self._connect_once()
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_exc = exc
                if attempt >= self._max_reconnect_attempts:
                    break
                delay = compute_backoff_delay(attempt, base_delay_s=self._backoff_base_s)
                self.reconnect_count += 1
                log_event(
                    logger,
                    "gnani_tts.reconnect_backoff",
                    level=logging.WARNING,
                    session_id=self._session_id,
                    attempt=attempt + 1,
                    delay_s=delay,
                    error=str(exc),
                )
                await asyncio.sleep(delay)
        assert last_exc is not None
        raise last_exc

    async def open(self, lang: str) -> None:
        self._lang = lang
        if self._ws is None:
            await self._connect_with_backoff()

    def set_language(self, lang: str) -> None:
        self._lang = lang

    async def _drain_until_final(self) -> None:
        """Drain remaining TTS frames so the persistent WebSocket stays synchronized."""
        ws = self._ws
        if ws is None or not self._in_flight:
            return
        deadline = time.monotonic() + self._drain_timeout_s
        drained = 0
        try:
            while self._in_flight:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    # Server did not finish within drain budget; reset socket cleanly
                    await self._close_socket()
                    self._in_flight = False
                    return
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                drained += 1
                if isinstance(raw, str):
                    try:
                        frame = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    msg_type = str(frame.get("type", "")).lower()
                    inner = frame.get("data") if isinstance(frame.get("data"), dict) else frame
                    if msg_type in {"complete", "done", "end"} or bool(
                        inner.get("is_final", frame.get("is_final", False))
                    ):
                        self._in_flight = False
                        break
        except Exception:
            await self._close_socket()
            self._in_flight = False
        finally:
            log_event(
                logger,
                "gnani_tts.drained",
                session_id=self._session_id,
                drained_frames=drained,
                socket_reused=self._ws is not None,
            )

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        if not text.strip():
            return

        async with self._stream_lock:
            self._cancel_event.clear()
            if self._ws is None:
                await self._connect_with_backoff()

            payload = {
                "text": text,
                "voice": self._settings.gnani_tts_voice,
                "model": self._settings.gnani_tts_model,
                "language": self._lang,
                "speed": 1.0,
                "audio_config": {
                    "sample_rate": self._settings.gnani_tts_sample_rate,
                    "num_channels": 1,
                    "sample_width": 2,
                    "encoding": "linear_pcm",
                    "container": "wav",
                },
            }

            t0 = time.monotonic_ns()
            chunks_yielded = 0
            first_chunk_logged = False

            try:
                for attempt in range(2):
                    if self._ws is None:
                        await self._connect_with_backoff()
                    try:
                        await self._ws.send(json.dumps(payload, ensure_ascii=False))
                    except Exception:
                        await self._close_socket()
                        if attempt == 0:
                            continue
                        raise

                    self._in_flight = True
                    try:
                        while self._in_flight:
                            if self._cancel_event.is_set():
                                await self._drain_until_final()
                                return

                            recv_task = asyncio.create_task(self._ws.recv())
                            cancel_task = asyncio.create_task(self._cancel_event.wait())
                            done, pending = await asyncio.wait(
                                {recv_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED
                            )
                            for p in pending:
                                p.cancel()
                                try:
                                    await p
                                except asyncio.CancelledError:
                                    pass

                            if cancel_task in done:
                                await self._drain_until_final()
                                return

                            raw = recv_task.result()
                            now_ns = time.monotonic_ns()

                            if isinstance(raw, bytes):
                                pcm, self._sample_rate = strip_wav_header(raw, self._sample_rate)
                                if pcm and not self._cancel_event.is_set():
                                    chunks_yielded += 1
                                    if not first_chunk_logged:
                                        first_chunk_logged = True
                                        log_event(
                                            logger,
                                            "gnani_tts.first_chunk",
                                            mono_ns=now_ns,
                                            session_id=self._session_id,
                                            ttfb_ms=round((now_ns - t0) / 1_000_000.0, 2),
                                        )
                                    yield pcm
                                continue

                            try:
                                frame = json.loads(raw)
                            except json.JSONDecodeError:
                                continue
                            if not isinstance(frame, dict):
                                continue

                            msg_type = str(frame.get("type", "")).strip().lower()
                            inner = frame.get("data") if isinstance(frame.get("data"), dict) else frame

                            if msg_type == "error" or frame.get("error") or inner.get("error"):
                                self._in_flight = False
                                raise RuntimeError(
                                    f"Gnani Timbre v3.0 error: {inner.get('error') or frame.get('error')}"
                                )

                            if msg_type in {"complete", "done", "end"}:
                                if chunks_yielded == 0:
                                    # Stale completion frame left over from previous turn on persistent socket
                                    continue
                                self._in_flight = False
                                break

                            audio_b64 = inner.get("audio") or ""
                            is_final = bool(inner.get("is_final", frame.get("is_final", False)))
                            if is_final and not audio_b64 and chunks_yielded == 0:
                                continue
                            if is_final:
                                self._in_flight = False

                            if audio_b64 and not self._cancel_event.is_set():
                                pcm, self._sample_rate = strip_wav_header(
                                    base64.b64decode(audio_b64), self._sample_rate
                                )
                                if pcm:
                                    chunks_yielded += 1
                                    if not first_chunk_logged:
                                        first_chunk_logged = True
                                        log_event(
                                            logger,
                                            "gnani_tts.first_chunk",
                                            mono_ns=now_ns,
                                            session_id=self._session_id,
                                            ttfb_ms=round((now_ns - t0) / 1_000_000.0, 2),
                                        )
                                    yield pcm

                            if is_final:
                                break
                        # Completed cleanly without disconnect
                        break
                    except websockets.ConnectionClosed:
                        self._in_flight = False
                        await self._close_socket()
                        if chunks_yielded == 0 and attempt == 0 and not self._cancel_event.is_set():
                            continue
                        break
            except asyncio.CancelledError:
                self._cancel_event.set()
                await self._drain_until_final()
                raise
            finally:
                log_event(
                    logger,
                    "gnani_tts.call_done",
                    session_id=self._session_id,
                    chunks_yielded=chunks_yielded,
                    total_ms=round((time.monotonic_ns() - t0) / 1_000_000.0, 2),
                    cancelled=self._cancel_event.is_set(),
                )

    async def cancel(self) -> None:
        """Signal immediate cancellation (within one chunk) and drain cleanly."""
        self._cancel_event.set()
        if not self._stream_lock.locked() and self._in_flight:
            await self._drain_until_final()

    async def close(self) -> None:
        self._cancel_event.set()
        await self._close_socket()
