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
  const catalog = useWorldStore((state) => state.catalog);
  const update = useWorldStore((state) => state.updateCard);
  const spec = containerDefinition(card, catalog)!;
  const activity = useNodeActivity(card, true);
  const generation = useNodeGeneration(card.id);
  const materializing = generation?.containerId === card.id;
  const connectingNodeId = useNodeSurfaceStore((state) => state.connectingNodeId);
  const frameRef = useRef<HTMLElement>(null);
  const activityLabel = activity.phase === "running" ? `Working · ${activity.running} active`
    : activity.phase === "waiting" ? `Waiting · ${activity.waiting}`
    : ({ completed: "Completed", stopped: "Stopped", failed: "Failed", idle: "Idle" })[activity.phase];
  useEffect(() => {
    if (connectingNodeId === card.id) clearConnectionHoverHint(frameRef.current);
  }, [card.id, connectingNodeId]);
  return <section ref={frameRef} className={`container-frame ${className} ${spec.virtual ? "virtual-workspace" : ""} ${materializing ? "is-materializing" : ""} ${selected ? "is-selected" : ""}`}
    data-card-id={card.id} data-card-type={card.type} data-activity={activity.phase} aria-label={label}
    onPointerMoveCapture={(event) => { if (spec.connectable && connectingNodeId !== card.id) updateConnectionHoverHint(event, frameRef.current); }}
    onPointerLeave={() => clearConnectionHoverHint(frameRef.current)}>
    <ActivityGlow phase={activity.phase} />
    {selected && <NodeResizeControl className="container-resize-arc" position="bottom-right" minWidth={spec.min_size[0]} minHeight={spec.min_size[1]} maxWidth={4096} maxHeight={4096}
      onResizeEnd={(_event, size) => void update(card.id, { size: { width: size.width, height: size.height } })} />}
    {spec.connectable && ([[Position.Top, "top"], [Position.Right, "right"], [Position.Bottom, "bottom"], [Position.Left, "left"]] as const).map(([position, side]) =>
      <Handle key={side} type="source" id={`boundary-${side}`} position={position} className={`semantic-handle semantic-handle--${side}`} data-connection-side={side} aria-label={`Connect ${card.name} ${side}`} />)}
    {spec.connectable && <ConnectionHoverHint />}
    <header className="container-drag-region container-header">{header}</header>
    {(spec.virtual || activity.phase !== "idle") && <span className="container-activity" role="status" data-phase={activity.phase}><i />{activityLabel}</span>}
    {children}
  </section>;
}

export function ContainerActions({ card, busy = false, deleteLabel = `Delete ${card.name} and members` }: { card: WorldCard; busy?: boolean; deleteLabel?: string }) {
  const remove = useWorldStore((state) => state.deleteCards);
  const dissolve = useWorldStore((state) => state.dissolveContainer);
  return <>
    <button className="secondary-button nodrag nopan" disabled={busy} title="Remove the container; keep members and their connections" onClick={() => void dissolve(card.id)}><Ungroup size={14} /> Dissolve</button>
    <button className="secondary-button nodrag nopan" disabled={busy} aria-label={deleteLabel} title="Delete the container and its members. Ctrl+Z to undo." onClick={() => void remove([card.id])}><Trash2 size={14} /></button>
  </>;
}

export function AddSelectedMembers({ card }: { card: WorldCard }) {
  const cards = useWorldStore((state) => state.cards);
  const selected = useWorldStore((state) => state.selectedCardIds);
  const catalog = useWorldStore((state) => state.catalog);
  const setMembership = useWorldStore((state) => state.setContainerMembership);
  const candidates = cards.filter((member) => selected.includes(member.id) && member.parent_id !== card.id && acceptsMember(card, member, catalog, cards));
  return candidates.length > 0 && <button className="secondary-button nodrag nopan" onClick={() => void setMembership(candidates.map((member) => member.id), card.id)}><Plus size={14} /> Add {candidates.length} selected cards</button>;
}

export function ContainerMembers({ card }: { card: WorldCard }) {
  const cards = useWorldStore((state) => state.cards);
  const setMembership = useWorldStore((state) => state.setContainerMembership);
  return <><div className="legion-member-list">{cards.filter((member) => member.parent_id === card.id).map((member) => <div key={member.id}><span title={member.name}>{member.name}</span>
    <button onClick={() => void setMembership([member.id], null)} aria-label={`Detach ${member.name}`}>Detach</button></div>)}</div><AddSelectedMembers card={card} /></>;
}
