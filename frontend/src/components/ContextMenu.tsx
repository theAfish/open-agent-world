import { useLayoutEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { overlayHost } from './Modal';
import './contextMenu.css';

export interface ContextMenuAnchor { x: number; y: number; trigger: HTMLElement }
export function ContextMenu({ anchor, label, onClose, children }: {
  anchor: ContextMenuAnchor; label: string; onClose: () => void; children: React.ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const close = useRef(onClose); close.current = onClose;
  useLayoutEffect(() => {
    const menu = ref.current!;
    const rect = menu.getBoundingClientRect();
    menu.style.left = `${Math.max(4, Math.min(anchor.x, window.innerWidth - rect.width - 4))}px`;
    menu.style.top = `${Math.max(4, Math.min(anchor.y, window.innerHeight - rect.height - 4))}px`;
    menu.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus();
    const outside = (event: PointerEvent) => { if (!menu.contains(event.target as Node)) close.current(); };
    const dismiss = () => close.current();
    const scroll = (event: Event) => { if (!menu.contains(event.target as Node)) dismiss(); };
    document.addEventListener('pointerdown', outside, true);
    window.addEventListener('resize', dismiss);
    window.addEventListener('scroll', scroll, true);
    return () => {
      document.removeEventListener('pointerdown', outside, true);
      window.removeEventListener('resize', dismiss);
      window.removeEventListener('scroll', scroll, true);
      if (menu.contains(document.activeElement) || document.activeElement === document.body) anchor.trigger.focus({ preventScroll: true });
    };
  }, [anchor]);
  return createPortal(<div ref={ref} role="menu" aria-label={label} className="ui-context-menu nodrag nopan nowheel"
    style={{ left: anchor.x, top: anchor.y }} onContextMenu={e => { e.preventDefault(); e.stopPropagation(); }}
    onBlur={e => { if (!e.currentTarget.contains(e.relatedTarget)) onClose(); }}
    onClick={e => { e.stopPropagation(); if ((e.target as Element).closest('button:not(:disabled)')) onClose(); }}
    onKeyDown={e => {
      e.stopPropagation();
      if (e.key === 'Escape') { e.preventDefault(); onClose(); anchor.trigger.focus(); }
      if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) {
        e.preventDefault();
        const items = Array.from(e.currentTarget.querySelectorAll<HTMLButtonElement>('button:not(:disabled)'));
        const index = items.indexOf(document.activeElement as HTMLButtonElement);
        items[e.key === 'Home' ? 0 : e.key === 'End' ? items.length - 1 : (index + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length]?.focus();
      }
    }}>{children}</div>, overlayHost(anchor.trigger));
}
