/**
 * Reminder Scheduler — Voice Khata
 *
 * Checks which customers need reminders and generates the
 * appropriate messages from templates.  Runs on app open
 * and periodically while the app is active.
 *
 * Reminder types (from settings):
 *   first   – due older than reminder_first_after_days (default 7)
 *   repeat  – still unpaid, every reminder_repeat_days (default 3)
 *   weekly  – customer took more credit while due, every weekly_summary_days (7)
 *   high_due – totalDue > high_due_limit
 *   thank_you – when due is fully cleared
 *
 * Only opted-in customers. Polite templates. Every send is logged.
 */

import { getTemplate } from '../utils/templateEngine.js';

/**
 * Calculate the number of whole days between two ISO date strings.
 */
function daysBetween(isoA, isoB) {
  const a = new Date(isoA);
  const b = new Date(isoB || new Date().toISOString());
  return Math.floor(Math.abs(b - a) / (1000 * 60 * 60 * 24));
}

/**
 * Check a single customer and return an array of reminder actions.
 *
 * @param {object} customer
 * @param {object} settings       – merged app settings
 * @param {object} templates      – all language templates { hi:{...}, kn:{...}, en:{...} }
 * @param {object} reminderLogRepo – the reminderLog repository
 * @returns {Promise<Array<{kind:string, text:string, customer:object}>>}
 */
export async function checkCustomerReminders(customer, settings, templates, reminderLogRepo) {
  const actions = [];

  // Skip: no due, not opted in, or no dueSince
  if (customer.totalDue <= 0 || !customer.optInWhatsApp || !customer.dueSince) {
    return actions;
  }

  const now = new Date().toISOString();
  const overdueDays = daysBetween(customer.dueSince, now);
  const lang = customer.preferredLanguage || settings?.languages?.default || 'hi';
  const shopName = settings?.shop?.name || '';
  const currency = settings?.display?.currency_symbol || '₹';

  const {
    reminder_first_after_days: firstAfter = 7,
    reminder_repeat_days: repeatEvery = 3,
    weekly_summary_days: weeklyEvery = 7,
  } = settings?.reminders || {};

  const highDueLimit = settings?.limits?.high_due_limit ?? 10000;

  const vars = {
    name: customer.name,
    total: customer.totalDue,
    days: overdueDays,
    shop: shopName,
    currency,
  };

  // Not overdue enough for any reminder yet
  if (overdueDays < firstAfter) return actions;

  // Get the most recent reminder of any kind
  const lastReminder = await reminderLogRepo.getLastReminder(customer.id);
  const daysSinceLastReminder = lastReminder
    ? daysBetween(lastReminder.sentAt, now)
    : Infinity;

  // ─── First reminder (never sent before) ───
  const lastFirst = await reminderLogRepo.getLastReminderOfKind(customer.id, 'first');
  if (!lastFirst) {
    actions.push({
      kind: 'first',
      text: getTemplate(templates, lang, 'reminder_first', vars),
      customer,
    });
    return actions; // One reminder per cycle
  }

  // ─── Repeat reminder ───
  if (daysSinceLastReminder >= repeatEvery) {
    actions.push({
      kind: 'repeat',
      text: getTemplate(templates, lang, 'reminder_repeat', vars),
      customer,
    });
  }

  // ─── Weekly summary (customer took more credit while having due) ───
  if (customer.lastCreditAt && customer.lastCreditAt > customer.dueSince) {
    const lastWeekly = await reminderLogRepo.getLastReminderOfKind(customer.id, 'weekly');
    const daysSinceWeekly = lastWeekly ? daysBetween(lastWeekly.sentAt, now) : Infinity;

    if (daysSinceWeekly >= weeklyEvery) {
      actions.push({
        kind: 'weekly',
        text: getTemplate(templates, lang, 'reminder_weekly_summary', vars),
        customer,
      });
    }
  }

  // ─── High-due notice ───
  if (customer.totalDue > highDueLimit) {
    const lastHighDue = await reminderLogRepo.getLastReminderOfKind(customer.id, 'high_due');
    const daysSinceHighDue = lastHighDue ? daysBetween(lastHighDue.sentAt, now) : Infinity;

    if (daysSinceHighDue >= repeatEvery) {
      actions.push({
        kind: 'high_due',
        text: getTemplate(templates, lang, 'reminder_high_due', vars),
        customer,
      });
    }
  }

  return actions;
}

/**
 * Run the reminder check for ALL opted-in customers.
 *
 * @param {object} customerRepo
 * @param {object} reminderLogRepo
 * @param {object} settings
 * @param {object} templates
 * @returns {Promise<Array>} All pending reminder actions across all customers
 */
export async function runReminderCheck(customerRepo, reminderLogRepo, settings, templates) {
  const allCustomers = await customerRepo.getAllCustomers(null); // null crypto for phone
  const allActions = [];

  for (const customer of allCustomers) {
    const actions = await checkCustomerReminders(customer, settings, templates, reminderLogRepo);
    allActions.push(...actions);
  }

  return allActions;
}

/**
 * Generate a thank-you message for a customer who just cleared their due.
 *
 * @param {object} customer
 * @param {object} settings
 * @param {object} templates
 * @returns {string} The thank-you message
 */
export function generateThankYou(customer, settings, templates) {
  const lang = customer.preferredLanguage || settings?.languages?.default || 'hi';
  return getTemplate(templates, lang, 'thank_you', {
    name: customer.name,
    shop: settings?.shop?.name || '',
  });
}
