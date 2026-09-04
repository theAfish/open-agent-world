import { create } from "zustand";
import { persist } from "zustand/middleware";
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
  sandbox: { preview: true, inspector: true, workspace: false },
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

interface NodeSurfaceState {
  surfaceLevels: Record<string, NodeSurfaceLevel>;
  connectingNodeId?: string;
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
  surfaceLevels: {},
  drafts: {},
  maximizedWorkspaces: {},

  showPreview: (nodeId) => set((state) => {
    if (state.surfaceLevels[nodeId] === "workspace" || state.surfaceLevels[nodeId] === "inspector") return state;
    if (state.connectingNodeId) return state;
    return {
      surfaceLevels: {
        ...Object.fromEntries(Object.entries(state.surfaceLevels).filter(([, level]) => level !== "preview")),
        [nodeId]: "preview",
      },
    };
  }),

  hidePreview: (nodeId) => set((state) => (
    !state.connectingNodeId && state.surfaceLevels[nodeId] === "preview"
      ? { surfaceLevels: Object.fromEntries(Object.entries(state.surfaceLevels).filter(([id]) => id !== nodeId)) }
      : state
  )),

  openInspector: (nodeId) => set((state) => ({
    connectingNodeId: undefined,
    surfaceLevels: {
      ...Object.fromEntries(Object.entries(state.surfaceLevels).filter(([, level]) => level !== "preview")),
      [nodeId]: "inspector",
    },
  })),

  closeInspector: (nodeId) => set((state) => {
    const ids = nodeId
      ? new Set([nodeId])
      : new Set(Object.entries(state.surfaceLevels)
        .filter(([, level]) => level === "inspector")
        .map(([id]) => id));
    return { surfaceLevels: Object.fromEntries(Object.entries(state.surfaceLevels).filter(([id]) => !ids.has(id))) };
  }),

  dismiss: (nodeId) => set((state) => {
    if (!nodeId) {
      return {
        surfaceLevels: {},
        connectingNodeId: undefined,
      };
    }
    return {
      surfaceLevels: Object.fromEntries(Object.entries(state.surfaceLevels).filter(([id]) => id !== nodeId)),
      connectingNodeId: state.connectingNodeId === nodeId ? undefined : state.connectingNodeId,
    };
  }),

  openWorkspace: (nodeId) => set((state) => ({
    surfaceLevels: { ...state.surfaceLevels, [nodeId]: "workspace" },
  })),

  closeWorkspace: (nodeId) => set((state) => {
    const ids = nodeId
      ? new Set([nodeId])
      : new Set(Object.entries(state.surfaceLevels)
        .filter(([, level]) => level === "workspace")
        .map(([id]) => id));
    return { surfaceLevels: Object.fromEntries(Object.entries(state.surfaceLevels).filter(([id]) => !ids.has(id))) };
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

  beginConnection: (nodeId) => set((state) => {
    if (state.surfaceLevels[nodeId] === "workspace" || state.surfaceLevels[nodeId] === "inspector") {
      return { connectingNodeId: nodeId };
    }
    return { surfaceLevels: { ...state.surfaceLevels, [nodeId]: "preview" }, connectingNodeId: nodeId };
  }),

  endConnection: () => set((state) => (
    state.connectingNodeId && state.surfaceLevels[state.connectingNodeId] === "preview"
      ? {
        surfaceLevels: Object.fromEntries(Object.entries(state.surfaceLevels)
          .filter(([id]) => id !== state.connectingNodeId)),
        connectingNodeId: undefined,
      }
      : { connectingNodeId: undefined }
  )),
}), {
  name: "oaw-node-surfaces-v1",
  version: 2,
  migrate: (persisted, version) => {
    if (version >= 2) return persisted;
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
    maximizedWorkspaces: state.maximizedWorkspaces,
  }),
}));

export function surfaceLevelForNode(
  nodeId: string,
  surfaceLevels: Readonly<Record<string, NodeSurfaceLevel>>,
): NodeSurfaceLevel {
  return surfaceLevels[nodeId] ?? "node";
}
