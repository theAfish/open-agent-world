import { invalidateHostedSession } from './hostedSession';

export async function adminRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch('/hosting/admin/' + path, { credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.timeout(30_000), ...init });
  if (response.status === 401) invalidateHostedSession();
  const value = await response.json();
  if (!response.ok) throw new Error(value.error?.message ?? 'Could not load administration settings.');
  return value;
}
