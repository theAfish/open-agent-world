import { useState, type FormEvent } from 'react';
import { LockKeyhole, LogOut } from 'lucide-react';
import { signOutHosted, type HostedSession } from './hostedSession';
import './hosted.css';

/** No persisted application stores are imported until identity is established. */
export function HostedSignIn({ session }: { session: HostedSession }) {
  const chinese = navigator.language.toLowerCase().startsWith('zh');
  const copy = (en: string, zh: string) => chinese ? zh : en;
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true); setError('');
    try {
      const response = await fetch('/auth/login', {
        method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: username.trim(), password }), signal: AbortSignal.timeout(15_000),
      });
      setPassword('');
      if (!response.ok) {
        setError(response.status === 429
          ? copy('Too many attempts. Wait a moment and try again.', '尝试次数过多，请稍后再试。')
          : copy('Unable to sign in. Check your username and password.', '登录失败，请检查用户名和密码。'));
        return;
      }
      window.location.replace('/');
    } catch {
      setPassword('');
      setError(copy('Cannot connect to the server. Please try again.', '无法连接服务器，请重试。'));
    } finally { setBusy(false); }
  };
  return <main className="hosted-login"><section className="hosted-login-card" aria-labelledby="hosted-title">
    <LockKeyhole size={28} aria-hidden="true" /><h1 id="hosted-title">Open Agent World</h1>
    {session.authenticated ? <>
      <p>{copy('Your account has no workspace assigned. Contact your administrator.', '当前账号尚未分配工作区，请联系管理员。')}</p>
      <button className="secondary-button" onClick={() => { void signOutHosted(); }}><LogOut size={16} />{copy('Sign out', '退出登录')}</button>
    </> : <>
      <p>{copy('Sign in to your workspace', '登录你的工作区')}</p>
      {session.login_methods.includes('password') && <form onSubmit={event => { void submit(event); }}>
        <label>{copy('Username', '用户名')}<input name="username" autoComplete="username" autoCapitalize="none" spellCheck={false} required maxLength={128} value={username} onChange={event => setUsername(event.target.value)} disabled={busy} autoFocus /></label>
        <label>{copy('Password', '密码')}<input name="password" type="password" autoComplete="current-password" required maxLength={1024} value={password} onChange={event => setPassword(event.target.value)} disabled={busy} /></label>
        {error && <p role="alert" className="hosted-login-error">{error}</p>}
        <button className="primary-button" disabled={busy}>{busy ? copy('Signing in…', '正在登录…') : copy('Sign in', '登录')}</button>
      </form>}
      {session.login_methods.includes('oidc') && <a className="secondary-button hosted-oidc-link" href="/auth/oidc/login">{copy('Sign in with your organization', '使用组织账号登录')}</a>}
      {!session.login_methods.length && <p role="alert">{copy('Sign in is not configured. Contact your administrator.', '登录方式尚未配置，请联系管理员。')}</p>}
      <small>{copy('Accounts are provided by your administrator.', '账号由管理员开通。')}</small>
    </>}
  </section></main>;
}
