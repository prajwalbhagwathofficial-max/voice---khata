"""Structured intent extraction using Gemini (never arithmetic, never query answers)."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from voice_khata.config import Settings
from voice_khata.logging_utils import log_event
from voice_khata.models import IntentType, LanguageCode, ParsedIntent

logger = logging.getLogger("voice_khata.nlu")

_LENDING_OR_PRESSURE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(interest|emi|loan|lending|borrow|penalty|late\s+fee|fine|credit\s+limit)\b", re.I),
    re.compile(r"\b(threaten|harass|chase|recover|overdue|pressure|warn|demand\s+payment|reminder)\b", re.I),
    re.compile(r"(ब्याज|लोन|कर्ज|किस्त|धमकी|दबाव|जुर्माना|रिमाइंडर)", re.I),
    re.compile(r"\b(byaaj|byaj|karz|kist|dhamki|dabav)\b", re.I),
    re.compile(r"(ಬಡ್ಡಿ|ಸಾಲ|ದಂಡ|ಬೆದರಿಕೆ|ಒತ್ತಡ|ರಿಮೈಂಡರ್)", re.I),
    re.compile(r"\b(baddi|saala)\b", re.I),
)

_CONFIRM_WORDS: frozenset[str] = frozenset(
    {
        "yes",
        "yeah",
        "yep",
        "confirm",
        "confirmed",
        "ok",
        "okay",
        "sure",
        "haan",
        "han",
        "ha",
        "ji",
        "ji haan",
        "haan ji",
        "theek hai",
        "thik hai",
        "sahi hai",
        "kar do",
        "likh do",
        "हाँ",
        "हां",
        "जी",
        "जी हाँ",
        "हाँ जी",
        "ठीक है",
        "सही है",
        "कर दो",
        "लिख दो",
        "ಹೌದು",
        "ಸರಿ",
        "ಬರೆಯಿರಿ",
        "ದಾಖಲಿಸಿ",
        "houdu",
        "haudu",
        "sari",
    }
)

_CANCEL_WORDS: frozenset[str] = frozenset(
    {
        "no",
        "nope",
        "cancel",
        "stop",
        "abort",
        "dont",
        "don't",
        "nahi",
        "nahin",
        "na",
        "mat karo",
        "chhodo",
        "ruko",
        "नहीं",
        "नही",
        "ना",
        "रद्द",
        "मत करो",
        "छोड़ो",
        "रुको",
        "ಬೇಡ",
        "ಇಲ್ಲ",
        "ರದ್ದು",
        "ನಿಲ್ಲಿಸಿ",
        "beda",
        "illa",
    }
)

_DECIMAL_RUPEE_RE = re.compile(r"\b\d+\.\d+\b")

_SYSTEM_PROMPT = """You are a strict structured intent extractor for Voice Khata, a kirana shop ledger.
You support Hindi (hi-IN), Kannada (kn-IN), Indian English (en-IN), and code-mixed speech (Hinglish/Kanglish).

STRICT RULES:
1. Return ONLY JSON matching the schema. Never generate prose or reply text.
2. NEVER perform arithmetic, addition, subtraction, multiplication, or balance calculation.
   Extract only the single explicit integer rupee amount spoken by the user.
   If the user says a decimal amount (like 12.50), set amount_rupees to null.
3. Allowed intents:
   - RECORD_CREDIT: Shopkeeper gave goods/udhaar on credit to a customer (customer owes more).
     Examples: "Ramesh ke 250 rupaye udhaar likh do", "Add 100 rupees credit for Meena rice", "ರಮೇಶ್ ಖಾತೆಗೆ 200 ರೂಪಾಯಿ ಉದ್ರಿ ಬರೆಯಿರಿ".
   - RECORD_PAYMENT: Customer paid money to the shopkeeper against their khata.
     Examples: "Ramesh ne 200 rupaye diye", "Meena paid 150 rupees", "ರಮೇಶ್ 150 ರೂಪಾಯಿ ಕೊಟ್ಟರು".
   - UNDO_LAST: Reverse the last entry (optionally for a named customer).
     Examples: "Aakhri entry undo karo", "Cancel Ramesh last entry", "ಕೊನೆಯ ದಾಖಲೆ ರದ್ದು ಮಾಡಿ".
   - QUERY_BALANCE: Check one specific customer's balance.
     Examples: "Ramesh ka kitna baaki hai?", "What is Meena's balance?", "ರಮೇಶ್ ಬಾಕಿ ಎಷ್ಟು?".
   - QUERY_TOTAL: Check total shop ledger balance / summary across all customers.
     Examples: "Kul kitna baaki hai?", "Total khata summary", "ಒಟ್ಟು ಬಾಕಿ ಎಷ್ಟು?".
   - QUERY_HISTORY: Ask for recent entries (for shop or a specific customer).
     Examples: "Aakhri entries dikhao", "Ramesh ki recent entries", "ಕೊನೆಯ ದಾಖಲೆಗಳು ತೋರಿಸಿ".
   - CONFIRM: User confirms a pending action ("haan", "yes", "ಹೌದು").
   - CANCEL: User cancels a pending action ("nahi", "no", "ಬೇಡ").
   - UNSUPPORTED_LENDING: Any mention of interest, loans, EMI, fines, reminders, or pressuring customers.
   - UNKNOWN: Anything unclear or outside kirana ledger bookkeeping.
