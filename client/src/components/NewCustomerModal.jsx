/**
 * New Customer Modal Component — Voice Khata
 *
 * Form for onboarding customers:
 * - Name (full Unicode support for Devanagari / Kannada / English)
 * - City / Neighborhood
 * - Phone number
 * - WhatsApp reminder opt-in consent
 * - Optional initial credit amount
 */

import { useState } from 'preact/hooks';

export function NewCustomerModal({
  initialData = {},
  isOpen,
  onClose,
  onSubmit,
}) {
  if (!isOpen) return null;

  const [name, setName] = useState(initialData.name || '');
  const [city, setCity] = useState(initialData.city || '');
  const [phone, setPhone] = useState(initialData.phone || '');
  const [whatsappConsent, setWhatsappConsent] = useState(true);
  const [initialCredit, setInitialCredit] = useState(initialData.amount ? String(initialData.amount) : '');
  const [error, setError] = useState('');

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!name.trim()) {
      setError('कृपया ग्राहक का नाम दर्ज करें');
      return;
    }

    const cleanedPhone = phone.replace(/\D/g, '');
    if (phone.trim() && cleanedPhone.length < 10) {
      setError('सही 10-अंकीय फ़ोन नंबर दर्ज करें');
      return;
    }

    setError('');
    onSubmit({
      name: name.trim(),
      city: city.trim() || 'लोकल',
      phone: cleanedPhone || '0000000000',
      whatsappConsent,
      initialCredit: parseFloat(initialCredit) || 0,
    });
  };

  return (
    <div style={overlayStyle}>
      <div style={modalStyle}>
        <div style={headerStyle}>
          <h2 style={{ margin: 0, fontSize: '18px' }}>👤 नया ग्राहक जोड़ें (New Customer)</h2>
          <button style={closeBtnStyle} onClick={onClose}>✕</button>
        </div>

        {error && <div style={errorBannerStyle}>⚠️ {error}</div>}

        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
          <div>
            <label style={labelStyle}>ग्राहक का नाम (Customer Name) *</label>
            <input
              type="text"
              placeholder="e.g. Ramesh / रमेश / ರಮೇಶ್"
              value={name}
              onInput={(e) => setName(e.target.value)}
              style={inputStyle}
              required
              autoFocus
            />
          </div>

          <div>
            <label style={labelStyle}>शहर / गाँव / मोहल्ला (City / Area) *</label>
            <input
              type="text"
              placeholder="e.g. Kengeri / केंगेरी"
              value={city}
              onInput={(e) => setCity(e.target.value)}
              style={inputStyle}
              required
            />
          </div>

          <div>
            <label style={labelStyle}>फ़ोन नंबर (Mobile Number)</label>
            <input
              type="tel"
              placeholder="10-अंक (e.g. 9876543210)"
              value={phone}
              onInput={(e) => setPhone(e.target.value)}
              style={inputStyle}
            />
          </div>

          <div>
            <label style={labelStyle}>शुरुआती उधार राशि (Initial Credit - Optional)</label>
            <input
              type="number"
              placeholder="₹ 0"
              value={initialCredit}
              onInput={(e) => setInitialCredit(e.target.value)}
              style={inputStyle}
            />
          </div>

          <label style={checkboxRowStyle}>
            <input
              type="checkbox"
              checked={whatsappConsent}
              onChange={(e) => setWhatsappConsent(e.target.checked)}
              style={{ width: '18px', height: '18px' }}
            />
            <span style={{ fontSize: '14px', color: '#333' }}>
              WhatsApp पर याद दिलाना स्वीकार है (Opt-in Reminder)
            </span>
          </label>

          <div style={buttonRowStyle}>
            <button type="submit" style={saveBtnStyle}>
              ग्राहक सुरक्षित करें (Save)
            </button>
            <button type="button" style={cancelBtnStyle} onClick={onClose}>
              रद्द करें
            </button>
          </div>
        </form>
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
  zIndex: 500,
  padding: '16px',
};

const modalStyle = {
  background: '#ffffff',
  width: '100%',
  maxWidth: '440px',
  borderRadius: '16px',
  padding: '20px',
  boxShadow: '0 8px 30px rgba(0,0,0,0.2)',
};

const headerStyle = {
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
  marginBottom: '16px',
};

const closeBtnStyle = {
  background: 'none',
  border: 'none',
  fontSize: '20px',
  cursor: 'pointer',
  color: '#888',
};

const labelStyle = {
  display: 'block',
  fontSize: '12px',
  fontWeight: '600',
  color: '#555',
  marginBottom: '4px',
};

const inputStyle = {
  width: '100%',
  padding: '10px 14px',
  borderRadius: '8px',
  border: '1px solid #ccc',
  fontSize: '15px',
  boxSizing: 'border-box',
};

const checkboxRowStyle = {
  display: 'flex',
  alignItems: 'center',
  gap: '8px',
  margin: '8px 0',
  cursor: 'pointer',
};

const errorBannerStyle = {
  background: '#ffebee',
  color: '#c62828',
  padding: '8px 12px',
  borderRadius: '8px',
  fontSize: '13px',
  marginBottom: '12px',
};

const buttonRowStyle = {
  display: 'flex',
  gap: '10px',
  marginTop: '10px',
};

const saveBtnStyle = {
  flex: 1,
  padding: '12px',
  background: '#1a73e8',
  color: 'white',
  border: 'none',
  borderRadius: '10px',
  fontWeight: '600',
  fontSize: '15px',
  cursor: 'pointer',
};

const cancelBtnStyle = {
  padding: '12px 16px',
  background: '#eee',
  color: '#555',
  border: 'none',
  borderRadius: '10px',
  cursor: 'pointer',
};
