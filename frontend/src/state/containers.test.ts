import { describe, expect, it } from 'vitest';
import { resizeContainerLayout, memberSurfacePosition, containerSizes, containerDisplayOwners } from './containers';
import { TEST_CATALOG } from './catalog.fixture';
import { buildCardDraft } from './helpers';
import { NODE_SURFACE_SIZE, type NodeSurfaceLevel } from './nodeSurfaces';
import type { WorldCard } from '../types/world';

const card = (id: string, type: string, x = 0, y = 0): WorldCard => ({ id, ...buildCardDraft(type, { x, y }) });
describe('container resize reflow', () => {
  it('represents workspace-only descendants by their container without growing or rearranging it', () => {
    const definition = TEST_CATALOG.node_types.find(type => type.id === 'legion')!;
    const catalog = { ...TEST_CATALOG, node_types: [...TEST_CATALOG.node_types, { ...definition, id: 'graph', frontend: { workspace: 'graph' }, container: { ...definition.container!, member_display: 'workspace' as const } }] };
    const parent = { ...card('parent', 'graph'), size: { width: 1000, height: 650 } };
    const nested = { ...card('nested', 'legion', 3000, 5000), parent_id: parent.id };
    const member = { ...card('member', 'text', 7000, 8000), parent_id: nested.id };
    const world = [member, nested, parent];
    const owners = containerDisplayOwners(world, catalog, { parent: 'preview' });
    expect(owners).toEqual(new Map([['nested', parent.id], ['member', parent.id]]));
    expect(containerSizes(world, catalog, new Map()).get(parent.id)).toEqual(parent.size);
    const resized = resizeContainerLayout(world, catalog, new Map(), parent.id, { width: 900, height: 600 });
    expect(resized.size).toEqual({ width: 900, height: 600 });
    expect(resized.positions.size).toBe(0);
    expect(containerDisplayOwners(world, TEST_CATALOG, {}).size).toBe(0);
  });
  it('wraps real preview and inspector rectangles within insets without overlap', () => {
    const parent = { ...card('parent', 'legion', 200, 100), size: { width: 1400, height: 800 } };
    const members = Array.from({ length: 6 }, (_, i) => ({ ...card(`m${i}`, 'text', 600 + i * 300, 250), parent_id: parent.id }));
    const levels = new Map<string, NodeSurfaceLevel>(members.map(m => [m.id, 'preview'])); levels.set('m2', 'inspector');
    const layout = resizeContainerLayout([parent, ...members], TEST_CATALOG, levels, parent.id, { width: 1000, height: 550 });
    const boxes = members.map(m => ({ ...memberSurfacePosition({ ...m, position: layout.positions.get(m.id)! }, parent, levels.get(m.id)!, TEST_CATALOG), ...NODE_SURFACE_SIZE[levels.get(m.id)!] }));
    for (const b of boxes) {
      expect(b.x).toBeGreaterThanOrEqual(parent.position.x + 320);
      expect(b.y).toBeGreaterThanOrEqual(parent.position.y + 100);
      expect(b.x + b.width).toBeLessThanOrEqual(parent.position.x + layout.size.width - 24);
      expect(b.y + b.height).toBeLessThanOrEqual(parent.position.y + layout.size.height - 24);
    }
    for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
      const a = boxes[i], b = boxes[j];
      expect(a.x + a.width <= b.x || b.x + b.width <= a.x || a.y + a.height <= b.y || b.y + b.height <= a.y).toBe(true);
    }
    const wider = resizeContainerLayout([parent, ...members], TEST_CATALOG, levels, parent.id, { width: 2000, height: 550 });
    expect(wider.size.height).toBeLessThan(layout.size.height);
  });

  it('moves nested descendants by the same delta and leaves external cards alone', () => {
    const parent = card('parent', 'legion');
    const nested = { ...card('nested', 'legion', 900, 500), parent_id: parent.id, size: { width: 900, height: 700 } };
    const child = { ...card('child', 'text', 1250, 650), parent_id: nested.id };
    const external = card('external', 'text', 3000, 3000);
    const layout = resizeContainerLayout([parent, nested, child, external], TEST_CATALOG, new Map(), parent.id, { width: 800, height: 550 });
    expect(layout.positions.get(child.id)!.x - layout.positions.get(nested.id)!.x).toBe(child.position.x - nested.position.x);
    expect(layout.positions.get(child.id)!.y - layout.positions.get(nested.id)!.y).toBe(child.position.y - nested.position.y);
    expect(layout.positions.has(external.id)).toBe(false);
  });
});
