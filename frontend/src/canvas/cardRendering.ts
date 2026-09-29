import type { CanvasNode } from '../cards/types';
import type { NodeSurfaceLevel } from '../types/world';

export type CardRenderLOD = 'far' | 'mid' | 'full' | 'offscreen';
export interface Rect { x: number; y: number; width: number; height: number }
export interface Camera extends Rect { zoom: number }
const intersects = (a: Rect, b: Rect) => a.x <= b.x + b.width && a.x + a.width >= b.x
  && a.y <= b.y + b.height && a.y + a.height >= b.y;
const contains = (a: Rect, b: Rect) => b.x >= a.x && b.y >= a.y
  && b.x + b.width <= a.x + a.width && b.y + b.height <= a.y + a.height;
const expand = (r: Rect, margin: number): Rect => ({ x: r.x - margin, y: r.y - margin, width: r.width + margin * 2, height: r.height + margin * 2 });

// Full compact cards are usable well before a multi-pane workspace. Thresholds
// describe projected pixels, never camera zoom or a change to saved presentation.
const THRESHOLDS = {
  node: { mid: 16, midExit: 12, full: 32, fullExit: 26 },
  preview: { mid: 44, midExit: 36, full: 80, fullExit: 64 },
  inspector: { mid: 48, midExit: 38, full: 160, fullExit: 132 },
  workspace: { mid: 48, midExit: 38, full: 200, fullExit: 168 },
} as const;

/** Area-equivalent screen size; cap extreme aspect ratios so a thin strip
 * doesn't hydrate like a readable workspace just because it is very long. */
export function projectedCardSize(width: number, height: number, zoom: number) {
  const short = Math.max(0, Math.min(width, height));
  return Math.sqrt(short * Math.min(Math.max(width, height), short * 4)) * zoom;
}

export function cardLOD(size: number, previous?: CardRenderLOD, surface: NodeSurfaceLevel = 'workspace'): Exclude<CardRenderLOD, 'offscreen'> {
  const threshold = THRESHOLDS[surface];
  if (size >= (previous === 'full' ? threshold.fullExit : threshold.full)) return 'full';
  if (size >= (previous === 'mid' || previous === 'full' ? threshold.midExit : threshold.mid)) return 'mid';
  return 'far';
}

/** A bounded uniform 2D index. Oversized containers use one overflow entry. */
export class CardSpatialIndex {
  private cells = new Map<string, Set<string>>();
  private bounds = new Map<string, Rect>();
  private overflow = new Set<string>();
  private keys(rect: Rect) {
    const left = Math.floor(rect.x / 2048), right = Math.floor((rect.x + rect.width) / 2048);
    const top = Math.floor(rect.y / 2048), bottom = Math.floor((rect.y + rect.height) / 2048);
    if ((right - left + 1) * (bottom - top + 1) > 4096) return null;
    const keys: string[] = [];
    for (let y = top; y <= bottom; y++) for (let x = left; x <= right; x++) keys.push(`${x}:${y}`);
    return keys;
  }
  set(id: string, rect: Rect) {
    const before = this.bounds.get(id);
    if (before) for (const key of this.keys(before) ?? []) {
      const cell = this.cells.get(key); cell?.delete(id); if (!cell?.size) this.cells.delete(key);
    }
    this.overflow.delete(id); this.bounds.set(id, rect);
    const keys = this.keys(rect);
    if (!keys) { this.overflow.add(id); return; }
    for (const key of keys) {
      let cell = this.cells.get(key); if (!cell) this.cells.set(key, cell = new Set()); cell.add(id);
    }
  }
  query(rect: Rect) {
    const keys = this.keys(rect);
    const ids = new Set(keys ? this.overflow : this.bounds.keys());
    if (keys) for (const key of keys) for (const id of this.cells.get(key) ?? []) ids.add(id);
    return new Set([...ids].filter(id => intersects(this.bounds.get(id)!, rect)));
  }
}

/** Per-canvas, transient policy. Geometry, visibility and hydration have separate
 * lifetimes. No world-store writes and no persisted presentation changes. */
