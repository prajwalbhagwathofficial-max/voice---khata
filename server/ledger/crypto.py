"""AES-GCM field encryption (96-bit random nonce) and cross-script phonetic keys."""

from __future__ import annotations

import base64
import os
import re
import unicodedata

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

NONCE_SIZE_BYTES = 12  # 96-bit nonce required for AES-GCM


class CryptoConfigError(ValueError):
    """Raised when LEDGER_ENC_KEY is missing or malformed."""


def generate_key_b64() -> str:
    """Generate a fresh 256-bit (32-byte) AES-GCM key encoded as URL-safe base64."""
    return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")


def decode_key(raw_key: str | bytes) -> bytes:
    """Decode an AES-GCM key (16, 24, or 32 bytes) from raw bytes, hex, or base64."""
    if isinstance(raw_key, bytes):
        key = raw_key
    else:
        cleaned = raw_key.strip()
        if not cleaned or cleaned == "replace_with_32_byte_base64_key":
            raise CryptoConfigError("LEDGER_ENC_KEY is not configured")
        key = b""
        # Try hex first if length matches 32/48/64 hex chars
        if len(cleaned) in (32, 48, 64) and all(c in "0123456789abcdefABCDEF" for c in cleaned):
            key = bytes.fromhex(cleaned)
        else:
            padded = cleaned + "=" * (-len(cleaned) % 4)
            try:
                key = base64.urlsafe_b64decode(padded.encode("ascii"))
            except Exception as exc:
                raise CryptoConfigError("LEDGER_ENC_KEY must be valid base64 or hex") from exc

    if len(key) not in (16, 24, 32):
        raise CryptoConfigError(
            f"LEDGER_ENC_KEY must decode to 16, 24, or 32 bytes (got {len(key)})"
        )
    return key


def key_from_env(env_var: str = "LEDGER_ENC_KEY") -> bytes:
    """Load AES-GCM key from the process environment."""
    raw = os.environ.get(env_var, "")
    return decode_key(raw)


class FieldCipher:
    """Encrypts and decrypts UTF-8 strings using AES-GCM with a random 96-bit nonce per field."""

    def __init__(self, key: bytes | str) -> None:
        self._aesgcm = AESGCM(decode_key(key))

    def encrypt_str(self, plaintext: str, *, aad: bytes | None = None) -> bytes:
        nonce = os.urandom(NONCE_SIZE_BYTES)
        ct = self._aesgcm.encrypt(nonce, plaintext.encode("utf-8"), aad)
        return nonce + ct

    def decrypt_str(self, blob: bytes, *, aad: bytes | None = None) -> str:
        if len(blob) <= NONCE_SIZE_BYTES:
            raise ValueError("Encrypted blob is too short")
        nonce = blob[:NONCE_SIZE_BYTES]
        ct = blob[NONCE_SIZE_BYTES:]
        return self._aesgcm.decrypt(nonce, ct, aad).decode("utf-8")

    def encrypt_optional(self, plaintext: str | None, *, aad: bytes | None = None) -> bytes | None:
        if plaintext is None:
            return None
        return self.encrypt_str(plaintext, aad=aad)

    def decrypt_optional(self, blob: bytes | None, *, aad: bytes | None = None) -> str | None:
        if blob is None:
            return None
        return self.decrypt_str(blob, aad=aad)

    def encrypt_bytes(self, data: bytes, *, aad: bytes | None = None) -> bytes:
        nonce = os.urandom(NONCE_SIZE_BYTES)
        ct = self._aesgcm.encrypt(nonce, data, aad)
        return nonce + ct

    def decrypt_bytes(self, blob: bytes, *, aad: bytes | None = None) -> bytes:
        if len(blob) <= NONCE_SIZE_BYTES:
            raise ValueError("Encrypted blob is too short")
        nonce = blob[:NONCE_SIZE_BYTES]
        ct = blob[NONCE_SIZE_BYTES:]
        return self._aesgcm.decrypt(nonce, ct, aad)


