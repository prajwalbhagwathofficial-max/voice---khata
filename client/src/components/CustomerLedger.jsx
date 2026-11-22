/**
 * Customer Ledger (Hisab) Component — Voice Khata
 *
 * Detailed customer view showing:
 * - Outstanding balance & overdue day count
 * - Running balance transaction history
 * - One-tap WhatsApp reminder trigger (wa.me deep link)
 * - Quick credit/payment entry
 */

import { useState, useEffect } from 'preact/hooks';
import * as transactionRepo from '../db/transactionRepo.js';
import * as reminderLogRepo from '../db/reminderLogRepo.js';
import { buildWhatsAppLink } from '../logic/whatsappSender.js';
import { getTemplate, fillTemplate } from '../utils/templateEngine.js';
import { BillModal } from './BillModal.jsx';

export function CustomerLedger({
  customer,
  templates = {},
  shopName = '',
  onClose,
  onAddCredit,
  onAddPayment,
  onReverseTxn,
}) {
  const [ledger, setLedger] = useState([]);
  const [loading, setLoading] = useState(true);
  const [actionAmount, setActionAmount] = useState('');
  const [actionType, setActionType] = useState('credit');
  const [showAddModal, setShowAddModal] = useState(false);
  const [activeBill, setActiveBill] = useState(null);

  useEffect(() => {
    if (!customer?.id) return;
    loadLedger();
  }, [customer?.id]);

  const loadLedger = async () => {
    setLoading(true);
    try {
      const records = await transactionRepo.getRunningBalance(customer.id);
      // Display newest first in UI
      setLedger(records.reverse());
    } catch (err) {
      console.error('Failed to load ledger:', err);
    } finally {
      setLoading(false);
    }
  };

  if (!customer) return null;

  // Calculate overdue days
  let overdueDays = 0;
  if (customer.dueSince) {
    const dueTime = new Date(customer.dueSince).getTime();
    overdueDays = Math.max(0, Math.floor((Date.now() - dueTime) / (1000 * 60 * 60 * 24)));
  }

  // Generate WhatsApp reminder link
  const reminderTemplate = getTemplate(templates, customer.preferredLanguage || 'hi', 'reminder_first');
  const reminderText = fillTemplate(reminderTemplate, {
    name: customer.name,
    shop: shopName || 'दुकानदार',
    total: customer.totalDue,
    days: overdueDays,
  });
  const whatsappUrl = customer.phone ? buildWhatsAppLink(customer.phone, reminderText) : null;

  const handleWhatsAppClick = async (e) => {
    e.preventDefault();
    if (!whatsappUrl) return;
    window.open(whatsappUrl, '_blank', 'noopener,noreferrer');
    try {
      await reminderLogRepo.logReminder({
        customerId: customer.id,
        channel: 'whatsapp',
        kind: overdueDays >= 7 ? 'first' : 'repeat',
        textSent: reminderText,
        status: 'sent',
      });
    } catch (err) {
      console.warn('Failed to log reminder:', err);
    }
  };

  const handleQuickSubmit = async (e) => {
    e.preventDefault();
    const amt = parseFloat(actionAmount);
    if (!amt || amt <= 0) return;

    if (actionType === 'credit') {
      await onAddCredit(customer, amt);
    } else {
      await onAddPayment(customer, amt);
    }

    setActionAmount('');
    setShowAddModal(false);
    await loadLedger();
  };

  return (
    <div style={overlayStyle}>
      <div style={modalStyle}>
        {/* Header */}
        <div style={headerStyle}>
          <div>
            <h2 style={{ margin: 0, fontSize: '20px' }}>{customer.name}</h2>
            <p style={{ margin: '4px 0 0', fontSize: '13px', color: '#666' }}>
              📍 {customer.city} | 📞 {customer.phone || 'फ़ोन नहीं'}
            </p>
          </div>
          <button style={closeBtnStyle} onClick={onClose}>✕</button>
        </div>

        {/* Balance Card */}
        <div style={balanceCardStyle}>
          <div>
            <span style={{ fontSize: '12px', color: '#555', textTransform: 'uppercase' }}>
              कुल बकाया (Total Due)
            </span>
            <div style={{ fontSize: '32px', fontWeight: '700', color: customer.totalDue > 0 ? '#d32f2f' : '#2e7d32' }}>
              ₹ {customer.totalDue.toLocaleString('en-IN')}
            </div>
            {customer.totalDue > 0 && overdueDays > 0 && (
              <span style={overdueBadgeStyle}>
                ⚠️ {overdueDays} दिन से बाकी
              </span>
            )}
          </div>

          {/* WhatsApp Reminder Button */}
          {customer.totalDue > 0 && whatsappUrl && (
            <button
              onClick={handleWhatsAppClick}
              style={{ ...whatsappButtonStyle, border: 'none', cursor: 'pointer' }}
            >
              💬 WhatsApp तगादा
            </button>
          )}
        </div>

        {/* Action Bar */}
        <div style={actionRowStyle}>
          <button
            style={creditBtnStyle}
            onClick={() => { setActionType('credit'); setShowAddModal(true); }}
          >
            ➕ उधार जोड़ें (+Credit)
          </button>
          <button
            style={paymentBtnStyle}
            onClick={() => { setActionType('payment'); setShowAddModal(true); }}
          >
            💰 जमा प्राप्त (-Payment)
          </button>
        </div>

        {/* Quick Amount Modal */}
        {showAddModal && (
          <form onSubmit={handleQuickSubmit} style={quickFormStyle}>
            <div style={{ fontWeight: '600', marginBottom: '8px' }}>
              {actionType === 'credit' ? 'उधार राशि दर्ज करें' : 'जमा राशि दर्ज करें'}
            </div>
            <div style={{ display: 'flex', gap: '8px' }}>
              <input
                type="number"
                placeholder="₹ राशि (e.g. 500)"
                value={actionAmount}
                onInput={(e) => setActionAmount(e.target.value)}
                style={inputStyle}
                autoFocus
              />
              <button type="submit" style={submitBtnStyle}>जोड़ें</button>
              <button type="button" style={cancelBtnStyle} onClick={() => setShowAddModal(false)}>रद्द</button>
            </div>
          </form>
        )}

        {/* Timeline Header */}
        <h3 style={{ fontSize: '15px', margin: '16px 0 8px', color: '#444' }}>
          खाता इतिहास (Ledger Timeline)
        </h3>

        {/* Ledger Entries */}
        <div style={timelineContainerStyle}>
          {loading ? (
            <p style={{ textAlign: 'center', color: '#888' }}>इतिहास लोड हो रहा है...</p>
          ) : ledger.length === 0 ? (
            <p style={{ textAlign: 'center', color: '#888', padding: '20px 0' }}>कोई लेन-देन नहीं मिला</p>
          ) : (
            ledger.map((txn) => {
              const isCredit = txn.type === 'credit';
              const isPurchase = txn.type === 'purchase';
              const isPayment = txn.type === 'payment';
              const dateStr = new Date(txn.timestamp).toLocaleDateString('en-IN', {
                day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit'
              });

              const badgeIcon = isPurchase ? '🧾' : isCredit ? '🔴' : '🟢';
              const titleText = isPurchase
                ? `खरीदारी ₹${txn.amount} (नकद)`
                : isCredit
                ? `उधार +₹${txn.amount}`
                : `जमा -₹${txn.amount}`;

              return (
                <div key={txn.id} style={txnRowStyle}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                    <span style={{ fontSize: '20px' }}>{badgeIcon}</span>
                    <div>
                      <div style={{ fontWeight: '600', fontSize: '15px' }}>
                        {titleText}
                      </div>
                      <div style={{ fontSize: '11px', color: '#888' }}>{dateStr}</div>
                      {isPurchase && (
                        <div style={{ fontSize: '11px', color: '#2e7d32', marginTop: '2px' }}>
                          🛍️ {txn.item || (txn.bill?.items?.[0]?.name) || 'किराना सामान'} | नकद भुगतान
                        </div>
                      )}
                      {txn.voiceTranscript && (
                        <div style={{ fontSize: '11px', color: '#1a73e8', fontStyle: 'italic', marginTop: '2px' }}>
                          🎤 "{txn.voiceTranscript}"
                        </div>
                      )}
                    </div>
                  </div>
                  <div style={{ textAlign: 'right' }}>
                    <div style={{ fontSize: '12px', color: '#777' }}>बैलेंस</div>
                    <div style={{ fontWeight: '700', fontSize: '14px' }}>
                      ₹{txn.runningBalance?.toLocaleString('en-IN')}
                    </div>
                    {isPurchase && (
                      <button
                        style={viewBillBtnStyle}
                        onClick={() => setActiveBill(txn.bill || {
                          billNumber: txn.billNumber || `BILL-${txn.id.slice(-6)}`,
                          date: dateStr,
                          customerName: customer.name,
                          items: txn.items || [{ name: txn.item || 'सामान', quantity: 1, price: txn.amount, total: txn.amount }],
                          total: txn.amount,
                          paymentMode: 'cash',
                        })}
                        title="बिल देखें"
                      >
                        🧾 बिल देखें
                      </button>
                    )}
                    {onReverseTxn && !txn.reversesTxnId && !isPurchase && (
                      <button
                        style={undoLinkStyle}
                        onClick={() => onReverseTxn(txn.id)}
                        title="लेन-देन वापस लें"
                      >
                        रद्द करें
                      </button>
                    )}
                  </div>
                </div>
              );
            })
          )}
        </div>

        {/* Bill Modal when activeBill is selected */}
        {activeBill && (
          <BillModal
            isOpen={true}
            bill={activeBill}
            shop={{ name: shopName }}
            onClose={() => setActiveBill(null)}
          />
        )}
      </div>
    </div>
  );
}

