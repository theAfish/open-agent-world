import { useEffect, useMemo, useState, useSyncExternalStore } from 'react';
import { ArrowDown, ArrowUp, Check, ChevronDown, ChevronRight, RotateCcw, X } from 'lucide-react';
import { TransferQueue, type TransferItem } from './transferQueue';
import { IconButton } from '../components/IconButton';
import { t } from '../i18n';
import './transfers.css';

export function useTransferQueue(scope = '') {
  const queue = useMemo(() => new TransferQueue(), [scope]);
  const items = useSyncExternalStore(queue.subscribe, queue.snapshot);
  // Defer disposal one microtask to tolerate StrictMode's setup/cleanup replay.
  const lifetime = useMemo(() => ({ live: false }), [queue]);
  useEffect(() => { lifetime.live = true; return () => { lifetime.live = false; queueMicrotask(() => { if (!lifetime.live) queue.dispose(); }); }; }, [queue, lifetime]);
  return { queue, items, active: items.some(item => item.status === 'queued' || item.status === 'transferring') };
}
export function formatTransferBytes(bytes: number) {
  return bytes < 1024 ? `${bytes} B` : bytes < 1024 ** 2 ? `${(bytes / 1024).toFixed(1)} KiB`
    : bytes < 1024 ** 3 ? `${(bytes / 1024 ** 2).toFixed(1)} MiB` : `${(bytes / 1024 ** 3).toFixed(2)} GiB`;
}
export function TransferList({ queue, items }: { queue: TransferQueue; items: TransferItem[] }) {
  const [expanded, setExpanded] = useState(false);
  if (!items.length) return null;
  const completed = items.filter(item => item.status === 'succeeded');
  const visible = items.filter(item => expanded || item.status !== 'succeeded');
  return <div className="file-transfers" aria-label={t('File transfers')}>
    {completed.length > 0 && <div className="file-transfers-completed">
      <button type="button" aria-expanded={expanded} onClick={() => setExpanded(value => !value)}><Check size={12} /><span>{t('{count} completed', { count: completed.length })}</span>{expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}</button>
      <IconButton icon={X} size="xs" quiet label={t('Clear completed transfers')} onClick={() => completed.forEach(item => queue.dismiss(item.id))} />
    </div>}
    <ul>{visible.map(item => {
    const active = item.status === 'queued' || item.status === 'transferring';
    const status = item.status === 'queued' ? t('Queued') : item.status === 'succeeded' ? t('Complete')
      : item.status === 'cancelled' ? t('Cancelled') : item.status === 'failed' ? t('Failed')
      : item.total > 0 && item.loaded >= item.total ? t('Finishing…') : `${formatTransferBytes(item.loaded)} / ${formatTransferBytes(item.total)}`;
    return <li key={item.id} className={`file-transfer is-${item.status}`}>
      <div className="file-transfer-name">{item.status === 'succeeded' ? <Check size={12} /> : item.direction === 'upload' ? <ArrowUp size={12} /> : <ArrowDown size={12} />}<span title={item.name}>{item.name}</span></div>
      <div className="file-transfer-state"><span role="status">{status}</span>
        {['failed', 'cancelled'].includes(item.status) && <IconButton icon={RotateCcw} size="xs" quiet label={t('Retry {name}', { name: item.name })} onClick={() => queue.retry(item.id)} />}
        <IconButton icon={X} size="xs" quiet label={t(active ? 'Cancel {name}' : 'Dismiss {name}', { name: item.name })} onClick={() => active ? queue.cancel(item.id) : queue.dismiss(item.id)} />
      </div>
      {item.status === 'transferring' && <progress aria-label={item.name} max={item.total || 1} value={item.total ? item.loaded : undefined} />}
      {item.error && <p role="alert">{item.name}: {item.error}</p>}
    </li>;
  })}</ul></div>;
}
