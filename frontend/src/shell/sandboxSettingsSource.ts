import { worldApi, type SandboxSettings } from '../api/client';
import type { SandboxRuntime } from '../types/world';
import { adminRequest } from '../auth/adminRequest';
import { currentHostedSession, isHosted } from '../auth/hostedSession';

export interface SandboxSettingsSnapshot {
  settings: SandboxSettings;
  runtimes: SandboxRuntime[];
  revision?: number;
  deliveryEnabled?: boolean;
}

type SandboxSettingsEdit = Parameters<typeof worldApi.saveSandboxSettings>[0];

interface SandboxSettingsSource {
  scope: 'workspace' | 'deployment';
  load(): Promise<SandboxSettingsSnapshot>;
  save(edit: SandboxSettingsEdit, previous: SandboxSettingsSnapshot): Promise<SandboxSettingsSnapshot>;
}

const personal: SandboxSettingsSource = {
  scope: 'workspace',
  async load() {
    const [settings, catalog] = await Promise.all([worldApi.getSandboxSettings(), worldApi.getSandboxRuntimes()]);
    return { settings, runtimes: catalog.runtimes };
  },
  async save(edit, previous) {
    return { ...previous, settings: await worldApi.saveSandboxSettings(edit) };
  },
};

type SharedEnvironmentSettings = {
  revision: number;
  environment_variables: Record<string, string | { secret_ref: string }>;
  secret_bindings: Record<string, boolean>;
};

const sharedSnapshot = (value: SharedEnvironmentSettings, deliveryEnabled?: boolean): SandboxSettingsSnapshot => ({
  // The shared endpoint manages environment bindings, never host runtime or paths.
  settings: { workspace_root: null, runtime: 'auto', environment_variables: value.environment_variables, secret_bindings: value.secret_bindings },
  runtimes: [], revision: value.revision, deliveryEnabled,
});

const deployment: SandboxSettingsSource = {
  scope: 'deployment',
  async load() {
    const [value, status] = await Promise.all([
      adminRequest<SharedEnvironmentSettings>('environment'),
      adminRequest<{ shared_environment_enabled: boolean }>('status'),
    ]);
    return sharedSnapshot(value, status.shared_environment_enabled);
  },
  async save(edit, previous) {
    const value = await adminRequest<SharedEnvironmentSettings>('environment', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ revision: previous.revision, environment_variables: edit.environment_variables, secrets: edit.secrets ?? {} }),
    });
    return sharedSnapshot(value, previous.deliveryEnabled);
  },
};

export function sandboxSettingsSource(): SandboxSettingsSource | null {
  if (!isHosted()) return personal;
  return currentHostedSession()?.user?.role === 'admin' ? deployment : null;
}
