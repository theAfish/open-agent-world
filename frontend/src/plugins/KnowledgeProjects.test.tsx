// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useState } from "react";
import type { ReactNode } from "react";
import type { PluginViewProps } from "./sdk";
import { ProjectWorkspace } from "../../../plugins/knowledge_base/frontend/ProjectWorkspace";
import type { BridgeProgress, ProjectTab } from "../../../plugins/knowledge_base/frontend/ProjectWorkspace";

vi.mock("@oaw/plugin-api", async () => ({
  t: (value: string, params?: Record<string, unknown>) => value.replace(/\{(v\d+)\}/g, (_, key: string) => String(params?.[key])),
  hostApiBase: "/api",
  DocumentMarkdown: (await import("../cards/DocumentMarkdown")).DocumentMarkdown,
  WorkspaceEmptyState: (await import("../workspace/WorkspacePrimitives")).WorkspaceEmptyState,
  WorkspaceToolbar: (await import("../workspace/WorkspacePrimitives")).WorkspaceToolbar,
  WorkspaceSection: ({ title, children, style }: { title: string; children: ReactNode; style: object }) => <section aria-label={title} style={style}>{children}</section>,
  useWorkspaceSections: () => ({ isDetached: () => false }),
  NetworkMap: () => <div aria-label="Knowledge graph map" />,
}));

const PROJECT = { id: "project-a", name: "Report A", source_count: 2, converted_count: 2, state: "ready" };
const FILES = [{ id: "file-a", filename: "report.md", relative_path: "report.md", state: "converted", record_id: "md-a", size: 20 },
  { id: "file-si", filename: "SI.csv", relative_path: "data/SI.csv", state: "converted", record_id: "md-si", size: 10 }];
const SPACE = { id: "space-a", name: "Reported values", kind: "table", version: 1, definition: { type: "object" }, system_prompt: "Extract reported values." };
const RESULT = { id: "result-a", project_id: PROJECT.id, project_name: PROJECT.name, space_id: SPACE.id, space_name: SPACE.name,
  kind: "table", space_version: 1, revision: 1, generation: 1, status: "completed", data: { conductivity: 1.2 },
  sources: [{ source_id: "file-a", filename: "report.md", relative_path: "report.md" }], updated_at: "2026-10-27T00:00:00Z" };
