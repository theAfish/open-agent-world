import { create } from "zustand";

export const useSummoningCaptureStore = create<{
  pending?: { id: string; libraryId: string; nodeIds: string[] };
  open: (libraryId: string, nodeIds: string[]) => void;
  close: () => void;
}>((set) => ({
  open: (libraryId, nodeIds) => set({ pending: { id: crypto.randomUUID(), libraryId, nodeIds } }),
  close: () => set({ pending: undefined }),
}));
