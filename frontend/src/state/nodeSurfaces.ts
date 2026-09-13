import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import { profileStorage } from "./profileStorage";
import type { CardType, PluginCatalog } from "../types/world";

export type NodeSurfaceLevel = "node" | "preview" | "inspector" | "workspace";

export interface NodeSurfaceSupport {
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
  return catalog?.node_types.find((definition) => definition.id === type)?.surfaces
    ?? NODE_SURFACE_SUPPORT[type]
    ?? GENERIC_SURFACE_SUPPORT;
}

export const NODE_SURFACE_SIZE = {
  node: { width: 96, height: 96 },
  preview: { width: 286, height: 156 },
  inspector: { width: 438, height: 570 },
  workspace: { width: 1_020, height: 700 },
} as const;

export interface SurfaceSize { width: number; height: number }
export const WORKSPACE_MIN_SIZE = { width: 640, height: 420 };

interface NodeSurfaceState {
  workspaceSizes: Record<string, SurfaceSize>;
  resizeWorkspace: (nodeId: string, size: SurfaceSize) => void;
  surfaceLevels: Record<string, NodeSurfaceLevel>;
  connectingNodeId?: string;
  dragging: boolean;
  setDragging: (dragging: boolean) => void;
  baseLevels: Record<string, "node" | "preview">;
  drafts: Record<string, string>;
  maximizedWorkspaces: Record<string, boolean>;
  showPreview: (nodeId: string) => void;
  hidePreview: (nodeId: string) => void;
  openInspector: (nodeId: string) => void;
  closeInspector: (nodeId?: string) => void;
  dismiss: (nodeId?: string) => void;
  openWorkspace: (nodeId: string) => void;
  closeWorkspace: (nodeId?: string) => void;
  setDraft: (nodeId: string, value: string) => void;
  toggleWorkspaceMaximized: (nodeId: string) => void;
  beginConnection: (nodeId: string) => void;
  endConnection: () => void;
}

export const useNodeSurfaceStore = create<NodeSurfaceState>()(persist((set) => ({
  workspaceSizes: {},
  resizeWorkspace: (nodeId, size) => set((state) => {
    if (!Number.isFinite(size.width) || !Number.isFinite(size.height)) return state;
    return { workspaceSizes: { ...state.workspaceSizes, [nodeId]: {
      width: Math.max(WORKSPACE_MIN_SIZE.width, Math.min(4096, size.width)),
      height: Math.max(WORKSPACE_MIN_SIZE.height, Math.min(4096, size.height)),
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
    return {
      surfaceLevels: { ...state.surfaceLevels, [nodeId]: "preview" },
      baseLevels: { ...state.baseLevels, [nodeId]: "preview" },
    };
  }),

  hidePreview: (nodeId) => set((state) => {
    if (state.connectingNodeId || state.dragging) return state;
    if (["workspace", "inspector"].includes(state.surfaceLevels[nodeId])) return state;
    return {
      surfaceLevels: { ...state.surfaceLevels, [nodeId]: "node" },
      baseLevels: { ...state.baseLevels, [nodeId]: "node" },
    };
  }),

  openInspector: (nodeId) => set((state) => {
    if (state.connectingNodeId || state.dragging) return state;
    const level = surfaceLevelForNode(nodeId, state.surfaceLevels);
    return {
      baseLevels: { ...state.baseLevels, [nodeId]: level === "node" || level === "preview"
        ? level : state.baseLevels[nodeId] ?? "preview" },
      surfaceLevels: { ...state.surfaceLevels, [nodeId]: "inspector" },
    };
  }),

  closeInspector: (nodeId) => set((state) => {
    if (state.connectingNodeId || state.dragging) return state;
    return { surfaceLevels: Object.fromEntries(Object.entries(state.surfaceLevels).map(([id, level]) => [
      id, level === "inspector" && (!nodeId || nodeId === id) ? state.baseLevels[id] ?? "preview" : level,
    ])) };
  }),

  dismiss: (nodeId) => set((state) => {
    if (!nodeId) {
      return {
        surfaceLevels: {},
        baseLevels: {},
        connectingNodeId: undefined,
      };
    }
    return {
      surfaceLevels: Object.fromEntries(Object.entries(state.surfaceLevels).filter(([id]) => id !== nodeId)),
      baseLevels: Object.fromEntries(Object.entries(state.baseLevels).filter(([id]) => id !== nodeId)),
      connectingNodeId: state.connectingNodeId === nodeId ? undefined : state.connectingNodeId,
    };
  }),

  openWorkspace: (nodeId) => set((state) => {
    if (state.connectingNodeId || state.dragging) return state;
    const level = surfaceLevelForNode(nodeId, state.surfaceLevels);
    return {
      baseLevels: { ...state.baseLevels, [nodeId]: level === "node" || level === "preview"
        ? level : state.baseLevels[nodeId] ?? "preview" },
      surfaceLevels: { ...state.surfaceLevels, [nodeId]: "workspace" },
    };
  }),

  closeWorkspace: (nodeId) => set((state) => {
    if (state.connectingNodeId || state.dragging) return state;
    return { surfaceLevels: Object.fromEntries(Object.entries(state.surfaceLevels).map(([id, level]) => [
      id, level === "workspace" && (!nodeId || nodeId === id) ? "inspector" : level,
    ])) };
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
  version: 3,
  migrate: (persisted, version) => {
    if (version >= 3) return persisted;
    if (version === 2) return { ...(persisted as object), baseLevels: {} };
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
    workspaceSizes: state.workspaceSizes,
  }),
}));

export function surfaceLevelForNode(
  nodeId: string,
  surfaceLevels: Readonly<Record<string, NodeSurfaceLevel>>,
): NodeSurfaceLevel {
  return surfaceLevels[nodeId] ?? "preview";
}
