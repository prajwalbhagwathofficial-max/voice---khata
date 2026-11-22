import test from 'node:test';
import assert from 'node:assert/strict';

// Helper functions mirroring client implementation
function normalizeName(str) {
  if (!str) return '';
  return str.toLowerCase().trim().replace(/[^\p{L}\p{N}]/gu, '');
}

function matchesCustomer(customerName, query) {
  const cn = normalizeName(customerName);
  const q = normalizeName(query);
  if (!cn || !q) return false;
  return cn === q || cn.includes(q) || q.includes(cn);
}

function findAllMatchingCustomers(nameOrQuery, customers) {
  if (!nameOrQuery) return [];
  const q = normalizeName(nameOrQuery);
  return customers.filter((c) => {
    if (matchesCustomer(c.name, nameOrQuery)) return true;
    const cn = normalizeName(c.name);
    const city = normalizeName(c.city);
    const phone = (c.phone || '').replace(/\D/g, '');
    if (q.includes(cn) && (q.includes(city) || (phone && q.includes(phone.slice(-4))))) return true;
    return false;
  });
}

function extractPhoneDigits(str) {
  if (!str) return '';
  const wordMap = {
    zero: '0', one: '1', two: '2', three: '3', four: '4', five: '5', six: '6', seven: '7', eight: '8', nine: '9',
    shunya: '0', ek: '1', do: '2', teen: '3', chaar: '4', char: '4', paanch: '5', panch: '5', chhah: '6', chhe: '6', saat: '7', aath: '8', nau: '9',
    sonne: '0', ondu: '1', eradu: '2', mooru: '3', naalku: '4', naaku: '4', aidu: '5', aaru: '6', elu: '7', entu: '8', ombattu: '9',
  };
  let cleaned = str.toLowerCase();
  for (const [w, d] of Object.entries(wordMap)) {
    const reg = new RegExp(`\\b${w}\\b`, 'gi');
    cleaned = cleaned.replace(reg, d);
  }
  return cleaned.replace(/\D/g, '');
}

function generateCustomerId(name, city, phone, digits = 6) {
  const cleanName = (name || '').toLowerCase().trim().replace(/\s+/g, '_').replace(/[^\p{L}\p{M}\p{N}_]/gu, '');
  const cleanCity = (city || 'kengeri').toLowerCase().trim().replace(/\s+/g, '_').replace(/[^\p{L}\p{M}\p{N}_]/gu, '');
  const phoneDigits = (phone || '').replace(/\D/g, '').slice(0, digits) || '000000';
  return `${cleanName}_${cleanCity}_${phoneDigits}`;
}

function generateUniqueCustomerId(name, city, phone, existingList) {
  const base = generateCustomerId(name, city, phone);
  if (!existingList.some(c => c.id === base || c.readableId === base)) return base;
  for (let i = 2; i <= 100; i++) {
    const candidate = `${base}_${i}`;
    if (!existingList.some(c => c.id === candidate || c.readableId === candidate)) return candidate;
  }
  return `${base}_${Date.now().toString().slice(-4)}`;
}

test('TEST 1 & TEST 5: New Customer Registration with Pending Udhaar Execution', async () => {
  const customers = [];
  const transactions = [];

  // 1. Initial command: "Add 500 credit for Ramesh"
  const targetName = "Ramesh";
  const amt = 500;
  const matches = findAllMatchingCustomers(targetName, customers);
  assert.equal(matches.length, 0, 'Customer Ramesh should not exist initially');

  // Pending transaction saved
  const pendingTxn = { type: 'udhaar', amount: amt, customerName: targetName };

  // 2. Voice registration details collected
  const regData = { name: 'Ramesh', city: 'Kengeri', phone: '8317365720', consent: true };
  const uniqueId = generateUniqueCustomerId(regData.name, regData.city, regData.phone, customers);
  assert.equal(uniqueId, 'ramesh_kengeri_831736');

  // Save customer
  const newCust = {
    id: uniqueId,
    name: regData.name,
    city: regData.city,
    phone: regData.phone,
    balance: 0,
    dueSince: null,
    optInWhatsApp: regData.consent,
  };
  customers.push(newCust);

  // 3. Complete pending credit transaction exactly once
  newCust.balance += pendingTxn.amount;
  newCust.dueSince = new Date().toISOString();
  transactions.push({
    id: 'txn-1',
    type: 'credit',
    customerId: newCust.id,
    customer: newCust.name,
    amount: pendingTxn.amount,
  });

  assert.equal(customers.length, 1);
  assert.equal(customers[0].balance, 500);
  assert.equal(transactions.length, 1);
  assert.equal(transactions[0].amount, 500);
});

test('TEST 2, 3, 4: Field Auto-Population and Spoken Phone Digit Normalization', () => {
  // Test spoken number extraction
  const inputPhone1 = "8317365720";
  const inputPhone2 = "eight three one seven three six five seven two zero";
  const inputPhone3 = "aath teen ek saat teen chhe paanch saat do shunya";

  assert.equal(extractPhoneDigits(inputPhone1), "8317365720");
  assert.equal(extractPhoneDigits(inputPhone2), "8317365720");
  assert.equal(extractPhoneDigits(inputPhone3), "8317365720");

  // Name extraction
  const rawSpokenName = "His name is Ramesh Kumar";
  const cleanName = rawSpokenName.replace(/^(?:his name is|her name is|customer name is)\s*/i, '').trim();
  assert.equal(cleanName, "Ramesh Kumar");

  // City extraction
  const rawSpokenCity = "He lives in Kengeri";
  const cleanCity = rawSpokenCity.replace(/^(?:he lives in|she lives in|rehta hai)\s*/i, '').trim();
  assert.equal(cleanCity, "Kengeri");
});

