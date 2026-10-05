import { create } from 'zustand';
import { createJSONStorage, persist, type StateStorage } from 'zustand/middleware';
import { profileStorage } from '../state/profileStorage';
import { tutorialKey } from './catalog';
import type { TutorialEntry, TutorialProgress } from './types';

interface TutorialState {
  enabled: boolean;
  progress: Record<string, TutorialProgress>;
  entries: TutorialEntry[];
  encountered: string[];
  current?: string;
  view: 'hint' | 'steps' | 'document';
  libraryOpen: boolean;
  sync: (entries: TutorialEntry[]) => void;
  encounter: (owners: string[]) => void;
  setEnabled: (enabled: boolean) => void;
  showLibrary: () => void;
  closeLibrary: () => void;
  open: (key: string, document?: boolean) => void;
  dismiss: () => void;
  move: (direction: -1 | 1) => void;
  complete: () => void;
}

function nextHint(state: TutorialState): string | undefined {
  if (!state.enabled) return;
  return state.entries.find(entry => {
    const progress = state.progress[entry.key];
    return state.encountered.includes(entry.owner) && entry.definition.trigger !== 'manual'
      && !!entry.definition.steps?.length && progress?.status !== 'dismissed'
      && !(progress?.status === 'completed' && progress.revision === (entry.definition.revision ?? 1))
      && (entry.definition.after ?? []).every(id => state.progress[tutorialKey(entry.owner, id)]?.status === 'completed');
  })?.key;
}

export function currentStep(entry: TutorialEntry, progress?: TutorialProgress) {
  const steps = entry.definition.steps ?? [];
  return progress?.revision === (entry.definition.revision ?? 1)
    ? Math.max(0, steps.findIndex(step => step.id === progress.stepId)) : 0;
}

/** Independent of world/onboarding stores so other hosts can reuse the engine. */
export function createTutorialStore(storage: StateStorage = profileStorage) {
  return create<TutorialState>()(persist((set, get) => {
    const advanceQueue = () => set(state => ({ current: nextHint(state), view: 'hint' }));
    const save = (status: TutorialProgress['status'], stepId?: string) => {
      const state = get(), entry = state.entries.find(item => item.key === state.current);
      if (!entry) return;
      const existing = state.progress[entry.key];
      if (status === 'reading' && existing?.status === 'completed' && existing.revision === (entry.definition.revision ?? 1)) status = 'completed';
      set({ progress: { ...state.progress, [entry.key]: { revision: entry.definition.revision ?? 1, status,
        stepId: stepId ?? state.progress[entry.key]?.stepId } } });
    };
    return {
      enabled: true, progress: {}, entries: [], encountered: [], view: 'hint', libraryOpen: false,
      sync(entries) {
        const current = get().entries.find(item => item.key === get().current);
        const next = entries.find(item => item.key === get().current);
        set({ entries });
        if (!next || current?.definition.revision !== next.definition.revision) advanceQueue();
      },
      encounter(owners) {
        set(state => ({ encountered: [...new Set([...state.encountered, ...owners])] }));
        if (!get().current) advanceQueue();
      },
      setEnabled(enabled) {
        set({ enabled });
        if (get().view === 'hint') advanceQueue();
      },
      showLibrary: () => set({ libraryOpen: true }),
      closeLibrary: () => set({ libraryOpen: false }),
      open(key, document = false) {
        const entry = get().entries.find(item => item.key === key);
        if (!entry) return;
        const steps = entry.definition.steps ?? [];
        set({ current: key, view: document || !steps.length ? 'document' : 'steps', libraryOpen: false });
        const progress = get().progress[key];
        if (!document && steps.length) save('reading', steps[progress?.status === 'completed' ? 0 : currentStep(entry, progress)]?.id);
      },
      dismiss() {
        const state = get(), entry = state.entries.find(item => item.key === state.current);
        const progress = state.progress[state.current ?? ''];
        if (progress?.status !== 'completed' || progress.revision !== (entry?.definition.revision ?? 1)) save('dismissed');
        advanceQueue();
      },
      move(direction) {
        const state = get(), entry = state.entries.find(item => item.key === state.current);
        if (!entry) return;
        const steps = entry.definition.steps ?? [];
        const index = Math.max(0, Math.min(steps.length - 1, currentStep(entry, state.progress[entry.key]) + direction));
        save('reading', steps[index]?.id);
      },
      complete() { save('completed'); advanceQueue(); },
    };
  }, {
    name: 'oaw-progressive-tutorials-v1', version: 1,
    storage: createJSONStorage(() => storage),
    partialize: ({ enabled, progress }) => ({ enabled, progress }),
    merge(saved, current) {
      const value = saved as Partial<TutorialState> | undefined;
      const progress = Object.fromEntries(Object.entries(value?.progress ?? {}).filter(([, item]) =>
        item && Number.isInteger(item.revision) && item.revision >= 1
        && ['reading', 'dismissed', 'completed'].includes(item.status)
        && (item.stepId === undefined || typeof item.stepId === 'string')));
      return { ...current, enabled: typeof value?.enabled === 'boolean' ? value.enabled : true, progress };
    },
  }));
}

export const useProgressiveTutorials = createTutorialStore();
