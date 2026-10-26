import { t, useLocale, useWorkspaceSections, WorkspaceEmptyState, WorkspaceSection, WorkspaceToolbar } from "@oaw/plugin-api";
import type { FrontendPlugin, PluginViewProps } from "@oaw/plugin-api";
import { useCallback, useEffect, useRef, useState } from "react";
import { useModelOptions, useConnectedCards, updateCardConfig } from "@oaw/plugin-api";
import { hostApiBase as deploymentApiBase } from "@oaw/plugin-api";
import { ModelSelect, Modal } from "@oaw/plugin-api";
import { DocumentMarkdown } from "@oaw/plugin-api";
import { BookOpen, FlaskConical, Settings2, Plus, RefreshCw, MoreHorizontal, FileText, X, Square, ScrollText, Upload, Search, ChevronRight, ChevronDown, List, Table2, Pencil, Network, ArrowRight, CheckCircle2, CircleAlert, LoaderCircle } from "lucide-react";
import { MineruCredentials } from "./MineruCredentials";
import { InsightsSection } from "./Insights";
import type { InsightKind } from "./Insights";
import { KnowledgeSection } from "./Knowledge";
import type { BridgeProgress } from "./Knowledge";
import { RecordsSection } from "./Records";
import { SchemaEditor } from "./SchemaEditor";
import type { SchemaDetail, SchemaItem } from "./SchemaEditor";
import { ExtractDialog } from "./ExtractDialog";
import type { ExtractDetails } from "./ExtractDialog";
import { RecordGrid, RecordTable, flattenValues } from "./RecordView";
import { RecordForm } from "./RecordForm";
import "./style.css";

type Settings = { collection_name: string; pdf_engine: string; mineru_base_url: string; vision_model: string; embedding_model: string };
type IndexStatus = { enabled: boolean; embedding_model: string | null; model_id: string | null; total: number;
  embedded: number; pending: number; stale: number; last_error?: string | null; built_at?: string | null };
type Group = { id: string; name: string; source_count: number; created_at: string; is_default: boolean };
type Overview = { collection: { id: string; name: string }; groups: Group[]; settings: Settings;
  engines: string[]; counts: Record<string, number>; active_jobs: number };
type Source = { id: string; filename: string; media_type: string; size: number; created_at: string;
  group_id: string | null; group_name: string | null;
  markdown: { artifact_id: string; size: number; engine: string | null } | null;
  record_id: string | null; records: number };
type Extracted = { record_id: string; filename: string | null; engine: string | null;
  total_characters: number; offset: number; markdown: string; has_more: boolean };
type Job = { id: string; kind: string; status: string; progress: number | null; message: string | null;
  error: string | null; source_id: string | null; group_id: string | null; created_at: string;
  completed_at: string | null };
type JobEvent = { event: string; step: string | null; message: string | null; level: string | null;
  occurred_at: string };
type SearchResult = { source_id: string; filename: string | null; record_id: string | null;
  group_id: string | null; group_name: string | null; heading_path: string | null; excerpt: string };
type ExperimentItem = { id: string; group_id: string; schema_id: string; bundle_id: string | null; name: string;
  data: Record<string, unknown>; validation: { valid?: boolean; errors?: string[]; missing?: string[] } | null;
  note: string | null; status: "draft" | "published";
  revision: number; created_by: string | null; created_at: string; updated_at: string };
type ExperimentDetail = ExperimentItem & { sources: { source_id: string; filename: string | null }[] };
type BundleFile = { source_id: string; filename: string; media_type: string; size: number;
  state: "pending" | "converting" | "converted" | "failed"; job_id: string | null; error: string | null };
type Bundle = { id: string; group_id: string; schema_id: string; name: string; notes: string;
  status: "ready" | "extracting" | "extracted" | "failed" | "deleted"; run_id: string | null;
  created_at: string; updated_at: string; files: BundleFile[]; records: number };
type RunSummary = { run_id: string; agent_id: string; status: string; error: string | null;
  created_at: string; finished_at: string | null;
  lifecycle?: { model_requests?: { model_request: number; error_type?: string; elapsed_ms?: number;
    elapsed_seconds?: number; idle_seconds?: number; finish_reason?: string }[] } };
type RunActivity = { id: string; type: string; timestamp: string; text?: string; name?: string;
  success?: boolean; interrupted?: boolean };
/** An approval the card asked the person to confirm before it publishes a fact. */
type Pending = { args: Record<string, unknown>; reasons: string[] };

/** Literature reads papers: Knowledge drafts the general concept graph from chosen
 * sources, Records reads structured data out of them with a schema and draws
 * insights over it. Experiment turns one upload of several files into reviewed
 * records against a schema, then Insights extracts what those records show as a
 * whole. Both publish into the one graph, in their own layer. Settings only ever
 * appears for the engineering view. */
type Category = "literature" | "experiment" | "settings";
type LiteratureTab = "sources" | "knowledge" | "records";
type ExperimentTab = "experiments" | "insights" | "schemas";
const LITERATURE_TABS: { id: LiteratureTab; label: () => string }[] = [
  { id: "sources", label: () => t("Sources") }, { id: "knowledge", label: () => t("Knowledge") },
  { id: "records", label: () => t("Records") },
];
const EXPERIMENT_TABS: { id: ExperimentTab; label: () => string }[] = [
  { id: "experiments", label: () => t("Experiments") }, { id: "insights", label: () => t("Insights") },
  { id: "schemas", label: () => t("Schemas") },
];
const KIND_LABELS: Record<InsightKind, () => string> = {
  anomaly: () => t("anomaly"), rule: () => t("rule"), trend: () => t("trend"),
  correlation: () => t("correlation"), threshold: () => t("threshold"), note: () => t("note"),
};

const ACTIVE = new Set(["QUEUED", "RUNNING", "PENDING", "RETRYING", "CANCELLING"]);
const message = (error: unknown) => error instanceof Error ? error.message : String(error);
const shortId = (value: string) => value.length > 13 ? `${value.slice(0, 8)}…` : value;
const bytes = (value: number) => value < 1024 * 1024 ? `${(value / 1024).toFixed(1)} KiB` : `${(value / 1024 / 1024).toFixed(1)} MiB`;
const pretty = (value: unknown) => JSON.stringify(value, null, 2);

const fileBase64 = (file: File) => new Promise<string>((resolve, reject) => {
  const reader = new FileReader();
  reader.onerror = () => reject(reader.error ?? new Error("File read failed"));
  reader.onload = () => resolve(String(reader.result).split(",", 2)[1] ?? "");
  reader.readAsDataURL(file);
});

/** The name an upload gets the moment its files are dropped, before anyone has
 * typed one: the first file's stem, counting the rest. Confirmed or changed in the
 * extraction window. */
const provisionalName = (files: File[]) => {
  const stem = (files[0]?.name ?? "").replace(/\.[^.]+$/, "") || t("Upload");
  return files.length > 1 ? `${stem} +${files.length - 1}` : stem;
};

/** An extraction needs a model, so it goes through the host bridge, not the
 * plugin. The engineering bridge lives at ``/api/...``; a deployment mounts the same
 * bridge scoped under the runtime workspace instead, gated by the release's grants. */
