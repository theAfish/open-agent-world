// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import type { PluginViewProps } from "./sdk";
import { useWorldStore } from "../state/worldStore";
import { TEST_CATALOG } from '../state/catalog.fixture';
import type { WorldCard, WorldEdge } from "../types/world";
import { WorkspaceSectionProvider, type WorkspaceSectionProps, type WorkspaceSectionRegistration } from "../workspace/WorkspaceSection";
import { WorkspaceAccess } from "../workspace/WorkspaceAccess";
import plugin from "../../../plugins/knowledge_base/frontend";

vi.mock("@oaw/plugin-api", async () => {
  const sections = await import("../workspace/WorkspaceSection");
  return ({
  ...await vi.importActual<typeof import('./sdk')>('./sdk'),
  WorkspaceEmptyState: (await import("../workspace/WorkspacePrimitives")).WorkspaceEmptyState,
  WorkspaceToolbar: (await import("../workspace/WorkspacePrimitives")).WorkspaceToolbar,
  t: (value: string, params?: Record<string, unknown>) => params
    ? value.replace(/\{(v\d+)\}/g, (_, key: string) => String(params[key]))
    : value,
  useLocale: () => undefined,
  NetworkMap: () => <div aria-label="Knowledge graph map" />,
  useWorkspaceSections: sections.useWorkspaceSections,
  WorkspaceSection: ({ title, children, ...props }: WorkspaceSectionProps) =>
    <sections.WorkspaceSection {...props} title={title}><section aria-label={title}>{children}</section></sections.WorkspaceSection>,
  });
});

const SOURCE = { id: "source-1", filename: "sintering.md", media_type: "text/markdown", size: 2048,
  created_at: "2026-09-23T00:00:00Z", markdown: null, record_id: null, records: 0 };
const CONVERTED = { ...SOURCE, markdown: { artifact_id: "artifact-1", size: 2048, engine: "text" },
  record_id: "record-1" };
const SCHEMA = { id: "schema-1", name: "Process graph", description: "Samples and processes",
  domain: "materials", kind: "literature", version: 1 };
const EXPERIMENT_SCHEMA = { id: "schema-2", name: "Conductivity run", description: null,
  domain: "materials", kind: "experiment", version: 1 };
const DOCUMENT = { record_id: "record-1", filename: "sintering.md", engine: "text",
  total_characters: 42, offset: 0, markdown: "# Sintering of Si3N4", has_more: false };
/** One knowledge draft as ``knowledge_drafts`` returns it, opened over the converted source. */
const DRAFT = { id: "draft-1", group_id: "collection-1", title: "sintering.md", status: "draft", revision: 1,
  run_id: null, review_log: [], created_by: "user:desktop", created_at: "2026-09-23T00:00:00Z", updated_at: "2026-09-23T00:00:00Z",
  entities: [{ id: "concept:si3n4", name: "Si3N4", description: "a ceramic", aliases: ["silicon nitride"], source_ids: ["source-1"] },
    { id: "concept:sintering", name: "Sintering", description: null, aliases: [], source_ids: ["source-1"] }],
  relations: [{ id: "concept:si3n4|processed_by|concept:sintering", source: "concept:si3n4",
    target: "concept:sintering", predicate: "processed_by", description: "Si3N4 powder is densified by pressureless sintering.",
    evidence_level: 2, snippet: "Si3N4 was sintered", source_ids: ["source-1"] }],
  sources: [{ source_id: "source-1", filename: "sintering.md", extracted_at: "2026-09-23T00:00:00Z", missing: false }] };

const PROJECT_FILE = { ...CONVERTED, relative_path: "sintering.md", state: "converted" };
const PROJECT = { id: "project-1", name: "Sintering project", description: "Report and supporting data",
  source_count: 1, converted_count: 1, state: "ready", files: [PROJECT_FILE] };
const SPACE = { id: "space-1", name: "Process data", description: "Reported process data", version: 1,
  kind: "table" as const, definition: { type: "object", properties: { temperature: { type: "number" } } },
  system_prompt: "Read reported process data." };
const RESULT = { id: "projection-1", project_id: PROJECT.id, project_name: PROJECT.name, space_id: SPACE.id,
  space_name: SPACE.name, space_version: 1, kind: "table" as const, status: "completed" as const,
  revision: 2, generation: 1, data: { temperature: 1750 }, source_count: 1, sources: DRAFT.sources.map(s => ({ ...s, relative_path: s.filename })),
  updated_at: "2026-10-27T00:00:00Z", space_snapshot: SPACE };
const PREVIEW = { token: "import-token", grouping: "files", depth: 1,
  projects: [{ key: "new.md", name: "new", file_count: 1 }],
  files: [{ id: "file-1", relative_path: "new.md", filename: "new.md", size: 5, project_key: "new.md" }] };
function materialData(name: string, args: Record<string, unknown>) {
  if (name === "projects") return args.project_id ? { project: PROJECT } : { projects: [PROJECT] };
  if (name === "spaces") return { spaces: [SPACE] };
  if (name === "projections") return args.operation === "get" ? { projection: RESULT } : { projections: [RESULT] };
  if (name === "markdown") return DOCUMENT;
  return undefined;
}
async function chooseProject() {
  fireEvent.click(await screen.findByLabelText("Select Sintering project"));
  fireEvent.change(screen.getByRole("combobox", { name: "Extraction Space" }), { target: { value: SPACE.id } });
}
async function openResult() {
  fireEvent.click(screen.getByRole("tab", { name: "Results" }));
  fireEvent.click(await screen.findByRole("button", { name: /Sintering project.*Process data/ }));
  await screen.findByRole("button", { name: "Edit result" });
}

const overview = (counts: Record<string, number> = {}) => ({
  collection: { id: "collection-1", name: "Knowledge base" },
  settings: { collection_name: "Knowledge base", pdf_engine: "auto", mineru_base_url: "" },
  engines: ["text"],
  counts: { sources: 1, records: 1, schemas: 1, knowledge_drafts: 0, published_knowledge: 0,
    entities: 0, relations: 0, insights: 0, published_insights: 0, ...counts },
  active_jobs: 0,
});
const EMPTY: Record<string, Record<string, unknown>> = {
  sources: { sources: [] }, schemas: { schemas: [] }, projects: { projects: [] }, spaces: { spaces: [] }, projections: { projections: [] },
  jobs: { jobs: [] }, graph: { entities: [], relations: [], truncated: false },
};

type Action = (name: string, args: Record<string, unknown>, confirm?: boolean) =>
  Promise<Record<string, unknown> | undefined>;

/** Renders the workspace over a stub card, answering whatever the test leaves out. */
const open = (handler: Action, overrides: { card?: Record<string, unknown>; host?: Record<string, unknown>;
  wrapper?: (children: ReactNode) => ReactNode } = {}) => {
  const action = vi.fn(async (name: string, args: Record<string, unknown>, confirm?: boolean) =>
    await handler(name, args, confirm) ?? (name === "overview" ? overview() : EMPTY[name] ?? {}));
  const Workspace = plugin.views.workspace;
  const props = { card: { id: "card-1", name: "Knowledge", config: { default_model: "" }, ...overrides.card },
    host: { resourceAction: action, ...overrides.host } };
  const workspace = <Workspace {...props as unknown as PluginViewProps} />;
  render(overrides.wrapper ? overrides.wrapper(workspace) : workspace);
  return action;
};

