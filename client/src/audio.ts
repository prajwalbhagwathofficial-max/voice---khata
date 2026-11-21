/**
 * Audio capture (getUserMedia + 16kHz 20ms PCM16 AudioWorklet) and
 * AudioWorklet ring-buffer playback controller with 60ms jitter buffer.
 */

export interface AudioEngineCallbacks {
  onCaptureFrame: (pcm: ArrayBuffer, rms: number) => void;
  onFirstAudioSample: (turnId: number, timestampMs: number) => void;
  onPlaybackStateChange: (playing: boolean) => void;
}

function getAudioContextClass(): typeof AudioContext | null {
  if (typeof window === "undefined") return null;
  return (
    window.AudioContext ||
    (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext ||
    null
  );
}

export class VoiceAudioEngine {
  private ctx: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private captureSource: MediaStreamAudioSourceNode | null = null;
  private captureNode: AudioWorkletNode | null = null;
  private scriptNode: ScriptProcessorNode | null = null;
  private silentSink: GainNode | null = null;
  private playbackNode: AudioWorkletNode | null = null;
  private playbackInitialized = false;
  private micInitialized = false;
  private isPlayingAudio = false;
  private captureActive = false;
  private isUsingScriptFallback = false;
  private readonly callbacks: AudioEngineCallbacks;

  constructor(callbacks: AudioEngineCallbacks) {
    this.callbacks = callbacks;
  }

  get playing(): boolean {
    return this.isPlayingAudio;
  }

  get micReady(): boolean {
    return this.micInitialized;
  }

  get usingScriptFallback(): boolean {
    return this.isUsingScriptFallback;
  }

  /**
   * Resume audio context on user gesture (crucial for iOS Safari / Android Chrome).
   */
  async resumeContext(): Promise<void> {
    if (this.ctx && this.ctx.state === "suspended") {
      try {
        await this.ctx.resume();
      } catch (err) {
        console.warn("AudioContext resume warning:", err);
      }
    }
  }

  async ensureInitialized(): Promise<void> {
    const AudioCtx = getAudioContextClass();
    if (!AudioCtx) {
      throw new Error("Web Audio API is not supported in this browser.");
    }

    if (!this.ctx) {
      try {
        // Attempt 48kHz first; if mobile hardware fails, fallback to native rate
        this.ctx = new AudioCtx({ sampleRate: 48000 });
      } catch {
        this.ctx = new AudioCtx();
      }
    }

    await this.resumeContext();

    if (this.playbackInitialized) {
      return;
    }

    try {
      await this.ctx.audioWorklet.addModule("/worklets/playback-processor.js");
      this.playbackNode = new AudioWorkletNode(this.ctx, "vk-playback-processor", {
        numberOfInputs: 0,
        numberOfOutputs: 1,
        outputChannelCount: [1],
      });
      this.playbackNode.port.onmessage = (event: MessageEvent) => {
        const msg = event.data;
        if (!msg) return;
        if (msg.type === "first-audio" && typeof msg.turnId === "number") {
          this.callbacks.onFirstAudioSample(msg.turnId, performance.now());
        } else if (msg.type === "playback-state") {
          this.isPlayingAudio = Boolean(msg.playing);
          this.callbacks.onPlaybackStateChange(this.isPlayingAudio);
        }
      };
      this.playbackNode.connect(this.ctx.destination);
      this.playbackInitialized = true;
    } catch (err) {
      console.warn("Playback AudioWorklet load failed, continuing with audio input:", err);
      this.playbackInitialized = false;
    }
  }

  async ensureMicCapture(): Promise<void> {
    await this.ensureInitialized();
    await this.resumeContext();

    if (!this.ctx || this.micInitialized) {
      return;
    }

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      throw new Error(
        "Microphone access requires a secure HTTPS connection or is not supported by your browser."
      );
    }

    // Try high-quality mobile constraints, fall back to basic audio on failure
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
    } catch {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: true,
      });
    }

    this.captureSource = this.ctx.createMediaStreamSource(this.stream);

    // Try AudioWorklet first; if worklets are restricted, use ScriptProcessor fallback
    let workletLoaded = false;
    try {
      await this.ctx.audioWorklet.addModule("/worklets/capture-processor.js");
      this.captureNode = new AudioWorkletNode(this.ctx, "vk-capture-processor", {
        numberOfInputs: 1,
        numberOfOutputs: 1,
        outputChannelCount: [1],
      });
      this.captureNode.port.onmessage = (event: MessageEvent) => {
        const msg = event.data;
        if (msg && msg.pcm instanceof ArrayBuffer) {
          const rms = typeof msg.rms === "number" ? msg.rms : 0;
          this.callbacks.onCaptureFrame(msg.pcm, rms);
        }
      };

      this.silentSink = this.ctx.createGain();
      this.silentSink.gain.value = 0;
      this.captureSource.connect(this.captureNode);
      this.captureNode.connect(this.silentSink);
      this.silentSink.connect(this.ctx.destination);
      workletLoaded = true;
      this.isUsingScriptFallback = false;
    } catch (workletErr) {
      console.warn("Capture AudioWorklet unavailable, activating mobile ScriptProcessor fallback:", workletErr);
      this._setupScriptProcessorFallback();
      workletLoaded = true;
    }

    if (workletLoaded) {
      this.micInitialized = true;
    }
  }

  private _setupScriptProcessorFallback(): void {
    if (!this.ctx || !this.captureSource) return;

    const bufferSize = 4096;
    const scriptNode = this.ctx.createScriptProcessor(bufferSize, 1, 1);
    this.scriptNode = scriptNode;
    this.isUsingScriptFallback = true;

    const targetRate = 16000;
    const frameSamples = 320; // 20ms at 16kHz
    const frame = new Int16Array(frameSamples);
    let writePos = 0;
    let phase = 0;
    let sumSq = 0;

    scriptNode.onaudioprocess = (e: AudioProcessingEvent) => {
      if (!this.captureActive) return;
      const input = e.inputBuffer.getChannelData(0);
      if (!input || input.length === 0) return;

      const currentRate = this.ctx?.sampleRate || 48000;
      const ratio = currentRate / targetRate;

      while (phase < input.length) {
        const idx = Math.floor(phase);
        const frac = phase - idx;
        const s0 = input[idx] || 0;
        const s1 = idx + 1 < input.length ? input[idx + 1] : s0;
        const sample = s0 + (s1 - s0) * frac;
        const clamped = Math.max(-1, Math.min(1, sample));

        sumSq += clamped * clamped;
        frame[writePos++] = clamped < 0 ? Math.round(clamped * 32768) : Math.round(clamped * 32767);

        if (writePos >= frameSamples) {
          const out = frame.slice(0, frameSamples);
          const rms = Math.sqrt(sumSq / frameSamples);
          this.callbacks.onCaptureFrame(out.buffer, rms);
          writePos = 0;
          sumSq = 0;
        }

        phase += ratio;
      }
      phase -= input.length;
    };

    this.silentSink = this.ctx.createGain();
    this.silentSink.gain.value = 0;
    this.captureSource.connect(scriptNode);
    scriptNode.connect(this.silentSink);
    this.silentSink.connect(this.ctx.destination);
  }

  setCaptureActive(active: boolean): void {
    this.captureActive = active;
    if (this.captureNode) {
      this.captureNode.port.postMessage({ type: "set-active", active });
    }
  }

  pushPlaybackPcm(turnId: number, pcm: ArrayBuffer, inputSampleRate: number): void {
    if (!this.playbackNode) {
      return;
    }
    this.playbackNode.port.postMessage(
      {
        type: "push",
        turnId,
        pcm,
        inputSampleRate,
      },
      [pcm]
    );
  }

  markEndOfTurn(turnId: number): void {
    this.playbackNode?.port.postMessage({ type: "eos", turnId });
  }

  flushPlayback(): void {
    this.isPlayingAudio = false;
    this.callbacks.onPlaybackStateChange(false);
    this.playbackNode?.port.postMessage({ type: "flush" });
  }

  async close(): Promise<void> {
    this.setCaptureActive(false);
    this.flushPlayback();
    if (this.stream) {
      this.stream.getTracks().forEach((t) => t.stop());
      this.stream = null;
    }
    this.captureSource?.disconnect();
    this.captureNode?.disconnect();
    this.scriptNode?.disconnect();
    this.silentSink?.disconnect();
    this.playbackNode?.disconnect();
    this.captureSource = null;
    this.captureNode = null;
    this.scriptNode = null;
    this.silentSink = null;
    this.playbackNode = null;
    if (this.ctx) {
      try {
        await this.ctx.close();
      } catch {}
      this.ctx = null;
    }
    this.playbackInitialized = false;
    this.micInitialized = false;
  }
}

