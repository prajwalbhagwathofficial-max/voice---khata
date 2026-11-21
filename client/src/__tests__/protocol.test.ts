import { describe, expect, it } from "vitest";
import {
  FRAME_TYPE_DOWNSTREAM_AUDIO,
  FRAME_TYPE_UPSTREAM_AUDIO,
  decodeDownstreamAudioFrame,
  encodeUpstreamAudioFrame,
  normalizeAndSortLedger,
  shouldAcceptAudioFrame,
} from "../protocol";

describe("binary framing codec", () => {
  it("encodes upstream 20ms 16kHz PCM16 frame with leading 0x01 byte", () => {
    const pcm = new Int16Array(320); // 20 ms at 16 kHz = 640 bytes
    pcm[0] = 1234;
    pcm[319] = -5678;

    const encoded = encodeUpstreamAudioFrame(pcm.buffer);
    const bytes = new Uint8Array(encoded);

    expect(bytes.byteLength).toBe(641);
    expect(bytes[0]).toBe(FRAME_TYPE_UPSTREAM_AUDIO);

    const decodedPcm = new Int16Array(encoded.slice(1));
    expect(decodedPcm[0]).toBe(1234);
    expect(decodedPcm[319]).toBe(-5678);
  });

  it("decodes downstream 0x02 frame with big-endian u32 turn_id and filters by active turn", () => {
    const payload = new Uint8Array([0x01, 0x02, 0x03, 0x04]);
    const buf = new ArrayBuffer(5 + payload.byteLength);
    const view = new DataView(buf);
    view.setUint8(0, FRAME_TYPE_DOWNSTREAM_AUDIO);
    view.setUint32(1, 42, false);
    new Uint8Array(buf, 5).set(payload);

    const frame = decodeDownstreamAudioFrame(buf);
    expect(frame).not.toBeNull();
    expect(frame!.turnId).toBe(42);
    expect(Array.from(new Uint8Array(frame!.pcm))).toEqual([1, 2, 3, 4]);

    expect(shouldAcceptAudioFrame(frame!, 42)).toBe(true);
    expect(shouldAcceptAudioFrame(frame!, 43)).toBe(false);
    expect(shouldAcceptAudioFrame(frame!, null)).toBe(false);
  });

  it("rejects short or non-0x02 downstream binary frames", () => {
    expect(decodeDownstreamAudioFrame(new ArrayBuffer(3))).toBeNull();
    const wrongType = new Uint8Array([0x01, 0, 0, 0, 1, 9, 9]).buffer;
    expect(decodeDownstreamAudioFrame(wrongType)).toBeNull();
  });
});

describe("ledger sorting", () => {
  it("sorts rows by amount owed descending with name tie-breaker", () => {
    const sorted = normalizeAndSortLedger([
      { customer_id: "c1", name: "Suresh", balance_inr: 150 },
      { customer_id: "c2", name: "Ramesh Sharma", balance_inr: 500 },
      { customer_id: "c3", name: "Anil", balance_inr: 500 },
      { customer_id: "c4", name: "Meena", balance_inr: 0 },
    ]);

    expect(sorted.map((r) => [r.name, r.amountOwed])).toEqual([
      ["Anil", 500],
      ["Ramesh Sharma", 500],
      ["Suresh", 150],
      ["Meena", 0],
    ]);
  });
});
