"""Tests for voice_khata.latency."""

from __future__ import annotations

import io
import json

from voice_khata.latency import TurnLatencyTracker
from voice_khata.logging_utils import configure_logging


def test_latency_tracker_records_monotonic_marks_and_durations() -> None:
    buf = io.StringIO()
    logger = configure_logging(level="INFO", stream=buf)

    ticks = iter(
        [
            1_000_000_000,  # turn_start
            1_010_000_000,  # audio_first_chunk (+10ms)
            1_210_000_000,  # stt_final (+200ms from audio_first_chunk)
            1_215_000_000,  # nlu_start
            1_295_000_000,  # nlu_end (80ms)
            1_300_000_000,  # db_start
            1_305_000_000,  # db_end (5ms)
            1_310_000_000,  # tts_start
            1_410_000_000,  # tts_first_audio_chunk (100ms)
            1_412_000_000,  # ws_first_audio_sent (202ms after stt_final)
            1_510_000_000,  # tts_end (200ms)
            1_520_000_000,  # turn_end (520ms)
        ]
    )

    tracker = TurnLatencyTracker(
        session_id="s1", turn_id="t001", clock_ns=lambda: next(ticks), logger=logger
    )
    tracker.mark_once("audio_first_chunk")
    # Calling mark_once a second time must not advance the clock or overwrite
    assert tracker.mark_once("audio_first_chunk") == 1_010_000_000
    tracker.mark("stt_final")
    tracker.mark("nlu_start")
    tracker.mark("nlu_end")
    tracker.mark("db_start")
    tracker.mark("db_end")
    tracker.mark("tts_start")
    tracker.mark("tts_first_audio_chunk")
    tracker.mark("ws_first_audio_sent")
    tracker.mark("tts_end")
    tracker.mark("turn_end")

    summary = tracker.to_summary()
    assert summary["durations_ms"]["stt_ms"] == 200.0
    assert summary["durations_ms"]["nlu_ms"] == 80.0
    assert summary["durations_ms"]["db_ms"] == 5.0
    assert summary["durations_ms"]["tts_ttfb_ms"] == 100.0
    assert summary["durations_ms"]["e2e_first_audio_ms"] == 202.0
    assert summary["durations_ms"]["turn_total_ms"] == 520.0

    lines = [json.loads(line) for line in buf.getvalue().strip().splitlines()]
    assert len(lines) == 12
    assert all(isinstance(row["ts_mono_ns"], int) for row in lines)
