// Bounded chunks avoid Uint8Array.from's per-character callback and yield the UI.
export async function decodePdf(base64:string,signal:AbortSignal):Promise<Uint8Array<ArrayBuffer>> {
  const started=performance.now();let maxChunk=0;
  const padding=base64.endsWith("==")?2:base64.endsWith("=")?1:0;
  const bytes=new Uint8Array(Math.floor(base64.length*3/4)-padding);
  const chunkSize=256*1024;let offset=0;
  for(let i=0;i<base64.length;i+=chunkSize){
    signal.throwIfAborted();const chunkStarted=performance.now(),decoded=atob(base64.slice(i,i+chunkSize));
    for(let j=0;j<decoded.length;j++)bytes[offset++]=decoded.charCodeAt(j);
    maxChunk=Math.max(maxChunk,performance.now()-chunkStarted);
    if(i+chunkSize<base64.length)await new Promise<void>(resolve=>setTimeout(resolve,0));
  }
  signal.throwIfAborted();performance.clearMeasures("oaw:pdf:decode");performance.measure("oaw:pdf:decode",{start:started,detail:{bytes:bytes.length,maxChunkMs:maxChunk}});return bytes;
}
