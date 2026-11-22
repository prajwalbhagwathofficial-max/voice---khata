"""Deterministic multilingual response templates (hi-IN, kn-IN, en-IN).

All user-facing responses — AI disclosure, confirmations, query answers, and
errors — come exclusively from these deterministic templates parameterized with
SQLite / validated integer-rupee values. Never uses LLM text generation,
lending terminology, or pressure language.
"""

from __future__ import annotations

from voice_khata.models import (
    CustomerBalance,
    EventType,
    IntentType,
    LanguageCode,
    LedgerEvent,
    ShopSummary,
)


def _format_balance_phrase(lang: LanguageCode, balance_rupees: int) -> str:
    if balance_rupees > 0:
        if lang == LanguageCode.HI_IN:
            return f"{balance_rupees} रुपये बाकी हैं"
        if lang == LanguageCode.KN_IN:
            return f"{balance_rupees} ರೂಪಾಯಿ ಬಾಕಿ ಇದೆ"
        if lang == LanguageCode.TE_IN:
            return f"{balance_rupees} రూపాయలు బాకీ ఉంది"
        return f"{balance_rupees} rupees balance"
    if balance_rupees < 0:
        advance = abs(balance_rupees)
        if lang == LanguageCode.HI_IN:
            return f"{advance} रुपये अग्रिम जमा हैं"
        if lang == LanguageCode.KN_IN:
            return f"{advance} ರೂಪಾಯಿ ಮುಂಗಡ ಜಮಾ ಇದೆ"
        if lang == LanguageCode.TE_IN:
            return f"{advance} రూపాయలు అడ్వాన్స్ ఉంది"
        return f"{advance} rupees advance credit"
    if lang == LanguageCode.HI_IN:
        return "हिसाब बराबर है, 0 रुपये बाकी हैं"
    if lang == LanguageCode.KN_IN:
        return "ಖಾತೆ ಸಮವಾಗಿದೆ, 0 ರೂಪಾಯಿ ಬಾಕಿ ಇದೆ"
    if lang == LanguageCode.TE_IN:
        return "లెక్క సమానమైంది, 0 రూపాయలు బాకీ ఉంది"
    return "settled at 0 rupees"


def render_ai_greeting(lang: LanguageCode) -> str:
    """First-turn mandatory AI disclosure in the shopkeeper's language."""
    if lang == LanguageCode.HI_IN:
        return (
            "नमस्ते, मैं वॉइस खाता एआई असिस्टेंट हूँ। "
            "मैं आपकी दुकान का खाता लिखने और हिसाब देखने में मदद कर सकता हूँ। "
            "बताइए, क्या दर्ज करना है?"
        )
    if lang == LanguageCode.KN_IN:
        return (
            "ನಮಸ್ಕಾರ, ನಾನು ವಾಯ್ಸ್ ಖಾತಾ ಎಐ ಸಹಾಯಕ. "
            "ನಿಮ್ಮ ಅಂಗಡಿಯ ಖಾತೆ ಬರೆಯಲು ಮತ್ತು ಲೆಕ್ಕ ನೋಡಲು ನಾನು ಸಹಾಯ ಮಾಡುತ್ತೇನೆ. "
            "ಹೇಳಿ, ಏನು ದಾಖಲಿಸಬೇಕು?"
        )
    return (
        "Hello, I am the Voice Khata AI assistant. "
        "I can help record shop ledger entries and check balances. "
        "What would you like to note?"
    )


def render_ai_disclosure_prefix(lang: LanguageCode) -> str:
    """Short AI disclosure prefix used if the user's first turn switches language."""
    if lang == LanguageCode.HI_IN:
        return "मैं वॉइस खाता एआई असिस्टेंट हूँ। "
    if lang == LanguageCode.KN_IN:
        return "ನಾನು ವಾಯ್ಸ್ ಖಾತಾ ಎಐ ಸಹಾಯಕ. "
    return "I am the Voice Khata AI assistant. "


