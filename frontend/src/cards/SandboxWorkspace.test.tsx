// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { worldApi } from "../api/client";
import { buildCardDraft } from "../state/helpers";
import { useNodeSurfaceStore } from "../state/nodeSurfaces";
import { useWorldStore } from "../state/worldStore";
import type { SandboxInfo, WorldCard } from "../types/world";
import { SandboxWorkspace } from "./SandboxWorkspace";

const card: WorldCard = { id: "sandbox-window", ...buildCardDraft("sandbox", { x: 0, y: 0 }), status: "ready" };
const info: SandboxInfo = {
  sandbox_id: card.id, state: "ready", runtime_id: "wsl:Ubuntu", runtime_locked: true,
  platform: "linux", shell: ["/bin/sh", "-c"], available: true, unavailable_reason: null,
  workspace_path: null, workspace_access: "read_write", workspace: "/workspace", resources_path: "/resources",
  security_boundary: "Linux namespaces in WSL2",
};

function Workspace() {
  const current = useWorldStore(state => state.cards[0]);
  return <SandboxWorkspace card={current} />;
}

describe("Sandbox workspace interaction", () => {
  let previewFile: (path: string) => Promise<unknown>;

  beforeEach(() => {
    vi.restoreAllMocks();
    useNodeSurfaceStore.setState({ drafts: {}, surfaceLevels: {}, baseLevels: {} });
    useWorldStore.setState({
      cards: [card], edges: [], events: [], sandboxInfo: { [card.id]: info }, sandboxBusy: {}, sandboxErrors: {}, sandboxRevisions: {},
      sandboxRuntimes: undefined, sandboxRuntimesLoading: false, sandboxRuntimesError: undefined,
      socketState: "closed", toasts: [], undoStack: [], redoStack: [], activityOpen: false,
    });
    previewFile = async path => ({ state: "text", text: `Contents of ${path}` });
    vi.spyOn(worldApi, "getSandbox").mockResolvedValue(info);
    vi.spyOn(worldApi, "getSandboxRuntimes").mockResolvedValue({
      default_runtime: "wsl:Ubuntu",
      runtimes: [{ id: "wsl:Ubuntu", label: "WSL Ubuntu", platform: "linux", available: true,
        reason: null, shell: ["/bin/sh", "-c"], supports_workspace: true }],
    });
    vi.spyOn(worldApi, "getNodeDocument").mockResolvedValue({ value: { variables: {} }, revision: 0, summary: {} });
    vi.spyOn(worldApi, "getCredentialBindings").mockResolvedValue({});
    vi.spyOn(worldApi, "sandboxWorkspace").mockImplementation(async <T,>(_id: string, action: string): Promise<T> => {
      if (action === "configuration") return { profile_id: null, ready: true, variables: [] } as T;
      if (action === "history") return [] as T;
      if (action === "files") return [{ id: "workspace", label: "Workspace", access: "read_write", directory: true }] as T;
      const query = new URLSearchParams(action.split("?")[1]);
      if (query.get("operation") === "list") return {
        entries: ["first.txt", "second.txt"].map(name => ({ name, directory: false, blocked: false, size: 10 })), truncated: false,
      } as T;
      if (query.get("operation") === "preview") return await previewFile(query.get("path")!) as T;
      throw new Error(`Unexpected workspace request: ${action}`);
    });
  });
  afterEach(() => cleanup());

  it("keeps the terminal and latest file selection when an older preview completes later", async () => {
    let resolveFirst!: (value: unknown) => void;
    previewFile = path => path === "first.txt"
      ? new Promise(resolve => { resolveFirst = resolve; })
      : Promise.resolve({ state: "text", text: "Second file content" });
    const { container } = render(<Workspace />);
    await screen.findByRole("button", { name: "first.txt" });
    fireEvent.change(screen.getByRole("textbox", { name: "Command" }), { target: { value: "echo keep this draft" } });
    fireEvent.click(screen.getByRole("button", { name: "first.txt" }));
    fireEvent.click(screen.getByRole("button", { name: "second.txt" }));
    await screen.findByText("Second file content");
    await act(async () => resolveFirst({ state: "text", text: "Stale first file content" }));

    expect(screen.queryByText("Stale first file content")).toBeNull();
    expect(screen.getByRole("button", { name: "second.txt" }).getAttribute("aria-current")).toBe("true");
    expect(screen.getByRole("tab", { name: "Terminal" }).getAttribute("aria-selected")).toBe("true");
    expect((screen.getByRole("textbox", { name: "Command" }) as HTMLTextAreaElement).value).toBe("echo keep this draft");
    expect(container.querySelector(".sandbox-preview")?.closest("[hidden]")).toBeNull();
    expect(container.querySelector(".sandbox-terminal")?.closest("[hidden]")).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: "History" }));
    fireEvent.click(screen.getByRole("tab", { name: "Settings" }));
    fireEvent.click(screen.getByRole("tab", { name: "Workspace" }));
    expect(screen.getByRole("tab", { name: "History" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByText("Second file content")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Terminal" }));
    expect((screen.getByRole("textbox", { name: "Command" }) as HTMLTextAreaElement).value).toBe("echo keep this draft");
  });

  it("uses Enter to run and prevents blank or duplicate submissions while busy", async () => {
    let finishCommand!: (value: Record<string, unknown>) => void;
    const execute = vi.spyOn(worldApi, "executeSandbox")
      .mockImplementationOnce(() => new Promise(resolve => { finishCommand = resolve; }))
      .mockResolvedValue({ stdout: "second output", stderr: "", exit_code: 0 });
    render(<Workspace />);
    await screen.findByRole("button", { name: "first.txt" });
    const command = screen.getByRole("textbox", { name: "Command" });
    fireEvent.keyDown(command, { key: "Enter", ctrlKey: true });
    expect(execute).not.toHaveBeenCalled();
    fireEvent.change(command, { target: { value: "  printf first  " } });
    fireEvent.keyDown(command, { key: "Enter", shiftKey: true });
    expect(execute).not.toHaveBeenCalled();
    fireEvent.keyDown(command, { key: "Enter" });
    expect(execute).toHaveBeenCalledWith(card.id, "printf first");
    expect((command as HTMLTextAreaElement).readOnly).toBe(true);
    expect(screen.getByRole("log").textContent).toContain("$ printf first");
    expect((command as HTMLTextAreaElement).value).toBe("");
    fireEvent.keyDown(command, { key: "Enter", metaKey: true });
    expect(execute).toHaveBeenCalledTimes(1);

    await act(async () => finishCommand({ stdout: "first output", stderr: "", exit_code: 0 }));
    await waitFor(() => expect((command as HTMLTextAreaElement).readOnly).toBe(false));
    fireEvent.change(command, { target: { value: "printf second" } });
    fireEvent.keyDown(command, { key: "Enter", metaKey: true });
    await waitFor(() => expect(execute).toHaveBeenCalledTimes(2));
    expect(execute).toHaveBeenLastCalledWith(card.id, "printf second");
    await waitFor(() => expect(screen.getByRole("log").textContent).toContain("second output"));
    expect(screen.getByRole("log").textContent).toContain("first output");
    expect(command.closest(".sandbox-terminal-output")?.contains(screen.getByRole("log"))).toBe(true);
    fireEvent.change(command, { target: { value: "unfinished" } });
    fireEvent.keyDown(command, { key: "ArrowUp" });
    expect((command as HTMLTextAreaElement).value).toBe("printf second");
    fireEvent.keyDown(command, { key: "ArrowDown" });
    expect((command as HTMLTextAreaElement).value).toBe("unfinished");
  });

  it("refreshes files after starting and rebinding the workspace without a socket event", async () => {
    const stopped = { ...info, state: "stopped", runtime_locked: false };
    useWorldStore.setState({ cards: [{ ...card, status: "stopped" }], sandboxInfo: { [card.id]: stopped } });
    vi.mocked(worldApi.getSandbox).mockResolvedValue(stopped);
    render(<Workspace />);
    await screen.findByRole("button", { name: "first.txt" });
    const fileRefreshes = () => vi.mocked(worldApi.sandboxWorkspace).mock.calls.filter(([, action]) => action === "files").length;
    const initialRefreshes = fileRefreshes();

    act(() => useWorldStore.setState({ cards: [card], sandboxInfo: { [card.id]: info } }));
    await waitFor(() => expect(fileRefreshes()).toBeGreaterThan(initialRefreshes));
    await screen.findByRole("button", { name: "second.txt" });
    const startedRefreshes = fileRefreshes();
    fireEvent.click(screen.getByRole("button", { name: "second.txt" }));
    await screen.findByText("Contents of second.txt");

    act(() => useWorldStore.setState({
      cards: [{ ...card, config: { ...card.config, workspace_path: "D:\\other-project", workspace_access: "read_only" } }],
      sandboxInfo: { [card.id]: { ...info, workspace_path: "D:\\other-project", workspace_access: "read_only" } },
    }));
    await waitFor(() => expect(fileRefreshes()).toBeGreaterThan(startedRefreshes));
    expect(screen.queryByText("Contents of second.txt")).toBeNull();
    expect(screen.getByText("Select a file to preview")).toBeTruthy();
    expect(useWorldStore.getState().socketState).toBe("closed");
  });

  it("retains a folder opened while the file tree refresh is in flight", async () => {
    const original = vi.mocked(worldApi.sandboxWorkspace).getMockImplementation()!;
    const rootEntries = { entries: [{ name: "src", directory: true, blocked: false, size: 0 }], truncated: false };
    let deferRoot = false;
    let resolveRoot: ((value: unknown) => void) | undefined;
    vi.mocked(worldApi.sandboxWorkspace).mockImplementation(async <T,>(id: string, action: string): Promise<T> => {
      const query = new URLSearchParams(action.split("?")[1]);
      if (query.get("operation") !== "list") return await original(id, action) as T;
      if (query.get("path") === "src") return {
        entries: [{ name: "index.ts", directory: false, blocked: false, size: 15 }], truncated: false,
      } as T;
      if (deferRoot) return await new Promise<unknown>(resolve => { resolveRoot = resolve; }) as T;
      return rootEntries as T;
    });
    render(<Workspace />);
    await screen.findByRole("button", { name: "src" });
    deferRoot = true;
    fireEvent.click(screen.getByRole("button", { name: "Refresh files" }));
    await waitFor(() => expect(resolveRoot).toBeTypeOf("function"));
    fireEvent.click(screen.getByRole("button", { name: "src" }));
    await screen.findByRole("button", { name: "index.ts" });
    await act(async () => resolveRoot!(rootEntries));
    await waitFor(() => expect((screen.getByRole("button", { name: "Refresh files" }) as HTMLButtonElement).disabled).toBe(false));
    expect(screen.getByRole("button", { name: "src" }).getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByRole("button", { name: "index.ts" })).toBeTruthy();
  });

  it.each(["binding", "sandbox"])("ignores an old expansion after switching %s and completing the new refresh", async change => {
    const original = vi.mocked(worldApi.sandboxWorkspace).getMockImplementation()!;
    let resolveOld!: (value: unknown) => void;
    let childRequests = 0;
    vi.mocked(worldApi.sandboxWorkspace).mockImplementation(async <T,>(id: string, action: string): Promise<T> => {
      const query = new URLSearchParams(action.split("?")[1]);
      if (query.get("operation") !== "list") return await original(id, action) as T;
      if (query.get("path") === "src") {
        childRequests++;
        if (childRequests === 1) return await new Promise<unknown>(resolve => { resolveOld = resolve; }) as T;
        return { entries: [{ name: "from-B.txt", directory: false }] } as T;
      }
      return { entries: [{ name: "src", directory: true }] } as T;
    });
    render(<Workspace />);
    fireEvent.click(await screen.findByRole("button", { name: "src" }));
    await waitFor(() => expect(childRequests).toBe(1));
    const next = { ...card, id: change === "sandbox" ? "sandbox-B" : card.id, config: { ...card.config, workspace_path: "D:\\B" } };
    vi.mocked(worldApi.getSandbox).mockResolvedValue({ ...info, sandbox_id: next.id, workspace_path: "D:\\B" });
    act(() => useWorldStore.setState({ cards: [next], sandboxInfo: { [next.id]: { ...info, workspace_path: "D:\\B" } } }));
    await waitFor(() => expect((screen.getByRole("button", { name: "Refresh files" }) as HTMLButtonElement).disabled).toBe(false));
    await act(async () => resolveOld({ entries: [{ name: "from-A.txt", directory: false }] }));
    fireEvent.click(screen.getByRole("button", { name: "src" }));
    await screen.findByRole("button", { name: "from-B.txt" });
    expect(childRequests).toBe(2);
    expect(screen.queryByText("from-A.txt")).toBeNull();
  });

  it("does not let an older expansion overwrite a newer refresh in the same workspace", async () => {
    const original = vi.mocked(worldApi.sandboxWorkspace).getMockImplementation()!;
    let resolveOld!: (value: unknown) => void;
    let count = 0;
    vi.mocked(worldApi.sandboxWorkspace).mockImplementation(async <T,>(id: string, action: string): Promise<T> => {
      const query = new URLSearchParams(action.split("?")[1]);
      if (query.get("operation") !== "list") return await original(id, action) as T;
      if (query.get("path") !== "src") return { entries: [{ name: "src", directory: true }] } as T;
      if (++count === 1) return await new Promise<unknown>(resolve => { resolveOld = resolve; }) as T;
      return { entries: [{ name: "new.txt", directory: false }] } as T;
    });
    render(<Workspace />);
    fireEvent.click(await screen.findByRole("button", { name: "src" }));
    fireEvent.click(screen.getByRole("button", { name: "Refresh files" }));
    await screen.findByRole("button", { name: "new.txt" });
    await act(async () => resolveOld({ entries: [{ name: "old.txt", directory: false }] }));
    expect(screen.getByRole("button", { name: "new.txt" })).toBeTruthy();
    expect(screen.queryByText("old.txt")).toBeNull();
  });

  it.each(["roots", "preview"])("ignores obsolete %s responses after rebinding and unmount", async operation => {
    const original = vi.mocked(worldApi.sandboxWorkspace).getMockImplementation()!;
    let delayed = false;
    const pending: ((value: unknown) => void)[] = [];
    vi.mocked(worldApi.sandboxWorkspace).mockImplementation(async <T,>(id: string, action: string): Promise<T> => {
      if (delayed && (operation === "roots" ? action === "files" : action.includes("operation=preview"))) {
        return await new Promise<unknown>(resolve => pending.push(resolve)) as T;
      }
      return await original(id, action) as T;
    });
    const { unmount } = render(<Workspace />);
    await screen.findByRole("button", { name: "first.txt" });
    delayed = true;
    fireEvent.click(screen.getByRole("button", { name: operation === "roots" ? "Refresh files" : "first.txt" }));
    await waitFor(() => expect(pending.length).toBe(1));
    delayed = false;
    act(() => useWorldStore.setState({ cards: [{ ...card, config: { ...card.config, workspace_path: "D:\\B" } }] }));
    await screen.findByRole("button", { name: "second.txt" });
    const obsolete = operation === "roots" ? [{ id: "obsolete", label: "Old root", directory: false }] : { state: "text", text: "Old preview" };
    await act(async () => pending[0](obsolete));
    expect(screen.queryByText("Old root")).toBeNull();
    expect(screen.queryByText("Old preview")).toBeNull();
    delayed = true;
    fireEvent.click(screen.getByRole("button", { name: operation === "roots" ? "Refresh files" : "second.txt" }));
    await waitFor(() => expect(pending.length).toBe(2));
    const calls = vi.mocked(worldApi.sandboxWorkspace).mock.calls.length;
    unmount();
    await act(async () => pending[1](obsolete));
    expect(vi.mocked(worldApi.sandboxWorkspace).mock.calls.length).toBe(calls);
  });
});