const ORIGINAL_UPDATE_CARD = useWorldStore.getState().updateCard;

it("saves a token through its private HTTP endpoint and keeps it when Settings reopens", async () => {
  let saved = false;
  const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
    if (init?.method === "PUT") saved = JSON.parse(String(init.body)).value !== null;
    return { ok: true, json: async () => ({ configured: saved, source: saved ? "card" : null }) };
  });
  vi.stubGlobal("fetch", fetchMock);
  const action = open(async () => undefined);
  fireEvent.click(screen.getByRole("button", { name: "Settings" }));
  const input = await screen.findByLabelText("MinerU token") as HTMLInputElement;
  fireEvent.change(input, { target: { value: "ui-private-token" } });
  fireEvent.click(screen.getByRole("button", { name: "Save token" }));
  await screen.findByText("Saved securely");
  expect(input.value).toBe("");
  expect(fetchMock).toHaveBeenCalledWith("/api/knowledge/card-1/mineru-token", expect.objectContaining({
    method: "PUT", body: JSON.stringify({ value: "ui-private-token" }) }));
  expect(JSON.stringify(action.mock.calls)).not.toContain("ui-private-token");

  fireEvent.click(screen.getByRole("button", { name: "Materials" }));
  fireEvent.click(screen.getByRole("button", { name: "Settings" }));
  await screen.findByText("Saved securely");
  expect((screen.getByLabelText("MinerU token") as HTMLInputElement).value).toBe("");
  expect((screen.getByRole("button", { name: "Save token" }) as HTMLButtonElement).disabled).toBe(true);
  expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "PUT")).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "Remove token" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/knowledge/card-1/mineru-token",
    expect.objectContaining({ method: "PUT", body: JSON.stringify({ value: null }) })));
});

it("imports a new project without renaming an already selected project", async () => {
  const action = open(async (name, args) => {
    if (name === "project_import_preview") return PREVIEW;
    if (name === "project_import_commit") return { projects: [{ ...PROJECT, id: "project-2" }] };
    return materialData(name, args);
  });
  fireEvent.click(await screen.findByLabelText("Select Sintering project"));
  fireEvent.change(screen.getByLabelText("Project files or archives"), { target: { files: [new File(["hello"], "new.md")] } });
  await screen.findByText("Organize import");
  fireEvent.change(screen.getByLabelText("Project name for new.md"), { target: { value: "New report" } });
  fireEvent.click(screen.getByRole("button", { name: "Import and convert" }));
  await waitFor(() => expect(action).toHaveBeenCalledWith("project_import_commit", expect.objectContaining({
    projects: [{ key: "new.md", name: "New report" }], auto_process: true })));
  expect(action.mock.calls.some(([name]) => name === "project_manage")).toBe(false);
});

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => ({ ok: true,
    json: async () => url.includes("/runs?") ? [] : ({ configured: false, source: null }) })));
  // The Knowledge and Insights sections draw a React Flow map, which measures itself on mount.
  vi.stubGlobal("ResizeObserver", class {
    observe() {} unobserve() {} disconnect() {}
  });
  useWorldStore.setState({ catalog: TEST_CATALOG, modelCatalog: { revision: 1, default_model: null, connections: [{
    id: "connection-1", name: "OpenAI", adapter: "openai", base_url: "", enabled: true,
    auth_mode: "api_key", api_key_configured: true,
    models: [{ id: "model-1", name: "gpt-4o", model_id: "gpt-4o", enabled: true }],
  }] } });
});
afterEach(() => {
  cleanup(); vi.unstubAllGlobals();
  // A test may stand in for edges/cards/updateCard to exercise the Settings
  // section's "Connected agents" picker; every other test expects the real,
  // empty-by-default world, so put it back.
  useWorldStore.setState({ edges: [], cards: [], updateCard: ORIGINAL_UPDATE_CARD });
});

it("keeps a detached project reader and unsent search mounted while another section opens", async () => {
  const destination = document.createElement("div"); document.body.append(destination);
  const register = (section: WorkspaceSectionRegistration) => { if (section.id === "projects") destination.append(section.host); return () => section.host.remove(); };
  try {
    open(async (name, args) => materialData(name, args), {
      wrapper: children => <WorkspaceSectionProvider cardId="card-1" editing={false} detachedSectionIds={new Set(["projects"])}
        hiddenSectionIds={new Set()} register={register} onSelect={() => {}} onDragStart={() => {}} onHide={() => {}}>{children}</WorkspaceSectionProvider>,
    });
    fireEvent.click(await within(destination).findByRole("button", { name: /Sintering project/ }));
    fireEvent.click(await within(destination).findByRole("button", { name: "sintering.md" }));
    await within(destination).findByRole("heading", { name: "Sintering of Si3N4" });
    const reader = destination.querySelector(".project-reader");
    const search = within(destination).getByLabelText("Find projects") as HTMLInputElement;
    fireEvent.change(search, { target: { value: "unsent query" } });
    fireEvent.click(screen.getByRole("button", { name: "Spaces" }));
    expect(await screen.findByRole("button", { name: "New Space" })).toBeTruthy();
    expect(destination.querySelector(".project-reader")).toBe(reader);
    expect(within(destination).getByLabelText("Find projects")).toBe(search);
    expect(search.value).toBe("unsent query");
  } finally { cleanup(); destination.remove(); }
});

it("filters project results by Space and restores the complete result set", async () => {
  const second = { ...RESULT, id: "projection-2", project_name: "Second project", space_id: "space-2", space_name: "Other output" };
  open(async (name, args) => name === "spaces" ? { spaces: [SPACE, { ...SPACE, id: "space-2", name: "Other output" }] }
    : name === "projections" ? { projections: [RESULT, second] } : materialData(name, args));
  fireEvent.click(screen.getByRole("tab", { name: "Results" }));
  await screen.findByRole("button", { name: /Second project/ });
  const select = screen.getByRole("combobox", { name: "Extraction Space" });
  fireEvent.change(select, { target: { value: SPACE.id } });
  expect(screen.queryByRole("button", { name: /Second project/ })).toBeNull();
  expect(screen.getByRole("button", { name: /Sintering project/ })).toBeTruthy();
  fireEvent.change(select, { target: { value: "" } });
  expect(screen.getByRole("button", { name: /Second project/ })).toBeTruthy();
});

it("does not mount detached Space or settings sections excluded by deployment grants", async () => {
  const register = vi.fn((_section: WorkspaceSectionRegistration) => () => {});
  open(async (name, args) => materialData(name, args), { host: { deployment: {} },
    wrapper: children => <WorkspaceAccess.Provider value={{ deployed: true, permissions: { "card-1": ["projects"] },
      plugin_access: { "card-1": { config_fields: [], document_fields: [], summary_fields: [], document_actions: [], downloads: [], resource_actions: {}, execution: false } } }}>
      <WorkspaceSectionProvider cardId="card-1" editing={false} detachedSectionIds={new Set(["spaces", "settings"])}
        hiddenSectionIds={new Set()} register={register} onSelect={() => {}} onDragStart={() => {}} onHide={() => {}}>{children}</WorkspaceSectionProvider>
    </WorkspaceAccess.Provider>,
  });
  await screen.findByRole("button", { name: /Sintering project/ });
  expect(register.mock.calls.map(([section]) => section.id)).toEqual(["projects"]);
  expect(screen.queryByRole("button", { name: "New Space" })).toBeNull();
  expect(screen.queryByLabelText("MinerU token")).toBeNull();
  expect(fetch).not.toHaveBeenCalled();
});