4. Set language_code to one of: "hi-IN", "kn-IN", "en-IN".
5. Keep customer_name clean (no honorifics like "ka", "ke", "ne", "avara", "'s").
"""


class _GeminiIntentSchema(BaseModel):
    """Schema passed to Gemini structured output (post-validated into strict ParsedIntent)."""

    intent: str
    customer_name: str | None = None
    amount_rupees: int | None = None
    item_note: str = ""
    language_code: str = "hi-IN"
    confidence: float = Field(default=0.9, ge=0.0, le=1.0)


class NLUClientProtocol(Protocol):
    async def extract_intent(
        self,
        transcript: str,
        *,
        default_language: LanguageCode,
        awaiting_confirmation: bool = False,
    ) -> ParsedIntent: ...


def _clean_phrase(text: str) -> str:
    return re.sub(r"[\s।,.!?]+", " ", text.strip().casefold()).strip()


def detect_lending_or_pressure(transcript: str) -> bool:
    """Deterministic guardrail blocking lending, interest, and pressure language."""
    return any(pattern.search(transcript) for pattern in _LENDING_OR_PRESSURE_PATTERNS)


def match_confirmation_fastpath(
    transcript: str, default_language: LanguageCode
) -> ParsedIntent | None:
    """Fast deterministic yes/no match when awaiting confirmation."""
    cleaned = _clean_phrase(transcript)
    if cleaned in _CONFIRM_WORDS:
        return ParsedIntent(
            intent=IntentType.CONFIRM,
            language_code=default_language,
            confidence=1.0,
        )
    if cleaned in _CANCEL_WORDS:
        return ParsedIntent(
            intent=IntentType.CANCEL,
            language_code=default_language,
            confidence=1.0,
        )
    return None


class GeminiIntentExtractor:
    """Extracts structured ParsedIntent using google-genai with strict validation."""

    def __init__(self, settings: Settings, genai_client: Any | None = None) -> None:
        self._settings = settings
        self._client = genai_client

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self._settings.has_gemini_key():
            raise RuntimeError("GEMINI_API_KEY is not configured in .env")
        from google import genai

        self._client = genai.Client(
            api_key=self._settings.gemini_api_key.get_secret_value()
        )
        return self._client

    def _call_gemini_sync(
        self, transcript: str, default_language: LanguageCode, awaiting_confirmation: bool
    ) -> str:
        from google.genai import types

        client = self._get_client()
        user_prompt = (
            f"default_language: {default_language.value}\n"
            f"awaiting_confirmation: {str(awaiting_confirmation).lower()}\n"
            f"transcript: {transcript}"
        )
        config_kwargs: dict[str, Any] = {
            "system_instruction": _SYSTEM_PROMPT,
            "temperature": 0.0,
            "response_mime_type": "application/json",
            "response_schema": _GeminiIntentSchema,
        }
        if hasattr(types, "ThinkingConfig"):
            try:
                config_kwargs["thinking_config"] = types.ThinkingConfig(
                    thinking_level="MINIMAL"
                )
            except Exception:
                pass
        response = client.models.generate_content(
            model=self._settings.gemini_model,
            contents=user_prompt,
            config=types.GenerateContentConfig(**config_kwargs),
        )
        return response.text or "{}"

    def _parse_llm_json(
        self, raw_json: str, default_language: LanguageCode, transcript: str
    ) -> ParsedIntent:
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError:
            log_event(logger, "nlu.invalid_json", level=logging.WARNING)
            return ParsedIntent(intent=IntentType.UNKNOWN, language_code=default_language, confidence=0.0)

        if not isinstance(data, dict):
            return ParsedIntent(intent=IntentType.UNKNOWN, language_code=default_language, confidence=0.0)

        # Hard rule: never accept decimal rupee values spoken in transcript
        if _DECIMAL_RUPEE_RE.search(transcript):
            data["amount_rupees"] = None

        raw_intent = str(data.get("intent", "UNKNOWN")).strip().upper()
        try:
            intent = IntentType(raw_intent)
        except ValueError:
            intent = IntentType.UNKNOWN

        lang = LanguageCode.normalize(data.get("language_code"), default=default_language)

        raw_amount = data.get("amount_rupees")
        if isinstance(raw_amount, float) or isinstance(raw_amount, bool):
            log_event(
                logger,
                "nlu.rejected_non_integer_amount",
                level=logging.WARNING,
                amount_type=type(raw_amount).__name__,
            )
            raw_amount = None

        try:
            return ParsedIntent(
                intent=intent,
                customer_name=data.get("customer_name"),
                amount_rupees=raw_amount,
                item_note=data.get("item_note") or "",
                language_code=lang,
                confidence=float(data.get("confidence", 0.9)),
            )
        except (ValidationError, ValueError, TypeError) as exc:
            log_event(
                logger,
                "nlu.validation_failed",
                level=logging.WARNING,
                error=str(exc),
            )
            return ParsedIntent(
                intent=intent,
                customer_name=data.get("customer_name") if isinstance(data.get("customer_name"), str) else None,
                amount_rupees=None,
                item_note="",
                language_code=lang,
                confidence=0.0,
            )

    async def extract_intent(
        self,
        transcript: str,
        *,
        default_language: LanguageCode,
        awaiting_confirmation: bool = False,
    ) -> ParsedIntent:
        cleaned = transcript.strip()
        if not cleaned:
            return ParsedIntent(
                intent=IntentType.UNKNOWN,
                language_code=default_language,
                confidence=0.0,
            )

        if detect_lending_or_pressure(cleaned):
            log_event(logger, "nlu.lending_guardrail_triggered", language=default_language.value)
            return ParsedIntent(
                intent=IntentType.UNSUPPORTED_LENDING,
                language_code=default_language,
                confidence=1.0,
            )

        if awaiting_confirmation:
            fast = match_confirmation_fastpath(cleaned, default_language)
            if fast is not None:
                return fast

        raw_json = await asyncio.to_thread(
            self._call_gemini_sync, cleaned, default_language, awaiting_confirmation
        )
        return self._parse_llm_json(raw_json, default_language, cleaned)
