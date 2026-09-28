import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode, type PointerEvent as ReactPointerEvent } from "react";
import { useReactFlow, useViewport, useStore } from "@xyflow/react";
import { BookOpen, ChevronLeft, Compass, Flag, Map as MapIcon, Plus, RefreshCw, Route, Settings2, X } from "lucide-react";
import { useLocale, t } from "../i18n";
import { useWorldStore } from "../state/worldStore";
import { useAutoResearch } from "../state/autoResearch";
import { researchVisibleIds } from "../state/researchVisibility";
import type { CanvasNode } from "../cards/types";
import { ResearchHub, type ResearchScopeDoc } from "../../../plugins/literature/frontend";
import { ScopeSnapshots } from "../../../plugins/literature/frontend/ScopeSnapshots";
import { PaperPortal, type SourceLocation } from "../../../plugins/library/frontend/PaperPortal";
import { ExplorationPanel } from "../../../plugins/literature/frontend/Exploration";
import { ScopeFrontiers, scopeRequest, discoveryLabel, evidenceLabel, type FrontierRecord } from "../../../plugins/literature/frontend/ScopeFrontiers";
import "./researchFog.css";
import { RESIZE_CORNERS, resizeFromCorner, type ResizeCorner } from "./resizeGeometry";
import "./resize.css";
import { FogPaint } from "./FogPaint";
import type { FogCorridor } from "./FogPaint";
import { researchRoadCurve } from "./researchRoadGeometry";
import { roadConnections } from "../../../plugins/literature/frontend/explorationModel";

type Region = { id: string; x: number; y: number; rx: number; ry: number; strength: number };
type Marker = { id: string; x: number; y: number; origin: {x:number;y:number}; route: FrontierRecord; materialized?:boolean; mode?:"chain"|"branch" };
type Road = {id:string;mode:"chain"|"branch";path:string;pair:string};
type NodeBox = { x:number; y:number; width:number; height:number };

const kindFor = (route: FrontierRecord) => route.discovery_state === "searching" ? "scout" : route.evidence_state === "conflicted" ? "dispute" : ["no_results","filtered_empty"].includes(route.discovery_state) ? "empty-search" : "signpost";

