import { t, useLocale } from "../i18n";
import { useWorldStore } from "../state/worldStore";
import { useState } from "react";
import { modelRef } from "../state/modelConnections";

export function ModelSelect({ value, onChange, allowEmpty = false, label = t("Model") }: {
  value: string; onChange: (value: string) => void; allowEmpty?: boolean; label?: string;
}) {
  useLocale();
  const catalog = useWorldStore(s => s.modelCatalog);
  const [query, setQuery] = useState("");
  const legacy = useWorldStore(s => s.modelSettings.models);
  const known = catalog.connections.some(c => c.enabled && c.models.some(m => m.enabled && modelRef(m.id) === value));
  return <div className="model-select"><input aria-label={t("Search {v0}", { v0: String(label.toLowerCase()) })} placeholder={t("Search models…")} value={query} onChange={e => setQuery(e.target.value)} />
    <select aria-label={label} value={value} onChange={e => { onChange(e.target.value); setQuery(""); }}>
    {allowEmpty && <option value="">{t("Use each agent’s model")}</option>}
    {value && !known && <option value={value}>{value.startsWith("oaw:model:") ? t("Unavailable model — choose another") : value}</option>}
    {catalog.connections.filter(c => c.enabled).map(c => <optgroup key={c.id} label={c.name}>
      {c.models.filter(m => m.enabled && (modelRef(m.id) === value || `${c.name} ${m.name} ${m.model_id}`.toLowerCase().includes(query.toLowerCase()))).map(m => <option key={m.id} value={modelRef(m.id)}>{m.name}</option>)}
    </optgroup>)}
    {!catalog.revision && <optgroup label={t("Previous models")}>{legacy.filter(m => m !== value && m.toLowerCase().includes(query.toLowerCase())).map(m => <option key={m} value={m}>{m}</option>)}</optgroup>}
  </select></div>;
}
