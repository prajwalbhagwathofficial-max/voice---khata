/**
 * Customer Repository
 *
 * Handles all customer CRUD with:
 *  - Automatic encryption of phone and address fields
 *  - Customer ID generation (name_city_phonePrefix) with collision check
 *  - Outbox integration for cloud sync
 *  - Search by name (case-insensitive, partial match)
 *
 * The caller passes the crypto module; if encryption is not ready
 * (no PIN entered yet), fields are stored raw (first-launch onboarding).
 */

import { v4 as uuidv4 } from 'uuid';
import db from './schema.js';
import { addToOutbox } from './outboxRepo.js';

// ───────────────────────── helpers ─────────────────────────

/**
 * Encrypt a value if the crypto module is ready, otherwise return as-is.
 */
async function maybeEncrypt(crypto, value) {
  if (!value) return '';
  if (crypto && crypto.isEncryptionReady()) {
    return crypto.encrypt(value);
  }
  return value;
}

/** Decrypt a value if the crypto module is ready. */
async function maybeDecrypt(crypto, value) {
  if (!value) return '';
  if (crypto && crypto.isEncryptionReady()) {
    try {
      return await crypto.decrypt(value);
    } catch {
      // Value might not be encrypted (legacy or pre-PIN data)
      return value;
    }
  }
  return value;
}

/**
 * Attach decrypted phone and address to a raw DB row.
 * Returns a new object (does not mutate).
 */
async function decryptCustomer(crypto, row) {
  if (!row) return null;
  return {
    ...row,
    phone: await maybeDecrypt(crypto, row.phoneEnc),
    address: await maybeDecrypt(crypto, row.addressEnc),
  };
}

// ─────────────────────── public API ───────────────────────

/**
 * Create a new customer.
 *
 * @param {object}   data
 * @param {string}   data.name              – display name
 * @param {string}   data.city              – city / village / colony
 * @param {string}   data.phone             – full phone number (encrypted before storage)
 * @param {string}  [data.address]          – address (encrypted)
 * @param {boolean} [data.optInWhatsApp]    – WhatsApp consent
 * @param {string}  [data.preferredLanguage]
 * @param {object}   settings               – from config (needs customer_id + languages)
 * @param {object}  [crypto]                – encryption service (may be null)
 * @param {Function} generateId             – async (name,city,phone,settings,existsCheck) => id
 * @returns {Promise<object>} the saved customer with decrypted phone
 */
export async function createCustomer(data, settings, crypto, generateId) {
  const { name, city, phone, address, optInWhatsApp, preferredLanguage } = data;

  // 1. Generate unique ID with collision check
  const idSettings = {
    phone_digits_in_id: 6,
    separator: '_',
    allow_no_phone: true,
    no_phone_identifier: 'nophone',
    ...(settings?.customer_id || {}),
  };
  const id = await generateId(
    name, city, phone, idSettings,
    async (candidate) => !!(await db.customers.get(candidate)),
  );

  // 2. Encrypt sensitive fields
  const phoneEnc = await maybeEncrypt(crypto, phone);
  const addressEnc = await maybeEncrypt(crypto, address || '');

  const now = new Date().toISOString();
  const customer = {
    uuid: uuidv4(),
    id,
    name,
    city,
    phoneEnc,
    addressEnc,
    optInWhatsApp: optInWhatsApp ?? false,
    preferredLanguage: preferredLanguage || settings?.languages?.default || 'hi',
    totalDue: 0,
    dueSince: null,
    lastPaymentAt: null,
    lastReminderAt: null,
    lastCreditAt: null,
    createdAt: now,
    updatedAt: now,
  };

  // 3. Atomic write: customer + outbox
  await db.transaction('rw', [db.customers, db.outbox], async () => {
    await db.customers.add(customer);
    await addToOutbox('customer', id, 'create', customer);
  });

  return { ...customer, phone, address: address || '' };
}

/**
 * Get a customer by ID, with decrypted phone/address.
 */
export async function getCustomerById(id, crypto) {
  const row = await db.customers.get(id);
  return decryptCustomer(crypto, row);
}

export function normalizeName(str) {
  if (!str) return '';
  return str.toLowerCase().trim().replace(/[^\p{L}\p{N}]/gu, '');
}

