import type { CardFinish } from '../cards/cardFinish';
import type { DesignTokens, FaceDesign, FaceElement, LayoutRecipe, MaterialType, StyleKit, SurfaceDesign, SurfaceRecipe } from './types';

export const RECIPES: { id: LayoutRecipe; name: string; description: string }[] = [
  { id: 'hero', name: 'Hero', description: '主角 · 清晰的视觉重心' },
  { id: 'compact', name: 'Compact', description: '紧凑 · 轻巧而有序' },
  { id: 'split', name: 'Split', description: '分栏 · 图文并置' },
  { id: 'badge', name: 'Badge', description: '徽章 · 居中展示' },
  { id: 'editorial', name: 'Editorial', description: '叙事 · 文字优先' },
  { id: 'utility', name: 'Utility', description: '工具 · 内容与操作' },
  { id: 'minimal', name: 'Minimal', description: '留白 · 少即是多' },
  { id: 'poster', name: 'Poster', description: '海报 · 突出图像' },
];

export const STYLE_KITS: Record<StyleKit, { name: string; description: string; background: string; surface: string; text: string; muted: string; border: string; accent: string; response: number }> = {
  sand: { name: 'Sand', description: '温暖砂岩', background: '#eee5d6', surface: '#f5eee3', text: '#403a31', muted: '#706555', border: '#d8cdbb', accent: '#9a7153', response: .72 },
  paper: { name: 'Paper', description: '自然纸感', background: '#f7f6f1', surface: '#fdfcf8', text: '#303b35', muted: '#667168', border: '#dfdfd5', accent: '#647b64', response: .65 },
  ink: { name: 'Ink', description: '深色墨韵', background: '#303639', surface: '#3b4244', text: '#f4f2e9', muted: '#b9c3c0', border: '#535c5d', accent: '#afc4b3', response: .9 },
  ceramic: { name: 'Ceramic', description: '柔润陶瓷', background: '#e6ece8', surface: '#f0f4f0', text: '#35463f', muted: '#64746b', border: '#ccd8cf', accent: '#647f73', response: .85 },
  industrial: { name: 'Industrial', description: '冷静金属', background: '#e0e4e5', surface: '#edf0ef', text: '#343f45', muted: '#606c73', border: '#c5cdcf', accent: '#647e8c', response: 1 },
  playful: { name: 'Playful', description: '轻快色彩', background: '#eee2df', surface: '#f8eeea', text: '#51403f', muted: '#7c6362', border: '#ddc9c5', accent: '#a66e69', response: .8 },
};
export const MATERIALS: { id: MaterialType; name: string; description: string }[] = [
  { id: 'none', name: 'None', description: '原生卡纸' }, { id: 'matte', name: 'Matte', description: '柔和哑光' },
  { id: 'foil', name: 'Foil', description: '细腻银箔' }, { id: 'holo', name: 'Holo', description: '切面全息' },
  { id: 'starlight', name: 'Starlight', description: '微光晶点' }, { id: 'iridescent', name: 'Iridescent', description: '珠光变色' },
];
export const MATERIAL_FINISH: Record<MaterialType, CardFinish> = { none: 'normal', matte: 'normal', foil: 'foil', holo: 'rainbow', starlight: 'starlight', iridescent: 'laser' };
export const SEMANTIC_SLOTS: { kind: FaceElement['kind']; label: string; hint: string }[] = [
  { kind: 'icon', label: '图标', hint: '一个容易识别的符号' }, { kind: 'title', label: '标题', hint: '卡牌的名称' },
  { kind: 'subtitle', label: '副标题', hint: '用一句话补充标题' }, { kind: 'description', label: '说明', hint: '简洁介绍卡牌的用途' },
  { kind: 'metadata', label: '元信息', hint: '作者、版本或时间' }, { kind: 'status', label: '状态', hint: '一个有意义的状态' },
  { kind: 'tags', label: '标签', hint: '使用逗号分隔' }, { kind: 'action', label: '操作', hint: '卡牌的运行入口' },
  { kind: 'illustration', label: '插图', hint: '上传一张 PNG 图片' }, { kind: 'badge', label: '徽标', hint: '一个简短的重点' },
];
const bound = (n: number, min: number, max: number) => Math.max(min, Math.min(max, n));
const round = (n: number) => Math.round(n * 100) / 100;

