const RETRY_DELAYS_MS = [250, 750];

function wait(delay: number, signal?: AbortSignal | null): Promise<void> {
  return new Promise((resolve, reject) => {
    signal?.throwIfAborted();
    const abort = () => {
      clearTimeout(timer);
      reject(signal!.reason);
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', abort);
      resolve();
    }, delay);
    signal?.addEventListener('abort', abort, { once: true });
  });
}

/** Retry transient read failures only; mutations may already have taken effect. */
export async function fetchWithRetry(url: string, init?: RequestInit): Promise<Response> {
  if ((init?.method ?? 'GET').toUpperCase() !== 'GET') return fetch(url, init);
  for (let attempt = 0; ; attempt += 1) {
    try {
      init?.signal?.throwIfAborted();
      const response = await fetch(url, init);
      // Vite returns an empty 500 when its upstream socket cannot connect.
      // Preserve application errors and their bodies instead of replaying them.
      const contentType = response.headers.get('content-type') ?? '';
      const proxyFailure = response.status === 500
        && (!contentType || contentType.startsWith('text/plain'))
        && await response.clone().text() === '';
      if (attempt === RETRY_DELAYS_MS.length || !(proxyFailure || [502, 503, 504].includes(response.status))) {
        return response;
      }
      await response.body?.cancel();
    } catch (error) {
      if (init?.signal?.aborted || attempt === RETRY_DELAYS_MS.length || !(error instanceof TypeError)) throw error;
    }
    await wait(RETRY_DELAYS_MS[attempt], init?.signal);
  }
}
