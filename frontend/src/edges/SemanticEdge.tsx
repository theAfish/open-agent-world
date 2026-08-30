import {
  BaseEdge,
  EdgeLabelRenderer,
  getBezierPath,
  type Edge,
  type EdgeProps,
} from "@xyflow/react";
import { getRelationshipOption } from "../state/relationships";
import type { Relationship } from "../types/world";

export interface SemanticEdgeData extends Record<string, unknown> {
  relationship: Relationship;
}

export type CanvasEdge = Edge<SemanticEdgeData, "semantic">;

export function SemanticEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  markerEnd,
  selected,
  data,
}: EdgeProps<CanvasEdge>) {
  const [path, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    curvature: 0.28,
  });
  const relationship = data?.relationship ?? "read";
  const option = getRelationshipOption(relationship);

  return (
    <>
      <BaseEdge
        id={id}
        path={path}
        markerEnd={markerEnd}
        className={`semantic-edge-path ${selected ? "is-selected" : ""}`}
      />
      <EdgeLabelRenderer>
        <div
          className={`semantic-edge-label ${selected ? "is-selected" : ""}`}
          style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
          title={option.description}
        >
          <span aria-hidden="true" />
          {option.shortLabel}
        </div>
      </EdgeLabelRenderer>
    </>
  );
}
