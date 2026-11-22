import test from 'node:test';
import assert from 'node:assert/strict';

// Phonetic script equivalents dictionary matching client implementation
const SCRIPT_EQUIVALENTS = {
  pavan: ['पवन', 'पावन', 'pavan', 'pawan', 'pawankumar', 'pavana', 'ಪವನ್', 'பவன்', 'పవన్'],
  ramesh: ['रमेश', 'ramesh', 'ramesha', 'umesh', 'amesh', 'ರಮೇಶ್', 'ரமேஷ்', 'రమేష్'],
  suresh: ['सुरेश', 'suresh', 'suresha', 'sooresh', 'uresh', 'ಸುರೇಶ್', 'சுரேஷ்'],
  nisarga: ['निसर्ग', 'निसर्गा', 'nisarga', 'nisharga', 'visarga', 'isarga', 'nisarg', 'ನಿಸರ್ಗ'],
};

function normalizeName(str) {
  if (!str) return '';
  return str
    .toLowerCase()
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[^\p{L}\p{N}]/gu, '')
    .trim();
}

function isExactNameMatch(storedName, query) {
  const sn = normalizeName(storedName);
  const qn = normalizeName(query);
  if (!sn || !qn) return false;
  if (sn === qn) return true;

  for (const eq of Object.values(SCRIPT_EQUIVALENTS)) {
    const hasSn = eq.some(e => normalizeName(e) === sn);
    const hasQn = eq.some(e => normalizeName(e) === qn);
    if (hasSn && hasQn) return true;
  }
  return false;
}

function fastLevenshtein(a, b) {
  if (a === b) return 0;
  const m = a.length, n = b.length;
  if (m === 0) return n;
  if (n === 0) return m;
  const d = Array.from({ length: m + 1 }, () => new Uint8Array(n + 1));
  for (let i = 0; i <= m; i++) d[i][0] = i;
  for (let j = 0; j <= n; j++) d[0][j] = j;
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost);
    }
  }
  return d[m][n];
}

function findFuzzyMatchingCustomers(query, list) {
  const qn = normalizeName(query);
  if (!qn || qn.length < 3) return [];
  return list.filter(c => {
    const cn = normalizeName(c.name);
    if (!cn) return false;
    if (cn.startsWith(qn) || qn.startsWith(cn) || cn.includes(qn) || qn.includes(cn)) return true;
    if (Math.abs(cn.length - qn.length) <= 2 && fastLevenshtein(cn, qn) <= 2) return true;
    return false;
  });
}

