import { t, useLocale } from "../i18n";
import { Handle, Position, useInternalNode, useReactFlow, type NodeProps } from "@xyflow/react";
import { Scan, Settings2 } from "lucide-react";
import { memo, useEffect, useRef, useState, type CSSProperties } from "react";
import { useNodeSurfaceStore } from "../state/nodeSurfaces";
import { useWorldStore } from "../state/worldStore";
import { MinisterPanel } from "./MinisterPanel";
export { MinisterReviews, MinisterNearby } from "./MinisterPanel";
export { ministerToolSummary } from "./ministerActivity";
import type { CanvasNode } from "./types";
import { MinisterPresence } from "./MinisterPresence";
import "./minister.css";

export const MINISTER_TYPE = "core.minister";
const clampRadius = (value: number) => Math.max(200, Math.min(3000, Math.round(value / 50) * 50));

export const MinisterNode = memo(function MinisterNode({ data, selected }: NodeProps<CanvasNode>) {
  useLocale();
  const { card } = data;
  const level = useNodeSurfaceStore(s => s.surfaceLevels[card.id]);
  const open = level === "inspector" || level === "workspace";
  const update = useWorldStore(s => s.updateCard);
  const savedRadius = Number(card.config.control_radius ?? 1200);
  const [radius, setRadius] = useState(savedRadius);
  const internal = useInternalNode(card.id);
  const { screenToFlowPosition } = useReactFlow();
  const pointer = useRef<{ x: number; y: number }>();
  const resizing = useRef(false);
  useEffect(() => { if (!resizing.current) setRadius(savedRadius); }, [savedRadius]);
  const saveRadius = () => {
    const value = Number.isFinite(radius) ? clampRadius(radius) : savedRadius;
    setRadius(value);
    if (value !== savedRadius) void update(card.id, { config: { control_radius: value } });
  };
  const [attention, setAttention] = useState(false);
  const toggle = () => open ? useNodeSurfaceStore.getState().dismiss(card.id) : useNodeSurfaceStore.getState().openInspector(card.id);
  return <div className={`minister-node ${selected ? "is-selected" : ""} ${open ? "is-open" : ""} ${card.config.allow_canvas_edits ? "can-edit" : ""}`}
    data-card-id={card.id} data-card-type={card.type} data-control-radius={radius} data-activity={card.status}
    style={{ "--minister-radius": `${radius}px`, "--minister-cx": `${card.size.width / 2}px`, "--minister-cy": `${card.size.height / 2}px` } as CSSProperties}>
    <div className="minister-radius" aria-hidden="true" />
    {[Position.Top, Position.Right, Position.Bottom, Position.Left].map(position => <Handle key={position}
      id={`boundary-${position}`} type="source" position={position} isConnectable={false}
      style={{ visibility: "hidden", pointerEvents: "none" }} />)}
    <div onPointerEnter={() => setAttention(true)} onFocus={() => setAttention(true)} className="minister-avatar-zone">
    <div className="minister-orb minister-drag-region" role="button" tabIndex={0} aria-label={t("Open Minister {v0}", { v0: String(card.name) })} aria-expanded={attention}
      onPointerDown={event => { pointer.current = { x: event.clientX, y: event.clientY }; }}
      onClick={event => {
        if (pointer.current && Math.hypot(event.clientX - pointer.current.x, event.clientY - pointer.current.y) > 5) return;
        if (!event.shiftKey && !event.ctrlKey && !event.metaKey) setAttention(true);
      }} onKeyDown={event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); setAttention(true); } }}>
      <Scan size={30} strokeWidth={1.35} /><span>{t("Minister")}</span><i aria-label={card.status} />
    </div>
    <button type="button" className="minister-manage nodrag nopan" aria-label={t("Settings and history for {v0}", { v0: String(card.name) })} onClick={toggle}><Settings2 size={14} /></button>
    </div>
    <MinisterPresence card={card} active={attention} setActive={setAttention} panelOpen={open} />
    <button type="button" className="minister-radius-handle nodrag nopan" aria-label={t("Resize {v0} control radius", { v0: String(card.name) })} title={t("Drag to change control radius")}
      onPointerDown={event => { event.stopPropagation(); resizing.current = true; event.currentTarget.setPointerCapture(event.pointerId); }}
      onPointerMove={event => {
        if (!resizing.current) return;
        const point = screenToFlowPosition({ x: event.clientX, y: event.clientY });
        const origin = internal?.internals.positionAbsolute ?? card.position;
        setRadius(clampRadius(Math.hypot(point.x - origin.x - card.size.width / 2, point.y - origin.y - card.size.height / 2)));
      }} onPointerUp={event => { if (!resizing.current) return; resizing.current = false; event.currentTarget.releasePointerCapture(event.pointerId); saveRadius(); }}
      onPointerCancel={() => { resizing.current = false; setRadius(savedRadius); }} onKeyDown={event => {
        if (["ArrowLeft", "ArrowRight", "ArrowDown", "ArrowUp"].includes(event.key)) {
          event.preventDefault(); const value = clampRadius(radius + (["ArrowLeft", "ArrowDown"].includes(event.key) ? -50 : 50));
          setRadius(value); void update(card.id, { config: { control_radius: value } });
        }
      }}><span>{radius}</span></button>
    {open && <MinisterPanel card={card} radius={radius} setRadius={setRadius} saveRadius={saveRadius} />}
  </div>;
});
