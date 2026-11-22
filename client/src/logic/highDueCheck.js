/**
 * High Due Checker — Voice Khata
 *
 * Checks if a customer’s totalDue exceeds the configured limit.
 * Returns warning info for the UI banner and voice alert.
 */

import { getTemplate } from '../utils/templateEngine.js';

/**
 * Check if a customer has high due.
 *
 * @param {object} customer   – customer record
 * @param {object} settings   – app settings (needs limits.high_due_limit)
 * @param {object} [templates] – all language templates
 * @returns {{isHighDue:boolean, totalDue:number, limit:number, warningText?:string, confirmText?:string}}
 */
export function checkHighDue(customer, settings, templates) {
  const limit = settings?.limits?.high_due_limit ?? 10000;
  const totalDue = customer?.totalDue ?? 0;

  if (totalDue <= limit) {
    return { isHighDue: false, totalDue, limit };
  }

  const lang = customer.preferredLanguage || settings?.languages?.default || 'hi';
  const vars = {
    name: customer.name,
    total: totalDue,
    currency: settings?.display?.currency_symbol || '₹',
    shop: settings?.shop?.name || '',
  };

  return {
    isHighDue: true,
    totalDue,
    limit,
    warningText: templates ? getTemplate(templates, lang, 'high_due_warning', vars) : '',
    confirmText: templates ? getTemplate(templates, lang, 'high_due_confirm', vars) : '',
  };
}

/**
 * Check if adding a new credit would push the customer over the limit.
 * Used for the "Due has crossed the limit. Continue?" confirmation.
 *
 * @param {object} customer
 * @param {number} newAmount – credit amount being added
 * @param {object} settings
 * @param {object} [templates]
 * @returns {{needsConfirmation:boolean, projectedDue:number, ...}}
 */
export function checkHighDueBeforeCredit(customer, newAmount, settings, templates) {
  const limit = settings?.limits?.high_due_limit ?? 10000;
  const currentDue = customer?.totalDue ?? 0;
  const projectedDue = currentDue + newAmount;

  if (projectedDue <= limit) {
    return { needsConfirmation: false, projectedDue, limit };
  }

  // Already over limit → always confirm
  // OR will cross with this credit → confirm
  const lang = customer.preferredLanguage || settings?.languages?.default || 'hi';
  const vars = {
    name: customer.name,
    total: projectedDue,
    currency: settings?.display?.currency_symbol || '₹',
  };

  return {
    needsConfirmation: true,
    projectedDue,
    limit,
    wasAlreadyOver: currentDue > limit,
    confirmText: templates ? getTemplate(templates, lang, 'high_due_confirm', vars) : '',
  };
}
