import { BaseEdge, EdgeText, useInternalNode, type EdgeProps } from '@xyflow/react';
import { mapCurve } from './mapGeometry';

export function MapEdge(props: EdgeProps) {
  const source = useInternalNode(props.source), target = useInternalNode(props.target);
  if (!source || !target) return null;
  const circle = (node: typeof source) => {
    const width = node.measured.width ?? 110, height = node.measured.height ?? 110;
    return { x: node.internals.positionAbsolute.x + width / 2, y: node.internals.positionAbsolute.y + height / 2, radius: Math.min(width, height) / 2 };
  };
  const curve = mapCurve(circle(source), circle(target));
  if (!curve) return null;
  return <>
    <BaseEdge id={props.id} path={curve.path} style={props.style} interactionWidth={18} />
    {(props.selected || props.data?.reveal) && <EdgeText x={curve.label.x} y={curve.label.y} label={props.data?.relation as string} labelBgPadding={[6, 4]} labelBgBorderRadius={5} />}
  </>;
}
export const mapEdgeTypes = { knowledge: MapEdge };
