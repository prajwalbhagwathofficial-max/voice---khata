"""Monotonic latency tracking for every latency-relevant pipeline stage."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from voice_khata.logging_utils import log_event

_DEFAULT_LOGGER = logging.getLogger("voice_khata.latency")


@dataclass
class TurnLatencyTracker:
    """Records monotonic timestamps for each stage in a voice turn."""

    session_id: str
    turn_id: str
    clock_ns: Callable[[], int] = time.monotonic_ns
    logger: logging.Logger = field(default_factory=lambda: _DEFAULT_LOGGER)
    started_mono_ns: int = field(init=False)
    marks: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.started_mono_ns = self.clock_ns()
        self.marks["turn_start"] = self.started_mono_ns
        log_event(
            self.logger,
            "latency.turn_start",
            mono_ns=self.started_mono_ns,
            session_id=self.session_id,
            turn_id=self.turn_id,
            stage="turn_start",
            since_turn_start_ms=0.0,
        )

    def mark(self, stage: str, **extra: Any) -> int:
        """Record a monotonic timestamp for a pipeline stage and emit a JSON log."""
        now_ns = self.clock_ns()
        self.marks[stage] = now_ns
        since_start_ms = round((now_ns - self.started_mono_ns) / 1_000_000, 2)
        log_event(
            self.logger,
            f"latency.{stage}",
            mono_ns=now_ns,
            session_id=self.session_id,
            turn_id=self.turn_id,
            stage=stage,
            since_turn_start_ms=since_start_ms,
            **extra,
        )
        return now_ns

    def mark_once(self, stage: str, **extra: Any) -> int:
        """Record a stage only the first time it happens (e.g. first audio chunk)."""
        if stage in self.marks:
            return self.marks[stage]
        return self.mark(stage, **extra)

    def elapsed_ms(self, start_stage: str, end_stage: str) -> float | None:
        """Compute elapsed milliseconds between two recorded monotonic stages."""
        start = self.marks.get(start_stage)
        end = self.marks.get(end_stage)
        if start is None or end is None:
            return None
        return round((end - start) / 1_000_000, 2)

    def to_summary(self) -> dict[str, Any]:
        """Return a JSON-serializable summary of recorded stages and key durations."""
        durations_ms: dict[str, float | None] = {
            "stt_ms": self.elapsed_ms("audio_first_chunk", "stt_final"),
            "nlu_ms": self.elapsed_ms("nlu_start", "nlu_end"),
            "db_ms": self.elapsed_ms("db_start", "db_end"),
            "tts_ttfb_ms": self.elapsed_ms("tts_start", "tts_first_audio_chunk"),
            "tts_total_ms": self.elapsed_ms("tts_start", "tts_end"),
            "e2e_first_audio_ms": self.elapsed_ms("stt_final", "ws_first_audio_sent"),
            "turn_total_ms": self.elapsed_ms("turn_start", "turn_end"),
        }
        return {
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "marks_mono_ns": dict(self.marks),
            "durations_ms": {k: v for k, v in durations_ms.items() if v is not None},
        }
