/**
 * Audio capture (getUserMedia + 16kHz 20ms PCM16 AudioWorklet) and
 * AudioWorklet ring-buffer playback controller with 60ms jitter buffer.
 */

export interface AudioEngineCallbacks {
  onCaptureFrame: (pcm: ArrayBuffer, rms: number) => void;
  onFirstAudioSample: (turnId: number, timestampMs: number) => void;
  onPlaybackStateChange: (playing: boolean) => void;
}

export class VoiceAudioEngine {
  private ctx: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private captureSource: MediaStreamAudioSourceNode | null = null;
  private captureNode: AudioWorkletNode | null = null;
  private silentSink: GainNode | null = null;
  private playbackNode: AudioWorkletNode | null = null;
  private playbackInitialized = false;
  private micInitialized = false;
  private isPlayingAudio = false;
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

  async ensureInitialized(): Promise<void> {
    if (!this.ctx) {
      this.ctx = new AudioContext({ sampleRate: 48000 });
    }
    if (this.ctx.state === "suspended") {
      await this.ctx.resume();
    }
    if (this.playbackInitialized) {
      return;
    }

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
  }

  async ensureMicCapture(): Promise<void> {
    await this.ensureInitialized();
    if (!this.ctx || this.micInitialized) {
      return;
    }

    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });

    await this.ctx.audioWorklet.addModule("/worklets/capture-processor.js");
    this.captureSource = this.ctx.createMediaStreamSource(this.stream);
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

    // Route through a 0-gain sink to destination so Chrome/Edge always pulls the capture graph
    this.silentSink = this.ctx.createGain();
    this.silentSink.gain.value = 0;
    this.captureSource.connect(this.captureNode);
    this.captureNode.connect(this.silentSink);
    this.silentSink.connect(this.ctx.destination);

    this.micInitialized = true;
  }

  setCaptureActive(active: boolean): void {
    this.captureNode?.port.postMessage({ type: "set-active", active });
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
    this.silentSink?.disconnect();
    this.playbackNode?.disconnect();
    this.captureSource = null;
    this.captureNode = null;
    this.silentSink = null;
    this.playbackNode = null;
    if (this.ctx) {
      await this.ctx.close();
      this.ctx = null;
    }
    this.playbackInitialized = false;
    this.micInitialized = false;
  }
}
