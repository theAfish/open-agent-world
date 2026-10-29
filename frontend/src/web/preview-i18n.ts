// The standalone storefront is English. Keep application profile storage and
// host authentication out of its renderer bundle.
export function useLocale() {}
export function t(key: string, values: Record<string, string | number> = {}) {
  return key.replace(/\{(\w+)\}/g, (match, name: string) => Object.hasOwn(values, name) ? String(values[name]) : match);
}
