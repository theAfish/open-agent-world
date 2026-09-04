import { Handle, Position, type NodeProps } from "@xyflow/react";
import { Bot, ExternalLink, FileText, Image as ImageIcon, MessagesSquare, Puzzle, Sparkles, Trash2, Workflow, X, type LucideIcon } from "lucide-react";
import { memo, type ComponentType, type CSSProperties, type PointerEvent as ReactPointerEvent, useEffect, useRef } from "react";
import { roundedRectAnchor } from "../edges/geometry";
import { nodeSurfaceSupport, surfaceLevelForNode, useNodeSurfaceStore, type NodeSurfaceLevel } from "../state/nodeSurfaces";
import { useWorldStore } from "../state/worldStore";
import { type CardType, type WorldCard } from "../types/world";
import { AgentCardBody } from "./AgentCard";
import { ConversationCardBody } from "./ConversationCard";
import { ImageCardBody } from "./ImageCard";
import { NodePreview } from "./NodePreview";
import { WorkspaceSurface } from "./NodeWorkspace";
import { SandboxCardBody } from "./SandboxCard";
import { TextCardBody } from "./TextCard";
import { RelationshipList } from "./CardUtilities";
import type { CanvasNode } from "./types";

const HOVER_INTENT_MS = 180;
const HOVER_LEAVE_GRACE_MS = 260;
const PREVIEW_HOVER_MARGIN_PX = 20;
const NON_DRAG_SELECTOR = "button, input, textarea, select, label, a, [contenteditable='true'], .react-flow__handle";

const ICONS: Partial<Record<CardType, LucideIcon>> = {
  agent: Bot,
  conversation: MessagesSquare,
  text: FileText,
  image: ImageIcon,
  sandbox: Workflow,
};

interface BodyProps { card: WorldCard; level: NodeSurfaceLevel }

const BODIES: Partial<Record<CardType, ComponentType<BodyProps>>> = {
  agent: AgentCardBody,
  conversation: ConversationCardBody,
  text: TextCardBody,
  image: ImageCardBody,
  sandbox: SandboxCardBody,
};

const CATALOG_ICONS: Record<string, LucideIcon> = {
  bot: Bot,
  "file-text": FileText,
  image: ImageIcon,
  workflow: Workflow,
  "messages-square": MessagesSquare,
  sparkles: Sparkles,
};

function GenericCardBody({ card }: BodyProps) {
  const entries = Object.entries(card.config).filter(([, value]) => (
    typeof value === "string" || typeof value === "number" || typeof value === "boolean"
  ));
  return (
    <div className="expanded-stack">
      <section className="card-section">
        <div className="section-heading"><span>Plugin configuration</span><small>catalog-driven</small></div>
        {entries.length > 0 ? (
          <dl className="plugin-config-list">
            {entries.map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{String(value)}</dd></div>)}
          </dl>
        ) : <div className="mini-empty"><span>No public configuration fields.</span></div>}
      </section>
      <section className="card-section">
        <div className="section-heading"><span>Relationships</span><small>backend-authoritative</small></div>
        <RelationshipList card={card} />
      </section>
    </div>
  );
}

function statusLabel(status: WorldCard["status"]): string {
  return status.replaceAll("_", " ");
}

