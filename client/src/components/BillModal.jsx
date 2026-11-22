/**
 * Bill / Cash Memo Modal Component — Voice Khata
 *
 * Displays a clean, printable Kirana Cash Memo for normal purchases.
 * Saved directly under the transaction for permanent record retrieval.
 */

export function BillModal({
  isOpen,
  bill,
  shop = {},
  onClose,
}) {
  if (!isOpen || !bill) return null;

  const handlePrint = () => {
    window.print();
  };

  const handleShare = () => {
    const text = `🧾 *${shop.name || 'दुकानदार'} - कैश मेमो*\nबिल संख्या: ${bill.billNumber}\nतारीख: ${bill.date} ${bill.time}\nग्राहक: ${bill.customerName}\nकुल राशि: ₹${bill.total} (नकद भुगतान)\nधन्यवाद! फिर पधारें।`;
    if (navigator.share) {
      navigator.share({ title: `बिल: ${bill.billNumber}`, text }).catch(() => {});
    } else {
      window.open(`https://wa.me/?text=${encodeURIComponent(text)}`, '_blank');
    }
  };

  const items = bill.items && bill.items.length > 0
    ? bill.items
    : [{ name: 'दुकान सामान (General Items)', quantity: 1, price: bill.total, total: bill.total }];

  return (
    <div style={overlayStyle}>
      <div style={modalStyle}>
        {/* Actions header */}
        <div style={topActionsStyle}>
          <span style={{ fontSize: '13px', fontWeight: '600', color: '#666' }}>🧾 बिल रसीद (Cash Receipt)</span>
          <div style={{ display: 'flex', gap: '8px' }}>
            <button onClick={handlePrint} style={actionPillStyle} title="प्रिंट करें">
              🖨️ प्रिंट
            </button>
            <button onClick={handleShare} style={actionPillStyle} title="शेयर करें">
              📤 शेयर
            </button>
            <button onClick={onClose} style={closeBtnStyle} title="बंद करें">
              ✕
            </button>
          </div>
        </div>

        {/* Printable Bill Area */}
        <div style={billPaperStyle} id="printable-bill">
          {/* Shop Header */}
          <div style={{ textAlign: 'center', borderBottom: '1.5px dashed #333', paddingBottom: '10px' }}>
            <h2 style={{ margin: '0 0 4px', fontSize: '20px', letterSpacing: '0.5px' }}>
              🏪 {shop.name || 'किराना एवं जनरल स्टोर'}
            </h2>
            <div style={{ fontSize: '12px', color: '#555' }}>
              {shop.city ? `📍 ${shop.city}` : 'लोकल बाज़ार'}
            </div>
            <div style={cashMemoTitleStyle}>
              कैश मेमो / CASH MEMO
            </div>
          </div>

          {/* Meta details */}
          <div style={metaGridStyle}>
            <div>
              <div style={metaLabelStyle}>बिल संख्या (Bill No):</div>
              <div style={{ fontWeight: '700', fontSize: '14px' }}>{bill.billNumber || 'BILL-0001'}</div>
            </div>
            <div style={{ textAlign: 'right' }}>
              <div style={metaLabelStyle}>दिनांक व समय:</div>
              <div style={{ fontSize: '12px' }}>{bill.date} | {bill.time}</div>
            </div>
            <div style={{ gridColumn: 'span 2', marginTop: '4px' }}>
              <div style={metaLabelStyle}>ग्राहक (Customer):</div>
              <div style={{ fontWeight: '600', fontSize: '14px' }}>{bill.customerName || 'नकद ग्राहक'}</div>
            </div>
          </div>

          {/* Items Table */}
          <table style={tableStyle}>
            <thead>
              <tr style={tableHeaderRowStyle}>
                <th style={{ textAlign: 'left', padding: '6px 4px', width: '50%' }}>सामान (Item)</th>
                <th style={{ textAlign: 'center', padding: '6px 4px', width: '20%' }}>मात्रा</th>
                <th style={{ textAlign: 'right', padding: '6px 4px', width: '30%' }}>रुपये (Total)</th>
              </tr>
            </thead>
            <tbody>
              {items.map((it, idx) => (
                <tr key={idx} style={{ borderBottom: '1px solid #eee' }}>
                  <td style={{ padding: '6px 4px', fontSize: '13px' }}>{it.name || 'सामान'}</td>
                  <td style={{ padding: '6px 4px', textAlign: 'center', fontSize: '13px' }}>{it.quantity || 1}</td>
                  <td style={{ padding: '6px 4px', textAlign: 'right', fontWeight: '600', fontSize: '13px' }}>
                    ₹{(it.total || it.price || bill.total).toLocaleString('en-IN')}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {/* Total & Paid Badge */}
          <div style={totalContainerStyle}>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '16px', fontWeight: '800' }}>
              <span>कुल राशि (Total):</span>
              <span>₹{(bill.total || 0).toLocaleString('en-IN')}</span>
            </div>
            <div style={paidBadgeStyle}>
              ✓ नकद भुगतान प्राप्त (PAID IN CASH)
            </div>
          </div>

          {/* Footer note */}
          <div style={footerStyle}>
            ✨ धन्यवाद! फिर पधारें! (Thank You! Visit Again) ✨
            <div style={{ fontSize: '10px', color: '#999', marginTop: '4px' }}>
              Voice Khata द्वारा जनरेट किया गया
            </div>
          </div>
        </div>

        {/* Bottom Close Button */}
        <button onClick={onClose} style={doneBtnStyle}>
          ठीक है (Done)
        </button>
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
  zIndex: 650,
  backdropFilter: 'blur(3px)',
  padding: '16px',
};

const modalStyle = {
  background: '#ffffff',
  width: '100%',
  maxWidth: '420px',
  borderRadius: '16px',
  padding: '16px',
  display: 'flex',
  flexDirection: 'column',
  boxShadow: '0 12px 36px rgba(0,0,0,0.25)',
  maxHeight: '92vh',
  overflowY: 'auto',
};

const topActionsStyle = {
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
  marginBottom: '12px',
};

const actionPillStyle = {
  background: '#f0f4f9',
  border: '1px solid #d0d7de',
  borderRadius: '16px',
  padding: '4px 10px',
  fontSize: '12px',
  fontWeight: '600',
  cursor: 'pointer',
};

const closeBtnStyle = {
  background: '#f0f0f0',
  border: 'none',
  fontSize: '14px',
  cursor: 'pointer',
  padding: '4px 8px',
  borderRadius: '50%',
  color: '#666',
};

const billPaperStyle = {
  background: '#fffdfa',
  border: '1px solid #d8d3c5',
  borderRadius: '8px',
  padding: '16px',
  boxShadow: 'inset 0 0 12px rgba(0,0,0,0.02)',
};

const cashMemoTitleStyle = {
  display: 'inline-block',
  background: '#222',
  color: '#fff',
  fontSize: '10px',
  fontWeight: '700',
  letterSpacing: '1px',
  padding: '2px 8px',
  borderRadius: '4px',
  marginTop: '6px',
};

const metaGridStyle = {
  display: 'grid',
  gridTemplateColumns: '1fr 1fr',
  gap: '4px',
  margin: '12px 0',
  fontSize: '12px',
  borderBottom: '1px dashed #ccc',
  paddingBottom: '8px',
};

const metaLabelStyle = {
  fontSize: '11px',
  color: '#777',
};

const tableStyle = {
  width: '100%',
  borderCollapse: 'collapse',
  margin: '10px 0',
};

const tableHeaderRowStyle = {
  borderBottom: '1.5px solid #333',
  fontSize: '12px',
  color: '#333',
};

const totalContainerStyle = {
  borderTop: '1.5px solid #333',
  paddingTop: '8px',
  marginTop: '8px',
};

const paidBadgeStyle = {
  background: '#e6f4ea',
  color: '#137333',
  fontWeight: '700',
  fontSize: '11px',
  textAlign: 'center',
  padding: '5px',
  borderRadius: '6px',
  marginTop: '8px',
  border: '1px solid #ceead6',
};

const footerStyle = {
  textAlign: 'center',
  marginTop: '14px',
  fontSize: '11px',
  color: '#555',
  borderTop: '1px dashed #ccc',
  paddingTop: '8px',
};

const doneBtnStyle = {
  marginTop: '14px',
  padding: '10px',
  background: '#1a73e8',
  color: '#ffffff',
  border: 'none',
  borderRadius: '8px',
  fontWeight: '700',
  cursor: 'pointer',
  fontSize: '14px',
};
