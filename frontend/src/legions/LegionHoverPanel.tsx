import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Layers3 } from 'lucide-react';
import { CatalogIcon } from '../components/CatalogIcon';
import { t, useLocale } from '../i18n';
import { useWorldStore } from '../state/worldStore';
import { useNodeSurfaceStore } from '../state/nodeSurfaces';
import './legionHoverPanel.css';

export const LEGION_HOVER_DELAY = 650;
type Hover = { id: string; saved: boolean; x: number; y: number };

/** Observe without intercepting the transparent container's canvas gestures. */
export function LegionHoverPanel() {
  useLocale();
  const [hover, setHover] = useState<Hover>();
  const cards = useWorldStore(s => s.cards);
  const legions = useWorldStore(s => s.legions);
  const catalog = useWorldStore(s => s.catalog);
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let pending: Hover | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const close = () => { clearTimeout(timer); timer = undefined; pending = undefined; setHover(undefined); };
    const move = (event: PointerEvent) => {
      const surface = useNodeSurfaceStore.getState();
      if (event.pointerType === 'touch' || event.buttons || surface.dragging || surface.connectingNodeId) { close(); return; }
      const target = event.target instanceof Element ? event.target : null;
      const saved = target?.closest<HTMLElement>('[data-legion-preview]');
      const header = target?.closest('.legion-container > .container-header');
      let id = saved?.dataset.legionPreview ?? header?.closest<HTMLElement>('.legion-container')?.dataset.cardId;
      if (!id && target?.matches('.react-flow__pane')) {
        // DOM bounds include current zoom, pan, resize and nested-container positions.
        const frames = Array.from(target.closest('.react-flow')!.querySelectorAll<HTMLElement>('.legion-container'));
        const hit = frames.filter(frame => {
          const rect = frame.getBoundingClientRect();
          return rect.width > 0 && event.clientX >= rect.left && event.clientX <= rect.right && event.clientY >= rect.top && event.clientY <= rect.bottom;
        }).sort((a, b) => a.getBoundingClientRect().width * a.getBoundingClientRect().height - b.getBoundingClientRect().width * b.getBoundingClientRect().height)[0];
        id = hit?.dataset.cardId;
      }
      if (!id) { close(); return; }
      if (pending?.id === id && pending.saved === Boolean(saved)) {
        pending = { ...pending, x: event.clientX, y: event.clientY };
        return;
      }
      close();
      pending = { id, saved: Boolean(saved), x: event.clientX, y: event.clientY };
      timer = setTimeout(() => { setHover(pending); }, LEGION_HOVER_DELAY);
    };
    const key = (event: KeyboardEvent) => { if (event.key === 'Escape') close(); };
    const leave = (event: PointerEvent) => { if (!event.relatedTarget) close(); };
    document.addEventListener('pointermove', move, true);
    document.addEventListener('pointerdown', close, true);
    document.addEventListener('pointercancel', close, true);
    document.addEventListener('pointerout', leave, true);
    document.addEventListener('keydown', key, true);
    document.addEventListener('wheel', close, true);
    document.addEventListener('scroll', close, true);
    window.addEventListener('blur', close);
    window.addEventListener('resize', close);
    const unsubscribe = useNodeSurfaceStore.subscribe(state => { if (state.dragging || state.connectingNodeId) close(); });
    return () => {
      clearTimeout(timer); unsubscribe();
      document.removeEventListener('pointermove', move, true);
      document.removeEventListener('pointerdown', close, true);
      document.removeEventListener('pointercancel', close, true);
      document.removeEventListener('pointerout', leave, true);
      document.removeEventListener('keydown', key, true);
      document.removeEventListener('wheel', close, true);
      document.removeEventListener('scroll', close, true);
      window.removeEventListener('blur', close);
      window.removeEventListener('resize', close);
    };
  }, []);
  const source = hover?.saved ? legions.find(item => item.id === hover.id) : cards.find(item => item.id === hover?.id);
  const saved = hover?.saved ? legions.find(item => item.id === hover.id) : undefined;
  const members = saved ? saved.members ?? saved.node_types.map(type => ({ type, name: '' })) : cards.filter(card => card.parent_id === hover?.id);
  useLayoutEffect(() => {
    if (!panel.current || !hover) return;
    const rect = panel.current.getBoundingClientRect();
    panel.current.style.left = `${Math.max(8, Math.min(hover.x + 16, window.innerWidth - rect.width - 8))}px`;
    panel.current.style.top = `${Math.max(8, hover.y + 18 + rect.height <= window.innerHeight - 8 ? hover.y + 18 : hover.y - rect.height - 12)}px`;
  }, [hover, source, members.length]);
  if (!hover || !source) return null;
  return createPortal(<div ref={panel} className="legion-hover-panel" role="tooltip" aria-label={t('Legion contents')}>
    <header><Layers3 size={17} /><strong>{source.name}</strong><small>{saved?.node_count ?? members.length} {t('members')}</small></header>
    {saved?.description && <p>{saved.description}</p>}
    <ul>{members.slice(0, 8).map((member, index) => {
      const definition = catalog.node_types.find(item => item.id === member.type);
      return <li key={index}><CatalogIcon definition={definition} size={16} /><div><strong>{member.name || (definition ? t(definition.label) : member.type)}</strong><small>{definition ? t(definition.label) : member.type}{'status' in member ? ` · ${t(String(member.status))}` : ''}</small></div></li>;
    })}</ul>
    {!members.length && <p>{t('No member cards')}</p>}
    {members.length > 8 && <p>{t('{count} more cards', { count: members.length - 8 })}</p>}
    {saved && !saved.compatible && <p>{t('This formation has unavailable dependencies.')}</p>}
  </div>, document.body);
}
