import { Check, ChevronDown, LayoutTemplate } from 'lucide-react';
import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { t } from '../i18n';
import type { WorldCard } from '../types/world';

export function WorkspaceSwitcher({ card, workspaces, disabled, onSwitch }: {
  card: WorldCard; workspaces: WorldCard[]; disabled: boolean; onSwitch: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const container = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const close = (restore = false) => { setOpen(false); if (restore) trigger.current?.focus(); };
  useLayoutEffect(() => {
    if (open) menu.current?.querySelector<HTMLElement>('[aria-checked="true"]')?.focus();
  }, [open]);
  useEffect(() => {
    if (!open) return;
    const outside = (event: Event) => { if (!container.current?.contains(event.target as Node)) close(); };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('focusin', outside);
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('focusin', outside); };
  }, [open]);
  return <div ref={container} className="legion-workspace-switcher">
    <button ref={trigger} className="legion-workspace-trigger" aria-label={t('Switch workspace')}
      aria-haspopup="menu" aria-expanded={open} aria-controls={open ? id : undefined} disabled={disabled}
      onClick={() => setOpen(value => !value)} onKeyDown={event => {
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); setOpen(true); }
      }}><strong>{card.name}</strong><ChevronDown size={13} className={open ? 'is-open' : ''} /></button>
    {open && <div ref={menu} id={id} role="menu" aria-label={t('Workspaces')} className="legion-workspace-menu" onKeyDown={event => {
      event.stopPropagation();
      if (event.key === 'Escape') { event.preventDefault(); close(true); return; }
      if (event.key === 'Tab') { close(true); return; }
      const items = [...menu.current!.querySelectorAll<HTMLButtonElement>('[role="menuitemradio"]')];
      const current = items.indexOf(document.activeElement as HTMLButtonElement);
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1
        : event.key === 'ArrowDown' ? (current + 1) % items.length : event.key === 'ArrowUp' ? (current + items.length - 1) % items.length : -1;
      if (next >= 0) { event.preventDefault(); items[next].focus(); }
    }}>
      <div className="legion-workspace-menu-label">{t('Workspaces')}</div>
      {workspaces.map(workspace => <button key={workspace.id} role="menuitemradio" aria-checked={workspace.id === card.id}
        onClick={() => { close(true); if (workspace.id !== card.id) onSwitch(workspace.id); }}>
        <LayoutTemplate size={14} /><span>{workspace.name}</span>{workspace.id === card.id && <Check size={14} />}
      </button>)}
    </div>}
  </div>;
}