def render_confirm_credit(
    lang: LanguageCode, customer_name: str, amount_rupees: int, item_note: str = ""
) -> str:
    if lang == LanguageCode.HI_IN:
        note_part = f" ({item_note})" if item_note else ""
        return (
            f"{customer_name} के खाते में {amount_rupees} रुपये उधार{note_part} लिखना है। "
            "कृपया हाँ या नहीं बोलें।"
        )
    if lang == LanguageCode.KN_IN:
        note_part = f" ({item_note})" if item_note else ""
        return (
            f"{customer_name} ಅವರ ಖಾತೆಯಲ್ಲಿ {amount_rupees} ರೂಪಾಯಿ ಉದ್ರಿ{note_part} ದಾಖಲಿಸಬೇಕೇ? "
            "ದಯವಿಟ್ಟು ಹೌದು ಅಥವಾ ಬೇಡ ಎಂದು ಹೇಳಿ."
        )
    note_part = f" for {item_note}" if item_note else ""
    return (
        f"Record {amount_rupees} rupees credit entry for {customer_name}{note_part}? "
        "Please say yes to confirm or no to cancel."
    )


def render_confirm_payment(
    lang: LanguageCode, customer_name: str, amount_rupees: int
) -> str:
    if lang == LanguageCode.HI_IN:
        return (
            f"{customer_name} से {amount_rupees} रुपये जमा दर्ज करना है। "
            "कृपया हाँ या नहीं बोलें।"
        )
    if lang == LanguageCode.KN_IN:
        return (
            f"{customer_name} ಅವರಿಂದ {amount_rupees} ರೂಪಾಯಿ ಪಾವತಿ ದಾಖಲಿಸಬೇಕೇ? "
            "ದಯವಿಟ್ಟು ಹೌದು ಅಥವಾ ಬೇಡ ಎಂದು ಹೇಳಿ."
        )
    return (
        f"Record {amount_rupees} rupees payment received from {customer_name}? "
        "Please say yes to confirm or no to cancel."
    )


def render_confirm_undo(lang: LanguageCode, event: LedgerEvent) -> str:
    amount = abs(event.signed_amount_rupees)
    if lang == LanguageCode.HI_IN:
        kind = "उधार" if event.event_type == EventType.CREDIT_GIVEN else "जमा"
        return (
            f"{event.customer_name} की आखिरी {amount} रुपये की {kind} प्रविष्टि को "
            "रिवर्स करना है। कृपया हाँ या नहीं बोलें।"
        )
    if lang == LanguageCode.KN_IN:
        kind = "ಉದ್ರಿ" if event.event_type == EventType.CREDIT_GIVEN else "ಪಾವತಿ"
        return (
            f"{event.customer_name} ಅವರ ಕೊನೆಯ {amount} ರೂಪಾಯಿ {kind} ದಾಖಲೆಯನ್ನು "
            "ರದ್ದು ಮಾಡುವ ಪ್ರತಿದಾಖಲೆ ಸೇರಿಸಬೇಕೇ? ದಯವಿಟ್ಟು ಹೌದು ಅಥವಾ ಬೇಡ ಎಂದು ಹೇಳಿ."
        )
    kind = "credit" if event.event_type == EventType.CREDIT_GIVEN else "payment"
    return (
        f"Add a reversal entry for {event.customer_name}'s last {kind} entry of "
        f"{amount} rupees? Please say yes to confirm or no to cancel."
    )


def render_credit_recorded(
    lang: LanguageCode, customer_name: str, amount_rupees: int, balance: CustomerBalance
) -> str:
    bal_phrase = _format_balance_phrase(lang, balance.balance_rupees)
    if lang == LanguageCode.HI_IN:
        return (
            f"दर्ज हो गया। {customer_name} के खाते में {amount_rupees} रुपये उधार लिखे गए। "
            f"अब कुल {bal_phrase}।"
        )
    if lang == LanguageCode.KN_IN:
        return (
            f"ದಾಖಲಾಗಿದೆ. {customer_name} ಅವರ ಖಾತೆಯಲ್ಲಿ {amount_rupees} ರೂಪಾಯಿ ಉದ್ರಿ ಸೇರಿಸಲಾಗಿದೆ. "
            f"ಈಗ ಒಟ್ಟು {bal_phrase}."
        )
    return (
        f"Recorded. Added {amount_rupees} rupees credit entry for {customer_name}. "
        f"Updated balance is {bal_phrase}."
    )


