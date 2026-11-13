import { ChevronDown } from 'lucide-react';
import { useCallback, useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { t } from '../i18n';
import { observeConversationPopup } from './conversationPopup';

/** A group switcher that moves with its pane, outside the session scroll area. */
export function ConversationGroupMenu({ host, title, open, onOpenChange, children }: {
  host: HTMLElement | null;
  title: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  children: (host: HTMLDivElement | null, close: () => void) => ReactNode;
}) {
  const id = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const [popup, setPopup] = useState<HTMLDivElement | null>(null);
  const popupRef = useRef<HTMLDivElement | null>(null);
  const attachPopup = useCallback((element: HTMLDivElement | null) => {
    popupRef.current = element;
    setPopup(element);
  }, []);
  const close = () => { onOpenChange(false); trigger.current?.focus(); };

  useLayoutEffect(() => {
    if (!open || !host || !popup || !trigger.current) return;
    const stop = observeConversationPopup(host, trigger.current, popup, () => onOpenChange(false), 'below');
    // A restored form owns its focus; otherwise start at the current group.
    if (!popup.contains(document.activeElement)) {
      (popup.querySelector<HTMLButtonElement>('[aria-current="true"]') ?? popup.querySelector<HTMLButtonElement>('button:not(:disabled)'))?.focus();
    }
    return stop;
  }, [open, host, popup, onOpenChange]);

  return <div className="conversation-group-switcher" onKeyDown={event => {
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); }
  }} onBlur={event => {
    if (event.relatedTarget instanceof Node && !event.currentTarget.contains(event.relatedTarget) && !popupRef.current?.contains(event.relatedTarget)) onOpenChange(false);
  }}>
    <button ref={trigger} type="button" className="conversation-group-tab" title={`${t('Groups')}: ${title}`}
      aria-label={t('Switch group: {v0}', { v0: title })} aria-haspopup="dialog" aria-expanded={open} aria-controls={open ? id : undefined}
      onClick={() => onOpenChange(!open)} onKeyDown={event => {
        if (event.key === 'ArrowDown') { event.preventDefault(); onOpenChange(true); }
      }}>
      <svg className="conversation-group-tab-shoulder" viewBox="0 0 18 32" preserveAspectRatio="none" aria-hidden="true">
        <path className="conversation-group-tab-fill" d="M0 0 C12 0 4 31.5 18 31.5 V0 Z" />
        <path className="conversation-group-tab-edge" d="M0 .5 C12 .5 4 31.5 18 31.5" />
      </svg>
      <span>{title}</span><ChevronDown size={12} aria-hidden="true" />
    </button>
    {open && host ? createPortal(<div ref={attachPopup} id={id} role="dialog" aria-label={t('Groups')}
      className="conversation-group-menu nodrag nopan nowheel">
      {children(popup, close)}
    </div>, host) : null}
  </div>;
}
