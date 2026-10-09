// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { worldApi } from "../api/client";
import { buildCardDraft } from "../state/helpers";
import { surfaceDraftKey, useNodeSurfaceStore } from "../state/nodeSurfaces";
import { useWorldStore } from "../state/worldStore";
import { useOpenFiles } from "../state/openFiles";
import type { SandboxInfo, WorldCard } from "../types/world";
import { SandboxWorkspace } from "./SandboxWorkspace";
import { ReactFlowProvider } from "@xyflow/react";
import type { ComponentProps } from "react";
import { WorldCardNode } from "./CardFrame";
import { WorkspaceSectionProvider, type WorkspaceSectionRegistration } from "../workspace/WorkspaceSection";

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
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it('uploads a dropped file through the shared API and refreshes the listing', async () => {
    const upload = vi.spyOn(worldApi, 'uploadSandboxEntry').mockResolvedValue({ written: 4 });
    render(<Workspace />);
    await screen.findByRole('button', { name: 'first.txt' });
    const panel = screen.getByLabelText('Sandbox files');
    const file = new File(['data'], 'dragged.txt');
    const dataTransfer = { types: ['Files'], files: [file] };
    fireEvent.dragEnter(panel, { dataTransfer });
    expect(panel.getAttribute('data-drop-active')).toBe('true');
    fireEvent.drop(panel, { dataTransfer });
    await waitFor(() => expect(upload).toHaveBeenCalledWith(card.id, 'dragged.txt', file, expect.objectContaining({ signal: expect.any(AbortSignal) })));
    await screen.findByText('1 completed');
    expect(panel.hasAttribute('data-drop-active')).toBe(false);
  });

  it('keeps read-only workspace drop targets non-writable', async () => {
    const upload = vi.spyOn(worldApi, 'uploadSandboxEntry');
    const readonly: SandboxInfo = { ...info, workspace_access: 'read_only' };
    vi.mocked(worldApi.getSandbox).mockResolvedValue(readonly);
    useWorldStore.setState({ sandboxInfo: { [card.id]: readonly } });
    render(<Workspace />);
    await screen.findByRole('button', { name: 'first.txt' });
    fireEvent.drop(screen.getByLabelText('Sandbox files'), { dataTransfer: { types: ['Files'], files: [new File(['data'], 'no.txt')] } });
    await act(async () => {});
    expect(upload).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: 'Upload files' })).toBeNull();
    const file = screen.getByRole('button', { name: 'first.txt' });
    expect(file.draggable).toBe(false);
    fireEvent.contextMenu(file);
    expect(screen.queryByRole('menuitem', { name: 'Delete' })).toBeNull();
  });

  it('confirms deletion and clears a removed preview only after the server succeeds', async () => {
    const remove = vi.spyOn(worldApi, 'deleteSandboxFile').mockRejectedValueOnce(new Error('File is in use')).mockResolvedValue({ deleted: 'first.txt' });
    render(<Workspace />);
    fireEvent.click(await screen.findByRole('button', { name: 'first.txt' }));
    await screen.findByText('Contents of first.txt');
    fireEvent.contextMenu(screen.getByRole('button', { name: 'first.txt' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Delete' }));
    expect(remove).not.toHaveBeenCalled();
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }));
    expect(remove).not.toHaveBeenCalled();
    fireEvent.contextMenu(screen.getByRole('button', { name: 'first.txt' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Delete' }));
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' }));
    await screen.findByText('File is in use');
    expect(screen.getByText('Contents of first.txt')).toBeTruthy();
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(remove).toHaveBeenLastCalledWith(card.id, 'first.txt');
    expect(screen.queryByText('Contents of first.txt')).toBeNull();
    expect(useOpenFiles.getState().sources[card.id]).toBeUndefined();
  });

  it('moves a dragged file into a folder and updates the current preview path', async () => {
    const original = vi.mocked(worldApi.sandboxWorkspace).getMockImplementation()!;
    let moved = false;
    vi.mocked(worldApi.sandboxWorkspace).mockImplementation(async <T,>(id: string, action: string): Promise<T> => {
      const query = new URLSearchParams(action.split('?')[1]);
      if (query.get('operation') !== 'list') return await original(id, action) as T;
      const entries = query.get('path') === 'inputs'
        ? moved ? [{ name: 'first.txt', directory: false, blocked: false, size: 4 }] : []
        : [{ name: 'inputs', directory: true, blocked: false, size: 0 }, ...moved ? [] : [{ name: 'first.txt', directory: false, blocked: false, size: 4 }]];
      return { entries } as T;
    });
    const move = vi.spyOn(worldApi, 'moveSandboxFile').mockImplementation(async (_id, path, destination) => { moved = true; return { moved: path, destination }; });
    render(<Workspace />);
    const file = await screen.findByRole('button', { name: 'first.txt' });
    fireEvent.click(file);
    await screen.findByText('Contents of first.txt');
    const dataTransfer = { types: [] as string[], setData(type: string) { this.types.push(type); } };
    fireEvent.dragStart(file, { dataTransfer });
    const folder = screen.getByRole('button', { name: 'inputs' });
    fireEvent.dragOver(folder, { dataTransfer });
    expect(folder.parentElement?.getAttribute('data-drop-target')).toBe('true');
    fireEvent.drop(folder, { dataTransfer });
    await waitFor(() => expect(move).toHaveBeenCalledWith(card.id, 'first.txt', 'inputs/first.txt'));
    await screen.findByText('Contents of inputs/first.txt');
    expect(useOpenFiles.getState().sources[card.id]?.reference).toMatchObject({ path: 'inputs/first.txt' });
    expect(screen.getByRole('button', { name: 'inputs' }).getAttribute('aria-expanded')).toBe('true');
    // A folder cannot be dropped into itself or its descendants.
    dataTransfer.types = [];
    fireEvent.dragStart(screen.getByRole('button', { name: 'inputs' }), { dataTransfer });
    fireEvent.drop(screen.getByRole('button', { name: 'first.txt' }), { dataTransfer });
    expect(move).toHaveBeenCalledTimes(1);
    // A matching MIME string from another browser tree carries no local authority.
    fireEvent.drop(screen.getByRole('button', { name: 'inputs' }), { dataTransfer });
    expect(move).toHaveBeenCalledTimes(1);
  });

  it('keeps a file batch running when switching to Settings and back', async () => {
    let finish!: () => void;
    const upload = vi.spyOn(worldApi, 'uploadSandboxEntry')
      .mockImplementationOnce(() => new Promise(resolve => { finish = () => resolve({ written: 1 }); }))
      .mockResolvedValue({ written: 1 });
    render(<Workspace />);
    await screen.findByRole('button', { name: 'first.txt' });
    fireEvent.drop(screen.getByLabelText('Sandbox files'), { dataTransfer: { types: ['Files'], files: [new File(['a'], 'a.txt'), new File(['b'], 'b.txt')] } });
    await waitFor(() => expect(upload).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole('tab', { name: 'Settings' }));
    await act(async () => finish());
    await waitFor(() => expect(upload).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByRole('tab', { name: 'Workspace' }));
    await screen.findByText('2 completed');
  });

  it('opens a keyboard-accessible context menu for the clicked file, independently of preview selection', async () => {
    const download = vi.spyOn(worldApi, 'downloadSandboxFile').mockResolvedValue(new Blob(['second']));
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: vi.fn(() => 'blob:test') });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() });
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    render(<Workspace />);
    fireEvent.click(await screen.findByRole('button', { name: 'first.txt' }));
    await screen.findByText('Contents of first.txt');
    const second = screen.getByRole('button', { name: 'second.txt' });
    fireEvent.contextMenu(second, { clientX: 20, clientY: 30 });
    fireEvent.click(screen.getByRole('menuitem', { name: 'Download' }));
    await waitFor(() => expect(download).toHaveBeenCalledWith(card.id, 'workspace', 'second.txt', expect.objectContaining({ signal: expect.any(AbortSignal) })));
    expect(screen.queryByRole('menu')).toBeNull();
    fireEvent.keyDown(second, { key: 'F10', shiftKey: true });
    fireEvent.click(screen.getByRole('menuitem', { name: 'Preview' }));
    await screen.findByText('Contents of second.txt');
    fireEvent.contextMenu(second);
    fireEvent.keyDown(screen.getByRole('menu'), { key: 'Escape' });
    expect(screen.queryByRole('menu')).toBeNull();
    expect(document.activeElement).toBe(second);
  });

  it('restores draft and file intent after virtualized unmount, without mounting unopened settings', async () => {
    const view = render(<Workspace />);
    expect(screen.queryByLabelText('Working folder')).toBeNull();
    expect(worldApi.getNodeDocument).not.toHaveBeenCalled();
    fireEvent.change(screen.getByRole('textbox', { name: 'Command' }), { target: { value: 'echo retained' } });
    fireEvent.click(await screen.findByRole('button', { name: 'first.txt' }));
    await screen.findByText('Contents of first.txt');
    view.unmount();
    const projectionKey = surfaceDraftKey(card.id, 'sandbox-static-preview', card.config.runtime, card.config.workspace_path, card.config.workspace_access, false);
    expect(JSON.parse(useNodeSurfaceStore.getState().drafts[projectionKey])).toMatchObject({ path: 'first.txt', text: 'Contents of first.txt' });
    expect(useOpenFiles.getState().sources[card.id]?.reference).toMatchObject({ path: 'first.txt' });
    render(<Workspace />);
    expect((screen.getByRole('textbox', { name: 'Command' }) as HTMLTextAreaElement).value).toBe('echo retained');
    expect(await screen.findByText('Contents of first.txt')).toBeTruthy();
  });

  it('loads subsequent file pages, selects multiple files, and searches nested paths', async () => {
    const original = vi.mocked(worldApi.sandboxWorkspace).getMockImplementation()!;
    const download = vi.spyOn(worldApi, 'downloadSandboxFile').mockRejectedValue(new Error('cancelled in test'));
    vi.mocked(worldApi.sandboxWorkspace).mockImplementation(async <T,>(id: string, action: string): Promise<T> => {
      const query = new URLSearchParams(action.split('?')[1]);
      if (query.get('operation') !== 'list') return await original(id, action) as T;
      if (query.get('query')) return { entries: [{ name: 'report.csv', path: 'deep/report.csv', directory: false, size: 8 }] } as T;
      return query.get('cursor') ? { entries: [{ name: 'last.txt', directory: false, size: 4 }] } as T
        : { entries: [{ name: 'first.txt', directory: false, size: 4 }], next_cursor: 'next-page' } as T;
    });
    render(<Workspace />);
    fireEvent.click(await screen.findByRole('button', { name: 'Load more' }));
    await screen.findByRole('button', { name: 'last.txt' });
    expect(screen.getByRole('button', { name: 'first.txt' })).toBeTruthy();
    expect(screen.queryByRole('checkbox', { name: 'Select first.txt' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'first.txt' }), { ctrlKey: true });
    fireEvent.click(screen.getByRole('button', { name: 'last.txt' }), { shiftKey: true });
    expect(screen.getByText('2 selected')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'first.txt' }).getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByRole('button', { name: 'last.txt' }).getAttribute('aria-pressed')).toBe('true');
    fireEvent.click(screen.getByRole('button', { name: 'Download selected files' }));
    await waitFor(() => expect(download).toHaveBeenCalledTimes(2));
    expect(screen.queryByRole('searchbox')).toBeNull();
    const searchToggle = screen.getByRole('button', { name: 'Search workspace files' });
    fireEvent.click(searchToggle);
    expect(document.activeElement).toBe(screen.getByRole('searchbox'));
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search workspace files' }), { target: { value: 'report' } });
    await screen.findByRole('button', { name: 'deep/report.csv' });
    expect(screen.queryByText('2 selected')).toBeNull();
    fireEvent.keyDown(screen.getByRole('searchbox'), { key: 'Escape' });
    expect(screen.queryByRole('searchbox')).toBeNull();
    expect(screen.getByRole('button', { name: 'first.txt' })).toBeTruthy();
    expect(document.activeElement).toBe(searchToggle);
    fireEvent.click(searchToggle);
    expect((screen.getByRole('searchbox') as HTMLInputElement).value).toBe('');
    fireEvent.click(searchToggle);
    expect(screen.queryByRole('searchbox')).toBeNull();
  });

  it("keeps synthetic workspaces rendered without requesting nonexistent backend resources", async () => {
    const synthetic = { ...card, id: "stress-3", ephemeral: true };
    const view = render(<SandboxWorkspace card={synthetic} />);
    expect(screen.getByRole("textbox", { name: "Command" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Refresh files" }));
    await act(async () => useWorldStore.setState({ socketState: "live" }));
    view.unmount();
    render(<SandboxWorkspace card={synthetic} />);
    await act(async () => {});
    expect(worldApi.getSandbox).not.toHaveBeenCalled();
    expect(worldApi.getSandboxRuntimes).not.toHaveBeenCalled();
    expect(worldApi.sandboxWorkspace).not.toHaveBeenCalled();
    expect(worldApi.getNodeDocument).not.toHaveBeenCalled();
    expect(worldApi.getCredentialBindings).not.toHaveBeenCalled();
  });

  it("keeps command drafts and file selection connected when their sections move out and back", async () => {
    const hosts = new Map<string, HTMLDivElement>();
    const register = ({ id, host }: WorkspaceSectionRegistration) => {
      hosts.set(id, host);
      return () => { hosts.delete(id); };
    };
    const noop = () => {};
    const workspace = (detachedSectionIds: Set<string>) => <WorkspaceSectionProvider cardId={card.id}
      editing={false} detachedSectionIds={detachedSectionIds} hiddenSectionIds={new Set()}
      register={register} onSelect={noop} onDragStart={noop} onHide={noop}>
      <SandboxWorkspace card={card} />
    </WorkspaceSectionProvider>;
    const view = render(workspace(new Set()));
    fireEvent.click(await screen.findByRole("button", { name: "first.txt" }));
    await screen.findByText("Contents of first.txt");
    const command = screen.getByRole("textbox", { name: "Command" }) as HTMLTextAreaElement;
    fireEvent.change(command, { target: { value: "echo draft" } });
    const detached = document.createElement("div");
    view.container.append(detached);

    view.rerender(workspace(new Set(["files", "terminal"])));
    detached.append(hosts.get("files")!, hosts.get("terminal")!);
    expect(screen.getByRole("textbox", { name: "Command" })).toBe(command);
    expect(command.value).toBe("echo draft");
    expect(screen.queryByRole("separator", { name: "Resize file sidebar" })).toBeNull();
    expect(screen.queryByRole("separator", { name: "Resize terminal" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "second.txt" }));
    expect(await screen.findByText("Contents of second.txt")).toBeTruthy();
    fireEvent.change(command, { target: { value: "echo moved" } });

    view.rerender(workspace(new Set()));
    expect(screen.getByRole("textbox", { name: "Command" })).toBe(command);
    expect(command.value).toBe("echo moved");
    expect(detached.childElementCount).toBe(0);
    expect(screen.getByRole("separator", { name: "Resize terminal" })).toBeTruthy();
  });

  it("delivers resize gestures through the card frame and saves both pane sizes", async () => {
    vi.stubGlobal("IntersectionObserver", class { observe() {} disconnect() {} });
    useNodeSurfaceStore.setState({ surfaceLevels: { [card.id]: "workspace" } });
    const outerPointerDown = vi.fn();
    const props = { id: card.id, data: { card }, selected: false, dragging: false } as ComponentProps<typeof WorldCardNode>;
    render(<ReactFlowProvider><div onPointerDown={outerPointerDown}><WorldCardNode {...props} /></div></ReactFlowProvider>);
    await screen.findByRole("button", { name: "first.txt" });
    const terminal = screen.getByRole("separator", { name: "Resize terminal" });
    const sidebar = screen.getByRole("separator", { name: "Resize file sidebar" });
    const area = terminal.parentElement!;
    const files = screen.getByRole("complementary", { name: "Sandbox files" });
    vi.spyOn(area, "getBoundingClientRect").mockReturnValue({ height: 500 } as DOMRect);
    Object.defineProperty(files, "offsetWidth", { configurable: true, value: 224 });
    vi.spyOn(files, "getBoundingClientRect").mockReturnValue({ width: 112 } as DOMRect);
    for (const divider of [terminal, sidebar]) {
      divider.setPointerCapture = vi.fn();
      divider.releasePointerCapture = vi.fn();
    }
    const pointer = (element: HTMLElement, type: string, x: number, y: number) => {
      const event = new MouseEvent(type, { bubbles: true, clientX: x, clientY: y, button: 0 });
      Object.defineProperty(event, "pointerId", { value: 1 });
      fireEvent(element, event);
    };
    pointer(terminal, "pointerdown", 300, 300);
    pointer(terminal, "pointermove", 300, 250);
    expect(terminal.getAttribute("aria-valuenow")).toBe("52");
    pointer(terminal, "pointerup", 300, 250);
    expect(useNodeSurfaceStore.getState().drafts[`sandbox-terminal:${card.id}`]).toBe("52");
    pointer(sidebar, "pointerdown", 112, 200);
    pointer(sidebar, "pointermove", 132, 200);
    expect(sidebar.getAttribute("aria-valuenow")).toBe("264");
    pointer(sidebar, "pointerup", 132, 200);
    expect(useNodeSurfaceStore.getState().drafts[`sandbox-sidebar:${card.id}`]).toBe("264");
    expect(outerPointerDown).not.toHaveBeenCalled();
  });

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
    expect(useOpenFiles.getState().sources[card.id].reference).toEqual({ kind: "sandbox", source_id: card.id, root: "workspace", path: "second.txt" });
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
