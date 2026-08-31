import { create } from "zustand";
import { apiErrorMessage, normalizeCard, resourceContentUrl, worldApi, type CardCreateInput } from "../api/client";
import type {
  CardType,
  EdgeDirection,
  FlowViewportState,
  Relationship,
  RuntimeEvent,
  ToastMessage,
  WorldCard,
  WorldEdge,
  WorldPosition,
  WorldSnapshot,
} from "../types/world";
import { getViewportChunkKeys, viewportCenterToWorld } from "./chunks";
import { buildCardDraft, expandedPatch, makeStressCards, mergeCardPatch } from "./helpers";
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

export interface PendingConnection {
  source: string;
  target: string;
  options: RelationshipOption[];
}

type RestorableCard = WorldCard & {
  restoreContent?: string;
  restoreImageData?: string;
  restoreImageMediaType?: string;
};

export type WorldHistoryOperation =
  | { id: number; label: string; kind: "card-created"; cards: RestorableCard[] }
  | { id: number; label: string; kind: "cards-deleted"; cards: RestorableCard[]; edges: WorldEdge[] }
  | { id: number; label: string; kind: "card-updated"; before: WorldCard; after: WorldCard }
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
    position: { ...card.position },
    size: { ...card.size },
    expanded: card.expanded,
    status: card.status,
    config: { ...card.config },
  };
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