it("keeps project data visible when experiment data is outside the release grants", async () => {
  open(async (name, args) => {
    if (name === "experiments") throw new Error("Resource action is not published");
    return materialData(name, args);
  }, { host: { deployment: {} } });
  expect(await screen.findByRole("button", { name: /Sintering project/ })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: /Sintering project/ }));
  expect(await screen.findByRole("button", { name: "sintering.md" })).toBeTruthy();
});

it("imports a selected file and requests conversion only after its project preview is confirmed", async () => {
  let imported = false;
  const action = open(async (name, args) => {
    if (name === "projects") return args.project_id ? { project: PROJECT } : { projects: imported ? [PROJECT] : [] };
    if (name === "project_import_preview") return PREVIEW;
    if (name === "project_import_commit") { imported = true; return { projects: [PROJECT] }; }
    return materialData(name, args);
  });
  fireEvent.change(screen.getByLabelText("Project files or archives"), { target: { files: [new File(["hello"], "new.md")] } });
  await screen.findByText("Organize import");
  expect(action.mock.calls.some(([name]) => name === "project_import_commit")).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Import and convert" }));
  await waitFor(() => expect(action).toHaveBeenCalledWith("project_import_commit", expect.objectContaining({ token: PREVIEW.token, auto_process: true })));
  fireEvent.click(await screen.findByRole("button", { name: "sintering.md" }));
  expect(await screen.findByRole("heading", { name: "Sintering of Si3N4" })).toBeTruthy();
  expect(action.mock.calls.some(([name]) => name === "process")).toBe(false);
});

it("snapshots selected files before resetting the input clears its FileList", async () => {
  const action = open(async (name, args) => name === "project_import_preview" ? PREVIEW : materialData(name, args));
  const input = screen.getByLabelText("Project files or archives") as HTMLInputElement;
  let files: File[] = [new File(["first"], "first.md"), new File(["second"], "second.md")];
  Object.defineProperty(input, "files", { configurable: true, get: () => files });
  Object.defineProperty(input, "value", { configurable: true, set: () => { files = []; }, get: () => "" });
  fireEvent.change(input);
  await waitFor(() => expect(action).toHaveBeenCalledWith("project_import_preview", expect.objectContaining({
    files: [expect.objectContaining({ filename: "first.md" }), expect.objectContaining({ filename: "second.md" })] })));
});

it("keeps converted projects visible and retries only the project with a failed file", async () => {
  const failed = { ...PROJECT, id: "project-2", name: "Scanned report", converted_count: 0, state: "failed",
    files: [{ ...PROJECT_FILE, id: "source-2", state: "failed", record_id: null, error: "OCR connection unavailable" }] };
  const action = open(async (name, args) => name === "projects"
    ? args.project_id ? { project: args.project_id === failed.id ? failed : PROJECT } : { projects: [PROJECT, failed] }
    : materialData(name, args));
  fireEvent.click(await screen.findByRole("button", { name: /Scanned report/ }));
  expect((await screen.findByRole("alert")).textContent).toContain("OCR connection unavailable");
  fireEvent.click(screen.getByRole("button", { name: "Convert pending files" }));
  await waitFor(() => expect(action).toHaveBeenCalledWith("project_process", { project_ids: ["project-2"] }));
  expect(screen.getByRole("button", { name: /Sintering project/ })).toBeTruthy();
});

it("enables a converting project file when polling reports its Markdown is ready", async () => {
  let ready = false;
  const action = open(async (name, args) => {
    if (name === "projects") { const project = ready ? PROJECT : { ...PROJECT, state: "converting", converted_count: 0,
      files: [{ ...PROJECT_FILE, state: "converting", record_id: null }] };
      return args.project_id ? { project } : { projects: [project] }; }
    return materialData(name, args);
  });
  fireEvent.click(await screen.findByRole("button", { name: /Sintering project/ }));
  expect((await screen.findByRole("button", { name: "sintering.md" })).hasAttribute("disabled")).toBe(true);
  ready = true;
  await waitFor(() => expect(screen.getByRole("button", { name: "sintering.md" }).hasAttribute("disabled")).toBe(false), { timeout: 4000 });
  fireEvent.click(screen.getByRole("button", { name: "sintering.md" }));
  await screen.findByRole("heading", { name: "Sintering of Si3N4" });
  expect(action).toHaveBeenCalledWith("markdown", expect.objectContaining({ source_id: SOURCE.id }));
});

it("routes empty results to projects and empty Spaces to an editable schema", async () => {
  open(async () => undefined);
  fireEvent.click(screen.getByRole("tab", { name: "Results" }));
  fireEvent.click(await screen.findByRole("button", { name: "Choose projects" }));
  expect(screen.getByRole("tab", { name: "Projects" }).getAttribute("aria-selected")).toBe("true");
  fireEvent.click(screen.getByRole("tab", { name: "Spaces" }));
  fireEvent.click(await screen.findByRole("button", { name: "Structured data" }));
  expect(await screen.findByLabelText("Output schema")).toBeTruthy();
  expect(screen.getByLabelText("Extraction instructions")).toBeTruthy();
});

it("searches converted project files and opens attributable source excerpts", async () => {
  const action = open(async (name, args) => name === "search" ? { results: [{ source_id: SOURCE.id, filename: SOURCE.filename,
    heading_path: "Methods", excerpt: "Sintering at 1750 C" }] } : materialData(name, args));
  const input = await screen.findByLabelText("Find projects");
  fireEvent.change(input, { target: { value: "1750" } });
  fireEvent.click(screen.getByRole("button", { name: "Search contents" }));
  expect(await screen.findByText("Sintering at 1750 C")).toBeTruthy();
  expect(screen.getByText("Methods")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: /sintering.md/ }));
  expect(await screen.findByRole("heading", { name: "Sintering of Si3N4" })).toBeTruthy();
  expect(action).toHaveBeenCalledWith("search", { query: "1750", all_groups: true });
});

it("shows project search failures inline and preserves the project list", async () => {
  open(async (name, args) => { if (name === "search") throw new Error("Search temporarily unavailable"); return materialData(name, args); });
  fireEvent.change(await screen.findByLabelText("Find projects"), { target: { value: "Sintering" } });
  fireEvent.click(screen.getByRole("button", { name: "Search contents" }));
  expect((await screen.findByRole("alert")).textContent).toContain("Search temporarily unavailable");
  expect(screen.getByRole("button", { name: /Sintering project/ })).toBeTruthy();
});

