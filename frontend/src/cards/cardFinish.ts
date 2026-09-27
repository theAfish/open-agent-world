/** Owned material, independent of the card's catalog definition. */
export const CARD_FINISHES = ['normal', 'foil', 'rainbow', 'starlight', 'laser'] as const;
export type CardFinish = typeof CARD_FINISHES[number];

/** Old snapshots and unknown future materials remain readable. Never roll here. */
export function normalizeCardFinish(value: unknown): CardFinish {
  return CARD_FINISHES.includes(value as CardFinish) ? value as CardFinish : 'normal';
}

export function finishLabel(value: unknown): string {
  const finish = normalizeCardFinish(value);
  return finish[0].toUpperCase() + finish.slice(1);
}