const overlayStyle = {
  position: 'fixed',
  top: 0, left: 0, right: 0, bottom: 0,
  background: 'rgba(0,0,0,0.5)',
  display: 'flex',
  alignItems: 'flex-end',
  justifyContent: 'center',
  zIndex: 400,
};

const modalStyle = {
  background: '#ffffff',
  width: '100%',
  maxWidth: '540px',
  maxHeight: '88vh',
  borderTopLeftRadius: '20px',
  borderTopRightRadius: '20px',
  padding: '20px',
  display: 'flex',
  flexDirection: 'column',
  boxShadow: '0 -4px 24px rgba(0,0,0,0.2)',
};

const headerStyle = {
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'flex-start',
  paddingBottom: '12px',
  borderBottom: '1px solid #eee',
};

const closeBtnStyle = {
  background: 'none',
  border: 'none',
  fontSize: '20px',
  cursor: 'pointer',
  color: '#666',
};

const balanceCardStyle = {
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
  background: '#f8f9fa',
  borderRadius: '12px',
  padding: '16px',
  margin: '14px 0',
};

const overdueBadgeStyle = {
  display: 'inline-block',
  background: '#ffebee',
  color: '#c62828',
  fontSize: '11px',
  fontWeight: '600',
  padding: '2px 8px',
  borderRadius: '10px',
  marginTop: '4px',
};

