import { t, useLocale, WorkspaceSection } from "@oaw/plugin-api";
import type { FrontendPlugin, PluginViewProps } from "@oaw/plugin-api";
import { useCallback, useEffect, useRef, useState } from "react";
import { useWorldStore } from "../../../frontend/src/state/worldStore";
import { availableModels } from "../../../frontend/src/state/modelConnections";
import { deploymentApiBase } from "../../../frontend/src/deployment/api";
import { ModelSelect } from "../../../frontend/src/cards/ModelSelect";
import { GraphMap } from "./GraphMap";
import "./style.css";

type Settings = { collection_name: string; pdf_engine: string; mineru_base_url: string };
type Group = { id: string; name: string; source_count: number; created_at: string; is_default: boolean };
type Overview = { collection: { id: string; name: string }; groups: Group[]; settings: Settings;
  engines: string[]; counts: Record<string, number>; active_jobs: number;
  graph_schema_id: string | null };
type Source = { id: string; filename: string; media_type: string; size: number; created_at: string;
  group_id: string | null; group_name: string | null;
  markdown: { artifact_id: string; size: number; engine: string | null } | null;
  record_id: string | null; projections: number };
type Extracted = { record_id: string; filename: string | null; engine: string | null;
  total_characters: number; offset: number; markdown: string; has_more: boolean };
type SchemaItem = { id: string; name: string; description: string | null; domain: string;
  kind: "literature" | "experiment"; version: number };
type SchemaDetail = SchemaItem & { definition: Record<string, unknown>; system_prompt: string;
  field_descriptions: Record<string, unknown> | null };
type Projection = { id: string; schema_id: string; record_id: string; status: string;
  valid: boolean | null; extracted_at: string; summary: string };
type ProjectionDetail = { id: string; schema_id: string; record_id: string; status: string;
  validation: { valid?: boolean; errors?: string[]; missing?: string[] } | null; notes: string | null;
  data: Record<string, unknown>; evidence: { id: string; source_id: string; artifact_id: string }[] };
type DraftItem = { id: string; status: string; revision: number; created_by: string; updated_at: string };
type DraftDetail = { id: string; status: string; revision: number; created_by: string;
  graph: { entities?: unknown[]; relations?: unknown[] }; evidence_ids: string[] };
type Entity = { id: string; type: string; name: string; properties: Record<string, unknown> };
type Relation = { id: string; type: string; source_id: string; target_id: string };
type Graph = { entities: Entity[]; relations: Relation[]; truncated: boolean };
type Job = { id: string; kind: string; status: string; progress: number | null; message: string | null;
  error: string | null; source_id: string | null; group_id: string | null; created_at: string;
  completed_at: string | null };
type JobEvent = { event: string; step: string | null; message: string | null; level: string | null;
  occurred_at: string };
type SearchResult = { source_id: string; filename: string | null; record_id: string | null;
  group_id: string | null; group_name: string | null; heading_path: string | null; excerpt: string };
type ExperimentConflict = { field: string; values: { value: unknown; source_id: string }[] };
type ExperimentItem = { id: string; group_id: string; schema_id: string; name: string;
  data: Record<string, unknown>; conflicts: ExperimentConflict[]; status: "draft" | "confirmed";
  revision: number; created_by: string | null; created_at: string; updated_at: string };
type ExperimentDetail = ExperimentItem & {
  evidence: { record_id: string; projection_id: string; source_id: string; artifact_id: string | null }[] };
/** An approval the card asked the person to confirm before it publishes a fact. */
type Pending = { args: Record<string, unknown>; reasons: string[] };

/** Literature stays the deep, general-purpose workflow; Experiment is a small,
 * shared-infrastructure workflow that reuses Schemas from the same collection.
 * Settings only ever appears for the engineering view. */
type Category = "literature" | "experiment" | "settings";
type LiteratureTab = "sources" | "schemas" | "projections" | "graph";
type ExperimentTab = "experiments" | "schemas";
const LITERATURE_TABS: { id: LiteratureTab; label: () => string }[] = [
  { id: "sources", label: () => t("Sources") },
  { id: "schemas", label: () => t("Schemas") }, { id: "projections", label: () => t("Projections") },
  { id: "graph", label: () => t("Graph") },
];
const EXPERIMENT_TABS: { id: ExperimentTab; label: () => string }[] = [
  { id: "experiments", label: () => t("Experiments") }, { id: "schemas", label: () => t("Schemas") },
];

const ACTIVE = new Set(["QUEUED", "RUNNING", "PENDING", "RETRYING"]);
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

/** The projection call needs a model, so it goes through the host bridge, not the
 * plugin. The engineering bridge lives at ``/api/...``; a deployment mounts the same
 * bridge scoped under the runtime workspace instead, gated by the release's grants. */
