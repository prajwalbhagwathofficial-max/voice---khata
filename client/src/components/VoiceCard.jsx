/**
 * Voice Interaction Card — Voice Khata
 *
 * Real-time feedback overlay showing live speech transcripts,
 * extracted intent, amounts, customer identity, and spoken responses.
 */

export function VoiceCard({
  transcript = '',
  isFinal = false,
  status = 'idle', // 'listening' | 'processing' | 'speaking' | 'success' | 'error'
  lastResult = null,
  onClose,
  onConfirm,
  onCancel,
}) {
  if (status === 'idle' && !transcript && !lastResult) return null;

  const getIntentBadge = (intent) => {
    switch (intent) {
      case 'credit': return { label: '➕ उधार (Credit)', color: '#d32f2f' };
      case 'payment': return { label: '💰 जमा (Payment)', color: '#2e7d32' };
      case 'query': return { label: '🔍 हिसाब (Query)', color: '#1976d2' };
      case 'greeting': return { label: '👋 नमस्ते (Greeting)', color: '#0288d1' };
      case 'chat': return { label: '💬 बातचीत (Assistant)', color: '#7b1fa2' };
      case 'correction': return { label: '✏️ सुधार (Correction)', color: '#ed6c02' };
      case 'undo': return { label: '↩️ रद्द (Undo)', color: '#7b1fa2' };
      default: return null;
    }
  };

  const badge = lastResult?.intent ? getIntentBadge(lastResult.intent) : null;

  return (
    <div style={cardContainerStyle}>
      <div style={cardHeaderStyle}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span style={indicatorDotStyle(status)} />
          <span style={{ fontWeight: '600', fontSize: '14px' }}>
            {status === 'listening' && 'सुन रहे हैं...'}
            {status === 'processing' && 'विश्लेषण हो रहा है...'}
            {status === 'speaking' && 'सहायक बोल रहा है...'}
            {status === 'success' && 'कार्य पूरा हुआ'}
            {status === 'error' && 'सहायक सुन रहा है...'}
          </span>
        </div>
        {onClose && (
          <button style={closeButtonStyle} onClick={onClose} aria-label="Close">✕</button>
        )}
      </div>

      {/* Spoken Transcript */}
      <div style={transcriptAreaStyle}>
        {transcript ? (
          <p style={{ margin: 0, fontSize: '16px', color: '#111' }}>
            "{transcript}"
            {!isFinal && <span style={blinkingCursorStyle}>|</span>}
          </p>
        ) : (
          <p style={{ margin: 0, fontSize: '14px', color: '#888', fontStyle: 'italic' }}>
            स्पष्ट आवाज में बोलिए (जैसे: "रमेश 500 रुपये उधार")
          </p>
        )}
      </div>

      {/* Extracted Details & Badge */}
      {lastResult && (
        <div style={detailsAreaStyle}>
          {badge && (
            <span style={{ ...badgeStyle, background: badge.color }}>
              {badge.label}
            </span>
          )}

          {lastResult.entities?.customerName && (
            <span style={entityChipStyle}>
              👤 {lastResult.entities.customerName}
            </span>
          )}

          {lastResult.entities?.amount !== null && lastResult.entities?.amount !== undefined && (
            <span style={entityChipStyle}>
              ₹ {lastResult.entities.amount}
            </span>
          )}

          {lastResult.message && (
            <div style={responseBubbleStyle}>
              🗣️ {lastResult.message}
            </div>
          )}

          {/* Confirmation buttons if high-due or pending check */}
          {lastResult.needsConfirmation && (
            <div style={confirmActionRowStyle}>
              <button style={confirmBtnStyle} onClick={onConfirm}>
                स्वीकार करें (Confirm)
              </button>
              <button style={cancelBtnStyle} onClick={onCancel}>
                रद्द करें (Cancel)
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

const cardContainerStyle = {
  position: 'fixed',
  bottom: '104px',
  left: '16px',
  right: '16px',
  maxWidth: '480px',
  margin: '0 auto',
  background: '#ffffff',
  borderRadius: '16px',
  boxShadow: '0 8px 30px rgba(0,0,0,0.18)',
  padding: '16px',
  zIndex: 250,
  border: '1px solid #e0e0e0',
  animation: 'slideUp 0.25s ease-out',
};

const cardHeaderStyle = {
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
  marginBottom: '10px',
};

const indicatorDotStyle = (status) => ({
  width: '10px',
  height: '10px',
  borderRadius: '50%',
  background:
    status === 'listening' ? '#e53935' :
    status === 'processing' ? '#f57c00' :
    status === 'speaking' ? '#2e7d32' :
    status === 'error' ? '#d32f2f' : '#1a73e8',
  display: 'inline-block',
});

const closeButtonStyle = {
  background: 'none',
  border: 'none',
  fontSize: '16px',
  color: '#888',
  cursor: 'pointer',
  padding: '4px',
};

const transcriptAreaStyle = {
  background: '#f8f9fa',
  borderRadius: '8px',
  padding: '12px',
  minHeight: '44px',
  marginBottom: '10px',
};

const blinkingCursorStyle = {
  display: 'inline-block',
  width: '2px',
  color: '#1a73e8',
  animation: 'blink 1s infinite',
};

const detailsAreaStyle = {
  display: 'flex',
  flexWrap: 'wrap',
  gap: '8px',
  alignItems: 'center',
};

const badgeStyle = {
  color: '#fff',
  fontSize: '12px',
  fontWeight: '600',
  padding: '4px 8px',
  borderRadius: '6px',
};

const entityChipStyle = {
  background: '#e8f0fe',
  color: '#1a73e8',
  fontSize: '13px',
  fontWeight: '600',
  padding: '4px 10px',
  borderRadius: '12px',
};

const responseBubbleStyle = {
  width: '100%',
  marginTop: '8px',
  background: '#e8f5e9',
  color: '#1b5e20',
  padding: '8px 12px',
  borderRadius: '8px',
  fontSize: '14px',
};

const confirmActionRowStyle = {
  display: 'flex',
  gap: '8px',
  width: '100%',
  marginTop: '10px',
};

const confirmBtnStyle = {
  flex: 1,
  padding: '10px',
  background: '#2e7d32',
  color: 'white',
  border: 'none',
  borderRadius: '8px',
  fontWeight: '600',
  cursor: 'pointer',
};

const cancelBtnStyle = {
  flex: 1,
  padding: '10px',
  background: '#757575',
  color: 'white',
  border: 'none',
  borderRadius: '8px',
  fontWeight: '600',
  cursor: 'pointer',
};
