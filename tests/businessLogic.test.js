/**
 * Business Logic tests — Voice Khata Phase 4
 *
 * Tests for: template engine, WhatsApp sender, distance checker,
 * high-due check, reminder scheduler, business workflows.
 *
 * Run:  node --test tests/businessLogic.test.js
 */

import 'fake-indexeddb/auto';
import { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

// Template engine
import { fillTemplate, getTemplate } from '../client/src/utils/templateEngine.js';

// WhatsApp sender
import { buildWhatsAppLink } from '../client/src/logic/whatsappSender.js';

// Distance checker
import { haversineKm } from '../client/src/logic/distanceChecker.js';

// High-due check
import { checkHighDue, checkHighDueBeforeCredit } from '../client/src/logic/highDueCheck.js';

// Reminder scheduler
import { checkCustomerReminders, generateThankYou } from '../client/src/logic/reminderScheduler.js';

// Repos (for integration tests)
import db from '../client/src/db/schema.js';
import * as customerRepo from '../client/src/db/customerRepo.js';
import * as transactionRepo from '../client/src/db/transactionRepo.js';
import * as reminderLogRepo from '../client/src/db/reminderLogRepo.js';
import { generateUniqueCustomerId } from '../client/src/utils/customerIdGenerator.js';

// Business workflows
import { addCreditWorkflow, addPaymentWorkflow, queryTotalDue, addPurchaseWorkflow } from '../client/src/logic/businessRules.js';

// ── Shared fixtures ──

const SETTINGS = {
  shop: { name: 'Pavan Store' },
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
    distance_limit_km: 5,
  },
};

const TEMPLATES = {
  hi: {
    reminder_first: '{name} ji, aapka {currency}{total} baaki hai. - {shop}',
    reminder_repeat: '{name} ji, {currency}{total} abhi baaki hai. - {shop}',
    reminder_high_due: '{name} ji, aapka baaki {currency}{total} zyada hai. - {shop}',
    reminder_weekly_summary: '{name} ji, is hafte ka hisab: {currency}{total}. - {shop}',
    high_due_warning: '{name} ka due {currency}{total} limit se zyada hai!',
    high_due_confirm: 'Due {currency}{total} ho jayega. Continue?',
    credit_added: '{name}, {currency}{amount} udhaar. Total {currency}{total}.',
    payment_received: '{name}, {currency}{amount} mila. Baaki {currency}{total}.',
    due_cleared: '{name}, aapka hisab clear ho gaya!',
    thank_you: 'Dhanyavaad {name}! - {shop}',
    total_query: '{name} ka total baaki: {currency}{total}',
  },
  en: {
    reminder_first: '{name}, your due is {currency}{total}. - {shop}',
    thank_you: 'Thank you {name}! - {shop}',
    total_query: '{name} total due: {currency}{total}',
  },
};

async function clearAll() {
  await db.customers.clear();
  await db.transactions.clear();
  await db.reminder_log.clear();
  await db.outbox.clear();
  await db.settings.clear();
}

// ——————————————————— Template Engine ———————————————————

describe('Template Engine', () => {
  it('should replace placeholders', () => {
    const result = fillTemplate('Hello {name}, due is {total}', { name: 'Ramesh', total: 500 });
    assert.equal(result, 'Hello Ramesh, due is 500');
  });

  it('should handle missing vars gracefully', () => {
    const result = fillTemplate('Hello {name}', {});
    assert.equal(result, 'Hello {name}');
  });

  it('should handle empty template', () => {
    assert.equal(fillTemplate('', { name: 'X' }), '');
    assert.equal(fillTemplate(null, { name: 'X' }), '');
  });

  it('getTemplate should fallback to English', () => {
    const result = getTemplate(TEMPLATES, 'kn', 'thank_you', { name: 'Ram', shop: 'S' });
    assert.equal(result, 'Thank you Ram! - S');
  });

  it('getTemplate should use specified language when available', () => {
    const result = getTemplate(TEMPLATES, 'hi', 'thank_you', { name: 'Ram', shop: 'S' });
    assert.equal(result, 'Dhanyavaad Ram! - S');
  });
});

// ——————————————————— WhatsApp Sender ———————————————————

