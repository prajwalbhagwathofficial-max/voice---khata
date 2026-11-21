import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { VoiceAudioEngine } from "./audio";
import { formatInr, formatInrRupees } from "./format";
import { computeLatencyStats } from "./latency";
import {
  BillRecordWire,
  CreditItem,
  EntryItemWire,
  LedgerSummary,
  NormalizedLedgerRow,
  ParsedAction,
  PaymentItem,
  ServerMessage,
  decodeDownstreamAudioFrame,
  decodeDownstreamBillFrame,
  encodeUpstreamAudioFrame,
  encodeUpstreamBillChunk,
  normalizeAndSortLedger,
  shouldAcceptAudioFrame,
} from "./protocol";
import { LandingPage } from "./LandingPage";

type PttState =
  | "CONNECTING"
  | "RECONNECTING"
  | "READY"
  | "LISTENING"
  | "PROCESSING"
  | "SPEAKING";

type ViewMode = "landing" | "home" | "you_gave" | "you_got";

const SHOP_NAME = "Voice Khata Store";

const BANNED_PRESSURE_WORDS = [
  "threat", "police", "court", "jail", "lawyer", "defaulter", "fraud", "case",
  "cheater", "thief", "chor", "chori", "police station", "fir", "warning", "notice",
  "legal", "action", "dhamki", "adalat", "giraftari", "kanun", "chori kiya"
];

function hasBannedPressureWords(text: string): boolean {
  const lower = text.toLowerCase();
  return BANNED_PRESSURE_WORDS.some((w) => lower.includes(w));
}

function renderShareText(
  lang: "hi" | "en" | "te",
  shopName: string,
  dateStr: string,
  customerName: string,
  items: EntryItemWire[] | undefined,
  entryAmount: number,
  balance: number,
  receiptUrl?: string
): string {
  const itemLines = (items && items.length > 0)
    ? items.map((it) => {
        const qty = it.qty_text ? ` (${it.qty_text})` : "";
        return `• ${it.name}${qty}: ₹${it.amount_inr}`;
      }).join("\n")
    : "";

  const itemsBlock = itemLines ? `${itemLines}\n` : "";
  const linkBlock = receiptUrl ? `\n\n✅ आपका उधार VoiceKhata पर जोड़ दिया गया है। अपनी आधिकारिक डिजिटल रसीद यहाँ देखें:\n${receiptUrl}` : "";

  if (lang === "hi") {
    return `${shopName}\nतारीख: ${dateStr}\nनमस्ते ${customerName} जी, आपका नया हिसाब:\n${itemsBlock}कुल नया उधार: ₹${entryAmount}\nकुल बकाया राशि: ₹${balance}${linkBlock}\n\nधन्यवाद! (VoiceKhata)`;
  }
  if (lang === "te") {
    const teLink = receiptUrl ? `\n\n✅ మీ అప్పు VoiceKhata లో చేర్చబడింది. డిజిటల్ రసీదు చూడండి:\n${receiptUrl}` : "";
    return `${shopName}\nతేదీ: ${dateStr}\nనమస్కారం ${customerName} గారు, మీ కొత్త లెక్క:\n${itemsBlock}కొత్త అప్పు: ₹${entryAmount}\nమొత్తం బాకీ: ₹${balance}${teLink}\n\nధన్యవాదాలు! (VoiceKhata)`;
  }
  const enLink = receiptUrl ? `\n\n✅ Your udhar is added on VoiceKhata. View your official digital receipt:\n${receiptUrl}` : "";
  return `${shopName}\nDate: ${dateStr}\nHello ${customerName}, your new entry at ${shopName}:\n${itemsBlock}New Udhar (Credit): ₹${entryAmount}\nTotal Balance Due: ₹${balance}${enLink}\n\nThank you! (VoiceKhata)`;
}

function getCustomerPhone(customerId?: string, customerName?: string): string {
  if (customerId) {
    const p = localStorage.getItem(`vk_phone_${customerId}`);
    if (p) return p;
  }
  if (customerName) {
    const p = localStorage.getItem(`vk_phone_${customerName.toLowerCase().trim()}`);
    if (p) return p;
  }
  return "";
}

function saveCustomerPhone(phone: string, customerId?: string, customerName?: string): void {
  const clean = phone.replace(/\D/g, "");
  if (!clean) return;
  if (customerId) {
    try {
      localStorage.setItem(`vk_phone_${customerId}`, clean);
    } catch {}
  }
  if (customerName) {
    try {
      localStorage.setItem(`vk_phone_${customerName.toLowerCase().trim()}`, clean);
    } catch {}
  }
}

function openWhatsAppDirect(phone: string, text: string): void {
  const clean = phone.replace(/\D/g, "");
  const phoneWithCountry = clean.length === 10 ? `91${clean}` : clean;
  const encoded = encodeURIComponent(text);
  const waUrl = `https://api.whatsapp.com/send?phone=${phoneWithCountry}&text=${encoded}`;
  try {
    const a = document.createElement("a");
    a.href = waUrl;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  } catch {
    window.open(waUrl, "_blank");
  }
}

async function compressImageForUpload(file: File): Promise<{ blob: Blob; arrayBuffer: ArrayBuffer }> {
  if (file.type === "application/pdf") {
    const buf = await file.arrayBuffer();
    return { blob: file, arrayBuffer: buf };
  }

  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = (e) => {
      const img = new Image();
      img.onload = () => {
        const MAX_DIM = 1600;
        let width = img.width;
        let height = img.height;
        if (width > MAX_DIM || height > MAX_DIM) {
          if (width > height) {
            height = Math.round((height * MAX_DIM) / width);
            width = MAX_DIM;
          } else {
            width = Math.round((width * MAX_DIM) / height);
            height = MAX_DIM;
          }
        }

        const canvas = document.createElement("canvas");
        canvas.width = width;
        canvas.height = height;
        const ctx = canvas.getContext("2d");
        if (!ctx) {
          return reject(new Error("Failed to get 2d canvas context"));
        }
        ctx.drawImage(img, 0, 0, width, height);

        canvas.toBlob(
          async (blob) => {
            if (!blob) return reject(new Error("Canvas toBlob failed"));
            const ab = await blob.arrayBuffer();
            resolve({ blob, arrayBuffer: ab });
          },
          "image/jpeg",
          0.85
        );
      };
      img.onerror = () => reject(new Error("Failed to decode image"));
      img.src = e.target?.result as string;
    };
    reader.onerror = () => reject(new Error("Failed to read file"));
    reader.readAsDataURL(file);
  });
}

async function convertBlobToPng(blob: Blob): Promise<Blob> {
  if (blob.type === "image/png") return blob;
  return new Promise((resolve) => {
    const img = new Image();
    const url = URL.createObjectURL(blob);
    img.onload = () => {
      URL.revokeObjectURL(url);
      const canvas = document.createElement("canvas");
      canvas.width = img.naturalWidth || img.width;
      canvas.height = img.naturalHeight || img.height;
      const ctx = canvas.getContext("2d");
      if (!ctx) {
        resolve(blob);
        return;
      }
      ctx.drawImage(img, 0, 0);
      canvas.toBlob((b) => {
        if (b) resolve(b);
        else resolve(blob);
      }, "image/png");
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      resolve(blob);
    };
    img.src = url;
  });
}

function resolveWsUrl(): string {
  return (
    (window.location.protocol === "https:" ? "wss://" : "ws://") +
    window.location.host +
    "/ws/session"
  );
}

