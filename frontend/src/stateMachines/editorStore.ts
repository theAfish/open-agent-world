import { create } from 'zustand';

/** The editor is a transient modal; configuration belongs to the selected card. */
export const useStateMachineEditor = create<{
  activeId?: string;
  open: (id: string) => void;
  close: () => void;
}>(set => ({
  open: activeId => set({ activeId }),
  close: () => set({ activeId: undefined }),
}));
