/**
 * SyncBadge — Visual sync status indicator
 *
 * Shows a small badge in the header:
 *   🟢 Synced       – all outbox entries pushed
 *   🟡 Pending (N)  – N entries waiting to push
 *   🔵 Syncing…     – push/pull in progress
 *   🔴 Offline      – no network
 *   🔴 Error        – last sync failed
 */

import { syncStatus, pendingCount } from '../sync/syncStatus.js';
import { pullFromCloud, processOutbox } from '../sync/syncEngine.js';

const STATUS_CONFIG = {
  synced:  { color: '#4caf50', label: 'Synced',  icon: '🟢' },
  pending: { color: '#ff9800', label: 'Pending', icon: '🟡' },
  syncing: { color: '#2196f3', label: 'Syncing', icon: '🔵' },
  offline: { color: '#f44336', label: 'Offline', icon: '🔴' },
  error:   { color: '#f44336', label: 'Error',   icon: '🔴' },
};

export function SyncBadge() {
  const status = syncStatus.value;
  const count = pendingCount.value;
  const cfg = STATUS_CONFIG[status] || STATUS_CONFIG.synced;

  const label = status === 'pending' && count > 0
    ? `Pending (${count})`
    : cfg.label;

  const handleClick = () => {
    pullFromCloud().then(() => processOutbox());
  };

  return (
    <div
      style={{ ...styles.badge, cursor: 'pointer' }}
      title={`Sync: ${label} (Click to re-sync now)`}
      onClick={handleClick}
    >
      <span
        style={{ ...styles.dot, background: cfg.color }}
        aria-hidden="true"
      />
      <span style={styles.text}>{label}</span>
    </div>
  );
}

const styles = {
  badge: {
    display: 'flex',
    alignItems: 'center',
    gap: '6px',
    fontSize: '12px',
    opacity: 0.95,
    cursor: 'default',
  },
  dot: {
    width: '8px',
    height: '8px',
    borderRadius: '50%',
    display: 'inline-block',
    flexShrink: 0,
  },
  text: {
    fontSize: '12px',
    whiteSpace: 'nowrap',
  },
};
