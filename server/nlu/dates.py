"""Deterministic relative date resolver for Hindi, Telugu, and English utterances.

Supports Latin, Devanagari, and Telugu scripts, including verb-context
disambiguation for Hindi "kal" and "parso". Returns None when no date is
present or when the reference is ambiguous. Never guesses.
"""

from __future__ import annotations

import re
import unicodedata
import zoneinfo
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from server.nlu.numbers import _JOINERS, _NUMERIC_STARTERS, _tokenize, parse_amount

KOLKATA_TZ = zoneinfo.ZoneInfo("Asia/Kolkata")

# Unicode-aware word boundary helpers (Python \b treats Devanagari/Telugu/Kannada combining marks specially).
_LEFT_BOUND = r"(?<![0-9A-Za-z\u0900-\u097F\u0C00-\u0C7F\u0C80-\u0CFF])"
_RIGHT_BOUND = r"(?![0-9A-Za-z\u0900-\u097F\u0C00-\u0C7F\u0C80-\u0CFF])"


def _wb(pattern: str) -> re.Pattern[str]:
    return re.compile(f"{_LEFT_BOUND}(?:{pattern}){_RIGHT_BOUND}", re.IGNORECASE)


# --- Direct unambiguous day expressions ---
_TODAY_RE = _wb(r"today|aaj|आज|ఈ\s*రోజు|నేడు|eeroju|eroju|iroju|nedu|ಇಂದು|ಇವತ್ತು|ivattu|indu")
_YESTERDAY_RE = _wb(r"yesterday|నిన్న|ninna|ನಿನ್ನೆ|ninne")
_TOMORROW_RE = _wb(r"tomorrow|రేపు|repu|ನಾಳೆ|naale|nale")
_DAY_BEFORE_YESTERDAY_RE = _wb(r"day\s+before\s+yesterday|మొన్న|monna|ಮೊನ್ನೆ|monne")
_DAY_AFTER_TOMORROW_RE = _wb(r"day\s+after\s+tomorrow|ఎల్లుండి|ellundi|yellundi|ನಾಡಿದ್ದು|naadiddu")

_LAST_WEEK_RE = _wb(
    r"last\s+week|pichhle\s+hafte|pichle\s+hafte|पिछले\s+हफ्ते|पिछले\s+सप्ताह|గత\s+వారం|gata\s+varam|gatha\s+vaaram|ಕಳೆದ\s+ವಾರ|kaleda\s+vaara"
)
_NEXT_WEEK_RE = _wb(
    r"next\s+week|agle\s+hafte|agale\s+hafte|अगले\s+हफ्ते|अगले\s+सप्ताह|వచ్చే\s+వారం|vachche\s+varam|vacche\s+vaaram|vache\s+varam|ಮುಂದಿನ\s+ವಾರ|mundina\s+vaara"
)

# --- Context-dependent Hindi words: kal (±1), parso (±2) ---
_KAL_RE = _wb(r"kal|कल")
_PARSO_RE = _wb(r"parso|parson|परसों|परसो")

# Past-tense verbs & explicit past markers (Latin + Devanagari)
_PAST_CONTEXT_RE = _wb(
    r"tha|thi|the|thaa|thee|"
    r"liya|liye|lia|li\s+thi|li\s+hai|le\s+gaya|le\s+gayi|le\s+gaye|le\s+liya|"
    r"diya|diye|dia|di\s+thi|de\s+diya|de\s+gaya|de\s+gayi|de\s+gaye|"
    r"gaya|gayi|gaye|aaya|aayi|aaye|aaya\s+tha|aayi\s+thi|"
    r"likha|likhi|likhe|rakha|kharida|khareeda|becha|hua|hui|huye|kiya|kiye|bheja|"
    r"chuka\s+diya|chukaya|jama\s+kiya|udhaar\s+liya|udhar\s+liya|"
    r"pichhla|pichhle|pichla|pichle|beeta|beete|pehle|pahle|"
    r"paid|gave|took|bought|purchased|borrowed|settled|recorded|noted|was|were|did|came|had|"
    r"था|थी|थे|लिया|लिये|लिए|दिया|दिए|दी\s+थी|गया|गई|गए|आया|आई|आए|"
    r"लिखा|लिखी|रखा|खरीदा|बेचा|हुआ|हुई|किया|किये|भेजा|चुकाया|ले\s+गया|दे\s+गया|दे\s+दिया|"
    r"पिछला|पिछले|बीता|बीते|पहले"
)