def render_payment_recorded(
    lang: LanguageCode, customer_name: str, amount_rupees: int, balance: CustomerBalance
) -> str:
    if balance.balance_rupees > 0:
        if lang == LanguageCode.HI_IN:
            return (
                f"दर्ज हो गया। {customer_name} से {amount_rupees} रुपये जमा लिखे गए। "
                f"अब बाकी देय राशि {balance.balance_rupees} रुपये है।"
            )
        if lang == LanguageCode.KN_IN:
            return (
                f"ದಾಖಲಾಗಿದೆ. {customer_name} ಅವರಿಂದ {amount_rupees} ರೂಪಾಯಿ ಪಾವತಿ ಸೇರಿಸಲಾಗಿದೆ. "
                f"ಈಗ ಉಳಿದ ಪಾವತಿಸಬೇಕಾದ ಮೊತ್ತ {balance.balance_rupees} ರೂಪಾಯಿ."
            )
        if lang == LanguageCode.TE_IN:
            return (
                f"నమోదయింది. {customer_name} నుండి {amount_rupees} రూపాయలు జమ చేయబడింది. "
                f"ఇక చెల్లించాల్సిన మిగిలిన మొత్తం {balance.balance_rupees} రూపాయలు."
            )
        return (
            f"Recorded. Noted {amount_rupees} rupees payment from {customer_name}. "
            f"The remaining payable amount is {balance.balance_rupees} rupees."
        )
    bal_phrase = _format_balance_phrase(lang, balance.balance_rupees)
    if lang == LanguageCode.HI_IN:
        return (
            f"दर्ज हो गया। {customer_name} से {amount_rupees} रुपये जमा लिखे गए। "
            f"अब {bal_phrase}।"
        )
    if lang == LanguageCode.KN_IN:
        return (
            f"ದಾಖಲಾಗಿದೆ. {customer_name} ಅವರಿಂದ {amount_rupees} ರೂಪಾಯಿ ಪಾವತಿ ಸೇರಿಸಲಾಗಿದೆ. "
            f"ಈಗ {bal_phrase}."
        )
    return (
        f"Recorded. Noted {amount_rupees} rupees payment from {customer_name}. "
        f"Updated balance is {bal_phrase}."
    )


def render_reversal_recorded(
    lang: LanguageCode, orig_event: LedgerEvent, balance: CustomerBalance
) -> str:
    amount = abs(orig_event.signed_amount_rupees)
    bal_phrase = _format_balance_phrase(lang, balance.balance_rupees)
    if lang == LanguageCode.HI_IN:
        return (
            f"रिवर्सल दर्ज हो गया। {orig_event.customer_name} की {amount} रुपये की प्रविष्टि "
            f"वापस की गई। अब {bal_phrase}।"
        )
    if lang == LanguageCode.KN_IN:
        return (
            f"ಪ್ರತಿದಾಖಲೆ ಸೇರಿಸಲಾಗಿದೆ. {orig_event.customer_name} ಅವರ {amount} ರೂಪಾಯಿ ದಾಖಲೆ "
            f"ಹಿಂಪಡೆಯಲಾಗಿದೆ. ಈಗ {bal_phrase}."
        )
    return (
        f"Reversal recorded for {orig_event.customer_name}'s {amount} rupees entry. "
        f"Updated balance is {bal_phrase}."
    )


def render_action_cancelled(lang: LanguageCode) -> str:
    if lang == LanguageCode.HI_IN:
        return "ठीक है, यह प्रविष्टि रद्द कर दी गई। खाते में कोई बदलाव नहीं किया गया।"
    if lang == LanguageCode.KN_IN:
        return "ಸರಿ, ಈ ದಾಖಲೆಯನ್ನು ರದ್ದುಗೊಳಿಸಲಾಗಿದೆ. ಖಾತೆಯಲ್ಲಿ ಯಾವುದೇ ಬದಲಾವಣೆ ಮಾಡಲಾಗಿಲ್ಲ."
    return "Okay, cancelled. No changes were recorded in the ledger."


