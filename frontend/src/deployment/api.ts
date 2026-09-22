export const deploymentApiBase = (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, '') ?? '/api';

export async function deploymentRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${deploymentApiBase}${path}`, {
    cache: 'no-store', ...options,
    headers: { ...(options.body && typeof options.body === 'string' ? { 'Content-Type': 'application/json' } : {}), ...options.headers },
  });
  if (!response.ok) {
    if (response.status === 401 && path.startsWith('/runtime-app')) window.dispatchEvent(new Event('oaw-session-expired'));
    const error = await response.json().catch(() => ({}));
    throw new Error(typeof error.detail === 'string' ? error.detail : error.error?.message ?? `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}
