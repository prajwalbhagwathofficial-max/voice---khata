/**
 * Business Rules — Voice Khata
 *
 * Central orchestrator that ties together all business logic modules.
 * UI components and voice handlers import from here.
 *
 * Re-exports individual modules plus provides high-level workflows.
 */

export { checkHighDue, checkHighDueBeforeCredit } from './highDueCheck.js';
export { checkDistance } from './distanceChecker.js';
export { buildWhatsAppLink, openWhatsApp, sendReminder } from './whatsappSender.js';
export { checkCustomerReminders, runReminderCheck, generateThankYou } from './reminderScheduler.js';
export { fillTemplate, getTemplate } from '../utils/templateEngine.js';

import { getTemplate } from '../utils/templateEngine.js';
import { checkHighDueBeforeCredit } from './highDueCheck.js';
import { generateThankYou } from './reminderScheduler.js';
import { sendReminder } from './whatsappSender.js';
import * as defaultTransactionRepo from '../db/transactionRepo.js';
import * as defaultCustomerRepo from '../db/customerRepo.js';
import * as defaultReminderLogRepo from '../db/reminderLogRepo.js';

/**
 * Full credit workflow — called after the voice/UI captures intent.
 *
 * Steps:
 *   1. Check high-due (if over limit, return confirmation needed)
 *   2. Create the transaction
 *   3. If due was cleared, generate thank-you + queue it
 *   4. Return result with TTS text
 *
 * @param {object} params
 * @param {string} params.customerId
 * @param {number} params.amount
 * @param {string} [params.timestamp]
 * @param {string} [params.note]
 * @param {string} [params.voiceTranscript]
 * @param {object} deps – { transactionRepo, customerRepo, reminderLogRepo, settings, templates, crypto }
 * @returns {Promise<{success:boolean, needsConfirmation?:boolean, confirmText?:string, ttsText?:string, dueState?:object, thankYouText?:string}>}
 */
export async function addCreditWorkflow(params, deps = {}) {
  const customerRepo = (deps && deps.customerRepo) || (params && params.customerRepo) || defaultCustomerRepo;
  const transactionRepo = (deps && deps.transactionRepo) || (params && params.transactionRepo) || defaultTransactionRepo;
  const reminderLogRepo = (deps && deps.reminderLogRepo) || (params && params.reminderLogRepo) || defaultReminderLogRepo;
  const settings = (deps && deps.settings) || (params && params.settings) || {};
  const templates = (deps && deps.templates) || (params && params.templates) || {};
  const crypto = (deps && deps.crypto) || (params && params.crypto) || null;

  let customer = params.customer;
  const custId = params.customerId || (customer ? customer.id : null);
  if (!customer && customerRepo && custId) {
    customer = await customerRepo.getCustomerById(custId, crypto);
  }
  if (!customer) return { success: false, error: 'Customer not found' };

  // 1. High-due check
  const highDue = checkHighDueBeforeCredit(customer, params.amount, settings, templates);
  if (highDue.needsConfirmation && !params.confirmed) {
    return {
      success: false,
      needsConfirmation: true,
      confirmText: highDue.confirmText,
      projectedDue: highDue.projectedDue,
    };
  }

  // 2. Create transaction
  const createTxn = transactionRepo?.createTransaction || defaultTransactionRepo?.createTransaction;
  if (typeof createTxn !== 'function') {
    throw new Error('Transaction repository createTransaction is not available');
  }
  const { txn, dueState } = await createTxn({
    customerId: custId,
    type: 'credit',
    amount: params.amount,
    timestamp: params.timestamp,
    note: params.note || '',
    voiceTranscript: params.voiceTranscript || '',
    source: params.source || 'voice',
  });

  const lang = customer.preferredLanguage || settings?.languages?.default || 'hi';
  const ttsText = getTemplate(templates, lang, 'credit_added', {
    name: customer.name,
    amount: params.amount,
    total: dueState.totalDue,
    currency: settings?.display?.currency_symbol || '₹',
  });

  const result = { success: true, txn, dueState, ttsText };

  // 3. If due cleared (shouldn't happen on credit, but just in case)
  if (dueState.totalDue === 0) {
    result.thankYouText = generateThankYou(customer, settings, templates);
  }

  return result;
}

/**
 * Full payment workflow.
 *
 * @param {object} params  – same shape as addCreditWorkflow
 * @param {object} [deps]
 * @returns {Promise<{success:boolean, ttsText:string, dueCleared:boolean, thankYouText?:string, ...}>}
 */
