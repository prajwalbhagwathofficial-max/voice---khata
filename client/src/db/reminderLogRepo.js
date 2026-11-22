/**
 * Reminder Log Repository
 *
 * Append-only log of every reminder/thank-you message sent.
 * Used for:
 *  - Deduplication (don't send two firsts on the same day)
 *  - Audit trail (show what was sent and when)
 *  - Deciding when the next reminder is due
 *
 * Fields:
 *   id          – uuid
 *   customerId  – FK to customers.id
 *   channel     – 'whatsapp' | 'sms' | 'voice'
 *   kind        – 'first' | 'repeat' | 'weekly' | 'high_due' | 'thank_you'
 *   sentAt      – ISO timestamp
 *   status      – 'sent' | 'failed' | 'pending'
 *   textSent    – the actual message text that was sent
 *   createdAt   – ISO timestamp
 */

import { v4 as uuidv4 } from 'uuid';
import db from './schema.js';
import { addToOutbox } from './outboxRepo.js';

/**
 * Log a reminder that was sent (or attempted).
 */
export async function logReminder(data) {
  const now = new Date().toISOString();
  const entry = {
    id: uuidv4(),
    customerId: data.customerId,
    channel: data.channel || 'whatsapp',
    kind: data.kind,
    sentAt: data.sentAt || now,
    status: data.status || 'sent',
    textSent: data.textSent || '',
    createdAt: now,
  };

  await db.transaction('rw', [db.reminder_log, db.outbox], async () => {
    await db.reminder_log.add(entry);
    await addToOutbox('reminder_log', entry.id, 'create', entry);
  });
  return entry;
}

/** Get all reminders for a customer, newest first. */
export async function getByCustomer(customerId) {
  return db.reminder_log
    .where('customerId')
    .equals(customerId)
    .reverse()
    .sortBy('sentAt');
}

/**
 * Get the most recent reminder of any kind for a customer.
 * Returns null if none found.
 */
export async function getLastReminder(customerId) {
  const all = await db.reminder_log
    .where('customerId')
    .equals(customerId)
    .toArray();
  if (all.length === 0) return null;
  // Sort descending by sentAt and return first
  all.sort((a, b) => b.sentAt.localeCompare(a.sentAt));
  return all[0];
}

/**
 * Get the last reminder of a specific kind for a customer.
 */
export async function getLastReminderOfKind(customerId, kind) {
  const all = await db.reminder_log
    .where('customerId')
    .equals(customerId)
    .toArray();
  const filtered = all.filter((r) => r.kind === kind);
  if (filtered.length === 0) return null;
  filtered.sort((a, b) => b.sentAt.localeCompare(a.sentAt));
  return filtered[0];
}

/** Get all log entries, optionally filtered by date range. */
export async function getAll(startDate, endDate) {
  let collection = db.reminder_log.orderBy('sentAt');
  const rows = await collection.toArray();
  if (!startDate && !endDate) return rows;
  return rows.filter((r) => {
    if (startDate && r.sentAt < startDate) return false;
    if (endDate && r.sentAt > endDate) return false;
    return true;
  });
}

/** Wipe all reminder logs (used by "delete all data"). */
export async function clearReminderLogs() {
  await db.reminder_log.clear();
}