type Handler = (action: string, args: Record<string, unknown>) => Promise<Record<string, unknown> | undefined>;
function setup(handler: Handler = async () => undefined, options: { request?: ReturnType<typeof vi.fn>; host?: Record<string, unknown>; deployed?: boolean; agent?: { id: string; name: string } } = {}) {
  const action = vi.fn(async (name: string, args: Record<string, unknown>) => await handler(name, args)
    ?? (name === "projects" ? args.project_id ? { project: { ...PROJECT, files: FILES } } : { projects: [PROJECT] }
      : name === "spaces" ? { spaces: [SPACE] } : name === "projections" ? args.operation === "get" ? { projection: RESULT } : { projections: [] } : {}));
  const request = options.request ?? vi.fn(async () => ({}));
  function View() {
    const [tab, setTab] = useState<ProjectTab>("projects");
    return <><nav>{(["projects", "spaces", "results"] as const).map(item => <button key={item} onClick={() => setTab(item)}>{item}</button>)}</nav>
      <ProjectWorkspace host={{ resourceAction: action, ...options.host } as unknown as PluginViewProps["host"]} cardId="kb" tab={tab} visible onTab={setTab}
        deployed={!!options.deployed} selectedModel="model-a" modelSelect={<span>model-a</span>} agent={options.agent} version={0}
        onSourceOpened={() => undefined} onChanged={async () => undefined} request={request} /></>;
  }
  render(<View />);
  return { action, request };
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("stages a folder locally and preserves its paths when importing the named project", async () => {
  const preview = { token: "preview-1", grouping: "single", depth: 1,
    files: [{ id: "file-1", relative_path: "root/A/report.md", filename: "report.md", size: 10, project_key: "root/A" }],
    projects: [{ key: "root/A", name: "A", file_count: 1 }] };
  const { action } = setup(async name => name === "project_import_preview" ? preview
    : name === "project_import_commit" ? { projects: [PROJECT], source_ids: ["file-1"] } : undefined);
  const file = new File(["# Report"], "report.md", { type: "text/markdown" });
  Object.defineProperty(file, "webkitRelativePath", { value: "root/A/report.md" });
  fireEvent.click(within(screen.getByRole("group", { name: "Projects" })).getByRole("button", { name: "New project" }));
  fireEvent.change(screen.getByLabelText("Project folder"), { target: { files: [file] } });
  await screen.findByText("root/A/report.md");
  expect(screen.getByLabelText("Project name")).toHaveValue("root");
  fireEvent.change(screen.getByLabelText("Project name"), { target: { value: "Reports" } });
  expect(action.mock.calls.some(([name]) => ["project_import_preview", "project_import_commit", "ingest"].includes(name))).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Import and convert" }));
  await waitFor(() => expect(action).toHaveBeenCalledWith("project_import_preview", expect.objectContaining({ files: [expect.objectContaining({ relative_path: "root/A/report.md" })], grouping: "single" }), undefined));
  await waitFor(() => expect(action).toHaveBeenCalledWith("project_import_commit", { token: "preview-1", grouping: "single", depth: 1, auto_process: true,
    projects: [{ key: "root/A", name: "Reports" }] }, undefined));
});

it("adds supporting files to the selected stable project rather than creating a new project", async () => {
  const { action } = setup(async (name, args) => name === "project_import_preview" ? { token: "si", grouping: args.grouping, depth: 1,
    files: [{ id: "si-1", filename: "extra.csv", relative_path: "extra.csv", project_key: "all", size: 2 }], projects: [{ key: "all", name: "extra", file_count: 1 }] }
    : name === "project_import_commit" ? { projects: [PROJECT] } : undefined);
  fireEvent.click(await screen.findByRole("button", { name: /Report A.*files ready/ }));
  fireEvent.click(screen.getByRole("button", { name: "New project" }));
  fireEvent.change(screen.getByRole("combobox", { name: "Project" }), { target: { value: PROJECT.id } });
  fireEvent.change(screen.getByLabelText("Project files or archives"), { target: { files: [new File(["x"], "extra.csv")] } });
  await screen.findByText("extra.csv");
  expect(screen.queryByLabelText("Project name")).toBeNull();
  expect(action.mock.calls.some(([name]) => name === "project_import_commit")).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Import and convert" }));
  await waitFor(() => expect(action).toHaveBeenCalledWith("project_import_commit", expect.objectContaining({ projects: [{ key: "all", name: "Report A", project_id: "project-a" }] }), undefined));
});

it("keeps selection and file reader through navigation without creating a projection", async () => {
  const { action } = setup(async name => name === "markdown" ? { markdown: "# Original report", has_more: false } : undefined);
  fireEvent.click(await screen.findByLabelText("Select Report A"));
  fireEvent.click(screen.getByRole("button", { name: /Report A.*files ready/ }));
  fireEvent.click(await screen.findByRole("button", { name: "report.md" }));
  await screen.findByRole("heading", { name: "Original report" });
  const reader = document.querySelector(".project-reader");
  fireEvent.click(screen.getByRole("button", { name: "spaces" }));
  fireEvent.click(screen.getByRole("button", { name: "projects" }));
  expect(screen.getByLabelText("Select Report A")).toBeChecked();
  expect(document.querySelector(".project-reader")).toBe(reader);
  expect(action.mock.calls.some(([name]) => name === "projection_prepare" || name === "knowledge_draft_manage")).toBe(false);
});

it("keeps streamed progress mounted during navigation and sends only selected projects", async () => {
  let release: () => void = () => undefined;
  const gate = new Promise<void>(resolve => { release = resolve; });
  const request = vi.fn(async (_path: string, _body: unknown, _deployed: boolean, progress: (event: BridgeProgress) => void) => {
    progress({ stage: "projection", done: 1, total: 2, message: "Reading original Markdown" }); await gate; return {};
  });
  const { action } = setup(undefined, { request });
  fireEvent.click(await screen.findByLabelText("Select Report A"));
  fireEvent.change(screen.getByRole("combobox", { name: "Extraction Space" }), { target: { value: "space-a" } });
  fireEvent.click(screen.getByRole("button", { name: "Run Space" }));
  expect(await screen.findByRole("status")).toHaveTextContent("Reading original Markdown");
  const bar = screen.getByRole("progressbar");
  expect(bar).toHaveAttribute("value", "1");
  fireEvent.click(screen.getByRole("button", { name: "projects" }));
  fireEvent.click(screen.getByRole("button", { name: "results" }));
  expect(screen.getByRole("progressbar")).toBe(bar);
  expect(request).toHaveBeenCalledWith("knowledge/kb/project-space", { project_ids: ["project-a"], space_id: "space-a", rerun: false, model: "model-a" }, false, expect.any(Function), expect.any(AbortSignal));
  expect(action.mock.calls.some(([name]) => name === "projection_save")).toBe(false);
  release(); await waitFor(() => expect(screen.queryByRole("progressbar")).toBeNull());
});

it("uses full result data immediately and makes review an optional versioned operation", async () => {
  const revised = { ...RESULT, id: "result-b", generation: 2, data: { conductivity: 1.3 } };
  let saved = false;
  const { action } = setup(async (name, args) => name === "projections" ? args.operation === "get" ? { projection: saved ? revised : RESULT }
    : { projections: [saved ? revised : { ...RESULT, data: undefined, sources: undefined }] }
    : name === "projection_revise" ? (saved = true, { projection: revised }) : undefined);
  fireEvent.click(screen.getByRole("button", { name: "results" }));
  fireEvent.click(await screen.findByRole("button", { name: /Report A.*Reported values/ }));
  await screen.findByText("1.2");
  expect(screen.getByRole("button", { name: "Review result" })).toBeEnabled();
  expect(screen.queryByText("Review stage")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Edit result" }));
  fireEvent.change(screen.getByLabelText("Result JSON"), { target: { value: '{"conductivity":1.3}' } });
  fireEvent.change(screen.getByLabelText("Revision note"), { target: { value: "Corrected against SI" } });
  fireEvent.click(screen.getByRole("button", { name: "Save revision" }));
  await waitFor(() => expect(action).toHaveBeenCalledWith("projection_revise", { projection_id: "result-a", data: { conductivity: 1.3 }, note: "Corrected against SI", expected_revision: 1 }, undefined));
  await screen.findByText("1.3");
});

it("releases only its prepared projections when the connected agent cannot start", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => [] })));
  const runAgent = vi.fn(async () => { throw new Error("Agent unavailable"); });
  const { action } = setup(async name => name === "projection_prepare" ? { projections: [{ ...RESULT, disposition: "created" }, { ...RESULT, id: "other-running", disposition: "running" }] } : undefined,
    { agent: { id: "agent-a", name: "Researcher" }, host: { runAgent } });
  fireEvent.click(await screen.findByLabelText("Select Report A"));
  fireEvent.change(screen.getByRole("combobox", { name: "Extraction Space" }), { target: { value: "space-a" } });
  fireEvent.click(screen.getByRole("button", { name: "Run Space" }));
  await screen.findByRole("alert");
  expect(runAgent).toHaveBeenCalledWith("agent-a", expect.stringContaining('["result-a"]'), true);
  expect(action.mock.calls.filter(([name]) => name === "projection_fail")).toEqual([["projection_fail", { projection_id: "result-a", error: "Agent unavailable" }, undefined]]);
});

