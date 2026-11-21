"""Unified custom asyncio TaskGroup session orchestrator for /server + /client."""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import os
import re
import time
import uuid
from datetime import date
from typing import Any, Literal, Protocol

from PIL import Image

from server.config import ServerSettings
from server.ledger import (
    EntryItemInput,
    Ledger,
    LedgerError,
    LedgerEventInput,
    LedgerEventType,
    seed_demo_data,
)
from server.nlu.dates import resolve_date
from server.nlu.items import extract_items_with_groq_fallback
from server.nlu.numbers import parse_amount
from server.protocol import (
    ActionMsg,
    BillAckMsg,
    BillDoneMsg,
    BillEndMsg,
    BillErrorMsg,
    BillGetDoneMsg,
    BillGetMsg,
    BillMetaMsg,
    BillStartMsg,
    CreditItemMsg,
    CustomerConsentMsg,
    CustomerConsentResultMsg,
    CustomerCreateMsg,
    CustomerCreateResultMsg,
    CustomerQueryMsg,
    CustomerResultMsg,
    EntryItemsResultMsg,
    EntryItemsSetMsg,
    ErrorMsg,
    EventDeleteMsg,
    EventDeleteResultMsg,
    EventReverseMsg,
    EventReverseResultMsg,
    FlushMsg,
    InjectTextMsg,
    InterruptMsg,
    LedgerMsg,
    LedgerRowMsg,
    LedgerSummaryMsg,
    PaymentItemMsg,
    PttDownMsg,
    PttUpMsg,
    ReadyMsg,
    ReceivedQueryMsg,
    ReceivedResultMsg,
    ResetDemoMsg,
    SessionStartMsg,
    ShareLoggedMsg,
    SnapshotMsg,
    SummaryMsg,
    TranscriptMsg,
    TtsEndMsg,
    TurnStartMsg,
    decode_upstream_audio,
    decode_upstream_bill_chunk,
    encode_downstream_audio,
    encode_downstream_bill_chunk,
    parse_client_message,
)
from server.speech.interfaces import STTClient, STTTranscriptEvent, TTSClient
from server.speech.mocks import MockSTT
from server.speech.whisper import transcribe_pcm_with_groq_whisper
from voice_khata import templates
from voice_khata.latency import TurnLatencyTracker
from voice_khata.logging_utils import log_event
from voice_khata.models import (
    CustomerBalance,
    EventType,
    IntentType,
    LanguageCode,
    LedgerEvent,
    ParsedIntent,
    ShopSummary,
)
from voice_khata.nlu import NLUClientProtocol, detect_lending_or_pressure

logger = logging.getLogger("voice_khata.server.session")


def detect_spoken_direction(
    transcript: str, mode_hint: Literal["gave", "got"] | None = None
) -> Literal["gave", "got"] | None:
    """Detect whether spoken/typed utterance specifies a credit ('got') vs debit ('gave').

    Rule:
    - CREDIT (Money Received / Inflow / 'You Got'): credit, credited, jama, paid, payment, received, mila, aaya, etc.
    - DEBIT (Money Given / Udhaar / Outflow / 'You Gave'): debit, debited, udhaar, likh do, ko diya, lent, gave, saala, appu, etc.
    """
    lower = transcript.casefold()

    debit_indicators = (
        "debit",
        "debited",
        "debits",
        "डेबिट",
        "udhar",
        "udhaar",
        "उधार",
        "khata",
        "खाता",
        "lent",
        "lend",
        "ko diya",
        "ko diye",
        "को दिया",
        "को दिए",
        "gave",
        "likh do",
        "likho",
        "लिखो",
        "लिख दो",
        "saala",
        "ಸಾಲ",
        "appu",
        "అప్పు",
        "icchanu",
        "ఇచ్చాను",
        "due",
        "charge",
    )
    credit_indicators = (
        "credit",
        "credited",
        "credits",
        "क्रेडिट",
        "jama",
        "जमा",
        "paid",
        "payment",
        "पेमेंट",
        "received",
        "got",
        "ne diya",
        "ne diye",
        "ने दिया",
        "ने दिए",
        "ne pay",
        "mil gaye",
        "mil gaya",
        "mila",
        "mile",
        "मिला",
        "मिले",
        "aaya",
        "aaye",
        "aayi",
        "आया",
        "आए",
        "vasool",
        "वसूल",
        "pavati",
        "ಪಾವತಿ",
        "vacchindi",
        "వచ్చింది",
        "deposit",
        "deposited",
    )

    has_debit = any(k in lower for k in debit_indicators)
    has_credit = any(k in lower for k in credit_indicators)

    # Hindi case-marker parsing:
    # "Customer ne [amount] diya/diye" -> Customer gave us money -> Credit / You Got
    padded = f" {lower} "
    if not has_credit and (" ne " in padded or " ने " in padded) and any(
        v in lower for v in ("diya", "diye", "दिया", "दिए", "pay", "bheja", "भेजा", "jama")
    ):
        has_credit = True

    # "Customer ko [amount] diya/diye" -> We gave customer goods/credit -> Debit / You Gave
    if not has_debit and (" ko " in padded or " को " in padded) and any(
        v in lower for v in ("diya", "diye", "दिया", "दिए", "bheja", "भेजा", "udhaar", "udhar")
    ):
        has_debit = True

    if has_debit and not has_credit:
        return "gave"
    if has_credit and not has_debit:
        return "got"
    if has_debit and has_credit:
        if any(
            u in lower
            for u in (
                "udhaar",
                "udhar",
                "उधार",
                "debit",
                "debited",
                "डेबिट",
                "likh do",
                "likho",
                "लिखो",
                "लिख दो",
            )
        ):
            return "gave"
        return "got"
    return mode_hint


class RawWebSocketTransport(Protocol):
    async def send_text(self, data: str) -> None: ...
    async def send_bytes(self, data: bytes) -> None: ...