it("runs a project Space through the host model bridge without saving model output in the UI", async () => {
  const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => ({ ok: true, json: async () => ({ projections: [RESULT] }) }));
  vi.stubGlobal("fetch", fetchMock);
  const action = open(async (name, args) => materialData(name, args));
  await chooseProject();
  fireEvent.click(screen.getByRole("button", { name: "Run Space" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/knowledge/card-1/project-space", expect.objectContaining({ method: "POST" })));
  expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toMatchObject({ project_ids: [PROJECT.id], space_id: SPACE.id, rerun: false });
  expect(action.mock.calls.some(([name]) => name === "projection_save" || name === "knowledge_draft_manage")).toBe(false);
  expect(screen.getByRole("tab", { name: "Results" }).getAttribute("aria-selected")).toBe("true");
});

it("preserves project selection across navigation without creating or starting another result", async () => {
  const action = open(async (name, args) => materialData(name, args));
  await chooseProject();
  fireEvent.click(screen.getByRole("tab", { name: "Results" }));
  fireEvent.click(screen.getByRole("button", { name: "Experiment" }));
  fireEvent.click(screen.getByRole("button", { name: "Materials" }));
  fireEvent.click(screen.getByRole("tab", { name: "Projects" }));
  expect((screen.getByLabelText("Select Sintering project") as HTMLInputElement).checked).toBe(true);
  expect((screen.getByRole("combobox", { name: "Extraction Space" }) as HTMLSelectElement).value).toBe(SPACE.id);
  expect(action.mock.calls.some(([name]) => ["projection_prepare", "project_manage", "projection_save"].includes(name))).toBe(false);
});

it("restores the selected result and unfinished JSON revision after the workspace closes", async () => {
  let draft: Record<string, unknown> = {};
  const host = { draft: { get: () => draft, set: (value: Record<string, unknown>) => { draft = value; } } };
  open(async (name, args) => materialData(name, args), { host });
  await openResult();
  fireEvent.click(screen.getByRole("button", { name: "Edit result" }));
  fireEvent.change(screen.getByLabelText("Result JSON"), { target: { value: '{"temperature":1800}' } });
  fireEvent.change(screen.getByLabelText("Revision note"), { target: { value: "Check SI correction" } });
  cleanup();
  open(async (name, args) => materialData(name, args), { host });
  fireEvent.click(screen.getByRole("tab", { name: "Results" }));
  expect((await screen.findByLabelText("Result JSON") as HTMLTextAreaElement).value).toBe('{"temperature":1800}');
  expect((screen.getByLabelText("Revision note") as HTMLInputElement).value).toBe("Check SI correction");
});

it.each(["extract", "research"])("projects through a %s agent and excludes a read-only connection", async access => {
  useWorldStore.setState({ cards: [{ id: "card-1", type: "knowledge.base", name: "Knowledge" },
    { id: "librarian", type: "agent", name: "Librarian" }, { id: "researcher", type: "agent", name: "Researcher" }] as unknown as WorldCard[],
    edges: [{ id: "read", source: "librarian", target: "card-1", relationship: "knowledge.base.read" },
      { id: "write", source: "researcher", target: "card-1", relationship: `knowledge.base.${access}` }] as unknown as WorldEdge[] });
  const runAgent = vi.fn(async () => undefined);
  const action = open(async (name, args) => name === "projection_prepare" ? { projections: [
    { ...RESULT, id: "new-run", status: "running", disposition: "created" },
    { ...RESULT, id: "existing-run", status: "running", disposition: "running" }] } : materialData(name, args), { host: { runAgent } });
  await chooseProject(); fireEvent.click(screen.getByRole("button", { name: "Run Space" }));
  await waitFor(() => expect(runAgent).toHaveBeenCalledTimes(1));
  const [agentId, prompt] = runAgent.mock.calls[0] as unknown as [string, string];
  expect(agentId).toBe("researcher"); expect(prompt).toContain("new-run"); expect(prompt).not.toContain("existing-run");
  expect(prompt).toContain("knowledge_projection_prompt"); expect(prompt).toContain("knowledge_projection_save");
  expect(action).toHaveBeenCalledWith("projection_prepare", { project_ids: [PROJECT.id], space_id: SPACE.id, rerun: false });
});

it("observes a failed agent run and releases its unfinished projection for retry", async () => {
  useWorldStore.setState({ cards: [{ id: "card-1", type: "knowledge.base" }, { id: "researcher", type: "agent", name: "Researcher" }] as unknown as WorldCard[],
    edges: [{ id: "write", source: "researcher", target: "card-1", relationship: "knowledge.base.research" }] as unknown as WorldEdge[] });
  let failed = false;
  let launched = false;
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => launched
    ? [{ run_id: "run-1", status: "failed", created_at: new Date().toISOString(), error: "Model connection unavailable" }] : [] })));
  const action = open(async (name, args) => {
    const result = { ...RESULT, status: failed ? "failed" : "running", error: failed ? "Model connection unavailable" : undefined };
    if (name === "projection_prepare") return { projections: [{ ...result, disposition: "created" }] };
    if (name === "projection_fail") { failed = true; return { projection: { ...result, status: "failed" } }; }
    if (name === "projections") return args.operation === "get" ? { projection: result } : { projections: [result] };
    return materialData(name, args);
  }, { host: { runAgent: vi.fn(async () => { launched = true; }) } });
  await chooseProject(); fireEvent.click(screen.getByRole("button", { name: "Run Space" }));
  await waitFor(() => expect(action).toHaveBeenCalledWith("projection_fail", expect.objectContaining({
    projection_id: RESULT.id, error: "Model connection unavailable" })), { timeout: 5000 });
  fireEvent.click(await screen.findByRole("button", { name: /Sintering project.*Process data/ }));
  expect(await screen.findByRole("button", { name: "Retry extraction" })).toBeTruthy();
});

it("runs optional result review through the host bridge and selects its new version", async () => {
  let reviewed = false;
  const revised = { ...RESULT, id: "projection-2", generation: 2, data: { temperature: 1800 } };
  const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => { reviewed = true; return { ok: true, json: async () => ({ projection: revised }) }; });
  vi.stubGlobal("fetch", fetchMock);
  open(async (name, args) => name === "projections" ? args.operation === "get"
    ? { projection: reviewed ? revised : RESULT } : { projections: [reviewed ? revised : RESULT] } : materialData(name, args));
  await openResult();
  expect(screen.getByText((1750).toLocaleString())).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Review result" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/knowledge/card-1/review-projection", expect.objectContaining({ method: "POST" })));
  expect(await screen.findByText((1800).toLocaleString())).toBeTruthy();
});

it("applies custom post-processing only after its instructions are explicitly submitted", async () => {
  const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => ({ ok: true, json: async () => ({ projection: RESULT }) }));
  vi.stubGlobal("fetch", fetchMock);
  open(async (name, args) => materialData(name, args));
  await openResult();
  fireEvent.click(screen.getByRole("button", { name: "Post-process" }));
  expect(fetchMock).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("Post-processing instructions"), { target: { value: "Normalize temperatures to Celsius" } });
  expect(fetchMock).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Run post-processing" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toMatchObject({
    projection_id: RESULT.id, instruction: "Normalize temperatures to Celsius" });
});

