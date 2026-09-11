import type { ReadingValue } from "./PdfReading";
export type PaperDoc = { revision:number; value:ReadingValue & {thumbnail:string;notes:string} };
export type PaperPreview = {revision:number;value:{thumbnail:string;pages:number;filename:string}};
const documents = new Map<string, PaperDoc>();
const pending = new Map<string, Promise<unknown>>();
const sizeOf = (doc:PaperDoc) => 2*(doc.value.pdf.length + doc.value.thumbnail.length + doc.value.notes.length
  + (doc.value.annotations??[]).reduce((sum,a)=>sum+(a.image?.length??0)+a.text.length+a.comment.length+a.translation.length,0));
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
export function rememberPaper(id:string, doc:PaperDoc) {
  if ((documents.get(id)?.revision ?? -1)>doc.revision) return;
  documents.delete(id); documents.set(id,doc);
  // Bound retained Base64 payloads (roughly two bytes per JS character).
  let bytes = [...documents.values()].reduce((sum,item)=>sum+sizeOf(item),0);
  while(documents.size>4 || bytes>64*1024*1024) {
    const first=documents.keys().next().value!;
    bytes-=sizeOf(documents.get(first)!); documents.delete(first);
  }
}
export async function loadPaper(id:string):Promise<PaperDoc> {
  // Always revalidate the lightweight revision; never reuse stale annotations.
  const preview=await loadPaperPreview(id);
  const cached=documents.get(id);
  if(cached?.revision===preview.revision) return cached;
  const doc=await request<PaperDoc>(`nodes/${id}/document`);
  rememberPaper(id,doc); return doc;
}
