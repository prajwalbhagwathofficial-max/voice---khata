"""Speech client interfaces, mocks, and persistent Gnani v3.0 implementations."""

from server.speech.gnani import GnaniSTTClient, GnaniTTSClient, compute_backoff_delay
from server.speech.interfaces import STTClient, STTTranscriptEvent, TTSClient
from server.speech.mocks import MockSTT, MockTTS
from server.speech.whisper import transcribe_pcm_with_groq_whisper

__all__ = [
    "GnaniSTTClient",
    "GnaniTTSClient",
    "MockSTT",
    "MockTTS",
    "STTClient",
    "STTTranscriptEvent",
    "TTSClient",
    "compute_backoff_delay",
    "transcribe_pcm_with_groq_whisper",
]
