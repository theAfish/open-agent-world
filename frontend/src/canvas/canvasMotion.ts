import type { WorldCard } from '../types/world';

interface CardSnapshot {
  cards: WorldCard[];
  loadedChunkKeys: string[];
  syncState: string;
}

/** Snapshot/chunk replacement is navigation, not a card operation. */
export function cardMotionChanges(next: CardSnapshot, previous: CardSnapshot) {
  if (next.cards === previous.cards || next.loadedChunkKeys !== previous.loadedChunkKeys
      || next.syncState === 'loading' || previous.syncState === 'loading') return { added: [], removed: [] };
  const before = new Set(previous.cards.map(card => card.id));
  const after = new Set(next.cards.map(card => card.id));
  return {
    added: next.cards.filter(card => !before.has(card.id)).map(card => card.id),
    removed: previous.cards.filter(card => !after.has(card.id)).map(card => card.id),
  };
}

const animations = new WeakMap<HTMLElement, Animation>();
export const canvasMotionReduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;

export function animateCard(element: HTMLElement) {
  animations.get(element)?.cancel();
  if (canvasMotionReduced() || document.hidden) return;
  element.dataset.canvasMotion = 'enter';
  const animation = element.animate([
    { opacity: 0, translate: '0 8px', scale: '.96' },
    { opacity: 1, translate: '0 0', scale: '1' },
  ], { duration: 260, easing: 'cubic-bezier(.2,.75,.25,1)' });
  animations.set(element, animation);
  const clear = () => {
    if (animations.get(element) !== animation) return;
    animations.delete(element);
    delete element.dataset.canvasMotion;
  };
  animation.onfinish = clear;
  animation.oncancel = clear;
  return animation;
}

