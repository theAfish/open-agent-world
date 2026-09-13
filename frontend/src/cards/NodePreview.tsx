import { t, useLocale } from "../i18n";
import { Bot, FileText, Image as ImageIcon, MessagesSquare, ShieldCheck, Workflow } from "lucide-react";
import { TaskBoardPreview } from "./TaskBoard";
import { SkillToolboxPreview } from "./SkillToolbox";
import { useMemo } from "react";
import { useWorldStore } from "../state/worldStore";
import type { WorldCard } from "../types/world";
import { PluginSurface } from "../plugins/PluginSurface";
import { modelRef } from "../state/modelConnections";

function compactText(value: unknown, fallback: string): string {
  const text = String(value ?? "").replace(/\s+/g, " ").trim();
  return text || fallback;
}

export function NodePreview({ card }: { card: WorldCard }) {
  useLocale();
  return <PluginSurface card={card} slot="preview" level="preview"><DefaultNodePreview card={card} /></PluginSurface>;
}

function DefaultNodePreview({ card }: { card: WorldCard }) {
  useLocale();
  const sandbox = useWorldStore(s => s.sandboxInfo[card.id]);
  const sandboxError = useWorldStore(s => s.sandboxErrors[card.id]);
  const edges = useWorldStore((state) => state.edges);
  const catalog = useWorldStore((state) => state.catalog);
  const modelCatalog = useWorldStore((state) => state.modelCatalog);
  const connectionCount = useMemo(
    () => edges.filter((edge) => edge.source === card.id || edge.target === card.id).length,
    [card.id, edges],
  );

  if (catalog.node_types.find((definition) => definition.id === card.type)?.traits.includes("ui.skill-package.v1")) return <SkillToolboxPreview card={card} />;
  if (catalog.node_types.find((definition) => definition.id === card.type)?.traits.includes("ui.task-board.v1")) return <TaskBoardPreview card={card} />;

  if (catalog.node_types.find((definition) => definition.id === card.type)?.traits.includes("ui.skill.v1")) return <SkillToolboxPreview card={card} single />;


  if (catalog.node_types.find((definition) => definition.id === card.type)?.traits.includes("core.agent")) {
    const reference = String(card.config.model ?? "");
    const configuredModel = modelCatalog.connections.flatMap(connection => connection.models)
      .find(model => modelRef(model.id) === reference);
    const modelName = configuredModel?.name || (reference.startsWith("oaw:model:")
      ? t("Unavailable model") : reference || t("Default model"));
    return (
      <div className="node-preview-summary">
        <p>{compactText(card.config.system_instruction, t("Ready for a scoped instruction."))}</p>
        <div className="node-preview-metadata">
          <span title={modelName}><Bot size={12} /> {modelName}</span>
          <span>{connectionCount} {t("world connections")}</span>
        </div>
      </div>
    );
  }

  if (card.type === "conversation") {
    return (
      <div className="node-preview-summary">
        <p>{compactText(card.config.description, t("A shared field for durable conversations."))}</p>
        <div className="node-preview-metadata">
          <span><MessagesSquare size={12} /> {t("Conversation field")}</span>
          <span>{connectionCount} {t("agents")}</span>
        </div>
      </div>
    );
  }

  if (card.type === "core.artifact-collection") return <div className="node-preview-summary"><p>{t("Retained file versions")}</p><small>{t("Open workspace to inspect, copy, or release published content.")}</small></div>;

  if (card.type === "text") {
    return (
      <div className="node-preview-summary">
        <p>{compactText(card.config.preview ?? card.config.content, t("Empty managed text resource."))}</p>
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
          <p>{String(card.config.filename ?? t("No image imported"))}</p>
          <div className="node-preview-metadata">
            <span>{card.config.image_width && card.config.image_height
              ? `${card.config.image_width} × ${card.config.image_height}`
              : t("Dimensions unavailable")}</span>
          </div>
        </div>
      </div>
    );
  }

  if (card.type === "sandbox") return (
    <div className="node-preview-summary">
      <p>{String(sandbox?.runtime_id ?? card.config.runtime ?? "auto")} · {(sandbox?.network_enabled ?? card.config.network_enabled) ? t("Network enabled") : t("Network disabled")}</p>
      <p>{String(sandboxError || sandbox?.unavailable_reason || card.config.active_command || card.config.last_error || t("Idle"))}</p>
      <div className="node-preview-metadata">
        <span><Workflow size={12} /> {connectionCount} {t("connections")}</span>
        <span><ShieldCheck size={12} /> {card.config.workspace_access === "read_only" ? t("Read only") : t("Read & write")} · {card.status}</span>
      </div>
    </div>
  );

  const definition = catalog.node_types.find((item) => item.id === card.type);
  return (
    <div className="node-preview-summary">
      <p>{compactText(card.config.summary ?? card.config.description, definition?.description ?? t("Plugin-defined world object."))}</p>
      <div className="node-preview-metadata">
        <span>{definition?.label ?? card.type}</span>
        <span>{connectionCount} {t("connections")}</span>
      </div>
    </div>
  );
}