it("shows streamed project progress and keeps it alive while another tab is open", async () => {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({ start(value) { controller = value; } });
  const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => ({ ok: true, headers: new Headers({ "content-type": "application/x-ndjson" }), body }));
  vi.stubGlobal("fetch", fetchMock);
  open(async (name, args) => materialData(name, args));
  await chooseProject(); fireEvent.click(screen.getByRole("button", { name: "Run Space" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  controller.enqueue(new TextEncoder().encode(JSON.stringify({ event: "progress", stage: "projection", done: 0, total: 1, message: "Reading supporting files" }) + "\n"));
  await within(screen.getByRole("region", { name: "Results" })).findByText("Reading supporting files");
  fireEvent.click(screen.getByRole("tab", { name: "Projects" }));
  expect(within(screen.getByRole("region", { name: "Projects" })).getByText("Reading supporting files")).toBeTruthy();
  fireEvent.click(screen.getByRole("tab", { name: "Results" }));
  controller.enqueue(new TextEncoder().encode(JSON.stringify({ event: "done", result: { projections: [RESULT] } }) + "\n")); controller.close();
  await waitFor(() => expect(screen.queryAllByText("Reading supporting files")).toHaveLength(0));
  expect(await screen.findByRole("button", { name: /Sintering project.*Process data/ })).toBeTruthy();
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

it("keeps graph relation evidence and original file provenance visible in a project result", async () => {
  const graph = { ...RESULT, kind: "graph", data: { entities: DRAFT.entities, relations: DRAFT.relations } };
  open(async (name, args) => name === "projections" ? args.operation === "get" ? { projection: graph } : { projections: [graph] } : materialData(name, args));
  await openResult();
  expect(screen.getByLabelText("Knowledge graph map")).toBeTruthy();
  fireEvent.click(screen.getByText("Result JSON"));
  expect(screen.getByText(/Si3N4 powder is densified by pressureless sintering/)).toBeTruthy();
  fireEvent.click(screen.getByText("Source files"));
  fireEvent.click(screen.getByRole("button", { name: "sintering.md" }));
  expect(await screen.findByRole("heading", { name: "Sintering of Si3N4" })).toBeTruthy();
});

const BUNDLE = { id: "bundle-1", group_id: "collection-1", schema_id: "schema-2", name: "Batch 7", notes: "",
  status: "ready", run_id: null, created_at: "2026-09-23T00:00:00Z", updated_at: "2026-09-23T00:00:00Z",
  files: [{ source_id: "source-9", filename: "batch7.csv", media_type: "text/csv", size: 128,
    state: "converted", job_id: "job-9", error: null }], records: 0 };
const RECORD = { id: "record-x", group_id: "collection-1", schema_id: "schema-2", bundle_id: "bundle-1", name: "A1",
  data: { sample_id: "A1", conductivity: 1.2 }, validation: { valid: true }, note: null, status: "draft",
  revision: 1, created_by: "agent:lab", created_at: "2026-09-23T00:00:00Z", updated_at: "2026-09-23T00:00:00Z" };
const EXPERIMENT_DEFINITION = { type: "object", required: ["sample_id"],
  properties: { sample_id: { type: "string" }, conductivity: { type: "number", description: "S/cm" } } };

it.each(["experiment", "research"])("extracts records through a %s agent, then publishes on confirmation", async access => {
  // Both writing roles can extract; the read-only Librarian cannot.
  useWorldStore.setState({
    cards: [{ id: "card-1", type: "knowledge.base", name: "Knowledge" }, { id: "lab", type: "agent", name: "Lab assistant" },
      { id: "librarian", type: "agent", name: "Librarian" }] as unknown as WorldCard[],
    edges: [{ id: "e1", source: "lab", target: "card-1", relationship: `knowledge.base.${access}` },
      { id: "e2", source: "librarian", target: "card-1", relationship: "knowledge.base.read" }] as unknown as WorldEdge[],
    catalog: TEST_CATALOG,
  });
  let extracted = false;
  let published = false;
  const runAgent = vi.fn(async () => { extracted = true; });
  const getAgentInfo = vi.fn(async () => ({ session_id: "s", status: extracted ? "idle" : "running", active_run_id: null }));
  const action = open(async (name, args, confirm) => {
    if (name === "schemas") return args.operation === "get"
      ? { schema: { ...EXPERIMENT_SCHEMA, definition: EXPERIMENT_DEFINITION, system_prompt: "x", field_descriptions: null } }
      : { schemas: [EXPERIMENT_SCHEMA] };
    if (name === "experiment_bundles" && args.operation === "get") return { bundle: { ...BUNDLE, status: extracted ? "extracted" : "ready", records: extracted ? 1 : 0 } };
    if (name === "experiment_bundles") return { bundles: [{ ...BUNDLE, status: extracted ? "extracted" : "ready", records: extracted ? 1 : 0 }] };
    if (name === "experiments" && args.operation === "get") return { experiment: { ...RECORD, status: published ? "published" : "draft", sources: [{ source_id: "source-9", filename: "batch7.csv" }] } };
    if (name === "experiments") return { experiments: extracted ? [{ ...RECORD, status: published ? "published" : "draft" }] : [] };
    if (name === "experiment_bundle_manage") return { bundle: BUNDLE };
    if (name === "experiment_review") {
      if (!confirm) return { status: "confirmation_required", reasons: ["Publishing marks this record as approved data."] };
      published = true;
      return { experiment: { ...RECORD, status: "published", revision: 2, sources: [] } };
    }
    return undefined;
  }, { host: { runAgent, getAgentInfo } });

  fireEvent.click(await screen.findByRole("button", { name: "Experiment" }));
  const region = within(await screen.findByRole("region", { name: "Experiments" }));
  fireEvent.click(await region.findByRole("tab", { name: /Batch 7/ }));
  fireEvent.click(await region.findByRole("button", { name: "Extract records" }));
  fireEvent.click(within(await screen.findByRole("dialog", { name: "Extract records from this upload" }))
    .getByRole("button", { name: "Extract records" }));

  await waitFor(() => expect(runAgent).toHaveBeenCalledTimes(1));
  const [agentId, prompt] = runAgent.mock.calls[0] as unknown as [string, string];
  expect(agentId).toBe("lab");
  expect(prompt).toContain("EXPERIMENT EXTRACTION REQUEST");
  expect(JSON.parse(prompt.split("\n")[2])).toMatchObject({ bundle_id: "bundle-1", schema_id: "schema-2", replace_drafts: false });
  expect(action).toHaveBeenCalledWith("experiment_bundle_manage",
    expect.objectContaining({ operation: "update", bundle_id: "bundle-1", status: "extracting" }), undefined);
  // The card never saves records itself: that is the agent's tool call.
  expect(action.mock.calls.some(([name]) => name === "experiment_save")).toBe(false);

  // The agent goes idle; open its record, then enter the edit form explicitly.
  const record = await region.findByRole("button", { name: /A1/ }, { timeout: 8000 });
  fireEvent.click(record);
  fireEvent.click(await region.findByRole("button", { name: "Edit" }));
  const field = await region.findByLabelText(/conductivity/) as HTMLInputElement;
  expect(field.value).toBe("1.2");
  fireEvent.click(region.getByRole("button", { name: "Cancel" }));

  fireEvent.click(region.getByRole("button", { name: "Publish" }));
  expect(await region.findByText("Publish this record?")).toBeTruthy();
  expect(action.mock.calls.filter(([name, , confirm]) => name === "experiment_review" && confirm)).toHaveLength(0);
  fireEvent.click(region.getByRole("button", { name: "Confirm and publish" }));
  await waitFor(() => expect(action).toHaveBeenCalledWith("experiment_review",
    expect.objectContaining({ operation: "publish", record_id: "record-x" }), true));
  expect(await region.findByText(/Record "A1" published/)).toBeTruthy();
});

it("shows completed graph projections immediately without requiring review or publication", async () => {
  const graph = { ...RESULT, kind: "graph", data: { entities: DRAFT.entities, relations: DRAFT.relations } };
  const action = open(async (name, args) => name === "projections" ? args.operation === "get" ? { projection: graph } : { projections: [graph] } : materialData(name, args));
  await openResult();
  expect(screen.getByLabelText("Knowledge graph map")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Review result" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Publish" })).toBeNull();
  expect(action.mock.calls.some(([name]) => ["projection_revise", "knowledge_review"].includes(name))).toBe(false);
  fireEvent.click(screen.getByLabelText("Show history"));
  await waitFor(() => expect(action).toHaveBeenCalledWith("projections", expect.objectContaining({ include_history: true })));
});

it("drafts insights over published records and publishes one into the experiment layer on confirmation", async () => {
  const record = { id: "record-1", group_id: "collection-1", schema_id: "schema-2", bundle_id: "bundle-1", name: "B7",
    data: { sample_id: "B7", moisture: 9 }, validation: { valid: true }, note: null, status: "published",
    revision: 2, created_by: "user:desktop", created_at: "2026-10-21T00:00:00Z", updated_at: "2026-10-21T00:00:00Z" };
  const insight = { id: "insight-1", group_id: null, schema_id: "schema-2", schema_ids: ["schema-2"], kind: "anomaly", title: "B7 is wet",
    statement: "B7 moisture is 3.2σ above the batch mean.", fields: ["moisture"], confidence: 0.9, properties: { z: 3.2 },
    status: "draft", revision: 1, note: null, run_id: null, created_by: "user:desktop",
    created_at: "2026-10-21T00:00:00Z", updated_at: "2026-10-21T00:00:00Z",
    evidence: [{ record_id: "record-1", role: "observed_in", name: "B7", stale: false }] };
  let published = false;
  const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({ insights: [insight], warnings: [], truncated: false }) }));
  vi.stubGlobal("fetch", fetchMock);
  const action = open(async (name, args, confirm) => {
    if (name === "schemas" && args.operation === "list") return { schemas: [SCHEMA, EXPERIMENT_SCHEMA] };
    if (name === "schemas" && args.operation === "get") return { schema: { ...EXPERIMENT_SCHEMA, definition: { type: "object", properties: {} }, system_prompt: "x", field_descriptions: null } };
    if (name === "experiments") return { experiments: [record] };
    if (name === "insights" && args.operation === "stats") return { schema_ids: ["schema-2"], cross_schema: null,
      statistics_by_schema: [{ schema_id: "schema-2", schema_name: "Conductivity run", record_count: 1, statistics: { records: 1, truncated: false, correlations: [],
        fields: [{ field: "moisture", kind: "numeric", count: 1, mean: 9, std: 0, min: 9, max: 9, median: 9, outliers: [] }] } }] };
    if (name === "insights") return { insights: [{ ...insight, status: published ? "published" : "draft" }] };
    if (name === "insight_review" && args.operation === "publish") {
      if (!confirm) return { status: "confirmation_required", reasons: ["Publishing writes this insight into the knowledge graph."] };
      published = true;
      return { insights: [{ ...insight, status: "published" }], graph: { entities: 3, relations: 2 } };
    }
    if (name === "graph" && args.layer === "experiment" && published) return { truncated: false, relations: [],
      entities: [{ id: "node-1", type: "anomaly", name: "B7 is wet", properties: { insight_id: "insight-1" } }] };
    return undefined;
  }, { host: { deployment: undefined } });

  fireEvent.click(await screen.findByRole("button", { name: "Experiment" }));
  fireEvent.click(await screen.findByRole("tab", { name: "Insights" }));
  // This tab reads the experiment layer only; the Literature map reads its own.
  await waitFor(() => expect(action).toHaveBeenCalledWith("graph", expect.objectContaining({ layer: "experiment" }), undefined));
  expect(action.mock.calls.some(([name, args]) => name === "graph" && args.layer === "literature")).toBe(false);
  const insightsSection = within(await screen.findByRole("region", { name: "Insights" }));
  expect(await insightsSection.findByText("B7 moisture is 3.2σ above the batch mean.")).toBeTruthy();
  expect(insightsSection.getByText("Nothing published yet")).toBeTruthy();

  // The open scope reads every schema; statistics are asked for it as well.
  await waitFor(() => expect(action).toHaveBeenCalledWith("insights", expect.objectContaining({ operation: "stats" }), undefined));
  expect(await insightsSection.findByText(/1 published record\(s\) across every schema/)).toBeTruthy();
  expect(insightsSection.getByText("Mean")).toBeTruthy();
  // Narrowing to one schema narrows the statistics and the extraction scope; the
  // extraction goes through the host bridge when no agent is connected.
  fireEvent.change(insightsSection.getByLabelText("Insight schema"), { target: { value: "schema-2" } });
  await waitFor(() => expect(action).toHaveBeenCalledWith("insights", expect.objectContaining({ operation: "stats", schema_id: "schema-2" }), undefined));
  expect(await insightsSection.findByText(/1 published record\(s\) of "Conductivity run"/)).toBeTruthy();
  fireEvent.click(insightsSection.getByRole("button", { name: "Extract insights" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/knowledge/card-1/insights", expect.objectContaining({ method: "POST" })));
  expect(JSON.parse(String((fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1].body))).toEqual(
    expect.objectContaining({ schema_ids: ["schema-2"], replace_drafts: false, all_groups: true }));

  fireEvent.click(insightsSection.getByRole("button", { name: "Publish" }));
  expect(await insightsSection.findByText(/Publishing writes this insight/)).toBeTruthy();
  fireEvent.click(insightsSection.getByRole("button", { name: "Confirm and publish" }));
  await waitFor(() => expect(action).toHaveBeenCalledWith("insight_review",
    expect.objectContaining({ operation: "publish", insight_ids: ["insight-1"] }), true));
  expect(await insightsSection.findByRole("button", { name: /B7 is wet/ })).toBeTruthy();
  expect(insightsSection.queryByRole("button", { name: "Publish" })).toBeNull();
});

it("surfaces the failed file's conversion error in its project", async () => {
  const project = { ...PROJECT, state: "failed", converted_count: 0, files: [{ ...PROJECT_FILE,
    filename: "scan.bin", relative_path: "raw/scan.bin", state: "failed", record_id: null, error: "convert: No markdown engine handles scan.bin" }] };
  open(async (name, args) => name === "projects" ? args.project_id ? { project } : { projects: [project] } : materialData(name, args));
  fireEvent.click(await screen.findByRole("button", { name: /Sintering project/ }));
  expect((await screen.findByRole("alert")).textContent).toContain("convert: No markdown engine handles scan.bin");
  expect(screen.getByRole("button", { name: "raw/scan.bin" }).hasAttribute("disabled")).toBe(true);
});

it("projects several selected projects with one Space and preserves each project's identity", async () => {
  const other = { ...PROJECT, id: "project-2", name: "Other report" };
  const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => ({ ok: true, json: async () => ({ projections: [] }) }));
  vi.stubGlobal("fetch", fetchMock);
  const action = open(async (name, args) => name === "projects" ? { projects: [PROJECT, other] } : materialData(name, args));
  await chooseProject(); fireEvent.click(screen.getByLabelText("Select Other report"));
  fireEvent.click(screen.getByRole("button", { name: "Run Space" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toMatchObject({ project_ids: ["project-1", "project-2"], space_id: SPACE.id });
  expect(action.mock.calls.some(([name]) => ["project_manage", "projection_save"].includes(name))).toBe(false);
});

it("lets an engineer pick a connected agent's model from the Knowledge card's Settings", async () => {
  const updateCard = vi.fn();
  useWorldStore.setState({
    edges: [{ id: "edge-1", source: "agent-1", target: "card-1",
      relationship: "knowledge.base.read", direction: "forward" } as WorldEdge],
    cards: [{ id: "agent-1", type: "agent", name: "Librarian", position: { x: 0, y: 0 },
      size: { width: 240, height: 180 }, expanded: false, status: "available",
      config: { model: "oaw:model:model-1" } } as WorldCard],
    updateCard,
  });
  open(async () => undefined);
  fireEvent.click(await screen.findByRole("button", { name: "Settings" }));
  const region = within(await screen.findByRole("region", { name: "Settings" }));

  expect(await region.findByText("Librarian")).toBeTruthy();
  fireEvent.change(region.getByLabelText("Librarian’s model"), { target: { value: "oaw:default" } });
  expect(updateCard).toHaveBeenCalledWith("agent-1", { config: { model: "oaw:default" } });
});

it("extracts records through the host bridge when no Lab assistant is connected", async () => {
  const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({
    records: [RECORD, { ...RECORD, id: "record-y", name: "A2" }], invalid: 0, bundle_id: "bundle-1", truncated: false }) }));
  vi.stubGlobal("fetch", fetchMock);
  const runAgent = vi.fn();
  const action = open(async (name, args) => {
    if (name === "schemas") return args.operation === "get"
      ? { schema: { ...EXPERIMENT_SCHEMA, definition: EXPERIMENT_DEFINITION, system_prompt: "x", field_descriptions: null } }
      : { schemas: [EXPERIMENT_SCHEMA] };
    if (name === "experiment_bundles") return { bundles: [BUNDLE] };
    if (name === "experiments") return { experiments: [] };
    return undefined;
  }, { host: { runAgent } });

  fireEvent.click(await screen.findByRole("button", { name: "Experiment" }));
  const region = within(await screen.findByRole("region", { name: "Experiments" }));
  fireEvent.click(await region.findByRole("tab", { name: /Batch 7/ }));
  fireEvent.click(await region.findByRole("button", { name: "Extract records" }));
  fireEvent.click(within(await screen.findByRole("dialog", { name: "Extract records from this upload" }))
    .getByRole("button", { name: "Extract records" }));

  await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
  expect(url).toBe("/api/knowledge/card-1/extract");
  expect(JSON.parse(String(init.body))).toEqual({ bundle_id: "bundle-1", model: "oaw:model:model-1", replace_drafts: false });
  // No agent run, and the card never saves records itself: the bridge hands them to the plugin.
  expect(runAgent).not.toHaveBeenCalled();
  expect(action.mock.calls.some(([name]) => name === "experiment_save")).toBe(false);
  expect(await region.findByText(/Extracted 2 record\(s\) from "Batch 7"/)).toBeTruthy();
});

it("summarises a failed extraction under the upload and opens its full run log in a window", async () => {
  const run = { run_id: "run-77", agent_id: "lab", status: "failed", created_at: "2026-10-21T06:12:27Z",
    finished_at: "2026-10-21T06:13:11Z", error: "Expecting property name enclosed in double quotes",
    lifecycle: { model_requests: [{ model_request: 1, finish_reason: "STOP", elapsed_ms: 4441 }, { model_request: 2, error_type: "JSONDecodeError" }] } };
  const activity = { items: [
    { id: "a1", type: "agent_progress", timestamp: "2026-10-21T06:12:27Z", text: "agent · model request 1 started" },
    { id: "a2", type: "tool_completed", timestamp: "2026-10-21T06:12:31Z", name: "knowledge_experiment_extract_prompt", success: true },
    { id: "a3", type: "agent_progress", timestamp: "2026-10-21T06:13:11Z", text: "agent · model request 2, attempt 1 interrupted (JSONDecodeError)" }] };
  vi.stubGlobal("fetch", vi.fn(async (url: string) => ({ ok: true, json: async () =>
    url.endsWith("/runs/run-77") ? run : url.includes("/runs/run-77/activity") ? activity : { configured: false, source: null } })));
  open(async (name, args) => {
    if (name === "schemas") return args.operation === "get"
      ? { schema: { ...EXPERIMENT_SCHEMA, definition: EXPERIMENT_DEFINITION, system_prompt: "x", field_descriptions: null } }
      : { schemas: [EXPERIMENT_SCHEMA] };
    if (name === "experiment_bundles") return { bundles: [{ ...BUNDLE, status: "failed", run_id: "run-77" }] };
    if (name === "experiments") return { experiments: [] };
    return undefined;
  });

  fireEvent.click(await screen.findByRole("button", { name: "Experiment" }));
  const region = within(await screen.findByRole("region", { name: "Experiments" }));
  fireEvent.click(await region.findByRole("tab", { name: /Batch 7/ }));

  // The summary is inline; the timeline waits behind "View log".
  const summary = within(await region.findByRole("alert"));
  expect(summary.getByText("Expecting property name enclosed in double quotes")).toBeTruthy();
  expect(summary.getByText(/interrupted mid-stream \(#2 JSONDecodeError\)/)).toBeTruthy();
  expect(region.queryByText("knowledge_experiment_extract_prompt")).toBeNull();

  fireEvent.click(summary.getByRole("button", { name: "View log" }));
  const dialog = within(await screen.findByRole("dialog", { name: /Extraction log · Batch 7/ }));
  expect(await dialog.findByText("knowledge_experiment_extract_prompt")).toBeTruthy();
  expect(dialog.getByText(/model request 2, attempt 1 interrupted/)).toBeTruthy();
  expect(dialog.getByText(/STOP · 4\.4s/)).toBeTruthy();
  // A finished run offers no Stop.
  expect(dialog.queryByRole("button", { name: /Stop/ })).toBeNull();
  fireEvent.click(dialog.getByRole("button", { name: "Close" }));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: /Extraction log/ })).toBeNull());
});

