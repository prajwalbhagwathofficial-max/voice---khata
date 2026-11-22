/**
 * Transaction Repository
 *
 * Append-only ledger of credit, payment, and adjustment entries.
 * Every mutation is a NEW row — edits and removals create reversing
 * transactions, never silent overwrites.
 *
 * After creating a transaction this repo also updates the customer's
 * running totals (totalDue, dueSince, lastCreditAt, lastPaymentAt)
 * via customerRepo.updateCustomerDue().
 */

import { v4 as uuidv4 } from 'uuid';
import db from './schema.js';
import { addToOutbox } from './outboxRepo.js';
import { updateCustomerDue } from './customerRepo.js';

// ─────────────────── helpers ───────────────────

/** Recalculate totalDue from all transactions for a customer. */
async function recalcDue(customerId) {
  const txns = await db.transactions
    .where('customerId')
    .equals(customerId)
    .toArray();

  let totalDue = 0;
  let earliestUnpaidCredit = null;
  let lastPaymentAt = null;
  let lastCreditAt = null;

  // Sort chronologically
  txns.sort((a, b) => a.timestamp.localeCompare(b.timestamp));

  // Walk through the ledger and derive the state
  let runningDue = 0;
  for (const txn of txns) {
    if (txn.type === 'credit') {
      runningDue += txn.amount;
      lastCreditAt = txn.timestamp;
    } else if (txn.type === 'payment') {
      runningDue -= txn.amount;
      lastPaymentAt = txn.timestamp;
    } else if (txn.type === 'adjustment') {
      // Adjustment can be positive (add) or negative (subtract)
      runningDue += txn.amount;
    }
  }

  totalDue = Math.max(0, runningDue); // Can't be negative

  // dueSince logic:
  // Walk forward to find when the current unbroken due streak started.
  // After full payment (due → 0), counter resets.
  // After partial payment, counter restarts (dueSince = payment time).
  let dueSince = null;
  let currentDue = 0;
  let lastResetTime = null;

  for (const txn of txns) {
    const prevDue = currentDue;
    if (txn.type === 'credit') {
      currentDue += txn.amount;
      if (prevDue === 0 && currentDue > 0) {
        // First credit on zero-due: set dueSince
        dueSince = txn.timestamp;
      }
      // New credit while due exists: keep dueSince unchanged (no reset)
    } else if (txn.type === 'payment') {
      currentDue -= txn.amount;
      if (currentDue <= 0) {
        // Full payment: clear
        currentDue = 0;
        dueSince = null;
      } else {
        // Partial payment: RESTART counter
        dueSince = txn.timestamp;
      }
    } else if (txn.type === 'adjustment') {
      currentDue += txn.amount;
      if (currentDue <= 0) {
        currentDue = 0;
        dueSince = null;
      }
    }
  }

  return { totalDue, dueSince, lastCreditAt, lastPaymentAt };
}

// ─────────────────── public API ───────────────────

/**
 * Create a new transaction and update the customer's due state.
 *
 * @param {object}  data
 * @param {string}  data.customerId
 * @param {'credit'|'payment'|'adjustment'} data.type
 * @param {number}  data.amount          – always positive
 * @param {string} [data.timestamp]      – ISO string, defaults to now
 * @param {string} [data.note]
 * @param {string} [data.voiceTranscript]
 * @param {'voice'|'manual'} [data.source]
 * @param {string} [data.reversesTxnId]  – set when this txn undoes another
 * @returns {Promise<{txn: object, customer: object}>}
 */
export async function createTransaction(data) {
  const now = new Date().toISOString();
  const billNumber = data.billNumber || (data.type === 'purchase' ? `BILL-${Math.floor(100000 + Math.random() * 900000)}` : null);
  const items = data.items || (data.item ? [{ name: data.item, quantity: data.quantity || 1, price: Math.abs(data.amount), total: Math.abs(data.amount) }] : []);
  const bill = data.bill || (data.type === 'purchase' ? {
    billNumber,
    date: (data.timestamp || now).split('T')[0],
    time: (data.timestamp || now).split('T')[1]?.slice(0, 5) || new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' }),
    customerName: data.customerName || 'नकद ग्राहक (Cash Customer)',
    items: items.length > 0 ? items : [{ name: 'दुकान सामान (General Items)', quantity: 1, price: Math.abs(data.amount), total: Math.abs(data.amount) }],
    subtotal: Math.abs(data.amount),
    total: Math.abs(data.amount),
    paymentMode: data.paymentMode || 'cash',
    status: 'paid',
  } : null);

  const txn = {
    id: uuidv4(),
    customerId: data.customerId || 'walk_in',
    type: data.type,
    amount: Math.abs(data.amount),
    item: data.item || (items[0]?.name) || '',
    items,
    billNumber,
    bill,
    timestamp: data.timestamp || now,
    note: data.note || '',
    voiceTranscript: data.voiceTranscript || '',
    source: data.source || 'manual',
    reversesTxnId: data.reversesTxnId || null,
    createdAt: now,
  };

  // 1. Write transaction + outbox atomically
  await db.transaction('rw', [db.transactions, db.outbox], async () => {
    await db.transactions.add(txn);
    await addToOutbox('transaction', txn.id, 'create', txn);
  });

  // 2. Recalculate and update customer due state (if customer attached)
  let dueState = { totalDue: 0, dueSince: null, lastCreditAt: null, lastPaymentAt: null };
  if (txn.customerId && txn.customerId !== 'walk_in') {
    dueState = await recalcDue(txn.customerId);
    await updateCustomerDue(
      txn.customerId,
      dueState.totalDue,
      dueState.dueSince,
      dueState.lastCreditAt,
      dueState.lastPaymentAt,
    );
  }

  return { txn, dueState };
}