export function recipeSettings(face: FaceDesign, surface?: SurfaceDesign): SurfaceRecipe {
  if (surface?.design) return surface.design;
  const kit: StyleKit = face.tone === 'midnight' ? 'ink' : face.tone === 'rose' ? 'playful' : face.tone === 'sage' ? 'ceramic' : face.tone === 'sky' ? 'industrial' : face.tone === 'stone' ? 'paper' : 'sand';
  return { recipe: face.variant === 'compact' ? 'compact' : face.variant === 'text' ? 'editorial' : face.variant === 'image' ? 'poster' : 'hero',
    kit, appearance: kit === 'ink' ? 'dark' : 'light', softness: .6, density: 'medium', emphasis: 'balanced', alignment: 'left', tokens: {},
    material: { type: ({ normal: 'none', foil: 'foil', rainbow: 'holo', starlight: 'starlight', laser: 'iridescent' } as const)[face.finish], intensity: .38, mask: 'visual', roughness: .32 } };
}

function mix(a: string, b: string, t: number) {
  return '#' + [1, 3, 5].map(i => Math.round(parseInt(a.slice(i, i + 2), 16) * (1 - t) + parseInt(b.slice(i, i + 2), 16) * t).toString(16).padStart(2, '0')).join('');
}
export function contrast(a: string, b: string) {
  const lum = (c: string) => [1, 3, 5].map(i => parseInt(c.slice(i, i + 2), 16) / 255)
    .map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4).reduce((n, v, i) => n + v * [.2126, .7152, .0722][i], 0);
  const x = lum(a), y = lum(b); return (Math.max(x, y) + .05) / (Math.min(x, y) + .05);
}
export function readableInk(background: string, preferred: string) {
  if (contrast(background, preferred) >= 4.5) return preferred;
  return contrast(background, '#242b28') > contrast(background, '#ffffff') ? '#242b28' : '#ffffff';
}

export function designTokens(surface: SurfaceDesign, settings = surface.design!): DesignTokens {
  const kit = STYLE_KITS[settings.kit], small = Math.min(surface.width, surface.height) <= 140;
  const unit = bound(Math.min(surface.width / 240, surface.height / 320), .5, 1.9);
  const dark = settings.appearance === 'dark';
  const background = dark ? settings.kit === 'ink' ? kit.background : mix(kit.background, '#1f2828', .92) : settings.kit === 'ink' ? '#e8ecea' : kit.background;
  const density = { low: 1.32, medium: 1, high: .72 }[settings.density];
  const muted = dark ? '#b9c3bc' : settings.kit === 'ink' ? '#616e66' : kit.muted;
  return { background, surface: dark ? mix(background, '#ffffff', .07) : settings.kit === 'ink' ? '#f5f7f4' : kit.surface,
    text: dark ? '#f1f3ec' : settings.kit === 'ink' ? '#303b35' : kit.text,
    muted: contrast(background, muted) >= 4.5 ? muted : readableInk(background, mix(muted, dark ? '#ffffff' : '#202a25', .12)),
    border: dark ? mix(background, '#ffffff', .17) : kit.border,
    radius: round((8 + settings.softness * 20) * unit), margin: round((small ? 28 : 23) * unit * (settings.density === 'high' ? .88 : 1)),
    gap: round(10 * unit * density), title_size: round((small ? 25 : 26) * unit * (settings.emphasis === 'title' ? 1.15 : 1)),
    body_size: round(Math.max(small ? 9 : 12, 13 * unit)), ...settings.tokens };
}

