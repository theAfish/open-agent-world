import type { PluginCatalog, WorldCard, WorldPosition, WorldSize } from "../types/world";
import { cardIndex } from './cardIndex';
import { surfaceSizeFor, type NodeSurfaceLevel, type SurfaceSizes } from "./nodeSurfaces";
import { nodePositionFromSurfacePosition, positionSurfaceAtNodeCenter } from "../canvas/nodeDisplacement";
import { isShadow, shadowLayout, insideShadow } from "./shadowCollection";

export const containerDefinition = (card: WorldCard, catalog: PluginCatalog) => catalog.node_types.find((type) => type.id === card.type)?.container;
export const isContainer = (card: WorldCard, catalog: PluginCatalog) => containerDefinition(card, catalog) != null;

export function containerShowsWorkspace(card: WorldCard, catalog: PluginCatalog, level?: NodeSurfaceLevel) {
  const definition = catalog.node_types.find(type => type.id === card.type);
  return !!definition?.frontend?.workspace && (definition.container?.member_display === 'workspace' || level === 'workspace');
}

/** Resolve once per world update, including members outside the current chunks. */
export function containerDisplayOwners(cards: WorldCard[], catalog: PluginCatalog, levels: Readonly<Record<string, NodeSurfaceLevel>>) {
  const byId = new Map(cards.map(card => [card.id, card]));
  const resolved = new Map<string, string | undefined>();
  const visit = (card: WorldCard): string | undefined => {
    if (resolved.has(card.id)) return resolved.get(card.id);
    const parent = byId.get(card.equipment?.owner_id ?? card.parent_id ?? '');
    const owner = parent ? visit(parent) ?? (containerShowsWorkspace(parent, catalog, levels[parent.id]) ? parent.id : undefined) : undefined;
    resolved.set(card.id, owner);
    return owner;
  };
  cards.forEach(visit);
  return new Map([...resolved].filter((entry): entry is [string, string] => entry[1] !== undefined));
}

/** Content bounds exclude the header; formation adds container insets outside them. */
export function containerContentBounds(cards: WorldCard[], catalog: PluginCatalog, levels: Map<string, NodeSurfaceLevel>, surfaceSizes: SurfaceSizes = {}) {
  const sizes = containerSizes(cards, catalog, levels, surfaceSizes);
  const surfaces = cards.filter(card => !card.equipment).map(card => {
    const level = levels.get(card.id) ?? 'preview';
    const size = sizes.get(card.id) ?? surfaceSizeFor(card.id, level, surfaceSizes);
    const position = isContainer(card, catalog) ? card.position : positionSurfaceAtNodeCenter(card.position, level);
    return { ...position, ...size };
  });
  if (!surfaces.length) return undefined;
  const x = Math.min(...surfaces.map(surface => surface.x));
  const y = Math.min(...surfaces.map(surface => surface.y));
  return { position: { x, y }, size: {
    width: Math.max(...surfaces.map(surface => surface.x + surface.width)) - x,
    height: Math.max(...surfaces.map(surface => surface.y + surface.height)) - y,
  } };
}

/** Expanded member surfaces stay below the header and inside the left border. */
export function memberSurfacePosition(card: WorldCard, parent: WorldCard, level: NodeSurfaceLevel, catalog: PluginCatalog) {
  const position = positionSurfaceAtNodeCenter(card.position, level);
  if(isShadow(parent)) return position;
  const [left, top] = containerDefinition(parent, catalog)!.content_inset;
  return { x: Math.max(position.x, parent.position.x + left), y: Math.max(position.y, parent.position.y + top) };
}
export function descendants(cards: WorldCard[], id: string): WorldCard[] {
  return cards.filter((card) => card.parent_id === id).flatMap((card) => [card, ...descendants(cards, card.id)]);
}
export function ownedDescendants(cards: WorldCard[], id: string): WorldCard[] {
  return cards.filter((c) => c.parent_id === id || c.equipment?.owner_id === id).flatMap((c) => [c, ...ownedDescendants(cards, c.id)]);
}
// Expand many roots together: build ownership once and visit each member once.
export function ownedCardIds(cards: WorldCard[], roots: Iterable<string>): Set<string> {
  const children = new Map<string, string[]>();
  for (const card of cards) {
    for (const owner of new Set([card.parent_id, card.equipment?.owner_id])) {
      if (!owner) continue;
      const members = children.get(owner) ?? [];
      members.push(card.id);
      children.set(owner, members);
    }
  }
  const ids = new Set(roots);
  const pending = [...ids];
  while (pending.length) {
    for (const child of children.get(pending.pop()!) ?? []) {
      if (ids.has(child)) continue;
      ids.add(child);
      pending.push(child);
    }
  }
  return ids;
}
export function ancestors(cards: WorldCard[], card: WorldCard): WorldCard[] {
  const parentId = card.equipment?.owner_id ?? card.parent_id;
  const parent = parentId ? cardIndex(cards).get(parentId) : undefined;
  return parent ? [parent, ...ancestors(cards, parent)] : [];
}
export function parentFirst<T extends WorldCard>(cards: T[]): T[] {
  return [...cards].sort((a, b) => ancestors(cards, a).length - ancestors(cards, b).length);
}
export function acceptsMember(container: WorldCard, member: WorldCard, catalog: PluginCatalog, cards: WorldCard[] = []) {
  const spec = containerDefinition(container, catalog);
  if (!spec || member.equipment || member.ephemeral || member.id === container.id || containerDefinition(member, catalog)?.parentable === false) return false;
  if (ancestors(cards, container).some((parent) => parent.id === member.id)) return false;
  const traits = catalog.node_types.find((type) => type.id === member.type)?.traits ?? [];
  return spec.member_traits.every((trait) => traits.includes(trait));
}

