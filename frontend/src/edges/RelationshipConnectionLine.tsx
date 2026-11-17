import type { ConnectionLineComponentProps } from "@xyflow/react";
import type { CanvasNode } from "../cards/types";
import { relationshipPath, relationshipPathToPoint, type NodeRect } from "./geometry";
import { nodeBoundaryShape, nodeCornerRadius } from "./nodeGeometry";
import { useWorldStore } from '../state/worldStore';
import type { PluginCatalog } from '../types/world';
import './connectionMotion.css';

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
  const geometry = toNode
    ? relationshipPath(
      nodeRect(fromNode, catalog),
      nodeRect(toNode, catalog),
      nodeCornerRadius(fromNode),
      nodeCornerRadius(toNode),
    ) : undefined;
  const path = geometry?.path
    ?? relationshipPathToPoint(nodeRect(fromNode, catalog), { x: toX, y: toY }, nodeCornerRadius(fromNode));
  const endpoint = geometry?.target ?? { x: toX, y: toY };
  return (
    <g className={`semantic-connection ${connectionStatus ? `is-${connectionStatus}` : ""}`}>
      <path d={path} className="semantic-connection-path" />
      <g key={`${toNode?.id ?? 'pointer'}-${connectionStatus}`} className="semantic-connection-tip">
        {connectionStatus === 'valid' && <circle cx={endpoint.x} cy={endpoint.y} r={8}
          className="semantic-connection-lock" />}
        <circle cx={endpoint.x} cy={endpoint.y} r={3.5} className="semantic-connection-dot" />
      </g>
    </g>
  );
}
