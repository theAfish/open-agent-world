import { t } from "@oaw/plugin-api";
import { useEffect, useState } from "react";
import { CheckCircle2, CircleAlert, FileText } from "lucide-react";

/** The chain ``knowledge_lineage`` returns: originals, records, insights. */
export type LineageChain = {
  root: { type: "source" | "record" | "insight"; id: string };
  sources: { id: string; missing: boolean; filename?: string; sha256?: string; size?: number;
    content_available?: boolean; uploaded_by?: string | null; uploaded_at?: string | null }[];
  records: { id: string; missing: boolean; name?: string; status?: string;
    sources: { source_id: string; sha256: string | null; intact: boolean }[] }[];
  insights: { id: string; missing: boolean; title?: string; kind?: string; status?: string;
    records: { record_id: string; role: string }[] }[];
};

type Props = {
  root: { source_id: string } | { record_id: string } | { insight_id: string };
  call: (action: string, args?: Record<string, unknown>) => Promise<Record<string, unknown>>;
  onOpenRecord?: (recordId: string) => void;
};

const short = (value: string | null | undefined) => value ? value.slice(0, 12) : "—";

/** Where this record or insight came from, down to the retained bytes — and what
 * rests on it. Each original shows its content hash; a record's link is marked when
 * the hash it was read against no longer matches, or the bytes are gone. */
export function LineagePanel({ root, call, onOpenRecord }: Props) {
  const [chain, setChain] = useState<LineageChain>();
  const [error, setError] = useState("");
  const key = JSON.stringify(root);
  useEffect(() => {
    let live = true;
    setChain(undefined); setError("");
    call("lineage", root).then(result => { if (live) setChain(result as LineageChain); })
      .catch((failure: unknown) => { if (live) setError(failure instanceof Error ? failure.message : String(failure)); });
    return () => { live = false; };
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  if (error) return <p role="alert" className="knowledge-meta">{error}</p>;
  if (!chain) return <p role="status" className="knowledge-meta">{t("Tracing…")}</p>;
  const isRoot = (type: LineageChain["root"]["type"], id: string) => chain.root.type === type && chain.root.id === id;
  return <div className="knowledge-lineage">
    <div>
      <h5>{t("Original files")} <span className="knowledge-total">{chain.sources.length}</span></h5>
      {!chain.sources.length && <p className="knowledge-meta">{t("No original file is linked.")}</p>}
      <ul>
        {chain.sources.map(source => <li key={source.id} data-root={isRoot("source", source.id) || undefined}>
          {source.missing
            ? <><CircleAlert size={13} /><span>{short(source.id)}</span><small>{t("missing")}</small></>
            : <>
              {source.content_available ? <CheckCircle2 size={13} /> : <CircleAlert size={13} />}
              <span><FileText size={12} />{source.filename}</span>
              <code title={source.sha256}>{short(source.sha256)}</code>
              {!source.content_available && <small>{t("bytes unavailable")}</small>}
            </>}
        </li>)}
      </ul>
    </div>
    <div>
      <h5>{t("Records")} <span className="knowledge-total">{chain.records.length}</span></h5>
      {!chain.records.length && <p className="knowledge-meta">{t("No record was read from it.")}</p>}
      <ul>
        {chain.records.map(record => {
          const broken = record.missing || record.sources.some(link => !link.intact);
          return <li key={record.id} data-root={isRoot("record", record.id) || undefined}>
            {broken ? <CircleAlert size={13} /> : <CheckCircle2 size={13} />}
            {record.missing
              ? <><span>{short(record.id)}</span><small>{t("deleted")}</small></>
              : <>
                {onOpenRecord && !isRoot("record", record.id)
                  ? <button type="button" className="knowledge-link" onClick={() => onOpenRecord(record.id)}>{record.name}</button>
                  : <span>{record.name}</span>}
                <small>{record.status}</small>
                <small>{t("{v0} files", { v0: record.sources.length })}</small>
              </>}
          </li>;
        })}
      </ul>
    </div>
    <div>
      <h5>{t("Insights")} <span className="knowledge-total">{chain.insights.length}</span></h5>
      {!chain.insights.length && <p className="knowledge-meta">{t("No insight rests on it.")}</p>}
      <ul>
        {chain.insights.map(insight => <li key={insight.id} data-root={isRoot("insight", insight.id) || undefined}>
          {insight.missing ? <CircleAlert size={13} /> : <CheckCircle2 size={13} />}
          <span>{insight.missing ? short(insight.id) : insight.title}</span>
          {!insight.missing && <small>{insight.kind} · {insight.status}</small>}
        </li>)}
      </ul>
    </div>
  </div>;
}
