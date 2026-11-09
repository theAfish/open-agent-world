import { useEffect, useLayoutEffect } from 'react';
import { resolveBackground, resolvePalette, useSkinCatalog } from '../appearance/registry';
import { TEXT_SIZE_STEP, useAppearance } from '../state/appearance';
import { useWorldStore } from '../state/worldStore';

export function useApplicationAppearance() {
  const textSize = useAppearance(state => state.textSize);
  const paletteId = useAppearance(state => state.paletteId);
  const backgroundId = useAppearance(state => state.backgroundId);
  const theme = useWorldStore(state => state.theme);
  const { palettes, backgrounds } = useSkinCatalog();
  const palette = resolvePalette(paletteId, palettes);
  const background = resolveBackground(backgroundId, backgrounds);

  useLayoutEffect(() => {
    const root = document.documentElement;
    const previousPalette = root.dataset.palette;
    const previousBackground = root.dataset.background;
    const previous = Object.entries(palette[theme]).map(([token, value]) => {
      const saved = { token, value: root.style.getPropertyValue(token), priority: root.style.getPropertyPriority(token) };
      root.style.setProperty(token, value);
      return saved;
    });
    root.dataset.palette = palette.id;
    root.dataset.background = background.id;
    return () => {
      for (const saved of previous) {
        if (saved.value) root.style.setProperty(saved.token, saved.value, saved.priority);
        else root.style.removeProperty(saved.token);
      }
      if (previousPalette === undefined) delete root.dataset.palette;
      else root.dataset.palette = previousPalette;
      if (previousBackground === undefined) delete root.dataset.background;
      else root.dataset.background = previousBackground;
    };
  }, [palette, background.id, theme]);

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
