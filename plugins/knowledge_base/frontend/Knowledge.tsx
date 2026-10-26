import { t, WorkspaceEmptyState, WorkspaceToolbar } from "@oaw/plugin-api";
import type { PluginViewProps } from "@oaw/plugin-api";
import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { ArrowRight, Check, CheckCircle2, ClipboardCheck, FileText, Loader2, LockKeyhole, Network, Pencil, Plus, Sparkles, Trash2 } from "lucide-react";
import { GraphMap } from "./GraphMap";
import type { Entity, Relation } from "./GraphMap";
import "./KnowledgeFlow.css";

/** One element of a knowledge draft as the store keeps it: a content-keyed id and
 * the sources it was read from. Concepts carry aliases; relations a free predicate,
 * MKB's evidence level (1 causal … 4 inferred) and the quote that states them. */
export type DraftEntity = { id: string; name: string; description: string | null; aliases: string[]; source_ids: string[] };
export type DraftRelation = {
  id: string; source: string; target: string; predicate: string; description: string | null;
  evidence_level: number; snippet: string | null; source_ids: string[];
};
/** One review run: the typed operations a model proposed and the card applied. */
export type ReviewEntry = {
  at: string; actor: string | null; operations: Record<string, unknown>[]; skipped: Record<string, unknown>[];
  before: { concepts: number; relations: number }; after: { concepts: number; relations: number };
};
export type KnowledgeDraft = {
  id: string; group_id: string; title: string; entities: DraftEntity[]; relations: DraftRelation[];
  status: "draft" | "published"; revision: number; run_id: string | null; review_log: ReviewEntry[]; created_by: string | null;
  created_at: string; updated_at: string;
  sources: { source_id: string; filename: string | null; extracted_at: string | null; missing: boolean }[];
};
/** One progress line of a streamed bridge request: where a long extraction or
 * review is, for the card to show while it waits. */
export type BridgeProgress = { stage: string; message?: string; done?: number; total?: number | null } & Record<string, unknown>;
/** Offered as suggestions in the predicate editor; any predicate the source supports is kept. */
export const SUGGESTED_PREDICATES = ["related_to", "part_of", "causes", "improves", "degrades", "measured_by",
  "is_a", "has_property", "used_in", "produced_by", "depends_on"] as const;
const EVIDENCE_LEVELS: Record<number, string> = { 1: "causal", 2: "observed", 3: "correlative", 4: "inferred" };
type Graph = { entities: Entity[]; relations: Relation[]; truncated: boolean };
type Pending = { args: Record<string, unknown>; reasons: string[] };
type SourceItem = { id: string; filename: string; group_name: string | null };
type Agent = { id: string; name: string };
type Edit = { title: string; entities: DraftEntity[]; relations: DraftRelation[] };
type WorkflowStep = "extract" | "review" | "publish";
type AgentRun = { agent_id: string; draft_id: string; kind: "extract" | "review" };
type ViewDraft = { draftId: string; creating: boolean; picked: string[]; title: string;
  workflowStep?: WorkflowStep; edit?: Edit; extracting?: AgentRun };

type Props = {
  host: PluginViewProps["host"]; cardId: string; deployed: boolean; busy: boolean;
  /** Bumped by the parent's refresh so this section reloads with the rest of the card. */
  version: number;
  /** Converted sources in the current group scope — what a draft can be opened over. */
  sources: SourceItem[]; activeGroup: string; groupCount: number;
  groupArgs: () => Record<string, unknown>; groupFilter: () => Record<string, unknown>;
  call: (action: string, args?: Record<string, unknown>, confirm?: boolean) => Promise<Record<string, unknown>>;
  perform: (work: () => Promise<void>) => Promise<void>;
  request: (path: string, body: unknown, deployed: boolean, onProgress?: (event: BridgeProgress) => void) => Promise<Record<string, unknown>>;
  /** An agent connected with the Knowledge extract relationship, if any. */
  extractAgent?: Agent; canRunAgent: boolean; selectedModel: string;
  modelSelect: ReactNode; groupSelect: ReactNode; refreshControls: ReactNode; banner: ReactNode;
  setNotice: (value: string) => void; setError: (value: string) => void;
  /** Open one source in the Sources tab. */
  onOpenSource: (sourceId: string) => void;
  /** Move to Sources to add or convert a document. */
  onAddSources?: () => void;
  active?: boolean;
  /** A one-shot request to continue work with exactly these sources. */
  suggestedSourceIds?: string[];
  onSourcesHandled?: () => void;
};

const shortId = (value: string) => value.length > 13 ? `${value.slice(0, 8)}…` : value;
const label = (value: string) => value.replaceAll("_", " ");

