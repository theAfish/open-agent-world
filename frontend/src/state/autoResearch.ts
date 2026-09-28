import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import { profileStorage } from "./profileStorage";
import { useWorldStore } from "./worldStore";

interface AutoResearchState {
  enabled: boolean;
  toolbarCollapsed: boolean;
  setToolbarCollapsed: (collapsed: boolean) => void;
  scopeId?: string;
  selectedFrontierId?: string;
  panel: "directions" | "scope" | "snapshots" | undefined;
  /** Display projection only. The Scope document remains authoritative. */
  memberIds: string[];
  createdIds: string[];
  /** Ephemeral projection: generic edges are replaced only when actually painted. */
  roadPairs:string[];
  setRoadPairs:(pairs:string[])=>void;
  rememberCreated: (ids:string[]) => void;
  toggle: () => void;
  selectScope: (id?: string) => void;
  selectFrontier: (id: string) => void;
  setPanel: (panel: AutoResearchState["panel"]) => void;
  setMembers: (scopeId: string, ids: string[]) => void;
}

export const useAutoResearch = create<AutoResearchState>()(persist((set) => ({
  enabled: false,
  toolbarCollapsed: false,
  setToolbarCollapsed: toolbarCollapsed => set({toolbarCollapsed}),
  memberIds: [],
  createdIds: [],
  roadPairs:[],
  setRoadPairs:roadPairs=>set(state=>state.roadPairs.length===roadPairs.length && state.roadPairs.every((pair,i)=>pair===roadPairs[i]) ? state : {roadPairs}),
  rememberCreated: ids => set(state => ({createdIds:[...new Set([...state.createdIds,...ids])]})),
  panel: undefined,
  toggle: () => set(state => ({ enabled: !state.enabled })),
  selectScope: scopeId => set({ scopeId, selectedFrontierId: undefined, memberIds: [], panel: undefined }),
  selectFrontier: selectedFrontierId => set({ selectedFrontierId, panel: "directions" }),
  setPanel: panel => set({ panel }),
  setMembers: (scopeId, memberIds) => set(state => state.scopeId === scopeId ? { memberIds } : {}),
}), {
  name: "oaw-auto-research-v1",
  storage: createJSONStorage(() => profileStorage),
  // Switching views cannot start, resume, pause, or otherwise change research work.
  partialize: state => ({ enabled: state.enabled, toolbarCollapsed: state.toolbarCollapsed, scopeId: state.scopeId, createdIds:state.createdIds }),
}));

// Watch the authoritative world, not viewport mounts. Also covers imports,
// pasted formations and cards created while a research workspace is open.
const unsubscribeCreated = useWorldStore.subscribe((state,previous) => {
  if (!useAutoResearch.getState().enabled || state.cards === previous.cards || previous.syncState === "loading") return;
  const known = new Set(previous.cards.map(card => card.id));
  const added = state.cards.filter(card => !known.has(card.id)).map(card => card.id);
  if (added.length) useAutoResearch.getState().rememberCreated(added);
});
if (import.meta.hot) import.meta.hot.dispose(unsubscribeCreated);
