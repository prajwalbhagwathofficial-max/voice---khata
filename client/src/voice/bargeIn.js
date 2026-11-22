/**
 * Barge-In Controller — Voice Khata
 *
 * Handles barge-in: when the user starts speaking while TTS is playing,
 * the TTS audio should stop immediately so the user's voice is captured.
 *
 * Uses the VAD (Voice Activity Detection) threshold from settings.
 * This module manages state only — actual audio stop is done by the
 * voice pipeline that calls these functions.
 */

/**
 * @typedef {object} BargeInState
 * @property {boolean} isTTSPlaying - Whether TTS audio is currently playing
 * @property {boolean} isBargeInActive - Whether user has barged in
 * @property {number} bargeInCount - Total barge-ins this session
 * @property {function|null} onBargeIn - Callback when barge-in detected
 */

const state = {
  isTTSPlaying: false,
  isBargeInActive: false,
  bargeInCount: 0,
  onBargeIn: null,
};

/**
 * Mark TTS as playing. While playing, voice input is monitored for barge-in.
 */
export function ttsStarted() {
  state.isTTSPlaying = true;
  state.isBargeInActive = false;
}

/**
 * Mark TTS as finished (natural end, not barge-in).
 */
export function ttsStopped() {
  state.isTTSPlaying = false;
  state.isBargeInActive = false;
}

/**
 * Called when voice activity is detected during TTS playback.
 * Returns true if barge-in should trigger (TTS should stop).
 *
 * @param {number} energy - Audio energy level (0.0 – 1.0)
 * @param {number} threshold - VAD threshold from settings (default 0.7)
 * @returns {boolean} Whether to trigger barge-in
 */
export function checkBargeIn(energy, threshold = 0.7) {
  if (!state.isTTSPlaying) return false;
  if (state.isBargeInActive) return false; // Already barged in

  if (energy >= threshold) {
    state.isBargeInActive = true;
    state.isTTSPlaying = false;
    state.bargeInCount++;

    if (state.onBargeIn) {
      state.onBargeIn();
    }

    return true;
  }

  return false;
}

/**
 * Register a callback for barge-in events.
 * The callback should stop TTS playback and resume STT.
 */
export function onBargeIn(callback) {
  state.onBargeIn = callback;
}

/**
 * Get current barge-in state (for debugging / metrics).
 */
export function getBargeInState() {
  return { ...state, onBargeIn: undefined };
}

/**
 * Reset state (new session).
 */
export function resetBargeIn() {
  state.isTTSPlaying = false;
  state.isBargeInActive = false;
  state.bargeInCount = 0;
  state.onBargeIn = null;
}
