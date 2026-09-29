import { describe, expect, it, vi } from 'vitest';
import { CardRenderModel, CardSpatialIndex, cardLOD, projectedCardSize } from './cardRendering';
import { createDisplacementCache } from './nodeDisplacement';
import { buildCardDraft } from '../state/helpers';
import type { CanvasNode } from '../cards/types';
const node = (id: string, x = 0, y = 0, width = 100, height = 100): CanvasNode => ({
  id, type: 'worldCard', position: { x, y }, width, height, style: { width, height },
  data: { card: { id, ...buildCardDraft('sandbox', { x, y }) }, surfaceLevel: 'workspace', displaced: false },
});
const camera = { x: 0, y: 0, width: 1000, height: 800, zoom: 1 };
describe('semantic canvas rendering', () => {
  it('keeps 1000 workspace models but hydrates only interacting cards in an overview', () => {
    const nodes = Array.from({ length: 1000 }, (_, i) => node(String(i), (i % 40) * 1300, Math.floor(i / 40) * 900, 1020, 700));
    const model = new CardRenderModel(); model.setNodes(nodes);
    model.setCamera({ x: 0, y: 0, width: 1920, height: 1080, zoom: .12 });
    expect(model.project(nodes)).toHaveLength(1000);
    expect(model.project(nodes).filter(n => n.data.renderLOD === 'full')).toHaveLength(0);
    expect(model.project(nodes).filter(n => n.data.renderLOD !== 'offscreen').length).toBeGreaterThan(100);
    model.pin('0', 'editing', true); model.pin('999', 'focus', true);
    expect(model.project(nodes).filter(n => n.data.renderLOD === 'full')).toHaveLength(2);
  });
  it('uses screen size and distinct promotion/demotion thresholds', () => {
    expect(cardLOD(47)).toBe('far'); expect(cardLOD(48)).toBe('mid');
    expect(cardLOD(39, 'mid')).toBe('mid'); expect(cardLOD(37, 'mid')).toBe('far');
    expect(cardLOD(200)).toBe('full'); expect(cardLOD(169, 'full')).toBe('full');
    expect(cardLOD(167, 'full')).toBe('mid');
    expect(cardLOD(42, 'full')).toBe('mid');
  });
  it('makes compact cards interactive at normal distances and keeps large workspaces faithful', () => {
    expect(cardLOD(projectedCardSize(96, 96, .35), undefined, 'node')).toBe('full');
    expect(cardLOD(projectedCardSize(224, 300, .35), undefined, 'preview')).toBe('full');
    expect(cardLOD(projectedCardSize(1020, 700, .12))).toBe('mid');
    expect(cardLOD(projectedCardSize(1020, 700, .25))).toBe('full');
    expect(cardLOD(projectedCardSize(2000, 800, .12))).toBe('mid');
    expect(cardLOD(projectedCardSize(3000, 1500, .12))).toBe('full');
    expect(projectedCardSize(4000, 10, 1)).toBe(20);
    expect(cardLOD(70, 'full', 'preview')).toBe('full');
    expect(cardLOD(70, 'mid', 'preview')).toBe('mid');
  });
  it('overscans, retains the exit ring and does not publish ordinary camera movement', () => {
    const model = new CardRenderModel(), nodes = [node('a', 1100), node('b', 1450)];
    model.setNodes(nodes); model.setCamera(camera);
    expect(model.project(nodes).map(n => n.data.renderLOD)).toEqual(['mid', 'offscreen']);
    const revision = model.getSnapshot(); model.setCamera({ ...camera, x: -20 });
    expect(model.getSnapshot()).toBe(revision);
    model.setCamera({ ...camera, x: -250 }); expect(model.project(nodes)[1].data.renderLOD).toBe('mid');
    model.setCamera(camera); expect(model.project(nodes)[1].data.renderLOD).toBe('mid');
    model.setCamera({ ...camera, x: 160 }); expect(model.project(nodes)[1].data.renderLOD).toBe('offscreen');
  });
  it('budgets mass automatic transitions, upgrades interaction immediately and discards reversed targets', () => {
    const callbacks = new Map<number, FrameRequestCallback>(); let serial = 0;
    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => { callbacks.set(++serial, cb); return serial; });
    vi.stubGlobal('cancelAnimationFrame', (id: number) => callbacks.delete(id));
    try {
      const nodes = Array.from({ length: 40 }, (_, i) => {
        const n = node(String(i), 0, 0, 224, 300); n.data.surfaceLevel = 'preview'; return n;
      });
      const model = new CardRenderModel(); model.setNodes(nodes); model.setCamera({ ...camera, zoom: .12 });
      model.setCamera({ ...camera, zoom: .22 });
      const mids = () => model.project(nodes).filter(n => n.data.renderLOD === 'mid').length;
      expect(mids()).toBeGreaterThan(0); expect(mids()).toBeLessThan(40);
      model.pin('39', 'focus', true);
      expect(model.project(nodes)[39].data.renderLOD).toBe('full');
      model.setCamera({ ...camera, zoom: .12 });
      while (callbacks.size) { const [id, cb] = callbacks.entries().next().value!; callbacks.delete(id); cb(0); }
      expect(mids()).toBe(0);
      expect(model.project(nodes).filter(n => n.data.renderLOD === 'far')).toHaveLength(39);
      model.setCamera({ ...camera, zoom: .22 });
      expect(callbacks.size).toBe(1); model.suspend(); expect(callbacks.size).toBe(0);
      model.pin('39', 'focus', false); expect(callbacks.size).toBe(0);
      model.setCamera({ ...camera, zoom: .12 });
      model.cancelScheduled(); expect(callbacks.size).toBe(0);
    } finally { vi.unstubAllGlobals(); }
  });
  it('notifies only the affected renderer and keeps XYFlow geometry identities through LOD changes', () => {
    const model = new CardRenderModel(), nodes = [node('a', 0, 0, 1020, 700), node('b', 1500, 0, 1020, 700)];
    model.setNodes(nodes); model.setCamera({ ...camera, zoom: .12 });
    const before = model.project(nodes, true), projection = model.getProjectionSnapshot();
    const a = vi.fn(), b = vi.fn(); const unsubscribe = model.subscribeCard('a', a); model.subscribeCard('b', b);
    model.pin('a', 'focus', true);
    expect(model.getLevel('a')).toBe('full'); expect(a).toHaveBeenCalledOnce(); expect(b).not.toHaveBeenCalled();
    expect(model.getProjectionSnapshot()).toBe(projection);
    const after = model.project(nodes, true);
    expect(after[0]).toBe(before[0]); expect(after[1]).toBe(before[1]);
    unsubscribe(); model.pin('a', 'focus', false); expect(a).toHaveBeenCalledOnce();
  });
  it('pins selection, focus, editing and dragging without changing saved presentation/geometry', () => {
    const model = new CardRenderModel(), nodes = [node('a', 5000)];
    const before = structuredClone(nodes);
    model.setNodes(nodes); model.setCamera({ ...camera, zoom: .12 });
    expect(model.project(nodes)[0].data.renderLOD).toBe('far');
    model.pin('a', 'focus', true); model.pin('a', 'editing', true);
    expect(model.project(nodes)[0].data.renderLOD).toBe('full');
    model.pin('a', 'focus', false); expect(model.project(nodes)[0].data.renderLOD).toBe('full');
    model.pin('a', 'editing', false); expect(model.project(nodes)[0].data.renderLOD).toBe('far');
    model.setNodes([{ ...nodes[0], selected: true }]);
    model.setCamera(camera); expect(model.project([{ ...nodes[0], selected: true }])[0].data.renderLOD).toBe('full');
    expect(nodes).toEqual(before);
  });
  it('hydrates selection immediately during a pointer gesture', () => {
    const model = new CardRenderModel(), nodes = [node('a', 0, 0, 224, 300)];
    model.setNodes(nodes); model.setCamera({ ...camera, zoom: .12 });
    model.holdGesture('a', true);
    const selected = [{ ...nodes[0], selected: true, dragging: true }];
    model.setNodes(selected);
    expect(model.project(selected)[0].data.renderLOD).toBe('full');
    model.holdGesture('a', false);
    expect(model.project(selected)[0].data.renderLOD).toBe('full');
  });
  it('indexes absolute nested rectangles while preserving logical hidden and endpoint proxies', () => {
    const model = new CardRenderModel();
    const nodes = [node('parent', -5000), { ...node('child', 5200), parentId: 'parent' }, { ...node('hidden'), hidden: true }];
    model.setNodes(nodes); model.setCamera(camera);
    const projected = model.project(nodes);
    expect(projected[0].data.renderLOD).toBe('offscreen');
    expect(projected[1].data.renderLOD).toBe('mid');
    expect(projected[2].data.renderLOD).toBe('offscreen');
    expect(projected[0].hidden).toBeUndefined(); expect(projected[0].style?.display).toBe('none');
    expect(projected[1].position).toEqual(nodes[1].position);
  });
  it('indexes negative coordinates and oversized containers without unbounded cell allocation', () => {
    const index = new CardSpatialIndex();
    index.set('negative', { x: -2050, y: -1, width: 100, height: 100 });
    index.set('large', { x: -1e8, y: -1e8, width: 2e8, height: 2e8 });
    expect([...index.query({ x: -2050, y: 0, width: 1, height: 1 })].sort()).toEqual(['large', 'negative']);
    index.set('negative', { x: 8000, y: 8000, width: 100, height: 100 });
    expect([...index.query({ x: -2050, y: 0, width: 1, height: 1 })]).toEqual(['large']);
  });
  it('reuses displacement for new metadata objects and invalidates for actual geometry', () => {
    const run = createDisplacementCache(), cards = [node('a').data.card, node('b', 250).data.card];
    const levels = new Map([['a', 'workspace' as const], ['b', 'preview' as const]]);
    const obstacles = [{ card: cards[0], level: 'workspace' as const, size: { width: 1020, height: 700 } }];
    const first = run(cards, obstacles, levels);
    expect(run(cards.map(c => ({ ...c, name: 'new name', revision: 4 })), obstacles.map(o => ({ ...o })), new Map(levels))).toBe(first);
    expect(run(cards, [{ ...obstacles[0], size: { width: 1200, height: 700 } }], levels)).not.toBe(first);
    expect(run([{ ...cards[0], position: { x: 1000, y: 0 } }, cards[1]], obstacles, levels)).not.toBe(first);
  });
});