def render_customer_balance(lang: LanguageCode, balance: CustomerBalance) -> str:
    took = getattr(balance, "total_credit_rupees", 0)
    paid = getattr(balance, "total_payment_rupees", 0)
    owed = balance.balance_rupees

    if lang == LanguageCode.HI_IN:
        if owed > 0:
            if paid > 0:
                return (
                    f"{balance.customer_name} ने कुल {took} रुपये का उधार लिया और {paid} रुपये जमा किए। "
                    f"जमा घटाकर, अब {balance.customer_name} को दुकानदार को {owed} रुपये देने हैं।"
                )
            return (
                f"{balance.customer_name} ने कुल {took or owed} रुपये का उधार लिया है। "
                f"अब {balance.customer_name} पर दुकानदार के {owed} रुपये बाकी हैं।"
            )
        elif owed < 0:
            advance = abs(owed)
            return (
                f"{balance.customer_name} ने {took} रुपये का सामान लिया और {paid} रुपये जमा किए। "
                f"{balance.customer_name} के {advance} रुपये एडवांस जमा हैं।"
            )
        else:
            return (
                f"{balance.customer_name} का पूरा हिसाब चुकता है। "
                f"कुल {took} रुपये उधार और {paid} रुपये जमा के बाद 0 रुपये बाकी हैं।"
            )

    if lang == LanguageCode.KN_IN:
        if owed > 0:
            if paid > 0:
                return (
                    f"{balance.customer_name} ಅವರು ಒಟ್ಟು {took} ರೂಪಾಯಿ ಉದ್ರಿ ಪಡೆದು, {paid} ರೂಪಾಯಿ ಪಾವತಿಸಿದ್ದಾರೆ. "
                    f"ಪಾವತಿಯನ್ನು ಕಳೆದ ನಂತರ, {balance.customer_name} ಅವರು ಅಂಗಡಿಯವರಿಗೆ {owed} ರೂಪಾಯಿ ಬಾಕಿ ಕೊಡಬೇಕು."
                )
            return (
                f"{balance.customer_name} ಅವರು ಒಟ್ಟು {took or owed} ರೂಪಾಯಿ ಉದ್ರಿ ಪಡೆದಿದ್ದಾರೆ. "
                f"{balance.customer_name} ಅವರು ಅಂಗಡಿಯವರಿಗೆ {owed} ರೂಪಾಯಿ ಬಾಕಿ ಕೊಡಬೇಕು."
            )
        elif owed < 0:
            advance = abs(owed)
            return (
                f"{balance.customer_name} ಅವರ {advance} ರೂಪಾಯಿ ಮುಂಗಡ ಜಮಾ ಇದೆ."
            )
        else:
            return (
                f"{balance.customer_name} ಅವರ ಸಂಪೂರ್ಣ ಲೆಕ್ಕ ಚುಕ್ತಾ ಆಗಿದೆ. 0 ರೂಪಾಯಿ ಬಾಕಿ."
            )

    if lang == LanguageCode.TE_IN:
        if owed > 0:
            if paid > 0:
                return (
                    f"{balance.customer_name} మొత్తం {took} రూపాయలు అప్పు తీసుకున్నారు మరియు {paid} రూపాయలు చెల్లించారు. "
                    f"చెల్లింపును తీసివేస్తే, {balance.customer_name} దుకాణదారునికి {owed} రూపాయలు బాకీ ఇవ్వాలి."
                )
            return (
                f"{balance.customer_name} మొత్తం {took or owed} రూపాయలు అప్పు తీసుకున్నారు. "
                f"{balance.customer_name} దుకాణదారునికి {owed} రూపాయలు బాకీ ఇవ్వాలి."
            )
        elif owed < 0:
            advance = abs(owed)
            return (
                f"{balance.customer_name} కు {advance} రూపాయల అడ్వాన్స్ జమ ఉంది."
            )
        else:
            return (
                f"{balance.customer_name} లెక్క పూర్తయింది. 0 రూపాయలు బాకీ."
            )

    # Default: English
    if owed > 0:
        if paid > 0:
            return (
                f"{balance.customer_name} took a total of {took} rupees credit and paid {paid} rupees. "
                f"Deducting the payment, {balance.customer_name} owes {owed} rupees to the shopkeeper."
            )
        return (
            f"{balance.customer_name} took a total of {took or owed} rupees credit. "
            f"{balance.customer_name} owes {owed} rupees to the shopkeeper."
        )
    elif owed < 0:
        advance = abs(owed)
        return (
            f"{balance.customer_name} took {took} rupees credit and paid {paid} rupees. "
            f"{balance.customer_name} has {advance} rupees advance balance with the shop."
        )
    return (
        f"{balance.customer_name} took {took} rupees credit and paid back {paid} rupees. "
        "The account is fully settled at 0 rupees owed."
    )