# Future-tense verbs & explicit future markers (Latin + Devanagari)
# Note: deliberately excludes standalone "mein" ("khate mein") so locative phrases don't misfire.
_FUTURE_CONTEXT_RE = _wb(
    r"dega|degi|denge|dunga|doonga|dungi|doongi|de\s+dega|de\s+dunga|de\s+denge|"
    r"lega|legi|lenge|lunga|loonga|le\s+lega|le\s+lunga|"
    r"aayega|aayegi|aayenge|ayega|ayegi|aaunga|aunga|"
    r"karega|karegi|karenge|karunga|karoonga|kar\s+dega|kar\s+dunga|"
    r"hoga|hogi|honge|jayega|jayegi|jayenge|jaunga|"
    r"likhega|likhunga|bhejega|bhejunga|chukayega|chukauga|chuka\s+dega|chuka\s+dunga|"
    r"jama\s+karega|jama\s+karunga|wapas\s+dega|wapas\s+karega|waapas\s+dega|"
    r"agla|agle|agale|aane\s+wala|aane\s+wale|baad|tak|"
    r"will|shall|going\s+to|gonna|promise|promises|coming|next|"
    r"देगा|देगी|देंगे|दूंगा|दूँगा|दूंगी|दूँगी|लेगा|लेगी|लेंगे|लूंगा|लूँगा|"
    r"आएगा|आएगी|आएंगे|आऊंगा|करेगा|करेगी|करेंगे|करूंगा|करूँगा|"
    r"होगा|होगी|होंगे|जाएगा|जाएगी|लिखेगा|भेजेगा|चुकाएगा|वापस\s+देगा|"
    r"अगला|अगले|आने\s+वाला|आने\s+वाले|बाद|तक"
)

# --- Relative offset patterns (capture a short numeric phrase up to 4 words before/after unit) ---
_NUM_PHRASE = r"([0-9०-९౦-౯A-Za-z\u0900-\u097F\u0C00-\u0C7F.,-]+(?:\s+[0-9०-९౦-౯A-Za-z\u0900-\u097F\u0C00-\u0C7F.,-]+){0,3})"

_FUTURE_DAYS_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Hindi: "5 din mein", "do din baad", "पाँच दिन में", "पाँच दिनों बाद"
    _wb(rf"{_NUM_PHRASE}\s+(?:din|dino|dinon|दिन|दिनों)\s+(?:mein|me|mai|baad|bad|में|बाद)"),
    # English: "in 5 days", "after 3 days"
    _wb(rf"(?:in|after)\s+{_NUM_PHRASE}\s+days?"),
    # English: "5 days later", "5 days from now"
    _wb(rf"{_NUM_PHRASE}\s+days?\s+(?:later|from\s+now)"),
    # Telugu: "5 రోజుల్లో", "ఐదు రోజుల తర్వాత", "5 rojullo", "aidu rojula tarvata"
    _wb(rf"{_NUM_PHRASE}\s+(?:రోజుల్లో|రోజులలో|rojullo|rojulalo)"),
    _wb(rf"{_NUM_PHRASE}\s+(?:రోజుల|rojula)\s+(?:తర్వాత|తరువాత|tarvata|taruvata|tarvatha|tharvata)"),
    # Kannada: "5 ದಿನಗಳಲ್ಲಿ", "ಐದು ದಿನಗಳ ನಂತರ"
    _wb(rf"{_NUM_PHRASE}\s+(?:ದಿನಗಳಲ್ಲಿ|ದಿನಗಳ\s+ನಂತರ|dinagalalli|dinagala\s+nantara)"),
)

