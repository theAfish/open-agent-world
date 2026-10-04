import type { CardFinish } from './cardFinish';

/** Optical properties of the laminate between artwork and protected top print. */
export interface MaterialEffect {
  substrate: 'printed';
  roughness: number;
  specular: number;
  iridescence: number;
  diffraction: number;
  edgeFoil: number;
  spotGloss: number;
  sparkle: number;
  emissive: number;
  /** Studio reflection radii and ambient reflectance of the laminate. */
  macro: readonly [width: number, height: number, reflectance: number];
  /** Contrast of the material's interference contours / brushing. */
  mesoscopic: number;
  /** Coherent brush, diffraction domains, isolated flakes, engraved grooves. */
  structure: readonly [number, number, number, number];
}

// Persisted finish IDs stay compatible with existing collections and world cards.
export const CARD_MATERIALS: Record<Exclude<CardFinish, 'normal'>, MaterialEffect> = {
  foil: { substrate: 'printed', roughness: .28, specular: .92, iridescence: .015,
    diffraction: 0, edgeFoil: .8, spotGloss: .35, sparkle: 0, emissive: 0,
    macro: [.62, .8, .28], mesoscopic: .08, structure: [1, 0, 0, 0] },
  rainbow: { substrate: 'printed', roughness: .32, specular: .8, iridescence: .95,
    diffraction: .8, edgeFoil: .68, spotGloss: .24, sparkle: 0, emissive: 0,
    macro: [.78, .85, .52], mesoscopic: .16, structure: [0, 1, 0, 0] },
  starlight: { substrate: 'printed', roughness: .42, specular: .3, iridescence: .035,
    diffraction: 0, edgeFoil: .3, spotGloss: .72, sparkle: .85, emissive: .015,
    macro: [.42, .4, .018], mesoscopic: .025, structure: [0, 0, 1, 0] },
  laser: { substrate: 'printed', roughness: .25, specular: .7, iridescence: .32,
    diffraction: .68, edgeFoil: .55, spotGloss: .3, sparkle: 0, emissive: 0,
    macro: [.52, .5, .04], mesoscopic: .16, structure: [0, 0, 0, 1] },
};

/** Uncoated background and text have exactly zero response, not a faint colour wash. */
export const MATERIAL_REGIONS = {
  artwork: 1, frame: .85, icon: .25, accents: 1, background: 0, text: 0,
} as const;
