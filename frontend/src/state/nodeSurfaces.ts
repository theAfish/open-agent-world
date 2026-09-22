import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import { profileStorage } from "./profileStorage";
import { clampSurfaceSize, type SurfaceSizes, type SurfaceSize } from "./surfaceGeometry";
export { NODE_SURFACE_SIZE, surfaceSizeFor, type SurfaceSize, type SurfaceSizes } from "./surfaceGeometry";
import type { CardType, LegionNodePresentation, NodePresentation, NodeSurfaceLevel, PluginCatalog, WorldCard } from "../types/world";

export type { NodeSurfaceLevel } from "../types/world";

/** Shared by the visible card boundary and relationship geometry. */
export const NODE_SURFACE_RADIUS: Record<NodeSurfaceLevel, number> = {
  node: 48, preview: 14, inspector: 24, workspace: 20,
};

export interface NodeSurfaceSupport {
  node?: boolean;
  preview: boolean;
  inspector: boolean;
  workspace: boolean;
}

export const NODE_SURFACE_SUPPORT: Record<CardType, NodeSurfaceSupport> = {
  agent: { preview: true, inspector: true, workspace: true },
  conversation: { preview: true, inspector: true, workspace: true },
  text: { preview: true, inspector: true, workspace: false },
  image: { preview: true, inspector: true, workspace: false },
  sandbox: { preview: true, inspector: true, workspace: true },
};

const GENERIC_SURFACE_SUPPORT: NodeSurfaceSupport = {
  preview: true,
  inspector: true,
  workspace: false,
};

export function nodeSurfaceSupport(
  type: CardType,
  catalog?: PluginCatalog,
): NodeSurfaceSupport {
  const { states } = nodePresentation(type, catalog);
  return { node: states.includes("node"), preview: states.includes("preview"),
    inspector: states.includes("inspector"), workspace: states.includes("workspace") };
}

const SURFACE_ORDER: readonly NodeSurfaceLevel[] = ["node", "preview", "inspector", "workspace"];
// Only used before a node's catalog definition is available (including synthetic nodes).
const UNREGISTERED_PRESENTATION: NodePresentation = { states: SURFACE_ORDER, initial: "preview", open: "inspector" };

export function nodePresentation(type: CardType, catalog?: PluginCatalog): NodePresentation {
  const definition = catalog?.node_types.find(item => item.id === type);
  if (definition?.presentation) return definition.presentation;
  const support = definition?.surfaces ?? NODE_SURFACE_SUPPORT[type] ?? GENERIC_SURFACE_SUPPORT;
  const states = SURFACE_ORDER.filter(level => level === "node" || support[level]);
  return { states, initial: support.preview ? "preview" : "node",
    open: support.inspector ? "inspector" : support.workspace ? "workspace" : support.preview ? "preview" : "node" };
}

function supportedLevel(presentation: NodePresentation, level: NodeSurfaceLevel): NodeSurfaceLevel {
  return presentation.states.includes(level) ? level : presentation.open;
}

function baseLevel(presentation: NodePresentation, preferred?: NodeSurfaceLevel): NodeSurfaceLevel {
  if (preferred && (preferred === "node" || preferred === "preview") && presentation.states.includes(preferred)) return preferred;
  return (["preview", "node"] as const).find(level => presentation.states.includes(level))
    ?? SURFACE_ORDER.find(level => presentation.states.includes(level))!;
}

/** Closing moves to the next smaller supported surface, restoring the chosen compact form. */
export function collapsedSurface(presentation: NodePresentation, current: NodeSurfaceLevel, base?: NodeSurfaceLevel): NodeSurfaceLevel {
  if (current === "workspace" && presentation.states.includes("inspector")) return "inspector";
  if (current === "workspace" || current === "inspector") return baseLevel(presentation, base);
  if (current === "preview" && presentation.states.includes("node")) return "node";
  return current;
}

interface NodeSurfaceState {
  capturePresentation: (cards: readonly WorldCard[], catalog: PluginCatalog) => Record<string, LegionNodePresentation>;
  restorePresentation: (cards: readonly WorldCard[], catalog: PluginCatalog, presentation?: Record<string, LegionNodePresentation>) => void;
  presentations: Record<string, NodePresentation>;
  syncCards: (cards: readonly (Pick<WorldCard, "id" | "type"> & Partial<Pick<WorldCard, "parent_id">>)[], catalog: PluginCatalog) => void;
  surfaceSizes: SurfaceSizes;
  resizeSurface: (nodeId: string, level: NodeSurfaceLevel, size: SurfaceSize) => void;
  surfaceLevels: Record<string, NodeSurfaceLevel>;
  connectingNodeId?: string;
  dragging: boolean;
  setDragging: (dragging: boolean) => void;
  baseLevels: Record<string, NodeSurfaceLevel>;
  drafts: Record<string, string>;
  maximizedWorkspaces: Record<string, boolean>;
  showPreview: (nodeId: string) => void;
  hidePreview: (nodeId: string) => void;
  openInspector: (nodeId: string) => void;
  openPrimary: (nodeId: string) => void;
  closeInspector: (nodeId?: string) => void;
  dismiss: (nodeId?: string) => void;
  openWorkspace: (nodeId: string) => void;
  closeWorkspace: (nodeId?: string) => void;
  closeExpanded: () => void;
  setDraft: (nodeId: string, value: string) => void;
  toggleWorkspaceMaximized: (nodeId: string) => void;
  beginConnection: (nodeId: string) => void;
  endConnection: () => void;
}

