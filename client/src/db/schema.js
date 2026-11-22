/**
 * Voice Khata — Dexie (IndexedDB) Schema
 *
 * This is the LOCAL-FIRST primary store. The UI reads/writes here;
 * the outbox queue pushes changes to the cloud asynchronously.
 *
 * Tables:
 *   customers  – id is the composite key (name_city_phonePrefix)
 *   transactions – append-only, uuid primary key
 *   reminder_log – append-only log of every reminder sent
 *   outbox – queue for cloud sync (processed by sync worker)
 *   settings – key-value overrides (merged with config/settings.json)
 */

import Dexie from 'dexie';

const db = new Dexie('VoiceKhata');

db.version(1).stores({
  // Composite key id = name_city_phonePrefix.  Indexed on name & city for search.
  customers: 'id, name, city, totalDue, dueSince, updatedAt',

  // Append-only ledger. Indexed for per-customer and date-range queries.
  transactions: 'id, customerId, type, timestamp, source, createdAt',

  // Every reminder sent – for audit & dedup.
  reminder_log: 'id, customerId, kind, sentAt, status',

  // Offline → cloud sync queue. Processed FIFO by createdAt.
  outbox: 'id, entity, entityId, op, createdAt, attempts',

  // Runtime settings overrides (key-value). Merged with config/settings.json.
  settings: 'key',
});

export default db;
