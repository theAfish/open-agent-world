import { fetchWithRetry } from '../api/fetchWithRetry';

export interface HostedSession {
  hosted: true;
  authenticated: boolean;
  user: { id: string; username: string; display_name: string; role?: 'user' | 'admin' } | null;
  shared_models?: boolean;
  login_methods: ('password' | 'oidc')[];
  instance: { id: string; application_id: string; name: string; mode: 'builder' | 'runtime' } | null;
}

let session: HostedSession | undefined;
let invalidated = false;
let stopMonitoring: (() => void) | undefined;
let stopRequests: AbortController | undefined;
let restoreFetch: (() => void) | undefined;
let verification: Promise<void> | undefined;
let scheduleVerification: ((delay: number) => void) | undefined;
let failedChecks = 0;
let connectionState: 'online' | 'reconnecting' = 'online';
const connectionListeners = new Set<() => void>();
const logoutFlag = 'oaw-hosted-signout';
export const currentHostedSession = () => session;
export const isHosted = () => session?.hosted === true;
export const hostedSessionActive = () => !invalidated && session?.authenticated === true;
export const hostedConnectionState = () => connectionState;
export function subscribeHostedConnection(listener: () => void) {
  connectionListeners.add(listener);
  return () => { connectionListeners.delete(listener); };
}

function setConnectionState(value: typeof connectionState) {
  if (connectionState === value) return;
  connectionState = value;
  connectionListeners.forEach(listener => listener());
}

function sessionError() { return new DOMException('The workspace session changed. Sign in again.', 'AbortError'); }

/** No user data, tokens or IDs are written to browser storage. */
function broadcastSignOut() {
  try { localStorage.setItem(logoutFlag, `${Date.now()}-${Math.random()}`); } catch { /* Polling is the fallback. */ }
}

function obscurePrivateContent(message = '正在验证登录 / Checking your session…') {
  document.documentElement.classList.add('hosted-session-obscured');
  let shield = document.getElementById('oaw-auth-shield');
  if (!shield) {
    shield = document.createElement('div');
    shield.id = 'oaw-auth-shield';
    shield.setAttribute('role', 'status');
    document.body.append(shield);
  }
  shield.textContent = message;
}

function revealPrivateContent() {
  document.getElementById('oaw-auth-shield')?.remove();
  document.documentElement.classList.remove('hosted-session-obscured');
}

/** Block synchronous cleanup writes before unmounting any private UI. */
export function invalidateHostedSession(navigate = true) {
  if (!isHosted() || invalidated) return;
  invalidated = true;
  obscurePrivateContent('登录已结束，正在返回登录页面 / Returning to sign in…');
  stopRequests?.abort(sessionError());
  stopMonitoring?.();
  window.dispatchEvent(new Event('oaw-hosted-session-ended'));
  document.title = 'Open Agent World';
  if (navigate) window.location.replace('/auth/login');
}

function isPrivateApi(url: URL) {
  return url.origin === window.location.origin && (url.pathname === '/api' || url.pathname.startsWith('/api/'));
}

/** Progress uploads use XHR, so they need the same account pin and revocation as fetch. */
export function guardHostedTransfer(xhr: XMLHttpRequest, value: string) {
  const noop = () => {};
  if (!isHosted() || !isPrivateApi(new URL(value, window.location.href))) {
    return { checkResponse: noop, release: noop };
  }
  if (!hostedSessionActive() || !session?.instance) throw sessionError();
  const instanceId = session.instance.id;
  xhr.setRequestHeader('X-OAW-Instance', instanceId);
  const abort = () => xhr.abort();
  window.addEventListener('oaw-hosted-session-ended', abort, { once: true });
  return {
    release: () => window.removeEventListener('oaw-hosted-session-ended', abort),
    checkResponse: () => {
      if (!hostedSessionActive()) throw sessionError();
      const responseInstance = xhr.getResponseHeader('X-OAW-Instance');
      if (xhr.status === 401 || (responseInstance && responseInstance !== instanceId)) {
        invalidateHostedSession();
        throw sessionError();
      }
    },
  };
}

/** Pin every API request to the account originally used by this page. */
export function installHostedFetch() {
  const original = window.fetch;
  const controller = stopRequests = new AbortController();
  const guarded: typeof fetch = async (input, init) => {
    const url = new URL(input instanceof Request ? input.url : String(input), window.location.href);
    if (!isPrivateApi(url)) return original(input, init);
    if (!hostedSessionActive() || !session?.instance) throw sessionError();
    const instanceId = session.instance.id;
    const headers = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined));
    headers.set('X-OAW-Instance', instanceId);
    const external = init?.signal ?? (input instanceof Request ? input.signal : undefined);
    const requestController = new AbortController();
    const abortSession = () => requestController.abort(sessionError());
    const abortExternal = () => requestController.abort(external?.reason);
    controller.signal.addEventListener('abort', abortSession, { once: true });
    external?.addEventListener('abort', abortExternal, { once: true });
    if (external?.aborted) abortExternal();
    try {
      const response = await original(input, { ...init, headers, signal: requestController.signal, cache: 'no-store' });
      if (invalidated) throw sessionError();
      const responseInstance = response.headers.get('X-OAW-Instance');
      let changed = Boolean(responseInstance && responseInstance !== instanceId);
      if (response.status === 409) {
        const body = await response.clone().json().catch(() => null);
        changed ||= body?.error?.code === 'instance_changed';
      }
      if (response.status === 401 || changed) {
        invalidateHostedSession();
        throw sessionError();
      }
      return response;
    } finally {
      controller.signal.removeEventListener('abort', abortSession);
      external?.removeEventListener('abort', abortExternal);
    }
  };
  window.fetch = guarded;
  return () => { if (window.fetch === guarded) window.fetch = original; controller.abort(); };
}

