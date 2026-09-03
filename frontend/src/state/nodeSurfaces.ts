import { create } from "zustand";
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
  activeNodeId?: string;
  level: NodeSurfaceLevel;
  inspectorNodeIds: string[];
  connectingNodeId?: string;
  drafts: Record<string, string>;
  maximizedWorkspaces: Record<string, boolean>;
  showPreview: (nodeId: string) => void;
  hidePreview: (nodeId: string) => void;
  openInspector: (nodeId: string) => void;
  closeInspector: (nodeId?: string) => void;
  dismiss: (nodeId?: string) => void;
  openWorkspace: (nodeId: string) => void;
  closeWorkspace: () => void;
  setDraft: (nodeId: string, value: string) => void;
  toggleWorkspaceMaximized: (nodeId: string) => void;
  beginConnection: (nodeId: string) => void;
  endConnection: () => void;
}

export const useNodeSurfaceStore = create<NodeSurfaceState>((set) => ({
  level: "node",
  inspectorNodeIds: [],
  drafts: {},
  maximizedWorkspaces: {},

  showPreview: (nodeId) => set((state) => {
    if (state.level === "workspace" || state.inspectorNodeIds.includes(nodeId)) return state;
    if (state.connectingNodeId) return state;
    return { activeNodeId: nodeId, level: "preview" };
  }),

  hidePreview: (nodeId) => set((state) => (
    !state.connectingNodeId && state.activeNodeId === nodeId && state.level === "preview"
      ? { activeNodeId: undefined, level: "node" }
      : state
  )),

  openInspector: (nodeId) => set((state) => ({
    activeNodeId: undefined,
    level: "node",
    connectingNodeId: undefined,
    inspectorNodeIds: [...new Set([...state.inspectorNodeIds, nodeId])],
  })),

  closeInspector: (nodeId) => set((state) => {
    if (!nodeId) return { inspectorNodeIds: [] };
    return { inspectorNodeIds: state.inspectorNodeIds.filter((id) => id !== nodeId) };
  }),

  dismiss: (nodeId) => set((state) => {
    if (!nodeId) {
      return {
        activeNodeId: undefined,
        level: "node",
        connectingNodeId: undefined,
        inspectorNodeIds: [],
      };
    }
    return {
      activeNodeId: state.activeNodeId === nodeId ? undefined : state.activeNodeId,
      level: state.activeNodeId === nodeId ? "node" : state.level,
      connectingNodeId: state.connectingNodeId === nodeId ? undefined : state.connectingNodeId,
      inspectorNodeIds: state.inspectorNodeIds.filter((id) => id !== nodeId),
    };
  }),

  openWorkspace: (nodeId) => set({ activeNodeId: nodeId, level: "workspace" }),

  closeWorkspace: () => set((state) => (
    state.level === "workspace" ? { activeNodeId: undefined, level: "node" } : state
  )),

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
    if (state.level === "workspace" || state.inspectorNodeIds.includes(nodeId)) {
      return { connectingNodeId: nodeId };
    }
    return { activeNodeId: nodeId, level: "preview", connectingNodeId: nodeId };
  }),

  endConnection: () => set((state) => (
    state.level === "preview" && state.activeNodeId === state.connectingNodeId
      ? { activeNodeId: undefined, level: "node", connectingNodeId: undefined }
      : { connectingNodeId: undefined }
  )),
}));

export function surfaceLevelForNode(
  nodeId: string,
  activeNodeId: string | undefined,
  level: NodeSurfaceLevel,
  inspectorNodeIds: readonly string[] = [],
): NodeSurfaceLevel {
  if (nodeId === activeNodeId && level === "workspace") return "workspace";
  if (inspectorNodeIds.includes(nodeId)) return "inspector";
  return nodeId === activeNodeId ? level : "node";
}
