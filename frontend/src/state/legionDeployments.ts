import { create } from 'zustand';
import type { WorldPosition } from '../types/world';

interface PendingDeployment {
  id: number;
  name: string;
  position: WorldPosition;
  stage: 'queued' | 'deploying';
}

let sequence = 0;
// These are transient placement indicators, never graph nodes or undo entries.
export const useLegionDeployments = create<{
  pending: PendingDeployment[];
  begin: (name: string, position: WorldPosition) => number;
  start: (id: number) => void;
  finish: (id: number) => void;
}>((set) => ({
  pending: [],
  begin: (name, position) => {
    const id = ++sequence;
    set(state => ({ pending: [...state.pending, { id, name, position: { ...position }, stage: 'queued' }] }));
    return id;
  },
  start: id => set(state => ({ pending: state.pending.map(item => item.id === id ? { ...item, stage: 'deploying' } : item) })),
  finish: id => set(state => ({ pending: state.pending.filter(item => item.id !== id) })),
}));