_PAST_DAYS_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Hindi: "do din pehle", "5 din pahle", "दो दिन पहले", "पाँच दिनों पहले"
    _wb(rf"{_NUM_PHRASE}\s+(?:din|dino|dinon|दिन|दिनों)\s+(?:pehle|pahle|pahale|purana|पहले)"),
    # English: "2 days ago", "5 days before", "3 days back"
    _wb(rf"{_NUM_PHRASE}\s+days?\s+(?:ago|before|back)"),
    # Telugu: "రెండు రోజుల క్రితం", "2 రోజుల ముందు", "rendu rojula kritam", "2 rojula mundu"
    _wb(rf"{_NUM_PHRASE}\s+(?:రోజుల|rojula)\s+(?:క్రితం|ముందు|kritam|kritham|mundu)"),
    # Kannada: "೨ ದಿನಗಳ ಹಿಂದೆ", "5 ದಿನಗಳ ಮುಂಚೆ"
    _wb(rf"{_NUM_PHRASE}\s+(?:ದಿನಗಳ\s+ಹಿಂದೆ|ದಿನಗಳ\s+ಮುಂಚೆ|dinagala\s+hinde|dinagala\s+munche)"),
)

_FUTURE_WEEKS_PATTERNS: tuple[re.Pattern[str], ...] = (
    _wb(rf"{_NUM_PHRASE}\s+(?:hafte|hafton|hafta|सप्ताह|हफ्ते|हफ्तों)\s+(?:mein|me|baad|में|बाद)"),
    _wb(rf"(?:in|after)\s+{_NUM_PHRASE}\s+weeks?"),
    _wb(rf"{_NUM_PHRASE}\s+(?:వారాల్లో|vaarallo|varallo)"),
    _wb(rf"{_NUM_PHRASE}\s+(?:వారాల|vaarala|varala)\s+(?:తర్వాత|తరువాత|tarvata|taruvata)"),
)

_PAST_WEEKS_PATTERNS: tuple[re.Pattern[str], ...] = (
    _wb(rf"{_NUM_PHRASE}\s+(?:hafte|hafton|hafta|सप्ताह|हफ्ते|हफ्तों)\s+(?:pehle|pahle|पहले)"),
    _wb(rf"{_NUM_PHRASE}\s+weeks?\s+(?:ago|before|back)"),
    _wb(rf"{_NUM_PHRASE}\s+(?:వారాల|vaarala|varala)\s+(?:క్రితం|ముందు|kritam|kritham|mundu)"),
)

MAX_RELATIVE_DAYS = 3650


@dataclass(frozen=True, slots=True)
class DateResolution:
    business_date: str  # YYYY-MM-DD
    date_phrase: str | None
    target_date: date


def _extract_offset_count(raw_phrase: str) -> int | None:
    """Extract the maximal contiguous numeric phrase immediately preceding the time unit."""
    tokens = _tokenize(raw_phrase)
    if not tokens:
        return None

    end = len(tokens)
    start = end
    while start > 0:
        tok = tokens[start - 1]
        if tok.kind in _NUMERIC_STARTERS:
            start -= 1
        elif tok.norm in _JOINERS and start < end:
            start -= 1
        else:
            break

    while start < end and tokens[start].norm in _JOINERS:
        start += 1

    if start >= end:
        return None

    slice_text = raw_phrase[tokens[start].start : tokens[end - 1].end]
    return parse_amount(slice_text)


def _match_relative_offsets_with_phrases(
    text: str, patterns: tuple[re.Pattern[str], ...], *, multiplier: int
) -> list[tuple[int, str]]:
    results: list[tuple[int, str]] = []
    for pat in patterns:
        for m in pat.finditer(text):
            count = _extract_offset_count(m.group(1))
            if count is None or count <= 0:
                continue
            days = count * abs(multiplier)
            if days > MAX_RELATIVE_DAYS:
                continue
            delta = days if multiplier > 0 else -days
            results.append((delta, m.group(0).strip()))
    return results


def _detect_tense(text: str) -> int | None:
    """Return -1 for past context, +1 for future context, None if absent or conflicting."""
    has_past = bool(_PAST_CONTEXT_RE.search(text))
    has_future = bool(_FUTURE_CONTEXT_RE.search(text))
    if has_past and has_future:
        return None
    if has_past:
        return -1
    if has_future:
        return 1
    return None