/** A projection on the existing ReactFlow; it never owns nodes or its viewport. */
export function ResearchFog({nodes}:{nodes:CanvasNode[]}) {
  useLocale();
  const cards = useWorldStore(state => state.cards);
  const worldEdges = useWorldStore(state => state.edges);
  const documentEvent = useWorldStore(state => state.events.find(event => event.type.startsWith("state_"))?.id);
  const {scopeId,selectedFrontierId,panel,selectScope,selectFrontier,setPanel,setMembers,setRoadPairs,createdIds} = useAutoResearch();
  const scopes = useMemo(() => cards.filter(card => card.type === "literature.scope"),[cards]);
  const activeId = scopes.some(card => card.id === scopeId) ? scopeId : undefined;
  const toolbarCollapsed=useAutoResearch(state=>state.toolbarCollapsed);
  const setToolbarCollapsed=useAutoResearch(state=>state.setToolbarCollapsed);
  const toolbarToggle=useRef<HTMLButtonElement>(null);
  const dockToggle=useRef<HTMLButtonElement>(null);
  const toggleToolbar=(collapsed:boolean)=>{
    setToolbarCollapsed(collapsed);
    requestAnimationFrame(()=> (collapsed ? dockToggle : toolbarToggle).current?.focus({preventScroll:true}));
  };
  const [doc,setDoc] = useState<ResearchScopeDoc>();
  const [error,setError] = useState("");
  const [busy,setBusy] = useState(false);
  const [paper,setPaper] = useState<{id:string;source?:SourceLocation}>();
  const activeRequest = useRef(0);
  const liveId = useRef(activeId); liveId.current = activeId;
  const flow = useReactFlow<CanvasNode>();
  const reload = useCallback(async () => {
    if (!activeId) throw new Error("Select a research scope");
    const request = ++activeRequest.current;
    const next:ResearchScopeDoc = await scopeRequest(activeId);
    if (activeId === liveId.current && request === activeRequest.current) { setDoc(next); setError(""); }
    return next;
  },[activeId]);
  useEffect(() => {
    setDoc(undefined); setError("");
    if (activeId) void reload().catch(reason => { if (liveId.current === activeId) setError(String(reason)); });
  },[activeId,reload]);
  useEffect(() => {
    if (!activeId) return;
    const timer = window.setTimeout(() => void reload().catch(reason => { if (liveId.current === activeId) setError(String(reason)); }),180);
    return () => window.clearTimeout(timer);
  },[documentEvent,activeId,reload]);
  useEffect(() => {
    const update = (event:Event) => { if ((event as CustomEvent).detail === activeId) void reload().catch(reason => setError(String(reason))); };
    window.addEventListener("oaw-research-updated",update);
    return () => window.removeEventListener("oaw-research-updated",update);
  },[activeId,reload]);
  const current = doc?.value.revisions.at(-1);
  const members = useMemo(() => [...new Set([...(doc?.value.paper_ids ?? []),...(current?.seed_paper_ids ?? [])])],[doc,current]);
  const visibleIds = useMemo(() => researchVisibleIds(cards,createdIds,members),[cards,createdIds,members]);
  useEffect(() => { if (scopeId) setMembers(scopeId,activeId && doc ? [activeId,...members] : []); },[scopeId,activeId,doc,members,setMembers]);
  const frontiers = (doc?.value.frontiers ?? []) as FrontierRecord[];
  const currentFrontiers = frontiers.filter(route => !route.stale && route.scope_revision === doc?.value.current_revision);

  const projection = useMemo(() => {
    const boxes = new Map<string,NodeBox>();
    for (const node of nodes) {
      if (node.hidden || node.data.equipmentDetail) continue;
      const internal = flow.getInternalNode(node.id);
      if (!internal) continue;
      const origin = internal.internals.positionAbsolute;
      const width = internal.measured.width ?? node.width ?? 280, height = internal.measured.height ?? node.height ?? 180;
      boxes.set(node.id,{x:origin.x+width/2,y:origin.y+height/2,width,height});
    }
    const regions:Region[] = [...visibleIds].flatMap(id => {
      const box = boxes.get(id); if (!box) return [];
      // Revealing a workspace is a navigation affordance, not evidence grading.
      // Circumscribed clearings protect card corners without square outer edges.
      return [{id,...box,rx:(box.width/2+26)*1.5,ry:(box.height/2+26)*1.5,strength:1}];
    });
    const roads:Road[]=[],corridors:FogCorridor[]=[];
    const index=cards.find(card => card.type === "literature.index" && card.config.scope_id === activeId);
    const scope = activeId ? (index ? boxes.get(index.id) : boxes.get(activeId)) : undefined;
    const scopeCard = cards.find(card => card.id === activeId);
    const basecamp = scopeCard?.parent_id ? boxes.get(scopeCard.parent_id) ?? scope : scope;
    const originCard=cards.find(card=>card.type==='literature.trail' && card.config.scope_id===activeId && card.config.entity_id==='trail:origin');
    const originBox=originCard ? boxes.get(originCard.id) : undefined;
    const departure=originBox ? {...originBox,y:originBox.y-35} : basecamp && scope ? {x:basecamp.x+basecamp.width/2+100,y:scope.y,width:48,height:48} : undefined;
    if(departure) regions.push({id:`departure:${activeId}`,...departure,rx:140,ry:140,strength:1});
    const connections=index && doc ? roadConnections(doc.value.exploration_roads ?? [],doc.value.exploration_nodes ?? [],index.id) : [];
    const knownPairs=new Set(connections.map(connection=>`${connection.source}/${connection.target}`));
    // Persisted navigation still reveals its corridor before a scope is selected.
    for(const edge of worldEdges) if(edge.relationship === "literature.road" && !knownPairs.has(`${edge.source}/${edge.target}`)) {
      connections.push({source:edge.source,target:edge.target,roadId:edge.id,mode:"chain"});
    }
    for(const connection of connections) {
      const source=boxes.get(connection.source),target=boxes.get(connection.target);
      if(!source || !target) continue;
      // The logical directory remains the source; the visible road leaves via
      // the same camp-edge signpost, not through an unrelated floating marker.
      const points=connection.source===index?.id && departure && !originCard ? [source,departure,target] : [source,target];
      for(let i=1;i<points.length;i++) {
        const curve=researchRoadCurve(points[i-1],points[i]);
        roads.push({id:connection.roadId,mode:connection.mode,path:curve.path,pair:`${connection.source}/${connection.target}`});
        corridors.push({points:curve.points,radius:Math.max(72,Math.min(125,Math.min(source.width,target.width)*.3)),strength:1});
      }
    }
    for(const edge of worldEdges) if(["literature.method","literature.related","literature.supports","literature.contrasts"].includes(edge.relationship)) {
      const source=boxes.get(edge.source),target=boxes.get(edge.target);
      if(source && target) corridors.push({points:researchRoadCurve(source,target).points,radius:64,strength:1});
    }
    const markers:Marker[] = [];
    // Persisted signposts remain interactive even after their scope revision changes.
    const signpostAnchors = new Map<string,{x:number;y:number}>();
    for (const card of cards) {
      if (card.type !== 'literature.trail' || card.config.scope_id !== activeId) continue;
      const box = boxes.get(card.id);
      const frontierId = card.config.frontier_id;
      if (box && typeof frontierId === 'string') signpostAnchors.set(frontierId,{x:box.x,y:box.y-35});
    }
    if (activeId) {
      currentFrontiers.forEach((route,index) => {
        const sourceBoxes = (route.source_paper_ids ?? []).map(id => boxes.get(id)).filter((box):box is NodeBox => !!box);
        const origin = sourceBoxes.length ? {x:sourceBoxes.reduce((sum,box) => sum+box.x,0)/sourceBoxes.length,y:sourceBoxes.reduce((sum,box) => sum+box.y,0)/sourceBoxes.length} : scope ?? {x:0,y:0};
        // This is only a stable view layout of persisted directions, not a
        // semantic-distance estimate or a claim about field coverage.
        const routeNode = cards.find(card => card.type === "literature.trail" && card.config.scope_id === activeId &&
          (card.config.frontier_id === route.id || card.config.entity_id === `trail:${route.id}`));
        const routeBox = routeNode ? boxes.get(routeNode.id) : undefined;
        if (!routeBox && !basecamp) return;
        const marker = {id:route.id,route,origin,materialized:!!routeBox,
          mode:doc?.value.exploration_roads?.find(road => road.frontier_id === route.id)?.mode,
          x:routeBox ? routeBox.x : basecamp!.x+basecamp!.width/2+180+(index%3)*240,
          y:routeBox ? routeBox.y-35 : basecamp!.y-basecamp!.height/2+90+Math.floor(index/3)*200};
        markers.push(marker);
        if (["found","no_results","filtered_empty"].includes(route.discovery_state)) regions.push({id:route.id,x:marker.x,y:marker.y,rx:95,ry:85,strength:.48});
      });
    }
    return {regions,markers,signpostAnchors,roads,corridors,scope:basecamp,departure,scopeId:activeId,scopeName:scopeCard?.name,originCardId:originCard?.id};
  },[nodes,flow,doc,visibleIds,activeId,cards,worldEdges]);
  useEffect(()=>{setRoadPairs(projection.roads.map(road=>road.pair));},[projection.roads,setRoadPairs]);
  useEffect(()=>()=>setRoadPairs([]),[setRoadPairs]);

  async function createScope() {
    setBusy(true); setError("");
    try { const card = await useWorldStore.getState().createCard("literature.scope"); if (card) { selectScope(card.id); setPanel("scope"); } }
    catch (reason) { setError(String(reason)); }
    finally { setBusy(false); }
  }
  return <>
    <FogPaint regions={projection.regions} corridors={projection.corridors}/>
    <ResearchRoads roads={projection.roads}/>
    <FogLandmarks projection={projection} count={currentFrontiers.length} snapshots={doc?.value.snapshots.length ?? 0} selectedFrontierId={selectedFrontierId} setPanel={setPanel} selectFrontier={selectFrontier}/>
    {toolbarCollapsed && <button ref={dockToggle} type="button" className="research-fog-dock nodrag nopan nowheel"
      aria-label={t("展开 AutoResearch 范围控制")} title={t("展开 AutoResearch 范围控制")} aria-expanded={false} aria-controls="research-scope-toolbar"
      onClick={()=>toggleToolbar(false)}><MapIcon size={20}/></button>}
    <section id="research-scope-toolbar" hidden={toolbarCollapsed} className="research-fog-toolbar nodrag nopan nowheel" aria-label={t("AutoResearch 范围控制")}>
      <header><MapIcon size={16}/><strong>AutoResearch</strong><span>{t("灰雾研究视图")}</span><button ref={toolbarToggle} type="button" className="research-fog-collapse"
        aria-label={t("收起 AutoResearch 范围控制")} title={t("收起到左侧边缘")} aria-expanded={true} aria-controls="research-scope-toolbar"
        onClick={()=>toggleToolbar(true)}><ChevronLeft size={15}/></button></header>
      <div className="research-fog-scope-picker"><select aria-label={t("选择研究范围")} value={activeId ?? ""} onChange={event => selectScope(event.target.value || undefined)}><option value="">{t("选择研究范围")}</option>{scopes.map(scope => <option key={scope.id} value={scope.id}>{scope.name}</option>)}</select><button type="button" disabled={busy} onClick={() => void createScope()} title={t("建立研究范围")} aria-label={t("建立研究范围")}><Plus size={16}/></button></div>
      {!activeId ? <p>{t("选择或建立一个有边界的问题，再把尚缺的证据放到路标上。此处没有自动生成的探索历史。")}</p> : <>
        <p className="research-fog-question">{current?.question ?? t("先定义研究问题、范围与预算。")}</p>
        <small>{t("范围外节点淡化，仍可操作。灰雾不表示整个领域的覆盖率。")}</small>
        <div className="research-fog-toolbar-actions"><button type="button" onClick={() => { useAutoResearch.setState({selectedFrontierId:undefined}); setPanel(panel === "directions" ? undefined : "directions"); }}><Route size={14}/>{t("探索方向")} · {currentFrontiers.length}</button><button type="button" onClick={() => setPanel(panel === "scope" ? undefined : "scope")}><Settings2 size={14}/>{t("范围与资料")}</button><button type="button" onClick={() => void reload().catch(reason => setError(String(reason)))} aria-label={t("刷新研究范围")}><RefreshCw size={14}/></button></div>
      </>}
      {error && <p className="research-fog-error" role="alert">{error}</p>}
      <details className="research-fog-legend"><summary>{t("如何读这张地图")}</summary><p><span className="research-fog-key is-metadata"/>{t("浅雾：已有题录或真实检索记录")}</p><p><span className="research-fog-key is-evidence"/>{t("揭雾：已有可定位来源；科学结论仍需核验")}</p><p><Flag size={12}/>{t("路标：真实保存的证据缺口与下一步")}</p><small>{t("虚线为拟定检索方向，路标位置仅为显示布局。开启或关闭此视图不启停任务。")}</small></details>
    </section>
    {activeId && panel && <ResearchDetail key={`${activeId}:${panel}:${selectedFrontierId ?? "origin"}`} anchor={panel === "directions" ? (selectedFrontierId ? projection.signpostAnchors.get(selectedFrontierId) ?? projection.markers.find(marker=>marker.id===selectedFrontierId) : projection.departure) : undefined} ariaLabel={panel === "scope" ? t("研究范围与资料") : panel === "snapshots" ? t("领域快照") : t("路标详情")}>
      <header><span>{panel === "scope" ? <><BookOpen size={15}/>{t("范围 · 资料 · 快照")}</> : panel === "snapshots" ? <><BookOpen size={15}/>{t("领域快照")}</> : <><Compass size={15}/>{t(selectedFrontierId ? "方向路标详情" : "初始路标详情")}</>}</span><button type="button" aria-label={t("关闭研究详情")} onClick={() => setPanel(undefined)}><X size={16}/></button></header>
      <div className="research-fog-detail-body">{panel === "scope" ? <ResearchHub key={activeId} scopeId={activeId} initialTab="settings"/> : doc ? panel === "snapshots" ? <ScopeSnapshots key={activeId} scopeId={activeId} doc={doc} reload={reload} openPaper={(id,source) => setPaper({id,source})}/> : <>
          <div className="research-signpost-detail-intro">
            {selectedFrontierId ? <button type="button" onClick={()=>useAutoResearch.setState({selectedFrontierId:undefined})}>{t("返回研究范围初始路标")}</button> : <><strong>{doc.value.revisions.at(-1)?.question ?? t("尚未设置研究问题")}</strong><p>{t("此路标对应当前研究范围，各条主干道从这里出发。")}</p></>}
          </div>
          {selectedFrontierId && !doc.value.exploration_nodes?.some(entity=>entity.kind==='trail' && entity.frontier_id===selectedFrontierId) ?
            <ScopeFrontiers key={`${activeId}:${selectedFrontierId}`} scopeId={activeId} doc={doc} reload={reload} selectedId={selectedFrontierId} onSelect={selectFrontier} onlySelected/> :
            <ExplorationPanel key={`${activeId}:${selectedFrontierId ?? 'origin'}`} scopeId={activeId} entityId={doc.value.exploration_nodes?.find(entity=>entity.kind==='trail' && entity.frontier_id===selectedFrontierId)?.id} onSelectTrail={selectFrontier} onSettings={()=>setPanel('scope')}/>}
          {!selectedFrontierId && !doc.value.exploration_nodes?.some(entity=>entity.kind==='trail') && <ScopeFrontiers scopeId={activeId} doc={doc} reload={reload} onSelect={selectFrontier}/>}
        </> : <p>{error || t("正在读取研究范围…")}</p>}</div>
    </ResearchDetail>}
    {paper && <PaperPortal paperId={paper.id} sourceLocation={paper.source} onClose={() => setPaper(undefined)}/>}
  </>;
}

