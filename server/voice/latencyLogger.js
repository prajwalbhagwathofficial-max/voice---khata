/**
 * Latency Logger — Voice Khata
 *
 * Tracks time-to-first-audio (TTS), STT transcript latency,
 * and overall voice pipeline metrics.
 * Target: < 2 second time-to-first-audio for TTS.
 */

const metrics = {
  stt: [],
  tts: [],
  total: { sttSessions: 0, ttsSessions: 0, errors: 0 },
};

const MAX_ENTRIES = 1000;

export function logSTTMetric(data) {
  metrics.stt.push({ ...data, timestamp: Date.now() });
  if (metrics.stt.length > MAX_ENTRIES) metrics.stt.shift();
}

export function logTTSMetric(data) {
  metrics.tts.push({ ...data, timestamp: Date.now() });
  if (metrics.tts.length > MAX_ENTRIES) metrics.tts.shift();
}

export function incrementSessions(type) {
  if (type === 'stt') metrics.total.sttSessions++;
  else if (type === 'tts') metrics.total.ttsSessions++;
}

export function incrementErrors() {
  metrics.total.errors++;
}

export function getMetrics() {
  const sttLatencies = metrics.stt.filter(m => m.isFinal).map(m => m.latencyMs);
  const ttsLatencies = metrics.tts.map(m => m.timeToFirstAudioMs);

  return {
    stt: {
      sessions: metrics.total.sttSessions,
      avgLatencyMs: avg(sttLatencies),
      p50LatencyMs: percentile(sttLatencies, 50),
      p95LatencyMs: percentile(sttLatencies, 95),
      recent: metrics.stt.slice(-10),
    },
    tts: {
      sessions: metrics.total.ttsSessions,
      avgTimeToFirstAudioMs: avg(ttsLatencies),
      p50TimeToFirstAudioMs: percentile(ttsLatencies, 50),
      p95TimeToFirstAudioMs: percentile(ttsLatencies, 95),
      under2Seconds: ttsLatencies.filter(l => l <= 2000).length,
      over2Seconds: ttsLatencies.filter(l => l > 2000).length,
      total: ttsLatencies.length,
      recent: metrics.tts.slice(-10),
    },
    errors: metrics.total.errors,
  };
}

/** Reset all metrics (for testing) */
export function resetMetrics() {
  metrics.stt.length = 0;
  metrics.tts.length = 0;
  metrics.total.sttSessions = 0;
  metrics.total.ttsSessions = 0;
  metrics.total.errors = 0;
}

function avg(arr) {
  if (arr.length === 0) return 0;
  return Math.round(arr.reduce((s, v) => s + v, 0) / arr.length);
}

function percentile(arr, p) {
  if (arr.length === 0) return 0;
  const sorted = [...arr].sort((a, b) => a - b);
  const idx = Math.ceil((p / 100) * sorted.length) - 1;
  return sorted[Math.max(0, idx)];
}