export function containerSizes(cards: WorldCard[], catalog: PluginCatalog, levels: Map<string, NodeSurfaceLevel>, surfaceSizes: SurfaceSizes = {}) {
  const sizes = new Map<string, WorldSize>();
  for (const card of parentFirst(cards).reverse()) {
    const spec = containerDefinition(card, catalog);
    if (!spec) continue;
    if(isShadow(card)){const layout=shadowLayout(card,cards,levels,catalog,surfaceSizes);sizes.set(card.id,{width:layout.width,height:layout.height});continue;}
    const size = { width: Math.max(card.size.width, spec.min_size[0]), height: Math.max(card.size.height, spec.min_size[1]) };
    if (containerShowsWorkspace(card, catalog, levels.get(card.id))) { sizes.set(card.id, size); continue; }
    for (const member of cards.filter((node) => node.parent_id === card.id)) {
      const level = levels.get(member.id) ?? "preview";
      const nested = sizes.get(member.id);
      const memberSize = nested ?? surfaceSizeFor(member.id, level, surfaceSizes);
      const position = nested ? member.position : memberSurfacePosition(member, card, level, catalog);
      size.width = Math.max(size.width, position.x - card.position.x + memberSize.width + spec.content_inset[2]);
      size.height = Math.max(size.height, position.y - card.position.y + memberSize.height + spec.content_inset[3]);
    }
    sizes.set(card.id, size);
  }
  return sizes;
}

/** Resize the frame around the current layout without rearranging its members. */
export function resizeContainerLayout(cards: WorldCard[], catalog: PluginCatalog, levels: Map<string, NodeSurfaceLevel>, id: string, requested: WorldSize, surfaceSizes: SurfaceSizes = {}, position?: WorldPosition) {
  const parent = cards.find(card => card.id === id)!;
  const positions = new Map<string, WorldPosition>();
  // Expanded members can be visually clamped under the old header. Preserve
  // that visible placement when the frame's origin changes, without reflow.
  if (position && !containerShowsWorkspace(parent, catalog, levels.get(id))) {
    const moved = { ...parent, position };
    for (const member of cards.filter(card => card.parent_id === id && !isContainer(card, catalog))) {
      const level = levels.get(member.id) ?? 'preview';
      const before = memberSurfacePosition(member, parent, level, catalog);
      const after = memberSurfacePosition(member, moved, level, catalog);
      if (before.x !== after.x || before.y !== after.y) positions.set(member.id, nodePositionFromSurfacePosition(before, level));
    }
  }
  const resized = cards.map(card => card.id === id ? { ...card, size: requested, position: position ?? card.position }
    : positions.has(card.id) ? { ...card, position: positions.get(card.id)! } : card);
  return {
    size: containerSizes(resized, catalog, levels, surfaceSizes).get(id)!,
    positions,
  };
}

/** Deepest eligible space wins; smaller spaces win when unrelated frames overlap. */
export function dropContainer(cards: WorldCard[], member: WorldCard, point: WorldPosition, catalog: PluginCatalog, sizes = new Map<string, WorldSize>(), accepts = acceptsMember) {
  return cards.filter((card) => {
    if (!containerDefinition(card, catalog) || !accepts(card, member, catalog, cards)) return false;
    const size = sizes.get(card.id) ?? card.size;
    if(isShadow(card)) {
      const rect=shadowLayout(card,cards,undefined,catalog);
      return insideShadow(point.x,point.y,rect);
    }
    // Membership follows the visible frame; insets only govern member layout.
    // Use the same boundary for the drag hint and the final drop.
    return point.x >= card.position.x && point.x <= card.position.x + size.width
      && point.y >= card.position.y && point.y <= card.position.y + size.height;
  }).sort((a, b) => ancestors(cards, b).length - ancestors(cards, a).length || a.size.width * a.size.height - b.size.width * b.size.height)[0];
}
