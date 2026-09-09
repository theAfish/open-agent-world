import { create } from "zustand";
import { useGenerationStore } from "../effects/generation";
import { persist } from "zustand/middleware";
import { apiErrorMessage, normalizeCard, resourceContentUrl, worldApi, type CardCreateInput } from "../api/client";
import type {
  CardType,
  EdgeDirection,
  FlowViewportState,
  LegionInstantiation,
  LegionSummary,
  PluginCatalog,
  Relationship,
  RuntimeEvent,
  SandboxConfig,
  SandboxInfo,
  SandboxRuntimeCatalog,
  ToastMessage,
  WorldCard,
  WorldEdge,
  WorldPosition,
  WorldSnapshot,
} from "../types/world";
import { getViewportChunkKeys, viewportCenterToWorld } from "./chunks";
import { EMPTY_CATALOG, getNodeType } from "./catalog";
import { buildCardDraft, makeStressCards, mergeCardPatch } from "./helpers";
import { summarizeLegionSelection } from "./legions";
import { ancestors, containerDefinition, descendants, ownedDescendants, isContainer, parentFirst } from "./containers";
import { isEquipmentConnection } from "./equipment";
import { validateConnection, type RelationshipOption } from "./relationships";
import { describeRuntimeError } from "./runtimeErrors";
import {
  normalizeModelList,
  persistModelSettings,
  readModelSettings,
  type ModelSettings,
} from "./modelSettings";

export type SyncState = "loading" | "online" | "syncing" | "offline";
export type SocketState = "connecting" | "live" | "closed";
type SandboxOperation = "saving" | "starting" | "stopping" | "executing";

export interface PendingConnection {
  source: string;
  target: string;
  options: RelationshipOption[];
}

type RestorableCard = WorldCard & {
  restoreDocument?: Record<string, unknown>;
  restoreLegionState?: Record<string, unknown>;
  restoreContent?: string;
  restoreImageData?: string;
  restoreImageMediaType?: string;
};

export type WorldHistoryOperation =
  | { id: number; label: string; kind: "group-dissolved"; group: RestorableCard; members: WorldCard[]; edges: WorldEdge[] }
  | { id: number; label: string; kind: "group-formed"; group: RestorableCard; before: WorldCard[]; after: WorldCard[] }
  | { id: number; label: string; kind: "card-created"; cards: RestorableCard[] }
  | { id: number; label: string; kind: "cards-deleted"; cards: RestorableCard[]; edges: WorldEdge[] }
  | { id: number; label: string; kind: "card-updated"; before: WorldCard; after: WorldCard }
  | { id: number; label: string; kind: "cards-updated"; before: WorldCard[]; after: WorldCard[]; membership?: boolean }
  | {
      id: number;
      label: string;
      kind: "legion-instantiated";
      legionId: string;
      position: WorldPosition;
      cards: WorldCard[];
      edges: WorldEdge[];
    }
  | { id: number; label: string; kind: "edge-created"; edge: WorldEdge }
  | { id: number; label: string; kind: "edge-deleted"; edge: WorldEdge }
  | { id: number; label: string; kind: "edge-updated"; before: WorldEdge; after: WorldEdge };

const HISTORY_LIMIT = 20;
const RESOURCE_HISTORY_LIMIT_BYTES = 4 * 1024 * 1024;

function copyCard(card: WorldCard): RestorableCard {
  return {
    ...card,
    position: { ...card.position },
    size: { ...card.size },
    config: { ...card.config },
  };
}

function copyEdge(edge: WorldEdge): WorldEdge {
  return { ...edge };
}

function cardRestorePatch(card: WorldCard): Partial<Omit<WorldCard, "id" | "type">> {
  return {
    name: card.name,
    parent_id: card.parent_id ?? null,
    equipment: card.equipment ?? null,
    position: { ...card.position },
    size: { ...card.size },
    expanded: card.expanded,
    status: card.status,
    config: { ...card.config },
  };
}

async function applyCardPositions(cards: WorldCard[], membership = false): Promise<WorldCard[]> {
  const persistent = cards.filter((card) => !card.ephemeral);
  const authoritative = persistent.length > 0
    ? await worldApi.batchUpdateNodes(persistent.map((card) => ({
        node_id: card.id,
        patch: { position: { ...card.position }, ...(membership ? { parent_id: card.parent_id ?? null } : {}) },
      })))
    : [];
  const authoritativeById = new Map(authoritative.map((card) => [card.id, card]));
  const missing = persistent.filter((card) => !authoritativeById.has(card.id));
  if (missing.length > 0) {
    throw new Error(`Batch response omitted ${missing.map((card) => card.id).join(", ")}.`);
  }
  return cards.map((card) => (
    card.ephemeral ? copyCard(card) : authoritativeById.get(card.id)!
  ));
}

function restoreCardInput(card: RestorableCard): CardCreateInput {
  return {
    ...copyCard(card),
    ...(card.restoreContent !== undefined ? { content: card.restoreContent } : {}),
    ...(card.restoreImageData !== undefined
      ? { data_base64: card.restoreImageData, media_type: card.restoreImageMediaType }
      : {}),
  };
}

async function restoreCard(card: RestorableCard): Promise<WorldCard> {
  const restored = await worldApi.restoreNode(restoreCardInput(card));
  if (card.restoreDocument) {
    try {
      const current = await worldApi.getNodeDocument(restored.id);
      await worldApi.nodeDocumentAction(restored.id, "replace", card.restoreDocument, current.revision);
    }
    catch (error) { await worldApi.deleteNode(restored.id); throw error; }
  }
  if (card.type === "legion" && card.restoreLegionState) {
    try { await worldApi.saveLegionState(restored.id, card.restoreLegionState, 0); }
    catch (error) { await worldApi.deleteNode(restored.id); throw error; }
  }
  return restored;
}

async function snapshotCardForHistory(card: WorldCard): Promise<RestorableCard> {
  const snapshot = copyCard(card);
  if (useWorldStore.getState().catalog.node_types.find((definition) => definition.id === card.type)?.has_document) {
    snapshot.restoreDocument = (await worldApi.getNodeDocument(card.id)).value;
    const field = useWorldStore.getState().catalog.node_types.find((definition) => definition.id === card.type)?.container?.document_field;
    if (field) snapshot.restoreDocument[field] = [];
  }
  try {
    if (card.type === "legion") snapshot.restoreLegionState = (await worldApi.getLegionState(card.id)).value;
    if (card.type === "text") {
      const content = await worldApi.getTextContent(card.id);
      if (new TextEncoder().encode(content).byteLength <= RESOURCE_HISTORY_LIMIT_BYTES) {
        snapshot.restoreContent = content;
      }
    }
    if (
      card.type === "image"
      && Number(card.config.revision ?? 0) > 0
      && Number(card.config.bytes ?? 0) <= RESOURCE_HISTORY_LIMIT_BYTES
    ) {
      const image = await worldApi.getImageRestoreData(card.id);
      snapshot.restoreImageData = image.data_base64;
      snapshot.restoreImageMediaType = image.media_type;
    }
  } catch {
    // A stale resource must not prevent removal; its card configuration is still restorable.
  }
  return snapshot;
}

function appendHistory(
  history: WorldHistoryOperation[],
  operation: WorldHistoryOperation,
): WorldHistoryOperation[] {
  return [...history, operation].slice(-HISTORY_LIMIT);
}

const INITIAL_VIEWPORT: FlowViewportState = {
  x: 0,
  y: 0,
  zoom: 1,
  width: typeof window === "undefined" ? 1280 : window.innerWidth,
  height: typeof window === "undefined" ? 800 : window.innerHeight,
};

