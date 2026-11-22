/**
 * Intent Extractor — Voice Khata (Server-side)
 *
 * Uses Groq LLM (llama-3.3-70b-versatile) to extract structured intent
 * and entities from a spoken transcript.
 *
 * Intents: credit, payment, query, greeting, correction, undo, unknown
 * Entities: customerName, amount, date, phone, confirmOrDeny
 *
 * Falls back gracefully if LLM is unavailable — returns intent: 'unknown'.
 */

import Groq from 'groq-sdk';

let groqClient = null;

/**
 * Initialise the Groq client. Called once at server startup.
 */
export function initGroq(apiKey) {
  if (!apiKey) return;
  groqClient = new Groq({ apiKey });
}

/**
 * System prompt for the LLM — instructs structured JSON output.
 */
const SYSTEM_PROMPT = `You are "Voice Khata" (वॉइस खाता) — an ultra-intelligent, respectful Indian kirana shop assistant (मुनीम जी).
You deeply understand ALL Indian languages (Hindi, Kannada, Tamil, Telugu, Marathi, Gujarati, Bengali, Malayalam, Punjabi, Odia, English) and mixed dialects (Hinglish, Kanglish, Tanglish, Telugish, code-mixing).

You understand fast speech, background noise, regional dialects, colloquial shopkeeper slangs, and speech recognition typos:
- "ओपन द खाता" / "नया खाता खोलो" / "नया कस्टमर" / "open account" / "ಖಾತೆ ಓಪನ್ ಮಾಡಿ" / "కొత్త ఖాతా" / "புது கணக்கு" -> new_customer
- "उधर" / "उधारे" / "आईएएस" / "लिख ले" / "चढ़ा दे" / "borrow" / "ಸಾಲ" / "ಕಡ" / "கடன்" / "అప్పు" / "उधारी" / "खाते में डालो" -> credit
- "जमा" / "दिया" / "आ गए" / "पेमेंट" / "paid" / "ಕೊಟ್ಟ" / "ಕೊಟ್ಟಳು" / "ಕೊಟ್ಟಿದ್ದಾರೆ" / "கொடுத்தார்" / "ఇచ్చాడు" / "settled" / "चुकाया" -> payment
- "खरीदा" / "लिया नकद" / "नगद" / "bought" / "cash sale" / "बिक्री" / "सेल" / "बिल बनाओ" / "ಖರೀದಿಸಿದರು" / "ತೆಗೆದುಕೊಂಡರು" -> purchase
- "हिसाब" / "कितना बाकी" / "बैलेंस" / "due" / "ಬಾಕಿ" / "ಲೆಕ್ಕ" / "பாக்கி" / "బకాయి" / "how much" -> query
- Casual chat, greetings, questions, or random speech -> chat / greeting

Phonetic & Slang Resolution Examples:
- "विसर्ग" / "निसर्ग" / "Nisharga" -> customerName: "Nisarga"
- "avan" / "avanl" / "pawan" / "pawana" -> customerName: "Pavan"
- "umesh" -> "Umesh" / "Ramesh"
- "paanch hundred" / "paanch sau" / "aidu nooru" / "ainuru" / "500" -> amount: 500
- "do hazaar" / "two thousand" / "eradu saavira" -> amount: 2000
- "ek sau pachaas" / "one fifty" / "150" -> amount: 150

ALWAYS respond with valid JSON matching this schema:
{
  "intent": "credit" | "payment" | "purchase" | "query" | "greeting" | "chat" | "correction" | "undo" | "new_customer" | "confirmation" | "denial" | "unknown",
  "customerName": string | null,
  "isFullSettlement": boolean,
  "isBoth": boolean,
  "item": string | null,
  "quantity": string | number | null,
  "amount": number | null,
  "date": string | null,
  "phone": string | null,
  "confirmOrDeny": "confirm" | "deny" | null,
  "correctedField": string | null,
  "correctedValue": string | null,
  "confidence": number,
  "rawLanguage": "hi" | "kn" | "ta" | "te" | "mr" | "gu" | "bn" | "ml" | "pa" | "en",
  "humanReply": string // Crisp, direct financial statement only: e.g. "Ramesh ka ₹1,500 kharch tha, ₹1,500 chuka diya, remaining baaki ₹0 hai." No unnecessary filler.
}

Rules:
1. "new_customer" = open/create a new customer account (e.g. open the khata of Nisarga, naya khata kholo, open khata, khata add karo).
2. "credit" = customer took goods on credit / udhaar (udhaar/udhar/saala/udhari/borrowed/उधार/उधर/लिखो/चढ़ाओ/खाते में डालो/baaki/sāla/kadan/appu).
3. "payment" = customer gave money / payment or cleared debt (diya/kotta/kottalu/paid/settled/जमा/दिया/चुकाया/आ गए/पेमेंट/jama/ichadu/kuduthan/already paid/saara chuka diya/saara kar chuka diya/poora chuka diya/saara udhaar chuka diya/cleared debt).
4. "purchase" = normal purchase / cash sale where customer buys items and pays immediately (e.g. kharida, bought, 2 packets milk, biscuit 20 cash, liya nakad, bikri, bill banao, cash, customer bought items for 250 rupees and paid the full amount, paid in full, customer paid fully). If no specific person name is given, set customerName to null.
5. "query" = shopkeeper asks how much is due or requests account details (kitna/yeshtu/how much/hisab/baaki/baaki hai/evvalavu/entha).
6. "greeting" = conversational greetings (namaste, hello, kaise ho, ram ram, good morning, vanakkam, namaskara).
7. "chat" = casual conversation, questions, compliments, or general remarks.
8. "correction" = user corrects previous entry (nahi/galat/not 300, 500 tha).
9. "undo" = user wants to cancel last entry (hatao, delete, undo, cancel).
10. "confirmation" = user affirms (haan, yes, save, save customer, save kar do, theek hai, bilkul, houdu, aama, sare).
11. "denial" = user denies (nahi, cancel, mat karo, no, beda, vendaam, vaddu).
12. For isFullSettlement: set to true if user says "saara kar chuka diya", "poora chuka diya", "saara udhaar chuka diya", "cleared all debt", "full settlement", or similar phrases. Set amount to null if no explicit number is mentioned.
13. For isBoth: set to true if user says "dono Ramesh", "dono ne chuka diya", "both customers", "dono ka hisab".
14. For amount: extract the numeric integer value only (e.g. 500, 200, 1500, 5000). Support mixed Indian terms: sau/sau (100), hazaar/hazar (1000), lakh (100000), rupaye/rupees/roopayi/rubai/takka.
15. For customerName: extract the person's clean base name, stripping off possessive, dative, or dialect suffixes (ka, ke, ki, ko, ji, bhaiya, ge, du, avara, avaru, ki, ku, gari, garu, kku, la, che, ne, na, 's).
16. ALWAYS generate "humanReply": A crisp, direct, numerical financial statement only (e.g. "Ramesh ka ₹X kharch tha, ₹Y chuka diya, remaining baaki ₹Z hai."). DO NOT talk unnecessary conversational filler or chit-chat.
17. Output only the JSON object.
18. CRITICAL NAME ACCURACY & RESOLUTION RULE:
If a "Known Store Customers" roster is provided in the prompt, ALWAYS compare any spoken name against that roster.
Speech recognition frequently:
- drops opening consonants (e.g. "avan" or "avanl" for "Pavan", "visarga" or "isarga" for "Nisarga", "amesh" for "Ramesh", "uresh" for "Suresh")
- alters Indian vowels, consonants, or slurs (e.g. "pawan", "bhavan", "pawana" for "Pavan"; "umesh" for "Ramesh"; "subhash" for "Suresh"; "chetan" for "Chethan")
- appends grammar/case suffixes (e.g. "pavanka", "pavange", "rameshavaru", "umeshji")
If a spoken word resembles, rhymes with, or is an acoustic/phonetic misrecognition of ANY name in the Known Store Customers list, YOU MUST SET customerName TO THE EXACT CANONICAL NAME FROM THAT LIST.
Only output a new/different customerName if the spoken name has no resemblance whatsoever to any existing customer or if the user explicitly says "open khata" / "naya customer" with a distinct name.`;

