"""Table-driven tests (50+ cases) for server.nlu.dates."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from server.nlu.dates import resolve_date

NOW_DATE = date(2026, 11, 18)
NOW_DT = datetime(2026, 11, 18, 17, 30, 0)

DATE_CASES: list[tuple[str, date | None]] = [
    # --- Today (Hindi / English / Telugu) ---
    ("aaj", date(2026, 11, 18)),
    ("आज", date(2026, 11, 18)),
    ("aaj Ramesh ne 200 rupaye diye", date(2026, 11, 18)),
    ("today", date(2026, 11, 18)),
    ("paid today", date(2026, 11, 18)),
    ("ఈరోజు", date(2026, 11, 18)),
    ("ఈ రోజు 300 రూపాయలు ఇచ్చారు", date(2026, 11, 18)),
    ("నేడు", date(2026, 11, 18)),
    ("eroju 250 iccharu", date(2026, 11, 18)),
    ("eeroju", date(2026, 11, 18)),
    # --- Explicit yesterday / tomorrow / day-before / day-after ---
    ("yesterday", date(2026, 11, 17)),
    ("tomorrow", date(2026, 11, 19)),
    ("day before yesterday", date(2026, 11, 16)),
    ("day after tomorrow", date(2026, 11, 20)),
    ("నిన్న", date(2026, 11, 17)),
    ("నిన్న రమేష్ 200 రూపాయలు తీసుకున్నాడు", date(2026, 11, 17)),
    ("ninna", date(2026, 11, 17)),
    ("రేపు", date(2026, 11, 19)),
    ("రేపు డబ్బులు ఇస్తాను", date(2026, 11, 19)),
    ("repu", date(2026, 11, 19)),
    ("మొన్న", date(2026, 11, 16)),
    ("monna", date(2026, 11, 16)),
    ("ఎల్లుండి", date(2026, 11, 20)),
    ("ellundi", date(2026, 11, 20)),
    # --- Hindi "kal" disambiguated by context verb (past vs future) ---
    ("kal Ramesh ne 200 rupaye diya tha", date(2026, 11, 17)),
    ("kal samaan le gaya tha", date(2026, 11, 17)),
    ("Ramesh ke khate mein kal 500 likha tha", date(2026, 11, 17)),
    ("कल पैसे लिए थे", date(2026, 11, 17)),
    ("कल उसने 300 रुपये दिया", date(2026, 11, 17)),
    ("kal Ramesh paid 150", date(2026, 11, 17)),
    ("kal paise dega", date(2026, 11, 19)),
    ("kal wapas kar dunga", date(2026, 11, 19)),
    ("kal aayega aur jama karega", date(2026, 11, 19)),
    ("कल पैसे देगा", date(2026, 11, 19)),
    ("कल मैं 400 रुपये दूंगा", date(2026, 11, 19)),
    ("kal tak de dega", date(2026, 11, 19)),
    ("kal he will pay", date(2026, 11, 19)),
    # --- Hindi "parso" disambiguated by context verb ---
    ("parso udhaar liya tha", date(2026, 11, 16)),
    ("परसों सामान खरीदा था", date(2026, 11, 16)),
    ("parso paise dega", date(2026, 11, 20)),
    ("परसों रुपये दूंगा", date(2026, 11, 20)),
    # --- Relative day offsets (future +N) ---
    ("5 din mein", date(2026, 11, 23)),
    ("5 din mein paise dega", date(2026, 11, 23)),
    ("Ramesh 200 rupaye 5 din mein dega", date(2026, 11, 23)),
    ("paanch din baad", date(2026, 11, 23)),
    ("पाँच दिन में", date(2026, 11, 23)),
    ("१० दिनों बाद", date(2026, 11, 28)),
    ("in 5 days", date(2026, 11, 23)),
    ("after three days", date(2026, 11, 21)),
    ("10 days later", date(2026, 11, 28)),
    ("5 రోజుల్లో", date(2026, 11, 23)),
    ("ఐదు రోజుల్లో ఇస్తాను", date(2026, 11, 23)),
    ("మూడు రోజుల తర్వాత", date(2026, 11, 21)),
    ("5 rojullo", date(2026, 11, 23)),
    ("aidu rojula tarvata", date(2026, 11, 23)),
    ("౫ రోజుల్లో", date(2026, 11, 23)),
    # --- Relative day offsets (past -N) ---
    ("do din pehle", date(2026, 11, 16)),
    ("2 din pahle", date(2026, 11, 16)),
    ("दो दिन पहले", date(2026, 11, 16)),
    ("पाँच दिनों पहले लिया था", date(2026, 11, 13)),
    ("2 days ago", date(2026, 11, 16)),
    ("five days before", date(2026, 11, 13)),
    ("రెండు రోజుల క్రితం", date(2026, 11, 16)),
    ("ఐదు రోజుల ముందు", date(2026, 11, 13)),
    ("rendu rojula kritam", date(2026, 11, 16)),
    ("2 rojula mundu", date(2026, 11, 16)),
    # --- Weeks ---
    ("last week", date(2026, 11, 11)),
    ("next week", date(2026, 11, 25)),
    ("pichle hafte", date(2026, 11, 11)),
    ("agle hafte", date(2026, 11, 25)),
    ("గత వారం", date(2026, 11, 11)),
    ("వచ్చే వారం", date(2026, 11, 25)),
    ("2 weeks ago", date(2026, 11, 4)),
    ("in 2 weeks", date(2026, 12, 2)),
    # --- Ambiguous / missing date MUST return None ---
    ("", None),
    ("Ramesh 340 rupaye", None),
    ("kal", None),
    ("कल", None),
    ("kal 200 rupaye", None),
    ("parso", None),
    ("परसों", None),
    ("kal liya tha aur kal dega", None),
    ("aaj ya tomorrow", None),
    ("3 40 din mein", None),
    ("1.5 din mein", None),
]


@pytest.mark.parametrize(("text", "expected"), DATE_CASES)
def test_resolve_date_table_driven(text: str, expected: date | None) -> None:
    assert len(DATE_CASES) >= 60
    assert resolve_date(text, NOW_DATE) == expected


def test_resolve_date_accepts_datetime_now() -> None:
    assert resolve_date("5 din mein", NOW_DT) == date(2026, 11, 23)
    assert resolve_date("do din pehle", NOW_DT) == date(2026, 11, 16)
