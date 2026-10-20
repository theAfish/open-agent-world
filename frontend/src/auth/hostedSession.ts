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
const logoutFlag = 'oaw-hosted-signout';
export const currentHostedSession = () => session;
export const isHosted = () => session?.hosted === true;
export const hostedSessionActive = () => !invalidated && session?.authenticated === true;

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
  restoreFetch = installHostedFetch();
  if (session.authenticated) monitorHostedSession();
  return session;
}

export async function verifyHostedSession() {
  if (!session?.authenticated || invalidated) return;
  try {
    const response = await fetch('/auth/me', { cache: 'no-store', credentials: 'same-origin', signal: AbortSignal.timeout(5000) });
    if (!response.ok) throw sessionError();
    const current = await response.json() as HostedSession;
    if (!current.hosted || !current.authenticated || current.user?.id !== session.user?.id || current.instance?.id !== session.instance?.id) {
      throw sessionError();
    }
    if (!invalidated && !document.hidden) revealPrivateContent();
  } catch { invalidateHostedSession(); }
}

function monitorHostedSession() {
  stopMonitoring?.();
  const timer = setInterval(() => { void verifyHostedSession(); }, 15_000);
  const hide = () => obscurePrivateContent();
  const show = () => { hide(); void verifyHostedSession(); };
  const visibility = () => { if (document.hidden) hide(); else show(); };
  const storage = (event: StorageEvent) => { if (event.key === logoutFlag && event.newValue) invalidateHostedSession(); };
  window.addEventListener('storage', storage);
  window.addEventListener('pageshow', show);
  window.addEventListener('pagehide', hide);
  document.addEventListener('visibilitychange', visibility);
  stopMonitoring = () => {
    clearInterval(timer);
    window.removeEventListener('storage', storage);
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
