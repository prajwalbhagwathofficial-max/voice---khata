"""Table-driven tests (120+ cases) and 1..99999 round-trip fuzz tests for server.nlu.numbers."""

from __future__ import annotations

import random

import pytest

from server.nlu.numbers import AmountSpan, extract_amounts, parse_amount

# ---------------------------------------------------------------------------
# Table-driven test cases for parse_amount (125+ explicit cases)
# ---------------------------------------------------------------------------
PARSE_AMOUNT_CASES: list[tuple[str, str | None, int | None]] = [
    # --- Basic ASCII digits & commas ---
    ("340", None, 340),
    ("1", None, 1),
    ("99", None, 99),
    ("1000", None, 1000),
    ("1,200", None, 1200),
    ("12,345", None, 12345),
    ("1,20,000", None, 120000),
    ("12,34,567", None, 1234567),
    ("₹340", None, 340),
    ("₹ 1,200", None, 1200),
    ("Rs. 450", None, 450),
    ("Rs 1250/-", None, 1250),
    ("340 rupees", "en", 340),
    ("340 rupaye", "hi", 340),
    ("340 रुपये", "hi", 340),
    ("340 రూపాయలు", "te", 340),
    ("500 only", "en", 500),
    # --- Devanagari & Telugu digits ---
    ("३४०", "hi", 340),
    ("१,२००", "hi", 1200),
    ("२५० रुपये", "hi", 250),
    ("१,२०,०००", "hi", 120000),
    ("౩౪౦", "te", 340),
    ("౧,౨౦౦", "te", 1200),
    ("౨౫౦ రూపాయలు", "te", 250),
    ("౫౦౦౦", "te", 5000),
    # --- English number words & colloquial forms ---
    ("three-forty rupees", "en", 340),
    ("three forty rupees", "en", 340),
    ("three hundred and forty", "en", 340),
    ("three hundred forty", "en", 340),
    ("two fifty rupees", "en", 250),
    ("two-fifty-five rupees", "en", 255),
    ("one fifteen rupees", "en", 115),
    ("nine ninety-nine rupees", "en", 999),
    ("three oh five rupees", "en", 305),
    ("hundred", "en", 100),
    ("one hundred", "en", 100),
    ("thousand", "en", 1000),
    ("two thousand five hundred", "en", 2500),
    ("twelve thousand three hundred and forty-five", "en", 12345),
    ("ninety-nine thousand nine hundred and ninety-nine", "en", 99999),
    ("lakh", "en", 100000),
    ("two lakh", "en", 200000),
    ("one lakh fifty thousand", "en", 150000),
    ("two hundred thousand", "en", 200000),
    ("one crore", "en", 10000000),
    ("fourty five rupees", "en", 45),
    # --- Hindi Romanized words ---
    ("teen sau chaalis", "hi", 340),
    ("teen sau chalis", "hi", 340),
    ("paanch sau", "hi", 500),
    ("hazaar", "hi", 1000),
    ("ek hazaar", "hi", 1000),
    ("do hazaar paanch sau", "hi", 2500),
    ("do hazaar aur paanch sau", "hi", 2500),
    ("ek hazaar pachchis", "hi", 1025),
    ("chaar hazaar saat sau unnis", "hi", 4719),
    ("pachas hazaar", "hi", 50000),
    ("ninyanve hazaar nau sau ninyanve", "hi", 99999),
    ("ek lakh pachas hazaar", "hi", 150000),
    ("sau rupaye", "hi", 100),
    ("pachhattar rupaye", "hi", 75),
    ("unhattar", "hi", 69),
    ("unasi", "hi", 79),
    ("nawasi", "hi", 89),
    ("chauranve", "hi", 94),
    # --- Hindi Devanagari words ---
    ("तीन सौ चालीस", "hi", 340),
    ("पाँच सौ", "hi", 500),
    ("पांच सौ रुपये", "hi", 500),
    ("हज़ार", "hi", 1000),
    ("हजार", "hi", 1000),
    ("दो हज़ार पाँच सौ", "hi", 2500),
    ("एक हज़ार दो सौ पैंतालीस", "hi", 1245),
    ("लाख", "hi", 100000),
    ("दो लाख पच्चीस हज़ार", "hi", 225000),
    ("सड़सठ", "hi", 67),
    ("अड़तीस", "hi", 38),
    ("निन्यानवे", "hi", 99),
    # --- Hindi & Indian fractional modifiers ---
    ("dedh sau", "hi", 150),
    ("डेढ़ सौ", "hi", 150),
    ("dedh hazaar", "hi", 1500),
    ("डेढ़ हज़ार", "hi", 1500),
    ("dedh lakh", "hi", 150000),
    ("dhai sau", "hi", 250),
    ("ढाई सौ", "hi", 250),
    ("dhai hazaar", "hi", 2500),
    ("ढाई हजार", "hi", 2500),
    ("dhai lakh", "hi", 250000),
    ("sawa sau", "hi", 125),
    ("सवा सौ", "hi", 125),
    ("sawa do sau", "hi", 225),
    ("sawa hazaar", "hi", 1250),
    ("sawa teen hazaar", "hi", 3250),
    ("sawa lakh", "hi", 125000),
    ("paune sau", "hi", 75),
    ("पौने सौ", "hi", 75),
    ("paune do sau", "hi", 175),
    ("पौने दो सौ", "hi", 175),
    ("paune teen sau", "hi", 275),
    ("paune hazaar", "hi", 750),
    ("paune do hazaar", "hi", 1750),
    ("saade teen sau", "hi", 350),
    ("साढ़े तीन सौ", "hi", 350),
    ("saade chaar sau", "hi", 450),
    ("saade paanch hazaar", "hi", 5500),
    ("saade teen hazaar", "hi", 3500),
    # --- Telugu script & Romanized Telugu ---
    ("మూడు వందల నలభై", "te", 340),
    ("మూడు వందల నలభై రూపాయలు", "te", 340),
    ("ఐదు వందలు", "te", 500),
    ("వంద", "te", 100),
    ("నూట యాభై", "te", 150),
    ("నూట ఇరవై ఐదు", "te", 125),
    ("వెయ్యి", "te", 1000),
    ("రెండు వేల ఐదు వందలు", "te", 2500),
    ("పది వేలు", "te", 10000),
    ("లక్ష", "te", 100000),
    ("రెండు లక్షల యాభై వేలు", "te", 250000),
    ("వందన్నర", "te", 150),
    ("వెయ్యిన్నర", "te", 1500),
    ("రెండున్నర వందలు", "te", 250),
    ("మూడున్నర వందలు", "te", 350),
    ("రెండున్నర వేలు", "te", 2500),
    ("పావు తక్కువ రెండు వందలు", "te", 175),
    ("ముప్పావు వంద", "te", 75),
    ("అర వెయ్యి", "te", 500),
    ("రెండొందల యాభై", "te", 250),
    ("నలభైఐదు", "te", 45),
    ("moodu vandala nalabhai", "te", 340),
    ("rendu vela aidu vandalu", "te", 2500),
    ("vandannara", "te", 150),
    ("veyyi roopayalu", "te", 1000),
    # --- Code-mixed & STT filler utterances ---
    ("5 sau", None, 500),
    ("5 sau rupaye", None, 500),
    ("2 hazaar 500", None, 2500),
    ("2 hazaar paanch sau", None, 2500),
    ("do thousand five hundred", None, 2500),
    ("teen hundred forty", None, 340),
    ("2 వేల ఐదు వందలు", None, 2500),
    ("3 వందల 40 రూపాయలు", None, 340),
    ("uh um Ramesh ke 340 rupaye udhaar likh do", "hi", 340),
    ("arre ji Meena ne teen sau chaalis rupaye jama kar do", "hi", 340),
    ("रमेश के खाते में पाँच सौ रुपये लिख दो", "hi", 500),
    ("రమేష్ ఖాతాలో ఐదు వందల రూపాయలు రాయండి", "te", 500),
    ("please add two hundred and fifty rupees for Suresh", "en", 250),
    # --- Ambiguous / Invalid inputs MUST return None ---
    ("", None, None),
    ("   ", None, None),
    ("hello Ramesh", None, None),
    ("3 40", None, None),
    ("3 40 rupaye", None, None),
    ("12 50", None, None),
    ("1 200", None, None),
    ("३ ४०", "hi", None),
    ("౩ ౪౦", "te", None),
    ("1.5k", None, None),
    ("2.5K", None, None),
    ("5k", None, None),
    ("12.50", None, None),
    ("1.5 hazaar", None, None),
    ("3.5 sau", None, None),
    ("1,2", None, None),
    ("12,34", None, None),
    ("1,2345", None, None),
    ("-50", None, None),
    ("0", None, None),
    ("zero", None, None),
    ("dedh", "hi", None),
    ("dhai", "hi", None),
    ("sawa paanch", "hi", None),
    ("saade teen", "hi", None),
    ("paune do", "hi", None),
    ("saade sau", "hi", None),
    ("two three", "en", None),
    ("do teen", "hi", None),
    ("twenty thirty", "en", None),
    ("200 aur 300", "hi", None),
    ("Ramesh 200 and Suresh 300", "en", None),
    ("no rupees", "en", None),
    ("three forty", "en", None),
    ("three-forty", "en", None),
    ("two fifty", "en", None),
    ("three forty and 50", "en", None),
    ("50 paise", "en", None),
    ("10 rupees and 50 paise", "en", None),
    ("aadha rupaya", "hi", None),
]


