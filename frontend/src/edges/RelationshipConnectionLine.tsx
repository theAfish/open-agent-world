import type { ConnectionLineComponentProps } from "@xyflow/react";
import type { CanvasNode } from "../cards/types";
import { relationshipPath, relationshipPathToPoint, type NodeRect } from "./geometry";

function nodeRect(node: ConnectionLineComponentProps<CanvasNode>["fromNode"]): NodeRect {
  return {
    x: node.internals.positionAbsolute.x,
    y: node.internals.positionAbsolute.y,
    width: node.measured.width ?? node.width ?? 1,
    height: node.measured.height ?? node.height ?? 1,
  };
}

export function RelationshipConnectionLine({
  fromNode,
  toNode,
  toX,
  toY,
  connectionStatus,
}: ConnectionLineComponentProps<CanvasNode>) {
  const path = toNode
    ? relationshipPath(nodeRect(fromNode), nodeRect(toNode)).path
    : relationshipPathToPoint(nodeRect(fromNode), { x: toX, y: toY });
  return (
    <g className={`semantic-connection ${connectionStatus ? `is-${connectionStatus}` : ""}`}>
      <path d={path} className="semantic-connection-path" />
      {!toNode ? <circle cx={toX} cy={toY} r={3.5} /> : null}
    </g>
  );
}
