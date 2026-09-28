import { useState } from "react";
import { t, useLocale } from "@oaw/plugin-api";
import { safePaperUrl, type PaperMetadataValue } from "./PaperMetadata";

export function paperDoi(value?:string) {
  const doi=(value ?? "").trim().replace(/^https?:\/\/(?:dx\.)?doi\.org\//i, "").replace(/^doi:\s*/i, "");
  return /^10\.\d{4,9}\/\S+$/i.test(doi) ? doi : undefined;
}

/** Bibliographic links remain available independently of local PDF availability. */
export function PaperSourceLinks({metadata={},loading=false}:{metadata?:PaperMetadataValue;loading?:boolean}) {
  useLocale();
  const [message,setMessage]=useState("");
  const doi=paperDoi(metadata.doi);
  const doiUrl=doi ? `https://doi.org/${encodeURI(doi).replace(/[?#]/g,encodeURIComponent)}` : undefined;
  const source=safePaperUrl(metadata.source_url);
  return <section className="library-paper-sources" aria-label={t("论文来源")}>
    <div><span>DOI</span>{doi ? <><a href={doiUrl} target="_blank" rel="noopener noreferrer" title={doi}>{doi}</a>
      <button type="button" aria-label={t("复制 DOI")} title={t("复制 DOI")} onClick={async()=>{try{await navigator.clipboard.writeText(doi);setMessage(t("DOI 已复制"));}catch{setMessage(t("复制失败，请选中 DOI 手动复制"));}}}><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg></button></> : <small>{t(loading?"正在读取…":"未提供 DOI")}</small>}</div>
    <div><span>{t("网址")}</span>{source ? <a href={source} target="_blank" rel="noopener noreferrer" title={source}>{source}</a> : <small>{t(loading?"正在读取…":"未提供原文网址")}</small>}</div>
    {message&&<small role="status">{message}</small>}
  </section>;
}
