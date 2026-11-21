"""Tests for voice_khata.config."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from voice_khata.config import Settings


def test_settings_defaults_enforce_prisma_and_timbre_v3(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GNANI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    cfg = Settings(_env_file=None)
    assert cfg.gnani_stt_model == "prisma-v3.0"
    assert cfg.gnani_tts_model == "timbre-v3.0"
    assert cfg.gnani_tts_voice == "Shivani"
    assert cfg.has_gnani_key() is False
    assert cfg.has_gemini_key() is False


def test_settings_masks_secrets_in_repr_and_str() -> None:
    cfg = Settings(
        _env_file=None,
        gnani_api_key="gnani-super-secret-123",
        gemini_api_key="gemini-super-secret-456",
    )
    rendered = f"{cfg!r} {cfg}"
    assert "gnani-super-secret-123" not in rendered
    assert "gemini-super-secret-456" not in rendered
    assert cfg.secret_values() == ("gnani-super-secret-123", "gemini-super-secret-456")
    assert cfg.has_gnani_key() is True
    assert cfg.has_gemini_key() is True


def test_settings_rejects_non_v3_speech_models() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, gnani_stt_model="prisma-v2.5")  # type: ignore[arg-type]

    with pytest.raises(ValidationError):
        Settings(_env_file=None, gnani_tts_model="timbre-v2.5")  # type: ignore[arg-type]


def test_settings_rejects_non_stock_or_cloned_voice() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, gnani_tts_voice="custom_cloned_voice_01")


def test_settings_rejects_non_websocket_speech_urls() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, gnani_stt_url="https://api.vachana.ai/stt/v3")
