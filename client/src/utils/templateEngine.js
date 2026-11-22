/**
 * Template Engine — Voice Khata
 *
 * Simple placeholder replacement for message templates.
 * Replaces {name}, {amount}, {total}, {days}, {shop}, {currency}, etc.
 */

/**
 * Fill a template string with variable values.
 *
 * @param {string} template – e.g. "Hello {name}, your due is ₹{total}"
 * @param {object} vars     – e.g. { name: 'Ramesh', total: 500 }
 * @returns {string}
 */
export function fillTemplate(template, vars = {}) {
  if (!template) return '';
  let result = template;
  for (const [key, value] of Object.entries(vars)) {
    result = result.replaceAll(`{${key}}`, String(value ?? ''));
  }
  return result;
}

/**
 * Get a template by key from a language's template set,
 * falling back to English if the key doesn't exist.
 *
 * @param {object} allTemplates – { hi: {...}, kn: {...}, en: {...} }
 * @param {string} lang         – language code
 * @param {string} key          – template key
 * @param {object} [vars]       – placeholder values
 * @returns {string}
 */
export function getTemplate(allTemplates, lang, key, vars = {}) {
  const tmpl = allTemplates?.[lang]?.[key] || allTemplates?.['en']?.[key] || '';
  return fillTemplate(tmpl, vars);
}
