import type { PackPackaging } from '../types/world';
export type { PackPackaging } from '../types/world';

export const PACK_PACKAGING = ['standard', 'premium', 'paper', 'collector'] as const;

/** Stable IDs shared with PackDefinition.packaging in the plugin API. */
export const PACK_DESIGNS: Record<PackPackaging, { label: string; color: string }> = {
  standard: { label: 'Standard Pack', color: '#b5d0d2' },
  premium: { label: 'Premium Pack', color: '#ded4c2' },
  paper: { label: 'Paper Sleeve', color: '#b9c3aa' },
  collector: { label: 'Collector Box', color: '#b9cedc' },
};

export function packPackaging(value?: string | null): PackPackaging {
  return PACK_PACKAGING.find(preset => preset === value) ?? 'standard';
}
