// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";
import { worldApi } from "../api/client";
import { buildCardDraft } from "../state/helpers";
import { useWorldStore } from "../state/worldStore";
import type { SandboxInfo, WorldCard } from "../types/world";
import { useNodeSurfaceStore } from "../state/nodeSurfaces";
import { SandboxCardBody, SandboxRuntimeControls, SandboxSettings } from "./SandboxCard";

const sandbox: WorldCard = { id: "lab", ...buildCardDraft("sandbox", { x: 0, y: 0 }) };
const info: SandboxInfo = {
  sandbox_id: sandbox.id, state: "stopped", runtime_id: "wsl:Ubuntu", runtime_locked: false,
  platform: "linux", shell: ["/bin/sh", "-c"], available: true, unavailable_reason: null,
  workspace_path: null, workspace_access: "read_write", workspace: "/workspace", resources_path: "/resources",
  security_boundary: "Linux namespaces in WSL2",
};

function Card() {
  const card = useWorldStore((state) => state.cards[0]);
  const [dirty, setDirty] = useState(false);
  return <><SandboxRuntimeControls card={card} disabled={dirty} /><SandboxSettings card={card} onDirtyChange={setDirty} /></>;
}

describe("sandbox configuration UI", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useNodeSurfaceStore.setState({ drafts: {} });
    useWorldStore.setState({
      cards: [sandbox], edges: [], sandboxInfo: {}, sandboxBusy: {}, sandboxErrors: {}, sandboxRevisions: {},
      sandboxRuntimes: undefined, sandboxRuntimesLoading: false, sandboxRuntimesError: undefined,
      socketState: "closed", toasts: [], undoStack: [], redoStack: [],
    });
    vi.spyOn(worldApi, "getNodeDocument").mockResolvedValue({ value: { variables: {} }, revision: 0, summary: {} });
    vi.spyOn(worldApi, "getCredentialBindings").mockResolvedValue({});
    vi.spyOn(worldApi, "sandboxWorkspace").mockResolvedValue({ profile_id: null, ready: true, variables: [] });
    vi.spyOn(worldApi, "getSandbox").mockResolvedValue(info);
    vi.spyOn(worldApi, "getSandboxRuntimes").mockResolvedValue({
      default_runtime: "wsl:Ubuntu",
      runtimes: [{ id: "wsl:Ubuntu", label: "WSL · Ubuntu", platform: "linux", available: true, reason: null,
        shell: ["/bin/sh", "-c"], supports_workspace: true, supported_network_modes: ["disabled"],
        network_reason: "Networking unavailable: isolated egress protecting host control services is not implemented" }],
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
    expect((screen.getByRole("option", { name: "Enabled" }) as HTMLOptionElement).disabled).toBe(true);
    expect(screen.getByText(/isolated egress protecting host control services/)).toBeTruthy();
  });

  it("keeps a rejected draft visible and blocks start until it is saved or reset", async () => {
    vi.spyOn(worldApi, "updateNode").mockRejectedValue(new Error("Working folder does not exist."));
    render(<Card />);
    await screen.findByText("Linux namespaces in WSL2");
    fireEvent.change(screen.getByLabelText("Working folder"), { target: { value: "D:\\missing" } });
    expect(screen.getByText(/Edits change files in this folder directly/)).toBeTruthy();
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

  it("saves enabled networking when the runtime reports its prerequisites ready", async () => {
    vi.mocked(worldApi.getSandboxRuntimes).mockResolvedValue({ default_runtime: "wsl:Ubuntu", runtimes: [{
      id: "wsl:Ubuntu", label: "WSL Ubuntu", platform: "linux", available: true, reason: null,
      shell: ["/bin/sh", "-c"], supports_workspace: true, supported_network_modes: ["disabled", "enabled"],
      network_available: true, network_status: "available", network_reason: "Public outbound IPv4 only",
    }] });
    vi.spyOn(worldApi, "updateNode").mockResolvedValue({ ...sandbox, config: { ...sandbox.config, network_enabled: true } });
    render(<Card />);
    await waitFor(() => expect((screen.getByRole("option", { name: "Enabled" }) as HTMLOptionElement).disabled).toBe(false));
    expect((screen.getByRole("option", { name: "Enabled" }) as HTMLOptionElement).disabled).toBe(false);
    fireEvent.change(screen.getByLabelText("Networking"), { target: { value: "enabled" } });
    expect((screen.getByRole("button", { name: "Start" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(worldApi.updateNode).toHaveBeenCalledWith(sandbox.id, {
      config: { runtime: "auto", workspace_path: null, workspace_access: "read_write", network_enabled: true },
    }));
  });

  it("keeps offline start available when only networking dependencies are missing", async () => {
    vi.mocked(worldApi.getSandboxRuntimes).mockResolvedValue({ default_runtime: "wsl:Ubuntu", runtimes: [{
      id: "wsl:Ubuntu", label: "WSL Ubuntu", platform: "linux", available: true, reason: null,
      shell: ["/bin/sh", "-c"], supports_workspace: true, supported_network_modes: ["disabled", "enabled"],
      network_available: false, network_status: "missing_component", network_reason: "Install slirp4netns, then refresh",
    }] });
    render(<Card />);
    await screen.findByText(/Install slirp4netns/);
    expect((screen.getByRole("option", { name: "Enabled" }) as HTMLOptionElement).disabled).toBe(false);
    expect((screen.getByRole("button", { name: "Start" }) as HTMLButtonElement).disabled).toBe(false);
    expect(screen.getByRole("status").textContent).toBe("Stopped");
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
    await screen.findByText(/Runtime fixed after first start/);
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

  it("blocks Start while the working folder picker is open", async () => {
    let chooseFolder!: (value: { path: string | null }) => void;
    vi.spyOn(worldApi, "pickFolder").mockImplementation(() => new Promise(resolve => { chooseFolder = resolve; }));
    render(<Card />);
    await waitFor(() => expect((screen.getByRole("button", { name: "Start" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "Browse for Working folder" }));
    expect((screen.getByRole("button", { name: "Start" }) as HTMLButtonElement).disabled).toBe(true);
    chooseFolder({ path: null });
    await waitFor(() => expect((screen.getByRole("button", { name: "Start" }) as HTMLButtonElement).disabled).toBe(false));
  });

  it("keeps the card compact and opens Settings in the sandbox window without starting", async () => {
    const start = vi.spyOn(worldApi, "startSandbox");
    useNodeSurfaceStore.setState({ surfaceLevels: {}, baseLevels: {}, drafts: {}, dragging: false, connectingNodeId: undefined });
    render(<SandboxCardBody card={sandbox} level="inspector" />);
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Stopped"));
    expect(screen.getByText("Managed workspace")).toBeTruthy();
    expect(screen.getByText("Configuration").parentElement?.hasAttribute("open")).toBe(false);
    expect(screen.getByText("Environment variables")).toBeTruthy();
    expect(screen.queryByLabelText("Command")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    expect(useNodeSurfaceStore.getState().surfaceLevels[sandbox.id]).toBe("workspace");
    expect(useNodeSurfaceStore.getState().drafts[`sandbox-tab:${sandbox.id}`]).toBe("settings");
    fireEvent.click(screen.getByRole("button", { name: "Open Window" }));
    expect(useNodeSurfaceStore.getState().drafts[`sandbox-tab:${sandbox.id}`]).toBe("workspace");
    expect(start).not.toHaveBeenCalled();
  });

  it("retries saved enabled policy with stale discovery, shows current failure and clears it on recovery", async () => {
    const enabled = { ...sandbox, config: { ...sandbox.config, network_enabled: true } };
    const unavailable = { ...info, network_enabled: true, supported_network_modes: ["disabled", "enabled"], network_available: false,
      network_status: "missing_component", network_reason: "Broker unavailable" };
    useWorldStore.setState({ cards: [enabled] });
    vi.mocked(worldApi.getSandbox).mockResolvedValue(unavailable);
    const catalog = { default_runtime: info.runtime_id, runtimes: [{ ...unavailable, id: info.runtime_id!, platform: "linux", label: "WSL Ubuntu", reason: null, supports_workspace: true }] };
    vi.mocked(worldApi.getSandboxRuntimes).mockResolvedValue(catalog);
    const start = vi.spyOn(worldApi, "startSandbox").mockRejectedValueOnce(new Error("Current broker failure"));
    render(<Card />);
    const retry = await screen.findByRole("button", { name: "Retry / Recheck" });
    expect((retry as HTMLButtonElement).disabled).toBe(false);
    vi.mocked(worldApi.getSandbox).mockResolvedValue({ ...unavailable, network_reason: "Current broker failure" });
    fireEvent.click(retry);
    await screen.findByRole("alert");
    expect(screen.queryByText("Broker unavailable")).toBeNull();
    expect(screen.getByRole("alert").textContent).toBe("Current broker failure");
    await waitFor(() => expect(useWorldStore.getState().sandboxBusy[sandbox.id]).toBeUndefined());
    start.mockResolvedValue({ ...unavailable, state: "ready", network_available: true, network_status: "available", network_reason: "Public IPv4 only" });
    fireEvent.click(screen.getByRole("button", { name: "Retry / Recheck" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Ready"));
    expect(screen.queryByText("Current broker failure")).toBeNull();
    expect(screen.queryByText("Broker unavailable")).toBeNull();
    expect(useWorldStore.getState().cards[0].config.network_enabled).toBe(true);
    expect(worldApi.getSandboxRuntimes).toHaveBeenCalledWith(false);
  });

  it("shares inspector settings drafts and saved configuration with the Window editor", async () => {
    function Surface({ inspector }: { inspector: boolean }) {
      const card = useWorldStore(s => s.cards[0]);
      return inspector ? <SandboxCardBody card={card} level="inspector" /> : <SandboxSettings card={card} />;
    }
    const { rerender } = render(<Surface inspector />);
    await waitFor(() => expect((screen.getByLabelText("Working folder") as HTMLInputElement).disabled).toBe(false));
    fireEvent.click(screen.getByText("Configuration"));
    fireEvent.change(screen.getByLabelText("Working folder"), { target: { value: "D:\\shared" } });
    rerender(<Surface inspector={false} />);
    expect((screen.getByLabelText("Working folder") as HTMLInputElement).value).toBe("D:\\shared");
    expect(screen.getByText("Unsaved changes")).toBeTruthy();
    const saved = { ...sandbox, config: { ...sandbox.config, workspace_path: "D:\\shared" } };
    vi.spyOn(worldApi, "updateNode").mockResolvedValue(saved);
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save" })));
    rerender(<Surface inspector />);
    expect((screen.getByLabelText("Working folder") as HTMLInputElement).value).toBe("D:\\shared");
    expect(screen.queryByText("Unsaved changes")).toBeNull();
    expect(screen.queryByRole("log")).toBeNull();
  });

  it("explicit recheck replaces a failed prerequisite reason without changing the saved policy", async () => {
    useWorldStore.setState({ cards: [{ ...sandbox, config: { ...sandbox.config, network_enabled: true } }],
      sandboxErrors: { [sandbox.id]: "Previous startup failure" } });
    vi.mocked(worldApi.getSandbox).mockResolvedValue({ ...info, supported_network_modes: ["disabled", "enabled"],
      network_enabled: true, network_available: false, network_status: "missing_component", network_reason: "Broker missing" });
    render(<Card />);
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe("Broker missing"));
    vi.mocked(worldApi.getSandbox).mockResolvedValue({ ...info, supported_network_modes: ["disabled", "enabled"],
      network_enabled: true, network_available: true, network_status: "available", network_reason: "Public IPv4 only" });
    fireEvent.click(screen.getByRole("button", { name: "Refresh sandbox environment" }));
    await waitFor(() => expect(worldApi.getSandboxRuntimes).toHaveBeenCalledWith(true));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    expect(screen.queryByText("Broker missing")).toBeNull();
    expect((screen.getByRole("button", { name: "Start" }) as HTMLButtonElement).disabled).toBe(false);
    expect(useWorldStore.getState().cards[0].config.network_enabled).toBe(true);
  });

  it("keeps an unsaved environment draft and its revision across surfaces", async () => {
    const { rerender } = render(<SandboxCardBody card={sandbox} level="inspector" />);
    await waitFor(() => expect((screen.getByRole("button", { name: "Save environment", hidden: true }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByText("Configuration"));
    fireEvent.click(screen.getByText("Environment variables"));
    fireEvent.click(screen.getByRole("button", { name: "Add variable" }));
    fireEvent.change(screen.getByLabelText("Environment variable 1 name"), { target: { value: "REGION" } });
    fireEvent.change(screen.getByLabelText("Environment variable 1 value"), { target: { value: "draft-region" } });
    vi.mocked(worldApi.getNodeDocument).mockResolvedValue({ value: { variables: { EXTERNAL: "new" } }, revision: 2, summary: {} });
    rerender(<SandboxSettings card={sandbox} />);
    fireEvent.click(screen.getByText("Environment variables"));
    expect((screen.getByLabelText("Environment variable 1 value") as HTMLInputElement).value).toBe("draft-region");
    const save = vi.spyOn(worldApi, "nodeDocumentAction").mockRejectedValue(new Error("Document revision conflict"));
    await waitFor(() => expect((screen.getByRole("button", { name: "Save environment" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "Save environment" }));
    await screen.findByText("Document revision conflict");
    expect(save).toHaveBeenCalledWith(sandbox.id, "replace", { variables: { REGION: "draft-region" } }, 0);
    expect(screen.getByText("Unsaved environment changes")).toBeTruthy();
  });

  it("uses current Sandbox capabilities when discovery is unavailable", async () => {
    useWorldStore.setState({ cards: [{ ...sandbox, config: { ...sandbox.config, network_enabled: true } }] });
    vi.mocked(worldApi.getSandboxRuntimes).mockRejectedValue(new Error("Discovery failed"));
    vi.mocked(worldApi.getSandbox).mockResolvedValue({ ...info, network_enabled: true,
      supported_network_modes: ["disabled", "enabled"], network_available: false,
      network_status: "missing_component", network_reason: "Restore broker" });
    render(<Card />);
    const retry = await screen.findByRole("button", { name: "Retry / Recheck" });
    expect((retry as HTMLButtonElement).disabled).toBe(false);
    expect(screen.queryByText("Discovery failed")).toBeNull();
    expect((screen.getByRole("option", { name: "Enabled" }) as HTMLOptionElement).disabled).toBe(false);
  });
});
