import type { CardFaceTone, CardFaceVariant } from '../components/cardFaceDesign';
import type { NodeSurfaceLevel, PluginCatalog, WorldCard } from '../types/world';
import type { FaceBox, FaceDesign, FaceElement, FaceStudio, SurfaceDesign } from './types';
import { recipeSettings, reflowSurface } from './designRecipes';

export const FACE_MODES: NodeSurfaceLevel[] = ['node', 'preview', 'inspector', 'workspace'];
export const MODE_LABELS: Record<NodeSurfaceLevel, string> = { node: '节点', preview: '卡片', inspector: '详细', workspace: '工作区' };
export const ELEMENT_LABELS: Record<FaceElement['kind'], string> = {
  title: '标题', description: '说明', icon: '图标', help: '操作提示', fields: '输入字段', action: '运行按钮', result: '结果区域', text: '自由文本',
  subtitle: '副标题', metadata: '元信息', status: '状态', tags: '标签', illustration: '插图', badge: '徽标',
};
export const FACE_PALETTES: Record<CardFaceTone, [string, string]> = {
  midnight: ['#273b42', '#edf1e9'], sage: ['#e2eadf', '#24382f'], sand: ['#eee1cc', '#473e31'],
  sky: ['#dceaf0', '#263d4b'], rose: ['#eedde0', '#51343e'], stone: ['#e6e4dd', '#3c403b'],
};

/** Presets are ordinary editable layers, so there is no hidden template geometry. */
export function presetSurface(mode: NodeSurfaceLevel, face: FaceDesign, preset: CardFaceVariant = face.variant): SurfaceDesign {
  const [width, height] = ({ node: [112, 112], preview: [240, 320], inspector: [438, 570], workspace: [1020, 700] })[mode];
  const [fill, color] = preset === 'dark' ? FACE_PALETTES.midnight : FACE_PALETTES[face.tone];
  const elements: FaceElement[] = [];
  const add = (kind: FaceElement['kind'], x: number, y: number, w: number, h: number, font = 16, align: FaceElement['align'] = 'left') =>
    elements.push({ id: kind, kind, x, y, width: w, height: h, font_size: font, color, align, text: '' });
  if (mode === 'node') {
    if (preset !== 'text') add('icon', 36, 16, 40, 40, 32, 'center');
    add('title', 14, preset === 'text' ? 28 : 66, width - 28, 36, 13, 'center');
  } else if (mode === 'preview') {
    if (preset === 'compact') { add('icon', 20, 24, 32, 32, 28); add('title', 62, 22, 158, 56, 20); }
    else { if (preset !== 'text') add('icon', 24, preset === 'image' ? 128 : 24, 56, 56, 42); add('title', 24, preset === 'text' ? 30 : preset === 'image' ? 194 : 104, 192, 62, 24); }
    add('description', 24, preset === 'compact' ? 98 : preset === 'image' ? 260 : 186, 192, preset === 'image' ? 40 : 96, 14);
  } else {
    const margin = 28, wide = mode === 'workspace', contentX = wide ? 350 : margin, contentW = wide ? width - 382 : width - 56;
    if (preset !== 'text') add('icon', margin, margin, 48, 48, 40);
    add('title', margin, 96, wide ? 290 : width - 56, 52, 28);
    add('description', margin, 160, wide ? 290 : width - 56, wide ? 150 : 54, 15);
    add('help', contentX, wide ? margin : 224, contentW, 44, 13);
    add('fields', contentX, wide ? 88 : 278, contentW, wide ? 300 : 110, 14);
    add('action', contentX, wide ? 410 : 402, contentW, 42, 14);
    add('result', contentX, wide ? 476 : 464, contentW, wide ? 194 : 78, 14);
  }
  return { width, height, preset, tone: preset === 'dark' ? 'midnight' : face.tone, field_layout: face.layout, shapes: [{ id: 'base', kind: mode === 'node' ? 'ellipse' : 'rect', x: 0, y: 0, width, height, radius: 24, fill, points: [] }],
    elements, background_png: '', image_fit: 'contain', image_shape: true };
}

