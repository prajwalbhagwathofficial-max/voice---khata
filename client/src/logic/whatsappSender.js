/**
 * WhatsApp Sender — Voice Khata
 *
 * Implementation B (demo/hackathon): opens wa.me deep-link
 * with prefilled text. No WhatsApp Business API needed.
 *
 * The sendReminder() interface returns the link; the UI
 * either opens it directly or shows it to the owner.
 */

/**
 * Build a WhatsApp deep-link URL with prefilled text.
 *
 * @param {string} phone – full phone number (digits only or with +91)
 * @param {string} text  – message text
 * @returns {string} wa.me URL
 */
export function buildWhatsAppLink(phone, text) {
  const digits = (phone || '').replace(/\D/g, '');
  // Add India country code if not present
  const fullPhone = digits.startsWith('91') ? digits : `91${digits}`;
  return `https://wa.me/${fullPhone}?text=${encodeURIComponent(text)}`;
}

/**
 * Open a WhatsApp deep-link in a new tab/window.
 * Returns the URL that was opened.
 *
 * @param {string} phone
 * @param {string} text
 * @returns {string} The wa.me URL
 */
export function openWhatsApp(phone, text) {
  const url = buildWhatsAppLink(phone, text);
  window.open(url, '_blank', 'noopener,noreferrer');
  return url;
}

/**
 * Send a reminder via WhatsApp (wa.me deep-link approach).
 * This is the sendReminder() interface from the spec.
 *
 * @param {object} params
 * @param {string} params.phone    – customer phone
 * @param {string} params.text     – reminder message
 * @param {boolean} [params.autoOpen] – auto-open the link (default: false)
 * @returns {{url: string, opened: boolean}}
 */
export function sendReminder({ phone, text, autoOpen = false }) {
  const url = buildWhatsAppLink(phone, text);
  if (autoOpen && typeof window !== 'undefined') {
    window.open(url, '_blank', 'noopener,noreferrer');
  }
  return { url, opened: autoOpen };
}