async function snapshotCardForHistory(card: WorldCard): Promise<RestorableCard> {
  const snapshot = copyCard(card);
  try {
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
  edges: WorldEdge[];
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

  initialize: () => Promise<void>;
  refreshWorld: () => Promise<void>;
  ensureChunks: (keys: string[]) => Promise<void>;
  setViewport: (viewport: FlowViewportState) => void;
  createCard: (type: CardType, position?: WorldPosition) => Promise<WorldCard | undefined>;
  updateCard: (
    id: string,
    patch: Partial<Omit<WorldCard, "id" | "type">>,
  ) => Promise<void>;
  toggleCardExpanded: (id: string) => Promise<void>;
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
  startSandbox: (id: string) => Promise<void>;
  stopSandbox: (id: string) => Promise<void>;
  executeSandbox: (id: string, command: string) => Promise<void>;
  ingestEvent: (event: RuntimeEvent) => void;
  setSocketState: (state: SocketState) => void;
  toggleActivity: () => void;
  setActivityOpen: (open: boolean) => void;
  toggleSettings: () => void;
  saveModelSettings: (settings: ModelSettings) => Promise<boolean>;
  restoreModelConnection: () => Promise<void>;
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

export const useWorldStore = create<WorldState>((set, get) => ({
  cards: [],
  edges: [],
  stressCards: [],
  activeChunkKeys: getViewportChunkKeys(INITIAL_VIEWPORT),
  loadedChunkKeys: [],
  loadingChunkKeys: [],
  viewport: INITIAL_VIEWPORT,
  syncState: "loading",
  socketState: "connecting",
  selectedCardIds: [],
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

  initialize: async () => {
    const keys = get().activeChunkKeys;
    set({ syncState: "loading", syncError: undefined });
    try {
      const snapshot = await worldApi.getWorld(keys);
      set({
        cards: snapshot.nodes,
        edges: snapshot.edges,
        loadedChunkKeys: [...new Set([...keys, ...chunkKeysFromSnapshot(snapshot)])],
        syncState: "online",
        syncError: undefined,
      });
      await get().restoreModelConnection();
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
    set({ syncState: "syncing" });
    try {
      const snapshot = await worldApi.getWorld(get().activeChunkKeys);
      set((state) => ({
        cards: mergeCards(state.cards, snapshot.nodes),
        edges: snapshot.edges,
        loadedChunkKeys: [
          ...new Set([
            ...state.loadedChunkKeys,
            ...state.activeChunkKeys,
            ...chunkKeysFromSnapshot(snapshot),
          ]),
        ],
        syncState: "online",
        syncError: undefined,
      }));
    } catch (error) {
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

  createCard: async (type, position) => {
    if (get().syncState === "offline") {
      get().pushToast({
        tone: "error",
        title: "Creation paused",
        detail: "Reconnect the world service before placing a persistent object.",
      });
      return undefined;
    }
    const finalPosition = position ?? viewportCenterToWorld(get().viewport);
    const draft = buildCardDraft(type, finalPosition);
    const configuredDraft = type === "agent" && get().modelSettings.models[0]
      ? { ...draft, config: { ...draft.config, model: get().modelSettings.models[0] } }
      : draft;
    set({ syncState: "syncing" });
    try {
      const card = await worldApi.createNode(configuredDraft);
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
  },

  updateCard: async (id, patch) => {
    const current = get().cards.find((card) => card.id === id) ?? get().stressCards.find((card) => card.id === id);
    if (!current) return;
    if (current.ephemeral) {
      const optimistic = mergeCardPatch(current, patch);
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
    set((state) => ({
      cards: state.cards.map((card) => (card.id === id ? optimistic : card)),
      syncState: "syncing",
    }));
    try {
      const authoritative = await worldApi.updateNode(id, patch);
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
      set((state) => ({
        cards: state.cards.map((card) => (card.id === id ? current : card)),
        syncState: "online",
      }));
      get().pushToast({ tone: "error", title: "Change was not saved", detail: apiErrorMessage(error) });
    }
  },

  toggleCardExpanded: async (id) => {
    const card = get().cards.find((item) => item.id === id) ?? get().stressCards.find((item) => item.id === id);
    if (card) await get().updateCard(id, expandedPatch(card));
  },

  deleteCard: async (id) => get().deleteCards([id]),

  deleteCards: async (ids) => {
    const requested = new Set(ids);
    const cards = [...get().cards, ...get().stressCards].filter((card) => requested.has(card.id));
    if (cards.length === 0) return;

    const snapshots = new Map<string, RestorableCard>();
    await Promise.all(cards.map(async (card) => snapshots.set(card.id, await snapshotCardForHistory(card))));

    const removed: WorldCard[] = [];
    for (const card of cards) {
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
    const attachedEdges = get().edges.filter(
      (edge) => removedIds.has(edge.source) || removedIds.has(edge.target),
    );
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
  },

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
      source,
      target,
      sourceCard?.type,
      targetCard?.type,
      get().edges,
    );
    if (!validation.valid || !source || !target) {
      get().pushToast({
        tone: "error",
        title: "That relationship is not valid",
        detail: validation.reason,
      });
      return;
    }
    set({ pendingConnection: { source, target, options: validation.options } });
  },

  closeConnectionDialog: () => set({ pendingConnection: undefined }),

  createConnection: async (relationship, direction = "forward") => {
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
  },

  selectEdge: (id) => set({ selectedEdgeId: id }),
  selectCards: (ids) => set({ selectedCardIds: [...new Set(ids)] }),

  updateSelectedEdge: async (patch) => {
    const id = get().selectedEdgeId;
    if (!id) return;
    const previous = get().edges.find((edge) => edge.id === id);
    if (!previous) return;
    try {
      const edge = await worldApi.updateEdge(
        id,
        typeof patch === "string" ? { relationship: patch } : patch,
      );
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
  },

  deleteSelectedEdge: async () => {
    const id = get().selectedEdgeId;
    if (!id) return;
    const edge = get().edges.find((item) => item.id === id);
    if (!edge) return;
    try {
      await worldApi.deleteEdge(id);
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
  },

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

  startSandbox: async (id) => {
    try {
      await worldApi.startSandbox(id);
      set((state) => ({
        cards: state.cards.map((card) =>
          card.id === id ? mergeCardPatch(card, { status: "ready" }) : card,
        ),
      }));
    } catch (error) {
      get().pushToast({ tone: "error", title: "Sandbox did not start", detail: apiErrorMessage(error) });
    }
  },

  stopSandbox: async (id) => {
    try {
      await worldApi.stopSandbox(id);
      set((state) => ({
        cards: state.cards.map((card) =>
          card.id === id
            ? mergeCardPatch(card, { status: "stopped", config: { active_command: "" } })
            : card,
        ),
      }));
    } catch (error) {
      get().pushToast({ tone: "error", title: "Sandbox did not stop", detail: apiErrorMessage(error) });
    }
  },

  executeSandbox: async (id, command) => {
    const previous = get().cards.find((card) => card.id === id);
    set((state) => ({
      cards: state.cards.map((card) =>
        card.id === id
          ? mergeCardPatch(card, {
              status: "running",
              config: { active_command: command },
            })
          : card,
      ),
      activityOpen: true,
    }));
    try {
      const result = await worldApi.executeSandbox(id, command);
      set((state) => ({
        cards: state.cards.map((card) =>
          card.id === id
            ? mergeCardPatch(card, {
                status: "ready",
                config: {
                  active_command: "",
                  output: state.socketState === "live"
                    ? card.config.output
                    : [
                        ...(Array.isArray(card.config.output) ? card.config.output : []),
                        ...String(result.stdout ?? "").split(/\r?\n/).filter(Boolean),
                        ...String(result.stderr ?? "").split(/\r?\n/).filter(Boolean).map((line) => `! ${line}`),
                      ].slice(-100),
                },
              })
            : card,
        ),
      }));
    } catch (error) {
      set((state) => ({
        cards: state.cards.map((card) =>
          card.id === id
            ? mergeCardPatch(card, {
                status: previous?.status ?? "ready",
                config: { active_command: "" },
              })
            : card,
        ),
      }));
      get().pushToast({ tone: "error", title: "Command was rejected", detail: apiErrorMessage(error) });
    }
  },

  ingestEvent: (event) => {
    const nodeId = event.node_id ?? event.agent_id ?? event.sandbox_id ?? event.resource_id;
    const normalizedType = event.type.replace(/[.\s-]/g, "_").toLowerCase();
    const outputText = String(
      event.payload.error ?? event.payload.text ?? event.payload.output ?? event.message ?? "",
    );
    if (normalizedType.includes("runtime_error")) {
      const model = String(get().cards.find((card) => card.id === nodeId)?.config.model ?? "configured model");
      get().pushToast({ tone: "error", ...describeRuntimeError(outputText, model) });
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
            else if (normalizedType.includes("command_finished")) status = "ready";
            else if (normalizedType.includes("error")) status = "error";

            const shouldAppend =
              normalizedType.includes("stdout") ||
              normalizedType.includes("stderr") ||
              normalizedType.includes("tool_") ||
              normalizedType.includes("command_") ||
              normalizedType.includes("agent_") ||
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
  },

  setSocketState: (socketState) => set({ socketState }),
  toggleActivity: () => set((state) => ({ activityOpen: !state.activityOpen })),
  setActivityOpen: (activityOpen) => set({ activityOpen }),
  toggleSettings: () => set((state) => ({ settingsOpen: !state.settingsOpen })),

  saveModelSettings: async (settings) => {
    const normalized: ModelSettings = {
      baseUrl: settings.baseUrl.trim(),
      apiKey: settings.apiKey,
      models: normalizeModelList(settings.models),
    };
    if (normalized.models.length === 0) {
      get().pushToast({ tone: "error", title: "Add at least one model", detail: "Agent cards need a model to call." });
      return false;
    }
    try {
      await worldApi.configureLlm({ base_url: normalized.baseUrl, api_key: normalized.apiKey });
      set({ modelSettings: normalized });
      persistModelSettings(normalized);
      get().pushToast({ tone: "success", title: "ADK model connection saved", detail: "New Agent runs use this connection when ADK selects LiteLLM." });
      return true;
    } catch (error) {
      get().pushToast({ tone: "error", title: "Model connection was not applied", detail: apiErrorMessage(error) });
      return false;
    }
  },

  restoreModelConnection: async () => {
    const settings = get().modelSettings;
    if (!settings.baseUrl && !settings.apiKey) return;
    try {
      await worldApi.configureLlm({ base_url: settings.baseUrl, api_key: settings.apiKey });
    } catch (error) {
      get().pushToast({
        tone: "error",
        title: "Model connection was not restored",
        detail: apiErrorMessage(error),
      });
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

  undo: async () => {
    const operation = get().undoStack.at(-1);
    if (!operation || get().historyBusy) return;
    set({ historyBusy: true });
    try {
      switch (operation.kind) {
        case "card-created": {
          for (const card of operation.cards) if (!card.ephemeral) await worldApi.deleteNode(card.id);
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
          for (const card of operation.cards) {
            restored.push(card.ephemeral ? copyCard(card) : await worldApi.createNode(restoreCardInput(card)));
          }
          const restoredEdges: WorldEdge[] = [];
          for (const edge of operation.edges) restoredEdges.push(await worldApi.createEdge(copyEdge(edge)));
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
      set((state) => ({
        undoStack: state.undoStack.slice(0, -1),
        redoStack: [...state.redoStack, operation],
      }));
      get().pushToast({ tone: "neutral", title: `${operation.label} undone` });
    } catch (error) {
      get().pushToast({ tone: "error", title: "Undo could not be completed", detail: apiErrorMessage(error) });
    } finally {
      set({ historyBusy: false });
    }
  },

  redo: async () => {
    const operation = get().redoStack.at(-1);
    if (!operation || get().historyBusy) return;
    set({ historyBusy: true });
    try {
      switch (operation.kind) {
        case "card-created": {
          const restored: WorldCard[] = [];
          for (const card of operation.cards) {
            restored.push(card.ephemeral ? copyCard(card) : await worldApi.createNode(restoreCardInput(card)));
          }
          set((state) => ({
            cards: mergeCards(state.cards, restored.filter((card) => !card.ephemeral)),
            stressCards: mergeCards(state.stressCards, restored.filter((card) => card.ephemeral)),
          }));
          break;
        }
        case "cards-deleted": {
          for (const card of operation.cards) if (!card.ephemeral) await worldApi.deleteNode(card.id);
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
      set((state) => ({
        redoStack: state.redoStack.slice(0, -1),
        undoStack: appendHistory(state.undoStack, operation),
      }));
      get().pushToast({ tone: "neutral", title: `${operation.label} redone` });
    } catch (error) {
      get().pushToast({ tone: "error", title: "Redo could not be completed", detail: apiErrorMessage(error) });
    } finally {
      set({ historyBusy: false });
    }
  },
}));
