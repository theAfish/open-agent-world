// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { worldApi } from "../api/client";
import { FolderPathInput } from "./FolderPathInput";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("shared folder picker", () => {
  it("blocks duplicate clicks while the system dialog is open", async () => {
    let finish!: (value: { path: string | null }) => void;
    const pick = vi.spyOn(worldApi, "pickFolder").mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    const picking = vi.fn();
    render(<FolderPathInput label="Workspace" value="" onChange={vi.fn()} onPickingChange={picking} />);
    const button = screen.getByRole("button");
    fireEvent.click(button);
    fireEvent.click(button);
    expect(pick).toHaveBeenCalledTimes(1);
    expect((button as HTMLButtonElement).disabled).toBe(true);
    expect(picking).toHaveBeenCalledWith(true);
    finish({ path: null });
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
    expect(picking).toHaveBeenLastCalledWith(false);
  });
  it("starts from the current path and fills the selected folder", async () => {
    const pick = vi.spyOn(worldApi, "pickFolder").mockResolvedValue({ path: "E:\\项目" });
    const change = vi.fn();
    render(<FolderPathInput label="Workspace" value={"D:\\Work"} onChange={change} />);
    fireEvent.click(screen.getByRole("button", { name: "Browse for Workspace" }));
    await waitFor(() => expect(change).toHaveBeenCalledWith("E:\\项目"));
    expect(pick).toHaveBeenCalledWith("D:\\Work");
  });

  it("keeps the current path when the system window is cancelled", async () => {
    vi.spyOn(worldApi, "pickFolder").mockResolvedValue({ path: null });
    const change = vi.fn();
    render(<FolderPathInput label="Workspace" value={"D:\\Work"} onChange={change} />);
    fireEvent.click(screen.getByRole("button", { name: "Browse for Workspace" }));
    await waitFor(() => expect((screen.getByRole("button") as HTMLButtonElement).disabled).toBe(false));
    expect(change).not.toHaveBeenCalled();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("reports unavailable dialogs and keeps manual entry usable", async () => {
    vi.spyOn(worldApi, "pickFolder").mockRejectedValue(new Error("Enter the path manually."));
    const change = vi.fn();
    render(<FolderPathInput label="Workspace" value="" onChange={change} />);
    fireEvent.click(screen.getByRole("button"));
    expect((await screen.findByRole("alert")).textContent).toContain("manually");
    fireEvent.change(screen.getByLabelText("Workspace"), { target: { value: "D:\\Work" } });
    expect(change).toHaveBeenCalledWith("D:\\Work");
  });
});
