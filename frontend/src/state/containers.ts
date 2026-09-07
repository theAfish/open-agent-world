import type { PluginCatalog, WorldCard, WorldPosition, WorldSize } from "../types/world";
import { NODE_SURFACE_SIZE, type NodeSurfaceLevel } from "./nodeSurfaces";
import { positionSurfaceAtNodeCenter } from "../canvas/nodeDisplacement";

export const containerDefinition = (card: WorldCard, catalog: PluginCatalog) => catalog.node_types.find((type) => type.id === card.type)?.container;
export const isContainer = (card: WorldCard, catalog: PluginCatalog) => containerDefinition(card, catalog) != null;

/** Expanded member surfaces stay below the header and inside the left border. */
export function memberSurfacePosition(card: WorldCard, parent: WorldCard, level: NodeSurfaceLevel, catalog: PluginCatalog) {
  const position = positionSurfaceAtNodeCenter(card.position, level);
  const [left, top] = containerDefinition(parent, catalog)!.content_inset;
  return { x: Math.max(position.x, parent.position.x + left), y: Math.max(position.y, parent.position.y + top) };
}
export function descendants(cards: WorldCard[], id: string): WorldCard[] {
  return cards.filter((card) => card.parent_id === id).flatMap((card) => [card, ...descendants(cards, card.id)]);
}
export function ownedDescendants(cards: WorldCard[], id: string): WorldCard[] {
  return cards.filter((c) => c.parent_id === id || c.equipment?.owner_id === id).flatMap((c) => [c, ...ownedDescendants(cards, c.id)]);
}
export function ancestors(cards: WorldCard[], card: WorldCard): WorldCard[] {
  const parent = cards.find((candidate) => candidate.id === (card.equipment?.owner_id ?? card.parent_id));
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

export function containerSizes(cards: WorldCard[], catalog: PluginCatalog, levels: Map<string, NodeSurfaceLevel>) {
  const sizes = new Map<string, WorldSize>();
  for (const card of parentFirst(cards).reverse()) {
    const spec = containerDefinition(card, catalog);
    if (!spec) continue;
    const size = { width: Math.max(card.size.width, spec.min_size[0]), height: Math.max(card.size.height, spec.min_size[1]) };
    for (const member of cards.filter((node) => node.parent_id === card.id)) {
      const level = levels.get(member.id) ?? "preview";
      const nested = sizes.get(member.id);
      const memberSize = nested ?? NODE_SURFACE_SIZE[level];
      const position = nested ? member.position : memberSurfacePosition(member, card, level, catalog);
      size.width = Math.max(size.width, position.x - card.position.x + memberSize.width + spec.content_inset[2]);
      size.height = Math.max(size.height, position.y - card.position.y + memberSize.height + spec.content_inset[3]);
    }
    sizes.set(card.id, size);
  }
  return sizes;
}

/** Deepest eligible space wins; smaller spaces win when unrelated frames overlap. */
export function dropContainer(cards: WorldCard[], member: WorldCard, point: WorldPosition, catalog: PluginCatalog, sizes = new Map<string, WorldSize>(), accepts = acceptsMember) {
  return cards.filter((card) => {
    if (!containerDefinition(card, catalog) || !accepts(card, member, catalog, cards)) return false;
    const [left, top, right, bottom] = containerDefinition(card, catalog)!.content_inset;
    const size = sizes.get(card.id) ?? card.size;
    return point.x >= card.position.x + left && point.x <= card.position.x + size.width - right
      && point.y >= card.position.y + top && point.y <= card.position.y + size.height - bottom;
  }).sort((a, b) => ancestors(cards, b).length - ancestors(cards, a).length || a.size.width * a.size.height - b.size.width * b.size.height)[0];
}
