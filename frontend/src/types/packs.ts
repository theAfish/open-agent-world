export interface PackInspection { manifest: { id: string; name: string; version: string }; sha256: string }
export interface PackInstallations {
  restart_required: boolean;
  versions: Array<{ id: string; name: string; version: string; selected: boolean; loaded: boolean;
    environment: { state: string; error: string | null } | null }>;
}

export interface StoreState {
  installed_version: string | null;
  loaded_version: string | null;
  available_version: string | null;
  update_available: boolean;
  restart_required: boolean;
  can_install: boolean;
}
export interface StorePack extends StoreState {
  id: string; name: string; summary: string; description: string;
  latest_version: string | null;
}
export interface StorePage { items: StorePack[]; next_cursor: string | null }
export interface StoreDetail extends StorePack { versions: string[]; versions_next_cursor: string | null }
export interface StoreVersion extends StoreState {
  pack_id: string; version: string; sha256: string; size_bytes: number;
  manifest: {
    id: string; version: string;
    compatibility: { oaw: string; plugin_api: string; frontend_api: number };
    dependencies: { packs: Array<{ id: string; version: string }> };
    runtime: { sandbox: { python: string[] } };
  };
}
