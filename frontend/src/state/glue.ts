import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';
import { profileStorage } from './profileStorage';
import { NODE_SURFACE_SIZE, WORKSPACE_MIN_SIZE, type NodeSurfaceLevel, type SurfaceSize } from './nodeSurfaces';
import { worldApi } from '../api/client';
import { reportInteraction } from './interactions';

export interface GlueBox { x: number; y: number; width: number; height: number; level: NodeSurfaceLevel; sizes?: Partial<Record<NodeSurfaceLevel, SurfaceSize>> }
export interface GlueBond { a: string; b: string; side: 'right' | 'left' | 'top' | 'bottom' }
export interface GlueCandidate extends GlueBond { dx: number; dy: number }
function minimumGlueSize(level: NodeSurfaceLevel): SurfaceSize {
  return level === 'workspace' ? WORKSPACE_MIN_SIZE : NODE_SURFACE_SIZE.node;
}
/** Change the surface size, then reposition its bonded neighbours in the same coordinate space. */
export function reflowGlueSurfaces(boxes: Record<string, GlueBox>, bonds: GlueBond[], levels: ReadonlyMap<string, NodeSurfaceLevel>, workspaces: Record<string, SurfaceSize>) {
  const changed = Object.keys(boxes).filter(id => {
    const level = levels.get(id);
    if (!level) return false;
    const minimum = minimumGlueSize(level);
    return level !== boxes[id].level || boxes[id].width < minimum.width || boxes[id].height < minimum.height;
  });
  if (!changed.length) return boxes;
  const next = { ...boxes };
  for (const id of changed) {
    const old = boxes[id], level = levels.get(id)!;
    const size = level === old.level ? old : old.sizes?.[level] ?? (level === 'workspace' ? workspaces[id] : undefined) ?? NODE_SURFACE_SIZE[level];
    const minimum = minimumGlueSize(level);
    next[id] = { ...old, width: Math.max(minimum.width, size.width), height: Math.max(minimum.height, size.height), level, sizes: { ...old.sizes, [old.level]: { width: old.width, height: old.height } } };
  }
  const visited = new Set<string>();
  for (const root of changed) {
    if (visited.has(root)) continue;
    visited.add(root);
    const queue = [root];
    for (let i = 0; i < queue.length; i++) {
      const id = queue[i];
      for (const bond of bonds) {
        if (bond.a !== id && bond.b !== id) continue;
        const peer = bond.a === id ? bond.b : bond.a;
        if (!next[peer] || visited.has(peer)) continue;
        const side = bond.a === id ? bond.side : ({ left: 'right', right: 'left', top: 'bottom', bottom: 'top' } as const)[bond.side];
        const a = next[id], b = next[peer], oldA = boxes[id], oldB = boxes[peer];
        // Preserve aligned ends; otherwise retain the offset while keeping a usable seam.
        const offset = (start: number, length: number, peerStart: number, peerLength: number, newLength: number, newPeerLength: number) => {
          if (Math.abs(start - peerStart) < 1) return 0;
          if (Math.abs(start + length - peerStart - peerLength) < 1) return newLength - newPeerLength;
          return Math.max(24 - newPeerLength, Math.min(newLength - 24, peerStart - start));
        };
        next[peer] = side === 'left' || side === 'right'
          ? { ...b, x: side === 'right' ? a.x + a.width : a.x - b.width, y: a.y + offset(oldA.y, oldA.height, oldB.y, oldB.height, a.height, b.height) }
          : { ...b, y: side === 'bottom' ? a.y + a.height : a.y - b.height, x: a.x + offset(oldA.x, oldA.width, oldB.x, oldB.width, a.width, b.width) };
        visited.add(peer); queue.push(peer);
      }
    }
  }
  return next;
}
export function glueGroup(id: string, bonds: GlueBond[]): Set<string> {
  const ids = new Set([id]);
  let changed = true;
  while (changed) { changed = false; for (const { a, b } of bonds) {
    if (ids.has(a) !== ids.has(b)) { ids.add(a); ids.add(b); changed = true; }
  } }
  return ids;
}
export function findGlue(moving: Record<string, GlueBox>, targets: Record<string, GlueBox>, threshold: number): GlueCandidate | undefined {
  let best: GlueCandidate | undefined;
  let distance = threshold;
  for (const [a, box] of Object.entries(moving)) for (const [b, other] of Object.entries(targets)) {
    if (a === b) continue;
    const overlapX = Math.min(box.x + box.width, other.x + other.width) - Math.max(box.x, other.x);
    const overlapY = Math.min(box.y + box.height, other.y + other.height) - Math.max(box.y, other.y);
    const candidates: Array<[GlueBond['side'], number, number, boolean]> = [
      ['right', other.x - box.x - box.width, 0, overlapY >= 24],
      ['left', other.x + other.width - box.x, 0, overlapY >= 24],
      ['bottom', 0, other.y - box.y - box.height, overlapX >= 24],
      ['top', 0, other.y + other.height - box.y, overlapX >= 24],
    ];
    for (const [side, dx, dy, valid] of candidates) if (valid && Math.abs(dx + dy) < distance) {
      distance = Math.abs(dx + dy); best = { a, b, side, dx, dy };
    }
  }
  return best;
}
export function seam(a: GlueBox, b: GlueBox, side: GlueBond['side']) {
  const vertical = side === 'left' || side === 'right';
  const start = vertical ? Math.max(a.y, b.y) : Math.max(a.x, b.x);
  const end = vertical ? Math.min(a.y + a.height, b.y + b.height) : Math.min(a.x + a.width, b.x + b.width);
  const axis = side === 'right' ? (a.x + a.width + b.x) / 2 : side === 'left' ? (a.x + b.x + b.width) / 2
    : side === 'bottom' ? (a.y + a.height + b.y) / 2 : (a.y + b.y + b.height) / 2;
  return { vertical, start, end, axis };
}
export function freeCorners(id: string, boxes: Record<string, GlueBox>, bonds: GlueBond[]) {
  const box = boxes[id];
  if (!box) return [];
  return (['top-left', 'top-right', 'bottom-left', 'bottom-right'] as const).filter(corner => {
    const x = box.x + (corner.endsWith('right') ? box.width : 0);
    const y = box.y + (corner.startsWith('bottom') ? box.height : 0);
    return !bonds.some(bond => {
      if (bond.a !== id && bond.b !== id || !boxes[bond.a] || !boxes[bond.b]) return false;
      const s = seam(boxes[bond.a], boxes[bond.b], bond.side);
      return Math.abs((s.vertical ? x : y) - s.axis) < 2 && (s.vertical ? y : x) >= s.start - 2 && (s.vertical ? y : x) <= s.end + 2;
    });
  });
}
export function resizeGlued(box: GlueBox, corner: string, dx: number, dy: number, peers: GlueBox[], threshold: number) {
  const minimum = minimumGlueSize(box.level);
  let left = box.x, right = box.x + box.width, top = box.y, bottom = box.y + box.height;
  const snap = (value: number, values: number[]) => values.reduce((best, n) => Math.abs(n - value) < Math.min(threshold, Math.abs(best - value)) ? n : best, value + threshold);
  const snapped = (value: number, values: number[]) => { const n = snap(value, values); return Math.abs(n - value) < threshold ? n : value; };
  if (corner.endsWith('left')) left = Math.min(right - minimum.width, snapped(left + dx, peers.flatMap(p => [p.x, p.x + p.width])));
  else right = Math.max(left + minimum.width, snapped(right + dx, peers.flatMap(p => [p.x, p.x + p.width])));
  if (corner.startsWith('top')) top = Math.min(bottom - minimum.height, snapped(top + dy, peers.flatMap(p => [p.y, p.y + p.height])));
  else bottom = Math.max(top + minimum.height, snapped(bottom + dy, peers.flatMap(p => [p.y, p.y + p.height])));
  return { ...box, x: left, y: top, width: right - left, height: bottom - top };
}
interface GlueState { activeEdits: number; boxes: Record<string, GlueBox>; bonds: GlueBond[]; setLayout: (boxes: Record<string, GlueBox>, bond?: GlueBond) => void; detach: (id: string) => void }
export const useGlueStore = create<GlueState>()(persist((set) => ({
  activeEdits: 0, boxes: {}, bonds: [],
  setLayout: (boxes, bond) => set(state => ({ boxes: { ...state.boxes, ...Object.fromEntries(Object.entries(boxes).map(([id, box]) => [id, { ...state.boxes[id], ...box }])) }, bonds: bond && !state.bonds.some(b => b.a === bond.a && b.b === bond.b || b.a === bond.b && b.b === bond.a) ? [...state.bonds, { a: bond.a, b: bond.b, side: bond.side }] : state.bonds })),
  detach: id => set(state => {
    const bonds = state.bonds.filter(b => b.a !== id && b.b !== id);
    const ids = new Set(bonds.flatMap(b => [b.a, b.b]));
    return { bonds, boxes: Object.fromEntries(Object.entries(state.boxes).filter(([key]) => ids.has(key))) };
  }),
}), { name: 'oaw-glue-v1', storage: createJSONStorage(() => profileStorage), partialize: ({ boxes, bonds }) => ({ boxes, bonds }) }));

