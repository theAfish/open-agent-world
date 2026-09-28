import { useCallback, useEffect, useState } from "react";
import { useLocale, type PluginViewProps } from "@oaw/plugin-api";
import { useWorldStore } from "../../../frontend/src/state/worldStore";
import { useNodeSurfaceStore } from "../../../frontend/src/state/nodeSurfaces";
import { PaperPortal, type SourceLocation } from "../../library/frontend/PaperPortal";
import { safePaperUrl } from "../../library/frontend/PaperMetadata";
import { ScopeFrontiers, scopeRequest, type FrontierRecord } from "./ScopeFrontiers";
import { ScopeSnapshots } from "./ScopeSnapshots";
import { ResearchHub, type ResearchScopeDoc } from "./index";
import { branchEntities, filterExploration, scopePaperIds, type ExplorationEntity, type ExplorationKind } from "./explorationModel";
import { explorationText as l } from "./explorationCopy";
import { ExplorationRoads, FindingRoadMount } from "./ExplorationRoads";
import "./Exploration.css";

// Several small findings share one Scope document. Fetch once, rather than
// issuing a request for every card entering the viewport or changing zoom.
type ScopeCache = { value?: ResearchScopeDoc; pending?: Promise<ResearchScopeDoc>; at: number; listeners: Set<(doc: ResearchScopeDoc) => void> };
const scopeCache = new Map<string, ScopeCache>();
function cacheEntry(id: string) {
  let entry = scopeCache.get(id);
  if (!entry) { entry = { at: 0, listeners: new Set() }; scopeCache.set(id, entry); }
  return entry;
}
async function loadScope(id: string, force = false): Promise<ResearchScopeDoc> {
  const entry = cacheEntry(id);
  if (entry.pending) return entry.pending;
  if (!force && entry.value && Date.now() - entry.at < 2000) return entry.value;
  entry.pending = scopeRequest(id).then((doc: ResearchScopeDoc) => {
    entry.value = doc; entry.at = Date.now(); entry.listeners.forEach(listener => listener(doc)); return doc;
  }).finally(() => { entry.pending = undefined; });
  return entry.pending;
}
export function useExplorationScope(scopeId: string) {
  const [doc, setDoc] = useState<ResearchScopeDoc | undefined>(() => scopeCache.get(scopeId)?.value), [error, setError] = useState("");
  const stateEvent = useWorldStore(state => state.events.find(event => event.type.startsWith("state_") && (event.node_id === scopeId || event.payload.node_id === scopeId || event.payload.owner_id === scopeId))?.id);
  const reload = useCallback(() => loadScope(scopeId, true), [scopeId]);
  useEffect(() => {
    setError("");
    if (!scopeId) { setDoc(undefined); return; }
    const entry = cacheEntry(scopeId); setDoc(entry.value);
    let active = true;
    const receive = (value: ResearchScopeDoc) => { if (active) { setDoc(value); setError(""); } };
    entry.listeners.add(receive);
    const receiveError = (reason: unknown) => { if (active) setError(String(reason)); };
    void loadScope(scopeId).then(receive).catch(receiveError);
    const update = (event: Event) => {
      const detail = (event as CustomEvent).detail;
      if (!detail || detail === scopeId) void loadScope(scopeId, true).catch(receiveError);
    };
    window.addEventListener("oaw-research-updated", update);
    return () => { active = false; entry.listeners.delete(receive); window.removeEventListener("oaw-research-updated", update); };
  }, [scopeId]);
  useEffect(() => {
    if (!scopeId || !stateEvent) return;
    const timer = window.setTimeout(() => { void loadScope(scopeId, true).catch(reason => setError(String(reason))); }, 120);
    return () => window.clearTimeout(timer);
  }, [scopeId, stateEvent]);
  return { doc, error, reload };
}

function openNode(id: string) { useNodeSurfaceStore.getState().openWorkspace(id); }
function notifyScope(scopeId: string) { window.dispatchEvent(new CustomEvent("oaw-research-updated", { detail: scopeId })); }
const kinds: ExplorationKind[] = ["trail", "paper", "method", "web", "perspective", "collection"];
const methodStatus = (status: string) => ({ draft: "Draft / 草稿", executable: "Executable / 可执行", validated: "Validated / 已验证" }[status] ?? status);

