"""Tests for voice_khata.logging_utils."""

from __future__ import annotations

import io
import json
import logging

from voice_khata.logging_utils import REDACTED, configure_logging, log_event


def test_structured_json_logging_emits_monotonic_ns_and_redacts_secrets() -> None:
    buf = io.StringIO()
    secret = "sk-test-secret-xyz-999"
    logger = configure_logging(level="DEBUG", secrets=(secret,), stream=buf)

    mono_ns = log_event(
        logger,
        "test.secret_check",
        session_id="sess1",
        api_key="raw-key-should-hide",
        headers={"X-API-Key-ID": secret, "model": "prisma-v3.0"},
        note=f"leaked {secret} inside message",
    )

    line = buf.getvalue().strip()
    parsed = json.loads(line)
    assert parsed["event"] == "test.secret_check"
    assert parsed["ts_mono_ns"] == mono_ns
    assert isinstance(parsed["ts_mono_ns"], int)
    assert parsed["api_key"] == REDACTED
    assert parsed["headers"]["X-API-Key-ID"] == REDACTED
    assert parsed["headers"]["model"] == "prisma-v3.0"
    assert secret not in line
    assert REDACTED in parsed["note"]


def test_exception_tracebacks_also_redact_secrets() -> None:
    buf = io.StringIO()
    secret = "gnani-top-secret-token"
    logger = configure_logging(level="ERROR", secrets=(secret,), stream=buf)

    try:
        raise RuntimeError(f"auth failed for {secret}")
    except RuntimeError:
        logger.exception("boom")

    raw = buf.getvalue()
    assert secret not in raw
    parsed = json.loads(raw.strip())
    assert REDACTED in parsed["exception"]
