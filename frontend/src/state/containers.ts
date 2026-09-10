import type { PluginCatalog, WorldCard, WorldPosition, WorldSize } from "../types/world";
import { NODE_SURFACE_SIZE, type NodeSurfaceLevel, type SurfaceSize } from "./nodeSurfaces";
import { nodePositionFromSurfacePosition, positionSurfaceAtNodeCenter } from "../canvas/nodeDisplacement";

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

export function containerSizes(cards: WorldCard[], catalog: PluginCatalog, levels: Map<string, NodeSurfaceLevel>, workspaceSizes: Record<string, SurfaceSize> = {}) {
  const sizes = new Map<string, WorldSize>();
  for (const card of parentFirst(cards).reverse()) {
    const spec = containerDefinition(card, catalog);
    if (!spec) continue;
    const size = { width: Math.max(card.size.width, spec.min_size[0]), height: Math.max(card.size.height, spec.min_size[1]) };
    if (containerShowsWorkspace(card, catalog, levels.get(card.id))) { sizes.set(card.id, size); continue; }
    for (const member of cards.filter((node) => node.parent_id === card.id)) {
      const level = levels.get(member.id) ?? "preview";
      const nested = sizes.get(member.id);
      const memberSize = nested ?? (level === "workspace" ? workspaceSizes[member.id] : undefined) ?? NODE_SURFACE_SIZE[level];
      const position = nested ? member.position : memberSurfacePosition(member, card, level, catalog);
      size.width = Math.max(size.width, position.x - card.position.x + memberSize.width + spec.content_inset[2]);
      size.height = Math.max(size.height, position.y - card.position.y + memberSize.height + spec.content_inset[3]);
    }
    sizes.set(card.id, size);
  }
  return sizes;
}

/** Reflow visible member surfaces on resize, keeping nested frames and their contents intact. */
export function resizeContainerLayout(cards: WorldCard[], catalog: PluginCatalog, levels: Map<string, NodeSurfaceLevel>, id: string, requested: WorldSize, workspaceSizes: Record<string, SurfaceSize> = {}) {
  const parent = cards.find(card => card.id === id)!;
  const spec = containerDefinition(parent, catalog)!;
  const [left, top, right, bottom] = spec.content_inset;
  if (containerShowsWorkspace(parent, catalog, levels.get(id))) return {
    size: { width: Math.max(requested.width, spec.min_size[0]), height: Math.max(requested.height, spec.min_size[1]) },
    positions: new Map<string, WorldPosition>(),
  };
  const sizes = containerSizes(cards, catalog, levels, workspaceSizes);
  const members = cards.filter(card => card.parent_id === id).sort((a, b) => a.position.y - b.position.y || a.position.x - b.position.x || a.id.localeCompare(b.id));
  const memberSize = (card: WorldCard) => sizes.get(card.id) ?? (levels.get(card.id) === 'workspace' ? workspaceSizes[card.id] : undefined) ?? NODE_SURFACE_SIZE[levels.get(card.id) ?? 'preview'];
  const width = Math.max(requested.width, spec.min_size[0], ...members.map(card => left + memberSize(card).width + right));
  const positions = new Map<string, WorldPosition>();
  let x = left, y = top, rowHeight = 0;
  for (const member of members) {
    const size = memberSize(member);
    if (x > left && x + size.width + right > width) { x = left; y += rowHeight + 24; rowHeight = 0; }
    const surface = { x: parent.position.x + x, y: parent.position.y + y };
    const position = isContainer(member, catalog) ? surface : nodePositionFromSurfacePosition(surface, levels.get(member.id) ?? 'preview');
    positions.set(member.id, position);
    for (const child of ownedDescendants(cards, member.id)) positions.set(child.id, {
      x: child.position.x + position.x - member.position.x,
      y: child.position.y + position.y - member.position.y,
    });
    x += size.width + 24;
    rowHeight = Math.max(rowHeight, size.height);
  }
  return { size: { width, height: Math.max(requested.height, spec.min_size[1], y + rowHeight + bottom) }, positions };
}

/** Deepest eligible space wins; smaller spaces win when unrelated frames overlap. */
export function dropContainer(cards: WorldCard[], member: WorldCard, point: WorldPosition, catalog: PluginCatalog, sizes = new Map<string, WorldSize>(), accepts = acceptsMember) {
  return cards.filter((card) => {
    if (!containerDefinition(card, catalog) || !accepts(card, member, catalog, cards)) return false;
    const size = sizes.get(card.id) ?? card.size;
    // Membership follows the visible frame; insets only govern member layout.
    // Use the same boundary for the drag hint and the final drop.
    return point.x >= card.position.x && point.x <= card.position.x + size.width
      && point.y >= card.position.y && point.y <= card.position.y + size.height;
  }).sort((a, b) => ancestors(cards, b).length - ancestors(cards, a).length || a.size.width * a.size.height - b.size.width * b.size.height)[0];
}
