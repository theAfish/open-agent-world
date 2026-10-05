import { useEffect, useState, type RefObject } from 'react';
import type { CardFinish } from '../cards/cardFinish';
import { MATERIAL_LIGHT_EVENT, type MaterialLight } from '../cards/cardMaterialRenderer';

export type StudyMode = 'pointer' | 'representative' | 'sweep';
/** Characteristic angles only drive the development gallery, never production cards. */
export const STUDY_ANGLES: Record<CardFinish, { x: number; y: number }> = {
  normal: { x: 0, y: 0 },
  foil: { x: -.35, y: .1 },
  rainbow: { x: .45, y: .15 },
  starlight: { x: .4, y: 0 },
  laser: { x: .45, y: -.4 },
};

export function useMaterialStudy(ref: RefObject<HTMLElement>, mode: StudyMode, revision: string, comparison = false) {
  const [reduced, setReduced] = useState(() => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? true);
  useEffect(() => {
    const media = window.matchMedia?.('(prefers-reduced-motion: reduce)');
    if (!media) return;
    const update = () => setReduced(media.matches);
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);
  useEffect(() => {
    if (!comparison || mode !== 'pointer' || !ref.current) return;
    const cards = [...ref.current.querySelectorAll<HTMLElement>('[data-preview-finish]')];
    let relaying = false;
    const sync = (event: Event) => {
      if (relaying) return;
      relaying = true;
      const pose = (event as CustomEvent<MaterialLight>).detail;
      for (const card of cards) if (card !== event.target) {
        card.style.setProperty('--card-angle-x', `${-pose.y*7}deg`);
        card.style.setProperty('--card-angle-y', `${pose.x*7}deg`);
        card.dispatchEvent(new CustomEvent(MATERIAL_LIGHT_EVENT, { detail: pose }));
      }
      relaying = false;
    };
    cards.forEach(card => card.addEventListener(MATERIAL_LIGHT_EVENT, sync));
    return () => cards.forEach(card => card.removeEventListener(MATERIAL_LIGHT_EVENT, sync));
  }, [ref, comparison, mode, revision]);
  useEffect(() => {
    const root = ref.current;
    if (!root || mode === 'pointer') return;
    const cards = [...root.querySelectorAll<HTMLElement>('[data-preview-finish]')];
    let pending: number | null = null, visible = true, started = 0, previous = -Infinity;
    const paint = (phase = 0) => {
      for (const card of cards) {
        const finish = card.dataset.previewFinish as CardFinish, pose = STUDY_ANGLES[comparison ? 'rainbow' : finish];
        const sweeping = mode === 'sweep' && !reduced;
        const x = pose.x+(sweeping ? Math.sin(phase)*.45 : 0);
        const y = pose.y+(sweeping ? Math.sin(phase*.5)*.3 : 0);
        const light: MaterialLight = { x, y, active: finish !== 'normal', immediate: true };
        card.style.setProperty('--card-angle-x', `${-y*7}deg`);
        card.style.setProperty('--card-angle-y', `${x*7}deg`);
        card.dataset.studyAngle = `${x.toFixed(3)},${y.toFixed(3)}`;
        card.dispatchEvent(new CustomEvent(MATERIAL_LIGHT_EVENT, { detail: light }));
      }
    };
    const tick = (now: number) => {
      pending = null;
      if (!started) started = now;
      if (now-previous >= 1000/24) { paint((now-started)/18000*Math.PI*2); previous = now; }
      if (visible && !document.hidden && mode === 'sweep' && !reduced) pending = requestAnimationFrame(tick);
    };
    const resume = () => {
      if (pending !== null) { cancelAnimationFrame(pending); pending = null; }
      if (visible && !document.hidden) pending = requestAnimationFrame(tick);
    };
    const intersection = new IntersectionObserver(entries => { visible = entries[0].isIntersecting; resume(); });
    intersection.observe(root);
    document.addEventListener('visibilitychange', resume);
    // Apply even before the material canvas's first visibility notification.
    paint(); resume();
    return () => {
      if (pending !== null) cancelAnimationFrame(pending);
      intersection.disconnect(); document.removeEventListener('visibilitychange', resume);
      for (const card of cards) {
        card.style.removeProperty('--card-angle-x'); card.style.removeProperty('--card-angle-y');
        delete card.dataset.studyAngle;
        card.dispatchEvent(new CustomEvent(MATERIAL_LIGHT_EVENT, { detail: { x: 0, y: 0, active: false } }));
      }
    };
  }, [ref, mode, revision, reduced, comparison]);
  return reduced;
}
