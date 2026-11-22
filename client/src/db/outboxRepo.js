/**
 * Outbox Repository
 *
 * Every local write adds an entry here.  The sync worker (Phase 3)
 * reads pending entries, pushes them to the cloud, then removes them.
 *
 * Fields:
 *   id         – uuid
 *   entity     – 'customer' | 'transaction' | 'reminder_log' | 'settings'
 *   entityId   – PK of the affected row
 *   op         – 'create' | 'update' | 'delete'
 *   payload    – JSON-stringified snapshot of the entity at write time
 *   attempts   – how many push attempts so far
 *   lastError  – last error message (null on success)
 *   createdAt  – ISO timestamp
 */

import { v4 as uuidv4 } from 'uuid';
import db from './schema.js';

/**
 * Add an entry to the outbox queue.
 * Called inside a Dexie transaction together with the entity write.
 */
export async function addToOutbox(entity, entityId, op, payload) {
  const entry = {
    id: uuidv4(),
    entity,
    entityId,
    op,
    payload: typeof payload === 'string' ? payload : JSON.stringify(payload),
    attempts: 0,
    lastError: null,
    createdAt: new Date().toISOString(),
  };
  await db.outbox.add(entry);
  return entry;
}

/** Get all pending outbox entries, oldest first, up to `limit`. */
export async function getPending(limit = 50) {
  return db.outbox.orderBy('createdAt').limit(limit).toArray();
}

/** Increment attempt counter and store the error message. */
export async function markAttempted(id, error = null) {
  await db.outbox.update(id, {
    attempts: (await db.outbox.get(id))?.attempts + 1 || 1,
    lastError: error ? String(error) : null,
  });
}

/** Remove a successfully synced entry. */
export async function removeDone(id) {
  await db.outbox.delete(id);
}

/** Count of entries waiting to be synced (drives the UI badge). */
export async function getOutboxCount() {
  return db.outbox.count();
}

/** Wipe the entire outbox (used by "delete all data"). */
export async function clearOutbox() {
  await db.outbox.clear();
}
