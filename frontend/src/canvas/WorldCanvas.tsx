import {
  Background,
  BackgroundVariant,
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
  type Viewport,
} from "@xyflow/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { WorldCardNode } from "../cards/CardFrame";
import type { CanvasNode, CanvasNodeData } from "../cards/types";
import { EdgeInspector } from "../edges/EdgeInspector";
import { SemanticEdge, type CanvasEdge } from "../edges/SemanticEdge";
import { filterCardsToChunks } from "../state/chunks";
import { useWorldStore } from "../state/worldStore";
import type { CardType } from "../types/world";
import { ContourLayer } from "./ContourLayer";

const nodeTypes = { worldCard: WorldCardNode };
const edgeTypes = { semantic: SemanticEdge };
const defaultViewport: Viewport = { x: 0, y: 0, zoom: 0.92 };

const minimapColors: Record<CardType, string> = {
  agent: "#75736c",
  text: "#7c7267",
  image: "#8a7560",
  sandbox: "#696c66",
};

function nodeFromCard(card: ReturnType<typeof useWorldStore.getState>["cards"][number]): CanvasNode {
  return {
    id: card.id,
    type: "worldCard",
    position: card.position,
    data: { card },
    style: { width: card.size.width },
    draggable: true,
    selectable: true,
    connectable: !card.ephemeral,
    zIndex: card.expanded ? 10 : 1,
  };
}

export function WorldCanvas() {
  const wrapper = useRef<HTMLDivElement>(null);
  const cards = useWorldStore((state) => state.cards);
  const stressCards = useWorldStore((state) => state.stressCards);
  const edges = useWorldStore((state) => state.edges);
  const activeChunkKeys = useWorldStore((state) => state.activeChunkKeys);
  const selectedEdgeId = useWorldStore((state) => state.selectedEdgeId);
  const setViewportState = useWorldStore((state) => state.setViewport);
  const updateCard = useWorldStore((state) => state.updateCard);
  const createCard = useWorldStore((state) => state.createCard);
  const requestConnection = useWorldStore((state) => state.requestConnection);
  const selectEdge = useWorldStore((state) => state.selectEdge);
  const deleteSelectedEdge = useWorldStore((state) => state.deleteSelectedEdge);
  const { getViewport, screenToFlowPosition } = useReactFlow<CanvasNode, CanvasEdge>();

  const renderCards = useMemo(
    () => filterCardsToChunks([...cards, ...stressCards], activeChunkKeys),
    [activeChunkKeys, cards, stressCards],
  );
  const mappedNodes = useMemo(() => renderCards.map(nodeFromCard), [renderCards]);
  const [nodes, setNodes, onNodesChange] = useNodesState<CanvasNode>(mappedNodes);

  useEffect(() => {
    setNodes(mappedNodes);
  }, [mappedNodes, setNodes]);

  const visibleNodeIds = useMemo(() => new Set(renderCards.map((card) => card.id)), [renderCards]);
  const flowEdges = useMemo<CanvasEdge[]>(
    () => edges
      .filter((edge) => visibleNodeIds.has(edge.source) && visibleNodeIds.has(edge.target))
      .map((edge) => ({
        id: edge.id,
        source: edge.source,
        target: edge.target,
        type: "semantic",
        data: { relationship: edge.relationship },
        selected: edge.id === selectedEdgeId,
        markerEnd: {
          type: MarkerType.ArrowClosed,
          width: 14,
          height: 14,
          color: "var(--edge-arrow)",
        },
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
      if ((event.key === "Delete" || event.key === "Backspace") && selectedEdgeId) {
        const target = event.target as HTMLElement | null;
        if (target?.matches("input, textarea, select, [contenteditable='true']")) return;
        event.preventDefault();
        void deleteSelectedEdge();
      }
      if (event.key === "Escape" && selectedEdgeId) selectEdge(undefined);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [deleteSelectedEdge, selectEdge, selectedEdgeId]);

  const onNodeDragStop: OnNodeDrag<CanvasNode> = useCallback((_event, node) => {
    void updateCard(node.id, { position: node.position });
  }, [updateCard]);

  const onConnect = useCallback((connection: Connection) => {
    requestConnection(connection.source, connection.target);
  }, [requestConnection]);

  const onDrop = useCallback((event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    const type = event.dataTransfer.getData("application/open-agent-card") as CardType;
    if (!(type === "agent" || type === "text" || type === "image" || type === "sandbox")) return;
    const position = screenToFlowPosition({ x: event.clientX, y: event.clientY });
    void createCard(type, position);
  }, [createCard, screenToFlowPosition]);

  return (
    <div
      ref={wrapper}
      className="world-canvas"
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
        onInit={onInit}
        onMoveEnd={onMoveEnd}
        onEdgeClick={(_event, edge) => selectEdge(edge.id)}
        onPaneClick={() => selectEdge(undefined)}
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
          nodeColor={(node) => minimapColors[(node.data as CanvasNodeData).card.type]}
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
      <div className="chunk-readout" aria-label={`${activeChunkKeys.length} nearby chunks active`}>
        <span aria-hidden="true" /> {activeChunkKeys.length} chunks live · {renderCards.length} cards mounted
      </div>
    </div>
  );
}
