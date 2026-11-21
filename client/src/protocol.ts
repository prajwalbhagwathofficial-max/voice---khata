/**
 * Binary framing codec and WebSocket JSON message types for Voice Khata /client.
 *
 * Binary framing:
 *   0x01 (client -> server): [1-byte type=0x01][PCM16 LE 16kHz mono 20ms frame (640B)]
 *   0x02 (server -> client): [1-byte type=0x02][4-byte u32 BE turn_id][PCM16 LE audio]
 */

export const FRAME_TYPE_UPSTREAM_AUDIO = 0x01;
export const FRAME_TYPE_DOWNSTREAM_AUDIO = 0x02;
export const FRAME_TYPE_UPSTREAM_BILL = 0x03;
export const FRAME_TYPE_DOWNSTREAM_BILL = 0x04;

export interface DownstreamAudioFrame {
  type: typeof FRAME_TYPE_DOWNSTREAM_AUDIO;
  turnId: number;
  pcm: ArrayBuffer;
}

export interface DownstreamBillFrame {
  type: typeof FRAME_TYPE_DOWNSTREAM_BILL;
  transferId: number;
  chunk: ArrayBuffer;
}

export interface EntryItemWire {
  id?: string;
  name: string;
  qty_text?: string | null;
  amount_inr: number;
}

export interface BillRecordWire {
  id: string;
  event_id: string;
  mime: string;
  size: number;
  sha256: string;
  created_ts: number;
}

export interface ShareLogRecordWire {
  id: string;
  event_id: string;
  customer_id: string;
  channel: string;
  created_ts: number;
}

export interface ParsedAction {
  intent?: string;
  type?: string;
  customer?: string;
  customer_name?: string;
  customer_id?: string;
  amount_paise?: number | null;
  amount_inr?: number | null;
  effective_date?: string | null;
  note?: string | null;
  status?: string;
  candidates?: string[];
  event_id?: string;
  items?: EntryItemWire[];
  items_sum_paise?: number | null;
  items_sum?: number | null;
  stated_total_paise?: number | null;
  stated_total?: number | null;
  question?: string;
}

export { formatInr, formatInrRupees } from "./format";

export interface LedgerRow {
  customer_id?: string;
  id?: string;
  name?: string;
  customer_name?: string;
  script?: string;
  balance_paise?: number;
  balance_inr?: number;
  amount_owed?: number;
  has_consent?: boolean;
}

export interface NormalizedLedgerRow {
  id: string;
  name: string;
  script: string;
  amountOwed: number;
  hasConsent?: boolean;
}

export interface PaymentItem {
  seq: number;
  event_id: string;
  customer_id: string;
  customer_name: string;
  amount_paise?: number;
  amount_inr: number;
  effective_date: string;
  ts: string;
  formatted_time?: string;
  note?: string | null;
}

export interface CreditItem {
  seq: number;
  event_id: string;
  customer_id: string;
  customer_name: string;
  amount_paise?: number;
  amount_inr: number;
  effective_date: string;
  ts: string;
  formatted_time?: string;
  note?: string | null;
  items?: EntryItemWire[];
  bills?: BillRecordWire[];
  has_bill?: boolean;
  item_count?: number;
  has_consent?: boolean;
}

export interface LedgerSummary {
  debtor_count: number;
  total_owed_paise?: number;
  total_owed: number;
  total_received_today_paise?: number;
  total_received_today: number;
}

export type ServerMessage =
  | { t: "ready"; session_id?: string; turn_id?: number; sample_rate?: number }
  | { t: "turn.start"; turn_id: number; sample_rate?: number }
  | { t: "tts.start"; turn_id: number; sample_rate?: number }
  | { t: "tts.end"; turn_id?: number }
  | { t: "transcript"; kind?: "partial" | "final"; is_final?: boolean; text: string; turn_id?: number }
  | { t: "transcript.partial"; text: string; turn_id?: number }
  | { t: "transcript.final"; text: string; turn_id?: number }
  | { t: "action"; action?: ParsedAction; turn_id?: number } & ParsedAction
  | {
      t: "ledger";
      rows?: LedgerRow[];
      balances?: LedgerRow[];
      summary?: LedgerSummary;
      payments?: PaymentItem[];
      credits?: CreditItem[];
    }
  | {
      t: "snapshot";
      summary?: { owing_count?: number; total_owed?: number; total_advance?: number; advance_count?: number };
      customers?: LedgerRow[];
      recent_events?: unknown[];
    }
  | {
      t: "summary";
      owing_count?: number;
      total_owed?: number;
      total_advance?: number;
      advance_count?: number;
    }
  | {
      t: "received.result";
      range_total: number;
      days: unknown[];
    }
  | {
      t: "customer.result";
      customer_id: string;
      customer_name: string;
      balance: number;
      advance: number;
      is_advance: boolean;
      timeline: unknown[];
    }
  | { t: "flush"; turn_id?: number }
  | { t: "error"; message: string }
  | { t: "bill.ack"; upload_id: number; received_bytes: number }
  | { t: "bill.done"; upload_id: number; bill_id: string; event_id: string }
  | { t: "bill.error"; upload_id: number; code: string; message: string }
  | { t: "bill.meta"; transfer_id: number; bill_id: string; mime: string; size: number; sha256: string }
  | { t: "bill.get_done"; transfer_id: number; bill_id: string }
  | { t: "entry.items.result"; event_id: string; status: "success" | "error"; items?: EntryItemWire[]; error?: string }
  | { t: "customer.consent.result"; customer_id: string; has_consent: boolean }
  | { t: "customer.create.result"; status: "success" | "error"; customer_id?: string; customer_name?: string; event_id?: string; error?: string }
  | { t: "event.delete.result"; event_id: string; status: "success" | "error"; error?: string };


