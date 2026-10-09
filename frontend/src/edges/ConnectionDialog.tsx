import { t, useLocale } from "../i18n";
import { ArrowLeftRight, ArrowRight, Link2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useWorldStore } from "../state/worldStore";
import type { EdgeDirection, Relationship } from "../types/world";
import { apiErrorMessage, worldApi } from '../api/client';
import type { DataSchema } from '../plugins/dataSources';

export function ConnectionDialog() {
  useLocale();
  const pending = useWorldStore((state) => state.pendingConnection);
  const cards = useWorldStore((state) => state.cards);
  const catalog = useWorldStore((state) => state.catalog);
  const close = useWorldStore((state) => state.closeConnectionDialog);
  const create = useWorldStore((state) => state.createConnection);
  const [selected, setSelected] = useState<Relationship | undefined>();
  const [direction, setDirection] = useState<EdgeDirection>("forward");
  const closeButton = useRef<HTMLButtonElement>(null);
  const [schemas, setSchemas] = useState<DataSchema[]>([]);
  const [schemaId, setSchemaId] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [retry, setRetry] = useState(0);
  const source = cards.find((card) => card.id === pending?.source);
  const target = cards.find((card) => card.id === pending?.target);
  const consumer = catalog.node_types.find(item => item.id === source?.type)?.data_consumer;
  const dataRead = !!consumer && catalog.relationships.some(item => item.id === selected && item.data_read);

  useEffect(() => {
    setSelected(pending?.options[0]?.value);
    setDirection("forward");
    if (pending) window.setTimeout(() => closeButton.current?.focus(), 0);
  }, [pending?.source, pending?.target]);

  useEffect(() => {
    let active = true;
    setSchemas([]); setSchemaId(''); setError(''); setLoading(false);
    if (!pending || !selected || !dataRead || !consumer) return;
    setLoading(true);
    worldApi.dataSourceSchemas(pending.source, pending.target, selected).then(result => {
      if (!active) return;
      const options = result.schemas.filter(schema => consumer.kinds.includes(schema.kind));
      setSchemas(options);
      if (source?.config[consumer.source_field] === pending.target && options.some(schema=>schema.id===source.config[consumer.schema_field])) setSchemaId(String(source.config[consumer.schema_field]));
    }).catch(error => {if(active)setError(apiErrorMessage(error));})
      .finally(()=>{if(active)setLoading(false);});
    return () => {active=false;};
  }, [pending?.source, pending?.target, selected, dataRead, retry]);

  useEffect(() => {
    if (!pending) return undefined;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) close();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [close, pending, busy]);

  if (!pending) return null;
  const selectedOption = pending.options.find((option) => option.value === selected);
  const canBeBidirectional = selectedOption?.directions.includes("bidirectional") ?? false;

  return (
    <div
      className="dialog-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy) close();
      }}
    >
      <section
        className="connection-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="connection-dialog-title"
      >
        <header>
          <div className="dialog-icon"><Link2 size={18} /></div>
          <div>
            <span>{t("Semantic relationship")}</span>
            <h2 id="connection-dialog-title">{t(dataRead ? "Connect data source" : "Choose a capability")}</h2>
          </div>
          <button ref={closeButton} disabled={busy} type="button" className="icon-button" onClick={close} aria-label={t("Close capability chooser")}>
            <X size={16} />
          </button>
        </header>

        <div className="connection-route" aria-label={t("{v0} connects to {v1}", { v0: String(source?.name), v1: String(target?.name) })}>
          <div><small>{t(catalog.node_types.find((item) => item.id === source?.type)?.label ?? source?.type ?? "")}</small><strong>{source?.name ?? pending.source}</strong></div>
          {direction === "bidirectional"
            ? <ArrowLeftRight size={18} aria-hidden="true" />
            : <ArrowRight size={18} aria-hidden="true" />}
          <div><small>{t(catalog.node_types.find((item) => item.id === target?.type)?.label ?? target?.type ?? "")}</small><strong>{target?.name ?? pending.target}</strong></div>
        </div>

        <fieldset className="permission-options" disabled={busy}>
          <legend>{t("The backend will grant exactly one permission")}</legend>
          {pending.options.map((option) => (
            <label key={option.value} className={selected === option.value ? "is-selected" : ""}>
              <input
                type="radio"
                name="relationship"
                value={option.value}
                checked={selected === option.value}
                onChange={() => {
                  setSelected(option.value);
                  if (!option.directions.includes("bidirectional")) setDirection("forward");
                }}
              />
              <span className="radio-indicator" aria-hidden="true" />
              <span><strong>{t(option.label)}</strong><small>{t(option.description)}</small></span>
            </label>
          ))}
        </fieldset>

        {dataRead && <div className="connection-schema">
          <label className="field-label"><span>Schema</span>
            <select aria-label="Schema" value={schemaId} disabled={busy || loading || !!error} onChange={event=>setSchemaId(event.target.value)}>
              <option value="">{t(loading ? 'Loading schemas…' : 'Choose a schema…')}</option>
              {schemas.map(schema=><option key={schema.id} value={schema.id}>{schema.label}</option>)}
            </select>
          </label>
          {loading && <p role="status">{t('Loading schemas…')}</p>}
          {!loading && !error && !schemas.length && <p role="status">{t('This source has no compatible schemas.')}</p>}
          {error && <div role="alert">{error} <button type="button" disabled={busy} onClick={()=>setRetry(value=>value+1)}>{t('Retry')}</button></div>}
        </div>}
        {pending.error && <p role="alert">{pending.error}</p>}

        {canBeBidirectional && (
          <fieldset className="permission-options direction-options">
            <legend>{t("Communication direction")}</legend>
            {([
              ["forward", t("One-way"), t("{v0} can message {v1}.", { v0: String(source?.name), v1: String(target?.name) })],
              ["bidirectional", t("Two-way"), t("Both agents can message each other directly.")],
            ] as const).map(([value, label, description]) => (
              <label key={value} className={direction === value ? "is-selected" : ""}>
                <input
                  type="radio"
                  name="direction"
                  value={value}
                  checked={direction === value}
                  onChange={() => setDirection(value)}
                />
                <span className="radio-indicator" aria-hidden="true" />
                <span><strong>{label}</strong><small>{description}</small></span>
              </label>
            ))}
          </fieldset>
        )}

        <footer>
          <button type="button" className="secondary-button" disabled={busy} onClick={close}>{t("Cancel")}</button>
          <button
            type="button"
            className="primary-button"
            disabled={!selected || busy || (dataRead && (loading || !!error || !schemaId))}
            onClick={() => {
              if (!selected || busy) return;
              setBusy(true);
              void create(selected, direction, dataRead ? schemas.find(schema=>schema.id===schemaId) : undefined).finally(()=>setBusy(false));
            }}
          >
            <Link2 size={14} /> {t(dataRead ? 'Connect' : 'Grant capability')} </button>
        </footer>
      </section>
    </div>
  );
}