it("lets the person stop a running extraction and watch its log live", async () => {
  useWorldStore.setState({
    cards: [{ id: "card-1", type: "knowledge.base", name: "Knowledge" }, { id: "lab", type: "agent", name: "Lab assistant" }] as unknown as WorldCard[],
    edges: [{ id: "e1", source: "lab", target: "card-1", relationship: "knowledge.base.experiment" }] as unknown as WorldEdge[],
    catalog: TEST_CATALOG,
  });
  const stopped: string[] = [];
  const run = { run_id: "run-88", agent_id: "lab", status: "running", created_at: "2099-01-01T00:00:00Z", finished_at: null, error: null, lifecycle: { model_requests: [] } };
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "POST" && url.endsWith("/agents/lab/stop")) { stopped.push(url); return { ok: true, json: async () => ({}) }; }
    return { ok: true, json: async () => url.includes("runs?agent_id=lab") ? [run] : url.endsWith("/runs/run-88") ? run
      : url.includes("/runs/run-88/activity") ? { items: [{ id: "p1", type: "agent_progress", timestamp: "2099-01-01T00:00:01Z", text: "agent · model request 1 started" }] }
      : { configured: false, source: null } };
  }));
  const runAgent = vi.fn(async () => undefined);
  // The agent stays "running" for the whole test, so the completion poller never fires.
  const getAgentInfo = vi.fn(async () => ({ session_id: "s", status: "running", active_run_id: "run-88" }));
  let bundleStatus = "ready", bundleRun: string | null = null;
  open(async (name, args) => {
    if (name === "schemas") return args.operation === "get"
      ? { schema: { ...EXPERIMENT_SCHEMA, definition: EXPERIMENT_DEFINITION, system_prompt: "x", field_descriptions: null } }
      : { schemas: [EXPERIMENT_SCHEMA] };
    if (name === "experiment_bundles") return { bundles: [{ ...BUNDLE, status: bundleStatus, run_id: bundleRun }] };
    if (name === "experiments") return { experiments: [] };
    if (name === "experiment_bundle_manage") { bundleStatus = String(args.status ?? bundleStatus); if (args.run_id) bundleRun = String(args.run_id); return { bundle: BUNDLE }; }
    return undefined;
  }, { host: { runAgent, getAgentInfo } });

  fireEvent.click(await screen.findByRole("button", { name: "Experiment" }));
  const region = within(await screen.findByRole("region", { name: "Experiments" }));
  fireEvent.click(await region.findByRole("tab", { name: /Batch 7/ }));
  fireEvent.click(await region.findByRole("button", { name: "Extract records" }));
  fireEvent.click(within(await screen.findByRole("dialog", { name: "Extract records from this upload" }))
    .getByRole("button", { name: "Extract records" }));
  await waitFor(() => expect(runAgent).toHaveBeenCalled());

  // Stop and View log appear while the run is underway; the window is live.
  fireEvent.click(await region.findByRole("button", { name: "View log" }));
  const dialog = within(await screen.findByRole("dialog", { name: /Extraction log/ }));
  expect(await dialog.findByText(/model request 1 started/)).toBeTruthy();
  expect(dialog.getByText(/live/)).toBeTruthy();
  fireEvent.click(dialog.getByRole("button", { name: "Stop extraction" }));
  await waitFor(() => expect(stopped).toEqual([expect.stringMatching(/\/agents\/lab\/stop$/)]));
});