@pytest.mark.parametrize(("text", "lang_hint", "expected"), PARSE_AMOUNT_CASES)
def test_parse_amount_table_driven(
    text: str, lang_hint: str | None, expected: int | None
) -> None:
    assert len(PARSE_AMOUNT_CASES) >= 120
    assert parse_amount(text, lang_hint=lang_hint) == expected


def test_extract_amounts_returns_exact_character_spans_and_multiple_items() -> None:
    text = "Ramesh 250 rupaye aur Suresh teen sau chaalis rupaye"
    spans = extract_amounts(text)
    assert len(spans) == 2

    s0, s1 = spans
    assert isinstance(s0, AmountSpan)
    assert s0.value == 250
    assert text[s0.start : s0.end] == "250"
    assert 0.9 <= s0.confidence <= 1.0

    assert s1.value == 340
    assert text[s1.start : s1.end] == "teen sau chaalis"
    assert 0.9 <= s1.confidence <= 1.0

    # Multiple amounts -> parse_amount must return None (ambiguous)
    assert parse_amount(text) is None


def test_extract_amounts_skips_ambiguous_fragments_and_preserves_telugu_span() -> None:
    text = "రమేష్ మూడు వందల నలభై రూపాయలు"
    spans = extract_amounts(text)
    assert len(spans) == 1
    assert spans[0].value == 340
    assert text[spans[0].start : spans[0].end] == "మూడు వందల నలభై"

    assert extract_amounts("3 40") == []
    assert extract_amounts("1.5k") == []