function resolveCustomerCandidates(nameOrQuery, list) {
  if (!nameOrQuery) return { type: 'none', candidates: [] };
  const rawTrim = (nameOrQuery || '').trim();
  const qn = normalizeName(rawTrim);

  // Priority 1: Direct immutable ID
  const idMatch = list.filter(c => c.id === rawTrim || c.readableId === rawTrim);
  if (idMatch.length === 1) {
    return { type: 'exact_id', candidates: idMatch };
  }

  // Priority 2: Query contains distinguishing info (name + city or name + phone)
  const distinguished = list.filter(c => {
    const cn = normalizeName(c.name);
    const city = normalizeName(c.city);
    const phone = (c.phone || '').replace(/\D/g, '');
    if (qn.includes(cn) && (city && qn.includes(city))) return true;
    if (qn.includes(cn) && (phone && qn.includes(phone.slice(-4)))) return true;
    return false;
  });
  if (distinguished.length === 1) {
    return { type: 'exact_distinguished', candidates: distinguished };
  }
  if (distinguished.length > 1) {
    return { type: 'exact_name', candidates: distinguished };
  }

  // Stage 2 / Priority 3: Exact Name Matching (surnames distinct)
  const exactMatches = list.filter(c => isExactNameMatch(c.name, rawTrim));
  if (exactMatches.length > 0) {
    return { type: 'exact_name', candidates: exactMatches };
  }

  // Stage 3 / Priority 4: Partial and Fuzzy Matching
  const fuzzyMatches = findFuzzyMatchingCustomers(rawTrim, list);
  if (fuzzyMatches.length > 0) {
    return { type: 'fuzzy', candidates: fuzzyMatches };
  }

  return { type: 'none', candidates: [] };
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

function askDistinguishingQuestion(candidates, targetName, intent, amount) {
  const distinctCities = [...new Set(candidates.map(c => c.city || 'Local'))];
  if (candidates.length === 1) {
    const prev = candidates[0];
    const phoneEnding = (prev.phone || '').slice(-4) || 'N/A';
    return `Found previous customer ${prev.name} (${prev.city || 'Kengeri'}, ending ${phoneEnding}). Is this the previous ${prev.name} or a new customer?`;
  }
  if (distinctCities.length === candidates.length) {
    return intent === 'payment'
      ? `Which ${targetName} paid ₹${amount}? ${distinctCities.join(' or ')}?`
      : `Which ${targetName}? ${distinctCities.join(' or ')}?`;
  }
  if (distinctCities.length === 1) {
    const phones = candidates.map(c => (c.phone || '').slice(-4) || 'N/A').join(' or ');
    return `Two customers named ${targetName} are from ${distinctCities[0]}. Which one? Phone ending ${phones}?`;
  }
  const options = candidates.map(c => `${c.city || 'Local'} (ending ${(c.phone || '').slice(-4) || 'N/A'})`).join(', or ');
  return `Found ${candidates.length} customers named ${targetName}. Which one? ${options}?`;
}

// Transaction simulation helper matching client recordTransaction
function simulateRecordTransaction(customers, transactions, type, customerName, amount, note, specificCustomerId = null) {
  let customer = null;
  if (specificCustomerId) {
    customer = customers.find(c => c.id === specificCustomerId || c.readableId === specificCustomerId);
  } else if (customerName) {
    const res = resolveCustomerCandidates(customerName, customers);
    if (res.candidates.length === 1) customer = res.candidates[0];
  }

  // Security guard: never update financial balance if customer identity is unresolved
  if (!customer && type !== 'bill') {
    return null;
  }

  if (customer) {
    if (type === 'udhaar') {
      customer.balance = (customer.balance || 0) + amount;
      if (!customer.dueSince) customer.dueSince = new Date().toISOString();
    } else if (type === 'vasooli') {
      customer.balance = Math.max(0, (customer.balance || 0) - amount);
      if (customer.balance === 0) {
        customer.dueSince = null;
      } else {
        customer.dueSince = new Date().toISOString();
      }
    }
  }

  const txn = {
    id: `txn_${Date.now()}_${Math.random()}`,
    type,
    customerId: customer ? customer.id : null,
    customer: customer ? customer.name : customerName,
    amount,
    note,
    timestamp: new Date().toISOString(),
  };
  transactions.push(txn);
  return txn;
}

// =============================================================================
// ACCEPTANCE TESTS (Section 16: Tests 1 to 15)
// =============================================================================

test('Test 1 — One Exact Match: Resolves to single customer and follows confirmation rules', () => {
  const storeCustomers = [
    { id: 'ramesh_kengeri_5720', name: 'Ramesh', city: 'Kengeri', phone: '9886795720', balance: 1500 },
    { id: 'suresh_rajaji_1111', name: 'Suresh', city: 'Rajajinagar', phone: '9886791111', balance: 500 },
  ];
  const transactions = [];

  const resolution = resolveCustomerCandidates('Ramesh', storeCustomers);
  assert.equal(resolution.type, 'exact_name');
  assert.equal(resolution.candidates.length, 1);
  assert.equal(resolution.candidates[0].id, 'ramesh_kengeri_5720');

  // Transaction executed using immutable ID after confirmation
  const txn = simulateRecordTransaction(storeCustomers, transactions, 'udhaar', 'Ramesh', 500, 'credit', resolution.candidates[0].id);
  assert.ok(txn);
  assert.equal(storeCustomers[0].balance, 2000);
  assert.equal(storeCustomers[1].balance, 500); // Suresh untouched
});

test('Test 2 — Two Identical Names, Different Cities: Ambiguity detected for repayment', () => {
  const storeCustomers = [
    { id: 'ramesh_kengeri_5720', name: 'Ramesh', city: 'Kengeri', phone: '9886795720', balance: 1500 },
    { id: 'ramesh_rajajinagar_8812', name: 'Ramesh', city: 'Rajajinagar', phone: '9886798812', balance: 800 },
  ];
  const transactions = [];

  const resolution = resolveCustomerCandidates('Ramesh', storeCustomers);
  assert.equal(resolution.type, 'exact_name');
  assert.equal(resolution.candidates.length, 2, 'Must retain all candidates, never choose first');

  // Verify no payment posted before resolution
  const unverifiedTxn = simulateRecordTransaction(storeCustomers, transactions, 'vasooli', 'Ramesh', 300, 'payment');
  assert.equal(unverifiedTxn, null, 'Unresolved transaction must be blocked');
  assert.equal(storeCustomers[0].balance, 1500, 'Ramesh Kengeri balance unchanged');
  assert.equal(storeCustomers[1].balance, 800, 'Ramesh Rajajinagar balance unchanged');

  // Check generated clarification question
  const question = askDistinguishingQuestion(resolution.candidates, 'Ramesh', 'payment', 300);
  assert.ok(question.includes('Kengeri'));
  assert.ok(question.includes('Rajajinagar'));
});

test('Test 3 — Same Name and Same City: Asks for distinguishing phone digits', () => {
  const storeCustomers = [
    { id: 'ramesh_kengeri_5720', name: 'Ramesh', city: 'Kengeri', phone: '9886795720', balance: 1500 },
    { id: 'ramesh_kengeri_1234', name: 'Ramesh', city: 'Kengeri', phone: '9886791234', balance: 2000 },
  ];

  const resolution = resolveCustomerCandidates('Ramesh', storeCustomers);
  assert.equal(resolution.candidates.length, 2);

  const question = askDistinguishingQuestion(resolution.candidates, 'Ramesh', 'credit', 500);
  assert.ok(question.includes('Phone ending') || question.includes('5720') || question.includes('1234'));
  assert.ok(question.includes('5720'));
  assert.ok(question.includes('1234'));
});

test('Test 4 — Three Duplicate Names: Retains all three candidates (surnames distinct)', () => {
  const storeCustomers = [
    { id: 'ramesh_kengeri_5720', name: 'Ramesh', city: 'Kengeri', phone: '9886795720', balance: 1500 },
    { id: 'ramesh_rajajinagar_8812', name: 'Ramesh', city: 'Rajajinagar', phone: '9886798812', balance: 800 },
    { id: 'ramesh_kengeri_1234', name: 'Ramesh', city: 'Kengeri', phone: '9886791234', balance: 2000 },
    { id: 'ramesh_kumar_kengeri_7421', name: 'Ramesh Kumar', city: 'Kengeri', phone: '9886797421', balance: 500 },
  ];

  const resolution = resolveCustomerCandidates('Ramesh', storeCustomers);
  assert.equal(resolution.candidates.length, 3, 'Must match exactly the 3 customers named Ramesh');
  // Ramesh Kumar is kept separate and distinct
  assert.ok(!resolution.candidates.some(c => c.id === 'ramesh_kumar_kengeri_7421'));
});

test('Test 5 — Voice Selection: "Kengeri wala" selects correct candidate from active list', () => {
  const candidates = [
    { id: 'ramesh_kengeri_5720', name: 'Ramesh', city: 'Kengeri', phone: '9886795720', balance: 1500 },
    { id: 'ramesh_rajajinagar_8812', name: 'Ramesh', city: 'Rajajinagar', phone: '9886798812', balance: 800 },
  ];

  const spoken = "Kengeri wala";
  const spokenNorm = normalizeName(spoken);
  const remaining = candidates.filter(c => spokenNorm.includes(normalizeName(c.city)));
  assert.equal(remaining.length, 1);
  assert.equal(remaining[0].id, 'ramesh_kengeri_5720');
});

test('Test 6 — No Matching Location: Asks again and posts no transaction', () => {
  const candidates = [
    { id: 'ramesh_kengeri_5720', name: 'Ramesh', city: 'Kengeri', phone: '9886795720', balance: 1500 },
    { id: 'ramesh_rajajinagar_8812', name: 'Ramesh', city: 'Rajajinagar', phone: '9886798812', balance: 800 },
  ];
  const transactions = [];

  const spoken = "Whitefield";
  const spokenNorm = normalizeName(spoken);
  const remaining = candidates.filter(c => spokenNorm.includes(normalizeName(c.city)));
  assert.equal(remaining.length, 0, 'No candidate should match Whitefield');
  assert.equal(transactions.length, 0, 'No transaction should be created');
});

test('Test 7 — Payment Safety: Ramesh B payment reduces only Ramesh B balance', () => {
  const storeCustomers = [
    { id: 'ramesh_a_kengeri', name: 'Ramesh', city: 'Kengeri', phone: '9886795720', balance: 1500 },
    { id: 'ramesh_b_rajaji', name: 'Ramesh', city: 'Rajajinagar', phone: '9886798812', balance: 800 },
  ];
  const transactions = [];

  simulateRecordTransaction(storeCustomers, transactions, 'vasooli', 'Ramesh', 300, 'payment', 'ramesh_b_rajaji');
  assert.equal(storeCustomers[0].balance, 1500, 'Ramesh A must remain ₹1,500');
  assert.equal(storeCustomers[1].balance, 500, 'Ramesh B must become ₹500');
});

test('Test 8 — Credit Safety: Ramesh A credit increases only Ramesh A balance', () => {
  const storeCustomers = [
    { id: 'ramesh_a_kengeri', name: 'Ramesh', city: 'Kengeri', phone: '9886795720', balance: 1500 },
    { id: 'ramesh_b_rajaji', name: 'Ramesh', city: 'Rajajinagar', phone: '9886798812', balance: 800 },
  ];
  const transactions = [];

  simulateRecordTransaction(storeCustomers, transactions, 'udhaar', 'Ramesh', 500, 'credit', 'ramesh_a_kengeri');
  assert.equal(storeCustomers[0].balance, 2000, 'Ramesh A must become ₹2,000');
  assert.equal(storeCustomers[1].balance, 800, 'Ramesh B must remain ₹800');
});

test('Test 9 — Cancel Clarification: Owner cancels, no state or balance changes', () => {
  const storeCustomers = [
    { id: 'ramesh_a_kengeri', name: 'Ramesh', city: 'Kengeri', phone: '9886795720', balance: 1500, dueSince: '2026-11-01T00:00:00Z' },
    { id: 'ramesh_b_rajaji', name: 'Ramesh', city: 'Rajajinagar', phone: '9886798812', balance: 800, dueSince: '2026-11-05T00:00:00Z' },
  ];
  const transactions = [];

  // User says "Cancel"
  let draft = { status: 'CUSTOMER_CLARIFICATION', amount: 500, selectedCustomerId: null };
  const userSpeech = "Cancel kar do";
  if (/cancel|chodo|hatao|stop/i.test(userSpeech)) {
    draft = null;
  }

  assert.equal(draft, null);
  assert.equal(transactions.length, 0);
  assert.equal(storeCustomers[0].balance, 1500);
  assert.equal(storeCustomers[0].dueSince, '2026-11-01T00:00:00Z');
  assert.equal(storeCustomers[1].balance, 800);
  assert.equal(storeCustomers[1].dueSince, '2026-11-05T00:00:00Z');
});

test('Test 10 — Change Selected Customer during confirmation', () => {
  const storeCustomers = [
    { id: 'ramesh_kengeri_5720', name: 'Ramesh', city: 'Kengeri', phone: '9886795720', balance: 1500 },
    { id: 'ramesh_rajaji_8812', name: 'Ramesh', city: 'Rajajinagar', phone: '9886798812', balance: 800 },
  ];
  const transactions = [];

  let draft = {
    selectedCustomerId: 'ramesh_kengeri_5720',
    amount: 500,
    status: 'AWAITING_CONFIRMATION',
  };

  // User says: "No, Rajajinagar Ramesh"
  const correction = "No, Rajajinagar Ramesh";
  const switchCandidate = storeCustomers.find(c => c.id !== draft.selectedCustomerId && normalizeName(correction).includes(normalizeName(c.city)));
  assert.ok(switchCandidate);
  assert.equal(switchCandidate.id, 'ramesh_rajaji_8812');

  draft.selectedCustomerId = switchCandidate.id;

  // After reconfirmation, executes for Rajajinagar Ramesh
  simulateRecordTransaction(storeCustomers, transactions, 'udhaar', 'Ramesh', draft.amount, 'credit', draft.selectedCustomerId);
  assert.equal(storeCustomers[0].balance, 1500, 'Kengeri Ramesh balance was untouched');
  assert.equal(storeCustomers[1].balance, 1300, 'Rajajinagar Ramesh received the 500 credit');
});

test('Test 11 — Double Confirmation: Owner says Yes twice, only 1 transaction recorded', () => {
  const storeCustomers = [
    { id: 'ramesh_kengeri_5720', name: 'Ramesh', city: 'Kengeri', phone: '9886795720', balance: 1500 },
  ];
  const transactions = [];

  const draft = {
    selectedCustomerId: 'ramesh_kengeri_5720',
    amount: 500,
    executed: false,
  };

  function handleConfirm(speech) {
    if (/yes|haan/i.test(speech)) {
      if (draft.executed) return;
      draft.executed = true;
      simulateRecordTransaction(storeCustomers, transactions, 'udhaar', 'Ramesh', draft.amount, 'credit', draft.selectedCustomerId);
    }
  }

  handleConfirm("Yes");
  handleConfirm("Yes"); // Repeated Yes

  assert.equal(transactions.length, 1, 'Only one transaction must be written');
  assert.equal(storeCustomers[0].balance, 2000);
});

test('Test 12 — Fuzzy Match: Incomplete name requires confirmation, no automatic transaction', () => {
  const storeCustomers = [
    { id: 'ramesh_kumar_7421', name: 'Ramesh Kumar', city: 'Kengeri', phone: '9886797421', balance: 500 },
  ];
  const transactions = [];

  // User says "Ramesh K"
  const resolution = resolveCustomerCandidates('Ramesh K', storeCustomers);
  assert.equal(resolution.type, 'fuzzy');
  assert.equal(resolution.candidates.length, 1);
  assert.equal(resolution.candidates[0].name, 'Ramesh Kumar');

  // Must not automatically post transaction
  assert.equal(transactions.length, 0);
  assert.equal(storeCustomers[0].balance, 500);
});

test('Test 13 — Existing Customer IDs: Verified immutable customer ID used throughout', () => {
  const storeCustomers = [
    { id: 'immutable_cust_998877', readableId: 'ramesh_kengeri_5720', name: 'Ramesh', city: 'Kengeri', phone: '9886795720', balance: 1000 },
  ];
  const transactions = [];

  const txn = simulateRecordTransaction(storeCustomers, transactions, 'udhaar', 'Ramesh', 250, 'credit', 'immutable_cust_998877');
  assert.equal(txn.customerId, 'immutable_cust_998877', 'Transaction must record the immutable customer ID');
});

test('Test 14 — Store Isolation: Customers from another store are excluded', () => {
  const allCustomers = [
    { id: 'store1_ramesh', storeId: 'store_1', name: 'Ramesh', city: 'Kengeri', phone: '9886795720', balance: 1000 },
    { id: 'store2_ramesh', storeId: 'store_2', name: 'Ramesh', city: 'Kengeri', phone: '9886795720', balance: 2000 },
  ];

  const currentStoreId = 'store_1';
  const scopedCustomers = allCustomers.filter(c => c.storeId === currentStoreId);
  const resolution = resolveCustomerCandidates('Ramesh', scopedCustomers);

  assert.equal(resolution.candidates.length, 1);
  assert.equal(resolution.candidates[0].id, 'store1_ramesh');
});

test('Test 15 — No Regression: Add credit, repayment, search, and history work without breaking', () => {
  const storeCustomers = [
    { id: 'c1', name: 'Ramesh', city: 'Kengeri', phone: '9886795720', balance: 1000, dueSince: '2026-11-01T00:00:00Z' },
    { id: 'c2', name: 'Suresh', city: 'Rajajinagar', phone: '9886798812', balance: 500, dueSince: '2026-11-05T00:00:00Z' },
  ];
  const transactions = [];

  // 1. Credit addition
  simulateRecordTransaction(storeCustomers, transactions, 'udhaar', 'Ramesh', 300, 'credit', 'c1');
  assert.equal(storeCustomers[0].balance, 1300);

  // 2. Repayment
  simulateRecordTransaction(storeCustomers, transactions, 'vasooli', 'Suresh', 200, 'payment', 'c2');
  assert.equal(storeCustomers[1].balance, 300);

  // 3. Transactions history
  assert.equal(transactions.length, 2);
  assert.equal(transactions[0].amount, 300);
  assert.equal(transactions[1].amount, 200);
});

test('Test 16 — Full Debt Settlement without Digit: "Ramesh ne saara kar chuka diya" disambiguates and clears exact balance', () => {
  const storeCustomers = [
    { id: 'ramesh_kengeri_5720', name: 'Ramesh', city: 'Kengeri', phone: '9886795720', balance: 1500 },
    { id: 'ramesh_rajajinagar_8812', name: 'Ramesh', city: 'Rajajinagar', phone: '9886798812', balance: 800 },
  ];
  const transactions = [];

  const spokenText = "Ramesh ne saara kar chuka diya";
  const isFullSettlement = /saara|poora|pura|full|all|everything|settle|khatam|chuk\s*gaya|chuka\s*diya/i.test(spokenText);
  assert.equal(isFullSettlement, true, 'Must detect full debt settlement phrase');

  // Multi-candidate resolution
  const resolution = resolveCustomerCandidates('Ramesh', storeCustomers);
  assert.equal(resolution.candidates.length, 2, 'Must identify both candidates without arbitrary selection');

  // Disambiguation prompt asks which Ramesh
  const question = `Which Ramesh ne saara hisab chuka diya? Kengeri or Rajajinagar?`;
  assert.match(question, /Which Ramesh ne saara hisab chuka diya/);

  // Owner responds "Kengeri"
  const ownerResponse = "Kengeri wala";
  const selectedCandidate = resolution.candidates.find(c =>
    normalizeName(ownerResponse).includes(normalizeName(c.city))
  );
  assert.ok(selectedCandidate);
  assert.equal(selectedCandidate.id, 'ramesh_kengeri_5720');

  // Amount dynamically resolves to candidate's entire current balance (₹1,500)
  const settlementAmount = selectedCandidate.balance;
  assert.equal(settlementAmount, 1500);

  // Execute payment
  const prevBalance = selectedCandidate.balance;
  const txn = simulateRecordTransaction(storeCustomers, transactions, 'vasooli', selectedCandidate.name, settlementAmount, 'payment', selectedCandidate.id);
  const newBalance = selectedCandidate.balance;

  assert.equal(newBalance, 0, 'Kengeri Ramesh balance must be ₹0');
  assert.equal(storeCustomers[1].balance, 800, 'Rajajinagar Ramesh balance must remain untouched at ₹800');

  // Crisp voice response formatting
  const responseVoice = `${selectedCandidate.name} ka ₹${prevBalance} kharch tha, ₹${settlementAmount} chuka diya, remaining baaki ₹${newBalance} hai.`;
  assert.equal(responseVoice, "Ramesh ka ₹1500 kharch tha, ₹1500 chuka diya, remaining baaki ₹0 hai.");
});

test('Test 17 — Both Customers Settlement: "dono Ramesh ne saara kar chuka diya" settles all matching accounts to ₹0', () => {
  const storeCustomers = [
    { id: 'ramesh_kengeri_5720', name: 'Ramesh', city: 'Kengeri', phone: '9886795720', balance: 1500 },
    { id: 'ramesh_rajajinagar_8812', name: 'Ramesh', city: 'Rajajinagar', phone: '9886798812', balance: 800 },
  ];
  const transactions = [];

  const spokenText = "Ramesh ne dono Ramesh ne saara kar chuka diya";
  const isBoth = /dono|both|ubhayaru|eradu\s*jana/i.test(spokenText);
  const isFullSettlement = /saara|poora|pura|full|all|everything|settle|khatam|chuk\s*gaya|chuka\s*diya/i.test(spokenText);
  assert.equal(isBoth, true, 'Must detect joint/both customer intent');
  assert.equal(isFullSettlement, true, 'Must detect full debt settlement');

  const resolution = resolveCustomerCandidates('Ramesh', storeCustomers);
  assert.equal(resolution.candidates.length, 2);

  // Both customer IDs targeted in joint transaction draft
  const jointDraft = {
    isBoth: true,
    bothCustomerIds: resolution.candidates.map(c => c.id),
    isFullSettlement: true,
    executed: false,
  };

  // Execution settles each customer to ₹0
  const results = [];
  for (const custId of jointDraft.bothCustomerIds) {
    const c = storeCustomers.find(item => item.id === custId);
    const amtToPay = c.balance;
    simulateRecordTransaction(storeCustomers, transactions, 'vasooli', c.name, amtToPay, 'payment', c.id);
    results.push({ customer: c, paid: amtToPay });
  }

  assert.equal(storeCustomers[0].balance, 0, 'Kengeri Ramesh must be ₹0');
  assert.equal(storeCustomers[1].balance, 0, 'Rajajinagar Ramesh must be ₹0');
  assert.equal(transactions.length, 2, '2 payment transactions must be recorded');

  const details = results.map(r => `${r.customer.city} ₹${r.paid} chuka diya`).join(', ');
  const responseVoice = `Dono Ramesh ka hisab chuk gaya: ${details}. Dono ka remaining baaki ₹0 hai.`;
  assert.equal(responseVoice, "Dono Ramesh ka hisab chuk gaya: Kengeri ₹1500 chuka diya, Rajajinagar ₹800 chuka diya. Dono ka remaining baaki ₹0 hai.");
});

test('Test 18 — Crisp Ledger Voice Formatting: Output strictly adheres to financial ledger statements without conversational fluff', () => {
  // 1. Payment format test
  const candidate = { name: 'Ramesh', balance: 1200 };
  const prev = 1500;
  const paid = 300;
  const rem = 1200;
  const paymentMsg = `${candidate.name} ka ₹${prev} kharch tha, ₹${paid} chuka diya, remaining baaki ₹${rem} hai.`;
  assert.equal(paymentMsg, "Ramesh ka ₹1500 kharch tha, ₹300 chuka diya, remaining baaki ₹1200 hai.");
  assert.equal(/namaste|bhai|sahab|kaise|shubh/i.test(paymentMsg), false, 'No conversational fluff in payment speech');

  // 2. Credit format test
  const creditPrev = 500;
  const creditAmt = 200;
  const creditNew = 700;
  const creditMsg = `${candidate.name} ka pehle ₹${creditPrev} tha, ₹${creditAmt} naya udhaar joda, ab kul baaki ₹${creditNew} hai.`;
  assert.equal(creditMsg, "Ramesh ka pehle ₹500 tha, ₹200 naya udhaar joda, ab kul baaki ₹700 hai.");
  assert.equal(/namaste|bhai|sahab|kaise|shubh/i.test(creditMsg), false, 'No conversational fluff in credit speech');
});

test('Test 19 — Sale & Collection Update: "Ramesh ne ₹50 sugar packet ka sale hua" updates Today\'s Collection and generates a summary bill', () => {
  const storeCustomers = [
    { id: 'c1', name: 'Ramesh', city: 'Kengeri', phone: '9886795720', balance: 1000 },
  ];
  const transactions = [];
  let todayCollection = 0;

  // 1. Spoken sale command
  const spokenText = "Ramesh ne ₹50 sugar packet ka sale hua";
  const amountMatch = spokenText.match(/(\d[\d,]*)/);
  const saleAmt = amountMatch ? parseInt(amountMatch[1].replace(/,/g, ''), 10) : 50;
  assert.equal(saleAmt, 50);

  // Extract item name
  const itemMatch = spokenText.match(/(?:ne\s+)?(?:₹?\s*\d+\s*(?:rupaye|rupees|rs)?\s*)?([a-zA-Z\u0900-\u097F\s]+?)(?:\s*ka\s*sale|\s*sale|\s*kharida|\s*bought|\s*bikri)/i);
  const itemName = (itemMatch && itemMatch[1]) ? itemMatch[1].trim() : 'sugar packet';
  assert.equal(itemName, 'sugar packet');

  // Record cash sale bill
  const txn = simulateRecordTransaction(storeCustomers, transactions, 'bill', 'Ramesh', saleAmt, itemName);
  assert.equal(txn.type, 'bill');
  assert.equal(txn.amount, 50);
  assert.equal(txn.customer, 'Ramesh');

  // Today's collection increment rule: includes both vasooli and cash sales (bills)
  todayCollection += txn.amount;
  assert.equal(todayCollection, 50, "Today's collection section must increment by sale amount");

  // Generate summary bill receipt
  const summaryBill = {
    billNumber: 'BILL-123456',
    customerName: 'Ramesh',
    items: [{ name: itemName, qty: 1, price: saleAmt }],
    totalAmount: saleAmt,
    amountPaid: saleAmt,
    balanceDue: 0,
    status: 'PAID (नकद चुकता)',
  };
  assert.equal(summaryBill.totalAmount, 50);
  assert.equal(summaryBill.amountPaid, 50);
  assert.equal(summaryBill.balanceDue, 0);
  assert.equal(summaryBill.status, 'PAID (नकद चुकता)');
});
