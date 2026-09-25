import { useStore, useStoreApi } from '@xyflow/react';
import { memo, useLayoutEffect } from 'react';
import './canvasCardLayers.css';

/** Reuse small card surfaces during overview pan, without retaining large
 * inspector/workspace textures or rendering React on every viewport change.
 */
export const CanvasCardLayers = memo(function CanvasCardLayers() {
  const root = useStore(state => state.domNode);
  const store = useStoreApi();
  useLayoutEffect(() => {
    if (!root) return;
    let enabled: boolean | undefined;
    const update = () => {
      const state = store.getState();
      const next = state.transform[2] <= 0.75;
      if (next === enabled) return;
      enabled = next;
      if (next) root.dataset.cardLayers = 'true';
      else delete root.dataset.cardLayers;
    };
    update();
    const unsubscribe = store.subscribe((state, previous) => {
      if (state.transform[2] !== previous.transform[2]) update();
    });
    return () => { unsubscribe(); delete root.dataset.cardLayers; };
  }, [root, store]);
  return null;
});
