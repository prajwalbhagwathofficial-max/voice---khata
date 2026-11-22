/**
 * Sync Routes — /api/sync/*
 *
 * Bridges the client outbox with the Supabase cloud tables.
 * All Supabase operations use the service_role key (server-side only).
 *
 * Endpoints:
 *   POST /api/sync/push       – push outbox entries to cloud
 *   GET  /api/sync/pull       – pull changes since a timestamp
 *   POST /api/sync/delete-all – wipe all cloud data
 */

import { getSupabase } from '../supabase/client.js';

export const memoryStore = {
  transactions: new Map(),
  customers: new Map(),
};

// ——— camelCase ↔ snake_case conversion ———

const CAMEL_TO_SNAKE = {
  phoneEnc: 'phone_enc', addressEnc: 'address_enc',
  optInWhatsApp: 'opt_in_whatsapp', preferredLanguage: 'preferred_language',
  totalDue: 'total_due', dueSince: 'due_since',
  lastPaymentAt: 'last_payment_at', lastReminderAt: 'last_reminder_at',
  lastCreditAt: 'last_credit_at', createdAt: 'created_at', updatedAt: 'updated_at',
  customerId: 'customer_id', voiceTranscript: 'voice_transcript',
  reversesTxnId: 'reverses_txn_id', sentAt: 'sent_at', textSent: 'text_sent',
};

function toSnake(obj) {
  if (!obj || typeof obj !== 'object') return obj;
  const out = {};
  for (const [k, v] of Object.entries(obj)) {
    out[CAMEL_TO_SNAKE[k] || k] = v;
  }
  return out;
}

function toCamel(obj) {
  if (!obj || typeof obj !== 'object') return obj;
  const snakeToCamel = Object.fromEntries(
    Object.entries(CAMEL_TO_SNAKE).map(([c, s]) => [s, c]),
  );
  const out = {};
  for (const [k, v] of Object.entries(obj)) {
    out[snakeToCamel[k] || k] = v;
  }
  return out;
}

const ENTITY_TABLE = {
  customer: 'customers',
  transaction: 'transactions',
  reminder_log: 'reminder_log',
  settings: 'settings',
};

/**
 * Register sync routes on the Fastify instance.
 */
export default async function syncRoutes(fastify, { config }) {
  const env = config.env;

  // ————————————————— POST /api/sync/push —————————————————
  fastify.post('/api/sync/push', async (request, reply) => {
    const sb = getSupabase(env);
    if (!sb) return reply.code(503).send({ error: 'Cloud sync not configured' });

    const { entries } = request.body || {};
    if (!Array.isArray(entries) || entries.length === 0) {
      return { synced: [], failed: [] };
    }

    const synced = [];
    const failed = [];

    for (const entry of entries) {
      try {
        const table = ENTITY_TABLE[entry.entity];
        if (!table) throw new Error(`Unknown entity: ${entry.entity}`);

        const payload = typeof entry.payload === 'string'
          ? JSON.parse(entry.payload)
          : entry.payload;

        if (entry.entity === 'transaction' && payload?.id) {
          memoryStore.transactions.set(payload.id, { ...payload, syncedAt: new Date().toISOString() });
        } else if (entry.entity === 'customer' && payload?.id) {
          memoryStore.customers.set(payload.id, { ...payload, syncedAt: new Date().toISOString() });
        }

        if (entry.op === 'delete') {
          const pkCol = table === 'settings' ? 'key' : 'id';
          const pkVal = entry.entityId || entry.entity_id || (typeof payload === 'object' ? payload?.id : payload);
          const { error } = await sb.from(table).delete().eq(pkCol, pkVal);
          if (error) throw error;
        } else {
          // create or update → upsert
          const row = toSnake(payload);
          // Remove internal fields not in the cloud schema
          delete row.phone;
          delete row.address;
          delete row.readable_id;
          delete row.readableId;
          const { error } = await sb.from(table).upsert(row, { onConflict: table === 'settings' ? 'key' : 'id' });
          if (error) throw error;
        }

        synced.push(entry.id);
      } catch (err) {
        fastify.log.error({ entry: entry.id, error: err.message }, 'Sync push failed');
        failed.push({ id: entry.id, error: err.message });
      }
    }

    return { synced, failed };
  });

  // ————————————————— GET /api/sync/pull —————————————————
  fastify.get('/api/sync/pull', async (request, reply) => {
    const sb = getSupabase(env);
    if (!sb) return reply.code(503).send({ error: 'Cloud sync not configured' });

    const since = request.query.since || '1970-01-01T00:00:00Z';

    const results = {};
    let schemaMissingNotified = false;
    const handleErr = (err, name) => {
      if (err.code === 'PGRST205') {
        if (!schemaMissingNotified) {
          fastify.log.warn('Supabase tables not yet created (PGRST205). Run migrations/001_initial_schema.sql in Supabase SQL Editor to enable cloud backup.');
          schemaMissingNotified = true;
        }
      } else if (err.code === '42501') {
        if (!schemaMissingNotified) {
          fastify.log.warn('Supabase permissions missing (42501). Run: GRANT ALL ON ALL TABLES IN SCHEMA public TO service_role; in Supabase SQL Editor.');
          schemaMissingNotified = true;
        }
      } else {
        fastify.log.error(err, `Pull ${name} failed`);
      }
    };

    // Customers
    const { data: customers, error: ce } = await sb
      .from('customers').select('*').gte('updated_at', since);
    if (ce) handleErr(ce, 'customers');
    results.customers = (customers || []).map(toCamel);

    // Transactions
    const { data: txns, error: te } = await sb
      .from('transactions').select('*').gte('created_at', since);
    if (te) handleErr(te, 'transactions');
    results.transactions = (txns || []).map(toCamel);

    // Reminder log
    const { data: logs, error: le } = await sb
      .from('reminder_log').select('*').gte('created_at', since);
    if (le) handleErr(le, 'reminder_log');
    results.reminder_log = (logs || []).map(toCamel);

    // Settings
    const { data: settings, error: se } = await sb
      .from('settings').select('*').gte('updated_at', since);
    if (se) handleErr(se, 'settings');
    results.settings = (settings || []).map(toCamel);

    results.pulledAt = new Date().toISOString();
    return results;
  });

  // ————————————————— POST /api/sync/delete-all —————————————————
  fastify.post('/api/sync/delete-all', async (request, reply) => {
    const sb = getSupabase(env);
    if (!sb) return reply.code(503).send({ error: 'Cloud sync not configured' });

    // Order matters: children before parents (FK constraints)
    const tables = ['reminder_log', 'transactions', 'customers', 'settings'];
    for (const table of tables) {
      const { error } = await sb.from(table).delete().neq('id', '__never__');
      if (error) {
        // settings uses 'key' not 'id'
        if (table === 'settings') {
          await sb.from(table).delete().neq('key', '__never__');
        } else {
          fastify.log.error(error, `delete-all ${table} failed`);
        }
      }
    }

    return { deleted: true, timestamp: new Date().toISOString() };
  });
}
