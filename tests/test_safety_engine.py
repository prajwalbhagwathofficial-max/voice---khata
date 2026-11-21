"""Hypothesis property tests and deterministic safety evaluation tests for KhataSaathi.

Hard requirement:
No COMMIT is ever returned when any field is unresolved.
"""

from __future__ import annotations

from typing import Any
from hypothesis import given, strategies as st

from server.nlu.parser import Intent, ParsedCandidate
from server.safety.engine import (
    AMOUNT_CONFIRM_THRESHOLD_PAISE,
    MAX_AMOUNT_PAISE,
    MIN_STT_CONFIDENCE,
    DecisionType,
    evaluate,
)


class MockLedgerView:
    def __init__(
        self,
        near_duplicates: list[Any] | None = None,
        customers_by_name: list[tuple[str, str]] | None = None,
    ) -> None:
        self._near_duplicates = near_duplicates or []
        self._customers_by_name = customers_by_name

    def find_near_duplicates(
        self, customer_id: str, amount_paise: int, within_seconds: float = 90.0
    ) -> list[Any]:
        return self._near_duplicates

    def find_customers_by_name(self, name: str) -> list[tuple[str, str]]:
        if self._customers_by_name is not None:
            return self._customers_by_name
        return [(f"cid_{name}", name)]


# ---------------------------------------------------------------------------
# Hypothesis Property Test
# ---------------------------------------------------------------------------
@st.composite
def candidate_strategy(draw: st.DrawFn) -> ParsedCandidate:
    intent = draw(st.sampled_from(list(Intent)))
    customer_name = draw(st.one_of(st.none(), st.text(min_size=1, max_size=20)))
    customer_id = draw(st.one_of(st.none(), st.text(min_size=1, max_size=20)))
    amount_paise = draw(
        st.one_of(
            st.none(),
            st.integers(min_value=-1000, max_value=MAX_AMOUNT_PAISE * 2),
        )
    )
    direction = draw(
        st.one_of(
            st.none(),
            st.sampled_from(["CREDIT_GIVEN", "PAYMENT_RECEIVED", "INVALID_DIR"]),
        )
    )
    business_date = draw(
        st.one_of(st.none(), st.sampled_from(["2026-11-21", "2026-11-20", ""]))
    )
    confidence = draw(st.floats(min_value=0.0, max_value=1.0))
    number_contextual = draw(st.booleans())
    is_rule_based = draw(st.booleans())

    return ParsedCandidate(
        intent=intent,
        customer_name=customer_name,
        customer_id=customer_id,
        amount_paise=amount_paise,
        direction=direction,
        business_date=business_date,
        confidence=confidence,
        number_contextual=number_contextual,
        is_rule_based=is_rule_based,
    )


@given(cand=candidate_strategy(), has_near_duplicate=st.booleans())
def test_hypothesis_no_commit_when_any_field_unresolved(
    cand: ParsedCandidate, has_near_duplicate: bool
) -> None:
    """Formal property invariant:

    Under NO circumstances can evaluate(...) return COMMIT if any field is
    missing, unresolved, ambiguous, or requires confirmation.
    """
    ledger = MockLedgerView(
        near_duplicates=[{"id": "ev_dup"}] if has_near_duplicate else []
    )
    decision = evaluate(cand, state=None, ledger_view=ledger)

    if decision.type == DecisionType.COMMIT:
        # Every single one of these invariants MUST strictly hold
        assert cand.intent in (Intent.CREDIT_GIVEN, Intent.PAYMENT_RECEIVED)
        assert cand.customer_id is not None or cand.customer_name is not None
        assert cand.amount_paise is not None
        assert 0 < cand.amount_paise <= MAX_AMOUNT_PAISE
        assert cand.amount_paise <= AMOUNT_CONFIRM_THRESHOLD_PAISE
        assert cand.direction in ("CREDIT_GIVEN", "PAYMENT_RECEIVED")
        assert cand.business_date is not None and len(cand.business_date) > 0
        assert cand.confidence >= MIN_STT_CONFIDENCE
        assert cand.is_rule_based is True
        assert cand.number_contextual is False
        assert not has_near_duplicate


# ---------------------------------------------------------------------------
# Unit tests for specific safety engine requirements
# ---------------------------------------------------------------------------
def test_clarify_priority_direction_first() -> None:
    # All fields missing: direction must be asked first!
    cand = ParsedCandidate(
        intent=Intent.CREDIT_GIVEN,
        customer_name=None,
        amount_paise=None,
        direction=None,
        business_date=None,
    )
    dec = evaluate(cand)
    assert dec.type == DecisionType.CLARIFY
    assert dec.field == "direction"


