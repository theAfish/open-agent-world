import { isMissingCard, MissingPlugin } from "./MissingPlugin";
import { t, useLocale } from "../i18n";
import { Bot, FileText, Image as ImageIcon, MessagesSquare, ShieldCheck } from "lucide-react";
import { TaskBoardPreview } from "./TaskBoard";
import { SkillToolboxPreview } from "./SkillToolbox";
import { useWorldStore } from "../state/worldStore";
import type { WorldCard } from "../types/world";
import { PluginSurface } from "../plugins/PluginSurface";
import { modelLabel } from "./modelLabel";
import { MINISTER_ROLE_CARD } from '../state/ministerRole';
import { MinisterRoleCardPreview } from './MinisterRoleCard';

function compactText(value: unknown, fallback: string): string {
  const text = String(value ?? "").replace(/\s+/g, " ").trim();
  return text || fallback;
}

export function NodePreview({ card }: { card: WorldCard }) {
  useLocale();
  const catalog = useWorldStore(state => state.catalog);
  if (isMissingCard(card, catalog)) return <div data-material-layer="top-print"><MissingPlugin card={card} compact /></div>;
  const preview = <PluginSurface card={card} slot="preview" level="preview"><DefaultNodePreview card={card} /></PluginSurface>;
  // Unknown plugin previews are information widgets by default. Authored card
  // artwork uses its own surface contract (e.g. factory cards in CardFrame).
  return catalog.node_types.find(definition => definition.id === card.type)?.frontend?.preview
    ? <div data-material-layer="top-print">{preview}</div> : preview;
}

function DefaultNodePreview({ card }: { card: WorldCard }) {
  useLocale();
  const sandbox = useWorldStore(s => s.sandboxInfo[card.id]);
  const sandboxError = useWorldStore(s => s.sandboxErrors[card.id]);
  const catalog = useWorldStore((state) => state.catalog);
  const modelCatalog = useWorldStore((state) => state.modelCatalog);
  if (card.type === MINISTER_ROLE_CARD) return <div data-material-layer="top-print"><MinisterRoleCardPreview /></div>;


  if (catalog.node_types.find((definition) => definition.id === card.type)?.traits.includes("ui.skill-package.v1")) return <div data-material-layer="top-print"><SkillToolboxPreview card={card} /></div>;
  if (catalog.node_types.find((definition) => definition.id === card.type)?.traits.includes("ui.task-board.v1")) return <div data-material-layer="top-print"><TaskBoardPreview card={card} /></div>;

  if (catalog.node_types.find((definition) => definition.id === card.type)?.traits.includes("ui.skill.v1")) return <div data-material-layer="top-print"><SkillToolboxPreview card={card} single /></div>;


  if (catalog.node_types.find((definition) => definition.id === card.type)?.traits.includes("core.agent")) {
    const modelName = modelLabel(modelCatalog, card.config.model);
    return (
      <div className="node-preview-summary">
        <p data-material-layer="top-print">{compactText(card.config.system_instruction, t("Ready for a scoped instruction."))}</p>
        <div className="node-preview-metadata" data-material-layer="top-print">
          <span title={modelName}><Bot size={12} /> {modelName}</span>
        </div>
      </div>
    );
  }

  if (card.type === "conversation") {
    return (
      <div className="node-preview-summary">
        <p data-material-layer="top-print">{compactText(card.config.description, t("A shared field for durable conversations."))}</p>
        <div className="node-preview-metadata" data-material-layer="top-print">
          <span><MessagesSquare size={12} /> {t("Conversation field")}</span>
        </div>
      </div>
    );
  }

  if (card.type === "core.artifact-collection") return <div className="node-preview-summary"><p data-material-layer="top-print">{t("Retained file versions")}</p><small data-material-layer="top-print">{t("Open workspace to inspect, copy, or release published content.")}</small></div>;

  if (card.type === "text") {
    return (
      <div className="node-preview-summary">
        <p data-material-layer="top-print">{compactText(card.config.preview ?? card.config.content, t("Empty managed text resource."))}</p>
        <div className="node-preview-metadata" data-material-layer="top-print">
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
          : <span className="node-preview-thumbnail" data-material-layer="top-print"><ImageIcon size={20} /></span>}
        <div>
          <p data-material-layer="top-print">{String(card.config.filename ?? t("No image imported"))}</p>
          <div className="node-preview-metadata" data-material-layer="top-print">
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
      <p data-material-layer="top-print">{String(sandbox?.runtime_id ?? card.config.runtime ?? "auto")} · {(sandbox?.network_enabled ?? card.config.network_enabled) ? t("Network enabled") : t("Network disabled")}</p>
      <p data-material-layer="top-print">{String(sandboxError || sandbox?.unavailable_reason || card.config.active_command || card.config.last_error || t("Idle"))}</p>
      <div className="node-preview-metadata" data-material-layer="top-print">
        <span><ShieldCheck size={12} /> {card.config.workspace_access === "read_only" ? t("Read only") : t("Read & write")} · {card.status}</span>
      </div>
    </div>
  );

  const definition = catalog.node_types.find((item) => item.id === card.type);
  return (
    <div className="node-preview-summary">
      <p data-material-layer="top-print">{compactText(card.config.summary ?? card.config.description, definition?.description ?? t("Plugin-defined world object."))}</p>
      <div className="node-preview-metadata" data-material-layer="top-print">
        <span>{definition?.label ?? card.type}</span>
      </div>
    </div>
  );
}
