import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';
import { profileStorage } from './profileStorage';

/** Resume the last workspace; closing it deliberately returns to the canvas. */
export const useLegionWorkspace = create<{
  activeId?: string; open: (id: string) => void; close: () => void;
}>()(persist(set => ({
  open: activeId => set({ activeId }), close: () => set({ activeId: undefined }),
}), { name: 'oaw-active-workspace-v1', storage: createJSONStorage(() => profileStorage), partialize: ({ activeId }) => ({ activeId }) }));
