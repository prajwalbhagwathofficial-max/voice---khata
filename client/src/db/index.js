/**
 * Database Layer — Barrel Export
 *
 * Usage:
 *   import { db, customerRepo, transactionRepo, ... } from './db/index.js';
 */

export { default as db } from './schema.js';
export * as customerRepo from './customerRepo.js';
export * as transactionRepo from './transactionRepo.js';
export * as settingsRepo from './settingsRepo.js';
export * as reminderLogRepo from './reminderLogRepo.js';
export * as outboxRepo from './outboxRepo.js';

// ───────────── Delete-all-data (wipe local + mark for cloud) ─────────────

import db_instance from './schema.js';
import { clearCustomers } from './customerRepo.js';
import { clearTransactions } from './transactionRepo.js';
import { clearSettings } from './settingsRepo.js';
import { clearReminderLogs } from './reminderLogRepo.js';
import { clearOutbox } from './outboxRepo.js';

/**
 * Nuclear option — wipe ALL local data.
 * Cloud wipe is handled separately via the sync adapter in Phase 3.
 */
export async function deleteAllLocalData() {
  await clearCustomers();
  await clearTransactions();
  await clearReminderLogs();
  await clearSettings();
  await clearOutbox();
}
