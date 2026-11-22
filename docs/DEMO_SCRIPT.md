# 🎤 Voice Khata — 3-Minute Live Hackathon Demo Script

**Project**: Voice Khata (Problem Statement 2: Voice-First Udhaar Ledger for Kirana Stores)  
**Target Duration**: 3 Minutes (180 Seconds)  
**Presenter**: Solo or Team (Merchant role + Presenter role)

---

## ⏱️ Timeline Overview

| Time | Segment | Live Action / Dialogue | Key Technology Highlighted |
|---|---|---|---|
| **0:00 - 0:30** | The Kirana Reality | Paper khata vs. typing friction | Local reality, 12M store owners |
| **0:30 - 1:15** | Voice Add-Credit | *"रमेश का 500 रुपये उधार लिखो"* | Gnani Prisma v3.0 + Deterministic Lexicon (<5ms) |
| **1:15 - 1:45** | Inline Correction & Barge-in | *"अरे नहीं, 300 नहीं 500 था"* + interrupt | Acoustic Barge-in (VAD) + Intent Extractor |
| **1:45 - 2:15** | Full Payment & Celebration | *"रमेश ने पूरा बकाया चुका दिया"* | Dexie Transaction + Canvas Confetti |
| **2:15 - 2:45** | 1-Tap WhatsApp Reminder | Open reminder queue & click send | wa.me deep-link + Anti-spam Cooldown |
| **2:45 - 3:00** | Offline Sync & Privacy | Toggle Airplane mode + Delete All Data | Outbox Pattern + PBKDF2 / AES-256-GCM |

---

## 🎭 Step-by-Step Script

### [0:00 - 0:30] Introduction: The Kirana Store Friction

*(Presenter holds up a classic red paper bahi-khata or dirty notepad)*

> **Presenter**:  
> "Good morning, judges. In India, over 12 million kirana store owners manage ₹3,000+ crore in daily informal credit (*udhaar*). But while billing and UPI went digital, **their credit books are still trapped in physical paper**.  
>  
> Why? Because typing on a small keyboard with dusty, wet fingers in the middle of a rush is impossible. Existing apps asked shopkeepers to behave like accountants.  
>  
> Introducing **Voice Khata** — a zero-typing, voice-first kirana ledger that lets store owners record credit as easily as talking to their customers, powered by **Gnani Vachana AI**."

---

### [0:30 - 1:15] Demo 1: Hands-Free Voice Add-Credit (Code-Switching)

*(Presenter taps the big blue floating microphone button. The button expands with a pulsating red acoustic energy wave)*

> **Presenter speaks naturally**:  
> 🗣️ *"अरे भाई, रमेश का पांच सौ रुपये का उधार लिख दो"*

*(Screen shows):*
- Real-time transcript appears in `VoiceCard`: *"अरे भाई, रमेश का पांच सौ रुपये का उधार लिख दो"*
- Intent Badge pops up: `➕ उधार (Credit)`
- Extracted chip: `👤 Ramesh` | `₹ 500`
- AI Assistant voice speaks via Gnani Timbre v3.0:  
  🔊 *"नमस्ते! मैं शर्मा किराना का AI सहायक हूँ। रमेश जी, ₹500 उधार जोड़ा गया। कुल बकाया ₹500।"*
- First-turn AI disclosure is fulfilled smoothly.

> **Presenter**:  
> "Notice two things:  
> 1. **Zero typing**: The app isolated the customer name and parsed the spoken Hindi words *"पांच सौ"* deterministically in **under 5 milliseconds** using our local lexicon engine.  
> 2. **AI Disclosure**: Transparently announced itself on the first turn, as mandated by conversational AI guidelines."

---

### [1:15 - 1:45] Demo 2: Inline Correction & Acoustic Barge-In

*(Presenter taps mic again)*

> **Presenter speaks**:  
> 🗣️ *"रमेश का दो सौ रुपये और उधार जोड़ो"*

*(Assistant starts speaking back)*:  
🔊 *"रमेश जी, ₹200 उधार जोड़ा गया। कुल बकाया ₹700..."*

*(Presenter interrupts and talks over the assistant)*:  
> 🗣️ *"अरे नहीं, 200 नहीं 300 था!"*

