import { useEffect } from 'react';
import { useStore } from '@xyflow/react';
import { useWorldStore } from '../state/worldStore';
import { eventType } from '../state/graphEvents';
import './connectionMotion.css';

/** Confirmed graph events animate once; mounting an edge during pan never does. */
export function ConnectionMotionLayer() {
  const root = useStore(state => state.domNode);
  useEffect(() => {
    if (!root) return;
    const motion = matchMedia('(prefers-reduced-motion: reduce)');
    const frames = new Set<number>();
    const animations = new Set<Animation>();
    const play = (element: Element, keyframes: Keyframe[]) => {
      const animation = element.animate(keyframes, { duration: 320, easing: 'cubic-bezier(.2,.8,.2,1)' });
      animations.add(animation);
      animation.onfinish = () => { animations.delete(animation); };
    };
    const reveal = (id: string, deadline: number) => {
      const frame = requestAnimationFrame(() => {
        frames.delete(frame);
        if (motion.matches || document.hidden || performance.now() > deadline) return;
        const path = root.querySelector<SVGPathElement>(`.semantic-edge-path[data-edge-id="${CSS.escape(id)}"]`);
        if (!path) {
          // The world event precedes React Flow's DOM commit. Offscreen edges
          // expire here instead of replaying the feedback when later revealed.
          reveal(id, deadline);
          return;
        }
        play(path, [{ opacity: 0, strokeWidth: 2.5 }, { opacity: 1, offset: .6 }, {}]);
        root.querySelectorAll(`.semantic-edge-endpoint[data-edge-id="${CSS.escape(id)}"]`).forEach(endpoint =>
          play(endpoint, [{ opacity: .2, r: '7px' }, {}]));
      });
      frames.add(frame);
    };
    const unsubscribe = useWorldStore.subscribe((state, previous) => {
      if (state.events === previous.events || motion.matches || document.hidden) return;
      const known = new Set(previous.events.map(event => event.id));
      for (const event of state.events) {
        if (known.has(event.id) || eventType(event) !== 'edge_created'
            || Math.abs(Date.now() - Date.parse(event.timestamp)) > 2000) continue;
        const edge = event.payload.edge as { id?: unknown } | undefined;
        if (typeof edge?.id === 'string' && state.edges.some(item => item.id === edge.id)) reveal(edge.id, performance.now() + 400);
      }
    });
    const cancel = () => {
      frames.forEach(cancelAnimationFrame); frames.clear();
      animations.forEach(animation => animation.cancel()); animations.clear();
    };
    const preferenceChanged = () => { if (motion.matches) cancel(); };
    const visibilityChanged = () => { if (document.hidden) cancel(); };
    motion.addEventListener('change', preferenceChanged);
    document.addEventListener('visibilitychange', visibilityChanged);
    return () => {
      unsubscribe();
      motion.removeEventListener('change', preferenceChanged);
      document.removeEventListener('visibilitychange', visibilityChanged);
      cancel();
    };
  }, [root]);
  return null;
}
