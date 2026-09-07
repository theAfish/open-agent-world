import { SchemaFields } from "@oaw/plugin-api";
import { useEffect, useState } from "react";
import type { FrontendPlugin, PluginViewProps } from "@oaw/plugin-api";
const apiErrorMessage = (error: unknown) => error instanceof Error ? error.message : String(error);
/** Codex owns its settings and local-runtime presentation. */
function CodexSettings({ card, definition, host }: PluginViewProps) {
  const [runtime, setRuntime] = useState<{ session_id: string; details?: Record<string, unknown> }>();
  const [error, setError] = useState("");
  const schema = definition.config_schema;
  useEffect(() => {
    let active = true;
    if (!card.ephemeral) void host.getAgentInfo().then((info) => { if (active) setRuntime(info); })
      .catch((reason) => { if (active) setError(apiErrorMessage(reason)); });
    return () => { active = false; };
  }, [card.id, card.ephemeral, card.config, card.status, host]);
  const save = async (key: string, value: unknown) => {
    if (card.config[key] === value) return;
    setError("");
    try { await host.updateConfig({ [key]: value }); }
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
export default { apiVersion: 1, views: { settings: CodexSettings } } satisfies FrontendPlugin;
