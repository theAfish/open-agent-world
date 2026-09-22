import type { NodeSurfaceLevel } from '../types/world';

export interface SurfaceSize { width: number; height: number }
export type SurfaceSizes = Record<string, Partial<Record<NodeSurfaceLevel, SurfaceSize>>>;

export const NODE_SURFACE_SIZE = {
  node: { width: 96, height: 96 },
  preview: { width: 224, height: 300 },
  inspector: { width: 438, height: 570 },
  workspace: { width: 1020, height: 700 },
} as const;

export const SURFACE_MAX_SIZE = { width: 4096, height: 4096 };
export function minimumSurfaceSize(level: NodeSurfaceLevel): SurfaceSize {
  if (level === 'workspace') return { width: 640, height: 420 };
  if (level === 'inspector') return { width: 320, height: 240 };
  return NODE_SURFACE_SIZE.node;
}

export function clampSurfaceSize(level: NodeSurfaceLevel, size: SurfaceSize): SurfaceSize {
  const min = minimumSurfaceSize(level);
  return {
    width: Math.max(min.width, Math.min(SURFACE_MAX_SIZE.width, size.width)),
    height: Math.max(min.height, Math.min(SURFACE_MAX_SIZE.height, size.height)),
  };
}

export function surfaceSizeFor(id: string, level: NodeSurfaceLevel, sizes: SurfaceSizes = {}): SurfaceSize {
  return sizes[id]?.[level] ?? NODE_SURFACE_SIZE[level];
}
