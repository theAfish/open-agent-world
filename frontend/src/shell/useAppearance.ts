import { useEffect, useLayoutEffect } from 'react';
import { TEXT_SIZE_STEP, useAppearance } from '../state/appearance';

export function useApplicationAppearance() {
  const textSize = useAppearance(state => state.textSize);
  useLayoutEffect(() => {
    const root = document.documentElement;
    const previous = root.style.getPropertyValue('--text-scale');
    root.style.setProperty('--text-scale', String(textSize / 100));
    return () => {
      if (previous) root.style.setProperty('--text-scale', previous);
      else root.style.removeProperty('--text-scale');
    };
  }, [textSize]);

  useEffect(() => {
    // Track the physical key: trackpad pinch also emits ctrlKey wheel events.
    let controlPressed = false;
    let accumulated = 0;
    let lastWheel = 0;
    const key = (event: KeyboardEvent) => { controlPressed = event.ctrlKey; accumulated = 0; };
    const reset = () => { controlPressed = false; accumulated = 0; };
    const wheel = (event: WheelEvent) => {
      const state = useAppearance.getState();
      if (!state.wheelShortcut || !controlPressed || !event.ctrlKey || event.altKey || event.metaKey
        || event.defaultPrevented || !event.deltaY) return;
      event.preventDefault();
      event.stopPropagation();
      const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? window.innerHeight : 1);
      if (event.timeStamp - lastWheel > 250 || Math.sign(accumulated) !== Math.sign(delta)) accumulated = 0;
      lastWheel = event.timeStamp;
      accumulated += delta;
      if (Math.abs(accumulated) < 40) return;
      state.setTextSize(state.textSize - Math.sign(accumulated) * TEXT_SIZE_STEP);
      accumulated = 0;
    };
    window.addEventListener('keydown', key, true);
    window.addEventListener('keyup', key, true);
    window.addEventListener('blur', reset);
    window.addEventListener('wheel', wheel, { capture: true, passive: false });
    return () => {
      window.removeEventListener('keydown', key, true);
      window.removeEventListener('keyup', key, true);
      window.removeEventListener('blur', reset);
      window.removeEventListener('wheel', wheel, true);
    };
  }, []);
}
