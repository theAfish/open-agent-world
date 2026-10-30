export interface CreatorMetadata {
  tutorials?: import('../tutorials/types').TutorialDefinition[];
  description: string; author: string; preparation: string; example: string; expected_result: string; accent_color: string;
  packaging?: import('./world').PackPackaging;
}
export interface CreatorRequest {
  legion_id: string; id: string; name: string; version: string; creator: CreatorMetadata; include_state_nodes: string[];
}
export interface PackInspection {
  manifest: { id: string; name: string; version: string; kind?: 'plugin' | 'content'; creator?: CreatorMetadata | null;
    dependencies?: { packs: Array<{ id: string; version: string }> } };
  sha256: string;
  plan?: PackInstallationPlan;
}
export interface PackEnvironmentStatus {
  id: string;
  target: 'host-worker' | 'sandbox';
  provider: string;
  management: 'managed' | 'legacy-shared' | 'external';
  state: 'ready' | 'missing' | 'on_use' | 'needs_configuration' | 'unsupported' | 'preparing' | 'failed';
  python: string[];
  node: string[];
  error?: string | null;
  operation_id?: string | null;
}
export interface PackBackendLibrary {
  name: string;
  version: string | null;
  management: 'host-provided' | 'managed';
}
export interface PackInstallationPlan {
  pack_id: string; name: string; version: string;
  source: 'archive' | 'store' | 'bundled' | 'development';
  restart_required: boolean;
  dependencies: Array<{ id: string; version: string; state: 'satisfied' | 'missing' | 'incompatible' | 'disabled' }>;
  environments: PackEnvironmentStatus[];
  backend_libraries?: PackBackendLibrary[];
  warnings: string[];
  blockers: string[];
}
export interface PackInventoryItem {
  id: string; owner: string; name: string; version: string;
  source: 'bundled' | 'development' | 'installed';
  enabled: boolean; loaded: boolean; enrolled?: boolean;
  environments: PackEnvironmentStatus[];
  backend_requirements: string[];
  backend_libraries?: PackBackendLibrary[];
  frontend_dependencies: string[];
  warnings: string[];
}
export interface PackUninstallPlan {
  id: string; owner: string; can_uninstall: boolean; blockers: string[];
  resources: Array<{ id: string; kind: string; action: 'remove' | 'retain' | 'defer'; reason: string }>;
  restart_required: boolean; source: 'installed' | 'development' | 'bundled';
}
export interface PackOperation {
  id: string; digest: string; pack_id: string; version: string;
  state: 'preparing' | 'prepared' | 'selecting' | 'completed' | 'failed';
  error: string | null; updated_at: number;
}
export interface CreatorInspection {
  manifest: PackInspection['manifest']; can_export: boolean;
  nodes: Array<{ key: string; name: string; type: string; has_state: boolean; included: boolean }>;
  issues: Array<{ severity: 'error' | 'warning' | 'info'; path: string; message: string }>;
}
export interface PackInstallations {
  restart_required: boolean;
  failures?: Array<{ id: string; owner: string | null; source: string; state: 'unavailable';
    phase: string; error: string; retry: 'repair_and_restart' }>;
  inventory?: PackInventoryItem[];
  operations?: PackOperation[];
  versions: Array<{ id: string; name: string; version: string; selected: boolean; loaded: boolean;
    owner?: string; source?: string;
    kind?: 'plugin' | 'content'; creator?: CreatorMetadata | null;
    presentation?: Omit<import('./world').PackDefinition, 'plugin_id' | 'source' | 'artwork_url'> | null;
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
export interface StorePreview {
  packaging: import('./world').PackPackaging;
  accent_color?: string | null;
  count?: number | null;
}
export interface StorePack extends StoreState {
  preview?: StorePreview | null;
  id: string; name: string; summary: string; description: string;
  latest_version: string | null;
}
export interface StorePage { items: StorePack[]; next_cursor: string | null }
export interface StoreDetail extends StorePack { versions: string[]; versions_next_cursor: string | null }
export interface StoreVersion extends StoreState {
  preview?: StorePreview | null;
  plan?: PackInstallationPlan;
  pack_id: string; version: string; sha256: string; size_bytes: number;
  manifest: {
    id: string; version: string;
    compatibility: { oaw: string; plugin_api: string; frontend_api: number };
    dependencies: { packs: Array<{ id: string; version: string }> };
    runtime: { sandbox: { python: string[] } };
  };
}
