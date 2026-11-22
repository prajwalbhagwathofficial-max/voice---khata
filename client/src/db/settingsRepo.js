/**
 * Settings Repository
 *
 * Stores runtime setting overrides as key-value pairs in IndexedDB.
 * The UI settings panel writes here; the config loader merges these
 * with config/settings.json defaults.
 *
 * Schema:  { key: string, value: string (JSON), updatedAt: string }
 */

import db from './schema.js';
import { addToOutbox } from './outboxRepo.js';

/**
 * Get a single setting by key.
 * @returns {*} Parsed value, or undefined if not set.
 */
export async function getSetting(key) {
  const row = await db.settings.get(key);
  if (!row) return undefined;
  try {
    return JSON.parse(row.value);
  } catch {
    return row.value;
  }
}

/**
 * Set a single setting. Overwrites if the key already exists.
 * Also adds to outbox for cloud sync.
 */
export async function setSetting(key, value) {
  const now = new Date().toISOString();
  const row = { key, value: JSON.stringify(value), updatedAt: now };

  await db.transaction('rw', [db.settings, db.outbox], async () => {
    await db.settings.put(row);
    await addToOutbox('settings', key, 'update', row);
  });
  return row;
}

/**
 * Get all stored overrides as a plain object { key: parsedValue }.
 */
export async function getAllSettings() {
  const rows = await db.settings.toArray();
  const result = {};
  for (const row of rows) {
    try {
      result[row.key] = JSON.parse(row.value);
    } catch {
      result[row.key] = row.value;
    }
  }
  return result;
}

/**
 * Merge stored overrides on top of the defaults object.
 * Supports dot-notation keys (e.g. "reminders.reminder_first_after_days").
 */
export async function getSettingsMerged(defaults) {
  const overrides = await getAllSettings();
  const merged = JSON.parse(JSON.stringify(defaults)); // deep clone

  for (const [key, value] of Object.entries(overrides)) {
    // Support dot-notation: "reminders.reminder_first_after_days" → nested set
    const parts = key.split('.');
    let obj = merged;
    for (let i = 0; i < parts.length - 1; i++) {
      if (!(parts[i] in obj) || typeof obj[parts[i]] !== 'object') {
        obj[parts[i]] = {};
      }
      obj = obj[parts[i]];
    }
    obj[parts[parts.length - 1]] = value;
  }
  return merged;
}

/** Remove a single override (reverts to the file default). */
export async function resetSetting(key) {
  await db.transaction('rw', [db.settings, db.outbox], async () => {
    await db.settings.delete(key);
    await addToOutbox('settings', key, 'delete', { key });
  });
}

/** Wipe all setting overrides (used by "delete all data"). */
export async function clearSettings() {
  await db.settings.clear();
}
