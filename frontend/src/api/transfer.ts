import type { TransferOptions } from '../files/transferQueue';
import { guardHostedTransfer } from '../auth/hostedSession';

/** Browser-managed binary buffers with byte progress, without JS chunk accumulation. */
export function transferResponse(url: string, method: 'GET' | 'POST', body: Blob | undefined, options: TransferOptions): Promise<Response> {
  return new Promise((resolve, reject) => {
    if (options.signal?.aborted) { reject(new DOMException('Cancelled', 'AbortError')); return; }
    const xhr = new XMLHttpRequest();
    const abort = () => xhr.abort();
    xhr.open(method, url);
    const hosted = guardHostedTransfer(xhr, url);
    const clean = () => { options.signal?.removeEventListener('abort', abort); hosted.release(); };
    xhr.responseType = 'blob';
    xhr.setRequestHeader('X-Request-ID', crypto.randomUUID());
    if (method === 'POST') xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    (method === 'POST' ? xhr.upload : xhr).onprogress = event => options.onProgress?.({ loaded: event.loaded, total: event.lengthComputable ? event.total : body?.size ?? 0 });
    xhr.onload = () => {
      clean();
      try { hosted.checkResponse(); } catch (error) { reject(error); return; }
      const headers = new Headers();
      xhr.getAllResponseHeaders().trim().split(/[\r\n]+/).forEach(line => {
        const colon = line.indexOf(':'); if (colon > 0) headers.append(line.slice(0, colon).trim(), line.slice(colon + 1).trim());
      });
      resolve(new Response(xhr.status === 204 ? null : xhr.response, { status: xhr.status, headers }));
    };
    xhr.onerror = () => { clean(); reject(new Error('The world service is not reachable.')); };
    xhr.onabort = () => { clean(); reject(new DOMException('Cancelled', 'AbortError')); };
    options.signal?.addEventListener('abort', abort, { once: true });
    try { xhr.send(body); } catch (error) { clean(); reject(error); }
  });
}
