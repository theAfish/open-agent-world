import { useLayoutEffect, useRef } from 'react';
import { useReactFlow, useStore } from '@xyflow/react';
import { useWorldStore } from '../state/worldStore';
import { useGenerationStore } from '../effects/generation';
import { ViewportPortal } from './FlowPortal';
import { animateCard, canvasMotionReduced, cardMotionChanges } from './canvasMotion';
import './canvasMotion.css';

/** Visual copies leave after confirmed removal; live React trees unmount normally. */
export function CanvasMotionLayer() {
  const root = useStore(state => state.domNode);
  const layer = useRef<HTMLDivElement>(null);
  const { screenToFlowPosition, getViewport } = useReactFlow();
  useLayoutEffect(() => {
    if (!root) return;
    const pending = new Map<string, number>();
    const running = new Set<Animation>();
    const ghosts = new Map<string, HTMLElement>();
    let frame = 0;
    const find = (id: string) => root.querySelector<HTMLElement>(`[data-card-id="${CSS.escape(id)}"]`);
    const track = (animation: Animation) => {
      running.add(animation);
      void animation.finished.then(() => running.delete(animation), () => running.delete(animation));
    };
    const visible = (element: HTMLElement) => {
      const box = element.getBoundingClientRect(), bounds = root.getBoundingClientRect();
      return box.width > 0 && box.height > 0 && box.right > bounds.left && box.left < bounds.right
        && box.bottom > bounds.top && box.top < bounds.bottom;
    };
    const enter = () => {
      frame = 0;
      for (const [id, deadline] of pending) {
        const element = find(id);
        const generation = useGenerationStore.getState().items.some(item => item.targetId === id || item.containerId === id);
        if (generation || performance.now() > deadline || canvasMotionReduced() || document.hidden) pending.delete(id);
        else if (element) {
          pending.delete(id);
          if (visible(element)) {
            const animation = animateCard(element);
            if (animation) track(animation);
          }
        }
      }
      if (pending.size) frame = requestAnimationFrame(enter);
    };
    const leave = (id: string) => {
      pending.delete(id);
      const source = find(id), host = layer.current;
      if (!source || !host || !visible(source)) return;
      const rect = source.getBoundingClientRect();
      const position = screenToFlowPosition({ x: rect.left, y: rect.top });
      const ghost = document.createElement('div');
      ghost.className = 'canvas-card-exit';
      ghost.inert = true;
      ghost.setAttribute('aria-hidden', 'true');
      Object.assign(ghost.style, { left: `${position.x}px`, top: `${position.y}px`,
        width: `${rect.width / getViewport().zoom}px`, height: `${rect.height / getViewport().zoom}px` });
      const copy = source.cloneNode(true) as HTMLElement;
      // DOM cloning preserves canvas dimensions, but not their painted pixels.
      const canvases = source.querySelectorAll('canvas');
      copy.querySelectorAll('canvas').forEach((canvas, index) => {
        const original = canvases[index];
        if (original.width && original.height) canvas.getContext('2d')?.drawImage(original, 0, 0);
      });
      copy.classList.remove('is-selected');
      copy.querySelectorAll('iframe, audio, video, .semantic-handle, .activity-glow, .card-move-grip').forEach(child => child.remove());
      for (const element of [copy, ...copy.querySelectorAll<HTMLElement>('*')]) {
        for (const attribute of [...element.attributes]) {
          if (attribute.name === 'id' || attribute.name === 'data-id' || attribute.name === 'data-card-id'
              || attribute.name === 'data-canvas-motion' || attribute.name.startsWith('data-generation')
              || attribute.name.startsWith('aria-')) element.removeAttribute(attribute.name);
        }
      }
      ghost.append(copy);
      ghosts.get(id)?.remove();
      ghosts.set(id, ghost);
      host.append(ghost);
      const animation = ghost.animate([
        { opacity: 1, scale: '1', translate: '0 0' },
        { opacity: 0, scale: '.94', translate: '0 -4px' },
      ], { duration: 180, easing: 'cubic-bezier(.4,0,1,1)', fill: 'forwards' });
      const remove = () => { ghost.remove(); if (ghosts.get(id) === ghost) ghosts.delete(id); };
      void animation.finished.then(remove, remove);
      track(animation);
    };
    const unsubscribe = useWorldStore.subscribe((next, previous) => {
      if (canvasMotionReduced() || document.hidden) return;
      const { added, removed } = cardMotionChanges(next, previous);
      // Subscribe before React removes the original DOM, so only successful
      // deletions get a copy. Panning and session filtering never reach here.
      removed.forEach(leave);
      for (const id of added) {
        ghosts.get(id)?.remove(); ghosts.delete(id);
        pending.set(id, performance.now() + 600);
      }
      if (pending.size && !frame) frame = requestAnimationFrame(enter);
    });
    const clear = () => {
      cancelAnimationFrame(frame); frame = 0; pending.clear();
      running.forEach(animation => animation.cancel()); running.clear();
      root.querySelectorAll<HTMLElement>('[data-canvas-motion]').forEach(element => element.getAnimations().forEach(animation => animation.cancel()));
      ghosts.forEach(ghost => ghost.remove()); ghosts.clear();
    };
    const media = matchMedia('(prefers-reduced-motion: reduce)');
    const preference = () => { if (media.matches) clear(); };
    const visibility = () => { if (document.hidden) clear(); };
    media.addEventListener('change', preference);
    document.addEventListener('visibilitychange', visibility);
    return () => { unsubscribe(); media.removeEventListener('change', preference); document.removeEventListener('visibilitychange', visibility); clear(); };
  }, [root, screenToFlowPosition, getViewport]);
  return <ViewportPortal><div ref={layer} className="canvas-motion-layer" aria-hidden="true" /></ViewportPortal>;
}
