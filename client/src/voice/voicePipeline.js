/**
 * Voice Pipeline Orchestrator — Voice Khata (Client-side)
 *
 * Ties together all voice modules into a coherent pipeline:
 *
 *   Mic → STT Proxy → numberParser + dateParser + correctionHandler
 *                    → LLM (server /api/voice/extract) for complex cases
 *                    → disclosure check
 *                    → business rules
 *                    → TTS Proxy → Speaker
 *                    → barge-in monitor
 *
 * This module is the main export that the UI layer will interact with.
 * It manages conversation state across turns.
 */

import { parseNumber } from './numberParser.js';
import { parseDate } from './dateParser.js';
import {
  isCorrection,
  isConfirmation,
  isDenial,
  detectIntentFromLexicon,
  extractCorrectedAmount,
} from './correctionHandler.js';
import {
  hasDisclosed,
  buildDisclosureTTS,
  resetDisclosure,
} from './disclosure.js';
import {
  ttsStarted,
  ttsStopped,
  checkBargeIn,
  resetBargeIn,
} from './bargeIn.js';

/**
 * @typedef {object} ConversationState
 * @property {string|null} lastIntent - Previous turn's intent
 * @property {string|null} pendingQuestion - What we last asked the user
 * @property {object|null} pendingTransaction - Transaction awaiting confirmation
 * @property {string} language - Detected language code
 * @property {number} turnCount - Number of turns this session
 */

/** Default conversation state */
function newConversationState() {
  return {
    lastIntent: null,
    pendingQuestion: null,
    pendingTransaction: null,
    language: 'hi',
    turnCount: 0,
  };
}

let _state = newConversationState();
let _lexicons = [];
let _templates = {};
let _settings = {};
let _serverUrl = '';

/**
 * Initialise the voice pipeline with lexicons, templates, and settings.
 *
 * @param {object} opts
 * @param {object[]} opts.lexicons - Array of lexicon objects
 * @param {object} opts.templates - Templates keyed by language
 * @param {object} opts.settings - App settings
 * @param {string} opts.serverUrl - Base server URL
 */
export function initPipeline({ lexicons, templates, settings, serverUrl }) {
  _lexicons = lexicons || [];
  _templates = templates || {};
  _settings = settings || {};
  _serverUrl = serverUrl || '';
  _state = newConversationState();
  resetDisclosure();
  resetBargeIn();
}

/**
 * Get current conversation state.
 */
export function getConversationState() {
  return { ..._state };
}

/**
 * Process a transcript through the voice pipeline.
 * This is the MAIN entry point called after STT returns a final transcript.
 *
 * @param {string} transcript - Final STT transcript
 * @param {string} [detectedLanguage] - Language detected by STT
 * @returns {Promise<object>} Pipeline result with actions to take
 */
export async function processTranscript(transcript, detectedLanguage, context = {}) {
  _state.turnCount++;

  // Update language if detected
  if (detectedLanguage) {
    const langMap = {
      'hi-IN': 'hi', 'hi': 'hi',
      'kn-IN': 'kn', 'kn': 'kn',
      'ta-IN': 'ta', 'ta': 'ta',
      'te-IN': 'te', 'te': 'te',
      'mr-IN': 'mr', 'mr': 'mr',
      'gu-IN': 'gu', 'gu': 'gu',
      'bn-IN': 'bn', 'bn': 'bn',
      'ml-IN': 'ml', 'ml': 'ml',
      'pa-IN': 'pa', 'pa': 'pa',
      'en-IN': 'en', 'en': 'en',
    };
    _state.language = langMap[detectedLanguage] || _state.language;
  }

  const result = {
    transcript,
    language: _state.language,
    intent: null,
    entities: {},
    ttsResponses: [],
    actions: [],
    needsConfirmation: false,
    error: null,
  };

  // Step 1: Check if disclosure is needed (first turn)
  if (!hasDisclosed()) {
    const disclosure = buildDisclosureTTS(
      _templates,
      _state.language,
      { shop: _settings.shop?.name || '' }
    );
    if (disclosure) {
      result.ttsResponses.push(disclosure);
    }
  }

  // Step 2: Fast lexicon-based intent detection
  const lexiconIntent = detectIntentFromLexicon(transcript, _lexicons);

  // Step 3: Parse numbers and dates locally (deterministic, fast)
  const numberResult = parseNumber(transcript, _lexicons);
  const dateResult = parseDate(transcript, _lexicons);

  result.entities.amount = numberResult.value;
  result.entities.date = dateResult.date;
  result.entities.dateExplicit = dateResult.isExplicit;

  // Step 4: Handle special cases fast (confirmation, denial, correction)
  if (_state.pendingQuestion) {
    if (isConfirmation(transcript, _lexicons)) {
      result.intent = 'confirmation';
      result.entities.confirmOrDeny = 'confirm';
      _state.pendingQuestion = null;
      return result;
    }
    if (isDenial(transcript, _lexicons)) {
      result.intent = 'denial';
      result.entities.confirmOrDeny = 'deny';
      _state.pendingQuestion = null;
      _state.pendingTransaction = null;
      return result;
    }
  }

  if (isCorrection(transcript, _lexicons)) {
    const corrected = extractCorrectedAmount(transcript, _lexicons);
    result.intent = 'correction';
    result.entities.correctedAmount = corrected.correctedAmount;
    result.entities.originalAmount = corrected.originalAmount;
    _state.lastIntent = 'correction';
    return result;
  }

  // Step 5: Set initial intent from fast lexicon
  if (lexiconIntent) {
    result.intent = lexiconIntent;
  }

  // Step 6: Use LLM for intelligent entity extraction (customer name, amounts, nuances)
  try {
    const llmResult = await callIntentExtractor(transcript, context);
    if (llmResult) {
      if (llmResult.rawLanguage) {
        result.language = llmResult.rawLanguage;
        _state.language = llmResult.rawLanguage;
      }
      if (llmResult.intent && llmResult.intent !== 'unknown') {
        result.intent = llmResult.intent;
      }
      if (llmResult.customerName) {
        result.entities.customerName = llmResult.customerName;
      }
      if (llmResult.item) {
        result.entities.item = llmResult.item;
      }
      if (llmResult.quantity) {
        result.entities.quantity = llmResult.quantity;
      }
      if (llmResult.amount) {
        result.entities.amount = llmResult.amount;
      }
      if (llmResult.phone) {
        result.entities.phone = llmResult.phone;
      }
      if (llmResult.date && !result.entities.dateExplicit) {
        result.entities.date = new Date(llmResult.date);
      }
      if (llmResult.confirmOrDeny) {
        result.entities.confirmOrDeny = llmResult.confirmOrDeny;
      }
      if (llmResult.humanReply) {
        result.humanReply = llmResult.humanReply;
      }
    }
  } catch (err) {
    console.warn('LLM intent extraction failed, using lexicon fallback:', err.message);
  }

  // Step 7: Deterministic fallback for customer name if LLM didn't resolve one
  if (!result.entities.customerName && transcript) {
    const fallbackName = extractCustomerNameFallback(transcript, result.entities.amount, _lexicons);
    if (fallbackName) {
      result.entities.customerName = fallbackName;
    }
  }

  // Default to unknown if nothing found
  if (!result.intent) {
    result.intent = 'unknown';
  }

  _state.lastIntent = result.intent;
  return result;
}

