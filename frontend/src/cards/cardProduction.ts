import type { CardFinish } from './cardFinish';
import { CARD_MATERIALS, configureMaterial, type CardMaterial } from './cardMaterial';

export const CARD_STOCKS = {
  cotton: { label: '棉纤粗纹', description: '纤维感', grain: .85, paper: '#f3eee2', ink: '#303a32', muted: '#667066', edge: '#cfc6b4' },
  ivory: { label: '哑光卡纸', description: '细腻柔和', grain: .32, paper: '#f5f3e9', ink: '#293d37', muted: '#5f6d63', edge: '#d3ccbc' },
  ink: { label: '染色卡纸', description: '厚实质感', grain: .5, paper: '#233332', ink: '#edf0e4', muted: '#b7c5b9', edge: '#152321' },
  pearl: { label: '细面涂布', description: '平滑纸面', grain: .08, paper: '#edf0ed', ink: '#303c48', muted: '#636e7a', edge: '#cbd1d2' },
} as const;
export const LAMINATES = ['none', 'gloss', 'holo', 'aurora', 'laser', 'starlight'] as const;
export type LaminateType = typeof LAMINATES[number];
export const MAX_PRODUCTION_LAYERS = 24;
export const PRODUCTION_LAYER_KINDS = ['ink', 'laminate', 'foil', 'emboss', 'uv'] as const;
export type ProductionLayerKind = typeof PRODUCTION_LAYER_KINDS[number];
export const PRODUCTION_MASK_SOURCES = ['all', 'text', 'shapes', 'artwork', 'accents', 'frame', 'elements', 'preset', 'png'] as const;
export const PRODUCTION_MASK_PRESETS = ['border', 'corners', 'diagonal', 'dots'] as const;
export interface ProductionMask {
  source: typeof PRODUCTION_MASK_SOURCES[number];
  elementIds: string[];
  preset: typeof PRODUCTION_MASK_PRESETS[number];
  png: string;
  fit?: 'contain' | 'cover' | 'stretch';
  channel: 'alpha' | 'luminance';
  invert: boolean;
}
export interface ProductionLayer {
  id: string;
  kind: ProductionLayerKind;
  enabled: boolean;
  strength: number;
  roughness: number;
  color: string;
  film: Exclude<LaminateType, 'none'>;
  relief: 'raised' | 'recessed';
  mask: ProductionMask;
  /** Elements owned by this pass; non-ink passes use their silhouettes. */
  content?: { source: 'all' | 'elements'; elementIds: string[] };
  pattern?: { motif: CardProduction['print']['motif']; density: number };
  blend?: 'normal' | 'multiply' | 'screen';
}
export type PrintProof = 'composite' | 'artwork' | 'finishing' | 'laminate' | 'protection';
export interface PrintFinishing {
  spotUV: number; foil: number; emboss: number; edgeFoil: number;
  target: 'accents' | 'artwork'; foilTone: 'silver' | 'gold';
}
/** Portable production intent. Explicit passes run in array order, bottom to top. */
export interface CardProduction {
  version: 1;
  stock: { type: keyof typeof CARD_STOCKS; grain: number; color?: string };
  print: { motif: 'contour' | 'rays' | 'grid' | 'none'; density: number; layered?: boolean };
  finishing: PrintFinishing;
  laminate: { type: LaminateType; strength: number; roughness: number };
  /** Omitted uses the legacy finishing/laminate pair; [] deliberately has no processes. */
  layers?: ProductionLayer[];
}
export const NO_FINISHING: PrintFinishing = { spotUV: 0, foil: 0, emboss: 0, edgeFoil: 0, target: 'accents', foilTone: 'gold' };
export function normalizeFinishing(value: Partial<PrintFinishing> = {}): PrintFinishing {
  const strength=(v: number | undefined)=>Number.isFinite(v)?Math.max(0,Math.min(1,v!)):0;
  return { spotUV:strength(value.spotUV),foil:strength(value.foil),emboss:strength(value.emboss),edgeFoil:strength(value.edgeFoil),
    target:value.target==='artwork'?'artwork':'accents',foilTone:value.foilTone==='silver'?'silver':'gold' };
}
export function productionForFinish(finish: CardFinish = 'normal'): CardProduction {
  return { version: 1, stock: { type: 'ivory', grain: .32 }, print: { motif: 'contour', density: .38 },
    finishing: { ...NO_FINISHING, ...(finish === 'foil' ? { foil: .7, edgeFoil: .55, emboss: .2 } : {}) },
    laminate: { type: finish === 'rainbow' ? 'aurora' : finish === 'laser' || finish === 'starlight' ? finish : 'none',
      strength: .45, roughness: .38 } };
}
export function compileProduction(production: CardProduction): { material: CardMaterial; finishing: PrintFinishing } {
  const film = production.laminate, id = film.type === 'none' || film.type === 'gloss' ? 'normal' : film.type;
  const material = configureMaterial(CARD_MATERIALS[id], {
    laminate: { opacity: film.type === 'none' ? 0 : film.type === 'gloss' ? .035 * film.strength : .3 * film.strength, roughness: film.roughness },
    clearcoat: { strength: film.type === 'none' ? 0 : film.type === 'gloss' ? .8 * film.strength : .22 * film.strength },
    response: { sparkle: CARD_MATERIALS[id].response.sparkle * film.strength },
    mask: { artwork: 1, frame: .25, accent: .7 },
  });
  return { material, finishing: normalizeFinishing(production.finishing) };
}
function defaultProductionLayer(kind: ProductionLayerKind, id: string): ProductionLayer {
  return { id, kind, enabled: true, strength: kind === 'ink' ? .5 : kind === 'laminate' ? .45 : .7,
    roughness: .38, color: kind === 'foil' ? '#d6ae61' : '#31594b', film: 'holo', relief: 'raised',
    mask: { source: kind === 'laminate' ? 'all' : kind === 'uv' ? 'artwork' : 'text',
      elementIds: [], preset: 'border', png: '', channel: 'alpha', invert: false } };
}
export function newProductionLayer(kind: ProductionLayerKind): ProductionLayer {
  return defaultProductionLayer(kind, `process-${crypto.randomUUID()}`);
}
/** Legacy recipes stay untouched until the author explicitly edits their process stack. */
export function productionLayers(production: CardProduction): ProductionLayer[] {
  if (production.layers !== undefined) return production.layers.map(layer => ({ ...layer, ...(layer.content ? { content: { ...layer.content, elementIds: [...layer.content.elementIds] } } : {}),
    mask: { ...layer.mask, elementIds: [...layer.mask.elementIds] } }));
  const result: ProductionLayer[] = [], finishing = normalizeFinishing(production.finishing);
  const append = (kind: ProductionLayerKind, strength: number, edge = false) => {
    if (strength <= 0) return;
    const layer = defaultProductionLayer(kind, `legacy-${edge ? 'edge-foil' : kind}`);
    layer.strength = strength;
    layer.color = finishing.foilTone === 'silver' ? '#c7cdd2' : '#d6ae61';
    layer.mask.source = edge ? 'frame' : kind === 'uv' || finishing.target === 'artwork' ? 'artwork' : 'accents';
    result.push(layer);
  };
  append('emboss', finishing.emboss);
  append('foil', finishing.foil);
  append('foil', finishing.edgeFoil, true);
  append('uv', finishing.spotUV);
  if (production.laminate.type !== 'none') {
    const layer = defaultProductionLayer('laminate', 'legacy-laminate');
    layer.film = production.laminate.type;
    layer.strength = production.laminate.strength;
    layer.roughness = production.laminate.roughness;
    result.push(layer);
  }
  return result;
}
/** Compile one pass only. Coverage, ink colour, foil tint and relief direction belong to its renderer. */
export function compileProductionLayer(layer: ProductionLayer): { material: CardMaterial; finishing: PrintFinishing } {
  const strength = layer.enabled ? Math.max(0, Math.min(1, Number.isFinite(layer.strength) ? layer.strength : 0)) : 0;
  const production = productionForFinish();
  production.laminate = { type: layer.kind === 'laminate' ? layer.film : 'none', strength, roughness: layer.roughness };
  const finishing = { ...NO_FINISHING, target: 'artwork' as const };
  if (layer.kind === 'foil') finishing.foil = strength;
  if (layer.kind === 'emboss') finishing.emboss = strength;
  if (layer.kind === 'uv') finishing.spotUV = strength;
  production.finishing = finishing;
  const compiled = compileProduction(production);
  return { material: configureMaterial(compiled.material, { mask: { artwork: 1, frame: 1, accent: 1 } }), finishing };
}
/** Custom paper colour is independent of stock texture; old recipes retain their original palette. */
export function resolveStock(production: CardProduction) {
  const stock = CARD_STOCKS[production.stock.type];
  const paper = production.stock.color;
  if (!paper || !/^#[0-9a-f]{6}$/i.test(paper)) return stock;
  const rgb = [1, 3, 5].map(start => parseInt(paper.slice(start, start + 2), 16));
  const linear = rgb.map(value => { const channel = value / 255; return channel <= .04045 ? channel / 12.92 : ((channel + .055) / 1.055) ** 2.4; });
  const luminance = linear[0] * .2126 + linear[1] * .7152 + linear[2] * .0722;
  const light = luminance > .179;
  const mix = (colour: readonly number[], amount: number) => '#' + rgb.map((value, i) =>
    Math.round(value + (colour[i] - value) * amount).toString(16).padStart(2, '0')).join('');
  const foreground = light ? [25, 34, 29] : [246, 246, 237];
  return { ...stock, paper, ink: light ? '#19221d' : '#f6f6ed', muted: mix(foreground, .68), edge: mix(foreground, .24) };
}
export function stockStyle(production: CardProduction) {
  const stock = resolveStock(production);
  return { '--stock-paper': stock.paper, '--stock-ink': stock.ink, '--stock-muted': stock.muted,
    '--stock-edge': stock.edge, '--stock-grain': production.stock.grain, '--print-density': production.print.density };
}
export function hasFinishing(value: PrintFinishing) { return value.spotUV + value.foil + value.emboss + value.edgeFoil > 0; }

/** Opening an older card is read-only; its first stack edit makes the original print explicit. */
export function printingLayers(production: CardProduction): ProductionLayer[] {
  const layers = productionLayers(production);
  if (production.print.layered) return layers;
  const base = newPrintLayer();
  base.id = 'original-print';
  while (layers.some(layer => layer.id === base.id)) base.id += '-base';
  base.content = { source: 'all', elementIds: [] };
  return [base, ...layers];
}
export function newPrintLayer(): ProductionLayer {
  const layer = newProductionLayer('ink');
  return { ...layer, strength: 1, blend: 'normal', mask: { ...layer.mask, source: 'all' }, content: { source: 'elements', elementIds: [] } };
}
/** New editor passes infer coverage from their own elements. Legacy masks stay readable. */
export function newDesignLayer(kind: ProductionLayerKind): ProductionLayer {
  const layer = kind === 'ink' ? newPrintLayer() : newProductionLayer(kind);
  return { ...layer, blend: 'normal', content: { source: 'elements', elementIds: [] },
    mask: { ...layer.mask, source: kind === 'foil' ? 'elements' : 'all', elementIds: [] } };
}
export function resolveElementCoverage(layer: ProductionLayer, layers: ProductionLayer[], ids: string[]): ProductionLayer {
  if (!layer.content || layer.kind === 'ink') return layer;
  const owned = printContentIds(layer, layers, ids);
  return { ...layer, mask: { ...layer.mask, source: owned.length || layer.kind === 'foil' ? 'elements' : 'all', elementIds: owned, invert: false } };
}
/** The original plate owns content not assigned to a later, explicit ink plate. */
export function printContentIds(layer: ProductionLayer, layers: ProductionLayer[], ids: string[]): string[] {
  if (!layer.content) return [];
  if (layer.content.source === 'elements') return layer.content.elementIds.filter(id => ids.includes(id));
  const assigned = new Set(layers.flatMap(item => item.content?.source === 'elements' ? item.content.elementIds : []));
  return ids.filter(id => !assigned.has(id));
}
