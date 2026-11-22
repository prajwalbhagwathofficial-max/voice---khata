/**
 * Sync Engine — Voice Khata
 *
 * Offline-first background sync between IndexedDB and the cloud.
 *
 * Lifecycle:
 *   1. initSync()      – called once at app start
 *   2. triggerSync()    – called after every local write
 *   3. pullFromCloud()  – called at start + periodically
 *   4. stopSync()       – cleanup on unmount
 *
 * Uses the outbox queue pattern:
 *   local write → outbox entry → sync worker → server → Supabase
 */

import { signal } from '@preact/signals';
import * as outboxRepo from '../db/outboxRepo.js';
import * as cloudAdapter from './cloudAdapter.js';
import db from '../db/schema.js';

// ——— Reactive state (Preact signals) ———

/** Current sync status for the UI badge. */
export const syncStatus = signal('synced'); // 'synced' | 'pending' | 'syncing' | 'offline' | 'error'

/** Number of outbox entries waiting to be pushed. */
export const pendingCount = signal(0);

/** ISO timestamp of the last successful sync. */
export const lastSyncAt = signal(null);

// ——— Internal state ———
let _intervalId = null;
let _isSyncing = false;
let _syncIntervalMs = 30000;
let _maxRetry = 5;
let _backoffBase = 1000;
let _backoffMax = 60000;

// ——— Public API ———

/**
 * Start the sync engine. Call once at app boot.
 *
 * @param {object} [settings] – sync settings from config/settings.json
 */
export function initSync(settings = {}) {
  _syncIntervalMs = settings.sync_interval_ms || 30000;
  _maxRetry = settings.max_retry_attempts || 5;
  _backoffBase = settings.backoff_base_ms || 1000;
  _backoffMax = settings.backoff_max_ms || 60000;

  // Load persisted lastSyncAt
  const stored = localStorage.getItem('vk_last_sync');
  if (stored) lastSyncAt.value = stored;

  // Online / offline listeners
  window.addEventListener('online', _onOnline);
  window.addEventListener('offline', _onOffline);

  // Set initial state
  if (!navigator.onLine) {
    syncStatus.value = 'offline';
  } else {
    // Pull on start, then process outbox
    pullFromCloud().then(() => processOutbox());
  }

  // Periodic sync
  _intervalId = setInterval(() => {
    if (navigator.onLine && !_isSyncing) {
      processOutbox();
    }
  }, _syncIntervalMs);

  _updatePendingCount();
}

/** Stop the sync engine (cleanup). */
export function stopSync() {
  if (_intervalId) clearInterval(_intervalId);
  window.removeEventListener('online', _onOnline);
  window.removeEventListener('offline', _onOffline);
  _intervalId = null;
}

/**
 * Trigger an immediate sync cycle (call after a local write).
 * Debounced: waits 500ms so batch writes only trigger one push.
 */
let _triggerTimer = null;
export function triggerSync() {
  _updatePendingCount();
  if (_triggerTimer) clearTimeout(_triggerTimer);
  _triggerTimer = setTimeout(() => {
    if (navigator.onLine && !_isSyncing) processOutbox();
  }, 500);
}

// ——— Core sync loops ———

/** Push pending outbox entries to the cloud. */
export async function processOutbox() {
  if (_isSyncing || !navigator.onLine) return;
  _isSyncing = true;

  try {
    await _updatePendingCount();
    const pending = await outboxRepo.getPending(20);

    if (pending.length === 0) {
      syncStatus.value = 'synced';
      _isSyncing = false;
      return;
    }

    syncStatus.value = 'syncing';

    // Filter out entries that exceeded max retries
    const toSync = pending.filter((e) => e.attempts < _maxRetry);
    const abandoned = pending.filter((e) => e.attempts >= _maxRetry);

    // Remove abandoned entries (they'll be retried on next full pull)
    for (const entry of abandoned) {
      await outboxRepo.removeDone(entry.id);
    }

    if (toSync.length === 0) {
      await _updatePendingCount();
      syncStatus.value = pendingCount.value > 0 ? 'pending' : 'synced';
      _isSyncing = false;
      return;
    }

    const result = await cloudAdapter.pushEntries(toSync);

    // Remove synced
    for (const id of result.synced || []) {
      await outboxRepo.removeDone(id);
    }

    // Mark failed with backoff
    for (const fail of result.failed || []) {
      await outboxRepo.markAttempted(fail.id, fail.error);
    }

    // Record sync time
    lastSyncAt.value = new Date().toISOString();
    localStorage.setItem('vk_last_sync', lastSyncAt.value);

    await _updatePendingCount();
    syncStatus.value = pendingCount.value > 0 ? 'pending' : 'synced';

  } catch (err) {
    console.error('[sync] Push failed:', err);
    syncStatus.value = 'error';
  }

  _isSyncing = false;
}

/**
 * Pull remote changes and merge into local IndexedDB.
 * Uses last-write-wins for customers (by updatedAt).
 * Transactions are append-only (insert if not exists).
 */
export async function pullFromCloud() {
  if (!navigator.onLine) return;

  try {
    const since = lastSyncAt.value || undefined;
    const data = await cloudAdapter.pullChanges(since);

    // Merge customers (last-write-wins by updatedAt)
    for (const c of data.customers || []) {
      const local = await db.customers.get(c.id);
      if (!local || c.updatedAt > local.updatedAt) {
        await db.customers.put(c);
      }
    }

    // Merge transactions (append-only: insert if not exists)
    for (const t of data.transactions || []) {
      const exists = await db.transactions.get(t.id);
      if (!exists) {
        await db.transactions.put(t);
      }
    }

    // Merge reminder_log (append-only)
    for (const r of data.reminder_log || []) {
      const exists = await db.reminder_log.get(r.id);
      if (!exists) {
        await db.reminder_log.put(r);
      }
    }

    // Merge settings (last-write-wins by updatedAt)
    for (const s of data.settings || []) {
      const local = await db.settings.get(s.key);
      if (!local || s.updatedAt > (local.updatedAt || '')) {
        await db.settings.put(s);
      }
    }

    // Update sync timestamp
    if (data.pulledAt) {
      lastSyncAt.value = data.pulledAt;
      localStorage.setItem('vk_last_sync', data.pulledAt);
    }

  } catch (err) {
    console.error('[sync] Pull failed:', err);
    // Don't set error state for pull failures — local data still works
  }
}

// ——— Private helpers ———

async function _updatePendingCount() {
  pendingCount.value = await outboxRepo.getOutboxCount();
  if (pendingCount.value > 0 && syncStatus.value === 'synced') {
    syncStatus.value = 'pending';
  }
}

function _onOnline() {
  console.log('[sync] Back online — syncing…');
  syncStatus.value = 'pending';
  processOutbox();
}

function _onOffline() {
  console.log('[sync] Offline');
  syncStatus.value = 'offline';
}