def render_customer_not_found(lang: LanguageCode, customer_name: str) -> str:
    if lang == LanguageCode.HI_IN:
        return f"{customer_name} के नाम से खाते में अभी कोई प्रविष्टि नहीं मिली।"
    if lang == LanguageCode.KN_IN:
        return f"{customer_name} ಹೆಸರಿನಲ್ಲಿ ಖಾತೆಯಲ್ಲಿ ಇನ್ನೂ ಯಾವುದೇ ದಾಖಲೆ ಇಲ್ಲ."
    return f"No ledger entries were found for {customer_name}."


def render_shop_summary(lang: LanguageCode, summary: ShopSummary) -> str:
    if summary.total_events == 0:
        if lang == LanguageCode.HI_IN:
            return "अभी खाते में कोई प्रविष्टि दर्ज नहीं है। कुल शेष 0 रुपये है।"
        if lang == LanguageCode.KN_IN:
            return "ಇನ್ನೂ ಖಾತೆಯಲ್ಲಿ ಯಾವುದೇ ದಾಖಲೆ ಇಲ್ಲ. ಒಟ್ಟು ಬಾಕಿ 0 ರೂಪಾಯಿ ಇದೆ."
        return "There are no ledger entries yet. Total balance is 0 rupees."

    if lang == LanguageCode.HI_IN:
        return (
            f"दुकान के खाते में कुल {summary.customer_count} ग्राहक हैं, "
            f"{summary.active_credit_customers} ग्राहकों का कुल {summary.total_outstanding_rupees} रुपये "
            f"शेष है, और कुल {summary.total_events} प्रविष्टियाँ दर्ज हैं।"
        )
    if lang == LanguageCode.KN_IN:
        return (
            f"ಅಂಗಡಿಯ ಖಾತೆಯಲ್ಲಿ ಒಟ್ಟು {summary.customer_count} ಗ್ರಾಹಕರಿದ್ದಾರೆ, "
            f"{summary.active_credit_customers} ಗ್ರಾಹಕರ ಒಟ್ಟು ಬಾಕಿ {summary.total_outstanding_rupees} ರೂಪಾಯಿ ಇದೆ, "
            f"ಮತ್ತು ಒಟ್ಟು {summary.total_events} ದಾಖಲೆಗಳಿವೆ."
        )
    return (
        f"The ledger has {summary.customer_count} customers, "
        f"{summary.active_credit_customers} with open balance totaling "
        f"{summary.total_outstanding_rupees} rupees across {summary.total_events} entries."
    )


