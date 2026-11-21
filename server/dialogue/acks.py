"""Dynamic short acknowledgments and refusal templates for KhataSaathi.

Hard rules:
- Stock voices only, deterministic templates, no LLM generation.
- Dynamic short templates (en, hi, kn, te) always include customer and amount.
- Sent ONLY AFTER commit returns.
- Failed commit speaks an error, never success.
- OUT_OF_SCOPE gets a fixed polite refusal.
"""

from __future__ import annotations


def render_commit_ack(
    lang: str,
    customer: str,
    amount_rupees: int,
    direction: str,
) -> str:
    """Short dynamic acknowledgment sent ONLY AFTER commit returns. Always includes customer & amount."""
    lang_code = (lang or "hi").lower()
    is_credit = direction == "CREDIT_GIVEN"

    if lang_code.startswith("hi"):
        kind = "उधार लिख दिया" if is_credit else "जमा लिख दिया"
        return f"{customer}: ₹{amount_rupees} {kind}।"

    if lang_code.startswith("kn"):
        kind = "ಉದ್ಧರಿ ದಾಖಲಾಗಿದೆ" if is_credit else "ಜಮಾ ದಾಖಲಾಗಿದೆ"
        return f"{customer}: ₹{amount_rupees} {kind}."

    if lang_code.startswith("te"):
        kind = "అప్పు రాశాను" if is_credit else "జమ రాశాను"
        return f"{customer}: ₹{amount_rupees} {kind}."

    # English default
    kind = "credit added" if is_credit else "payment received"
    return f"{customer}: Rs {amount_rupees} {kind}."


def render_correction_ack(
    lang: str,
    customer: str,
    amount_rupees: int,
    direction: str,
) -> str:
    """Short dynamic correction acknowledgment."""
    lang_code = (lang or "hi").lower()
    is_credit = direction == "CREDIT_GIVEN"

    if lang_code.startswith("hi"):
        kind = "उधार" if is_credit else "जमा"
        return f"{customer}: सुधार कर दिया, ₹{amount_rupees} {kind}।"

    if lang_code.startswith("kn"):
        kind = "ಉದ್ಧರಿ" if is_credit else "ಜಮಾ"
        return f"{customer}: ಸರಿಪಡಿಸಲಾಗಿದೆ, ₹{amount_rupees} {kind}."

    if lang_code.startswith("te"):
        kind = "అప్పు" if is_credit else "జమ"
        return f"{customer}: సరిదిద్దాను, ₹{amount_rupees} {kind}."

    kind = "credit" if is_credit else "payment"
    return f"{customer}: Corrected to Rs {amount_rupees} {kind}."


def render_undo_ack(
    lang: str,
    customer: str,
    amount_rupees: int,
    direction: str,
) -> str:
    """Explicitly states what was reversed before appending reversal."""
    lang_code = (lang or "hi").lower()
    is_credit = direction == "CREDIT_GIVEN"

    if lang_code.startswith("hi"):
        kind = "उधार" if is_credit else "पेमेंट"
        return f"{customer} का ₹{amount_rupees} {kind} हटा दिया।"

    if lang_code.startswith("kn"):
        kind = "ಉದ್ಧರಿ" if is_credit else "ಪಾವತಿ"
        return f"{customer} ಅವರ ₹{amount_rupees} {kind} ಹಿಂಪಡೆಯಲಾಗಿದೆ."

    if lang_code.startswith("te"):
        kind = "అప్పు" if is_credit else "పేమెంట్"
        return f"{customer} యొక్క ₹{amount_rupees} {kind} రద్దు చేశాను."

    kind = "credit" if is_credit else "payment"
    return f"Removed {customer}'s Rs {amount_rupees} {kind}."


def render_out_of_scope_refusal(lang: str) -> str:
    """Fixed polite refusal for lending, pressure messages, and health advice."""
    lang_code = (lang or "hi").lower()

    if lang_code.startswith("hi"):
        return (
            "माफ़ कीजिए, यह सिस्टम केवल दुकान के उधार और जमा के लिए है। "
            "क़र्ज़, दबाव वाले संदेश या स्वास्थ्य सलाह यहाँ उपलब्ध नहीं है।"
        )

    if lang_code.startswith("kn"):
        return (
            "ಕ್ಷಮಿಸಿ, ಈ ವ್ಯವಸ್ಥೆಯು ಅಂಗಡಿಯ ಉದ್ಧರಿ ಮತ್ತು ಜಮಾ ಲೆಕ್ಕಕ್ಕೆ ಮಾತ್ರ. "
            "ಸಾಲ, ಒತ್ತಡದ ಸಂದೇಶ ಅಥವಾ ಆರೋಗ್ಯ ಸಲಹೆ ಲಭ್ಯವಿಲ್ಲ."
        )

    if lang_code.startswith("te"):
        return (
            "క్షమించండి, ఈ సిస్టమ్ కేవలం దుకాణం అప్పు మరియు జమ లెక్కల కోసం మాత్రమే. "
            "రుణాలు, ఒత్తిడి సందೇಶాలు లేదా ఆరోగ్య సలహాలు అందించబడవు."
        )

    return (
        "Sorry, this system only supports shop credit and payment ledger entries. "
        "Loans, recovery messages, and medical advice are not supported."
    )


def render_commit_error(lang: str) -> str:
    """Failed commit speaks an error, never success."""
    lang_code = (lang or "hi").lower()

    if lang_code.startswith("hi"):
        return "खाते में प्रविष्टि दर्ज नहीं हो सकी। कृपया दोबारा बोलें।"

    if lang_code.startswith("kn"):
        return "ಖಾತೆಯಲ್ಲಿ ದಾಖಲಿಸಲು ಸಾಧ್ಯವಾಗಲಿಲ್ಲ. ದಯವಿಟ್ಟು ಮತ್ತೊಮ್ಮೆ ಪ್ರಯತ್ನಿಸಿ."

    if lang_code.startswith("te"):
        return "ఖాతాలో నమోదు చేయడం విఫలమైంది. దయచేసి మళ్ళీ ప్రయత్నించండి."

    return "Failed to record transaction. Please try again."


def render_which_entry_question(lang: str) -> str:
    """When correction TTL expires, ask which entry to change."""
    lang_code = (lang or "hi").lower()

    if lang_code.startswith("hi"):
        return "कौन सी प्रविष्टि बदलनी है? कृपया ग्राहक का नाम या तारीख बताएं।"

    if lang_code.startswith("kn"):
        return "ಯಾವ ದಾಖಲೆಯನ್ನು ಬದಲಾಯಿಸಬೇಕು? ದಯವಿಟ್ಟು ಗ್ರಾಹಕರ ಹೆಸರು ಅಥವಾ ದಿನಾಂಕವನ್ನು ತಿಳಿಸಿ."

    if lang_code.startswith("te"):
        return "ఏ రికార్డును మార్చాలి? దయచేసి కస్టమర్ పేరు లేదా తేదీని చెప్పండి."

    return "Which entry do you want to change? Please state the customer or date."
