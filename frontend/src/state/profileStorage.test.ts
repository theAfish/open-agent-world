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

const devProfile = { mode: "development", profile_id: "dev", generation: "g1", version: "1", values: {} };

it("backs off disconnected monitor probes without nested retries and recovers", async () => {
  vi.resetModules(); vi.useFakeTimers();
  const request = vi.fn().mockImplementation(async () => new Response(JSON.stringify(devProfile)));
  vi.stubGlobal("fetch", request);
  const { initializeProfile } = await import("./profileStorage");
  await initializeProfile();
  request.mockImplementation(async () => new Response(null, { status: 500 }));
  for (const delay of [2000, 4000, 8000, 16000, 32000, 60000, 60000]) {
    const count = request.mock.calls.length;
    await vi.advanceTimersByTimeAsync(delay - 1);
    expect(request).toHaveBeenCalledTimes(count);
    await vi.advanceTimersByTimeAsync(1);
    expect(request).toHaveBeenCalledTimes(count + 1);
  }
  request.mockImplementation(async () => new Response(JSON.stringify(devProfile)));
  await vi.advanceTimersByTimeAsync(60000);
  const count = request.mock.calls.length;
  await vi.advanceTimersByTimeAsync(2000);
  expect(request).toHaveBeenCalledTimes(count + 1);
});

it("does not overlap slow probes and aborts a probe before reinitializing", async () => {
  vi.resetModules(); vi.useFakeTimers();
  const request = vi.fn().mockImplementation(async () => new Response(JSON.stringify(devProfile)));
  vi.stubGlobal("fetch", request);
  const { initializeProfile } = await import("./profileStorage");
  await initializeProfile();
  let probeSignal: AbortSignal | undefined;
  request.mockImplementationOnce((_url: string, init: RequestInit) => new Promise((_resolve, reject) => {
    probeSignal = init.signal!;
    probeSignal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
  }));
  await vi.advanceTimersByTimeAsync(6000);
  expect(request).toHaveBeenCalledTimes(2); // Initial hydration and one unfinished probe.
  await initializeProfile();
  expect(probeSignal?.aborted).toBe(true);
  await vi.advanceTimersByTimeAsync(4000);
  expect(request).toHaveBeenCalledTimes(5); // Only the replacement monitor runs.
});

it("times out a stalled probe before scheduling its next attempt", async () => {
  vi.resetModules(); vi.useFakeTimers();
  const request = vi.fn().mockImplementation(async () => new Response(JSON.stringify(devProfile)));
  vi.stubGlobal("fetch", request);
  const { initializeProfile } = await import("./profileStorage");
  await initializeProfile();
  let aborted = false;
  request.mockImplementationOnce((_url: string, init: RequestInit) => new Promise((_resolve, reject) => {
    init.signal!.addEventListener("abort", () => {
      aborted = true;
      reject(new DOMException("Aborted", "AbortError"));
    });
  }));
  await vi.advanceTimersByTimeAsync(7000);
  expect(aborted).toBe(true);
  expect(request).toHaveBeenCalledTimes(2);
  await vi.advanceTimersByTimeAsync(3999);
  expect(request).toHaveBeenCalledTimes(2);
  await vi.advanceTimersByTimeAsync(1);
  expect(request).toHaveBeenCalledTimes(3);
});