# --- Script detection ---
def detect_script(text: str) -> str:
    """Classify the dominant writing script of a customer name."""
    has_lat = False
    has_dev = False
    has_tel = False
    has_kan = False
    for ch in text:
        code = ord(ch)
        if 0x0900 <= code <= 0x097F:
            has_dev = True
        elif 0x0C00 <= code <= 0x0C7F:
            has_tel = True
        elif 0x0C80 <= code <= 0x0CFF:
            has_kan = True
        elif ("A" <= ch <= "Z") or ("a" <= ch <= "z"):
            has_lat = True

    scripts = [
        name
        for name, flag in (
            ("latin", has_lat),
            ("devanagari", has_dev),
            ("telugu", has_tel),
            ("kannada", has_kan),
        )
        if flag
    ]
    if not scripts:
        return "other"
    if len(scripts) == 1:
        return scripts[0]
    return "mixed"


# --- Cross-script Indic phonetic key (Soundex-style, never plaintext) ---
_DEVANAGARI_TO_LATIN: dict[str, str] = {
    # Vowels & vowel signs
    "अ": "a", "आ": "a", "इ": "i", "ई": "i", "उ": "u", "ऊ": "u", "ऋ": "ri",
    "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au",
    "ा": "a", "ि": "i", "ी": "i", "ु": "u", "ू": "u", "ृ": "ri",
    "े": "e", "ै": "ai", "ो": "o", "ौ": "au", "ं": "n", "ँ": "n", "ः": "h",
    "्": "", "़": "",
    # Consonants
    "क": "k", "ख": "k", "ग": "g", "घ": "g", "ङ": "n",
    "च": "c", "छ": "c", "ज": "j", "झ": "j", "ञ": "n",
    "ट": "t", "ठ": "t", "ड": "d", "ढ": "d", "ण": "n",
    "त": "t", "थ": "t", "द": "d", "ध": "d", "न": "n",
    "प": "p", "फ": "f", "ब": "b", "भ": "b", "म": "m",
    "य": "y", "र": "r", "ल": "l", "व": "v",
    "श": "s", "ष": "s", "स": "s", "ह": "h",
    "ड़": "r", "ढ़": "r", "क़": "k", "ख़": "k", "ग़": "g", "ज़": "z", "फ़": "f",
}

_TELUGU_TO_LATIN: dict[str, str] = {
    # Vowels & vowel signs
    "అ": "a", "ఆ": "a", "ఇ": "i", "ఈ": "i", "ఉ": "u", "ఊ": "u", "ఋ": "ri",
    "ఎ": "e", "ఏ": "e", "ఐ": "ai", "ఒ": "o", "ఓ": "o", "ఔ": "au",
    "ా": "a", "ి": "i", "ీ": "i", "ు": "u", "ూ": "u", "ృ": "ri",
    "ె": "e", "ే": "e", "ై": "ai", "ొ": "o", "ో": "o", "ౌ": "au",
    "ం": "n", "ః": "h", "్": "",
    # Consonants
    "క": "k", "ఖ": "k", "గ": "g", "ఘ": "g", "ఙ": "n",
    "చ": "c", "ఛ": "c", "జ": "j", "ఝ": "j", "ఞ": "n",
    "ట": "t", "ఠ": "t", "డ": "d", "ఢ": "d", "ణ": "n",
    "త": "t", "థ": "t", "ద": "d", "ధ": "d", "న": "n",
    "ప": "p", "ఫ": "f", "బ": "b", "భ": "b", "మ": "m",
    "య": "y", "ర": "r", "ఱ": "r", "ల": "l", "ಳ": "l", "వ": "v",
    "శ": "s", "ష": "s", "స": "s", "హ": "h",
}

