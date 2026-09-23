import type { ReadingValue } from "./PdfReading";
export type Extraction = {id:string;source:"grobid"|"agent"|"user";created_at:string;extractor:string;warnings:number;note:string;based_on:string|null;actor_id:string|null;key:string};
export type PaperDoc = { revision:number; value:ReadingValue & {notes:string} };
export type PaperPreview = {revision:number;value:{thumbnail:string;pages:number;filename:string;sha256:string|null;extraction:Extraction|null}};
// The raw PDF is a served file with an ETag; only the small reader document is cached here.
const documents = new Map<string, PaperDoc>();
const pending = new Map<string, Promise<unknown>>();
async function request<T>(path:string):Promise<T> {
  if (pending.has(path)) return pending.get(path) as Promise<T>;
  const result = fetch(`/api/${path}`).then(async response => {
    if (!response.ok) throw new Error(await response.text());
    return response.json() as Promise<T>;
  });
  pending.set(path,result);
  try { return await result; } finally { if(pending.get(path)===result)pending.delete(path); }
}
export const loadPaperPreview = (id:string) => request<PaperPreview>(`library/papers/${id}/preview`);
export const forgetPaper = (id:string) => documents.delete(id);
export const pdfUrl = (id:string, sha256:string|null) => sha256 ? `/api/nodes/${id}/files/raw.pdf?v=${sha256.slice(0,16)}` : "";
/** Merge a reader-document response with the Paper's raw-PDF facts. */
export function withSource(doc:{revision:number;value:Record<string,unknown>}, source:ReadingValue):PaperDoc {
  return {revision:doc.revision,value:{...(doc.value as PaperDoc["value"]),pdfUrl:source.pdfUrl,pages:source.pages,filename:source.filename}};
}
export function rememberPaper(id:string, doc:PaperDoc) {
  if ((documents.get(id)?.revision ?? -1)>doc.revision) return;
  documents.delete(id); documents.set(id,doc);
  while(documents.size>8) documents.delete(documents.keys().next().value!);
}
export async function loadPaper(id:string):Promise<PaperDoc> {
  // Always revalidate the lightweight revision; never reuse stale annotations.
  const preview=await loadPaperPreview(id);
  const cached=documents.get(id);
  const source={pdfUrl:pdfUrl(id,preview.value.sha256),pages:preview.value.pages,filename:preview.value.filename} as ReadingValue;
  if(cached?.revision===preview.revision&&cached.value.pdfUrl===source.pdfUrl) return cached;
  const doc=withSource(await request<{revision:number;value:Record<string,unknown>}>(`nodes/${id}/document`),source);
  rememberPaper(id,doc); return doc;
}