export async function addPaymentWorkflow(params, deps = {}) {
  const customerRepo = (deps && deps.customerRepo) || (params && params.customerRepo) || defaultCustomerRepo;
  const transactionRepo = (deps && deps.transactionRepo) || (params && params.transactionRepo) || defaultTransactionRepo;
  const reminderLogRepo = (deps && deps.reminderLogRepo) || (params && params.reminderLogRepo) || defaultReminderLogRepo;
  const settings = (deps && deps.settings) || (params && params.settings) || {};
  const templates = (deps && deps.templates) || (params && params.templates) || {};
  const crypto = (deps && deps.crypto) || (params && params.crypto) || null;

  let customer = params.customer;
  const custId = params.customerId || (customer ? customer.id : null);
  if (!customer && customerRepo && custId) {
    customer = await customerRepo.getCustomerById(custId, crypto);
  }
  if (!customer) return { success: false, error: 'Customer not found' };

  // Create payment transaction
  const createTxn = transactionRepo?.createTransaction || defaultTransactionRepo?.createTransaction;
  if (typeof createTxn !== 'function') {
    throw new Error('Transaction repository createTransaction is not available');
  }
  const { txn, dueState } = await createTxn({
    customerId: custId,
    type: 'payment',
    amount: params.amount,
    timestamp: params.timestamp,
    note: params.note || '',
    voiceTranscript: params.voiceTranscript || '',
    source: params.source || 'voice',
  });

  const lang = customer.preferredLanguage || settings?.languages?.default || 'hi';

  let ttsText;
  let dueCleared = false;
  let thankYouText = null;

  if (dueState.totalDue === 0) {
    // Full payment → due cleared
    dueCleared = true;
    ttsText = getTemplate(templates, lang, 'due_cleared', { name: customer.name });
    thankYouText = generateThankYou(customer, settings, templates);

    // Queue thank-you message if opted in
    if (customer.optInWhatsApp && customer.phone) {
      const thankMsg = thankYouText;
      sendReminder({ phone: customer.phone, text: thankMsg, autoOpen: false });
      // Log the thank-you
      if (reminderLogRepo) {
        await reminderLogRepo.logReminder({
          customerId: custId,
          kind: 'thank_you',
          channel: 'whatsapp',
          textSent: thankMsg,
          status: 'pending', // owner needs to open the link
        });
      }
    }
  } else {
    // Partial payment
    ttsText = getTemplate(templates, lang, 'payment_received', {
      name: customer.name,
      amount: params.amount,
      total: dueState.totalDue,
      currency: settings?.display?.currency_symbol || '₹',
    });
  }

  return { success: true, txn, dueState, ttsText, dueCleared, thankYouText };
}

/**
 * Query a customer’s total due.
 *
 * @param {string|object} customerOrId
 * @param {object} [deps]
 * @returns {Promise<{ttsText:string, totalDue:number}>}
 */
export async function queryTotalDue(customerOrId, deps = {}) {
  let customer = null;
  let custId = null;

  if (customerOrId && typeof customerOrId === 'object') {
    customer = customerOrId;
    custId = customer.id;
  } else {
    custId = customerOrId;
  }

  const customerRepo = deps.customerRepo || defaultCustomerRepo;
  const settings = deps.settings || {};
  const templates = deps.templates || (deps && deps.hi ? deps : {});
  const crypto = deps.crypto || null;

  if (!customer && customerRepo && custId) {
    customer = await customerRepo.getCustomerById(custId, crypto);
  }
  if (!customer) return { ttsText: 'Customer not found', totalDue: 0 };

  const lang = customer.preferredLanguage || settings?.languages?.default || 'hi';
  const ttsText = getTemplate(templates, lang, 'total_query', {
    name: customer.name,
    total: customer.totalDue || 0,
    currency: settings?.display?.currency_symbol || '₹',
  });

  return { ttsText, totalDue: customer.totalDue || 0 };
}

/**
 * Workflow: Record a normal cash purchase (Buy item, pay now)
 * A normal purchase generates a bill and saves it under the transaction,
 * but does NOT alter the customer's udhaar balance or due-age.
 *
 * @param {object} params
 * @param {string} [params.customerId]
 * @param {object} [params.customer]
 * @param {number} params.amount
 * @param {string} [params.item]
 * @param {number|string} [params.quantity]
 * @param {string} [params.timestamp]
 * @param {string} [params.source='voice']
 * @param {string} [params.voiceTranscript]
 * @param {object} [deps]
 * @returns {Promise<{success:boolean, txn:object, bill:object, ttsText:string}>}
 */
export async function addPurchaseWorkflow(params, deps = {}) {
  const transactionRepo = (deps && deps.transactionRepo) || (params && params.transactionRepo) || defaultTransactionRepo;
  const customerRepo = (deps && deps.customerRepo) || (params && params.customerRepo) || defaultCustomerRepo;
  const settings = (deps && deps.settings) || (params && params.settings) || {};
  const templates = (deps && deps.templates) || (params && params.templates) || {};
  const crypto = (deps && deps.crypto) || (params && params.crypto) || null;

  let customer = params.customer;
  const custId = params.customerId || (customer ? customer.id : null);
  if (!customer && customerRepo && custId && custId !== 'walk_in') {
    customer = await customerRepo.getCustomerById(custId, crypto);
  }

  const itemDesc = params.item || 'सामान (General Items)';
  const qty = params.quantity || 1;
  const amount = params.amount || 0;

  const createTxn = transactionRepo?.createTransaction || defaultTransactionRepo?.createTransaction;
  if (typeof createTxn !== 'function') {
    throw new Error('Transaction repository createTransaction is not available');
  }
  const { txn, dueState } = await createTxn({
    customerId: custId || 'walk_in',
    type: 'purchase',
    amount,
    item: itemDesc,
    quantity: qty,
    timestamp: params.timestamp,
    note: params.note || `Purchase: ${itemDesc}`,
    voiceTranscript: params.voiceTranscript || '',
    source: params.source || 'voice',
    customerName: customer ? customer.name : 'नकद ग्राहक',
  });

  const lang = customer?.preferredLanguage || settings?.languages?.default || 'hi';
  const defaultTts = lang === 'kn'
    ? `${itemDesc} ₹${amount} ಖರೀದಿ ದಾಖಲಾಗಿದೆ. ಬಿಲ್ ಸಿದ್ಧವಾಗಿದೆ.`
    : lang === 'en'
    ? `Purchase of ₹${amount} for ${itemDesc} recorded. Bill is ready.`
    : `${itemDesc} के ₹${amount} की खरीदारी दर्ज की गई। बिल तैयार है।`;

  const ttsText = getTemplate(templates, lang, 'purchase_recorded', {
    item: itemDesc,
    amount,
    billNumber: txn.billNumber || '',
    name: customer?.name || 'ग्राहक',
    currency: settings?.display?.currency_symbol || '₹',
  }) || defaultTts;

  return {
    success: true,
    txn,
    bill: txn.bill,
    dueState,
    ttsText,
  };
}
