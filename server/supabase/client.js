/**
 * Supabase Client — Server-side only
 *
 * Uses the SERVICE_ROLE key which bypasses RLS.
 * Never import this in client code.
 */

import { createClient } from '@supabase/supabase-js';

let _client = null;

/**
 * Reset the Supabase client singleton (e.g. when credentials are updated).
 */
export function resetSupabase() {
  _client = null;
  console.log('[supabase] Client reset requested.');
}

/**
 * Get or create the Supabase client singleton.
 * Returns null if credentials are not configured.
 */
export function getSupabase(env) {
  if (_client) return _client;

  const url = env.SUPABASE_URL;
  const key = env.SUPABASE_SERVICE_ROLE_KEY;

  if (!url || !key) {
    console.warn('[supabase] URL or service-role key missing — cloud sync disabled.');
    return null;
  }

  _client = createClient(url, key, {
    auth: {
      persistSession: false,
      autoRefreshToken: false,
    },
  });

  console.log('[supabase] Client initialised for:', url);
  return _client;
}
