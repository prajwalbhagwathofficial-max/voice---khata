"""Tests for voice_khata.models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from voice_khata.models import (
    IntentType,
    LanguageCode,
    ParsedIntent,
    normalize_customer_name,
    validate_strict_positive_rupees,
)


def test_integer_rupees_validator_accepts_whole_positive_ints() -> None:
    assert validate_strict_positive_rupees(250) == 250
    assert validate_strict_positive_rupees("45") == 45


@pytest.mark.parametrize("bad_value", [0, -10, 12.5, 100.0, "12.50", True, False, "abc"])
def test_integer_rupees_validator_rejects_floats_bools_and_non_positive(bad_value: object) -> None:
    with pytest.raises(ValueError):
        validate_strict_positive_rupees(bad_value)


def test_parsed_intent_forbids_extra_fields_and_normalizes_language() -> None:
    parsed = ParsedIntent(
        intent=IntentType.RECORD_CREDIT,
        customer_name="  Ramesh   Kumar ",
        amount_rupees=150,
        language_code="kannada",  # type: ignore[arg-type]
    )
    assert parsed.customer_name == "Ramesh Kumar"
    assert parsed.amount_rupees == 150
    assert parsed.language_code == LanguageCode.KN_IN

    with pytest.raises(ValidationError):
        ParsedIntent(
            intent=IntentType.QUERY_BALANCE,
            customer_name="Ramesh",
            reply_text="should not be allowed",  # type: ignore[call-arg]
        )


def test_normalize_customer_name_collapses_whitespace_and_case() -> None:
    assert normalize_customer_name("  Meena   Devi ") == "meena devi"
    assert normalize_customer_name("MEENA DEVI") == "meena devi"