export function faceStudio(face: FaceDesign): FaceStudio {
  if (!face.studio) return { version: 1, enabled: [...FACE_MODES], initial: 'preview', open: 'workspace',
    modes: Object.fromEntries(FACE_MODES.map(mode => [mode, reflowSurface(presetSurface(mode, face), face)])) };
  // Upgrade untouched factory presets when editing; preserve authored v1 geometry.
  const shared = recipeSettings(face, face.studio.modes.preview), modes = { ...face.studio.modes };
  let changed = false;
  for (const mode of FACE_MODES) {
    const surface = modes[mode];
    if (!surface) continue;
    if (surface.design) {
      const compiled = reflowSurface(surface, face);
      if (JSON.stringify(compiled) !== JSON.stringify(surface)) { modes[mode] = compiled; changed = true; }
      continue;
    }
    if (surface.background_png) continue;
    const preset = presetSurface(mode, face, surface.preset);
    const sameItems = (items: object[], defaults: object[]) => items.length === defaults.length && items.every((item, i) =>
      Object.entries(defaults[i]).every(([key, value]) => JSON.stringify((item as Record<string, unknown>)[key]) === JSON.stringify(value)));
    if (surface.width !== preset.width || surface.height !== preset.height || !sameItems(surface.shapes, preset.shapes)
      || !sameItems(surface.elements, preset.elements) || surface.elements.some(e => e.placement === 'free' || e.image_png || Object.keys(e.overrides ?? {}).length)) continue;
    modes[mode] = reflowSurface(surface, face, { ...shared, tokens: {} }); changed = true;
  }
  return changed ? { ...face.studio, modes } : face.studio;
}

export type FaceCard = Pick<WorldCard, 'type'> & Partial<Pick<WorldCard, 'config'>>;
export function cardStudio(card: FaceCard, catalog: PluginCatalog): FaceStudio | undefined {
  if (!catalog.node_types.find(type => type.id === card.type)?.traits.includes('ui.factory-card.v1')) return;
  return (card.config?.face as FaceDesign | undefined)?.studio;
}

/** Snap the moving object's edges/center to the canvas, other layers and an 8px grid. */
export function snapBox(box: FaceBox, surface: SurfaceDesign, others: FaceBox[], enabled: boolean, resize = false) {
  const result = { ...box }, guides: { axis: 'x' | 'y'; position: number }[] = [];
  for (const axis of ['x', 'y'] as const) {
    const dimension = axis === 'x' ? 'width' : 'height', limit = surface[dimension];
    if (!enabled) {
      if (resize) result[dimension] = Math.max(8, Math.min(limit - result[axis], result[dimension]));
      else result[axis] = Math.max(0, Math.min(limit - result[dimension], result[axis]));
      continue;
    }
    const anchors = [0, limit / 2, limit, ...others.flatMap(item => [item[axis], item[axis] + item[dimension] / 2, item[axis] + item[dimension]])];
    const moving = resize ? [box[axis] + box[dimension]] : [box[axis], box[axis] + box[dimension] / 2, box[axis] + box[dimension]];
    let delta = 7, guide: number | undefined;
    for (const target of anchors) for (const point of moving) if (Math.abs(target - point) < Math.abs(delta)) { delta = target - point; guide = target; }
    if (resize) result[dimension] = Math.max(8, Math.min(limit - box[axis], guide === undefined ? Math.round((box[axis] + box[dimension]) / 8) * 8 - box[axis] : box[dimension] + delta));
    else result[axis] = Math.max(0, Math.min(limit - box[dimension], guide === undefined ? Math.round(box[axis] / 8) * 8 : box[axis] + delta));
    if (guide !== undefined) guides.push({ axis, position: guide });
  }
  return { box: result, guides };
}

export function resizeDesign(surface: SurfaceDesign, width: number, height: number): SurfaceDesign {
  const position = (value: number) => Math.max(-2048, Math.min(2048, Math.round(value)));
  const dimension = (value: number) => Math.max(8, Math.min(2048, Math.round(value)));
  const scale = <T extends FaceBox>(item: T): T => {
    const element = item as unknown as FaceElement;
    const fixed = element.placement === 'free' && (element.sizing === 'fixed' || element.sizing === 'hug');
    const x = fixed ? element.pin === 'end' ? item.x + width - surface.width : element.pin === 'center' ? item.x + (width - surface.width) / 2 : item.x : item.x * width / surface.width;
    return { ...item, x: position(x), y: position(item.y * height / surface.height), width: dimension(fixed ? item.width : item.width * width / surface.width), height: dimension(item.height * height / surface.height) };
  };
  return { ...surface, width, height, shapes: surface.shapes.map(scale), elements: surface.elements.map(scale) };
}
