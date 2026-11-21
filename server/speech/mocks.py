"""MockSTT and MockTTS selected when USE_MOCKS=true."""

from __future__ import annotations

import asyncio
import json
import math
import time
from array import array
from collections.abc import AsyncIterator
from pathlib import Path

from server.speech.interfaces import STTClient, STTTranscriptEvent, TTSClient

_DEFAULT_CLIP_TRANSCRIPTS: dict[str, str] = {
    "clip_01_ramesh_credit_340": "Ramesh Kumar ke teen sau chaalis rupaye udhaar likh do",
    "clip_02_meena_payment_250": "Meena Devi ne dhai sau rupaye jama kar diye",
    "clip_03_venkatesh_credit_150_te": "వెంకటేష్ ఖాతాలో నూట యాభై రూపాయలు రాయండి",
    "clip_04_suresh_credit_175": "Suresh Patel ke paune do sau rupaye udhaar",
    "clip_05_anil_correction_void": "Wrong entry, cancel Anil last entry",
    "clip_06_lakshmi_correction_350": "Nahi nahi Lakshmi Rao ka saade teen sau rupaye karo",
}


def load_scripted_transcripts(labels_path: Path | None = None) -> dict[str, str]:
    """Load clip_id -> transcript mapping from eval/labels.json if available."""
    mapping = dict(_DEFAULT_CLIP_TRANSCRIPTS)
    candidate = labels_path or Path("eval/labels.json")
    if candidate.exists():
        try:
            raw = json.loads(candidate.read_text(encoding="utf-8"))
            clips = raw.get("clips", []) if isinstance(raw, dict) else raw
            for item in clips:
                if isinstance(item, dict) and item.get("id") and item.get("transcript"):
                    mapping[str(item["id"])] = str(item["transcript"])
        except Exception:
            pass
    return mapping


class MockSTT(STTClient):
    """Returns scripted transcripts keyed by test-clip name or socket-injected text."""

    def __init__(self, transcripts: dict[str, str] | None = None) -> None:
        self._transcripts = (
            dict(transcripts) if transcripts is not None else load_scripted_transcripts()
        )
        self._lang = "hi-IN"
        self._queue: asyncio.Queue[STTTranscriptEvent | None] = asyncio.Queue()
        self._active_clip_id: str | None = None
        self._injected_text: str | None = None
        self._audio_bytes_received = 0
        self._opened = False

    def set_clip_id(self, clip_id: str | None) -> None:
        self._active_clip_id = clip_id

    def inject_text(self, text: str) -> None:
        self._injected_text = text

    async def open(self, lang: str) -> None:
        self._lang = lang
        self._opened = True

    async def send_audio(self, pcm_bytes: bytes) -> None:
        if not self._opened:
            raise RuntimeError("MockSTT must be opened before sending audio")
        self._audio_bytes_received += len(pcm_bytes)

    async def end_utterance(self) -> None:
        text = self._injected_text
        if text is None and self._active_clip_id is not None:
            text = self._transcripts.get(self._active_clip_id)
        if text is None:
            text = "Ramesh Kumar ka baaki kitna hai"

        self._injected_text = None
        self._active_clip_id = None

        words = text.split()
        if len(words) > 2:
            partial = " ".join(words[: max(1, len(words) // 2)])
            await self._queue.put(
                STTTranscriptEvent(
                    kind="partial",
                    text=partial,
                    confidence=0.88,
                    mono_ns=time.monotonic_ns(),
                )
            )
            await asyncio.sleep(0.002)

        await self._queue.put(
            STTTranscriptEvent(
                kind="final",
                text=text,
                confidence=0.98,
                mono_ns=time.monotonic_ns(),
            )
        )

    async def events(self) -> AsyncIterator[STTTranscriptEvent]:
        while True:
            ev = await self._queue.get()
            if ev is None:
                break
            yield ev

    async def close(self) -> None:
        self._opened = False
        await self._queue.put(None)


class MockTTS(TTSClient):
    """Generates short 16 kHz PCM16 tone chunks and stops within one chunk on cancel()."""

    def __init__(
        self,
        *,
        sample_rate: int = 16000,
        chunk_count: int = 8,
        chunk_delay_s: float = 0.005,
        freq_hz: float = 440.0,
    ) -> None:
        self._sample_rate = sample_rate
        self._chunk_count = chunk_count
        self._chunk_delay_s = chunk_delay_s
        self._freq_hz = freq_hz
        self._lang = "hi-IN"
        self._cancelled = False
        self._opened = False

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    async def open(self, lang: str) -> None:
        self._lang = lang
        self._opened = True

    def _make_chunk(self, chunk_index: int) -> bytes:
        frame_samples = (self._sample_rate * 20) // 1000  # 20 ms
        offset = chunk_index * frame_samples
        samples = array(
            "h",
            (
                int(
                    round(
                        math.sin(2.0 * math.pi * self._freq_hz * ((offset + i) / self._sample_rate))
                        * 6500
                    )
                )
                for i in range(frame_samples)
            ),
        )
        return samples.tobytes()

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        if not self._opened:
            await self.open(self._lang)
        self._cancelled = False
        if not text.strip():
            return

        for idx in range(self._chunk_count):
            if self._cancelled:
                break
            if self._chunk_delay_s > 0:
                await asyncio.sleep(self._chunk_delay_s)
            if self._cancelled:
                break
            yield self._make_chunk(idx)

    async def cancel(self) -> None:
        self._cancelled = True

    async def close(self) -> None:
        self._cancelled = True
        self._opened = False
