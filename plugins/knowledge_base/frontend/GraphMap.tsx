import { t, useNestedFlowGestures } from "@oaw/plugin-api";
import {
  Background, Controls, ReactFlow, ReactFlowProvider,
  useEdgesState, useNodesState, useReactFlow, type Edge, type Node,
} from "@xyflow/react";
import { useEffect, useRef, useState } from "react";
import { graphLayout, type Point } from "./graphLayout";
import { mapEdgeTypes } from "./MapEdge";

export type Entity = { id: string; type: string; name: string; properties: Record<string, unknown> };
export type Relation = { id: string; type: string; source_id: string; target_id: string };

type Props = {
  entities: Entity[];
  relations: Relation[];
  selected: string;
  onSelect: (id: string) => void;
  /** Traverse outward from one entity, adding its neighbours to the map. */
  onExpand: (id: string) => void;
};

/** The published graph, drawn. Click an entity to inspect it, double-click to expand. */
export function GraphMap(props: Props) {
  return <ReactFlowProvider><GraphCanvas {...props} /></ReactFlowProvider>;
}

function GraphCanvas({ entities, relations, selected, onSelect, onExpand }: Props) {
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const positions = useRef(new Map<string, Point>());
  const canvas = useRef<HTMLElement>(null);
  const [hovered, setHovered] = useState("");
  const flow = useReactFlow();
  const selectionBox = useNestedFlowGestures(canvas, dragged =>
    dragged.forEach(node => positions.current.set(node.id, node.position)));

  useEffect(() => {
    const live = new Set(entities.map(entity => entity.id));
    for (const id of [...positions.current.keys()]) if (!live.has(id)) positions.current.delete(id);
    // Entities already placed keep their spot; expanding only settles the newcomers.
    graphLayout([...live], relations.map(relation =>
      ({ source: relation.source_id, target: relation.target_id })), positions.current)
      .forEach((point, id) => positions.current.set(id, point));
    setNodes(entities.map(entity => ({
      id: entity.id, position: positions.current.get(entity.id)!, connectable: false,
      style: { width: 104, height: 104 }, ariaLabel: `${entity.type}: ${entity.name}`,
      data: { label: <><span className="knowledge-node-type">{entity.type}</span>
        <span className="knowledge-node-name">{entity.name}</span></> },
    })));
    setEdges(relations
      .filter(relation => live.has(relation.source_id) && live.has(relation.target_id))
      .map(relation => ({ id: relation.id, source: relation.source_id, target: relation.target_id,
        type: "relation", data: { relation: relation.type.replaceAll("_", " ") } })));
    const settle = requestAnimationFrame(() => requestAnimationFrame(() =>
      void flow.fitView({ padding: 0.25, maxZoom: 1, duration: 150 })));
    return () => cancelAnimationFrame(settle);
  }, [entities, relations, setNodes, setEdges, flow]);

  // Hover or selection dims everything the entity is not connected to.
  const active = hovered || selected;
  const related = new Set(active ? [active] : []);
  if (active) for (const edge of edges) {
    if (edge.source === active || edge.target === active) { related.add(edge.source); related.add(edge.target); }
  }
  const visibleNodes = nodes.map(node => ({ ...node, selected: node.id === selected,
    className: `knowledge-node${active ? related.has(node.id) ? " is-related" : " is-muted" : ""}` }));
  const visibleEdges = edges.map(edge => {
    const touches = edge.source === active || edge.target === active;
    return { ...edge, data: { ...edge.data, reveal: touches },
      className: active && !touches ? "is-muted" : "" };
  });

  return <div className="knowledge-map">
    <main ref={canvas} className="knowledge-canvas">
      <ReactFlow nodes={visibleNodes} edges={visibleEdges} edgeTypes={mapEdgeTypes}
        onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
        onNodeMouseEnter={(_, node) => setHovered(node.id)} onNodeMouseLeave={() => setHovered("")}
        onNodeClick={(_, node) => onSelect(node.id)}
        onNodeDoubleClick={(_, node) => onExpand(node.id)}
        onNodeDragStop={(_, node) => positions.current.set(node.id, node.position)}
        onPaneClick={() => onSelect("")}
        noPanClassName="knowledge-nopan" noDragClassName="knowledge-nodrag"
        noWheelClassName="knowledge-nowheel"
        nodesDraggable={false} panOnDrag={false} zoomOnScroll={false} zoomOnPinch={false}
        zoomOnDoubleClick={false} selectionKeyCode={null} selectionOnDrag={false}
        autoPanOnNodeDrag={false} autoPanOnSelection={false} nodesConnectable={false}
        deleteKeyCode={null} onlyRenderVisibleElements minZoom={0.1} maxZoom={2} fitView
        aria-label={t("Knowledge graph map")}>
        <Background /><Controls showInteractive={false} />
      </ReactFlow>
      {selectionBox && <div className="knowledge-selection-box" style={{ left: selectionBox.x,
        top: selectionBox.y, width: selectionBox.width, height: selectionBox.height }} />}
    </main>
  </div>;
}
