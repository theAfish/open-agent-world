// @vitest-environment jsdom
import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { useAppearance, useRegisteredSkinMods } from '../state/appearance';
import { registerSkinMod, resolveBackground, resolvePalette, useSkinCatalog } from './registry';
import type { SkinMod } from './skinMod';

const initial = useAppearance.getState();
const mod: SkinMod = { schemaVersion: 1, id: 'acme.blueprint', name: 'Blueprint',
  palettes: [{ id: 'drafting', name: 'Drafting', light: { '--accent': '#146' }, dark: {} }],
  backgrounds: [{ id: 'squared', name: 'Squared', kind: 'grid', spacing: 16 }],
};
afterEach(() => { cleanup(); useAppearance.setState(initial); useRegisteredSkinMods.setState({ mods: [] }); localStorage.clear(); });

it('reacts to registrations and cleanup, restores fallback without losing desired IDs, and does not persist session mods', () => {
  const { result } = renderHook(useSkinCatalog);
  act(() => { useAppearance.getState().setPaletteId('acme.blueprint/drafting'); useAppearance.getState().setBackgroundId('acme.blueprint/squared'); });
  expect(resolvePalette(useAppearance.getState().paletteId, result.current.palettes).id).toBe('oaw/paper');
  let dispose: () => void;
  act(() => { dispose = registerSkinMod(mod); });
  expect(resolvePalette(useAppearance.getState().paletteId, result.current.palettes).light['--accent']).toBe('#146');
  expect(resolveBackground(useAppearance.getState().backgroundId, result.current.backgrounds).spacing).toBe(16);
  act(() => useAppearance.getState().setTextSize(110));
  expect(JSON.parse(localStorage.getItem('oaw-appearance')!).skinMods).toEqual([]);
  act(() => { dispose(); dispose(); });
  expect(resolvePalette(useAppearance.getState().paletteId, result.current.palettes).id).toBe('oaw/paper');
  expect(resolveBackground(useAppearance.getState().backgroundId, result.current.backgrounds).id).toBe('oaw/terrain');
  expect(useAppearance.getState().paletteId).toBe('acme.blueprint/drafting');
});

it('rejects runtime/import collisions in both directions and an old cleanup cannot unregister a newer owner', () => {
  const first = registerSkinMod(mod);
  expect(() => registerSkinMod(mod)).toThrow('already installed or registered');
  expect(() => useAppearance.getState().importSkinMod(mod)).toThrow('already installed or registered');
  first();
  const second = registerSkinMod(mod);
  first();
  expect(useRegisteredSkinMods.getState().mods).toHaveLength(1);
  second();
  useAppearance.getState().importSkinMod(mod);
  expect(() => registerSkinMod(mod)).toThrow('already installed or registered');
});