/**
 * Fallback customer name extractor from transcript text
 */
function extractCustomerNameFallback(text, amount, lexicons = []) {
  if (!text) return null;
  let cleaned = text;
  if (amount !== null && amount !== undefined) {
    cleaned = cleaned.replace(new RegExp(String(amount), 'g'), ' ');
  }

  const stopWords = new Set([
    'उधार', 'उधर', 'उधारे', 'उधारी', 'जमा', 'दिया', 'दिए', 'पेमेंट', 'हिसाब', 'बाकी',
    'आईएएस', 'जी', 'का', 'के', 'की', 'को', 'ने', 'है', 'था', 'रुपये', 'रुपया', 'रुपए', 'रु',
    'udhaar', 'udhar', 'udhari', 'diya', 'diye', 'jama', 'payment', 'paid', 'hisab', 'baaki',
    'likho', 'likh', 'chadhao', 'aur', 'se', 'hai', 'tha', 'rupees', 'rs', 'credit', 'due',
    'ಬಾಕಿ', 'ಸಾಲ', 'ಕೊಟ್ಟ', 'ಇದೆ', 'ಅವರ', 'ಅಣ್ಣ', 'ಅವರು', 'ಗೆ', 'ದು', 'ರೂಪಾಯಿ', 'ಖಾತೆ', 'ಓಪನ್',
    'கடன்', 'கொடுத்தார்', 'பாக்கி', 'ரூபாய்',
    'అప్పు', 'ఇచ్చాడు', 'బకాయి', 'రూపాయలు',
    'जमा', 'उधार', 'रुपये'
  ]);

  for (const lex of (lexicons || [])) {
    const words = [
      ...(lex.credit_words || []),
      ...(lex.payment_words || []),
      ...(lex.query_words || []),
      ...(lex.undo_words || []),
      ...(lex.confirmation || []),
      ...(lex.negation || []),
    ];
    for (const w of words) {
      stopWords.add(w.toLowerCase().trim());
    }
  }

  const tokens = cleaned.split(/[\s,./\-]+/).filter(t => t.trim().length > 0);
  const candidateTokens = [];
  for (const t of tokens) {
    const lower = t.toLowerCase();
    if (!stopWords.has(lower) && !/^\d+$/.test(lower)) {
      candidateTokens.push(t);
    }
  }
  return candidateTokens.length > 0 ? candidateTokens.slice(0, 2).join(' ') : null;
}

/**
 * Call the server-side intent extraction endpoint.
 */
async function callIntentExtractor(transcript, context = {}) {
  if (!_serverUrl) return null;

  const resp = await fetch(`${_serverUrl}/api/voice/extract`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      transcript,
      context: {
        lastIntent: _state.lastIntent,
        pendingQuestion: _state.pendingQuestion,
        existingCustomers: context.existingCustomers || [],
      },
    }),
  });

  if (!resp.ok) return null;
  return resp.json();
}

/**
 * Set a pending question (the system just asked the user something).
 */
export function setPendingQuestion(question) {
  _state.pendingQuestion = question;
}

/**
 * Set a pending transaction (awaiting confirmation).
 */
export function setPendingTransaction(txn) {
  _state.pendingTransaction = txn;
}

/**
 * Reset the entire pipeline state (new session).
 */
export function resetPipeline() {
  _state = newConversationState();
  resetDisclosure();
  resetBargeIn();
}

// Re-export barge-in functions for the UI layer
export { ttsStarted, ttsStopped, checkBargeIn };