def test_clarify_priority_customer_second() -> None:
    # Direction is present, but customer and amount are missing: customer asked second!
    cand = ParsedCandidate(
        intent=Intent.CREDIT_GIVEN,
        customer_name=None,
        amount_paise=None,
        direction="CREDIT_GIVEN",
        business_date="2026-11-21",
    )
    dec = evaluate(cand)
    assert dec.type == DecisionType.CLARIFY
    assert dec.field == "customer"


def test_clarify_priority_amount_third() -> None:
    # Direction and customer are present, but amount is missing: amount asked third!
    cand = ParsedCandidate(
        intent=Intent.CREDIT_GIVEN,
        customer_name="Ramesh",
        customer_id="c_ramesh",
        amount_paise=None,
        direction="CREDIT_GIVEN",
        business_date="2026-11-21",
    )
    dec = evaluate(cand)
    assert dec.type == DecisionType.CLARIFY
    assert dec.field == "amount"


def test_ambiguous_customer_match_clarifies() -> None:
    # Multiple matching customers: asks "Ramesh Kumar ya Ramesh Auto?"
    cand = ParsedCandidate(
        intent=Intent.CREDIT_GIVEN,
        customer_name="Ramesh",
        amount_paise=34000,
        direction="CREDIT_GIVEN",
        business_date="2026-11-21",
    )
    ledger = MockLedgerView(
        customers_by_name=[("c1", "Ramesh Kumar"), ("c2", "Ramesh Auto")]
    )
    dec = evaluate(cand, ledger_view=ledger)
    assert dec.type == DecisionType.CLARIFY
    assert dec.field == "customer"
    assert dec.options == ["Ramesh Kumar", "Ramesh Auto"]


def test_near_duplicate_within_90s_requires_confirm() -> None:
    cand = ParsedCandidate(
        intent=Intent.CREDIT_GIVEN,
        customer_name="Ramesh",
        customer_id="c_ramesh",
        amount_paise=34000,
        direction="CREDIT_GIVEN",
        business_date="2026-11-21",
    )
    ledger = MockLedgerView(near_duplicates=[{"event_id": "prev_1"}])
    dec = evaluate(cand, ledger_view=ledger)
    assert dec.type == DecisionType.CONFIRM
    assert "near_duplicate" in (dec.reason or "")


def test_contextual_number_pattern_requires_confirm() -> None:
    cand = ParsedCandidate(
        intent=Intent.CREDIT_GIVEN,
        customer_name="Ramesh",
        customer_id="c_ramesh",
        amount_paise=34000,
        direction="CREDIT_GIVEN",
        business_date="2026-11-21",
        number_contextual=True,  # "three forty"
    )
    dec = evaluate(cand)
    assert dec.type == DecisionType.CONFIRM
    assert "contextual_number_pattern" in (dec.reason or "")


def test_amount_above_threshold_requires_confirm() -> None:
    cand = ParsedCandidate(
        intent=Intent.CREDIT_GIVEN,
        customer_name="Ramesh",
        customer_id="c_ramesh",
        amount_paise=600_000,  # Rs 6,000 (> Rs 5,000 threshold)
        direction="CREDIT_GIVEN",
        business_date="2026-11-21",
    )
    dec = evaluate(cand)
    assert dec.type == DecisionType.CONFIRM
    assert "amount_above_threshold" in (dec.reason or "")


def test_llm_rules_disagreement_requires_confirm() -> None:
    rule_cand = ParsedCandidate(
        intent=Intent.CREDIT_GIVEN,
        customer_name="Ramesh",
        customer_id="c_ramesh",
        amount_paise=34000,
        direction="CREDIT_GIVEN",
        business_date="2026-11-21",
    )
    llm_cand = ParsedCandidate(
        intent=Intent.PAYMENT_RECEIVED,
        customer_name="Ramesh",
        amount_paise=34000,
        direction="PAYMENT_RECEIVED",  # Disagrees on direction!
    )
    dec = evaluate(rule_cand, llm_candidate=llm_cand)
    assert dec.type == DecisionType.CONFIRM
    assert "rules_llm_disagreement" in (dec.reason or "")


def test_out_of_scope_rejects() -> None:
    cand = ParsedCandidate(
        intent=Intent.OUT_OF_SCOPE,
        rejection_reason="unsupported_lending",
    )
    dec = evaluate(cand)
    assert dec.type == DecisionType.REJECT
    assert dec.reason == "unsupported_lending"


def test_commit_when_all_invariants_pass() -> None:
    cand = ParsedCandidate(
        intent=Intent.CREDIT_GIVEN,
        customer_name="Ramesh",
        customer_id="c_ramesh",
        amount_paise=34000,
        direction="CREDIT_GIVEN",
        business_date="2026-11-21",
        confidence=0.98,
        is_rule_based=True,
        number_contextual=False,
    )
    dec = evaluate(cand)
    assert dec.type == DecisionType.COMMIT
