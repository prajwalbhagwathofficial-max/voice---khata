"""Unified server configuration loaded strictly from environment / .env with SecretStr."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from voice_khata.config import TIMBRE_V3_VOICES

_PLACEHOLDER_PREFIXES = ("your_", "replace_with_")


def _is_configured(secret: SecretStr) -> bool:
    val = secret.get_secret_value().strip()
    if not val:
        return False
    return not any(val.startswith(prefix) for prefix in _PLACEHOLDER_PREFIXES)


class ServerSettings(BaseSettings):
    """Runtime settings for the single-origin Voice Khata Render/Docker service."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    use_mocks: bool = Field(default=False)

    gnani_api_key: SecretStr = Field(default=SecretStr(""))
    gnani_stt_url: str = Field(default="wss://api.vachana.ai/stt/v3/stream")
    gnani_stt_model: Literal["prisma-v3.0"] = Field(default="prisma-v3.0")
    gnani_tts_url: str = Field(default="wss://api.vachana.ai/api/v1/tts")
    gnani_tts_model: Literal["timbre-v3.0"] = Field(default="timbre-v3.0")
    gnani_tts_voice: str = Field(default="Shivani")
    gnani_tts_sample_rate: int = Field(default=48000, ge=8000, le=48000)

    groq_api_key: SecretStr = Field(default=SecretStr(""))
    groq_model: str = Field(default="qwen/qwen3.8-27b")

    gemini_api_key: SecretStr = Field(default=SecretStr(""))
    gemini_model: str = Field(default="gemini-3.5-flash-lite")

    data_dir: Path = Field(default=Path("./data"))
    ledger_db_path: Path | None = Field(default=None)
    db_encryption_key: SecretStr = Field(default=SecretStr(""))
    ledger_enc_key: SecretStr = Field(default=SecretStr(""))
    admin_token: SecretStr = Field(default=SecretStr(""))
    access_code: SecretStr = Field(default=SecretStr(""))

    stt_mode: str = Field(default="transcribe")
    correction_ttl_seconds: int = Field(default=300)
    amount_confirm_threshold_paise: int = Field(default=500000)
    transcript_ttl_hours: int = Field(default=24)
    session_idle_minutes: int = Field(default=15)
    feature_bills: bool = Field(default=False)
    feature_share: bool = Field(default=False)

    voice_khata_host: str = Field(default="0.0.0.0")
    voice_khata_port: int = Field(default=8000, ge=1, le=65535)
    voice_khata_log_level: str = Field(default="INFO")
    shop_name: str = Field(default="Voice Khata Kirana Store")

    @field_validator("gnani_stt_url", "gnani_tts_url")
    @classmethod
    def _validate_ws_url(cls, value: str) -> str:
        if not (value.startswith("wss://") or value.startswith("ws://")):
            raise ValueError("Speech URLs must use ws:// or wss:// scheme")
        return value

    @field_validator("gnani_tts_voice")
    @classmethod
    def _validate_stock_voice(cls, value: str) -> str:
        if value not in TIMBRE_V3_VOICES:
            raise ValueError(f"Unsupported Timbre v3.0 voice: {value!r}")
        return value

    def effective_db_encryption_key(self) -> str:
        if _is_configured(self.db_encryption_key):
            return self.db_encryption_key.get_secret_value().strip()
        if _is_configured(self.ledger_enc_key):
            return self.ledger_enc_key.get_secret_value().strip()
        return ""

    def db_encryption_key_fingerprint(self) -> str:
        key = self.effective_db_encryption_key()
        if not key:
            return "none"
        import hashlib
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]

    def resolved_db_path(self) -> Path:
        if self.ledger_db_path is not None:
            return self.ledger_db_path
        return self.data_dir / "ledger.db"

    def has_gnani_key(self) -> bool:
        return _is_configured(self.gnani_api_key)

    def has_groq_key(self) -> bool:
        return _is_configured(self.groq_api_key)

    def has_gemini_key(self) -> bool:
        return _is_configured(self.gemini_api_key)

    def has_db_encryption_key(self) -> bool:
        return bool(self.effective_db_encryption_key())

    def has_admin_token(self) -> bool:
        return _is_configured(self.admin_token)

    def has_access_code(self) -> bool:
        return _is_configured(self.access_code)

    def validate_required_secrets(self) -> None:
        """Fail fast on startup if mandatory secrets are missing (unless USE_MOCKS=true)."""
        if self.use_mocks:
            return
        missing: list[str] = []
        if not self.has_gnani_key():
            missing.append("GNANI_API_KEY")
        if not self.has_groq_key():
            missing.append("GROQ_API_KEY")
        if not self.has_db_encryption_key():
            missing.append("DB_ENCRYPTION_KEY")
        if not self.has_admin_token():
            missing.append("ADMIN_TOKEN")
        if missing:
            joined = ", ".join(missing)
            raise RuntimeError(
                f"Startup aborted: missing required environment variable(s): {joined}. "
                "Set them in the environment/.env or enable USE_MOCKS=true."
            )

    def secret_values(self) -> tuple[str, ...]:
        vals: list[str] = []
        for s in (
            self.gnani_api_key,
            self.groq_api_key,
            self.gemini_api_key,
            self.db_encryption_key,
            self.ledger_enc_key,
            self.admin_token,
            self.access_code,
        ):
            raw = s.get_secret_value().strip()
            if raw:
                vals.append(raw)
        return tuple(vals)
