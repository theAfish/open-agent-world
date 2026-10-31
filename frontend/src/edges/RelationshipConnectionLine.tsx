import type { ConnectionLineComponentProps } from "@xyflow/react";
import type { CanvasNode } from "../cards/types";
import { relationshipPath, relationshipPathToPoint, type NodeRect } from "./geometry";
import { nodeBoundaryShape, nodeCornerRadius } from "./nodeGeometry";
import { useWorldStore } from '../state/worldStore';
import type { PluginCatalog } from '../types/world';

function nodeRect(node: ConnectionLineComponentProps<CanvasNode>["fromNode"], catalog: PluginCatalog): NodeRect {
  return {
    x: node.internals.positionAbsolute.x,
    y: node.internals.positionAbsolute.y,
    width: node.measured.width ?? node.width ?? 1,
    height: node.measured.height ?? node.height ?? 1,
    outline: node.data.shadowOutline as NodeRect["outline"],
    shape: nodeBoundaryShape(node, catalog),
  };
}

export function RelationshipConnectionLine({
  fromNode,
  toNode,
  toX,
  toY,
  connectionStatus,
}: ConnectionLineComponentProps<CanvasNode>) {
  const catalog = useWorldStore(state => state.catalog);
  const path = toNode
    ? relationshipPath(
      nodeRect(fromNode, catalog),
      nodeRect(toNode, catalog),
      nodeCornerRadius(fromNode),
      nodeCornerRadius(toNode),
    ).path
    : relationshipPathToPoint(nodeRect(fromNode, catalog), { x: toX, y: toY }, nodeCornerRadius(fromNode));
  return (
    <g className={`semantic-connection ${connectionStatus ? `is-${connectionStatus}` : ""}`}>
      <path d={path} className="semantic-connection-path" />
      {!toNode ? <circle cx={toX} cy={toY} r={3.5} /> : null}
    </g>
  );
}
