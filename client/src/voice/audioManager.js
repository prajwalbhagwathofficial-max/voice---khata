/**
 * Audio Manager — Voice Khata (Client-side)
 *
 * Handles:
 * 1. Microphone access & audio stream acquisition
 * 2. Real-time PCM downsampling (16kHz mono 16-bit linear PCM)
 * 3. RMS energy calculation for Voice Activity Detection (VAD) / barge-in
 * 4. WebSocket connection to Gnani STT proxy (/api/voice/stt)
 * 5. Native browser Web Speech API (webkitSpeechRecognition) fallback/hybrid engine
 * 6. Web Audio API playback of TTS audio chunks from (/api/voice/tts)
 * 7. Barge-in interruption handling
 */

import { checkBargeIn, ttsStarted, ttsStopped } from './bargeIn.js';

let audioContext = null;
let mediaStream = null;
let mediaStreamSource = null; // Stored at module-level to prevent V8 garbage collection
let micGainNode = null;
let processorNode = null;
let sttSocket = null;
let ttsSocket = null;
let nativeRecognizer = null;
let currentSource = null;
let isRecording = false;
let isPlayingTTS = false;
let audioQueue = [];
let activeFallbackTimer = null;
let currentTTSGeneration = 0;
let preConnectionBuffer = [];

/**
 * Map Indian language codes to the appropriate Gnani voice.
 */
export function getVoiceForLanguage(lang) {
  const clean = (lang || 'hi').toLowerCase().slice(0, 2);
  switch (clean) {
    case 'kn': return 'Chetana';
    case 'ta': return 'Gayatri';
    case 'te': return 'Rohini';
    case 'mr': return 'Kavita';
    case 'gu': return 'Komal';
    case 'bn': return 'Mita';
    case 'ml': return 'Arya';
    case 'pa': return 'Simran';
    case 'en': return 'Shivani';
    default: return 'Shivani';
  }
}

/**
 * Request microphone permissions and ensure AudioContext is initialized.
 */
export async function initAudio() {
  if (!audioContext) {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    audioContext = new AudioContextClass();
  }
  if (audioContext.state === 'suspended') {
    await audioContext.resume();
  }
  return audioContext;
}

/**
 * Determine the best WebSocket URL for a given path.
 */
