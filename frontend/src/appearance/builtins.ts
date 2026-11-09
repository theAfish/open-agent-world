import type { SkinMod } from './skinMod';

export const DEFAULT_PALETTE_ID = 'oaw/paper';
export const DEFAULT_BACKGROUND_ID = 'oaw/terrain';

/** Paper inherits the existing light/dark theme, including future host tokens. */
export const BUILTIN_SKIN_MOD: SkinMod = {
  schemaVersion: 1, id: 'oaw', name: 'Open Agent World',
  palettes: [
    { id: 'paper', name: 'Paper', light: {}, dark: {} },
    { id: 'ocean', name: 'Ocean', light: {
      '--canvas': '#dce6eb', '--canvas-deep': '#c8d8e0', '--surface': '#f2f7faf2', '--surface-solid': '#eff5f8',
      '--surface-soft': '#e2ecf1', '--surface-raised': '#f8fcff', '--chrome': '#f2f7fae6', '--chrome-raised': '#f1f7fa',
      '--card-surface': '#f9fdff', '--card-soft': '#eff6fa', '--ink': '#233945', '--ink-soft': '#526b78', '--ink-faint': '#667f8b',
      '--line': '#324f6129', '--line-strong': '#324f6147', '--accent': '#287b99', '--accent-strong': '#1c607c', '--accent-soft': '#287b9921',
      '--grid-dot': '#324f612b', '--contour': '#287b9926', '--contour-fill': '#287b9902', '--edge': '#678494', '--edge-arrow': '#678494',
      '--on-accent': '#f8fcff', '--minimap-mask': '#c8d8e0b8', '--scrollbar-thumb': '#526b7861', '--scrollbar-thumb-hover': '#526b7894',
    }, dark: {
      '--canvas': '#172731', '--canvas-deep': '#101e27', '--surface': '#20343ff5', '--surface-solid': '#243b47',
      '--surface-soft': '#1d303b', '--surface-raised': '#2c4552', '--chrome': '#20343ff0', '--chrome-raised': '#29414e',
      '--card-surface': '#2b4350', '--card-soft': '#253b48', '--ink': '#e0edf3', '--ink-soft': '#b0c9d6', '--ink-faint': '#8faab8',
      '--line': '#d6eaf521', '--line-strong': '#d6eaf53d', '--accent': '#72bfdb', '--accent-strong': '#9dd7ec', '--accent-soft': '#72bfdb26',
      '--grid-dot': '#d6eaf524', '--contour': '#72bfdb29', '--contour-fill': '#72bfdb02', '--edge': '#91b2c3', '--edge-arrow': '#91b2c3',
      '--on-accent': '#102731', '--minimap-mask': '#101e27c2', '--scrollbar-thumb': '#b0c9d64d', '--scrollbar-thumb-hover': '#b0c9d680',
    } },
    { id: 'forest', name: 'Forest', light: {
      '--canvas': '#e1e7dd', '--canvas-deep': '#ced8c8', '--surface': '#f4f8f0f2', '--surface-solid': '#f1f5ed',
      '--surface-soft': '#e6eddf', '--surface-raised': '#fbfdf7', '--chrome': '#f4f8f0e6', '--chrome-raised': '#f3f7ed',
      '--card-surface': '#fcfef8', '--card-soft': '#f2f7eb', '--ink': '#303e2e', '--ink-soft': '#5a6d53', '--ink-faint': '#728269',
      '--line': '#3f553729', '--line-strong': '#3f553747', '--accent': '#537b45', '--accent-strong': '#3e6332', '--accent-soft': '#537b4521',
      '--grid-dot': '#3f55372b', '--contour': '#537b4526', '--contour-fill': '#537b4502', '--edge': '#77886c', '--edge-arrow': '#77886c',
      '--on-accent': '#fbfdf7', '--minimap-mask': '#ced8c8b8', '--scrollbar-thumb': '#5a6d5361', '--scrollbar-thumb-hover': '#5a6d5394',
    }, dark: {
      '--canvas': '#202a20', '--canvas-deep': '#171f17', '--surface': '#2c382af5', '--surface-solid': '#303e2d',
      '--surface-soft': '#273425', '--surface-raised': '#3a4936', '--chrome': '#2c382af0', '--chrome-raised': '#354431',
      '--card-surface': '#394733', '--card-soft': '#313e2c', '--ink': '#e7efdf', '--ink-soft': '#bfcfb3', '--ink-faint': '#9aad8d',
      '--line': '#e4efda21', '--line-strong': '#e4efda3d', '--accent': '#a0c58b', '--accent-strong': '#bdddab', '--accent-soft': '#a0c58b26',
      '--grid-dot': '#e4efda24', '--contour': '#a0c58b29', '--contour-fill': '#a0c58b02', '--edge': '#a3b697', '--edge-arrow': '#a3b697',
      '--on-accent': '#1c2a17', '--minimap-mask': '#171f17c2', '--scrollbar-thumb': '#bfcfb34d', '--scrollbar-thumb-hover': '#bfcfb380',
    } },
  ],
  backgrounds: [
    { id: 'terrain', name: 'Terrain', kind: 'terrain' },
    { id: 'solid', name: 'Solid', kind: 'solid' },
    { id: 'dots', name: 'Dots', kind: 'dots', spacing: 24 },
    { id: 'grid', name: 'Grid', kind: 'grid', spacing: 32 },
    { id: 'gradient', name: 'Gradient', kind: 'gradient', angle: 135 },
  ],
};
