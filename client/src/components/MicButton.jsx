/**
 * Mic Button Component — Voice Khata
 *
 * Floating action button with distinct visual states:
 * - 'idle': Ready, blue gradient, microphone icon
 * - 'listening': Active recording, red pulse, audio wave ring
 * - 'processing': Computing STT/LLM, amber rotating ring
 * - 'speaking': TTS outputting audio, green equalizer ripple
 * - 'error': Failed state, red shake with retry icon
 */

export function MicButton({ state = 'idle', energy = 0, onClick, onHoldStart, onHoldEnd }) {
  const getBackgroundColor = () => {
    switch (state) {
      case 'listening': return '#e53935'; // Red
      case 'processing': return '#f57c00'; // Amber
      case 'speaking': return '#2e7d32'; // Green
      case 'error': return '#c62828'; // Crimson
      default: return '#1a73e8'; // Blue
    }
  };

  const getIcon = () => {
    switch (state) {
      case 'listening': return '🎙️';
      case 'processing': return '⏳';
      case 'speaking': return '🔊';
      case 'error': return '⚠️';
      default: return '🎤';
    }
  };

  const getLabel = () => {
    switch (state) {
      case 'listening': return 'सुन रहे हैं... (बोलिए)';
      case 'processing': return 'समझ रहे हैं...';
      case 'speaking': return 'बोल रहे हैं...';
      case 'error': return 'पुनः प्रयास करें';
      default: return 'बोलकर खाता लिखें';
    }
  };

  const scale = state === 'listening' ? 1 + energy * 0.35 : 1;

  return (
    <div style={containerStyle}>
      <div style={labelStyle}>{getLabel()}</div>
      <button
        style={{
          ...buttonStyle,
          background: getBackgroundColor(),
          transform: `scale(${scale})`,
          boxShadow: state === 'listening'
            ? `0 0 0 ${Math.round(energy * 20)}px rgba(229, 57, 53, 0.4), 0 6px 18px rgba(0,0,0,0.3)`
            : '0 4px 14px rgba(0,0,0,0.25)',
        }}
        onClick={onClick}
        onMouseDown={onHoldStart}
        onMouseUp={onHoldEnd}
        onTouchStart={onHoldStart}
        onTouchEnd={onHoldEnd}
        aria-label={getLabel()}
        title={getLabel()}
      >
        <span style={{ fontSize: '30px' }}>{getIcon()}</span>
      </button>
    </div>
  );
}

const containerStyle = {
  position: 'fixed',
  bottom: '24px',
  right: '24px',
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  zIndex: 300,
  gap: '8px',
};

const labelStyle = {
  fontSize: '12px',
  fontWeight: '600',
  color: '#333',
  background: 'rgba(255, 255, 255, 0.95)',
  padding: '4px 10px',
  borderRadius: '12px',
  boxShadow: '0 2px 6px rgba(0,0,0,0.1)',
  pointerEvents: 'none',
  whiteSpace: 'nowrap',
};

const buttonStyle = {
  width: '68px',
  height: '68px',
  borderRadius: '50%',
  border: 'none',
  color: 'white',
  cursor: 'pointer',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  transition: 'transform 0.1s ease, background 0.3s ease, box-shadow 0.15s ease',
  outline: 'none',
  userSelect: 'none',
};