def resolve_business_date(
    text: str,
    now: date | datetime | None = None,
) -> DateResolution | None:
    """Resolve a relative date mention in shop-local time (Asia/Kolkata).

    Returns DateResolution(business_date, date_phrase, target_date),
    or None if no date mention is found or if tense/date is ambiguous.
    """
    if not text or not text.strip():
        return None

    if now is None:
        base_date = datetime.now(KOLKATA_TZ).date()
    elif isinstance(now, datetime):
        base_date = now.date()
    else:
        base_date = now

    normalized = unicodedata.normalize("NFC", text).strip()

    candidate_map: dict[date, str] = {}
    ambiguous = False

    # 1. Relative day/week offsets first
    for delta, phrase in _match_relative_offsets_with_phrases(normalized, _FUTURE_DAYS_PATTERNS, multiplier=1):
        candidate_map[base_date + timedelta(days=delta)] = phrase
    for delta, phrase in _match_relative_offsets_with_phrases(normalized, _PAST_DAYS_PATTERNS, multiplier=-1):
        candidate_map[base_date + timedelta(days=delta)] = phrase
    for delta, phrase in _match_relative_offsets_with_phrases(normalized, _FUTURE_WEEKS_PATTERNS, multiplier=7):
        candidate_map[base_date + timedelta(days=delta)] = phrase
    for delta, phrase in _match_relative_offsets_with_phrases(normalized, _PAST_WEEKS_PATTERNS, multiplier=-7):
        candidate_map[base_date + timedelta(days=delta)] = phrase

    # 2. Multi-word day-before / day-after expressions
    scrubbed = normalized
    m_bf = _DAY_BEFORE_YESTERDAY_RE.search(scrubbed)
    if m_bf:
        candidate_map[base_date - timedelta(days=2)] = m_bf.group(0).strip()
        scrubbed = _DAY_BEFORE_YESTERDAY_RE.sub(" ", scrubbed)

    m_af = _DAY_AFTER_TOMORROW_RE.search(scrubbed)
    if m_af:
        candidate_map[base_date + timedelta(days=2)] = m_af.group(0).strip()
        scrubbed = _DAY_AFTER_TOMORROW_RE.sub(" ", scrubbed)

    # 3. Direct unambiguous day/week expressions
    m_td = _TODAY_RE.search(scrubbed)
    if m_td:
        candidate_map[base_date] = m_td.group(0).strip()
    m_yd = _YESTERDAY_RE.search(scrubbed)
    if m_yd:
        candidate_map[base_date - timedelta(days=1)] = m_yd.group(0).strip()
    m_tm = _TOMORROW_RE.search(scrubbed)
    if m_tm:
        candidate_map[base_date + timedelta(days=1)] = m_tm.group(0).strip()
    m_lw = _LAST_WEEK_RE.search(scrubbed)
    if m_lw:
        candidate_map[base_date - timedelta(days=7)] = m_lw.group(0).strip()
    m_nw = _NEXT_WEEK_RE.search(scrubbed)
    if m_nw:
        candidate_map[base_date + timedelta(days=7)] = m_nw.group(0).strip()

    # 4. Context-dependent Hindi "kal" (±1) and "parso" (±2)
    m_kal = _KAL_RE.search(scrubbed)
    if m_kal:
        tense = _detect_tense(normalized)
        if tense is None:
            ambiguous = True
        else:
            candidate_map[base_date + timedelta(days=tense)] = m_kal.group(0).strip()

    m_parso = _PARSO_RE.search(scrubbed)
    if m_parso:
        tense = _detect_tense(normalized)
        if tense is None:
            ambiguous = True
        else:
            candidate_map[base_date + timedelta(days=2 * tense)] = m_parso.group(0).strip()

    if ambiguous or len(candidate_map) != 1:
        return None

    target, phrase = next(iter(candidate_map.items()))
    return DateResolution(
        business_date=target.isoformat(),
        date_phrase=phrase,
        target_date=target,
    )


def resolve_date(
    text: str,
    now: date | datetime | None = None,
) -> date | None:
    """Resolve a relative date mention in Hindi, Telugu, Kannada, or English relative to `now`.

    Returns None if no date mention is found or if the mention is ambiguous
    (e.g., bare "kal" without a past/future verb, or conflicting dates).
    """
    res = resolve_business_date(text, now)
    return res.target_date if res is not None else None
