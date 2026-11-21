/**
 * AudioWorkletProcessor that downsamples microphone Float32 audio to
 * 16kHz 16-bit signed little-endian mono PCM and emits ~100ms chunks (1600 samples).
 */
class Pcm16kProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._targetRate = 16000;
    this._chunkSamples = 1600; // 100ms at 16kHz
    this._buffer = new Int16Array(this._chunkSamples);
    this._writePos = 0;
    this._phase = 0;
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0] || input[0].length === 0) {
      return true;
    }

    const channel = input[0];
    const ratio = sampleRate / this._targetRate;
    let sumSq = 0;

    while (this._phase < channel.length) {
      const idx = Math.floor(this._phase);
      const frac = this._phase - idx;
      const s0 = channel[idx] || 0;
      const s1 = idx + 1 < channel.length ? channel[idx + 1] : s0;
      const sample = s0 + (s1 - s0) * frac;

      sumSq += sample * sample;
      const clamped = Math.max(-1, Math.min(1, sample));
      this._buffer[this._writePos++] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;

      if (this._writePos >= this._chunkSamples) {
        const out = this._buffer.slice(0, this._chunkSamples);
        const rms = Math.sqrt(sumSq / this._chunkSamples);
        this.port.postMessage(
          { pcm: out.buffer, rms },
          [out.buffer]
        );
        this._writePos = 0;
        sumSq = 0;
      }

      this._phase += ratio;
    }

    this._phase -= channel.length;
    return true;
  }
}

registerProcessor("pcm-16k-processor", Pcm16kProcessor);