def render_recent_history(
    lang: LanguageCode, events: list[LedgerEvent], customer_name: str | None = None
) -> str:
    if not events:
        if customer_name:
            return render_customer_not_found(lang, customer_name)
        if lang == LanguageCode.HI_IN:
            return "अभी खाते में कोई हालिया प्रविष्टि नहीं है।"
        if lang == LanguageCode.KN_IN:
            return "ಇನ್ನೂ ಖಾತೆಯಲ್ಲಿ ಯಾವುದೇ ಇತ್ತೀಚಿನ ದಾಖಲೆ ಇಲ್ಲ."
        return "There are no recent entries in the ledger."

    shown = events[:3]
    parts: list[str] = []
    for ev in shown:
        amt = abs(ev.signed_amount_rupees)
        if lang == LanguageCode.HI_IN:
            kind = {
                EventType.CREDIT_GIVEN: "उधार",
                EventType.PAYMENT_RECEIVED: "जमा",
                EventType.REVERSAL: "रिवर्सल",
            }[ev.event_type]
            parts.append(f"{ev.customer_name} {amt} रुपये {kind}")
        elif lang == LanguageCode.KN_IN:
            kind = {
                EventType.CREDIT_GIVEN: "ಉದ್ರಿ",
                EventType.PAYMENT_RECEIVED: "ಪಾವತಿ",
                EventType.REVERSAL: "ಪ್ರತಿದಾಖಲೆ",
            }[ev.event_type]
            parts.append(f"{ev.customer_name} {amt} ರೂಪಾಯಿ {kind}")
        else:
            kind = {
                EventType.CREDIT_GIVEN: "credit",
                EventType.PAYMENT_RECEIVED: "payment",
                EventType.REVERSAL: "reversal",
            }[ev.event_type]
            parts.append(f"{ev.customer_name} {amt} rupees {kind}")

    joined = ", ".join(parts)
    if lang == LanguageCode.HI_IN:
        return f"आखिरी {len(shown)} प्रविष्टियाँ हैं: {joined}।"
    if lang == LanguageCode.KN_IN:
        return f"ಕೊನೆಯ {len(shown)} ದಾಖಲೆಗಳು: {joined}."
    return f"Last {len(shown)} entries: {joined}."


def render_nothing_to_undo(lang: LanguageCode, customer_name: str | None = None) -> str:
    if lang == LanguageCode.HI_IN:
        if customer_name:
            return f"{customer_name} के खाते में रिवर्स करने के लिए कोई प्रविष्टि नहीं मिली।"
        return "खाते में रिवर्स करने के लिए कोई हालिया प्रविष्टि नहीं मिली।"
    if lang == LanguageCode.KN_IN:
        if customer_name:
            return f"{customer_name} ಅವರ ಖಾತೆಯಲ್ಲಿ ಹಿಂಪಡೆಯಲು ಯಾವುದೇ ದಾಖಲೆ ಇಲ್ಲ."
        return "ಖಾತೆಯಲ್ಲಿ ಹಿಂಪಡೆಯಲು ಯಾವುದೇ ಇತ್ತೀಚಿನ ದಾಖಲೆ ಇಲ್ಲ."
    if customer_name:
        return f"There is no reversible entry found for {customer_name}."
    return "There is no recent reversible entry in the ledger."


def render_missing_details(
    lang: LanguageCode, intent: IntentType, missing_fields: list[str]
) -> str:
    need_customer = "customer_name" in missing_fields
    need_amount = "amount_rupees" in missing_fields

    if lang == LanguageCode.HI_IN:
        if need_customer and need_amount:
            return "कृपया ग्राहक का नाम और पूरे रुपये की राशि बताएं।"
        if need_customer:
            return "कृपया ग्राहक का नाम बताएं।"
        if need_amount:
            return "कृपया पूरे रुपये में राशि बताएं।"
        return "कृपया ग्राहक का नाम और विवरण फिर से बताएं।"

    if lang == LanguageCode.KN_IN:
        if need_customer and need_amount:
            return "ದಯವಿಟ್ಟು ಗ್ರಾಹಕರ ಹೆಸರು ಮತ್ತು ಪೂರ್ಣ ರೂಪಾಯಿ ಮೊತ್ತವನ್ನು ಹೇಳಿ."
        if need_customer:
            return "ದಯವಿಟ್ಟು ಗ್ರಾಹಕರ ಹೆಸರನ್ನು ಹೇಳಿ."
        if need_amount:
            return "ದಯವಿಟ್ಟು ಪೂರ್ಣ ರೂಪಾಯಿ ಮೊತ್ತವನ್ನು ಹೇಳಿ."
        return "ದಯವಿಟ್ಟು ಗ್ರಾಹಕರ ಹೆಸರು ಮತ್ತು ವಿವರಗಳನ್ನು ಮತ್ತೆ ಹೇಳಿ."

    if need_customer and need_amount:
        return "Please say both the customer's name and the whole rupee amount."
    if need_customer:
        return "Please tell me the customer's name."
    if need_amount:
        return "Please tell me the whole rupee amount."
    return "Please repeat the customer name and details."


