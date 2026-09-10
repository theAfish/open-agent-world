import { create } from "zustand";
import { ApiError, apiErrorMessage, worldApi } from "../api/client";
import type { NodeTypeCatalogItem, PluginCatalog } from "../types/world";
import { loadLegacyDecks } from "../palette/legacyDecks";
import { useWorldStore } from "./worldStore";

export interface DeckEntry { kind: "node" | "legion"; id: string }
export interface CardDeck { id: string; name: string; icon: string; entries: DeckEntry[] }
export interface PackDefinition { id: string; plugin_id: string; name: string; description: string; cards: string[]; compatibility: boolean }
export interface LibrarySnapshot {
  schema_version: number;
  revision: number;
  migration_pending: boolean;
  plugins: Record<string, { descriptor: PluginCatalog["plugins"][number]; installed: boolean; enabled: boolean }>;
  packs: Record<string, { definition: PackDefinition; owned: boolean; opened: boolean; opened_at: string | null }>;
  card_definitions: Record<string, NodeTypeCatalogItem>;
  collection: Record<string, { card_id: string; plugin_id: string; source_pack_ids: string[]; unlocked: boolean; unlocked_at: string }>;
  decks: CardDeck[];
  active_deck_id: string;
  available_card_ids: string[];
  available_pack_ids: string[];
}
export interface LibraryEdit {
  action: "open_pack" | "create_deck" | "update_deck" | "delete_deck" | "activate_deck" | "import_legacy" | "set_plugin_enabled";
  id?: string;
  name?: string;
  icon?: string;
  entries?: DeckEntry[];
  decks?: CardDeck[];
  enabled?: boolean;
}
interface LibraryStore {
  snapshot: LibrarySnapshot | null;
  open: boolean;
  busy: boolean;
  error: string;
  show: () => void;
  close: () => void;
  refresh: () => Promise<void>;
  edit: (edit: LibraryEdit) => Promise<LibrarySnapshot | null>;
}

let loading: Promise<void> | undefined;
let refreshQueued = false;
export const useCardLibrary = create<LibraryStore>((set, get) => {
  const accept = (snapshot: LibrarySnapshot) => set(state => !state.snapshot || snapshot.revision >= state.snapshot.revision ? { snapshot } : {});
  return {
    snapshot: null, open: false, busy: false, error: "",
    show: () => { set({ open: true }); void get().refresh(); },
    close: () => set({ open: false }),
    refresh: async () => {
      if (loading) { refreshQueued = true; return loading; }
      loading = (async () => {
        do {
          refreshQueued = false;
          try {
            let snapshot = await worldApi.getCardLibrary();
            if (snapshot.migration_pending) {
              const catalog = await worldApi.getCatalog();
              const decks = loadLegacyDecks(catalog).map(deck => ({ id: deck.id, name: deck.label, icon: deck.icon,
                entries: deck.cardTypes.filter(id => snapshot.collection[id]?.unlocked && snapshot.available_card_ids.includes(id)).map(id => ({ kind: "node" as const, id })) }));
              try { snapshot = await worldApi.editCardLibrary({ action: "import_legacy", decks, expected_revision: snapshot.revision }); }
              catch (error) {
                if (!(error instanceof ApiError) || error.status !== 409) throw error;
                snapshot = await worldApi.getCardLibrary();
              }
            }
            accept(snapshot);
            const catalog = await worldApi.getCatalog();
            // An older catalog request must not replace one observed after a newer edit.
            if (get().snapshot?.revision === snapshot.revision) useWorldStore.setState({ catalog });
            set({ error: "" });
          } catch (error) { set({ error: apiErrorMessage(error) }); }
        } while (refreshQueued);
      })().finally(() => { loading = undefined; });
      return loading;
    },
    edit: async edit => {
      if (get().busy || !get().snapshot) return null;
      set({ busy: true, error: "" });
      try {
        const snapshot = await worldApi.editCardLibrary({ ...edit, expected_revision: get().snapshot!.revision });
        accept(snapshot);
        if (edit.action === "set_plugin_enabled") {
          useWorldStore.setState({ catalog: await worldApi.getCatalog() });
          await useWorldStore.getState().refreshWorld();
        }
        return snapshot;
      } catch (error) {
        await get().refresh();
        set({ error: apiErrorMessage(error) });
        return null;
      } finally { set({ busy: false }); }
    },
  };
});