export class CardRenderModel {
  private nodes: CanvasNode[] = [];
  private geometry = '';
  private boxes = new Map<string, Rect>();
  private index = new CardSpatialIndex();
  private mounted = new Set<string>();
  private levels = new Map<string, CardRenderLOD>();
  private automaticLevels = new Map<string, CardRenderLOD>();
  private pins = new Map<string, Set<string>>();
  private gestures = new Set<string>();
  private camera: Camera = { x: 0, y: 0, zoom: 1, width: 0, height: 0 };
  private safe?: Rect;
  private listeners = new Set<() => void>();
  private cardListeners = new Map<string, Set<() => void>>();
  private revision = 0;
  private projectionRevision = 0;
  private frame?: number;
  private suspended = false;
  private projected = new Map<string, { source: CanvasNode; lod: CardRenderLOD; node: CanvasNode }>();
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  getSnapshot = () => this.revision;
  getProjectionSnapshot = () => this.projectionRevision;
  getLevel = (id: string): CardRenderLOD => this.levels.get(id) ?? 'offscreen';
  subscribeCard = (id: string, listener: () => void) => {
    let listeners = this.cardListeners.get(id);
    if (!listeners) this.cardListeners.set(id, listeners = new Set());
    listeners.add(listener);
    return () => { listeners.delete(listener); if (!listeners.size) this.cardListeners.delete(id); };
  };
  cancelScheduled() { if (this.frame !== undefined) cancelAnimationFrame(this.frame); this.frame = undefined; }
  suspend() { this.suspended = true; this.cancelScheduled(); }
  setNodes(nodes: CanvasNode[]) {
    this.nodes = nodes;
    const signature = nodes.map(n => [n.id, n.parentId, n.position.x, n.position.y, n.width, n.height, n.data.surfaceLevel, n.hidden, n.selected, n.dragging, n.resizing].join(':')).join('|');
    if (signature === this.geometry) return;
    this.geometry = signature;
    this.boxes = new Map(); this.index = new CardSpatialIndex();
    const byId = new Map(nodes.map(n => [n.id, n]));
    const box = (n: CanvasNode): Rect => {
      const known = this.boxes.get(n.id); if (known) return known;
      const parent = byId.get(n.parentId ?? '');
      const origin = parent ? box(parent) : { x: 0, y: 0 };
      const rect = { x: origin.x + n.position.x, y: origin.y + n.position.y,
        width: Number(n.width ?? n.style?.width ?? 96), height: Number(n.height ?? n.style?.height ?? 96) };
      this.boxes.set(n.id, rect); return rect;
    };
    for (const node of nodes) if (!node.hidden) this.index.set(node.id, box(node));
    for (const id of this.levels.keys()) if (!byId.has(id)) {
      this.levels.delete(id); this.automaticLevels.delete(id); this.pins.delete(id); this.gestures.delete(id); this.projected.delete(id);
    }
    this.safe = undefined; this.update();
  }
  setCamera(camera: Camera) { this.suspended = false; this.camera = camera; this.update(); }
  holdGesture(id: string, active: boolean) {
    if (active) this.gestures.add(id); else this.gestures.delete(id);
    this.update();
  }
  pin(id: string, reason: string, active: boolean) {
    let reasons = this.pins.get(id);
    if (active === !!reasons?.has(reason)) return;
    if (active) { if (!reasons) this.pins.set(id, reasons = new Set()); reasons.add(reason); }
    else { reasons?.delete(reason); if (!reasons?.size) this.pins.delete(id); }
    this.update();
  }
  private update() {
    if (this.suspended) return;
    const c = this.camera;
    if (!(c.width > 0 && c.height > 0 && c.zoom > 0)) return;
    const viewport = { x: -c.x / c.zoom, y: -c.y / c.zoom, width: c.width / c.zoom, height: c.height / c.zoom };
    if (!this.safe || !contains(this.safe, viewport)) {
      const enter = expand(viewport, 240 / c.zoom), exit = expand(viewport, 480 / c.zoom);
      const mounted = this.index.query(enter);
      for (const id of this.mounted) { const box = this.boxes.get(id); if (box && intersects(box, exit)) mounted.add(id); }
      this.mounted = mounted;
      this.safe = expand(viewport, 100 / c.zoom);
    }
    const changed: string[] = [];
    let projectionChanged = false, pending = false;
    // Many equal-sized cards cross a boundary together. Spread automatic view
    // replacement across frames; interaction pins always bypass this budget.
    let proxyBudget = 12, fullBudget = 3;
    for (const node of this.nodes) {
      const box = this.boxes.get(node.id);
      const pinned = node.selected || node.dragging || node.resizing || this.pins.has(node.id);
      const visible = !node.hidden && (this.mounted.has(node.id) || pinned || this.gestures.has(node.id));
      const size = projectedCardSize(box?.width ?? 0, box?.height ?? 0, c.zoom);
      const auto = cardLOD(size, this.automaticLevels.get(node.id), node.data.surfaceLevel);
      this.automaticLevels.set(node.id, auto);
      const previous = this.levels.get(node.id);
      const lod = !visible ? 'offscreen' : pinned ? 'full'
        : this.gestures.has(node.id) && previous && previous !== 'offscreen' ? previous : auto;
      if (previous && previous !== 'offscreen' && lod !== 'offscreen' && lod !== previous && !pinned
        && typeof requestAnimationFrame === 'function') {
        if (lod === 'full' ? fullBudget-- <= 0 : proxyBudget-- <= 0) { pending = true; continue; }
      }
      if (previous !== lod) {
        this.levels.set(node.id, lod); changed.push(node.id);
        if (Boolean(previous && previous !== 'offscreen') !== (lod !== 'offscreen')) projectionChanged = true;
      }
    }
    if (pending && this.frame === undefined) this.frame = requestAnimationFrame(() => { this.frame = undefined; this.update(); });
    else if (!pending) this.cancelScheduled();
    if (changed.length) {
      this.revision++;
      if (projectionChanged) this.projectionRevision++;
      this.listeners.forEach(listener => listener());
      for (const id of changed) this.cardListeners.get(id)?.forEach(listener => listener());
    }
  }
  project(nodes: CanvasNode[], geometryOnly = false) {
    return nodes.map(source => {
      const level = this.levels.get(source.id) ?? 'offscreen';
      // XYFlow needs only geometry/visibility. Publishing each renderer change
      // through its node array invalidates every node/minimap subscription.
      const lod = geometryOnly && level !== 'offscreen' ? 'full' : level;
      const cached = this.projected.get(source.id);
      if (cached?.source === source && cached.lod === lod) return cached.node;
      // Keep geometry proxies in XYFlow for edges, selection, fitView, minimap,
      // nested ownership and public getNodes(). Only their views are virtualized.
      const node: CanvasNode = { ...source, data: { ...source.data, renderLOD: lod },
        className: lod === 'offscreen' ? source.className : [source.className, 'card-view-mounted'].filter(Boolean).join(' '),
        style: lod === 'offscreen' ? { ...source.style, display: 'none' } : source.style };
      this.projected.set(source.id, { source, lod, node });
      return node;
    });
  }
}