const RUNNING = { ...RESULT, status: "running", data: null };

it("stops an agent's extraction from the card: cancels the live run and fails the running result so it can be restarted", async () => {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => ({ ok: true, json: async () => url.includes("/runs?agent_id=")
    ? [{ run_id: "run-live", status: "running", created_at: "2026-11-02T06:33:30Z" }, { run_id: "run-old", status: "succeeded", created_at: "2026-11-01T00:00:00Z" }] : {}, init }));
  vi.stubGlobal("fetch", fetchMock);
  let failed = false;
  const { action } = setup(async (name, args) => name === "projections" ? args.operation === "get" ? { projection: failed ? { ...RESULT, status: "failed" } : RUNNING }
    : { projections: [failed ? { ...RESULT, status: "failed", data: undefined } : { ...RUNNING, data: undefined }] }
    : name === "projection_fail" ? (failed = true, { projection: { ...RESULT, status: "failed" } }) : undefined,
    { agent: { id: "agent-a", name: "Researcher" }, host: { runAgent: vi.fn() } });
  fireEvent.click(screen.getByRole("button", { name: "results" }));
  fireEvent.click(await screen.findByRole("button", { name: /Report A.*Reported values/ }));
  const stop = (await screen.findAllByRole("button", { name: "Stop" })).at(-1)!;
  fireEvent.click(stop);
  await waitFor(() => expect(action).toHaveBeenCalledWith("projection_fail", { projection_id: "result-a", expected_revision: 1, error: "Stopped by the user. Retry extraction." }, undefined));
  const cancels = fetchMock.mock.calls.filter(([, init]) => init?.method === "POST").map(([url]) => url);
  // Only the live run is cancelled; finished runs are left alone.
  expect(cancels).toEqual(["/api/runs/run-live/cancel"]);
  await waitFor(() => expect(screen.queryByRole("button", { name: "Stop" })).toBeNull());
  expect((await screen.findAllByText("Extraction stopped")).length).toBeGreaterThan(0);
  // The failed result can be retried straight away.
  expect(await screen.findByRole("button", { name: "Retry extraction" })).not.toBeDisabled();
});

