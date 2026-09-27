export interface CreatorMetadata {
  description: string; author: string; preparation: string; example: string; expected_result: string; accent_color: string;
}
export interface CreatorRequest {
  legion_id: string; id: string; name: string; version: string; creator: CreatorMetadata; include_state_nodes: string[];
}
export interface PackInspection {
  manifest: { id: string; name: string; version: string; kind?: 'plugin' | 'content'; creator?: CreatorMetadata | null;
    dependencies?: { packs: Array<{ id: string; version: string }> } };
  sha256: string;
}
export interface CreatorInspection {
  manifest: PackInspection['manifest']; can_export: boolean;
  nodes: Array<{ key: string; name: string; type: string; has_state: boolean; included: boolean }>;
  issues: Array<{ severity: 'error' | 'warning' | 'info'; path: string; message: string }>;
}
export interface PackInstallations {
  restart_required: boolean;
  versions: Array<{ id: string; name: string; version: string; selected: boolean; loaded: boolean;
    kind?: 'plugin' | 'content'; creator?: CreatorMetadata | null;
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
