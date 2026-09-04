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
import { WorldCardNode } from "../cards/CardFrame";
import type { CanvasNode, CanvasNodeData } from "../cards/types";
import { EdgeInspector } from "../edges/EdgeInspector";
import { RelationshipConnectionLine } from "../edges/RelationshipConnectionLine";
import { SemanticEdge, type CanvasEdge } from "../edges/SemanticEdge";
import { filterCardsToChunks } from "../state/chunks";
import { getNodeType } from "../state/catalog";
import { validateConnection } from "../state/relationships";
import { useWorldStore } from "../state/worldStore";
import { NODE_SURFACE_SIZE, surfaceLevelForNode, useNodeSurfaceStore, type NodeSurfaceLevel } from "../state/nodeSurfaces";
import type { CardType } from "../types/world";
import { ContourLayer } from "./ContourLayer";
import {
  displacedPositions,
  nodePositionFromSurfacePosition,
  positionSurfaceAtNodeCenter,
  type SurfaceObstacle,
} from "./nodeDisplacement";

const nodeTypes = { worldCard: WorldCardNode };
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
  const activeChunkKeys = useWorldStore((state) => state.activeChunkKeys);
  const viewport = useWorldStore((state) => state.viewport);
  const selectedEdgeId = useWorldStore((state) => state.selectedEdgeId);
  const selectedCardIds = useWorldStore((state) => state.selectedCardIds);
  const surfaceLevelsByNodeId = useNodeSurfaceStore((state) => state.surfaceLevels);
  const closeWorkspace = useNodeSurfaceStore((state) => state.closeWorkspace);
  const dismissSurface = useNodeSurfaceStore((state) => state.dismiss);
  const beginConnection = useNodeSurfaceStore((state) => state.beginConnection);
  const endConnection = useNodeSurfaceStore((state) => state.endConnection);
  const setViewportState = useWorldStore((state) => state.setViewport);
  const updateCard = useWorldStore((state) => state.updateCard);
  const createCard = useWorldStore((state) => state.createCard);
  const requestConnection = useWorldStore((state) => state.requestConnection);
  const selectEdge = useWorldStore((state) => state.selectEdge);
  const deleteSelectedEdge = useWorldStore((state) => state.deleteSelectedEdge);
  const deleteCards = useWorldStore((state) => state.deleteCards);
  const selectCards = useWorldStore((state) => state.selectCards);
  const undo = useWorldStore((state) => state.undo);
  const redo = useWorldStore((state) => state.redo);
  const { getViewport, screenToFlowPosition } = useReactFlow<CanvasNode, CanvasEdge>();

  const renderCards = useMemo(
    () => filterCardsToChunks([...cards, ...stressCards], activeChunkKeys),
    [activeChunkKeys, cards, stressCards],
  );
  const surfaceLevels = useMemo(() => new Map(renderCards.map((card) => [
    card.id,
    surfaceLevelForNode(card.id, surfaceLevelsByNodeId),
  ])), [renderCards, surfaceLevelsByNodeId]);
  const surfaceObstacles = useMemo<SurfaceObstacle[]>(() => renderCards.flatMap<SurfaceObstacle>((card) => {
    const level = surfaceLevels.get(card.id);
    return level === "preview" || level === "inspector" || level === "workspace" ? [{ card, level }] : [];
  }), [renderCards, surfaceLevels]);
  const displacedById = useMemo(
    () => displacedPositions(renderCards, surfaceObstacles, surfaceLevels),
    [renderCards, surfaceLevels, surfaceObstacles],
  );
  const mappedNodes = useMemo(
    () => renderCards.map((card) => {
      const displaced = displacedById.get(card.id);
      return nodeFromCard(
        card,
        surfaceLevels.get(card.id) ?? "node",
        displaced?.displaced ?? false,
        displaced?.position ?? card.position,
      );
    }),
    [displacedById, renderCards, surfaceLevels],
  );
  const [nodes, setNodes, onNodesChange] = useNodesState<CanvasNode>(mappedNodes);
  const nodesRef = useRef(nodes);
  const positionAnimation = useRef<number>();
  const activeDragIds = useRef(new Set<string>());

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
    const currentById = new Map(nodesRef.current.map((node) => [node.id, node]));
    const starts = new Map(mappedNodes.map((node) => [
      node.id,
      currentById.get(node.id)?.position ?? node.position,
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
  }, [cancelPositionAnimation, mappedNodes, setNodes]);

  const visibleNodeIds = useMemo(() => new Set(renderCards.map((card) => card.id)), [renderCards]);
  const flowEdges = useMemo<CanvasEdge[]>(
    () => edges
      .filter((edge) => visibleNodeIds.has(edge.source) && visibleNodeIds.has(edge.target))
      .map((edge) => ({
        id: edge.id,
        source: edge.source,
        target: edge.target,
        type: "semantic",
        data: { relationship: edge.relationship, direction: edge.direction },
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
      })),
    [edges, selectedEdgeId, visibleNodeIds],
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
        else if (selectedEdgeId) selectEdge(undefined);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [closeWorkspace, deleteCards, deleteSelectedEdge, dismissSurface, redo, selectEdge, selectedCardIds, selectedEdgeId, surfaceLevelsByNodeId, undo]);

  const onNodeDragStart: OnNodeDrag<CanvasNode> = useCallback((_event, node, draggedNodes) => {
    cancelPositionAnimation();
    activeDragIds.current.clear();
    activeDragIds.current.add(node.id);
    draggedNodes.forEach((draggedNode) => activeDragIds.current.add(draggedNode.id));
  }, [cancelPositionAnimation]);

  const onNodeDragStop: OnNodeDrag<CanvasNode> = useCallback((_event, node) => {
    cancelPositionAnimation();
    activeDragIds.current.clear();
    void updateCard(node.id, {
      position: nodePositionFromSurfacePosition(node.position, node.data.surfaceLevel),
    });
  }, [cancelPositionAnimation, updateCard]);

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
    ).valid;
  }, [catalog, edges, renderCards]);

  const onSelectionChange = useCallback(({ nodes: selectedNodes }: OnSelectionChangeParams<CanvasNode, CanvasEdge>) => {
    selectCards(selectedNodes.map((node) => node.id));
  }, [selectCards]);

  const onDrop = useCallback((event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    const type = event.dataTransfer.getData("application/open-agent-card") as CardType;
    if (!getNodeType(catalog, type)) return;
    const position = screenToFlowPosition({ x: event.clientX, y: event.clientY });
    void createCard(type, position);
  }, [catalog, createCard, screenToFlowPosition]);

  return (
    <div
      ref={wrapper}
      className="world-canvas"
      data-testid="world-canvas"
      onWheelCapture={(event) => {
        const element = wrapper.current;
        if (element && isScrollableArea(event.target, element)) event.stopPropagation();
      }}
      onDrop={onDrop}
      onDragOver={(event) => {
        event.preventDefault();
        event.dataTransfer.dropEffect = "copy";
      }}
    >
      <ReactFlow<CanvasNode, CanvasEdge>
        nodes={nodes}
        edges={flowEdges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodesChange={onNodesChange}
        onNodeDragStart={onNodeDragStart}
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
        edgesFocusable
        elevateNodesOnSelect
        proOptions={{ hideAttribution: true }}
        aria-label="Open Agent World spatial canvas"
      >
        <ContourLayer />
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
