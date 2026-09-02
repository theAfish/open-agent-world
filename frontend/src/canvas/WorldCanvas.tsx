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
import { NodeWorkspace } from "../cards/NodeWorkspace";
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
import { displacedPosition, nodePositionFromSurfacePosition, positionSurfaceAtNodeCenter } from "./nodeDisplacement";

const nodeTypes = { worldCard: WorldCardNode };
const edgeTypes = { semantic: SemanticEdge };
const defaultViewport: Viewport = { x: 0, y: 0, zoom: 0.92 };

function nodeFromCard(
  card: ReturnType<typeof useWorldStore.getState>["cards"][number],
  activeNodeId: string | undefined,
  activeLevel: NodeSurfaceLevel,
  inspectorNodeIds: readonly string[],
  inspectors: readonly ReturnType<typeof useWorldStore.getState>["cards"][number][],
): CanvasNode {
  const surfaceLevel = surfaceLevelForNode(card.id, activeNodeId, activeLevel, inspectorNodeIds);
  const visualLevel = surfaceLevel === "workspace" ? "inspector" : surfaceLevel;
  const size = NODE_SURFACE_SIZE[visualLevel];
  const displaced = displacedPosition(card, inspectors);
  return {
    id: card.id,
    type: "worldCard",
    position: positionSurfaceAtNodeCenter(displaced.position, surfaceLevel),
    data: { card, surfaceLevel, displaced: displaced.displaced },
    style: { width: size.width, height: size.height },
    draggable: (visualLevel === "node" || visualLevel === "preview") && !displaced.displaced,
    selectable: true,
    connectable: !card.ephemeral,
    zIndex: visualLevel === "inspector" ? 20 : visualLevel === "preview" ? 12 : 1,
  };
}

export function WorldCanvas() {
  const wrapper = useRef<HTMLDivElement>(null);
  const cards = useWorldStore((state) => state.cards);
  const catalog = useWorldStore((state) => state.catalog);
  const stressCards = useWorldStore((state) => state.stressCards);
  const edges = useWorldStore((state) => state.edges);
  const activeChunkKeys = useWorldStore((state) => state.activeChunkKeys);
  const selectedEdgeId = useWorldStore((state) => state.selectedEdgeId);
  const selectedCardIds = useWorldStore((state) => state.selectedCardIds);
  const activeSurfaceNodeId = useNodeSurfaceStore((state) => state.activeNodeId);
  const activeSurfaceLevel = useNodeSurfaceStore((state) => state.level);
  const inspectorNodeIds = useNodeSurfaceStore((state) => state.inspectorNodeIds);
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
  const inspectorCards = useMemo(
    () => renderCards.filter((card) => inspectorNodeIds.includes(card.id)),
    [inspectorNodeIds, renderCards],
  );
  const mappedNodes = useMemo(
    () => renderCards.map((card) => nodeFromCard(
      card,
      activeSurfaceNodeId,
      activeSurfaceLevel,
      inspectorNodeIds,
      inspectorCards,
    )),
    [activeSurfaceLevel, activeSurfaceNodeId, inspectorCards, inspectorNodeIds, renderCards],
  );
  const [nodes, setNodes, onNodesChange] = useNodesState<CanvasNode>(mappedNodes);
  const nodesRef = useRef(nodes);
  const positionAnimation = useRef<number>();

  useEffect(() => {
    nodesRef.current = nodes;
  }, [nodes]);

  useEffect(() => {
    if (positionAnimation.current) cancelAnimationFrame(positionAnimation.current);
    const currentById = new Map(nodesRef.current.map((node) => [node.id, node]));
    const starts = mappedNodes.map((node) => currentById.get(node.id)?.position ?? node.position);
    const moving = mappedNodes.some((node, index) => (
      Math.abs(node.position.x - starts[index].x) > 0.1
      || Math.abs(node.position.y - starts[index].y) > 0.1
    ));
    if (!moving) {
      setNodes(mappedNodes);
      return;
    }

    const startedAt = performance.now();
    const tick = (now: number) => {
      const progress = Math.min(1, (now - startedAt) / 380);
      const eased = 1 - Math.pow(1 - progress, 3);
      setNodes(mappedNodes.map((node, index) => ({
        ...node,
        position: {
          x: starts[index].x + (node.position.x - starts[index].x) * eased,
          y: starts[index].y + (node.position.y - starts[index].y) * eased,
        },
      })));
      if (progress < 1) positionAnimation.current = requestAnimationFrame(tick);
    };
    positionAnimation.current = requestAnimationFrame(tick);
    return () => {
      if (positionAnimation.current) cancelAnimationFrame(positionAnimation.current);
    };
  }, [mappedNodes, setNodes]);

  useEffect(() => {
    if (activeSurfaceNodeId && !renderCards.some((card) => card.id === activeSurfaceNodeId)) {
      dismissSurface(activeSurfaceNodeId);
    }
  }, [activeSurfaceNodeId, dismissSurface, renderCards]);

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
        if (activeSurfaceLevel === "workspace") closeWorkspace();
        else if (selectedEdgeId) selectEdge(undefined);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [activeSurfaceLevel, activeSurfaceNodeId, closeWorkspace, deleteCards, deleteSelectedEdge, dismissSurface, redo, selectEdge, selectedCardIds, selectedEdgeId, undo]);

  const onNodeDragStop: OnNodeDrag<CanvasNode> = useCallback((_event, node) => {
    void updateCard(node.id, {
      position: nodePositionFromSurfacePosition(node.position, node.data.surfaceLevel),
    });
  }, [updateCard]);

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
        defaultViewport={defaultViewport}
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
      <NodeWorkspace />
    </div>
  );
}
