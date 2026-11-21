/**
 * Voice Khata laptop browser client.
 * Streams 16kHz 16-bit LE mono PCM over WebSocket (/ws/session) and plays
 * Gnani Timbre v3.0 PCM chunks in real time with cooperative barge-in.
 */

(() => {
  const el = {
    langSelect: document.getElementById("language-select"),
    connPill: document.getElementById("connection-pill"),
    statePill: document.getElementById("state-pill"),
    btnConnect: document.getElementById("btn-connect"),
    btnMic: document.getElementById("btn-mic"),
    btnBargeIn: document.getElementById("btn-barge-in"),
    micLevelBar: document.getElementById("mic-level-bar"),
    errorBanner: document.getElementById("error-banner"),
    pendingCard: document.getElementById("pending-card"),
    pendingIntent: document.getElementById("pending-intent"),
    pendingSummary: document.getElementById("pending-summary"),
    btnConfirm: document.getElementById("btn-confirm"),
    btnCancel: document.getElementById("btn-cancel"),
    liveTranscript: document.getElementById("live-transcript"),
    conversationLog: document.getElementById("conversation-log"),
    latencyMetrics: document.getElementById("latency-metrics"),
    statTotal: document.getElementById("stat-total"),
    statCustomers: document.getElementById("stat-customers"),
    statActive: document.getElementById("stat-active"),
    statEvents: document.getElementById("stat-events"),
    balancesTbody: document.getElementById("balances-tbody"),
    eventsTbody: document.getElementById("events-tbody"),
  };

  let ws = null;
  let audioCtx = null;
  let micStream = null;
  let workletNode = null;
  let isRecording = false;
  let activeTtsTurnId = null;
  let nextPlayTime = 0;
  let activeSources = [];

  function formatRupees(value) {
    const num = Number.parseInt(value, 10) || 0;
    const sign = num < 0 ? "-" : "";
    return `${sign}₹${Math.abs(num).toLocaleString("en-IN")}`;
  }

  function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text == null ? "" : String(text);
    return div.innerHTML;
  }

  function showError(msg) {
    if (!msg) {
      el.errorBanner.classList.add("hidden");
      el.errorBanner.textContent = "";
      return;
    }
    el.errorBanner.textContent = msg;
    el.errorBanner.classList.remove("hidden");
  }

  function setConnectionStatus(connected) {
    el.connPill.textContent = connected ? "Connected (WS)" : "Disconnected";
    el.connPill.className = `pill ${connected ? "pill-connected" : "pill-disconnected"}`;
    el.btnConnect.textContent = connected ? "End Session" : "Start Voice Session";
    el.btnMic.disabled = !connected;
    el.btnBargeIn.disabled = !connected;
  }

  function setAgentState(state) {
    el.statePill.textContent = state;
    const classMap = {
      IDLE: "pill-idle",
      LISTENING: "pill-listening",
      PROCESSING: "pill-processing",
      SPEAKING: "pill-speaking",
      AWAITING_CONFIRMATION: "pill-confirm",
    };
    el.statePill.className = `pill ${classMap[state] || "pill-idle"}`;
  }

  function appendChat(role, text) {
    const item = document.createElement("div");
    item.className = `chat-item ${role === "user" ? "chat-user" : "chat-agent"}`;
    item.innerHTML = `<span class="chat-role">${role === "user" ? "Shopkeeper" : "Voice Khata AI"}</span>${escapeHtml(text)}`;
    el.conversationLog.appendChild(item);
    el.conversationLog.scrollTop = el.conversationLog.scrollHeight;
  }

  function renderPending(pending) {
    if (!pending) {
      el.pendingCard.classList.add("hidden");
      return;
    }
    el.pendingCard.classList.remove("hidden");
    el.pendingIntent.textContent = pending.intent;
    const amt = pending.amount_rupees != null ? formatRupees(pending.amount_rupees) : "";
    const cust = pending.customer_name || "";
    const note = pending.item_note ? ` (${pending.item_note})` : "";
    el.pendingSummary.textContent = `${pending.intent.replace("_", " ")}: ${cust} · ${amt}${note}`;
  }

  function renderLedgerSnapshot(msg) {
    const s = msg.summary || {};
    el.statTotal.textContent = formatRupees(s.total_outstanding_rupees || 0);
    el.statCustomers.textContent = String(s.customer_count || 0);
    el.statActive.textContent = String(s.active_credit_customers || 0);
    el.statEvents.textContent = String(s.total_events || 0);

    const balances = msg.balances || [];
    if (balances.length === 0) {
      el.balancesTbody.innerHTML = `<tr><td colspan="3" class="empty-cell">No customers recorded yet.</td></tr>`;
    } else {
      el.balancesTbody.innerHTML = balances
        .map((b) => {
          const cls = b.balance_rupees > 0 ? "amt-pos" : b.balance_rupees < 0 ? "amt-neg" : "";
          return `
            <tr>
              <td>${escapeHtml(b.customer_name)}</td>
              <td class="num">${b.event_count}</td>
              <td class="num ${cls}">${formatRupees(b.balance_rupees)}</td>
            </tr>
          `;
        })
        .join("");
    }

    const events = msg.recent_events || [];
    if (events.length === 0) {
      el.eventsTbody.innerHTML = `<tr><td colspan="6" class="empty-cell">No ledger events yet.</td></tr>`;
    } else {
      el.eventsTbody.innerHTML = events
        .map((ev) => {
          const tagClass =
            ev.event_type === "CREDIT_GIVEN"
              ? "tag-credit"
              : ev.event_type === "PAYMENT_RECEIVED"
              ? "tag-payment"
              : "tag-reversal";
          const amtCls = ev.signed_amount_rupees > 0 ? "amt-pos" : "amt-neg";
          const signPrefix = ev.signed_amount_rupees > 0 ? "+" : "";
          const timeShort = (ev.created_at_utc || "").replace("T", " ").replace("+00:00", "Z");
          return `
            <tr>
              <td>#${ev.id}</td>
              <td>${escapeHtml(ev.customer_name)}</td>
              <td><span class="tag ${tagClass}">${escapeHtml(ev.event_type)}</span></td>
              <td class="num ${amtCls}">${signPrefix}${formatRupees(ev.signed_amount_rupees)}</td>
              <td>${escapeHtml(ev.item_note || "")}</td>
              <td>${escapeHtml(timeShort)}</td>
            </tr>
          `;
        })
        .join("");
    }
  }

  function renderLatency(msg) {
    const d = msg.durations_ms || {};
    const fmt = (v) => (v != null ? `${v} ms` : "—");
    el.latencyMetrics.innerHTML = `
      <span>STT: ${fmt(d.stt_ms)}</span>
      <span>NLU: ${fmt(d.nlu_ms)}</span>
      <span>DB: ${fmt(d.db_ms)}</span>
      <span>TTS TTFB: ${fmt(d.tts_ttfb_ms)}</span>
      <span>E2E 1st Audio: ${fmt(d.e2e_first_audio_ms)}</span>
    `;
  }

  async function ensureAudioContext() {
    if (!audioCtx) {
      audioCtx = new AudioContext({ sampleRate: 48000 });
    }
    if (audioCtx.state === "suspended") {
      await audioCtx.resume();
    }
  }

  function stopPlayback() {
    activeTtsTurnId = null;
    for (const src of activeSources) {
      try {
        src.stop();
      } catch (_) {}
    }
    activeSources = [];
    if (audioCtx) {
      nextPlayTime = audioCtx.currentTime;
    }
  }

  function playPcmChunk(turnId, pcmB64, sampleRate) {
    if (!audioCtx || !pcmB64) return;
    if (activeTtsTurnId !== turnId) {
      activeTtsTurnId = turnId;
      nextPlayTime = Math.max(audioCtx.currentTime + 0.02, nextPlayTime);
    }

    const binStr = atob(pcmB64);
    const len = binStr.length;
    const bytes = new Uint8Array(len);
    for (let i = 0; i < len; i++) {
      bytes[i] = binStr.charCodeAt(i);
    }

    const int16 = new Int16Array(bytes.buffer, 0, Math.floor(bytes.byteLength / 2));
    if (int16.length === 0) return;

    const float32 = new Float32Array(int16.length);
    for (let i = 0; i < int16.length; i++) {
      float32[i] = int16[i] / 32768;
    }

    const buffer = audioCtx.createBuffer(1, float32.length, sampleRate || 48000);
    buffer.copyToChannel(float32, 0);

    const src = audioCtx.createBufferSource();
    src.buffer = buffer;
    src.connect(audioCtx.destination);

    const startAt = Math.max(audioCtx.currentTime + 0.01, nextPlayTime);
    src.start(startAt);
    nextPlayTime = startAt + buffer.duration;
    activeSources.push(src);
    src.onended = () => {
      activeSources = activeSources.filter((s) => s !== src);
    };
  }

  async function startMicCapture() {
    if (!ws || ws.readyState !== WebSocket.OPEN || isRecording) return;
    showError("");
    await ensureAudioContext();
    stopPlayback();

    if (!micStream) {
      micStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      await audioCtx.audioWorklet.addModule("/static/pcm-worklet.js");
      const source = audioCtx.createMediaStreamSource(micStream);
      workletNode = new AudioWorkletNode(audioCtx, "pcm-16k-processor");
      workletNode.port.onmessage = (event) => {
        const { pcm, rms } = event.data;
        const pct = Math.min(100, Math.round(rms * 320));
        el.micLevelBar.style.width = `${isRecording ? pct : 0}%`;
        if (isRecording && ws && ws.readyState === WebSocket.OPEN) {
          ws.send(pcm);
        }
      };
      source.connect(workletNode);
    }

    isRecording = true;
    el.btnMic.classList.add("recording");
    el.btnMic.textContent = "Listening… Click to Stop";
    el.liveTranscript.textContent = "Listening…";
    ws.send(JSON.stringify({ type: "audio_start" }));
  }

  function stopMicCapture() {
    if (!isRecording) return;
    isRecording = false;
    el.btnMic.classList.remove("recording");
    el.btnMic.textContent = "Hold or Click to Speak";
    el.micLevelBar.style.width = "0%";
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "audio_end" }));
    }
  }

  async function connectSession() {
    if (ws) {
      disconnectSession();
      return;
    }

    showError("");
    await ensureAudioContext();

    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const lang = el.langSelect.value;
    const url = `${proto}//${window.location.host}/ws/session?lang=${encodeURIComponent(lang)}`;
    ws = new WebSocket(url);

    ws.onopen = () => {
      setConnectionStatus(true);
      ws.send(JSON.stringify({ type: "session_start", language_code: lang }));
    };

    ws.onmessage = (event) => {
      let msg;
      try {
        msg = JSON.parse(event.data);
      } catch (_) {
        return;
      }

      switch (msg.type) {
        case "state":
          setAgentState(msg.state);
          renderPending(msg.pending_action);
          break;
        case "ledger_snapshot":
          renderLedgerSnapshot(msg);
          break;
        case "stt_partial":
          el.liveTranscript.textContent = msg.transcript;
          break;
        case "stt_final":
          el.liveTranscript.textContent = msg.transcript;
          if (msg.transcript) {
            appendChat("user", msg.transcript);
          }
          break;
        case "agent_text":
          appendChat("agent", msg.text);
          break;
        case "tts_chunk":
          playPcmChunk(msg.turn_id, msg.pcm_b64, msg.sample_rate);
          break;
        case "tts_stop":
          stopPlayback();
          break;
        case "latency":
          renderLatency(msg);
          break;
        case "error":
          showError(msg.message);
          break;
        default:
          break;
      }
    };

    ws.onclose = () => {
      stopMicCapture();
      stopPlayback();
      ws = null;
      setConnectionStatus(false);
      setAgentState("IDLE");
    };

    ws.onerror = () => {
      showError("WebSocket connection error.");
    };
  }

  function disconnectSession() {
    stopMicCapture();
    stopPlayback();
    if (ws) {
      ws.close();
      ws = null;
    }
    if (micStream) {
      micStream.getTracks().forEach((t) => t.stop());
      micStream = null;
      workletNode = null;
    }
    setConnectionStatus(false);
    setAgentState("IDLE");
  }

  el.btnConnect.addEventListener("click", () => {
    connectSession().catch((err) => showError(err.message));
  });

  el.btnMic.addEventListener("click", () => {
    if (isRecording) {
      stopMicCapture();
    } else {
      startMicCapture().catch((err) => showError(`Microphone error: ${err.message}`));
    }
  });

  el.btnBargeIn.addEventListener("click", () => {
    stopPlayback();
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "barge_in" }));
    }
  });

  el.btnConfirm.addEventListener("click", () => {
    stopPlayback();
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "confirm_pending" }));
    }
  });

  el.btnCancel.addEventListener("click", () => {
    stopPlayback();
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "cancel_pending" }));
    }
  });

  el.langSelect.addEventListener("change", () => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "set_language", language_code: el.langSelect.value }));
    }
  });

  // Push-to-Talk via Space bar on laptop keyboard
  window.addEventListener("keydown", (e) => {
    if (e.code === "Space" && !e.repeat && document.activeElement?.tagName !== "SELECT") {
      if (ws && ws.readyState === WebSocket.OPEN && !isRecording) {
        e.preventDefault();
        startMicCapture().catch((err) => showError(`Microphone error: ${err.message}`));
      }
    }
  });

  window.addEventListener("keyup", (e) => {
    if (e.code === "Space" && isRecording) {
      e.preventDefault();
      stopMicCapture();
    }
  });
})();