function presentationFor(state: NodeSurfaceState, id: string) {
  return state.presentations[id] ?? UNREGISTERED_PRESENTATION;
}

function openSurface(state: NodeSurfaceState, id: string, requested?: NodeSurfaceLevel) {
  if (state.connectingNodeId || state.dragging) return state;
  const presentation = presentationFor(state, id);
  const current = surfaceLevelForNode(id, state.surfaceLevels);
  const level = supportedLevel(presentation, requested ?? presentation.open);
  return {
    baseLevels: { ...state.baseLevels, [id]: baseLevel(presentation,
      current === "node" || current === "preview" ? current : state.baseLevels[id]) },
    surfaceLevels: { ...state.surfaceLevels, [id]: level },
  };
}

function closeSurfaces(state: NodeSurfaceState, target: NodeSurfaceLevel, nodeId?: string, liveOnly = false) {
  if (state.connectingNodeId || state.dragging) return state;
  return { surfaceLevels: Object.fromEntries(Object.entries(state.surfaceLevels).map(([id, level]) => [
    id, level === target && (!nodeId || nodeId === id) && (!liveOnly || state.presentations[id])
      ? collapsedSurface(presentationFor(state, id), level, state.baseLevels[id]) : level,
  ])) };
}

export const useNodeSurfaceStore = create<NodeSurfaceState>()(persist((set, get) => ({
  capturePresentation: (cards, catalog) => {
    const state = get();
    return Object.fromEntries(cards.map(card => [card.id, {
      level: state.surfaceLevels[card.id] ?? nodePresentation(card.type, catalog).initial,
      base_level: state.baseLevels[card.id] === 'node' ? 'node' : 'preview',
      ...(state.surfaceSizes[card.id] ? { surface_sizes: structuredClone(state.surfaceSizes[card.id]) } : {}),
    }]));
  },
  restorePresentation: (cards, catalog, saved = {}) => set(state => {
    const surfaceLevels = { ...state.surfaceLevels }, baseLevels = { ...state.baseLevels }, surfaceSizes = { ...state.surfaceSizes };
    for (const card of cards) {
      const value = saved[card.id];
      if (!value) continue;
      const presentation = nodePresentation(card.type, catalog);
      surfaceLevels[card.id] = supportedLevel(presentation, value.level);
      baseLevels[card.id] = baseLevel(presentation, value.base_level ?? undefined);
      surfaceSizes[card.id] = { ...(value.workspace_size ? { workspace: value.workspace_size } : {}), ...value.surface_sizes };
    }
    return { surfaceLevels, baseLevels, surfaceSizes };
  }),
  presentations: {},
  syncCards: (cards, catalog) => set(state => {
    const presentations: Record<string, NodePresentation> = {};
    const surfaceLevels = { ...state.surfaceLevels }, baseLevels = { ...state.baseLevels };
    for (const card of cards) {
      // Wait for the catalog instead of persisting a guessed initial surface.
      if (!catalog.node_types.some(type => type.id === card.type)) continue;
      const presentation = nodePresentation(card.type, catalog);
      presentations[card.id] = presentation;
      const definition = catalog.node_types.find(type => type.id === card.type)!;
      const initial = card.parent_id && !definition.container && presentation.states.includes("node")
        ? "node" : presentation.initial;
      surfaceLevels[card.id] = supportedLevel(presentation, surfaceLevels[card.id] ?? initial);
      baseLevels[card.id] = baseLevel(presentation, baseLevels[card.id] ?? surfaceLevels[card.id]);
    }
    return { presentations, surfaceLevels, baseLevels };
  }),
  surfaceSizes: {},
  resizeSurface: (nodeId, level, size) => set(state => {
    if (!Number.isFinite(size.width) || !Number.isFinite(size.height)) return state;
    return { surfaceSizes: { ...state.surfaceSizes, [nodeId]: {
      ...state.surfaceSizes[nodeId], [level]: clampSurfaceSize(level, size),
    } } };
  }),
  surfaceLevels: {},
  drafts: {},
  maximizedWorkspaces: {},

  dragging: false,
  baseLevels: {},
  setDragging: (dragging) => set({ dragging }),

  showPreview: (nodeId) => set((state) => {
    if (state.connectingNodeId || state.dragging) return state;
    if (["workspace", "inspector"].includes(state.surfaceLevels[nodeId])) return state;
    if (!presentationFor(state, nodeId).states.includes("preview")) return state;
    return {
      surfaceLevels: { ...state.surfaceLevels, [nodeId]: "preview" },
      baseLevels: { ...state.baseLevels, [nodeId]: "preview" },
    };
  }),

  hidePreview: (nodeId) => set((state) => {
    if (state.connectingNodeId || state.dragging) return state;
    if (["workspace", "inspector"].includes(state.surfaceLevels[nodeId])) return state;
    if (!presentationFor(state, nodeId).states.includes("node")) return state;
    return {
      surfaceLevels: { ...state.surfaceLevels, [nodeId]: "node" },
      baseLevels: { ...state.baseLevels, [nodeId]: "node" },
    };
  }),

  openPrimary: (nodeId) => set(state => openSurface(state, nodeId)),
  openInspector: (nodeId) => set(state => openSurface(state, nodeId, "inspector")),

  closeInspector: (nodeId) => set(state => closeSurfaces(state, "inspector", nodeId)),

  dismiss: (nodeId) => set((state) => {
    if (!nodeId) {
      return {
        surfaceLevels: { ...state.surfaceLevels, ...Object.fromEntries(Object.entries(state.presentations).map(([id, p]) => [id, baseLevel(p, state.baseLevels[id])])) },
        connectingNodeId: undefined,
      };
    }
    return {
      surfaceLevels: state.presentations[nodeId]
        ? { ...state.surfaceLevels, [nodeId]: baseLevel(state.presentations[nodeId], state.baseLevels[nodeId]) }
        : Object.fromEntries(Object.entries(state.surfaceLevels).filter(([id]) => id !== nodeId)),
      connectingNodeId: state.connectingNodeId === nodeId ? undefined : state.connectingNodeId,
    };
  }),

  openWorkspace: (nodeId) => set(state => openSurface(state, nodeId, "workspace")),

  closeWorkspace: (nodeId) => set(state => closeSurfaces(state, "workspace", nodeId)),
  closeExpanded: () => set(state => {
    const target = (["workspace", "inspector"] as const).find(level =>
      Object.entries(state.surfaceLevels).some(([id, current]) => state.presentations[id] && current === level
        && collapsedSurface(presentationFor(state, id), current, state.baseLevels[id]) !== current));
    return target ? closeSurfaces(state, target, undefined, true) : state;
  }),

  setDraft: (nodeId, value) => set((state) => ({
    drafts: { ...state.drafts, [nodeId]: value },
  })),

  toggleWorkspaceMaximized: (nodeId) => set((state) => ({
    maximizedWorkspaces: {
      ...state.maximizedWorkspaces,
      [nodeId]: !state.maximizedWorkspaces[nodeId],
    },
  })),

  beginConnection: (nodeId) => set({ connectingNodeId: nodeId }),
  endConnection: () => set({ connectingNodeId: undefined }),
}), {
  name: "oaw-node-surfaces-v1",
  storage: createJSONStorage(() => profileStorage),
  version: 4,
  migrate: (persisted, version) => {
    const saved = persisted as { workspaceSizes?: Record<string, SurfaceSize>; surfaceSizes?: SurfaceSizes };
    const { workspaceSizes: legacySizes, ...rest } = saved;
    const surfaceSizes = saved.surfaceSizes ?? Object.fromEntries(Object.entries(legacySizes ?? {}).map(([id, size]) => [id, { workspace: clampSurfaceSize('workspace', size) }]));
    if (version >= 3) return { ...rest, surfaceSizes };
    if (version === 2) return { ...rest, surfaceSizes, baseLevels: {} };
    const legacy = persisted as Partial<{
      activeNodeId: string;
      level: NodeSurfaceLevel;
      inspectorNodeIds: string[];
      maximizedWorkspaces: Record<string, boolean>;
    }>;
    return {
      surfaceLevels: {
        ...Object.fromEntries((legacy.inspectorNodeIds ?? []).map((nodeId) => [nodeId, "inspector"])),
        ...(legacy.activeNodeId ? { [legacy.activeNodeId]: legacy.level ?? "node" } : {}),
      },
      maximizedWorkspaces: legacy.maximizedWorkspaces ?? {},
    };
  },
  partialize: (state) => ({
    surfaceLevels: state.surfaceLevels,
    baseLevels: state.baseLevels,
    maximizedWorkspaces: state.maximizedWorkspaces,
    surfaceSizes: state.surfaceSizes,
  }),
}));

export function surfaceLevelForNode(
  nodeId: string,
  surfaceLevels: Readonly<Record<string, NodeSurfaceLevel>>,
): NodeSurfaceLevel {
  return surfaceLevels[nodeId] ?? "preview";
}