export function slotText(element: FaceElement, face: FaceDesign) {
  if (element.kind === 'title') return face.title;
  if (element.kind === 'description') return face.description;
  if (element.kind === 'help') return face.help_text;
  if (element.kind === 'action') return face.button_label;
  return element.text;
}
const textLength = (text: string) => Array.from(text).reduce((n, c) => n + (c.charCodeAt(0) > 255 ? 1 : .55), 0);
export function newSlot(kind: FaceElement['kind']): FaceElement {
  return { id: kind, kind, x: 0, y: 0, width: 80, height: 24, font_size: 13, color: '#35463f', align: 'left', placement: 'slot', sizing: 'fill', pin: 'start',
    text: ({ subtitle: '每一天，都有新灵感', metadata: 'OAW · 01', status: '就绪', tags: '灵感, 日常', badge: '精选' } as Partial<Record<FaceElement['kind'], string>>)[kind] ?? '' };
}

/** Compile semantic intent into v1 geometry. Existing free layers and inline assets survive. */
export function reflowSurface(surface: SurfaceDesign, face: FaceDesign, settings = recipeSettings(face, surface)): SurfaceDesign {
  const tokens = designTokens(surface, settings), { width: w, height: h } = surface;
  const margin = Math.min(tokens.margin, w * .22, h * .2), innerW = w - margin * 2, innerH = h - margin * 2;
  const small = Math.min(w, h) <= 140, wide = w / h > 1.25;
  const recipe = settings.recipe, center = recipe === 'badge' || small, align = small ? 'center' : settings.alignment;
  const fieldLayout = settings.field_layout ?? (recipe === 'split' || recipe === 'utility' || wide ? 'columns' : 'stack');
  // A workspace has an identity column and a working column, with shared type tokens.
  const introKinds = ['icon', 'illustration', 'title', 'subtitle', 'description', 'metadata', 'status', 'tags', 'badge'];
  const intro = surface.elements.filter(e => e.placement !== 'free' && introKinds.includes(e.kind));
  const work = surface.elements.filter(e => e.placement !== 'free' && !introKinds.includes(e.kind));
  if (wide && !small && intro.length && work.some(e => ['fields', 'action', 'result'].includes(e.kind))) {
    const gutter = Math.max(tokens.gap * 2, innerW * .05), introW = (innerW - gutter) * .36;
    const layoutColumn = (elements: FaceElement[], width: number, x: number, identity: boolean) => {
      const column = reflowSurface({ ...surface, width, height: innerH, elements }, face, { ...settings,
        recipe: identity ? recipe : 'utility', field_layout: fieldLayout,
        tokens: { ...tokens, margin: 0, title_size: tokens.title_size * (identity ? 1.08 : 1) } });
      return column.elements.map(e => ({ ...e, x: round(e.x + x), y: round(e.y + margin) }));
    };
    const positions = new Map([...layoutColumn(intro, introW, margin, true), ...layoutColumn(work, innerW - introW - gutter, margin + introW + gutter, false)].map(e => [e.id, e]));
    return { ...surface, design: settings, field_layout: fieldLayout,
      shapes: surface.shapes.map((shape, i) => i === 0 ? { ...shape, fill: tokens.background, radius: tokens.radius } : shape),
      elements: surface.elements.map(e => positions.get(e.id) ?? e) };
  }
  const gap = Math.min(tokens.gap, innerH / Math.max(3, surface.elements.length * 2));
  const visual = surface.elements.filter(e => e.placement !== 'free' && (e.kind === 'icon' || e.kind === 'illustration'));
  const compact = ['compact', 'utility'].includes(recipe);
  const hasTitle = surface.elements.some(e => e.kind === 'title' && e.placement !== 'free');
  const side = (recipe === 'split' || wide || compact && (visual.length > 1 || !hasTitle)) && !small && visual.length > 0;
  const inline = compact && !small && !side && visual.length === 1;
  const inlineSize = Math.min(tokens.body_size * 2.8, innerH * .2);
  const visualW = side ? innerW * .32 : innerW;
  const bodyX = side ? margin + visualW + gap * 1.6 : margin, bodyW = side ? innerW - visualW - gap * 1.6 : innerW;
  const visualSize = Math.min(visualW, innerH * (recipe === 'poster' ? .42 : recipe === 'minimal' ? .12 : .24)) * (settings.emphasis === 'visual' ? 1.2 : 1);
  const order = recipe === 'editorial' ? ['badge', 'metadata', 'title', 'subtitle', 'description', 'illustration', 'icon', 'status', 'tags', 'help', 'fields', 'action', 'result', 'text']
    : ['badge', 'illustration', 'icon', 'title', 'subtitle', 'description', 'metadata', 'status', 'tags', 'help', 'fields', 'action', 'result', 'text'];
  const managed = surface.elements.filter(e => e.placement !== 'free').sort((a, b) => order.indexOf(a.kind) - order.indexOf(b.kind));
  const flow = managed.filter(e => !((side || inline) && visual.includes(e)));
  const metrics = flow.map(element => {
    const isVisual = visual.includes(element), isTitle = element.kind === 'title';
    let font = isTitle ? tokens.title_size : element.kind === 'subtitle' ? tokens.body_size * 1.13 : tokens.body_size;
    font = element.overrides?.font_size ?? font;
    let width = bodyW;
    if (inline && isTitle) width = Math.max(32, width - inlineSize - gap);
    if (isVisual) width = element.kind === 'illustration' ? bodyW : Math.min(visualSize, bodyW);
    if (element.sizing === 'fixed') width = Math.min(width, element.width);
    if (element.sizing === 'hug' && !isVisual) width = Math.min(width, Math.max(24, textLength(slotText(element, face)) * font + 14));
    const lines = Math.max(1, Math.min(isTitle ? 2 : element.kind === 'description' ? 4 : 2, Math.ceil(textLength(slotText(element, face)) * font / Math.max(1, width))));
    let height = font * 1.4 * lines;
    if (inline && isTitle) height = Math.max(inlineSize, height);
    if (isVisual) height = element.kind === 'illustration' ? Math.min(bodyW * .6, innerH * .35) : Math.min(visualSize, bodyW);
    if (element.kind === 'action') height = Math.max(24, tokens.body_size * 2.5);
    if (element.kind === 'fields') height = (fieldLayout === 'columns' ? 1 : 2) * tokens.body_size * 3.5 + (fieldLayout === 'columns' ? 0 : gap);
    if (element.kind === 'result') height = Math.max(tokens.body_size * 4, innerH * .2);
    if (['status', 'tags', 'badge'].includes(element.kind)) height = Math.max(18, font * 2);
    return { element, width, height, font };
  });
  let total = metrics.reduce((n, m) => n + m.height, 0) + Math.max(0, flow.length - 1) * gap;
  const result = metrics.find(m => m.element.kind === 'result');
  // Functional workspaces use the remaining height for readable output.
  if (result && work.length === surface.elements.length && innerH > total) { result.height += innerH - total; total = innerH; }
  const fit = Math.min(1, innerH / Math.max(1, total));
  let y = margin + (center ? Math.max(0, innerH - total) / 2 : recipe === 'minimal' ? Math.max(0, innerH - total) * .27 : 0);
  const positions = new Map<string, FaceElement>();
  for (const { element, width, height, font } of metrics) {
    const isVisual = visual.includes(element), eAlign = element.overrides?.align ?? align;
    const pin = element.pin === 'center' ? 'center' : element.pin === 'end' ? 'right' : eAlign;
    let x = bodyX + (pin === 'center' ? (bodyW - width) / 2 : pin === 'right' ? bodyW - width : 0);
    if (inline && element.kind === 'title') x = bodyX + bodyW - width;
    const color = element.overrides?.color ?? (['description', 'metadata', 'help', 'subtitle'].includes(element.kind) ? tokens.muted : tokens.text);
    positions.set(element.id, { ...element, placement: 'slot', x: round(x), y: round(y), width: round(Math.max(8, width)), height: round(Math.max(8, height * fit)),
      font_size: round(bound(isVisual ? Math.min(width, height * fit) * .55 : font * fit, 8, 128)), color, align: eAlign });
    y += (height + gap) * fit;
  }
  if (side || inline) {
    const title = flow.find(e => e.kind === 'title');
    let visualY = inline && title ? positions.get(title.id)!.y : margin;
    for (const element of visual) {
      const size = Math.min(inline ? inlineSize * fit : visualW, inline ? inlineSize * fit : innerH / Math.max(1, visual.length) - gap);
      const height = inline ? size : element.kind === 'illustration' ? Math.min(innerH / visual.length - gap, size * 1.5) : size;
      positions.set(element.id, { ...element, placement: 'slot', x: margin, y: round(visualY), width: round(size), height: round(height), font_size: round(bound(size * .55, 8, 128)), color: tokens.text, align: 'center' });
      visualY += height + gap;
    }
  }
  return { ...surface, design: settings, field_layout: fieldLayout,
    shapes: surface.shapes.map((shape, i) => i === 0 ? { ...shape, fill: tokens.background, radius: tokens.radius } : shape),
    elements: surface.elements.map(element => positions.get(element.id) ?? element) };
}