export function KnowledgeSection({
  host, cardId, deployed, busy, version, sources, activeGroup, groupCount, groupArgs, groupFilter, call, perform, request,
  extractAgent, canRunAgent, selectedModel, modelSelect, groupSelect, refreshControls, banner, setNotice, setError, onOpenSource,
  onAddSources, suggestedSourceIds, onSourcesHandled, active = true,
}: Props) {
  // The host scopes transient view state to this card/session. It survives closing
  // a workspace without introducing a durable setting or another storage owner.
  const [restored] = useState(() => host.draft?.get()?.knowledgeView as ViewDraft | undefined);
  const [drafts, setDrafts] = useState<KnowledgeDraft[]>([]);
  const [draftId, setDraftId] = useState(restored?.draftId ?? "");
  const [graph, setGraph] = useState<Graph>();
  const [entity, setEntity] = useState("");
  // A selected published relation: read like a node — statement, evidence, snippet, sources.
  const [relationId, setRelationId] = useState("");
  const [side, setSide] = useState<"concepts" | "relations">("concepts");
  const [filter, setFilter] = useState("");
  const [creating, setCreating] = useState(restored?.creating ?? false);
  const [picked, setPicked] = useState<string[]>(restored?.picked ?? []);
  const [title, setTitle] = useState(restored?.title ?? "");
  const [edit, setEdit] = useState<Edit | undefined>(restored?.edit);
  const [pending, setPending] = useState<Pending>();
  // A graph review the model proposed and the person has not confirmed yet.
  const [proposal, setProposal] = useState<{ operations: Record<string, unknown>[]; drafts: string[] }>();
  const [replace, setReplace] = useState(false);
  // A running agent extraction or review: which agent to watch until it is idle again.
  const [extracting, setExtracting] = useState<AgentRun | undefined>(restored?.extracting);
  // Where a streamed bridge run is: shown as a progress strip until it finishes.
  const [progress, setProgress] = useState<{ kind: "extract" | "review" | "graph"; event: BridgeProgress }>();
  const [workflowStep, setWorkflowStep] = useState<WorkflowStep | undefined>(restored?.workflowStep);
  const [lastFailure, setLastFailure] = useState<string>();
  const [loaded, setLoaded] = useState(false);
  const working = !!progress || !!extracting;
  const generation = useRef(0);
  const appliedSourceSuggestion = useRef<string[]>();
  const evidenceRef = useRef<HTMLHeadingElement>(null);
  const graphRef = useRef<HTMLElement>(null);
  const previousDraftId = useRef(draftId);

  useEffect(() => {
    if (previousDraftId.current !== draftId) {
      setEdit(undefined); setPending(undefined); setWorkflowStep(undefined);
      previousDraftId.current = draftId;
    }
  }, [draftId]);

  useEffect(() => {
    host.draft?.set({ ...host.draft.get(), knowledgeView: { draftId, creating, picked, title, workflowStep, edit, extracting } satisfies ViewDraft });
  }, [host.draft, draftId, creating, picked, title, workflowStep, edit, extracting]);

  const matchingDraft = (ids: string[], name?: string) => {
    const matches = drafts.filter(item => item.sources.length === new Set(ids).size
      && item.sources.every(source => ids.includes(source.source_id)) && (!name || item.title === name));
    return matches.find(item => item.id === draftId) ?? matches.find(item => item.entities.length) ?? matches[0];
  };
  const selectDraft = (item: KnowledgeDraft) => {
    setDraftId(item.id); setCreating(false); setPicked([]); setTitle(""); setEdit(undefined);
    setPending(undefined); setReplace(false); setWorkflowStep(undefined); setLastFailure(undefined);
  };

  useEffect(() => {
    if (!loaded || !suggestedSourceIds || appliedSourceSuggestion.current === suggestedSourceIds) return;
    appliedSourceSuggestion.current = suggestedSourceIds;
    const existing = matchingDraft(suggestedSourceIds);
    if (existing) selectDraft(existing);
    else { setPicked([...new Set(suggestedSourceIds)]); setTitle(""); setCreating(true); setPending(undefined); setReplace(false); }
    onSourcesHandled?.();
    // The request is consumed exactly once, after the saved drafts are available.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loaded, suggestedSourceIds, drafts, onSourcesHandled]);

  const loadGraph = useCallback(async (args: Record<string, unknown> = {}) => {
    const current = generation.current;
    // full: the map and the detail pane read each element's properties; the agent's
    // compact view omits them to fit the host's tool-result window.
    const result = await call("graph", { layer: "literature", limit: 500, full: true, ...groupFilter(), ...args });
    if (generation.current === current) setGraph(result as unknown as Graph);
  }, [call, groupFilter]);

  const load = useCallback(async () => {
    const current = ++generation.current;
    const [list, map] = await Promise.allSettled([
      call("knowledge_drafts", { operation: "list", limit: 200, ...groupArgs() }),
      call("graph", { layer: "literature", limit: 500, full: true, ...groupFilter() }),
    ]);
    if (generation.current !== current) return;
    if (list.status === "fulfilled") {
      const items = (list.value.drafts as KnowledgeDraft[]) ?? [];
      setDrafts(items); setLoaded(true);
      setDraftId(previous => items.some(item => item.id === previous) ? previous
        : items.find(item => item.entities.length)?.id ?? items[0]?.id ?? "");
    }
    if (map.status === "fulfilled") setGraph(map.value as unknown as Graph);
    const failure = [list, map].find(result => result.status === "rejected");
    if (failure) throw (failure as PromiseRejectedResult).reason;
  }, [call, groupArgs, groupFilter]);

  useEffect(() => { if (active && !working) void perform(load); }, [load, perform, version, active, working]);
  useEffect(() => () => { generation.current += 1; }, []);

  const draft = drafts.find(item => item.id === draftId);
  const pendingSources = draft?.sources.filter(item => !item.extracted_at && !item.missing) ?? [];
  // A group change or refresh can remove a selected source from this scope. Keep
  // the form mounted, but never submit invisible or no-longer-converted files.
  const selectedSourceIds = picked.filter(id => sources.some(source => source.id === id));
  const existingSelection = selectedSourceIds.length ? matchingDraft(selectedSourceIds, title.trim()) : undefined;

  const create = () => perform(async () => {
    if (!selectedSourceIds.length) throw new Error(t("Select at least one converted source first"));
    if (existingSelection) { selectDraft(existingSelection); setNotice(""); return; }
    const result = await call("knowledge_draft_manage", { operation: "open", source_ids: selectedSourceIds,
      ...(title.trim() ? { title: title.trim() } : {}), ...(activeGroup ? { group_id: activeGroup } : {}) });
    const created = result.draft as KnowledgeDraft;
    setCreating(false); setPicked([]); setTitle("");
    setDraftId(created.id); setEdit(undefined); setPending(undefined); setReplace(false); setWorkflowStep("extract"); setLastFailure(undefined);
    setNotice("");
    await load();
  });

  // Two ways to extract, as for records and insights: a connected agent holding the
  // Knowledge extract relationship is a real Agent run the card watches until idle;
  // otherwise one JSON-mode call per source through the host bridge.
  const extract = (target: KnowledgeDraft) => perform(async () => {
    setWorkflowStep("extract"); setLastFailure(undefined);
    const sourceIds = (replace ? target.sources : target.sources.filter(item => !item.extracted_at))
      .filter(item => !item.missing).map(item => item.source_id);
    if (!sourceIds.length) throw new Error(t("Every source of this draft is extracted already; tick Replace to read them again"));
    if (extractAgent && host.runAgent && !deployed) {
      const body = { draft_id: target.id, title: target.title, source_ids: sourceIds, knowledge_card: cardId, replace };
      await host.runAgent(extractAgent.id, [
        "Perform the knowledge extraction described by the JSON data below.",
        "KNOWLEDGE EXTRACTION REQUEST", JSON.stringify(body), "END KNOWLEDGE EXTRACTION REQUEST",
        "For each source_id in order: call knowledge_source_outline to see its sections. Then read it section by section —",
        "knowledge_extract_prompt with that source_id, this draft_id and section=<index> (group short neighbouring sections",
        "in one call; follow has_more with offset inside a long one). Before naming the concepts of a section, call",
        "knowledge_lookup with its key terms and reuse the names, aliases and predicates it returns from the draft and the",
        "published graph. Name the concepts the section is about (concepts only — no values, measurements or metadata) and",
        "the relations it states between them as free active-voice predicates, each with an evidence_level 1-4 and a short",
        "quoted snippet, and save them with knowledge_save for that source after each section or group of sections",
        `(pass replace=true only on the very first save when replace is ${replace ? "true" : "false"}).`,
        "Then report what you added and what was unclear.",
      ].join("\n"));
      setExtracting({ agent_id: extractAgent.id, draft_id: target.id, kind: "extract" });
      setNotice(t("{v0} is reading {v1} source(s) into \"{v2}\"…", { v0: extractAgent.name, v1: sourceIds.length, v2: target.title }));
      return;
    }
    if (!selectedModel) throw new Error(deployed
      ? t("This deployment has no default model configured yet")
      : t("Configure a model connection first, or connect an Agent with the Knowledge extract relationship"));
    setProgress({ kind: "extract", event: { stage: "start", message: t("Starting…") } });
    let result: Record<string, unknown>;
    try {
      result = await request(`knowledge/${cardId}/knowledge`, { draft_id: target.id, model: selectedModel, replace }, deployed,
        event => setProgress({ kind: "extract", event }));
    } catch (error) {
      setLastFailure(error instanceof Error ? error.message : String(error));
      throw error;
    } finally { setProgress(undefined); }
    const read = result.sources as { entities: number; relations: number; chunks: number; truncated: boolean }[];
    const added = read.reduce((sum, item) => sum + item.entities, 0);
    const chunks = read.reduce((sum, item) => sum + item.chunks, 0);
    setNotice(read.some(item => item.truncated)
      ? t("Read {v0} source(s) in {v1} part(s), some only in part · {v2} concepts added", { v0: read.length, v1: chunks, v2: added })
      : t("Read {v0} source(s) in {v1} part(s) · {v2} concepts added · review and publish below", { v0: read.length, v1: chunks, v2: added }));
    setEdit(undefined);
    setWorkflowStep("review");
    await load();
  });

  // Review the published layer as a whole: the bridge returns the model's proposed
  // operations; nothing changes until the person confirms, because they rewrite
  // published knowledge across every draft that asserts the affected elements.
  const reviewGraph = () => perform(async () => {
    if (!selectedModel) throw new Error(t("Configure a model connection first"));
    setProgress({ kind: "graph", event: { stage: "start", message: t("Starting…") } });
    let result: Record<string, unknown>;
    try {
      result = await request(`knowledge/${cardId}/graph-audit`, { model: selectedModel, ...groupArgs() }, deployed,
        event => setProgress({ kind: "graph", event }));
    } finally { setProgress(undefined); }
    const operations = (result.operations as Record<string, unknown>[]) ?? [];
    if (!operations.length) { setProposal(undefined); setNotice(t("The review found nothing to change in the published knowledge")); return; }
    setProposal({ operations, drafts: (result.drafts as string[]) ?? [] });
  });

  const applyGraphReview = () => proposal && perform(async () => {
    const result = await call("knowledge_graph_audit_apply", { operations: proposal.operations, ...groupArgs() }, true);
    setProposal(undefined); setEntity(""); setEdit(undefined);
    const written = result.graph as { entities: number; relations: number };
    setNotice(t("Review applied {v0} operation(s) to {v1} published draft(s) · the graph now holds {v2} concepts and {v3} relations", {
      v0: result.applied as number, v1: (result.drafts as string[]).length, v2: written.entities, v3: written.relations }));
    await load();
  });

  // Review a draft before publishing: the card computes findings (predicate
  // distribution, look-alike names, isolated concepts, matches in the published graph),
  // a model proposes typed operations, the card applies them and keeps a log.
  const review = (target: KnowledgeDraft) => perform(async () => {
    setWorkflowStep("review"); setLastFailure(undefined);
    if (extractAgent && host.runAgent && !deployed) {
      await host.runAgent(extractAgent.id, [
        "Review the knowledge draft described by the JSON data below before a person publishes it.",
        "KNOWLEDGE REVIEW REQUEST", JSON.stringify({ draft_id: target.id, title: target.title, knowledge_card: cardId }), "END KNOWLEDGE REVIEW REQUEST",
        "Call knowledge_audit_prompt with this draft_id and follow its instruction: standardize synonymous predicates, merge",
        "concepts that denote the same thing (prefer a name the published graph already uses), delete unsupported or",
        "duplicate relations and metadata-only concepts, and flag what you are unsure of. When a merge or deletion needs",
        "checking, read the relevant source section with knowledge_extract_prompt first. Submit the operations with one",
        "knowledge_audit_apply call, then report what you changed and what the person should check.",
      ].join("\n"));
      setExtracting({ agent_id: extractAgent.id, draft_id: target.id, kind: "review" });
      setNotice(t("{v0} is reviewing \"{v1}\"…", { v0: extractAgent.name, v1: target.title }));
      return;
    }
    if (!selectedModel) throw new Error(deployed
      ? t("This deployment has no default model configured yet")
      : t("Configure a model connection first, or connect an Agent with the Knowledge extract relationship"));
    setProgress({ kind: "review", event: { stage: "start", message: t("Starting…") } });
    let result: Record<string, unknown>;
    try {
      result = await request(`knowledge/${cardId}/audit`, { draft_id: target.id, model: selectedModel }, deployed,
        event => setProgress({ kind: "review", event }));
    } catch (error) {
      setLastFailure(error instanceof Error ? error.message : String(error));
      throw error;
    } finally { setProgress(undefined); }
    const applied = result.applied as number;
    setNotice(applied ? t("Review applied {v0} change(s) · read the review log below", { v0: applied }) : t("The review found nothing to change"));
    setEdit(undefined);
    await load();
  });

  useEffect(() => {
    if (!extracting) return;
    let active = true;
    const timer = window.setInterval(() => {
      host.getAgentInfo(extracting.agent_id).then(async info => {
        const state = info as { status?: string; active_run_id?: string | null; last_error?: string | null };
        if (!active || state.status === "running" || state.active_run_id) return;
        active = false; window.clearInterval(timer);
        setExtracting(undefined); setEdit(undefined);
        setWorkflowStep(undefined);
        if (state.status === "error") {
          const failure = state.last_error || t("The agent stopped before finishing. Check its conversation and try again.");
          setLastFailure(failure); setError(failure);
        } else setNotice(t("The agent run finished · check the draft results below"));
        await perform(load);
      }).catch(() => undefined);
    }, 2500);
    return () => { active = false; window.clearInterval(timer); };
  }, [extracting, host, perform, load, setNotice, setError]);

  const beginEdit = (target: KnowledgeDraft) => setEdit({
    title: target.title, entities: target.entities.map(item => ({ ...item })), relations: target.relations.map(item => ({ ...item })) });

  const saveEdit = (target: KnowledgeDraft) => edit && perform(async () => {
    if (!edit.title.trim()) throw new Error(t("Give the draft a title first"));
    if (edit.entities.some(item => !item.name.trim())) throw new Error(t("Every concept needs a name"));
    await call("knowledge_update", { draft_id: target.id, title: edit.title.trim(), entities: edit.entities,
      relations: edit.relations, expected_revision: target.revision });
    setEdit(undefined);
    setWorkflowStep("review");
    setLastFailure(undefined);
    setNotice(t("Changes saved"));
    await load();
  });

  const removeEntity = (id: string) => edit && setEdit({ ...edit,
    entities: edit.entities.filter(item => item.id !== id),
    relations: edit.relations.filter(item => item.source !== id && item.target !== id) });

  const addEntity = () => edit && setEdit({ ...edit, entities: [...edit.entities,
    { id: `new:${Date.now()}`, name: "", description: null, aliases: [], source_ids: [] }] });

  const addRelation = () => edit && edit.entities.length >= 2 && setEdit({ ...edit, relations: [...edit.relations,
    { id: `new:${Date.now()}`, source: edit.entities[0].id, target: edit.entities[1].id, predicate: "related_to",
      description: null, evidence_level: 3, snippet: null, source_ids: [] }] });

  const editEntity = (id: string, patch: Partial<DraftEntity>) => edit && setEdit({ ...edit,
    entities: edit.entities.map(row => row.id === id ? { ...row, ...patch } : row) });
  const editRelation = (id: string, patch: Partial<DraftRelation>) => edit && setEdit({ ...edit,
    relations: edit.relations.map(row => row.id === id ? { ...row, ...patch } : row) });

  const publish = (target: KnowledgeDraft, confirmed?: Pending) => perform(async () => {
    setWorkflowStep("publish"); setLastFailure(undefined);
    const args = confirmed?.args ?? { operation: "publish", draft_id: target.id, expected_revision: target.revision };
    const result = await call("knowledge_review", args, !!confirmed);
    if (result.status === "confirmation_required") {
      setPending({ args, reasons: (result.reasons as string[]) ?? [] });
      return;
    }
    setPending(undefined);
    const written = result.graph as { entities: number; relations: number };
    setNotice(t("Published \"{v0}\": {v1} concepts and {v2} relations are now in the graph", {
      v0: target.title, v1: written.entities, v2: written.relations }));
    await load();
  });

  const unpublish = (target: KnowledgeDraft) => perform(async () => {
    await call("knowledge_review", { operation: "unpublish", draft_id: target.id });
    setEntity(""); setWorkflowStep("review");
    setNotice(t("\"{v0}\" is a draft again and is out of the graph", { v0: target.title }));
    await load();
  });

  const remove = (target: KnowledgeDraft) => perform(async () => {
    await call("knowledge_draft_manage", { operation: "delete", draft_id: target.id });
    if (draftId === target.id) { setDraftId(""); setEdit(undefined); }
    setNotice(t("Draft deleted"));
    await load();
  });

  const expand = (id: string) => {
    setEntity(id); setRelationId("");
    void perform(() => loadGraph({ operation: "traverse", entity_id: id, max_depth: 2 }));
  };
  const selectEntity = (id: string) => { setEntity(id); setRelationId(""); };
  const selectRelation = (id: string) => { setRelationId(id); setEntity(""); setSide("relations"); };

  const selectedEntity = graph?.entities.find(item => item.id === entity);
  const selectedRelation = graph?.relations.find(item => item.id === relationId);
  const graphName = (id: string) => graph?.entities.find(item => item.id === id)?.name ?? shortId(id);
  const relationProperty = (relation: Relation | undefined, key: string) => {
    const value = relation?.properties?.[key];
    return typeof value === "string" && value ? value : undefined;
  };
  const sourceName = (id: string) => sources.find(item => item.id === id)?.filename
    ?? draft?.sources.find(item => item.source_id === id)?.filename ?? shortId(id);
  const sourceChips = (ids: string[]) => <span className="knowledge-insight-evidence">
    {ids.map(id => <button key={id} type="button" className="knowledge-chip" disabled={busy}
      title={t("Open the source")} onClick={() => onOpenSource(id)}>{sourceName(id)}</button>)}
  </span>;
  const entityName = (list: DraftEntity[], id: string) => list.find(item => item.id === id)?.name || shortId(id);
  const blocker = !draft ? "" : draft.status === "published" ? t("Unpublish this draft before extracting again.")
    : working ? t("An extraction is already running.")
    : (!canRunAgent && !selectedModel) ? (deployed ? t("This deployment has no default model configured yet.")
      : t("Configure a model connection in OAW Settings, or connect an Agent to this card with the Knowledge extract relationship."))
    : "";
  const elements = (target: KnowledgeDraft) => t("{v0} concepts · {v1} relations", { v0: target.entities.length, v1: target.relations.length });
  const runningStep = extracting?.draft_id === draft?.id ? extracting?.kind
    : progress?.kind === "extract" || progress?.kind === "review" ? progress.kind : undefined;
  const currentStep = draft?.status === "published" ? "publish" : runningStep
    ?? (!draft?.entities.length ? "extract" : workflowStep ?? (pendingSources.length ? "extract" : "review"));
  const startDraft = () => { setCreating(true); setPicked([]); setTitle(""); setPending(undefined); setReplace(false); };
  const extractionComplete = !!draft?.sources.length && draft.sources.every(item => item.extracted_at && !item.missing);
  const workflowStages = [
    { id: "extract" as const, label: t("Extract"), accessible: t("Extraction stage"), icon: Sparkles,
      complete: extractionComplete, detail: t("{v0} / {v1} sources read", {
        v0: draft?.sources.filter(item => item.extracted_at && !item.missing).length ?? 0, v1: draft?.sources.length ?? 0 }) },
    { id: "review" as const, label: t("Review"), accessible: t("Review stage"), icon: ClipboardCheck,
      complete: false, detail: draft?.review_log.length
        ? t("{v0} review(s) recorded", { v0: draft.review_log.length })
        : draft?.entities.length ? t("Check source evidence") : t("After extraction") },
    { id: "publish" as const, label: t("Publish"), accessible: t("Publication stage"), icon: Network,
      complete: draft?.status === "published", detail: draft?.status === "published" ? t("In the graph")
        : draft?.entities.length ? t("Your confirmation") : t("Needs extracted concepts") },
  ];
  // A determinate bar when the bridge knows how many steps remain, an indeterminate
  // one for an agent run or a step of unknown length; always with the current stage.
  const progressStrip = (kind: "extract" | "review" | "graph") => {
    const streamed = progress?.kind === kind ? progress.event : undefined;
    const agent = extracting?.draft_id === draft?.id && extracting
      && ((kind === "extract" && extracting.kind === "extract") || (kind === "review" && extracting.kind === "review"));
    if (!streamed && !agent) return null;
    const total = typeof streamed?.total === "number" && streamed.total > 0 ? streamed.total : undefined;
    const done = typeof streamed?.done === "number" ? streamed.done : 0;
    const message = streamed?.message ?? (agent ? t("{v0} is working… the card refreshes when it finishes", { v0: extractAgent?.name ?? t("The agent") }) : t("Working…"));
    return <div className="knowledge-progress" role="status" aria-live="polite">
      {total ? <progress value={Math.max(0, Math.min(done, total))} max={total} aria-label={message} /> : <progress aria-label={message} />}
      <span>{message}{total ? <small> · {t("{v0} of {v1}", { v0: Math.min(done, total), v1: total })}</small> : null}</span>
    </div>;
  };
  const describeOperation = (item: Record<string, unknown>) => {
    const text = (key: string) => String(item[key] ?? "");
    switch (item.op) {
      case "merge_concepts": return t("Merged {v0} into {v1}", {
        v0: ((item.merged ?? item.drop) as string[] ?? []).join(", "), v1: text(item.into ? "into" : "keep") })
        + (item.renamed ? ` (${t("renamed from {v0}", { v0: text("renamed") })})` : "");
      case "rename_predicate": return t("Renamed predicate {v0} → {v1}", { v0: label(text("from")), v1: label(text("to")) })
        + (item.count !== undefined ? ` (${t("{v0} relation(s)", { v0: String(item.count) })})` : "");
      case "add_alias": return t("Added alias {v0} to {v1}", { v0: text("alias"), v1: text("name") });
      case "delete_relation": return t("Deleted relation {v0} {v1} {v2}", { v0: text("source"), v1: label(text("predicate")), v2: text("target") });
      case "set_predicate": return t("Retyped {v0} {v1} {v2} → {v3}", { v0: text("source"), v1: label(text("predicate")), v2: text("target"), v3: label(text("to")) });
      case "delete_concept": return t("Deleted concept {v0}", { v0: text("name") });
      case "set_evidence": return t("Set evidence of {v0} {v1} {v2} to {v3}", { v0: text("source"), v1: label(text("predicate")), v2: text("target"), v3: text("level") });
      case "flag": return t("Flagged: {v0}", { v0: text("message") });
      default: return text("op");
    }
  };

  return <>
    <WorkspaceToolbar label={t("Knowledge")} className="knowledge-section-toolbar">
      {groupSelect}
      <div className="knowledge-heading-actions">
        {refreshControls}
        <button type="button" className={drafts.length ? "knowledge-primary" : undefined} disabled={busy || working || creating} onClick={startDraft}>
          <Plus size={14} />{t("New draft")}</button>
      </div>
    </WorkspaceToolbar>
    {banner}

    {creating && <div className="knowledge-form knowledge-draft-form knowledge-flow-create">
      <div className="knowledge-flow-heading"><span className="knowledge-flow-symbol"><Network size={22} /></span>
        <div><h4>{t("What do you want to understand?")}</h4>
          <p>{t("Bring related sources into one knowledge draft.")}</p></div></div>
      <label>{t("Title")}<input value={title} disabled={busy} placeholder={t("For example, how processing affects conductivity")}
        onChange={event => setTitle(event.target.value)} /></label>
      <div className="knowledge-flow-picker-heading"><strong>{t("Choose sources")}</strong>
        {!!sources.length && <span>{t("{v0} selected", { v0: selectedSourceIds.length })}</span>}</div>
      {!sources.length ? <div className="knowledge-flow-source-empty"><FileText size={26} />
        <p>{t("Convert a source in Sources first.")}</p>
        {onAddSources && <button type="button" disabled={busy} onClick={onAddSources}>{t("Open Sources")}<ArrowRight size={14} /></button>}
      </div> : <ul className="knowledge-flow-source-picker">
        {sources.map(item => <li key={item.id}>
          <label className={`knowledge-flow-source-option${picked.includes(item.id) ? " is-selected" : ""}`}>
            <input type="checkbox" checked={picked.includes(item.id)} disabled={busy} aria-label={t("Select {v0}", { v0: item.filename })}
            onChange={event => setPicked(event.target.checked ? [...picked, item.id] : picked.filter(value => value !== item.id))} />
            <FileText size={20} aria-hidden="true" />
            <span><strong>{item.filename}</strong>
              {!activeGroup && item.group_name && groupCount > 1 && <small>{item.group_name}</small>}</span>
            {picked.includes(item.id) && <Check size={16} aria-hidden="true" />}
          </label>
        </li>)}
      </ul>}
      <div className="knowledge-formbar">
        <button type="button" className="knowledge-primary" disabled={busy || !selectedSourceIds.length} onClick={() => void create()}>
          {existingSelection ? t("Continue existing draft") : t("Create draft from {v0} source(s)", { v0: selectedSourceIds.length })}</button>
        <button type="button" disabled={busy} onClick={() => setCreating(false)}>{t("Cancel")}</button>
      </div>
    </div>}

    <section className="knowledge-insight-block knowledge-flow-drafts" hidden={creating}>
      <h4>{t("Knowledge drafts")} {!!drafts.length && <span className="knowledge-total">{drafts.length}</span>}</h4>
      {!drafts.length && loaded && <div className="knowledge-flow-welcome">
        <div className="knowledge-flow-illustration" aria-hidden="true">
          <span><FileText size={24} /></span><i /><span><Sparkles size={24} /></span><i /><span><Network size={26} /></span>
        </div>
        <h3>{sources.length ? t("Connect what your sources know") : t("Your knowledge graph starts with a source")}</h3>
        <p>{sources.length ? t("Read related sources together, review their evidence, and publish the connections.")
          : t("Add a document and convert it into readable source material.")}</p>
        {sources.length ? <button type="button" className="knowledge-primary" disabled={busy} onClick={startDraft}>
          <Plus size={16} />{t("Start a draft")}<ArrowRight size={15} /></button>
          : onAddSources && <button type="button" className="knowledge-primary" disabled={busy} onClick={onAddSources}>
            <Plus size={16} />{t("Open Sources")}<ArrowRight size={15} /></button>}
      </div>}
      <div className={`knowledge-split${draft ? " has-detail" : ""}`}>
        {!!drafts.length && <div className="knowledge-source-pane">
          <ul className="knowledge-list knowledge-record-list">
            {drafts.map(item => <li key={item.id}>
              <button type="button" aria-pressed={draftId === item.id} disabled={busy || working}
                onClick={() => { selectDraft(item); setNotice(""); }}>
                <span><span className={`knowledge-badge knowledge-status-${item.status}`}>{item.status === "published" ? t("Published") : t("Draft")}</span> {item.title}</span>
                <small>{t("{v0} source(s)", { v0: item.sources.length })} · {elements(item)}</small>
                <span className="knowledge-draft-source-names">{item.sources.map(source => source.filename ?? shortId(source.source_id)).join(" · ")}</span>
                <span className="knowledge-flow-mini" aria-hidden="true">
                  <i data-complete={!!item.sources.length && item.sources.every(source => source.extracted_at && !source.missing)} />
                  <i /><i data-complete={item.status === "published"} />
                </span>
              </button>
            </li>)}
          </ul>
        </div>}

        {draft && <div className="knowledge-detail knowledge-draft-detail">
          {edit ? <label>{t("Title")}<input value={edit.title} disabled={busy} onChange={event => setEdit({ ...edit, title: event.target.value })} /></label>
            : <h4>{draft.title}</h4>}
          <div className="knowledge-flow-stages" aria-label={t("Knowledge draft workflow")}>
            {workflowStages.map((step, index) => <button key={step.id} type="button"
              className="knowledge-flow-step" aria-label={step.accessible} aria-current={currentStep === step.id ? "step" : undefined}
              title={draft.status === "published" ? t("Published draft: unpublish to change it")
                : step.id !== "extract" && !draft.entities.length ? t("Extract concepts before reviewing or publishing") : step.label}
              data-complete={step.complete} disabled={busy || working || !!edit || draft.status === "published" || (step.id !== "extract" && !draft.entities.length)}
              onClick={() => setWorkflowStep(step.id)}>
              <span className="knowledge-flow-step-icon">{runningStep === step.id ? <Loader2 className="knowledge-flow-spinner" size={18} />
                : step.complete ? <Check size={18} /> : step.id !== "extract" && !draft.entities.length ? <LockKeyhole size={18} /> : <step.icon size={18} />}</span>
              <span><strong>{index + 1}. {step.label}</strong><small>{step.detail}</small></span>
            </button>)}
          </div>

          {lastFailure && <div className="knowledge-flow-failure" role="status">
            <strong>{t("This step did not finish")}</strong><span>{lastFailure}</span></div>}

          {draft.status === "draft" && <section className="knowledge-flow-panel" hidden={currentStep !== "extract"}>
            <div className="knowledge-flow-panel-heading"><Sparkles size={20} /><h5>{t("Read the sources together")}</h5></div>
            <p>{canRunAgent && extractAgent ? t("{v0} will read the sources and connect their concepts, with evidence.", { v0: extractAgent.name })
              : t("Extract concepts and connections, each linked to its source evidence.")}</p>
            {currentStep === "extract" && !canRunAgent && !deployed && modelSelect}
            {deployed && <p className="knowledge-meta">{t("Model: {v0}", { v0: selectedModel || t("none configured") })}</p>}
            <div className="knowledge-formbar">
              <button type="button" className="knowledge-primary" disabled={busy || !!blocker || (!replace && !pendingSources.length)} onClick={() => void extract(draft)}>
                <Sparkles size={14} />{extracting?.kind === "extract" || progress?.kind === "extract" ? t("Extracting…") : replace ? t("Extract all {v0} again", { v0: draft.sources.length })
                  : t("Extract {v0} unread source(s)", { v0: pendingSources.length })}</button>
            </div>
            {draft.sources.some(item => item.extracted_at) && <details className="knowledge-flow-options">
              <summary>{t("Re-extraction options")}</summary>
              <label className="knowledge-checkrow"><input type="checkbox" checked={replace} disabled={busy || !!extracting} onChange={event => setReplace(event.target.checked)} />
                {t("Replace: empty the draft and read every source again")}</label>
            </details>}
            {progressStrip("extract")}
            {blocker && <p className="knowledge-empty">{blocker}</p>}
          </section>}

          {draft.status === "draft" && !!draft.entities.length && <section className="knowledge-flow-panel" hidden={currentStep !== "review"}>
            <div className="knowledge-flow-panel-heading"><ClipboardCheck size={20} /><h5>{t("Check the evidence")}</h5></div>
            <p>{t("Inspect the concepts and their source quotes. A model review can help find duplicates and unsupported connections.")}</p>
            {currentStep === "review" && !canRunAgent && !deployed && modelSelect}
            <div className="knowledge-formbar">
              {!deployed && <button type="button" className="knowledge-primary" disabled={busy || !!blocker} onClick={() => void review(draft)}>
                <ClipboardCheck size={14} />{extracting?.kind === "review" || progress?.kind === "review" ? t("Reviewing…") : t("Review draft")}</button>
              }
              <button type="button" disabled={busy || !!extracting} onClick={() => evidenceRef.current?.scrollIntoView({ block: "start" })}>
                {t("Inspect evidence")}<ArrowRight size={14} /></button>
              <button type="button" disabled={busy || !!extracting} onClick={() => setWorkflowStep("publish")}>
                {t("Ready to publish")}<ArrowRight size={14} /></button>
            </div>
            {progressStrip("review")}
            {!deployed && blocker && <p className="knowledge-empty">{blocker}</p>}
            {!!draft.review_log.length && <ul className="knowledge-list knowledge-compact knowledge-review-log">
              {[...draft.review_log].reverse().map((entry, index) => <li key={`${entry.at}-${index}`}>
                <div className="knowledge-review-entry">
                  <small>{new Date(entry.at).toLocaleString()} · {entry.actor ?? t("model")} · {t("{v0} → {v1} concepts, {v2} → {v3} relations", {
                    v0: entry.before.concepts, v1: entry.after.concepts, v2: entry.before.relations, v3: entry.after.relations })}</small>
                  <ul>
                    {entry.operations.map((item, position) => <li key={position} className={item.op === "flag" ? "knowledge-review-flag" : undefined}>
                      {describeOperation(item)}{item.reason ? <small> — {String(item.reason)}</small> : null}</li>)}
                    {entry.skipped.map((item, position) => <li key={`skipped-${position}`} className="knowledge-review-skipped">
                      {t("Skipped {v0}: {v1}", { v0: String(item.op), v1: String(item.skipped ?? "") })}</li>)}
                  </ul>
                </div>
              </li>)}
            </ul>}
          </section>}

          {currentStep === "publish" && <section className="knowledge-flow-panel knowledge-flow-publish">
            <div className="knowledge-flow-panel-heading">{draft.status === "published" ? <CheckCircle2 size={21} /> : <Network size={21} />}
              <h5>{draft.status === "published" ? t("Connected to your knowledge graph") : t("Make this draft part of your knowledge graph")}</h5></div>
            <p>{draft.status === "published" ? elements(draft) : t("Publishing adds these concepts and connections to the graph after your confirmation.")}</p>
            {draft.status === "published" && <button type="button" onClick={() => graphRef.current?.scrollIntoView({ block: "start" })}>
              {t("Explore the graph")}<ArrowRight size={14} /></button>}
          </section>}

          <details className="knowledge-flow-sources" open={!draft.entities.length || undefined}>
            <summary><FileText size={15} />{t("Sources")}<span className="knowledge-total">{draft.sources.length}</span></summary>
            <ul className="knowledge-list knowledge-compact">
              {draft.sources.map(item => <li key={item.source_id}>
                <button type="button" disabled={busy || item.missing} onClick={() => onOpenSource(item.source_id)}>
                  <span>{item.filename ?? shortId(item.source_id)}</span>
                  <small>{item.missing ? t("missing") : item.extracted_at ? t("extracted") : t("not read yet")}</small>
                </button>
              </li>)}
            </ul>
          </details>

          {(edit || !!draft.entities.length) && <h5 ref={evidenceRef}>{t("Concepts")} {!!(edit ?? draft).entities.length && <span className="knowledge-total">{(edit ?? draft).entities.length}</span>}</h5>}
          {edit && !edit.entities.length && <p className="knowledge-empty">{t("Nothing extracted yet.")}</p>}
          {!!(edit ?? draft).entities.length && <div className="knowledge-record-grid knowledge-elements"><table>
            <thead><tr><th scope="col">{t("Name")}</th><th scope="col">{t("Aliases")}</th><th scope="col">{t("Description")}</th><th scope="col">{t("Sources")}</th>{edit && <th scope="col" />}</tr></thead>
            <tbody>{(edit ?? draft).entities.map(item => <tr key={item.id}>
              <td>{edit ? <input value={item.name} disabled={busy} aria-label={t("Name")}
                  onChange={event => editEntity(item.id, { name: event.target.value })} />
                : <strong>{item.name}</strong>}</td>
              <td>{edit ? <input value={item.aliases.join(", ")} disabled={busy} aria-label={t("Aliases")} placeholder={t("comma-separated")}
                  onChange={event => editEntity(item.id, { aliases: event.target.value.split(",").map(value => value.trim()).filter(Boolean) })} />
                : item.aliases.length ? <span className="knowledge-insight-evidence">{item.aliases.map(alias => <span key={alias} className="knowledge-chip-schema">{alias}</span>)}</span> : "—"}</td>
              <td className="knowledge-kv-text">{item.description ?? "—"}</td>
              <td>{sourceChips(item.source_ids)}</td>
              {edit && <td><button type="button" className="knowledge-icon-button" aria-label={t("Remove {v0}", { v0: item.name || t("concept") })} disabled={busy}
                onClick={() => removeEntity(item.id)}><Trash2 size={14} /></button></td>}
            </tr>)}</tbody>
          </table></div>}
          {edit && <button type="button" disabled={busy} onClick={addEntity}><Plus size={13} />{t("Add concept")}</button>}

          {(edit || !!draft.relations.length) && <h5>{t("Relations")} {!!(edit ?? draft).relations.length && <span className="knowledge-total">{(edit ?? draft).relations.length}</span>}</h5>}
          {!!(edit ?? draft).relations.length && <div className="knowledge-record-grid knowledge-elements"><table>
            <thead><tr><th scope="col">{t("Source")}</th><th scope="col">{t("Predicate")}</th><th scope="col">{t("Target")}</th>
              <th scope="col">{t("Statement")}</th><th scope="col">{t("Evidence")}</th><th scope="col">{t("Snippet")}</th><th scope="col">{t("Sources")}</th>{edit && <th scope="col" />}</tr></thead>
            <tbody>{(edit ?? draft).relations.map(item => {
              const pick = (key: "source" | "target") => <select value={item[key]} disabled={busy} aria-label={key === "source" ? t("Source") : t("Target")}
                onChange={event => editRelation(item.id, { [key]: event.target.value })}>
                {edit?.entities.map(option => <option key={option.id} value={option.id}>{option.name || shortId(option.id)}</option>)}</select>;
              return <tr key={item.id}>
                <td>{edit ? pick("source") : entityName(draft.entities, item.source)}</td>
                <td>{edit ? <input value={item.predicate} disabled={busy} aria-label={t("Predicate")} list="knowledge-predicates"
                    onChange={event => editRelation(item.id, { predicate: event.target.value })} />
                  : <em>{label(item.predicate)}</em>}</td>
                <td>{edit ? pick("target") : entityName(draft.entities, item.target)}</td>
                <td className="knowledge-kv-text">{edit ? <input value={item.description ?? ""} disabled={busy} aria-label={t("Statement")}
                    placeholder={t("What the document claims about this pair")}
                    onChange={event => editRelation(item.id, { description: event.target.value || null })} />
                  : item.description ?? "—"}</td>
                <td>{edit ? <select value={item.evidence_level} disabled={busy} aria-label={t("Evidence")}
                    onChange={event => editRelation(item.id, { evidence_level: Number(event.target.value) })}>
                    {[1, 2, 3, 4].map(level => <option key={level} value={level}>{level} · {t(EVIDENCE_LEVELS[level])}</option>)}</select>
                  : <span className="knowledge-chip-schema" title={t(EVIDENCE_LEVELS[item.evidence_level] ?? "")}>{item.evidence_level}</span>}</td>
                <td className="knowledge-kv-text">{item.snippet ? <q>{item.snippet}</q> : "—"}</td>
                <td>{sourceChips(item.source_ids)}</td>
                {edit && <td><button type="button" className="knowledge-icon-button" aria-label={t("Remove relation")} disabled={busy}
                  onClick={() => setEdit({ ...edit, relations: edit.relations.filter(row => row.id !== item.id) })}><Trash2 size={14} /></button></td>}
              </tr>;
            })}</tbody>
          </table></div>}
          {edit && <button type="button" disabled={busy || edit.entities.length < 2} onClick={addRelation}><Plus size={13} />{t("Add relation")}</button>}
          {edit && <datalist id="knowledge-predicates">
            {[...new Set([...SUGGESTED_PREDICATES, ...edit.relations.map(item => item.predicate)])].map(value => <option key={value} value={value} />)}
          </datalist>}

          {edit ? <div className="knowledge-formbar">
            <button type="button" className="knowledge-primary" disabled={busy} onClick={() => void saveEdit(draft)}>{t("Save changes")}</button>
            <button type="button" disabled={busy} onClick={() => setEdit(undefined)}>{t("Cancel")}</button>
          </div> : pending ? <div role="alert" className="knowledge-confirm">
            <strong>{t("Publish this draft?")}</strong>
            <p>{pending.reasons.join(" ")}</p>
            <div className="knowledge-formbar">
              <button type="button" className="knowledge-primary" disabled={busy} onClick={() => void publish(draft, pending)}>{t("Confirm and publish")}</button>
              <button type="button" disabled={busy} onClick={() => setPending(undefined)}>{t("Cancel")}</button>
            </div>
          </div> : <div className="knowledge-formbar knowledge-draft-actions">
            {draft.status === "draft" ? <>
              <button type="button" disabled={busy || working} onClick={() => beginEdit(draft)}><Pencil size={13} />{t("Edit")}</button>
              <button type="button" className={currentStep === "publish" ? "knowledge-primary" : undefined} disabled={busy || working || !draft.entities.length}
                onClick={() => void publish(draft)}>{t("Publish")}</button>
              <button type="button" className="knowledge-danger" disabled={busy || working} onClick={() => void remove(draft)}>{t("Delete")}</button>
            </> : <button type="button" disabled={busy} onClick={() => void unpublish(draft)}>{t("Unpublish")}</button>}
          </div>}
          <details className="knowledge-flow-options"><summary>{t("Draft details")}</summary>
            <p className="knowledge-meta">{t("revision {v0} · by {v1}", { v0: draft.revision, v1: draft.created_by ?? "—" })}</p>
            {draft.status === "draft" && !edit && extractAgent && <p className="knowledge-meta">
              {t("You can also ask {v0} to change this draft in the conversation — refer to it as \"{v1}\".", { v0: extractAgent.name, v1: draft.title })}</p>}
          </details>
        </div>}
      </div>
    </section>

    <section className="knowledge-insight-block knowledge-flow-published" ref={graphRef}
      hidden={creating || (loaded && !drafts.length && !graph?.entities.length && !filter.trim())}>
      <h4>{t("Published knowledge")}</h4>
      {!deployed && !!graph?.entities.length && <div className="knowledge-formbar">
        <button type="button" disabled={busy || !!proposal || !!extracting} onClick={() => void reviewGraph()}>
          <ClipboardCheck size={14} />{progress?.kind === "graph" ? t("Reviewing…") : t("Review graph")}</button>
        <span className="knowledge-meta">{t("A model reviews the whole published layer — synonymous predicates, duplicate concepts, isolated or unsupported elements — and proposes changes you confirm before they are applied to every published draft and the graph is rebuilt.")}</span>
      </div>}
      {progressStrip("graph")}
      {proposal && <div role="alert" className="knowledge-confirm">
        <strong>{t("Apply this review to the published knowledge?")}</strong>
        <p>{t("{v0} operation(s) across {v1} published draft(s). Flags are notes for you and change nothing.", { v0: proposal.operations.length, v1: proposal.drafts.length })}</p>
        <ul className="knowledge-review-proposal">
          {proposal.operations.map((item, index) => <li key={index} className={item.op === "flag" ? "knowledge-review-flag" : undefined}>
            {describeOperation(item)}{item.reason ? <small> — {String(item.reason)}</small> : null}</li>)}
        </ul>
        <div className="knowledge-formbar">
          <button type="button" className="knowledge-primary" disabled={busy} onClick={() => void applyGraphReview()}>{t("Confirm and apply")}</button>
          <button type="button" disabled={busy} onClick={() => setProposal(undefined)}>{t("Cancel")}</button>
        </div>
      </div>}
      <form className="knowledge-formbar knowledge-graph-search"
        hidden={!graph?.entities.length && !drafts.some(item => item.status === "published") && !filter.trim()} onSubmit={event => {
        event.preventDefault();
        void perform(() => loadGraph(filter.trim() ? { name_contains: filter.trim(), semantic: false } : {}));
      }}>
        <input type="search" aria-label={t("Filter by name")} placeholder={t("Filter by name")} value={filter} disabled={busy}
          onChange={event => setFilter(event.target.value)} />
        <button type="submit" disabled={busy}>{t("Search")}</button>
        <button type="button" disabled={busy} onClick={() => void perform(async () => { setFilter(""); setEntity(""); setRelationId(""); await loadGraph(); })}>{t("Show everything")}</button>
      </form>
      {!graph?.entities.length ? <WorkspaceEmptyState className="knowledge-graph-empty" icon={<Network size={28} />}
        title={filter.trim() ? t("No matches") : t("No published knowledge yet")}
        description={filter.trim() ? t("Try another name or show everything.") : t("The graph is empty until a draft is published.")} /> : <>
        <p className="knowledge-meta">{t("{v0} entities · {v1} relations{v2}", {
          v0: graph.entities.length, v1: graph.relations.length, v2: graph.truncated ? t(" · result limited") : "" })}</p>
        <div className="knowledge-graph">
          <GraphMap graphKey={`knowledge:${cardId}`} entities={graph.entities} relations={graph.relations} selected={entity}
            onSelect={selectEntity} onExpand={expand} />
          <div className="knowledge-graphside">
            <div className="knowledge-tabstrip knowledge-side-tabs" role="tablist" aria-label={t("Published elements")}>
              <button type="button" role="tab" aria-selected={side === "concepts"} disabled={busy} onClick={() => setSide("concepts")}>
                {t("Concepts")} <span className="knowledge-total">{graph.entities.length}</span></button>
              <button type="button" role="tab" aria-selected={side === "relations"} disabled={busy} onClick={() => setSide("relations")}>
                {t("Relations")} <span className="knowledge-total">{graph.relations.length}</span></button>
            </div>
            {side === "concepts" ? <ul className="knowledge-list">
              {graph.entities.map(item => <li key={item.id}>
                <button type="button" aria-pressed={entity === item.id} disabled={busy} onClick={() => selectEntity(item.id)}>
                  <span>{item.name}</span><small>{label(item.type)}</small>
                </button>
              </li>)}
            </ul> : <ul className="knowledge-list knowledge-relation-list">
              {graph.relations.map(item => <li key={item.id}>
                <button type="button" aria-pressed={relationId === item.id} disabled={busy} onClick={() => selectRelation(item.id)}
                  aria-label={t("{v0} {v1} {v2}", { v0: graphName(item.source_id), v1: label(item.type), v2: graphName(item.target_id) })}>
                  <span>{graphName(item.source_id)} <em>{label(item.type)}</em> {graphName(item.target_id)}</span>
                  {relationProperty(item, "description") && <small>{relationProperty(item, "description")}</small>}
                </button>
              </li>)}
            </ul>}
            {selectedRelation && <div className="knowledge-detail knowledge-relation-detail">
              <h4>
                <button type="button" className="knowledge-link" disabled={busy} onClick={() => selectEntity(selectedRelation.source_id)}>{graphName(selectedRelation.source_id)}</button>
                {" "}<em>{label(selectedRelation.type)}</em>{" "}
                <button type="button" className="knowledge-link" disabled={busy} onClick={() => selectEntity(selectedRelation.target_id)}>{graphName(selectedRelation.target_id)}</button>
              </h4>
              <p className="knowledge-meta">{t("relation")}{typeof selectedRelation.properties?.evidence_level === "number"
                ? ` · ${t("evidence {v0}", { v0: selectedRelation.properties.evidence_level as number })} (${t(EVIDENCE_LEVELS[selectedRelation.properties.evidence_level as number] ?? "")})` : ""}</p>
              {relationProperty(selectedRelation, "description")
                ? <p>{relationProperty(selectedRelation, "description")}</p>
                : <p className="knowledge-empty">{t("No statement was recorded for this relation; re-extract its source to add one.")}</p>}
              {relationProperty(selectedRelation, "snippet") && <blockquote className="knowledge-snippet">{relationProperty(selectedRelation, "snippet")}</blockquote>}
              {Array.isArray(selectedRelation.properties?.source_ids) && sourceChips(selectedRelation.properties!.source_ids as string[])}
            </div>}
            {selectedEntity && <div className="knowledge-detail">
              <h4>{selectedEntity.name}</h4>
              <p className="knowledge-meta">{label(selectedEntity.type)}{Array.isArray(selectedEntity.properties.aliases) && (selectedEntity.properties.aliases as string[]).length
                ? ` · ${t("also: {v0}", { v0: (selectedEntity.properties.aliases as string[]).join(", ") })}` : ""}</p>
              {typeof selectedEntity.properties.description === "string" && <p>{selectedEntity.properties.description}</p>}
              {Array.isArray(selectedEntity.properties.source_ids) && sourceChips(selectedEntity.properties.source_ids as string[])}
              <button type="button" disabled={busy} onClick={() => expand(selectedEntity.id)}>{t("Show neighbours")}</button>
              <ul className="knowledge-relations">
                {graph.relations.filter(relation => relation.source_id === selectedEntity.id || relation.target_id === selectedEntity.id).map(relation => {
                  const statement = relationProperty(relation, "description");
                  return <li key={relation.id}>
                    <button type="button" className="knowledge-link" disabled={busy} onClick={() => selectRelation(relation.id)}
                      title={t("Read this relation")}>{graphName(relation.source_id)} →{label(relation.type)}→ {graphName(relation.target_id)}</button>
                    {statement && <small className="knowledge-statement">{statement}</small>}</li>;
                })}
              </ul>
            </div>}
          </div>
        </div>
      </>}
    </section>
  </>;
}
