// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { worldApi } from "../api/client";
import { buildCardDraft } from "../state/helpers";
import { useWorldStore } from "../state/worldStore";
import type { SandboxInfo, WorldCard } from "../types/world";
import { SandboxCardBody } from "./SandboxCard";

const sandbox: WorldCard = { id: "lab", ...buildCardDraft("sandbox", { x: 0, y: 0 }) };
const info: SandboxInfo = {
  sandbox_id: sandbox.id, state: "stopped", runtime_id: "wsl:Ubuntu", runtime_locked: false,
  platform: "linux", shell: ["/bin/sh", "-c"], available: true, unavailable_reason: null,
  workspace_path: null, workspace_access: "read_write", workspace: "/workspace", resources_path: "/resources",
  security_boundary: "Linux namespaces in WSL2",
};

function Card() {
  const card = useWorldStore((state) => state.cards[0]);
  return <SandboxCardBody card={card} level="inspector" />;
}

describe("sandbox configuration UI", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useWorldStore.setState({
      cards: [sandbox], edges: [], sandboxInfo: {}, sandboxBusy: {}, sandboxErrors: {}, sandboxRevisions: {},
      sandboxRuntimes: undefined, sandboxRuntimesLoading: false, sandboxRuntimesError: undefined,
      socketState: "closed", toasts: [], undoStack: [], redoStack: [],
    });
    vi.spyOn(worldApi, "getSandbox").mockResolvedValue(info);
    vi.spyOn(worldApi, "getSandboxRuntimes").mockResolvedValue({
      default_runtime: "wsl:Ubuntu",
      runtimes: [{ id: "wsl:Ubuntu", label: "WSL · Ubuntu", platform: "linux", available: true, reason: null,
        shell: ["/bin/sh", "-c"], supports_workspace: true }],
    });
  });
  afterEach(() => cleanup());

  it("renders the backend runtime, shell, directory and isolation metadata", async () => {
    render(<Card />);
    await screen.findByText("Linux namespaces in WSL2");
    expect(screen.getByRole("status").textContent).toBe("Stopped");
    expect(screen.getByText("/bin/sh -c")).toBeTruthy();
    expect(screen.getByText("/workspace")).toBeTruthy();
    expect(screen.queryByText("Native Windows boundary")).toBeNull();
    expect((screen.getByLabelText("Folder access") as HTMLSelectElement).disabled).toBe(true);
  });

  it("keeps a rejected draft visible and blocks start until it is saved or reset", async () => {
    vi.spyOn(worldApi, "updateNode").mockRejectedValue(new Error("Working folder does not exist."));
    render(<Card />);
    await screen.findByText("Linux namespaces in WSL2");
    fireEvent.change(screen.getByLabelText("Working folder"), { target: { value: "D:\\missing" } });
    expect(screen.getByText(/Agent edits change the files/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Start" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", "Working folder does not exist.");
    expect((screen.getByLabelText("Working folder") as HTMLInputElement).value).toBe("D:\\missing");
    expect(screen.getByText("Unsaved changes")).toBeTruthy();
    expect(useWorldStore.getState().cards[0].config.workspace_path).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Reset" }));
    expect((screen.getByLabelText("Working folder") as HTMLInputElement).value).toBe("");
    expect((screen.getByRole("button", { name: "Start" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("clears an external read-only binding back to a writable managed workspace", async () => {
    useWorldStore.setState({ cards: [{ ...sandbox, config: { ...sandbox.config, workspace_path: "D:\\project", workspace_access: "read_only" } }] });
    vi.spyOn(worldApi, "updateNode").mockResolvedValue(sandbox);
    render(<Card />);
    await screen.findByText("Linux namespaces in WSL2");
    fireEvent.change(screen.getByLabelText("Working folder"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(worldApi.updateNode).toHaveBeenCalledWith(sandbox.id, {
      config: { runtime: "auto", workspace_path: null, workspace_access: "read_write" },
    }));
  });

  it("locks a provisioned runtime while allowing stopped workspace edits", async () => {
    vi.mocked(worldApi.getSandbox).mockResolvedValue({ ...info, runtime_locked: true });
    render(<Card />);
    await screen.findByText(/Runtime fixed for this sandbox/);
    expect((screen.getByLabelText("Runtime") as HTMLSelectElement).disabled).toBe(true);
    expect((screen.getByLabelText("Working folder") as HTMLInputElement).disabled).toBe(false);
  });

  it("shows runtime unavailability without claiming the sandbox is ready", async () => {
    vi.mocked(worldApi.getSandbox).mockResolvedValue({ ...info, available: false, unavailable_reason: "Install bubblewrap in this WSL distribution." });
    render(<Card />);
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", "Install bubblewrap in this WSL distribution.");
    expect(screen.getByRole("status").textContent).toBe("Runtime unavailable");
    expect((screen.getByRole("button", { name: "Start" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
