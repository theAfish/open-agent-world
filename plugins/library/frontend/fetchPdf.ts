// PDF bytes come from the Paper's files through the host file route; the browser revalidates by ETag.
export async function fetchPdf(url:string,signal:AbortSignal):Promise<Uint8Array<ArrayBuffer>> {
  const started=performance.now();
  const response=await fetch(url,{signal});
  if(!response.ok)throw new Error(await response.text()||`HTTP ${response.status}`);
  const bytes=new Uint8Array(await response.arrayBuffer());
  signal.throwIfAborted();performance.clearMeasures("oaw:pdf:fetch");performance.measure("oaw:pdf:fetch",{start:started,detail:{bytes:bytes.length}});return bytes;
}