export function ExplorationSummary({scopeId, doc, reload}: {scopeId: string; doc: ResearchScopeDoc; reload: () => Promise<ResearchScopeDoc>}) {
  useLocale();
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const index = useWorldStore(state => state.cards.find(card => card.type === "literature.index" && card.config.scope_id === scopeId));
  const entities = doc.value.exploration_nodes ?? [];
  async function sync() {
    setBusy(true); setError("");
    try { const live = await reload(); await scopeRequest(scopeId, "organize", { expected_revision: live.revision, arguments: { action: "sync" } }); await reload(); await useWorldStore.getState().refreshWorld(); notifyScope(scopeId); }
    catch (reason) { setError(String(reason)); } finally { setBusy(false); }
  }
  return <section className="exploration-summary"><header><div><h4>{l("index")}</h4><small>{l("subtitle")}</small></div>{index && <button onClick={() => openNode(index.id)}>{l("openIndex")}</button>}</header>
    <div className="exploration-counts">{kinds.map(kind => <span key={kind}>{l(kind)} <b>{entities.filter(entity => entity.kind === kind).length}</b></span>)}</div>
    <button disabled={busy || !doc.value.revisions.length} onClick={() => void sync()}>{busy ? l("saving") : l("sync")}</button><small>{l("syncHelp")}</small>{error && <p role="alert">{error}</p>}
  </section>;
}

type Action = "add" | "link" | "core_collection" | "attach_camp";
function ExplorationEditor({scopeId, doc, initialFrontierId = "", reload, onDone}: {
  scopeId: string; doc: ResearchScopeDoc; initialFrontierId?: string; reload: () => Promise<ResearchScopeDoc>; onDone: () => void;
}) {
  const cards = useWorldStore(state => state.cards);
  const entities = doc.value.exploration_nodes ?? [], frontiers = (doc.value.frontiers ?? []) as FrontierRecord[];
  const currentFrontiers = frontiers.filter(route => route.scope_revision === doc.value.current_revision && !route.stale);
  const currentEntities = entities.filter(entity => entity.scope_revision === doc.value.current_revision);
  const members = scopePaperIds(doc.value);
  const barracks = cards.filter(card => card.type === "oaw.barracks");
  const [action, setAction] = useState<Action>("add"), [kind, setKind] = useState<"perspective" | "web">("perspective");
  const [title, setTitle] = useState(""), [url, setUrl] = useState(""), [rationale, setRationale] = useState("");
  const [frontierId, setFrontierId] = useState(initialFrontierId), [papers, setPapers] = useState<string[]>([]);
  const [source, setSource] = useState(""), [target, setTarget] = useState(""), [relation, setRelation] = useState("related");
  const [barracksId, setBarracksId] = useState(barracks[0]?.id ?? ""), [busy, setBusy] = useState(false), [error, setError] = useState("");
  const titleOf = (id: string) => cards.find(card => card.id === id)?.name ?? entities.find(entity => entity.paper_id === id)?.title ?? id;
  async function submit(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const args: Record<string, unknown> = { action, rationale: rationale.trim() };
      if (action === "add") {
        if (kind === "web" && (!safePaperUrl(url) || !url.startsWith("https://"))) throw new Error(l("safeUrl"));
        Object.assign(args, { kind, title: title.trim(), paper_ids: papers, ...(frontierId ? { frontier_id: frontierId } : {}), ...(kind === "web" ? { url: url.trim() } : {}) });
      } else if (action === "link") Object.assign(args, { source, target, relation });
      else if (action === "core_collection") Object.assign(args, { title: title.trim(), paper_ids: papers, frontier_id: frontierId });
      else Object.assign(args, { frontier_id: frontierId, barracks_id: barracksId });
      const live = await reload();
      await scopeRequest(scopeId, "organize", { expected_revision: live.revision, arguments: args });
      await reload(); await useWorldStore.getState().refreshWorld(); notifyScope(scopeId); onDone();
    } catch (reason) { setError(String(reason)); } finally { setBusy(false); }
  }
  return <form className="exploration-editor" onSubmit={submit}>
    <nav>{([['add', 'add'], ['link', 'link'], ['core_collection', 'core'], ['attach_camp', 'camp']] as const).map(([value,label]) => <button key={value} type="button" aria-pressed={action === value} onClick={() => { setAction(value); setError(""); }}>{l(label)}</button>)}</nav>
    {action === "link" ? <>
      <p>{l("linkHelp")}</p><label>{l("source")}<select required value={source} onChange={event => setSource(event.target.value)}><option value="">—</option>{currentEntities.map(entity => <option key={entity.id} value={entity.id}>{l(entity.kind)} · {entity.title}</option>)}</select></label>
      <label>{l("target")}<select required value={target} onChange={event => setTarget(event.target.value)}><option value="">—</option>{currentEntities.filter(entity => entity.id !== source).map(entity => <option key={entity.id} value={entity.id}>{l(entity.kind)} · {entity.title}</option>)}</select></label>
      <label>{l("relation")}<select value={relation} onChange={event => setRelation(event.target.value)}>{(["related", "supports", "contrasts"] as const).map(value => <option key={value} value={value}>{l(value)}</option>)}</select></label>
    </> : <>
      <label>{l("branch")}<select required={action !== "add"} value={frontierId} onChange={event => setFrontierId(event.target.value)}><option value="">{action === "add" ? l("anyBranch") : l("selectBranch")}</option>{currentFrontiers.map(route => <option key={route.id} value={route.id}>{route.query}</option>)}</select></label>
      {action === "attach_camp" ? <><p>{l("campHelp")}</p><label>{l("barracks")}<select required value={barracksId} onChange={event => setBarracksId(event.target.value)}><option value="">—</option>{barracks.map(card => <option key={card.id} value={card.id}>{card.name}</option>)}</select></label>{!barracks.length && <small>{l("noBarracks")}</small>}</> : <>
        {action === "add" && <label>{l("type")}<select value={kind} onChange={event => setKind(event.target.value as typeof kind)}><option value="perspective">{l("perspective")}</option><option value="web">{l("web")}</option></select></label>}
        <label>{l("title")}<input required maxLength={500} value={title} onChange={event => setTitle(event.target.value)}/></label>
        {action === "add" && kind === "web" && <label>{l("url")}<input type="url" required placeholder="https://" value={url} onChange={event => setUrl(event.target.value)}/></label>}
        <p>{action === "core_collection" ? l("coreHelp") : l("sourceHelp")}</p>
        <fieldset><legend>{l("sources")} · {l("selection")} {papers.length}</legend><div className="exploration-paper-picker">{members.map(id => <label key={id}><input type="checkbox" checked={papers.includes(id)} onChange={event => setPapers(current => event.target.checked ? [...current,id] : current.filter(value => value !== id))}/><span>{titleOf(id)}</span></label>)}</div></fieldset>
      </>}
    </>}
    <label>{l("rationale")}<textarea required maxLength={4000} value={rationale} onChange={event => setRationale(event.target.value)}/></label>
    {error && <p role="alert">{error}</p>}
    <footer><button disabled={busy || !rationale.trim() || (action === "link" && (!source || !target || source === target)) || ((action === "core_collection" || (action === "add" && kind === "perspective")) && !papers.length)}>{busy ? l("saving") : l("save")}</button><button type="button" disabled={busy} onClick={onDone}>{l("cancel")}</button></footer>
  </form>;
}

