import { normalizeCard, normalizeEdge } from '../api/client';
import type { RuntimeEvent, WorldCard, WorldEdge } from '../types/world';

type EntityVersion = { revision?: number; created_at?: string };
export type Tombstones = Record<string, EntityVersion>;

export function isOlder(incoming: EntityVersion, current: EntityVersion): boolean {
  if (incoming.created_at && current.created_at && incoming.created_at !== current.created_at) return incoming.created_at < current.created_at;
  return incoming.revision !== undefined && current.revision !== undefined && incoming.revision < current.revision;
}

function wasDeleted(entity: EntityVersion & { id: string }, deleted: Tombstones): boolean {
  const previous = deleted[entity.id];
  if (!previous) return false;
  // A later incarnation restored with the same ID is a new object.
  if (entity.created_at && previous.created_at) return entity.created_at <= previous.created_at;
  return !isOlder(previous, entity);
}

function mergeEntities<T extends EntityVersion & { id: string }>(current: T[], incoming: T[], deleted: Tombstones): T[] {
  const byId = new Map(current.map(entity => [entity.id, entity]));
  for (const entity of incoming) {
    if (!wasDeleted(entity, deleted) && (!byId.has(entity.id) || !isOlder(entity, byId.get(entity.id)!))) byId.set(entity.id, entity);
  }
  return [...byId.values()];
}

export function mergeCards(current: WorldCard[], incoming: WorldCard[], deleted: Tombstones = {}): WorldCard[] {
  return mergeEntities(current, incoming, deleted);
}

export function mergeEdges(current: WorldEdge[], incoming: WorldEdge[], deleted: Tombstones = {}): WorldEdge[] {
  return mergeEntities(current, incoming, deleted);
}

export const eventType = (event: RuntimeEvent) => event.type.replace(/[.\s-]/g, '_').toLowerCase();
const GRAPH_EVENTS = new Set(['card_created', 'card_updated', 'card_deleted', 'edge_created', 'edge_updated', 'edge_deleted']);
export const isGraphEvent = (event: RuntimeEvent) => {
  const type = eventType(event);
  // Edge mutations carry a companion permission notification. Its graph change
  // is already authoritative; keep the notification without rebuilding cards.
  return GRAPH_EVENTS.has(type) || (type === 'permission_changed' && !!event.payload.edge);
};

interface StreamState { eventStream?: string; eventSequence?: number }

export function sequenceEvents(state: StreamState, incoming: RuntimeEvent[]) {
  let { eventStream, eventSequence } = state;
  let gap = false;
  const events: RuntimeEvent[] = [];
  for (const event of incoming) {
    if (event.stream_id && event.sequence !== undefined) {
      const sameStream = eventStream === event.stream_id;
      if (sameStream && eventSequence !== undefined && event.sequence <= eventSequence) continue;
      gap ||= eventStream !== undefined && (!sameStream || (eventSequence !== undefined
        && event.sequence > eventSequence + (eventType(event) === 'connection_ready' ? 0 : 1)));
      eventStream = event.stream_id;
      eventSequence = event.sequence;
    }
    events.push(event);
  }
  return { events, gap, eventStream, eventSequence };
}

interface GraphState {
  cards: WorldCard[];
  stressCards: WorldCard[];
  edges: WorldEdge[];
  cardTombstones: Tombstones;
  edgeTombstones: Tombstones;
  selectedCardIds: string[];
  selectedEdgeId?: string;
  events: RuntimeEvent[];
}

