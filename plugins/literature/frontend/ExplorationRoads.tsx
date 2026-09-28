import { useEffect, useState } from "react";
import { useLocale } from "@oaw/plugin-api";
import { useWorldStore } from "../../../frontend/src/state/worldStore";
import { ScopeFrontiers, scopeRequest } from "./ScopeFrontiers";
import type { ResearchScopeDoc } from "./index";
import { availableRoadParents, orderedRoads, reorderRoadMembers, type ExplorationEntity, type ExplorationRoad } from "./explorationModel";
import { explorationText as l } from "./explorationCopy";
import "./ExplorationRoads.css";

/** Small code-native wayfinding marks, independent of the main canvas renderer. */
export function RoadGlyph({kind, className = ""}: {kind: "station" | "chain" | "branch"; className?: string}) {
  return <svg className={`exploration-road-glyph ${className}`} viewBox="0 0 32 32" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    {kind === "station" ? <><rect x="7" y="7" width="18" height="16" rx="4"/><path d="M11 11h10M11 16h3m4 0h3M11 23l-3 4m13-4 3 4M10 27h12"/><circle cx="11.5" cy="20" r=".75" fill="currentColor"/><circle cx="20.5" cy="20" r=".75" fill="currentColor"/></> : kind === "chain" ? <><path d="M16 28V5m-5 5 5-5 5 5"/><circle cx="16" cy="20" r="3.5" fill="var(--surface-raised)"/></> : <><path d="M12 28V5m-5 5 5-5 5 5M12 23c0-8 12-4 12-13V6m-4 4 4-4 4 4"/><circle cx="12" cy="23" r="2.5" fill="var(--surface-raised)"/></>}
  </svg>;
}

type RoadMutation = Record<string, unknown> | ((live: ResearchScopeDoc) => Record<string, unknown>);

export function FindingRoadMount({scopeId,doc,entity,reload}: {scopeId:string;doc:ResearchScopeDoc;entity:ExplorationEntity;reload:()=>Promise<ResearchScopeDoc>}) {
  const [busy,setBusy]=useState(false),[error,setError]=useState("");
  const roads=doc.value.exploration_roads??[],current=roads.find(road=>road.member_ids.includes(entity.id));
  if(!roads.length||entity.kind==="trail")return null;
  async function move(roadId:string){
    setBusy(true);setError("");
    try{const live=await reload();await scopeRequest(scopeId,"organize",{expected_revision:live.revision,arguments:{action:"move_member",entity_id:entity.id,road_id:roadId}});await reload();await useWorldStore.getState().refreshWorld();window.dispatchEvent(new CustomEvent("oaw-research-updated",{detail:scopeId}));}
    catch(reason){setError(String(reason));}finally{setBusy(false);}
  }
  return <div className="exploration-finding-road"><label>{l("roadMove")}<select disabled={busy} value={current?.id??""} onChange={event=>void move(event.target.value)}><option disabled value="">{l("roadBeside")}</option>{roads.map(road=><option key={road.id} value={road.id}>{road.id==="trunk"?l("trunk"):road.title}</option>)}</select></label><small>{l("roadMoveHelp")}</small>{error&&<p role="alert">{error}</p>}</div>;
}