function StageTask({scopeId, doc, entity, strategy, frontierId, reload, onDone}: {
  scopeId: string; doc: ResearchScopeDoc; entity?: ExplorationEntity; strategy: "close_read" | "method" | "branch_search";
  frontierId?: string; reload: () => Promise<ResearchScopeDoc>; onDone: () => void;
}) {
  const cards = useWorldStore(state => state.cards), papers = scopePaperIds(doc.value);
  const [paperId, setPaperId] = useState(entity?.paper_id ?? entity?.paper_ids?.[0] ?? papers[0] ?? "");
  const [rationale, setRationale] = useState(""), [busy, setBusy] = useState(false), [error, setError] = useState(""), [saved, setSaved] = useState(false);
  async function submit(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const live = await reload();
      await scopeRequest(scopeId, "organize", {expected_revision: live.revision, arguments: {
        action: "stage_task", strategy, rationale: rationale.trim(),
        ...(strategy === "method" && entity?.method_id ? {method_id: entity.method_id} : strategy === "close_read" ? {paper_id: paperId} : {}),
        ...(frontierId ? {frontier_id: frontierId} : {}),
      }});
      await reload(); await useWorldStore.getState().refreshWorld(); notifyScope(scopeId); setSaved(true);
    } catch (reason) { setError(String(reason)); } finally { setBusy(false); }
  }
  return <form className="exploration-editor" onSubmit={submit}><h4>{strategy === "branch_search" ? l("stageBranch") : strategy === "method" ? l("stageMethod") : l("micro")}</h4><small>{l("taskHelp")}</small>
    {!doc.value.task_board_id && <p>{l("taskBoardMissing")}</p>}
    {strategy === "close_read" && <label>{l("paper")}<select required value={paperId} onChange={event => setPaperId(event.target.value)}>{papers.map(id => <option key={id} value={id}>{cards.find(card => card.id === id)?.name ?? id}</option>)}</select></label>}
    <label>{strategy === "branch_search" ? l("searchGoal") : l("taskGoal")}<textarea required maxLength={4000} value={rationale} onChange={event => setRationale(event.target.value)}/></label>
    {error && <p role="alert">{error}</p>}{saved && <p role="status">{l("staged")}</p>}
    <footer><button disabled={busy || saved || !doc.value.task_board_id || !rationale.trim() || (strategy === "close_read" && !paperId)}>{busy ? l("saving") : l("save")}</button>{doc.value.task_board_id && <button type="button" onClick={() => openNode(doc.value.task_board_id!)}>{l("tasks")}</button>}<button type="button" onClick={onDone}>{l("cancel")}</button></footer>
  </form>;
}

