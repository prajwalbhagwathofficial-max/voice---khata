/**
 * Sync Status — re-exports for convenience
 *
 * Components import from here to get the reactive signals
 * without depending on the full sync engine.
 */

export { syncStatus, pendingCount, lastSyncAt } from './syncEngine.js';