export function ExplorationRoads({scopeId, doc, reload, compact = false, initialFrontierId, onOpen}: {
  scopeId: string; doc: ResearchScopeDoc; reload: () => Promise<ResearchScopeDoc>; compact?: boolean;
  initialFrontierId?: string; onOpen: (entity: ExplorationEntity) => void;
}) {
  useLocale();
  const roads = doc.value.exploration_roads ?? [];
  const entities = doc.value.exploration_nodes ?? [];
  const [selectedId, setSelectedId] = useState(initialFrontierId ? `route:${initialFrontierId}` : "trunk");
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [adding, setAdding] = useState(false), [organizing, setOrganizing] = useState(false);
  const [parentId, setParentId] = useState(""), [attachAfter, setAttachAfter] = useState("");
  const selected = roads.find(road => road.id === selectedId) ?? roads[0];
  const parents = selected ? availableRoadParents(roads,selected.id) : [];
  const parent = roads.find(road => road.id === parentId);
  const titleOf = (id: string) => entities.find(entity => entity.id === id)?.title ?? l("roadUnknown");
  const roadTitle = (road: ExplorationRoad) => road.id === "trunk" ? l("trunk") : `${road.title}${road.scope_revision !== doc.value.current_revision ? ` · r${road.scope_revision}` : ""}`;
  useEffect(() => { if (initialFrontierId) setSelectedId(`route:${initialFrontierId}`); }, [initialFrontierId]);
  useEffect(() => { setParentId(selected?.parent_id ?? ""); setAttachAfter(selected?.attach_after ?? ""); setOrganizing(false); }, [selected?.id, selected?.parent_id, selected?.attach_after]);

  async function mutate(input: RoadMutation) {
    setBusy(true); setError("");
    try {
      const live = await reload(), args = typeof input === "function" ? input(live) : input;
      await scopeRequest(scopeId,"organize",{expected_revision:live.revision,arguments:args});
      await reload(); await useWorldStore.getState().refreshWorld();
      window.dispatchEvent(new CustomEvent("oaw-research-updated",{detail:scopeId}));
    } catch (reason) { setError(String(reason)); } finally { setBusy(false); }
  }

  function moveInRoad(entityId: string, direction: -1 | 1) {
    if (!selected) return;
    void mutate(live => {
      const road = live.value.exploration_roads?.find(item => item.id === selected.id);
      if (!road) throw new Error(l("roadUnknown"));
      return {action:"reorder",road_id:road.id,member_ids:reorderRoadMembers(road.member_ids,entityId,direction)};
    });
  }

  if (compact && initialFrontierId && selected) return <section className="exploration-roads is-compact" aria-label={l("roadMap")}>
    <div className="exploration-road-station"><RoadGlyph kind={selected.mode}/><div><small>{l(selected.mode === "chain" ? "roadChain" : "roadBranch")}</small><strong>{l("roadFindings")} · {selected.member_ids.length}</strong></div></div>
    <div className="exploration-road-mini">{selected.member_ids.slice(0,3).map(id => {const entity = entities.find(item => item.id === id); return <button className="exploration-entry" key={id} disabled={!entity} onClick={() => entity && onOpen(entity)}>{titleOf(id)}</button>;})}</div>
    {!selected.member_ids.length && <small>{l("roadNoMembers")}</small>}
  </section>;
  if (compact) return <section className="exploration-roads is-compact" aria-label={l("roadMap")}>
    <div className="exploration-road-station"><RoadGlyph kind="station"/><div><small>{l("station")}</small><strong>{l("index")}</strong></div><span>{roads.length} {l("roadMap")}</span></div>
    <div className="exploration-road-mini">{orderedRoads(roads).slice(0,4).map(({road}) => <div key={road.id}><RoadGlyph kind={road.id === "trunk" ? "chain" : road.mode}/><span>{roadTitle(road)}</span><small>{road.member_ids.length}</small></div>)}</div>
    {!roads.length && <small>{l("roadEmpty")}</small>}
  </section>;

  return <section className="exploration-roads" aria-label={l("roadMap")}>
    <header className="exploration-roads-heading"><div><small>{l("station")} → {l("roadMap")}</small><h4>{l("roadMap")}</h4></div><div><button type="button" disabled={busy || !doc.value.revisions.length} aria-expanded={adding} onClick={() => setAdding(value => !value)}>{adding ? l("cancel") : l("roadNewSignpost")}</button>{!!roads.length && <button type="button" disabled={busy} title={l("roadLayoutHelp")} onClick={() => void mutate({action:"layout"})}>{l("roadLayout")}</button>}</div></header>
    <p className="exploration-road-help">{l("roadHelp")}</p>
    {adding && <ScopeFrontiers scopeId={scopeId} doc={doc} reload={reload} creationOnly onSelect={id => { setAdding(false); setSelectedId(`route:${id}`); }}/>}
    {!roads.length ? <div className="exploration-road-empty"><RoadGlyph kind="station"/><p>{l("roadEmpty")}</p><button disabled={busy || !doc.value.revisions.length} onClick={() => void mutate({action:"migrate_roads",layout:false})}>{busy ? l("saving") : l("roadInitialize")}</button><small>{l("roadInitializeHelp")}</small></div> : <>
      <div className="exploration-road-station"><RoadGlyph kind="station"/><div><small>{l("station")}</small><strong>{l("index")}</strong></div><span>{roads.length} {l("roadMap")}</span></div>
      <nav className="exploration-road-tabs" aria-label={l("roadMap")}>{orderedRoads(roads).map(({road,depth}) => <button key={road.id} type="button" disabled={busy} aria-pressed={road.id === selected?.id} onClick={() => setSelectedId(road.id)} title={roadTitle(road)}><RoadGlyph kind={road.id === "trunk" ? "chain" : road.mode}/><span>{depth > 1 ? "↳ " : ""}{roadTitle(road)}</span><b>{road.member_ids.length}</b></button>)}</nav>
      {selected && <div className="exploration-road-detail">
        <header><div><small>{selected.id === "trunk" ? l("trunk") : l(selected.mode === "chain" ? "roadChain" : "roadBranch")}</small><h4>{roadTitle(selected)}</h4></div><div>{selected.anchor_id && <button disabled={busy} onClick={() => { const anchor = entities.find(entity => entity.id === selected.anchor_id); if (anchor) onOpen(anchor); }}>{l("roadSignpost")}</button>}<button disabled={busy} onClick={() => void mutate({action:"layout",road_id:selected.id})}>{l("roadLayoutOne")}</button>{selected.id !== "trunk" && <button disabled={busy} aria-expanded={organizing} onClick={() => setOrganizing(value => !value)}>{l("roadSettings")}</button>}</div></header>
        {selected.id !== "trunk" && <div className="exploration-road-mode" aria-label={l("roadSignpost")}>{(["chain","branch"] as const).map(mode => <button key={mode} disabled={busy} aria-pressed={selected.mode === mode} title={l("roadModeHelp")} onClick={() => void mutate({action:"route_mode",road_id:selected.id,mode})}><RoadGlyph kind={mode}/>{l(mode === "chain" ? "roadChain" : "roadBranch")}</button>)}</div>}
        {organizing && selected.id !== "trunk" && <form className="exploration-road-connection" onSubmit={event => { event.preventDefault(); void mutate({action:"reparent",road_id:selected.id,parent_id:parentId || null,...(attachAfter && selected.mode === "branch" ? {attach_after:attachAfter} : {attach_after:null})}); }}>
          <label>{l("roadParent")}<select disabled={busy} value={parentId} onChange={event => {setParentId(event.target.value);setAttachAfter("");}}><option value="">研究范围初始路标（独立主干道）</option>{parents.map(road => <option key={road.id} value={road.id}>{roadTitle(road)}</option>)}</select></label>
          {selected.mode === "branch" && parentId && <label>{l("roadAnchor")}<select disabled={busy} value={attachAfter} onChange={event => setAttachAfter(event.target.value)}><option value="">{l("roadTail")}</option>{parent?.anchor_id && <option value={parent.anchor_id}>{l("roadSignpost")} · {titleOf(parent.anchor_id)}</option>}{parent?.member_ids.map(id => <option key={id} value={id}>{titleOf(id)}</option>)}</select></label>}
          <button disabled={busy}>{l("roadSaveParent")}</button>
        </form>}
        <ol className="exploration-road-members">{selected.member_ids.map((id,index) => {
          const entity = entities.find(item => item.id === id);
          return <li key={id}><span className="exploration-road-stop">{String(index+1).padStart(2,"0")}</span><button className="exploration-road-finding" disabled={!entity || busy} onClick={() => entity && onOpen(entity)}><small>{entity ? l(entity.kind) : l("roadUnknown")}</small><strong>{titleOf(id)}</strong></button><div className="exploration-road-order"><button type="button" aria-label={`${l("roadMoveUp")} ${titleOf(id)}`} title={l("roadMoveUp")} disabled={busy || index === 0} onClick={() => moveInRoad(id,-1)}>↑</button><button type="button" aria-label={`${l("roadMoveDown")} ${titleOf(id)}`} title={l("roadMoveDown")} disabled={busy || index === selected.member_ids.length-1} onClick={() => moveInRoad(id,1)}>↓</button></div>
            <select className="exploration-road-mount" aria-label={`${l("roadMove")} ${titleOf(id)}`} title={l("roadMoveHelp")} disabled={busy || !entity || roads.length < 2} value={selected.id} onChange={event => void mutate({action:"move_member",entity_id:id,road_id:event.target.value})}>{roads.map(road => <option key={road.id} value={road.id}>{roadTitle(road)}</option>)}</select>
          </li>;
        })}</ol>
        {!selected.member_ids.length && <p className="exploration-road-no-members">{l("roadNoMembers")}</p>}
        <small>{l("roadMoveHelp")}</small>
      </div>}
    </>}
    {busy && <small role="status">{l("saving")}</small>}{error && <p role="alert">{error}</p>}
  </section>;
}