/**
 * Extract intent and entities from a transcript.
 *
 * @param {string} transcript - The STT transcript text
 * @param {object} [context] - Optional conversation context
 * @param {string} [context.lastIntent] - Previous turn's intent
 * @param {string} [context.pendingQuestion] - What we last asked
 * @param {object} config - Server config object
 * @returns {Promise<object>} Extracted intent + entities
 */
export async function extractIntent(transcript, context = {}, config = {}) {
  if (!transcript || transcript.trim().length === 0) {
    return defaultResponse('unknown', 0);
  }

  if (!groqClient) {
    // Fallback: return unknown if LLM not configured
    return defaultResponse('unknown', 0);
  }

  const preferredModel = config.settings?.llm?.model || 'qwen/qwen3.8-27b';
  const candidateModels = [preferredModel, 'qwen/qwen3.8-27b', 'openai/gpt-oss-120b', 'openai/gpt-oss-20b'];
  // Deduplicate
  const uniqueModels = [...new Set(candidateModels)];

  const maxTokens = config.settings?.llm?.max_tokens || 512;
  const temperature = config.settings?.llm?.temperature || 0.1;

  // Build user message with context
  let userMsg = `Transcript: "${transcript}"`;
  if (context.lastIntent) {
    userMsg += `\nPrevious intent: ${context.lastIntent}`;
  }
  if (context.pendingQuestion) {
    userMsg += `\nPending question: ${context.pendingQuestion}`;
  }
  if (Array.isArray(context.existingCustomers) && context.existingCustomers.length > 0) {
    const customerListStr = context.existingCustomers
      .map(c => `- ${c.name}${c.city ? ` (${c.city})` : ''}${c.phoneLast4 ? ` [Phone: ...${c.phoneLast4}]` : ''}`)
      .join('\n');
    userMsg += `\nKnown Store Customers:\n${customerListStr}`;
  }

  let lastError = null;
  let isQuotaExhausted = false;

  for (const model of uniqueModels) {
    try {
      const completion = await groqClient.chat.completions.create({
        model,
        messages: [
          { role: 'system', content: SYSTEM_PROMPT },
          { role: 'user', content: userMsg },
        ],
        max_tokens: maxTokens,
        temperature,
        response_format: { type: 'json_object' },
      });

      const content = completion.choices?.[0]?.message?.content;
      if (!content) continue;

      const parsed = JSON.parse(content);
      return {
        intent: parsed.intent || 'unknown',
        customerName: parsed.customerName || null,
        isFullSettlement: Boolean(parsed.isFullSettlement || /saara|poora|pura|full|all|everything|settle|khatam|chuk\s*gaya|chuka\s*diya/i.test(transcript)),
        isBoth: Boolean(parsed.isBoth || /dono|both|ubhayaru|eradu\s*jana/i.test(transcript)),
        item: parsed.item || null,
        quantity: parsed.quantity || null,
        amount: typeof parsed.amount === 'number' ? parsed.amount : null,
        date: parsed.date || null,
        phone: parsed.phone || null,
        confirmOrDeny: parsed.confirmOrDeny || null,
        correctedField: parsed.correctedField || null,
        correctedValue: parsed.correctedValue || null,
        confidence: typeof parsed.confidence === 'number' ? parsed.confidence : 0.95,
        rawLanguage: parsed.rawLanguage || 'hi',
        humanReply: parsed.humanReply || null,
      };
    } catch (err) {
      lastError = err;
      if (err.status === 429 || err.message?.includes('429') || err.message?.toLowerCase().includes('quota') || err.message?.toLowerCase().includes('rate limit')) {
        isQuotaExhausted = true;
      }
      console.warn(`Model ${model} extraction failed: ${err.message}. Trying next fallback...`);
    }
  }

  const resp = defaultResponse('unknown', 0, transcript, context);
  if (isQuotaExhausted) {
    resp.quotaExhausted = true;
    resp.error = 'Groq API Quota Exhausted (Rate Limit / Credits Expired). Please update your API key in Settings.';
  } else if (lastError) {
    resp.error = lastError.message;
  }
  return resp;
}

