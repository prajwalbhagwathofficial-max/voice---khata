"""Multilingual Indian integer-rupee amount parser (English, Hindi, Telugu).

Supports Latin, Devanagari, and Telugu scripts (digits and words, including
code-mixed utterances, Indian fractional modifiers, and STT quirks).
Returns None whenever an expression is non-integer or ambiguous. Never guesses.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from fractions import Fraction
from typing import Literal

# 1-to-1 digit translation preserves exact string length and character offsets.
# Devanagari (0-9), Telugu (0-9), Kannada (0-9).
_DIGIT_TRANSLATION = str.maketrans(
    "०१२३४५६७८९౦౧౨౩౪౫౬౭౮౯೦೧೨೩೪೫೬೭೮೯",
    "012345678901234567890123456789",
)

_WESTERN_COMMA_RE = re.compile(r"^\d{1,3}(?:,\d{3})+$")
_INDIAN_COMMA_RE = re.compile(r"^\d{1,2}(?:,\d{2})*,\d{3}$")
_PLAIN_INT_RE = re.compile(r"^\d+$")

# Tokenizes digit-bearing tokens, currency symbols, and Latin/Devanagari/Telugu/Kannada words
# while keeping exact 0-based character offsets in the original text.
_TOKEN_RE = re.compile(
    r"""
    (?P<digit>[-+]?\d+(?:[.,]\d+)*(?:[A-Za-z]+)?)
    | (?P<word>[A-Za-z]+(?:-[A-Za-z]+)*|[\u0900-\u097F]+(?:-[\u0900-\u097F]+)*|[\u0C00-\u0C7F]+(?:-[\u0C00-\u0C7F]+)*|[\u0C80-\u0CFF]+(?:-[\u0C80-\u0CFF]+)*)
    | (?P<currency>₹|/-)
    """,
    re.VERBOSE,
)

# --- English Lexicon ---
_EN_UNITS: dict[str, int] = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
}

_EN_TEENS: dict[str, int] = {
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}

_EN_TENS: dict[str, int] = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fourty": 40,  # common STT / spelling quirk
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}

_EN_SCALES: dict[str, int] = {
    "hundred": 100,
    "hundreds": 100,
    "thousand": 1000,
    "thousands": 1000,
    "lakh": 100_000,
    "lakhs": 100_000,
    "lac": 100_000,
    "lacs": 100_000,
    "crore": 10_000_000,
    "crores": 10_000_000,
}

# --- Hindi Lexicon (0..99 complete in Romanized + Devanagari) ---
_HI_UNITS: dict[str, int] = {
    "shunya": 0,
    "sunya": 0,
    "शून्य": 0,
    "ek": 1,
    "aik": 1,
    "एक": 1,
    "do": 2,
    "दो": 2,
    "teen": 3,
    "tin": 3,
    "तीन": 3,
    "chaar": 4,
    "char": 4,
    "चार": 4,
    "paanch": 5,
    "panch": 5,
    "पाँच": 5,
    "पांच": 5,
    "chhe": 6,
    "che": 6,
    "chhah": 6,
    "chah": 6,
    "छह": 6,
    "छै": 6,
    "छे": 6,
    "saat": 7,
    "sat": 7,
    "सात": 7,
    "aath": 8,
    "ath": 8,
    "आठ": 8,
    "nau": 9,
    "नौ": 9,
}

_HI_TEENS: dict[str, int] = {
    "das": 10,
    "दस": 10,
    "gyarah": 11,
    "gyara": 11,
    "ग्यारह": 11,
    "barah": 12,
    "bara": 12,
    "baarah": 12,
    "बारह": 12,
    "terah": 13,
    "tera": 13,
    "तेरह": 13,
    "chaudah": 14,
    "chauda": 14,
    "chodah": 14,
    "चौदह": 14,
    "pandrah": 15,
    "pandra": 15,
    "पंद्रह": 15,
    "पन्द्रह": 15,
    "solah": 16,
    "sola": 16,
    "सोलह": 16,
    "satrah": 17,
    "satra": 17,
    "सत्रह": 17,
    "atharah": 18,
    "athara": 18,
    "attharah": 18,
    "अठारह": 18,
    "अट्ठारह": 18,
    "unnis": 19,
    "unees": 19,
    "उन्नीस": 19,
}

_HI_COMPOUNDS: dict[str, int] = {
    # 20..29
    "bees": 20, "bis": 20, "बीस": 20,
    "ikkis": 21, "ikees": 21, "ekis": 21, "इक्कीस": 21,
    "bais": 22, "baees": 22, "baais": 22, "बाईस": 22, "बाइस": 22,
    "teis": 23, "teees": 23, "तेईस": 23, "तेइस": 23,
    "chaubis": 24, "chobis": 24, "चौबीस": 24,
    "pachchis": 25, "pachis": 25, "pachees": 25, "पच्चीस": 25,
    "chhabbis": 26, "chabbis": 26, "chhabees": 26, "छब्बीस": 26,
    "sattais": 27, "satais": 27, "sattaees": 27, "सत्ताईस": 27, "सत्ताइस": 27,
    "atthais": 28, "athais": 28, "atthaees": 28, "अट्ठाईस": 28, "अट्ठाइस": 28, "अठाइस": 28,
    "untis": 29, "untees": 29, "उनतीस": 29,
    # 30..39
    "tees": 30, "tis": 30, "तीस": 30,
    "iktis": 31, "iktees": 31, "ektis": 31, "इकतीस": 31,
    "battis": 32, "batees": 32, "बत्तीस": 32,
    "taintis": 33, "taintees": 33, "tentis": 33, "तैंतीस": 33, "तेतीस": 33,
    "chauntis": 34, "chauntees": 34, "chautis": 34, "चौंतीस": 34,
    "paintis": 35, "paintees": 35, "pentis": 35, "पैंतीस": 35,
    "chhattis": 36, "chatis": 36, "chhattees": 36, "छत्तीस": 36,
    "saintis": 37, "saintees": 37, "सैंतीस": 37,
    "adtis": 38, "adtees": 38, "artis": 38, "अड़तीस": 38, "अरतीस": 38,
    "untalis": 39, "untaalis": 39, "उनतालीस": 39,
    # 40..49
    "chaalis": 40, "chalis": 40, "chalees": 40, "चालीस": 40,
    "iktalis": 41, "iktaalis": 41, "ektalis": 41, "इकतालीस": 41,
    "bayalis": 42, "bayaalis": 42, "biyalis": 42, "बयालीस": 42, "बियालीस": 42,
    "taintalis": 43, "taintaalis": 43, "tiralis": 43, "तैंतालीस": 43, "तिरालीस": 43,
    "chauwalis": 44, "chawalis": 44, "chavalis": 44, "चौवालीस": 44, "चवालीस": 44,
    "paintalis": 45, "paintaalis": 45, "पैंतालीस": 45,
    "chhiyalis": 46, "chiyalis": 46, "छियालीस": 46,
    "saintalis": 47, "saintaalis": 47, "सैंतालीस": 47,
    "adtalis": 48, "adtaalis": 48, "artalis": 48, "अड़तालीस": 48, "अरतालीस": 48,
    "unchas": 49, "unchaas": 49, "उनचास": 49,
    # 50..59
    "pachaas": 50, "pachas": 50, "पचास": 50,
    "ikyawan": 51, "ikyavan": 51, "ekyavan": 51, "इक्यावन": 51,
    "bawan": 52, "bavan": 52, "baawan": 52, "बावन": 52,
    "tirpan": 53, "तिरपन": 53,
    "chauwan": 54, "chauvan": 54, "chawan": 54, "चौवन": 54,
    "pachpan": 55, "पचपन": 55,
    "chhappan": 56, "chappan": 56, "छप्पन": 56,
    "sattawan": 57, "sattavan": 57, "satawan": 57, "सत्तावन": 57,
    "atthawan": 58, "atthavan": 58, "athawan": 58, "अट्ठावन": 58,
    "unsath": 59, "उनसठ": 59,
    # 60..69
    "saath": 60, "साठ": 60,
    "iksath": 61, "eksath": 61, "इकसठ": 61,
    "basath": 62, "baasath": 62, "बासठ": 62,
    "tirsath": 63, "तिरसठ": 63,
    "chaunsath": 64, "chausath": 64, "चौंसठ": 64,
    "painsath": 65, "pensath": 65, "पैंसठ": 65,
    "chhiyasath": 66, "chiyasath": 66, "छियासठ": 66,
    "sadsath": 67, "sarsath": 67, "सड़सठ": 67,
    "adsath": 68, "arsath": 68, "अड़सठ": 68,
    "unhattar": 69, "unhatar": 69, "उनहत्तर": 69,
    # 70..79
    "sattar": 70, "satar": 70, "सत्तर": 70,
    "ikhattar": 71, "ekhattar": 71, "इकहत्तर": 71,
    "bahattar": 72, "bahatar": 72, "बहत्तर": 72,
    "tihattar": 73, "tihatar": 73, "तिहत्तर": 73,
    "chauhattar": 74, "chauhatar": 74, "चौहत्तर": 74,
    "pachhattar": 75, "pachhatar": 75, "pichhattar": 75, "पचहत्तर": 75,
    "chhihattar": 76, "chihattar": 76, "छिहत्तर": 76,
    "sathattar": 77, "satahattar": 77, "सतहत्तर": 77,
    "athhattar": 78, "athahattar": 78, "अठहत्तर": 78,
    "unasi": 79, "unyaasi": 79, "unnaasi": 79, "उनासी": 79, "उन्यासी": 79,
    # 80..89
    "assi": 80, "asee": 80, "अस्सी": 80,
    "ikyasi": 81, "ikyaasi": 81, "ekyasi": 81, "इक्यासी": 81,
    "bayasi": 82, "bayaasi": 82, "biyasi": 82, "बयासी": 82, "बियासी": 82,
    "tirasi": 83, "tiraasi": 83, "तिरासी": 83,
    "chaurasi": 84, "chauraasi": 84, "चौरासी": 84,
    "pachasi": 85, "pachaasi": 85, "पचासी": 85,
    "chhiyasi": 86, "chiyasi": 86, "छियासी": 86,
    "sattasi": 87, "sattaasi": 87, "satasi": 87, "सत्तासी": 87,
    "atthasi": 88, "atthaasi": 88, "athasi": 88, "अट्ठासी": 88,
    "nawasi": 89, "nawaasi": 89, "navasi": 89, "नवासी": 89,
    # 90..99
    "nabbe": 90, "nabbay": 90, "navve": 90, "नब्बे": 90, "नव्वे": 90,
    "ikyanve": 91, "ikyanbe": 91, "ekyanve": 91, "इक्यानवे": 91, "इक्यानबे": 91,
    "banve": 92, "baanve": 92, "banbe": 92, "बानवे": 92, "बानबे": 92,
    "tiranve": 93, "tiraanve": 93, "tiranbe": 93, "तिरानवे": 93, "तिरानबे": 93,
    "chauranve": 94, "chauraanve": 94, "chauranbe": 94, "चौरानवे": 94, "चौरानबे": 94,
    "pachanve": 95, "pachaanve": 95, "pachanbe": 95, "पचानवे": 95, "पचानबे": 95, "पचियानवे": 95,
    "chhiyanve": 96, "chiyanve": 96, "chhiyanbe": 96, "छियानवे": 96, "छियानबे": 96,
    "sattanve": 97, "sattaanve": 97, "sattanbe": 97, "सत्तानवे": 97, "सत्तानबे": 97,
    "atthanve": 98, "atthaanve": 98, "atthanbe": 98, "अट्ठानवे": 98, "अट्ठानबे": 98,
    "ninyanve": 99, "ninyanbe": 99, "ninanve": 99, "निन्यानवे": 99, "निन्यानबे": 99,
}

_HI_SCALES: dict[str, int] = {
    "sau": 100,
    "sow": 100,
    "सौ": 100,
    "hazaar": 1000,
    "hazar": 1000,
    "hajaar": 1000,
    "hajar": 1000,
    "हज़ार": 1000,
    "हजार": 1000,
    "laakh": 100_000,
    "लाख": 100_000,
    "karod": 10_000_000,
    "karor": 10_000_000,
    "करोड़": 10_000_000,
    "करोड": 10_000_000,
}

# --- Telugu Lexicon (Telugu script + Romanized) ---
_TE_UNITS: dict[str, int] = {
    "సున్నా": 0, "sunna": 0,
    "ఒకటి": 1, "ఒక": 1, "okati": 1, "oka": 1,
    "రెండు": 2, "rendu": 2,
    "మూడు": 3, "moodu": 3, "mudu": 3,
    "నాలుగు": 4, "nalugu": 4, "naalugu": 4,
    "ఐదు": 5, "అయిదు": 5, "aidu": 5, "ayidu": 5,
    "ఆరు": 6, "aaru": 6, "aru": 6,
    "ఏడు": 7, "edu": 7, "yedu": 7,
    "ఎనిమిది": 8, "enimidi": 8, "yenimidi": 8,
    "తొమ్మిది": 9, "tommidi": 9, "thommidi": 9,
}

_TE_TEENS: dict[str, int] = {
    "పది": 10, "padi": 10,
    "పదకొండు": 11, "padakondu": 11,
    "పన్నెండు": 12, "pannendu": 12,
    "పదమూడు": 13, "padamoodu": 13, "padamudu": 13,
    "పద్నాలుగు": 14, "పదునాలుగు": 14, "padnalugu": 14, "padunalugu": 14,
    "పదిహేను": 15, "padihenu": 15,
    "పదహారు": 16, "padaharu": 16,
    "పదిహేడు": 17, "padihedu": 17,
    "పద్దెనిమిది": 18, "పదునెనిమిది": 18, "paddenimidi": 18,
    "పంతొమ్మిది": 19, "pantommidi": 19, "panthommidi": 19,
}

_TE_TENS: dict[str, int] = {
    "ఇరవై": 20, "iravai": 20, "iruvai": 20,
    "ముప్పై": 30, "muppai": 30,
    "నలభై": 40, "nalabhai": 40, "nalabai": 40,
    "యాభై": 50, "ఏభై": 50, "yabhai": 50, "yaabhai": 50, "yabai": 50,
    "అరవై": 60, "aravai": 60,
    "డెబ్బై": 70, "debbai": 70, "debbhai": 70,
    "ఎనభై": 80, "enabhai": 80, "yenabhai": 80, "enabai": 80,
    "తొంభై": 90, "tombhai": 90, "thombhai": 90, "tombai": 90,
}

_TE_SCALES: dict[str, int] = {
    "వంద": 100, "వందల": 100, "వందలు": 100, "నూరు": 100,
    "vanda": 100, "vandala": 100, "vandalu": 100, "nooru": 100, "nuru": 100,
    "వెయ్యి": 1000, "వేయి": 1000, "వేల": 1000, "వేలు": 1000,
    "veyyi": 1000, "veyi": 1000, "vela": 1000, "velu": 1000,
    "లక్ష": 100_000, "లక్షల": 100_000, "లక్షలు": 100_000,
    "laksha": 100_000, "lakshala": 100_000, "lakshalu": 100_000,
    "కోటి": 10_000_000, "కోట్ల": 10_000_000, "కోట్లు": 10_000_000,
    "koti": 10_000_000, "kotla": 10_000_000, "kotlu": 10_000_000,
}

# Pre-multiplied Telugu hundred words (e.g., నూట యాభై = 150, రెండొందలు = 200)
_TE_HUNDRED_VALUES: dict[str, int] = {
    "నూట": 100, "noota": 100, "nuta": 100,
    "రెండొందలు": 200, "రెండొందల": 200, "rendondalu": 200, "rendondala": 200,
    "మూడొందలు": 300, "మూడొందల": 300, "moodondalu": 300, "moodondala": 300,
    "నాలుగొందలు": 400, "నాలుగొందల": 400, "nalugondalu": 400,
    "ఐదొందలు": 500, "ఐదొందల": 500, "aidondalu": 500,
    "ఆరొందలు": 600, "ఆరొందల": 600, "aarondalu": 600,
    "ఏడొందలు": 700, "ఏడొందల": 700, "edondalu": 700,
    "ఎనిమిదొందలు": 800, "ఎనిమిదొందల": 800, "enimidondalu": 800,
    "తొమ్మిదొందలు": 900, "తొమ్మిదొందల": 900, "tommidondalu": 900,
}

# --- Kannada Lexicon (Kannada script + Romanized) ---
_KN_UNITS: dict[str, int] = {
    "ಸೊನ್ನೆ": 0, "sonne": 0,
    "ಒಂದು": 1, "ondu": 1, "ಒನ್": 1,
    "ಎರಡು": 2, "eradu": 2,
    "ಮೂರು": 3, "mooru": 3, "muru": 3,
    "ನಾಲ್ಕು": 4, "naalku": 4, "nalku": 4,
    "ಐದು": 5, "aidu": 5, "ayidu": 5,
    "ಆರು": 6, "aaru": 6, "aru": 6,
    "ಏಳು": 7, "elu": 7, "yelu": 7, "yedhu": 7,
    "ಎಂಟು": 8, "entu": 8, "yentu": 8,
    "ಒಂಬತ್ತು": 9, "ombattu": 9, "ombhattu": 9,
}

_KN_TEENS: dict[str, int] = {
    "ಹತ್ತು": 10, "hattu": 10,
    "ಹನ್ನೊಂದು": 11, "hannondu": 11,
    "ಹನ್ನೆರಡು": 12, "hanneradu": 12,
    "ಹದಿಮೂರು": 13, "hadimooru": 13, "hadimuru": 13,
    "ಹದಿನಾಲ್ಕು": 14, "hadinaalku": 14, "hadinalku": 14,
    "ಹದಿನೈದು": 15, "hadinaidu": 15,
    "ಹದಿನಾರು": 16, "hadinaaru": 16, "hadinaru": 16,
    "ಹದಿನೇಳು": 17, "hadinelu": 17,
    "ಹದಿನೆಂಟು": 18, "hadinentu": 18,
    "ಹತ್ತೊಂಬತ್ತು": 19, "hattonbattu": 19, "hattonbhattu": 19, "hathombathu": 19,
}

_KN_TENS: dict[str, int] = {
    "ಇಪ್ಪತ್ತು": 20, "ippattu": 20, "ippathu": 20,
    "ಮೂವತ್ತು": 30, "moovattu": 30, "muvattu": 30,
    "ನಲವತ್ತು": 40, "nalavattu": 40, "nalavathu": 40,
    "ಐವತ್ತು": 50, "aivattu": 50, "aivathu": 50,
    "ಅರವತ್ತು": 60, "aravattu": 60, "aravathu": 60,
    "ಎಪ್ಪತ್ತು": 70, "eppattu": 70, "yeppattu": 70,
    "ಎಂಬತ್ತು": 80, "embattu": 80, "yembattu": 80,
    "ತೊಂಬತ್ತು": 90, "tombattu": 90, "thombathu": 90,
}

_KN_SCALES: dict[str, int] = {
    "ನೂರು": 100, "ನೂರ": 100, "nooru": 100, "nuru": 100, "noora": 100,
    "ಸಾವಿರ": 1000, "ಸಾವಿರದ": 1000, "saavira": 1000, "savira": 1000, "saavirada": 1000,
    "ಲಕ್ಷ": 100_000, "ಲಕ್ಷದ": 100_000, "laksha": 100_000, "lakshada": 100_000,
    "ಕೋಟಿ": 10_000_000, "ಕೋಟಿಗಳ": 10_000_000, "koti": 10_000_000, "kotigalu": 10_000_000,
}

# Pre-multiplied Kannada hundred words (e.g. ಇನ್ನೂರು = 200, ಮುನ್ನೂರು = 300)
_KN_HUNDRED_VALUES: dict[str, int] = {
    "ಇನ್ನೂರು": 200, "ಇನ್ನೂರ": 200, "innooru": 200, "innuru": 200,
    "ಮುನ್ನೂರು": 300, "ಮುನ್ನೂರ": 300, "munnooru": 300, "munnuru": 300,
    "ನಾನೂರು": 400, "ನಾನೂರಾ": 400, "naanooru": 400, "nanuru": 400,
    "ಐನೂರು": 500, "ಐನೂರ": 500, "ainooru": 500, "ainuru": 500,
    "ಆರುನೂರು": 600, "aarunooru": 600,
    "ಏಳುನೂರು": 700, "elunooru": 700,
    "ಎಂಟುನೂರು": 800, "entunooru": 800,
    "ಒಂಬೈನೂರು": 900, "ombainooru": 900,
}

# Fused exact Telugu fractional-scale values
_EXACT_COMPOUNDS: dict[str, int] = {
    "వందన్నర": 150, "vandannara": 150,
    "వెయ్యిన్నర": 1500, "వేయిన్నర": 1500, "veyyinnara": 1500, "veyinnara": 1500,
    "లక్షన్నర": 150_000, "lakshannara": 150_000,
}

# Standalone fractional multipliers (must multiply a following SCALE to form an integer)
_FRAC_STANDALONE: dict[str, Fraction] = {
    "dedh": Fraction(3, 2),
    "ded": Fraction(3, 2),
    "डेढ़": Fraction(3, 2),
    "डेढ": Fraction(3, 2),
    "dhai": Fraction(5, 2),
    "adhaai": Fraction(5, 2),
    "adhai": Fraction(5, 2),
    "ढाई": Fraction(5, 2),
    "अढ़ाई": Fraction(5, 2),
    "अढाई": Fraction(5, 2),
    # Telugu half/three-quarter + fused -nnara (+0.5)
    "అర": Fraction(1, 2),
    "ara": Fraction(1, 2),
    "ముప్పావు": Fraction(3, 4),
    "muppaavu": Fraction(3, 4),
    "muppavu": Fraction(3, 4),
    "ఒకటిన్నర": Fraction(3, 2),
    "okatinnara": Fraction(3, 2),
    "రెండున్నర": Fraction(5, 2),
    "rendunnara": Fraction(5, 2),
    "మూడున్నర": Fraction(7, 2),
    "moodunnara": Fraction(7, 2),
    "నాలుగున్నర": Fraction(9, 2),
    "nalugunnara": Fraction(9, 2),
    "ఐదున్నర": Fraction(11, 2),
    "aidunnara": Fraction(11, 2),
    "ఆరున్నర": Fraction(13, 2),
    "aarunnara": Fraction(13, 2),
    "ఏడున్నర": Fraction(15, 2),
    "edunnara": Fraction(15, 2),
    "ఎనిమిదిన్నర": Fraction(17, 2),
    "enimidinnara": Fraction(17, 2),
    "తొమ్మిదిన్నర": Fraction(19, 2),
    "tommidinnara": Fraction(19, 2),
}

# Prefix modifiers: (delta, allow_bare_scale)
# sawa (+1/4): "sawa sau" = 1.25*100, "sawa do sau" = 2.25*100
# paune (-1/4): "paune sau" = 0.75*100, "paune do sau" = 1.75*100
# saade (+1/2): "saade teen sau" = 3.5*100 (requires integer base)
_FRAC_PREFIX: dict[str, tuple[Fraction, bool]] = {
    "sawa": (Fraction(1, 4), True),
    "sava": (Fraction(1, 4), True),
    "सवा": (Fraction(1, 4), True),
    "సవా": (Fraction(1, 4), True),
    "paune": (Fraction(-1, 4), True),
    "paun": (Fraction(-1, 4), True),
    "पौने": (Fraction(-1, 4), True),
    "पौन": (Fraction(-1, 4), True),
    "saade": (Fraction(1, 2), False),
    "saadhe": (Fraction(1, 2), False),
    "sadhe": (Fraction(1, 2), False),
    "sade": (Fraction(1, 2), False),
    "साढ़े": (Fraction(1, 2), False),
    "साढे": (Fraction(1, 2), False),
}

_JOINERS: frozenset[str] = frozenset({"and", "aur", "और", "మరియు", "ಮತ್ತು", "mattu"})

_CURRENCY_WORDS: frozenset[str] = frozenset(
    {
        "₹",
        "/-",
        "rs",
        "inr",
        "rupee",
        "rupees",
        "rupaye",
        "rupay",
        "rupaiye",
        "rupaiya",
        "rupia",
        "रुपये",
        "रुपए",
        "रुपया",
        "रू",
        "रु",
        "రూపాయలు",
        "రూపాయి",
        "రూ",
        "roopayalu",
        "rupayalu",
        "ರೂಪಾಯಿ",
        "ರೂಪಾಯಿಗಳು",
        "ರೂ",
        "roopayi",
        "rupayi",
        "roopayigalu",
        "only",
    }
)

# Hindi imperative verb stems that precede auxiliary "do" ("likh do", "kar do", "de do").
_HINDI_DO_VERB_STEMS: frozenset[str] = frozenset(
    {
        "likh", "kar", "de", "bata", "daal", "dal", "jod", "kaat", "kat",
        "hata", "bhej", "chhod", "rakh", "nikal", "chuka", "bol", "padh",
        "jama", "udhaar", "udhar", "entry", "note", "add", "cancel", "undo",
        "लिख", "कर", "दे", "बता", "डाल", "जोड़", "काट", "हटा", "भेज",
        "छोड़", "रख", "निकाल", "चुका", "बोल", "पढ़", "जमा", "दिखा",
    }
)

_TE_TENS_SCRIPT_PREFIXES: tuple[tuple[str, int], ...] = (
    ("ఇరవై", 20),
    ("ముప్పై", 30),
    ("నలభై", 40),
    ("యాభై", 50),
    ("ఏభై", 50),
    ("అరవై", 60),
    ("డెబ్బై", 70),
    ("ఎనభై", 80),
    ("తొంభై", 90),
)

_TE_UNITS_SCRIPT: dict[str, int] = {
    "ఒకటి": 1,
    "రెండు": 2,
    "మూడు": 3,
    "నాలుగు": 4,
    "ఐదు": 5,
    "అయిదు": 5,
    "ఆరు": 6,
    "ఏడు": 7,
    "ఎనిమిది": 8,
    "తొమ్మిది": 9,
}

_KN_TENS_SCRIPT_PREFIXES: tuple[tuple[str, int], ...] = (
    ("ಇಪ್ಪತ್ತ", 20),
    ("ಮೂವತ್ತ", 30),
    ("ನಲವತ್ತ", 40),
    ("ಐವತ್ತ", 50),
    ("ಅರವತ್ತ", 60),
    ("ಎಪ್ಪತ್ತ", 70),
    ("ಎಂಬತ್ತ", 80),
    ("ತೊಂಬತ್ತ", 90),
)

_KN_UNITS_SCRIPT_SUFFIX: dict[str, int] = {
    "ಒಂದು": 1, "ಒಂದ": 1, "ೊಂದು": 1, "ೋಂದು": 1,
    "ಎರಡು": 2, "ಎರಡ": 2, "ೆರಡು": 2, "ೇರಡು": 2,
    "ಮೂರು": 3, "ೂರು": 3,
    "ನಾಲ್ಕು": 4,
    "ಐದು": 5, "ೈದು": 5,
    "ಆರು": 6, "ಾರು": 6,
    "ಏಳು": 7, "ೇಳು": 7,
    "ಎಂಟು": 8, "ೆಂಟು": 8,
    "ಒಂಬತ್ತು": 9, "ೊಂಬತ್ತು": 9,
}

# Build NFC-normalized lookup tables so decomposed Devanagari nuktas match cleanly.
def _nfc_dict(src: dict[str, int]) -> dict[str, int]:
    out: dict[str, int] = {}
    for k, v in src.items():
        out[unicodedata.normalize("NFC", k)] = v
    return out


_UNITS_ALL: dict[str, tuple[int, str]] = {}
for _k, _v in _nfc_dict(_EN_UNITS).items():
    _UNITS_ALL[_k] = (_v, "en")
for _k, _v in _nfc_dict(_HI_UNITS).items():
    _UNITS_ALL[_k] = (_v, "hi")
for _k, _v in _nfc_dict(_TE_UNITS).items():
    _UNITS_ALL[_k] = (_v, "te")
for _k, _v in _nfc_dict(_KN_UNITS).items():
    _UNITS_ALL[_k] = (_v, "kn")

_TEENS_ALL: dict[str, tuple[int, str]] = {}
for _k, _v in _nfc_dict(_EN_TEENS).items():
    _TEENS_ALL[_k] = (_v, "en")
for _k, _v in _nfc_dict(_HI_TEENS).items():
    _TEENS_ALL[_k] = (_v, "hi")
for _k, _v in _nfc_dict(_TE_TEENS).items():
    _TEENS_ALL[_k] = (_v, "te")
for _k, _v in _nfc_dict(_KN_TEENS).items():
    _TEENS_ALL[_k] = (_v, "kn")

_TENS_ALL: dict[str, tuple[int, str]] = {}
for _k, _v in _nfc_dict(_EN_TENS).items():
    _TENS_ALL[_k] = (_v, "en")
for _k, _v in _nfc_dict(_TE_TENS).items():
    _TENS_ALL[_k] = (_v, "te")
for _k, _v in _nfc_dict(_KN_TENS).items():
    _TENS_ALL[_k] = (_v, "kn")

_COMPOUNDS_ALL: dict[str, tuple[int, str]] = {
    unicodedata.normalize("NFC", k): (v, "hi") for k, v in _HI_COMPOUNDS.items()
}

_SCALES_ALL: dict[str, tuple[int, str]] = {}
for _k, _v in _nfc_dict(_EN_SCALES).items():
    _SCALES_ALL[_k] = (_v, "en")
for _k, _v in _nfc_dict(_HI_SCALES).items():
    _SCALES_ALL[_k] = (_v, "hi")
for _k, _v in _nfc_dict(_TE_SCALES).items():
    _SCALES_ALL[_k] = (_v, "te")
for _k, _v in _nfc_dict(_KN_SCALES).items():
    _SCALES_ALL[_k] = (_v, "kn")

_HUNDRED_VALUES_ALL = _nfc_dict(_TE_HUNDRED_VALUES)
_HUNDRED_VALUES_ALL.update(_nfc_dict(_KN_HUNDRED_VALUES))
_EXACT_COMPOUNDS_ALL = _nfc_dict(_EXACT_COMPOUNDS)
_FRAC_STANDALONE_ALL = {unicodedata.normalize("NFC", k): v for k, v in _FRAC_STANDALONE.items()}
_FRAC_PREFIX_ALL = {unicodedata.normalize("NFC", k): v for k, v in _FRAC_PREFIX.items()}

TokenKind = Literal[
    "DIGIT",
    "INVALID_DIGIT",
    "UNIT",
    "TEEN",
    "TENS",
    "COMPOUND",
    "HUNDRED_VALUE",
    "EXACT",
    "SCALE",
    "FRAC_STANDALONE",
    "FRAC_PREFIX",
    "JOINER",
    "OH",
    "CURRENCY",
    "OTHER",
]


@dataclass(frozen=True, slots=True)
class AmountSpan:
    """Extracted integer rupee amount with character offsets into original text."""

    value: int
    start: int
    end: int
    confidence: float
    is_contextual: bool = False


@dataclass(frozen=True, slots=True)
class _Tok:
    kind: TokenKind
    raw: str
    norm: str
    start: int
    end: int
    val: int = 0
    frac: Fraction | None = None
    allow_bare_scale: bool = False
    lang: str = ""
    hyphen_prev: bool = False


def _parse_digit_token(raw: str) -> int | None:
    """Parse ASCII digit token; reject decimals, negatives, k/m suffixes, bad commas."""
    if raw.startswith("-") or raw.startswith("+"):
        return None
    if "." in raw or any(ch.isalpha() for ch in raw):
        return None
    if "," in raw:
        if not (_WESTERN_COMMA_RE.match(raw) or _INDIAN_COMMA_RE.match(raw)):
            return None
        return int(raw.replace(",", ""))
    if _PLAIN_INT_RE.match(raw):
        return int(raw)
    return None


def _classify_word(word: str, start: int, end: int, *, hyphen_prev: bool = False) -> _Tok:
    norm = unicodedata.normalize("NFC", word).casefold()

    if norm in _CURRENCY_WORDS:
        return _Tok("CURRENCY", word, norm, start, end, hyphen_prev=hyphen_prev)
    if norm in _JOINERS:
        return _Tok("JOINER", word, norm, start, end, hyphen_prev=hyphen_prev)
    if norm in ("oh", "o"):
        return _Tok("OH", word, norm, start, end, lang="en", hyphen_prev=hyphen_prev)
    if norm in _EXACT_COMPOUNDS_ALL:
        return _Tok("EXACT", word, norm, start, end, val=_EXACT_COMPOUNDS_ALL[norm], lang="te", hyphen_prev=hyphen_prev)
    if norm in _HUNDRED_VALUES_ALL:
        # Check if Telugu or Kannada
        h_lang = "kn" if norm in _KN_HUNDRED_VALUES else "te"
        return _Tok("HUNDRED_VALUE", word, norm, start, end, val=_HUNDRED_VALUES_ALL[norm], lang=h_lang, hyphen_prev=hyphen_prev)
    if norm in _FRAC_STANDALONE_ALL:
        return _Tok("FRAC_STANDALONE", word, norm, start, end, frac=_FRAC_STANDALONE_ALL[norm], hyphen_prev=hyphen_prev)
    if norm in _FRAC_PREFIX_ALL:
        delta, allow_bare = _FRAC_PREFIX_ALL[norm]
        return _Tok(
            "FRAC_PREFIX",
            word,
            norm,
            start,
            end,
            frac=delta,
            allow_bare_scale=allow_bare,
            hyphen_prev=hyphen_prev,
        )
    if norm in _SCALES_ALL:
        val, lang = _SCALES_ALL[norm]
        return _Tok("SCALE", word, norm, start, end, val=val, lang=lang, hyphen_prev=hyphen_prev)
    if norm in _UNITS_ALL:
        val, lang = _UNITS_ALL[norm]
        return _Tok("UNIT", word, norm, start, end, val=val, lang=lang, hyphen_prev=hyphen_prev)
    if norm in _TEENS_ALL:
        val, lang = _TEENS_ALL[norm]
        return _Tok("TEEN", word, norm, start, end, val=val, lang=lang, hyphen_prev=hyphen_prev)
    if norm in _TENS_ALL:
        val, lang = _TENS_ALL[norm]
        return _Tok("TENS", word, norm, start, end, val=val, lang=lang, hyphen_prev=hyphen_prev)
    if norm in _COMPOUNDS_ALL:
        val, lang = _COMPOUNDS_ALL[norm]
        return _Tok("COMPOUND", word, norm, start, end, val=val, lang=lang, hyphen_prev=hyphen_prev)

    # Fused Telugu tens + unit (e.g., "నలభైఐదు" -> 45)
    for prefix, tens_val in _TE_TENS_SCRIPT_PREFIXES:
        if norm.startswith(prefix) and len(norm) > len(prefix):
            rem = norm[len(prefix) :]
            if rem in _TE_UNITS_SCRIPT:
                return _Tok(
                    "COMPOUND",
                    word,
                    norm,
                    start,
                    end,
                    val=tens_val + _TE_UNITS_SCRIPT[rem],
                    lang="te",
                    hyphen_prev=hyphen_prev,
                )

    # Fused Kannada tens + unit (e.g., "ಇಪ್ಪತ್ತೈದು" -> 25, "ಮೂವತ್ತೈದು" -> 35)
    for prefix, tens_val in _KN_TENS_SCRIPT_PREFIXES:
        if norm.startswith(prefix) and len(norm) > len(prefix):
            rem = norm[len(prefix) :]
            if rem in _KN_UNITS_SCRIPT_SUFFIX:
                return _Tok(
                    "COMPOUND",
                    word,
                    norm,
                    start,
                    end,
                    val=tens_val + _KN_UNITS_SCRIPT_SUFFIX[rem],
                    lang="kn",
                    hyphen_prev=hyphen_prev,
                )

    return _Tok("OTHER", word, norm, start, end, hyphen_prev=hyphen_prev)


def _tokenize(text: str) -> list[_Tok]:
    translated = text.translate(_DIGIT_TRANSLATION)
    tokens: list[_Tok] = []

    for match in _TOKEN_RE.finditer(translated):
        start, end = match.start(), match.end()
        if match.group("digit") is not None:
            raw = match.group("digit")
            parsed = _parse_digit_token(raw)
            if parsed is None:
                tokens.append(_Tok("INVALID_DIGIT", raw, raw.casefold(), start, end))
            else:
                tokens.append(_Tok("DIGIT", raw, raw, start, end, val=parsed))
        elif match.group("currency") is not None:
            raw = match.group("currency")
            tokens.append(_Tok("CURRENCY", raw, raw, start, end))
        else:
            raw = match.group("word")
            if "-" in raw:
                pos = start
                parts = raw.split("-")
                for idx, part in enumerate(parts):
                    p_end = pos + len(part)
                    tokens.append(_classify_word(part, pos, p_end, hyphen_prev=(idx > 0)))
                    pos = p_end + 1
            else:
                tokens.append(_classify_word(raw, start, end))

    # Post-pass 1: Merge Telugu two-word "పావు తక్కువ" / "paavu takkuva" -> FRAC_PREFIX(-1/4)
    merged: list[_Tok] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if (
            i + 1 < len(tokens)
            and tok.norm in ("పావు", "paavu", "pavu")
            and tokens[i + 1].norm in ("తక్కువ", "takkuva")
        ):
            nxt = tokens[i + 1]
            merged.append(
                _Tok(
                    "FRAC_PREFIX",
                    text[tok.start : nxt.end],
                    "paavu_takkuva",
                    tok.start,
                    nxt.end,
                    frac=Fraction(-1, 4),
                    allow_bare_scale=True,
                    lang="te",
                )
            )
            i += 2
            continue
        merged.append(tok)
        i += 1
    tokens = merged

    # Post-pass 2: Disambiguate Hindi imperative auxiliary "do" / "दो" ("likh do", "kar do")
    for idx, tok in enumerate(tokens):
        if tok.norm in ("do", "दो") and tok.kind == "UNIT":
            prev_tok = tokens[idx - 1] if idx > 0 else None
            next_tok = tokens[idx + 1] if idx + 1 < len(tokens) else None
            if prev_tok is not None and prev_tok.norm in _HINDI_DO_VERB_STEMS:
                next_is_num_scale = next_tok is not None and next_tok.kind in (
                    "SCALE",
                    "CURRENCY",
                )
                if not next_is_num_scale:
                    tokens[idx] = _Tok("OTHER", tok.raw, tok.norm, tok.start, tok.end)

    return tokens


_NUMERIC_STARTERS: frozenset[TokenKind] = frozenset(
    {
        "DIGIT",
        "INVALID_DIGIT",
        "UNIT",
        "TEEN",
        "TENS",
        "COMPOUND",
        "HUNDRED_VALUE",
        "EXACT",
        "SCALE",
        "FRAC_STANDALONE",
        "FRAC_PREFIX",
    }
)


_TX_CONTEXT_RE = re.compile(
    r"""(?xi)
    \b(
        # English
        rupee|rupees|rs|inr|credit|debit|paid|pay|payment|received|receive|gave|got|owe|due|bill|balance|account|entry
        # Hindi
        |udhaar|udhar|jama|khata|khate|diya|diye|di|liye|li|likh|likho|baki|baaki|hisaab|hisab|chuka|de|le|rupaye|rupaya|rupay
        |उधार|जमा|खाता|खाते|दिया|दिए|दी|लिया|लिए|लिख|लिखो|बाकी|हिसाब|रुपये|रुपए|रुपया|रुपये|दे|लो
        # Telugu
        |appu|jama|khata|khatalo|icchanu|iccharu|ivvandi|rayandi|baaki|roopayalu|rupayalu
        |అప్పు|జమ|ఖాతా|ఖాతాలో|ఇచ్చారు|ఇచ్చాను|ఇవ్వండి|రాయండి|బాకీ|రూపాయలు|రూపాయి
        # Kannada
        |udhdhari|uddhari|saala|jama|khate|khateli|kotte|kottaru|kodi|baredi|bari|baki|roopayi|roopayigalu
        |ಉದ್ಧರಿ|ಉದ್ರಿ|ಸಾಲ|ಜಮಾ|ಖಾತೆ|ಖಾತೆಯಲ್ಲಿ|ಕೊಟ್ಟೆ|ಕೊಟ್ಟರು|ಕೊಡಿ|ಬರೆ|ಬರೆಯಿರಿ|ಬಾಕಿ|ರೂಪಾಯಿ|ರೂಪಾಯಿಗಳು
    )\b
    """
)

_INDIC_LEFT = r"(?<![0-9A-Za-z\u0900-\u0CFF])"
_INDIC_RIGHT = r"(?![0-9A-Za-z\u0900-\u0CFF])"

_FRACTION_OR_PAISE_RE = re.compile(
    rf"""(?xi)
    # Explicit sub-rupee / paise words
    {_INDIC_LEFT}(?:
        paise|paisa|pise|पैसे|पैसा|పైసలు|పైసా|ಪೈಸೆ|ಪೈಸಾ
        |chavanni|athanni|चवन्नी|अठन्नी
        |half\s+rupee|quarter\s+rupee|aadha\s+rupaya|aadhe\s+rupaye
        |ಅರ್ಧ\s+ರೂಪಾಯಿ|అర\s+రూపాయి
    ){_INDIC_RIGHT}
    # Decimal numbers: e.g. 12.50, .50, 3.5
    | \b\d+\.\d+\b
    """
)


def _has_transaction_context(text: str) -> bool:
    if "₹" in text or "/-" in text:
        return True
    return bool(_TX_CONTEXT_RE.search(text))


def _is_whitespace_only_between(text: str, left_end: int, right_start: int) -> bool:
    between = text[left_end:right_start]
    return between.strip() == ""


def _parse_group_under_1000(
    tokens: list[_Tok], idx: int, text: str
) -> tuple[int | None, int, bool, bool, bool]:
    """Parse a number component in 0..999 starting at tokens[idx].

    Returns (value, next_idx, used_hundred_scale, ambiguous, is_contextual).
    """
    n = len(tokens)
    if idx >= n:
        return None, idx, False, False, False

    tok = tokens[idx]

    # Case 1: Invalid digit token (e.g. "1.5k", "12.50", "1,2")
    if tok.kind == "INVALID_DIGIT":
        return None, idx + 1, False, True, False

    # Case 2: Bare DIGIT token
    if tok.kind == "DIGIT":
        # Adjacent bare digits separated only by whitespace (e.g. "3 40") -> AMBIGUOUS!
        if (
            idx + 1 < n
            and tokens[idx + 1].kind in ("DIGIT", "INVALID_DIGIT")
            and _is_whitespace_only_between(text, tok.end, tokens[idx + 1].start)
        ):
            j = idx + 1
            while (
                j + 1 < n
                and tokens[j + 1].kind in ("DIGIT", "INVALID_DIGIT")
                and _is_whitespace_only_between(text, tokens[j].end, tokens[j + 1].start)
            ):
                j += 1
            return None, j + 1, False, True, False

        # Check "<digit> sau/hundred" (e.g., "5 sau" -> 500, "5 sau 40" -> 540)
        if idx + 1 < n and tokens[idx + 1].kind == "SCALE" and tokens[idx + 1].val == 100:
            if tok.val < 1 or tok.val > 99:
                return None, idx + 2, True, True, False
            val = tok.val * 100
            j = idx + 2
            j_after_join = j + 1 if (j < n and tokens[j].kind == "JOINER") else j
            rem, j_end, rem_amb = _parse_under_100(tokens, j_after_join, text)
            if rem_amb:
                return None, j_end, True, True, False
            if rem is not None:
                return val + rem, j_end, True, False, False
            return val, j, True, False, False

        return tok.val, idx + 1, False, False, False

    # Case 3: Pre-multiplied Telugu/Kannada hundred value (e.g., "నూట యాభై" = 150, "ಇನ್ನೂರು" = 200)
    if tok.kind == "HUNDRED_VALUE":
        val = tok.val
        j = idx + 1
        j_after_join = j + 1 if (j < n and tokens[j].kind == "JOINER") else j
        rem, j_end, rem_amb = _parse_under_100(tokens, j_after_join, text)
        if rem_amb:
            return None, j_end, True, True, False
        if rem is not None:
            return val + rem, j_end, True, False, False
        return val, j, True, False, False

    # Case 4: Bare hundred scale ("sau", "सौ", "hundred", "వంద", "ನೂರು") -> 100 (+ optional under-100)
    if tok.kind == "SCALE" and tok.val == 100:
        val = 100
        j = idx + 1
        j_after_join = j + 1 if (j < n and tokens[j].kind == "JOINER") else j
        rem, j_end, rem_amb = _parse_under_100(tokens, j_after_join, text)
        if rem_amb:
            return None, j_end, True, True, False
        if rem is not None:
            return val + rem, j_end, True, False, False
        return val, j, True, False, False

    # Case 5: Colloquial English hundreds without scale word ("three forty" = 340, "two fifty" = 250)
    # Digits-less: only parses as hundreds inside transaction context with no other numbers.
    if tok.kind == "UNIT" and tok.val >= 1 and tok.lang == "en" and idx + 1 < n:
        nxt = tokens[idx + 1]
        if nxt.kind == "OH" and idx + 2 < n and tokens[idx + 2].kind == "UNIT" and tokens[idx + 2].lang == "en":
            return tok.val * 100 + tokens[idx + 2].val, idx + 3, True, False, True
        if nxt.kind == "TEEN" and nxt.lang == "en":
            return tok.val * 100 + nxt.val, idx + 2, True, False, True
        if nxt.kind == "TENS" and nxt.lang == "en":
            val = tok.val * 100 + nxt.val
            if idx + 2 < n and tokens[idx + 2].kind == "UNIT" and tokens[idx + 2].lang == "en" and tokens[idx + 2].val >= 1:
                return val + tokens[idx + 2].val, idx + 3, True, False, True
            return val, idx + 2, True, False, True

    # Case 6: Standard under-100 optionally followed by hundred scale (e.g. "teen sau chaalis" = 340)
    base, j, amb = _parse_under_100(tokens, idx, text)
    if amb:
        return None, j, False, True, False
    if base is None:
        return None, idx, False, False, False

    if j < n and tokens[j].kind == "SCALE" and tokens[j].val == 100:
        if base == 0:
            return None, j + 1, True, True, False
        val = base * 100
        j += 1
        j_after_join = j + 1 if (j < n and tokens[j].kind == "JOINER") else j
        rem, j_end, rem_amb = _parse_under_100(tokens, j_after_join, text)
        if rem_amb:
            return None, j_end, True, True, False
        if rem is not None:
            return val + rem, j_end, True, False, False
        return val, j, True, False, False

    return base, j, False, False, False


def _parse_under_100(tokens: list[_Tok], idx: int, text: str) -> tuple[int | None, int, bool]:
    """Parse a single 0..99 component (word or 1..99 digit after a scale)."""
    n = len(tokens)
    if idx >= n:
        return None, idx, False
    tok = tokens[idx]

    if tok.kind == "INVALID_DIGIT":
        return None, idx + 1, True

    if tok.kind == "DIGIT":
        if (
            idx + 1 < n
            and tokens[idx + 1].kind in ("DIGIT", "INVALID_DIGIT")
            and _is_whitespace_only_between(text, tok.end, tokens[idx + 1].start)
        ):
            return None, idx + 2, True
        if 1 <= tok.val < 100:
            # Don't swallow a digit that actually multiplies a following scale (e.g. "2 hazaar 5 sau")
            if idx + 1 < n and tokens[idx + 1].kind == "SCALE":
                return None, idx, False
            return tok.val, idx + 1, False
        return None, idx, False

    if tok.kind == "COMPOUND":
        # Reject ambiguous double compounds like "pachas saath"
        if idx + 1 < n and tokens[idx + 1].kind in ("UNIT", "TEEN", "TENS", "COMPOUND"):
            if not (tokens[idx + 1].kind == "SCALE"):
                return None, idx + 2, True
        return tok.val, idx + 1, False

    if tok.kind == "TEEN":
        if idx + 1 < n and tokens[idx + 1].kind in ("UNIT", "TEEN", "TENS", "COMPOUND"):
            return None, idx + 2, True
        return tok.val, idx + 1, False

    if tok.kind == "TENS":
        if idx + 1 < n and tokens[idx + 1].kind == "UNIT" and tokens[idx + 1].val >= 1:
            # Make sure the following unit isn't multiplying a scale ("twenty five hundred" -> 25*100 is fine!)
            return tok.val + tokens[idx + 1].val, idx + 2, False
        if idx + 1 < n and tokens[idx + 1].kind in ("TENS", "TEEN", "COMPOUND"):
            return None, idx + 2, True
        return tok.val, idx + 1, False

    if tok.kind == "UNIT":
        # Two consecutive units ("two three", "do teen") -> ambiguous!
        if idx + 1 < n and tokens[idx + 1].kind in ("UNIT", "TEEN", "TENS", "COMPOUND"):
            return None, idx + 2, True
        return tok.val, idx + 1, False

    return None, idx, False


def _parse_fractional_expression(
    tokens: list[_Tok], idx: int
) -> tuple[int | None, int, int, bool]:
    """Parse Indian fractional expressions (dedh, dhai, sawa, paune, saade, -nnara).

    Returns (value, next_idx, consumed_scale, ambiguous).
    """
    n = len(tokens)
    tok = tokens[idx]

    if tok.kind == "FRAC_STANDALONE":
        assert tok.frac is not None
        if idx + 1 < n and tokens[idx + 1].kind == "SCALE":
            scale = tokens[idx + 1].val
            exact = tok.frac * scale
            if exact.denominator != 1:
                return None, idx + 2, scale, True
            return int(exact), idx + 2, scale, False
        # Standalone "dedh" / "dhai" without a scale is 1.5 / 2.5 -> non-integer -> ambiguous
        return None, idx + 1, 0, True

    if tok.kind == "FRAC_PREFIX":
        assert tok.frac is not None
        # Pattern A: "<prefix> <scale>" e.g. "sawa sau" = 125, "paune sau" = 75, "sawa hazaar" = 1250
        if idx + 1 < n and tokens[idx + 1].kind == "SCALE":
            if not tok.allow_bare_scale:
                return None, idx + 2, 0, True
            scale = tokens[idx + 1].val
            exact = (Fraction(1) + tok.frac) * scale
            if exact.denominator != 1 or exact <= 0:
                return None, idx + 2, scale, True
            return int(exact), idx + 2, scale, False

        # Pattern B: "<prefix> <int> <scale>" e.g. "paune do sau" = 175, "saade teen sau" = 350
        if idx + 2 < n:
            mid = tokens[idx + 1]
            scl = tokens[idx + 2]
            if mid.kind in ("UNIT", "TEEN", "TENS", "COMPOUND", "DIGIT") and scl.kind == "SCALE":
                base_val = mid.val
                if base_val < 1:
                    return None, idx + 3, 0, True
                exact = (Fraction(base_val) + tok.frac) * scl.val
                if exact.denominator != 1 or exact <= 0:
                    return None, idx + 3, scl.val, True
                return int(exact), idx + 3, scl.val, False

        # "<prefix> <int>" without scale (e.g. "sawa paanch" = 5.25) -> non-integer -> None
        end_idx = idx + 2 if (idx + 1 < n and tokens[idx + 1].kind in ("UNIT", "TEEN", "TENS", "COMPOUND", "DIGIT")) else idx + 1
        return None, end_idx, 0, True

    return None, idx, 0, False


def _parse_number_span(
    tokens: list[_Tok],
    start_idx: int,
    text: str,
    *,
    in_transaction_context: bool = False,
) -> tuple[int | None, int, float, bool, bool]:
    """Parse a complete multi-scale integer number starting at tokens[start_idx].

    Returns (value, end_idx_exclusive, confidence, was_ambiguous, is_contextual).
    """
    n = len(tokens)
    idx = start_idx
    total = 0
    max_remaining_scale = 100_000_000  # Strictly decreasing scale hierarchy
    consumed_any = False
    has_word_tokens = False
    has_digit_tokens = False
    span_is_contextual = False

    while idx < n:
        # Handle optional joiner ("and", "aur", "और") between strictly decreasing scale parts
        joiner_skipped = False
        if consumed_any and tokens[idx].kind == "JOINER":
            if idx + 1 < n and tokens[idx + 1].kind in _NUMERIC_STARTERS:
                idx += 1
                joiner_skipped = True
            else:
                break

        tok = tokens[idx]
        if tok.kind not in _NUMERIC_STARTERS:
            if joiner_skipped:
                idx -= 1
            break

        # 1. Invalid digit token poisons the span
        if tok.kind == "INVALID_DIGIT":
            return None, idx + 1, 0.0, True, False

        # 2. Exact fused compounds ("వందన్నర"=150, "ವೆಯ್ಯಿನ್ನರ"=1500, "ಲಕ್ಷನ್ನರ"=150000)
        if tok.kind == "EXACT":
            eff_scale = 100_000 if tok.val >= 100_000 else (1000 if tok.val >= 1000 else 100)
            if eff_scale >= max_remaining_scale:
                if joiner_skipped:
                    idx -= 1
                break
            total += tok.val
            max_remaining_scale = eff_scale
            consumed_any = True
            has_word_tokens = True
            idx += 1
            continue

        # 3. Fractional modifier expressions ("dedh sau", "paune do sau", "saade teen sau")
        if tok.kind in ("FRAC_STANDALONE", "FRAC_PREFIX"):
            f_val, f_next, f_scale, f_amb = _parse_fractional_expression(tokens, idx)
            if f_amb or f_val is None:
                return None, f_next, 0.0, True, False
            if f_scale >= max_remaining_scale:
                if joiner_skipped:
                    idx -= 1
                break
            total += f_val
            max_remaining_scale = f_scale
            consumed_any = True
            has_word_tokens = True
            idx = f_next
            continue

        # 4. Bare major scale ("hazaar", "lakh", "crore", "వెయ్యి", "ಸಾವಿರ")
        if tok.kind == "SCALE" and tok.val >= 1000:
            if tok.val >= max_remaining_scale:
                if joiner_skipped:
                    idx -= 1
                break
            total += tok.val
            max_remaining_scale = tok.val
            consumed_any = True
            has_word_tokens = True
            idx += 1
            continue

        # 5. Group under 1000 (or standalone large digit token like "1200" or "340")
        grp_val, grp_next, used_hundred, grp_amb, grp_ctx = _parse_group_under_1000(tokens, idx, text)
        if grp_amb:
            return None, grp_next, 0.0, True, False
        if grp_val is None:
            if joiner_skipped:
                idx -= 1
            break

        if grp_ctx:
            span_is_contextual = True

        # Check if this group is followed by a major scale (thousand / lakh / crore)
        if grp_next < n and tokens[grp_next].kind == "SCALE" and tokens[grp_next].val >= 1000:
            scale_val = tokens[grp_next].val
            if scale_val >= max_remaining_scale:
                if joiner_skipped:
                    idx -= 1
                break
            if grp_val <= 0:
                return None, grp_next + 1, 0.0, True, False
            if any(t.kind == "DIGIT" for t in tokens[idx:grp_next]):
                has_digit_tokens = True
            if any(t.kind != "DIGIT" for t in tokens[idx : grp_next + 1]):
                has_word_tokens = True
            total += grp_val * scale_val
            max_remaining_scale = scale_val
            consumed_any = True
            idx = grp_next + 1
            continue

        # Otherwise this group is the trailing <1000 remainder or a standalone digit number
        if consumed_any:
            if grp_val <= 0 or grp_val >= max_remaining_scale:
                if joiner_skipped:
                    idx -= 1
                break
            if used_hundred and max_remaining_scale <= 100:
                if joiner_skipped:
                    idx -= 1
                break
            if any(t.kind == "DIGIT" for t in tokens[idx:grp_next]):
                has_digit_tokens = True
            if any(t.kind != "DIGIT" for t in tokens[idx:grp_next]):
                has_word_tokens = True
            total += grp_val
            max_remaining_scale = 1
            idx = grp_next
            break
        else:
            # Standalone first group
            if any(t.kind == "DIGIT" for t in tokens[idx:grp_next]):
                has_digit_tokens = True
            if any(t.kind != "DIGIT" for t in tokens[idx:grp_next]):
                has_word_tokens = True
            total = grp_val
            consumed_any = True
            max_remaining_scale = 100 if used_hundred else 1
            idx = grp_next
            break

    if not consumed_any:
        return None, start_idx, 0.0, False, False

    # Positive integer rupees only (1..999_999_999).
    if total <= 0:
        return None, idx, 0.0, True, False

    # If a trailing invalid digit immediately follows (e.g. "2 hazaar 5.5") -> poison whole span
    if idx < n and tokens[idx].kind == "INVALID_DIGIT" and _is_whitespace_only_between(
        text, tokens[idx - 1].end, tokens[idx].start
    ):
        return None, idx + 1, 0.0, True, False

    # Colloquial digits-less pattern (e.g. "three forty"):
    # Must only parse as 340 inside transaction context with NO OTHER numbers.
    if span_is_contextual and not has_digit_tokens:
        has_tx = in_transaction_context or _has_transaction_context(text)
        if not has_tx:
            return None, idx, 0.0, True, False

        # Check that NO other number token exists in the text
        other_nums = [
            t for k, t in enumerate(tokens)
            if (k < start_idx or k >= idx) and t.kind in _NUMERIC_STARTERS
        ]
        if other_nums:
            return None, idx, 0.0, True, False

    confidence = 0.90 if span_is_contextual else (
        0.99 if (has_digit_tokens and not has_word_tokens) else (
            0.93 if (has_digit_tokens and has_word_tokens) else 0.95
        )
    )
    return total, idx, confidence, False, span_is_contextual


def extract_amounts(
    text: str,
    lang_hint: str | None = None,
    *,
    in_transaction_context: bool = False,
) -> list[AmountSpan]:
    """Extract all unambiguous positive integer rupee amounts with exact text spans.

    Ambiguous numeric fragments (e.g. "3 40", "1.5k", "12.50", "two three",
    "sawa paanch") and fractions of a rupee are skipped and never guessed.
    """
    _ = lang_hint  # All supported scripts/languages are parsed uniformly.
    if not text or not text.strip():
        return []

    if _FRACTION_OR_PAISE_RE.search(text):
        return []

    tokens = _tokenize(text)
    spans: list[AmountSpan] = []
    i = 0
    n = len(tokens)

    while i < n:
        tok = tokens[i]
        if tok.kind not in _NUMERIC_STARTERS:
            i += 1
            continue

        val, end_idx, conf, ambiguous, is_ctx = _parse_number_span(
            tokens, i, text, in_transaction_context=in_transaction_context
        )
        if ambiguous or val is None or end_idx <= i:
            i = max(i + 1, end_idx)
            continue

        span_start = tokens[i].start
        span_end = tokens[end_idx - 1].end
        spans.append(
            AmountSpan(
                value=val,
                start=span_start,
                end=span_end,
                confidence=conf,
                is_contextual=is_ctx,
            )
        )
        i = end_idx

    return spans


def parse_amount(
    text: str,
    lang_hint: str | None = None,
    *,
    in_transaction_context: bool = False,
) -> int | None:
    """Parse a single unambiguous integer rupee amount from text, else None.

    Returns None if:
    - no valid integer amount is found,
    - any invalid/ambiguous numeric token exists in the input (e.g. "3 40", "1.5k"),
    - fraction of a rupee is present,
    - "three forty" is uttered without transaction context or with other numbers,
    - more than one distinct amount is present.
    """
    if not text or not text.strip():
        return None

    if _FRACTION_OR_PAISE_RE.search(text):
        return None

    tokens = _tokenize(text)
    spans: list[AmountSpan] = []
    saw_ambiguous = False
    i = 0
    n = len(tokens)

    while i < n:
        tok = tokens[i]
        if tok.kind not in _NUMERIC_STARTERS:
            i += 1
            continue

        val, end_idx, conf, ambiguous, is_ctx = _parse_number_span(
            tokens, i, text, in_transaction_context=in_transaction_context
        )
        if ambiguous or val is None or end_idx <= i:
            saw_ambiguous = True
            i = max(i + 1, end_idx)
            continue

        spans.append(
            AmountSpan(
                value=val,
                start=tokens[i].start,
                end=tokens[end_idx - 1].end,
                confidence=conf,
                is_contextual=is_ctx,
            )
        )
        i = end_idx

    if saw_ambiguous or len(spans) != 1:
        return None
    return spans[0].value
