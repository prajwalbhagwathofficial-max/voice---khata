"""Evaluation metrics and Markdown summary table formatting per noise level."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ClipResult:
    """Evaluation outcome for a single clip at a specific noise level."""

    clip_id: str
    noise_level: str  # "clean", "20dB", "10dB", "5dB"
    expected_customer: str | None
    expected_amount: int | None
    expected_direction: str
    is_correction: bool
    predicted_customer: str | None
    predicted_amount: int | None
    predicted_direction: str | None
    correction_succeeded: bool
    ttfa_ms: float | None
    error: str | None = None

    @property
    def amount_exact(self) -> bool:
        return self.predicted_amount == self.expected_amount

    @property
    def direction_exact(self) -> bool:
        if self.predicted_direction is None:
            return False
        return self.predicted_direction.strip().upper() == self.expected_direction.strip().upper()


@dataclass(frozen=True, slots=True)
class NoiseLevelSummary:
    """Aggregated accuracy and TTFA latency statistics for one noise level."""

    noise_level: str
    total_clips: int
    amount_accuracy_pct: float
    direction_accuracy_pct: float
    correction_clips: int
    correction_success_pct: float | None
    p50_ttfa_ms: float | None
    p95_ttfa_ms: float | None


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = math.ceil((pct / 100.0) * len(ordered))
    idx = min(len(ordered) - 1, max(0, rank - 1))
    return round(ordered[idx], 1)


def compute_noise_summaries(
    results: list[ClipResult], noise_order: list[str] | None = None
) -> list[NoiseLevelSummary]:
    """Group clip results by noise level and compute exact metrics and p50/p95 TTFA."""
    grouped: dict[str, list[ClipResult]] = {}
    for res in results:
        grouped.setdefault(res.noise_level, []).append(res)

    levels = noise_order if noise_order is not None else list(grouped.keys())
    summaries: list[NoiseLevelSummary] = []

    for level in levels:
        rows = grouped.get(level, [])
        if not rows:
            continue
        total = len(rows)
        amt_ok = sum(1 for r in rows if r.amount_exact)
        dir_ok = sum(1 for r in rows if r.direction_exact)

        corr_rows = [r for r in rows if r.is_correction]
        corr_pct: float | None = None
        if corr_rows:
            corr_ok = sum(1 for r in corr_rows if r.correction_succeeded)
            corr_pct = round((corr_ok / len(corr_rows)) * 100.0, 1)

        ttfas = [r.ttfa_ms for r in rows if r.ttfa_ms is not None]

        summaries.append(
            NoiseLevelSummary(
                noise_level=level,
                total_clips=total,
                amount_accuracy_pct=round((amt_ok / total) * 100.0, 1),
                direction_accuracy_pct=round((dir_ok / total) * 100.0, 1),
                correction_clips=len(corr_rows),
                correction_success_pct=corr_pct,
                p50_ttfa_ms=percentile(ttfas, 50.0),
                p95_ttfa_ms=percentile(ttfas, 95.0),
            )
        )
    return summaries


def format_markdown_table(summaries: list[NoiseLevelSummary]) -> str:
    """Render per-noise-level evaluation results as a GitHub-flavored Markdown table."""
    header = (
        "| Noise Level | Clips | Exact Amount Acc | Direction Acc | "
        "Correction Success | p50 TTFA (ms) | p95 TTFA (ms) |"
    )
    sep = "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"
    lines = [header, sep]

    for s in summaries:
        corr_str = (
            f"{s.correction_success_pct:.1f}% ({s.correction_clips})"
            if s.correction_success_pct is not None
            else "N/A"
        )
        p50_str = f"{s.p50_ttfa_ms:.1f}" if s.p50_ttfa_ms is not None else "—"
        p95_str = f"{s.p95_ttfa_ms:.1f}" if s.p95_ttfa_ms is not None else "—"
        lines.append(
            f"| {s.noise_level} | {s.total_clips} | "
            f"{s.amount_accuracy_pct:.1f}% | {s.direction_accuracy_pct:.1f}% | "
            f"{corr_str} | {p50_str} | {p95_str} |"
        )
    return "\n".join(lines)
