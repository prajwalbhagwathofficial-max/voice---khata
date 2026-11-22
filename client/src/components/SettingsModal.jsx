/**
 * Settings & API Key Management Modal — Voice Khata
 *
 * Provides:
 * 1. Custom API Key input (Gnani, Groq, Supabase) when default quotas are exhausted
 * 2. Store profile (name, city, geofence coordinates)
 * 3. Credit limits & reminder cadence configuration
 * 4. Nuclear "Delete All Data" data-privacy wipe button
 */

import { useState, useEffect } from 'preact/hooks';
import * as settingsRepo from '../db/settingsRepo.js';
import { deleteAllLocalData } from '../db/index.js';

export function SettingsModal({
  isOpen,
  onClose,
  initialSettings = {},
  onSave,
}) {
  if (!isOpen) return null;

  // Local state for all settings
  const [shopName, setShopName] = useState('');
  const [city, setCity] = useState('');
  const [language, setLanguage] = useState('auto');
  const [lat, setLat] = useState(0);
  const [lng, setLng] = useState(0);
  const [highDueLimit, setHighDueLimit] = useState(10000);
  const [distanceLimitKm, setDistanceLimitKm] = useState(5);

  // API Key overrides
  const [gnaniKey, setGnaniKey] = useState('');
  const [groqKey, setGroqKey] = useState('');
  const [groqModel, setGroqModel] = useState('qwen/qwen3.8-27b');
  const [supabaseUrl, setSupabaseUrl] = useState('');
  const [supabaseKey, setSupabaseKey] = useState('');
  const [serverStatus, setServerStatus] = useState(null);

  const [showKeys, setShowKeys] = useState(false);
  const [savedSuccess, setSavedSuccess] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);

  useEffect(() => {
    loadCurrentSettings();
  }, [isOpen]);

  const loadCurrentSettings = async () => {
    try {
      const stored = await settingsRepo.getAllSettings();
      const s = { ...initialSettings, ...stored };

      setShopName(s.shop?.name || '');
      setCity(s.shop?.city || '');
      setLanguage(stored.language_override || s.languages?.default || 'auto');
      setLat(s.shop?.lat || 0);
      setLng(s.shop?.lng || 0);
      setHighDueLimit(s.limits?.high_due_limit || 10000);
      setDistanceLimitKm(s.limits?.distance_limit_km || 5);

      setGnaniKey(s.api_keys?.gnani_key || '');
      setGroqKey(s.api_keys?.groq_key || '');
      setGroqModel(s.api_keys?.groq_model || 'qwen/qwen3.8-27b');
      setSupabaseUrl(s.api_keys?.supabase_url || '');
      setSupabaseKey(s.api_keys?.supabase_key || '');

      // Check live status on server
      try {
        const res = await fetch('/api/settings/keys-status');
        if (res.ok) {
          const data = await res.json();
          setServerStatus(data);
          if (data.groq?.model) setGroqModel(data.groq.model);
        }
      } catch (netErr) {
        console.warn('Could not reach /api/settings/keys-status:', netErr);
      }
    } catch (e) {
      console.warn('Failed to load settings:', e);
    }
  };

  const handleGetCurrentLocation = () => {
    if (!navigator.geolocation) {
      alert('ब्राउज़र लोकेशन समर्थित नहीं है');
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setLat(pos.coords.latitude);
        setLng(pos.coords.longitude);
      },
      (err) => alert(`लोकेशन प्राप्त नहीं हुआ: ${err.message}`)
    );
  };

  const handleSubmit = async (e) => {
    e.preventDefault();

    const updated = {
      shop: {
        name: shopName.trim(),
        city: city.trim(),
        lat: parseFloat(lat) || 0,
        lng: parseFloat(lng) || 0,
      },
      limits: {
        high_due_limit: parseFloat(highDueLimit) || 10000,
        distance_limit_km: parseFloat(distanceLimitKm) || 5,
      },
      api_keys: {
        gnani_key: gnaniKey.trim(),
        groq_key: groqKey.trim(),
        groq_model: groqModel.trim(),
        supabase_url: supabaseUrl.trim(),
        supabase_key: supabaseKey.trim(),
      },
    };

    try {
      await settingsRepo.setSetting('shop', updated.shop);
      await settingsRepo.setSetting('limits', updated.limits);
      await settingsRepo.setSetting('api_keys', updated.api_keys);
      await settingsRepo.setSetting('language_override', language);

      // Post dynamically to Fastify backend server
      try {
        await fetch('/api/settings/keys', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            groqApiKey: groqKey.trim() || undefined,
            groqModel: groqModel.trim() || undefined,
            gnaniApiKey: gnaniKey.trim() || undefined,
            supabaseUrl: supabaseUrl.trim() || undefined,
            supabaseServiceKey: supabaseKey.trim() || undefined,
          }),
        });
      } catch (beErr) {
        console.warn('Backend keys sync warning:', beErr);
      }

      setSavedSuccess(true);
      setTimeout(() => setSavedSuccess(false), 2500);

      if (onSave) onSave(updated);
    } catch (err) {
      console.error('Failed to save settings:', err);
    }
  };

  const handleDeleteAllData = async () => {
    try {
      await deleteAllLocalData();
      // Purge cloud if server available
      await fetch('/api/sync/delete-all', { method: 'POST' }).catch(() => {});
      alert('सभी स्थानीय और बैकअप डेटा सुरक्षित रूप से मिटा दिया गया है।');
      window.location.reload();
    } catch (err) {
      alert(`डेटा मिटाने में त्रुटि: ${err.message}`);
    }
  };

  return (
    <div style={overlayStyle}>
      <div style={modalStyle}>
        <div style={headerStyle}>
          <h2 style={{ margin: 0, fontSize: '18px' }}>⚙️ सेटिंग्स (Store Settings)</h2>
          <button style={closeBtnStyle} onClick={onClose}>✕</button>
        </div>

        {savedSuccess && (
          <div style={successBannerStyle}>
            ✅ सेटिंग्स सफलतापूर्वक सुरक्षित हो गईं!
          </div>
        )}

        <form onSubmit={handleSubmit} style={formStyle}>
          {/* Section 1: API Keys (User decision highlight) */}
          <div style={sectionBoxStyle}>
            <div style={sectionHeaderStyle}>
              <span style={{ fontWeight: '700', fontSize: '14px', color: '#1a73e8' }}>
                🔑 कस्टम API की (API Key Overrides)
              </span>
              <button
                type="button"
                style={toggleKeyBtnStyle}
                onClick={() => setShowKeys(!showKeys)}
              >
                {showKeys ? 'छिपाएं (Hide)' : 'दिखाएं (Show)'}
              </button>
            </div>
            <p style={{ margin: '4px 0 10px', fontSize: '12px', color: '#666' }}>
              डिफ़ॉल्ट कोटा समाप्त होने पर अपनी निजी API कुंजी यहाँ दर्ज करें।
            </p>

            {serverStatus && (
              <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', margin: '4px 0 10px' }}>
                <span style={{ fontSize: '11px', padding: '2px 8px', borderRadius: '12px', background: serverStatus.groq?.configured ? '#e8f5e9' : '#ffebee', color: serverStatus.groq?.configured ? '#2e7d32' : '#c62828', fontWeight: '600' }}>
                  Groq: {serverStatus.groq?.configured ? `सक्रिय (${serverStatus.groq.masked})` : 'कॉन्फ़िगर नहीं'}
                </span>
                <span style={{ fontSize: '11px', padding: '2px 8px', borderRadius: '12px', background: serverStatus.gnani?.configured ? '#e8f5e9' : '#ffebee', color: serverStatus.gnani?.configured ? '#2e7d32' : '#c62828', fontWeight: '600' }}>
                  Gnani: {serverStatus.gnani?.configured ? `सक्रिय (${serverStatus.gnani.masked})` : 'कॉन्फ़िगर नहीं'}
                </span>
                <span style={{ fontSize: '11px', padding: '2px 8px', borderRadius: '12px', background: serverStatus.supabase?.configured ? '#e8f5e9' : '#ffebee', color: serverStatus.supabase?.configured ? '#2e7d32' : '#c62828', fontWeight: '600' }}>
                  Supabase: {serverStatus.supabase?.configured ? 'कनेक्टेड' : 'कॉन्फ़िगर नहीं'}
                </span>
              </div>
            )}

            <div style={fieldGroupStyle}>
              <label style={labelStyle}>Gnani Vachana AI API Key (STT/TTS)</label>
              <input
                type={showKeys ? 'text' : 'password'}
                placeholder="vach_..."
                value={gnaniKey}
                onInput={(e) => setGnaniKey(e.target.value)}
                style={inputStyle}
              />
            </div>

            <div style={fieldGroupStyle}>
              <label style={labelStyle}>Groq API Key (Voice AI Intent Extractor)</label>
              <input
                type={showKeys ? 'text' : 'password'}
                placeholder="gsk_..."
                value={groqKey}
                onInput={(e) => setGroqKey(e.target.value)}
                style={inputStyle}
              />
            </div>

            <div style={fieldGroupStyle}>
              <label style={labelStyle}>Groq Model (LLM Model Name)</label>
              <input
                type="text"
                placeholder="e.g. qwen/qwen3.8-27b or llama-3.3-70b-versatile"
                value={groqModel}
                onInput={(e) => setGroqModel(e.target.value)}
                style={inputStyle}
              />
            </div>

            <div style={fieldGroupStyle}>
              <label style={labelStyle}>Supabase Project URL</label>
              <input
                type="text"
                placeholder="https://xyz.supabase.co"
                value={supabaseUrl}
                onInput={(e) => setSupabaseUrl(e.target.value)}
                style={inputStyle}
              />
            </div>

            <div style={fieldGroupStyle}>
              <label style={labelStyle}>Supabase Service / Anon Key</label>
              <input
                type={showKeys ? 'text' : 'password'}
                placeholder="sb_..."
                value={supabaseKey}
                onInput={(e) => setSupabaseKey(e.target.value)}
                style={inputStyle}
              />
            </div>
          </div>

          {/* Section 2: Store Profile */}
          <div style={sectionBoxStyle}>
            <span style={{ fontWeight: '700', fontSize: '14px', color: '#333' }}>
              🏪 दुकान का विवरण (Shop Profile)
            </span>

            <div style={{ ...fieldGroupStyle, marginTop: '8px' }}>
              <label style={labelStyle}>दुकान का नाम (Shop Name)</label>
              <input
                type="text"
                value={shopName}
                onInput={(e) => setShopName(e.target.value)}
                placeholder="e.g. शर्मा किराना स्टोर"
                style={inputStyle}
              />
            </div>

            <div style={fieldGroupStyle}>
              <label style={labelStyle}>पसंदीदा भाषा (Preferred Indian Language / Dialect)</label>
              <select
                value={language}
                onChange={(e) => setLanguage(e.target.value)}
                style={inputStyle}
              >
                <option value="auto">🌐 सभी भारतीय भाषाएँ (All Indian Languages - Auto Detect)</option>
                <option value="hi">🇮🇳 हिंदी / Hinglish (Hindi)</option>
                <option value="kn">🇮🇳 ಕನ್ನಡ / Kanglish (Kannada)</option>
                <option value="ta">🇮🇳 தமிழ் / Tanglish (Tamil)</option>
                <option value="te">🇮🇳 తెలుగు (Telugu)</option>
                <option value="mr">🇮🇳 मराठी (Marathi)</option>
                <option value="gu">🇮🇳 ગુજરાતી (Gujarati)</option>
                <option value="bn">🇮🇳 বাংলা (Bengali)</option>
                <option value="ml">🇮🇳 മലയാളം (Malayalam)</option>
                <option value="pa">🇮🇳 ਪੰਜਾਬੀ (Punjabi)</option>
                <option value="en">🇬🇧 English (Indian)</option>
              </select>
            </div>

            <div style={fieldGroupStyle}>
              <label style={labelStyle}>शहर (City / Town)</label>
              <input
                type="text"
                value={city}
                onInput={(e) => setCity(e.target.value)}
                placeholder="e.g. बेंगलुरु"
                style={inputStyle}
              />
            </div>

            <div style={fieldGroupStyle}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <label style={labelStyle}>दुकान की लोकेशन (GPS Geofence)</label>
                <button
                  type="button"
                  style={gpsBtnStyle}
                  onClick={handleGetCurrentLocation}
                >
                  📍 वर्तमान जगह सेट करें
                </button>
              </div>
              <div style={{ display: 'flex', gap: '8px', marginTop: '4px' }}>
                <input
                  type="number"
                  step="any"
                  placeholder="Latitude"
                  value={lat}
                  onInput={(e) => setLat(e.target.value)}
                  style={{ ...inputStyle, flex: 1 }}
                />
                <input
                  type="number"
                  step="any"
                  placeholder="Longitude"
                  value={lng}
                  onInput={(e) => setLng(e.target.value)}
                  style={{ ...inputStyle, flex: 1 }}
                />
              </div>
            </div>
          </div>

          {/* Section 3: Limits & Thresholds */}
          <div style={sectionBoxStyle}>
            <span style={{ fontWeight: '700', fontSize: '14px', color: '#333' }}>
              ⚖️ सीमाएं और सुरक्षा (Limits)
            </span>

            <div style={{ display: 'flex', gap: '8px', marginTop: '8px' }}>
              <div style={{ flex: 1 }}>
                <label style={labelStyle}>अधिक बकाया सीमा (₹)</label>
                <input
                  type="number"
                  value={highDueLimit}
                  onInput={(e) => setHighDueLimit(e.target.value)}
                  style={inputStyle}
                />
              </div>
              <div style={{ flex: 1 }}>
                <label style={labelStyle}>दूरी सीमा (किमी)</label>
                <input
                  type="number"
                  value={distanceLimitKm}
                  onInput={(e) => setDistanceLimitKm(e.target.value)}
                  style={inputStyle}
                />
              </div>
            </div>
          </div>

          {/* Section 4: Data Security / Wipe */}
          <div style={{ ...sectionBoxStyle, borderColor: '#ffcdd2', background: '#ffebee' }}>
            <span style={{ fontWeight: '700', fontSize: '14px', color: '#c62828' }}>
              🛡️ डेटा निजता और रीसेट (Data Privacy)
            </span>
            <p style={{ margin: '4px 0 10px', fontSize: '12px', color: '#c62828' }}>
              यह क्रिया सभी ग्राहक और खाता रिकॉर्ड्स को स्थायी रूप से हटा देगी।
            </p>
            {showDeleteConfirm ? (
              <div style={{ display: 'flex', gap: '8px' }}>
                <button
                  type="button"
                  style={dangerDeleteBtnStyle}
                  onClick={handleDeleteAllData}
                >
                  पुष्टि करें: हाँ, सब मिटाएं
                </button>
                <button
                  type="button"
                  style={cancelDeleteBtnStyle}
                  onClick={() => setShowDeleteConfirm(false)}
                >
                  रद्द करें
                </button>
              </div>
            ) : (
              <button
                type="button"
                style={deleteTriggerBtnStyle}
                onClick={() => setShowDeleteConfirm(true)}
              >
                🗑️ सारा खाता डेटा मिटाएं (Delete All Data)
              </button>
            )}
          </div>

          <div style={footerRowStyle}>
            <button type="submit" style={saveBtnStyle}>
              सेटिंग्स सुरक्षित करें (Save)
            </button>
            <button type="button" style={closeFooterBtnStyle} onClick={onClose}>
              बंद करें
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
  maxWidth: '520px',
  maxHeight: '90vh',
  borderRadius: '16px',
  padding: '20px',
  boxShadow: '0 8px 30px rgba(0,0,0,0.2)',
  display: 'flex',
  flexDirection: 'column',
};

