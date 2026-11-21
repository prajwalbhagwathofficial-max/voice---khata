"""Tests for voice_khata.templates (AI disclosure, multilingual DB templates, zero pressure)."""

from __future__ import annotations

import pytest

from voice_khata import templates
from voice_khata.models import (
    CustomerBalance,
    EventType,
    IntentType,
    LanguageCode,
    LedgerEvent,
    ShopSummary,
)

FORBIDDEN_PRESSURE_WORDS = (
    "overdue",
    "penalty",
    "fine",
    "threaten",
    "recover",
    "chase",
    "interest rate",
    "emi",
    "demand",
    "reminder",
)


@pytest.mark.parametrize(
    ("lang", "ai_marker"),
    [
        (LanguageCode.HI_IN, "एआई"),
        (LanguageCode.KN_IN, "ಎಐ"),
        (LanguageCode.EN_IN, "AI"),
    ],
)
def test_first_turn_greeting_states_ai_identity_in_user_language(
    lang: LanguageCode, ai_marker: str
) -> None:
    greeting = templates.render_ai_greeting(lang)
    prefix = templates.render_ai_disclosure_prefix(lang)
    assert ai_marker in greeting
    assert ai_marker in prefix


@pytest.mark.parametrize("lang", [LanguageCode.HI_IN, LanguageCode.KN_IN, LanguageCode.EN_IN])
def test_templates_use_integer_rupees_and_contain_no_pressure_language(
    lang: LanguageCode,
) -> None:
    bal_pos = CustomerBalance(customer_id=1, customer_name="Meena", balance_rupees=350, event_count=2)
    bal_zero = CustomerBalance(customer_id=1, customer_name="Meena", balance_rupees=0, event_count=2)
    bal_adv = CustomerBalance(customer_id=1, customer_name="Meena", balance_rupees=-50, event_count=2)
    summary = ShopSummary(
        total_outstanding_rupees=350,
        customer_count=1,
        active_credit_customers=1,
        total_events=2,
    )
    ev = LedgerEvent(
        id=1,
        event_uid="u1",
        created_at_utc="2026-11-18T10:00:00+00:00",
        created_at_mono_ns=12345,
        customer_id=1,
        customer_name="Meena",
        event_type=EventType.CREDIT_GIVEN,
        signed_amount_rupees=350,
        language_code=lang,
    )

    outputs = [
        templates.render_ai_greeting(lang),
        templates.render_confirm_credit(lang, "Meena", 350, "atta"),
        templates.render_confirm_payment(lang, "Meena", 200),
        templates.render_confirm_undo(lang, ev),
        templates.render_credit_recorded(lang, "Meena", 350, bal_pos),
        templates.render_payment_recorded(lang, "Meena", 200, bal_pos),
        templates.render_reversal_recorded(lang, ev, bal_zero),
        templates.render_action_cancelled(lang),
        templates.render_customer_balance(lang, bal_pos),
        templates.render_customer_balance(lang, bal_zero),
        templates.render_customer_balance(lang, bal_adv),
        templates.render_customer_not_found(lang, "Unknown"),
        templates.render_shop_summary(lang, summary),
        templates.render_recent_history(lang, [ev]),
        templates.render_nothing_to_undo(lang, "Meena"),
        templates.render_missing_details(lang, IntentType.RECORD_CREDIT, ["customer_name", "amount_rupees"]),
        templates.render_unsupported_lending(lang),
        templates.render_unknown(lang),
        templates.render_error(lang),
    ]

    assert "350" in templates.render_customer_balance(lang, bal_pos)
    assert "50" in templates.render_customer_balance(lang, bal_adv)

    for text in outputs:
        lowered = text.lower()
        for word in FORBIDDEN_PRESSURE_WORDS:
            assert word not in lowered, f"Forbidden word {word!r} found in: {text}"
