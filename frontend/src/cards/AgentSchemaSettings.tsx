import { t, useLocale } from "../i18n";
import { SchemaFields } from "../plugins/SchemaFields";
import { useEffect, useState } from "react";
import { apiErrorMessage, worldApi } from "../api/client";
import { useWorldStore } from "../state/worldStore";
import type { WorldCard } from "../types/world";
import { CircleCheck, CircleX, FolderOpen, Terminal } from 'lucide-react';
import './agentPreview.css';


/** Plugin-owned scalar configuration, rendered without provider-specific code. */
export function AgentSchemaSettings({ card, active = true }: { card: WorldCard; active?: boolean }) {
  useLocale();
  const catalog = useWorldStore((state) => state.catalog);
  const updateCard = useWorldStore((state) => state.updateCard);
  const [runtime, setRuntime] = useState<{ session_id: string; details?: Record<string, unknown> }>();
  const [error, setError] = useState("");
  const schema = catalog.node_types.find((item) => item.id === card.type)?.config_schema;
  useEffect(() => {
    if (!active) return;
    let current = true;
    if (!card.ephemeral) void worldApi.getAgentInfo(card.id).then((info) => { if (current) setRuntime(info); })
      .catch((reason) => { if (current) setError(apiErrorMessage(reason)); });
    return () => { current = false; };
  }, [card.id, card.ephemeral, card.config, card.status, active]);
  const save = async (key: string, value: unknown) => {
    if (card.config[key] === value) return;
    setError("");
    try { await updateCard(card.id, { config: { [key]: value } }); }
    catch (reason) { setError(apiErrorMessage(reason)); }
  };
  const details = runtime?.details ?? {};
  const available = typeof details.available === 'boolean' ? details.available : undefined;
  const source = typeof details.source === 'string' ? details.source : '';
  const version = typeof details.version === 'string' ? details.version : '';
  const workspace = typeof details.workspace === 'string' ? details.workspace
    : typeof details.workspace_path === 'string' ? details.workspace_path : '';
  const technical = Object.entries(details).filter(([key]) => !['available', 'source', 'version', 'workspace', 'workspace_path'].includes(key));
  return <>
    <section className="card-section" aria-label={t("Local runtime")}>
      <div className="section-heading"><span>{t("Local runtime")}</span></div>
      {runtime ? <>
        <div className="agent-runtime-tags">
          {available !== undefined && <span className="agent-runtime-state" data-available={available}>
            {available ? <CircleCheck size={14} aria-hidden="true" /> : <CircleX size={14} aria-hidden="true" />}{t(available ? 'Ready' : 'Unavailable')}
          </span>}
          {source && <span className="agent-preview-tag"><Terminal size={12} aria-hidden="true" /><span>{source.replaceAll('-', ' ')}</span></span>}
          {version && <span className="agent-preview-tag" title={t('Version')}><span>{version}</span></span>}
        </div>
        {workspace && <div className="agent-preview-project" title={workspace}><FolderOpen size={14} aria-hidden="true" /><span>{workspace}</span></div>}
        {(technical.length > 0 || runtime.session_id) && <details className="agent-runtime-details">
          <summary>{t('Runtime details')}</summary>
          <dl className="plugin-config-list">
            {technical.map(([key, value]) => <div key={key}><dt>{key.replaceAll('_', ' ')}</dt><dd style={{ overflowWrap: 'anywhere' }}>{String(value)}</dd></div>)}
            {runtime.session_id && <div><dt>{t('session')}</dt><dd style={{ overflowWrap: 'anywhere' }}>{runtime.session_id}</dd></div>}
          </dl>
        </details>}
      </> : <p>{t("Discovering local runtime…")}</p>}
      {error && <p role="alert">{error}</p>}
    </section>
    <SchemaFields schema={schema} config={card.config} save={save} />
  </>;
}
