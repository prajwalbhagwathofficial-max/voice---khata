"""Custom asyncio TaskGroup session orchestrator for Voice Khata."""

from __future__ import annotations

import asyncio
import base64
import logging
import time
import uuid
from typing import Any, Protocol

from voice_khata import templates
from voice_khata.gnani_stt import STTClientFactoryProtocol, STTStreamProtocol
from voice_khata.gnani_tts import TTSClientProtocol
from voice_khata.latency import TurnLatencyTracker
from voice_khata.ledger import LedgerError, LedgerStore
from voice_khata.logging_utils import log_event
from voice_khata.models import (
    IntentType,
    LanguageCode,
    ParsedIntent,
    PendingAction,
    SessionState,
)
from voice_khata.nlu import NLUClientProtocol

logger = logging.getLogger("voice_khata.orchestrator")


class WebSocketTransport(Protocol):
    async def send_json(self, data: dict[str, Any]) -> None: ...


class SessionOrchestrator:
    """Orchestrates one laptop browser voice ledger session over WebSocket.

    Uses asyncio.TaskGroup for structured concurrency, cooperative cancellation
    for barge-in/interrupts, and delegates shielded SQLite transactions to
    LedgerStore.
    """

    def __init__(
        self,
        *,
        transport: WebSocketTransport,
        ledger: LedgerStore,
        nlu: NLUClientProtocol,
        stt_factory: STTClientFactoryProtocol,
        tts: TTSClientProtocol,
        session_id: str | None = None,
        initial_language: LanguageCode = LanguageCode.HI_IN,
    ) -> None:
        self.session_id = session_id or uuid.uuid4().hex[:12]
        self._transport = transport
        self._ledger = ledger
        self._nlu = nlu
        self._stt_factory = stt_factory
        self._tts = tts

        self.language_code = initial_language
        self.state = SessionState.IDLE
        self.ai_disclosed_languages: set[LanguageCode] = set()
        self.pending_action: PendingAction | None = None

        self._outbound_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=256)
        self._turn_counter = 0
        self._active_turn_task: asyncio.Task[None] | None = None
        self._active_stt_stream: STTStreamProtocol | None = None
        self._active_stt_reader: asyncio.Task[None] | None = None
        self._active_stt_latency: TurnLatencyTracker | None = None
        self._active_stt_turn_id: str | None = None
        self._last_partial_transcript: str = ""
        self._tg: asyncio.TaskGroup | None = None
        self._closed = False

    def _next_turn_id(self) -> str:
        self._turn_counter += 1
        return f"t{self._turn_counter:03d}"

    async def _enqueue(self, payload: dict[str, Any]) -> None:
        if self._closed:
            return
        await self._outbound_queue.put(payload)

    async def _set_state(self, new_state: SessionState) -> None:
        self.state = new_state
        await self._enqueue(
            {
                "type": "state",
                "session_id": self.session_id,
                "state": new_state.value,
                "language_code": self.language_code.value,
                "pending_action": (
                    self.pending_action.model_dump(mode="json")
                    if self.pending_action is not None
                    else None
                ),
            }
        )

    async def send_ledger_snapshot(self) -> None:
        summary, balances, recent = await asyncio.gather(
            self._ledger.get_shop_summary(),
            self._ledger.list_customer_balances(),
            self._ledger.list_recent_events(limit=25),
        )
        await self._enqueue(
            {
                "type": "ledger_snapshot",
                "summary": summary.model_dump(mode="json"),
                "balances": [b.model_dump(mode="json") for b in balances],
                "recent_events": [e.model_dump(mode="json") for e in recent],
            }
        )

    async def _outbound_writer_loop(self) -> None:
        try:
            while True:
                msg = await self._outbound_queue.get()
                if msg is None:
                    break
                await self._transport.send_json(msg)
        except asyncio.CancelledError:
            raise

    async def run(self, inbound_messages: Any) -> None:
        """Run the session inside an asyncio.TaskGroup until client disconnects."""
        log_event(
            logger,
            "session.started",
            session_id=self.session_id,
            language_code=self.language_code.value,
        )
        try:
            async with asyncio.TaskGroup() as tg:
                self._tg = tg
                writer_task = tg.create_task(
                    self._outbound_writer_loop(), name=f"ws-writer-{self.session_id}"
                )
                await self._enqueue(
                    {
                        "type": "session_ready",
                        "session_id": self.session_id,
                        "language_code": self.language_code.value,
                    }
                )
                await self.send_ledger_snapshot()

                async for message in inbound_messages:
                    await self.handle_inbound(message)

                await self._cancel_active_stt()
                await self._cancel_active_turn()
                self._closed = True
                await self._outbound_queue.put(None)
                await writer_task
        except asyncio.CancelledError:
            await self._cancel_active_stt()
            await self._cancel_active_turn()
            raise
        finally:
            self._closed = True
            self._tg = None
            log_event(logger, "session.ended", session_id=self.session_id)

    async def _cancel_active_turn(self) -> None:
        task = self._active_turn_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._active_turn_task = None

    async def _cancel_active_stt(self) -> None:
        reader = self._active_stt_reader
        stream = self._active_stt_stream
        self._active_stt_reader = None
        self._active_stt_stream = None
        if stream is not None:
            await stream.close()
        if reader is not None and not reader.done():
            reader.cancel()
            try:
                await reader
            except asyncio.CancelledError:
                pass

    def _spawn_turn(self, coro: Any, *, name: str) -> asyncio.Task[None]:
        if self._tg is not None:
            task = self._tg.create_task(coro, name=name)
        else:
            task = asyncio.create_task(coro, name=name)
        self._active_turn_task = task
        return task

    async def handle_inbound(self, message: dict[str, Any] | bytes) -> None:
        """Dispatch inbound binary PCM audio or JSON control frames."""
        if isinstance(message, bytes):
            if self._active_stt_stream is not None:
                if self._active_stt_latency is not None:
                    self._active_stt_latency.mark_once("audio_first_chunk", byte_len=len(message))
                await self._active_stt_stream.send_audio(message)
            return

        msg_type = str(message.get("type", "")).strip()

        if msg_type == "session_start":
            lang = LanguageCode.normalize(
                message.get("language_code"), default=self.language_code
            )
            self.language_code = lang
            await self.send_ledger_snapshot()
            await self._start_greeting_turn(lang)

        elif msg_type == "set_language":
            lang = LanguageCode.normalize(
                message.get("language_code"), default=self.language_code
            )
            self.language_code = lang
            await self._set_state(
                SessionState.AWAITING_CONFIRMATION
                if self.pending_action is not None
                else SessionState.IDLE
            )
            if lang not in self.ai_disclosed_languages:
                await self._start_greeting_turn(lang)

        elif msg_type == "barge_in":
            await self._cancel_active_turn()
            await self._enqueue({"type": "tts_stop", "session_id": self.session_id})
            await self._set_state(
                SessionState.AWAITING_CONFIRMATION
                if self.pending_action is not None
                else SessionState.IDLE
            )

        elif msg_type == "audio_start":
            await self._handle_audio_start()

        elif msg_type == "audio_end":
            await self._handle_audio_end()

        elif msg_type == "confirm_pending":
            await self._cancel_active_stt()
            await self._cancel_active_turn()
            turn_id = self._next_turn_id()
            latency = TurnLatencyTracker(session_id=self.session_id, turn_id=turn_id)
            self._spawn_turn(
                self._execute_intent_turn(
                    ParsedIntent(
                        intent=IntentType.CONFIRM,
                        language_code=self.language_code,
                        confidence=1.0,
                    ),
                    transcript="[UI Confirm]",
                    turn_id=turn_id,
                    latency=latency,
                ),
                name=f"turn-{self.session_id}-{turn_id}",
            )

        elif msg_type == "cancel_pending":
            await self._cancel_active_stt()
            await self._cancel_active_turn()
            turn_id = self._next_turn_id()
            latency = TurnLatencyTracker(session_id=self.session_id, turn_id=turn_id)
            self._spawn_turn(
                self._execute_intent_turn(
                    ParsedIntent(
                        intent=IntentType.CANCEL,
                        language_code=self.language_code,
                        confidence=1.0,
                    ),
                    transcript="[UI Cancel]",
                    turn_id=turn_id,
                    latency=latency,
                ),
                name=f"turn-{self.session_id}-{turn_id}",
            )

        elif msg_type == "refresh_ledger":
            await self.send_ledger_snapshot()

    async def _start_greeting_turn(self, lang: LanguageCode) -> None:
        await self._cancel_active_turn()
        turn_id = self._next_turn_id()
        latency = TurnLatencyTracker(session_id=self.session_id, turn_id=turn_id)
        greeting_text = templates.render_ai_greeting(lang)
        self.ai_disclosed_languages.add(lang)
        self._spawn_turn(
            self._speak_response(
                text=greeting_text,
                language_code=lang,
                turn_id=turn_id,
                latency=latency,
                is_greeting=True,
            ),
            name=f"greeting-{self.session_id}-{turn_id}",
        )

    async def _handle_audio_start(self) -> None:
        # Barge-in: cooperatively cancel any ongoing agent TTS/turn
        await self._cancel_active_turn()
        await self._enqueue({"type": "tts_stop", "session_id": self.session_id})
        await self._cancel_active_stt()

        turn_id = self._next_turn_id()
        latency = TurnLatencyTracker(session_id=self.session_id, turn_id=turn_id)
        self._active_stt_turn_id = turn_id
        self._active_stt_latency = latency
        self._last_partial_transcript = ""

        stream = self._stt_factory.create_stream(
            language_code=self.language_code,
            session_id=self.session_id,
            turn_id=turn_id,
        )
        try:
            await stream.start()
            latency.mark("stt_connect")
        except Exception as exc:
            log_event(
                logger,
                "orchestrator.stt_start_failed",
                level=logging.ERROR,
                session_id=self.session_id,
                turn_id=turn_id,
                error=str(exc),
            )
            await self._enqueue(
                {
                    "type": "error",
                    "session_id": self.session_id,
                    "message": "Unable to connect to Gnani Prisma v3.0 STT. Check GNANI_API_KEY in .env.",
                }
            )
            await self._set_state(SessionState.IDLE)
            return

        self._active_stt_stream = stream
        await self._set_state(SessionState.LISTENING)

        reader_coro = self._consume_stt_events(stream, turn_id=turn_id, latency=latency)
        if self._tg is not None:
            self._active_stt_reader = self._tg.create_task(
                reader_coro, name=f"stt-reader-{self.session_id}-{turn_id}"
            )
        else:
            self._active_stt_reader = asyncio.create_task(
                reader_coro, name=f"stt-reader-{self.session_id}-{turn_id}"
            )

    async def _handle_audio_end(self) -> None:
        if self._active_stt_stream is None:
            return
        await self._active_stt_stream.flush_utterance()

    async def _consume_stt_events(
        self,
        stream: STTStreamProtocol,
        *,
        turn_id: str,
        latency: TurnLatencyTracker,
    ) -> None:
        final_received = False
        cancelled = False
        try:
            async for event in stream.events():
                if not event.is_final:
                    if event.transcript:
                        self._last_partial_transcript = event.transcript
                        latency.mark_once("stt_first_partial")
                        await self._enqueue(
                            {
                                "type": "stt_partial",
                                "turn_id": turn_id,
                                "transcript": event.transcript,
                            }
                        )
                    continue

                final_received = True
                transcript = event.transcript or self._last_partial_transcript
                detected_lang = event.language_code or self.language_code
                latency.mark("stt_final", transcript_len=len(transcript))

                await self._enqueue(
                    {
                        "type": "stt_final",
                        "turn_id": turn_id,
                        "transcript": transcript,
                        "language_code": detected_lang.value,
                    }
                )
                self._active_stt_stream = None
                self._active_stt_reader = None
                await stream.close()

                if transcript.strip():
                    self._spawn_turn(
                        self._process_transcript_turn(
                            transcript=transcript,
                            detected_language=detected_lang,
                            turn_id=turn_id,
                            latency=latency,
                        ),
                        name=f"turn-{self.session_id}-{turn_id}",
                    )
                else:
                    await self._set_state(
                        SessionState.AWAITING_CONFIRMATION
                        if self.pending_action is not None
                        else SessionState.IDLE
                    )
                break
        except asyncio.CancelledError:
            cancelled = True
            raise
        finally:
            if (
                not cancelled
                and not final_received
                and self._last_partial_transcript
                and not self._closed
            ):
                # Stream ended after partial without explicit final flag
                transcript = self._last_partial_transcript
                latency.mark("stt_final", transcript_len=len(transcript), fallback_partial=True)
                await self._enqueue(
                    {
                        "type": "stt_final",
                        "turn_id": turn_id,
                        "transcript": transcript,
                        "language_code": self.language_code.value,
                    }
                )
                self._spawn_turn(
                    self._process_transcript_turn(
                        transcript=transcript,
                        detected_language=self.language_code,
                        turn_id=turn_id,
                        latency=latency,
                    ),
                    name=f"turn-{self.session_id}-{turn_id}",
                )

    async def _process_transcript_turn(
        self,
        *,
        transcript: str,
        detected_language: LanguageCode,
        turn_id: str,
        latency: TurnLatencyTracker,
    ) -> None:
        try:
            await self._set_state(SessionState.PROCESSING)
            latency.mark("nlu_start")
            parsed = await self._nlu.extract_intent(
                transcript,
                default_language=detected_language,
                awaiting_confirmation=self.pending_action is not None,
            )
            latency.mark("nlu_end", intent=parsed.intent.value)
            await self._execute_intent_turn(
                parsed, transcript=transcript, turn_id=turn_id, latency=latency
            )
        except asyncio.CancelledError:
            log_event(
                logger,
                "turn.cancelled",
                session_id=self.session_id,
                turn_id=turn_id,
            )
            raise
        except Exception as exc:
            log_event(
                logger,
                "turn.error",
                level=logging.ERROR,
                session_id=self.session_id,
                turn_id=turn_id,
                error=str(exc),
            )
            err_text = templates.render_error(self.language_code)
            await self._speak_response(
                text=err_text,
                language_code=self.language_code,
                turn_id=turn_id,
                latency=latency,
            )

    async def _execute_intent_turn(
        self,
        parsed: ParsedIntent,
        *,
        transcript: str,
        turn_id: str,
        latency: TurnLatencyTracker,
    ) -> None:
        lang = parsed.language_code or self.language_code
        self.language_code = lang

        response_text = ""
        refresh_ledger = False

        match parsed.intent:
            case IntentType.UNSUPPORTED_LENDING:
                response_text = templates.render_unsupported_lending(lang)

            case IntentType.CONFIRM:
                if self.pending_action is None:
                    response_text = templates.render_unknown(lang)
                else:
                    action = self.pending_action
                    self.pending_action = None
                    response_text, refresh_ledger = await self._commit_pending_action(
                        action, lang=lang, latency=latency
                    )

            case IntentType.CANCEL:
                if self.pending_action is not None:
                    self.pending_action = None
                    response_text = templates.render_action_cancelled(lang)
                else:
                    response_text = templates.render_action_cancelled(lang)

            case IntentType.RECORD_CREDIT:
                missing = self._missing_write_fields(parsed)
                if missing:
                    response_text = templates.render_missing_details(
                        lang, IntentType.RECORD_CREDIT, missing
                    )
                else:
                    assert parsed.customer_name is not None
                    assert parsed.amount_rupees is not None
                    self.pending_action = PendingAction(
                        action_id=uuid.uuid4().hex,
                        intent=IntentType.RECORD_CREDIT,
                        language_code=lang,
                        customer_name=parsed.customer_name,
                        amount_rupees=parsed.amount_rupees,
                        item_note=parsed.item_note,
                        transcript=transcript,
                    )
                    response_text = templates.render_confirm_credit(
                        lang, parsed.customer_name, parsed.amount_rupees, parsed.item_note
                    )

            case IntentType.RECORD_PAYMENT:
                missing = self._missing_write_fields(parsed)
                if missing:
                    response_text = templates.render_missing_details(
                        lang, IntentType.RECORD_PAYMENT, missing
                    )
                else:
                    assert parsed.customer_name is not None
                    assert parsed.amount_rupees is not None
                    self.pending_action = PendingAction(
                        action_id=uuid.uuid4().hex,
                        intent=IntentType.RECORD_PAYMENT,
                        language_code=lang,
                        customer_name=parsed.customer_name,
                        amount_rupees=parsed.amount_rupees,
                        item_note=parsed.item_note,
                        transcript=transcript,
                    )
                    response_text = templates.render_confirm_payment(
                        lang, parsed.customer_name, parsed.amount_rupees
                    )

            case IntentType.UNDO_LAST:
                latency.mark("db_start", op="get_last_reversible_event")
                target = await self._ledger.get_last_reversible_event(parsed.customer_name)
                latency.mark("db_end", op="get_last_reversible_event")
                if target is None:
                    response_text = templates.render_nothing_to_undo(lang, parsed.customer_name)
                else:
                    self.pending_action = PendingAction(
                        action_id=uuid.uuid4().hex,
                        intent=IntentType.UNDO_LAST,
                        language_code=lang,
                        customer_name=target.customer_name,
                        amount_rupees=abs(target.signed_amount_rupees),
                        transcript=transcript,
                        target_event_id=target.id,
                    )
                    response_text = templates.render_confirm_undo(lang, target)

            case IntentType.QUERY_BALANCE:
                if not parsed.customer_name:
                    response_text = templates.render_missing_details(
                        lang, IntentType.QUERY_BALANCE, ["customer_name"]
                    )
                else:
                    latency.mark("db_start", op="get_customer_balance")
                    bal = await self._ledger.get_customer_balance(parsed.customer_name)
                    latency.mark("db_end", op="get_customer_balance")
                    if bal is None:
                        response_text = templates.render_customer_not_found(
                            lang, parsed.customer_name
                        )
                    else:
                        response_text = templates.render_customer_balance(lang, bal)

            case IntentType.QUERY_TOTAL:
                latency.mark("db_start", op="get_shop_summary")
                summary = await self._ledger.get_shop_summary()
                latency.mark("db_end", op="get_shop_summary")
                response_text = templates.render_shop_summary(lang, summary)

            case IntentType.QUERY_HISTORY:
                latency.mark("db_start", op="list_recent_events")
                events = await self._ledger.list_recent_events(
                    limit=5, customer_name=parsed.customer_name
                )
                latency.mark("db_end", op="list_recent_events")
                response_text = templates.render_recent_history(
                    lang, events, parsed.customer_name
                )

            case _:
                response_text = templates.render_unknown(lang)

        # Guarantee AI identity is disclosed on the first turn in the user's language
        if lang not in self.ai_disclosed_languages:
            response_text = templates.render_ai_disclosure_prefix(lang) + response_text
            self.ai_disclosed_languages.add(lang)

        latency.mark("template_render", text_len=len(response_text))

        if refresh_ledger:
            await self.send_ledger_snapshot()

        await self._speak_response(
            text=response_text,
            language_code=lang,
            turn_id=turn_id,
            latency=latency,
        )

    @staticmethod
    def _missing_write_fields(parsed: ParsedIntent) -> list[str]:
        missing: list[str] = []
        if not parsed.customer_name:
            missing.append("customer_name")
        if parsed.amount_rupees is None:
            missing.append("amount_rupees")
        return missing

    async def _commit_pending_action(
        self,
        action: PendingAction,
        *,
        lang: LanguageCode,
        latency: TurnLatencyTracker,
    ) -> tuple[str, bool]:
        latency.mark("db_start", op=action.intent.value)
        try:
            if action.intent == IntentType.RECORD_CREDIT:
                assert action.customer_name is not None and action.amount_rupees is not None
                event, balance = await self._ledger.record_credit(
                    customer_name=action.customer_name,
                    amount_rupees=action.amount_rupees,
                    item_note=action.item_note,
                    transcript=action.transcript,
                    language_code=action.language_code,
                    event_uid=action.action_id,
                )
                latency.mark("db_end", op="record_credit", event_id=event.id)
                return (
                    templates.render_credit_recorded(
                        lang, event.customer_name, action.amount_rupees, balance
                    ),
                    True,
                )

            if action.intent == IntentType.RECORD_PAYMENT:
                assert action.customer_name is not None and action.amount_rupees is not None
                event, balance = await self._ledger.record_payment(
                    customer_name=action.customer_name,
                    amount_rupees=action.amount_rupees,
                    item_note=action.item_note,
                    transcript=action.transcript,
                    language_code=action.language_code,
                    event_uid=action.action_id,
                )
                latency.mark("db_end", op="record_payment", event_id=event.id)
                return (
                    templates.render_payment_recorded(
                        lang, event.customer_name, action.amount_rupees, balance
                    ),
                    True,
                )

            if action.intent == IntentType.UNDO_LAST and action.target_event_id is not None:
                rev_event, orig_event, balance = await self._ledger.reverse_event(
                    target_event_id=action.target_event_id,
                    transcript=action.transcript,
                    language_code=action.language_code,
                    event_uid=action.action_id,
                )
                latency.mark("db_end", op="reverse_event", event_id=rev_event.id)
                return templates.render_reversal_recorded(lang, orig_event, balance), True

            latency.mark("db_end", op="noop")
            return templates.render_unknown(lang), False
        except LedgerError as exc:
            latency.mark("db_end", op="ledger_error")
            log_event(
                logger,
                "ledger.commit_rejected",
                level=logging.WARNING,
                session_id=self.session_id,
                error=str(exc),
            )
            return templates.render_nothing_to_undo(lang, action.customer_name), False

    async def _speak_response(
        self,
        *,
        text: str,
        language_code: LanguageCode,
        turn_id: str,
        latency: TurnLatencyTracker,
        is_greeting: bool = False,
    ) -> None:
        await self._set_state(SessionState.SPEAKING)
        await self._enqueue(
            {
                "type": "agent_text",
                "turn_id": turn_id,
                "text": text,
                "language_code": language_code.value,
                "is_greeting": is_greeting,
            }
        )

        latency.mark("tts_start")
        first_chunk_sent = False
        try:
            async for chunk in self._tts.synthesize_stream(
                text=text,
                language_code=language_code,
                session_id=self.session_id,
                turn_id=turn_id,
            ):
                if chunk.pcm_bytes:
                    if not first_chunk_sent:
                        latency.mark("tts_first_audio_chunk", byte_len=len(chunk.pcm_bytes))
                    await self._enqueue(
                        {
                            "type": "tts_chunk",
                            "turn_id": turn_id,
                            "seq": chunk.seq,
                            "sample_rate": chunk.sample_rate,
                            "pcm_b64": base64.b64encode(chunk.pcm_bytes).decode("ascii"),
                            "is_final": chunk.is_final,
                        }
                    )
                    if not first_chunk_sent:
                        first_chunk_sent = True
                        latency.mark("ws_first_audio_sent")
                elif chunk.is_final:
                    await self._enqueue(
                        {
                            "type": "tts_done",
                            "turn_id": turn_id,
                        }
                    )
            latency.mark("tts_end")
        except asyncio.CancelledError:
            latency.mark("tts_cancelled")
            raise
        except Exception as exc:
            latency.mark("tts_error")
            log_event(
                logger,
                "orchestrator.tts_failed",
                level=logging.ERROR,
                session_id=self.session_id,
                turn_id=turn_id,
                error=str(exc),
            )
            await self._enqueue(
                {
                    "type": "error",
                    "session_id": self.session_id,
                    "message": "Gnani Timbre v3.0 TTS unavailable. Check GNANI_API_KEY in .env.",
                }
            )
        finally:
            await self._enqueue({"type": "tts_done", "turn_id": turn_id})
            latency.mark("turn_end")
            await self._enqueue({"type": "latency", **latency.to_summary()})

        next_state = (
            SessionState.AWAITING_CONFIRMATION
            if self.pending_action is not None
            else SessionState.IDLE
        )
        await self._set_state(next_state)
