"""Tests for voice_khata.orchestrator (TaskGroup, AI disclosure, confirm/commit, barge-in)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from voice_khata.gnani_stt import STTEvent
from voice_khata.gnani_tts import TTSAudioChunk
from voice_khata.ledger import LedgerStore
from voice_khata.models import IntentType, LanguageCode, ParsedIntent
from voice_khata.orchestrator import SessionOrchestrator


class _RecordingTransport:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self._turn_done_count = 0
        self._turn_done_event = asyncio.Event()

    async def send_json(self, data: dict[str, Any]) -> None:
        self.messages.append(data)
        if data.get("type") == "latency":
            self._turn_done_count += 1
            self._turn_done_event.set()

    async def wait_for_completed_turns(self, count: int, timeout: float = 3.0) -> None:
        deadline = time.monotonic() + timeout
        while self._turn_done_count < count:
            self._turn_done_event.clear()
            if self._turn_done_count >= count:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"Timed out waiting for {count} completed turns (got {self._turn_done_count})"
                )
            await asyncio.wait_for(self._turn_done_event.wait(), timeout=remaining)


class _ScriptedNLU:
    def __init__(self, mapping: dict[str, ParsedIntent]) -> None:
        self._mapping = mapping

    async def extract_intent(
        self,
        transcript: str,
        *,
        default_language: LanguageCode,
        awaiting_confirmation: bool = False,
    ) -> ParsedIntent:
        return self._mapping.get(
            transcript,
            ParsedIntent(intent=IntentType.UNKNOWN, language_code=default_language),
        )


class _FakeSTTStream:
    def __init__(self, events: list[STTEvent]) -> None:
        self._events = list(events)
        self.audio_chunks: list[bytes] = []
        self.flushed = False
        self.closed = False

    async def start(self) -> None:
        return None

    async def send_audio(self, pcm_bytes: bytes) -> None:
        self.audio_chunks.append(pcm_bytes)

    async def flush_utterance(self) -> None:
        self.flushed = True

    async def events(self) -> AsyncIterator[STTEvent]:
        for ev in self._events:
            await asyncio.sleep(0.005)
            yield ev

    async def close(self) -> None:
        self.closed = True


class _FakeSTTFactory:
    def __init__(self, streams: list[_FakeSTTStream]) -> None:
        self._streams = list(streams)

    def create_stream(self, *, language_code: LanguageCode, session_id: str, turn_id: str):
        return self._streams.pop(0)


class _FakeTTS:
    def __init__(self, delay_per_chunk: float = 0.0, num_chunks: int = 2) -> None:
        self._delay = delay_per_chunk
        self._num_chunks = num_chunks
        self.spoken_texts: list[tuple[str, LanguageCode]] = []
        self.cancelled_count = 0

    async def synthesize_stream(
        self,
        *,
        text: str,
        language_code: LanguageCode,
        session_id: str,
        turn_id: str,
    ) -> AsyncIterator[TTSAudioChunk]:
        self.spoken_texts.append((text, language_code))
        try:
            for idx in range(1, self._num_chunks + 1):
                if self._delay:
                    await asyncio.sleep(self._delay)
                yield TTSAudioChunk(
                    seq=idx,
                    pcm_bytes=b"\x01\x00" * 16,
                    sample_rate=48000,
                    is_final=(idx == self._num_chunks),
                    mono_ns=time.monotonic_ns(),
                )
        except asyncio.CancelledError:
            self.cancelled_count += 1
            raise


@pytest.mark.asyncio
async def test_first_turn_discloses_ai_and_commits_credit_then_queries_db(tmp_path: Path) -> None:
    ledger = LedgerStore(tmp_path / "khata.db")
    transport = _RecordingTransport()
    nlu = _ScriptedNLU(
        {
            "Ramesh 250 udhaar": ParsedIntent(
                intent=IntentType.RECORD_CREDIT,
                customer_name="Ramesh",
                amount_rupees=250,
                item_note="sugar",
                language_code=LanguageCode.HI_IN,
            ),
            "हाँ": ParsedIntent(
                intent=IntentType.CONFIRM,
                language_code=LanguageCode.HI_IN,
            ),
            "Ramesh ka baaki kitna hai": ParsedIntent(
                intent=IntentType.QUERY_BALANCE,
                customer_name="Ramesh",
                language_code=LanguageCode.HI_IN,
            ),
        }
    )
    stt_factory = _FakeSTTFactory(
        [
            _FakeSTTStream(
                [
                    STTEvent(transcript="Ramesh", is_final=False, mono_ns=time.monotonic_ns()),
                    STTEvent(
                        transcript="Ramesh 250 udhaar",
                        is_final=True,
                        language_code=LanguageCode.HI_IN,
                        mono_ns=time.monotonic_ns(),
                    ),
                ]
            ),
            _FakeSTTStream(
                [
                    STTEvent(
                        transcript="हाँ",
                        is_final=True,
                        language_code=LanguageCode.HI_IN,
                        mono_ns=time.monotonic_ns(),
                    )
                ]
            ),
            _FakeSTTStream(
                [
                    STTEvent(
                        transcript="Ramesh ka baaki kitna hai",
                        is_final=True,
                        language_code=LanguageCode.HI_IN,
                        mono_ns=time.monotonic_ns(),
                    )
                ]
            ),
        ]
    )
    tts = _FakeTTS()

    orch = SessionOrchestrator(
        transport=transport,
        ledger=ledger,
        nlu=nlu,
        stt_factory=stt_factory,
        tts=tts,
        initial_language=LanguageCode.HI_IN,
    )

    async def inbound():
        yield {"type": "session_start", "language_code": "hi-IN"}
        await transport.wait_for_completed_turns(1)

        yield {"type": "audio_start"}
        yield b"\x00\x01" * 1600
        yield {"type": "audio_end"}
        await transport.wait_for_completed_turns(2)

        yield {"type": "audio_start"}
        yield b"\x00\x01" * 1600
        yield {"type": "audio_end"}
        await transport.wait_for_completed_turns(3)

        yield {"type": "audio_start"}
        yield b"\x00\x01" * 1600
        yield {"type": "audio_end"}
        await transport.wait_for_completed_turns(4)

    await orch.run(inbound())

    # Turn 0 greeting explicitly states AI identity in Hindi
    agent_texts = [m["text"] for m in transport.messages if m["type"] == "agent_text"]
    assert len(agent_texts) == 4
    assert "एआई" in agent_texts[0]
    assert "250" in agent_texts[1]  # confirmation prompt
    assert "250" in agent_texts[2]  # recorded confirmation
    assert "250" in agent_texts[3]  # DB balance query answer

    # Verify ledger committed exactly 1 event with computed balance 250
    bal = await ledger.get_customer_balance("Ramesh")
    assert bal is not None
    assert bal.balance_rupees == 250

    # Verify latency messages were emitted with monotonic timestamps
    latency_msgs = [m for m in transport.messages if m["type"] == "latency"]
    assert len(latency_msgs) >= 3
    assert all("marks_mono_ns" in m for m in latency_msgs)


@pytest.mark.asyncio
async def test_barge_in_cooperatively_cancels_active_tts(tmp_path: Path) -> None:
    ledger = LedgerStore(tmp_path / "khata.db")
    transport = _RecordingTransport()
    nlu = _ScriptedNLU({})
    stt_factory = _FakeSTTFactory([])
    tts = _FakeTTS(delay_per_chunk=0.08, num_chunks=10)

    orch = SessionOrchestrator(
        transport=transport,
        ledger=ledger,
        nlu=nlu,
        stt_factory=stt_factory,
        tts=tts,
        initial_language=LanguageCode.EN_IN,
    )

    async def inbound():
        yield {"type": "session_start", "language_code": "en-IN"}
        await asyncio.sleep(0.05)
        yield {"type": "barge_in"}
        await asyncio.sleep(0.03)

    await orch.run(inbound())

    assert tts.cancelled_count == 1
    assert any(m["type"] == "tts_stop" for m in transport.messages)


@pytest.mark.asyncio
async def test_payment_undo_reversal_and_lending_refusal(tmp_path: Path) -> None:
    ledger = LedgerStore(tmp_path / "khata.db")
    await ledger.record_credit(
        customer_name="Ananya", amount_rupees=500, language_code=LanguageCode.KN_IN
    )

    transport = _RecordingTransport()
    nlu = _ScriptedNLU(
        {
            "Ananya 200 paid": ParsedIntent(
                intent=IntentType.RECORD_PAYMENT,
                customer_name="Ananya",
                amount_rupees=200,
                language_code=LanguageCode.KN_IN,
            ),
            "Undo last": ParsedIntent(
                intent=IntentType.UNDO_LAST,
                customer_name="Ananya",
                language_code=LanguageCode.KN_IN,
            ),
            "Add interest": ParsedIntent(
                intent=IntentType.UNSUPPORTED_LENDING,
                language_code=LanguageCode.EN_IN,
            ),
        }
    )
    stt_factory = _FakeSTTFactory(
        [
            _FakeSTTStream(
                [
                    STTEvent(
                        transcript="Ananya 200 paid",
                        is_final=True,
                        language_code=LanguageCode.KN_IN,
                        mono_ns=time.monotonic_ns(),
                    )
                ]
            ),
            _FakeSTTStream(
                [
                    STTEvent(
                        transcript="Undo last",
                        is_final=True,
                        language_code=LanguageCode.KN_IN,
                        mono_ns=time.monotonic_ns(),
                    )
                ]
            ),
            _FakeSTTStream(
                [
                    STTEvent(
                        transcript="Add interest",
                        is_final=True,
                        language_code=LanguageCode.EN_IN,
                        mono_ns=time.monotonic_ns(),
                    )
                ]
            ),
        ]
    )
    tts = _FakeTTS()

    orch = SessionOrchestrator(
        transport=transport,
        ledger=ledger,
        nlu=nlu,
        stt_factory=stt_factory,
        tts=tts,
        initial_language=LanguageCode.KN_IN,
    )

    async def inbound():
        yield {"type": "session_start", "language_code": "kn-IN"}
        await transport.wait_for_completed_turns(1)

        # Record 200 payment then confirm via UI control
        yield {"type": "audio_start"}
        yield {"type": "audio_end"}
        await transport.wait_for_completed_turns(2)
        yield {"type": "confirm_pending"}
        await transport.wait_for_completed_turns(3)

        # Undo that payment then confirm via UI control
        yield {"type": "audio_start"}
        yield {"type": "audio_end"}
        await transport.wait_for_completed_turns(4)
        yield {"type": "confirm_pending"}
        await transport.wait_for_completed_turns(5)

        # Lending request in English (new language -> must also disclose AI identity!)
        yield {"type": "audio_start"}
        yield {"type": "audio_end"}
        await transport.wait_for_completed_turns(6)

    await orch.run(inbound())

    bal = await ledger.get_customer_balance("Ananya")
    assert bal is not None
    # 500 credit - 200 payment + 200 reversal = 500
    assert bal.balance_rupees == 500
    assert bal.event_count == 3

    agent_texts = [m["text"] for m in transport.messages if m["type"] == "agent_text"]
    assert "ಎಐ" in agent_texts[0]
    assert "AI assistant" in agent_texts[-1]
    assert "does not support lending" in agent_texts[-1]