it("aborts a host-model extraction from the card without reporting an error", async () => {
  let signal: AbortSignal | undefined;
  const request = vi.fn((_path: string, _body: unknown, _deployed: boolean, onProgress?: (event: BridgeProgress) => void, abort?: AbortSignal) => {
    signal = abort; onProgress?.({ stage: "projection", message: "Reading original Markdown", done: 0, total: 1 });
    return new Promise<Record<string, unknown>>((_resolve, reject) => abort?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError"))));
  });
  setup(undefined, { request });
  fireEvent.click(await screen.findByLabelText("Select Report A"));
  fireEvent.change(screen.getByRole("combobox", { name: "Extraction Space" }), { target: { value: "space-a" } });
  fireEvent.click(screen.getByRole("button", { name: "Run Space" }));
  const stop = (await screen.findAllByRole("button", { name: "Stop" }))[0];
  expect(signal?.aborted).toBe(false);
  fireEvent.click(stop);
  await waitFor(() => expect(signal?.aborted).toBe(true));
  await screen.findAllByText("Extraction stopped");
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.queryByRole("progressbar")).toBeNull();
});

it("uses the deployment bridge without offering a live model or engineering settings", async () => {
  const { request } = setup(undefined, { deployed: true });
  fireEvent.click(await screen.findByLabelText("Select Report A"));
  fireEvent.change(screen.getByRole("combobox", { name: "Extraction Space" }), { target: { value: "space-a" } });
  fireEvent.click(screen.getByRole("button", { name: "Run Space" }));
  await waitFor(() => expect(request).toHaveBeenCalledWith("knowledge/kb/project-space", { project_ids: ["project-a"], space_id: "space-a", rerun: false }, true, expect.any(Function), expect.any(AbortSignal)));
  expect(screen.queryByText("model-a")).toBeNull();
});

it("loads additional project pages without losing the selected execution scope", async () => {
  const next = { ...PROJECT, id: "project-b", name: "Report B" };
  const { action, request } = setup(async (name, args) => name === "projects" && !args.project_id
    ? args.offset === 200 ? { projects: [next], has_more: false, next_offset: null }
      : { projects: [PROJECT], has_more: true, next_offset: 200 } : undefined);
  fireEvent.click(await screen.findByLabelText("Select Report A"));
  fireEvent.click(screen.getByRole("button", { name: "Load more projects" }));
  fireEvent.click(await screen.findByLabelText("Select Report B"));
  expect(screen.getByLabelText("Select Report A")).toBeChecked();
  expect(action).toHaveBeenCalledWith("projects", { limit: 200, offset: 200 }, undefined);
  fireEvent.change(screen.getByRole("combobox", { name: "Extraction Space" }), { target: { value: "space-a" } });
  fireEvent.click(screen.getByRole("button", { name: "Run Space" }));
  await waitFor(() => expect(request).toHaveBeenCalledWith("knowledge/kb/project-space", expect.objectContaining({ project_ids: ["project-a", "project-b"] }), false, expect.any(Function), expect.any(AbortSignal)));
});

