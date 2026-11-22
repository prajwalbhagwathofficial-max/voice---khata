# 📊 Voice Khata — Pitch Deck Outline

**Subtitle**: The Voice-First Udhaar Ledger for 12 Million Indian Kirana Stores  
**Hackathon Track**: Gnani Voice Hackathon 2026 — Problem Statement 2  
**Format**: 10-Slide Deck Structure

---

## Slide 1: Title & Vision
- **Header**: **Voice Khata (वॉइस खाता)**
- **Tagline**: Speak your ledger. Zero typing. 100% offline-ready.
- **Visual**: Side-by-side comparison of an ink-stained, dog-eared red paper bahi-khata vs. a smartphone running Voice Khata with pulsating voice wave.
- **Presenter Hook**: *"If UPI digitized payments, why are kirana credit books still stuck in 1950?"*

---

## Slide 2: The ₹3,000 Crore Kirana Pain Point
- **The Numbers**:
  - 12+ Million small neighborhood kirana stores in India.
  - 65% of daily transactions involve informal credit (*udhaar*).
  - ₹3,000+ Crore in outstanding credit floating in unorganized ledgers daily.
- **The Ground Reality**:
  - Store owners have wet, dusty hands; customers arrive in surges; typing on 6-inch screens is physically inconvenient.
  - Up to 15% of annual credit is lost due to forgotten entries, illegible handwriting, or lost notebooks.
  - Awkward collection calls ruin valuable customer relationships.

---

## Slide 3: Why Existing Khata Apps Failed to Scale
- **Friction 1: Keyboard First**: Designed by tech teams who assume everyone loves tapping forms. Typing names in English or finding matras on regional keyboards during rush hour takes 45+ seconds per entry.
- **Friction 2: Cloud Dependency**: Rural and suburban store basements have intermittent 4G/5G. Traditional apps freeze when disconnected.
- **Friction 3: Inflexible & Impolite**: Spammy SMS reminders with aggressive language damage neighborhood trust.

---

## Slide 4: The Voice Khata Solution
- **Speak naturally**: *"रमेश का 500 रुपये उधार लिख दो"* $\rightarrow$ Saved in < 500ms.
- **Hybrid Parsing Engine**: Local deterministic lexicon for < 5ms instant number/date parsing + Groq Llama 3.3 for conversational nuance.
- **Acoustic Barge-in**: Interrupt the assistant mid-sentence to fix mistakes (*"अरे 300 नहीं 500!"*).
- **Polite Localized WhatsApp Reminders**: 1-tap `wa.me` deep links with prefilled, respectful Hindi/Kannada templates.
- **100% Local-First & Encrypted**: Full offline capability via Dexie.js + PBKDF2/AES-256-GCM encryption.

---

## Slide 5: Voice Architecture Powered by Gnani Vachana AI
- **Acoustic Frontend**:
  - Browser Web Audio API captures 16kHz 16-bit linear PCM audio.
  - Fastify WebSocket proxy streams audio to **Gnani Prisma v3.0** (`indic-auto` language detection).
  - Secure server-side credential isolation — API keys never touch the client.
- **Speech Synthesis**:
  - Response generated via **Gnani Timbre v3.0** (`Shivani` voice) with target < 2s Time-to-First-Audio.
- **Dialog Intelligence**:
  - Turn 1 AI disclosure mandated.
  - Dual-path routing: Fast Lexicon path (< 5ms) for standard phrases, Groq Llama 3.3 for unstructured colloquial requests.

---

## Slide 6: Business Safety Rules & Kirana Safeguards
- **1. Derived Overdue Counter**: `now - dueSince` recalculated mathematically from transaction history (never a brittle incrementing variable). Partial payment resets the counter; new credit preserves original due date.
- **2. High-Due Shield**: Automatic voice + touch confirmation modal before recording transactions that push balance over ₹10,000.
- **3. Geofence Distance Warning**: Haversine formula warns if recording occurs > 5 km outside store coordinates.
- **4. Anti-Spam Cooldown**: 7-day initial overdue threshold, strictly enforces 3-day quiet period between reminders, and respects customer opt-out.

---

## Slide 7: Technical Excellence & Architecture
- **Offline-First Dual-Store Architecture**:
  - Primary store: IndexedDB (Dexie v4) on device.
  - Cloud backup: Supabase Postgres with Outbox synchronization pattern.
- **Enterprise Grade Security**:
  - Sensitive customer fields (Phone, Address) encrypted at rest with AES-256-GCM.
  - One-click "Delete All Data" nuclear cryptographic wipe for merchant privacy.
- **Performance Benchmarks**:
  - 179 Automated Tests (100% PASS in < 500ms).
  - Client bundle: 199 kB PWA (64 kB gzip) with instant service-worker precaching.

---

## Slide 8: Market Opportunity & Unit Economics
- **TAM**: 12+ Million Kirana and MSME Retailers in India.
- **SAM**: 4.5 Million smartphone-equipped merchants in Tier-2/3/4 towns.
- **Monetization Paths**:
  - Freemium SaaS: Core voice ledger free forever (drives retention).
  - Premium Tier (₹99/month): Automated WhatsApp Cloud API scheduled dispatch, multi-device staff accounts, and GST voice invoice exports.
  - Micro-Credit & Working Capital Partnerships: Pre-approved merchant credit based on reliable, audited transaction ledger velocity.

---

## Slide 9: Product Roadmap
- **Phase 1 (Hackathon PS2 - Completed)**: Full voice ledger, Kannada/Hindi lexicons, barge-in, wa.me reminders, PWA.
- **Phase 2 (Q2 2026)**: Voice-to-Bill camera integration (snap receipt or spoken product list: *"दो पैकेट आटा, एक किलो चीनी"*).
- **Phase 3 (Q3 2026)**: Smart Soundbox integration — voice confirmation broadcasted on store hardware.
- **Phase 4 (Q4 2026)**: Vernacular Credit Scoring engine for NBFC merchant loans.

---

## Slide 10: Summary & Conclusion
- **Voice Khata transforms everyday kirana store operations**:
  - Saves 45 minutes of manual bookkeeping every evening.
  - Recovers 15-20% of forgotten udhaar balances with zero awkwardness.
  - Accessible to every shopkeeper, regardless of English literacy or typing speed.
- **Contact & Links**:
  - GitHub Repository & Live Demo
  - Powered by Gnani Vachana AI & Groq Llama 3.3
- **Closing**: *"When shopkeepers speak, business flows. That is Voice Khata."*