describe('WhatsApp Sender', () => {
  it('should build a valid wa.me link', () => {
    const url = buildWhatsAppLink('8317365720', 'Hello Ramesh');
    assert.equal(url, 'https://wa.me/918317365720?text=Hello%20Ramesh');
  });

  it('should not double-add country code', () => {
    const url = buildWhatsAppLink('918317365720', 'Hi');
    assert.equal(url, 'https://wa.me/918317365720?text=Hi');
  });

  it('should strip non-digits from phone', () => {
    const url = buildWhatsAppLink('+91-8317-365-720', 'Test');
    assert.equal(url, 'https://wa.me/918317365720?text=Test');
  });

  it('should encode special characters in text', () => {
    const url = buildWhatsAppLink('1234567890', '₹500 due & pending');
    assert.ok(url.includes('%E2%82%B9500'));
    assert.ok(url.includes('%26'));
  });
});

// ——————————————————— Distance Checker ———————————————————

describe('Distance Checker (haversineKm)', () => {
  it('should return 0 for same point', () => {
    const d = haversineKm(12.9, 77.5, 12.9, 77.5);
    assert.equal(d, 0);
  });

  it('should compute ~1.1 km for nearby Bangalore points', () => {
    // Kengeri to a point ~1 km away
    const d = haversineKm(12.9141, 77.4829, 12.9241, 77.4829);
    assert.ok(d > 0.9 && d < 1.3, `Expected ~1.1 km, got ${d}`);
  });

  it('should compute ~2200 km for Delhi to Bangalore', () => {
    const d = haversineKm(28.6139, 77.209, 12.9716, 77.5946);
    assert.ok(d > 1700 && d < 1800, `Expected ~1740 km, got ${d}`);
  });
});

// ——————————————————— High Due Check ———————————————————

describe('High Due Check', () => {
  const customer = { name: 'Ramesh', totalDue: 5000, preferredLanguage: 'hi' };
  const highCustomer = { name: 'Ramesh', totalDue: 15000, preferredLanguage: 'hi' };

  it('should return isHighDue: false when under limit', () => {
    const result = checkHighDue(customer, SETTINGS, TEMPLATES);
    assert.equal(result.isHighDue, false);
  });

  it('should return isHighDue: true when over limit', () => {
    const result = checkHighDue(highCustomer, SETTINGS, TEMPLATES);
    assert.equal(result.isHighDue, true);
    assert.ok(result.warningText.includes('15000'));
  });

  it('checkHighDueBeforeCredit: no confirm when under limit', () => {
    const result = checkHighDueBeforeCredit(customer, 2000, SETTINGS, TEMPLATES);
    assert.equal(result.needsConfirmation, false);
  });

  it('checkHighDueBeforeCredit: confirm when credit pushes over limit', () => {
    const result = checkHighDueBeforeCredit(customer, 6000, SETTINGS, TEMPLATES);
    assert.equal(result.needsConfirmation, true);
    assert.equal(result.projectedDue, 11000);
  });
});

// ——————————————————— Reminder Scheduler ———————————————————

describe('Reminder Scheduler', () => {
  it('should not remind non-opted-in customers', async () => {
    const customer = {
      id: 'test', name: 'Test', totalDue: 500, optInWhatsApp: false,
      dueSince: new Date(Date.now() - 10 * 86400000).toISOString(),
    };
    const actions = await checkCustomerReminders(customer, SETTINGS, TEMPLATES, reminderLogRepo);
    assert.equal(actions.length, 0);
  });

  it('should not remind customers with zero due', async () => {
    const customer = {
      id: 'test', name: 'Test', totalDue: 0, optInWhatsApp: true,
      dueSince: null,
    };
    const actions = await checkCustomerReminders(customer, SETTINGS, TEMPLATES, reminderLogRepo);
    assert.equal(actions.length, 0);
  });

  it('should not remind before first_after_days', async () => {
    const customer = {
      id: 'test', name: 'Test', totalDue: 500, optInWhatsApp: true,
      dueSince: new Date(Date.now() - 3 * 86400000).toISOString(), // 3 days ago
    };
    const actions = await checkCustomerReminders(customer, SETTINGS, TEMPLATES, reminderLogRepo);
    assert.equal(actions.length, 0);
  });

  it('should send first reminder after first_after_days', async () => {
    await clearAll();
    const customer = {
      id: 'test_first', name: 'Ramesh', totalDue: 500, optInWhatsApp: true,
      preferredLanguage: 'hi',
      dueSince: new Date(Date.now() - 10 * 86400000).toISOString(), // 10 days ago
    };
    const actions = await checkCustomerReminders(customer, SETTINGS, TEMPLATES, reminderLogRepo);
    assert.equal(actions.length, 1);
    assert.equal(actions[0].kind, 'first');
    assert.ok(actions[0].text.includes('Ramesh'));
    assert.ok(actions[0].text.includes('500'));
  });

  it('generateThankYou should produce correct text', () => {
    const customer = { name: 'Ramesh', preferredLanguage: 'hi' };
    const text = generateThankYou(customer, SETTINGS, TEMPLATES);
    assert.ok(text.includes('Dhanyavaad'));
    assert.ok(text.includes('Ramesh'));
    assert.ok(text.includes('Pavan Store'));
  });
});

