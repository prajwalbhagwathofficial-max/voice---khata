"""Application settings loaded strictly from .env with secret protection."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

TIMBRE_V3_VOICES: frozenset[str] = frozenset(
    {
        "Shivani",
        "Avinash",
        "Lakshmi",
        "Kalpana",
        "Chetana",
        "Vasudha",
        "Riyaa",
        "Tarika",
        "Shubham",
        "Prakash",
        "Devansh",
        "Yashraj",
    }
)


class Settings(BaseSettings):
    """Runtime configuration for Voice Khata.

    API keys are stored as SecretStr so they are never serialized or printed
    in logs or tracebacks.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    gnani_api_key: SecretStr = Field(default=SecretStr(""))
    gnani_stt_url: str = Field(default="wss://api.vachana.ai/stt/v3/stream")
    gnani_stt_model: Literal["prisma-v3.0"] = Field(default="prisma-v3.0")
    gnani_tts_url: str = Field(default="wss://api.vachana.ai/api/v1/tts")
    gnani_tts_model: Literal["timbre-v3.0"] = Field(default="timbre-v3.0")
    gnani_tts_voice: str = Field(default="Shivani")
    gnani_tts_sample_rate: int = Field(default=48000, ge=8000, le=48000)

    gemini_api_key: SecretStr = Field(default=SecretStr(""))
    gemini_model: str = Field(default="gemini-3.5-flash-lite")

    voice_khata_db_path: Path = Field(default=Path("data/voice_khata.db"))
    voice_khata_host: str = Field(default="127.0.0.1")
    voice_khata_port: int = Field(default=8000, ge=1, le=65535)
    voice_khata_log_level: str = Field(default="INFO")

    @field_validator("gnani_stt_url", "gnani_tts_url")
    @classmethod
    def _validate_ws_url(cls, value: str) -> str:
        if not (value.startswith("wss://") or value.startswith("ws://")):
            raise ValueError("Speech service URLs must use ws:// or wss:// WebSocket scheme")
        return value

    @field_validator("gnani_tts_voice")
    @classmethod
    def _validate_stock_voice(cls, value: str) -> str:
        if value not in TIMBRE_V3_VOICES:
            allowed = ", ".join(sorted(TIMBRE_V3_VOICES))
            raise ValueError(
                f"Voice {value!r} is not an approved Timbre v3.0 stock voice. Allowed: {allowed}"
            )
        return value

    def secret_values(self) -> tuple[str, ...]:
        """Return non-empty raw secrets for log redaction registration."""
        secrets: list[str] = []
        for secret in (self.gnani_api_key, self.gemini_api_key):
            raw = secret.get_secret_value().strip()
            if raw:
                secrets.append(raw)
        return tuple(secrets)

    def has_gnani_key(self) -> bool:
        val = self.gnani_api_key.get_secret_value().strip()
        return bool(val) and val != "your_gnani_api_key_here"

    def has_gemini_key(self) -> bool:
        val = self.gemini_api_key.get_secret_value().strip()
        return bool(val) and val != "your_gemini_api_key_here"
