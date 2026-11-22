/**
 * AI Disclosure Manager — Voice Khata
 *
 * Manages the mandatory AI disclosure spoken on the first turn
 * of each session. Tracks whether disclosure has been given
 * so it isn't repeated on every interaction.
 *
 * Template from: config/templates/{lang}.json → "ai_disclosure"
 */

let _disclosed = false;

/**
 * Check if AI disclosure has been given this session.
 */
export function hasDisclosed() {
  return _disclosed;
}

/**
 * Mark disclosure as given.
 */
export function markDisclosed() {
  _disclosed = true;
}

/**
 * Reset disclosure state (e.g., on new session / page reload).
 */
export function resetDisclosure() {
  _disclosed = false;
}

/**
 * Get the disclosure text for the current language,
 * with the shop name filled in.
 *
 * @param {object} templates - Templates object keyed by language code
 * @param {string} lang - Language code ('hi', 'kn', 'en')
 * @param {object} vars - Variables to fill ({ shop })
 * @returns {string|null} Disclosure text, or null if already disclosed
 */
export function getDisclosureText(templates, lang = 'hi', vars = {}) {
  if (_disclosed) return null;

  const tpl = templates?.[lang] || templates?.hi || templates?.en;
  if (!tpl?.ai_disclosure) return null;

  let text = tpl.ai_disclosure;
  for (const [key, val] of Object.entries(vars)) {
    text = text.replaceAll(`{${key}}`, val || '');
  }

  return text;
}

/**
 * Build the disclosure TTS payload and mark as disclosed.
 * Returns null if already disclosed.
 *
 * @param {object} templates
 * @param {string} lang
 * @param {object} vars
 * @returns {{ text: string, language: string }|null}
 */
export function buildDisclosureTTS(templates, lang = 'hi', vars = {}) {
  const text = getDisclosureText(templates, lang, vars);
  if (!text) return null;

  markDisclosed();

  // Map language code to TTS language_code
  const langMap = { hi: 'hi-IN', kn: 'kn-IN', en: 'en-IN' };
  return {
    text,
    language: langMap[lang] || 'hi-IN',
  };
}