class UnifiedSessionOrchestrator:
    """Custom asyncio.TaskGroup orchestrator bridging /client, /server/speech, /server/nlu, and /server/ledger."""

    def __init__(
        self,
        *,
        transport: RawWebSocketTransport,
        ledger: Ledger,
        nlu: NLUClientProtocol | None,
        stt: STTClient,
        tts: TTSClient,
        session_id: str | None = None,
        initial_lang: LanguageCode = LanguageCode.HI_IN,
        tts_cache: dict[tuple[str, str], tuple[int, list[bytes]]] | None = None,
        settings: ServerSettings | None = None,
    ) -> None:
        self.session_id = session_id or uuid.uuid4().hex[:12]
        self._settings = settings or ServerSettings()
        self._transport = transport
        self._ledger = ledger
        self._nlu = nlu
        self._stt = stt
        self._tts = tts
        self._tts_cache = tts_cache or {}

        self.language = initial_lang
        self._turn_id = 0
        self._current_mode_hint: Literal["gave", "got"] | None = None
        self._ai_disclosed_langs: set[LanguageCode] = set()
        self._pending_disambiguation: dict[str, Any] | None = None
        self._pending_clarification: dict[str, Any] | None = None
        self._active_bill_upload: dict[str, Any] | None = None
        self._bill_upload_timestamps: list[float] = []
        self._outbound: asyncio.Queue[tuple[str, Any] | None] = asyncio.Queue(maxsize=512)
        self._tg: asyncio.TaskGroup | None = None
        self._active_response_task: asyncio.Task[None] | None = None
        self._stt_reader_task: asyncio.Task[None] | None = None
        self._fallback_task: asyncio.Task[None] | None = None
        self._active_latency: TurnLatencyTracker | None = None
        self._turn_pcm_frames: list[bytes] = []
        self._current_turn_final_received: bool = False
        self._stt_opened = False
        self._tts_opened = False
        self._closed = False

    def _next_turn_id(self) -> int:
        self._turn_id += 1
        return self._turn_id

    async def _send_model(self, model: Any) -> None:
        if self._closed:
            return
        await self._outbound.put(("text", model.model_dump_json()))

    async def _send_audio(self, turn_id: int, pcm_bytes: bytes) -> None:
        if self._closed or not pcm_bytes:
            return
        await self._outbound.put(("bytes", encode_downstream_audio(turn_id, pcm_bytes)))

    async def _writer_loop(self) -> None:
        try:
            while True:
                item = await self._outbound.get()
                if item is None:
                    break
                kind, payload = item
                try:
                    if kind == "text":
                        await self._transport.send_text(payload)
                    else:
                        await self._transport.send_bytes(payload)
                except Exception:
                    # Client disconnected while outbound frames were queued
                    self._closed = True
                    break
        except asyncio.CancelledError:
            raise

    async def _push_ledger(self) -> None:
        balances = await self._ledger.balances_all()
        stats = await self._ledger.summary_stats()
        payments = await self._ledger.payments_list()
        credits = await self._ledger.credits_list()

        rows = [
            LedgerRowMsg(
                customer_id=b.customer.id,
                name=b.customer.name,
                script=b.customer.script,
                balance_paise=b.balance_paise,
                balance_inr=b.balance_inr,
                item_count=getattr(b, "item_count", 0),
                has_bill=getattr(b, "has_bill", False),
                has_consent=getattr(b, "has_consent", False),
            )
            for b in balances
        ]
        payment_msgs = [
            PaymentItemMsg(
                seq=p.seq,
                event_id=p.event_id,
                customer_id=p.customer_id,
                customer_name=p.customer_name,
                amount_paise=p.amount_paise,
                amount_inr=p.amount_inr,
                effective_date=p.effective_date,
                ts=p.ts,
                formatted_time=p.formatted_time,
                note=p.note,
                items=[it.model_dump() if hasattr(it, "model_dump") else it for it in getattr(p, "items", [])],
                bills=[b.model_dump() if hasattr(b, "model_dump") else b for b in getattr(p, "bills", [])],
                has_bill=len(getattr(p, "bills", [])) > 0 or getattr(p, "has_bill", False),
                bill_id=(p.bills[0].id if getattr(p, "bills", None) and len(p.bills) > 0 else getattr(p, "bill_id", None)),
                bill_mime=(p.bills[0].mime if getattr(p, "bills", None) and len(p.bills) > 0 else getattr(p, "bill_mime", None)),
            )
            for p in payments
        ]
        credit_msgs = [
            CreditItemMsg(
                seq=c.seq,
                event_id=c.event_id,
                customer_id=c.customer_id,
                customer_name=c.customer_name,
                amount_paise=c.amount_paise,
                amount_inr=c.amount_inr,
                effective_date=c.effective_date,
                ts=c.ts,
                formatted_time=c.formatted_time,
                note=c.note,
                items=[it.model_dump() if hasattr(it, "model_dump") else it for it in getattr(c, "items", [])],
                bills=[b.model_dump() if hasattr(b, "model_dump") else b for b in getattr(c, "bills", [])],
                has_bill=len(getattr(c, "bills", [])) > 0 or getattr(c, "has_bill", False),
                bill_id=(c.bills[0].id if getattr(c, "bills", None) and len(c.bills) > 0 else getattr(c, "bill_id", None)),
                bill_mime=(c.bills[0].mime if getattr(c, "bills", None) and len(c.bills) > 0 else getattr(c, "bill_mime", None)),
            )
            for c in credits
        ]
        summary_msg = LedgerSummaryMsg(
            debtor_count=stats.debtor_count,
            total_owed_paise=stats.total_owed_paise,
            total_owed=stats.total_owed,
            total_received_today_paise=stats.total_received_today_paise,
            total_received_today=stats.total_received_today,
        )
        await self._send_model(
            LedgerMsg(
                rows=rows,
                summary=summary_msg,
                payments=payment_msgs,
                credits=credit_msgs,
            )
        )


    async def _cancel_active_response(self, *, emit_flush: bool) -> None:
        task = self._active_response_task
        await self._tts.cancel()
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._active_response_task = None
        if emit_flush:
            await self._send_model(FlushMsg(turn_id=self._turn_id))

    def _spawn_response(self, coro: Any, *, name: str) -> None:
        if self._tg is not None:
            self._active_response_task = self._tg.create_task(coro, name=name)
        else:
            self._active_response_task = asyncio.create_task(coro, name=name)

    async def _ensure_speech_open(self) -> None:
        if not self._tts_opened:
            try:
                await self._tts.open(self.language.value)
                self._tts_opened = True
            except Exception as exc:
                log_event(
                    logger,
                    "session.tts_prewarm_failed",
                    level=logging.WARNING,
                    session_id=self.session_id,
                    error=str(exc),
                )
        if not self._stt_opened:
            try:
                stt_lang = self.language.value
                await self._stt.open(stt_lang)
                self._stt_opened = True
                if self._tg is not None:
                    self._stt_reader_task = self._tg.create_task(
                        self._stt_event_loop(), name=f"stt-events-{self.session_id}"
                    )
            except Exception as exc:
                log_event(
                    logger,
                    "session.stt_prewarm_failed",
                    level=logging.WARNING,
                    session_id=self.session_id,
                    error=str(exc),
                )

    async def _stt_event_loop(self) -> None:
        try:
            async for ev in self._stt.events():
                await self._handle_stt_event(ev)
        except asyncio.CancelledError:
            raise

    async def _handle_stt_event(self, ev: STTTranscriptEvent) -> None:
        if ev.kind == "partial":
            if self._active_latency is not None:
                self._active_latency.mark_once("stt_first_partial")
            await self._send_model(
                TranscriptMsg(
                    kind="partial",
                    text=ev.text,
                    confidence=ev.confidence,
                    turn_id=self._turn_id,
                )
            )
            return

        # Final transcript from STT
        if self._current_turn_final_received:
            return

        final_text = ev.text.strip()
        # If Gnani STT returned empty or failed, immediately attempt Whisper fallback with recorded pcm
        if not final_text and not isinstance(self._stt, MockSTT):
            recorded_pcm = b"".join(self._turn_pcm_frames)
            key_val = self._settings.groq_api_key.get_secret_value()
            if key_val and len(recorded_pcm) >= 3200:
                fallback_text = await transcribe_pcm_with_groq_whisper(
                    recorded_pcm,
                    key_val,
                    sample_rate=16000,
                    language_hint=self.language.value,
                )
                if fallback_text:
                    final_text = fallback_text.strip()

        self._current_turn_final_received = True
        if self._fallback_task is not None and not self._fallback_task.done():
            self._fallback_task.cancel()
        self._fallback_task = None

        if ev.language_code:
            self.language = LanguageCode.normalize(ev.language_code, default=self.language)
        turn_id = self._next_turn_id()
        latency = self._active_latency or TurnLatencyTracker(
            session_id=self.session_id, turn_id=f"t{turn_id:03d}"
        )
        self._active_latency = None
        latency.mark("stt_final", transcript_len=len(final_text))

        await self._send_model(
            TranscriptMsg(
                kind="final",
                text=final_text,
                confidence=ev.confidence or 0.95,
                turn_id=turn_id,
            )
        )
        if not final_text:
            # Always release client from PROCESSING back to READY when no speech was heard
            await self._send_model(TtsEndMsg(turn_id=turn_id))
            return

        await self._cancel_active_response(emit_flush=False)
        self._spawn_response(
            self._handle_utterance_turn(final_text, turn_id=turn_id, latency=latency),
            name=f"turn-{self.session_id}-{turn_id}",
        )

    async def _check_and_run_fallback_whisper(self, target_turn_id: int, pcm_bytes: bytes) -> None:
        try:
            # Allow Gnani STT up to 1.0s to deliver final transcript
            await asyncio.sleep(1.0)
            if self._current_turn_final_received:
                return

            key_val = self._settings.groq_api_key.get_secret_value()
            if not key_val or len(pcm_bytes) < 3200:
                if not self._current_turn_final_received:
                    self._current_turn_final_received = True
                    turn_id = self._next_turn_id()
                    await self._send_model(TranscriptMsg(kind="final", text="", turn_id=turn_id))
                    await self._send_model(TtsEndMsg(turn_id=turn_id))
                return

            log_event(
                logger,
                "stt.fallback_invoked",
                session_id=self.session_id,
                pcm_len=len(pcm_bytes),
            )
            transcript = await transcribe_pcm_with_groq_whisper(
                pcm_bytes,
                key_val,
                sample_rate=16000,
                language_hint=self.language.value,
            )
            if self._current_turn_final_received:
                return

            self._current_turn_final_received = True
            turn_id = self._next_turn_id()
            latency = self._active_latency or TurnLatencyTracker(
                session_id=self.session_id, turn_id=f"t{turn_id:03d}"
            )
            self._active_latency = None

            if transcript and transcript.strip():
                clean_text = transcript.strip()
                latency.mark("stt_final", transcript_len=len(clean_text), source="groq_whisper")
                await self._send_model(
                    TranscriptMsg(
                        kind="final",
                        text=clean_text,
                        confidence=0.98,
                        turn_id=turn_id,
                    )
                )
                await self._cancel_active_response(emit_flush=False)
                self._spawn_response(
                    self._handle_utterance_turn(clean_text, turn_id=turn_id, latency=latency),
                    name=f"turn-{self.session_id}-{turn_id}",
                )
            else:
                await self._send_model(TranscriptMsg(kind="final", text="", turn_id=turn_id))
                await self._send_model(TtsEndMsg(turn_id=turn_id))
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log_event(logger, "stt.fallback_exception", level=logging.WARNING, error=str(exc))

    async def run(self, inbound_stream: Any) -> None:
        """Run the WebSocket session inside asyncio.TaskGroup."""
        log_event(logger, "server.session.started", session_id=self.session_id)
        try:
            async with asyncio.TaskGroup() as tg:
                self._tg = tg
                writer = tg.create_task(self._writer_loop(), name=f"writer-{self.session_id}")

                async for item in inbound_stream:
                    await self.handle_inbound(item)

                await self._cancel_active_response(emit_flush=False)
                if self._stt_reader_task is not None and not self._stt_reader_task.done():
                    self._stt_reader_task.cancel()
                self._closed = True
                await self._outbound.put(None)
                await writer
        except asyncio.CancelledError:
            await self._cancel_active_response(emit_flush=False)
            raise
        finally:
            self._closed = True
            self._active_bill_upload = None
            self._tg = None
            await self._stt.close()
            await self._tts.close()
            log_event(logger, "server.session.ended", session_id=self.session_id)

    async def handle_inbound(self, item: bytes | str) -> None:
        if isinstance(item, bytes):
            pcm = decode_upstream_audio(item)
            if pcm is not None:
                self._turn_pcm_frames.append(pcm)
                if self._stt_opened:
                    if self._active_latency is not None:
                        self._active_latency.mark_once("audio_first_chunk", byte_len=len(pcm))
                    await self._stt.send_audio(pcm)
                return
            bill_chunk = decode_upstream_bill_chunk(item)
            if bill_chunk is not None:
                upload_id, chunk = bill_chunk
                await self._handle_bill_chunk(upload_id, chunk)
                return
            return

        try:
            msg = parse_client_message(item)
        except Exception as exc:
            await self._send_model(ErrorMsg(message=f"Invalid protocol message: {exc}"))
            return

        if isinstance(msg, SessionStartMsg):
            if msg.lang:
                self.language = LanguageCode.normalize(msg.lang, default=self.language)
            await self._ensure_speech_open()
            await self._send_model(
                ReadyMsg(
                    session_id=self.session_id,
                    turn_id=self._turn_id,
                    sample_rate=self._tts.sample_rate,
                    lang=self.language.value,
                )
            )
            summary_res = await self._ledger.summary()
            cust_bals = await self._ledger.balances_all()
            recent_evs = await self._ledger.recent_events(limit=50)
            await self._send_model(
                SnapshotMsg(
                    summary=summary_res.model_dump(),
                    customers=[
                        {
                            "id": b.customer.id,
                            "name": b.customer.name,
                            "script": b.customer.script,
                            "balance_paise": b.balance_paise,
                            "balance_inr": b.balance_inr,
                        }
                        for b in cust_bals
                    ],
                    recent_events=recent_evs,
                )
            )
            await self._push_ledger()
            # First turn mandatory AI disclosure in the user's language
            await self._speak_ai_greeting()

        elif isinstance(msg, InterruptMsg):
            await self._cancel_active_response(emit_flush=True)

        elif isinstance(msg, PttDownMsg):
            await self._cancel_active_response(emit_flush=False)
            if self._fallback_task is not None and not self._fallback_task.done():
                self._fallback_task.cancel()
            self._fallback_task = None
            self._turn_pcm_frames = []
            self._current_turn_final_received = False
            if msg.lang:
                self.language = LanguageCode.normalize(msg.lang, default=self.language)
            if msg.mode is not None:
                self._current_mode_hint = msg.mode
            if not self._stt_opened:
                await self._ensure_speech_open()
            if isinstance(self._stt, MockSTT):
                self._stt.set_clip_id(msg.clip_id)
            self._active_latency = TurnLatencyTracker(
                session_id=self.session_id, turn_id=f"t{self._turn_id + 1:03d}"
            )

        elif isinstance(msg, PttUpMsg):
            if self._active_latency is not None:
                self._active_latency.mark("ptt_up")
            if self._stt_opened:
                await self._stt.end_utterance()
            recorded_pcm = b"".join(self._turn_pcm_frames)
            target_turn_id = self._turn_id + 1
            if self._tg is not None and len(recorded_pcm) >= 3200 and not isinstance(self._stt, MockSTT):
                self._fallback_task = self._tg.create_task(
                    self._check_and_run_fallback_whisper(target_turn_id, recorded_pcm),
                    name=f"whisper-fallback-{self.session_id}-{target_turn_id}",
                )

        elif isinstance(msg, InjectTextMsg):
            self._current_turn_final_received = False
            self._turn_pcm_frames = []
            if self._fallback_task is not None and not self._fallback_task.done():
                self._fallback_task.cancel()
            self._fallback_task = None
            if msg.lang:
                self.language = LanguageCode.normalize(msg.lang, default=self.language)
            if msg.mode is not None:
                self._current_mode_hint = msg.mode
            if isinstance(self._stt, MockSTT):
                self._stt.inject_text(msg.text)
                if not self._stt_opened:
                    await self._ensure_speech_open()
                await self._stt.end_utterance()
            else:
                turn_id = self._next_turn_id()
                latency = TurnLatencyTracker(session_id=self.session_id, turn_id=f"t{turn_id:03d}")
                await self._send_model(
                    TranscriptMsg(kind="final", text=msg.text, confidence=1.0, turn_id=turn_id)
                )
                await self._cancel_active_response(emit_flush=False)
                self._spawn_response(
                    self._handle_utterance_turn(msg.text, turn_id=turn_id, latency=latency),
                    name=f"turn-{self.session_id}-{turn_id}",
                )

        elif isinstance(msg, ResetDemoMsg):
            self._pending_disambiguation = None
            await self._cancel_active_response(emit_flush=True)
            await self._ledger.wipe_all()
            profile_name = getattr(msg, "profile", "demo_full")
            await seed_demo_data(self._ledger, profile=profile_name, force=True)
            summary_res = await self._ledger.summary()
            await self._send_model(
                SummaryMsg(
                    owing_count=summary_res.owing_count,
                    total_owed_paise=summary_res.total_owed_paise,
                    total_owed=summary_res.total_owed,
                    total_advance_paise=summary_res.total_advance_paise,
                    total_advance=summary_res.total_advance,
                    advance_count=summary_res.advance_count,
                )
            )
            await self._push_ledger()

        elif isinstance(msg, ReceivedQueryMsg):
            from_val = msg.date if msg.date is not None else msg.from_date
            to_val = msg.date if msg.date is not None else msg.to_date
            res = await self._ledger.received(from_val, to_val)
            await self._send_model(
                ReceivedResultMsg(
                    range_total_paise=res.range_total_paise,
                    range_total=res.range_total,
                    days=[d.model_dump() for d in res.days],
                )
            )

        elif isinstance(msg, CustomerQueryMsg):
            try:
                res = await self._ledger.customer_timeline(msg.id)
                await self._send_model(
                    CustomerResultMsg(
                        customer_id=res.customer_id,
                        customer_name=res.customer_name,
                        balance_paise=res.balance_paise,
                        balance=res.balance,
                        advance_paise=res.advance_paise,
                        advance=res.advance,
                        is_advance=res.is_advance,
                        timeline=[e.model_dump() for e in res.events],
                    )
                )
            except LedgerError as exc:
                await self._send_model(ErrorMsg(message=str(exc)))

        elif isinstance(msg, EntryItemsSetMsg):
            await self._handle_entry_items_set(msg)

        elif isinstance(msg, BillStartMsg):
            await self._handle_bill_start(msg)

        elif isinstance(msg, BillEndMsg):
            await self._handle_bill_end(msg)

        elif isinstance(msg, BillGetMsg):
            await self._handle_bill_get(msg)

        elif isinstance(msg, CustomerConsentMsg):
            await self._handle_customer_consent(msg)

        elif isinstance(msg, ShareLoggedMsg):
            await self._handle_share_logged(msg)

        elif isinstance(msg, EventDeleteMsg):
            await self._handle_event_delete(msg)

        elif isinstance(msg, EventReverseMsg):
            await self._handle_event_reverse(msg)

        elif isinstance(msg, CustomerCreateMsg):
            await self._handle_customer_create(msg)

    async def _handle_customer_create(self, msg: CustomerCreateMsg) -> None:
        try:
            clean_name = msg.name.strip()
            if not clean_name:
                await self._send_model(
                    CustomerCreateResultMsg(
                        status="error",
                        error="Customer name cannot be empty",
                    )
                )
                return

            matches = await self._ledger.customers_matching(clean_name)
            if matches:
                cust = matches[0]
            else:
                cust = await self._ledger.add_customer(clean_name)

            ev_id: str | None = None
            if msg.initial_credit_paise and msg.initial_credit_paise > 0:
                ev = await self._ledger.post_event(
                    LedgerEventInput(
                        type=LedgerEventType.CREDIT_GIVEN,
                        customer_id=cust.id,
                        amount_paise=msg.initial_credit_paise,
                        note=msg.note or "Initial Udhar entry",
                        session_scope=self.session_id,
                    )
                )
                ev_id = ev.event_id

            await self._send_model(
                CustomerCreateResultMsg(
                    status="success",
                    customer_id=cust.id,
                    customer_name=cust.name,
                    event_id=ev_id,
                )
            )
            await self._push_ledger()
        except Exception as exc:
            await self._send_model(
                CustomerCreateResultMsg(
                    status="error",
                    error=str(exc),
                )
            )

    async def _handle_event_delete(self, msg: EventDeleteMsg) -> None:
        try:
            success = await self._ledger.delete_event(msg.event_id)
            if success:
                if hasattr(self, "_last_event") and self._last_event and getattr(self._last_event, "event_id", None) == msg.event_id:
                    self._last_event = None
                await self._send_model(
                    EventDeleteResultMsg(event_id=msg.event_id, status="success")
                )
                await self._push_ledger()
            else:
                await self._send_model(
                    EventDeleteResultMsg(
                        event_id=msg.event_id,
                        status="error",
                        error="Transaction not found or could not be deleted",
                    )
                )
        except Exception as exc:
            await self._send_model(
                EventDeleteResultMsg(
                    event_id=msg.event_id,
                    status="error",
                    error=str(exc),
                )
            )

    async def _handle_event_reverse(self, msg: EventReverseMsg) -> None:
        try:
            rev = await self._ledger.reverse(
                msg.event_id,
                reason=msg.reason,
                session_scope=self.session_id,
            )
            if hasattr(self, "_last_event") and self._last_event and getattr(self._last_event, "event_id", None) == msg.event_id:
                self._last_event = None
            await self._send_model(
                EventReverseResultMsg(event_id=msg.event_id, status="success")
            )
            await self._push_ledger()
        except Exception as exc:
            await self._send_model(
                EventReverseResultMsg(
                    event_id=msg.event_id,
                    status="error",
                    error=str(exc),
                )
            )

    async def _handle_bill_start(self, msg: BillStartMsg) -> None:
        now = time.monotonic()
        self._bill_upload_timestamps = [t for t in self._bill_upload_timestamps if now - t < 60.0]
        if len(self._bill_upload_timestamps) >= 10:
            await self._send_model(
                BillErrorMsg(
                    upload_id=msg.upload_id,
                    code="RATE_LIMIT",
                    message="Max 10 bill uploads per minute exceeded",
                )
            )
            return

        if self._active_bill_upload is not None:
            await self._send_model(
                BillErrorMsg(
                    upload_id=msg.upload_id,
                    code="CONCURRENT_UPLOAD",
                    message="Another bill upload is already active",
                )
            )
            return

        if msg.size > 5 * 1024 * 1024:
            await self._send_model(
                BillErrorMsg(
                    upload_id=msg.upload_id,
                    code="OVERSIZE",
                    message="Bill file exceeds 5MB limit",
                )
            )
            return

        if msg.mime not in ("image/jpeg", "image/png", "application/pdf"):
            await self._send_model(
                BillErrorMsg(
                    upload_id=msg.upload_id,
                    code="UNSUPPORTED_MIME",
                    message="Supported file types are JPEG, PNG, and PDF",
                )
            )
            return

        try:
            ev = await self._ledger.get_event_by_id(msg.event_id)
            if ev is None:
                await self._send_model(
                    BillErrorMsg(
                        upload_id=msg.upload_id,
                        code="EVENT_NOT_FOUND",
                        message=f"Event {msg.event_id} not found",
                    )
                )
                return
        except Exception as exc:
            await self._send_model(
                BillErrorMsg(upload_id=msg.upload_id, code="DB_ERROR", message=str(exc))
            )
            return

        self._bill_upload_timestamps.append(now)
        self._active_bill_upload = {
            "upload_id": msg.upload_id,
            "event_id": msg.event_id,
            "mime": msg.mime,
            "size": msg.size,
            "sha256": msg.sha256.lower(),
            "chunks": [],
            "received_bytes": 0,
        }

    async def _handle_bill_chunk(self, upload_id: int, chunk: bytes) -> None:
        if self._active_bill_upload is None or self._active_bill_upload["upload_id"] != upload_id:
            return

        self._active_bill_upload["received_bytes"] += len(chunk)
        if self._active_bill_upload["received_bytes"] > 5 * 1024 * 1024:
            self._active_bill_upload = None
            await self._send_model(
                BillErrorMsg(
                    upload_id=upload_id,
                    code="OVERSIZE",
                    message="Bill file exceeded 5MB limit during upload",
                )
            )
            return

        self._active_bill_upload["chunks"].append(chunk)
        await self._send_model(
            BillAckMsg(upload_id=upload_id, received_bytes=self._active_bill_upload["received_bytes"])
        )

    async def _handle_bill_end(self, msg: BillEndMsg) -> None:
        if self._active_bill_upload is None or self._active_bill_upload["upload_id"] != msg.upload_id:
            await self._send_model(
                BillErrorMsg(upload_id=msg.upload_id, code="NO_ACTIVE_UPLOAD", message="No active upload")
            )
            return

        upload = self._active_bill_upload
        self._active_bill_upload = None
        raw_bytes = b"".join(upload["chunks"])

        calc_sha = hashlib.sha256(raw_bytes).hexdigest().lower()
        if calc_sha != upload["sha256"]:
            await self._send_model(
                BillErrorMsg(
                    upload_id=msg.upload_id,
                    code="HASH_MISMATCH",
                    message="SHA-256 checksum mismatch",
                )
            )
            return

        mime = upload["mime"]
        if mime == "image/jpeg":
            if not raw_bytes.startswith(b"\xff\xd8\xff"):
                await self._send_model(
                    BillErrorMsg(
                        upload_id=msg.upload_id,
                        code="INVALID_MAGIC",
                        message="Invalid JPEG magic bytes",
                    )
                )
                return
        elif mime == "image/png":
            if not raw_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
                await self._send_model(
                    BillErrorMsg(
                        upload_id=msg.upload_id,
                        code="INVALID_MAGIC",
                        message="Invalid PNG magic bytes",
                    )
                )
                return
        elif mime == "application/pdf":
            if not raw_bytes.startswith(b"%PDF-"):
                await self._send_model(
                    BillErrorMsg(
                        upload_id=msg.upload_id,
                        code="INVALID_MAGIC",
                        message="Invalid PDF magic bytes",
                    )
                )
                return

        clean_bytes = raw_bytes
        if mime in ("image/jpeg", "image/png"):
            try:
                img = Image.open(io.BytesIO(raw_bytes))
                buf = io.BytesIO()
                if mime == "image/jpeg":
                    img.convert("RGB").save(buf, format="JPEG", quality=85)
                else:
                    img.save(buf, format="PNG")
                clean_bytes = buf.getvalue()
            except Exception as exc:
                await self._send_model(
                    BillErrorMsg(
                        upload_id=msg.upload_id,
                        code="INVALID_IMAGE",
                        message=f"Failed to process image: {exc}",
                    )
                )
                return

        try:
            bill_rec = await self._ledger.attach_bill(upload["event_id"], mime, clean_bytes)
            await self._send_model(
                BillDoneMsg(upload_id=msg.upload_id, bill_id=bill_rec.id, event_id=upload["event_id"])
            )
            await self._push_ledger()
        except Exception as exc:
            await self._send_model(
                BillErrorMsg(upload_id=msg.upload_id, code="STORAGE_ERROR", message=str(exc))
            )

    async def _handle_bill_get(self, msg: BillGetMsg) -> None:
        try:
            bill_rec, file_bytes = await self._ledger.get_bill_by_id(msg.bill_id)
            await self._send_model(
                BillMetaMsg(
                    transfer_id=msg.transfer_id,
                    bill_id=bill_rec.id,
                    mime=bill_rec.mime,
                    size=len(file_bytes),
                    sha256=bill_rec.sha256,
                )
            )
            CHUNK_SIZE = 64 * 1024
            for offset in range(0, len(file_bytes), CHUNK_SIZE):
                chunk = file_bytes[offset : offset + CHUNK_SIZE]
                down_frame = encode_downstream_bill_chunk(msg.transfer_id, chunk)
                await self._outbound.put(("bytes", down_frame))
            await self._send_model(
                BillGetDoneMsg(transfer_id=msg.transfer_id, bill_id=bill_rec.id)
            )
        except Exception as exc:
            await self._send_model(ErrorMsg(message=f"Failed to fetch bill: {exc}"))

    async def _handle_entry_items_set(self, msg: EntryItemsSetMsg) -> None:
        try:
            domain_items = [
                EntryItemInput(name=it.name, qty_text=it.qty_text, amount_inr=it.amount_inr)
                for it in msg.items
            ]
            updated = await self._ledger.set_event_items(msg.event_id, domain_items)
            await self._send_model(
                EntryItemsResultMsg(
                    event_id=msg.event_id,
                    status="success",
                    items=[it.model_dump() for it in updated],
                )
            )
            await self._push_ledger()
        except Exception as exc:
            await self._send_model(
                EntryItemsResultMsg(event_id=msg.event_id, status="error", error=str(exc))
            )

    async def _handle_customer_consent(self, msg: CustomerConsentMsg) -> None:
        try:
            await self._ledger.record_consent(msg.customer_id)
            await self._send_model(
                CustomerConsentResultMsg(customer_id=msg.customer_id, has_consent=True)
            )
            await self._push_ledger()
        except Exception as exc:
            await self._send_model(ErrorMsg(message=f"Failed to record consent: {exc}"))

    async def _handle_share_logged(self, msg: ShareLoggedMsg) -> None:
        try:
            await self._ledger.record_share_logged(
                msg.customer_id, msg.event_id, channel=msg.channel
            )
            await self._push_ledger()
        except Exception as exc:
            await self._send_model(ErrorMsg(message=f"Failed to record share log: {exc}"))

    async def _speak_ai_greeting(self) -> None:
        turn_id = self._next_turn_id()
        latency = TurnLatencyTracker(session_id=self.session_id, turn_id=f"t{turn_id:03d}")
        greeting = templates.render_ai_greeting(self.language)
        self._ai_disclosed_langs.add(self.language)
        self._spawn_response(
            self._speak_text(greeting, turn_id=turn_id, latency=latency),
            name=f"greeting-{self.session_id}-{turn_id}",
        )

    def _try_resolve_pending_disambiguation(self, transcript: str) -> Any | None:
        """Resolve a follow-up answer like 'Ramesh Kumar', 'Sharma', 'first', or 'second'."""
        pending = self._pending_disambiguation
        if not pending:
            return None
        candidates = pending["candidates"]
        cleaned = transcript.strip().casefold()

        # Check ordinal selection ("first"/"pehla"/"1", "second"/"doosra"/"2")
        if any(w in cleaned for w in ("first", "1st", "pehla", "pehle", "पहला", "पहले", "एक")):
            return candidates[0]
        if len(candidates) > 1 and any(
            w in cleaned for w in ("second", "2nd", "doosra", "dusra", "दूसरा", "दो")
        ):
            return candidates[1]

        # Check phonetic / token overlap against each candidate's distinctive tokens (e.g. "Kumar" / "Sharma")
        from server.ledger.crypto import phonetic_key

        spoken_tokens = set(phonetic_key(transcript).split())
        if not spoken_tokens:
            return None

        best = []
        for cand in candidates:
            cand_tokens = set(cand.name_norm.split())
            # Count matching tokens, giving extra weight if all candidate tokens match or distinctive surname matches
            common = spoken_tokens & cand_tokens
            if common:
                best.append((len(common), cand))

        if not best:
            return None
        best.sort(key=lambda pair: pair[0], reverse=True)
        if len(best) == 1 or best[0][0] > best[1][0]:
            return best[0][1]
        return None

    def _try_resolve_pending_clarification(self, transcript: str) -> int | None:
        """Resolve user answer to item sum mismatch question.

        e.g., 'Items ka total 330 hai, 340 nahi. Kaun sa sahi?'
        Answers could be:
        - '330' / 'items wala' / 'saman' / 'pehla' -> items_sum (330)
        - '340' / 'total wala' / 'doosra' -> stated_total (340)
        """
        pending = self._pending_clarification
        if not pending:
            return None
        items_sum = pending["items_sum"]
        stated_total = pending["stated_total"]
        cleaned = transcript.strip().casefold()

        det = parse_amount(transcript)
        if det == items_sum:
            return items_sum
        if det == stated_total:
            return stated_total

        if str(items_sum) in cleaned:
            return items_sum
        if str(stated_total) in cleaned:
            return stated_total

        if any(w in cleaned for w in ("item", "saman", "सामान", "pehla", "pehle", "पहला", "first", "1st")):
            return items_sum
        if any(w in cleaned for w in ("total", "doosra", "dusra", "दूसरा", "second", "2nd")):
            return stated_total

        return None

    async def _extract_intent_with_deterministic_nlu(
        self, transcript: str, lang: LanguageCode
    ) -> tuple[ParsedIntent, str]:
        """Extract intent via Gemini (if available) or deterministic fallback,
        always overriding amount with deterministic server.nlu.numbers.parse_amount
        and resolving effective_date via server.nlu.dates.resolve_date.
        """
        today = date.today()
        eff_date = resolve_date(transcript, today) or today
        det_amount = parse_amount(transcript)

        if det_amount is None:
            m_total = re.search(
                r"\b(?:kul|total|sab milakar|mothamu|ottu)\b",
                transcript,
                re.I,
            )
            if m_total:
                det_amount = parse_amount(transcript[m_total.start():])
                if det_amount is None:
                    m_val = re.search(
                        r"\b(?:kul|total|sab milakar|mothamu|ottu)\s+([^\s,;।\n]+)",
                        transcript,
                        re.I,
                    )
                    if m_val:
                        det_amount = parse_amount(m_val.group(1))

        if det_amount is None:
            from server.nlu.items import extract_items_rule_based

            items_pre = extract_items_rule_based(transcript)
            if items_pre:
                det_amount = sum(it.amount_inr for it in items_pre)

        if detect_lending_or_pressure(transcript):
            return (
                ParsedIntent(intent=IntentType.UNSUPPORTED_LENDING, language_code=lang),
                eff_date.isoformat(),
            )

        parsed: ParsedIntent | None = None
        if self._nlu is not None:
            try:
                parsed = await self._nlu.extract_intent(
                    transcript, default_language=lang, awaiting_confirmation=False
                )
            except Exception as exc:
                log_event(
                    logger,
                    "server.nlu.gemini_fallback",
                    level=logging.WARNING,
                    session_id=self.session_id,
                    error=str(exc),
                )

        if parsed is None:
            parsed = self._deterministic_intent_fallback(transcript, lang, det_amount)

        # Hard rule: deterministic Indian number parser takes precedence over LLM amount
        final_amount = det_amount if det_amount is not None else parsed.amount_rupees
        return (
            ParsedIntent(
                intent=parsed.intent,
                customer_name=parsed.customer_name,
                amount_rupees=final_amount,
                item_note=parsed.item_note,
                language_code=parsed.language_code,
                confidence=parsed.confidence,
            ),
            eff_date.isoformat(),
        )

    @staticmethod
    def _deterministic_intent_fallback(
        transcript: str, lang: LanguageCode, det_amount: int | None
    ) -> ParsedIntent:
        lower = transcript.casefold()
        words = transcript.strip().split()
        stop = {
            "wrong", "entry", "cancel", "undo", "void", "last", "nahi", "please",
            "add", "record", "credit", "payment", "paid", "total", "balance", "kul",
            "baaki", "kitna", "hai", "ke", "ka", "ki", "ne", "ko", "udhaar", "jama",
        }
        cand_name: str | None = None
        for idx, w in enumerate(words):
            cleaned = w.strip(".,!?")
            if cleaned and cleaned.casefold() not in stop and not cleaned[0].isdigit():
                if (
                    idx + 1 < len(words)
                    and words[idx + 1].strip(".,!?").casefold() not in stop
                    and not words[idx + 1][0].isdigit()
                    and words[idx + 1][0].isupper()
                ):
                    cand_name = f"{cleaned} {words[idx + 1].strip('.,!?')}"
                else:
                    cand_name = cleaned
                break

        if any(k in lower for k in ("undo", "void", "cancel", "wrong entry", "रिवर्स", "रद्द")):
            return ParsedIntent(
                intent=IntentType.UNDO_LAST,
                customer_name=cand_name,
                amount_rupees=det_amount,
                language_code=lang,
            )
        spoken_dir = detect_spoken_direction(transcript)
        if spoken_dir == "got":
            return ParsedIntent(
                intent=IntentType.RECORD_PAYMENT,
                customer_name=cand_name,
                amount_rupees=det_amount,
                language_code=lang,
            )
        if spoken_dir == "gave":
            return ParsedIntent(
                intent=IntentType.RECORD_CREDIT,
                customer_name=cand_name,
                amount_rupees=det_amount,
                language_code=lang,
            )
        if any(k in lower for k in ("kul", "total", "summary", "कुल", "ಒಟ್ಟು")):
            return ParsedIntent(intent=IntentType.QUERY_TOTAL, language_code=lang)
        if any(k in lower for k in ("baaki", "balance", "kitna", "बाकी", "बैलेंस", "ಬಾಕಿ")):
            return ParsedIntent(
                intent=IntentType.QUERY_BALANCE, customer_name=cand_name, language_code=lang
            )
        if any(k in lower for k in ("jama", "paid", "diye", "diya", "payment", "credit", "credited", "जमा", "दिए", "ಪಾವತಿ")):
            return ParsedIntent(
                intent=IntentType.RECORD_PAYMENT,
                customer_name=cand_name,
                amount_rupees=det_amount,
                language_code=lang,
            )
        if det_amount is not None:
            return ParsedIntent(
                intent=IntentType.RECORD_CREDIT,
                customer_name=cand_name,
                amount_rupees=det_amount,
                language_code=lang,
            )
        return ParsedIntent(intent=IntentType.UNKNOWN, language_code=lang)

    async def _handle_utterance_turn(
        self, transcript: str, *, turn_id: int, latency: TurnLatencyTracker
    ) -> None:
        try:
            latency.mark("nlu_start")
            if self._pending_clarification is not None:
                chosen_amt = self._try_resolve_pending_clarification(transcript)
                if chosen_amt is not None:
                    pending = self._pending_clarification
                    self._pending_clarification = None
                    cust = pending["customer"]
                    eff_date = pending["effective_date"]
                    items_to_save = pending["items"] if chosen_amt == pending["items_sum"] else []
                    chosen_paise = chosen_amt * 100
                    rec = await self._ledger.post_event(
                        LedgerEventInput(
                            type=LedgerEventType.CREDIT_GIVEN,
                            customer_id=cust.id,
                            amount_paise=chosen_paise,
                            effective_date=eff_date,
                            note=pending["item_note"] or None,
                            session_scope=self.session_id,
                            items=items_to_save,
                        )
                    )
                    new_bal = await self._ledger.balance(cust.id)
                    bal_model = CustomerBalance(
                        customer_id=0,
                        customer_name=cust.name,
                        balance_rupees=new_bal // 100,
                        event_count=1,
                    )
                    response_text = templates.render_credit_recorded(
                        self.language, cust.name, chosen_amt, bal_model
                    )
                    action_msg = ActionMsg(
                        turn_id=turn_id,
                        type="CREDIT",
                        customer=cust.name,
                        amount_paise=chosen_paise,
                        amount_inr=chosen_amt,
                        effective_date=eff_date,
                        note=pending["item_note"] or "Credit given",
                        status="Recorded in You Gave (उधार दिया)",
                        event_id=rec.event_id,
                        items=[it.model_dump() for it in rec.items],
                        items_sum_paise=pending["items_sum"] * 100 if items_to_save else None,
                        items_sum=pending["items_sum"] if items_to_save else None,
                        stated_total_paise=chosen_paise,
                        stated_total=chosen_amt,
                    )
                    await self._send_model(action_msg)
                    summary_res = await self._ledger.summary()
                    await self._send_model(
                        SummaryMsg(
                            owing_count=summary_res.owing_count,
                            total_owed=summary_res.total_owed,
                            total_advance=summary_res.total_advance,
                            advance_count=summary_res.advance_count,
                        )
                    )
                    await self._push_ledger()
                    await self._speak_text(response_text, turn_id=turn_id, latency=latency)
                    return
                elif any(w in transcript.casefold() for w in ("cancel", "nahi", "नहीं", "radd")):
                    self._pending_clarification = None
                    response_text = templates.render_action_cancelled(self.language)
                    await self._send_model(
                        ActionMsg(turn_id=turn_id, type="CANCEL", status="cancelled")
                    )
                    await self._speak_text(response_text, turn_id=turn_id, latency=latency)
                    return

            resolved_cust = self._try_resolve_pending_disambiguation(transcript)
            if resolved_cust is not None and self._pending_disambiguation is not None:
                pending = self._pending_disambiguation
                self._pending_disambiguation = None
                parsed = ParsedIntent(
                    intent=pending["intent"],
                    customer_name=resolved_cust.name,
                    amount_rupees=pending["amount_inr"],
                    item_note=pending["item_note"],
                    language_code=self.language,
                )
                eff_date = pending["effective_date"]
            else:
                self._pending_disambiguation = None
                parsed, eff_date = await self._extract_intent_with_deterministic_nlu(
                    transcript, self.language
                )
            latency.mark("nlu_end", intent=parsed.intent.value)

            direction_overridden = False
            if parsed.intent in (IntentType.RECORD_CREDIT, IntentType.RECORD_PAYMENT):
                spoken_dir = detect_spoken_direction(transcript)
                mode_hint = self._current_mode_hint
                if spoken_dir is not None:
                    final_dir = spoken_dir
                    if mode_hint is not None and mode_hint != spoken_dir:
                        direction_overridden = True
                elif mode_hint is not None:
                    final_dir = mode_hint
                    direction_overridden = False
                else:
                    final_dir = (
                        "got" if parsed.intent == IntentType.RECORD_PAYMENT else "gave"
                    )
                    direction_overridden = False

                target_intent = (
                    IntentType.RECORD_PAYMENT if final_dir == "got" else IntentType.RECORD_CREDIT
                )
                if target_intent != parsed.intent:
                    parsed = ParsedIntent(
                        intent=target_intent,
                        customer_name=parsed.customer_name,
                        amount_rupees=parsed.amount_rupees,
                        item_note=parsed.item_note,
                        language_code=parsed.language_code,
                        confidence=parsed.confidence,
                    )

            lang = parsed.language_code or self.language
            self.language = lang
            if hasattr(self._tts, "set_language"):
                self._tts.set_language(lang.value)

            response_text = ""
            action_msg: ActionMsg | None = None
            ledger_changed = False

            match parsed.intent:
                case IntentType.UNSUPPORTED_LENDING:
                    response_text = templates.render_unsupported_lending(lang)
                    action_msg = ActionMsg(
                        turn_id=turn_id, type="UNSUPPORTED_LENDING", status="declined"
                    )

                case IntentType.RECORD_CREDIT | IntentType.RECORD_PAYMENT:
                    missing: list[str] = []
                    if not parsed.customer_name:
                        missing.append("customer_name")
                    if parsed.amount_rupees is None:
                        missing.append("amount_rupees")
                    if missing:
                        response_text = templates.render_missing_details(lang, parsed.intent, missing)
                        action_msg = ActionMsg(
                            turn_id=turn_id,
                            type=parsed.intent.value,
                            customer=parsed.customer_name,
                            amount_inr=parsed.amount_rupees,
                            status=f"missing: {', '.join(missing)}",
                            direction_overridden=direction_overridden,
                        )
                    else:
                        assert parsed.customer_name is not None and parsed.amount_rupees is not None
                        latency.mark("db_start", op=parsed.intent.value)
                        matches = (
                            [resolved_cust]
                            if resolved_cust is not None
                            else await self._ledger.customers_matching(parsed.customer_name)
                        )
                        if len(matches) > 1:
                            names = ", ".join(m.name for m in matches)
                            self._pending_disambiguation = {
                                "intent": parsed.intent,
                                "amount_inr": parsed.amount_rupees,
                                "effective_date": eff_date,
                                "item_note": parsed.item_note,
                                "candidates": matches,
                            }
                            response_text = (
                                f"Two matching customers found: {names}. Which one should I record {parsed.amount_rupees} rupees for?"
                                if lang == LanguageCode.EN_IN
                                else f"दो ग्राहक मिले: {names}। {parsed.amount_rupees} रुपये किसके खाते में लिखने हैं?"
                            )
                            action_msg = ActionMsg(
                                turn_id=turn_id,
                                type="DISAMBIGUATE",
                                customer=names,
                                amount_inr=parsed.amount_rupees,
                                effective_date=eff_date,
                                status="Say full name (e.g. Ramesh Kumar or Ramesh Sharma)",
                                direction_overridden=direction_overridden,
                                candidates=[m.name for m in matches],
                            )
                            latency.mark("db_end", op="disambiguate")
                        else:
                            cust = (
                                matches[0]
                                if len(matches) == 1
                                else await self._ledger.add_customer(parsed.customer_name)
                            )
                            ev_type = (
                                LedgerEventType.CREDIT_GIVEN
                                if parsed.intent == IntentType.RECORD_CREDIT
                                else LedgerEventType.PAYMENT_RECEIVED
                            )

                            items_to_record: list[EntryItemInput] = []
                            if ev_type == LedgerEventType.CREDIT_GIVEN:
                                raw_key = self._settings.groq_api_key
                                groq_key = (
                                    raw_key.get_secret_value()
                                    if hasattr(raw_key, "get_secret_value")
                                    else (str(raw_key) if raw_key else os.getenv("GROQ_API_KEY"))
                                )
                                extracted_items = await extract_items_with_groq_fallback(
                                    transcript, cust.name, groq_api_key=groq_key
                                )
                                if extracted_items:
                                    items_sum = sum(it.amount_inr for it in extracted_items)
                                    if parsed.amount_rupees is not None and items_sum != parsed.amount_rupees:
                                        self._pending_clarification = {
                                            "customer": cust,
                                            "items": extracted_items,
                                            "items_sum": items_sum,
                                            "stated_total": parsed.amount_rupees,
                                            "effective_date": eff_date,
                                            "item_note": parsed.item_note,
                                        }
                                        question_text = templates.render_item_sum_mismatch(
                                            lang, items_sum, parsed.amount_rupees
                                        )
                                        action_msg = ActionMsg(
                                            turn_id=turn_id,
                                            type="CLARIFICATION_REQUIRED",
                                            customer=cust.name,
                                            amount_paise=parsed.amount_rupees * 100,
                                            amount_inr=parsed.amount_rupees,
                                            status="Awaiting clarification",
                                            items=[it.model_dump() for it in extracted_items],
                                            items_sum_paise=items_sum * 100,
                                            items_sum=items_sum,
                                            stated_total_paise=parsed.amount_rupees * 100,
                                            stated_total=parsed.amount_rupees,
                                            question=question_text,
                                        )
                                        await self._send_model(action_msg)
                                        await self._speak_text(question_text, turn_id=turn_id, latency=latency)
                                        return
                                    else:
                                        items_to_record = extracted_items
                                        if parsed.amount_rupees is None:
                                            parsed = ParsedIntent(
                                                intent=parsed.intent,
                                                customer_name=parsed.customer_name,
                                                amount_rupees=items_sum,
                                                item_note=parsed.item_note,
                                                language_code=parsed.language_code,
                                            )

                            amt_paise = (parsed.amount_rupees or 0) * 100
                            rec = await self._ledger.post_event(
                                LedgerEventInput(
                                    type=ev_type,
                                    customer_id=cust.id,
                                    amount_paise=amt_paise,
                                    effective_date=eff_date,
                                    note=parsed.item_note or None,
                                    session_scope=self.session_id,
                                    items=items_to_record,
                                )
                            )
                            new_bal = await self._ledger.balance(cust.id)
                            latency.mark("db_end", op=ev_type.value)
                            ledger_changed = True

                            bal_model = CustomerBalance(
                                customer_id=0,
                                customer_name=cust.name,
                                balance_rupees=new_bal // 100,
                                event_count=1,
                            )
                            if ev_type == LedgerEventType.CREDIT_GIVEN:
                                response_text = templates.render_credit_recorded(
                                    lang, cust.name, parsed.amount_rupees or 0, bal_model
                                )
                            else:
                                response_text = templates.render_payment_recorded(
                                    lang, cust.name, parsed.amount_rupees or 0, bal_model
                                )
                            is_corr = any(
                                k in transcript.casefold()
                                for k in ("nahi nahi", "wrong", "correction", "galat")
                            )
                            status_desc = (
                                "Recorded in You Got (जमा मिला)"
                                if ev_type == LedgerEventType.PAYMENT_RECEIVED
                                else "Recorded in You Gave (उधार दिया)"
                            )
                            action_msg = ActionMsg(
                                turn_id=turn_id,
                                type=ev_type.value,
                                customer=cust.name,
                                customer_id=cust.id,
                                amount_paise=amt_paise,
                                amount_inr=parsed.amount_rupees,
                                effective_date=eff_date,
                                note=parsed.item_note
                                or (
                                    "Credit received"
                                    if ev_type == LedgerEventType.PAYMENT_RECEIVED
                                    else "Credit given"
                                ),
                                status=status_desc,
                                correction_applied=is_corr,
                                direction_overridden=direction_overridden,
                                event_id=rec.event_id,
                                items=[it.model_dump() for it in rec.items],
                                items_sum_paise=sum(it.amount_paise for it in rec.items) if rec.items else None,
                                items_sum=sum(it.amount_inr for it in rec.items) if rec.items else None,
                                stated_total_paise=amt_paise,
                                stated_total=parsed.amount_rupees,
                            )

                case IntentType.UNDO_LAST:
                    latency.mark("db_start", op="void_last")
                    target = await self._ledger.last_event(self.session_id, active_only=True)
                    if target is None:
                        target = await self._ledger.last_event(None, active_only=True)
                    if target is None:
                        latency.mark("db_end", op="nothing_to_void")
                        response_text = templates.render_nothing_to_undo(lang, parsed.customer_name)
                    else:
                        await self._ledger.reverse(target.event_id, session_scope=self.session_id)
                        cust = await self._ledger.get_customer(target.customer_id)
                        cust_name = cust.name if cust else target.customer_id
                        new_bal = await self._ledger.balance(target.customer_id)
                        latency.mark("db_end", op="voided")
                        ledger_changed = True
                        orig_ev = LedgerEvent(
                            id=target.seq,
                            event_uid=target.event_id,
                            created_at_utc=target.ts,
                            created_at_mono_ns=0,
                            customer_id=0,
                            customer_name=cust_name,
                            event_type=(
                                EventType.CREDIT_GIVEN
                                if target.type in (LedgerEventType.CREDIT_GIVEN, LedgerEventType.CREDIT)
                                else EventType.PAYMENT_RECEIVED
                            ),
                            signed_amount_rupees=(
                                target.amount_inr
                                if target.type in (LedgerEventType.CREDIT_GIVEN, LedgerEventType.CREDIT)
                                else -target.amount_inr
                            ),
                            language_code=lang,
                        )
                        bal_model = CustomerBalance(
                            customer_id=0,
                            customer_name=cust_name,
                            balance_rupees=new_bal // 100,
                            event_count=1,
                        )
                        response_text = templates.render_reversal_recorded(lang, orig_ev, bal_model)
                        action_msg = ActionMsg(
                            turn_id=turn_id,
                            type="VOID",
                            customer=cust_name,
                            amount_paise=target.amount_paise,
                            amount_inr=target.amount_inr,
                            effective_date=target.effective_date,
                            status="voided",
                            correction_applied=True,
                        )

                case IntentType.QUERY_BALANCE:
                    if not parsed.customer_name:
                        response_text = templates.render_missing_details(
                            lang, IntentType.QUERY_BALANCE, ["customer_name"]
                        )
                    else:
                        latency.mark("db_start", op="query_balance")
                        matches = (
                            [resolved_cust]
                            if resolved_cust is not None
                            else await self._ledger.customers_matching(parsed.customer_name)
                        )
                        if not matches:
                            latency.mark("db_end", op="not_found")
                            response_text = templates.render_customer_not_found(
                                lang, parsed.customer_name
                            )
                        elif len(matches) > 1:
                            latency.mark("db_end", op="disambiguate")
                            names = ", ".join(m.name for m in matches)
                            self._pending_disambiguation = {
                                "intent": IntentType.QUERY_BALANCE,
                                "amount_inr": None,
                                "effective_date": eff_date,
                                "item_note": "",
                                "candidates": matches,
                            }
                            response_text = (
                                f"Two matching customers found: {names}. Which one's balance would you like?"
                                if lang == LanguageCode.EN_IN
                                else f"दो ग्राहक मिले: {names}। किसका हिसाब देखना है?"
                            )
                            action_msg = ActionMsg(
                                turn_id=turn_id,
                                type="DISAMBIGUATE",
                                customer=names,
                                status="Say full name (e.g. Ramesh Kumar or Ramesh Sharma)",
                            )
                        else:
                            cust = matches[0]
                            bal = await self._ledger.balance(cust.id)
                            latency.mark("db_end", op="query_balance")
                            bal_model = CustomerBalance(
                                customer_id=0,
                                customer_name=cust.name,
                                balance_rupees=bal // 100,
                                event_count=1,
                            )
                            response_text = templates.render_customer_balance(lang, bal_model)
                            action_msg = ActionMsg(
                                turn_id=turn_id,
                                type="QUERY_BALANCE",
                                customer=cust.name,
                                amount_paise=bal,
                                amount_inr=bal // 100,
                                status="queried",
                            )

                case IntentType.QUERY_TOTAL:
                    latency.mark("db_start", op="query_total")
                    all_bals = await self._ledger.balances_all()
                    total_out = sum(b.balance_inr for b in all_bals)
                    active_cnt = sum(1 for b in all_bals if b.balance_inr > 0)
                    latency.mark("db_end", op="query_total")
                    summary = ShopSummary(
                        total_outstanding_rupees=total_out,
                        customer_count=len(all_bals),
                        active_credit_customers=active_cnt,
                        total_events=len(all_bals),
                    )
                    response_text = templates.render_shop_summary(lang, summary)
                    action_msg = ActionMsg(
                        turn_id=turn_id,
                        type="QUERY_TOTAL",
                        amount_inr=total_out,
                        status="queried",
                    )

                case _:
                    response_text = templates.render_unknown(lang)

            if lang not in self._ai_disclosed_langs:
                response_text = templates.render_ai_disclosure_prefix(lang) + response_text
                self._ai_disclosed_langs.add(lang)

            if action_msg is not None:
                await self._send_model(action_msg)
            if ledger_changed:
                summary_res = await self._ledger.summary()
                await self._send_model(
                    SummaryMsg(
                        owing_count=summary_res.owing_count,
                        total_owed=summary_res.total_owed,
                        total_advance=summary_res.total_advance,
                        advance_count=summary_res.advance_count,
                    )
                )
                await self._push_ledger()

            await self._speak_text(response_text, turn_id=turn_id, latency=latency)
        except asyncio.CancelledError:
            log_event(logger, "server.turn.cancelled", session_id=self.session_id, turn_id=turn_id)
            raise
        except (LedgerError, Exception) as exc:
            log_event(
                logger,
                "server.turn.error",
                level=logging.ERROR,
                session_id=self.session_id,
                turn_id=turn_id,
                error=str(exc),
            )
            await self._send_model(ErrorMsg(message=str(exc)))

    async def _speak_text(
        self, text: str, *, turn_id: int, latency: TurnLatencyTracker
    ) -> None:
        cached = self._tts_cache.get((self.language.value, text))
        sr = cached[0] if cached is not None else self._tts.sample_rate
        await self._send_model(TurnStartMsg(turn_id=turn_id, sample_rate=sr))
        latency.mark("tts_start", cache_hit=cached is not None)
        first = True
        try:
            if cached is not None:
                for pcm_chunk in cached[1]:
                    if first:
                        latency.mark("tts_first_audio_chunk", byte_len=len(pcm_chunk))
                    await self._send_audio(turn_id, pcm_chunk)
                    if first:
                        first = False
                        latency.mark("ws_first_audio_sent")
            else:
                async for pcm_chunk in self._tts.synthesize(text):
                    if first:
                        latency.mark("tts_first_audio_chunk", byte_len=len(pcm_chunk))
                    await self._send_audio(turn_id, pcm_chunk)
                    if first:
                        first = False
                        latency.mark("ws_first_audio_sent")
            latency.mark("tts_end")
        except asyncio.CancelledError:
            latency.mark("tts_cancelled")
            raise
        finally:
            await self._send_model(TtsEndMsg(turn_id=turn_id))
            latency.mark("turn_end")


Session = UnifiedSessionOrchestrator