export const SCRIPT_EQUIVALENTS = {
  pavan: ['पवन', 'पावन', 'pavan', 'pawan', 'pawankumar', 'pavankumar', 'pavana', 'ಪವನ್', 'பவன்', 'పవన్', 'পবন', 'પવન', 'avan', 'avanl', 'awan', 'bhavan', 'अवन', 'अवनल'],
  nisarga: ['निसर्ग', 'निसर्गा', 'nisarga', 'nisharga', 'visarga', 'isarga', 'nisarg', 'ನಿಸರ್ಗ', 'ನಿಸರ್ಗ', 'நிசர்கா'],
  ramesh: ['रमेश', 'ramesh', 'ramesha', 'ರಮೇಶ್', 'ரமேஷ்', 'రమేష్', 'রমেশ', 'રમેશ'],
  suresh: ['सुरेश', 'suresh', 'suresha', 'ಸುರೇಶ್', 'சுரேஷ்', 'సురేష్', 'সুরেশ', 'સુરેશ'],
  chirag: ['चिराग', 'chirag', 'ಚಿರಾಗ್', 'சிராக்', 'చిరాగ్', 'চিরাগ', 'ચિરાગ'],
  amisha: ['अमीषा', 'amisha', 'amishas', 'ಅಮೀಷಾ', 'அமிஷா', 'అమీషా'],
  mahesh: ['महेश', 'mahesh', 'ಮಹೇಶ್', 'மகேஷ்', 'మహేష్'],
  rajesh: ['राजेश', 'rajesh', 'ರಾಜೇಶ್', 'ராஜேஷ்', 'రాజేష్'],
  dinesh: ['दिनेश', 'dinesh', 'ದಿನೇಶ್', 'தினேஷ்', 'దినేష్'],
  mukesh: ['मुकेश', 'mukesh', 'ಮುಕೇಶ್', 'முகேஷ்', 'ముఖేష్'],
  anil: ['अनिल', 'anil', 'ಅನಿಲ್', 'அனில்', 'అనిల్'],
  sunil: ['सुनील', 'सुनिल', 'sunil', 'ಸುನಿಲ್', 'சுனில்', 'సునీల్'],
  vijay: ['विजय', 'vijay', 'ವಿಜಯ್', 'விஜய்', 'విజయ్'],
  rahul: ['राहुल', 'rahul', 'ರಾಹುಲ್', 'ராகுல்', 'రాహుల్'],
  amit: ['अमित', 'amit', 'ಅಮಿತ್', 'அமித்', 'అమిత్'],
  rohit: ['रोहित', 'rohit', 'ರೋಹಿತ್', 'ரோஹித்', 'రోహిత్'],
  priya: ['प्रिया', 'priya', 'ಪ್ರಿಯಾ', 'பிரியா', 'ప్రియ'],
  ravi: ['रवि', 'ravi', 'ರವಿ', 'ரவி', 'రవి'],
  kumar: ['कुमार', 'kumar', 'ಕುಮಾರ್', 'குமார்', 'కుమార్'],
  manjunath: ['मंजूनाथ', 'manjunath', 'manjunatha', 'anjunath', 'manju', 'ಮಂಜುನಾಥ್'],
  chethan: ['चेतन', 'chethan', 'chetan', 'cethan', 'jethan', 'ಚೇತನ್', 'சேதன்'],
  venkatesh: ['वेंकटेश', 'venkatesh', 'venkat', 'venki', 'enkatesh', 'ವೆಂಕಟೇಶ್', 'வெங்கடேஷ்'],
  basavaraj: ['बसंत', 'basavaraj', 'basava', 'asavaraj', 'ಬಸವರಾಜ್'],
  shishir: ['शिशिर', 'shishir', 'sisir', 'shishira', 'ಶಿಶಿರ್'],
  sharma: ['शर्मा', 'sharma', 'ಶರ್ಮಾ', 'சர்மா', 'శర్మ'],
  gupta: ['गुप्ता', 'gupta', 'ಗುಪ್ತಾ', 'குப்தா', 'గుప్తా'],
  singh: ['सिंह', 'singh', 'ಸಿಂಗ್', 'சிங்', 'సింగ్'],
  verma: ['वर्मा', 'verma', 'ವರ್ಮಾ', 'வர்மா', 'వర్మ'],
  patel: ['पटेल', 'patel', 'ಪಟೇಲ್', 'படேல்', 'పటేల్'],
  reddy: ['रेड्डी', 'reddy', 'ರೆಡ್ಡಿ', 'ரெட்டி', 'రెడ్డి'],
  gowda: ['गौड़ा', 'gowda', 'ಗೌಡ', 'கவுடா', 'గౌడ'],
};

