/**
 * Cloud Adapter — Voice Khata
 *
 * Calls the server sync endpoints. API keys stay server-side.
 * This adapter is swappable: replace these fetch calls to
 * switch from Supabase to Firebase or any other backend.
 */

const BASE = '/api/sync';

/**
 * Push outbox entries to the cloud via the server.
 *
 * @param {Array} entries – raw outbox rows from IndexedDB
 * @returns {Promise<{synced: string[], failed: Array<{id:string,error:string}>}>}
 */
export async function pushEntries(entries) {
  const res = await fetch(`${BASE}/push`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ entries }),
  });

  if (!res.ok) {
    throw new Error(`Push failed: ${res.status} ${res.statusText}`);
  }
  return res.json();
}

/**
 * Pull changes from the cloud since a given timestamp.
 *
 * @param {string} [since] – ISO timestamp; omit for full pull
 * @returns {Promise<{customers:[], transactions:[], reminder_log:[], settings:[], pulledAt:string}>}
 */
export async function pullChanges(since) {
  const url = since
    ? `${BASE}/pull?since=${encodeURIComponent(since)}`
    : `${BASE}/pull`;

  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`Pull failed: ${res.status} ${res.statusText}`);
  }
  return res.json();
}

/**
 * Delete ALL data from the cloud (matches "delete all data" button).
 */
export async function deleteAllRemote() {
  const res = await fetch(`${BASE}/delete-all`, { method: 'POST' });
  if (!res.ok) {
    throw new Error(`Delete-all failed: ${res.status} ${res.statusText}`);
  }
  return res.json();
}