function ResearchRoads({roads}:{roads:Road[]}) {
  const viewport=useViewport();
  return <svg className="research-roads" aria-label="研究道路" data-segments={roads.length}>
    <g transform={`translate(${viewport.x} ${viewport.y}) scale(${viewport.zoom})`}>
      {roads.map((road,index)=><g key={`${road.pair}:${index}`} data-road-id={road.id} data-road-mode={road.mode}>
        <path className="research-road-shoulder" d={road.path} vectorEffect="non-scaling-stroke"/>
        <path className="research-road-surface" d={road.path} vectorEffect="non-scaling-stroke"/>
        <path className="research-road-center" d={road.path} vectorEffect="non-scaling-stroke"/>
      </g>)}
    </g>
  </svg>;
}

function RoadSign({mode}:{mode?:"chain"|"branch"}) {
  return <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M10 21V4M6 8l4-4 4 4"/>{mode==="branch" && <path d="M10 16l10-10M15 6h5v5"/>}
  </svg>;
}

function FogLandmarks({projection,count,snapshots,selectedFrontierId,setPanel,selectFrontier}:{
  projection:{regions:Region[];markers:Marker[];scope?:NodeBox;departure?:NodeBox;scopeId?:string;scopeName?:string;originCardId?:string};count:number;snapshots:number;
  selectedFrontierId?:string;setPanel:(panel:"directions"|"snapshots")=>void;selectFrontier:(id:string)=>void;
}) {
  const viewport = useViewport();
  const detailOpen=useAutoResearch(state=>state.panel === "directions");
  const screen = (point:{x:number;y:number}) => ({left:viewport.x+point.x*viewport.zoom,top:viewport.y+point.y*viewport.zoom});
  const zoomedOut = viewport.zoom < .45;
  const markers = projection.markers.filter((marker,index) => index < 24 || marker.id === selectedFrontierId);


  return (<div className="research-fog-landmarks" aria-label={t("研究范围路标")}>
      {!zoomedOut && <svg className="research-fog-routes" aria-hidden="true">{markers.filter(marker => !marker.materialized).map(marker => <line key={marker.id} x1={screen(marker.origin).left} y1={screen(marker.origin).top} x2={screen(marker).left} y2={screen(marker).top} data-selected={marker.id === selectedFrontierId}/>)}</svg>}
      {projection.departure && !projection.originCardId && <button type="button" className="research-fog-cluster nodrag nopan" data-scope-id={projection.scopeId} data-near={!zoomedOut || undefined} style={{...screen(projection.departure),visibility:detailOpen && !selectedFrontierId ? "hidden" : undefined}} onClick={() => { useAutoResearch.setState({selectedFrontierId:undefined}); setPanel("directions"); }} aria-label={t("查看探索方向")} title={`${t("研究范围起点")} · ${projection.scopeName ?? ""}`}>
        {zoomedOut ? <Flag size={16}/> : <span className="research-fog-sprite" aria-hidden="true"/>}<span className="research-fog-count">{count}</span><span className="research-fog-marker-label">{t("研究范围起点")}</span>
      </button>}
      {!zoomedOut && markers.filter(marker=>!marker.materialized).map(marker => <button type="button" key={marker.id} className="research-fog-marker nodrag nopan" data-kind={kindFor(marker.route)} data-materialized={marker.materialized || undefined} data-selected={marker.id === selectedFrontierId} style={{...screen(marker),visibility:detailOpen && selectedFrontierId===marker.id ? "hidden" : undefined}} onClick={() => selectFrontier(marker.id)} aria-label={`${t("探索路标")}: ${marker.route.query} · ${t(discoveryLabel(marker.route.discovery_state))} · ${t(evidenceLabel(marker.route.evidence_state))}`} title={`${marker.route.query}\n${t(discoveryLabel(marker.route.discovery_state))} · ${t(evidenceLabel(marker.route.evidence_state))}`}>
        <span className="research-fog-sprite" aria-hidden="true"/>
        {marker.materialized ? <span className="research-fog-road-badge" aria-hidden="true"><RoadSign mode={marker.mode}/></span> : <span className="research-fog-marker-label" aria-hidden="true">{marker.route.query}<small>{t(discoveryLabel(marker.route.discovery_state))}</small></span>}
      </button>)}
      {projection.scope && !!snapshots && <button type="button" className="research-fog-marker nodrag nopan" data-kind="snapshot" style={screen({x:projection.scope.x-projection.scope.width/2-120,y:projection.scope.y})} onClick={() => setPanel("snapshots")} aria-label={t("打开领域快照")}><span className="research-fog-sprite" aria-hidden="true"/><span className="research-fog-marker-label">{t("领域快照")} · {snapshots}</span></button>}
    </div>);
}


