import { useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

interface Item { index: number; label: string; disabled: boolean; group?: string; hidden: boolean }
interface Menu {
  select: HTMLSelectElement;
  items: Item[];
  active: number;
  fontSize: string;
  label: string;
}

function itemsFor(select: HTMLSelectElement): Item[] {
  return Array.from(select.options, (option, index) => {
    const group = option.parentElement instanceof HTMLOptGroupElement ? option.parentElement : undefined;
    return { index, label: option.label, disabled: option.disabled || !!group?.disabled, group: group?.label, hidden: option.hidden || !!group?.hidden };
  });
}

function position(select: HTMLSelectElement) {
  const box = select.getBoundingClientRect();
  const width = Math.min(Math.max(box.width, 160), window.innerWidth - 16);
  const below = window.innerHeight - box.bottom - 12;
  const above = box.top - 12;
  const upwards = below < 180 && above > below;
  const maxHeight = Math.max(40, Math.min(260, upwards ? above : below));
  return {
    left: Math.max(8, Math.min(box.left, window.innerWidth - width - 8)),
    top: upwards ? box.top - 4 : box.bottom + 4,
    width, maxHeight,
    transform: upwards ? 'translateY(-100%)' : undefined,
  };
}

/** Keep native form values/labels and React onChange, but draw every popup ourselves.
 * Delegation also covers dynamically loaded plugin controls without changing their API.
 */
export function SelectMenuLayer() {
  const id = useId();
  const [menu, setMenu] = useState<Menu | null>(null);
  const current = useRef<Menu | null>(null);
  const popup = useRef<HTMLDivElement>(null);
  const search = useRef({ text: '', time: 0 });

  useEffect(() => {
    let savedAttributes: Record<string, string | null> = {};
    const update = (next: Menu | null) => { current.current = next; setMenu(next); };
    const close = () => {
      observer.disconnect();
      const select = current.current?.select;
      if (select) for (const [name, value] of Object.entries(savedAttributes)) {
        if (value === null) select.removeAttribute(name); else select.setAttribute(name, value);
      }
      search.current = { text: '', time: 0 };
      update(null);
    };
    const targetSelect = (target: EventTarget | null) => {
      if (!(target instanceof HTMLSelectElement) || target.disabled || target.multiple || target.size > 1) return null;
      return target;
    };
    const open = (select: HTMLSelectElement) => {
      close();
      const items = itemsFor(select);
      if (!items.some(item => !item.disabled && !item.hidden)) return;
      select.focus({ preventScroll: true });
      savedAttributes = Object.fromEntries(['aria-expanded', 'aria-controls', 'aria-activedescendant'].map(name => [name, select.getAttribute(name)]));
      select.setAttribute('aria-expanded', 'true');
      select.setAttribute('aria-controls', id);
      const label = select.getAttribute('aria-label')
        || select.getAttribute('aria-labelledby')?.split(/\s+/).map(labelId => document.getElementById(labelId)?.textContent ?? '').join(' ')
        || Array.from(select.labels ?? [], label => label.textContent).join(' ');
      const active = items.find(item => item.index === select.selectedIndex && !item.disabled && !item.hidden)?.index
        ?? items.find(item => !item.disabled && !item.hidden)!.index;
      select.setAttribute('aria-activedescendant', `${id}-${active}`);
      update({ select, items, active, fontSize: getComputedStyle(select).fontSize, label });
      observer.observe(document.body, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ['disabled', 'hidden', 'label'] });
    };
    const activate = (active: number) => {
      const state = current.current;
      if (!state) return;
      state.select.setAttribute('aria-activedescendant', `${id}-${active}`);
      update({ ...state, active });
    };
    const choose = (index: number) => {
      const select = current.current?.select;
      const item = select && itemsFor(select)[index];
      if (!select || select.disabled || !item || item.disabled || item.hidden) return;
      const changed = select.selectedIndex !== index;
      close();
      select.focus({ preventScroll: true });
      if (changed) {
        select.selectedIndex = index;
        select.dispatchEvent(new Event('input', { bubbles: true }));
        select.dispatchEvent(new Event('change', { bubbles: true }));
      }
    };
    const pointerDown = (event: Event) => {
      if ('button' in event && event.button !== 0) return;
      if (targetSelect(event.target)) event.preventDefault();
      else if (popup.current?.contains(event.target as Node)) event.preventDefault();
      else close();
    };
    const click = (event: MouseEvent) => {
      const select = targetSelect(event.target);
      if (select) {
        event.preventDefault();
        event.stopPropagation();
        if (current.current?.select === select) close(); else open(select);
      } else if (event.target instanceof Element && popup.current?.contains(event.target)) {
        const option = event.target.closest<HTMLElement>('[data-select-option]');
        if (option) choose(Number(option.dataset.selectOption));
        event.preventDefault();
        event.stopPropagation();
      }
    };
    const keyDown = (event: KeyboardEvent) => {
      const select = targetSelect(event.target);
      if (!select) return;
      const opening = ['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(event.key);
      const typing = event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey;
      if (!current.current && !opening && !typing) return;
      if (event.key === 'Tab') { close(); return; }
      event.preventDefault();
      event.stopPropagation();
      if (event.key === 'Escape' || (event.altKey && event.key === 'ArrowUp')) { close(); return; }
      if (!current.current) { open(select); if (opening) return; }
      const state = current.current;
      if (!state) return;
      const enabled = itemsFor(select).filter(item => !item.disabled && !item.hidden);
      if (!enabled.length) { close(); return; }
      const offset = enabled.findIndex(item => item.index === state.active);
      if (event.key === 'ArrowDown') activate(enabled[Math.min(offset + 1, enabled.length - 1)].index);
      else if (event.key === 'ArrowUp') activate(enabled[offset < 0 ? enabled.length - 1 : Math.max(offset - 1, 0)].index);
      else if (event.key === 'Home') activate(enabled[0].index);
      else if (event.key === 'End') activate(enabled[enabled.length - 1].index);
      else if (event.key === 'Enter' || event.key === ' ') choose(state.active);
      else if (typing) {
        const now = Date.now();
        const text = now - search.current.time < 700 ? search.current.text + event.key : event.key;
        search.current = { text, time: now };
        const term = /^(.)\1+$/u.test(text) ? event.key : text;
        const ordered = [...enabled.slice(offset + 1), ...enabled.slice(0, offset + 1)];
        const match = ordered.find(item => item.label.toLocaleLowerCase().startsWith(term.toLocaleLowerCase()));
        if (match) activate(match.index);
      }
    };
    const focusIn = (event: FocusEvent) => { if (current.current && event.target !== current.current.select) close(); };
    const pointerMove = (event: PointerEvent) => {
      if (!(event.target instanceof Element) || !popup.current?.contains(event.target)) return;
      const option = event.target.closest<HTMLElement>('[data-select-option]');
      if (option && option.getAttribute('aria-disabled') !== 'true') {
        const index = Number(option.dataset.selectOption);
        if (index !== current.current?.active) activate(index);
      }
    };
    const reposition = (event?: Event) => {
      const state = current.current;
      if (!state || (event?.target instanceof Node && popup.current?.contains(event.target))) return;
      if (!state.select.isConnected || state.select.disabled) close();
      else update({ ...state, items: itemsFor(state.select), fontSize: getComputedStyle(state.select).fontSize });
    };
    // Pointer and mouse cancellation are both needed to suppress native WebKit menus.
    document.addEventListener('pointerdown', pointerDown, true);
    document.addEventListener('mousedown', pointerDown, true);
    document.addEventListener('click', click, true);
    document.addEventListener('keydown', keyDown, true);
    document.addEventListener('focusin', focusIn, true);
    document.addEventListener('pointermove', pointerMove, true);
    document.addEventListener('scroll', reposition, true);
    window.addEventListener('resize', reposition);
    const observer = new MutationObserver(records => {
      const state = current.current;
      if (!state) return;
      if (!state.select.isConnected || state.select.disabled) close();
      else if (records.some(record => state.select.contains(record.target))) reposition();
    });
    return () => {
      close();
      observer.disconnect();
      document.removeEventListener('pointerdown', pointerDown, true);
      document.removeEventListener('mousedown', pointerDown, true);
      document.removeEventListener('click', click, true);
      document.removeEventListener('keydown', keyDown, true);
      document.removeEventListener('focusin', focusIn, true);
      document.removeEventListener('pointermove', pointerMove, true);
      document.removeEventListener('scroll', reposition, true);
      window.removeEventListener('resize', reposition);
    };
  }, [id]);

  useEffect(() => {
    if (menu) document.getElementById(`${id}-${menu.active}`)?.scrollIntoView?.({ block: 'nearest' });
  }, [id, menu?.active]);

  if (!menu) return null;
  return createPortal(<div ref={popup} id={id} role="listbox" aria-label={menu.label}
    className="ui-select-menu nodrag nopan nowheel" style={{ ...position(menu.select), fontSize: menu.fontSize }}>
    {menu.items.filter(item => !item.hidden).map((item, index, items) => <div key={item.index}>
      {item.group && item.group !== items[index - 1]?.group && <div className="ui-select-group">{item.group}</div>}
      <div id={`${id}-${item.index}`} role="option" aria-selected={item.index === menu.select.selectedIndex}
        aria-disabled={item.disabled || undefined} data-select-option={item.index}
        className={`ui-select-option${item.index === menu.active ? ' is-active' : ''}`}>
        <span>{item.label || '\u00a0'}</span><span aria-hidden="true">{item.index === menu.select.selectedIndex ? '✓' : ''}</span>
      </div>
    </div>)}
  </div>, document.body);
}
