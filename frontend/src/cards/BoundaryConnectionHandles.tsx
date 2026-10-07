import { Handle, Position, useConnection } from '@xyflow/react';
import { t } from '../i18n';
import { ConnectionHoverHint } from './ConnectionHoverHint';
import './boundaryConnection.css';

/** A movable boundary affordance plus a body drop target, including self-links.
 * The parent updates ConnectionHoverHint on pointer movement.
 */
export function BoundaryConnectionHandles({ label }: { label: string }) {
  const active = useConnection(connection => connection.inProgress);
  return <>
    <ConnectionHoverHint />
    <Handle id="boundary" type="source" position={Position.Top} className="boundary-connection-source"
      aria-label={t('Connect from {name}', { name: label })} />
    <Handle id="surface-drop" type="target" position={Position.Top} className="connection-drop-surface"
      data-active={active || undefined} isConnectableStart={false} isConnectableEnd={active}
      aria-label={t('Connect to {name}', { name: label })} />
  </>;
}