/**
 * Get all transactions for a customer, optionally filtered by date range.
 * Returned newest-first by default.
 */
export async function getByCustomer(customerId, startDate, endDate) {
  let txns = await db.transactions
    .where('customerId')
    .equals(customerId)
    .toArray();

  if (startDate) txns = txns.filter((t) => t.timestamp >= startDate);
  if (endDate) txns = txns.filter((t) => t.timestamp <= endDate);

  txns.sort((a, b) => b.timestamp.localeCompare(a.timestamp)); // newest first
  return txns;
}

/** Get all transactions across all customers, newest first. */
export async function getAll(startDate, endDate) {
  let txns = await db.transactions.toArray();
  if (startDate) txns = txns.filter((t) => t.timestamp >= startDate);
  if (endDate) txns = txns.filter((t) => t.timestamp <= endDate);
  txns.sort((a, b) => b.timestamp.localeCompare(a.timestamp));
  return txns;
}

/** Total credit amount for today. */
export async function getTodayCredit() {
  const todayStart = new Date();
  todayStart.setHours(0, 0, 0, 0);
  const todayStr = todayStart.toISOString();
  const txns = await db.transactions.where('type').equals('credit').toArray();
  return txns
    .filter((t) => t.timestamp >= todayStr)
    .reduce((sum, t) => sum + t.amount, 0);
}

/** Total payment amount for today. */
export async function getTodayPayments() {
  const todayStart = new Date();
  todayStart.setHours(0, 0, 0, 0);
  const todayStr = todayStart.toISOString();
  const txns = await db.transactions.where('type').equals('payment').toArray();
  return txns
    .filter((t) => t.timestamp >= todayStr)
    .reduce((sum, t) => sum + t.amount, 0);
}

/**
 * Reverse a previous transaction by creating an offsetting entry.
 * Edits are NEVER silent overwrites — always a new reversing txn.
 */
export async function reverseTransaction(txnId, reason) {
  const original = await db.transactions.get(txnId);
  if (!original) throw new Error(`Transaction ${txnId} not found`);

  // Create an offsetting transaction
  const reverseType = original.type === 'credit' ? 'payment' : 'credit';
  return createTransaction({
    customerId: original.customerId,
    type: reverseType,
    amount: original.amount,
    note: reason || `Reversal of ${txnId}`,
    source: 'manual',
    reversesTxnId: txnId,
  });
}

/**
 * Compute a running-balance view of a customer's ledger.
 * Returns an array of { ...txn, runningBalance } oldest-first.
 */
export async function getRunningBalance(customerId, startDate, endDate) {
  let txns = await db.transactions
    .where('customerId')
    .equals(customerId)
    .toArray();

  if (startDate) txns = txns.filter((t) => t.timestamp >= startDate);
  if (endDate) txns = txns.filter((t) => t.timestamp <= endDate);

  txns.sort((a, b) => {
    const timeCmp = a.timestamp.localeCompare(b.timestamp);
    if (timeCmp !== 0) return timeCmp;
    return (a.createdAt || '').localeCompare(b.createdAt || '');
  }); // oldest first

  let balance = 0;
  return txns.map((t) => {
    if (t.type === 'credit') balance += t.amount;
    else if (t.type === 'payment') balance -= t.amount;
    else if (t.type === 'purchase') {
      // Normal purchase paid immediately: does not change udhaar balance!
    } else balance += t.amount; // adjustment
    return { ...t, runningBalance: Math.max(0, balance) };
  });
}

/** Wipe all transactions (used by "delete all data"). */
export async function clearTransactions() {
  await db.transactions.clear();
}
