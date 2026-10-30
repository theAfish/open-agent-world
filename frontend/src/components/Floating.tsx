import { cloneElement, useEffect, useId, useLayoutEffect, useRef, useState, type HTMLAttributes, type ReactElement, type ReactNode } from 'react';
import './floating.css';

export type FloatingPlacement = 'top' | 'bottom' | 'left' | 'right';
type Trigger = ReactElement<HTMLAttributes<HTMLElement>>;
const floatingLayers: HTMLElement[] = [];

/** Stay in the trigger's DOM/theme scope; the native top layer escapes clipping and transforms. */
function FloatingSurface({ anchor, placement, id, role, label, className = '', children, onClose, onPointerEnter, onPointerLeave }: {
  anchor: HTMLElement; placement: FloatingPlacement; id: string; role: 'tooltip' | 'dialog';
  label?: string; className?: string; children: ReactNode; onClose: () => void;
  onPointerEnter?: () => void; onPointerLeave?: () => void;
}) {
  const ref = useRef<HTMLElement>(null);
  const close = useRef(onClose); close.current = onClose;
  useLayoutEffect(() => {
    const surface = ref.current!;
    surface.setAttribute('popover', 'manual');
    surface.showPopover();
    floatingLayers.push(surface);
    let frame: number;
    const position = () => {
      if (!anchor.isConnected || anchor.matches(':disabled')) { close.current(); return; }
      const a = anchor.getBoundingClientRect(), b = surface.getBoundingClientRect();
      const gap = 8, edge = 8, width = window.innerWidth, height = window.innerHeight;
      const space = { top: a.top, bottom: height - a.bottom, left: a.left, right: width - a.right };
      const opposite = { top: 'bottom', bottom: 'top', left: 'right', right: 'left' } as const;
      const required = (placement === 'top' || placement === 'bottom' ? b.height : b.width) + gap + edge;
      const side = space[placement] < required && space[opposite[placement]] > space[placement] ? opposite[placement] : placement;
      let left = a.left + (a.width - b.width) / 2, top = a.top + (a.height - b.height) / 2;
      if (side === 'top') top = a.top - b.height - gap;
      if (side === 'bottom') top = a.bottom + gap;
      if (side === 'left') left = a.left - b.width - gap;
      if (side === 'right') left = a.right + gap;
      surface.style.left = `${Math.max(edge, Math.min(left, width - b.width - edge))}px`;
      surface.style.top = `${Math.max(edge, Math.min(top, height - b.height - edge))}px`;
      surface.dataset.placement = side;
      // Canvas zoom/pan and workspace animations can move anchors without a resize or scroll event.
      frame = requestAnimationFrame(position);
    };
    position();
    if (role === 'dialog') {
      (surface.querySelector<HTMLElement>('[data-autofocus], button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]') ?? surface).focus({ preventScroll: true });
    }
    const key = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented || floatingLayers.at(-1) !== surface) return;
      event.preventDefault(); event.stopPropagation(); close.current();
    };
    const outside = (event: Event) => {
      if (!surface.contains(event.target as Node) && !anchor.contains(event.target as Node)) close.current();
    };
    window.addEventListener('keydown', key, true);
    document.addEventListener('pointerdown', outside, true);
    if (role === 'dialog') document.addEventListener('focusin', outside);
    return () => {
      cancelAnimationFrame(frame);
      floatingLayers.splice(floatingLayers.indexOf(surface), 1);
      window.removeEventListener('keydown', key, true);
      document.removeEventListener('pointerdown', outside, true);
      document.removeEventListener('focusin', outside);
      if (role === 'dialog' && anchor.isConnected && (surface.contains(document.activeElement) || document.activeElement === document.body)) anchor.focus({ preventScroll: true });
    };
  }, [anchor, placement, role]);
  return <aside ref={ref} id={id} role={role} aria-label={label} tabIndex={role === 'dialog' ? -1 : undefined}
    className={`ui-floating ui-${role === 'tooltip' ? 'tooltip' : 'popover'} nodrag nopan nowheel ${className}`}
    onPointerEnter={onPointerEnter} onPointerLeave={onPointerLeave}
    onPointerDown={event => event.stopPropagation()} onClick={event => event.stopPropagation()} onKeyDown={event => event.stopPropagation()}>
    {children}
  </aside>;
}

export interface TooltipProps {
  /** Noninteractive explanation. Use Popover for buttons, links or form fields. */
  content: ReactNode;
  /** A single focusable element that forwards DOM events and ARIA attributes. No wrapper is added. */
  children: Trigger;
  placement?: FloatingPlacement;
  /** Pointer hover delay in milliseconds; keyboard focus opens immediately. */
  delay?: number;
  className?: string;
}

export function Tooltip({ content, children, placement = 'top', delay = 350, className }: TooltipProps) {
  const id = useId(), [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout>>();
  const focused = useRef(false);
  const cancel = () => clearTimeout(timer.current);
  const close = () => { cancel(); setAnchor(null); };
  const leave = () => { cancel(); if (!focused.current) timer.current = setTimeout(() => setAnchor(null), 120); };
  useEffect(() => () => clearTimeout(timer.current), []);
  return <>{cloneElement(children, {
    'aria-describedby': [children.props['aria-describedby'], anchor ? id : undefined].filter(Boolean).join(' ') || undefined,
    onPointerEnter: event => {
      children.props.onPointerEnter?.(event); cancel();
      const target = event.currentTarget;
      if (!event.defaultPrevented && event.pointerType !== 'touch') timer.current = setTimeout(() => setAnchor(target), delay);
    },
    onPointerLeave: event => { children.props.onPointerLeave?.(event); leave(); },
    onFocus: event => { children.props.onFocus?.(event); if (!event.defaultPrevented) { cancel(); focused.current = true; setAnchor(event.currentTarget); } },
    onBlur: event => { children.props.onBlur?.(event); focused.current = false; leave(); },
    onPointerDown: event => { children.props.onPointerDown?.(event); close(); },
    onClick: event => { children.props.onClick?.(event); close(); },
  })}{anchor && <FloatingSurface anchor={anchor} placement={placement} id={id} role="tooltip" className={className}
    onClose={close} onPointerEnter={cancel} onPointerLeave={leave}>{content}</FloatingSurface>}</>;
}

export interface PopoverProps {
  label: string;
  trigger: Trigger;
  children: ReactNode | ((close: () => void) => ReactNode);
  placement?: FloatingPlacement;
  className?: string;
}

/** A nonmodal dialog: focus enters on open and returns on dismissal; Tab can leave naturally. */
export function Popover({ label, trigger, children, placement = 'bottom', className }: PopoverProps) {
  const id = useId(), [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const close = () => setAnchor(null);
  return <>{cloneElement(trigger, {
    'aria-haspopup': 'dialog', 'aria-expanded': !!anchor, 'aria-controls': anchor ? id : undefined,
    onClick: event => {
      trigger.props.onClick?.(event); event.stopPropagation();
      if (!event.defaultPrevented) setAnchor(anchor ? null : event.currentTarget);
    },
  })}{anchor && <FloatingSurface anchor={anchor} placement={placement} id={id} role="dialog" label={label} className={className} onClose={close}>
    {typeof children === 'function' ? children(close) : children}
  </FloatingSurface>}</>;
}
