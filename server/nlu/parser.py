"""Deterministic NLU intent and candidate parser for KhataSaathi.

Architecture Rule:
Models propose meaning; deterministic code owns money, identity checks, commits,
and balance arithmetic. Never silently guess a financial field.

Intents:
- CREDIT_GIVEN
- PAYMENT_RECEIVED
- BALANCE_QUERY
- TODAY_CREDIT_QUERY
- TODAY_PAYMENTS_QUERY
- TOTAL_OUTSTANDING_QUERY
- CORRECTION
- UNDO
- YES
- NO
- OUT_OF_SCOPE (lending, pressure messages, health advice)

Direction rules:
- "ko ... udhaar diya" is credit (CREDIT_GIVEN)
- "ne ... diye" is payment (PAYMENT_RECEIVED)
- "ko ... diye" is credit (CREDIT_GIVEN)
- "kal 200 diya tha" or "340 likho" has no direction -> direction=None
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from server.nlu.dates import resolve_business_date
from server.nlu.numbers import extract_amounts, parse_amount


class Intent(str, Enum):
    CREDIT_GIVEN = "CREDIT_GIVEN"
    PAYMENT_RECEIVED = "PAYMENT_RECEIVED"
    BALANCE_QUERY = "BALANCE_QUERY"
    TODAY_CREDIT_QUERY = "TODAY_CREDIT_QUERY"
    TODAY_PAYMENTS_QUERY = "TODAY_PAYMENTS_QUERY"
    TOTAL_OUTSTANDING_QUERY = "TOTAL_OUTSTANDING_QUERY"
    CORRECTION = "CORRECTION"
    UNDO = "UNDO"
    YES = "YES"
    NO = "NO"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


@dataclass
class ParsedCandidate:
    intent: Intent
    customer_name: str | None = None
    customer_id: str | None = None
    amount_paise: int | None = None
    direction: str | None = None  # "CREDIT_GIVEN" | "PAYMENT_RECEIVED" | None
    business_date: str | None = None  # YYYY-MM-DD
    date_phrase: str | None = None
    confidence: float = 0.95
    number_contextual: bool = False
    is_rule_based: bool = True
    raw_text: str = ""
    correction_field: str | None = None  # "amount" | "customer" | "date" | "direction"
    rejection_reason: str | None = None


_LB = r"(?<![0-9A-Za-z\u0900-\u0CFF])"
_RB = r"(?![0-9A-Za-z\u0900-\u0CFF])"

# ---------------------------------------------------------------------------
# Out of Scope Patterns (Lending, Pressure, Health)
# ---------------------------------------------------------------------------
_OUT_OF_SCOPE_LENDING = re.compile(
    rf"""(?xi)
    {_LB}(?:
        loan|interest|byaaj|byaj|vyaj|baddi|vaddi|emi|finance|interest\s+rate|percent\s+interest
        |ब्याज|लोन|ईएमआई|ऋण|किस्त|ब्याज\s*दर|ವಡ್ಡ|ಬಡ್ಡಿ|ಸಾಲದ\s+ಬಡ್ಡಿ|వడ్డీ|రుణం
    ){_RB}
    """
)

_OUT_OF_SCOPE_PRESSURE = re.compile(
    rf"""(?xi)
    {_LB}(?:
        police|case|fir|court|lawyer|threat|threaten|recovery\s+agent|goon|gunda|pitunga|marunga
        |chori|chor|shikayat|notice|legal\s+notice|warn|warning
        |पुलिस|शिकायत|केस|कोर्ट|वकील|धमकी|गुंडा|मारूंगा|पीटूंगा|रिकवरी
        |ಪೊಲೀಸ್|ದೂರು|ಕೋರ್ಟ್|ಬೆದರಿಕೆ|ಹೊಡೆಯುತ್ತೇನೆ|పోలీస్|కేసు|కోర్టు|బెదిరింపు
    ){_RB}
    """
)

_OUT_OF_SCOPE_HEALTH = re.compile(
    rf"""(?xi)
    {_LB}(?:
        medicine|medicines|tablet|tablets|doctor|disease|illness|fever|cough|cold|infection
        |blood\s+pressure|sugar|diabetes|pain|syrup|dawa|davai|dawai|bukhar|sardi|khansi|dard
        |दवा|दवाई|दवाइयां|डॉक्टर|बुखार|सर्दी|खांसी|दर्द|बीमारी|शुगर|बीपी|इलाज
        |ಔಷಧ|ಮಾತ್ರೆ|ವೈದ್ಯರು|ಜ್ವರ|ಶೀತ|ಕೆಮ್ಮು|ಮದ್ದು|మందు|డాక్టర్|జ్వరం|దగ్గు|రొంప
    ){_RB}
    """
)


# ---------------------------------------------------------------------------
# Dialogue Control (YES, NO, UNDO, CORRECTION)
# ---------------------------------------------------------------------------
_YES_RE = re.compile(
    rf"""(?xi)
    ^(?:
        haan|han|ha|yes|yep|yeah|sahi|sahi\s+hai|theek\s+hai|thik\s+hai|theek|ok|okay|correct
        |avunu|sari|haudu|ಹೌದು|ಸರಿ|हाँ|हाँ\s+सही\s+है|सही|सही\s+है|ठीक|ठीक\s+है|అవును|సరే
    )(?:\s+(?:sahi\s+hai|sahi|hai|theek|theek\s+hai|correct))*$
    """
)

_NO_RE = re.compile(
    rf"""(?xi)
    ^(?:
        nahi|nahin|na|no|nope|galat|incorrect|wrong|cancel
        |kaadu|vaddu|alla|beda|ಅಲ್ಲ|ಬೇಡ|नहीं|गलत|కాదు|వద్దు
    )$
    """
)

_UNDO_RE = re.compile(
    rf"""(?xi)
    {_LB}(?:
        undo|revert|wapas\s+lo|waapas\s+lo|hata\s+do|cancel\s+last|delete\s+last|pichla\s+hatao|pichhla\s+hatao
        |raddu\s+cheyi|himpade
        |वापस\s+लो|हटा\s+दो|रद्द\s+करो|पिछला\s+हटाओ|ಹಿಂಪಡೆ|రద్దు\s+చేయి
    ){_RB}
    """
)

_CORRECTION_PREFIX_RE = re.compile(
    rf"""(?xi)
    ^(?:nahi|nahin|no|galat|change|badlo|नहीं|गलत|కాదు|ಅಲ್ಲ)[\s,]+
    """
)


# ---------------------------------------------------------------------------
# Query Patterns (Balance, Today's Credit, Today's Payments, Total Outstanding)
# ---------------------------------------------------------------------------
_BALANCE_QUERY_RE = re.compile(
    rf"""(?xi)
    {_LB}(?:
        balance|hisaab|hisab|baaki\s+kitna|kitna\s+baaki|kitna\s+dena|kitne\s+paise\s+dena|kitne\s+paise|kitna\s+banta|kitna\s+hai
        |how\s+(?:much|many)(?:\s+money)?.*?(?:owe|owes|due|pay|give|pending|banta|left)
        |how\s+(?:much|many)(?:\s+money)?\s+(?:should|does|did)
        |what\s+is.*(?:balance|due|pending|hisaab|account)
        |baaki\s+entha|balance\s+entha|entha\s+(?:dabbu\s+)?ivvali|lekkalu
        |baaki\s+eshtu|balance\s+eshtu|eshtu\s+(?:duddu\s+|hana\s+)?kodabeku
        |हिसाब|बैलेंस|बाकी\s+कितना|कितना\s+बाकी|कितना\s+देना|कितने\s+पैसे\s+देने|कितने\s+पैसे
        |బాకీ\s+ఎంత|బ్యాలెన్స్\s+ఎంత|ఎంత\s+ఇవ్వాలి|బాకీ|లెక్క
        |ಬಾಕಿ\s+ಎಷ್ಟು|ಬ್ಯಾಲೆನ್ಸ್\s+ಎಷ್ಟು|ಎಷ್ಟು\s+ಕೊಡಬೇಕು|ಬಾಕಿ|ಲೆಕ್ಕ
    ){_RB}
    """
)

_TODAY_CREDIT_QUERY_RE = re.compile(
    rf"""(?xi)
    {_LB}(?:
        aaj\s+kitna\s+udhaar|aaj\s+ka\s+udhaar|aaj\s+ka\s+credit|today.*credit|credit.*today
        |eeroju.*appu|ivattu.*udhdhari|ivattina\s+udhdhari|ಇವತ್ತು.*ಉದ್ಧರಿ
        |आज\s+कितना\s+उधार|आज\s+का\s+उधार|आज\s+का\s+क्रेडिट
        |ఈరోజు.*అప్పు
    ){_RB}
    """
)

_TODAY_PAYMENTS_QUERY_RE = re.compile(
    rf"""(?xi)
    {_LB}(?:
        aaj\s+kitna\s+jama|aaj\s+kitna\s+payment|aaj\s+kitna\s+aaya|today.*payment|today.*collection|payment.*today
        |eeroju.*jama|eeroju.*payment|ivattu.*jama|ivattina\s+collection|ಇವತ್ತು.*ಜಮಾ
        |आज\s+कितना\s+जमा|आज\s+कितना\s+पेमेंट|आज\s+कितने\s+पैसे\s+आए
        |ఈరోజు.*జమ
    ){_RB}
    """
)

_TOTAL_OUTSTANDING_QUERY_RE = re.compile(
    rf"""(?xi)
    {_LB}(?:
        total\s+outstanding|total\s+udhaar|kul\s+(?:kitna\s+)?baaki|kul\s+udhaar|total\s+baaki|sabka\s+kitna\s+baaki
        |total\s+money\s+to\s+be\s+collected|overall\s+balance|total\s+due
        |motham\s+(?:entha\s+)?baaki|ottu\s+baaki|ಒಟ್ಟು\s+ಬಾಕಿ
        |कुल\s+बाकी|कुल\s+उधार|सबका\s+कितना\s+बाकी|टोटल\s+बाकी|मंडी\s+में\s+कितना\s+बाकी
        |మొత్తం\s+(?:ఎంత\s+)?బాకీ
    ){_RB}
    """
)


# ---------------------------------------------------------------------------
# Strict Direction Rules (as specified in prompt)
# ---------------------------------------------------------------------------
_CREDIT_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Hindi: "ko ... udhaar diya", "ko ... udhaar", "ko ... diye", "ko ... diya", "udhaar लिखो"
    re.compile(r"""(?xi)\b\w+\s+ko\b.*?\b(?:udhaar|udhar)\s+(?:diya|diye|de\s+do|likh|likho|rakh)\b"""),
    re.compile(r"""(?xi)\b\w+\s+ko\b.*?\b(?:diye|diya|de\s+diye|de\s+diya)\b"""),
    re.compile(r"""(?xi)\b(?:udhaar|udhar)\s+(?:diya|likho|likh\s+do|rakh|daal)\b"""),
    re.compile(rf"""(?xi){_LB}[\u0900-\u097F\w]+\s+के\s+खाते\s+में{_RB}.*?{_LB}(?:उधार|क्रेडिट){_RB}"""),
    re.compile(rf"""(?xi){_LB}(?:उधार|क्रेडिट)\s+(?:लिखो|लिख\s+दो|रखो|डाला|दिया|दे\s+दो){_RB}"""),
    re.compile(rf"""(?xi){_LB}[\u0900-\u097F]+\s+को{_RB}.*?{_LB}(?:उधार\s+दिया|दिए|दिया|उधार\s+लिखो|उधार){_RB}"""),
    # English: "gave credit to X", "gave X 500", "credit X with", "sold on credit"
    re.compile(r"""(?xi)\b(?:gave|given)\b.*?\b(?:to\s+\w+|credit)\b"""),
    re.compile(r"""(?xi)\bcredit\s+(?:to\s+)?\w+\b"""),
    re.compile(r"""(?xi)\b(?:gave|give)\s+\w+\b.*?\b(?:credit|rupees|rs)\b"""),
    re.compile(r"""(?xi)\badd\b.*?\bcredit\b"""),
    # Telugu: "ki ... appu", "ki ... icchanu", "ki ... ivvandi", "appu icchanu"
    re.compile(r"""(?xi)\b\w+\s*(?:ki|ku)\b.*?\b(?:appu|icchanu|iccharu|ivvandi)\b"""),
    re.compile(rf"""(?xi){_LB}[\u0C00-\u0C7F]+\s*కి{_RB}.*?{_LB}(?:అప్పు|ఇచ్చాను|ఇచ్చారు|ఇవ్వండి){_RB}"""),
    re.compile(r"""(?xi)\bappu\s+(?:icchanu|pettandi|rayandi)\b"""),
    re.compile(rf"""(?xi){_LB}అప్పు\s+(?:ఇచ్చాను|రాయండి|పెట్టండి){_RB}"""),
    re.compile(rf"""(?xi){_LB}[\u0C00-\u0C7F]+\s*(?:కి|కు){_RB}.*?{_LB}(?:ఇచ్చారు|ఇచ్చాను|ఇవ్వండి){_RB}"""),
    # Kannada: "ge ... udhdhari", "ge ... kotte", "udhdhari kotte"
    re.compile(r"""(?xi)\b\w+\s*(?:ge)\b.*?\b(?:udhdhari|uddhari|udri|kotte|kottaru|baredi|bare|rupees\s+udhdhari)\b"""),
    re.compile(rf"""(?xi){_LB}[\u0C80-\u0CFF]+\s*ಗೆ{_RB}.*?{_LB}(?:ಉದ್ಧರಿ|ಉದ್ರಿ|ಕೊಟ್ಟೆ|ಕೊಟ್ಟರು|ಬರೆ|ಬರೆಯಿರಿ){_RB}"""),
    re.compile(r"""(?xi)\b(?:udhdhari|uddhari|udri)\s+(?:kotte|bare|baredi|kottaru)\b"""),
    re.compile(rf"""(?xi){_LB}(?:ಉದ್ಧರಿ|ಉದ್ರಿ)\s+(?:ಕೊಟ್ಟೆ|ಬರೆ|ಬರೆಯಿರಿ|ಕೊಟ್ಟರು){_RB}"""),
    re.compile(rf"""(?xi){_LB}[\u0C80-\u0CFF]+\s*(?:ಗೆ){_RB}.*?{_LB}(?:ಕೊಟ್ಟರು|ಕೊಟ್ಟೆ){_RB}"""),
)

_PAYMENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Hindi: "ne ... diye", "ne ... jama kiye", "se ... mila", "se ... aaya"
    re.compile(r"""(?xi)\b\w+\s+ne\b.*?\b(?:diye|diya|jama\s+kiye|jama\s+kiya|chukaye|chukaya|payment)\b"""),
    re.compile(r"""(?xi)\b\w+\s+se\b.*?\b(?:mile|mila|payment\s+aaya|paisa\s+aaya|transfer\s+aaya)\b"""),
    re.compile(r"""(?xi)\b(?:jama\s+kar\s+do|jama\s+likho|payment\s+aaya|bhugtan)\b"""),
    re.compile(rf"""(?xi){_LB}[\u0900-\u097F]+\s+ने{_RB}.*?{_LB}(?:दिए|दिया|जमा\s+किए|जमा\s+किया|चुकाए){_RB}"""),
    re.compile(rf"""(?xi){_LB}[\u0900-\u097F]+\s+से{_RB}.*?{_LB}(?:मिले|मिला|पेमेंट\s+आया){_RB}"""),
    re.compile(rf"""(?xi){_LB}(?:जमा\s+करो|जमा\s+लिखो|जमा\s+हुआ){_RB}"""),
    # English: "X paid", "received from X", "got from X", "payment from X", "X returned"
    re.compile(r"""(?xi)\b\w+\s+(?:paid|cleared|settled|returned|repaid)\b"""),
    re.compile(r"""(?xi)\b(?:returned|repaid|repay|wapas\s+diye|wapas\s+diya|wapas\s+kiye|wapas\s+kiya)\b"""),
    re.compile(rf"""(?xi){_LB}(?:वापस|लौटा|चुका)(?:\s+(?:दिए|दिया|किया|किए))?{_RB}"""),
    re.compile(r"""(?xi)\b(?:received|got|collected)\b.*?\bfrom\s+\w+\b"""),
    re.compile(r"""(?xi)\bpayment\s+from\s+\w+\b"""),
    # Telugu: "nundi vacchindi", "iccharu", "jama chesaru"
    re.compile(r"""(?xi)\b\w+\s*(?:nundi|nunchi)\b.*?\b(?:vacchindi|vaccharu|payment)\b"""),
    re.compile(r"""(?xi)\b\w+\s+(?:jama\s+chesaru|pay\s+chesaru|iccharu|payment\s+iccharu)\b"""),
    re.compile(rf"""(?xi){_LB}[\u0C00-\u0C7F]+\s*నుండి{_RB}.*?{_LB}(?:వచ్చింది|వచ్చాయి|పేమెంట్){_RB}"""),
    re.compile(rf"""(?xi){_LB}(?:జమ\s+చేశారు|చెల్లించారు|జమ\s+చేసారు|పేమెంట్\s+చేశారు|చెల్లింపు){_RB}"""),
    re.compile(rf"""(?xi){_LB}[\u0C00-\u0C7F]+\s+.*?{_LB}(?:ఇచ్చారు){_RB}"""),
    # Kannada: "ninda bantu", "kottaru", "jama madidaru"
    re.compile(r"""(?xi)\b\w+\s*(?:ninda)\b.*?\b(?:bantu|kottaru)\b"""),
    re.compile(r"""(?xi)\b\w+\s*(?:jama\s+madidaru|payment\s+kottaru|payment\s+madidaru|kottaru|jama\s+kottaru)\b"""),
    re.compile(rf"""(?xi){_LB}[\u0C80-\u0CFF]+\s*ನಿಂದ{_RB}.*?{_LB}(?:ಬಂತು|ಕೊಟ್ಟರು){_RB}"""),
    re.compile(rf"""(?xi){_LB}(?:ಜಮಾ\s+ಮಾಡಿದರು|ಪಾವತಿಸಿದರು|ಜಮಾ\s+ಕೊಟ್ಟರು|ಜಮಾ\s+ಆಯಿತು){_RB}"""),
    re.compile(rf"""(?xi){_LB}[\u0C80-\u0CFF]+\s+.*?{_LB}(?:ಕೊಟ್ಟರು){_RB}"""),
)


def _check_direction(text: str) -> str | None:
    """Return 'CREDIT_GIVEN', 'PAYMENT_RECEIVED', or None if direction is not spoken.

    Strict rules:
    - 'ko ... udhaar diya' -> CREDIT_GIVEN
    - 'ne ... diye' -> PAYMENT_RECEIVED
    - 'ko ... diye' -> CREDIT_GIVEN
    - 'kal 200 diya tha' or '340 likho' -> None
    """
    normalized = unicodedata.normalize("NFC", text)

    has_credit_kw = bool(re.search(rf"""(?xi){_LB}(?:udhdhari|uddhari|udri|ಉದ್ಧರಿ|ಉದ್ರಿ|appu|అప్పు){_RB}""", normalized))
    has_repayment = bool(re.search(rf"""(?xi){_LB}(?:wapas|chukaye|chukaya|వాపస్|ಹಿಂತಿರುಗಿಸಿದ){_RB}""", normalized))

    # Specific Hindi disambiguation: "ko ... diye" vs "ne ... diye"
    has_ko_diye = bool(
        re.search(r"""(?xi)\b\w+\s+ko\b.*?\b(diye|diya|de\s+do)\b""", normalized)
        or re.search(rf"""(?xi){_LB}[\u0900-\u097F]+\s+को{_RB}.*?{_LB}(दिए|दिया|दे\s+दो){_RB}""", normalized)
    )
    has_ne_diye = bool(
        re.search(r"""(?xi)\b\w+\s+ne\b.*?\b(diye|diya|jama|chukaye)\b""", normalized)
        or re.search(rf"""(?xi){_LB}[\u0900-\u097F]+\s+ने{_RB}.*?{_LB}(दिए|दिया|जमा|चुकाए){_RB}""", normalized)
    )

    if has_ko_diye and not has_ne_diye:
        return "CREDIT_GIVEN"
    if has_ne_diye and not has_ko_diye:
        return "PAYMENT_RECEIVED"

    # Credit keyword without repayment (e.g. "Manju ge 2000 udhdhari kottaru")
    if has_credit_kw and not has_repayment:
        return "CREDIT_GIVEN"

    # Dative giving ("X ge 200 kottaru" or "X ki 500 iccharu")
    has_dative_giving = bool(
        re.search(r"""(?xi)\b\w+\s*(?:ge|ki|ku)\b.*?\b(?:kotte|kottaru|icchanu|iccharu|ivvandi)\b""", normalized)
        or re.search(rf"""(?xi){_LB}[\u0C00-\u0C7F]+\s*(?:కి|కు){_RB}.*?{_LB}(?:ఇచ్చాను|ఇచ్చారు|ఇవ్వండి){_RB}""", normalized)
        or re.search(rf"""(?xi){_LB}[\u0C80-\u0CFF]+\s*(?:ಗೆ){_RB}.*?{_LB}(?:ಕೊಟ್ಟೆ|ಕೊಟ್ಟರು){_RB}""", normalized)
        or re.search(rf"""(?xi){_LB}[\u0900-\u097F\w]+\s+के\s+खाते\s+में{_RB}.*?{_LB}(?:उधार|क्रेडिट){_RB}""", normalized)
    )
    if has_dative_giving:
        return "CREDIT_GIVEN"

    is_payment = any(bool(pat.search(normalized)) for pat in _PAYMENT_PATTERNS)
    is_credit = any(bool(pat.search(normalized)) for pat in _CREDIT_PATTERNS)

    if is_credit and not is_payment:
        return "CREDIT_GIVEN"
    if is_payment and not is_credit:
        return "PAYMENT_RECEIVED"

    # Conflicting or missing direction -> None
    return None


def _extract_customer_name(
    text: str,
    aliases: Sequence[tuple[str, str]] | None = None,
) -> tuple[str | None, str | None]:
    """Extract customer name and customer_id (if matched in aliases).

    Never guesses or overrides spoken names. Returns (customer_name, customer_id).
    """
    norm = unicodedata.normalize("NFC", text).strip()

    # 1. If customer directory / aliases are provided, match exact or prefix
    if aliases:
        for cid, alias in aliases:
            a_norm = unicodedata.normalize("NFC", alias).casefold()
            pat = rf"(?i)\b{re.escape(a_norm)}\b"
            if re.search(pat, norm.casefold()):
                return alias, cid

    # 2. Heuristic extraction of capitalized name or Indic entity
    # Common post-position markers in Hindi/English/Telugu/Kannada
    # e.g. "Ramesh ke khate", "Ramesh ko", "Ramesh ne", "for Suresh", "should Ramesh owe", "Ramesh ge"
    m = re.search(
        r"""(?xi)
        \b(?:for|to|from|should|does|did)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)
        | ([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+(?:ke|ko|ne|se|ka|ki|nundi|nunchi|ki|ku|ge|owe|owes|pay|due|has|have|kodabeku|ivvali)\b
        | ([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+(?:\d+|teen|two|three|do|రెండు|ಎರಡು|udhaar|jama|paid|returned|repaid|wapas)
        | ([\u0900-\u097F]+)\s+(?:के|को|ने|से|का|की|देना)\b
        | ([\u0C00-\u0C7F]+)\s*(?:కి|కు|నుండి|ఇవ్వాలి)\b
        | ([\u0C80-\u0CFF]+)\s*(?:ಗೆ|ನಿಂದ|ಕೊಡಬೇಕು)\b
        """,
        norm,
    )
    if m:
        for g in m.groups():
            if g:
                cand = g.strip()
                # Exclude day names or keywords or pronouns
                if cand.casefold() not in (
                    "today", "yesterday", "tomorrow", "kal", "aaj", "parso", "monna", "ninna",
                    "he", "she", "him", "her", "they", "them", "his", "it", "usne", "unhone", "uska", "unka", "avaru", "atanu",
                    "how", "many", "much", "money", "should", "does", "did", "owe", "owes", "due", "pay"
                ):
                    return cand, None

    # Fallback: check any word in the utterance if capitalized and not a command word/pronoun
    query_stop_words = {
        "please", "add", "record", "note", "check", "what", "how", "today", "yesterday", "tomorrow", "undo",
        "he", "she", "him", "her", "they", "them", "his", "it", "usne", "unhone", "uska", "unka", "avaru", "atanu",
        "many", "much", "money", "should", "does", "did", "owe", "owes", "due", "pay", "give", "total", "balance"
    }
    words = norm.split()
    for w in words:
        clean = w.strip(",.:;!?")
        if clean and clean[0].isupper() and clean.isalpha() and clean.casefold() not in query_stop_words:
            return clean, None

    return None, None


def parse_transcript(
    text: str,
    *,
    aliases: Sequence[tuple[str, str]] | None = None,
    in_transaction_context: bool = False,
) -> ParsedCandidate:
    """Deterministically parse an utterance into an intent and candidate fields.

    The model may only propose meaning; this deterministic parser owns meaning
    and strictly enforces all safety, intent, and direction invariants.
    """
    raw = (text or "").strip()
    if not raw:
        return ParsedCandidate(
            intent=Intent.NO,
            raw_text=raw,
            confidence=1.0,
            rejection_reason="empty_utterance",
        )

    # 1. Out of Scope Check (Lending, Pressure, Health)
    if _OUT_OF_SCOPE_LENDING.search(raw):
        return ParsedCandidate(
            intent=Intent.OUT_OF_SCOPE,
            raw_text=raw,
            confidence=0.99,
            rejection_reason="unsupported_lending",
        )
    if _OUT_OF_SCOPE_PRESSURE.search(raw):
        return ParsedCandidate(
            intent=Intent.OUT_OF_SCOPE,
            raw_text=raw,
            confidence=0.99,
            rejection_reason="pressure_language",
        )
    if _OUT_OF_SCOPE_HEALTH.search(raw):
        return ParsedCandidate(
            intent=Intent.OUT_OF_SCOPE,
            raw_text=raw,
            confidence=0.99,
            rejection_reason="health_advice",
        )

    # 2. Dialogue Control Check: YES, NO, UNDO
    if _YES_RE.match(raw.strip()):
        return ParsedCandidate(intent=Intent.YES, raw_text=raw, confidence=0.99)

    # Check for CORRECTION before NO: e.g. "nahi, 430" or "nahi Ramesh tha"
    if _CORRECTION_PREFIX_RE.match(raw.strip()):
        rest = _CORRECTION_PREFIX_RE.sub("", raw.strip()).strip()
        amt_corr = parse_amount(rest, in_transaction_context=True)
        date_res = resolve_business_date(rest)
        cust_name, cust_id = _extract_customer_name(rest, aliases)
        dir_corr = _check_direction(rest)

        corr_field = "amount" if amt_corr is not None else (
            "customer" if cust_name is not None else (
                "date" if date_res is not None else (
                    "direction" if dir_corr is not None else "amount"
                )
            )
        )
        return ParsedCandidate(
            intent=Intent.CORRECTION,
            amount_paise=(amt_corr * 100) if amt_corr is not None else None,
            customer_name=cust_name,
            customer_id=cust_id,
            direction=dir_corr,
            business_date=date_res.business_date if date_res else None,
            date_phrase=date_res.date_phrase if date_res else None,
            correction_field=corr_field,
            raw_text=raw,
            confidence=0.95,
        )

    if _NO_RE.match(raw.strip()):
        return ParsedCandidate(intent=Intent.NO, raw_text=raw, confidence=0.99)

    if _UNDO_RE.search(raw):
        return ParsedCandidate(intent=Intent.UNDO, raw_text=raw, confidence=0.98)

    # 3. Read Queries (Total Outstanding, Today's Credit, Today's Payments, Balance)
    if _TOTAL_OUTSTANDING_QUERY_RE.search(raw):
        return ParsedCandidate(intent=Intent.TOTAL_OUTSTANDING_QUERY, raw_text=raw, confidence=0.95)

    if _TODAY_CREDIT_QUERY_RE.search(raw):
        return ParsedCandidate(intent=Intent.TODAY_CREDIT_QUERY, raw_text=raw, confidence=0.95)

    if _TODAY_PAYMENTS_QUERY_RE.search(raw):
        return ParsedCandidate(intent=Intent.TODAY_PAYMENTS_QUERY, raw_text=raw, confidence=0.95)

    if _BALANCE_QUERY_RE.search(raw):
        cname, cid = _extract_customer_name(raw, aliases)
        return ParsedCandidate(
            intent=Intent.BALANCE_QUERY,
            customer_name=cname,
            customer_id=cid,
            raw_text=raw,
            confidence=0.95,
        )

    # 4. Financial Transaction (Credit or Payment or Directionless)
    direction = _check_direction(raw)
    amount_rupees = parse_amount(raw, in_transaction_context=(in_transaction_context or direction is not None))
    cname, cid = _extract_customer_name(raw, aliases)
    date_res = resolve_business_date(raw)

    # Check if number was contextual (e.g. "three forty")
    amt_spans = extract_amounts(raw, in_transaction_context=(in_transaction_context or direction is not None))
    is_contextual = any(s.is_contextual for s in amt_spans) if amt_spans else False

    # Determine intent
    if direction == "CREDIT_GIVEN":
        intent = Intent.CREDIT_GIVEN
    elif direction == "PAYMENT_RECEIVED":
        intent = Intent.PAYMENT_RECEIVED
    else:
        # Direction is None: e.g. "kal 200 diya tha", "340 likho", "Ramesh 500"
        intent = Intent.CREDIT_GIVEN  # Candidate intent placeholder, but direction=None triggers CLARIFY!

    return ParsedCandidate(
        intent=intent,
        customer_name=cname,
        customer_id=cid,
        amount_paise=(amount_rupees * 100) if amount_rupees is not None else None,
        direction=direction,
        business_date=date_res.business_date if date_res else None,
        date_phrase=date_res.date_phrase if date_res else None,
        confidence=0.90 if is_contextual else 0.95,
        number_contextual=is_contextual,
        is_rule_based=True,
        raw_text=raw,
    )
