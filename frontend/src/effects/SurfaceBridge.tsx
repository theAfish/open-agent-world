import { useId } from "react";
import { useStore, ViewportPortal } from "@xyflow/react";
import "./surfaceBridge.css";

/** A pointer-inert area between two canvas surfaces, independent of graph relationships. */
export function SurfaceBridge({ sourceId, targetId }: { sourceId: string; targetId: string }) {
  const gradientId = `surface-bridge-${useId().replaceAll(":", "")}`;
  const source = useStore((state) => state.nodeLookup.get(sourceId));
  const target = useStore((state) => state.nodeLookup.get(targetId));
  if (!source || !target || source.hidden || target.hidden) return null;
  const a = source.internals.positionAbsolute, b = target.internals.positionAbsolute;
  const sw = source.measured.width ?? 294, sh = source.measured.height ?? 40;
  const tw = target.measured.width ?? 438, th = target.measured.height ?? 570;
  const right = b.x + tw / 2 >= a.x + sw / 2;
  const sx = a.x + (right ? sw : 0), tx = b.x + (right ? 0 : tw);
  const sy = a.y + sh / 2, ty = b.y + Math.min(th / 2, 150);
  const middle = (sx + tx) / 2;
  const half = Math.min(th / 2 - 24, 100);
  const d = `M ${sx} ${sy - 17} C ${middle} ${sy - 17}, ${middle} ${ty - half}, ${tx} ${ty - half}
    L ${tx} ${ty + half} C ${middle} ${ty + half}, ${middle} ${sy + 17}, ${sx} ${sy + 17} Z`;
  return <ViewportPortal><svg className="surface-bridge" data-surface-bridge={targetId} aria-hidden="true">
    <defs><linearGradient id={gradientId} gradientUnits="userSpaceOnUse" x1={sx} y1={sy} x2={tx} y2={ty}>
      <stop offset="0" stopColor="var(--accent)" stopOpacity="0.24" />
      <stop offset="0.48" stopColor="var(--accent)" stopOpacity="0.07" />
      <stop offset="1" stopColor="var(--accent)" stopOpacity="0.15" />
    </linearGradient></defs>
    <path d={d} fill={`url(#${gradientId})`} />
  </svg></ViewportPortal>;
}
