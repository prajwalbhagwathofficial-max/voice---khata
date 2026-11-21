"""Stream labeled 16 kHz WAV clips through the real WebSocket pipeline (/ws/session).

Supports SNR noise mixing (clean, 20, 10, 5 dB) from /eval/noise, --max-clips
credit cap, and --mock dry-run mode over a real local WebSocket server.
Caches nothing that hides real latency.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import struct
import time
from array import array
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import websockets

from eval.audio import (
    EXPECTED_SAMPLE_RATE,
    has_non_silent_pcm16,
    load_wav_pcm16,
    mix_noise_snr,
    slice_pcm16_frames,
)
from eval.metrics import ClipResult, compute_noise_summaries, format_markdown_table
from server.nlu.numbers import parse_amount

FRAME_TYPE_UPSTREAM_AUDIO = 0x01
FRAME_TYPE_DOWNSTREAM_AUDIO = 0x02


@dataclass(frozen=True, slots=True)
class LabeledClip:
    id: str
    wav_path: Path
    expected_customer: str | None
    expected_amount: int | None
    expected_direction: str
    correction: bool
    transcript: str = ""
    lang: str = "hi-IN"


def load_labels(labels_path: str | Path, *, max_clips: int | None = None) -> list[LabeledClip]:
    """Load labeled WAV entries from labels.json, honoring --max-clips."""
    path = Path(labels_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    entries = raw["clips"] if isinstance(raw, dict) and "clips" in raw else raw
    if not isinstance(entries, list):
        raise ValueError("labels.json must contain a list or {'clips': [...]}")

    if max_clips is not None:
        if max_clips < 0:
            raise ValueError("--max-clips must be >= 0")
        entries = entries[:max_clips]

    clips: list[LabeledClip] = []
    for item in entries:
        wav_rel = Path(str(item["wav"]))
        wav_abs = wav_rel if wav_rel.is_absolute() else (path.parent / wav_rel)
        exp_amt = item.get("expected_amount")
        clips.append(
            LabeledClip(
                id=str(item["id"]),
                wav_path=wav_abs,
                expected_customer=item.get("expected_customer"),
                expected_amount=int(exp_amt) if exp_amt is not None else None,
                expected_direction=str(item["expected_direction"]).upper(),
                correction=bool(item.get("correction", False)),
                transcript=str(item.get("transcript", "")),
                lang=str(item.get("lang", "hi-IN")),
            )
        )
    return clips


def load_noise_files(noise_dir: str | Path) -> list[Path]:
    """Return sorted 16 kHz WAV noise files in /eval/noise."""
    root = Path(noise_dir)
    if not root.exists():
        return []
    return sorted(p for p in root.iterdir() if p.is_file() and p.suffix.lower() == ".wav")


def parse_snr_levels(spec: str) -> list[tuple[str, float | None]]:
    """Parse comma-separated SNR spec like 'clean,20,10,5' into (label, snr_db|None)."""
    out: list[tuple[str, float | None]] = []
    for raw in spec.split(","):
        token = raw.strip().lower()
        if not token:
            continue
        if token == "clean":
            out.append(("clean", None))
        else:
            val = float(token.replace("db", ""))
            label = f"{int(val)}dB" if val.is_integer() else f"{val}dB"
            out.append((label, val))
    return out


def encode_upstream_frame(pcm_20ms: bytes) -> bytes:
    return bytes([FRAME_TYPE_UPSTREAM_AUDIO]) + pcm_20ms


def decode_downstream_frame(data: bytes) -> tuple[int, bytes] | None:
    if len(data) < 5 or data[0] != FRAME_TYPE_DOWNSTREAM_AUDIO:
        return None
    turn_id = struct.unpack_from(">I", data, 1)[0]
    return turn_id, data[5:]


def _make_mock_tts_tone_pcm(duration_s: float = 0.12, freq_hz: float = 440.0) -> bytes:
    n = int(EXPECTED_SAMPLE_RATE * duration_s)
    samples = array(
        "h",
        (int(round(math.sin(2.0 * math.pi * freq_hz * (i / EXPECTED_SAMPLE_RATE)) * 8000)) for i in range(n)),
    )
    return samples.tobytes()


@asynccontextmanager
async def _run_mock_ws_server(clips: list[LabeledClip]):
    """Run a real local WebSocket server speaking the exact /ws/session protocol for --mock dry runs."""
    clip_map = {c.id: c for c in clips}
    tone_pcm = _make_mock_tts_tone_pcm()

    async def handler(ws: Any) -> None:
        turn_id = 0
        active_clip: LabeledClip | None = None
        received_audio_bytes = 0

        async for msg in ws:
            if isinstance(msg, bytes):
                if len(msg) > 1 and msg[0] == FRAME_TYPE_UPSTREAM_AUDIO:
                    received_audio_bytes += len(msg) - 1
                continue

            data = json.loads(msg)
            msg_type = data.get("t")

            if msg_type == "session.start":
                await ws.send(
                    json.dumps(
                        {
                            "t": "ready",
                            "session_id": "eval-mock",
                            "turn_id": turn_id,
                            "sample_rate": EXPECTED_SAMPLE_RATE,
                        }
                    )
                )
            elif msg_type == "ptt.down":
                received_audio_bytes = 0
                active_clip = clip_map.get(str(data.get("clip_id", "")))
            elif msg_type == "ptt.up":
                turn_id += 1
                await ws.send(
                    json.dumps(
                        {
                            "t": "turn.start",
                            "turn_id": turn_id,
                            "sample_rate": EXPECTED_SAMPLE_RATE,
                        }
                    )
                )

                # Simulate realistic minimal processing + run real server.nlu.numbers parser on transcript
                await asyncio.sleep(0.005)
                transcript = active_clip.transcript if active_clip else ""
                parsed_amt = parse_amount(transcript) if transcript else None
                if parsed_amt is None and active_clip is not None:
                    parsed_amt = active_clip.expected_amount

                direction = active_clip.expected_direction if active_clip else "UNKNOWN"
                customer = active_clip.expected_customer if active_clip else None
                is_corr = active_clip.correction if active_clip else False

                await ws.send(
                    json.dumps(
                        {
                            "t": "transcript",
                            "kind": "final",
                            "text": transcript,
                            "turn_id": turn_id,
                        }
                    )
                )
                await ws.send(
                    json.dumps(
                        {
                            "t": "action",
                            "turn_id": turn_id,
                            "type": direction,
                            "customer": customer,
                            "amount_inr": parsed_amt,
                            "correction_applied": is_corr,
                            "received_audio_bytes": received_audio_bytes,
                        }
                    )
                )

                # Stream downstream 0x02 binary PCM16 frames with big-endian u32 turn_id
                for frame in slice_pcm16_frames(tone_pcm):
                    header = struct.pack(">BI", FRAME_TYPE_DOWNSTREAM_AUDIO, turn_id)
                    await ws.send(header + frame)

                await ws.send(json.dumps({"t": "tts.end", "turn_id": turn_id}))

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        sock = next(iter(server.sockets))
        port = sock.getsockname()[1]
        yield f"ws://127.0.0.1:{port}/ws/session"


async def stream_single_clip(
    ws: Any,
    clip: LabeledClip,
    *,
    noise_level: str,
    noise_path: Path | None,
    snr_db: float | None,
    pace_realtime: bool = False,
    turn_timeout_s: float = 15.0,
) -> ClipResult:
    """Stream one WAV clip (freshly loaded and noise-mixed, zero caching) over WebSocket."""
    # Always read fresh from disk and mix fresh — never cache across levels or runs.
    pcm = load_wav_pcm16(clip.wav_path)
    if snr_db is not None and noise_path is not None:
        noise_pcm = load_wav_pcm16(noise_path)
        pcm = mix_noise_snr(pcm, noise_pcm, snr_db)

    frames = slice_pcm16_frames(pcm)

    await ws.send(json.dumps({"t": "ptt.down", "clip_id": clip.id, "lang": clip.lang}))
    for frame in frames:
        await ws.send(encode_upstream_frame(frame))
        if pace_realtime:
            await asyncio.sleep(0.020)

    ptt_up_mono_ns = time.monotonic_ns()
    await ws.send(json.dumps({"t": "ptt.up"}))

    current_turn_id: int | None = None
    first_audio_mono_ns: int | None = None
    pred_customer: str | None = None
    pred_amount: int | None = None
    pred_direction: str | None = None
    correction_succeeded = False
    error_msg: str | None = None

    deadline = time.monotonic() + turn_timeout_s
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            error_msg = "timeout waiting for turn completion"
            break
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        except TimeoutError:
            error_msg = "timeout waiting for turn completion"
            break

        if isinstance(raw, bytes):
            decoded = decode_downstream_frame(raw)
            if decoded is None:
                continue
            turn_id, audio_pcm = decoded
            if current_turn_id is not None and turn_id != current_turn_id:
                continue
            if first_audio_mono_ns is None and has_non_silent_pcm16(audio_pcm):
                first_audio_mono_ns = time.monotonic_ns()
            continue

        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue

        t = msg.get("t")
        if t in ("turn.start", "tts.start"):
            if isinstance(msg.get("turn_id"), int):
                current_turn_id = msg["turn_id"]
        elif t == "action":
            payload = msg.get("action") if isinstance(msg.get("action"), dict) else msg
            pred_customer = payload.get("customer") or payload.get("customer_name")
            raw_amt = payload.get("amount_inr")
            pred_amount = int(raw_amt) if isinstance(raw_amt, int) and not isinstance(raw_amt, bool) else None
            raw_dir = payload.get("type") or payload.get("intent") or payload.get("direction")
            pred_direction = str(raw_dir).upper() if raw_dir else None

            if clip.correction:
                explicit_corr = payload.get("correction_applied")
                amt_ok = pred_amount == clip.expected_amount
                dir_ok = pred_direction == clip.expected_direction
                correction_succeeded = (
                    bool(explicit_corr) and amt_ok and dir_ok
                    if explicit_corr is not None
                    else (amt_ok and dir_ok)
                )
        elif t == "error":
            error_msg = str(msg.get("message", "server error"))
            break
        elif t == "tts.end":
            break

    ttfa_ms = (
        round((first_audio_mono_ns - ptt_up_mono_ns) / 1_000_000.0, 2)
        if first_audio_mono_ns is not None
        else None
    )

    return ClipResult(
        clip_id=clip.id,
        noise_level=noise_level,
        expected_customer=clip.expected_customer,
        expected_amount=clip.expected_amount,
        expected_direction=clip.expected_direction,
        is_correction=clip.correction,
        predicted_customer=pred_customer,
        predicted_amount=pred_amount,
        predicted_direction=pred_direction,
        correction_succeeded=correction_succeeded,
        ttfa_ms=ttfa_ms,
        error=error_msg,
    )


async def run_evaluation(
    *,
    ws_url: str | None = None,
    labels_path: str | Path = "eval/labels.json",
    noise_dir: str | Path = "eval/noise",
    snr_spec: str = "clean,20,10,5",
    max_clips: int | None = None,
    use_mock: bool = False,
    pace_realtime: bool = False,
    turn_timeout_s: float = 15.0,
) -> tuple[list[ClipResult], str]:
    """Run the full WebSocket evaluation across configured SNR noise levels."""
    clips = load_labels(labels_path, max_clips=max_clips)
    snr_levels = parse_snr_levels(snr_spec)
    noise_files = load_noise_files(noise_dir)

    if any(snr is not None for _, snr in snr_levels) and not noise_files:
        raise FileNotFoundError(f"No 16 kHz noise WAV files found in {noise_dir}")

    async def _execute(target_url: str) -> list[ClipResult]:
        all_results: list[ClipResult] = []
        async with websockets.connect(target_url, open_timeout=10) as ws:
            await ws.send(json.dumps({"t": "session.start"}))
            # Wait for initial ready frame
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
                if isinstance(raw, str) and json.loads(raw).get("t") == "ready":
                    break

            for level_label, snr_db in snr_levels:
                for idx, clip in enumerate(clips):
                    noise_file = noise_files[idx % len(noise_files)] if snr_db is not None else None
                    res = await stream_single_clip(
                        ws,
                        clip,
                        noise_level=level_label,
                        noise_path=noise_file,
                        snr_db=snr_db,
                        pace_realtime=pace_realtime,
                        turn_timeout_s=turn_timeout_s,
                    )
                    all_results.append(res)
        return all_results

    if use_mock:
        async with _run_mock_ws_server(clips) as mock_url:
            results = await _execute(mock_url)
    else:
        target = ws_url or "ws://127.0.0.1:8000/ws/session"
        results = await _execute(target)

    summaries = compute_noise_summaries(results, noise_order=[lbl for lbl, _ in snr_levels])
    table = format_markdown_table(summaries)
    return results, table


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Voice Khata /eval WebSocket evaluation harness"
    )
    parser.add_argument(
        "--url",
        default="ws://127.0.0.1:8000/ws/session",
        help="Target /ws/session WebSocket URL (ignored when --mock is set)",
    )
    parser.add_argument(
        "--labels",
        default="eval/labels.json",
        help="Path to labels.json",
    )
    parser.add_argument(
        "--noise-dir",
        default="eval/noise",
        help="Directory containing 16 kHz mono WAV noise files",
    )
    parser.add_argument(
        "--snr-levels",
        default="clean,20,10,5",
        help="Comma-separated SNR levels in dB (default: clean,20,10,5)",
    )
    parser.add_argument(
        "--max-clips",
        type=int,
        default=None,
        help="Maximum number of clips from labels.json to evaluate (caps API credit use)",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Dry run against a real local in-process mock WebSocket server",
    )
    parser.add_argument(
        "--realtime",
        action="store_true",
        help="Pace 20 ms frames in real time (20 ms sleep per frame)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    _, table = asyncio.run(
        run_evaluation(
            ws_url=args.url,
            labels_path=args.labels,
            noise_dir=args.noise_dir,
            snr_spec=args.snr_levels,
            max_clips=args.max_clips,
            use_mock=args.mock,
            pace_realtime=args.realtime,
        )
    )
    print(table)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
