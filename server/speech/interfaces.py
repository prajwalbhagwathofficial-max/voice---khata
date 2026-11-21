"""Abstract STTClient and TTSClient interfaces for /server/speech."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class STTTranscriptEvent:
    """Streaming transcript event yielded by STTClient.events()."""

    kind: Literal["partial", "final"]
    text: str
    confidence: float | None = None
    mono_ns: int = 0
    language_code: str | None = None


class STTClient(ABC):
    """Abstract streaming Speech-to-Text client (persistent per session)."""

    @abstractmethod
    async def open(self, lang: str) -> None:
        """Open and pre-warm the STT session for `lang`."""

    @abstractmethod
    async def send_audio(self, pcm_bytes: bytes) -> None:
        """Send 16 kHz mono PCM16 little-endian audio bytes."""

    @abstractmethod
    async def end_utterance(self) -> None:
        """Signal end-of-utterance (ptt.up) to flush the final transcript."""

    @abstractmethod
    def events(self) -> AsyncIterator[STTTranscriptEvent]:
        """Yield streaming (partial | final, text, confidence) transcript events."""

    @abstractmethod
    async def close(self) -> None:
        """Close the STT session cleanly."""


class TTSClient(ABC):
    """Abstract streaming Text-to-Speech client (persistent per session)."""

    @property
    @abstractmethod
    def sample_rate(self) -> int:
        """PCM16 output sample rate in Hz."""

    @abstractmethod
    async def open(self, lang: str) -> None:
        """Open and pre-warm the TTS session for `lang`."""

    @abstractmethod
    def synthesize(self, text: str) -> AsyncIterator[bytes]:
        """Stream raw PCM16 LE chunks for `text`."""

    @abstractmethod
    async def cancel(self) -> None:
        """Stop generation within one chunk and drain without breaking the connection."""

    @abstractmethod
    async def close(self) -> None:
        """Close the TTS session cleanly."""
