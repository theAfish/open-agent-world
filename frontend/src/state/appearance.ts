import { create } from 'zustand';
import { profileStorage } from './profileStorage';

const STORAGE_KEY = 'oaw-appearance';
export const MIN_TEXT_SIZE = 80;
export const MAX_TEXT_SIZE = 200;
export const TEXT_SIZE_STEP = 10;

function textSize(value: unknown) {
  return typeof value === 'number' && Number.isFinite(value)
    ? Math.max(MIN_TEXT_SIZE, Math.min(MAX_TEXT_SIZE, Math.round(value / TEXT_SIZE_STEP) * TEXT_SIZE_STEP)) : 100;
}

function initialAppearance() {
  try {
    const saved = JSON.parse(profileStorage.getItem(STORAGE_KEY) ?? '{}');
    return { textSize: textSize(saved?.textSize), wheelShortcut: saved?.wheelShortcut !== false };
  } catch {
    return { textSize: 100, wheelShortcut: true };
  }
}

interface Appearance {
  textSize: number;
  wheelShortcut: boolean;
  setTextSize: (value: number) => void;
  setWheelShortcut: (enabled: boolean) => void;
}

export const useAppearance = create<Appearance>((set, get) => {
  const update = (changes: Partial<Pick<Appearance, 'textSize' | 'wheelShortcut'>>) => {
    const next = { textSize: get().textSize, wheelShortcut: get().wheelShortcut, ...changes };
    try { profileStorage.setItem(STORAGE_KEY, JSON.stringify(next)); } catch { /* Keep session preferences usable without browser storage. */ }
    set(next);
  };
  return {
    ...initialAppearance(),
    setTextSize: value => update({ textSize: textSize(value) }),
    setWheelShortcut: wheelShortcut => update({ wheelShortcut }),
  };
});
