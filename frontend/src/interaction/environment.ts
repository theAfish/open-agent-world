import { create } from 'zustand';

export const COMPACT_LAYOUT_QUERY = '(max-width: 700px)';

type Input = 'mouse' | 'touch' | 'pen' | 'keyboard';
interface InteractionEnvironment {
  compact: boolean;
  touch: boolean;
  hover: boolean;
  reducedMotion: boolean;
  input: Input;
  height: number;
  top: number;
  bottom: number;
}

/** Presentation state only. Never save device geometry to application preferences. */
export const useInteractionEnvironment = create<InteractionEnvironment>(() => ({
  compact: false, touch: false, hover: true, reducedMotion: false,
  input: 'mouse', height: 0, top: 0, bottom: 0,
}));

/** Shared by builder, published workspaces and sign-in; disposed during Vite HMR. */
export function startInteractionEnvironment() {
  const root = document.documentElement;
  const queries = {
    compact: matchMedia(COMPACT_LAYOUT_QUERY),
    touch: matchMedia('(any-pointer: coarse)'),
    hover: matchMedia('(any-hover: hover)'),
    reducedMotion: matchMedia('(prefers-reduced-motion: reduce)'),
  };
  const viewport = window.visualViewport;
  const attributes = ['data-layout', 'data-touch-input', 'data-input'];
  const properties = ['--app-height', '--app-top', '--app-bottom'];
  const oldAttributes = attributes.map(name => root.getAttribute(name));
  const oldProperties = properties.map(name => root.style.getPropertyValue(name));
  let input: Input = queries.touch.matches ? 'touch' : 'mouse';
  let observedTouch = false;
  let frame = 0;
  const update = () => {
    cancelAnimationFrame(frame);
    frame = 0;
    // Browser pinch zoom must not resize/reflow the app underneath the user.
    const visible = viewport && Math.abs(viewport.scale - 1) < .01;
    const height = Math.round(visible ? viewport.height : window.innerHeight);
    const top = Math.round(visible ? viewport.offsetTop : 0);
    const next: InteractionEnvironment = {
      compact: queries.compact.matches,
      touch: queries.touch.matches || navigator.maxTouchPoints > 0 || observedTouch,
      hover: queries.hover.matches, reducedMotion: queries.reducedMotion.matches, input,
      height, top, bottom: Math.max(0, Math.round(window.innerHeight - height - top)),
    };
    const previous = useInteractionEnvironment.getState();
    if (Object.keys(next).some(key => next[key as keyof InteractionEnvironment] !== previous[key as keyof InteractionEnvironment])) {
      useInteractionEnvironment.setState(next);
    }
    root.dataset.layout = next.compact ? 'compact' : 'regular';
    root.dataset.touchInput = String(next.touch);
    root.dataset.input = input;
    properties.forEach((name, index) => root.style.setProperty(name, `${[height, top, next.bottom][index]}px`));
  };
  const schedule = () => { if (!frame) frame = requestAnimationFrame(update); };
  const pointer = (event: PointerEvent) => {
    if (!['mouse', 'touch', 'pen'].includes(event.pointerType)) return;
    input = event.pointerType as Input;
    if (input === 'touch' || input === 'pen') observedTouch = true;
    update();
  };
  const keyboard = (event: KeyboardEvent) => {
    if (!['Tab', 'Enter', ' ', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    input = 'keyboard'; update();
  };
  update();
  Object.values(queries).forEach(query => query.addEventListener('change', schedule));
  window.addEventListener('resize', schedule);
  viewport?.addEventListener('resize', schedule);
  viewport?.addEventListener('scroll', schedule);
  document.addEventListener('pointerdown', pointer, true);
  document.addEventListener('keydown', keyboard, true);
  return () => {
    cancelAnimationFrame(frame);
    Object.values(queries).forEach(query => query.removeEventListener('change', schedule));
    window.removeEventListener('resize', schedule);
    viewport?.removeEventListener('resize', schedule);
    viewport?.removeEventListener('scroll', schedule);
    document.removeEventListener('pointerdown', pointer, true);
    document.removeEventListener('keydown', keyboard, true);
    attributes.forEach((name, index) => {
      const value = oldAttributes[index];
      if (value === null) root.removeAttribute(name); else root.setAttribute(name, value);
    });
    properties.forEach((name, index) => {
      if (oldProperties[index]) root.style.setProperty(name, oldProperties[index]); else root.style.removeProperty(name);
    });
  };
}
