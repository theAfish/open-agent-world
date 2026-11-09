import { create } from 'zustand';
import { profileStorage } from './profileStorage';
import { DEFAULT_BACKGROUND_ID, DEFAULT_PALETTE_ID } from '../appearance/builtins';
import { MAX_IMPORTED_SKIN_MODS, parseSkinMod, type SkinMod } from '../appearance/skinMod';

const STORAGE_KEY = 'oaw-appearance';
export const MIN_TEXT_SIZE = 80;
export const MAX_TEXT_SIZE = 200;
export const TEXT_SIZE_STEP = 10;

function textSize(value: unknown) {
  return typeof value === 'number' && Number.isFinite(value)
    ? Math.max(MIN_TEXT_SIZE, Math.min(MAX_TEXT_SIZE, Math.round(value / TEXT_SIZE_STEP) * TEXT_SIZE_STEP)) : 100;
}

function selection(value: unknown, fallback: string): string {
  return typeof value === 'string' && /^[a-z][a-z0-9._-]{0,63}\/[a-z][a-z0-9._-]{0,63}$/.test(value) ? value : fallback;
}

interface AppearancePreferences {
  textSize: number;
  wheelShortcut: boolean;
  paletteId: string;
  backgroundId: string;
  skinMods: SkinMod[];
}
const defaults = (): AppearancePreferences => ({
  textSize: 100, wheelShortcut: true, paletteId: DEFAULT_PALETTE_ID, backgroundId: DEFAULT_BACKGROUND_ID, skinMods: [],
});

function initialAppearance(): AppearancePreferences {
  try {
    const saved = JSON.parse(profileStorage.getItem(STORAGE_KEY) ?? '{}');
    const skinMods: SkinMod[] = [];
    if (Array.isArray(saved?.skinMods)) for (const value of saved.skinMods.slice(0, MAX_IMPORTED_SKIN_MODS)) {
      try {
        const mod = parseSkinMod(value);
        if (!skinMods.some(existing => existing.id === mod.id)) skinMods.push(mod);
      } catch { /* A malformed mod must not discard other appearance preferences. */ }
    }
    return {
      textSize: textSize(saved?.textSize), wheelShortcut: saved?.wheelShortcut !== false,
      paletteId: selection(saved?.paletteId, DEFAULT_PALETTE_ID),
      backgroundId: selection(saved?.backgroundId, DEFAULT_BACKGROUND_ID), skinMods,
    };
  } catch {
    return defaults();
  }
}

interface Appearance extends AppearancePreferences {
  setTextSize: (value: number) => void;
  setWheelShortcut: (enabled: boolean) => void;
  setPaletteId: (id: string) => void;
  setBackgroundId: (id: string) => void;
  importSkinMod: (input: unknown) => SkinMod;
  removeSkinMod: (id: string) => void;
}

/** Session registrations are deliberately separate from profile-owned imports. */
export const useRegisteredSkinMods = create<{ mods: SkinMod[] }>(() => ({ mods: [] }));

function assertAvailable(id: string, imported: SkinMod[]) {
  if (imported.some(mod => mod.id === id) || useRegisteredSkinMods.getState().mods.some(mod => mod.id === id)) {
    throw new Error(`Skin mod "${id}" is already installed or registered.`);
  }
}

export const useAppearance = create<Appearance>((set, get) => {
  const update = (changes: Partial<AppearancePreferences>) => {
    const { textSize, wheelShortcut, paletteId, backgroundId, skinMods } = get();
    const next = { textSize, wheelShortcut, paletteId, backgroundId, skinMods, ...changes };
    try { profileStorage.setItem(STORAGE_KEY, JSON.stringify(next)); } catch { /* Keep session preferences usable without browser storage. */ }
    set(next);
  };
  return {
    ...initialAppearance(),
    setTextSize: value => update({ textSize: textSize(value) }),
    setWheelShortcut: wheelShortcut => update({ wheelShortcut }),
    setPaletteId: id => update({ paletteId: selection(id, DEFAULT_PALETTE_ID) }),
    setBackgroundId: id => update({ backgroundId: selection(id, DEFAULT_BACKGROUND_ID) }),
    importSkinMod: input => {
      const mod = parseSkinMod(input);
      const { skinMods, paletteId, backgroundId } = get();
      assertAvailable(mod.id, skinMods);
      if (skinMods.length >= MAX_IMPORTED_SKIN_MODS) throw new Error(`At most ${MAX_IMPORTED_SKIN_MODS} skin mods can be imported.`);
      update({ skinMods: [...skinMods, mod],
        paletteId: mod.palettes.length ? `${mod.id}/${mod.palettes[0].id}` : paletteId,
        backgroundId: mod.backgrounds.length ? `${mod.id}/${mod.backgrounds[0].id}` : backgroundId,
      });
      return mod;
    },
    removeSkinMod: id => update({ skinMods: get().skinMods.filter(mod => mod.id !== id) }),
  };
});

/** Register trusted extension data for this session; the owner must call cleanup on disposal. */
export function registerSkinMod(input: SkinMod): () => void {
  const mod = parseSkinMod(input);
  assertAvailable(mod.id, useAppearance.getState().skinMods);
  useRegisteredSkinMods.setState(state => ({ mods: [...state.mods, mod] }));
  let active = true;
  return () => {
    if (!active) return;
    active = false;
    useRegisteredSkinMods.setState(state => ({ mods: state.mods.filter(entry => entry !== mod) }));
  };
}
