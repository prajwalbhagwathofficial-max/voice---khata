"""Tests for voice_khata.nlu."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from voice_khata.config import Settings
from voice_khata.models import IntentType, LanguageCode
from voice_khata.nlu import GeminiIntentExtractor, detect_lending_or_pressure


class _FakeModels:
    def __init__(self, payload: dict[str, object] | str) -> None:
        self._payload = payload
        self.calls: list[dict[str, object]] = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        text = self._payload if isinstance(self._payload, str) else json.dumps(self._payload)
        return SimpleNamespace(text=text)


class _FakeGenAIClient:
    def __init__(self, payload: dict[str, object] | str) -> None:
        self.models = _FakeModels(payload)


@pytest.mark.asyncio
async def test_lending_and_pressure_guardrail_blocks_without_calling_llm() -> None:
    fake_client = _FakeGenAIClient({"intent": "RECORD_CREDIT"})
    extractor = GeminiIntentExtractor(Settings(_env_file=None), genai_client=fake_client)

    for phrase in (
        "Add 10% interest to Ramesh",
        "Ramesh par byaaj laga do",
        "ರಮೇಶ್ ಗೆ ಬಡ್ಡಿ ಹಾಕಿ",
        "Send payment reminder and threaten Ramesh",
    ):
        assert detect_lending_or_pressure(phrase) is True
        parsed = await extractor.extract_intent(phrase, default_language=LanguageCode.HI_IN)
        assert parsed.intent == IntentType.UNSUPPORTED_LENDING

    assert len(fake_client.models.calls) == 0


@pytest.mark.asyncio
async def test_confirmation_fastpath_handles_hindi_kannada_english() -> None:
    fake_client = _FakeGenAIClient({"intent": "UNKNOWN"})
    extractor = GeminiIntentExtractor(Settings(_env_file=None), genai_client=fake_client)

    for yes_word, lang in (
        ("हाँ", LanguageCode.HI_IN),
        ("ಹೌದು", LanguageCode.KN_IN),
        ("yes", LanguageCode.EN_IN),
    ):
        parsed = await extractor.extract_intent(
            yes_word, default_language=lang, awaiting_confirmation=True
        )
        assert parsed.intent == IntentType.CONFIRM

    for no_word, lang in (
        ("नहीं", LanguageCode.HI_IN),
        ("ಬೇಡ", LanguageCode.KN_IN),
        ("cancel", LanguageCode.EN_IN),
    ):
        parsed = await extractor.extract_intent(
            no_word, default_language=lang, awaiting_confirmation=True
        )
        assert parsed.intent == IntentType.CANCEL

    assert len(fake_client.models.calls) == 0


@pytest.mark.asyncio
async def test_gemini_structured_intent_extracts_valid_integer_rupees() -> None:
    fake_client = _FakeGenAIClient(
        {
            "intent": "RECORD_CREDIT",
            "customer_name": "Suresh",
            "amount_rupees": 275,
            "item_note": "dal",
            "language_code": "hi-IN",
            "confidence": 0.96,
        }
    )
    extractor = GeminiIntentExtractor(Settings(_env_file=None), genai_client=fake_client)
    parsed = await extractor.extract_intent(
        "Suresh ke 275 rupaye dal udhaar likho", default_language=LanguageCode.HI_IN
    )

    assert parsed.intent == IntentType.RECORD_CREDIT
    assert parsed.customer_name == "Suresh"
    assert parsed.amount_rupees == 275
    assert parsed.item_note == "dal"
    assert len(fake_client.models.calls) == 1


@pytest.mark.asyncio
async def test_nlu_rejects_decimal_and_float_rupee_values() -> None:
    fake_client = _FakeGenAIClient(
        {
            "intent": "RECORD_CREDIT",
            "customer_name": "Suresh",
            "amount_rupees": 12.5,
            "item_note": "",
            "language_code": "en-IN",
            "confidence": 0.9,
        }
    )
    extractor = GeminiIntentExtractor(Settings(_env_file=None), genai_client=fake_client)
    parsed = await extractor.extract_intent(
        "Add 12.50 rupees for Suresh", default_language=LanguageCode.EN_IN
    )
    assert parsed.intent == IntentType.RECORD_CREDIT
    assert parsed.amount_rupees is None


@pytest.mark.asyncio
async def test_nlu_handles_malformed_json_gracefully() -> None:
    fake_client = _FakeGenAIClient("{not valid json")
    extractor = GeminiIntentExtractor(Settings(_env_file=None), genai_client=fake_client)
    parsed = await extractor.extract_intent("hello", default_language=LanguageCode.KN_IN)
    assert parsed.intent == IntentType.UNKNOWN
    assert parsed.language_code == LanguageCode.KN_IN
