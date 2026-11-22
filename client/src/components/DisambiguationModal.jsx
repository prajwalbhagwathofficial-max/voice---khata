/**
 * Disambiguation Modal Component — Voice Khata
 *
 * Appears when multiple customers match the spoken/searched name.
 * Allows the shopkeeper to select the intended customer by:
 *   - Clicking/tapping the customer card
 *   - Speaking the city/locality (e.g. "Kengeri wala")
 *   - Speaking phone digits (e.g. "8317")
 */

export function DisambiguationModal({
  isOpen,
  customers = [],
  customerName = '',
  amount = null,
  intent = 'credit',
  onSelect,
  onClose,
}) {
  if (!isOpen || !customers || customers.length === 0) return null;

  const maskPhone = (phone) => {
    if (!phone) return 'फ़ोन नहीं';
    const clean = phone.replace(/\D/g, '');
    if (clean.length < 4) return clean;
    return `***${clean.slice(-4)}`;
  };

  const getActionLabel = () => {
    if (intent === 'credit') return amount ? `₹${amount} उधार जोड़ें` : 'उधार दर्ज करें';
    if (intent === 'payment') return amount ? `₹${amount} जमा दर्ज करें` : 'जमा दर्ज करें';
    if (intent === 'purchase') return amount ? `₹${amount} की खरीदारी` : 'खरीदारी बिल';
    return 'खाता देखें';
  };

  return (
    <div style={overlayStyle}>
      <div style={modalStyle}>
        <div style={headerStyle}>
          <div>
            <h2 style={{ margin: 0, fontSize: '18px', color: '#1a237e' }}>
              👥 ग्राहक चुनें (Select Customer)
            </h2>
            <p style={{ margin: '4px 0 0', fontSize: '13px', color: '#555' }}>
              <strong>"{customerName || 'ग्राहक'}"</strong> नाम के {customers.length} ग्राहक मिले
            </p>
          </div>
          <button style={closeBtnStyle} onClick={onClose} title="रद्द करें">✕</button>
        </div>

        <div style={actionNoticeStyle}>
          💡 <strong>{getActionLabel()}</strong> के लिए सही ग्राहक पर टैप करें:
        </div>

        <div style={listContainerStyle}>
          {customers.map((c, idx) => {
            const hasDue = (c.totalDue || 0) > 0;
            return (
              <div
                key={c.id || idx}
                style={customerCardStyle}
                onClick={() => onSelect(c)}
              >
                <div style={{ flex: 1 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <span style={{ fontWeight: '700', fontSize: '16px', color: '#222' }}>
                      {c.name}
                    </span>
                    <span style={cityBadgeStyle}>
                      📍 {c.city || 'लोकल'}
                    </span>
                  </div>
                  <div style={{ fontSize: '13px', color: '#666', marginTop: '4px' }}>
                    📞 {maskPhone(c.phone)}
                    {c.id && <span style={{ marginLeft: '8px', color: '#999', fontSize: '11px' }}>ID: {c.id}</span>}
                  </div>
                </div>

                <div style={{ textAlign: 'right' }}>
                  <div style={{ fontSize: '11px', color: '#777' }}>बकाया</div>
                  <div style={{
                    fontWeight: '700',
                    fontSize: '15px',
                    color: hasDue ? '#d32f2f' : '#2e7d32',
                  }}>
                    ₹{(c.totalDue || 0).toLocaleString('en-IN')}
                  </div>
                  <button style={selectBtnStyle}>चुनें →</button>
                </div>
              </div>
            );
          })}
        </div>

        <div style={footerHelpStyle}>
          🎙️ <em>सुझाव: आप माइक दबाकर शहर (जैसे "केंगेरी") या फ़ोन नंबर के अंक भी बोल सकते हैं।</em>
        </div>
      </div>
    </div>
  );
}

const overlayStyle = {
  position: 'fixed',
  top: 0, left: 0, right: 0, bottom: 0,
  background: 'rgba(0, 0, 0, 0.65)',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  zIndex: 600,
  backdropFilter: 'blur(3px)',
  padding: '16px',
};

const modalStyle = {
  background: '#ffffff',
  width: '100%',
  maxWidth: '480px',
  borderRadius: '16px',
  padding: '20px',
  display: 'flex',
  flexDirection: 'column',
  boxShadow: '0 12px 36px rgba(0,0,0,0.25)',
  animation: 'slideUp 0.25s ease-out',
  maxHeight: '85vh',
};

const headerStyle = {
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'flex-start',
  paddingBottom: '12px',
  borderBottom: '1px solid #e0e0e0',
};

const closeBtnStyle = {
  background: '#f0f0f0',
  border: 'none',
  fontSize: '16px',
  cursor: 'pointer',
  padding: '6px 10px',
  borderRadius: '50%',
  color: '#666',
};

const actionNoticeStyle = {
  background: '#e8f0fe',
  color: '#1a73e8',
  padding: '8px 12px',
  borderRadius: '8px',
  fontSize: '13px',
  marginTop: '12px',
  marginBottom: '8px',
};

const listContainerStyle = {
  display: 'flex',
  flexDirection: 'column',
  gap: '10px',
  overflowY: 'auto',
  maxHeight: '50vh',
  padding: '4px 2px',
};

const customerCardStyle = {
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
  padding: '12px 14px',
  borderRadius: '12px',
  border: '1.5px solid #e0e0e0',
  background: '#fafafa',
  cursor: 'pointer',
  transition: 'all 0.15s ease',
};

const cityBadgeStyle = {
  fontSize: '11px',
  fontWeight: '600',
  color: '#1a73e8',
  background: '#e8f0fe',
  padding: '2px 8px',
  borderRadius: '12px',
};

const selectBtnStyle = {
  marginTop: '4px',
  padding: '3px 8px',
  fontSize: '11px',
  fontWeight: '600',
  background: '#1a73e8',
  color: '#ffffff',
  border: 'none',
  borderRadius: '6px',
  cursor: 'pointer',
};

const footerHelpStyle = {
  marginTop: '14px',
  paddingTop: '10px',
  borderTop: '1px solid #f0f0f0',
  fontSize: '12px',
  color: '#666',
  textAlign: 'center',
};
