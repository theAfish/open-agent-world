import type { CardFinish } from './cardFinish';

export const CARD_MATERIAL_IDS = ['normal', 'foil', 'holo', 'aurora', 'laser', 'starlight'] as const;
export type CardMaterialId = typeof CARD_MATERIAL_IDS[number];
export const MATERIAL_DEBUG_VIEWS = ['composite', 'artwork', 'laminate', 'regions', 'protection', 'coverage', 'finishing'] as const;
export type MaterialDebugView = typeof MATERIAL_DEBUG_VIEWS[number];

/** Optical parameters for one material pass. Legacy recipes combine tooling and
 * film with protected print; explicit process stacks compose masked passes in
 * authored order. Neither path introduces a time source. */
export interface CardMaterial {
  version: 1;
  id: CardMaterialId;
  laminate: { opacity: number; roughness: number; metalness: number };
  response: { specular: number; iridescence: number; diffraction: number; sparkle: number };
  pattern: { scale: number; brush: number; domains: number; flow: number; grooves: number };
  clearcoat: { strength: number };
  mask: { artwork: number; frame: number; accent: number };
}
export type MaterialOverrides = { [K in Exclude<keyof CardMaterial, 'version' | 'id'>]?: Partial<CardMaterial[K]> };

/** Bounds are shared by resolution, tests and the live parameter inspector. */
export const MATERIAL_SCHEMA = {
  laminate: { opacity: [0, .32], roughness: [.06, 1], metalness: [0, 1] },
  response: { specular: [0, 1], iridescence: [0, 1], diffraction: [0, 1], sparkle: [0, 1] },
  pattern: { scale: [.25, 4], brush: [0, 1], domains: [0, 1], flow: [0, 1], grooves: [0, 1] },
  clearcoat: { strength: [0, 1] },
  mask: { artwork: [0, 1], frame: [0, 1], accent: [0, 1] },
} as const;

const base: CardMaterial = {
  version: 1, id: 'normal',
  laminate: { opacity: 0, roughness: .85, metalness: 0 },
  response: { specular: 0, iridescence: 0, diffraction: 0, sparkle: 0 },
  pattern: { scale: 1, brush: 0, domains: 0, flow: 0, grooves: 0 },
  clearcoat: { strength: 0 }, mask: { artwork: 1, frame: .65, accent: 1 },
};

export function configureMaterial(material: CardMaterial, overrides: MaterialOverrides = {}): CardMaterial {
  const result = { version: 1, id: material.id } as CardMaterial;
  for (const group of Object.keys(MATERIAL_SCHEMA) as (keyof typeof MATERIAL_SCHEMA)[]) {
    const values: Record<string, number> = {};
    for (const [key, [min, max]] of Object.entries(MATERIAL_SCHEMA[group])) {
      const original = (material[group] as unknown as Record<string, number>)[key];
      const value = (overrides[group] as Record<string, number> | undefined)?.[key] ?? original;
      const fallback = (base[group] as unknown as Record<string, number>)[key];
      values[key] = Math.max(min, Math.min(max, Number.isFinite(value) ? value : Number.isFinite(original) ? original : fallback));
    }
    Object.assign(result, { [group]: values });
  }
  return result;
}
const preset = (id: CardMaterialId, overrides: MaterialOverrides) => configureMaterial({ ...base, id }, overrides);

/** Data only: every preset executes the same optical kernel. */
export const CARD_MATERIALS: Record<CardMaterialId, CardMaterial> = {
  normal: preset('normal', {}),
  foil: preset('foil', { laminate: { opacity: .24, roughness: .27, metalness: 1 },
    response: { specular: .95 }, pattern: { brush: .8 }, clearcoat: { strength: .3 } }),
  holo: preset('holo', { laminate: { opacity: .28, roughness: .3, metalness: .25 },
    response: { specular: .65, iridescence: .65, diffraction: .85 },
    pattern: { domains: 1, scale: 1.5 }, clearcoat: { strength: .25 } }),
  aurora: preset('aurora', { laminate: { opacity: .3, roughness: .42, metalness: .08 },
    response: { specular: .45, iridescence: 1, diffraction: .15 },
    pattern: { flow: 1, scale: .8 }, clearcoat: { strength: .22 } }),
  laser: preset('laser', { laminate: { opacity: .25, roughness: .15, metalness: .3 },
    response: { specular: .75, diffraction: 1, iridescence: .35 },
    pattern: { grooves: 1, scale: 1.6 }, clearcoat: { strength: .08 } }),
  starlight: preset('starlight', { laminate: { opacity: .25, roughness: .5 },
    response: { specular: .04, sparkle: 1 }, clearcoat: { strength: .12 } }),
};

/** Storage IDs and reward weights are independent of the material vocabulary.
 * Preserve the existing flowing rainbow finish when loading old collections. */
export function materialForFinish(finish: CardFinish): CardMaterial {
  return CARD_MATERIALS[finish === 'rainbow' ? 'aurora' : finish];
}

export interface MaterialEnvironment {
  /** Card-local white area light direction, independently controlled from the view. */
  light: readonly [number, number, number];
  intensity: number;
  ambient: number;
}
export const DEFAULT_MATERIAL_ENVIRONMENT: MaterialEnvironment = { light: [-.35, -.5, 1.4], intensity: 1, ambient: .3 };
export function resolveEnvironment(value: Partial<MaterialEnvironment> = {}): MaterialEnvironment {
  const vector = value.light ?? DEFAULT_MATERIAL_ENVIRONMENT.light;
  const light = vector.length === 3 && vector.every(Number.isFinite) && Math.hypot(...vector) > .001
    ? vector : DEFAULT_MATERIAL_ENVIRONMENT.light;
  const bound = (v: number | undefined, fallback: number) => Math.max(0, Math.min(2, Number.isFinite(v) ? v! : fallback));
  return { light, intensity: bound(value.intensity, 1), ambient: bound(value.ambient, .3) };
}
