import { describe, expect, it } from 'vitest';
import { FACE_MODES, faceStudio, presetSurface } from './faceDesign';
import { RECIPES, SEMANTIC_SLOTS, STYLE_KITS, chooseRecipe, contrast, designTokens, freeSurface, newSlot, polishSurface, recipeSettings, reflowSurface, restyleSurface } from './designRecipes';
import type { FaceDesign, StyleKit } from './types';

const face: FaceDesign = { title: '每日灵感', description: '收集想法，让灵感成为下一次行动。', variant: 'icon', tone: 'sand', color: '#6c8069', icon: 'sparkles', finish: 'normal', layout: 'stack', help_text: '', button_label: '开始' };
describe('semantic card design', () => {
  it('produces distinct, bounded layouts across every supported card surface', () => {
    const layouts = new Set<string>();
    for (const mode of FACE_MODES) for (const recipe of RECIPES) {
      const result = chooseRecipe(presetSurface(mode, face), face, recipe.id);
      if (mode === 'preview') layouts.add(JSON.stringify(result.elements));
      for (const e of result.elements) {
        expect(e.x).toBeGreaterThanOrEqual(0); expect(e.y).toBeGreaterThanOrEqual(0);
        expect(e.x + e.width).toBeLessThanOrEqual(result.width + .02);
        expect(e.y + e.height).toBeLessThanOrEqual(result.height + .02);
        expect(e.font_size).toBeGreaterThanOrEqual(8);
      }
    }
    expect(layouts.size).toBe(8);
  });
  it('reflows additions/removals and preserves free geometry, copy and images', () => {
    const surface = faceStudio(face).modes.preview!;
    const subtitle = { ...newSlot('subtitle'), text: '专注每一个好想法' };
    const free = { ...newSlot('text'), id: 'free', placement: 'free' as const, x: 120, y: 251, width: 93, height: 20, text: 'Edition 01' };
    const added = reflowSurface({ ...surface, elements: [...surface.elements, subtitle, free] }, face);
    expect(added.elements.find(e => e.kind === 'description')!.y).toBeGreaterThan(surface.elements.find(e => e.kind === 'description')!.y);
    const changed = chooseRecipe({ ...added, background_png: 'retained-png' }, face, 'split');
    expect(changed.elements.find(e => e.id === 'free')).toEqual(free);
    expect(changed.elements.find(e => e.kind === 'subtitle')!.text).toBe(subtitle.text);
    expect(changed.background_png).toBe('retained-png');
    const removed = reflowSurface({ ...added, elements: added.elements.filter(e => e.kind !== 'subtitle' && e.id !== 'free') }, face);
    expect(removed.elements).toEqual(surface.elements);
  });
  it('detaches individual and complete preset layouts without moving or deforming any layer', () => {
    for (const mode of FACE_MODES) for (const recipe of RECIPES) {
      const surface = chooseRecipe(presetSurface(mode, face), face, recipe.id);
      surface.elements[0].print = { opacity: .6, blend: 'multiply' };
      const first = surface.elements[0];
      const detached = freeSurface(surface, first.id);
      expect(detached.elements[0]).toEqual({ ...first, placement: 'free' });
      expect(detached.elements.slice(1)).toEqual(surface.elements.slice(1));
      expect(detached.shapes).toBe(surface.shapes);
      expect(detached.design).toBe(surface.design);
      const all = freeSurface(detached);
      expect(all.elements).toEqual(surface.elements.map(element => ({ ...element, placement: 'free' })));
      expect(freeSurface(all)).toBe(all);
      expect(first.placement).toBe('slot');
    }
  });
  it('changes paper and ink without reflowing copy, artwork or detached layers', () => {
    const surface = chooseRecipe(presetSurface('preview', face), face, 'compact');
    surface.elements[0] = { ...surface.elements[0], placement: 'free', width: 47, height: 47, image_png: 'artwork', print: { opacity: .8, blend: 'screen' } };
    surface.elements[1] = { ...surface.elements[1], color: '#ff0055', overrides: { color: '#ff0055' } };
    const settings = surface.design!;
    const stock = { ...settings.production!, stock: { ...settings.production!.stock, type: 'ink' as const } };
    const changed = restyleSurface(surface, face, { ...settings, production: stock });
    expect(changed.shapes[0].fill).not.toBe(surface.shapes[0].fill);
    expect(changed.elements[2].color).not.toBe(surface.elements[2].color);
    expect(changed.elements[1].color).toBe('#ff0055');
    expect(changed.elements.map(({ color: _color, ...element }) => element)).toEqual(surface.elements.map(({ color: _color, ...element }) => element));
    changed.shapes[0] = { ...changed.shapes[0], radius: 21, fill: '#bead73' };
    const finished = restyleSurface(changed, face, { ...changed.design!, production: { ...stock, laminate: { ...stock.laminate, type: 'holo', strength: .9 } } });
    expect(finished.elements).toEqual(changed.elements);
    expect(finished.shapes).toEqual(changed.shapes);
  });
  it('preserves saved compositions on repeated reads and after text edits', () => {
    const studio = faceStudio(face), preview = studio.modes.preview!;
    const authored = freeSurface(preview, 'icon');
    authored.elements[0] = { ...authored.elements[0], x: 121, y: 202, width: 49, height: 49 };
    const saved = { ...face, studio: { ...studio, modes: { ...studio.modes, preview: authored } } };
    const bytes = JSON.stringify(saved.studio);
    expect(faceStudio(saved)).toBe(saved.studio);
    expect(faceStudio({ ...saved, title: 'A much longer title that changes the line count', description: 'New copy '.repeat(30) })).toBe(saved.studio);
    expect(faceStudio({ ...saved, studio: faceStudio(saved) })).toBe(saved.studio);
    expect(JSON.stringify(saved.studio)).toBe(bytes);
    expect(faceStudio(saved).modes.preview!.elements.slice(1)).toEqual(preview.elements.slice(1));
  });
  it('applies explicit typography tokens without moving boxes or overriding custom type', () => {
    for (const mode of ['preview', 'workspace'] as const) {
      const surface = faceStudio(face).modes[mode]!, previous = designTokens(surface);
      surface.elements = surface.elements.map(element => element.kind === 'description' ? { ...element, font_size: 19, overrides: { font_size: 19 } } : element);
      surface.elements.push({ ...newSlot('text'), id: 'custom', placement: 'free', font_size: 37 });
      surface.elements.push({ ...newSlot('text'), id: 'following', placement: 'free', font_size: previous.body_size });
      const settings = { ...surface.design!, tokens: { ...surface.design!.tokens, title_size: previous.title_size * 1.2, body_size: previous.body_size + 2 } };
      const changed = restyleSurface(surface, face, settings);
      const font = (value: typeof surface, id: string) => value.elements.find(element => element.id === id)!.font_size;
      expect(font(changed, 'title')).toBe(Math.round(font(surface, 'title') * 1.2 * 100) / 100);
      expect(font(changed, 'description')).toBe(19);
      expect(font(changed, 'custom')).toBe(37);
      expect(font(changed, 'following')).toBe(previous.body_size + 2);
      expect(font(changed, 'icon')).toBe(font(surface, 'icon'));
      expect(changed.elements.map(({ font_size: _font, ...element }) => element)).toEqual(surface.elements.map(({ font_size: _font, ...element }) => element));
      expect(restyleSurface(changed, face, settings).elements).toEqual(changed.elements);
    }
  });
  it('keeps dense, long semantic content inside the safe area', () => {
    const surface = presetSurface('preview', face), longFace = { ...face, title: '一个很长的卡牌标题'.repeat(8), description: '这是一段较长的说明。'.repeat(40) };
    for (const recipe of RECIPES) {
      const next = chooseRecipe({ ...surface, elements: SEMANTIC_SLOTS.map(s => newSlot(s.kind)) }, longFace, recipe.id);
      for (const e of next.elements) { expect(e.y).toBeGreaterThanOrEqual(0); expect(e.y + e.height).toBeLessThanOrEqual(surface.height); }
    }
  });
  it('separates illustrations, icons and copy even in compact and wide layouts', () => {
    for (const mode of ['preview', 'workspace'] as const) for (const recipe of RECIPES) {
      const surface = presetSurface(mode, face);
      const next = chooseRecipe({ ...surface, elements: SEMANTIC_SLOTS.map(s => newSlot(s.kind)) }, face, recipe.id);
      for (const a of next.elements) {
        expect(a.y + a.height).toBeLessThanOrEqual(surface.height);
        for (const b of next.elements.filter(e => e.id !== a.id)) {
          const overlapX = Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x);
          const overlapY = Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y);
          expect(overlapX > .05 && overlapY > .05, `${mode}/${recipe.id}: ${a.id} overlaps ${b.id}`).toBe(false);
        }
      }
    }
  });
  it('provides legible tokens in all style kits and both appearances', () => {
    const surface = presetSurface('preview', face);
    for (const kit of Object.keys(STYLE_KITS) as StyleKit[]) for (const appearance of ['light', 'dark'] as const) {
      const tokens = designTokens(surface, { ...recipeSettings(face), kit, appearance });
      expect(contrast(tokens.background, tokens.text)).toBeGreaterThanOrEqual(4.5);
      expect(contrast(tokens.background, tokens.muted)).toBeGreaterThanOrEqual(4.5);
    }
  });
  it('polishes deterministically, repairs contrast/edges and is idempotent', () => {
    const surface = faceStudio(face).modes.preview!;
    const broken = { ...surface, design: { ...surface.design!, tokens: { text: '#eee5d6', muted: '#eee5d6' }, material: { ...surface.design!.material, intensity: .95 } },
      elements: [...surface.elements, { ...newSlot('text'), placement: 'free' as const, x: -20, y: 900, width: 600, height: 28, font_size: 75, color: '#eee5d6' }] };
    const polished = polishSurface(broken, face);
    expect(polished.details.length).toBeGreaterThanOrEqual(3);
    expect(polished.surface.design!.material.intensity).toBe(.55);
    expect(polishSurface(polished.surface, face).surface).toEqual(polished.surface);
    for (const e of polished.surface.elements) {
      expect(contrast(designTokens(polished.surface).background, e.color)).toBeGreaterThanOrEqual(4.5);
      expect(e.x).toBeGreaterThanOrEqual(0); expect(e.y + e.height).toBeLessThanOrEqual(surface.height);
    }
  });
  it('leaves hand-positioned v1 designs untouched until an explicit design action', () => {
    const surface = presetSurface('preview', face);
    surface.elements[0].x += 7;
    const legacy = { ...face, studio: { version: 1 as const, enabled: ['preview' as const], initial: 'preview' as const, open: 'preview' as const, modes: { preview: surface } } };
    expect(faceStudio(legacy)).toBe(legacy.studio);
    expect(faceStudio(legacy).modes.preview?.design).toBeUndefined();
  });
  it('upgrades untouched legacy views to the style already chosen for the card', () => {
    const preview = chooseRecipe(presetSurface('preview', face), face, 'compact');
    preview.design!.kit = 'ink'; preview.design!.appearance = 'dark';
    const legacy = { ...face, studio: { version: 1 as const, enabled: FACE_MODES, initial: 'preview' as const, open: 'workspace' as const,
      modes: { preview, node: presetSurface('node', face), workspace: presetSurface('workspace', face) } } };
    const next = faceStudio(legacy);
    expect(next.modes.node!.design).toMatchObject({ recipe: 'compact', kit: 'ink' });
    expect(next.modes.workspace!.design).toMatchObject({ recipe: 'compact', kit: 'ink' });
    expect(legacy.studio.modes.workspace.design).toBeUndefined();
    const title = next.modes.workspace!.elements.find(e => e.kind === 'title')!;
    expect(title.font_size).toBeGreaterThan(36);
    expect(title.x).toBeLessThan(next.modes.workspace!.elements.find(e => e.kind === 'fields')!.x);
  });
  it('keeps polish results saveable after extreme advanced typography overrides', () => {
    const surface = faceStudio(face).modes.preview!;
    surface.design!.tokens = { title_size: 128, body_size: 128 };
    const result = polishSurface(surface, face).surface;
    const tokens = designTokens(result);
    expect(tokens.title_size).toBeLessThanOrEqual(128);
    expect(tokens.title_size).toBeGreaterThanOrEqual(tokens.body_size * 1.4);
    expect(polishSurface(result, face).surface).toEqual(result);
  });
});
