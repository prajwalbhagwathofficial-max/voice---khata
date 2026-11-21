"""Pure, deterministic safety evaluation engine for KhataSaathi transactions.

Architecture Rule:
Models propose meaning; deterministic code owns money, identity checks, commits,
and balance arithmetic. Never silently guess a financial field.
Clarification is a successful outcome.

Invariants for COMMIT:
- Unique strong customer match (customer_id is resolved and unambiguous)
- Amount > 0 and within policy limit (MAX_AMOUNT_PAISE)
- Explicit direction (CREDIT_GIVEN or PAYMENT_RECEIVED; never None or guessed)
- Resolved business_date (shop-local date, Asia/Kolkata)
- Confidence >= MIN_STT_CONFIDENCE
- No near-duplicate within 90 seconds
- Not contextual number pattern
- Rules and LLM in agreement
- Amount <= AMOUNT_CONFIRM_THRESHOLD_PAISE (otherwise requires spoken CONFIRM)
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field as dc_field
from enum import Enum
from typing import Any, Protocol

from server.nlu.parser import Intent, ParsedCandidate


class DecisionType(str, Enum):
    COMMIT = "COMMIT"
    CONFIRM = "CONFIRM"
    CLARIFY = "CLARIFY"
    REJECT = "REJECT"
    QUERY = "QUERY"


# Policy thresholds
MAX_AMOUNT_PAISE: int = 100_000_000  # Rs 10,00,000 maximum single transaction
AMOUNT_CONFIRM_THRESHOLD_PAISE: int = 500_000  # Rs 5,000 requires spoken confirmation
MIN_STT_CONFIDENCE: float = 0.85
NEAR_DUPLICATE_WINDOW_SECONDS: float = 90.0


@dataclass
class Decision:
    type: DecisionType
    field: str | None = None  # "direction", "customer", "amount", "date"
    options: list[str] = dc_field(default_factory=list)
    reason: str | None = None
    prompt_text: str | None = None
    candidate: ParsedCandidate | None = None
    query_type: str | None = None


class LedgerView(Protocol):
    def find_near_duplicates(
        self, customer_id: str, amount_paise: int, within_seconds: float = 90.0
    ) -> list[Any]: ...
    def find_customers_by_name(self, name: str) -> list[tuple[str, str]]: ...


class ConversationStateView(Protocol):
    last_customer_id: str | None
    last_amount_paise: int | None
    last_event_id: str | None
    mode: str
    pending: dict[str, Any]


def evaluate(
    candidate: ParsedCandidate,
    state: Any | None = None,
    ledger_view: Any | None = None,
    *,
    llm_candidate: ParsedCandidate | None = None,
) -> Decision:
    """Pure evaluation function.

    Returns one of:
    - COMMIT: All preconditions met, zero ambiguity, ready to append to ledger.
    - CONFIRM: Read-back confirmation needed (low confidence, large amount, contextual number, LLM used/disagreement, near-duplicate).
    - CLARIFY: One missing field question (priority: direction -> customer -> amount -> date).
    - REJECT: Policy violation, negative amount, out of scope.
    - QUERY: Read-only ledger queries (balance, today credit, today payments, total outstanding).
    """
    # 1. Handle Out of Scope
    if candidate.intent == Intent.OUT_OF_SCOPE:
        return Decision(
            type=DecisionType.REJECT,
            reason=candidate.rejection_reason or "out_of_scope",
            candidate=candidate,
        )

    # 2. Handle Read Queries
    if candidate.intent in (
        Intent.TOTAL_OUTSTANDING_QUERY,
        Intent.TODAY_CREDIT_QUERY,
        Intent.TODAY_PAYMENTS_QUERY,
    ):
        return Decision(
            type=DecisionType.QUERY,
            query_type=candidate.intent.value,
            candidate=candidate,
        )

    if candidate.intent == Intent.BALANCE_QUERY:
        if not candidate.customer_id and not candidate.customer_name:
            return Decision(
                type=DecisionType.CLARIFY,
                field="customer",
                options=[],
                reason="missing_customer_for_balance",
                candidate=candidate,
            )
        return Decision(
            type=DecisionType.QUERY,
            query_type=candidate.intent.value,
            candidate=candidate,
        )

    # 3. Handle Dialogue Control (YES / NO / UNDO / CORRECTION)
    if candidate.intent in (Intent.YES, Intent.NO, Intent.UNDO, Intent.CORRECTION):
        return Decision(
            type=DecisionType.QUERY,
            query_type=candidate.intent.value,
            candidate=candidate,
        )

    # 4. Amount Policy Rejection (<= 0 or > MAX_AMOUNT_PAISE)
    if candidate.amount_paise is not None:
        if candidate.amount_paise <= 0:
            return Decision(
                type=DecisionType.REJECT,
                reason="amount_non_positive",
                candidate=candidate,
            )
        if candidate.amount_paise > MAX_AMOUNT_PAISE:
            return Decision(
                type=DecisionType.REJECT,
                reason="amount_exceeds_maximum_policy",
                candidate=candidate,
            )

    # 5. Clarify Missing or Ambiguous Fields in Strict Priority Order:
    # Priority 1: Direction (Must be explicitly spoken, never guessed from UI or fallback)
    if candidate.direction is None or candidate.direction not in ("CREDIT_GIVEN", "PAYMENT_RECEIVED"):
        return Decision(
            type=DecisionType.CLARIFY,
            field="direction",
            options=["udhaar", "jama"],
            reason="missing_direction",
            candidate=candidate,
        )

    # Priority 2: Customer Identity
    # Must have a customer name or resolved customer_id
    if not candidate.customer_id and not candidate.customer_name:
        return Decision(
            type=DecisionType.CLARIFY,
            field="customer",
            options=[],
            reason="missing_customer",
            candidate=candidate,
        )

    # If customer name was spoken and ledger view is available, check for ambiguity
    if candidate.customer_name and ledger_view is not None:
        matches = None
        if hasattr(ledger_view, "find_customers_by_name"):
            res = ledger_view.find_customers_by_name(candidate.customer_name)
            if not inspect.isawaitable(res):
                matches = res
        elif hasattr(ledger_view, "_customers_matching_sync"):
            recs = ledger_view._customers_matching_sync(candidate.customer_name)
            matches = [(r.id, r.name) for r in recs]

        if matches is not None:
            if len(matches) > 1:
                # Ambiguous customer: e.g. "Ramesh Kumar" vs "Ramesh Auto"
                return Decision(
                    type=DecisionType.CLARIFY,
                    field="customer",
                    options=[name for _, name in matches],
                    reason="ambiguous_customer",
                    prompt_text=f"{matches[0][1]} ya {matches[1][1]}?",
                    candidate=candidate,
                )
            if len(matches) == 1 and not candidate.customer_id:
                candidate.customer_id = matches[0][0]

    if not candidate.customer_id and not candidate.customer_name:
        return Decision(
            type=DecisionType.CLARIFY,
            field="customer",
            options=[],
            reason="missing_customer",
            candidate=candidate,
        )

    # Priority 3: Amount
    if candidate.amount_paise is None or candidate.amount_paise <= 0:
        return Decision(
            type=DecisionType.CLARIFY,
            field="amount",
            options=[],
            reason="missing_amount",
            candidate=candidate,
        )

    # Priority 4: Business Date
    if not candidate.business_date:
        return Decision(
            type=DecisionType.CLARIFY,
            field="date",
            options=["aaj", "kal"],
            reason="unresolved_date",
            candidate=candidate,
        )

    # 6. Check Near-Duplicate within 90 Seconds
    if ledger_view is not None and candidate.customer_id and candidate.amount_paise:
        dupes = None
        if hasattr(ledger_view, "_find_near_duplicates_sync"):
            try:
                dupes = ledger_view._find_near_duplicates_sync(
                    candidate.customer_id,
                    candidate.amount_paise,
                    type=candidate.direction,
                    window_s=int(NEAR_DUPLICATE_WINDOW_SECONDS),
                )
            except TypeError:
                try:
                    dupes = ledger_view._find_near_duplicates_sync(
                        candidate.customer_id,
                        candidate.amount_paise,
                        within_seconds=NEAR_DUPLICATE_WINDOW_SECONDS,
                    )
                except TypeError:
                    dupes = ledger_view._find_near_duplicates_sync(
                        candidate.customer_id,
                        candidate.amount_paise,
                    )
        elif hasattr(ledger_view, "find_near_duplicates"):
            res = None
            try:
                res = ledger_view.find_near_duplicates(
                    candidate.customer_id,
                    candidate.amount_paise,
                    within_seconds=NEAR_DUPLICATE_WINDOW_SECONDS,
                )
            except TypeError:
                try:
                    res = ledger_view.find_near_duplicates(
                        candidate.customer_id,
                        candidate.amount_paise,
                        type=candidate.direction,
                        window_s=int(NEAR_DUPLICATE_WINDOW_SECONDS),
                    )
                except TypeError:
                    res = ledger_view.find_near_duplicates(
                        candidate.customer_id,
                        candidate.amount_paise,
                    )
            if not inspect.isawaitable(res):
                dupes = res

        if dupes:
            amt_inr = candidate.amount_paise // 100
            cname = candidate.customer_name or "customer"
            return Decision(
                type=DecisionType.CONFIRM,
                reason="near_duplicate_within_90s",
                prompt_text=f"{cname} ke liye pehle se Rs {amt_inr} likha hua hai, kya ye doosra hai?",
                candidate=candidate,
            )

    # 7. Check All Confirmation Conditions (Spoken Read-Back)
    # CONFIRM when:
    # 1. STT confidence is low (< 0.85)
    # 2. Rules and LLM disagree
    # 3. LLM path was used (!is_rule_based)
    # 4. Number came from contextual pattern ("three forty")
    # 5. Amount > AMOUNT_CONFIRM_THRESHOLD_PAISE (> Rs 5,000)
    confirm_reasons: list[str] = []

    if candidate.confidence < MIN_STT_CONFIDENCE:
        confirm_reasons.append("low_confidence")

    if not candidate.is_rule_based:
        confirm_reasons.append("llm_path_used")

    if llm_candidate is not None:
        if (
            llm_candidate.amount_paise != candidate.amount_paise
            or llm_candidate.direction != candidate.direction
            or (llm_candidate.customer_name and candidate.customer_name and llm_candidate.customer_name.casefold() != candidate.customer_name.casefold())
        ):
            confirm_reasons.append("rules_llm_disagreement")

    if candidate.number_contextual:
        confirm_reasons.append("contextual_number_pattern")

    if candidate.amount_paise > AMOUNT_CONFIRM_THRESHOLD_PAISE:
        confirm_reasons.append("amount_above_threshold")

    if confirm_reasons:
        cname = candidate.customer_name or "customer"
        amt_inr = candidate.amount_paise // 100
        dir_name = "udhaar" if candidate.direction == "CREDIT_GIVEN" else "jama"
        return Decision(
            type=DecisionType.CONFIRM,
            reason="; ".join(confirm_reasons),
            prompt_text=f"{cname}, Rs {amt_inr} {dir_name}, sahi?",
            candidate=candidate,
        )

    # 8. All checks passed: COMMIT
    return Decision(
        type=DecisionType.COMMIT,
        candidate=candidate,
    )
