import { t } from '../i18n';
import type { PackOperation } from '../types/packs';

export function packOperationPending(operation: PackOperation) {
  return operation.state !== 'completed' && operation.state !== 'failed';
}

const labels: Record<PackOperation['state'], string> = {
  preparing: 'Preparing environments', prepared: 'Environments prepared', selecting: 'Selecting version',
  completed: 'Installed', failed: 'Installation failed',
};

export function PackOperationStatus({ operation }: { operation: PackOperation }) {
  return <div className="pack-operation" role="status" data-operation-id={operation.id}>
    <span>{operation.pack_id} {operation.version} · {t(labels[operation.state])}</span>
    {operation.error && <p className="pack-action-error">{operation.error}</p>}
  </div>;
}

export function PackOperations({ operations }: { operations?: PackOperation[] }) {
  const pending = operations?.filter(packOperationPending) ?? [];
  const failed = operations?.filter(item => item.state === 'failed').slice(0, 5) ?? [];
  if (!pending.length && !failed.length) return null;
  return <section className="pack-operations" aria-label={t('Pack installation progress')}>
    {pending.map(operation => <PackOperationStatus key={operation.id} operation={operation} />)}
    {failed.length > 0 && <details><summary>{t('Recent installation failures')}</summary>
      {failed.map(operation => <PackOperationStatus key={operation.id} operation={operation} />)}
    </details>}
  </section>;
}
