import { useStore } from '@xyflow/react';
import { memo } from 'react';
import { t, useLocale } from '../i18n';
import { useLegionDeployments } from '../state/legionDeployments';
import { ViewportPortal } from './FlowPortal';
import './legionDeployment.css';

export const LegionDeploymentLayer = memo(function LegionDeploymentLayer() {
  useLocale();
  const pending = useLegionDeployments(state => state.pending);
  const zoom = useStore(state => pending.length ? state.transform[2] : 1);
  if (!pending.length) return null;
  return <ViewportPortal>{pending.map(item => <div key={item.id} className="legion-deployment" role="status"
    data-deployment-stage={item.stage}
    style={{ left: item.position.x, top: item.position.y, transform: `translate(-50%, -50%) scale(${1 / zoom})` }}>
    <span className="legion-deployment-spinner" aria-hidden="true" />
    <span>{t(item.stage === 'queued' ? 'Waiting to deploy {name}…' : 'Deploying {name}…', { name: item.name })}</span>
  </div>)}</ViewportPortal>;
});
