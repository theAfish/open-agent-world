import { DocumentMarkdown, WorkspaceEmptyState, WorkspaceSection, WorkspaceToolbar, hostApiBase, t, useWorkspaceSections } from "@oaw/plugin-api";
import type { PluginViewProps } from "@oaw/plugin-api";
import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { ArrowRight, CheckCircle2, ChevronDown, CircleAlert, FileText, FolderOpen, FolderPlus, Layers3, LoaderCircle, Lock, Network, Pencil, Play, Plus, RefreshCw, Search, Sparkles, Square, Table2, Trash2, Upload, X } from "lucide-react";
import { GraphMap } from "./GraphMap";
import { RecordTable } from "./RecordView";
import { RunLogModal, type LogEvent } from "./RunLogModal";
import "./ProjectWorkspace.css";

export type ProjectTab = "projects" | "spaces" | "results";
export type BridgeProgress = { stage: string; message?: string; done?: number; total?: number | null } & Record<string, unknown>;
type Project = { id: string; name: string; description?: string; source_count: number; converted_count: number; state: string };
type ProjectFile = { id: string; filename: string; relative_path: string; size: number; state: string; error?: string;
  markdown?: { artifact_id: string }; record_id?: string };
type ProjectDetail = Project & { files: ProjectFile[] };
type Space = { id: string; name: string; description?: string; version: number; kind: "table" | "graph"; builtin?: boolean;
  definition: Record<string, unknown>; system_prompt: string };
type Projection = { id: string; project_id: string; project_name: string; space_id: string; space_name: string;
  space_version: number; kind: "table" | "graph"; status: "running" | "completed" | "failed";
  revision: number; generation: number; data: unknown; error?: string; updated_at: string; stale?: boolean; stale_reason?: string | null;
  sources: { source_id: string; filename: string; relative_path: string }[]; space_snapshot?: { definition: Record<string, unknown> } };
type Intake = { projectId: string; name: string; files: File[] };
type Template = { definition: Record<string, unknown>; system_prompt?: string };
type SpaceEdit = { id?: string; name: string; description: string; kind: "table" | "graph"; definition: string; system_prompt: string; readOnly?: boolean };
type AgentTask = { agentId: string; projectionIds: string[]; startedAt: string; previousRunIds: string[]; runId?: string;
  reviewOf?: { id: string; projectId: string; spaceId: string } };
type SavedView = { selected: string[]; projectId: string; spaceId: string; resultId: string; editor?: SpaceEdit; agentTask?: AgentTask; resultEdit?: string; revisionNote?: string; projectPages?: number; resultPages?: number };
type Props = { host: PluginViewProps["host"]; cardId: string; tab: ProjectTab; visible: boolean; onTab: (tab: ProjectTab) => void;
  deployed: boolean; selectedModel: string; modelSelect: ReactNode; agent?: { id: string; name: string }; version: number;
  sourceToOpen?: string; onSourceOpened: () => void; onChanged: () => Promise<void>;
  request: (path: string, body: unknown, deployed: boolean, onProgress?: (event: BridgeProgress) => void, signal?: AbortSignal) => Promise<Record<string, unknown>> };
/** Run statuses that are still in progress; any other status means the run has ended. */
const LIVE_RUN = ["created", "running", "waiting"];
/** The person stopped a model run; not a failure to report. */
class Stopped extends Error {}
const pretty = (value: unknown) => JSON.stringify(value, null, 2);
const message = (error: unknown) => error instanceof Error ? error.message : String(error);
const TABLE_SCHEMA = { type: "object", properties: { records: { type: "array", items: { type: "object", properties: {
  name: { type: "string" }, value: { type: "number" }, unit: { type: "string" } }, required: ["name"] } } }, required: ["records"] };
const statusLabel = (state: string) => ({ converted: t("Ready"), ready: t("Ready"), completed: t("Completed"),
  pending: t("Needs conversion"), converting: t("Converting"), running: t("Processing"), failed: t("Failed"), empty: t("No files") }[state] ?? state);
function Status({ state }: { state: string }) {
  return <span className={`project-status is-${state}`}>{["running", "converting"].includes(state) ? <LoaderCircle size={13} className="project-spin" />
    : state === "failed" ? <CircleAlert size={13} /> : ["completed", "ready", "converted"].includes(state) ? <CheckCircle2 size={13} /> : <FileText size={13} />}{statusLabel(state)}</span>;
}

