import type { NodeTypeCatalogItem } from '../types/world';

export const CARD_FACE_VARIANTS = ['icon', 'image', 'text', 'compact', 'dark'] as const;
export const CARD_FACE_TONES = ['midnight', 'sage', 'sand', 'sky', 'rose', 'stone'] as const;
export type CardFaceVariant = typeof CARD_FACE_VARIANTS[number];
export type CardFaceTone = typeof CARD_FACE_TONES[number];
export interface CardFaceDesign {
  variant: CardFaceVariant;
  tone: CardFaceTone;
  image_url?: string | null;
}

/** Old catalogs get calm, deterministic defaults; plugins can choose explicitly. */
export function cardFaceDesign(definition?: Pick<NodeTypeCatalogItem, 'id' | 'traits' | 'card_face'>): CardFaceDesign {
  if (definition?.card_face) return definition.card_face;
  const traits = definition?.traits ?? [];
  if (definition?.id === 'image') return { variant: 'image', tone: 'sky' };
  if (definition?.id === 'text') return { variant: 'text', tone: 'rose' };
  if (definition?.id === 'sandbox') return { variant: 'dark', tone: 'midnight' };
  if (traits.includes('core.agent') || definition?.id === 'agent') return { variant: 'icon', tone: 'sage' };
  if (traits.includes('ui.skill.v1') || traits.includes('ui.skill-package.v1')) return { variant: 'icon', tone: 'sage' };
  if (traits.includes('ui.task-board.v1')) return { variant: 'compact', tone: 'stone' };
  if (definition?.id === 'conversation') return { variant: 'text', tone: 'sand' };
  return { variant: 'icon', tone: definition ? 'sand' : 'stone' };
}