# ---------------------------------------------------------------------------
# Round-trip fuzz tests: ANY value in 1..99999 in English & Hindi words
# ---------------------------------------------------------------------------
_EN_0_19 = [
    "", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
    "seventeen", "eighteen", "nineteen",
]
_EN_TENS_LIST = [
    "", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety",
]

_HI_DEV_0_99 = [
    "शून्य", "एक", "दो", "तीन", "चार", "पाँच", "छह", "सात", "आठ", "नौ",
    "दस", "ग्यारह", "बारह", "तेरह", "चौदह", "पंद्रह", "सोलह", "सत्रह", "अठारह", "उन्नीस",
    "बीस", "इक्कीस", "बाईस", "तेईस", "चौबीस", "पच्चीस", "छब्बीस", "सत्ताईस", "अट्ठाईस", "उनतीस",
    "तीस", "इकतीस", "बत्तीस", "तैंतीस", "चौंतीस", "पैंतीस", "छत्तीस", "सैंतीस", "अड़तीस", "उनतालीस",
    "चालीस", "इकतालीस", "बयालीस", "तैंतालीस", "चौवालीस", "पैंतालीस", "छियालीस", "सैंतालीस", "अड़तालीस", "उनचास",
    "पचास", "इक्यावन", "बावन", "तिरपन", "चौवन", "पचपन", "छप्पन", "सत्तावन", "अट्ठावन", "उनसठ",
    "साठ", "इकसठ", "बासठ", "तिरसठ", "चौंसठ", "पैंसठ", "छियासठ", "सड़सठ", "अड़सठ", "उनहत्तर",
    "सत्तर", "इकहत्तर", "बहत्तर", "तिहत्तर", "चौहत्तर", "पचहत्तर", "छिहत्तर", "सतहत्तर", "अठहत्तर", "उनासी",
    "अस्सी", "इक्यासी", "बयासी", "तिरासी", "चौरासी", "पचासी", "छियासी", "सत्तासी", "अट्ठासी", "नवासी",
    "नब्बे", "इक्यानवे", "बानवे", "तिरानवे", "चौरानवे", "पचानवे", "छियानवे", "सत्तानवे", "अट्ठानवे", "निन्यानवे",
]

