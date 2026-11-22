/**
 * Customer List Component — Voice Khata
 *
 * Kirana store home dashboard showing:
 * - Aggregated credit summary cards
 * - Searchable customer roster
 * - Visual status badges (overdue, settled, active)
 * - Quick tap to view complete ledger
 */

import { useState } from 'preact/hooks';

export function CustomerList({
  customers = [],
  onSelectCustomer,
  onAddNewCustomer,
}) {
  const [searchTerm, setSearchTerm] = useState('');
  const [filterType, setFilterType] = useState('all'); // 'all' | 'due' | 'overdue'

  // Filter customers
  const filtered = customers.filter((c) => {
    const term = searchTerm.toLowerCase();
    const matchesSearch =
      (c.name || '').toLowerCase().includes(term) ||
      (c.phone || '').includes(term) ||
      (c.city || '').toLowerCase().includes(term);

    if (!matchesSearch) return false;

    if (filterType === 'due') return c.totalDue > 0;
    if (filterType === 'overdue') {
      if (!c.dueSince || c.totalDue <= 0) return false;
      const days = (Date.now() - new Date(c.dueSince).getTime()) / (1000 * 60 * 60 * 24);
      return days >= 7;
    }
    return true;
  });

  // Calculate aggregates
  const totalOutstanding = customers.reduce((sum, c) => sum + (c.totalDue || 0), 0);
  const overdueCount = customers.filter((c) => {
    if (!c.dueSince || c.totalDue <= 0) return false;
    return (Date.now() - new Date(c.dueSince).getTime()) / (1000 * 60 * 60 * 24) >= 7;
  }).length;

  return (
    <div style={containerStyle}>
      {/* Khata Metrics Overview */}
      <div style={statsGridStyle}>
        <div style={statCardStyle}>
          <div style={statLabelStyle}>कुल बाकी (Total Due)</div>
          <div style={statValueDueStyle}>₹{totalOutstanding.toLocaleString('en-IN')}</div>
        </div>
        <div style={statCardStyle}>
          <div style={statLabelStyle}>कुल ग्राहक (Customers)</div>
          <div style={statValueNormalStyle}>{customers.length}</div>
        </div>
        <div style={statCardStyle}>
          <div style={statLabelStyle}>7+ दिन से बाकी (Overdue)</div>
          <div style={statValueWarnStyle}>{overdueCount}</div>
        </div>
      </div>

      {/* Search and Action Bar */}
      <div style={searchRowStyle}>
        <input
          type="text"
          placeholder="🔍 ग्राहक खोजें (नाम या फ़ोन)..."
          value={searchTerm}
          onInput={(e) => setSearchTerm(e.target.value)}
          style={searchInputStyle}
        />
        <button style={addCustomerBtnStyle} onClick={onAddNewCustomer}>
          + नया ग्राहक
        </button>
      </div>

      {/* Filter Tabs */}
      <div style={filterTabsStyle}>
        <button
          style={filterTabStyle(filterType === 'all')}
          onClick={() => setFilterType('all')}
        >
          सभी ({customers.length})
        </button>
        <button
          style={filterTabStyle(filterType === 'due')}
          onClick={() => setFilterType('due')}
        >
          बाकीदार ({customers.filter((c) => c.totalDue > 0).length})
        </button>
        <button
          style={filterTabStyle(filterType === 'overdue')}
          onClick={() => setFilterType('overdue')}
        >
          पुराना बाकी ({overdueCount})
        </button>
      </div>

      {/* Customer List */}
      <div style={listContainerStyle}>
        {filtered.length === 0 ? (
          <div style={emptyStateStyle}>
            <p style={{ margin: 0, fontSize: '15px', color: '#666' }}>
              कोई ग्राहक नहीं मिला।
            </p>
            <p style={{ margin: '6px 0 0', fontSize: '13px', color: '#999' }}>
              माइक बटन दबाकर बोलिए "रमेश 500 रुपये उधार"
            </p>
          </div>
        ) : (
          filtered.map((c) => {
            let overdueDays = 0;
            if (c.dueSince && c.totalDue > 0) {
              overdueDays = Math.max(0, Math.floor((Date.now() - new Date(c.dueSince).getTime()) / (1000 * 60 * 60 * 24)));
            }

            return (
              <div
                key={c.id}
                style={customerCardStyle}
                onClick={() => onSelectCustomer(c)}
              >
                <div style={{ flex: 1 }}>
                  <div style={{ fontWeight: '600', fontSize: '16px', color: '#222' }}>
                    {c.name}
                  </div>
                  <div style={{ fontSize: '12px', color: '#777', marginTop: '2px' }}>
                    📍 {c.city || 'शहर नहीं'} | 📞 {c.phone || 'फ़ोन नहीं'}
                  </div>
                  {c.totalDue > 0 && overdueDays > 0 && (
                    <div style={overdueTagStyle}>
                      ⏳ {overdueDays} दिन से बाकी
                    </div>
                  )}
                </div>

                <div style={{ textAlign: 'right' }}>
                  <div style={{ fontSize: '11px', color: '#888' }}>बकाया राशि</div>
                  <div style={{
                    fontSize: '18px',
                    fontWeight: '700',
                    color: c.totalDue > 0 ? '#d32f2f' : '#2e7d32',
                  }}>
                    ₹{c.totalDue.toLocaleString('en-IN')}
                  </div>
                  <div style={{ fontSize: '12px', color: '#1a73e8', marginTop: '4px' }}>
                    खाता देखें →
                  </div>
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}

const containerStyle = {
  maxWidth: '720px',
  margin: '0 auto',
  padding: '16px',
};

const statsGridStyle = {
  display: 'grid',
  gridTemplateColumns: 'repeat(3, 1fr)',
  gap: '10px',
  marginBottom: '16px',
};

const statCardStyle = {
  background: '#ffffff',
  borderRadius: '12px',
  padding: '12px',
  boxShadow: '0 2px 8px rgba(0,0,0,0.06)',
  textAlign: 'center',
};

const statLabelStyle = {
  fontSize: '11px',
  color: '#666',
  marginBottom: '4px',
};

const statValueDueStyle = {
  fontSize: '18px',
  fontWeight: '700',
  color: '#d32f2f',
};

const statValueNormalStyle = {
  fontSize: '18px',
  fontWeight: '700',
  color: '#1a73e8',
};

const statValueWarnStyle = {
  fontSize: '18px',
  fontWeight: '700',
  color: '#f57c00',
};

const searchRowStyle = {
  display: 'flex',
  gap: '8px',
  marginBottom: '12px',
};

const searchInputStyle = {
  flex: 1,
  padding: '12px 16px',
  borderRadius: '12px',
  border: '1px solid #ddd',
  fontSize: '15px',
  outline: 'none',
  background: '#fff',
  boxShadow: '0 2px 6px rgba(0,0,0,0.04)',
};

const addCustomerBtnStyle = {
  padding: '0 16px',
  background: '#1a73e8',
  color: 'white',
  border: 'none',
  borderRadius: '12px',
  fontWeight: '600',
  fontSize: '14px',
  cursor: 'pointer',
  whiteSpace: 'nowrap',
};

const filterTabsStyle = {
  display: 'flex',
  gap: '8px',
  marginBottom: '14px',
};

const filterTabStyle = (active) => ({
  flex: 1,
  padding: '8px',
  borderRadius: '8px',
  border: 'none',
  background: active ? '#1a73e8' : '#e0e0e0',
  color: active ? '#fff' : '#444',
  fontWeight: '600',
  fontSize: '13px',
  cursor: 'pointer',
  transition: 'all 0.15s ease',
});

const listContainerStyle = {
  display: 'flex',
  flexDirection: 'column',
  gap: '10px',
  paddingBottom: '90px', // space for floating mic button
};

const customerCardStyle = {
  background: '#ffffff',
  borderRadius: '12px',
  padding: '14px 16px',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  boxShadow: '0 2px 6px rgba(0,0,0,0.05)',
  cursor: 'pointer',
  border: '1px solid #eee',
  transition: 'transform 0.1s ease, box-shadow 0.1s ease',
};

const overdueTagStyle = {
  display: 'inline-block',
  fontSize: '11px',
  color: '#c62828',
  background: '#ffebee',
  padding: '2px 8px',
  borderRadius: '10px',
  marginTop: '4px',
};

const emptyStateStyle = {
  textAlign: 'center',
  padding: '40px 20px',
  background: '#fff',
  borderRadius: '12px',
  border: '1px dashed #ccc',
};
