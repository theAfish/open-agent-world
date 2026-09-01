import { Handle, Position, type NodeProps } from "@xyflow/react";
import { Bot, ExternalLink, FileText, Image as ImageIcon, Trash2, Workflow, X, type LucideIcon } from "lucide-react";
import { memo, type ComponentType, useEffect, useRef } from "react";
import { NODE_SURFACE_SUPPORT, surfaceLevelForNode, useNodeSurfaceStore, type NodeSurfaceLevel } from "../state/nodeSurfaces";
import { useWorldStore } from "../state/worldStore";
import { CARD_TYPE_LABELS, type CardType, type WorldCard } from "../types/world";
import { AgentCardBody } from "./AgentCard";
import { ImageCardBody } from "./ImageCard";
import { NodePreview } from "./NodePreview";
import { SandboxCardBody } from "./SandboxCard";
import { TextCardBody } from "./TextCard";
import type { CanvasNode } from "./types";

const HOVER_INTENT_MS = 180;
const HOVER_LEAVE_GRACE_MS = 140;

const ICONS: Record<CardType, LucideIcon> = {
  agent: Bot,
  text: FileText,
  image: ImageIcon,
  sandbox: Workflow,
};

interface BodyProps { card: WorldCard; level: NodeSurfaceLevel }

const BODIES: Record<CardType, ComponentType<BodyProps>> = {
  agent: AgentCardBody,
  text: TextCardBody,
  image: ImageCardBody,
  sandbox: SandboxCardBody,
};

function statusLabel(status: WorldCard["status"]): string {
  return status.replaceAll("_", " ");
}

function WorldCardNodeComponent({ data, selected }: NodeProps<CanvasNode>) {
  const card = data.card;
  const activeNodeId = useNodeSurfaceStore((state) => state.activeNodeId);
  const activeLevel = useNodeSurfaceStore((state) => state.level);
  const inspectorNodeIds = useNodeSurfaceStore((state) => state.inspectorNodeIds);
  const showPreview = useNodeSurfaceStore((state) => state.showPreview);
  const hidePreview = useNodeSurfaceStore((state) => state.hidePreview);
  const openInspector = useNodeSurfaceStore((state) => state.openInspector);
  const closeInspector = useNodeSurfaceStore((state) => state.closeInspector);
  const dismissSurface = useNodeSurfaceStore((state) => state.dismiss);
  const openWorkspace = useNodeSurfaceStore((state) => state.openWorkspace);
  const updateCard = useWorldStore((state) => state.updateCard);
  const deleteCard = useWorldStore((state) => state.deleteCard);
  const enterTimer = useRef<ReturnType<typeof setTimeout>>();
  const leaveTimer = useRef<ReturnType<typeof setTimeout>>();
  const level = surfaceLevelForNode(card.id, activeNodeId, activeLevel, inspectorNodeIds);
  const visualLevel = level === "workspace" ? "inspector" : level;
  const Icon = ICONS[card.type];
  const Body = BODIES[card.type];
  const support = NODE_SURFACE_SUPPORT[card.type];

  const clearTimers = () => {
    if (enterTimer.current) clearTimeout(enterTimer.current);
    if (leaveTimer.current) clearTimeout(leaveTimer.current);
  };

  useEffect(() => clearTimers, []);

  const onPointerEnter = () => {
    if (leaveTimer.current) clearTimeout(leaveTimer.current);
    if (!support.preview || activeLevel === "inspector" || activeLevel === "workspace" || level === "preview") return;
    enterTimer.current = setTimeout(() => showPreview(card.id), HOVER_INTENT_MS);
  };

  const onPointerLeave = () => {
    if (enterTimer.current) clearTimeout(enterTimer.current);
    if (level !== "preview") return;
    leaveTimer.current = setTimeout(() => hidePreview(card.id), HOVER_LEAVE_GRACE_MS);
  };

  return (
    <article
      className={`world-card node-surface world-card--${card.type} is-${visualLevel} ${selected ? "is-selected" : ""} ${card.status === "running" ? "is-running" : ""} ${card.status === "error" ? "is-error" : ""} ${card.ephemeral ? "is-ephemeral" : ""}`}
      aria-label={`${CARD_TYPE_LABELS[card.type]} ${card.name}`}
      data-card-id={card.id}
      data-card-type={card.type}
      data-card-expanded={visualLevel === "inspector" ? "true" : "false"}
      data-surface-level={level}
      onPointerEnter={onPointerEnter}
      onPointerLeave={onPointerLeave}
      onClick={(event) => {
        if ((event.target as HTMLElement).closest("button, input, textarea, select, label, .react-flow__handle")) return;
        if (support.inspector && (visualLevel === "node" || visualLevel === "preview")) openInspector(card.id);
      }}
    >
      {!card.ephemeral ? ([
        [Position.Top, "top"], [Position.Right, "right"], [Position.Bottom, "bottom"], [Position.Left, "left"],
      ] as const).map(([position, side]) => (
        <Handle key={side} id={`boundary-${side}`} type="source" position={position}
          className={`semantic-handle semantic-handle--${side}`} data-connection-side={side}
          aria-label={`Start a relationship from the ${side} edge of ${card.name}`} />
      )) : null}

      <header className="card-header node-surface-header">
        <div className="card-kind-icon" aria-hidden="true"><Icon size={18} strokeWidth={1.7} /></div>
        <div className="card-title-group">
          <span className="card-eyebrow">{CARD_TYPE_LABELS[card.type]}</span>
          <h2 title={card.name}>{card.name}</h2>
          <input className="card-name-input nodrag nopan" defaultValue={card.name}
            aria-label={`${CARD_TYPE_LABELS[card.type]} name`}
            onBlur={(event) => {
              const name = event.currentTarget.value.trim();
              if (name && name !== card.name) void updateCard(card.id, { name });
            }}
            onKeyDown={(event) => { if (event.key === "Enter") event.currentTarget.blur(); }} />
        </div>
        <div className="card-status" data-status={card.status} title={`Status: ${statusLabel(card.status)}`}>
          <span aria-hidden="true" /><span>{statusLabel(card.status)}</span>
        </div>
        <button type="button" className="icon-button node-surface-close nodrag nopan"
          onClick={() => closeInspector(card.id)} aria-label={`Close ${card.name} inspector`}><X size={14} /></button>
      </header>

      <div className="node-preview-content" aria-hidden={visualLevel !== "preview"}>
        <NodePreview card={card} />
        <span className="node-preview-hint">Click for details</span>
      </div>

      <div className="card-body node-inspector-content" aria-hidden={visualLevel !== "inspector"}>
        <Body card={card} level={level} />
      </div>

      <footer className="card-footer node-inspector-footer">
        <span className="card-id">{card.ephemeral ? "synthetic" : card.id.slice(0, 8)}</span>
        <div className="card-footer-actions nodrag nopan">
          {!card.ephemeral ? <button type="button" className="icon-button icon-button--danger"
            onClick={() => { dismissSurface(card.id); void deleteCard(card.id); }} aria-label={`Remove ${card.name}`}
            title="Remove object (Ctrl+Z to undo)"><Trash2 size={14} /></button> : null}
          {support.workspace ? (
            <button type="button" className="card-expand-button" onClick={() => openWorkspace(card.id)}>
              Open workspace <ExternalLink size={13} />
            </button>
          ) : null}
        </div>
      </footer>
    </article>
  );
}

export const WorldCardNode = memo(WorldCardNodeComponent);
