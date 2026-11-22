/**
 * Server-side Reminder Scheduler — Voice Khata
 *
 * Provides a /api/reminders/check endpoint that:
 *   1. Loads all customers from Supabase
 *   2. Checks who needs reminders
 *   3. Returns the list with wa.me deep-links
 *
 * Can be called manually from the app or by a cron job.
 */

import { getSupabase } from '../supabase/client.js';

export default async function reminderRoutes(fastify, { config }) {
  const env = config.env;

  /**
   * GET /api/reminders/check
   * Returns customers who need reminders with pre-built wa.me links.
   */
  fastify.get('/api/reminders/check', async (request, reply) => {
    const sb = getSupabase(env);
    if (!sb) return reply.code(503).send({ error: 'Cloud sync not configured' });

    const settings = config.settings;
    const templates = config.templates;
    const {
      reminder_first_after_days: firstAfter = 7,
      reminder_repeat_days: repeatEvery = 3,
      weekly_summary_days: weeklyEvery = 7,
    } = settings.reminders || {};
    const highDueLimit = settings.limits?.high_due_limit ?? 10000;

    // 1. Get all customers with due > 0 and opted in
    const { data: customers, error } = await sb
      .from('customers')
      .select('*')
      .gt('total_due', 0)
      .eq('opt_in_whatsapp', true)
      .not('due_since', 'is', null);

    if (error) {
      fastify.log.error(error, 'Failed to fetch customers');
      return reply.code(500).send({ error: 'Database error' });
    }

    if (!customers || customers.length === 0) {
      return { reminders: [], count: 0 };
    }

    const now = new Date();
    const reminders = [];

    for (const c of customers) {
      const dueSince = new Date(c.due_since);
      const overdueDays = Math.floor((now - dueSince) / (1000 * 60 * 60 * 24));

      if (overdueDays < firstAfter) continue;

      // Get last reminder for this customer
      const { data: lastLogs } = await sb
        .from('reminder_log')
        .select('*')
        .eq('customer_id', c.id)
        .order('sent_at', { ascending: false })
        .limit(1);

      const lastReminder = lastLogs?.[0];
      const daysSinceLast = lastReminder
        ? Math.floor((now - new Date(lastReminder.sent_at)) / (1000 * 60 * 60 * 24))
        : Infinity;

      // Determine reminder kind
      let kind = null;
      if (!lastReminder) {
        kind = 'first';
      } else if (daysSinceLast >= repeatEvery) {
        kind = 'repeat';
      }

      if (c.total_due > highDueLimit && daysSinceLast >= repeatEvery) {
        kind = 'high_due';
      }

      if (!kind) continue;

      const lang = c.preferred_language || settings.languages?.default || 'hi';
      const templateKey = kind === 'first' ? 'reminder_first'
        : kind === 'high_due' ? 'reminder_high_due'
        : 'reminder_repeat';

      let text = templates[lang]?.[templateKey] || templates['en']?.[templateKey] || '';
      text = text
        .replaceAll('{name}', c.name)
        .replaceAll('{total}', String(c.total_due))
        .replaceAll('{days}', String(overdueDays))
        .replaceAll('{shop}', settings.shop?.name || '');

      // Build wa.me link (phone is encrypted, so we can't use it here)
      // The client decrypts and builds the link
      reminders.push({
        customerId: c.id,
        customerName: c.name,
        totalDue: Number(c.total_due),
        overdueDays,
        kind,
        text,
        lang,
      });
    }

    return { reminders, count: reminders.length, checkedAt: now.toISOString() };
  });
}
