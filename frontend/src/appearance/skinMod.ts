/** Color-only tokens shared by the shell, cards, and canvas renderers. */
export const COLOR_TOKENS = [
  '--canvas', '--canvas-deep', '--surface', '--surface-solid', '--surface-soft', '--surface-raised',
  '--chrome', '--chrome-raised', '--card-surface', '--card-soft', '--ink', '--ink-soft', '--ink-faint',
  '--line', '--line-strong', '--highlight', '--shadow', '--shadow-deep', '--accent', '--accent-strong',
  '--accent-soft', '--agent', '--text', '--image', '--sandbox', '--danger', '--danger-soft', '--warning',
  '--success', '--success-soft', '--info', '--info-soft', '--on-accent', '--action-fill', '--grid-dot',
  '--contour', '--contour-fill', '--edge', '--edge-arrow', '--minimap-mask', '--scrollbar-thumb', '--scrollbar-thumb-hover',
] as const;
export type SkinColorToken = typeof COLOR_TOKENS[number];
export type SkinColors = Partial<Record<SkinColorToken, string>>;
export interface SkinPalette { id: string; name: string; light: SkinColors; dark: SkinColors }
export interface SkinBackground {
  id: string;
  name: string;
  kind: 'terrain' | 'solid' | 'dots' | 'grid' | 'gradient';
  spacing?: number;
  angle?: number;
}
export interface SkinMod {
  schemaVersion: 1;
  id: string;
  name: string;
  palettes: SkinPalette[];
  backgrounds: SkinBackground[];
}
export interface PaletteEntry extends SkinPalette { modId: string }
export interface BackgroundEntry extends SkinBackground { modId: string }
export const MAX_SKIN_MOD_BYTES = 64 * 1024;
export const MAX_IMPORTED_SKIN_MODS = 12;

function object(value: unknown, keys: string[], label: string): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || Object.getPrototypeOf(value) !== Object.prototype && Object.getPrototypeOf(value) !== null) {
    throw new Error(`${label} must be an object.`);
  }
  const result = value as Record<string, unknown>;
  if (Object.keys(result).some(key => !keys.includes(key))) throw new Error(`${label} contains an unsupported field.`);
  return result;
}
function id(value: unknown): string {
  if (typeof value !== 'string' || !/^[a-z][a-z0-9._-]{0,63}$/.test(value)) throw new Error('Skin IDs must use lowercase letters, numbers, dots, underscores or hyphens (1–64 characters).');
  return value;
}
function name(value: unknown): string {
  if (typeof value !== 'string' || !value.trim() || value.length > 80) throw new Error('Skin names must contain 1–80 characters.');
  return value.trim();
}
function colors(value: unknown): SkinColors {
  const values = object(value, [...COLOR_TOKENS], 'Palette colors');
  const result: SkinColors = {};
  for (const [key, color] of Object.entries(values)) {
    if (typeof color !== 'string' || !/^#(?:[\da-f]{3}|[\da-f]{4}|[\da-f]{6}|[\da-f]{8})$/i.test(color)) {
      throw new Error('Palette colors must be hexadecimal colors (#RGB, #RGBA, #RRGGBB or #RRGGBBAA).');
    }
    result[key as SkinColorToken] = color;
  }
  return result;
}
function entries<T extends { id: string }>(value: unknown, parse: (entry: unknown) => T): T[] {
  if (!Array.isArray(value) || value.length > 16) throw new Error('A skin mod may contain at most 16 palettes and 16 backgrounds.');
  const result = value.map(parse);
  if (new Set(result.map(entry => entry.id)).size !== result.length) throw new Error('Skin entry IDs must be unique within each collection.');
  return result;
}

/** Parse a data-only mod. CSS, URLs, executable code, and unknown fields are rejected. */
export function parseSkinMod(input: unknown): SkinMod {
  let serialized: string | undefined;
  try { serialized = JSON.stringify(input); } catch { throw new Error('A skin mod must be JSON data.'); }
  if (!serialized || new TextEncoder().encode(serialized).length > MAX_SKIN_MOD_BYTES) throw new Error('A skin mod must be at most 64 KiB.');
  const value = object(input, ['schemaVersion', 'id', 'name', 'palettes', 'backgrounds'], 'Skin mod');
  if (value.schemaVersion !== 1) throw new Error('Unsupported skin mod schema version.');
  const modId = id(value.id);
  if (modId === 'oaw') throw new Error('The oaw skin namespace is reserved.');
  const palettes = entries(value.palettes, entry => {
    const palette = object(entry, ['id', 'name', 'light', 'dark'], 'Palette');
    return { id: id(palette.id), name: name(palette.name), light: colors(palette.light), dark: colors(palette.dark) };
  });
  const backgrounds = entries(value.backgrounds, entry => {
    const background = object(entry, ['id', 'name', 'kind', 'spacing', 'angle'], 'Background');
    if (typeof background.kind !== 'string' || !['terrain', 'solid', 'dots', 'grid', 'gradient'].includes(background.kind)) throw new Error('Unsupported background kind.');
    const result: SkinBackground = { id: id(background.id), name: name(background.name), kind: background.kind as SkinBackground['kind'] };
    if (background.spacing !== undefined) {
      if (!['dots', 'grid'].includes(result.kind) || typeof background.spacing !== 'number'
        || !Number.isFinite(background.spacing) || background.spacing < 8 || background.spacing > 128) {
        throw new Error('Dots and grid spacing must be between 8 and 128.');
      }
      result.spacing = background.spacing;
    }
    if (background.angle !== undefined) {
      if (result.kind !== 'gradient' || typeof background.angle !== 'number'
        || !Number.isFinite(background.angle) || background.angle < 0 || background.angle > 360) {
        throw new Error('Gradient angle must be between 0 and 360.');
      }
      result.angle = background.angle;
    }
    return result;
  });
  if (!palettes.length && !backgrounds.length) throw new Error('A skin mod must contain a palette or background.');
  return { schemaVersion: 1, id: modId, name: name(value.name), palettes, backgrounds };
}
