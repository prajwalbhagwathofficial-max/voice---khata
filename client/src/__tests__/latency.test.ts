import { describe, expect, it } from "vitest";
import { classifyLatencyTone, computeLatencyStats, percentile } from "../latency";

describe("latency stats and threshold", () => {
  it("classifies < 800 ms as green, 800..1499 as amber, >= 1500 as red", () => {
    expect(classifyLatencyTone(null)).toBe("neutral");
    expect(classifyLatencyTone(420)).toBe("green");
    expect(classifyLatencyTone(799)).toBe("green");
    expect(classifyLatencyTone(800)).toBe("amber");
    expect(classifyLatencyTone(1499)).toBe("amber");
    expect(classifyLatencyTone(1500)).toBe("red");
  });

  it("computes p50 and p95 over session samples", () => {
    const samples = [300, 400, 450, 500, 550, 600, 700, 750, 900, 1200];
    expect(percentile(samples, 50)).toBe(550);
    expect(percentile(samples, 95)).toBe(1200);

    const stats = computeLatencyStats(samples);
    expect(stats.lastMs).toBe(1200);
    expect(stats.p50Ms).toBe(550);
    expect(stats.p95Ms).toBe(1200);
    expect(stats.count).toBe(10);
    expect(stats.tone).toBe("amber");
  });
});
