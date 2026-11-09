import { expect, it } from 'vitest';
import { MAX_SKIN_MOD_BYTES, parseSkinMod } from './skinMod';

const mod = {
  schemaVersion: 1, id: 'acme.blueprint', name: 'Blueprint',
  palettes: [{ id: 'drafting', name: 'Drafting', light: { '--accent': '#acf', '--line': '#1234' }, dark: { '--accent': '#aabbcc', '--line': '#11223344' } }],
  backgrounds: [{ id: 'squared', name: 'Squared', kind: 'grid', spacing: 32 }],
};

it('accepts data-only palette and background definitions and returns an independent copy', () => {
  const parsed = parseSkinMod(mod);
  expect(parsed).toEqual(mod);
  expect(parsed.palettes[0].light).not.toBe(mod.palettes[0].light);
  expect(parsed.backgrounds).not.toBe(mod.backgrounds);
});

it.each([
  ['CSS value', { ...mod, palettes: [{ ...mod.palettes[0], light: { '--accent': 'url(https://example.com/a)' } }] }],
  ['layout token', { ...mod, palettes: [{ ...mod.palettes[0], light: { '--font-size-body': '#fff' } }] }],
  ['injected property', { ...mod, palettes: [{ ...mod.palettes[0], light: { '--accent; background': '#fff' } }] }],
  ['extra CSS field', { ...mod, css: 'body { display: none }' }],
  ['reserved namespace', { ...mod, id: 'oaw' }],
  ['namespace escape', { ...mod, id: 'acme/paper' }],
  ['future schema', { ...mod, schemaVersion: 2 }],
  ['duplicate entry', { ...mod, palettes: [...mod.palettes, ...mod.palettes] }],
  ['empty mod', { ...mod, palettes: [], backgrounds: [] }],
  ['unknown background', { ...mod, backgrounds: [{ ...mod.backgrounds[0], kind: 'iframe' }] }],
  ['tiny grid', { ...mod, backgrounds: [{ ...mod.backgrounds[0], spacing: 0 }] }],
  ['huge grid', { ...mod, backgrounds: [{ ...mod.backgrounds[0], spacing: 129 }] }],
  ['non-finite spacing', { ...mod, backgrounds: [{ ...mod.backgrounds[0], spacing: Infinity }] }],
  ['irrelevant parameter', { ...mod, backgrounds: [{ ...mod.backgrounds[0], kind: 'terrain' }] }],
  ['out-of-bounds angle', { ...mod, backgrounds: [{ id: 'a', name: 'A', kind: 'gradient', angle: 361 }] }],
  ['too many entries', { ...mod, palettes: Array.from({ length: 17 }, (_, i) => ({ ...mod.palettes[0], id: `p${i}` })) }],
])('rejects %s', (_label, value) => { expect(() => parseSkinMod(value)).toThrow(); });

it('rejects oversized and non-JSON input before registering any entries', () => {
  expect(() => parseSkinMod({ ...mod, name: 'x'.repeat(MAX_SKIN_MOD_BYTES) })).toThrow('64 KiB');
  const circular: Record<string, unknown> = { ...mod };
  circular.self = circular;
  expect(() => parseSkinMod(circular)).toThrow('JSON data');
});