function resolveWebSocketUrl(path) {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${window.location.host}${path}`;
}

/**
 * Start streaming microphone audio and capturing speech transcripts.
 * Uses a hybrid engine:
 * - Gnani Prisma v3.0 STT WebSocket proxy (Primary)
 * - Browser SpeechRecognition / webkitSpeechRecognition (Complementary / Zero-latency fallback)
 *
 * @param {object} opts
 * @param {string} [opts.wsUrl] - Custom WebSocket URL
 * @param {string} [opts.language] - Speech recognition language (e.g. 'hi-IN', 'kn-IN', 'en-IN')
 * @param {function} opts.onTranscript - Callback receiving { transcript, isFinal, confidence, detectedLanguage }
 * @param {function} opts.onError - Callback receiving error message
 * @param {function} [opts.onEnergy] - Callback receiving live audio energy level (0.0 to 1.0)
 */
export async function startRecording({ wsUrl, language = 'hi-IN', onTranscript, onError, onEnergy }) {
  if (isRecording) return;

  try {
    await initAudio();

    // 1. Acquire mic stream with hardware native rate (disabling noiseSuppression prevents cutting leading consonants like "P")
    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: false,
        autoGainControl: true,
      },
    });

    isRecording = true;
    preConnectionBuffer = [];

    // 2. Set up Web Audio pipeline for RMS energy and PCM streaming
    setupAudioProcessing(mediaStream, onEnergy);

    // 3. Connect to Gnani STT WebSocket proxy
    const langParam = language && language !== 'auto' ? language : 'indic-auto';
    const targetUrl = wsUrl || resolveWebSocketUrl(`/api/voice/stt?lang=${encodeURIComponent(langParam)}`);
    try {
      sttSocket = new WebSocket(targetUrl);
      sttSocket.binaryType = 'arraybuffer';

      sttSocket.onopen = () => {
        console.log('[VoiceKhata] STT WebSocket connected (language: ' + langParam + ')');
        // Instantly flush all pre-buffered audio chunks so opening words are never clipped
        while (preConnectionBuffer.length > 0) {
          const buffered = preConnectionBuffer.shift();
          try { sttSocket.send(buffered); } catch {}
        }
        // Stop native recognizer if it was running to prevent duplicate transcripts
        if (nativeRecognizer) {
          try { nativeRecognizer.stop(); } catch {}
          nativeRecognizer = null;
        }
      };

      sttSocket.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          const transcriptText = data.text || data.transcript || data.result || '';
          if (transcriptText && transcriptText.trim()) {
            onTranscript({
              transcript: transcriptText.trim(),
              isFinal: Boolean(data.is_final || data.final),
              confidence: data.confidence || 0.95,
              detectedLanguage: data.detected_language_code || data.language_code || null,
            });
          } else if (data.type === 'error') {
            console.warn('[VoiceKhata] STT server message:', data.message);
          }
        } catch (e) {
          console.warn('[VoiceKhata] Non-JSON STT message:', event.data);
        }
      };

      sttSocket.onerror = (err) => {
        console.warn('[VoiceKhata] STT WebSocket error, starting browser fallback:', err);
        setupNativeSpeechRecognition(language, onTranscript);
      };

      sttSocket.onclose = () => {
        console.log('[VoiceKhata] STT WebSocket closed');
      };
    } catch (wsErr) {
      console.warn('[VoiceKhata] Could not open STT WebSocket:', wsErr);
      setupNativeSpeechRecognition(language, onTranscript);
    }

  } catch (err) {
    console.error('[VoiceKhata] Failed to start recording:', err);
    if (onError) onError(err.name === 'NotAllowedError' ? 'Microphone permission denied' : err.message);
    stopRecording();
  }
}

/**
 * Configure browser Web Speech API (webkitSpeechRecognition) if supported.
 */
function setupNativeSpeechRecognition(language, onTranscript) {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) return;

  try {
    nativeRecognizer = new SpeechRecognition();
    nativeRecognizer.continuous = true;
    nativeRecognizer.interimResults = true;
    nativeRecognizer.lang = language || 'hi-IN';
    nativeRecognizer.maxAlternatives = 1;

    nativeRecognizer.onresult = (event) => {
      let interimTranscript = '';
      let finalTranscript = '';

      for (let i = event.resultIndex; i < event.results.length; ++i) {
        const item = event.results[i];
        if (item.isFinal) {
          finalTranscript += item[0].transcript;
        } else {
          interimTranscript += item[0].transcript;
        }
      }

      const activeText = (finalTranscript || interimTranscript).trim();
      if (activeText) {
        onTranscript({
          transcript: activeText,
          isFinal: Boolean(finalTranscript),
          confidence: 0.95,
          detectedLanguage: language,
        });
      }
    };

    nativeRecognizer.onerror = (event) => {
      // Ignore 'no-speech' or 'aborted' without crashing
      if (event.error !== 'no-speech' && event.error !== 'aborted') {
        console.warn('[VoiceKhata] Native speech recognition event:', event.error);
      }
    };

    nativeRecognizer.onend = () => {
      if (isRecording) {
        try { nativeRecognizer.start(); } catch {}
      }
    };

    nativeRecognizer.start();
    console.log('[VoiceKhata] Native SpeechRecognition active (' + language + ')');
  } catch (e) {
    console.warn('[VoiceKhata] Could not initialize native speech recognition:', e);
  }
}

/**
 * Configure audio pipeline to calculate live energy and convert float samples to 16kHz 16-bit PCM.
 */
function setupAudioProcessing(stream, onEnergy) {
  mediaStreamSource = audioContext.createMediaStreamSource(stream);

  // Microphone Sensitivity Pre-amp: Boost gain to 2.8x so soft speech and initial consonants (like 'P' in Pavan, 'N' in Nisarga) are clear
  micGainNode = audioContext.createGain();
  micGainNode.gain.value = 2.8;

  // Dynamics Compressor: boosts quiet murmurs and whisper speech while preventing clipping for loud speech
  const compressor = audioContext.createDynamicsCompressor();
  compressor.threshold.value = -45;
  compressor.knee.value = 30;
  compressor.ratio.value = 12;
  compressor.attack.value = 0.003;
  compressor.release.value = 0.15;

  // Buffer size 2048 gives ~42ms latency at 48kHz (twice as fast as 4096)
  processorNode = audioContext.createScriptProcessor(2048, 1, 1);

  // Connect: Source -> Pre-amp Gain -> Compressor -> Processor
  mediaStreamSource.connect(micGainNode);
  micGainNode.connect(compressor);
  compressor.connect(processorNode);

  // Mute processor output so mic does NOT play through speakers (prevents feedback/howling)
  const silenceGain = audioContext.createGain();
  silenceGain.gain.value = 0;
  processorNode.connect(silenceGain);
  silenceGain.connect(audioContext.destination);

  processorNode.onaudioprocess = (e) => {
    if (!isRecording) return;

    const inputData = e.inputBuffer.getChannelData(0);

    // 1. Calculate RMS energy for visual pulse and VAD / barge-in
    let sum = 0;
    for (let i = 0; i < inputData.length; i++) {
      sum += inputData[i] * inputData[i];
    }
    const rms = Math.min(1.0, Math.sqrt(sum / inputData.length) * 5); // Normalized scale
    if (onEnergy) onEnergy(rms);

    // 2. Check barge-in: If TTS is speaking and user talks loudly, cut off TTS
    if (isPlayingTTS) {
      const bargedIn = checkBargeIn(rms, 0.65);
      if (bargedIn) {
        stopTTSPlayback();
      }
    }

    // 3. Resample to 16kHz 16-bit PCM and send to STT proxy
    const pcmData = downsampleTo16kPCM(inputData, audioContext.sampleRate);
    if (sttSocket && sttSocket.readyState === WebSocket.OPEN) {
      // Flush any pre-buffered audio chunks first so starting words are never clipped
      while (preConnectionBuffer.length > 0) {
        const buffered = preConnectionBuffer.shift();
        try { sttSocket.send(buffered); } catch {}
      }
      try {
        sttSocket.send(pcmData.buffer);
      } catch {}
    } else {
      // Buffer initial audio chunks while WebSocket connects (keeps up to ~2 seconds)
      if (preConnectionBuffer.length < 50) {
        preConnectionBuffer.push(pcmData.buffer);
      }
    }
  };
}

/**
 * Convert browser Float32Array audio buffer into 16-bit Linear PCM at 16,000Hz.
 */
function downsampleTo16kPCM(buffer, inputSampleRate) {
  const targetSampleRate = 16000;
  if (inputSampleRate === targetSampleRate) {
    const result = new Int16Array(buffer.length);
    for (let i = 0; i < buffer.length; i++) {
      const s = Math.max(-1, Math.min(1, buffer[i]));
      result[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    return result;
  }

  const ratio = inputSampleRate / targetSampleRate;
  const newLength = Math.round(buffer.length / ratio);
  const result = new Int16Array(newLength);

  for (let i = 0; i < newLength; i++) {
    const originIdx = Math.round(i * ratio);
    const s = Math.max(-1, Math.min(1, buffer[originIdx] || 0));
    result[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return result;
}

/**
 * Stop microphone recording immediately and disconnect nodes.
 */
export function stopRecording() {
  isRecording = false;

  if (nativeRecognizer) {
    try {
      nativeRecognizer.stop();
    } catch {}
    nativeRecognizer = null;
  }

  if (processorNode) {
    try {
      processorNode.disconnect();
    } catch {}
    processorNode = null;
  }

  if (micGainNode) {
    try {
      micGainNode.disconnect();
    } catch {}
    micGainNode = null;
  }

  if (mediaStreamSource) {
    try {
      mediaStreamSource.disconnect();
    } catch {}
    mediaStreamSource = null;
  }

  if (mediaStream) {
    mediaStream.getTracks().forEach((track) => track.stop());
    mediaStream = null;
  }

  if (sttSocket) {
    try {
      sttSocket.close();
    } catch {}
    sttSocket = null;
  }
}

/**
 * Gracefully finish recording on tap-to-stop:
 * 1. Halts audio capture.
 * 2. Sends { type: 'stop' } to proxy to trigger silence flushing.
 * 3. Keeps sttSocket open briefly to allow the final transcript from Gnani to arrive.
 */
export async function finishRecording() {
  isRecording = false;

  if (nativeRecognizer) {
    try {
      nativeRecognizer.stop();
    } catch {}
    nativeRecognizer = null;
  }

  if (processorNode) {
    try {
      processorNode.disconnect();
    } catch {}
    processorNode = null;
  }

  if (micGainNode) {
    try {
      micGainNode.disconnect();
    } catch {}
    micGainNode = null;
  }

  if (mediaStreamSource) {
    try {
      mediaStreamSource.disconnect();
    } catch {}
    mediaStreamSource = null;
  }

  if (mediaStream) {
    mediaStream.getTracks().forEach((track) => track.stop());
    mediaStream = null;
  }

  if (sttSocket && sttSocket.readyState === WebSocket.OPEN) {
    try {
      sttSocket.send(JSON.stringify({ type: 'stop' }));
    } catch {}
  }
}

/**
 * Send text to Gnani TTS proxy and stream back audio playback.
 *
 * @param {object} opts
 * @param {string} opts.text - Text to speak
 * @param {string} [opts.language] - Language tag (e.g. 'hi-IN', 'kn-IN', 'en-IN')
 * @param {string} [opts.voice] - Voice name (default: Shivani)
 * @param {function} [opts.onStart] - Called when first audio chunk begins playing
 * @param {function} [opts.onEnd] - Called when playback completes
 * @param {function} [opts.onError] - Called on TTS error
 */
export async function speakText({ text, language = 'hi-IN', voice, onStart, onEnd, onError }) {
  stopTTSPlayback(); // Clear previous TTS if still playing

  const thisGen = ++currentTTSGeneration;
  const activeVoice = voice || getVoiceForLanguage(language);

  try {
    await initAudio();

    const targetUrl = resolveWebSocketUrl('/api/voice/tts');
    ttsSocket = new WebSocket(targetUrl);
    let rawChunks = [];
    let hasPlayed = false;

    // Safety timeout: if Gnani TTS takes > 2500ms, fallback to browser speech synthesis
    activeFallbackTimer = setTimeout(() => {
      if (thisGen !== currentTTSGeneration) return;
      if (!hasPlayed) {
        console.warn('[VoiceKhata] Gnani TTS response timeout, switching to browser speech synthesis');
        if (ttsSocket) {
          try { ttsSocket.close(); } catch {}
          ttsSocket = null;
        }
        hasPlayed = true;
        fallbackBrowserSpeech(text, language, onStart, onEnd);
      }
    }, 2500);

    ttsSocket.onopen = () => {
      if (thisGen !== currentTTSGeneration) return;
      ttsSocket.send(JSON.stringify({ text, language, voice: activeVoice }));
    };

    ttsSocket.onmessage = async (event) => {
      if (thisGen !== currentTTSGeneration) return;
      try {
        const msg = JSON.parse(event.data);
        const audioBase64 = msg.audio || msg.data?.audio;
        const isFinal = Boolean(msg.is_final || msg.data?.is_final || msg.type === 'complete');

        if (audioBase64) {
          const binaryStr = window.atob(audioBase64);
          const bytes = new Uint8Array(binaryStr.length);
          for (let i = 0; i < binaryStr.length; i++) {
            bytes[i] = binaryStr.charCodeAt(i);
          }
          rawChunks.push(bytes);
        }

        if (isFinal && rawChunks.length > 0 && !hasPlayed) {
          if (activeFallbackTimer) {
            clearTimeout(activeFallbackTimer);
            activeFallbackTimer = null;
          }
          hasPlayed = true;

          // Concatenate all chunks into a single complete WAV byte array
          const totalLength = rawChunks.reduce((acc, c) => acc + c.length, 0);
          const completeWav = new Uint8Array(totalLength);
          let offset = 0;
          for (const chunk of rawChunks) {
            completeWav.set(chunk, offset);
            offset += chunk.length;
          }

          // If RIFF header is present, ensure correct length values in bytes 4-7 and 40-43
          if (completeWav.length >= 44 && completeWav[0] === 82 && completeWav[1] === 73 && completeWav[2] === 70 && completeWav[3] === 70) {
            const dv = new DataView(completeWav.buffer);
            dv.setUint32(4, completeWav.byteLength - 8, true);
            dv.setUint32(40, completeWav.byteLength - 44, true);
          }

          try {
            const decodedBuffer = await audioContext.decodeAudioData(completeWav.buffer.slice(0));
            if (thisGen !== currentTTSGeneration) return;

            isPlayingTTS = true;
            ttsStarted();
            if (onStart) onStart();

            currentSource = audioContext.createBufferSource();
            currentSource.buffer = decodedBuffer;
            currentSource.connect(audioContext.destination);

            currentSource.onended = () => {
              currentSource = null;
              isPlayingTTS = false;
              ttsStopped();
              if (onEnd) onEnd();
            };

            currentSource.start(0);
          } catch (decodeErr) {
            console.warn('[VoiceKhata] Complete WAV decode failed, falling back:', decodeErr);
            fallbackBrowserSpeech(text, language, onStart, onEnd);
          }
        } else if (msg.type === 'error') {
          console.error('[VoiceKhata] TTS proxy error:', msg.message);
          if (activeFallbackTimer) {
            clearTimeout(activeFallbackTimer);
            activeFallbackTimer = null;
          }
          if (!hasPlayed) {
            hasPlayed = true;
            fallbackBrowserSpeech(text, language, onStart, onEnd);
            if (onError) onError(msg.message);
          }
        }
      } catch (err) {
        console.error('[VoiceKhata] TTS message parse error:', err);
      }
    };

    ttsSocket.onerror = (err) => {
      console.warn('[VoiceKhata] TTS WebSocket error, falling back to browser speech:', err);
      if (activeFallbackTimer) {
        clearTimeout(activeFallbackTimer);
        activeFallbackTimer = null;
      }
      if (!hasPlayed) {
        hasPlayed = true;
        fallbackBrowserSpeech(text, language, onStart, onEnd);
      }
    };
  } catch (err) {
    console.warn('[VoiceKhata] speakText failed, falling back:', err);
    fallbackBrowserSpeech(text, language, onStart, onEnd);
    if (onError) onError(err.message);
  }
}

/**
 * Fallback browser SpeechSynthesis if Gnani TTS WebSocket is offline.
 */
function fallbackBrowserSpeech(text, language, onStart, onEnd) {
  if (typeof window === 'undefined' || !window.speechSynthesis) return;

  try {
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = language || 'hi-IN';
    utterance.rate = 1.0;

    utterance.onstart = () => {
      isPlayingTTS = true;
      ttsStarted();
      if (onStart) onStart();
    };

    utterance.onend = () => {
      isPlayingTTS = false;
      ttsStopped();
      if (onEnd) onEnd();
    };

    utterance.onerror = () => {
      isPlayingTTS = false;
      ttsStopped();
      if (onEnd) onEnd();
    };

    window.speechSynthesis.speak(utterance);
  } catch (e) {
    console.warn('[VoiceKhata] SpeechSynthesis fallback failed:', e);
  }
}

/**
 * Play queued audio buffers sequentially.
 */
function playNextInQueue(onComplete) {
  if (audioQueue.length === 0) {
    isPlayingTTS = false;
    ttsStopped();
    if (onComplete) onComplete();
    return;
  }

  const buffer = audioQueue.shift();
  currentSource = audioContext.createBufferSource();
  currentSource.buffer = buffer;
  currentSource.connect(audioContext.destination);

  currentSource.onended = () => {
    currentSource = null;
    playNextInQueue(onComplete);
  };

  currentSource.start(0);
}

/**
 * Immediately stop any active TTS playback (used by barge-in or manual cancel).
 */
export function stopTTSPlayback() {
  currentTTSGeneration++;
  if (activeFallbackTimer) {
    clearTimeout(activeFallbackTimer);
    activeFallbackTimer = null;
  }
  if (typeof window !== 'undefined' && window.speechSynthesis) {
    try { window.speechSynthesis.cancel(); } catch {}
  }

  if (currentSource) {
    try {
      currentSource.stop(0);
      currentSource.disconnect();
    } catch {}
    currentSource = null;
  }
  audioQueue = [];
  isPlayingTTS = false;
  ttsStopped();

  if (ttsSocket) {
    try {
      ttsSocket.close();
    } catch {}
    ttsSocket = null;
  }
}

export function isAudioRecording() {
  return isRecording;
}

export function isTTSSpeaking() {
  return isPlayingTTS;
}
