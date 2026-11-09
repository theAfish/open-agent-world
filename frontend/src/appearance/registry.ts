import { useMemo } from 'react';
import { useAppearance, useRegisteredSkinMods } from '../state/appearance';
import { BUILTIN_SKIN_MOD, DEFAULT_BACKGROUND_ID, DEFAULT_PALETTE_ID } from './builtins';
import type { BackgroundEntry, PaletteEntry } from './skinMod';
export { registerSkinMod } from '../state/appearance';
export { DEFAULT_BACKGROUND_ID, DEFAULT_PALETTE_ID } from './builtins';

export function useSkinCatalog(): { palettes: PaletteEntry[]; backgrounds: BackgroundEntry[] } {
  const imported = useAppearance(state => state.skinMods);
  const registered = useRegisteredSkinMods(state => state.mods);
  return useMemo(() => {
    const mods = [BUILTIN_SKIN_MOD, ...imported, ...registered];
    return {
      palettes: mods.flatMap(mod => mod.palettes.map(entry => ({ ...entry, id: `${mod.id}/${entry.id}`, modId: mod.id }))),
      backgrounds: mods.flatMap(mod => mod.backgrounds.map(entry => ({ ...entry, id: `${mod.id}/${entry.id}`, modId: mod.id }))),
    };
  }, [imported, registered]);
}

export function resolvePalette(id: string, palettes: PaletteEntry[]): PaletteEntry {
  return palettes.find(entry => entry.id === id) ?? palettes.find(entry => entry.id === DEFAULT_PALETTE_ID)!;
}
export function resolveBackground(id: string, backgrounds: BackgroundEntry[]): BackgroundEntry {
  return backgrounds.find(entry => entry.id === id) ?? backgrounds.find(entry => entry.id === DEFAULT_BACKGROUND_ID)!;
}
