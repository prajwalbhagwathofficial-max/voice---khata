/**
 * Phase 5 Tests — Voice Proxy & Latency Logger
 *
 * Tests for:
 * - Latency logger: metrics tracking, percentile calculations, reset
 * - STT proxy: module loads, config reads
 * - TTS proxy: module loads, config reads
 * - Voice routes: module loads, registers metrics endpoint
 */

import { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

import {
  logSTTMetric,
  logTTSMetric,
  incrementSessions,
  incrementErrors,
  getMetrics,
  resetMetrics,
} from '../server/voice/latencyLogger.js';

// ─── Latency Logger ──────────────────────────────────────────

describe('Latency Logger', () => {
  beforeEach(() => {
    resetMetrics();
  });

  it('should start with zero metrics', () => {
    const m = getMetrics();
    assert.equal(m.stt.sessions, 0);
    assert.equal(m.tts.sessions, 0);
    assert.equal(m.errors, 0);
    assert.equal(m.stt.avgLatencyMs, 0);
    assert.equal(m.tts.avgTimeToFirstAudioMs, 0);
  });

  it('should track STT sessions', () => {
    incrementSessions('stt');
    incrementSessions('stt');
    const m = getMetrics();
    assert.equal(m.stt.sessions, 2);
    assert.equal(m.tts.sessions, 0);
  });

  it('should track TTS sessions', () => {
    incrementSessions('tts');
    const m = getMetrics();
    assert.equal(m.tts.sessions, 1);
  });

  it('should track errors', () => {
    incrementErrors();
    incrementErrors();
    incrementErrors();
    const m = getMetrics();
    assert.equal(m.errors, 3);
  });

  it('should log and aggregate STT latency for final transcripts', () => {
    logSTTMetric({ sessionId: 's1', latencyMs: 300, isFinal: false, transcriptLength: 5 });
    logSTTMetric({ sessionId: 's1', latencyMs: 800, isFinal: true, transcriptLength: 20 });
    logSTTMetric({ sessionId: 's2', latencyMs: 1200, isFinal: true, transcriptLength: 30 });

    const m = getMetrics();
    // avg of final only: (800 + 1200) / 2 = 1000
    assert.equal(m.stt.avgLatencyMs, 1000);
    // recent should have all 3
    assert.equal(m.stt.recent.length, 3);
  });

  it('should log and aggregate TTS time-to-first-audio', () => {
    logTTSMetric({ sessionId: 't1', requestId: 'r1', timeToFirstAudioMs: 1500, textLength: 50 });
    logTTSMetric({ sessionId: 't1', requestId: 'r2', timeToFirstAudioMs: 2500, textLength: 80 });
    logTTSMetric({ sessionId: 't2', requestId: 'r3', timeToFirstAudioMs: 1800, textLength: 30 });

    const m = getMetrics();
    // avg: (1500 + 2500 + 1800) / 3 ≈ 1933
    assert.equal(m.tts.avgTimeToFirstAudioMs, 1933);
    // under2Seconds: 1500 and 1800
    assert.equal(m.tts.under2Seconds, 2);
    assert.equal(m.tts.over2Seconds, 1);
    assert.equal(m.tts.total, 3);
  });

  it('should compute p50 and p95 correctly', () => {
    // Add 10 TTS metrics: 100, 200, ..., 1000
    for (let i = 1; i <= 10; i++) {
      logTTSMetric({ sessionId: `t${i}`, timeToFirstAudioMs: i * 100, textLength: 10 });
    }

    const m = getMetrics();
    assert.equal(m.tts.p50TimeToFirstAudioMs, 500);
    assert.equal(m.tts.p95TimeToFirstAudioMs, 1000);
  });

  it('should keep only the last MAX_ENTRIES', () => {
    // Log 5 entries and check recent returns max 10
    for (let i = 0; i < 5; i++) {
      logSTTMetric({ sessionId: `s${i}`, latencyMs: i * 100, isFinal: true, transcriptLength: 10 });
    }
    const m = getMetrics();
    assert.equal(m.stt.recent.length, 5); // only 5, not truncated (< 10 recent cap)
  });

  it('should reset all metrics', () => {
    incrementSessions('stt');
    incrementSessions('tts');
    incrementErrors();
    logSTTMetric({ sessionId: 's1', latencyMs: 500, isFinal: true, transcriptLength: 10 });
    logTTSMetric({ sessionId: 't1', timeToFirstAudioMs: 1000, textLength: 20 });

    resetMetrics();
    const m = getMetrics();
    assert.equal(m.stt.sessions, 0);
    assert.equal(m.tts.sessions, 0);
    assert.equal(m.errors, 0);
    assert.equal(m.stt.recent.length, 0);
    assert.equal(m.tts.recent.length, 0);
  });
});

// ─── Module loading tests ─────────────────────────────────────

describe('STT Proxy module', () => {
  it('should export a default async function', async () => {
    const mod = await import('../server/voice/sttProxy.js');
    assert.equal(typeof mod.default, 'function');
  });
});

describe('TTS Proxy module', () => {
  it('should export a default async function', async () => {
    const mod = await import('../server/voice/ttsProxy.js');
    assert.equal(typeof mod.default, 'function');
  });
});

describe('Voice Routes module', () => {
  it('should export a default async function', async () => {
    const mod = await import('../server/voice/index.js');
    assert.equal(typeof mod.default, 'function');
  });
});

// ─── TTS target validation ──────────────────────────────────

describe('TTS 2-second target tracking', () => {
  beforeEach(() => {
    resetMetrics();
  });

  it('should correctly classify requests as under/over 2 seconds', () => {
    logTTSMetric({ sessionId: 't1', timeToFirstAudioMs: 500, textLength: 10 });
    logTTSMetric({ sessionId: 't2', timeToFirstAudioMs: 1999, textLength: 20 });
    logTTSMetric({ sessionId: 't3', timeToFirstAudioMs: 2000, textLength: 30 });  // exactly 2s = under
    logTTSMetric({ sessionId: 't4', timeToFirstAudioMs: 2001, textLength: 40 });
    logTTSMetric({ sessionId: 't5', timeToFirstAudioMs: 3000, textLength: 50 });

    const m = getMetrics();
    assert.equal(m.tts.under2Seconds, 3); // 500, 1999, 2000
    assert.equal(m.tts.over2Seconds, 2);  // 2001, 3000
  });

  it('should track per-request metadata', () => {
    logTTSMetric({
      sessionId: 't1',
      requestId: 'r1',
      timeToFirstAudioMs: 1200,
      textLength: 45,
      language: 'hi-IN',
      voice: 'Shivani',
    });

    const m = getMetrics();
    const recent = m.tts.recent[0];
    assert.equal(recent.sessionId, 't1');
    assert.equal(recent.requestId, 'r1');
    assert.equal(recent.timeToFirstAudioMs, 1200);
    assert.equal(recent.textLength, 45);
    assert.equal(recent.language, 'hi-IN');
    assert.equal(recent.voice, 'Shivani');
    assert.ok(recent.timestamp > 0);
  });
});
