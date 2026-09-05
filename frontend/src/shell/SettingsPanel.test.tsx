// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { worldApi } from "../api/client";
import { useWorldStore } from "../state/worldStore";
import { SettingsPanel } from "./SettingsPanel";

describe("Sandbox application settings", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useWorldStore.setState({ settingsOpen: true });
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
    const saveModel = vi.spyOn(useWorldStore.getState(), "saveModelSettings");
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
});
