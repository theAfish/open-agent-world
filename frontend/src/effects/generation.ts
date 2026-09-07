import { create } from "zustand";

export interface NodeGeneration {
  id: string;
  sourceId: string;
  targetId: string;
  containerId?: string;
  phase: "pending" | "flying" | "settling";
}

/** Transient visual events only. Never persisted or reconstructed from world snapshots. */
export const useGenerationStore = create<{
  items: NodeGeneration[];
  seen: string[];
  enqueue: (item: Omit<NodeGeneration, "phase">) => void;
  setPhase: (id: string, phase: NodeGeneration["phase"]) => void;
  remove: (id: string) => void;
}>((set, get) => ({
  items: [], seen: [],
  enqueue: (item) => {
    if (get().seen.includes(item.id)) return;
    set((state) => ({ items: [...state.items, { ...item, phase: "pending" }], seen: [...state.seen, item.id].slice(-128) }));
    // A source outside the loaded viewport must never leave a destination hidden.
    setTimeout(() => get().remove(item.id), 2400);
  },
  setPhase: (id, phase) => set((state) => ({ items: state.items.map((item) => item.id === id ? { ...item, phase } : item) })),
  remove: (id) => set((state) => ({ items: state.items.filter((item) => item.id !== id) })),
}));

export const useNodeGeneration = (id: string) => useGenerationStore((state) => state.items.find(
  (item) => item.targetId === id || item.containerId === id || item.sourceId === id,
));

export function flightPosition(from: { x: number; y: number }, to: { x: number; y: number }, progress: number) {
  const t = 1 - (1 - progress) ** 3;
  const lift = Math.min(120, Math.hypot(to.x - from.x, to.y - from.y) * 0.15);
  return { x: from.x + (to.x - from.x) * t, y: from.y + (to.y - from.y) * t - Math.sin(t * Math.PI) * lift };
}
