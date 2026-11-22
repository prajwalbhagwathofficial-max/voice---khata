# 🎤 Voice Khata — वॉइस खाता
### Voice-First Udhaar (Credit) Ledger for Kirana Stores

> **Gnani Voice Hackathon 2026** — Problem Statement 2  
> *The shopkeeper speaks mid-sale — the ledger updates without touching the screen.*

[![Tests](https://img.shields.io/badge/tests-194%20passed-brightgreen.svg)](#-test-suite)
[![PWA](https://img.shields.io/badge/PWA-offline--first-blue.svg)](#-offline-first-dual-store-architecture)
[![Gnani Vachana AI](https://img.shields.io/badge/AI-Prisma%20v3%20%7C%20Timbre%20v3-purple.svg)](#-voice-pipeline-architecture)
[![10 Indian Languages](https://img.shields.io/badge/languages-10%20Indic-orange.svg)](#-10-indian-languages--voice-personalities)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## 🌟 Executive Summary

In India, over **12 million neighborhood kirana stores** extend informal daily credit (*udhaar*), but managing paper books leads to uncollected balances, lost notebooks, and awkward collection calls. Existing digital ledger apps failed to scale because typing on mobile screens with wet, busy hands during rush hour is impractical.

**Voice Khata** transforms this experience through a **zero-typing, voice-first ledger**:
- 🎙️ **All-India Multilingual & Mixed Vernacular**: Speak in Hindi, Kannada, Tamil, Telugu, Marathi, Gujarati, Bengali, Malayalam, Punjabi, English, Hinglish, or Kanglish (*"रमेश का 500 रुपये उधार लिखो"* / *"ರಮೇಶ್ 500 ಸಾಲ ಬರೆಯಿರಿ"* / *"ரமேஷுக்கு 500 ரூபாய் கடன் எழுதுங்க"* / *"సురేష్ 300 రూపాయలు ఇచ్చాడు"*).
- 🗣️ **Regional Voice Personalities**: Speaks back in native accents using Gnani Timbre voices (Chetana for Kannada, Gayatri for Tamil, Rohini for Telugu, Kavita for Marathi, Komal for Gujarati, Mita for Bengali, Arya for Malayalam, Simran for Punjabi, Shivani for Hindi/English).
- ⚡ **Instant Deterministic Parsing (< 5ms)**: Local lexicon engine parses numbers and dates deterministically before falling back to Groq LLM for unstructured phrasing.
- 🛑 **Acoustic Barge-in & Zero Overlap**: Speak over the assistant at any moment (*"अरे 300 नहीं 500!"*) — acoustic energy VAD cuts playback with 0ms audible lag, guaranteed single-voice channel prevents multiple AI voices speaking at once.
- 📢 **1-Tap WhatsApp Reminders**: Localized, respectful `wa.me` deep links with strict 3-day anti-spam cooldowns.
- 🛡️ **Offline-First Dual Store**: Works in rural basements without internet via Dexie.js (IndexedDB) with AES-256-GCM encryption, synchronizing to Supabase Postgres when online.

---

## 🗣️ 10 Indian Languages & Voice Personalities

| Language | Code | Gnani Timbre Voice | Sample Voice Command |
|---|---|---|---|
| **Hindi** | `hi` | `Shivani` | *"रमेश का 500 रुपये उधार लिखो"* |
| **Kannada** | `kn` | `Chetana` | *"ಚಿರಾಗ್ ಗೆ 200 ರೂಪಾಯಿ ಸಾಲ ಬರೆಯಿರಿ"* |
| **Tamil** | `ta` | `Gayatri` | *"ரமேஷுக்கு 500 ரூபாய் கடன் எழுதுங்க"* |
| **Telugu** | `te` | `Rohini` | *"సురేష్ 300 రూపాయలు ఇచ్చాడు"* |
| **Marathi** | `mr` | `Kavita` | *"रमेशला 500 रुपये उधारी लिहा"* |
| **Gujarati** | `gu` | `Komal` | *"રમેશને 500 રૂપિયા ઉધાર લખો"* |
| **Bengali** | `bn` | `Mita` | *"রমেশকে 500 টাকা ধার লেখো"* |
| **Malayalam** | `ml` | `Arya` | *"രമേശിന് 500 രൂപ കടം എഴുതുക"* |
| **Punjabi** | `pa` | `Simran` | *"ਰਮੇਸ਼ ਨੂੰ 500 ਰੁਪਏ ਉਧਾਰ ਲਿਖੋ"* |
| **English** | `en` | `Shivani` | *"Add 500 rupees credit for Ramesh"* |

- **Auto-Detection (`indic-auto`)**: The merchant does not need to switch settings; speaking any language automatically detects the tongue and routes to the matching regional voice.
- **Dialect Suffix Stripping**: Cleanly strips Indic and Dravidian case endings (`-du`, `-ge`, `-kku`, `-ki`, `-ku`, `-gari`, `-ka`, `-la`, `-ne`, `-ke`) so `"ಚೆರಾಗ್ದು"` (Chirag's) correctly maps to customer **Chirag**.
- **Code-Switching Support**: Supports mixed Indian-English combinations (*"Please put 500 rupees debit of Ramesh"*, *"Amisha's 500 rupees madam"*).

---

## 🏗️ System Architecture

```mermaid
flowchart TD
    subgraph Client ["Client Layer (Preact PWA + Web Audio)"]
        Mic["🎙️ Microphone (16kHz PCM)"]
        AudioMgr["🔊 Audio Manager & RMS Energy"]
        VAD["🛑 Barge-In Controller (VAD)"]
        Dexie["💾 IndexedDB (Dexie v4)<br/>AES-256-GCM Encrypted"]
        Outbox["📤 Outbox Queue"]
        UI["📱 UI: Customer Ledger & Metrics"]
    end

    subgraph Server ["Fastify Proxy & Orchestration Layer"]
        STTProxy["🔄 STT WebSocket Proxy<br/>/api/voice/stt"]
        TTSProxy["🔊 TTS WebSocket Proxy<br/>/api/voice/tts"]
        LLM["🧠 Groq Llama 3.3 70B<br/>/api/voice/extract"]
        SyncRoute["☁️ Cloud Sync Engine<br/>/api/sync/push & pull"]
        LatencyLog["📊 Latency & TTFA Logger"]
    end

    subgraph Cloud ["External Cloud Services"]
        GnaniSTT["☁️ Gnani Prisma v3.0 (STT)<br/>wss://api.vachana.ai/stt/v3/stream"]
        GnaniTTS["☁️ Gnani Timbre v3.0 (TTS)<br/>wss://api.vachana.ai/api/v1/tts"]
        Supabase["🐘 Supabase Postgres + RLS<br/>Cloud Ledger Backup"]
    end

    Mic --> AudioMgr
    AudioMgr -->|Live PCM Chunks| STTProxy
    AudioMgr -->|RMS Energy| VAD
    STTProxy -->|Auth Injected| GnaniSTT
    GnaniSTT -->|Transcripts| STTProxy
    STTProxy -->|Real-time JSON| AudioMgr
    AudioMgr --> UI

    UI -->|Deterministic Number & Intent| Dexie
    UI -.->|Unstructured Intent Fallback| LLM
    Dexie -->|Atomic Writes| Outbox

    TTSProxy -->|Auth Injected| GnaniTTS
    GnaniTTS -->|Base64 Audio Chunks| TTSProxy
    TTSProxy -->|Audio Playback Stream| AudioMgr
    TTSProxy --> LatencyLog

    Outbox -->|Background Sync| SyncRoute
    SyncRoute -->|Service Role Client| Supabase
```

---

## 🔄 Voice Pipeline Architecture

The Voice Khata pipeline operates on a **dual-path routing model** to achieve instant responsiveness and low operating cost:

```
                          ┌───────────────────────────┐
                          │   Spoken Audio Input      │
                          └─────────────┬─────────────┘
                                        ▼
                          ┌───────────────────────────┐
                          │  Gnani Prisma v3.0 (STT)  │
                          └─────────────┬─────────────┘
                                        ▼
                                  Transcript
                                        │
                 ┌──────────────────────┴──────────────────────┐
                 ▼                                             ▼
       [ Fast Local Lexicon ]                        [ Groq Llama 3.3 70B ]
      Deterministic Rule Match                      Complex / Colloquial Intent
             ( < 5ms )                                      ( ~350ms )
                 │                                             │
                 └──────────────────────┬──────────────────────┘
                                        ▼
                          ┌───────────────────────────┐
                          │     Business Rules        │
                          │ • Derived Overdue Counter │
                          │ • High-Due Limit Shield   │
                          │ • Geofence Distance Check │
                          └─────────────┬─────────────┘
                                        ▼
                          ┌───────────────────────────┐
                          │   Encrypted IndexedDB     │
                          └─────────────┬─────────────┘
                                        ▼
                          ┌───────────────────────────┐
                          │   Gnani Timbre v3.0 (TTS) │
                          │    Shivani Voice (< 2s)   │
                          └───────────────────────────┘
```

1. **Fast Local Path (< 5ms)**:
   - Evaluates spoken transcripts against compiled multi-lingual lexicons (`hi.json`, `kn.json`, `en.json`).
   - Words like *"paanch sau"*, *"aidu nooru"*, or *"1,500"* are extracted without round-trip latency.
2. **Deep Semantic Path (Groq Llama 3.3 70B)**:
   - For colloquial store conversations (*"Sharma ji jo kal aaye the unka teen packet doodh ka likh do"*), requests route to `/api/voice/extract` returning strict JSON entities.
3. **Turn-1 Mandatory AI Disclosure**:
   - Transparently announces: *"नमस्ते! मैं {दुकान} का AI सहायक हूँ..."* on Turn 1; stays silent on subsequent turns.
4. **Acoustic Barge-in**:
   - Real-time RMS audio energy monitoring interrupts ongoing TTS playback instantly when the merchant speaks.

---

## 💾 Offline-First Dual-Store Architecture

```
Client (Browser)                                    Server & Cloud
────────────────                                    ──────────────
[ User Action ]
      │
      ▼
┌──────────────┐
│ Dexie.js v4  │──(Atomic Dexie Transaction)
│  customers   │
│ transactions │
└──────────────┘
      │
      ▼
┌──────────────┐                                ┌──────────────────┐
│ Outbox Queue │──(Background Worker HTTP)────▶ │ Fastify Server   │
│   (Dexie)    │                                │  /api/sync/push  │
└──────────────┘                                └─────────┬────────┘
                                                          │
                                                          ▼
                                                ┌──────────────────┐
                                                │ Supabase Cloud   │
                                                │  Postgres + RLS  │
                                                └──────────────────┘
```

- **Primary Source of Truth**: On-device IndexedDB database (`voice_khata`).
- **Encrypted Fields**: Customer phone number and street address are encrypted with PBKDF2 (100,000 rounds) + AES-256-GCM.
- **Outbox Pattern**: Every local write atomically queues an outbox entry. A background sync worker flushes entries to Supabase with exponential backoff.
- **Derived Counters**: `totalDue` and `dueSince` are derived from ledger history. Partial payments restart the overdue counter; new credit preserves the original due date.

---

## 📂 Project Structure

```
voice-khata/
├── client/                     # Preact PWA Frontend
│   ├── src/
│   │   ├── components/         # UI: MicButton, VoiceCard, Ledger, Reminders, Confetti
│   │   ├── crypto/             # AES-256-GCM + PBKDF2 WebCrypto implementation
│   │   ├── db/                 # Dexie v4 schemas and repositories
│   │   ├── logic/              # Business rules: High due, Distance, Reminders, WhatsApp
│   │   ├── sync/               # Outbox sync engine and reactive status signals
│   │   ├── utils/              # Template interpolation & Unicode customer ID generator
│   │   ├── voice/              # Audio manager, barge-in, number & date parsers
│   │   ├── app.jsx             # Main interactive application root
│   │   └── main.jsx            # PWA entrypoint & Service Worker registration
│   ├── vite.config.js          # Vite + PWA manifest config
│   └── package.json
├── server/                     # Node.js + Fastify Backend
│   ├── config/                 # Settings & multi-lingual lexicon loader
│   ├── llm/                    # Groq Llama 3.3 JSON intent extractor
│   ├── reminders/              # Server-side reminder check endpoint
│   ├── routes/                 # Sync push/pull/delete-all routes
│   ├── supabase/               # Authenticated Supabase service client
│   ├── voice/                  # WebSocket proxies (Gnani STT / TTS) & Latency Logger
│   └── index.js                # Server entrypoint
├── config/
│   ├── settings.json           # Default store settings & thresholds
│   ├── templates/              # hi.json, kn.json, en.json reminder & speech templates
│   └── lexicon/                # hi.json, kn.json, en.json word & number maps
├── migrations/
│   └── 001_initial_schema.sql  # Supabase Postgres schema with RLS policies
├── tests/                      # Automated test suite (179 passing tests)
│   ├── businessLogic.test.js   # Credit, payment, and ledger workflows
│   ├── crypto.test.js          # PBKDF2 + AES-GCM verification
│   ├── customerIdGenerator.test.js # Unicode name sanitization
│   ├── noisyUtterances.test.js # Dialects, fillers, and code-switching tests
│   ├── reminders.test.js       # Cooldowns, opt-out, and wa.me links
│   ├── repos.test.js           # IndexedDB repository CRUD & running balance
│   ├── voicePipeline.test.js   # Lexicon parser, dates, barge-in, disclosure
│   └── voiceProxy.test.js      # WebSocket proxy & TTFA latency metrics
├── docs/
│   ├── DEMO_SCRIPT.md          # 3-minute hackathon demo script
│   └── PITCH_DECK.md           # 10-slide investor & hackathon pitch deck
├── .env.example
├── package.json
└── README.md
```

---

## 🧪 Test Suite

Voice Khata is verified with **194 automated tests across 43 test suites** with 100% pass rate:

```bash
node --test tests/*.test.js
```

```
▶ Template Engine ...................................  5 pass
▶ WhatsApp Sender & wa.me deep-links ................  4 pass
▶ Distance Checker (Haversine Formula) ..............  3 pass
▶ High Due Checks & Threshold Enforcement ...........  4 pass
▶ Business Workflows (Credit, Payment, Query) .......  6 pass
▶ Encryption Service (PBKDF2 + AES-256-GCM) ......... 10 pass
▶ Customer ID Generator (Unicode Devanagari/Kannada)  12 pass
▶ Repos (Customer, Txn, Settings, Outbox, Log) ...... 17 pass
▶ Number Parser (Hindi, Kannada, English, Digits) ... 17 pass
▶ Date Parser (Relative & Explicit) .................  9 pass
▶ Correction Handler (Negation, Amounts, Inline Fix)   9 pass
▶ Lexicon Intent Detection ..........................  7 pass
▶ Disclosure Manager (First turn requirement) .......  5 pass
▶ Barge-In Controller (Energy threshold VAD) ........  6 pass
▶ Intent Extractor Module (Groq LLM) ................  3 pass
▶ Voice Pipeline Orchestrator (Full turn handling) ..  9 pass
▶ Latency Logger & Metrics (p50/p95, 2s target) .....  9 pass
▶ STT / TTS Proxies & Target Tracking ...............  5 pass
▶ Reminder Scheduler Rules & Anti-Spam Cooldown .....  7 pass
▶ Multi-lingual Reminder Templates (hi, kn, en) .....  3 pass
▶ Reminder Audit Logging (reminder_log table) .......  2 pass
▶ Noisy Utterances & Real-World Kirana Audio ........ 18 pass
▶ Multilingual E2E (All 10 Indian Languages & Voices) 15 pass
─────────────────────────────────────────────────────────────
  Total: 194 tests, 43 suites, 0 failures, ~460ms
```

---

## 🚀 Quick Start Guide

### 1. Prerequisites
- Node.js v18+ (tested on Node v20/v24)
- npm v9+

### 2. Installation
```powershell
# Clone the repository
git clone <repo-url>
cd voice-khata

# Install server dependencies
npm install

# Install client dependencies
cd client
npm install
cd ..
```

### 3. Environment Setup
Create a `.env` file in the root directory:
```env
PORT=3000
NODE_ENV=development

# Gnani Vachana AI (STT: Prisma v3.0, TTS: Timbre v3.0)
GNANI_API_KEY=your_gnani_api_key_here
GNANI_STT_URL=wss://api.vachana.ai/stt/v3/stream
GNANI_TTS_URL=wss://api.vachana.ai/api/v1/tts

# Groq LLM (Llama 3.3 70B)
GROQ_API_KEY=your_groq_api_key_here
GROQ_MODEL=llama-3.3-70b-versatile

# Supabase Cloud Database (Optional for cloud sync)
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=your_anon_key
SUPABASE_SERVICE_ROLE_KEY=your_service_role_key
```

### 4. Running Locally
```powershell
# Run backend server (Port 3000) and frontend Vite dev server (Port 5173) concurrently:
npm run dev
```

Open `http://localhost:5173` in your browser.

---

## 📱 User Interface Walkthrough

1. **Dashboard**: Live credit metrics (Total Outstanding, Customers, 7+ Day Overdue).
2. **Push-To-Talk Mic**: Tap the floating mic button to record any transaction.
3. **Voice Interaction Overlay**: Displays live transcript, intent badge, extracted entity chips, and assistant response.
4. **Customer Ledger**: View full running balance timeline, credit (+) in red, payment (-) in green, and voice transcript notes.
5. **WhatsApp Reminders**: 1-tap `wa.me` deep link dispatch with polite Hindi, Kannada, or English templates.
6. **Settings & Custom API Keys**: Allows store owners to add personal Gnani/Groq keys when shared quotas run out.
7. **Full Payment Celebration**: 60fps confetti animation when debt is completely settled.

---

## 🔒 Security & Data Privacy

- **Zero Client Credential Exposure**: Gnani and Groq API keys remain strictly on the Fastify server.
- **Client-Side Field Encryption**: Sensitive merchant fields are encrypted with PBKDF2 key derivation and AES-256-GCM.
- **Right to be Forgotten**: The *"सभी डेटा मिटाएं (Delete All Data)"* action executes a complete cryptographic wipe on local IndexedDB and triggers remote Supabase purge.
- **Row-Level Security (RLS)**: Enforced on all Supabase tables.

---

## 📋 Hackathon Phase Completion Status

- [x] **Phase 0**: Requirements, architecture, folder structure
- [x] **Phase 1**: Scaffold, configuration loader, templates, settings
- [x] **Phase 2**: Dexie.js schema, repos, PBKDF2/AES-GCM encryption, customer ID generator
- [x] **Phase 3**: Supabase cloud adapter, outbox sync engine, reactive sync badge
- [x] **Phase 4**: Business rules engine, reminder scheduler, distance checker, high due shield
- [x] **Phase 5**: Fastify WebSocket proxy for Gnani STT & TTS, TTFA latency logger (<2s target)
- [x] **Phase 6**: Multi-lingual number parser, date parser, correction handler, barge-in, Groq LLM
- [x] **Phase 7**: UI layer (Mic button, Voice card, Customer list, Ledger timeline, Confetti)
- [x] **Phase 8**: WhatsApp reminder review modal, wa.me deep links, anti-spam cooldowns
- [x] **Phase 9**: Noisy-utterance test suite, README, 3-minute demo script, pitch deck outline
- [x] **Phase 10**: Production PWA offline hardening, Workbox 30-day template & lexicon caching
- [x] **Phase 11**: All-India 10-language voice verification, cross-script name matching, 194 automated tests
- [x] **Phase 12**: Single-voice guaranteed concurrency engine, latency benchmarks, hackathon documentation package

---

## 📄 Documentation Links

- [3-Minute Live Demo Script](docs/DEMO_SCRIPT.md)
- [Pitch Deck Outline](docs/PITCH_DECK.md)
- [Phase Summaries](file:///C:/Users/pn466/.gemini/antigravity/brain/12c63d21-dc4a-4ca8-92cc-2349abac890d/)

---

*Built with ❤️ for Indian Kirana Store Owners — Gnani Voice Hackathon 2026.*
