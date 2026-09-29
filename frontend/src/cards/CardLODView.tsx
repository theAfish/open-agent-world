import { memo, useRef, type CSSProperties } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { t, useLocale } from '../i18n';
import { useWorldStore } from '../state/worldStore';
import { useNodeSurfaceStore } from '../state/nodeSurfaces';
import { ConnectionDropSurface } from './ConnectionDropSurface';
import { CatalogIcon } from '../components/CatalogIcon';
import { NODE_SURFACE_RADIUS } from '../state/nodeSurfaces';
import { CardStaticPreview } from './CardStaticPreview';
import type { CanvasNode } from './types';
import './cardLOD.css';

/** Card model projection: no plugin bodies, editors, file requests or workspaces. */
export const CardLODView = memo(function CardLODView({ data, selected, type, isConnectable, width, height }: NodeProps<CanvasNode>) {
  useLocale();
  const card = data.card;
  const definition = useWorldStore(s => s.catalog.node_types.find(d => d.id === card.type));
  const level = data.surfaceLevel;
  const container = type === 'container';
  const pointer = useRef<{ x: number; y: number; moved: boolean }>();
  const mid = data.renderLOD === 'mid';
  const workspace = level === 'workspace' || level === 'inspector';
  return <article className={`world-card node-surface card-lod-view world-card--${card.type} is-${level} ${selected ? 'is-selected' : ''} ${container ? 'container-frame' : ''}`}
    style={{ '--card-kind': definition?.color, borderRadius: NODE_SURFACE_RADIUS[level] } as CSSProperties}
    data-card-id={card.id} data-card-type={card.type} data-surface-level={level}
    data-card-expanded={level === 'workspace' || level === 'inspector' ? 'true' : 'false'}
    data-render-lod={data.renderLOD} aria-label={`${t(definition?.label ?? card.type)} ${card.name}`}
    onPointerDown={event => { pointer.current = { x: event.clientX, y: event.clientY, moved: false }; }}
    onPointerMove={event => { if (pointer.current && Math.hypot(event.clientX - pointer.current.x, event.clientY - pointer.current.y) > 5) pointer.current.moved = true; }}
    onClick={event => {
      if (event.detail !== 0 && pointer.current?.moved) return;
      if (event.shiftKey || event.ctrlKey || event.metaKey || event.altKey) return;
      if (useNodeSurfaceStore.getState().connectingNodeId || useNodeSurfaceStore.getState().dragging) return;
      if ((event.target as Element).closest('.react-flow__handle, button, input, textarea, select, a, [contenteditable="true"]')) return;
      if (!container && (level === 'node' || level === 'preview')) useNodeSurfaceStore.getState().openPrimary(card.id);
    }}>
    {isConnectable && !container && <ConnectionDropSurface nodeId={card.id} />}
    {isConnectable && ([Position.Top, Position.Right, Position.Bottom, Position.Left] as const).map(side =>
      <Handle key={side} id={`boundary-${side}`} type="source" position={side}
        className={`semantic-handle semantic-handle--${side}`} data-connection-side={side}
        aria-label={t('Start a relationship from the {v0} edge of {v1}', { v0: side, v1: card.name })} />)}
    <header data-status={card.status} title={t(card.status)} className={`card-lod-heading node-drag-region ${mid ? workspace ? 'workspace-titlebar' : 'card-header node-surface-header' : 'card-lod-symbol'} ${container ? 'container-header container-drag-region' : ''}`}>
      {isConnectable && container && <ConnectionDropSurface nodeId={card.id} />}
      {mid ? <><span className="card-lod-kind" aria-hidden="true"><CatalogIcon definition={definition} size={24} /></span>
      <div className="card-lod-title"><span className="card-eyebrow">{t(definition?.label ?? card.type)}</span><strong>{card.name}</strong></div>
      <span className="card-lod-status" data-status={card.status} /></>
        : <><CatalogIcon definition={definition} size={34} /><strong>{card.name}</strong></>}
    </header>
    {mid && level !== 'node' && !container && <CardStaticPreview card={card} definition={definition} workspace={workspace} width={width} height={height} />}
  </article>;
});