function preferredTheme(): "light" | "dark" {
  if (typeof window === "undefined") return "light";
  const saved = window.localStorage.getItem("oaw-theme");
  if (saved === "dark" || saved === "light") return saved;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function mergeCards(current: WorldCard[], incoming: WorldCard[]): WorldCard[] {
  const byId = new Map(current.map((card) => [card.id, card]));
  for (const card of incoming) byId.set(card.id, card);
  return [...byId.values()];
}

export function mergeEdges(current: WorldEdge[], incoming: WorldEdge[]): WorldEdge[] {
  const byId = new Map(current.map((edge) => [edge.id, edge]));
  for (const edge of incoming) byId.set(edge.id, edge);
  return [...byId.values()];
}

export function mergeLegions(current: LegionSummary[], incoming: LegionSummary[]): LegionSummary[] {
  const byId = new Map(current.map((legion) => [legion.id, legion]));
  for (const legion of incoming) byId.set(legion.id, legion);
  return [...byId.values()];
}

type LegionLibraryResult =
  | { ok: true; legions: LegionSummary[] }
  | { ok: false; error: unknown };

async function loadLegionLibrary(): Promise<LegionLibraryResult> {
  try {
    return { ok: true, legions: await worldApi.getLegions() };
  } catch (error) {
    return { ok: false, error };
  }
}

function samePosition(first: WorldPosition, second: WorldPosition): boolean {
  return first.x === second.x && first.y === second.y;
}

function chunkKeysFromSnapshot(snapshot: WorldSnapshot): string[] {
  return snapshot.chunks.flatMap((chunk) => {
    if (typeof chunk === "string") return [chunk];
    if (Array.isArray(chunk)) return [`${chunk[0]}:${chunk[1]}`];
    if (chunk.key) return [chunk.key];
    return [`${chunk.x}:${chunk.y}`];
  });
}

interface WorldState {
  cards: WorldCard[];
  catalog: PluginCatalog;
  edges: WorldEdge[];
  legions: LegionSummary[];
  legionError?: string;
  stressCards: WorldCard[];
  activeChunkKeys: string[];
  loadedChunkKeys: string[];
  loadingChunkKeys: string[];
  viewport: FlowViewportState;
  syncState: SyncState;
  syncError?: string;
  socketState: SocketState;
  selectedEdgeId?: string;
  selectedCardIds: string[];
  selectionRevision: number;
  pendingConnection?: PendingConnection;
  events: RuntimeEvent[];
  activityOpen: boolean;
  settingsOpen: boolean;
  modelSettings: ModelSettings;
  paletteCollapsed: boolean;
  theme: "light" | "dark";
  toasts: ToastMessage[];
  undoStack: WorldHistoryOperation[];
  redoStack: WorldHistoryOperation[];
  historyBusy: boolean;
  positionCommitBusy: boolean;
  sandboxInfo: Record<string, SandboxInfo | undefined>;
  sandboxBusy: Record<string, SandboxOperation | undefined>;
  sandboxErrors: Record<string, string | undefined>;
  sandboxRevisions: Record<string, number>;
  sandboxRuntimes?: SandboxRuntimeCatalog;
  sandboxRuntimesLoading: boolean;
  sandboxRuntimesError?: string;

  initialize: () => Promise<void>;
  refreshWorld: () => Promise<void>;
  ensureChunks: (keys: string[]) => Promise<void>;
  setViewport: (viewport: FlowViewportState) => void;
  formLegionGroup: (nodeIds: string[]) => Promise<void>;
  setContainerMembership: (ids: string[], parentId: string | null) => Promise<void>;
  createCard: (type: CardType, position?: WorldPosition, placement?: Pick<WorldCard, "equipment" | "parent_id">) => Promise<WorldCard | undefined>;
  updateCard: (
    id: string,
    patch: Partial<Omit<WorldCard, "id" | "type">>,
  ) => Promise<void>;
  updateCardPositions: (updates: Array<{ id: string; position: WorldPosition; parent_id?: string | null }>) => Promise<void>;
  waitForPositionCommits: () => Promise<void>;
  createLegion: (input: {
    name: string;
    description?: string;
    nodeIds: string[];
  }) => Promise<LegionSummary | undefined>;
  deleteLegion: (id: string) => Promise<boolean>;
  instantiateLegion: (
    id: string,
    anchor?: WorldPosition,
  ) => Promise<LegionInstantiation | undefined>;
  dissolveContainer: (id: string) => Promise<void>;
  deleteCard: (id: string) => Promise<void>;
  deleteCards: (ids: string[]) => Promise<void>;
  requestConnection: (source?: string | null, target?: string | null) => void;
  closeConnectionDialog: () => void;
  createConnection: (relationship: Relationship, direction?: EdgeDirection) => Promise<void>;
  selectEdge: (id?: string) => void;
  selectCards: (ids: string[]) => void;
  updateSelectedEdge: (
    patch: Relationship | { relationship?: Relationship; direction?: EdgeDirection },
  ) => Promise<void>;
  deleteSelectedEdge: () => Promise<void>;
  loadText: (id: string) => Promise<void>;
  saveText: (id: string, content: string) => Promise<boolean>;
  uploadImage: (id: string, file: File) => Promise<boolean>;
  runAgent: (id: string, prompt: string) => Promise<void>;
  stopAgent: (id: string) => Promise<void>;
  loadSandboxRuntimes: (refresh?: boolean) => Promise<void>;
  refreshSandbox: (id: string, clearError?: boolean) => Promise<void>;
  saveSandboxConfig: (id: string, config: SandboxConfig) => Promise<boolean>;
  startSandbox: (id: string) => Promise<boolean>;
  stopSandbox: (id: string) => Promise<boolean>;
  executeSandbox: (id: string, command: string) => Promise<boolean>;
  ingestEvent: (event: RuntimeEvent) => void;
  setSocketState: (state: SocketState) => void;
  toggleActivity: () => void;
  setActivityOpen: (open: boolean) => void;
  toggleSettings: () => void;
  saveModelSettings: (settings: ModelSettings, clearApiKey?: boolean) => Promise<boolean>;
  togglePalette: () => void;
  toggleTheme: () => void;
  generateStressWorld: (count?: number) => void;
  clearStressWorld: () => void;
  pushToast: (message: Omit<ToastMessage, "id">) => void;
  dismissToast: (id: string) => void;
  undo: () => Promise<void>;
  redo: () => Promise<void>;
}

let toastSequence = 0;
let historySequence = 0;
let historyTransactionTail: Promise<void> = Promise.resolve();
let positionCommitTail: Promise<void> = Promise.resolve();
let pendingPositionCommitCount = 0;
let worldMutationEpoch = 0;
let refreshSequence = 0;
const legionOperationTails = new Map<string, Promise<void>>();

function markWorldMutation(): void {
  worldMutationEpoch += 1;
}

// This is the outermost editor lock. Transactions may acquire a per-Legion
// queue, but must never wait for positionCommitTail: position commits already
// run on this same queue and may have been enqueued after the transaction.
function withHistoryTransaction<T>(transaction: () => Promise<T>): Promise<T> {
  const result = historyTransactionTail.then(transaction, transaction);
  historyTransactionTail = result.then(() => undefined, () => undefined);
  return result;
}

function withLegionOperation<T>(id: string, operation: () => Promise<T>): Promise<T> {
  const previous = legionOperationTails.get(id) ?? Promise.resolve();
  const result = previous.then(operation, operation);
  const tail = result.then(() => undefined, () => undefined);
  legionOperationTails.set(id, tail);
  void tail.finally(() => {
    if (legionOperationTails.get(id) === tail) legionOperationTails.delete(id);
  });
  return result;
}

export const useWorldStore = create<WorldState>()(persist((set, get) => ({
  cards: [],
  catalog: EMPTY_CATALOG,
  edges: [],
  legions: [],
  legionError: undefined,
  stressCards: [],
  activeChunkKeys: getViewportChunkKeys(INITIAL_VIEWPORT),
  loadedChunkKeys: [],
  loadingChunkKeys: [],
  viewport: INITIAL_VIEWPORT,
  syncState: "loading",
  socketState: "connecting",
  selectedCardIds: [],
  selectionRevision: 0,
  events: [],
  activityOpen: false,
  settingsOpen: false,
  modelSettings: readModelSettings(),
  paletteCollapsed: false,
  theme: preferredTheme(),
  toasts: [],
  undoStack: [],
  redoStack: [],
  historyBusy: false,
  positionCommitBusy: false,
  sandboxInfo: {},
  sandboxBusy: {},
  sandboxErrors: {},
  sandboxRevisions: {},
  sandboxRuntimesLoading: false,

  initialize: async () => {
    const keys = getViewportChunkKeys(get().viewport);
    set({ activeChunkKeys: keys });
    set({ syncState: "loading", syncError: undefined, legionError: undefined });
    try {
      const [catalog, snapshot, library] = await Promise.all([
        worldApi.getCatalog(),
        worldApi.getWorld(keys),
        loadLegionLibrary(),
      ]);
      const legionError = library.ok ? undefined : apiErrorMessage(library.error);
      set({
        catalog,
        cards: snapshot.nodes,
        edges: snapshot.edges,
        legions: library.ok ? library.legions : [],
        legionError,
        loadedChunkKeys: [...new Set([...keys, ...chunkKeysFromSnapshot(snapshot)])],
        syncState: "online",
        syncError: undefined,
      });
      if (!library.ok) {
        get().pushToast({
          tone: "error",
          title: "Legion library unavailable",
          detail: `${legionError} The canvas remains available.`,
        });
      }
    } catch (error) {
      const message = apiErrorMessage(error);
      set({ syncState: "offline", syncError: message });
      get().pushToast({
        tone: "error",
        title: "World service unavailable",
        detail: `${message} Your canvas remains read-only until it reconnects.`,
      });
    }
  },

  refreshWorld: async () => {
    const refreshId = ++refreshSequence;
    set({ syncState: "syncing" });
    try {
      for (let attempt = 0; attempt < 2; attempt += 1) {
        const refreshedChunkKeys = [...get().activeChunkKeys];
        const mutationEpoch = worldMutationEpoch;
        const [snapshot, library] = await Promise.all([
          worldApi.getWorld(refreshedChunkKeys),
          loadLegionLibrary(),
        ]);
        if (refreshId !== refreshSequence) return;
        if (mutationEpoch !== worldMutationEpoch) {
          if (attempt === 0) {
            await get().waitForPositionCommits();
            continue;
          }
          set((state) => state.syncState === "syncing"
            ? { syncState: "online", syncError: undefined }
            : {});
          return;
        }

        const legionError = library.ok ? undefined : apiErrorMessage(library.error);
        set((state) => ({
          cards: mergeCards(state.cards, snapshot.nodes),
          edges: snapshot.edges,
          ...(library.ok ? { legions: library.legions, legionError: undefined } : { legionError }),
          loadedChunkKeys: [
            ...new Set([
              ...refreshedChunkKeys,
              ...chunkKeysFromSnapshot(snapshot),
            ]),
          ],
          syncState: "online",
          syncError: undefined,
        }));
        if (!library.ok) {
          get().pushToast({
            tone: "error",
            title: "Legion library refresh failed",
            detail: `${legionError} Existing Legion cards remain available.`,
          });
        }
        // The snapshot only authoritatively replaces the requested chunks. Any
        // other cached chunk must be reloaded before its cached cards are used
        // again, otherwise replacing `edges` above would permanently hide its
        // relationships. If the viewport moved during this request, load its
        // new chunks immediately.
        await get().ensureChunks(get().activeChunkKeys);
        return;
      }
    } catch (error) {
      if (refreshId !== refreshSequence) return;
      const message = apiErrorMessage(error);
      set({ syncState: "offline", syncError: message });
      get().pushToast({ tone: "error", title: "Refresh failed", detail: message });
    }
  },

  ensureChunks: async (keys) => {
    const state = get();
    const missing = keys.filter(
      (key) => !state.loadedChunkKeys.includes(key) && !state.loadingChunkKeys.includes(key),
    );
    if (missing.length === 0 || state.syncState === "offline") return;
    set((current) => ({
      loadingChunkKeys: [...new Set([...current.loadingChunkKeys, ...missing])],
    }));
    try {
      const snapshot = await worldApi.getWorld(missing);
      set((current) => ({
        cards: mergeCards(current.cards, snapshot.nodes),
        edges: mergeEdges(current.edges, snapshot.edges),
        loadedChunkKeys: [
          ...new Set([...current.loadedChunkKeys, ...missing, ...chunkKeysFromSnapshot(snapshot)]),
        ],
        loadingChunkKeys: current.loadingChunkKeys.filter((key) => !missing.includes(key)),
        syncState: "online",
      }));
    } catch (error) {
      set((current) => ({
        loadingChunkKeys: current.loadingChunkKeys.filter((key) => !missing.includes(key)),
      }));
      get().pushToast({
        tone: "error",
        title: "Terrain could not load",
        detail: apiErrorMessage(error),
      });
    }
  },

  setViewport: (viewport) => {
    const keys = getViewportChunkKeys(viewport);
    set({ viewport, activeChunkKeys: keys });
    void get().ensureChunks(keys);
  },

  formLegionGroup: (nodeIds) => withHistoryTransaction(async () => {
    const before = get().cards.filter((c) => nodeIds.includes(c.id)).map(copyCard);
    try {
      const result = await worldApi.formLegionGroup("New Legion", nodeIds);
      const group = result.find((c) => c.type === "legion")!;
      markWorldMutation();
      set((state) => ({ cards: mergeCards(state.cards, result), selectedCardIds: [group.id], selectionRevision: state.selectionRevision + 1,
        undoStack: appendHistory(state.undoStack, { id: ++historySequence, kind: "group-formed", label: "Form Legion", group, before, after: result.filter((c) => c.id !== group.id) }), redoStack: [] }));
    } catch (error) { get().pushToast({ tone: "error", title: "Legion was not formed", detail: apiErrorMessage(error) }); }
  }),

  setContainerMembership: (ids, parentId) => withHistoryTransaction(async () => {
    const before = get().cards.filter((c) => ids.includes(c.id) && !c.ephemeral).map(copyCard);
    const group = get().cards.find((c) => c.id === parentId);
    try {
      const after = await worldApi.batchUpdateNodes(before.map((c, index) => ({ node_id: c.id, patch: {
        parent_id: parentId,
        ...(group ? { position: { x: group.position.x + containerDefinition(group, get().catalog)!.content_inset[0] + 100 + (index % 2) * 310, y: group.position.y + containerDefinition(group, get().catalog)!.content_inset[1] + 40 + Math.floor(index / 2) * 200 } } : {}),
      } })));
      markWorldMutation();
      set((state) => ({ cards: mergeCards(state.cards, after),
        undoStack: appendHistory(state.undoStack, { id: ++historySequence, kind: "cards-updated", label: parentId ? "Join container" : "Leave container", before, after, membership: true }), redoStack: [] }));
    } catch (error) { get().pushToast({ tone: "error", title: "Membership was not changed", detail: apiErrorMessage(error) }); }
  }),

  createCard: (type, position, placement) => withHistoryTransaction(async () => {
    if (get().syncState === "offline") {
      get().pushToast({
        tone: "error",
        title: "Creation paused",
        detail: "Reconnect the world service before placing a persistent object.",
      });
      return undefined;
    }
    const definition = getNodeType(get().catalog, type);
    if (!definition) {
      get().pushToast({
        tone: "error",
        title: "Unknown plugin node type",
        detail: `The backend catalog does not define ${type}.`,
      });
      return undefined;
    }
    if (definition.user_creatable === false) {
      get().pushToast({
        tone: "error",
        title: "Use the dedicated creation action",
        detail: `${definition.label} cannot be placed as a standalone card.`,
      });
      return undefined;
    }
    const finalPosition = position ?? viewportCenterToWorld(get().viewport);
    const draft = buildCardDraft(type, finalPosition, definition);
    const configuredDraft = type === "agent" && get().modelSettings.models[0]
      ? { ...draft, config: { ...draft.config, model: get().modelSettings.models[0] } }
      : draft;
    set({ syncState: "syncing" });
    try {
      const card = await worldApi.createNode({ ...configuredDraft, ...placement });
      markWorldMutation();
      set((state) => ({
        cards: mergeCards(state.cards, [card]),
        syncState: "online",
        undoStack: appendHistory(state.undoStack, {
            id: ++historySequence,
            label: `Place ${card.name}`,
            kind: "card-created",
            cards: [copyCard(card)],
          }),
        redoStack: [],
      }));
      if (isContainer(card, get().catalog)) {
        const snapshot = await worldApi.getWorld();
        const members = ownedDescendants(snapshot.nodes, card.id);
        set((state) => ({ cards: mergeCards(state.cards, members) }));
      }
      get().pushToast({
        tone: "success",
        title: `${card.name} placed`,
        detail: "Drag its ports to define a real capability.",
      });
      return card;
    } catch (error) {
      set({ syncState: "online" });
      get().pushToast({ tone: "error", title: "Object was not created", detail: apiErrorMessage(error) });
      return undefined;
    }
  }),

  updateCard: (id, patch) => withHistoryTransaction(async () => {
    const current = get().cards.find((card) => card.id === id) ?? get().stressCards.find((card) => card.id === id);
    if (!current) return;
    if (current.ephemeral) {
      const optimistic = mergeCardPatch(current, patch);
      markWorldMutation();
      set((state) => ({
        stressCards: state.stressCards.map((card) =>
          card.id === id ? optimistic : card,
        ),
        undoStack: appendHistory(state.undoStack, {
            id: ++historySequence,
            label: `Edit ${optimistic.name}`,
            kind: "card-updated",
            before: copyCard(current),
            after: copyCard(optimistic),
          }),
        redoStack: [],
      }));
      return;
    }

    const optimistic = mergeCardPatch(current, patch);
    markWorldMutation();
    set((state) => ({
      cards: state.cards.map((card) => (card.id === id ? optimistic : card)),
      syncState: "syncing",
    }));
    try {
      const authoritative = await worldApi.updateNode(id, patch);
      markWorldMutation();
      set((state) => ({
        cards: state.cards.map((card) => (card.id === id ? authoritative : card)),
        syncState: "online",
        undoStack: appendHistory(state.undoStack, {
            id: ++historySequence,
            label: `Edit ${authoritative.name}`,
            kind: "card-updated",
            before: copyCard(current),
            after: copyCard(authoritative),
          }),
        redoStack: [],
      }));
    } catch (error) {
      markWorldMutation();
      set((state) => ({
        cards: state.cards.map((card) => (card.id === id ? current : card)),
        syncState: "online",
      }));
      get().pushToast({ tone: "error", title: "Change was not saved", detail: apiErrorMessage(error) });
    }
  }),

  updateCardPositions: (updates) => {
    const parents = new Map(updates.filter((update) => update.parent_id !== undefined).map((update) => [update.id, update.parent_id!]));
    const requested = new Map<string, WorldPosition>();
    for (const update of updates) {
      if (!Number.isFinite(update.position.x) || !Number.isFinite(update.position.y)) continue;
      requested.set(update.id, { ...update.position });
    }
    const explicit = new Map(requested);
    for (const member of get().cards) {
      if (explicit.has(member.id)) continue;
      const movedParent = ancestors(get().cards, member).find((parent) => explicit.has(parent.id));
      if (movedParent) {
        const target = explicit.get(movedParent.id)!;
        requested.set(member.id, { x: member.position.x + target.x - movedParent.position.x, y: member.position.y + target.y - movedParent.position.y });
      }
    }
    if (requested.size === 0) return Promise.resolve();
    pendingPositionCommitCount += 1;
    set({ positionCommitBusy: true });
    const scheduled = withHistoryTransaction(async () => {
      const available = [...get().cards, ...get().stressCards];
      const before = available.filter((card) => {
        const position = requested.get(card.id);
        return position !== undefined && (!samePosition(card.position, position) || (parents.has(card.id) && card.parent_id !== parents.get(card.id)));
      });
      if (before.length === 0) return;

      const beforeById = new Map(before.map((card) => [card.id, copyCard(card)]));
      const requestedById = new Map(before.map((card) => [card.id, requested.get(card.id)!]));
      const optimisticById = new Map(before.map((card) => [
        card.id,
        mergeCardPatch(card, { position: requestedById.get(card.id)!, ...(parents.has(card.id) ? { parent_id: parents.get(card.id)! } : {}) }),
      ]));
      markWorldMutation();
      set((state) => ({
        cards: state.cards.map((card) => optimisticById.get(card.id) ?? card),
        stressCards: state.stressCards.map((card) => optimisticById.get(card.id) ?? card),
      }));

      const persistent = before.filter((card) => !card.ephemeral);
      if (persistent.length > 0) set({ syncState: "syncing" });
      const succeeded = new Map<string, WorldCard>();
      const failed = new Map<string, unknown>();
      if (persistent.length > 0) {
        try {
          const authoritative = await worldApi.batchUpdateNodes(persistent.map((card) => ({
            node_id: card.id,
            patch: { position: requestedById.get(card.id)!, ...(parents.has(card.id) ? { parent_id: parents.get(card.id)! } : {}) },
          })));
          const authoritativeById = new Map(authoritative.map((card) => [card.id, card]));
          const missing = persistent.filter((card) => !authoritativeById.has(card.id));
          if (missing.length > 0) {
            throw new Error(`Batch response omitted ${missing.map((card) => card.id).join(", ")}.`);
          }
          persistent.forEach((card) => succeeded.set(card.id, authoritativeById.get(card.id)!));
        } catch (error) {
          persistent.forEach((card) => failed.set(card.id, error));
        }
      }
      for (const card of before.filter((item) => item.ephemeral)) {
        succeeded.set(card.id, optimisticById.get(card.id)!);
      }

      const reconcile = (card: WorldCard): WorldCard => {
        const target = requestedById.get(card.id);
        if (!target || !samePosition(card.position, target)) return card;
        return succeeded.get(card.id) ?? beforeById.get(card.id) ?? card;
      };
      const historyBefore = before
        .filter((card) => succeeded.has(card.id))
        .map((card) => copyCard(beforeById.get(card.id) ?? card));
      const historyAfter = historyBefore.map((card) => copyCard(succeeded.get(card.id) ?? card));
      markWorldMutation();
      set((state) => ({
        cards: state.cards.map(reconcile),
        stressCards: state.stressCards.map(reconcile),
        syncState: persistent.length > 0 ? "online" : state.syncState,
        ...(historyAfter.length > 0 ? {
          undoStack: appendHistory(state.undoStack, {
            id: ++historySequence,
            label: historyAfter.length === 1 ? `Move ${historyAfter[0].name}` : `Move ${historyAfter.length} cards`,
            kind: "cards-updated" as const,
            before: historyBefore,
            after: historyAfter,
            membership: parents.size > 0,
          }),
          redoStack: [],
        } : {}),
      }));
      if (failed.size > 0) {
        const firstError = failed.values().next().value;
        get().pushToast({
          tone: "error",
          title: failed.size === 1 ? "A card position was not saved" : `${failed.size} card positions were not saved`,
          detail: apiErrorMessage(firstError),
        });
      }
    });

    positionCommitTail = scheduled.then(() => undefined, () => undefined);
    return scheduled.finally(() => {
      pendingPositionCommitCount = Math.max(0, pendingPositionCommitCount - 1);
      if (pendingPositionCommitCount === 0) set({ positionCommitBusy: false });
    });
  },

  waitForPositionCommits: async () => {
    while (true) {
      const pending = positionCommitTail;
      await pending;
      if (pending === positionCommitTail) return;
    }
  },

  createLegion: ({ name, description, nodeIds }) => withHistoryTransaction(async () => {
    if (get().syncState === "offline") {
      get().pushToast({
        tone: "error",
        title: "Legion could not be collected",
        detail: "Reconnect the world service before saving a reusable formation.",
      });
      return undefined;
    }
    const ids = [...new Set(nodeIds)];
    const catalog = get().catalog;
    const topology = summarizeLegionSelection(
      [...get().cards, ...get().stressCards],
      get().edges,
      ids,
      catalog,
    );
    const selected = topology.cards;
    if (ids.length < 2 || selected.length !== ids.length) {
      get().pushToast({
        tone: "error",
        title: "Choose at least two cards",
        detail: "A Legion captures the selected cards and every relationship between them.",
      });
      return undefined;
    }
    if (topology.unsupportedCards.length > 0 || topology.unsupportedEdges.length > 0) {
      const blockedCards = topology.unsupportedCards.map((card) => {
        const definition = getNodeType(catalog, card.type);
        return `${card.name} (${card.ephemeral ? "synthetic card" : definition?.plugin_id ?? "missing node definition"})`;
      });
      const blockedRelationships = [...new Set(topology.unsupportedEdges.map((edge) => {
        const definition = catalog.relationships.find((item) => item.id === edge.relationship);
        return `${definition?.label ?? edge.relationship} (${definition?.plugin_id ?? "missing relationship definition"})`;
      }))];
      get().pushToast({
        tone: "error",
        title: "A plugin blocked this Legion",
        detail: [
          ...(blockedCards.length > 0 ? [`Cards: ${blockedCards.join(", ")}.`] : []),
          ...(blockedRelationships.length > 0
            ? [`Relationships: ${blockedRelationships.join(", ")}.`]
            : []),
        ].join(" "),
      });
      return undefined;
    }

    set({ syncState: "syncing" });
    try {
      const legion = await worldApi.createLegion({
        name: name.trim(),
        ...(description?.trim() ? { description: description.trim() } : {}),
        node_ids: ids,
      });
      markWorldMutation();
      set((state) => ({
        legions: mergeLegions(state.legions, [legion]),
        legionError: undefined,
        syncState: "online",
      }));
      get().pushToast({
        tone: "success",
        title: `${legion.name} collected`,
        detail: `${legion.node_count} cards and ${legion.edge_count} internal links are ready to deploy.`,
      });
      return legion;
    } catch (error) {
      set({ syncState: "online" });
      get().pushToast({ tone: "error", title: "Legion was not saved", detail: apiErrorMessage(error) });
      return undefined;
    }
  }),

  deleteLegion: (id) => withHistoryTransaction(() => withLegionOperation(id, async () => {
    const legion = get().legions.find((item) => item.id === id);
    if (!legion) return false;
    try {
      const deleted = await worldApi.deleteLegion(id);
      const keepOtherLegions = (operation: WorldHistoryOperation) => (
        operation.kind !== "legion-instantiated" || operation.legionId !== id
      );
      markWorldMutation();
      set((state) => ({
        legions: state.legions.filter((item) => item.id !== id),
        legionError: undefined,
        undoStack: state.undoStack.filter(keepOtherLegions),
        redoStack: state.redoStack.filter(keepOtherLegions),
      }));
      get().pushToast({ tone: "neutral", title: `${deleted.name} removed from the library` });
      return true;
    } catch (error) {
      get().pushToast({ tone: "error", title: "Legion was not removed", detail: apiErrorMessage(error) });
      return false;
    }
  })),

  instantiateLegion: (id, anchor) => withHistoryTransaction(() => withLegionOperation(id, async () => {
    const legion = get().legions.find((item) => item.id === id);
    if (!legion) {
      get().pushToast({ tone: "error", title: "Legion is unavailable", detail: "Refresh the Legion library and try again." });
      return undefined;
    }
    if (!legion.compatible) {
      get().pushToast({
        tone: "error",
        title: `${legion.name} cannot be deployed`,
        detail: legion.issues.join(" ") || "One or more required plugins are unavailable.",
      });
      return undefined;
    }
    if (get().syncState === "offline") {
      get().pushToast({ tone: "error", title: "Deployment paused", detail: "Reconnect the world service first." });
      return undefined;
    }
    const finalAnchor = anchor ?? viewportCenterToWorld(get().viewport);
    const origin = {
      x: finalAnchor.x - legion.bounds.width / 2,
      y: finalAnchor.y - legion.bounds.height / 2,
    };
    set({ syncState: "syncing" });
    try {
      const instance = await worldApi.instantiateLegion(id, origin);
      const cards = instance.nodes.map(copyCard);
      const edges = instance.edges.map(copyEdge);
      markWorldMutation();
      set((state) => ({
        cards: mergeCards(state.cards, instance.nodes),
        edges: mergeEdges(state.edges, instance.edges),
        selectedCardIds: instance.nodes.map((card) => card.id),
        selectionRevision: state.selectionRevision + 1,
        selectedEdgeId: undefined,
        syncState: "online",
        undoStack: appendHistory(state.undoStack, {
          id: ++historySequence,
          label: `Deploy ${legion.name}`,
          kind: "legion-instantiated",
          legionId: legion.id,
          position: { ...origin },
          cards,
          edges,
        }),
        redoStack: [],
      }));
      get().pushToast({
        tone: "success",
        title: `${legion.name} deployed`,
        detail: `${instance.nodes.length} cards and ${instance.edges.length} links instantiated.`,
      });
      return instance;
    } catch (error) {
      set({ syncState: "online" });
      get().pushToast({ tone: "error", title: "Legion was not deployed", detail: apiErrorMessage(error) });
      return undefined;
    }
  })),

  dissolveContainer: (id) => withHistoryTransaction(async () => {
    const group = get().cards.find((c) => c.id === id && isContainer(c, get().catalog));
    if (!group) return;
    const members = get().cards.filter((c) => c.parent_id === id).map(copyCard);
    const edges = get().edges.filter((e) => e.source === id || e.target === id).map(copyEdge);
    try {
      const snapshot = await snapshotCardForHistory(group);
      const detached = members.length ? await applyCardPositions(members.map((c) => ({ ...c, parent_id: null })), true) : [];
      try { await worldApi.deleteNode(id); }
      catch (error) { if (members.length) await applyCardPositions(members, true); throw error; }
      markWorldMutation();
      set((state) => ({ cards: mergeCards(state.cards.filter((c) => c.id !== id), detached),
        edges: state.edges.filter((e) => e.source !== id && e.target !== id),
        selectedCardIds: members.map((c) => c.id), selectedEdgeId: undefined, selectionRevision: state.selectionRevision + 1,
        undoStack: appendHistory(state.undoStack, { id: ++historySequence, kind: "group-dissolved", label: "Dissolve container", group: snapshot, members, edges }), redoStack: [] }));
      get().pushToast({ tone: "neutral", title: `${group.name} dissolved`, detail: "Members and their connections were kept. Ctrl+Z to restore the container." });
    } catch (error) { get().pushToast({ tone: "error", title: "Container was not dissolved", detail: apiErrorMessage(error) }); }
  }),

  deleteCard: async (id) => get().deleteCards([id]),

  deleteCards: (ids) => withHistoryTransaction(async () => {
    // Container members may have been created server-side since the last snapshot.
    if (ids.some((id) => get().cards.some((card) => card.id === id && isContainer(card, get().catalog)))) {
      try {
        const snapshot = await worldApi.getWorld();
        const owned = new Set(ids);
        ids.forEach((id) => ownedDescendants(snapshot.nodes, id).forEach((member) => owned.add(member.id)));
        set((state) => ({ cards: mergeCards(state.cards, snapshot.nodes.filter((card) => owned.has(card.id))) }));
      } catch (error) {
        get().pushToast({ tone: "error", title: "Cards were not removed", detail: apiErrorMessage(error) });
        return;
      }
    }
    const requested = new Set(ids);
    for (const id of ids) {
      const card = get().cards.find((item) => item.id === id);
      if (card) ownedDescendants(get().cards, id).forEach((member) => requested.add(member.id));
    }
    const cards = [...get().cards, ...get().stressCards].filter((card) => requested.has(card.id));
    if (cards.length === 0) return;

    const snapshots = new Map<string, RestorableCard>();
    try {
      await Promise.all(cards.map(async (card) => snapshots.set(card.id, await snapshotCardForHistory(card))));
    } catch (error) {
      get().pushToast({ tone: "error", title: "Cards were not removed", detail: `Could not preserve document data for undo: ${apiErrorMessage(error)}` });
      return;
    }

    const attachedBefore = get().edges.filter((edge) => requested.has(edge.source) || requested.has(edge.target)).map(copyEdge);
    const removed: WorldCard[] = [];
    if (cards.some((card) => card.equipment || isContainer(card, get().catalog))) {
      try {
        const persistent = cards.filter((card) => !card.ephemeral);
        if (persistent.length) await worldApi.deleteNodes(persistent.map((card) => card.id));
        removed.push(...cards);
      } catch (error) {
        get().pushToast({ tone: "error", title: "Cards were not removed", detail: apiErrorMessage(error) });
        return;
      }
    } else for (const card of cards) {
      try {
        if (!card.ephemeral) await worldApi.deleteNode(card.id);
        removed.push(card);
      } catch (error) {
        get().pushToast({
          tone: "error",
          title: `${card.name} was not removed`,
          detail: apiErrorMessage(error),
        });
      }
    }
    if (removed.length === 0) return;

    const removedIds = new Set(removed.map((card) => card.id));
    const attachedEdges = attachedBefore.filter(
      (edge) => removedIds.has(edge.source) || removedIds.has(edge.target),
    );
    markWorldMutation();
    set((state) => ({
      cards: state.cards.filter((card) => !removedIds.has(card.id)),
      stressCards: state.stressCards.filter((card) => !removedIds.has(card.id)),
      edges: state.edges.filter((edge) => !removedIds.has(edge.source) && !removedIds.has(edge.target)),
      selectedCardIds: state.selectedCardIds.filter((id) => !removedIds.has(id)),
      selectedEdgeId: attachedEdges.some((edge) => edge.id === state.selectedEdgeId)
        ? undefined
        : state.selectedEdgeId,
      undoStack: appendHistory(state.undoStack, {
          id: ++historySequence,
          label: removed.length === 1 ? `Remove ${removed[0].name}` : `Remove ${removed.length} cards`,
          kind: "cards-deleted",
          cards: removed.map((card) => snapshots.get(card.id) ?? copyCard(card)),
          edges: attachedEdges.map(copyEdge),
        }),
      redoStack: [],
    }));
    get().pushToast({
      tone: "neutral",
      title: removed.length === 1 ? `${removed[0].name} removed` : `${removed.length} cards removed`,
      detail: "Press Ctrl+Z to restore.",
    });
  }),

  requestConnection: (source, target) => {
    const sourceCard = get().cards.find((card) => card.id === source) ?? get().stressCards.find((card) => card.id === source);
    const targetCard = get().cards.find((card) => card.id === target) ?? get().stressCards.find((card) => card.id === target);
    if (sourceCard?.ephemeral || targetCard?.ephemeral) {
      get().pushToast({
        tone: "neutral",
        title: "Synthetic cards are topology-only",
        detail: "Clear the stress world and connect persistent objects to grant capabilities.",
      });
      return;
    }
    const validation = validateConnection(
      get().catalog,
      source,
      target,
      sourceCard?.type,
      targetCard?.type,
      get().edges,
      get().cards,
    );
    if (!validation.valid || !validation.source || !validation.target) {
      get().pushToast({
        tone: "error",
        title: "That relationship is not valid",
        detail: validation.reason,
      });
      return;
    }
    set({
      pendingConnection: {
        source: validation.source,
        target: validation.target,
        options: validation.options,
      },
    });
  },

  closeConnectionDialog: () => set({ pendingConnection: undefined }),

  createConnection: (relationship, direction = "forward") => withHistoryTransaction(async () => {
    const pending = get().pendingConnection;
    if (!pending) return;
    set({ syncState: "syncing" });
    try {
      const edge = await worldApi.createEdge({
        source: pending.source,
        target: pending.target,
        relationship,
        direction,
      });
      markWorldMutation();
      set((state) => ({
        edges: mergeEdges(state.edges, [edge]),
        pendingConnection: undefined,
        selectedEdgeId: edge.id,
        syncState: "online",
        undoStack: appendHistory(state.undoStack, {
            id: ++historySequence,
            label: "Create relationship",
            kind: "edge-created",
            edge: copyEdge(edge),
          }),
        redoStack: [],
      }));
      get().pushToast({
        tone: "success",
        title: "Capability granted",
        detail: "The backend accepted this relationship and updated effective permissions.",
      });
    } catch (error) {
      set({ syncState: "online" });
      get().pushToast({
        tone: "error",
        title: "Capability was not granted",
        detail: apiErrorMessage(error),
      });
    }
  }),

  selectEdge: (id) => set({ selectedEdgeId: id }),
  selectCards: (ids) => set({ selectedCardIds: [...new Set(ids)] }),

  updateSelectedEdge: (patch) => withHistoryTransaction(async () => {
    const id = get().selectedEdgeId;
    if (!id) return;
    const previous = get().edges.find((edge) => edge.id === id);
    if (!previous) return;
    try {
      const edge = await worldApi.updateEdge(
        id,
        typeof patch === "string" ? { relationship: patch } : patch,
      );
      markWorldMutation();
      set((state) => ({
        edges: state.edges.map((item) => (item.id === id ? edge : item)),
        undoStack: appendHistory(state.undoStack, {
            id: ++historySequence,
            label: "Edit relationship",
            kind: "edge-updated",
            before: copyEdge(previous),
            after: copyEdge(edge),
          }),
        redoStack: [],
      }));
      get().pushToast({ tone: "success", title: "Permission updated" });
    } catch (error) {
      get().pushToast({ tone: "error", title: "Permission was not changed", detail: apiErrorMessage(error) });
    }
  }),

  deleteSelectedEdge: () => withHistoryTransaction(async () => {
    const id = get().selectedEdgeId;
    if (!id) return;
    const edge = get().edges.find((item) => item.id === id);
    if (!edge) return;
    try {
      await worldApi.deleteEdge(id);
      markWorldMutation();
      set((state) => ({
        edges: state.edges.filter((edge) => edge.id !== id),
        selectedEdgeId: undefined,
        undoStack: appendHistory(state.undoStack, {
            id: ++historySequence,
            label: "Remove relationship",
            kind: "edge-deleted",
            edge: copyEdge(edge),
          }),
        redoStack: [],
      }));
      get().pushToast({
        tone: "neutral",
        title: "Capability revoked",
        detail: "The backend removed this relationship immediately.",
      });
    } catch (error) {
      get().pushToast({ tone: "error", title: "Capability remains active", detail: apiErrorMessage(error) });
    }
  }),

  loadText: async (id) => {
    try {
      const result = await worldApi.getText(id);
      set((state) => ({
        cards: state.cards.map((card) =>
          card.id === id
            ? mergeCardPatch(card, {
                config: {
                  content: result.content,
                  revision: result.revision,
                  history: result.history as never,
                },
              })
            : card,
        ),
      }));
    } catch (error) {
      get().pushToast({ tone: "error", title: "Text could not be loaded", detail: apiErrorMessage(error) });
    }
  },

  saveText: async (id, content) => {
    const card = get().cards.find((item) => item.id === id);
    if (!card) return false;
    try {
      const result = await worldApi.saveText(
        id,
        content,
        typeof card.config.revision === "number" ? card.config.revision : undefined,
      );
      const resource = (result.resource && typeof result.resource === "object"
        ? result.resource
        : result) as Record<string, unknown>;
      set((state) => ({
        cards: state.cards.map((item) =>
          item.id === id
            ? mergeCardPatch(item, {
                status: "available",
                config: {
                  content: String(resource.content ?? content),
                  preview: String(resource.content ?? content).slice(0, 140),
                  revision: typeof resource.revision === "number"
                    ? resource.revision
                    : (Number(item.config.revision) || 0) + 1,
                  history: Array.isArray(resource.history)
                    ? (resource.history as never)
                    : item.config.history,
                },
              })
            : item,
        ),
      }));
      get().pushToast({ tone: "success", title: "Text saved", detail: "The managed resource is up to date." });
      return true;
    } catch (error) {
      get().pushToast({ tone: "error", title: "Text was not saved", detail: apiErrorMessage(error) });
      return false;
    }
  },

  uploadImage: async (id, file) => {
    try {
      const result = await worldApi.uploadImage(id, file);
      const source = (result.resource && typeof result.resource === "object"
        ? result.resource
        : result) as Record<string, unknown>;
      const nestedNode = result.node;
      if (nestedNode) {
        const node = normalizeCard(nestedNode);
        set((state) => ({ cards: state.cards.map((card) => (card.id === id ? node : card)) }));
      } else {
        const previewUrl = typeof source.preview_url === "string"
          ? source.preview_url
          : resourceContentUrl(id);
        set((state) => ({
          cards: state.cards.map((card) =>
            card.id === id
              ? mergeCardPatch(card, {
                  name: String(source.filename ?? file.name),
                  config: {
                    filename: String(source.filename ?? file.name),
                    mime_type: String(source.mime_type ?? source.media_type ?? file.type),
                    bytes: typeof source.bytes === "number"
                      ? source.bytes
                      : typeof source.size_bytes === "number" ? source.size_bytes : file.size,
                    image_width: typeof source.width === "number" ? source.width : undefined,
                    image_height: typeof source.height === "number" ? source.height : undefined,
                    preview_url: previewUrl,
                    revision: typeof source.revision === "number" ? source.revision : 1,
                  },
                })
              : card,
          ),
        }));
      }
      get().pushToast({ tone: "success", title: "Image imported", detail: "A managed copy is now available in this world." });
      return true;
    } catch (error) {
      get().pushToast({ tone: "error", title: "Image import failed", detail: apiErrorMessage(error) });
      return false;
    }
  },

  runAgent: async (id, prompt) => {
    try {
      await worldApi.runAgent(id, prompt);
      set((state) => ({
        cards: state.cards.map((card) =>
          card.id === id
            ? mergeCardPatch(card, { status: "running", config: { prompt } })
            : card,
        ),
        activityOpen: true,
      }));
    } catch (error) {
      get().pushToast({ tone: "error", title: "Agent did not start", detail: apiErrorMessage(error) });
    }
  },

  stopAgent: async (id) => {
    try {
      await worldApi.stopAgent(id);
      set((state) => ({
        cards: state.cards.map((card) =>
          card.id === id ? mergeCardPatch(card, { status: "idle" }) : card,
        ),
      }));
    } catch (error) {
      get().pushToast({ tone: "error", title: "Agent did not stop", detail: apiErrorMessage(error) });
    }
  },

  loadSandboxRuntimes: async (refresh = false) => {
    if (get().sandboxRuntimesLoading || (get().sandboxRuntimes && !refresh)) return;
    set({ sandboxRuntimesLoading: true, sandboxRuntimesError: undefined });
    try {
      set({ sandboxRuntimes: await worldApi.getSandboxRuntimes(refresh) });
    } catch (error) {
      set({ sandboxRuntimesError: apiErrorMessage(error) });
    } finally {
      set({ sandboxRuntimesLoading: false });
    }
  },

  refreshSandbox: async (id, clearError = false) => {
    if (get().sandboxBusy[id]) return;
    const hadInfo = !!get().sandboxInfo[id];
    const revision = (get().sandboxRevisions[id] ?? 0) + 1;
    set((state) => ({ sandboxRevisions: { ...state.sandboxRevisions, [id]: revision } }));
    try {
      const info = await worldApi.getSandbox(id);
      if (get().sandboxRevisions[id] !== revision) return;
      set((state) => ({
        sandboxInfo: { ...state.sandboxInfo, [id]: info },
        ...(clearError || !hadInfo || (info.network_available && info.state === "ready") ? { sandboxErrors: { ...state.sandboxErrors, [id]: undefined } } : {}),
        cards: state.cards.map((card) => card.id === id ? mergeCardPatch(card, {
          status: info.state,
          ...(info.state !== "running" ? { config: { active_command: "" } } : {}),
        }) : card),
      }));
    } catch (error) {
      if (get().sandboxRevisions[id] !== revision) return;
      set((state) => ({
        sandboxInfo: { ...state.sandboxInfo, [id]: undefined },
        sandboxErrors: { ...state.sandboxErrors, [id]: apiErrorMessage(error) },
      }));
    }
  },

  saveSandboxConfig: async (id, config) => {
    if (get().sandboxBusy[id]) return false;
    const revision = (get().sandboxRevisions[id] ?? 0) + 1;
    set((state) => ({
      sandboxBusy: { ...state.sandboxBusy, [id]: "saving" },
      sandboxErrors: { ...state.sandboxErrors, [id]: undefined },
      sandboxRevisions: { ...state.sandboxRevisions, [id]: revision },
    }));
    return withHistoryTransaction(async () => {
      try {
        const current = get().cards.find((card) => card.id === id);
        if (!current || current.status !== "stopped") throw new Error("Stop the sandbox before changing its workspace.");
        const authoritative = await worldApi.updateNode(id, { config: { ...config } });
        markWorldMutation();
        set((state) => ({
          cards: state.cards.map((card) => card.id === id ? authoritative : card),
          sandboxInfo: { ...state.sandboxInfo, [id]: undefined },
          sandboxBusy: { ...state.sandboxBusy, [id]: undefined },
          undoStack: appendHistory(state.undoStack, {
            id: ++historySequence,
            label: `Configure ${authoritative.name}`,
            kind: "card-updated",
            before: copyCard(current),
            after: copyCard(authoritative),
          }),
          redoStack: [],
        }));
        await get().refreshSandbox(id);
        return true;
      } catch (error) {
        set((state) => ({
          sandboxBusy: { ...state.sandboxBusy, [id]: undefined },
          sandboxErrors: { ...state.sandboxErrors, [id]: apiErrorMessage(error) },
        }));
        get().pushToast({ tone: "error", title: "Sandbox settings were not saved", detail: apiErrorMessage(error) });
        return false;
      }
    });
  },

  startSandbox: async (id) => {
    if (get().sandboxBusy[id]) return false;
    const revision = (get().sandboxRevisions[id] ?? 0) + 1;
    set((state) => ({
      sandboxBusy: { ...state.sandboxBusy, [id]: "starting" },
      sandboxErrors: { ...state.sandboxErrors, [id]: undefined },
      sandboxRevisions: { ...state.sandboxRevisions, [id]: revision },
    }));
    let failed = false;
    try {
      const info = await worldApi.startSandbox(id);
      set((state) => ({
        sandboxInfo: { ...state.sandboxInfo, [id]: info },
        cards: state.cards.map((card) => card.id === id ? mergeCardPatch(card, { status: info.state }) : card),
      }));
      return true;
    } catch (error) {
      failed = true;
      set((state) => ({ sandboxErrors: { ...state.sandboxErrors, [id]: apiErrorMessage(error) } }));
      get().pushToast({ tone: "error", title: "Sandbox did not start", detail: apiErrorMessage(error) });
      return false;
    } finally {
      set((state) => ({ sandboxBusy: { ...state.sandboxBusy, [id]: undefined } }));
      if (failed) await get().refreshSandbox(id);
      // Startup refreshed backend prerequisites. Read its current cached catalog;
      // do not run another expensive discovery probe or keep the old UI snapshot.
      try { set({ sandboxRuntimes: await worldApi.getSandboxRuntimes(false), sandboxRuntimesError: undefined }); }
      catch (error) { set({ sandboxRuntimesError: apiErrorMessage(error) }); }
    }
  },

  stopSandbox: async (id) => {
    const busy = get().sandboxBusy[id];
    if (busy && busy !== "executing") return false;
    const revision = (get().sandboxRevisions[id] ?? 0) + 1;
    set((state) => ({
      sandboxBusy: { ...state.sandboxBusy, [id]: "stopping" },
      sandboxErrors: { ...state.sandboxErrors, [id]: undefined },
      sandboxRevisions: { ...state.sandboxRevisions, [id]: revision },
    }));
    let failed = false;
    try {
      const info = await worldApi.stopSandbox(id);
      set((state) => ({
        sandboxInfo: { ...state.sandboxInfo, [id]: info },
        cards: state.cards.map((card) => card.id === id
          ? mergeCardPatch(card, { status: info.state, config: { active_command: "" } }) : card),
      }));
      return true;
    } catch (error) {
      failed = true;
      set((state) => ({ sandboxErrors: { ...state.sandboxErrors, [id]: apiErrorMessage(error) } }));
      get().pushToast({ tone: "error", title: "Sandbox did not stop", detail: apiErrorMessage(error) });
      return false;
    } finally {
      set((state) => ({ sandboxBusy: { ...state.sandboxBusy, [id]: undefined } }));
      if (failed) await get().refreshSandbox(id);
    }
  },

  executeSandbox: async (id, command) => {
    if (get().sandboxBusy[id]) return false;
    const previousEventIds = new Set(get().events.map((event) => event.id));
    const revision = (get().sandboxRevisions[id] ?? 0) + 1;
    set((state) => ({
      sandboxBusy: { ...state.sandboxBusy, [id]: "executing" },
      sandboxErrors: { ...state.sandboxErrors, [id]: undefined },
      sandboxRevisions: { ...state.sandboxRevisions, [id]: revision },
      cards: state.cards.map((card) => card.id === id
        ? mergeCardPatch(card, { config: { active_command: command } }) : card),
      activityOpen: true,
    }));
    try {
      const result = await worldApi.executeSandbox(id, command);
      if (get().sandboxRevisions[id] !== revision) return true;
      const receivedOutput = (channel: "stdout" | "stderr") => get().events.some((event) => (
        !previousEventIds.has(event.id)
        && (event.sandbox_id ?? event.node_id) === id
        && event.type.toLowerCase().includes(channel)
        && !!(event.payload.text ?? event.payload.output ?? event.message)
      ));
      const stdout = receivedOutput("stdout") ? [] : String(result.stdout ?? "").split(/\r?\n/).filter(Boolean);
      const stderr = receivedOutput("stderr") ? [] : String(result.stderr ?? "").split(/\r?\n/).filter(Boolean).map((line) => `! ${line}`);
      const completion = typeof result.exit_code === "number"
        ? [`exit ${result.exit_code}${result.timed_out ? " · timed out" : result.cancelled ? " · cancelled" : ""}`]
        : [];
      set((state) => ({
        cards: state.cards.map((card) => card.id === id
          ? mergeCardPatch(card, {
              config: {
                active_command: "",
                output: [
                  ...(Array.isArray(card.config.output) ? card.config.output : []),
                  ...stdout,
                  ...stderr,
                  ...completion,
                ].slice(-100),
              },
            }) : card),
      }));
      return true;
    } catch (error) {
      if (get().sandboxRevisions[id] !== revision) return false;
      set((state) => ({ sandboxErrors: { ...state.sandboxErrors, [id]: apiErrorMessage(error) } }));
      get().pushToast({ tone: "error", title: "Command was rejected", detail: apiErrorMessage(error) });
      return false;
    } finally {
      if (get().sandboxRevisions[id] === revision) {
        set((state) => ({
          sandboxBusy: { ...state.sandboxBusy, [id]: undefined },
          cards: state.cards.map((card) => card.id === id
            ? mergeCardPatch(card, { config: { active_command: "" } }) : card),
        }));
        await get().refreshSandbox(id);
      }
    }
  },

  ingestEvent: (event) => {
    const nodeId = event.node_id ?? event.agent_id ?? event.sandbox_id ?? event.resource_id;
    const normalizedType = event.type.replace(/[.\s-]/g, "_").toLowerCase();
    if (normalizedType === "nodes_generated") {
      const { source_id, target_id, container_id, nodes } = event.payload;
      if (typeof source_id === "string" && typeof target_id === "string" && Date.now() - Date.parse(event.timestamp) < 2400) {
        useGenerationStore.getState().enqueue({ id: event.id, sourceId: source_id, targetId: target_id,
          containerId: typeof container_id === "string" ? container_id : undefined });
      }
      if (Array.isArray(nodes)) {
        worldMutationEpoch += 1;
        set((state) => ({ cards: mergeCards(state.cards, nodes.map(normalizeCard)) }));
      }
    }
    if (normalizedType === "card_deleted" && nodeId) {
      worldMutationEpoch += 1;
      set((state) => ({
        cards: state.cards.filter((card) => card.id !== nodeId),
        edges: state.edges.filter((edge) => edge.source !== nodeId && edge.target !== nodeId),
        selectedCardIds: state.selectedCardIds.filter((id) => id !== nodeId),
        selectedEdgeId: state.edges.some((edge) => edge.id === state.selectedEdgeId
          && (edge.source === nodeId || edge.target === nodeId)) ? undefined : state.selectedEdgeId,
      }));
    }
    const outputText = String(
      event.payload.error ?? event.payload.text ?? event.payload.output ?? event.message ?? "",
    );
    if (normalizedType.includes("runtime_error")) {
      const model = String(get().cards.find((card) => card.id === nodeId)?.config.model ?? "configured model");
      get().pushToast({ tone: "error", ...describeRuntimeError(outputText, model) });
    }
    if (normalizedType === "run_failed") {
      // Nested run failures propagate to their root; only the root failure is
      // surfaced globally so the user sees one clear notification per run tree.
      const run = event.payload.run as Record<string, unknown> | undefined;
      if (!run || run.parent_run_id == null) {
        const model = String(get().cards.find((card) => card.id === nodeId)?.config.model ?? "configured model");
        get().pushToast({ tone: "error", ...describeRuntimeError(outputText || "The run failed without an error detail.", model) });
      }
    }
    set((state) => ({
      events: [event, ...state.events].slice(0, 160),
      cards: nodeId
        ? state.cards.map((card) => {
            if (card.id !== nodeId) return card;
            let status = card.status;
            if (typeof event.payload.status === "string") status = event.payload.status as WorldCard["status"];
            else if (normalizedType.includes("agent_started")) status = "running";
            else if (normalizedType.includes("agent_stopped") || normalizedType.includes("agent_completed")) status = "idle";
            else if (normalizedType.includes("command_started")) status = "running";
            else if (normalizedType.includes("command_finished") && card.type !== "sandbox") status = "ready";
            else if (normalizedType.includes("error")) status = "error";

            const shouldAppend =
              normalizedType.includes("stdout") ||
              normalizedType.includes("stderr") ||
              normalizedType.includes("tool_") ||
              normalizedType.includes("command_") ||
              normalizedType.includes("agent_") ||
              normalizedType.startsWith("run_") ||
              normalizedType.includes("error");
            const existing = Array.isArray(card.config.output) ? card.config.output : [];
            const output = shouldAppend && outputText
              ? [...existing, `${normalizedType.includes("stderr") ? "! " : ""}${outputText}`].slice(-100)
              : existing;
            return mergeCardPatch(card, {
              status,
              config: {
                output,
                active_command: normalizedType.includes("command_finished")
                  ? ""
                  : String(event.payload.command ?? card.config.active_command ?? ""),
              },
            });
          })
        : state.cards,
    }));

    if (normalizedType.includes("permission_changed")) {
      void get().refreshWorld();
    }
    if (normalizedType.includes("resource_modified") && nodeId) {
      void get().loadText(nodeId);
    }
    if (nodeId && ["sandbox_command_finished", "sandbox_state_changed"].includes(normalizedType)) {
      void get().refreshSandbox(nodeId);
    }
  },

  setSocketState: (socketState) => set({ socketState }),
  toggleActivity: () => set((state) => ({ activityOpen: !state.activityOpen })),
  setActivityOpen: (activityOpen) => set({ activityOpen }),
  toggleSettings: () => set((state) => ({ settingsOpen: !state.settingsOpen })),

  saveModelSettings: async (settings, clearApiKey = false) => {
    const normalized: ModelSettings = {
      baseUrl: settings.baseUrl.trim(),
      apiKey: settings.apiKey,
      apiKeyConfigured: settings.apiKeyConfigured,
      models: normalizeModelList(settings.models),
    };
    if (normalized.models.length === 0) {
      get().pushToast({ tone: "error", title: "Add at least one model", detail: "Agent cards need a model to call." });
      return false;
    }
    try {
      const saved = await worldApi.configureLlm({
        base_url: normalized.baseUrl,
        api_key: normalized.apiKey || null,
        clear_api_key: clearApiKey,
      });
      const persisted = {
        ...normalized,
        baseUrl: saved.base_url,
        apiKey: "",
        apiKeyConfigured: saved.api_key_configured,
      };
      set({ modelSettings: persisted });
      persistModelSettings(persisted);
      get().pushToast({ tone: "success", title: "Model connection saved securely", detail: "The backend will restore this connection automatically after restarts." });
      return true;
    } catch (error) {
      get().pushToast({ tone: "error", title: "Model connection was not applied", detail: apiErrorMessage(error) });
      return false;
    }
  },

  togglePalette: () => set((state) => ({ paletteCollapsed: !state.paletteCollapsed })),

  toggleTheme: () => {
    const theme = get().theme === "light" ? "dark" : "light";
    document.documentElement.dataset.theme = theme;
    window.localStorage.setItem("oaw-theme", theme);
    set({ theme });
  },

  generateStressWorld: (count = 1500) => {
    set({ stressCards: makeStressCards(count) });
    get().pushToast({
      tone: "neutral",
      title: `${count.toLocaleString()} synthetic cards generated`,
      detail: "Only cards inside the active chunk ring become live canvas nodes.",
    });
  },

  clearStressWorld: () => set({ stressCards: [] }),

  pushToast: (message) => {
    toastSequence += 1;
    set((state) => ({
      toasts: [...state.toasts, { ...message, id: `toast-${toastSequence}` }].slice(-4),
    }));
  },

  dismissToast: (id) => set((state) => ({
    toasts: state.toasts.filter((toast) => toast.id !== id),
  })),

  undo: () => {
    if (get().historyBusy) return Promise.resolve();
    set({ historyBusy: true });
    return withHistoryTransaction(async () => {
      const operation = get().undoStack.at(-1);
      if (!operation) {
        set({ historyBusy: false });
        return;
      }
      try {
      const applyUndo = async () => {
        if (
          operation.kind === "legion-instantiated"
          && get().undoStack.at(-1)?.id !== operation.id
        ) return;
      switch (operation.kind) {
        case "group-dissolved": {
          const group = await restoreCard(operation.group);
          let members: WorldCard[];
          try { members = operation.members.length ? await applyCardPositions(operation.members, true) : []; }
          catch (error) { await worldApi.deleteNode(group.id); throw error; }
          const edges: WorldEdge[] = [];
          for (const edge of operation.edges) edges.push(await worldApi.createEdge(copyEdge(edge)));
          set((state) => ({ cards: mergeCards(state.cards, [group, ...members]), edges: mergeEdges(state.edges, edges) }));
          break;
        }
        case "group-formed": {
          operation.group = await snapshotCardForHistory(operation.group);
          const restored = await applyCardPositions(operation.before, true);
          try { await worldApi.deleteNode(operation.group.id); }
          catch (error) { await applyCardPositions(operation.after, true); throw error; }
          set((state) => ({ cards: mergeCards(state.cards.filter((c) => c.id !== operation.group.id), restored), selectedCardIds: restored.map((c) => c.id), selectionRevision: state.selectionRevision + 1 }));
          break;
        }
        case "card-created": {
          const expanded = operation.cards.flatMap((card) => [card, ...ownedDescendants(get().cards, card.id)]);
          operation.cards = await Promise.all(parentFirst([...new Map(expanded.map((card) => [card.id, card])).values()]).map(snapshotCardForHistory));
          if (operation.cards.some((card) => card.equipment || isContainer(card, get().catalog))) {
            await worldApi.deleteNodes(operation.cards.filter((card) => !card.ephemeral).map((card) => card.id));
          } else {
            for (const card of operation.cards) if (!card.ephemeral) await worldApi.deleteNode(card.id);
          }
          const ids = new Set(operation.cards.map((card) => card.id));
          set((state) => ({
            cards: state.cards.filter((card) => !ids.has(card.id)),
            stressCards: state.stressCards.filter((card) => !ids.has(card.id)),
            edges: state.edges.filter((edge) => !ids.has(edge.source) && !ids.has(edge.target)),
            selectedCardIds: state.selectedCardIds.filter((id) => !ids.has(id)),
          }));
          break;
        }
        case "cards-deleted": {
          const restored: WorldCard[] = [];
          for (const card of parentFirst(operation.cards)) {
            restored.push(card.ephemeral ? copyCard(card) : await restoreCard(card));
          }
          const restoredEdges: WorldEdge[] = [];
          for (const edge of operation.edges) {
            if (!isEquipmentConnection(edge.source, edge.target, [...get().cards, ...restored])) {
              restoredEdges.push(await worldApi.createEdge(copyEdge(edge)));
            }
          }
          set((state) => ({
            cards: mergeCards(state.cards, restored.filter((card) => !card.ephemeral)),
            stressCards: mergeCards(state.stressCards, restored.filter((card) => card.ephemeral)),
            edges: mergeEdges(state.edges, restoredEdges),
          }));
          break;
        }
        case "card-updated": {
          const restored = operation.before.ephemeral
            ? copyCard(operation.before)
            : await worldApi.updateNode(operation.before.id, cardRestorePatch(operation.before));
          set((state) => ({
            cards: state.cards.map((card) => card.id === restored.id ? restored : card),
            stressCards: state.stressCards.map((card) => card.id === restored.id ? restored : card),
          }));
          break;
        }
        case "cards-updated": {
          const restored = await applyCardPositions(operation.before, operation.membership);
          const byId = new Map(restored.map((card) => [card.id, card]));
          set((state) => ({
            cards: state.cards.map((card) => byId.get(card.id) ?? card),
            stressCards: state.stressCards.map((card) => byId.get(card.id) ?? card),
          }));
          break;
        }
        case "legion-instantiated": {
          await worldApi.deleteNodes(operation.cards.map((card) => card.id));
          const ids = new Set(operation.cards.map((card) => card.id));
          set((state) => ({
            cards: state.cards.filter((card) => !ids.has(card.id)),
            edges: state.edges.filter((edge) => !ids.has(edge.source) && !ids.has(edge.target)),
            selectedCardIds: state.selectedCardIds.filter((id) => !ids.has(id)),
            selectedEdgeId: operation.edges.some((edge) => edge.id === state.selectedEdgeId)
              ? undefined
              : state.selectedEdgeId,
          }));
          break;
        }
        case "edge-created":
          await worldApi.deleteEdge(operation.edge.id);
          set((state) => ({
            edges: state.edges.filter((edge) => edge.id !== operation.edge.id),
            selectedEdgeId: state.selectedEdgeId === operation.edge.id ? undefined : state.selectedEdgeId,
          }));
          break;
        case "edge-deleted": {
          const edge = await worldApi.createEdge(copyEdge(operation.edge));
          set((state) => ({ edges: mergeEdges(state.edges, [edge]) }));
          break;
        }
        case "edge-updated": {
          const edge = await worldApi.updateEdge(operation.before.id, {
            relationship: operation.before.relationship,
            direction: operation.before.direction,
          });
          set((state) => ({
            edges: state.edges.map((item) => item.id === edge.id ? edge : item),
          }));
          break;
        }
      }
      markWorldMutation();
      set((state) => ({
        undoStack: state.undoStack.slice(0, -1),
        redoStack: [...state.redoStack, operation],
      }));
      get().pushToast({ tone: "neutral", title: `${operation.label} undone` });
      };
      if (operation.kind === "legion-instantiated") {
        await withLegionOperation(operation.legionId, applyUndo);
      } else {
        await applyUndo();
      }
      } catch (error) {
        get().pushToast({ tone: "error", title: "Undo could not be completed", detail: apiErrorMessage(error) });
      } finally {
        set({ historyBusy: false });
      }
    });
  },

  redo: () => {
    if (get().historyBusy) return Promise.resolve();
    set({ historyBusy: true });
    return withHistoryTransaction(async () => {
      const operation = get().redoStack.at(-1);
      if (!operation) {
        set({ historyBusy: false });
        return;
      }
      try {
      const applyRedo = async () => {
        if (
          operation.kind === "legion-instantiated"
          && get().redoStack.at(-1)?.id !== operation.id
        ) return;
        if (
          operation.kind === "legion-instantiated"
          && !get().legions.some((legion) => legion.id === operation.legionId)
        ) {
          set((state) => ({
            redoStack: state.redoStack.filter((item) => item.id !== operation.id),
          }));
          get().pushToast({
            tone: "error",
            title: "Legion redo is unavailable",
            detail: "The Legion card was deleted, so this deployment cannot be recreated.",
          });
          return;
        }
      let redoneOperation = operation;
      switch (operation.kind) {
        case "group-dissolved": {
          operation.group = await snapshotCardForHistory(operation.group);
          const detached = operation.members.length ? await applyCardPositions(operation.members.map((c) => ({ ...c, parent_id: null })), true) : [];
          try { await worldApi.deleteNode(operation.group.id); }
          catch (error) { if (operation.members.length) await applyCardPositions(operation.members, true); throw error; }
          set((state) => ({ cards: mergeCards(state.cards.filter((c) => c.id !== operation.group.id), detached),
            edges: state.edges.filter((e) => e.source !== operation.group.id && e.target !== operation.group.id),
            selectedCardIds: detached.map((c) => c.id), selectionRevision: state.selectionRevision + 1 }));
          break;
        }
        case "group-formed": {
          const group = await restoreCard(operation.group);
          let members: WorldCard[];
          try { members = await applyCardPositions(operation.after, true); }
          catch (error) { await worldApi.deleteNode(group.id); throw error; }
          set((state) => ({ cards: mergeCards(state.cards, [group, ...members]) }));
          break;
        }
        case "card-created": {
          const restored: WorldCard[] = [];
          for (const card of parentFirst(operation.cards)) {
            restored.push(card.ephemeral ? copyCard(card) : await restoreCard(card));
          }
          set((state) => ({
            cards: mergeCards(state.cards, restored.filter((card) => !card.ephemeral)),
            stressCards: mergeCards(state.stressCards, restored.filter((card) => card.ephemeral)),
          }));
          break;
        }
        case "cards-deleted": {
          if (operation.cards.some((card) => card.equipment || isContainer(card, get().catalog))) {
            await worldApi.deleteNodes(operation.cards.filter((card) => !card.ephemeral).map((card) => card.id));
          } else {
            for (const card of operation.cards) if (!card.ephemeral) await worldApi.deleteNode(card.id);
          }
          const ids = new Set(operation.cards.map((card) => card.id));
          set((state) => ({
            cards: state.cards.filter((card) => !ids.has(card.id)),
            stressCards: state.stressCards.filter((card) => !ids.has(card.id)),
            edges: state.edges.filter((edge) => !ids.has(edge.source) && !ids.has(edge.target)),
            selectedCardIds: state.selectedCardIds.filter((id) => !ids.has(id)),
          }));
          break;
        }
        case "card-updated": {
          const restored = operation.after.ephemeral
            ? copyCard(operation.after)
            : await worldApi.updateNode(operation.after.id, cardRestorePatch(operation.after));
          set((state) => ({
            cards: state.cards.map((card) => card.id === restored.id ? restored : card),
            stressCards: state.stressCards.map((card) => card.id === restored.id ? restored : card),
          }));
          break;
        }
        case "cards-updated": {
          const restored = await applyCardPositions(operation.after, operation.membership);
          const byId = new Map(restored.map((card) => [card.id, card]));
          set((state) => ({
            cards: state.cards.map((card) => byId.get(card.id) ?? card),
            stressCards: state.stressCards.map((card) => byId.get(card.id) ?? card),
          }));
          break;
        }
        case "legion-instantiated": {
          const instance = await worldApi.instantiateLegion(operation.legionId, operation.position);
          const cards = instance.nodes.map(copyCard);
          const edges = instance.edges.map(copyEdge);
          redoneOperation = { ...operation, cards, edges };
          set((state) => ({
            cards: mergeCards(state.cards, instance.nodes),
            edges: mergeEdges(state.edges, instance.edges),
            selectedCardIds: instance.nodes.map((card) => card.id),
            selectionRevision: state.selectionRevision + 1,
            selectedEdgeId: undefined,
          }));
          break;
        }
        case "edge-created": {
          const edge = await worldApi.createEdge(copyEdge(operation.edge));
          set((state) => ({ edges: mergeEdges(state.edges, [edge]) }));
          break;
        }
        case "edge-deleted":
          await worldApi.deleteEdge(operation.edge.id);
          set((state) => ({
            edges: state.edges.filter((edge) => edge.id !== operation.edge.id),
            selectedEdgeId: state.selectedEdgeId === operation.edge.id ? undefined : state.selectedEdgeId,
          }));
          break;
        case "edge-updated": {
          const edge = await worldApi.updateEdge(operation.after.id, {
            relationship: operation.after.relationship,
            direction: operation.after.direction,
          });
          set((state) => ({
            edges: state.edges.map((item) => item.id === edge.id ? edge : item),
          }));
          break;
        }
      }
      markWorldMutation();
      set((state) => ({
        redoStack: state.redoStack.slice(0, -1),
        undoStack: appendHistory(state.undoStack, redoneOperation),
      }));
      get().pushToast({ tone: "neutral", title: `${operation.label} redone` });
      };
      if (operation.kind === "legion-instantiated") {
        await withLegionOperation(operation.legionId, applyRedo);
      } else {
        await applyRedo();
      }
      } catch (error) {
        get().pushToast({ tone: "error", title: "Redo could not be completed", detail: apiErrorMessage(error) });
      } finally {
        set({ historyBusy: false });
      }
    });
  },
}), {
  name: "oaw-canvas-viewport-v1",
  partialize: (state) => ({ viewport: state.viewport }),
}));
