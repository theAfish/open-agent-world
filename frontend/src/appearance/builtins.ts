import type { SkinMod } from './skinMod';

export const DEFAULT_PALETTE_ID = 'oaw/paper';
export const DEFAULT_BACKGROUND_ID = 'oaw/terrain';

/** Paper inherits the host theme. Ocean and Forest keep large surfaces nearly neutral,
 * reserving their signature hue for accents and subtle canvas details. */
export const BUILTIN_SKIN_MOD: SkinMod = {
  schemaVersion: 1, id: 'oaw', name: 'Open Agent World',
  palettes: [
    { id: 'paper', name: 'Paper', light: {}, dark: {} },
    { id: 'ocean', name: 'Ocean', light: {
      '--canvas': '#e8eaeb', '--canvas-deep': '#dcdfe1', '--surface': '#fafafbf2', '--surface-solid': '#f5f6f7',
      '--surface-soft': '#eceeef', '--surface-raised': '#ffffff', '--chrome': '#fafafbe6', '--chrome-raised': '#f7f8f9',
      '--card-surface': '#ffffff', '--card-soft': '#f5f6f7', '--ink': '#30353b', '--ink-soft': '#59616a', '--ink-faint': '#6f7780',
      '--line': '#343b4329', '--line-strong': '#343b4347', '--accent': '#386f9e', '--accent-strong': '#2d5b83', '--accent-soft': '#386f9e18',
      '--grid-dot': '#343b4324', '--contour': '#627a8d1f', '--contour-fill': '#627a8d02', '--edge': '#7a838b', '--edge-arrow': '#7a838b',
      '--on-accent': '#ffffff', '--minimap-mask': '#dcdfe1b8', '--scrollbar-thumb': '#59616a61', '--scrollbar-thumb-hover': '#59616a94',
    }, dark: {
      '--canvas': '#222528', '--canvas-deep': '#1b1d20', '--surface': '#2b2e32f5', '--surface-solid': '#2e3236',
      '--surface-soft': '#26292d', '--surface-raised': '#393e43', '--chrome': '#2b2e32f0', '--chrome-raised': '#34393e',
      '--card-surface': '#373c41', '--card-soft': '#30353a', '--ink': '#e9ecef', '--ink-soft': '#bdc3c9', '--ink-faint': '#939ca5',
      '--line': '#e5e9ed21', '--line-strong': '#e5e9ed3d', '--accent': '#83afd5', '--accent-strong': '#a6c7e3', '--accent-soft': '#83afd51f',
      '--grid-dot': '#e5e9ed20', '--contour': '#8c9fac22', '--contour-fill': '#8c9fac02', '--edge': '#949fa9', '--edge-arrow': '#949fa9',
      '--on-accent': '#19232c', '--minimap-mask': '#1b1d20c2', '--scrollbar-thumb': '#bdc3c94d', '--scrollbar-thumb-hover': '#bdc3c980',
    } },
    { id: 'forest', name: 'Forest', light: {
      '--canvas': '#e9e9e4', '--canvas-deep': '#dedfd8', '--surface': '#fafaf7f2', '--surface-solid': '#f6f6f2',
      '--surface-soft': '#edeee8', '--surface-raised': '#fffffc', '--chrome': '#fafaf7e6', '--chrome-raised': '#f8f8f4',
      '--card-surface': '#fffffc', '--card-soft': '#f6f6f1', '--ink': '#343831', '--ink-soft': '#5e655a', '--ink-faint': '#747c6e',
      '--line': '#3d443729', '--line-strong': '#3d443747', '--accent': '#5c7753', '--accent-strong': '#46613e', '--accent-soft': '#5c775318',
      '--grid-dot': '#3d443724', '--contour': '#78866b1f', '--contour-fill': '#78866b02', '--edge': '#808778', '--edge-arrow': '#808778',
      '--on-accent': '#ffffff', '--minimap-mask': '#dedfd8b8', '--scrollbar-thumb': '#5e655a61', '--scrollbar-thumb-hover': '#5e655a94',
    }, dark: {
      '--canvas': '#252723', '--canvas-deep': '#1d1f1c', '--surface': '#2e312cf5', '--surface-solid': '#32352f',
      '--surface-soft': '#292c27', '--surface-raised': '#3d4139', '--chrome': '#2e312cf0', '--chrome-raised': '#383c34',
      '--card-surface': '#3b3f37', '--card-soft': '#343830', '--ink': '#ebede6', '--ink-soft': '#c1c6b9', '--ink-faint': '#989f90',
      '--line': '#e7ebdf21', '--line-strong': '#e7ebdf3d', '--accent': '#a3bb91', '--accent-strong': '#c0d2b2', '--accent-soft': '#a3bb911f',
      '--grid-dot': '#e7ebdf20', '--contour': '#a3ae9422', '--contour-fill': '#a3ae9402', '--edge': '#9da691', '--edge-arrow': '#9da691',
      '--on-accent': '#21291c', '--minimap-mask': '#1d1f1cc2', '--scrollbar-thumb': '#c1c6b94d', '--scrollbar-thumb-hover': '#c1c6b980',
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
