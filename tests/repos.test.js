/**
 * Integration tests for the IndexedDB repositories.
 *
 * Uses fake-indexeddb to run Dexie in Node.js.
 *
 * Run:  node --test tests/repos.test.js
 */

import 'fake-indexeddb/auto';
import { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

// Must import db AFTER fake-indexeddb/auto so Dexie finds indexedDB
import db from '../client/src/db/schema.js';
import * as customerRepo from '../client/src/db/customerRepo.js';
import * as transactionRepo from '../client/src/db/transactionRepo.js';
import * as settingsRepo from '../client/src/db/settingsRepo.js';
import * as outboxRepo from '../client/src/db/outboxRepo.js';
import * as reminderLogRepo from '../client/src/db/reminderLogRepo.js';
import { generateUniqueCustomerId } from '../client/src/utils/customerIdGenerator.js';

const SETTINGS = {
  customer_id: { phone_digits_in_id: 6, separator: '_' },
  languages: { default: 'hi' },
};

// No encryption for repo tests
const noCrypto = null;

async function clearAll() {
  await db.customers.clear();
  await db.transactions.clear();
  await db.reminder_log.clear();
  await db.outbox.clear();
  await db.settings.clear();
}

// ——————————————————— Customer Repo ———————————————————

describe('CustomerRepo', () => {
  beforeEach(async () => { await clearAll(); });

  it('should create a customer with the correct ID format', async () => {
    const c = await customerRepo.createCustomer(
      { name: 'Ramesh', city: 'Kengeri', phone: '8317365720' },
      SETTINGS, noCrypto, generateUniqueCustomerId,
    );
    assert.equal(c.id, 'ramesh_kengeri_831736');
    assert.equal(c.name, 'Ramesh');
    assert.equal(c.totalDue, 0);
    assert.equal(c.dueSince, null);
  });

  it('should handle ID collision by appending _2', async () => {
    await customerRepo.createCustomer(
      { name: 'Ramesh', city: 'Kengeri', phone: '8317365720' },
      SETTINGS, noCrypto, generateUniqueCustomerId,
    );
    const c2 = await customerRepo.createCustomer(
      { name: 'Ramesh', city: 'Kengeri', phone: '8317365720' },
      SETTINGS, noCrypto, generateUniqueCustomerId,
    );
    assert.equal(c2.id, 'ramesh_kengeri_831736_2');
  });

  it('should search customers by partial name (case-insensitive)', async () => {
    await customerRepo.createCustomer(
      { name: 'Ramesh Kumar', city: 'Kengeri', phone: '1111111111' },
      SETTINGS, noCrypto, generateUniqueCustomerId,
    );
    await customerRepo.createCustomer(
      { name: 'Suresh', city: 'Jayanagar', phone: '2222222222' },
      SETTINGS, noCrypto, generateUniqueCustomerId,
    );

    const matches = await customerRepo.searchCustomers('ramesh', noCrypto);
    assert.equal(matches.length, 1);
    assert.equal(matches[0].name, 'Ramesh Kumar');
  });

  it('should return all customers', async () => {
    await customerRepo.createCustomer(
      { name: 'A', city: 'X', phone: '1111111111' },
      SETTINGS, noCrypto, generateUniqueCustomerId,
    );
    await customerRepo.createCustomer(
      { name: 'B', city: 'Y', phone: '2222222222' },
      SETTINGS, noCrypto, generateUniqueCustomerId,
    );
    const all = await customerRepo.getAllCustomers(noCrypto);
    assert.equal(all.length, 2);
  });

  it('should add an outbox entry on create', async () => {
    await customerRepo.createCustomer(
      { name: 'Test', city: 'City', phone: '3333333333' },
      SETTINGS, noCrypto, generateUniqueCustomerId,
    );
    const count = await outboxRepo.getOutboxCount();
    assert.equal(count, 1);
  });
});

// ——————————————————— Transaction Repo ———————————————————

describe('TransactionRepo', () => {
  let customerId;

  beforeEach(async () => {
    await clearAll();
    const c = await customerRepo.createCustomer(
      { name: 'Ramesh', city: 'Kengeri', phone: '8317365720' },
      SETTINGS, noCrypto, generateUniqueCustomerId,
    );
    customerId = c.id;
  });

  it('should add credit and update totalDue', async () => {
    const { txn, dueState } = await transactionRepo.createTransaction({
      customerId, type: 'credit', amount: 340, source: 'voice',
    });

    assert.equal(txn.type, 'credit');
    assert.equal(txn.amount, 340);
    assert.equal(dueState.totalDue, 340);
    assert.ok(dueState.dueSince, 'dueSince should be set after first credit');

    const customer = await customerRepo.getCustomerById(customerId, noCrypto);
    assert.equal(customer.totalDue, 340);
  });

  it('acceptance: credit 340, payment 200 yesterday → due = 140', async () => {
    // Credit 340 today
    await transactionRepo.createTransaction({
      customerId, type: 'credit', amount: 340, source: 'voice',
    });

    // Payment 200 yesterday
    const yesterday = new Date();
    yesterday.setDate(yesterday.getDate() - 1);
    const { dueState } = await transactionRepo.createTransaction({
      customerId, type: 'payment', amount: 200,
      timestamp: yesterday.toISOString(), source: 'voice',
    });

    assert.equal(dueState.totalDue, 140, 'Due should be 340 - 200 = 140');
    assert.ok(dueState.dueSince, 'dueSince should be set (partial payment restarts counter)');
  });

  it('should clear due on full payment', async () => {
    await transactionRepo.createTransaction({
      customerId, type: 'credit', amount: 500,
    });
    const { dueState } = await transactionRepo.createTransaction({
      customerId, type: 'payment', amount: 500,
    });

    assert.equal(dueState.totalDue, 0);
    assert.equal(dueState.dueSince, null, 'dueSince should be null after full payment');
  });

  it('partial payment should RESTART the counter', async () => {
    const { dueState: d1 } = await transactionRepo.createTransaction({
      customerId, type: 'credit', amount: 1000,
    });
    const originalDueSince = d1.dueSince;

    await new Promise((r) => setTimeout(r, 15));

    const { dueState: d2 } = await transactionRepo.createTransaction({
      customerId, type: 'payment', amount: 300,
    });

    assert.equal(d2.totalDue, 700);
    assert.notEqual(d2.dueSince, originalDueSince, 'Partial payment should restart the counter');
  });

  it('new credit while due exists should NOT reset dueSince', async () => {
    const { dueState: d1 } = await transactionRepo.createTransaction({
      customerId, type: 'credit', amount: 500,
    });
    const originalDueSince = d1.dueSince;

    await new Promise((r) => setTimeout(r, 15));

    const { dueState: d2 } = await transactionRepo.createTransaction({
      customerId, type: 'credit', amount: 200,
    });

    assert.equal(d2.totalDue, 700);
    assert.equal(d2.dueSince, originalDueSince, 'Adding more credit should keep the original dueSince');
  });

  it('should reverse a transaction', async () => {
    const { txn } = await transactionRepo.createTransaction({
      customerId, type: 'credit', amount: 500,
    });

    const { dueState } = await transactionRepo.reverseTransaction(txn.id, 'mistake');
    assert.equal(dueState.totalDue, 0, 'Reversing a credit should clear the due');
  });

  it('should compute running balance', async () => {
    const rbCust = await customerRepo.createCustomer(
      { name: 'RunningBalTest', city: 'Kengeri', phone: '9999999999' },
      SETTINGS, noCrypto, generateUniqueCustomerId,
    );
    const rbId = rbCust.id;
    const t1 = new Date(2026, 0, 1, 10, 0, 0).toISOString();
    const t2 = new Date(2026, 0, 1, 10, 5, 0).toISOString();
    const t3 = new Date(2026, 0, 1, 10, 10, 0).toISOString();

    await transactionRepo.createTransaction({ customerId: rbId, type: 'credit', amount: 500, timestamp: t1 });
    await transactionRepo.createTransaction({ customerId: rbId, type: 'payment', amount: 200, timestamp: t2 });
    await transactionRepo.createTransaction({ customerId: rbId, type: 'credit', amount: 100, timestamp: t3 });

    const ledger = await transactionRepo.getRunningBalance(rbId);
    assert.equal(ledger.length, 3);
    assert.equal(ledger[0].runningBalance, 500);
    assert.equal(ledger[1].runningBalance, 300);
    assert.equal(ledger[2].runningBalance, 400);
  });
});

// ——————————————————— Settings Repo ———————————————————

describe('SettingsRepo', () => {
  beforeEach(async () => { await clearAll(); });

  it('should set and get a setting', async () => {
    await settingsRepo.setSetting('high_due_limit', 15000);
    const val = await settingsRepo.getSetting('high_due_limit');
    assert.equal(val, 15000);
  });

  it('should return undefined for missing keys', async () => {
    const val = await settingsRepo.getSetting('nonexistent');
    assert.equal(val, undefined);
  });

  it('should merge overrides onto defaults', async () => {
    await settingsRepo.setSetting('limits.high_due_limit', 20000);
    const defaults = { limits: { high_due_limit: 10000, distance_limit_km: 5 } };
    const merged = await settingsRepo.getSettingsMerged(defaults);
    assert.equal(merged.limits.high_due_limit, 20000, 'Override should win');
    assert.equal(merged.limits.distance_limit_km, 5, 'Default should be kept');
  });
});

// ——————————————————— Outbox Repo ———————————————————

describe('OutboxRepo', () => {
  beforeEach(async () => { await clearAll(); });

  it('should track pending count across operations', async () => {
    assert.equal(await outboxRepo.getOutboxCount(), 0);

    await outboxRepo.addToOutbox('customer', 'c1', 'create', { name: 'test' });
    assert.equal(await outboxRepo.getOutboxCount(), 1);

    const pending = await outboxRepo.getPending();
    assert.equal(pending.length, 1);
    assert.equal(pending[0].entity, 'customer');

    await outboxRepo.removeDone(pending[0].id);
    assert.equal(await outboxRepo.getOutboxCount(), 0);
  });
});

// ——————————————————— Reminder Log Repo ———————————————————

describe('ReminderLogRepo', () => {
  beforeEach(async () => { await clearAll(); });

  it('should log and retrieve reminders', async () => {
    const entry = await reminderLogRepo.logReminder({
      customerId: 'ramesh_kengeri_831736',
      kind: 'first',
      channel: 'whatsapp',
      textSent: 'Please pay',
    });

    assert.ok(entry.id);
    assert.equal(entry.kind, 'first');

    const last = await reminderLogRepo.getLastReminder('ramesh_kengeri_831736');
    assert.equal(last.id, entry.id);
  });
});
