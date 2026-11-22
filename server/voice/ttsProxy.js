/**
 * TTS Proxy — Voice Khata
 *
 * WebSocket proxy for Gnani Timbre v3.0 text-to-speech.
 * Browser connects to ws://server/api/voice/tts
 * Server opens upstream to wss://api.vachana.ai/api/v1/tts
 *
 * Client → Server: JSON { text, language?, voice? }
 * Server → Client: JSON { type: 'audio', chunk_index, audio (base64), is_final }
 *
 * The session stays open between requests so the client can
 * send multiple TTS requests without re-connecting.
 */

import WebSocket from 'ws';
import { logTTSMetric, incrementSessions, incrementErrors } from './latencyLogger.js';

export default async function ttsProxy(fastify, { config }) {
  const ttsEndpoint = config.env.GNANI_TTS_URL || 'wss://api.vachana.ai/api/v1/tts';
  const apiKey = config.env.GNANI_API_KEY;
  const ttsModel = config.settings?.voice?.tts_model || 'timbre-v3.0';
  const defaultVoice = config.settings?.voice?.tts_voice || 'Shivani';
  const defaultSpeed = config.settings?.voice?.tts_speed || 1.0;
  const sampleRate = config.settings?.voice?.tts_sample_rate || 48000;

  fastify.get('/api/voice/tts', { websocket: true }, (socket, req) => {
    if (!apiKey) {
      socket.send(JSON.stringify({ type: 'error', message: 'Gnani API key not configured' }));
      socket.close(1008, 'No API key');
      return;
    }

    const sessionId = `tts_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    fastify.log.info({ sessionId }, 'TTS session started');
    incrementSessions('tts');

    // Each incoming text message opens a fresh upstream connection
    socket.on('message', (data) => {
      let request;
      try {
        request = JSON.parse(data.toString());
      } catch {
        socket.send(JSON.stringify({ type: 'error', message: 'Invalid JSON' }));
        return;
      }

      const { text, language, voice } = request;
      if (!text) {
        socket.send(JSON.stringify({ type: 'error', message: 'No text provided' }));
        return;
      }

      const requestId = `${sessionId}_${Date.now()}`;
      const requestStartTime = Date.now();
      let firstAudioTime = null;
      let chunkCount = 0;

      fastify.log.info({ requestId, textLength: text.length, language }, 'TTS request');

      let upstream;
      try {
        upstream = new WebSocket(ttsEndpoint, {
          headers: { 'X-API-Key-ID': apiKey },
        });
      } catch (err) {
        fastify.log.error({ err, requestId }, 'Failed to connect to Gnani TTS');
        socket.send(JSON.stringify({ type: 'error', requestId, message: 'TTS connection failed' }));
        incrementErrors();
        return;
      }

      upstream.on('open', () => {
        const ttsRequest = {
          text,
          voice: voice || defaultVoice,
          model: ttsModel,
          language: language || 'hi-IN',
          speed: defaultSpeed,
          audio_config: {
            sample_rate: sampleRate,
            num_channels: 1,
            sample_width: 2,
            encoding: 'linear_pcm',
            container: 'wav',
          },
        };
        upstream.send(JSON.stringify(ttsRequest));
      });

      upstream.on('message', (chunk) => {
        try {
          const msg = JSON.parse(chunk.toString());
          chunkCount++;

          // Gnani sends audio inside msg.data.audio or msg.audio
          const audioBase64 = msg.audio || msg.data?.audio || '';
          const isFinal = Boolean(msg.is_final || msg.data?.is_final || msg.type === 'complete');
          const chunkIndex = msg.chunk_index ?? msg.data?.chunk_index ?? chunkCount;

          // Track time-to-first-audio
          if (!firstAudioTime && audioBase64) {
            firstAudioTime = Date.now();
            const ttfa = firstAudioTime - requestStartTime;

            const isUnder2s = ttfa <= 2000;
            fastify.log.info(
              { requestId, timeToFirstAudioMs: ttfa, under2sTarget: isUnder2s },
              isUnder2s ? 'TTS first audio ✓ under 2s' : 'TTS first audio ✗ OVER 2s target'
            );

            logTTSMetric({
              sessionId,
              requestId,
              timeToFirstAudioMs: ttfa,
              textLength: text.length,
              language: language || 'hi-IN',
              voice: voice || defaultVoice,
            });
          }

          // Forward audio chunk to browser
          if (socket.readyState === WebSocket.OPEN && (audioBase64 || isFinal)) {
            socket.send(JSON.stringify({
              type: 'audio',
              requestId,
              chunk_index: chunkIndex,
              audio: audioBase64,
              is_final: isFinal,
            }));
          }

          // On final chunk, log totals and close upstream
          if (isFinal) {
            const totalMs = Date.now() - requestStartTime;
            fastify.log.info(
              { requestId, totalChunks: chunkCount, totalMs },
              'TTS synthesis complete'
            );
            upstream.close(1000, 'Complete');
          }
        } catch {
          // Forward raw if not JSON
          if (socket.readyState === WebSocket.OPEN) {
            socket.send(chunk);
          }
        }
      });

      upstream.on('error', (err) => {
        fastify.log.error({ err: err.message, requestId }, 'Gnani TTS upstream error');
        if (socket.readyState === WebSocket.OPEN) {
          socket.send(JSON.stringify({ type: 'error', requestId, message: `TTS upstream error: ${err.message}` }));
        }
        incrementErrors();
      });

      upstream.on('close', () => {
        // Upstream closed after one request; session stays open for next
      });
    });

    socket.on('close', () => {
      fastify.log.info({ sessionId }, 'TTS client disconnected');
    });

    socket.on('error', (err) => {
      fastify.log.error({ err: err.message, sessionId }, 'TTS client socket error');
    });
  });
}