def render_unsupported_lending(lang: LanguageCode) -> str:
    if lang == LanguageCode.HI_IN:
        return (
            "वॉइस खाता केवल साधारण दुकान हिसाब लिखने के लिए है। "
            "इसमें ब्याज, लोन या किसी तरह का दबाव संदेश शामिल नहीं है।"
        )
    if lang == LanguageCode.KN_IN:
        return (
            "ವಾಯ್ಸ್ ಖಾತಾ ಕೇವಲ ಸರಳ ಅಂಗಡಿ ಲೆಕ್ಕ ಬರೆಯಲು ಮಾತ್ರ. "
            "ಇದರಲ್ಲಿ ಬಡ್ಡಿ, ಸಾಲ ಅಥವಾ ಯಾವುದೇ ಒತ್ತಡದ ಸೇವೆಗಳಿಲ್ಲ."
        )
    return (
        "Voice Khata is only a simple shop bookkeeping ledger. "
        "It does not support lending, interest, or payment pressure."
    )


def render_unknown(lang: LanguageCode) -> str:
    if lang == LanguageCode.HI_IN:
        return (
            "मैं समझ नहीं पाया। आप किसी ग्राहक का उधार, जमा, हिसाब या आखिरी प्रविष्टि रिवर्स करने को कह सकते हैं।"
        )
    if lang == LanguageCode.KN_IN:
        return (
            "ನನಗೆ ಅರ್ಥವಾಗಲಿಲ್ಲ. ನೀವು ಗ್ರಾಹಕರ ಉದ್ರಿ, ಪಾವತಿ, ಬಾಕಿ ಲೆಕ್ಕ ಅಥವಾ ಕೊನೆಯ ದಾಖಲೆ ರದ್ದು ಮಾಡಲು ಹೇಳಬಹುದು."
        )
    return (
        "I did not understand that. You can record a credit entry, record a payment, "
        "check a balance, or reverse the last entry."
    )


def render_error(lang: LanguageCode) -> str:
    if lang == LanguageCode.HI_IN:
        return "तकनीकी समस्या के कारण यह अनुरोध पूरा नहीं हो सका। कृपया फिर से प्रयास करें।"
    if lang == LanguageCode.KN_IN:
        return "ತಾಂತ್ರಿಕ ತೊಂದರೆಯಿಂದ ಈ ವಿನಂತಿ ಪೂರ್ಣವಾಗಲಿಲ್ಲ. ದಯವಿಟ್ಟು ಮತ್ತೆ ಪ್ರಯತ್ನಿಸಿ."
    if lang == LanguageCode.TE_IN:
        return "సాంకేతిక సమస్య వల్ల ఈ అభ్యర్థన పూర్తి కాలేదు. దయచేసి మళ్లీ ప్రయత్నించండి."
    return "Sorry, something went wrong while processing that request. Please try again."


BANNED_PRESSURE_WORDS: tuple[str, ...] = (
    "overdue",
    "penalty",
    "urgent",
    "urgently",
    "immediately",
    "legal",
    "warning",
    "defaulter",
    "action",
    "threat",
    "court",
    "police",
    "demand",
    "interest",
    "fine",
    "arrest",
    "notice",
    "turant",
    "chetawani",
    "dand",
    "byaj",
    "hechcharike",
    "toralu",
    "vaddhi",
    "hechcharika",
    "last date",
    "due date",
    "seize",
)


def validate_no_pressure_words(text: str) -> None:
    """Raises ValueError if any banned pressure word appears in the text."""
    lower = text.lower()
    for word in BANNED_PRESSURE_WORDS:
        if word in lower:
            raise ValueError(f"Template contains banned pressure word: {word!r}")


def render_item_sum_mismatch(
    lang: LanguageCode | str, items_sum: int, stated_total: int
) -> str:
    """Deterministic prompt when sum of line items does not equal the stated total."""
    norm = LanguageCode.normalize(lang) if isinstance(lang, str) else lang
    if norm == LanguageCode.HI_IN:
        msg = f"Items ka total {items_sum} hai, {stated_total} nahi. Kaun sa sahi?"
    elif norm == LanguageCode.TE_IN:
        msg = f"ఐటమ్స్ మొత్తం {items_sum}, {stated_total} కాదు. ఏది సరైనది?"
    elif norm == LanguageCode.KN_IN:
        msg = f"ಐಟಂಗಳ ಒಟ್ಟು {items_sum}, {stated_total} ಅಲ್ಲ. ಯಾವುದು ಸರಿ?"
    else:
        msg = f"Items total is {items_sum}, not {stated_total}. Which one is correct?"
    validate_no_pressure_words(msg)
    return msg


