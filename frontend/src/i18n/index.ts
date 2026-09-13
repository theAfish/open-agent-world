import { create } from 'zustand';
import messages from './messages.json';

export type Locale = 'en' | 'zh-CN';
const STORAGE_KEY = 'oaw.locale';
const dictionary: Record<string, readonly string[]> = messages;

function initialLocale(): Locale {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === 'en' || saved === 'zh-CN') return saved;
  } catch { /* Private browsing may disable storage. */ }
  return typeof navigator !== 'undefined' && navigator.language.toLowerCase().startsWith('zh') ? 'zh-CN' : 'en';
}

export const useLocale = create<{ locale: Locale; setLocale: (locale: Locale) => void }>(set => ({
  locale: initialLocale(),
  setLocale(locale) {
    try { localStorage.setItem(STORAGE_KEY, locale); } catch { /* Session switching still works. */ }
    set({ locale });
  },
}));

/** UI copy only: never pass user-authored content, identifiers, or API payloads here. */
export function t(key: string, values: Record<string, string | number> = {}): string {
  const message = dictionary[key]?.[useLocale.getState().locale === 'zh-CN' ? 1 : 0] ?? key;
  return message.replace(/\{(\w+)\}/g, (match, name: string) => Object.hasOwn(values, name) ? String(values[name]) : match);
}
