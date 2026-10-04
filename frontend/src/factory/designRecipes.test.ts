import { describe, expect, it } from 'vitest';
import { FACE_MODES, faceStudio, presetSurface } from './faceDesign';
import { RECIPES, SEMANTIC_SLOTS, STYLE_KITS, chooseRecipe, contrast, designTokens, newSlot, polishSurface, recipeSettings, reflowSurface } from './designRecipes';
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
