/**
 * Customer ID Generator — Voice Khata
 *
 * Format: {name}_{city}_{first N digits of phone}
 *   – all lowercase
 *   – spaces replaced by separator
 *   – non-alphanumeric chars removed (Unicode-aware: keeps Devanagari, Kannada, etc.)
 *   – collision check appends _2, _3, … if the base ID already exists
 *
 * Example: "Pavan" / "Kengeri" / "8317365720" → "pavan_kengeri_831736"
 */

/**
 * Build the base customer ID (no collision check).
 *
 * @param {string} name   – customer name
 * @param {string} city   – city / village / colony
 * @param {string} phone  – full phone number
 * @param {object} [settings]
 * @param {number} [settings.phone_digits_in_id=6]
 * @param {string} [settings.separator='_']
 * @returns {string}
 */
export function generateCustomerId(name, city, phone, settings = {}) {
  const sep = settings.separator || '_';
  const digits = settings.phone_digits_in_id || 6;

  const cleanName = sanitise(name, sep);
  const cleanCity = sanitise(city, sep);
  const phonePrefix = (phone || '').replace(/\D/g, '').slice(0, digits);

  if (!cleanName) throw new Error('Customer name is required for ID generation');
  if (!cleanCity) throw new Error('City is required for ID generation');

  let phoneSegment = phonePrefix;
  if (!phoneSegment) {
    if (settings.allow_no_phone || settings.require_phone === false) {
      phoneSegment = settings.no_phone_identifier || 'nophone';
    } else {
      throw new Error('Phone number is required for ID generation');
    }
  }

  return `${cleanName}${sep}${cleanCity}${sep}${phoneSegment}`;
}

/**
 * Generate a unique customer ID, appending _2, _3, … on collision.
 *
 * @param {string}   name
 * @param {string}   city
 * @param {string}   phone
 * @param {object}   settings
 * @param {Function} existsCheck – async (candidateId) => boolean
 * @returns {Promise<string>}
 */
export async function generateUniqueCustomerId(name, city, phone, settings, existsCheck) {
  const base = generateCustomerId(name, city, phone, settings);

  if (!(await existsCheck(base))) return base;

  const sep = settings.separator || '_';
  for (let i = 2; i <= 100; i++) {
    const candidate = `${base}${sep}${i}`;
    if (!(await existsCheck(candidate))) return candidate;
  }

  throw new Error(`Too many ID collisions for base: ${base}`);
}

// ─── internal ───

/**
 * Lowercase, replace whitespace with separator, strip non-word chars.
 * Uses Unicode-aware regex so Devanagari / Kannada letters AND
 * combining marks (matras like े ं ी) are kept.
 */
function sanitise(str, sep) {
  if (!str) return '';
  const esc = sep.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return str
    .toLowerCase()
    .trim()
    .replace(/\s+/g, sep)
    .replace(new RegExp(`[^\\p{L}\\p{M}\\p{N}${esc}]`, 'gu'), '')
    .replace(new RegExp(`${esc}+`, 'g'), sep)
    .replace(new RegExp(`^${esc}|${esc}$`, 'g'), '');
}
