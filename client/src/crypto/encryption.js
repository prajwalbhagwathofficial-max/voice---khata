/**
 * Encryption Service — Voice Khata
 *
 * Uses the WebCrypto API (AES-256-GCM) with PBKDF2 key derivation.
 *
 * Flow:
 *   First launch:  owner sets a PIN
 *                  → random 128-bit salt generated
 *                  → PIN + salt → PBKDF2 (100 000 iterations, SHA-256)
 *                  → AES-256-GCM key kept in memory
 *                  → salt + test-cipher stored in IndexedDB settings
 *   Subsequent:    owner enters PIN
 *                  → salt loaded from settings
 *                  → key derived, test-cipher decrypted to verify PIN
 *
 * The PIN is NEVER stored. The key lives only in JS memory.
 * On page reload the owner must re-enter the PIN.
 */

// ── tunables ──
const PBKDF2_ITERATIONS = 100_000;
const SALT_BYTES = 16;   // 128-bit salt
const IV_BYTES = 12;     // 96-bit IV for AES-GCM
const KEY_BITS = 256;
const TEST_PLAINTEXT = 'VOICE_KHATA_PIN_OK'; // known string for PIN verification

// ── in-memory state ──
let _key = null;

// ═══════════════════ Public API ═══════════════════

/**
 * First-time setup: generate salt, derive key, create a test cipher.
 * The caller must persist `salt` and `testCipher` (e.g. in settings repo).
 *
 * @param {string} pin – 4-6 digit owner PIN / passphrase
 * @returns {Promise<{salt: string, testCipher: string}>}
 */
export async function setupEncryption(pin) {
  const salt = crypto.getRandomValues(new Uint8Array(SALT_BYTES));
  _key = await deriveKey(pin, salt);
  const testCipher = await encrypt(TEST_PLAINTEXT);
  return {
    salt: bytesToBase64(salt),
    testCipher,
  };
}

/**
 * Returning user: derive the key from the stored salt.
 * Does NOT verify the PIN — call verifyPin() first if needed.
 */
export async function initEncryption(pin, saltBase64) {
  const salt = base64ToBytes(saltBase64);
  _key = await deriveKey(pin, salt);
}

/**
 * Check whether a PIN is correct by trying to decrypt the stored test cipher.
 * Does NOT set the in-memory key — call initEncryption() after verification.
 *
 * @returns {Promise<boolean>}
 */
export async function verifyPin(pin, saltBase64, testCipherBase64) {
  try {
    const salt = base64ToBytes(saltBase64);
    const tempKey = await deriveKey(pin, salt);

    const data = base64ToBytes(testCipherBase64);
    const iv = data.slice(0, IV_BYTES);
    const ct = data.slice(IV_BYTES);

    const plain = await crypto.subtle.decrypt({ name: 'AES-GCM', iv }, tempKey, ct);
    return new TextDecoder().decode(plain) === TEST_PLAINTEXT;
  } catch {
    return false; // wrong PIN → decryption fails
  }
}

/**
 * Encrypt a plaintext string → base64 (IV ‖ ciphertext).
 */
export async function encrypt(plaintext) {
  assertReady();
  if (!plaintext) return '';

  const iv = crypto.getRandomValues(new Uint8Array(IV_BYTES));
  const encoded = new TextEncoder().encode(plaintext);
  const ct = await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, _key, encoded);

  const combined = new Uint8Array(IV_BYTES + ct.byteLength);
  combined.set(iv);
  combined.set(new Uint8Array(ct), IV_BYTES);
  return bytesToBase64(combined);
}

/**
 * Decrypt a base64 string (IV ‖ ciphertext) → plaintext.
 */
export async function decrypt(encryptedBase64) {
  assertReady();
  if (!encryptedBase64) return '';

  const data = base64ToBytes(encryptedBase64);
  const iv = data.slice(0, IV_BYTES);
  const ct = data.slice(IV_BYTES);

  const plain = await crypto.subtle.decrypt({ name: 'AES-GCM', iv }, _key, ct);
  return new TextDecoder().decode(plain);
}

/** Is the in-memory key available? */
export function isEncryptionReady() {
  return _key !== null;
}

/** Clear the in-memory key (logout / lock). */
export function clearEncryption() {
  _key = null;
}

// ═══════════════════ Internals ═══════════════════

function assertReady() {
  if (!_key) throw new Error('Encryption not initialised — call setupEncryption() or initEncryption() first.');
}

async function deriveKey(pin, salt) {
  const keyMaterial = await crypto.subtle.importKey(
    'raw',
    new TextEncoder().encode(pin),
    'PBKDF2',
    false,
    ['deriveKey'],
  );
  return crypto.subtle.deriveKey(
    { name: 'PBKDF2', salt, iterations: PBKDF2_ITERATIONS, hash: 'SHA-256' },
    keyMaterial,
    { name: 'AES-GCM', length: KEY_BITS },
    false,
    ['encrypt', 'decrypt'],
  );
}

function bytesToBase64(bytes) {
  const bin = Array.from(bytes, (b) => String.fromCharCode(b)).join('');
  return btoa(bin);
}

function base64ToBytes(b64) {
  const bin = atob(b64);
  return new Uint8Array(Array.from(bin, (c) => c.charCodeAt(0)));
}
