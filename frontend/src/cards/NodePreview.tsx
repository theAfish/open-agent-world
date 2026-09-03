import { Bot, FileText, Image as ImageIcon, MessagesSquare, ShieldCheck, Workflow } from "lucide-react";
import { useMemo } from "react";
import { useWorldStore } from "../state/worldStore";
import type { WorldCard } from "../types/world";

function compactText(value: unknown, fallback: string): string {
  const text = String(value ?? "").replace(/\s+/g, " ").trim();
  return text || fallback;
}

export function NodePreview({ card }: { card: WorldCard }) {
  const edges = useWorldStore((state) => state.edges);
  const catalog = useWorldStore((state) => state.catalog);
  const connectionCount = useMemo(
    () => edges.filter((edge) => edge.source === card.id || edge.target === card.id).length,
    [card.id, edges],
  );

  if (card.type === "agent") {
    return (
      <div className="node-preview-summary">
        <p>{compactText(card.config.system_instruction, "Ready for a scoped instruction.")}</p>
        <div className="node-preview-metadata">
          <span><Bot size={12} /> {String(card.config.model ?? "Default model")}</span>
          <span>{connectionCount} capabilities</span>
        </div>
      </div>
    );
  }

  if (card.type === "conversation") {
    return (
      <div className="node-preview-summary">
        <p>{compactText(card.config.description, "A shared field for durable conversations.")}</p>
        <div className="node-preview-metadata">
          <span><MessagesSquare size={12} /> Conversation field</span>
          <span>{connectionCount} agents</span>
        </div>
      </div>
    );
  }

  if (card.type === "text") {
    return (
      <div className="node-preview-summary">
        <p>{compactText(card.config.preview ?? card.config.content, "Empty managed text resource.")}</p>
        <div className="node-preview-metadata">
          <span><FileText size={12} /> {String(card.config.filename ?? card.name)}</span>
          <span>r{Number(card.config.revision ?? 0)}</span>
        </div>
      </div>
    );
  }

  if (card.type === "image") {
    return (
      <div className="node-preview-summary node-preview-summary--image">
        {typeof card.config.preview_url === "string"
          ? <img src={card.config.preview_url} alt="" draggable={false} />
          : <span className="node-preview-thumbnail"><ImageIcon size={20} /></span>}
        <div>
          <p>{String(card.config.filename ?? "No image imported")}</p>
          <div className="node-preview-metadata">
            <span>{card.config.image_width && card.config.image_height
              ? `${card.config.image_width} × ${card.config.image_height}`
              : "Dimensions unavailable"}</span>
          </div>
        </div>
      </div>
    );
  }

  if (card.type === "sandbox") return (
    <div className="node-preview-summary">
      <p>{card.status === "stopped" ? "Secure execution environment is stopped." : "Contained execution environment is available."}</p>
      <div className="node-preview-metadata">
        <span><Workflow size={12} /> {connectionCount} connections</span>
        <span><ShieldCheck size={12} /> Network denied</span>
      </div>
    </div>
  );

  const definition = catalog.node_types.find((item) => item.id === card.type);
  return (
    <div className="node-preview-summary">
      <p>{compactText(card.config.summary ?? card.config.description, definition?.description ?? "Plugin-defined world object.")}</p>
      <div className="node-preview-metadata">
        <span>{definition?.label ?? card.type}</span>
        <span>{connectionCount} connections</span>
      </div>
    </div>
  );
}
