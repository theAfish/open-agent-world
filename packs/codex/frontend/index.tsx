import { SchemaFields, t, useLocale } from "@oaw/plugin-api";
import { useState } from "react";
import type { FrontendPlugin, PluginViewProps } from "@oaw/plugin-api";
import "./settings.css";

/** Codex owns the order and progressive disclosure of its settings. */
function CodexSettings({ card, definition, host }: PluginViewProps) {
  useLocale();
  const [error, setError] = useState("");
  const properties = (definition.config_schema?.properties ?? {}) as Record<string, Record<string, unknown>>;
  const save = async (key: string, value: unknown) => {
    if (card.config[key] === value) return;
    setError("");
    try { await host.updateConfig({ [key]: value }); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  };
  const fields = (keys: string[], descriptions = true) => <SchemaFields
    schema={{ properties: Object.fromEntries(keys.filter(key => properties[key]).map(key => [key,
      descriptions ? properties[key] : { ...properties[key], description: undefined },
    ])) }} config={card.config} save={save} />;
  const main = ["workspace_path", "model", "reasoning_effort", "session_mode", "codex_sandbox", "system_instruction"];
  const advanced = ["client_source", "codex_command"];
  const extra = Object.keys(properties).filter(key => ![...main, ...advanced].includes(key));
  return <div className="codex-settings">
    <section className="codex-settings-section">
      <label className="field-label codex-project-field">
        <span>{t("Project folder")}</span>
        <input key={String(card.config.workspace_path ?? "")} defaultValue={String(card.config.workspace_path ?? "")}
          placeholder={t("Default Workspace location / codex-workspace")}
          onBlur={(event) => void save("workspace_path", event.target.value.trim())} />
      </label>
      {!String(card.config.workspace_path ?? "").trim() && <small>{t("Uses default workspace")}</small>}
      <p className="codex-settings-help">{t("Leave blank to share codex-workspace under Default Workspace location, created on first run. Or enter an existing absolute project folder.")}</p>
    </section>
    <section className="codex-settings-section">
      <div className="codex-settings-grid">{fields(["model", "reasoning_effort"], false)}</div>
      <p className="codex-settings-help">{t("Default uses your local Codex settings.")}</p>
      <div className="codex-settings-grid">{fields(["session_mode", "codex_sandbox"], false)}</div>
      <p className="codex-settings-help">{t("Continue keeps context per conversation; fresh starts over each run. File access applies to this local project, independently of OAW Sandbox cards.")}</p>
      {fields(["system_instruction"], false)}
    </section>
    <details className="codex-settings-advanced">
      <summary>{t("Advanced settings")}</summary>
      <div className="codex-settings-section">
        {fields(["client_source"])}
        {card.config.client_source === "manual" && fields(["codex_command"])}
        {fields(extra)}
      </div>
    </details>
    {error && <p className="codex-settings-error" role="alert">{error}</p>}
  </div>;
}
export default { apiVersion: 1, views: { settings: CodexSettings } } satisfies FrontendPlugin;
