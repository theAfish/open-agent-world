import { Handle, Position, type NodeProps } from "@xyflow/react";
import {
  Bot,
  ChevronDown,
  ChevronUp,
  FileText,
  Image as ImageIcon,
  Trash2,
  Workflow,
  type LucideIcon,
} from "lucide-react";
import { memo, type ComponentType } from "react";
import { useWorldStore } from "../state/worldStore";
import { CARD_TYPE_LABELS, type CardType, type WorldCard } from "../types/world";
import { AgentCardBody } from "./AgentCard";
import { ImageCardBody } from "./ImageCard";
import { SandboxCardBody } from "./SandboxCard";
import { TextCardBody } from "./TextCard";
import type { CanvasNode } from "./types";

const ICONS: Record<CardType, LucideIcon> = {
  agent: Bot,
  text: FileText,
  image: ImageIcon,
  sandbox: Workflow,
};

const BODIES: Record<CardType, ComponentType<{ card: WorldCard }>> = {
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
  const toggleExpanded = useWorldStore((state) => state.toggleCardExpanded);
  const updateCard = useWorldStore((state) => state.updateCard);
  const deleteCard = useWorldStore((state) => state.deleteCard);
  const Icon = ICONS[card.type];
  const Body = BODIES[card.type];
  const canSource = card.type !== "sandbox";
  const canTarget = card.type !== "agent";

  const handleDelete = () => {
    if (window.confirm(`Remove ${card.name} and all of its relationships?`)) {
      void deleteCard(card.id);
    }
  };

  return (
    <article
      className={`world-card world-card--${card.type} ${card.expanded ? "is-expanded" : ""} ${selected ? "is-selected" : ""} ${card.status === "running" ? "is-running" : ""} ${card.status === "error" ? "is-error" : ""} ${card.ephemeral ? "is-ephemeral" : ""}`}
      aria-label={`${CARD_TYPE_LABELS[card.type]} ${card.name}`}
      data-card-id={card.id}
    >
      {canTarget ? (
        <Handle
          type="target"
          position={Position.Left}
          className="semantic-handle semantic-handle--target"
          aria-label={`Connect a capability into ${card.name}`}
        />
      ) : null}

      <header className="card-header">
        <div className="card-kind-icon" aria-hidden="true">
          <Icon size={17} strokeWidth={1.7} />
        </div>
        <div className="card-title-group">
          <span className="card-eyebrow">{CARD_TYPE_LABELS[card.type]}</span>
          {card.expanded && !card.ephemeral ? (
            <input
              className="card-name-input nodrag nopan"
              defaultValue={card.name}
              aria-label={`${CARD_TYPE_LABELS[card.type]} name`}
              onBlur={(event) => {
                const name = event.currentTarget.value.trim();
                if (name && name !== card.name) void updateCard(card.id, { name });
              }}
              onKeyDown={(event) => {
                if (event.key === "Enter") event.currentTarget.blur();
              }}
            />
          ) : (
            <h2 title={card.name}>{card.name}</h2>
          )}
        </div>
        <div className="card-status" data-status={card.status} title={`Status: ${statusLabel(card.status)}`}>
          <span aria-hidden="true" />
          <span>{statusLabel(card.status)}</span>
        </div>
      </header>

      <div className="card-body">
        <Body card={card} />
      </div>

      <footer className="card-footer">
        <span className="card-id">{card.ephemeral ? "synthetic" : card.id.slice(0, 8)}</span>
        <div className="card-footer-actions nodrag nopan">
          {card.expanded && !card.ephemeral ? (
            <button
              type="button"
              className="icon-button icon-button--danger"
              onClick={handleDelete}
              aria-label={`Remove ${card.name}`}
              title="Remove object"
            >
              <Trash2 size={14} />
            </button>
          ) : null}
          <button
            type="button"
            className="card-expand-button"
            onClick={() => void toggleExpanded(card.id)}
            aria-expanded={card.expanded}
            aria-label={`${card.expanded ? "Collapse" : "Expand"} ${card.name}`}
          >
            {card.expanded ? "Fold" : "Open"}
            {card.expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          </button>
        </div>
      </footer>

      {canSource ? (
        <Handle
          type="source"
          position={Position.Right}
          className="semantic-handle semantic-handle--source"
          aria-label={`Connect a capability from ${card.name}`}
        />
      ) : null}
    </article>
  );
}

export const WorldCardNode = memo(WorldCardNodeComponent);