/** Docked settings are separate from the world-anchored signpost surface. */
function ResearchDetail({anchor,ariaLabel,children}:{anchor?:{x:number;y:number};ariaLabel:string;children:ReactNode}) {
  return anchor ? <SignpostDetail anchor={anchor} ariaLabel={ariaLabel}>{children}</SignpostDetail>
    : <aside className="research-fog-detail nodrag nopan nowheel" aria-label={ariaLabel}>{children}</aside>;
}

function SignpostDetail({anchor,ariaLabel,children}:{anchor:{x:number;y:number};ariaLabel:string;children:ReactNode}) {
  const viewport=useViewport();
  const canvasHeight=useStore(state=>state.height);
  const canvasWidth=useStore(state=>state.width);
  const [openingScale]=useState(()=>1/viewport.zoom);
  const scale=viewport.zoom*openingScale;
  // Capture opening geometry once. Never clamp to screen edges during pan/zoom:
  // the window and its stone hat form one surface in world coordinates.
  const [box,setBox]=useState(()=>{
    const width=Math.min(440,Math.max(320,canvasWidth-36));
    const height=Math.min(620,Math.max(320,canvasHeight-240));
    // Fit once on open; subsequent viewport changes never change this geometry.
    const screenX=Math.max(18,Math.min(canvasWidth-width-18,viewport.x+anchor.x*viewport.zoom-width/2));
    const screenY=Math.max(112,Math.min(canvasHeight-height-100,viewport.y+anchor.y*viewport.zoom+24));
    return {x:screenX-viewport.x,y:screenY-viewport.y,width,height};
  });
  const gesture=useRef<{x:number;y:number;zoom:number;box:typeof box;corner?:ResizeCorner}>();
  const start=(event:ReactPointerEvent<HTMLElement>,corner?:ResizeCorner)=>{
    if(event.button!==0 || (!corner && (!(event.target as Element).closest('header') || (event.target as Element).closest('button')))) return;
    event.preventDefault();event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    gesture.current={x:event.clientX,y:event.clientY,zoom:scale,box,corner};
  };
  const move=(event:ReactPointerEvent<HTMLElement>)=>{
    const g=gesture.current;if(!g) return;
    const dx=(event.clientX-g.x)/g.zoom,dy=(event.clientY-g.y)/g.zoom;
    setBox(g.corner ? resizeFromCorner(g.box,g.corner,dx,dy,{min:{width:320,height:240}})
      : {...g.box,x:g.box.x+dx,y:g.box.y+dy});
  };
  const finish=(event:ReactPointerEvent<HTMLElement>)=>{
    gesture.current=undefined;
    if(event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  };
  return <div className="research-signpost-world-layer" style={{transform:`translate(${viewport.x}px,${viewport.y}px) scale(${scale})`}}>
    <aside className="research-fog-detail nodrag nopan nowheel is-signpost-window" aria-label={ariaLabel}
      style={{left:box.x,top:box.y,right:'auto',bottom:'auto',width:box.width,height:box.height,maxHeight:'none'}}
      onPointerDown={event=>start(event)} onPointerMove={move} onPointerUp={finish}
      onPointerCancel={event=>{if(gesture.current) setBox(gesture.current.box);finish(event);}}
      onLostPointerCapture={()=>{gesture.current=undefined;}}>
      <span className="research-detail-hat" aria-hidden="true"/>
      {children}
      {RESIZE_CORNERS.map(corner=><button key={corner} type="button" className={`surface-resize-arc ${corner}`}
        aria-label={`调整路标窗口大小 ${corner}`} style={{left:corner.endsWith('right')?'100%':0,top:corner.startsWith('bottom')?'100%':0}}
        onPointerDown={event=>start(event,corner)} onPointerMove={move} onPointerUp={finish}
        onKeyDown={event=>{const dx=event.key==='ArrowRight'?20:event.key==='ArrowLeft'?-20:0;
          const dy=event.key==='ArrowDown'?20:event.key==='ArrowUp'?-20:0;
          if(dx||dy){event.preventDefault();setBox(current=>resizeFromCorner(current,corner,dx,dy,{min:{width:320,height:240}}));}}}/>) }
    </aside>
  </div>;
}
