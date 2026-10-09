import type { PackPackaging } from '../../types/world';

type Point = [number, number, number];
export const ease = (value: number) => { const t = Math.max(0, Math.min(1, value)); return t * t * (3 - 2 * t); };
const phase = (progress: number, start: number, end: number) => ease((progress - start) / (end - start));

/** Paths are in the package's local space: +Y is the mouth, +Z is the box face. */
export const PACK_OPENING_PRESETS = {
  standard: { exit: 'top', release: .18, clear: .57, display: .78, height: 2.95, fan: .32 },
  premium: { exit: 'top', release: .22, clear: .60, display: .80, height: 3.02, fan: .30 },
  paper: { exit: 'top', release: .30, clear: .66, display: .82, height: 2.97, fan: .26 },
  collector: { exit: 'front', release: .44, clear: .63, display: .82, height: 3.02, fan: .32 },
} as const;

export function openingPose(packaging: PackPackaging, progress: number, count: number) {
  const preset = PACK_OPENING_PRESETS[packaging];
  const open = phase(progress, 0, packaging === 'paper' ? .28 : .18);
  const pull = phase(progress, preset.release, preset.clear);
  const fan = phase(progress, preset.clear, preset.display);
  const fade = phase(progress, .86, 1);
  // Return after the cards clear, resting the cap above the rim with an in-plane twist.
  const boxSettle = phase(progress, .82, 1);
  const boxSwing = phase(progress, .17, .42) * 1.95 * (1 - boxSettle);
  const closurePosition: Point = packaging === 'collector'
    ? [-1.2 * (1 - Math.cos(boxSwing)), 0, .35 + open * (.50 * (1 - boxSettle) + .11 * boxSettle) + Math.sin(boxSwing) * 1.2]
    : packaging === 'paper' ? [0, 1.61, .103 - phase(progress, .12, .28) * .20] : [open * .58, open * .7, open * .15];
  const closureRotation: Point = packaging === 'collector' ? [0, -boxSwing, -.10 * boxSettle]
    : packaging === 'paper' ? [-open * 3.25, 0, 0] : [0, 0, -open * .24];
  return {
    closurePosition, closureRotation, closureVisible: packaging === 'paper' || packaging === 'collector' || progress < .20,
    mouth: phase(progress, .10, .18), bodyDepth: 1 - phase(progress, .73, 1) * .77,
    cards: Array.from({ length: count }, (_, i) => {
      const offset = i - (count - 1) / 2;
      return {
        position: [offset * fan * preset.fan,
          (preset.exit === 'front' ? fan : pull) * preset.height + fade * .2,
          offset * .032 + (preset.exit === 'front' ? .06 + pull * .90 : fan * .12)] as Point,
        rotation: [-fan * .06, 0, -offset * fan * .13] as Point,
        // Scale around each card's centre, never the pack origin (which pulls cards back inside).
        scale: 1 - fade * .16, opacity: 1 - fade,
      };
    }),
  };
}

/** Three.js uses Y-up: positive pitch points the face down towards a lower pointer. */
export function hoverAngles(x: number, y: number) {
  const clamp = (v: number) => Math.max(-.5, Math.min(.5, v));
  return { yaw: clamp(x) * 28, pitch: clamp(y) * 22 };
}
