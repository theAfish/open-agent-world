export interface PackInspection { manifest: { id: string; name: string; version: string }; sha256: string }
export interface PackInstallations {
  restart_required: boolean;
  versions: Array<{ id: string; name: string; version: string; selected: boolean; loaded: boolean;
    environment: { state: string; error: string | null } | null }>;
}