function MethodFinding({entity, doc, scopeId, openPaper, reload}: {entity: ExplorationEntity; doc: ResearchScopeDoc; scopeId: string; openPaper: (id: string, source?: SourceLocation) => void; reload: () => Promise<ResearchScopeDoc>}) {
  const method = doc.value.methods.find(item => item.id === entity.method_id);
  const [busy, setBusy] = useState(false), [status, setStatus] = useState("");
  if (!method) return <p>{l("unknown")}</p>;
  async function assimilate() {
    setBusy(true); setStatus("");
    try {
      const live = await reload();
      const response = await fetch(`/api/nodes/${encodeURIComponent(doc.value.knowledge_id!)}/document`), knowledge = await response.json();
      if (!response.ok) throw new Error(String(knowledge.detail));
      await scopeRequest(scopeId, "assimilate_method", { expected_revision: live.revision, arguments: { method_id: method.id, knowledge_revision: knowledge.revision } });
      await useWorldStore.getState().refreshWorld(); setStatus(l("assimilated"));
    } catch (reason) { setStatus(String(reason)); } finally { setBusy(false); }
  }
  return <section className="exploration-method"><h4>{l("methodDetail")}</h4><small>{methodStatus(method.status)} · r{method.revision}</small><p>{method.purpose}</p>
    {!!method.steps?.length && <details><summary>{l("steps")}</summary><ol>{method.steps.map((step: {instruction: string}, index: number) => <li key={index}>{step.instruction}</li>)}</ol></details>}
    {!!method.missing?.length && <p>{l("missing")}: {method.missing.join(" · ")}</p>}
    {method.sources?.map((source: SourceLocation & {id: string; paper_id: string}) => <button key={source.id} onClick={() => openPaper(source.paper_id,source)}>{l("sourceText")} · p{source.page}</button>)}
    <footer><a href={`/api/literature/scopes/${encodeURIComponent(scopeId)}/methods/${encodeURIComponent(method.id)}/export`}>{l("export")}</a>{doc.value.knowledge_id && <button disabled={busy} onClick={() => void assimilate()}>{l("assimilate")}</button>}</footer>{status && <p role="status">{status}</p>}
  </section>;
}

