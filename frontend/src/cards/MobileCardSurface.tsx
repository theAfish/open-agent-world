import { useId, useLayoutEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { ArrowLeft, ExternalLink, Trash2 } from 'lucide-react';
import { CardName } from './CardName';
import { CatalogIcon } from '../components/CatalogIcon';
import { IconButton } from '../components/IconButton';
import { t } from '../i18n';
import { useWorldStore } from '../state/worldStore';
import { useNodeSurfaceStore } from '../state/nodeSurfaces';
import type { WorldCard } from '../types/world';
import './mobileCardSurface.css';

/** Move the DOM host, retaining its single React owner and local editor state. */
export function CardSurfaceMount({ host, active = true, className = '', hidden }: {
  host: HTMLDivElement; active?: boolean; className?: string; hidden?: boolean;
}) {
  const mount = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    if (!active) return;
    const target = mount.current!;
    target.appendChild(host);
    return () => { if (host.parentNode === target) target.removeChild(host); };
  }, [host, active]);
  return <div ref={mount} className={className} aria-hidden={hidden} />;
}

/** A native top-layer window escapes canvas zoom and makes the canvas inert. */
export function MobileCardSurface({ card, level, host, onWorkspace }: {
  card: WorldCard; level: 'inspector' | 'workspace'; host: HTMLDivElement; onWorkspace?: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const back = useRef<HTMLButtonElement>(null);
  const label = useId();
  const definition = useWorldStore(state => state.catalog.node_types.find(item => item.id === card.type));
  const close = useNodeSurfaceStore(state => state.closeMobileSurface);
  useLayoutEffect(() => {
    const element = dialog.current!;
    const previous = document.activeElement as HTMLElement | null;
    element.showModal();
    back.current?.focus({ preventScroll: true });
    return () => {
      element.close();
      if (previous?.isConnected) previous.focus({ preventScroll: true });
    };
  }, []);
  return createPortal(<dialog ref={dialog} role="dialog" className="mobile-card-surface nodrag nopan nowheel" aria-labelledby={label}
    data-mobile-card-id={card.id} data-mobile-surface-level={level}
    onCancel={event => { event.preventDefault(); close(); }}
    onPointerDown={event => event.stopPropagation()} onClick={event => event.stopPropagation()}
    onDoubleClick={event => event.stopPropagation()} onKeyDown={event => event.stopPropagation()} onWheel={event => event.stopPropagation()}>
    <header className="mobile-card-titlebar">
      <IconButton ref={back} icon={ArrowLeft} label={t('Back to canvas')} onClick={close} />
      <CatalogIcon definition={definition} size={20} />
      <div id={label} className="mobile-card-title"><CardName card={card} workspace label={definition?.label ?? card.type} /></div>
      {level === 'inspector' && onWorkspace && <IconButton icon={ExternalLink} label={t('Open workspace')} onClick={onWorkspace} />}
      {!card.ephemeral && <IconButton icon={Trash2} danger label={t('Remove {v0}', { v0: card.name })} onClick={() => {
        close(); void useWorldStore.getState().deleteCard(card.id);
      }} />}
    </header>
    <CardSurfaceMount host={host} className="mobile-card-content world-card" />
  </dialog>, document.body);
}
