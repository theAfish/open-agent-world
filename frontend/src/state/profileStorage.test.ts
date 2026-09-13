// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
afterEach(() => { vi.clearAllTimers(); vi.useRealTimers(); vi.unstubAllGlobals(); });

it("hydrates a profile without importing another profile's browser state and persists by generation", async () => {
  vi.resetModules(); vi.useFakeTimers();
  const { configureProfile, profileStorage, flushPreferences } = await import("./profileStorage");
  localStorage.setItem("oaw-theme", "old-formal-theme");
  const request = vi.fn().mockResolvedValue(new Response('{}'));
  vi.stubGlobal("fetch", request);
  configureProfile({ mode: "development", profile_id: "dev", generation: "g1", version: "1", values: {} });
  expect(profileStorage.getItem("oaw-theme")).toBeNull();
  profileStorage.setItem("oaw-theme", "dark");
  await flushPreferences();
  expect(JSON.parse(request.mock.calls[0][1].body)).toEqual({ profile_id: "dev", generation: "g1", changes: { "oaw-theme": "dark" } });
  expect(localStorage.getItem("oaw-theme")).toBe("old-formal-theme");
  configureProfile({ mode: "production", profile_id: "formal", generation: "g2", version: "1", values: { "oaw-theme": "light" } });
  expect(profileStorage.getItem("oaw-theme")).toBe("light");
});

it("starts a formal profile when browser storage is unavailable", async () => {
  vi.resetModules(); vi.useFakeTimers();
  const { initializeProfile, currentProfile } = await import("./profileStorage");
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("disabled"); });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("disabled"); });
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ mode: "production", profile_id: "private", generation: "g1", version: "1", values: {} }))));
  await initializeProfile();
  expect(currentProfile()?.profile_id).toBe("private");
  vi.restoreAllMocks();
});
