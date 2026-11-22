/**
 * STT Proxy — Voice Khata
 *
 * WebSocket proxy for Gnani Prisma v3.0 speech-to-text.
 * Browser connects to ws://server/api/voice/stt
 * Server opens upstream to wss://api.vachana.ai/stt/v3/stream
 * with auth headers that browsers cannot set on WebSocket.
 *
 * Client → Server: raw PCM audio (16-bit mono, 16kHz)
 * Server → Client: JSON transcript messages
 */

import WebSocket from 'ws';
import fs from 'fs';
import path from 'path';
import { logSTTMetric, incrementSessions, incrementErrors } from './latencyLogger.js';

function debugLog(...args) {
  const line = `[${new Date().toISOString()}] ${args.map(a => typeof a === 'object' ? JSON.stringify(a) : a).join(' ')}\n`;
  try {
    fs.appendFileSync('server/voice_debug.log', line);
  } catch {}
}

export default async function sttProxy(fastify, { config }) {
  const sttEndpoint = config.env.GNANI_STT_URL || 'wss://api.vachana.ai/stt/v3/stream';
  const apiKey = config.env.GNANI_API_KEY;
  const sttModel = config.settings?.voice?.stt_model || 'prisma-v3.0';
  const sampleRate = String(config.settings?.voice?.stt_sample_rate || 16000);
  const languageCode = config.settings?.languages?.stt_language_code || 'indic-auto';

  fastify.get('/api/voice/stt', { websocket: true }, (socket, req) => {
    if (!apiKey) {
      socket.send(JSON.stringify({ type: 'error', message: 'Gnani API key not configured' }));
      socket.close(1008, 'No API key');
      return;
    }

    const queryLang = req.query?.lang;
    const activeLanguage = (queryLang && queryLang !== 'auto') ? queryLang : (config.settings?.languages?.stt_language_code || 'indic-auto');
    const sessionId = `stt_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    let upstream = null;
    let audioStartTime = null;
    const upstreamBuffer = [];

    fastify.log.info({ sessionId, activeLanguage }, 'STT session started');
    debugLog('STT session started:', sessionId, 'language:', activeLanguage);
    incrementSessions('stt');

    // Open upstream connection to Gnani with auth headers
    try {
      upstream = new WebSocket(sttEndpoint, {
        headers: {
          'x-api-key-id': apiKey,
          'x-model': sttModel,
          'language_code': activeLanguage,
          'x-sample-rate': sampleRate,
        },
      });
    } catch (err) {
      debugLog('Upstream connect error:', err.message);
      fastify.log.error({ err, sessionId }, 'Failed to connect to Gnani STT');
      socket.send(JSON.stringify({ type: 'error', message: 'STT connection failed' }));
      socket.close(1011, 'Upstream connection failed');
      incrementErrors();
      return;
    }

    upstream.on('open', () => {
      debugLog('Gnani STT upstream connected for:', sessionId);
      fastify.log.info({ sessionId }, 'Gnani STT upstream connected');
      socket.send(JSON.stringify({ type: 'connected', sessionId }));

      // Flush all pre-buffered audio chunks immediately so no opening phoneme is lost
      while (upstreamBuffer.length > 0) {
        const buffered = upstreamBuffer.shift();
        try {
          upstream.send(buffered);
        } catch {}
      }
    });

    upstream.on('message', (data) => {
      debugLog('Gnani STT message received:', data.toString().slice(0, 250));
      try {
        const msg = JSON.parse(data.toString());

        // Gnani returns text in 'text', normalize so both 'text' and 'transcript' exist
        const transcriptText = msg.text || msg.transcript || '';
        if (transcriptText) {
          msg.transcript = transcriptText;
          msg.text = transcriptText;
        }

        // Track latency for transcript messages
        if ((msg.type === 'transcript' || transcriptText) && audioStartTime) {
          const latencyMs = Date.now() - audioStartTime;

          logSTTMetric({
            sessionId,
            latencyMs,
            isFinal: msg.is_final || false,
            transcriptLength: transcriptText.length,
            confidence: msg.confidence || 0.95,
            detectedLanguage: msg.detected_language_code || msg.language_code,
          });

          if (msg.is_final) {
            fastify.log.info({
              sessionId,
              latencyMs,
              transcript: transcriptText.slice(0, 60),
            }, 'STT final transcript');
          }
        }

        // Forward normalized message to browser
        if (socket.readyState === WebSocket.OPEN) {
          socket.send(JSON.stringify(msg));
        }
      } catch {
        // Forward raw data if not JSON
        if (socket.readyState === WebSocket.OPEN) {
          socket.send(data);
        }
      }
    });

    upstream.on('error', (err) => {
      fastify.log.error({ err: err.message, sessionId }, 'Gnani STT upstream error');
      if (socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: 'error', message: `STT upstream error: ${err.message}` }));
      }
      incrementErrors();
    });

    upstream.on('close', (code, reason) => {
      fastify.log.info({ sessionId, code, reason: reason?.toString() }, 'Gnani STT upstream closed');
      if (socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: 'closed', sessionId }));
        socket.close(1000, 'Upstream closed');
      }
    });

    // Forward audio or control messages from browser → Gnani
    let clientChunkCount = 0;
    socket.on('message', (data) => {
      clientChunkCount++;
      // Check if browser sent a JSON control command (e.g. stop/finish)
      if (typeof data === 'string' || (Buffer.isBuffer(data) && data[0] === 123 /* '{' */)) {
        try {
          const parsed = JSON.parse(data.toString());
          if (parsed.type === 'stop' || parsed.type === 'finish') {
            debugLog('Client sent STOP command. Chunks forwarded so far:', clientChunkCount);
            // Stream 500ms of silence frames (16kHz 16-bit mono = 3200 bytes per 100ms)
            // to ensure Gnani's VAD triggers end of speech and returns the final transcript
            if (upstream && upstream.readyState === WebSocket.OPEN) {
              const silence = Buffer.alloc(3200);
              for (let i = 0; i < 5; i++) {
                upstream.send(silence);
              }
            }
            return;
          }
        } catch {}
      }

      // Start latency timer on first audio chunk
      if (!audioStartTime) audioStartTime = Date.now();

      if (upstream && upstream.readyState === WebSocket.OPEN) {
        upstream.send(data);
      } else {
        // Buffer initial audio chunks before Gnani upstream connection handshake finishes
        if (upstreamBuffer.length < 50) {
          upstreamBuffer.push(data);
        }
      }
    });

    socket.on('close', () => {
      debugLog('STT client disconnected, total chunks forwarded:', clientChunkCount);
      fastify.log.info({ sessionId }, 'STT client disconnected');
      // Give upstream 1200ms to deliver final transcript before terminating
      setTimeout(() => {
        if (upstream && upstream.readyState === WebSocket.OPEN) {
          upstream.close(1000, 'Client disconnected');
        }
      }, 1200);
    });

    socket.on('error', (err) => {
      fastify.log.error({ err: err.message, sessionId }, 'STT client socket error');
    });
  });
}
