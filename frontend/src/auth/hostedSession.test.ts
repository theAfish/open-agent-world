// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { HostedSession } from './hostedSession';

const account: HostedSession = {
  hosted: true, authenticated: true, user: { id: 'alice', username: 'alice', display_name: 'Alice' },
  login_methods: ['password', 'oidc'], instance: { id: 'alice-profile', application_id: 'app', name: 'Workspace', mode: 'builder' },
};
const json = (value: unknown, init?: ResponseInit) => new Response(JSON.stringify(value), init);

beforeEach(() => { vi.resetModules(); vi.useFakeTimers(); vi.spyOn(console, 'error').mockImplementation(() => {}); });
afterEach(async () => {
  const auth = await import('./hostedSession');
  auth.invalidateHostedSession(false);
  document.documentElement.classList.remove('hosted-session-obscured');
  document.getElementById('oaw-auth-shield')?.remove();
  vi.clearAllTimers(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.clear();
});

async function start(next: (...args: Parameters<typeof fetch>) => Promise<Response> = async () => json({})) {
  const request = vi.fn<typeof fetch>().mockResolvedValueOnce(json(account)).mockImplementation(next);
  vi.stubGlobal('fetch', request);
  const auth = await import('./hostedSession');
  await auth.initializeHostedSession();
  return { auth, request };
}

it('leaves local fetch and browser persistence unchanged when authentication is absent', async () => {
  const request = vi.fn().mockResolvedValue(new Response('', { status: 404 }));
  vi.stubGlobal('fetch', request);
  const auth = await import('./hostedSession');
  expect(await auth.initializeHostedSession()).toBeUndefined();
  expect(fetch).toBe(request);
  expect(auth.hostedInstanceUrl('/api/resources/x/content')).toBe('/api/resources/x/content');
});

it('fails closed when the authentication endpoint is broken instead of treating it as local', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('failed', { status: 500 })));
  const auth = await import('./hostedSession');
  await expect(auth.initializeHostedSession()).rejects.toThrow('Could not verify');
});

it('pins reads and mutations to the original instance and preserves existing headers', async () => {
  const { request } = await start();
  await fetch('/api/nodes/copied-id', { method: 'PATCH', headers: { 'Content-Type': 'application/json', 'X-Test': 'yes', 'X-OAW-Instance': 'forged' }, body: '{}' });
  await fetch('/api/world');
  const mutation = request.mock.calls[1][1]!;
  expect(new Headers(mutation.headers).get('X-OAW-Instance')).toBe('alice-profile');
  expect(new Headers(mutation.headers).get('X-Test')).toBe('yes');
  expect(mutation.cache).toBe('no-store');
  expect(new Headers(request.mock.calls[2][1]?.headers).get('X-OAW-Instance')).toBe('alice-profile');
});

it('does not attach private instance identifiers to external requests or static assets', async () => {
  const { request } = await start();
  const options = { method: 'POST', body: '{}' };
  await fetch('https://external.example/api/message', options);
  await fetch('/assets/example.svg');
  expect(request.mock.calls[1]).toEqual(['https://external.example/api/message', options]);
  expect(request.mock.calls[2]).toEqual(['/assets/example.svg', undefined]);
});

it('pins download URLs and websocket connections while leaving external URLs alone', async () => {
  const { auth } = await start();
  expect(auth.hostedInstanceUrl('/api/files?path=a%20b#preview')).toBe('/api/files?path=a+b&instance=alice-profile#preview');
  expect(auth.hostedInstanceUrl(`ws://${window.location.host}/ws/events`)).toContain('/ws/events?instance=alice-profile');
  expect(auth.hostedInstanceUrl('https://external.example/api/files')).toBe('https://external.example/api/files');
});

