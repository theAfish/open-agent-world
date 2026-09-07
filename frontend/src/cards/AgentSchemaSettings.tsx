import { SchemaFields } from "../plugins/SchemaFields";
import { useEffect, useState } from "react";
import { apiErrorMessage, worldApi } from "../api/client";
import { useWorldStore } from "../state/worldStore";
import type { WorldCard } from "../types/world";


/** Plugin-owned scalar configuration, rendered without provider-specific code. */
export function AgentSchemaSettings({ card }: { card: WorldCard }) {
  const catalog = useWorldStore((state) => state.catalog);
  const updateCard = useWorldStore((state) => state.updateCard);
  const [runtime, setRuntime] = useState<{ session_id: string; details?: Record<string, unknown> }>();
  const [error, setError] = useState("");
  const schema = catalog.node_types.find((item) => item.id === card.type)?.config_schema;
  useEffect(() => {
    let active = true;
    if (!card.ephemeral) void worldApi.getAgentInfo(card.id).then((info) => { if (active) setRuntime(info); })
      .catch((reason) => { if (active) setError(apiErrorMessage(reason)); });
    return () => { active = false; };
  }, [card.id, card.ephemeral, card.config, card.status]);
  const save = async (key: string, value: unknown) => {
    if (card.config[key] === value) return;
    setError("");
    try { await updateCard(card.id, { config: { [key]: value } }); }
    catch (reason) { setError(apiErrorMessage(reason)); }
  };
  return <>
    <section className="card-section" aria-label="Local runtime">
      <div className="section-heading"><span>Local runtime</span><small>{String(card.config.runtime_provider_id ?? "")}</small></div>
      {runtime ? <dl className="plugin-config-list">
        {Object.entries(runtime.details ?? {}).map(([key, value]) => <div key={key}><dt>{key.replaceAll("_", " ")}</dt><dd style={{ overflowWrap: "anywhere" }}>{String(value)}</dd></div>)}
        <div><dt>session</dt><dd style={{ overflowWrap: "anywhere" }}>{runtime.session_id}</dd></div>
      </dl> : <p>Discovering local runtime…</p>}
      {error && <p role="alert">{error}</p>}
    </section>
    <SchemaFields schema={schema} config={card.config} save={save} />
  </>;
}
