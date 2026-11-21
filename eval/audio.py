"""16 kHz mono PCM16 WAV loader, exact SNR noise mixer, and 20 ms frame slicer."""

from __future__ import annotations

import math
import struct
import wave
from array import array
from pathlib import Path

EXPECTED_SAMPLE_RATE = 16000
EXPECTED_SAMPLE_WIDTH = 2  # PCM16
EXPECTED_CHANNELS = 1
FRAME_MS = 20
FRAME_SAMPLES = (EXPECTED_SAMPLE_RATE * FRAME_MS) // 1000  # 320 samples
FRAME_BYTES = FRAME_SAMPLES * EXPECTED_SAMPLE_WIDTH  # 640 bytes
SILENCE_PCM16_THRESHOLD = 3


def load_wav_pcm16(path: str | Path) -> bytes:
    """Load a 16 kHz mono 16-bit PCM WAV file and return raw PCM16 little-endian bytes."""
    wav_path = Path(path)
    with wave.open(str(wav_path), "rb") as wf:
        if wf.getnchannels() != EXPECTED_CHANNELS:
            raise ValueError(f"{wav_path}: expected mono (1 channel), got {wf.getnchannels()}")
        if wf.getsampwidth() != EXPECTED_SAMPLE_WIDTH:
            raise ValueError(f"{wav_path}: expected 16-bit PCM (2 bytes), got {wf.getsampwidth()}")
        if wf.getframerate() != EXPECTED_SAMPLE_RATE:
            raise ValueError(
                f"{wav_path}: expected {EXPECTED_SAMPLE_RATE} Hz, got {wf.getframerate()} Hz"
            )
        return wf.readframes(wf.getnframes())


def save_wav_pcm16(path: str | Path, pcm_bytes: bytes) -> None:
    """Write raw 16 kHz mono PCM16 little-endian bytes to a WAV file."""
    wav_path = Path(path)
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(EXPECTED_CHANNELS)
        wf.setsampwidth(EXPECTED_SAMPLE_WIDTH)
        wf.setframerate(EXPECTED_SAMPLE_RATE)
        wf.writeframes(pcm_bytes)


def _bytes_to_int16(pcm_bytes: bytes) -> array:
    usable = len(pcm_bytes) - (len(pcm_bytes) % 2)
    arr = array("h")
    arr.frombytes(pcm_bytes[:usable])
    return arr


def rms_power(pcm_bytes: bytes) -> float:
    """Compute mean-square power of PCM16 samples."""
    samples = _bytes_to_int16(pcm_bytes)
    if not samples:
        return 0.0
    total = sum(s * s for s in samples)
    return total / len(samples)


def mix_noise_snr(signal_pcm: bytes, noise_pcm: bytes, snr_db: float) -> bytes:
    """Mix `noise_pcm` into `signal_pcm` at an exact target SNR in decibels.

    Loops `noise_pcm` cleanly if shorter than `signal_pcm`. Clamps output to int16.
    """
    sig = _bytes_to_int16(signal_pcm)
    noi = _bytes_to_int16(noise_pcm)
    if not sig:
        return b""
    if not noi:
        return signal_pcm

    n_len = len(sig)
    # Tile noise to match signal length
    tiled = array("h", (noi[i % len(noi)] for i in range(n_len)))

    sig_power = sum(s * s for s in sig) / n_len
    noi_power = sum(w * w for w in tiled) / n_len
    if sig_power <= 0.0 or noi_power <= 0.0:
        return signal_pcm

    target_ratio = 10.0 ** (snr_db / 10.0)
    scale = math.sqrt(sig_power / (noi_power * target_ratio))

    out = array("h", [0] * n_len)
    for i in range(n_len):
        mixed = int(round(sig[i] + scale * tiled[i]))
        if mixed > 32767:
            mixed = 32767
        elif mixed < -32768:
            mixed = -32768
        out[i] = mixed
    return out.tobytes()


def slice_pcm16_frames(pcm_bytes: bytes, frame_bytes: int = FRAME_BYTES) -> list[bytes]:
    """Slice PCM16 bytes into exact 20 ms frames (zero-padding final short frame)."""
    if not pcm_bytes:
        return []
    frames: list[bytes] = []
    for offset in range(0, len(pcm_bytes), frame_bytes):
        chunk = pcm_bytes[offset : offset + frame_bytes]
        if len(chunk) < frame_bytes:
            chunk = chunk + (b"\x00" * (frame_bytes - len(chunk)))
        frames.append(chunk)
    return frames


def has_non_silent_pcm16(pcm_bytes: bytes, threshold: int = SILENCE_PCM16_THRESHOLD) -> bool:
    """Return True if any 16-bit LE sample exceeds the silence threshold."""
    usable = len(pcm_bytes) - (len(pcm_bytes) % 2)
    for offset in range(0, usable, 2):
        sample = struct.unpack_from("<h", pcm_bytes, offset)[0]
        if abs(sample) > threshold:
            return True
    return False
