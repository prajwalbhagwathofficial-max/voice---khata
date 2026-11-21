"""Groq Whisper STT fallback client using httpx (whisper-large-v3-turbo)."""

from __future__ import annotations

import io
import logging
import wave
from typing import Any

import httpx

from voice_khata.logging_utils import log_event

logger = logging.getLogger("voice_khata.server.speech.whisper")

_GROQ_AUDIO_TRANSCRIPTIONS_URL = "https://api.groq.com/openai/v1/audio/transcriptions"

_LANG_MAP = {
    "hi-IN": "hi",
    "hi": "hi",
    "en-IN": "en",
    "en": "en",
    "te-IN": "te",
    "te": "te",
    "kn-IN": "kn",
    "kn": "kn",
    "ta-IN": "ta",
    "ta": "ta",
}


def pcm16_to_wav_bytes(pcm_bytes: bytes, sample_rate: int = 16000) -> bytes:
    """Pack raw 16-bit signed mono PCM bytes into a valid RIFF/WAVE container."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


async def transcribe_pcm_with_groq_whisper(
    pcm_bytes: bytes,
    api_key: str,
    *,
    sample_rate: int = 16000,
    language_hint: str | None = None,
    http_client: httpx.AsyncClient | None = None,
    model: str = "whisper-large-v3-turbo",
) -> str | None:
    """Transcribe 16 kHz mono PCM16 audio using Groq Whisper.

    Ultra-fast fallback (typically 200-800 ms TTFB on Groq LPUs).
    Returns cleaned transcript string or None on failure/empty audio.
    """
    key = api_key.strip()
    if not key or len(pcm_bytes) < 3200:  # < 100 ms at 16 kHz = 3200 bytes
        return None

    wav_bytes = pcm16_to_wav_bytes(pcm_bytes, sample_rate)

    whisper_lang = _LANG_MAP.get(language_hint or "")
    data: dict[str, Any] = {
        "model": model,
        "response_format": "json",
    }
    if whisper_lang:
        data["language"] = whisper_lang

    files = {"file": ("utterance.wav", wav_bytes, "audio/wav")}
    headers = {"Authorization": f"Bearer {key}"}

    close_client = False
    client = http_client
    if client is None or client.is_closed:
        client = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=3.0))
        close_client = True

    try:
        resp = await client.post(
            _GROQ_AUDIO_TRANSCRIPTIONS_URL,
            headers=headers,
            data=data,
            files=files,
        )
        if resp.status_code == 200:
            payload = resp.json()
            text = str(payload.get("text") or "").strip()
            log_event(
                logger,
                "groq_whisper.success",
                bytes_sent=len(wav_bytes),
                transcript=text,
                language=whisper_lang,
            )
            return text if text else None
        log_event(
            logger,
            "groq_whisper.failed",
            level=logging.WARNING,
            status_code=resp.status_code,
            body=resp.text[:200],
        )
        return None
    except Exception as exc:
        log_event(
            logger,
            "groq_whisper.exception",
            level=logging.WARNING,
            error=str(exc),
        )
        return None
    finally:
        if close_client and client is not None and not client.is_closed:
            await client.aclose()