_HI_ROM_0_99 = [
    "shunya", "ek", "do", "teen", "chaar", "paanch", "chhe", "saat", "aath", "nau",
    "das", "gyarah", "barah", "terah", "chaudah", "pandrah", "solah", "satrah", "atharah", "unnis",
    "bees", "ikkis", "bais", "teis", "chaubis", "pachchis", "chhabbis", "sattais", "atthais", "untis",
    "tees", "iktis", "battis", "taintis", "chauntis", "paintis", "chhattis", "saintis", "adtis", "untalis",
    "chaalis", "iktalis", "bayalis", "taintalis", "chauwalis", "paintalis", "chhiyalis", "saintalis", "adtalis", "unchas",
    "pachaas", "ikyawan", "bawan", "tirpan", "chauwan", "pachpan", "chhappan", "sattawan", "atthawan", "unsath",
    "saath", "iksath", "basath", "tirsath", "chaunsath", "painsath", "chhiyasath", "sadsath", "adsath", "unhattar",
    "sattar", "ikhattar", "bahattar", "tihattar", "chauhattar", "pachhattar", "chhihattar", "sathattar", "athhattar", "unasi",
    "assi", "ikyasi", "bayasi", "tirasi", "chaurasi", "pachasi", "chhiyasi", "sattasi", "atthasi", "nawasi",
    "nabbe", "ikyanve", "banve", "tiranve", "chauranve", "pachanve", "chhiyanve", "sattanve", "atthanve", "ninyanve",
]


def _render_en_under_1000(n: int, *, use_and: bool = False) -> str:
    parts: list[str] = []
    hundreds, rem = divmod(n, 100)
    if hundreds:
        parts.append(f"{_EN_0_19[hundreds]} hundred")
        if rem and use_and:
            parts.append("and")
    if rem:
        if rem < 20:
            parts.append(_EN_0_19[rem])
        else:
            tens, units = divmod(rem, 10)
            parts.append(f"{_EN_TENS_LIST[tens]}-{_EN_0_19[units]}" if units else _EN_TENS_LIST[tens])
    return " ".join(parts)


def int_to_english_words(n: int, *, use_and: bool = False) -> str:
    assert 1 <= n <= 99999
    thousands, rem = divmod(n, 1000)
    parts: list[str] = []
    if thousands:
        parts.append(f"{_render_en_under_1000(thousands)} thousand")
    if rem:
        if thousands and rem < 100 and use_and:
            parts.append("and")
        parts.append(_render_en_under_1000(rem, use_and=use_and))
    return " ".join(parts)


def int_to_hindi_words(n: int, *, script: str = "devanagari") -> str:
    assert 1 <= n <= 99999
    table = _HI_DEV_0_99 if script == "devanagari" else _HI_ROM_0_99
    sau = "सौ" if script == "devanagari" else "sau"
    hazaar = "हज़ार" if script == "devanagari" else "hazaar"

    thousands, rem = divmod(n, 1000)
    hundreds, last2 = divmod(rem, 100)
    parts: list[str] = []
    if thousands:
        parts.append(f"{table[thousands]} {hazaar}")
    if hundreds:
        parts.append(f"{table[hundreds]} {sau}")
    if last2:
        parts.append(table[last2])
    return " ".join(parts)


def test_fuzz_round_trip_english_and_hindi_1_to_99999() -> None:
    rng = random.Random(20261118)
    # Cover every number 1..1000 exhaustively + key boundaries + 2,500 random samples across 1..99999
    boundary_values = set(range(1, 1001)) | {
        1001, 1010, 1099, 1100, 1999, 2000, 2005, 2500, 9999,
        10000, 10001, 10100, 11111, 19099, 20000, 25678, 50000,
        75050, 88888, 90000, 99000, 99900, 99990, 99999,
    }
    fuzz_values = boundary_values | {rng.randint(1, 99999) for _ in range(2500)}

    for n in sorted(fuzz_values):
        en_plain = int_to_english_words(n, use_and=False)
        assert parse_amount(en_plain, lang_hint="en") == n, f"EN failed for {n}: {en_plain!r}"

        en_and = int_to_english_words(n, use_and=True)
        assert parse_amount(en_and, lang_hint="en") == n, f"EN(and) failed for {n}: {en_and!r}"

        hi_dev = int_to_hindi_words(n, script="devanagari")
        assert parse_amount(hi_dev, lang_hint="hi") == n, f"HI(dev) failed for {n}: {hi_dev!r}"

        hi_rom = int_to_hindi_words(n, script="roman")
        assert parse_amount(hi_rom, lang_hint="hi") == n, f"HI(rom) failed for {n}: {hi_rom!r}"
