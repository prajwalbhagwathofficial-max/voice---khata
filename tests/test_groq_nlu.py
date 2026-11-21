"""Unit tests for server.nlu.groq_nlu (Groq structured intent extraction)."""

from __future__ import annotations

import json

import httpx
import pytest

from server.config import ServerSettings
from server.nlu.groq_nlu import GroqIntentExtractor
from voice_khata.models import IntentType, LanguageCode


def _mock_groq_client(reply_obj: dict[str, object]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-groq-key"
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(reply_obj)}}]},
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_groq_extractor_parses_structured_intent_and_enforces_int_rupees() -> None:
    cfg = ServerSettings(_env_file=None, groq_api_key="test-groq-key")
    client = _mock_groq_client(
        {
            "intent": "RECORD_CREDIT",
            "customer_name": "Suresh Patel",
            "amount_rupees": 340,
            "item_note": "oil",
            "language_code": "hi-IN",
            "confidence": 0.97,
        }
    )
    extractor = GroqIntentExtractor(cfg, http_client=client)
    res = await extractor.extract_intent(
        "Suresh Patel ke 340 rupaye udhaar likh do", default_language=LanguageCode.HI_IN
    )
    assert res.intent == IntentType.RECORD_CREDIT
    assert res.customer_name == "Suresh Patel"
    assert res.amount_rupees == 340
    assert res.item_note == "oil"
    await client.aclose()


@pytest.mark.asyncio
async def test_groq_extractor_blocks_lending_and_rejects_decimals() -> None:
    cfg = ServerSettings(_env_file=None, groq_api_key="test-groq-key")
    client = _mock_groq_client(
        {
            "intent": "RECORD_CREDIT",
            "customer_name": "Ramesh",
            "amount_rupees": 12.5,
            "item_note": "",
            "language_code": "en-IN",
            "confidence": 0.9,
        }
    )
    extractor = GroqIntentExtractor(cfg, http_client=client)

    lending = await extractor.extract_intent(
        "Add 10 percent interest on Ramesh", default_language=LanguageCode.EN_IN
    )
    assert lending.intent == IntentType.UNSUPPORTED_LENDING

    dec = await extractor.extract_intent(
        "Add 12.50 rupees for Ramesh", default_language=LanguageCode.EN_IN
    )
    assert dec.amount_rupees is None
    await client.aclose()
