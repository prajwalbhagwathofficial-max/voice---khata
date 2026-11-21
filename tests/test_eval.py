"""Tests for /eval (SNR mixing, metrics, Markdown report, --max-clips, and --mock WS pipeline)."""

from __future__ import annotations

import math
from array import array
from pathlib import Path

import pytest

from eval.audio import (
    EXPECTED_SAMPLE_RATE,
    FRAME_BYTES,
    has_non_silent_pcm16,
    load_wav_pcm16,
    mix_noise_snr,
    rms_power,
    save_wav_pcm16,
    slice_pcm16_frames,
)
from eval.metrics import ClipResult, compute_noise_summaries, format_markdown_table
from eval.runner import load_labels, main, parse_snr_levels, run_evaluation
from eval.synth_assets import ensure_synthetic_eval_assets


def _sine_pcm(freq_hz: float, duration_s: float, amp: int = 8000) -> bytes:
    n = int(EXPECTED_SAMPLE_RATE * duration_s)
    return array(
        "h",
        (int(round(math.sin(2.0 * math.pi * freq_hz * i / EXPECTED_SAMPLE_RATE) * amp)) for i in range(n)),
    ).tobytes()


@pytest.mark.parametrize("target_snr_db", [20.0, 10.0, 5.0])
def test_mix_noise_snr_matches_target_decibels(target_snr_db: float) -> None:
    # Orthogonal sine frequencies guarantee exact empirical SNR measurement
    signal = _sine_pcm(440.0, 0.5, amp=6000)
    noise = _sine_pcm(1373.0, 0.25, amp=4000)  # shorter than signal -> verifies looping too

    mixed = mix_noise_snr(signal, noise, target_snr_db)
    assert len(mixed) == len(signal)

    # Subtract original signal to recover exact added noise component
    sig_arr = array("h")
    sig_arr.frombytes(signal)
    mix_arr = array("h")
    mix_arr.frombytes(mixed)
    added_noise = array("h", (mix_arr[i] - sig_arr[i] for i in range(len(sig_arr)))).tobytes()

    p_sig = rms_power(signal)
    p_noi = rms_power(added_noise)
    measured_snr_db = 10.0 * math.log10(p_sig / p_noi)
    assert abs(measured_snr_db - target_snr_db) < 0.25


def test_wav_io_frame_slicing_and_non_silent_detection(tmp_path: Path) -> None:
    pcm = _sine_pcm(300.0, 0.05, amp=2000)  # 50 ms -> 2.5 frames -> 3 padded 20 ms frames
    wav_file = tmp_path / "sample.wav"
    save_wav_pcm16(wav_file, pcm)

    loaded = load_wav_pcm16(wav_file)
    assert loaded == pcm

    frames = slice_pcm16_frames(loaded)
    assert len(frames) == 3
    assert all(len(f) == FRAME_BYTES for f in frames)

    assert has_non_silent_pcm16(b"\x00" * 640) is False
    assert has_non_silent_pcm16(frames[0]) is True


def test_metrics_computation_and_markdown_table() -> None:
    results = [
        ClipResult("c1", "clean", "Ramesh", 340, "CREDIT", False, "Ramesh", 340, "CREDIT", False, 120.0),
        ClipResult("c2", "clean", "Anil", 125, "VOID", True, "Anil", 125, "VOID", True, 200.0),
        ClipResult("c1", "5dB", "Ramesh", 340, "CREDIT", False, "Ramesh", 300, "CREDIT", False, 450.0),
        ClipResult("c2", "5dB", "Anil", 125, "VOID", True, "Anil", 125, "CREDIT", False, 900.0),
    ]

    summaries = compute_noise_summaries(results, noise_order=["clean", "5dB"])
    assert len(summaries) == 2

    clean, noisy = summaries
    assert clean.amount_accuracy_pct == 100.0
    assert clean.direction_accuracy_pct == 100.0
    assert clean.correction_success_pct == 100.0
    assert clean.p50_ttfa_ms == 120.0
    assert clean.p95_ttfa_ms == 200.0

    assert noisy.amount_accuracy_pct == 50.0
    assert noisy.direction_accuracy_pct == 50.0
    assert noisy.correction_success_pct == 0.0
    assert noisy.p50_ttfa_ms == 450.0
    assert noisy.p95_ttfa_ms == 900.0

    md = format_markdown_table(summaries)
    assert "| Noise Level | Clips | Exact Amount Acc |" in md
    assert "| clean | 2 | 100.0% | 100.0% | 100.0% (1) | 120.0 | 200.0 |" in md
    assert "| 5dB | 2 | 50.0% | 50.0% | 0.0% (1) | 450.0 | 900.0 |" in md


def test_parse_snr_levels_and_max_clips(tmp_path: Path) -> None:
    labels_path = ensure_synthetic_eval_assets(tmp_path / "eval_pkg")
    all_clips = load_labels(labels_path)
    assert len(all_clips) == 6

    capped = load_labels(labels_path, max_clips=2)
    assert len(capped) == 2
    assert capped[0].id == all_clips[0].id

    assert parse_snr_levels("clean,20,10,5") == [
        ("clean", None),
        ("20dB", 20.0),
        ("10dB", 10.0),
        ("5dB", 5.0),
    ]


@pytest.mark.asyncio
async def test_mock_websocket_eval_pipeline_end_to_end_across_snr_levels(tmp_path: Path) -> None:
    eval_root = tmp_path / "eval_e2e"
    labels_path = ensure_synthetic_eval_assets(eval_root)

    results, table = await run_evaluation(
        labels_path=labels_path,
        noise_dir=eval_root / "noise",
        snr_spec="clean,20,10,5",
        max_clips=6,
        use_mock=True,
    )

    # 6 clips * 4 noise levels = 24 real WebSocket turns
    assert len(results) == 24
    assert all(r.error is None for r in results)
    assert all(r.ttfa_ms is not None and r.ttfa_ms >= 0.0 for r in results)
    assert all(r.amount_exact for r in results)
    assert all(r.direction_exact for r in results)

    for lvl in ("clean", "20dB", "10dB", "5dB"):
        assert f"| {lvl} | 6 |" in table


def test_cli_main_mock_with_max_clips(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    eval_root = tmp_path / "eval_cli"
    labels_path = ensure_synthetic_eval_assets(eval_root)

    rc = main(
        [
            "--mock",
            "--labels",
            str(labels_path),
            "--noise-dir",
            str(eval_root / "noise"),
            "--snr-levels",
            "20,10,5",
            "--max-clips",
            "3",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "| 20dB | 3 |" in out
    assert "| 10dB | 3 |" in out
    assert "| 5dB | 3 |" in out