*(Immediate reaction):*
- Assistant cuts off speech instantly (0ms audible overlap).
- `bargeIn.js` acoustic energy detector halts playback.
- `correctionHandler.js` extracts: `original: 200`, `corrected: 300`.
- Ledger updates to ₹800.
- Assistant speaks:  
  🔊 *"रमेश जी, राशि सुधारी गई ₹300। कुल बकाया ₹800।"*

> **Presenter**:  
> "Our acoustic VAD provides **real barge-in**. The shopkeeper didn't have to wait for the machine to finish or press a cancel button. They corrected their slip-of-tongue naturally."

---

### [1:45 - 2:15] Demo 3: Full Payment & Celebration

*(Presenter taps mic)*

> **Presenter speaks**:  
> 🗣️ *"रमेश ने आठ सौ रुपये पूरे जमा कर दिए"*

*(Immediate reaction):*
- Ledger updates `totalDue` from ₹800 $\rightarrow$ **₹0**.
- `dueSince` resets to null.
- **Screen explodes in a 60fps celebratory confetti particle burst!**
- Assistant speaks warmly:  
  🔊 *"धन्यवाद रमेश जी! आपका शर्मा किराना पर सभी बकाया चुका दिया गया है।"*

> **Presenter**:  
> "When informal credit is cleared, it’s a moment of relief and trust. Our micro-celebration UI reinforces positive payment psychology without cluttering the app."

---

### [1:45 - 2:45] Demo 4: 1-Tap Polite WhatsApp Reminders

*(Presenter taps the customer list and opens the ledger or clicks the `📢 तगादा` button on the header)*

> **Presenter**:  
> "What about customers who forget to pay? Kirana owners hate calling customers for money because it feels awkward and confrontational.  
>  
> Voice Khata features an **automated reminder scheduler**. Every day, it scans customers whose balance has exceeded 7 days."

*(Presenter clicks '📢 तगादा' button):*
- Modal shows overdue customer `Suresh` (Overdue 12 days, Due ₹1,500).
- Shows polite Hindi template preview:  
  *"नमस्ते सुरेश जी, शर्मा किराना से। आपका ₹1500 का बकाया 12 दिन से है। कृपया सुविधा अनुसार भुगतान करें।"*
- Presenter clicks **"💬 WhatsApp पर भेजें"**:
- WhatsApp Web / App opens with prefilled text and country code (+91).
- Dispatched badge changes to `✅ भेजा गया`.
- Anti-spam cooldown locks reminder for 3 days to protect merchant-customer relationship.

---

### [2:45 - 3:00] Conclusion: Offline-First Architecture & Data Privacy

*(Presenter quickly toggles browser DevTools to 'Offline' and adds a transaction)*

> **Presenter**:  
> "Most importantly, Voice Khata is **offline-first**. Everything is stored in encrypted IndexedDB using Dexie.js and PBKDF2/AES-256-GCM. When the network drops in a rural basement store, the ledger never crashes. Changes queue in our Outbox and sync to Supabase when reconnected.  
>  
> And if the merchant ever changes phones or retires, our 'Delete All Data' button executes a full local and cloud cryptographic wipe.  
>  
> **Voice Khata**: The simplicity of speech, the speed of local hardware, and the intelligence of Gnani AI. Thank you!"

---

## 💡 Quick Tips for Judges Q&A

- **Q: What if the store is very noisy?**  
  *A: We combine a fast local lexicon parser with Gnani's Prisma v3.0 noise-robust acoustic model, backed by an RMS energy filter that discards ambient low-energy background babble.*
- **Q: How do you handle regional Indian languages and mixed code-switching?**  
  *A: We support 10 Indian regional languages (Hindi, Kannada, Tamil, Telugu, Marathi, Gujarati, Bengali, Malayalam, Punjabi, Indian English) with automatic language detection (`indic-auto`). We assign dedicated regional Gnani Timbre voices (Chetana for Kannada, Gayatri for Tamil, Rohini for Telugu, Kavita for Marathi, etc.) and handle dialectal possessive suffixes like Kannada '-du'/'ge', Telugu '-ki'/'ku', Tamil '-kku', and Hindi '-ka'/'ke' without manual language switching.*
- **Q: What happens if API limits run out?**  
  *A: In the Settings modal, merchants can plug in their personal Gnani, Groq, or Supabase API keys with zero code deployment.*
