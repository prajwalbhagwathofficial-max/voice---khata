"""Structured JSON logging with monotonic timestamps and secret redaction."""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime, timezone
from typing import Any

REDACTED = "[REDACTED]"

SENSITIVE_KEY_TOKENS: tuple[str, ...] = (
    "api_key",
    "apikey",
    "x-api-key",
    "authorization",
    "secret",
    "token",
    "password",
)

_STANDARD_RECORD_ATTRS: frozenset[str] = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(token in lowered for token in SENSITIVE_KEY_TOKENS)


def scrub_value(value: Any, secrets: tuple[str, ...]) -> Any:
    """Recursively redact registered secret substrings and sensitive keys."""
    if isinstance(value, str):
        redacted = value
        for secret in secrets:
            if secret and secret in redacted:
                redacted = redacted.replace(secret, REDACTED)
        return redacted
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for k, v in value.items():
            key_str = str(k)
            if _is_sensitive_key(key_str):
                cleaned[key_str] = REDACTED
            else:
                cleaned[key_str] = scrub_value(v, secrets)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [scrub_value(item, secrets) for item in value]
    return value


class JsonLogFormatter(logging.Formatter):
    """Formats every log record as a single-line JSON object with monotonic time."""

    def __init__(self, secrets: tuple[str, ...] = ()) -> None:
        super().__init__()
        self._secrets: tuple[str, ...] = tuple(s for s in secrets if s)

    def update_secrets(self, secrets: tuple[str, ...]) -> None:
        self._secrets = tuple(s for s in secrets if s)

    def format(self, record: logging.LogRecord) -> str:
        mono_ns = getattr(record, "mono_ns", None)
        if not isinstance(mono_ns, int):
            mono_ns = time.monotonic_ns()

        payload: dict[str, Any] = {
            "ts_utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "ts_mono_ns": mono_ns,
            "level": record.levelname,
            "logger": record.name,
            "message": scrub_value(record.getMessage(), self._secrets),
        }

        for key, value in record.__dict__.items():
            if key in _STANDARD_RECORD_ATTRS or key == "mono_ns" or key.startswith("_"):
                continue
            if _is_sensitive_key(key):
                payload[key] = REDACTED
            else:
                payload[key] = scrub_value(value, self._secrets)

        if record.exc_info:
            exc_text = self.formatException(record.exc_info)
            payload["exception"] = scrub_value(exc_text, self._secrets)

        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(
    level: str = "INFO",
    secrets: tuple[str, ...] = (),
    stream: Any | None = None,
) -> logging.Logger:
    """Configure structured JSON logging on the voice_khata root logger."""
    logger = logging.getLogger("voice_khata")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False

    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonLogFormatter(secrets=secrets))

    logger.handlers.clear()
    logger.addHandler(handler)
    return logger


def log_event(
    logger: logging.Logger,
    event: str,
    *,
    level: int = logging.INFO,
    mono_ns: int | None = None,
    **fields: Any,
) -> int:
    """Emit a structured log event with an explicit monotonic timestamp."""
    ts_mono_ns = mono_ns if mono_ns is not None else time.monotonic_ns()
    logger.log(
        level,
        event,
        extra={"event": event, "mono_ns": ts_mono_ns, **fields},
    )
    return ts_mono_ns
