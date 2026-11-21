/**
 * AudioWorkletProcessor: downsamples microphone Float32 mono audio to
 * 16 kHz mono PCM16 (signed 16-bit LE) and posts exact 20 ms frames (320 samples / 640 bytes)
 * along with RMS energy so the UI can confirm live mic signal.
 */
class CaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._targetRate = 16000;
    this._frameSamples = 320; // 20 ms at 16 kHz
    this._frame = new Int16Array(this._frameSamples);
    this._writePos = 0;
    this._phase = 0;
    this._active = false;
    this._sumSq = 0;

    this.port.onmessage = (event) => {
      const msg = event.data;
      if (msg && msg.type === "set-active") {
        this._active = Boolean(msg.active);
        if (!this._active) {
          this._writePos = 0;
          this._phase = 0;
          this._sumSq = 0;
        }
      }
    };
  }

  process(inputs, outputs) {
    // Keep output silent (connected via 0-gain node so Chromium always pulls inputs)
    if (outputs && outputs[0] && outputs[0][0]) {
      outputs[0][0].fill(0);
    }
    if (!this._active) {
      return true;
    }
    const input = inputs[0];
    if (!input || !input[0] || input[0].length === 0) {
      return true;
    }

    const channel = input[0];
    const ratio = sampleRate / this._targetRate;

    while (this._phase < channel.length) {
      const idx = Math.floor(this._phase);
      const frac = this._phase - idx;
      const s0 = channel[idx] || 0;
      const s1 = idx + 1 < channel.length ? channel[idx + 1] : s0;
      const sample = s0 + (s1 - s0) * frac;

      const clamped = Math.max(-1, Math.min(1, sample));
      this._sumSq += clamped * clamped;
      this._frame[this._writePos++] =
        clamped < 0 ? Math.round(clamped * 32768) : Math.round(clamped * 32767);

      if (this._writePos >= this._frameSamples) {
        const out = this._frame.slice(0, this._frameSamples);
        const rms = Math.sqrt(this._sumSq / this._frameSamples);
        this.port.postMessage({ pcm: out.buffer, rms }, [out.buffer]);
        this._writePos = 0;
        this._sumSq = 0;
      }

      this._phase += ratio;
    }

    this._phase -= channel.length;
    return true;
  }
}

registerProcessor("vk-capture-processor", CaptureProcessor);