it("stores the vision model as a card setting, blank for the OAW default model", async () => {
  const saved: Record<string, unknown>[] = [];
  const action = open(async (name, args) => {
    if (name === "settings") { saved.push(args); return { settings: { collection_name: "Knowledge base",
      pdf_engine: "auto", mineru_base_url: "", vision_model: (args.vision_model as string) ?? "" } }; }
    return undefined;
  });
  fireEvent.click(await screen.findByRole("button", { name: "Settings" }));
  const region = within(await screen.findByRole("region", { name: "Settings" }));
  const picker = await region.findByLabelText("Vision model for images") as HTMLSelectElement;
  expect(picker.value).toBe("oaw:default");

  fireEvent.change(picker, { target: { value: "oaw:model:model-1" } });
  await waitFor(() => expect(saved).toContainEqual({ vision_model: "oaw:model:model-1" }));
  fireEvent.change(region.getByLabelText("Vision model for images"), { target: { value: "oaw:default" } });
  await waitFor(() => expect(saved).toContainEqual({ vision_model: "" }));
  // The key never passes through the card: only a model reference is stored.
  expect(action.mock.calls.every(([, args]) => !JSON.stringify(args).includes("api_key"))).toBe(true);
});

it("stores the embedding model as a card setting, off by default", async () => {
  const saved: Record<string, unknown>[] = [];
  open(async (name, args) => {
    if (name === "settings") { saved.push(args); return { settings: { collection_name: "Knowledge base",
      pdf_engine: "auto", mineru_base_url: "", vision_model: "", embedding_model: (args.embedding_model as string) ?? "" } }; }
    return undefined;
  });
  fireEvent.click(await screen.findByRole("button", { name: "Settings" }));
  const region = within(await screen.findByRole("region", { name: "Settings" }));
  const picker = await region.findByLabelText("Embedding model for graph search") as HTMLSelectElement;
  expect(picker.value).toBe("");

  fireEvent.change(picker, { target: { value: "oaw:model:model-1" } });
  await waitFor(() => expect(saved).toContainEqual({ embedding_model: "oaw:model:model-1" }));
  fireEvent.change(region.getByLabelText("Embedding model for graph search"), { target: { value: "" } });
  await waitFor(() => expect(saved).toContainEqual({ embedding_model: "" }));
});

