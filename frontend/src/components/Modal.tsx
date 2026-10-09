import { useId, useLayoutEffect, useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import { IconButton } from './IconButton';
import { t } from '../i18n';
import './modal.css';

/** Keep overlays in the active native dialog's top layer when a workspace is open. */
export function overlayHost(origin: Element | null = document.activeElement): Element {
  return origin?.closest('dialog[open]') ?? document.querySelector('dialog.legion-workspace[open]') ?? document.body;
}

export function Modal({ title, children, actions, onClose, busy = false, className = '' }: {
  title: string; children: ReactNode; actions?: ReactNode; onClose: () => void; busy?: boolean; className?: string;
}) {
  const id = useId(), panel = useRef<HTMLElement>(null);
  const host = useRef(overlayHost());
  const options = useRef({ onClose, busy }); options.current = { onClose, busy };
  useLayoutEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const element = panel.current!;
    const focusable = () => Array.from(element.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input:not(:disabled), textarea:not(:disabled), [tabindex="0"]'));
    (element.querySelector<HTMLElement>('[data-autofocus]') ?? focusable()[0] ?? element).focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault(); event.stopPropagation();
        if (!options.current.busy) options.current.onClose();
      }
      if (event.key === 'Tab') {
        const items = focusable(), first = items[0] ?? element, last = items.at(-1) ?? element;
        if (!element.contains(document.activeElement) || (event.shiftKey ? document.activeElement === first : document.activeElement === last)) {
          event.preventDefault(); (event.shiftKey ? last : first).focus();
        }
        event.stopPropagation();
      }
    };
    document.addEventListener('keydown', key, true);
    return () => { document.removeEventListener('keydown', key, true); if (previous?.isConnected) previous.focus({ preventScroll: true }); };
  }, []);
  return createPortal(<div className="ui-modal-backdrop nodrag nopan nowheel" onPointerDown={e => {
    e.stopPropagation(); if (e.target === e.currentTarget && !busy) onClose();
  }} onClick={e => e.stopPropagation()} onKeyDown={e => e.stopPropagation()}>
    <section ref={panel} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby={id} aria-busy={busy} className={`ui-modal ${className}`}>
      <header><h2 id={id}>{title}</h2><IconButton icon={X} size="sm" quiet disabled={busy} label={t('Close')} onClick={onClose} /></header>
      <div className="ui-modal-content">{children}</div>
      {actions && <footer>{actions}</footer>}
    </section>
  </div>, host.current);
}
