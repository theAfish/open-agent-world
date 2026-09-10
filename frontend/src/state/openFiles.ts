import { create } from "zustand";
import { useEffect } from "react";
import { useWorldStore } from "./worldStore";

export type FileReference =
  | { kind: "sandbox"; source_id: string; root: string; path: string }
  | { kind: "conversation"; source_id: string; session_id: string; version_id: string; path: string };
export interface OpenedFile { reference: FileReference; name: string; sequence: number }

interface OpenFilesState {
  sequence: number;
  sources: Record<string, OpenedFile>;
  pins: Record<string, OpenedFile | undefined>;
  open(reference: FileReference, name: string): void;
  clear(sourceId: string): void;
  pin(viewerId: string, file?: OpenedFile): void;
}

// UI intent is local to this browser; neither bytes nor paths are persisted in card config.
export const useOpenFiles = create<OpenFilesState>((set) => ({
  sequence: 0, sources: {}, pins: {},
  open: (reference, name) => set(state => ({ sequence: state.sequence + 1,
    sources: { ...state.sources, [reference.source_id]: { reference, name, sequence: state.sequence + 1 } } })),
  clear: sourceId => set(state => ({
    sources: Object.fromEntries(Object.entries(state.sources).filter(([id]) => id !== sourceId)),
    pins: Object.fromEntries(Object.entries(state.pins).filter(([, file]) => file?.reference.source_id !== sourceId)),
  })),
  pin: (viewerId, file) => set(state => ({ pins: { ...state.pins, [viewerId]: file } })),
}));

export function useFileViewer(viewerId: string) {
  const edges = useWorldStore(state => state.edges);
  const cards = useWorldStore(state => state.cards);
  const sources = useOpenFiles(state => state.sources);
  const pin = useOpenFiles(state => state.pins[viewerId]);
  const connected = cards.filter(card => edges.some(edge => edge.relationship === "core.file-preview"
    && edge.source === viewerId && edge.target === card.id));
  const allowed = new Set(connected.map(card => card.id));
  const active = Object.values(sources).filter(file => allowed.has(file.reference.source_id))
    .sort((a, b) => b.sequence - a.sequence)[0];
  const pinned = !!pin && allowed.has(pin.reference.source_id);
  useEffect(() => {
    if (pin && !pinned) useOpenFiles.getState().pin(viewerId, undefined);
  }, [pin, pinned, viewerId]);
  const file = pinned ? pin : active;
  return { file, pinned, sources: connected.map(card => ({ id: card.id, name: card.name })),
    setPinned: (value: boolean) => useOpenFiles.getState().pin(viewerId, value ? file : undefined) };
}
