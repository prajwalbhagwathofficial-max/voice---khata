/**
 * Warning Modal Component — Voice Khata
 *
 * Displays critical business warnings:
 * 1. High-due limit breach (e.g. > ₹10,000 credit) with explicit merchant confirmation
 * 2. Geofence distance warning when merchant is far from the registered shop location
 */

export function WarningModal({
  isOpen,
  type = 'high_due', // 'high_due' | 'distance'
  data = {},
  onConfirm,
  onCancel,
}) {
  if (!isOpen) return null;

  return (
    <div style={overlayStyle}>
      <div style={modalStyle}>
        <div style={iconCircleStyle(type)}>
          {type === 'high_due' ? '⚠️' : '📍'}
        </div>

        <h3 style={titleStyle}>
          {type === 'high_due' ? 'अधिक बकाया चेतावनी (High Due Warning)' : 'दुकान से दूरी की चेतावनी (Distance Warning)'}
        </h3>

        <div style={contentStyle}>
          {type === 'high_due' ? (
            <p style={{ margin: 0, lineHeight: 1.5 }}>
              <strong>{data.name || 'ग्राहक'}</strong> का कुल बकाया ₹<strong>{data.newTotal?.toLocaleString('en-IN')}</strong> हो जाएगा,
              जो सुरक्षित सीमा (₹{data.limit?.toLocaleString('en-IN')}) से अधिक है।
              <br /><br />
              क्या आप फिर भी यह उधार जोड़ना चाहते हैं?
            </p>
          ) : (
            <p style={{ margin: 0, lineHeight: 1.5 }}>
              आप दुकान से लगभग <strong>{data.distanceKm} किमी</strong> दूर हैं।
              <br />
              दुकान की सीमा <strong>{data.limitKm || 5} किमी</strong> है।
            </p>
          )}
        </div>

        <div style={buttonRowStyle}>
          <button style={confirmBtnStyle} onClick={onConfirm}>
            {type === 'high_due' ? 'हाँ, उधार जोड़ें (Confirm)' : 'ठीक है (OK)'}
          </button>
          {type === 'high_due' && (
            <button style={cancelBtnStyle} onClick={onCancel}>
              रद्द करें (Cancel)
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

const overlayStyle = {
  position: 'fixed',
  top: 0, left: 0, right: 0, bottom: 0,
  background: 'rgba(0,0,0,0.6)',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  zIndex: 600,
  padding: '16px',
};

const modalStyle = {
  background: '#ffffff',
  width: '100%',
  maxWidth: '420px',
  borderRadius: '16px',
  padding: '24px',
  textAlign: 'center',
  boxShadow: '0 8px 30px rgba(0,0,0,0.25)',
};

const iconCircleStyle = (type) => ({
  width: '56px',
  height: '56px',
  borderRadius: '50%',
  background: type === 'high_due' ? '#fff3e0' : '#e3f2fd',
  color: type === 'high_due' ? '#ef6c00' : '#1976d2',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  fontSize: '28px',
  margin: '0 auto 16px',
});

const titleStyle = {
  margin: '0 0 12px',
  fontSize: '18px',
  color: '#222',
};

const contentStyle = {
  fontSize: '14px',
  color: '#555',
  marginBottom: '20px',
  textAlign: 'left',
  background: '#fafafa',
  padding: '12px',
  borderRadius: '8px',
  border: '1px solid #eee',
};

const buttonRowStyle = {
  display: 'flex',
  gap: '10px',
};

const confirmBtnStyle = {
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

const cancelBtnStyle = {
  flex: 1,
  padding: '12px',
  background: '#e0e0e0',
  color: '#333',
  border: 'none',
  borderRadius: '10px',
  fontWeight: '600',
  fontSize: '14px',
  cursor: 'pointer',
};