it('clears private rendering and blocks subsequent writes on an instance mismatch', async () => {
  const { auth, request } = await start(async () => json({ error: { code: 'instance_changed' } }, { status: 409 }));
  const ended = vi.fn();
  window.addEventListener('oaw-hosted-session-ended', ended, { once: true });
  await expect(fetch('/api/nodes/copied-id', { method: 'DELETE' })).rejects.toMatchObject({ name: 'AbortError' });
  expect(auth.hostedSessionActive()).toBe(false);
  expect(document.documentElement.classList.contains('hosted-session-obscured')).toBe(true);
  expect(ended).toHaveBeenCalledOnce();
  await expect(fetch('/api/world')).rejects.toMatchObject({ name: 'AbortError' });
  expect(request).toHaveBeenCalledTimes(2);
});

it('preserves ordinary revision conflicts without ending the session', async () => {
  const { auth } = await start(async () => json({ detail: 'revision conflict' }, { status: 409 }));
  expect((await fetch('/api/nodes/x', { method: 'PATCH' })).status).toBe(409);
  expect(auth.hostedSessionActive()).toBe(true);
});

it('requires a fresh page if a startup retry finds a different account', async () => {
  const { auth } = await start(async () => json({ ...account, user: { ...account.user!, id: 'bob' } }));
  await expect(auth.initializeHostedSession()).rejects.toMatchObject({ name: 'AbortError' });
  expect(auth.hostedSessionActive()).toBe(false);
  expect(auth.currentHostedSession()?.user?.id).toBe('alice');
});

it('aborts pending requests and never delivers a late private response after logout', async () => {
  let resolve!: (response: Response) => void;
  const { auth, request } = await start(() => new Promise(done => { resolve = done; }));
  const pending = fetch('/api/world');
  const rejected = expect(pending).rejects.toMatchObject({ name: 'AbortError' });
  auth.invalidateHostedSession(false);
  expect(request.mock.calls[1][1]?.signal?.aborted).toBe(true);
  resolve(json({ private: 'old account' }));
  await rejected;
});

it('detects cookie changes on account verification before revealing a resumed page', async () => {
  const { auth } = await start(async () => json({ ...account, user: { ...account.user!, id: 'bob' } }));
  window.dispatchEvent(new Event('pagehide'));
  expect(document.documentElement.classList.contains('hosted-session-obscured')).toBe(true);
  await auth.verifyHostedSession();
  expect(auth.hostedSessionActive()).toBe(false);
});

it('does not read, import or write browser preferences for hosted accounts', async () => {
  localStorage.setItem('oaw-theme', 'private-local-theme');
  const { auth, request } = await start(async () => json({ mode: 'production', profile_id: 'alice-profile', generation: 'g1', version: '1', values: {} }));
  const storage = await import('../state/profileStorage');
  expect(storage.profileStorage.getItem('oaw-theme')).toBeNull();
  storage.profileStorage.setItem('oaw-theme', 'dark');
  expect(localStorage.getItem('oaw-theme')).toBe('private-local-theme');
  await storage.initializeProfile();
  expect(storage.profileStorage.getItem('oaw-theme')).toBeNull();
  expect(request).toHaveBeenCalledTimes(2);
  storage.profileStorage.setItem('oaw-theme', 'light');
  auth.invalidateHostedSession(false);
  window.dispatchEvent(new Event('pagehide'));
  await vi.advanceTimersByTimeAsync(3000);
  expect(request).toHaveBeenCalledTimes(2);
  expect(storage.currentProfile()).toBeUndefined();
});

it('does not retry failed preference writes after the account has been invalidated', async () => {
  let reject!: (reason: Error) => void;
  const { auth, request } = await start(() => new Promise((_resolve, fail) => { reject = fail; }));
  const storage = await import('../state/profileStorage');
  storage.configureProfile({ mode: 'production', profile_id: 'alice-profile', generation: 'g1', version: '1', values: {} });
  storage.profileStorage.setItem('theme', 'dark');
  const pending = storage.flushPreferences();
  const rejected = expect(pending).rejects.toThrow();
  auth.invalidateHostedSession(false);
  reject(new Error('closed'));
  await rejected;
  await vi.advanceTimersByTimeAsync(5000);
  expect(request).toHaveBeenCalledTimes(2);
});
