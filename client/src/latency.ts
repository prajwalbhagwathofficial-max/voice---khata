/**
 * Session latency statistics (last turn delta, p50, p95, and <800ms color threshold).
 */

export const LATENCY_GREEN_THRESHOLD_MS = 800;
export const LATENCY_AMBER_THRESHOLD_MS = 1500;

export type LatencyTone = "neutral" | "green" | "amber" | "red";

export interface LatencyStats {
  lastMs: number | null;
  p50Ms: number | null;
  p95Ms: number | null;
  count: number;
  tone: LatencyTone;
}

export function percentile(samples: readonly number[], p: number): number | null {
  if (samples.length === 0) {
    return null;
  }
  const sorted = [...samples].sort((a, b) => a - b);
  const rank = Math.ceil((p / 100) * sorted.length);
  const index = Math.min(sorted.length - 1, Math.max(0, rank - 1));
  return Math.round(sorted[index]);
}

export function classifyLatencyTone(deltaMs: number | null): LatencyTone {
  if (deltaMs === null) {
    return "neutral";
  }
  if (deltaMs < LATENCY_GREEN_THRESHOLD_MS) {
    return "green";
  }
  if (deltaMs < LATENCY_AMBER_THRESHOLD_MS) {
    return "amber";
  }
  return "red";
}

export function computeLatencyStats(samples: readonly number[]): LatencyStats {
  const lastMs = samples.length > 0 ? Math.round(samples[samples.length - 1]) : null;
  return {
    lastMs,
    p50Ms: percentile(samples, 50),
    p95Ms: percentile(samples, 95),
    count: samples.length,
    tone: classifyLatencyTone(lastMs),
  };
}
