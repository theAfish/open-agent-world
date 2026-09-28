import { t, useLocale } from "@oaw/plugin-api";
import { useEffect, useState } from "react";

export type PaperMetadataValue = {
  title?: string; authors?: string[]; year?: string | number; doi?: string;
  source_abstract?: string; abstract_source_url?: string; agent_abstract?: string; source_url?: string;
};

export function safePaperUrl(value?: string) {
  try { const url = new URL(value ?? ""); return ["http:","https:"].includes(url.protocol) && !url.username && !url.password ? url.href : undefined; }
  catch { return undefined; }
}

/** A Paper is useful before its PDF is available; missing full text is not a render error. */
export function PaperMetadata({ title, metadata = {}, onReady, onImport }: {
  title: string; metadata?: PaperMetadataValue; onReady: () => void; onImport: (file: File) => Promise<void>;
}) {
  useLocale();
  const [busy,setBusy] = useState(false), [error,setError] = useState("");
  useEffect(() => { const frame = requestAnimationFrame(onReady); return () => cancelAnimationFrame(frame); }, [onReady]);
  const doi = metadata.doi && /^10\.\d{4,9}\//.test(metadata.doi) ? "https://doi.org/" + encodeURI(metadata.doi).replace(/[?#]/g,encodeURIComponent) : undefined;
  const source = safePaperUrl(metadata.source_url), abstractSource = safePaperUrl(metadata.abstract_source_url);
  return <section className="library-metadata-reading nowheel" aria-label={t("论文题录")}>
    <article>
      <small>{t("已收录题录 · 尚无本地全文")}</small>
      <h1>{metadata.title || title}</h1>
      <p>{metadata.authors?.join(" · ")}{metadata.year ? ` · ${metadata.year}` : ""}</p>
      <div className="library-metadata-links">
        {doi&&<a href={doi} target="_blank" rel="noopener noreferrer">DOI ↗</a>}
        {source&&<a href={source} target="_blank" rel="noopener noreferrer">{t("原文页面")} ↗</a>}
      </div>
      <h2>{t("来源摘要")}</h2>
      <p className="library-metadata-abstract">{metadata.source_abstract || t("暂无来源摘要")}</p>
      {abstractSource&&<a href={abstractSource} target="_blank" rel="noopener noreferrer">{t("摘要来源")} ↗</a>}
      {metadata.agent_abstract&&<><h2>{t("Agent 整理")}</h2><p className="library-metadata-abstract">{metadata.agent_abstract}</p></>}
      <div className="library-metadata-import">
        <p>{t("导入 PDF 后可阅读原文、查看引用与添加批注。")}</p>
        <small>{t("文件会关联当前题录；导入时可选择主文或独立的补充材料 SI。")}</small>
        <label>{busy?t("正在导入…"):t("导入 PDF")}<input disabled={busy} type="file" accept=".pdf,application/pdf" onChange={async event=>{
          const file=event.target.files?.[0];if(!file)return;
          if(file.size>25*1024*1024){setError(t("每个 PDF 最大 25 MiB"));return;}
          setBusy(true);setError("");try{await onImport(file);}catch(error){setError(String(error));}finally{setBusy(false);}
        }}/></label>
        {error&&<p role="alert">{error}</p>}
      </div>
    </article>
  </section>;
}
