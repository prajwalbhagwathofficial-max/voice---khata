"""Ultra-low-latency structured intent extraction via Groq API (never arithmetic or query replies)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx
from pydantic import ValidationError

from server.config import ServerSettings
from voice_khata.logging_utils import log_event
from voice_khata.models import IntentType, LanguageCode, ParsedIntent
from voice_khata.nlu import (
    _SYSTEM_PROMPT,
    detect_lending_or_pressure,
    match_confirmation_fastpath,
)

logger = logging.getLogger("voice_khata.server.nlu.groq")

_GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
_DECIMAL_RUPEE_RE = re.compile(r"\b\d+\.\d+\b")

_JSON_SCHEMA_HINT = (
    'Return ONLY a JSON object with keys: '
    '{"intent": one of [RECORD_CREDIT, RECORD_PAYMENT, UNDO_LAST, QUERY_BALANCE, QUERY_TOTAL, '
    'QUERY_HISTORY, CONFIRM, CANCEL, UNSUPPORTED_LENDING, UNKNOWN], '
    '"customer_name": string or null, "amount_rupees": integer or null, '
    '"item_note": string, "language_code": one of ["hi-IN", "kn-IN", "en-IN"], '
    '"confidence": number between 0 and 1}.'
)


class GroqIntentExtractor:
    """Async Groq structured intent extractor using httpx (never logs API keys)."""

    def __init__(
        self,
        settings: ServerSettings,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._external_client = http_client
        self._client: httpx.AsyncClient | None = http_client

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=4.0))
        return self._client

    async def close(self) -> None:
        if self._external_client is None and self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    def _parse_json_response(
        self, raw_json: str, default_language: LanguageCode, transcript: str
    ) -> ParsedIntent:
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError:
            log_event(logger, "groq_nlu.invalid_json", level=logging.WARNING)
            return ParsedIntent(
                intent=IntentType.UNKNOWN, language_code=default_language, confidence=0.0
            )
        if not isinstance(data, dict):
            return ParsedIntent(
                intent=IntentType.UNKNOWN, language_code=default_language, confidence=0.0
            )

        if _DECIMAL_RUPEE_RE.search(transcript):
            data["amount_rupees"] = None

        raw_intent = str(data.get("intent", "UNKNOWN")).strip().upper()
        try:
            intent = IntentType(raw_intent)
        except ValueError:
            intent = IntentType.UNKNOWN

        lang = LanguageCode.normalize(data.get("language_code"), default=default_language)
        raw_amount = data.get("amount_rupees")
        if isinstance(raw_amount, (float, bool)):
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
            log_event(logger, "groq_nlu.validation_failed", level=logging.WARNING, error=str(exc))
            return ParsedIntent(
                intent=intent,
                customer_name=data.get("customer_name")
                if isinstance(data.get("customer_name"), str)
                else None,
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
                intent=IntentType.UNKNOWN, language_code=default_language, confidence=0.0
            )

        if detect_lending_or_pressure(cleaned):
            return ParsedIntent(
                intent=IntentType.UNSUPPORTED_LENDING,
                language_code=default_language,
                confidence=1.0,
            )

        if awaiting_confirmation:
            fast = match_confirmation_fastpath(cleaned, default_language)
            if fast is not None:
                return fast

        api_key = self._settings.groq_api_key.get_secret_value().strip()
        if not api_key:
            raise RuntimeError("GROQ_API_KEY is not configured")

        payload: dict[str, Any] = {
            "model": self._settings.groq_model,
            "temperature": 0.0,
            "max_tokens": 160,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": f"{_SYSTEM_PROMPT}\n\n{_JSON_SCHEMA_HINT}"},
                {
                    "role": "user",
                    "content": (
                        f"default_language: {default_language.value}\n"
                        f"awaiting_confirmation: {str(awaiting_confirmation).lower()}\n"
                        f"transcript: {cleaned}"
                    ),
                },
            ],
        }

        client = self._get_client()
        resp = await client.post(
            _GROQ_CHAT_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        resp.raise_for_status()
        body = resp.json()
        raw_content = (
            body.get("choices", [{}])[0].get("message", {}).get("content", "{}")
            if isinstance(body, dict)
            else "{}"
        )
        return self._parse_json_response(str(raw_content), default_language, cleaned)
