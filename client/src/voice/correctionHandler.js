/**
 * Correction Handler — Voice Khata
 *
 * Manages the correction flow when the user says "galat" / "nahi" / "wrong"
 * after a transaction was confirmed.
 *
 * Flow:
 *   1. System: "Ramesh ji, ₹500 udhaar. Total ₹1200."
 *   2. User: "galat, 300 tha" or "nahi, paanch sau nahi teen sau"
 *   3. System: detects correction intent → asks for clarification or applies fix
 *
 * This module detects correction patterns and builds the corrected extraction.
 */

import { parseNumber } from './numberParser.js';

/**
 * Helper to match keyword with word boundary protection
 * (prevents "no" in English from false-matching inside Kannada "nooru").
 */
function containsWord(text, keyword) {
  const kw = keyword.toLowerCase().trim();
  if (!kw) return false;
  // If keyword consists of ASCII letters, use word boundary regex
  if (/^[a-zA-Z0-9\s]+$/.test(kw)) {
    const escaped = kw.replace(/[-/\\^$*+?.()|[\]{}]/g, '\\$&');
    const regex = new RegExp(`(?:^|\\s|[^a-zA-Z0-9])${escaped}(?:$|\\s|[^a-zA-Z0-9])`, 'i');
    return regex.test(text);
  }
  // For Unicode scripts (Devanagari, Kannada), use substring check
  return text.includes(kw);
}

/**
 * Check if the transcript contains a correction/negation signal.
 *
 * @param {string} transcript - User's spoken text
 * @param {object[]} lexicons - Lexicon objects
 * @returns {boolean}
 */
export function isCorrection(transcript, lexicons) {
  if (!transcript) return false;
  const lower = transcript.toLowerCase().trim();

  for (const lex of lexicons) {
    const words = [
      ...(lex.correction || []),
      ...(lex.negation || []),
    ];
    for (const word of words) {
      if (containsWord(lower, word)) {
        return true;
      }
    }
  }
  return false;
}

/**
 * Check if the transcript is a confirmation.
 *
 * @param {string} transcript
 * @param {object[]} lexicons
 * @returns {boolean}
 */
export function isConfirmation(transcript, lexicons) {
  if (!transcript) return false;
  const lower = transcript.toLowerCase().trim();

  for (const lex of lexicons) {
    const words = lex.confirmation || [];
    for (const word of words) {
      if (containsWord(lower, word)) {
        return true;
      }
    }
  }
  return false;
}

/**
 * Check if the transcript is a denial (subset of negation, more explicit).
 *
 * @param {string} transcript
 * @param {object[]} lexicons
 * @returns {boolean}
 */
export function isDenial(transcript, lexicons) {
  if (!transcript) return false;
  const lower = transcript.toLowerCase().trim();

  for (const lex of lexicons) {
    const words = lex.negation || [];
    for (const word of words) {
      if (containsWord(lower, word)) {
        return true;
      }
    }
  }
  return false;
}

/**
 * Detect the intent category from lexicon keywords (fast, pre-LLM check).
 *
 * @param {string} transcript
 * @param {object[]} lexicons
 * @returns {'credit'|'payment'|'query'|'undo'|'correction'|'confirmation'|'denial'|null}
 */
export function detectIntentFromLexicon(transcript, lexicons) {
  if (!transcript) return null;
  const lower = transcript.toLowerCase().trim();

  // Check in order of specificity
  const checks = [
    { key: 'undo_words', intent: 'undo' },
    { key: 'correction', intent: 'correction' },
    { key: 'query_words', intent: 'query' },
    { key: 'purchase_words', intent: 'purchase' },
    { key: 'payment_words', intent: 'payment' },
    { key: 'credit_words', intent: 'credit' },
  ];

  for (const { key, intent } of checks) {
    for (const lex of lexicons) {
      const words = lex[key] || [];
      for (const word of words) {
        if (containsWord(lower, word)) {
          return intent;
        }
      }
    }
  }

  // Check confirmation/denial last
  if (isConfirmation(transcript, lexicons)) return 'confirmation';
  if (isDenial(transcript, lexicons)) return 'denial';

  return null;
}

/**
 * Extract a corrected amount from a correction utterance.
 * Pattern: "nahi, 300 tha" or "galat, paanch sau nahi teen sau"
 *
 * @param {string} transcript
 * @param {object[]} lexicons
 * @returns {{ correctedAmount: number|null, originalAmount: number|null }}
 */
export function extractCorrectedAmount(transcript, lexicons) {
  if (!transcript) return { correctedAmount: null, originalAmount: null };

  const lower = transcript.toLowerCase().trim();

  // 1. Check for explicit digit occurrences
  const digitMatches = [...lower.matchAll(/\b(\d+(?:,\d+)*(?:\.\d+)?)\b/g)];
  if (digitMatches.length >= 2) {
    const orig = parseFloat(digitMatches[0][1].replace(/,/g, ''));
    const corr = parseFloat(digitMatches[1][1].replace(/,/g, ''));
    return { correctedAmount: corr, originalAmount: orig };
  } else if (digitMatches.length === 1) {
    return { correctedAmount: parseFloat(digitMatches[0][1].replace(/,/g, '')), originalAmount: null };
  }

  // 2. Scan for negation keywords with whole-word boundary matching
  const allNegations = [];
  for (const lex of lexicons) {
    for (const word of (lex.negation || [])) {
      allNegations.push(word.toLowerCase());
    }
    for (const word of (lex.correction || [])) {
      allNegations.push(word.toLowerCase());
    }
  }

  let lastNegIdx = -1;
  let matchedNegLen = 0;

  for (const neg of allNegations) {
    const kw = neg.trim();
    if (!kw) continue;

    let regex;
    if (/^[a-zA-Z0-9\s]+$/.test(kw)) {
      const escaped = kw.replace(/[-/\\^$*+?.()|[\]{}]/g, '\\$&');
      regex = new RegExp(`(?:^|\\s|[^a-zA-Z0-9])(${escaped})(?:$|\\s|[^a-zA-Z0-9])`, 'gi');
    } else {
      regex = new RegExp(kw.replace(/[-/\\^$*+?.()|[\]{}]/g, '\\$&'), 'g');
    }

    let match;
    while ((match = regex.exec(lower)) !== null) {
      const matchPos = match.index + (match[0].indexOf(kw));
      if (matchPos > lastNegIdx) {
        lastNegIdx = matchPos;
        matchedNegLen = kw.length;
      }
    }
  }

  if (lastNegIdx === -1) {
    const single = parseNumber(transcript, lexicons);
    return { correctedAmount: single.value, originalAmount: null };
  }

  const before = transcript.slice(0, lastNegIdx).trim();
  const after = transcript.slice(lastNegIdx + matchedNegLen).trim();

  const beforeNum = parseNumber(before, lexicons);
  const afterNum = parseNumber(after, lexicons);

  if (beforeNum.value !== null && afterNum.value !== null) {
    return { correctedAmount: afterNum.value, originalAmount: beforeNum.value };
  }

  if (afterNum.value !== null) {
    return { correctedAmount: afterNum.value, originalAmount: null };
  }

  if (beforeNum.value !== null) {
    return { correctedAmount: beforeNum.value, originalAmount: null };
  }

  return { correctedAmount: null, originalAmount: null };
}
