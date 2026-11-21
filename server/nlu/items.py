"""Extract itemised bill items from voice/text transcripts.

Rule-based extraction first, Groq fallback second (names/qty only, strict JSON, 400ms timeout).
Item AMOUNTS always come from server.nlu.numbers.parse_amount, never from an LLM.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from server.ledger.models import EntryItemInput
from server.nlu.numbers import parse_amount

logger = logging.getLogger("voice_khata.server.nlu.items")

# Common units for items in Indian kirana stores
_UNIT_WORDS = {
    "kilo", "kilos", "kg", "kgs", "gram", "grams", "gm", "gms", "g",
    "packet", "packets", "pkt", "pkts", "litre", "litres", "liter", "liters", "l",
    "darjan", "dozen", "dabba", "dappe", "piece", "pieces", "pcs", "pc",
    "bottle", "bottles", "bori", "tin", "tins", "pouch", "pouches",
}

# Quantity numbers/words
_QTY_NUMS = {
    "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "15", "20", "24", "25", "50",
    "ek", "do", "teen", "char", "chaar", "paanch", "che", "chhah", "saat", "aath", "nau", "das",
    "aadha", "dhai", "dedh", "half", "one", "two", "three", "four", "five", "six", "seven",
    "eight", "nine", "ten", "dozen",
}

# Stopwords that belong to header/transaction intent, not items
_HEADER_STOPWORDS = {
    "udhar", "udhaar", "उधार", "khata", "likh", "likho", "likh do", "jama", "जमा",
    "credit", "debit", "paid", "payment", "mila", "mile", "diya", "diye", "rupaye",
    "rupees", "ka", "ke", "ki", "ko", "ne", "aur", "and",
}


# Common number words for multi-word amounts like 'ek sau bees'
_NUM_WORDS = {
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
    "nineteen", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety",
    "hundred", "thousand", "lakh", "sau", "hazaar", "hazar", "ek", "do", "teen", "char",
    "chaar", "paanch", "che", "saat", "aath", "nau", "das", "gyarah", "barah", "terah",
    "chaudah", "pandrah", "solah", "satrah", "atharah", "unnis", "bees", "pachas", "sau",
    "dhai", "dedh", "aadha", "paune", "saade", "sawa",
}


def _split_into_clauses(text: str) -> list[str]:
    """Split transcript into clauses by commas, semicolons, and connecting words."""
    # Separate trailing total/header indicators like 'kul 200', 'total 200'
    text = re.sub(r"\b(kul|total|sab milakar|mothamu|ottu)\b", r", \1", text, flags=re.IGNORECASE)
    raw_clauses = re.split(r"[,;।\n]+", text)
    clauses: list[str] = []
    for c in raw_clauses:
        sub = re.split(r"\s+(?:aur|and|మరియు|ಮತ್ತು)\s+", c.strip())
        for s in sub:
            cleaned = s.strip()
            if cleaned:
                clauses.append(cleaned)
    return clauses


def _is_header_clause(clause: str, stated_amount: int | None) -> bool:
    """Check if this clause is the main transaction header (e.g. 'Ramesh ka 340 udhaar')."""
    lower = clause.casefold()
    if any(k in lower for k in ("kul", "total", "summary", "mothamu", "ottu", "sab milakar")):
        return True
    has_dir = any(k in lower for k in ("udhaar", "udhar", "khata", "likh", "jama", "credit", "debit"))
    if stated_amount is not None:
        amt = parse_amount(clause)
        if amt == stated_amount and has_dir:
            return True
    return has_dir and (" ka " in f" {lower} " or " ke " in f" {lower} " or " ne " in f" {lower} " or " ko " in f" {lower} ")


def _extract_amount_suffix(tokens: list[str]) -> tuple[int | None, int | None]:
    """Find the start index of the amount suffix in tokens, returning (amount, amount_start_idx)."""
    if not tokens:
        return None, None

    # Check 1: single token at the end is a clean number / currency
    last_val = parse_amount(tokens[-1])
    if last_val is not None and last_val > 0:
        # Check if previous tokens are part of a multi-word number like 'ek sau' before 'bees'
        idx = len(tokens) - 1
        while idx > 0 and tokens[idx - 1].lower().strip(".,") in _NUM_WORDS:
            cand = " ".join(tokens[idx - 1:])
            val = parse_amount(cand)
            if val is not None and val > 0:
                last_val = val
                idx -= 1
            else:
                break
        return last_val, idx

    # Check 2: ends with 'rupaye' / 'rupees' / '/-'
    if len(tokens) >= 2 and tokens[-1].lower().strip(".,") in {"rupaye", "rupees", "rs", "₹", "/-"}:
        val = parse_amount(tokens[-2])
        if val is not None and val > 0:
            idx = len(tokens) - 2
            while idx > 0 and tokens[idx - 1].lower().strip(".,") in _NUM_WORDS:
                cand = " ".join(tokens[idx - 1:-1])
                cand_val = parse_amount(cand)
                if cand_val is not None and cand_val > 0:
                    val = cand_val
                    idx -= 1
                else:
                    break
            return val, idx

    # Check 3: multi-word number at suffix
    for n in range(min(4, len(tokens)), 0, -1):
        sub_tokens = [t.lower().strip(".,") for t in tokens[-n:]]
        if all(w.isdigit() or w in _NUM_WORDS for w in sub_tokens):
            val = parse_amount(" ".join(tokens[-n:]))
            if val is not None and val > 0:
                return val, len(tokens) - n

    return None, None


def _extract_single_item(clause: str) -> EntryItemInput | None:
    """Extract item name, quantity, and amount from a single clause like '2 kilo chawal 120' or 'tel 220'."""
    tokens = clause.strip().split()
    if not tokens:
        return None

    amount, amount_start_idx = _extract_amount_suffix(tokens)
    if amount is None or amount_start_idx is None or amount_start_idx == 0:
        return None

    prefix_tokens = tokens[:amount_start_idx]
    if not prefix_tokens:
        return None

    # Strip leading header postpositions like 'Ramesh ko', 'Suresh ke', etc.
    name_tokens = list(prefix_tokens)
    while len(name_tokens) >= 2 and name_tokens[1].lower().strip(".,") in {"ko", "ka", "ke", "ki", "ne"}:
        name_tokens = name_tokens[2:]

    # Extract quantity from the start of prefix_tokens if present
    qty_text: str | None = None

    if len(name_tokens) >= 2:
        t0 = name_tokens[0].lower().strip(".,")
        t1 = name_tokens[1].lower().strip(".,")
        if (t0.isdigit() or t0 in _QTY_NUMS) and t1 in _UNIT_WORDS:
            qty_text = f"{name_tokens[0]} {name_tokens[1]}"
            name_tokens = name_tokens[2:]
    elif len(name_tokens) >= 1:
        t0 = name_tokens[0].lower().strip(".,")
        if t0 in {"aadha", "dhai", "dedh", "half", "dozen"}:
            qty_text = name_tokens[0]
            name_tokens = name_tokens[1:]

    name = " ".join(name_tokens).strip(".,!?:- ")
    name_clean = " ".join(w for w in name.split() if w.lower() not in _HEADER_STOPWORDS).strip()
    if not name_clean:
        name_clean = name

    if not name_clean:
        return None

    return EntryItemInput(name=name_clean, qty_text=qty_text, amount_inr=amount)


def extract_items_rule_based(transcript: str, stated_amount: int | None = None) -> list[EntryItemInput]:
    """Extract item entries from transcript using deterministic regex and numbers.py."""
    clauses = _split_into_clauses(transcript)
    items: list[EntryItemInput] = []

    for c in clauses:
        if _is_header_clause(c, stated_amount):
            continue
        item = _extract_single_item(c)
        if item is not None:
            items.append(item)

    # If no items found from comma-delimited clauses, check inline itemisation
    # e.g. "Ramesh ka 340 udhaar 2 kilo chawal 120 tel 220"
    if not items:
        # Strip header part (up to 'udhaar', 'jama', etc.)
        match = re.search(r"\b(udhaar|udhar|jama|likho|likh do|credit|debit)\b", transcript, re.IGNORECASE)
        if match:
            rest = transcript[match.end():].strip()
            # Split rest on pattern: boundary after a number when followed by a non-digit token
            units = r"(?:kilo|kg|gram|gm|g|litre|ltr|l|darjan|dozen|packet|pkt|pouch|cup|glass|botal|bottle)"
            marked = re.sub(
                rf"(\b\d+\b)(?!\s+{units}\b)\s+([A-Za-z\u0900-\u097F\u0C00-\u0C7F])",
                r"\1 | \2",
                rest,
                flags=re.IGNORECASE,
            )
            sub_chunks = [sc.strip() for sc in marked.split("|") if sc.strip()]
            for chunk in sub_chunks:
                item = _extract_single_item(chunk)
                if item is not None:
                    items.append(item)

    return items


async def extract_items_with_groq_fallback(
    transcript: str,
    stated_amount: int | None = None,
    *,
    groq_api_key: str | None = None,
    groq_model: str = "llama-3.1-8b-instant",
) -> list[EntryItemInput]:
    """Extract items using rules first, falling back to Groq JSON mode (names & qty only, 400ms timeout).
    
    Item AMOUNTS always come from nlu/numbers.py, never the LLM.
    """
    rule_items = extract_items_rule_based(transcript, stated_amount)
    if rule_items:
        return rule_items

    # If no items detected or API key missing, return empty
    if not groq_api_key:
        return []

    try:
        import httpx

        # Groq strict JSON schema extraction for NAMES and QTY ONLY (amounts strictly forbidden)
        prompt = (
            "Extract any shopkeeper bill items from the following utterance.\n"
            "Utterance: " + transcript + "\n\n"
            "Return JSON matching: {\"items\": [{\"name\": \"item name\", \"qty_text\": \"qty or null\"}]}.\n"
            "Do NOT extract amounts or prices."
        )

        async with httpx.AsyncClient(timeout=0.4) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {groq_api_key.strip()}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": groq_model,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are a kirana store parser. Extract item names and quantities only. "
                                "Never extract amounts. Return strictly valid JSON."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.0,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                raw_content = data["choices"][0]["message"]["content"]
                parsed = json.loads(raw_content)
                extracted_candidates = parsed.get("items", [])
                
                # Now match each item back to numbers.py to strictly determine amounts!
                final_items: list[EntryItemInput] = []
                for cand in extracted_candidates:
                    c_name = cand.get("name")
                    c_qty = cand.get("qty_text")
                    if not c_name:
                        continue
                    # Search for this item name in transcript and extract number following it
                    pattern = rf"{re.escape(c_name)}.*?(\d+|[\u0966-\u096F]+|[a-zA-Z\u0900-\u097F]+(?:\s+[a-zA-Z\u0900-\u097F]+)*)"
                    m = re.search(pattern, transcript, re.IGNORECASE)
                    if m:
                        candidate_num_str = transcript[m.end(0) - len(m.group(1)): m.end(0) + 20]
                        amt = parse_amount(candidate_num_str)
                        if amt is not None and amt > 0:
                            final_items.append(
                                EntryItemInput(name=c_name, qty_text=c_qty, amount_inr=amt)
                            )
                if final_items:
                    return final_items
    except Exception as exc:
        logger.debug("Groq item extraction fallback skipped or timed out: %s", exc)

    return rule_items
