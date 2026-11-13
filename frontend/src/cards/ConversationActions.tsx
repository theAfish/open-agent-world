import { useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { MoreHorizontal } from 'lucide-react';
import { observeConversationPopup } from './conversationPopup';

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
    return observeConversationPopup(host, trigger.current, menu.current, () => setOpen(false), 'flip');
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
