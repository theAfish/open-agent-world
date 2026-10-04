import { normalizeCardFinish, type CardFinish } from './cardFinish';
import { CardMaterialCanvas } from './CardMaterialCanvas';
import './cardFinish.css';

export type CardFinishQuality = 'thumbnail' | 'standard' | 'showcase';
/** Chrome is retained as a compatibility guard: application surfaces never mount materials. */
export type CardFinishSurface = 'card' | 'chrome';

export function CardFinishLayer({ finish, quality = 'standard', reveal = false, surface = 'card', restrained = false, roughness }: {
  finish?: CardFinish;
  quality?: CardFinishQuality;
  reveal?: boolean;
  surface?: CardFinishSurface;
  restrained?: boolean;
  roughness?: number;
}) {
  const material = normalizeCardFinish(finish);
  if (material === 'normal' || surface === 'chrome') return null;
  return <span className={`card-finish-layer card-finish-layer--${material}`}
    data-finish={material} data-quality={quality} data-material-surface={surface} data-material-layer="laminate"
    data-reveal={reveal || undefined} aria-hidden="true">
    <CardMaterialCanvas finish={material} quality={quality} restrained={restrained} roughness={roughness} />
  </span>;
}
