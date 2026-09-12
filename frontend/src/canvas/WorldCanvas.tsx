import { GlueLayer } from "./GlueLayer";
import { reportInteraction } from "../state/interactions";
import { findGlue, glueGroup, reflowGlueSurfaces, refreshGlue, beginGlueEdit, cancelGlueRefresh, persistGlue, useGlueStore, type GlueBox, type GlueCandidate } from "../state/glue";
import { MapAtlas } from "./MapAtlas";
import {
  Background,
  BackgroundVariant,
  ConnectionMode,
  Controls,
  MarkerType,
  ReactFlow,
  applyNodeChanges,
  useNodesState,
  useReactFlow,
  type Connection,
  type NodeChange,
  type OnNodeDrag,
  type OnInit,
  type OnMove,
  type OnSelectionChangeParams,
  type Viewport,
} from "@xyflow/react";
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { apiErrorMessage, worldApi } from "../api/client";
import { transformationOptions } from "./documentTransformations";
import { importPdf, type PdfImportProgress } from "./importPdf";
import { PdfImportIndicator } from "./PdfImportIndicator";
import { layoutIsPresented } from "./layoutPresentation";
import { SHADOW, isShadow, collectionState, foldedAncestor, hiddenCollectionEdge, shadowLayout, shadowPoints, useCollectionDrag, useCollectionRelease, canReleaseMember, collectionAnchorFromSurface } from "../state/shadowCollection";
import { EquipmentCardNode, EquipmentPanelNode } from "../cards/Equipment";
import { equipmentOriginId, equipmentSurfaceNodes } from "./equipmentLayout";
import { SurfaceBridge } from "../effects/SurfaceBridge";
import { canEquip, equipmentOwner, useEquipmentDrag, useEquipmentPanel } from "../state/equipment";
import { ContainerCardNode } from "../cards/ContainerCard";
import { ancestors, containerDefinition, containerDisplayOwners, containerShowsWorkspace, containerSizes, dropContainer, isContainer, memberSurfacePosition, parentFirst, resizeContainerLayout } from "../state/containers";
import { WorldCardNode } from "../cards/CardFrame";
import { MinisterNode, MINISTER_TYPE } from "../cards/Minister";
import type { CanvasNode, CanvasNodeData } from "../cards/types";
import { EdgeInspector } from "../edges/EdgeInspector";
import { RelationshipConnectionLine } from "../edges/RelationshipConnectionLine";
import { SemanticEdge, type CanvasEdge } from "../edges/SemanticEdge";
import { filterCardsToChunks } from "../state/chunks";
import { getNodeType } from "../state/catalog";
import { buildCardDraft } from "../state/helpers";
import { hasPaletteDrag, readPaletteDrag } from "../palette/dragPayload";
import { getConnectionOptions, validateConnection } from "../state/relationships";
import { useWorldStore } from "../state/worldStore";
import { NODE_SURFACE_SIZE, surfaceLevelForNode, useNodeSurfaceStore, type NodeSurfaceLevel, type SurfaceSize } from "../state/nodeSurfaces";
import { ContourLayer } from "./ContourLayer";
import { LocalMiniMap } from "./LocalMiniMap";
import { GenerationLayer } from "../effects/GenerationLayer";
import {
  displacedPositions,
  nodePositionFromSurfacePosition,
  positionSurfaceAtNodeCenter,
  type SurfaceObstacle,
} from "./nodeDisplacement";

const nodeTypes = { worldCard: WorldCardNode, minister: MinisterNode, container: ContainerCardNode, equipment: EquipmentCardNode, equipmentPanel: EquipmentPanelNode };
const edgeTypes = { semantic: SemanticEdge };

function isScrollableArea(target: EventTarget | null, boundary: HTMLElement): boolean {
  let element = target instanceof HTMLElement ? target : null;
  while (element && element !== boundary) {
    const style = window.getComputedStyle(element);
    const scrollableY = /(auto|scroll)/.test(style.overflowY) && element.scrollHeight > element.clientHeight;
    const scrollableX = /(auto|scroll)/.test(style.overflowX) && element.scrollWidth > element.clientWidth;
    if (scrollableY || scrollableX) return true;
    element = element.parentElement;
  }
  return false;
}

function nodeFromCard(
  card: ReturnType<typeof useWorldStore.getState>["cards"][number],
  surfaceLevel: NodeSurfaceLevel,
  displaced: boolean,
  position: ReturnType<typeof useWorldStore.getState>["cards"][number]["position"],
  windowSize?: SurfaceSize,
): CanvasNode {
  const size = windowSize ?? (surfaceLevel === 'node' ? card.size : NODE_SURFACE_SIZE[surfaceLevel]);
  return {
    id: card.id,
    type: "worldCard",
    position: positionSurfaceAtNodeCenter(position, surfaceLevel),
    data: { card, surfaceLevel, displaced },
    width: size.width, height: size.height, className: undefined,
    style: { width: size.width, height: size.height },
    parentId: undefined,
    extent: undefined,
    // Explicitly clear React Flow's previous folded/culling state on expansion.
    // An omitted flag survives the {...live, ...node} animation merge.
    hidden: false,
    draggable: true,
    dragHandle: surfaceLevel === "workspace" ? ".node-drag-region" : undefined,
    selectable: true,
    connectable: !card.ephemeral,
    zIndex: surfaceLevel === "workspace" ? 24 : surfaceLevel === "inspector" ? 20 : surfaceLevel === "preview" ? 12 : 1,
  };
}