it("shows the semantic index behind graph search and builds it on request", async () => {
  let embedded = 0;
  const action = open(async (name, args) => {
    if (name === "overview") return { ...overview(), settings: { collection_name: "Knowledge base", pdf_engine: "auto",
      mineru_base_url: "", vision_model: "", embedding_model: "oaw:model:emb" } };
    if (name === "graph_index") {
      if (args.operation === "build") embedded = 23;
      return { enabled: true, embedding_model: "oaw:model:emb", model_id: "qwen3.7-text-embedding", total: 23,
        embedded, pending: 23 - embedded, stale: 0, last_error: embedded ? null : "Embedding model request failed (HTTP 400)" };
    }
    return undefined;
  });
  fireEvent.click(await screen.findByRole("button", { name: "Settings" }));
  const panel = within(await screen.findByRole("group", { name: "Semantic index" }));
  expect(await panel.findByText("qwen3.7-text-embedding: 0 of 23 graph elements embedded · 23 pending")).toBeTruthy();
  expect(panel.getByRole("alert").textContent).toContain("HTTP 400");

  fireEvent.click(panel.getByRole("button", { name: "Build index now" }));
  expect(await panel.findByText("qwen3.7-text-embedding: 23 of 23 graph elements embedded")).toBeTruthy();
  expect(panel.queryByRole("alert")).toBeNull();
  expect(action).toHaveBeenCalledWith("graph_index", { operation: "build" }, undefined);
});

it("hides engineering settings and uses the deployment Space bridge without a client model override", async () => {
  const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => ({ ok: true, json: async () => ({ projections: [RESULT] }) }));
  vi.stubGlobal("fetch", fetchMock);
  open(async (name, args) => materialData(name, args), {
    card: { config: { default_model: "oaw:model:model-1" } },
    host: { deployment: { config_fields: ["default_model"], document_fields: [], summary_fields: [], document_actions: [], downloads: [], resource_actions: {}, execution: false } },
  });
  expect(screen.queryByRole("region", { name: "Settings" })).toBeNull();
  expect(screen.queryByLabelText("Collection name")).toBeNull();
  await chooseProject();
  expect(screen.queryByLabelText("Model")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Run Space" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  expect(fetchMock.mock.calls[0][0]).toBe("/api/runtime-app/workspace/knowledge/card-1/project-space");
  expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({ space_id: SPACE.id, project_ids: [PROJECT.id], rerun: false });
});