function WorldCardNodeComponent({ data, selected }: NodeProps<CanvasNode>) {
  const card = data.card;
  const catalog = useWorldStore((state) => state.catalog);
  const surfaceLevels = useNodeSurfaceStore((state) => state.surfaceLevels);
  const showPreview = useNodeSurfaceStore((state) => state.showPreview);
  const hidePreview = useNodeSurfaceStore((state) => state.hidePreview);
  const openInspector = useNodeSurfaceStore((state) => state.openInspector);
  const closeInspector = useNodeSurfaceStore((state) => state.closeInspector);
  const dismissSurface = useNodeSurfaceStore((state) => state.dismiss);
  const openWorkspace = useNodeSurfaceStore((state) => state.openWorkspace);
  const updateCard = useWorldStore((state) => state.updateCard);
  const deleteCard = useWorldStore((state) => state.deleteCard);
  const connectingNodeId = useNodeSurfaceStore((state) => state.connectingNodeId);
  const cardRef = useRef<HTMLElement>(null);
  const enterTimer = useRef<ReturnType<typeof setTimeout>>();
  const leaveTimer = useRef<ReturnType<typeof setTimeout>>();
  const pressedPointerId = useRef<number>();
  const level = surfaceLevelForNode(card.id, surfaceLevels);
  const visualLevel = level;
  const definition = catalog.node_types.find((item) => item.id === card.type);
  const label = definition?.label ?? card.type;
  const Icon = ICONS[card.type] ?? CATALOG_ICONS[definition?.icon ?? ""] ?? Puzzle;
  const Body = BODIES[card.type] ?? GenericCardBody;
  const support = nodeSurfaceSupport(card.type, catalog);

  const clearEnterTimer = () => {
    if (enterTimer.current) clearTimeout(enterTimer.current);
    enterTimer.current = undefined;
  };

  const clearLeaveTimer = () => {
    if (leaveTimer.current) clearTimeout(leaveTimer.current);
    leaveTimer.current = undefined;
  };

  const clearTimers = () => {
    clearEnterTimer();
    clearLeaveTimer();
  };

  useEffect(() => clearTimers, []);

  useEffect(() => {
    if (connectingNodeId === card.id) cardRef.current?.removeAttribute("data-connection-hot");
  }, [card.id, connectingNodeId]);

  useEffect(() => {
    if (level !== "preview") return;

    const isWithinPreviewHoverArea = (clientX: number, clientY: number) => {
      const bounds = cardRef.current?.getBoundingClientRect();
      if (!bounds) return false;
      return clientX >= bounds.left - PREVIEW_HOVER_MARGIN_PX
        && clientX <= bounds.right + PREVIEW_HOVER_MARGIN_PX
        && clientY >= bounds.top - PREVIEW_HOVER_MARGIN_PX
        && clientY <= bounds.bottom + PREVIEW_HOVER_MARGIN_PX;
    };

    const schedulePreviewHide = () => {
      if (pressedPointerId.current !== undefined || leaveTimer.current) return;
      leaveTimer.current = setTimeout(() => {
        leaveTimer.current = undefined;
        if (pressedPointerId.current === undefined) hidePreview(card.id);
      }, HOVER_LEAVE_GRACE_MS);
    };

    const onWindowPointerMove = (event: PointerEvent) => {
      if (pressedPointerId.current !== undefined || isWithinPreviewHoverArea(event.clientX, event.clientY)) {
        clearLeaveTimer();
        return;
      }
      schedulePreviewHide();
    };

    const onWindowPointerRelease = (event: PointerEvent) => {
      if (pressedPointerId.current !== event.pointerId) return;
      pressedPointerId.current = undefined;
      if (isWithinPreviewHoverArea(event.clientX, event.clientY)) clearLeaveTimer();
      else schedulePreviewHide();
    };

    const onWindowBlur = () => {
      if (pressedPointerId.current === undefined) return;
      pressedPointerId.current = undefined;
      schedulePreviewHide();
    };

    window.addEventListener("pointermove", onWindowPointerMove, true);
    window.addEventListener("pointerup", onWindowPointerRelease, true);
    window.addEventListener("pointercancel", onWindowPointerRelease, true);
    window.addEventListener("blur", onWindowBlur);
    return () => {
      window.removeEventListener("pointermove", onWindowPointerMove, true);
      window.removeEventListener("pointerup", onWindowPointerRelease, true);
      window.removeEventListener("pointercancel", onWindowPointerRelease, true);
      window.removeEventListener("blur", onWindowBlur);
    };
  }, [card.id, hidePreview, level]);

  const onPointerEnter = () => {
    clearLeaveTimer();
    if (pressedPointerId.current !== undefined) return;
    if (!support.preview || level === "inspector" || level === "workspace" || level === "preview") return;
    clearEnterTimer();
    enterTimer.current = setTimeout(() => showPreview(card.id), HOVER_INTENT_MS);
  };

  const onPointerLeave = (event: ReactPointerEvent<HTMLElement>) => {
    cardRef.current?.removeAttribute("data-connection-hot");
    clearEnterTimer();
    if (level !== "preview" || pressedPointerId.current !== undefined) {
      clearLeaveTimer();
      return;
    }
    const bounds = cardRef.current?.getBoundingClientRect();
    if (bounds
      && event.clientX >= bounds.left - PREVIEW_HOVER_MARGIN_PX
      && event.clientX <= bounds.right + PREVIEW_HOVER_MARGIN_PX
      && event.clientY >= bounds.top - PREVIEW_HOVER_MARGIN_PX
      && event.clientY <= bounds.bottom + PREVIEW_HOVER_MARGIN_PX) {
      clearLeaveTimer();
      return;
    }
    clearLeaveTimer();
    leaveTimer.current = setTimeout(() => hidePreview(card.id), HOVER_LEAVE_GRACE_MS);
  };

  const onPointerMove = (event: ReactPointerEvent<HTMLElement>) => {
    if (card.ephemeral || connectingNodeId === card.id) return;
    const element = cardRef.current;
    if (!element) return;
    const bounds = element.getBoundingClientRect();
    const scaleX = element.offsetWidth / Math.max(bounds.width, 1);
    const scaleY = element.offsetHeight / Math.max(bounds.height, 1);
    const pointer = {
      x: (event.clientX - bounds.left) * scaleX,
      y: (event.clientY - bounds.top) * scaleY,
    };
    const cornerRadius = Number.parseFloat(window.getComputedStyle(element).borderTopLeftRadius) || 0;
    const anchor = roundedRectAnchor(
      { x: 0, y: 0, width: element.offsetWidth, height: element.offsetHeight },
      pointer,
      cornerRadius,
    );
    if (Math.hypot(pointer.x - anchor.x, pointer.y - anchor.y) > 20 * Math.max(scaleX, scaleY)) {
      element.removeAttribute("data-connection-hot");
      return;
    }
    element.style.setProperty("--connection-hint-x", `${anchor.x}px`);
    element.style.setProperty("--connection-hint-y", `${anchor.y}px`);
    element.dataset.connectionHot = "true";
  };

  const onPointerDownCapture = (event: ReactPointerEvent<HTMLElement>) => {
    const target = event.target as HTMLElement;
    const isNonDraggableTarget = Boolean(target.closest(NON_DRAG_SELECTOR));
    clearEnterTimer();
    if (!isNonDraggableTarget && level === "preview") {
      clearLeaveTimer();
      pressedPointerId.current = event.pointerId;
    }
    if (isNonDraggableTarget) {
      event.stopPropagation();
    }
  };

  return (
    <article
      ref={cardRef}
      className={`world-card node-surface world-card--${card.type} is-${visualLevel} ${selected ? "is-selected" : ""} ${card.status === "running" ? "is-running" : ""} ${card.status === "error" ? "is-error" : ""} ${card.ephemeral ? "is-ephemeral" : ""}`}
      style={{ "--card-kind": definition?.color } as CSSProperties}
      aria-label={`${label} ${card.name}`}
      data-card-id={card.id}
      data-card-type={card.type}
      data-card-expanded={visualLevel === "inspector" || visualLevel === "workspace" ? "true" : "false"}
      data-surface-level={level}
      onPointerEnter={onPointerEnter}
      onPointerLeave={onPointerLeave}
      onPointerMove={onPointerMove}
      onPointerDownCapture={onPointerDownCapture}
      onClick={(event) => {
        if ((event.target as HTMLElement).closest("button, input, textarea, select, label, .react-flow__handle, .node-preview-hover-buffer")) return;
        if (support.inspector && (visualLevel === "node" || visualLevel === "preview")) openInspector(card.id);
      }}
    >
      {visualLevel === "preview" ? (
        <div className="node-preview-hover-buffer" data-preview-hover-buffer aria-hidden="true" />
      ) : null}
      {!card.ephemeral ? (
        <svg className="connection-hover-hint" data-connection-hover-hint viewBox="0 0 12 12" aria-hidden="true">
          <circle className="semantic-edge-endpoint" cx="6" cy="6" r="4.5" />
        </svg>
      ) : null}
      {!card.ephemeral ? ([
        [Position.Top, "top"], [Position.Right, "right"], [Position.Bottom, "bottom"], [Position.Left, "left"],
      ] as const).map(([position, side]) => (
        <Handle key={side} id={`boundary-${side}`} type="source" position={position}
          className={`semantic-handle semantic-handle--${side}`} data-connection-side={side}
          aria-label={`Start a relationship from the ${side} edge of ${card.name}`} />
      )) : null}

      {visualLevel === "workspace" ? <WorkspaceSurface card={card} /> : <>
        <header className="card-header node-surface-header">
          <div className="card-kind-icon" aria-hidden="true"><Icon size={18} strokeWidth={1.7} /></div>
          <div className="card-title-group">
            <span className="card-eyebrow">{label}</span>
            <h2 title={card.name}>{card.name}</h2>
            <input className="card-name-input nodrag nopan" defaultValue={card.name}
              aria-label={`${label} name`}
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
      </>}
    </article>
  );
}

export const WorldCardNode = memo(WorldCardNodeComponent);