export function getIndianPhoneticKey(str) {
  if (!str) return '';
  let s = str.toLowerCase().trim();
  // Strip common honorific and grammatical case suffixes:
  s = s.replace(/(?:ka|ke|ki|ko|ji|bhai|bhaiya|ge|ige|du|annu|avaru|avara|kku|ukku|ki|ku|gari|garu|la|che|ne|na|'s)$/i, '');
  // Normalize Indian phonetics:
  s = s.replace(/ph/g, 'f')
       .replace(/[wv]/g, 'v')
       .replace(/ee|ea/g, 'i')
       .replace(/oo|ou/g, 'u')
       .replace(/aa/g, 'a')
       .replace(/ai/g, 'e')
       .replace(/sh|zh|ch/g, 's')
       .replace(/dh/g, 'd')
       .replace(/th/g, 't')
       .replace(/kh/g, 'k')
       .replace(/gh/g, 'g')
       .replace(/bh/g, 'b')
       .replace(/jh/g, 'j');
  // Collapse duplicate letters:
  s = s.replace(/(.)\1+/g, '$1');
  // Strip trailing schwa 'a' (e.g. pavana -> pavan, ramesha -> ramesh)
  if (s.length > 3 && s.endsWith('a')) {
    s = s.slice(0, -1);
  }
  return s;
}

export function levenshtein(a, b) {
  if (a === b) return 0;
  if (!a.length) return b.length;
  if (!b.length) return a.length;
  const v0 = new Array(b.length + 1);
  const v1 = new Array(b.length + 1);
  for (let i = 0; i <= b.length; i++) v0[i] = i;

  for (let i = 0; i < a.length; i++) {
    v1[0] = i + 1;
    for (let j = 0; j < b.length; j++) {
      const cost = a[i] === b[j] ? 0 : 1;
      v1[j + 1] = Math.min(v1[j] + 1, v0[j + 1] + 1, v0[j] + cost);
    }
    for (let j = 0; j <= b.length; j++) v0[j] = v1[j];
  }
  return v1[b.length];
}

export function scoreCustomerMatch(customerName, query) {
  const cn = normalizeName(customerName);
  const q = normalizeName(query);
  if (!cn || !q) return 0;
  if (cn === q) return 1.0;

  // Cross-script phonetic dictionary check
  for (const equivalents of Object.values(SCRIPT_EQUIVALENTS)) {
    const hasCn = equivalents.some((e) => normalizeName(e) === cn);
    const hasQ = equivalents.some((e) => normalizeName(e) === q);
    if (hasCn && hasQ) return 0.99;
  }

  if (cn.includes(q) || q.includes(cn)) return 0.95;

  const pkCn = getIndianPhoneticKey(cn);
  const pkQ = getIndianPhoneticKey(q);
  if (pkCn && pkQ && pkCn === pkQ) return 0.92;

  // Dropped initial consonant / acoustic start cutoff (e.g. pavan -> avan, nisarga -> isarga)
  if ((cn.endsWith(q) || q.endsWith(cn)) && Math.abs(cn.length - q.length) <= 2) {
    return 0.90;
  }
  if (pkCn && pkQ && (pkCn.endsWith(pkQ) || pkQ.endsWith(pkCn)) && Math.abs(pkCn.length - pkQ.length) <= 2) {
    return 0.88;
  }

  // Fuzzy Levenshtein on phonetic keys
  const maxLen = Math.max(pkCn.length, pkQ.length);
  const dist = levenshtein(pkCn, pkQ);
  if (dist <= 2 && maxLen >= 3) {
    return 1 - (dist / maxLen);
  }

  return 0;
}

export function matchesName(customerName, query) {
  return scoreCustomerMatch(customerName, query) >= 0.70;
}

/**
 * Scan raw spoken transcript and extract best matching customer from existing roster.
 * Bypasses LLM misspellings by inspecting spoken tokens.
 */
export function resolveCustomerFromTranscript(transcript, rawName, allCustomers = []) {
  if (!allCustomers.length) return null;

  // 1. Try matching with raw extracted name first
  if (rawName) {
    let bestMatch = null;
    let highestScore = 0;
    for (const c of allCustomers) {
      const score = scoreCustomerMatch(c.name, rawName);
      if (score > highestScore) {
        highestScore = score;
        bestMatch = c;
      }
    }
    if (highestScore >= 0.75) {
      return bestMatch;
    }
  }

  // 2. Scan tokens and n-grams in the raw transcript against customer list
  if (transcript) {
    const words = transcript.toLowerCase().replace(/[^\p{L}\p{N}\s]/gu, ' ').split(/\s+/).filter(Boolean);
    let bestTokenMatch = null;
    let highestTokenScore = 0;

    for (const c of allCustomers) {
      for (const w of words) {
        const score = scoreCustomerMatch(c.name, w);
        if (score > highestTokenScore) {
          highestTokenScore = score;
          bestTokenMatch = c;
        }
      }
    }
    if (highestTokenScore >= 0.75) {
      return bestTokenMatch;
    }
  }

  return null;
}

/**
 * Search customers by name (case-insensitive, cross-script Latin/Indic matching).
 * Returns decrypted results.
 */
export async function searchCustomers(nameQuery, crypto) {
  if (!nameQuery || nameQuery.trim().length === 0) return [];
  const all = await db.customers.toArray();
  const scored = all
    .map((c) => ({ customer: c, score: scoreCustomerMatch(c.name, nameQuery) }))
    .filter((item) => item.score >= 0.70)
    .sort((a, b) => b.score - a.score);
  return Promise.all(scored.map((item) => decryptCustomer(crypto, item.customer)));
}

/** Get all customers with decrypted fields. */
export async function getAllCustomers(crypto) {
  const all = await db.customers.toArray();
  return Promise.all(all.map((c) => decryptCustomer(crypto, c)));
}

/**
 * Update arbitrary fields on a customer.
 * If phone or address are in `fields`, they are re-encrypted.
 */
export async function updateCustomer(id, fields, crypto) {
  const updates = { ...fields, updatedAt: new Date().toISOString() };

  // Re-encrypt if phone or address changed
  if ('phone' in updates) {
    updates.phoneEnc = await maybeEncrypt(crypto, updates.phone);
    delete updates.phone;
  }
  if ('address' in updates) {
    updates.addressEnc = await maybeEncrypt(crypto, updates.address);
    delete updates.address;
  }

  await db.transaction('rw', [db.customers, db.outbox], async () => {
    await db.customers.update(id, updates);
    const full = await db.customers.get(id);
    await addToOutbox('customer', id, 'update', full);
  });

  return getCustomerById(id, crypto);
}

/**
 * Update the due-tracking fields after a credit or payment.
 * This is the core counter logic hook — called by TransactionRepo.
 *
 * @param {string}      id
 * @param {number}      totalDue     – new total
 * @param {string|null} dueSince     – ISO string or null (cleared)
 * @param {string|null} lastCreditAt – set on credit
 * @param {string|null} lastPaymentAt – set on payment
 */
export async function updateCustomerDue(id, totalDue, dueSince, lastCreditAt, lastPaymentAt) {
  const updates = { totalDue, dueSince, updatedAt: new Date().toISOString() };
  if (lastCreditAt !== undefined) updates.lastCreditAt = lastCreditAt;
  if (lastPaymentAt !== undefined) updates.lastPaymentAt = lastPaymentAt;

  await db.transaction('rw', [db.customers, db.outbox], async () => {
    await db.customers.update(id, updates);
    const full = await db.customers.get(id);
    await addToOutbox('customer', id, 'update', full);
  });
}

/**
 * Update the lastReminderAt timestamp.
 */
export async function updateLastReminder(id, sentAt) {
  await db.customers.update(id, {
    lastReminderAt: sentAt,
    updatedAt: new Date().toISOString(),
  });
}

/**
 * Find customers whose dueSince is older than `days` days ago.
 * Used by the reminder scheduler.
 */
export async function getCustomersWithOverdue(days) {
  const cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - days);
  const cutoffStr = cutoff.toISOString();

  const all = await db.customers
    .where('totalDue')
    .above(0)
    .toArray();

  return all.filter((c) => c.dueSince && c.dueSince <= cutoffStr);
}

/**
 * Delete a customer and all their transactions.
 * Adds outbox entries for cloud deletion.
 */
export async function deleteCustomer(id) {
  await db.transaction('rw', [db.customers, db.transactions, db.reminder_log, db.outbox], async () => {
    // Delete related transactions
    const txns = await db.transactions.where('customerId').equals(id).toArray();
    for (const txn of txns) {
      await db.transactions.delete(txn.id);
      await addToOutbox('transaction', txn.id, 'delete', { id: txn.id });
    }
    // Delete related reminder logs
    const logs = await db.reminder_log.where('customerId').equals(id).toArray();
    for (const log of logs) {
      await db.reminder_log.delete(log.id);
    }
    // Delete the customer
    await db.customers.delete(id);
    await addToOutbox('customer', id, 'delete', { id });
  });
}

/** Wipe all customers (used by "delete all data"). */
export async function clearCustomers() {
  await db.customers.clear();
}
