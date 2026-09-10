import {
  Background,
  BackgroundVariant,
  ConnectionMode,
  Controls,
  MarkerType,
  MiniMap,
  ReactFlow,
  useNodesState,
  useReactFlow,
  type Connection,
  type OnNodeDrag,
  type OnInit,
  type OnMove,
  type OnSelectionChangeParams,
  type Viewport,
} from "@xyflow/react";
import { useCallback, useEffect, useMemo, useRef } from "react";
import { worldApi } from "../api/client";
import { transformationOptions } from "./documentTransformations";
import { EquipmentCardNode, EquipmentPanelNode } from "../cards/Equipment";
import { equipmentOriginId, equipmentSurfaceNodes } from "./equipmentLayout";
import { SurfaceBridge } from "../effects/SurfaceBridge";
import { canEquip, equipmentOwner, useEquipmentDrag, useEquipmentPanel } from "../state/equipment";
import { ContainerCardNode } from "../cards/ContainerCard";
import { ancestors, containerDefinition, containerSizes, dropContainer, isContainer, memberSurfacePosition, parentFirst } from "../state/containers";
import { WorldCardNode } from "../cards/CardFrame";
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
import { NODE_SURFACE_SIZE, surfaceLevelForNode, useNodeSurfaceStore, type NodeSurfaceLevel } from "../state/nodeSurfaces";
import { ContourLayer } from "./ContourLayer";
import { GenerationLayer } from "../effects/GenerationLayer";
import {
  displacedPositions,
  nodePositionFromSurfacePosition,
  positionSurfaceAtNodeCenter,
  type SurfaceObstacle,
} from "./nodeDisplacement";

const nodeTypes = { worldCard: WorldCardNode, container: ContainerCardNode, equipment: EquipmentCardNode, equipmentPanel: EquipmentPanelNode };
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
): CanvasNode {
  const size = NODE_SURFACE_SIZE[surfaceLevel];
  return {
    id: card.id,
    type: "worldCard",
    position: positionSurfaceAtNodeCenter(position, surfaceLevel),
    data: { card, surfaceLevel, displaced },
    style: { width: size.width, height: size.height },
    parentId: undefined,
    extent: undefined,
    draggable: true,
    dragHandle: surfaceLevel === "workspace" ? ".node-drag-region" : undefined,
    selectable: true,
    connectable: !card.ephemeral,
    zIndex: surfaceLevel === "workspace" ? 24 : surfaceLevel === "inspector" ? 20 : surfaceLevel === "preview" ? 12 : 1,
  };
}