async function request(path: string, body: unknown, deployed: boolean) {
  const base = deployed ? `${deploymentApiBase}/runtime-app/workspace` : deploymentApiBase;
  const response = await fetch(`${base}/${path}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : t("The projection request failed"));
  return data as Record<string, unknown>;
}

/** The shared error/notice banner, duplicated at the top of every section so it is
 * visible whichever one is showing — the alternative (once, in the shared toolbar)
 * is invisible whenever a layout extracts every section into its own tab, exactly
 * the shape the "Knowledge research" preset uses. */
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
        <strong>{summary.counts.facts} {summary.counts.facts === 1 ? t("published fact") : t("published facts")}</strong>
        <p>{t("{v0} documents · {v1} entities · {v2} awaiting review", {
          v0: summary.counts.sources, v1: summary.counts.entities, v2: summary.counts.pending_review })}</p>
      </>}
  </div>;
}

export function Workspace({ host, card }: PluginViewProps) {
  useLocale();
  // The left rail switches which workflow is visible; each workflow remembers its
  // own last-opened tab so hopping to Experiment and back does not lose your place.
  const [category, setCategory] = useState<Category>("literature");
  const [literatureTab, setLiteratureTab] = useState<LiteratureTab>("sources");
  const [experimentTab, setExperimentTab] = useState<ExperimentTab>("experiments");
  const [overview, setOverview] = useState<Overview>();
  const [sources, setSources] = useState<Source[]>([]);
  const [schemas, setSchemas] = useState<SchemaItem[]>([]);
  const [projections, setProjections] = useState<Projection[]>([]);
  const [drafts, setDrafts] = useState<DraftItem[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [graph, setGraph] = useState<Graph>();

  // "" means every group; otherwise the id every list is narrowed to, so sources,
  // markdown, projections, drafts and the graph all stay scoped to one group at a time.
  const [activeGroup, setActiveGroup] = useState("");
  const [groupName, setGroupName] = useState("");
  const [groupEditing, setGroupEditing] = useState(false);
  const [selectedSources, setSelectedSources] = useState<string[]>([]);
  // Which literature schema's projections build the published graph — the one
  // pipeline Graph owns end to end. Every other literature schema is "custom":
  // Projections runs it and reviews the result, but it never reaches the graph.
  const [graphSchemaId, setGraphSchemaId] = useState("");
  const [customSchemaId, setCustomSchemaId] = useState("");
  const [customSelectedSources, setCustomSelectedSources] = useState<string[]>([]);

  const [selectedSource, setSelectedSource] = useState("");
  const [extracted, setExtracted] = useState<Extracted>();
  const [documentError, setDocumentError] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<SearchResult[]>();
  const [searchError, setSearchError] = useState("");
  const [schemaId, setSchemaId] = useState("");
  const [editor, setEditor] = useState<{ id: string; name: string; description: string;
    system_prompt: string; definition: string; kind: "literature" | "experiment" }>();
  const [chosen, setChosen] = useState<string[]>([]);
  const [projection, setProjection] = useState<ProjectionDetail>();
  const [draftDetail, setDraftDetail] = useState<DraftDetail>();
  const [chosenExperiment, setChosenExperiment] = useState<string[]>([]);
  const [experiments, setExperiments] = useState<ExperimentItem[]>([]);
  const [experimentDetail, setExperimentDetail] = useState<ExperimentDetail>();
  const [experimentDataDraft, setExperimentDataDraft] = useState("");
  const [reviewNotes, setReviewNotes] = useState("");
  const [pending, setPending] = useState<Pending>();
  const [notice, setNotice] = useState("");
  const [filter, setFilter] = useState("");
  const [entity, setEntity] = useState("");
  const [jobDetail, setJobDetail] = useState<{ job: Job; events: JobEvent[] }>();
  const [settings, setSettings] = useState<Settings>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const generation = useRef(0);
  const documentSequence = useRef(0);
  // A deployment mounts no model settings and no live picker, so projection there
  // always uses whichever model was chosen ahead of time in the engineering Settings
  // section — never a value the deployed viewer can change.
  const deployed = !!host.deployment;
  const defaultModel = (card.config?.default_model as string | undefined) ?? "";
  const catalog = useWorldStore(state => state.modelCatalog);
  const legacyModels = useWorldStore(state => state.modelSettings.models);
  const models = catalog.revision > 0
    ? availableModels({ ...catalog, connections: catalog.connections.filter(c => c.adapter === "openai" || c.adapter === "legacy") })
    : legacyModels.map(value => ({ value, label: value }));
  const [model, setModel] = useState("");
  const selectedModel = deployed ? defaultModel
    : models.some(item => item.value === model) ? model : (models[0]?.value ?? "");
  // A deployment never carries edges (only the flattened permissions grant), and
  // this is engineering-only anyway, so an empty list there is both correct and safe.
  const worldEdges = useWorldStore(state => state.edges);
  const worldCards = useWorldStore(state => state.cards);
  const updateCard = useWorldStore(state => state.updateCard);
  const connectedAgents = deployed ? [] : worldEdges
    .filter(edge => edge.target === card.id && edge.relationship.startsWith("knowledge.base."))
    .map(edge => worldCards.find(item => item.id === edge.source && item.type === "agent"))
    .filter((item): item is NonNullable<typeof item> => !!item);

  const call = useCallback((action: string, args: Record<string, unknown> = {}, confirm?: boolean) =>
    host.resourceAction(action, args, confirm), [host]);

  // "" (All groups) asks for the merged, cross-group view; a chosen group narrows
  // every list to it. Graph and jobs already treat an omitted group_id as "all".
  const groupArgs = useCallback((): Record<string, unknown> =>
    activeGroup ? { group_id: activeGroup } : { all_groups: true }, [activeGroup]);
  const groupFilter = useCallback((): Record<string, unknown> =>
    activeGroup ? { group_id: activeGroup } : {}, [activeGroup]);

  // Select a source for the extracted-markdown/projection panel and jump to the
  // Sources tab, wherever the click that asked for it happened — a search result
  // or a projection's evidence link both point at a source, not at the tab.
  const viewSource = useCallback((id: string) => {
    setSelectedSource(id); setSchemaId(schemaId || schemas[0]?.id || "");
    setCategory("literature"); setLiteratureTab("sources");
  }, [schemaId, schemas]);

  const refresh = useCallback(async () => {
    const current = generation.current;
    // Settled, not all-or-nothing: an older release may not have "experiments"
    // published yet (or any single call could hiccup), and that must not wipe out
    // every other section's data — only whichever call actually failed stays empty.
    const results = await Promise.allSettled([
      call("overview", groupArgs()), call("sources", { limit: 100, ...groupArgs() }),
      call("schemas", { operation: "list" }),
      call("projections", { limit: 100, ...groupArgs() }),
      call("draft", { operation: "list", ...groupArgs() }), call("jobs", { limit: 20, ...groupFilter() }),
      call("experiments", { limit: 100, ...groupArgs() }),
    ]);
    if (generation.current !== current) return;
    const [summary, sourceList, schemaList, projectionList, draftList, jobList, experimentList] =
      results.map(result => result.status === "fulfilled" ? result.value : undefined);
    if (summary) {
      setOverview(summary as Overview); setSettings((summary as Overview).settings);
      setGraphSchemaId((summary as Overview).graph_schema_id ?? "");
    }
    if (sourceList) setSources((sourceList.sources as Source[]) ?? []);
    if (schemaList) setSchemas((schemaList.schemas as SchemaItem[]) ?? []);
    if (projectionList) setProjections((projectionList.projections as Projection[]) ?? []);
    if (draftList) setDrafts((draftList.drafts as DraftItem[]) ?? []);
    if (jobList) setJobs((jobList.jobs as Job[]) ?? []);
    setExperiments((experimentList?.experiments as ExperimentItem[]) ?? []);
    // A core section failing is still worth surfacing; "experiments" not being
    // published on an older release is not a real error, so it never blocks this.
    const coreFailure = results.slice(0, 6).find(result => result.status === "rejected");
    if (coreFailure) throw (coreFailure as PromiseRejectedResult).reason;
  }, [call, groupArgs, groupFilter]);

  const loadGraph = useCallback(async (args: Record<string, unknown> = {}) => {
    const current = generation.current;
    const result = await call("graph", { ...groupFilter(), ...args }) as Graph;
    if (generation.current === current) setGraph(result);
  }, [call, groupFilter]);

  const perform = useCallback(async (work: () => Promise<void>) => {
    const current = generation.current;
    setBusy(true); setError("");
    try { await work(); }
    catch (reason) { if (generation.current === current) setError(message(reason)); }
    finally { if (generation.current === current) setBusy(false); }
  }, []);

  /** Walk outward from one entity; the map keeps whatever is already placed. */
  const expand = useCallback((id: string) => {
    setEntity(id);
    void perform(() => loadGraph({ operation: "traverse", entity_id: id, max_depth: 2 }));
  }, [loadGraph, perform]);

  useEffect(() => {
    void perform(async () => { await refresh(); await loadGraph(); });
    return () => { generation.current += 1; documentSequence.current += 1; };
  }, [card.id, perform, refresh, loadGraph]);

  // A conversion job runs on the card's own thread, so poll until nothing is active.
  useEffect(() => {
    if (!jobs.some(job => ACTIVE.has(job.status))) return;
    const timer = setTimeout(() => { void refresh().catch(reason => setError(message(reason))); }, 1500);
    return () => clearTimeout(timer);
  }, [jobs, refresh]);

  useEffect(() => {
    const sequence = ++documentSequence.current;
    setExtracted(undefined); setDocumentError("");
    if (!selectedSource) return;
    call("markdown", { source_id: selectedSource, limit: 40000 }).then(value => {
      if (sequence === documentSequence.current) setExtracted(value as Extracted);
    }).catch(reason => { if (sequence === documentSequence.current) setDocumentError(message(reason)); });
  }, [call, selectedSource]);

  const upload = (files: FileList | File[]) => perform(async () => {
    const list = Array.from(files);
    if (!list.length) return;
    for (const file of list) {
      const content = await fileBase64(file);
      await call("ingest", { filename: file.name, content_base64: content,
        media_type: file.type || "application/octet-stream",
        ...(activeGroup ? { group_id: activeGroup } : {}) });
    }
    setNotice(list.length === 1
      ? t("{v0} uploaded. Select it below and press Process to convert it to markdown.", { v0: list[0].name })
      : t("{v0} files uploaded. Select them below and press Process to convert them in a batch.", { v0: list.length }));
    await refresh();
  });

  const processSources = (sourceIds: string[]) => perform(async () => {
    if (!sourceIds.length) return;
    const result = await call("process", { source_ids: sourceIds });
    const outcomes = result.jobs as { job?: { id: string } }[];
    const queued = outcomes.filter(item => item.job).length;
    setNotice(queued
      ? t("Converting {v0} of {v1} selected source(s)…", { v0: queued, v1: outcomes.length })
      : t("Nothing to convert: already converted or already running"));
    setSelectedSources([]);
    await refresh();
  });

  const processPending = () => perform(async () => {
    const result = await call("process", groupFilter());
    const outcomes = result.jobs as unknown[];
    setNotice(outcomes.length
      ? t("Converting {v0} pending source(s)…", { v0: outcomes.length })
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

  const project = () => extracted && perform(async () => {
    if (!schemaId) throw new Error(t("Choose an extraction schema first"));
    if (!selectedModel) throw new Error(deployed
      ? t("This deployment has no default model configured yet")
      : t("Configure a model connection first"));
    const result = await request(`knowledge/${card.id}/project`, {
      schema_id: schemaId, model: selectedModel, record_id: extracted.record_id }, deployed);
    const saved = result.projection as { id: string; validation?: { valid?: boolean } };
    setNotice(result.truncated
      ? t("Projected a truncated document; only the first part was sent to the model")
      : saved.validation?.valid === false
        ? t("Projection saved but it does not satisfy the schema; open it to see why")
        : t("Projection {v0} saved", { v0: shortId(saved.id) }));
    await refresh();
  });

  // Same call as "Project to JSON" above, just run once per source id given
  // instead of the one currently open — used by both Projections' custom batch
  // (deliberately selected sources) and Graph's "project all pending" (every
  // converted source the graph schema has not already produced a projection for).
  const projectSources = (targetSchemaId: string, sourceIds: string[], onDone?: () => void) => perform(async () => {
    if (!targetSchemaId) throw new Error(t("Choose an extraction schema first"));
    if (!selectedModel) throw new Error(deployed
      ? t("This deployment has no default model configured yet")
      : t("Configure a model connection first"));
    const targets = sources.filter(item => sourceIds.includes(item.id) && item.record_id);
    if (!targets.length) throw new Error(t("Select at least one converted source first"));
    let invalid = 0;
    for (const source of targets) {
      const result = await request(`knowledge/${card.id}/project`, {
        schema_id: targetSchemaId, model: selectedModel, record_id: source.record_id }, deployed);
      const projection = result.projection as { validation?: { valid?: boolean } };
      if (projection.validation?.valid === false) invalid++;
    }
    setNotice(invalid
      ? t("Projected {v0} source(s); {v1} do not satisfy the schema", { v0: targets.length, v1: invalid })
      : t("Projected {v0} source(s)", { v0: targets.length }));
    onDone?.();
    await refresh();
  });

  // Which literature schema builds the graph is a business choice, not engineering
  // config, so it stays changeable from a deployed release — unlike Settings.
  const setGraphSchema = (id: string) => perform(async () => {
    const result = await call("graph_schema", { operation: "set", schema_id: id || null });
    setGraphSchemaId((result.schema_id as string) ?? "");
    await refresh();
  });

  const saveSchema = () => editor && perform(async () => {
    let definition: unknown;
    try { definition = JSON.parse(editor.definition); }
    catch { throw new Error(t("The schema definition must be valid JSON")); }
    const payload = { name: editor.name, description: editor.description || null,
      system_prompt: editor.system_prompt, definition, kind: editor.kind };
    const result = editor.id
      ? await call("schemas", { operation: "update", schema_id: editor.id, ...payload })
      : await call("schemas", { operation: "create", ...payload });
    setSchemaId((result.schema as SchemaItem).id);
    setEditor(undefined);
    await refresh();
  });

  const openSchema = (id: string) => perform(async () => {
    const result = await call("schemas", { operation: "get", schema_id: id });
    const detail = result.schema as SchemaDetail;
    setEditor({ id: detail.id, name: detail.name, description: detail.description ?? "",
      system_prompt: detail.system_prompt, definition: pretty(detail.definition), kind: detail.kind });
  });

  const openProjection = (id: string) => perform(async () => {
    setProjection((await call("projections", { projection_id: id })).projection as ProjectionDetail);
  });

  const loadDraft = useCallback(async (id: string) => {
    const current = generation.current;
    const result = await call("draft", { operation: "get", draft_id: id });
    if (generation.current !== current) return;
    setPending(undefined);
    setDraftDetail(result.draft as DraftDetail);
  }, [call]);

  const buildDraft = () => perform(async () => {
    const result = await call("draft", { operation: "create", projection_ids: chosen });
    const built = result.draft as { id: string; entities: number; relations: number };
    setNotice(t("Draft ready for review: {v0} entities, {v1} relations", {
      v0: built.entities, v1: built.relations }));
    setChosen([]);
    await refresh();
    await loadDraft(built.id);
  });

  const loadExperiment = useCallback(async (id: string) => {
    const current = generation.current;
    const result = await call("experiments", { operation: "get", record_id: id });
    if (generation.current !== current) return;
    const detail = result.experiment as ExperimentDetail;
    setExperimentDetail(detail);
    setExperimentDataDraft(pretty(detail.data));
  }, [call]);

  const assembleExperiment = () => perform(async () => {
    if (!chosenExperiment.length) throw new Error(t("Select at least one projection first"));
    if (!selectedModel) throw new Error(deployed
      ? t("This deployment has no default model configured yet")
      : t("Configure a model connection first"));
    const result = await request(`knowledge/${card.id}/assemble`, {
      projection_ids: chosenExperiment, model: selectedModel }, deployed);
    const built = result.record as { id: string; name: string; conflicts: unknown[] };
    setNotice(built.conflicts.length
      ? t("Experiment record \"{v0}\" assembled with {v1} field(s) to review", {
          v0: built.name, v1: built.conflicts.length })
      : t("Experiment record \"{v0}\" assembled", { v0: built.name }));
    setChosenExperiment([]);
    await refresh();
    await loadExperiment(built.id);
  });

  const saveExperimentEdits = () => experimentDetail && perform(async () => {
    let data: Record<string, unknown>;
    try { data = JSON.parse(experimentDataDraft); }
    catch { throw new Error(t("The experiment data must be valid JSON")); }
    await call("experiment_update", { operation: "update", record_id: experimentDetail.id,
      data, expected_revision: experimentDetail.revision });
    setNotice(t("Changes saved"));
    await refresh();
    await loadExperiment(experimentDetail.id);
  });

  const confirmExperiment = () => experimentDetail && perform(async () => {
    await call("experiment_update", { operation: "confirm", record_id: experimentDetail.id,
      expected_revision: experimentDetail.revision });
    setNotice(t("Experiment record confirmed"));
    await refresh();
    await loadExperiment(experimentDetail.id);
  });

  const decide = (operation: "submit" | "approve" | "reject", confirmed?: Pending) =>
    draftDetail && perform(async () => {
      const args = confirmed?.args ?? { operation, draft_id: draftDetail.id,
        expected_revision: draftDetail.revision, notes: reviewNotes || null };
      const result = await call("review", args, !!confirmed);
      if (result.status === "confirmation_required") {
        setPending({ args, reasons: (result.reasons as string[]) ?? [] });
        return;
      }
      setPending(undefined); setReviewNotes("");
      const published = result.graph as { entities: number; relations: number } | undefined;
      setNotice(published
        ? t("Published as fact: {v0} entities and {v1} relations are now in the graph", {
            v0: published.entities, v1: published.relations })
        : t("Draft {v0}", { v0: String(result.decision).toLowerCase() }));
      await refresh();
      await loadGraph();
      await loadDraft(draftDetail.id);
    });

  const openJob = (id: string) => perform(async () => {
    const result = await call("jobs", { job_id: id });
    setJobDetail({ job: result.job as Job, events: (result.events as JobEvent[]) ?? [] });
  });

  const saveSettings = (patch: Partial<Settings>) => perform(async () => {
    const result = await call("settings", patch);
    setSettings(result.settings as Settings);
    await refresh();
  });

  // Engineering-only: a deployment has no writable config at all, so this never
  // runs there — the Settings section that calls it is not even mounted when deployed.
  const saveDefaultModel = (value: string) => perform(async () => {
    await host.updateConfig({ default_model: value });
  });

  const counts = overview?.counts ?? {};
  const current = settings ?? { collection_name: "", pdf_engine: "auto", mineru_base_url: "" };
  const currentSource = sources.find(item => item.id === selectedSource);
  const running = jobs.filter(job => ACTIVE.has(job.status));
  // A search can drop the selected entity; the panel follows what is on the map.
  const selectedEntity = graph?.entities.find(item => item.id === entity);
  const groups = overview?.groups ?? [];
  const activeGroupObject = groups.find(item => item.id === activeGroup);
  const pendingSources = sources.filter(item => !item.markdown);
  const experimentSchemaIds = new Set(schemas.filter(item => item.kind === "experiment").map(item => item.id));
  const experimentProjections = projections.filter(item => experimentSchemaIds.has(item.schema_id));
  const literatureSchemas = schemas.filter(item => item.kind === "literature");
  // Every literature schema is a candidate to become the graph schema; whichever
  // one is not currently chosen is "custom" — Projections runs and reviews it, but
  // it never reaches the graph. Changing the choice in Graph moves a schema
  // between these two lists; nothing about the schema itself is retagged.
  const customSchemas = literatureSchemas.filter(item => item.id !== graphSchemaId);
  const convertedSources = sources.filter(item => item.markdown && item.record_id);
  const customProjections = projections.filter(item =>
    item.schema_id !== graphSchemaId && !experimentSchemaIds.has(item.schema_id));
  const graphProjections = projections.filter(item => item.schema_id === graphSchemaId);
  const graphProjectedRecordIds = new Set(graphProjections.map(item => item.record_id));
  const graphPendingSources = convertedSources.filter(item => !graphProjectedRecordIds.has(item.record_id!));

  return <div className="knowledge-app nodrag nowheel" aria-label={t("{v0} knowledge base", { v0: card.name })}>
    <header className="knowledge-toolbar">
      <span className="knowledge-badge">{t("Knowledge base")}</span>
      <label className="knowledge-groupselect">{t("Group")}
        <select value={activeGroup} disabled={busy}
          onChange={event => { setActiveGroup(event.target.value); setGroupEditing(false); }}>
          <option value="">{t("All groups")}</option>
          {groups.map(item => <option key={item.id} value={item.id}>
            {item.name}{item.is_default ? ` (${t("default")})` : ""}</option>)}
        </select>
      </label>
      <span className="knowledge-counts">
        {t("{v0} documents · {v1} projections · {v2} facts · {v3} entities", {
          v0: counts.sources ?? 0, v1: counts.projections ?? 0,
          v2: counts.facts ?? 0, v3: counts.entities ?? 0 })}
      </span>
      {!!running.length && <span className="knowledge-running" role="status">
        {t("{v0} conversion running", { v0: running.length })}</span>}
      <button type="button" disabled={busy} onClick={() => void perform(async () => {
        setNotice(""); await refresh(); await loadGraph();
      })}>{t("Refresh")}</button>
    </header>

    <div className="knowledge-layout">
      {/* The rail switches workflows; each workflow keeps its own tab strip, so the
          middle column only ever shows the tabs that belong to whichever is active. */}
      <nav className="knowledge-rail" aria-label={t("Workflow")}>
        <button type="button" aria-pressed={category === "literature"} onClick={() => setCategory("literature")}>
          {t("Literature")}</button>
        <button type="button" aria-pressed={category === "experiment"} onClick={() => setCategory("experiment")}>
          {t("Experiment")}</button>
        {!deployed && <button type="button" aria-pressed={category === "settings"} onClick={() => setCategory("settings")}>
          {t("Settings")}</button>}
      </nav>
      <div className="knowledge-content">
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
    <div className="knowledge-grid">
      {!deployed && category === "settings" && <WorkspaceSection id="settings" title={t("Settings")} className="knowledge-section knowledge-settings-section">
        {/* A detached section is portaled straight into its own tab, past ".knowledge-app": this
            wrapper carries the same classes so base colors and control styling still apply there. */}
        <div className="knowledge-section knowledge-settings-section">
        <Banner error={error} notice={notice} />
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
        <small>{t("The MinerU token comes from the OAW_MINERU_TOKEN environment variable, never from this card.")}</small>
        <p>{t("Available engines: {v0}", { v0: (overview?.engines ?? []).join(", ") || "—" })}</p>
        <label>{t("Default model for deployment")}
          <select value={defaultModel} disabled={busy || !models.length}
            onChange={event => void saveDefaultModel(event.target.value)}>
            <option value="">{t("None chosen")}</option>
            {models.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
          </select>
        </label>
        <small>{t("A published deployment has no live model picker, so Project to JSON there always uses this one.")}</small>
        <h4>{t("Connected agents")}</h4>
        {!connectedAgents.length
          ? <p className="knowledge-meta">{t("No Agent is wired to this base yet — connect one with a Knowledge read or Knowledge extract edge on the canvas.")}</p>
          : connectedAgents.map(agent => <div key={agent.id} className="knowledge-agent-model">
              <span>{agent.name}</span>
              <ModelSelect label={t("{v0}’s model", { v0: agent.name })}
                value={String(agent.config?.model ?? "oaw:default")}
                onChange={value => void updateCard(agent.id, { config: { model: value } })} />
            </div>)}
        <small>{t("This is the same model catalog configured on the canvas — it changes what the Agent itself uses to answer, in every conversation, not just this one.")}</small>
        </div>
      </WorkspaceSection>}
      {category === "literature" && literatureTab === "sources" && <WorkspaceSection id="sources" title={t("Sources")} className="knowledge-section">
        <div className="knowledge-section">
        <h3>{t("Sources")}</h3>
        <Banner error={error} notice={notice} />
        <div className="knowledge-groupbar">
          {/* The toolbar's own group picker lives outside every section, so a layout that
              places Sources into its own tab (as a deployment does) can leave it with no way
              to switch groups at all; this copy keeps that control reachable right here. */}
          {!groupEditing ? <div className="knowledge-formbar">
            <label className="knowledge-groupselect">{t("Group")}
              <select value={activeGroup} disabled={busy}
                onChange={event => { setActiveGroup(event.target.value); setGroupEditing(false); }}>
                <option value="">{t("All groups")}</option>
                {groups.map(item => <option key={item.id} value={item.id}>
                  {item.name}{item.is_default ? ` (${t("default")})` : ""}</option>)}
              </select>
            </label>
            <button type="button" disabled={busy} onClick={() => { setGroupName(""); setGroupEditing(true); }}>
              {t("New group")}</button>
            {activeGroupObject && <button type="button" disabled={busy}
              onClick={() => { setGroupName(activeGroupObject.name); setGroupEditing(true); }}>
              {t("Rename group")}</button>}
            {activeGroupObject && !activeGroupObject.is_default && <button type="button" disabled={busy}
              onClick={() => void deleteGroup()}>{t("Delete group")}</button>}
          </div> : <div className="knowledge-formbar">
            <input value={groupName} disabled={busy} placeholder={t("Group name")} autoFocus
              onChange={event => setGroupName(event.target.value)} />
            <button type="button" className="knowledge-primary" disabled={busy || !groupName.trim()}
              onClick={() => void (activeGroupObject ? renameGroup() : createGroup())}>
              {activeGroupObject ? t("Save") : t("Create")}</button>
            <button type="button" disabled={busy} onClick={() => setGroupEditing(false)}>{t("Cancel")}</button>
          </div>}
        </div>
        <label className="knowledge-upload">{t("Add documents")}
          <input type="file" multiple disabled={busy} onChange={event => {
            const files = Array.from(event.target.files ?? []);
            event.target.value = "";
            if (files.length) void upload(files);
          }} />
        </label>
        <small>{t("PDF, markdown, text, CSV, TSV, Excel (.xlsx), images or JSON up to 32 MiB each. Uploads stay unconverted until you process them below, alone or in a batch.")}</small>

        {/* Folded in from what used to be its own "Search" tab: Sources already
            has almost every source-level action, so full-text search over the
            converted documents belongs right here too, not behind another tab. */}
        <div className="knowledge-search-inline">
          <div className="knowledge-formbar">
            <input value={searchQuery} disabled={busy} placeholder={t("Search converted documents…")}
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
        </div>

        {!sources.length && <p className="knowledge-empty">{t("Nothing uploaded yet.")}</p>}
        <ul className="knowledge-list">
          {sources.map(item => <li key={item.id} className="knowledge-checkrow">
            <label><input type="checkbox" checked={selectedSources.includes(item.id)} disabled={busy}
              onChange={event => setSelectedSources(event.target.checked
                ? [...selectedSources, item.id] : selectedSources.filter(value => value !== item.id))} />
              <span className="knowledge-visually-hidden">{t("Select {v0}", { v0: item.filename })}</span>
            </label>
            <button type="button" aria-pressed={selectedSource === item.id}
              onClick={() => viewSource(item.id)}>
              <span>{item.filename}{!activeGroup && item.group_name ? ` · ${item.group_name}` : ""}</span>
              <small>{bytes(item.size)} · {item.markdown
                ? t("markdown via {v0}", { v0: item.markdown.engine ?? "?" })
                : t("awaiting conversion")} · {t("{v0} projections", { v0: item.projections })}</small>
            </button>
            <button type="button" className="knowledge-viewmarkdown" disabled={!item.markdown}
              aria-pressed={selectedSource === item.id} title={item.markdown ? t("View extracted markdown") : t("Not converted yet")}
              onClick={() => viewSource(item.id)}>{t("Markdown")}</button>
          </li>)}
        </ul>
        <div className="knowledge-formbar">
          <button type="button" className="knowledge-primary" disabled={busy || !selectedSources.length}
            onClick={() => void processSources(selectedSources)}>
            {t("Process {v0} selected", { v0: selectedSources.length })}</button>
          <button type="button" disabled={busy || !pendingSources.length}
            onClick={() => void processPending()}>
            {t("Process all pending ({v0})", { v0: pendingSources.length })}</button>
        </div>

        {currentSource && <div className="knowledge-detail">
          <h4>{t("Extracted markdown — {v0}", { v0: currentSource.filename })}</h4>
          <div className="knowledge-projectbar">
            {/* An ad hoc, single-document projection while reading — any schema,
                including the graph one. Batch-projecting several sources at once
                lives in Projections (custom schemas) and Graph (the graph schema). */}
            <label>{t("Extraction schema")}
              <select value={schemaId} disabled={busy} onChange={event => setSchemaId(event.target.value)}>
                <option value="">{t("Choose a schema")}</option>
                {schemas.map(item => <option key={item.id} value={item.id}>{item.name} v{item.version}</option>)}
              </select>
            </label>
            {deployed
              ? <p className="knowledge-meta">{t("Model: {v0}", { v0: defaultModel || t("none configured") })}</p>
              : <label>{t("Model")}
                  <select value={selectedModel} disabled={busy || !models.length} onChange={event => setModel(event.target.value)}>
                    {!models.length && <option value="">{t("No model configured")}</option>}
                    {models.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
                  </select>
                </label>}
            <button type="button" className="knowledge-primary" disabled={busy || !extracted || !schemaId || !selectedModel}
              onClick={() => void project()}>{busy ? t("Working…") : t("Project to JSON")}</button>
            <button type="button" onClick={() => setSelectedSource("")}>{t("Close")}</button>
          </div>
          {documentError ? <p className="knowledge-empty">{documentError}</p>
            : !extracted ? <p role="status">{t("Loading markdown…")}</p> : <>
            <p className="knowledge-meta">{t("{v0} · {v1} characters · engine {v2}", {
              v0: extracted.filename ?? currentSource.filename, v1: extracted.total_characters,
              v2: extracted.engine ?? "?" })}</p>
            <pre className="knowledge-markdown">{extracted.markdown}</pre>
            {extracted.has_more && <button type="button" disabled={busy} onClick={() => void extend()}>{t("Load more")}</button>}
          </>}
        </div>}

        <h4>{t("Conversion jobs")}</h4>
        {!jobs.length && <p className="knowledge-empty">{t("No jobs yet.")}</p>}
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
        </div>
      </WorkspaceSection>}

      {((category === "literature" && literatureTab === "schemas") || (category === "experiment" && experimentTab === "schemas")) && <WorkspaceSection id="schemas" title={t("Schemas")} className="knowledge-section">
        <div className="knowledge-section">
        <h3>{t("Schemas")}</h3>
        <Banner error={error} notice={notice} />
        <p className="knowledge-meta">{t("A schema is a JSON Schema plus the system prompt used to extract it.")}</p>
        <ul className="knowledge-list">
          {schemas.map(item => <li key={item.id}>
            <button type="button" aria-pressed={editor?.id === item.id} disabled={busy} onClick={() => void openSchema(item.id)}>
              <span>{item.name}</span>
              <small>v{item.version} · {item.kind === "experiment" ? t("experiment") : t("literature")} · {item.description || item.domain}</small>
            </button>
          </li>)}
        </ul>
        {!editor && <button type="button" disabled={busy} onClick={() => setEditor({
          id: "", name: "", description: "", system_prompt: "", kind: "literature",
          definition: pretty({ type: "object", required: [], properties: { entities: { type: "array", items: { type: "object" } }, relations: { type: "array", items: { type: "object" } } } }),
        })}>{t("New schema")}</button>}
        {editor && <div className="knowledge-form">
          <label>{t("Name")}<input value={editor.name} disabled={busy}
            onChange={event => setEditor({ ...editor, name: event.target.value })} /></label>
          <label>{t("Description")}<input value={editor.description} disabled={busy}
            onChange={event => setEditor({ ...editor, description: event.target.value })} /></label>
          <label>{t("Kind")}
            <select value={editor.kind} disabled={busy}
              onChange={event => setEditor({ ...editor, kind: event.target.value as "literature" | "experiment" })}>
              <option value="literature">{t("Literature — projections build a draft for the knowledge graph")}</option>
              <option value="experiment">{t("Experiment — projections assemble into one experiment record")}</option>
            </select>
          </label>
          <label>{t("System prompt")}<textarea value={editor.system_prompt} disabled={busy} rows={3}
            onChange={event => setEditor({ ...editor, system_prompt: event.target.value })} /></label>
          <label>{t("JSON Schema definition")}<textarea value={editor.definition} disabled={busy} rows={8} spellCheck={false}
            onChange={event => setEditor({ ...editor, definition: event.target.value })} /></label>
          <div className="knowledge-formbar">
            <button type="button" className="knowledge-primary" disabled={busy || !editor.name || !editor.system_prompt}
              onClick={() => void saveSchema()}>{editor.id ? t("Save schema") : t("Create schema")}</button>
            <button type="button" disabled={busy} onClick={() => setEditor(undefined)}>{t("Cancel")}</button>
          </div>
        </div>}
        </div>
      </WorkspaceSection>}

      {category === "literature" && literatureTab === "projections" && <WorkspaceSection id="projections" title={t("Projections")} className="knowledge-section">
        <div className="knowledge-section">
        <h3>{t("Projections")}</h3>
        <Banner error={error} notice={notice} />
        <p className="knowledge-meta">{t("Custom structured extraction — pull domain-specific data (e.g. materials properties) out of your documents with a schema of your own. A projection here is reviewed right below; it never builds a draft or reaches the published graph — only Graph's own schema does that.")}</p>

        <h4>{t("Project sources")}</h4>
        {!customSchemas.length
          ? <p className="knowledge-empty">{t("Create a literature schema in Schemas first (or free one up: Graph is currently using {v0}).", {
              v0: literatureSchemas.find(item => item.id === graphSchemaId)?.name ?? t("none") })}</p>
          : <>
            <div className="knowledge-formbar">
              <label>{t("Custom schema")}
                <select value={customSchemaId} disabled={busy} onChange={event => setCustomSchemaId(event.target.value)}>
                  <option value="">{t("Choose a schema")}</option>
                  {customSchemas.map(item => <option key={item.id} value={item.id}>{item.name} v{item.version}</option>)}
                </select>
              </label>
              {deployed
                ? <p className="knowledge-meta">{t("Model: {v0}", { v0: defaultModel || t("none configured") })}</p>
                : <label>{t("Model")}
                    <select value={selectedModel} disabled={busy || !models.length} onChange={event => setModel(event.target.value)}>
                      {!models.length && <option value="">{t("No model configured")}</option>}
                      {models.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
                    </select>
                  </label>}
            </div>
            {!convertedSources.length ? <p className="knowledge-empty">{t("Convert a source in Sources first.")}</p> : <ul className="knowledge-list">
              {convertedSources.map(item => <li key={item.id} className="knowledge-checkrow">
                <label><input type="checkbox" checked={customSelectedSources.includes(item.id)} disabled={busy}
                  onChange={event => setCustomSelectedSources(event.target.checked
                    ? [...customSelectedSources, item.id] : customSelectedSources.filter(value => value !== item.id))} />
                  <span className="knowledge-visually-hidden">{t("Select {v0}", { v0: item.filename })}</span>
                </label>
                <span className="knowledge-checkrow-label">
                  <span>{item.filename}</span>
                  <small>{!activeGroup && item.group_name ? item.group_name : t("{v0} projections", { v0: item.projections })}</small>
                </span>
              </li>)}
            </ul>}
            <button type="button" className="knowledge-primary"
              disabled={busy || !customSchemaId || !selectedModel || !customSelectedSources.length}
              onClick={() => void projectSources(customSchemaId, customSelectedSources, () => setCustomSelectedSources([]))}>
              {t("Project {v0} selected", { v0: customSelectedSources.length })}</button>
          </>}

        <h4>{t("Projections")}</h4>
        {!customProjections.length && <p className="knowledge-empty">{t("No custom projections yet.")}</p>}
        <ul className="knowledge-list">
          {customProjections.map(item => <li key={item.id}>
            <button type="button" aria-pressed={projection?.id === item.id} disabled={busy}
              onClick={() => void openProjection(item.id)}>
              <span>{item.summary || shortId(item.id)}</span>
              <small>{item.valid === false ? t("invalid") : t("valid")} · {schemas.find(s => s.id === item.schema_id)?.name ?? shortId(item.schema_id)}</small>
            </button>
          </li>)}
        </ul>
        {projection && <div className="knowledge-detail">
          <h4>{t("Projection {v0}", { v0: shortId(projection.id) })}</h4>
          {projection.validation?.valid === false && <p role="alert" className="knowledge-error">
            {(projection.validation.errors ?? projection.validation.missing ?? []).join("; ") || t("Does not satisfy the schema")}</p>}
          <p className="knowledge-meta">{t("Evidence: {v0}", {
            v0: projection.evidence.map(link => shortId(link.source_id)).join(", ") || "—" })}</p>
          <pre>{pretty(projection.data)}</pre>
        </div>}
        </div>
      </WorkspaceSection>}

      {category === "experiment" && experimentTab === "experiments" && <WorkspaceSection id="experiments" title={t("Experiments")} className="knowledge-section">
        <div className="knowledge-section">
        <h3>{t("Experiments")}</h3>
        <Banner error={error} notice={notice} />
        <p className="knowledge-meta">{t("Assemble several per-file extractions (one kind=\"experiment\" schema) into one structured, queryable experiment record. Never touches a draft, review or the published graph.")}</p>
        <h4>{t("Select projections to assemble")}</h4>
        {!experimentProjections.length
          ? <p className="knowledge-empty">{t("Project a document against an experiment-kind schema first, in Sources.")}</p>
          : <ul className="knowledge-list">
              {experimentProjections.map(item => <li key={item.id} className="knowledge-checkrow">
                <label><input type="checkbox" checked={chosenExperiment.includes(item.id)} disabled={busy}
                  onChange={event => setChosenExperiment(event.target.checked
                    ? [...chosenExperiment, item.id] : chosenExperiment.filter(value => value !== item.id))} />
                  <span className="knowledge-visually-hidden">{t("Select projection {v0}", { v0: shortId(item.id) })}</span>
                </label>
                <span className="knowledge-checkrow-label">
                  <span>{item.summary || shortId(item.id)}</span>
                  <small>{schemas.find(s => s.id === item.schema_id)?.name ?? shortId(item.schema_id)}</small>
                </span>
              </li>)}
            </ul>}
        <button type="button" className="knowledge-primary" disabled={busy || !chosenExperiment.length}
          onClick={() => void assembleExperiment()}>
          {t("Assemble experiment record from {v0} selected", { v0: chosenExperiment.length })}</button>

        <h4>{t("Experiment records")}</h4>
        {!experiments.length && <p className="knowledge-empty">{t("No experiment records yet.")}</p>}
        <ul className="knowledge-list">
          {experiments.map(item => <li key={item.id}>
            <button type="button" aria-pressed={experimentDetail?.id === item.id} disabled={busy}
              onClick={() => void loadExperiment(item.id)}>
              <span>{item.name}</span>
              <small>{item.status} · {t("revision {v0}", { v0: item.revision })}
                {item.conflicts.length ? ` · ${t("{v0} to review", { v0: item.conflicts.length })}` : ""}</small>
            </button>
          </li>)}
        </ul>
        {experimentDetail && <div className="knowledge-detail">
          <h4>{experimentDetail.name}</h4>
          <p className="knowledge-meta">{t("{v0} · revision {v1} · evidence: {v2}", {
            v0: experimentDetail.status, v1: experimentDetail.revision,
            v2: experimentDetail.evidence.map(link => shortId(link.source_id)).join(", ") || "—" })}</p>
          {!!experimentDetail.conflicts.length && <div role="alert" className="knowledge-confirm">
            <strong>{t("Sources disagree on these fields")}</strong>
            <ul className="knowledge-relations">
              {experimentDetail.conflicts.map((conflict, index) => <li key={index}>
                <code>{conflict.field}</code>: {conflict.values.map(v =>
                  `${JSON.stringify(v.value)} (${shortId(v.source_id)})`).join(" vs. ")}
              </li>)}
            </ul>
          </div>}
          <label>{t("Merged data (JSON)")}<textarea value={experimentDataDraft} disabled={busy} rows={10}
            spellCheck={false} onChange={event => setExperimentDataDraft(event.target.value)} /></label>
          <div className="knowledge-formbar">
            <button type="button" disabled={busy} onClick={() => void saveExperimentEdits()}>
              {t("Save changes")}</button>
            <button type="button" className="knowledge-primary" disabled={busy || experimentDetail.status === "confirmed"}
              onClick={() => void confirmExperiment()}>{t("Confirm")}</button>
          </div>
        </div>}
        </div>
      </WorkspaceSection>}

      {category === "literature" && literatureTab === "graph" && <WorkspaceSection id="graph" title={t("Graph")} className="knowledge-section knowledge-wide">
        <div className="knowledge-section knowledge-wide">
        <h3>{t("Knowledge graph")}</h3>
        <Banner error={error} notice={notice} />
        <p className="knowledge-meta">{t("The graph is a specific, always-on projection: one literature schema, chosen once below, whose output is projected, reviewed and published right here — end to end.")}</p>
        <label>{t("Graph schema")}
          <select value={graphSchemaId} disabled={busy} onChange={event => void setGraphSchema(event.target.value)}>
            <option value="">{t("Choose a schema")}</option>
            {literatureSchemas.map(item => <option key={item.id} value={item.id}>{item.name} v{item.version}</option>)}
          </select>
        </label>

        {!graphSchemaId
          ? <p className="knowledge-empty">{t("Choose a schema above to start building the graph from your sources (create one in Schemas first if none fits).")}</p>
          : <>
            <h4>{t("Project sources")}</h4>
            <p className="knowledge-meta">{t("{v0} converted source(s) awaiting projection against the graph schema.", { v0: graphPendingSources.length })}</p>
            {!deployed && <label>{t("Model")}
              <select value={selectedModel} disabled={busy || !models.length} onChange={event => setModel(event.target.value)}>
                {!models.length && <option value="">{t("No model configured")}</option>}
                {models.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
              </select>
            </label>}
            <button type="button" className="knowledge-primary" disabled={busy || !selectedModel || !graphPendingSources.length}
              onClick={() => void projectSources(graphSchemaId, graphPendingSources.map(item => item.id))}>
              {t("Project all pending ({v0})", { v0: graphPendingSources.length })}</button>

            <h4>{t("Projections")}</h4>
            {!graphProjections.length && <p className="knowledge-empty">{t("No graph-schema projections yet.")}</p>}
            <ul className="knowledge-list">
              {graphProjections.map(item => <li key={item.id} className="knowledge-checkrow">
                <label><input type="checkbox" checked={chosen.includes(item.id)} disabled={busy}
                  onChange={event => setChosen(event.target.checked
                    ? [...chosen, item.id] : chosen.filter(value => value !== item.id))} />
                  <span className="knowledge-visually-hidden">{t("Select projection {v0}", { v0: shortId(item.id) })}</span>
                </label>
                <button type="button" aria-pressed={projection?.id === item.id} disabled={busy}
                  onClick={() => void openProjection(item.id)}>
                  <span>{item.summary || shortId(item.id)}</span>
                  <small>{item.valid === false ? t("invalid") : t("valid")}</small>
                </button>
              </li>)}
            </ul>
            <button type="button" className="knowledge-primary" disabled={busy || !chosen.length}
              onClick={() => void buildDraft()}>{t("Build draft from {v0} selected", { v0: chosen.length })}</button>
            {projection && <div className="knowledge-detail">
              <h4>{t("Projection {v0}", { v0: shortId(projection.id) })}</h4>
              {projection.validation?.valid === false && <p role="alert" className="knowledge-error">
                {(projection.validation.errors ?? projection.validation.missing ?? []).join("; ") || t("Does not satisfy the schema")}</p>}
              <p className="knowledge-meta">{t("Evidence: {v0}", {
                v0: projection.evidence.map(link => shortId(link.source_id)).join(", ") || "—" })}</p>
              <pre>{pretty(projection.data)}</pre>
            </div>}

            <h4>{t("Review")}</h4>
            <p className="knowledge-meta">{t("Approving publishes a fact revision and is the only thing that writes the graph.")}</p>
            {!drafts.length && <p className="knowledge-empty">{t("No drafts yet. Select projections above to build one.")}</p>}
            <ul className="knowledge-list">
              {drafts.map(item => <li key={item.id}>
                <button type="button" aria-pressed={draftDetail?.id === item.id} disabled={busy}
                  onClick={() => void perform(() => loadDraft(item.id))}>
                  <span>{shortId(item.id)}</span><small>{item.status} · {t("revision {v0}", { v0: item.revision })} · {item.created_by}</small>
                </button>
              </li>)}
            </ul>
            {draftDetail && <div className="knowledge-detail">
              <h4>{t("Draft {v0}", { v0: shortId(draftDetail.id) })}</h4>
              <p className="knowledge-meta">{t("{v0} · {v1} entities · {v2} relations · {v3} evidence links", {
                v0: draftDetail.status, v1: draftDetail.graph.entities?.length ?? 0,
                v2: draftDetail.graph.relations?.length ?? 0, v3: draftDetail.evidence_ids.length })}</p>
              <pre>{pretty(draftDetail.graph)}</pre>
              <label>{t("Review notes")}<textarea value={reviewNotes} rows={2} disabled={busy}
                onChange={event => setReviewNotes(event.target.value)} /></label>
              {pending ? <div role="alert" className="knowledge-confirm">
                <strong>{t("Publish this draft as fact?")}</strong>
                <p>{pending.reasons.join(" ")}</p>
                <button type="button" className="knowledge-primary" disabled={busy}
                  onClick={() => void decide("approve", pending)}>{t("Confirm and publish")}</button>
                <button type="button" disabled={busy} onClick={() => setPending(undefined)}>{t("Cancel")}</button>
              </div> : <div className="knowledge-formbar">
                <button type="button" disabled={busy || draftDetail.status !== "DRAFT"}
                  onClick={() => void decide("submit")}>{t("Submit for review")}</button>
                {/* mkb only allows approve/reject once a draft has moved past DRAFT into IN_REVIEW. */}
                <button type="button" className="knowledge-primary" disabled={busy || draftDetail.status !== "IN_REVIEW"}
                  onClick={() => void decide("approve")}>{t("Approve")}</button>
                <button type="button" disabled={busy || draftDetail.status !== "IN_REVIEW"}
                  onClick={() => void decide("reject")}>{t("Reject")}</button>
              </div>}
            </div>}
          </>}

        <h4>{t("Published graph")}</h4>
        <div className="knowledge-formbar">
          <label>{t("Filter by name")}<input value={filter} disabled={busy}
            onChange={event => setFilter(event.target.value)} /></label>
          <button type="button" disabled={busy} onClick={() => void perform(() =>
            loadGraph(filter.trim() ? { name_contains: filter.trim() } : {}))}>{t("Search")}</button>
          <button type="button" disabled={busy} onClick={() => void perform(async () => {
            setFilter(""); setEntity(""); await loadGraph();
          })}>{t("Show everything")}</button>
        </div>
        {!graph?.entities.length ? <p className="knowledge-empty">{t("The graph is empty until a draft is approved.")}</p> : <>
          <p className="knowledge-meta">{t("{v0} entities · {v1} relations{v2}", {
            v0: graph.entities.length, v1: graph.relations.length,
            v2: graph.truncated ? t(" · result limited") : "" })}</p>
          <div className="knowledge-graph">
            <GraphMap entities={graph.entities} relations={graph.relations} selected={entity}
              onSelect={setEntity} onExpand={expand} />
            <div className="knowledge-graphside">
              <ul className="knowledge-list">
                {graph.entities.map(item => <li key={item.id}>
                  <button type="button" aria-pressed={entity === item.id} disabled={busy}
                    onClick={() => setEntity(item.id)}>
                    <span>{item.name}</span><small>{item.type}</small>
                  </button>
                </li>)}
              </ul>
              {selectedEntity && <div className="knowledge-detail">
                <h4>{selectedEntity.name}</h4>
                <p className="knowledge-meta">{selectedEntity.type}</p>
                <button type="button" disabled={busy} onClick={() => expand(selectedEntity.id)}>
                  {t("Show neighbours")}</button>
                {!!Object.keys(selectedEntity.properties ?? {}).length && <pre>{pretty(selectedEntity.properties)}</pre>}
                <ul className="knowledge-relations">
                  {graph.relations.filter(relation => relation.source_id === selectedEntity.id
                      || relation.target_id === selectedEntity.id).map(relation => {
                    const name = (id: string) => graph.entities.find(item => item.id === id)?.name ?? shortId(id);
                    return <li key={relation.id}>{name(relation.source_id)} →{relation.type}→ {name(relation.target_id)}</li>;
                  })}
                </ul>
              </div>}
            </div>
          </div>
        </>}
        </div>
      </WorkspaceSection>}
    </div>
    </div>
  </div>
  </div>;
}

export default { apiVersion: 1, views: { preview: Preview, workspace: Workspace } } satisfies FrontendPlugin;
