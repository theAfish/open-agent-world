/** Hydrated before importing any persisted stores. The backend owns profile identity. */
export interface ApplicationProfile {
  mode: "production" | "development" | "preview";
  profile_id: string;
  generation: string;
  version: string;
  values: Record<string, string>;
}
const API = (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, "") ?? "/api";
let profile: ApplicationProfile | undefined;
let pending: Record<string, string | null> = {};
let saving: Promise<void> | undefined;
let timer: ReturnType<typeof setTimeout> | undefined;
let monitor: ReturnType<typeof setInterval> | undefined;

export const currentProfile = () => profile;
export const applicationUrl = (path = "") => `${API}/application${path}`;

export async function fetchProfile(): Promise<ApplicationProfile> {
  const response = await fetch(applicationUrl(), { cache: "no-store", signal: AbortSignal.timeout(5000) });
  if (!response.ok) throw new Error(`Application startup failed (${response.status})`);
  return response.json();
}

export function configureProfile(value: ApplicationProfile) {
  profile = value;
  pending = {};
}

export async function flushPreferences(): Promise<void> {
  if (timer) clearTimeout(timer);
  if (saving) { await saving; return flushPreferences(); }
  if (!profile || !Object.keys(pending).length) return;
  const changes = pending;
  pending = {};
  const selected = profile;
  saving = (async () => {
    try {
      const response = await fetch(applicationUrl("/preferences"), {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ profile_id: selected.profile_id, generation: selected.generation, changes }),
        signal: AbortSignal.timeout(5000),
      });
      if (response.status === 409) { window.location.reload(); return; }
      if (!response.ok) throw new Error("Could not save application preferences");
    } catch (error) {
      pending = { ...changes, ...pending };
      timer = setTimeout(() => { void flushPreferences().catch(() => {}); }, 2000);
      throw error;
    }
  })().finally(() => { saving = undefined; });
  return saving;
}

function queue(key: string, value: string | null) {
  pending[key] = value;
  if (timer) clearTimeout(timer);
  timer = setTimeout(() => { void flushPreferences().catch(() => {}); }, 250);
}

export const profileStorage = {
  getItem(key: string): string | null {
    return profile ? profile.values[key] ?? null : typeof localStorage === "undefined" ? null : localStorage.getItem(key);
  },
  setItem(key: string, value: string) {
    if (!profile) { if (typeof localStorage !== "undefined") localStorage.setItem(key, value); return; }
    if (profile.values[key] === value) return;
    profile.values[key] = value;
    queue(key, value);
  },
  removeItem(key: string) {
    if (!profile) { if (typeof localStorage !== "undefined") localStorage.removeItem(key); return; }
    delete profile.values[key];
    queue(key, null);
  },
};

export async function initializeProfile() {
  configureProfile(await fetchProfile());
  const browserRead = (key: string) => { try { return localStorage.getItem(key); } catch { return null; } };
  // One-time production migration only. Development never imports daily-use browser state.
  const marker = `oaw-profile-imported:${profile!.profile_id}`;
  if (profile!.mode === "production" && !Object.keys(profile!.values).length && !browserRead(marker)) {
    for (const key of ["oaw-onboarding-v1", "oaw-canvas-viewport-v1", "oaw-glue-v1", "oaw-node-surfaces-v1",
      "oaw-library-preferences", "oaw-theme", "oaw.locale", "open-agent-world.decks.v2", "open-agent-world.custom-decks.v1"]) {
      const value = browserRead(key);
      if (value !== null) profileStorage.setItem(key, value);
    }
    await flushPreferences();
    try { localStorage.setItem(marker, "true"); } catch { /* The backend remains authoritative without browser storage. */ }
  }
  if (monitor) clearInterval(monitor);
  monitor = setInterval(() => {
    void fetchProfile().then(next => {
      if (next.profile_id !== profile?.profile_id || next.generation !== profile?.generation) window.location.reload();
    }).catch(() => {});
  }, 2000);
  window.addEventListener("pagehide", () => {
    if (profile && Object.keys(pending).length) {
      void fetch(applicationUrl("/preferences"), { method: "PATCH", keepalive: true,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ profile_id: profile.profile_id, generation: profile.generation, changes: pending }),
      }).catch(() => {});
    }
  });
}
