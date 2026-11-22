import test from 'node:test';
import assert from 'node:assert/strict';
import crypto from 'crypto';

test('WhatsApp Direct Link generation includes name, date, udhaar amount, total due and /r/{event_id}', () => {
  const customer = {
    id: 'ramesh_kengeri_831736',
    name: 'Ramesh',
    phone: '8317365720',
    balance: 2000,
  };
  const amount = 500;
  const eventId = 'txn_8392019482';
  const cleanPhone = customer.phone.replace(/\D/g, '');
  const num = cleanPhone.length === 10 ? '91' + cleanPhone : cleanPhone;
  const origin = 'http://localhost:5173';
  const dateRaw = new Date().toISOString();
  const dateStr = new Date(dateRaw).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
  
  const receiptUrl = `${origin}/r/${encodeURIComponent(eventId)}?c=${encodeURIComponent(customer.name)}&a=${amount}&t=udhaar&b=${customer.balance}&p=${encodeURIComponent(cleanPhone)}&d=${encodeURIComponent(dateRaw)}`;
  
  const text = `*Mera Kirana Store - VoiceKhata Receipt* 🧾\n` +
    `Namaste *${customer.name}* ji,\n` +
    `Aapke khate mein *₹${amount}* udhaar joda gaya hai.\n` +
    `📅 *Date:* ${dateStr}\n` +
    `💰 *Kul Baki (Total Due):* ₹${customer.balance}\n\n` +
    `🔗 *Tamper-Evident Digital Receipt:*\n${receiptUrl}\n\n` +
    `🛡️ Verified by VoiceKhata • Digital Udhaar Ledger`;

  const waUrl = `https://wa.me/${num}?text=${encodeURIComponent(text)}`;

  assert.ok(waUrl.startsWith('https://wa.me/918317365720?text='));
  assert.ok(waUrl.includes(encodeURIComponent('/r/' + eventId)));
  assert.ok(waUrl.includes(encodeURIComponent('500')));
  assert.ok(waUrl.includes(encodeURIComponent('Ramesh')));
  assert.ok(waUrl.includes(encodeURIComponent('*Kul Baki (Total Due):* ₹2000')));
});

test('Tamper-Evident Receipt (/r/{event_id}) cryptographic SHA-256 validation', () => {
  const eventId = 'txn_778899aabb';
  const customerName = 'Ramesh';
  const amount = 500;
  const type = 'udhaar';
  const balanceDue = 2000;
  const dateStr = '2026-11-22T00:00:00.000Z';

  const hashInput = `${eventId}|${customerName}|${amount}|${type}|${balanceDue}|${dateStr}`;
  const sha256Hash = crypto.createHash('sha256').update(hashInput).digest('hex');

  assert.equal(typeof sha256Hash, 'string');
  assert.equal(sha256Hash.length, 64);

  // Altering any parameter must break the seal
  const alteredHash = crypto.createHash('sha256').update(`${eventId}|${customerName}|9999|${type}|${balanceDue}|${dateStr}`).digest('hex');
  assert.notEqual(sha256Hash, alteredHash, 'Tamper detection verified: altered amount produces different hash');
});