/** Images, downloads and websocket handshakes cannot carry our fetch header. */
export function hostedInstanceUrl(value: string): string {
  if (!session?.instance) return value;
  const url = new URL(value, window.location.href);
  const sameHostSocket = ['ws:', 'wss:'].includes(url.protocol) && url.host === window.location.host;
  if (!isPrivateApi(url) && !sameHostSocket) return value;
  url.searchParams.set('instance', session.instance.id);
  return value.startsWith('/') ? `${url.pathname}${url.search}${url.hash}` : url.href;
}

export async function initializeHostedSession(): Promise<HostedSession | undefined> {
  stopMonitoring?.();
  restoreFetch?.();
  const response = await fetchWithRetry('/auth/me', { cache: 'no-store', credentials: 'same-origin', signal: AbortSignal.timeout(5000) });
  // Local installations keep their existing startup, including shared-password deployments.
  if (response.status === 404) { session = undefined; return undefined; }
  if (!response.ok) throw new Error('Could not verify your account.');
  const value = await response.json() as HostedSession;
  if (value.hosted !== true || typeof value.authenticated !== 'boolean') throw new Error('Invalid account response.');
  // A startup retry may follow partially imported stores. Never reuse them for another account.
  if (session?.authenticated && (!value.authenticated || value.user?.id !== session.user?.id || value.instance?.id !== session.instance?.id)) {
    invalidateHostedSession();
    throw sessionError();
  }
  session = value;
  invalidated = false;
  failedChecks = 0;
  setConnectionState('online');
  restoreFetch = installHostedFetch();
  if (session.authenticated) monitorHostedSession();
  return session;
}

export function verifyHostedSession(): Promise<void> {
  if (!session?.authenticated || invalidated) return Promise.resolve();
  if (verification) return verification;
  const expected = session;
  verification = (async () => {
    try {
      const response = await fetch('/auth/me', { cache: 'no-store', credentials: 'same-origin', signal: AbortSignal.timeout(5000) });
      if (invalidated || session !== expected) return;
      if (response.status === 401) { invalidateHostedSession(); return; }
      if (!response.ok) throw new Error('Account verification unavailable');
      const current = await response.json() as HostedSession;
      if (invalidated || session !== expected) return;
      // An unavailable or malformed response does not prove that the user signed out.
      if (current?.hosted !== true || typeof current.authenticated !== 'boolean'
          || (current.authenticated && (!current.user?.id || !('instance' in current)))) throw new Error('Invalid account response');
      if (!current.authenticated || current.user?.id !== expected.user?.id || current.instance?.id !== expected.instance?.id) {
        invalidateHostedSession();
        return;
      }
      failedChecks = 0;
      setConnectionState('online');
      if (!document.hidden) revealPrivateContent();
    } catch {
      if (invalidated || session !== expected) return;
      failedChecks += 1;
      setConnectionState('reconnecting');
      // A page restored from history stays shielded until its account is checked.
      // Ordinary tab switches keep the mounted workspace visible.
      if (document.documentElement.classList.contains('hosted-session-obscured')) {
        obscurePrivateContent('暂时无法验证登录，正在重连。工作区已保留。 / Cannot verify your session. Reconnecting; your workspace is still open.');
      }
    }
  })().finally(() => {
    verification = undefined;
    if (invalidated || session !== expected) return;
    const delay = failedChecks ? Math.min(1000 * 2 ** Math.min(failedChecks - 1, 5), 30_000) * (0.8 + Math.random() * 0.4) : 15_000;
    scheduleVerification?.(delay);
  });
  return verification;
}

function monitorHostedSession() {
  stopMonitoring?.();
  let timer: ReturnType<typeof setTimeout>;
  scheduleVerification = (delay) => {
    clearTimeout(timer);
    timer = setTimeout(() => { void verifyHostedSession(); }, delay);
  };
  scheduleVerification(15_000);
  const hide = () => obscurePrivateContent();
  const show = () => { void verifyHostedSession(); };
  const offline = () => setConnectionState('reconnecting');
  const visibility = () => { if (!document.hidden) show(); };
  const storage = (event: StorageEvent) => { if (event.key === logoutFlag && event.newValue) invalidateHostedSession(); };
  window.addEventListener('storage', storage);
  window.addEventListener('online', show);
  window.addEventListener('offline', offline);
  window.addEventListener('pageshow', show);
  window.addEventListener('pagehide', hide);
  document.addEventListener('visibilitychange', visibility);
  stopMonitoring = () => {
    clearTimeout(timer);
    scheduleVerification = undefined;
    window.removeEventListener('storage', storage);
    window.removeEventListener('online', show);
    window.removeEventListener('offline', offline);
    window.removeEventListener('pageshow', show);
    window.removeEventListener('pagehide', hide);
    document.removeEventListener('visibilitychange', visibility);
  };
}

export async function signOutHosted() {
  if (!isHosted()) return;
  invalidateHostedSession(false);
  try {
    const response = await fetch('/auth/logout', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: '{}', signal: AbortSignal.timeout(5000) });
    if (!response.ok) throw new Error('Sign out failed');
    broadcastSignOut();
    window.location.replace('/auth/login');
  } catch {
    // Keep all private views obscured when logout cannot reach the server.
    obscurePrivateContent('无法连接服务器完成退出，请重试。 / Could not complete sign out. Please retry.');
    const retry = document.createElement('button');
    retry.className = 'secondary-button';
    retry.textContent = '重试退出 / Retry sign out';
    retry.onclick = () => { void signOutHosted(); };
    document.getElementById('oaw-auth-shield')?.append(retry);
  }
}