export interface SharedGlue { revision: number; boxes: Record<string, GlueBox>; bonds: GlueBond[] }
let sharedRevision: number | undefined;
let loadSequence = 0;
let saves: Promise<unknown> = Promise.resolve();

export function cancelGlueRefresh() { ++loadSequence; }

/** Hold refreshes through the entire gesture and both position/layout saves. */
export function beginGlueEdit() {
  cancelGlueRefresh();
  useGlueStore.setState(state => ({ activeEdits: state.activeEdits + 1 }));
  let ended = false;
  return () => {
    if (ended) return;
    ended = true;
    cancelGlueRefresh();
    useGlueStore.setState(state => ({ activeEdits: state.activeEdits - 1 }));
  };
}

/** Browser persistence is a migration cache; the shared state owns live bonds. */
export async function refreshGlue(migrate = false) {
  if (useGlueStore.getState().activeEdits) return;
  const sequence = ++loadSequence;
  await saves.catch(() => undefined);
  let shared = await worldApi.getGlue();
  if (migrate && shared.revision === 0 && useGlueStore.getState().bonds.length) {
    const world = await worldApi.getWorld();
    const roots = new Set(world.nodes.filter(card => !card.parent_id && !card.equipment && card.type !== 'core.minister').map(card => card.id));
    const local = useGlueStore.getState();
    const bonds = local.bonds.filter(bond => roots.has(bond.a) && roots.has(bond.b));
    const ids = new Set(bonds.flatMap(bond => [bond.a, bond.b]));
    if (bonds.length) shared = await worldApi.saveGlue({ revision: 0, bonds,
      boxes: Object.fromEntries(Object.entries(local.boxes).filter(([key]) => ids.has(key))) });
  }
  if (sequence === loadSequence && !useGlueStore.getState().activeEdits) {
    sharedRevision = shared.revision;
    useGlueStore.setState({ boxes: shared.boxes, bonds: shared.bonds });
  }
}

/** Call at gesture commit, never for every pointer move. A conflict refreshes
 * the shared layout and asks the user to retry instead of replaying stale glue. */
export function persistGlue(detach: string[] = []) {
  const { boxes, bonds } = useGlueStore.getState();
  const edit = { boxes: structuredClone(boxes), bonds: structuredClone(bonds), detach };
  ++loadSequence;
  const save = saves.catch(() => undefined).then(async () => {
    if (sharedRevision === undefined) sharedRevision = (await worldApi.getGlue()).revision;
    const shared = await worldApi.saveGlue({ revision: sharedRevision, ...edit });
    sharedRevision = shared.revision;
    reportInteraction({ type: 'glue-saved', bonds: shared.bonds });
  });
  saves = save;
  return save.catch(async error => { await refreshGlue(); throw error; });
}
