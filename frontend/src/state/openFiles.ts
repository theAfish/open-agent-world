import { create } from "zustand";
import { createContext, useContext, useEffect } from "react";
import { useWorldStore } from "./worldStore";

export type FileReference =
  | { kind: "sandbox"; source_id: string; root: string; path: string }
  | { kind: "conversation"; source_id: string; session_id: string; version_id: string; path: string };
export interface FileOpenOptions { reveal?: boolean; onReveal?: () => void; workspaceKey?: string }
export interface OpenedFile extends FileOpenOptions { reference: FileReference; name: string; sequence: number }
export const FileWorkspaceContext = createContext<string | undefined>(undefined);
const scopedKey = (id: string, scope?: string) => scope ? JSON.stringify([id, scope]) : id;

interface OpenFilesState {
  sequence: number;
  sources: Record<string, OpenedFile>;
  pins: Record<string, OpenedFile | undefined>;
  open(reference: FileReference, name: string, options?: FileOpenOptions): void;
  clear(sourceId: string): void;
  changeSandboxPath(sourceId: string, path: string, destination?: string): void;
  pin(viewerId: string, file?: OpenedFile): void;
}

// UI intent is local to this browser; neither bytes nor paths are persisted in card config.
export const useOpenFiles = create<OpenFilesState>((set) => ({
  sequence: 0, sources: {}, pins: {},
  open: (reference, name, options) => set(state => ({ sequence: state.sequence + 1,
    sources: { ...state.sources, [scopedKey(reference.source_id, options?.workspaceKey)]: { reference, name, sequence: state.sequence + 1, ...options, reveal: options?.reveal ?? true } } })),
  clear: sourceId => set(state => ({
    sources: Object.fromEntries(Object.entries(state.sources).filter(([, file]) => file.reference.source_id !== sourceId)),
    pins: Object.fromEntries(Object.entries(state.pins).filter(([, file]) => file?.reference.source_id !== sourceId)),
  })),
  changeSandboxPath: (sourceId, path, destination) => set(state => {
    function update(file: OpenedFile | undefined): OpenedFile | undefined {
      const reference = file?.reference;
      if (!file || reference?.kind !== 'sandbox' || reference.source_id !== sourceId || reference.root !== 'workspace'
        || (reference.path !== path && !reference.path.startsWith(`${path}/`))) return file;
      return destination === undefined ? undefined : { ...file, reference: { ...reference, path: destination + reference.path.slice(path.length) } };
    }
    return {
      sources: Object.fromEntries(Object.entries(state.sources).flatMap(([id, file]) => { const next = update(file); return next ? [[id, next]] : []; })),
      pins: Object.fromEntries(Object.entries(state.pins).map(([id, file]) => [id, update(file)])),
    };
  }),
  pin: (viewerId, file) => set(state => ({ pins: { ...state.pins, [viewerId]: file } })),
}));

export function useFileViewer(viewerId: string) {
  const workspaceKey = useContext(FileWorkspaceContext);
  const pinKey = scopedKey(viewerId, workspaceKey);
  const edges = useWorldStore(state => state.edges);
  const cards = useWorldStore(state => state.cards);
  const sources = useOpenFiles(state => state.sources);
  const pin = useOpenFiles(state => state.pins[pinKey]);
  const connected = cards.filter(card => edges.some(edge => edge.relationship === "core.file-preview"
    && edge.source === viewerId && edge.target === card.id));
  const allowed = new Set(connected.map(card => card.id));
  const active = Object.values(sources).filter(file => allowed.has(file.reference.source_id)
    && (!workspaceKey || file.workspaceKey === workspaceKey))
    .sort((a, b) => b.sequence - a.sequence)[0];
  const pinned = !!pin && allowed.has(pin.reference.source_id);
  useEffect(() => {
    if (pin && !pinned) useOpenFiles.getState().pin(pinKey, undefined);
  }, [pin, pinned, pinKey]);
  const file = pinned ? pin : active;
  return { file, pinned, sources: connected.map(card => ({ id: card.id, name: card.name })),
    setPinned: (value: boolean) => useOpenFiles.getState().pin(pinKey, value ? file : undefined) };
}
