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
import {isShadow,shadowPresentation,shadowPoints,useCollectionDrag} from "../state/shadowCollection";
import {useNodeSurfaceStore,surfaceLevelForNode} from "../state/nodeSurfaces";

export interface SemanticEdgeData extends Record<string, unknown> {
  relationship: Relationship;
  direction: EdgeDirection;
  sourceCardId?: string;
  targetCardId?: string;
  fading?: boolean;
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
  const cards=useWorldStore(s=>s.cards),surfaces=useNodeSurfaceStore(s=>s.surfaceLevels),positions=useCollectionDrag(s=>s.positions);
  const outline=(node:typeof sourceNode)=>{
    if(!node||!isShadow(node.data.card)||!Object.keys(positions).length)return node?.data.shadowOutline as NodeRect["outline"];
    const rect=shadowPresentation(node.data.card,cards,new Map(cards.map(c=>[c.id,surfaceLevelForNode(c.id,surfaces)])),positions,catalog);
    return shadowPoints(rect.width,rect.height,rect.rects);
  };
  const nodeRect = (node: typeof sourceNode, fallbackX: number, fallbackY: number): NodeRect => {
    const origin=node?.data.shadowOrigin as {x:number;y:number}|undefined;
    if(node&&origin&&isShadow(node.data.card)&&Object.keys(positions).length){
      const rect=shadowPresentation(node.data.card,cards,new Map(cards.map(c=>[c.id,surfaceLevelForNode(c.id,surfaces)])),positions,catalog);
      return {...rect,x:node.internals.positionAbsolute.x+rect.x-origin.x,y:node.internals.positionAbsolute.y+rect.y-origin.y,outline:outline(node)};
    }
    return ({
    x: node?.internals.positionAbsolute.x ?? fallbackX,
    y: node?.internals.positionAbsolute.y ?? fallbackY,
    width: node?.measured.width ?? node?.width ?? 1,
    height: node?.measured.height ?? node?.height ?? 1,
    outline: outline(node),
  });};
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
          style={{ transform: `translate(-50%, -50%) translate(${geometry.labelX}px, ${geometry.labelY}px)`,opacity:data?.fading?0:1,transition:"opacity 400ms ease",pointerEvents:data?.fading?"none":undefined }}
          title={option.description}
        >
          <span aria-hidden="true" />
          {option.shortLabel}
        </div>
      </EdgeLabelRenderer>
    </>
  );
}
