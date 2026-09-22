import { describe, expect, it } from 'vitest';
import { containerContentBounds, resizeContainerLayout, memberSurfacePosition, containerSizes, containerDisplayOwners, ownedCardIds } from './containers';
import { TEST_CATALOG } from './catalog.fixture';
import { buildCardDraft } from './helpers';
import { NODE_SURFACE_SIZE, type NodeSurfaceLevel } from './nodeSurfaces';
import type { WorldCard } from '../types/world';

const card = (id: string, type: string, x = 0, y = 0): WorldCard => ({ id, ...buildCardDraft(type, { x, y }) });

it('expands overlapping roots, nested members and equipment without selecting external peers', () => {
  const parent = card('parent', 'legion');
  const agent = { ...card('agent', 'agent'), parent_id: parent.id };
  const equipment = { ...card('equipment', 'text'), equipment: { owner_id: agent.id, relationship: 'read' } };
  const nested = { ...card('nested', 'text'), parent_id: equipment.id };
  expect(ownedCardIds([parent, agent, equipment, nested, card('external', 'text')], [parent.id, agent.id]))
    .toEqual(new Set([parent.id, agent.id, equipment.id, nested.id]));
});
describe('container resize reflow', () => {
  it('preserves clamped expanded member placement when the frame origin moves', () => {
    const parent = { ...card('parent', 'legion', 200, 100), size: { width: 1200, height: 1000 } };
    const member = { ...card('member', 'text', 250, 200), parent_id: parent.id };
    const levels = new Map<string, NodeSurfaceLevel>([[member.id, 'inspector']]);
    const before = memberSurfacePosition(member, parent, 'inspector', TEST_CATALOG);
    const position = { x: 100, y: 0 };
    const layout = resizeContainerLayout([parent, member], TEST_CATALOG, levels, parent.id, { width: 1300, height: 1100 }, {}, position);
    const after = memberSurfacePosition({ ...member, position: layout.positions.get(member.id)! }, { ...parent, position }, 'inspector', TEST_CATALOG);
    expect(after).toEqual(before);
  });
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
  it('preserves manual member positions when shrinking and enlarging the frame', () => {
    const parent = { ...card('parent', 'legion', 200, 100), size: { width: 2400, height: 1800 } };
    const members = [
      { ...card('a', 'text', 750, 380), parent_id: parent.id },
      { ...card('b', 'text', 1300, 950), parent_id: parent.id },
    ];
    const levels = new Map<string, NodeSurfaceLevel>([['a', 'preview'], ['b', 'inspector']]);
    const world = [parent, ...members];
    const original = structuredClone(world);
    const layout = resizeContainerLayout(world, TEST_CATALOG, levels, parent.id, { width: 800, height: 550 });
    expect(layout.positions.size).toBe(0);
    expect(layout.size.width).toBeLessThan(parent.size.width);
    expect(layout.size.height).toBeLessThan(parent.size.height);
    for (const member of members) {
      const level = levels.get(member.id)!;
      const surface = memberSurfacePosition(member, parent, level, TEST_CATALOG);
      expect(surface.x + NODE_SURFACE_SIZE[level].width + 24).toBeLessThanOrEqual(parent.position.x + layout.size.width);
      expect(surface.y + NODE_SURFACE_SIZE[level].height + 24).toBeLessThanOrEqual(parent.position.y + layout.size.height);
    }
    const wider = resizeContainerLayout(world, TEST_CATALOG, levels, parent.id, { width: 3000, height: 2200 });
    expect(wider.size).toEqual({ width: 3000, height: 2200 });
    expect(wider.positions.size).toBe(0);
    expect(world).toEqual(original);
  });

  it('preserves nested descendants and includes custom workspace bounds', () => {
    const parent = card('parent', 'legion');
    const nested = { ...card('nested', 'legion', 900, 500), parent_id: parent.id, size: { width: 900, height: 700 } };
    const child = { ...card('child', 'text', 1250, 650), parent_id: nested.id };
    const workspace = { ...card('workspace', 'agent', 2200, 1500), parent_id: parent.id };
    const external = card('external', 'text', 9000, 9000);
    const world = [parent, nested, child, workspace, external];
    const levels = new Map<string, NodeSurfaceLevel>([['workspace', 'workspace']]);
    const sizes = { workspace: { workspace: { width: 1600, height: 1200 } } };
    const layout = resizeContainerLayout(world, TEST_CATALOG, levels, parent.id, { width: 800, height: 550 }, sizes);
    expect(layout.positions.size).toBe(0);
    const surface = memberSurfacePosition(workspace, parent, 'workspace', TEST_CATALOG);
    expect(layout.size.width).toBe(surface.x + 1600 + 24);
    expect(layout.size.height).toBe(surface.y + 1200 + 24);
  });
});

it('places header space outside preview, inspector and custom workspace content bounds', () => {
  for (const level of ['preview', 'inspector', 'workspace'] as const) {
    const member = card('member', 'text', 600, 400);
    const levels = new Map<string, NodeSurfaceLevel>([[member.id, level]]);
    const workspaces = { member: { workspace: { width: 1400, height: 900 } } };
    const bounds = containerContentBounds([member], TEST_CATALOG, levels, workspaces)!;
    const spec = TEST_CATALOG.node_types.find(type => type.id === 'legion')!.container!;
    const parent = card('parent', 'legion', bounds.position.x - spec.content_inset[0], bounds.position.y - spec.content_inset[1]);
    expect(memberSurfacePosition(member, parent, level, TEST_CATALOG)).toEqual(bounds.position);
    expect(bounds.size).toEqual(level === 'workspace' ? workspaces.member.workspace : NODE_SURFACE_SIZE[level]);
  }
});