/** Project selection is only an execution scope. Stable results belong to a project and Space. */
export function ProjectWorkspace({ host, cardId, tab, visible, onTab, deployed, selectedModel, modelSelect, agent, version,
  sourceToOpen, onSourceOpened, onChanged, request }: Props) {
  const { isDetached } = useWorkspaceSections();
  const restored = useRef(host.draft?.get()?.projectView as SavedView | undefined).current;
  const [projects, setProjects] = useState<Project[]>([]);
  const [spaces, setSpaces] = useState<Space[]>([]);
  const [results, setResults] = useState<Projection[]>([]);
  const [projectPages, setProjectPages] = useState(restored?.projectPages ?? 1);
  const [resultPages, setResultPages] = useState(restored?.resultPages ?? 1);
  const [moreProjects, setMoreProjects] = useState(false);
  const [moreResults, setMoreResults] = useState(false);
  const [selected, setSelected] = useState<string[]>(restored?.selected ?? []);
  const [projectId, setProjectId] = useState(restored?.projectId ?? "");
  const [project, setProject] = useState<ProjectDetail>();
  const [spaceId, setSpaceId] = useState(restored?.spaceId ?? "");
  const [resultId, setResultId] = useState(restored?.resultId ?? "");
  const [resultDetail, setResultDetail] = useState<Projection>();
  const [agentTask, setAgentTask] = useState<AgentTask | undefined>(restored?.agentTask);
  const [intake, setIntake] = useState<Intake>();
  const [graphTemplate, setGraphTemplate] = useState<Template>();
  const [fileChecks, setFileChecks] = useState<string[]>([]);
  const [removal, setRemoval] = useState<"files" | "project">();
  const [editor, setEditor] = useState<SpaceEdit | undefined>(restored?.editor);
  const [query, setQuery] = useState("");
  const [searchResults, setSearchResults] = useState<{ source_id: string; filename: string; heading_path?: string; excerpt: string }[]>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [drag, setDrag] = useState(false);
  const [progress, setProgress] = useState<BridgeProgress>();
  const [logOpen, setLogOpen] = useState(false);
  const [progressLog, setProgressLog] = useState<LogEvent[]>([]);
  const [stopping, setStopping] = useState(false);
  const abortRef = useRef<AbortController>();
  const [reader, setReader] = useState<{ sourceId: string; filename: string; markdown: string; has_more: boolean }>();
  const [resultEdit, setResultEdit] = useState<string | undefined>(restored?.resultEdit);
  const [revisionNote, setRevisionNote] = useState(restored?.revisionNote ?? "");
  const [postProcess, setPostProcess] = useState<string>();
  const [history, setHistory] = useState(false);
  const [graphSelection, setGraphSelection] = useState("");
  const [rename, setRename] = useState<string>();
  const mounted = useRef(true);
  const filesInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);
  const call = useCallback((action: string, args: Record<string, unknown> = {}, confirm?: boolean) => host.resourceAction(action, args, confirm), [host]);
  const readAgentRuns = useCallback(async (agentId: string) => {
    const response = await fetch(`${hostApiBase}/runs?agent_id=${encodeURIComponent(agentId)}`);
    if (!response.ok) throw new Error(t("Unable to read agent progress"));
    const runs = await response.json() as { run_id: string; status: string; created_at: string; error?: string }[];
    if (!Array.isArray(runs)) throw new Error(t("Unable to read agent progress"));
    return runs;
  }, []);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => { host.draft?.set({ ...host.draft.get(), projectView: { selected, projectId, spaceId, resultId, editor, agentTask, resultEdit, revisionNote, projectPages, resultPages } }); },
    [host.draft, selected, projectId, spaceId, resultId, editor, agentTask, resultEdit, revisionNote, projectPages, resultPages]);
  const load = useCallback(async () => {
    const [projectLists, s, resultLists] = await Promise.all([
      Promise.all(Array.from({ length: projectPages }, (_, page) => call("projects", { limit: 200, offset: page * 200 }))),
      call("spaces", { operation: "list" }),
      Promise.all(Array.from({ length: resultPages }, (_, page) => call("projections", { operation: "list", include_history: history, limit: 100, offset: page * 100 })))]);
    if (!mounted.current) return;
    setProjects([...new Map(projectLists.flatMap(p => p.projects as Project[] ?? []).map(p => [p.id, p])).values()]);
    const loaded = s.spaces as Space[] ?? [];
    setSpaces(loaded);
    setGraphTemplate((s.templates as { graph?: Template } | undefined)?.graph);
    setSpaceId(current => current || loaded.find(space => space.builtin)?.id || "");
    setResults([...new Map(resultLists.flatMap(r => r.projections as Projection[] ?? []).map(r => [r.id, r])).values()]);
    setMoreProjects(!!projectLists.at(-1)?.has_more); setMoreResults(!!resultLists.at(-1)?.has_more);
  }, [call, history, projectPages, resultPages]);
  // A host-model call the person can stop: every streamed step is kept for the log window.
  const stream = useCallback(async (path: string, body: unknown, onEvent?: (event: BridgeProgress) => void) => {
    const controller = new AbortController();
    abortRef.current = controller;
    setProgressLog([]);
    try {
      return await request(path, body, deployed, event => {
        setProgressLog(log => [...log.slice(-199), { at: new Date().toISOString(), stage: event.stage, message: event.message, done: event.done, total: event.total }]);
        onEvent?.(event);
      }, controller.signal);
    } catch (reason) {
      if (controller.signal.aborted) throw new Stopped();
      throw reason;
    } finally { if (abortRef.current === controller) abortRef.current = undefined; }
  }, [request, deployed]);
  const perform = useCallback(async (work: () => Promise<void>) => {
    setBusy(true); setError(""); setNotice("");
    try { await work(); } catch (reason) {
      if (mounted.current) { if (reason instanceof Stopped) setNotice(t("Extraction stopped")); else setError(message(reason)); }
    }
    finally { if (mounted.current) setBusy(false); }
  }, []);
  useEffect(() => { void load().catch(reason => setError(message(reason))); }, [load, version]);
  useEffect(() => {
    if (!resultId) { setResultDetail(undefined); return; }
    let active = true;
    call("projections", { operation: "get", projection_id: resultId }).then(value => { if (active) setResultDetail(value.projection as Projection); })
      .catch(reason => { if (active) setError(message(reason)); });
    return () => { active = false; };
  }, [call, resultId, results]);
  useEffect(() => {
    if (!agentTask) return;
    let active = true;
    const timer = setTimeout(async () => {
      try {
        const runs = await readAgentRuns(agentTask.agentId);
        const run = agentTask.runId ? runs.find(r => r.run_id === agentTask.runId)
          : runs.filter(r => !agentTask.previousRunIds.includes(r.run_id) && Date.parse(r.created_at) >= Date.parse(agentTask.startedAt) - 1000).sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
        if (!active) return;
        if (run && !LIVE_RUN.includes(run.status.toLowerCase())) {
          for (const id of agentTask.projectionIds) {
            const current = (await call("projections", { operation: "get", projection_id: id })).projection as Projection;
            if (current.status === "running") await call("projection_fail", { projection_id: id,
              error: run.error || t("The agent stopped before saving this result. Retry extraction.") });
          }
          if (active && agentTask.reviewOf) {
            const value = await call("projections", { operation: "list", project_id: agentTask.reviewOf.projectId, space_id: agentTask.reviewOf.spaceId, limit: 1 });
            const latest = (value.projections as Projection[])[0];
            if (latest && active) setResultId(previous => previous === agentTask.reviewOf?.id ? latest.id : previous);
            if (run.status.toLowerCase() !== "succeeded") setError(run.error || t("The agent did not finish reviewing this result"));
          }
          if (active) { setAgentTask(undefined); await load(); }
        } else if (active) { setAgentTask({ ...agentTask, ...(run ? { runId: run.run_id } : {}) }); await load(); }
      } catch (reason) { if (active) { setError(message(reason)); setAgentTask({ ...agentTask }); } }
    }, 1800);
    return () => { active = false; clearTimeout(timer); };
  }, [agentTask, call, load, readAgentRuns]);
  useEffect(() => { setFileChecks([]); setRemoval(undefined); }, [projectId]);
  useEffect(() => {
    if (!projectId) { setProject(undefined); return; }
    let active = true;
    call("projects", { project_id: projectId }).then(value => { if (active) setProject(value.project as ProjectDetail); })
      .catch(reason => { if (active) setError(message(reason)); });
    return () => { active = false; };
  }, [call, projectId, projects]);
  useEffect(() => {
    if (!projects.some(p => p.state === "converting") && !results.some(r => r.status === "running")) return;
    const timer = setTimeout(() => void load().catch(reason => setError(message(reason))), 1600);
    return () => clearTimeout(timer);
  }, [projects, results, load]);
  const openFile = useCallback(async (sourceId: string, filename: string) => {
    const value = await call("markdown", { source_id: sourceId, limit: 40000 });
    setReader({ sourceId, filename, markdown: value.markdown as string, has_more: !!value.has_more });
  }, [call]);
  useEffect(() => {
    if (!sourceToOpen) return;
    void perform(async () => { await openFile(sourceToOpen, t("Source document")); onSourceOpened(); });
  }, [sourceToOpen, openFile, onSourceOpened, perform]);
  const openIntake = (projectId = "", files: File[] = []) => { setIntake({ projectId, name: "", files: [] }); if (files.length) addFiles(files, false, projectId); };
  const addFiles = (files: File[], folder = false, projectId?: string) => setIntake(current => {
    const base = current ?? { projectId: projectId ?? "", name: "", files: [] };
    const key = (file: File) => file.webkitRelativePath || file.name;
    const merged = [...new Map([...base.files, ...files].map(file => [key(file), file])).values()];
    const first = files[0];
    const suggestion = folder ? first?.webkitRelativePath.split("/")[0] : first?.name.replace(/\.[^.]+$/, "");
    return { ...base, files: merged, name: base.name || (base.projectId ? "" : suggestion ?? "") };
  });
  const importFiles = () => intake && perform(async () => {
    const target = projects.find(p => p.id === intake.projectId);
    const name = (target?.name ?? intake.name).trim();
    if (!name || !intake.files.length) return;
    const payload = intake.files.map((file, index) => ({ filename: file.name, file_key: `file${index}`,
      relative_path: file.webkitRelativePath || file.name, media_type: file.type || "application/octet-stream" }));
    const staged = await host.resourceFileAction("project_import_preview", { files: payload, grouping: "single" },
      Object.fromEntries(intake.files.map((file, index) => [`file${index}`, file]))) as { token: string; projects: { key: string }[] };
    const value = await call("project_import_commit", { token: staged.token, grouping: "single", depth: 1,
      projects: [{ key: staged.projects[0].key, name, ...(target ? { project_id: target.id } : {}) }], auto_process: true });
    const imported = value.projects as Project[];
    setIntake(undefined); setSelected(imported.map(p => p.id)); setProjectId(imported[0]?.id ?? "");
    await load(); await onChanged();
  });
  const removeFiles = () => project && perform(async () => {
    await call("project_file_remove", { project_id: project.id, source_ids: fileChecks }, true);
    setFileChecks([]); setRemoval(undefined); setNotice(t("Files removed. Results that used them are marked out of date."));
    await load(); await onChanged();
  });
  const deleteProject = () => project && perform(async () => {
    await call("project_delete", { project_id: project.id }, true);
    setSelected(current => current.filter(id => id !== project.id)); setProjectId(""); setReader(undefined); setRemoval(undefined);
    setNotice(t("Project deleted")); await load(); await onChanged();
  });
  const convert = (ids: string[]) => perform(async () => {
    for (let offset = 0; offset < ids.length; offset += 100) await call("project_process", { project_ids: ids.slice(offset, offset + 100) });
    await load(); await onChanged();
  });
  const searchDocuments = () => perform(async () => {
    if (!query.trim()) { setSearchResults(undefined); return; }
    const value = await call("search", { query: query.trim(), ...(selected.length === 1 ? { group_id: selected[0] } : { all_groups: true }) });
    setSearchResults(value.results as typeof searchResults);
  });
  const newSpace = (kind: "table" | "graph") => setEditor({ name: "", description: "", kind,
    definition: pretty(kind === "graph" ? graphTemplate?.definition ?? {} : TABLE_SCHEMA),
    system_prompt: kind === "graph" ? graphTemplate?.system_prompt ?? ""
      : "Extract structured records from all project documents according to the schema. Preserve reported values and units. Do not invent missing values." });
  const saveSpace = () => editor && !editor.readOnly && perform(async () => {
    const value = await call("space_manage", { operation: editor.id ? "update" : "create", ...(editor.id ? { space_id: editor.id } : {}),
      name: editor.name, description: editor.description, kind: editor.kind, definition: JSON.parse(editor.definition), system_prompt: editor.system_prompt });
    setSpaceId((value.space as Space).id); setEditor(undefined); await load();
  });
  const currentResult = resultDetail?.id === resultId ? resultDetail : undefined;
  const canRunAgent = !deployed && !!agent && typeof host.runAgent === "function";
  const canRun = !!selectedModel || canRunAgent;
  const runBusy = busy || !!agentTask;
  const execute = (ids = selected, rerun = false, targetSpace = spaceId) => perform(async () => {
    if (!targetSpace || !ids.length) return;
    setSpaceId(targetSpace);
    onTab("results");
    if (canRunAgent && agent) {
      const previousRunIds = (await readAgentRuns(agent.id)).map(run => run.run_id);
      const created: Projection[] = [];
      try {
        for (let offset = 0; offset < ids.length; offset += 100) {
          const prepared = await call("projection_prepare", { project_ids: ids.slice(offset, offset + 100), space_id: targetSpace, rerun });
          created.push(...(prepared.projections as (Projection & { disposition: string })[]).filter(r => r.disposition === "created"));
        }
        if (created.length) {
          const task = { agentId: agent.id, projectionIds: created.map(r => r.id), startedAt: new Date().toISOString(), previousRunIds };
          await host.runAgent!(agent.id, `Use the connected knowledge base ${cardId}. Extract the prepared projections ${JSON.stringify(task.projectionIds)}. For each, read every original Markdown window via knowledge_projection_prompt and save schema-valid data with knowledge_projection_save. These runs are already prepared; do not create another run. If extraction fails, call knowledge_projection_fail. Do not run review unless asked.`, true);
          setAgentTask(task); setNotice(t("The agent is processing the selected projects"));
        } else setNotice(t("Existing results and running tasks are reused"));
      } catch (reason) {
        for (const projection of created) await call("projection_fail", { projection_id: projection.id, error: message(reason) });
        await load(); throw reason;
      }
    } else {
      setProgress({ stage: "projection", done: 0, total: ids.length });
      try {
        for (let offset = 0; offset < ids.length; offset += 100) {
          await stream(`knowledge/${cardId}/project-space`, { space_id: targetSpace, project_ids: ids.slice(offset, offset + 100), rerun,
            ...(deployed ? {} : { model: selectedModel }) },
          event => setProgress({ ...event, done: offset + (event.done ?? 0), total: ids.length }));
        }
      }
      finally { setProgress(undefined); await load().catch(() => undefined); }
      return;
    }
    await load();
  });
  const review = (instruction?: string) => currentResult && perform(async () => {
    if (canRunAgent && agent) {
      const previousRunIds = (await readAgentRuns(agent.id)).map(run => run.run_id);
      const startedAt = new Date().toISOString();
      await host.runAgent!(agent.id, `Review projection ${currentResult.id} in knowledge base ${cardId} against its original documents using knowledge_projection_review_prompt. Read all Markdown windows. Apply supported corrections through knowledge_projection_revise with expected_revision and a concise note. Preserve the schema and evidence. ${instruction || "This is an optional review of an existing result."}`, true);
      setAgentTask({ agentId: agent.id, projectionIds: [], startedAt, previousRunIds,
        reviewOf: { id: currentResult.id, projectId: currentResult.project_id, spaceId: currentResult.space_id } });
      setNotice(t("The agent is reviewing this result"));
    } else {
      setProgress({ stage: "review" });
      try { const value = await stream(`knowledge/${cardId}/review-projection`, { projection_id: currentResult.id,
        ...(instruction ? { instruction } : {}), ...(deployed ? {} : { model: selectedModel }) }, setProgress);
        if ((value.projection as Projection | undefined)?.id) setResultId((value.projection as Projection).id); }
      finally { setProgress(undefined); }
    }
    setPostProcess(undefined); await load();
  });
  const revise = () => currentResult && resultEdit !== undefined && perform(async () => {
    const value = await call("projection_revise", { projection_id: currentResult.id, data: JSON.parse(resultEdit),
      note: revisionNote, expected_revision: currentResult.revision });
    setResultId((value.projection as Projection).id); setResultEdit(undefined); setRevisionNote(""); await load();
  });
  // One Stop for every kind of extraction. It does not go through perform(): a busy
  // card must still be stoppable. A cancelled run leaves its projection "running", so
  // Stop also fails those, which is what makes "Restart extraction" work at once.
  const running = results.filter(r => r.status === "running");
  const stop = useCallback(async () => {
    setStopping(true); setError("");
    try {
      abortRef.current?.abort();
      if (agent && !deployed) {
        for (const run of await readAgentRuns(agent.id)) {
          if (!LIVE_RUN.includes(run.status.toLowerCase())) continue;
          const response = await fetch(`${hostApiBase}/runs/${encodeURIComponent(run.run_id)}/cancel`, { method: "POST" });
          if (!response.ok) throw new Error(t("The run could not be stopped"));
        }
      }
      const ids = new Map(running.map(r => [r.id, r.revision]));
      for (const id of agentTask?.projectionIds ?? []) if (!ids.has(id)) ids.set(id, 1);
      for (const [id, revision] of ids) {
        await call("projection_fail", { projection_id: id, expected_revision: revision, error: t("Stopped by the user. Retry extraction.") }).catch(() => undefined);
      }
      setAgentTask(undefined);
      setNotice(t("Extraction stopped"));
      await load();
    } catch (reason) { if (mounted.current) setError(message(reason)); }
    finally { if (mounted.current) setStopping(false); }
  }, [agent, deployed, running, agentTask, call, load, readAgentRuns]);
  const active = !!agentTask || !!progress || running.length > 0;
  const canStop = active && !stopping;
  const logAvailable = !!agentTask || (!deployed && !!agent) || progressLog.length > 0;
  const selectedProjects = projects.filter(p => selected.includes(p.id));
  const ready = selectedProjects.length === selected.length && selectedProjects.length > 0 && selectedProjects.every(p => p.source_count > 0 && p.converted_count === p.source_count);
  const visibleProjects = projects.filter(p => `${p.name} ${p.description ?? ""}`.toLowerCase().includes(query.toLowerCase()));
  const visibleResults = results.filter(r => (!spaceId || r.space_id === spaceId) && (!selected.length || selected.includes(r.project_id)));
  const toggle = (id: string) => setSelected(previous => previous.includes(id) ? previous.filter(value => value !== id) : [...previous, id]);
  const sectionVisible = (id: ProjectTab) => visible && tab === id || isDetached(id);
  const runControls = <>
    {logAvailable && <button type="button" onClick={() => setLogOpen(true)}>{t("View log")}</button>}
    {active && <button type="button" className="project-stop" disabled={stopping} onClick={() => void stop()}><Square size={13} />{stopping ? t("Stopping…") : t("Stop")}</button>}</>;
  const banner = <>{error && <p role="alert" className="knowledge-error">{error}</p>}{notice && <p role="status" className="knowledge-notice">{notice}</p>}
    {progress && <div className="project-progress" role="status"><LoaderCircle className="project-spin" size={17} /><span>{progress.message || (progress.stage === "review" ? t("Reviewing result…") : t("Extracting project data…"))}</span>
      {progress.total ? <progress value={progress.done ?? 0} max={progress.total} /> : <progress />}{runControls}</div>}
    {!progress && agentTask && <div className="project-progress" role="status"><LoaderCircle className="project-spin" size={17} /><span>{t("The agent is extracting. Open the log to follow it.")}</span>{runControls}</div>}</>;
  const refreshButton = <button type="button" className="knowledge-icon-button" aria-label={t("Refresh projects")} disabled={busy}
    onClick={() => void perform(load)}><RefreshCw size={16} /></button>;
  const spacePicker = <label className="project-space-picker"><Layers3 size={16} /><span className="knowledge-visually-hidden">{t("Extraction Space")}</span>
    <select aria-label={t("Extraction Space")} value={spaceId} onChange={event => { setSpaceId(event.target.value); setResultId(""); }} disabled={busy}>
      <option value="">{t("Choose a Space")}</option>{spaces.map(s => <option key={s.id} value={s.id}>{s.name} · v{s.version}</option>)}</select></label>;
  const documentReader = reader && <div className="project-reader"><div className="project-detail-heading"><h3>{reader.filename}</h3><button type="button" className="knowledge-icon-button" aria-label={t("Close document")} onClick={() => setReader(undefined)}><X size={16} /></button></div><div className="knowledge-prose"><DocumentMarkdown content={reader.markdown} /></div>
    {reader.has_more && <button type="button" disabled={busy} onClick={() => void perform(async () => { const next = await call("markdown", { source_id: reader.sourceId, offset: reader.markdown.length, limit: 40000 }); setReader({ ...reader, markdown: reader.markdown + String(next.markdown), has_more: !!next.has_more }); })}>{t("Read more")}</button>}</div>;

  return <>
    <WorkspaceSection id="projects" title={t("Projects")} className="knowledge-section knowledge-wide" style={sectionVisible("projects") ? undefined : { display: "none" }}
      layout={{ min_width: 320, min_height: 320, ideal_width: 960, ideal_height: 640 }}>
      <div className="knowledge-section project-workspace" data-detached={isDetached("projects") || undefined}>
        <WorkspaceToolbar label={t("Projects")} className="knowledge-section-toolbar project-toolbar"><div className="project-toolbar-title"><strong>{t("Projects")}</strong><span className="project-count">{projects.length}</span></div>
          {!!projects.length && !intake && <form className="project-filter" onSubmit={event => { event.preventDefault(); void searchDocuments(); }}><div className="project-search-field"><Search size={16} /><input aria-label={t("Find projects")} placeholder={t("Find projects or search document contents")} value={query} onChange={event => { setQuery(event.target.value); setSearchResults(undefined); }} /></div>{!!query.trim() && <button type="submit" disabled={busy}>{t("Search contents")}</button>}</form>}
          <div className="knowledge-heading-actions">{refreshButton}
          <button type="button" className="knowledge-primary" disabled={busy || !!intake} onClick={() => openIntake()}><FolderPlus size={16} />{t("New project")}</button></div></WorkspaceToolbar>
        {banner}
        <input ref={filesInput} className="knowledge-visually-hidden" type="file" multiple aria-label={t("Project files or archives")} onChange={event => { addFiles(Array.from(event.target.files ?? [])); event.target.value = ""; }} />
        <input ref={folderInput} className="knowledge-visually-hidden" type="file" multiple {...{ webkitdirectory: "" }} aria-label={t("Project folder")} onChange={event => { addFiles(Array.from(event.target.files ?? []), true); event.target.value = ""; }} />
        {intake ? <div className={`project-import-preview project-intake${drag ? " is-over" : ""}`} onDragOver={event => { event.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)} onDrop={event => { event.preventDefault(); setDrag(false); if (!busy) addFiles(Array.from(event.dataTransfer.files)); }}>
          <div className="project-detail-heading"><div><span className="project-eyebrow">{t("Add materials")}</span><h3>{intake.projectId ? t("Add files to a project") : t("New project")}</h3></div>
            <button type="button" className="knowledge-icon-button" aria-label={t("Cancel import")} disabled={busy} onClick={() => setIntake(undefined)}><X size={18} /></button></div>
          <div className="project-import-controls"><label>{t("Project")}<select aria-label={t("Project")} value={intake.projectId} disabled={busy} onChange={event => setIntake({ ...intake, projectId: event.target.value })}>
            <option value="">{t("New project")}</option>{projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
            {!intake.projectId && <label>{t("Project name")}<input aria-label={t("Project name")} value={intake.name} disabled={busy} onChange={event => setIntake({ ...intake, name: event.target.value })} /></label>}</div>
          <div className="project-action-row"><button type="button" disabled={busy} onClick={() => filesInput.current?.click()}><Upload size={16} />{t("Add files or archives")}</button>
            <button type="button" disabled={busy} onClick={() => folderInput.current?.click()}><FolderOpen size={16} />{t("Add a folder")}</button></div>
          <p className="knowledge-meta">{intake.projectId ? t("A file with the same path replaces the project's current version.") : t("Drop files here too. Archives are unpacked into the project.")}</p>
          {!!intake.files.length && <ul className="project-intake-files">{intake.files.map(file => <li key={file.webkitRelativePath || file.name}><FileText size={14} /><span>{file.webkitRelativePath || file.name}</span>
            <button type="button" className="knowledge-icon-button" aria-label={t("Remove {v0}", { v0: file.name })} disabled={busy} onClick={() => setIntake({ ...intake, files: intake.files.filter(item => item !== file) })}><X size={13} /></button></li>)}</ul>}
          <div className="project-action-row"><button type="button" disabled={busy} onClick={() => setIntake(undefined)}>{t("Cancel")}</button><button type="button" className="knowledge-primary" disabled={busy || !intake.files.length || (!intake.projectId && !intake.name.trim())} onClick={() => void importFiles()}>
            {busy ? <LoaderCircle className="project-spin" size={16} /> : <ArrowRight size={16} />}{t("Import and convert")}</button></div>
        </div> : <>
          {!projects.length ? <div className={`project-dropzone${drag ? " is-over" : ""}`} onDragOver={event => { event.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)} onDrop={event => { event.preventDefault(); setDrag(false); if (!busy) openIntake("", Array.from(event.dataTransfer.files)); }}>
            <div className="project-file-stack" aria-hidden="true"><FileText /><FolderOpen /><Table2 /></div><h3>{t("Bring your materials together")}</h3><p>{t("Reports, documents, datasets and supporting files")}</p>
            <div className="project-action-row"><button type="button" className="knowledge-primary" disabled={busy} onClick={() => openIntake()}><FolderPlus size={16} />{t("New project")}</button></div>
          </div> : <>
            {searchResults !== undefined && <div className="project-search-results">{searchResults.length ? searchResults.map((hit, index) => <button type="button" key={`${hit.source_id}:${index}`} onClick={() => void perform(() => openFile(hit.source_id, hit.filename))}><FileText size={16} /><span><strong>{hit.filename}</strong><span>{hit.heading_path}</span><p>{hit.excerpt}</p></span></button>) : <p>{t("No matches")}</p>}</div>}
            <div className="project-browser"><div className={`project-split${project || reader ? " has-detail" : ""}`}><div className="project-list"><label className="project-select-all"><input type="checkbox" checked={visibleProjects.length > 0 && visibleProjects.every(p => selected.includes(p.id))}
              onChange={event => setSelected(event.target.checked ? [...new Set([...selected, ...visibleProjects.map(p => p.id)])] : selected.filter(id => !visibleProjects.some(p => p.id === id)))} />{t("Select visible projects")}</label>
              {visibleProjects.map(p => <div className={`project-row${projectId === p.id ? " is-open" : ""}`} key={p.id}><input type="checkbox" aria-label={t("Select {v0}", { v0: p.name })} checked={selected.includes(p.id)} onChange={() => toggle(p.id)} />
                <button type="button" aria-pressed={projectId === p.id} onClick={() => { if (projectId !== p.id) setReader(undefined); setProjectId(p.id); setRename(undefined); }}><FolderOpen size={20} /><span className="project-row-text"><strong>{p.name}</strong><span className="project-row-meta"><span>{t("{v0} / {v1} files ready", { v0: p.converted_count, v1: p.source_count })}</span><Status state={p.state} /></span></span></button></div>)}
              {!visibleProjects.length && <p className="project-no-matches">{t("No matches")}</p>}
              {moreProjects && <button type="button" disabled={busy} onClick={() => setProjectPages(value => value + 1)}>{t("Load more projects")}</button>}
            </div>{(project || reader) && <div className="project-detail-column">{project && <div className="project-detail"><div className="project-detail-heading"><h3>{project.name}</h3><div className="knowledge-heading-actions"><button type="button" aria-label={t("Rename project")} className="knowledge-icon-button" onClick={() => setRename(project.name)}><Pencil size={15} /></button>
              <button type="button" aria-label={t("Delete project")} title={t("Delete project")} className="knowledge-icon-button" disabled={busy} onClick={() => setRemoval("project")}><Trash2 size={15} /></button>
              <button type="button" aria-label={t("Close project")} className="knowledge-icon-button" onClick={() => { setProjectId(""); setReader(undefined); }}><X size={17} /></button></div></div>
              {rename !== undefined && <form className="project-rename" onSubmit={event => { event.preventDefault(); void perform(async () => { await call("project_manage", { operation: "update", project_id: project.id, name: rename }); setRename(undefined); await load(); }); }}>
                <input aria-label={t("Project name")} value={rename} onChange={event => setRename(event.target.value)} /><button type="submit" disabled={busy || !rename.trim()}>{t("Save")}</button></form>}
              {project.description && <p>{project.description}</p>}<div className="project-files-heading"><span>{t("Source files")}</span><span>{project.files.length}</span></div><div className="project-file-list">{project.files.map(f => <div className="project-file" key={f.id}><input type="checkbox" aria-label={t("Select {v0}", { v0: f.relative_path || f.filename })} checked={fileChecks.includes(f.id)} disabled={busy} onChange={event => { setRemoval(undefined); setFileChecks(event.target.checked ? [...fileChecks, f.id] : fileChecks.filter(id => id !== f.id)); }} /><FileText size={16} /><button type="button" disabled={!f.record_id || busy} onClick={() => void perform(() => openFile(f.id, f.filename))}>{f.relative_path || f.filename}</button><Status state={f.state} />{f.error && <p role="alert">{f.error}</p>}</div>)}</div>
              <div className="project-action-row"><button type="button" disabled={busy} onClick={() => openIntake(project.id)}><Plus size={15} />{t("Add files")}</button>
                {!!fileChecks.length && <button type="button" disabled={busy} onClick={() => setRemoval("files")}><Trash2 size={15} />{t("Remove selected ({v0})", { v0: fileChecks.length })}</button>}
                {project.converted_count < project.source_count && <button type="button" disabled={busy || project.state === "converting"} onClick={() => void convert([project.id])}><RefreshCw size={15} />{t("Convert pending files")}</button>}</div>
              {removal && <div role="alert" className="knowledge-confirm"><p>{removal === "project"
                ? t("Delete \"{v0}\" with its {v1} files and every result extracted from them? The knowledge graph is rebuilt without them.", { v0: project.name, v1: project.files.length })
                : t("Remove {v0} selected files? They disappear from search and extraction, and results that used them are marked out of date.", { v0: fileChecks.length })}</p>
                <div className="project-action-row"><button type="button" disabled={busy} onClick={() => setRemoval(undefined)}>{t("Cancel")}</button>
                  <button type="button" className="knowledge-primary" disabled={busy} onClick={() => void (removal === "project" ? deleteProject() : removeFiles())}><Trash2 size={15} />{removal === "project" ? t("Delete project") : t("Remove files")}</button></div></div>}
            </div>}{documentReader}</div>}</div>
          {!!selected.length && <div className="project-batchbar"><div className="project-batch-summary"><div><span>{t("{v0} selected", { v0: selected.length })}</span><button type="button" className="knowledge-icon-button" aria-label={t("Clear selection")} title={t("Clear selection")} onClick={() => setSelected([])}><X size={14} /></button></div>
            {!deployed && <div className="project-model-choice">{canRunAgent ? <span className="knowledge-meta"><Sparkles size={13} />{agent?.name}</span> : modelSelect}</div>}</div><div className="project-batch-actions">{spaces.length > 0 ? <>{spacePicker}
            <button type="button" className="knowledge-primary" disabled={runBusy || !spaceId || !ready || !canRun} onClick={() => void execute()}><Play size={16} />{t("Run Space")}</button></> : <button type="button" className="knowledge-primary" onClick={() => { onTab("spaces"); newSpace("table"); }}><Plus size={15} />{t("Create a Space")}</button>}
            {!ready && <button type="button" disabled={busy} onClick={() => void convert(selected)}>{t("Convert selected projects")}</button>}
            {!canRun && <span className="knowledge-meta">{t("Connect an agent or choose a model to extract")}</span>}</div></div>}
          </div></>}
          {!projects.length && documentReader}
        </>}
      </div>
    </WorkspaceSection>
    <WorkspaceSection id="spaces" title={t("Spaces")} className="knowledge-section knowledge-wide" style={sectionVisible("spaces") ? undefined : { display: "none" }} layout={{ min_width: 320, min_height: 320, ideal_width: 960, ideal_height: 640 }}>
      <div className="knowledge-section project-workspace" data-detached={isDetached("spaces") || undefined}>
        <WorkspaceToolbar label={t("Spaces")} className="knowledge-section-toolbar"><strong>{t("Spaces")}</strong><button type="button" className="knowledge-primary" disabled={busy} onClick={() => newSpace("table")}><Plus size={16} />{t("New Space")}</button></WorkspaceToolbar>{banner}
        {editor ? <form className="project-space-editor" onSubmit={event => { event.preventDefault(); void saveSpace(); }}><div className="project-detail-heading"><h3>{editor.readOnly ? t("Built-in Space") : editor.id ? t("Edit Space") : t("Define an extraction Space")}</h3><button type="button" aria-label={t("Close Space editor")} onClick={() => setEditor(undefined)}><X size={16} /></button></div>
          {!editor.id && <div className="project-template-picker"><button type="button" aria-pressed={editor.kind === "table"} onClick={() => newSpace("table")}><Table2 size={24} />{t("Structured data")}</button><button type="button" aria-pressed={editor.kind === "graph"} onClick={() => newSpace("graph")}><Network size={24} />{t("Knowledge graph")}</button></div>}
          <label>{t("Space name")}<input aria-label={t("Space name")} value={editor.name} required readOnly={editor.readOnly} onChange={event => setEditor({ ...editor, name: event.target.value })} /></label>
          <label>{t("Description")}<input aria-label={t("Description")} value={editor.description} readOnly={editor.readOnly} onChange={event => setEditor({ ...editor, description: event.target.value })} /></label>
          <label>{t("Extraction instructions")}<textarea aria-label={t("Extraction instructions")} rows={4} required readOnly={editor.readOnly} value={editor.system_prompt} onChange={event => setEditor({ ...editor, system_prompt: event.target.value })} /></label>
          <label>{t("Output schema")}<textarea aria-label={t("Output schema")} className="project-code" rows={13} required readOnly={editor.readOnly} spellCheck={false} value={editor.definition} onChange={event => setEditor({ ...editor, definition: event.target.value })} /></label>
          <div className="project-action-row"><button type="button" onClick={() => setEditor(undefined)}>{editor.readOnly ? t("Close") : t("Cancel")}</button>{!editor.readOnly && <button type="submit" className="knowledge-primary" disabled={busy || !editor.name.trim()}>{t("Save Space")}</button>}</div>
        </form> : spaces.length ? <div className="project-space-cards">{spaces.map(s => <article key={s.id} className="project-space-card"><div className="project-space-icon">{s.kind === "graph" ? <Network size={24} /> : <Table2 size={24} />}</div><h3>{s.name}</h3><span className="knowledge-meta">{s.kind === "graph" ? t("Knowledge graph") : t("Structured data")} · v{s.version}</span><p>{s.description}</p>
          <div className="project-action-row">{s.builtin ? <button type="button" onClick={() => setEditor({ ...s, description: s.description ?? "", definition: pretty(s.definition), readOnly: true })}><Lock size={14} />{t("View built-in")}</button> : <button type="button" onClick={() => setEditor({ ...s, description: s.description ?? "", definition: pretty(s.definition) })}><Pencil size={14} />{t("Configure")}</button>}<button type="button" onClick={() => { setSpaceId(s.id); onTab("projects"); }}><ArrowRight size={14} />{t("Use Space")}</button></div></article>)}</div>
          : <WorkspaceEmptyState icon={<Layers3 size={30} />} title={t("Define what you want to extract")} description={t("Reuse a Space across documents, reports and datasets.")} actions={<div className="project-action-row"><button type="button" className="knowledge-primary" onClick={() => newSpace("table")}><Table2 size={16} />{t("Structured data")}</button><button type="button" onClick={() => newSpace("graph")}><Network size={16} />{t("Knowledge graph")}</button></div>} />}
      </div>
    </WorkspaceSection>
    <WorkspaceSection id="results" title={t("Results")} className="knowledge-section knowledge-wide" style={sectionVisible("results") ? undefined : { display: "none" }} layout={{ min_width: 320, min_height: 320, ideal_width: 960, ideal_height: 640 }}>
      <div className="knowledge-section project-workspace" data-detached={isDetached("results") || undefined}>
        <WorkspaceToolbar label={t("Results")} className="knowledge-section-toolbar"><div className="project-toolbar-title"><strong>{t("Results")}</strong><span className="project-count">{visibleResults.length}</span></div>{spacePicker}<div className="knowledge-heading-actions"><label className="project-history"><input type="checkbox" checked={history} onChange={event => setHistory(event.target.checked)} />{t("Show history")}</label>{refreshButton}</div></WorkspaceToolbar>{banner}
        {!!selected.length && <div className="project-result-scope"><span>{t("Results for {v0} selected projects", { v0: selected.length })}</span><button type="button" onClick={() => setSelected([])}>{t("Show all projects")}</button></div>}
        {!visibleResults.length ? <WorkspaceEmptyState icon={<Table2 size={30} />} title={t("Project results appear here")} description={t("Choose projects and run a Space to extract their data.")} actions={<button type="button" className="knowledge-primary" onClick={() => onTab("projects")}><FolderOpen size={16} />{t("Choose projects")}</button>} />
          : <div className={`project-split project-results-layout${currentResult ? " has-detail" : ""}`}><div className="project-result-list">{visibleResults.map(r => <button type="button" key={r.id} aria-pressed={resultId === r.id} onClick={() => { setResultId(r.id); setResultEdit(undefined); setGraphSelection(""); }}>
            <strong title={r.project_name}>{r.project_name}</strong><span title={r.space_name}>{r.space_name} · v{r.space_version} · #{r.generation}</span><Status state={r.status} />{r.stale && <span className="project-status is-failed"><CircleAlert size={13} />{t("Out of date")}</span>}</button>)}</div>
            {currentResult && <div className="project-result-detail"><div className="project-detail-heading"><div><h3 title={currentResult.project_name}>{currentResult.project_name}</h3><span className="knowledge-meta" title={currentResult.space_name}>{currentResult.space_name} · v{currentResult.space_version}</span></div><Status state={currentResult.status} /></div>
              {currentResult.error && <p role="alert" className="knowledge-error">{currentResult.error}</p>}
              {currentResult.stale && <div role="status" className="knowledge-confirm"><p>{currentResult.stale_reason} {t("Review it, or extract again from the current files.")}</p>
                <div className="project-action-row"><button type="button" className="knowledge-primary" disabled={runBusy || !canRun} onClick={() => void execute([currentResult.project_id], true, currentResult.space_id)}><RefreshCw size={15} />{t("Re-extract")}</button></div></div>}
              {currentResult.status === "completed" && <><div className="project-result-actions"><button type="button" disabled={runBusy || !canRun} onClick={() => void review()}><Sparkles size={15} />{t("Review result")}</button><button type="button" disabled={runBusy || !canRun} onClick={() => setPostProcess("")}><Layers3 size={15} />{t("Post-process")}</button><button type="button" disabled={runBusy} onClick={() => { setResultEdit(pretty(currentResult.data)); setRevisionNote(""); }}><Pencil size={15} />{t("Edit result")}</button>
                <button type="button" disabled={runBusy || !canRun} onClick={() => void execute([currentResult.project_id], true, currentResult.space_id)}><RefreshCw size={15} />{t("Re-extract")}</button></div>
                {postProcess !== undefined && <form className="project-space-editor" onSubmit={event => { event.preventDefault(); void review(postProcess); }}><label>{t("Post-processing instructions")}<textarea aria-label={t("Post-processing instructions")} rows={3} value={postProcess} onChange={event => setPostProcess(event.target.value)} placeholder={t("For example: normalize units and check the reported values against the source.")} /></label><div className="project-action-row"><button type="button" onClick={() => setPostProcess(undefined)}>{t("Cancel")}</button><button type="submit" className="knowledge-primary" disabled={busy || !postProcess.trim()}>{t("Run post-processing")}</button></div></form>}
                {resultEdit !== undefined ? <div className="project-space-editor"><label>{t("Result JSON")}<textarea aria-label={t("Result JSON")} rows={14} className="project-code" value={resultEdit} onChange={event => setResultEdit(event.target.value)} /></label><label>{t("Revision note")}<input aria-label={t("Revision note")} value={revisionNote} onChange={event => setRevisionNote(event.target.value)} /></label><div className="project-action-row"><button type="button" onClick={() => setResultEdit(undefined)}>{t("Cancel")}</button><button type="button" className="knowledge-primary" disabled={busy || !revisionNote.trim()} onClick={() => void revise()}>{t("Save revision")}</button></div></div>
                  : <ProjectionData projection={currentResult} definition={currentResult.space_snapshot?.definition} selected={graphSelection} onSelect={setGraphSelection} />}
              </>}
              {currentResult.status === "failed" && <button type="button" className="knowledge-primary" disabled={runBusy || !canRun} onClick={() => void execute([currentResult.project_id], true, currentResult.space_id)}><RefreshCw size={15} />{t("Retry extraction")}</button>}
              {currentResult.status === "running" && <div className="project-action-row project-run-controls">
                <button type="button" disabled={runBusy || !canRun} onClick={() => void execute([currentResult.project_id], true, currentResult.space_id)}><RefreshCw size={15} />{t("Restart extraction")}</button>
                {logAvailable && <button type="button" onClick={() => setLogOpen(true)}>{t("View log")}</button>}
                <button type="button" className="project-stop" disabled={stopping} onClick={() => void stop()}><Square size={13} />{stopping ? t("Stopping…") : t("Stop")}</button></div>}
              <details className="project-evidence"><summary><ChevronDown size={14} />{t("Source files")} <span>{currentResult.sources.length}</span></summary>{currentResult.sources.map(f => <button key={f.source_id} type="button" onClick={() => { onTab("projects"); setProjectId(currentResult.project_id); void perform(() => openFile(f.source_id, f.filename)); }}><FileText size={14} />{f.relative_path || f.filename}</button>)}</details>
            </div>}
          </div>}
        {moreResults && <button type="button" disabled={busy} onClick={() => setResultPages(value => value + 1)}>{t("Load more results")}</button>}
      </div>
    </WorkspaceSection>
    {logOpen && <RunLogModal agentId={!deployed ? agent?.id : undefined} runId={agentTask?.runId} events={progressLog}
      stopping={stopping} canStop={canStop} onStop={() => void stop()} onClose={() => setLogOpen(false)} />}
  </>;
}

function ProjectionData({ projection, definition, selected, onSelect }: { projection: Projection; definition?: Record<string, unknown>; selected: string; onSelect: (id: string) => void }) {
  const data = projection.data;
  if (projection.kind === "graph" && data && typeof data === "object" && !Array.isArray(data)) {
    const graph = data as { entities?: { id: string; name: string; type?: string; description?: string }[]; relations?: { source: string; target: string; predicate: string; snippet?: string; description?: string; evidence_level?: number }[] };
    const entity = graph.entities?.find(e => e.id === selected);
    const relation = graph.relations?.find((_r, i) => `${projection.id}:${i}` === selected);
    const name = (id: string) => graph.entities?.find(e => e.id === id)?.name ?? id;
    return <><GraphMap graphKey={`projection:${projection.id}`} entities={(graph.entities ?? []).map(e => ({ ...e, type: e.type || "concept", properties: { description: e.description } }))}
      relations={(graph.relations ?? []).map((r, i) => ({ id: `${projection.id}:${i}`, source_id: r.source, target_id: r.target, type: r.predicate, properties: { snippet: r.snippet } }))} selected={selected} onSelect={onSelect} onExpand={onSelect} />
      <details className="project-graph-elements"><summary>{t("Entities and relationships")}</summary><div>{(graph.entities ?? []).map(e => <button key={e.id} type="button" onClick={() => onSelect(e.id)}>{e.name}</button>)}</div><div className="project-graph-relations">{(graph.relations ?? []).map((r, i) => <div key={i}><button type="button" onClick={() => onSelect(`${projection.id}:${i}`)}>{name(r.source)} → {r.predicate.replaceAll("_", " ")} → {name(r.target)}</button>{(r.description || r.snippet) && <p>{r.description || r.snippet}</p>}</div>)}</div></details>
      {entity && <div className="project-graph-detail"><strong>{entity.name}</strong><p>{entity.description}</p>
        <div className="project-graph-relations">{(graph.relations ?? []).map((r, i) => ({ r, i })).filter(({ r }) => r.source === entity.id || r.target === entity.id).map(({ r, i }) =>
          <div key={i}><button type="button" onClick={() => onSelect(`${projection.id}:${i}`)}>{r.source === entity.id ? "→" : "←"} {r.predicate.replaceAll("_", " ")} {name(r.source === entity.id ? r.target : r.source)}</button>
            {(r.description || r.snippet) && <p>{r.description || r.snippet}</p>}</div>)}</div></div>}
      {relation && <div className="project-graph-detail"><strong>{relation.predicate.replaceAll("_", " ")}</strong><p>{relation.description}</p>{relation.snippet && <blockquote>{relation.snippet}</blockquote>}{relation.evidence_level !== undefined && <span>{t("Evidence level {v0}", { v0: relation.evidence_level })}</span>}<div className="project-action-row"><button type="button" onClick={() => onSelect(relation.source)}>{name(relation.source)}</button><ArrowRight size={14} /><button type="button" onClick={() => onSelect(relation.target)}>{name(relation.target)}</button></div></div>}
      <details><summary>{t("Result JSON")}</summary><pre className="project-json">{pretty(data)}</pre></details></>;
  }
  return data && typeof data === "object" ? <RecordTable key={projection.id} data={data as Record<string, unknown> | unknown[]} definition={definition} /> : <pre className="project-json">{pretty(data)}</pre>;
}
