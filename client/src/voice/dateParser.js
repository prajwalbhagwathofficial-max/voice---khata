/**
 * Date Parser — Voice Khata
 *
 * Parses spoken date expressions into JavaScript Date objects
 * using the lexicon date mappings.
 *
 * Supports:
 *   - Relative words: "aaj", "kal", "parso", "yesterday", "ನಿನ್ನೆ"
 *   - Explicit dates: "15 November", "10/11"
 *   - Falls back to today if nothing recognised
 */

/**
 * Parse a date expression from spoken text.
 *
 * @param {string} text - Transcript text
 * @param {object[]} lexicons - Lexicon objects with .dates
 * @param {Date} [now] - Reference "now" for relative dates (default: new Date())
 * @returns {{ date: Date, matched: string|null, isExplicit: boolean }}
 */
export function parseDate(text, lexicons, now = new Date()) {
  if (!text) {
    return { date: now, matched: null, isExplicit: false };
  }

  const lower = text.toLowerCase().trim();

  // Build a combined date-word→offset map (longest phrases first)
  const dateMap = buildDateMap(lexicons);
  const sortedKeys = Object.keys(dateMap).sort((a, b) => b.length - a.length);

  // Try lexicon-based relative dates first
  for (const phrase of sortedKeys) {
    if (lower.includes(phrase)) {
      const offset = dateMap[phrase];
      const date = new Date(now);
      date.setDate(date.getDate() + offset);
      date.setHours(0, 0, 0, 0);
      return { date, matched: phrase, isExplicit: true };
    }
  }

  // Try explicit date formats: DD/MM, DD-MM, DD/MM/YYYY
  const slashMatch = lower.match(/(\d{1,2})[\/\-](\d{1,2})(?:[\/\-](\d{2,4}))?/);
  if (slashMatch) {
    const day = parseInt(slashMatch[1], 10);
    const month = parseInt(slashMatch[2], 10) - 1; // JS months are 0-based
    let year = slashMatch[3] ? parseInt(slashMatch[3], 10) : now.getFullYear();
    if (year < 100) year += 2000;
    const date = new Date(year, month, day);
    if (!isNaN(date.getTime())) {
      return { date, matched: slashMatch[0], isExplicit: true };
    }
  }

  // Try "N din pehle" / "N days ago" pattern
  const daysAgoHi = lower.match(/(\d+)\s*(din|दिन)\s*(pehle|पहले|pahle|ago)/);
  if (daysAgoHi) {
    const days = parseInt(daysAgoHi[1], 10);
    const date = new Date(now);
    date.setDate(date.getDate() - days);
    date.setHours(0, 0, 0, 0);
    return { date, matched: daysAgoHi[0], isExplicit: true };
  }

  const daysAgoEn = lower.match(/(\d+)\s*days?\s*ago/);
  if (daysAgoEn) {
    const days = parseInt(daysAgoEn[1], 10);
    const date = new Date(now);
    date.setDate(date.getDate() - days);
    date.setHours(0, 0, 0, 0);
    return { date, matched: daysAgoEn[0], isExplicit: true };
  }

  // No date found — default to now
  return { date: now, matched: null, isExplicit: false };
}

/**
 * Build a combined phrase→offset map from all lexicons.
 */
function buildDateMap(lexicons) {
  const map = {};
  for (const lex of lexicons) {
    if (!lex.dates) continue;
    for (const [phrase, offset] of Object.entries(lex.dates)) {
      map[phrase.toLowerCase()] = offset;
    }
  }
  return map;
}
