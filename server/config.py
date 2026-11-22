"""
Configuration & Environment Variable Management for Voice Khata
"""

import os
import hashlib
import logging
from typing import Optional
from dotenv import load_dotenv

# Load .env file if present
load_dotenv()

logger = logging.getLogger("voice_khata.config")

def parse_bool(val: Optional[str], default: bool = False) -> bool:
    if val is None:
        return default
    return str(val).strip().lower() in ("true", "1", "yes", "on")

def parse_int(val: Optional[str], default: int) -> int:
    if val is None:
        return default
    try:
        return int(str(val).strip())
    except (ValueError, TypeError):
        return default

# ─── Environment Variables ──────────────────────────────────────────────────
DATA_DIR = os.getenv("DATA_DIR", "./data")
USE_MOCKS = parse_bool(os.getenv("USE_MOCKS"), default=False)
STT_MODE = os.getenv("STT_MODE", "transcribe")
CORRECTION_TTL_SECONDS = parse_int(os.getenv("CORRECTION_TTL_SECONDS"), default=30)
AMOUNT_CONFIRM_THRESHOLD_PAISE = parse_int(os.getenv("AMOUNT_CONFIRM_THRESHOLD_PAISE"), default=500000)
TRANSCRIPT_TTL_HOURS = parse_int(os.getenv("TRANSCRIPT_TTL_HOURS"), default=24)
SESSION_IDLE_MINUTES = parse_int(os.getenv("SESSION_IDLE_MINUTES"), default=15)
FEATURE_BILLS = parse_bool(os.getenv("FEATURE_BILLS"), default=False)
FEATURE_SHARE = parse_bool(os.getenv("FEATURE_SHARE"), default=False)

GNANI_API_KEY = os.getenv("GNANI_API_KEY", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
DB_ENCRYPTION_KEY = os.getenv("DB_ENCRYPTION_KEY", "").strip()
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "").strip()
ACCESS_CODE = os.getenv("ACCESS_CODE", "").strip()

def get_key_fingerprint(key: str) -> str:
    """Return an 8-character hex fingerprint of the key without leaking contents."""
    if not key:
        return "none"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]

def validate_config():
    """
    Fail fast if required secrets are missing, unless USE_MOCKS is True.
    Never print secret values in logs or exception messages.
    """
    if USE_MOCKS:
        logger.info("Running in USE_MOCKS=true mode. Secret validation skipped.")
        return

    required = {
        "GNANI_API_KEY": GNANI_API_KEY,
        "GROQ_API_KEY": GROQ_API_KEY,
        "DB_ENCRYPTION_KEY": DB_ENCRYPTION_KEY,
        "ADMIN_TOKEN": ADMIN_TOKEN,
    }

    missing = [name for name, val in required.items() if not val]
    if missing:
        error_msg = f"FATAL Configuration Error: Missing required secrets: {', '.join(missing)}. Set them in environment or set USE_MOCKS=true."
        logger.critical(error_msg)
        raise RuntimeError(error_msg)

    # Log only key fingerprint for observability
    fp = get_key_fingerprint(DB_ENCRYPTION_KEY)
    logger.info(f"Configuration valid. DB_ENCRYPTION_KEY fingerprint: {fp} (STT_MODE={STT_MODE})")
