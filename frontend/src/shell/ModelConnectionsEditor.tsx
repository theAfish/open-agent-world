import { t, useLocale } from "../i18n";
import { Plus, Server, Star, Trash2 } from "lucide-react";
import { useState } from "react";
import { availableModels, modelRef, type ModelCatalog, type ModelConnection } from "../state/modelConnections";

const presets = {
  openai: { name: "OpenAI", base_url: "https://api.openai.com/v1", auth_mode: "api_key" as const },
  anthropic: { name: "Anthropic", base_url: "https://api.anthropic.com", auth_mode: "api_key" as const },
  gemini: { name: "Google Gemini", base_url: "", auth_mode: "api_key" as const },
  compatible: { name: "Custom connection", base_url: "", auth_mode: "api_key" as const },
  local: { name: "Local models", base_url: "http://localhost:11434/v1", auth_mode: "none" as const },
};

function defaultEnvironmentVariable(adapter: ModelConnection["adapter"]): string {
  return adapter === "anthropic" ? "ANTHROPIC_API_KEY" : adapter === "gemini" ? "GEMINI_API_KEY" : "OPENAI_API_KEY";
}

export function ModelConnectionsEditor({ value, onChange, saved, busy }: {
  value: ModelCatalog; onChange: (value: ModelCatalog) => void; saved: ModelCatalog; busy: boolean;
}) {
  useLocale();
  const [selected, setSelected] = useState(value.connections[0]?.id ?? "");
  const [preset, setPreset] = useState<keyof typeof presets>("compatible");
  const [query, setQuery] = useState("");
  const [advancedConnectionIds, setAdvancedConnectionIds] = useState<Set<string>>(new Set());
  const connection = value.connections.find(c => c.id === selected) ?? value.connections[0];
  const update = (patch: Partial<ModelConnection>) => {
    if (!connection) return;
    const next = { ...value, connections: value.connections.map(c => c.id === connection.id ? { ...c, ...patch } : c) };
    if (!availableModels(next).some(m => m.value === next.default_model)) next.default_model = null;
    onChange(next);
  };
  const add = () => {
    const id = crypto.randomUUID();
    const p = presets[preset];
    const added: ModelConnection = { id, name: p.name, base_url: p.base_url,
      adapter: preset === "compatible" || preset === "local" ? "openai" : preset,
      auth_mode: p.auth_mode, enabled: true, api_key_configured: false, models: [] };
    onChange({ ...value, connections: [...value.connections, added] }); setSelected(id); setQuery("");
  };
  return <fieldset className="model-editor" disabled={busy}>
    <div className="settings-page-heading"><h3>{t("Models & connections")}</h3><p>{t("Connect your accounts, then choose the models your agents can use.")}</p></div>
    <label className="field-label"><span>{t("Default for new agents")}</span>
      <select value={value.default_model ?? ""} onChange={e => onChange({ ...value, default_model: e.target.value || null })}>
        <option value="">{t("Use agent template default")}</option>
        {availableModels(value).map(m => <option key={m.value} value={m.value}>{m.connection} / {m.label}</option>)}
      </select>
    </label>
    <div className="connection-add">
      <select aria-label={t("New connection type")} value={preset} onChange={e => setPreset(e.target.value as keyof typeof presets)}>
        <option value="compatible">{t("OpenAI-compatible service")}</option><option value="openai">{t("OpenAI")}</option>
        <option value="anthropic">{t("Anthropic")}</option><option value="gemini">{t("Google Gemini")}</option><option value="local">{t("Local service")}</option>
      </select>
      <button type="button" className="secondary-button" onClick={add} disabled={value.connections.length >= 100}><Plus size={14} /> {t("Add connection")}</button>
    </div>
    <div className="connections-layout">
      <div className="connection-list" aria-label={t("Connections")}>
        <input aria-label={t("Search connections")} placeholder={t("Search connections…")} value={query} onChange={e => setQuery(e.target.value)} />
        {value.connections.filter(c => c.name.toLowerCase().includes(query.toLowerCase())).map(c => <button type="button" key={c.id}
          className="connection-item" aria-pressed={connection?.id === c.id} onClick={() => setSelected(c.id)}>
          <Server size={14} /><span><strong>{c.name || t("Untitled connection")}</strong><small>{c.enabled ? t("{v0} models", { v0: String(c.models.filter(m => m.enabled).length) }) : t("Disabled")}</small></span>
        </button>)}
        {!value.connections.length && <p className="settings-description">{t("Add a connection to get started.")}</p>}
      </div>
      {connection ? <div className="connection-detail" key={connection.id}>
        <div className="connection-detail-title"><h4>{connection.name || t("New connection")}</h4>
          <label className="settings-check"><input type="checkbox" checked={connection.enabled} onChange={e => update({ enabled: e.target.checked })} />{t("Enabled")}</label>
          {!saved.connections.some(c => c.id === connection.id) && <button type="button" className="icon-button" aria-label={t("Remove unsaved connection")} onClick={() => {
            const next = { ...value, connections: value.connections.filter(c => c.id !== connection.id) };
            if (!availableModels(next).some(m => m.value === next.default_model)) next.default_model = null;
            onChange(next);
          }}><Trash2 size={14} /></button>}
        </div>
        <div className="connection-fields">
        <label className="field-label"><span>{t("Connection name")}</span><input value={connection.name} maxLength={120} required onChange={e => update({ name: e.target.value })} placeholder={t("Company account")} /></label>
        <label className="field-label"><span>{t("API format")}</span><select disabled={connection.id === "legacy"} value={connection.adapter} onChange={e => update({ adapter: e.target.value as ModelConnection["adapter"] })}>
          <option value="openai">{t("OpenAI-compatible Chat Completions")}</option><option value="anthropic">{t("Anthropic Messages")}</option><option value="gemini">{t("Google Gemini")}</option>
          {connection.adapter === "legacy" && <option value="legacy">{t("Previous automatic routing")}</option>}
        </select></label>
        <label className="field-label"><span>{t("Base URL")}</span><input type="url" value={connection.base_url} onChange={e => update({ base_url: e.target.value })} placeholder={t("Use provider default")} spellCheck={false} />
          <small>{t("Use the service’s API address, including /v1 if required. Leave blank for the provider default.")}</small></label>
        <div className="field-label connection-key-field">
          <label htmlFor="connection-key">{t("API key")}</label><input id="connection-key" type="password" value={connection.api_key ?? ""}
            autoComplete="new-password" spellCheck={false} data-1p-ignore
            placeholder={connection.api_key_configured && !connection.clear_api_key ? t("Saved securely — leave blank to keep") : t("Enter API key")}
            onChange={e => {
              const api_key = e.target.value;
              update({ api_key, ...(api_key.trim() ? { auth_mode: "api_key", clear_api_key: false } : {}) });
            }} />
          <small>{connection.clear_api_key ? t("The saved key will be removed when you save. Enter a new key to replace it instead.")
            : connection.auth_mode === "none" ? t("Optional for this connection. Leave blank to use no key, or enter a key to use it.")
            : connection.auth_mode === "environment" ? t("Using backend credentials. Enter a key here to use it instead.")
            : t("Keys are encrypted on the backend. Their values are never returned to the browser.")}</small>
          {connection.api_key_configured && <button type="button" className="secondary-button" onClick={() => update({ clear_api_key: !connection.clear_api_key, api_key: "" })}>{connection.clear_api_key ? t("Keep saved key") : t("Remove saved key")}</button>}
        </div>
        </div>
        <div className="connection-advanced">
          <button type="button" className="text-button" aria-expanded={advancedConnectionIds.has(connection.id)} onClick={() => setAdvancedConnectionIds(current => {
            const next = new Set(current); if (next.has(connection.id)) next.delete(connection.id); else next.add(connection.id); return next;
          })}>{t("Advanced connection options")}</button>
          {advancedConnectionIds.has(connection.id) && <div className="field-label"><label htmlFor="connection-auth-source">{t("Authentication source")}</label><select id="connection-auth-source" value={connection.auth_mode} onChange={e => {
            const auth_mode = e.target.value as ModelConnection["auth_mode"];
            update({ auth_mode, api_key: "" });
          }}>
            <option value="api_key">{t("API key entered here")}</option><option value="none">{t("No API key")}</option><option value="environment">{t("Backend environment")}</option>
          </select>
            {connection.auth_mode === "environment" && <label className="field-label"><span>{t("Environment variable name")}</span><input aria-label={t("Backend environment variable")} aria-describedby="connection-environment-help" value={connection.environment_variable ?? ""} maxLength={128} spellCheck={false}
              placeholder={defaultEnvironmentVariable(connection.adapter)} onChange={e => update({ environment_variable: e.target.value || null })} />
              <small id="connection-environment-help">{t("Enter a variable name, not an API key. For managed deployments, set its value on the server before starting OAW. Leave blank to use")} {defaultEnvironmentVariable(connection.adapter)}.</small>
            </label>}
          </div>}
        </div>
        <div className="connection-model-heading"><h4>{t("Models")}</h4><button type="button" className="secondary-button" disabled={connection.models.length >= 100} onClick={() => update({ models: [...connection.models, { id: crypto.randomUUID(), name: "", model_id: "", enabled: true }] })}><Plus size={13} /> {t("Add model")}</button></div>
        {!connection.models.length && <p className="settings-description">{t("Add a model using the model ID supplied by your service.")}</p>}
        {connection.models.map((model, index) => <div className="connection-model-row" key={model.id}>
          <label className="field-label"><span>{t("Display name")}{value.default_model === modelRef(model.id) && <Star className="model-default-icon" size={12} role="img" aria-label={t("Default for new agents")}><title>{t("Default for new agents")}</title></Star>}</span><input aria-label={t("Model {v0} display name", { v0: String(index + 1) })} required maxLength={120} value={model.name}
            placeholder={t("Code assistant")} onChange={e => update({ models: connection.models.map(m => m.id === model.id ? { ...m, name: e.target.value } : m) })} /></label>
          <label className="field-label"><span>{t("Model ID")}</span><input aria-label={t("Model {v0} ID", { v0: String(index + 1) })} required maxLength={200} value={model.model_id}
            placeholder={t("Service model ID")} spellCheck={false} onChange={e => update({ models: connection.models.map(m => m.id === model.id ? { ...m, model_id: e.target.value } : m) })} /></label>
          <div className="connection-model-actions"><label className="settings-check"><input type="checkbox" aria-label={t("Enable model {v0}", { v0: String(index + 1) })} checked={model.enabled}
            onChange={e => update({ models: connection.models.map(m => m.id === model.id ? { ...m, enabled: e.target.checked } : m) })} />{t("On")}</label>
          {!saved.connections.some(c => c.models.some(m => m.id === model.id)) && <button type="button" className="icon-button" aria-label={t("Remove model {v0}", { v0: String(index + 1) })} onClick={() => update({ models: connection.models.filter(m => m.id !== model.id) })}><Trash2 size={13} /></button>}
          </div>
        </div>)}
        <p className="settings-description">{t("Changes apply to new runs. Disable saved models or connections to preserve existing agent references.")}</p>
      </div> : <div className="connection-empty"><Server size={32} /><h4>{t("Your models, your accounts")}</h4><p>{t("Add multiple accounts from the same provider, or connect your own service.")}</p></div>}
    </div>
  </fieldset>;
}
