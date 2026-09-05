// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from "vitest";
import { worldApi } from "../api/client";
import type { SandboxConfig, SandboxInfo, WorldCard } from "../types/world";
import { buildCardDraft } from "./helpers";
import { useWorldStore } from "./worldStore";

const sandbox: WorldCard = { id: "lab", ...buildCardDraft("sandbox", { x: 0, y: 0 }) };
const info: SandboxInfo = {
  sandbox_id: sandbox.id,
  state: "stopped",
  runtime_id: "linux",
  runtime_locked: false,
  platform: "linux",
  shell: ["/bin/sh", "-c"],
  available: true,
  unavailable_reason: null,
  workspace_path: null,
  workspace_access: "read_write",
  workspace: "/workspace",
  resources_path: "/resources",
  security_boundary: "Linux namespaces",
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

describe("sandbox state contracts", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useWorldStore.setState({
      cards: [sandbox],
      sandboxInfo: { lab: info },
      sandboxBusy: {},
      sandboxErrors: {},
      sandboxRevisions: {},
      sandboxRuntimes: undefined,
      sandboxRuntimesLoading: false,
      sandboxRuntimesError: undefined,
      socketState: "closed",
      events: [],
      toasts: [],
      undoStack: [],
      redoStack: [],
    });
    vi.spyOn(worldApi, "getSandbox").mockResolvedValue(info);
  });

  it("publishes workspace settings only after persistence and blocks start during save", async () => {
    const config: SandboxConfig = { runtime: "linux", workspace_path: "/home/me/project", workspace_access: "read_only" };
    const pending = deferred<WorldCard>();
    vi.spyOn(worldApi, "updateNode").mockReturnValue(pending.promise);
    const start = vi.spyOn(worldApi, "startSandbox");

    const saving = useWorldStore.getState().saveSandboxConfig(sandbox.id, config);
    await Promise.resolve();
    expect(useWorldStore.getState().cards[0].config.workspace_path).toBeNull();
    expect(useWorldStore.getState().sandboxBusy.lab).toBe("saving");
    expect(await useWorldStore.getState().startSandbox(sandbox.id)).toBe(false);
    expect(start).not.toHaveBeenCalled();

    pending.resolve({ ...sandbox, config: { ...config } });
    expect(await saving).toBe(true);
    expect(useWorldStore.getState().cards[0].config).toMatchObject(config);
    expect(useWorldStore.getState().undoStack).toHaveLength(1);
    expect(worldApi.updateNode).toHaveBeenCalledWith(sandbox.id, { config });
  });

  it("retains persisted configuration and history when the server rejects a folder", async () => {
    vi.spyOn(worldApi, "updateNode").mockRejectedValue(new Error("Working folder does not exist."));
    const saved = await useWorldStore.getState().saveSandboxConfig(sandbox.id, {
      runtime: "linux", workspace_path: "/missing", workspace_access: "read_write",
    });
    expect(saved).toBe(false);
    expect(useWorldStore.getState().cards[0]).toEqual(sandbox);
    expect(useWorldStore.getState().undoStack).toEqual([]);
    expect(useWorldStore.getState().sandboxErrors.lab).toBe("Working folder does not exist.");
    expect(useWorldStore.getState().sandboxBusy.lab).toBeUndefined();
  });

  it("uses the backend start state and ignores an older inspection response", async () => {
    const oldInspection = deferred<SandboxInfo>();
    vi.mocked(worldApi.getSandbox).mockReturnValueOnce(oldInspection.promise);
    vi.spyOn(worldApi, "startSandbox").mockResolvedValue({ ...info, state: "running", runtime_locked: true });
    const inspecting = useWorldStore.getState().refreshSandbox(sandbox.id);
    await useWorldStore.getState().startSandbox(sandbox.id);
    oldInspection.resolve(info);
    await inspecting;
    expect(useWorldStore.getState().cards[0].status).toBe("running");
    expect(useWorldStore.getState().sandboxInfo.lab?.runtime_locked).toBe(true);
  });

  it("refreshes the actual state after a failed start without losing the error", async () => {
    vi.spyOn(worldApi, "startSandbox").mockRejectedValue(new Error("Isolation could not be established."));
    vi.mocked(worldApi.getSandbox).mockResolvedValue({ ...info, state: "error" });
    expect(await useWorldStore.getState().startSandbox(sandbox.id)).toBe(false);
    expect(useWorldStore.getState().cards[0].status).toBe("error");
    expect(useWorldStore.getState().sandboxErrors.lab).toBe("Isolation could not be established.");
  });

  it("lets stop cancel a command and ignores its late response", async () => {
    const command = deferred<Record<string, unknown>>();
    vi.spyOn(worldApi, "executeSandbox").mockReturnValue(command.promise);
    vi.spyOn(worldApi, "stopSandbox").mockResolvedValue(info);
    useWorldStore.setState({ cards: [{ ...sandbox, status: "ready" }] });
    const executing = useWorldStore.getState().executeSandbox(sandbox.id, "sleep 30");
    expect(await useWorldStore.getState().executeSandbox(sandbox.id, "echo overlap")).toBe(false);
    expect(await useWorldStore.getState().stopSandbox(sandbox.id)).toBe(true);
    command.resolve({ stdout: "cancelled", exit_code: -1 });
    await executing;
    expect(useWorldStore.getState().cards[0].status).toBe("stopped");
    expect(useWorldStore.getState().cards[0].config.active_command).toBe("");
    expect(useWorldStore.getState().sandboxBusy.lab).toBeUndefined();
    expect(worldApi.getSandbox).not.toHaveBeenCalled();
  });

  it("refreshes state after execution failure rather than assuming the sandbox is ready", async () => {
    vi.spyOn(worldApi, "executeSandbox").mockRejectedValue(new Error("Runtime disconnected."));
    useWorldStore.setState({ cards: [{ ...sandbox, status: "ready" }] });
    expect(await useWorldStore.getState().executeSandbox(sandbox.id, "echo hello")).toBe(false);
    expect(useWorldStore.getState().cards[0].status).toBe("stopped");
    expect(useWorldStore.getState().sandboxErrors.lab).toBe("Runtime disconnected.");
  });

  it("uses HTTP output when a connected socket delivered no output events", async () => {
    vi.spyOn(worldApi, "executeSandbox").mockResolvedValue({ stdout: "hello\n", stderr: "failure\n", exit_code: 2 });
    useWorldStore.setState({ cards: [{ ...sandbox, status: "ready" }], socketState: "live" });
    await useWorldStore.getState().executeSandbox(sandbox.id, "run task");
    expect(useWorldStore.getState().cards[0].config.output).toEqual(["hello", "! failure", "exit 2"]);
  });

  it("shares runtime discovery across cards and refreshes only when requested", async () => {
    const discovery = vi.spyOn(worldApi, "getSandboxRuntimes").mockResolvedValue({ runtimes: [], default_runtime: null });
    await Promise.all([
      useWorldStore.getState().loadSandboxRuntimes(),
      useWorldStore.getState().loadSandboxRuntimes(),
    ]);
    await useWorldStore.getState().loadSandboxRuntimes();
    expect(discovery).toHaveBeenCalledTimes(1);
    await useWorldStore.getState().loadSandboxRuntimes(true);
    expect(discovery).toHaveBeenCalledTimes(2);
  });

  it("does not revive a stopped sandbox from a late command-finished event", async () => {
    const inspection = deferred<SandboxInfo>();
    vi.mocked(worldApi.getSandbox).mockReturnValue(inspection.promise);
    useWorldStore.getState().ingestEvent({
      id: "finished", type: "sandbox_command_finished", sandbox_id: sandbox.id,
      timestamp: "2026-09-05T00:00:00Z", payload: { exit_code: -1 },
    });
    expect(useWorldStore.getState().cards[0].status).toBe("stopped");
    inspection.resolve(info);
    await Promise.resolve();
  });
});
