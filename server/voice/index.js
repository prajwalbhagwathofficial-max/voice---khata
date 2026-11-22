/**
 * Voice Routes — Voice Khata
 *
 * Registers the STT and TTS WebSocket proxies and the metrics endpoint.
 *
 * Endpoints:
 *   WS  /api/voice/stt      - STT proxy (browser ↔ Gnani Prisma v3.0)
 *   WS  /api/voice/tts      - TTS proxy (browser ↔ Gnani Timbre v3.0)
 *   GET /api/voice/metrics   - Latency metrics dashboard
 */

import sttProxy from './sttProxy.js';
import ttsProxy from './ttsProxy.js';
import { getMetrics } from './latencyLogger.js';

export default async function voiceRoutes(fastify, opts) {
  // Register STT WebSocket proxy
  await fastify.register(sttProxy, opts);

  // Register TTS WebSocket proxy
  await fastify.register(ttsProxy, opts);

  // Metrics endpoint - JSON dashboard for latency monitoring
  fastify.get('/api/voice/metrics', async () => {
    return getMetrics();
  });
}