async function request(path: string, body: unknown, deployed: boolean, onProgress?: (event: BridgeProgress) => void) {
  const base = deployed ? `${deploymentApiBase}/runtime-app/workspace` : deploymentApiBase;
  const response = await fetch(`${base}/${path}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const type = response.headers?.get?.("content-type") ?? "";
  if (!type.includes("x-ndjson") || !response.body) {
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : t("The extraction request failed"));
    return data as Record<string, unknown>;
  }
  // The engineering routes stream: progress lines, then one done or error line.
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result: Record<string, unknown> | undefined;
  const handle = (line: string) => {
    if (!line.trim()) return;
    const event = JSON.parse(line) as Record<string, unknown>;
    if (event.event === "progress") onProgress?.(event as unknown as BridgeProgress);
    else if (event.event === "done") result = event.result as Record<string, unknown>;
    else if (event.event === "error") throw new Error(typeof event.detail === "string" ? event.detail : t("The extraction request failed"));
  };
  for (;;) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    const lines = buffer.split("\n");
    buffer = done ? "" : (lines.pop() ?? "");
    for (const line of lines) handle(line);
    if (done) break;
  }
  if (buffer) handle(buffer);
  if (!result) throw new Error(t("The extraction request failed"));
  return result;
}

/** Read a host API resource (engineering canvas only — the run log is not part of
 * a deployed release, which carries no agent edges to start a run with). */
async function fetchHost<T>(path: string): Promise<T> {
  const response = await fetch(`${deploymentApiBase}/${path}`);
  if (!response.ok) throw new Error(t("Request failed ({v0})", { v0: response.status }));
  return response.json() as Promise<T>;
}

async function postHost(path: string) {
  const response = await fetch(`${deploymentApiBase}/${path}`, { method: "POST" });
  if (!response.ok) throw new Error(t("Request failed ({v0})", { v0: response.status }));
}

/** The newest run of one agent — what ``runAgent`` just started, since the host
 * does not hand the id back to a plugin. */
async function latestRun(agentId: string): Promise<RunSummary | undefined> {
  const runs = await fetchHost<RunSummary[]>(`runs?agent_id=${encodeURIComponent(agentId)}`);
  return [...runs].sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
}

/** The shared error/notice banner, duplicated at the top of every section so it is
 * visible whichever one is showing — the alternative (once, in the shared toolbar)
 * is invisible whenever a layout extracts every section into its own tab, exactly
 * the shape the "Lab Manager" preset uses. */
function Banner({ error, notice }: { error: string; notice: string }) {
  if (!error && !notice) return null;
  return <>
    {error && <p role="alert" className="knowledge-error">{error}</p>}
    {notice && <p role="status" className="knowledge-notice">{notice}</p>}
  </>;
}

function Preview({ host, card }: PluginViewProps) {
  useLocale();
  const [summary, setSummary] = useState<Overview>();
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    host.resourceAction("overview", {}).then(value => { if (active) setSummary(value as Overview); })
      .catch(reason => { if (active) setError(message(reason)); });
    return () => { active = false; };
  }, [host, card.id]);
  return <div className="knowledge-preview">
    <span className="knowledge-badge">{t("Knowledge base")}</span>
    {error ? <p role="alert" title={error}>{t("Knowledge base unavailable")}</p>
      : !summary ? <p role="status">{t("Opening knowledge base…")}</p>
      : <>
        <strong>{summary.counts.entities} {summary.counts.entities === 1 ? t("published entity") : t("published entities")}</strong>
        <p>{t("{v0} documents · {v1} relations · {v2} draft(s) awaiting review", {
          v0: summary.counts.sources, v1: summary.counts.relations,
          v2: (summary.counts.knowledge_drafts - summary.counts.published_knowledge) + (summary.counts.insights - summary.counts.published_insights) })}</p>
      </>}
  </div>;
}

export function Workspace({ host, card }: PluginViewProps) {
  useLocale();
  const { isInline, isDetached } = useWorkspaceSections();
  // The navigation switches which workflow is visible; each workflow remembers its
  // own last-opened tab so hopping to Experiment and back does not lose your place.
  const [category, setCategory] = useState<Category>("literature");
  const [literatureTab, setLiteratureTab] = useState<LiteratureTab>("sources");
  const [experimentTab, setExperimentTab] = useState<ExperimentTab>("experiments");
  const [overview, setOverview] = useState<Overview>();
  const [sources, setSources] = useState<Source[]>([]);
  const [schemas, setSchemas] = useState<SchemaItem[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);

  // "" means every group; otherwise the id every list is narrowed to, so sources,
  // markdown, drafts, records and the graph all stay scoped to one group at a time.
  const [activeGroup, setActiveGroup] = useState("");
  const [groupName, setGroupName] = useState("");
  const [groupEditing, setGroupEditing] = useState(false);
  const [groupCreating, setGroupCreating] = useState(false);
  const [rawMarkdown, setRawMarkdown] = useState(false);
  const [selectedSources, setSelectedSources] = useState<string[]>([]);
  const [suggestedSourceIds, setSuggestedSourceIds] = useState<string[]>();
  const [knowledgeVisited, setKnowledgeVisited] = useState(false);
  const consumeSourceSuggestion = useCallback(() => {
    setSuggestedSourceIds(undefined); setSelectedSources([]);
  }, []);
  const [sourceDrag, setSourceDrag] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<{ done: number; total: number }>();
  // Literature → Records: "" shows every literature schema's records; a chosen id narrows to it.
  const [literatureSchemaId, setLiteratureSchemaId] = useState("");
  // A record an insight's evidence asked Literature → Records to unfold.
  const [focusRecordId, setFocusRecordId] = useState("");

  const [selectedSource, setSelectedSource] = useState("");
  const [extracted, setExtracted] = useState<Extracted>();
  const [documentError, setDocumentError] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<SearchResult[]>();
  const [searchError, setSearchError] = useState("");
  const [experiments, setExperiments] = useState<ExperimentItem[]>([]);
  // Records open in place: which rows are unfolded, the full detail (sources,
  // revision) fetched for each as it opens, and the one row being edited — its data
  // as the form sees it, its raw JSON when the person flips to that view, and the
  // name/note fields beside it.
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [recordDetails, setRecordDetails] = useState<Record<string, ExperimentDetail>>({});
  const [editingId, setEditingId] = useState("");
  const [recordDraft, setRecordDraft] = useState<Record<string, unknown>>({});
  const [recordName, setRecordName] = useState("");
  const [recordNote, setRecordNote] = useState("");
  const [recordJson, setRecordJson] = useState("");
  const [rawRecord, setRawRecord] = useState(false);
  const [publishPending, setPublishPending] = useState<Pending & { record_id: string }>();
  // How the records are narrowed and shown: free text, status, and list or grid.
  const [recordQuery, setRecordQuery] = useState("");
  const [recordStatus, setRecordStatus] = useState<"" | "draft" | "published">("");
  const [recordView, setRecordView] = useState<"list" | "grid">("list");
  // Experiment: "" shows every schema's uploads and records; a chosen id narrows to it.
  const [experimentSchemaId, setExperimentSchemaId] = useState("");
  // Insights: the schema whose published records are read; "" lists every schema's
  // insights but extracts nothing. Bumping refreshVersion makes the section reload
  // alongside every other list.
  const [insightSchemaId, setInsightSchemaId] = useState("");
  const [refreshVersion, setRefreshVersion] = useState(0);
  // Full definitions by schema id — the record tables and forms need the properties,
  // which the schema list does not carry.
  const [schemaDetails, setSchemaDetails] = useState<Record<string, SchemaDetail>>({});
  const [bundles, setBundles] = useState<Bundle[]>([]);
  const [bundleId, setBundleId] = useState("");
  // The upload-details window: confirming name/description/schema before an
  // extraction, or just editing them.
  const [dialog, setDialog] = useState<{ mode: "extract" | "edit" }>();
  // A running extraction: which upload, and which agent to watch until it is done.
  const [extracting, setExtracting] = useState<{ bundle_id: string; agent_id: string; started_at: string }>();
  // The agent run behind the selected upload's last extraction, read from the host's
  // run API: status, error and the tool/progress timeline, so a failure is explained
  // right here rather than in a conversation the run never belonged to.
  const [runLog, setRunLog] = useState<{ run: RunSummary; activity: RunActivity[] }>();
  const [runLogError, setRunLogError] = useState("");
  const [runLogOpen, setRunLogOpen] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [notice, setNotice] = useState("");
  const [jobDetail, setJobDetail] = useState<{ job: Job; events: JobEvent[] }>();
  const [settings, setSettings] = useState<Settings>();
  const [indexStatus, setIndexStatus] = useState<IndexStatus>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const generation = useRef(0);
  const documentSequence = useRef(0);
  // A deployment mounts no model settings and no live picker, so extraction there
  // always uses whichever model was chosen ahead of time in the engineering Settings
  // section — never a value the deployed viewer can change.
  const deployed = !!host.deployment;
  const defaultModel = (card.config?.default_model as string | undefined) ?? "";
  const models = useModelOptions(["openai", "legacy"]);
  const [model, setModel] = useState("");
  const selectedModel = deployed ? defaultModel
    : models.some(item => item.value === model) ? model : (models[0]?.value ?? "");
  // A deployment never carries edges (only the flattened permissions grant), and
  // this is engineering-only anyway, so an empty list there is both correct and safe.
  const connectedCards = useConnectedCards(card.id, { direction: "incoming", relationshipPrefix: "knowledge.base.", trait: "core.agent" });
  const connectedAgents = deployed ? [] : connectedCards;
  // Research grants both extraction paths; keep the narrower roles eligible too.
  const researchAgents = useConnectedCards(card.id, { direction: "incoming", relationshipPrefix: "knowledge.base.research", trait: "core.agent" });
  const experimentAgents = useConnectedCards(card.id, { direction: "incoming", relationshipPrefix: "knowledge.base.experiment", trait: "core.agent" });
  const literatureAgents = useConnectedCards(card.id, { direction: "incoming", relationshipPrefix: "knowledge.base.extract", trait: "core.agent" });
  const labAgents = [...new Map([...experimentAgents, ...researchAgents].map(agent => [agent.id, agent])).values()];
  const extractAgents = [...new Map([...literatureAgents, ...researchAgents].map(agent => [agent.id, agent])).values()];
  const canRunAgents = !deployed && typeof host.runAgent === "function" && labAgents.length > 0;
  const canExtract = canRunAgents || !!selectedModel;


  const call = useCallback((action: string, args: Record<string, unknown> = {}, confirm?: boolean) =>
    host.resourceAction(action, args, confirm), [host]);

  // "" (All groups) asks for the merged, cross-group view; a chosen group narrows
  // every list to it. Graph and jobs already treat an omitted group_id as "all".
  const groupArgs = useCallback((): Record<string, unknown> =>
    activeGroup ? { group_id: activeGroup } : { all_groups: true }, [activeGroup]);
  const groupFilter = useCallback((): Record<string, unknown> =>
    activeGroup ? { group_id: activeGroup } : {}, [activeGroup]);

  // Select a source for the reader and jump to the Sources tab, wherever the click
  // that asked for it happened — a search result, a draft's source chip or a
  // record's evidence all point at a source, not at the tab.
  const viewSource = useCallback((id: string) => {
    setSelectedSource(id);
    setCategory("literature"); setLiteratureTab("sources");
  }, []);

  const refresh = useCallback(async () => {
    const current = generation.current;
    // Settled, not all-or-nothing: an older release may not have "experiments"
    // published yet (or any single call could hiccup), and that must not wipe out
    // every other section's data — only whichever call actually failed stays empty.
    const results = await Promise.allSettled([
      call("overview", groupArgs()), call("sources", { limit: 100, ...groupArgs() }),
      call("schemas", { operation: "list" }), call("jobs", { limit: 20, ...groupFilter() }),
      call("experiments", { limit: 200, ...groupArgs() }),
      call("experiment_bundles", { limit: 100, ...groupArgs() }),
    ]);
    if (generation.current !== current) return;
    const [summary, sourceList, schemaList, jobList, experimentList, bundleList] =
      results.map(result => result.status === "fulfilled" ? result.value : undefined);
    if (summary) { setOverview(summary as Overview); setSettings((summary as Overview).settings); }
    if (sourceList) setSources((sourceList.sources as Source[]) ?? []);
    if (schemaList) setSchemas((schemaList.schemas as SchemaItem[]) ?? []);
    if (jobList) setJobs((jobList.jobs as Job[]) ?? []);
    setExperiments((experimentList?.experiments as ExperimentItem[]) ?? []);
    setBundles((bundleList?.bundles as Bundle[]) ?? []);
    setRefreshVersion(value => value + 1);
    // A core section failing is still worth surfacing; "experiments" not being
    // published on an older release is not a real error, so it never blocks this.
    const coreFailure = results.slice(0, 4).find(result => result.status === "rejected");
    if (coreFailure) throw (coreFailure as PromiseRejectedResult).reason;
  }, [call, groupArgs, groupFilter]);

  const perform = useCallback(async (work: () => Promise<void>) => {
    const current = generation.current;
    setBusy(true); setError("");
    try { await work(); }
    catch (reason) { if (generation.current === current) setError(message(reason)); }
    finally { if (generation.current === current) setBusy(false); }
  }, []);

  useEffect(() => {
    void perform(refresh);
    return () => { generation.current += 1; documentSequence.current += 1; };
  }, [card.id, perform, refresh]);

  // A conversion job runs on the card's own thread, so poll until nothing is active.
  useEffect(() => {
    if (!jobs.some(job => ACTIVE.has(job.status))) return;
    const timer = setTimeout(() => { void refresh().catch(reason => setError(message(reason))); }, 1500);
    return () => clearTimeout(timer);
  }, [jobs, refresh]);

  const selectedDocumentId = sources.find(item => item.id === selectedSource)?.record_id;
  useEffect(() => {
    const sequence = ++documentSequence.current;
    setExtracted(undefined); setDocumentError("");
    if (!selectedSource || !selectedDocumentId) return;
    call("markdown", { source_id: selectedSource, limit: 40000 }).then(value => {
      if (sequence === documentSequence.current) setExtracted(value as Extracted);
    }).catch(reason => { if (sequence === documentSequence.current) setDocumentError(message(reason)); });
  }, [call, selectedSource, selectedDocumentId]);

  const upload = (files: FileList | File[]) => perform(async () => {
    const list = Array.from(files);
    if (!list.length) return;
    const currentGeneration = generation.current;
    const failures: string[] = [];
    setNotice(""); setUploadProgress({ done: 0, total: list.length });
    try {
      for (const [index, file] of list.entries()) {
        if (generation.current !== currentGeneration) return;
        try {
          if (file.size > 32 * 1024 * 1024) throw new Error(t("Up to 32 MiB per file"));
          const content = await fileBase64(file);
          if (generation.current !== currentGeneration) return;
          const result = await call("ingest", { filename: file.name, content_base64: content,
            media_type: file.type || "application/octet-stream",
            ...(activeGroup ? { group_id: activeGroup } : {}) });
          if (generation.current !== currentGeneration) return;
          const source = result.source as { id: string };
          // Queue only this upload; existing pending documents remain an explicit choice.
          await call("process", { source_ids: [source.id] });
        } catch (reason) { failures.push(`${file.name}: ${message(reason)}`); }
        if (generation.current === currentGeneration) setUploadProgress({ done: index + 1, total: list.length });
      }
      if (generation.current !== currentGeneration) return;
      await refresh();
      if (failures.length) throw new Error(failures.join("\n"));
    } finally {
      if (generation.current === currentGeneration) setUploadProgress(undefined);
    }
  });

  const processSources = (sourceIds: string[]) => perform(async () => {
    if (!sourceIds.length) return;
    const result = await call("process", { source_ids: sourceIds });
    const outcomes = result.jobs as { job?: { id: string } }[];
    const queued = outcomes.filter(item => item.job).length;
    setNotice(queued
      ? ""
      : t("Nothing to convert: already converted or already running"));
    setSelectedSources([]);
    await refresh();
  });

  const processPending = () => perform(async () => {
    const result = await call("process", groupFilter());
    const outcomes = result.jobs as unknown[];
    setNotice(outcomes.length
      ? ""
      : t("Nothing pending to convert"));
    await refresh();
  });

  const runSearch = () => perform(async () => {
    setSearchError("");
    if (!searchQuery.trim()) { setSearchResults(undefined); return; }
    try {
      const result = await call("search", { query: searchQuery.trim(), ...groupArgs() });
      setSearchResults(result.results as SearchResult[]);
    } catch (reason) { setSearchError(message(reason)); setSearchResults(undefined); }
  });

  const createGroup = () => perform(async () => {
    if (!groupName.trim()) throw new Error(t("Name the group first"));
    const result = await call("groups", { operation: "create", name: groupName.trim() });
    setActiveGroup((result.group as Group).id);
    setGroupName(""); setGroupEditing(false);
    await refresh();
  });

  const renameGroup = () => activeGroup && perform(async () => {
    if (!groupName.trim()) throw new Error(t("Name the group first"));
    await call("groups", { operation: "rename", group_id: activeGroup, name: groupName.trim() });
    setGroupName(""); setGroupEditing(false);
    await refresh();
  });

  const deleteGroup = () => activeGroup && perform(async () => {
    await call("groups", { operation: "delete", group_id: activeGroup });
    setActiveGroup("");
    await refresh();
  });

  const extend = () => extracted && perform(async () => {
    const next = await call("markdown", { source_id: selectedSource,
      offset: extracted.offset + extracted.markdown.length, limit: 40000 }) as Extracted;
    setExtracted({ ...next, offset: extracted.offset, markdown: extracted.markdown + next.markdown });
  });

  // The full record (sources, revision) behind a row, fetched as it opens and kept
  // until the list refreshes it.
  const loadExperiment = useCallback(async (id: string) => {
    const current = generation.current;
    const result = await call("experiments", { operation: "get", record_id: id });
    if (generation.current !== current) return undefined;
    const detail = result.experiment as ExperimentDetail;
    setRecordDetails(previous => ({ ...previous, [id]: detail }));
    return detail;
  }, [call]);

  const toggleRecord = (id: string) => {
    setExpanded(previous => {
      const next = new Set(previous);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
    if (!expanded.has(id) && !recordDetails[id]) void loadExperiment(id).catch(reason => setError(message(reason)));
  };

  const openRecord = (id: string) => {
    setRecordView("list");
    if (!expanded.has(id)) toggleRecord(id);
  };

  // From an insight's evidence to the record it cites: a literature record unfolds
  // in Literature → Records; an experiment one here, with the filters widened so
  // the row is in view.
  const jumpToRecord = (id: string) => {
    const record = experiments.find(item => item.id === id);
    if (record && !experimentSchemaIds.has(record.schema_id)) {
      setCategory("literature"); setLiteratureTab("records"); setFocusRecordId(id);
      return;
    }
    setCategory("experiment"); setExperimentTab("experiments");
    setBundleId(""); setRecordStatus(""); setRecordQuery("");
    if (record && currentExperimentSchemaId && record.schema_id !== currentExperimentSchemaId) setExperimentSchemaId("");
    openRecord(id);
  };

  const beginEdit = (record: ExperimentItem) => {
    const detail = recordDetails[record.id];
    const data = detail?.data ?? record.data;
    setEditingId(record.id); setPublishPending(undefined); setRawRecord(false);
    setRecordDraft(data); setRecordName(detail?.name ?? record.name); setRecordNote(detail?.note ?? record.note ?? "");
    setRecordJson(pretty(data));
  };

  // The schema bar's choice: "" means every experiment schema at once.
  const experimentSchemas = schemas.filter(item => item.kind === "experiment");
  const currentExperimentSchemaId = experimentSchemas.some(item => item.id === experimentSchemaId) ? experimentSchemaId : "";
  // Fetch the definition of every experiment schema in view once; the tables, the
  // grid's columns and the edit form all read from this cache.
  useEffect(() => {
    const wanted = experimentSchemas.map(item => item.id).filter(id => !(id in schemaDetails));
    if (!wanted.length) return;
    const current = generation.current;
    Promise.allSettled(wanted.map(id => call("schemas", { operation: "get", schema_id: id }))).then(results => {
      if (generation.current !== current) return;
      const loaded: Record<string, SchemaDetail> = {};
      results.forEach(result => { if (result.status === "fulfilled") { const detail = result.value.schema as SchemaDetail; loaded[detail.id] = detail; } });
      if (Object.keys(loaded).length) setSchemaDetails(previous => ({ ...previous, ...loaded }));
    });
    // schemaDetails is what this effect fills; keying on the schema list is enough.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [call, schemas]);

  // Dropping files is how an upload comes to exist: it is created on the spot under
  // the schema in view (or the only one), named after its files, and every file
  // starts converting at once. Name, description and schema are confirmed later, in
  // the extraction window.
  const uploadNewBundle = (files: FileList | File[]) => perform(async () => {
    const list = Array.from(files);
    if (!list.length) return;
    const schemaId = currentExperimentSchemaId || experimentSchemas[0]?.id;
    if (!schemaId) throw new Error(t("Create an experiment schema first"));
    const result = await call("experiment_bundle_manage", { operation: "create", schema_id: schemaId,
      name: provisionalName(list), notes: "", ...(activeGroup ? { group_id: activeGroup } : {}) });
    const bundle = result.bundle as Bundle;
    for (const file of list) {
      const content = await fileBase64(file);
      await call("experiment_ingest", { bundle_id: bundle.id, filename: file.name, content_base64: content,
        media_type: file.type || "application/octet-stream" });
    }
    setBundleId(bundle.id);
    setNotice(t("{v0} file(s) uploaded as \"{v1}\" · converting", { v0: list.length, v1: bundle.name }));
    await refresh();
  });

  const deleteBundle = (bundle: Bundle) => perform(async () => {
    await call("experiment_bundle_manage", { operation: "delete", bundle_id: bundle.id });
    if (bundleId === bundle.id) setBundleId("");
    if (experiments.some(item => item.id === editingId && item.bundle_id === bundle.id)) setEditingId("");
    setNotice(t("Upload \"{v0}\" removed", { v0: bundle.name }));
    await refresh();
  });

  const uploadToBundle = (bundle: Bundle, files: FileList | File[]) => perform(async () => {
    const list = Array.from(files);
    if (!list.length) return;
    for (const file of list) {
      const content = await fileBase64(file);
      await call("experiment_ingest", { bundle_id: bundle.id, filename: file.name, content_base64: content,
        media_type: file.type || "application/octet-stream" });
    }
    setNotice(t("{v0} uploaded · converting", { v0: list.length }));
    await refresh();
  });

  // Two ways to turn an upload into records. With a Lab assistant connected
  // (knowledge.base.experiment), Extract is a real Agent run: the agent reads the
  // upload through its knowledge tools and saves the records itself, and the card
  // only watches it until idle. Otherwise the host bridge makes one JSON-mode model
  // call and hands the result to the same experiment_save, so a record is validated
  // and evidence-linked identically whichever path produced it.
  const runExtraction = async (bundle: Bundle, replace: boolean) => {
    const agent = labAgents[0];
    if (agent && host.runAgent && !deployed) {
      const schema = schemas.find(item => item.id === bundle.schema_id);
      await call("experiment_bundle_manage", { operation: "update", bundle_id: bundle.id, status: "extracting", run_id: null });
      const request = { bundle_id: bundle.id, bundle_name: bundle.name, schema_id: bundle.schema_id,
        schema_name: schema?.name ?? null, knowledge_card: card.id, replace_drafts: replace,
        files: bundle.files.map(file => file.filename) };
      await host.runAgent(agent.id, [
        "Perform the experiment extraction described by the JSON data below.",
        "EXPERIMENT EXTRACTION REQUEST", JSON.stringify(request), "END EXPERIMENT EXTRACTION REQUEST",
        "Call knowledge_experiment_extract_prompt with this bundle_id and read the whole document (follow has_more with offset),",
        "produce one record per specimen, sample, run or table row, save them in one knowledge_experiment_save call",
        `with replace_drafts=${replace ? "true" : "false"}, then report what you saved and what was unclear.`,
      ].join("\n"));
      const startedAt = new Date().toISOString();
      setExtracting({ bundle_id: bundle.id, agent_id: agent.id, started_at: startedAt });
      setNotice(t("{v0} is extracting records from \"{v1}\"…", { v0: agent.name, v1: bundle.name }));
      // Remember which run this was, so the log can be reopened after a reload.
      const run = await latestRun(agent.id).catch(() => undefined);
      if (run && run.created_at >= startedAt.slice(0, 19)) {
        await call("experiment_bundle_manage", { operation: "update", bundle_id: bundle.id, status: "extracting", run_id: run.run_id }).catch(() => undefined);
      }
      await refresh();
      return;
    }
    if (!selectedModel) throw new Error(deployed
      ? t("This deployment has no default model configured yet")
      : t("Configure a model connection first, or connect a Lab assistant agent"));
    const result = await request(`knowledge/${card.id}/extract`, {
      bundle_id: bundle.id, model: selectedModel, replace_drafts: replace }, deployed);
    const records = result.records as unknown[];
    const invalid = result.invalid as number;
    setNotice(result.truncated
      ? t("Extracted {v0} record(s) from the first part of a very long upload", { v0: records.length })
      : invalid
        ? t("Extracted {v0} record(s); {v1} do not satisfy the schema — open them to see why", { v0: records.length, v1: invalid })
        : t("Extracted {v0} record(s) from \"{v1}\" · review and publish them below", { v0: records.length, v1: bundle.name }));
    await refresh();
  };

  // The extraction window's confirm: settle whatever changed about the upload, then
  // (in extract mode) read it. The window closes as soon as the work starts; an
  // error lands in the section's banner like any other.
  const confirmDialog = (bundle: Bundle, mode: "extract" | "edit", details: ExtractDetails) => perform(async () => {
    setDialog(undefined);
    let target = bundle;
    const patch: Record<string, unknown> = {};
    if (details.name !== bundle.name) patch.name = details.name;
    if (details.notes !== bundle.notes) patch.notes = details.notes;
    if (details.schema_id !== bundle.schema_id) patch.schema_id = details.schema_id;
    if (Object.keys(patch).length) {
      const result = await call("experiment_bundle_manage", { operation: "update", bundle_id: bundle.id, ...patch });
      target = result.bundle as Bundle;
    }
    if (mode === "extract") { await runExtraction(target, details.replace); return; }
    setNotice(t("Upload details saved"));
    await refresh();
  });

  useEffect(() => {
    if (!extracting) return;
    let active = true;
    const timer = window.setInterval(() => {
      host.getAgentInfo(extracting.agent_id).then(async info => {
        const state = info as { status?: string; active_run_id?: string | null };
        if (!active || state.status === "running" || state.active_run_id) return;
        active = false; window.clearInterval(timer);
        const current = generation.current;
        const result = await call("experiment_bundles", { operation: "get", bundle_id: extracting.bundle_id }).catch(() => undefined);
        if (generation.current !== current) return;
        const bundle = result?.bundle as Bundle | undefined;
        if (bundle?.status === "extracting") {
          const run = await latestRun(extracting.agent_id).catch(() => undefined);
          await call("experiment_bundle_manage", { operation: "update", bundle_id: bundle.id, status: "failed",
            run_id: run?.run_id ?? bundle.run_id }).catch(() => undefined);
          setNotice(run?.error
            ? t("The agent run failed: {v0} — see the extraction log below", { v0: run.error })
            : t("The agent finished without saving any records — see the extraction log below"));
        } else if (bundle) {
          setNotice(t("{v0} record(s) extracted from \"{v1}\" · review and publish them below", { v0: bundle.records, v1: bundle.name }));
        }
        setExtracting(undefined); setStopping(false);
        await refresh();
      }).catch(() => undefined);
    }, 2500);
    return () => { active = false; window.clearInterval(timer); };
  }, [extracting, host, call, refresh]);

  const loadRunLog = useCallback(async (runId: string) => {
    const current = generation.current;
    setRunLogError("");
    try {
      const [run, activity] = await Promise.all([
        fetchHost<RunSummary>(`runs/${encodeURIComponent(runId)}`),
        fetchHost<{ items: RunActivity[] }>(`runs/${encodeURIComponent(runId)}/activity?limit=100`)]);
      if (generation.current === current) setRunLog({ run, activity: activity.items ?? [] });
    } catch (reason) {
      if (generation.current === current) { setRunLog(undefined); setRunLogError(message(reason)); }
    }
  }, []);

  // A failed extraction loads its log so the summary under the upload is ready;
  // a running one is polled below while the log window is open.
  useEffect(() => {
    const bundle = bundles.find(item => item.id === bundleId);
    if (!deployed && bundle?.run_id && bundle.status === "failed" && runLog?.run.run_id !== bundle.run_id) void loadRunLog(bundle.run_id);
    // runLog is deliberately not a dependency: it is what this effect sets.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bundles, bundleId, deployed, loadRunLog]);

  // While the log window is open on a running extraction, keep it live.
  useEffect(() => {
    const bundle = bundles.find(item => item.id === bundleId);
    const runId = bundle?.run_id;
    if (!runLogOpen || !runId) return;
    void loadRunLog(runId);
    if (!extracting && bundle?.status !== "extracting") return;
    const timer = window.setInterval(() => { void loadRunLog(runId); }, 2000);
    return () => window.clearInterval(timer);
  }, [runLogOpen, bundles, bundleId, extracting, loadRunLog]);

  // Stop asks the host to stop the agent (the same thing its own card's Stop does);
  // the completion poller then sees the agent idle, finds no records, and marks the
  // upload failed with the run id, so the log explains the interruption.
  const stopExtraction = (bundle: Bundle) => {
    const agentId = extracting?.agent_id ?? labAgents[0]?.id;
    if (!agentId) return;
    setStopping(true);
    void postHost(`agents/${encodeURIComponent(agentId)}/stop`)
      .then(() => setNotice(t("Stopping the extraction of \"{v0}\"…", { v0: bundle.name })))
      .catch(reason => setError(message(reason)))
      .finally(() => setStopping(false));
  };

  const saveRecordEdits = (record: ExperimentItem) => perform(async () => {
    let data = recordDraft;
    if (rawRecord) {
      try { data = JSON.parse(recordJson); }
      catch { throw new Error(t("The record data must be valid JSON")); }
    }
    if (!recordName.trim()) throw new Error(t("Name the record first"));
    await call("experiment_update", { record_id: record.id, name: recordName.trim(), data,
      note: recordNote, expected_revision: record.revision });
    setEditingId("");
    setNotice(t("Changes saved"));
    await refresh();
    await loadExperiment(record.id);
  });

  const publishRecord = (record: ExperimentItem, confirmed?: Pending) => perform(async () => {
    const args = confirmed?.args ?? { operation: "publish", record_id: record.id, expected_revision: record.revision };
    const result = await call("experiment_review", args, !!confirmed);
    if (result.status === "confirmation_required") {
      setPublishPending({ record_id: record.id, args, reasons: (result.reasons as string[]) ?? [] });
      return;
    }
    setPublishPending(undefined);
    setNotice(t("Record \"{v0}\" published", { v0: record.name }));
    await refresh();
    await loadExperiment(record.id);
  });

  const unpublishRecord = (record: ExperimentItem) => perform(async () => {
    await call("experiment_review", { operation: "unpublish", record_id: record.id, expected_revision: record.revision });
    setNotice(t("Record \"{v0}\" is a draft again", { v0: record.name }));
    await refresh();
    await loadExperiment(record.id);
  });

  const deleteRecord = (record: ExperimentItem) => perform(async () => {
    await call("experiment_review", { operation: "delete", record_id: record.id });
    if (editingId === record.id) setEditingId("");
    setExpanded(previous => { const next = new Set(previous); next.delete(record.id); return next; });
    setNotice(t("Record deleted"));
    await refresh();
  });

  const openJob = (id: string) => perform(async () => {
    const result = await call("jobs", { job_id: id });
    setJobDetail({ job: result.job as Job, events: (result.events as JobEvent[]) ?? [] });
  });

  const saveSettings = (patch: Partial<Settings>) => perform(async () => {
    const result = await call("settings", patch);
    setSettings(result.settings as Settings);
    await refresh();
    await loadIndex();
  });

  // The semantic index behind graph search: loaded when Settings opens and after any
  // change to the embedding model, built on request so the first search is not slow.
  const loadIndex = useCallback(async () => {
    try { setIndexStatus(await call("graph_index", { operation: "status" }) as IndexStatus); }
    catch { setIndexStatus(undefined); }
  }, [call]);
  const buildIndex = () => perform(async () => {
    setIndexStatus(await call("graph_index", { operation: "build" }) as IndexStatus);
  });
  useEffect(() => {
    if (!deployed && (category === "settings" || isDetached("settings"))) void loadIndex();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [category, deployed, loadIndex]);

  // Engineering-only: a deployment has no writable config at all, so this never
  // runs there — the Settings section that calls it is not even mounted when deployed.
  const saveDefaultModel = (value: string) => perform(async () => {
    await host.updateConfig({ default_model: value });
  });

  const current = settings ?? { collection_name: "", pdf_engine: "auto", mineru_base_url: "", vision_model: "", embedding_model: "" };
  const currentSource = sources.find(item => item.id === selectedSource);
  const running = jobs.filter(job => ACTIVE.has(job.status));
  const groups = overview?.groups ?? [];
  const activeGroupObject = groups.find(item => item.id === activeGroup);
  const sourceJob = (id: string) => jobs.find(job => job.source_id === id);
  const sourceState = (item: Source) => {
    const job = sourceJob(item.id);
    return job && ACTIVE.has(job.status) ? "processing"
      : job && ["FAILED", "CANCELLED"].includes(job.status) ? "failed"
      : item.markdown && item.record_id ? "ready" : "pending";
  };
  const pendingSources = sources.filter(item => ["pending", "failed"].includes(sourceState(item)));
  const experimentSchemaIds = new Set(experimentSchemas.map(item => item.id));
  const schemaName = (id: string) => schemas.find(item => item.id === id)?.name ?? shortId(id);
  const schemaBundles = bundles.filter(item => !currentExperimentSchemaId || item.schema_id === currentExperimentSchemaId);
  const selectedBundle = schemaBundles.find(item => item.id === bundleId);
  // Records read from literature sources share the table; the Experiment tab shows its own schemas' only.
  const scopedRecords = experiments.filter(item => experimentSchemaIds.has(item.schema_id)
    && (!currentExperimentSchemaId || item.schema_id === currentExperimentSchemaId)
    && (!selectedBundle || item.bundle_id === selectedBundle.id));
  // The filter bar: status, then free text against everything a person might
  // remember about a record — its name, note, upload, schema, file names, and
  // every value inside its data.
  const query = recordQuery.trim().toLowerCase();
  const bundleById = new Map(bundles.map(item => [item.id, item]));
  const filteredRecords = scopedRecords.filter(item => {
    if (recordStatus && item.status !== recordStatus) return false;
    if (!query) return true;
    const bundle = item.bundle_id ? bundleById.get(item.bundle_id) : undefined;
    const haystack = [item.name, item.note ?? "", bundle?.name ?? "", schemaName(item.schema_id),
      ...(bundle?.files.map(file => file.filename) ?? []), ...flattenValues(item.data)].join("\n").toLowerCase();
    return haystack.includes(query);
  });
  // The grid shows one schema's columns when one is in view; across schemas, the
  // union of their properties so every record still has somewhere to put its values.
  const gridDefinition = (() => {
    const ids = Array.from(new Set(filteredRecords.map(item => item.schema_id)));
    if (ids.length === 1) return schemaDetails[ids[0]]?.definition as Record<string, unknown> | undefined;
    const properties: Record<string, unknown> = {};
    ids.forEach(id => Object.assign(properties, (schemaDetails[id]?.definition as { properties?: Record<string, unknown> } | undefined)?.properties ?? {}));
    return { properties };
  })();
  const runHint = canRunAgents
    ? t("Runs the connected {v0}, which reads the upload page by page and saves the records itself.", { v0: labAgents[0]?.name ?? "" })
    : t("Extraction runs as one model call through the selected model.");
  const extractBlocker = !canExtract
    ? (deployed ? t("This deployment has no default model configured yet.")
      : t("Configure a model connection in OAW Settings, or connect an Agent to this card with the Knowledge experiment relationship, to extract records."))
    : (extracting || selectedBundle?.status === "extracting") ? t("An extraction is already running.") : "";
  const literatureSchemas = schemas.filter(item => item.kind === "literature");
  const literatureRecords = experiments.filter(item => !experimentSchemaIds.has(item.schema_id));
  const convertedSources = sources.filter(item => item.markdown && item.record_id);
  const activeSection = category === "settings" ? "settings" : category === "literature" ? literatureTab : experimentTab;
  const knowledgeActive = activeSection === "knowledge" || isDetached("knowledge");
  useEffect(() => { if (knowledgeActive) setKnowledgeVisited(true); }, [knowledgeActive]);
  const publishedCounts = experiments.reduce<Record<string, number>>((counts, item) => {
    if (item.status === "published") counts[item.schema_id] = (counts[item.schema_id] ?? 0) + 1;
    return counts;
  }, {});

  const groupSelect = <label className="knowledge-groupselect">
    <span className="knowledge-visually-hidden">{t("Group")}</span>
    <select aria-label={t("Group")} value={activeGroup} disabled={busy}
      onChange={event => { setActiveGroup(event.target.value); setGroupEditing(false); setSelectedSources([]); setSuggestedSourceIds(undefined); setSearchResults(undefined); }}>
      <option value="">{t("All groups")}</option>
      {groups.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
    </select>
  </label>;

  const modelSelect = <label>{t("Model")}
    <select value={selectedModel} disabled={busy || !models.length} onChange={event => setModel(event.target.value)}>
      {!models.length && <option value="">{t("No model configured")}</option>}
      {models.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
    </select>
  </label>;

  const refreshControls = <>
      {!!running.length && <span className="knowledge-running" role="status">
        {t("{v0} conversion running", { v0: running.length })}</span>}
      <button type="button" className="knowledge-icon-button" aria-label={t("Refresh")} title={t("Refresh")}
        disabled={busy} onClick={() => void perform(async () => {
          setNotice(""); await refresh();
        })}><RefreshCw size={15} /></button>
  </>;

  return <div className="knowledge-app nodrag nowheel" aria-label={t("{v0} knowledge base", { v0: card.name })}>

    <header className="knowledge-navigation">
      <nav className="knowledge-workflows" aria-label={t("Workflow")}>
        <button type="button" aria-pressed={category === "literature"} onClick={() => setCategory("literature")}>
          <BookOpen size={16} />{t("Literature")}</button>
        <button type="button" aria-pressed={category === "experiment"} onClick={() => setCategory("experiment")}>
          <FlaskConical size={16} />{t("Experiment")}</button>
        {!deployed && <button type="button" aria-pressed={category === "settings"} onClick={() => setCategory("settings")}>
          <Settings2 size={16} />{t("Settings")}</button>}
      </nav>
        {category === "literature" && <div className="knowledge-tabstrip" role="tablist" aria-label={t("Literature sections")}>
          {LITERATURE_TABS.map(tab => <button key={tab.id} type="button" role="tab"
            aria-selected={literatureTab === tab.id} aria-pressed={literatureTab === tab.id}
            onClick={() => setLiteratureTab(tab.id)}>{tab.label()}</button>)}
        </div>}
        {category === "experiment" && <div className="knowledge-tabstrip" role="tablist" aria-label={t("Experiment sections")}>
          {EXPERIMENT_TABS.map(tab => <button key={tab.id} type="button" role="tab"
            aria-selected={experimentTab === tab.id} aria-pressed={experimentTab === tab.id}
            onClick={() => setExperimentTab(tab.id)}>{tab.label()}</button>)}
        </div>}
    </header>
    <div className="knowledge-grid">
      {!isInline(activeSection) && <WorkspaceEmptyState className="knowledge-section-placeholder" icon={<BookOpen size={24} />}
        title={isDetached(activeSection) ? t("Continue alongside this section") : t("Choose a section to continue")}
        description={isDetached(activeSection) ? t("This section is open in its own pane. Choose another to work alongside it.")
          : t("Use the navigation above to choose available content.")}
        actions={LITERATURE_TABS.filter(tab => isInline(tab.id)).map(tab => <button key={tab.id} type="button" onClick={() => {
          setCategory("literature"); setLiteratureTab(tab.id);
        }}>{tab.label()}</button>)} />}
      {!deployed && (category === "settings" || isDetached("settings")) && <WorkspaceSection id="settings" title={t("Settings")} className="knowledge-section knowledge-settings-section">
        {/* A detached section is portaled straight into its own tab, past ".knowledge-app": this
            wrapper carries the same classes so base colors and control styling still apply there. */}
        <div className="knowledge-section knowledge-settings-section" data-detached={isDetached("settings") || undefined}>
        <WorkspaceToolbar label={t("Settings")} className="knowledge-section-toolbar">{refreshControls}</WorkspaceToolbar>
        <Banner error={error} notice={notice} />
        <div className="knowledge-settings-layout">
        <section className="knowledge-settings-fields" aria-label={t("Documents")}>
        <h4>{t("Documents")}</h4>
        <label>{t("Collection name")}
          <input value={current.collection_name} disabled={busy}
            onChange={event => setSettings({ ...current, collection_name: event.target.value })}
            onBlur={event => void saveSettings({ collection_name: event.target.value })} />
        </label>
        <label>{t("PDF engine")}
          <select value={current.pdf_engine} disabled={busy}
            onChange={event => void saveSettings({ pdf_engine: event.target.value })}>
            {["auto", "pymupdf4llm", "mineru", "text"].map(value => <option key={value} value={value}>{value}</option>)}
          </select>
        </label>
        <label>{t("MinerU base URL")}
          <input value={current.mineru_base_url} disabled={busy} placeholder="https://mineru.net"
            onChange={event => setSettings({ ...current, mineru_base_url: event.target.value })}
            onBlur={event => void saveSettings({ mineru_base_url: event.target.value })} />
        </label>
        <MineruCredentials cardId={card.id} onSaved={refresh} />
        {/* Images (a photographed table, a chart) are transcribed by a vision-capable
            model the plugin calls from its own conversion job. The picker is the same
            catalog agents use; "" means the OAW default model. The OAW_VISION_*
            variables (card secret, global environment variables, process env) remain
            the fallback when no connection resolves. */}
        <div className="knowledge-agent-model knowledge-vision-model">
          <span>{t("Vision model for images")}</span>
          <ModelSelect label={t("Vision model for images")}
            value={current.vision_model || "oaw:default"}
            onChange={value => void saveSettings({ vision_model: value === "oaw:default" ? "" : value })} />
        </div>
        <p className="knowledge-meta">{(overview?.engines ?? []).includes("vision")
          ? t("Images convert through this model. Pick an OpenAI-compatible connection whose model can read images.")
          : t("No vision model resolves yet: choose one here, set a default model in OAW Settings, or add OAW_VISION_BASE_URL and OAW_VISION_API_KEY under Settings → Environment variables.")}</p>
        {/* Graph search's semantic channel. Unlike the vision model there is no default
            to fall back to — a chat model cannot embed — so "" means off and the graph
            is searched by words alone. */}
        <div className="knowledge-agent-model knowledge-vision-model">
          <span>{t("Embedding model for graph search")}</span>
          <ModelSelect label={t("Embedding model for graph search")} allowEmpty emptyLabel={t("Off — search by words only")}
            value={current.embedding_model ?? ""}
            onChange={value => void saveSettings({ embedding_model: value })} />
        </div>
        <p className="knowledge-meta">{t("Pick an OpenAI-compatible connection with an embedding model (one that serves /embeddings). Graph text — concept names, descriptions and relation statements — is sent to it when you search.")}</p>
        {current.embedding_model && <div className="knowledge-semantic-index" role="group" aria-label={t("Semantic index")}>
          <h5>{t("Semantic index")}</h5>
          {!indexStatus ? <p className="knowledge-meta">{t("Checking…")}</p> : <>
            <p className="knowledge-meta">{indexStatus.model_id
              ? t("{v0}: {v1} of {v2} graph elements embedded", { v0: indexStatus.model_id, v1: indexStatus.embedded, v2: indexStatus.total })
              : t("{v0} of {v1} graph elements embedded", { v0: indexStatus.embedded, v1: indexStatus.total })}
              {indexStatus.pending ? ` · ${t("{v0} pending", { v0: indexStatus.pending })}` : ""}
              {indexStatus.stale ? ` · ${t("{v0} stale", { v0: indexStatus.stale })}` : ""}</p>
            {indexStatus.last_error && <p className="knowledge-error" role="alert">{t("Last error: {v0}", { v0: indexStatus.last_error })}</p>}
            <button type="button" disabled={busy} onClick={() => void buildIndex()}>{t("Build index now")}</button>
            <p className="knowledge-meta">{t("Searches keep the index up to date on their own; building it here makes the first search fast and shows whether the model works.")}</p>
          </>}
        </div>}
        <details className="knowledge-disclosure"><summary>{t("Available engines")}</summary>
          <p>{(overview?.engines ?? []).join(", ") || "—"}</p></details>
        </section>
        <section className="knowledge-settings-fields" aria-label={t("Model")}>
        <h4>{t("Model")}</h4>
        <label>{t("Default model for deployment")}
          <select value={defaultModel} disabled={busy || !models.length}
            onChange={event => void saveDefaultModel(event.target.value)}>
            <option value="">{t("None chosen")}</option>
            {models.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
          </select>
        </label>

        <h4>{t("Connected agents")}</h4>
        {!connectedAgents.length
          ? <p className="knowledge-meta">{t("Connect an Agent on the canvas to choose its model.")}</p>
          : connectedAgents.map(agent => <div key={agent.id} className="knowledge-agent-model">
              <span>{agent.name}</span>
              <ModelSelect label={t("{v0}’s model", { v0: agent.name })}
                value={String(agent.config?.model ?? "oaw:default")}
                onChange={value => void updateCardConfig(agent.id, { model: value })} />
            </div>)}
        </section>
        </div>
        </div>
      </WorkspaceSection>}
      {((category === "literature" && literatureTab === "sources") || isDetached("sources")) && <WorkspaceSection id="sources" title={t("Sources")} layout={{ min_width: 320, min_height: 280, ideal_width: 920, ideal_height: 640 }} className="knowledge-section">
        <div className={`knowledge-section knowledge-sources${sourceDrag ? " is-file-over" : ""}`} data-detached={isDetached("sources") || undefined}
          onDragOver={event => { if (event.dataTransfer.types.includes("Files")) { event.preventDefault(); if (!busy) setSourceDrag(true); } }}
          onDragLeave={event => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setSourceDrag(false); }}
          onDrop={event => {
            const files = Array.from(event.dataTransfer.files ?? []);
            if (!files.length) return;
            event.preventDefault(); setSourceDrag(false);
            if (!busy) void upload(files);
          }}>
        <WorkspaceToolbar label={t("Documents")} className="knowledge-section-toolbar">
          <span className="knowledge-meta">{sources.length === 1 ? t("1 document") : t("{v0} documents", { v0: sources.length })}</span>
          {!!running.length && <span className="knowledge-running" role="status">
            {t("{v0} conversion running", { v0: running.length })}</span>}
          <div className="knowledge-heading-actions">
          <button type="button" className="knowledge-icon-button" aria-label={t("Refresh")} title={t("Refresh")}
            disabled={busy} onClick={() => void perform(async () => { setNotice(""); await refresh(); })}>
            <RefreshCw size={15} /></button>
          </div>
        </WorkspaceToolbar>
        <Banner error={error} notice={notice} />
        <div className="knowledge-source-toolbar">
        <div className="knowledge-document-controls">
          {groupSelect}
          <details className="knowledge-group-menu">
            <summary aria-label={t("Manage groups")} title={t("Manage groups")}><MoreHorizontal size={18} /></summary>
            <div className="knowledge-group-actions">
              <button type="button" disabled={busy} onClick={event => {
                setGroupName(""); setGroupCreating(true); setGroupEditing(true);
                event.currentTarget.closest("details")?.removeAttribute("open");
              }}>{t("New group")}</button>
              {activeGroupObject && <button type="button" disabled={busy} onClick={event => {
                setGroupName(activeGroupObject.name); setGroupCreating(false); setGroupEditing(true);
                event.currentTarget.closest("details")?.removeAttribute("open");
              }}>{t("Rename group")}</button>}
              {activeGroupObject && !activeGroupObject.is_default && <button type="button" disabled={busy}
                onClick={() => void deleteGroup()}>{t("Delete group")}</button>}
            </div>
          </details>
        </div>
        {groupEditing && <div className="knowledge-formbar">
          <input value={groupName} disabled={busy} placeholder={t("Group name")} aria-label={t("Group name")} autoFocus
            onChange={event => setGroupName(event.target.value)} />
          <button type="button" className="knowledge-primary" disabled={busy || !groupName.trim()}
            onClick={() => void (groupCreating ? createGroup() : renameGroup())}>
            {groupCreating ? t("Create") : t("Save")}</button>
          <button type="button" disabled={busy} onClick={() => setGroupEditing(false)}>{t("Cancel")}</button>
        </div>}
        {/* Folded in from what used to be its own "Search" tab: Sources already
            has almost every source-level action, so full-text search over the
            converted documents belongs right here too, not behind another tab. */}
        {!!sources.length && <div className="knowledge-search-inline">
          <div className="knowledge-formbar">
            <input type="search" value={searchQuery} disabled={busy} aria-label={t("Search converted documents")} placeholder={t("Search converted documents…")}
              onChange={event => setSearchQuery(event.target.value)}
              onKeyDown={event => { if (event.key === "Enter") void runSearch(); }} />
            <button type="button" disabled={busy || !searchQuery.trim()}
              onClick={() => void runSearch()}>{t("Search")}</button>
          </div>
          {searchError && <p role="alert" className="knowledge-error">{searchError}</p>}
          {searchResults && !searchResults.length && <p className="knowledge-empty">{t("No matches.")}</p>}
          {!!searchResults?.length && <ul className="knowledge-list">
            {searchResults.map((item, index) => <li key={index}>
              <button type="button" onClick={() => viewSource(item.source_id)}>
                <span>{item.filename ?? shortId(item.source_id)}{item.group_name ? ` · ${item.group_name}` : ""}</span>
                <small>{item.heading_path || t("(no heading)")} · {item.excerpt.slice(0, 160)}</small>
              </button>
            </li>)}
          </ul>}
        </div>}
        </div>

        <div className={`knowledge-source-dropzone${sources.length ? " is-compact" : ""}`} aria-busy={!!uploadProgress}>
          {!sources.length && <div className="knowledge-file-illustration" aria-hidden="true">
            <span><Table2 size={28} /></span><span><FileText size={34} /></span><span><BookOpen size={28} /></span>
          </div>}
          <div className="knowledge-source-intro">
            {sources.length ? <strong><Upload size={18} />{t("Drop documents here")}</strong>
              : <><h3>{t("Add your first document")}</h3><p>{t("Papers, notes, spreadsheets or images · up to 32 MiB each")}</p></>}
          </div>
          <label className={`knowledge-upload${busy ? " is-disabled" : ""}`}>
            <Plus size={16} />{t("Add documents")}
            <input className="knowledge-visually-hidden" type="file" multiple disabled={busy}
              aria-label={t("Add documents")} title={t("Up to 32 MiB per file")}
              onChange={event => {
                const files = Array.from(event.target.files ?? []);
                event.target.value = "";
                if (files.length) void upload(files);
              }} />
          </label>
          {!sources.length && <span className="knowledge-meta">{t("Or drop files here · conversion starts automatically")}</span>}
          {uploadProgress && <div className="knowledge-upload-progress" role="status">
            <span>{t("Preparing {v0} of {v1} files", { v0: uploadProgress.done, v1: uploadProgress.total })}</span>
            <progress aria-label={t("Upload progress")} value={uploadProgress.done} max={uploadProgress.total} />
          </div>}
        </div>
        {!!convertedSources.length && !currentSource && <div className="knowledge-source-goals" role="group" aria-label={t("Use your documents")}>
          <button type="button" onClick={() => viewSource(convertedSources.find(item => selectedSources.includes(item.id))?.id ?? convertedSources[0].id)}>
            <BookOpen size={22} /><span><strong>{t("Read documents")}</strong><small>{t("Open the full text")}</small></span><ArrowRight size={16} />
          </button>
          <button type="button" onClick={() => {
            const selected = convertedSources.filter(item => selectedSources.includes(item.id));
            // With no explicit selection, continue the workspace instead of silently
            // starting another draft over every document in the collection.
            setSuggestedSourceIds(selected.length ? selected.map(item => item.id) : undefined);
            setLiteratureTab("knowledge"); setCategory("literature");
          }}><Network size={22} /><span><strong>{t("Build knowledge")}</strong><small>{t("Connect ideas across sources")}</small></span><ArrowRight size={16} /></button>
          <button type="button" onClick={() => { setLiteratureTab("records"); setCategory("literature"); }}>
            <Table2 size={22} /><span><strong>{t("Extract records")}</strong><small>{t("Compare structured data")}</small></span><ArrowRight size={16} />
          </button>
        </div>}

        <div className={`knowledge-document-layout${currentSource ? " has-document" : ""}`}>
        <div className="knowledge-source-pane">
        <ul className="knowledge-list knowledge-source-list">
          {sources.map(item => <li key={item.id} className="knowledge-checkrow" data-state={sourceState(item)}>
            <label><input type="checkbox" checked={selectedSources.includes(item.id)} disabled={busy}
              onChange={event => setSelectedSources(event.target.checked
                ? [...selectedSources, item.id] : selectedSources.filter(value => value !== item.id))} />
              <span className="knowledge-visually-hidden">{t("Select {v0}", { v0: item.filename })}</span>
            </label>
            <button type="button" aria-pressed={selectedSource === item.id}
              onClick={() => viewSource(item.id)}>
              <span className="knowledge-source-name" title={item.filename}><FileText size={17} />{item.filename}</span>
              <small className="knowledge-source-state">
                {sourceState(item) === "ready" ? <CheckCircle2 size={13} /> : sourceState(item) === "processing" ? <LoaderCircle size={13} className="knowledge-spin" /> : sourceState(item) === "failed" ? <CircleAlert size={13} /> : <Upload size={13} />}
                {bytes(item.size)} · {sourceState(item) === "ready" ? t("Ready") : sourceState(item) === "processing" ? t("Converting…") : sourceState(item) === "failed" ? t("Needs attention") : t("awaiting conversion")}
                {!activeGroup && item.group_name && groups.length > 1 ? ` · ${item.group_name}` : ""}</small>
            </button>
            {sourceState(item) === "failed" && <button type="button" className="knowledge-source-retry" disabled={busy}
              aria-label={t("Retry {v0}", { v0: item.filename })} onClick={() => void processSources([item.id])}><RefreshCw size={14} />{t("Retry")}</button>}
          </li>)}
        </ul>
        {(selectedSources.length > 0 || pendingSources.length > 0) && <div className="knowledge-formbar knowledge-batchbar">
          {selectedSources.length > 0 && <button type="button" className="knowledge-primary" disabled={busy}
            onClick={() => void processSources(selectedSources)}>
            {t("Process {v0} selected", { v0: selectedSources.length })}</button>}
          {pendingSources.length > 0 && <button type="button" disabled={busy}
            onClick={() => void processPending()}>
            {t("Process all pending ({v0})", { v0: pendingSources.length })}</button>}
        </div>}
        </div>

        {currentSource && <div className="knowledge-detail knowledge-reader">
          <div className="knowledge-reader-heading">
            <h4>{currentSource.filename}</h4>
            <button type="button" className="knowledge-icon-button" aria-label={t("Close document")} title={t("Close document")}
              onClick={() => setSelectedSource("")}><X size={16} /></button>
          </div>
          {!currentSource.record_id ? <WorkspaceEmptyState
            icon={sourceState(currentSource) === "processing" ? <LoaderCircle className="knowledge-spin" size={24} /> : <FileText size={24} />}
            busy={sourceState(currentSource) === "processing"}
            title={sourceState(currentSource) === "processing" ? t("Preparing your document") : t("Document needs conversion")}
            description={sourceState(currentSource) === "processing" ? t("The full text will appear here when it is ready.") : sourceJob(currentSource.id)?.error}
            actions={sourceState(currentSource) !== "processing" && <button type="button" className="knowledge-primary" disabled={busy}
              onClick={() => void processSources([currentSource.id])}>{t("Convert document")}</button>} />
            : documentError ? <p role="alert" className="knowledge-error">{documentError}</p>
            : !extracted ? <p role="status">{t("Loading markdown…")}</p> : <>
            <div className="knowledge-reading-toolbar">
              <span className="knowledge-meta">{t("{v0} characters", { v0: extracted.total_characters })}</span>
              <button type="button" aria-pressed={rawMarkdown} onClick={() => setRawMarkdown(!rawMarkdown)}>{t("Markdown")}</button>
            </div>
            {rawMarkdown ? <pre className="knowledge-markdown">{extracted.markdown}</pre>
              : <article className="knowledge-prose"><DocumentMarkdown content={extracted.markdown} /></article>}
            {extracted.has_more && <button type="button" disabled={busy} onClick={() => void extend()}>{t("Load more")}</button>}
          </>}
        </div>}

        </div>
        {!!jobs.length && <details className="knowledge-disclosure knowledge-jobs">
        <summary>{t("Conversion jobs")} <span className="knowledge-total">{jobs.length}</span></summary>
        <ul className="knowledge-list">
          {jobs.map(job => <li key={job.id}>
            <button type="button" aria-pressed={jobDetail?.job.id === job.id} disabled={busy}
              onClick={() => void openJob(job.id)}>
              <span>{shortId(job.id)} · {job.status}</span>
              <small>{job.error || job.message || job.kind}</small>
            </button>
          </li>)}
        </ul>
        {jobDetail && <div className="knowledge-detail">
          <h4>{t("Job {v0}", { v0: shortId(jobDetail.job.id) })}</h4>
          {jobDetail.job.error && <p role="alert" className="knowledge-error">{jobDetail.job.error}</p>}
          <ul className="knowledge-events">
            {jobDetail.events.map((event, index) => <li key={index}>
              <code>{event.step ?? event.event}</code> {event.message}
            </li>)}
          </ul>
        </div>}
        </details>}
        </div>
      </WorkspaceSection>}

      {((category === "experiment" && experimentTab === "schemas") || isDetached("schemas")) && <WorkspaceSection id="schemas" title={t("Schemas")} className="knowledge-section">
        <div className="knowledge-section" data-detached={isDetached("schemas") || undefined}>
        <WorkspaceToolbar label={t("Schemas")} className="knowledge-section-toolbar">
          <span className="knowledge-meta">{t("Experiment schemas: the fields each specimen record carries")}</span>
          <div className="knowledge-heading-actions">{refreshControls}</div>
        </WorkspaceToolbar>
        <Banner error={error} notice={notice} />
        <SchemaEditor busy={busy} schemas={experimentSchemas} kind="experiment" call={call} perform={perform}
          onSaved={async saved => {
            // The experiment tables and forms read definitions from a cache; an edit refills it.
            setSchemaDetails(previous => { const next = { ...previous }; delete next[saved.id]; return next; });
            await refresh();
          }} />
        </div>
      </WorkspaceSection>}

      {(knowledgeActive || knowledgeVisited) && <WorkspaceSection id="knowledge" title={t("Knowledge")} layout={{ min_width: 320, min_height: 320, ideal_width: 960, ideal_height: 640 }} className="knowledge-section knowledge-wide"
        style={knowledgeActive ? undefined : { display: "none" }}>
        <div className="knowledge-section knowledge-wide" data-detached={isDetached("knowledge") || undefined}>
          <KnowledgeSection host={host} cardId={card.id} deployed={deployed} busy={busy} version={refreshVersion}
            sources={convertedSources} activeGroup={activeGroup} groupCount={groups.length}
            active={knowledgeActive} suggestedSourceIds={suggestedSourceIds} onSourcesHandled={consumeSourceSuggestion}
            onAddSources={() => { setCategory("literature"); setLiteratureTab("sources"); }}
            groupArgs={groupArgs} groupFilter={groupFilter} call={call} perform={perform} request={request}
            extractAgent={extractAgents[0]} canRunAgent={!deployed && typeof host.runAgent === "function" && extractAgents.length > 0}
            selectedModel={selectedModel} modelSelect={modelSelect} groupSelect={groupSelect} refreshControls={refreshControls}
            banner={<Banner error={error} notice={notice} />} setNotice={setNotice} setError={setError} onOpenSource={viewSource} />
        </div>
      </WorkspaceSection>}

      {((category === "literature" && literatureTab === "records") || isDetached("records")) && <WorkspaceSection id="records" title={t("Records")} layout={{ min_width: 320, min_height: 320, ideal_width: 960, ideal_height: 640 }} className="knowledge-section knowledge-wide">
        <div className="knowledge-section knowledge-wide" data-detached={isDetached("records") || undefined}>
          <RecordsSection host={host} cardId={card.id} deployed={deployed} busy={busy} version={refreshVersion}
            schemas={literatureSchemas} records={literatureRecords} sources={convertedSources}
            schemaId={literatureSchemas.some(item => item.id === literatureSchemaId) ? literatureSchemaId : ""} onSchemaChange={setLiteratureSchemaId}
            activeGroup={activeGroup} groupCount={groups.length} groupArgs={groupArgs} groupFilter={groupFilter}
            call={call} perform={perform} request={request} refresh={refresh}
            extractAgent={extractAgents[0]} labAgent={labAgents[0]} selectedModel={selectedModel}
            modelSelect={modelSelect} groupSelect={groupSelect} refreshControls={refreshControls}
            banner={<Banner error={error} notice={notice} />} setNotice={setNotice} setError={setError}
            onOpenSource={viewSource} onAddSources={() => { setCategory("literature"); setLiteratureTab("sources"); }} kindLabel={kind => KIND_LABELS[kind]()}
            focusRecordId={focusRecordId} onFocused={() => setFocusRecordId("")} />
        </div>
      </WorkspaceSection>}

      {((category === "experiment" && experimentTab === "experiments") || isDetached("experiments")) && <WorkspaceSection id="experiments" title={t("Experiments")} layout={{ min_width: 320, min_height: 320, ideal_width: 960, ideal_height: 640 }} className="knowledge-section">
        <div className="knowledge-section" data-detached={isDetached("experiments") || undefined}>
        <WorkspaceToolbar label={t("Experiments")} className="knowledge-section-toolbar">
          {groupSelect}
          <label className="knowledge-groupselect">
            <span className="knowledge-visually-hidden">{t("Experiment schema")}</span>
            <select aria-label={t("Experiment schema")} value={currentExperimentSchemaId} disabled={busy || !experimentSchemas.length}
              onChange={event => { setExperimentSchemaId(event.target.value); setBundleId(""); setEditingId(""); }}>
              {!experimentSchemas.length
                ? <option value="">{t("No experiment schema yet")}</option>
                : <option value="">{t("All schemas")}</option>}
              {experimentSchemas.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
          </label>
          <div className="knowledge-heading-actions">{refreshControls}
            <button type="button" disabled={busy} onClick={() => { setCategory("experiment"); setExperimentTab("schemas"); }}>
              <Plus size={14} />{t("New schema")}</button>
          </div>
        </WorkspaceToolbar>
        <Banner error={error} notice={notice} />
        {!experimentSchemas.length && <div className="knowledge-experiment-start">
          <div className="knowledge-experiment-route" aria-hidden="true"><Table2 size={26} /><ArrowRight size={18} /><Upload size={26} /><ArrowRight size={18} /><CheckCircle2 size={26} /></div>
          <WorkspaceEmptyState title={t("What will you measure?")}
            description={t("Choose the fields for your records, then add an experiment's files together.")}
            actions={<button type="button" className="knowledge-primary" onClick={() => { setCategory("experiment"); setExperimentTab("schemas"); }}><Plus size={16} />{t("Define record fields")}</button>} />
        </div>}
        {!!experimentSchemas.length && <div className="knowledge-bundles"
          onDragOver={event => { if (event.dataTransfer.types.includes("Files")) event.preventDefault(); }}
          onDrop={event => {
            const files = Array.from(event.dataTransfer.files ?? []);
            if (!files.length) return;
            event.preventDefault();
            if (busy) return;
            if (selectedBundle) void uploadToBundle(selectedBundle, files); else void uploadNewBundle(files);
          }}>
          {/* The drop zone is always here, between the toolbar and the uploads, so the
              way files come in never moves: tall and explanatory before the first upload,
              a one-line strip once there are uploads to show below it. */}
          <div className={`knowledge-dropzone${schemaBundles.length ? " is-compact" : ""}`}>
            <Upload size={schemaBundles.length ? 16 : 22} />
            <strong>{selectedBundle
              ? t("Drop files here to add them to \"{v0}\"", { v0: selectedBundle.name })
              : t("Drop an experiment's files here")}</strong>
            {!schemaBundles.length && <p>{t("Add the files of one experiment together. Conversion starts automatically.")}</p>}
            <label className={`knowledge-upload${busy ? " is-disabled" : ""}`}>
              <Upload size={14} />{schemaBundles.length ? t("New upload from files") : t("Choose files")}
              <input className="knowledge-visually-hidden" type="file" multiple disabled={busy}
                aria-label={t("Choose files for a new experiment upload")}
                onChange={event => {
                  const files = Array.from(event.target.files ?? []);
                  event.target.value = "";
                  if (files.length) void uploadNewBundle(files);
                }} />
            </label>
          </div>
          {!!schemaBundles.length && <div className="knowledge-bundle-chips" role="tablist" aria-label={t("Uploads")}>
            <button type="button" role="tab" aria-selected={!selectedBundle} onClick={() => setBundleId("")}>{t("All records")}
              <span className="knowledge-total">{experiments.filter(item => !currentExperimentSchemaId || item.schema_id === currentExperimentSchemaId).length}</span></button>
            {schemaBundles.map(item => <button key={item.id} type="button" role="tab" aria-selected={selectedBundle?.id === item.id}
              title={item.notes || undefined} onClick={() => setBundleId(item.id)}>{item.name}
              <span className="knowledge-total">{item.records}</span>
              {!currentExperimentSchemaId && <span className="knowledge-chip-schema">{schemaName(item.schema_id)}</span>}
              {(item.status === "extracting" || extracting?.bundle_id === item.id) && <span className="knowledge-running">{t("extracting…")}</span>}
              {item.status === "failed" && <span className="knowledge-error">{t("failed")}</span>}
            </button>)}
          </div>}
          {selectedBundle && <details className="knowledge-disclosure knowledge-workflow knowledge-bundle" open>
            <summary>{t("Files in \"{v0}\"", { v0: selectedBundle.name })} <span className="knowledge-total">{selectedBundle.files.length}</span></summary>
            <div className="knowledge-bundle-meta">
              <span className="knowledge-badge">{schemaName(selectedBundle.schema_id)}</span>
              <p className="knowledge-meta">{selectedBundle.notes || t("No description yet — add units, what the files show, which specimen a photo is of.")}</p>
              <button type="button" className="knowledge-icon-button" disabled={busy || !!extracting} aria-label={t("Edit upload details")}
                title={t("Edit upload details")} onClick={() => setDialog({ mode: "edit" })}><Pencil size={14} /></button>
            </div>
            {!selectedBundle.files.length
              ? <p className="knowledge-empty">{t("Add the files that describe this experiment: images, spreadsheets, PDFs, text. Each converts as soon as it is uploaded.")}</p>
              : <ul className="knowledge-list knowledge-bundle-files">
                {selectedBundle.files.map(file => <li key={file.source_id} className="knowledge-bundle-file">
                  <FileText size={14} />
                  <span className="knowledge-checkrow-label"><span>{file.filename}</span>
                    <small>{bytes(file.size)} · {file.state === "converted" ? t("converted") : file.state === "converting" ? t("converting…")
                      : file.state === "failed" ? (file.error || t("conversion failed")) : t("waiting")}</small></span>
                  {file.job_id && file.state === "failed" && <button type="button" disabled={busy}
                    onClick={() => void openJob(file.job_id!)}>{t("Why?")}</button>}
                </li>)}
              </ul>}
            <div className="knowledge-formbar">
              <label className={`knowledge-upload${busy ? " is-disabled" : ""}`}>
                <Plus size={16} />{t("Add files")}
                <input className="knowledge-visually-hidden" type="file" multiple disabled={busy}
                  aria-label={t("Add files to {v0}", { v0: selectedBundle.name })} title={t("Up to 32 MiB per file")}
                  onChange={event => {
                    const files = Array.from(event.target.files ?? []);
                    event.target.value = "";
                    if (files.length) void uploadToBundle(selectedBundle, files);
                  }} />
              </label>
              <button type="button" className="knowledge-primary"
                disabled={busy || !canExtract || !!extracting || selectedBundle.status === "extracting"
                  || !selectedBundle.files.length || selectedBundle.files.some(file => file.state !== "converted")}
                title={runHint}
                onClick={() => setDialog({ mode: "extract" })}>
                {selectedBundle.records > 0 ? t("Re-extract records") : t("Extract records")}</button>
              {(extracting?.bundle_id === selectedBundle.id || selectedBundle.status === "extracting") && !deployed &&
                <button type="button" className="knowledge-danger" disabled={stopping}
                  onClick={() => stopExtraction(selectedBundle)}><Square size={13} /> {stopping ? t("Stopping…") : t("Stop")}</button>}
              {!deployed && selectedBundle.run_id && <button type="button" onClick={() => setRunLogOpen(true)}>
                <ScrollText size={13} /> {t("View log")}</button>}
              <button type="button" disabled={busy || !!extracting} onClick={() => void deleteBundle(selectedBundle)}>{t("Remove upload")}</button>
            </div>
            {!canExtract && <p className="knowledge-meta">{deployed
              ? t("This deployment has no default model configured yet.")
              : t("Configure a model connection in OAW Settings, or connect an Agent to this card with the Knowledge experiment relationship, to extract records.")}</p>}
            {canExtract && !canRunAgents && !deployed && <p className="knowledge-meta">
              {t("Extraction runs as one model call. Connect a Lab assistant (Knowledge experiment relationship) to have an agent read long uploads page by page and fix records in conversation.")}</p>}
            {canExtract && selectedBundle.files.some(file => file.state !== "converted") && !!selectedBundle.files.length &&
              <p className="knowledge-meta">{t("Extraction starts once every file is converted.")}</p>}
            {jobDetail && selectedBundle.files.some(file => file.job_id === jobDetail.job.id) && <div className="knowledge-detail">
              {jobDetail.job.error && <p role="alert" className="knowledge-error">{jobDetail.job.error}</p>}
              <ul className="knowledge-events">
                {jobDetail.events.map((event, index) => <li key={index}><code>{event.step ?? event.event}</code> {event.message}</li>)}
              </ul>
            </div>}
            {!deployed && selectedBundle.status === "failed" && runLog?.run.run_id === selectedBundle.run_id && <div role="alert" className="knowledge-confirm knowledge-runlog">
              <strong>{t("The last extraction failed")}</strong>
              {runLog.run.error && <p className="knowledge-error">{runLog.run.error}</p>}
              {(runLog.run.lifecycle?.model_requests ?? []).some(item => item.error_type) && <p>
                {t("A model request was interrupted mid-stream ({v0}). This is the model connection, not the upload: check the connection in OAW Settings → Models, or extract without the agent for a single non-streaming call.", {
                  v0: (runLog.run.lifecycle?.model_requests ?? []).filter(item => item.error_type).map(item => `#${item.model_request} ${item.error_type}`).join(", ") })}</p>}
              <button type="button" onClick={() => setRunLogOpen(true)}><ScrollText size={13} /> {t("View log")}</button>
            </div>}
            {runLogOpen && selectedBundle.run_id && <Modal title={t("Extraction log · {v0}", { v0: selectedBundle.name })}
              className="knowledge-modal knowledge-runlog-modal" onClose={() => setRunLogOpen(false)}
              actions={<>
                {(extracting?.bundle_id === selectedBundle.id || selectedBundle.status === "extracting") &&
                  <button type="button" className="knowledge-danger" disabled={stopping}
                    onClick={() => stopExtraction(selectedBundle)}><Square size={13} /> {stopping ? t("Stopping…") : t("Stop extraction")}</button>}
                <button type="button" onClick={() => void loadRunLog(selectedBundle.run_id!)}><RefreshCw size={13} /> {t("Refresh")}</button>
                {labAgents[0] && <button type="button" onClick={() => { setRunLogOpen(false); host.openWorkspace(labAgents[0].id); }}>{t("Open {v0}", { v0: labAgents[0].name })}</button>}
              </>}>
              <p className="knowledge-meta">{t("run {v0}", { v0: shortId(selectedBundle.run_id) })}
                {runLog?.run.run_id === selectedBundle.run_id && <> · {t("Status: {v0}", { v0: runLog.run.status })}
                  {runLog.run.finished_at ? ` · ${new Date(runLog.run.finished_at).toLocaleTimeString()}` : (extracting || selectedBundle.status === "extracting") ? ` · ${t("live")}` : ""}</>}</p>
              {runLogError && <p role="alert" className="knowledge-error">{runLogError}</p>}
              {!runLog && !runLogError && <p role="status" className="knowledge-meta">{t("Loading run…")}</p>}
              {runLog && runLog.run.run_id === selectedBundle.run_id && <>
                {runLog.run.error && <p role="alert" className="knowledge-error">{runLog.run.error}</p>}
                {!!(runLog.run.lifecycle?.model_requests ?? []).length && <ul className="knowledge-relations">
                  {(runLog.run.lifecycle?.model_requests ?? []).map(item => <li key={item.model_request}>
                    <code>{t("model request {v0}", { v0: item.model_request })}</code>{" "}
                    {item.error_type ? `${t("interrupted")} (${item.error_type})`
                      : item.finish_reason ? `${item.finish_reason}${item.elapsed_ms ? ` · ${(item.elapsed_ms / 1000).toFixed(1)}s` : ""}`
                      : t("in progress")}
                  </li>)}
                </ul>}
                <ul className="knowledge-events knowledge-runlog-events">
                  {runLog.activity.map(item => <li key={item.id}>
                    <small>{new Date(item.timestamp).toLocaleTimeString()}</small>{" "}
                    <code>{item.type === "tool_completed" ? (item.name ?? "tool") : item.type}</code>{" "}
                    {item.type === "tool_completed"
                      ? (item.interrupted ? t("interrupted") : item.success === false ? t("failed") : t("completed"))
                      : (item.text ?? "")}
                  </li>)}
                  {!runLog.activity.length && <li className="knowledge-meta">{t("No activity recorded yet")}</li>}
                </ul>
              </>}
            </Modal>}
          </details>}
          {!!schemaBundles.length && <div className="knowledge-filterbar" role="search">
            <label className="knowledge-filter-search">
              <span className="knowledge-visually-hidden">{t("Search records")}</span>
              <Search size={14} aria-hidden />
              <input type="search" value={recordQuery} placeholder={t("Search records: name, value, file, note…")}
                aria-label={t("Search records")} onChange={event => setRecordQuery(event.target.value)} />
            </label>
            <label className="knowledge-groupselect">
              <span className="knowledge-visually-hidden">{t("Status")}</span>
              <select aria-label={t("Status")} value={recordStatus} onChange={event => setRecordStatus(event.target.value as "" | "draft" | "published")}>
                <option value="">{t("All statuses")}</option>
                <option value="draft">{t("Draft")}</option>
                <option value="published">{t("Published")}</option>
              </select>
            </label>
            <span className="knowledge-meta knowledge-filter-count" role="status">
              {filteredRecords.length === scopedRecords.length
                ? t("{v0} record(s)", { v0: scopedRecords.length })
                : t("{v0} of {v1} record(s)", { v0: filteredRecords.length, v1: scopedRecords.length })}</span>
            {(query || recordStatus) && <button type="button" onClick={() => { setRecordQuery(""); setRecordStatus(""); }}><X size={13} />{t("Clear")}</button>}
            <div className="knowledge-view-toggle" role="group" aria-label={t("View")}>
              <button type="button" aria-pressed={recordView === "list"} title={t("List")} aria-label={t("List")} onClick={() => setRecordView("list")}><List size={14} /></button>
              <button type="button" aria-pressed={recordView === "grid"} title={t("Table")} aria-label={t("Table")} onClick={() => setRecordView("grid")}><Table2 size={14} /></button>
            </div>
          </div>}
          {!scopedRecords.length && !!schemaBundles.length && <WorkspaceEmptyState title={t("No records yet")}
            description={selectedBundle ? t("Add this upload's files, then extract records from them.")
              : t("Choose an upload above to add files and extract records.")} />}
          {!!scopedRecords.length && !filteredRecords.length && <p className="knowledge-empty">{t("No records match the current filters.")}</p>}
          {recordView === "grid" && !!filteredRecords.length && <RecordGrid definition={gridDefinition} showBundle={!selectedBundle}
            onOpen={openRecord} records={filteredRecords.map(item => ({ id: item.id, name: item.name, status: item.status, data: item.data,
              validation: item.validation, updated_at: item.updated_at,
              bundle: item.bundle_id ? (bundleById.get(item.bundle_id)?.name ?? shortId(item.bundle_id)) : undefined }))} />}
          {recordView === "list" && !!filteredRecords.length && <ul className="knowledge-record-rows">
            {filteredRecords.map(item => {
              const open = expanded.has(item.id);
              const detail = recordDetails[item.id];
              const record = detail && detail.revision >= item.revision ? detail : item;
              const definition = schemaDetails[item.schema_id]?.definition;
              const editing = editingId === item.id;
              const bundle = item.bundle_id ? bundleById.get(item.bundle_id) : undefined;
              return <li key={item.id} className={`knowledge-record-row${open ? " is-open" : ""}`}>
                <button type="button" className="knowledge-record-summary" aria-expanded={open} onClick={() => toggleRecord(item.id)}>
                  {open ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
                  <span className="knowledge-record-title">{item.name}</span>
                  <span className={`knowledge-badge knowledge-status-${item.status}`}>{item.status === "published" ? t("Published") : t("Draft")}</span>
                  {item.validation?.valid === false && <span className="knowledge-error">{t("does not satisfy the schema")}</span>}
                  <small className="knowledge-meta">
                    {!selectedBundle && bundle ? `${bundle.name} · ` : ""}
                    {!currentExperimentSchemaId ? `${schemaName(item.schema_id)} · ` : ""}
                    {new Date(item.updated_at).toLocaleString()}</small>
                </button>
                {open && <div className="knowledge-record-body">
                  {record.validation?.valid === false && <p role="alert" className="knowledge-error">
                    {(record.validation.errors ?? record.validation.missing ?? []).join("; ") || t("Does not satisfy the schema")}</p>}
                  {editing
                    ? <>
                      <div className="knowledge-reading-toolbar">
                        <label className="knowledge-record-field knowledge-record-name">{t("Name")}<input value={recordName} disabled={busy}
                          onChange={event => setRecordName(event.target.value)} /></label>
                        <button type="button" aria-pressed={rawRecord} disabled={busy}
                          onClick={() => { if (!rawRecord) setRecordJson(pretty(recordDraft)); else { try { setRecordDraft(JSON.parse(recordJson)); } catch { /* keep the form's last good value */ } } setRawRecord(!rawRecord); }}>{t("JSON")}</button>
                      </div>
                      {rawRecord
                        ? <label className="knowledge-record-field">{t("Data (JSON)")}<textarea value={recordJson} disabled={busy} rows={10}
                            spellCheck={false} onChange={event => setRecordJson(event.target.value)} /></label>
                        : definition
                          ? <RecordForm definition={definition} value={recordDraft} disabled={busy} onChange={setRecordDraft} />
                          : <p role="status">{t("Loading schema…")}</p>}
                      <label className="knowledge-record-field">{t("Note")}<input value={recordNote} disabled={busy}
                        placeholder={t("Where it came from, what was unclear…")} onChange={event => setRecordNote(event.target.value)} /></label>
                      <p className="knowledge-meta">{t("You can also ask the Lab assistant to change this record in the conversation — refer to it as \"{v0}\".", { v0: record.name })}</p>
                      <div className="knowledge-formbar">
                        <button type="button" className="knowledge-primary" disabled={busy} onClick={() => void saveRecordEdits(record)}>{t("Save changes")}</button>
                        <button type="button" disabled={busy} onClick={() => setEditingId("")}>{t("Cancel")}</button>
                      </div>
                    </>
                    : <>
                      <RecordTable definition={definition} data={record.data} validation={record.validation} />
                      <p className="knowledge-meta">{t("revision {v0} · from {v1}", { v0: record.revision,
                        v1: detail ? (detail.sources.map(link => link.filename ?? shortId(link.source_id)).join(", ") || "—") : "…" })}
                        {record.note ? ` · ${record.note}` : ""}</p>
                      {publishPending?.record_id === item.id ? <div role="alert" className="knowledge-confirm">
                        <strong>{t("Publish this record?")}</strong>
                        <p>{publishPending.reasons.join(" ")}</p>
                        <div className="knowledge-formbar">
                          <button type="button" className="knowledge-primary" disabled={busy}
                            onClick={() => void publishRecord(record, publishPending)}>{t("Confirm and publish")}</button>
                          <button type="button" disabled={busy} onClick={() => setPublishPending(undefined)}>{t("Cancel")}</button>
                        </div>
                      </div> : <div className="knowledge-formbar">
                        {record.status === "published"
                          ? <button type="button" disabled={busy} onClick={() => void unpublishRecord(record)}>{t("Unpublish")}</button>
                          : <>
                            <button type="button" disabled={busy} onClick={() => beginEdit(record)}><Pencil size={13} />{t("Edit")}</button>
                            <button type="button" className="knowledge-primary" disabled={busy || record.validation?.valid === false}
                              onClick={() => void publishRecord(record)}>{t("Publish")}</button>
                            <button type="button" className="knowledge-danger" disabled={busy} onClick={() => void deleteRecord(record)}>{t("Delete")}</button>
                          </>}
                      </div>}
                    </>}
                </div>}
              </li>;
            })}
          </ul>}
          {dialog && selectedBundle && <ExtractDialog key={`${selectedBundle.id}:${dialog.mode}`} bundle={selectedBundle} mode={dialog.mode}
            schemas={experimentSchemas} busy={busy} canExtract={canExtract} runHint={runHint} blocker={extractBlocker}
            onClose={() => setDialog(undefined)} onConfirm={details => void confirmDialog(selectedBundle, dialog.mode, details)} />}
        </div>}
        </div>
      </WorkspaceSection>}

      {((category === "experiment" && experimentTab === "insights") || isDetached("insights")) && <WorkspaceSection id="insights" title={t("Insights")} layout={{ min_width: 320, min_height: 320, ideal_width: 960, ideal_height: 640 }} className="knowledge-section knowledge-wide">
        <div className="knowledge-section knowledge-wide" data-detached={isDetached("insights") || undefined}>
          <InsightsSection host={host} cardId={card.id} deployed={deployed} busy={busy} version={refreshVersion}
            schemas={experimentSchemas} schemaId={experimentSchemas.some(item => item.id === insightSchemaId) ? insightSchemaId : ""}
            onSchemaChange={setInsightSchemaId} publishedCounts={publishedCounts}
            groupArgs={groupArgs} groupFilter={groupFilter} call={call} perform={perform} request={request}
            labAgent={labAgents[0]} canRunAgent={canRunAgents} selectedModel={selectedModel}
            modelSelect={modelSelect}
            groupSelect={groupSelect} refreshControls={refreshControls} banner={<Banner error={error} notice={notice} />}
            setNotice={setNotice} setError={setError} onOpenRecord={jumpToRecord}
            onOpenExperiments={() => { setCategory("experiment"); setExperimentTab("experiments"); }}
            kindLabel={kind => KIND_LABELS[kind]()} />
        </div>
      </WorkspaceSection>}

    </div>
  </div>;
}

export default { apiVersion: 1, views: { preview: Preview, workspace: Workspace } } satisfies FrontendPlugin;