// Reduce a burst in stream order, with one index and one array materialization.
export function applyGraphEvents(state: GraphState, events: RuntimeEvent[]): Partial<GraphState> {
  if (!events.length) return {};
  const cards = new Map(state.cards.map(card => [card.id, card]));
  const edges = new Map(state.edges.map(edge => [edge.id, edge]));
  const cardTombstones = { ...state.cardTombstones };
  const edgeTombstones = { ...state.edgeTombstones };
  let cardsChanged = false, edgesChanged = false;
  const syntheticIds = new Set(state.stressCards.map(card => card.id));
  const selectedIds = new Set(state.selectedCardIds);
  let selectedEdgeId = state.selectedEdgeId;
  let incident: Map<string, Set<string>> | undefined;
  const indexEdge = (edge: WorldEdge) => {
    for (const id of [edge.source, edge.target]) {
      const attached = incident!.get(id) ?? new Set<string>();
      attached.add(edge.id);
      incident!.set(id, attached);
    }
  };
  const removeEdge = (edge: WorldEdge) => {
    edges.delete(edge.id);
    incident?.get(edge.source)?.delete(edge.id);
    incident?.get(edge.target)?.delete(edge.id);
    edgesChanged = true;
    if (selectedEdgeId === edge.id) selectedEdgeId = undefined;
  };
  const removeCard = (id: string, version?: EntityVersion) => {
    if (version) cardTombstones[id] = { revision: version.revision, created_at: version.created_at };
    cardsChanged = cards.delete(id) || cardsChanged;
    if (!syntheticIds.has(id)) selectedIds.delete(id);
    if (!incident) {
      incident = new Map();
      for (const edge of edges.values()) indexEdge(edge);
    }
    for (const edgeId of incident.get(id) ?? []) {
      const edge = edges.get(edgeId)!;
      if (version) edgeTombstones[edge.id] = { revision: edge.revision, created_at: edge.created_at };
      removeEdge(edge);
    }
  };
  const recorded: RuntimeEvent[] = [];
  for (const event of events) {
    const type = eventType(event);
    if (type.startsWith('card_') && event.payload.node) {
      let node: WorldCard;
      try { node = normalizeCard(event.payload.node); }
      catch { continue; } // A malformed message must not discard the rest of a burst.
      const current = cards.get(node.id);
      if (!current || !isOlder(node, current)) {
        if (type === 'card_deleted') removeCard(node.id, node);
        else if (!wasDeleted(node, cardTombstones)) {
          if (current?.config.output !== undefined) node.config.output = current.config.output;
          if (current?.config.active_command !== undefined) node.config.active_command = current.config.active_command;
          cards.set(node.id, node);
          cardsChanged = true;
        }
      }
    } else if (type === 'card_deleted') {
      const id = event.node_id ?? event.agent_id ?? event.sandbox_id ?? event.resource_id;
      if (id) removeCard(id);
    }
    if (type.startsWith('edge_') && event.payload.edge) {
      let edge: WorldEdge;
      try { edge = normalizeEdge(event.payload.edge); }
      catch { continue; }
      const current = edges.get(edge.id);
      if (!current || !isOlder(edge, current)) {
        if (type === 'edge_deleted') {
          edgeTombstones[edge.id] = { revision: edge.revision, created_at: edge.created_at };
          if (current) removeEdge(current);
        } else if (!wasDeleted(edge, edgeTombstones)) {
          if (current) {
            incident?.get(current.source)?.delete(current.id);
            incident?.get(current.target)?.delete(current.id);
          }
          edges.set(edge.id, edge);
          if (incident) indexEdge(edge);
          edgesChanged = true;
        }
      }
    }
    recorded.push(event);
  }
  const selected = state.selectedCardIds.filter(id => selectedIds.has(id) && (cards.has(id) || syntheticIds.has(id)));
  return {
    cards: cardsChanged ? [...cards.values()] : state.cards,
    edges: edgesChanged ? [...edges.values()] : state.edges,
    cardTombstones, edgeTombstones,
    events: [...recorded.slice(-160).reverse(), ...state.events].slice(0, 160),
    selectedCardIds: selected.length === state.selectedCardIds.length ? state.selectedCardIds : selected,
    selectedEdgeId: selectedEdgeId && edges.has(selectedEdgeId) ? selectedEdgeId : undefined,
  };
}