export function WorldCanvas() {
  const [pinToolActive, setPinToolActive] = useState(false);
  const [glueActive, setGlueActive] = useState(false);
  const [gluePreview, setGluePreview] = useState<GlueCandidate>();
  const storedGlueBoxes = useGlueStore(s => s.boxes);
  const glueBonds = useGlueStore(s => s.bonds);
  const glueEvent = useWorldStore(s => s.events.find(event => event.type.startsWith('state_') || event.type.startsWith('card_'))?.id);
  const activeGlueEdits = useGlueStore(s => s.activeEdits);
  const glueSocket = useWorldStore(s => s.socketState);
  const glueDrag = useRef<{ origin: { x: number; y: number }; boxes: Record<string, GlueBox>; latest: Record<string, GlueBox>; candidate?: GlueCandidate }>();
  const [importStatus,setImportStatus]=useState("");
  const [importProgress,setImportProgress]=useState<PdfImportProgress>();
  const importingPdf=useRef(false);
  const wrapper = useRef<HTMLDivElement>(null);
  const clipboardTask = useRef<Promise<unknown>>(Promise.resolve());
  const clipboardPending = useRef(false);
  const cards = useWorldStore((state) => state.cards);
  const catalog = useWorldStore((state) => state.catalog);
  const stressCards = useWorldStore((state) => state.stressCards);
  const edges = useWorldStore((state) => state.edges);
  const legions = useWorldStore((state) => state.legions);
  const activeChunkKeys = useWorldStore((state) => state.activeChunkKeys);
  const viewport = useWorldStore((state) => state.viewport);
  const selectedEdgeId = useWorldStore((state) => state.selectedEdgeId);
  const selectedCardIds = useWorldStore((state) => state.selectedCardIds);
  const selectionRevision = useWorldStore((state) => state.selectionRevision);
  const surfaceLevelsByNodeId = useNodeSurfaceStore((state) => state.surfaceLevels);
  const workspaceSizes = useNodeSurfaceStore((state) => state.workspaceSizes);
  const connectingNodeId = useNodeSurfaceStore((state) => state.connectingNodeId);
  const dragging = useNodeSurfaceStore((state) => state.dragging);
  const setDragging = useNodeSurfaceStore((state) => state.setDragging);
  useEffect(() => {
    if (dragging || activeGlueEdits) return;
    const timer = window.setTimeout(() => void refreshGlue(true).catch(reason =>
      useWorldStore.getState().pushToast({ tone: 'error', title: 'Glue could not synchronize', detail: apiErrorMessage(reason) })), 120);
    return () => window.clearTimeout(timer);
  }, [glueEvent, glueSocket, dragging, activeGlueEdits]);
  const closeInspector = useNodeSurfaceStore((state) => state.closeInspector);
  const closeWorkspace = useNodeSurfaceStore((state) => state.closeWorkspace);
  const dismissSurface = useNodeSurfaceStore((state) => state.dismiss);
  const beginConnection = useNodeSurfaceStore((state) => state.beginConnection);
  const endConnection = useNodeSurfaceStore((state) => state.endConnection);
  const setViewportState = useWorldStore((state) => state.setViewport);
  const updateCardPositions = useWorldStore((state) => state.updateCardPositions);
  const createCard = useWorldStore((state) => state.createCard);
  const updateCard = useWorldStore((state) => state.updateCard);
  const instantiateLegion = useWorldStore((state) => state.instantiateLegion);
  const requestConnection = useWorldStore((state) => state.requestConnection);
  const selectEdge = useWorldStore((state) => state.selectEdge);
  const deleteSelectedEdge = useWorldStore((state) => state.deleteSelectedEdge);
  const deleteCards = useWorldStore((state) => state.deleteCards);
  const selectCards = useWorldStore((state) => state.selectCards);
  const undo = useWorldStore((state) => state.undo);
  const redo = useWorldStore((state) => state.redo);
  const { fitView, getNodes, getViewport, screenToFlowPosition } = useReactFlow<CanvasNode, CanvasEdge>();
  const displayOwners = useMemo(() => containerDisplayOwners(cards, catalog, surfaceLevelsByNodeId), [cards, catalog, surfaceLevelsByNodeId]);

  const renderCards = useMemo(
    () => {
      const visible = filterCardsToChunks([...cards, ...stressCards].filter((c) => !displayOwners.has(c.id) && !equipmentOwner(c, cards)), activeChunkKeys, catalog);
      const ids = new Set(visible.map((c) => c.id));
      return [...visible, ...cards.filter((c) => { const owner = equipmentOwner(c, cards); return !displayOwners.has(c.id) && owner && ids.has(owner.id); })];
    },
    [activeChunkKeys, cards, stressCards, catalog, displayOwners],
  );
  const surfaceLevels = useMemo(() => new Map(renderCards.map((card) => [
    card.id,
    card.type === MINISTER_TYPE ? "node" : surfaceLevelForNode(card.id, surfaceLevelsByNodeId),
  ])), [renderCards, surfaceLevelsByNodeId]);
  const glueBoxes = useMemo(() => reflowGlueSurfaces(storedGlueBoxes, glueBonds, surfaceLevels, workspaceSizes),
    [storedGlueBoxes, glueBonds, surfaceLevels, workspaceSizes]);
  useEffect(() => {
    if (glueBoxes === storedGlueBoxes) return;
    const endEdit = beginGlueEdit();
    useGlueStore.getState().setLayout(glueBoxes);
    void updateCardPositions(Object.entries(glueBoxes).filter(([id, box]) => box !== storedGlueBoxes[id]).map(([id, box]) => ({
      id, position: nodePositionFromSurfacePosition(box, box.level),
    }))).then(() => persistGlue()).catch(reason => useWorldStore.getState().pushToast({ tone: 'error', title: 'Glue layout needs a retry', detail: apiErrorMessage(reason) })).finally(endEdit);
  }, [glueBoxes, storedGlueBoxes, updateCardPositions]);
  const surfaceObstacles = useMemo<SurfaceObstacle[]>(() => renderCards.flatMap<SurfaceObstacle>((card) => {
    if (isContainer(card, catalog) || card.parent_id || card.equipment) return [];
    const level = surfaceLevels.get(card.id);
    return level === "inspector" || level === "workspace" ? [{ card, level, size: level === "workspace" ? workspaceSizes[card.id] : undefined }] : [];
  }), [renderCards, surfaceLevels, catalog, workspaceSizes]);
  const displacedById = useMemo(
    () => displacedPositions(renderCards.filter((c) => !isContainer(c, catalog) && !c.parent_id && !c.equipment), surfaceObstacles, surfaceLevels),
    [renderCards, surfaceLevels, surfaceObstacles, catalog],
  );
  const equipmentPanels = useEquipmentPanel((state) => state.openIds);
  const equipmentPositions = useEquipmentPanel((state) => state.positions);
  const mappedNodes = useMemo(() => {
    const byId = new Map(renderCards.map((c) => [c.id, c]));
    const frameSizes = containerSizes(renderCards, catalog, surfaceLevels, workspaceSizes);
    return parentFirst(renderCards).flatMap<CanvasNode>((card) => {
      const level = surfaceLevels.get(card.id) ?? "preview";
      const folded=foldedAncestor(card,renderCards);
      const displaced = displacedById.get(card.id);
      let node = nodeFromCard(card, level, displaced?.displaced ?? false, displaced?.position ?? card.position, level === "workspace" ? workspaceSizes[card.id] : undefined);
      if (card.type === MINISTER_TYPE) {
        // The chat opens beside the orb; its world position and radius never shift.
        node = { ...node, type: "minister", position: card.position, data: { ...node.data, displaced: false },
          width: card.size.width, height: card.size.height, style: { width: card.size.width, height: card.size.height },
          dragHandle: ".minister-drag-region", connectable: false,
          zIndex: ["inspector", "workspace"].includes(surfaceLevelsByNodeId[card.id]) ? 28 : 2 };
      }
      if (isContainer(card, catalog)) {
        const { width, height } = frameSizes.get(card.id)!;
        node = { ...node, type: "container", position: card.position, width, height, style: { width, height }, zIndex: 0,
          dragHandle: ".container-drag-region", connectable: containerDefinition(card, catalog)!.connectable };
        if(isShadow(card)) {
          const rect=shadowLayout(card,renderCards,surfaceLevels,catalog);
          node={...node,position:{x:rect.x,y:rect.y},className:"shadow-flow-node",data:{...node.data,shadowOrigin:{x:rect.x,y:rect.y},shadowWidth:rect.width,shadowHeight:rect.height,shadowOutline:shadowPoints(rect.width,rect.height,rect.rects)}};
        }
      }
      if(folded){
        const index=renderCards.filter(c=>c.parent_id===folded.id).findIndex(c=>c.id===card.id);
        const total=renderCards.filter(c=>c.parent_id===folded.id).length;
        const shown=card.parent_id===folded.id && collectionState(folded)==="stacked" && index>=Math.max(0,total-6);
        node={...node,type:"worldCard",hidden:!shown,draggable:false,selectable:false,connectable:false,
          data:{...node.data,surfaceLevel:"preview",collectionOwner:folded.id,stackIndex:index},
          style:{width:NODE_SURFACE_SIZE.preview.width,height:NODE_SURFACE_SIZE.preview.height},zIndex:12+Math.max(0,index-Math.max(0,total-6))};
        if(shown)return {...node,parentId:folded.id,position:{x:25+(index-Math.max(0,total-6))*9,y:70+(index-Math.max(0,total-6))*9}};
        if(card.parent_id===folded.id&&collectionState(folded)==="minimal")return {...node,parentId:folded.id,position:{x:66-NODE_SURFACE_SIZE.preview.width/2,y:64-NODE_SURFACE_SIZE.preview.height/2}};
      }
      const equipmentAgent = equipmentOwner(card, cards);
      if (equipmentAgent && byId.has(equipmentAgent.id)) {
        const owner = equipmentAgent;
        const ownerLevel = surfaceLevels.get(owner.id) ?? "preview";
        const index = renderCards.filter((c) => equipmentOwner(c, cards)?.id === owner.id).findIndex((c) => c.id === card.id);
        const ownerHeight = frameSizes.get(owner.id)?.height
          ?? (ownerLevel === "workspace" ? workspaceSizes[owner.id]?.height : undefined)
          ?? (ownerLevel === "node" ? owner.size.height : NODE_SURFACE_SIZE[ownerLevel].height);
        return equipmentSurfaceNodes(node, owner.id, ownerLevel, index, equipmentPanels.includes(owner.id), equipmentPositions[card.id], ownerHeight);
      }
      if (card.parent_id && byId.has(card.parent_id)) {
        const parent = byId.get(card.parent_id)!;
        const origin=isShadow(parent)?shadowLayout(parent,renderCards,surfaceLevels,catalog):parent.position;
        const position = isContainer(card, catalog) ? node.position : memberSurfacePosition(card, parent, level, catalog);
        return { ...node, parentId: parent.id, position: { x: position.x - origin.x, y: position.y - origin.y } };
      }
      const glued = glueBoxes[card.id];
      if (glued && node.type === 'worldCard' && !node.parentId) node = { ...node, position: { x: glued.x, y: glued.y },
        width: glued.width, height: glued.height, style: { width: glued.width, height: glued.height }, className: 'is-glued', data: { ...node.data, displaced: false } };
      return node;
    }).flatMap((node): CanvasNode[] => {
      if (node.type === "equipment" || node.data.equipmentDetail || !catalog.node_types.find((type) => type.id === node.data.card.type)?.traits.includes("core.agent")) return [node];
      const count = renderCards.filter((card) => equipmentOwner(card, cards)?.id === node.id).length;
      return [node, { id: `${node.id}:equipment`, type: "equipmentPanel", data: node.data, parentId: node.id,
        position: { x: 0, y: Number(node.style?.height ?? NODE_SURFACE_SIZE[node.data.surfaceLevel].height) + 8 },
        style: { width: 320, height: 46 + Math.max(2, count + 1) * 48 },
        hidden: !equipmentPanels.includes(node.id), draggable: false, selectable: false, connectable: false, zIndex: 24 }];
    });
  }, [displacedById, renderCards, surfaceLevels, surfaceLevelsByNodeId, catalog, cards, equipmentPanels, equipmentPositions, workspaceSizes, glueBoxes]);
  const [nodes, setNodes] = useNodesState<CanvasNode>(mappedNodes);
  const onNodesChange = useCallback((changes: NodeChange<CanvasNode>[]) => {
    setNodes(current => {
      let next = applyNodeChanges(changes, current);
      for (const change of changes) {
        if (change.type !== 'dimensions' || !change.resizing || !change.dimensions) continue;
        const parent = cards.find(card => card.id === change.id);
        if (!parent || !isContainer(parent, catalog)) continue;
        if (containerShowsWorkspace(parent, catalog, surfaceLevels.get(parent.id))) continue;
        const layout = resizeContainerLayout(cards, catalog, surfaceLevels, parent.id, change.dimensions, workspaceSizes);
        const reflowed = new Map(cards.map(card => [card.id, { ...card, position: layout.positions.get(card.id) ?? card.position }]));
        next = next.map(node => {
          if (node.id === parent.id) return { ...node, width: layout.size.width, height: layout.size.height, measured: layout.size, style: { ...node.style, ...layout.size } };
          if (!layout.positions.has(node.id)) return node;
          const card = reflowed.get(node.id)!;
          const owner = reflowed.get(card.parent_id ?? '');
          if (!owner) return node;
          const position = isContainer(card, catalog) ? card.position : memberSurfacePosition(card, owner, surfaceLevels.get(card.id) ?? 'preview', catalog);
          return { ...node, position: { x: position.x - owner.position.x, y: position.y - owner.position.y } };
        });
      }
      return next;
    });
  }, [cards, catalog, surfaceLevels, setNodes, workspaceSizes]);
  const nodesRef = useRef(nodes);
  const positionAnimation = useRef<number>();
  const activeDragIds = useRef(new Set<string>());
  const cancelledDrag=useRef(false);
  const appliedSelectionRevision = useRef(selectionRevision);

  const cancelPositionAnimation = useCallback(() => {
    if (positionAnimation.current === undefined) return;
    cancelAnimationFrame(positionAnimation.current);
    positionAnimation.current = undefined;
  }, []);

  useEffect(() => {
    nodesRef.current = nodes;
  }, [nodes]);

  // Keep the drag silhouette until React Flow has presented the committed layout.
  // Clearing it in the request's finally block exposes the previous outline for a frame.
  useEffect(()=>{
    if(dragging)return;
    const positions=useCollectionDrag.getState().positions;
    if(!Object.keys(positions).length)return;
    // Compare against the reconciled layout, not the requested coordinates:
    // a failed save can legitimately restore the previous card position.
    const presented=layoutIsPresented(nodes,mappedNodes);
    if(presented)useCollectionDrag.getState().set();
  },[nodes,mappedNodes,dragging]);

  useEffect(() => {
    cancelPositionAnimation();
    if (connectingNodeId || dragging) return;
    const currentById = new Map(nodesRef.current.map((node) => [node.id, node]));
    const absolute=(node:CanvasNode|undefined):{x:number;y:number}=>{
      if(!node)return {x:0,y:0};
      const parent=node.parentId?absolute(currentById.get(node.parentId)): {x:0,y:0};
      return {x:node.position.x+parent.x,y:node.position.y+parent.y};
    };
    const starts = new Map(mappedNodes.map((node) => [
      node.id,
      // A changed parent changes the coordinate space, not the visual location.
      // Member reflow must remain inside the resized frame, including during undo.
      currentById.has(node.id) ? (()=>{
        const previous=currentById.get(node.id)!;
        // Member reflow must remain inside the resized frame, including during undo.
        if(previous.parentId===node.parentId)return !node.parentId ? previous.position : node.position;
        const old=absolute(previous),parent=absolute(node.parentId?currentById.get(node.parentId):undefined);
        return {x:old.x-parent.x,y:old.y-parent.y};
      })() : node.position,
    ]));

    const applyProgress = (eased: number) => {
      setNodes((currentNodes) => {
        const liveById = new Map(currentNodes.map((node) => [node.id, node]));
        return mappedNodes.map((node) => {
          const live = liveById.get(node.id);
          if (live && activeDragIds.current.has(node.id)) {
            return {
              ...live,
              ...node,
              position: live.position,
              dragging: live.dragging,
            };
          }
          if (live?.resizing) return { ...node, ...live };
          // Commit canonical geometry without discarding React Flow's live
          // selection. Updates can arrive mid-marquee; its membership cache
          // will not reselect nodes whose selected flag we accidentally erase.
          if (eased === 1) return { ...node, selected: live?.selected };
          const start = starts.get(node.id) ?? node.position;
          const previous=currentById.get(node.id);
          const outline=node.data.shadowOutline as {x:number;y:number}[]|undefined;
          const oldOutline=previous?.data.shadowOutline as typeof outline;
          const width=Number(previous?.style?.width??node.style?.width)+(Number(node.style?.width)-Number(previous?.style?.width??node.style?.width))*eased;
          const height=Number(previous?.style?.height??node.style?.height)+(Number(node.style?.height)-Number(previous?.style?.height??node.style?.height))*eased;
          return {
            ...live,
            ...node,
            ...(node.hidden&&previous&&!previous.hidden&&eased<1?{hidden:false,style:{...node.style,opacity:1-eased,pointerEvents:"none" as const},data:{...node.data,collectionFading:true}}:{}),
            ...(outline?{style:{...node.style,width,height},data:{...node.data,shadowWidth:width,shadowHeight:height,shadowOutline:outline.map((p,i)=>({x:(oldOutline?.[i]?.x??p.x)+(p.x-(oldOutline?.[i]?.x??p.x))*eased,y:(oldOutline?.[i]?.y??p.y)+(p.y-(oldOutline?.[i]?.y??p.y))*eased}))}}:{}),
            position: {
              x: start.x + (node.position.x - start.x) * eased,
              y: start.y + (node.position.y - start.y) * eased,
            },
          };
        });
      });
    };

    const moving = mappedNodes.some((node) => {
      if (activeDragIds.current.has(node.id) || glueBoxes[node.id]) return false;
      const start = starts.get(node.id) ?? node.position;
      if(isShadow(node.data.card)&&currentById.has(node.id)) {
        const old=currentById.get(node.id)!;
        if(old.data.card.config.display_state!==node.data.card.config.display_state||old.style?.width!==node.style?.width||old.style?.height!==node.style?.height)return true;
      }
      return Math.abs(node.position.x - start.x) > 0.1
        || Math.abs(node.position.y - start.y) > 0.1;
    });
    const structuralChange=mappedNodes.some(node=>{
      const old=currentById.get(node.id);
      return old&&(old.parentId!==node.parentId||old.hidden!==node.hidden||old.data.surfaceLevel!==node.data.surfaceLevel||
        (isShadow(node.data.card)&&collectionState(old.data.card)!==collectionState(node.data.card)));
    });
    // Do not replay pointer movement after persistence; only morph structural changes.
    if (!moving || !structuralChange) {
      applyProgress(1);
      return;
    }

    const startedAt = performance.now();
    const reparenting=mappedNodes.some(node=>currentById.has(node.id)&&currentById.get(node.id)!.parentId!==node.parentId);
    const collapsing=mappedNodes.some(node=>isShadow(node.data.card)&&collectionState(node.data.card)!=="expanded");
    const duration=reparenting?SHADOW.absorbMs:collapsing?SHADOW.collapseMs:SHADOW.expandMs;
    const tick = (now: number) => {
      const progress = Math.min(1, (now - startedAt) / (window.matchMedia("(prefers-reduced-motion: reduce)").matches?1:duration));
      const eased = 1 - Math.pow(1 - progress, 3);
      applyProgress(eased);
      if (progress < 1) positionAnimation.current = requestAnimationFrame(tick);
      else positionAnimation.current = undefined;
    };
    positionAnimation.current = requestAnimationFrame(tick);
    return cancelPositionAnimation;
  }, [cancelPositionAnimation, connectingNodeId, dragging, mappedNodes, setNodes, glueBoxes]);

  useEffect(() => {
    if (appliedSelectionRevision.current === selectionRevision) return;
    appliedSelectionRevision.current = selectionRevision;
    const selected = new Set(selectedCardIds);
    setNodes((currentNodes) => currentNodes.map((node) => ({
      ...node,
      selected: selected.has(node.id),
    })));
  }, [selectedCardIds, selectionRevision, setNodes]);

  const visibleNodeIds = useMemo(() => new Set(renderCards.map((card) => card.id)), [renderCards]);
  const displayEndpoint = useCallback((id: string) => {
    if (displayOwners.has(id)) return displayOwners.get(id)!;
    const card = cards.find((item) => item.id === id);
    const folded=card&&foldedAncestor(card,cards);
    if(folded)return folded.id;
    return card && nodes.find((node) => node.id === id)?.hidden ? equipmentOwner(card, cards)?.id ?? id : id;
  }, [cards, nodes, displayOwners]);
  const flowEdges = useMemo<CanvasEdge[]>(
    () => edges
      .filter(edge => !glueBonds.some(b => glueBoxes[b.a] && glueBoxes[b.b] && (b.a === edge.source && b.b === edge.target || b.b === edge.source && b.a === edge.target)))
      .filter((edge) => visibleNodeIds.has(displayEndpoint(edge.source)) && visibleNodeIds.has(displayEndpoint(edge.target)))
      .filter((edge) => !hiddenCollectionEdge(edge.source, edge.target, cards))
      .map<CanvasEdge>((edge) => ({
        id: edge.id,
        source: displayEndpoint(edge.source),
        target: displayEndpoint(edge.target),
        type: "semantic",
        data: { relationship: edge.relationship, direction: edge.direction, sourceCardId: edge.source, targetCardId: edge.target },
        selected: edge.id === selectedEdgeId,
        markerEnd: {
          type: MarkerType.ArrowClosed,
          width: 14,
          height: 14,
          color: "var(--edge-arrow)",
        },
        markerStart: edge.direction === "bidirectional" ? {
          type: MarkerType.ArrowClosed,
          width: 14,
          height: 14,
          color: "var(--edge-arrow)",
        } : undefined,
        interactionWidth: 24,
      })).filter((edge) => edge.source !== edge.target),
      [edges, cards, selectedEdgeId, visibleNodeIds, displayEndpoint, glueBonds, glueBoxes],
  );

  const dimensions = useCallback(() => ({
    width: wrapper.current?.clientWidth ?? window.innerWidth,
    height: wrapper.current?.clientHeight ?? window.innerHeight,
  }), []);

  const commitViewport = useCallback((next: Viewport) => {
    const size = dimensions();
    setViewportState({ ...next, ...size });
  }, [dimensions, setViewportState]);

  const onMoveEnd: OnMove = useCallback((event, next) => {
    commitViewport(next);
    if (event || document.activeElement?.closest('.world-controls')) reportInteraction({ type: 'viewport', ...next });
  }, [commitViewport]);
  const onInit: OnInit<CanvasNode, CanvasEdge> = useCallback((instance) => {
    commitViewport(instance.getViewport());
  }, [commitViewport]);

  useEffect(() => {
    const onResize = () => commitViewport(getViewport());
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [commitViewport, getViewport]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      // Modal workspaces and embedded readers own their keyboard shortcuts.
      if (event.defaultPrevented || document.querySelector("dialog:modal") || target?.closest(".library-reader, input, textarea, select, [contenteditable='true']")) return;
      const modifier = event.ctrlKey || event.metaKey;
      const key = event.key.toLowerCase();
      if (modifier && !event.altKey && !event.shiftKey && ["c", "x", "v"].includes(key)) {
        if (event.isComposing || target?.isContentEditable || target?.closest("[contenteditable], [role='textbox'], .xterm")
          || window.getSelection()?.toString() || useNodeSurfaceStore.getState().dragging) return;
        const state = useWorldStore.getState();
        if (key === "v" ? !state.clipboard && !clipboardPending.current : !state.selectedCardIds.length) return;
        event.preventDefault();
        if (event.repeat) return;
        if (key === "v") clipboardTask.current = clipboardTask.current.then(() => state.pasteSelection());
        else {
          const ids = [...state.selectedCardIds];
          clipboardPending.current = true;
          clipboardTask.current = state.copySelection().then(async copied => {
            if (copied && key === "x") await state.deleteCards(ids);
          }).finally(() => { clipboardPending.current = false; });
        }
        return;
      }
      if (!modifier && !event.altKey && event.key.toLowerCase() === "f") {
        if (event.defaultPrevented || event.isComposing || event.repeat
          || target?.isContentEditable
          || target?.closest("input, textarea, select, [role='textbox'], .xterm")
          || useNodeSurfaceStore.getState().dragging) return;
        const selected = new Set(selectedCardIds);
        const focusNodes = getNodes().filter((node) => selected.has(node.id) && !node.hidden);
        if (focusNodes.length === 0) return;
        event.preventDefault();
        void fitView({
          nodes: focusNodes,
          padding: 0.15,
          minZoom: 0.12,
          maxZoom: 2.2,
          duration: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 300,
        }).then(() => reportInteraction({ type: 'focus', ids: focusNodes.map(node => node.id) }));
        return;
      }
      if (modifier && event.key.toLowerCase() === "z") {
        event.preventDefault();
        if (event.shiftKey) void redo();
        else void undo();
        return;
      }
      if (modifier && event.key.toLowerCase() === "y") {
        event.preventDefault();
        void redo();
        return;
      }
      if (event.key === "Delete" || event.key === "Backspace") {
        if (selectedCardIds.length > 0) {
          event.preventDefault();
          selectedCardIds.forEach((id) => dismissSurface(id));
          void deleteCards(selectedCardIds);
          return;
        }
        if (selectedEdgeId) {
          event.preventDefault();
          void deleteSelectedEdge();
        }
      }
      if (event.key === "Escape") {
        if (Object.values(surfaceLevelsByNodeId).includes("workspace")) closeWorkspace();
        else if (Object.values(surfaceLevelsByNodeId).includes("inspector")) closeInspector();
        else if (selectedEdgeId) selectEdge(undefined);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [closeInspector, closeWorkspace, deleteCards, deleteSelectedEdge, dismissSurface, fitView, getNodes, redo, selectEdge, selectedCardIds, selectedEdgeId, surfaceLevelsByNodeId, undo]);

  const onNodeDragStart: OnNodeDrag<CanvasNode> = useCallback((_event, node, draggedNodes) => {
    cancelledDrag.current=false;
    cancelPositionAnimation();
    if ((glueActive || glueBoxes[node.id]) && node.type === 'worldCard' && !node.parentId && !node.data.card.ephemeral && !node.data.equipmentDetail) {
      cancelGlueRefresh();
      const ids = glueGroup(node.id, glueBonds);
      draggedNodes.forEach(n => glueGroup(n.id, glueBonds).forEach(id => ids.add(id)));
      const boxes = Object.fromEntries(nodesRef.current.filter(n => ids.has(n.id) && n.type === 'worldCard' && !n.parentId).map(n => [n.id,
        { x: n.position.x, y: n.position.y, width: Number(n.style?.width), height: Number(n.style?.height), level: n.data.surfaceLevel }]));
      glueDrag.current = { origin: { x: boxes[node.id].x, y: boxes[node.id].y }, boxes, latest: boxes };
      useEquipmentDrag.getState().set();
      setDragging(true);
      activeDragIds.current = ids;
      return;
    }
    useEquipmentDrag.getState().set(node.data.card.type !== MINISTER_TYPE && !node.data.equipmentDetail && draggedNodes.length <= 1 ? node.data.card : undefined);
    setDragging(true);
    activeDragIds.current.clear();
    activeDragIds.current.add(node.id);
    draggedNodes.forEach((draggedNode) => activeDragIds.current.add(draggedNode.id));
  }, [cancelPositionAnimation, setDragging, glueActive, glueBoxes, glueBonds]);

  const equipmentDropOwner = useCallback((resource: CanvasNodeData["card"], x: number, y: number) => {
    return cards.find((candidate) => {
      if (activeDragIds.current.has(candidate.id) || !canEquip(resource, candidate, catalog, cards)) return false;
      const box = wrapper.current?.querySelector(`[data-equip-target="${candidate.id}"]`)?.getBoundingClientRect();
      return box && x > box.left && x < box.right && y > box.top && y < box.bottom;
    });
  }, [cards, catalog]);
  const transformationTarget = useCallback((event: MouseEvent | TouchEvent, node: CanvasNode) => {
    if (!("clientX" in event)) return;
    const stackedIds = [...new Set(document.elementsFromPoint(event.clientX, event.clientY).map(element => element.closest('.react-flow__node')?.getAttribute('data-id')).filter(Boolean))];
    for (const id of stackedIds) {
      const target = cards.find(card => card.id === id);
      if (!target) continue;
      const option = transformationOptions(catalog, node.data.card, target)[0];
      if (!option) continue;
      const element = wrapper.current?.querySelector<HTMLElement>(`.react-flow__node[data-id="${CSS.escape(target.id)}"]`);
      const rect = element?.getBoundingClientRect();
      if (rect && event.clientX > rect.left + 32 && event.clientX < rect.right - 32 && event.clientY > rect.top + 70 && event.clientY < rect.bottom - 32) return { target, option, element };
    }
  }, [cards, catalog]);
  const clearTransformationHints = () => wrapper.current?.querySelectorAll("[data-transformation-hint]").forEach(element => element.removeAttribute("data-transformation-hint"));
  const clearContainerDropHint = useCallback(() => {
    wrapper.current?.querySelectorAll<HTMLElement>("[data-member-drop]").forEach((frame) => {
      delete frame.dataset.memberDrop;
      delete frame.dataset.memberDropSide;
    });
  }, []);
  useEffect(() => {
    const cancel=()=>{
      useCollectionDrag.getState().set();
      clearContainerDropHint();
      if(!activeDragIds.current.size)return;
      cancelledDrag.current=true;activeDragIds.current.clear();setDragging(false);setNodes(mappedNodes);
    };
    const escape=(event:KeyboardEvent)=>{if(event.key==="Escape"&&activeDragIds.current.size){event.preventDefault();cancel();}};
    window.addEventListener("blur", cancel);
    window.addEventListener("pointercancel", cancel);
    window.addEventListener("keydown",escape);
    return () => {
      clearContainerDropHint();
      window.removeEventListener("blur", cancel);
      window.removeEventListener("pointercancel", cancel);
      window.removeEventListener("keydown",escape);
    };
  }, [clearContainerDropHint,mappedNodes,setNodes,setDragging]);
  const onNodeDrag: OnNodeDrag<CanvasNode> = useCallback((event, node) => {
    if(cancelledDrag.current)return;
    // Whole collections translate through React Flow's parent transform. Defer
    // drop hit testing to release instead of scanning/morphing the world per move.
    if(isShadow(node.data.card))return;
    clearContainerDropHint();
    clearTransformationHints();
    const gluing = glueDrag.current;
    if (gluing) {
      const dx = node.position.x - gluing.origin.x, dy = node.position.y - gluing.origin.y;
      const moved = Object.fromEntries(Object.entries(gluing.boxes).map(([id, b]) => [id, { ...b, x: b.x + dx, y: b.y + dy }]));
      const targets = Object.fromEntries(nodesRef.current.filter(n => !gluing.boxes[n.id] && n.type === 'worldCard' && !n.parentId && !n.hidden && !n.data.card.ephemeral && !n.data.equipmentDetail).map(n => [n.id,
        { x: n.position.x, y: n.position.y, width: Number(n.style?.width), height: Number(n.style?.height), level: n.data.surfaceLevel }]));
      gluing.latest = moved;
      gluing.candidate = glueActive ? findGlue(moved, targets, 16 / getViewport().zoom) : undefined;
      setGluePreview(gluing.candidate);
      setNodes(current => current.map(n => moved[n.id] ? { ...n, position: { x: moved[n.id].x, y: moved[n.id].y } } : n));
      return;
    }
    const transformation = transformationTarget(event, node);
    transformation?.element?.setAttribute("data-transformation-hint", `${transformation.option[1].label}: ${node.data.card.name}`);
    const member = node.data.card;
    if (!node.data.equipmentDetail && !member.ephemeral && member.type !== MINISTER_TYPE) {
      const parent = cards.find((c) => c.id === node.parentId);
      const origin=parent&&(isShadow(parent)?shadowLayout(parent,cards,surfaceLevels,catalog):parent.position);
      const surface = origin ? { x: node.position.x + origin.x, y: node.position.y + origin.y } : node.position;
      const position = isContainer(member, catalog) ? surface : nodePositionFromSurfacePosition(surface, node.data.surfaceLevel);
      if(isShadow(parent)&&!useCollectionRelease.getState().active[parent!.id])useCollectionDrag.getState().set(member.id,position);
      const point = { x: position.x + 48, y: position.y + 48 };
      const sizes = new Map(nodesRef.current.map((item) => [item.id, { width: Number(item.style?.width), height: Number(item.style?.height) }]));
      const destination = dropContainer(cards, member, point, catalog, sizes);
      const paint = (id: string, mode: string) => {
        const frame = wrapper.current?.querySelector<HTMLElement>(`.container-frame[data-card-id="${id}"]`);
        const container = cards.find((c) => c.id === id);
        if (!frame || !container) return;
        const size = sizes.get(id) ?? container.size;
        const distances = [Math.abs(point.y-container.position.y), Math.abs(point.x-container.position.x-size.width), Math.abs(point.y-container.position.y-size.height), Math.abs(point.x-container.position.x)];
        frame.dataset.memberDrop = mode;
        frame.dataset.memberDropSide = ["top","right","bottom","left"][distances.indexOf(Math.min(...distances))];
      };
      if (destination?.id !== member.parent_id && (!isShadow(parent)||useCollectionRelease.getState().active[parent!.id])) {
        if (member.parent_id) paint(member.parent_id, "leave");
        if (destination) paint(destination.id, "enter");
      }
    }
    const resource = useEquipmentDrag.getState().resource;
    if (resource?.id !== node.id || !("clientX" in event)) return;
    useEquipmentDrag.getState().set(resource, equipmentDropOwner(resource, event.clientX, event.clientY)?.id);
  }, [equipmentDropOwner, clearContainerDropHint, cards, catalog, transformationTarget, glueActive, getViewport, setNodes]);

  const onNodeDragStop: OnNodeDrag<CanvasNode> = useCallback((_event, node, draggedNodes) => {
    if(cancelledDrag.current){activeDragIds.current.clear();setDragging(false);setNodes(mappedNodes);return;}
    clearContainerDropHint();
    cancelPositionAnimation();
    clearTransformationHints();
    const gluing = glueDrag.current;
    if (gluing) {
      const candidate = gluing.candidate;
      const layout = Object.fromEntries(Object.entries(gluing.latest).map(([id, b]) => [id, { ...b, x: b.x + (candidate?.dx ?? 0), y: b.y + (candidate?.dy ?? 0) }]));
      if (candidate) {
        const target = nodesRef.current.find(n => n.id === candidate.b)!;
        layout[target.id] = { x: target.position.x, y: target.position.y, width: Number(target.style?.width), height: Number(target.style?.height), level: target.data.surfaceLevel };
      }
      if (candidate || Object.keys(layout).some(id => glueBoxes[id])) useGlueStore.getState().setLayout(layout, candidate);
      glueDrag.current = undefined;
      setGluePreview(undefined);
      void updateCardPositions(Object.entries(layout).map(([id, b]) => ({ id, position: nodePositionFromSurfacePosition(b, b.level) })))
        .then(() => persistGlue()).catch(reason => useWorldStore.getState().pushToast({ tone: 'error', title: 'Glue layout needs a retry', detail: apiErrorMessage(reason) })).finally(() => {
        activeDragIds.current.clear(); setDragging(false);
      });
      return;
    }
    const transformation = draggedNodes.length <= 1 ? transformationTarget(_event, node) : undefined;
    if (transformation) {
      useEquipmentDrag.getState().set();
      void (async () => {
        try {
          const [source, target] = await Promise.all([worldApi.getNodeDocument(node.id), worldApi.getNodeDocument(transformation.target.id)]);
          const request = { source_id: node.id, source_revision: source.revision, expected_revision: target.revision };
          const preview = await worldApi.transformDocument(transformation.target.id, transformation.option[0], request);
          const skills = Array.isArray(source.value.skills) ? source.value.skills.length : null;
          if (window.confirm(`${String(preview.label)} "${node.data.card.name}"?\n${skills === null ? "" : `${skills} skills will be added.\n`}The source card is consumed only after a successful commit. Its package snapshot is preserved.`)) {
            await worldApi.transformDocument(transformation.target.id, transformation.option[0], { ...request, confirm: true });
          }
        } catch (error) { window.alert(String(error)); }
        finally { activeDragIds.current.clear(); setDragging(false); await useWorldStore.getState().refreshWorld(); }
      })();
      return;
    }
    const targetId = useEquipmentDrag.getState().targetId;
    useEquipmentDrag.getState().set();
    if (targetId) {
      const owner = cards.find((card) => card.id === targetId)!;
      const relationship = getConnectionOptions(catalog, owner.type, node.data.card.type)[0].value;
      void updateCard(node.id, { parent_id: null, equipment: { owner_id: targetId, relationship } }).finally(() => {
        activeDragIds.current.clear(); setDragging(false);
      });
      return;
    }
    const moved = draggedNodes.length > 0 ? draggedNodes : [node];
    const movedIds = new Set(moved.map((n) => n.id));
    moved.filter((item) => item.data.equipmentDetail).forEach((item) => useEquipmentPanel.getState().move(item.id, item.position));
    const updates = moved.filter((n) => !n.data.equipmentDetail && !ancestors(cards, n.data.card).some((parent) => movedIds.has(parent.id))).map((draggedNode) => {
      const parent = cards.find((c) => c.id === draggedNode.parentId);
      const origin=parent&&(isShadow(parent)?shadowLayout(parent,cards,surfaceLevels,catalog):parent.position);
      let surfacePosition = origin
        ? { x: draggedNode.position.x + origin.x, y: draggedNode.position.y + origin.y }
        : { ...draggedNode.position };
      if(isShadow(draggedNode.data.card)) {
        const rect=shadowLayout(draggedNode.data.card,cards,surfaceLevels,catalog);
        surfacePosition=collectionAnchorFromSurface(surfacePosition,draggedNode.data.card.position,rect);
      }
      return {
        id: draggedNode.id,
        position: isContainer(draggedNode.data.card, catalog) ? surfacePosition
          : nodePositionFromSurfacePosition(surfacePosition, draggedNode.data.surfaceLevel),
      };
    });
    const sizes = new Map(nodesRef.current.map((item) => [item.id, { width: Number(item.style?.width), height: Number(item.style?.height) }]));
    void updateCardPositions(updates.map((update) => {
      const member = cards.find((card) => card.id === update.id)!;
      if (member.ephemeral || member.type === MINISTER_TYPE || containerDefinition(member, catalog)?.parentable === false) return update;
      const owner=cards.find(c=>c.id===member.parent_id);
      if(owner&&isShadow(owner)) {
        const release=canReleaseMember(Boolean(useCollectionRelease.getState().active[owner.id]),{x:update.position.x+48,y:update.position.y+48},shadowLayout(owner,cards,surfaceLevels,catalog));
        // In normal layout mode the silhouette follows the member; membership stays locked.
        // In release mode test the saved silhouette, not the moving member's expanded hull.
        return {...update,parent_id:release?(owner.parent_id??null):owner.id};
      }
      const destination = dropContainer(cards, member, { x: update.position.x + 48, y: update.position.y + 48 }, catalog, sizes);
      if(destination&&isShadow(destination)&&collectionState(destination)!=="expanded")return {...update,position:member.position,parent_id:destination.id};
      return { ...update, parent_id: destination?.id ?? null };
    })).finally(() => {
      activeDragIds.current.clear();
      setDragging(false);
    });
  }, [cancelPositionAnimation, cards, setDragging, updateCardPositions, updateCard, catalog, clearContainerDropHint, transformationTarget, glueBoxes]);

  const onConnect = useCallback((connection: Connection) => {
    requestConnection(connection.source, connection.target);
  }, [requestConnection]);

  const isValidConnection = useCallback((connection: Connection | CanvasEdge) => {
    const source = renderCards.find((card) => card.id === connection.source);
    const target = renderCards.find((card) => card.id === connection.target);
    return validateConnection(
      catalog,
      connection.source,
      connection.target,
      source?.type,
      target?.type,
      edges,
      cards,
    ).valid;
  }, [catalog, edges, renderCards, cards]);

  const onSelectionChange = useCallback(({ nodes: selectedNodes }: OnSelectionChangeParams<CanvasNode, CanvasEdge>) => {
    selectCards(selectedNodes.map((node) => node.id));
  }, [selectCards]);

  const onDrop = useCallback((event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    clearTransformationHints();
    if(event.dataTransfer.files.length){
      const files=Array.from(event.dataTransfer.files).filter(f=>/\.pdf$/i.test(f.name));
      if(!files.length||importingPdf.current)return;
      if(!getNodeType(catalog,"library.paper")){setImportStatus("请先启用 Library 插件");return;}
      const position=screenToFlowPosition({x:event.clientX,y:event.clientY});
      const parent=dropContainer(cards,{id:"",type:"library.paper"} as typeof cards[number],position,catalog);
      importingPdf.current=true;
      void (async()=>{
        let completed=0;
        try {
          for(const [i,file] of files.entries()) {
            setImportStatus(`${i+1} / ${files.length} · ${file.name}`);
            const card=await importPdf(file,{x:position.x+(i%3)*40,y:position.y+Math.floor(i/3)*40},parent?.id,setImportProgress);
            useWorldStore.getState().acceptImportedCard(card);
            completed++;
          }
          setImportStatus(`已导入 ${completed} 篇 PDF`);
        } catch(e) { setImportStatus(`已导入 ${completed} 篇；${String(e)}`); }
        finally { importingPdf.current=false;setImportProgress(undefined); }
      })();
      return;
    }
    const payload = readPaletteDrag(event.dataTransfer);
    if (!payload) return;
    const position = screenToFlowPosition({ x: event.clientX, y: event.clientY });
    if (payload.kind === "node") {
      const definition = getNodeType(catalog, payload.type);
      if (!definition) return;
      const resource = { ...buildCardDraft(payload.type, position, definition), id: "" };
      const transformation = transformationTarget(event.nativeEvent, { data: { card: resource } } as CanvasNode);
      if (transformation) {
        useEquipmentDrag.getState().set();
        void (async () => {
          try {
            const target = await worldApi.getNodeDocument(transformation.target.id);
            const request = { source_type: payload.type, expected_revision: target.revision };
            const preview = await worldApi.transformDocument(transformation.target.id, transformation.option[0], request);
            if (window.confirm(`${String(preview.label)} "${definition.label}" into "${transformation.target.name}"?`)) {
              await worldApi.transformDocument(transformation.target.id, transformation.option[0], { ...request, confirm: true });
              await useWorldStore.getState().refreshWorld();
            }
          } catch (error) { window.alert(String(error)); }
        })();
        return;
      }
      const owner = equipmentDropOwner(resource, event.clientX, event.clientY);
      useEquipmentDrag.getState().set();
      if (owner) {
        void createCard(payload.type, position, { equipment: { owner_id: owner.id, relationship: getConnectionOptions(catalog, owner.type, payload.type)[0].value } });
        return;
      }
      const parent = dropContainer(cards, { id: "", type: payload.type } as typeof cards[number], position, catalog);
      void createCard(payload.type, position).then((created) => {
        if (created && parent) void updateCard(created.id, { parent_id: parent.id });
      });
      return;
    }
    const legion = legions.find((item) => item.id === payload.id);
    if (!legion || legion.revision !== payload.revision) return;
    void instantiateLegion(payload.id, position);
  }, [cards, catalog, createCard, instantiateLegion, legions, screenToFlowPosition, updateCard, equipmentDropOwner, transformationTarget]);

  return (
    <div
      ref={wrapper}
      className={`world-canvas ${pinToolActive ? "pin-tool-active" : ""} ${cards.some((card) => selectedCardIds.includes(card.id) && isContainer(card, catalog)) ? "has-selected-container" : ""}`}
      data-testid="world-canvas"
      onMouseDownCapture={(event) => {
        if (event.button !== 0 || !(event.target instanceof Element)) return;
        const target = event.target;
        if (!target.closest(".react-flow__handle")
          && !target.classList.contains("react-flow__pane")) return;
        // Keep React Flow's pan/selection/connection events, but do not let
        // Shift-click extend an old browser text selection into the canvas.
        // Controls retain their native focus and activation behavior.
        if (target.closest("button, input, textarea, select, a, [role='button'], [contenteditable]")) return;
        event.preventDefault();
        window.getSelection()?.removeAllRanges();
      }}
      style={{"--shadow-hover-ms":`${SHADOW.hoverMs}ms`,"--shadow-expand-ms":`${SHADOW.expandMs}ms`,"--shadow-collapse-ms":`${SHADOW.collapseMs}ms`,"--shadow-absorb-ms":`${SHADOW.absorbMs}ms`,"--shadow-hover-px":`${SHADOW.hoverPx}px`,"--shadow-hover-deg":`${SHADOW.hoverDegrees}deg`} as CSSProperties}
      onWheelCapture={(event) => {
        const element = wrapper.current;
        if ((event.target as Element).closest('.react-flow')?.id !== 'oaw-world-map') return;
        if (element && isScrollableArea(event.target, element)) event.stopPropagation();
      }}
      onDrop={onDrop}
      onDragOver={(event) => {
        if (!hasPaletteDrag(event.dataTransfer)&&!event.dataTransfer.types.includes("Files")) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = "copy";
        const resource = useEquipmentDrag.getState().resource;
        clearTransformationHints();
        if (resource) {
          const transformation = transformationTarget(event.nativeEvent, { data: { card: resource } } as CanvasNode);
          transformation?.element?.setAttribute("data-transformation-hint", `${transformation.option[1].label}: ${resource.name}`);
        }
        if (resource) useEquipmentDrag.getState().set(resource, equipmentDropOwner(resource, event.clientX, event.clientY)?.id);
      }}
    >
      {importStatus&&<PdfImportIndicator status={importStatus} progress={importProgress} onDismiss={()=>setImportStatus("")} />}
      <ReactFlow<CanvasNode, CanvasEdge>
        id="oaw-world-map"
        nodes={nodes}
        edges={flowEdges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodesChange={onNodesChange}
        onNodeDragStart={onNodeDragStart}
        onNodeDrag={onNodeDrag}
        onNodeDragStop={onNodeDragStop}
        onConnect={onConnect}
        onConnectStart={(_event, params) => {
          if (params.nodeId) beginConnection(params.nodeId);
        }}
        onConnectEnd={endConnection}
        isValidConnection={isValidConnection}
        connectionMode={ConnectionMode.Loose}
        connectionLineComponent={RelationshipConnectionLine}
        onInit={onInit}
        onMoveEnd={onMoveEnd}
        onEdgeClick={(_event, edge) => selectEdge(edge.id)}
        onSelectionChange={onSelectionChange}
        onSelectionStart={(event) => {
          // The previous selection overlay disappears on the first movement.
          // Keep capture on the pane so that removal (or crossing a control)
          // cannot swallow subsequent movement and the final pointerup.
          const pointer = event as React.PointerEvent<HTMLDivElement>;
          pointer.currentTarget.setPointerCapture(pointer.pointerId);
        }}
        onPaneClick={(event) => {
          if (pinToolActive) {
            const point = screenToFlowPosition({ x: event.clientX, y: event.clientY });
            useWorldStore.setState(state => ({ mapPins: [...state.mapPins, { id: crypto.randomUUID(), name: `图钉 ${state.mapPins.length + 1}`, ...point, zoom: getViewport().zoom }] }));
            return;
          }
          selectEdge(undefined);
          selectCards([]);
        }}
        minZoom={0.12}
        maxZoom={2.2}
        defaultViewport={{ x: viewport.x, y: viewport.y, zoom: viewport.zoom }}
        panOnScroll={false}
        selectionOnDrag={false}
        onlyRenderVisibleElements
        deleteKeyCode={null}
        nodesFocusable
        nodeDragThreshold={5}
        nodeClickDistance={5}
        edgesFocusable
        elevateNodesOnSelect={false}
        proOptions={{ hideAttribution: true }}
        aria-label="Open Agent World spatial canvas"
      >
        <ContourLayer />
        <GlueLayer nodes={nodes} preview={gluePreview} />
        <GenerationLayer />
        {nodes.filter((node) => node.data.equipmentDetail && !node.hidden).map((node) =>
          <SurfaceBridge key={node.id} sourceId={equipmentOriginId(node.id)} targetId={node.id} />)}
        <Background
          variant={BackgroundVariant.Dots}
          gap={24}
          size={1.15}
          color="var(--grid-dot)"
        />
        <LocalMiniMap />
        <MapAtlas active={pinToolActive} onActiveChange={active => { setPinToolActive(active); if (active) setGlueActive(false); }} glueActive={glueActive} onGlueChange={active => { setGlueActive(active); if (active) setPinToolActive(false); }} />
        <Controls
          className="world-controls"
          position="bottom-right"
          showInteractive={false}
          aria-label="Canvas zoom controls"
        />
      </ReactFlow>
      <EdgeInspector />
    </div>
  );
}
