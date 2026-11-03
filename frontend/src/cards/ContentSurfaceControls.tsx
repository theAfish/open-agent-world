import { GripVertical, Maximize2, MoreHorizontal, Trash2, X } from 'lucide-react';
import { IconButton } from '../components/IconButton';
import { Popover } from '../components/Floating';
import { t, useLocale } from '../i18n';
import { cardPresentation, collapsedSurface, nodeSurfaceSupport, useNodeSurfaceStore } from '../state/nodeSurfaces';
import { useWorldStore } from '../state/worldStore';
import type { NodeTypeCatalogItem, WorldCard } from '../types/world';
import { CardName } from './CardName';
import { finishLabel } from './cardFinish';
import './contentSurface.css';

/** Opt-in presentation only; older and third-party cards retain their existing shell. */
export const hasContentSurface = (definition?: NodeTypeCatalogItem) => !!definition?.traits.includes('ui.content-surface.v1');

/** A narrow drag edge replaces title/footer bars around self-explanatory content. */
export function ContentSurfaceControls({ card, level }: { card: WorldCard; level: 'preview' | 'inspector' | 'workspace' }) {
  useLocale();
  const catalog = useWorldStore(state => state.catalog);
  const definition = catalog.node_types.find(item => item.id === card.type);
  const base = useNodeSurfaceStore(state => state.baseLevels[card.id]);
  const canCollapse = level !== 'preview' && collapsedSurface(cardPresentation(card, catalog), level, base) !== level;
  const close = () => {
    const state = useNodeSurfaceStore.getState();
    if (level === 'workspace') state.closeWorkspace(card.id); else state.closeInspector(card.id);
  };
  return <aside className="content-surface-controls" aria-label={t('Card controls')}>
    <Popover label={t('Card details')} className="content-surface-menu-content"
      placement={level === 'preview' && !definition?.traits.includes('ui.ellipse.v1') ? 'bottom' : 'right'}
      trigger={<button type="button" className="content-surface-menu-button nodrag nopan" aria-label={t('Card details')} title={t('Card details')}><MoreHorizontal size={16} aria-hidden="true" /></button>}>
        <CardName card={card} workspace label={definition?.label ?? card.type} />
        <dl><div><dt>{t('Status')}</dt><dd>{t(card.status.replaceAll('_', ' '))}</dd></div>
          <div><dt>{t('Finish')}</dt><dd>{t(finishLabel(card.finish))}</dd></div></dl>
        <small className="content-surface-id" title={card.id}>{card.id.slice(0, 8)}</small>
        {!card.ephemeral && <button type="button" className="content-surface-remove" aria-label={t('Remove {v0}', { v0: String(card.name) })}
          title={t('Remove object (Ctrl+Z to undo)')} onClick={() => {
            useNodeSurfaceStore.getState().dismiss(card.id); void useWorldStore.getState().deleteCard(card.id);
          }}><Trash2 size={13} />{t('Remove')}</button>}
    </Popover>
    <div className="content-surface-grip node-drag-region" title={t('Drag {v0}', { v0: String(card.name) })} aria-hidden="true"><GripVertical size={14} /></div>
    <div className="content-surface-actions">
      {level === 'inspector' && nodeSurfaceSupport(card.type, catalog).workspace && <IconButton icon={Maximize2} size="sm" quiet
        label={t('Open workspace')} onClick={() => useNodeSurfaceStore.getState().openWorkspace(card.id)} />}
      {canCollapse && <IconButton icon={X} size="sm" quiet onClick={close}
        label={level === 'workspace' ? t('Close workspace') : t('Close {v0} inspector', { v0: String(card.name) })} />}
    </div>
  </aside>;
}