const whatsappButtonStyle = {
  display: 'flex',
  alignItems: 'center',
  gap: '6px',
  background: '#25D366',
  color: 'white',
  padding: '10px 14px',
  borderRadius: '10px',
  fontWeight: '600',
  fontSize: '13px',
  textDecoration: 'none',
  boxShadow: '0 2px 6px rgba(37,211,102,0.3)',
};

const actionRowStyle = {
  display: 'flex',
  gap: '10px',
  marginBottom: '12px',
};

const creditBtnStyle = {
  flex: 1,
  padding: '12px',
  background: '#d32f2f',
  color: 'white',
  border: 'none',
  borderRadius: '10px',
  fontWeight: '600',
  fontSize: '14px',
  cursor: 'pointer',
};

const paymentBtnStyle = {
  flex: 1,
  padding: '12px',
  background: '#2e7d32',
  color: 'white',
  border: 'none',
  borderRadius: '10px',
  fontWeight: '600',
  fontSize: '14px',
  cursor: 'pointer',
};

const quickFormStyle = {
  background: '#eef2f6',
  padding: '12px',
  borderRadius: '10px',
  marginBottom: '12px',
};

const inputStyle = {
  flex: 1,
  padding: '10px',
  borderRadius: '8px',
  border: '1px solid #ccc',
  fontSize: '15px',
};

const submitBtnStyle = {
  padding: '10px 16px',
  background: '#1a73e8',
  color: 'white',
  border: 'none',
  borderRadius: '8px',
  fontWeight: '600',
  cursor: 'pointer',
};

const cancelBtnStyle = {
  padding: '10px 12px',
  background: '#888',
  color: 'white',
  border: 'none',
  borderRadius: '8px',
  cursor: 'pointer',
};

const timelineContainerStyle = {
  flex: 1,
  overflowY: 'auto',
  display: 'flex',
  flexDirection: 'column',
  gap: '8px',
  paddingRight: '4px',
};

const txnRowStyle = {
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
  padding: '10px 12px',
  background: '#fafafa',
  borderRadius: '10px',
  border: '1px solid #eee',
};

const undoLinkStyle = {
  background: 'none',
  border: 'none',
  color: '#c62828',
  fontSize: '11px',
  cursor: 'pointer',
  padding: '2px 0',
  textDecoration: 'underline',
};

const viewBillBtnStyle = {
  marginTop: '4px',
  padding: '3px 8px',
  background: '#e8f0fe',
  color: '#1a73e8',
  border: '1px solid #1a73e8',
  borderRadius: '6px',
  fontSize: '11px',
  fontWeight: '600',
  cursor: 'pointer',
};