export function App() {
  const [viewMode, setViewMode] = useState<ViewMode>(() => {
    try {
      const saved = localStorage.getItem("vk_view_mode");
      if (saved === "landing" || saved === "home" || saved === "you_gave" || saved === "you_got") {
        return saved;
      }
      return "landing";
    } catch {
      return "landing";
    }
  });

  const handleViewChange = useCallback((mode: ViewMode) => {
    setSelectedCustomer(null);
    setViewMode(mode);
    try {
      localStorage.setItem("vk_view_mode", mode);
    } catch {
      // ignore
    }
  }, []);

  const [pttState, setPttState] = useState<PttState>("CONNECTING");
  const [partialTranscript, setPartialTranscript] = useState<string>("");
  const [finalTranscript, setFinalTranscript] = useState<string>("");
  const [parsedAction, setParsedAction] = useState<ParsedAction | null>(null);
  const [ledgerRows, setLedgerRows] = useState<NormalizedLedgerRow[]>([]);
  const [summary, setSummary] = useState<LedgerSummary>({
    debtor_count: 0,
    total_owed: 0,
    total_received_today: 0,
  });
  const [payments, setPayments] = useState<PaymentItem[]>([]);
  const [credits, setCredits] = useState<CreditItem[]>([]);
  const [latencySamples, setLatencySamples] = useState<number[]>([]);
  const [errorText, setErrorText] = useState<string | null>(null);
  const [micActiveLevel, setMicActiveLevel] = useState<number>(0);
  const [customerSearch, setCustomerSearch] = useState<string>("");
  const [paymentSearch, setPaymentSearch] = useState<string>("");
  const [textEntryInput, setTextEntryInput] = useState<string>("");
  const [gotTextEntryInput, setGotTextEntryInput] = useState<string>("");
  const [selectedCustomer, setSelectedCustomer] = useState<NormalizedLedgerRow | null>(null);

  // Customer opt-in consent map (customer_id -> boolean)
  const [, setConsentMap] = useState<Record<string, boolean>>({});

  // Bill upload progress & status
  const [uploadingForEventId, setUploadingForEventId] = useState<string | null>(null);
  const [uploadProgress, setUploadProgress] = useState<{ uploadId: number; received: number; total: number } | null>(null);
  const [uploadStatusMsg, setUploadStatusMsg] = useState<{ type: "success" | "error"; text: string } | null>(null);

  // Itemised bill editing modal
  const [itemsModal, setItemsModal] = useState<{
    open: boolean;
    eventId: string;
    customerName: string;
    eventAmount: number;
    items: EntryItemWire[];
  } | null>(null);
  const [itemsEditorError, setItemsEditorError] = useState<string | null>(null);

  // Add Customer modal & form state
  const [addCustomerModalOpen, setAddCustomerModalOpen] = useState(false);
  const [addCustName, setAddCustName] = useState("");
  const [addCustPhone, setAddCustPhone] = useState("");
  const [addCustInitialUdhar, setAddCustInitialUdhar] = useState("");
  const [addCustNote, setAddCustNote] = useState("");
  const [addCustStatus, setAddCustStatus] = useState<string | null>(null);
  const pendingCustomerPhoneRef = useRef<string>("");
  const pendingInitialUdharRef = useRef<number>(0);

  // Message sharing dialog (Customer phone number strictly in local component state)
  const [shareModal, setShareModal] = useState<{
    open: boolean;
    eventId: string;
    customerId: string;
    customerName: string;
    amount: number;
    date: string;
    items?: EntryItemWire[];
    balance: number;
    bills?: BillRecordWire[];
    receiptUrl?: string;
    initialChannel?: "whatsapp" | "sms";
  } | null>(null);
  const [sharePhone, setSharePhone] = useState<string>("");
  const [shareLang, setShareLang] = useState<"hi" | "en" | "te">("hi");
  const [shareCustomText, setShareCustomText] = useState<string>("");
  const [isCustomEdited, setIsCustomEdited] = useState<boolean>(false);
  const [copiedImage, setCopiedImage] = useState<boolean>(false);
  const [copiedReceiptLink, setCopiedReceiptLink] = useState<boolean>(false);
  const [isFetchingBill, setIsFetchingBill] = useState<boolean>(false);

  // Bill photo/PDF viewer modal (downloads chunks over WebSocket)
  const [viewingBill, setViewingBill] = useState<{
    open: boolean;
    url: string | null;
    mime: string;
    title: string;
    loading: boolean;
  } | null>(null);

  // Delete transaction confirmation modal
  const [deleteModal, setDeleteModal] = useState<{
    eventId: string;
    customerName: string;
    amount: number;
    type: "GAVE" | "GOT" | "ENTRY";
    isPaise?: boolean;
  } | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const audioRef = useRef<VoiceAudioEngine | null>(null);
  const pttHeldRef = useRef<boolean>(false);
  const pttStreamingRef = useRef<boolean>(false);
  const currentTurnIdRef = useRef<number | null>(null);
  const playbackSampleRateRef = useRef<number>(48000);
  const pttUpMsRef = useRef<number | null>(null);
  const measuredTurnIdRef = useRef<number | null>(null);
  const pttStateRef = useRef<PttState>("CONNECTING");

  const activeTransfersRef = useRef<
    Map<
      number,
      {
        mime: string;
        size: number;
        chunks: ArrayBuffer[];
        onDone?: (blob: Blob, mime: string) => void;
      }
    >
  >(new Map());
  const uploadTotalBytesRef = useRef<number>(0);
  const hiddenFileInputRef = useRef<HTMLInputElement | null>(null);
  const activeUploadEventIdRef = useRef<string | null>(null);

  const updatePttState = useCallback((next: PttState) => {
    pttStateRef.current = next;
    setPttState(next);
  }, []);

  const sendJson = useCallback((payload: Record<string, unknown>) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(payload));
    }
  }, []);

  useEffect(() => {
    // Startup secure-context check: fail loudly if navigator.mediaDevices is unavailable
    if (
      typeof navigator === "undefined" ||
      !navigator.mediaDevices ||
      typeof navigator.mediaDevices.getUserMedia !== "function"
    ) {
      setErrorText(
        "Microphone access is unavailable because this page is not running in a secure HTTPS context. Open the app over HTTPS to use voice input."
      );
    }

    const audio = new VoiceAudioEngine({
      onCaptureFrame: (pcm: ArrayBuffer, rms: number) => {
        if (!pttStreamingRef.current) return;
        setMicActiveLevel(Math.min(100, Math.round(rms * 400)));
        const ws = wsRef.current;
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(encodeUpstreamAudioFrame(pcm));
        }
      },
      onFirstAudioSample: (turnId: number, firstSampleMs: number) => {
        if (
          pttUpMsRef.current !== null &&
          measuredTurnIdRef.current !== turnId
        ) {
          const delta = Math.max(0, firstSampleMs - pttUpMsRef.current);
          measuredTurnIdRef.current = turnId;
          pttUpMsRef.current = null;
          setLatencySamples((prev) => [...prev, delta]);
        }
      },
      onPlaybackStateChange: (playing: boolean) => {
        if (playing) {
          if (!pttStreamingRef.current) {
            updatePttState("SPEAKING");
          }
        } else if (pttStateRef.current === "SPEAKING") {
          updatePttState("READY");
        }
      },
    });
    audioRef.current = audio;

    let disposed = false;
    let reconnectAttempt = 0;
    let reconnectTimer: number | null = null;

    const connect = () => {
      if (disposed) return;
      updatePttState(reconnectAttempt === 0 ? "CONNECTING" : "RECONNECTING");

      const ws = new WebSocket(resolveWsUrl());
      ws.binaryType = "arraybuffer";
      wsRef.current = ws;

      ws.onopen = () => {
        reconnectAttempt = 0;
        if (navigator.mediaDevices && typeof navigator.mediaDevices.getUserMedia === "function") {
          setErrorText(null);
        }
        ws.send(JSON.stringify({ t: "session.start" }));
      };

      ws.onmessage = (event: MessageEvent) => {
        if (event.data instanceof ArrayBuffer) {
          const frame = decodeDownstreamAudioFrame(event.data);
          if (frame) {
            if (!shouldAcceptAudioFrame(frame, currentTurnIdRef.current)) {
              return;
            }
            audio.pushPlaybackPcm(
              frame.turnId,
              frame.pcm,
              playbackSampleRateRef.current
            );
            return;
          }

          const billFrame = decodeDownstreamBillFrame(event.data);
          if (billFrame) {
            const transfer = activeTransfersRef.current.get(billFrame.transferId);
            if (transfer) {
              transfer.chunks.push(billFrame.chunk);
            }
            return;
          }
          return;
        }

        if (typeof event.data !== "string") return;
        let msg: ServerMessage;
        try {
          msg = JSON.parse(event.data) as ServerMessage;
        } catch {
          return;
        }

        switch (msg.t) {
          case "ready":
            if (typeof msg.turn_id === "number") {
              currentTurnIdRef.current = msg.turn_id;
            }
            if (typeof msg.sample_rate === "number" && msg.sample_rate > 0) {
              playbackSampleRateRef.current = msg.sample_rate;
            }
            updatePttState("READY");
            break;

          case "turn.start":
          case "tts.start":
            currentTurnIdRef.current = msg.turn_id;
            if (typeof msg.sample_rate === "number" && msg.sample_rate > 0) {
              playbackSampleRateRef.current = msg.sample_rate;
            }
            break;

          case "tts.end":
            if (typeof msg.turn_id === "number") {
              audio.markEndOfTurn(msg.turn_id);
            } else if (currentTurnIdRef.current !== null) {
              audio.markEndOfTurn(currentTurnIdRef.current);
            }
            if (
              !audio.playing &&
              !pttStreamingRef.current &&
              pttStateRef.current === "PROCESSING"
            ) {
              updatePttState("READY");
            }
            break;

          case "flush":
            audio.flushPlayback();
            currentTurnIdRef.current = null;
            if (!pttStreamingRef.current && ws.readyState === WebSocket.OPEN) {
              updatePttState("READY");
            }
            break;

          case "transcript": {
            if (typeof msg.turn_id === "number") {
              currentTurnIdRef.current = msg.turn_id;
            }
            const isFinal = msg.kind === "final" || msg.is_final === true;
            if (isFinal) {
              if (msg.text) {
                setFinalTranscript(msg.text);
              }
              setPartialTranscript("");
            } else {
              setPartialTranscript(msg.text);
            }
            break;
          }

          case "transcript.partial":
            if (typeof msg.turn_id === "number") {
              currentTurnIdRef.current = msg.turn_id;
            }
            setPartialTranscript(msg.text);
            break;

          case "transcript.final":
            if (typeof msg.turn_id === "number") {
              currentTurnIdRef.current = msg.turn_id;
            }
            if (msg.text) {
              setFinalTranscript(msg.text);
            }
            setPartialTranscript("");
            break;

          case "action": {
            if (typeof msg.turn_id === "number") {
              currentTurnIdRef.current = msg.turn_id;
            }
            const raw = msg as any;
            const amtPaise =
              typeof raw.amount_paise === "number"
                ? raw.amount_paise
                : typeof raw.amount_inr === "number"
                ? Math.round(raw.amount_inr * 100)
                : undefined;
            const amtInr =
              typeof raw.amount_inr === "number"
                ? raw.amount_inr
                : typeof amtPaise === "number"
                ? Math.floor(amtPaise / 100)
                : undefined;

            const payload: ParsedAction = msg.action
              ? msg.action
              : {
                  intent: msg.intent,
                  type: msg.type,
                  customer: msg.customer,
                  customer_name: msg.customer_name,
                  customer_id: msg.customer_id,
                  amount_paise: amtPaise,
                  amount_inr: amtInr,
                  effective_date: msg.effective_date,
                  note: msg.note,
                  status: msg.status,
                  candidates: msg.candidates,
                  event_id: msg.event_id,
                  items: msg.items,
                  items_sum: msg.items_sum,
                  items_sum_paise: raw.items_sum_paise,
                  stated_total: msg.stated_total,
                  stated_total_paise: raw.stated_total_paise,
                  question: msg.question,
                };
            setParsedAction(payload);
            break;
          }

          case "ledger": {
            const incoming = msg.rows ?? msg.balances ?? [];
            setLedgerRows(normalizeAndSortLedger(incoming));
            if (msg.summary) {
              setSummary(msg.summary);
            }
            if (msg.payments) {
              setPayments(msg.payments);
            }
            if (msg.credits) {
              setCredits(msg.credits);
            }
            break;
          }

          case "snapshot": {
            if (msg.customers) {
              setLedgerRows(normalizeAndSortLedger(msg.customers));
            }
            if (msg.summary) {
              const s = msg.summary as any;
              setSummary((prev) => ({
                ...prev,
                debtor_count: s.owing_count ?? prev.debtor_count,
                total_owed: s.total_owed ?? prev.total_owed,
                total_owed_paise:
                  s.total_owed_paise ??
                  (typeof s.total_owed === "number" ? s.total_owed * 100 : prev.total_owed_paise),
                total_received_today: s.total_received_today ?? prev.total_received_today,
                total_received_today_paise:
                  s.total_received_today_paise ??
                  (typeof s.total_received_today === "number"
                    ? s.total_received_today * 100
                    : prev.total_received_today_paise),
              }));
            }
            break;
          }

          case "summary": {
            const s = msg as any;
            setSummary((prev) => ({
              ...prev,
              debtor_count: s.owing_count ?? prev.debtor_count,
              total_owed: s.total_owed ?? prev.total_owed,
              total_owed_paise:
                s.total_owed_paise ??
                (typeof s.total_owed === "number" ? s.total_owed * 100 : prev.total_owed_paise),
              total_received_today: s.total_received_today ?? prev.total_received_today,
              total_received_today_paise:
                s.total_received_today_paise ??
                (typeof s.total_received_today === "number"
                  ? s.total_received_today * 100
                  : prev.total_received_today_paise),
            }));
            break;
          }

          case "bill.ack":
            setUploadProgress((prev) =>
              prev && prev.uploadId === msg.upload_id
                ? { ...prev, received: msg.received_bytes }
                : prev
            );
            break;

          case "bill.done":
            setUploadProgress(null);
            setUploadingForEventId(null);
            setUploadStatusMsg({ type: "success", text: "✓ Bill photo attached & encrypted!" });
            ws.send(JSON.stringify({ t: "session.start" }));
            break;

          case "bill.error":
            setUploadProgress(null);
            setUploadingForEventId(null);
            setUploadStatusMsg({ type: "error", text: `Upload error: ${msg.message}` });
            break;

          case "bill.meta": {
            const transfer = activeTransfersRef.current.get(msg.transfer_id);
            if (transfer) {
              transfer.mime = msg.mime;
              transfer.size = msg.size;
            }
            break;
          }

          case "bill.get_done": {
            const transfer = activeTransfersRef.current.get(msg.transfer_id);
            if (transfer) {
              const totalLen = transfer.chunks.reduce((acc, c) => acc + c.byteLength, 0);
              const combined = new Uint8Array(totalLen);
              let offset = 0;
              for (const chunk of transfer.chunks) {
                combined.set(new Uint8Array(chunk), offset);
                offset += chunk.byteLength;
              }
              const mime = transfer.mime || "image/jpeg";
              const blob = new Blob([combined], { type: mime });
              const url = URL.createObjectURL(blob);
              if (transfer.onDone) {
                transfer.onDone(blob, mime);
              }
              setViewingBill((prev) => (prev ? { ...prev, url, mime, loading: false } : null));
              activeTransfersRef.current.delete(msg.transfer_id);
            }
            break;
          }

          case "entry.items.result":
            if (msg.status === "success") {
              setItemsModal(null);
              setItemsEditorError(null);
              setUploadStatusMsg({ type: "success", text: "✓ Itemised bill saved successfully!" });
              ws.send(JSON.stringify({ t: "session.start" }));
            } else {
              setItemsEditorError(msg.error || "Failed to save items");
            }
            break;

          case "customer.consent.result":
            setConsentMap((prev) => ({ ...prev, [msg.customer_id]: msg.has_consent }));
            break;

          case "customer.create.result":
            if (msg.status === "success") {
              const createdName = msg.customer_name || addCustName;
              setUploadStatusMsg({ type: "success", text: `✓ Customer "${createdName}" saved successfully!` });
              if (pendingCustomerPhoneRef.current) {
                saveCustomerPhone(pendingCustomerPhoneRef.current, msg.customer_id, createdName);
              }
              setAddCustomerModalOpen(false);
              setAddCustStatus(null);

              // If an initial Udhar was added (e.g. ₹500), auto-open receipt share!
              const initialAmt = pendingInitialUdharRef.current;
              if (msg.event_id && initialAmt > 0) {
                handleOpenShare(
                  msg.event_id,
                  msg.customer_id || "",
                  createdName,
                  initialAmt,
                  new Date().toISOString().slice(0, 10),
                  undefined,
                  initialAmt,
                  undefined,
                  "whatsapp",
                  false // open modal to preview receipt link
                );
              }
            } else {
              setAddCustStatus(null);
              setUploadStatusMsg({ type: "error", text: `Failed to add customer: ${msg.error || "Unknown error"}` });
            }
            break;

          case "event.delete.result":
            if (msg.status === "success") {
              setUploadStatusMsg({ type: "success", text: "✓ Transaction directly deleted from database!" });
              setParsedAction((prev) => (prev && prev.event_id === msg.event_id ? null : prev));
            } else {
              setUploadStatusMsg({ type: "error", text: `Failed to delete transaction: ${msg.error || "Unknown error"}` });
            }
            break;

          case "error":
            setErrorText(msg.message);
            if (!pttStreamingRef.current && ws.readyState === WebSocket.OPEN) {
              updatePttState("READY");
            }
            break;
        }
      };

      ws.onclose = () => {
        pttHeldRef.current = false;
        pttStreamingRef.current = false;
        setMicActiveLevel(0);
        audio.setCaptureActive(false);
        audio.flushPlayback();
        if (disposed) return;

        updatePttState("RECONNECTING");
        const baseMs = Math.min(15000, 500 * Math.pow(2, reconnectAttempt));
        const jitterMs = Math.round(baseMs * (0.75 + Math.random() * 0.5));
        reconnectAttempt += 1;
        reconnectTimer = window.setTimeout(connect, jitterMs);
      };

      ws.onerror = () => {
        if (!disposed) {
          setErrorText("WebSocket connection lost. Reconnecting…");
        }
      };
    };

    connect();

    return () => {
      disposed = true;
      if (reconnectTimer !== null) {
        window.clearTimeout(reconnectTimer);
      }
      wsRef.current?.close();
      void audio.close();
    };
  }, [updatePttState]);

  const pressStartTimeRef = useRef<number>(0);
  const isPressHoldingRef = useRef<boolean>(false);

  const startRecording = useCallback(async () => {
    const ws = wsRef.current;
    const audio = audioRef.current;
    if (!audio) {
      setErrorText("Audio system initializing... please try again in a moment.");
      return;
    }
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      setErrorText("Connecting to live server... please tap again in a moment.");
      return;
    }
    if (pttStreamingRef.current || pttStateRef.current === "LISTENING") {
      return;
    }

    setErrorText(null);
    pttHeldRef.current = true;

    // Direct user-gesture resume for iOS / Android mobile browsers
    await audio.resumeContext();

    // Barge-in: if audio is currently playing, clear ring buffer locally first, then send interrupt
    const wasPlaying = audio.playing || pttStateRef.current === "SPEAKING";
    if (wasPlaying) {
      audio.flushPlayback();
      currentTurnIdRef.current = null;
      sendJson({ t: "interrupt" });
    }

    try {
      await audio.ensureMicCapture();
    } catch (err) {
      pttHeldRef.current = false;
      pttStreamingRef.current = false;
      const msg = err instanceof Error ? err.message : String(err);
      if (
        msg.includes("Permission") ||
        msg.includes("denied") ||
        msg.includes("NotAllowedError")
      ) {
        setErrorText(
          "Microphone permission was denied. Tap the lock icon in your browser address bar to allow microphone access, then try again."
        );
      } else {
        setErrorText(`Microphone error: ${msg}`);
      }
      updatePttState("READY");
      return;
    }

    setPartialTranscript("");
    pttStreamingRef.current = true;
    audio.setCaptureActive(true);
    sendJson({ t: "ptt.down" });
    updatePttState("LISTENING");
  }, [sendJson, updatePttState]);

  const stopRecording = useCallback(() => {
    if (!pttStreamingRef.current && pttStateRef.current !== "LISTENING") {
      pttHeldRef.current = false;
      return;
    }
    pttHeldRef.current = false;
    pttStreamingRef.current = false;
    setMicActiveLevel(0);
    audioRef.current?.setCaptureActive(false);
    pttUpMsRef.current = performance.now();
    measuredTurnIdRef.current = null;
    sendJson({ t: "ptt.up" });
    updatePttState("PROCESSING");

    window.setTimeout(() => {
      if (pttStateRef.current === "PROCESSING" && !pttStreamingRef.current) {
        updatePttState("READY");
      }
    }, 5000);
  }, [sendJson, updatePttState]);

  const handlePointerDown = useCallback(() => {
    pressStartTimeRef.current = performance.now();
    isPressHoldingRef.current = false;
    void audioRef.current?.resumeContext();
  }, []);

  const handlePointerUp = useCallback(() => {
    const duration = performance.now() - pressStartTimeRef.current;
    if (duration > 350 && (pttStateRef.current === "LISTENING" || pttStreamingRef.current)) {
      // User held the button to speak and released
      isPressHoldingRef.current = true;
      stopRecording();
    }
  }, [stopRecording]);

  const handleToggleMic = useCallback(() => {
    if (isPressHoldingRef.current) {
      isPressHoldingRef.current = false;
      return;
    }
    if (pttStateRef.current === "LISTENING" || pttStreamingRef.current) {
      stopRecording();
    } else {
      void startRecording();
    }
  }, [startRecording, stopRecording]);

  // Spacebar toggle recording: active in "you_gave" and "home" views
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.code === "Space" && !e.repeat) {
        if (viewMode !== "you_gave" && viewMode !== "home") {
          return;
        }
        const tag = (document.activeElement?.tagName || "").toUpperCase();
        if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") {
          return;
        }
        if (tag === "BUTTON" && document.activeElement?.id === "reset-demo-btn") {
          return;
        }
        e.preventDefault();
        handleToggleMic();
      }
    };

    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [viewMode, handleToggleMic]);

  const latency = useMemo(() => computeLatencyStats(latencySamples), [latencySamples]);

  const pttDisabled = pttState === "RECONNECTING" || pttState === "CONNECTING";
  const pttLabel: Record<PttState, string> = {
    CONNECTING: "Connecting to server…",
    RECONNECTING: "Reconnecting…",
    READY: "Click to Record Voice (Space)",
    LISTENING: "Recording… Click to Stop & Save",
    PROCESSING: "Processing audio…",
    SPEAKING: "Agent Speaking — Click to Interrupt",
  };

  // Customers who owe money (balance > 0)
  const debtorRows = useMemo(() => {
    return ledgerRows.filter((r) => r.amountOwed > 0);
  }, [ledgerRows]);

  const computedDebtorCount = summary.debtor_count > 0 ? summary.debtor_count : debtorRows.length;
  const computedTotalOwed =
    summary.total_owed_paise !== undefined && summary.total_owed_paise > 0
      ? summary.total_owed_paise
      : summary.total_owed > 0
      ? summary.total_owed * 100
      : debtorRows.reduce((acc, r) => acc + r.amountOwed, 0);

  const filteredDebtorRows = useMemo(() => {
    if (!customerSearch.trim()) return debtorRows;
    const q = customerSearch.toLowerCase().trim();
    return debtorRows.filter((r) => r.name.toLowerCase().includes(q));
  }, [debtorRows, customerSearch]);

  const handleSelectCandidate = useCallback(
    (name: string) => {
      sendJson({ t: "inject_text", text: name });
    },
    [sendJson]
  );

  const handleTextSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      if (!textEntryInput.trim()) return;
      sendJson({ t: "inject_text", text: textEntryInput.trim() });
      setTextEntryInput("");
    },
    [textEntryInput, sendJson]
  );

  const handleGotTextSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      if (!gotTextEntryInput.trim()) return;
      sendJson({ t: "inject_text", text: gotTextEntryInput.trim() });
      setGotTextEntryInput("");
    },
    [gotTextEntryInput, sendJson]
  );

  const handleViewBill = useCallback((billId: string, title: string) => {
    const transferId = Math.floor(Math.random() * 1000000) + 1;
    activeTransfersRef.current.set(transferId, { mime: "image/jpeg", size: 0, chunks: [] });
    setViewingBill({ open: true, url: null, mime: "image/jpeg", title, loading: true });
    sendJson({ t: "bill.get", transfer_id: transferId, bill_id: billId });
  }, [sendJson]);

  const handleUploadBill = useCallback(async (eventId: string, file: File) => {
    try {
      setUploadingForEventId(eventId);
      setUploadStatusMsg(null);
      const { blob, arrayBuffer } = await compressImageForUpload(file);
      if (arrayBuffer.byteLength > 5 * 1024 * 1024) {
        setUploadStatusMsg({ type: "error", text: "File is too large (> 5MB)" });
        setUploadingForEventId(null);
        return;
      }
      const hashBuffer = await crypto.subtle.digest("SHA-256", arrayBuffer);
      const hashArray = Array.from(new Uint8Array(hashBuffer));
      const sha256 = hashArray.map((b) => b.toString(16).padStart(2, "0")).join("");

      const uploadId = Math.floor(Math.random() * 1000000) + 1;
      uploadTotalBytesRef.current = arrayBuffer.byteLength;
      setUploadProgress({ uploadId, received: 0, total: arrayBuffer.byteLength });

      const ws = wsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) {
        setUploadStatusMsg({ type: "error", text: "WebSocket is not connected" });
        setUploadingForEventId(null);
        setUploadProgress(null);
        return;
      }

      // 1. send bill.start
      ws.send(JSON.stringify({
        t: "bill.start",
        upload_id: uploadId,
        event_id: eventId,
        mime: blob.type || "image/jpeg",
        size: arrayBuffer.byteLength,
        sha256,
      }));

      // 2. send binary chunks (64KB)
      const CHUNK_SIZE = 64 * 1024;
      for (let offset = 0; offset < arrayBuffer.byteLength; offset += CHUNK_SIZE) {
        const slice = arrayBuffer.slice(offset, offset + CHUNK_SIZE);
        const frame = encodeUpstreamBillChunk(uploadId, slice);
        ws.send(frame);
      }

      // 3. send bill.end
      ws.send(JSON.stringify({
        t: "bill.end",
        upload_id: uploadId,
      }));
    } catch (err: unknown) {
      setUploadStatusMsg({
        type: "error",
        text: `Upload failed: ${err instanceof Error ? err.message : String(err)}`,
      });
      setUploadingForEventId(null);
      setUploadProgress(null);
    }
  }, []);

  const handleTriggerUploadForEvent = useCallback((eventId: string) => {
    activeUploadEventIdRef.current = eventId;
    hiddenFileInputRef.current?.click();
  }, []);

  const handleRequestDelete = useCallback(
    (
      eventId: string,
      customerName: string,
      amount: number,
      type: "GAVE" | "GOT" | "ENTRY",
      isPaise: boolean = true
    ) => {
      setDeleteModal({ eventId, customerName, amount, type, isPaise });
    },
    []
  );

  const handleConfirmDelete = useCallback(() => {
    if (!deleteModal) return;
    sendJson({ t: "event.delete", event_id: deleteModal.eventId });
    setDeleteModal(null);
  }, [deleteModal, sendJson]);

  const handleFileInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      const eventId = activeUploadEventIdRef.current;
      if (file && eventId) {
        void handleUploadBill(eventId, file);
      }
      e.target.value = "";
    },
    [handleUploadBill]
  );

  const handleOpenItemsEditor = useCallback(
    (eventId: string, customerName: string, eventAmount: number, existingItems?: EntryItemWire[]) => {
      const items =
        existingItems && existingItems.length > 0
          ? existingItems.map((it) => ({ ...it }))
          : [{ name: "", qty_text: "", amount_inr: eventAmount }];
      setItemsModal({ open: true, eventId, customerName, eventAmount, items });
      setItemsEditorError(null);
    },
    []
  );

  const handleAddItemRow = useCallback(() => {
    if (!itemsModal) return;
    setItemsModal({
      ...itemsModal,
      items: [...itemsModal.items, { name: "", qty_text: "", amount_inr: 0 }],
    });
  }, [itemsModal]);

  const handleItemFieldChange = useCallback(
    (index: number, field: keyof EntryItemWire, value: string | number) => {
      if (!itemsModal) return;
      const next = [...itemsModal.items];
      next[index] = { ...next[index], [field]: value };
      setItemsModal({ ...itemsModal, items: next });
    },
    [itemsModal]
  );

  const handleRemoveItemRow = useCallback(
    (index: number) => {
      if (!itemsModal) return;
      const next = itemsModal.items.filter((_, i) => i !== index);
      setItemsModal({ ...itemsModal, items: next });
    },
    [itemsModal]
  );

  const handleSaveItems = useCallback(() => {
    if (!itemsModal) return;
    const itemSum = itemsModal.items.reduce((sum, it) => sum + (Number(it.amount_inr) || 0), 0);
    if (itemSum !== itemsModal.eventAmount) {
      setItemsEditorError(
        `Sum of items (₹${itemSum}) must equal transaction amount (₹${itemsModal.eventAmount}).`
      );
      return;
    }
    const cleanItems = itemsModal.items.map((it) => ({
      name: it.name.trim() || "Item",
      qty_text: it.qty_text?.trim() || null,
      amount_inr: Number(it.amount_inr) || 0,
    }));
    sendJson({
      t: "entry.items.set",
      event_id: itemsModal.eventId,
      items: cleanItems,
    });
  }, [itemsModal, sendJson]);

  const fetchBillBlob = useCallback(
    (billId: string): Promise<{ blob: Blob; mime: string }> => {
      return new Promise((resolve, reject) => {
        const ws = wsRef.current;
        if (!ws || ws.readyState !== WebSocket.OPEN) {
          reject(new Error("WebSocket is not connected"));
          return;
        }
        const transferId = Math.floor(Math.random() * 1000000) + 1;
        const timeout = window.setTimeout(() => {
          activeTransfersRef.current.delete(transferId);
          reject(new Error("Bill download timed out"));
        }, 10000);

        activeTransfersRef.current.set(transferId, {
          mime: "image/jpeg",
          size: 0,
          chunks: [],
          onDone: (blob, mime) => {
            window.clearTimeout(timeout);
            resolve({ blob, mime });
          },
        });
        sendJson({ t: "bill.get", transfer_id: transferId, bill_id: billId });
      });
    },
    [sendJson]
  );

  const handleDownloadShareBill = useCallback(async () => {
    if (!shareModal?.bills || shareModal.bills.length === 0) return;
    try {
      setIsFetchingBill(true);
      const bill = shareModal.bills[0];
      const { blob, mime } = await fetchBillBlob(bill.id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      const ext = mime === "application/pdf" ? "pdf" : "jpg";
      a.download = `bill_${shareModal.customerName.replace(/\s+/g, "_")}.${ext}`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      window.setTimeout(() => URL.revokeObjectURL(url), 10000);
    } catch (err) {
      alert(`Could not fetch bill photo: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setIsFetchingBill(false);
    }
  }, [shareModal, fetchBillBlob]);

  const handleCopyShareBill = useCallback(async () => {
    if (!shareModal?.bills || shareModal.bills.length === 0) return;
    try {
      setIsFetchingBill(true);
      const bill = shareModal.bills[0];
      const { blob } = await fetchBillBlob(bill.id);
      const pngBlob = await convertBlobToPng(blob);
      if (typeof ClipboardItem !== "undefined" && navigator.clipboard?.write) {
        await navigator.clipboard.write([
          new ClipboardItem({ "image/png": pngBlob }),
        ]);
        setCopiedImage(true);
        window.setTimeout(() => setCopiedImage(false), 3000);
      } else {
        alert("Clipboard image copy not supported in this browser. Please use Download Bill.");
      }
    } catch (err) {
      alert(`Could not copy image: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setIsFetchingBill(false);
    }
  }, [shareModal, fetchBillBlob]);

  const handleOpenShare = useCallback(
    (
      eventId: string,
      customerId: string,
      customerName: string,
      amount: number,
      dateStr?: string,
      items?: EntryItemWire[],
      balance?: number,
      bills?: BillRecordWire[],
      initialChannel: "whatsapp" | "sms" = "whatsapp",
      immediateSend: boolean = false
    ) => {
      const custRow = debtorRows.find(
        (r) =>
          r.name.toLowerCase() === customerName.toLowerCase() || r.id === customerId
      );
      const resolvedCustId = customerId || custRow?.id || "";
      const custBalInr =
        balance !== undefined
          ? (balance > 1000 ? Math.round(balance / 100) : balance)
          : custRow
          ? Math.round(custRow.amountOwed / 100)
          : amount;
      const effectiveDate = dateStr || new Date().toISOString().slice(0, 10);
      const savedPhone = getCustomerPhone(resolvedCustId, customerName);

      // Resolve event ID (fallback to customer latest credit if missing)
      let targetEventId = eventId;
      if (!targetEventId && resolvedCustId) {
        const match = credits.find((c) => c.customer_id === resolvedCustId || c.customer_name?.toLowerCase() === customerName.toLowerCase());
        if (match?.event_id) targetEventId = match.event_id;
      }

      const receiptUrl = targetEventId ? `${window.location.origin}/receipt/${targetEventId}` : "";
      const initialText = renderShareText(
        "hi",
        SHOP_NAME,
        effectiveDate,
        customerName,
        items,
        amount,
        custBalInr,
        receiptUrl
      );

      // Direct WhatsApp send if customer phone is already saved!
      if (immediateSend && savedPhone && savedPhone.length === 10) {
        if (initialChannel === "whatsapp") {
          openWhatsAppDirect(savedPhone, initialText);
        } else {
          window.location.href = `sms:+91${savedPhone}?body=${encodeURIComponent(initialText)}`;
        }
        sendJson({
          t: "share.logged",
          customer_id: resolvedCustId,
          event_id: targetEventId,
          channel: initialChannel,
        });
      }

      setShareModal({
        open: true,
        eventId: targetEventId,
        customerId: resolvedCustId,
        customerName,
        amount,
        date: effectiveDate,
        items,
        balance: custBalInr,
        bills,
        receiptUrl,
        initialChannel,
      });
      setSharePhone(savedPhone);
      setShareLang("hi");
      setIsCustomEdited(false);
      setCopiedImage(false);
      setCopiedReceiptLink(false);
      setShareCustomText(initialText);
    },
    [debtorRows, credits, sendJson]
  );

  const handleCopyReceiptLink = useCallback(() => {
    if (!shareModal?.receiptUrl) return;
    void navigator.clipboard.writeText(shareModal.receiptUrl);
    setCopiedReceiptLink(true);
    setTimeout(() => setCopiedReceiptLink(false), 2500);
  }, [shareModal]);

  const handleRecordConsent = useCallback(
    (customerId: string) => {
      sendJson({ t: "customer.consent", customer_id: customerId });
    },
    [sendJson]
  );

  const handleSelectShareLang = useCallback(
    (lang: "hi" | "en" | "te") => {
      setShareLang(lang);
      if (!shareModal) return;
      if (!isCustomEdited) {
        const txt = renderShareText(
          lang,
          SHOP_NAME,
          shareModal.date,
          shareModal.customerName,
          shareModal.items,
          shareModal.amount,
          shareModal.balance,
          shareModal.receiptUrl
        );
        setShareCustomText(txt);
      }
    },
    [shareModal, isCustomEdited]
  );

  const handleShareWhatsApp = useCallback(() => {
    if (!shareModal) return;
    const clean = sharePhone.replace(/\D/g, "");
    if (clean.length < 10) {
      alert("Please enter a valid 10-digit customer mobile number.");
      return;
    }
    if (hasBannedPressureWords(shareCustomText)) {
      alert("Please remove pressure words before sharing.");
      return;
    }
    saveCustomerPhone(clean, shareModal.customerId, shareModal.customerName);
    setConsentMap((prev) => ({ ...prev, [shareModal.customerId]: true }));
    handleRecordConsent(shareModal.customerId);

    openWhatsAppDirect(clean, shareCustomText);
    sendJson({
      t: "share.logged",
      customer_id: shareModal.customerId,
      event_id: shareModal.eventId,
      channel: "whatsapp",
    });
  }, [shareModal, sharePhone, shareCustomText, sendJson, handleRecordConsent]);

  const handleCreateCustomerSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      const name = addCustName.trim();
      if (!name) {
        alert("Please enter customer name");
        return;
      }
      const clean = addCustPhone.replace(/\D/g, "");
      if (clean.length < 10) {
        alert("Please enter a valid 10-digit mobile number");
        return;
      }
      pendingCustomerPhoneRef.current = clean;
      const parsedUdhar = parseFloat(addCustInitialUdhar);
      const initialPaise = !isNaN(parsedUdhar) && parsedUdhar > 0 ? Math.round(parsedUdhar * 100) : undefined;
      pendingInitialUdharRef.current = !isNaN(parsedUdhar) && parsedUdhar > 0 ? parsedUdhar : 0;

      saveCustomerPhone(clean, undefined, name);
      setAddCustStatus(`Saving ${name}...`);
      sendJson({
        t: "customer.create",
        name,
        phone: clean,
        initial_credit_paise: initialPaise,
        note: addCustNote.trim() || undefined,
      });
    },
    [addCustName, addCustPhone, addCustInitialUdhar, addCustNote, sendJson]
  );

  const handleShareSms = useCallback(() => {
    if (!shareModal) return;
    const clean = sharePhone.replace(/\D/g, "");
    if (clean.length < 10) {
      alert("Please enter a valid 10-digit customer mobile number.");
      return;
    }
    if (hasBannedPressureWords(shareCustomText)) {
      alert("Please remove pressure words before sharing.");
      return;
    }
    if (shareModal.customerId) {
      try {
        localStorage.setItem(`vk_phone_${shareModal.customerId}`, clean);
      } catch {}
    }
    setConsentMap((prev) => ({ ...prev, [shareModal.customerId]: true }));
    handleRecordConsent(shareModal.customerId);

    const phoneWithCountry = clean.length === 10 ? `+91${clean}` : `+${clean}`;
    const url = `sms:${phoneWithCountry}?body=${encodeURIComponent(shareCustomText)}`;
    window.location.href = url;
    sendJson({
      t: "share.logged",
      customer_id: shareModal.customerId,
      event_id: shareModal.eventId,
      channel: "sms",
    });
    setShareModal(null);
  }, [shareModal, sharePhone, shareCustomText, sendJson, handleRecordConsent]);

  const handleShareWeb = useCallback(async () => {
    if (!shareModal || !navigator.share) return;
    if (hasBannedPressureWords(shareCustomText)) {
      alert("Please remove pressure words before sharing.");
      return;
    }
    try {
      setIsFetchingBill(true);
      let filesToShare: File[] = [];
      if (shareModal.bills && shareModal.bills.length > 0) {
        try {
          const { blob, mime } = await fetchBillBlob(shareModal.bills[0].id);
          const ext = mime === "application/pdf" ? "pdf" : "jpg";
          const file = new File(
            [blob],
            `bill_${shareModal.customerName.replace(/\s+/g, "_")}.${ext}`,
            { type: mime }
          );
          if (navigator.canShare && navigator.canShare({ files: [file] })) {
            filesToShare = [file];
          }
        } catch (fetchErr) {
          console.warn("Could not attach bill to Web Share:", fetchErr);
        }
      }

      const shareData: ShareData = {
        title: `Voice Khata - ${shareModal.customerName}`,
        text: shareCustomText,
        ...(filesToShare.length > 0 ? { files: filesToShare } : {}),
      };

      await navigator.share(shareData);

      sendJson({
        t: "share.logged",
        customer_id: shareModal.customerId,
        event_id: shareModal.eventId,
        channel: "webshare",
      });
      setShareModal(null);
    } catch {
      // Share cancelled
    } finally {
      setIsFetchingBill(false);
    }
  }, [shareModal, shareCustomText, fetchBillBlob, sendJson]);

  // Group payments by date for the "You Got" view, with total received calculated for each date
  const groupedPayments = useMemo(() => {
    const groups: {
      date: string;
      displayDate: string;
      totalReceived: number;
      items: PaymentItem[];
    }[] = [];
    const dateMap = new Map<string, PaymentItem[]>();

    for (const p of payments) {
      const dateKey = p.effective_date;
      if (!dateMap.has(dateKey)) {
        dateMap.set(dateKey, []);
      }
      dateMap.get(dateKey)!.push(p);
    }

    const todayStr = new Date().toISOString().slice(0, 10);
    const yesterday = new Date(Date.now() - 86400000).toISOString().slice(0, 10);

    for (const [dateKey, items] of dateMap.entries()) {
      const totalReceived = items.reduce((sum, item) => sum + item.amount_inr, 0);
      let displayDate = dateKey;
      if (dateKey === todayStr) {
        displayDate = `Today (${dateKey})`;
      } else if (dateKey === yesterday) {
        displayDate = `Yesterday (${dateKey})`;
      } else {
        try {
          const d = new Date(dateKey + "T00:00:00");
          displayDate = d.toLocaleDateString("en-IN", {
            day: "numeric",
            month: "short",
            year: "numeric",
          });
        } catch {
          displayDate = dateKey;
        }
      }
      groups.push({
        date: dateKey,
        displayDate,
        totalReceived,
        items,
      });
    }

    // Sort descending by date
    groups.sort((a, b) => b.date.localeCompare(a.date));
    return groups;
  }, [payments]);

  const filteredGroupedPayments = useMemo(() => {
    if (!paymentSearch.trim()) return groupedPayments;
    const q = paymentSearch.toLowerCase().trim();
    return groupedPayments
      .map((group) => {
        const matchingItems = group.items.filter((item) =>
          item.customer_name.toLowerCase().includes(q)
        );
        return {
          ...group,
          items: matchingItems,
          totalReceived: matchingItems.reduce((sum, item) => sum + item.amount_inr, 0),
        };
      })
      .filter((group) => group.items.length > 0);
  }, [groupedPayments, paymentSearch]);

  const filteredCreditEntries = useMemo(() => {
    if (!customerSearch.trim()) return credits;
    const q = customerSearch.toLowerCase().trim();
    return credits.filter((item) =>
      item.customer_name.toLowerCase().includes(q)
    );
  }, [credits, customerSearch]);

  const customerRecentTransactions = useMemo(() => {
    if (!selectedCustomer) return [];
    const nameLower = selectedCustomer.name.toLowerCase();
    const custCredits = credits
      .filter((c) => c.customer_name.toLowerCase().includes(nameLower) || nameLower.includes(c.customer_name.toLowerCase()))
      .map((c) => ({
        id: c.event_id,
        desc: c.items && c.items.length > 0 ? c.items.map((i) => `${i.name}${i.qty_text ? ` (${i.qty_text})` : ""}`).join(", ") : "Rice & Dal (5kg)",
        date: c.formatted_time || c.effective_date || "Today",
        amount: c.amount_inr,
        type: "credit" as const,
      }));
    const custPayments = payments
      .filter((p) => p.customer_name.toLowerCase().includes(nameLower) || nameLower.includes(p.customer_name.toLowerCase()))
      .map((p) => ({
        id: p.event_id,
        desc: p.note || "Monthly Settlement",
        date: p.formatted_time || p.effective_date || "Today",
        amount: p.amount_inr,
        type: "payment" as const,
      }));
    const combined = [...custCredits, ...custPayments];
    if (combined.length === 0) {
      return [
        { id: "tx-1", desc: "Rice & Dal (5kg)", date: "21 Nov 2023", amount: 1200, type: "credit" as const },
        { id: "tx-2", desc: "Monthly Settlement", date: "18 Nov 2023", amount: 2500, type: "payment" as const },
        { id: "tx-3", desc: "Oil & Spices", date: "15 Nov 2023", amount: 850, type: "credit" as const },
        { id: "tx-4", desc: "Milk & Bread (Weekly)", date: "12 Nov 2023", amount: 450, type: "credit" as const },
      ];
    }
    return combined.slice(0, 5);
  }, [selectedCustomer, credits, payments]);

  if (viewMode === "landing") {
    return (
      <div className="page nb-landing-page-wrap">
        {errorText && (
          <div className="error-bar" role="alert">
            {errorText}
          </div>
        )}
        <LandingPage
          onLaunchApp={(targetMode) => handleViewChange(targetMode || "home")}
          summary={summary}
          totalDebtors={computedDebtorCount}
        />
      </div>
    );
  }

  return (
    <div className="page">
      <header className="topbar">
        <div className="title-group">
          <button
            type="button"
            className="logo-btn"
            onClick={() => handleViewChange("landing")}
            title="Go to Landing Page"
          >
            <div className="topbar-logo-icon" aria-hidden="true">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
                <path
                  d="M13 2L3 14H12L11 22L21 10H12L13 2Z"
                  fill="#ffe17c"
                  stroke="#171e19"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </div>
            <h1>Voice Khata</h1>
          </button>
          <span className="app-subtitle">स्मार्ट आवाज़ बहीखाता</span>
          <span className={`state-badge state-${pttState.toLowerCase()}`}>{pttState}</span>
        </div>

        <div className="header-right">
          <button
            type="button"
            className="nb-topbar-landing-btn"
            onClick={() => handleViewChange("landing")}
            title="View Landing Page & Features"
          >
            ⚡ Landing &amp; Features
          </button>
          <div className="latency-bar" aria-live="polite">
            <span className={`latency-pill latency-${latency.tone}`}>
              Turn: {latency.lastMs !== null ? `${latency.lastMs} ms` : "—"}
            </span>
            <span className="latency-stat">
              p50: {latency.p50Ms !== null ? `${latency.p50Ms} ms` : "—"}
            </span>
            <span className="latency-stat">
              p95: {latency.p95Ms !== null ? `${latency.p95Ms} ms` : "—"}
            </span>
          </div>
        </div>
      </header>

      {errorText && (
        <div className="error-bar" role="alert">
          {errorText}
        </div>
      )}

      {/* 1. TOP SUMMARY METRICS DASHBOARD */}
      <section className="summary-dashboard" aria-label="Khata summary overview">
        {selectedCustomer ? (
          <>
            <div className="summary-card card-debtors">
              <div className="summary-card-icon" aria-hidden="true">
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path>
                  <circle cx="9" cy="7" r="4"></circle>
                  <path d="M23 21v-2a4 4 0 0 0-3-3.87"></path>
                  <path d="M16 3.13a4 4 0 0 1 0 7.75"></path>
                </svg>
              </div>
              <div className="summary-card-content">
                <span className="summary-card-label">TOTAL BALANCE OWED</span>
                <span className="summary-card-value amount-owed-value">
                  {formatInr(selectedCustomer.amountOwed)}
                </span>
                <span className="summary-card-sub">Total pending from this customer</span>
              </div>
            </div>

            <div className="summary-card card-owed">
              <div className="summary-card-icon" aria-hidden="true">
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path>
                  <circle cx="9" cy="7" r="4"></circle>
                  <path d="M23 21v-2a4 4 0 0 0-3-3.87"></path>
                  <path d="M16 3.13a4 4 0 0 1 0 7.75"></path>
                </svg>
              </div>
              <div className="summary-card-content">
                <span className="summary-card-label">TOTAL TRANSACTIONS</span>
                <span className="summary-card-value">
                  {customerRecentTransactions.length || 42}
                </span>
                <span className="summary-card-sub">Lifetime entries recorded</span>
              </div>
            </div>

            <div className="summary-card card-received">
              <div className="summary-card-icon" aria-hidden="true">
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path>
                  <circle cx="9" cy="7" r="4"></circle>
                  <path d="M23 21v-2a4 4 0 0 0-3-3.87"></path>
                  <path d="M16 3.13a4 4 0 0 1 0 7.75"></path>
                </svg>
              </div>
              <div className="summary-card-content">
                <span className="summary-card-label">LAST PAYMENT</span>
                <span className="summary-card-value amount-received-value">
                  ₹2,500
                </span>
                <span className="summary-card-sub">Received on 12 Nov 2023</span>
              </div>
            </div>
          </>
        ) : (
          <>
            <div className="summary-card card-debtors">
              <div className="summary-card-icon" aria-hidden="true">
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path>
                  <circle cx="9" cy="7" r="4"></circle>
                  <path d="M23 21v-2a4 4 0 0 0-3-3.87"></path>
                  <path d="M16 3.13a4 4 0 0 1 0 7.75"></path>
                </svg>
              </div>
              <div className="summary-card-content">
                <span className="summary-card-label">PEOPLE WHO OWE YOU</span>
                <span className="summary-card-value">
                  {computedDebtorCount}
                </span>
                <span className="summary-card-sub">Active customers with pending balance</span>
              </div>
            </div>

            <div className="summary-card card-owed">
              <div className="summary-card-icon" aria-hidden="true">
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path>
                  <circle cx="9" cy="7" r="4"></circle>
                  <path d="M23 21v-2a4 4 0 0 0-3-3.87"></path>
                  <path d="M16 3.13a4 4 0 0 1 0 7.75"></path>
                </svg>
              </div>
              <div className="summary-card-content">
                <span className="summary-card-label">TOTAL AMOUNT OWED</span>
                <span className="summary-card-value amount-owed-value">
                  {formatInr(computedTotalOwed)}
                </span>
                <span className="summary-card-sub">Total credit to be collected</span>
              </div>
            </div>

            <div className="summary-card card-received">
              <div className="summary-card-icon" aria-hidden="true">
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path>
                  <circle cx="9" cy="7" r="4"></circle>
                  <path d="M23 21v-2a4 4 0 0 0-3-3.87"></path>
                  <path d="M16 3.13a4 4 0 0 1 0 7.75"></path>
                </svg>
              </div>
              <div className="summary-card-content">
                <span className="summary-card-label">TOTAL RECEIVED TODAY</span>
                <span className="summary-card-value amount-received-value">
                  {formatInrRupees(summary.total_received_today)}
                </span>
                <span className="summary-card-sub">Today's total payments received</span>
              </div>
            </div>
          </>
        )}
      </section>

      {/* 2. MODE NAVIGATION TABS (Enclosed in a unified card) */}
      <div className="mode-nav-container">
        <div className="mode-nav-card" role="tablist" aria-label="Khata navigation modes">
          <button
            id="tab-home"
            role="tab"
            type="button"
            aria-selected={viewMode === "home"}
            className={`mode-nav-btn ${viewMode === "home" ? "active" : ""}`}
            onClick={() => handleViewChange("home")}
          >
            <span className="mode-tab-icon" aria-hidden="true">⊞</span>
            <div className="mode-tab-text">
              <span className="mode-tab-title">Home</span>
              <span className="mode-tab-sub">होम पेज (OVERVIEW)</span>
            </div>
          </button>

          <button
            id="tab-you-gave"
            role="tab"
            type="button"
            aria-selected={viewMode === "you_gave"}
            className={`mode-nav-btn ${viewMode === "you_gave" ? "active" : ""}`}
            onClick={() => handleViewChange("you_gave")}
          >
            <span className="mode-tab-icon arrow-red" aria-hidden="true">↗</span>
            <div className="mode-tab-text">
              <span className="mode-tab-title">You Gave</span>
              <span className="mode-tab-sub">उधार दिया (VOICE ENTRY)</span>
            </div>
            <span className="mode-nav-badge">{debtorRows.length} debtors</span>
          </button>

          <button
            id="tab-you-got"
            role="tab"
            type="button"
            aria-selected={viewMode === "you_got"}
            className={`mode-nav-btn ${viewMode === "you_got" ? "active" : ""}`}
            onClick={() => handleViewChange("you_got")}
          >
            <span className="mode-tab-icon arrow-green" aria-hidden="true">↙</span>
            <div className="mode-tab-text">
              <span className="mode-tab-title">You Got</span>
              <span className="mode-tab-sub">जमा मिला (PAYMENTS)</span>
            </div>
            <span className="mode-nav-badge">{payments.length} txns</span>
          </button>

          <button
            id="tab-landing"
            role="tab"
            type="button"
            aria-selected={false}
            className="mode-nav-btn"
            onClick={() => handleViewChange("landing")}
          >
            <span className="mode-tab-icon" aria-hidden="true">⚡</span>
            <div className="mode-tab-text">
              <span className="mode-tab-title">Landing</span>
              <span className="mode-tab-sub">FEATURES &amp; SAAS</span>
            </div>
          </button>
        </div>
      </div>

      {/* 3. MAIN WORKSPACE */}
      <main className="layout-container">
        {viewMode === "home" ? (
          selectedCustomer ? (
            /* ========================================================
               CUSTOMER PROFILE VIEW (SCREENSHOT 3)
               ======================================================== */
            <div className="layout customer-profile-layout">
              <div className="profile-toolbar-row">
                <button
                  type="button"
                  className="btn-back-home"
                  onClick={() => setSelectedCustomer(null)}
                >
                  &larr; Back to Home
                </button>
                <span className="profile-crumb-sub">
                  CUSTOMER PROFILE / <strong>{selectedCustomer.name}</strong>
                </span>
              </div>

              <div className="profile-grid">
                {/* Left Column: Customer Card + Notes */}
                <div className="profile-left-col">
                  <div className="card customer-detail-card card-accent-red">
                    <div className="cust-header-row">
                      <div className="cust-avatar-box">
                        <span>{selectedCustomer.name.charAt(0)}</span>
                      </div>
                      <div className="cust-name-col">
                        <h2 className="cust-big-name">{selectedCustomer.name}</h2>
                        <span className="cust-phone-joined">
                          📞 +91 98765-43210 &bull; Joined Nov 2023
                        </span>
                      </div>
                      <div className="cust-balance-col">
                        <span className="cust-balance-label">CURRENT BALANCE</span>
                        <span className="cust-balance-val">
                          {formatInr(selectedCustomer.amountOwed)}
                        </span>
                      </div>
                    </div>

                    <div className="cust-profile-actions">
                      <button
                        type="button"
                        className="btn-whatsapp-reminder"
                        onClick={() =>
                          setShareModal({
                            open: true,
                            eventId: "profile-share",
                            customerId: selectedCustomer.id,
                            customerName: selectedCustomer.name,
                            amount: selectedCustomer.amountOwed,
                            date: "Today",
                            balance: selectedCustomer.amountOwed,
                          })
                        }
                      >
                        💬 SEND WHATSAPP REMINDER
                      </button>
                      <button
                        type="button"
                        className="btn-settle-payment"
                        onClick={() => {
                          handleViewChange("you_got");
                          setGotTextEntryInput(`${selectedCustomer.name} `);
                        }}
                      >
                        ✓ SETTLE PAYMENT (जमा करें)
                      </button>
                    </div>
                  </div>

                  <div className="card personal-notes-card">
                    <h3 className="notes-heading">PERSONAL NOTES</h3>
                    <p className="notes-text">
                      &ldquo;Regular customer. Prefers paying monthly via UPI. Often buys groceries for his small restaurant.&rdquo;
                    </p>
                  </div>
                </div>

                {/* Right Column: Recent Transactions */}
                <div className="profile-right-col">
                  <div className="card recent-tx-card card-accent-green">
                    <div className="recent-tx-header">
                      <h3 className="recent-tx-title">RECENT 5 TRANSACTIONS</h3>
                      <span className="recent-tx-sub">LATEST LEDGER UPDATES</span>
                    </div>

                    <div className="recent-tx-table-wrap">
                      <table className="recent-tx-table">
                        <thead>
                          <tr>
                            <th>DATE &amp; DESC</th>
                            <th className="num">AMOUNT</th>
                          </tr>
                        </thead>
                        <tbody>
                          {customerRecentTransactions.map((tx) => (
                            <tr key={tx.id}>
                              <td>
                                <strong className="tx-date">{tx.date}</strong>
                                <span className="tx-desc">{tx.desc}</span>
                              </td>
                              <td className={`num tx-amount ${tx.type === "credit" ? "tx-red" : "tx-green"}`}>
                                ₹{tx.amount.toLocaleString("en-IN")} {tx.type === "credit" ? "↗" : "↙"}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          ) : (
            /* ========================================================
               HOME DASHBOARD (SCREENSHOT 1)
               ======================================================== */
            <div className="layout home-dashboard-layout">
              <div className="home-toolbar-row">
                <button
                  type="button"
                  className="btn-back-home"
                  onClick={() => handleViewChange("home")}
                >
                  &larr; Back to Home
                </button>
              </div>

              <div className="home-split-grid home-two-col-grid">
                {/* Left Column: HOLD TO TALK dark panel */}
                <div className="home-left-col">
                  <div className="voice-panel-dark">
                    <div className="voice-panel-status">
                      {pttState === "LISTENING" ? (
                        <><span className="dot-red" style={{ color: "#ff5f57" }}>●</span> RECORDING LIVE</>
                      ) : pttState === "PROCESSING" ? (
                        <><span className="dot-yellow" style={{ color: "#ffe17c" }}>●</span> PROCESSING ENTRY</>
                      ) : (
                        <><span className="status-dot-green">●</span> SYSTEM READY</>
                      )}
                    </div>

                    <button
                      type="button"
                      className={`voice-circle-btn ${pttState === "LISTENING" ? "listening ptt-listening" : ""}`}
                      disabled={pttDisabled}
                      onClick={handleToggleMic}
                      onPointerDown={handlePointerDown}
                      onPointerUp={handlePointerUp}
                      onPointerCancel={handlePointerUp}
                      aria-pressed={pttState === "LISTENING"}
                      title={pttLabel[pttState] || "Click or Hold to Record Voice"}
                      style={{ touchAction: "manipulation", userSelect: "none", WebkitUserSelect: "none" }}
                    >
                      {pttState === "LISTENING" ? (
                        <svg width="36" height="36" viewBox="0 0 24 24" fill="#ffffff">
                          <rect x="5" y="5" width="14" height="14" rx="2" />
                        </svg>
                      ) : (
                        <svg width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="#000000" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"></path>
                          <path d="M19 10v2a7 7 0 0 1-14 0v-2"></path>
                          <line x1="12" y1="19" x2="12" y2="23"></line>
                          <line x1="8" y1="23" x2="16" y2="23"></line>
                        </svg>
                      )}
                    </button>

                    <h2 className="voice-panel-title">
                      {pttState === "LISTENING" ? "🔴 Recording... Click to Stop" : pttState === "PROCESSING" ? "⚡ Processing Entry..." : "Click to Record Voice"}
                    </h2>
                    <p className="voice-panel-sub">
                      {pttState === "LISTENING"
                        ? "Listening... Speak your entry (e.g. \"Ramesh 500 udhaar\"), then click to stop."
                        : "Click mic once to speak (e.g. \"Ramesh 500 udhaar\"), click again to save."}
                    </p>

                    {pttState === "LISTENING" && (
                      <div className="ptt-mic-track mt-4" aria-hidden="true">
                        <span className="ptt-mic-fill" style={{ width: `${micActiveLevel}%` }} />
                      </div>
                    )}
                  </div>

                  {/* Disambiguation & Clarification & Parsed Action if active */}
                  {parsedAction && (
                    <div className="card parsed-action-card mt-4">
                      <div className="parsed-action-header">
                        <span className="parsed-action-badge">
                          ✓ {parsedAction.type || "ACTION PROPOSED"}
                        </span>
                        <div className="parsed-action-toolbar">
                          {parsedAction.event_id && (
                            <button
                              type="button"
                              className="btn-delete-entry"
                              onClick={() => {
                                if (parsedAction.event_id) {
                                  handleRequestDelete(
                                    parsedAction.event_id,
                                    parsedAction.customer || parsedAction.customer_name || "Customer",
                                    parsedAction.amount_paise ?? (parsedAction.amount_inr ? parsedAction.amount_inr * 100 : 0),
                                    "ENTRY",
                                    true
                                  );
                                }
                              }}
                            >
                              🗑 Delete Entry
                            </button>
                          )}
                        </div>
                      </div>
                      <div className="action-fields-grid">
                        <div className="action-field">
                          <span className="action-field-label">Customer</span>
                          <span className="action-field-val">
                            {parsedAction.customer || parsedAction.customer_name || "—"}
                          </span>
                        </div>
                        <div className="action-field">
                          <span className="action-field-label">Amount</span>
                          <span className="action-field-val">
                            {parsedAction.amount_paise != null
                              ? formatInr(parsedAction.amount_paise)
                              : parsedAction.amount_inr != null
                              ? formatInrRupees(parsedAction.amount_inr)
                              : "—"}
                          </span>
                        </div>
                      </div>
                    </div>
                  )}
                </div>

                {/* Right Column: DEBTORS LIST */}
                <div className="home-right-col">
                  <div className="card debtors-list-card card-accent-red">
                    <div className="debtors-header-row" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px', flexWrap: 'wrap' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <h2 className="debtors-title" style={{ margin: 0 }}>DEBTORS LIST</h2>
                        <span className="debtors-badge-count">
                          {debtorRows.length} ACTIVE DEBTORS
                        </span>
                      </div>
                      <button
                        type="button"
                        className="btn-add-customer-header"
                        onClick={() => {
                          setAddCustName("");
                          setAddCustPhone("");
                          setAddCustInitialUdhar("");
                          setAddCustNote("");
                          setAddCustStatus(null);
                          setAddCustomerModalOpen(true);
                        }}
                        title="Add a new customer with their mobile number / नया ग्राहक जोड़ें"
                      >
                        ➕ Add Customer
                      </button>
                    </div>
                    <span className="debtors-subtitle">REAL-TIME BALANCE TRACKING</span>

                    <div className="search-bar-row" style={{ display: 'flex', gap: '8px' }}>
                      <input
                        type="search"
                        className="customer-search-input"
                        placeholder="🔍 Filter active debtors..."
                        value={customerSearch}
                        onChange={(e) => setCustomerSearch(e.target.value)}
                        style={{ flex: 1 }}
                      />
                      {customerSearch.trim() && !filteredDebtorRows.some((r) => r.name.toLowerCase() === customerSearch.toLowerCase().trim()) && (
                        <button
                          type="button"
                          className="btn-add-customer-quick"
                          onClick={() => {
                            setAddCustName(customerSearch.trim());
                            setAddCustPhone("");
                            setAddCustInitialUdhar("");
                            setAddCustNote("");
                            setAddCustStatus(null);
                            setAddCustomerModalOpen(true);
                          }}
                          title={`Add "${customerSearch.trim()}" as a customer`}
                        >
                          ➕ Add &ldquo;{customerSearch.trim()}&rdquo;
                        </button>
                      )}
                    </div>

                    <div className="table-scroll">
                      <table className="ledger-table">
                        <thead>
                          <tr>
                            <th>CUSTOMER NAME</th>
                            <th className="num">AMOUNT OWED (₹)</th>
                          </tr>
                        </thead>
                        <tbody>
                          {filteredDebtorRows.length === 0 ? (
                            <tr>
                              <td colSpan={2} className="empty-row">
                                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '8px', padding: '12px' }}>
                                  <span>
                                    {customerSearch
                                      ? `No debtors matching "${customerSearch}"`
                                      : "No pending debts! All customers are settled."}
                                  </span>
                                  {customerSearch.trim() && (
                                    <button
                                      type="button"
                                      className="btn-add-customer-header"
                                      onClick={() => {
                                        setAddCustName(customerSearch.trim());
                                        setAddCustPhone("");
                                        setAddCustInitialUdhar("");
                                        setAddCustNote("");
                                        setAddCustStatus(null);
                                        setAddCustomerModalOpen(true);
                                      }}
                                    >
                                      ➕ Add &ldquo;{customerSearch.trim()}&rdquo; as Customer
                                    </button>
                                  )}
                                </div>
                              </td>
                            </tr>
                          ) : (
                            filteredDebtorRows.map((row) => {
                              const savedPhone = getCustomerPhone(row.id, row.name);
                              return (
                                <tr
                                  key={row.id}
                                  className="clickable-debtor-row"
                                  onClick={() => setSelectedCustomer(row)}
                                  title="Click to view customer details"
                                >
                                  <td>
                                    <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
                                      <strong className="customer-cell-name">{row.name}</strong>
                                      {savedPhone ? (
                                        <span className="customer-phone-badge" style={{ fontSize: '11px', color: '#15803d', fontWeight: 800 }}>
                                          📱 +91 {savedPhone}
                                        </span>
                                      ) : (
                                        <span
                                          className="customer-add-phone-link"
                                          style={{ fontSize: '11px', color: '#71717a', textDecoration: 'underline', cursor: 'pointer' }}
                                          onClick={(e) => {
                                            e.stopPropagation();
                                            setAddCustName(row.name);
                                            setAddCustPhone("");
                                            setAddCustInitialUdhar("");
                                            setAddCustNote("");
                                            setAddCustomerModalOpen(true);
                                          }}
                                          title="Add contact number for WhatsApp receipt"
                                        >
                                          + Add Phone (WhatsApp)
                                        </span>
                                      )}
                                    </div>
                                  </td>
                                  <td className="num amount-owed text-red" style={{ verticalAlign: 'middle' }}>
                                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: '8px' }}>
                                      <span>{formatInr(row.amountOwed)}</span>
                                      <button
                                        type="button"
                                        className="mini-action-btn mini-whatsapp-btn"
                                        title={`WhatsApp पर रसीद भेजें / Send WhatsApp receipt to ${row.name}`}
                                        onClick={(e) => {
                                          e.stopPropagation();
                                          const latestCredit = credits.find(
                                            (c) => c.customer_id === row.id || c.customer_name?.toLowerCase() === row.name.toLowerCase()
                                          );
                                          handleOpenShare(
                                            latestCredit?.event_id || "",
                                            row.id,
                                            row.name,
                                            latestCredit?.amount_inr || Math.round(row.amountOwed / 100),
                                            latestCredit?.effective_date || new Date().toISOString().slice(0, 10),
                                            latestCredit?.items,
                                            Math.round(row.amountOwed / 100),
                                            latestCredit?.bills,
                                            "whatsapp",
                                            true // Direct send if phone exists!
                                          );
                                        }}
                                      >
                                        <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor">
                                          <path d="M12.031 6.172c-3.181 0-5.767 2.586-5.768 5.766-.001 1.298.38 2.27 1.019 3.287l-.582 2.128 2.182-.573c.978.58 1.911.928 3.145.929 3.178 0 5.767-2.587 5.768-5.766.001-3.187-2.575-5.77-5.764-5.771zm3.392 8.244c-.144.405-.837.774-1.17.824-.299.045-.677.063-1.092-.069-.252-.08-.575-.187-.988-.365-1.739-.751-2.874-2.502-2.961-2.617-.087-.116-.708-.94-.708-1.793s.448-1.273.607-1.446c.159-.173.346-.217.462-.217l.332.006c.106.005.249-.04.39.298.144.347.491 1.2.534 1.287.043.087.072.188.014.304-.058.116-.087.188-.173.289l-.26.304c-.087.086-.177.18-.076.354.101.174.449.741.964 1.201.662.591 1.221.774 1.394.86s.275.072.376-.043c.101-.116.433-.506.549-.68.116-.173.231-.145.39-.087s1.011.477 1.184.564.289.13.332.202c.045.072.045.419-.1.824zm-3.423-14.416c-6.627 0-12 5.373-12 12 0 2.17.58 4.204 1.597 5.959l-1.697 6.285 6.438-1.688c1.691.923 3.633 1.444 5.662 1.444 6.627 0 12-5.373 12-12 0-6.627-5.373-12-12-12z"/>
                                        </svg>
                                        <span>WA</span>
                                      </button>
                                    </div>
                                  </td>
                                </tr>
                              );
                            })
                          )}
                        </tbody>
                      </table>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          )
        ) : viewMode === "you_gave" ? (
          /* ========================================================
             YOU GAVE SECTION: PTT "HOLD TO TALK / PRESS TO SPEAK"
             APPEARS HERE, MATCHING SCREENSHOT 2
             ======================================================== */
          <div className="home-dashboard-layout">
            <div className="section-toolbar">
              <button
                type="button"
                className="btn-back-home"
                onClick={() => handleViewChange("home")}
              >
                &larr; Back to Home
              </button>
            </div>

            <div className="home-two-col-grid">
              {/* Left Column: NEW CREDIT ENTRY (नया उधार) */}
              <div className="home-left-col">
                <div className="card card-accent-red">
                  <div className="debtors-header-row">
                    <h2 className="debtors-title">NEW CREDIT ENTRY (नया उधार)</h2>
                  </div>
                  <span className="debtors-subtitle">CLICK BUTTON TO SPEAK OR TYPE</span>

                  <div className="voice-panel-dark">
                    <div className="voice-panel-status">
                      {pttState === "LISTENING" ? (
                        <><span className="dot-red" style={{ color: "#ff5f57" }}>●</span> RECORDING LIVE</>
                      ) : pttState === "PROCESSING" ? (
                        <><span className="dot-yellow" style={{ color: "#ffe17c" }}>●</span> PROCESSING ENTRY</>
                      ) : (
                        <><span className="dot-green">●</span> SYSTEM READY</>
                      )}
                    </div>

                    <button
                      type="button"
                      className={`voice-circle-btn ptt-${pttState.toLowerCase()} ${pttState === "LISTENING" ? "ptt-listening" : ""}`}
                      disabled={pttDisabled}
                      onClick={handleToggleMic}
                      onPointerDown={handlePointerDown}
                      onPointerUp={handlePointerUp}
                      onPointerCancel={handlePointerUp}
                      aria-pressed={pttState === "LISTENING"}
                      title={pttLabel[pttState] || "Click or Hold to Record Voice"}
                      style={{ touchAction: "manipulation", userSelect: "none", WebkitUserSelect: "none" }}
                    >
                      {pttState === "LISTENING" ? (
                        <svg width="36" height="36" viewBox="0 0 24 24" fill="#ffffff">
                          <rect x="5" y="5" width="14" height="14" rx="2" />
                        </svg>
                      ) : (
                        <svg width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="#000000" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"></path>
                          <path d="M19 10v2a7 7 0 0 1-14 0v-2"></path>
                          <line x1="12" y1="19" x2="12" y2="23"></line>
                          <line x1="8" y1="23" x2="16" y2="23"></line>
                        </svg>
                      )}
                    </button>

                    <h3 className="voice-panel-title">
                      {pttState === "LISTENING" ? "🔴 Recording Voice... Click to Stop" : pttState === "PROCESSING" ? "⚡ Processing Entry..." : "Click to Record Voice"}
                    </h3>
                    <p className="voice-panel-sub">
                      {pttState === "LISTENING"
                        ? "Listening... Speak your entry (e.g. \"Ramesh 500 udhaar\"), then click to stop."
                        : "Click mic once to speak (e.g. \"Ramesh 500 udhaar\"), click again to save."}
                    </p>

                    {pttState === "LISTENING" && (
                      <div className="ptt-mic-track mt-4" aria-hidden="true">
                        <span className="ptt-mic-fill" style={{ width: `${micActiveLevel}%` }} />
                      </div>
                    )}
                  </div>

                  <div className="manual-entry-box">
                    <div className="manual-entry-label">OR TYPE ENTRY</div>
                    <form onSubmit={handleTextSubmit} className="quick-text-form" aria-label="Manual text credit entry">
                      <input
                        type="text"
                        className="quick-text-input"
                        placeholder="e.g. Ramesh 500 udhaar / Suresh 200..."
                        value={textEntryInput}
                        onChange={(e) => setTextEntryInput(e.target.value)}
                      />
                      <button
                        type="submit"
                        className="quick-text-btn"
                        disabled={!textEntryInput.trim()}
                      >
                        Record ↵
                      </button>
                    </form>
                  </div>

                  {/* DISAMBIGUATION CANDIDATE PICKER */}
                  {parsedAction?.candidates && parsedAction.candidates.length > 0 && (
                    <div className="candidate-picker-box">
                      <div className="candidate-picker-header">
                        <span className="candidate-picker-icon">👥</span>
                        <strong>Multiple customers match &ldquo;{parsedAction.customer || parsedAction.customer_name}&rdquo;. Tap to select:</strong>
                      </div>
                      <div className="candidate-chip-list">
                        {parsedAction.candidates.map((cand) => (
                          <button
                            key={cand}
                            type="button"
                            className="candidate-chip-btn"
                            onClick={() => handleSelectCandidate(cand)}
                          >
                            ✓ {cand}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* CLARIFICATION REQUIRED (Item sum mismatch) */}
                  {parsedAction?.type === "CLARIFICATION_REQUIRED" && (
                    <div className="card clarification-card">
                      <div className="clarification-header">
                        <span className="clarification-icon" aria-hidden="true">⚠️</span>
                        <div>
                          <h3>Amount Clarification Needed</h3>
                          <p className="clarification-question">
                            {parsedAction.question || "Items total does not match stated total."}
                          </p>
                        </div>
                      </div>

                      {parsedAction.items && parsedAction.items.length > 0 && (
                        <div className="clarification-items-preview">
                          <span className="clarification-items-label">Recognized Items:</span>
                          <div className="clarification-items-chips">
                            {parsedAction.items.map((it, idx) => (
                              <span key={idx} className="clarification-chip">
                                {it.name} {it.qty_text ? `(${it.qty_text})` : ""}: ₹{it.amount_inr}
                              </span>
                            ))}
                          </div>
                        </div>
                      )}

                      <div className="clarification-actions">
                        {parsedAction.items_sum != null && (
                          <button
                            type="button"
                            className="clarify-btn clarify-items"
                            onClick={() =>
                              sendJson({
                                t: "inject_text",
                                text: `${parsedAction.items_sum} sahi hai`,
                              })
                            }
                          >
                            ✓ Keep Items Total: ₹{parsedAction.items_sum}
                          </button>
                        )}
                        {parsedAction.stated_total != null && (
                          <button
                            type="button"
                            className="clarify-btn clarify-total"
                            onClick={() =>
                              sendJson({
                                t: "inject_text",
                                text: `${parsedAction.stated_total} sahi hai`,
                              })
                            }
                          >
                            ✓ Keep Spoken Total: ₹{parsedAction.stated_total}
                          </button>
                        )}
                        <button
                          type="button"
                          className="clarify-btn clarify-cancel"
                          onClick={() =>
                            sendJson({
                              t: "inject_text",
                              text: "cancel",
                            })
                          }
                        >
                          ✕ Cancel
                        </button>
                      </div>
                    </div>
                  )}

                  {uploadProgress && (
                    <div className="upload-progress-card">
                      <div className="progress-info">
                        <span>📎 Uploading encrypted bill photo over WebSocket...</span>
                        <span>
                          {Math.round((uploadProgress.received / uploadProgress.total) * 100)}%
                        </span>
                      </div>
                      <div className="progress-bar-track">
                        <div
                          className="progress-bar-fill"
                          style={{
                            width: `${Math.min(100, Math.round((uploadProgress.received / uploadProgress.total) * 100))}%`,
                          }}
                        />
                      </div>
                    </div>
                  )}

                  {uploadStatusMsg && (
                    <div className={`status-banner status-${uploadStatusMsg.type}`}>
                      <span>{uploadStatusMsg.text}</span>
                      <button
                        type="button"
                        className="close-status-btn"
                        onClick={() => setUploadStatusMsg(null)}
                      >
                        ✕
                      </button>
                    </div>
                  )}

                  <div className="card transcript-card mt-3">
                    <div className="card-header-row">
                      <h2>Live Transcript</h2>
                      {finalTranscript && <span className="badge-recognized">Final</span>}
                    </div>
                    <div className="transcript-body" aria-live="polite">
                      {partialTranscript ? (
                        <p className="transcript-partial">{partialTranscript}</p>
                      ) : finalTranscript ? (
                        <p className="transcript-final">“{finalTranscript}”</p>
                      ) : (
                        <p className="placeholder">
                          Hold the button or Spacebar and speak (Hindi, English, Telugu, Kannada).
                        </p>
                      )}
                    </div>
                  </div>

                  {parsedAction && (
                    <div className="card parsed-action-card mt-3">
                      <div className="parsed-action-header">
                        <span className="parsed-action-badge">
                          ✓ {parsedAction.type || "CREDIT RECORDED"}
                        </span>
                        <div className="parsed-action-toolbar">
                          {parsedAction.event_id && (
                            <button
                              type="button"
                              className="btn-delete-entry"
                              onClick={() => {
                                if (parsedAction.event_id) {
                                  handleRequestDelete(
                                    parsedAction.event_id,
                                    parsedAction.customer || parsedAction.customer_name || "Customer",
                                    parsedAction.amount_paise ?? (parsedAction.amount_inr ? parsedAction.amount_inr * 100 : 0),
                                    "GAVE",
                                    true
                                  );
                                }
                              }}
                              title="Delete transaction directly from database"
                            >
                              🗑 Delete Entry
                            </button>
                          )}
                        </div>
                      </div>
                      <div className="action-fields-grid">
                        <div className="action-field">
                          <span className="action-field-label">Customer</span>
                          <span className="action-field-val">
                            {parsedAction.customer || parsedAction.customer_name || "—"}
                          </span>
                        </div>
                        <div className="action-field">
                          <span className="action-field-label">Amount</span>
                          <span className="action-field-val text-red">
                            {parsedAction.amount_paise != null
                              ? formatInr(parsedAction.amount_paise)
                              : parsedAction.amount_inr != null
                              ? formatInrRupees(parsedAction.amount_inr)
                              : "—"}
                          </span>
                        </div>
                      </div>

                      {parsedAction.event_id && (
                        <div className="entry-actions-toolbar mt-3">
                          <button
                            type="button"
                            className="entry-action-btn btn-items"
                            onClick={() =>
                              handleOpenItemsEditor(
                                parsedAction.event_id || "",
                                parsedAction.customer || parsedAction.customer_name || "Customer",
                                parsedAction.amount_inr || 0,
                                parsedAction.items
                              )
                            }
                          >
                            📝 {parsedAction.items && parsedAction.items.length > 0 ? "Edit Items" : "Add Items"}
                          </button>

                          <button
                            type="button"
                            className="entry-action-btn btn-bill"
                            disabled={uploadingForEventId === (parsedAction.event_id || "")}
                            onClick={() => handleTriggerUploadForEvent(parsedAction.event_id || "")}
                          >
                            {uploadingForEventId === (parsedAction.event_id || "")
                              ? "📎 Uploading…"
                              : "📎 Attach Bill Photo"}
                          </button>

                          <button
                            type="button"
                            className="entry-action-btn btn-whatsapp-entry"
                            onClick={() => {
                              const matchingCredit = credits.find(
                                (c) => c.event_id === parsedAction.event_id
                              );
                              handleOpenShare(
                                parsedAction.event_id || "",
                                parsedAction.customer_id || matchingCredit?.customer_id || "",
                                parsedAction.customer || parsedAction.customer_name || "Customer",
                                parsedAction.amount_inr || 0,
                                parsedAction.effective_date || "",
                                parsedAction.items,
                                undefined,
                                matchingCredit?.bills,
                                "whatsapp",
                                true
                              );
                            }}
                            title="Send Receipt via WhatsApp"
                          >
                            💬 WhatsApp
                          </button>

                          <button
                            type="button"
                            className="entry-action-btn btn-sms-entry"
                            onClick={() => {
                              const matchingCredit = credits.find(
                                (c) => c.event_id === parsedAction.event_id
                              );
                              handleOpenShare(
                                parsedAction.event_id || "",
                                parsedAction.customer_id || matchingCredit?.customer_id || "",
                                parsedAction.customer || parsedAction.customer_name || "Customer",
                                parsedAction.amount_inr || 0,
                                parsedAction.effective_date || "",
                                parsedAction.items,
                                undefined,
                                matchingCredit?.bills,
                                "sms",
                                true
                              );
                            }}
                            title="Send Receipt via SMS"
                          >
                            ✉️ SMS
                          </button>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>

              {/* Right Column: CREDIT ENTRIES YOU GAVE */}
              <div className="home-right-col">
                <div className="card card-accent-red">
                  <div className="debtors-header-row">
                    <h2 className="debtors-title">CREDIT ENTRIES YOU GAVE</h2>
                    <span className="debtors-badge-count">
                      {filteredCreditEntries.length} ENTRIES
                    </span>
                  </div>
                  <span className="debtors-subtitle">PERMANENT RECORD OF CREDIT EXTENDED TO CUSTOMERS</span>

                  <div className="search-bar-row">
                    <input
                      type="search"
                      className="customer-search-input"
                      placeholder="🔍 Filter credit entries..."
                      value={customerSearch}
                      onChange={(e) => setCustomerSearch(e.target.value)}
                    />
                    {customerSearch && (
                      <button
                        type="button"
                        className="clear-search-btn"
                        onClick={() => setCustomerSearch("")}
                      >
                        ✕
                      </button>
                    )}
                  </div>

                  <div className="table-scroll">
                    <table className="ledger-table">
                      <thead>
                        <tr>
                          <th>CUSTOMER NAME</th>
                          <th className="num">AMOUNT</th>
                          <th>DATE</th>
                          <th>STATUS</th>
                          <th className="num">ACTION</th>
                        </tr>
                      </thead>
                      <tbody>
                        {filteredCreditEntries.length === 0 ? (
                          <tr>
                            <td colSpan={5} className="empty-row">
                              {customerSearch
                                ? `No credit entries matching "${customerSearch}"`
                                : "No credit entries recorded yet."}
                            </td>
                          </tr>
                        ) : (
                          filteredCreditEntries.map((cr) => (
                            <tr key={cr.event_id || cr.seq}>
                              <td>
                                <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
                                  <strong className="customer-cell-name">{cr.customer_name}</strong>
                                  {(() => {
                                    const ph = getCustomerPhone(cr.customer_id, cr.customer_name);
                                    return ph ? (
                                      <span style={{ fontSize: '11px', color: '#15803d', fontWeight: 800 }}>
                                        📱 +91 {ph}
                                      </span>
                                    ) : null;
                                  })()}
                                </div>
                              </td>
                              <td className="num amount-owed text-red">
                                {cr.amount_paise !== undefined ? formatInr(cr.amount_paise) : formatInrRupees(cr.amount_inr)}
                              </td>
                              <td className="payment-time-cell">
                                {cr.formatted_time || cr.effective_date}
                              </td>
                              <td>
                                <span className="badge-status badge-confirmed">CONFIRMED</span>
                              </td>
                              <td className="num">
                                <div className="table-actions-cell">
                                  <button
                                    type="button"
                                    className="mini-action-btn"
                                    onClick={() =>
                                      handleOpenItemsEditor(
                                        cr.event_id,
                                        cr.customer_name,
                                        cr.amount_inr,
                                        cr.items
                                      )
                                    }
                                    title="Edit Items"
                                  >
                                    📝
                                  </button>

                                  {((cr.bills && cr.bills.length > 0) || cr.has_bill) ? (
                                    <button
                                      type="button"
                                      className="mini-action-btn"
                                      onClick={() =>
                                        handleViewBill(
                                          cr.bills && cr.bills.length > 0 ? cr.bills[0].id : cr.event_id,
                                          `${cr.customer_name} Bill`
                                        )
                                      }
                                      title="View Bill Photo"
                                    >
                                      📎
                                    </button>
                                  ) : (
                                    <button
                                      type="button"
                                      className="mini-action-btn"
                                      disabled={uploadingForEventId === cr.event_id}
                                      onClick={() => handleTriggerUploadForEvent(cr.event_id)}
                                      title="Attach Bill Photo"
                                    >
                                      📎
                                    </button>
                                  )}

                                  {/* WhatsApp Symbol */}
                                  <button
                                    type="button"
                                    className="mini-action-btn mini-whatsapp-btn"
                                    onClick={() =>
                                      handleOpenShare(
                                        cr.event_id,
                                        cr.customer_id,
                                        cr.customer_name,
                                        cr.amount_inr,
                                        cr.formatted_time || cr.effective_date,
                                        cr.items,
                                        undefined,
                                        cr.bills,
                                        "whatsapp",
                                        true
                                      )
                                    }
                                    title="Send Receipt via WhatsApp / व्हाट्सएप पर रसीद भेजें"
                                    aria-label="Send Receipt via WhatsApp"
                                  >
                                    <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
                                      <path d="M12.031 6.172c-3.181 0-5.767 2.586-5.768 5.766-.001 1.298.38 2.27 1.019 3.287l-.582 2.128 2.182-.573c.978.58 1.911.928 3.145.929 3.178 0 5.767-2.587 5.768-5.766.001-3.187-2.575-5.77-5.764-5.771zm3.392 8.244c-.144.405-.837.774-1.17.824-.299.045-.677.063-1.092-.069-.252-.08-.575-.187-.988-.365-1.739-.751-2.874-2.502-2.961-2.617-.087-.116-.708-.94-.708-1.793s.448-1.273.607-1.446c.159-.173.346-.217.462-.217l.332.006c.106.005.249-.04.39.298.144.347.491 1.2.534 1.287.043.087.072.188.014.304-.058.116-.087.188-.173.289l-.26.304c-.087.086-.177.18-.076.354.101.174.449.741.964 1.201.662.591 1.221.774 1.394.86s.275.072.376-.043c.101-.116.433-.506.549-.68.116-.173.231-.145.39-.087s1.011.477 1.184.564.289.13.332.202c.045.072.045.419-.1.824zm-3.423-14.416c-6.627 0-12 5.373-12 12 0 2.17.58 4.204 1.597 5.959l-1.697 6.285 6.438-1.688c1.691.923 3.633 1.444 5.662 1.444 6.627 0 12-5.373 12-12 0-6.627-5.373-12-12-12z"/>
                                    </svg>
                                  </button>

                                  {/* Message / SMS Symbol */}
                                  <button
                                    type="button"
                                    className="mini-action-btn mini-sms-btn"
                                    onClick={() =>
                                      handleOpenShare(
                                        cr.event_id,
                                        cr.customer_id,
                                        cr.customer_name,
                                        cr.amount_inr,
                                        cr.formatted_time || cr.effective_date,
                                        cr.items,
                                        undefined,
                                        cr.bills,
                                        "sms",
                                        true
                                      )
                                    }
                                    title="Send Receipt via SMS / मैसेज भेजें"
                                    aria-label="Send Receipt via SMS"
                                  >
                                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                                      <path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"></path>
                                      <polyline points="22,6 12,13 2,6"></polyline>
                                    </svg>
                                  </button>

                                  <button
                                    type="button"
                                    className="btn-delete-tx"
                                    onClick={() =>
                                      handleRequestDelete(
                                        cr.event_id,
                                        cr.customer_name,
                                        cr.amount_paise ?? cr.amount_inr * 100,
                                        "GAVE",
                                        true
                                      )
                                    }
                                    title="Delete credit directly from database"
                                  >
                                    🗑 Delete
                                  </button>
                                </div>
                              </td>
                            </tr>
                          ))
                        )}
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>
            </div>
          </div>
        ) : (
          /* ========================================================
             YOU GOT SECTION: ALL CREATED MONEY / PAYMENTS RECEIVED
             MATCHING SCREENSHOT 4
             ======================================================== */
          <div className="you-got-layout">
            <div className="daily-collection-banner card-accent-green">
              <div className="daily-banner-info">
                <span className="daily-banner-tag">DAILY COLLECTION SUMMARY</span>
                <h2>Total Money Received Today</h2>
                <p className="daily-banner-sub">
                  Sum of all customer payment deposits (जमा) recorded for today
                </p>
              </div>
              <div className="daily-banner-stat">
                <span className="daily-banner-num text-green">+{formatInrRupees(summary.total_received_today)}</span>
                <span className="daily-banner-caption">Total Received Today</span>
              </div>
            </div>

            <div className="section-toolbar you-got-toolbar">
              <button
                type="button"
                className="btn-back-home"
                onClick={() => handleViewChange("home")}
              >
                &larr; Back to Home
              </button>

              {/* QUICK CREDIT RECEIVED ENTRY FORM IN YOU GOT */}
              <form onSubmit={handleGotTextSubmit} className="quick-text-form got-entry-form" aria-label="Manual credit received entry">
                <input
                  type="text"
                  className="quick-text-input"
                  placeholder="⊕ Record received credit here: e.g. Ramesh 200 credit / Suresh 500 jama..."
                  value={gotTextEntryInput}
                  onChange={(e) => setGotTextEntryInput(e.target.value)}
                />
                <button
                  type="submit"
                  className="quick-text-btn btn-green btn-save-credit"
                  disabled={!gotTextEntryInput.trim()}
                >
                  + Save Received Credit ↵
                </button>
              </form>
            </div>

            <div className="card payments-history-card card-accent-green">
              <div className="payments-history-header">
                <div>
                  <h2 className="debtors-title">ALL RECEIVED PAYMENTS (जमा मिला हिसाब)</h2>
                  <span className="debtors-subtitle">
                    STORED PERMANENTLY IN DATABASE WITH EXACT TRANSACTION TIMESTAMP
                  </span>
                </div>
                <span className="debtors-badge-count">{payments.length} TRANSACTIONS RECORDED</span>
              </div>

              <div className="search-bar-row">
                <input
                  type="search"
                  className="customer-search-input"
                  placeholder="🔍 Search payments by customer name..."
                  value={paymentSearch}
                  onChange={(e) => setPaymentSearch(e.target.value)}
                />
                {paymentSearch && (
                  <button
                    type="button"
                    className="clear-search-btn"
                    onClick={() => setPaymentSearch("")}
                  >
                    ✕ Clear
                  </button>
                )}
              </div>

              {filteredGroupedPayments.length === 0 ? (
                <div className="empty-payments-state">
                  <div className="empty-icon" aria-hidden="true">📥</div>
                  <p className="empty-title">
                    {paymentSearch
                      ? `No payments matching "${paymentSearch}"`
                      : "No customer payments recorded yet"}
                  </p>
                  <p className="empty-desc">
                    {paymentSearch
                      ? "Try searching with another customer name."
                      : "When customers pay money back (e.g. \"Ramesh gave 200 rupees\"), each transaction will appear here with the customer name, amount, date & time, and total money received on that day."}
                  </p>
                </div>
              ) : (
                <div className="payment-groups-container">
                  {filteredGroupedPayments.map((group) => (
                    <div key={group.date} className="payment-day-group">
                      <div className="payment-day-header">
                        <div className="day-info">
                          <span className="day-date">{group.displayDate}</span>
                          <span className="day-count">
                            ({group.items.length} {group.items.length === 1 ? "payment" : "payments"})
                          </span>
                        </div>
                        <div className="day-total">
                          <span className="day-total-label">Day Total Received:</span>
                          <span className="day-total-amount text-green">+{formatInrRupees(group.totalReceived)}</span>
                        </div>
                      </div>

                      <div className="table-scroll">
                        <table className="payments-table">
                          <thead>
                            <tr>
                              <th scope="col">CUSTOMER NAME</th>
                              <th scope="col" className="num">MONEY SENT (₹)</th>
                              <th scope="col">DATE &amp; TIME</th>
                              <th scope="col">NOTE</th>
                              <th scope="col" className="status-col">STATUS</th>
                              <th scope="col" className="action-col num">ACTION</th>
                            </tr>
                          </thead>
                          <tbody>
                            {group.items.map((item) => (
                              <tr key={item.event_id || item.seq}>
                                <td>
                                  <strong className="customer-name-cell">{item.customer_name}</strong>
                                </td>
                                <td className="num payment-amount-cell text-green">
                                  +{item.amount_paise !== undefined ? formatInr(item.amount_paise) : formatInrRupees(item.amount_inr)}
                                </td>
                                <td className="payment-time-cell">
                                  {item.formatted_time || `${item.effective_date} ${item.ts.slice(11, 16)}`}
                                </td>
                                <td className="payment-note-cell">
                                  {item.note || <span className="empty-note">—</span>}
                                </td>
                                <td className="status-col">
                                  <span className="badge-status badge-confirmed">✓ RECEIVED</span>
                                </td>
                                <td className="action-col num">
                                  <button
                                    type="button"
                                    className="btn-delete-tx"
                                    onClick={() =>
                                      handleRequestDelete(
                                        item.event_id,
                                        item.customer_name,
                                        item.amount_paise ?? item.amount_inr * 100,
                                        "GOT",
                                        true
                                      )
                                    }
                                    title="Delete payment directly from database"
                                  >
                                    🗑 Delete
                                  </button>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}
      </main>

      {/* HIDDEN FILE INPUT FOR BILL PHOTO ATTACHMENT */}
      <input
        type="file"
        ref={hiddenFileInputRef}
        accept="image/jpeg,image/png,application/pdf"
        capture="environment"
        style={{ display: "none" }}
        onChange={handleFileInputChange}
      />

      {/* 1. ITEMISED BILL MODAL */}
      {itemsModal && itemsModal.open && (
        <div className="modal-backdrop" onClick={() => setItemsModal(null)}>
          <div className="modal-card modal-items" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <div>
                <h3>Itemised Bill / सामान की सूची</h3>
                <span className="modal-sub">
                  Customer: <strong>{itemsModal.customerName}</strong> &bull; Total Entry: <strong>{formatInrRupees(itemsModal.eventAmount)}</strong>
                </span>
              </div>
              <button
                type="button"
                className="modal-close-btn"
                onClick={() => setItemsModal(null)}
              >
                ✕
              </button>
            </div>

            {itemsEditorError && (
              <div className="modal-error-banner">{itemsEditorError}</div>
            )}

            <div className="modal-table-container">
              <table className="items-editor-table">
                <thead>
                  <tr>
                    <th>Item Name (सामान)</th>
                    <th>Qty / Unit (मात्रा)</th>
                    <th className="num">Amount (₹)</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {itemsModal.items.map((item, idx) => (
                    <tr key={idx}>
                      <td>
                        <input
                          type="text"
                          placeholder="e.g. Chawal, Dal, Sugar"
                          className="item-input"
                          value={item.name}
                          onChange={(e) => handleItemFieldChange(idx, "name", e.target.value)}
                        />
                      </td>
                      <td>
                        <input
                          type="text"
                          placeholder="e.g. 2 kg, 1 pc"
                          className="item-input qty-input"
                          value={item.qty_text || ""}
                          onChange={(e) => handleItemFieldChange(idx, "qty_text", e.target.value)}
                        />
                      </td>
                      <td>
                        <input
                          type="number"
                          min="0"
                          placeholder="₹"
                          className="item-input amt-input"
                          value={item.amount_inr === 0 && !item.name ? "" : item.amount_inr}
                          onChange={(e) =>
                            handleItemFieldChange(
                              idx,
                              "amount_inr",
                              Math.max(0, parseInt(e.target.value || "0", 10))
                            )
                          }
                        />
                      </td>
                      <td className="action-col">
                        <button
                          type="button"
                          className="delete-item-btn"
                          onClick={() => handleRemoveItemRow(idx)}
                          title="Remove item"
                        >
                          ✕
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="modal-row-btn">
              <button type="button" className="add-row-btn" onClick={handleAddItemRow}>
                + Add Item Line
              </button>
            </div>

            {(() => {
              const currentSum = itemsModal.items.reduce(
                (acc, it) => acc + (Number(it.amount_inr) || 0),
                0
              );
              const diff = Math.abs(currentSum - itemsModal.eventAmount);
              const isMatch = currentSum === itemsModal.eventAmount;
              return (
                <div
                  className={`items-sum-status ${
                    isMatch ? "sum-match" : "sum-mismatch"
                  }`}
                >
                  <div className="sum-labels">
                    <span>
                      Items Sum: <strong>{formatInrRupees(currentSum)}</strong>
                    </span>
                    <span>
                      Required Total: <strong>{formatInrRupees(itemsModal.eventAmount)}</strong>
                    </span>
                  </div>
                  {isMatch ? (
                    <span className="sum-verdict">✓ Total matches entry amount</span>
                  ) : (
                    <span className="sum-verdict">
                      ⚠ Difference of {formatInrRupees(diff)} (
                      {currentSum > itemsModal.eventAmount ? "Over" : "Under"}). Total must match{" "}
                      {formatInrRupees(itemsModal.eventAmount)}.
                    </span>
                  )}
                </div>
              );
            })()}

            <div className="modal-footer">
              <button
                type="button"
                className="modal-btn-cancel"
                onClick={() => setItemsModal(null)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="modal-btn-save"
                disabled={
                  itemsModal.items.reduce(
                    (acc, it) => acc + (Number(it.amount_inr) || 0),
                    0
                  ) !== itemsModal.eventAmount
                }
                onClick={handleSaveItems}
              >
                Save Items (बहीखाता में जोड़ें)
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 1.5 ADD CUSTOMER MODAL */}
      {addCustomerModalOpen && (
        <div className="modal-backdrop" onClick={() => setAddCustomerModalOpen(false)}>
          <div
            className="modal-card modal-add-customer"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="modal-header">
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <span style={{ fontSize: '22px' }}>👤</span>
                <div>
                  <h3 style={{ margin: 0 }}>नया ग्राहक जोड़ें / Add Customer</h3>
                  <span className="modal-sub">
                    Save customer &amp; mobile number for 1-click WhatsApp receipts
                  </span>
                </div>
              </div>
              <button
                type="button"
                className="modal-close-btn"
                onClick={() => setAddCustomerModalOpen(false)}
              >
                ✕
              </button>
            </div>

            <form onSubmit={handleCreateCustomerSubmit} className="modal-body add-customer-form">
              <div className="form-group">
                <label className="form-label">
                  <strong>Customer Name / ग्राहक का नाम</strong> <span style={{ color: '#ff5f57' }}>*</span>
                </label>
                <input
                  type="text"
                  className="form-input"
                  placeholder="e.g. Ramesh Kumar, Sunita, Raju"
                  value={addCustName}
                  onChange={(e) => setAddCustName(e.target.value)}
                  required
                  autoFocus
                />
              </div>

              <div className="form-group">
                <label className="form-label">
                  <strong>Mobile Number / मोबाइल नंबर (WhatsApp)</strong> <span style={{ color: '#ff5f57' }}>*</span>
                </label>
                <div className="phone-input-wrap">
                  <span className="phone-prefix">+91</span>
                  <input
                    type="tel"
                    className="phone-input"
                    placeholder="10-digit mobile number"
                    value={addCustPhone}
                    onChange={(e) => setAddCustPhone(e.target.value.replace(/\D/g, "").slice(0, 10))}
                    maxLength={10}
                    required
                  />
                </div>
                <span className="phone-privacy-hint">
                  🔒 Saved locally in your browser for 1-click WhatsApp messaging
                </span>
              </div>

              <div className="form-group">
                <label className="form-label">
                  <strong>Initial Udhar / शुरुआती उधार (₹ Optional)</strong>
                </label>
                <input
                  type="number"
                  className="form-input"
                  placeholder="e.g. 500 (Leave empty if none)"
                  value={addCustInitialUdhar}
                  onChange={(e) => setAddCustInitialUdhar(e.target.value)}
                  min="0"
                />
                <span className="phone-privacy-hint">
                  If entered, ₹{addCustInitialUdhar || "0"} will be added to ledger and a receipt generated immediately
                </span>
              </div>

              <div className="form-group">
                <label className="form-label">
                  <strong>Note / सामान विवरण (Optional)</strong>
                </label>
                <input
                  type="text"
                  className="form-input"
                  placeholder="e.g. Rice, Dal, Daily groceries"
                  value={addCustNote}
                  onChange={(e) => setAddCustNote(e.target.value)}
                />
              </div>

              {addCustStatus && (
                <div className="add-cust-status-banner">
                  ⏳ {addCustStatus}
                </div>
              )}

              <div className="modal-actions-row">
                <button
                  type="button"
                  className="modal-btn-cancel"
                  onClick={() => setAddCustomerModalOpen(false)}
                >
                  Cancel / रद्द करें
                </button>
                <button
                  type="submit"
                  className="share-channel-btn btn-whatsapp"
                  style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}
                  disabled={!addCustName.trim() || addCustPhone.replace(/\D/g, "").length !== 10}
                >
                  💾 Save Customer &amp; Contact
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* 2. SHARE RECEIPT MODAL */}
      {shareModal && shareModal.open && (
        <div className="modal-backdrop" onClick={() => setShareModal(null)}>
          <div
            className="modal-card modal-share"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="modal-header">
              <div>
                <h3>
                  {shareModal.initialChannel === "sms"
                    ? "✉️ Send Receipt via SMS / मैसेज भेजें"
                    : "💬 Send Receipt via WhatsApp / व्हाट्सएप पर रसीद भेजें"}
                </h3>
                <span className="modal-sub">
                  Customer: <strong>{shareModal.customerName}</strong> &bull; Udhar Added:{" "}
                  <strong className="text-red">₹{shareModal.amount}</strong>
                </span>
              </div>
              <button
                type="button"
                className="modal-close-btn"
                onClick={() => setShareModal(null)}
              >
                ✕
              </button>
            </div>

            {(() => {
              const cleanPhone = sharePhone.replace(/\D/g, "");
              const hasPressure = hasBannedPressureWords(shareCustomText);

              return (
                <div className="share-form-content">
                  {/* TRANSACTION SUMMARY STRIP */}
                  <div className="share-summary-strip">
                    <div className="share-sum-item">
                      <span className="share-sum-lbl">CUSTOMER / ग्राहक</span>
                      <strong className="share-sum-val">{shareModal.customerName}</strong>
                    </div>
                    <div className="share-sum-item">
                      <span className="share-sum-lbl">UDHAR ADDED / नया उधार</span>
                      <strong className="share-sum-val text-red">₹{shareModal.amount}</strong>
                    </div>
                    <div className="share-sum-item">
                      <span className="share-sum-lbl">TOTAL BALANCE / कुल बकाया</span>
                      <strong className="share-sum-val">₹{shareModal.balance}</strong>
                    </div>
                  </div>

                  {/* DIRECT RECEIPT LINK BOX */}
                  {shareModal.receiptUrl && (
                    <div className="share-receipt-link-box">
                      <div className="receipt-link-info">
                        <span className="receipt-link-badge">🔗 OFFICIAL DIGITAL RECEIPT LINK</span>
                        <span className="receipt-link-text">{shareModal.receiptUrl}</span>
                      </div>
                      <button
                        type="button"
                        className="btn-copy-receipt-link"
                        onClick={handleCopyReceiptLink}
                      >
                        {copiedReceiptLink ? "✓ Copied!" : "📋 Copy Link"}
                      </button>
                    </div>
                  )}

                  {/* MOBILE NUMBER INPUT */}
                  <div className="share-input-group">
                    <label htmlFor="share-phone-input">
                      <strong>Customer Mobile Number (ग्राहक का 10-अंकों का मोबाइल नंबर):</strong>
                    </label>
                    <div className="phone-input-wrap">
                      <span className="phone-prefix">+91</span>
                      <input
                        id="share-phone-input"
                        type="tel"
                        placeholder="10-digit number (e.g. 9876543210)"
                        maxLength={10}
                        autoFocus
                        className="phone-input"
                        value={sharePhone}
                        onChange={(e) =>
                          setSharePhone(e.target.value.replace(/\D/g, ""))
                        }
                      />
                    </div>
                    <div className="phone-privacy-hint">
                      🔒 Client-side autofill: phone numbers are never stored in the database ledger.
                    </div>
                  </div>

                  <div className="share-lang-tabs">
                    <span className="lang-label">Template Language:</span>
                    <button
                      type="button"
                      className={`lang-tab-btn ${shareLang === "hi" ? "active" : ""}`}
                      onClick={() => handleSelectShareLang("hi")}
                    >
                      हिन्दी (Hindi)
                    </button>
                    <button
                      type="button"
                      className={`lang-tab-btn ${shareLang === "en" ? "active" : ""}`}
                      onClick={() => handleSelectShareLang("en")}
                    >
                      English
                    </button>
                    <button
                      type="button"
                      className={`lang-tab-btn ${shareLang === "te" ? "active" : ""}`}
                      onClick={() => handleSelectShareLang("te")}
                    >
                      తెలుగు (Telugu)
                    </button>
                  </div>

                  <div className="share-preview-group">
                    <div className="preview-header">
                      <label htmlFor="share-text-area"><strong>Autofilled WhatsApp Message Preview:</strong></label>
                      {isCustomEdited && (
                        <span className="customized-pill">Customized</span>
                      )}
                    </div>
                    <textarea
                      id="share-text-area"
                      rows={7}
                      className="share-textarea"
                      value={shareCustomText}
                      onChange={(e) => {
                        setShareCustomText(e.target.value);
                        setIsCustomEdited(true);
                      }}
                    />
                    {hasPressure && (
                      <div className="pressure-warning-banner">
                        ⚠️ Restricted pressure words detected. Please maintain a polite tone without threats or legal terms.
                      </div>
                    )}
                  </div>

                  {shareModal.bills && shareModal.bills.length > 0 && (
                    <div className="share-bill-attachment-banner">
                      <div className="bill-attach-info">
                        <span className="bill-attach-badge">📎 Bill Photo Attached ({shareModal.bills.length})</span>
                        <span className="bill-attach-note">
                          Included in Web Share, or copy/download for WhatsApp:
                        </span>
                      </div>
                      <div className="bill-attach-actions">
                        <button
                          type="button"
                          className="bill-copy-btn"
                          disabled={isFetchingBill}
                          onClick={handleCopyShareBill}
                          title="Copies photo to clipboard. Paste with Ctrl+V into WhatsApp chat."
                        >
                          {copiedImage ? "✓ Photo Copied! (Press Ctrl+V in WhatsApp)" : "📋 Copy Photo to Clipboard"}
                        </button>
                        <button
                          type="button"
                          className="bill-download-btn"
                          disabled={isFetchingBill}
                          onClick={handleDownloadShareBill}
                          title="Downloads bill photo to your device"
                        >
                          ⬇ Download Photo
                        </button>
                      </div>
                    </div>
                  )}

                  <div className="share-actions-row">
                    <button
                      type="button"
                      className="share-channel-btn btn-whatsapp"
                      disabled={cleanPhone.length !== 10 || hasPressure}
                      onClick={handleShareWhatsApp}
                      title="Opens WhatsApp with customer receipt text and link pre-filled"
                    >
                      💬 Open WhatsApp &amp; Send
                    </button>
                    <button
                      type="button"
                      className="share-channel-btn btn-whatsapp-web"
                      disabled={cleanPhone.length !== 10 || hasPressure}
                      onClick={() => {
                        saveCustomerPhone(cleanPhone, shareModal.customerId, shareModal.customerName);
                        setConsentMap((prev) => ({ ...prev, [shareModal.customerId]: true }));
                        handleRecordConsent(shareModal.customerId);
                        const phoneWithCountry = cleanPhone.length === 10 ? `91${cleanPhone}` : cleanPhone;
                        const url = `https://web.whatsapp.com/send?phone=${phoneWithCountry}&text=${encodeURIComponent(shareCustomText)}`;
                        try {
                          const a = document.createElement("a");
                          a.href = url;
                          a.target = "_blank";
                          a.rel = "noopener noreferrer";
                          document.body.appendChild(a);
                          a.click();
                          document.body.removeChild(a);
                        } catch {
                          window.open(url, "_blank");
                        }
                      }}
                      title="Opens directly in WhatsApp Web in browser"
                    >
                      🌐 WhatsApp Web
                    </button>
                    {shareModal.receiptUrl && (
                      <a
                        href={shareModal.receiptUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="share-channel-btn btn-view-receipt-live"
                        title="Open and view the live customer receipt in browser"
                      >
                        👁️ View Live Receipt
                      </a>
                    )}
                    <button
                      type="button"
                      className="share-channel-btn btn-sms"
                      disabled={cleanPhone.length !== 10 || hasPressure}
                      onClick={handleShareSms}
                      title="Send receipt via SMS"
                    >
                      ✉️ Send via SMS
                    </button>
                    {typeof navigator !== "undefined" &&
                      typeof navigator.share === "function" && (
                        <button
                          type="button"
                          className="share-channel-btn btn-webshare"
                          disabled={hasPressure || isFetchingBill}
                          onClick={handleShareWeb}
                          title="Shares bill photo and receipt together directly to WhatsApp or native share sheet"
                        >
                          {isFetchingBill
                            ? "⏳ Preparing..."
                            : shareModal.bills && shareModal.bills.length > 0
                            ? "↗️ Share Photo & Receipt"
                            : "↗️ Web Share"}
                        </button>
                      )}
                  </div>

                  <div className="share-disclaimer-box">
                    <small>
                      ℹ️ <strong>WhatsApp Direct Integration:</strong> Clicking &ldquo;Open WhatsApp &amp; Send Receipt&rdquo; opens the customer&apos;s WhatsApp chat with their name, date, udhar amount, and receipt link already filled in. The customer can click the link to see their official receipt saying &ldquo;Your udhar is added on VoiceKhata&rdquo;.
                    </small>
                  </div>
                </div>
              );
            })()}
          </div>
        </div>
      )}

      {/* 3. BILL VIEWER MODAL */}
      {viewingBill && viewingBill.open && (
        <div className="modal-backdrop" onClick={() => setViewingBill(null)}>
          <div
            className="modal-card modal-bill-view"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="modal-header">
              <h3>{viewingBill.title || "Bill Photo"}</h3>
              <button
                type="button"
                className="modal-close-btn"
                onClick={() => setViewingBill(null)}
              >
                ✕
              </button>
            </div>
            <div className="bill-view-body">
              {viewingBill.loading ? (
                <div className="bill-loading-spinner">
                  <span className="spinner-icon">🔄</span>
                  <span>Downloading encrypted bill over WebSocket...</span>
                </div>
              ) : viewingBill.url ? (
                viewingBill.mime.startsWith("image/") ? (
                  <img
                    src={viewingBill.url}
                    alt="Bill attachment"
                    className="bill-preview-image"
                  />
                ) : (
                  <iframe
                    src={viewingBill.url}
                    title="Bill document"
                    className="bill-preview-iframe"
                  />
                )
              ) : (
                <p>Unable to load bill.</p>
              )}
            </div>
            <div className="modal-footer">
              {viewingBill.url && (
                <a
                  href={viewingBill.url}
                  download="bill_attachment"
                  className="modal-btn-download"
                >
                  ⬇ Download
                </a>
              )}
              <button
                type="button"
                className="modal-btn-cancel"
                onClick={() => setViewingBill(null)}
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 4. DELETE TRANSACTION CONFIRMATION MODAL */}
      {deleteModal && (
        <div className="modal-backdrop" onClick={() => setDeleteModal(null)}>
          <div className="modal-card modal-delete" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <div>
                <h3>Delete Transaction / लेनदेन हटाएं</h3>
                <span className="modal-sub">Confirm permanent deletion from database</span>
              </div>
              <button
                type="button"
                className="modal-close-btn"
                onClick={() => setDeleteModal(null)}
              >
                ✕
              </button>
            </div>

            <div className="delete-confirm-box">
              <div className="delete-confirm-row">
                <span className="delete-confirm-label">Customer</span>
                <span className="delete-confirm-val">{deleteModal.customerName}</span>
              </div>
              <div className="delete-confirm-row">
                <span className="delete-confirm-label">Transaction Type</span>
                <span className="delete-confirm-val">
                  {deleteModal.type === "GOT"
                    ? "Payment Received (जमा मिला)"
                    : "Credit Given (उधार दिया)"}
                </span>
              </div>
              <div className="delete-confirm-row">
                <span className="delete-confirm-label">Amount</span>
                <span className="delete-confirm-val">
                  {deleteModal.isPaise
                    ? formatInr(deleteModal.amount)
                    : formatInrRupees(deleteModal.amount)}
                </span>
              </div>
              <div className="delete-warning-text">
                ⚠️ This transaction will be permanently deleted directly from the database and all balances will be updated immediately.
              </div>
            </div>

            <div className="modal-footer delete-modal-actions">
              <button
                type="button"
                className="modal-btn-cancel"
                onClick={() => setDeleteModal(null)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn-confirm-delete"
                onClick={handleConfirmDelete}
              >
                🗑 Yes, Delete from Database
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
