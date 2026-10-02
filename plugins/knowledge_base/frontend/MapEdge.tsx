import { BaseEdge, EdgeText, useInternalNode, type EdgeProps } from "@xyflow/react";
import { mapCurve } from "./mapGeometry";

/** A relation drawn between two entity circles, labelled when it matters. */
export function MapEdge(props: EdgeProps) {
  const source = useInternalNode(props.source), target = useInternalNode(props.target);
  if (!source || !target) return null;
  const circle = (node: typeof source) => {
    const width = node.measured.width ?? 104, height = node.measured.height ?? 104;
    return { x: node.internals.positionAbsolute.x + width / 2,
      y: node.internals.positionAbsolute.y + height / 2, radius: Math.min(width, height) / 2 };
  };
  const curve = mapCurve(circle(source), circle(target));
  if (!curve) return null;
  return <>
    <BaseEdge id={props.id} path={curve.path} style={props.style} interactionWidth={18} />
    {(props.selected || props.data?.reveal) && <EdgeText x={curve.label.x} y={curve.label.y}
      label={props.data?.relation as string} labelBgPadding={[6, 4]} labelBgBorderRadius={5} />}
  </>;
}

export const mapEdgeTypes = { relation: MapEdge };
