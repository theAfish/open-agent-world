// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';

afterEach(() => { vi.clearAllTimers(); vi.useRealTimers(); vi.restoreAllMocks(); localStorage.clear(); });

it.each(['broken json', 'null', '{"textSize":"large"}', '{"textSize":1e999}'])(
  'uses a readable default for invalid saved appearance: %s', async saved => {
    vi.resetModules();
    localStorage.setItem('oaw-appearance', saved);
    const { useAppearance } = await import('./appearance');
    expect(useAppearance.getState().textSize).toBe(100);
  },
);

it('hydrates the active profile and persists bounded changes without reading another profile’s browser settings', async () => {
  vi.resetModules(); vi.useFakeTimers();
  localStorage.setItem('oaw-appearance', '{"textSize":80}');
  const { configureProfile, currentProfile, discardProfile } = await import('./profileStorage');
  configureProfile({ mode: 'development', profile_id: 'dev', generation: 'g1', version: '1',
    values: { 'oaw-appearance': '{"textSize":150,"wheelShortcut":false}' } });
  const { useAppearance } = await import('./appearance');
  expect(useAppearance.getState()).toMatchObject({ textSize: 150, wheelShortcut: false });
  useAppearance.getState().setTextSize(999);
  expect(useAppearance.getState().textSize).toBe(200);
  useAppearance.getState().setTextSize(-1);
  expect(useAppearance.getState().textSize).toBe(80);
  useAppearance.getState().setTextSize(134);
  useAppearance.getState().setWheelShortcut(true);
  expect(JSON.parse(currentProfile()!.values['oaw-appearance'])).toEqual({
    textSize: 130, wheelShortcut: true, paletteId: 'oaw/paper', backgroundId: 'oaw/terrain', skinMods: [],
  });
  expect(localStorage.getItem('oaw-appearance')).toBe('{"textSize":80}');
  discardProfile();
});

it('keeps appearance usable when browser storage is unavailable', async () => {
  vi.resetModules();
  const { profileStorage } = await import('./profileStorage');
  vi.spyOn(profileStorage, 'getItem').mockImplementation(() => { throw new Error('disabled'); });
  vi.spyOn(profileStorage, 'setItem').mockImplementation(() => { throw new Error('disabled'); });
  const { useAppearance } = await import('./appearance');
  useAppearance.getState().setTextSize(120);
  expect(useAppearance.getState().textSize).toBe(120);
});

const blueprint = {
  schemaVersion: 1, id: 'test.blueprint', name: 'Blueprint',
  palettes: [{ id: 'blue', name: 'Blueprint blue', light: { '--accent': '#146' }, dark: { '--accent': '#acf' } }],
  backgrounds: [{ id: 'squares', name: 'Small squares', kind: 'grid', spacing: 16 }],
};

it('persists imported mods and selection with text preferences, then hydrates them after reload', async () => {
  vi.resetModules();
  let { useAppearance } = await import('./appearance');
  useAppearance.getState().setTextSize(140);
  useAppearance.getState().importSkinMod(blueprint);
  const saved = JSON.parse(localStorage.getItem('oaw-appearance')!);
  expect(saved).toMatchObject({ textSize: 140, paletteId: 'test.blueprint/blue', backgroundId: 'test.blueprint/squares', skinMods: [blueprint] });
  vi.resetModules();
  ({ useAppearance } = await import('./appearance'));
  expect(useAppearance.getState()).toMatchObject(saved);
  useAppearance.getState().removeSkinMod('test.blueprint');
  expect(useAppearance.getState()).toMatchObject({ paletteId: 'test.blueprint/blue', backgroundId: 'test.blueprint/squares', skinMods: [] });
  expect(JSON.parse(localStorage.getItem('oaw-appearance')!).skinMods).toEqual([]);
});

it('rejects invalid or duplicate imports atomically and keeps a palette-only import from replacing the background', async () => {
  vi.resetModules();
  const { useAppearance } = await import('./appearance');
  useAppearance.getState().setBackgroundId('oaw/dots');
  useAppearance.getState().importSkinMod({ ...blueprint, backgrounds: [] });
  expect(useAppearance.getState().backgroundId).toBe('oaw/dots');
  const before = localStorage.getItem('oaw-appearance');
  expect(() => useAppearance.getState().importSkinMod(blueprint)).toThrow('already installed');
  expect(() => useAppearance.getState().importSkinMod({ ...blueprint, id: 'another', palettes: [{ ...blueprint.palettes[0], light: { '--accent': 'url(https://example.com)' } }] })).toThrow('hexadecimal');
  expect(localStorage.getItem('oaw-appearance')).toBe(before);
});

it('recovers valid persisted mods and unknown selections without discarding text settings', async () => {
  vi.resetModules();
  localStorage.setItem('oaw-appearance', JSON.stringify({ textSize: 160, wheelShortcut: false,
    paletteId: 'missing.palette/purple', backgroundId: 'missing.background/dots',
    skinMods: [{ ...blueprint, id: 'oaw' }, blueprint, blueprint, { broken: true }],
  }));
  const { useAppearance } = await import('./appearance');
  expect(useAppearance.getState()).toMatchObject({ textSize: 160, wheelShortcut: false,
    paletteId: 'missing.palette/purple', backgroundId: 'missing.background/dots', skinMods: [blueprint],
  });
});

it('bounds imported mod count so the appearance value stays within the profile storage limit', async () => {
  vi.resetModules();
  const { useAppearance } = await import('./appearance');
  for (let i = 0; i < 12; i++) useAppearance.getState().importSkinMod({ ...blueprint, id: `test.mod-${i}` });
  expect(() => useAppearance.getState().importSkinMod({ ...blueprint, id: 'test.overflow' })).toThrow('At most 12');
  expect(useAppearance.getState().skinMods).toHaveLength(12);
});