it("does not mistake the previous completed agent run for a newly queued extraction", async () => {
  const previousRun = { run_id: "previous-run", status: "completed", created_at: new Date().toISOString() };
  const fetchMock = vi.fn(async () => ({ ok: true, json: async () => [previousRun] }));
  vi.stubGlobal("fetch", fetchMock);
  const runAgent = vi.fn(async () => undefined);
  const { action } = setup(async name => name === "projection_prepare"
    ? { projections: [{ ...RESULT, status: "running", disposition: "created" }] } : undefined,
  { agent: { id: "agent-a", name: "Researcher" }, host: { runAgent } });
  fireEvent.click(await screen.findByLabelText("Select Report A"));
  fireEvent.change(screen.getByRole("combobox", { name: "Extraction Space" }), { target: { value: "space-a" } });
  fireEvent.click(screen.getByRole("button", { name: "Run Space" }));
  await waitFor(() => expect(runAgent).toHaveBeenCalledOnce());
  await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(2), { timeout: 3500 });
  expect(action.mock.calls.some(([name]) => name === "projection_fail")).toBe(false);
});

it("notices a finished agent run (status \"succeeded\") and shows the saved result instead of staying on running", async () => {
  let runs: unknown[] = [];
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => runs })));
  const runAgent = vi.fn(async () => { runs = [{ run_id: "run-new", status: "succeeded", created_at: new Date(Date.now() + 1000).toISOString() }]; });
  const { action } = setup(async (name, args) => name === "projection_prepare"
    ? { projections: [{ ...RESULT, status: "running", disposition: "created" }] }
    : name === "projections" && args.operation === "get" ? { projection: RESULT } : undefined,
  { agent: { id: "agent-a", name: "Researcher" }, host: { runAgent } });
  fireEvent.click(await screen.findByLabelText("Select Report A"));
  fireEvent.change(screen.getByRole("combobox", { name: "Extraction Space" }), { target: { value: "space-a" } });
  fireEvent.click(screen.getByRole("button", { name: "Run Space" }));
  await screen.findAllByText("The agent is extracting. Open the log to follow it.");
  await waitFor(() => expect(screen.queryAllByText("The agent is extracting. Open the log to follow it.")).toHaveLength(0), { timeout: 5000 });
  expect(action.mock.calls.some(([name]) => name === "projection_fail")).toBe(false);
});

it("shows each relation's description (or its supporting snippet) in the list and under a selected concept", async () => {
  const GRAPH = { ...RESULT, kind: "graph", data: {
    entities: [{ id: "a", name: "GAN" }, { id: "b", name: "CrystalGAN" }, { id: "c", name: "DiscoGAN" }],
    relations: [{ source: "b", target: "a", predicate: "extends", description: "CrystalGAN adapts GAN to crystals." },
      { source: "b", target: "c", predicate: "compared_with", snippet: "CrystalGAN was compared with DiscoGAN." }] } };
  setup(async (name, args) => name === "projections" ? args.operation === "get" ? { projection: GRAPH } : { projections: [{ ...GRAPH, data: undefined }] } : undefined);
  fireEvent.click(screen.getByRole("button", { name: "results" }));
  fireEvent.click(await screen.findByRole("button", { name: /Report A/ }));
  expect(await screen.findByText("CrystalGAN adapts GAN to crystals.")).toBeTruthy();
  expect(screen.getByText("CrystalGAN was compared with DiscoGAN.")).toBeTruthy();
  fireEvent.click(screen.getAllByRole("button", { name: "GAN" })[0]);
  // The selected concept lists its own relations with their descriptions.
  expect((await screen.findAllByText("CrystalGAN adapts GAN to crystals.")).length).toBe(2);
  expect(screen.getAllByText("CrystalGAN was compared with DiscoGAN.").length).toBe(1);
});
