import { useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { MoreHorizontal } from 'lucide-react';

/** Keep menus out of scrolling lists, but inside their moving workspace. */
export function ConversationActions({ host, label, title, children }: {
  host: HTMLElement | null;
  label: string;
  title: string;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const id = useId();
  useLayoutEffect(() => {
    if (!open || !host || !trigger.current || !menu.current) return;
    const anchor = trigger.current;
    const popup = menu.current;
    const position = () => {
      const origin = host.getBoundingClientRect();
      const rect = anchor.getBoundingClientRect();
      const scale = host.offsetWidth ? origin.width / host.offsetWidth || 1 : 1;
      const bottom = (rect.bottom - origin.top) / scale + 4;
      const top = (rect.top - origin.top) / scale - popup.offsetHeight - 4;
      popup.style.left = `${Math.max(4, (rect.right - origin.left) / scale - popup.offsetWidth)}px`;
      popup.style.top = `${Math.max(4, bottom + popup.offsetHeight <= host.clientHeight ? bottom : top)}px`;
      popup.style.maxWidth = `${Math.max(0, host.clientWidth - 8)}px`;
      popup.style.maxHeight = `${Math.max(0, host.clientHeight - 8)}px`;
    };
    const outside = (event: PointerEvent) => {
      if (event.target instanceof Node && !anchor.contains(event.target) && !popup.contains(event.target)) setOpen(false);
    };
    position();
    const observer = typeof ResizeObserver === 'undefined' ? undefined : new ResizeObserver(position);
    observer?.observe(host);
    observer?.observe(popup);
    window.addEventListener('resize', position);
    window.addEventListener('scroll', position, true);
    document.addEventListener('pointerdown', outside);
    return () => {
      observer?.disconnect();
      window.removeEventListener('resize', position);
      window.removeEventListener('scroll', position, true);
      document.removeEventListener('pointerdown', outside);
    };
  }, [open, host]);

  return <details className="conversation-session-actions" open={open} onBlur={event => {
    if (!event.currentTarget.contains(event.relatedTarget) && !menu.current?.contains(event.relatedTarget)) setOpen(false);
  }} onKeyDown={event => {
    if (event.key === 'Escape') {
      event.preventDefault(); event.stopPropagation(); setOpen(false); trigger.current?.focus();
    }
  }}>
    <summary ref={trigger} aria-label={label} title={title} aria-expanded={open} aria-controls={open ? id : undefined}
      onClick={event => { event.preventDefault(); setOpen(value => !value); }}
      onKeyDown={event => {
        if (open && (event.key === 'ArrowDown' || (event.key === 'Tab' && !event.shiftKey))) {
          event.preventDefault(); menu.current?.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus();
        }
      }}><MoreHorizontal size={15} /></summary>
    {open && host ? createPortal(<div ref={menu} id={id} className="conversation-session-menu conversation-floating-actions nodrag nopan nowheel"
      onClick={event => {
        if ((event.target as Element).closest('button:not(:disabled)')) setOpen(false);
      }}>{children}</div>, host) : null}
  </details>;
}
