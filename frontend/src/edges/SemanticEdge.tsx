import {
  BaseEdge,
  EdgeLabelRenderer,
  useInternalNode,
  type Edge,
  type EdgeProps,
} from "@xyflow/react";
import { getRelationshipOption } from "../state/relationships";
import { useWorldStore } from "../state/worldStore";
import type { EdgeDirection, Relationship } from "../types/world";
import { relationshipPath, type NodeRect } from "./geometry";
import { nodeCornerRadius } from "./nodeGeometry";
import type { CanvasNode } from "../cards/types";

export interface SemanticEdgeData extends Record<string, unknown> {
  relationship: Relationship;
  direction: EdgeDirection;
  sourceCardId?: string;
  targetCardId?: string;
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
  markerStart,
  selected,
  data,
}: EdgeProps<CanvasEdge>) {
  const catalog = useWorldStore((state) => state.catalog);
  const sourceNode = useInternalNode<CanvasNode>(source);
  const targetNode = useInternalNode<CanvasNode>(target);
  const nodeRect = (node: typeof sourceNode, fallbackX: number, fallbackY: number): NodeRect => ({
    x: node?.internals.positionAbsolute.x ?? fallbackX,
    y: node?.internals.positionAbsolute.y ?? fallbackY,
    width: node?.measured.width ?? node?.width ?? 1,
    height: node?.measured.height ?? node?.height ?? 1,
  });
  const geometry = relationshipPath(
    nodeRect(sourceNode, sourceX, sourceY),
    nodeRect(targetNode, targetX, targetY),
    nodeCornerRadius(sourceNode),
    nodeCornerRadius(targetNode),
  );
  const relationship = data?.relationship ?? "read";
  const bidirectional = data?.direction === "bidirectional";
  const option = getRelationshipOption(catalog, relationship);
  const generated = catalog.relationships.find((item) => item.id === relationship)?.generated;

  return (
    <>
      <BaseEdge
        id={id}
        path={bidirectional ? geometry.bidirectionalMarkerPath : geometry.markerPath}
        markerEnd={markerEnd}
        markerStart={markerStart}
        className={`semantic-edge-path ${generated ? "is-generated" : ""} ${selected ? "is-selected" : ""}`}
        data-edge-id={id}
        data-source-id={data?.sourceCardId ?? source}
        data-target-id={data?.targetCardId ?? target}
      />
      <circle
        cx={geometry.source.x}
        cy={geometry.source.y}
        r={4.5}
        className="semantic-edge-endpoint semantic-edge-endpoint--source"
        data-edge-id={id}
        data-source-id={data?.sourceCardId ?? source}
        data-target-id={data?.targetCardId ?? target}
        data-edge-endpoint="source"
        aria-hidden="true"
      />
      <circle
        cx={geometry.target.x}
        cy={geometry.target.y}
        r={4.5}
        className="semantic-edge-endpoint semantic-edge-endpoint--target"
        data-edge-id={id}
        data-source-id={data?.sourceCardId ?? source}
        data-target-id={data?.targetCardId ?? target}
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
