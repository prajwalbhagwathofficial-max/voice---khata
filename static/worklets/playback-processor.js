/**
 * AudioWorkletProcessor: ring buffer playback with a 60 ms jitter buffer,
 * immediate zero-on-flush/interrupt, and first non-silent sample detection.
 */
class PlaybackProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._capacity = Math.max(sampleRate * 12, 16000 * 12); // 12 seconds ring capacity
    this._ring = new Float32Array(this._capacity);
    this._readPos = 0;
    this._writePos = 0;
    this._buffered = 0;

    this._jitterSamples = Math.round(sampleRate * 0.06); // 60 ms jitter buffer
    this._primed = false;
    this._endOfStream = false;
    this._activeTurnId = null;
    this._reportedFirstAudio = false;
    this._wasPlaying = false;
    this._silenceThreshold = 1e-4;

    this.port.onmessage = (event) => {
      const msg = event.data;
      if (!msg) return;

      if (msg.type === "flush") {
        this._flush();
        return;
      }

      if (msg.type === "eos") {
        if (msg.turnId === this._activeTurnId) {
          this._endOfStream = true;
        }
        return;
      }

      if (msg.type === "push") {
        const { turnId, pcm, inputSampleRate } = msg;
        if (turnId !== this._activeTurnId) {
          this._activeTurnId = turnId;
          this._reportedFirstAudio = false;
          this._endOfStream = false;
        }
        this._enqueuePcm16(new Int16Array(pcm), inputSampleRate || sampleRate);
      }
    };
  }

  _flush() {
    this._ring.fill(0);
    this._readPos = 0;
    this._writePos = 0;
    this._buffered = 0;
    this._primed = false;
    this._endOfStream = false;
    this._activeTurnId = null;
    this._reportedFirstAudio = false;
    if (this._wasPlaying) {
      this._wasPlaying = false;
      this.port.postMessage({ type: "playback-state", playing: false });
    }
  }

  _writeSample(val) {
    if (this._buffered >= this._capacity) {
      // Advance read pointer to drop oldest sample on overflow
      this._readPos = (this._readPos + 1) % this._capacity;
      this._buffered--;
    }
    this._ring[this._writePos] = val;
    this._writePos = (this._writePos + 1) % this._capacity;
    this._buffered++;
  }

  _enqueuePcm16(int16, inputRate) {
    const inLen = int16.length;
    if (inLen === 0) return;

    if (inputRate === sampleRate) {
      for (let i = 0; i < inLen; i++) {
        this._writeSample(int16[i] / 32768);
      }
      return;
    }

    const ratio = inputRate / sampleRate;
    const outLen = Math.max(1, Math.round(inLen / ratio));
    for (let i = 0; i < outLen; i++) {
      const srcPos = i * ratio;
      const idx = Math.floor(srcPos);
      const frac = srcPos - idx;
      const s0 = int16[Math.min(idx, inLen - 1)] / 32768;
      const s1 = int16[Math.min(idx + 1, inLen - 1)] / 32768;
      this._writeSample(s0 + (s1 - s0) * frac);
    }
  }

  process(_inputs, outputs) {
    const output = outputs[0];
    if (!output || !output[0]) {
      return true;
    }
    const channel = output[0];

    if (!this._primed) {
      if (this._buffered >= this._jitterSamples || (this._endOfStream && this._buffered > 0)) {
        this._primed = true;
      } else {
        channel.fill(0);
        return true;
      }
    }

    if (this._buffered === 0) {
      channel.fill(0);
      this._primed = false;
      if (this._wasPlaying) {
        this._wasPlaying = false;
        this.port.postMessage({ type: "playback-state", playing: false });
      }
      return true;
    }

    if (!this._wasPlaying) {
      this._wasPlaying = true;
      this.port.postMessage({ type: "playback-state", playing: true });
    }

    let heardNonSilent = false;
    for (let i = 0; i < channel.length; i++) {
      if (this._buffered > 0) {
        const sample = this._ring[this._readPos];
        this._ring[this._readPos] = 0;
        this._readPos = (this._readPos + 1) % this._capacity;
        this._buffered--;
        channel[i] = sample;
        if (!this._reportedFirstAudio && Math.abs(sample) > this._silenceThreshold) {
          heardNonSilent = true;
        }
      } else {
        channel[i] = 0;
      }
    }

    if (heardNonSilent && !this._reportedFirstAudio && this._activeTurnId !== null) {
      this._reportedFirstAudio = true;
      this.port.postMessage({ type: "first-audio", turnId: this._activeTurnId });
    }

    if (this._buffered === 0 && this._endOfStream) {
      this._primed = false;
      if (this._wasPlaying) {
        this._wasPlaying = false;
        this.port.postMessage({ type: "playback-state", playing: false });
      }
    }

    return true;
  }
}

registerProcessor("vk-playback-processor", PlaybackProcessor);
