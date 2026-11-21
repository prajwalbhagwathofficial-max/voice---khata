"""Synthetic 16 kHz WAV clip & shop-noise generator for /eval (no real voices)."""

from __future__ import annotations

import json
import math
import random
from array import array
from pathlib import Path

from eval.audio import EXPECTED_SAMPLE_RATE, save_wav_pcm16

SYNTHETIC_LABELS: list[dict[str, object]] = [
    {
        "id": "clip_01_ramesh_credit_340",
        "wav": "clips/clip_01_ramesh_credit_340.wav",
        "lang": "hi-IN",
        "transcript": "Ramesh ke teen sau chaalis rupaye udhaar likh do",
        "expected_customer": "Ramesh Kumar",
        "expected_amount": 340,
        "expected_direction": "CREDIT",
        "correction": False,
    },
    {
        "id": "clip_02_meena_payment_250",
        "wav": "clips/clip_02_meena_payment_250.wav",
        "lang": "hi-IN",
        "transcript": "Meena ne dhai sau rupaye jama kar diye",
        "expected_customer": "Meena Devi",
        "expected_amount": 250,
        "expected_direction": "PAYMENT",
        "correction": False,
    },
    {
        "id": "clip_03_venkatesh_credit_150_te",
        "wav": "clips/clip_03_venkatesh_credit_150_te.wav",
        "lang": "te-IN",
        "transcript": "వెంకటేష్ ఖాతాలో నూట యాభై రూపాయలు రాయండి",
        "expected_customer": "వెంకటేష్",
        "expected_amount": 150,
        "expected_direction": "CREDIT",
        "correction": False,
    },
    {
        "id": "clip_04_suresh_credit_175",
        "wav": "clips/clip_04_suresh_credit_175.wav",
        "lang": "hi-IN",
        "transcript": "Suresh ke paune do sau rupaye udhaar",
        "expected_customer": "Suresh Patel",
        "expected_amount": 175,
        "expected_direction": "CREDIT",
        "correction": False,
    },
    {
        "id": "clip_05_anil_correction_void",
        "wav": "clips/clip_05_anil_correction_void.wav",
        "lang": "en-IN",
        "transcript": "Wrong entry, cancel Anil last entry",
        "expected_customer": "Anil Nair",
        "expected_amount": 125,
        "expected_direction": "VOID",
        "correction": True,
    },
    {
        "id": "clip_06_lakshmi_correction_350",
        "wav": "clips/clip_06_lakshmi_correction_350.wav",
        "lang": "hi-IN",
        "transcript": "Nahi nahi Lakshmi ka saade teen sau rupaye karo",
        "expected_customer": "Lakshmi Rao",
        "expected_amount": 350,
        "expected_direction": "CREDIT",
        "correction": True,
    },
]


def _make_speechlike_tone(duration_s: float, base_hz: float, seed: int) -> bytes:
    """Generate a synthetic 16 kHz PCM16 speech-band modulated tone (purely synthetic)."""
    n = int(EXPECTED_SAMPLE_RATE * duration_s)
    rng = random.Random(seed)
    out = array("h", [0] * n)
    for i in range(n):
        t = i / EXPECTED_SAMPLE_RATE
        # Syllable-like amplitude envelope (3-4 Hz)
        env = 0.5 * (1.0 + math.sin(2.0 * math.pi * 3.5 * t))
        sig = (
            0.55 * math.sin(2.0 * math.pi * base_hz * t)
            + 0.30 * math.sin(2.0 * math.pi * (base_hz * 2.0) * t)
            + 0.15 * rng.uniform(-1.0, 1.0)
        )
        out[i] = int(max(-32767, min(32767, round(env * sig * 12000))))
    return out.tobytes()


def _make_kirana_noise(duration_s: float, seed: int = 99) -> bytes:
    """Generate synthetic band-limited shop ambience noise at 16 kHz PCM16."""
    n = int(EXPECTED_SAMPLE_RATE * duration_s)
    rng = random.Random(seed)
    out = array("h", [0] * n)
    prev = 0.0
    for i in range(n):
        t = i / EXPECTED_SAMPLE_RATE
        # Simple 1-pole low-pass filtered white noise + soft 50Hz fan hum
        white = rng.uniform(-1.0, 1.0)
        prev = 0.72 * prev + 0.28 * white
        hum = 0.15 * math.sin(2.0 * math.pi * 50.0 * t)
        val = (prev + hum) * 9000.0
        out[i] = int(max(-32767, min(32767, round(val))))
    return out.tobytes()


def ensure_synthetic_eval_assets(eval_dir: str | Path = "eval") -> Path:
    """Generate synthetic 16 kHz WAV clips, noise WAV, and labels.json inside `eval_dir`."""
    root = Path(eval_dir)
    clips_dir = root / "clips"
    noise_dir = root / "noise"
    clips_dir.mkdir(parents=True, exist_ok=True)
    noise_dir.mkdir(parents=True, exist_ok=True)

    for idx, entry in enumerate(SYNTHETIC_LABELS):
        wav_path = root / str(entry["wav"])
        if not wav_path.exists():
            pcm = _make_speechlike_tone(duration_s=0.36, base_hz=240.0 + idx * 35.0, seed=100 + idx)
            save_wav_pcm16(wav_path, pcm)

    noise_path = noise_dir / "kirana_ambience.wav"
    if not noise_path.exists():
        save_wav_pcm16(noise_path, _make_kirana_noise(duration_s=2.0))

    labels_path = root / "labels.json"
    if not labels_path.exists():
        labels_path.write_text(
            json.dumps({"clips": SYNTHETIC_LABELS}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return labels_path


if __name__ == "__main__":
    path = ensure_synthetic_eval_assets(Path(__file__).resolve().parent)
    print(f"Synthetic eval assets ready: {path}")
