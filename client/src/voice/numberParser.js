/**
 * Number Parser — Voice Khata
 *
 * Deterministic parser that converts spoken number words to numeric values
 * using the lexicon files. Supports Hindi, Kannada, English, and Hinglish/Kanglish.
 *
 * Examples:
 *   "paanch sau" → 500
 *   "do hazaar teen sau pachaas" → 2350
 *   "ಐದು ನೂರು" → 500
 *   "five hundred fifty" → 550
 *   "250 rupees" → 250
 *
 * Algorithm:
 *   1. Tokenise the input
 *   2. Try to parse each token as a digit literal or a lexicon word
 *   3. When a multiplier is encountered (sau/hazaar/hundred/nooru),
 *      multiply the running group by that multiplier
 *   4. Sum all groups
 */

/**
 * Parse a spoken utterance to extract a numeric amount.
 * Tries all provided lexicons (multi-language) so code-switching works.
 *
 * @param {string} text - The spoken text (transcript)
 * @param {object[]} lexicons - Array of lexicon objects (hi, kn, en)
 * @returns {{ value: number|null, raw: string }} - Parsed value and the matched portion
 */
export function parseNumber(text, lexicons) {
  if (!text) {
    return { value: null, raw: '' };
  }

  // Check if any token is a plain digit/number first (always extracts 500, ₹500, 250/- etc.)
  const digitLiteral = extractDigitLiteral(text);
  if (digitLiteral !== null) {
    return { value: digitLiteral, raw: String(digitLiteral) };
  }

  if (!lexicons || lexicons.length === 0) {
    return { value: null, raw: '' };
  }

  // Build a combined word→value map from all lexicons
  const wordMap = buildWordMap(lexicons);

  // Also find multi-word keys (sorted longest-first for greedy matching)
  const multiWordKeys = Object.keys(wordMap)
    .filter(k => k.includes(' '))
    .sort((a, b) => b.length - a.length);

  // Normalise input
  let normalised = text.toLowerCase().trim();
  // Remove currency words
  normalised = normalised.replace(/[₹$]/g, '');
  normalised = normalised.replace(/\b(rupees?|rupaye?|rupiya|rs\.?)\b/gi, '');
  normalised = normalised.trim();

  // Replace multi-word lexicon entries with their values first
  for (const phrase of multiWordKeys) {
    if (normalised.includes(phrase)) {
      normalised = normalised.replace(phrase, ` __MW_${wordMap[phrase]}__ `);
    }
  }

  // Tokenise
  const tokens = normalised
    .split(/[\s,]+/)
    .filter(t => t.length > 0);

  // Get connectors set
  const connectors = new Set();
  for (const lex of lexicons) {
    if (lex.numbers?.connectors) {
      lex.numbers.connectors.forEach(c => connectors.add(c.toLowerCase()));
    }
  }

  // Parse token sequence into a number
  const result = parseTokens(tokens, wordMap, connectors);
  return result;
}

/**
 * Extract a plain digit literal from text (e.g., "250", "1500")
 */
function extractDigitLiteral(text) {
  // Match a standalone or boundary-bounded number (e.g. 500, 3.5, ₹1,500, 250/-, 500उधार)
  const match = text.match(/(?:^|[^\d.])(\d+(?:,\d+)*(?:\.\d+)?)(?:[^\d.]|$)/);
  if (match) {
    const cleaned = match[1].replace(/,/g, '');
    const num = parseFloat(cleaned);
    if (!isNaN(num) && num >= 0) return num;
  }
  return null;
}

/**
 * Build a combined word→value map from all lexicons.
 */
function buildWordMap(lexicons) {
  const map = {};
  for (const lex of lexicons) {
    if (!lex.numbers) continue;
    const { digits, teens, tens, multipliers } = lex.numbers;
    for (const group of [digits, teens, tens]) {
      if (group) {
        for (const [word, val] of Object.entries(group)) {
          map[word.toLowerCase()] = { value: val, type: 'number' };
        }
      }
    }
    if (multipliers) {
      for (const [word, val] of Object.entries(multipliers)) {
        map[word.toLowerCase()] = { value: val, type: 'multiplier' };
      }
    }
  }
  return map;
}

/**
 * Parse tokens into a number using the word map.
 * Handles patterns like "paanch sau tees" → 530, "do hazaar" → 2000.
 */
function parseTokens(tokens, wordMap, connectors) {
  let total = 0;
  let current = 0;
  let found = false;
  const rawParts = [];

  for (const token of tokens) {
    // Check for multi-word replacement
    const mwMatch = token.match(/^__MW_(.+)__$/);
    if (mwMatch) {
      const val = parseFloat(mwMatch[1]);
      current += val;
      found = true;
      continue;
    }

    // Skip connectors ("aur", "and", "mattu")
    if (connectors.has(token)) continue;

    const entry = wordMap[token];
    if (!entry) {
      // If we were accumulating and hit an unknown word, stop
      if (found) break;
      continue;
    }

    found = true;
    rawParts.push(token);

    if (entry.type === 'multiplier') {
      if (current === 0) current = 1; // "sau" alone = 100
      current *= entry.value;
      // If multiplier is >= 1000, push to total (allows "do hazaar paanch sau")
      if (entry.value >= 1000) {
        total += current;
        current = 0;
      }
    } else {
      current += entry.value;
    }
  }

  total += current;

  if (!found) {
    return { value: null, raw: '' };
  }

  return { value: total, raw: rawParts.join(' ') };
}

/**
 * Extract ALL numbers from a text (for phone number scenarios, etc.)
 * Returns an array of { value, startIndex }.
 */
export function extractAllNumbers(text, lexicons) {
  const result = parseNumber(text, lexicons);
  if (result.value !== null) {
    return [result];
  }
  return [];
}