/**
 * Encode a 20 ms 16 kHz mono PCM16 buffer as a binary type 0x01 frame.
 */
export function encodeUpstreamAudioFrame(pcm: ArrayBuffer): ArrayBuffer {
  const src = new Uint8Array(pcm);
  const out = new Uint8Array(1 + src.byteLength);
  out[0] = FRAME_TYPE_UPSTREAM_AUDIO;
  out.set(src, 1);
  return out.buffer;
}

/**
 * Decode a binary server frame. Returns null if not a valid type 0x02 frame.
 */
export function decodeDownstreamAudioFrame(buffer: ArrayBuffer): DownstreamAudioFrame | null {
  if (buffer.byteLength < 5) {
    return null;
  }
  const view = new DataView(buffer);
  const frameType = view.getUint8(0);
  if (frameType !== FRAME_TYPE_DOWNSTREAM_AUDIO) {
    return null;
  }
  const turnId = view.getUint32(1, false); // big-endian u32
  const pcm = buffer.slice(5);
  return {
    type: FRAME_TYPE_DOWNSTREAM_AUDIO,
    turnId,
    pcm,
  };
}

/**
 * Encode an upstream bill chunk frame (type 0x03, 4-byte u32 BE upload_id, chunk).
 */
export function encodeUpstreamBillChunk(uploadId: number, chunk: ArrayBuffer): ArrayBuffer {
  const src = new Uint8Array(chunk);
  const out = new Uint8Array(5 + src.byteLength);
  out[0] = FRAME_TYPE_UPSTREAM_BILL;
  const view = new DataView(out.buffer);
  view.setUint32(1, uploadId, false);
  out.set(src, 5);
  return out.buffer;
}

/**
 * Decode a downstream bill frame (type 0x04, 4-byte u32 BE transfer_id, chunk).
 */
export function decodeDownstreamBillFrame(buffer: ArrayBuffer): DownstreamBillFrame | null {
  if (buffer.byteLength < 5) {
    return null;
  }
  const view = new DataView(buffer);
  const frameType = view.getUint8(0);
  if (frameType !== FRAME_TYPE_DOWNSTREAM_BILL) {
    return null;
  }
  const transferId = view.getUint32(1, false);
  const chunk = buffer.slice(5);
  return {
    type: FRAME_TYPE_DOWNSTREAM_BILL,
    transferId,
    chunk,
  };
}

/**
 * Accept a downstream audio frame only if its turn_id matches the current turn.
 */
export function shouldAcceptAudioFrame(
  frame: DownstreamAudioFrame,
  currentTurnId: number | null
): boolean {
  return currentTurnId !== null && frame.turnId === currentTurnId;
}

/**
 * Normalize and sort ledger rows strictly by amount owed descending (tie-break by name).
 */
export function normalizeAndSortLedger(rows: LedgerRow[]): NormalizedLedgerRow[] {
  return rows
    .map((row, idx) => {
      const amount =
        typeof row.balance_paise === "number"
          ? Math.trunc(row.balance_paise)
          : typeof row.balance_inr === "number"
          ? Math.trunc(row.balance_inr)
          : typeof row.amount_owed === "number"
          ? Math.trunc(row.amount_owed)
          : 0;
      return {
        id: row.customer_id ?? row.id ?? `row-${idx}`,
        name: row.name ?? row.customer_name ?? "Unknown",
        script: row.script ?? "",
        amountOwed: amount,
        hasConsent: row.has_consent ?? false,
      };
    })
    .sort((a, b) => {
      if (b.amountOwed !== a.amountOwed) {
        return b.amountOwed - a.amountOwed;
      }
      return a.name.localeCompare(b.name);
    });
}