export function chooseRecipe(surface: SurfaceDesign, face: FaceDesign, recipe: LayoutRecipe): SurfaceDesign {
  const settings = { ...recipeSettings(face, surface), recipe, alignment: (recipe === 'badge' ? 'center' : 'left') as SurfaceRecipe['alignment'] };
  const elements = [...surface.elements];
  if (recipe === 'utility' && Math.min(surface.width, surface.height) > 140) for (const kind of ['fields', 'action'] as const) {
    if (!elements.some(e => e.kind === kind) && elements.length < 32) elements.push(newSlot(kind));
  }
  return reflowSurface({ ...surface, elements }, face, settings);
}

/** Stable, local corrections; a second pass is a no-op and callers store one undo snapshot. */
export function polishSurface(surface: SurfaceDesign, face: FaceDesign) {
  let settings = recipeSettings(face, surface);
  const tokens = designTokens(surface, settings);
  const bodySize = Math.min(32, tokens.body_size);
  settings = { ...settings, material: { ...settings.material, intensity: Math.min(.55, settings.material.intensity) }, tokens: { ...settings.tokens,
    text: readableInk(tokens.background, tokens.text), muted: readableInk(tokens.background, tokens.muted),
    margin: bound(tokens.margin, Math.min(surface.width, surface.height) * .06, Math.min(surface.width, surface.height) * .18),
    gap: bound(tokens.gap, 4, 24), body_size: bodySize, title_size: bound(Math.max(tokens.title_size, bodySize * 1.4), 8, 64) } };
  let next = reflowSurface(surface, face, settings);
  const resolved = designTokens(next), margin = resolved.margin;
  next = { ...next, elements: next.elements.map(element => {
    const color = readableInk(resolved.background, element.color);
    if (element.placement !== 'free') return { ...element, color, overrides: element.overrides?.color ? { ...element.overrides, color } : element.overrides };
    const width = Math.min(element.width, surface.width - margin * 2), height = Math.min(element.height, surface.height - margin * 2);
    return { ...element, color, width, height, x: bound(element.x, margin, surface.width - margin - width), y: bound(element.y, margin, surface.height - margin - height),
      font_size: bound(element.font_size, 8, element.kind === 'title' ? resolved.title_size : resolved.body_size * 1.15) };
  }) };
  const details: string[] = [];
  if (surface.elements.some((e, i) => ['x', 'y', 'width', 'height'].some(k => e[k as 'x'] !== next.elements[i][k as 'x']))) details.push('间距与边距');
  if (surface.elements.some((e, i) => e.font_size !== next.elements[i].font_size)) details.push('文字层级');
  if (surface.elements.some((e, i) => e.color !== next.elements[i].color)) details.push('文字对比度');
  if (settings.material.intensity !== surface.design?.material.intensity) details.push('材质强度');
  if (surface.shapes.some((s, i) => s.fill !== next.shapes[i].fill || s.radius !== next.shapes[i].radius)) details.push('卡面风格');
  return { surface: next, details };
}
