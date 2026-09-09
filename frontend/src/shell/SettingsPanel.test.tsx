// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { worldApi } from "../api/client";
import { useWorldStore } from "../state/worldStore";
import { SettingsPanel } from "./SettingsPanel";

const savedCatalog = () => ({ revision: 1, default_model: null, connections: [{
  id: "work", name: "Work account", adapter: "openai" as const, base_url: "https://example.test/v1",
  enabled: true, auth_mode: "api_key" as const, api_key_configured: true,
  models: [{ id: "assistant", name: "Assistant", model_id: "test-model", enabled: true }],
}] });

describe("Application settings", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useWorldStore.setState({ settingsOpen: true });
    vi.spyOn(worldApi, "getModelConnections").mockResolvedValue(savedCatalog());
    vi.spyOn(worldApi, "getSandboxSettings").mockResolvedValue({ workspace_root: "D:\\Workspaces", runtime: "auto" });
    vi.spyOn(worldApi, "getSandboxRuntimes").mockResolvedValue({ default_runtime: "windows", runtimes: [
      { id: "windows", label: "Windows", platform: "windows", available: true, reason: null, shell: [], supports_workspace: true },
    ] });
  });
  afterEach(cleanup);

  it("browses into the draft without saving until Save settings is clicked", async () => {
    vi.spyOn(worldApi, "pickFolder").mockResolvedValue({ path: "E:\\Selected" });
    const save = vi.spyOn(worldApi, "saveSandboxSettings");
    render(<SettingsPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Sandbox" }));
    const browse = screen.getByRole("button", { name: "Browse for Default Workspace location" });
    await waitFor(() => expect((browse as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(browse);
    await waitFor(() => expect((screen.getByLabelText("Default Workspace location") as HTMLInputElement).value).toBe("E:\\Selected"));
    expect(save).not.toHaveBeenCalled();
  });

  it("loads and saves Sandbox defaults independently of model settings", async () => {
    const save = vi.spyOn(worldApi, "saveSandboxSettings").mockResolvedValue({ workspace_root: "E:\\Projects", runtime: "windows" });
    const saveModel = vi.spyOn(worldApi, "saveModelConnections");
    render(<SettingsPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Sandbox" }));
    const folder = screen.getByLabelText("Default Workspace location") as HTMLInputElement;
    await waitFor(() => expect(folder.value).toBe("D:\\Workspaces"));
    fireEvent.change(folder, { target: { value: "E:\\Projects" } });
    fireEvent.change(screen.getByLabelText("Default runtime"), { target: { value: "windows" } });
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    await waitFor(() => expect(useWorldStore.getState().settingsOpen).toBe(false));
    expect(save).toHaveBeenCalledWith({ workspace_root: "E:\\Projects", runtime: "windows" });
    expect(saveModel).not.toHaveBeenCalled();
  });

  it("keeps the dialog and draft after a rejected path, and supports clearing the default", async () => {
    const save = vi.spyOn(worldApi, "saveSandboxSettings").mockRejectedValue(new Error("Folder is not accessible"));
    render(<SettingsPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Sandbox" }));
    const folder = screen.getByLabelText("Default Workspace location") as HTMLInputElement;
    await waitFor(() => expect(folder.disabled).toBe(false));
    fireEvent.change(folder, { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Folder is not accessible");
    expect(useWorldStore.getState().settingsOpen).toBe(true);
    expect(save).toHaveBeenCalledWith({ workspace_root: null, runtime: "auto" });
    expect(folder.value).toBe("");
  });

  it("blocks saving until settings load and lets the user retry", async () => {
    vi.spyOn(worldApi, "getSandboxSettings").mockRejectedValueOnce(new Error("Backend unavailable"));
    render(<SettingsPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Sandbox" }));
    await screen.findByRole("alert");
    expect((screen.getByRole("button", { name: "Save settings" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect((screen.getByRole("button", { name: "Save settings" }) as HTMLButtonElement).disabled).toBe(false));
  });

  it("shows saved-key status without loading the secret into the browser", async () => {

    const saveModel = vi.spyOn(worldApi, "saveModelConnections").mockResolvedValue(savedCatalog());
    render(<SettingsPanel />);

    const key = await screen.findByLabelText("API key") as HTMLInputElement;
    await waitFor(() => expect(key.placeholder).toContain("Saved securely"));
    expect(key.value).toBe("");
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));

    await waitFor(() => expect(saveModel).toHaveBeenCalled());
    expect(saveModel.mock.calls[0][0].connections[0].api_key).toBeUndefined();
    expect(saveModel.mock.calls[0][0].connections[0].clear_api_key).toBeUndefined();
  });

  it("only removes a persisted key after an explicit user action", async () => {

    const saveModel = vi.spyOn(worldApi, "saveModelConnections").mockResolvedValue(savedCatalog());
    render(<SettingsPanel />);

    fireEvent.click(await screen.findByRole("button", { name: "Remove saved key" }));
    expect(screen.getByText(/The saved key will be removed when you save/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));

    await waitFor(() => expect(saveModel).toHaveBeenCalled());
    expect(saveModel.mock.calls[0][0].connections[0].clear_api_key).toBe(true);
  });

  it("uses preset authentication defaults and keeps deployment authentication in advanced options", async () => {
    render(<SettingsPanel />);
    await screen.findByLabelText("Connection name");
    expect(screen.queryByText("Authentication source")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Advanced connection options" }));
    expect((screen.getByLabelText("Authentication source") as HTMLSelectElement).value).toBe("api_key");
    fireEvent.change(screen.getByLabelText("New connection type"), { target: { value: "local" } });
    fireEvent.click(screen.getByRole("button", { name: "Add connection" }));
    expect((screen.getByLabelText("API key") as HTMLInputElement).disabled).toBe(false);
    expect(screen.getByText(/Optional for this connection/)).toBeTruthy();
    fireEvent.click(screen.getAllByRole("button", { name: "Advanced connection options" }).at(-1)!);
    fireEvent.change(screen.getAllByLabelText("Authentication source").at(-1)!, { target: { value: "environment" } });
    expect((screen.getByLabelText("API key") as HTMLInputElement).disabled).toBe(false);
    expect((screen.getByLabelText("Backend environment variable") as HTMLInputElement).placeholder).toBe("OPENAI_API_KEY");
    expect(screen.getByText(/managed deployments/)).toBeTruthy();
  });

  it.each(["none", "environment"] as const)("accepts a key directly from %s without opening advanced options", async (auth_mode) => {
    const initial = savedCatalog();
    vi.mocked(worldApi.getModelConnections).mockResolvedValue({ ...initial, connections: [{ ...initial.connections[0],
      id: "legacy", adapter: "legacy", auth_mode, api_key_configured: false,
    }] });
    const save = vi.spyOn(worldApi, "saveModelConnections").mockResolvedValue(initial);
    render(<SettingsPanel />);
    const key = await screen.findByLabelText("API key") as HTMLInputElement;
    expect(key.disabled).toBe(false);
    expect(screen.queryByLabelText("Authentication source")).toBeNull();
    fireEvent.change(key, { target: { value: "direct-key" } });
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    await waitFor(() => expect(save).toHaveBeenCalled());
    expect(save.mock.calls[0][0].connections[0]).toMatchObject({ auth_mode: "api_key", api_key: "direct-key", clear_api_key: false });
  });

  it("changing credential sources does not delete the saved key", async () => {
    const save = vi.spyOn(worldApi, "saveModelConnections").mockResolvedValue(savedCatalog());
    render(<SettingsPanel />);
    await screen.findByLabelText("API key");
    fireEvent.click(screen.getByRole("button", { name: "Advanced connection options" }));
    fireEvent.change(screen.getByLabelText("Authentication source"), { target: { value: "environment" } });
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    await waitFor(() => expect(save).toHaveBeenCalled());
    expect(save.mock.calls[0][0].connections[0].auth_mode).toBe("environment");
    expect(save.mock.calls[0][0].connections[0].clear_api_key).toBeUndefined();
  });

  it("entering a replacement cancels pending key removal", async () => {
    const save = vi.spyOn(worldApi, "saveModelConnections").mockResolvedValue(savedCatalog());
    render(<SettingsPanel />);
    fireEvent.click(await screen.findByRole("button", { name: "Remove saved key" }));
    const key = screen.getByLabelText("API key") as HTMLInputElement;
    expect(key.disabled).toBe(false);
    fireEvent.change(key, { target: { value: "replacement-key" } });
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    await waitFor(() => expect(save).toHaveBeenCalled());
    expect(save.mock.calls[0][0].connections[0]).toMatchObject({ api_key: "replacement-key", clear_api_key: false });
  });
  it("keeps separate connection drafts and preserves them across tabs", async () => {
    const save = vi.spyOn(worldApi, "saveModelConnections").mockImplementation(async v => v);
    render(<SettingsPanel />);
    await screen.findByLabelText("Connection name");
    fireEvent.click(screen.getByRole("button", { name: "Add connection" }));
    fireEvent.change(screen.getByLabelText("Connection name"), { target: { value: "Personal" } });
    fireEvent.change(screen.getByLabelText("API key"), { target: { value: "personal-secret" } });
    fireEvent.click(screen.getByRole("button", { name: "Add model" }));
    fireEvent.change(screen.getByLabelText("Model 1 display name"), { target: { value: "Personal assistant" } });
    fireEvent.change(screen.getByLabelText("Model 1 ID"), { target: { value: "test-model" } });
    fireEvent.click(screen.getByRole("button", { name: "Sandbox" }));
    fireEvent.click(screen.getByRole("button", { name: "Models" }));
    fireEvent.click(screen.getByRole("button", { name: /Personal 1 models/ }));
    expect((screen.getByLabelText("API key") as HTMLInputElement).value).toBe("personal-secret");
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    await waitFor(() => expect(save).toHaveBeenCalled());
    expect(save.mock.calls[0][0].connections).toHaveLength(2);
    expect(save.mock.calls[0][0].connections[0].api_key).toBeUndefined();
    expect(save.mock.calls[0][0].connections[1].api_key).toBe("personal-secret");
  });

  it("preserves a failed save and supports explicitly discarding a stale draft", async () => {
    vi.spyOn(worldApi, "saveModelConnections").mockRejectedValue(new Error("Model settings changed in another window"));
    render(<SettingsPanel />);
    fireEvent.change(await screen.findByLabelText("Connection name"), { target: { value: "Unsaved name" } });
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    expect((await screen.findByRole("alert")).textContent).toContain("another window");
    expect((screen.getByLabelText("Connection name") as HTMLInputElement).value).toBe("Unsaved name");
    fireEvent.click(screen.getByRole("button", { name: "Discard draft and reload" }));
    await waitFor(() => expect((screen.getByLabelText("Connection name") as HTMLInputElement).value).toBe("Work account"));
  });

});
