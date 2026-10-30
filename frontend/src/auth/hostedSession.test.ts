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

function xhrResponse(status = 200, instance = 'alice-profile') {
  return {
    status, setRequestHeader: vi.fn(), getResponseHeader: vi.fn(() => instance), abort: vi.fn(),
  } as unknown as XMLHttpRequest;
}

it('pins progress transfers and cancels them when the hosted account ends', async () => {
  const { auth } = await start();
  const xhr = xhrResponse();
  const guard = auth.guardHostedTransfer(xhr, '/api/conversations/chat/sessions/session/attachments');
  expect(xhr.setRequestHeader).toHaveBeenCalledWith('X-OAW-Instance', 'alice-profile');
  auth.invalidateHostedSession(false);
  expect(xhr.abort).toHaveBeenCalledOnce();
  expect(guard.checkResponse).toThrow(expect.objectContaining({ name: 'AbortError' }));
  expect(() => auth.guardHostedTransfer(xhrResponse(), '/api/files')).toThrow();
  guard.release();
});

it('sends the instance pin through the actual binary transfer path while reporting upload progress', async () => {
  await start();
  const headers = new Headers();
  vi.stubGlobal('XMLHttpRequest', class {
    status = 201;
    response = new Blob(['{"uploaded":true}']);
    upload: { onprogress?: (event: unknown) => void } = {};
    onload?: () => void;
    open = vi.fn();
    setRequestHeader(name: string, value: string) { headers.set(name, value); }
    getResponseHeader() { return 'alice-profile'; }
    getAllResponseHeaders() { return 'Content-Type: application/json\r\nX-OAW-Instance: alice-profile'; }
    send(body: Blob) {
      this.upload.onprogress?.({ loaded: body.size, total: body.size, lengthComputable: true });
      this.onload?.();
    }
  });
  const { transferResponse } = await import('../api/transfer');
  const onProgress = vi.fn();
  const response = await transferResponse('/api/runtime-app/workspace/conversations/c/sessions/s/attachments',
    'POST', new Blob(['PDF']), { onProgress });
  expect(response.status).toBe(201);
  expect(headers.get('X-OAW-Instance')).toBe('alice-profile');
  expect(headers.get('Content-Type')).toBe('application/octet-stream');
  expect(onProgress).toHaveBeenCalledWith({ loaded: 3, total: 3 });
});

it.each([401, 200])('rejects a progress response from an expired or changed instance (%s)', async status => {
  const { auth } = await start();
  const guard = auth.guardHostedTransfer(xhrResponse(status, status === 401 ? 'alice-profile' : 'bob-profile'), '/api/files');
  guard.release();
  expect(guard.checkResponse).toThrow();
  expect(auth.hostedSessionActive()).toBe(false);
});

it('releases completed transfers and leaves external transfers unpinned', async () => {
  const { auth } = await start();
  const xhr = xhrResponse(409);
  const guard = auth.guardHostedTransfer(xhr, '/api/files');
  guard.checkResponse(); // An ordinary revision conflict does not end the account session.
  guard.release();
  const external = xhrResponse();
  auth.guardHostedTransfer(external, 'https://external.example/api/files').checkResponse();
  expect(external.setRequestHeader).not.toHaveBeenCalled();
  auth.invalidateHostedSession(false);
  expect(xhr.abort).not.toHaveBeenCalled();
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

it.each([
  () => Promise.reject(new TypeError('Failed to fetch')),
  () => Promise.reject(new DOMException('Timed out', 'TimeoutError')),
  async () => new Response('Unavailable', { status: 503 }),
  async () => json({}),
])('keeps the workspace session on a transient verification failure and recovers automatically (%#)', async fail => {
  vi.spyOn(Math, 'random').mockReturnValue(0.5);
  let unavailable = true;
  const { auth, request } = await start(() => unavailable ? fail() : Promise.resolve(json(account)));
  const ended = vi.fn();
  window.addEventListener('oaw-hosted-session-ended', ended);
  try {
    await auth.verifyHostedSession();
    expect(auth.hostedSessionActive()).toBe(true);
    expect(auth.hostedConnectionState()).toBe('reconnecting');
    expect(ended).not.toHaveBeenCalled();
    expect(document.getElementById('oaw-auth-shield')).toBeNull();
    unavailable = false;
    await vi.advanceTimersByTimeAsync(1000);
    expect(request).toHaveBeenCalledTimes(3);
    expect(auth.hostedConnectionState()).toBe('online');
    await fetch('/api/world');
    expect(new Headers(request.mock.lastCall?.[1]?.headers).get('X-OAW-Instance')).toBe('alice-profile');
    await vi.advanceTimersByTimeAsync(15_000);
    expect(request).toHaveBeenCalledTimes(5);
  } finally { window.removeEventListener('oaw-hosted-session-ended', ended); }
});

it('coalesces checks and backs off repeated failures until the browser comes online', async () => {
  vi.spyOn(Math, 'random').mockReturnValue(0.5);
  let finish!: (response: Response) => void;
  const { auth, request } = await start(() => new Promise(resolve => { finish = resolve; }));
  const pending = auth.verifyHostedSession();
  expect(auth.verifyHostedSession()).toBe(pending);
  window.dispatchEvent(new Event('online'));
  expect(request).toHaveBeenCalledTimes(2);
  finish(new Response('Unavailable', { status: 503 }));
  await pending;
  request.mockImplementation(async () => new Response('Unavailable', { status: 503 }));
  await vi.advanceTimersByTimeAsync(1000);
  expect(request).toHaveBeenCalledTimes(3);
  await vi.advanceTimersByTimeAsync(1999);
  expect(request).toHaveBeenCalledTimes(3);
  await vi.advanceTimersByTimeAsync(1);
  expect(request).toHaveBeenCalledTimes(4);
  request.mockImplementation(async () => json(account));
  window.dispatchEvent(new Event('online'));
  await auth.verifyHostedSession();
  expect(auth.hostedConnectionState()).toBe('online');
  expect(request).toHaveBeenCalledTimes(5);
});

it('keeps ordinary tab switches visible while checking the account in the background', async () => {
  let finish!: (response: Response) => void;
  const { auth } = await start(() => new Promise(resolve => { finish = resolve; }));
  const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(true);
  document.dispatchEvent(new Event('visibilitychange'));
  hidden.mockReturnValue(false);
  document.dispatchEvent(new Event('visibilitychange'));
  expect(document.getElementById('oaw-auth-shield')).toBeNull();
  finish(json(account));
  await auth.verifyHostedSession();
  expect(auth.hostedSessionActive()).toBe(true);
});

it.each([401, 200])('ends the session on confirmed expiry (%s)', async status => {
  const { auth } = await start(async () => json({ ...account, authenticated: false, user: null, instance: null }, { status }));
  await auth.verifyHostedSession();
  expect(auth.hostedSessionActive()).toBe(false);
});

it('does not reveal a history-restored page from a late verification after sign out', async () => {
  let finish!: (response: Response) => void;
  const { auth, request } = await start(() => new Promise(resolve => { finish = resolve; }));
  window.dispatchEvent(new Event('pagehide'));
  const pending = auth.verifyHostedSession();
  auth.invalidateHostedSession(false);
  finish(json(account));
  await pending;
  expect(document.documentElement.classList.contains('hosted-session-obscured')).toBe(true);
  expect(auth.hostedSessionActive()).toBe(false);
  await vi.advanceTimersByTimeAsync(30_000);
  expect(request).toHaveBeenCalledTimes(2);
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