function defaultResponse(intent, confidence, transcript = '', context = {}) {
  const isFullSettlement = Boolean(/saara|poora|pura|full|all|everything|settle|khatam|chuk\s*gaya|chuka\s*diya|kar\s+chuka/i.test(transcript));
  const isBoth = Boolean(/dono|both|ubhayaru|eradu\s*jana/i.test(transcript));
  let detectedIntent = intent;
  if (detectedIntent === 'unknown' || !detectedIntent) {
    if (/mile|vasooli|jama|paid|diya|chuka|kar\s+chuka|settle/i.test(transcript) || isFullSettlement) {
      detectedIntent = 'payment';
    } else if (/udhaar|udhar|likh|chada|credit|sāla/i.test(transcript)) {
      detectedIntent = 'credit';
    }
  }

  let customerName = null;
  if (Array.isArray(context.existingCustomers)) {
    for (const c of context.existingCustomers) {
      if (c && c.name && transcript.toLowerCase().includes(c.name.toLowerCase())) {
        customerName = c.name;
        break;
      }
    }
  }
  if (!customerName && transcript) {
    const first = transcript.split(/\s+/)[0].replace(/[^a-zA-Z\u0900-\u097F]/g, '');
    if (first && !/^(kripya|please|aaj|kal|dono|both)$/i.test(first)) {
      customerName = first;
    }
  }

  const amtMatch = transcript.match(/(\d[\d,]*)/);
  const amount = amtMatch ? parseInt(amtMatch[1].replace(/,/g, ''), 10) : null;

  return {
    intent: detectedIntent,
    customerName,
    isFullSettlement,
    isBoth,
    item: null,
    quantity: null,
    amount,
    date: null,
    phone: null,
    confirmOrDeny: null,
    correctedField: null,
    correctedValue: null,
    confidence: typeof confidence === 'number' ? confidence : 0,
    rawLanguage: 'hi',
    humanReply: customerName ? `${customerName} ka hisab update ho raha hai.` : 'Kaam batayein.',
  };
}

/**
 * Fastify plugin: registers /api/voice/extract endpoint
 */
export default async function intentRoutes(fastify, { config }) {
  // Initialise Groq on registration
  initGroq(config.env.GROQ_API_KEY);

  fastify.post('/api/voice/extract', async (request, reply) => {
    const { transcript, context } = request.body || {};

    if (!transcript) {
      return reply.code(400).send({ error: 'transcript is required' });
    }

    const result = await extractIntent(transcript, context, config);
    return result;
  });
}
