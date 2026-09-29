import { createContext, useCallback, useContext, useId, useLayoutEffect, useMemo, useState, useSyncExternalStore } from 'react';
import { useStore, useStoreApi } from '@xyflow/react';
import type { CanvasNode } from '../cards/types';
import { CardRenderModel, type CardRenderLOD } from './cardRendering';

export const CardRenderingContext = createContext<CardRenderModel | null>(null);

export function useCardRendering(nodes: CanvasNode[]) {
  const store = useStoreApi();
  const root = useStore(state => state.domNode);
  const [model] = useState(() => new CardRenderModel());
  const revision = useSyncExternalStore(model.subscribe, model.getProjectionSnapshot, model.getProjectionSnapshot);
  useLayoutEffect(() => {
    const update = () => {
      const { transform: [x, y, zoom], width, height } = store.getState();
      model.setCamera({ x, y, zoom, width, height });
    };
    update();
    const unsubscribe = store.subscribe((state, previous) => {
      if (state.transform !== previous.transform || state.width !== previous.width || state.height !== previous.height) update();
    });
    return () => { unsubscribe(); model.suspend(); };
  }, [model, store]);
  useLayoutEffect(() => { model.setNodes(nodes); }, [model, nodes]);
  useLayoutEffect(() => {
    if (!root) return;
    let focused: string | undefined;
    const update = () => {
      const target = document.activeElement;
      const next = target instanceof Element && target.closest('.react-flow') === root
        ? target.closest('.react-flow__node')?.getAttribute('data-id') ?? undefined : undefined;
      if (next === focused) return;
      if (focused) model.pin(focused, 'focus', false);
      focused = next; if (focused) model.pin(focused, 'focus', true);
    };
    // Delay blur until the new focus has arrived, including controls in portals.
    const blur = () => queueMicrotask(update);
    root.addEventListener('focusin', update); root.addEventListener('focusout', blur);
    update();
    return () => { root.removeEventListener('focusin', update); root.removeEventListener('focusout', blur); if (focused) model.pin(focused, 'focus', false); };
  }, [model, root]);
  useLayoutEffect(() => {
    if (!root) return;
    // Retain visibility through a gesture, but selection/focus can hydrate
    // immediately. The stable card boundary preserves its eventual click.
    let gesture: string | undefined;
    const held = new Set<string>();
    const releases = new Set<number>();
    const release = (id: string) => {
      const frame = requestAnimationFrame(() => { model.holdGesture(id, false); held.delete(id); releases.delete(frame); });
      releases.add(frame);
    };
    const down = (event: PointerEvent) => {
      if (gesture) release(gesture);
      gesture = undefined;
      const target = event.target instanceof Element ? event.target : null;
      const card = target?.closest<HTMLElement>('.card-lod-view:not(.container-frame)');
      if (!card || event.button !== 0 || target?.closest('.react-flow__handle')) return;
      gesture = card.dataset.cardId!;
      held.add(gesture); model.holdGesture(gesture, true);
    };
    const releaseGesture = () => { if (gesture) release(gesture); gesture = undefined; };
    root.addEventListener('pointerdown', down, true);
    window.addEventListener('pointerup', releaseGesture, true);
    window.addEventListener('pointercancel', releaseGesture);
    window.addEventListener('blur', releaseGesture);
    return () => { root.removeEventListener('pointerdown', down, true);
      window.removeEventListener('pointerup', releaseGesture, true);
      window.removeEventListener('pointercancel', releaseGesture); window.removeEventListener('blur', releaseGesture);
      releases.forEach(cancelAnimationFrame); held.forEach(id => model.holdGesture(id, false)); };
  }, [root, model]);
  const renderedNodes = useMemo(() => model.project(nodes, true), [model, nodes, revision]);
  return { model, renderedNodes };
}

/** A renderer change stays local to that card; no XYFlow node-array update. */
export function useCardRenderLOD(id: string, fallback: CardRenderLOD = 'full') {
  const model = useContext(CardRenderingContext);
  const subscribe = useCallback((listener: () => void) => model?.subscribeCard(id, listener) ?? (() => {}), [model, id]);
  const snapshot = useCallback(() => model?.getLevel(id) ?? fallback, [model, id, fallback]);
  return useSyncExternalStore(subscribe, snapshot, snapshot);
}

/** Keep an in-progress operation or non-serializable edit hydrated. */
export function useHydrationLease(id: string, reason: string, active: boolean) {
  const model = useContext(CardRenderingContext);
  const lease = useId();
  useLayoutEffect(() => {
    if (!active || !model) return;
    const key = `${reason}:${lease}`;
    model.pin(id, key, true);
    return () => model.pin(id, key, false);
  }, [model, id, reason, lease, active]);
}