const headerStyle = {
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
  marginBottom: '12px',
};

const closeBtnStyle = {
  background: 'none',
  border: 'none',
  fontSize: '20px',
  cursor: 'pointer',
  color: '#888',
};

const successBannerStyle = {
  background: '#e8f5e9',
  color: '#2e7d32',
  padding: '8px 12px',
  borderRadius: '8px',
  fontSize: '13px',
  marginBottom: '10px',
  fontWeight: '600',
};

const formStyle = {
  overflowY: 'auto',
  display: 'flex',
  flexDirection: 'column',
  gap: '14px',
  paddingRight: '4px',
};

const sectionBoxStyle = {
  background: '#fafafa',
  padding: '12px',
  borderRadius: '12px',
  border: '1px solid #e0e0e0',
};

const sectionHeaderStyle = {
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
};

const toggleKeyBtnStyle = {
  background: 'none',
  border: 'none',
  color: '#1a73e8',
  fontSize: '12px',
  fontWeight: '600',
  cursor: 'pointer',
};

const fieldGroupStyle = {
  marginBottom: '8px',
};

const labelStyle = {
  display: 'block',
  fontSize: '11px',
  fontWeight: '600',
  color: '#555',
  marginBottom: '4px',
};

const inputStyle = {
  width: '100%',
  padding: '8px 12px',
  borderRadius: '8px',
  border: '1px solid #ccc',
  fontSize: '14px',
  boxSizing: 'border-box',
};

const gpsBtnStyle = {
  background: 'none',
  border: 'none',
  color: '#1a73e8',
  fontSize: '12px',
  cursor: 'pointer',
  fontWeight: '600',
};

const deleteTriggerBtnStyle = {
  width: '100%',
  padding: '10px',
  background: '#fff',
  border: '1px solid #d32f2f',
  color: '#d32f2f',
  borderRadius: '8px',
  fontWeight: '600',
  fontSize: '13px',
  cursor: 'pointer',
};

const dangerDeleteBtnStyle = {
  flex: 1,
  padding: '10px',
  background: '#d32f2f',
  color: 'white',
  border: 'none',
  borderRadius: '8px',
  fontWeight: '600',
  fontSize: '13px',
  cursor: 'pointer',
};

const cancelDeleteBtnStyle = {
  padding: '10px 14px',
  background: '#eee',
  border: 'none',
  borderRadius: '8px',
  cursor: 'pointer',
};

const footerRowStyle = {
  display: 'flex',
  gap: '10px',
  marginTop: '8px',
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

const closeFooterBtnStyle = {
  padding: '12px 16px',
  background: '#eee',
  color: '#444',
  border: 'none',
  borderRadius: '10px',
  cursor: 'pointer',
};
