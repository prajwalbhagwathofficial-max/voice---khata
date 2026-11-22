/**
 * Phase 8 Tests — WhatsApp Reminders & Scheduler
 *
 * Tests:
 * 1. wa.me deep-link URL construction & URL encoding
 * 2. 7-day initial overdue threshold rule
 * 3. 3-day repeat reminder anti-spam cooldown rule
 * 4. Opt-out safety enforcement (optInWhatsApp = false)
 * 5. High-due limit reminder prioritization
 * 6. Audit logging in reminder_log repository
 * 7. Multi-lingual message rendering (hi, kn, en)
 */

import 'fake-indexeddb/auto';
import { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import db from '../client/src/db/schema.js';
import * as customerRepo from '../client/src/db/customerRepo.js';
import * as reminderLogRepo from '../client/src/db/reminderLogRepo.js';
import { generateUniqueCustomerId } from '../client/src/utils/customerIdGenerator.js';
import { buildWhatsAppLink, sendReminder } from '../client/src/logic/whatsappSender.js';
import {
  checkCustomerReminders,
  runReminderCheck,
  generateThankYou,
} from '../client/src/logic/reminderScheduler.js';

// Load templates
const hiTpl = JSON.parse(readFileSync('config/templates/hi.json', 'utf8'));
const knTpl = JSON.parse(readFileSync('config/templates/kn.json', 'utf8'));
const enTpl = JSON.parse(readFileSync('config/templates/en.json', 'utf8'));
const templates = { hi: hiTpl, kn: knTpl, en: enTpl };

const SETTINGS = {
  shop: { name: 'Pavan Kirana' },
  display: { currency_symbol: '₹' },
  languages: { default: 'hi' },
  customer_id: { phone_digits_in_id: 6, separator: '_' },
  reminders: {
    reminder_first_after_days: 7,
    reminder_repeat_days: 3,
    weekly_summary_days: 7,
  },
  limits: {
    high_due_limit: 10000,
  },
};

const noCrypto = null;

async function clearDb() {
  await db.customers.clear();
  await db.transactions.clear();
  await db.reminder_log.clear();
  await db.outbox.clear();
}

// ─── WhatsApp Link Builder ──────────────────────────────────

describe('WhatsApp Link Builder (wa.me)', () => {
  it('should format standard 10-digit Indian numbers with 91 prefix', () => {
    const url = buildWhatsAppLink('9876543210', 'नमस्ते रमेश जी');
    assert.ok(url.startsWith('https://wa.me/919876543210?text='));
    assert.ok(url.includes('%E0%A4%A8%E0%A4%AE%E0%A4%B8%E0%A5%8D%E0%A4%A4%E0%A5%87'));
  });

  it('should not duplicate 91 if already provided', () => {
    const url = buildWhatsAppLink('919876543210', 'Hello');
    assert.ok(url.startsWith('https://wa.me/919876543210?text='));
  });

  it('should clean dashes, spaces, and brackets', () => {
    const url = buildWhatsAppLink('+91 (98765) 43-210', 'Hello');
    assert.ok(url.startsWith('https://wa.me/919876543210?text='));
  });

  it('sendReminder interface should return url', () => {
    const result = sendReminder({ phone: '9988776655', text: 'Test message', autoOpen: false });
    assert.equal(typeof result.url, 'string');
    assert.equal(result.opened, false);
  });
});

// ─── Reminder Rules & Anti-Spam Safety ─────────────────────

describe('Reminder Scheduler Rules & Safety', () => {
  beforeEach(async () => {
    await clearDb();
  });

  it('should NOT generate reminder for customer with 0 due', async () => {
    const cust = {
      id: 'cust_01',
      name: 'Ramesh',
      phone: '9876543210',
      totalDue: 0,
      optInWhatsApp: true,
      dueSince: new Date(Date.now() - 10 * 86400000).toISOString(),
    };
    const actions = await checkCustomerReminders(cust, SETTINGS, templates, reminderLogRepo);
    assert.equal(actions.length, 0);
  });

  it('should NOT generate reminder if customer opted OUT of WhatsApp', async () => {
    const cust = {
      id: 'cust_02',
      name: 'Suresh',
      phone: '9876543210',
      totalDue: 1500,
      optInWhatsApp: false, // Opted out
      dueSince: new Date(Date.now() - 15 * 86400000).toISOString(),
    };
    const actions = await checkCustomerReminders(cust, SETTINGS, templates, reminderLogRepo);
    assert.equal(actions.length, 0);
  });

  it('should NOT generate reminder if overdue is less than first_after_days (7 days)', async () => {
    const cust = {
      id: 'cust_03',
      name: 'Dinesh',
      phone: '9876543210',
      totalDue: 800,
      optInWhatsApp: true,
      dueSince: new Date(Date.now() - 4 * 86400000).toISOString(), // 4 days ago
    };
    const actions = await checkCustomerReminders(cust, SETTINGS, templates, reminderLogRepo);
    assert.equal(actions.length, 0);
  });

  it('should generate "first" reminder when overdue >= 7 days with no prior log', async () => {
    const cust = {
      id: 'cust_04',
      name: 'Ganesh',
      phone: '9876543210',
      totalDue: 1200,
      optInWhatsApp: true,
      dueSince: new Date(Date.now() - 9 * 86400000).toISOString(), // 9 days ago
    };
    const actions = await checkCustomerReminders(cust, SETTINGS, templates, reminderLogRepo);
    assert.equal(actions.length, 1);
    assert.equal(actions[0].kind, 'first');
    assert.ok(actions[0].text.includes('Ganesh'));
    assert.ok(actions[0].text.includes('1200'));
    assert.ok(actions[0].text.includes('Pavan Kirana'));
  });

  it('should enforce 3-day cooldown after first reminder', async () => {
    const cust = {
      id: 'cust_05',
      name: 'Mahesh',
      phone: '9876543210',
      totalDue: 2000,
      optInWhatsApp: true,
      dueSince: new Date(Date.now() - 12 * 86400000).toISOString(),
    };

    // Log a "first" reminder sent 1 day ago
    await reminderLogRepo.logReminder({
      customerId: cust.id,
      channel: 'whatsapp',
      kind: 'first',
      sentAt: new Date(Date.now() - 1 * 86400000).toISOString(),
      textSent: 'Reminder 1',
    });

    // Only 1 day passed, repeatEvery is 3 days -> NO reminder
    const actions = await checkCustomerReminders(cust, SETTINGS, templates, reminderLogRepo);
    assert.equal(actions.length, 0);
  });

  it('should trigger "repeat" reminder after 3 days have elapsed since last send', async () => {
    const cust = {
      id: 'cust_06',
      name: 'Raju',
      phone: '9876543210',
      totalDue: 2500,
      optInWhatsApp: true,
      dueSince: new Date(Date.now() - 15 * 86400000).toISOString(),
    };

    // Log a reminder sent 4 days ago
    await reminderLogRepo.logReminder({
      customerId: cust.id,
      channel: 'whatsapp',
      kind: 'first',
      sentAt: new Date(Date.now() - 4 * 86400000).toISOString(),
      textSent: 'First notice',
    });

    // 4 days >= 3 days -> triggers repeat
    const actions = await checkCustomerReminders(cust, SETTINGS, templates, reminderLogRepo);
    assert.ok(actions.length >= 1);
    const repeatAction = actions.find((a) => a.kind === 'repeat');
    assert.ok(repeatAction);
    assert.ok(repeatAction.text.includes('Raju'));
  });

  it('should trigger "high_due" reminder if customer balance exceeds threshold', async () => {
    const cust = {
      id: 'cust_07',
      name: 'Vikram',
      phone: '9876543210',
      totalDue: 15000, // Exceeds 10,000 limit
      optInWhatsApp: true,
      dueSince: new Date(Date.now() - 14 * 86400000).toISOString(),
    };

    // Log first reminder sent 5 days ago
    await reminderLogRepo.logReminder({
      customerId: cust.id,
      channel: 'whatsapp',
      kind: 'first',
      sentAt: new Date(Date.now() - 5 * 86400000).toISOString(),
      textSent: 'First',
    });

    const actions = await checkCustomerReminders(cust, SETTINGS, templates, reminderLogRepo);
    const highDueAction = actions.find((a) => a.kind === 'high_due');
    assert.ok(highDueAction);
    assert.ok(highDueAction.text.includes('15000'));
  });
});

// ─── Multi-Lingual Template Rendering ───────────────────────

describe('Multi-lingual Reminder Templates', () => {
  it('should render Hindi reminder template with shop and name', () => {
    const cust = { name: 'मुकेश', preferredLanguage: 'hi', totalDue: 1800, dueSince: '2026-11-01T00:00:00.000Z' };
    const thankYou = generateThankYou(cust, SETTINGS, templates);
    assert.ok(thankYou.includes('मुकेश'));
    assert.ok(thankYou.includes('Pavan Kirana'));
  });

  it('should render Kannada reminder template with shop and name', () => {
    const cust = { name: 'ಮಂಜುನಾಥ', preferredLanguage: 'kn', totalDue: 950 };
    const thankYou = generateThankYou(cust, SETTINGS, templates);
    assert.ok(thankYou.includes('ಮಂಜುನಾಥ'));
    assert.ok(thankYou.includes('Pavan Kirana'));
  });

  it('should render English reminder template with shop and name', () => {
    const cust = { name: 'John', preferredLanguage: 'en', totalDue: 500 };
    const thankYou = generateThankYou(cust, SETTINGS, templates);
    assert.ok(thankYou.includes('John'));
    assert.ok(thankYou.includes('Pavan Kirana'));
  });
});

// ─── Audit Logging & Deduplication ──────────────────────────

describe('Reminder Audit Logging (reminder_log table)', () => {
  beforeEach(async () => {
    await clearDb();
  });

  it('should record reminder entry with uuid and timestamp in Dexie', async () => {
    const entry = await reminderLogRepo.logReminder({
      customerId: 'cust_99',
      channel: 'whatsapp',
      kind: 'first',
      textSent: 'Namaste',
      status: 'sent',
    });

    assert.ok(entry.id);
    assert.equal(entry.customerId, 'cust_99');
    assert.equal(entry.status, 'sent');

    const logs = await reminderLogRepo.getByCustomer('cust_99');
    assert.equal(logs.length, 1);
    assert.equal(logs[0].kind, 'first');
  });

  it('should accurately fetch last reminder of a specific kind', async () => {
    await reminderLogRepo.logReminder({
      customerId: 'cust_88',
      channel: 'whatsapp',
      kind: 'first',
      sentAt: '2026-11-01T10:00:00.000Z',
    });
    await reminderLogRepo.logReminder({
      customerId: 'cust_88',
      channel: 'whatsapp',
      kind: 'repeat',
      sentAt: '2026-11-05T10:00:00.000Z',
    });

    const lastFirst = await reminderLogRepo.getLastReminderOfKind('cust_88', 'first');
    assert.equal(lastFirst.kind, 'first');
    assert.equal(lastFirst.sentAt, '2026-11-01T10:00:00.000Z');

    const lastOverall = await reminderLogRepo.getLastReminder('cust_88');
    assert.equal(lastOverall.kind, 'repeat');
  });
});