// ——————————————————— Business Workflows ———————————————————

describe('Business Workflows', () => {
  let customerId;
  const deps = {
    transactionRepo,
    customerRepo,
    reminderLogRepo,
    settings: SETTINGS,
    templates: TEMPLATES,
    crypto: null,
  };

  beforeEach(async () => {
    await clearAll();
    const c = await customerRepo.createCustomer(
      { name: 'Ramesh', city: 'Kengeri', phone: '8317365720', optInWhatsApp: true },
      SETTINGS, null, generateUniqueCustomerId,
    );
    customerId = c.id;
  });

  it('addCreditWorkflow should add credit and return TTS text', async () => {
    const result = await addCreditWorkflow(
      { customerId, amount: 500 },
      deps,
    );
    assert.equal(result.success, true);
    assert.equal(result.dueState.totalDue, 500);
    assert.ok(result.ttsText.includes('500'));
    assert.ok(result.ttsText.includes('Ramesh'));
  });

  it('addCreditWorkflow should require confirmation when high due', async () => {
    // First, add a big credit to get close to the limit
    await transactionRepo.createTransaction({
      customerId, type: 'credit', amount: 9000,
    });

    const result = await addCreditWorkflow(
      { customerId, amount: 2000 },
      deps,
    );
    assert.equal(result.success, false);
    assert.equal(result.needsConfirmation, true);
    assert.equal(result.projectedDue, 11000);
  });

  it('addCreditWorkflow should proceed when confirmed', async () => {
    await transactionRepo.createTransaction({
      customerId, type: 'credit', amount: 9000,
    });

    const result = await addCreditWorkflow(
      { customerId, amount: 2000, confirmed: true },
      deps,
    );
    assert.equal(result.success, true);
    assert.equal(result.dueState.totalDue, 11000);
  });

  it('addPaymentWorkflow: partial payment', async () => {
    await transactionRepo.createTransaction({
      customerId, type: 'credit', amount: 1000,
    });

    const result = await addPaymentWorkflow(
      { customerId, amount: 300 },
      deps,
    );
    assert.equal(result.success, true);
    assert.equal(result.dueCleared, false);
    assert.equal(result.dueState.totalDue, 700);
    assert.ok(result.ttsText.includes('300'));
    assert.ok(result.ttsText.includes('700'));
  });

  it('addPaymentWorkflow: full payment clears due and generates thank-you', async () => {
    await transactionRepo.createTransaction({
      customerId, type: 'credit', amount: 500,
    });

    const result = await addPaymentWorkflow(
      { customerId, amount: 500 },
      deps,
    );
    assert.equal(result.success, true);
    assert.equal(result.dueCleared, true);
    assert.equal(result.dueState.totalDue, 0);
    assert.ok(result.ttsText.includes('clear'));
    assert.ok(result.thankYouText.includes('Dhanyavaad'));
  });

  it('queryTotalDue should return TTS text', async () => {
    await transactionRepo.createTransaction({
      customerId, type: 'credit', amount: 750,
    });

    const result = await queryTotalDue(customerId, deps);
    assert.equal(result.totalDue, 750);
    assert.ok(result.ttsText.includes('Ramesh'));
    assert.ok(result.ttsText.includes('750'));
  });

  it('addPurchaseWorkflow: records cash purchase with bill without modifying totalDue', async () => {
    // Initial state: customer has 500 due
    await transactionRepo.createTransaction({
      customerId, type: 'credit', amount: 500,
    });

    const result = await addPurchaseWorkflow(
      { customerId, amount: 60, item: '2 packets milk' },
      deps,
    );
    assert.equal(result.success, true);
    assert.equal(result.txn.type, 'purchase');
    assert.equal(result.txn.amount, 60);
    assert.ok(result.bill);
    assert.ok(result.bill.billNumber.startsWith('BILL-'));
    assert.equal(result.bill.total, 60);
    assert.equal(result.bill.paymentMode, 'cash');
    // totalDue must remain 500!
    assert.equal(result.dueState.totalDue, 500);

    // Verify running balance view also shows unaffected balance
    const running = await transactionRepo.getRunningBalance(customerId);
    const purchaseRow = running.find((r) => r.type === 'purchase');
    assert.ok(purchaseRow);
    assert.equal(purchaseRow.runningBalance, 500);
  });
});
