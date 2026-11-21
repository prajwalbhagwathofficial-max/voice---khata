"""Multi-turn conversation state manager for KhataSaathi.

Specifications:
- ConversationState (mode: LISTENING | CLARIFYING | CORRECTING | CONFIRMING,
  pending, last_event_id, last_customer_id, last_amount, pending_correction).
- Pending CLARIFY accumulates fields across turns.
- "nahi" alone cancels a pending confirm.
- "nahi, 430" is a CORRECTION (amount) of pending or last event within CORRECTION_TTL_SECONDS (default 60s).
- Newer write replaces context.
- After expiry, ask which entry.
- Correction supports amount, customer, date, and direction, and calls ledger.correct().
- UNDO states what it reversed ("Ramesh ka Rs 250 payment hata diya") then reverses.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal

from server.nlu.parser import Intent, ParsedCandidate

CORRECTION_TTL_SECONDS: float = 60.0

ModeType = Literal["LISTENING", "CLARIFYING", "CORRECTING", "CONFIRMING"]


@dataclass
class ConversationState:
    mode: ModeType = "LISTENING"
    pending: dict[str, Any] = field(default_factory=dict)
    last_event_id: str | None = None
    last_customer_id: str | None = None
    last_customer_name: str | None = None
    last_amount: int | None = None  # in paise
    last_direction: str | None = None  # "CREDIT_GIVEN" | "PAYMENT_RECEIVED"
    last_business_date: str | None = None
    last_event_ts: float | None = None
    pending_confirm: ParsedCandidate | None = None
    pending_correction: dict[str, Any] | None = None
    correction_ttl_seconds: float = CORRECTION_TTL_SECONDS

    def record_committed_event(
        self,
        event_id: str,
        customer_id: str,
        customer_name: str,
        amount_paise: int,
        direction: str,
        business_date: str,
        ts: float | None = None,
    ) -> None:
        """Newer write commits to ledger, updates last event context, and resets pending dialogue."""
        self.last_event_id = event_id
        self.last_customer_id = customer_id
        self.last_customer_name = customer_name
        self.last_amount = amount_paise
        self.last_direction = direction
        self.last_business_date = business_date
        self.last_event_ts = ts if ts is not None else time.time()

        self.mode = "LISTENING"
        self.pending = {}
        self.pending_confirm = None
        self.pending_correction = None

    def start_clarify(
        self,
        field_to_clarify: str,
        options: list[str],
        candidate: ParsedCandidate,
    ) -> None:
        """Enter CLARIFYING mode and store partially resolved candidate fields in pending."""
        self.mode = "CLARIFYING"
        self.pending = {
            "clarify_field": field_to_clarify,
            "options": options,
            "customer_name": candidate.customer_name,
            "customer_id": candidate.customer_id,
            "amount_paise": candidate.amount_paise,
            "direction": candidate.direction,
            "business_date": candidate.business_date,
            "date_phrase": candidate.date_phrase,
            "confidence": candidate.confidence,
            "number_contextual": candidate.number_contextual,
            "is_rule_based": candidate.is_rule_based,
        }

    def start_confirm(self, candidate: ParsedCandidate) -> None:
        """Enter CONFIRMING mode awaiting spoken YES / NO / CORRECTION."""
        self.mode = "CONFIRMING"
        self.pending_confirm = candidate

    def cancel_pending(self) -> None:
        """User said 'nahi' alone or explicit cancel: resets pending state back to LISTENING."""
        self.mode = "LISTENING"
        self.pending = {}
        self.pending_confirm = None
        self.pending_correction = None

    def accumulate_clarification(self, candidate: ParsedCandidate) -> ParsedCandidate:
        """Merge newly spoken clarification turn into accumulated pending fields."""
        clarify_field = self.pending.get("clarify_field")

        # 1. Spoken direction clarification
        if clarify_field == "direction":
            raw_lower = candidate.raw_text.casefold()
            if candidate.direction:
                self.pending["direction"] = candidate.direction
            elif any(w in raw_lower for w in ("udhaar", "udhar", "credit", "gave", "diya", "kotte", "appu")):
                self.pending["direction"] = "CREDIT_GIVEN"
            elif any(w in raw_lower for w in ("jama", "payment", "received", "paid", "mila", "kottaru")):
                self.pending["direction"] = "PAYMENT_RECEIVED"

        # 2. Spoken customer clarification (e.g. choosing between "Ramesh Kumar" and "Ramesh Auto")
        elif clarify_field == "customer":
            options = self.pending.get("options", [])
            raw_lower = candidate.raw_text.casefold()
            matched_option = None
            for opt in options:
                if opt.casefold() in raw_lower:
                    matched_option = opt
                    break
            if matched_option:
                self.pending["customer_name"] = matched_option
                self.pending["customer_id"] = None  # to be resolved by ledger
            elif candidate.customer_name:
                self.pending["customer_name"] = candidate.customer_name
                self.pending["customer_id"] = candidate.customer_id

        # 3. Spoken amount clarification
        elif clarify_field == "amount":
            if candidate.amount_paise:
                self.pending["amount_paise"] = candidate.amount_paise

        # 4. Spoken date clarification
        elif clarify_field == "date":
            if candidate.business_date:
                self.pending["business_date"] = candidate.business_date
                self.pending["date_phrase"] = candidate.date_phrase

        # Also absorb any other fields provided in this clarification turn
        if candidate.amount_paise and not self.pending.get("amount_paise"):
            self.pending["amount_paise"] = candidate.amount_paise
        if candidate.direction and not self.pending.get("direction"):
            self.pending["direction"] = candidate.direction
        if candidate.customer_name and not self.pending.get("customer_name"):
            self.pending["customer_name"] = candidate.customer_name
            self.pending["customer_id"] = candidate.customer_id
        if candidate.business_date and not self.pending.get("business_date"):
            self.pending["business_date"] = candidate.business_date
            self.pending["date_phrase"] = candidate.date_phrase

        # Reconstruct updated ParsedCandidate
        return ParsedCandidate(
            intent=Intent(self.pending.get("direction") or "CREDIT_GIVEN"),
            customer_name=self.pending.get("customer_name"),
            customer_id=self.pending.get("customer_id"),
            amount_paise=self.pending.get("amount_paise"),
            direction=self.pending.get("direction"),
            business_date=self.pending.get("business_date"),
            date_phrase=self.pending.get("date_phrase"),
            confidence=min(candidate.confidence, self.pending.get("confidence", 0.95)),
            number_contextual=self.pending.get("number_contextual", False),
            is_rule_based=True,
            raw_text=candidate.raw_text,
        )

    def handle_correction(
        self, candidate: ParsedCandidate, now_ts: float | None = None
    ) -> tuple[str, dict[str, Any] | ParsedCandidate | None]:
        """Handle a CORRECTION turn (e.g. 'nahi, 430' or 'nahi Ramesh tha').

        Returns (action_type, payload):
        - ("update_pending_confirm", updated_candidate)
        - ("update_pending_clarify", updated_candidate)
        - ("correct_last_event", new_fields_dict)
        - ("ask_which_entry", None)
        """
        curr_ts = now_ts if now_ts is not None else time.time()

        # 1. If currently in CONFIRMING, correction mutates the pending confirmation
        if self.mode == "CONFIRMING" and self.pending_confirm is not None:
            updated = self._apply_correction_fields(self.pending_confirm, candidate)
            self.pending_confirm = updated
            return "update_pending_confirm", updated

        # 2. If currently in CLARIFYING, correction mutates the pending dict
        if self.mode == "CLARIFYING" and self.pending:
            if candidate.amount_paise is not None:
                self.pending["amount_paise"] = candidate.amount_paise
            if candidate.customer_name is not None:
                self.pending["customer_name"] = candidate.customer_name
                self.pending["customer_id"] = candidate.customer_id
            if candidate.direction is not None:
                self.pending["direction"] = candidate.direction
            if candidate.business_date is not None:
                self.pending["business_date"] = candidate.business_date
                self.pending["date_phrase"] = candidate.date_phrase

            updated_cand = ParsedCandidate(
                intent=Intent(self.pending.get("direction") or "CREDIT_GIVEN"),
                customer_name=self.pending.get("customer_name"),
                customer_id=self.pending.get("customer_id"),
                amount_paise=self.pending.get("amount_paise"),
                direction=self.pending.get("direction"),
                business_date=self.pending.get("business_date"),
                date_phrase=self.pending.get("date_phrase"),
                confidence=candidate.confidence,
                is_rule_based=True,
                raw_text=candidate.raw_text,
            )
            return "update_pending_clarify", updated_cand

        # 3. If in LISTENING: check TTL against last committed event
        if self.last_event_id is not None and self.last_event_ts is not None:
            elapsed = curr_ts - self.last_event_ts
            if elapsed <= self.correction_ttl_seconds:
                # Within 60s TTL: correct last event!
                new_fields: dict[str, Any] = {"event_id": self.last_event_id}
                if candidate.amount_paise is not None:
                    new_fields["amount_paise"] = candidate.amount_paise
                if candidate.customer_name is not None:
                    new_fields["customer_name"] = candidate.customer_name
                    new_fields["customer_id"] = candidate.customer_id
                if candidate.direction is not None:
                    new_fields["direction"] = candidate.direction
                if candidate.business_date is not None:
                    new_fields["business_date"] = candidate.business_date
                    new_fields["date_phrase"] = candidate.date_phrase

                self.mode = "CORRECTING"
                self.pending_correction = new_fields
                return "correct_last_event", new_fields

        # 4. TTL expired or no last event: ask which entry
        return "ask_which_entry", None

    def _apply_correction_fields(
        self, base: ParsedCandidate, corr: ParsedCandidate
    ) -> ParsedCandidate:
        return ParsedCandidate(
            intent=corr.intent if corr.intent not in (Intent.CORRECTION, Intent.NO) else base.intent,
            customer_name=corr.customer_name if corr.customer_name is not None else base.customer_name,
            customer_id=corr.customer_id if corr.customer_id is not None else base.customer_id,
            amount_paise=corr.amount_paise if corr.amount_paise is not None else base.amount_paise,
            direction=corr.direction if corr.direction is not None else base.direction,
            business_date=corr.business_date if corr.business_date is not None else base.business_date,
            date_phrase=corr.date_phrase if corr.date_phrase is not None else base.date_phrase,
            confidence=corr.confidence,
            is_rule_based=True,
            raw_text=corr.raw_text,
        )
