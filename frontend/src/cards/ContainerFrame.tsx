import { t, useLocale } from "../i18n";
import { Handle, NodeResizeControl, Position } from "@xyflow/react";
import { Plus, Trash2, Ungroup } from "lucide-react";
import { type ReactNode, useEffect, useRef } from "react";
import { ConnectionHoverHint, clearConnectionHoverHint, updateConnectionHoverHint } from "./ConnectionHoverHint";
import { acceptsMember, containerDefinition } from "../state/containers";
import { useNodeSurfaceStore } from "../state/nodeSurfaces";
import { useWorldStore } from "../state/worldStore";
import type { WorldCard } from "../types/world";
import { ActivityGlow } from "../effects/ActivityGlow";
import { useNodeActivity } from "../effects/useNodeActivity";
import { useNodeGeneration } from "../effects/generation";

/** Shared spatial shell. Plugin presentations provide their header and controls. */
export function ContainerFrame({ card, selected, className, label, header, children }: {
  card: WorldCard; selected?: boolean; className: string; label: string; header: ReactNode; children?: ReactNode;
}) {
  useLocale();
  const catalog = useWorldStore((state) => state.catalog);
  const resize = useWorldStore((state) => state.resizeContainer);
  const spec = containerDefinition(card, catalog)!;
  const activity = useNodeActivity(card, true);
  const generation = useNodeGeneration(card.id);
  const materializing = generation?.containerId === card.id;
  const connectingNodeId = useNodeSurfaceStore((state) => state.connectingNodeId);
  const frameRef = useRef<HTMLElement>(null);
  const activityLabel = activity.phase === "running" ? t("Working · {v0} active", { v0: String(activity.running) })
    : activity.phase === "waiting" ? t("Waiting · {v0}", { v0: String(activity.waiting) })
    : ({ completed: t("Completed"), stopped: t("Stopped"), failed: t("Failed"), idle: t("Idle") })[activity.phase];
  useEffect(() => {
    if (connectingNodeId === card.id) clearConnectionHoverHint(frameRef.current);
  }, [card.id, connectingNodeId]);
  return <section ref={frameRef} className={`container-frame ${className} ${spec.virtual ? "virtual-workspace" : ""} ${materializing ? "is-materializing" : ""} ${selected ? "is-selected" : ""}`}
    data-card-id={card.id} data-card-type={card.type} data-activity={activity.phase} aria-label={label}
    onPointerMoveCapture={(event) => { if (spec.connectable && connectingNodeId !== card.id) updateConnectionHoverHint(event, frameRef.current); }}
    onPointerLeave={() => clearConnectionHoverHint(frameRef.current)}>
    <ActivityGlow phase={activity.phase} />
    {selected && <NodeResizeControl className="container-resize-arc" position="bottom-right" minWidth={spec.min_size[0]} minHeight={spec.min_size[1]} maxWidth={4096} maxHeight={4096}
      onResizeEnd={(_event, size) => void resize(card.id, { width: size.width, height: size.height })} />}
    {spec.connectable && ([[Position.Top, "top"], [Position.Right, "right"], [Position.Bottom, "bottom"], [Position.Left, "left"]] as const).map(([position, side]) =>
      <Handle key={side} type="source" id={`boundary-${side}`} position={position} className={`semantic-handle semantic-handle--${side}`} data-connection-side={side} aria-label={t("Connect {v0} {v1}", { v0: String(card.name), v1: String(side) })} />)}
    {spec.connectable && <ConnectionHoverHint />}
    <header className="container-drag-region container-header">{header}</header>
    {(spec.virtual || activity.phase !== "idle") && <span className="container-activity" role="status" data-phase={activity.phase}><i />{activityLabel}</span>}
    {children}
  </section>;
}

export function ContainerActions({ card, busy = false, deleteLabel = t("Delete {v0} and members", { v0: String(card.name) }), releaseMode }: { card: WorldCard; busy?: boolean; deleteLabel?: string; releaseMode?:{active:boolean;toggle:()=>void} }) {
  useLocale();
  const remove = useWorldStore((state) => state.deleteCards);
  const dissolve = useWorldStore((state) => state.dissolveContainer);
  return <>
    <button className="secondary-button nodrag nopan" disabled={busy} aria-pressed={releaseMode?.active} title={releaseMode?t("切换移出模式：开启后拖到阴影边缘移出成员"):t("Remove the container; keep members and their connections")} onClick={e=>{e.stopPropagation();if(releaseMode)releaseMode.toggle();else void dissolve(card.id);}}><Ungroup size={14} /> {releaseMode?(releaseMode.active?t("完成移出"):t("移出成员")):t("Dissolve")}</button>
    <button className="secondary-button nodrag nopan" disabled={busy} aria-label={deleteLabel} title={t("Delete the container and its members. Ctrl+Z to undo.")} onClick={() => void remove([card.id])}><Trash2 size={14} /></button>
  </>;
}

export function AddSelectedMembers({ card }: { card: WorldCard }) {
  useLocale();
  const cards = useWorldStore((state) => state.cards);
  const selected = useWorldStore((state) => state.selectedCardIds);
  const catalog = useWorldStore((state) => state.catalog);
  const setMembership = useWorldStore((state) => state.setContainerMembership);
  const candidates = cards.filter((member) => selected.includes(member.id) && member.parent_id !== card.id && acceptsMember(card, member, catalog, cards));
  return candidates.length > 0 && <button className="secondary-button nodrag nopan" onClick={() => void setMembership(candidates.map((member) => member.id), card.id)}><Plus size={14} /> {t("Add")} {candidates.length} {t("selected cards")}</button>;
}

export function ContainerMembers({ card }: { card: WorldCard }) {
  useLocale();
  const cards = useWorldStore((state) => state.cards);
  const setMembership = useWorldStore((state) => state.setContainerMembership);
  return <><div className="legion-member-list">{cards.filter((member) => member.parent_id === card.id).map((member) => <div key={member.id}><span title={member.name}>{member.name}</span>
    <button onClick={() => void setMembership([member.id], null)} aria-label={t("Detach {v0}", { v0: String(member.name) })}>{t("Detach")}</button></div>)}</div><AddSelectedMembers card={card} /></>;
}
