/**
 * Daily Reminder Review Modal — Voice Khata (Phase 8)
 *
 * Daily workflow for kirana owners to review and dispatch polite
 * WhatsApp reminder messages to overdue customers.
 *
 * Features:
 * - Scans all customers using reminderScheduler.js
 * - Displays pending reminders with kind ('first', 'repeat', 'weekly', 'high_due')
 * - Shows localized message preview
 * - One-click "WhatsApp भेजें" button opens wa.me link AND logs to reminder_log
 * - Respects customer opt-out and anti-spam cooldowns (3-day minimum repeat)
 */

import { useState, useEffect } from 'preact/hooks';
import { runReminderCheck } from '../logic/reminderScheduler.js';
import * as customerRepo from '../db/customerRepo.js';
import * as reminderLogRepo from '../db/reminderLogRepo.js';
import { buildWhatsAppLink } from '../logic/whatsappSender.js';

export function ReminderModal({
  isOpen,
  onClose,
  settings,
  templates,
  onReminderSent,
}) {
  if (!isOpen) return null;

  const [pendingReminders, setPendingReminders] = useState([]);
  const [sentMap, setSentMap] = useState({}); // customerId -> boolean
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadReminders();
  }, [isOpen]);

  const loadReminders = async () => {
    setLoading(true);
    try {
      const actions = await runReminderCheck(customerRepo, reminderLogRepo, settings, templates);
      setPendingReminders(actions || []);
    } catch (err) {
      console.error('Failed to run reminder check:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleSendReminder = async (action) => {
    const { customer, text, kind } = action;
    const url = buildWhatsAppLink(customer.phone, text);

    // 1. Open WhatsApp deep link
    window.open(url, '_blank', 'noopener,noreferrer');

    // 2. Audit log to IndexedDB reminder_log
    try {
      await reminderLogRepo.logReminder({
        customerId: customer.id,
        channel: 'whatsapp',
        kind,
        textSent: text,
        status: 'sent',
      });

      setSentMap((prev) => ({ ...prev, [customer.id]: true }));
      if (onReminderSent) onReminderSent(customer, kind);
    } catch (e) {
      console.error('Failed to log reminder:', e);
    }
  };

  const getKindLabel = (kind) => {
    switch (kind) {
      case 'first': return { label: 'पहला तगादा (1st Notice)', color: '#1976d2' };
      case 'repeat': return { label: 'अनुस्मारक (Repeat)', color: '#f57c00' };
      case 'weekly': return { label: 'साप्ताहिक सारांश (Weekly)', color: '#7b1fa2' };
      case 'high_due': return { label: 'अधिक बकाया (High Due)', color: '#d32f2f' };
      default: return { label: 'तगादा', color: '#555' };
    }
  };

  const pendingCount = pendingReminders.filter((r) => !sentMap[r.customer.id]).length;

  return (
    <div style={overlayStyle}>
      <div style={modalStyle}>
        {/* Header */}
        <div style={headerStyle}>
          <div>
            <h2 style={{ margin: 0, fontSize: '18px', color: '#111' }}>
              📢 आज के WhatsApp तगादे (Reminders)
            </h2>
            <p style={{ margin: '4px 0 0', fontSize: '12px', color: '#666' }}>
              कुल बाकीदार: {pendingReminders.length} | शेष: {pendingCount}
            </p>
          </div>
          <button style={closeBtnStyle} onClick={onClose}>✕</button>
        </div>

        {/* Content list */}
        <div style={contentListStyle}>
          {loading ? (
            <p style={{ textAlign: 'center', color: '#777', padding: '30px 0' }}>
              तगादा सूची तैयार हो रही है...
            </p>
          ) : pendingReminders.length === 0 ? (
            <div style={emptyStateStyle}>
              <span style={{ fontSize: '36px' }}>🎉</span>
              <p style={{ fontWeight: '600', margin: '8px 0 2px' }}>
                आज किसी भी ग्राहक का तगादा बाकी नहीं है!
              </p>
              <p style={{ fontSize: '12px', color: '#666', margin: 0 }}>
                सभी ग्राहक 7-दिन की सीमा के अंदर हैं या हाल ही में याद दिला दिया गया है।
              </p>
            </div>
          ) : (
            pendingReminders.map((action) => {
              const { customer, text, kind } = action;
              const isSent = Boolean(sentMap[customer.id]);
              const badge = getKindLabel(kind);

              return (
                <div key={customer.id} style={reminderCardStyle}>
                  <div style={reminderHeaderRow}>
                    <div>
                      <span style={{ fontWeight: '700', fontSize: '15px' }}>
                        {customer.name}
                      </span>
                      <span style={{ fontSize: '12px', color: '#666', marginLeft: '8px' }}>
                        📍 {customer.city} | 📞 {customer.phone}
                      </span>
                    </div>
                    <span style={{ ...kindBadgeStyle, background: badge.color }}>
                      {badge.label}
                    </span>
                  </div>

                  <div style={dueDetailsRow}>
                    <span>
                      कुल बकाया: <strong>₹{customer.totalDue?.toLocaleString('en-IN')}</strong>
                    </span>
                    <span style={{ fontSize: '12px', color: '#c62828' }}>
                      ({Math.floor((Date.now() - new Date(customer.dueSince).getTime()) / (1000 * 60 * 60 * 24))} दिन से बाकी)
                    </span>
                  </div>

                  {/* Message preview */}
                  <div style={messageBoxStyle}>
                    💬 "{text}"
                  </div>

                  {/* Action button */}
                  <div style={{ textAlign: 'right', marginTop: '10px' }}>
                    {isSent ? (
                      <span style={sentBadgeStyle}>
                        ✅ भेजा गया (Dispatched)
                      </span>
                    ) : (
                      <button
                        style={sendBtnStyle}
                        onClick={() => handleSendReminder(action)}
                      >
                        💬 WhatsApp पर भेजें
                      </button>
                    )}
                  </div>
                </div>
              );
            })
          )}
        </div>

        {/* Footer */}
        <div style={footerStyle}>
          <button style={closeFooterBtnStyle} onClick={onClose}>
            पूर्ण (Close)
          </button>
        </div>
      </div>
    </div>
  );
}

const overlayStyle = {
  position: 'fixed',
  top: 0, left: 0, right: 0, bottom: 0,
  background: 'rgba(0,0,0,0.5)',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  zIndex: 550,
  padding: '16px',
};

const modalStyle = {
  background: '#ffffff',
  width: '100%',
  maxWidth: '560px',
  maxHeight: '88vh',
  borderRadius: '16px',
  display: 'flex',
  flexDirection: 'column',
  boxShadow: '0 8px 30px rgba(0,0,0,0.22)',
};

const headerStyle = {
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
  padding: '16px 20px',
  borderBottom: '1px solid #eee',
};

const closeBtnStyle = {
  background: 'none',
  border: 'none',
  fontSize: '20px',
  cursor: 'pointer',
  color: '#888',
};

const contentListStyle = {
  flex: 1,
  overflowY: 'auto',
  padding: '16px 20px',
  display: 'flex',
  flexDirection: 'column',
  gap: '12px',
};

const reminderCardStyle = {
  background: '#fafafa',
  borderRadius: '12px',
  border: '1px solid #e0e0e0',
  padding: '14px',
};

const reminderHeaderRow = {
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
  marginBottom: '6px',
};

const kindBadgeStyle = {
  color: 'white',
  fontSize: '11px',
  fontWeight: '700',
  padding: '3px 8px',
  borderRadius: '6px',
};

const dueDetailsRow = {
  fontSize: '13px',
  color: '#444',
  marginBottom: '8px',
  display: 'flex',
  gap: '8px',
  alignItems: 'center',
};

const messageBoxStyle = {
  background: '#e8f5e9',
  color: '#1b5e20',
  padding: '10px 12px',
  borderRadius: '8px',
  fontSize: '13px',
  lineHeight: 1.4,
  fontStyle: 'italic',
};

const sendBtnStyle = {
  background: '#25D366',
  color: 'white',
  border: 'none',
  padding: '8px 16px',
  borderRadius: '8px',
  fontWeight: '600',
  fontSize: '13px',
  cursor: 'pointer',
  boxShadow: '0 2px 6px rgba(37,211,102,0.3)',
};

const sentBadgeStyle = {
  display: 'inline-block',
  background: '#c8e6c9',
  color: '#2e7d32',
  padding: '6px 12px',
  borderRadius: '6px',
  fontWeight: '600',
  fontSize: '12px',
};

const emptyStateStyle = {
  textAlign: 'center',
  padding: '40px 20px',
  background: '#f9f9f9',
  borderRadius: '12px',
};

const footerStyle = {
  padding: '12px 20px',
  borderTop: '1px solid #eee',
  textAlign: 'right',
};

const closeFooterBtnStyle = {
  padding: '10px 20px',
  background: '#1a73e8',
  color: 'white',
  border: 'none',
  borderRadius: '8px',
  fontWeight: '600',
  fontSize: '14px',
  cursor: 'pointer',
};