export function ExplorationPanel({scopeId, entityId, compact = false, cardId, onSettings, onSelectTrail}: {scopeId: string; entityId?: string; compact?: boolean; cardId?: string; onSettings?:()=>void; onSelectTrail?:(id:string)=>void}) {
  useLocale();
  const { doc, error, reload } = useExplorationScope(scopeId);
  const cards = useWorldStore(state => state.cards);
  const [query,setQuery] = useState(""), [kind,setKind] = useState<ExplorationKind>(), [editing,setEditing] = useState(false);
  const [paper,setPaper] = useState<{id: string; source?: SourceLocation}>(), [snapshot,setSnapshot] = useState(false);
  const [selectedId,setSelectedId] = useState(entityId), [branch,setBranch] = useState("");
  const [taskStrategy,setTaskStrategy] = useState<"close_read" | "method" | "branch_search">();
  useEffect(() => { setSelectedId(entityId); setEditing(false); setTaskStrategy(undefined); }, [entityId,scopeId]);
  useEffect(() => { setTaskStrategy(undefined); }, [selectedId,branch]);
  const openPaper = (id: string, source?: SourceLocation) => setPaper({id,source});
  if (!scopeId) return <div className="literature-exploration"><p>{l("noScope")}</p></div>;
  if (!doc) return <div className="literature-exploration"><p role={error ? "alert" : "status"}>{error || l("loading")}</p></div>;
  const entities = doc.value.exploration_nodes ?? [], links = doc.value.exploration_links ?? [];
  const entity = entities.find(item => item.id === selectedId);
  const frontierId = entity?.kind === "trail" ? entity.frontier_id : branch;
  const visible = filterExploration(branchEntities(entities,links,frontierId),query,kind);
  const connected = entity ? links.filter(link => link.source === entity.id || link.target === entity.id) : [];
  const selectedPapers = entity?.paper_id ? [entity.paper_id] : entity?.paper_ids ?? [];
  const titleOf = (id: string) => cards.find(card => card.id === id)?.name ?? entities.find(item => item.paper_id === id)?.title ?? id;
  const visit = (item: ExplorationEntity) => item.kind === "paper" && item.paper_id ? openPaper(item.paper_id) : item.kind === "trail" && item.frontier_id && onSelectTrail ? onSelectTrail(item.frontier_id) : setSelectedId(item.id);
  const current = doc.value.revisions.at(-1);
  const camps = (doc.value.path_camps ?? []).filter(camp => !frontierId || camp.frontier_id === frontierId);
  const compactRoad = compact && (!entity || entity.kind === "trail");
  return <div className={`literature-exploration nodrag nopan nowheel${compact ? " is-compact" : ""}${compactRoad ? " is-road-preview" : ""}`}>
    {!compactRoad && !(onSettings && !entity) && <header className="exploration-heading"><div><small>{entity ? l(entity.kind) : l("index")}{entity && ` · ${entity.scope_revision === doc.value.current_revision ? l("current") : l("historical")}`}</small><h3>{entity?.title ?? current?.question ?? l("index")}</h3></div>{!compact && <button onClick={() => void reload()}>{l("refresh")}</button>}</header>}
    {!compactRoad && entity?.rationale && <p>{entity.rationale}</p>}
    {!compactRoad && entity?.url && safePaperUrl(entity.url) && <a href={safePaperUrl(entity.url)!} target="_blank" rel="noopener noreferrer">{l("web")} ↗</a>}
    {!entity && !compact && <><p>{l("subtitle")}</p><div className="exploration-counts">{kinds.map(value => <span key={value}>{l(value)} <b>{entities.filter(item => item.kind === value).length}</b></span>)}</div></>}
    {entityId && !entity && <p>{l("unknown")}</p>}
    {!compactRoad && !!selectedPapers.length && <section><h4>{entity?.kind === "collection" ? l("members") : l("sources")}</h4>{selectedPapers.slice(0,compact ? 4 : undefined).map(id => <button className="exploration-entry" key={id} onClick={() => openPaper(id)}>{titleOf(id)}</button>)}</section>}
    {onSelectTrail && entity?.kind === "trail" && entity.frontier_id && <ScopeFrontiers scopeId={scopeId} doc={doc} reload={reload} selectedId={entity.frontier_id} onlySelected/>}
    {(!entity || entity.kind === "trail") && <ExplorationRoads scopeId={scopeId} doc={doc} reload={reload} compact={compact} initialFrontierId={entity?.frontier_id} onOpen={visit}/>}
    {compact ? <>
      {!entity && !(doc.value.exploration_roads?.length) && visible.slice(0,3).map(item => <button className="exploration-entry" key={item.id} onClick={() => visit(item)}><small>{l(item.kind)}</small>{item.title}</button>)}
      {!!connected.length && <small>{l("routes")} · {connected.length}</small>}
      <footer>{cardId && <button onClick={() => openNode(cardId)}>{l("more")}</button>}<button onClick={() => onSettings ? onSettings() : openNode(scopeId)}>{l("scope")}</button></footer>
    </> : <>
      <nav className="exploration-toolbar"><button aria-pressed={editing} onClick={() => setEditing(value => !value)}>{editing ? l("cancel") : l("add")}</button><button onClick={() => setTaskStrategy(entity?.kind === "method" ? "method" : "close_read")}>{entity?.kind === "method" ? l("stageMethod") : l("stageMicro")}</button>{frontierId && <button onClick={() => setTaskStrategy("branch_search")}>{l("stageBranch")}</button>}<button onClick={() => onSettings ? onSettings() : openNode(scopeId)}>{l("scopeDetails")}</button>{selectedId && <button onClick={() => setSelectedId(undefined)}>{l("index")}</button>}</nav>
      {taskStrategy && <StageTask scopeId={scopeId} doc={doc} entity={entity} strategy={taskStrategy} frontierId={frontierId} reload={reload} onDone={() => setTaskStrategy(undefined)}/>}
      {entity && entity.kind !== "trail" && <FindingRoadMount scopeId={scopeId} doc={doc} entity={entity} reload={reload}/>}
      {editing && <ExplorationEditor scopeId={scopeId} doc={doc} initialFrontierId={frontierId} reload={reload} onDone={() => setEditing(false)}/>}
      {entity?.kind === "method" && <MethodFinding entity={entity} doc={doc} scopeId={scopeId} openPaper={openPaper} reload={reload}/>}
      {!!connected.length && <section className="exploration-connections"><h4>{l("routes")}</h4>{connected.map(link => { const other = entities.find(item => item.id === (link.source === entity!.id ? link.target : link.source)); return other && <article key={link.id}><button className="exploration-entry" onClick={() => visit(other)}><small>{l(link.relation === "method" ? "methodRelation" : link.relation)} · {l(other.kind)}</small>{other.title}</button><small>{link.rationale}</small></article>; })}</section>}
      {!onSelectTrail && entity?.kind === "trail" && entity.frontier_id && <ScopeFrontiers scopeId={scopeId} doc={doc} reload={reload} selectedId={entity.frontier_id} onlySelected/>}
      {!!camps.length && <section><h4>{l("recordedCamps")}</h4>{camps.map(camp => <article key={`${camp.frontier_id}:${camp.barracks_id}`}><button onClick={() => openNode(camp.barracks_id)}>{l("viewCamp")} · {titleOf(camp.barracks_id)}</button><p>{camp.rationale}</p></article>)}</section>}
      {(!entity || entity.kind === "trail") && <>
        <div className="exploration-filter"><input aria-label={l("search")} placeholder={l("search")} value={query} onChange={event => setQuery(event.target.value)}/>{!entity && <select aria-label={l("branch")} value={branch} onChange={event => setBranch(event.target.value)}><option value="">{l("anyBranch")}</option>{(doc.value.frontiers as FrontierRecord[]).map(route => <option key={route.id} value={route.id}>{route.query}</option>)}</select>}</div>
        <nav className="exploration-kinds"><button aria-pressed={!kind} onClick={() => setKind(undefined)}>{l("all")}</button>{kinds.map(value => <button key={value} aria-pressed={kind === value} onClick={() => setKind(value)}>{l(value)}</button>)}</nav>
        <div className="exploration-list">{visible.map(item => <button className="exploration-entry" key={item.id} onClick={() => visit(item)}><small>{l(item.kind)} · r{item.scope_revision}</small><strong>{item.title}</strong>{item.rationale && <span>{item.rationale}</span>}</button>)}</div>
        {!visible.length && <p>{entities.length ? l("noResults") : l("empty")}</p>}
        <ExplorationSummary scopeId={scopeId} doc={doc} reload={reload}/>
      </>}
      <button className="exploration-snapshot-toggle" aria-expanded={snapshot} onClick={() => setSnapshot(value => !value)}>{l("meso")}</button>
      {snapshot && <ScopeSnapshots scopeId={scopeId} doc={doc} reload={reload} openPaper={openPaper}/>}
    </>}
    {error && <p role="alert">{error}</p>}
    {paper && <PaperPortal paperId={paper.id} sourceLocation={paper.source} onClose={() => setPaper(undefined)}/>}
  </div>;
}

export function ExplorationView({card, level}: PluginViewProps) {
  if(card.type === "literature.index") return <ResearchHub scopeId={String(card.config.scope_id ?? "")} compact={level !== "workspace"} cardId={card.id}/>;
  return <ExplorationPanel scopeId={typeof card.config.scope_id === "string" ? card.config.scope_id : ""} entityId={typeof card.config.entity_id === "string" ? card.config.entity_id : undefined} compact={level !== "workspace"} cardId={card.id}/>;
}
