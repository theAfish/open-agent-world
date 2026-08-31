import {
  BaseEdge,
  EdgeLabelRenderer,
  useInternalNode,
  type Edge,
  type EdgeProps,
} from "@xyflow/react";
import { getRelationshipOption } from "../state/relationships";
import type { Relationship } from "../types/world";
import { relationshipPath, type NodeRect } from "./geometry";

export interface SemanticEdgeData extends Record<string, unknown> {
  relationship: Relationship;
}

export type CanvasEdge = Edge<SemanticEdgeData, "semantic">;

export function SemanticEdge({
  id,
  source,
  target,
  sourceX,
  sourceY,
  targetX,
  targetY,
  markerEnd,
  selected,
  data,
}: EdgeProps<CanvasEdge>) {
  const sourceNode = useInternalNode(source);
  const targetNode = useInternalNode(target);
  const nodeRect = (node: typeof sourceNode, fallbackX: number, fallbackY: number): NodeRect => ({
    x: node?.internals.positionAbsolute.x ?? fallbackX,
    y: node?.internals.positionAbsolute.y ?? fallbackY,
    width: node?.measured.width ?? node?.width ?? 1,
    height: node?.measured.height ?? node?.height ?? 1,
  });
  const geometry = relationshipPath(
    nodeRect(sourceNode, sourceX, sourceY),
    nodeRect(targetNode, targetX, targetY),
  );
  const relationship = data?.relationship ?? "read";
  const option = getRelationshipOption(relationship);

  return (
    <>
      <BaseEdge
        id={id}
        path={geometry.path}
        markerEnd={markerEnd}
        className={`semantic-edge-path ${selected ? "is-selected" : ""}`}
        data-edge-id={id}
        data-source-id={source}
        data-target-id={target}
      />
      <circle
        cx={geometry.source.x}
        cy={geometry.source.y}
        r={4.5}
        className="semantic-edge-endpoint semantic-edge-endpoint--source"
        data-edge-id={id}
        data-source-id={source}
        data-target-id={target}
        data-edge-endpoint="source"
        aria-hidden="true"
      />
      <circle
        cx={geometry.target.x}
        cy={geometry.target.y}
        r={4.5}
        className="semantic-edge-endpoint semantic-edge-endpoint--target"
        data-edge-id={id}
        data-source-id={source}
        data-target-id={target}
        data-edge-endpoint="target"
        aria-hidden="true"
      />
      <EdgeLabelRenderer>
        <div
          className={`semantic-edge-label ${selected ? "is-selected" : ""}`}
          style={{ transform: `translate(-50%, -50%) translate(${geometry.labelX}px, ${geometry.labelY}px)` }}
          title={option.description}
        >
          <span aria-hidden="true" />
          {option.shortLabel}
        </div>
      </EdgeLabelRenderer>
    </>
  );
}