_KANNADA_TO_LATIN: dict[str, str] = {
    # Vowels & vowel signs
    "ಅ": "a", "ಆ": "a", "ಇ": "i", "ಈ": "i", "ಉ": "u", "ಊ": "u", "ಋ": "ri",
    "ಎ": "e", "ಏ": "e", "ಐ": "ai", "ಒ": "o", "ಓ": "o", "ಔ": "au",
    "ಾ": "a", "ಿ": "i", "ೀ": "i", "ು": "u", "ೂ": "u", "ೃ": "ri",
    "ೆ": "e", "ೇ": "e", "ೈ": "ai", "ೊ": "o", "ೋ": "o", "ೌ": "au",
    "ಂ": "n", "ಃ": "h", "್": "",
    # Consonants
    "ಕ": "k", "ಖ": "k", "ಗ": "g", "ಘ": "g", "ಙ": "n",
    "ಚ": "c", "ಛ": "c", "ಜ": "j", "ಝ": "j", "ಞ": "n",
    "ಟ": "t", "ಠ": "t", "ಡ": "d", "ಢ": "d", "ಣ": "n",
    "ತ": "t", "ಥ": "t", "ದ": "d", "ಧ": "d", "ನ": "n",
    "ಪ": "p", "ಫ": "f", "ಬ": "b", "ಭ": "b", "ಮ": "m",
    "ಯ": "y", "ರ": "r", "ಱ": "r", "ಲ": "l", "ಳ": "l", "ವ": "v",
    "ಶ": "s", "ಷ": "s", "ಸ": "s", "ಹ": "h",
}

_DIGRAPH_SUBS: tuple[tuple[str, str], ...] = (
    ("ksh", "ks"),
    ("sh", "s"),
    ("ch", "c"),
    ("ph", "f"),
    ("bh", "b"),
    ("th", "t"),
    ("dh", "d"),
    ("kh", "k"),
    ("gh", "g"),
    ("jh", "j"),
    ("w", "v"),
)

# Soundex-like phonetic equivalence classes tuned for Indian names
_SOUNDEX_MAP: dict[str, str] = {
    "b": "1", "f": "1", "p": "1", "v": "1",
    "c": "2", "g": "2", "j": "2", "k": "2", "q": "2", "s": "2", "x": "2", "z": "2",
    "d": "3", "t": "3",
    "l": "4",
    "m": "5", "n": "5",
    "r": "6",
}

_WORD_SPLIT_RE = re.compile(r"[^\w]+", re.UNICODE)


def _transliterate_to_latin(text: str) -> str:
    nfc = unicodedata.normalize("NFC", text)
    out: list[str] = []
    for ch in nfc:
        if ch in _DEVANAGARI_TO_LATIN:
            out.append(_DEVANAGARI_TO_LATIN[ch])
        elif ch in _TELUGU_TO_LATIN:
            out.append(_TELUGU_TO_LATIN[ch])
        elif ch in _KANNADA_TO_LATIN:
            out.append(_KANNADA_TO_LATIN[ch])
        elif ch.isascii():
            out.append(ch.lower())
        elif ch.isspace():
            out.append(" ")
    return "".join(out)


def _phonetic_token(latin_word: str) -> str:
    word = re.sub(r"[^a-z]", "", latin_word.lower())
    if not word:
        return ""
    for src, dst in _DIGRAPH_SUBS:
        word = word.replace(src, dst)

    lead = word[0].upper()
    lead_code = _SOUNDEX_MAP.get(word[0], "")
    digits: list[str] = []
    prev_code = lead_code
    for ch in word[1:]:
        code = _SOUNDEX_MAP.get(ch, "")
        if code and code != prev_code:
            digits.append(code)
            if len(digits) == 3:
                break
        prev_code = code

    return (lead + "".join(digits)).ljust(4, "0")


def phonetic_key(name: str) -> str:
    """Compute a non-plaintext cross-script phonetic key for a customer name.

    Maps Latin, Devanagari, and Telugu spellings of the same name to the same
    Soundex-style key (e.g., 'Ramesh', 'रमेश', 'రమేష్' -> 'R520').
    """
    latin = _transliterate_to_latin(name)
    parts = [p for p in _WORD_SPLIT_RE.split(latin) if p]
    codes = [_phonetic_token(part) for part in parts]
    return " ".join(c for c in codes if c)