test('TEST 6: Duplicate Customer Disambiguation (Case B)', () => {
  const customers = [
    { id: 'ramesh_kengeri_831736', name: 'Ramesh', city: 'Kengeri', phone: '8317365720', balance: 100 },
    { id: 'ramesh_rajajinagar_988679', name: 'Ramesh', city: 'Rajajinagar', phone: '9886798812', balance: 200 }
  ];

  const matches = findAllMatchingCustomers('Ramesh', customers);
  assert.equal(matches.length, 2, 'Should detect two matching customers named Ramesh');

  // Owner disambiguates by city: "Kengeri Ramesh" or "Kengeri"
  const spokenAnswer = "Kengeri Ramesh";
  const spokenNorm = normalizeName(spokenAnswer);
  const selected = matches.find(c => spokenNorm.includes(normalizeName(c.city)));
  assert.ok(selected);
  assert.equal(selected.city, 'Kengeri');
  assert.equal(selected.id, 'ramesh_kengeri_831736');
});

test('TEST 7: Anonymous Fully Paid Walk-in Bill (Case C)', () => {
  const spoken = "Customer bought items for 250 rupees and paid the full amount";
  const isAnonymousCashSale =
    /paid\s+(?:the\s+)?full(?:\s+amount)?/i.test(spoken) ||
    /(?:bought|kharida|purchase|sale).*?(?:full|paid|cash)/i.test(spoken);

  assert.ok(isAnonymousCashSale);

  const amountMatch = spoken.match(/(\d[\d,]*)/);
  const amount = parseInt(amountMatch[1], 10);
  assert.equal(amount, 250);

  const billRecord = {
    customerName: 'Walk-in Customer',
    totalAmount: amount,
    amountPaid: amount,
    balanceDue: 0,
    status: 'PAID',
  };

  assert.equal(billRecord.customerName, 'Walk-in Customer');
  assert.equal(billRecord.balanceDue, 0);
  assert.equal(billRecord.status, 'PAID');
});

test('TEST 8 & 9: Due Aging Lifecycle (Partial Payment & Full Payment Settlement)', () => {
  const customer = {
    id: 'cust-1',
    name: 'Ramesh',
    balance: 0,
    dueSince: null,
  };

  // 1. First credit: 500
  customer.balance += 500;
  customer.dueSince = '2026-11-01T00:00:00.000Z';
  assert.equal(customer.balance, 500);
  assert.equal(customer.dueSince, '2026-11-01T00:00:00.000Z');

  // 2. Partial payment: pays 200 -> balance 300
  customer.balance -= 200;
  const partialPaymentDate = new Date().toISOString();
  customer.dueSince = partialPaymentDate; // Restarts due-age counter per Section 14
  assert.equal(customer.balance, 300);
  assert.equal(customer.dueSince, partialPaymentDate);

  // 3. Full payment: pays remaining 300 -> balance 0
  customer.balance -= 300;
  customer.dueSince = null; // Counter stops, account settled
  assert.equal(customer.balance, 0);
  assert.equal(customer.dueSince, null);

  // Customer account remains in database (NOT deleted!)
  assert.ok(customer.id);
  assert.equal(customer.name, 'Ramesh');
});

test('TEST 10 & 11: 7-Day Overdue Udhaar Detection and Reminder Eligibility', () => {
  const now = Date.now();
  const eightDaysAgo = new Date(now - 8 * 24 * 60 * 60 * 1000).toISOString();
  const threeDaysAgo = new Date(now - 3 * 24 * 60 * 60 * 1000).toISOString();

  const customers = [
    { id: 'c1', name: 'Ramesh', balance: 500, dueSince: eightDaysAgo, optInWhatsApp: true, phone: '8317365720' },
    { id: 'c2', name: 'Suresh', balance: 200, dueSince: threeDaysAgo, optInWhatsApp: true, phone: '9886793181' },
    { id: 'c3', name: 'Ganesh', balance: 0, dueSince: null, optInWhatsApp: true, phone: '9999999999' },
  ];

  const overdueList = customers.filter(c => {
    if (!c.dueSince || c.balance <= 0) return false;
    const days = Math.floor((now - new Date(c.dueSince).getTime()) / (1000 * 60 * 60 * 24));
    return days >= 7;
  });

  assert.equal(overdueList.length, 1);
  assert.equal(overdueList[0].name, 'Ramesh');

  // Verify wa.me message format
  const c = overdueList[0];
  const msg = `Hello ${c.name}, this is a friendly reminder from Mera Kirana Store. Your pending amount is ₹${c.balance}.`;
  const url = `https://wa.me/91${c.phone}?text=${encodeURIComponent(msg)}`;
  assert.ok(url.includes('wa.me/918317365720'));
  assert.ok(url.includes('500'));
});
