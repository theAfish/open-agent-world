import { useWorldStore } from "../state/worldStore";
import { useState } from "react";
import { modelRef } from "../state/modelConnections";

export function ModelSelect({ value, onChange, allowEmpty = false, label = "Model" }: {
  value: string; onChange: (value: string) => void; allowEmpty?: boolean; label?: string;
}) {
  const catalog = useWorldStore(s => s.modelCatalog);
  const [query, setQuery] = useState("");
  const legacy = useWorldStore(s => s.modelSettings.models);
  const known = catalog.connections.some(c => c.enabled && c.models.some(m => m.enabled && modelRef(m.id) === value));
  return <div className="model-select"><input aria-label={`Search ${label.toLowerCase()}`} placeholder="Search models…" value={query} onChange={e => setQuery(e.target.value)} />
    <select aria-label={label} value={value} onChange={e => { onChange(e.target.value); setQuery(""); }}>
    {allowEmpty && <option value="">Use each agent’s model</option>}
    {value && !known && <option value={value}>{value.startsWith("oaw:model:") ? "Unavailable model — choose another" : value}</option>}
    {catalog.connections.filter(c => c.enabled).map(c => <optgroup key={c.id} label={c.name}>
      {c.models.filter(m => m.enabled && (modelRef(m.id) === value || `${c.name} ${m.name} ${m.model_id}`.toLowerCase().includes(query.toLowerCase()))).map(m => <option key={m.id} value={modelRef(m.id)}>{m.name}</option>)}
    </optgroup>)}
    {!catalog.revision && <optgroup label="Previous models">{legacy.filter(m => m !== value && m.toLowerCase().includes(query.toLowerCase())).map(m => <option key={m} value={m}>{m}</option>)}</optgroup>}
  </select></div>;
}