def render_share_receipt(
    lang: LanguageCode | str,
    shop_name: str,
    date_str: str,
    customer_name: str,
    items: list[dict[str, Any]] | None,
    entry_amount: int,
    current_balance: int,
) -> str:
    """Fixed polite message receipt template.

    Never includes more than: shop name, date, customer name, item lines,
    entry total, current balance, and polite greeting/closing.
    """
    norm = LanguageCode.normalize(lang) if isinstance(lang, str) else lang
    items_list = items or []

    if norm == LanguageCode.HI_IN:
        lines = [f"{shop_name}", f"दिनांक: {date_str}", f"ग्राहक: {customer_name}"]
        if items_list:
            lines.append("सामान:")
            for it in items_list:
                name = it.get("name") or it.get("name_enc") or "सामान"
                qty = it.get("qty_text")
                amt = it.get("amount_inr", 0)
                if qty:
                    lines.append(f"- {name} ({qty}): ₹{amt}")
                else:
                    lines.append(f"- {name}: ₹{amt}")
        lines.append(f"कुल: ₹{entry_amount}")
        lines.append(f"वर्तमान शेष: ₹{current_balance}")
        lines.append("धन्यवाद!")
        msg = "\n".join(lines)
    elif norm == LanguageCode.TE_IN:
        lines = [f"{shop_name}", f"తేదీ: {date_str}", f"కస్టమర్: {customer_name}"]
        if items_list:
            lines.append("వస్తువులు:")
            for it in items_list:
                name = it.get("name") or it.get("name_enc") or "వస్తువు"
                qty = it.get("qty_text")
                amt = it.get("amount_inr", 0)
                if qty:
                    lines.append(f"- {name} ({qty}): ₹{amt}")
                else:
                    lines.append(f"- {name}: ₹{amt}")
        lines.append(f"మొత్తం: ₹{entry_amount}")
        lines.append(f"ప్రస్తుత బాకీ: ₹{current_balance}")
        lines.append("ధన్యవాదాలు!")
        msg = "\n".join(lines)
    elif norm == LanguageCode.KN_IN:
        lines = [f"{shop_name}", f"ದಿನಾಂಕ: {date_str}", f"ಗ್ರಾಹಕ: {customer_name}"]
        if items_list:
            lines.append("ವಸ್ತುಗಳು:")
            for it in items_list:
                name = it.get("name") or it.get("name_enc") or "ವಸ್ತು"
                qty = it.get("qty_text")
                amt = it.get("amount_inr", 0)
                if qty:
                    lines.append(f"- {name} ({qty}): ₹{amt}")
                else:
                    lines.append(f"- {name}: ₹{amt}")
        lines.append(f"ಒಟ್ಟು: ₹{entry_amount}")
        lines.append(f"ಪ್ರಸ್ತುತ ಬಾಕಿ: ₹{current_balance}")
        lines.append("ಧನ್ಯವಾದಗಳು!")
        msg = "\n".join(lines)
    else:
        lines = [f"{shop_name}", f"Date: {date_str}", f"Customer: {customer_name}"]
        if items_list:
            lines.append("Items:")
            for it in items_list:
                name = it.get("name") or it.get("name_enc") or "Item"
                qty = it.get("qty_text")
                amt = it.get("amount_inr", 0)
                if qty:
                    lines.append(f"- {name} ({qty}): Rs. {amt}")
                else:
                    lines.append(f"- {name}: Rs. {amt}")
        lines.append(f"Total: Rs. {entry_amount}")
        lines.append(f"Current balance: Rs. {current_balance}")
        lines.append("Thank you!")
        msg = "\n".join(lines)

    validate_no_pressure_words(msg)
    return msg

