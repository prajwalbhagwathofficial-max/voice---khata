"""Table-driven test suite for Kannada numbers, digits, and currency phrases.

==============================================================================
WARNING: NATIVE-SPEAKER REVIEW REQUIRED (NEEDS_REVIEW)

This file contains table-driven test cases for Kannada numbers (digits,
number words, compounds, scales, and colloquial Kirana phrases).
All entries must be reviewed and approved by a native Kannada speaker
before deployment to production.
==============================================================================
"""

from __future__ import annotations

import pytest

from server.nlu.numbers import parse_amount

KANNADA_NEEDS_REVIEW_CASES: list[tuple[str, int | None]] = [
    # --- Kannada digits (U+0CE6 .. U+0CEF) ---
    ("೦", None),  # 0 is rejected as transaction rupee amount
    ("೧", 1),
    ("೨", 2),
    ("೩", 3),
    ("೪", 4),
    ("೫", 5),
    ("೬", 6),
    ("೭", 7),
    ("೮", 8),
    ("೯", 9),
    ("೩೪೦", 340),
    ("೧,೨೦೦", 1200),
    ("೨೫೦ ರೂಪಾಯಿ", 250),
    ("೧,೨೦,೦೦೦", 120000),
    ("೫೦೦೦", 5000),
    ("೯೯", 99),
    # --- Kannada unit words (0..9) ---
    ("ಒಂದು ರೂಪಾಯಿ", 1),
    ("ಎರಡು ರೂಪಾಯಿ", 2),
    ("ಮೂರು ರೂಪಾಯಿ", 3),
    ("ನಾಲ್ಕು ರೂಪಾಯಿ", 4),
    ("ಐದು ರೂಪಾಯಿ", 5),
    ("ಆರು ರೂಪಾಯಿ", 6),
    ("ಏಳು ರೂಪಾಯಿ", 7),
    ("ಎಂಟು ರೂಪಾಯಿ", 8),
    ("ಒಂಬತ್ತು ರೂಪಾಯಿ", 9),
    ("ondu roopayi", 1),
    ("eradu roopayi", 2),
    ("mooru roopayi", 3),
    ("naalku roopayi", 4),
    ("aidu roopayi", 5),
    # --- Kannada teen words (10..19) ---
    ("ಹತ್ತು ರೂಪಾಯಿ", 10),
    ("ಹನ್ನೊಂದು ರೂಪಾಯಿ", 11),
    ("ಹನ್ನೆರಡು ರೂಪಾಯಿ", 12),
    ("ಹದಿಮೂರು ರೂಪಾಯಿ", 13),
    ("ಹದಿನಾಲ್ಕು ರೂಪಾಯಿ", 14),
    ("ಹದಿನೈದು ರೂಪಾಯಿ", 15),
    ("ಹದಿನಾರು ರೂಪಾಯಿ", 16),
    ("ಹದಿನೇಳು ರೂಪಾಯಿ", 17),
    ("ಹದಿನೆಂಟು ರೂಪಾಯಿ", 18),
    ("ಹತ್ತೊಂಬತ್ತು ರೂಪಾಯಿ", 19),
    ("hattu roopayi", 10),
    ("hannondu roopayi", 11),
    ("hadinaidu roopayi", 15),
    # --- Kannada tens (20..90) ---
    ("ಇಪ್ಪತ್ತು ರೂಪಾಯಿ", 20),
    ("ಮೂವತ್ತು ರೂಪಾಯಿ", 30),
    ("ನಲವತ್ತು ರೂಪಾಯಿ", 40),
    ("ಐವತ್ತು ರೂಪಾಯಿ", 50),
    ("ಅರವತ್ತು ರೂಪಾಯಿ", 60),
    ("ಎಪ್ಪತ್ತು ರೂಪಾಯಿ", 70),
    ("ಎಂಬತ್ತು ರೂಪಾಯಿ", 80),
    ("ತೊಂಬತ್ತು ರೂಪಾಯಿ", 90),
    ("ippattu roopayi", 20),
    ("moovattu roopayi", 30),
    ("nalavattu roopayi", 40),
    ("aivattu roopayi", 50),
    # --- Kannada fused compounds (tens + units) ---
    ("ಇಪ್ಪತ್ತೈದು", 25),
    ("ಮೂವತ್ತೈದು", 35),
    ("ನಲವತ್ತೈದು", 45),
    ("ಐವತ್ತೈದು", 55),
    ("ಅರವತ್ತೈದು", 65),
    ("ಎಪ್ಪತ್ತೈದು", 75),
    ("ಎಂಬತ್ತೈದು", 85),
    ("ತೊಂಬತ್ತೈದು", 95),
    ("ಇಪ್ಪತ್ತೆರಡು", 22),
    ("ಮೂವತ್ತೂರು", 33),
    ("ನಲವತ್ತನಾಲ್ಕು", 44),
    # --- Kannada scales & pre-multiplied hundreds ---
    ("ನೂರು", 100),
    ("ಇನ್ನೂರು", 200),
    ("ಮುನ್ನೂರು", 300),
    ("ನಾನೂರು", 400),
    ("ಐನೂರು", 500),
    ("ಆರುನೂರು", 600),
    ("ಏಳುನೂರು", 700),
    ("ಎಂಟುನೂರು", 800),
    ("ಒಂಬೈನೂರು", 900),
    ("nooru roopayi", 100),
    ("innooru roopayi", 200),
    ("munnooru roopayi", 300),
    ("ainooru roopayi", 500),
    ("ಸಾವಿರ", 1000),
    ("ಎರಡು ಸಾವಿರ", 2000),
    ("ಐದು ಸಾವಿರ", 5000),
    ("ಹತ್ತು ಸಾವಿರ", 10000),
    ("ಲಕ್ಷ", 100000),
    ("ಎರಡು ಲಕ್ಷ", 200000),
    ("ಕೋಟಿ", 10000000),
    ("saavira roopayi", 1000),
    ("laksha roopayi", 100000),
    # --- Conversational Kannada Kirana store phrases ---
    ("ರಮೇಶ್ ಗೆ ೩೪೦ ರೂಪಾಯಿ ಉದ್ಧರಿ ಬರೆ", 340),
    ("ಸುರೇಶ್ ೫೦೦ ರೂಪಾಯಿ ಜಮಾ ಕೊಟ್ಟರು", 500),
    ("ಮಂಜುನಾಥ್ ಖಾತೆಯಲ್ಲಿ ೨೫೦ ರೂಪಾಯಿ ಬರೆಯಿರಿ", 250),
    ("ಅರವಿಂದ್ ಗೆ ಮೂರು ನೂರು ರೂಪಾಯಿ ಉದ್ರಿ ಕೊಟ್ಟೆ", 300),
    ("ಮುನ್ನೂರು ರೂಪಾಯಿ", 300),
    # --- Ambiguous or invalid Kannada phrases (MUST return None) ---
    ("೩ ೪೦", None),
    ("೧.೫ ಸಾವಿರ", None),
    ("1.5 ಲಕ್ಷ", None),
    ("ಅರ್ಧ ರೂಪಾಯಿ", None),
    ("೫೦ ಪೈಸೆ", None),
    ("50 paise", None),
    ("ಯಾವುದೇ ರೂಪಾಯಿ ಇಲ್ಲ", None),
]


@pytest.mark.parametrize(("text", "expected"), KANNADA_NEEDS_REVIEW_CASES)
def test_kannada_numbers_needs_review(text: str, expected: int | None) -> None:
    assert parse_amount(text, lang_hint="kn") == expected