export function WorldCanvas() {
  const wrapper = useRef<HTMLDivElement>(null);
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
  const connectingNodeId = useNodeSurfaceStore((state) => state.connectingNodeId);
  const dragging = useNodeSurfaceStore((state) => state.dragging);
  const setDragging = useNodeSurfaceStore((state) => state.setDragging);
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

  const renderCards = useMemo(
    () => {
      const visible = filterCardsToChunks([...cards, ...stressCards].filter((c) => !equipmentOwner(c, cards)), activeChunkKeys, catalog);
      const ids = new Set(visible.map((c) => c.id));
      return [...visible, ...cards.filter((c) => { const owner = equipmentOwner(c, cards); return owner && ids.has(owner.id); })];
    },
    [activeChunkKeys, cards, stressCards, catalog],
  );
  const surfaceLevels = useMemo(() => new Map(renderCards.map((card) => [
    card.id,
    surfaceLevelForNode(card.id, surfaceLevelsByNodeId),
  ])), [renderCards, surfaceLevelsByNodeId]);
  const surfaceObstacles = useMemo<SurfaceObstacle[]>(() => renderCards.flatMap<SurfaceObstacle>((card) => {
    if (isContainer(card, catalog) || card.parent_id || card.equipment) return [];
    const level = surfaceLevels.get(card.id);
    return level === "inspector" || level === "workspace" ? [{ card, level }] : [];
  }), [renderCards, surfaceLevels, catalog]);
  const displacedById = useMemo(
    () => displacedPositions(renderCards.filter((c) => !isContainer(c, catalog) && !c.parent_id && !c.equipment), surfaceObstacles, surfaceLevels),
    [renderCards, surfaceLevels, surfaceObstacles, catalog],
  );
  const equipmentPanels = useEquipmentPanel((state) => state.openIds);
  const equipmentPositions = useEquipmentPanel((state) => state.positions);
  const mappedNodes = useMemo(() => {
    const byId = new Map(renderCards.map((c) => [c.id, c]));
    const frameSizes = containerSizes(renderCards, catalog, surfaceLevels);
    return parentFirst(renderCards).flatMap<CanvasNode>((card) => {
      if (ancestors(renderCards, card).some((parent) => surfaceLevels.get(parent.id) === "workspace" && catalog.node_types.find((type) => type.id === parent.type)?.frontend?.workspace)) return [];
      const level = surfaceLevels.get(card.id) ?? "preview";
      const displaced = displacedById.get(card.id);
      let node = nodeFromCard(card, level, displaced?.displaced ?? false, displaced?.position ?? card.position);
      if (isContainer(card, catalog)) {
        const { width, height } = level === "workspace" && catalog.node_types.find((type) => type.id === card.type)?.frontend?.workspace
          ? card.size : frameSizes.get(card.id)!;
        node = { ...node, type: "container", position: card.position, style: { width, height }, zIndex: 0,
          dragHandle: ".container-drag-region", connectable: containerDefinition(card, catalog)!.connectable };
      }
      const equipmentAgent = equipmentOwner(card, cards);
      if (equipmentAgent && byId.has(equipmentAgent.id)) {
        const owner = equipmentAgent;
        const ownerLevel = surfaceLevels.get(owner.id) ?? "preview";
        const index = renderCards.filter((c) => equipmentOwner(c, cards)?.id === owner.id).findIndex((c) => c.id === card.id);
        return equipmentSurfaceNodes(node, owner.id, ownerLevel, index, equipmentPanels.includes(owner.id), equipmentPositions[card.id]);
      }
      if (card.parent_id && byId.has(card.parent_id)) {
        const parent = byId.get(card.parent_id)!;
        const position = isContainer(card, catalog) ? node.position : memberSurfacePosition(card, parent, level, catalog);
        return { ...node, parentId: parent.id, position: { x: position.x - parent.position.x, y: position.y - parent.position.y } };
      }
      return node;
    }).flatMap((node): CanvasNode[] => {
      if (node.type === "equipment" || node.data.equipmentDetail || !catalog.node_types.find((type) => type.id === node.data.card.type)?.traits.includes("core.agent")) return [node];
      const count = renderCards.filter((card) => equipmentOwner(card, cards)?.id === node.id).length;
      return [node, { id: `${node.id}:equipment`, type: "equipmentPanel", data: node.data, parentId: node.id,
        position: { x: 0, y: NODE_SURFACE_SIZE[node.data.surfaceLevel].height + 8 },
        style: { width: 320, height: 46 + Math.max(2, count + 1) * 48 },
        hidden: !equipmentPanels.includes(node.id), draggable: false, selectable: false, connectable: false, zIndex: 24 }];
    });
  }, [displacedById, renderCards, surfaceLevels, catalog, cards, equipmentPanels, equipmentPositions]);
  const [nodes, setNodes, onNodesChange] = useNodesState<CanvasNode>(mappedNodes);
  const nodesRef = useRef(nodes);
  const positionAnimation = useRef<number>();
  const activeDragIds = useRef(new Set<string>());
  const appliedSelectionRevision = useRef(selectionRevision);

  const cancelPositionAnimation = useCallback(() => {
    if (positionAnimation.current === undefined) return;
    cancelAnimationFrame(positionAnimation.current);
    positionAnimation.current = undefined;
  }, []);

  useEffect(() => {
    nodesRef.current = nodes;
  }, [nodes]);

  useEffect(() => {
    cancelPositionAnimation();
    if (connectingNodeId || dragging) return;
    const currentById = new Map(nodesRef.current.map((node) => [node.id, node]));
    const starts = new Map(mappedNodes.map((node) => [
      node.id,
      // A changed parent changes the coordinate space, not the visual location.
      currentById.has(node.id) && currentById.get(node.id)!.parentId === node.parentId ? currentById.get(node.id)!.position : node.position,
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
          const start = starts.get(node.id) ?? node.position;
          return {
            ...live,
            ...node,
            position: {
              x: start.x + (node.position.x - start.x) * eased,
              y: start.y + (node.position.y - start.y) * eased,
            },
          };
        });
      });
    };

    const moving = mappedNodes.some((node) => {
      if (activeDragIds.current.has(node.id)) return false;
      const start = starts.get(node.id) ?? node.position;
      return Math.abs(node.position.x - start.x) > 0.1
        || Math.abs(node.position.y - start.y) > 0.1;
    });
    if (!moving) {
      applyProgress(1);
      return;
    }

    const startedAt = performance.now();
    const tick = (now: number) => {
      const progress = Math.min(1, (now - startedAt) / 380);
      const eased = 1 - Math.pow(1 - progress, 3);
      applyProgress(eased);
      if (progress < 1) positionAnimation.current = requestAnimationFrame(tick);
      else positionAnimation.current = undefined;
    };
    positionAnimation.current = requestAnimationFrame(tick);
    return cancelPositionAnimation;
  }, [cancelPositionAnimation, connectingNodeId, dragging, mappedNodes, setNodes]);

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
    const card = cards.find((item) => item.id === id);
    return card && nodes.find((node) => node.id === id)?.hidden ? equipmentOwner(card, cards)?.id ?? id : id;
  }, [cards, nodes]);
  const flowEdges = useMemo<CanvasEdge[]>(
    () => edges
      .filter((edge) => visibleNodeIds.has(edge.source) && visibleNodeIds.has(edge.target))
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
    [edges, selectedEdgeId, visibleNodeIds, displayEndpoint],
  );

  const dimensions = useCallback(() => ({
    width: wrapper.current?.clientWidth ?? window.innerWidth,
    height: wrapper.current?.clientHeight ?? window.innerHeight,
  }), []);

  const commitViewport = useCallback((next: Viewport) => {
    const size = dimensions();
    setViewportState({ ...next, ...size });
  }, [dimensions, setViewportState]);

  const onMoveEnd: OnMove = useCallback((_event, next) => commitViewport(next), [commitViewport]);
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
      if (target?.matches("input, textarea, select, [contenteditable='true']")) return;
      const modifier = event.ctrlKey || event.metaKey;
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
        });
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
    cancelPositionAnimation();
    useEquipmentDrag.getState().set(!node.data.equipmentDetail && draggedNodes.length <= 1 ? node.data.card : undefined);
    setDragging(true);
    activeDragIds.current.clear();
    activeDragIds.current.add(node.id);
    draggedNodes.forEach((draggedNode) => activeDragIds.current.add(draggedNode.id));
  }, [cancelPositionAnimation, setDragging]);

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
  const onNodeDrag: OnNodeDrag<CanvasNode> = useCallback((event, node) => {
    clearTransformationHints();
    const transformation = transformationTarget(event, node);
    transformation?.element?.setAttribute("data-transformation-hint", `${transformation.option[1].label}: ${node.data.card.name}`);
    const resource = useEquipmentDrag.getState().resource;
    if (resource?.id !== node.id || !("clientX" in event)) return;
    useEquipmentDrag.getState().set(resource, equipmentDropOwner(resource, event.clientX, event.clientY)?.id);
  }, [equipmentDropOwner, transformationTarget]);

  const onNodeDragStop: OnNodeDrag<CanvasNode> = useCallback((_event, node, draggedNodes) => {
    cancelPositionAnimation();
    clearTransformationHints();
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
      const surfacePosition = parent
        ? { x: draggedNode.position.x + parent.position.x, y: draggedNode.position.y + parent.position.y }
        : draggedNode.position;
      return {
        id: draggedNode.id,
        position: isContainer(draggedNode.data.card, catalog) ? surfacePosition
          : nodePositionFromSurfacePosition(surfacePosition, draggedNode.data.surfaceLevel),
      };
    });
    const sizes = new Map(nodesRef.current.map((item) => [item.id, { width: Number(item.style?.width), height: Number(item.style?.height) }]));
    void updateCardPositions(updates.map((update) => {
      const member = cards.find((card) => card.id === update.id)!;
      if (member.ephemeral || containerDefinition(member, catalog)?.parentable === false) return update;
      const destination = dropContainer(cards, member, { x: update.position.x + 48, y: update.position.y + 48 }, catalog, sizes);
      return { ...update, parent_id: destination?.id ?? null };
    })).finally(() => {
      activeDragIds.current.clear();
      setDragging(false);
    });
  }, [cancelPositionAnimation, cards, setDragging, updateCardPositions, updateCard, catalog, transformationTarget]);

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
      className={`world-canvas ${cards.some((card) => selectedCardIds.includes(card.id) && isContainer(card, catalog)) ? "has-selected-container" : ""}`}
      data-testid="world-canvas"
      onMouseDownCapture={(event) => {
        if (event.button !== 0 || !(event.target instanceof Element)
          || !event.target.closest(".react-flow__handle")) return;
        // Keep the mouse event flowing to React Flow, but prevent native text
        // selection/dragging from taking over a boundary connection gesture.
        event.preventDefault();
        window.getSelection()?.removeAllRanges();
      }}
      onWheelCapture={(event) => {
        const element = wrapper.current;
        if (element && isScrollableArea(event.target, element)) event.stopPropagation();
      }}
      onDrop={onDrop}
      onDragOver={(event) => {
        if (!hasPaletteDrag(event.dataTransfer)) return;
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
        onPaneClick={() => {
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
        <GenerationLayer />
        {nodes.filter((node) => node.data.equipmentDetail && !node.hidden).map((node) =>
          <SurfaceBridge key={node.id} sourceId={equipmentOriginId(node.id)} targetId={node.id} />)}
        <Background
          variant={BackgroundVariant.Dots}
          gap={24}
          size={1.15}
          color="var(--grid-dot)"
        />
        <MiniMap
          className="world-minimap"
          nodeColor={(node) => (
            getNodeType(catalog, (node.data as CanvasNodeData).card.type)?.color ?? "#75736c"
          )}
          nodeStrokeWidth={0}
          maskColor="var(--minimap-mask)"
          pannable
          zoomable
          ariaLabel="World overview"
        />
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
